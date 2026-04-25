import json
import os
import shutil
import tempfile
import time

from fastapi.testclient import TestClient

from pixlstash.server import Server
from pixlstash.db_models.picture import Picture

API_PREFIX = "/api/v1"


def test_watch_folder():
    """Test watching a folder for changes."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = f"{temp_dir}/server-config.json"
        pictures_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "pictures")
        )
        assert os.path.isdir(pictures_dir), "Pictures directory not found"
        image_files = [
            f
            for dirpath, _, filenames in os.walk(pictures_dir)
            for f in filenames
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]
        assert image_files, "No images found in pictures directory"

        with Server(server_config_path) as server:
            with TestClient(server.api) as client:
                # First login to set the password
                response = client.post(
                    f"{API_PREFIX}/login",
                    json={"username": "testuser", "password": "testpassword"},
                )
                assert response.status_code == 200
                assert (
                    response.json()["message"]
                    == "Username and password set successfully."
                )

                with open(server_config_path, "r") as f:
                    server_config = json.load(f)
                existing_watch_folders = list(server_config.get("watch_folders") or [])
                if not any(
                    entry.get("folder") == pictures_dir
                    for entry in existing_watch_folders
                ):
                    existing_watch_folders.append(
                        {"folder": pictures_dir, "last_checked": 0}
                    )
                server_config["watch_folders"] = existing_watch_folders
                with open(server_config_path, "w") as f:
                    json.dump(server_config, f, indent=2)

                start = time.monotonic()
                pictures = []
                expected_count = len(image_files)
                while time.monotonic() - start < 20:
                    pictures = server.vault.db.run_task(
                        lambda session: Picture.find(session)
                    )
                    if len(pictures) >= expected_count:
                        break
                    time.sleep(0.25)

                assert len(pictures) == expected_count, (
                    f"Expected {expected_count} pictures, got {len(pictures)}"
                )
                assert all(pic.import_source_folder == pictures_dir for pic in pictures)

                filtered = client.get(
                    f"{API_PREFIX}/pictures",
                    params={
                        "fields": "grid",
                        "import_source_folder": pictures_dir,
                    },
                )
                assert filtered.status_code == 200
                assert len(filtered.json()) == expected_count

                empty_filtered = client.get(
                    f"{API_PREFIX}/pictures",
                    params={
                        "fields": "grid",
                        "import_source_folder": os.path.join(pictures_dir, "_missing"),
                    },
                )
                assert empty_filtered.status_code == 200
                assert empty_filtered.json() == []


def test_watch_folder_delete_after_import():
    """Test watch folder delete_after_import removes source files after import."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = f"{temp_dir}/server-config.json"
        source_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "pictures")
        )
        assert os.path.isdir(source_dir), "Pictures directory not found"
        image_files = [
            os.path.join(dirpath, f)
            for dirpath, _, filenames in os.walk(source_dir)
            for f in filenames
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]
        assert image_files, "No images found in pictures directory"

        watch_dir = os.path.join(temp_dir, "watch")
        os.makedirs(watch_dir, exist_ok=True)
        for src_path in image_files:
            rel = os.path.relpath(src_path, source_dir)
            dst = os.path.join(watch_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src_path, dst)

        with Server(server_config_path) as server:
            with TestClient(server.api) as client:
                response = client.post(
                    f"{API_PREFIX}/login",
                    json={"username": "testuser", "password": "testpassword"},
                )
                assert response.status_code == 200

                with open(server_config_path, "r") as f:
                    server_config = json.load(f)
                existing_watch_folders = list(server_config.get("watch_folders") or [])
                existing_watch_folders.append(
                    {
                        "folder": watch_dir,
                        "last_checked": 0,
                        "delete_after_import": True,
                    }
                )
                server_config["watch_folders"] = existing_watch_folders
                with open(server_config_path, "w") as f:
                    json.dump(server_config, f, indent=2)

                expected_count = len(image_files)
                start = time.monotonic()
                pictures = []
                while time.monotonic() - start < 20:
                    pictures = server.vault.db.run_task(
                        lambda session: Picture.find(session)
                    )
                    remaining_files = [
                        os.path.join(dp, f)
                        for dp, _, fnames in os.walk(watch_dir)
                        for f in fnames
                        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                    ]
                    if len(pictures) >= expected_count and not remaining_files:
                        break
                    time.sleep(0.25)

                assert len(pictures) == expected_count, (
                    f"Expected {expected_count} pictures, got {len(pictures)}"
                )
                assert all(pic.import_source_folder == watch_dir for pic in pictures)
                remaining_files = [
                    os.path.join(dp, f)
                    for dp, _, fnames in os.walk(watch_dir)
                    for f in fnames
                    if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                ]
                assert not remaining_files, "Watch folder did not delete source files"
