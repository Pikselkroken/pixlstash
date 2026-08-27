"""Server REST endpoint tests that do **not** exercise async workers.

These tests previously each created their own ``Server`` instance inside a
``tempfile.TemporaryDirectory``, paying ~2-3 s of startup overhead per test.
The tests in this module are pure REST checks (no image upload through the
worker pipeline, no `wait_for_faces`/`get_worker_future`/etc.), so it is safe
to share a single ``Server`` for the whole module and just wipe the domain
tables / image-root contents between tests.

Worker-heavy tests still live in :mod:`tests.test_server`.
"""

import logging
import os
import shutil
import stat
import tempfile
import time

try:
    import tomllib
except ImportError:  # Python < 3.11
    import tomli as tomllib
import zipfile
from io import BytesIO

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from pixlstash.db_models import (
    Character,
    DeletedFileLog,
    Face,
    GuestScore,
    GuestSession,
    ImportFolder,
    MetaData,
    Picture,
    PictureLikeness,
    PictureLikenessQueue,
    PictureProjectMember,
    PictureSet,
    PictureSetMember,
    PictureStack,
    Project,
    ProjectAttachment,
    Quality,
    ReferenceFolder,
    Tag,
    TagPrediction,
    User,
    UserToken,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.server import Server
from pixlstash.utils.image_processing.image_utils import ImageUtils
from tests.utils import wipe_tables

logger = get_logger(__name__)


def get_project_version():
    pyproject_path = os.path.join(os.path.dirname(__file__), "../pyproject.toml")
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


# Tables wiped between tests. Order: child rows before parent rows so any FK
# constraints are satisfied (SQLite is permissive, but explicit is safer).
_RESET_TABLES = [
    PictureLikenessQueue,
    PictureLikeness,
    PictureProjectMember,
    PictureSetMember,
    TagPrediction,
    Face,
    Quality,
    MetaData,
    DeletedFileLog,
    ProjectAttachment,
    PictureStack,
    Picture,
    PictureSet,
    Project,
    Character,
    ReferenceFolder,
    ImportFolder,
    Tag,
    GuestScore,
    GuestSession,
    UserToken,
    User,
]


@pytest.fixture(scope="module")
def server():
    """Shared Server instance for all tests in this module.

    Server construction (DB migrations, vault start-up, route registration,
    ...) takes a couple of seconds, so we do it once for the module rather
    than per test. The ``reset_vault`` fixture restores a clean state
    between tests.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(server_config_path) as srv:
            yield srv


@pytest.fixture(autouse=True)
def reset_vault(server):
    """Restore a pristine vault state before each test.

    - Truncate every domain table (and auth tables) so the DB looks freshly
      migrated with no rows.
    - Delete every file in the vault image_root *except* the SQLite database
      (and its WAL/journal companions) so disk state matches the DB.
    - Recreate the User row via ``auth.ensure_user()`` so login flows work
      the same way they would on a freshly-created Server.
    - Reset auth in-memory caches.
    """

    # FK enforcement is off for the wipe so table order doesn't matter; the
    # DB is left empty, so referential integrity is preserved overall.
    server.vault.db.run_task(wipe_tables, _RESET_TABLES)

    image_root = server.vault.image_root
    db_basenames = {"vault.db", "vault.db-wal", "vault.db-shm", "vault.db-journal"}
    for entry in os.listdir(image_root):
        if entry in db_basenames:
            continue
        path = os.path.join(image_root, entry)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError:
                logger.debug(f"Failed to delete file during vault reset: {path}")

    server.auth.password_hash = None
    server.auth.username = None
    server.auth.user = None
    server.auth.active_session_ids = {}
    # Go through the flush helper so the revocation epoch is bumped too - a
    # bare _token_cache.clear() skips it (see AuthService._flush_token_cache).
    server.auth._flush_token_cache()
    server.auth.ensure_user()

    yield


def test_esmeralda_vault_character_and_logo(server):
    """Esmeralda Vault exists and the Logo is not associated with any character."""
    server.vault.import_default_data()
    client = TestClient(server.api)

    response = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert response.status_code == 200
    response = client.get("/protected")
    assert response.status_code == 200
    assert response.json()["message"] == "You are authenticated!"

    pics = server.vault.db.run_task(lambda s: s.query(Picture).all())
    assert len(pics) > 0, "No pictures found in vault"

    logging.info(
        f"Found {len(pics)} pictures in vault, starting facial features processing"
    )

    resp = client.get("/characters")
    assert resp.status_code == 200
    chars = resp.json()
    assert len(chars) > 0, "No characters found in vault"
    esmeralda = None
    for c in chars:
        if c.get("name") == "Esmeralda Vault":
            esmeralda = c
            break
    assert esmeralda is not None, "Esmeralda Vault character not found"
    char_id = esmeralda["id"]
    logging.info(f"Found Esmeralda Vault character with ID: {char_id}")

    resp2 = client.get("/pictures")
    assert resp2.status_code == 200
    pics = resp2.json()
    assert len(pics) > 0, "No pictures found in vault"
    pic_id = None
    for pic in pics:
        char_resp = client.get(f"/pictures/{pic['id']}/metadata")
        if char_resp.status_code == 200:
            pic_info = char_resp.json()
            char_ids = [str(cid) for cid in pic_info.get("character_ids", [])]
            if str(char_id) in char_ids:
                pic_id = pic["id"]
                break

    # The logo has no face, so no character association.
    assert pic_id is None, (
        f"Logo picture should not be associated with any character (char_id={char_id})"
    )

    img_resp = client.get(f"/pictures/{pics[0]['id']}.png")
    assert img_resp.status_code == 200
    logo_path = os.path.join(os.path.dirname(__file__), "../Logo.png")
    with open(logo_path, "rb") as f:
        logo_bytes = f.read()
    assert img_resp.content == logo_bytes, (
        "Esmeralda Vault's picture does not match Logo.png"
    )


def test_create_and_get_default_character(server):
    """Test creating and fetching the default character 'Esmeralda'."""
    client = TestClient(server.api)

    response = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert response.status_code == 200

    char_name = "Esmeralda"
    char_desc = "Default vault character"
    resp = client.post(
        "/characters",
        json={"name": char_name, "description": char_desc},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    char_id = data["character"]["id"]
    assert data["character"]["name"] == char_name
    assert data["character"]["description"] == char_desc

    resp2 = client.get(f"/characters/{char_id}")
    assert resp2.status_code == 200
    char = resp2.json()
    assert char["id"] == char_id
    assert char["name"] == char_name
    assert char["description"] == char_desc


def test_favicon(server):
    """Test /favicon.ico endpoint returns 200 and ICO content."""
    client = TestClient(server.api)
    resp = client.get("/favicon.ico")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/vnd.microsoft.icon"
    assert resp.content[:4] == b"\x00\x00\x01\x00"  # ICO file signature


def test_pictures_likeness_groups(server):
    """Test /pictures/likeness-groups endpoint returns 200 and valid structure."""
    client = TestClient(server.api)

    response = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert response.status_code == 200
    resp = client.get("/pictures/likeness-groups")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_pictures_thumbnails(server):
    """Test /pictures/thumbnails endpoint returns 200 and valid structure."""
    client = TestClient(server.api)

    response = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert response.status_code == 200
    resp = client.post("/pictures/thumbnails", json={"ids": []})
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


def test_scrapheap_purge_logs_deleted_files(server):
    """Purging the scrapheap records each removed file in deleted_file_log.

    The log is how we know which files can no longer be restored (e.g. when
    rolling a vault back to an older snapshot), so a permanent purge must
    write one row per deleted file, with its content hash.
    """
    server.vault.import_default_data()
    client = TestClient(server.api)

    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200

    def _first_picture(session: Session):
        pic = session.exec(select(Picture)).first()
        assert pic is not None, "No pictures imported"
        return (pic.id, pic.file_path, pic.pixel_sha)

    pic_id, file_path, pixel_sha = server.vault.db.run_task(_first_picture)
    assert file_path, "Imported picture has no file_path"
    abs_path = ImageUtils.resolve_picture_path(server.vault.image_root, file_path)
    assert os.path.isfile(abs_path)

    # Soft-delete into the scrapheap, then permanently purge it.
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200
    purge_resp = _purge_scrapheap(client)
    assert purge_resp.status_code == 200

    # The row and the file on disk are gone...
    assert server.vault.db.run_task(lambda s: s.get(Picture, pic_id)) is None
    assert not os.path.isfile(abs_path)

    # ...and a deleted_file_log row records the deletion as opaque hashes -
    # the raw path is never stored (privacy), only its SHA-256.
    expected_path_sha = DeletedFileLog.hash_path(file_path)

    def _fetch_logs(session: Session):
        rows = session.exec(select(DeletedFileLog)).all()
        return [(r.path_sha, r.pixel_sha, r.file_removed) for r in rows]

    logs = server.vault.db.run_task(_fetch_logs)
    assert len(logs) == 1, f"Expected exactly one log row, got {logs}"
    assert logs[0][:2] == (expected_path_sha, pixel_sha)
    # A managed (vault) picture delete-forever genuinely removes the file, so the
    # ledger records a permanent deletion restore must never resurrect.
    assert logs[0][2] is True, (
        f"Managed delete-forever must log file_removed=True, got {logs[0][2]!r}"
    )
    # The cleartext path must not appear in any column of the log row.
    assert file_path not in (logs[0][0] or ""), "Raw path leaked into path_sha."


def test_scrapheap_purge_reports_snapshots_with_deleted(server):
    """Purging the scrapheap reports which snapshots still hold the metadata
    for the deleted pictures, so the user can choose to delete those snapshots."""
    server.vault.import_default_data()
    client = TestClient(server.api)
    login_resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert login_resp.status_code == 200

    pic_id = server.vault.db.run_task(lambda s: s.exec(select(Picture)).first().id)

    # Snapshot now contains this picture, then soft-delete + purge it.
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200
    resp = _purge_scrapheap(client)
    assert resp.status_code == 200
    body = resp.json()

    snaps = body.get("snapshots_with_deleted") or []
    assert any(s["id"] == cp.id and s["matched_count"] >= 1 for s in snaps), (
        f"Expected snapshot {cp.id} in {snaps}"
    )


def _purge_scrapheap(client, *, ids=None, include_protected=None):
    """Drive the real preview -> confirm delete-forever flow.

    ``DELETE /pictures/scrapheap`` refuses without the single-use
    ``confirm_token`` the preview mints for that exact selection, so a test may
    not call the destructive endpoint bare.
    """
    preview = client.post("/pictures/scrapheap/delete-preview", json={"ids": ids})
    assert preview.status_code == 200, preview.text
    body = {"confirm_token": preview.json()["confirm_token"]}
    if ids is not None:
        body["picture_ids"] = ids
    if include_protected is not None:
        body["include_protected"] = include_protected
    return client.request("DELETE", "/api/v1/pictures/scrapheap", json=body)


def _make_reference_folder_picture(server, folder_dir, file_name, *, allow_delete):
    """Create a reference folder, a real image file in it, and an indexed Picture.

    Returns (folder_id, picture_id, abs_file_path).
    """
    os.makedirs(folder_dir, exist_ok=True)
    abs_file_path = os.path.join(folder_dir, file_name)
    Image.new("RGB", (8, 8), color=(10, 20, 30)).save(abs_file_path, format="PNG")
    pixel_sha = ImageUtils.calculate_hash_from_file_path(abs_file_path)

    def _insert(session: Session):
        folder = ReferenceFolder(
            folder=folder_dir,
            label="refs",
            allow_delete_file=allow_delete,
            status="active",
            # pending_reimport defaults to False, so this folder never triggers
            # the explicit-re-import ledger override - a routine scan of it
            # simply skips ledger paths, which is what these tests assert.
        )
        session.add(folder)
        session.commit()
        session.refresh(folder)
        pic = Picture(
            file_path=abs_file_path,
            reference_folder_id=folder.id,
            pixel_sha=pixel_sha,
            format="PNG",
            width=8,
            height=8,
            original_file_name=file_name,
        )
        session.add(pic)
        session.commit()
        session.refresh(pic)
        return folder.id, pic.id

    folder_id, pic_id = server.vault.db.run_task(_insert)
    return folder_id, pic_id, abs_file_path


def _run_reference_folder_scan(server, folder_id, folder_dir):
    from pixlstash.tasks.reference_folder_scan_task import ReferenceFolderScanTask

    task = ReferenceFolderScanTask(
        database=server.vault.db,
        folder_id=folder_id,
        folder_path=folder_dir,
        resolved_path=folder_dir,
    )
    return task._run_task()


def test_scrapheap_delete_forever_destroys_protected_reference_original(
    server, tmp_path
):
    """Explicit "Delete forever" genuinely destroys a reference-folder original
    even when allow_delete_file=False.

    Rev-4 maintainer decision + Round-3 escape hatch: the "delete all" confirm
    sends include_protected=true, which removes the on-disk source file for EVERY
    selected picture - deliberately overriding the routine reference-folder file
    protection for a protected original - deletes the Picture row, and logs
    file_removed=True so a subsequent restore drops the row and never resurrects
    it. (The routine protection still applies to soft-delete-to-scrapheap and the
    reference-folder scan; and include_protected=false skips protected originals
    entirely - see the routine-protection and escape-hatch tests below.)
    """
    client = TestClient(server.api)
    login_resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert login_resp.status_code == 200

    folder_dir = str(tmp_path / "protected_refs")
    folder_id, pic_id, abs_file_path = _make_reference_folder_picture(
        server, folder_dir, "destroy_me.png", allow_delete=False
    )
    expected_path_sha = DeletedFileLog.hash_path(abs_file_path)

    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200
    # include_protected=true is the type-to-confirm "delete all" action.
    purge_resp = _purge_scrapheap(client, include_protected=True)
    assert purge_resp.status_code == 200
    assert purge_resp.json()["deleted_count"] == 1

    # Row gone AND the protected source file is genuinely destroyed on disk.
    assert server.vault.db.run_task(lambda s: s.get(Picture, pic_id)) is None
    assert not os.path.isfile(abs_file_path), (
        "Explicit delete-forever must destroy even a protected reference original"
    )

    def _fetch_logs(session: Session):
        return [
            (r.path_sha, r.pixel_sha, r.file_removed)
            for r in session.exec(select(DeletedFileLog)).all()
        ]

    logs = server.vault.db.run_task(_fetch_logs)
    ledger_entries = [
        removed for path_sha, _, removed in logs if path_sha == expected_path_sha
    ]
    assert ledger_entries, f"Expected ledger entry for destroyed file, got {logs}"
    # The file is genuinely gone, so restore must never resurrect it:
    # file_removed=True.
    assert ledger_entries[0] is True, (
        "Explicit delete-forever of a protected original must log "
        f"file_removed=True, got {ledger_entries[0]!r}"
    )

    # A scan of the folder finds nothing to re-import - the file no longer exists.
    result = _run_reference_folder_scan(server, folder_id, folder_dir)
    assert result["new_count"] == 0, (
        f"Destroyed file must not be re-imported by scan: {result}"
    )
    reimported = server.vault.db.run_task(
        lambda s: s.exec(
            select(Picture).where(Picture.reference_folder_id == folder_id)
        ).all()
    )
    assert reimported == [], f"Scan re-created picture rows: {reimported}"


def test_soft_delete_to_scrapheap_keeps_reference_original_on_disk(server, tmp_path):
    """Routine scrapheap handling still protects reference originals.

    Moving a protected (allow_delete_file=False) reference-folder picture to the
    scrapheap is a soft delete: the Picture row stays (deleted=True), the on-disk
    source file is never touched, and no permanent-deletion ledger row is written.
    Only the explicit "Delete forever" destroys the file (test above).
    """
    client = TestClient(server.api)
    login_resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert login_resp.status_code == 200

    folder_dir = str(tmp_path / "routine_protected_refs")
    _folder_id, pic_id, abs_file_path = _make_reference_folder_picture(
        server, folder_dir, "routine_keep.png", allow_delete=False
    )

    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200

    # Soft-deleted: still in DB, marked deleted, and the file is untouched.
    pic = server.vault.db.run_task(lambda s: s.get(Picture, pic_id))
    assert pic is not None, "Soft delete must keep the row (scrapheap)"
    assert pic.deleted is True
    assert os.path.isfile(abs_file_path), (
        "Routine soft-delete must never remove the reference original"
    )
    # Nothing was permanently deleted, so no ledger row exists yet.
    assert _ledger_flags_for(server, abs_file_path) == [], (
        "Routine soft-delete must not write a permanent-deletion ledger row"
    )


def _seed_scrapheap_mixed(server, client, tmp_path):
    """Create 2 protected + 1 unprotected reference pictures and soft-delete all.

    Each picture gets its OWN directory. ``_make_reference_folder_picture``
    creates a fresh ``ReferenceFolder`` row per call, so pointing two calls at
    one directory registered that directory TWICE - a state the API rejects with
    409, and one that made the background reference-folder scanner treat each
    folder's file as an unindexed file of the other and re-import it. The
    re-imported row then recycled the rowid SQLite had just freed by purging,
    so the post-purge assertions intermittently read a different picture. That
    is the root cause of this module's historical scrapheap flake; keeping the
    directories disjoint fixes it at the source rather than by suppressing the
    scanner.

    Returns (protected_ids, protected_paths, unprotected_id, unprotected_path).
    """
    _f1, p1, path1 = _make_reference_folder_picture(
        server, str(tmp_path / "prot_a"), "keep_a.png", allow_delete=False
    )
    _f2, p2, path2 = _make_reference_folder_picture(
        server, str(tmp_path / "prot_b"), "keep_b.png", allow_delete=False
    )
    unprot_dir = str(tmp_path / "unprot")
    _f3, p3, path3 = _make_reference_folder_picture(
        server, unprot_dir, "gone.png", allow_delete=True
    )
    for pid in (p1, p2, p3):
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200
    return [p1, p2], {path1, path2}, p3, path3


def test_scrapheap_delete_preview_reports_full_protected_set(server, tmp_path):
    """The authoritative preview names EVERY protected reference original in the
    full scrapheap set (queried from the DB, never a virtualized/grid window)."""
    client = TestClient(server.api)
    login_resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert login_resp.status_code == 200
    prot_ids, prot_paths, unprot_id, _unprot_path = _seed_scrapheap_mixed(
        server, client, tmp_path
    )

    # ids omitted => the entire scrapheap.
    resp = client.post("/pictures/scrapheap/delete-preview", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_count"] == 3
    assert body["protected_count"] == 2
    assert body["unprotected_count"] == 1
    assert {item["id"] for item in body["protected"]} == set(prot_ids)
    # Absolute on-disk paths of the protected originals at risk.
    assert {item["file_path"] for item in body["protected"]} == prot_paths


def test_scrapheap_delete_include_protected_false_skips_protected(server, tmp_path):
    """include_protected=false purges only unprotected pictures; protected
    originals are left completely intact (row kept + deleted, file kept, no
    ledger row)."""
    client = TestClient(server.api)
    login_resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert login_resp.status_code == 200
    prot_ids, prot_paths, unprot_id, unprot_path = _seed_scrapheap_mixed(
        server, client, tmp_path
    )

    resp = _purge_scrapheap(client, include_protected=False)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted_count"] == 1, body
    assert body["skipped_count"] == 2, body
    assert body["include_protected"] is False

    # Unprotected: purged (row gone, file removed, ledger file_removed=True).
    assert server.vault.db.run_task(lambda s: s.get(Picture, unprot_id)) is None
    assert not os.path.isfile(unprot_path)
    assert _ledger_flags_for(server, unprot_path) == [True]

    # Protected: fully intact - row kept & still deleted, file on disk, no ledger.
    for pid, path in zip(prot_ids, sorted(prot_paths)):
        pic = server.vault.db.run_task(lambda s, i=pid: s.get(Picture, i))
        assert pic is not None and pic.deleted is True, (
            "Protected picture must stay soft-deleted in the scrapheap"
        )
    for path in prot_paths:
        assert os.path.isfile(path), "Protected original file must be kept on disk"
        assert _ledger_flags_for(server, path) == [], (
            "A skipped protected picture must write no permanent-deletion ledger row"
        )


def test_scrapheap_delete_include_protected_true_destroys_all(server, tmp_path):
    """include_protected=true purges everything, destroying protected originals
    too (files removed, file_removed=True)."""
    client = TestClient(server.api)
    login_resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert login_resp.status_code == 200
    prot_ids, prot_paths, unprot_id, unprot_path = _seed_scrapheap_mixed(
        server, client, tmp_path
    )

    resp = _purge_scrapheap(client, include_protected=True)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted_count"] == 3, body
    assert body["skipped_count"] == 0, body
    assert body["include_protected"] is True

    # Everything purged: all rows gone, all files removed, all ledger True.
    for pid in [*prot_ids, unprot_id]:
        assert server.vault.db.run_task(lambda s, i=pid: s.get(Picture, i)) is None
    for path in [*prot_paths, unprot_path]:
        assert not os.path.isfile(path), "Delete-all must destroy the file"
        assert _ledger_flags_for(server, path) == [True]


def test_scrapheap_purge_unprotected_folder_removes_file_and_logs(server, tmp_path):
    """allow_delete_file=True removes the on-disk file as well as the row.

    Emptying the scrapheap must delete the Picture row, write a DeletedFileLog
    entry, and remove the source file from disk.
    """
    client = TestClient(server.api)
    login_resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert login_resp.status_code == 200

    folder_dir = str(tmp_path / "unprotected_refs")
    _folder_id, pic_id, abs_file_path = _make_reference_folder_picture(
        server, folder_dir, "remove_me.png", allow_delete=True
    )
    expected_path_sha = DeletedFileLog.hash_path(abs_file_path)

    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200
    purge_resp = _purge_scrapheap(client)
    assert purge_resp.status_code == 200
    assert purge_resp.json()["deleted_count"] == 1

    # Row gone, ledger written, file removed from disk.
    assert server.vault.db.run_task(lambda s: s.get(Picture, pic_id)) is None
    assert not os.path.isfile(abs_file_path), "Unprotected source file must be removed"

    logs = server.vault.db.run_task(
        lambda s: [
            (r.path_sha, r.file_removed) for r in s.exec(select(DeletedFileLog)).all()
        ]
    )
    removed_flags = [
        removed for path_sha, removed in logs if path_sha == expected_path_sha
    ]
    assert removed_flags, f"Expected ledger entry, got {logs}"
    # The file was actually removed from disk, so this is a genuine permanent
    # deletion: file_removed=True (restore must never resurrect it).
    assert removed_flags[0] is True, (
        f"Unprotected purge must log file_removed=True, got {removed_flags[0]!r}"
    )


# ---------------------------------------------------------------------------
# deleted_file_log Change 1: a genuine hard delete upgrades a stale
# file_removed=False row to True instead of skipping. (Change 2, the explicit
# reference-folder re-import override, is tested in test_restore.py where the
# reference-folder scan runs deterministically with background workers off.)
# ---------------------------------------------------------------------------


def _seed_deleted_log(server, file_path, *, file_removed, pixel_sha=None):
    """Insert one deleted_file_log row for *file_path* (path stored hashed)."""
    from datetime import datetime, timezone

    def _do(session: Session):
        session.add(
            DeletedFileLog(
                path_sha=DeletedFileLog.hash_path(file_path),
                pixel_sha=pixel_sha,
                deleted_at=datetime.now(timezone.utc),
                file_removed=file_removed,
            )
        )
        session.commit()

    server.vault.db.run_task(_do)


def _ledger_flags_for(server, file_path):
    """Return the file_removed flags of every ledger row for *file_path*."""
    path_sha = DeletedFileLog.hash_path(file_path)
    return server.vault.db.run_task(
        lambda s: [
            r.file_removed
            for r in s.exec(
                select(DeletedFileLog).where(DeletedFileLog.path_sha == path_sha)
            ).all()
        ]
    )


def test_missing_file_purge_upgrades_kept_flag_to_removed(server, tmp_path):
    """Change 1: a path first logged file_removed=False (removed-but-kept) that
    is later genuinely purged must have its existing row UPGRADED to
    file_removed=True - one row, updated not duplicated - so the ledger is
    truthful rather than relying only on restore's missing-file net."""
    from pixlstash.tasks.missing_file_purge_task import MissingFilePurgeTask

    folder_dir = str(tmp_path / "refs_upgrade")
    os.makedirs(folder_dir, exist_ok=True)
    abs_path = os.path.join(folder_dir, "vanished.png")

    # First recorded as protected/kept (file_removed=False).
    _seed_deleted_log(server, abs_path, file_removed=False, pixel_sha="sha_keep")

    # A picture still points at that path, but the file is now genuinely gone.
    def _insert(session: Session):
        pic = Picture(
            file_path=abs_path,
            pixel_sha="sha_keep",
            original_file_name="vanished.png",
        )
        session.add(pic)
        session.commit()
        session.refresh(pic)
        return pic.id

    pic_id = server.vault.db.run_task(_insert)
    assert not os.path.isfile(abs_path), "File must be absent for the purge to fire."

    pics = server.vault.db.run_task(
        lambda s: s.exec(select(Picture).where(Picture.id == pic_id)).all()
    )
    result = MissingFilePurgeTask(server.vault.db, pics)._run_task()
    assert result["purged"] == 1, result

    flags = _ledger_flags_for(server, abs_path)
    assert flags == [True], (
        f"Expected exactly one ledger row upgraded to file_removed=True, got {flags}"
    )
    assert server.vault.db.run_task(lambda s: s.get(Picture, pic_id)) is None


def test_scrapheap_delete_forever_upgrades_stale_kept_flag(server, tmp_path):
    """The scrapheap delete-forever writer upgrades a stale kept row.

    A legacy file_removed=False ledger row (a removed-but-kept picture recorded
    before the rev-4 delete-forever behaviour change, or a missing-file-kept
    state) that is later hit by an explicit delete-forever of the SAME path must
    have its existing row UPGRADED to file_removed=True instead of a duplicate
    being inserted - still exactly one row. (Under rev-4 the scrapheap writer
    itself only ever writes True, so the False row is seeded to represent a
    pre-existing kept entry.)
    """
    client = TestClient(server.api)
    login_resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert login_resp.status_code == 200

    abs_file_path = str(tmp_path / "kept_then_forever.png")
    Image.new("RGB", (8, 8), color=(10, 20, 30)).save(abs_file_path, format="PNG")

    # Seed a stale kept (file_removed=False) ledger row for this path.
    _seed_deleted_log(server, abs_file_path, file_removed=False, pixel_sha="sha_keep")
    assert _ledger_flags_for(server, abs_file_path) == [False]

    # A picture points at the SAME path and is soft-deleted then delete-forever.
    def _insert_plain(session: Session):
        pic = Picture(
            file_path=abs_file_path,
            pixel_sha="sha_gone",
            original_file_name="kept_then_forever.png",
            deleted=True,
        )
        session.add(pic)
        session.commit()
        session.refresh(pic)
        return pic.id

    server.vault.db.run_task(_insert_plain)
    assert _purge_scrapheap(client).json()["deleted_count"] == 1

    flags = _ledger_flags_for(server, abs_file_path)
    assert flags == [True], (
        f"Delete-forever must upgrade the stale kept row to True (one row): {flags}"
    )
    # And the on-disk file is genuinely destroyed by the explicit delete-forever.
    assert not os.path.isfile(abs_file_path)


def test_scrapheap_count_matches_grid(server):
    """The scrapheap badge count must equal the scrapheap grid length.

    Regression for the '72 deleted, empty grid' bug: count_scrapheap and
    Picture.find(only_deleted=True) must apply the same filters and agree.
    """
    server.vault.import_default_data()
    client = TestClient(server.api)
    login_resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert login_resp.status_code == 200

    pic_ids = server.vault.db.run_task(
        lambda s: [p.id for p in s.exec(select(Picture)).all()]
    )
    assert pic_ids, "No pictures imported"
    for pic_id in pic_ids:
        delete_resp = client.delete(f"/pictures/{pic_id}")
        assert delete_resp.status_code == 200

    count = client.get("/characters/SCRAPHEAP/summary").json()
    badge_count = count.get("image_count")

    def _grid(session: Session):
        return Picture.find(session, only_deleted=True, select_fields=["id"])

    grid = server.vault.db.run_task(_grid)
    assert badge_count == len(grid), (
        f"Scrapheap count {badge_count} != grid length {len(grid)}"
    )
    assert len(grid) == len(pic_ids)


def test_pictures_export(server):
    """Test /pictures/export endpoint returns 200 and zip content."""
    server.vault.import_default_data(add_tagger_test_images=True)
    client = TestClient(server.api)

    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200

    resp = client.get("/pictures/export")
    assert resp.status_code == 200, f"Error: {resp.text}"
    assert resp.headers["content-type"] == "application/json"

    task_id = resp.json().get("task_id")
    assert task_id, "Missing task_id in export response"

    status_payload = None
    timeout_s = 10
    start = time.time()
    while time.time() - start < timeout_s:
        status_resp = client.get("/pictures/export/status", params={"task_id": task_id})
        assert status_resp.status_code == 200, f"Error: {status_resp.text}"
        status_payload = status_resp.json()
        if status_payload.get("status") == "completed":
            break
        if status_payload.get("status") == "failed":
            raise AssertionError("Export task failed")
        time.sleep(0.1)

    assert status_payload, "Missing export status payload"
    assert status_payload.get("status") == "completed", (
        f"Export task did not complete in {timeout_s}s"
    )

    download_url = status_payload.get("download_url")
    assert download_url, "Missing download_url in export status"

    export_task = server.export_tasks[task_id]
    export_path = export_task["file_path"]
    private_dir = export_task["private_dir"]
    assert stat.S_IMODE(os.stat(export_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(private_dir).st_mode) == 0o700

    download_resp = client.get(download_url)
    assert download_resp.status_code == 200, f"Error: {download_resp.text}"
    assert download_resp.content[:2] == b"PK"  # ZIP file signature
    assert task_id not in server.export_tasks
    assert not os.path.exists(private_dir)
    logger.info(
        "Exported pictures zip size: {} bytes".format(len(download_resp.content))
    )

    with zipfile.ZipFile(BytesIO(download_resp.content)) as zf:
        zip_names = set(zf.namelist())
        image_names = [name for name in zip_names if not name.lower().endswith(".txt")]
        pictures = server.vault.db.run_task(Picture.find)

        assert len(pictures) == len(image_names), (
            f"Expected {len(pictures)} pictures in export, "
            f"found {len(image_names)} in zip"
        )
        for fname in image_names:
            found = False
            with zf.open(fname) as f:
                data = f.read()
                sha = ImageUtils.calculate_hash_from_bytes(data)

            for pic in pictures:
                if sha == pic.pixel_sha:
                    found = True
                    assert len(data) == pic.size_bytes, (
                        f"Size mismatch for {fname}: {len(data)} != {pic.size_bytes}"
                    )
                    img = Image.open(BytesIO(data))
                    assert img.format.lower() == (pic.format or "").lower(), (
                        f"Format mismatch for {fname}: {img.format} != {pic.format}"
                    )
                    assert img.width == pic.width, (
                        f"Width mismatch for {fname}: {img.width} != {pic.width}"
                    )
                    assert img.height == pic.height, (
                        f"Height mismatch for {fname}: {img.height} != {pic.height}"
                    )
                    break
            assert found, (
                f"No database picture matches exported SHA for picture {fname}"
            )


def test_read_version(server):
    client = TestClient(server.api)
    response = client.get("/version")
    assert response.status_code == 200
    expected_version = get_project_version()
    data = response.json()
    assert data["message"] == "PixlStash REST API"
    assert data["version"] == expected_version
    assert "install_type" in data
    # The install_type contract is exactly Server.INSTALL_TYPES; the frontend
    # guards anything else to "other", but the backend must never emit a value
    # outside the set in the first place. Asserted against the constant rather
    # than a copy of it: a literal here passed on CI (which sets no install-type
    # env var) and failed only on a developer's machine, which is the one that
    # exports PIXLSTASH_INSTALL_TYPE=dev.
    assert data["install_type"] in set(Server.INSTALL_TYPES)


def test_read_version_install_type_docker(server, monkeypatch):
    """The env flag makes /version report the reliable ``docker`` signal."""
    monkeypatch.setenv("PIXLSTASH_IN_DOCKER", "1")
    monkeypatch.delenv("PIXLSTASH_INSTALL_TYPE", raising=False)
    client = TestClient(server.api)
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json()["install_type"] == "docker"


def test_read_version_install_type_pip_default(server, monkeypatch):
    """With no docker signals and no override, the default is ``pip``."""
    monkeypatch.delenv("PIXLSTASH_IN_DOCKER", raising=False)
    monkeypatch.delenv("PIXLSTASH_INSTALL_TYPE", raising=False)
    # Server.running_in_docker() also checks /.dockerenv; neutralise it so the
    # test is deterministic regardless of where it runs.
    monkeypatch.setattr(os.path, "exists", lambda path: False)
    client = TestClient(server.api)
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json()["install_type"] == "pip"


def test_read_version_install_type_override(server, monkeypatch):
    """A valid PIXLSTASH_INSTALL_TYPE override wins over docker detection."""
    # Even with the docker flag set, an explicit override takes precedence so an
    # installer (e.g. the Windows build) can declare "other".
    monkeypatch.setenv("PIXLSTASH_IN_DOCKER", "1")
    monkeypatch.setenv("PIXLSTASH_INSTALL_TYPE", "other")
    client = TestClient(server.api)
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json()["install_type"] == "other"


def test_read_version_install_type_electron(server, monkeypatch):
    """The Electron desktop app declares its channel via the override env var."""
    monkeypatch.delenv("PIXLSTASH_IN_DOCKER", raising=False)
    monkeypatch.setenv("PIXLSTASH_INSTALL_TYPE", "electron")
    client = TestClient(server.api)
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json()["install_type"] == "electron"


def test_read_version_install_type_invalid_override_ignored(server, monkeypatch):
    """An invalid override is ignored and detection falls back to docker."""
    monkeypatch.setenv("PIXLSTASH_INSTALL_TYPE", "garbage")
    monkeypatch.setenv("PIXLSTASH_IN_DOCKER", "1")
    client = TestClient(server.api)
    response = client.get("/version")
    assert response.status_code == 200
    # "garbage" is dropped; docker detection then supplies the reliable value.
    assert response.json()["install_type"] == "docker"
