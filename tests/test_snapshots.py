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
        config_path = f"{tmp}/server-config.json"
        # Disable background workers so finders (QualityTask etc.) don't write
        # to `picture` between a test's last write and the undo/restore call,
        # which would break exact ChangeLog count assertions.
        with open(config_path, "w") as fh:
            json.dump({"disable_background_workers": True}, fh)
        with Server(config_path) as srv:
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
    with open(abs_manifest) as fh:
        manifest = json.load(fh)

    assert "max_changelog_id" in manifest
    assert "picture_count" in manifest
    assert "picture_ids" in manifest
    assert "schema_version" in manifest


def test_snapshot_picture_count_matches(server):
    _add_pictures(server, count=4)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    assert cp.picture_count == 4

    abs_manifest = os.path.join(server.vault.image_root, cp.manifest_relative_path)
    with open(abs_manifest) as fh:
        manifest = json.load(fh)
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


# ---------------------------------------------------------------------------
# GFS retention: DAILY snapshots beyond the keep limit are pruned
# ---------------------------------------------------------------------------


def test_gfs_retention_prunes_oldest_daily(server):
    """Creating > GFS_KEEP_DAILY DAILY snapshots prunes the oldest.

    The prune happens at the tail of ``create_snapshot``. We backdate the
    created_at on the older snapshots so the prune has a deterministic
    notion of "oldest" (creating them back-to-back can collapse to the
    same microsecond).
    """
    from datetime import datetime, timedelta, timezone

    from pixlstash.services.snapshot_service import GFS_KEEP_DAILY

    cps = []
    for i in range(GFS_KEEP_DAILY + 2):
        cp = server.vault.snapshot_service.create_snapshot("DAILY")
        cps.append(cp)
        # Backdate this snapshot so the next create's prune sees clear ordering.

        def _backdate(session, cp_id=cp.id, i=i):
            row = session.get(Snapshot, cp_id)
            if row is not None:
                row.created_at = datetime.now(timezone.utc) - timedelta(
                    days=GFS_KEEP_DAILY + 2 - i
                )
                session.add(row)
                session.commit()

        server.vault.db.run_task(_backdate)

    # Trigger one more prune by creating a final DAILY snapshot.
    server.vault.snapshot_service.create_snapshot("DAILY")

    surviving = [
        s for s in server.vault.snapshot_service.list_snapshots() if s.kind == "DAILY"
    ]
    assert len(surviving) == GFS_KEEP_DAILY, (
        f"Expected exactly {GFS_KEEP_DAILY} DAILY snapshots after prune, "
        f"got {len(surviving)}"
    )

    # The two oldest of our original batch must have been pruned.
    surviving_ids = {s.id for s in surviving}
    assert cps[0].id not in surviving_ids
    assert cps[1].id not in surviving_ids


# ---------------------------------------------------------------------------
# undo_to_snapshot: ChangeLog truncated past the target → file restore (H1)
# ---------------------------------------------------------------------------


def test_undo_to_snapshot_escalates_when_changelog_truncated(server):
    """If ``min(ChangeLog.id) > target.max_changelog_id + 1`` the entries
    needed to rewind through the change log are gone. ``undo_to_snapshot``
    must escalate to ``restore_full`` rather than silently produce a partial
    rewind."""
    _add_pictures(server, count=1)
    # Place a file on disk so the full-restore fallback doesn't drop the row.
    open(os.path.join(server.vault.image_root, "pic_0.jpg"), "wb").close()

    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Read the snapshot's max_changelog_id, then drop the entire ChangeLog
    # so the next surviving id (currently min — None / 0) is far above the
    # target's max_changelog_id + 1. We'll also insert a few "ghost" rows
    # past the target's max to make min(ChangeLog.id) strictly greater than
    # max_changelog_id + 1.
    abs_manifest = os.path.join(server.vault.image_root, cp.manifest_relative_path)
    with open(abs_manifest) as fh:
        manifest = json.load(fh)
    target_max = manifest["max_changelog_id"]
    assert target_max is not None and target_max >= 0

    def _simulate_truncation(session):
        session.exec(delete(ChangeLog))
        # Insert a stand-in row whose id is well above target_max + 1 to
        # mimic a post-prune state where the surviving min is too high.
        ghost_id = target_max + 1000
        session.exec(
            text(
                "INSERT INTO changelog (id, txn_id, seq_in_txn, table_name, "
                "row_pk_json, op, before_json, after_json, created_at) VALUES "
                "(:id, 'ghost', 0, 'picture', '{}', 'UPDATE', NULL, NULL, datetime('now'))"
            ).bindparams(id=ghost_id)
        )
        session.commit()

    server.vault.db.run_task(_simulate_truncation)

    report = server.vault.undo_service.undo_to_snapshot(cp.id)

    assert report.escalated_to_full_restore, (
        "Expected undo_to_snapshot to escalate to full restore when ChangeLog "
        f"is truncated past the target. Got report: {report}"
    )
    assert report.escalated_tables == ["<changelog-truncated>"], (
        f"Expected escalation marker, got: {report.escalated_tables}"
    )


# ---------------------------------------------------------------------------
# GFS retention: OPPORTUNISTIC snapshots are capped to GFS_KEEP_OPPORTUNISTIC
# ---------------------------------------------------------------------------


def test_gfs_retention_prunes_oldest_opportunistic(server):
    """OPPORTUNISTIC snapshots accumulate from safety-snapshot-before-restore
    and ``snapshot_if_due()``. Without a cap they pin the ChangeLog
    truncation floor (see ``_apply_gfs_retention``) and grow unbounded.
    """
    from datetime import datetime, timedelta, timezone

    from pixlstash.services.snapshot_service import GFS_KEEP_OPPORTUNISTIC

    cps = []
    for i in range(GFS_KEEP_OPPORTUNISTIC + 2):
        cp = server.vault.snapshot_service.create_snapshot("OPPORTUNISTIC")
        cps.append(cp)

        def _backdate(session, cp_id=cp.id, i=i):
            row = session.get(Snapshot, cp_id)
            if row is not None:
                row.created_at = datetime.now(timezone.utc) - timedelta(
                    hours=GFS_KEEP_OPPORTUNISTIC + 2 - i
                )
                session.add(row)
                session.commit()

        server.vault.db.run_task(_backdate)

    # Trigger one more prune by creating a final OPPORTUNISTIC.
    server.vault.snapshot_service.create_snapshot("OPPORTUNISTIC")

    surviving = [
        s
        for s in server.vault.snapshot_service.list_snapshots()
        if s.kind == "OPPORTUNISTIC"
    ]
    assert len(surviving) == GFS_KEEP_OPPORTUNISTIC, (
        f"Expected exactly {GFS_KEEP_OPPORTUNISTIC} OPPORTUNISTIC after prune, "
        f"got {len(surviving)}"
    )
    surviving_ids = {s.id for s in surviving}
    assert cps[0].id not in surviving_ids
    assert cps[1].id not in surviving_ids


def test_gfs_retention_does_not_prune_manual(server):
    """MANUAL snapshots are user-curated and must never be auto-pruned, even
    when many of them exist."""
    cps = [
        server.vault.snapshot_service.create_snapshot("MANUAL", label=f"m{i}")
        for i in range(10)
    ]
    # Triggering another snapshot must not touch the MANUAL ones.
    server.vault.snapshot_service.create_snapshot("MANUAL", label="trigger")

    surviving_manual = [
        s for s in server.vault.snapshot_service.list_snapshots() if s.kind == "MANUAL"
    ]
    assert len(surviving_manual) == 11
    surviving_ids = {s.id for s in surviving_manual}
    for cp in cps:
        assert cp.id in surviving_ids, (
            f"MANUAL snapshot {cp.id} was pruned but MANUAL must never auto-prune"
        )
