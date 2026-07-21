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

                create_folder = client.post(
                    f"{API_PREFIX}/import-folders",
                    json={
                        "folder": pictures_dir,
                        "delete_after_import": False,
                    },
                )
                assert create_folder.status_code == 200

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

                create_folder = client.post(
                    f"{API_PREFIX}/import-folders",
                    json={
                        "folder": watch_dir,
                        "delete_after_import": True,
                    },
                )
                assert create_folder.status_code == 200

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


def test_watch_folder_imports_copied_files_with_old_mtime_after_initial_scan():
    """Copied files with preserved old mtime should still be imported.

    Regression test: after the first scan advances import_folder.last_checked,
    copying in files whose mtime is older than last_checked must still be
    discovered by the import finder.
    """
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
        assert len(image_files) >= 2, "Need at least two images for this test"

        watch_dir = os.path.join(temp_dir, "watch")
        os.makedirs(watch_dir, exist_ok=True)

        first_src = image_files[0]
        second_src = image_files[1]
        first_dst = os.path.join(watch_dir, os.path.basename(first_src))
        second_dst = os.path.join(watch_dir, os.path.basename(second_src))

        shutil.copy2(first_src, first_dst)

        with Server(server_config_path) as server:
            with TestClient(server.api) as client:
                response = client.post(
                    f"{API_PREFIX}/login",
                    json={"username": "testuser", "password": "testpassword"},
                )
                assert response.status_code == 200

                create_folder = client.post(
                    f"{API_PREFIX}/import-folders",
                    json={
                        "folder": watch_dir,
                        "delete_after_import": False,
                    },
                )
                assert create_folder.status_code == 200

                # Wait for initial import to complete.
                start = time.monotonic()
                while time.monotonic() - start < 20:
                    pictures = server.vault.db.run_task(
                        lambda session: Picture.find(session)
                    )
                    if len(pictures) >= 1:
                        break
                    time.sleep(0.25)

                initial_pictures = server.vault.db.run_task(
                    lambda session: Picture.find(session)
                )
                assert len(initial_pictures) == 1

                # Copy another file and force its mtime far in the past to
                # mimic file-manager copy behavior that preserves source mtime.
                shutil.copy2(second_src, second_dst)
                old_ts = time.time() - (7 * 24 * 60 * 60)
                os.utime(second_dst, (old_ts, old_ts))

                # The second file should still be discovered and imported.
                start = time.monotonic()
                while time.monotonic() - start < 20:
                    pictures = server.vault.db.run_task(
                        lambda session: Picture.find(session)
                    )
                    if len(pictures) >= 2:
                        break
                    time.sleep(0.25)

                final_pictures = server.vault.db.run_task(
                    lambda session: Picture.find(session)
                )
                assert len(final_pictures) == 2, (
                    "Expected second copied file to be imported even with old mtime"
                )


def test_watch_folder_retries_after_transient_hash_failure(monkeypatch):
    """A transient hash failure must not drop a watched file permanently.

    Regression test (platform-independent): the finder marks a candidate as
    "seen" at discovery time. If hashing then fails once and the file carries a
    copy-preserved old mtime, the old code left it in the seen-set forever, so it
    could never become a candidate again. The fix has the task report the failed
    path back to the finder, which forgets it and re-discovers it on the next
    scan. We simulate the transient failure by making
    ``ImageUtils.calculate_hash_from_file_path`` raise once for the target file,
    then succeed, and assert the file is eventually imported.
    """
    from pixlstash.utils.image_processing.image_utils import ImageUtils

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

        target_src = image_files[0]
        target_dst = os.path.join(watch_dir, os.path.basename(target_src))
        shutil.copy2(target_src, target_dst)
        # Preserve an old mtime, mimicking file-manager copies. This is what
        # makes the failure permanent without the fix: seen_ts <= last_checked,
        # so the only path back to candidacy is is_new_path being True again.
        old_ts = time.time() - (7 * 24 * 60 * 60)
        os.utime(target_dst, (old_ts, old_ts))

        target_normcase = os.path.normcase(os.path.abspath(target_dst))
        real_calculate_hash = ImageUtils.calculate_hash_from_file_path
        failed_once = {"done": False}

        def flaky_calculate_hash(file_path):
            if (
                not failed_once["done"]
                and os.path.normcase(os.path.abspath(file_path)) == target_normcase
            ):
                failed_once["done"] = True
                raise PermissionError(
                    f"[Errno 13] Permission denied (simulated): {file_path}"
                )
            return real_calculate_hash(file_path)

        monkeypatch.setattr(
            ImageUtils,
            "calculate_hash_from_file_path",
            staticmethod(flaky_calculate_hash),
        )

        with Server(server_config_path) as server:
            with TestClient(server.api) as client:
                response = client.post(
                    f"{API_PREFIX}/login",
                    json={"username": "testuser", "password": "testpassword"},
                )
                assert response.status_code == 200

                create_folder = client.post(
                    f"{API_PREFIX}/import-folders",
                    json={
                        "folder": watch_dir,
                        "delete_after_import": False,
                    },
                )
                assert create_folder.status_code == 200

                # The first hash attempt fails; the file must still be imported
                # on a later scan after the finder re-discovers it.
                start = time.monotonic()
                pictures = []
                while time.monotonic() - start < 30:
                    pictures = server.vault.db.run_task(
                        lambda session: Picture.find(session)
                    )
                    if len(pictures) >= 1:
                        break
                    time.sleep(0.25)

                assert failed_once["done"], (
                    "Expected the simulated transient hash failure to fire"
                )
                assert len(pictures) == 1, (
                    "Expected the watched file to be imported after a transient "
                    "hash failure (retry on a later scan)"
                )
