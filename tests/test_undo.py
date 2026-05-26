"""Tests for UndoService — reversing individual transactions and checkpoint undo."""

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


def _add_picture(server, filename="undo.jpg", description="original") -> Picture:
    def _do(session):
        pic = Picture(file_path=filename, filename=filename, description=description)
        session.add(pic)
        session.commit()
        session.refresh(pic)
        return pic

    return server.vault.db.run_task(_do)


def _get_picture(server, pic_id: int):
    return server.vault.db.run_immediate_read_task(lambda s: s.get(Picture, pic_id))


def _changelog_count(server) -> int:
    from sqlmodel import func

    return server.vault.db.run_immediate_read_task(
        lambda s: s.exec(select(func.count()).select_from(ChangeLog)).one()
    )


# ---------------------------------------------------------------------------
# undo_last_transaction: reverses UPDATE
# ---------------------------------------------------------------------------


def test_undo_reverses_update(server):
    # INSERT (txn A), then UPDATE (txn B); undo reverses txn B only.
    pic = _add_picture(server, description="before")

    def _update(session):
        p = session.get(Picture, pic.id)
        p.description = "after"
        session.commit()

    server.vault.db.run_task(_update)
    assert _get_picture(server, pic.id).description == "after"

    report = server.vault.undo_service.undo_last_transaction()

    assert report.reverted_row_count >= 1
    assert not report.errors
    assert _get_picture(server, pic.id).description == "before"


# ---------------------------------------------------------------------------
# undo_last_transaction: reverses INSERT (deletes the row)
# ---------------------------------------------------------------------------


def test_undo_reverses_insert(server):
    pic = _add_picture(server, filename="to_be_undone.jpg")

    report = server.vault.undo_service.undo_last_transaction()

    assert report.reverted_row_count >= 1
    assert not report.errors
    assert _get_picture(server, pic.id) is None, "Undoing an INSERT must remove the row"


# ---------------------------------------------------------------------------
# undo_last_transaction: reverses DELETE (re-inserts the row)
# ---------------------------------------------------------------------------


def test_undo_reverses_delete(server):
    pic = _add_picture(server, filename="deleted_then_undone.jpg")

    def _del(session):
        p = session.get(Picture, pic.id)
        session.delete(p)
        session.commit()

    server.vault.db.run_task(_del)
    assert _get_picture(server, pic.id) is None

    report = server.vault.undo_service.undo_last_transaction()

    assert report.reverted_row_count >= 1
    assert not report.errors
    restored = _get_picture(server, pic.id)
    assert restored is not None, "Undoing a DELETE must re-insert the row"
    assert restored.file_path == "deleted_then_undone.jpg"


# ---------------------------------------------------------------------------
# undo_last_transaction: empty changelog is a no-op
# ---------------------------------------------------------------------------


def test_undo_empty_changelog_is_noop(server):
    report = server.vault.undo_service.undo_last_transaction()

    assert report.reverted_txn_count == 0
    assert report.reverted_row_count == 0
    assert not report.errors


# ---------------------------------------------------------------------------
# undo_last_transaction: only the LAST transaction is reverted
# ---------------------------------------------------------------------------


def test_undo_only_reverts_last_transaction(server):
    # Two separate tasks = two separate txn_ids.
    pic = _add_picture(server, description="v0")  # txn A: INSERT

    def _upd1(session):
        session.get(Picture, pic.id).description = "v1"
        session.commit()

    def _upd2(session):
        session.get(Picture, pic.id).description = "v2"
        session.commit()

    server.vault.db.run_task(_upd1)  # txn B
    server.vault.db.run_task(_upd2)  # txn C — last

    report = server.vault.undo_service.undo_last_transaction()

    assert report.reverted_txn_count == 1  # only txn C
    assert _get_picture(server, pic.id).description == "v1"


# ---------------------------------------------------------------------------
# undo_to_checkpoint: reverts multiple transactions made after checkpoint
# ---------------------------------------------------------------------------


def test_undo_to_checkpoint_reverts_post_checkpoint_changes(server):
    pic = _add_picture(server, description="v0")
    cp = server.vault.checkpoint_service.create_checkpoint("MANUAL")

    def _upd1(session):
        session.get(Picture, pic.id).description = "v1"
        session.commit()

    def _upd2(session):
        session.get(Picture, pic.id).description = "v2"
        session.commit()

    server.vault.db.run_task(_upd1)  # post-checkpoint txn 1
    server.vault.db.run_task(_upd2)  # post-checkpoint txn 2

    assert _get_picture(server, pic.id).description == "v2"

    report = server.vault.undo_service.undo_to_checkpoint(cp.id)

    assert report.reverted_txn_count >= 2
    assert not report.errors
    final_desc = _get_picture(server, pic.id).description
    assert final_desc == "v0", (
        f"Expected 'v0' after undo-to-checkpoint, got '{final_desc}'"
    )


# ---------------------------------------------------------------------------
# undo_to_checkpoint: no changes after checkpoint → no-op
# ---------------------------------------------------------------------------


def test_undo_to_checkpoint_noop_when_no_changes(server):
    # Background tasks (ComfyUI extraction, text scoring, etc.) may write to
    # included columns (comfyui_models, text_embedding, etc.) between checkpoint
    # creation and the undo call.  Those writes are correctly reversed by
    # undo_to_checkpoint (restoring the picture to its exact checkpoint state).
    # What matters is that user-visible state (description) is preserved and
    # that no errors are reported.
    pic = _add_picture(server, description="stable")
    cp = server.vault.checkpoint_service.create_checkpoint("MANUAL")

    report = server.vault.undo_service.undo_to_checkpoint(cp.id)

    assert not report.errors
    final_desc = _get_picture(server, pic.id).description
    assert final_desc == "stable"


# ---------------------------------------------------------------------------
# undo_to_checkpoint: invalid checkpoint raises ValueError
# ---------------------------------------------------------------------------


def test_undo_to_checkpoint_invalid_id_raises(server):
    with pytest.raises(ValueError, match="not found"):
        server.vault.undo_service.undo_to_checkpoint(9999)
