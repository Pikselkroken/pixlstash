"""Tests for SnapshotService — creation, listing, deletion, and GFS retention."""

import json
import os
import shutil
import tempfile

import pytest
from sqlalchemy import text
from sqlmodel import delete

from pixlstash.db_models import Picture
from pixlstash.db_models.change_log import ChangeLog
from pixlstash.db_models.snapshot import Snapshot
from pixlstash.server import Server


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as tmp:
        with Server(f"{tmp}/server-config.json") as srv:
            yield srv


@pytest.fixture(autouse=True)
def clean_db(server):
    """Wipe DB rows and snapshot files before each test."""

    def _wipe(session):
        session.exec(text("PRAGMA foreign_keys = OFF"))
        session.exec(delete(Snapshot))
        session.exec(delete(ChangeLog))
        session.exec(delete(Picture))
        session.exec(text("PRAGMA foreign_keys = ON"))
        session.commit()

    server.vault.db.run_task(_wipe)

    cp_dir = os.path.join(server.vault.image_root, "snapshots")
    if os.path.isdir(cp_dir):
        shutil.rmtree(cp_dir)
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_db_snapshots(server) -> int:
    from sqlmodel import func, select

    return server.vault.db.run_immediate_read_task(
        lambda s: s.exec(select(func.count()).select_from(Snapshot)).one()
    )


def _add_pictures(server, count: int = 3):
    def _do(session):
        for i in range(count):
            session.add(Picture(file_path=f"pic_{i}.jpg", filename=f"pic_{i}.jpg"))
        session.commit()

    server.vault.db.run_task(_do)


# ---------------------------------------------------------------------------
# create_snapshot: files and DB row are created
# ---------------------------------------------------------------------------


def test_create_manual_snapshot_creates_files_and_row(server):
    cp = server.vault.snapshot_service.create_snapshot("MANUAL", label="my label")

    assert cp.id is not None
    assert cp.kind == "MANUAL"
    assert cp.label == "my label"

    abs_snapshot = os.path.join(server.vault.image_root, cp.relative_path)
    assert os.path.isfile(abs_snapshot), "Snapshot .sqlite must exist on disk"

    abs_manifest = os.path.join(server.vault.image_root, cp.manifest_relative_path)
    assert os.path.isfile(abs_manifest), "Manifest .json must exist on disk"


def test_snapshot_manifest_contains_expected_keys(server):
    _add_pictures(server, count=2)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    abs_manifest = os.path.join(server.vault.image_root, cp.manifest_relative_path)
    manifest = json.loads(open(abs_manifest).read())

    assert "max_changelog_id" in manifest
    assert "picture_count" in manifest
    assert "picture_ids" in manifest
    assert "schema_version" in manifest


def test_snapshot_picture_count_matches(server):
    _add_pictures(server, count=4)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    assert cp.picture_count == 4

    abs_manifest = os.path.join(server.vault.image_root, cp.manifest_relative_path)
    manifest = json.loads(open(abs_manifest).read())
    assert manifest["picture_count"] == 4
    assert len(manifest["picture_ids"]) == 4


# ---------------------------------------------------------------------------
# list_snapshots and get_snapshot
# ---------------------------------------------------------------------------


def test_list_snapshots_returns_all(server):
    server.vault.snapshot_service.create_snapshot("MANUAL")
    server.vault.snapshot_service.create_snapshot("OPPORTUNISTIC")

    cps = server.vault.snapshot_service.list_snapshots()
    assert len(cps) == 2


def test_list_snapshots_ordered_newest_first(server):
    cp1 = server.vault.snapshot_service.create_snapshot("MANUAL", label="first")
    cp2 = server.vault.snapshot_service.create_snapshot("MANUAL", label="second")

    cps = server.vault.snapshot_service.list_snapshots()
    assert cps[0].id == cp2.id, "Newest snapshot should be first"
    assert cps[1].id == cp1.id


def test_get_snapshot_returns_correct_row(server):
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")
    fetched = server.vault.snapshot_service.get_snapshot(cp.id)

    assert fetched is not None
    assert fetched.id == cp.id
    assert fetched.kind == "MANUAL"


def test_get_snapshot_nonexistent_returns_none(server):
    result = server.vault.snapshot_service.get_snapshot(9999)
    assert result is None


# ---------------------------------------------------------------------------
# delete_snapshot removes row and files
# ---------------------------------------------------------------------------


def test_delete_snapshot_removes_row_and_files(server):
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")
    abs_snapshot = os.path.join(server.vault.image_root, cp.relative_path)
    abs_manifest = os.path.join(server.vault.image_root, cp.manifest_relative_path)
    assert os.path.isfile(abs_snapshot)
    assert os.path.isfile(abs_manifest)

    deleted = server.vault.snapshot_service.delete_snapshot(cp.id)

    assert deleted is True
    assert server.vault.snapshot_service.get_snapshot(cp.id) is None
    assert not os.path.isfile(abs_snapshot), "Snapshot file should be removed"
    assert not os.path.isfile(abs_manifest), "Manifest file should be removed"


def test_delete_nonexistent_snapshot_returns_false(server):
    result = server.vault.snapshot_service.delete_snapshot(9999)
    assert result is False


# ---------------------------------------------------------------------------
# snapshot_if_due: skips when a recent snapshot exists
# ---------------------------------------------------------------------------


def test_snapshot_if_due_creates_when_none_exist(server):
    result = server.vault.snapshot_service.snapshot_if_due("test")
    assert result is not None
    assert _count_db_snapshots(server) == 1


def test_snapshot_if_due_skips_when_recent(server):
    server.vault.snapshot_service.create_snapshot("OPPORTUNISTIC")
    assert _count_db_snapshots(server) == 1

    result = server.vault.snapshot_service.snapshot_if_due("test")

    assert result is None, "Should skip — a snapshot was just taken"
    assert _count_db_snapshots(server) == 1
