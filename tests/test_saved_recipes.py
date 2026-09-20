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

from pixlstash import auth
from pixlstash.authz.policy import AccessPolicy
from pixlstash.authz.registry import ROUTE_POLICIES
from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, SavedRecipe
from pixlstash.hub.workflow_cards import CORE_RULE_VERSION
from pixlstash.server import Server
from pixlstash.services import saved_recipe_service
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
    # Export (v1.12 B8): the one route here that hands back the prompt as a
    # file, which is the single thing the workflow export exists to strip.
    ("GET", "/api/v1/recipes/{recipe_id}/export"),
    # The looks the pictures themselves carry (v1.12 F6): the same picture
    # rows credit is grouped from, returned as groups instead of a count.
    ("GET", "/api/v1/recipes/used"),
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
                # `specials` empty rather than NULL: NULL is "not derived
                # yet" and would have the backfill finder re-derive this
                # hand-written row out from under the test.
                "INSERT INTO workflow_topology_core "
                "(topology_hash, core_hash, core_version, workflow_type, "
                "slots, specials) VALUES (?, ?, ?, 'txt2img', '[]', '')",
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
        ("GET", f"{API}/recipes/{saved['id']}/export", None),
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

    # And the other way round, which is the half a test asking only about the
    # excluded card cannot see: A left the group, so B's automatic stack must
    # stop holding it. Reading CARD_A returns from the unstacked branch before
    # the grouping query runs at all.
    from_b = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_B}
    ).json()
    assert [row["name"] for row in from_b] == ["On B"]


def test_a_stack_membership_row_beats_the_automatic_grouping(recipe_env):
    """An explicit membership is read first, so A stacks with C and not with B."""
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

    # B is left alone with its own: A is placed elsewhere, so the automatic
    # group B is still in must not pick it back up. Only a read from B's side
    # exercises that exclusion.
    from_b = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_B}
    ).json()
    assert [row["name"] for row in from_b] == ["On B"]


def test_a_membership_row_is_obeyed_whatever_its_stacks_kind(recipe_env):
    """An ``auto`` stack with rows is an arrangement somebody made, like a manual one."""
    on_a = _save(recipe_env.owner, CARD_A, name="On A")
    _save(recipe_env.owner, CARD_B, name="On B")
    on_c = _save(recipe_env.owner, CARD_C, name="On C")
    with recipe_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_stack (stack_id, kind, core_hash) "
            "VALUES ('auto-1', 'auto', ?)",
            (CORE_OTHER,),
        )
        conn.executemany(
            "INSERT INTO workflow_stack_member (stack_id, workflow_key, position) "
            "VALUES ('auto-1', ?, ?)",
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


def test_two_recipes_of_the_same_look_are_both_credited(recipe_env):
    """A group counts for every recipe it matches, not for the first one read.

    Two recipes differing only in a LoRA strength are the same look as far as a
    picture row can tell — nothing stores a strength — so crediting one of them
    and not the other would be a guess the payload presents as an answer.
    """
    weak = _save(recipe_env.owner, CARD_A, name="Weak", loras=_ada(0.3))
    strong = _save(recipe_env.owner, CARD_A, name="Strong", loras=_ada(0.9))
    credit = {
        row["id"]: row["pictures"]
        for row in recipe_env.owner.get(
            f"{API}/recipes", params={"workflow_key": CARD_A}
        ).json()
    }
    assert credit == {weak["id"]: 2, strong["id"]: 2}


def test_a_picture_never_read_for_metadata_is_credited_to_nobody(recipe_env):
    """The over-count this rule exists to stop.

    A picture the extraction pass has not reached has NULL prompt and NULL
    ``comfyui_loras``. Folded in, it would key as "no prompt, no LoRAs" — which
    is exactly a recipe saved with the defaults — and hand that recipe the whole
    un-extracted half of the library.
    """

    def add(session):
        for path in ("unread_one.png", "unread_two.png"):
            session.add(
                Picture(
                    file_path=path,
                    deleted=False,
                    created_at=datetime(2026, 9, 3),
                    workflow_structural_hash=VARIANT_A,
                    workflow_hash_version="v1",
                    comfyui_positive_prompt=None,
                    comfyui_loras=None,
                )
            )
        session.commit()

    recipe_env.server.vault.db.run_task(add, priority=DBPriority.IMMEDIATE)
    _save(recipe_env.owner, CARD_A, name="Empty", prompt="", loras=[])
    listed = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_A}
    ).json()
    assert listed[0]["pictures"] == 0


def test_credit_survives_whitespace_around_the_prompt(recipe_env):
    """A recipe saved from a picture must credit that picture.

    The two prompts arrive by different routes — one typed into the Save
    dialog, one read out of the graph — so a trailing newline is not a
    different look, and a byte-exact match would read 0 against the very
    picture the recipe was saved from.
    """
    _save(recipe_env.owner, CARD_A, prompt=f"  {PROMPT}\n", loras=_ada())
    listed = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_A}
    ).json()
    assert listed[0]["pictures"] == 2


# ===========================================================================
# Bad requests are refused, not crashed on
# ===========================================================================


def test_a_source_picture_this_library_does_not_hold_is_refused(recipe_env):
    """422, not the vault's foreign key surfacing as a 500 in the owner's log."""
    r = recipe_env.owner.post(
        f"{API}/recipes", json={"workflow_key": CARD_A, "source_picture_id": 999999}
    )
    assert r.status_code == 422, r.text
    assert recipe_env.owner.get(f"{API}/recipes").json() == []

    saved = _save(recipe_env.owner, CARD_A)
    r = recipe_env.owner.patch(
        f"{API}/recipes/{saved['id']}", json={"source_picture_id": 999999}
    )
    assert r.status_code == 422, r.text

    # And a real one is accepted, so the check is not simply refusing the field.
    picture_id = recipe_env.owner.get(f"{API}/pictures").json()[0]["id"]
    r = recipe_env.owner.patch(
        f"{API}/recipes/{saved['id']}", json={"source_picture_id": picture_id}
    )
    assert r.status_code == 200 and r.json()["source_picture_id"] == picture_id


def test_a_null_name_or_prompt_clears_it_rather_than_failing_the_write(recipe_env):
    """Both columns are NOT NULL; a null in the body means "empty"."""
    saved = _save(recipe_env.owner, CARD_A, name="Named", prompt="something")
    r = recipe_env.owner.patch(
        f"{API}/recipes/{saved['id']}", json={"name": None, "prompt": None}
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "" and r.json()["prompt"] == ""


def test_an_oversized_overrides_map_or_seed_is_refused(recipe_env):
    """The ceiling the module claims: every free-form field has one."""
    r = recipe_env.owner.post(
        f"{API}/recipes",
        json={"workflow_key": CARD_A, "overrides": {"k": "x" * 30000}},
    )
    assert r.status_code == 422, r.text
    r = recipe_env.owner.post(
        f"{API}/recipes", json={"workflow_key": CARD_A, "seed": "9" * 200}
    )
    assert r.status_code == 422, r.text
    assert recipe_env.owner.get(f"{API}/recipes").json() == []


def test_reordering_one_tab_leaves_every_other_recipe_where_it_was(recipe_env):
    """A tab reorders its own stack's subset, and nothing else may move.

    Writing 0..n-1 over a subset would drop those rows onto positions another
    workflow's recipes already hold: the unfiltered listing would interleave the
    two, and the next save — which appends after the highest position — would
    land in the middle of them.
    """
    first_a = _save(recipe_env.owner, CARD_A, name="A1")
    _save(recipe_env.owner, CARD_C, name="C1")
    second_a = _save(recipe_env.owner, CARD_A, name="A2")

    r = recipe_env.owner.put(
        f"{API}/recipes/order",
        json={"recipe_ids": [second_a["id"], first_a["id"]]},
    )
    assert r.status_code == 200, r.text

    listed = recipe_env.owner.get(f"{API}/recipes").json()
    assert [row["name"] for row in listed] == ["A2", "C1", "A1"], (
        "the outsider moved, or the two reordered rows landed on its position"
    )
    positions = [row["position"] for row in listed]
    assert len(set(positions)) == len(positions), f"positions collide: {positions}"

    # The next save still lands last rather than in the middle of them.
    appended = _save(recipe_env.owner, CARD_A, name="A3")
    assert appended["position"] > max(positions)
    assert [row["name"] for row in recipe_env.owner.get(f"{API}/recipes").json()] == [
        "A2",
        "C1",
        "A1",
        "A3",
    ]


def test_the_service_refuses_to_write_a_field_the_api_does_not_offer(recipe_env):
    """``workflow_key`` and ``position`` are not editable, at the service too.

    The route's payload model cannot carry either, so this is the guard behind
    it: a recipe does not move between workflows, and ordering has its own
    route.
    """
    saved = _save(recipe_env.owner, CARD_A, name="Named")
    updated = saved_recipe_service.update_recipe(
        recipe_env.server.vault,
        saved["id"],
        {"workflow_key": CARD_C, "position": 99, "name": "Renamed"},
    )
    assert updated["name"] == "Renamed"
    assert updated["workflow_key"] == CARD_A
    assert updated["position"] == saved["position"]


def test_a_recipe_whose_stored_json_will_not_parse_still_lists(recipe_env):
    """One damaged row must not take the whole tab down with it."""
    saved = _save(recipe_env.owner, CARD_A, loras=_ada())

    def damage(session):
        recipe = session.get(SavedRecipe, saved["id"])
        recipe.loras = "{not json"
        recipe.overrides = "{not json"
        session.add(recipe)
        session.commit()

    recipe_env.server.vault.db.run_task(damage, priority=DBPriority.IMMEDIATE)
    listed = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_A}
    ).json()
    assert listed[0]["loras"] == [] and listed[0]["overrides"] == {}
    # It reads as a recipe with no LoRAs, which credits the pictures that loaded
    # none — 0 here — rather than silently keeping its old credit.
    assert listed[0]["pictures"] == 0


def test_a_picture_read_with_no_prompt_matches_a_recipe_with_no_prompt(recipe_env):
    """The other half of the sentinel, decided rather than left to fall out.

    A prompt-free graph is ordinary — an upscale, a ``ConditioningZeroOut``,
    two samplers that disagree — and a picture the pass HAS read and found no
    prompt in really was made by this stack with that look. Excluding it would
    make a recipe saved on an upscale workflow read 0 for ever; the stack is
    what bounds the answer.
    """

    def add(session):
        session.add(
            Picture(
                file_path="a_promptless.png",
                deleted=False,
                created_at=datetime(2026, 9, 4),
                workflow_structural_hash=VARIANT_A,
                workflow_hash_version="v1",
                comfyui_positive_prompt=None,
                comfyui_loras=json.dumps([]),
            )
        )
        session.commit()

    recipe_env.server.vault.db.run_task(add, priority=DBPriority.IMMEDIATE)
    _save(recipe_env.owner, CARD_A, name="Upscale", prompt="", loras=[])
    listed = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_A}
    ).json()
    assert listed[0]["pictures"] == 1


def test_stacking_the_same_lora_twice_is_a_different_look(recipe_env):
    """Duplicates are kept, because the picture side keeps them.

    ``comfyui_loras`` holds one entry per loader node, so a graph that loads one
    file twice says so. A recipe that stacks it twice must not credit the
    pictures that loaded it once.
    """

    def add(session):
        session.add(
            Picture(
                file_path="a_stacked.png",
                deleted=False,
                created_at=datetime(2026, 9, 5),
                workflow_structural_hash=VARIANT_A,
                workflow_hash_version="v1",
                comfyui_positive_prompt=PROMPT,
                comfyui_loras=json.dumps([ADA, ADA]),
            )
        )
        session.commit()

    recipe_env.server.vault.db.run_task(add, priority=DBPriority.IMMEDIATE)
    once = _save(recipe_env.owner, CARD_A, name="Once", loras=_ada())
    twice = _save(
        recipe_env.owner,
        CARD_A,
        name="Twice",
        loras=[
            {"filename": ADA, "strength": 1.0},
            {"filename": f"characters/{ADA}", "strength": 0.2},
        ],
    )
    credit = {
        row["id"]: row["pictures"]
        for row in recipe_env.owner.get(
            f"{API}/recipes", params={"workflow_key": CARD_A}
        ).json()
    }
    assert credit == {once["id"]: 2, twice["id"]: 1}


def test_reordering_a_subset_of_one_tab_moves_only_the_rows_it_names(recipe_env):
    """Permuting means the rows left out keep their place, wherever that is."""
    first = _save(recipe_env.owner, CARD_A, name="A1")
    middle = _save(recipe_env.owner, CARD_A, name="A2")
    last = _save(recipe_env.owner, CARD_A, name="A3")

    r = recipe_env.owner.put(
        f"{API}/recipes/order", json={"recipe_ids": [last["id"], first["id"]]}
    )
    assert r.status_code == 200, r.text
    # Two rows were named and two rows moved; A2 keeps the position it had, which
    # leaves it between them.
    assert [row["name"] for row in recipe_env.owner.get(f"{API}/recipes").json()] == [
        "A3",
        "A2",
        "A1",
    ]
    assert middle["position"] == 1


def test_one_request_may_order_recipes_of_different_workflows(recipe_env):
    """A stack's tab does exactly this: its rows belong to several members."""
    on_a = _save(recipe_env.owner, CARD_A, name="On A")
    on_b = _save(recipe_env.owner, CARD_B, name="On B")
    r = recipe_env.owner.put(
        f"{API}/recipes/order", json={"recipe_ids": [on_b["id"], on_a["id"]]}
    )
    assert r.status_code == 200, r.text
    listed = recipe_env.owner.get(f"{API}/recipes", params={"workflow_key": CARD_A})
    assert [row["name"] for row in listed.json()] == ["On B", "On A"]


def test_an_id_list_too_long_to_bind_is_refused_as_a_bad_request(recipe_env):
    """A database limit must not reach the caller as a 500."""
    saved = _save(recipe_env.owner, CARD_A)
    r = recipe_env.owner.put(
        f"{API}/recipes/order",
        json={"recipe_ids": list(range(1, 100002))},
    )
    assert r.status_code == 422, r.status_code
    # The order that exists is untouched, and an ordinary request still works.
    r = recipe_env.owner.put(f"{API}/recipes/order", json={"recipe_ids": [saved["id"]]})
    assert r.status_code == 200, r.text


def test_a_variant_keyed_by_a_superseded_rule_neither_stacks_nor_credits(recipe_env):
    """Rows from an older ``key_version`` are another build's cards.

    A ``WORKFLOW_KEY_VERSION`` bump or a flipped slot mark re-keys a variant, and
    the hub holds both rows until the pass drains. Grouping by the stale one
    would stack cards this build does not compute, and counting its pictures
    would credit them to whoever happens to hold the old key — so both reads
    filter, like every other reader of ``workflow_variant`` but the grouping
    report.

    Two stale rows, because there are two queries: one gives card D a place in
    A's automatic stack, the other hangs a second variant off card B.
    """
    stale_topology = _h("topo-d")
    stale_variant = _h("variant-d")
    stale_card = _h("card-d")
    b_old_variant = _h("variant-b-old")
    with recipe_env.server.hub.transaction() as conn:
        for topology, structural in (
            (stale_topology, stale_variant),
            (TOPO_B, b_old_variant),
        ):
            if topology == stale_topology:
                conn.execute(
                    "INSERT INTO workflow_topology "
                    "(topology_hash, hash_version, node_count, first_seen_at) "
                    "VALUES (?, 'v1', 12, '2026-09-01T00:00:00Z')",
                    (topology,),
                )
                conn.execute(
                    "INSERT INTO workflow_topology_core "
                    "(topology_hash, core_hash, core_version, workflow_type, "
                    "slots, specials) VALUES (?, ?, ?, 'txt2img', '[]', '')",
                    (topology, CORE_SHARED, CORE_RULE_VERSION),
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
            "VALUES (?, ?, ?, 'v0')",
            (stale_variant, stale_topology, stale_card),
        )
        conn.execute(
            "INSERT INTO workflow_variant "
            "(structural_hash, topology_hash, workflow_key, key_version) "
            "VALUES (?, ?, ?, 'v0')",
            (b_old_variant, TOPO_B, CARD_B),
        )

    def add(session):
        for path, structural in (
            ("d_match.png", stale_variant),
            ("b_old_match.png", b_old_variant),
        ):
            session.add(
                Picture(
                    file_path=path,
                    deleted=False,
                    created_at=datetime(2026, 9, 6),
                    workflow_structural_hash=structural,
                    workflow_hash_version="v1",
                    comfyui_positive_prompt=PROMPT,
                    comfyui_loras=json.dumps([ADA]),
                )
            )
        session.commit()

    recipe_env.server.vault.db.run_task(add, priority=DBPriority.IMMEDIATE)
    on_a = _save(recipe_env.owner, CARD_A, name="On A", loras=_ada())
    on_b = _save(recipe_env.owner, CARD_B, name="On B", loras=_ada())
    _save(recipe_env.owner, stale_card, name="On the stale card", loras=_ada())

    listed = recipe_env.owner.get(
        f"{API}/recipes", params={"workflow_key": CARD_A}
    ).json()
    assert {row["id"] for row in listed} == {on_a["id"], on_b["id"]}, (
        "a card known only under a superseded key rule joined the stack"
    )
    # Still the two pictures of the current variants: the stale variant's
    # picture belongs to a card this build does not compute.
    assert {row["id"]: row["pictures"] for row in listed} == {
        on_a["id"]: 2,
        on_b["id"]: 2,
    }


# ===========================================================================
# Export (v1.12 B8) — everything, and a list saying so
# ===========================================================================


def test_exporting_a_recipe_hands_back_everything_it_holds(recipe_env):
    """A recipe export withholds nothing; `shares` is what the dialog lists.

    The opposite of `GET /workflows/{key}/export`, and deliberately: a recipe
    IS the prompt and the LoRA names, so one with those taken out would make
    nothing. The contract is that the owner is told what they are agreeing to,
    not that the file is scrubbed.
    """
    saved = _save(
        recipe_env.owner,
        CARD_A,
        loras=_ada(0.8),
        overrides={"sampler|steps": 30},
        seed="12345",
    )
    r = recipe_env.owner.get(f"{API}/recipes/{saved['id']}/export")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["recipe"]["prompt"] == PROMPT
    assert payload["recipe"]["loras"][0]["filename"] == ADA
    assert payload["recipe"]["loras"][0]["strength"] == 0.8
    assert payload["recipe"]["overrides"] == {"sampler|steps": 30}
    assert payload["recipe"]["seed"] == "12345"
    assert payload["filename"].endswith(".json")
    # Every one of those facts is named in `shares`, because the dialog shows
    # that list and nothing else before the owner agrees to the file.
    shares = " ".join(payload["shares"])
    assert "the prompt you wrote" in shares
    assert ADA in shares
    assert "parameter setting" in shares
    assert "seed" in shares


def test_a_recipe_export_leaves_this_librarys_own_bookkeeping_out(recipe_env):
    """The row id, its place in the tab and the source picture mean nothing elsewhere."""
    saved = _save(recipe_env.owner, CARD_A, source_picture_id=None)
    recipe = recipe_env.owner.get(f"{API}/recipes/{saved['id']}/export").json()[
        "recipe"
    ]
    for local in ("id", "position", "source_picture_id", "pictures"):
        assert local not in recipe, f"{local} is this library's bookkeeping"
    # What a recipe genuinely needs to travel is still there.
    assert recipe["workflow_key"] == CARD_A
    assert recipe["keep_seed"] is False


def test_a_recipe_export_says_when_it_carries_a_name_the_shelf_cannot_vouch_for(
    recipe_env,
):
    """The forgotten-name guard on the recipe side: a warning, not a blank.

    Implementation plan §5.7 asks BOTH exports to check. The workflow export
    answers by blanking the name; a recipe cannot, because the name is the
    recipe, so it answers by saying so in the list the owner reads first.
    """
    saved = _save(recipe_env.owner, CARD_A, loras=_ada())
    shares = recipe_env.owner.get(f"{API}/recipes/{saved['id']}/export").json()[
        "shares"
    ]
    assert any("no longer holds" in line and ADA in line for line in shares), shares

    # Put the model on the shelf and the warning goes: the line is about what
    # this machine holds, not about the recipe naming a LoRA at all.
    with recipe_env.server.hub.transaction() as conn:
        conn.execute("DELETE FROM model WHERE filename = ?", (ADA,))
        # `kind` and a digest are both NOT NULL for an adapter by CHECK
        # constraint, so the shelf row is written the way a scan writes one.
        conn.execute(
            "INSERT INTO model (file_kind, kind, filename, sha256, provenance) "
            "VALUES ('adapter', 'unknown', ?, ?, 'scanned')",
            (ADA, _h("ada-digest")),
        )
    try:
        shares = recipe_env.owner.get(f"{API}/recipes/{saved['id']}/export").json()[
            "shares"
        ]
        assert not any("no longer holds" in line for line in shares), shares
        assert any(ADA in line for line in shares), "the LoRA is still listed"
    finally:
        with recipe_env.server.hub.transaction() as conn:
            conn.execute("DELETE FROM model WHERE filename = ?", (ADA,))


def test_exporting_a_recipe_that_does_not_exist_is_a_404(recipe_env):
    assert recipe_env.owner.get(f"{API}/recipes/999999/export").status_code == 404


def test_the_export_stays_closed_with_the_gate_rolled_back(recipe_env):
    """``AUTHZ_GATE_ENFORCING = False`` is a documented rollback (§16.3).

    Every other recipe route is a write, which the READ-token middleware
    refuses on the verb whatever the gate is doing. This one is a **GET**, and
    it returns the owner's prompt verbatim — so it is the first route in this
    module that needs the second belt, ``auth.READ_BLOCKED_GET_PREFIXES``, and
    the gated ``test_every_untemplated_owner_class_get_is_on_the_read_blocked_belt``
    is what noticed the prefix was missing.
    """
    saved = _save(recipe_env.owner, CARD_A, loras=_ada())
    path = f"{API}/recipes/{saved['id']}/export"
    server = recipe_env.server
    scoped = _bearer(
        server,
        _mint(
            recipe_env.owner,
            "recipe rollback scoped",
            resource_type="character",
            resource_id=recipe_env.character_id,
        ),
    )
    unscoped = _bearer(server, _mint(recipe_env.owner, "recipe rollback unscoped"))
    previously_enforcing = server.authz._enforcing
    server.authz._enforcing = False
    try:
        for client in (scoped, unscoped):
            assert client.get(f"{API}/pictures").status_code == 200, (
                "the token is dead; the refusal below would prove nothing"
            )
            assert_real_route(server.api, "GET", path)
            r = client.get(path)
            assert r.status_code == 403, f"GET {path}: {r.status_code} {r.text}"
        # The positive control, with the gate still rolled back: over-blocking
        # the owner is its own regression.
        assert recipe_env.owner.get(path).status_code == 200
    finally:
        server.authz._enforcing = previously_enforcing


def test_the_belt_and_not_only_the_gate_is_what_refuses_the_export(recipe_env):
    """Take the prefix away and the rollback stops holding.

    Without this the test above passes on the gate alone and says nothing
    about the belt it is named for.
    """
    saved = _save(recipe_env.owner, CARD_A)
    path = f"{API}/recipes/{saved['id']}/export"
    server = recipe_env.server
    scoped = _bearer(
        server,
        _mint(
            recipe_env.owner,
            "recipe belt probe",
            resource_type="character",
            resource_id=recipe_env.character_id,
        ),
    )
    previously_enforcing = server.authz._enforcing
    previously_blocked = auth.READ_BLOCKED_GET_PREFIXES
    server.authz._enforcing = False
    auth.READ_BLOCKED_GET_PREFIXES = tuple(
        prefix for prefix in previously_blocked if prefix != "/api/v1/recipes/"
    )
    try:
        assert scoped.get(path).status_code == 200, (
            "with the gate rolled back AND the prefix removed this must be "
            "reachable — if it is not, the assertion above is passing on "
            "something else and proves nothing about the belt"
        )
    finally:
        auth.READ_BLOCKED_GET_PREFIXES = previously_blocked
        server.authz._enforcing = previously_enforcing


def test_a_lora_saved_without_its_extension_is_still_warned_about(recipe_env):
    """`shares` judges the name against the shelf, not against its suffix.

    `unvouched_model_values` ends in an extension test, which is right for a
    graph widget — where a string may be an enum token rather than a filename —
    and wrong here, where the field IS a model name. A recipe saved as "ada"
    rather than "ada.safetensors" would otherwise be exported in plain text
    with nothing said about it.
    """
    bare = ADA.removesuffix(".safetensors")
    saved = _save(
        recipe_env.owner,
        CARD_A,
        loras=[{"filename": bare, "sha256": None, "strength": 1.0}],
    )
    shares = recipe_env.owner.get(f"{API}/recipes/{saved['id']}/export").json()[
        "shares"
    ]
    assert any("no longer holds" in line and bare in line for line in shares), shares


# ===========================================================================
# Used looks — what the stack's own pictures were made with (v1.12 F6)
# ===========================================================================


def _used(client, *keys: str) -> list[dict]:
    r = client.get(f"{API}/recipes/used", params={"workflow_key": list(keys)})
    assert r.status_code == 200, r.text
    return r.json()


def test_used_looks_are_the_stacks_own_pictures_and_no_outsiders(recipe_env):
    """A library that has saved nothing still has looks to show.

    This is the whole point of the route: the Recipes tab was empty for every
    workflow on a full library, because "recipe" meant only what somebody had
    pressed Save on.
    """
    looks = _used(recipe_env.owner, CARD_A)

    # The stack's three distinct looks, and neither card C's picture nor the
    # soft-deleted one. Compared on the base name: the two pictures of the
    # first look spell their LoRA differently, and which spelling survives the
    # merge is the database's grouping order, not a promise.
    assert [
        (
            look["prompt"],
            [x["filename"].rsplit("/", 1)[-1].lower() for x in look["loras"]],
            look["pictures"],
        )
        for look in looks
    ] == [
        # Most pictures first, then by prompt: there is no order the owner
        # chose on this half of the tab, so the busiest look leads.
        (PROMPT, [ADA], 2),
        (PROMPT, ["other_style.safetensors"], 1),
        (OTHER_PROMPT, [ADA], 1),
    ]


def test_one_look_spelled_two_ways_is_one_row(recipe_env):
    """`a_match` and `b_match` load the same file under different names.

    They arrive as two SQL groups, because the column is grouped as text.
    Listing both would offer the same look twice with its pictures split
    between them, and credit already sums on the normalized key.
    """
    looks = _used(recipe_env.owner, CARD_A)
    matching = [
        look
        for look in looks
        if look["prompt"] == PROMPT
        and len(look["loras"]) == 1
        and look["loras"][0]["filename"].lower().endswith(ADA)
    ]
    assert len(matching) == 1, looks
    assert matching[0]["pictures"] == 2


def test_a_saved_recipe_takes_its_look_off_the_used_half(recipe_env):
    """The two halves of the tab can never both claim one look."""
    before = _used(recipe_env.owner, CARD_A)
    assert any(look["prompt"] == PROMPT and look["pictures"] == 2 for look in before)

    _save(recipe_env.owner, CARD_A, prompt=PROMPT, loras=_ada())

    after = _used(recipe_env.owner, CARD_A)
    assert not any(
        look["prompt"] == PROMPT
        and len(look["loras"]) == 1
        and look["loras"][0]["filename"].lower().endswith(ADA)
        for look in after
    ), after
    # And the others are untouched: saving one look does not hide the rest.
    assert {look["prompt"] for look in after} == {PROMPT, OTHER_PROMPT}


def test_a_selection_of_several_workflows_is_the_union_counted_once(recipe_env):
    """The multi-selection ask, and the double-count it must not make.

    A and B are one stack, so naming both must answer exactly as naming one:
    the stacks are deduplicated before the pictures are read. Naming C as well
    adds C's own picture.
    """
    one = _used(recipe_env.owner, CARD_A)
    both = _used(recipe_env.owner, CARD_A, CARD_B)
    assert both == one, "naming two members of one stack counted its looks twice"

    with_c = _used(recipe_env.owner, CARD_A, CARD_C)
    assert (
        sum(look["pictures"] for look in with_c)
        == sum(look["pictures"] for look in one) + 1
    )
    # C's picture carries the same look as the stack's own, so the union folds
    # them into one row rather than listing it twice.
    matching = [
        look
        for look in with_c
        if look["prompt"] == PROMPT
        and len(look["loras"]) == 1
        and look["loras"][0]["filename"].lower().endswith(ADA)
    ]
    assert len(matching) == 1 and matching[0]["pictures"] == 3, with_c


def test_a_look_names_a_cover_picture_to_read_its_strengths_back_from(recipe_env):
    """The card's thumbnail, and where the Save dialog gets the strengths.

    A picture row stores LoRA names and no strength, so a look cannot carry
    one; the cover is a real picture of the group, so the dialog can read the
    graph's own strengths when the owner saves it.
    """
    looks = _used(recipe_env.owner, CARD_A)
    assert all(look["cover_picture_id"] for look in looks), looks

    def paths(session):
        return {
            picture.id: picture.file_path for picture in session.exec(select(Picture))
        }

    by_id = recipe_env.server.vault.db.run_immediate_read_task(paths)
    covers = {by_id[look["cover_picture_id"]] for look in looks}
    # Never the soft-deleted picture, which is in no group at all.
    assert "a_binned.png" not in covers, covers


def test_used_looks_need_a_workflow_and_answer_empty_without_one(recipe_env):
    """No key is no question: this route never lists the whole library.

    `GET /recipes` with no key deliberately answers the library's saved rows;
    the equivalent here would be a group-by over every picture that exists.
    """
    r = recipe_env.owner.get(f"{API}/recipes/used")
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_no_scoped_token_can_read_the_used_looks(recipe_env):
    """The **gate** refuses it, not the verb belt in front of the gate.

    A bare READ token is refused by `READ_BLOCKED_GET_PREFIXES` before routing,
    so asserting 403 on one proves nothing about this route's declaration: with
    the entry loosened to `ANY_TOKEN` that assertion still passes. A token
    RESTRICTED to one picture gets past no belt and is refused by the gate on
    the declaration alone, which is the thing under test.
    """
    assert_real_route(recipe_env.server.api, "GET", "/api/v1/recipes/used")

    def write_one_picture(session):
        picture = session.exec(select(Picture)).first()
        return picture.id

    picture_id = recipe_env.server.vault.db.run_immediate_read_task(write_one_picture)
    scoped = _bearer(
        recipe_env.server,
        _mint(recipe_env.owner, "used-looks probe", picture_id=picture_id),
    )
    r = scoped.get(f"{API}/recipes/used", params={"workflow_key": CARD_A})
    assert r.status_code == 403, r.text

    # The positive control, on the same seeded library: over-blocking would be
    # its own regression and this assertion is what tells the two apart.
    assert _used(recipe_env.owner, CARD_A)


def test_the_read_token_belt_also_closes_the_used_looks(recipe_env):
    """The belt in front of the gate, asserted as itself.

    `/api/v1/recipes/` is in `READ_BLOCKED_GET_PREFIXES`, which is what keeps
    this route closed if `AUTHZ_GATE_ENFORCING` is ever rolled back. Separate
    from the test above so neither can stand in for the other.
    """
    assert "/api/v1/recipes/" in auth.READ_BLOCKED_GET_PREFIXES, (
        "the prefix that closes this route to every scoped token is gone"
    )
    # And by its own path, which is what an untemplated owner-class GET needs
    # to survive the `AUTHZ_GATE_ENFORCING = False` rollback.
    assert "/api/v1/recipes/used" in auth.READ_BLOCKED_GET_PATHS
    scoped = _bearer(recipe_env.server, _mint(recipe_env.owner, "belt probe"))
    r = scoped.get(f"{API}/recipes/used", params={"workflow_key": CARD_A})
    assert r.status_code == 403, r.text


def test_the_gate_alone_refuses_the_used_looks_with_the_belt_lifted(recipe_env):
    """The declaration, proved behaviourally rather than only pinned.

    **Every scoped token is refused on this path before routing**, whatever
    its scope and whatever it is restricted to: `/api/v1/recipes/` is in
    `READ_BLOCKED_GET_PREFIXES` and that check does not consult the scope. So
    an ordinary 403 here proves the belt and says nothing about
    `ROUTE_POLICIES` — loosen the entry to `ANY_TOKEN` and the plain assertion
    above still passes, which is the silent coverage loss §16 designs against.

    Lifting the belt for the length of this test is what puts the gate in the
    path, so the refusal measured is the declaration's. Both are wanted: the
    belt keeps this closed if `AUTHZ_GATE_ENFORCING` is ever rolled back, and
    the gate closes it while enforcement is on.
    """
    # **Both halves of the belt, or this test dies again.** The prefix holds
    # the templated routes and the frozenset holds this one by its own path;
    # lifting either alone leaves the other refusing, and the assertion below
    # would pass on the belt while claiming to measure the gate.
    without_prefix = tuple(
        prefix
        for prefix in auth.READ_BLOCKED_GET_PREFIXES
        if prefix != "/api/v1/recipes/"
    )
    without_path = auth.READ_BLOCKED_GET_PATHS - {"/api/v1/recipes/used"}
    assert len(without_prefix) == len(auth.READ_BLOCKED_GET_PREFIXES) - 1, (
        "the prefix this test lifts is no longer there; the belt has moved"
    )
    assert len(without_path) == len(auth.READ_BLOCKED_GET_PATHS) - 1, (
        "this route is no longer on the exact-path belt; the belt has moved"
    )
    scoped = _bearer(recipe_env.server, _mint(recipe_env.owner, "gate probe"))
    original = auth.READ_BLOCKED_GET_PREFIXES
    original_paths = auth.READ_BLOCKED_GET_PATHS
    auth.READ_BLOCKED_GET_PREFIXES = without_prefix
    auth.READ_BLOCKED_GET_PATHS = without_path
    try:
        r = scoped.get(f"{API}/recipes/used", params={"workflow_key": CARD_A})
        assert r.status_code == 403, (
            "with the READ-token belt lifted the gate let a scoped token read "
            f"the owner's prompts: {r.status_code} {r.text}"
        )
        # The owner is unaffected by the lift, so the 403 above is the scope
        # being refused and not the route having broken.
        assert _used(recipe_env.owner, CARD_A)
    finally:
        auth.READ_BLOCKED_GET_PREFIXES = original
        auth.READ_BLOCKED_GET_PATHS = original_paths

    # And the belt is back, so every later test measures the shipped shape.
    assert (
        scoped.get(f"{API}/recipes/used", params={"workflow_key": CARD_A}).status_code
        == 403
    )


def test_used_looks_survive_sqlites_variable_ceiling(recipe_env):
    """A selection whose stacks hold more variants than SQLite takes at once.

    One card accumulates a variant per structural change, so a hundred selected
    cards resolve to thousands of structural hashes. Unchunked that is one
    `IN` over the bound-parameter floor and a 500 — the database limit
    surfacing as a fault, which is what the caps in this module exist to stop.
    Pinned to the historical 999 because this machine's SQLite is far above it
    and a green run here would otherwise prove nothing.
    """
    import sqlite3

    from sqlalchemy import event as sa_event

    extra = 1200
    with recipe_env.server.hub.transaction() as conn:
        for index in range(extra):
            structural = _h(f"ceiling-variant-{index}")
            conn.execute(
                "INSERT INTO workflow_recipe "
                "(structural_hash, topology_hash, hash_version, node_count, "
                "first_seen_at) VALUES (?, ?, 'v1', 12, '2026-09-01T00:00:00Z')",
                (structural, TOPO_A),
            )
            conn.execute(
                "INSERT INTO workflow_variant "
                "(structural_hash, topology_hash, workflow_key, key_version) "
                "VALUES (?, ?, ?, 'v1')",
                (structural, TOPO_A, CARD_A),
            )

    engine = recipe_env.server.vault.db._engine

    def _set_limit(dbapi_conn, _record):
        dbapi_conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

    sa_event.listen(engine, "connect", _set_limit)
    engine.dispose()
    try:
        looks = _used(recipe_env.owner, CARD_A)
        # The answer is unchanged: chunking must merge, not concatenate, or a
        # look straddling two chunks is listed twice with its pictures split.
        assert [(look["prompt"], look["pictures"]) for look in looks] == [
            (PROMPT, 2),
            (PROMPT, 1),
            (OTHER_PROMPT, 1),
        ]
        listed = recipe_env.owner.get(f"{API}/recipes", params={"workflow_key": CARD_A})
        assert listed.status_code == 200, listed.text
    finally:
        sa_event.remove(engine, "connect", _set_limit)
        engine.dispose()


def test_a_workflow_key_longer_than_a_digest_is_refused(recipe_env):
    """The per-key ceiling the list rewrite must not have dropped.

    A workflow key is a 64-character digest. `max_length` on a `list[str]`
    bounds the LIST, so the string ceiling has to be declared on the item or
    it silently disappears — which is what happened when this parameter
    stopped being a single string.
    """
    for path in (f"{API}/recipes", f"{API}/recipes/used"):
        r = recipe_env.owner.get(path, params={"workflow_key": "a" * 4000})
        assert r.status_code == 422, f"{path} took a 4000-character key: {r.text}"


def test_a_picture_with_no_prompt_is_not_a_look(recipe_env):
    """The group that would otherwise lead the list saying nothing.

    A graph that was read but yielded no prompt leaves `comfyui_loras = "[]"`
    and `comfyui_positive_prompt = NULL`. Every such picture in the stack folds
    into one group, which would be the biggest one and would render as a
    thumbnail, "Not kept yet" and a count — nothing identifying, at the top of
    the feature's headline surface.
    """

    def add_promptless(session):
        for index in range(3):
            session.add(
                Picture(
                    file_path=f"no_prompt_{index}.png",
                    deleted=False,
                    created_at=datetime(2026, 9, 2),
                    workflow_structural_hash=VARIANT_A,
                    workflow_hash_version="v1",
                    comfyui_positive_prompt=None,
                    comfyui_loras=json.dumps([]),
                    comfyui_models=json.dumps([]),
                )
            )
        session.commit()

    recipe_env.server.vault.db.run_task(add_promptless, priority=DBPriority.IMMEDIATE)

    looks = _used(recipe_env.owner, CARD_A)
    assert all(look["prompt"] or look["loras"] for look in looks), looks
    # The stack's real looks are untouched; only the nameless group goes.
    assert len(looks) == 3


def test_a_picture_with_no_prompt_but_a_lora_is_still_a_look(recipe_env):
    """Only the group with nothing at all goes: a LoRA names a look too."""

    def add_lora_only(session):
        session.add(
            Picture(
                file_path="lora_only.png",
                deleted=False,
                created_at=datetime(2026, 9, 2),
                workflow_structural_hash=VARIANT_A,
                workflow_hash_version="v1",
                comfyui_positive_prompt=None,
                comfyui_loras=json.dumps(["lonely.safetensors"]),
                comfyui_models=json.dumps([]),
            )
        )
        session.commit()

    recipe_env.server.vault.db.run_task(add_lora_only, priority=DBPriority.IMMEDIATE)

    looks = _used(recipe_env.owner, CARD_A)
    assert any(
        not look["prompt"]
        and [row["filename"] for row in look["loras"]] == ["lonely.safetensors"]
        for look in looks
    ), looks
