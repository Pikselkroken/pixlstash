import json
import os
import shutil
import tempfile
import time

from fastapi.testclient import TestClient

from pixlvault.server import Server
from pixlvault.db_models.picture import Picture
from pixlvault.worker_registry import WorkerType


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
            for f in os.listdir(pictures_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]
        assert image_files, "No images found in pictures directory"

        with Server(server_config_path) as server:
            client = TestClient(server.api)

            # First login to set the password
            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200
            assert (
                response.json()["message"] == "Username and password set successfully."
            )

            with open(server_config_path, "r") as f:
                server_config = json.load(f)
            existing_watch_folders = list(server_config.get("watch_folders") or [])
            if not any(
                entry.get("folder") == pictures_dir for entry in existing_watch_folders
            ):
                existing_watch_folders.append(
                    {"folder": pictures_dir, "last_checked": 0}
                )
            server_config["watch_folders"] = existing_watch_folders
            with open(server_config_path, "w") as f:
                json.dump(server_config, f, indent=2)

            server.vault.start_workers(
                {
                    WorkerType.WATCH_FOLDERS,
                    WorkerType.TAGGER,
                    WorkerType.IMAGE_EMBEDDING,
                    WorkerType.SMART_SCORE_SCRAPHEAP,
                }
            )
            worker = server.vault._workers.get(WorkerType.WATCH_FOLDERS)
            if worker:
                worker.notify()

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


def test_watch_folder_delete_after_import():
    """Test watch folder delete_after_import removes source files after import."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = f"{temp_dir}/server-config.json"
        source_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "pictures")
        )
        assert os.path.isdir(source_dir), "Pictures directory not found"
        image_files = [
            f
            for f in os.listdir(source_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]
        assert image_files, "No images found in pictures directory"

        watch_dir = os.path.join(temp_dir, "watch")
        os.makedirs(watch_dir, exist_ok=True)
        for name in image_files:
            shutil.copy2(os.path.join(source_dir, name), watch_dir)

        with Server(server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
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

            server.vault.start_workers(
                {
                    WorkerType.WATCH_FOLDERS,
                    WorkerType.TAGGER,
                    WorkerType.IMAGE_EMBEDDING,
                    WorkerType.SMART_SCORE_SCRAPHEAP,
                }
            )
            worker = server.vault._workers.get(WorkerType.WATCH_FOLDERS)
            if worker:
                worker.notify()

            expected_count = len(image_files)
            start = time.monotonic()
            pictures = []
            while time.monotonic() - start < 20:
                pictures = server.vault.db.run_task(
                    lambda session: Picture.find(session)
                )
                remaining_files = [
                    f
                    for f in os.listdir(watch_dir)
                    if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                ]
                if len(pictures) >= expected_count and not remaining_files:
                    break
                time.sleep(0.25)

            assert len(pictures) == expected_count, (
                f"Expected {expected_count} pictures, got {len(pictures)}"
            )
            remaining_files = [
                f
                for f in os.listdir(watch_dir)
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
            ]
            assert not remaining_files, "Watch folder did not delete source files"
