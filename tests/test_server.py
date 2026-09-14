"""Worker-heavy REST tests: upload, stacking, sets, projects, search.

These tests each used to build their own ``Server`` inside a
``tempfile.TemporaryDirectory``. Booting a Server (migrations, vault start-up,
route registration) plus minting credentials costs ~1.8 s per test and buys
nothing here: no test in this module asserts anything about server construction
or authentication. The module therefore shares one ``server`` and one logged-in
``client`` and resets the *library* between tests (``clean_library``), the same
trade already made in :mod:`tests.test_server_simple` and
:mod:`tests.test_authentication`.

Module-scoped, not class-scoped: the gate shards individual tests
(``--ci-shard``, tests/conftest.py), so a narrower scope would be rebuilt once
per shard per group and give most of the saving back.

``clean_library`` is autouse and is also the integrity check for the sharing.
It runs before *every* test rather than as a trailing "runs last" canary
precisely because the sharder splits this module across shards, so no single
test is guaranteed to run after the ones it would be watching.
"""

import numpy as np
import asyncio
import contextlib
import logging
import os
import json
import random
import shutil
import sys
import tempfile
import threading
import time
import tomllib
import types
from types import SimpleNamespace
import zipfile

import gc
import psutil
import tracemalloc
import collections

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from io import BytesIO
from pathlib import Path
from sqlalchemy import text
from sqlmodel import Session, delete, select, update
from urllib.parse import quote

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
)
import pixlstash.image_plugins.service as image_plugin_service
import pixlstash.routes.pictures as pictures_routes
from pixlstash.image_plugins.base import ImagePlugin
from pixlstash.inference.cpu_query_encoders import CpuQueryEncoders
from pixlstash.inference.errors import (
    NO_GPU_WORKER_DETAIL,
    CpuQueryEncodersNotReadyError,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.routes.pictures._anomaly import clear_anomaly_region_cache
from pixlstash.task_runner import (
    TaskCancelledError,
    TaskRunner,
    TaskRunnerNotRunningError,
)
from pixlstash.tasks.gpu_call_task import GpuCallTask
from pixlstash.tasks.tag_task import TagTask
from pixlstash.tasks.task_type import TaskType
from pixlstash.server import Server
from pixlstash.services import plugin_service
from pixlstash.utils.service.export_utils import ExportUtils
from starlette.datastructures import QueryParams
from tests.utils import seed_likeness_stable, upload_pictures_and_wait, wait_for_faces

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


# Library tables emptied before every test. Child rows before parent rows so
# FK constraints are satisfied even with enforcement on. ``User``/``UserToken``
# are deliberately absent: the module shares one logged-in client, and wiping
# identity would cost a ~0.45 s credential re-mint per test for no benefit -
# nothing here asserts anything about authentication.
_LIBRARY_TABLES = [
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
]

_DB_BASENAMES = {"vault.db", "vault.db-wal", "vault.db-shm", "vault.db-journal"}


@pytest.fixture(scope="module")
def server():
    """One Server for the whole module.

    Construction (DB migrations, vault start-up, route registration) is the
    single largest per-test cost in this file and none of the tests below
    assert anything about it, so it is paid once. ``clean_library`` restores a
    pristine library between tests.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as srv:
            yield srv


@pytest.fixture(scope="module")
def client(server):
    """One logged-in client for the whole module.

    The first login also *sets* the password, exactly as it did per test
    before. Both the hash on set and the hash on verify are deliberately slow
    (~0.15 s each), so the session is established once and reused; the identity
    tables are excluded from ``clean_library`` to keep it valid. The session is
    re-proven before every test - see ``clean_library``.
    """
    api_client = TestClient(server.api)
    response = api_client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert response.status_code == 200, response.text
    return api_client


def _reset_pipeline_bookkeeping(vault) -> None:
    """Drop every in-memory record of work that the stopped schedulers held.

    Both structures below are drained on the *completion* path of a task, and a
    task cancelled by ``TaskRunner.stop`` never gets there. Left behind, they
    are not merely untidy - emptying ``picture`` makes SQLite hand out ids from
    1 again, so stale entries name the *next* test's pictures:

    * ``WorkPlanner._inflight_by_finder`` keeps a count that never returns to
      zero, so a finder sitting at its in-flight cap stops submitting and any
      finder that ``depends_on`` it never runs at all;
    * ``BaseTaskFinder._claimed_picture_ids`` keeps ids claimed forever, and
      ``_filter_and_claim`` then skips exactly the pictures the next test just
      uploaded - they never get an embedding, likeness parameters or faces, and
      a test waiting on the pipeline waits until its timeout.

    Both were observed: leaving them behind cost two tests a 180 s
    ``likeness pipeline did not settle`` timeout and lost a third one picture's
    faces. ``UnprocessableImageRegistry`` needs no help - it re-stats the file
    it recorded, and the reset deleted it, so a recycled id prunes itself.
    """
    planner = vault._work_planner
    if planner is not None:
        with planner._lock:
            planner._inflight_by_finder.clear()
            planner._finder_by_task_id.clear()
            planner._finder_exhausted.clear()
    for finder in (vault._planner_work_finders or {}).values():
        claim_lock = getattr(finder, "_claim_lock", None)
        if claim_lock is None:
            continue
        with claim_lock:
            finder._claimed_picture_ids.clear()


@pytest.fixture(autouse=True)
def clean_library(request, server, client):
    """Empty the shared library before each test, then prove it is empty.

    Restores what a fresh ``Server`` used to give each test:

    * the work planner and task runner are stopped, so no background task from
      the previous test is alive while the next one runs. That is not just
      tidiness: emptying ``picture`` makes SQLite reuse ids from 1, so a
      surviving face-extraction or quality task writes its result onto a
      *different* picture that happens to hold the id it captured. Stopping is
      exactly what ``Server.__exit__`` used to do between tests here;
    * every library table is emptied;
    * every file under ``image_root`` except the SQLite database is deleted, so
      disk state matches the database;
    * the scheduler's in-memory work bookkeeping is cleared before it restarts
      (``_reset_pipeline_bookkeeping``). A fresh Server started with all of it
      empty; a cancelled task never reaches the code that would drain it.

    The assertions afterwards are what make sharing safe to rely on, and they
    check *identity* rather than counts: an absent object and a refused request
    produce the same status code, so "the library is empty" and "the credential
    is live" have to be established separately. A test asserting
    ``pic_b not in ids`` would otherwise pass just as happily against a query
    that was denied or a wipe that silently did nothing.
    """
    vault = server.vault
    planner, runner = vault._work_planner, vault._task_runner
    if planner is not None:
        planner.stop()
    if runner is not None:
        runner.stop()

    def _wipe(session: Session):
        # ``picture.stack_id -> picturestack.id`` and ``picture.source_picture_id
        # -> picture.id`` are the references no delete order below satisfies, so
        # they are nulled first and the remaining tables delete children before
        # parents. That UPDATE is also what opens the transaction: pysqlite
        # emits BEGIN lazily on the first DML, and ``defer_foreign_keys`` only
        # holds for the transaction it is set in, so issued ahead of any
        # statement it runs in autocommit and is gone again by COMMIT (#822
        # measured it reading back 0). The pragma is therefore a safety net over
        # an order that already holds, and the assertion proves it engaged
        # rather than assuming it. Preferred over toggling ``foreign_keys`` off
        # and on: it is scoped to this transaction and resets itself, so a
        # delete that raises cannot leave enforcement disabled on a pooled
        # connection for the rest of the module. Removing the pragma from this
        # exact order was tried and stays green, which is what makes it a net
        # rather than the thing correctness rests on.
        session.exec(update(Picture).values(stack_id=None, source_picture_id=None))
        session.exec(text("PRAGMA defer_foreign_keys = ON"))
        assert session.exec(text("PRAGMA defer_foreign_keys")).one()[0] == 1, (
            "deferred FK enforcement did not engage; the deletes below would be "
            "order-sensitive without it"
        )
        for model in _LIBRARY_TABLES:
            session.exec(delete(model))
        session.commit()

    vault.db.run_task(_wipe)

    image_root = vault.image_root
    for entry in os.listdir(image_root):
        if entry in _DB_BASENAMES:
            continue
        path = os.path.join(image_root, entry)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError as exc:
                logger.warning(
                    f"Could not delete {path} during library reset: {exc}. "
                    "A stale file here can make a later import report 'duplicate'."
                )

    _reset_pipeline_bookkeeping(vault)
    if runner is not None:
        runner.start()
    if planner is not None:
        planner.start()

    protected = client.get("/protected")
    assert protected.status_code == 200, (
        f"the shared session is no longer authenticated ({protected.status_code}: "
        f"{protected.text}) - every assertion below would prove nothing"
    )
    for endpoint in ("/pictures", "/characters", "/picture_sets", "/projects"):
        remaining = client.get(endpoint)
        assert remaining.status_code == 200, remaining.text
        assert remaining.json() == [], (
            f"{endpoint} is not empty after the reset: {remaining.json()} - a "
            "previous test leaked state into this one"
        )

    log_resources(f"START {request.node.name}")
    yield
    gc.collect()
    log_resources(f"END {request.node.name}")


def test_upload_existing_picture(client):
    """Test uploading an existing picture."""
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


def test_duplicate_import_updates_project_context(client):
    """Duplicate imports should still apply project association context."""
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

    unrelated_metadata_resp = client.get(f"/pictures/{unrelated_picture_id}/metadata")
    assert unrelated_metadata_resp.status_code == 200
    assert unrelated_metadata_resp.json().get("project_id") is None


def test_set_project_for_existing_pictures_bulk(client):
    """Bulk project assignment endpoint should update only targeted pictures and support unassign."""
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


def test_set_project_reconciles_project_set_membership(client):
    """Adding another project membership should not remove existing set memberships."""
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

    project_a_resp = client.post("/projects", json={"name": "Membership Project A"})
    project_b_resp = client.post("/projects", json={"name": "Membership Project B"})
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


def test_unassigned_picture_query_respects_project_filter(client):
    """UNASSIGNED picture queries should honor project scope filters."""
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
    unassigned_project_ids = {item.get("id") for item in unassigned_project_resp.json()}
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


def test_unassigned_excludes_stack_when_any_member_is_in_set(client):
    """Unassigned should exclude the entire stack if any member is in a picture set."""
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

    unassigned_resp = client.get("/pictures", params={"character_id": "UNASSIGNED"})
    assert unassigned_resp.status_code == 200
    unassigned_ids = {item.get("id") for item in unassigned_resp.json()}
    assert pic_a not in unassigned_ids
    assert pic_b not in unassigned_ids

    summary_resp = client.get("/characters/UNASSIGNED/summary")
    assert summary_resp.status_code == 200
    assert summary_resp.json().get("image_count") == 0


def test_stacking_unions_project_membership_across_members(client):
    """Stack membership is atomic: stacking pictures from different projects
    unions their project memberships so every member belongs to every project
    the stack touches. The (unassigned) stack then appears in each project's
    UNASSIGNED grid, collapsed to a single leader, regardless of which member
    is the leader."""
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

    # Force pic_b to be the stack leader.
    reorder_resp = client.patch(
        f"/stacks/{stack_id}/order",
        json={"picture_ids": [pic_b, pic_a]},
    )
    assert reorder_resp.status_code == 200

    # Union: stacking pic_a (project A) with pic_b (project B) makes both
    # pictures belong to BOTH projects.
    def project_ids(project_id):
        resp = client.get("/pictures", params={"project_id": str(project_id)})
        assert resp.status_code == 200
        return {item.get("id") for item in resp.json()}

    assert {pic_a, pic_b} <= project_ids(project_a_id)
    assert {pic_a, pic_b} <= project_ids(project_b_id)

    # The unassigned (no character/set) stack appears in project A's
    # UNASSIGNED grid, collapsed to a single leader.
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
    assert len(grid_ids & {pic_a, pic_b}) == 1


def test_unassigned_project_scope_uses_project_character_and_set_membership_only(
    server, client
):
    """Project-scoped UNASSIGNED should ignore assignments from other projects."""
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

    created_face_id = server.vault.db.run_task(create_face, pic_id, character_id)
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
    global_unassigned_ids = {item.get("id") for item in global_unassigned_resp.json()}
    assert pic_id not in global_unassigned_ids

    # Project-A-scoped unassigned should include it because assignments are
    # only to project-B character/set.
    project_unassigned_resp = client.get(
        "/pictures",
        params={"character_id": "UNASSIGNED", "project_id": str(project_a_id)},
    )
    assert project_unassigned_resp.status_code == 200
    project_unassigned_ids = {item.get("id") for item in project_unassigned_resp.json()}
    assert pic_id in project_unassigned_ids

    summary_resp = client.get(
        "/characters/UNASSIGNED/summary",
        params={"project_id": str(project_a_id)},
    )
    assert summary_resp.status_code == 200
    assert summary_resp.json().get("image_count") == 1


def test_unassigned_project_scope_ignores_global_character_and_set_assignments(
    server, client
):
    """Project-scoped UNASSIGNED should include pictures assigned only to global groups."""
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

    project_resp = client.post("/projects", json={"name": "Project Scope Global Test"})
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
    global_character_id = (global_character_resp.json().get("character") or {}).get(
        "id"
    )
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

    created_face_id = server.vault.db.run_task(create_face, pic_id, global_character_id)
    assert created_face_id is not None

    global_set_resp = client.post(
        "/picture_sets",
        json={"name": "Global Set", "project_id": None},
    )
    assert global_set_resp.status_code == 200
    global_set_id = (global_set_resp.json().get("picture_set") or {}).get("id")
    assert global_set_id is not None

    add_member_resp = client.post(f"/picture_sets/{global_set_id}/members/{pic_id}")
    assert add_member_resp.status_code == 200

    project_unassigned_resp = client.get(
        "/pictures",
        params={"character_id": "UNASSIGNED", "project_id": str(project_id)},
    )
    assert project_unassigned_resp.status_code == 200
    project_unassigned_ids = {item.get("id") for item in project_unassigned_resp.json()}
    assert pic_id in project_unassigned_ids

    summary_resp = client.get(
        "/characters/UNASSIGNED/summary",
        params={"project_id": str(project_id)},
    )
    assert summary_resp.status_code == 200
    assert summary_resp.json().get("image_count") == 1


def test_project_scoped_picture_set_counts_only_include_project_pictures(
    server, client
):
    """Project-scoped set counts should include only members in that project."""
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

    project_a_resp = client.post("/projects", json={"name": "Set Count Project A"})
    project_b_resp = client.post("/projects", json={"name": "Set Count Project B"})
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
    scoped_set = next(s for s in scoped_sets_resp.json() if s.get("id") == set_id)
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


def test_pictures_endpoint_supports_set_intersection_filter(client):
    """/pictures should support set_ids + set_mode=intersection filters."""
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

    assert client.post(f"/picture_sets/{set_a_id}/members/{pic_a}").status_code == 200
    assert client.post(f"/picture_sets/{set_a_id}/members/{pic_b}").status_code == 200
    assert client.post(f"/picture_sets/{set_b_id}/members/{pic_b}").status_code == 200

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


def test_add_picture_to_project_set_aligns_picture_project_membership(client):
    """Adding to a project set should not overwrite existing project memberships."""
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

    project_a_resp = client.post("/projects", json={"name": "Set Align Project A"})
    project_b_resp = client.post("/projects", json={"name": "Set Align Project B"})
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


def test_all_picture_query_respects_project_filter(client):
    """ALL picture queries should honor project_id filters."""
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


def test_unassigned_project_filter_with_id_returns_matching_picture(client):
    """Regression: `?id=<pic>&project_id=UNASSIGNED` must return the matching
    unassigned picture, not an empty list.

    The `id` filter arrives as a string query param while the UNASSIGNED branch
    builds an int id list from `select(Picture.id)`; intersecting the two
    without normalising types silently yielded an empty set (`{'5'} & {5}`), so
    the endpoint returned nothing instead of the picture."""
    unassigned_import = upload_pictures_and_wait(
        client,
        [("file", ("id-unassigned.png", random_images[14], "image/png"))],
    )
    assigned_import = upload_pictures_and_wait(
        client,
        [("file", ("id-assigned.png", random_images[15], "image/png"))],
    )

    pic_unassigned = unassigned_import["results"][0]["picture_id"]
    pic_assigned = assigned_import["results"][0]["picture_id"]

    project_resp = client.post(
        "/projects",
        json={"name": "Id Unassigned Filter Project"},
    )
    assert project_resp.status_code == 200
    project_id = project_resp.json()["id"]

    set_resp = client.patch(
        "/pictures/project",
        json={"picture_ids": [pic_assigned], "project_id": project_id},
    )
    assert set_resp.status_code == 200

    # The core regression: filtering by the unassigned picture's own id
    # while scoping to UNASSIGNED must return it, not an empty list.
    resp = client.get(
        "/pictures",
        params={"id": str(pic_unassigned), "project_id": "UNASSIGNED"},
    )
    assert resp.status_code == 200
    returned_ids = {item.get("id") for item in resp.json()}
    assert pic_unassigned in returned_ids

    # An assigned picture's id under UNASSIGNED scope stays excluded.
    resp_assigned = client.get(
        "/pictures",
        params={"id": str(pic_assigned), "project_id": "UNASSIGNED"},
    )
    assert resp_assigned.status_code == 200
    assert pic_assigned not in {item.get("id") for item in resp_assigned.json()}


def test_stack_query_respects_project_filter(server, client):
    """Stack endpoint should honor project_id filters when building candidates."""
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

    # The background likeness pipeline both wipes pairs
    # (reset_likeness_for_pictures) and writes its own real ones, so seed
    # only once it is quiescent and on a wiped slate.
    seed_likeness_stable(server, seed_likeness)

    stacks_a_resp = client.get(
        "/pictures/likeness-groups",
        params={"threshold": 0.9, "project_id": str(project_a_id)},
    )
    assert stacks_a_resp.status_code == 200
    stacks_a_ids = {item.get("id") for item in stacks_a_resp.json()}
    assert pic_a1 in stacks_a_ids
    assert pic_a2 in stacks_a_ids
    assert pic_b1 not in stacks_a_ids
    assert pic_b2 not in stacks_a_ids

    stacks_b_resp = client.get(
        "/pictures/likeness-groups",
        params={"threshold": 0.9, "project_id": str(project_b_id)},
    )
    assert stacks_b_resp.status_code == 200
    stacks_b_ids = {item.get("id") for item in stacks_b_resp.json()}
    assert pic_b1 in stacks_b_ids
    assert pic_b2 in stacks_b_ids
    assert pic_a1 not in stacks_b_ids
    assert pic_a2 not in stacks_b_ids


def test_smart_score_query_respects_project_filter(client):
    """SMART_SCORE queries should honor project_id filters when selecting candidates."""
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


def test_character_likeness_query_respects_project_filter(client, monkeypatch):
    """CHARACTER_LIKENESS queries should honor project_id candidate scoping."""
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

    def fake_find_pictures_by_character_likeness_sql(
        _server,
        _character_id,
        _reference_character_id,
        _offset,
        _limit,
        _descending,
        candidate_ids=None,
        deleted_only=False,
        stack_leaders_only=False,
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
        pictures_routes._listing,
        "find_pictures_by_character_likeness_sql",
        fake_find_pictures_by_character_likeness_sql,
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


def test_import_sidecar_txt_tags_for_matching_image(client):
    """Import should apply matching sidecar .txt tags and ignore orphan .txt files."""
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


def test_import_zip_sidecar_txt_tags_for_matching_image(client):
    """Zip import should apply matching sidecar .txt tags and ignore orphan .txt files."""
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


def test_duplicate_import_with_sidecar_replaces_existing_tags(server, client):
    """Duplicate import with sidecar captions should replace existing tags atomically."""
    # First import creates a picture.
    first_files = [("file", ("replace_tags.png", random_images[2], "image/png"))]
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
    # A tag task that read the picture before the sidecar replaced its tags.
    stale = TagTask(
        database=server.vault.db,
        tagging_workflow=None,
        pictures=[SimpleNamespace(id=picture_id)],
    )

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
    # #1361: the replacement is a reset, so the older task's write is dropped.
    assert (
        server.vault.db.run_task(
            stale._add_tags_unless_reset,
            [{"pic_id": picture_id, "tags": ["stale-model-tag"]}],
        )
        == []
    )


def test_characters_summary(server, client):
    """Test /characters/summary endpoint returns 200 and valid structure."""
    src_dir = os.path.join(os.path.dirname(__file__), "../pictures")
    image_files = [
        f
        for f in os.listdir(src_dir)
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    ]

    server.vault.import_default_data()

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


def test_pictures_likeness_groups_supports_set_intersection_filter(server, client):
    """/pictures/likeness-groups should support repeated set_ids with intersection mode."""
    imported_a = upload_pictures_and_wait(
        client,
        [
            (
                "file",
                (
                    "set-intersection-a.png",
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
                    "set-intersection-b.png",
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
                    "set-intersection-c.png",
                    random_images[12],
                    "image/png",
                ),
            )
        ],
    )

    pic_a = imported_a["results"][0]["picture_id"]
    pic_b = imported_b["results"][0]["picture_id"]
    pic_c = imported_c["results"][0]["picture_id"]

    set_a_resp = client.post("/picture_sets", json={"name": "Set A"})
    set_b_resp = client.post("/picture_sets", json={"name": "Set B"})
    assert set_a_resp.status_code == 200
    assert set_b_resp.status_code == 200
    set_a_id = (set_a_resp.json().get("picture_set") or {}).get("id")
    set_b_id = (set_b_resp.json().get("picture_set") or {}).get("id")
    assert set_a_id is not None
    assert set_b_id is not None

    # /pictures/likeness-groups groups are built from likeness edges, so seed a chain.
    def seed_likeness_edges(session):
        ab_a, ab_b = sorted((pic_a, pic_b))
        bc_a, bc_b = sorted((pic_b, pic_c))
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

    # The background likeness pipeline both wipes pairs
    # (reset_likeness_for_pictures) and writes its own real ones, so seed
    # only once it is quiescent and on a wiped slate.
    seed_likeness_stable(server, seed_likeness_edges)

    assert client.post(f"/picture_sets/{set_a_id}/members/{pic_b}").status_code == 200
    assert client.post(f"/picture_sets/{set_a_id}/members/{pic_c}").status_code == 200
    assert client.post(f"/picture_sets/{set_b_id}/members/{pic_b}").status_code == 200
    assert client.post(f"/picture_sets/{set_b_id}/members/{pic_c}").status_code == 200

    union_resp = client.get(
        "/pictures/likeness-groups",
        params=[("set_ids", str(set_a_id)), ("set_ids", str(set_b_id))],
    )
    assert union_resp.status_code == 200
    union_ids = {item.get("id") for item in union_resp.json()}
    assert pic_b in union_ids
    assert pic_c in union_ids

    intersection_resp = client.get(
        "/pictures/likeness-groups",
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


def test_post_logo_identical_upload(server, client):
    server.vault.import_default_data()

    logo_path = os.path.join(os.path.dirname(__file__), "../Logo.png")
    with open(logo_path, "rb") as f:
        img_bytes = f.read()
        files = [("file", ("identical_logo.png", img_bytes, "image/png"))]
    import_status = upload_pictures_and_wait(client, files)
    assert import_status["status"] == "completed"
    assert import_status["results"][0]["status"] == "duplicate"


def test_post_logo_altered_pixel_upload(client):
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


def test_benchmark_add_images_by_binary_upload(client):
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


def test_semantic_search(server, client, request):
    """Test: Add all images from pictures folder, wait for tagging, perform semantic search, print results, assert count."""
    src_dir = os.path.join(os.path.dirname(__file__), "../pictures")
    image_files = [
        f
        for f in os.listdir(src_dir)
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    ]

    server.vault.import_default_data()

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
            f"Timed out waiting for imported_at for picture ids: {sorted(pending)}"
        )

    wait_for_imported_at()

    # Wait for facial features to be processed and associate Esmeralda Vault with largest face in each picture
    picture_ids_with_chars: set[int] = set()
    for pid in picture_ids:
        # Fetch faces for this picture - poll because face extraction is async
        faces_data = wait_for_faces(client, pid, timeout_s=60)
        logging.debug(f"Received face data for picture ID {pid}: {faces_data}")
        logging.debug(f"Picture ID {pid} has {len(faces_data)} faces detected")
        if not faces_data:
            continue  # No faces detected

        # Order faces left to right
        faces_ordered = sorted(faces_data, key=lambda f: f.get("bbox", [0, 0, 0, 0])[0])
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
            f"Picture {pid} has no faces with character_id after association - association did not persist"
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
                sum(row["top_score"] for row in query_rows) / max(1, len(query_rows))
            ),
            4,
        ),
        "min_top_score": round(float(min(row["top_score"] for row in query_rows)), 4),
    }

    device_tag = "cpu" if Server.DEFAULT_FORCE_CPU else "gpu"
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


def _record_query_encoding(server, monkeypatch):
    """Replace the query encoders and ``Picture.semantic_search`` with spies.

    Each fake encoder records the text it was given and the thread it ran on,
    and returns an array of its own, so a test can check both where the encode
    happened and that its result is what reached ``semantic_search``.

    Returns:
        ``(encodes, searches, writer)``: ``encodes`` collects
        ``(encoder, text, thread, embedding)`` per encode, ``searches`` the
        keyword arguments of each ``semantic_search`` call, and ``writer`` is
        the database writer thread no encode may run on.
    """
    writer = server.vault.db._task_worker
    assert writer is not None and writer.is_alive(), "no live DB writer thread"
    encodes = []
    searches = []

    def fake_text_embedding(text):
        embedding = np.full(384, 0.5, dtype=np.float32)
        encodes.append(("sbert", text, threading.current_thread(), embedding))
        return embedding

    def fake_clip_text_embedding(text):
        embedding = np.full(512, 0.25, dtype=np.float32)
        encodes.append(("clip", text, threading.current_thread(), embedding))
        return embedding

    real_semantic_search = Picture.semantic_search

    def spy_semantic_search(cls, session, *args, **kwargs):
        searches.append(kwargs)
        return real_semantic_search(session, *args, **kwargs)

    monkeypatch.setattr(server.vault, "generate_text_embedding", fake_text_embedding)
    monkeypatch.setattr(
        server.vault, "generate_clip_text_embedding", fake_clip_text_embedding
    )
    monkeypatch.setattr(Picture, "semantic_search", classmethod(spy_semantic_search))

    # Positive control for the thread identity: an encode made inside a
    # database task records ``writer``, so the ``is not writer`` checks in the
    # tests can fail.
    server.vault.db.run_task(
        lambda session: server.vault.generate_text_embedding("control")
    )
    _, _, control_thread, _ = encodes.pop()
    assert control_thread is writer, (
        f"an encode inside a database task ran on {control_thread!r}, not on "
        f"the DB writer {writer!r}"
    )
    return encodes, searches, writer


def test_text_search_encodes_the_query_off_the_db_writer_thread(
    server, client, monkeypatch
):
    """``GET /pictures/search`` encodes its query before queuing the search.

    The search runs on the single DB writer thread. An encode there blocks
    every database write in the app for the length of a model call.
    """
    encodes, searches, writer = _record_query_encoding(server, monkeypatch)

    resp = client.get(
        "/pictures/search", params={"query": "a red bicycle", "threshold": 0.0}
    )
    assert resp.status_code == 200, resp.text

    assert [(name, text) for name, text, _, _ in encodes] == [
        ("sbert", "a red bicycle"),
        ("clip", "a red bicycle"),
    ], "search must encode the query once per encoder, SBERT first"
    for name, _, thread, _ in encodes:
        assert thread is not writer, (
            f"the {name} query encode ran on the DB writer thread"
        )
    assert len(searches) == 1, searches
    assert searches[0]["query_embedding"] is encodes[0][3]
    assert searches[0]["clip_query_embedding"] is encodes[1][3]


def _export_by_query(server, client, query):
    """Export by *query* and wait for the export to finish."""
    resp = client.get("/pictures/export", params={"query": query})
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]
    try:
        deadline = time.monotonic() + 30
        while True:
            status = client.get("/pictures/export/status", params={"task_id": task_id})
            assert status.status_code == 200, status.text
            if status.json()["status"] != "in_progress":
                return status.json()
            assert time.monotonic() < deadline, "export did not finish within 30 s"
            time.sleep(0.1)
    finally:
        task = server.export_tasks.pop(task_id, None)
        private_dir = (task or {}).get("private_dir")
        if private_dir:
            shutil.rmtree(private_dir, ignore_errors=True)


def _settle_the_gpu_worker(runner):
    """Cancel the queued tasks and wait out the one the GPU worker is running.

    Call with the work planner stopped, so nothing queues more. Afterwards a
    routed call starts at once, and no GPU task rewrites a row a test seeds.
    """
    runner.cancel_pending_tasks()
    runner.run_on_gpu_worker(lambda: None, timeout_s=120)


def _make_every_query_find(server, picture_id):
    """Point *picture_id*'s text embedding along the stand-in SBERT query vectors.

    Those are all ``np.full(384, 0.5)``, so a text search then scores the
    picture above 0. ``Picture.semantic_search`` drops a picture that scores 0,
    as a fresh upload with no tags and no text embedding does. Call with the
    work planner stopped: the GPU worker is settled first, so no embedding task
    overwrites the vector before the export reads it.
    """
    assert not server.vault._work_planner.is_running(), "stop the work planner first"
    _settle_the_gpu_worker(server.vault._task_runner)

    def set_embedding(session):
        session.exec(
            update(Picture)
            .where(Picture.id == picture_id)
            .values(text_embedding=np.ones(384, dtype=np.float32).tobytes())
        )
        session.commit()

    server.vault.db.run_task(set_embedding)


def test_export_by_query_encodes_the_query_off_the_db_writer_thread(
    server, client, monkeypatch
):
    """``GET /pictures/export?query=`` encodes its query before queuing the search.

    Export by query uses only the SBERT encoder, on ``"A photo of " + query``.
    """
    picture_id = _upload_one_picture(client)
    encodes, searches, writer = _record_query_encoding(server, monkeypatch)
    planner = server.vault._work_planner
    planner.stop()
    try:
        _make_every_query_find(server, picture_id)
        status = _export_by_query(server, client, "a red bicycle")
    finally:
        planner.start()

    # clean_library empties the library before each test: the one upload is all.
    assert (status["status"], status["processed"]) == ("completed", 1), status
    assert [(name, text) for name, text, _, _ in encodes] == [
        ("sbert", "A photo of a red bicycle"),
    ], "export must encode 'A photo of <query>' once, with SBERT only"
    assert encodes[0][2] is not writer, (
        "the export query encode ran on the DB writer thread"
    )
    assert len(searches) == 1, searches
    assert searches[0]["query_embedding"] is encodes[0][3]
    assert searches[0].get("clip_query_embedding") is None


class _CpuQueryService:
    """A CPU copy of SBERT or CLIP that records each encode in *calls*.

    Records ``(name, thread)``, and in :attr:`on_loop` whether an event loop
    was running on that thread. Loaded unless *load_error* is given, which its
    load raises.
    """

    def __init__(self, calls, load_error: Exception | None = None):
        self._calls = calls
        self._load_error = load_error
        self.on_loop: list[bool] = []

    def is_loaded(self) -> bool:
        return self._load_error is None

    def ensure_ready(self) -> None:
        if self._load_error is not None:
            raise self._load_error

    def encode(self, texts):
        self._record("cpu_sbert")
        return [np.full(384, 0.5, np.float32) for _ in texts]

    def encode_text(self, query):
        self._record("cpu_clip_text")
        return np.full(512, 0.25, np.float32)

    def encode_image_batch(self, images, tensors=None):
        self._record("cpu_clip_image")
        return np.ones((len(images), 512), dtype=np.float32)

    def _record(self, name):
        self.ensure_ready()
        try:
            asyncio.get_running_loop()
            self.on_loop.append(True)
        except RuntimeError:
            self.on_loop.append(False)
        self._calls.append((name, threading.current_thread()))


class _MetalEngine:
    """An engine on Apple Metal whose device work records the thread it ran on.

    Carries the three Metal query encoders, their CPU copies, and the anomaly
    tagger's load and Grad-CAM pass, each appending ``(name, thread)`` to
    :attr:`calls`. The CPU copies' names start with ``cpu_``.
    """

    device = "mps"

    def __init__(
        self,
        tagger_loaded: bool = True,
        cpu_load_error: Exception | None = None,
    ):
        self.calls: list[tuple[str, threading.Thread]] = []
        self.cpu_query_encoders = CpuQueryEncoders(
            clip_service=_CpuQueryService(self.calls, cpu_load_error),
            sbert_service=_CpuQueryService(self.calls, cpu_load_error),
        )
        self.text_embedding_workflow = types.SimpleNamespace(
            encode_query=self._recorder("sbert", [np.full(384, 0.5, np.float32)]),
            encode_clip_query=self._recorder(
                "clip_text", np.full(512, 0.25, np.float32)
            ),
        )
        self.clip_embedding_workflow = types.SimpleNamespace(
            encode_images=self._recorder(
                "clip_image", np.ones((1, 512), dtype=np.float32)
            )
        )
        self._tagger_loaded = tagger_loaded
        self.pixlstash_tagger_service = types.SimpleNamespace(
            is_loaded=lambda: self._tagger_loaded,
            version=lambda: 1,
            resolve_label_index=lambda label: 0,
            localize_anomaly=self._recorder(
                "localize_anomaly", {"boxes": [], "diffuse": True, "heatmap": None}
            ),
        )

    def ensure_pixlstash_tagger_ready(self) -> bool:
        self.calls.append(("tagger_load", threading.current_thread()))
        self._tagger_loaded = True
        return True

    def close(self) -> None:
        """Nothing to release; present because vault shutdown calls it."""

    def _recorder(self, name, result):
        def record(*args, **kwargs):
            self.calls.append((name, threading.current_thread()))
            return result

        return record


@contextlib.contextmanager
def _serving_on_metal(server, engine):
    """Serve with *engine* as the vault's engine, the work planner stopped.

    Stopped so no finder builds real work around the stand-in engine. The real
    engine is put back before the planner starts again.
    """
    vault = server.vault
    planner = vault._work_planner
    planner.stop()
    original = vault._engine
    vault._engine = engine
    try:
        yield vault._task_runner._gpu_worker_thread
    finally:
        vault._engine = original
        planner.start()


def _small_png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _record_gpu_calls(server, monkeypatch):
    """Record every ``GpuCallTask`` submitted to the vault's task runner.

    The real ``submit`` still runs; ``run_on_gpu_worker`` submits through it.
    """
    runner = server.vault._task_runner
    calls = []
    real_submit = runner.submit

    def submit(task):
        if isinstance(task, GpuCallTask):
            calls.append(task)
        return real_submit(task)

    monkeypatch.setattr(runner, "submit", submit)
    return calls


def test_likeness_search_encodes_on_the_cpu_copy_off_the_event_loop(
    server, client, monkeypatch
):
    """On Metal the upload is encoded by the CPU copy of CLIP, in the executor.

    Never on the GPU worker, and never on the event loop: the encode blocks.
    """
    engine = _MetalEngine()
    gpu_calls = _record_gpu_calls(server, monkeypatch)

    with _serving_on_metal(server, engine) as worker:
        resp = client.post(
            "/pictures/likeness-search",
            files=[("files", ("query.png", _small_png(), "image/png"))],
        )

    assert resp.status_code == 200, resp.text
    assert [name for name, _ in engine.calls] == ["cpu_clip_image"]
    assert engine.calls[0][1] is not worker
    assert engine.cpu_query_encoders.clip_service.on_loop == [False], (
        "the query image was encoded on the event loop"
    )
    assert gpu_calls == [], (
        f"likeness search queued work on the GPU worker: {gpu_calls}"
    )


def test_text_search_encodes_on_the_cpu_copies_on_metal(server, client, monkeypatch):
    engine = _MetalEngine()
    gpu_calls = _record_gpu_calls(server, monkeypatch)

    with _serving_on_metal(server, engine) as worker:
        resp = client.get(
            "/pictures/search", params={"query": "a red bicycle", "threshold": 0.0}
        )

    assert resp.status_code == 200, resp.text
    assert [name for name, _ in engine.calls] == ["cpu_sbert", "cpu_clip_text"]
    for name, thread in engine.calls:
        assert thread is not worker, f"the {name} encode ran on the GPU worker"
    assert gpu_calls == [], f"text search queued work on the GPU worker: {gpu_calls}"


def test_export_by_query_encodes_on_the_cpu_copy_on_metal(server, client, monkeypatch):
    picture_id = _upload_one_picture(client)
    engine = _MetalEngine()

    with _serving_on_metal(server, engine) as worker:
        _make_every_query_find(server, picture_id)
        gpu_calls = _record_gpu_calls(server, monkeypatch)
        status = _export_by_query(server, client, "a red bicycle")

    # clean_library empties the library before each test: the one upload is all.
    assert (status["status"], status["processed"]) == ("completed", 1), status
    assert [name for name, _ in engine.calls] == ["cpu_sbert"]
    assert engine.calls[0][1] is not worker
    assert gpu_calls == [], f"export queued work on the GPU worker: {gpu_calls}"


@pytest.mark.parametrize("route", ["search", "likeness-search"])
def test_a_search_is_503_while_the_cpu_copies_are_not_loaded(
    route, server, client, monkeypatch
):
    """A search that cannot encode its query yet is refused, not failed with a 500.

    The CPU copies' load, queued on the GPU worker, fails here.
    """
    engine = _MetalEngine(cpu_load_error=OSError("no model files"))
    gpu_calls = _record_gpu_calls(server, monkeypatch)

    with _serving_on_metal(server, engine):
        if route == "search":
            resp = client.get(
                "/pictures/search", params={"query": "a red bicycle", "threshold": 0.0}
            )
        else:
            resp = client.post(
                "/pictures/likeness-search",
                files=[("files", ("query.png", _small_png(), "image/png"))],
            )

    assert len(gpu_calls) == 1, "the search did not queue the load it needed"
    assert resp.status_code == 503, resp.text
    assert "still loading its models" in resp.json()["detail"]
    assert engine.calls == []


@pytest.mark.parametrize("route", ["zip", "folder"])
def test_an_export_by_query_is_503_while_the_cpu_copies_are_not_loaded(
    route, server, client
):
    """Starting an export by query is refused, as a search is, until it can encode.

    The export encodes its query in its background task, after the start
    request has answered, where a failure reaches the owner as a bare
    ``failed``. The CPU copies' load, queued on the GPU worker, fails here. The
    export of a set does not encode its query, so it still starts. The folder
    route needs a loopback owner, which the in-process client counts as.
    """
    engine = _MetalEngine(cpu_load_error=OSError("no model files"))

    with (
        _serving_on_metal(server, engine),
        tempfile.TemporaryDirectory() as destination,
    ):

        def start(**params):
            if route == "zip":
                return client.get("/pictures/export", params=params)
            return client.post(
                "/pictures/export/folder",
                params={"destination": destination, **params},
            )

        before = set(server.export_tasks)
        refused = start(query="a red bicycle")
        after_refused = set(server.export_tasks)
        # Positive control: an export that starts is seen registering its task.
        started = start(query="a red bicycle", set_id=987654)
        started_ids = set(server.export_tasks) - after_refused
        for task_id in started_ids:
            server.export_tasks.pop(task_id, None)

    assert refused.status_code == 503, refused.text
    assert refused.json()["detail"] == (
        "Search is still loading its models; try the export again shortly."
    )
    assert after_refused - before == set(), "the refused export registered a task"
    assert started.status_code == 200, started.text
    assert started_ids == {started.json()["task_id"]}
    assert engine.calls == []


@pytest.mark.parametrize("route", ["search", "likeness", "zip", "folder"])
def test_a_search_with_no_gpu_worker_to_load_its_models_does_not_promise_a_retry(
    route, server, client, monkeypatch
):
    """A dead GPU worker does not come back, so "try again shortly" would mislead.

    Positive control: the same refusal with a worker running still asks for a
    retry.
    """
    details = {}

    with (
        _serving_on_metal(server, _MetalEngine()),
        tempfile.TemporaryDirectory() as destination,
    ):
        for worker_running in (False, True):

            def refuse(worker_running=worker_running):
                raise CpuQueryEncodersNotReadyError(
                    "not loaded", worker_running=worker_running
                )

            monkeypatch.setattr(server.vault, "query_encoders", refuse)
            if route == "search":
                resp = client.get(
                    "/pictures/search",
                    params={"query": "a red bicycle", "threshold": 0.0},
                )
            elif route == "likeness":
                resp = client.post(
                    "/pictures/likeness-search",
                    files=[("files", ("query.png", _small_png(), "image/png"))],
                )
            elif route == "zip":
                resp = client.get("/pictures/export", params={"query": "a red bicycle"})
            else:
                resp = client.post(
                    "/pictures/export/folder",
                    params={"destination": destination, "query": "a red bicycle"},
                )
            assert resp.status_code == 503, resp.text
            details[worker_running] = resp.json()["detail"]

    assert details[False] == NO_GPU_WORKER_DETAIL
    assert "again shortly" in details[True]


def test_an_export_that_cannot_encode_its_query_when_it_runs_fails_with_a_warning(
    server, caplog
):
    """The background fallback, for a query encoder load that fails.

    The start route cannot check the encoders before the engine exists. By the
    time the export task runs, its encode waits for their load and fails only
    if that load fails or outlasts the wait. Both generators share the encode,
    so both are run directly, with the CPU copies' load failing on the GPU
    worker.
    """
    engine = _MetalEngine(cpu_load_error=OSError("no model files"))
    request = types.SimpleNamespace(
        query_params=QueryParams(), state=types.SimpleNamespace()
    )
    tasks = {
        "zip-task": {"status": "in_progress"},
        "folder-task": {"status": "in_progress"},
    }
    caplog.set_level(logging.WARNING, logger=ExportUtils.__module__)

    with (
        _serving_on_metal(server, engine),
        tempfile.TemporaryDirectory() as destination,
    ):
        ExportUtils.generate_zip(
            server, request, "zip-task", tasks, {"query": "a red bicycle"}
        )
        ExportUtils.generate_folder_export(
            server,
            request,
            "folder-task",
            tasks,
            {"destination": destination, "query": "a red bicycle"},
        )

    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == ExportUtils.__module__ and record.levelno == logging.WARNING
    ]
    for task_id, task in tasks.items():
        assert task["status"] == "failed", (task_id, task)
        prefix = (
            f"Export task {task_id} cannot encode its search query yet "
            "(query='a red bicycle'): "
        )
        assert [m for m in warnings if m.startswith(prefix)], (task_id, warnings)
    assert engine.calls == []


def _fail_on_the_gpu_worker(server, monkeypatch, error):
    """Make every routed call raise *error*; return the calls that reached it."""
    attempts = []

    def fail(fn, /, *args, **kwargs):
        attempts.append(fn)
        raise error

    monkeypatch.setattr(server.vault._task_runner, "run_on_gpu_worker", fail)
    return attempts


def _time_out_on_the_gpu_worker(server, monkeypatch):
    """Make every routed call raise ``TimeoutError``, as a busy worker would."""
    return _fail_on_the_gpu_worker(
        server, monkeypatch, TimeoutError("GpuCallTask did not complete within 60.0s")
    )


def test_the_anomaly_region_runs_the_tagger_on_the_gpu_worker(
    server, client, monkeypatch
):
    """Both the on-demand tagger load and the Grad-CAM pass use the device."""
    status = upload_pictures_and_wait(
        client, [("file", ("anomaly.png", random_images[0], "image/png"))]
    )
    assert status["status"] == "completed", status
    picture_id = status["results"][0]["picture_id"]
    url = f"/pictures/{picture_id}/anomaly_region"
    clear_anomaly_region_cache()
    engine = _MetalEngine(tagger_loaded=False)

    with _serving_on_metal(server, engine) as worker:
        resp = client.get(url, params={"tag": "malformed hand"})
        assert resp.status_code == 200, resp.text
        assert [name for name, _ in engine.calls] == [
            "tagger_load",
            "localize_anomaly",
        ]
        for name, thread in engine.calls:
            assert thread is worker, f"{name} ran on {thread.name}"

        # A busy worker is a 503 whether or not the tagger has to load first.
        attempts = _time_out_on_the_gpu_worker(server, monkeypatch)
        for tagger_loaded in (False, True):
            clear_anomaly_region_cache()
            engine._tagger_loaded = tagger_loaded
            attempts.clear()
            busy = client.get(url, params={"tag": "malformed hand"})
            assert attempts, "the request never reached the GPU worker"
            assert busy.status_code == 503, (tagger_loaded, busy.text)
            assert "GPU is busy" in busy.json()["detail"]
    clear_anomaly_region_cache()


def _request_anomaly_region(client, picture_id):
    """Request the anomaly region of *picture_id*, past the route's cache."""
    clear_anomaly_region_cache()
    return client.get(
        f"/pictures/{picture_id}/anomaly_region", params={"tag": "malformed hand"}
    )


def _upload_one_picture(client):
    """Upload a picture and return its id."""
    status = upload_pictures_and_wait(
        client, [("file", ("anomaly.png", random_images[0], "image/png"))]
    )
    assert status["status"] == "completed", status
    return status["results"][0]["picture_id"]


def test_the_anomaly_region_is_503_when_there_is_no_gpu_worker_to_run_on(
    server, client, monkeypatch
):
    """A stopping runner, or none at all, is a temporary refusal, not a 500."""
    picture_id = _upload_one_picture(client)
    errors = [
        TaskCancelledError("Task GpuCallTask was cancelled before completion"),
        TaskRunnerNotRunningError(
            "TaskRunner vault-task-runner is stopped or was never started"
        ),
    ]

    with _serving_on_metal(server, _MetalEngine(tagger_loaded=False)):
        for error in errors:
            attempts = _fail_on_the_gpu_worker(server, monkeypatch, error)
            resp = _request_anomaly_region(client, picture_id)

            assert attempts, "the anomaly region never reached the GPU worker"
            assert resp.status_code == 503, (type(error).__name__, resp.text)
            assert resp.json()["detail"] == (
                "The GPU worker is not running; if this persists, restart PixlStash."
            )
    clear_anomaly_region_cache()


def test_a_model_error_on_the_gpu_worker_is_not_reported_as_a_missing_worker(
    server, client, monkeypatch
):
    """Only the runner's own errors are a 503 for want of a worker.

    A ``RuntimeError`` the model raised keeps the route's existing answer.
    """
    picture_id = _upload_one_picture(client)
    # A 500 is raised into the test by a client that re-raises server errors.
    quiet_client = TestClient(server.api, raise_server_exceptions=False)
    quiet_client.cookies = client.cookies
    attempts = _fail_on_the_gpu_worker(
        server, monkeypatch, RuntimeError("the model is not loaded")
    )

    with _serving_on_metal(server, _MetalEngine(tagger_loaded=False)):
        resp = _request_anomaly_region(quiet_client, picture_id)

    assert attempts, "the anomaly region never reached the GPU worker"
    assert resp.status_code == 500, resp.text
    assert "not running" not in resp.text
    assert "Failed to localise anomaly region" in resp.json()["detail"]
    clear_anomaly_region_cache()


class _ExitingPlugin(ImagePlugin):
    """An image plugin whose run ends with ``sys.exit(3)``, as argparse would."""

    name = "exits"

    def __init__(self):
        self.threads: list[threading.Thread] = []

    def parameter_schema(self):
        return []

    def run(
        self,
        images,
        parameters=None,
        progress_callback=None,
        error_callback=None,
        captions=None,
    ):
        self.threads.append(threading.current_thread())
        sys.exit(3)


def _serve_plugin(monkeypatch, plugin):
    """Serve ``POST /pictures/plugins/{plugin.name}`` with *plugin*.

    Its inputs are stand-in pictures, so no upload and no real engine is
    needed; the run fails before any output would be imported.
    """
    manager = types.SimpleNamespace(
        reload=lambda: None,
        get_plugin=lambda name: plugin if name == plugin.name else None,
    )
    monkeypatch.setattr(plugin_service, "get_image_plugin_manager", lambda: manager)
    monkeypatch.setattr(
        image_plugin_service,
        "_load_input_images",
        lambda server, picture_ids: [
            (
                types.SimpleNamespace(id=picture_id, description=""),
                Image.new("RGB", (8, 8)),
                "PNG",
                "input.png",
            )
            for picture_id in picture_ids
        ],
    )


def test_a_plugin_run_is_503_when_there_is_no_gpu_worker_to_run_on(
    server, client, monkeypatch
):
    """On Metal a plugin runs on the GPU worker; with none it is a temporary refusal.

    A stopped runner, a dead worker, or a full restore cancelling the queued
    run. The plugin's own error keeps its 500.
    """
    plugin = _ExitingPlugin()
    _serve_plugin(monkeypatch, plugin)
    url = f"/pictures/plugins/{plugin.name}"
    errors = [
        TaskCancelledError("Task GpuCallTask was cancelled before completion"),
        TaskRunnerNotRunningError(
            "TaskRunner vault-task-runner GPU worker is no longer running"
        ),
    ]

    with _serving_on_metal(server, _MetalEngine()):
        for error in errors:
            attempts = _fail_on_the_gpu_worker(server, monkeypatch, error)
            resp = client.post(url, json={"picture_ids": [1]})

            assert attempts, "the plugin run never reached the GPU worker"
            assert resp.status_code == 503, (type(error).__name__, resp.text)
            assert resp.json()["detail"] == (
                "The GPU worker is not running; if this persists, restart PixlStash."
            )

        # Positive control: an error from the plugin is still a failed plugin.
        attempts = _fail_on_the_gpu_worker(
            server, monkeypatch, RuntimeError("the upscaler weights are missing")
        )
        resp = client.post(url, json={"picture_ids": [1]})

    assert attempts, "the plugin run never reached the GPU worker"
    assert resp.status_code == 500, resp.text
    assert "Plugin failed: the upscaler weights are missing" in resp.json()["detail"]
    assert plugin.threads == []


def test_a_plugin_that_exits_fails_its_run_and_the_gpu_worker_runs_on(
    server, client, monkeypatch
):
    """A plugin's ``sys.exit()`` is a failed run, not the end of a thread.

    Not of the GPU worker, which every plugin runs on on Metal, and not of the
    server: asyncio lets ``SystemExit`` out of the event loop. The request is
    made on a thread of its own because that is how the event loop's end shows
    here: the client waits for ever on a loop that has stopped.
    """
    plugin = _ExitingPlugin()
    _serve_plugin(monkeypatch, plugin)
    runner = server.vault._task_runner
    responses = []

    with _serving_on_metal(server, _MetalEngine()) as worker:
        requester = threading.Thread(
            target=lambda: responses.append(
                client.post(
                    f"/pictures/plugins/{plugin.name}", json={"picture_ids": [1]}
                )
            ),
            daemon=True,
        )
        requester.start()
        requester.join(30)

        assert responses, "the request never finished: its event loop stopped"
        resp = responses[0]
        assert plugin.threads == [worker], "the plugin did not run on the GPU worker"
        assert resp.status_code == 500, resp.text
        # Equal, not containing: a ``GpuCallTask`` that fails to keep the
        # ``SystemExit`` for its waiter still names it, inside a runner error.
        assert resp.json()["detail"] == "Plugin failed: SystemExit(3)"
        assert worker.is_alive(), "the plugin's sys.exit() ended the GPU worker"
        assert runner.run_on_gpu_worker(threading.current_thread, timeout_s=10) is (
            worker
        )
    assert client.get("/protected").status_code == 200


def test_the_anomaly_region_loads_and_localises_in_one_gpu_call(
    server, client, monkeypatch
):
    """An idle unload queued on the worker cannot land between load and pass.

    The load here queues exactly that unload, as ``_maybe_aggressive_unload``
    does from a progress poll. Split over two routed calls, the unload runs
    between them and the Grad-CAM pass finds no model.
    """
    picture_id = _upload_one_picture(client)
    engine = _MetalEngine(tagger_loaded=False)
    runner = server.vault._task_runner
    routed = []
    real_run_on_gpu_worker = runner.run_on_gpu_worker

    def counting_run_on_gpu_worker(fn, /, *args, **kwargs):
        routed.append(fn)
        return real_run_on_gpu_worker(fn, *args, **kwargs)

    def unload():
        engine._tagger_loaded = False

    def load_and_queue_an_idle_unload():
        engine.calls.append(("tagger_load", threading.current_thread()))
        engine._tagger_loaded = True
        runner.submit(GpuCallTask(unload))
        return True

    def localize(pil_image, tag):
        engine.calls.append(("localize_anomaly", threading.current_thread()))
        if not engine._tagger_loaded:
            raise RuntimeError("PixlStash tagger model is not loaded")
        return {"boxes": [], "diffuse": True, "heatmap": None}

    engine.ensure_pixlstash_tagger_ready = load_and_queue_an_idle_unload
    engine.pixlstash_tagger_service.localize_anomaly = localize
    monkeypatch.setattr(runner, "run_on_gpu_worker", counting_run_on_gpu_worker)

    with _serving_on_metal(server, engine) as worker:
        resp = _request_anomaly_region(client, picture_id)
        # Lets the queued unload run before the real engine is put back.
        real_run_on_gpu_worker(lambda: None, timeout_s=60)

    assert resp.status_code == 200, resp.text
    assert len(routed) == 1, routed
    assert [name for name, _ in engine.calls] == ["tagger_load", "localize_anomaly"]
    for name, thread in engine.calls:
        assert thread is worker, f"{name} ran on {thread.name}"
    assert engine._tagger_loaded is False, "the queued unload never ran"
    clear_anomaly_region_cache()


def test_an_anomaly_region_request_that_times_out_leaves_the_gpu_call_its_picture(
    server, client, monkeypatch
):
    """The routed call owns the picture it localises, not the request waiting on it.

    A call that has started on the GPU worker runs to the end after its caller
    stops waiting (``run_on_gpu_worker``). Here the request times out while the
    tagger's cold load is still under way; the Grad-CAM pass that follows must
    still read its picture, which it cannot if the request opened the file and
    closed it on its way out with the 503.
    """
    picture_id = _upload_one_picture(client)
    engine = _MetalEngine(tagger_loaded=False)
    runner = server.vault._task_runner
    load_started = threading.Event()
    release_load = threading.Event()
    reads = []

    def slow_load():
        engine.calls.append(("tagger_load", threading.current_thread()))
        load_started.set()
        release_load.wait(60)
        engine._tagger_loaded = True
        return True

    def localize(pil_image, tag):
        engine.calls.append(("localize_anomaly", threading.current_thread()))
        try:
            pil_image.convert("RGB")
        except Exception as exc:
            reads.append(exc)
        else:
            reads.append("read")
        return {"boxes": [], "diffuse": True, "heatmap": None}

    engine.ensure_pixlstash_tagger_ready = slow_load
    engine.pixlstash_tagger_service.localize_anomaly = localize

    with _serving_on_metal(server, engine) as worker:
        # So the call starts well inside its short timeout instead of being
        # cancelled while it waits behind the upload's own work.
        _settle_the_gpu_worker(runner)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(TaskRunner, "GPU_CALL_TIMEOUT_S", 1.0)
                resp = _request_anomaly_region(client, picture_id)
        finally:
            release_load.set()
            # Waits for the abandoned call, so it finishes on the stand-in engine.
            runner.run_on_gpu_worker(lambda: None, timeout_s=60)

    assert load_started.is_set(), (
        "the call never started on the GPU worker, so nothing was in flight at "
        "the timeout"
    )
    assert resp.status_code == 503, resp.text
    assert "GPU is busy" in resp.json()["detail"]
    assert reads == ["read"], (
        f"the Grad-CAM pass could not read its picture after the timeout: {reads!r}"
    )
    assert [name for name, _ in engine.calls] == ["tagger_load", "localize_anomaly"]
    for name, thread in engine.calls:
        assert thread is worker, f"{name} ran on {thread.name}"
    clear_anomaly_region_cache()
