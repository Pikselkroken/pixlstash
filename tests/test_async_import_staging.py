"""Tests for the async streaming-staging import (#459).

Covers the two-phase flow end to end:

* Phase A (unsafe window): open a staging session, stream files into it.
* Phase B (safe window): commit hands off to a background ``PictureImportTask``
  on the shared task runner; progress is polled via the staging status endpoint.

Also covers both authz directions on the owner-only mutating routes: a READ
scoped token is denied (403) and the owner is allowed (200 / works) - over-blocking
the owner would be its own regression.
"""

import io
import os
import tempfile
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import select

from pixlstash.db_models import Character, Picture, PictureSet, PictureSetMember, Tag
from pixlstash.server import Server
from tests.authz_guard import no_spa_fallback  # noqa: F401

API = "/api/v1"

# The SPA catch-all answers unmatched GETs with 200, so a wrong URL can make a
# positive assertion vacuous. See tests/authz_guard.py.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")


def _png_bytes(color=(100, 149, 237)) -> bytes:
    img = Image.new("RGB", (48, 48), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_set(srv, name="drop-set") -> int:
    def _do(session):
        s = PictureSet(name=name)
        session.add(s)
        session.commit()
        session.refresh(s)
        return s.id

    return srv.vault.db.run_task(_do)


def _make_character(srv, name="drop-char") -> int:
    def _do(session):
        c = Character(name=name)
        session.add(c)
        session.commit()
        session.refresh(c)
        return c.id

    return srv.vault.db.run_task(_do)


def _set_member_ids(srv, set_id) -> set:
    return set(
        srv.vault.db.run_task(
            lambda s: [
                m.picture_id
                for m in s.exec(
                    select(PictureSetMember).where(PictureSetMember.set_id == set_id)
                ).all()
            ]
        )
    )


def _tags_for(srv, picture_id) -> set:
    return set(
        srv.vault.db.run_task(
            lambda s: [
                t.tag
                for t in s.exec(select(Tag).where(Tag.picture_id == picture_id)).all()
            ]
        )
    )


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


def _wait_for_stage(client, staging_id, target=("completed", "failed"), timeout_s=30):
    """Poll the staging status until it reaches a terminal stage."""
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


def test_staging_import_happy_path(owner_server):
    """Open → stream one file → commit → the background task imports it."""
    _srv, client = owner_server

    r = client.post(f"{API}/pictures/import/staging", json={"total_files": 1})
    assert r.status_code == 200, r.text
    staging_id = r.json()["staging_id"]
    assert r.json()["safe_threshold"] == 1

    r = client.post(
        f"{API}/pictures/import/staging/{staging_id}/files",
        files=[("file", ("photo.png", _png_bytes(), "image/png"))],
    )
    assert r.status_code == 200, r.text
    assert r.json()["staged"] == 1
    assert r.json()["received"] == ["photo.png"]

    r = client.post(f"{API}/pictures/import/staging/{staging_id}/commit")
    assert r.status_code == 200, r.text
    assert r.json()["staged_count"] == 1
    assert r.json()["task_id"]

    status = _wait_for_stage(client, staging_id, target=("completed",))
    assert status["stage"] == "completed", status
    assert status["imported_count"] == 1
    assert status["duplicate_count"] == 0

    # The picture is really in the vault, and the staging dir is cleaned up.
    r = client.get(f"{API}/pictures")
    assert r.status_code == 200
    assert len(r.json()) == 1
    staging_dir = os.path.join(_srv.vault.image_root, ".staging", staging_id)
    assert not os.path.isdir(staging_dir), "Staging dir must be removed after import"


def test_staging_import_dedupes_identical_files(owner_server):
    """Two byte-identical staged files import once; the second is a duplicate."""
    _srv, client = owner_server
    data = _png_bytes(color=(10, 200, 30))

    r = client.post(f"{API}/pictures/import/staging", json={})
    staging_id = r.json()["staging_id"]

    r = client.post(
        f"{API}/pictures/import/staging/{staging_id}/files",
        files=[
            ("file", ("a.png", data, "image/png")),
            ("file", ("b.png", data, "image/png")),
        ],
    )
    assert r.status_code == 200, r.text
    assert r.json()["staged"] == 2

    r = client.post(f"{API}/pictures/import/staging/{staging_id}/commit")
    assert r.status_code == 200, r.text

    status = _wait_for_stage(client, staging_id, target=("completed",))
    assert status["imported_count"] == 1, status
    assert status["duplicate_count"] == 1, status

    imported = _srv.vault.db.run_task(lambda s: s.query(Picture).count())
    assert imported == 1


def test_staging_files_rejects_unsupported_extension(owner_server):
    """A non-media file is skipped and reported, not staged."""
    _srv, client = owner_server
    r = client.post(f"{API}/pictures/import/staging", json={})
    staging_id = r.json()["staging_id"]

    r = client.post(
        f"{API}/pictures/import/staging/{staging_id}/files",
        files=[("file", ("notes.exe", b"nope", "application/octet-stream"))],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["staged"] == 0
    assert body["skipped"] == ["notes.exe"]

    # Committing an empty session is a 400.
    r = client.post(f"{API}/pictures/import/staging/{staging_id}/commit")
    assert r.status_code == 400, r.text


def test_staging_cancel_discards_without_importing(owner_server):
    """Cancelling before commit removes the staged files and imports nothing."""
    _srv, client = owner_server
    r = client.post(f"{API}/pictures/import/staging", json={})
    staging_id = r.json()["staging_id"]
    staging_dir = os.path.join(_srv.vault.image_root, ".staging", staging_id)

    client.post(
        f"{API}/pictures/import/staging/{staging_id}/files",
        files=[("file", ("x.png", _png_bytes(), "image/png"))],
    )
    assert os.path.isdir(staging_dir)

    r = client.delete(f"{API}/pictures/import/staging/{staging_id}")
    assert r.status_code == 200, r.text
    assert r.json()["stage"] == "cancelled"
    assert not os.path.isdir(staging_dir), "Cancel must remove the staging dir"

    # Session is gone (404), and no pictures were imported.
    r = client.get(f"{API}/pictures/import/staging/{staging_id}/status")
    assert r.status_code == 404
    assert client.get(f"{API}/pictures").json() == []


def test_staging_status_unknown_session_404(owner_server):
    _srv, client = owner_server
    r = client.get(f"{API}/pictures/import/staging/does-not-exist/status")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Authz - both directions on the owner-only mutating routes
# ---------------------------------------------------------------------------


def _read_token(client) -> str:
    r = client.post(
        f"{API}/users/me/token",
        json={"description": "read", "scope": "READ"},
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_staging_open_denied_for_read_token_allowed_for_owner(owner_server):
    """Out-of-scope (READ token) → 403; in-scope (owner) → 200 (not over-blocked)."""
    _srv, owner = owner_server
    token = _read_token(owner)

    scoped = TestClient(_srv.api, raise_server_exceptions=True)
    r = scoped.post(
        f"{API}/pictures/import/staging",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403, (
        f"READ token must not open a staging session, got {r.status_code}: {r.text}"
    )

    # The owner is not over-blocked.
    r = owner.post(f"{API}/pictures/import/staging", json={})
    assert r.status_code == 200, r.text


def test_staging_files_and_commit_denied_for_read_token(owner_server):
    """READ token is blocked from streaming into and committing a session."""
    _srv, owner = owner_server
    token = _read_token(owner)
    auth = {"Authorization": f"Bearer {token}"}

    # Owner opens a real session (so the routes exist to attack).
    staging_id = owner.post(f"{API}/pictures/import/staging", json={}).json()[
        "staging_id"
    ]

    scoped = TestClient(_srv.api, raise_server_exceptions=True)
    r = scoped.post(
        f"{API}/pictures/import/staging/{staging_id}/files",
        files=[("file", ("x.png", _png_bytes(), "image/png"))],
        headers=auth,
    )
    assert r.status_code == 403, r.text

    r = scoped.post(f"{API}/pictures/import/staging/{staging_id}/commit", headers=auth)
    assert r.status_code == 403, r.text

    r = scoped.delete(f"{API}/pictures/import/staging/{staging_id}", headers=auth)
    assert r.status_code == 403, r.text


def test_scrapheap_delete_preview_denied_for_read_token_allowed_for_owner(owner_server):
    """The delete-preview route returns per-object absolute on-disk paths, so it is
    OWNER_ONLY: a READ token → 403, the owner → 200 (empty scrapheap, not over-blocked)."""
    _srv, owner = owner_server
    token = _read_token(owner)

    scoped = TestClient(_srv.api, raise_server_exceptions=True)
    r = scoped.post(
        f"{API}/pictures/scrapheap/delete-preview",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403, (
        f"READ token must not preview scrapheap deletions (leaks on-disk paths), "
        f"got {r.status_code}: {r.text}"
    )

    # The owner is not over-blocked - an empty scrapheap previews as zero counts.
    r = owner.post(f"{API}/pictures/scrapheap/delete-preview", json={})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Addition 1 - server-side set / character association
# ---------------------------------------------------------------------------


def _import_one(client, open_body, filename="a.png", data=None):
    """Open → stream one image → commit → wait; return the imported picture id."""
    data = data if data is not None else _png_bytes()
    staging_id = client.post(f"{API}/pictures/import/staging", json=open_body).json()[
        "staging_id"
    ]
    r = client.post(
        f"{API}/pictures/import/staging/{staging_id}/files",
        files=[("file", (filename, data, "image/png"))],
    )
    assert r.status_code == 200, r.text
    r = client.post(f"{API}/pictures/import/staging/{staging_id}/commit")
    assert r.status_code == 200, r.text
    _wait_for_stage(client, staging_id, target=("completed",))
    return staging_id


def _only_picture_id(srv):
    return srv.vault.db.run_task(lambda s: s.exec(select(Picture.id)).first())


def test_drop_to_set_associates_imported_pictures(owner_server):
    srv, client = owner_server
    set_id = _make_set(srv)
    _import_one(client, {"set_id": set_id})
    pic_id = _only_picture_id(srv)
    assert pic_id in _set_member_ids(srv, set_id), "Imported picture must join the set"


def test_drop_to_character_sets_pending_assignment(owner_server):
    srv, client = owner_server
    character_id = _make_character(srv)
    _import_one(client, {"character_id": character_id})
    pic_id = _only_picture_id(srv)
    pending = srv.vault.db.run_task(
        lambda s: s.get(Picture, pic_id).pending_character_id
    )
    assert pending == character_id, (
        "Imported picture must carry the pending character assignment "
        "(resolved after face extraction)"
    )


def test_drop_to_set_and_character_together(owner_server):
    srv, client = owner_server
    set_id = _make_set(srv)
    character_id = _make_character(srv)
    _import_one(client, {"set_id": set_id, "character_id": character_id})
    pic_id = _only_picture_id(srv)
    assert pic_id in _set_member_ids(srv, set_id)
    assert (
        srv.vault.db.run_task(lambda s: s.get(Picture, pic_id).pending_character_id)
        == character_id
    )


def test_project_id_still_associates(owner_server):
    srv, client = owner_server
    project_id = srv.vault.db.run_task(lambda s: _make_project(s))
    _import_one(client, {"project_id": project_id})
    pic_id = _only_picture_id(srv)
    assert (
        srv.vault.db.run_task(lambda s: s.get(Picture, pic_id).project_id) == project_id
    )


def _make_project(session):
    from pixlstash.db_models import Project

    p = Project(name="drop-project")
    session.add(p)
    session.commit()
    session.refresh(p)
    return p.id


def test_open_with_nonexistent_set_errors(owner_server):
    _srv, client = owner_server
    r = client.post(f"{API}/pictures/import/staging", json={"set_id": 999999})
    assert r.status_code == 404, r.text


def test_open_with_nonexistent_character_errors(owner_server):
    _srv, client = owner_server
    r = client.post(f"{API}/pictures/import/staging", json={"character_id": 999999})
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Addition 2 - zip archives + .txt caption sidecars
# ---------------------------------------------------------------------------


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_staging_zip_imports_all_images(owner_server):
    srv, client = owner_server
    zip_data = _zip_bytes(
        {
            "one.png": _png_bytes(color=(10, 20, 30)),
            "two.png": _png_bytes(color=(200, 100, 50)),
            "three.png": _png_bytes(color=(5, 150, 250)),
        }
    )
    staging_id = client.post(f"{API}/pictures/import/staging", json={}).json()[
        "staging_id"
    ]
    r = client.post(
        f"{API}/pictures/import/staging/{staging_id}/files",
        files=[("file", ("bundle.zip", zip_data, "application/zip"))],
    )
    assert r.status_code == 200, r.text
    assert r.json()["staged"] == 3, r.json()
    assert r.json()["received"] == ["bundle.zip"]

    r = client.post(f"{API}/pictures/import/staging/{staging_id}/commit")
    assert r.status_code == 200, r.text
    status = _wait_for_stage(client, staging_id, target=("completed",))
    assert status["imported_count"] == 3, status
    assert srv.vault.db.run_task(lambda s: s.query(Picture).count()) == 3


def test_staging_txt_sidecar_sets_tags(owner_server):
    srv, client = owner_server
    staging_id = client.post(f"{API}/pictures/import/staging", json={}).json()[
        "staging_id"
    ]
    # Comma-separated .txt = tag list (the legacy single-shot sidecar rule).
    r = client.post(
        f"{API}/pictures/import/staging/{staging_id}/files",
        files=[
            ("file", ("kitten.png", _png_bytes(), "image/png")),
            ("file", ("kitten.txt", b"cat, cute, fluffy", "text/plain")),
        ],
    )
    assert r.status_code == 200, r.text
    assert r.json()["staged"] == 1
    assert r.json()["sidecars"] == 1

    client.post(f"{API}/pictures/import/staging/{staging_id}/commit")
    _wait_for_stage(client, staging_id, target=("completed",))
    pic_id = _only_picture_id(srv)
    tags = _tags_for(srv, pic_id)
    assert {"cat", "cute", "fluffy"} <= tags, f"Sidecar tags missing: {tags}"


def test_staging_orphan_sidecar_is_skipped_gracefully(owner_server):
    srv, client = owner_server
    staging_id = client.post(f"{API}/pictures/import/staging", json={}).json()[
        "staging_id"
    ]
    # Sidecar whose stem matches no staged image.
    r = client.post(
        f"{API}/pictures/import/staging/{staging_id}/files",
        files=[
            ("file", ("photo.png", _png_bytes(), "image/png")),
            ("file", ("nomatch.txt", b"alpha, beta", "text/plain")),
        ],
    )
    assert r.status_code == 200, r.text
    assert r.json()["staged"] == 1
    assert r.json()["sidecars"] == 1

    r = client.post(f"{API}/pictures/import/staging/{staging_id}/commit")
    assert r.status_code == 200, r.text
    status = _wait_for_stage(client, staging_id, target=("completed",))
    assert status["imported_count"] == 1, status
    # The image imported fine; the orphan sidecar contributed no tags.
    pic_id = _only_picture_id(srv)
    assert {"alpha", "beta"} & _tags_for(srv, pic_id) == set()


# ---------------------------------------------------------------------------
# Round 3 - project_id fail-fast (H2) and the staging-session reaper (H1)
# ---------------------------------------------------------------------------


def test_open_with_nonexistent_project_errors(owner_server):
    _srv, client = owner_server
    r = client.post(f"{API}/pictures/import/staging", json={"project_id": 999999})
    assert r.status_code == 404, r.text


def test_direct_import_with_nonexistent_project_errors(owner_server):
    """The direct multipart endpoint fails fast on a stale project id too.

    It used to skip the check the staging path runs, so the id only failed at
    the membership INSERT - after every file had been imported and committed.
    The caller (the ComfyUI saver node, which stores the project as
    "<name> #<id>" and so replays whatever id was current when the workflow was
    authored) was told the import failed while the pictures were already in the
    vault.
    """
    srv, client = owner_server
    r = client.post(
        f"{API}/pictures/import",
        files=[("file", ("a.png", _png_bytes(), "image/png"))],
        data={"project_id": "999999"},
    )
    assert r.status_code == 404, r.text
    assert "999999" in r.text
    # And nothing was imported on the way to the refusal.
    assert srv.vault.db.run_task(lambda s: s.exec(select(Picture.id)).first()) is None


def test_reaper_evicts_stale_session_and_dir(owner_server):
    from pixlstash.routes.pictures import _import as import_mod

    srv, client = owner_server
    staging_id = client.post(f"{API}/pictures/import/staging", json={}).json()[
        "staging_id"
    ]
    staging_dir = os.path.join(srv.vault.image_root, ".staging", staging_id)
    # Drop a marker so we can prove the dir is really removed.
    with open(os.path.join(staging_dir, "marker.png"), "wb") as fh:
        fh.write(_png_bytes())
    assert os.path.isdir(staging_dir)

    # Backdate the session well past the idle TTL, then run the reaper.
    session = srv.staging_sessions[staging_id]
    session["last_update_epoch_ms"] = int(time.time() * 1000) - (
        (import_mod._STAGING_SESSION_TTL_S + 60) * 1000
    )
    import_mod._reap_staging_sessions(srv)

    assert staging_id not in srv.staging_sessions, "Stale session must be evicted"
    assert not os.path.isdir(staging_dir), "Reaper must remove the staging dir"


def test_reaper_keeps_fresh_session(owner_server):
    from pixlstash.routes.pictures import _import as import_mod

    srv, client = owner_server
    staging_id = client.post(f"{API}/pictures/import/staging", json={}).json()[
        "staging_id"
    ]
    # A just-opened session is within TTL and must survive the reaper.
    import_mod._reap_staging_sessions(srv)
    assert staging_id in srv.staging_sessions, "Fresh session must not be evicted"
