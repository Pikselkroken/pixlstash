import numpy as np
import logging
import shutil
import os
import json
import random
import tempfile
import time
import tomllib
import zipfile

import gc
import psutil
import tracemalloc
import collections

from PIL import Image
from fastapi.testclient import TestClient
from io import BytesIO
from pathlib import Path
from sqlmodel import select
from urllib.parse import quote

from pixlstash.db_models.picture import Picture
from pixlstash.db_models.picture_likeness import PictureLikeness
from pixlstash.db_models.face import Face
from pixlstash.db_models.picture_set import PictureSetMember
import pixlstash.routes.pictures as pictures_routes
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.tasks.task_type import TaskType
from pixlstash.picture_tagger import PictureTagger
from pixlstash.server import Server
from tests.utils import upload_pictures_and_wait, wait_for_faces

logger = get_logger(__name__)

_REGRESSION_DIR = Path(__file__).resolve().parent / "regression"

# CI runs in a reduced-size mode, but some tests intentionally reference
# fixture indices up to 15.
TEST_SIZE = 16 if os.getenv("GITHUB_ACTIONS") == "true" else 50
random_images = []
total_bytes = 0
for i in range(TEST_SIZE):
    arr = np.random.randint(0, 256, (1024, 1024, 3), dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, format="PNG")
    img_bytes = buf.getvalue()
    random_images.append(img_bytes)
    total_bytes += len(img_bytes)


def get_project_version():
    pyproject_path = os.path.join(os.path.dirname(__file__), "../pyproject.toml")
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def log_resources(label):
    process = psutil.Process()
    rss = process.memory_info().rss / (1024 * 1024)
    logger.info(f"[RESOURCE] {label}: RSS={rss:.2f}MB, Threads={process.num_threads()}")
    logger.info(f"[RESOURCE] {label}: gc objects={len(gc.get_objects())}")
    counter = collections.Counter(type(obj) for obj in gc.get_objects())
    logger.info(f"[RESOURCE] {label}: Top object types: {counter.most_common(5)}")
    if tracemalloc.is_tracing():
        logger.info(
            f"[RESOURCE] {label}: Tracemalloc current={tracemalloc.get_traced_memory()[0] / (1024 * 1024):.2f}MB, peak={tracemalloc.get_traced_memory()[1] / (1024 * 1024):.2f}MB"
        )


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")


def _write_semantic_regression_temp_artifact(actual: dict, device_tag: str) -> Path:
    temp_root = Path(tempfile.gettempdir()) / "pixlstash" / "semantic-regression"
    temp_root.mkdir(parents=True, exist_ok=True)
    artifact_path = temp_root / (
        f"semantic_search_{device_tag}_actual_{int(time.time() * 1000)}.json"
    )
    _write_json(artifact_path, actual)
    return artifact_path


_SEMANTIC_SCORE_TOLERANCE = 0.005


def _check_semantic_search_regression(
    regression_path: Path, actual: dict, device_tag: str
) -> None:
    """Compare *actual* against the baseline in *regression_path*.

    Scores are compared with ``_SEMANTIC_SCORE_TOLERANCE`` tolerance so that
    minor floating-point drift (e.g. from GPU non-determinism or occasional
    CPU spillover) does not produce false failures.  Baselines are treated as
    read-only by tests: when the baseline is missing or results diverge,
    the current payload is written to a temporary artifact and an AssertionError
    is raised so the developer can review and update the baseline intentionally.
    """
    if not regression_path.exists():
        artifact_path = _write_semantic_regression_temp_artifact(actual, device_tag)
        raise AssertionError(
            f"Semantic search baseline is missing for device='{device_tag}'.\n"
            f"Expected baseline: {regression_path}\n"
            f"Captured actual payload: {artifact_path}"
        )

    with open(regression_path, encoding="utf-8") as fh:
        baseline = json.load(fh)

    failures: list[str] = []

    baseline_queries = {row["query"]: row for row in baseline.get("queries", [])}
    for row in actual.get("queries", []):
        query = row["query"]
        if query not in baseline_queries:
            failures.append(f"New query not in baseline: {query!r}")
            continue
        base_row = baseline_queries[query]
        if row.get("top_description") != base_row.get("top_description"):
            failures.append(
                f"top_description changed for query {query!r}:\n"
                f"  baseline: {base_row.get('top_description')!r}\n"
                f"  actual:   {row.get('top_description')!r}"
            )
        score_delta = abs(float(row["top_score"]) - float(base_row["top_score"]))
        if score_delta > _SEMANTIC_SCORE_TOLERANCE:
            failures.append(
                f"top_score moved by {score_delta:.4f} (tolerance {_SEMANTIC_SCORE_TOLERANCE}) "
                f"for query {query!r}: baseline={base_row['top_score']} actual={row['top_score']}"
            )

    for key in ("avg_top_score", "min_top_score"):
        base_val = float(baseline.get("summary", {}).get(key, 0))
        actual_val = float(actual.get("summary", {}).get(key, 0))
        delta = abs(actual_val - base_val)
        if delta > _SEMANTIC_SCORE_TOLERANCE:
            failures.append(
                f"summary.{key} moved by {delta:.4f} (tolerance {_SEMANTIC_SCORE_TOLERANCE}): "
                f"baseline={base_val} actual={actual_val}"
            )

    if failures:
        artifact_path = _write_semantic_regression_temp_artifact(actual, device_tag)
        raise AssertionError(
            f"Semantic search regression detected for device='{device_tag}'.\n"
            f"Baseline file was not modified: {regression_path.name}.\n"
            f"Captured actual payload: {artifact_path}\n\n" + "\n".join(failures)
        )


def test_esmeralda_vault_character_and_logo():
    """Test that Esmeralda Vault exists and that the Logo is not associated with any character."""

    tracemalloc.start()
    log_resources("START test_esmeralda_vault_character_and_logo")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server-config.json")

        # This triggers _import_default_data
        with Server(server_config_path) as server:
            server.vault.import_default_data()
            client = TestClient(server.api)

            # Get a valid token
            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200
            # First access with the token
            response = client.get("/protected")
            assert response.status_code == 200
            assert response.json()["message"] == "You are authenticated!"

            pics = server.vault.db.run_task(lambda s: s.query(Picture).all())
            assert len(pics) > 0, "No pictures found in vault"

            logging.info(
                f"Found {len(pics)} pictures in vault, starting facial features processing"
            )

            # Find Esmeralda Vault character (by name)
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

            # Find all pictures, then filter by character association (robust to int/str id)
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

            # In the end the logo simply doesn't have any face and so no character association
            assert pic_id is None, (
                f"Logo picture should not be associated with any character (char_id={char_id})"
            )

            # Fetch the  picture form id
            img_resp = client.get(f"/pictures/{pics[0]['id']}.png")
            assert img_resp.status_code == 200
            logo_path = os.path.join(os.path.dirname(__file__), "../Logo.png")
            with open(logo_path, "rb") as f:
                logo_bytes = f.read()
            # Compare the full file
            assert img_resp.content == logo_bytes, (
                "Esmeralda Vault's picture does not match Logo.png"
            )
    gc.collect()
    log_resources("END test_esmeralda_vault_character_and_logo")


def test_create_and_get_default_character():
    """Test creating and fetching the default character 'Esmeralda'."""
    log_resources("START test_create_and_get_default_character")

    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            # Get a valid token
            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            # Create Esmeralda
            char_name = "Esmeralda"
            char_desc = "Default vault character"
            resp = client.post(
                "/characters",
                json={"name": char_name, "description": char_desc},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            logger.info("Created character: {}".format(data["character"]))
            char_id = data["character"]["id"]
            assert data["character"]["name"] == char_name
            assert data["character"]["description"] == char_desc

            # Fetch Esmeralda by id
            resp2 = client.get(f"/characters/{char_id}")
            assert resp2.status_code == 200
            char = resp2.json()
            logger.info("List object?? " + str(char))
            assert char["id"] == char_id
            assert char["name"] == char_name
            assert char["description"] == char_desc
    gc.collect()
    log_resources("END test_create_and_get_default_character")


def test_upload_existing_picture():
    """Test uploading an existing picture."""

    log_resources("START test_upload_existing_picture")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            # Get a valid token
            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200
            # Create a new picture
            img_bytes = random_images[0]
            images = [("file", ("master.png", img_bytes, "image/png"))]
            import_status = upload_pictures_and_wait(client, images)
            assert import_status["status"] == "completed"
            assert import_status["results"][0]["status"] == "success"
            picture_id_1 = import_status["results"][0]["picture_id"]

            # Fetch the picture and check it
            fetch_r1 = client.get(f"/pictures/{picture_id_1}/metadata")
            assert 200 == fetch_r1.status_code, "Error: " + fetch_r1.text
            fetched_picture = fetch_r1.json()
            assert fetched_picture["id"] == picture_id_1

            # Upload a new file
            img_bytes2 = random_images[1]
            files2 = [("file", ("iteration2.png", img_bytes2, "image/png"))]
            import_status_2 = upload_pictures_and_wait(client, files2)
            assert import_status_2["status"] == "completed"
            assert import_status_2["results"][0]["status"] == "success"
            picture_id_2 = import_status_2["results"][0]["picture_id"]

            # Fetch the new picture and check association
            fetch_r2 = client.get(f"/pictures/{picture_id_2}/metadata")
            assert 200 == fetch_r2.status_code, "Error: " + fetch_r2.text
            fetched_picture_2 = fetch_r2.json()
            logger.info(f"Fetched picture 2 metadata: {fetched_picture_2}")
            assert fetched_picture_2["id"] == picture_id_2

            # Upload the first picture again. Should report duplicate
            files3 = [("file", ("random_name.png", img_bytes, "image/png"))]
            import_status_3 = upload_pictures_and_wait(client, files3)
            assert import_status_3["status"] == "completed"
            assert import_status_3["results"][0]["status"] == "duplicate"

            image_bytes3 = random_images[2]
            # Upload two pictures at once, one existing and one new
            files4 = [
                files2[0],
                ("file", ("random_name2.png", image_bytes3, "image/png")),
            ]
            import_status_4 = upload_pictures_and_wait(client, files4)
            assert import_status_4["status"] == "completed"
            for i, result in enumerate(import_status_4["results"]):
                if i == 0:
                    assert result["status"] == "duplicate"  # Existing picture
                else:
                    assert result["status"] == "success"  # New picture

    gc.collect()
    log_resources("END test_upload_existing_picture")


def test_duplicate_import_updates_project_context():
    """Duplicate imports should still apply project association context."""

    log_resources("START test_duplicate_import_updates_project_context")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            img_bytes = random_images[0]
            files = [("file", ("duplicate-project.png", img_bytes, "image/png"))]

            first_import = upload_pictures_and_wait(client, files)
            assert first_import["status"] == "completed"
            assert first_import["results"][0]["status"] == "success"
            picture_id = first_import["results"][0]["picture_id"]

            unrelated_import = upload_pictures_and_wait(
                client,
                [("file", ("unrelated.png", random_images[1], "image/png"))],
            )
            assert unrelated_import["status"] == "completed"
            assert unrelated_import["results"][0]["status"] == "success"
            unrelated_picture_id = unrelated_import["results"][0]["picture_id"]

            project_resp = client.post(
                "/projects",
                json={"name": "Import Context Project"},
            )
            assert project_resp.status_code == 200
            project_id = project_resp.json()["id"]

            duplicate_import = upload_pictures_and_wait(
                client,
                files,
                form_data={"project_id": str(project_id)},
            )
            assert duplicate_import["status"] == "completed"
            assert duplicate_import["results"][0]["status"] == "duplicate"
            assert duplicate_import["results"][0]["picture_id"] == picture_id

            metadata_resp = client.get(f"/pictures/{picture_id}/metadata")
            assert metadata_resp.status_code == 200
            assert metadata_resp.json().get("project_id") == project_id

            unrelated_metadata_resp = client.get(
                f"/pictures/{unrelated_picture_id}/metadata"
            )
            assert unrelated_metadata_resp.status_code == 200
            assert unrelated_metadata_resp.json().get("project_id") is None

    gc.collect()
    log_resources("END test_duplicate_import_updates_project_context")


def test_set_project_for_existing_pictures_bulk():
    """Bulk project assignment endpoint should update only targeted pictures and support unassign."""

    log_resources("START test_set_project_for_existing_pictures_bulk")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            first_import = upload_pictures_and_wait(
                client,
                [("file", ("bulk-project-a.png", random_images[2], "image/png"))],
            )
            second_import = upload_pictures_and_wait(
                client,
                [("file", ("bulk-project-b.png", random_images[3], "image/png"))],
            )

            pic_a = first_import["results"][0]["picture_id"]
            pic_b = second_import["results"][0]["picture_id"]

            project_resp = client.post(
                "/projects",
                json={"name": "Bulk Set Project"},
            )
            assert project_resp.status_code == 200
            project_id = project_resp.json()["id"]

            set_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_a], "project_id": project_id},
            )
            assert set_resp.status_code == 200
            assert set_resp.json().get("updated_count") == 1

            meta_a = client.get(f"/pictures/{pic_a}/metadata")
            meta_b = client.get(f"/pictures/{pic_b}/metadata")
            assert meta_a.status_code == 200
            assert meta_b.status_code == 200
            assert meta_a.json().get("project_id") == project_id
            assert meta_b.json().get("project_id") is None

            unset_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_a], "project_id": None},
            )
            assert unset_resp.status_code == 200
            assert unset_resp.json().get("updated_count") == 1

            meta_a_after = client.get(f"/pictures/{pic_a}/metadata")
            assert meta_a_after.status_code == 200
            assert meta_a_after.json().get("project_id") is None

    gc.collect()
    log_resources("END test_set_project_for_existing_pictures_bulk")


def test_set_project_reconciles_project_set_membership():
    """Adding another project membership should not remove existing set memberships."""

    log_resources("START test_set_project_reconciles_project_set_membership")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            imported = upload_pictures_and_wait(
                client,
                [
                    (
                        "file",
                        (
                            "project-membership-reconcile.png",
                            random_images[12],
                            "image/png",
                        ),
                    )
                ],
            )
            pic_id = imported["results"][0]["picture_id"]

            project_a_resp = client.post(
                "/projects", json={"name": "Membership Project A"}
            )
            project_b_resp = client.post(
                "/projects", json={"name": "Membership Project B"}
            )
            assert project_a_resp.status_code == 200
            assert project_b_resp.status_code == 200
            project_a_id = project_a_resp.json()["id"]
            project_b_id = project_b_resp.json()["id"]

            set_resp = client.post(
                "/picture_sets",
                json={"name": "Membership Set A", "project_id": project_a_id},
            )
            assert set_resp.status_code == 200
            set_id = (set_resp.json().get("picture_set") or {}).get("id")
            assert set_id is not None

            add_resp = client.post(f"/picture_sets/{set_id}/members/{pic_id}")
            assert add_resp.status_code == 200

            assign_a_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_id], "project_id": project_a_id},
            )
            assert assign_a_resp.status_code == 200

            before_members_resp = client.get(f"/picture_sets/{set_id}/members")
            assert before_members_resp.status_code == 200
            assert pic_id in set(before_members_resp.json().get("picture_ids") or [])

            move_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_id], "project_id": project_b_id},
            )
            assert move_resp.status_code == 200

            after_members_resp = client.get(f"/picture_sets/{set_id}/members")
            assert after_members_resp.status_code == 200
            assert pic_id in set(after_members_resp.json().get("picture_ids") or [])

            project_a_pictures_resp = client.get(
                "/pictures",
                params={"project_id": str(project_a_id)},
            )
            project_b_pictures_resp = client.get(
                "/pictures",
                params={"project_id": str(project_b_id)},
            )
            assert project_a_pictures_resp.status_code == 200
            assert project_b_pictures_resp.status_code == 200
            project_a_ids = {p.get("id") for p in project_a_pictures_resp.json()}
            project_b_ids = {p.get("id") for p in project_b_pictures_resp.json()}
            assert pic_id in project_a_ids
            assert pic_id in project_b_ids

            metadata_resp = client.get(f"/pictures/{pic_id}/metadata")
            assert metadata_resp.status_code == 200
            assert metadata_resp.json().get("project_id") == project_b_id

    gc.collect()
    log_resources("END test_set_project_reconciles_project_set_membership")


def test_unassigned_picture_query_respects_project_filter():
    """UNASSIGNED picture queries should honor project scope filters."""

    log_resources("START test_unassigned_picture_query_respects_project_filter")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            first_import = upload_pictures_and_wait(
                client,
                [("file", ("unassigned-project-a.png", random_images[4], "image/png"))],
            )
            second_import = upload_pictures_and_wait(
                client,
                [("file", ("unassigned-project-b.png", random_images[5], "image/png"))],
            )

            pic_a = first_import["results"][0]["picture_id"]
            pic_b = second_import["results"][0]["picture_id"]

            project_resp = client.post(
                "/projects",
                json={"name": "Unassigned Scoped Query Project"},
            )
            assert project_resp.status_code == 200
            project_id = project_resp.json()["id"]

            set_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_a], "project_id": project_id},
            )
            assert set_resp.status_code == 200

            scoped_resp = client.get(
                "/pictures",
                params={"character_id": "UNASSIGNED", "project_id": str(project_id)},
            )
            assert scoped_resp.status_code == 200
            scoped_ids = {item.get("id") for item in scoped_resp.json()}
            assert pic_a in scoped_ids
            assert pic_b not in scoped_ids

            unassigned_project_resp = client.get(
                "/pictures",
                params={"character_id": "UNASSIGNED", "project_id": "UNASSIGNED"},
            )
            assert unassigned_project_resp.status_code == 200
            unassigned_project_ids = {
                item.get("id") for item in unassigned_project_resp.json()
            }
            assert pic_b in unassigned_project_ids
            assert pic_a not in unassigned_project_ids

            summary_scoped_resp = client.get(
                "/characters/UNASSIGNED/summary",
                params={"project_id": str(project_id)},
            )
            assert summary_scoped_resp.status_code == 200
            assert summary_scoped_resp.json().get("image_count") == 1

            summary_unassigned_project_resp = client.get(
                "/characters/UNASSIGNED/summary",
                params={"project_id": "UNASSIGNED"},
            )
            assert summary_unassigned_project_resp.status_code == 200
            assert summary_unassigned_project_resp.json().get("image_count") == 1

    gc.collect()
    log_resources("END test_unassigned_picture_query_respects_project_filter")


def test_unassigned_excludes_stack_when_any_member_is_in_set():
    """Unassigned should exclude the entire stack if any member is in a picture set."""

    log_resources("START test_unassigned_excludes_stack_when_any_member_is_in_set")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            first_import = upload_pictures_and_wait(
                client,
                [
                    (
                        "file",
                        ("unassigned-stack-set-a.png", random_images[6], "image/png"),
                    )
                ],
            )
            second_import = upload_pictures_and_wait(
                client,
                [
                    (
                        "file",
                        ("unassigned-stack-set-b.png", random_images[7], "image/png"),
                    )
                ],
            )

            pic_a = first_import["results"][0]["picture_id"]
            pic_b = second_import["results"][0]["picture_id"]

            stack_resp = client.post("/stacks", json={"picture_ids": [pic_a, pic_b]})
            assert stack_resp.status_code == 200

            set_resp = client.post(
                "/picture_sets",
                json={"name": "Unassigned Stack Exclusion Set"},
            )
            assert set_resp.status_code == 200
            set_id = (set_resp.json().get("picture_set") or {}).get("id")
            assert set_id is not None

            add_member_resp = client.post(f"/picture_sets/{set_id}/members/{pic_a}")
            assert add_member_resp.status_code == 200

            unassigned_resp = client.get(
                "/pictures", params={"character_id": "UNASSIGNED"}
            )
            assert unassigned_resp.status_code == 200
            unassigned_ids = {item.get("id") for item in unassigned_resp.json()}
            assert pic_a not in unassigned_ids
            assert pic_b not in unassigned_ids

            summary_resp = client.get("/characters/UNASSIGNED/summary")
            assert summary_resp.status_code == 200
            assert summary_resp.json().get("image_count") == 0

    gc.collect()
    log_resources("END test_unassigned_excludes_stack_when_any_member_is_in_set")


def test_unassigned_project_grid_includes_stack_when_leader_is_out_of_scope():
    """Project-scoped UNASSIGNED grid should not drop stacks only because global leader is out of scope."""

    log_resources(
        "START test_unassigned_project_grid_includes_stack_when_leader_is_out_of_scope"
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            import_a = upload_pictures_and_wait(
                client,
                [
                    (
                        "file",
                        (
                            "unassigned-project-stack-a.png",
                            random_images[0],
                            "image/png",
                        ),
                    )
                ],
            )
            import_b = upload_pictures_and_wait(
                client,
                [
                    (
                        "file",
                        (
                            "unassigned-project-stack-b.png",
                            random_images[1],
                            "image/png",
                        ),
                    )
                ],
            )

            pic_a = import_a["results"][0]["picture_id"]
            pic_b = import_b["results"][0]["picture_id"]

            project_a_resp = client.post("/projects", json={"name": "Project A"})
            project_b_resp = client.post("/projects", json={"name": "Project B"})
            assert project_a_resp.status_code == 200
            assert project_b_resp.status_code == 200
            project_a_id = project_a_resp.json()["id"]
            project_b_id = project_b_resp.json()["id"]

            assign_resp = client.patch(
                "/pictures/project",
                json={
                    "picture_ids": [pic_a, pic_b],
                    "project_id": project_a_id,
                },
            )
            assert assign_resp.status_code == 200

            clear_b_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_b], "project_id": None},
            )
            assert clear_b_resp.status_code == 200

            assign_b_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_b], "project_id": project_b_id},
            )
            assert assign_b_resp.status_code == 200

            stack_resp = client.post("/stacks", json={"picture_ids": [pic_a, pic_b]})
            assert stack_resp.status_code == 200
            stack_id = stack_resp.json().get("id")
            assert stack_id is not None

            # Force pic_b to be stack leader while querying project A scope.
            reorder_resp = client.patch(
                f"/stacks/{stack_id}/order",
                json={"picture_ids": [pic_b, pic_a]},
            )
            assert reorder_resp.status_code == 200

            summary_resp = client.get(
                "/characters/UNASSIGNED/summary",
                params={"project_id": str(project_a_id)},
            )
            assert summary_resp.status_code == 200
            assert summary_resp.json().get("image_count") == 1

            grid_resp = client.get(
                "/pictures",
                params={
                    "character_id": "UNASSIGNED",
                    "project_id": str(project_a_id),
                    "fields": "grid",
                },
            )
            assert grid_resp.status_code == 200
            grid_ids = {item.get("id") for item in grid_resp.json()}
            assert pic_a in grid_ids

    gc.collect()
    log_resources(
        "END test_unassigned_project_grid_includes_stack_when_leader_is_out_of_scope"
    )


def test_unassigned_project_scope_uses_project_character_and_set_membership_only():
    """Project-scoped UNASSIGNED should ignore assignments from other projects."""

    log_resources(
        "START test_unassigned_project_scope_uses_project_character_and_set_membership_only"
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            imported = upload_pictures_and_wait(
                client,
                [
                    (
                        "file",
                        (
                            "project-scope-assignment.png",
                            random_images[2],
                            "image/png",
                        ),
                    )
                ],
            )
            pic_id = imported["results"][0]["picture_id"]

            project_a_resp = client.post("/projects", json={"name": "Project Scope A"})
            project_b_resp = client.post("/projects", json={"name": "Project Scope B"})
            assert project_a_resp.status_code == 200
            assert project_b_resp.status_code == 200
            project_a_id = project_a_resp.json()["id"]
            project_b_id = project_b_resp.json()["id"]

            assign_project_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_id], "project_id": project_a_id},
            )
            assert assign_project_resp.status_code == 200

            character_resp = client.post(
                "/characters",
                json={"name": "Other Project Character", "project_id": project_b_id},
            )
            assert character_resp.status_code == 200
            character_id = (character_resp.json().get("character") or {}).get("id")
            assert character_id is not None

            def create_face(session, picture_id, target_character_id):
                face = Face(
                    picture_id=picture_id,
                    frame_index=0,
                    face_index=0,
                    character_id=target_character_id,
                    bbox=[0, 0, 16, 16],
                )
                session.add(face)
                session.commit()
                return face.id

            created_face_id = server.vault.db.run_task(
                create_face, pic_id, character_id
            )
            assert created_face_id is not None

            set_resp = client.post(
                "/picture_sets",
                json={
                    "name": "Other Project Set",
                    "project_id": project_b_id,
                },
            )
            assert set_resp.status_code == 200
            other_project_set_id = (set_resp.json().get("picture_set") or {}).get("id")
            assert other_project_set_id is not None

            def force_set_member(session, target_set_id: int, target_picture_id: int):
                exists = session.exec(
                    select(PictureSetMember).where(
                        PictureSetMember.set_id == target_set_id,
                        PictureSetMember.picture_id == target_picture_id,
                    )
                ).first()
                if not exists:
                    session.add(
                        PictureSetMember(
                            set_id=target_set_id,
                            picture_id=target_picture_id,
                        )
                    )
                    session.commit()

            server.vault.db.run_task(force_set_member, other_project_set_id, pic_id)

            # Global unassigned should exclude this picture (it is globally assigned).
            global_unassigned_resp = client.get(
                "/pictures",
                params={"character_id": "UNASSIGNED"},
            )
            assert global_unassigned_resp.status_code == 200
            global_unassigned_ids = {
                item.get("id") for item in global_unassigned_resp.json()
            }
            assert pic_id not in global_unassigned_ids

            # Project-A-scoped unassigned should include it because assignments are
            # only to project-B character/set.
            project_unassigned_resp = client.get(
                "/pictures",
                params={"character_id": "UNASSIGNED", "project_id": str(project_a_id)},
            )
            assert project_unassigned_resp.status_code == 200
            project_unassigned_ids = {
                item.get("id") for item in project_unassigned_resp.json()
            }
            assert pic_id in project_unassigned_ids

            summary_resp = client.get(
                "/characters/UNASSIGNED/summary",
                params={"project_id": str(project_a_id)},
            )
            assert summary_resp.status_code == 200
            assert summary_resp.json().get("image_count") == 1

    gc.collect()
    log_resources(
        "END test_unassigned_project_scope_uses_project_character_and_set_membership_only"
    )


def test_unassigned_project_scope_ignores_global_character_and_set_assignments():
    """Project-scoped UNASSIGNED should include pictures assigned only to global groups."""

    log_resources(
        "START test_unassigned_project_scope_ignores_global_character_and_set_assignments"
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            imported = upload_pictures_and_wait(
                client,
                [
                    (
                        "file",
                        (
                            "project-scope-global-assignment.png",
                            random_images[9],
                            "image/png",
                        ),
                    )
                ],
            )
            pic_id = imported["results"][0]["picture_id"]

            project_resp = client.post(
                "/projects", json={"name": "Project Scope Global Test"}
            )
            assert project_resp.status_code == 200
            project_id = project_resp.json()["id"]

            assign_project_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_id], "project_id": project_id},
            )
            assert assign_project_resp.status_code == 200

            global_character_resp = client.post(
                "/characters",
                json={"name": "Global Character", "project_id": None},
            )
            assert global_character_resp.status_code == 200
            global_character_id = (
                global_character_resp.json().get("character") or {}
            ).get("id")
            assert global_character_id is not None

            def create_face(session, picture_id, target_character_id):
                face = Face(
                    picture_id=picture_id,
                    frame_index=0,
                    face_index=0,
                    character_id=target_character_id,
                    bbox=[0, 0, 16, 16],
                )
                session.add(face)
                session.commit()
                return face.id

            created_face_id = server.vault.db.run_task(
                create_face, pic_id, global_character_id
            )
            assert created_face_id is not None

            global_set_resp = client.post(
                "/picture_sets",
                json={"name": "Global Set", "project_id": None},
            )
            assert global_set_resp.status_code == 200
            global_set_id = (global_set_resp.json().get("picture_set") or {}).get("id")
            assert global_set_id is not None

            add_member_resp = client.post(
                f"/picture_sets/{global_set_id}/members/{pic_id}"
            )
            assert add_member_resp.status_code == 200

            project_unassigned_resp = client.get(
                "/pictures",
                params={"character_id": "UNASSIGNED", "project_id": str(project_id)},
            )
            assert project_unassigned_resp.status_code == 200
            project_unassigned_ids = {
                item.get("id") for item in project_unassigned_resp.json()
            }
            assert pic_id in project_unassigned_ids

            summary_resp = client.get(
                "/characters/UNASSIGNED/summary",
                params={"project_id": str(project_id)},
            )
            assert summary_resp.status_code == 200
            assert summary_resp.json().get("image_count") == 1

    gc.collect()
    log_resources(
        "END test_unassigned_project_scope_ignores_global_character_and_set_assignments"
    )


def test_project_scoped_picture_set_counts_only_include_project_pictures():
    """Project-scoped set counts should include only members in that project."""

    log_resources(
        "START test_project_scoped_picture_set_counts_only_include_project_pictures"
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            login_resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert login_resp.status_code == 200

            imported_a = upload_pictures_and_wait(
                client,
                [("file", ("set-project-a.png", random_images[3], "image/png"))],
            )
            imported_b = upload_pictures_and_wait(
                client,
                [("file", ("set-project-b.png", random_images[4], "image/png"))],
            )
            pic_a = imported_a["results"][0]["picture_id"]
            pic_b = imported_b["results"][0]["picture_id"]

            project_a_resp = client.post(
                "/projects", json={"name": "Set Count Project A"}
            )
            project_b_resp = client.post(
                "/projects", json={"name": "Set Count Project B"}
            )
            assert project_a_resp.status_code == 200
            assert project_b_resp.status_code == 200
            project_a_id = project_a_resp.json()["id"]
            project_b_id = project_b_resp.json()["id"]

            assign_project_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_a], "project_id": project_a_id},
            )
            assign_other_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_b], "project_id": project_b_id},
            )
            assert assign_project_resp.status_code == 200
            assert assign_other_resp.status_code == 200

            set_resp = client.post(
                "/picture_sets",
                json={"name": "Project Scoped Set", "project_id": project_a_id},
            )
            assert set_resp.status_code == 200
            set_id = (set_resp.json().get("picture_set") or {}).get("id")
            assert set_id is not None

            def force_members(session, target_set_id: int, picture_ids: list[int]):
                for pid in picture_ids:
                    exists = session.exec(
                        select(PictureSetMember).where(
                            PictureSetMember.set_id == target_set_id,
                            PictureSetMember.picture_id == pid,
                        )
                    ).first()
                    if exists:
                        continue
                    session.add(PictureSetMember(set_id=target_set_id, picture_id=pid))
                session.commit()

            server.vault.db.run_task(force_members, set_id, [pic_a, pic_b])

            all_sets_resp = client.get("/picture_sets")
            assert all_sets_resp.status_code == 200
            all_set = next(s for s in all_sets_resp.json() if s.get("id") == set_id)
            assert all_set.get("picture_count") == 2

            scoped_sets_resp = client.get(
                "/picture_sets",
                params={"project_id": str(project_a_id)},
            )
            assert scoped_sets_resp.status_code == 200
            scoped_set = next(
                s for s in scoped_sets_resp.json() if s.get("id") == set_id
            )
            assert scoped_set.get("picture_count") == 1

            scoped_set_view_resp = client.get(
                f"/picture_sets/{set_id}",
                params={"project_id": str(project_a_id)},
            )
            assert scoped_set_view_resp.status_code == 200
            scoped_ids = {
                p.get("id") for p in (scoped_set_view_resp.json().get("pictures") or [])
            }
            assert pic_a in scoped_ids
            assert pic_b not in scoped_ids

    gc.collect()
    log_resources(
        "END test_project_scoped_picture_set_counts_only_include_project_pictures"
    )


def test_pictures_endpoint_supports_set_intersection_filter():
    """/pictures should support set_ids + set_mode=intersection filters."""

    log_resources("START test_pictures_endpoint_supports_set_intersection_filter")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            login_resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert login_resp.status_code == 200

            imported_a = upload_pictures_and_wait(
                client,
                [("file", ("set-intersection-a.png", random_images[8], "image/png"))],
            )
            imported_b = upload_pictures_and_wait(
                client,
                [("file", ("set-intersection-b.png", random_images[9], "image/png"))],
            )
            pic_a = imported_a["results"][0]["picture_id"]
            pic_b = imported_b["results"][0]["picture_id"]

            set_a_resp = client.post("/picture_sets", json={"name": "Set A"})
            set_b_resp = client.post("/picture_sets", json={"name": "Set B"})
            assert set_a_resp.status_code == 200
            assert set_b_resp.status_code == 200
            set_a_id = (set_a_resp.json().get("picture_set") or {}).get("id")
            set_b_id = (set_b_resp.json().get("picture_set") or {}).get("id")
            assert set_a_id is not None
            assert set_b_id is not None

            assert (
                client.post(f"/picture_sets/{set_a_id}/members/{pic_a}").status_code
                == 200
            )
            assert (
                client.post(f"/picture_sets/{set_a_id}/members/{pic_b}").status_code
                == 200
            )
            assert (
                client.post(f"/picture_sets/{set_b_id}/members/{pic_b}").status_code
                == 200
            )

            union_resp = client.get(
                "/pictures",
                params=[("set_ids", str(set_a_id)), ("set_ids", str(set_b_id))],
            )
            assert union_resp.status_code == 200
            union_ids = {item.get("id") for item in union_resp.json()}
            assert pic_a in union_ids
            assert pic_b in union_ids

            intersection_resp = client.get(
                "/pictures",
                params=[
                    ("set_ids", str(set_a_id)),
                    ("set_ids", str(set_b_id)),
                    ("set_mode", "intersection"),
                ],
            )
            assert intersection_resp.status_code == 200
            intersection_ids = {item.get("id") for item in intersection_resp.json()}
            assert pic_b in intersection_ids
            assert pic_a not in intersection_ids

    gc.collect()
    log_resources("END test_pictures_endpoint_supports_set_intersection_filter")


def test_add_picture_to_project_set_aligns_picture_project_membership():
    """Adding to a project set should not overwrite existing project memberships."""

    log_resources(
        "START test_add_picture_to_project_set_aligns_picture_project_membership"
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            login_resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert login_resp.status_code == 200

            imported = upload_pictures_and_wait(
                client,
                [
                    (
                        "file",
                        ("set-align-project.png", random_images[5], "image/png"),
                    )
                ],
            )
            pic_id = imported["results"][0]["picture_id"]

            project_a_resp = client.post(
                "/projects", json={"name": "Set Align Project A"}
            )
            project_b_resp = client.post(
                "/projects", json={"name": "Set Align Project B"}
            )
            assert project_a_resp.status_code == 200
            assert project_b_resp.status_code == 200
            project_a_id = project_a_resp.json()["id"]
            project_b_id = project_b_resp.json()["id"]

            assign_other_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_id], "project_id": project_b_id},
            )
            assert assign_other_resp.status_code == 200

            set_resp = client.post(
                "/picture_sets",
                json={"name": "Set Align Project", "project_id": project_a_id},
            )
            assert set_resp.status_code == 200
            set_id = (set_resp.json().get("picture_set") or {}).get("id")
            assert set_id is not None

            add_resp = client.post(f"/picture_sets/{set_id}/members/{pic_id}")
            assert add_resp.status_code == 200

            metadata_resp = client.get(f"/pictures/{pic_id}/metadata")
            assert metadata_resp.status_code == 200
            # Adding a picture to a project-scoped set aligns its primary
            # project to that set's project.
            assert metadata_resp.json().get("project_id") == project_a_id

            project_a_resp = client.get(
                "/pictures",
                params={"project_id": str(project_a_id)},
            )
            assert project_a_resp.status_code == 200
            project_a_ids = {p.get("id") for p in project_a_resp.json()}
            assert pic_id in project_a_ids

            # Existing membership to project B should remain alongside the
            # aligned primary project.
            project_b_resp = client.get(
                "/pictures",
                params={"project_id": str(project_b_id)},
            )
            assert project_b_resp.status_code == 200
            project_b_ids = {p.get("id") for p in project_b_resp.json()}
            assert pic_id in project_b_ids

    gc.collect()
    log_resources(
        "END test_add_picture_to_project_set_aligns_picture_project_membership"
    )


def test_all_picture_query_respects_project_filter():
    """ALL picture queries should honor project_id filters."""

    log_resources("START test_all_picture_query_respects_project_filter")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            first_import = upload_pictures_and_wait(
                client,
                [("file", ("all-project-a.png", random_images[6], "image/png"))],
            )
            second_import = upload_pictures_and_wait(
                client,
                [("file", ("all-project-b.png", random_images[7], "image/png"))],
            )

            pic_a = first_import["results"][0]["picture_id"]
            pic_b = second_import["results"][0]["picture_id"]

            project_resp = client.post(
                "/projects",
                json={"name": "All Scoped Query Project"},
            )
            assert project_resp.status_code == 200
            project_id = project_resp.json()["id"]

            set_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_a], "project_id": project_id},
            )
            assert set_resp.status_code == 200

            scoped_resp = client.get(
                "/pictures",
                params={"project_id": str(project_id)},
            )
            assert scoped_resp.status_code == 200
            scoped_ids = {item.get("id") for item in scoped_resp.json()}
            assert pic_a in scoped_ids
            assert pic_b not in scoped_ids

            unassigned_project_resp = client.get(
                "/pictures",
                params={"project_id": "UNASSIGNED"},
            )
            assert unassigned_project_resp.status_code == 200
            unassigned_ids = {item.get("id") for item in unassigned_project_resp.json()}
            assert pic_b in unassigned_ids
            assert pic_a not in unassigned_ids

    gc.collect()
    log_resources("END test_all_picture_query_respects_project_filter")


def test_stack_query_respects_project_filter():
    """Stack endpoint should honor project_id filters when building candidates."""

    log_resources("START test_stack_query_respects_project_filter")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            imports = [
                upload_pictures_and_wait(
                    client,
                    [("file", ("stack-proj-a1.png", random_images[8], "image/png"))],
                ),
                upload_pictures_and_wait(
                    client,
                    [("file", ("stack-proj-a2.png", random_images[9], "image/png"))],
                ),
                upload_pictures_and_wait(
                    client,
                    [("file", ("stack-proj-b1.png", random_images[10], "image/png"))],
                ),
                upload_pictures_and_wait(
                    client,
                    [("file", ("stack-proj-b2.png", random_images[11], "image/png"))],
                ),
            ]

            pic_a1 = imports[0]["results"][0]["picture_id"]
            pic_a2 = imports[1]["results"][0]["picture_id"]
            pic_b1 = imports[2]["results"][0]["picture_id"]
            pic_b2 = imports[3]["results"][0]["picture_id"]

            project_a_resp = client.post(
                "/projects",
                json={"name": "Stacks Project A"},
            )
            project_b_resp = client.post(
                "/projects",
                json={"name": "Stacks Project B"},
            )
            assert project_a_resp.status_code == 200
            assert project_b_resp.status_code == 200
            project_a_id = project_a_resp.json()["id"]
            project_b_id = project_b_resp.json()["id"]

            set_a_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_a1, pic_a2], "project_id": project_a_id},
            )
            set_b_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_b1, pic_b2], "project_id": project_b_id},
            )
            assert set_a_resp.status_code == 200
            assert set_b_resp.status_code == 200

            def seed_likeness(session):
                a1, a2 = sorted([pic_a1, pic_a2])
                b1, b2 = sorted([pic_b1, pic_b2])
                session.add(
                    PictureLikeness(
                        picture_id_a=a1,
                        picture_id_b=a2,
                        likeness=0.99,
                        metric="test",
                    )
                )
                session.add(
                    PictureLikeness(
                        picture_id_a=b1,
                        picture_id_b=b2,
                        likeness=0.99,
                        metric="test",
                    )
                )
                session.commit()

            server.vault.db.run_task(seed_likeness)

            stacks_a_resp = client.get(
                "/pictures/stacks",
                params={"threshold": 0.9, "project_id": str(project_a_id)},
            )
            assert stacks_a_resp.status_code == 200
            stacks_a_ids = {item.get("id") for item in stacks_a_resp.json()}
            assert pic_a1 in stacks_a_ids
            assert pic_a2 in stacks_a_ids
            assert pic_b1 not in stacks_a_ids
            assert pic_b2 not in stacks_a_ids

            stacks_b_resp = client.get(
                "/pictures/stacks",
                params={"threshold": 0.9, "project_id": str(project_b_id)},
            )
            assert stacks_b_resp.status_code == 200
            stacks_b_ids = {item.get("id") for item in stacks_b_resp.json()}
            assert pic_b1 in stacks_b_ids
            assert pic_b2 in stacks_b_ids
            assert pic_a1 not in stacks_b_ids
            assert pic_a2 not in stacks_b_ids

    gc.collect()
    log_resources("END test_stack_query_respects_project_filter")


def test_smart_score_query_respects_project_filter(monkeypatch):
    """SMART_SCORE queries should honor project_id filters when selecting candidates."""

    log_resources("START test_smart_score_query_respects_project_filter")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            first_import = upload_pictures_and_wait(
                client,
                [("file", ("smart-project-a.png", random_images[12], "image/png"))],
            )
            second_import = upload_pictures_and_wait(
                client,
                [("file", ("smart-project-b.png", random_images[13], "image/png"))],
            )

            pic_a = first_import["results"][0]["picture_id"]
            pic_b = second_import["results"][0]["picture_id"]

            project_resp = client.post(
                "/projects",
                json={"name": "Smart Score Scoped Project"},
            )
            assert project_resp.status_code == 200
            project_id = project_resp.json()["id"]

            set_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_a], "project_id": project_id},
            )
            assert set_resp.status_code == 200

            def fake_find_pictures_by_smart_score(
                _server,
                _format,
                _offset,
                _limit,
                _descending,
                candidate_ids=None,
                penalised_tags=None,
                only_deleted=False,
                progress_reporter=None,
                **_kwargs,
            ):
                ids = sorted(set(candidate_ids or []))
                return [{"id": pid, "score": 0.0, "smartScore": 0.0} for pid in ids]

            monkeypatch.setattr(
                pictures_routes,
                "find_pictures_by_smart_score",
                fake_find_pictures_by_smart_score,
            )

            scoped_resp = client.get(
                "/pictures",
                params={"sort": "SMART_SCORE", "project_id": str(project_id)},
            )
            assert scoped_resp.status_code == 200
            scoped_ids = {item.get("id") for item in scoped_resp.json()}
            assert pic_a in scoped_ids
            assert pic_b not in scoped_ids

            unassigned_resp = client.get(
                "/pictures",
                params={"sort": "SMART_SCORE", "project_id": "UNASSIGNED"},
            )
            assert unassigned_resp.status_code == 200
            unassigned_ids = {item.get("id") for item in unassigned_resp.json()}
            assert pic_b in unassigned_ids
            assert pic_a not in unassigned_ids

    gc.collect()
    log_resources("END test_smart_score_query_respects_project_filter")


def test_character_likeness_query_respects_project_filter(monkeypatch):
    """CHARACTER_LIKENESS queries should honor project_id candidate scoping."""

    log_resources("START test_character_likeness_query_respects_project_filter")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            # Create reference character required by CHARACTER_LIKENESS sort.
            char_resp = client.post("/characters", json={"name": "Ref Character"})
            assert char_resp.status_code == 200
            reference_character_id = char_resp.json()["character"]["id"]

            first_import = upload_pictures_and_wait(
                client,
                [("file", ("likeness-project-a.png", random_images[14], "image/png"))],
            )
            second_import = upload_pictures_and_wait(
                client,
                [("file", ("likeness-project-b.png", random_images[15], "image/png"))],
            )

            pic_a = first_import["results"][0]["picture_id"]
            pic_b = second_import["results"][0]["picture_id"]

            project_resp = client.post(
                "/projects",
                json={"name": "Likeness Scoped Project"},
            )
            assert project_resp.status_code == 200
            project_id = project_resp.json()["id"]

            set_resp = client.patch(
                "/pictures/project",
                json={"picture_ids": [pic_a], "project_id": project_id},
            )
            assert set_resp.status_code == 200

            def fake_find_pictures_by_character_likeness(
                _server,
                _character_id,
                _reference_character_id,
                _offset,
                _limit,
                _descending,
                candidate_ids=None,
            ):
                ids = sorted(set(candidate_ids or []))
                return [
                    {
                        "id": pid,
                        "score": 0.0,
                        "character_likeness": 0.0,
                    }
                    for pid in ids
                ]

            monkeypatch.setattr(
                pictures_routes,
                "find_pictures_by_character_likeness",
                fake_find_pictures_by_character_likeness,
            )

            scoped_resp = client.get(
                "/pictures",
                params={
                    "sort": "CHARACTER_LIKENESS",
                    "reference_character_id": str(reference_character_id),
                    "project_id": str(project_id),
                },
            )
            assert scoped_resp.status_code == 200
            scoped_ids = {item.get("id") for item in scoped_resp.json()}
            assert pic_a in scoped_ids
            assert pic_b not in scoped_ids

            unassigned_resp = client.get(
                "/pictures",
                params={
                    "sort": "CHARACTER_LIKENESS",
                    "reference_character_id": str(reference_character_id),
                    "project_id": "UNASSIGNED",
                },
            )
            assert unassigned_resp.status_code == 200
            unassigned_ids = {item.get("id") for item in unassigned_resp.json()}
            assert pic_b in unassigned_ids
            assert pic_a not in unassigned_ids

    gc.collect()
    log_resources("END test_character_likeness_query_respects_project_filter")


def test_import_sidecar_txt_tags_for_matching_image():
    """Import should apply matching sidecar .txt tags and ignore orphan .txt files."""

    log_resources("START test_import_sidecar_txt_tags_for_matching_image")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            image_name = "sidecar_sample.png"
            files = [
                ("file", (image_name, random_images[0], "image/png")),
                (
                    "file",
                    (
                        "sidecar_sample.txt",
                        b"1girl, blue_eyes, smiling",
                        "text/plain",
                    ),
                ),
                (
                    "file",
                    (
                        "orphan_only.txt",
                        b"1girl, blue_eyes, smiling",
                        "text/plain",
                    ),
                ),
            ]

            import_status = upload_pictures_and_wait(client, files)
            assert import_status["status"] == "completed"
            assert import_status["results"][0]["status"] == "success"
            picture_id = import_status["results"][0]["picture_id"]

            metadata_resp = client.get(f"/pictures/{picture_id}/metadata")
            assert metadata_resp.status_code == 200
            tags = {
                (entry.get("tag") or "").strip().lower()
                for entry in (metadata_resp.json().get("tags") or [])
                if isinstance(entry, dict)
            }
            assert "1girl" in tags
            assert "blue eyes" in tags
            assert "smiling" in tags

    gc.collect()
    log_resources("END test_import_sidecar_txt_tags_for_matching_image")


def test_import_zip_sidecar_txt_tags_for_matching_image():
    """Zip import should apply matching sidecar .txt tags and ignore orphan .txt files."""

    log_resources("START test_import_zip_sidecar_txt_tags_for_matching_image")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, "w") as zip_file:
                zip_file.writestr("dataset/zip_sidecar.png", random_images[1])
                zip_file.writestr(
                    "dataset/zip_sidecar.txt",
                    "1girl, blue_eyes, smiling",
                )
                zip_file.writestr(
                    "dataset/orphan_sidecar.txt",
                    "1girl, blue_eyes, smiling",
                )

            files = [
                (
                    "file",
                    (
                        "dataset.zip",
                        zip_buffer.getvalue(),
                        "application/zip",
                    ),
                )
            ]

            import_status = upload_pictures_and_wait(client, files)
            assert import_status["status"] == "completed"
            assert import_status["results"][0]["status"] == "success"
            picture_id = import_status["results"][0]["picture_id"]

            metadata_resp = client.get(f"/pictures/{picture_id}/metadata")
            assert metadata_resp.status_code == 200
            tags = {
                (entry.get("tag") or "").strip().lower()
                for entry in (metadata_resp.json().get("tags") or [])
                if isinstance(entry, dict)
            }
            assert "1girl" in tags
            assert "blue eyes" in tags
            assert "smiling" in tags

    gc.collect()
    log_resources("END test_import_zip_sidecar_txt_tags_for_matching_image")


def test_duplicate_import_with_sidecar_replaces_existing_tags():
    """Duplicate import with sidecar captions should replace existing tags atomically."""

    log_resources("START test_duplicate_import_with_sidecar_replaces_existing_tags")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            # First import creates a picture.
            first_files = [
                ("file", ("replace_tags.png", random_images[2], "image/png"))
            ]
            first_import = upload_pictures_and_wait(client, first_files)
            assert first_import["status"] == "completed"
            assert first_import["results"][0]["status"] == "success"
            picture_id = first_import["results"][0]["picture_id"]

            # Seed a pre-existing manual tag that should be removed on duplicate+sidecar import.
            add_resp = client.post(
                f"/pictures/{picture_id}/tags",
                json={"tag": "legacy tag"},
            )
            assert add_resp.status_code == 200

            dup_files = [
                ("file", ("replace_tags.png", random_images[2], "image/png")),
                (
                    "file",
                    (
                        "replace_tags.txt",
                        b"1girl, blue_eyes, smiling",
                        "text/plain",
                    ),
                ),
            ]
            dup_import = upload_pictures_and_wait(client, dup_files)
            assert dup_import["status"] == "completed"
            assert dup_import["results"][0]["status"] == "duplicate"
            assert dup_import["results"][0]["picture_id"] == picture_id

            metadata_resp = client.get(f"/pictures/{picture_id}/metadata")
            assert metadata_resp.status_code == 200
            tags = {
                (entry.get("tag") or "").strip().lower()
                for entry in (metadata_resp.json().get("tags") or [])
                if isinstance(entry, dict)
            }
            assert "legacy tag" not in tags
            assert "1girl" in tags
            assert "blue eyes" in tags
            assert "smiling" in tags

    gc.collect()
    log_resources("END test_duplicate_import_with_sidecar_replaces_existing_tags")


def test_favicon():
    """Test /favicon.ico endpoint returns 200 and PNG content."""
    log_resources("START test_favicon")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path) as server:
            client = TestClient(server.api)
            resp = client.get("/favicon.ico")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "image/vnd.microsoft.icon"
            assert resp.content[:4] == b"\x00\x00\x01\x00"  # ICO file signature
    gc.collect()
    log_resources("END test_favicon")


def test_characters_summary():
    """Test /characters/summary endpoint returns 200 and valid structure."""
    log_resources("START test_characters_summary")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        src_dir = os.path.join(os.path.dirname(__file__), "../pictures")
        image_files = [
            f
            for f in os.listdir(src_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]

        with Server(server_config_path) as server:
            server.vault.import_default_data()
            client = TestClient(server.api)

            # Get a valid token
            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            # Get Esmeralda Vault character ID
            resp = client.get("/characters")
            assert resp.status_code == 200
            chars = resp.json()
            esmeralda_id = None
            for c in chars:
                if c.get("name") == "Esmeralda Vault":
                    esmeralda_id = c["id"]
                    break
            assert esmeralda_id is not None, "Esmeralda Vault character not found"

            # Upload all images as new pictures
            picture_ids = []
            for fname in image_files:
                with open(os.path.join(src_dir, fname), "rb") as f:
                    files = [("file", (fname, f.read(), "image/png"))]
                    import_status = upload_pictures_and_wait(client, files)
                assert import_status["status"] == "completed"
                assert import_status["results"][0]["status"] == "success"
                picture_ids.append(import_status["results"][0]["picture_id"])

            # Wait for facial features to be processed and associate Esmeralda Vault with largest face in each picture
            for pid in picture_ids:
                faces_data = wait_for_faces(client, pid, timeout_s=60)
                if not faces_data:
                    continue

                def face_area(face):
                    bbox = face.get("bbox")
                    if bbox and len(bbox) == 4:
                        return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                    return 0

                largest_face = max(faces_data, key=face_area)
                face_id = largest_face.get("id")
                assert face_id is not None
                assoc_resp = client.post(
                    f"/characters/{esmeralda_id}/faces",
                    json={"face_ids": [face_id]},
                )
                assert assoc_resp.status_code == 200, (
                    f"Failed to associate face {face_id} with Esmeralda Vault: {assoc_resp.text}"
                )
                assoc_data = assoc_resp.json()
                assert assoc_data["status"] == "success"

                # Query the character-face association to verify
                check_assoc_resp = client.get(f"/characters/{esmeralda_id}/faces")
                assert check_assoc_resp.status_code == 200, (
                    f"Failed to fetch faces for character {esmeralda_id} after association"
                )
                faces_data = check_assoc_resp.json().get("faces", [])
                face_ids = [f.get("id") for f in faces_data]
                assert face_id in face_ids, (
                    f"Face ID {face_id} not found in Esmeralda Vault character association: {face_ids}"
                )
                logging.debug(
                    f"Verified Esmeralda Vault character association for face {face_id}"
                )

            # Call /characters/summary and check count
            summary_resp = client.get(f"/characters/{str(esmeralda_id)}/summary")
            assert summary_resp.status_code == 200
            summary = summary_resp.json()
            # Accept dict or list, but check count
            if isinstance(summary, dict):
                count = summary.get("image_count")
            elif isinstance(summary, list):
                count = len(summary)
            else:
                count = None
            assert count is not None and count >= len(picture_ids), (
                f"Expected at least {len(picture_ids)} pictures for Esmeralda Vault, got {count}"
            )
    gc.collect()
    log_resources("END test_characters_summary")


def test_pictures_stacks():
    """Test /pictures/stacks endpoint returns 200 and valid structure."""
    log_resources("START test_pictures_stacks")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200
            resp = client.get("/pictures/stacks")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
    gc.collect()
    log_resources("END test_pictures_stacks")


def test_pictures_stacks_supports_set_intersection_filter():
    """/pictures/stacks should support repeated set_ids with intersection mode."""

    log_resources("START test_pictures_stacks_supports_set_intersection_filter")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            login_resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert login_resp.status_code == 200

            imported_a = upload_pictures_and_wait(
                client,
                [
                    (
                        "file",
                        (
                            "set-intersection-stack-a.png",
                            random_images[10],
                            "image/png",
                        ),
                    )
                ],
            )
            imported_b = upload_pictures_and_wait(
                client,
                [
                    (
                        "file",
                        (
                            "set-intersection-stack-b.png",
                            random_images[11],
                            "image/png",
                        ),
                    )
                ],
            )
            imported_c = upload_pictures_and_wait(
                client,
                [
                    (
                        "file",
                        (
                            "set-intersection-stack-c.png",
                            random_images[12],
                            "image/png",
                        ),
                    )
                ],
            )

            pic_a = imported_a["results"][0]["picture_id"]
            pic_b = imported_b["results"][0]["picture_id"]
            pic_c = imported_c["results"][0]["picture_id"]

            set_a_resp = client.post("/picture_sets", json={"name": "Stack Set A"})
            set_b_resp = client.post("/picture_sets", json={"name": "Stack Set B"})
            assert set_a_resp.status_code == 200
            assert set_b_resp.status_code == 200
            set_a_id = (set_a_resp.json().get("picture_set") or {}).get("id")
            set_b_id = (set_b_resp.json().get("picture_set") or {}).get("id")
            assert set_a_id is not None
            assert set_b_id is not None

            # /pictures/stacks groups are built from likeness edges, so seed a chain.
            def seed_likeness_edges(session, a: int, b: int, c: int):
                ab_a, ab_b = sorted((a, b))
                bc_a, bc_b = sorted((b, c))
                session.add(
                    PictureLikeness(
                        picture_id_a=ab_a,
                        picture_id_b=ab_b,
                        likeness=0.95,
                        metric="clip",
                    )
                )
                session.add(
                    PictureLikeness(
                        picture_id_a=bc_a,
                        picture_id_b=bc_b,
                        likeness=0.95,
                        metric="clip",
                    )
                )
                session.commit()

            server.vault.db.run_task(seed_likeness_edges, pic_a, pic_b, pic_c)

            assert (
                client.post(f"/picture_sets/{set_a_id}/members/{pic_b}").status_code
                == 200
            )
            assert (
                client.post(f"/picture_sets/{set_a_id}/members/{pic_c}").status_code
                == 200
            )
            assert (
                client.post(f"/picture_sets/{set_b_id}/members/{pic_b}").status_code
                == 200
            )
            assert (
                client.post(f"/picture_sets/{set_b_id}/members/{pic_c}").status_code
                == 200
            )

            union_resp = client.get(
                "/pictures/stacks",
                params=[("set_ids", str(set_a_id)), ("set_ids", str(set_b_id))],
            )
            assert union_resp.status_code == 200
            union_ids = {item.get("id") for item in union_resp.json()}
            assert pic_b in union_ids
            assert pic_c in union_ids

            intersection_resp = client.get(
                "/pictures/stacks",
                params=[
                    ("set_ids", str(set_a_id)),
                    ("set_ids", str(set_b_id)),
                    ("set_mode", "intersection"),
                    ("min_group_size", 1),
                ],
            )
            assert intersection_resp.status_code == 200
            intersection_ids = {item.get("id") for item in intersection_resp.json()}
            assert pic_b in intersection_ids
            assert pic_c in intersection_ids
            assert pic_a not in intersection_ids

    gc.collect()
    log_resources("END test_pictures_stacks_supports_set_intersection_filter")


def test_pictures_thumbnails():
    """Test /pictures/thumbnails endpoint returns 200 and valid structure."""
    log_resources("START test_pictures_thumbnails")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200
            # Send empty payload for basic test
            resp = client.post("/pictures/thumbnails", json={"ids": []})
            assert resp.status_code == 200
            assert isinstance(resp.json(), dict)
    gc.collect()
    log_resources("END test_pictures_thumbnails")


def test_pictures_export():
    """Test /pictures/export endpoint returns 200 and zip content."""
    import zipfile

    log_resources("START test_pictures_export")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path) as server:
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
                status_resp = client.get(
                    "/pictures/export/status", params={"task_id": task_id}
                )
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
                "Exported pictures zip size: {} bytes".format(
                    len(download_resp.content)
                )
            )

            # Extract zip and compare SHA, file size, format, width, height
            with zipfile.ZipFile(BytesIO(download_resp.content)) as zf:
                zip_names = set(zf.namelist())
                image_names = [
                    name for name in zip_names if not name.lower().endswith(".txt")
                ]
                # Get expected metadata from the database
                pictures = server.vault.db.run_task(Picture.find)

                assert len(pictures) == len(image_names), (
                    f"Expected {len(pictures)} pictures in export, found {len(image_names)} in zip"
                )
                logger.info("Found {} images in export zip".format(len(image_names)))
                for fname in image_names:
                    found = False
                    data = None
                    with zf.open(fname) as f:
                        data = f.read()
                        sha = ImageUtils.calculate_hash_from_bytes(data)

                    # For file in the zip find a matching picture by SHA
                    for pic in pictures:
                        if sha == pic.pixel_sha:
                            found = True
                            # Compare file size
                            assert len(data) == pic.size_bytes, (
                                f"Size mismatch for {fname}: {len(data)} != {pic.size_bytes}"
                            )
                            # Compare format, width, height
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
    gc.collect()
    log_resources("END test_pictures_export")


def test_post_logo_identical_upload():
    log_resources("START test_post_logo_identical_upload")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            server.vault.import_default_data()
            client = TestClient(server.api)

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            logo_path = os.path.join(os.path.dirname(__file__), "../Logo.png")
            with open(logo_path, "rb") as f:
                img_bytes = f.read()
                files = [("file", ("identical_logo.png", img_bytes, "image/png"))]
            import_status = upload_pictures_and_wait(client, files)
            assert import_status["status"] == "completed"
            assert import_status["results"][0]["status"] == "duplicate"
    gc.collect()
    log_resources("END test_post_logo_identical_upload")


def test_post_logo_altered_pixel_upload():
    log_resources("START test_post_logo_altered_pixel_upload")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            logo_path = os.path.join(os.path.dirname(__file__), "../Logo.png")
            img = Image.open(logo_path).convert("RGBA")
            arr = np.array(img)
            arr[0, 0] = [255, 0, 0, 255]  # Red pixel
            altered_img = Image.fromarray(arr)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                altered_img.save(tmp.name)
                tmp_path = tmp.name
            img_bytes = None
            with open(tmp_path, "rb") as f:
                img_bytes = f.read()
            files = [("file", ("altered_logo.png", img_bytes, "image/png"))]
            import_status = upload_pictures_and_wait(client, files)
            assert import_status["status"] == "completed"
            assert import_status["results"][0]["status"] == "success"
            assert import_status["results"][0]["picture_id"]
            os.remove(tmp_path)
    gc.collect()
    log_resources("END test_post_logo_altered_pixel_upload")


def test_read_version():
    log_resources("START test_read_version")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            response = client.get("/version")
            assert response.status_code == 200
            expected_version = get_project_version()
            assert response.json() == {
                "message": "PixlStash REST API",
                "version": expected_version,
            }
    gc.collect()
    log_resources("END test_read_version")


def test_version_latest_no_version_fetched():
    """GET /version/latest returns nulls when no PyPI fetch has been done."""
    log_resources("START test_version_latest_no_version_fetched")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            # _latest_version starts as None (no background fetch in tests)
            assert server._latest_version is None
            client = TestClient(server.api)
            response = client.get("/version/latest")
            assert response.status_code == 200
            assert response.json() == {"latest_version": None, "release_url": None}
    gc.collect()
    log_resources("END test_version_latest_no_version_fetched")


def test_version_latest_with_newer_version():
    """GET /version/latest returns version and release URL when a newer version is available."""
    log_resources("START test_version_latest_with_newer_version")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            server._latest_version = "99.0.0"
            client = TestClient(server.api)
            response = client.get("/version/latest")
            assert response.status_code == 200
            data = response.json()
            assert data["latest_version"] == "99.0.0"
            assert data["release_url"] == (
                "https://pikselkroken.github.io/pixlstash/upgrade"
            )
    gc.collect()
    log_resources("END test_version_latest_with_newer_version")


def test_benchmark_add_images_by_binary_upload():
    log_resources("START test_benchmark_add_images_by_binary_upload")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            start = time.time()
            ids = []
            files = []
            for i, img_bytes in enumerate(random_images):
                file = ("file", (f"image_{i:04d}.png", img_bytes, "image/png"))
                files.append(file)

            import_status = upload_pictures_and_wait(client, files, timeout_s=60)
            end = time.time()

            assert import_status["status"] == "completed"
            assert len(import_status["results"]) == TEST_SIZE
            for result in import_status["results"]:
                assert result["status"] == "success"
                ids.append(result["picture_id"])

            print(
                f"Upload Benchmark: Added {TEST_SIZE} images in {end - start:.2f} seconds or {total_bytes / (end - start) / 1024 / 1024:.2f} MB/s"
            )

            # Read back and check a few images
            random_indices = random.sample(range(TEST_SIZE), 3)
            for check_idx in random_indices:
                pic_id = ids[check_idx]
                img_resp = client.get(f"/pictures/{pic_id}.png")
                assert img_resp.status_code == 200
                assert img_resp.content[:1024] == random_images[check_idx][:1024]
    gc.collect()
    log_resources("END test_benchmark_add_images_by_binary_upload")


def test_semantic_search(request):
    """Test: Add all images from pictures folder, wait for tagging, perform semantic search, print results, assert count."""

    log_resources("START test_semantic_search")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")

        src_dir = os.path.join(os.path.dirname(__file__), "../pictures")
        image_files = [
            f
            for f in os.listdir(src_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]

        with Server(server_config_path=server_config_path) as server:
            server.vault.import_default_data()
            client = TestClient(server.api)

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            # Get Esmeralda's character ID
            resp = client.get("/characters")
            assert resp.status_code == 200
            chars = resp.json()
            esmeralda_id = None
            barbara_id = None
            barry_id = None
            cassandra_id = None
            for c in chars:
                if c.get("name") == "Esmeralda Vault":
                    esmeralda_id = c["id"]
                elif c.get("name") == "Barbara Vault":
                    barbara_id = c["id"]
                elif c.get("name") == "Barry Vault":
                    barry_id = c["id"]
                elif c.get("name") == "Cassandra Vault":
                    cassandra_id = c["id"]

            assert esmeralda_id is not None, "Esmeralda Vault character not found"
            assert barbara_id is not None, "Barbara Vault character not found"
            assert barry_id is not None, "Barry Vault character not found"
            assert cassandra_id is not None, "Cassandra Vault character not found"

            # Upload all images as new pictures
            picture_ids = []
            embeddings_futures = []
            for fname in image_files:
                with open(os.path.join(src_dir, fname), "rb") as f:
                    files = [("file", (fname, f.read(), "image/png"))]
                    import_status = upload_pictures_and_wait(client, files)
                assert import_status["status"] == "completed"
                assert import_status["results"][0]["status"] == "success"
                picture_ids.append(import_status["results"][0]["picture_id"])
                embeddings_futures.append(
                    server.vault.get_worker_future(
                        TaskType.TEXT_EMBEDDING,
                        Picture,
                        picture_ids[-1],
                        "text_embedding",
                    )
                )

            tag_futures = [
                server.vault.get_worker_future(
                    TaskType.TAGGER,
                    Picture,
                    pic_id,
                    "tags",
                )
                for pic_id in picture_ids
            ]
            description_futures = [
                server.vault.get_worker_future(
                    TaskType.DESCRIPTION,
                    Picture,
                    pic_id,
                    "description",
                )
                for pic_id in picture_ids
            ]

            def wait_for_imported_at(timeout_s=60, poll_interval=0.5):
                start = time.time()
                pending = set(picture_ids)
                while pending and (time.time() - start) < timeout_s:
                    completed = set()
                    for pid in pending:
                        meta_resp = client.get(f"/pictures/{pid}/metadata")
                        if meta_resp.status_code != 200:
                            continue
                        meta = meta_resp.json()
                        if meta.get("imported_at"):
                            completed.add(pid)
                    pending -= completed
                    if pending:
                        time.sleep(poll_interval)
                assert not pending, (
                    "Timed out waiting for imported_at for picture ids: "
                    f"{sorted(pending)}"
                )

            wait_for_imported_at()

            # Wait for facial features to be processed and associate Esmeralda Vault with largest face in each picture
            picture_ids_with_chars: set[int] = set()
            for pid in picture_ids:
                # Fetch faces for this picture — poll because face extraction is async
                faces_data = wait_for_faces(client, pid, timeout_s=60)
                logging.debug(f"Received face data for picture ID {pid}: {faces_data}")
                logging.debug(f"Picture ID {pid} has {len(faces_data)} faces detected")
                if not faces_data:
                    continue  # No faces detected

                # Order faces left to right
                faces_ordered = sorted(
                    faces_data, key=lambda f: f.get("bbox", [0, 0, 0, 0])[0]
                )
                if len(faces_ordered) == 1:
                    face_id = faces_ordered[0].get("id")
                    assert face_id is not None, (
                        f"No face id found for largest face in picture {pid}"
                    )
                    # Associate Esmeralda Vault with this face
                    assoc_resp = client.post(
                        f"/characters/{esmeralda_id}/faces",
                        json={"face_ids": [face_id]},
                    )
                    assert assoc_resp.status_code == 200, (
                        f"Failed to associate face {face_id} with Esmeralda Vault: {assoc_resp.text}"
                    )
                    assoc_data = assoc_resp.json()
                    assert assoc_data["status"] == "success"
                    logging.debug(
                        f"Associated face ID {face_id} in picture {pid} with Esmeralda Vault character ID {esmeralda_id}"
                    )

                    # Query the character-face association to verify
                    check_assoc_resp = client.get(f"/characters/{esmeralda_id}/faces")
                    assert check_assoc_resp.status_code == 200, (
                        f"Failed to fetch faces for character {esmeralda_id} after association due to {check_assoc_resp.text}"
                    )
                    faces_data = check_assoc_resp.json().get("faces", [])
                    assert len(faces_data) > 0, (
                        f"No faces found for character {esmeralda_id} after association"
                    )
                    face_ids = [f.get("id") for f in faces_data]
                    assert face_id in face_ids, (
                        f"Face ID {face_id} not found in Esmeralda Vault character association: {face_ids} and {faces_data}"
                    )
                    logging.debug(
                        f"Verified Esmeralda Vault character association for face {face_id}"
                    )
                    picture_ids_with_chars.add(pid)
                elif len(faces_ordered) >= 3:
                    # Associate Barbara, Barry, Cassandra with left, center, right faces
                    face_ids = [
                        faces_ordered[0].get("id"),
                        faces_ordered[len(faces_ordered) // 2].get("id"),
                        faces_ordered[-1].get("id"),
                    ]
                    char_ids = [barbara_id, barry_id, cassandra_id]
                    for face_id, char_id in zip(face_ids, char_ids):
                        assert face_id is not None, (
                            f"No face id found for face in picture {pid} for character {char_id}"
                        )
                        assoc_resp = client.post(
                            f"/characters/{char_id}/faces",
                            json={"face_ids": [face_id]},
                        )
                        assert assoc_resp.status_code == 200, (
                            f"Failed to associate face {face_id} with character {char_id}: {assoc_resp.text}"
                        )
                        assoc_data = assoc_resp.json()
                        assert assoc_data["status"] == "success"
                        logging.debug(
                            f"Associated face ID {face_id} in picture {pid} with character ID {char_id}"
                        )
                    picture_ids_with_chars.add(pid)

            # Assert that character associations persisted in the DB.
            for pid in picture_ids_with_chars:
                faces_check_resp = client.get(f"/pictures/{pid}/faces")
                assert faces_check_resp.status_code == 200, (
                    f"Failed to fetch faces for picture {pid} after character association"
                )
                faces_check = faces_check_resp.json().get("faces", [])
                assigned = [f for f in faces_check if f.get("character_id") is not None]
                assert assigned, (
                    f"Picture {pid} has no faces with character_id after association — association did not persist"
                )

            # Replace embedding futures: the originals may have resolved before
            # character association (and clear_field) ran, so they could be stale.
            # We need fresh futures that will only resolve after the re-embedding.
            embeddings_futures = [
                server.vault.get_worker_future(
                    TaskType.TEXT_EMBEDDING,
                    Picture,
                    pid,
                    "text_embedding",
                )
                for pid in picture_ids
            ]

            for future in description_futures:
                future.result(timeout=120)

            for future in tag_futures:
                future.result(timeout=120)

            # Wait for all text embeddings to be processed (futures refreshed post-association)
            for future in embeddings_futures:
                result_id = future.result(timeout=80)
                logging.debug(f"Text embedding processed for picture ID: {result_id}")

            def wait_for_semantic_ready(timeout_s=80, poll_interval=0.5):
                start = time.time()
                pending = set(picture_ids)
                while pending and (time.time() - start) < timeout_s:
                    completed = set()
                    for pid in pending:
                        meta_resp = client.get(f"/pictures/{pid}/metadata")
                        if meta_resp.status_code != 200:
                            continue
                        meta = meta_resp.json()
                        if not meta.get("description"):
                            continue
                        embed_resp = client.get(f"/pictures/{pid}/text_embedding")
                        if embed_resp.status_code != 200:
                            continue
                        if embed_resp.json().get("text_embedding") is None:
                            continue
                        completed.add(pid)
                    pending -= completed
                    if pending:
                        time.sleep(poll_interval)
                assert not pending, (
                    f"Timed out waiting for semantic readiness for picture ids: {sorted(pending)}"
                )

            wait_for_semantic_ready()

            # Inspect embeddings for each picture after embedding futures complete
            for pid in picture_ids:
                meta_resp = client.get(f"/pictures/{pid}/text_embedding")
                assert meta_resp.status_code == 200
                meta = meta_resp.json()
                embedding_b64 = meta.get("text_embedding")
                if embedding_b64:
                    import base64
                    import numpy as np

                    emb_bytes = base64.b64decode(embedding_b64)
                    emb = np.frombuffer(emb_bytes, dtype=np.float32)
                    print(
                        f"Picture {pid} embedding: shape={emb.shape}, norm={np.linalg.norm(emb):.4f}, sample={emb[:5]}"
                    )
                else:
                    print(f"Picture {pid} has no embedding!")

            # Perform semantic search
            search_texts = [
                "It was a bright rainy day but Esmeralda needed to get out and get some fresh air, so she dressed for the weather, brought an umbrella and walked out into the countryside.",
                "Esmeralda smiles as she sits across me in the cafe wearing her grey sweater. The sunlight filters through the window of the empty cafe",
                "It was a bright winter morning, and Esmeralda decided to go for a walk in the woods. The snow had fallen the night before, and she enjoyed the glistening trees and the crisp air. She was glad to have her scarf and her warm coat to keep her cozy.",
                "Esmeralda spent hours in her garden tending to her grass and bushes wearing her dungarees. The greenery made her smile. Especially when the sky was blue",
                "Do I look like a man? Esmeralda asked, raising an eyebrow as she posed with her grey business suit, complete with shirt, jacket and tie.",
                "Esmeralda sat down on the wooden park bench and considered her predicament. She was in serious trouble.",
            ]

            query_rows = []

            for search_text in search_texts:
                search_resp = client.get(
                    f"/pictures/search?query={quote(search_text)}&threshold=0.4"
                )
                assert search_resp.status_code == 200
                results = search_resp.json()

                assert 1 <= len(results), (
                    f"Expected at least one results, got {len(results)} for the text '{search_text}'"
                )
                print("===== Semantic Search Result =====")
                print(f"Search text:\n{search_text}\n")
                print(f"Number of results: {len(results)}\n")
                for r in results:
                    print(f"Match: {r['description']}")
                    print(f"Similarity: {r['likeness_score']:.4f}.")

                query_rows.append(
                    {
                        "query": search_text,
                        "top_score": round(float(results[0]["likeness_score"]), 4),
                        "top_description": results[0].get("description", ""),
                    }
                )

            summary = {
                "total_queries": len(query_rows),
                "avg_top_score": round(
                    float(
                        sum(row["top_score"] for row in query_rows)
                        / max(1, len(query_rows))
                    ),
                    4,
                ),
                "min_top_score": round(
                    float(min(row["top_score"] for row in query_rows)), 4
                ),
            }

            device_tag = "cpu" if PictureTagger.FORCE_CPU else "gpu"
            regression_payload = {
                "meta": {
                    "device": device_tag,
                    "query_threshold": 0.4,
                    "schema_version": 1,
                },
                "summary": summary,
                "queries": query_rows,
            }

            regression_path = _REGRESSION_DIR / f"semantic_search_{device_tag}.json"

            if request.config.getoption("--fast-captions", default=False):
                logger.info(
                    "Skipping semantic search regression comparison: --fast-captions produces truncated "
                    "descriptions that differ from the full-caption baseline."
                )
            else:
                _check_semantic_search_regression(
                    regression_path, regression_payload, device_tag
                )
    gc.collect()
    log_resources("END test_semantic_search")
