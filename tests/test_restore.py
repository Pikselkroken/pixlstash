"""Tests for RestoreService — full and per-resource restore."""

import os
import shutil
import tempfile

import pytest
from sqlalchemy import text
from sqlmodel import delete, select

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
    """Wipe all relevant tables and checkpoint files before each test."""

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


def _add_picture(server, filename="test.jpg", description=None) -> Picture:
    def _do(session):
        pic = Picture(file_path=filename, filename=filename, description=description)
        session.add(pic)
        session.commit()
        session.refresh(pic)
        return pic

    return server.vault.db.run_task(_do)


def _get_picture(server, pic_id: int):
    return server.vault.db.run_immediate_read_task(
        lambda s: s.get(Picture, pic_id)
    )


def _create_file(server, relative_path: str):
    """Create an empty placeholder file inside the vault image_root."""
    abs_path = os.path.join(server.vault.image_root, relative_path)
    open(abs_path, "wb").close()
    return abs_path


# ---------------------------------------------------------------------------
# Full restore: reverts a mutated description to the pre-snapshot value
# ---------------------------------------------------------------------------


def test_full_restore_reverts_mutation(server):
    _create_file(server, "original.jpg")
    pic = _add_picture(server, filename="original.jpg", description="before")
    cp = server.vault.checkpoint_service.create_checkpoint("MANUAL")

    def _mutate(session):
        p = session.get(Picture, pic.id)
        p.description = "after mutation"
        session.commit()

    server.vault.db.run_task(_mutate)

    # Sanity: mutation is visible before restore.
    assert _get_picture(server, pic.id).description == "after mutation"

    report = server.vault.restore_service.restore_full(cp.id)

    assert not report.errors, f"Restore errors: {report.errors}"
    assert report.missing_files_count == 0

    restored_pic = _get_picture(server, pic.id)
    assert restored_pic is not None
    assert restored_pic.description == "before", (
        f"Expected description 'before' after restore, got '{restored_pic.description}'"
    )


# ---------------------------------------------------------------------------
# Full restore: picture without matching file on disk is dropped
# ---------------------------------------------------------------------------


def test_full_restore_drops_row_for_missing_file(server):
    # Add a picture whose file does NOT exist on disk.
    pic = _add_picture(server, filename="ghost.jpg")
    cp = server.vault.checkpoint_service.create_checkpoint("MANUAL")

    report = server.vault.restore_service.restore_full(cp.id)

    assert report.missing_files_count == 1, (
        f"Expected 1 missing-file picture, got {report.missing_files_count}"
    )
    remaining = _get_picture(server, pic.id)
    assert remaining is None, "Row for missing-file picture must be removed after restore"


# ---------------------------------------------------------------------------
# Full restore with dry_run=True: DB is not modified
# ---------------------------------------------------------------------------


def test_full_restore_dry_run_leaves_db_unchanged(server):
    _create_file(server, "dry.jpg")
    pic = _add_picture(server, filename="dry.jpg", description="original")
    cp = server.vault.checkpoint_service.create_checkpoint("MANUAL")

    def _mutate(session):
        p = session.get(Picture, pic.id)
        p.description = "mutated"
        session.commit()

    server.vault.db.run_task(_mutate)

    report = server.vault.restore_service.restore_full(cp.id, dry_run=True)

    # dry_run must not error out.
    assert not report.errors

    # The mutation must still be present.
    desc = _get_picture(server, pic.id).description
    assert desc == "mutated", f"dry_run should not change the DB, got '{desc}'"


# ---------------------------------------------------------------------------
# Per-resource restore: deleted picture is re-inserted from snapshot
# ---------------------------------------------------------------------------


def test_restore_resource_re_inserts_deleted_picture(server):
    _create_file(server, "restorable.jpg")
    pic = _add_picture(server, filename="restorable.jpg", description="original")
    cp = server.vault.checkpoint_service.create_checkpoint("MANUAL")

    # Delete the picture from the live DB.
    def _del(session):
        session.delete(session.get(Picture, pic.id))
        session.commit()

    server.vault.db.run_task(_del)
    assert _get_picture(server, pic.id) is None

    report = server.vault.restore_service.restore_resource(cp.id, "picture", pic.id)

    assert not report.errors, f"Restore errors: {report.errors}"
    assert report.upserted_count >= 1

    restored = _get_picture(server, pic.id)
    assert restored is not None, "Picture should be re-inserted by restore_resource"
    assert restored.file_path == "restorable.jpg"


# ---------------------------------------------------------------------------
# Per-resource restore: mutated description is reverted
# ---------------------------------------------------------------------------


def test_restore_resource_reverts_description_change(server):
    _create_file(server, "revert_desc.jpg")
    pic = _add_picture(server, filename="revert_desc.jpg", description="v1")
    cp = server.vault.checkpoint_service.create_checkpoint("MANUAL")

    def _mutate(session):
        p = session.get(Picture, pic.id)
        p.description = "v2"
        session.commit()

    server.vault.db.run_task(_mutate)

    report = server.vault.restore_service.restore_resource(cp.id, "picture", pic.id)

    assert not report.errors
    assert report.upserted_count >= 1

    restored = _get_picture(server, pic.id)
    assert restored.description == "v1", (
        f"Expected description 'v1' after per-resource restore, got '{restored.description}'"
    )


# ---------------------------------------------------------------------------
# Per-resource restore: invalid resource_type raises ValueError
# ---------------------------------------------------------------------------


def test_restore_resource_invalid_type_raises(server):
    cp = server.vault.checkpoint_service.create_checkpoint("MANUAL")
    with pytest.raises(ValueError, match="Invalid resource_type"):
        server.vault.restore_service.restore_resource(cp.id, "unknown_type", 1)
