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
from tests.authz_guard import no_spa_fallback  # noqa: F401

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

# (sha256, file_kind, kind, display_name, filename, base_model)
_SEED_MODELS = (
    (ADAPTER_WITH_BASE, "adapter", "lora", "Alice", "alice.safetensors", "SDXL 1.0"),
    (ADAPTER_WITH_BASE_2, "adapter", "lokr", "Bob", "bob.safetensors", "Flux.1 dev"),
    (ADAPTER_NO_BASE, "adapter", "lora", None, "sd_xl_noname.safetensors", None),
    (ADAPTER_NO_BASE_2, "adapter", "lora", "Dana", "dana.safetensors", None),
    (UNKNOWN_HASH, "unknown", None, None, "mystery.safetensors", None),
    (
        CHECKPOINT_HASHED,
        "checkpoint",
        None,
        "Base XL",
        "base_xl.safetensors",
        "SDXL 1.0",
    ),
    # The one that has no hash yet: a 24 GB file the hash finder has not read.
    (None, "checkpoint", None, None, "huge_unhashed.safetensors", None),
)


def _seed_hub(server) -> dict[str, int]:
    """Write the shelf tables from scratch. Returns filename -> model.id."""
    with server.hub.transaction() as conn:
        conn.execute("DELETE FROM model_file")
        conn.execute("DELETE FROM model")
        conn.execute("DELETE FROM model_folder")
        conn.execute(
            "INSERT INTO model_folder (id, path, kind, movable, created_at) "
            "VALUES (1, '/models/loras', 'user', 'per_item', '2026-08-09T00:00:00Z')"
        )
        ids: dict[str, int] = {}
        for sha, file_kind, kind, display_name, filename, base_model in _SEED_MODELS:
            cursor = conn.execute(
                "INSERT INTO model (file_kind, kind, sha256, display_name, filename, "
                "base_model, provenance, file_size, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'external', 4096, '2026-08-09T00:00:00Z')",
                (file_kind, kind, sha, display_name, filename, base_model),
            )
            model_id = int(cursor.lastrowid)
            ids[filename] = model_id
            conn.execute(
                "INSERT INTO model_file (model_id, model_folder_id, relpath, state, "
                "seen_at, file_mtime) VALUES (?, 1, ?, 'present', ?, 17)",
                (model_id, filename, "2026-08-09T00:00:00Z"),
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
    server = Server(f"{tmp.name}/server-config.json")
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
            "file_mtime": 17,
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
