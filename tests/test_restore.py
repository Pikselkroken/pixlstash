"""Tests for RestoreService — full and per-resource restore."""

import json
import os
import shutil
import tempfile

import pytest
from sqlalchemy import text
from sqlmodel import delete

from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureSet,
    PictureSetMember,
    Project,
)
from pixlstash.db_models.picture_project import PictureProjectMember
from pixlstash.db_models.tag import Tag
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
    """Wipe all relevant tables and snapshot files before each test."""

    def _wipe(session):
        session.exec(text("PRAGMA foreign_keys = OFF"))
        session.exec(delete(Snapshot))
        session.exec(delete(ChangeLog))
        session.exec(delete(PictureProjectMember))
        session.exec(delete(PictureSetMember))
        session.exec(delete(Face))
        session.exec(delete(Tag))
        session.exec(delete(Picture))
        session.exec(delete(PictureSet))
        session.exec(delete(Project))
        session.exec(delete(Character))
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


def _add_picture(server, filename="test.jpg", description=None) -> Picture:
    def _do(session):
        pic = Picture(file_path=filename, filename=filename, description=description)
        session.add(pic)
        session.commit()
        session.refresh(pic)
        return pic

    return server.vault.db.run_task(_do)


def _get_picture(server, pic_id: int):
    return server.vault.db.run_immediate_read_task(lambda s: s.get(Picture, pic_id))


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
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

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
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    report = server.vault.restore_service.restore_full(cp.id)

    assert report.missing_files_count == 1, (
        f"Expected 1 missing-file picture, got {report.missing_files_count}"
    )
    remaining = _get_picture(server, pic.id)
    assert remaining is None, (
        "Row for missing-file picture must be removed after restore"
    )


# ---------------------------------------------------------------------------
# Full restore with dry_run=True: DB is not modified
# ---------------------------------------------------------------------------


def test_full_restore_dry_run_leaves_db_unchanged(server):
    _create_file(server, "dry.jpg")
    pic = _add_picture(server, filename="dry.jpg", description="original")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

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
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

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
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

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
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")
    with pytest.raises(ValueError, match="Invalid resource_type"):
        server.vault.restore_service.restore_resource(cp.id, "unknown_type", 1)


# ---------------------------------------------------------------------------
# Per-resource restore: dependents (Face, Tag, PSM, PPM) mirror snapshot
# ---------------------------------------------------------------------------


def test_restore_resource_picture_replaces_dependents(server):
    """Picture restore must mirror the snapshot's Face/Tag/PSM/PPM state.

    This is the H3 fix: previously, ``_upsert_rows`` merged Faces / picture
    set members / picture project members by snapshot PK. That left live-only
    rows in place (so the restored picture wasn't really reverted) and could
    overwrite an unrelated live Face that reused the same surrogate id. The
    fix is delete-then-insert keyed by ``picture_id``.
    """
    _create_file(server, "h3_pic.jpg")
    other = _add_picture(server, filename="h3_other.jpg")
    _create_file(server, "h3_other.jpg")
    pic = _add_picture(server, filename="h3_pic.jpg", description="orig")

    def _setup_snapshot_state(session):
        # Snapshot state for pic: 2 tags, 1 face, member of set_a.
        session.add(Tag(picture_id=pic.id, tag="keep1"))
        session.add(Tag(picture_id=pic.id, tag="keep2"))
        session.add(Face(picture_id=pic.id, frame_index=0, face_index=0))
        set_a = PictureSet(name="set_a")
        session.add(set_a)
        session.commit()
        session.refresh(set_a)
        session.add(PictureSetMember(set_id=set_a.id, picture_id=pic.id))
        session.commit()
        return set_a.id

    set_a_id = server.vault.db.run_task(_setup_snapshot_state)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _diverge(session):
        # Drop "keep2"; add a new tag, a new face, and reassign to a new set.
        for t in session.exec(
            text(
                "SELECT id FROM tag WHERE picture_id = :pid AND tag = 'keep2'"
            ).bindparams(pid=pic.id)
        ).all():
            session.exec(text("DELETE FROM tag WHERE id = :id").bindparams(id=t.id))
        session.add(Tag(picture_id=pic.id, tag="live_only"))
        session.add(Face(picture_id=pic.id, frame_index=1, face_index=0))
        set_b = PictureSet(name="set_b")
        session.add(set_b)
        session.commit()
        session.refresh(set_b)
        session.exec(
            text("DELETE FROM picturesetmember WHERE picture_id = :pid").bindparams(
                pid=pic.id
            )
        )
        session.add(PictureSetMember(set_id=set_b.id, picture_id=pic.id))
        # Add a Face on an UNRELATED picture so we can confirm it survives.
        session.add(Face(picture_id=other.id, frame_index=0, face_index=0))
        session.commit()

    server.vault.db.run_task(_diverge)

    report = server.vault.restore_service.restore_resource(cp.id, "picture", pic.id)
    assert not report.errors, f"restore_resource errors: {report.errors}"

    def _check(session):
        live_tags = sorted(
            t.tag
            for t in session.exec(
                text("SELECT tag FROM tag WHERE picture_id = :pid").bindparams(
                    pid=pic.id
                )
            ).all()
        )
        live_faces = session.exec(
            text(
                "SELECT id, frame_index, face_index FROM face WHERE picture_id = :pid"
            ).bindparams(pid=pic.id)
        ).all()
        live_psm_set_ids = [
            r.set_id
            for r in session.exec(
                text(
                    "SELECT set_id FROM picturesetmember WHERE picture_id = :pid"
                ).bindparams(pid=pic.id)
            ).all()
        ]
        other_faces = session.exec(
            text("SELECT id FROM face WHERE picture_id = :pid").bindparams(pid=other.id)
        ).all()
        return live_tags, live_faces, live_psm_set_ids, other_faces

    live_tags, live_faces, live_psm_set_ids, other_faces = (
        server.vault.db.run_immediate_read_task(_check)
    )

    # Tags: only the two snapshot tags remain; live-only tag dropped.
    assert live_tags == ["keep1", "keep2"], f"got tags {live_tags}"
    # Faces: only the snapshot's one face remains; live-added face is gone.
    assert len(live_faces) == 1, f"got {len(live_faces)} faces, expected 1"
    assert live_faces[0].frame_index == 0
    # PSM: only the snapshot's set_a membership, not the live set_b membership.
    assert live_psm_set_ids == [set_a_id], (
        f"expected membership in [{set_a_id}], got {live_psm_set_ids}"
    )
    # Unrelated picture's face survives — restore is scoped to valid_picture_ids.
    assert len(other_faces) == 1, (
        "Face on unrelated picture must survive a picture-scoped restore"
    )


# ---------------------------------------------------------------------------
# Per-resource restore: picture_set restores members
# ---------------------------------------------------------------------------


def test_restore_resource_picture_set(server):
    _create_file(server, "set_p1.jpg")
    _create_file(server, "set_p2.jpg")
    p1 = _add_picture(server, filename="set_p1.jpg")
    p2 = _add_picture(server, filename="set_p2.jpg")

    def _setup(session):
        s = PictureSet(name="my_set", description="snapshot version")
        session.add(s)
        session.commit()
        session.refresh(s)
        session.add(PictureSetMember(set_id=s.id, picture_id=p1.id))
        session.add(PictureSetMember(set_id=s.id, picture_id=p2.id))
        session.commit()
        return s.id

    set_id = server.vault.db.run_task(_setup)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _mutate(session):
        s = session.get(PictureSet, set_id)
        s.description = "live divergence"
        session.commit()

    server.vault.db.run_task(_mutate)

    report = server.vault.restore_service.restore_resource(cp.id, "picture_set", set_id)
    assert not report.errors, f"errors: {report.errors}"

    restored = server.vault.db.run_immediate_read_task(
        lambda s: s.get(PictureSet, set_id)
    )
    assert restored is not None
    assert restored.description == "snapshot version"


# ---------------------------------------------------------------------------
# Per-resource restore: character row restored
# ---------------------------------------------------------------------------


def test_restore_resource_character(server):
    def _add_char(session):
        c = Character(name="Alice")
        session.add(c)
        session.commit()
        session.refresh(c)
        return c.id

    char_id = server.vault.db.run_task(_add_char)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _mutate(session):
        c = session.get(Character, char_id)
        c.name = "Bob"
        session.commit()

    server.vault.db.run_task(_mutate)

    report = server.vault.restore_service.restore_resource(cp.id, "character", char_id)
    assert not report.errors, f"errors: {report.errors}"

    restored = server.vault.db.run_immediate_read_task(
        lambda s: s.get(Character, char_id)
    )
    assert restored.name == "Alice"


# ---------------------------------------------------------------------------
# restore_batch: mixed resource types in one call
# ---------------------------------------------------------------------------


def test_restore_batch_mixed_types(server):
    _create_file(server, "batch_p.jpg")
    pic = _add_picture(server, filename="batch_p.jpg", description="orig")

    def _setup(session):
        c = Character(name="Eve")
        session.add(c)
        session.commit()
        session.refresh(c)
        return c.id

    char_id = server.vault.db.run_task(_setup)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _mutate(session):
        session.get(Picture, pic.id).description = "after"
        session.get(Character, char_id).name = "Mallory"
        session.commit()

    server.vault.db.run_task(_mutate)

    report = server.vault.restore_service.restore_batch(
        cp.id,
        [
            {"type": "picture", "id": pic.id},
            {"type": "character", "id": char_id},
        ],
    )
    assert not report.errors, f"errors: {report.errors}"

    def _check(session):
        return (
            session.get(Picture, pic.id).description,
            session.get(Character, char_id).name,
        )

    desc, name = server.vault.db.run_immediate_read_task(_check)
    assert desc == "orig"
    assert name == "Eve"


# ---------------------------------------------------------------------------
# Concurrent restore is rejected with RestoreInProgressError (C2 guardrail)
# ---------------------------------------------------------------------------


def test_concurrent_restore_rejected_with_409(server):
    """A second restore call while one is in flight must short-circuit with
    ``RestoreInProgressError`` (the in-flight one still completes normally).
    Guards C2: without the per-instance lock, two swap + cleanup pipelines
    would interleave on the writer thread."""
    from pixlstash.services.restore_service import RestoreInProgressError

    _create_file(server, "lock_test.jpg")
    _add_picture(server, filename="lock_test.jpg", description="v1")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    svc = server.vault.restore_service
    acquired = svc._restore_lock.acquire(blocking=False)
    assert acquired, "Pre-test: restore lock should be free"
    try:
        with pytest.raises(RestoreInProgressError):
            svc.restore_full(cp.id)
    finally:
        svc._restore_lock.release()


# ---------------------------------------------------------------------------
# _upgrade_snapshot_schema: alembic-upgrade-on-restore actually runs
# ---------------------------------------------------------------------------


def test_upgrade_snapshot_schema_runs_alembic_on_old_snapshot(server):
    """``_upgrade_snapshot_schema`` must successfully alembic-upgrade a
    snapshot whose schema is behind ``head``. We synthesize that by taking
    a current snapshot, dropping the ``metadata_hash`` column and the
    ``alembic_version`` row that records its migration, then asserting that
    the upgraded temp copy has the column back."""
    import sqlite3

    from sqlalchemy import inspect as sa_inspect
    from sqlmodel import create_engine

    _create_file(server, "schema_upgrade.jpg")
    _add_picture(server, filename="schema_upgrade.jpg")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    abs_snapshot = os.path.join(server.vault.image_root, cp.relative_path)
    assert os.path.isfile(abs_snapshot)

    # Strip the metadata_hash column AND back-date alembic_version to before
    # the migration that added it (0049_snapshots → previous head 0048).
    with sqlite3.connect(abs_snapshot) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(picture)").fetchall()}
        assert "metadata_hash" in cols, (
            "Pre-test invariant: current schema has metadata_hash"
        )
        conn.execute("ALTER TABLE picture DROP COLUMN metadata_hash")
        conn.execute(
            "UPDATE alembic_version SET version_num = '0048_normalize_stack_positions'"
        )
        conn.commit()

    # Sanity: column is gone from the snapshot file.
    probe = create_engine(f"sqlite:///{abs_snapshot}", echo=False)
    try:
        cols_before = {c["name"] for c in sa_inspect(probe).get_columns("picture")}
    finally:
        probe.dispose()
    assert "metadata_hash" not in cols_before

    upgraded = server.vault.restore_service._upgrade_snapshot_schema(abs_snapshot)
    assert upgraded is not None, "Schema upgrade returned None"
    try:
        probe2 = create_engine(f"sqlite:///{upgraded}", echo=False)
        try:
            cols_after = {c["name"] for c in sa_inspect(probe2).get_columns("picture")}
        finally:
            probe2.dispose()
        assert "metadata_hash" in cols_after, (
            "_upgrade_snapshot_schema must add columns introduced by later "
            f"migrations; got columns: {sorted(cols_after)}"
        )
    finally:
        shutil.rmtree(os.path.dirname(upgraded), ignore_errors=True)
