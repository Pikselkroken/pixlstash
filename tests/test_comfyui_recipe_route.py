"""Route-level tests for the recipe consent controls (R3, CWE-829).

The graph a recipe replays is **file metadata**: whoever made the image authored
it, and PixlStash's premise is importing images from elsewhere. Replaying it
executes it on the owner's ComfyUI, bounded only by which node packs are
installed. Two of the three controls are asserted here at the HTTP boundary,
because a control that only exists in the dialog is not a control:

1. ``GET /comfyui/pictures/{id}/recipe`` discloses the distinct ``class_type``
   list, so the owner can see what they are approving.
2. The read endpoint reports whether the source file came from *outside* this
   instance, which is what turns "an embedded workflow" into "someone else's
   embedded workflow".

The third is the refusal on the run itself. It used to be ``POST
/comfyui/run_recipe``, which #1410 retired; ``POST /workflows/run`` carries it
now, and ``tests/test_workflows_api.py`` asserts it in both directions -- the
refusal must fire without ``allow_unchecked`` AND the run must still succeed
with it, since over-blocking is its own regression.

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
from pixlstash.db_models.generation import Generation, GenerationInput
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


# ===========================================================================
# The Recipe section's read (#1313)
#
# `GET /comfyui/pictures/{id}/workflow` carries what the lightbox's Recipe
# section shows beside the graph it already served. Asserted here rather than in
# a suite of its own because this module already imports a picture whose file
# carries a real API `prompt` chunk, which is the expensive half; the graph
# reading itself is unit-tested in `tests/test_picture_recipe.py`.
# ===========================================================================


SHELF_CHECKPOINT_SHA = "c" * 64


def _shelf_checkpoint(server) -> None:
    """Put the recipe's own checkpoint on the shelf, named but not hashed alike.

    Named rather than digest-matched on purpose: the graph says
    ``ckpt_name``, so the match is by name and the badge must stay off.
    """
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, filename, sha256, provenance) "
            "VALUES ('checkpoint', ?, ?, 'scanned')",
            ("sd_xl_base_1.0.safetensors", SHELF_CHECKPOINT_SHA),
        )


def _shelf_model_id(server) -> int:
    row = server.hub.fetchall(
        "SELECT id FROM model WHERE filename = 'sd_xl_base_1.0.safetensors'"
    )
    assert row, "the shelf checkpoint was not written"
    return row[0]["id"]


def _topology_hash(server, pic_id: int) -> str:
    """The topology the scan filed for *pic_id*, waited for.

    Polled rather than read once, and read BEFORE the request that is asserted
    against it. The extraction pass stamps this column in the background, so a
    single read taken after the response compares two different moments: the
    route honestly answered `null` for a picture not yet filed, and the column
    was written while the assertion was being set up. That passed or failed on
    how busy the machine was - it went red only when `tests/test_migrations.py`
    ran first and slowed the scan down. `_variant_of` below waits for the same
    pass for the same reason.
    """
    for _ in range(120):
        pics = server.vault.db.run_immediate_read_task(
            Picture.find, id=pic_id, select_fields=["id", "workflow_topology_hash"]
        )
        value = getattr(pics[0], "workflow_topology_hash", None) if pics else None
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(f"the workflow scan never filed picture {pic_id}")


def _second_picture(client) -> int:
    """One more imported picture, to stand in as a run's input."""
    files = [
        (
            "file",
            (
                "input-recipe.png",
                _recipe_png_bytes(RECIPE_GRAPH, (30, 60, 90)),
                "image/png",
            ),
        )
    ]
    st = upload_pictures_and_wait(client, files, timeout_s=60)
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
            # `specials` is written even though this row is a stand-in:
            # NULL means "no pass has read this topology", so the live backfill
            # finder would pick the variant straight back up and overwrite the
            # hand-picked key and core hash this helper exists to install.
            "INSERT OR REPLACE INTO workflow_topology_core "
            "(topology_hash, core_hash, core_version, workflow_type, slots, "
            "specials) VALUES (?, ?, ?, NULL, '[]', '')",
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


def _lock_input(server, pic_id: int, input_id: int, pixel_sha: str):
    """Record that *pic_id*'s run loaded *input_id* at node 7, position 0.

    The ``generation`` row is merged rather than inserted: the workflow scan has
    already written one for a picture whose file carries an API prompt graph,
    which is every picture in this module.
    """

    def write(session):
        session.merge(Generation(picture_id=pic_id, seed="12345"))
        session.commit()
        session.merge(
            GenerationInput(
                picture_id=pic_id,
                node_ref="7",
                position=0,
                pixel_sha=pixel_sha,
                input_picture_id=input_id,
            )
        )
        session.commit()

    server.vault.db.run_task(write)


def _bin(server, picture_id: int):
    def write(session):
        picture = session.get(Picture, picture_id)
        picture.deleted = True
        session.add(picture)
        session.commit()

    server.vault.db.run_task(write)


def test_the_recipe_read_carries_the_models_settings_and_topology(env):
    server, client, pic_id = env
    _shelf_checkpoint(server)
    # Before the request, not after: see `_topology_hash`.
    topology = _topology_hash(server, pic_id)

    r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe?preflight=false")
    assert r.status_code == 200, r.text
    body = r.json()

    slots = {slot["name"]: slot for slot in body["model_slots"]}
    assert set(slots) == {"sd_xl_base_1.0.safetensors"}
    assert slots["sd_xl_base_1.0.safetensors"]["model_id"] == _shelf_model_id(server)
    # Matched by name, so it is a file called that and nothing stronger.
    assert slots["sd_xl_base_1.0.safetensors"]["verified"] is False

    assert body["settings"] == {"steps": 20}
    # The graph's bytes are the OTHER route's job now: one read says what the
    # picture was made with, the other hands over what ComfyUI can open.
    assert "workflow" not in body
    graph = client.get(f"{API}/comfyui/pictures/{pic_id}/workflow").json()
    assert graph["workflow"]["4"]["class_type"] == "CheckpointLoaderSimple"
    # And the key the "Open in Workflows" link navigates by, which the scan
    # wrote when it filed this picture.
    assert body["topology_hash"] == topology
    # An import is not lineage: nothing ran here, so no `generation_input` row
    # exists and none is invented.
    assert body["inputs"] == []


def test_the_resolution_lock_names_what_a_run_loaded_until_it_is_gone(env):
    """Both states in one environment: a `Server` boot is the expensive part of
    this module and neither half needs its own."""
    server, client, pic_id = env
    input_id = _second_picture(client)
    _lock_input(server, pic_id, input_id, "d" * 64)

    r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe?preflight=false")
    assert r.status_code == 200, r.text
    assert r.json()["inputs"] == [
        {
            "node_ref": "7",
            "position": 0,
            "pixel_sha": "d" * 64,
            "input_picture_id": input_id,
        }
    ]

    # Deleting the source does not unmake what was made from it - but the grid
    # can no longer show it, so the id must not be offered as if it could.
    _bin(server, input_id)
    r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe?preflight=false")
    assert r.status_code == 200, r.text
    row = r.json()["inputs"][0]
    assert row["input_picture_id"] is None and row["pixel_sha"] == "d" * 64


def test_a_scoped_token_is_served_the_graph_but_never_the_library(env):
    """The filename and the strength are in the graph it is already being
    served. Which row of the owner's shelf that file is, and which OTHER
    picture a run loaded, are not - the second is an id the gate refuses this
    token on every other route, plus a content hash of it."""
    server, client, pic_id = env
    _shelf_checkpoint(server)
    input_id = _second_picture(client)
    _lock_input(server, pic_id, input_id, "d" * 64)

    r = client.post(
        f"{API}/users/me/token",
        json={
            "description": "recipe scope probe",
            "scope": "READ",
            "resource_type": "picture",
            "resource_id": pic_id,
        },
    )
    assert r.status_code == 200, r.text
    scoped = TestClient(server.api)
    scoped.headers.update({"Authorization": f"Bearer {r.json()['token']}"})

    # The positive control: the credential is live and in scope for this route,
    # so what it does NOT get below is a refusal rather than a dead token.
    r = scoped.get(f"{API}/comfyui/pictures/{pic_id}/recipe?preflight=false")
    assert r.status_code == 200, r.text
    scoped_body = r.json()
    slot = scoped_body["model_slots"][0]
    assert slot["name"] == "sd_xl_base_1.0.safetensors"
    assert slot["model_id"] is None and slot["verified"] is False
    assert scoped_body["inputs"] == []

    # And the control on the other side: this token really is refused that
    # picture everywhere else, which is what makes serving its id here a leak.
    assert scoped.get(f"{API}/pictures/{input_id}/metadata").status_code == 403

    # The owner, on the same picture, does get both - over-blocking would be
    # its own regression.
    owner_body = client.get(
        f"{API}/comfyui/pictures/{pic_id}/recipe?preflight=false"
    ).json()
    assert owner_body["model_slots"][0]["model_id"] == _shelf_model_id(server)
    assert owner_body["inputs"][0]["input_picture_id"] == input_id


def test_the_lightbox_read_asks_comfyui_nothing(env, monkeypatch):
    """`?preflight=false` is the whole reason one read can serve both callers.

    The Recipe tab re-reads on every filmstrip step, so a ComfyUI round-trip per
    arrow-key is not affordable; the Remix dialog keeps the pre-flight because
    it is about to run the thing. Asserted by making the call explode: if the
    route still reaches for `/object_info`, this test says so.
    """
    _server, client, pic_id = env
    asked = []

    def boom(url):
        asked.append(url)
        raise AssertionError("the lightbox read must not ask ComfyUI anything")

    monkeypatch.setattr(comfyui_module, "fetch_object_info", boom)

    r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe?preflight=false")
    assert r.status_code == 200, r.text
    assert asked == []
    # And it says so rather than implying the graph passed a check it skipped.
    assert r.json()["preflight"]["checked"] is False

    # The control: the default still asks, so the flag is doing the work and
    # the Remix dialog's pre-flight has not been quietly switched off.
    _comfyui_reachable(monkeypatch)
    r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe")
    assert r.status_code == 200, r.text
    assert r.json()["preflight"]["checked"] is True


# ── The editor graph ────────────────────────────────────────────────────────
#
# A ComfyUI picture does not always carry the API `prompt` chunk: a file
# exported from the editor, or re-saved by a node that writes only `workflow`,
# carries the editor's own serialisation instead. Those pictures used to answer
# `no_prompt_chunk` - the same answer as a holiday photo - and so lost their
# whole recipe and the offer to run it again.
#
# They are now converted against ComfyUI's own `/object_info`, exactly or not
# at all. The conversion itself is covered in
# `tests/test_comfyui_ui_graph_conversion.py`; what is asserted here is the
# route's half: that the picture answers like an API-chunk one when the
# rebuild works, that it still fills in the recipe and names the reason when it
# does not, and that a run submits the rebuilt graph rather than the file's.

# Declares the wiring as well as the widgets, so the rebuilt graph is a real
# graph and not a set of loose values.
EDITOR_OBJECT_INFO = {
    "CheckpointLoaderSimple": {
        "input": {"required": {"ckpt_name": [["sd_xl_base_1.0.safetensors"], {}]}},
        "input_order": {"required": ["ckpt_name"]},
    },
    "CLIPTextEncode": {
        "input": {"required": {"text": ["STRING", {"multiline": True}]}},
        "input_order": {"required": ["text"]},
    },
    "KSampler": {
        "input": {
            "required": {
                "model": ["MODEL", {}],
                "seed": ["INT", {"default": 0, "control_after_generate": True}],
                "steps": ["INT", {"default": 20}],
                "positive": ["CONDITIONING", {}],
            }
        },
        "input_order": {"required": ["model", "seed", "steps", "positive"]},
    },
    "SaveImage": {
        "input": {
            "required": {
                "images": ["IMAGE", {}],
                "filename_prefix": ["STRING", {}],
            }
        },
        "input_order": {"required": ["images", "filename_prefix"]},
    },
}

# The same picture as RECIPE_GRAPH, in the editor's serialisation: widgets in a
# positional array with the control-after-generate row between the seed and the
# steps, and wires in a link table.
EDITOR_GRAPH = {
    "last_node_id": 9,
    "last_link_id": 3,
    "nodes": [
        {
            "id": 4,
            "type": "CheckpointLoaderSimple",
            "mode": 0,
            "inputs": [],
            "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}],
            "widgets_values": ["sd_xl_base_1.0.safetensors"],
        },
        {
            "id": 6,
            "type": "CLIPTextEncode",
            "mode": 0,
            "inputs": [],
            "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [2]}],
            "widgets_values": ["a cat in a hat"],
        },
        {
            "id": 3,
            "type": "KSampler",
            "mode": 0,
            "inputs": [
                {"name": "model", "type": "MODEL", "link": 1},
                {"name": "positive", "type": "CONDITIONING", "link": 2},
            ],
            "outputs": [{"name": "LATENT", "type": "LATENT", "links": [3]}],
            "widgets_values": [12345, "randomize", 20],
        },
        {
            "id": 9,
            "type": "SaveImage",
            "mode": 0,
            "inputs": [{"name": "images", "type": "IMAGE", "link": 3}],
            "outputs": [],
            "widgets_values": ["ComfyUI"],
        },
    ],
    "links": [
        [1, 4, 0, 3, 0, "MODEL"],
        [2, 6, 0, 3, 1, "CONDITIONING"],
        [3, 3, 0, 9, 0, "IMAGE"],
    ],
}

# What the editor graph above must rebuild into, in full. Asserted whole rather
# than field by field: the failure this guards against is a value landing on
# the wrong input, which a spot-check of two fields would not see.
EXPECTED_REBUILD = {
    "4": {
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
        "class_type": "CheckpointLoaderSimple",
        "_meta": {"title": "CheckpointLoaderSimple"},
    },
    "6": {
        "inputs": {"text": "a cat in a hat"},
        "class_type": "CLIPTextEncode",
        "_meta": {"title": "CLIPTextEncode"},
    },
    "3": {
        "inputs": {
            "model": ["4", 0],
            "seed": 12345,
            "steps": 20,
            "positive": ["6", 0],
        },
        "class_type": "KSampler",
        "_meta": {"title": "KSampler"},
    },
    "9": {
        "inputs": {"images": ["3", 0], "filename_prefix": "ComfyUI"},
        "class_type": "SaveImage",
        "_meta": {"title": "SaveImage"},
    },
}


def _editor_png_bytes(graph: dict, colour: tuple[int, int, int]) -> bytes:
    """A PNG carrying *graph* in the ``workflow`` chunk and nothing else.

    Deliberately no ``prompt`` chunk: this is the file the whole section is
    about, and adding one would let the route answer from it instead.
    """
    img = Image.new("RGB", (256, 256), colour)
    meta = PngInfo()
    meta.add_text("workflow", json.dumps(graph))
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()


@pytest.fixture(scope="module")
def editor_env():
    """A server holding one picture whose only graph is the editor's.

    Module-scoped: standing the server up costs more than every assertion in
    this section put together, and none of them writes to the picture.
    """
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
                    "editor-only.png",
                    _editor_png_bytes(EDITOR_GRAPH, (40, 160, 90)),
                    "image/png",
                ),
            )
        ]
        st = upload_pictures_and_wait(client, files, timeout_s=60)
        assert st["status"] == "completed", st
        r = client.get(f"{API}/pictures")
        assert r.status_code == 200, r.text
        picture_ids = [p["id"] for p in r.json()]
        assert picture_ids, "The editor-graph picture did not import"
        yield server, client, picture_ids[0]
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


@pytest.fixture(autouse=True)
def _forget_object_info():
    """Drop the route's `/object_info` cache around every test in this file.

    The editor read reuses a map for a minute, and these tests change what
    ComfyUI answers between them - so without this a test asserting "ComfyUI is
    unreachable" would be served the previous test's map and pass for the wrong
    reason.
    """
    comfyui_module._forget_cached_object_info()
    yield
    comfyui_module._forget_cached_object_info()


def _editor_comfyui_reachable(monkeypatch):
    monkeypatch.setattr(
        comfyui_module, "fetch_object_info", lambda url: dict(EDITOR_OBJECT_INFO)
    )


class TestAnEditorGraphIsRecognised:
    def test_it_answers_like_a_picture_that_carried_the_api_chunk(
        self, editor_env, monkeypatch
    ):
        _server, client, pic_id = editor_env
        _editor_comfyui_reachable(monkeypatch)
        r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe?preflight=false")
        assert r.status_code == 200, r.text
        body = r.json()
        # Not `no_prompt_chunk`, which is what this picture used to answer.
        assert body["available"] is True, body
        assert body["reason"] is None
        assert body["source"] == "comfyui"
        assert body["converted_from_editor_graph"] is True
        assert body["conversion_problems"] == []
        # Named for the chunk it came out of, so a reader can tell a rebuilt
        # graph from the one ComfyUI executed.
        assert body["summary"] == "Editor Workflow · 4 nodes"
        assert body["positive_prompt"] == "a cat in a hat"
        assert body["seed"] == 12345
        assert body["seed_text"] == "12345"
        assert body["settings"]["steps"] == 20
        assert body["models"] == ["sd_xl_base_1.0.safetensors"]
        assert body["node_classes"] == EXPECTED_CLASSES
        assert body["seed_inputs"], "a rebuilt graph must still offer a new seed"

    def test_the_editor_read_does_ask_comfyui_even_with_preflight_off(
        self, editor_env, monkeypatch
    ):
        """The one exception to `?preflight=false` costing no network.

        Without `/object_info` there is nothing to report about an editor
        graph at all - not merely nothing to judge - so the flag cannot switch
        this read off. Asserted rather than assumed, because the sibling test
        above asserts the opposite for an API-chunk picture and the two
        contracts have to be told apart.
        """
        _server, client, pic_id = editor_env
        asked = []

        def counted(url):
            asked.append(url)
            return dict(EDITOR_OBJECT_INFO)

        monkeypatch.setattr(comfyui_module, "fetch_object_info", counted)
        r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe?preflight=false")
        assert r.status_code == 200, r.text
        assert len(asked) == 1
        # And the second read is served from the cache, which is what makes
        # walking the filmstrip affordable.
        r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe?preflight=false")
        assert r.status_code == 200, r.text
        assert len(asked) == 1, "the map must be reused, not re-fetched per step"

    def test_a_rebuild_that_cannot_be_exact_still_reports_the_recipe(
        self, editor_env, monkeypatch
    ):
        """The distinction the whole change turns on.

        "Nothing here was made in ComfyUI" and "this is a ComfyUI picture
        PixlStash cannot hand back to ComfyUI" are two different answers, and
        only the second one is true of this file.
        """
        _server, client, pic_id = editor_env
        partial = {
            k: v for k, v in EDITOR_OBJECT_INFO.items() if k != "CheckpointLoaderSimple"
        }
        monkeypatch.setattr(comfyui_module, "fetch_object_info", lambda url: partial)
        r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe?preflight=false")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is False
        assert body["reason"] == "editor_graph"
        assert body["source"] == "comfyui"
        assert body["converted_from_editor_graph"] is False
        assert body["conversion_problems"] == [
            "this ComfyUI has no node class 'CheckpointLoaderSimple'"
        ]
        # The recipe itself is still there: read off the editor graph, which is
        # what the file actually says.
        assert body["summary"] == "Editor Workflow · 4 nodes · 3 links"
        assert body["positive_prompt"] == "a cat in a hat"
        assert body["models"] == ["sd_xl_base_1.0.safetensors"]

    def test_an_unreachable_comfyui_is_a_refusal_not_a_guess(
        self, editor_env, monkeypatch
    ):
        _server, client, pic_id = editor_env
        _comfyui_unreachable(monkeypatch)
        r = client.get(f"{API}/comfyui/pictures/{pic_id}/recipe?preflight=false")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["reason"] == "editor_graph"
        assert body["conversion_problems"] == [
            "PixlStash could not ask ComfyUI which nodes it has"
        ]
        assert body["positive_prompt"] == "a cat in a hat"


class TestAnEditorGraphCanBeRunAgain:
    def test_the_run_submits_the_rebuilt_graph(self, editor_env, monkeypatch):
        _server, client, pic_id = editor_env
        _editor_comfyui_reachable(monkeypatch)
        submitted = _capture_submissions(monkeypatch)
        r = client.post(
            f"{API}/comfyui/run_recipe",
            json={
                "picture_id": pic_id,
                "seed_mode": "fixed",
                "seed": 777,
                "stack": False,
            },
        )
        assert r.status_code == 200, r.text
        assert len(submitted) == 1
        graph = submitted[0]
        # The seed is the one the request pinned; everything else is the
        # rebuild, asserted whole.
        assert graph["3"]["inputs"]["seed"] == 777
        expected = json.loads(json.dumps(EXPECTED_REBUILD))
        expected["3"]["inputs"]["seed"] = 777
        assert graph == expected

    def test_a_graph_that_cannot_be_rebuilt_is_refused_by_name(
        self, editor_env, monkeypatch
    ):
        """Refused with what to go and fix, not with "no executable workflow".

        The file HAS a workflow; what it does not have is a node class this
        ComfyUI can run, and the two send the reader to different places.
        """
        _server, client, pic_id = editor_env
        partial = {k: v for k, v in EDITOR_OBJECT_INFO.items() if k != "KSampler"}
        monkeypatch.setattr(comfyui_module, "fetch_object_info", lambda url: partial)
        submitted = _capture_submissions(monkeypatch)
        r = client.post(
            f"{API}/comfyui/run_recipe", json={"picture_id": pic_id, "stack": False}
        )
        assert r.status_code == 400, r.text
        assert "KSampler" in r.json()["detail"]
        assert submitted == [], "nothing may be submitted when the rebuild failed"

    def test_the_run_never_trusts_a_map_the_read_cached(self, editor_env, monkeypatch):
        """A run decides what executes, so it asks ComfyUI itself every time.

        The read may serve a minute-old map; a run that did the same could
        submit a graph against node definitions that have since changed.
        """
        _server, client, pic_id = editor_env
        _editor_comfyui_reachable(monkeypatch)
        # Warm the read's cache.
        assert (
            client.get(
                f"{API}/comfyui/pictures/{pic_id}/recipe?preflight=false"
            ).status_code
            == 200
        )
        asked = []

        def counted(url):
            asked.append(url)
            return dict(EDITOR_OBJECT_INFO)

        monkeypatch.setattr(comfyui_module, "fetch_object_info", counted)
        _capture_submissions(monkeypatch)
        r = client.post(
            f"{API}/comfyui/run_recipe", json={"picture_id": pic_id, "stack": False}
        )
        assert r.status_code == 200, r.text
        assert asked, "the run must read /object_info rather than reuse the cache"
