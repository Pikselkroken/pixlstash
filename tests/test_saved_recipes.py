"""Saved recipes (v1.12 B6): the routes, the stack they list, and credit.

Environment sharing
-------------------
One ``Server`` per module, because the boot is the expensive part and every
assertion here is a row. The autouse fixture wipes the ``saved_recipe`` table
and re-seeds the hub and the pictures before each test, so nothing inherits
another test's state, and re-checks that the work finders are still out of the
planner: a warm vault's ComfyUI extraction sweep would rewrite the very
``comfyui_*`` columns credit is measured from.

The seeded library is shaped around what credit has to get right:

* two cards, **A** and **B**, sharing a ``core_hash`` so they are one automatic
  stack, and a third card **C** on its own — a recipe saved on A must list
  beside B's and must not see C's pictures;
* pictures matching a recipe's prompt and LoRA names under a **different
  spelling** (``characters/Ada.safetensors`` against ``ada.safetensors``), which
  must credit, and one loading a **different LoRA**, which must not;
* a soft-deleted picture that matches everything, which must not count.

Both directions, per §16.1: the owner's reads and writes work (over-blocking is
its own regression), a live resource-scoped token is refused by the gate, and
every route's ``OWNER_ONLY`` declaration is pinned — the writes are refused by
the READ-token middleware before the gate is reached, so the declaration is
what a loosened policy would have to get past.
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
from sqlmodel import delete, select

from pixlstash.authz.policy import AccessPolicy
from pixlstash.authz.registry import ROUTE_POLICIES
from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, SavedRecipe
from pixlstash.hub.workflow_cards import CORE_RULE_VERSION
from pixlstash.server import Server
from tests.authz_guard import assert_real_route, no_spa_fallback  # noqa: F401

API = "/api/v1"

# The SPA catch-all answers an unmatched GET with 200, which would make every
# positive assertion below vacuous if a path were misspelled.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")

_RECIPE_ROUTES = (
    ("GET", "/api/v1/recipes"),
    ("POST", "/api/v1/recipes"),
    ("PUT", "/api/v1/recipes/order"),
    ("PATCH", "/api/v1/recipes/{recipe_id}"),
    ("DELETE", "/api/v1/recipes/{recipe_id}"),
)


def _h(name: str) -> str:
    """A stable stand-in for one content key: 64 hex characters, like the real one."""
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


CARD_A = _h("card-a")
CARD_B = _h("card-b")
CARD_C = _h("card-c")
TOPO_A, TOPO_B, TOPO_C = _h("topo-a"), _h("topo-b"), _h("topo-c")
VARIANT_A, VARIANT_B, VARIANT_C = _h("variant-a"), _h("variant-b"), _h("variant-c")
# A and B differ only in what the stack rule strips, so they group; C does not.
CORE_SHARED = _h("core-shared")
CORE_OTHER = _h("core-other")

PROMPT = "a cold portrait, rim light"
OTHER_PROMPT = "a warm portrait, soft light"
ADA = "ada.safetensors"

# (topology, structural, workflow_key, core_hash)
_SEED_CARDS = (
    (TOPO_A, VARIANT_A, CARD_A, CORE_SHARED),
    (TOPO_B, VARIANT_B, CARD_B, CORE_SHARED),
    (TOPO_C, VARIANT_C, CARD_C, CORE_OTHER),
)

# (file_path, structural, prompt, loras, deleted)
_SEED_PICTURES = (
    # Credits: the recipe's prompt, the recipe's LoRA, on the recipe's own card.
    ("a_match.png", VARIANT_A, PROMPT, [f"characters/{ADA.capitalize()}"], False),
    # Credits too: same look, made on the stack's OTHER member.
    ("b_match.png", VARIANT_B, PROMPT, [ADA], False),
    # Does not: a different LoRA is a different look, whatever the prompt says.
    ("a_other_lora.png", VARIANT_A, PROMPT, ["other_style.safetensors"], False),
    # Does not: same LoRA, different prompt.
    ("a_other_prompt.png", VARIANT_A, OTHER_PROMPT, [ADA], False),
    # Does not: it is in the Scrapheap, so the recipe has made nothing with it.
    ("a_binned.png", VARIANT_A, PROMPT, [ADA], True),
    # Does not: card C is not in A and B's stack.
    ("c_match.png", VARIANT_C, PROMPT, [ADA], False),
)


def _seed_hub(server) -> None:
    """Write the card tables from scratch: three cards, two of them stacked."""
    with server.hub.transaction() as conn:
        # Children before parents: the hub enforces foreign keys.
        conn.execute("DELETE FROM workflow_stack_member")
        conn.execute("DELETE FROM workflow_stack")
        conn.execute("DELETE FROM workflow_unstacked")
        conn.execute("DELETE FROM workflow_variant")
        conn.execute("DELETE FROM workflow_recipe_graph")
        conn.execute("DELETE FROM workflow_recipe_asset")
        conn.execute("DELETE FROM workflow_recipe")
        conn.execute("DELETE FROM workflow_topology_core")
        conn.execute("DELETE FROM workflow_topology")
        for topology, structural, key, core in _SEED_CARDS:
            conn.execute(
                "INSERT INTO workflow_topology "
                "(topology_hash, hash_version, node_count, first_seen_at) "
                "VALUES (?, 'v1', 12, '2026-09-01T00:00:00Z')",
                (topology,),
            )
            conn.execute(
                "INSERT INTO workflow_recipe "
                "(structural_hash, topology_hash, hash_version, node_count, "
                "first_seen_at) VALUES (?, ?, 'v1', 12, '2026-09-01T00:00:00Z')",
                (structural, topology),
            )
            conn.execute(
                "INSERT INTO workflow_variant "
                "(structural_hash, topology_hash, workflow_key, key_version) "
                "VALUES (?, ?, ?, 'v1')",
                (structural, topology, key),
            )
            conn.execute(
                "INSERT INTO workflow_topology_core "
                "(topology_hash, core_hash, core_version, workflow_type, slots) "
                "VALUES (?, ?, ?, 'txt2img', '[]')",
                (topology, core, CORE_RULE_VERSION),
            )


def _seed_pictures(server) -> None:
    """Replace the vault's pictures with the seeded set, in one queued task."""

    def write(session):
        session.exec(delete(SavedRecipe))
        session.exec(delete(Picture))
        for path, structural, prompt, loras, deleted in _SEED_PICTURES:
            session.add(
                Picture(
                    file_path=path,
                    deleted=deleted,
                    created_at=datetime(2026, 9, 1),
                    workflow_structural_hash=structural,
                    workflow_hash_version="v1",
                    comfyui_positive_prompt=prompt,
                    comfyui_loras=json.dumps(loras),
                    comfyui_models=json.dumps([]),
                )
            )
        session.commit()

    server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


def _quiesce_background_work(server):
    """Take every work finder out of the planner and let the pipeline settle.

    A shared server is warm, and ``MissingComfyUIExtractionFinder`` looks for
    exactly the rows this module hand-writes: it would re-read the (nonexistent)
    files and blank the ``comfyui_*`` columns credit is measured from. The
    planner thread and the task runner keep running.
    """
    planner = server.vault._work_planner
    task_types = list(server.vault._planner_work_finders)
    for task_type in task_types:
        server.vault._planner_work_finders.pop(task_type)
    removed = planner.detach_finders(task_types)

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
def recipe_env():
    """One Server and one owner login, for every test in the module."""
    tmp = tempfile.TemporaryDirectory()
    config_path = f"{tmp.name}/server-config.json"
    with open(config_path, "w") as handle:
        json.dump({"port": 8000}, handle)
    server = Server(config_path)
    server.__enter__()
    try:
        owner = TestClient(server.api, raise_server_exceptions=True)
        r = owner.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-ownerpass1"},
        )
        assert r.status_code == 200, r.text

        r = owner.post(f"{API}/characters", json={"name": "Recipe Character"})
        assert r.status_code in {200, 201}, r.text
        character_id = r.json().get("id") or r.json()["character"]["id"]

        _quiesce_background_work(server)
        yield SimpleNamespace(server=server, owner=owner, character_id=character_id)
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


@pytest.fixture(autouse=True)
def fresh_library(recipe_env):
    """Re-seed the hub and the vault, and empty the recipes, before every test."""
    assert not recipe_env.server.vault._planner_work_finders, (
        "a work finder is back in the planner; the seeded rows are no longer "
        "the only thing writing to this vault"
    )
    _seed_hub(recipe_env.server)
    _seed_pictures(recipe_env.server)
    # The owner session is what every positive control runs on; prove it is live
    # before any refusal is measured against it.
    r = recipe_env.owner.get(f"{API}/recipes")
    assert r.status_code == 200, (
        f"the shared owner session cannot read the recipes ({r.status_code}: "
        f"{r.text}) — every refusal below would prove nothing"
    )
    assert r.json() == [], "a previous test left recipes behind"
    yield recipe_env


def _save(client, workflow_key: str, **fields) -> dict:
    body = {"workflow_key": workflow_key, "name": "A look", "prompt": PROMPT}
    body.update(fields)
    r = client.post(f"{API}/recipes", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _ada(strength: float = 1.0) -> list[dict]:
    return [{"filename": ADA, "sha256": None, "strength": strength}]


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


def test_every_recipe_route_is_declared_owner_only():
    """§16.1: the declaration IS the enforcement, so pin every cell.

    The four writes are refused by the READ-token middleware before the gate
    reads anything, so a loosened declaration would not show up in the refusal
    test below; this is what would catch it.
    """
    for key in _RECIPE_ROUTES:
        assert key in ROUTE_POLICIES, f"{key} has no ROUTE_POLICIES entry"
        assert ROUTE_POLICIES[key].policy is AccessPolicy.OWNER_ONLY, (
            f"{key} declares {ROUTE_POLICIES[key].policy}, not OWNER_ONLY"
        )


def test_no_scoped_token_can_read_or_write_a_saved_recipe(recipe_env):
    """Every route refuses a live resource-scoped share token.

    ``assert_real_route`` is load-bearing: the middleware answers before
    routing, so a renamed route would 403 identically and the assertion would
    dissolve into a test of nothing.
    """
    saved = _save(recipe_env.owner, CARD_A, loras=_ada())
    token = _mint(
        recipe_env.owner,
        "recipe scope probe",
        resource_type="character",
        resource_id=recipe_env.character_id,
    )
    client = _bearer(recipe_env.server, token)
    assert client.get(f"{API}/pictures").status_code == 200, (
        "the scoped token is dead; the refusals below would prove nothing"
    )

    calls = (
        ("GET", f"{API}/recipes", None),
        ("POST", f"{API}/recipes", {"workflow_key": CARD_A}),
        ("PUT", f"{API}/recipes/order", {"recipe_ids": [saved["id"]]}),
        ("PATCH", f"{API}/recipes/{saved['id']}", {"name": "stolen"}),
        ("DELETE", f"{API}/recipes/{saved['id']}", None),
    )
    for method, path, body in calls:
        assert_real_route(recipe_env.server.api, method, path)
        r = client.request(method, path, json=body)
        assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"

    # The owner's own recipe is untouched by any of that.
    assert recipe_env.owner.get(f"{API}/recipes").json()[0]["id"] == saved["id"]


# ===========================================================================
# The row, and the order
# ===========================================================================


def test_a_saved_recipe_round_trips_with_its_loras_and_overrides(recipe_env):
    """The two JSON columns come back as objects, not as the strings they are stored as."""
    saved = _save(
        recipe_env.owner,
        CARD_A,
        name="Cold portrait",
        loras=_ada(0.4),
        overrides={"KSampler.steps": 28},
        seed="18446744073709551615",
        keep_seed=True,
    )
    listed = recipe_env.owner.get(f"{API}/recipes").json()
    assert [row["id"] for row in listed] == [saved["id"]]
    row = listed[0]
    assert row["name"] == "Cold portrait"
    assert row["workflow_key"] == CARD_A
    assert row["loras"] == _ada(0.4)
    assert row["overrides"] == {"KSampler.steps": 28}
    # Text, not an integer: this seed is above SQLite's INTEGER ceiling.
    assert row["seed"] == "18446744073709551615"
    assert row["keep_seed"] is True


def test_a_new_recipe_is_appended_after_the_ones_already_saved(recipe_env):
    first = _save(recipe_env.owner, CARD_A, name="First")
    second = _save(recipe_env.owner, CARD_A, name="Second")
    assert second["position"] > first["position"]
    listed = recipe_env.owner.get(f"{API}/recipes").json()
    assert [row["name"] for row in listed] == ["First", "Second"]


def test_the_order_route_rewrites_positions_and_refuses_an_unknown_id(recipe_env):
    """A partial reorder would leave an order nobody chose and no error to say so."""
    first = _save(recipe_env.owner, CARD_A, name="First")
    second = _save(recipe_env.owner, CARD_A, name="Second")

    r = recipe_env.owner.put(
        f"{API}/recipes/order", json={"recipe_ids": [second["id"], first["id"]]}
    )
    assert r.status_code == 200, r.text
    listed = recipe_env.owner.get(f"{API}/recipes").json()
    assert [row["name"] for row in listed] == ["Second", "First"]

    r = recipe_env.owner.put(
        f"{API}/recipes/order",
        json={"recipe_ids": [first["id"], second["id"], 999999]},
    )
    assert r.status_code == 404, r.text
    listed = recipe_env.owner.get(f"{API}/recipes").json()
    assert [row["name"] for row in listed] == ["Second", "First"], (
        "the refused reorder was applied in part"
    )

    r = recipe_env.owner.put(
        f"{API}/recipes/order", json={"recipe_ids": [first["id"], first["id"]]}
    )
    assert r.status_code == 400, r.text


def test_a_patch_writes_what_it_carries_and_leaves_the_rest(recipe_env):
    saved = _save(
        recipe_env.owner, CARD_A, name="Cold portrait", loras=_ada(0.4), seed="7"
    )
    r = recipe_env.owner.patch(
        f"{API}/recipes/{saved['id']}", json={"name": "Colder portrait"}
    )
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["name"] == "Colder portrait"
    assert row["prompt"] == PROMPT
    assert row["loras"] == _ada(0.4)
    assert row["seed"] == "7"
    assert row["workflow_key"] == CARD_A

    assert (
        recipe_env.owner.patch(f"{API}/recipes/999999", json={"name": "x"}).status_code
        == 404
    )


def test_a_deleted_recipe_is_gone_and_deleting_it_twice_is_a_404(recipe_env):
    saved = _save(recipe_env.owner, CARD_A)
    assert recipe_env.owner.delete(f"{API}/recipes/{saved['id']}").status_code == 200
    assert recipe_env.owner.get(f"{API}/recipes").json() == []
    assert recipe_env.owner.delete(f"{API}/recipes/{saved['id']}").status_code == 404


# ===========================================================================
# Ownership: the recipe belongs to its workflow, and runs on the stack (D10)
# ===========================================================================


def test_the_tab_lists_every_stack_members_recipes_and_no_outsiders(recipe_env):
    """Asked about A, the answer holds B's recipe because A and B are one stack."""
    on_a = _save(recipe_env.owner, CARD_A, name="On A")
    on_b = _save(recipe_env.owner, CARD_B, name="On B")
    _save(recipe_env.owner, CARD_C, name="On C")

    listed = recipe_env.owner.get(f"{API}/recipes", params={"workflow_key": CARD_A})
    assert listed.status_code == 200, listed.text
    assert {row["id"] for row in listed.json()} == {on_a["id"], on_b["id"]}
    # Each one still names the workflow it was saved from, which is what an
    # Unstack leaves behind.
    assert {row["workflow_key"] for row in listed.json()} == {CARD_A, CARD_B}


def test_an_unstacked_card_keeps_its_own_recipes_and_only_those(recipe_env):
    """The owner taking A out of its automatic group is a decision the read obeys."""
    on_a = _save(recipe_env.owner, CARD_A, name="On A")
    _save(recipe_env.owner, CARD_B, name="On B")
    with recipe_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_unstacked (workflow_key) VALUES (?)", (CARD_A,)
        )

    listed = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_A}
    ).json()
    assert [row["id"] for row in listed] == [on_a["id"]]


def test_a_manual_stack_beats_the_automatic_grouping(recipe_env):
    """A manual assignment is read first, so A stacks with C and not with B."""
    on_a = _save(recipe_env.owner, CARD_A, name="On A")
    _save(recipe_env.owner, CARD_B, name="On B")
    on_c = _save(recipe_env.owner, CARD_C, name="On C")
    with recipe_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_stack (stack_id, kind, core_hash) "
            "VALUES ('manual-1', 'manual', NULL)"
        )
        conn.executemany(
            "INSERT INTO workflow_stack_member (stack_id, workflow_key, position) "
            "VALUES ('manual-1', ?, ?)",
            ((CARD_A, 0), (CARD_C, 1)),
        )

    listed = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_A}
    ).json()
    assert {row["id"] for row in listed} == {on_a["id"], on_c["id"]}


# ===========================================================================
# Credit, computed on read
# ===========================================================================


def test_credit_counts_the_stacks_matching_pictures_however_the_lora_is_spelled(
    recipe_env,
):
    """Two pictures, on two cards of one stack, one of them naming the LoRA with
    a folder and a capital. Credit is a match on the look, not on the spelling.

    The strength differs from anything a picture could record, which is the
    point: no picture row stores one, so credit must ignore it rather than
    quietly crediting nothing.
    """
    saved = _save(recipe_env.owner, CARD_A, loras=_ada(0.4))
    listed = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_A}
    ).json()
    assert [row["id"] for row in listed] == [saved["id"]]
    assert listed[0]["pictures"] == 2


def test_a_different_lora_is_a_different_look_and_credits_nothing(recipe_env):
    """The non-match the acceptance asks for: same prompt, another LoRA."""
    saved = _save(
        recipe_env.owner,
        CARD_A,
        loras=[{"filename": "someone_else.safetensors", "strength": 1.0}],
    )
    listed = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_A}
    ).json()
    assert [row["id"] for row in listed] == [saved["id"]]
    assert listed[0]["pictures"] == 0


def test_a_different_prompt_credits_nothing_either(recipe_env):
    _save(recipe_env.owner, CARD_A, prompt="a prompt nobody wrote", loras=_ada())
    listed = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_A}
    ).json()
    assert listed[0]["pictures"] == 0


def test_a_recipe_with_no_loras_credits_only_pictures_that_loaded_none(recipe_env):
    """``[]`` on both sides is a match; a picture that loaded a LoRA is not."""
    _save(recipe_env.owner, CARD_A, loras=[])
    listed = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_A}
    ).json()
    assert listed[0]["pictures"] == 0

    def add(session):
        session.add(
            Picture(
                file_path="a_bare.png",
                deleted=False,
                created_at=datetime(2026, 9, 2),
                workflow_structural_hash=VARIANT_A,
                workflow_hash_version="v1",
                comfyui_positive_prompt=PROMPT,
                comfyui_loras=json.dumps([]),
            )
        )
        session.commit()

    recipe_env.server.vault.db.run_task(add, priority=DBPriority.IMMEDIATE)
    listed = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_A}
    ).json()
    assert listed[0]["pictures"] == 1


def test_a_picture_in_the_scrapheap_is_not_credited(recipe_env):
    """The seeded ``a_binned.png`` carries the whole look and is soft-deleted.

    Credit reads 2 and not 3: a workflow whose pictures sit in the Scrapheap has
    to read as none kept, which is the rule the whole workflow library counts
    by. Restoring it takes the count to 3, so the assertion measures the filter
    rather than a number two other mistakes could also produce.
    """
    _save(recipe_env.owner, CARD_A, loras=_ada())
    listed = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_A}
    ).json()
    assert listed[0]["pictures"] == 2

    def restore(session):
        picture = session.exec(
            select(Picture).where(Picture.file_path == "a_binned.png")
        ).one()
        picture.deleted = False
        session.add(picture)
        session.commit()

    recipe_env.server.vault.db.run_task(restore, priority=DBPriority.IMMEDIATE)
    listed = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_A}
    ).json()
    assert listed[0]["pictures"] == 3


def test_the_unfiltered_list_reports_no_credit_rather_than_a_wrong_one(recipe_env):
    """Without a workflow key there is no stack to count over, and the payload
    says 0 rather than a number measured against the wrong population."""
    _save(recipe_env.owner, CARD_A, loras=_ada())
    listed = recipe_env.owner.get(f"{API}/recipes").json()
    assert listed[0]["pictures"] == 0
