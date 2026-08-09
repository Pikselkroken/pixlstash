"""The model shelf's read API (shelf plan B5): behaviour and both authz directions.

Environment sharing
-------------------
One ``Server`` per module, built once. The shelf's own data lives in the **hub**
and is written here with plain SQL, which costs microseconds; the expensive part
is the server boot, so it is paid once. Per test, the autouse fixture wipes and
re-seeds the three shelf tables and the vault's ``adapter_attachment`` rows, and
re-mints every credential — so no test can inherit another test's token, and a
negative assertion cannot pass because the credential was dead rather than
because the scope was refused.

The seeded shelf is deliberately shaped around the three decisions B5 had to
make, so a regression in any of them is a failing assertion rather than a
judgement call:

* two adapters **with** a base model and two **without** — a null base model is a
  bulk state (37 % of a measured 91-file folder), so it is carried by the filter
  as an explicit ``UNASSIGNED`` rather than dropped;
* one ``file_kind='unknown'``, which must appear in neither list by default and
  must **never** be returned by ``/checkpoints``;
* one checkpoint with ``sha256`` NULL, which is what a 24 GB file looks like
  before ``MissingCheckpointHashFinder`` has read it: listable, carrying a stable
  ``model.id``, and not addressable by hash.

Both authz directions on every route, per §16.1: the owner cookie 200s (over-
blocking is its own regression) and every scoped/unscoped share token is 403'd by
the gate's ``OWNER_ONLY`` declaration — with an in-scope positive control proving
the refused token is live.
"""

from __future__ import annotations

import json
import os
import tempfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from pixlstash.authz.policy import AccessPolicy
from pixlstash.authz.registry import ROUTE_POLICIES
from pixlstash.database import DBPriority
from pixlstash.db_models.adapter_attachment import AdapterAttachment
from pixlstash.server import Server
from tests.authz_guard import assert_real_route, no_spa_fallback  # noqa: F401

API = "/api/v1"

# The SPA catch-all answers an unmatched GET with 200, which would make a
# positive assertion vacuous if a path were misspelled.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")

_SHELF_ROUTES = (
    ("GET", "/api/v1/adapters"),
    ("GET", "/api/v1/adapters/{sha256}"),
    ("GET", "/api/v1/checkpoints"),
)


# Hashes are only ever compared, never interpreted, so a readable stand-in beats
# 64 characters of hex. Padded to full length so nothing accidentally depends on
# the shape.
def _h(name: str) -> str:
    return (name + "0" * 64)[:64]


ADAPTER_WITH_BASE = _h("adapterbase")
ADAPTER_WITH_BASE_2 = _h("adapterbase2")
ADAPTER_NO_BASE = _h("adapternobase")
ADAPTER_NO_BASE_2 = _h("adapternobase2")
UNKNOWN_HASH = _h("unknownfile")
CHECKPOINT_HASHED = _h("checkpointhashed")

# The stack the sorting tests need. Two of the four adapters are one subject's
# run, so "a row never sorts by a number it does not display" is assertable:
# a stacked row must sort by the stack's total size and its newest member's
# date, not by the cover's own.
STACK_ID = 7

# (sha256, file_kind, kind, display_name, filename, base_model, created_at,
#  file_size, file_mtime, stack_position)
#
# Every sortable column is given a *different* order from every other, so a sort
# that silently fell back to id order, or read the wrong column, cannot pass.
_SEED_MODELS = (
    (
        ADAPTER_WITH_BASE,
        "adapter",
        "lora",
        "Alice",
        "alice.safetensors",
        "SDXL 1.0",
        "2026-08-01T00:00:00Z",
        1000,
        11,
        0,
    ),
    (
        ADAPTER_WITH_BASE_2,
        "adapter",
        "lokr",
        "Bob",
        "bob.safetensors",
        "Flux.1 dev",
        "2026-08-02T00:00:00Z",
        5000,
        55,
        None,
    ),
    (
        ADAPTER_NO_BASE,
        "adapter",
        "lora",
        None,
        "sd_xl_noname.safetensors",
        None,
        "2026-08-03T00:00:00Z",
        2000,
        22,
        None,
    ),
    (
        ADAPTER_NO_BASE_2,
        "adapter",
        "lora",
        "Dana",
        "dana.safetensors",
        None,
        "2026-08-04T00:00:00Z",
        3000,
        44,
        1,
    ),
    (
        UNKNOWN_HASH,
        "unknown",
        None,
        None,
        "mystery.safetensors",
        None,
        "2026-08-05T00:00:00Z",
        100,
        5,
        None,
    ),
    (
        CHECKPOINT_HASHED,
        "checkpoint",
        None,
        "Base XL",
        "base_xl.safetensors",
        "SDXL 1.0",
        "2026-08-06T00:00:00Z",
        9000,
        66,
        None,
    ),
    # The one that has no hash yet: a 24 GB file the hash finder has not read.
    (
        None,
        "checkpoint",
        None,
        None,
        "huge_unhashed.safetensors",
        None,
        "2026-08-07T00:00:00Z",
        24000,
        77,
        None,
    ),
)


def _seed_hub(server) -> dict[str, int]:
    """Write the shelf tables from scratch. Returns filename -> model.id."""
    with server.hub.transaction() as conn:
        conn.execute("DELETE FROM model_file")
        conn.execute("DELETE FROM model")
        conn.execute("DELETE FROM model_folder")
        conn.execute("DELETE FROM adapter_stack")
        conn.execute(
            "INSERT INTO model_folder (id, path, kind, movable, created_at) "
            "VALUES (1, '/models/loras', 'user', 'per_item', '2026-08-09T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO adapter_stack (id, name, created_at) "
            "VALUES (?, 'Alice run', '2026-08-01T00:00:00Z')",
            (STACK_ID,),
        )
        ids: dict[str, int] = {}
        for row in _SEED_MODELS:
            (
                sha,
                file_kind,
                kind,
                display_name,
                filename,
                base_model,
                created_at,
                file_size,
                file_mtime,
                stack_position,
            ) = row
            cursor = conn.execute(
                "INSERT INTO model (file_kind, kind, sha256, display_name, filename, "
                "base_model, provenance, file_size, created_at, stack_id, "
                "stack_position) VALUES (?, ?, ?, ?, ?, ?, 'external', ?, ?, ?, ?)",
                (
                    file_kind,
                    kind,
                    sha,
                    display_name,
                    filename,
                    base_model,
                    file_size,
                    created_at,
                    None if stack_position is None else STACK_ID,
                    stack_position,
                ),
            )
            model_id = int(cursor.lastrowid)
            ids[filename] = model_id
            conn.execute(
                "INSERT INTO model_file (model_id, model_folder_id, relpath, state, "
                "seen_at, file_mtime) VALUES (?, 1, ?, 'present', ?, ?)",
                (model_id, filename, "2026-08-09T00:00:00Z", file_mtime),
            )
    return ids


def _clear_attachments(server) -> None:
    def wipe(session: Session):
        session.exec(delete(AdapterAttachment))
        session.commit()

    server.vault.db.run_task(wipe, priority=DBPriority.IMMEDIATE)


def _attach(server, sha256: str, entity_type: str, entity_id: int) -> None:
    def add(session: Session):
        session.add(
            AdapterAttachment(
                adapter_sha256=sha256, entity_type=entity_type, entity_id=entity_id
            )
        )
        session.commit()

    server.vault.db.run_task(add, priority=DBPriority.IMMEDIATE)


@pytest.fixture(scope="module")
def shelf_env():
    """One Server, one owner login, one character and one set to attach to."""
    tmp = tempfile.TemporaryDirectory()
    config_path = f"{tmp.name}/server-config.json"
    # trusted_proxies lets a test spoof the real client IP through
    # X-Forwarded-For, which is the only way to exercise the LOCAL_OWNER_ONLY
    # locality half. Without the header the in-process peer reads as loopback,
    # so it changes nothing for the other tests.
    with open(config_path, "w") as handle:
        json.dump({"port": 8000, "trusted_proxies": ["testclient"]}, handle)
    server = Server(config_path)
    server.__enter__()
    try:
        owner = TestClient(server.api, raise_server_exceptions=True)
        r = owner.post(
            f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
        )
        assert r.status_code == 200, r.text

        r = owner.post(f"{API}/characters", json={"name": "Shelf Character"})
        assert r.status_code in {200, 201}, r.text
        character_id = r.json().get("id") or r.json()["character"]["id"]

        r = owner.post(f"{API}/picture_sets", json={"name": "Shelf Set"})
        assert r.status_code in {200, 201}, r.text
        set_id = r.json()["picture_set"]["id"]

        yield SimpleNamespace(
            server=server, owner=owner, character_id=character_id, set_id=set_id
        )
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


@pytest.fixture(autouse=True)
def fresh_shelf(shelf_env):
    """Re-seed the shelf and re-mint credentials before every test.

    Identity, not counts: every assertion below names the rows it expects, so
    accumulated state in the shared server cannot make one pass for the wrong
    reason.
    """
    server = shelf_env.server
    shelf_env.model_ids = _seed_hub(server)
    _clear_attachments(server)
    # The owner session is what every positive control runs on; prove it is live
    # before any refusal is measured against it.
    r = shelf_env.owner.get(f"{API}/adapters")
    assert r.status_code == 200, (
        f"the shared owner session cannot read the shelf ({r.status_code}: "
        f"{r.text}) — every refusal below would prove nothing"
    )
    yield shelf_env


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


def _names(payload: list[dict]) -> set[str]:
    return {row["filename"] for row in payload}


# ===========================================================================
# Declarations — the registry entry is the route's only authorization
# ===========================================================================


def test_every_shelf_route_is_declared_owner_only():
    """§16.1: the declaration IS the enforcement. A missing entry is a 403 at
    runtime and a red guardrail, so pin the three cells explicitly."""
    for key in _SHELF_ROUTES:
        assert key in ROUTE_POLICIES, f"{key} has no ROUTE_POLICIES entry"
        declared = ROUTE_POLICIES[key]
        assert declared.policy is AccessPolicy.OWNER_ONLY, (
            f"{key} declares {declared.policy}, not OWNER_ONLY"
        )
        assert declared.library_independent is False, (
            f"{key} exempts itself from the library pin; the shelf joins hub "
            "content to the active vault's attachments and must stay pinned"
        )


# ===========================================================================
# The list, and the three decisions B5 had to make
# ===========================================================================


def test_adapters_list_returns_adapters_only(shelf_env):
    r = shelf_env.owner.get(f"{API}/adapters")
    assert r.status_code == 200, r.text
    assert _names(r.json()["adapters"]) == {
        "alice.safetensors",
        "bob.safetensors",
        "sd_xl_noname.safetensors",
        "dana.safetensors",
    }


def test_null_base_model_rows_are_listed_not_dropped(shelf_env):
    """DoD 5: a null base model is a bulk state. It must reach the client as
    ``null`` on a listed row, never as a dropped row or a coerced string."""
    rows = shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    by_name = {row["filename"]: row for row in rows}
    assert by_name["sd_xl_noname.safetensors"]["base_model"] is None
    assert by_name["dana.safetensors"]["base_model"] is None
    assert by_name["alice.safetensors"]["base_model"] == "SDXL 1.0"


def test_base_model_filter_carries_not_set_as_an_explicit_value(shelf_env):
    """The picker's "Not set" option selects exactly the null rows, and the
    named-value filter excludes them."""
    r = shelf_env.owner.get(f"{API}/adapters", params={"base_model": "UNASSIGNED"})
    assert r.status_code == 200, r.text
    assert _names(r.json()["adapters"]) == {
        "sd_xl_noname.safetensors",
        "dana.safetensors",
    }

    r = shelf_env.owner.get(f"{API}/adapters", params={"base_model": "SDXL 1.0"})
    assert _names(r.json()["adapters"]) == {"alice.safetensors"}


def test_unknown_is_neither_adapter_nor_checkpoint_by_default(shelf_env):
    """DoD 5: ``unknown`` never renders as a checkpoint. It is absent from both
    lists until it is asked for by name, and ``/checkpoints`` never serves it."""
    adapters = shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    assert "mystery.safetensors" not in _names(adapters)

    checkpoints = shelf_env.owner.get(f"{API}/checkpoints").json()["checkpoints"]
    assert "mystery.safetensors" not in _names(checkpoints)
    assert all(row["file_kind"] == "checkpoint" for row in checkpoints)

    r = shelf_env.owner.get(f"{API}/adapters", params={"file_kind": "unknown"})
    assert r.status_code == 200, r.text
    assert _names(r.json()["adapters"]) == {"mystery.safetensors"}


def test_checkpoint_cannot_be_requested_from_the_adapter_block(shelf_env):
    r = shelf_env.owner.get(f"{API}/adapters", params={"file_kind": "checkpoint"})
    assert r.status_code == 400, r.text
    assert "GET /checkpoints" in r.text


def test_unhashed_checkpoint_is_listed_and_addressed_by_id(shelf_env):
    """A checkpoint registers with ``sha256`` NULL. It must still be listable,
    and ``model.id`` (AUTOINCREMENT, never reissued) is its only identifier."""
    rows = shelf_env.owner.get(f"{API}/checkpoints").json()["checkpoints"]
    by_name = {row["filename"]: row for row in rows}
    assert set(by_name) == {"base_xl.safetensors", "huge_unhashed.safetensors"}

    unhashed = by_name["huge_unhashed.safetensors"]
    assert unhashed["sha256"] is None
    assert unhashed["id"] == shelf_env.model_ids["huge_unhashed.safetensors"]


def test_checkpoint_hash_is_not_served_by_the_adapter_detail_route(shelf_env):
    """The blocks stay separate: a hashed checkpoint is a real row, but it is not
    an adapter, and serving it from ``/adapters/{sha256}`` would fold the two."""
    r = shelf_env.owner.get(f"{API}/adapters/{CHECKPOINT_HASHED}")
    assert r.status_code == 404, r.text
    assert "checkpoint" in r.text


def test_adapter_detail_carries_locations(shelf_env):
    r = shelf_env.owner.get(f"{API}/adapters/{ADAPTER_WITH_BASE}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == "alice.safetensors"
    assert body["locations"] == [
        {
            "folder_id": 1,
            "folder_path": "/models/loras",
            "relpath": "alice.safetensors",
            "state": "present",
            "file_mtime": 11,
        }
    ]
    # An unknown is hashed on sight, so it is addressable here too.
    assert shelf_env.owner.get(f"{API}/adapters/{UNKNOWN_HASH}").status_code == 200


def test_search_escapes_sqlite_like_wildcards(shelf_env):
    """``sd_xl`` must not also match ``sdaxl``: an unescaped ``_`` is a wildcard."""
    r = shelf_env.owner.get(f"{API}/adapters", params={"q": "sd_xl"})
    assert _names(r.json()["adapters"]) == {"sd_xl_noname.safetensors"}
    r = shelf_env.owner.get(f"{API}/adapters", params={"q": "sdaxl"})
    assert r.json()["adapters"] == []


# ===========================================================================
# The cross-database filter — hub query + vault query, joined in Python
# ===========================================================================


def test_character_and_set_filters_read_the_vault_attachment_table(shelf_env):
    server = shelf_env.server
    _attach(server, ADAPTER_WITH_BASE, "character", shelf_env.character_id)
    _attach(server, ADAPTER_NO_BASE, "set", shelf_env.set_id)

    r = shelf_env.owner.get(
        f"{API}/adapters", params={"character_id": shelf_env.character_id}
    )
    assert r.status_code == 200, r.text
    assert _names(r.json()["adapters"]) == {"alice.safetensors"}

    r = shelf_env.owner.get(f"{API}/adapters", params={"set_id": shelf_env.set_id})
    assert _names(r.json()["adapters"]) == {"sd_xl_noname.safetensors"}

    # And an entity with nothing attached filters to empty rather than to all.
    r = shelf_env.owner.get(f"{API}/adapters", params={"character_id": 999999})
    assert r.json()["adapters"] == []


def test_attachments_are_returned_on_the_list_not_one_row_at_a_time(shelf_env):
    """The shelf shows who uses a LoRA. Serving that on the list is what keeps
    F3 from issuing one detail request per row."""
    _attach(shelf_env.server, ADAPTER_WITH_BASE, "character", shelf_env.character_id)
    rows = shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    by_name = {row["filename"]: row for row in rows}
    assert by_name["alice.safetensors"]["attachments"] == [
        {"entity_type": "character", "entity_id": shelf_env.character_id}
    ]
    assert by_name["dana.safetensors"]["attachments"] == []


def test_character_and_set_filters_are_mutually_exclusive(shelf_env):
    r = shelf_env.owner.get(
        f"{API}/adapters",
        params={"character_id": shelf_env.character_id, "set_id": shelf_env.set_id},
    )
    assert r.status_code == 400, r.text


# ===========================================================================
# Authorization — both directions, on every route
# ===========================================================================


def _shelf_paths() -> list[str]:
    return [
        f"{API}/adapters",
        f"{API}/adapters/{ADAPTER_WITH_BASE}",
        f"{API}/checkpoints",
    ]


def test_owner_reaches_every_shelf_route(shelf_env):
    """The positive direction. Over-blocking is its own regression."""
    for path in _shelf_paths():
        r = shelf_env.owner.get(path)
        assert r.status_code == 200, (
            f"{path} refused the owner: {r.status_code} {r.text}"
        )


def test_resource_scoped_share_token_is_refused_on_every_shelf_route(shelf_env):
    """The negative direction, with a live-credential control in front of it: the
    same token performs an in-scope read first, so a 403 below is a scope
    refusal and not a dead token."""
    token = _mint(
        shelf_env.owner,
        "shelf character token",
        resource_type="character",
        resource_id=shelf_env.character_id,
    )
    client = _bearer(shelf_env.server, token)

    control = client.get(f"{API}/pictures")
    assert control.status_code == 200, (
        f"the freshly minted scoped token cannot do an in-scope read "
        f"({control.status_code}: {control.text}) — the refusals below would "
        "prove nothing"
    )

    for path in _shelf_paths():
        r = client.get(path)
        assert r.status_code == 403, (
            f"{path} served a resource-scoped share token: {r.status_code} {r.text}"
        )


def test_unscoped_read_token_is_refused_on_every_shelf_route(shelf_env):
    """``OWNER_ONLY`` rejects an unscoped READ token too, not only a
    resource-scoped one — this is the sibling vector that the scope-filter
    policies (``SCOPED_LIST``) would let through."""
    token = _mint(shelf_env.owner, "shelf global read token")
    client = _bearer(shelf_env.server, token)

    control = client.get(f"{API}/pictures")
    assert control.status_code == 200, (
        f"the unscoped READ token cannot read at all ({control.status_code}: "
        f"{control.text}) — the refusals below would prove nothing"
    )

    for path in _shelf_paths():
        r = client.get(path)
        assert r.status_code == 403, (
            f"{path} served an unscoped READ token: {r.status_code} {r.text}"
        )


def test_unauthenticated_is_refused_on_every_shelf_route(shelf_env):
    anon = TestClient(shelf_env.server.api)
    for path in _shelf_paths():
        r = anon.get(path)
        assert r.status_code == 401, (
            f"{path} answered an unauthenticated caller: {r.status_code} {r.text}"
        )


# ===========================================================================
# model_folder CRUD + rescan (shelf plan B5, part 2)
# ===========================================================================


def _new_folder_path(tmp_root: str, name: str) -> str:
    path = os.path.join(tmp_root, name)
    os.makedirs(path, exist_ok=True)
    return path


def test_registering_a_folder_derives_movable_and_owner(shelf_env, tmp_path):
    """``movable`` and ``owner`` follow from ``kind`` and are not caller inputs:
    offering them would let a caller register a combination that means nothing."""
    r = shelf_env.owner.post(
        f"{API}/model-folders",
        json={"path": _new_folder_path(str(tmp_path), "loras")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["kind"], body["movable"], body["owner"]) == ("user", "per_item", None)

    r = shelf_env.owner.post(
        f"{API}/model-folders",
        json={"path": _new_folder_path(str(tmp_path), "runs"), "kind": "source"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["kind"], body["movable"], body["owner"]) == (
        "source",
        "external",
        "ai-toolkit",
    )


def test_managed_and_foreign_folders_are_not_creatable_over_http(shelf_env, tmp_path):
    """PixlStash registers those for itself (tagger artifacts, InsightFace, the
    HuggingFace cache); a hand-made row would collide with that registration."""
    for kind in ("managed", "foreign", "nonsense"):
        r = shelf_env.owner.post(
            f"{API}/model-folders",
            json={"path": _new_folder_path(str(tmp_path), f"k-{kind}"), "kind": kind},
        )
        assert r.status_code == 400, f"{kind}: {r.status_code} {r.text}"


def test_registering_the_same_folder_twice_conflicts(shelf_env, tmp_path):
    path = _new_folder_path(str(tmp_path), "dupe")
    assert (
        shelf_env.owner.post(f"{API}/model-folders", json={"path": path}).status_code
        == 200
    )
    r = shelf_env.owner.post(f"{API}/model-folders", json={"path": path})
    assert r.status_code == 409, r.text


def test_a_system_directory_is_refused(shelf_env):
    r = shelf_env.owner.post(f"{API}/model-folders", json={"path": "/etc"})
    assert r.status_code == 400, r.text


def test_folder_list_counts_copies_and_reports_the_seeded_folder(shelf_env):
    r = shelf_env.owner.get(f"{API}/model-folders")
    assert r.status_code == 200, r.text
    folders = {row["path"]: row for row in r.json()["folders"]}
    assert folders["/models/loras"]["file_count"] == len(_SEED_MODELS)


def test_patch_changes_the_bind_path_but_never_the_registered_path(shelf_env):
    r = shelf_env.owner.patch(
        f"{API}/model-folders/1",
        json={"host_path": "/host/models", "delete_after_import": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["host_path"] == "/host/models"
    assert r.json()["delete_after_import"] is True
    assert r.json()["path"] == "/models/loras"

    # `path` is not a field on the update schema at all: relocating a folder is
    # a copy-verify-repoint operation (B7), not an edit.
    r = shelf_env.owner.patch(f"{API}/model-folders/1", json={"path": "/elsewhere"})
    assert r.status_code == 422, r.text


def test_forgetting_a_folder_tombstones_and_keeps_every_model(shelf_env):
    """Shelf plan §7: removal drops the location rows and keeps the model rows
    with their curation, which is what lets it skip a confirmation prompt. If
    this ever hard-deletes, the confirmation has to come back."""
    before = shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    assert _names(before) == {
        "alice.safetensors",
        "bob.safetensors",
        "sd_xl_noname.safetensors",
        "dana.safetensors",
    }

    r = shelf_env.owner.delete(f"{API}/model-folders/1")
    assert r.status_code == 200, r.text
    assert r.json()["tombstoned_files"] == len(_SEED_MODELS)

    after = shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    assert _names(after) == _names(before), (
        "forgetting a folder deleted model rows; the shelf's no-confirmation "
        "rule depends on this being a tombstone"
    )
    assert all(row["locations"] == [] for row in after)
    # Identity, not just count: the curation survived, which is the whole point.
    assert {row["filename"]: row["display_name"] for row in after} == {
        row["filename"]: row["display_name"] for row in before
    }
    assert shelf_env.owner.get(f"{API}/model-folders").json()["folders"] == []


def test_rescan_of_a_missing_folder_reports_started_and_does_not_raise(shelf_env):
    """The seeded folder does not exist on disk, so the scan marks it
    unreachable. The route still answers 202 immediately: it must never block on
    reading a folder of 1,800 adapters."""
    r = shelf_env.owner.post(f"{API}/model-folders/1/rescan")
    assert r.status_code == 202, r.text
    assert r.json() == {"status": "started", "id": 1}


def test_rescan_skips_a_source_folder(shelf_env, tmp_path):
    """`source` folders are taken FROM, never catalogued in place."""
    r = shelf_env.owner.post(
        f"{API}/model-folders",
        json={"path": _new_folder_path(str(tmp_path), "src"), "kind": "source"},
    )
    folder_id = r.json()["id"]
    r = shelf_env.owner.post(f"{API}/model-folders/{folder_id}/rescan")
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "skipped"


def test_unknown_folder_id_is_404_on_every_mutator(shelf_env):
    assert (
        shelf_env.owner.patch(f"{API}/model-folders/999999", json={}).status_code == 404
    )
    assert shelf_env.owner.delete(f"{API}/model-folders/999999").status_code == 404
    assert shelf_env.owner.post(f"{API}/model-folders/999999/rescan").status_code == 404


# ---- authorization: the read tier vs the §16.3 locality tier ---------------

_FOLDER_MUTATORS = (
    ("POST", f"{API}/model-folders", {"json": {"path": "/tmp/pixlstash-authz-probe"}}),
    ("PATCH", f"{API}/model-folders/1", {"json": {}}),
    ("DELETE", f"{API}/model-folders/1", {}),
    ("POST", f"{API}/model-folders/1/rescan", {}),
)


def _xff(ip: str) -> dict:
    return {"X-Forwarded-For": ip}


def test_folder_list_is_owner_only_in_both_directions(shelf_env):
    assert shelf_env.owner.get(f"{API}/model-folders").status_code == 200

    token = _mint(shelf_env.owner, "folder list probe")
    client = _bearer(shelf_env.server, token)
    assert client.get(f"{API}/pictures").status_code == 200, (
        "the READ token is dead; the refusal below would prove nothing"
    )
    assert client.get(f"{API}/model-folders").status_code == 403


def test_local_owner_reaches_every_folder_mutator(shelf_env):
    """The positive direction of the §16.3 tier: loopback, RFC1918 LAN and
    Tailscale CGNAT all pass. Over-blocking is its own regression, and the
    Tailscale case is the one that was a false deny before the scoped predicate."""
    for method, path, kwargs in _FOLDER_MUTATORS:
        for headers in ({}, _xff("192.168.1.9"), _xff("100.64.0.5")):
            r = shelf_env.owner.request(method, path, headers=headers, **kwargs)
            assert "restricted to local" not in r.text, (
                f"{method} {path} from {headers or 'loopback'} was refused as "
                f"non-local: {r.status_code} {r.text}"
            )


def test_remote_owner_is_refused_on_every_folder_mutator_naming_the_flag(shelf_env):
    """The negative direction. A public client IP is 403'd and the message names
    ``allow_remote_host_ops``, exactly as the reference-folder block does."""
    for method, path, kwargs in _FOLDER_MUTATORS:
        r = shelf_env.owner.request(method, path, headers=_xff("8.8.8.8"), **kwargs)
        assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"
        assert "allow_remote_host_ops" in r.text, (
            f"{method} {path} denied without naming the setting that enables it: "
            f"{r.text}"
        )


def test_remote_owner_is_admitted_when_the_flag_is_set(shelf_env):
    """The flag is what separates this tier from the loopback red line; if it did
    nothing here, the tier would silently be the stricter one."""
    config = shelf_env.server.auth._server_config
    previous = config.get("allow_remote_host_ops")
    config["allow_remote_host_ops"] = True
    try:
        for method, path, kwargs in _FOLDER_MUTATORS:
            r = shelf_env.owner.request(method, path, headers=_xff("8.8.8.8"), **kwargs)
            assert "restricted to local" not in r.text, (
                f"{method} {path} stayed refused with allow_remote_host_ops=true: "
                f"{r.status_code} {r.text}"
            )
    finally:
        if previous is None:
            config.pop("allow_remote_host_ops", None)
        else:
            config["allow_remote_host_ops"] = previous


def test_share_tokens_never_reach_a_folder_mutator(shelf_env):
    """No share token reaches these four, and this pins **which layer** says so.

    Be precise about what this proves, because the alternative is a decorative
    test. All four mutators are non-GET, and the auth middleware blocks every
    non-GET for a READ token ahead of routing; every resource-scoped token is a
    READ token (``ALL``+``resource_type`` is refused at mint and fail-closed at
    the middleware). So the 403 below is the **middleware's**, and the gate's
    ``LOCAL_OWNER_ONLY`` owner half is defence in depth that no HTTP request can
    observe independently — there exists no credential that reaches the gate on
    these routes and fails its owner check. Verified by mutation: deleting
    ``_enforce_unscoped_owner`` from the gate's ``LOCAL_OWNER_ONLY`` branch does
    **not** turn this file red, and that is a property of the system rather than
    a hole in this test. It is flagged to the adversarial review rather than
    papered over, and it is not specific to the shelf: it holds for all 18
    routes on the tier.

    What this test does own: the block is real, the credential is live, and the
    routes exist. ``assert_real_route`` is load-bearing — middleware answers 403
    before routing, so a renamed route would 403 identically and the assertion
    would dissolve into nothing.
    """
    token = _mint(
        shelf_env.owner,
        "folder mutator probe",
        resource_type="character",
        resource_id=shelf_env.character_id,
    )
    client = _bearer(shelf_env.server, token)
    assert client.get(f"{API}/pictures").status_code == 200, (
        "the scoped token is dead; the refusals below would prove nothing"
    )
    for method, path, kwargs in _FOLDER_MUTATORS:
        assert_real_route(shelf_env.server.api, method, path)
        r = client.request(method, path, **kwargs)
        assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"


# ===========================================================================
# PUT /adapters/{sha256}/attachments — the assignment path (B5, part 3)
# ===========================================================================


def _attachments_url(sha256: str) -> str:
    return f"{API}/adapters/{sha256}/attachments"


def test_put_attachments_replaces_the_whole_set(shelf_env):
    """PUT, not PATCH: the shelf hands over the state it wants. Computing a delta
    client-side would let two open tabs interleave into a set neither chose."""
    _attach(shelf_env.server, ADAPTER_WITH_BASE, "set", shelf_env.set_id)

    r = shelf_env.owner.put(
        _attachments_url(ADAPTER_WITH_BASE),
        json=[{"entity_type": "character", "entity_id": shelf_env.character_id}],
    )
    assert r.status_code == 200, r.text
    assert r.json()["attachments"] == [
        {"entity_type": "character", "entity_id": shelf_env.character_id}
    ], "the pre-existing set attachment survived a full replacement"

    # And the list route agrees, which is what the shelf will actually render.
    rows = shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    by_name = {row["filename"]: row for row in rows}
    assert by_name["alice.safetensors"]["attachments"] == [
        {"entity_type": "character", "entity_id": shelf_env.character_id}
    ]


def test_put_empty_list_detaches_everything(shelf_env):
    _attach(shelf_env.server, ADAPTER_WITH_BASE, "character", shelf_env.character_id)
    r = shelf_env.owner.put(_attachments_url(ADAPTER_WITH_BASE), json=[])
    assert r.status_code == 200, r.text
    assert r.json()["attachments"] == []


def test_put_deduplicates_on_the_composite_key(shelf_env):
    r = shelf_env.owner.put(
        _attachments_url(ADAPTER_WITH_BASE),
        json=[
            {"entity_type": "character", "entity_id": shelf_env.character_id},
            {"entity_type": "character", "entity_id": shelf_env.character_id},
        ],
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["attachments"]) == 1


def test_put_refuses_an_entity_that_does_not_exist_and_writes_nothing(shelf_env):
    """``adapter_attachment`` carries no foreign key — it cannot, its other end
    is in the hub — so a typo'd id would sit there invisible and permanent.
    The check runs before the delete, so a refused call is a no-op."""
    _attach(shelf_env.server, ADAPTER_WITH_BASE, "character", shelf_env.character_id)

    r = shelf_env.owner.put(
        _attachments_url(ADAPTER_WITH_BASE),
        json=[{"entity_type": "character", "entity_id": 999999}],
    )
    assert r.status_code == 404, r.text

    r = shelf_env.owner.get(f"{API}/adapters/{ADAPTER_WITH_BASE}")
    assert r.json()["attachments"] == [
        {"entity_type": "character", "entity_id": shelf_env.character_id}
    ], "a refused attachment write still wiped the existing set"


def test_put_refuses_an_unknown_entity_type(shelf_env):
    r = shelf_env.owner.put(
        _attachments_url(ADAPTER_WITH_BASE),
        json=[{"entity_type": "project", "entity_id": 1}],
    )
    assert r.status_code == 400, r.text


def test_put_refuses_a_checkpoint_and_an_unknown_hash(shelf_env):
    """Attachment means "this character uses this LoRA". A base model is not
    something a character uses in that sense, and the table is keyed by a hash a
    checkpoint may not have yet."""
    r = shelf_env.owner.put(_attachments_url(CHECKPOINT_HASHED), json=[])
    assert r.status_code == 400, r.text

    r = shelf_env.owner.put(_attachments_url(_h("nosuchmodel")), json=[])
    assert r.status_code == 404, r.text


def test_put_attaches_an_unclassified_file(shelf_env):
    """An ``unknown`` is hashed on sight and is most likely an adapter format we
    have not met yet, so it must be assignable while it waits for a correction."""
    r = shelf_env.owner.put(
        _attachments_url(UNKNOWN_HASH),
        json=[{"entity_type": "set", "entity_id": shelf_env.set_id}],
    )
    assert r.status_code == 200, r.text
    assert r.json()["attachments"] == [
        {"entity_type": "set", "entity_id": shelf_env.set_id}
    ]


def test_attachment_write_is_owner_only_in_both_directions(shelf_env):
    """Positive: the owner writes. Negative: no share token does.

    Same caveat as the folder mutators — PUT is a non-GET, so the middleware's
    READ-token write block answers before the gate's ``OWNER_ONLY`` check. The
    block is the live enforcement; ``assert_real_route`` is what stops a renamed
    route from 403ing identically and making this vacuous.
    """
    assert (
        shelf_env.owner.put(_attachments_url(ADAPTER_WITH_BASE), json=[]).status_code
        == 200
    )

    for description, restriction in (
        (
            "attachment scoped probe",
            {"resource_type": "character", "resource_id": shelf_env.character_id},
        ),
        ("attachment unscoped probe", {}),
    ):
        token = _mint(shelf_env.owner, description, **restriction)
        client = _bearer(shelf_env.server, token)
        assert client.get(f"{API}/pictures").status_code == 200, (
            f"{description} is dead; the refusal below would prove nothing"
        )
        assert_real_route(
            shelf_env.server.api, "PUT", _attachments_url(ADAPTER_WITH_BASE)
        )
        r = client.put(_attachments_url(ADAPTER_WITH_BASE), json=[])
        assert r.status_code == 403, f"{description}: {r.status_code} {r.text}"


# ===========================================================================
# Sorting (B7) — the aggregates are in the list query, not per row
# ===========================================================================


def _order(shelf_env, **params) -> list[str]:
    r = shelf_env.owner.get(f"{API}/adapters", params=params)
    assert r.status_code == 200, r.text
    return [row["filename"] for row in r.json()["adapters"]]


def test_sort_keys_are_exactly_the_five_that_were_ruled():
    """The route's ``Literal`` and the SQL builder's map are two spellings of
    one decision. If they drift, an accepted query key builds no ORDER BY, or a
    ruled key becomes a 422."""
    from typing import get_args

    from pixlstash.routes.model_shelf import SortKey
    from pixlstash.services.model_shelf_service import SORT_KEYS

    assert set(get_args(SortKey)) == set(SORT_KEYS)
    assert set(SORT_KEYS) == {
        "added_at",
        "file_mtime",
        "name",
        "size",
        "base_model",
    }


def test_default_sort_is_newest_added_first(shelf_env):
    """Ruled default. Alice and Dana are one stack, so both carry the stack's
    newest member date (Dana's, 08-04) and lead — Alice's own 08-01 would put her
    last, which is exactly the per-row reading this must not do."""
    assert _order(shelf_env) == [
        "alice.safetensors",
        "dana.safetensors",
        "sd_xl_noname.safetensors",
        "bob.safetensors",
    ]


def test_a_stacked_row_sorts_by_the_stack_total_not_the_cover(shelf_env):
    """Size is the sum of all members and is displayed as such, because a cover
    understates a run by about six times in the column the shelf exists to
    answer. Alice's own file is the *smallest* of the four; her stack's total is
    second largest, and that is where she must land."""
    assert _order(shelf_env, sort="size") == [
        "bob.safetensors",  # 5000, unstacked
        "alice.safetensors",  # stack total 4000, own file 1000
        "dana.safetensors",  # same stack, same total; tie-broken by name
        "sd_xl_noname.safetensors",  # 2000
    ]


def test_stack_aggregates_are_on_the_row(shelf_env):
    """The numbers the row displays come back with it, so the shelf never has to
    ask a second question per row."""
    rows = {
        row["filename"]: row
        for row in shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    }
    alice = rows["alice.safetensors"]
    assert alice["stack_id"] == STACK_ID
    assert alice["member_count"] == 2
    assert alice["total_size"] == 4000
    assert alice["newest_member_at"] == "2026-08-04T00:00:00Z"

    standalone = rows["bob.safetensors"]
    assert standalone["member_count"] is None
    assert standalone["total_size"] is None
    assert standalone["newest_member_at"] is None


def test_nulls_sort_last_in_both_directions(shelf_env):
    """Two of six rows in the design's own mock have no name and no base model,
    and 37 % of a measured real folder records neither. A user who flips the
    direction is not asking for 900 unnamed rows at the top."""
    ascending = _order(shelf_env, sort="name", direction="asc")
    descending = _order(shelf_env, sort="name", direction="desc")
    assert ascending == [
        "alice.safetensors",
        "bob.safetensors",
        "dana.safetensors",
        "sd_xl_noname.safetensors",
    ]
    assert descending == [
        "dana.safetensors",
        "bob.safetensors",
        "alice.safetensors",
        "sd_xl_noname.safetensors",
    ]
    assert ascending[-1] == descending[-1] == "sd_xl_noname.safetensors"


def test_file_modified_sorts_on_the_newest_present_copy(shelf_env):
    """``file_mtime`` is per *copy*, and a model can have several. It is also the
    one date that is NOT stack-aggregated: a stack's members were written at
    different times and the row shows the newest of its own copies."""
    assert _order(shelf_env, sort="file_mtime") == [
        "bob.safetensors",  # 55
        "dana.safetensors",  # 44
        "sd_xl_noname.safetensors",  # 22
        "alice.safetensors",  # 11
    ]


def test_a_missing_copy_contributes_no_modification_date(shelf_env):
    """A ``missing`` row's mtime is the last thing we saw, not the last thing
    that happened, so it must not answer "when was this modified". With no
    present copy the row has no date and sorts last in either direction."""
    with shelf_env.server.hub.transaction() as conn:
        conn.execute(
            "UPDATE model_file SET state = 'missing' WHERE relpath = 'bob.safetensors'"
        )
    rows = {
        row["filename"]: row
        for row in shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    }
    assert rows["bob.safetensors"]["newest_file_mtime"] is None
    assert rows["dana.safetensors"]["newest_file_mtime"] == 44
    for direction in ("asc", "desc"):
        assert (
            _order(shelf_env, sort="file_mtime", direction=direction)[-1]
            == "bob.safetensors"
        )


def test_base_model_sort_puts_the_unrecorded_rows_last(shelf_env):
    assert _order(shelf_env, sort="base_model", direction="asc") == [
        "bob.safetensors",  # Flux.1 dev
        "alice.safetensors",  # SDXL 1.0
        "dana.safetensors",  # none; tie-broken by name, nulls last
        "sd_xl_noname.safetensors",  # none, and no name either
    ]


def test_checkpoints_sort_too(shelf_env):
    r = shelf_env.owner.get(f"{API}/checkpoints", params={"sort": "size"})
    assert r.status_code == 200, r.text
    assert [row["filename"] for row in r.json()["checkpoints"]] == [
        "huge_unhashed.safetensors",
        "base_xl.safetensors",
    ]


def test_an_unknown_sort_key_is_refused_before_it_reaches_the_sql(shelf_env):
    r = shelf_env.owner.get(f"{API}/adapters", params={"sort": "id; DROP TABLE model"})
    assert r.status_code == 422, r.text
    assert shelf_env.owner.get(f"{API}/adapters").status_code == 200


def test_sorting_by_an_aggregate_is_still_two_hub_queries(shelf_env):
    """The claim B5 left standing and B7 had to keep: the list is one SELECT for
    the rows and one for the locations, whatever the sort and *whatever the row
    count*. 1,806 rows sorted by "the total size of the stack this row belongs
    to" is otherwise the textbook N+1."""
    server = shelf_env.server
    calls: list[str] = []
    original = server.hub.fetchall

    def counting(sql, params=()):
        calls.append(sql)
        return original(sql, params)

    server.hub.fetchall = counting
    try:
        assert (
            shelf_env.owner.get(f"{API}/adapters", params={"sort": "size"}).status_code
            == 200
        )
        with_four_rows = len(calls)

        # Twenty more adapters, ten of them in a second stack.
        calls.clear()
        with server.hub.transaction() as conn:
            conn.execute("INSERT INTO adapter_stack (id, name) VALUES (99, 'Bulk run')")
            for index in range(20):
                cursor = conn.execute(
                    "INSERT INTO model (file_kind, kind, sha256, filename, "
                    "provenance, file_size, created_at, stack_id, stack_position) "
                    "VALUES ('adapter', 'lora', ?, ?, 'external', ?, ?, ?, ?)",
                    (
                        _h(f"bulk{index}z"),
                        f"bulk_{index}.safetensors",
                        1000 + index,
                        f"2026-07-{index + 1:02d}T00:00:00Z",
                        99 if index < 10 else None,
                        index if index < 10 else None,
                    ),
                )
                conn.execute(
                    "INSERT INTO model_file (model_id, model_folder_id, relpath, "
                    "state, seen_at, file_mtime) VALUES (?, 1, ?, 'present', ?, ?)",
                    (
                        int(cursor.lastrowid),
                        f"bulk_{index}.safetensors",
                        "2026-08-09T00:00:00Z",
                        index,
                    ),
                )
        calls.clear()
        r = shelf_env.owner.get(f"{API}/adapters", params={"sort": "size"})
        assert r.status_code == 200, r.text
        assert len(r.json()["adapters"]) == 24
        assert len(calls) == with_four_rows == 2, (
            f"{len(calls)} hub queries for 24 rows vs {with_four_rows} for 4 — "
            "the list has grown a per-row lookup"
        )
    finally:
        server.hub.fetchall = original
