"""Tests for RestoreService — full and per-resource restore."""

import json
import os
import shutil
import tempfile
from contextlib import closing

import pytest
from sqlalchemy import text
from sqlmodel import delete, select

from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureSet,
    PictureSetMember,
    Project,
)
from pixlstash.db_models.picture_likeness import (
    PictureLikeness,
    PictureLikenessFrontier,
    PictureLikenessQueue,
)
from pixlstash.db_models.picture_project import PictureProjectMember
from pixlstash.db_models.tag import Tag
from pixlstash.db_models.snapshot import Snapshot
from pixlstash.server import Server


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = f"{tmp}/server-config.json"
        # Disable background workers so finders (QualityTask etc.) don't write
        # to `picture` between a test's last write and the restore call.
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
        # Likeness pipeline rows are populated by restore_full (via
        # ensure_all), so they accumulate across tests. Without an
        # explicit wipe — FKs are OFF here, so CASCADE doesn't fire —
        # they orphan and collide with the next test's replay.
        session.exec(delete(PictureLikeness))
        session.exec(delete(PictureLikenessQueue))
        session.exec(delete(PictureLikenessFrontier))
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
    with pytest.raises(ValueError, match="Unsupported resource_type"):
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
    """Two concurrent ``restore_full`` calls from different threads: one
    wins, the other raises ``RestoreInProgressError``.

    Guards the production race that the prior single-thread lock-acquire
    test only mimicked: without the per-service lock, two swap + cleanup
    pipelines would interleave on the writer thread (live-DB corruption).
    """
    import threading

    from pixlstash.services.restore_service import RestoreInProgressError

    _create_file(server, "concurrent.jpg")
    _add_picture(server, filename="concurrent.jpg", description="v1")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    svc = server.vault.restore_service
    results: list = [None, None]
    errors: list = [None, None]

    def _do_restore(idx: int):
        try:
            results[idx] = svc.restore_full(cp.id)
        except Exception as exc:
            errors[idx] = exc

    t0 = threading.Thread(target=_do_restore, args=(0,))
    t1 = threading.Thread(target=_do_restore, args=(1,))
    t0.start()
    t1.start()
    t0.join(timeout=30)
    t1.join(timeout=30)

    assert not t0.is_alive() and not t1.is_alive(), (
        "Both restore threads must finish in 30s"
    )

    successes = [r for r in results if r is not None]
    rejections = [e for e in errors if isinstance(e, RestoreInProgressError)]
    unexpected = [
        e for e in errors if e is not None and not isinstance(e, RestoreInProgressError)
    ]

    assert not unexpected, f"Unexpected error from a restore thread: {unexpected}"
    assert len(successes) == 1, (
        f"Exactly one restore must succeed; got {len(successes)} successes, "
        f"results={results}, errors={errors}"
    )
    assert len(rejections) == 1, (
        f"Exactly one restore must be rejected with RestoreInProgressError; "
        f"got {len(rejections)}, errors={errors}"
    )


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

    from pixlstash.utils.snapshot_compression import materialize_snapshot

    _create_file(server, "schema_upgrade.jpg")
    _add_picture(server, filename="schema_upgrade.jpg")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Materialize to a plain .sqlite (the legacy on-disk form) so we can mutate
    # it to simulate an old-schema snapshot; _upgrade_snapshot_schema accepts
    # both compressed and plain inputs.
    work_dir = tempfile.mkdtemp(prefix="pixlstash_test_oldschema_")
    abs_snapshot = os.path.join(work_dir, "snapshot.sqlite")
    materialize_snapshot(
        os.path.join(server.vault.image_root, cp.relative_path), abs_snapshot
    )
    assert os.path.isfile(abs_snapshot)

    # Strip the metadata_hash column AND back-date alembic_version to before
    # the migration that added it (0049_snapshots → previous head 0048).
    with closing(sqlite3.connect(abs_snapshot)) as conn:
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
        shutil.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Per-resource restore: "project" is intentionally unsupported in this release
# ---------------------------------------------------------------------------


def test_restore_resource_project_rejected(server):
    """``resource_type='project'`` must raise ``ValueError`` and the route
    handler must map that to a 400. Project's graph (ProjectAttachment +
    Character.project_id + PictureSet.project_id + PPM) isn't yet rebuilt by
    the per-resource path; use the full restore until that's implemented.
    """
    _create_file(server, "proj.jpg")
    pic = _add_picture(server, filename="proj.jpg")

    def _setup(session):
        proj = Project(name="MyProject", description="snap")
        session.add(proj)
        session.commit()
        session.refresh(proj)
        session.add(PictureProjectMember(project_id=proj.id, picture_id=pic.id))
        session.commit()
        return proj.id

    proj_id = server.vault.db.run_task(_setup)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    with pytest.raises(ValueError, match="Unsupported resource_type 'project'"):
        server.vault.restore_service.restore_resource(cp.id, "project", proj_id)

    # preview_resource must also reject.
    with pytest.raises(ValueError, match="Unsupported resource_type 'project'"):
        server.vault.restore_service.preview_resource(cp.id, "project", proj_id)


def test_restore_batch_skips_project_entries(server):
    """Mixed batch with a project entry must record an error for the project
    and complete the supported entries (no halt-on-first-error)."""
    _create_file(server, "batch_pic.jpg")
    pic = _add_picture(server, filename="batch_pic.jpg", description="orig")

    def _setup(session):
        proj = Project(name="BatchProject")
        session.add(proj)
        session.commit()
        session.refresh(proj)
        return proj.id

    proj_id = server.vault.db.run_task(_setup)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Mutate the picture so the batch restore has something to undo.
    def _mutate(session):
        session.get(Picture, pic.id).description = "mutated"
        session.commit()

    server.vault.db.run_task(_mutate)

    report = server.vault.restore_service.restore_batch(
        cp.id,
        [
            {"type": "project", "id": proj_id},
            {"type": "picture", "id": pic.id},
        ],
    )
    assert any("project" in e for e in report.errors), (
        f"Expected a 'project' rejection in errors, got {report.errors}"
    )
    assert report.upserted_count >= 1, (
        "The picture entry must still have been restored despite the "
        "project entry being rejected."
    )
    restored = _get_picture(server, pic.id)
    assert restored.description == "orig"


# ---------------------------------------------------------------------------
# compare_hashes: NULL backfill on live + snapshot, identical vs changed
# ---------------------------------------------------------------------------


def test_compare_hashes_backfills_null_live_and_returns_identical(server):
    """The snapshot-side hash now comes from the manifest's precomputed map
    (so an interactive compare never decompresses the archive). The live side
    may still be NULL on pre-migration rows; ``compare_hashes`` must compute
    and persist the live hash, then report the picture identical because the
    live and manifest hashes match.
    """
    from sqlalchemy import update as sa_update

    _create_file(server, "cmp.jpg")
    pic = _add_picture(server, filename="cmp.jpg", description="same")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # The hash sidecar must carry a precomputed hash for this picture, and the
    # manifest itself must stay lean (no embedded hash map).
    sidecar = server.vault.snapshot_service.load_picture_hashes(cp.id)
    assert str(pic.id) in sidecar, "Snapshot must carry a per-picture hash sidecar"
    assert "picture_hashes" not in server.vault.snapshot_service.load_manifest(cp.id), (
        "Manifest must not embed the hash map (kept in a sidecar)"
    )

    # Force the live hash to NULL so we exercise the live backfill path.
    def _null_live(session):
        session.execute(sa_update(Picture).values(metadata_hash=None))
        session.commit()

    server.vault.db.run_task(_null_live)

    result = server.vault.restore_service.compare_hashes(cp.id, [pic.id])
    assert result["identical_ids"] == [pic.id], (
        f"Picture must be reported identical after live backfill; got {result}"
    )
    assert result["changed_ids"] == []

    # The live hash must have been persisted by the bulk Core UPDATE.
    persisted = server.vault.db.run_immediate_read_task(
        lambda s: s.get(Picture, pic.id).metadata_hash
    )
    assert persisted is not None, "compare_hashes must persist the backfilled live hash"


def test_compare_hashes_detects_mutation(server):
    """A picture mutated after the snapshot must land in ``changed_ids``."""
    _create_file(server, "cmp_diff.jpg")
    pic = _add_picture(server, filename="cmp_diff.jpg", description="orig")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _mutate(session):
        session.get(Picture, pic.id).description = "different"
        session.commit()

    server.vault.db.run_task(_mutate)

    result = server.vault.restore_service.compare_hashes(cp.id, [pic.id])
    assert result["changed_ids"] == [pic.id], (
        f"Mutated picture must be reported as changed; got {result}"
    )
    assert result["identical_ids"] == []


# ---------------------------------------------------------------------------
# Restore preview: ``is_compatible`` flag reflects schema_version comparison
# ---------------------------------------------------------------------------


def test_preview_is_compatible_false_when_snapshot_newer_than_live(server):
    """``is_compatible`` must be ``false`` when the snapshot's
    ``schema_version`` sorts strictly above the live alembic head — a
    snapshot from a future schema cannot be downgraded.
    """
    from pixlstash.routes.snapshots import _serialize_snapshot

    _create_file(server, "compat.jpg")
    _add_picture(server, filename="compat.jpg")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    live_schema = server.vault.snapshot_service.get_live_schema_version()
    assert live_schema, "Pre-test: live schema_version must be populated"

    # Force a synthetic schema_version that sorts above the live one.
    future_version = "zzzz_future_schema"

    def _bump(session):
        s = session.get(Snapshot, cp.id)
        s.schema_version = future_version
        session.commit()

    server.vault.db.run_task(_bump)

    cp_reloaded = server.vault.snapshot_service.get_snapshot(cp.id)
    payload = _serialize_snapshot(
        cp_reloaded,
        server.vault.snapshot_service.load_manifest(cp.id),
        live_schema,
    )
    assert payload["is_compatible"] is False, (
        f"Snapshot {future_version} > live {live_schema} must be reported "
        f"incompatible; got {payload}"
    )


def test_preview_is_compatible_true_when_schemas_match(server):
    """The happy path: snapshot schema == live schema → is_compatible=True."""
    from pixlstash.routes.snapshots import _serialize_snapshot

    _create_file(server, "compat_ok.jpg")
    _add_picture(server, filename="compat_ok.jpg")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    live_schema = server.vault.snapshot_service.get_live_schema_version()
    payload = _serialize_snapshot(
        cp,
        server.vault.snapshot_service.load_manifest(cp.id),
        live_schema,
    )
    assert payload["is_compatible"] is True


# ---------------------------------------------------------------------------
# Missing-dependencies prompt (per-resource and batch)
# ---------------------------------------------------------------------------


def test_restore_resource_picture_with_missing_character_raises_without_confirm(
    server,
):
    """Per-A2: a snapshot picture's Face references a character that the user
    has since deleted. Without ``confirm_restore_dependencies=True``, the
    service must refuse to write anything and raise
    ``MissingDependenciesError`` carrying the missing character ids.
    """
    from pixlstash.services.restore_service import MissingDependenciesError

    _create_file(server, "with_char.jpg")
    pic = _add_picture(server, filename="with_char.jpg")

    # Snapshot: picture has a face attached to character 'alice'.
    def _setup_snapshot_state(session):
        c = Character(name="alice")
        session.add(c)
        session.commit()
        session.refresh(c)
        session.add(
            Face(
                picture_id=pic.id,
                frame_index=0,
                face_index=0,
                character_id=c.id,
                bbox_="[0,0,10,10]",
            )
        )
        session.commit()
        return c.id

    alice_id = server.vault.db.run_task(_setup_snapshot_state)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Live: user deletes the character (Face.character_id cascades to NULL
    # in the live DB but the snapshot still has the reference).
    def _delete_char(session):
        session.exec(delete(Character).where(Character.id == alice_id))
        session.commit()

    server.vault.db.run_task(_delete_char)

    # Default call refuses with MissingDependenciesError.
    with pytest.raises(MissingDependenciesError) as exc_info:
        server.vault.restore_service.restore_resource(cp.id, "picture", pic.id)
    assert "characters" in exc_info.value.missing, (
        f"Expected missing characters, got: {exc_info.value.missing}"
    )
    assert alice_id in exc_info.value.missing["characters"]

    # Live state must be untouched: character still absent, face still without
    # a character (the missing-deps probe must NOT write anything).
    chars_after = server.vault.db.run_immediate_read_task(
        lambda s: s.exec(select(Character)).all()
    )
    assert chars_after == [], (
        "Refused restore must leave the live DB untouched; "
        f"characters now exist: {chars_after}"
    )


def test_restore_resource_picture_confirm_restores_missing_character(server):
    """With ``confirm_restore_dependencies=True``, the service first re-inserts
    the missing character from the snapshot and then upserts the picture's
    faces — both end up in the live DB."""
    _create_file(server, "with_char2.jpg")
    pic = _add_picture(server, filename="with_char2.jpg")

    def _setup_snapshot_state(session):
        c = Character(name="bob")
        session.add(c)
        session.commit()
        session.refresh(c)
        session.add(
            Face(
                picture_id=pic.id,
                frame_index=0,
                face_index=0,
                character_id=c.id,
                bbox_="[0,0,20,20]",
            )
        )
        session.commit()
        return c.id

    bob_id = server.vault.db.run_task(_setup_snapshot_state)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _delete_char(session):
        session.exec(delete(Character).where(Character.id == bob_id))
        session.commit()

    server.vault.db.run_task(_delete_char)

    report = server.vault.restore_service.restore_resource(
        cp.id,
        "picture",
        pic.id,
        confirm_restore_dependencies=True,
    )
    assert report.upserted_count > 0

    # Character must be back, with its name preserved.
    char_after = server.vault.db.run_immediate_read_task(
        lambda s: s.get(Character, bob_id)
    )
    assert char_after is not None and char_after.name == "bob", (
        f"Confirmed restore must re-insert the missing character; got {char_after}"
    )

    # And the picture's face must reference it.
    face_after = server.vault.db.run_immediate_read_task(
        lambda s: s.exec(select(Face).where(Face.picture_id == pic.id)).first()
    )
    assert face_after is not None and face_after.character_id == bob_id


def test_restore_batch_unions_missing_dependencies_across_items(server):
    """The batch path must collect the union of missing parents across all
    items and raise once with the combined dict, not item-by-item."""
    from pixlstash.services.restore_service import MissingDependenciesError

    _create_file(server, "batch_a.jpg")
    _create_file(server, "batch_b.jpg")
    pa = _add_picture(server, filename="batch_a.jpg")
    pb = _add_picture(server, filename="batch_b.jpg")

    def _setup(session):
        c1 = Character(name="char_a")
        c2 = Character(name="char_b")
        session.add(c1)
        session.add(c2)
        session.commit()
        session.refresh(c1)
        session.refresh(c2)
        session.add(
            Face(
                picture_id=pa.id,
                frame_index=0,
                face_index=0,
                character_id=c1.id,
                bbox_="[0,0,1,1]",
            )
        )
        session.add(
            Face(
                picture_id=pb.id,
                frame_index=0,
                face_index=0,
                character_id=c2.id,
                bbox_="[0,0,1,1]",
            )
        )
        session.commit()
        return c1.id, c2.id

    c1_id, c2_id = server.vault.db.run_task(_setup)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _delete_both(session):
        session.exec(delete(Character))
        session.commit()

    server.vault.db.run_task(_delete_both)

    resources = [
        {"type": "picture", "id": pa.id},
        {"type": "picture", "id": pb.id},
    ]
    with pytest.raises(MissingDependenciesError) as exc_info:
        server.vault.restore_service.restore_batch(cp.id, resources)
    missing_chars = set(exc_info.value.missing.get("characters", []))
    assert {c1_id, c2_id}.issubset(missing_chars), (
        "Batch missing-deps union must include BOTH characters; "
        f"got {exc_info.value.missing}"
    )


def test_restore_batch_confirm_restores_all_missing_parents_once(server):
    """With confirm=True, the batch path restores the union of missing
    parents in one pre-pass, then upserts each item — no per-item retries."""
    _create_file(server, "batch_c.jpg")
    _create_file(server, "batch_d.jpg")
    pc = _add_picture(server, filename="batch_c.jpg")
    pd = _add_picture(server, filename="batch_d.jpg")

    def _setup(session):
        c1 = Character(name="char_c")
        c2 = Character(name="char_d")
        session.add(c1)
        session.add(c2)
        session.commit()
        session.refresh(c1)
        session.refresh(c2)
        session.add(
            Face(
                picture_id=pc.id,
                frame_index=0,
                face_index=0,
                character_id=c1.id,
                bbox_="[0,0,1,1]",
            )
        )
        session.add(
            Face(
                picture_id=pd.id,
                frame_index=0,
                face_index=0,
                character_id=c2.id,
                bbox_="[0,0,1,1]",
            )
        )
        session.commit()
        return c1.id, c2.id

    c1_id, c2_id = server.vault.db.run_task(_setup)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _delete_both(session):
        session.exec(delete(Character))
        session.commit()

    server.vault.db.run_task(_delete_both)

    resources = [
        {"type": "picture", "id": pc.id},
        {"type": "picture", "id": pd.id},
    ]
    report = server.vault.restore_service.restore_batch(
        cp.id, resources, confirm_restore_dependencies=True
    )
    assert report.errors == [], f"batch errors should be empty, got {report.errors}"

    chars_after = server.vault.db.run_immediate_read_task(
        lambda s: {c.id: c.name for c in s.exec(select(Character)).all()}
    )
    assert chars_after == {c1_id: "char_c", c2_id: "char_d"}, (
        f"Confirmed batch must restore BOTH missing characters; got {chars_after}"
    )


# ---------------------------------------------------------------------------
# Full restore preserves live likeness pipeline state across the swap
# ---------------------------------------------------------------------------


def test_full_restore_preserves_live_likeness_queue_and_frontier(server):
    """The snapshot strip drops the likeness queue + frontier (they're LIVE
    pipeline progress, not user data). Full restore must capture the live
    state BEFORE the swap and replay it AFTER — for pictures that survive
    the restore. Pictures dropped by the restore must lose their queue/
    frontier rows; pictures new in the snapshot must gain frontier rows
    via ensure_all.
    """
    from pixlstash.db_models.picture_likeness import (
        PictureLikeness,
        PictureLikenessFrontier,
        PictureLikenessQueue,
    )

    # Live state at snapshot time: pictures {survivor, soon_to_be_added}.
    _create_file(server, "survivor.jpg")
    _create_file(server, "soon.jpg")
    survivor = _add_picture(server, filename="survivor.jpg", description="v1")
    soon_added = _add_picture(server, filename="soon.jpg")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Post-snapshot: add a NEW picture that doesn't exist in the snapshot.
    # After restore this picture gets dropped (the swap replaces the live
    # DB with the snapshot's picture set).
    _create_file(server, "future.jpg")
    future = _add_picture(server, filename="future.jpg")

    # Mutate the live likeness pipeline state: survivor + future are
    # both in the queue and have frontier rows. soon_added (in snapshot)
    # has no live progress yet — it should still get a frontier row
    # post-restore via ensure_all.
    def _seed_live_state(session):
        a, b = sorted([survivor.id, future.id])
        session.add(
            PictureLikeness(
                picture_id_a=a, picture_id_b=b, likeness=0.5, metric="clip_cosine"
            )
        )
        session.add(PictureLikenessQueue(picture_id=survivor.id))
        session.add(PictureLikenessQueue(picture_id=future.id))
        session.add(PictureLikenessFrontier(picture_id_a=survivor.id, j_max=future.id))
        session.add(PictureLikenessFrontier(picture_id_a=future.id, j_max=future.id))
        session.commit()

    server.vault.db.run_task(_seed_live_state)

    # Sanity: pre-restore state.
    pre = server.vault.db.run_immediate_read_task(
        lambda s: (
            set(s.exec(select(PictureLikenessQueue.picture_id)).all()),
            {
                r.picture_id_a: r.j_max
                for r in s.exec(select(PictureLikenessFrontier)).all()
            },
            s.exec(select(PictureLikeness)).all(),
        )
    )
    pre_queue, pre_frontier, pre_likeness = pre
    assert pre_queue == {survivor.id, future.id}
    assert pre_frontier == {survivor.id: future.id, future.id: future.id}
    assert len(pre_likeness) == 1

    # Restore. Snapshot's picture set is {survivor, soon_added}; future
    # gets dropped by the swap.
    report = server.vault.restore_service.restore_full(cp.id)
    assert not report.errors, f"Restore errors: {report.errors}"

    post = server.vault.db.run_immediate_read_task(
        lambda s: (
            set(s.exec(select(PictureLikenessQueue.picture_id)).all()),
            {
                r.picture_id_a: r.j_max
                for r in s.exec(select(PictureLikenessFrontier)).all()
            },
            s.exec(select(PictureLikeness)).all(),
        )
    )
    post_queue, post_frontier, post_likeness = post

    # Survivor's queue entry is preserved; future's is gone (FK on a
    # dropped picture).
    assert post_queue == {survivor.id}, (
        f"Queue must preserve survivor and drop future; got {post_queue}"
    )
    # Survivor's frontier row + j_max is preserved.
    assert post_frontier.get(survivor.id) == future.id, (
        f"survivor frontier j_max must survive the swap; got {post_frontier}"
    )
    # Future's frontier row is gone (its picture no longer exists).
    assert future.id not in post_frontier, (
        f"future picture's frontier row must be cleared; got {post_frontier}"
    )
    # soon_added gained a frontier row via ensure_all (initialised to its
    # own id — see PictureLikenessFrontier.ensure_all).
    assert post_frontier.get(soon_added.id) == soon_added.id, (
        f"soon_added must gain a frontier row via ensure_all; got {post_frontier}"
    )
    # The snapshot's picturelikeness was stripped, so post-restore has zero
    # likeness rows — the pipeline will recompute.
    assert post_likeness == [], (
        f"likeness rows must be empty after restore; got {post_likeness}"
    )


# ---------------------------------------------------------------------------
# Full restore keeps newer snapshots in the index (roll-forward is possible)
# ---------------------------------------------------------------------------


def test_full_restore_preserves_newer_snapshots_in_index(server):
    """Restoring an older snapshot must NOT hide newer ones.

    The ``Snapshot`` table lives inside the live DB, so the file swap would
    roll the snapshot index back to whatever snapshots existed when the
    target was taken — and because ``VACUUM INTO`` copies the live DB *before*
    a snapshot records its own row, an old snapshot's file doesn't even list
    itself. Without the post-swap reconciliation the whole list would
    disappear, stranding the user with no way to roll forward. The fix
    re-inserts every captured snapshot whose file still exists on disk.
    """
    _create_file(server, "rollfwd.jpg")
    pic = _add_picture(server, filename="rollfwd.jpg", description="state_a")

    # Snapshot A — the older restore point.
    cp_a = server.vault.snapshot_service.create_snapshot("MANUAL", label="A")

    # Diverge, then take the newer snapshot C.
    def _mutate(session):
        session.get(Picture, pic.id).description = "state_c"
        session.commit()

    server.vault.db.run_task(_mutate)
    cp_c = server.vault.snapshot_service.create_snapshot("MANUAL", label="C")

    # Restore the OLDER snapshot A.
    report = server.vault.restore_service.restore_full(cp_a.id)
    assert not report.errors, f"Restore errors: {report.errors}"
    assert _get_picture(server, pic.id).description == "state_a"

    # The newer snapshot C must still be listed after the restore, keyed by
    # its file path (ids are re-assigned on re-insert, so compare paths).
    snapshots = server.vault.snapshot_service.list_snapshots()
    listed_paths = {s.relative_path for s in snapshots}
    assert cp_c.relative_path in listed_paths, (
        "Newer snapshot C must remain in the index after restoring older "
        f"snapshot A; listed paths: {listed_paths}"
    )
    assert cp_a.relative_path in listed_paths, (
        f"The restored snapshot A must also remain listed; listed paths: {listed_paths}"
    )
    # The pre-restore safety snapshot (OPPORTUNISTIC) is preserved too.
    assert any(s.kind == "OPPORTUNISTIC" for s in snapshots), (
        f"Safety snapshot must survive the swap; got kinds "
        f"{[s.kind for s in snapshots]}"
    )

    # Roll forward: restoring C again must bring back the newer state, proving
    # the surviving index row is a usable restore point.
    cp_c_live = next(s for s in snapshots if s.relative_path == cp_c.relative_path)
    report_fwd = server.vault.restore_service.restore_full(cp_c_live.id)
    assert not report_fwd.errors, f"Roll-forward errors: {report_fwd.errors}"
    assert _get_picture(server, pic.id).description == "state_c", (
        "Restoring the surviving newer snapshot must roll the state forward"
    )


# ---------------------------------------------------------------------------
# Missing-dependency restore — picture_set and project parents (issue #1)
# ---------------------------------------------------------------------------


def test_restore_resource_picture_with_missing_picture_set_raises_without_confirm(
    server,
):
    """A snapshot picture is a member of a PictureSet that was later deleted
    in live. Restoring the picture references the missing set; un-confirmed
    must raise MissingDependenciesError carrying the set id and write nothing.
    """
    from pixlstash.services.restore_service import MissingDependenciesError

    _create_file(server, "in_set.jpg")
    pic = _add_picture(server, filename="in_set.jpg")

    def _setup(session):
        ps = PictureSet(name="my_set")
        session.add(ps)
        session.commit()
        session.refresh(ps)
        session.add(PictureSetMember(set_id=ps.id, picture_id=pic.id))
        session.commit()
        return ps.id

    set_id = server.vault.db.run_task(_setup)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Live: drop the membership then the set (FK-safe order).
    def _delete_set(session):
        session.exec(delete(PictureSetMember).where(PictureSetMember.set_id == set_id))
        session.exec(delete(PictureSet).where(PictureSet.id == set_id))
        session.commit()

    server.vault.db.run_task(_delete_set)

    with pytest.raises(MissingDependenciesError) as exc_info:
        server.vault.restore_service.restore_resource(cp.id, "picture", pic.id)
    assert set_id in exc_info.value.missing.get("picture_sets", []), (
        f"Expected missing picture_sets to include {set_id}; "
        f"got {exc_info.value.missing}"
    )

    sets_after = server.vault.db.run_immediate_read_task(
        lambda s: s.exec(select(PictureSet)).all()
    )
    assert sets_after == [], (
        f"Refused restore must not write the set back; got {sets_after}"
    )


def test_restore_resource_picture_confirm_restores_missing_picture_set(server):
    """With confirm=True, the deleted PictureSet is re-inserted from the
    snapshot before the membership is upserted — both land in live."""
    _create_file(server, "in_set2.jpg")
    pic = _add_picture(server, filename="in_set2.jpg")

    def _setup(session):
        ps = PictureSet(name="set_to_restore")
        session.add(ps)
        session.commit()
        session.refresh(ps)
        session.add(PictureSetMember(set_id=ps.id, picture_id=pic.id))
        session.commit()
        return ps.id

    set_id = server.vault.db.run_task(_setup)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _delete_set(session):
        session.exec(delete(PictureSetMember).where(PictureSetMember.set_id == set_id))
        session.exec(delete(PictureSet).where(PictureSet.id == set_id))
        session.commit()

    server.vault.db.run_task(_delete_set)

    report = server.vault.restore_service.restore_resource(
        cp.id, "picture", pic.id, confirm_restore_dependencies=True
    )
    assert report.upserted_count > 0

    set_after = server.vault.db.run_immediate_read_task(
        lambda s: s.get(PictureSet, set_id)
    )
    assert set_after is not None and set_after.name == "set_to_restore", (
        f"Confirmed restore must re-insert the set; got {set_after}"
    )
    member_after = server.vault.db.run_immediate_read_task(
        lambda s: s.exec(
            select(PictureSetMember).where(PictureSetMember.set_id == set_id)
        ).first()
    )
    assert member_after is not None and member_after.picture_id == pic.id


def test_restore_resource_picture_with_missing_project_raises_without_confirm(
    server,
):
    """A snapshot picture belongs to a Project (via PictureProjectMember) that
    was later deleted in live. Un-confirmed restore must raise with the
    missing project id and write nothing."""
    from pixlstash.services.restore_service import MissingDependenciesError

    _create_file(server, "in_proj.jpg")
    pic = _add_picture(server, filename="in_proj.jpg")

    def _setup(session):
        proj = Project(name="my_project")
        session.add(proj)
        session.commit()
        session.refresh(proj)
        session.add(PictureProjectMember(project_id=proj.id, picture_id=pic.id))
        session.commit()
        return proj.id

    proj_id = server.vault.db.run_task(_setup)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _delete_proj(session):
        session.exec(
            delete(PictureProjectMember).where(
                PictureProjectMember.project_id == proj_id
            )
        )
        session.exec(delete(Project).where(Project.id == proj_id))
        session.commit()

    server.vault.db.run_task(_delete_proj)

    with pytest.raises(MissingDependenciesError) as exc_info:
        server.vault.restore_service.restore_resource(cp.id, "picture", pic.id)
    assert proj_id in exc_info.value.missing.get("projects", []), (
        f"Expected missing projects to include {proj_id}; got {exc_info.value.missing}"
    )

    projects_after = server.vault.db.run_immediate_read_task(
        lambda s: s.exec(select(Project)).all()
    )
    assert projects_after == [], (
        f"Refused restore must not write the project back; got {projects_after}"
    )


def test_restore_resource_picture_confirm_restores_missing_project(server):
    """With confirm=True, the deleted Project is re-inserted from the snapshot
    before the project membership is upserted."""
    _create_file(server, "in_proj2.jpg")
    pic = _add_picture(server, filename="in_proj2.jpg")

    def _setup(session):
        proj = Project(name="project_to_restore")
        session.add(proj)
        session.commit()
        session.refresh(proj)
        session.add(PictureProjectMember(project_id=proj.id, picture_id=pic.id))
        session.commit()
        return proj.id

    proj_id = server.vault.db.run_task(_setup)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _delete_proj(session):
        session.exec(
            delete(PictureProjectMember).where(
                PictureProjectMember.project_id == proj_id
            )
        )
        session.exec(delete(Project).where(Project.id == proj_id))
        session.commit()

    server.vault.db.run_task(_delete_proj)

    report = server.vault.restore_service.restore_resource(
        cp.id, "picture", pic.id, confirm_restore_dependencies=True
    )
    assert report.upserted_count > 0

    proj_after = server.vault.db.run_immediate_read_task(
        lambda s: s.get(Project, proj_id)
    )
    assert proj_after is not None and proj_after.name == "project_to_restore", (
        f"Confirmed restore must re-insert the project; got {proj_after}"
    )


# ---------------------------------------------------------------------------
# Missing-file ratio guard (A3 / issue #2)
# ---------------------------------------------------------------------------


def test_full_restore_refuses_when_most_files_missing(server):
    """≥10 pictures and >50% missing on disk looks like a mount failure, not
    a real deletion. Full restore must refuse rather than wipe metadata for
    that many pictures."""
    # 10 pictures; create files for only 3 → 7 missing (70%).
    for i in range(10):
        name = f"ratio_{i}.jpg"
        if i < 3:
            _create_file(server, name)
        _add_picture(server, filename=name)

    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    with pytest.raises(RuntimeError) as exc_info:
        server.vault.restore_service.restore_full(cp.id)
    msg = str(exc_info.value).lower()
    assert "missing" in msg and "mount" in msg, (
        f"Refusal must explain the mount-failure heuristic; got: {exc_info.value}"
    )

    # Live DB untouched — all 10 rows still present (no swap happened).
    count_after = server.vault.db.run_immediate_read_task(
        lambda s: len(s.exec(select(Picture)).all())
    )
    assert count_after == 10, (
        f"Refused restore must not drop any rows; got {count_after} pictures"
    )


def test_full_restore_allows_high_missing_ratio_with_override(server):
    """The same >50% scenario proceeds when the caller explicitly opts in via
    allow_without_safety — the missing-file rows are then dropped."""
    for i in range(10):
        name = f"ovr_{i}.jpg"
        if i < 3:
            _create_file(server, name)
        _add_picture(server, filename=name)

    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    report = server.vault.restore_service.restore_full(cp.id, allow_without_safety=True)
    assert not report.errors, f"Override restore errors: {report.errors}"
    assert report.missing_files_count == 7

    # Only the 3 pictures whose files exist survive the cleanup.
    count_after = server.vault.db.run_immediate_read_task(
        lambda s: len(s.exec(select(Picture)).all())
    )
    assert count_after == 3, (
        f"Override restore must drop the 7 missing-file rows; got {count_after}"
    )


# ---------------------------------------------------------------------------
# Restore lifecycle events: STARTED/COMPLETED/FAILED ordering (issue #3)
# ---------------------------------------------------------------------------


def _capture_restore_events(server):
    """Register a listener and return a list that accrues restore EventTypes."""
    from pixlstash.event_types import EventType

    captured: list = []
    restore_types = {
        EventType.RESTORE_STARTED,
        EventType.RESTORE_COMPLETED,
        EventType.RESTORE_FAILED,
    }

    def _listener(event_type, data):
        if event_type in restore_types:
            captured.append(event_type)

    server.vault.add_event_listener(_listener)
    return captured


def test_full_restore_emits_started_then_completed(server):
    """A successful full restore emits exactly STARTED then COMPLETED, in
    that order, and never FAILED."""
    from pixlstash.event_types import EventType

    _create_file(server, "evt_ok.jpg")
    _add_picture(server, filename="evt_ok.jpg", description="v1")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    events = _capture_restore_events(server)
    server.vault.restore_service.restore_full(cp.id)

    assert events == [
        EventType.RESTORE_STARTED,
        EventType.RESTORE_COMPLETED,
    ], f"Expected STARTED→COMPLETED; got {[e.name for e in events]}"


def test_restore_nonexistent_snapshot_emits_no_started(server):
    """A 404-equivalent (snapshot not found) is detected BEFORE the STARTED
    event, so the UI is never left with a dangling activeJob."""
    events = _capture_restore_events(server)

    with pytest.raises(ValueError):
        server.vault.restore_service.restore_full(999999)

    assert events == [], (
        f"Missing-snapshot restore must emit no lifecycle events; "
        f"got {[e.name for e in events]}"
    )


def test_missing_deps_refusal_emits_started_then_failed(server):
    """A dependency-refusal restore emits STARTED then a terminal FAILED
    (so the client clears activeJob), never COMPLETED."""
    from pixlstash.event_types import EventType
    from pixlstash.services.restore_service import MissingDependenciesError

    _create_file(server, "evt_dep.jpg")
    pic = _add_picture(server, filename="evt_dep.jpg")

    def _setup(session):
        c = Character(name="evt_char")
        session.add(c)
        session.commit()
        session.refresh(c)
        session.add(
            Face(
                picture_id=pic.id,
                frame_index=0,
                face_index=0,
                character_id=c.id,
                bbox_="[0,0,10,10]",
            )
        )
        session.commit()
        return c.id

    char_id = server.vault.db.run_task(_setup)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _del(session):
        session.exec(delete(Character).where(Character.id == char_id))
        session.commit()

    server.vault.db.run_task(_del)

    events = _capture_restore_events(server)
    with pytest.raises(MissingDependenciesError):
        server.vault.restore_service.restore_resource(cp.id, "picture", pic.id)

    assert events == [
        EventType.RESTORE_STARTED,
        EventType.RESTORE_FAILED,
    ], f"Expected STARTED→FAILED; got {[e.name for e in events]}"


# ---------------------------------------------------------------------------
# Compression: snapshots are stored compressed and keep blobs across restore
# ---------------------------------------------------------------------------


def test_snapshot_is_compressed_on_disk(server):
    """New snapshots are written as zstd archives (``.sqlite.zst``)."""
    _create_file(server, "compressed.jpg")
    _add_picture(server, filename="compressed.jpg")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    assert cp.relative_path.endswith(".sqlite.zst"), (
        f"Snapshot must be compressed; got {cp.relative_path}"
    )
    abs_path = os.path.join(server.vault.image_root, cp.relative_path)
    assert os.path.isfile(abs_path)
    # zstd magic number (little-endian 0xFD2FB528).
    with open(abs_path, "rb") as fh:
        assert fh.read(4) == b"\x28\xb5\x2f\xfd", "File must carry the zstd magic"


def test_full_restore_keeps_embeddings_no_regen(server):
    """The expensive blobs (image embedding + scores) now ride inside the
    snapshot, so restoring brings them back instead of NULL-resetting them
    for the WorkPlanner to regenerate.
    """
    from sqlalchemy import update as sa_update

    _create_file(server, "embed.jpg")
    pic = _add_picture(server, filename="embed.jpg", description="v1")

    embedding = b"\x07" * 4096

    def _set_blobs(session):
        session.execute(
            sa_update(Picture)
            .where(Picture.id == pic.id)
            .values(image_embedding=embedding, smart_score=0.81)
        )
        session.commit()

    server.vault.db.run_task(_set_blobs)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Wipe the blobs on the live DB (as if regeneration had cleared them).
    def _wipe_blobs(session):
        session.execute(
            sa_update(Picture)
            .where(Picture.id == pic.id)
            .values(image_embedding=None, smart_score=None)
        )
        session.commit()

    server.vault.db.run_task(_wipe_blobs)
    assert _get_picture(server, pic.id).image_embedding is None

    report = server.vault.restore_service.restore_full(cp.id)
    assert not report.errors, f"Restore errors: {report.errors}"

    restored = _get_picture(server, pic.id)
    assert restored.image_embedding == embedding, (
        "image_embedding must be restored from the snapshot, not NULL-reset"
    )
    assert restored.smart_score == 0.81, (
        "smart_score must be restored from the snapshot"
    )


def test_full_restore_from_legacy_uncompressed_snapshot(server):
    """Snapshots created before compression are plain ``.sqlite`` files. The
    restore read path must still handle them (the materialize copy branch)."""
    from pixlstash.utils.snapshot_compression import materialize_snapshot

    _create_file(server, "legacy.jpg")
    pic = _add_picture(server, filename="legacy.jpg", description="legacy_v1")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    vault_root = server.vault.image_root
    # Decompress the new archive into a sibling plain .sqlite to emulate a
    # legacy on-disk snapshot, and register a Snapshot row pointing at it.
    legacy_rel = cp.relative_path[: -len(".zst")]
    legacy_abs = os.path.join(vault_root, legacy_rel)
    materialize_snapshot(os.path.join(vault_root, cp.relative_path), legacy_abs)

    def _register(session):
        row = Snapshot(
            kind="MANUAL",
            created_at=cp.created_at,
            relative_path=legacy_rel,
            manifest_relative_path=cp.manifest_relative_path,
            byte_size=os.path.getsize(legacy_abs),
            picture_count=cp.picture_count,
            schema_version=cp.schema_version,
            label="legacy",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id

    legacy_id = server.vault.db.run_task(_register)

    def _mutate(session):
        session.get(Picture, pic.id).description = "legacy_v2"
        session.commit()

    server.vault.db.run_task(_mutate)

    report = server.vault.restore_service.restore_full(legacy_id)
    assert not report.errors, f"Legacy restore errors: {report.errors}"
    assert _get_picture(server, pic.id).description == "legacy_v1", (
        "Restoring a legacy uncompressed snapshot must work"
    )
