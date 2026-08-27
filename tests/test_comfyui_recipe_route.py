"""Route-level tests for the recipe consent controls (R3, CWE-829).

The graph a recipe replays is **file metadata**: whoever made the image authored
it, and PixlStash's premise is importing images from elsewhere. Replaying it
executes it on the owner's ComfyUI, bounded only by which node packs are
installed. Three controls make that a decision rather than an accident, and all
three are asserted here at the HTTP boundary, because a control that only exists
in the dialog is not a control:

1. ``GET /comfyui/pictures/{id}/recipe`` discloses the distinct ``class_type``
   list, so the owner can see what they are approving.
2. ``POST /comfyui/run_recipe`` **refuses** when the pre-flight could not run
   (ComfyUI unreachable ⇒ nothing about the graph was inspected) unless the
   caller sends an explicit ``allow_unchecked`` acknowledgement.
3. The read endpoint reports whether the source file came from *outside* this
   instance, which is what turns "an embedded workflow" into "someone else's
   embedded workflow".

Both directions throughout: the refusal must fire without the flag AND the run
must still succeed with it, since over-blocking is its own regression.
"""

import gc
import io
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import pixlstash.routes.comfyui as comfyui_module
from pixlstash.db_models import Picture
from pixlstash.server import Server
from tests.authz_guard import no_spa_fallback  # noqa: F401
from tests.utils import upload_pictures_and_wait

API = "/api/v1"

pytestmark = pytest.mark.usefixtures("no_spa_fallback")

# A minimal but realistic API-format graph: a loader, a sampler with a seed, two
# text encoders and a writer. Deliberately declared out of alphabetical order so
# the sorted disclosure is actually proven to sort.
RECIPE_GRAPH = {
    "9": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "ComfyUI", "images": ["3", 0]},
    },
    "3": {
        "class_type": "KSampler",
        "inputs": {"seed": 12345, "steps": 20, "model": ["4", 0]},
    },
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    },
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry"}},
    # Not a node: the sanitizer drops it, so it must not reach the class list.
    "pixlstash_output_nodes": ["9"],
}

OBJECT_INFO = {
    "SaveImage": {"input": {"required": {"filename_prefix": ["STRING", {}]}}},
    # ``control_after_generate`` is what marks the input as a seed; without it
    # the recipe has nothing to re-roll and is honestly reported unavailable.
    "KSampler": {
        "input": {
            "required": {
                "seed": ["INT", {"default": 0, "control_after_generate": True}]
            }
        }
    },
    "CheckpointLoaderSimple": {
        "input": {"required": {"ckpt_name": [["sd_xl_base_1.0.safetensors"], {}]}}
    },
    "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}]}}},
}

EXPECTED_CLASSES = [
    "CheckpointLoaderSimple",
    "CLIPTextEncode",
    "KSampler",
    "SaveImage",
]


def _recipe_png_bytes(graph: dict, colour: tuple[int, int, int]) -> bytes:
    """A PNG carrying *graph* in its ComfyUI ``prompt`` text chunk."""
    img = Image.new("RGB", (256, 256), colour)
    meta = PngInfo()
    meta.add_text("prompt", json.dumps(graph))
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()


@pytest.fixture
def env():
    """A live server with one imported picture that carries a recipe."""
    temp_dir = tempfile.TemporaryDirectory()
    config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(config_path, "w") as fh:
        fh.write(json.dumps({"port": 8000}))
    server = Server(config_path)
    server.__enter__()
    try:
        client = TestClient(server.api, raise_server_exceptions=True)
        r = client.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
        )
        assert r.status_code == 200, r.text

        files = [
            (
                "file",
                (
                    "recipe.png",
                    _recipe_png_bytes(RECIPE_GRAPH, (120, 90, 200)),
                    "image/png",
                ),
            )
        ]
        st = upload_pictures_and_wait(client, files, timeout_s=60)
        assert st["status"] == "completed", st

        r = client.get(f"{API}/pictures")
        assert r.status_code == 200, r.text
        picture_ids = [p["id"] for p in r.json()]
        assert picture_ids, "The recipe picture did not import"

        yield server, client, picture_ids[0]
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


def _comfyui_reachable(monkeypatch):
    monkeypatch.setattr(
        comfyui_module, "fetch_object_info", lambda url: dict(OBJECT_INFO)
    )


def _comfyui_unreachable(monkeypatch):
    def boom(url):
        raise RuntimeError(f"Could not reach ComfyUI at {url}")

    monkeypatch.setattr(comfyui_module, "fetch_object_info", boom)


def _capture_submissions(monkeypatch) -> list[dict]:
    """Stub the ComfyUI submit + output import; return the captured graphs."""
    submitted: list[dict] = []

    def fake_submit(base_url, graph, client_id):
        submitted.append(graph)
        return {"prompt_id": "test-prompt-1"}

    monkeypatch.setattr(comfyui_module, "_submit_comfyui_prompt", fake_submit)
    monkeypatch.setattr(
        comfyui_module, "_process_comfyui_outputs", lambda *a, **kw: None
    )
    return submitted


def _clear_origin_fields(server, pic_id: int) -> None:
    """Make *pic_id* look like this instance generated it.

    The import path stamps ``original_file_name``; PixlStash's own ComfyUI
    import does not. Clearing it is the cheapest faithful way to exercise the
    not-imported branch without standing up a generation run.
    """

    def update(session):
        pic = session.get(Picture, pic_id)
        pic.original_file_name = None
        pic.import_source_folder = None
        pic.reference_folder_id = None
        session.add(pic)
        session.commit()

    server.vault.db.run_task(update)


def _set_watch_folder_origin(server, pic_id: int, folder: str) -> None:
    """Make *pic_id* look like a watch-folder import."""

    def update(session):
        pic = session.get(Picture, pic_id)
        pic.original_file_name = None
        pic.import_source_folder = folder
        session.add(pic)
        session.commit()

    server.vault.db.run_task(update)


class TestRecipeDisclosesNodeClasses:
    def test_lists_the_distinct_class_types_sorted(self, env, monkeypatch):
        server, client, pic_id = env
        _comfyui_reachable(monkeypatch)
        r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is True, body
        # Distinct (two CLIPTextEncode nodes collapse to one entry), sorted, and
        # free of the non-node bookkeeping key the sanitizer drops.
        assert body["node_classes"] == EXPECTED_CLASSES
        assert body["node_count"] == 5

    def test_the_class_list_survives_an_unreachable_comfyui(self, env, monkeypatch):
        """The disclosure is read from the file, not from ComfyUI.

        This is the case that matters most: the pre-flight is the thing that
        goes missing when ComfyUI is down, and that is exactly when the owner
        has nothing else to judge the graph by.
        """
        server, client, pic_id = env
        _comfyui_unreachable(monkeypatch)
        r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["preflight"]["checked"] is False
        assert body["node_classes"] == EXPECTED_CLASSES


class TestRecipeReportsSourceOrigin:
    def test_an_uploaded_picture_is_reported_as_imported(self, env, monkeypatch):
        server, client, pic_id = env
        _comfyui_reachable(monkeypatch)
        r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source_is_imported"] is True
        assert body["source_label"] == "Imported file"

    def test_a_locally_generated_picture_is_not(self, env, monkeypatch):
        server, client, pic_id = env
        _comfyui_reachable(monkeypatch)
        _clear_origin_fields(server, pic_id)
        r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source_is_imported"] is False
        assert body["source_label"] is None

    def test_a_watched_folder_names_the_route_in_not_the_path(self, env, monkeypatch):
        server, client, pic_id = env
        _comfyui_reachable(monkeypatch)
        _set_watch_folder_origin(server, pic_id, "/home/someone/private/incoming")
        r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source_is_imported"] is True
        assert body["source_label"] == "Watched folder"
        # The owner's filesystem layout is not the dialog's business.
        assert "/home/someone" not in json.dumps(body)


class TestRunRecipeRefusesPixlStashNodes:
    """A ComfyUI-PixlStash graph is a cycle, and its ids are frozen.

    The loaders serialise a choice as ``"<name> #<id>"``, so replaying the file
    re-applies whatever project / set / character / picture id was current when
    it was written - ids that may name a deleted project, or one that now lives
    in a different library. Before this refusal that surfaced as a raw SQLite
    FOREIGN KEY error from the saver's own import, *after* the images had
    already been imported.
    """

    @staticmethod
    def _pixlstash_env(env, monkeypatch):
        """Re-answer the recipe read with a graph that carries a pack node."""
        import pixlstash.utils.comfyui_utilities as utils

        graph = dict(RECIPE_GRAPH)
        graph["11"] = {
            "class_type": "PixlStashPictureSaver",
            "inputs": {"filename_prefix": "v", "pixlstash_project": ["12", 0]},
        }
        graph["12"] = {
            "class_type": "PixlStashProjectLoader",
            "inputs": {"pixlstash_project": "Gone #6"},
        }
        monkeypatch.setattr(utils, "find_comfy_api_prompt", lambda *a, **kw: graph)
        monkeypatch.setattr(
            comfyui_module, "find_comfy_api_prompt", lambda *a, **kw: graph
        )

    def test_run_is_refused_and_nothing_is_submitted(self, env, monkeypatch):
        server, client, pic_id = env
        self._pixlstash_env(env, monkeypatch)
        submitted = _capture_submissions(monkeypatch)
        r = client.post(f"{API}/comfyui/run_recipe", json={"picture_id": pic_id})
        assert r.status_code == 400, r.text
        assert "PixlStash nodes" in r.json()["detail"]
        assert submitted == []

    def test_the_recipe_read_says_so_before_the_user_commits(self, env, monkeypatch):
        # The dialog has to be able to offer "copy it into ComfyUI" instead,
        # which it cannot do if the refusal only arrives on submit.
        server, client, pic_id = env
        self._pixlstash_env(env, monkeypatch)
        r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is False
        assert body["reason"] == "pixlstash_nodes"


class TestRunRecipeRefusesAnUncheckedPreflight:
    def test_refuses_without_the_override(self, env, monkeypatch):
        server, client, pic_id = env
        _comfyui_unreachable(monkeypatch)
        submitted = _capture_submissions(monkeypatch)
        r = client.post(f"{API}/comfyui/run_recipe", json={"picture_id": pic_id})
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert "could not reach ComfyUI" in detail
        assert "has not been inspected" in detail
        # The refusal is a refusal: nothing reached ComfyUI.
        assert submitted == []

    def test_a_false_override_is_not_an_override(self, env, monkeypatch):
        server, client, pic_id = env
        _comfyui_unreachable(monkeypatch)
        submitted = _capture_submissions(monkeypatch)
        r = client.post(
            f"{API}/comfyui/run_recipe",
            json={"picture_id": pic_id, "allow_unchecked": False},
        )
        assert r.status_code == 400, r.text
        assert submitted == []

    def test_only_the_literal_true_is_consent(self, env, monkeypatch):
        """R3b: consent is the JSON boolean ``true`` and nothing else. The
        string ``"false"`` is truthy in Python, and ``"true"``/``1``/``[true]``
        are the sibling spellings a lenient cast would also let through."""
        server, client, pic_id = env
        _comfyui_unreachable(monkeypatch)
        submitted = _capture_submissions(monkeypatch)
        for value in ("false", "true", 1, "yes", [True], {"v": True}):
            for key in ("allow_unchecked", "allowUnchecked"):
                r = client.post(
                    f"{API}/comfyui/run_recipe",
                    json={"picture_id": pic_id, key: value},
                )
                assert r.status_code == 400, (
                    f"{key}={value!r} must not read as consent: {r.text}"
                )
        assert submitted == []

    def test_the_explicit_override_runs_it(self, env, monkeypatch):
        server, client, pic_id = env
        _comfyui_unreachable(monkeypatch)
        submitted = _capture_submissions(monkeypatch)
        r = client.post(
            f"{API}/comfyui/run_recipe",
            json={"picture_id": pic_id, "allow_unchecked": True},
        )
        assert r.status_code == 200, r.text
        assert r.json()["prompts"][0]["prompt_id"] == "test-prompt-1"
        assert len(submitted) == 1
        # What ran is what was disclosed: the same class set, and none of the
        # non-node bookkeeping keys.
        assert sorted({node["class_type"] for node in submitted[0].values()}) == sorted(
            EXPECTED_CLASSES
        )
        assert "pixlstash_output_nodes" not in submitted[0]

    def test_the_camel_case_spelling_is_accepted_too(self, env, monkeypatch):
        server, client, pic_id = env
        _comfyui_unreachable(monkeypatch)
        _capture_submissions(monkeypatch)
        r = client.post(
            f"{API}/comfyui/run_recipe",
            json={"picture_id": pic_id, "allowUnchecked": True},
        )
        assert r.status_code == 200, r.text

    def test_a_reachable_comfyui_needs_no_override(self, env, monkeypatch):
        """The gate is on *unchecked*, not on recipes in general.

        A pre-flight that actually ran and passed is the normal path and must
        stay a single click; requiring the acknowledgement here would train the
        user to tick it without reading, which is the failure mode the control
        exists to avoid.
        """
        server, client, pic_id = env
        _comfyui_reachable(monkeypatch)
        submitted = _capture_submissions(monkeypatch)
        r = client.post(f"{API}/comfyui/run_recipe", json={"picture_id": pic_id})
        assert r.status_code == 200, r.text
        assert len(submitted) == 1
