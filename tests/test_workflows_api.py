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
import tempfile
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import delete

from pixlstash.authz.policy import AccessPolicy
from pixlstash.authz.registry import ROUTE_POLICIES
from pixlstash.database import DBPriority
from pixlstash.db_models import Picture
from pixlstash.server import Server
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

_DOCUMENTS = {
    BUSY_RECIPE_A: {"nodes": {"1": {"class_type": "CheckpointLoaderSimple"}}},
    BUSY_RECIPE_B: {"nodes": {"1": {"class_type": "CheckpointLoaderSimple"}}},
    BINNED_RECIPE: {"nodes": {}},
    FORGOTTEN_RECIPE: {"nodes": {}},
}

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

        detached = _quiesce_background_work(server)

        yield SimpleNamespace(
            server=server,
            owner=owner,
            character_id=character_id,
            detached=detached,
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


def test_every_workflow_route_is_declared_owner_only():
    """§16.1: the declaration IS the enforcement, so pin all four cells.

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
