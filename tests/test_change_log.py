"""Tests for the ChangeLog flush-hook infrastructure in VaultDatabase."""

import json
import tempfile

import pytest
from sqlalchemy import text
from sqlmodel import delete, select

from pixlstash.db_models import Picture
from pixlstash.db_models.change_log import ChangeLog
from pixlstash.db_models.quality import Quality
from pixlstash.server import Server


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as tmp:
        with Server(f"{tmp}/server-config.json") as srv:
            yield srv


@pytest.fixture(autouse=True)
def clean_db(server):
    """Wipe ChangeLog, Quality, and Picture rows before each test."""

    def _wipe(session):
        session.exec(text("PRAGMA foreign_keys = OFF"))
        session.exec(delete(ChangeLog))
        session.exec(delete(Quality))
        session.exec(delete(Picture))
        session.exec(text("PRAGMA foreign_keys = ON"))
        session.commit()

    server.vault.db.run_task(_wipe)
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_picture(server, filename="test.jpg", description=None) -> Picture:
    def _do(session):
        pic = Picture(file_path=filename, filename=filename, description=description)
        session.add(pic)
        session.commit()
        session.refresh(pic)
        return pic

    return server.vault.db.run_task(_do)


def _all_changelog(server) -> list[ChangeLog]:
    return server.vault.db.run_immediate_read_task(
        lambda s: list(s.exec(select(ChangeLog).order_by(ChangeLog.id)).all())
    )


def _clear_changelog(server):
    server.vault.db.run_task(lambda s: (s.exec(delete(ChangeLog)), s.commit()))


# ---------------------------------------------------------------------------
# INSERT is logged
# ---------------------------------------------------------------------------


def test_insert_creates_changelog_row(server):
    pic = _insert_picture(server)

    rows = _all_changelog(server)
    picture_rows = [r for r in rows if r.table_name == "picture"]
    assert len(picture_rows) == 1

    row = picture_rows[0]
    assert row.op == "INSERT"
    assert str(pic.id) in row.row_pk_json
    assert row.before_json is None
    assert row.after_json is not None


# ---------------------------------------------------------------------------
# UPDATE is logged with before/after
# ---------------------------------------------------------------------------


def test_update_creates_changelog_row_with_before_after(server):
    pic = _insert_picture(server, description="original")
    _clear_changelog(server)

    def _update(session):
        p = session.get(Picture, pic.id)
        p.description = "changed"
        session.add(p)
        session.commit()

    server.vault.db.run_task(_update)

    rows = _all_changelog(server)
    update_rows = [r for r in rows if r.op == "UPDATE" and r.table_name == "picture"]
    assert update_rows, "Expected at least one UPDATE ChangeLog row for picture"

    row = update_rows[0]
    assert row.before_json is not None
    assert row.after_json is not None

    before = json.loads(row.before_json)
    after = json.loads(row.after_json)
    assert before.get("description") == "original"
    assert after.get("description") == "changed"


# ---------------------------------------------------------------------------
# DELETE is logged with before state, no after
# ---------------------------------------------------------------------------


def test_delete_creates_changelog_row(server):
    pic = _insert_picture(server)
    _clear_changelog(server)

    def _delete_pic(session):
        p = session.get(Picture, pic.id)
        session.delete(p)
        session.commit()

    server.vault.db.run_task(_delete_pic)

    rows = _all_changelog(server)
    delete_rows = [r for r in rows if r.op == "DELETE" and r.table_name == "picture"]
    assert delete_rows, "Expected a DELETE ChangeLog row"

    row = delete_rows[0]
    assert row.before_json is not None
    assert row.after_json is None


# ---------------------------------------------------------------------------
# Excluded table (Quality) produces no data payload
# ---------------------------------------------------------------------------


def test_excluded_table_produces_no_data_payload(server):
    """Quality is in CHANGE_LOG_EXCLUDED_TABLES — no before/after JSON stored."""
    pic = _insert_picture(server)
    _clear_changelog(server)

    def _add_quality(session):
        q = Quality(picture_id=pic.id)
        session.add(q)
        session.commit()

    server.vault.db.run_task(_add_quality)

    rows = _all_changelog(server)
    quality_rows = [r for r in rows if r.table_name == "quality"]
    # Any recorded row must have no before/after payload.
    for row in quality_rows:
        assert row.before_json is None, "Excluded table must not store before_json"
        assert row.after_json is None, "Excluded table must not store after_json"


# ---------------------------------------------------------------------------
# Multiple ops in one task share txn_id; seq_in_txn is monotonic
# ---------------------------------------------------------------------------


def test_multi_op_transaction_shares_txn_id(server):
    def _multi(session):
        for i in range(3):
            session.add(Picture(file_path=f"multi_{i}.jpg", filename=f"multi_{i}.jpg"))
        session.commit()

    server.vault.db.run_task(_multi)

    rows = _all_changelog(server)
    picture_rows = [r for r in rows if r.table_name == "picture"]
    assert len(picture_rows) == 3

    txn_ids = {r.txn_id for r in picture_rows}
    assert len(txn_ids) == 1, "All rows in one task must share a txn_id"

    seqs = [r.seq_in_txn for r in picture_rows]
    assert seqs == sorted(seqs), "seq_in_txn must be monotonically increasing"


# ---------------------------------------------------------------------------
# write_reason annotates all rows in the context
# ---------------------------------------------------------------------------


def test_write_reason_annotates_changelog_rows(server):
    with server.vault.db.write_reason("my explicit reason"):
        _insert_picture(server, filename="reason_test.jpg")

    rows = _all_changelog(server)
    picture_rows = [r for r in rows if r.table_name == "picture"]
    assert picture_rows, "No picture rows in changelog"
    assert all(r.reason == "my explicit reason" for r in picture_rows)


# ---------------------------------------------------------------------------
# Without write_reason, reason field is None
# ---------------------------------------------------------------------------


def test_no_write_reason_leaves_reason_null(server):
    _insert_picture(server, filename="no_reason.jpg")

    rows = _all_changelog(server)
    picture_rows = [r for r in rows if r.table_name == "picture"]
    assert picture_rows
    assert all(r.reason is None for r in picture_rows)
