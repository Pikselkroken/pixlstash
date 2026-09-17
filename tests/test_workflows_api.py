"""The Workflows view's read API (implementation plan §F1/§F2), both authz directions.

Environment sharing
-------------------
One ``Server`` per module, built once, because the boot is the expensive part
and everything this suite asserts is a row. The hub rows are written with plain
SQL and the vault's pictures through one queued task; the autouse fixture wipes
and re-seeds both before every test and re-mints the credentials, so no
assertion can inherit another test's state and no refusal can pass because the
token was dead rather than because the scope was refused.

The seeded library is shaped around the three states the list has to survive
(design ``States.dc.html``), so each is an assertion rather than a judgement:

* a topology with **two variants** and kept pictures — the ordinary row, and the
  one whose expansion has to add up;
* a topology whose every picture is **soft-deleted**, which must read as *none
  kept* rather than vanishing or reading as live;
* a recipe whose **asset names were forgotten**, which must still list, still
  group and still expand, and simply stop saying which models it used.

Both directions on every route, per §16.1: the owner 200s (over-blocking is its
own regression) and every scoped share token is 403'd by the gate's
``OWNER_ONLY`` declaration, with an in-scope positive control proving the
refused credential is live.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import delete, select

from pixlstash import auth
from pixlstash.authz.policy import AccessPolicy
from pixlstash.authz.registry import ROUTE_POLICIES
from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, ReferenceFolder
from pixlstash.hub.workflows import PictureGhost, record_picture_ghosts
from pixlstash.services.workflow_hash import WorkflowGraphError, asset_reference
from pixlstash.services.workflow_io import detect_workflow_io
import pixlstash.routes.comfyui as comfyui_module
from pixlstash.services import workflow_bindings, workflow_inbox
from pixlstash.server import Server
from pixlstash.tasks.ghost_cascade_task import GhostCascadeTask
from pixlstash.tasks.task_type import TaskType
from tests.authz_guard import assert_real_route, no_spa_fallback  # noqa: F401

API = "/api/v1"

# The SPA catch-all answers an unmatched GET with 200, which would make every
# positive assertion below vacuous if a path were misspelled.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")

_WORKFLOW_ROUTES = (
    ("GET", "/api/v1/workflows"),
    ("GET", "/api/v1/workflows/{topology_hash}/variants"),
    ("GET", "/api/v1/workflows/{topology_hash}/pictures"),
    ("GET", "/api/v1/workflows/recipes/{structural_hash}/graph"),
    # The ghost routes. Pinned here as well as refused in the authz test below:
    # every token that test can mint is READ, which the middleware refuses on a
    # DELETE before the gate reads the declaration, so a loosened entry would
    # leave that test green.
    ("GET", "/api/v1/server-config/ghost-retention"),
    ("PATCH", "/api/v1/server-config/ghost-retention"),
    ("DELETE", "/api/v1/server-config/ghost-retention/ghosts"),
    ("DELETE", "/api/v1/server-config/ghost-retention/model-ghosts"),
)


def _h(name: str) -> str:
    """A stable stand-in for one graph key.

    Digested rather than spelled out, because the routes check the shape: a key
    is 64 hex characters, and a readable stand-in padded to that length is
    refused as malformed by exactly the guard this suite also asserts.
    """
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


BUSY_TOPOLOGY = _h("busytopology")
BUSY_RECIPE_A = _h("busyrecipea")
BUSY_RECIPE_B = _h("busyrecipeb")
BINNED_TOPOLOGY = _h("binnedtopology")
BINNED_RECIPE = _h("binnedrecipe")
FORGOTTEN_TOPOLOGY = _h("forgottentopology")
FORGOTTEN_RECIPE = _h("forgottenrecipe")

# (structural_hash, topology_hash, node_count, first_seen_at)
_SEED_RECIPES = (
    (BUSY_RECIPE_A, BUSY_TOPOLOGY, 47, "2026-08-01T00:00:00Z"),
    (BUSY_RECIPE_B, BUSY_TOPOLOGY, 47, "2026-08-02T00:00:00Z"),
    (BINNED_RECIPE, BINNED_TOPOLOGY, 12, "2026-08-03T00:00:00Z"),
    (FORGOTTEN_RECIPE, FORGOTTEN_TOPOLOGY, 38, "2026-08-04T00:00:00Z"),
)

# (structural_hash, widget_name, normalized_filename). The forgotten recipe has
# none, which is the state itself and not a missing row.
_SEED_ASSETS = (
    (BUSY_RECIPE_A, "ckpt_name", "realvisxl.safetensors"),
    (BUSY_RECIPE_A, "lora_name", "add_detail.safetensors"),
    (BUSY_RECIPE_B, "ckpt_name", "realvisxl.safetensors"),
)

# The stored shape: every asset an ``asset_reference``. The forgotten recipe's
# three references have no asset row behind them, which is what "names
# forgotten" is read from.
_DOCUMENTS = {
    BUSY_RECIPE_A: {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": asset_reference("realvisxl.safetensors")},
        },
        "2": {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": asset_reference("add_detail.safetensors"),
                "strength_model": None,
                "model": ["1", 0],
            },
        },
    },
    BUSY_RECIPE_B: {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": asset_reference("realvisxl.safetensors")},
        },
    },
    BINNED_RECIPE: {"1": {"class_type": "SaveImage", "inputs": {}}},
    FORGOTTEN_RECIPE: {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": asset_reference("gone_base.safetensors")},
        },
        "2": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": asset_reference("gone_one.safetensors")},
        },
        "3": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": asset_reference("gone_two.safetensors")},
        },
    },
}

# The one model on the shelf: BUSY's checkpoint. Its LoRA is not, which makes
# ``add_detail.safetensors`` a model ghost.
_SHELF_FILENAME = "realvisxl.safetensors"

# (file_path, topology, structural, deleted, created_at)
_SEED_PICTURES = (
    ("busy_one.png", BUSY_TOPOLOGY, BUSY_RECIPE_A, False, "2026-08-10T00:00:00Z"),
    ("busy_two.png", BUSY_TOPOLOGY, BUSY_RECIPE_A, False, "2026-08-11T00:00:00Z"),
    ("busy_three.png", BUSY_TOPOLOGY, BUSY_RECIPE_B, False, "2026-08-12T00:00:00Z"),
    ("binned.png", BINNED_TOPOLOGY, BINNED_RECIPE, True, "2026-08-13T00:00:00Z"),
    (
        "forgotten.png",
        FORGOTTEN_TOPOLOGY,
        FORGOTTEN_RECIPE,
        False,
        "2026-08-14T00:00:00Z",
    ),
    # Read for a workflow and found to carry none: it counts towards `scanned`
    # and belongs to no topology. Every real library has these.
    ("photograph.jpg", None, None, False, "2026-08-15T00:00:00Z"),
)

# The one picture the pass has NOT reached. Seeded separately because it is the
# only row with a NULL `workflow_hash_version`, which is the whole difference
# between "we have read everything" and "we are still reading" — and a fixture
# where every picture is scanned makes that field's test pass against a count of
# any column at all.
_UNSCANNED_PICTURE = ("not_read_yet.png", "2026-08-16T00:00:00Z")


def _stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _seed_hub(server) -> None:
    """Write the four workflow tables from scratch."""
    with server.hub.transaction() as conn:
        # Children before parents: the hub enforces foreign keys, so a leftover
        # row aborts the wipe rather than lingering.
        conn.execute("DELETE FROM workflow_recipe_asset")
        conn.execute("DELETE FROM workflow_recipe_graph")
        conn.execute("DELETE FROM workflow_recipe")
        conn.execute("DELETE FROM workflow_topology")
        conn.execute("DELETE FROM workflow_picture_ghost")
        conn.execute("DELETE FROM workflow_picture_input")
        conn.execute("DELETE FROM workflow_parameter_pins")
        conn.execute(
            "DELETE FROM model WHERE filename IN (?, ?)",
            (_SHELF_FILENAME, "add_detail.safetensors"),
        )
        # Hashed, like a checkpoint the finder has already read: an unhashed
        # one holds back every digest judgement (see the digest tests below).
        conn.execute(
            "INSERT INTO model (file_kind, filename, sha256, provenance) "
            "VALUES ('checkpoint', ?, ?, 'scanned')",
            (_SHELF_FILENAME, _h("realvisxl-digest")),
        )
        for topology, node_count, first_seen in (
            (BUSY_TOPOLOGY, 47, "2026-08-01T00:00:00Z"),
            (BINNED_TOPOLOGY, 12, "2026-08-03T00:00:00Z"),
            (FORGOTTEN_TOPOLOGY, 38, "2026-08-04T00:00:00Z"),
        ):
            conn.execute(
                "INSERT INTO workflow_topology "
                "(topology_hash, hash_version, node_count, first_seen_at) "
                "VALUES (?, 'v1', ?, ?)",
                (topology, node_count, first_seen),
            )
        conn.executemany(
            "INSERT INTO workflow_recipe "
            "(structural_hash, topology_hash, hash_version, node_count, first_seen_at) "
            "VALUES (?, ?, 'v1', ?, ?)",
            _SEED_RECIPES,
        )
        conn.executemany(
            "INSERT INTO workflow_recipe_asset "
            "(structural_hash, widget_name, normalized_filename) VALUES (?, ?, ?)",
            _SEED_ASSETS,
        )
        conn.executemany(
            "INSERT INTO workflow_recipe_graph "
            "(structural_hash, document_sha256, document, created_at) "
            "VALUES (?, 'x', ?, '2026-08-01T00:00:00Z')",
            [(key, json.dumps(doc)) for key, doc in _DOCUMENTS.items()],
        )


def _seed_pictures(server) -> None:
    """Replace the vault's pictures with the seeded set, in one queued task."""

    def write(session):
        session.exec(delete(Picture))
        for path, topology, structural, deleted, created in _SEED_PICTURES:
            session.add(
                Picture(
                    file_path=path,
                    deleted=deleted,
                    created_at=_stamp(created),
                    workflow_topology_hash=topology,
                    workflow_structural_hash=structural,
                    workflow_hash_version="v1",
                )
            )
        path, created = _UNSCANNED_PICTURE
        session.add(Picture(file_path=path, deleted=False, created_at=_stamp(created)))
        session.commit()

    server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


def _quiesce_background_work(server):
    """Take every work finder out of the planner and let the pipeline settle.

    A shared server is WARM, so its sweeps land inside the tests rather than
    sitting in the long backoff a freshly-built one is in — and this module's
    fixtures are hand-placed rows that those sweeps rewrite. The one that
    matters here is ``MissingComfyUIExtractionFinder``: it looks for exactly the
    NULL ``workflow_hash_version`` this suite seeds to prove the difference
    between "read everything" and "still reading", reads the (nonexistent) file
    and stamps the column, and the scan assertion then measured whichever ran
    first. Every finder goes, not a curated subset: nothing here needs derived
    data, every assertion is a status code or a count over rows this file
    wrote.

    The planner thread and the task runner keep running, so a route that submits
    work directly is unaffected. Returns the removed names so the per-test
    fixture can re-check that they are still gone.
    """
    planner = server.vault._work_planner
    task_types = list(server.vault._planner_work_finders)
    for task_type in task_types:
        server.vault._planner_work_finders.pop(task_type)
    removed = planner.detach_finders(task_types)

    # Work already queued when the finders went is still ours to wait for: it
    # would otherwise write into the first test's freshly seeded library.
    runner = server.vault._task_runner
    runner.cancel_pending_tasks()
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        with runner._active_task_lock:
            active = list(runner._active_tasks.values())
        if not active:
            return removed
        time.sleep(0.05)
    raise AssertionError(
        f"background work did not settle within 60s; still running: {active}"
    )


@pytest.fixture(scope="module")
def workflow_env():
    """One Server and one owner login, for every test in the module."""
    tmp = tempfile.TemporaryDirectory()
    config_path = f"{tmp.name}/server-config.json"
    with open(config_path, "w") as handle:
        json.dump({"port": 8000}, handle)
    server = Server(config_path)
    server.__enter__()
    try:
        owner = TestClient(server.api, raise_server_exceptions=True)
        # `example-` marks the value as invented, per CLAUDE.md's stand-in
        # table. The rest of the suite writes `ownerpass1`, which predates the
        # rule and is not this file's to change; a new line follows it.
        r = owner.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-ownerpass1"},
        )
        assert r.status_code == 200, r.text

        r = owner.post(f"{API}/characters", json={"name": "Workflow Character"})
        assert r.status_code in {200, 201}, r.text
        character_id = r.json().get("id") or r.json()["character"]["id"]

        extraction_finder = server.vault._planner_work_finders[
            TaskType.COMFYUI_EXTRACTION
        ]
        detached = _quiesce_background_work(server)

        yield SimpleNamespace(
            server=server,
            owner=owner,
            character_id=character_id,
            detached=detached,
            extraction_finder=extraction_finder,
        )
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


@pytest.fixture(autouse=True)
def fresh_library(workflow_env):
    """Re-seed the hub and the vault before every test.

    Identity, not counts, for the shared-environment reason: every assertion
    below names the workflow it expects, so state left by another test cannot
    make one pass for the wrong reason.
    """
    # Re-checked every test rather than trusted from module setup: a finder that
    # came back would rewrite the seeded rows and the failure would look like a
    # bug in the route.
    assert not workflow_env.server.vault._planner_work_finders, (
        "a work finder is back in the planner; the seeded rows are no longer "
        "the only thing writing to this vault"
    )
    _seed_hub(workflow_env.server)
    _seed_pictures(workflow_env.server)
    # The owner session is what every positive control runs on; prove it is live
    # before any refusal is measured against it.
    r = workflow_env.owner.get(f"{API}/workflows")
    assert r.status_code == 200, (
        f"the shared owner session cannot read the library ({r.status_code}: "
        f"{r.text}) — every refusal below would prove nothing"
    )
    yield workflow_env


def _by_hash(payload) -> dict:
    return {row["topology_hash"]: row for row in payload["workflows"]}


def _mint(owner_client, description: str, **restriction) -> str:
    r = owner_client.post(
        f"{API}/users/me/token",
        json={"description": description, "scope": "READ", **restriction},
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(server, token: str) -> TestClient:
    client = TestClient(server.api)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ===========================================================================
# Declarations — the registry entry is the route's only authorization
# ===========================================================================


def test_the_workflow_scan_records_instances_for_the_open_library(workflow_env):
    """Without the library, the scan files recipes and silently no instance."""
    library_uuid = workflow_env.server.vault.library_uuid
    assert library_uuid
    assert workflow_env.extraction_finder._library_uuid == library_uuid


def test_every_workflow_route_is_declared_owner_only():
    """§16.1: the declaration IS the enforcement, so pin every cell.

    OWNER_ONLY is a decision here rather than a default: the counts are read
    across every non-deleted picture in the vault, so a scoped token holding
    them would learn the size of the whole library one workflow at a time.
    """
    for key in _WORKFLOW_ROUTES:
        assert key in ROUTE_POLICIES, f"{key} has no ROUTE_POLICIES entry"
        assert ROUTE_POLICIES[key].policy is AccessPolicy.OWNER_ONLY, (
            f"{key} declares {ROUTE_POLICIES[key].policy}, not OWNER_ONLY"
        )


def test_no_scoped_token_can_read_the_workflow_library(workflow_env):
    """Every route refuses a live resource-scoped share token.

    ``assert_real_route`` is load-bearing: the middleware answers before
    routing, so a renamed route would 403 identically and the assertion would
    dissolve into a test of nothing.
    """
    token = _mint(
        workflow_env.owner,
        "workflow scope probe",
        resource_type="character",
        resource_id=workflow_env.character_id,
    )
    client = _bearer(workflow_env.server, token)
    assert client.get(f"{API}/pictures").status_code == 200, (
        "the scoped token is dead; the refusals below would prove nothing"
    )
    paths = (
        f"{API}/workflows",
        f"{API}/workflows/{BUSY_TOPOLOGY}/variants",
        f"{API}/workflows/{BUSY_TOPOLOGY}/pictures",
        f"{API}/workflows/recipes/{BUSY_RECIPE_A}/graph",
    )
    for path in paths:
        assert_real_route(workflow_env.server.api, "GET", path)
        r = client.get(path)
        assert r.status_code == 403, f"GET {path}: {r.status_code} {r.text}"


# ===========================================================================
# The list opens at topology level
# ===========================================================================


def test_the_list_is_one_row_per_topology_not_per_recipe(workflow_env):
    """§F1's whole shape: four recipes, three rows, variants counted not listed."""
    payload = workflow_env.owner.get(f"{API}/workflows").json()
    rows = _by_hash(payload)
    assert set(rows) == {BUSY_TOPOLOGY, BINNED_TOPOLOGY, FORGOTTEN_TOPOLOGY}
    assert rows[BUSY_TOPOLOGY]["variants"] == 2
    assert rows[BINNED_TOPOLOGY]["variants"] == 1


def test_a_row_counts_the_kept_pictures_and_names_when_they_were_made(workflow_env):
    rows = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())
    assert rows[BUSY_TOPOLOGY]["pictures"] == 3
    assert rows[BUSY_TOPOLOGY]["last_used"].startswith("2026-08-12")


def test_a_workflow_whose_pictures_are_all_binned_reads_as_none_kept(workflow_env):
    """It must still list — the graph outliving its pictures is the point of the
    hub — and it must read as zero rather than as live."""
    rows = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())
    assert BINNED_TOPOLOGY in rows
    assert rows[BINNED_TOPOLOGY]["pictures"] == 0
    assert rows[BINNED_TOPOLOGY]["last_used"] is None


def test_forgotten_model_names_leave_the_row_intact_and_the_assets_empty(
    workflow_env,
):
    """ "Forget this model's name" is a row delete, so the workflow keeps
    listing, keeps its node count and simply stops saying what it used."""
    rows = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())
    row = rows[FORGOTTEN_TOPOLOGY]
    assert row["assets"] == []
    assert row["node_count"] == 38
    assert row["pictures"] == 1


def test_forgotten_names_are_counted_so_the_row_can_say_how_many(workflow_env):
    """The hash does not move when a name goes, so the row still groups; the
    document's unresolved references are what say three models were there."""
    rows = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())
    assert rows[FORGOTTEN_TOPOLOGY]["forgotten_models"] == 3
    assert rows[BUSY_TOPOLOGY]["forgotten_models"] == 0
    (variant,) = workflow_env.owner.get(
        f"{API}/workflows/{FORGOTTEN_TOPOLOGY}/variants"
    ).json()
    assert variant["forgotten_models"] == 3


def test_a_row_carries_its_ghosts_for_the_filter(workflow_env):
    """Picture ghosts are this library's; model ghosts are names for models
    not on the shelf. Each positive sits beside a row that must read zero."""
    server = workflow_env.server
    record_picture_ghosts(
        server.hub,
        [
            PictureGhost(
                library_uuid=server.vault.library_uuid,
                pixel_sha="sha-binned-ghost",
                instance_hash=_h("binned-instance"),
                structural_hash=BINNED_RECIPE,
                thumbnail=b"thumbnail-bytes",
            ),
            PictureGhost(
                library_uuid=_h("another-library"),
                pixel_sha="sha-elsewhere",
                instance_hash=_h("binned-instance"),
                structural_hash=BUSY_RECIPE_A,
                thumbnail=b"thumbnail-bytes",
            ),
        ],
    )
    rows = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())
    assert rows[BINNED_TOPOLOGY]["ghosts"] == 1
    assert rows[BUSY_TOPOLOGY]["ghosts"] == 0
    assert rows[BUSY_TOPOLOGY]["model_ghosts"] == 1
    assert rows[FORGOTTEN_TOPOLOGY]["model_ghosts"] == 0

    settings = workflow_env.owner.get(f"{API}/server-config/ghost-retention").json()
    assert settings["picture_ghosts"] == 1
    assert settings["model_ghosts"] == 1


def test_forgetting_model_ghosts_keeps_the_workflow_and_the_shelfs_names(
    workflow_env,
):
    """The purge forgets the LoRA the shelf no longer has, keeps the checkpoint
    it does, and the workflow stays one row of two variants."""
    owner = workflow_env.owner
    r = owner.delete(f"{API}/server-config/ghost-retention/model-ghosts")
    assert r.status_code == 200, r.text
    assert r.json()["names_forgotten"] == 1

    row = _by_hash(owner.get(f"{API}/workflows").json())[BUSY_TOPOLOGY]
    assert [a["name"] for a in row["assets"]] == [_SHELF_FILENAME]
    assert row["variants"] == 2
    assert row["model_ghosts"] == 0
    assert row["forgotten_models"] == 1
    assert owner.get(f"{API}/server-config/ghost-retention").json()["model_ghosts"] == 0


def test_model_ghosts_judge_only_what_the_shelf_can_hold(workflow_env):
    """The shelf scans ``.safetensors`` alone, so a ``.pth`` is never on it and
    must never be forgotten as a ghost. A loader digest is judged against the
    shelf's digests: the unknown one goes, the one on the shelf stays."""
    server = workflow_env.server
    on_shelf, unknown = _h("digest-on-shelf"), _h("digest-unknown")
    with server.hub.transaction() as conn:
        conn.executemany(
            "INSERT INTO workflow_recipe_asset "
            "(structural_hash, widget_name, normalized_filename) VALUES (?, ?, ?)",
            [
                (BUSY_RECIPE_A, "model_name", "4x_ultrasharp.pth"),
                (BUSY_RECIPE_A, "lora_sha256", unknown),
                (BUSY_RECIPE_B, "lora_sha256", on_shelf),
                # An unset loader and a blank download digest name no model.
                (BUSY_RECIPE_B, "checkpoint_sha256", ""),
                (BUSY_RECIPE_B, "expected_sha256", "not-a-digest"),
            ],
        )
        conn.execute("DELETE FROM model WHERE sha256 = ?", (on_shelf,))
        conn.execute(
            "INSERT INTO model (file_kind, kind, sha256, provenance) "
            "VALUES ('adapter', 'lora', ?, 'scanned')",
            (on_shelf,),
        )
    try:
        owner = workflow_env.owner
        base = f"{API}/server-config/ghost-retention"
        assert owner.get(base).json()["model_ghosts"] == 2
        # A confirm for a count that is no longer true destroys nothing.
        r = owner.delete(f"{base}/model-ghosts", params={"expected": 1})
        assert r.status_code == 409, r.text
        assert owner.get(base).json()["model_ghosts"] == 2

        r = owner.delete(f"{base}/model-ghosts", params={"expected": 2})
        assert r.status_code == 200, r.text
        assert r.json()["names_forgotten"] == 2
        left = {
            row["normalized_filename"]
            for row in server.hub.fetchall(
                "SELECT normalized_filename FROM workflow_recipe_asset"
            )
        }
        assert {
            "4x_ultrasharp.pth",
            on_shelf,
            _SHELF_FILENAME,
            "",
            "not-a-digest",
        } <= left
        assert not {"add_detail.safetensors", unknown} & left
    finally:
        with server.hub.transaction() as conn:
            conn.execute("DELETE FROM model WHERE sha256 = ?", (on_shelf,))


def test_digests_wait_while_a_shelf_checkpoint_is_unhashed(workflow_env):
    """Until the hash finder reads it, a checkpoint's loader digest matches
    nothing on the shelf, so judging it then would forget a model on disk."""
    server = workflow_env.server
    digest = _h("digest-of-unhashed-checkpoint")
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_recipe_asset "
            "(structural_hash, widget_name, normalized_filename) VALUES (?, ?, ?)",
            (BUSY_RECIPE_B, "checkpoint_sha256", digest),
        )
        conn.execute("DELETE FROM model WHERE filename = 'unhashed.safetensors'")
        conn.execute(
            "INSERT INTO model (file_kind, filename, provenance) "
            "VALUES ('checkpoint', 'unhashed.safetensors', 'scanned')"
        )
    base = f"{API}/server-config/ghost-retention"
    try:
        # Only the seeded LoRA name; the digest is not judged yet.
        assert workflow_env.owner.get(base).json()["model_ghosts"] == 1
    finally:
        with server.hub.transaction() as conn:
            conn.execute("DELETE FROM model WHERE filename = 'unhashed.safetensors'")
    # Positive control: with nothing waiting, the same digest is a ghost.
    assert workflow_env.owner.get(base).json()["model_ghosts"] == 2


def test_a_model_back_on_the_shelf_is_not_a_ghost(workflow_env):
    """A copy's basename counts as much as the recorded filename."""
    server = workflow_env.server
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, filename, provenance) "
            "VALUES ('checkpoint', NULL, 'scanned')"
        )
        model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        folder_id = conn.execute("SELECT id FROM model_folder LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state) "
            "VALUES (?, ?, 'loras/Add_Detail.safetensors', 'present')",
            (model_id, folder_id),
        )
    try:
        r = workflow_env.owner.delete(
            f"{API}/server-config/ghost-retention/model-ghosts"
        )
        assert r.json()["names_forgotten"] == 0
    finally:
        with server.hub.transaction() as conn:
            conn.execute("DELETE FROM model_file WHERE model_id = ?", (model_id,))
            conn.execute("DELETE FROM model WHERE id = ?", (model_id,))


def test_a_row_carries_each_asset_its_variants_name_exactly_once(workflow_env):
    """A LIST comparison, not a set, and that is the point of the test.

    The asset table is keyed per recipe, so two variants naming the same
    checkpoint are two rows. Compared as a set that duplication is invisible —
    and it is not cosmetic: it is what turns the 159-variant family's Models
    cell into 159 copies of one filename and its descriptor into a claim that
    the graph loads 159 adapters at once.
    """
    rows = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())
    assets = rows[BUSY_TOPOLOGY]["assets"]
    assert [(a["widget"], a["name"]) for a in assets] == [
        ("ckpt_name", "realvisxl.safetensors"),
        ("lora_name", "add_detail.safetensors"),
    ]


def test_adapter_slots_count_one_run_not_the_names_across_variants(workflow_env):
    """What one run loads, which the set of names cannot answer.

    Both of BUSY's recipes name the same checkpoint; only one names an adapter.
    A topology is the graph alone, so its adapter slots are a property every
    recipe under it shares — one here, however many files the family has been
    bound to over its life.
    """
    rows = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())
    assert rows[BUSY_TOPOLOGY]["adapter_slots"] == 1
    assert rows[FORGOTTEN_TOPOLOGY]["adapter_slots"] == 0


def test_the_scan_block_says_which_empty_state_the_list_is_in(workflow_env):
    """The list cannot tell "not looked yet" from "looked, and nothing" on its
    own, and three of the four states a new user meets are exactly that.

    The two figures must **differ** here, or the assertion says nothing: with
    every picture scanned, counting any column at all gives the same answer and
    the one distinction this block exists to draw goes untested.
    """
    scan = workflow_env.owner.get(f"{API}/workflows").json()["scan"]
    # Six kept pictures, one of them not yet read. The binned picture is in
    # neither figure: it is not kept.
    assert scan["pictures"] == 6
    assert scan["scanned"] == 5


# ===========================================================================
# The variants are the row's expansion
# ===========================================================================


def test_variants_add_up_to_the_row_above_them(workflow_env):
    variants = workflow_env.owner.get(
        f"{API}/workflows/{BUSY_TOPOLOGY}/variants"
    ).json()
    by_hash = {row["structural_hash"]: row for row in variants}
    assert set(by_hash) == {BUSY_RECIPE_A, BUSY_RECIPE_B}
    assert by_hash[BUSY_RECIPE_A]["pictures"] == 2
    assert by_hash[BUSY_RECIPE_B]["pictures"] == 1
    row = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())[BUSY_TOPOLOGY]
    assert sum(v["pictures"] for v in variants) == row["pictures"]


def test_a_variant_carries_only_its_own_assets(workflow_env):
    """The LoRA belongs to one of the two recipes; the expansion is where that
    difference becomes visible, and it is the reason variants exist at all."""
    variants = workflow_env.owner.get(
        f"{API}/workflows/{BUSY_TOPOLOGY}/variants"
    ).json()
    by_hash = {row["structural_hash"]: row for row in variants}
    assert {a["name"] for a in by_hash[BUSY_RECIPE_A]["assets"]} == {
        "realvisxl.safetensors",
        "add_detail.safetensors",
    }
    assert {a["name"] for a in by_hash[BUSY_RECIPE_B]["assets"]} == {
        "realvisxl.safetensors"
    }


def test_an_unknown_topology_is_a_404_not_an_empty_list(workflow_env):
    """A hash from another machine is "this machine does not have it", which an
    empty 200 would render as "this workflow has no variants"."""
    r = workflow_env.owner.get(f"{API}/workflows/{_h('nosuchtopology')}/variants")
    assert r.status_code == 404, r.text


def test_a_malformed_hash_is_refused_by_name(workflow_env):
    r = workflow_env.owner.get(f"{API}/workflows/not-a-hash/variants")
    assert r.status_code == 422, r.text
    assert "topology_hash" in r.text


# ===========================================================================
# The rail's tiles, and the graph
# ===========================================================================


def test_picture_ids_are_newest_first_and_exclude_the_scrapheap(workflow_env):
    """Named for the order, so the order is what is asserted.

    The rail draws six tiles out of a workflow that may have a thousand
    pictures, so which six is the whole of the choice; a test that only counted
    them would pass with the sort reversed.
    """
    ids = workflow_env.owner.get(f"{API}/workflows/{BUSY_TOPOLOGY}/pictures").json()
    assert len(ids) == 3
    dated = {
        row["file_path"]: row["id"]
        for row in workflow_env.owner.get(f"{API}/pictures", params={"id": ids}).json()
    }
    # busy_three (2026-08-12) is the newest of the three, busy_one the oldest.
    assert ids[0] == dated["busy_three.png"]
    assert ids[-1] == dated["busy_one.png"]

    binned = workflow_env.owner.get(
        f"{API}/workflows/{BINNED_TOPOLOGY}/pictures"
    ).json()
    assert binned == []


def test_the_tile_limit_is_the_routes_to_set_not_the_callers(workflow_env):
    """A tile strip must not be turnable into a library dump by editing a URL."""
    r = workflow_env.owner.get(
        f"{API}/workflows/{BUSY_TOPOLOGY}/pictures", params={"limit": 100000}
    )
    assert r.status_code == 422, r.text


def test_a_recipe_serves_its_stored_graph_and_says_it_will_not_run(workflow_env):
    """The stored document is prompt-free and parameter-free by construction, so
    it describes the workflow and cannot be handed back to ComfyUI. The payload
    has to say so; a caller discovering it by feeding this to ComfyUI is the
    defect §B5 exists to close."""
    r = workflow_env.owner.get(f"{API}/workflows/recipes/{BUSY_RECIPE_A}/graph")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["document"] == _DOCUMENTS[BUSY_RECIPE_A]
    assert body["runnable"] is False


def test_an_unknown_recipe_is_a_404(workflow_env):
    r = workflow_env.owner.get(f"{API}/workflows/recipes/{_h('nosuchrecipe')}/graph")
    assert r.status_code == 404, r.text


# ===========================================================================
# Hardening (#1293): the rollback belt, transport, and the ghost routes
# ===========================================================================

_TEMPLATED_PATHS = (
    f"{API}/workflows/{BUSY_TOPOLOGY}/variants",
    f"{API}/workflows/{BUSY_TOPOLOGY}/pictures",
    f"{API}/workflows/recipes/{BUSY_RECIPE_A}/graph",
)


def test_the_templated_reads_stay_closed_with_the_gate_rolled_back(workflow_env):
    """``AUTHZ_GATE_ENFORCING = False`` is a documented rollback, and the belt
    that survives it used to match literal paths only, so these three answered
    a share token. ``READ_BLOCKED_GET_PREFIXES`` is what refuses them now."""
    server = workflow_env.server
    unscoped = _bearer(server, _mint(workflow_env.owner, "rollback unscoped"))
    scoped = _bearer(
        server,
        _mint(
            workflow_env.owner,
            "rollback scoped",
            resource_type="character",
            resource_id=workflow_env.character_id,
        ),
    )
    previously_enforcing = server.authz._enforcing
    server.authz._enforcing = False
    try:
        for client in (unscoped, scoped):
            assert client.get(f"{API}/pictures").status_code == 200, (
                "the token is dead; the refusals below would prove nothing"
            )
            for path in _TEMPLATED_PATHS:
                assert_real_route(server.api, "GET", path)
                r = client.get(path)
                assert r.status_code == 403, f"GET {path}: {r.status_code} {r.text}"
        for path in _TEMPLATED_PATHS:
            r = workflow_env.owner.get(path)
            assert r.status_code == 200, f"owner GET {path}: {r.status_code} {r.text}"
    finally:
        server.authz._enforcing = previously_enforcing


def test_the_workflow_reads_refuse_remote_plaintext_under_require_ssl(
    workflow_env, monkeypatch
):
    """The same transport rule as the model-shelf reads naming the same files."""
    server = workflow_env.server
    monkeypatch.setitem(server.auth._server_config, "require_ssl", True)
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "8.8.8.8")
    for path in (f"{API}/workflows", *_TEMPLATED_PATHS):
        r = workflow_env.owner.get(path)
        assert r.status_code == 403 and "HTTPS is required" in r.text, (
            f"GET {path}: {r.status_code} {r.text}"
        )


def _ghost(server, pixel_sha: str, instance_hash: str) -> PictureGhost:
    return PictureGhost(
        library_uuid=server.vault.library_uuid,
        pixel_sha=pixel_sha,
        instance_hash=instance_hash,
        thumbnail=b"thumbnail-bytes",
    )


def _ghost_shas(server) -> set[str]:
    return {
        row["pixel_sha"]
        for row in server.hub.fetchall("SELECT pixel_sha FROM workflow_picture_ghost")
    }


def test_the_ghost_routes_are_the_owners_alone(workflow_env, monkeypatch):
    """Both directions on GET, PATCH and the erase, with the gate enforcing.

    The GET belts are emptied so the refusal is measured at the gate rather
    than at the middleware in front of it; PATCH and DELETE are refused to a
    READ token before routing either way.
    """
    server = workflow_env.server
    monkeypatch.setattr(auth, "READ_BLOCKED_GET_PATHS", frozenset())
    monkeypatch.setattr(auth, "READ_BLOCKED_GET_PREFIXES", ())
    record_picture_ghosts(server.hub, [_ghost(server, "sha-authz", _h("authz"))])
    tokens = {
        "unscoped": _mint(workflow_env.owner, "ghost unscoped"),
        "scoped": _mint(
            workflow_env.owner,
            "ghost scoped",
            resource_type="character",
            resource_id=workflow_env.character_id,
        ),
    }
    record_picture_ghosts(
        server.hub,
        [
            PictureGhost(
                library_uuid=_h("another-library"),
                pixel_sha="sha-other-library",
                instance_hash=_h("authz"),
                thumbnail=b"thumbnail-bytes",
            )
        ],
    )
    base = f"{API}/server-config/ghost-retention"
    previously_enforcing = server.authz._enforcing
    server.authz._enforcing = True
    try:
        for label, token in tokens.items():
            client = _bearer(server, token)
            assert client.get(f"{API}/pictures").status_code == 200, label
            r = client.get(base)
            assert r.status_code == 403, f"{label} GET: {r.status_code} {r.text}"
            assert "Owner-level" in r.text, f"{label} GET not refused by the gate"
            r = client.patch(base, json={"workflow_ghost_retention": "on"})
            assert r.status_code == 403, f"{label} PATCH: {r.status_code} {r.text}"
            r = client.delete(f"{base}/ghosts")
            assert r.status_code == 403, f"{label} DELETE: {r.status_code} {r.text}"
            assert_real_route(server.api, "DELETE", f"{base}/model-ghosts")
            r = client.delete(f"{base}/model-ghosts")
            assert r.status_code == 403, f"{label} forget: {r.status_code} {r.text}"
        assert _ghost_shas(server) == {"sha-authz", "sha-other-library"}
        assert server.hub.fetchone(
            "SELECT 1 FROM workflow_recipe_asset "
            "WHERE normalized_filename = 'add_detail.safetensors'"
        )
        assert server.vault.ghost_retention == "covered"

        owner = workflow_env.owner
        assert owner.get(base).status_code == 200
        r = owner.patch(base, json={"workflow_ghost_retention": "covered"})
        assert r.status_code == 200, r.text
        r = owner.delete(f"{base}/ghosts")
        assert r.status_code == 200, r.text
        assert r.json()["ghosts_erased"] == 1
        # The erase is the active library's: another library's ghost stays.
        assert _ghost_shas(server) == {"sha-other-library"}
        r = owner.delete(f"{base}/model-ghosts")
        assert r.status_code == 200, r.text
        assert r.json()["names_forgotten"] == 1
    finally:
        server.authz._enforcing = previously_enforcing


def test_removing_a_reference_folder_cascades_its_uncovered_ghosts(workflow_env):
    """The reported repro: a folder removal left an uncovered ghost behind.

    Two ghosts, two instance hashes. The folder held the only picture carrying
    one of them and one of two carrying the other, so exactly one ghost loses
    its cover. The other staying is the positive control: a cascade that fired
    on every hash the folder touched would pass the first assertion alone.
    """
    server = workflow_env.server
    lost, kept = _h("folder-lost-instance"), _h("folder-kept-instance")
    with tempfile.TemporaryDirectory() as folder_dir:

        def insert(session):
            folder = ReferenceFolder(folder=folder_dir, label="refs", status="active")
            session.add(folder)
            session.commit()
            session.refresh(folder)
            for name, instance in (("lost.png", lost), ("kept.png", kept)):
                session.add(
                    Picture(
                        file_path=f"{folder_dir}/{name}",
                        reference_folder_id=folder.id,
                        workflow_instance_hash=instance,
                    )
                )
            session.add(Picture(file_path="cover.png", workflow_instance_hash=kept))
            session.commit()
            return folder.id

        folder_id = server.vault.db.run_task(insert)
        record_picture_ghosts(
            server.hub,
            [_ghost(server, "sha-lost", lost), _ghost(server, "sha-kept", kept)],
        )

        r = workflow_env.owner.delete(f"{API}/reference-folders/{folder_id}")
        assert r.status_code == 200, r.text

    result = GhostCascadeTask(vault=server.vault)._run_task()
    assert result["destroyed"] == 1, result
    assert _ghost_shas(server) == {"sha-kept"}


def test_a_hub_attached_vault_registers_the_ghost_cascade(workflow_env):
    """Every cascade test above drives the task directly; this is what makes the
    planner run it at all. The module detached the finders, so they are read
    back from the planner's record of what it detached."""
    assert "GhostCascadeFinder" in workflow_env.detached


# ===========================================================================
# How each picture input is filled (#1305)
# ===========================================================================

_INPUT_ROUTES = (
    ("GET", "/api/v1/comfyui/workflows/{workflow_name}/inputs"),
    ("PUT", "/api/v1/comfyui/workflows/{workflow_name}/inputs"),
)


def _isolate_workflow_folders(tmp_path, monkeypatch) -> None:
    """Point every workflow folder a route touches at *tmp_path*, and fake the trash.

    Deleting a workflow writes it back to the inbox and sends it to the system
    trash. Both are shared by every checkout on the machine, and a PixlStash
    running against the same data folder would import what lands in the inbox.
    """
    inbox = tmp_path / "inbox"
    # exist_ok: two workflow fixtures can share one test's tmp_path, which is
    # how a test gets a workflow with a LoRA loader and one without at once.
    inbox.mkdir(exist_ok=True)

    def fake_trash(path):
        os.remove(path)

    monkeypatch.setattr(comfyui_module, "_workflow_dirs", lambda: [("user", tmp_path)])
    monkeypatch.setattr(comfyui_module, "workflow_user_dir", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_inbox, "workflow_inbox_dir", lambda: str(inbox))
    monkeypatch.setattr(workflow_inbox, "send2trash", fake_trash)
    monkeypatch.setattr(comfyui_module, "send2trash", fake_trash)
    comfyui_module._describe_workflow.cache_clear()


@pytest.fixture
def edit_workflow(tmp_path, monkeypatch):
    """One two-input workflow file in a user folder of its own.

    The real user folder is shared by every checkout on the machine, so the
    routes are pointed at a temporary one rather than written into it.
    """
    # Stored the way the placeholder migration leaves a dialog-bound file: the
    # old token sat on the SECOND input, so a default that just took the lowest
    # node id would show here.
    graph, _changed = workflow_bindings.migrate_placeholders(
        {
            "1": {"class_type": "LoadImage", "inputs": {"image": "Logo.png"}},
            "2": {
                "class_type": "LoadImage",
                "inputs": {"image": "{{image_path}}"},
                "_meta": {"title": "Reference"},
            },
            "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        }
    )
    (tmp_path / "edit.json").write_text(json.dumps(graph), encoding="utf-8")
    _isolate_workflow_folders(tmp_path, monkeypatch)
    return f"{API}/comfyui/workflows/edit.json/inputs"


def _picture_with_sha(server, file_path: str, pixel_sha: str) -> int:
    def write(session):
        picture = session.exec(
            select(Picture).where(Picture.file_path == file_path)
        ).one()
        picture.pixel_sha = pixel_sha
        session.add(picture)
        session.commit()
        return picture.id

    return server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


def _by_node(body) -> dict:
    return {item["node_id"]: item for item in body["inputs"]}


def test_the_input_routes_are_declared_owner_only():
    for key in _INPUT_ROUTES:
        assert ROUTE_POLICIES[key].policy is AccessPolicy.OWNER_ONLY, key


def test_no_scoped_token_can_read_or_set_a_workflows_inputs(
    workflow_env, edit_workflow
):
    """The setup names Fixed pictures by id, so a share token gets neither half.

    The belts are emptied, and the token's scope is let through the non-GET
    refusal, so both halves are refused by the gate's declaration rather than
    by the middleware in front of it: "Owner-level" is the gate's own string.
    """
    server = workflow_env.server
    token = _mint(
        workflow_env.owner,
        "inputs scope probe",
        resource_type="character",
        resource_id=workflow_env.character_id,
    )
    client = _bearer(server, token)
    assert client.get(f"{API}/pictures").status_code == 200, "the token is dead"
    assert_real_route(server.api, "GET", edit_workflow)
    assert_real_route(server.api, "PUT", edit_workflow)
    body = {
        "inputs": [
            {"node_id": "1", "mode": "picker"},
            {"node_id": "2", "mode": "picker"},
        ]
    }
    previously_enforcing = server.authz._enforcing
    server.authz._enforcing = True
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(auth, "READ_BLOCKED_GET_PATHS", frozenset())
            patch.setattr(auth, "READ_BLOCKED_GET_PREFIXES", ())
            patch.setattr(auth, "WRITE_ENABLED_SCOPES", frozenset({"READ", "WRITE"}))
            r = client.get(edit_workflow)
            assert r.status_code == 403 and "Owner-level" in r.text, r.text
            r = client.put(edit_workflow, json=body)
            assert r.status_code == 403 and "Owner-level" in r.text, r.text
        assert client.put(edit_workflow, json=body).status_code == 403
        # The refusal wrote nothing: the owner still reads the defaults.
        owner_view = _by_node(workflow_env.owner.get(edit_workflow).json())
        assert owner_view["2"]["mode"] == "selection"
    finally:
        server.authz._enforcing = previously_enforcing


def test_an_unconfigured_workflow_reads_its_defaults_with_titles(
    workflow_env, edit_workflow
):
    r = workflow_env.owner.get(edit_workflow)
    assert r.status_code == 200, r.text
    assert r.json()["inputs"] == [
        {
            "node_id": "1",
            "title": "LoadImage",
            "mode": "picker",
            "picture_id": None,
            "picture_missing": False,
        },
        {
            "node_id": "2",
            "title": "Reference",
            "mode": "selection",
            "picture_id": None,
            "picture_missing": False,
        },
    ]


def test_a_fixed_picture_is_kept_by_content_and_leaves_the_selection_pill(
    workflow_env, edit_workflow
):
    server, owner = workflow_env.server, workflow_env.owner
    picture_id = _picture_with_sha(server, "busy_one.png", "sha-fixed-reference")
    r = owner.put(
        edit_workflow,
        json={
            "inputs": [
                {"node_id": "1", "mode": "picker"},
                {"node_id": "2", "mode": "fixed", "picture_id": picture_id},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _by_node(r.json())["2"]["picture_id"] == picture_id
    assert (
        server.hub.fetchone(
            "SELECT pixel_sha FROM workflow_picture_input WHERE node_id = '2'"
        )["pixel_sha"]
        == "sha-fixed-reference"
    )
    listed = {
        item["name"]: item
        for item in owner.get(f"{API}/comfyui/workflows").json()["workflows"]
    }
    assert listed["edit.json"]["has_selection_input"] is False

    # Moving the picture to the Scrapheap leaves the input saying so.
    def bin_it(session):
        picture = session.get(Picture, picture_id)
        picture.deleted = True
        session.add(picture)
        session.commit()

    server.vault.db.run_task(bin_it, priority=DBPriority.IMMEDIATE)
    fixed = _by_node(owner.get(edit_workflow).json())["2"]
    assert (fixed["picture_id"], fixed["picture_missing"]) == (None, True)

    # Changing the other input keeps the Fixed one's picture, missing or not.
    r = owner.put(
        edit_workflow,
        json={
            "inputs": [
                {"node_id": "1", "mode": "selection"},
                {"node_id": "2", "mode": "fixed"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert (
        server.hub.fetchone(
            "SELECT pixel_sha FROM workflow_picture_input WHERE node_id = '2'"
        )["pixel_sha"]
        == "sha-fixed-reference"
    )


def test_leaving_fixed_keeps_the_picture_for_coming_back(workflow_env, edit_workflow):
    """One arrow key steps an input off Fixed; it must not cost the picture."""
    server, owner = workflow_env.server, workflow_env.owner
    picture_id = _picture_with_sha(server, "busy_two.png", "sha-kept-reference")

    def put(mode_2, **extra):
        r = owner.put(
            edit_workflow,
            json={
                "inputs": [
                    {"node_id": "1", "mode": "selection"},
                    {"node_id": "2", "mode": mode_2, **extra},
                ]
            },
        )
        assert r.status_code == 200, r.text
        return _by_node(r.json())["2"]

    put("fixed", picture_id=picture_id)
    stepped_off = put("picker")
    assert (stepped_off["mode"], stepped_off["picture_id"]) == ("picker", picture_id)
    back = put("fixed")
    assert (back["mode"], back["picture_id"], back["picture_missing"]) == (
        "fixed",
        picture_id,
        False,
    )


def test_an_unhashed_picture_is_a_409_and_a_duplicate_resolves_to_its_oldest_copy(
    workflow_env, edit_workflow
):
    server, owner = workflow_env.server, workflow_env.owner

    def ids_by_path(session):
        return {p.file_path: p.id for p in session.exec(select(Picture)).all()}

    ids = server.vault.db.run_immediate_read_task(ids_by_path)
    body = {
        "inputs": [
            {"node_id": "1", "mode": "selection"},
            {"node_id": "2", "mode": "fixed", "picture_id": ids["busy_one.png"]},
        ]
    }
    # Seeded pictures carry no pixel_sha: there is nothing to name one by.
    r = owner.put(edit_workflow, json=body)
    assert r.status_code == 409, r.text

    older = _picture_with_sha(server, "busy_one.png", "sha-duplicate")
    newer = _picture_with_sha(server, "busy_three.png", "sha-duplicate")
    assert older < newer
    body["inputs"][1]["picture_id"] = newer
    r = owner.put(edit_workflow, json=body)
    assert r.status_code == 200, r.text
    assert _by_node(r.json())["2"]["picture_id"] == older


def test_a_bad_setup_writes_nothing(workflow_env, edit_workflow):
    owner = workflow_env.owner
    two_selections = {
        "inputs": [
            {"node_id": "1", "mode": "selection"},
            {"node_id": "2", "mode": "selection"},
        ]
    }
    assert owner.put(edit_workflow, json=two_selections).status_code == 400
    unknown_picture = {
        "inputs": [
            {"node_id": "1", "mode": "selection"},
            {"node_id": "2", "mode": "fixed", "picture_id": 987654},
        ]
    }
    assert owner.put(edit_workflow, json=unknown_picture).status_code == 404
    # A Fixed input with no picture of its own to keep.
    no_picture = {
        "inputs": [
            {"node_id": "1", "mode": "selection"},
            {"node_id": "2", "mode": "fixed"},
        ]
    }
    assert owner.put(edit_workflow, json=no_picture).status_code == 400
    assert (
        workflow_env.server.hub.fetchone(
            "SELECT COUNT(*) AS n FROM workflow_picture_input"
        )["n"]
        == 0
    )
    missing = f"{API}/comfyui/workflows/nosuch.json/inputs"
    assert owner.get(missing).status_code == 404


def test_deleting_a_workflow_forgets_its_setup(workflow_env, edit_workflow):
    owner = workflow_env.owner
    r = owner.put(
        edit_workflow,
        json={
            "inputs": [
                {"node_id": "1", "mode": "picker"},
                {"node_id": "2", "mode": "selection"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    r = owner.delete(f"{API}/comfyui/workflows/edit.json")
    assert r.status_code == 200, r.text
    assert (
        workflow_env.server.hub.fetchone(
            "SELECT COUNT(*) AS n FROM workflow_picture_input"
        )["n"]
        == 0
    )


# ===========================================================================
# A workflow's parameters and pins (#1306)
# ===========================================================================

_PARAMETER_ROUTES = (
    ("GET", "/api/v1/comfyui/workflows/{workflow_name}/parameters"),
    ("PUT", "/api/v1/comfyui/workflows/{workflow_name}/pins"),
)

_SAMPLER_INFO = {
    "KSampler": {
        "input": {
            "required": {
                "seed": [
                    "INT",
                    {"min": 0, "max": 2**64 - 1, "control_after_generate": True},
                ],
                "steps": ["INT", {"min": 1, "max": 150}],
                "sampler_name": [["euler", "dpmpp_2m"], {}],
            }
        }
    },
}


@pytest.fixture
def sampler_workflow(tmp_path, monkeypatch):
    """One API-format workflow with a prompt a run fills, in its own user folder.

    ComfyUI is replaced by ``object_info``: set ``.info`` to a map, or to an
    exception for an unreachable ComfyUI.
    """
    graph = {
        "1": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 2**64 - 2,
                "steps": 20,
                "sampler_name": "euler",
                "positive": ["2", 0],
            },
        },
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }
    (tmp_path / "sampler.json").write_text(json.dumps(graph), encoding="utf-8")
    (tmp_path / "canvas.json").write_text(
        json.dumps({"nodes": [{"id": 1, "type": "KSampler"}], "links": []}),
        encoding="utf-8",
    )
    _isolate_workflow_folders(tmp_path, monkeypatch)
    comfy = SimpleNamespace(info=_SAMPLER_INFO, asked=0)

    def fake_object_info(_url):
        comfy.asked += 1
        if isinstance(comfy.info, Exception):
            raise comfy.info
        return comfy.info

    monkeypatch.setattr(comfyui_module, "fetch_object_info", fake_object_info)
    comfy.parameters = f"{API}/comfyui/workflows/sampler.json/parameters"
    comfy.pins = f"{API}/comfyui/workflows/sampler.json/pins"
    return comfy


def _parameter(body, node_id: str, name: str) -> dict:
    return next(
        p for p in body["parameters"] if (p["node_id"], p["name"]) == (node_id, name)
    )


def _pinned(body) -> list:
    return [(p["node_id"], p["name"]) for p in body["parameters"] if p["pinned"]]


def test_the_parameter_routes_are_declared_owner_only():
    for key in _PARAMETER_ROUTES:
        assert ROUTE_POLICIES[key].policy is AccessPolicy.OWNER_ONLY, key


def test_no_scoped_token_can_read_parameters_or_set_pins(
    workflow_env, sampler_workflow
):
    """Refused by the gate's declaration, not the middleware in front of it."""
    server = workflow_env.server
    token = _mint(
        workflow_env.owner,
        "parameters scope probe",
        resource_type="character",
        resource_id=workflow_env.character_id,
    )
    client = _bearer(server, token)
    assert client.get(f"{API}/pictures").status_code == 200, "the token is dead"
    assert_real_route(server.api, "GET", sampler_workflow.parameters)
    assert_real_route(server.api, "PUT", sampler_workflow.pins)
    body = {"pins": [{"node_id": "1", "name": "steps"}]}
    previously_enforcing = server.authz._enforcing
    server.authz._enforcing = True
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(auth, "READ_BLOCKED_GET_PATHS", frozenset())
            patch.setattr(auth, "READ_BLOCKED_GET_PREFIXES", ())
            patch.setattr(auth, "WRITE_ENABLED_SCOPES", frozenset({"READ", "WRITE"}))
            r = client.get(sampler_workflow.parameters)
            assert r.status_code == 403 and "Owner-level" in r.text, r.text
            r = client.put(sampler_workflow.pins, json=body)
            assert r.status_code == 403 and "Owner-level" in r.text, r.text
        # Refused before ComfyUI was asked, and nothing was stored.
        assert sampler_workflow.asked == 0
        owner_view = workflow_env.owner.get(sampler_workflow.parameters).json()
        assert owner_view["pins_saved"] is False
    finally:
        server.authz._enforcing = previously_enforcing


def test_parameters_are_typed_from_comfyui_and_leave_the_prompt_out(
    workflow_env, sampler_workflow
):
    r = workflow_env.owner.get(sampler_workflow.parameters)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["readable"], body["typed"], body["comfyui_error"]) == (
        True,
        True,
        None,
    )
    steps = _parameter(body, "1", "steps")
    assert (steps["kind"], steps["value"], steps["min"], steps["max"]) == (
        "int",
        20,
        1,
        150,
    )
    assert _parameter(body, "1", "sampler_name")["options"] == ["euler", "dpmpp_2m"]
    assert _parameter(body, "1", "seed")["kind"] == "seed"
    names = {(p["node_id"], p["name"]) for p in body["parameters"]}
    assert ("2", "text") not in names and ("1", "positive") not in names
    assert body["pins_saved"] is False
    assert _pinned(body) == [("1", "seed"), ("1", "steps"), ("1", "sampler_name")]


def test_an_unreachable_comfyui_still_shows_the_recorded_values(
    workflow_env, sampler_workflow
):
    """The values survive the outage, and the exception's own text does not.

    ``comfyui_error`` is composed by the route, not taken from the exception:
    whatever the fetch failed on - a chained error, an internal address, a
    token echoed back by a proxy - belongs in the log, not in a response.
    """
    sampler_workflow.info = RuntimeError(
        "Could not reach ComfyUI at http://127.0.0.1:8188 (example-secret)"
    )
    r = workflow_env.owner.get(sampler_workflow.parameters)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["typed"] is False
    assert body["comfyui_error"].startswith("Could not read ComfyUI's node types at")
    assert "example-secret" not in body["comfyui_error"]
    steps = _parameter(body, "1", "steps")
    assert (steps["value"], steps["min"], steps["max"]) == (20, None, None)
    assert _parameter(body, "1", "sampler_name")["options"] is None


def test_a_ui_format_file_is_unreadable_without_asking_comfyui(
    workflow_env, sampler_workflow
):
    r = workflow_env.owner.get(f"{API}/comfyui/workflows/canvas.json/parameters")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["readable"], body["parameters"]) == (False, [])
    # Nothing was asked, so nothing is reported offline.
    assert (body["typed"], body["comfyui_error"]) == (False, None)
    assert sampler_workflow.asked == 0


def test_a_ui_file_the_reduction_refuses_is_still_just_unreadable(
    workflow_env, sampler_workflow, tmp_path
):
    """A subgraph instance with no definition makes detection raise; the form
    does not need detection for a file it cannot read anyway."""
    canvas = {
        "nodes": [{"id": 1, "type": "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"}],
        "links": [],
        "definitions": {"subgraphs": []},
    }
    with pytest.raises(WorkflowGraphError):
        detect_workflow_io(canvas)
    (tmp_path / "orphan.json").write_text(json.dumps(canvas), encoding="utf-8")
    r = workflow_env.owner.get(f"{API}/comfyui/workflows/orphan.json/parameters")
    assert r.status_code == 200, r.text
    assert r.json()["readable"] is False


def test_a_workflow_with_nothing_to_set_does_not_ask_comfyui(
    workflow_env, sampler_workflow, tmp_path
):
    graph = {"1": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}}}
    (tmp_path / "bare.json").write_text(json.dumps(graph), encoding="utf-8")
    r = workflow_env.owner.get(f"{API}/comfyui/workflows/bare.json/parameters")
    assert r.status_code == 200, r.text
    assert (r.json()["parameters"], r.json()["typed"]) == ([], False)
    assert sampler_workflow.asked == 0


def test_pins_are_kept_in_order_and_null_restores_the_defaults(
    workflow_env, sampler_workflow
):
    owner = workflow_env.owner
    order = [
        {"node_id": "1", "name": "sampler_name"},
        {"node_id": "1", "name": "steps"},
    ]
    r = owner.put(sampler_workflow.pins, json={"pins": order})
    assert r.status_code == 200, r.text
    assert r.json() == {"workflow": "sampler.json", "pins_saved": True, "pins": order}
    # Saving does not wait on ComfyUI.
    assert sampler_workflow.asked == 0
    body = owner.get(sampler_workflow.parameters).json()
    assert (body["pins_saved"], body["pins"]) == (True, order)
    assert _pinned(body) == [("1", "steps"), ("1", "sampler_name")]

    r = owner.put(sampler_workflow.pins, json={"pins": []})
    assert r.json()["pins"] == [] and r.json()["pins_saved"] is True
    body = owner.get(sampler_workflow.parameters).json()
    assert (body["pins"], _pinned(body)) == ([], [])

    r = owner.put(sampler_workflow.pins, json={"pins": None})
    assert (r.json()["pins_saved"], r.json()["pins"]) == (False, None)
    body = owner.get(sampler_workflow.parameters).json()
    assert [(p["node_id"], p["name"]) for p in body["pins"]] == [
        ("1", "seed"),
        ("1", "steps"),
        ("1", "sampler_name"),
    ]


def test_a_seed_beyond_float_precision_is_returned_exactly(
    workflow_env, sampler_workflow
):
    body = workflow_env.owner.get(sampler_workflow.parameters).json()
    seed = _parameter(body, "1", "seed")
    assert (seed["value"], seed["max"]) == (2**64 - 2, 2**64 - 1)


def test_a_bad_pin_writes_nothing(workflow_env, sampler_workflow):
    owner = workflow_env.owner
    for body in (
        {"pins": [{"node_id": "2", "name": "text"}]},
        {"pins": [{"node_id": "1"}]},
        {"pins": "steps"},
        {},
    ):
        assert owner.put(sampler_workflow.pins, json=body).status_code == 400, body
    assert (
        workflow_env.server.hub.fetchone(
            "SELECT COUNT(*) AS n FROM workflow_parameter_pins"
        )["n"]
        == 0
    )
    missing = f"{API}/comfyui/workflows/nosuch.json/parameters"
    assert owner.get(missing).status_code == 404


def test_deleting_a_workflow_forgets_its_pins(workflow_env, sampler_workflow):
    owner = workflow_env.owner
    r = owner.put(
        sampler_workflow.pins, json={"pins": [{"node_id": "1", "name": "steps"}]}
    )
    assert r.status_code == 200, r.text
    r = owner.delete(f"{API}/comfyui/workflows/sampler.json")
    assert r.status_code == 200, r.text
    assert (
        workflow_env.server.hub.fetchone(
            "SELECT COUNT(*) AS n FROM workflow_parameter_pins"
        )["n"]
        == 0
    )


# ===========================================================================
# Running a workflow (#1307)
# ===========================================================================

_RUN_ROUTE = ("POST", "/api/v1/comfyui/workflows/{workflow_name}/run")


@pytest.fixture
def fake_comfyui(tmp_path, monkeypatch):
    """ComfyUI replaced by recorders, and every picture given a file to upload.

    ``uploads`` holds ``(file, upload_name)``, ``submitted`` each graph sent and
    ``collected`` the arguments each output import started with.
    """
    comfy = SimpleNamespace(uploads=[], submitted=[], collected=[])
    files = tmp_path / "pictures"
    files.mkdir()

    def resolve(_root, file_path):
        path = files / file_path
        path.write_bytes(b"not really a png")
        return str(path)

    def upload(_url, file_path, upload_name=None):
        comfy.uploads.append((file_path.rsplit("/", 1)[-1], upload_name))
        return upload_name

    def submit(_url, workflow, _client_id=None):
        comfy.submitted.append(json.loads(json.dumps(workflow)))
        return {"prompt_id": f"prompt-{len(comfy.submitted)}"}

    def collect(*args, **kwargs):
        comfy.collected.append((args[3:], kwargs.get("view_context")))

    monkeypatch.setattr(
        comfyui_module.ImageUtils, "resolve_picture_path", staticmethod(resolve)
    )
    monkeypatch.setattr(comfyui_module, "_upload_image_to_comfyui", upload)
    monkeypatch.setattr(comfyui_module, "_submit_comfyui_prompt", submit)
    monkeypatch.setattr(comfyui_module, "_process_comfyui_outputs", collect)
    return comfy


def _picture_ids(server) -> dict:
    def ids_by_path(session):
        return {p.file_path: p.id for p in session.exec(select(Picture)).all()}

    return server.vault.db.run_immediate_read_task(ids_by_path)


def _run(owner, name: str, **body):
    return owner.post(f"{API}/comfyui/workflows/{name}/run", json=body)


def _wait_for_collection(comfy, count: int) -> None:
    deadline = time.monotonic() + 5.0
    while len(comfy.collected) < count and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(comfy.collected) == count, comfy.collected


def test_the_run_route_is_declared_owner_only_and_refuses_a_scoped_token(
    workflow_env, edit_workflow, fake_comfyui
):
    assert ROUTE_POLICIES[_RUN_ROUTE].policy is AccessPolicy.OWNER_ONLY
    server = workflow_env.server
    token = _mint(
        workflow_env.owner,
        "run scope probe",
        resource_type="character",
        resource_id=workflow_env.character_id,
    )
    client = _bearer(server, token)
    assert client.get(f"{API}/pictures").status_code == 200, "the token is dead"
    url = f"{API}/comfyui/workflows/edit.json/run"
    assert_real_route(server.api, "POST", url)
    ids = _picture_ids(server)
    body = {
        "picture_ids": [ids["busy_one.png"]],
        "pictures": [{"node_id": "1", "picture_id": ids["busy_two.png"]}],
        "stack": False,
    }
    previously_enforcing = server.authz._enforcing
    server.authz._enforcing = True
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(auth, "WRITE_ENABLED_SCOPES", frozenset({"READ", "WRITE"}))
            r = client.post(url, json=body)
            assert r.status_code == 403 and "Owner-level" in r.text, r.text
        assert fake_comfyui.submitted == []
        # The in-scope positive control: the owner runs the same body.
        r = workflow_env.owner.post(url, json=body)
        assert r.status_code == 200, r.text
    finally:
        server.authz._enforcing = previously_enforcing


def test_a_selection_runs_once_per_picture_with_the_picker_in_every_run(
    workflow_env, edit_workflow, fake_comfyui
):
    ids = _picture_ids(workflow_env.server)
    selected = [ids["busy_one.png"], ids["busy_three.png"]]
    picked = ids["busy_two.png"]
    r = _run(
        workflow_env.owner,
        "edit.json",
        picture_ids=selected,
        pictures=[{"node_id": "1", "picture_id": picked}],
        stack=False,
    )
    assert r.status_code == 200, r.text
    assert [p["picture_id"] for p in r.json()["prompts"]] == selected
    # Node 2 holds the old binding, so it is the Selection input by default.
    assert [g["2"]["inputs"]["image"] for g in fake_comfyui.submitted] == [
        f"pixlstash-{selected[0]}-unhashed.png",
        f"pixlstash-{selected[1]}-unhashed.png",
    ]
    assert {g["1"]["inputs"]["image"] for g in fake_comfyui.submitted} == {
        f"pixlstash-{picked}-unhashed.png"
    }
    # The picker's picture is uploaded once, not once per run.
    assert [name for name, _ in fake_comfyui.uploads].count("busy_two.png") == 1
    _wait_for_collection(fake_comfyui, 2)
    assert sorted(args for args, _ in fake_comfyui.collected) == sorted(
        (["3"], None, pic_id) for pic_id in selected
    )


def test_a_fixed_input_is_filled_from_the_setup_and_a_lost_one_runs_nothing(
    workflow_env, edit_workflow, fake_comfyui
):
    server, owner = workflow_env.server, workflow_env.owner
    fixed = _picture_with_sha(server, "busy_two.png", "sha-run-fixed")
    r = owner.put(
        edit_workflow,
        json={
            "inputs": [
                {"node_id": "1", "mode": "fixed", "picture_id": fixed},
                {"node_id": "2", "mode": "selection"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    selected = _picture_ids(server)["busy_one.png"]
    r = _run(owner, "edit.json", picture_ids=[selected], stack=False)
    assert r.status_code == 200, r.text
    assert fake_comfyui.submitted[0]["1"]["inputs"]["image"] == (
        f"pixlstash-{fixed}-sha-run-fixed.png"
    )

    def bin_it(session):
        picture = session.get(Picture, fixed)
        picture.deleted = True
        session.add(picture)
        session.commit()

    server.vault.db.run_task(bin_it, priority=DBPriority.IMMEDIATE)
    r = _run(owner, "edit.json", picture_ids=[selected], stack=False)
    assert r.status_code == 409, r.text
    assert len(fake_comfyui.submitted) == 1


def test_a_run_that_cannot_be_filled_submits_nothing(
    workflow_env, edit_workflow, fake_comfyui
):
    owner = workflow_env.owner
    ids = _picture_ids(workflow_env.server)
    one, two = ids["busy_one.png"], ids["busy_two.png"]
    picker = [{"node_id": "1", "picture_id": two}]
    for name, body in (
        # The picker input was not chosen.
        ("edit.json", {"picture_ids": [one]}),
        # A Selection input with nothing selected.
        ("edit.json", {"pictures": picker}),
        # A node that is not a picker.
        (
            "edit.json",
            {"picture_ids": [one], "pictures": [*picker, {"node_id": "2"}]},
        ),
        (
            "edit.json",
            {
                "picture_ids": [one],
                "pictures": picker,
                "seed_mode": "fixed",
                "seed": "x",
            },
        ),
        ("edit.json", {"picture_ids": ["1"], "pictures": picker}),
        ("edit.json", {"picture_ids": [one], "pictures": picker, "seed_mode": "fixd"}),
        ("edit.json", {"picture_ids": [one], "pictures": picker, "stack": "false"}),
    ):
        r = _run(owner, name, **body)
        assert r.status_code == 400, (body, r.text)
    r = _run(owner, "edit.json", picture_ids=[987654], pictures=picker)
    assert r.status_code == 404, r.text

    # A picture in the Scrapheap is not one a run may read.
    def bin_it(session):
        picture = session.get(Picture, one)
        picture.deleted = True
        session.add(picture)
        session.commit()

    workflow_env.server.vault.db.run_task(bin_it, priority=DBPriority.IMMEDIATE)
    r = _run(owner, "edit.json", picture_ids=[one], pictures=picker)
    assert r.status_code == 404, r.text
    assert fake_comfyui.submitted == [] and fake_comfyui.uploads == []


def test_a_workflow_without_a_selection_input_runs_once_and_takes_no_selection(
    workflow_env, sampler_workflow, fake_comfyui
):
    owner = workflow_env.owner
    one = _picture_ids(workflow_env.server)["busy_one.png"]
    r = _run(owner, "sampler.json", picture_ids=[one])
    assert r.status_code == 400, r.text
    r = _run(owner, "canvas.json")
    assert r.status_code == 400 and "UI format" in r.text, r.text
    assert fake_comfyui.submitted == []

    r = _run(
        owner,
        "sampler.json",
        caption="a dog",
        values=[{"node_id": "1", "name": "steps", "value": 31}],
        seed_mode="keep",
        character_id=workflow_env.character_id,
    )
    assert r.status_code == 200, r.text
    (prompt,) = r.json()["prompts"]
    assert (prompt["picture_id"], prompt["prompt_id"]) == (None, "prompt-1")
    (graph,) = fake_comfyui.submitted
    assert graph["1"]["inputs"]["steps"] == 31
    assert graph["1"]["inputs"]["seed"] == 2**64 - 2
    assert graph["2"]["inputs"]["text"] == "a dog"
    _wait_for_collection(fake_comfyui, 1)
    assert fake_comfyui.collected == [
        ((["3"], None, None), {"character_id": workflow_env.character_id})
    ]

    r = _run(owner, "sampler.json", values=[{"node_id": "2", "name": "text"}])
    assert r.status_code == 400, r.text
    r = _run(owner, "sampler.json")
    assert r.status_code == 200, r.text
    assert fake_comfyui.submitted[-1]["1"]["inputs"]["seed"] != 2**64 - 2


def test_the_list_says_which_workflows_can_run(workflow_env, sampler_workflow):
    listed = {
        item["name"]: item
        for item in workflow_env.owner.get(f"{API}/comfyui/workflows").json()[
            "workflows"
        ]
    }
    assert listed["sampler.json"]["runnable"] is True
    assert listed["canvas.json"]["runnable"] is False


def test_a_selection_run_stacks_each_output_with_its_picture(
    workflow_env, edit_workflow, fake_comfyui
):
    ids = _picture_ids(workflow_env.server)
    one, two = ids["busy_one.png"], ids["busy_two.png"]
    r = _run(
        workflow_env.owner,
        "edit.json",
        picture_ids=[one],
        pictures=[{"node_id": "1", "picture_id": two}],
    )
    assert r.status_code == 200, r.text
    _wait_for_collection(fake_comfyui, 1)
    ((output_nodes, stack_id, source), _context) = fake_comfyui.collected[0]
    assert (output_nodes, source) == (["3"], one)
    assert stack_id is not None
    assert str(stack_id) in fake_comfyui.submitted[0]["3"]["inputs"]["filename_prefix"]


def test_a_template_holding_the_picture_and_the_caption_gets_both(
    workflow_env, tmp_path, edit_workflow, fake_comfyui
):
    # A loader detection does not recognise, bound by the old dialog with both
    # tokens in one string: filled a role at a time, one would wipe the other.
    graph, _changed = workflow_bindings.migrate_placeholders(
        {
            "7": {
                "class_type": "MyPictureSource",
                "inputs": {"image": "{{image_path}} | {{caption}}"},
            },
            "3": {"class_type": "SaveImage", "inputs": {"images": ["7", 0]}},
        }
    )
    (tmp_path / "joint.json").write_text(json.dumps(graph), encoding="utf-8")
    comfyui_module._describe_workflow.cache_clear()
    one = _picture_ids(workflow_env.server)["busy_one.png"]
    r = _run(
        workflow_env.owner, "joint.json", picture_ids=[one], caption="dog", stack=False
    )
    assert r.status_code == 200, r.text
    assert fake_comfyui.submitted[0]["7"]["inputs"]["image"] == (
        f"pixlstash-{one}-unhashed.png | dog"
    )


def test_a_batch_that_fails_partway_returns_the_runs_it_started(
    workflow_env, edit_workflow, fake_comfyui, monkeypatch
):
    def submit_once(_url, workflow, _client_id=None):
        if fake_comfyui.submitted:
            raise HTTPException(status_code=502, detail="ComfyUI prompt failed")
        fake_comfyui.submitted.append(workflow)
        return {"prompt_id": "prompt-1"}

    monkeypatch.setattr(comfyui_module, "_submit_comfyui_prompt", submit_once)
    ids = _picture_ids(workflow_env.server)
    r = _run(
        workflow_env.owner,
        "edit.json",
        picture_ids=[ids["busy_one.png"], ids["busy_three.png"]],
        pictures=[{"node_id": "1", "picture_id": ids["busy_two.png"]}],
        stack=False,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "partial"
    assert [p["prompt_id"] for p in body["prompts"]] == ["prompt-1"]
    assert "ComfyUI prompt failed" in body["error"]


def test_a_run_names_the_missing_pictures_and_caps_the_batch(
    workflow_env, edit_workflow, fake_comfyui, monkeypatch
):
    owner = workflow_env.owner
    ids = _picture_ids(workflow_env.server)
    one, two, three = ids["busy_one.png"], ids["busy_two.png"], ids["busy_three.png"]
    picker = [{"node_id": "1", "picture_id": two}]
    r = _run(owner, "edit.json", picture_ids=[one, 987654], pictures=picker)
    assert r.status_code == 404 and "987654" in r.json()["detail"], r.text

    monkeypatch.setattr(comfyui_module, "MAX_RUNS_PER_REQUEST", 1)
    r = _run(owner, "edit.json", picture_ids=[one, three], pictures=picker)
    assert r.status_code == 400 and "at most 1" in r.json()["detail"], r.text
    assert fake_comfyui.submitted == []
    r = _run(owner, "edit.json", picture_ids=[one], pictures=picker, stack=False)
    assert r.status_code == 200, r.text


def test_setup_and_run_resolve_a_duplicated_fixed_picture_to_the_same_copy(
    workflow_env, edit_workflow, fake_comfyui
):
    server, owner = workflow_env.server, workflow_env.owner
    older = _picture_with_sha(server, "busy_two.png", "sha-run-duplicate")
    newer = _picture_with_sha(server, "busy_three.png", "sha-run-duplicate")
    r = owner.put(
        edit_workflow,
        json={
            "inputs": [
                {"node_id": "1", "mode": "fixed", "picture_id": newer},
                {"node_id": "2", "mode": "selection"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _by_node(r.json())["1"]["picture_id"] == older
    selected = _picture_ids(server)["busy_one.png"]
    r = _run(owner, "edit.json", picture_ids=[selected], stack=False)
    assert r.status_code == 200, r.text
    assert fake_comfyui.submitted[0]["1"]["inputs"]["image"] == (
        f"pixlstash-{older}-sha-run-duplicate.png"
    )


# ===========================================================================
# A LoRA from the shelf, put into the run (#1310)
# ===========================================================================

_SHELF_LORA_SHA = _h("shelf-lora-digest")
_SHELF_LORA_FILENAME = "example-subject-v2.safetensors"
# What the shelf calls the copy it scanned, and what ComfyUI calls the file it
# has. They are deliberately different paths of the same basename, because that
# is the ordinary case: the two sides count from different folders.
_SHELF_LORA_RELPATH = f"sd15/{_SHELF_LORA_FILENAME}"
_COMFY_LORA_NAME = f"characters/{_SHELF_LORA_FILENAME}"


@pytest.fixture
def lora_workflow(tmp_path, monkeypatch, workflow_env):
    """One workflow carrying both kinds of LoRA slot, and one adapter on the shelf.

    The shelf rows are written here and removed again, because the module's own
    re-seed only knows about the two models it seeds itself; a leftover adapter
    would be a second file for the next test's basename to match.

    ``comfy.info`` is what ComfyUI answers for ``object_info``: set it to a map,
    or to an exception for a ComfyUI that cannot be reached.
    """
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "{{image_path}}"}},
        "2": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "whatever-is-there.safetensors", "model": ["1", 0]},
        },
        "3": {
            "class_type": "PixlStashAdapterLoader",
            "inputs": {"adapter_sha256": "0" * 64, "model": ["2", 0]},
        },
        "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
    }
    bound, _changed = workflow_bindings.migrate_placeholders(graph)
    (tmp_path / "lora.json").write_text(json.dumps(bound), encoding="utf-8")
    _isolate_workflow_folders(tmp_path, monkeypatch)

    comfy = SimpleNamespace(
        info={
            "LoraLoader": {
                "input": {"required": {"lora_name": [[_COMFY_LORA_NAME], {}]}}
            }
        }
    )

    def fake_object_info(_url):
        if isinstance(comfy.info, Exception):
            raise comfy.info
        return comfy.info

    monkeypatch.setattr(comfyui_module, "fetch_object_info", fake_object_info)

    hub = workflow_env.server.hub
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, kind, filename, sha256, provenance) "
            "VALUES ('adapter', 'lora', ?, ?, 'scanned')",
            (_SHELF_LORA_FILENAME, _SHELF_LORA_SHA),
        )
        model_id = conn.execute(
            "SELECT id FROM model WHERE sha256 = ?", (_SHELF_LORA_SHA,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO model_folder (path, kind, movable) "
            "VALUES ('/home/me/loras', 'reference', 'fixed')"
        )
        folder_id = conn.execute(
            "SELECT id FROM model_folder WHERE path = '/home/me/loras'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state) "
            "VALUES (?, ?, ?, 'present')",
            (model_id, folder_id, _SHELF_LORA_RELPATH),
        )
    comfy.model_id = model_id
    try:
        yield comfy
    finally:
        with hub.transaction() as conn:
            conn.execute("DELETE FROM model_file WHERE model_id = ?", (model_id,))
            conn.execute("DELETE FROM model_folder WHERE id = ?", (folder_id,))
            conn.execute("DELETE FROM model WHERE id = ?", (model_id,))


def _lora_run(workflow_env, **body):
    ids = _picture_ids(workflow_env.server)
    return _run(
        workflow_env.owner,
        "lora.json",
        picture_ids=[ids["busy_one.png"]],
        stack=False,
        **body,
    )


def test_a_workflow_says_which_lora_slots_a_run_can_swap(
    workflow_env, lora_workflow, edit_workflow
):
    owner = workflow_env.owner
    r = owner.get(f"{API}/comfyui/workflows/lora.json/inputs")
    assert r.status_code == 200, r.text
    assert [(s["node_id"], s["by"], s["value"]) for s in r.json()["lora_slots"]] == [
        ("2", "filename", "whatever-is-there.safetensors"),
        ("3", "digest", "0" * 64),
    ]
    # The control: the workflow beside it has no loader and says so with an
    # empty list rather than by leaving the field out.
    r = owner.get(f"{API}/comfyui/workflows/edit.json/inputs")
    assert r.status_code == 200 and r.json()["lora_slots"] == [], r.text


def test_a_shelf_lora_goes_into_the_named_slot_and_no_other(
    workflow_env, lora_workflow, fake_comfyui
):
    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="2")
    assert r.status_code == 200, r.text
    submitted = fake_comfyui.submitted[0]
    # The name this ComfyUI lists, matched on the basename the shelf knows.
    assert submitted["2"]["inputs"]["lora_name"] == _COMFY_LORA_NAME
    # The other loader keeps the LoRA the workflow chose: swapping both would
    # load one file twice and lose the other.
    assert submitted["3"]["inputs"]["adapter_sha256"] == "0" * 64


def test_the_pixlstash_loader_takes_the_digest_without_asking_comfyui(
    workflow_env, lora_workflow, fake_comfyui
):
    # Nothing to resolve, so an unreachable ComfyUI is no obstacle either.
    lora_workflow.info = RuntimeError("Could not reach ComfyUI at http://127.0.0.1:1")
    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="3")
    assert r.status_code == 200, r.text
    submitted = fake_comfyui.submitted[0]
    assert submitted["3"]["inputs"]["adapter_sha256"] == _SHELF_LORA_SHA
    assert submitted["2"]["inputs"]["lora_name"] == "whatever-is-there.safetensors"


def test_the_path_this_shelf_scanned_wins_over_another_file_of_that_name(
    workflow_env, lora_workflow, fake_comfyui
):
    lora_workflow.info = {
        "LoraLoader": {
            "input": {
                "required": {"lora_name": [[_COMFY_LORA_NAME, _SHELF_LORA_RELPATH], {}]}
            }
        }
    }
    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="2")
    assert r.status_code == 200, r.text
    assert fake_comfyui.submitted[0]["2"]["inputs"]["lora_name"] == _SHELF_LORA_RELPATH


def test_a_workflow_with_two_loaders_will_not_guess_which_one(
    workflow_env, lora_workflow, fake_comfyui
):
    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA)
    assert r.status_code == 400, r.text
    assert "2 LoRA slots" in r.json()["detail"]
    assert "2 lora_name, 3 adapter_sha256" in r.json()["detail"]

    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="4")
    assert r.status_code == 400 and "not a LoRA loader" in r.text
    assert fake_comfyui.submitted == [] and fake_comfyui.uploads == []


def test_the_chosen_lora_wins_over_one_set_in_the_parameter_form(
    workflow_env, lora_workflow, fake_comfyui
):
    r = _lora_run(
        workflow_env,
        adapter_sha256=_SHELF_LORA_SHA,
        lora_node_id="2",
        values=[{"node_id": "2", "name": "lora_name", "value": "from-the-form.st"}],
    )
    assert r.status_code == 200, r.text
    assert fake_comfyui.submitted[0]["2"]["inputs"]["lora_name"] == _COMFY_LORA_NAME


def test_a_run_without_a_lora_leaves_the_workflows_own_choice(
    workflow_env, lora_workflow, fake_comfyui
):
    r = _lora_run(workflow_env)
    assert r.status_code == 200, r.text
    submitted = fake_comfyui.submitted[0]
    assert submitted["2"]["inputs"]["lora_name"] == "whatever-is-there.safetensors"
    assert submitted["3"]["inputs"]["adapter_sha256"] == "0" * 64


def test_a_workflow_with_no_lora_loader_is_refused_by_name(
    workflow_env, lora_workflow, edit_workflow, fake_comfyui
):
    ids = _picture_ids(workflow_env.server)
    r = _run(
        workflow_env.owner,
        "edit.json",
        picture_ids=[ids["busy_one.png"]],
        pictures=[{"node_id": "1", "picture_id": ids["busy_two.png"]}],
        adapter_sha256=_SHELF_LORA_SHA,
        stack=False,
    )
    assert r.status_code == 400, r.text
    assert "no LoRA loader" in r.json()["detail"]
    assert fake_comfyui.submitted == [] and fake_comfyui.uploads == []


def test_a_lora_the_shelf_or_comfyui_does_not_have_stops_before_any_upload(
    workflow_env, lora_workflow, fake_comfyui
):
    r = _lora_run(workflow_env, adapter_sha256=_h("nothing"), lora_node_id="2")
    assert r.status_code == 404 and "not on this PixlStash's shelf" in r.text

    # On the shelf, absent from the ComfyUI this would run on: that ComfyUI
    # lists LoRAs, just not this one under any name the shelf knows it by.
    lora_workflow.info = {
        "LoraLoader": {"input": {"required": {"lora_name": [["other.st"], {}]}}}
    }
    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="2")
    assert r.status_code == 400 and "not on the ComfyUI" in r.text

    # A loader whose file list ComfyUI does not enumerate (an empty combo reads
    # as "not listed", never as "nothing installed").
    lora_workflow.info = {
        "LoraLoader": {"input": {"required": {"lora_name": [[], {}]}}}
    }
    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="2")
    assert r.status_code == 400 and "will not guess" in r.text

    # A ComfyUI that cannot be asked at all is a 502, not a guess.
    lora_workflow.info = RuntimeError("Could not reach ComfyUI at http://127.0.0.1:1")
    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="2")
    assert r.status_code == 502 and "could not ask ComfyUI" in r.text

    # Nothing reached ComfyUI for any of the three.
    assert fake_comfyui.submitted == [] and fake_comfyui.uploads == []


def test_only_a_file_a_lora_loader_can_load_is_accepted(
    workflow_env, lora_workflow, fake_comfyui
):
    """A checkpoint, a VAE or an engine is refused by name, an unknown is not.

    ``file_kind`` is an allow-list here: the shelf holds five other kinds and
    writing any of them into ``lora_name`` is a run that fails further in.
    """
    hub = workflow_env.server.hub
    unknown_sha, vae_sha = _h("unclassified-file"), _h("a-vae")
    with hub.transaction() as conn:
        conn.executemany(
            "INSERT INTO model (file_kind, filename, sha256, provenance) "
            "VALUES (?, ?, ?, 'scanned')",
            [
                ("unknown", "mystery.safetensors", unknown_sha),
                ("vae", "example-vae.safetensors", vae_sha),
            ],
        )
    try:
        r = _lora_run(
            workflow_env, adapter_sha256=_h("realvisxl-digest"), lora_node_id="2"
        )
        assert r.status_code == 400 and "is a checkpoint" in r.text

        r = _lora_run(workflow_env, adapter_sha256=vae_sha, lora_node_id="2")
        assert r.status_code == 400 and "is a vae" in r.text

        # Unclassified is first-class on this shelf and usually an adapter the
        # header reader could not place, so it is offered, not refused: it gets
        # as far as the name check.
        r = _lora_run(workflow_env, adapter_sha256=unknown_sha, lora_node_id="2")
        assert r.status_code == 400 and "not on the ComfyUI" in r.text
    finally:
        with hub.transaction() as conn:
            conn.execute(
                "DELETE FROM model WHERE sha256 IN (?, ?)", (unknown_sha, vae_sha)
            )
    assert fake_comfyui.submitted == [] and fake_comfyui.uploads == []


def test_edit_with_comfyui_swaps_the_same_way_the_run_panel_does(
    workflow_env, lora_workflow, fake_comfyui
):
    """run_i2i is the overlay's "Edit with ComfyUI", and it runs a saved file too.

    Same body, same refusals: the swap belongs to every route that runs a
    workflow, not only to the run panel's own (#1310).
    """
    ids = _picture_ids(workflow_env.server)
    r = workflow_env.owner.post(
        f"{API}/comfyui/run_i2i",
        json={
            "picture_ids": [ids["busy_one.png"]],
            "workflow_name": "lora.json",
            "stack": False,
            "adapter_sha256": _SHELF_LORA_SHA,
            "lora_node_id": "2",
        },
    )
    assert r.status_code == 200, r.text
    submitted = fake_comfyui.submitted[0]
    assert submitted["2"]["inputs"]["lora_name"] == _COMFY_LORA_NAME
    assert submitted["3"]["inputs"]["adapter_sha256"] == "0" * 64

    # And the same refusal, before anything is uploaded for a second run.
    fake_comfyui.submitted.clear()
    fake_comfyui.uploads.clear()
    r = workflow_env.owner.post(
        f"{API}/comfyui/run_i2i",
        json={
            "picture_ids": [ids["busy_one.png"]],
            "workflow_name": "lora.json",
            "stack": False,
            "adapter_sha256": _SHELF_LORA_SHA,
        },
    )
    assert r.status_code == 400 and "2 LoRA slots" in r.text
    assert fake_comfyui.submitted == [] and fake_comfyui.uploads == []


def test_the_workflow_list_says_which_files_have_a_lora_loader(
    workflow_env, lora_workflow, edit_workflow
):
    """The menus that run a workflow read the list, not a request per file."""
    r = workflow_env.owner.get(f"{API}/comfyui/workflows")
    assert r.status_code == 200, r.text
    slots = {w["name"]: w.get("lora_slots") for w in r.json()["workflows"]}
    assert [s["node_id"] for s in slots["lora.json"]] == ["2", "3"]
    assert slots["edit.json"] == []


def test_a_stacker_swaps_the_slot_named_and_leaves_its_other_loras(
    workflow_env, lora_workflow, fake_comfyui, tmp_path
):
    """A slot is a node AND a field: one stacker carries several LoRAs.

    Naming the node alone used to keep every field on it and write the chosen
    LoRA into all of them - the style and the character both gone, a disabled
    slot switched on, one file loaded three times.
    """
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "{{image_path}}"}},
        "7": {
            "class_type": "CR LoRA Stack",
            "inputs": {
                "lora_name_1": "example-style.safetensors",
                "lora_name_2": "example-character.safetensors",
                "lora_name_3": "None",
            },
        },
        "9": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }
    bound, _changed = workflow_bindings.migrate_placeholders(graph)
    (tmp_path / "stack.json").write_text(json.dumps(bound), encoding="utf-8")
    lora_workflow.info = {
        "CR LoRA Stack": {
            "input": {
                "required": {
                    f"lora_name_{n}": [["None", _COMFY_LORA_NAME], {}]
                    for n in (1, 2, 3)
                }
            }
        }
    }
    ids = _picture_ids(workflow_env.server)

    def run(**body):
        return _run(
            workflow_env.owner,
            "stack.json",
            picture_ids=[ids["busy_one.png"]],
            stack=False,
            adapter_sha256=_SHELF_LORA_SHA,
            **body,
        )

    # The node alone is still three slots, and says which.
    r = run(lora_node_id="7")
    assert r.status_code == 400, r.text
    assert "3 LoRA slots" in r.json()["detail"]
    assert "7 lora_name_1, 7 lora_name_2, 7 lora_name_3" in r.json()["detail"]
    assert fake_comfyui.submitted == []

    r = run(lora_node_id="7", lora_field="lora_name_2")
    assert r.status_code == 200, r.text
    inputs = fake_comfyui.submitted[0]["7"]["inputs"]
    assert inputs["lora_name_2"] == _COMFY_LORA_NAME
    assert inputs["lora_name_1"] == "example-style.safetensors"
    assert inputs["lora_name_3"] == "None"

    r = run(lora_node_id="7", lora_field="lora_name_9")
    assert r.status_code == 400 and "not a LoRA slot" in r.text


def test_an_empty_slot_name_counts_as_not_sent(
    workflow_env, lora_workflow, fake_comfyui
):
    """A client with nothing chosen yet sends "", which must not name node "".

    Two slots and an empty name is still an unnamed choice between two - the
    refusal that lists them, not "Node  is not a LoRA loader".
    """
    r = _lora_run(
        workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="", lora_field=""
    )
    assert r.status_code == 400, r.text
    assert "2 LoRA slots" in r.json()["detail"]
    assert "is not a LoRA loader" not in r.json()["detail"]


def test_a_share_link_sees_no_lora_filenames_on_the_workflow_list(
    workflow_env, lora_workflow
):
    """The list is open to share tokens, and a slot's value is the owner's inventory.

    /models/ and /adapters/ keep the model inventory from those tokens, so the
    list must not hand the same filenames and digests out through its slots.
    Both directions: the scoped token still reads the list (over-blocking is
    its own regression) and the owner still gets the values from /inputs.
    """
    server = workflow_env.server
    token = _mint(
        workflow_env.owner,
        "lora slot probe",
        resource_type="character",
        resource_id=workflow_env.character_id,
    )
    client = _bearer(server, token)
    r = client.get(f"{API}/comfyui/workflows")
    assert r.status_code == 200, r.text
    row = next(w for w in r.json()["workflows"] if w["name"] == "lora.json")
    assert [s["node_id"] for s in row["lora_slots"]] == ["2", "3"]
    assert all("value" not in slot for slot in row["lora_slots"])
    assert "whatever-is-there.safetensors" not in r.text
    assert "0" * 64 not in r.text

    # The owner's own read of the file's setup keeps them.
    r = workflow_env.owner.get(f"{API}/comfyui/workflows/lora.json/inputs")
    assert r.status_code == 200, r.text
    assert "whatever-is-there.safetensors" in {
        s["value"] for s in r.json()["lora_slots"]
    }
    # And the scoped token cannot reach that read at all.
    r = client.get(f"{API}/comfyui/workflows/lora.json/inputs")
    assert r.status_code == 403, r.text


def test_a_swap_reaches_a_document_stored_with_its_graph_one_level_down():
    """The import dialog stores {"prompt": graph}; detection reads inside it, so must the write."""
    graph = {"5": {"class_type": "LoraLoader", "inputs": {"lora_name": "old.st"}}}
    swap = {
        "adapter": {"sha256": "b" * 64, "filenames": ["new.st"]},
        "targets": [
            {
                "node_id": "5",
                "class_type": "LoraLoader",
                "field": "lora_name",
                "by": "filename",
            }
        ],
        "object_info": {
            "LoraLoader": {"input": {"required": {"lora_name": [["new.st"], {}]}}}
        },
    }
    wrapped = {"prompt": graph}
    comfyui_module._apply_lora_swap(wrapped, swap, "test")
    assert wrapped["prompt"]["5"]["inputs"]["lora_name"] == "new.st"

    # A slot the instance does not have is refused, never run with its own LoRA.
    with pytest.raises(HTTPException) as refused:
        comfyui_module._apply_lora_swap({"prompt": {}}, swap, "test")
    assert refused.value.status_code == 500


def test_a_ui_format_file_is_refused_as_what_it_is_before_the_shelf_is_asked():
    """No hub here at all: the refusal must come before the lookup, and name the format."""
    with pytest.raises(HTTPException) as refused:
        comfyui_module._resolve_lora_swap(
            None, {"adapter_sha256": "b" * 64}, None, "test", "http://127.0.0.1:1"
        )
    assert refused.value.status_code == 400
    assert "UI format" in refused.value.detail
