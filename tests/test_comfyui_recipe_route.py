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

The B5 classes at the foot of the file cover the same endpoint's *extended*
read - the negative prompt, the sampler settings, the LoRA strengths, the
workflow card, and the A1111 branch - plus the picture-list filters that
resolve a card to the variants that made its pictures.
"""

import gc
import io
import json
import os
import sqlite3
import tempfile
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import pixlstash.routes.comfyui as comfyui_module
from pixlstash.db_models import Picture
from pixlstash.hub import workflow_cards
from pixlstash.hub.workflow_cards import CORE_RULE_VERSION
from pixlstash.server import Server
from pixlstash.services.workflow_identity import WORKFLOW_KEY_VERSION
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


class TestSwappingALoRAIntoAReplay:
    """#1310: "Generate variants" puts a shelf LoRA into the picture's own graph.

    The replay is where the LoRA a picture was made with is visible, so it is
    where swapping one has to work. The same rules as the saved-workflow run:
    one slot, resolved to a name this ComfyUI lists, and a graph with no LoRA
    loader refused rather than run without it.
    """

    SHA = "cd" * 32
    FILENAME = "example-subject-v2.safetensors"
    COMFY_NAME = f"characters/{FILENAME}"

    def _picture_with_a_lora(self, client) -> int:
        graph = json.loads(json.dumps(RECIPE_GRAPH))
        graph["5"] = {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "whatever-is-there.safetensors", "model": ["4", 0]},
        }
        files = [
            (
                "file",
                (
                    "lora-recipe.png",
                    _recipe_png_bytes(graph, (10, 20, 30)),
                    "image/png",
                ),
            )
        ]
        st = upload_pictures_and_wait(client, files, timeout_s=60)
        assert st["status"] == "completed", st
        r = client.get(f"{API}/pictures")
        assert r.status_code == 200, r.text
        return max(p["id"] for p in r.json())

    def _shelf_lora(self, server):
        with server.hub.transaction() as conn:
            conn.execute(
                "INSERT INTO model (file_kind, kind, filename, sha256, provenance) "
                "VALUES ('adapter', 'lora', ?, ?, 'scanned')",
                (self.FILENAME, self.SHA),
            )

    def _object_info_with_the_lora(self, monkeypatch):
        info = dict(OBJECT_INFO)
        info["LoraLoader"] = {
            "input": {"required": {"lora_name": [[self.COMFY_NAME], {}]}}
        }
        monkeypatch.setattr(comfyui_module, "fetch_object_info", lambda url: dict(info))

    def test_the_read_says_which_slots_a_replay_can_swap(self, env, monkeypatch):
        server, client, pic_id = env
        self._object_info_with_the_lora(monkeypatch)
        lora_pic = self._picture_with_a_lora(client)

        r = client.get(f"{API}/comfyui/pictures/{lora_pic}/recipe")
        assert r.status_code == 200, r.text
        assert [(s["node_id"], s["by"]) for s in r.json()["lora_slots"]] == [
            ("5", "filename")
        ]
        # The control: the picture beside it carries no loader and says so with
        # an empty list rather than by leaving the field out.
        r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe")
        assert r.status_code == 200 and r.json()["lora_slots"] == [], r.text

    def test_a_replay_runs_with_the_chosen_lora(self, env, monkeypatch):
        """And the swap is what makes it runnable at all.

        The recipe names a LoRA this ComfyUI does not have, which the pre-flight
        refuses - so the swap is applied *before* the graph is judged, and the
        control below proves the same replay is refused without one. A picture
        made with a LoRA that has since gone is exactly the one worth re-running
        with another.
        """
        server, client, pic_id = env
        self._object_info_with_the_lora(monkeypatch)
        self._shelf_lora(server)
        submitted = _capture_submissions(monkeypatch)
        lora_pic = self._picture_with_a_lora(client)

        r = client.post(
            f"{API}/comfyui/run_recipe",
            json={"picture_id": lora_pic, "adapter_sha256": self.SHA},
        )
        assert r.status_code == 200, r.text
        assert submitted[0]["5"]["inputs"]["lora_name"] == self.COMFY_NAME
        # Its seed was still re-rolled: the swap rides along with the replay,
        # it does not replace it.
        assert (
            submitted[0]["3"]["inputs"]["seed"] != RECIPE_GRAPH["3"]["inputs"]["seed"]
        )

        # The control: the same replay without a swap is refused, because the
        # LoRA the picture was made with is not on this ComfyUI.
        r = client.post(f"{API}/comfyui/run_recipe", json={"picture_id": lora_pic})
        assert r.status_code == 400, r.text
        assert "whatever-is-there.safetensors" in r.json()["detail"]
        assert len(submitted) == 1

    def test_a_recipe_with_no_lora_loader_is_refused_by_name(self, env, monkeypatch):
        server, client, pic_id = env
        self._object_info_with_the_lora(monkeypatch)
        self._shelf_lora(server)
        submitted = _capture_submissions(monkeypatch)

        r = client.post(
            f"{API}/comfyui/run_recipe",
            json={"picture_id": pic_id, "adapter_sha256": self.SHA},
        )
        assert r.status_code == 400, r.text
        assert "no LoRA loader" in r.json()["detail"]
        assert submitted == []

    def _typed_object_info(self) -> dict:
        """OBJECT_INFO with the outputs the insertion plan reads links by."""
        info = dict(OBJECT_INFO)
        info["CheckpointLoaderSimple"] = {
            **OBJECT_INFO["CheckpointLoaderSimple"],
            "output": ["MODEL", "CLIP", "VAE"],
        }
        info["KSampler"] = {**OBJECT_INFO["KSampler"], "output": ["LATENT"]}
        info["CLIPTextEncode"] = {
            **OBJECT_INFO["CLIPTextEncode"],
            "output": ["CONDITIONING"],
        }
        return info

    def test_a_replay_never_adds_the_pixlstash_loader(self, env, monkeypatch):
        """Its variant would be one Generate variants refuses to replay.

        The graph carries a PixlStash node then, and run_recipe refuses any
        such graph - so the loader that resolves a LoRA by digest is the one
        option a replay must not take, however well it would work here.
        """
        server, client, pic_id = env
        info = self._typed_object_info()
        # The core loader is there but does not list this shelf LoRA, which on
        # any other route is exactly when the digest loader goes in instead.
        info["LoraLoaderModelOnly"] = {
            "input": {
                "required": {
                    "model": ["MODEL", {}],
                    "lora_name": [["something-else.safetensors"], {}],
                }
            },
            "output": ["MODEL"],
        }
        info["PixlStashAdapterLoader"] = {
            "input": {
                "required": {
                    "model": ["MODEL", {}],
                    "adapter_sha256": ["STRING", {"default": ""}],
                },
                "optional": {"clip": ["CLIP", {}]},
            },
            "output": ["MODEL", "CLIP", "STRING"],
        }
        monkeypatch.setattr(comfyui_module, "fetch_object_info", lambda url: info)
        self._shelf_lora(server)
        submitted = _capture_submissions(monkeypatch)

        r = client.post(
            f"{API}/comfyui/run_recipe",
            json={
                "picture_id": pic_id,
                "adapter_sha256": self.SHA,
                "insert_lora_loader": True,
            },
        )
        assert r.status_code == 400, r.text
        assert "not on this ComfyUI" in r.json()["detail"]
        assert "PixlStashAdapterLoader" not in r.text
        assert submitted == []

        # The control: the same ComfyUI, the same shelf LoRA, listed by the
        # core loader - the replay runs and takes that one.
        info["LoraLoaderModelOnly"]["input"]["required"]["lora_name"] = [
            [self.COMFY_NAME],
            {},
        ]
        r = client.post(
            f"{API}/comfyui/run_recipe",
            json={
                "picture_id": pic_id,
                "adapter_sha256": self.SHA,
                "insert_lora_loader": True,
            },
        )
        assert r.status_code == 200, r.text
        assert submitted[0]["10"]["class_type"] == "LoraLoaderModelOnly"

    def test_a_recipe_with_no_lora_loader_takes_one_added_where_it_was_shown(
        self, env, monkeypatch
    ):
        """#1376: the read shows the splice, and the replay carries it out.

        RECIPE_GRAPH's text encoders read no CLIP, so the loader is model-only.
        """
        server, client, pic_id = env
        info = self._typed_object_info()
        info["LoraLoaderModelOnly"] = {
            "input": {
                "required": {
                    "model": ["MODEL", {}],
                    "lora_name": [[self.COMFY_NAME], {}],
                }
            },
            "output": ["MODEL"],
        }
        monkeypatch.setattr(comfyui_module, "fetch_object_info", lambda url: info)
        self._shelf_lora(server)
        submitted = _capture_submissions(monkeypatch)

        r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe")
        assert r.status_code == 200, r.text
        plan = r.json()["lora_insertion"]["plan"]
        assert plan["model"]["node_id"] == "4" and plan["clip"] is None
        assert [(w["node_id"], w["field"]) for w in plan["rewires"]] == [("3", "model")]

        r = client.post(
            f"{API}/comfyui/run_recipe",
            json={
                "picture_id": pic_id,
                "adapter_sha256": self.SHA,
                "insert_lora_loader": True,
            },
        )
        assert r.status_code == 200, r.text
        assert submitted[0]["10"]["class_type"] == "LoraLoaderModelOnly"
        assert submitted[0]["10"]["inputs"]["lora_name"] == self.COMFY_NAME
        assert submitted[0]["3"]["inputs"]["model"] == ["10", 0]

    def test_an_unchecked_replay_will_not_guess_a_name(self, env, monkeypatch):
        """No object_info, no swap: the acknowledgement covers the graph, not the file list.

        A 502 like every other run route, not a 400 saying the loader does not
        list its files: ComfyUI was never asked.
        """
        server, client, pic_id = env
        _comfyui_unreachable(monkeypatch)
        self._shelf_lora(server)
        submitted = _capture_submissions(monkeypatch)
        lora_pic = self._picture_with_a_lora(client)

        r = client.post(
            f"{API}/comfyui/run_recipe",
            json={
                "picture_id": lora_pic,
                "allow_unchecked": True,
                "adapter_sha256": self.SHA,
            },
        )
        assert r.status_code == 502 and "could not ask ComfyUI" in r.text
        assert submitted == []

    def test_a_missing_loader_node_is_named_not_mistaken_for_a_file_list(
        self, env, monkeypatch
    ):
        """The swap is resolved before the pre-flight, so it must say what the pre-flight would."""
        server, client, pic_id = env
        _comfyui_reachable(monkeypatch)  # OBJECT_INFO has no LoraLoader at all
        self._shelf_lora(server)
        submitted = _capture_submissions(monkeypatch)
        lora_pic = self._picture_with_a_lora(client)

        r = client.post(
            f"{API}/comfyui/run_recipe",
            json={"picture_id": lora_pic, "adapter_sha256": self.SHA},
        )
        assert r.status_code == 400, r.text
        assert "has no LoraLoader node" in r.json()["detail"]
        assert submitted == []


# ── The extended read (B5): the rest of the recipe, and the workflow filters ──

# A graph that actually carries what the extended read reports: both prompts
# wired to a sampler, a LoRA at two strengths, and the five sampler settings.
FULL_GRAPH = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 42,
            "steps": 25,
            "cfg": 7.5,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["5", 0],
            # Through a node that carries BOTH sides, so the walk has to
            # follow the one it started on rather than whichever it meets.
            "positive": ["8", 0],
            "negative": ["8", 1],
        },
    },
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    },
    "5": {
        "class_type": "LoraLoader",
        "inputs": {
            "lora_name": "example-style.safetensors",
            "strength_model": 0.8,
            "strength_clip": 0.6,
            "model": ["4", 0],
            "clip": ["4", 1],
        },
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "a castle on a hill", "clip": ["5", 1]},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "blurry, watermark", "clip": ["5", 1]},
    },
    "8": {
        "class_type": "ControlNetApplyAdvanced",
        "inputs": {
            "positive": ["6", 0],
            "negative": ["7", 0],
            "strength": 1.0,
        },
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "ComfyUI", "images": ["3", 0]},
    },
}

# One A1111 generation, written the way the web UI writes it: the prompts on
# their own lines, the fields on the last one, a LoRA as a prompt tag.
A1111_PARAMETERS = (
    "a castle on a hill <lora:example-style:0.8>\n"
    "Negative prompt: blurry, watermark\n"
    "Steps: 25, Sampler: Euler a, CFG scale: 7, Seed: 4242, Size: 512x768, "
    "Model hash: 0123456789, Model: sd_xl_base_1.0"
)


def _a1111_png_bytes(parameters: str, colour: tuple[int, int, int]) -> bytes:
    """A PNG carrying A1111 infotext in its ``parameters`` text chunk."""
    img = Image.new("RGB", (256, 256), colour)
    meta = PngInfo()
    meta.add_text("parameters", parameters)
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()


def _upload_one(client, name: str, data: bytes) -> int:
    """Import one picture and return its id."""
    st = upload_pictures_and_wait(
        client, [("file", (name, data, "image/png"))], timeout_s=60
    )
    assert st["status"] == "completed", st
    r = client.get(f"{API}/pictures")
    assert r.status_code == 200, r.text
    return max(p["id"] for p in r.json())


def _variant_of(server, pic_id: int) -> str:
    """The variant the import filed for *pic_id*, waited for.

    Read rather than written: the extraction pass owns
    ``picture.workflow_structural_hash`` and would overwrite a hand-written
    one, so a test that needs a picture on a card has to card the variant the
    picture actually has.
    """
    for _ in range(120):
        pics = server.vault.db.run_immediate_read_task(
            Picture.find, id=pic_id, select_fields=["id", "workflow_structural_hash"]
        )
        value = getattr(pics[0], "workflow_structural_hash", None) if pics else None
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(f"picture {pic_id} was never filed under a variant")


def _card_picture(server, pic_id: int, key: str, core: str) -> str:
    """Put *pic_id*'s variant on card *key*, and its topology in stack *core*.

    The rows the card pass derives, written directly with the names this test
    can assert on. Content-addressed rows (the topology and the recipe) are the
    pipeline's own and are left alone.
    """
    structural = _variant_of(server, pic_id)
    row = server.hub.fetchone(
        "SELECT topology_hash FROM workflow_recipe WHERE structural_hash = ?",
        (structural,),
    )
    assert row is not None, f"the hub filed no recipe for {structural}"
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_variant "
            "(structural_hash, topology_hash, workflow_key, key_version) "
            "VALUES (?, ?, ?, ?)",
            (structural, row["topology_hash"], key, WORKFLOW_KEY_VERSION),
        )
        conn.execute(
            "INSERT OR REPLACE INTO workflow_topology_core "
            "(topology_hash, core_hash, core_version, workflow_type, slots) "
            "VALUES (?, ?, ?, NULL, '[]')",
            (row["topology_hash"], core, CORE_RULE_VERSION),
        )
    return structural


class TestRecipeReadsTheWholeRecipe:
    def test_a_comfyui_graph_reports_prompts_settings_and_strengths(
        self, env, monkeypatch
    ):
        server, client, _ = env
        # Deliberately with ComfyUI down: none of these fields comes from it,
        # and the dialog has to be able to describe the recipe regardless.
        _comfyui_unreachable(monkeypatch)
        pic = _upload_one(client, "full.png", _recipe_png_bytes(FULL_GRAPH, (9, 9, 90)))

        r = client.get(f"{API}/comfyui/pictures/{pic}/recipe")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source"] == "comfyui"
        assert body["positive_prompt"] == "a castle on a hill"
        assert body["negative_prompt"] == "blurry, watermark"
        assert body["settings"] == {
            "steps": 25,
            "cfg": 7.5,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
        }
        assert body["models"] == ["sd_xl_base_1.0.safetensors"]
        assert [(s["node_id"], s["strengths"]) for s in body["lora_slots"]] == [
            ("5", {"model": 0.8, "clip": 0.6})
        ]

    def test_a_graph_with_no_sampler_settings_reports_none_not_a_guess(
        self, env, monkeypatch
    ):
        server, client, pic_id = env
        _comfyui_unreachable(monkeypatch)
        # RECIPE_GRAPH's sampler names no conditioning and no cfg/scheduler.
        body = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe").json()
        assert body["negative_prompt"] is None
        assert body["settings"] == {"steps": 20}

    def test_an_a1111_picture_answers_from_its_infotext(self, env, monkeypatch):
        server, client, _ = env
        _comfyui_unreachable(monkeypatch)
        pic = _upload_one(
            client, "a1111.png", _a1111_png_bytes(A1111_PARAMETERS, (200, 40, 40))
        )

        r = client.get(f"{API}/comfyui/pictures/{pic}/recipe")
        assert r.status_code == 200, r.text
        body = r.json()
        # Readable, but not submittable to ComfyUI: there is no graph to run.
        assert body["available"] is False
        assert body["reason"] == "a1111"
        assert body["source"] == "a1111"
        assert body["positive_prompt"] == "a castle on a hill"
        assert body["negative_prompt"] == "blurry, watermark"
        assert body["seed"] == 4242
        assert body["models"] == ["sd_xl_base_1.0.safetensors"]
        assert body["loras"] == ["example-style.safetensors"]
        assert [s["strengths"] for s in body["lora_slots"]] == [{"model": 0.8}]
        # No node is named: the reduction's ids are in no graph, and there is
        # no replay to send one back to.
        assert [s["node_id"] for s in body["lora_slots"]] == [None]
        # A value that is wholly a number is reported as one, so `steps` is a
        # number on BOTH branches rather than a string on this one; anything
        # else stays the text A1111 wrote.
        assert body["settings"] == {
            "steps": 25,
            "sampler": "Euler a",
            "cfg_scale": 7,
            "size": "512x768",
        }
        # The consent fields are about what would execute, and nothing would.
        assert body["node_classes"] == []
        assert body["node_count"] == 0

    def test_a_picture_with_neither_is_still_no_prompt_chunk(self, env, monkeypatch):
        """The control: the A1111 branch must not answer for a plain photo."""
        server, client, _ = env
        _comfyui_unreachable(monkeypatch)
        buf = io.BytesIO()
        Image.new("RGB", (256, 256), (7, 7, 7)).save(buf, format="PNG")
        pic = _upload_one(client, "plain.png", buf.getvalue())

        body = client.get(f"{API}/comfyui/pictures/{pic}/recipe").json()
        assert body["reason"] == "no_prompt_chunk"
        assert body["source"] == "comfyui"

    def test_the_workflow_key_is_the_card_the_hub_holds(self, env, monkeypatch):
        server, client, pic_id = env
        _comfyui_unreachable(monkeypatch)
        _card_picture(server, pic_id, "key-alpha", "core-shared")

        body = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe").json()
        assert body["workflow_key"] == "key-alpha"

    def test_a_variant_the_hub_has_no_card_for_gets_no_key(self, env):
        """The other direction: the read reports a card, it never invents one."""
        server, _client, _pic_id = env
        assert workflow_cards.key_of_variant(server.hub, "z" * 64) is None


class TestPictureListWorkflowFilters:
    """Both directions: the card's own pictures, and nobody else's."""

    def _two_carded_pictures(self, server, client) -> tuple[int, int]:
        alpha = _upload_one(
            client, "alpha.png", _recipe_png_bytes(FULL_GRAPH, (1, 2, 3))
        )
        beta = _upload_one(
            client, "beta.png", _a1111_png_bytes(A1111_PARAMETERS, (3, 2, 1))
        )
        _card_picture(server, alpha, "key-alpha", "core-shared")
        _card_picture(server, beta, "key-beta", "core-shared")
        return alpha, beta

    def _ids(self, client, query: str) -> set[int]:
        r = client.get(f"{API}/pictures{query}")
        assert r.status_code == 200, r.text
        return {p["id"] for p in r.json()}

    def test_a_key_lists_its_own_pictures_and_only_those(self, env):
        server, client, pic_id = env
        alpha, beta = self._two_carded_pictures(server, client)

        assert self._ids(client, "?workflow_key=key-alpha") == {alpha}
        assert self._ids(client, "?workflow_key=key-beta") == {beta}
        # The control: without the filter all three are there, so the two
        # assertions above are narrowing rather than describing an empty grid.
        assert {alpha, beta, pic_id} <= self._ids(client, "")

    def test_an_automatic_stack_lists_every_card_sharing_its_core_hash(self, env):
        server, client, _pic_id = env
        alpha, beta = self._two_carded_pictures(server, client)

        assert self._ids(client, "?workflow_stack=core-shared") == {alpha, beta}

    def test_a_stored_stack_lists_the_cards_it_names(self, env):
        """The other half of the same query: membership rows, not a core hash.

        Nothing writes these tables yet, so the rows are written here - the
        point is that a stack named by its own id resolves rather than coming
        back as an empty grid.
        """
        server, client, _pic_id = env
        alpha, _beta = self._two_carded_pictures(server, client)
        with server.hub.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO workflow_stack (stack_id, kind, core_hash) "
                "VALUES ('stack-one', 'manual', NULL)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO workflow_stack_member "
                "(stack_id, workflow_key, position) VALUES ('stack-one', 'key-alpha', 0)"
            )

        # Only the card the stack names, though both cards share a core hash.
        assert self._ids(client, "?workflow_stack=stack-one") == {alpha}

    def test_the_resolved_parameter_name_is_not_reachable_from_the_query(self, env):
        """`Picture.find` declares it now, so a client must not be able to send it.

        Unpopped it reaches `PredicateFilter` as a bare string and 500s on its
        `List[str]`. It could never widen anything - it only adds an IN - but a
        reachable 500 is its own bug.
        """
        server, client, pic_id = env

        r = client.get(f"{API}/pictures?workflow_structural_hashes=abc")
        assert r.status_code == 200, r.text
        assert pic_id in {p["id"] for p in r.json()}, "and it filtered nothing"

    def test_an_empty_parameter_is_a_filter_not_the_absence_of_one(self, env):
        """`?workflow_key=` names no card, so it must not answer with everything."""
        server, client, _pic_id = env
        self._two_carded_pictures(server, client)

        assert self._ids(client, "?workflow_key=") == set()

    def test_a_card_nothing_was_made_with_lists_nothing(self, env):
        """The direction that fails open if the empty list reads as no filter."""
        server, client, _ = env
        self._two_carded_pictures(server, client)

        assert self._ids(client, "?workflow_key=key-nobody-has") == set()
        assert self._ids(client, "?workflow_stack=core-nobody-has") == set()

    def test_the_two_filters_narrow_each_other(self, env):
        server, client, _ = env
        alpha, _ = self._two_carded_pictures(server, client)

        assert self._ids(
            client, "?workflow_key=key-alpha&workflow_stack=core-shared"
        ) == {alpha}
        assert (
            self._ids(client, "?workflow_key=key-alpha&workflow_stack=core-nobody-has")
            == set()
        )


class TestTheFieldsThatCannotBeRendered:
    """Pure functions, no server: what a crafted file must not be able to do."""

    def test_an_a1111_weight_that_is_not_a_finite_number_gives_no_strength(self):
        """`<lora:x:inf>` parses as a float and cannot be rendered.

        `allow_nan=False` is the renderer's default, so reporting it would turn
        the read into a 500 for a share-token holder. A weight that is not a
        single finite number gives nothing rather than something of another
        shape - `strengths` is numbers on both branches or it is absent.
        """
        for weight in ("inf", "-inf", "nan", "0.8,0.5", "", None, "heavy"):
            assert comfyui_module._a1111_strengths(weight) == {}, weight
        assert comfyui_module._a1111_strengths("0.8") == {"model": 0.8}

    def test_the_workflow_filter_fails_closed_when_the_hub_cannot_answer(self):
        """A filter that cannot be resolved lists nothing, never everything.

        The dangerous direction: widening a grid that was asked for one
        workflow back to the whole library reads as the filter working.
        """
        from pixlstash.routes.pictures._listing import _resolve_workflow_filter

        class _NoHub:
            hub = None

        class _BrokenHub:
            class hub:  # noqa: N801 - a stand-in, not a class being defined
                @staticmethod
                def fetchall(*_a, **_kw):
                    raise sqlite3.OperationalError("the hub is not answering")

        assert _resolve_workflow_filter(_NoHub(), {"workflow_key": "k"}) == []
        assert _resolve_workflow_filter(_BrokenHub(), {"workflow_key": "k"}) == []
        # The control: no filter asked for is still no filter, not an empty one.
        assert _resolve_workflow_filter(_NoHub(), {}) is None
