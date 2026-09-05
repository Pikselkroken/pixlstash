"""Import must SEE the Scrapheap, and must offer a restore rather than a re-import.

``Picture.find`` defaults ``include_deleted=False``, and the one-shot import
called it that way, so a scrapheaped picture was invisible to import dedup:
re-importing a file whose picture sits in the Scrapheap created a brand-new
second row while the original was still there. A bulk "Keep cover only" cleanup
puts hundreds of pictures in the Scrapheap in one gesture, all of them copies of
files the user still has on disk, so the next import would silently undo the
cleanup and roughly double the bytes.

These tests pin both directions:

* a scrapheaped match is NOT imported again, is reported in its own bucket, and
  is offered for restore (rather than restored behind the user's back);
* a genuinely new file still imports: over-matching is its own regression;
* a permanently purged (and ledgered) file is not resurrected: with no row left
  there is nothing to match, so a deliberate re-import is a NEW picture;
* the buckets are disjoint and sum to the file total, on every path.
"""

import io
import os
import tempfile
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import select

from pixlstash.db_models import (
    DeletedFileLog,
    Picture,
    PictureSet,
    PictureSetMember,
)
from pixlstash.server import Server
from pixlstash.services.comfyui_service import _import_comfyui_outputs
from pixlstash.utils.image_processing.image_utils import ImageUtils
from tests.authz_guard import no_spa_fallback  # noqa: F401


API = "/api/v1"

pytestmark = pytest.mark.usefixtures("no_spa_fallback")


def _png_bytes(color=(100, 149, 237)) -> bytes:
    img = Image.new("RGB", (48, 48), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _bmp_bytes(color=(100, 149, 237), size=(48, 48)) -> bytes:
    """Uncompressed images make equal/different byte-size cases deterministic."""
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


@pytest.fixture
def owner_server():
    """A fresh Server with a claimed owner account and an authed client."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = os.path.join(tmp, "server-config.json")
        with Server(config_path) as srv:
            client = TestClient(srv.api, raise_server_exceptions=True)
            r = client.post(
                f"{API}/login",
                json={"username": "owner", "password": "example-owner-password"},
            )
            assert r.status_code == 200, r.text
            yield srv, client


# ---------------------------------------------------------------------------
# Helpers: the two import paths
# ---------------------------------------------------------------------------


def _wait_for_stage(client, staging_id, target=("completed", "failed"), timeout_s=30):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        r = client.get(f"{API}/pictures/import/staging/{staging_id}/status")
        assert r.status_code == 200, r.text
        last = r.json()
        if last["stage"] in target:
            return last
        time.sleep(0.1)
    raise AssertionError(f"Staging {staging_id} did not reach {target}: {last}")


def _staging_import(client, files):
    """Run the streaming-staging import over ``files`` and return its status."""
    staging_id = client.post(f"{API}/pictures/import/staging", json={}).json()[
        "staging_id"
    ]
    r = client.post(
        f"{API}/pictures/import/staging/{staging_id}/files",
        files=[("file", (name, data, "image/png")) for name, data in files],
    )
    assert r.status_code == 200, r.text
    r = client.post(f"{API}/pictures/import/staging/{staging_id}/commit")
    assert r.status_code == 200, r.text
    return _wait_for_stage(client, staging_id, target=("completed",))


def _wait_for_import_task(client, task_id, timeout_s=30):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        r = client.get(f"{API}/pictures/import/status", params={"task_id": task_id})
        assert r.status_code == 200, r.text
        last = r.json()
        if last["status"] in ("completed", "failed"):
            return last
        time.sleep(0.1)
    raise AssertionError(f"Import task {task_id} did not finish: {last}")


def _one_shot_import(client, files):
    """Run the one-shot ``POST /pictures/import`` and return its final status."""
    r = client.post(
        f"{API}/pictures/import",
        files=[("file", (name, data, "image/png")) for name, data in files],
    )
    assert r.status_code == 200, r.text
    return _wait_for_import_task(client, r.json()["task_id"])


def _picture_ids(srv, deleted=None):
    def _do(session):
        query = select(Picture.id)
        if deleted is not None:
            query = query.where(Picture.deleted.is_(deleted))
        return list(session.exec(query).all())

    return sorted(int(i) for i in srv.vault.db.run_task(_do))


def _row_count(srv) -> int:
    return srv.vault.db.run_task(lambda s: s.query(Picture).count())


def _scrapheap(client, picture_id):
    r = client.delete(f"{API}/pictures/{picture_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _purge_forever(client, ids):
    """Preview → redeem the confirm token → delete forever."""
    r = client.post(f"{API}/pictures/scrapheap/delete-preview", json={"ids": ids})
    assert r.status_code == 200, r.text
    token = r.json()["confirm_token"]
    r = client.request(
        "DELETE",
        f"{API}/pictures/scrapheap",
        json={"ids": ids, "confirm_token": token, "include_protected": True},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# The bug: a scrapheaped match must not become a second row
# ---------------------------------------------------------------------------


def test_staging_reimport_of_scrapheaped_picture_creates_no_second_row(owner_server):
    """Streaming-staging path: the match is reported in its own bucket."""
    srv, client = owner_server
    data = _png_bytes(color=(11, 22, 33))

    status = _staging_import(client, [("a.png", data)])
    assert status["imported_count"] == 1, status
    original_id = _picture_ids(srv)[0]

    _scrapheap(client, original_id)
    assert _picture_ids(srv, deleted=True) == [original_id]

    status = _staging_import(client, [("a-again.png", data)])
    assert status["imported_count"] == 0, status
    # NOT an ordinary duplicate: a distinct outcome the user can act on.
    assert status["duplicate_count"] == 0, status
    assert status["scrapheaped_count"] == 1, status
    assert status["scrapheaped_picture_ids"] == [original_id], status

    assert _row_count(srv) == 1, "The scrapheaped picture must not be duplicated"
    assert _picture_ids(srv, deleted=True) == [original_id], (
        "The match must stay in the Scrapheap: restoring is offered, not performed"
    )


def test_one_shot_reimport_of_scrapheaped_picture_creates_no_second_row(owner_server):
    """One-shot ``POST /pictures/import``: the path that carried the bug."""
    srv, client = owner_server
    data = _png_bytes(color=(44, 55, 66))

    status = _one_shot_import(client, [("a.png", data)])
    assert status["imported_count"] == 1, status
    original_id = _picture_ids(srv)[0]

    _scrapheap(client, original_id)

    status = _one_shot_import(client, [("a-again.png", data)])
    assert status["imported_count"] == 0, status
    assert status["duplicate_count"] == 0, status
    assert status["scrapheaped_count"] == 1, status
    assert status["scrapheaped_picture_ids"] == [original_id], status
    assert [entry["status"] for entry in status["results"]] == ["scrapheaped"], status

    assert _row_count(srv) == 1, "The scrapheaped picture must not be duplicated"
    assert _picture_ids(srv, deleted=True) == [original_id]


# ---------------------------------------------------------------------------
# The other direction: over-matching is its own regression
# ---------------------------------------------------------------------------


def test_new_file_still_imports_next_to_a_scrapheaped_match(owner_server):
    """A genuinely new file must still import while a sibling matches the heap."""
    srv, client = owner_server
    old = _png_bytes(color=(1, 2, 3))
    fresh = _png_bytes(color=(250, 240, 230))

    _staging_import(client, [("old.png", old)])
    original_id = _picture_ids(srv)[0]
    _scrapheap(client, original_id)

    status = _staging_import(client, [("old-again.png", old), ("fresh.png", fresh)])
    assert status["imported_count"] == 1, status
    assert status["scrapheaped_count"] == 1, status
    assert status["duplicate_count"] == 0, status
    assert status["failed_count"] == 0, status
    assert _row_count(srv) == 2, "The new file must still become a picture"
    assert len(_picture_ids(srv, deleted=False)) == 1


def test_buckets_are_disjoint_and_sum_to_the_file_total(owner_server):
    """new + live duplicate + scrapheaped match, in one batch."""
    srv, client = owner_server
    live = _png_bytes(color=(9, 9, 9))
    heaped = _png_bytes(color=(80, 80, 80))
    fresh = _png_bytes(color=(200, 30, 30))

    _staging_import(client, [("live.png", live), ("heaped.png", heaped)])
    ids = _picture_ids(srv)
    heaped_id = srv.vault.db.run_task(
        lambda s: s.exec(
            select(Picture.id).where(Picture.original_file_name == "heaped.png")
        ).first()
    )
    assert heaped_id in ids
    _scrapheap(client, heaped_id)

    status = _staging_import(
        client,
        [("live2.png", live), ("heaped2.png", heaped), ("fresh.png", fresh)],
    )
    assert status["imported_count"] == 1, status
    assert status["duplicate_count"] == 1, status
    assert status["scrapheaped_count"] == 1, status
    assert status["failed_count"] == 0, status
    assert (
        status["imported_count"]
        + status["duplicate_count"]
        + status["scrapheaped_count"]
        + status["failed_count"]
        + (status["cancelled_count"] or 0)
        == status["total"]
        == 3
    ), status


def test_one_shot_buckets_sum_to_the_file_total(owner_server):
    """Same arithmetic on the one-shot path (imported + duplicate + scrapheaped)."""
    srv, client = owner_server
    live = _png_bytes(color=(7, 7, 7))
    heaped = _png_bytes(color=(70, 70, 70))
    fresh = _png_bytes(color=(170, 30, 30))

    _one_shot_import(client, [("live.png", live), ("heaped.png", heaped)])
    heaped_id = srv.vault.db.run_task(
        lambda s: s.exec(
            select(Picture.id).where(Picture.original_file_name == "heaped.png")
        ).first()
    )
    _scrapheap(client, heaped_id)

    status = _one_shot_import(
        client,
        [("live2.png", live), ("heaped2.png", heaped), ("fresh.png", fresh)],
    )
    assert status["imported_count"] == 1, status
    assert status["duplicate_count"] == 1, status
    assert status["scrapheaped_count"] == 1, status
    assert (
        status["imported_count"]
        + status["duplicate_count"]
        + status["scrapheaped_count"]
        == status["total"]
        == 3
    ), status


# ---------------------------------------------------------------------------
# Edge cases settled deliberately
# ---------------------------------------------------------------------------


def test_several_copies_of_one_scrapheaped_picture_count_per_file(owner_server):
    """Counts are per FILE; the restore offer is per PICTURE."""
    srv, client = owner_server
    data = _png_bytes(color=(123, 45, 67))

    _staging_import(client, [("a.png", data)])
    original_id = _picture_ids(srv)[0]
    _scrapheap(client, original_id)

    status = _staging_import(
        client, [("a1.png", data), ("a2.png", data), ("a3.png", data)]
    )
    assert status["scrapheaped_count"] == 3, status
    assert status["scrapheaped_picture_ids"] == [original_id], status
    assert status["imported_count"] == 0 and status["duplicate_count"] == 0, status
    assert _row_count(srv) == 1


def test_purged_and_ledgered_file_imports_as_new_and_is_not_resurrected(owner_server):
    """Delete-forever removes the row, so a re-import is a NEW picture."""
    srv, client = owner_server
    data = _png_bytes(color=(210, 105, 30))

    _staging_import(client, [("gone.png", data)])
    original_id = _picture_ids(srv)[0]
    original_path = srv.vault.db.run_task(
        lambda s: s.get(Picture, original_id).file_path
    )
    _scrapheap(client, original_id)
    outcome = _purge_forever(client, [original_id])
    assert outcome["deleted_count"] == 1, outcome
    assert _row_count(srv) == 0

    ledger_before = srv.vault.db.run_task(lambda s: s.query(DeletedFileLog).count())
    assert ledger_before >= 1, "the purge must have written the permanent-delete ledger"

    status = _staging_import(client, [("gone.png", data)])
    # Nothing to match and nothing to restore: the ledger stops a SNAPSHOT
    # RESTORE resurrecting the destroyed row, not the owner re-importing a file
    # they still have.
    assert status["scrapheaped_count"] == 0, status
    assert status["duplicate_count"] == 0, status
    assert status["imported_count"] == 1, status

    new_ids = _picture_ids(srv)
    assert len(new_ids) == 1
    # SQLite hands the emptied table's rowid back out, so identity is judged on
    # the vault file, not on the integer id: a resurrected row would carry the
    # destroyed picture's path, a fresh import gets its own uuid.
    fresh = srv.vault.db.run_task(lambda s: s.get(Picture, new_ids[0]))
    assert fresh.file_path != original_path, (
        "the purged row must not come back; this is a new picture"
    )
    assert fresh.deleted is False
    assert (
        srv.vault.db.run_task(lambda s: s.query(DeletedFileLog).count())
        == ledger_before
    ), "a fresh import must not retract the permanent-deletion ledger"


def test_live_row_wins_over_a_scrapheaped_row_with_the_same_hash(owner_server):
    """Both rows can exist (the old bug made them). A live match is a duplicate."""
    srv, client = owner_server
    data = _png_bytes(color=(15, 240, 90))

    _staging_import(client, [("a.png", data)])
    live_id = _picture_ids(srv)[0]

    # Forge the pre-fix state: a second, soft-deleted row with the same content.
    def _clone_as_deleted(session):
        live = session.get(Picture, live_id)
        clone = Picture(
            file_path=live.file_path,
            pixel_sha=live.pixel_sha,
            size_bytes=live.size_bytes,
            deleted=True,
        )
        session.add(clone)
        session.commit()
        session.refresh(clone)
        return clone.id

    heaped_id = srv.vault.db.run_task(_clone_as_deleted)

    status = _staging_import(client, [("a-again.png", data)])
    assert status["duplicate_count"] == 1, status
    assert status["scrapheaped_count"] == 0, status
    assert status["scrapheaped_picture_ids"] == [], status
    assert _row_count(srv) == 2, "no third row"
    assert _picture_ids(srv, deleted=True) == [heaped_id]


def test_scrapheaped_member_of_a_locked_set_is_still_reported(owner_server):
    """A lock freezes edits and destruction, not the fact that content exists.

    Detection must not consult locks: hiding the match would put the re-imported
    copy back on disk, which is the exact doubling this change exists to stop.
    """
    srv, client = owner_server
    data = _png_bytes(color=(60, 20, 180))

    _staging_import(client, [("a.png", data)])
    original_id = _picture_ids(srv)[0]
    _scrapheap(client, original_id)

    def _lock_set_around(session, picture_id):
        picture_set = PictureSet(name="frozen")
        session.add(picture_set)
        session.commit()
        session.refresh(picture_set)
        session.add(PictureSetMember(set_id=picture_set.id, picture_id=picture_id))
        picture_set.locked = True
        session.add(picture_set)
        session.commit()
        return picture_set.id

    srv.vault.db.run_task(_lock_set_around, original_id)

    status = _staging_import(client, [("a-again.png", data)])
    assert status["scrapheaped_count"] == 1, status
    assert status["scrapheaped_picture_ids"] == [original_id], status
    assert _row_count(srv) == 1


def test_scrapheaped_stack_member_is_offered_and_refolds_on_restore(owner_server):
    """A stacked match needs no special path: the shipped restore re-folds it.

    ``restore_scrapheap`` already calls ``normalize_stack_positions`` for every
    affected stack, which is exactly why this change reuses it instead of
    writing a second restore.
    """
    srv, client = owner_server
    leader = _png_bytes(color=(5, 5, 5))
    member = _png_bytes(color=(6, 6, 6))

    _staging_import(client, [("leader.png", leader), ("member.png", member)])

    leader_id, member_id = _picture_ids(srv)
    r = client.post(f"{API}/stacks", json={"picture_ids": [leader_id, member_id]})
    assert r.status_code == 200, r.text
    stack_id = srv.vault.db.run_task(lambda s: s.get(Picture, leader_id).stack_id)
    assert stack_id is not None
    _scrapheap(client, member_id)

    status = _staging_import(client, [("member-again.png", member)])
    assert status["scrapheaped_count"] == 1, status
    assert status["scrapheaped_picture_ids"] == [member_id], status
    assert _row_count(srv) == 2, "no third row inside the stack"

    r = client.post(
        f"{API}/pictures/scrapheap/restore", json={"picture_ids": [member_id]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["restored_count"] == 1

    positions = srv.vault.db.run_task(
        lambda s: {
            int(p.id): p.stack_position
            for p in s.exec(select(Picture).where(Picture.stack_id == stack_id)).all()
        }
    )
    assert set(positions) == {leader_id, member_id}
    assert sorted(positions.values()) == [0, 1], (
        f"restore must re-fold the member into the stack ordering: {positions}"
    )


# ---------------------------------------------------------------------------
# The offer: the ids the import hands back drive the SHIPPED restore endpoint
# ---------------------------------------------------------------------------


def test_offered_ids_restore_through_the_shipped_endpoint(owner_server):
    """The import reports; the user chooses; the existing restore route acts."""
    srv, client = owner_server
    data = _png_bytes(color=(33, 99, 200))

    _staging_import(client, [("a.png", data)])
    original_id = _picture_ids(srv)[0]
    _scrapheap(client, original_id)

    status = _staging_import(client, [("a-again.png", data)])
    offered = status["scrapheaped_picture_ids"]
    assert offered == [original_id]

    r = client.post(f"{API}/pictures/scrapheap/restore", json={"picture_ids": offered})
    assert r.status_code == 200, r.text
    assert r.json()["restored_count"] == 1, r.text
    assert _picture_ids(srv, deleted=True) == []
    assert _picture_ids(srv, deleted=False) == [original_id]
    assert _row_count(srv) == 1


def test_offer_reports_honestly_when_the_match_was_purged_meanwhile(owner_server):
    """A match past its retention deadline can be swept before the user clicks.

    The offer is not a promise. The shipped restore endpoint reports what it
    really restored, so the caller can say "0 of 1" instead of claiming success.
    """
    srv, client = owner_server
    data = _png_bytes(color=(240, 200, 10))

    _staging_import(client, [("a.png", data)])
    original_id = _picture_ids(srv)[0]
    _scrapheap(client, original_id)

    status = _staging_import(client, [("a-again.png", data)])
    offered = status["scrapheaped_picture_ids"]
    assert offered == [original_id]

    # The auto-purge sweep (or the user) destroys it before the offer is taken.
    _purge_forever(client, offered)
    assert _row_count(srv) == 0

    r = client.post(f"{API}/pictures/scrapheap/restore", json={"picture_ids": offered})
    assert r.status_code == 200, r.text
    assert r.json()["restored_count"] == 0, (
        "restoring a purged picture must report 0, not pretend it worked"
    )


# ---------------------------------------------------------------------------
# Authz: import status is owner data, not a progress counter
#
# Both status routes were declared ANY_TOKEN on the grounds that they return
# "progress/stage/counts only". They do not: the completed payloads carry
# `results[].picture_id`, `results[].file` (the vault-relative filename) and
# `scrapheaped_picture_ids`, for pictures anywhere in the vault. A resource-
# scoped READ token that is refused a picture's thumbnail was handed that same
# picture's id and filename here. Both are now OWNER_ONLY at the gate.
#
# Both directions, on both entry points a share token has (the Authorization
# header and the ?token= query parameter), because a gate that covered one is a
# hole rather than a policy. The owner side is asserted on a REAL completed task
# so this cannot pass by returning 404 to everyone.
# ---------------------------------------------------------------------------


def _scoped_read_token(client, srv, picture_id) -> str:
    """A READ token scoped to a picture set holding exactly *picture_id*."""
    r = client.post(f"{API}/picture_sets", json={"name": "shared-with-a-guest"})
    assert r.status_code == 200, r.text
    set_id = r.json()["picture_set"]["id"]

    def add_member(session):
        session.add(PictureSetMember(set_id=set_id, picture_id=picture_id))
        session.commit()

    srv.vault.db.run_task(add_member)
    r = client.post(
        f"{API}/users/me/token",
        json={
            "description": "share",
            "scope": "READ",
            "resource_type": "picture_set",
            "resource_id": set_id,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_import_status_is_owner_only_and_leaks_no_out_of_scope_ids(owner_server):
    """Out-of-scope (scoped READ token) → 403; the owner still reads it → 200."""
    srv, owner = owner_server
    r = owner.post(
        f"{API}/pictures/import",
        files=[
            ("file", ("shared.png", _png_bytes((9, 9, 9)), "image/png")),
            ("file", ("private.png", _png_bytes((77, 88, 99)), "image/png")),
        ],
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]
    status = _wait_for_import_task(owner, task_id)
    assert status["status"] == "completed", status

    shared_id, private_id = _picture_ids(srv)
    token = _scoped_read_token(owner, srv, shared_id)
    scoped = TestClient(srv.api, raise_server_exceptions=True)

    # The scope is real: the token reaches its own picture and not the other.
    assert (
        scoped.get(
            f"{API}/pictures/{shared_id}/metadata",
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 200
    )
    assert (
        scoped.get(
            f"{API}/pictures/{private_id}/metadata",
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 403
    )

    # ...so it must not read the import receipt naming that same picture, on
    # either entry point a share token has.
    status_url = f"{API}/pictures/import/status"
    by_header = scoped.get(
        status_url,
        params={"task_id": task_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    by_query = scoped.get(status_url, params={"task_id": task_id, "token": token})
    for r in (by_header, by_query):
        assert r.status_code == 403, (
            f"a scoped READ token must not read import status, got "
            f"{r.status_code}: {r.text}"
        )
        assert str(private_id) not in r.text

    # The owner is not over-blocked, and the payload really is per-object data.
    r = owner.get(f"{API}/pictures/import/status", params={"task_id": task_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert {entry["picture_id"] for entry in body["results"]} == {
        shared_id,
        private_id,
    }, body
    assert all(entry.get("file") for entry in body["results"]), body


def test_staging_status_is_owner_only_and_the_owner_still_polls_it(owner_server):
    """Same contract on the async staging sibling, both directions."""
    srv, owner = owner_server
    staging_id = owner.post(f"{API}/pictures/import/staging", json={}).json()[
        "staging_id"
    ]
    r = owner.post(
        f"{API}/pictures/import/staging/{staging_id}/files",
        files=[("file", ("a.png", _png_bytes((5, 6, 7)), "image/png"))],
    )
    assert r.status_code == 200, r.text
    assert owner.post(f"{API}/pictures/import/staging/{staging_id}/commit").status_code
    _wait_for_stage(owner, staging_id, target=("completed",))

    picture_id = _picture_ids(srv)[0]
    token = _scoped_read_token(owner, srv, picture_id)
    scoped = TestClient(srv.api, raise_server_exceptions=True)
    url = f"{API}/pictures/import/staging/{staging_id}/status"

    r = scoped.get(url, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403, r.text
    r = scoped.get(url, params={"token": token})
    assert r.status_code == 403, r.text

    # The owner is not over-blocked: the same poll the import UI runs still works.
    r = owner.get(url)
    assert r.status_code == 200, r.text
    assert r.json()["stage"] == "completed", r.json()


# ---------------------------------------------------------------------------
# Sampled SHA is a candidate key, never final file identity
# ---------------------------------------------------------------------------


def test_one_shot_same_size_sampled_collision_imports_both(owner_server, monkeypatch):
    srv, client = owner_server
    first = _bmp_bytes((1, 2, 3))
    second = _bmp_bytes((4, 5, 6))
    assert len(first) == len(second)
    monkeypatch.setattr(
        ImageUtils,
        "calculate_hash_from_bytes",
        staticmethod(lambda _data: "forced-sampled-collision"),
    )

    status = _one_shot_import(client, [("a.bmp", first), ("b.bmp", second)])

    assert status["imported_count"] == 2, status
    assert status["duplicate_count"] == 0, status
    assert _row_count(srv) == 2


def test_one_shot_different_size_sampled_collision_imports_both(
    owner_server, monkeypatch
):
    srv, client = owner_server
    first = _bmp_bytes((7, 8, 9), (48, 48))
    second = _bmp_bytes((7, 8, 9), (49, 48))
    assert len(first) != len(second)
    monkeypatch.setattr(
        ImageUtils,
        "calculate_hash_from_bytes",
        staticmethod(lambda _data: "forced-sampled-collision"),
    )

    status = _one_shot_import(client, [("a.bmp", first), ("b.bmp", second)])

    assert status["imported_count"] == 2, status
    assert status["duplicate_count"] == 0, status
    assert _row_count(srv) == 2


def test_full_hash_mismatch_does_not_offer_scrapheap_restore(owner_server, monkeypatch):
    srv, client = owner_server
    old = _bmp_bytes((10, 20, 30))
    different = _bmp_bytes((30, 20, 10))
    assert len(old) == len(different)
    monkeypatch.setattr(
        ImageUtils,
        "calculate_hash_from_bytes",
        staticmethod(lambda _data: "forced-sampled-collision"),
    )

    assert _one_shot_import(client, [("old.bmp", old)])["imported_count"] == 1
    _scrapheap(client, _picture_ids(srv)[0])
    status = _one_shot_import(client, [("different.bmp", different)])

    assert status["imported_count"] == 1, status
    assert status["scrapheaped_count"] == 0, status
    assert status["scrapheaped_picture_ids"] == [], status
    assert _row_count(srv) == 2


def test_staging_and_comfy_batches_keep_sampled_collisions_distinct(
    owner_server, monkeypatch
):
    srv, client = owner_server
    first = _bmp_bytes((40, 50, 60))
    second = _bmp_bytes((60, 50, 40))
    assert len(first) == len(second)
    monkeypatch.setattr(
        ImageUtils,
        "calculate_hash_from_file_path",
        staticmethod(lambda _path: "forced-staging-collision"),
    )

    status = _staging_import(client, [("a.bmp", first), ("b.bmp", second)])
    assert status["imported_count"] == 2, status
    assert status["duplicate_count"] == 0, status

    monkeypatch.setattr(
        ImageUtils,
        "calculate_hash_from_bytes",
        staticmethod(lambda _data: "forced-comfy-collision"),
    )
    third = _bmp_bytes((70, 80, 90))
    fourth = _bmp_bytes((90, 80, 70))
    new_ids, duplicate_ids = _import_comfyui_outputs(
        srv, [(third, ".bmp"), (fourth, ".bmp")]
    )
    assert len(new_ids) == 2
    assert duplicate_ids == []

    repeated_new, repeated_duplicates = _import_comfyui_outputs(
        srv, [(third, ".bmp"), (fourth, ".bmp")]
    )
    assert repeated_new == []
    assert sorted(repeated_duplicates) == sorted(new_ids)
