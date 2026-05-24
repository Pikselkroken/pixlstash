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
from sqlmodel import Session, delete
from sqlalchemy import text

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

    def _wipe(session: Session):
        # Disable FK enforcement so wipe order doesn't matter; the test
        # leaves the DB empty so referential integrity is preserved overall.
        session.exec(text("PRAGMA foreign_keys = OFF"))
        for model in _RESET_TABLES:
            session.exec(delete(model))
        session.commit()
        session.exec(text("PRAGMA foreign_keys = ON"))

    server.vault.db.run_task(_wipe)

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
    with server.auth._token_cache_lock:
        server.auth._token_cache.clear()
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

    download_resp = client.get(download_url)
    assert download_resp.status_code == 200, f"Error: {download_resp.text}"
    assert download_resp.content[:2] == b"PK"  # ZIP file signature
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
