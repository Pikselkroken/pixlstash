"""Tests for CheckpointService — creation, listing, deletion, and GFS retention."""

import json
import os
import shutil
import tempfile

import pytest
from sqlalchemy import text
from sqlmodel import delete

from pixlstash.db_models import Picture
from pixlstash.db_models.change_log import ChangeLog
from pixlstash.db_models.checkpoint import Checkpoint
from pixlstash.server import Server


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as tmp:
        with Server(f"{tmp}/server-config.json") as srv:
            yield srv


@pytest.fixture(autouse=True)
def clean_db(server):
    """Wipe DB rows and checkpoint files before each test."""

    def _wipe(session):
        session.exec(text("PRAGMA foreign_keys = OFF"))
        session.exec(delete(Checkpoint))
        session.exec(delete(ChangeLog))
        session.exec(delete(Picture))
        session.exec(text("PRAGMA foreign_keys = ON"))
        session.commit()

    server.vault.db.run_task(_wipe)

    cp_dir = os.path.join(server.vault.image_root, "checkpoints")
    if os.path.isdir(cp_dir):
        shutil.rmtree(cp_dir)
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_db_checkpoints(server) -> int:
    from sqlmodel import func, select

    return server.vault.db.run_immediate_read_task(
        lambda s: s.exec(select(func.count()).select_from(Checkpoint)).one()
    )


def _add_pictures(server, count: int = 3):
    def _do(session):
        for i in range(count):
            session.add(Picture(file_path=f"pic_{i}.jpg", filename=f"pic_{i}.jpg"))
        session.commit()

    server.vault.db.run_task(_do)


# ---------------------------------------------------------------------------
# create_checkpoint: files and DB row are created
# ---------------------------------------------------------------------------


def test_create_manual_checkpoint_creates_files_and_row(server):
    cp = server.vault.checkpoint_service.create_checkpoint("MANUAL", label="my label")

    assert cp.id is not None
    assert cp.kind == "MANUAL"
    assert cp.label == "my label"

    abs_snapshot = os.path.join(server.vault.image_root, cp.relative_path)
    assert os.path.isfile(abs_snapshot), "Snapshot .sqlite must exist on disk"

    abs_manifest = os.path.join(server.vault.image_root, cp.manifest_relative_path)
    assert os.path.isfile(abs_manifest), "Manifest .json must exist on disk"


def test_checkpoint_manifest_contains_expected_keys(server):
    _add_pictures(server, count=2)
    cp = server.vault.checkpoint_service.create_checkpoint("MANUAL")

    abs_manifest = os.path.join(server.vault.image_root, cp.manifest_relative_path)
    manifest = json.loads(open(abs_manifest).read())

    assert "max_changelog_id" in manifest
    assert "picture_count" in manifest
    assert "picture_ids" in manifest
    assert "schema_version" in manifest


def test_checkpoint_picture_count_matches(server):
    _add_pictures(server, count=4)
    cp = server.vault.checkpoint_service.create_checkpoint("MANUAL")

    assert cp.picture_count == 4

    abs_manifest = os.path.join(server.vault.image_root, cp.manifest_relative_path)
    manifest = json.loads(open(abs_manifest).read())
    assert manifest["picture_count"] == 4
    assert len(manifest["picture_ids"]) == 4


# ---------------------------------------------------------------------------
# list_checkpoints and get_checkpoint
# ---------------------------------------------------------------------------


def test_list_checkpoints_returns_all(server):
    server.vault.checkpoint_service.create_checkpoint("MANUAL")
    server.vault.checkpoint_service.create_checkpoint("OPPORTUNISTIC")

    cps = server.vault.checkpoint_service.list_checkpoints()
    assert len(cps) == 2


def test_list_checkpoints_ordered_newest_first(server):
    cp1 = server.vault.checkpoint_service.create_checkpoint("MANUAL", label="first")
    cp2 = server.vault.checkpoint_service.create_checkpoint("MANUAL", label="second")

    cps = server.vault.checkpoint_service.list_checkpoints()
    assert cps[0].id == cp2.id, "Newest checkpoint should be first"
    assert cps[1].id == cp1.id


def test_get_checkpoint_returns_correct_row(server):
    cp = server.vault.checkpoint_service.create_checkpoint("MANUAL")
    fetched = server.vault.checkpoint_service.get_checkpoint(cp.id)

    assert fetched is not None
    assert fetched.id == cp.id
    assert fetched.kind == "MANUAL"


def test_get_checkpoint_nonexistent_returns_none(server):
    result = server.vault.checkpoint_service.get_checkpoint(9999)
    assert result is None


# ---------------------------------------------------------------------------
# delete_checkpoint removes row and files
# ---------------------------------------------------------------------------


def test_delete_checkpoint_removes_row_and_files(server):
    cp = server.vault.checkpoint_service.create_checkpoint("MANUAL")
    abs_snapshot = os.path.join(server.vault.image_root, cp.relative_path)
    abs_manifest = os.path.join(server.vault.image_root, cp.manifest_relative_path)
    assert os.path.isfile(abs_snapshot)
    assert os.path.isfile(abs_manifest)

    deleted = server.vault.checkpoint_service.delete_checkpoint(cp.id)

    assert deleted is True
    assert server.vault.checkpoint_service.get_checkpoint(cp.id) is None
    assert not os.path.isfile(abs_snapshot), "Snapshot file should be removed"
    assert not os.path.isfile(abs_manifest), "Manifest file should be removed"


def test_delete_nonexistent_checkpoint_returns_false(server):
    result = server.vault.checkpoint_service.delete_checkpoint(9999)
    assert result is False


# ---------------------------------------------------------------------------
# checkpoint_if_due: skips when a recent checkpoint exists
# ---------------------------------------------------------------------------


def test_checkpoint_if_due_creates_when_none_exist(server):
    result = server.vault.checkpoint_service.checkpoint_if_due("test")
    assert result is not None
    assert _count_db_checkpoints(server) == 1


def test_checkpoint_if_due_skips_when_recent(server):
    server.vault.checkpoint_service.create_checkpoint("OPPORTUNISTIC")
    assert _count_db_checkpoints(server) == 1

    result = server.vault.checkpoint_service.checkpoint_if_due("test")

    assert result is None, "Should skip — a checkpoint was just taken"
    assert _count_db_checkpoints(server) == 1
