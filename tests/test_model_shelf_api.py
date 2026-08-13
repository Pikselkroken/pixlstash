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

import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from pixlstash.authz.policy import AccessPolicy
from pixlstash.authz.registry import ROUTE_POLICIES
from pixlstash.database import DBPriority
from pixlstash.db_models.adapter_attachment import AdapterAttachment
from pixlstash.routes import model_folders as model_folders_routes
from pixlstash.routes.model_imports import sample_path_within
from pixlstash.routes.model_shelf import MAX_ATTACHMENTS_PER_MODEL
from pixlstash.server import Server
from pixlstash.services.model_folder_scanner import ModelFolderScanner
from pixlstash.services.model_mover import SHELF_IO_LOCK
from tests.authz_guard import assert_real_route, no_spa_fallback  # noqa: F401
from tests.test_model_folder_scanner import write_adapter, write_checkpoint

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
    # The seed deletes model_folder rows behind the API's back, so the rowids it
    # frees can come back attached to a different folder. Drop the remembered
    # scans with them, as DELETE /model-folders does on the real path.
    with model_folders_routes._scans_lock:
        model_folders_routes._scans.clear()
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
    """Every OWNER_ONLY shelf GET.

    One list, three tests: the owner reaches all of them, a resource-scoped
    share token reaches none, and an unscoped READ token reaches none. Adding a
    route here is what makes that completeness arithmetic rather than a
    judgement call, so a new GET belongs in this list on the day it ships.
    """
    return [
        f"{API}/adapters",
        f"{API}/adapters/{ADAPTER_WITH_BASE}",
        f"{API}/checkpoints",
        f"{API}/model-stacks/proposals",
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


def test_a_symlink_into_a_system_directory_is_refused(shelf_env, tmp_path):
    """The blocklist is a string comparison, so it has to run on the resolved
    path: ``/home/u/models-link -> /etc`` passes the lexical check, and the scan
    then walks /etc because ``os.walk`` follows the *top-level* link. ``GET
    /adapters`` is reachable from any network location, so the walk's filenames
    leave the host.
    """
    assert_real_route(shelf_env.server.api, "POST", f"{API}/model-folders")
    link = tmp_path / "models-link"
    try:
        link.symlink_to("/etc", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")

    r = shelf_env.owner.post(f"{API}/model-folders", json={"path": str(link)})
    assert r.status_code == 400, r.text
    # The refusal must be the blocklist's, not an incidental 4xx: a generic
    # failure here would leave the bypass open the moment the incident changed.
    assert "restricted system directory" in r.json()["detail"], r.text
    registered = {
        row["path"]
        for row in shelf_env.owner.get(f"{API}/model-folders").json()["folders"]
    }
    assert registered == {"/models/loras"}, registered


def test_a_symlink_to_an_allowed_folder_registers_at_its_resolved_path(
    shelf_env, tmp_path
):
    """The positive direction: resolving must refuse system directories without
    refusing a symlinked model folder, which is an ordinary way to keep adapters
    on a second drive. The row stores the resolved path, so it names the
    directory the scanner actually walks."""
    real = tmp_path / "real-loras"
    real.mkdir()
    link = tmp_path / "linked-loras"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")

    r = shelf_env.owner.post(f"{API}/model-folders", json={"path": str(link)})
    assert r.status_code == 200, r.text
    assert r.json()["path"] == os.path.realpath(str(real))

    r = shelf_env.owner.post(f"{API}/model-folders/{r.json()['id']}/rescan")
    assert r.status_code == 202, r.text


def test_a_path_that_is_not_a_directory_is_refused(shelf_env, tmp_path):
    """An existing directory or nothing: a file or a missing path registers a
    folder that can only ever scan as unreachable."""
    a_file = tmp_path / "adapter.safetensors"
    a_file.write_bytes(b"not a folder")
    for candidate in (str(a_file), str(tmp_path / "does-not-exist")):
        r = shelf_env.owner.post(f"{API}/model-folders", json={"path": candidate})
        assert r.status_code == 400, f"{candidate}: {r.status_code} {r.text}"
        assert "not a directory" in r.json()["detail"], r.text


def test_the_filesystem_root_is_refused(shelf_env):
    """``/`` is absolute, is no blocklist entry and is prefixed by none, so the
    blocklist alone admits it — and a rescan of it then stats every file on every
    mounted volume and SHA-256s every adapter on the machine, with a server
    restart as the only off switch. It is refused for containing the vault."""
    assert_real_route(shelf_env.server.api, "POST", f"{API}/model-folders")
    r = shelf_env.owner.post(f"{API}/model-folders", json={"path": "/"})
    assert r.status_code == 409, r.text
    assert "PixlStash data folder" in r.json()["detail"], r.text


def test_the_vault_data_folder_is_refused(shelf_env):
    r = shelf_env.owner.post(
        f"{API}/model-folders", json={"path": shelf_env.server.vault.image_root}
    )
    assert r.status_code == 409, r.text
    assert "PixlStash data folder" in r.json()["detail"], r.text


def test_a_folder_overlapping_a_registered_one_is_refused_in_both_directions(
    shelf_env, tmp_path
):
    """Two roots over the same files give every file a ``model_file`` row per
    root: double-counted in ``file_count`` and in a model's locations, and walked
    twice by the scanner. Nesting is refused whichever way round it arrives."""
    parent = _new_folder_path(str(tmp_path), "nest")
    child = _new_folder_path(parent, "inner")
    grandparent = str(tmp_path)

    assert (
        shelf_env.owner.post(f"{API}/model-folders", json={"path": parent}).status_code
        == 200
    )

    r = shelf_env.owner.post(f"{API}/model-folders", json={"path": child})
    assert r.status_code == 409, r.text
    assert "inside a registered model folder" in r.json()["detail"], r.text

    r = shelf_env.owner.post(f"{API}/model-folders", json={"path": grandparent})
    assert r.status_code == 409, r.text
    assert "is inside this path" in r.json()["detail"], r.text


def test_a_folder_beside_a_registered_one_still_registers(shelf_env, tmp_path):
    """The positive control for the containment rule: siblings do not overlap,
    and refusing them would be its own regression."""
    assert (
        shelf_env.owner.post(
            f"{API}/model-folders", json={"path": _new_folder_path(str(tmp_path), "a")}
        ).status_code
        == 200
    )
    r = shelf_env.owner.post(
        f"{API}/model-folders", json={"path": _new_folder_path(str(tmp_path), "b")}
    )
    assert r.status_code == 200, r.text


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
    reading a folder of 1,800 adapters. It answers with the id of the task now
    queued, which is what a client watches instead of guessing from
    ``last_checked``."""
    r = shelf_env.owner.post(f"{API}/model-folders/1/rescan")
    assert r.status_code == 202, r.text
    body = r.json()
    assert (body["status"], body["id"]) == ("started", 1)
    assert body["task_id"], body
    assert _await_scan(shelf_env, 1)["scan_status"] == "completed", (
        "an unreadable folder is a scan that succeeded and recorded "
        "'unreachable', not a scan that failed"
    )


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
    papered over, and it is not specific to the shelf.

    **What is NOT true — corrected by the 2026-08-09 security review:** the tier
    is not all non-GET. ``GET /filesystem/browse`` and
    ``GET /reference-folders/detect-sidecars`` are both ``LOCAL_OWNER_ONLY``, and
    for those two the middleware's non-GET rule says nothing. What refuses a
    share token there is ``READ_BLOCKED_GET_PATHS``, a hand-maintained frozenset
    with no guardrail tying it to ``ROUTE_POLICIES``: the review dropped one path
    from it and a resource-scoped READ token got a full host filesystem listing
    (200, not 403). So the reasoning above holds for **these four routes**
    because they are non-GET, not for the tier. A GET added to the tier without
    a matching ``READ_BLOCKED_GET_PATHS`` entry is a real hole and nothing goes
    red. Tracked as a follow-up; do not read this docstring as saying the tier
    is safe by construction.

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
# A rescan is a task, not a thread
# ===========================================================================

_TERMINAL_SCAN_STATES = ("completed", "failed", "cancelled")


def _register_folder_with_adapters(shelf_env, tmp_path, name, count) -> tuple[int, str]:
    """Register a folder holding *count* real adapters. Returns ``(id, path)``."""
    folder = tmp_path / name
    folder.mkdir()
    for index in range(count):
        write_adapter(folder / f"a{index}.safetensors")
    r = shelf_env.owner.post(f"{API}/model-folders", json={"path": str(folder)})
    assert r.status_code == 200, r.text
    return r.json()["id"], str(folder)


def _await_scan(shelf_env, folder_id, timeout=30.0) -> dict:
    """Poll the folder list until this folder's scan reaches a terminal state."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        for row in shelf_env.owner.get(f"{API}/model-folders").json()["folders"]:
            if row["id"] != folder_id:
                continue
            last = row
            if row["scan_status"] in _TERMINAL_SCAN_STATES:
                return row
        time.sleep(0.02)
    raise AssertionError(f"the scan never settled; last seen: {last}")


def test_a_rescan_is_a_task_with_progress_a_denominator_and_one_scan_per_folder(
    shelf_env, tmp_path, monkeypatch
):
    """The three things the bare daemon thread could not give.

    It ran unobserved: a 57 GB folder is minutes of hashing with nothing to
    show, a crash looked exactly like a slow read, and the thread was alive at
    interpreter shutdown — the shape #856's teardown gate exists to catch. As a
    ``TaskRunner`` task the scan reports file progress on the same
    ``/workers/progress`` lane every other task uses, and the runner owns its
    lifecycle.

    The scan is held inside ``_describe`` so the in-flight assertions are made
    against a genuinely running task rather than a race.
    """
    folder_id, _ = _register_folder_with_adapters(shelf_env, tmp_path, "held", 3)

    started = threading.Event()
    release = threading.Event()
    ran_on = []
    original = ModelFolderScanner._describe

    def blocking(self, abs_path, relpath, known, result):
        ran_on.append(threading.current_thread().name)
        started.set()
        assert release.wait(30), "the held scan was never released"
        return original(self, abs_path, relpath, known, result)

    monkeypatch.setattr(ModelFolderScanner, "_describe", blocking)

    r = shelf_env.owner.post(f"{API}/model-folders/{folder_id}/rescan")
    assert r.status_code == 202, r.text
    task_id = r.json()["task_id"]
    assert task_id, r.text
    assert started.wait(30), "the submitted task never reached the scanner"

    # The task runner owns it. Not a thread this route spawned and forgot.
    assert ran_on[0].startswith("vault-task-runner"), ran_on

    lane = shelf_env.owner.get(f"{API}/workers/progress").json()["workers"][
        "ModelFolderScanTask"
    ]
    assert lane["running"] is True, lane
    # The denominator is known before the first (potentially multi-GB) hash,
    # which is the whole point of materialising the walk.
    assert (lane["total"], lane["current"]) == (3, 0), lane

    # One scan per folder: the second press is refused and told which task is
    # already doing the work, rather than reading the same bytes again.
    again = shelf_env.owner.post(f"{API}/model-folders/{folder_id}/rescan")
    assert again.status_code == 202, again.text
    assert again.json() == {
        "status": "already_running",
        "id": folder_id,
        "task_id": task_id,
    }

    release.set()
    row = _await_scan(shelf_env, folder_id)
    assert row["scan_status"] == "completed", row
    assert row["scan_error"] is None, row
    assert row["file_count"] == 3, row
    assert row["last_checked"] is not None, row

    # Terminal, so the lane goes quiet again — over-reporting a finished scan as
    # running is its own regression.
    lane = shelf_env.owner.get(f"{API}/workers/progress").json()["workers"][
        "ModelFolderScanTask"
    ]
    assert lane["running"] is False, lane


def test_a_crashed_rescan_reports_failed_rather_than_looking_slow(
    shelf_env, tmp_path, monkeypatch
):
    """The scanner logs its exception and returns without stamping
    ``last_checked``, so a timestamp cannot tell a crash from a slow read — which
    is why the UI had to guess with a ten-minute ceiling. The task's status can.
    """
    folder_id, _ = _register_folder_with_adapters(shelf_env, tmp_path, "doomed", 2)

    def die(self, *args, **kwargs):
        raise RuntimeError("the drive went away mid-scan")

    monkeypatch.setattr(ModelFolderScanner, "scan_folder", die)

    r = shelf_env.owner.post(f"{API}/model-folders/{folder_id}/rescan")
    assert r.status_code == 202, r.text

    row = _await_scan(shelf_env, folder_id)
    assert row["scan_status"] == "failed", row
    assert "the drive went away mid-scan" in (row["scan_error"] or ""), row
    # The evidence that the timestamp alone was never enough.
    assert row["last_checked"] is None, row


def test_forgetting_a_folder_forgets_the_scan_recorded_against_it(shelf_env, tmp_path):
    """SQLite reuses rowids, so a remembered scan outliving its folder would let
    a folder registered later inherit a previous one's outcome."""
    folder_id, _ = _register_folder_with_adapters(shelf_env, tmp_path, "shortlived", 1)
    assert (
        shelf_env.owner.post(f"{API}/model-folders/{folder_id}/rescan").status_code
        == 202
    )
    assert _await_scan(shelf_env, folder_id)["scan_status"] == "completed"

    r = shelf_env.owner.delete(f"{API}/model-folders/{folder_id}")
    assert r.status_code == 200, r.text
    assert folder_id not in model_folders_routes._scans


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


def test_put_refuses_a_list_longer_than_the_ceiling(shelf_env):
    """Every element is a ``session.get`` inside one ``DBPriority.IMMEDIATE``
    vault transaction, which is the queue every other write waits behind, so an
    unbounded body is a stall any authenticated caller can trigger. Refused by
    validation, before the handler opens that transaction."""
    over = [
        {"entity_type": "character", "entity_id": shelf_env.character_id}
        for _ in range(MAX_ATTACHMENTS_PER_MODEL + 1)
    ]
    r = shelf_env.owner.put(_attachments_url(ADAPTER_WITH_BASE), json=over)
    assert r.status_code == 422, r.text

    # The positive control: a list at the ceiling still writes. Over-blocking a
    # legitimate assignment would be its own regression.
    at_limit = over[:MAX_ATTACHMENTS_PER_MODEL]
    r = shelf_env.owner.put(_attachments_url(ADAPTER_WITH_BASE), json=at_limit)
    assert r.status_code == 200, r.text


def test_put_refuses_an_attachment_carrying_an_unrecognised_key(shelf_env):
    """The response model allows extra keys so an old client keeps reading a
    newer server. A request is the other direction: an unrecognised key is a
    typo, and accepting it silently turns a misspelled ``entity_id`` into a
    no-op the user reads as success."""
    r = shelf_env.owner.put(
        _attachments_url(ADAPTER_WITH_BASE),
        json=[
            {
                "entity_type": "character",
                "entity_id": shelf_env.character_id,
                "entitiy_id": 999999,
            }
        ],
    )
    assert r.status_code == 422, r.text


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
    import threading

    server = shelf_env.server
    calls: list[str] = []
    original = server.hub.fetchall

    # Count the REQUEST's queries by naming the thread that serves them, not by
    # listing the ones that must not count. The server is shared, so background
    # workers run against this same hub throughout, and every one of their
    # queries landing in the tally fails this assertion for a reason that has
    # nothing to do with the list query.
    #
    # A denylist was tried twice and lost twice. It began as `model-move`, gained
    # `model-folder-rescan` when a rescan outlived the test that started it, and
    # was still short: `TaskRunner` names its workers `<name>-cpu-<i>` and
    # `<name>-gpu` (task_runner.py:423), and `CHECKPOINT_HASH` works on the hub
    # — so it slipped through and this failed with "3 hub queries for 24 rows"
    # on an unrelated PR. Every future worker would have to be remembered here.
    #
    # An allowlist cannot go stale that way: Starlette serves the handler on its
    # own threadpool, whose threads are named "AnyIO worker thread", and nothing
    # else in this process is. It also cannot rot into a dead assertion — the
    # tally is asserted to be exactly 2, so a naming change that matched nothing
    # would fail loudly rather than pass silently.
    request_thread_prefix = "AnyIO worker thread"

    def counting(sql, params=()):
        if threading.current_thread().name.startswith(request_thread_prefix):
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


# ===========================================================================
# Moves (B7) — behaviour at the route, and both authz directions
# ===========================================================================

_MOVE_ROUTES = (
    ("POST", f"{API}/model-moves", {"json": {"destination_folder_id": 1, "items": []}}),
    ("GET", f"{API}/model-moves", {}),
    ("DELETE", f"{API}/model-moves", {}),
)


@pytest.fixture
def move_folders(shelf_env, tmp_path):
    """A real source folder with a real file in it, and a real destination.

    Registered directly in the hub, because ``POST /model-folders`` validates
    against the system-directory blocklist and a pytest tmp dir is not the thing
    under test here. Reset by the autouse re-seed like everything else.
    """
    from pixlstash.routes import model_moves

    model_moves._job = None
    source_dir = tmp_path / "loras"
    destination_dir = tmp_path / "archive"
    source_dir.mkdir()
    destination_dir.mkdir()
    (source_dir / "moving.safetensors").write_bytes(b"moving" * 1024)

    server = shelf_env.server
    with server.hub.transaction() as conn:
        source_id = int(
            conn.execute(
                "INSERT INTO model_folder (path, kind, movable, created_at) "
                "VALUES (?, 'user', 'per_item', '2026-08-09T00:00:00Z')",
                (str(source_dir),),
            ).lastrowid
        )
        destination_id = int(
            conn.execute(
                "INSERT INTO model_folder (path, kind, movable, created_at) "
                "VALUES (?, 'user', 'per_item', '2026-08-09T00:00:00Z')",
                (str(destination_dir),),
            ).lastrowid
        )
        model_id = int(
            conn.execute(
                "INSERT INTO model (file_kind, kind, sha256, filename, provenance, "
                "file_size, created_at) VALUES ('adapter', 'lora', ?, "
                "'moving.safetensors', 'external', ?, '2026-08-09T00:00:00Z')",
                (_h("movingfile"), 6 * 1024),
            ).lastrowid
        )
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state, "
            "seen_at, file_mtime) VALUES (?, ?, 'moving.safetensors', 'present', "
            "'2026-08-09T00:00:00Z', 1)",
            (model_id, source_id),
        )
    return SimpleNamespace(
        source_dir=source_dir,
        destination_dir=destination_dir,
        source_id=source_id,
        destination_id=destination_id,
    )


def _await_move(shelf_env, timeout=10.0):
    """Poll the status route until the job stops running."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = shelf_env.owner.get(f"{API}/model-moves").json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError("the move never finished")


@pytest.mark.parametrize("mutate", ["_record_result", "_finish_job"])
def test_the_move_worker_writes_the_job_under_the_readers_lock(mutate):
    """The worker thread must not touch ``_job`` while a reader holds the lock.

    ``_snapshot`` reads the dict in several steps — ``done`` from
    ``len(results)``, then ``results`` itself, then ``status`` — so a write
    landing between two of them hands the client a snapshot that contradicts
    itself. Asserted as blocking rather than by racing for real: hold
    ``_job_lock``, run the worker's write on another thread, and require that it
    has *not* happened until the lock is released. Drop the ``with _job_lock``
    from either helper and the write lands immediately and this goes red.

    No server and no fixture: it is a claim about one module's locking.
    """
    from pixlstash.routes import model_moves
    from pixlstash.services.model_mover import MoveOutcome

    job = {"results": [], "status": "running", "finished_at": None}
    write = (
        (
            lambda: model_moves._record_result(
                job, MoveOutcome(1, "a.safetensors", "moved")
            )
        )
        if mutate == "_record_result"
        else (lambda: model_moves._finish_job(job))
    )
    landed = threading.Event()

    def worker():
        write()
        landed.set()

    with model_moves._job_lock:
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        assert not landed.wait(0.25), (
            f"{mutate} wrote to the job while a reader held _job_lock"
        )
        assert job["results"] == [] and job["status"] == "running", (
            "the reader saw a half-written job"
        )
    assert landed.wait(5.0), f"{mutate} never completed after the lock was released"
    thread.join(5.0)
    if mutate == "_record_result":
        assert [r["relpath"] for r in job["results"]] == ["a.safetensors"]
    else:
        assert job["status"] == "finished" and job["finished_at"] is not None


def test_every_move_route_is_declared_local_owner_only():
    """§16.3: the shelf's first block that writes and unlinks host files. The
    GET is on the tier too, deliberately — see the coverage-matrix rationale."""
    for method, path, _ in _MOVE_ROUTES:
        key = (method, path.replace(API, "/api/v1"))
        assert key in ROUTE_POLICIES, f"{key} has no ROUTE_POLICIES entry"
        declared = ROUTE_POLICIES[key]
        assert declared.policy is AccessPolicy.LOCAL_OWNER_ONLY, (
            f"{key} declares {declared.policy}, not LOCAL_OWNER_ONLY"
        )
        assert declared.justification, f"{key} is on the §16.3 tier with no reason"


def test_a_move_relocates_the_file_and_the_row(shelf_env, move_folders):
    r = shelf_env.owner.post(
        f"{API}/model-moves",
        json={
            "destination_folder_id": move_folders.destination_id,
            "items": [
                {"folder_id": move_folders.source_id, "relpath": "moving.safetensors"}
            ],
        },
    )
    assert r.status_code == 202, r.text
    assert r.json()["total"] == 1

    body = _await_move(shelf_env)
    assert [item["status"] for item in body["results"]] == ["moved"]
    assert not (move_folders.source_dir / "moving.safetensors").exists()
    assert (move_folders.destination_dir / "moving.safetensors").exists()

    row = shelf_env.owner.get(f"{API}/adapters/{_h('movingfile')}").json()
    assert [location["folder_id"] for location in row["locations"]] == [
        move_folders.destination_id
    ]


def test_a_move_is_refused_before_the_first_byte_when_it_cannot_work(
    shelf_env, move_folders
):
    """Validation is in the POST, not in the background job: a mistake is an
    immediate error, not 1,499 files moved and no undo."""
    r = shelf_env.owner.post(
        f"{API}/model-moves",
        json={
            "destination_folder_id": move_folders.destination_id,
            "items": [
                {"folder_id": move_folders.source_id, "relpath": "nope.safetensors"}
            ],
        },
    )
    assert r.status_code == 404, r.text
    assert (move_folders.source_dir / "moving.safetensors").exists()
    assert list(move_folders.destination_dir.iterdir()) == []

    r = shelf_env.owner.post(
        f"{API}/model-moves",
        json={"destination_folder_id": 999999, "items": []},
    )
    assert r.status_code == 404, r.text


def test_a_move_and_an_import_share_one_job_slot(
    shelf_env, move_folders, import_folders
):
    """They used to hold **separate** locks, which serialized each against
    itself and neither against the other: both could find one destination
    filename free and whichever wrote second won in silence.

    Held deterministically rather than by racing two threads — the slot is the
    contract, and a timing test would prove less and flake more. Both directions
    plus a positive control, so a 409 cannot pass because the request was bad.
    """
    assert SHELF_IO_LOCK.acquire(blocking=False), "the slot was left held"
    move_body = {
        "destination_folder_id": move_folders.destination_id,
        "items": [
            {"folder_id": move_folders.source_id, "relpath": "moving.safetensors"}
        ],
    }
    import_body = {
        "source_folder_id": import_folders.source_id,
        "run_name": "Clementine",
        "destination_folder_id": import_folders.destination_id,
    }
    try:
        for url, body in (
            (f"{API}/model-moves", move_body),
            (f"{API}/model-imports", import_body),
        ):
            r = shelf_env.owner.post(url, json=body)
            assert r.status_code == 409, f"{url} ran while the slot was taken: {r.text}"
            assert "already running" in r.text
        assert (move_folders.source_dir / "moving.safetensors").exists()
        assert list(import_folders.destination_dir.iterdir()) == []
    finally:
        SHELF_IO_LOCK.release()

    # Positive control: with the slot free, the same import is accepted.
    assert shelf_env.owner.post(
        f"{API}/model-imports", json=import_body
    ).status_code == (200)


def test_cancelling_when_nothing_runs_is_a_conflict_not_a_silent_success(shelf_env):
    from pixlstash.routes import model_moves

    model_moves._job = None
    assert shelf_env.owner.get(f"{API}/model-moves").json()["status"] == "idle"
    assert shelf_env.owner.delete(f"{API}/model-moves").status_code == 409


def test_move_routes_refuse_every_share_token(shelf_env):
    """The negative direction, with a live positive control on each token so a
    refusal cannot pass because the credential was dead."""
    for description, restriction in (
        ("move scoped probe", {"resource_type": "character", "resource_id": 1}),
        ("move unscoped probe", {}),
    ):
        token = _mint(shelf_env.owner, description, **restriction)
        client = _bearer(shelf_env.server, token)
        assert client.get(f"{API}/pictures").status_code == 200, (
            f"{description} is dead; the refusals below would prove nothing"
        )
        for method, path, kwargs in _MOVE_ROUTES:
            assert_real_route(shelf_env.server.api, method, path)
            r = client.request(method, path, **kwargs)
            assert r.status_code == 403, (
                f"{description} reached {method} {path}: {r.status_code} {r.text}"
            )


def test_local_owner_reaches_every_move_route(shelf_env):
    """Over-blocking is its own regression: loopback, RFC1918 LAN and Tailscale
    CGNAT must all pass the locality half."""
    for method, path, kwargs in _MOVE_ROUTES:
        for headers in ({}, _xff("192.168.1.9"), _xff("100.64.0.5")):
            r = shelf_env.owner.request(method, path, headers=headers, **kwargs)
            assert "restricted to local" not in r.text, (
                f"{method} {path} from {headers or 'loopback'} was refused as "
                f"non-local: {r.status_code} {r.text}"
            )


def test_remote_owner_is_refused_on_every_move_route_naming_the_flag(shelf_env):
    for method, path, kwargs in _MOVE_ROUTES:
        r = shelf_env.owner.request(method, path, headers=_xff("8.8.8.8"), **kwargs)
        assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"
        assert "allow_remote_host_ops" in r.text, (
            f"{method} {path} denied without naming the setting that enables it: "
            f"{r.text}"
        )


# ===========================================================================
# ai-toolkit import (B7) — the route, and both authz directions
# ===========================================================================

_IMPORT_ROUTES = (
    ("GET", f"{API}/model-folders/1/runs", {}),
    (
        "POST",
        f"{API}/model-imports",
        {
            "json": {
                "source_folder_id": 1,
                "run_name": "Clementine",
                "destination_folder_id": 1,
            }
        },
    ),
)


def _write_run_adapter(path, seed):
    """A header-only safetensors with LoRA markers and a distinguishing payload."""
    import struct

    header = {f"blocks.{i}.lora_A.weight": _TENSOR for i in range(2)}
    header["blocks.0.lora_B.weight"] = _TENSOR
    header["__metadata__"] = {"format": "pt"}
    blob = json.dumps(header).encode()
    path.write_bytes(struct.pack("<Q", len(blob)) + blob + seed)


_TENSOR = {"dtype": "F16", "shape": [8, 16], "data_offsets": [0, 0]}


@pytest.fixture
def import_folders(shelf_env, tmp_path):
    """A registered ai-toolkit output root with one run, and a destination."""
    output_root = tmp_path / "output"
    run_dir = output_root / "Clementine"
    run_dir.mkdir(parents=True)
    _write_run_adapter(run_dir / "Clementine.safetensors", b"final")
    _write_run_adapter(run_dir / "Clementine_000000500.safetensors", b"step500")
    # Not "loras": ``move_folders`` claims that name under the same ``tmp_path``,
    # and ``model_folder.path`` is UNIQUE, so one test may want both fixtures.
    destination_dir = tmp_path / "imported"
    destination_dir.mkdir()

    with shelf_env.server.hub.transaction() as conn:
        source_id = int(
            conn.execute(
                "INSERT INTO model_folder (path, kind, owner, movable, "
                "delete_after_import, created_at) VALUES (?, 'source', "
                "'ai-toolkit', 'external', 0, '2026-08-09T00:00:00Z')",
                (str(output_root),),
            ).lastrowid
        )
        destination_id = int(
            conn.execute(
                "INSERT INTO model_folder (path, kind, movable, created_at) "
                "VALUES (?, 'user', 'per_item', '2026-08-09T00:00:00Z')",
                (str(destination_dir),),
            ).lastrowid
        )
    return SimpleNamespace(
        output_root=output_root,
        run_dir=run_dir,
        destination_dir=destination_dir,
        source_id=source_id,
        destination_id=destination_id,
    )


def test_every_import_route_is_declared_local_owner_only():
    for method, path in (
        ("GET", "/api/v1/model-folders/{folder_id}/runs"),
        (
            "GET",
            "/api/v1/model-folders/{folder_id}/runs/{run_name}/samples/{filename}",
        ),
        ("POST", "/api/v1/model-imports"),
    ):
        declared = ROUTE_POLICIES.get((method, path))
        assert declared is not None, f"({method}, {path}) has no ROUTE_POLICIES entry"
        assert declared.policy is AccessPolicy.LOCAL_OWNER_ONLY, (
            f"{method} {path} declares {declared.policy}, not LOCAL_OWNER_ONLY"
        )
        assert declared.justification, f"{method} {path} is on the tier with no reason"


def test_listing_runs_describes_them_without_importing_anything(
    shelf_env, import_folders
):
    r = shelf_env.owner.get(f"{API}/model-folders/{import_folders.source_id}/runs")
    assert r.status_code == 200, r.text
    runs = r.json()["runs"]
    assert [run["name"] for run in runs] == ["Clementine"]
    assert len(runs[0]["checkpoints"]) == 2
    # Nothing was taken: the card grid is drawn before any decision.
    assert list(import_folders.destination_dir.iterdir()) == []
    assert shelf_env.owner.get(f"{API}/adapters").json()["adapters"] != []


def test_a_folder_that_is_catalogued_in_place_holds_no_runs(shelf_env, import_folders):
    """A `user` folder is a library of models, not a place runs are taken from."""
    r = shelf_env.owner.get(f"{API}/model-folders/{import_folders.destination_id}/runs")
    assert r.status_code == 400, r.text


# ── The sample route's containment (F6) ─────────────────────────────────────
#
# This is the one route on the shelf that serves file BYTES from a path the
# caller helped name, so it is the one whose containment has to be proved rather
# than reasoned about. Both directions: the positive control is here so a fix
# that over-blocks is caught as its own regression.


def _sample(env, folders, run_name, filename):
    """Request one sample, percent-encoding both names."""
    return env.owner.get(
        f"{API}/model-folders/{folders.source_id}/runs"
        f"/{quote(run_name, safe='')}/samples/{quote(filename, safe='')}"
    )


def test_the_sample_route_resolves_before_any_negative_is_trusted(
    shelf_env, import_folders
):
    """A request to a path that does not route returns the same 404 a refusal
    does, so every negative below is worthless unless the route is known to
    exist. This is that proof."""
    samples = import_folders.run_dir / "samples"
    samples.mkdir(exist_ok=True)
    (samples / "probe.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    r = _sample(shelf_env, import_folders, "Clementine", "probe.png")
    assert r.status_code == 200, r.text


def test_a_sample_inside_the_registered_root_is_served(shelf_env, import_folders):
    """The positive control. Over-blocking is its own regression."""
    samples = import_folders.run_dir / "samples"
    samples.mkdir(exist_ok=True)
    # A one-pixel PNG: real bytes, so the media type is not the only thing
    # asserted about what comes back.
    (samples / "sample_000000500_0.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    )

    r = _sample(shelf_env, import_folders, "Clementine", "sample_000000500_0.png")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")


def test_the_sample_join_refuses_a_name_that_climbs_out(tmp_path):
    """The containment itself, asserted where it can actually be made to fail.

    **Not over HTTP, and that is the finding rather than a shortcut.** Starlette
    percent-decodes the path before matching, so `{filename}` is structurally
    incapable of carrying a `/`: a literal `../` is collapsed by the client, and
    `..%2F` becomes an extra path segment that matches no route. Three
    measurements got this wrong before it was pinned down —

      1. the plain-`../` version stayed green with `resolve_path_within` deleted;
      2. so did the percent-encoded version;
      3. and `tests/authz_guard.py::no_spa_fallback` then caught the reason on
         CI: the request was not reaching the API at all, it was being answered
         **200 by the SPA catch-all**. It passed locally only because a dev
         checkout has no built frontend to fall back to.

    So an HTTP-level traversal test here asserts nothing and was removed rather
    than kept as reassurance. The guard is still real and still load-bearing on
    **Windows**, where a backslash is both an ordinary URL character and a path
    separator, and where four CI shards run — which is why this asserts the join
    directly, and why it fails on every platform when the guard is removed.

    If either path segment is ever changed to a `:path` converter, the HTTP
    traversal becomes reachable everywhere and this file needs a test for it.
    """
    run_dir = tmp_path / "Clementine"
    (run_dir / "samples").mkdir(parents=True)
    (run_dir / "samples" / "ok.png").write_bytes(b"\x89PNG")
    (run_dir / "private.png").write_bytes(b"\x89PNG")

    # Positive control: over-blocking is its own regression.
    assert sample_path_within(str(run_dir), "ok.png") == str(
        run_dir / "samples" / "ok.png"
    )

    for escape in ("../private.png", "..", "../../etc/passwd", "/etc/passwd"):
        with pytest.raises(ValueError):
            sample_path_within(str(run_dir), escape)


def test_the_filename_segment_cannot_carry_a_slash_through_routing(shelf_env):
    """Pin the routing SHAPE, which is what makes the HTTP traversal unreachable.

    The test that used to assert this over HTTP was deleted: the request never
    reached the API, so it asserted nothing (see the docstring above). The
    adversarial review of #878 asked for a structural replacement, on the
    grounds that `{filename}` matching one path segment was left guarded by a
    prose comment alone.

    Measured while writing it, the property turns out to be guarded twice, and
    the stronger guard is the one nobody wrote for this purpose: switching to
    `{filename:path}` changes the route's *effective path*, which then no longer
    matches its `ROUTE_POLICIES` key, and `AuthzGate.enforce_startup` refuses to
    boot the server at all. The deny-by-default registry pins the route shape as
    a side effect of pinning its policy.

    This test is kept anyway, because that guard only fires while the two
    disagree: someone changing the route AND the registry key together would
    satisfy it, and this says the shape itself is the requirement.
    """
    from tests.authz_guard import resolves_to_real_route

    app = shelf_env.server.api
    # The legitimate shape resolves...
    assert resolves_to_real_route(
        app, "GET", f"{API}/model-folders/1/runs/Clementine/samples/a.png"
    )
    # ...and one carrying a separator does not, because it is an extra segment.
    assert not resolves_to_real_route(
        app, "GET", f"{API}/model-folders/1/runs/Clementine/samples/../a.png"
    )


def test_a_symlinked_samples_directory_is_not_its_own_safe_base(tmp_path):
    """A planted `samples` symlink must not become the containment root.

    `resolve_path_within` derives its safe base by `realpath`-ing the base it is
    handed, so containing the filename against `run_dir/samples` directly makes
    a symlinked `samples` its own safe base and turns this route into an
    arbitrary-image reader for any allowlisted extension.

    Not hypothetical for a `source` folder. Every other registered path is one
    the owner chose; a source folder's *contents* are third-party tool output
    the owner merely pointed at, and both tarballs and git repositories carry
    symlinks. A directory symlink or an NTFS junction does the same on Windows,
    where four CI shards run.

    Found by the adversarial review of this PR, which is the point of having
    one: the author had already convinced himself the containment held, and the
    module docstring said so.
    """
    run_dir = tmp_path / "Clementine"
    run_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.png").write_bytes(b"\x89PNG\r\n\x1a\nsecret")
    (run_dir / "samples").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        sample_path_within(str(run_dir), "private.png")


def test_a_symlinked_sample_file_is_refused_too(tmp_path):
    """The sibling case, kept so a fix to the directory hinge cannot quietly
    drop the file hinge: a real `samples/` holding a symlink OUT is the other
    half, and it was already closed."""
    run_dir = tmp_path / "Clementine"
    (run_dir / "samples").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.png").write_bytes(b"\x89PNG\r\n\x1a\nsecret")
    (run_dir / "samples" / "innocent.png").symlink_to(outside / "private.png")

    with pytest.raises(ValueError):
        sample_path_within(str(run_dir), "innocent.png")


def test_a_file_that_is_not_an_image_is_never_served_from_our_origin(
    shelf_env, import_folders
):
    """An allowlist, not a guess. `samples/` is a directory on the owner's disk
    and anything can be dropped into it; `mimetypes` would label this text/html
    and serve it same-origin."""
    samples = import_folders.run_dir / "samples"
    samples.mkdir(exist_ok=True)
    (samples / "note.html").write_text("<script>alert(1)</script>")

    r = _sample(shelf_env, import_folders, "Clementine", "note.html")
    assert r.status_code == 400, r.text
    assert b"script" not in r.content


def test_a_sample_that_is_not_there_is_a_404_and_not_a_500(shelf_env, import_folders):
    r = _sample(shelf_env, import_folders, "Clementine", "nothing.png")
    assert r.status_code == 404, r.text


def test_only_a_source_folder_serves_samples(shelf_env, import_folders):
    """Same gate as the listing: a folder catalogued in place is not taken from."""
    r = shelf_env.owner.get(
        f"{API}/model-folders/{import_folders.destination_id}"
        f"/runs/Clementine/samples/a.png"
    )
    assert r.status_code == 400, r.text


def test_importing_a_run_registers_it_as_one_stack(shelf_env, import_folders):
    r = shelf_env.owner.post(
        f"{API}/model-imports",
        json={
            "source_folder_id": import_folders.source_id,
            "run_name": "Clementine",
            "destination_folder_id": import_folders.destination_id,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [f["status"] for f in body["files"]] == ["imported", "imported"]
    assert body["deleted_source"] is False
    assert body["stack_id"] is not None

    # delete_after_import is off, so the run keeps its own copy.
    assert (import_folders.run_dir / "Clementine.safetensors").exists()
    assert (import_folders.destination_dir / "Clementine.safetensors").exists()

    rows = {
        row["filename"]: row
        for row in shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    }
    cover = rows["Clementine.safetensors"]
    assert cover["provenance"] == "trained"
    assert cover["stack_position"] == 0
    assert cover["member_count"] == 2


def test_a_run_name_that_escapes_the_output_root_is_refused(shelf_env, import_folders):
    """The body names a run, never a path. A name that resolves outside the
    registered root is refused rather than read."""
    r = shelf_env.owner.post(
        f"{API}/model-imports",
        json={
            "source_folder_id": import_folders.source_id,
            "run_name": "../../etc",
            "destination_folder_id": import_folders.destination_id,
        },
    )
    assert r.status_code == 400, r.text
    assert list(import_folders.destination_dir.iterdir()) == []


def test_import_routes_refuse_every_share_token(shelf_env):
    for description, restriction in (
        ("import scoped probe", {"resource_type": "character", "resource_id": 1}),
        ("import unscoped probe", {}),
    ):
        token = _mint(shelf_env.owner, description, **restriction)
        client = _bearer(shelf_env.server, token)
        assert client.get(f"{API}/pictures").status_code == 200, (
            f"{description} is dead; the refusals below would prove nothing"
        )
        for method, path, kwargs in _IMPORT_ROUTES:
            assert_real_route(shelf_env.server.api, method, path)
            r = client.request(method, path, **kwargs)
            assert r.status_code == 403, (
                f"{description} reached {method} {path}: {r.status_code} {r.text}"
            )


def test_local_owner_reaches_every_import_route(shelf_env):
    for method, path, kwargs in _IMPORT_ROUTES:
        for headers in ({}, _xff("192.168.1.9"), _xff("100.64.0.5")):
            r = shelf_env.owner.request(method, path, headers=headers, **kwargs)
            assert "restricted to local" not in r.text, (
                f"{method} {path} from {headers or 'loopback'} was refused as "
                f"non-local: {r.status_code} {r.text}"
            )


def test_remote_owner_is_refused_on_every_import_route_naming_the_flag(shelf_env):
    for method, path, kwargs in _IMPORT_ROUTES:
        r = shelf_env.owner.request(method, path, headers=_xff("8.8.8.8"), **kwargs)
        assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"
        assert "allow_remote_host_ops" in r.text, (
            f"{method} {path} denied without naming the setting that enables it: "
            f"{r.text}"
        )


# ===========================================================================
# The managed store (B7) — it is always there, and it does not go away
# ===========================================================================


@pytest.fixture
def managed_folder(shelf_env, tmp_path):
    """A managed row and a plain user row, so both delete directions are live.

    Registered here rather than relied on from server start, because the autouse
    re-seed wipes ``model_folder`` before every test — which is what keeps the
    shelf assertions above deterministic.
    """
    store = tmp_path / "managed"
    plain = tmp_path / "plain"
    store.mkdir()
    plain.mkdir()
    with shelf_env.server.hub.transaction() as conn:
        managed_id = int(
            conn.execute(
                "INSERT INTO model_folder (path, kind, owner, movable, created_at) "
                "VALUES (?, 'managed', 'pixlstash', 'root_only', "
                "'2026-08-09T00:00:00Z')",
                (str(store),),
            ).lastrowid
        )
        plain_id = int(
            conn.execute(
                "INSERT INTO model_folder (path, kind, movable, created_at) "
                "VALUES (?, 'user', 'per_item', '2026-08-09T00:00:00Z')",
                (str(plain),),
            ).lastrowid
        )
    return SimpleNamespace(managed_id=managed_id, plain_id=plain_id)


def test_the_managed_store_cannot_be_forgotten(shelf_env, managed_folder):
    """409, not 403: the owner is fully authorized and the request is well
    formed. What refuses it is the state of the target row."""
    r = shelf_env.owner.delete(f"{API}/model-folders/{managed_folder.managed_id}")
    assert r.status_code == 409, r.text
    assert (
        shelf_env.server.hub.fetchone(
            "SELECT id FROM model_folder WHERE id = ?", (managed_folder.managed_id,)
        )
        is not None
    )


def test_forgetting_an_ordinary_folder_still_works(shelf_env, managed_folder):
    """The other direction. Over-blocking is its own regression, and a refusal
    that also caught `user` folders would break the shelf's only tombstone."""
    r = shelf_env.owner.delete(f"{API}/model-folders/{managed_folder.plain_id}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"
    assert (
        shelf_env.server.hub.fetchone(
            "SELECT id FROM model_folder WHERE id = ?", (managed_folder.plain_id,)
        )
        is None
    )


def test_the_managed_kind_cannot_be_created_over_http(shelf_env, tmp_path):
    """The refusal above is only worth anything if a second managed row cannot
    be made in the first place."""
    r = shelf_env.owner.post(
        f"{API}/model-folders",
        json={"path": str(tmp_path / "second-store"), "kind": "managed"},
    )
    assert r.status_code == 400, r.text


# ===========================================================================
# Relocating the managed store — a B7 move of every file it holds
# ===========================================================================


@pytest.fixture
def relocatable_store(shelf_env, tmp_path):
    """A managed store with two adapters in it, and an empty target drive."""
    from pixlstash.routes import model_moves

    model_moves._job = None
    store = tmp_path / "store"
    target = tmp_path / "big-drive" / "models"
    store.mkdir()
    with shelf_env.server.hub.transaction() as conn:
        managed_id = int(
            conn.execute(
                "INSERT INTO model_folder (path, kind, owner, movable, created_at) "
                "VALUES (?, 'managed', 'pixlstash', 'root_only', "
                "'2026-08-09T00:00:00Z')",
                (str(store),),
            ).lastrowid
        )
        for index, name in enumerate(("one.safetensors", "two.safetensors")):
            (store / name).write_bytes(name.encode() * 512)
            model_id = int(
                conn.execute(
                    "INSERT INTO model (file_kind, kind, sha256, filename, "
                    "provenance, file_size, created_at) VALUES ('adapter', 'lora', "
                    "?, ?, 'external', ?, '2026-08-09T00:00:00Z')",
                    (_h(f"stored{index}z"), name, 512 * len(name)),
                ).lastrowid
            )
            conn.execute(
                "INSERT INTO model_file (model_id, model_folder_id, relpath, "
                "state, seen_at, file_mtime) VALUES (?, ?, ?, 'present', "
                "'2026-08-09T00:00:00Z', 1)",
                (model_id, managed_id, name),
            )
        # A tombstone: a file the store once held and no longer has. It must
        # survive the relocation rather than be dropped with the old row.
        ghost = int(
            conn.execute(
                "INSERT INTO model (file_kind, kind, sha256, filename, "
                "provenance, file_size, created_at) VALUES ('adapter', 'lora', "
                "?, 'ghost.safetensors', 'external', 10, '2026-08-09T00:00:00Z')",
                (_h("ghostz"),),
            ).lastrowid
        )
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state, "
            "seen_at) VALUES (?, ?, 'ghost.safetensors', 'missing', "
            "'2026-08-09T00:00:00Z')",
            (ghost, managed_id),
        )
    return SimpleNamespace(store=store, target=target, managed_id=managed_id)


def _managed_rows(shelf_env):
    return shelf_env.server.hub.fetchall(
        "SELECT id, path, kind FROM model_folder WHERE kind = 'managed' ORDER BY id"
    )


def test_relocating_the_store_moves_its_files_and_keeps_one_managed_row(
    shelf_env, relocatable_store
):
    r = shelf_env.owner.post(
        f"{API}/model-folders/{relocatable_store.managed_id}/relocate",
        json={"path": str(relocatable_store.target)},
    )
    assert r.status_code == 202, r.text
    body = _await_move(shelf_env)
    assert [item["status"] for item in body["results"]] == ["moved", "moved"]

    assert sorted(p.name for p in relocatable_store.target.iterdir()) == [
        "one.safetensors",
        "two.safetensors",
    ]
    assert not relocatable_store.store.exists(), "the vacated directory was tidied"

    managed = _managed_rows(shelf_env)
    assert len(managed) == 1, "exactly one managed folder, always"
    assert managed[0]["path"] == str(relocatable_store.target)
    assert managed[0]["id"] != relocatable_store.managed_id, (
        "the new row is the store now; the old one is gone"
    )

    # Every row the old store held now belongs to the new one — including the
    # tombstone, which came across rather than being dropped with the old row:
    # the store moving is not news about whether that file came back. (The
    # module's own seeded folder 1 is not part of this and is excluded.)
    rows = shelf_env.server.hub.fetchall(
        "SELECT relpath, state FROM model_file WHERE model_folder_id = ? "
        "ORDER BY relpath",
        (managed[0]["id"],),
    )
    assert {row["relpath"]: row["state"] for row in rows} == {
        "ghost.safetensors": "missing",
        "one.safetensors": "present",
        "two.safetensors": "present",
    }
    assert (
        shelf_env.server.hub.fetchall(
            "SELECT relpath FROM model_file WHERE model_folder_id = ?",
            (relocatable_store.managed_id,),
        )
        == []
    )


def test_a_relocation_interrupted_before_the_promotion_leaves_one_managed_row(
    shelf_env, relocatable_store, monkeypatch
):
    """The crash window that is specific to relocation. Every file may already
    have moved and the promotion may not have run — and that must still leave
    exactly one managed folder and no row naming a file that is gone."""
    from pixlstash.routes import model_moves

    monkeypatch.setattr(
        model_moves,
        "_finish_relocation",
        lambda *args, **kwargs: None,  # the process died before the promotion
    )
    r = shelf_env.owner.post(
        f"{API}/model-folders/{relocatable_store.managed_id}/relocate",
        json={"path": str(relocatable_store.target)},
    )
    assert r.status_code == 202, r.text
    _await_move(shelf_env)

    managed = _managed_rows(shelf_env)
    assert len(managed) == 1, "a half-done relocation must never leave two stores"
    assert managed[0]["id"] == relocatable_store.managed_id, (
        "the old store is still the store until the promotion commits"
    )
    # Every present row this relocation touched still names a file that exists.
    # Scoped to the two folders involved: the module's seeded folder 1 is a
    # fixture with no files behind it and is not what this asserts about.
    for row in shelf_env.server.hub.fetchall(
        "SELECT f.path, mf.relpath FROM model_file mf "
        "JOIN model_folder f ON f.id = mf.model_folder_id "
        "WHERE mf.state = 'present' AND f.path IN (?, ?)",
        (str(relocatable_store.store), str(relocatable_store.target)),
    ):
        assert os.path.exists(os.path.join(row["path"], row["relpath"])), (
            f"{row['relpath']} is registered but is not on disk"
        )


def test_a_symlink_into_a_system_directory_is_refused_not_followed(
    shelf_env, relocatable_store, tmp_path
):
    """The blocklist is canonicalized, not lexical.

    Two reviews disagreed. The security sign-off filed the lexical check under
    "explicitly not a finding" because owner-chosen paths are trusted and an
    owner who may name ``/usr`` directly gains nothing by naming a link to it.
    That is right about boundaries and beside the point about *this* check:
    there is no non-owner principal, so the blocklist is not a boundary, it is
    the guard against the owner relocating the store onto a system directory by
    accident — and a symlink is exactly the accident the owner cannot see in the
    path they typed. Lexically checked, this route would ``makedirs`` under
    ``/usr``, move every file of the store there, and ``rmdir`` around it.
    """
    link = tmp_path / "big-drive-link"
    os.symlink("/usr", str(link))
    url = f"{API}/model-folders/{relocatable_store.managed_id}/relocate"

    r = shelf_env.owner.post(url, json={"path": str(link / "models")})
    assert r.status_code == 400, f"a link into /usr was accepted: {r.status_code}"
    assert "restricted system directory" in r.text, r.text
    assert not os.path.exists("/usr/models"), "the relocation wrote through the link"
    assert sorted(p.name for p in relocatable_store.store.iterdir()) == [
        "one.safetensors",
        "two.safetensors",
    ]

    # Positive control, because over-blocking is its own regression: a link to
    # an ordinary directory still relocates, and the store is registered at the
    # location it really landed in rather than at the name it was reached by.
    alias = tmp_path / "alias"
    os.symlink(str(relocatable_store.target.parent), str(alias))
    assert (
        shelf_env.owner.post(url, json={"path": str(alias / "models")}).status_code
        == 202
    )
    _await_move(shelf_env)
    assert [row["path"] for row in _managed_rows(shelf_env)] == [
        str(relocatable_store.target)
    ]


def test_a_relocation_target_must_be_absolute_and_off_the_system_blocklist(
    shelf_env, relocatable_store
):
    """The only caller-supplied host path in the whole B7 stack, and it had no
    test at all: nulling the guard left the suite green.

    Owner-chosen paths are trusted here (the reference-folder precedent), so the
    guard is narrow on purpose — absolute, and not a system directory — but it
    is the difference between relocating the store onto ``/etc`` and not. Both
    refusals plus the positive control below, because over-blocking would make
    the store unmovable.
    """
    url = f"{API}/model-folders/{relocatable_store.managed_id}/relocate"
    for path, why in (
        ("/etc/pixlstash-models", "restricted system directory"),
        ("relative/models", "absolute"),
    ):
        r = shelf_env.owner.post(url, json={"path": path})
        assert r.status_code == 400, f"{path} was accepted: {r.status_code} {r.text}"
        assert why in r.text, f"{path} was refused without saying why: {r.text}"
    # Nothing moved and the store is still the store.
    assert sorted(p.name for p in relocatable_store.store.iterdir()) == [
        "one.safetensors",
        "two.safetensors",
    ]
    assert [row["id"] for row in _managed_rows(shelf_env)] == [
        relocatable_store.managed_id
    ]

    # Positive control: an ordinary absolute path is still accepted.
    assert (
        shelf_env.owner.post(
            url, json={"path": str(relocatable_store.target)}
        ).status_code
        == 202
    )
    _await_move(shelf_env)


def test_only_the_managed_store_can_be_relocated(shelf_env, tmp_path):
    """An ordinary folder is one the owner registered; moving it is the owner's
    own act, and re-registering is how the shelf hears about it."""
    plain = tmp_path / "plain"
    plain.mkdir()
    with shelf_env.server.hub.transaction() as conn:
        plain_id = int(
            conn.execute(
                "INSERT INTO model_folder (path, kind, movable, created_at) "
                "VALUES (?, 'user', 'per_item', '2026-08-09T00:00:00Z')",
                (str(plain),),
            ).lastrowid
        )
    r = shelf_env.owner.post(
        f"{API}/model-folders/{plain_id}/relocate",
        json={"path": str(tmp_path / "elsewhere")},
    )
    assert r.status_code == 409, r.text


def test_relocate_is_owner_only_and_local_only(shelf_env, relocatable_store):
    path = f"{API}/model-folders/{relocatable_store.managed_id}/relocate"
    body = {"json": {"path": str(relocatable_store.target)}}

    token = _mint(shelf_env.owner, "relocate probe")
    client = _bearer(shelf_env.server, token)
    assert client.get(f"{API}/pictures").status_code == 200, (
        "the READ token is dead; the refusal below would prove nothing"
    )
    assert_real_route(shelf_env.server.api, "POST", path)
    assert client.post(path, **body).status_code == 403

    remote = shelf_env.owner.post(path, headers=_xff("8.8.8.8"), **body)
    assert remote.status_code == 403, remote.text
    assert "allow_remote_host_ops" in remote.text

    # Positive control: the local owner is not blocked.
    local = shelf_env.owner.post(path, headers=_xff("192.168.1.9"), **body)
    assert "restricted to local" not in local.text, local.text


def test_relocating_keeps_the_stores_subdirectories(shelf_env, relocatable_store):
    """The store is ``movable='root_only'`` — it moves as a unit — so its tree
    has to arrive as a tree. Flattened, two runs holding a same-named checkpoint
    collide and the store can never be relocated at all."""
    with shelf_env.server.hub.transaction() as conn:
        for run in ("runA", "runB"):
            nested = relocatable_store.store / run
            nested.mkdir()
            (nested / "model.safetensors").write_bytes(run.encode() * 512)
            model_id = int(
                conn.execute(
                    "INSERT INTO model (file_kind, kind, sha256, filename, "
                    "provenance, file_size, created_at) VALUES ('adapter', 'lora', "
                    "?, 'model.safetensors', 'external', ?, '2026-08-09T00:00:00Z')",
                    (_h(f"nested{run}"), 512 * len(run)),
                ).lastrowid
            )
            conn.execute(
                "INSERT INTO model_file (model_id, model_folder_id, relpath, "
                "state, seen_at, file_mtime) VALUES (?, ?, ?, 'present', "
                "'2026-08-09T00:00:00Z', 1)",
                (model_id, relocatable_store.managed_id, f"{run}/model.safetensors"),
            )

    r = shelf_env.owner.post(
        f"{API}/model-folders/{relocatable_store.managed_id}/relocate",
        json={"path": str(relocatable_store.target)},
    )
    assert r.status_code == 202, r.text
    body = _await_move(shelf_env)
    assert [item["status"] for item in body["results"]] == ["moved"] * 4, body

    for run in ("runA", "runB"):
        assert (relocatable_store.target / run / "model.safetensors").exists()
    managed = _managed_rows(shelf_env)
    assert len(managed) == 1
    assert managed[0]["path"] == str(relocatable_store.target)
    relpaths = {
        row["relpath"]
        for row in shelf_env.server.hub.fetchall(
            "SELECT relpath FROM model_file WHERE model_folder_id = ? AND "
            "state = 'present'",
            (managed[0]["id"],),
        )
    }
    assert relpaths == {
        "one.safetensors",
        "two.safetensors",
        "runA/model.safetensors",
        "runB/model.safetensors",
    }
    assert not relocatable_store.store.exists(), "the emptied tree was not tidied"


def test_a_relocation_with_a_failed_file_does_not_promote_the_new_folder(
    shelf_env, relocatable_store, monkeypatch
):
    """The promotion is the point of no return, so it must not run over a
    half-moved store. One unverifiable file and the managed row stays exactly
    where it was, with whatever moved catalogued under an ordinary folder."""
    from pixlstash.services import model_mover

    monkeypatch.setattr(model_mover, "same_device", lambda *_: False)
    monkeypatch.setattr(model_mover, "file_digest", lambda path: "0" * 64)

    r = shelf_env.owner.post(
        f"{API}/model-folders/{relocatable_store.managed_id}/relocate",
        json={"path": str(relocatable_store.target)},
    )
    assert r.status_code == 202, r.text
    body = _await_move(shelf_env)
    assert {item["status"] for item in body["results"]} == {"failed"}

    managed = _managed_rows(shelf_env)
    assert len(managed) == 1
    assert managed[0]["id"] == relocatable_store.managed_id, (
        "the store was promoted despite files that never arrived"
    )
    assert (relocatable_store.store / "one.safetensors").exists()


# ===========================================================================
# The verb layer (F3): PATCH /models and POST /models/forget
# ===========================================================================


def _model_row(shelf_env, model_id: int) -> dict:
    row = shelf_env.server.hub.fetchone("SELECT * FROM model WHERE id = ?", (model_id,))
    assert row is not None, f"model {model_id} is gone"
    return dict(row)


def _set_states(shelf_env, model_id: int, state: str) -> None:
    with shelf_env.server.hub.transaction() as conn:
        conn.execute(
            "UPDATE model_file SET state = ? WHERE model_id = ?", (state, model_id)
        )


def test_an_edit_writes_only_the_fields_it_names(shelf_env):
    """Three verbs share one route, so the field that is NOT sent is the whole
    contract: setting a base model across a selection must not blank the names
    in it."""
    alice = shelf_env.model_ids["alice.safetensors"]
    bob = shelf_env.model_ids["bob.safetensors"]
    before = {mid: _model_row(shelf_env, mid) for mid in (alice, bob)}

    r = shelf_env.owner.patch(
        f"{API}/models", json={"ids": [alice, bob], "base_model": "FLUX.2"}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": sorted([alice, bob]), "fields": ["base_model"]}

    for mid in (alice, bob):
        after = _model_row(shelf_env, mid)
        assert after["base_model"] == "FLUX.2"
        assert after["display_name"] == before[mid]["display_name"], (
            "an unmentioned column was written"
        )
        assert after["kind"] == before[mid]["kind"]


def test_an_explicit_null_clears_the_column(shelf_env):
    """Distinct from "not sent". Clearing a wrong base model back to unset is a
    correction the owner is entitled to make, and it is what puts the row back
    in the filter's `not set` bucket."""
    alice = shelf_env.model_ids["alice.safetensors"]
    r = shelf_env.owner.patch(
        f"{API}/models", json={"ids": [alice], "base_model": None}
    )
    assert r.status_code == 200, r.text
    assert r.json()["fields"] == ["base_model"]
    assert _model_row(shelf_env, alice)["base_model"] is None


def test_a_rename_is_refused_across_a_selection(shelf_env):
    """A name is a fact about one file. In bulk it would give every selected row
    the same name, and there is no undo to walk that back."""
    alice = shelf_env.model_ids["alice.safetensors"]
    bob = shelf_env.model_ids["bob.safetensors"]

    r = shelf_env.owner.patch(
        f"{API}/models", json={"ids": [alice, bob], "display_name": "Both"}
    )
    assert r.status_code == 400
    assert "one model at a time" in r.text
    assert _model_row(shelf_env, alice)["display_name"] == "Alice", (
        "the refusal still wrote"
    )

    # Positive control: one id is the rename, and it works.
    ok = shelf_env.owner.patch(
        f"{API}/models", json={"ids": [alice], "display_name": "Alice Prime"}
    )
    assert ok.status_code == 200, ok.text
    assert _model_row(shelf_env, alice)["display_name"] == "Alice Prime"


def test_an_unhashed_checkpoint_cannot_be_called_an_adapter(shelf_env):
    """The hub's own CHECK would reject it. Left to SQLite that is a 500 naming
    a constraint, which says nothing about the file the owner picked."""
    unhashed = shelf_env.model_ids["huge_unhashed.safetensors"]
    r = shelf_env.owner.patch(
        f"{API}/models", json={"ids": [unhashed], "file_kind": "adapter"}
    )
    assert r.status_code == 400, r.text
    assert "huge_unhashed.safetensors" in r.text
    assert "hashed" in r.text
    assert _model_row(shelf_env, unhashed)["file_kind"] == "checkpoint"


def test_clearing_the_algorithm_of_an_adapter_is_refused(shelf_env):
    """Named no `file_kind` at all, so it never looked like the CHECK guard's
    business — and the row is already an adapter, so `kind = NULL` violates
    `CHECK (file_kind <> 'adapter' OR kind IS NOT NULL)`. Reported by the CSO
    review of #869; it was a 500 before the guard read the post-write state."""
    alice = shelf_env.model_ids["alice.safetensors"]
    r = shelf_env.owner.patch(f"{API}/models", json={"ids": [alice], "kind": None})
    assert r.status_code == 400, r.text
    assert "alice.safetensors" in r.text
    assert "algorithm" in r.text
    assert _model_row(shelf_env, alice)["kind"] == "lora"


def test_clearing_the_algorithm_while_naming_adapter_is_refused(shelf_env):
    """The sibling of the case above: this one DID reach the guard and passed,
    because the guard read the STORED kind rather than the one about to be
    written."""
    alice = shelf_env.model_ids["alice.safetensors"]
    r = shelf_env.owner.patch(
        f"{API}/models",
        json={"ids": [alice], "kind": None, "file_kind": "adapter"},
    )
    assert r.status_code == 400, r.text
    assert "algorithm" in r.text
    row = _model_row(shelf_env, alice)
    assert (row["file_kind"], row["kind"]) == ("adapter", "lora")


def test_clearing_the_algorithm_of_a_checkpoint_is_allowed(shelf_env):
    """The positive control for the two refusals above: the constraint only
    binds adapters, so over-blocking here would be its own regression."""
    base = shelf_env.model_ids["base_xl.safetensors"]
    r = shelf_env.owner.patch(f"{API}/models", json={"ids": [base], "kind": None})
    assert r.status_code == 200, r.text
    assert _model_row(shelf_env, base)["kind"] is None


def test_forget_reads_its_gate_inside_the_write_transaction(shelf_env):
    """The gate and the DELETE must be one critical section.

    `hub.fetchall` takes and releases the hub lock per call, so a gate read
    through it leaves a window in which a background scan can flip a row from
    `missing` back to `present` before the DELETE lands — and the model is
    forgotten anyway. Counting the reads that go OUTSIDE the transaction is the
    assertion, because the race itself cannot be scheduled reliably. Reported by
    the CSO review of #869.

    **Counted per THREAD, and that is what makes the count mean anything.**
    `hub.fetchall` is patched on the shared, module-scoped server, so every
    caller in the process goes through it — including the background finders,
    and `MissingCheckpointHashFinder` queries `model` on the hub in both
    `progress()` and `find_task()`. A sweep landing inside the request window
    put its SQL in this list and failed the test with a diagnostic pointing at
    the forget, which had done nothing wrong. Measured: red once in four runs of
    a loaded four-file combination.

    The allowlist is the same one the N+1 guard above uses, and for the same
    reason: Starlette serves a sync handler on its own threadpool, whose threads
    are named "AnyIO worker thread", and nothing else in this process is. A
    denylist of background worker names was tried twice there and lost twice.

    Pinning to the TEST's thread id instead does not work and is worth recording
    — it was tried here first. The handler is `def`, so FastAPI runs it in that
    threadpool rather than on the caller, which means an ident match excludes
    every read the request makes and turns this into a green assertion about
    nothing. It was caught by moving the builtin gate out of the transaction and
    watching the test stay green.

    **This assertion is satisfied by an empty list, and that is intended**: a
    correct `forget_models` makes NO `hub.fetchall` call at all, because every
    read it needs happens on the transaction's own connection. So there is no
    in-test way to prove the allowlist still matches — an added "we saw at least
    one" check fails on correct code. Liveness is proved by the N+1 guard above,
    which uses the same prefix and asserts an exact count, so a renamed
    threadpool goes red there rather than silently emptying this."""
    alice = shelf_env.model_ids["alice.safetensors"]
    _set_states(shelf_env, alice, "missing")

    hub = shelf_env.server.hub
    original = hub.fetchall
    outside: list[str] = []
    request_thread_prefix = "AnyIO worker thread"

    def counting(sql, params=()):
        if "model" in sql and threading.current_thread().name.startswith(
            request_thread_prefix
        ):
            outside.append(sql)
        return original(sql, params)

    hub.fetchall = counting
    try:
        r = shelf_env.owner.post(f"{API}/models/forget", json={"ids": [alice]})
    finally:
        hub.fetchall = original

    assert r.status_code == 200, r.text
    assert r.json()["forgotten"] == [alice]
    assert outside == [], (
        f"the forget read its gate outside the write transaction: {outside}"
    )


def test_an_unknown_is_corrected_to_an_adapter_when_it_can_be(shelf_env):
    """The positive direction, and the reason `unknown` is a stored value rather
    than a guess: it is one UPDATE away from correct, and the correction is
    never re-derived away by a later scan."""
    unknown = shelf_env.model_ids["mystery.safetensors"]
    r = shelf_env.owner.patch(
        f"{API}/models",
        json={"ids": [unknown], "file_kind": "adapter", "kind": "lora"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["fields"] == ["kind", "file_kind"]
    row = _model_row(shelf_env, unknown)
    assert (row["file_kind"], row["kind"]) == ("adapter", "lora")

    # ...and it now appears in the adapters list, which is the point of it.
    listed = shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    assert unknown in {row["id"] for row in listed}


def test_an_edit_naming_no_field_is_refused(shelf_env):
    r = shelf_env.owner.patch(
        f"{API}/models", json={"ids": [shelf_env.model_ids["alice.safetensors"]]}
    )
    assert r.status_code == 400
    assert "at least one" in r.text


def test_file_kind_cannot_be_cleared_only_corrected(shelf_env):
    """Every file is something, and `unknown` is how the shelf says so. A null
    would leave a row that neither list block matches."""
    alice = shelf_env.model_ids["alice.safetensors"]
    r = shelf_env.owner.patch(f"{API}/models", json={"ids": [alice], "file_kind": None})
    assert r.status_code == 400
    assert "unknown" in r.text
    assert _model_row(shelf_env, alice)["file_kind"] == "adapter"


def test_forget_takes_a_model_whose_every_copy_is_missing(shelf_env):
    """The verb's whole purpose. It destroys curation, which is why it is one of
    the two confirmations."""
    alice = shelf_env.model_ids["alice.safetensors"]
    _set_states(shelf_env, alice, "missing")

    r = shelf_env.owner.post(f"{API}/models/forget", json={"ids": [alice]})
    assert r.status_code == 200, r.text
    assert r.json() == {"forgotten": [alice], "refused": []}

    assert (
        shelf_env.server.hub.fetchone("SELECT id FROM model WHERE id = ?", (alice,))
        is None
    )
    assert (
        shelf_env.server.hub.fetchone(
            "SELECT model_id FROM model_file WHERE model_id = ?", (alice,)
        )
        is None
    ), "the location rows outlived the model they point at"


def test_forget_refuses_a_model_that_is_still_there(shelf_env):
    """`present` means the file is on the disk. Forgetting it would destroy the
    curation and the next scan would rebuild the row blank from the file."""
    alice = shelf_env.model_ids["alice.safetensors"]
    r = shelf_env.owner.post(f"{API}/models/forget", json={"ids": [alice]})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "forgotten": [],
        "refused": [{"id": alice, "reason": "still_has_a_copy"}],
    }
    assert _model_row(shelf_env, alice)["display_name"] == "Alice"


def test_forget_refuses_a_model_we_could_not_look_for(shelf_env):
    """The one that matters. `unreachable` is "we could not look" — an unplugged
    NAS — and treating it as a deletion would wipe the curation for a whole
    drive on one call."""
    alice = shelf_env.model_ids["alice.safetensors"]
    _set_states(shelf_env, alice, "unreachable")

    r = shelf_env.owner.post(f"{API}/models/forget", json={"ids": [alice]})
    assert r.status_code == 200, r.text
    assert r.json()["forgotten"] == []
    assert r.json()["refused"] == [{"id": alice, "reason": "still_has_a_copy"}]
    assert _model_row(shelf_env, alice)["display_name"] == "Alice"


def test_a_mixed_selection_forgets_what_it_can_and_reports_the_rest(shelf_env):
    """Reported, not raised: the selection was made against a list that may be
    seconds old, and failing the whole call because one file came back is the
    wrong answer to good news."""
    gone = shelf_env.model_ids["alice.safetensors"]
    here = shelf_env.model_ids["bob.safetensors"]
    _set_states(shelf_env, gone, "missing")

    r = shelf_env.owner.post(
        f"{API}/models/forget", json={"ids": [gone, here, 999_999]}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["forgotten"] == [gone]
    assert body["refused"] == [
        {"id": here, "reason": "still_has_a_copy"},
        {"id": 999_999, "reason": "no_such_model"},
    ]
    assert _model_row(shelf_env, here)["display_name"] == "Bob"


def test_forget_leaves_a_models_copies_in_other_folders_alone(shelf_env):
    """A model with a copy in two folders, one of them gone, is not forgettable:
    the file is still on the disk under the other folder."""
    alice = shelf_env.model_ids["alice.safetensors"]
    with shelf_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model_folder (id, path, kind, movable, created_at) "
            "VALUES (2, '/models/spare', 'user', 'per_item', '2026-08-09T00:00:00Z')"
        )
        conn.execute(
            "UPDATE model_file SET state = 'missing' WHERE model_id = ?", (alice,)
        )
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state, "
            "seen_at, file_mtime) VALUES (?, 2, 'alice.safetensors', 'present', "
            "'2026-08-09T00:00:00Z', 11)",
            (alice,),
        )

    r = shelf_env.owner.post(f"{API}/models/forget", json={"ids": [alice]})
    assert r.status_code == 200, r.text
    assert r.json()["refused"] == [{"id": alice, "reason": "still_has_a_copy"}]


def test_the_verb_routes_are_owner_only_in_both_directions(shelf_env):
    alice = shelf_env.model_ids["alice.safetensors"]
    calls = (
        ("PATCH", f"{API}/models", {"json": {"ids": [alice], "base_model": "SDXL"}}),
        ("POST", f"{API}/models/forget", {"json": {"ids": [alice]}}),
    )

    token = _mint(shelf_env.owner, "verb probe")
    client = _bearer(shelf_env.server, token)
    assert client.get(f"{API}/pictures").status_code == 200, (
        "the READ token is dead; the refusals below would prove nothing"
    )

    for method, path, kwargs in calls:
        assert_real_route(shelf_env.server.api, method, path)
        assert client.request(method, path, **kwargs).status_code == 403, (
            f"{method} {path} served a scoped READ token"
        )
        # Positive control: the owner is not blocked on the same call.
        assert shelf_env.owner.request(method, path, **kwargs).status_code == 200


# ===========================================================================
# GET /model-folders/devices — the drive bands' capacity meter (F2)
# ===========================================================================


def _devices(shelf_env) -> list[dict]:
    r = shelf_env.owner.get(f"{API}/model-folders/devices")
    assert r.status_code == 200, r.text
    return r.json()["devices"]


def test_two_folders_on_one_drive_share_one_band(shelf_env, tmp_path):
    """The band is a drive, not a path. Both folders here are under `tmp_path`
    and therefore one filesystem, so a path-keyed implementation would draw two
    meters for one drive and let the same free space be read twice."""
    first, first_path = _register_folder_with_adapters(shelf_env, tmp_path, "driveA", 2)
    second, second_path = _register_folder_with_adapters(
        shelf_env, tmp_path, "driveB", 1
    )
    assert os.stat(first_path).st_dev == os.stat(second_path).st_dev, (
        "the two folders are not on one filesystem here, so this machine "
        "cannot prove the grouping"
    )

    bands = [d for d in _devices(shelf_env) if first in d["folder_ids"]]
    assert len(bands) == 1, f"the drive was reported {len(bands)} times: {bands}"
    band = bands[0]
    assert second in band["folder_ids"], (
        "two folders on one filesystem were split across bands"
    )
    assert band["folder_ids"] == sorted(band["folder_ids"]), "folder ids are unordered"
    assert band["total_bytes"] > 0 and band["free_bytes"] > 0
    assert band["free_bytes"] <= band["total_bytes"]
    assert band["mount_point"], "the band has no label to draw"


def test_the_meter_counts_present_bytes_and_ignores_missing_ones(shelf_env, tmp_path):
    """`shelf_bytes` answers "how much of this drive is the shelf". A `missing`
    row names bytes that are not on the drive any more, so counting it would
    report space the drive itself does not agree is in use."""
    folder_id, folder_path = _register_folder_with_adapters(
        shelf_env, tmp_path, "metered", 3
    )
    assert (
        shelf_env.owner.post(f"{API}/model-folders/{folder_id}/rescan").status_code
        == 202
    )
    assert _await_scan(shelf_env, folder_id)["scan_status"] == "completed"

    band = next(d for d in _devices(shelf_env) if folder_id in d["folder_ids"])
    on_disk = sum(
        os.path.getsize(os.path.join(folder_path, name))
        for name in os.listdir(folder_path)
    )
    assert band["shelf_bytes"] >= on_disk, (
        f"the meter reported {band['shelf_bytes']} bytes for {on_disk} on disk"
    )

    with shelf_env.server.hub.transaction() as conn:
        conn.execute(
            "UPDATE model_file SET state = 'missing' WHERE model_folder_id = ?",
            (folder_id,),
        )
    after = next(d for d in _devices(shelf_env) if folder_id in d["folder_ids"])
    assert after["shelf_bytes"] == 0, (
        f"vanished files still counted toward the meter: {after['shelf_bytes']}"
    )


def test_an_unmeasurable_folder_still_gets_a_band(shelf_env, tmp_path):
    """An offline drive is the normal case this has to survive: the folder must
    keep somewhere to sit, and its capacity must read as unknown rather than as
    zero, which would draw a full meter."""
    folder_id, folder_path = _register_folder_with_adapters(
        shelf_env, tmp_path, "goingaway", 1
    )
    for name in os.listdir(folder_path):
        os.unlink(os.path.join(folder_path, name))
    os.rmdir(folder_path)

    band = next(d for d in _devices(shelf_env) if folder_id in d["folder_ids"])
    assert band["device_id"] is None
    assert band["total_bytes"] is None and band["free_bytes"] is None, (
        "an unmeasurable drive reported a capacity"
    )
    assert band["folder_ids"] == [folder_id], (
        "two folders we cannot stat were merged into one band, which claims a "
        "sameness nothing measured"
    )
    assert band["mount_point"] == folder_path


def test_devices_is_owner_only_in_both_directions(shelf_env):
    path = f"{API}/model-folders/devices"
    assert shelf_env.owner.get(path).status_code == 200

    token = _mint(shelf_env.owner, "device list probe")
    client = _bearer(shelf_env.server, token)
    assert client.get(f"{API}/pictures").status_code == 200, (
        "the READ token is dead; the refusal below would prove nothing"
    )
    assert_real_route(shelf_env.server.api, "GET", path)
    assert client.get(path).status_code == 403


def test_devices_is_not_restricted_to_a_local_caller(shelf_env):
    """The meter is `owner_only`, not the §16.3 locality tier: a remote owner
    already reads every registered path from `GET /model-folders`, so blocking
    this one would cost the drive bands and withhold nothing."""
    r = shelf_env.owner.get(f"{API}/model-folders/devices", headers=_xff("8.8.8.8"))
    assert r.status_code == 200, r.text


_LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason=(
        "the label lookup is per-platform: /dev/disk/by-label and /proc/mounts "
        "are Linux's answer, and the Windows and macOS branches read a volume "
        "API and a mount name that no fixture here can stand in for"
    ),
)


@_LINUX_ONLY
def test_a_band_is_named_by_its_volume_label_when_it_has_one(
    shelf_env, tmp_path, monkeypatch
):
    """A Linux mount point runs to `/media/glindkvist/102AB4B6757AF9A3`, which
    crowds a band header out. The volume's own name is what the owner recognises;
    the mount point stays on the response for the tooltip."""
    from pixlstash.utils import system_utils

    folder_id, folder_path = _register_folder_with_adapters(
        shelf_env, tmp_path, "labelled", 1
    )
    mount_point = system_utils.mount_point_of(folder_path)

    by_label = tmp_path / "by-label"
    by_label.mkdir()
    device = tmp_path / "fake-device"
    device.write_text("")
    # udev escapes a space as \x20, so a label with one proves the decoding too.
    os.symlink(device, by_label / "Model\\x20Drive")
    mounts = tmp_path / "mounts"
    mounts.write_text(
        f"{device} {mount_point.replace(' ', chr(92) + '040')} ext4 rw 0 0\n"
    )

    monkeypatch.setattr(system_utils, "_BY_LABEL_DIR", str(by_label))
    monkeypatch.setattr(system_utils, "_MOUNTS_FILE", str(mounts))

    band = next(d for d in _devices(shelf_env) if folder_id in d["folder_ids"])
    assert band["label"] == "Model Drive"
    assert band["mount_point"] == mount_point, (
        "the precise string must survive for the tooltip"
    )


def test_a_mount_point_holding_a_backslash_still_matches_its_device():
    """`/proc/mounts` escapes a literal backslash as `\\134`, which a pattern
    written for `\\040` and `\\011` does not cover. Left undecoded the mount
    point never matches its device and the drive silently loses its label.
    Reported by the review of #868."""
    from pixlstash.utils.system_utils import _unescape_mount_field

    assert _unescape_mount_field(r"/mnt/My\040Disk") == "/mnt/My Disk"
    assert _unescape_mount_field(r"/mnt/back\134slash") == "/mnt/back\\slash"
    assert _unescape_mount_field(r"/mnt/tab\011here") == "/mnt/tab\there"
    # A path with nothing to decode comes back untouched.
    assert _unescape_mount_field("/mnt/models") == "/mnt/models"


@_LINUX_ONLY
def test_a_drive_with_no_label_reports_null_rather_than_a_guess(
    shelf_env, tmp_path, monkeypatch
):
    """The band then falls back to the mount point, which is never wrong, only
    long. Inventing a name from the path would be neither."""
    from pixlstash.utils import system_utils

    folder_id, _ = _register_folder_with_adapters(shelf_env, tmp_path, "unlabelled", 1)
    empty = tmp_path / "no-labels"
    empty.mkdir()
    monkeypatch.setattr(system_utils, "_BY_LABEL_DIR", str(empty))

    band = next(d for d in _devices(shelf_env) if folder_id in d["folder_ids"])
    assert band["label"] is None
    assert band["mount_point"]


# ===========================================================================
# Built-in engines: listed for completeness, refused by every verb
# ===========================================================================


def _declare_engine(shelf_env, display_name="PixlStash anomaly tagger") -> int:
    """An `engine` row in a folder PixlStash owns. Returns its model id."""
    with shelf_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model_folder (id, path, kind, owner, movable, created_at) "
            "VALUES (9, '/engines', 'foreign', 'pixlstash', 'root_only', "
            "'2026-08-11T00:00:00Z')"
        )
        cursor = conn.execute(
            "INSERT INTO model (file_kind, kind, display_name, filename, "
            "provenance, file_size, created_at) VALUES ('engine', 'tagger', ?, "
            "'tagger.safetensors', 'builtin', 99, '2026-08-11T00:00:00Z')",
            (display_name,),
        )
        model_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state, "
            "seen_at) VALUES (?, 9, 'tagger.safetensors', 'present', "
            "'2026-08-11T00:00:00Z')",
            (model_id,),
        )
    return model_id


def test_an_engine_is_listed_only_when_asked_for(shelf_env):
    """It rides the adapters block as `unknown` does, under an explicit
    `file_kind`. The shelf's first question is which LoRA, not which tagger."""
    engine = _declare_engine(shelf_env)

    default = shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    assert engine not in {row["id"] for row in default}

    asked = shelf_env.owner.get(
        f"{API}/adapters", params={"file_kind": "engine"}
    ).json()["adapters"]
    listed = {row["id"]: row for row in asked}
    assert engine in listed
    assert listed[engine]["kind"] == "tagger", "the role is what the row shows"


def test_an_engine_is_never_served_as_a_checkpoint(shelf_env):
    """The same rule `unknown` has: a file we downloaded for ourselves is not a
    base model, and must not read as one."""
    engine = _declare_engine(shelf_env)
    served = shelf_env.owner.get(f"{API}/checkpoints").json()["checkpoints"]
    assert engine not in {row["id"] for row in served}


def test_every_editing_verb_refuses_an_engine(shelf_env):
    """409, not 403: the caller is authorized and the request is well formed.
    What refuses it is what the target IS."""
    engine = _declare_engine(shelf_env)
    for change in (
        {"display_name": "Mine now"},
        {"base_model": "SDXL 1.0"},
        {"file_kind": "adapter", "kind": "lora"},
    ):
        r = shelf_env.owner.patch(f"{API}/models", json={"ids": [engine], **change})
        assert r.status_code == 409, f"{change} was allowed: {r.text}"
        assert "PixlStash downloaded" in r.text

    row = _model_row(shelf_env, engine)
    assert (row["display_name"], row["file_kind"]) == (
        "PixlStash anomaly tagger",
        "engine",
    )


def test_forget_reports_an_engine_rather_than_deleting_it(shelf_env):
    """Reported like every other refusal rather than raised, and refused inside
    the same transaction as the state gate — forgetting one would delete a row
    that the next start-up declares straight back."""
    engine = _declare_engine(shelf_env)
    _set_states(shelf_env, engine, "missing")

    r = shelf_env.owner.post(f"{API}/models/forget", json={"ids": [engine]})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "forgotten": [],
        "refused": [{"id": engine, "reason": "is_a_builtin_engine"}],
    }
    assert _model_row(shelf_env, engine)["display_name"] == "PixlStash anomaly tagger"


def test_the_folder_pixlstash_owns_cannot_be_forgotten_or_rescanned(shelf_env):
    """Rescan especially: the scanner yields only `.safetensors` and sweeps what
    it did not see to `missing`, so pointing it at a folder of ONNX and `.pth`
    engines would mark them all missing on every pass."""
    _declare_engine(shelf_env)

    forgotten = shelf_env.owner.delete(f"{API}/model-folders/9")
    assert forgotten.status_code == 409, forgotten.text
    assert "downloaded for itself" in forgotten.text

    rescan = shelf_env.owner.post(f"{API}/model-folders/9/rescan")
    assert rescan.status_code == 202, rescan.text
    assert rescan.json()["status"] == "skipped"


# ===========================================================================
# base_model_folded — the fold applied on the way out
# ===========================================================================


def test_a_row_carries_the_canonical_label_beside_the_raw_one(shelf_env):
    """Both, never one. The raw string is what the file says and is what the
    shelf displays; the folded one is what a grouping or a facet should key on,
    so `sdxl_base_v1-0` and `SDXL` land in one bucket rather than two."""
    alice = shelf_env.model_ids["alice.safetensors"]
    with shelf_env.server.hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET base_model = 'sdxl_base_v1-0' WHERE id = ?", (alice,)
        )

    row = next(
        r
        for r in shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
        if r["id"] == alice
    )
    assert row["base_model"] == "sdxl_base_v1-0", "the raw spelling was rewritten"
    assert row["base_model_folded"] == "SDXL 1.0"


def test_two_spellings_of_one_base_fold_together(shelf_env):
    """The whole point: the shelf can group on this and get one row per base
    rather than one per spelling."""
    alice = shelf_env.model_ids["alice.safetensors"]
    bob = shelf_env.model_ids["bob.safetensors"]
    with shelf_env.server.hub.transaction() as conn:
        conn.execute("UPDATE model SET base_model = 'SDXL' WHERE id = ?", (alice,))
        conn.execute(
            "UPDATE model SET base_model = 'stable diffusion xl' WHERE id = ?", (bob,)
        )

    folded = {
        r["id"]: r["base_model_folded"]
        for r in shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    }
    assert folded[alice] == "SDXL 1.0"
    assert folded[bob] == folded[alice], "two spellings of one base did not meet"


def test_an_unrecognised_base_model_folds_to_null_and_is_still_served(shelf_env):
    """Not an error, and not a reason to drop or rewrite the row: an unknown
    string is stored verbatim, displayed verbatim, and simply has no canonical
    label yet."""
    alice = shelf_env.model_ids["alice.safetensors"]
    with shelf_env.server.hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET base_model = 'my private base v3' WHERE id = ?",
            (alice,),
        )

    row = next(
        r
        for r in shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
        if r["id"] == alice
    )
    assert row["base_model"] == "my private base v3"
    assert row["base_model_folded"] is None


def test_a_row_with_no_base_model_folds_to_null(shelf_env):
    """37% of real adapters record nothing. Null in, null out, no exception."""
    row = next(
        r
        for r in shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
        if r["id"] == shelf_env.model_ids["sd_xl_noname.safetensors"]
    )
    assert row["base_model"] is None
    assert row["base_model_folded"] is None


# ── Stack detection over HTTP (shelf plan F5) ───────────────────────────────


def test_every_stack_route_is_declared_owner_only():
    """Not the §16.3 locality tier its shelf neighbours are on: neither route
    takes, walks, writes or unlinks a host path."""
    for method, path in (
        ("GET", "/api/v1/model-stacks/proposals"),
        ("POST", "/api/v1/model-stacks"),
    ):
        declared = ROUTE_POLICIES.get((method, path))
        assert declared is not None, f"({method}, {path}) has no ROUTE_POLICIES entry"
        assert declared.policy is AccessPolicy.OWNER_ONLY, (
            f"{method} {path} declares {declared.policy}, not OWNER_ONLY"
        )
        assert declared.justification, f"{method} {path} declares no reason"


def test_the_dry_run_proposes_without_writing(shelf_env):
    """The house rule over HTTP: reading the proposals changes nothing.

    Asserted against the shelf's own rows rather than a purpose-built fixture,
    so it also proves detection is safe to call on a real shelf.
    """
    before = shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    stacked_before = [a for a in before if a.get("stack_id") is not None]

    r = shelf_env.owner.get(f"{API}/model-stacks/proposals")
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["proposals"], list)

    after = shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    stacked_after = [a for a in after if a.get("stack_id") is not None]
    assert len(stacked_after) == len(stacked_before)


def test_applying_a_stack_needs_at_least_two_models(shelf_env):
    bob = shelf_env.model_ids["bob.safetensors"]
    r = shelf_env.owner.post(f"{API}/model-stacks", json={"model_ids": [bob]})
    assert r.status_code == 400, r.text


def test_applying_refuses_more_than_one_run_is_worth(shelf_env):
    """A training run has tens of steps, not thousands."""
    r = shelf_env.owner.post(
        f"{API}/model-stacks", json={"model_ids": list(range(1, 500))}
    )
    assert r.status_code == 400, r.text


def test_the_ceiling_counts_unique_ids_not_repeats(shelf_env):
    """`apply_stack` de-dupes, so the guard has to as well.

    A client that repeated an id would otherwise be told it sent too many
    models while its actual selection was two. Reported by the review of #882.
    """
    bob = shelf_env.model_ids["bob.safetensors"]
    noname = shelf_env.model_ids["sd_xl_noname.safetensors"]
    r = shelf_env.owner.post(
        f"{API}/model-stacks",
        json={"model_ids": [bob, noname] * 400},
    )
    assert r.status_code != 400, r.text

    if r.status_code == 200:
        with shelf_env.server.hub.transaction() as conn:
            conn.execute(
                "UPDATE model SET stack_id = NULL, stack_position = NULL "
                "WHERE id IN (?, ?)",
                (bob, noname),
            )
            conn.execute(
                "DELETE FROM adapter_stack WHERE id = ?", (r.json()["stack_id"],)
            )


def test_applying_a_stack_collapses_the_rows_and_reports_the_count(shelf_env):
    # The two seeded adapters that are NOT already in a stack. `alice` and
    # `dana` carry a `stack_position`, so naming them here would be testing the
    # refusal below rather than the write.
    bob = shelf_env.model_ids["bob.safetensors"]
    noname = shelf_env.model_ids["sd_xl_noname.safetensors"]

    r = shelf_env.owner.post(
        f"{API}/model-stacks", json={"model_ids": [bob, noname], "name": "Duo"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["member_count"] == 2

    # A second attempt on the same rows is refused by state, not by shape: they
    # are in a stack now, so fewer than two survive the in-transaction re-read.
    again = shelf_env.owner.post(
        f"{API}/model-stacks", json={"model_ids": [bob, noname]}
    )
    assert again.status_code == 409, again.text

    # Put them back, so the module-scoped shelf is unchanged for other tests.
    with shelf_env.server.hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET stack_id = NULL, stack_position = NULL "
            "WHERE id IN (?, ?)",
            (bob, noname),
        )
        conn.execute("DELETE FROM adapter_stack WHERE id = ?", (body["stack_id"],))


# ── The icon verb over HTTP (shelf plan, the sixth verb) ────────────────────

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_every_icon_route_is_declared_owner_only():
    """Not §16.3: the store is PixlStash's own directory beside the hub, and no
    route here takes, walks or serves a caller-supplied host path."""
    for method, path in (
        ("POST", "/api/v1/models/{model_id}/icon"),
        ("GET", "/api/v1/model-icons/{sha256}"),
        ("POST", "/api/v1/models/icons/clear"),
    ):
        declared = ROUTE_POLICIES.get((method, path))
        assert declared is not None, f"({method}, {path}) has no ROUTE_POLICIES entry"
        assert declared.policy is AccessPolicy.OWNER_ONLY, (
            f"{method} {path} declares {declared.policy}, not OWNER_ONLY"
        )
        assert declared.justification, f"{method} {path} declares no reason"


def test_setting_an_icon_surfaces_it_on_the_list(shelf_env):
    alice = shelf_env.model_ids["alice.safetensors"]
    r = shelf_env.owner.post(
        f"{API}/models/{alice}/icon",
        files={"file": ("logo.png", _PNG, "image/png")},
    )
    assert r.status_code == 200, r.text
    digest = r.json()["icon_sha256"]

    rows = shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    row = next(a for a in rows if a["id"] == alice)
    assert row["icon_sha256"] == digest

    served = shelf_env.owner.get(f"{API}/model-icons/{digest}")
    assert served.status_code == 200, served.text
    assert served.headers["content-type"] == "image/png"
    assert served.content == _PNG

    shelf_env.owner.post(f"{API}/models/icons/clear", json={"ids": [alice]})


def test_two_models_given_one_logo_share_a_single_file(shelf_env):
    """The dedup that makes an icon the right object for a base-model mark:
    forty Flux checkpoints wanting one logo is the normal case."""
    alice = shelf_env.model_ids["alice.safetensors"]
    bob = shelf_env.model_ids["bob.safetensors"]
    first = shelf_env.owner.post(
        f"{API}/models/{alice}/icon", files={"file": ("a.png", _PNG, "image/png")}
    ).json()["icon_sha256"]
    second = shelf_env.owner.post(
        f"{API}/models/{bob}/icon", files={"file": ("b.png", _PNG, "image/png")}
    ).json()["icon_sha256"]
    assert first == second

    icons = os.path.join(os.path.dirname(shelf_env.server.hub.path), "icons")
    assert os.listdir(icons) == [f"{first}.webp"]

    shelf_env.owner.post(f"{API}/models/icons/clear", json={"ids": [alice, bob]})


def test_a_non_image_upload_is_refused(shelf_env):
    """Checked on the bytes, not on the filename or the declared type — both of
    which say `image/png` here."""
    alice = shelf_env.model_ids["alice.safetensors"]
    r = shelf_env.owner.post(
        f"{API}/models/{alice}/icon",
        files={"file": ("logo.png", b"<script>alert(1)</script>", "image/png")},
    )
    assert r.status_code == 400, r.text
    row = _model_row(shelf_env, alice)
    assert row["icon_sha256"] is None


def test_an_icon_path_that_is_not_a_digest_is_refused(shelf_env):
    """400 rather than 404: the segment is not an icon address at all."""
    r = shelf_env.owner.get(f"{API}/model-icons/not-a-digest")
    assert r.status_code == 400, r.text


def test_clearing_reports_what_changed_not_what_was_sent(shelf_env):
    """A selection of two where one had an icon is "1 cleared", not "2"."""
    alice = shelf_env.model_ids["alice.safetensors"]
    bob = shelf_env.model_ids["bob.safetensors"]
    shelf_env.owner.post(
        f"{API}/models/{alice}/icon", files={"file": ("a.png", _PNG, "image/png")}
    )

    r = shelf_env.owner.post(f"{API}/models/icons/clear", json={"ids": [alice, bob]})
    assert r.status_code == 200, r.text
    assert r.json()["cleared"] == [alice]
    assert _model_row(shelf_env, alice)["icon_sha256"] is None


def test_clearing_leaves_the_stored_file_for_the_rows_still_using_it(shelf_env):
    """The store is shared, so a clear must not delete a mark forty rows use."""
    alice = shelf_env.model_ids["alice.safetensors"]
    bob = shelf_env.model_ids["bob.safetensors"]
    digest = shelf_env.owner.post(
        f"{API}/models/{alice}/icon", files={"file": ("a.png", _PNG, "image/png")}
    ).json()["icon_sha256"]
    shelf_env.owner.post(
        f"{API}/models/{bob}/icon", files={"file": ("b.png", _PNG, "image/png")}
    )

    shelf_env.owner.post(f"{API}/models/icons/clear", json={"ids": [alice]})

    still = shelf_env.owner.get(f"{API}/model-icons/{digest}")
    assert still.status_code == 200, "bob's mark was deleted with alice's clear"
    assert _model_row(shelf_env, bob)["icon_sha256"] == digest

    shelf_env.owner.post(f"{API}/models/icons/clear", json={"ids": [bob]})


# ===========================================================================
# Add file (F6's remainder) — the loose-file path onto the shelf
# ===========================================================================
#
# The one shelf route that takes a host path in its body, because the file it
# adds is by definition in a folder nobody registered. Both authz directions are
# asserted below, and so is the ruling that makes it a *copy*: the owner's own
# file is not ours to unlink.

_ADD_FILE_ROUTE = (
    "POST",
    f"{API}/model-files",
    {"json": {"path": "/nowhere.safetensors"}},
)


@pytest.fixture
def loose_file(shelf_env, tmp_path):
    """A managed store to add into, and one adapter sitting outside every folder."""
    store = tmp_path / "managed"
    store.mkdir()
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    source = downloads / "loose.safetensors"
    write_adapter(source, name="Loose LoRA")

    with shelf_env.server.hub.transaction() as conn:
        store_id = int(
            conn.execute(
                "INSERT INTO model_folder (path, kind, owner, movable, created_at) "
                "VALUES (?, 'managed', 'pixlstash', 'root_only', "
                "'2026-08-12T00:00:00Z')",
                (str(store),),
            ).lastrowid
        )
    return SimpleNamespace(store=store, store_id=store_id, source=source)


def test_the_add_file_route_is_declared_local_owner_only():
    declared = ROUTE_POLICIES.get(("POST", "/api/v1/model-files"))
    assert declared is not None, (
        "(POST, /api/v1/model-files) has no ROUTE_POLICIES entry"
    )
    assert declared.policy is AccessPolicy.LOCAL_OWNER_ONLY, (
        f"POST /model-files declares {declared.policy}, not LOCAL_OWNER_ONLY"
    )
    assert declared.justification, "POST /model-files is on the tier with no reason"


def test_a_loose_file_lands_in_the_managed_store_and_is_listed_without_a_rescan(
    shelf_env, loose_file
):
    """The whole of F6's `Add file`: one call, and the row is there.

    No `destination_folder_id`, so this also pins the ruled default — the managed
    store — rather than the caller having to name it.
    """
    r = shelf_env.owner.post(
        f"{API}/model-files", json={"path": str(loose_file.source)}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == "loose.safetensors"
    assert body["folder_id"] == loose_file.store_id

    assert (loose_file.store / "loose.safetensors").exists()
    # A copy: the owner's own file is not ours to unlink.
    assert loose_file.source.exists()
    # And no rescan: the row is on the shelf as the call returns.
    listed = shelf_env.owner.get(f"{API}/adapters").json()["adapters"]
    assert "loose.safetensors" in _names(listed)


def test_a_file_already_inside_a_registered_folder_is_refused_not_duplicated(
    shelf_env, loose_file
):
    """A second copy under the same name, forever, is not what the owner meant."""
    inside = loose_file.store / "already-there.safetensors"
    write_adapter(inside)

    r = shelf_env.owner.post(f"{API}/model-files", json={"path": str(inside)})
    assert r.status_code == 409, r.text
    assert "Rescan" in r.text
    assert sorted(p.name for p in loose_file.store.iterdir()) == [
        "already-there.safetensors"
    ]


def test_a_file_that_is_not_a_model_is_refused_before_anything_is_copied(
    shelf_env, loose_file, tmp_path
):
    notes = tmp_path / "downloads" / "notes.txt"
    notes.write_text("not a model")
    r = shelf_env.owner.post(f"{API}/model-files", json={"path": str(notes)})
    assert r.status_code == 400, r.text
    assert list(loose_file.store.iterdir()) == []

    r = shelf_env.owner.post(
        f"{API}/model-files", json={"path": str(tmp_path / "nope.safetensors")}
    )
    assert r.status_code == 404, r.text
    assert list(loose_file.store.iterdir()) == []


def test_adding_the_same_name_twice_is_refused_rather_than_overwritten(
    shelf_env, loose_file, tmp_path
):
    """Nothing on the shelf overwrites bytes it did not write, this included."""
    assert (
        shelf_env.owner.post(
            f"{API}/model-files", json={"path": str(loose_file.source)}
        ).status_code
        == 200
    )
    second = tmp_path / "elsewhere"
    second.mkdir()
    twin = second / "loose.safetensors"
    write_adapter(twin, name="A different LoRA", pad=3)

    r = shelf_env.owner.post(f"{API}/model-files", json={"path": str(twin)})
    assert r.status_code == 409, r.text
    assert (
        loose_file.store / "loose.safetensors"
    ).stat().st_size == loose_file.source.stat().st_size, (
        "the second file was written over the first"
    )


def test_a_source_folder_is_never_a_destination_for_a_loose_file(
    shelf_env, loose_file, tmp_path
):
    output_root = tmp_path / "aitk-output"
    output_root.mkdir()
    with shelf_env.server.hub.transaction() as conn:
        source_id = int(
            conn.execute(
                "INSERT INTO model_folder (path, kind, owner, movable, created_at) "
                "VALUES (?, 'source', 'ai-toolkit', 'external', "
                "'2026-08-12T00:00:00Z')",
                (str(output_root),),
            ).lastrowid
        )
    r = shelf_env.owner.post(
        f"{API}/model-files",
        json={"path": str(loose_file.source), "destination_folder_id": source_id},
    )
    assert r.status_code == 400, r.text
    assert list(output_root.iterdir()) == []


def test_add_file_takes_the_same_job_slot_as_a_move_and_an_import(
    shelf_env, loose_file
):
    """One shelf I/O slot machine-wide: two writers would race for the filename."""
    assert SHELF_IO_LOCK.acquire(blocking=False), "the slot was left held"
    try:
        r = shelf_env.owner.post(
            f"{API}/model-files", json={"path": str(loose_file.source)}
        )
        assert r.status_code == 409, r.text
        assert "already running" in r.text
        assert list(loose_file.store.iterdir()) == []
    finally:
        SHELF_IO_LOCK.release()

    # Positive control: with the slot free, the same add is accepted.
    assert (
        shelf_env.owner.post(
            f"{API}/model-files", json={"path": str(loose_file.source)}
        ).status_code
        == 200
    )


def test_add_file_refuses_every_share_token(shelf_env):
    method, path, kwargs = _ADD_FILE_ROUTE
    for description, restriction in (
        ("add-file scoped probe", {"resource_type": "character", "resource_id": 1}),
        ("add-file unscoped probe", {}),
    ):
        token = _mint(shelf_env.owner, description, **restriction)
        client = _bearer(shelf_env.server, token)
        assert client.get(f"{API}/pictures").status_code == 200, (
            f"{description} is dead; the refusal below would prove nothing"
        )
        assert_real_route(shelf_env.server.api, method, path)
        r = client.request(method, path, **kwargs)
        assert r.status_code == 403, (
            f"{description} reached {method} {path}: {r.status_code} {r.text}"
        )


def test_add_file_is_reachable_locally_and_refused_remotely(shelf_env):
    """Both directions of the locality half: over-blocking is its own regression."""
    method, path, kwargs = _ADD_FILE_ROUTE
    for headers in ({}, _xff("192.168.1.9"), _xff("100.64.0.5")):
        r = shelf_env.owner.request(method, path, headers=headers, **kwargs)
        assert "restricted to local" not in r.text, (
            f"{method} {path} from {headers or 'loopback'} was refused as "
            f"non-local: {r.status_code} {r.text}"
        )

    r = shelf_env.owner.request(method, path, headers=_xff("8.8.8.8"), **kwargs)
    assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"
    assert "allow_remote_host_ops" in r.text


def test_the_picker_lists_model_files_only_when_it_is_asked_to(shelf_env, loose_file):
    """`Add file` needs files in the listing; every other picker needs them out.

    Both directions, because the flag is opt-in for a reason: a folder of 1,800
    adapters would bury its subfolders in them for a caller choosing a directory.
    """
    downloads = loose_file.source.parent
    (downloads / "notes.txt").write_text("not a model")
    (downloads / "sub").mkdir()

    plain = shelf_env.owner.get(
        f"{API}/filesystem/browse", params={"path": str(downloads)}
    )
    assert plain.status_code == 200, plain.text
    assert [entry["name"] for entry in plain.json()["entries"]] == ["sub"]

    picker = shelf_env.owner.get(
        f"{API}/filesystem/browse",
        params={"path": str(downloads), "include_model_files": True},
    )
    assert picker.status_code == 200, picker.text
    entries = picker.json()["entries"]
    # Directories first, then the model file — and never the .txt beside it.
    assert [entry["name"] for entry in entries] == ["sub", "loose.safetensors"]
    assert entries[1]["is_file"] is True
    assert entries[0]["is_dir"] is True


def test_the_copy_is_hashed_on_its_way_in_and_never_read_again(
    shelf_env, loose_file, monkeypatch
):
    """One read of the bytes, not two.

    The copy is hashed as it is written and the written file is read back once
    to verify it, so the digest is already known and proven when the row is
    registered. A scanner that hashed again would read a gigabyte a third time
    with the caller still waiting on the response. Asserted by counting, because
    the wrong version is *correct* — only slower — and would never fail an
    assertion about the row.
    """
    from pixlstash.services import model_folder_scanner

    rehashed = []
    monkeypatch.setattr(
        model_folder_scanner,
        "sha256_file",
        lambda path: rehashed.append(path) or "0" * 64,
    )

    r = shelf_env.owner.post(
        f"{API}/model-files", json={"path": str(loose_file.source)}
    )
    assert r.status_code == 200, r.text
    assert rehashed == [], (
        "the file was read again to compute a digest the copy already had"
    )

    expected = hashlib.sha256(loose_file.source.read_bytes()).hexdigest()
    row = shelf_env.server.hub.fetchone(
        "SELECT sha256 FROM model WHERE id = ?", (r.json()["model_id"],)
    )
    assert row["sha256"] == expected, "the row does not name the bytes on disk"


def test_a_checkpoint_added_this_way_keeps_the_digest_rather_than_deferring_it(
    shelf_env, loose_file, tmp_path
):
    """A scan leaves a checkpoint unhashed because reading 24 GB is the cost it
    exists to defer. Here the bytes went through a hash on their way in, so the
    read is already paid for and deferring would only schedule a second one."""
    source = tmp_path / "downloads" / "big.safetensors"
    write_checkpoint(source)

    r = shelf_env.owner.post(f"{API}/model-files", json={"path": str(source)})
    assert r.status_code == 200, r.text
    row = shelf_env.server.hub.fetchone(
        "SELECT file_kind, sha256 FROM model WHERE id = ?", (r.json()["model_id"],)
    )
    assert row["file_kind"] == "checkpoint"
    assert row["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
