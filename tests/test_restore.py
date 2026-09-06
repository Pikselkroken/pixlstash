"""Tests for RestoreService - full and per-resource restore."""

import asyncio
import json
import os
import sqlite3
import stat
import threading
from datetime import datetime, timedelta, timezone
import shutil
import tempfile
from contextlib import closing

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, create_engine, delete, select

from pixlstash.db_models import (
    Character,
    CharacterProjectMember,
    DeletedFileLog,
    Face,
    Picture,
    PictureSet,
    PictureSetMember,
    PictureSetProjectMember,
    Project,
    ReferenceFolder,
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
from pixlstash.trusted_sqlite import TrustedSQLiteLocation
from pixlstash.db_models.user_token import UserToken
from pixlstash.services import scrapheap_service
from pixlstash.utils.snapshot_compression import (
    compress_snapshot,
    materialize_snapshot,
)
from tests.utils import delete_characters, delete_projects, wipe_tables


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

    server.vault.db.run_task(
        wipe_tables,
        [
            Snapshot,
            # Likeness pipeline rows are populated by restore_full (via
            # ensure_all), so they accumulate across tests. Without an
            # explicit wipe - FKs are OFF for the wipe, so CASCADE doesn't
            # fire - they orphan and collide with the next test's replay.
            PictureLikeness,
            PictureLikenessQueue,
            PictureLikenessFrontier,
            PictureProjectMember,
            CharacterProjectMember,
            PictureSetProjectMember,
            PictureSetMember,
            Face,
            Tag,
            DeletedFileLog,
            Picture,
            ReferenceFolder,
            PictureSet,
            Project,
            Character,
        ],
    )

    cp_dir = os.path.join(server.vault.image_root, "snapshots")
    if os.path.isdir(cp_dir):
        shutil.rmtree(cp_dir)
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_picture(
    server, filename="test.jpg", description=None, pixel_sha=None
) -> Picture:
    def _do(session):
        pic = Picture(
            file_path=filename,
            filename=filename,
            description=description,
            pixel_sha=pixel_sha,
        )
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


def _create_picture_share(owner, server, filename: str) -> tuple[Picture, dict]:
    """Create a servable picture and return its scoped share credential."""
    from PIL import Image

    file_path = _create_file(server, filename)
    Image.new("RGB", (2, 2), color=(20, 40, 60)).save(file_path, format="JPEG")
    pic = _add_picture(server, filename=filename)

    def _set_format(session):
        stored = session.get(Picture, pic.id)
        stored.format = os.path.splitext(filename)[1].lstrip(".").lower()
        session.add(stored)
        session.commit()

    server.vault.db.run_task(_set_format)
    response = owner.post(
        "/api/v1/users/me/token",
        json={
            "description": "restore barrier share",
            "scope": "READ",
            "resource_type": "picture",
            "resource_id": pic.id,
        },
    )
    assert response.status_code == 200, response.text
    return pic, response.json()


def _remove_file(server, relative_path: str):
    """Delete a placeholder file from the vault image_root."""
    os.remove(os.path.join(server.vault.image_root, relative_path))


def _add_deleted_log(
    server,
    file_path: str,
    pixel_sha: str | None = None,
    file_removed: bool = True,
):
    """Record a deletion in deleted_file_log (path stored hashed).

    ``file_removed=True`` (default) is a genuine permanent deletion - the file
    was removed from disk and restore must never resurrect it. ``file_removed=
    False`` records a picture removed from the library whose file was KEPT on
    disk (a protected reference-folder picture): restore must NOT treat it as a
    permanent deletion.
    """
    from datetime import datetime, timezone

    def _do(session):
        session.add(
            DeletedFileLog(
                path_sha=DeletedFileLog.hash_path(file_path),
                pixel_sha=pixel_sha,
                deleted_at=datetime.now(timezone.utc),
                file_removed=file_removed,
            )
        )
        session.commit()

    server.vault.db.run_task(_do)


def _count_deleted_log(server, file_path: str) -> int:
    path_sha = DeletedFileLog.hash_path(file_path)
    return server.vault.db.run_immediate_read_task(
        lambda s: len(
            s.exec(
                select(DeletedFileLog).where(DeletedFileLog.path_sha == path_sha)
            ).all()
        )
    )


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


def test_the_location_guard_is_released_before_the_restore_swap(server, monkeypatch):
    """No open guard fd may survive to the ``os.replace`` of ``vault.db``.

    Windows refuses to replace a file the process holds an open handle on
    (WinError 5), so the guard retained for POSIX lock-lifetime (the WAL
    split-brain fix) must be released inside the swap's exclusive section,
    strictly after ``engine.dispose()``. Linux performs that replace happily
    with the fd open, which is exactly why this regression reached CI: no
    Linux test could fail on it. This pins the invariant Linux CAN see -
    at the moment of the replace, the guard is gone.
    """
    _create_file(server, "guarded.jpg")
    _add_picture(server, filename="guarded.jpg", description="x")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    live_db = server.vault.db._db_path
    # This fixture's vault takes the unregistered branch (vault.py, no
    # hub-registered library), which carries NO guard - leaving the test
    # vacuously green with or without the release (the revert-check caught
    # that). Arm the guard exactly as a registered-library open does, so the
    # invariant is exercised against the state the corruption fix created.
    if server.vault.db._location_guard is None:
        server.vault.db._location_guard = TrustedSQLiteLocation.open(live_db)
    assert server.vault.db._location_guard is not None

    guard_state_at_replace = []
    real_replace = os.replace

    def observing_replace(src, dst, *args, **kwargs):
        if str(dst) == str(live_db):
            guard_state_at_replace.append(
                getattr(server.vault.db, "_location_guard", None)
            )
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", observing_replace)

    report = server.vault.restore_service.restore_full(cp.id)

    assert not report.errors, f"Restore errors: {report.errors}"
    assert guard_state_at_replace, "the swap never replaced the live database"
    assert guard_state_at_replace == [None], (
        "an open location-guard fd survived to os.replace; "
        "that is WinError 5 on Windows"
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_full_restore_leaves_live_db_owner_only_under_group_umask(server):
    """A successful restore must not loosen the live database's mode.

    ``VACUUM INTO`` creates the snapshot at 0644 & ~umask and ``copy2``
    preserves that mode, so under the Debian/Ubuntu umask 002 the swapped-in
    ``vault.db`` used to come out group-writable - and the trusted-location
    check then refused it at the next startup, bricking the library.
    """
    _create_file(server, "perm.jpg")
    pic = _add_picture(server, filename="perm.jpg")

    old_umask = os.umask(0o002)
    try:
        cp = server.vault.snapshot_service.create_snapshot("MANUAL")
        report = server.vault.restore_service.restore_full(cp.id)
    finally:
        os.umask(old_umask)

    assert not report.errors, f"Restore errors: {report.errors}"
    live_db = os.path.join(server.vault.image_root, "vault.db")
    assert stat.S_IMODE(os.lstat(live_db).st_mode) == 0o600

    # Reopens cleanly: this guard is the exact check that refuses a
    # group-writable vault.db at the next startup.
    guard = TrustedSQLiteLocation.open(live_db)
    guard.close()

    # And the re-created engine still serves reads.
    assert _get_picture(server, pic.id) is not None


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
    # Unrelated picture's face survives - restore is scoped to valid_picture_ids.
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


def test_restore_existing_character_replaces_project_memberships_exactly(server):
    """A root character restore reinstates allowed joins and removes live-only ones."""
    from pixlstash.services.project_membership_service import set_character_projects

    def _setup(session):
        allowed = Project(name="character-allowed")
        denied = Project(name="character-denied")
        session.add(allowed)
        session.add(denied)
        session.flush()
        character = Character(name="snapshot-character")
        session.add(character)
        session.flush()
        set_character_projects(session, character, [allowed.id])
        session.commit()
        return character.id, allowed.id, denied.id

    char_id, allowed_id, denied_id = server.vault.db.run_task(_setup)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _diverge(session):
        character = session.get(Character, char_id)
        character.name = "live-character"
        set_character_projects(session, character, [denied_id])
        session.commit()

    server.vault.db.run_task(_diverge)
    report = server.vault.restore_service.restore_resource(cp.id, "character", char_id)
    assert not report.errors

    restored, project_ids = server.vault.db.run_immediate_read_task(
        lambda session: (
            session.get(Character, char_id),
            sorted(
                session.exec(
                    select(CharacterProjectMember.project_id).where(
                        CharacterProjectMember.character_id == char_id
                    )
                ).all()
            ),
        )
    )
    assert restored.name == "snapshot-character"
    assert restored.project_id == allowed_id
    assert project_ids == [allowed_id]
    assert denied_id not in project_ids


def test_restore_existing_picture_set_replaces_project_memberships_exactly(server):
    """A root set restore reinstates allowed joins and removes live-only ones."""
    from pixlstash.services.project_membership_service import set_picture_set_projects

    def _setup(session):
        allowed = Project(name="set-allowed")
        denied = Project(name="set-denied")
        session.add(allowed)
        session.add(denied)
        session.flush()
        picture_set = PictureSet(name="snapshot-set")
        session.add(picture_set)
        session.flush()
        set_picture_set_projects(session, picture_set, [allowed.id])
        session.commit()
        return picture_set.id, allowed.id, denied.id

    set_id, allowed_id, denied_id = server.vault.db.run_task(_setup)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _diverge(session):
        picture_set = session.get(PictureSet, set_id)
        picture_set.name = "live-set"
        set_picture_set_projects(session, picture_set, [denied_id])
        session.commit()

    server.vault.db.run_task(_diverge)
    report = server.vault.restore_service.restore_resource(cp.id, "picture_set", set_id)
    assert not report.errors

    restored, project_ids = server.vault.db.run_immediate_read_task(
        lambda session: (
            session.get(PictureSet, set_id),
            sorted(
                session.exec(
                    select(PictureSetProjectMember.project_id).where(
                        PictureSetProjectMember.set_id == set_id
                    )
                ).all()
            ),
        )
    )
    assert restored.name == "snapshot-set"
    assert restored.project_id == allowed_id
    assert project_ids == [allowed_id]
    assert denied_id not in project_ids


def test_root_entity_membership_projects_are_restore_dependencies(server):
    """A deleted project referenced only by the root join is preflighted."""
    from pixlstash.services.project_membership_service import set_character_projects
    from pixlstash.services.restore import MissingDependenciesError

    def _setup(session):
        project = Project(name="root-membership-dependency")
        session.add(project)
        session.flush()
        character = Character(name="root-with-project")
        session.add(character)
        session.flush()
        set_character_projects(session, character, [project.id])
        session.commit()
        return character.id, project.id

    char_id, project_id = server.vault.db.run_task(_setup)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    server.vault.db.run_task(delete_projects, [project_id])

    with pytest.raises(MissingDependenciesError) as exc_info:
        server.vault.restore_service.restore_resource(cp.id, "character", char_id)
    assert exc_info.value.missing == {"projects": [project_id]}

    report = server.vault.restore_service.restore_resource(
        cp.id,
        "character",
        char_id,
        confirm_restore_dependencies=True,
    )
    assert not report.errors
    project, project_ids = server.vault.db.run_immediate_read_task(
        lambda session: (
            session.get(Project, project_id),
            session.exec(
                select(CharacterProjectMember.project_id).where(
                    CharacterProjectMember.character_id == char_id
                )
            ).all(),
        )
    )
    assert project is not None
    assert project_ids == [project_id]


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

    from pixlstash.services.restore import RestoreInProgressError

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


def test_restore_schema_scratch_removes_portable_identity(server):
    """Every full/preview/resource restore consumes the same sanitized scratch."""
    marker = "RESTORE-PORTABLE-SECRET-f342"
    snapshot = server.vault.snapshot_service.create_snapshot("MANUAL")
    work_dir = tempfile.mkdtemp(prefix="pixlstash_test_restore_identity_")
    plain = os.path.join(work_dir, "identity-bearing.sqlite")
    materialize_snapshot(
        os.path.join(server.vault.image_root, snapshot.relative_path), plain
    )
    identity_engine = create_engine(f"sqlite:///{plain}")
    try:
        with Session(identity_engine) as session:
            from pixlstash.db_models import User

            session.add(
                User(
                    username=f"user-{marker}",
                    password_hash=f"password-{marker}",
                    hidden_tags=f'["{marker}"]',
                )
            )
            session.commit()
    finally:
        identity_engine.dispose()

    upgraded = server.vault.restore_service._upgrade_snapshot_schema(plain)
    try:
        with closing(sqlite3.connect(upgraded)) as connection:
            for table in ("user", "usertoken", "guest_session", "guest_score"):
                assert connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone() == (0,)
        assert marker.encode() not in open(upgraded, "rb").read()
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
    ``schema_version`` sorts strictly above the live alembic head - a
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
    from pixlstash.services.restore import MissingDependenciesError

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

    # Live: user deletes the character (the delete route nulls Face.character_id
    # in the live DB, but the snapshot still has the reference).
    server.vault.db.run_task(delete_characters, [alice_id])

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
    faces - both end up in the live DB."""
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

    server.vault.db.run_task(delete_characters, [bob_id])

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
    from pixlstash.services.restore import MissingDependenciesError

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

    server.vault.db.run_task(delete_characters)

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
    parents in one pre-pass, then upserts each item - no per-item retries."""
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

    server.vault.db.run_task(delete_characters)

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
    state BEFORE the swap and replay it AFTER - for pictures that survive
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
    # has no live progress yet - it should still get a frontier row
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
    # own id - see PictureLikenessFrontier.ensure_all).
    assert post_frontier.get(soon_added.id) == soon_added.id, (
        f"soon_added must gain a frontier row via ensure_all; got {post_frontier}"
    )
    # The snapshot's picturelikeness was stripped, so post-restore has zero
    # likeness rows - the pipeline will recompute.
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
    target was taken - and because ``VACUUM INTO`` copies the live DB *before*
    a snapshot records its own row, an old snapshot's file doesn't even list
    itself. Without the post-swap reconciliation the whole list would
    disappear, stranding the user with no way to roll forward. The fix
    re-inserts every captured snapshot whose file still exists on disk.
    """
    _create_file(server, "rollfwd.jpg")
    pic = _add_picture(server, filename="rollfwd.jpg", description="state_a")

    # Snapshot A - the older restore point.
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
# Missing-dependency restore - picture_set and project parents (issue #1)
# ---------------------------------------------------------------------------


def test_restore_resource_picture_with_missing_picture_set_raises_without_confirm(
    server,
):
    """A snapshot picture is a member of a PictureSet that was later deleted
    in live. Restoring the picture references the missing set; un-confirmed
    must raise MissingDependenciesError carrying the set id and write nothing.
    """
    from pixlstash.services.restore import MissingDependenciesError

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
    snapshot before the membership is upserted - both land in live."""
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
    from pixlstash.services.restore import MissingDependenciesError

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

    # Live DB untouched - all 10 rows still present (no swap happened).
    count_after = server.vault.db.run_immediate_read_task(
        lambda s: len(s.exec(select(Picture)).all())
    )
    assert count_after == 10, (
        f"Refused restore must not drop any rows; got {count_after} pictures"
    )


def test_full_restore_allows_high_missing_ratio_with_override(server):
    """The same >50% scenario proceeds when the caller explicitly opts in via
    allow_without_safety - the missing-file rows are then dropped."""
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
    from pixlstash.services.restore import MissingDependenciesError

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

    server.vault.db.run_task(delete_characters, [char_id])

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


# ---------------------------------------------------------------------------
# Full-restore preview lists only the resources that actually change
# ---------------------------------------------------------------------------


def test_preview_full_lists_only_changed_pictures(server):
    """The preview must surface the pictures that actually changed, not the
    first N pictures regardless of state. Three pictures, only one mutated:
    the preview shows exactly that one and counts the rest as unchanged.
    """
    for i in range(3):
        _create_file(server, f"prev_{i}.jpg")
    p0 = _add_picture(server, filename="prev_0.jpg", description="a")
    p1 = _add_picture(server, filename="prev_1.jpg", description="b")
    p2 = _add_picture(server, filename="prev_2.jpg", description="c")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Mutate only p1 after the snapshot.
    def _mutate(session):
        session.get(Picture, p1.id).description = "b-changed"
        session.commit()

    server.vault.db.run_task(_mutate)

    preview = server.vault.restore_service.preview_full(cp.id)

    assert preview.summary["pictures_to_revert"] == 1, preview.summary
    assert preview.summary["pictures_unchanged"] == 2, preview.summary
    assert preview.summary["pictures_to_recreate"] == 0
    assert preview.summary["pictures_to_delete"] == 0

    shown_ids = {r.id for r in preview.resources}
    assert shown_ids == {p1.id}, (
        f"Preview must list only the changed picture; got {shown_ids}"
    )
    assert p0.id not in shown_ids and p2.id not in shown_ids
    assert "description" in preview.resources[0].changed_fields


def test_preview_full_classifies_recreate_and_delete(server):
    """Pictures present only in the snapshot are 'recreate'; pictures present
    only in live are 'delete'. Unchanged pictures stay out of the list."""
    _create_file(server, "keep.jpg")
    _create_file(server, "gone.jpg")
    _add_picture(server, filename="keep.jpg", description="keep")
    p_gone = _add_picture(server, filename="gone.jpg", description="gone")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Add p_new BEFORE deleting p_gone so p_new gets a fresh max id - deleting
    # first would let SQLite reuse p_gone's rowid for p_new and collapse the
    # two distinct cases into one "id present in both".
    _create_file(server, "new.jpg")
    p_new = _add_picture(server, filename="new.jpg", description="new")

    # Snapshot has p_gone but live won't (→ recreate on restore).
    def _del_gone(session):
        session.delete(session.get(Picture, p_gone.id))
        session.commit()

    server.vault.db.run_task(_del_gone)

    preview = server.vault.restore_service.preview_full(cp.id)

    assert preview.summary["pictures_to_recreate"] == 1, preview.summary
    assert preview.summary["pictures_to_delete"] == 1, preview.summary
    assert preview.summary["pictures_unchanged"] == 1, preview.summary

    by_id = {r.id: r for r in preview.resources}
    assert p_gone.id in by_id and not by_id[p_gone.id].exists_in_live
    assert p_gone.id in by_id and by_id[p_gone.id].exists_in_snapshot
    assert p_new.id in by_id and by_id[p_new.id].exists_in_live
    assert p_new.id in by_id and not by_id[p_new.id].exists_in_snapshot


def test_preview_full_detects_set_membership_change(server):
    """A picture whose only post-snapshot change is set membership must still
    surface as changed - membership is folded into metadata_hash, so the
    preview no longer reports it as unchanged."""
    _create_file(server, "memb.jpg")
    pic = _add_picture(server, filename="memb.jpg", description="same")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # After the snapshot, move the picture into a new set (no column/tag change).
    def _add_to_set(session):
        s = PictureSet(name="after-set")
        session.add(s)
        session.commit()
        session.refresh(s)
        session.add(PictureSetMember(set_id=s.id, picture_id=pic.id))
        session.commit()

    server.vault.db.run_task(_add_to_set)

    preview = server.vault.restore_service.preview_full(cp.id)
    assert preview.summary["pictures_to_revert"] == 1, preview.summary
    assert preview.summary["pictures_unchanged"] == 0, preview.summary
    assert pic.id in {r.id for r in preview.resources}


# ---------------------------------------------------------------------------
# Permanent-deletion ledger (deleted_file_log) cross-check
# ---------------------------------------------------------------------------


def test_full_restore_skips_permanently_deleted_picture(server):
    """A snapshot picture whose file is in deleted_file_log must not be
    resurrected - even though its file still exists on disk, so the
    missing-file pass would NOT drop it. The ledger entry must also survive
    the DB swap (it was recorded after the snapshot was taken)."""
    _create_file(server, "purged.jpg")
    pic = _add_picture(server, filename="purged.jpg", description="doomed")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Simulate a permanent purge AFTER the snapshot: drop the live row and
    # record the deletion. The file is left on disk to prove the drop comes
    # from the ledger, not the missing-file check.
    def _del(session):
        session.delete(session.get(Picture, pic.id))
        session.commit()

    server.vault.db.run_task(_del)
    _add_deleted_log(server, "purged.jpg")

    report = server.vault.restore_service.restore_full(cp.id)

    assert report.missing_files_count == 0, "File is on disk - not missing."
    assert report.permanently_deleted_count == 1, report.permanently_deleted_count
    assert _get_picture(server, pic.id) is None, (
        "Permanently-deleted picture must not be resurrected by restore."
    )
    # The ledger entry recorded after the snapshot must survive the swap.
    assert _count_deleted_log(server, "purged.jpg") == 1, (
        "deleted_file_log entry must be replayed across the DB swap."
    )


def test_full_restore_keeps_live_reference_picture_in_ledger(server):
    """A reference-folder picture that is ALIVE (deleted=False) with its file on
    disk must survive restore even when its path is in deleted_file_log from an
    earlier scrapheap purge.

    Regression for ~145 reference-folder rows vanishing on a
    snapshot->immediate-restore: their absolute paths were in the ledger (a
    protected-folder purge logged the path but kept the file), the pictures were
    later re-indexed and alive again, and restore's permanent-deletion
    cross-check dropped them by path_sha even though the user was actively using
    them. The live-active cross-check must keep them."""
    from pixlstash.db_models.reference_folder import (
        ReferenceFolder,
        ReferenceFolderStatus,
    )

    with tempfile.TemporaryDirectory() as ref_dir:
        # File lives OUTSIDE image_root, referenced by its absolute path - the
        # real reference-folder shape.
        abs_path = os.path.join(ref_dir, "reference.png")
        open(abs_path, "wb").close()

        def _setup(session):
            rf = ReferenceFolder(
                folder=ref_dir,
                label="ref",
                allow_delete_file=False,
                status=ReferenceFolderStatus.ACTIVE,
            )
            session.add(rf)
            session.commit()
            session.refresh(rf)
            pic = Picture(
                file_path=abs_path,
                filename="reference.png",
                reference_folder_id=rf.id,
                deleted=False,
            )
            session.add(pic)
            session.commit()
            session.refresh(pic)
            return rf.id, pic.id

        folder_id, pic_id = server.vault.db.run_task(_setup)

        # Brand-new snapshot captures the reference picture ALIVE.
        cp = server.vault.snapshot_service.create_snapshot("MANUAL")

        # Its absolute path was recorded in the ledger by an earlier protected
        # purge (file kept on disk, path logged).
        _add_deleted_log(server, abs_path)

        report = server.vault.restore_service.restore_full(cp.id)

        assert report.missing_files_count == 0, "File is on disk - not missing."
        assert report.permanently_deleted_count == 0, (
            "A live, actively-used reference picture must not be counted as "
            f"permanently deleted: {report.permanently_deleted_count}"
        )
        restored = _get_picture(server, pic_id)
        assert restored is not None, (
            "Live reference-folder picture with its file on disk must survive "
            "restore even though its path is in deleted_file_log."
        )
        assert restored.reference_folder_id == folder_id, (
            "reference_folder_id link must be intact after restore."
        )
        assert not restored.deleted


def test_full_restore_ledger_drops_purged_but_keeps_reindexed(server):
    """The live-active cross-check must distinguish a genuinely purged path
    (still dropped) from one re-indexed and alive again (kept), even when both
    share the ledger - guarding the rescue from over-keeping (the ledger's
    'never resurrect' guarantee must still hold for content the user really
    deleted)."""
    from pixlstash.db_models.reference_folder import (
        ReferenceFolder,
        ReferenceFolderStatus,
    )

    with tempfile.TemporaryDirectory() as ref_dir:
        alive_path = os.path.join(ref_dir, "alive.png")
        purged_path = os.path.join(ref_dir, "purged.png")
        open(alive_path, "wb").close()
        open(purged_path, "wb").close()

        def _setup(session):
            rf = ReferenceFolder(
                folder=ref_dir,
                allow_delete_file=False,
                status=ReferenceFolderStatus.ACTIVE,
            )
            session.add(rf)
            session.commit()
            session.refresh(rf)
            alive = Picture(
                file_path=alive_path,
                filename="alive.png",
                reference_folder_id=rf.id,
                deleted=False,
            )
            purged = Picture(
                file_path=purged_path,
                filename="purged.png",
                reference_folder_id=rf.id,
                deleted=False,
            )
            session.add(alive)
            session.add(purged)
            session.commit()
            session.refresh(alive)
            session.refresh(purged)
            return alive.id, purged.id

        alive_id, purged_id = server.vault.db.run_task(_setup)

        # Snapshot captures BOTH alive.
        cp = server.vault.snapshot_service.create_snapshot("MANUAL")

        # Both paths are logged; the "purged" one is ALSO removed from the live
        # DB (a genuine permanent deletion - file lingers because protected).
        _add_deleted_log(server, alive_path)
        _add_deleted_log(server, purged_path)

        def _drop_purged(session):
            session.delete(session.get(Picture, purged_id))
            session.commit()

        server.vault.db.run_task(_drop_purged)

        report = server.vault.restore_service.restore_full(cp.id)

        assert _get_picture(server, alive_id) is not None, (
            "Re-indexed, live reference picture must be kept."
        )
        assert _get_picture(server, purged_id) is None, (
            "A genuinely purged path with no live active picture must still be "
            "dropped by the ledger."
        )
        assert report.permanently_deleted_count == 1, (
            f"Exactly one (the purged) should count: {report.permanently_deleted_count}"
        )


def test_full_restore_keeps_kept_file_reference_picture_not_in_live_db(server):
    """Root-cause test: a ledger entry with ``file_removed=False`` (file kept on
    disk) must NEVER count as a permanent deletion, even for a snapshot picture
    that is NOT present in the live DB - so the content-aware live-active rescue
    net (which only saves pictures alive in the live DB) does not apply.

    This isolates the ``_load_deleted_file_index`` ``file_removed`` filter from
    the secondary rescue net: the picture was removed from the library but its
    file kept (protected reference folder), a snapshot captured it ALIVE, and on
    rollback restore must bring it back because its content is not gone. With the
    old ledger (path-only, no ``file_removed``) this row would be dropped as a
    permanent deletion - the ~139-picture data-loss class."""
    from pixlstash.db_models.reference_folder import (
        ReferenceFolder,
        ReferenceFolderStatus,
    )

    with tempfile.TemporaryDirectory() as ref_dir:
        abs_path = os.path.join(ref_dir, "kept.png")
        open(abs_path, "wb").close()

        def _setup(session):
            rf = ReferenceFolder(
                folder=ref_dir,
                label="ref",
                allow_delete_file=False,
                status=ReferenceFolderStatus.ACTIVE,
            )
            session.add(rf)
            session.commit()
            session.refresh(rf)
            pic = Picture(
                file_path=abs_path,
                filename="kept.png",
                reference_folder_id=rf.id,
                deleted=False,
            )
            session.add(pic)
            session.commit()
            session.refresh(pic)
            return rf.id, pic.id

        folder_id, pic_id = server.vault.db.run_task(_setup)

        # Snapshot captures the reference picture ALIVE.
        cp = server.vault.snapshot_service.create_snapshot("MANUAL")

        # Now it is removed from the LIVE library (protected purge: file kept,
        # logged with file_removed=False). It is NOT in the live DB anymore, so
        # the live-active rescue net cannot save it - only the ledger filter can.
        def _drop(session):
            session.delete(session.get(Picture, pic_id))
            session.commit()

        server.vault.db.run_task(_drop)
        _add_deleted_log(server, abs_path, file_removed=False)

        report = server.vault.restore_service.restore_full(cp.id)

        assert report.missing_files_count == 0, "File is on disk - not missing."
        assert report.permanently_deleted_count == 0, (
            "A file_removed=False ledger entry must never count as a permanent "
            f"deletion: {report.permanently_deleted_count}"
        )
        restored = _get_picture(server, pic_id)
        assert restored is not None, (
            "Reference picture whose file was KEPT must be restored from the "
            "snapshot that captured it alive - its content is not gone."
        )
        assert restored.reference_folder_id == folder_id
        assert not restored.deleted
        assert os.path.isfile(abs_path), "The kept file must be untouched."


def test_full_restore_file_removed_true_still_drops_and_not_resurrected(server):
    """The other direction: a ``file_removed=True`` ledger entry (genuine purge,
    file gone) must STILL be dropped on restore and never resurrected - the
    never-resurrect guarantee holds after the meaning split."""
    _create_file(server, "gone.jpg")
    pic = _add_picture(server, filename="gone.jpg", description="doomed")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Genuine permanent purge after the snapshot: row dropped, file logged as
    # actually removed. (File left on disk here to prove the drop comes from the
    # ledger's file_removed=True flag, not the missing-file check.)
    def _del(session):
        session.delete(session.get(Picture, pic.id))
        session.commit()

    server.vault.db.run_task(_del)
    _add_deleted_log(server, "gone.jpg", file_removed=True)

    report = server.vault.restore_service.restore_full(cp.id)

    assert report.missing_files_count == 0, "File is on disk - not missing."
    assert report.permanently_deleted_count == 1, report.permanently_deleted_count
    assert _get_picture(server, pic.id) is None, (
        "A genuinely purged (file_removed=True) picture must not be resurrected."
    )


def test_explicit_reimport_never_resurfaces_genuinely_gone_file(server):
    """Invariant (Change 2 safety): an explicit reference-folder re-import only
    resurfaces files PRESENT on disk. A genuinely-gone file (absent on disk,
    logged file_removed=True) must NOT be re-imported by the fresh-folder scan,
    its ledger entry must survive, and a restore must still drop it and never
    resurrect it."""
    from pixlstash.db_models.reference_folder import (
        ReferenceFolder,
        ReferenceFolderStatus,
    )
    from pixlstash.tasks.reference_folder_scan_task import ReferenceFolderScanTask

    with tempfile.TemporaryDirectory() as ref_dir:
        # This path is never created on disk - the content is genuinely gone.
        gone_path = os.path.join(ref_dir, "gone.png")

        pic = _add_picture(server, filename=gone_path, pixel_sha="sha_gone")
        cp = server.vault.snapshot_service.create_snapshot("MANUAL")

        # Genuine permanent purge after the snapshot: row dropped, file absent,
        # logged as actually removed.
        def _drop(session):
            session.delete(session.get(Picture, pic.id))
            session.commit()

        server.vault.db.run_task(_drop)
        _add_deleted_log(server, gone_path, pixel_sha="sha_gone", file_removed=True)

        # Explicit re-add of the folder (pending_reimport=True - the strongest
        # override signal). Because the gone file is absent from disk it is never
        # in disk_paths, so even the explicit override cannot touch its ledger
        # entry.
        def _add_folder(session):
            rf = ReferenceFolder(
                folder=ref_dir,
                label="ref",
                allow_delete_file=False,
                status=ReferenceFolderStatus.ACTIVE,
                last_scanned=None,
                pending_reimport=True,
            )
            session.add(rf)
            session.commit()
            session.refresh(rf)
            return rf.id

        folder_id = server.vault.db.run_task(_add_folder)
        result = ReferenceFolderScanTask(
            database=server.vault.db,
            folder_id=folder_id,
            folder_path=ref_dir,
            resolved_path=ref_dir,
        )._run_task()
        assert result["new_count"] == 0, (
            f"A genuinely-gone file must never be re-imported: {result}"
        )
        assert _count_deleted_log(server, gone_path) == 1, (
            "The ledger must still guard the genuinely-gone file after re-import."
        )

        # Restore still drops it and never resurrects it.
        report = server.vault.restore_service.restore_full(cp.id)
        assert report.permanently_deleted_count >= 1, report.permanently_deleted_count
        assert _get_picture(server, pic.id) is None, (
            "A genuinely-gone (file_removed=True) file must never be resurrected."
        )


# ---------------------------------------------------------------------------
# Change 2: an EXPLICIT reference-folder re-import (the dedicated one-shot
# `pending_reimport` flag, set only by the deliberate folder-add endpoint)
# overrides the ledger; a routine sync scan does not. Driven with background
# workers off (module fixture) so the scan runs deterministically.
# ---------------------------------------------------------------------------


def _ref_ledger_flags(server, file_path):
    path_sha = DeletedFileLog.hash_path(file_path)
    return server.vault.db.run_immediate_read_task(
        lambda s: [
            r.file_removed
            for r in s.exec(
                select(DeletedFileLog).where(DeletedFileLog.path_sha == path_sha)
            ).all()
        ]
    )


def _make_ref_image(folder_dir, file_name):
    from PIL import Image

    os.makedirs(folder_dir, exist_ok=True)
    path = os.path.join(folder_dir, file_name)
    Image.new("RGB", (8, 8), color=(10, 20, 30)).save(path, format="PNG")
    return path


def _add_ref_folder(server, folder_dir, *, pending_reimport=False, last_scanned=None):
    from pixlstash.db_models.reference_folder import ReferenceFolderStatus

    def _insert(session):
        rf = ReferenceFolder(
            folder=folder_dir,
            label="refs",
            allow_delete_file=False,
            status=ReferenceFolderStatus.ACTIVE,
            last_scanned=last_scanned,
            pending_reimport=pending_reimport,
        )
        session.add(rf)
        session.commit()
        session.refresh(rf)
        return rf.id

    return server.vault.db.run_task(_insert)


def _ref_pending_reimport(server, folder_id):
    return server.vault.db.run_immediate_read_task(
        lambda s: s.get(ReferenceFolder, folder_id).pending_reimport
    )


def _index_ref_picture(server, folder_id, file_path):
    from pixlstash.utils.image_processing.image_utils import ImageUtils

    pixel_sha = ImageUtils.calculate_hash_from_file_path(file_path)

    def _insert(session):
        pic = Picture(
            file_path=file_path,
            reference_folder_id=folder_id,
            pixel_sha=pixel_sha,
            original_file_name=os.path.basename(file_path),
        )
        session.add(pic)
        session.commit()
        session.refresh(pic)
        return pic.id

    return server.vault.db.run_task(_insert)


def _run_ref_scan(server, folder_id, folder_dir):
    from pixlstash.tasks.reference_folder_scan_task import ReferenceFolderScanTask

    return ReferenceFolderScanTask(
        database=server.vault.db,
        folder_id=folder_id,
        folder_path=folder_dir,
        resolved_path=folder_dir,
    )._run_task()


def test_reference_folder_explicit_reimport_overrides_ledger(server):
    """A deliberate folder (re-)add (pending_reimport=True) must re-import a
    removed-but-kept file present on disk, clear its ledger entry so restore can
    resurface it, and clear the one-shot flag once consumed."""
    from pixlstash.utils.image_processing.image_utils import ImageUtils

    with tempfile.TemporaryDirectory() as ref_dir:
        abs_path = _make_ref_image(ref_dir, "resurfaced.png")
        pixel_sha = ImageUtils.calculate_hash_from_file_path(abs_path)
        _add_deleted_log(server, abs_path, pixel_sha=pixel_sha, file_removed=False)

        folder_id = _add_ref_folder(server, ref_dir, pending_reimport=True)
        result = _run_ref_scan(server, folder_id, ref_dir)

        assert result["new_count"] == 1, (
            f"Explicit re-import must re-import the present-on-disk file: {result}"
        )
        imported = server.vault.db.run_task(
            lambda s: s.exec(
                select(Picture).where(Picture.reference_folder_id == folder_id)
            ).all()
        )
        assert len(imported) == 1 and imported[0].file_path == abs_path
        assert _ref_ledger_flags(server, abs_path) == [], (
            "The ledger entry for the resurfaced file must be cleared."
        )
        assert _ref_pending_reimport(server, folder_id) is False, (
            "The one-shot pending_reimport flag must be cleared after the scan."
        )


def test_reference_folder_reimport_flag_is_one_shot(server):
    """Once a scan consumes pending_reimport, a subsequent scan is a routine scan
    and does NOT override the ledger - the override cannot recur without a fresh
    deliberate add."""
    from pixlstash.utils.image_processing.image_utils import ImageUtils

    with tempfile.TemporaryDirectory() as ref_dir:
        abs_path = _make_ref_image(ref_dir, "once.png")
        pixel_sha = ImageUtils.calculate_hash_from_file_path(abs_path)

        folder_id = _add_ref_folder(server, ref_dir, pending_reimport=True)
        # First scan consumes the flag and imports the file.
        _run_ref_scan(server, folder_id, ref_dir)
        assert _ref_pending_reimport(server, folder_id) is False

        # A new removed-but-kept ledger entry appears for that path (e.g. the
        # picture is scrapheaped-and-purged while protected). A second, routine
        # scan must NOT resurface it - the flag is already spent.
        server.vault.db.run_task(
            lambda s: s.exec(delete(Picture).where(Picture.file_path == abs_path))
        )
        _add_deleted_log(server, abs_path, pixel_sha=pixel_sha, file_removed=False)

        result = _run_ref_scan(server, folder_id, ref_dir)
        assert result["new_count"] == 0, (
            f"A spent flag must not override the ledger again: {result}"
        )
        assert _ref_ledger_flags(server, abs_path) == [False], (
            "A routine second scan must leave the ledger entry intact."
        )


def test_reference_folder_routine_rescan_does_not_reimport(server):
    """A routine re-scan of an existing folder (pending_reimport=False) must NOT
    auto re-import a removed-but-kept file and must leave its ledger intact."""
    from pixlstash.utils.image_processing.image_utils import ImageUtils

    with tempfile.TemporaryDirectory() as ref_dir:
        abs_path = _make_ref_image(ref_dir, "kept.png")
        pixel_sha = ImageUtils.calculate_hash_from_file_path(abs_path)
        _add_deleted_log(server, abs_path, pixel_sha=pixel_sha, file_removed=False)

        import time as _time

        folder_id = _add_ref_folder(
            server, ref_dir, pending_reimport=False, last_scanned=_time.time()
        )
        result = _run_ref_scan(server, folder_id, ref_dir)

        assert result["new_count"] == 0, (
            f"Routine re-scan must not auto re-import a removed-but-kept file: {result}"
        )
        assert (
            server.vault.db.run_task(
                lambda s: s.exec(
                    select(Picture).where(Picture.reference_folder_id == folder_id)
                ).all()
            )
            == []
        )
        assert _ref_ledger_flags(server, abs_path) == [False], (
            "Routine re-scan must leave the ledger entry intact."
        )


def test_reference_folder_emptied_then_last_scanned_reset_no_override(server):
    """Edge closed by the dedicated flag: an already-emptied folder (zero indexed
    pictures) whose last_scanned is reset to None by a sync-toggle / rename /
    mount-recovery is NOT an explicit re-import (pending_reimport stays False), so
    a removed-but-kept file present on disk must NOT resurface and its ledger
    entry must be retained. Under the old last_scanned heuristic this would have
    wrongly fired."""
    from pixlstash.utils.image_processing.image_utils import ImageUtils

    with tempfile.TemporaryDirectory() as ref_dir:
        kept_path = _make_ref_image(ref_dir, "kept.png")
        kept_sha = ImageUtils.calculate_hash_from_file_path(kept_path)
        _add_deleted_log(server, kept_path, pixel_sha=kept_sha, file_removed=False)

        # Zero indexed pictures AND last_scanned reset to None - the exact shape
        # that the removed heuristic misread as a fresh re-add.
        folder_id = _add_ref_folder(
            server, ref_dir, pending_reimport=False, last_scanned=None
        )
        result = _run_ref_scan(server, folder_id, ref_dir)

        assert result["new_count"] == 0, (
            f"An emptied folder with reset last_scanned must not re-import: {result}"
        )
        assert _ref_ledger_flags(server, kept_path) == [False], (
            "The ledger entry must be retained - this is not an explicit re-import."
        )
        assert (
            server.vault.db.run_task(
                lambda s: s.exec(
                    select(Picture).where(Picture.file_path == kept_path)
                ).all()
            )
            == []
        ), "Removed-but-kept file must not resurface without a deliberate re-add."


def test_full_restore_ledger_rescue_drops_when_content_differs(server):
    """CSO purge-evasion: a ledger-matched snapshot row must NOT be rescued when
    DIFFERENT content now sits at the same path.

    Content C1 was purged (its path AND pixel_sha are in the ledger); different
    content C2 (different pixel_sha) is alive at the same path. The path-only
    rescue would keep C1's stale row on the strength of the path collision; the
    content-aware rescue must leave it dropped because the two shas are known
    and differ.
    """
    _create_file(server, "evasion.jpg")
    c1 = _add_picture(server, filename="evasion.jpg", pixel_sha="C1_sha")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # After the snapshot: C1 is purged (row dropped + logged by path AND content)
    # and DIFFERENT content C2 is indexed alive at the same path.
    _drop_picture_row(server, c1.id)
    _add_deleted_log(server, "evasion.jpg", pixel_sha="C1_sha")
    _add_picture(server, filename="evasion.jpg", pixel_sha="C2_sha")

    report = server.vault.restore_service.restore_full(cp.id)

    assert _get_picture(server, c1.id) is None, (
        "stale purged content (C1) must NOT be resurrected when different live "
        "content (C2) sits at the same path - this is the purge-evasion vector"
    )
    assert report.permanently_deleted_count == 1, (
        f"C1 must still count as permanently deleted: {report.permanently_deleted_count}"
    )


def test_full_restore_ledger_rescue_keeps_when_content_matches(server):
    """A ledger-matched row whose live content MATCHES (same file re-indexed →
    same pixel_sha) must be rescued and survive restore."""
    _create_file(server, "match.jpg")
    pic = _add_picture(server, filename="match.jpg", pixel_sha="M_sha")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Its path was logged by an earlier protected-folder purge, but the SAME
    # content is alive and in active use (pixel_sha unchanged).
    _add_deleted_log(server, "match.jpg", pixel_sha="OTHER_sha")

    report = server.vault.restore_service.restore_full(cp.id)

    assert _get_picture(server, pic.id) is not None, (
        "a ledger-matched picture whose live content matches must be rescued"
    )
    assert report.permanently_deleted_count == 0, (
        f"matching live content must not count as purged: {report.permanently_deleted_count}"
    )


def test_full_restore_ledger_rescue_keeps_when_live_sha_null(server):
    """NULL fallback: a ledger-matched row must be rescued when the live-active
    picture at its path is not yet hashed (pixel_sha=NULL).

    This is the not-yet-hashed reference-folder picture - the maintainer's
    explicit non-strict choice: unconfirmable content must be kept, never
    re-dropped.
    """
    _create_file(server, "nullref.jpg")
    snap_pic = _add_picture(server, filename="nullref.jpg", pixel_sha="N_sha")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # After the snapshot: the path is logged, the old row dropped, and the file
    # re-indexed as a NEW alive picture that has not been hashed yet.
    _drop_picture_row(server, snap_pic.id)
    _add_deleted_log(server, "nullref.jpg")
    _add_picture(server, filename="nullref.jpg", pixel_sha=None)

    report = server.vault.restore_service.restore_full(cp.id)

    assert _get_picture(server, snap_pic.id) is not None, (
        "a ledger-matched row must be rescued when the live picture is not yet "
        "hashed (NULL pixel_sha) - the non-strict NULL fallback"
    )
    assert report.permanently_deleted_count == 0, (
        f"unconfirmable content must not count as purged: {report.permanently_deleted_count}"
    )


def test_restore_resource_refuses_permanently_deleted_picture(server):
    """Per-resource restore must skip a picture matched by content hash in
    deleted_file_log (sha match, with a different recorded path)."""
    _create_file(server, "gone.jpg")
    pic = _add_picture(server, filename="gone.jpg", description="original")

    # Give the picture a content hash, then record that hash as deleted under
    # a different path - exercises the pixel_sha matching branch.
    def _set_sha(session):
        p = session.get(Picture, pic.id)
        p.pixel_sha = "sha-deadbeef"
        session.commit()

    server.vault.db.run_task(_set_sha)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _del(session):
        session.delete(session.get(Picture, pic.id))
        session.commit()

    server.vault.db.run_task(_del)
    _add_deleted_log(server, "some-other-path.jpg", pixel_sha="sha-deadbeef")

    report = server.vault.restore_service.restore_resource(cp.id, "picture", pic.id)

    assert report.permanently_deleted_count == 1, report.permanently_deleted_count
    assert report.upserted_count == 0, "Nothing should be upserted."
    assert _get_picture(server, pic.id) is None, (
        "restore_resource must not resurrect a permanently-deleted picture."
    )


def test_full_restore_ratio_check_excludes_permanent_deletions(server):
    """Permanent deletions must not trip the >50%-missing mount-failure guard.

    12 pictures are snapshotted; 8 are then permanently deleted (files removed
    AND logged). Their files are missing on disk (8/12 = 67% > 50%), which
    without the ledger cross-check would refuse the restore as a suspected
    mount failure. With it, the 8 are excluded from the suspicious ratio and
    the restore proceeds, dropping all 8."""
    kept_ids = []
    deleted_ids = []
    for i in range(12):
        name = f"ratio_{i}.jpg"
        _create_file(server, name)
        p = _add_picture(server, filename=name)
        (deleted_ids if i < 8 else kept_ids).append((p.id, name))

    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Permanently delete the first 8: remove files + record the deletions.
    for pid, name in deleted_ids:
        _remove_file(server, name)
        _add_deleted_log(server, name)

    # Must NOT raise (the 8 are known deletions, not a mount failure).
    report = server.vault.restore_service.restore_full(cp.id)

    assert report.missing_files_count == 8, report.missing_files_count
    assert report.permanently_deleted_count == 8, report.permanently_deleted_count
    for pid, _ in deleted_ids:
        assert _get_picture(server, pid) is None
    for pid, _ in kept_ids:
        assert _get_picture(server, pid) is not None


def test_preview_full_warns_about_permanently_deleted(server):
    """The full-restore preview surfaces a permanently-deleted count/warning."""
    _create_file(server, "prev_del.jpg")
    pic = _add_picture(server, filename="prev_del.jpg")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    _add_deleted_log(server, "prev_del.jpg")

    preview = server.vault.restore_service.preview_full(cp.id)
    assert preview.summary.get("permanently_deleted") == 1, preview.summary
    assert any("permanently deleted" in w for w in preview.warnings), preview.warnings
    assert pic.id  # silence unused-var linters


# ---------------------------------------------------------------------------
# Legacy snapshots at an *intermediate* schema (has metadata_hash, but predates
# later columns) must be alembic-upgraded, not sniffed for a single column.
# ---------------------------------------------------------------------------

# Revision that introduced tags_file / description_file; a snapshot stamped at
# its parent has metadata_hash (added back in 0049) but not the sidecar columns.
_INTERMEDIATE_REVISION = "0056_add_hide_purge_snapshot_warning"
_INTERMEDIATE_ONLY_COLUMNS = (
    "tags_file",
    "tags_file_mtime",
    "description_file",
    "description_file_mtime",
)


def _register_legacy_uncompressed_snapshot(server, cp, *, with_sidecar: bool):
    """Materialize *cp* to a plain ``.sqlite`` and register it as a snapshot.

    Args:
        server: The test server fixture.
        cp: The compressed snapshot to copy from.
        with_sidecar: When False the registered snapshot points at a manifest
            path with no ``.hashes.json`` beside it, so ``compare_hashes``
            takes the legacy in-file read path instead of the sidecar path.

    Returns:
        Tuple of (snapshot_id, absolute path to the plain .sqlite file).
    """
    from pixlstash.utils.snapshot_compression import materialize_snapshot

    vault_root = server.vault.image_root
    legacy_rel = cp.relative_path[: -len(".zst")]
    legacy_abs = os.path.join(vault_root, legacy_rel)
    materialize_snapshot(os.path.join(vault_root, cp.relative_path), legacy_abs)

    manifest_rel = (
        cp.manifest_relative_path
        if with_sidecar
        else legacy_rel + ".no-sidecar.manifest.json"
    )

    def _register(session):
        row = Snapshot(
            kind="MANUAL",
            created_at=cp.created_at,
            relative_path=legacy_rel,
            manifest_relative_path=manifest_rel,
            byte_size=os.path.getsize(legacy_abs),
            picture_count=cp.picture_count,
            schema_version=cp.schema_version,
            label="legacy",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id

    return server.vault.db.run_task(_register), legacy_abs


def _downgrade_snapshot_to_intermediate_schema(abs_snapshot: str):
    """Rewrite *abs_snapshot* to look like a pre-0057 snapshot.

    Drops the caption-sidecar columns added by 0057, back-dates
    ``alembic_version`` to 0057's parent, and NULLs ``metadata_hash`` so the
    read path is forced through the full-entity hash computation - the exact
    combination that produced ``no such column: picture.tags_file``.
    """
    import sqlite3

    with closing(sqlite3.connect(abs_snapshot)) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(picture)").fetchall()}
        assert "metadata_hash" in cols, "Pre-test invariant: snapshot has metadata_hash"
        for col in _INTERMEDIATE_ONLY_COLUMNS:
            assert col in cols, f"Pre-test invariant: snapshot has {col}"
            conn.execute(f"ALTER TABLE picture DROP COLUMN {col}")
        conn.execute("UPDATE picture SET metadata_hash = NULL")
        conn.execute(
            "UPDATE alembic_version SET version_num = ?", (_INTERMEDIATE_REVISION,)
        )
        conn.commit()


def _snapshot_columns(abs_snapshot: str) -> set:
    import sqlite3

    with closing(sqlite3.connect(abs_snapshot)) as conn:
        return {r[1] for r in conn.execute("PRAGMA table_info(picture)").fetchall()}


def _snapshot_revision(abs_snapshot: str) -> str:
    import sqlite3

    with closing(sqlite3.connect(abs_snapshot)) as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def test_compare_hashes_upgrades_intermediate_schema_snapshot(server):
    """A snapshot that *has* ``metadata_hash`` but predates ``tags_file`` must
    still be alembic-upgraded before its hashes are read.

    The old code probed for ``metadata_hash`` alone, concluded the file was
    current, and then blew up with ``no such column: picture.tags_file`` on the
    ORM entity load - swallowing the error and reporting every picture as
    changed. An unmodified picture must come back as *identical*.
    """
    _create_file(server, "intermediate.jpg")
    pic = _add_picture(server, filename="intermediate.jpg", description="same")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    legacy_id, legacy_abs = _register_legacy_uncompressed_snapshot(
        server, cp, with_sidecar=False
    )
    _downgrade_snapshot_to_intermediate_schema(legacy_abs)
    assert "tags_file" not in _snapshot_columns(legacy_abs)

    result = server.vault.restore_service.compare_hashes(legacy_id, [pic.id])

    assert result["identical_ids"] == [pic.id], (
        "An unchanged picture in an intermediate-schema snapshot must compare "
        f"identical after the schema upgrade; got {result}"
    )
    assert result["changed_ids"] == []
    # The upgrade must have been written back to the snapshot file itself.
    assert "tags_file" in _snapshot_columns(legacy_abs), (
        "compare_hashes must persist the upgraded schema into the snapshot"
    )
    assert _snapshot_revision(legacy_abs) != _INTERMEDIATE_REVISION


def test_compare_hashes_intermediate_schema_snapshot_detects_mutation(server):
    """The upgrade path must not over-match: a picture genuinely changed since
    the intermediate-schema snapshot still lands in ``changed_ids``."""
    _create_file(server, "intermediate_diff.jpg")
    pic = _add_picture(server, filename="intermediate_diff.jpg", description="orig")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    legacy_id, legacy_abs = _register_legacy_uncompressed_snapshot(
        server, cp, with_sidecar=False
    )
    _downgrade_snapshot_to_intermediate_schema(legacy_abs)

    def _mutate(session):
        session.get(Picture, pic.id).description = "different"
        session.commit()

    server.vault.db.run_task(_mutate)

    result = server.vault.restore_service.compare_hashes(legacy_id, [pic.id])
    assert result["changed_ids"] == [pic.id], (
        f"Mutated picture must still be reported as changed; got {result}"
    )
    assert result["identical_ids"] == []


def test_compare_hashes_leaves_current_snapshot_untouched(server):
    """A legacy uncompressed snapshot already at head must not be rewritten.

    The schema-currency check is there to upgrade stale files, not to churn
    every snapshot on every compare.
    """
    _create_file(server, "current_legacy.jpg")
    pic = _add_picture(server, filename="current_legacy.jpg", description="same")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    legacy_id, legacy_abs = _register_legacy_uncompressed_snapshot(
        server, cp, with_sidecar=False
    )
    revision_before = _snapshot_revision(legacy_abs)
    stat_before = os.stat(legacy_abs)

    result = server.vault.restore_service.compare_hashes(legacy_id, [pic.id])

    assert result["identical_ids"] == [pic.id], result
    stat_after = os.stat(legacy_abs)
    assert stat_after.st_mtime_ns == stat_before.st_mtime_ns, (
        "A snapshot already at head must not be rewritten by compare_hashes"
    )
    assert stat_after.st_size == stat_before.st_size
    assert _snapshot_revision(legacy_abs) == revision_before


def test_backfill_all_snapshot_hashes_repairs_intermediate_schema_snapshot(server):
    """``backfill_all_snapshot_hashes`` must repair an intermediate-schema
    snapshot rather than failing on it.

    The old single-column probe took the "column already exists" branch and ran
    the hash fill straight against the un-upgraded file, which raised on the
    ORM load and left the snapshot permanently broken.
    """
    from sqlmodel import create_engine

    _create_file(server, "backfill_intermediate.jpg")
    pic = _add_picture(server, filename="backfill_intermediate.jpg")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    _legacy_id, legacy_abs = _register_legacy_uncompressed_snapshot(
        server, cp, with_sidecar=False
    )
    _downgrade_snapshot_to_intermediate_schema(legacy_abs)

    server.vault.restore_service.backfill_all_snapshot_hashes()

    assert "tags_file" in _snapshot_columns(legacy_abs), (
        "backfill_all_snapshot_hashes must upgrade an intermediate-schema snapshot"
    )
    assert _snapshot_revision(legacy_abs) != _INTERMEDIATE_REVISION

    engine = create_engine(f"sqlite:///{legacy_abs}", echo=False)
    try:
        from sqlmodel import Session as _Session

        with _Session(engine) as session:
            stored = session.get(Picture, pic.id).metadata_hash
    finally:
        engine.dispose()
    assert stored is not None, (
        "backfill must fill the NULL metadata_hash in the repaired snapshot"
    )


# ---------------------------------------------------------------------------
# Snapshot engine configuration (issue #709): every engine the restore package
# opens on a snapshot file goes through ``snapshot_engine``, which pairs the
# vault engine's busy timeout / page cache / custom functions with the one
# deliberate deviation a snapshot needs (no WAL, so it stays a single file).
# ---------------------------------------------------------------------------


def _make_legacy_snapshot(server, filename: str):
    """Create a picture, snapshot it, and register a plain ``.sqlite`` copy."""
    _create_file(server, filename)
    pic = _add_picture(server, filename=filename)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")
    snap_id, legacy_abs = _register_legacy_uncompressed_snapshot(
        server, cp, with_sidecar=False
    )
    return pic, snap_id, legacy_abs


def test_snapshot_engine_is_configured_like_the_vault_engine(server):
    """A snapshot engine is not a bare ``create_engine``.

    Before #709 these nine engines ran on SQLite's defaults: a 5 s busy
    timeout, a 2 MiB page cache and no FK enforcement.
    """
    from pixlstash.database import SQLITE_BUSY_TIMEOUT_S, SQLITE_CACHE_SIZE_KIB
    from pixlstash.services.restore.schema_upgrade import snapshot_engine

    _pic, _snap_id, legacy_abs = _make_legacy_snapshot(server, "engine_cfg.jpg")

    engine = snapshot_engine(legacy_abs)
    try:
        with engine.connect() as conn:
            assert (
                conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
                == SQLITE_BUSY_TIMEOUT_S * 1000
            )
            assert (
                conn.exec_driver_sql("PRAGMA cache_size").scalar()
                == SQLITE_CACHE_SIZE_KIB
            )
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            # The deviation: a snapshot must stay a single file.
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() != "wal"
    finally:
        engine.dispose()

    for suffix in ("-wal", "-shm"):
        assert not os.path.exists(legacy_abs + suffix), (
            f"snapshot engine must not leave a {suffix} companion behind"
        )


def _engine_pragmas(engine) -> dict:
    """Read the §13 settings off a *real* pooled connection of *engine*.

    Every one of these is per-connection state applied by the ``connect``
    listener plus ``connect_args``, so reading them back from a connection the
    pool actually hands out is the only assertion that proves both halves of
    the configuration arrived. Asserting that some constructor was called
    would not.
    """
    with engine.connect() as conn:
        return {
            name: conn.exec_driver_sql(f"PRAGMA {name}").scalar()
            for name in ("journal_mode", "foreign_keys", "cache_size", "busy_timeout")
        }


def test_full_restore_rebuilds_the_live_engine_with_the_startup_configuration(
    server, tmp_path
):
    """The engine rebuilt after the DB swap is configured like the startup one.

    This is issue #651 itself: ``_swap_database`` disposes the live engine and
    builds a replacement, and for a while that replacement was written out by
    hand, so the two definitions drifted. Settings are read back from a real
    pooled connection of the rebuilt engine, because they are per-connection
    state applied by ``connect_args`` and the ``connect`` listener; asserting
    that some constructor was called would prove nothing.

    The reference is a fresh ``create_configured_engine``, the exact
    expression ``VaultDatabase.__init__`` uses, rather than the live engine
    read before the restore. The live engine's pool hands back *recycled*
    connections, and this module's ``clean_db`` fixture runs
    ``PRAGMA foreign_keys = OFF`` on one of them, which sticks for that
    connection's lifetime. That reference is not circular with respect to what
    is under test here: ``_swap_database`` reverting to a bare
    ``create_engine`` breaks the comparison. The other link in the chain,
    helper == real startup engine, is asserted in
    ``tests/test_database_engine_config.py``.

    Note which pragmas discriminate: the file swapped in is a snapshot, i.e. a
    rollback-journal file, so a bare ``create_engine`` here reports
    ``journal_mode=delete``, ``foreign_keys=0``, SQLite's default 2 MiB cache
    and a 5 s busy timeout.
    """
    from pixlstash.database import (
        SQLITE_BUSY_TIMEOUT_S,
        SQLITE_CACHE_SIZE_KIB,
        create_configured_engine,
    )

    _create_file(server, "engine_after_swap.jpg")
    pic = _add_picture(
        server, filename="engine_after_swap.jpg", description="before swap"
    )
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    engine_before = server.vault.db._engine

    report = server.vault.restore_service.restore_full(cp.id)
    assert not report.errors, f"Restore errors: {report.errors}"

    engine_after = server.vault.db._engine
    assert engine_after is not engine_before, (
        "the restore did not rebuild the engine, so this test proves nothing "
        "about the rebuild"
    )
    settings_after = _engine_pragmas(engine_after)

    reference_engine = create_configured_engine(tmp_path / "startup-reference.db")
    try:
        settings_reference = _engine_pragmas(reference_engine)
    finally:
        reference_engine.dispose()

    assert settings_after == settings_reference, (
        "the engine rebuilt by _swap_database drifted from the startup engine "
        f"(#651): startup={settings_reference} rebuilt={settings_after}"
    )
    # Absolute values too: equality alone would also hold if BOTH engines were
    # built badly.
    assert settings_after["journal_mode"] == "wal", settings_after
    assert settings_after["foreign_keys"] == 1, settings_after
    assert settings_after["cache_size"] == SQLITE_CACHE_SIZE_KIB, settings_after
    assert settings_after["busy_timeout"] == SQLITE_BUSY_TIMEOUT_S * 1000, (
        settings_after
    )

    # The custom SQL functions are the other half of the connect listener and
    # travel with the same configuration.
    with engine_after.connect() as conn:
        assert isinstance(
            conn.exec_driver_sql("SELECT levenshtein('kitten', 'sitting')").scalar(),
            float,
        )
        assert isinstance(
            conn.exec_driver_sql(
                "SELECT levenshtein_with_id('kitten', 'sitting', 1)"
            ).scalar(),
            float,
        )

    # And the restore itself really did run against the rebuilt engine.
    assert _get_picture(server, pic.id) is not None


def test_preview_paths_open_snapshots_through_the_shared_engine_helper(server):
    """The preview / compare paths must all route through ``snapshot_engine``.

    Asserted behaviourally: each engine they open is counted, so a call site
    that goes back to a bare ``create_engine`` shows up as a miss.
    """
    import pixlstash.services.restore.preview as preview_mod
    from pixlstash.services.restore.schema_upgrade import snapshot_engine

    pic, snap_id, _legacy_abs = _make_legacy_snapshot(server, "engine_routing.jpg")

    calls = []

    def _counting(db_path):
        calls.append(db_path)
        return snapshot_engine(db_path)

    original = preview_mod.snapshot_engine
    preview_mod.snapshot_engine = _counting
    try:
        svc = server.vault.restore_service
        svc.preview_full(snap_id)
        svc.preview_resource(snap_id, "picture", pic.id)
        svc.preview_batch(snap_id, [{"type": "picture", "id": pic.id}])
        svc.compare_hashes(snap_id, [pic.id])
    finally:
        preview_mod.snapshot_engine = original

    # Three previews plus the legacy in-file read in compare_hashes.
    assert len(calls) >= 4, calls


def test_full_restore_paths_open_snapshots_through_the_shared_engine_helper(server):
    """``full_restore``'s two snapshot readers must route through the helper.

    Patching ``preview.snapshot_engine`` only ever covered ``preview.py``;
    ``full_restore.py`` imports the name into its own module namespace, so it
    needs its own spy. Each of its two call sites is pinned separately (a
    revert of just one would otherwise hide behind the other), then a real
    end-to-end ``restore_full`` proves the flow reaches both.
    """
    import pixlstash.services.restore.full_restore as full_mod
    from pixlstash.services.restore.schema_upgrade import snapshot_engine

    _pic, snap_id, legacy_abs = _make_legacy_snapshot(server, "engine_full.jpg")
    svc = server.vault.restore_service

    calls = []

    def _counting(db_path):
        calls.append(db_path)
        return snapshot_engine(db_path)

    original = full_mod.snapshot_engine
    full_mod.snapshot_engine = _counting
    try:
        # Site 1: the missing-file scan.
        svc._find_missing_file_ids(legacy_abs, server.vault.image_root)
        assert len(calls) == 1, (
            f"_find_missing_file_ids did not open the snapshot through "
            f"snapshot_engine: {calls}"
        )

        # Site 2: the permanent-deletion ledger cross-check. It short-circuits
        # on an empty ledger, so hand it a hash to look for.
        calls.clear()
        svc._find_permanently_deleted_ids(legacy_abs, {"0" * 64}, set())
        assert len(calls) == 1, (
            f"_find_permanently_deleted_ids did not open the snapshot through "
            f"snapshot_engine: {calls}"
        )

        # End to end: a real restore reaches both. A ledger row for an
        # unrelated path keeps site 2 live without matching anything in the
        # snapshot.
        calls.clear()
        _add_deleted_log(server, "purged_elsewhere.jpg")
        report = svc.restore_full(snap_id)
        assert not report.errors, f"Restore errors: {report.errors}"
    finally:
        full_mod.snapshot_engine = original

    assert len(calls) >= 2, (
        f"a full restore must open the snapshot through snapshot_engine for "
        f"both the missing-file scan and the deletion-ledger scan: {calls}"
    )


def test_resource_restore_paths_open_snapshots_through_the_shared_engine_helper(
    server,
):
    """``resource_restore``'s two snapshot readers must route through the helper.

    Same reasoning as the ``full_restore`` case: its own module namespace, its
    own spy, and each call site pinned by its own restore call so reverting
    one is not masked by the other.
    """
    import pixlstash.services.restore.resource_restore as resource_mod
    from pixlstash.services.restore.schema_upgrade import snapshot_engine

    pic, snap_id, _legacy_abs = _make_legacy_snapshot(server, "engine_resource.jpg")
    svc = server.vault.restore_service

    calls = []

    def _counting(db_path):
        calls.append(db_path)
        return snapshot_engine(db_path)

    original = resource_mod.snapshot_engine
    resource_mod.snapshot_engine = _counting
    try:
        # Site 1: single-resource restore.
        report = svc.restore_resource(snap_id, "picture", pic.id)
        assert not report.errors, f"restore_resource errors: {report.errors}"
        assert len(calls) >= 1, (
            f"restore_resource did not open the snapshot through "
            f"snapshot_engine: {calls}"
        )

        # Site 2: the batch dependency collection.
        calls.clear()
        svc.restore_batch(snap_id, [{"type": "picture", "id": pic.id}])
        assert len(calls) >= 1, (
            f"restore_batch did not open the snapshot through snapshot_engine: {calls}"
        )
    finally:
        resource_mod.snapshot_engine = original


def test_snapshot_hash_backfill_survives_foreign_key_violations(server):
    """The only restore path that WRITES to a snapshot runs with FKs enforced.

    A snapshot is restored as a unit and may legitimately hold rows that
    violate constraints (an orphaned tag, a dangling parent reference). The
    hash backfill only ever updates ``picture.metadata_hash``, which is
    neither a child key nor a parent key, so SQLite runs no FK check and the
    write must still succeed. This is the assertion that made it safe to leave
    FK enforcement ON for snapshot engines rather than silently off (#709).

    Both directions are asserted. The positive one alone would also pass with
    ``foreign_keys=OFF``, which would leave the setting pinned by nothing but a
    single PRAGMA read, so the same connection is then made to *refuse* a
    genuine violation.
    """
    from pixlstash.services.restore.schema_upgrade import snapshot_engine

    pic, _snap_id, legacy_abs = _make_legacy_snapshot(server, "fk_violation.jpg")

    with closing(sqlite3.connect(legacy_abs)) as conn:
        conn.execute("UPDATE picture SET metadata_hash = NULL")
        # An orphaned child row: tag.picture_id has a real FK to picture.id.
        conn.execute(
            "INSERT INTO tag (picture_id, tag) VALUES (?, ?)", (999999, "orphan")
        )
        conn.commit()
        violations_before = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert violations_before, "pre-test invariant: the snapshot really is violating"

    server.vault.restore_service._backfill_snapshot(legacy_abs)

    engine = snapshot_engine(legacy_abs)
    try:
        with engine.connect() as conn:
            stored = conn.exec_driver_sql(
                f"SELECT metadata_hash FROM picture WHERE id = {pic.id}"
            ).scalar()
            assert stored is not None, (
                "the backfill must still write through FK enforcement"
            )

            # Negative direction, on the SAME connection: an insert that really
            # does violate tag.picture_id -> picture.id must be rejected. With
            # foreign_keys=OFF this INSERT succeeds, which is exactly the state
            # the positive assertion above cannot tell apart.
            with pytest.raises(IntegrityError) as excinfo:
                conn.exec_driver_sql(
                    "INSERT INTO tag (picture_id, tag) VALUES (424242, 'must-fail')"
                )
            assert "FOREIGN KEY" in str(excinfo.value).upper(), str(excinfo.value)
            conn.rollback()
    finally:
        engine.dispose()

    with closing(sqlite3.connect(legacy_abs)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        # The backfill must not have silently "repaired" the snapshot either.
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == violations_before
    for suffix in ("-wal", "-shm"):
        assert not os.path.exists(legacy_abs + suffix)


# ---------------------------------------------------------------------------
# Data-loss regression: restore must not resurrect a scrapheap "ghost" that
# shadows a file re-added after the snapshot, or emptying the scrapheap would
# hard-delete that live file.
# ---------------------------------------------------------------------------


def _add_scrapheap_picture(server, filename, pixel_sha=None):
    """Insert a soft-deleted (scrapheap) picture and return its id."""

    def _do(session):
        pic = Picture(
            file_path=filename,
            filename=filename,
            deleted=True,
            pixel_sha=pixel_sha,
        )
        session.add(pic)
        session.commit()
        session.refresh(pic)
        return pic.id

    return server.vault.db.run_task(_do)


def _drop_picture_row(server, pic_id):
    def _do(session):
        pic = session.get(Picture, pic_id)
        if pic is not None:
            session.delete(pic)
            session.commit()

    server.vault.db.run_task(_do)


def _empty_scrapheap_via_http(server):
    """Drive the real DELETE /pictures/scrapheap endpoint as the owner."""
    from fastapi.testclient import TestClient

    client = TestClient(server.api, raise_server_exceptions=True)
    login = client.post(
        "/api/v1/login",
        json={"username": "owner", "password": "example-owner-password"},
    )
    assert login.status_code == 200, login.text
    # The destructive endpoint refuses without the single-use confirm_token the
    # preview mints, so drive the real preview -> confirm flow.
    preview = client.post("/api/v1/pictures/scrapheap/delete-preview", json={})
    assert preview.status_code == 200, preview.text
    resp = client.request(
        "DELETE",
        "/api/v1/pictures/scrapheap",
        json={"confirm_token": preview.json()["confirm_token"]},
    )
    assert resp.status_code == 200, resp.text
    # The endpoint deletes files in a FastAPI BackgroundTask; TestClient runs
    # those synchronously before returning, so the filesystem is settled here.
    return resp.json()


def test_full_restore_does_not_hard_delete_file_readded_after_snapshot(server):
    """A scrapheap row in the snapshot must not delete a live re-added file.

    Reproduces the live-instance data loss: a picture is soft-deleted at
    ``shared.jpg`` before the snapshot; after the snapshot the same path is
    re-used by a NEW active picture (content added after the snapshot, and NOT
    recorded in deleted_file_log). Restoring the snapshot must not resurrect the
    stale scrapheap row on top of the live file, because emptying the scrapheap
    would then hard-delete a file the user legitimately added after the
    snapshot.
    """
    shared = _create_file(server, "shared.jpg")
    with open(shared, "wb") as fh:
        fh.write(b"OLD-DELETED-CONTENT")
    # Snapshot captures the picture as a scrapheap (deleted=True) row.
    _add_scrapheap_picture(server, "shared.jpg", pixel_sha="sha_old")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # After the snapshot: the path is re-used by a new ACTIVE picture whose file
    # content differs (added after the snapshot). The old scrapheap row is gone
    # from the live DB.
    with open(shared, "wb") as fh:
        fh.write(b"NEW-LIVE-CONTENT-ADDED-AFTER-SNAPSHOT")
    _add_picture(server, filename="shared.jpg")

    report = server.vault.restore_service.restore_full(cp.id)
    assert not report.errors, f"Restore errors: {report.errors}"

    # The file must still exist immediately after restore (restore never
    # touches image files) ...
    assert os.path.isfile(shared), "restore itself must never delete image files"

    # ... and the dangerous scrapheap ghost must NOT have been resurrected, so
    # emptying the scrapheap cannot target the live file.
    scrapheap_ids = server.vault.db.run_immediate_read_task(
        lambda s: [
            p.id for p in s.exec(select(Picture).where(Picture.deleted.is_(True))).all()
        ]
    )
    assert scrapheap_ids == [], (
        "restore resurrected a scrapheap ghost shadowing a live re-added file; "
        "emptying the scrapheap would hard-delete that file"
    )

    result = _empty_scrapheap_via_http(server)
    assert result["deleted_count"] == 0, result

    assert os.path.isfile(shared), (
        "FILE LOSS: a file added after the snapshot was hard-deleted by the "
        "restore -> empty-scrapheap cycle"
    )
    # The live file must not have been recorded as permanently deleted.
    assert _count_deleted_log(server, "shared.jpg") == 0, (
        "the added-after file was logged in deleted_file_log - it was purged"
    )


def test_full_restore_preserves_genuine_scrapheap_picture(server):
    """The fix must not over-drop: a real scrapheap entry still round-trips.

    A picture soft-deleted before the snapshot whose file is NOT re-used by any
    live active picture must still be resurrected by restore (so it remains
    recoverable / purgeable as before).
    """
    trashed = _create_file(server, "trashed.jpg")
    with open(trashed, "wb") as fh:
        fh.write(b"TRASHED-CONTENT")
    pic_id = _add_scrapheap_picture(server, "trashed.jpg", pixel_sha="sha_trash")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Mutate the live DB so the restore has something to revert, but do NOT
    # re-use trashed.jpg for any active picture.
    _drop_picture_row(server, pic_id)

    report = server.vault.restore_service.restore_full(cp.id)
    assert not report.errors, f"Restore errors: {report.errors}"

    restored = server.vault.db.run_immediate_read_task(
        lambda s: s.exec(
            select(Picture).where(Picture.file_path == "trashed.jpg")
        ).first()
    )
    assert restored is not None and restored.deleted, (
        "a genuine scrapheap picture (no live shadow) must survive restore"
    )


@pytest.mark.parametrize("ghost_path_kind", ["absolute", "dot_slash", "double_slash"])
def test_full_restore_ghost_path_drift_does_not_delete_live_file(
    server, ghost_path_kind
):
    """Ghost/live path-string drift must not defeat the guard.

    The scrapheap deleter removes ``resolve_picture_path(image_root, file_path)``,
    so the guard must match on the RESOLVED path too - otherwise a ghost whose
    stored path differs as a STRING but resolves to the same on-disk file (the
    reference-folder ABSOLUTE vs imported RELATIVE collision, plus ``./`` /
    ``//`` drift) survives restore and is hard-deleted on the next empty of the
    scrapheap. One file on disk is referenced by the snapshot ghost via a
    drifted path and by the live active picture via the plain relative path;
    after restore + empty-scrapheap the live file must still exist.
    """
    root = server.vault.image_root
    rel = "drift.jpg"
    abs_path = _create_file(server, rel)
    with open(abs_path, "wb") as fh:
        fh.write(b"OLD-DELETED-CONTENT")

    if ghost_path_kind == "absolute":
        ghost_path = os.path.join(root, rel)
    elif ghost_path_kind == "dot_slash":
        ghost_path = "./" + rel
    else:  # double_slash: an absolute path with a doubled separator
        ghost_path = os.path.join(root, "") + "/" + rel

    # Snapshot captures the scrapheap ghost referencing the file via the drifted
    # path string.
    _add_scrapheap_picture(server, ghost_path, pixel_sha="sha_old")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # After the snapshot: the same on-disk file is re-used by a NEW active
    # picture stored under the plain RELATIVE path (imported/managed
    # convention), with different content.
    with open(abs_path, "wb") as fh:
        fh.write(b"NEW-LIVE-CONTENT-ADDED-AFTER-SNAPSHOT")
    _add_picture(server, filename=rel)

    report = server.vault.restore_service.restore_full(cp.id)
    assert not report.errors, f"Restore errors: {report.errors}"
    assert os.path.isfile(abs_path), "restore itself must never delete image files"

    scrapheap_ids = server.vault.db.run_immediate_read_task(
        lambda s: [
            p.id for p in s.exec(select(Picture).where(Picture.deleted.is_(True))).all()
        ]
    )
    assert scrapheap_ids == [], (
        f"ghost with {ghost_path_kind} path drift was resurrected and shadows "
        "the live file; emptying the scrapheap would hard-delete it"
    )

    result = _empty_scrapheap_via_http(server)
    assert result["deleted_count"] == 0, result
    assert os.path.isfile(abs_path), (
        f"FILE LOSS via {ghost_path_kind} path drift: a file added after the "
        "snapshot was hard-deleted by the restore -> empty-scrapheap cycle"
    )
    assert _count_deleted_log(server, rel) == 0


# ---------------------------------------------------------------------------
# Restore must not arm the scrapheap retention auto-purge (v1.8.0)
# ---------------------------------------------------------------------------


def _set_deleted(server, pic_id, deleted_at):
    def _do(session):
        p = session.get(Picture, pic_id)
        p.deleted = True
        p.deleted_at = deleted_at
        session.add(p)
        session.commit()

    server.vault.db.run_task(_do)


def test_full_restore_rearms_the_scrapheap_retention_clock(server):
    """A restored scrapheap must get a FULL retention window, not the snapshot's.

    The snapshot DB carries each scrapheap row's ORIGINAL ``deleted_at``. For any
    snapshot older than the window that deadline is already expired, so the first
    sweep after the restore would permanently destroy the very scrapheap the user
    just restored - and write ``file_removed=True``, so a second restore could
    not bring it back. Same failure family as the logged restore data-loss
    incident.
    """
    from pixlstash.services import scrapheap_service

    _create_file(server, "scrapheaped.jpg")
    pic = _add_picture(server, filename="scrapheaped.jpg")
    ancient = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400)
    _set_deleted(server, pic.id, ancient)

    cp = server.vault.snapshot_service.create_snapshot("MANUAL")
    report = server.vault.restore_service.restore_full(cp.id)
    assert not report.errors, f"Restore errors: {report.errors}"

    restored = _get_picture(server, pic.id)
    assert restored is not None, "the scrapheap row must survive the restore"
    assert restored.deleted is True, "restore must not silently un-delete it"
    stamped = restored.deleted_at
    assert stamped is not None
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    assert stamped > datetime.now(timezone.utc) - timedelta(minutes=5), (
        f"deleted_at must be re-stamped to the restore time, got {stamped}"
    )

    # The concrete consequence: it is no longer due for auto-purge.
    assert (
        scrapheap_service.find_due_retention_picture_ids(
            server.vault, datetime.now(timezone.utc), 30, None, 100
        )
        == []
    ), "a just-restored scrapheap picture must not be immediately purgeable"


def test_full_restore_leaves_live_pictures_deleted_at_alone(server):
    """The other direction: re-arming must not touch non-deleted rows."""
    _create_file(server, "alive.jpg")
    pic = _add_picture(server, filename="alive.jpg")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    report = server.vault.restore_service.restore_full(cp.id)
    assert not report.errors, f"Restore errors: {report.errors}"

    restored = _get_picture(server, pic.id)
    assert restored.deleted is False
    assert restored.deleted_at is None, (
        "A live picture must never be given a scrapheap deadline by a restore"
    )


def test_resource_restore_rearms_the_scrapheap_retention_clock(server):
    """The per-resource upsert path merges snapshot rows verbatim, so it needs
    the same re-stamp as the full restore."""
    from pixlstash.services import scrapheap_service

    _create_file(server, "res_scrapheaped.jpg")
    pic = _add_picture(server, filename="res_scrapheaped.jpg")
    ancient = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400)
    _set_deleted(server, pic.id, ancient)

    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Move the clock forward live, then restore the resource from the snapshot.
    def _bump(session):
        p = session.get(Picture, pic.id)
        p.description = "mutated"
        session.commit()

    server.vault.db.run_task(_bump)

    report = server.vault.restore_service.restore_resource(cp.id, "picture", pic.id)
    assert not report.errors, f"Restore errors: {report.errors}"

    restored = _get_picture(server, pic.id)
    assert restored.deleted is True
    stamped = restored.deleted_at
    assert stamped is not None
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    assert stamped > datetime.now(timezone.utc) - timedelta(minutes=5), (
        f"deleted_at must be re-stamped by the resource restore, got {stamped}"
    )
    assert (
        scrapheap_service.find_due_retention_picture_ids(
            server.vault, datetime.now(timezone.utc), 30, None, 100
        )
        == []
    )


def test_restore_can_resurrect_a_picture_whose_file_removal_failed(server):
    """F5 - a ledger corrected to file_removed=False must NOT block restore.

    ``file_removed=True`` is written before the file is touched, so a failed
    ``os.remove`` would otherwise leave the ledger permanently asserting a
    deletion that never happened - and restore, which trusts the ledger, would
    drop the picture forever even though its file is sitting right there.
    """
    _create_file(server, "kept_by_accident.jpg")
    pic = _add_picture(server, filename="kept_by_accident.jpg")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # The purge ran, the row went away, but the file removal failed - so the
    # ledger was corrected to "removed from library, file kept".
    def _simulate_failed_purge(session):
        session.add(
            DeletedFileLog(
                path_sha=DeletedFileLog.hash_path("kept_by_accident.jpg"),
                pixel_sha=None,
                deleted_at=datetime.now(timezone.utc),
                file_removed=False,
            )
        )
        session.delete(session.get(Picture, pic.id))
        session.commit()

    server.vault.db.run_task(_simulate_failed_purge)
    assert _get_picture(server, pic.id) is None

    report = server.vault.restore_service.restore_full(cp.id)
    assert not report.errors, f"Restore errors: {report.errors}"
    assert _get_picture(server, pic.id) is not None, (
        "a file_removed=False ledger row means the file was KEPT, so restore "
        "must be able to bring the picture back"
    )


def test_restore_still_refuses_a_genuinely_purged_picture(server):
    """The other direction: the F5 correction must not weaken the real guard."""
    _create_file(server, "genuinely_gone.jpg")
    pic = _add_picture(server, filename="genuinely_gone.jpg")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _simulate_real_purge(session):
        session.add(
            DeletedFileLog(
                path_sha=DeletedFileLog.hash_path("genuinely_gone.jpg"),
                pixel_sha=None,
                deleted_at=datetime.now(timezone.utc),
                file_removed=True,
            )
        )
        session.delete(session.get(Picture, pic.id))
        session.commit()

    server.vault.db.run_task(_simulate_real_purge)
    os.remove(os.path.join(server.vault.image_root, "genuinely_gone.jpg"))

    report = server.vault.restore_service.restore_full(cp.id)
    assert not report.errors, f"Restore errors: {report.errors}"
    assert _get_picture(server, pic.id) is None, (
        "a genuine permanent deletion must never be resurrected"
    )


def test_restore_does_not_resurrect_a_picture_whose_ledger_row_survived_a_collision(
    server,
):
    """The consequence the write-ownership bound protects.

    Reviewer's variant 2: picture A's content was genuinely destroyed at path P
    and logged file_removed=True. Different content is later written at P and
    purged with a failing os.remove. If that second purge were allowed to
    downgrade A's row to False, restore would resurrect A - bound to the WRONG
    file. The purge now only corrects rows it wrote, so A's row stays True and
    restore keeps refusing.
    """
    _create_file(server, "collision.jpg")
    pic_a = _add_picture(server, filename="collision.jpg", pixel_sha="sha-C1")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    # Purge A: content C1 genuinely destroyed at this path.
    def _purge_a(session):
        session.add(
            DeletedFileLog(
                path_sha=DeletedFileLog.hash_path("collision.jpg"),
                pixel_sha="sha-C1",
                deleted_at=datetime.now(timezone.utc),
                file_removed=True,
            )
        )
        session.delete(session.get(Picture, pic_a.id))
        session.commit()

    server.vault.db.run_task(_purge_a)
    os.remove(os.path.join(server.vault.image_root, "collision.jpg"))

    # Different content C2 now occupies the same path (the file exists again).
    _create_file(server, "collision.jpg")

    # A second purge at that path fails its removal. With the ownership bound it
    # does NOT own A's row, so the row stays True.
    unconfirmed = [DeletedFileLog.hash_path("collision.jpg")]
    corrected = server.vault.db.run_task(
        scrapheap_service.mark_files_kept_in_session, unconfirmed, set()
    )
    assert corrected == 0, "a row this purge did not write must not be corrected"

    report = server.vault.restore_service.restore_full(cp.id)
    assert not report.errors, f"Restore errors: {report.errors}"
    assert _get_picture(server, pic_a.id) is None, (
        "picture A's content was genuinely destroyed; restore must never "
        "resurrect it and bind it to whatever now occupies that path"
    )


def test_restore_resource_rebuilds_entity_project_membership(server):
    """A restored parent character comes back with its project membership rows,
    not just its scalar ``project_id`` (issue #125).

    The join table is the read model: a character restored with the FK alone
    would be silently invisible to ``GET /characters?project_id=…`` and refused
    by a project-scoped share token, which is the same "restored but unusable"
    failure class as the 2026-07-22 snapshot-restore incident.
    """
    from pixlstash.db_models import CharacterProjectMember, Project
    from pixlstash.services.project_membership_service import set_character_projects

    _create_file(server, "membership_restore.jpg")
    pic = _add_picture(server, filename="membership_restore.jpg")

    def _setup_snapshot_state(session):
        p1 = Project(name="restore-proj-1")
        p2 = Project(name="restore-proj-2")
        session.add(p1)
        session.add(p2)
        session.flush()
        c = Character(name="multi-project-char")
        session.add(c)
        session.flush()
        set_character_projects(session, c, [p1.id, p2.id])
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
        return c.id, sorted([p1.id, p2.id])

    char_id, project_ids = server.vault.db.run_task(_setup_snapshot_state)
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    server.vault.db.run_task(delete_characters, [char_id])

    report = server.vault.restore_service.restore_resource(
        cp.id,
        "picture",
        pic.id,
        confirm_restore_dependencies=True,
    )
    assert report.upserted_count > 0

    restored = server.vault.db.run_immediate_read_task(
        lambda s: (
            s.get(Character, char_id),
            sorted(
                int(r)
                for r in s.exec(
                    select(CharacterProjectMember.project_id).where(
                        CharacterProjectMember.character_id == char_id
                    )
                ).all()
            ),
        )
    )
    char_after, membership_after = restored
    assert char_after is not None
    assert membership_after == project_ids, (
        "restored character must regain BOTH project memberships; got "
        f"{membership_after}"
    )
    assert char_after.project_id == project_ids[0], (
        "the legacy primary-project FK must survive the restore too"
    )


# ---------------------------------------------------------------------------
# A restore always leaves the vault with no API tokens
# ---------------------------------------------------------------------------

# The revision immediately before the one that clears API tokens, used to build
# a snapshot that looks like it came from an earlier release. The restore path
# clears tokens on its own regardless of the stamp; these tests pin that it
# holds for a snapshot on either side of that revision.
_REVISION_BEFORE_TOKEN_RESET = "0085_recompute_smart_score_restored_builtin_anchors"


def _rewrite_snapshot(server, cp, mutate) -> None:
    """Materialize a snapshot, apply *mutate* to it, and compress it back.

    Snapshots are stored as zstd archives, so producing one that looks like it
    came from an earlier release means decompressing, editing, and recompressing
    over the original path.
    """
    abs_path = os.path.join(server.vault.image_root, cp.relative_path)
    tmp_dir = tempfile.mkdtemp(prefix="pixlstash_test_snap_")
    tmp_sqlite = os.path.join(tmp_dir, "snap.sqlite")
    try:
        materialize_snapshot(abs_path, tmp_sqlite)
        with closing(sqlite3.connect(tmp_sqlite)) as conn:
            mutate(conn)
            conn.commit()
        compress_snapshot(tmp_sqlite, abs_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _snapshot_token_count(server, cp) -> int:
    """Return how many usertoken rows the snapshot archive holds."""
    abs_path = os.path.join(server.vault.image_root, cp.relative_path)
    tmp_dir = tempfile.mkdtemp(prefix="pixlstash_test_snap_")
    tmp_sqlite = os.path.join(tmp_dir, "snap.sqlite")
    try:
        materialize_snapshot(abs_path, tmp_sqlite)
        with closing(sqlite3.connect(tmp_sqlite)) as conn:
            return conn.execute("SELECT COUNT(*) FROM usertoken").fetchone()[0]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _live_token_count(server) -> int:
    return server.vault.db.run_immediate_read_task(
        lambda s: len(s.exec(select(UserToken)).all())
    )


def _add_tokens_stamped_before_the_reset(conn) -> None:
    """Put token rows in a snapshot and stamp it at the earlier revision."""
    conn.execute("DELETE FROM usertoken")
    # The owner row the server creates on start-up is already in the snapshot;
    # hang the tokens off it rather than building one with every NOT NULL
    # settings column this table carries.
    user_row = conn.execute("SELECT id FROM user ORDER BY id LIMIT 1").fetchone()
    if user_row is None:
        # Identity no longer lives in vault snapshots. Foreign keys are off on
        # this raw snapshot-editing connection, so an orphan id is enough to
        # model the portable legacy credential rows restore must erase.
        user_id = 1
    else:
        user_id = user_row[0]
    for token_id, scope in ((1, "ALL"), (2, "READ")):
        conn.execute(
            "INSERT INTO usertoken "
            "(id, user_id, token_hash, token_prefix, scope, created_at, "
            " include_attachments, watermark) "
            "VALUES (?, ?, ?, ?, ?, '2026-01-01 00:00:00', 0, 1)",
            (token_id, user_id, f"hash-{token_id}", f"prefix{token_id}", scope),
        )
    conn.execute("DELETE FROM alembic_version")
    conn.execute(
        "INSERT INTO alembic_version (version_num) VALUES (?)",
        (_REVISION_BEFORE_TOKEN_RESET,),
    )


def _add_guest_rows(conn) -> None:
    """Attach a guest session and a guest score to the snapshot's tokens.

    Both tables reference a token by id, so they have to go with the tokens
    rather than survive them.
    """
    picture_id = conn.execute("SELECT id FROM picture ORDER BY id LIMIT 1").fetchone()[
        0
    ]
    conn.execute(
        "INSERT INTO guest_session "
        "(session_id, token_public_id, created_at, last_active_at, cookie_token) "
        "VALUES ('sess-1', 'public-2', '2026-01-01 00:00:00', "
        "'2026-01-01 00:00:00', NULL)"
    )
    conn.execute(
        "INSERT INTO guest_score "
        "(session_id, token_public_id, picture_id, score, scored_at) "
        "VALUES ('sess-1', 'public-2', ?, 4, '2026-01-01 00:00:00')",
        (picture_id,),
    )


def _live_guest_row_counts(server) -> tuple[int, int]:
    """Return ``(guest_session_count, guest_score_count)`` in the live DB."""

    def _count(session):
        sessions = session.execute(text("SELECT COUNT(*) FROM guest_session")).scalar()
        scores = session.execute(text("SELECT COUNT(*) FROM guest_score")).scalar()
        return sessions, scores

    return server.vault.db.run_immediate_read_task(_count)


def _stamp_snapshot_at_head(conn) -> None:
    """Stamp the snapshot at the current head so no migration runs on restore.

    The head is read from the migration graph rather than written as a literal.
    A hardcoded identifier would make the test assert against one particular
    migration chain, which is the assumption the restore path itself refuses to
    make.
    """
    from pixlstash.services.restore.schema_upgrade import _alembic_head_revisions

    (head,) = sorted(_alembic_head_revisions())
    conn.execute("DELETE FROM alembic_version")
    conn.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (head,))


def test_restoring_an_older_snapshot_leaves_no_api_tokens(server):
    """A snapshot from before the token reset restores with its tokens cleared.

    Tokens issued under the earlier rules cannot come back through a restore.
    """
    _create_file(server, "token_snapshot.jpg")
    pic = _add_picture(server, filename="token_snapshot.jpg", description="before")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    _rewrite_snapshot(server, cp, _add_tokens_stamped_before_the_reset)
    # The snapshot really does carry tokens, so a clean result after the
    # restore cannot be an artefact of there being nothing to carry.
    assert _snapshot_token_count(server, cp) == 2

    report = server.vault.restore_service.restore_full(cp.id)
    assert not report.errors, f"Restore errors: {report.errors}"

    assert _live_token_count(server) == 0, (
        "tokens from a snapshot predating the reset must not come back"
    )
    # The rest of the restore still works.
    assert _get_picture(server, pic.id) is not None


def test_restoring_a_current_snapshot_also_leaves_no_api_tokens(server):
    """A snapshot stamped at head restores with its tokens cleared too.

    The clearing is a property of the restore path, not a side effect of a
    schema upgrade, so it does not depend on where the snapshot's revision sits
    relative to the migration that clears tokens. This snapshot is stamped at
    head, so no migration runs during the restore and the restore path is the
    only thing that can clear the rows.
    """
    _create_file(server, "token_snapshot_current.jpg")
    pic = _add_picture(server, filename="token_snapshot_current.jpg")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _add_tokens_at_head(conn):
        _add_tokens_stamped_before_the_reset(conn)
        _stamp_snapshot_at_head(conn)

    _rewrite_snapshot(server, cp, _add_tokens_at_head)
    assert _snapshot_token_count(server, cp) == 2

    report = server.vault.restore_service.restore_full(cp.id)
    assert not report.errors, f"Restore errors: {report.errors}"

    assert _live_token_count(server) == 0, (
        "a restore always leaves the vault with no API tokens, including for a "
        "snapshot taken by the current release"
    )
    # Over-blocking is its own regression: the picture data still restores.
    assert _get_picture(server, pic.id) is not None


def test_restoring_a_snapshot_clears_guest_sessions_and_scores(server):
    """Guest sessions and scores go with the tokens they reference.

    Both tables key on a token id, and SQLite reuses the lowest free integer
    primary key, so a row left behind would come to describe whichever token is
    created next.
    """
    _create_file(server, "token_snapshot_guests.jpg")
    pic = _add_picture(server, filename="token_snapshot_guests.jpg", description="keep")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    def _add_tokens_and_guests(conn):
        _add_tokens_stamped_before_the_reset(conn)
        _add_guest_rows(conn)
        _stamp_snapshot_at_head(conn)

    _rewrite_snapshot(server, cp, _add_tokens_and_guests)
    assert _snapshot_token_count(server, cp) == 2

    report = server.vault.restore_service.restore_full(cp.id)
    assert not report.errors, f"Restore errors: {report.errors}"

    assert _live_token_count(server) == 0
    assert _live_guest_row_counts(server) == (0, 0), (
        "guest sessions and scores must not outlive the tokens they reference"
    )
    # The restore is otherwise unharmed.
    restored = _get_picture(server, pic.id)
    assert restored is not None
    assert restored.description == "keep"


def test_full_restore_closes_auth_before_swap_and_across_queue_gap(server, monkeypatch):
    """Old cookies/tokens receive 503 between the DB swap and token deletion."""
    from fastapi.testclient import TestClient

    owner = TestClient(server.api, raise_server_exceptions=True)
    login = owner.post(
        "/api/v1/login",
        json={"username": "owner", "password": "example-owner-password"},
    )
    assert login.status_code == 200, login.text
    token_response = owner.post(
        "/api/v1/users/me/token",
        json={"description": "pre-restore", "scope": "READ"},
    )
    assert token_response.status_code == 200, token_response.text
    token = token_response.json()["token"]

    _create_file(server, "restore-auth-gate.jpg")
    _add_picture(server, filename="restore-auth-gate.jpg")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    service = server.vault.restore_service
    original_swap = service._swap_database
    # The hub owns the token rows now, so the vault-side task that runs in the
    # queue gap is the guest-state clear. It is the same position in the restore
    # sequence the token clear used to occupy.
    original_clear = service._clear_guest_state
    entered_queue_gap = threading.Event()
    release_token_clear = threading.Event()

    def _assert_gate_then_swap(live_db_path, new_db_path):
        assert server.auth.is_auth_closed_for_restore()
        return original_swap(live_db_path, new_db_path)

    def _blocked_token_clear(session):
        entered_queue_gap.set()
        assert release_token_clear.wait(120), "test did not release token clear"
        return original_clear(session)

    monkeypatch.setattr(service, "_swap_database", _assert_gate_then_swap)
    monkeypatch.setattr(service, "_clear_guest_state", _blocked_token_clear)

    outcome = {}

    def _restore():
        try:
            outcome["report"] = service.restore_full(cp.id)
        except BaseException as exc:  # surface worker-thread assertion failures
            outcome["error"] = exc

    thread = threading.Thread(target=_restore, daemon=True)
    thread.start()
    try:
        # Inside the try, so a restore that never arrives is still released and
        # joined: left blocked, it holds the scratch snapshot.sqlite open and
        # the next test's snapshots rmtree fails on Windows. The wait is about
        # whether the gap is reached at all - materialising and
        # alembic-upgrading the snapshot comes first, and a Windows runner
        # takes several times the ~2.7s that costs on Linux.
        assert entered_queue_gap.wait(120), (
            "restore never reached token-clear queue gap"
        )
        assert server.auth.is_auth_closed_for_restore()

        class _PreRestoreWebSocket:
            cookies = {"session_id": owner.cookies.get("session_id")}
            headers = {"authorization": f"Bearer {token}"}
            query_params = {}

        assert server.auth.authenticate_websocket(_PreRestoreWebSocket()) is None
        cookie_response = owner.get("/api/v1/session/context")
        token_response = TestClient(server.api).get(
            "/api/v1/session/context",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert cookie_response.status_code == 503, cookie_response.text
        assert token_response.status_code == 503, token_response.text
        assert cookie_response.headers["Retry-After"] == "5"
    finally:
        release_token_clear.set()
        thread.join(timeout=20)

    assert not thread.is_alive(), "restore thread did not finish"
    assert "error" not in outcome, outcome.get("error")
    assert not outcome["report"].errors
    assert not server.auth.is_auth_closed_for_restore()
    assert owner.get("/api/v1/session/context").status_code == 401
    assert (
        TestClient(server.api)
        .get(
            "/api/v1/session/context",
            headers={"Authorization": f"Bearer {token}"},
        )
        .status_code
        == 401
    )


def test_share_link_is_unavailable_while_restore_admission_is_closed(server):
    """The embedded share credential uses the same atomic restore gate."""
    from fastapi.testclient import TestClient

    owner = TestClient(server.api, raise_server_exceptions=True)
    login = owner.post(
        "/api/v1/login",
        json={"username": "owner", "password": "example-owner-password"},
    )
    assert login.status_code == 200, login.text
    _pic, created = _create_picture_share(owner, server, "share-gate.jpg")
    share_path = f"/share/{created['token']}.jpg"
    share_url = f"http://testserver{share_path}"
    anonymous = TestClient(server.api)
    assert anonymous.get(share_url).status_code == 200

    server.auth.close_auth_for_restore()
    try:
        response = anonymous.get(share_url)
        assert response.status_code == 503, response.text
        assert response.headers["Retry-After"] == "5"
    finally:
        server.auth.reopen_auth_after_restore()

    assert anonymous.get(share_url).status_code == 200
    deleted = owner.delete(f"/api/v1/users/me/token/{created['token_id']}")
    assert deleted.status_code == 200, deleted.text


def test_full_restore_drains_an_admitted_share_before_swap(server, monkeypatch):
    """A share token/resource lookup admitted before closure finishes first."""
    from fastapi import Request
    from fastapi.responses import Response
    from fastapi.testclient import TestClient

    from pixlstash.services import share_service

    owner = TestClient(server.api, raise_server_exceptions=True)
    login = owner.post(
        "/api/v1/login",
        json={"username": "owner", "password": "example-owner-password"},
    )
    assert login.status_code == 200, login.text
    _pic, created = _create_picture_share(owner, server, "share-drain.jpg")
    share_path = f"/share/{created['token']}.jpg"
    share_url = f"http://testserver{share_path}"
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    admitted = threading.Event()
    release_share = threading.Event()
    lookup_completed = threading.Event()
    close_started = threading.Event()
    swap_started = threading.Event()
    original_close = server.auth.close_auth_for_restore
    original_swap = server.vault.restore_service._swap_database

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": share_path,
            "raw_path": share_path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )

    async def _serve_admitted_share(_request):
        admitted.set()
        released = await asyncio.get_running_loop().run_in_executor(
            None, release_share.wait, 10
        )
        assert released, "test did not release admitted share request"
        matched = share_service.validate_picture_share_token(
            server.auth, created["token"]
        )
        assert matched is not None
        assert share_service.get_shared_picture(server.vault, matched.resource_id)
        lookup_completed.set()
        return Response(status_code=200)

    def _close_and_signal(restore_request_lease=None):
        close_started.set()
        return original_close(restore_request_lease)

    def _swap_and_signal(live_db_path, new_db_path):
        assert lookup_completed.is_set(), "share lookup crossed the database cutover"
        swap_started.set()
        return original_swap(live_db_path, new_db_path)

    monkeypatch.setattr(server.auth, "close_auth_for_restore", _close_and_signal)
    monkeypatch.setattr(
        server.vault.restore_service, "_swap_database", _swap_and_signal
    )

    share_outcome = {}
    restore_outcome = {}

    def _share_request():
        try:
            share_outcome["response"] = asyncio.run(
                server.auth.auth_middleware(
                    request,
                    _serve_admitted_share,
                    allow_origins=[],
                    allow_origin_regex=None,
                )
            )
        except BaseException as exc:
            share_outcome["error"] = exc

    def _restore():
        try:
            restore_outcome["report"] = server.vault.restore_service.restore_full(cp.id)
        except BaseException as exc:
            restore_outcome["error"] = exc

    share_thread = threading.Thread(target=_share_request, daemon=True)
    restore_thread = threading.Thread(target=_restore, daemon=True)
    share_thread.start()
    if not admitted.wait(10):
        raise AssertionError(
            "share request never acquired its admission lease: "
            f"{share_outcome.get('error')!r}"
        )
    restore_thread.start()
    assert close_started.wait(20), "restore never began the admission drain"
    assert server.auth.is_auth_closed_for_restore()
    assert not swap_started.wait(0.2), "database swapped before share request drained"

    release_share.set()
    share_thread.join(timeout=10)
    restore_thread.join(timeout=30)
    assert not share_thread.is_alive(), "share request did not finish"
    assert not restore_thread.is_alive(), "restore did not finish after share drain"
    assert "error" not in share_outcome, share_outcome.get("error")
    assert share_outcome["response"].status_code == 200
    assert lookup_completed.is_set()
    assert "error" not in restore_outcome, restore_outcome.get("error")
    assert not restore_outcome["report"].errors
    assert swap_started.is_set()

    # Restore deletes every token, including one present in the restored
    # snapshot, so the old share credential cannot resolve a post-swap ID.
    assert _live_token_count(server) == 0
    assert not server.auth._token_cache
    assert TestClient(server.api).get(share_url).status_code == 404


def test_full_restore_drains_an_admitted_http_request_before_swap(server, monkeypatch):
    """The restore request excludes itself but waits for older API traffic."""
    from fastapi.testclient import TestClient

    restore_client = TestClient(server.api, raise_server_exceptions=True)
    blocked_client = TestClient(server.api, raise_server_exceptions=True)
    for client in (restore_client, blocked_client):
        login = client.post(
            "/api/v1/login",
            json={"username": "owner", "password": "example-owner-password"},
        )
        assert login.status_code == 200, login.text

    token_response = restore_client.post(
        "/api/v1/users/me/token",
        json={"description": "drain-test", "scope": "READ"},
    )
    assert token_response.status_code == 200, token_response.text
    old_token = token_response.json()["token"]

    _create_file(server, "restore-admission-drain.jpg")
    _add_picture(server, filename="restore-admission-drain.jpg")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    admitted = threading.Event()
    release_request = threading.Event()
    close_started = threading.Event()
    swap_started = threading.Event()
    original_admitted = server.auth._auth_middleware_admitted
    original_close = server.auth.close_auth_for_restore
    original_swap = server.vault.restore_service._swap_database

    async def _pause_after_admission(
        request, call_next, allow_origins, allow_origin_regex
    ):
        if request.headers.get("x-pause-after-admission") == "yes":
            admitted.set()
            released = await asyncio.get_running_loop().run_in_executor(
                None, release_request.wait, 10
            )
            assert released, "test did not release admitted request"
        return await original_admitted(
            request, call_next, allow_origins, allow_origin_regex
        )

    def _close_and_signal(restore_request_lease=None):
        close_started.set()
        return original_close(restore_request_lease)

    def _swap_and_signal(live_db_path, new_db_path):
        swap_started.set()
        return original_swap(live_db_path, new_db_path)

    monkeypatch.setattr(
        server.auth, "_auth_middleware_admitted", _pause_after_admission
    )
    monkeypatch.setattr(server.auth, "close_auth_for_restore", _close_and_signal)
    monkeypatch.setattr(
        server.vault.restore_service, "_swap_database", _swap_and_signal
    )

    blocked_outcome = {}
    restore_outcome = {}

    def _blocked_request():
        blocked_outcome["response"] = blocked_client.get(
            "/api/v1/session/context",
            headers={"X-Pause-After-Admission": "yes"},
        )

    def _restore_request():
        restore_outcome["response"] = restore_client.post(
            f"/api/v1/snapshots/{cp.id}/restore",
            json={"dry_run": False, "allow_without_safety": False},
        )

    blocked_thread = threading.Thread(target=_blocked_request, daemon=True)
    restore_thread = threading.Thread(target=_restore_request, daemon=True)
    blocked_thread.start()
    assert admitted.wait(10), "request never acquired its admission lease"
    restore_thread.start()
    assert close_started.wait(20), "restore never began the admission drain"
    assert server.auth.is_auth_closed_for_restore()
    assert not swap_started.wait(0.2), "database swapped before prior request drained"
    unavailable = TestClient(server.api).get("/api/v1/session/context")
    assert unavailable.status_code == 503, unavailable.text

    release_request.set()
    blocked_thread.join(timeout=10)
    restore_thread.join(timeout=30)
    assert not blocked_thread.is_alive(), "admitted request did not finish"
    assert not restore_thread.is_alive(), "restore did not finish after drain"
    assert blocked_outcome["response"].status_code == 200
    assert restore_outcome["response"].status_code == 200, restore_outcome[
        "response"
    ].text
    assert swap_started.is_set()

    # Both pre-restore credential forms require fresh authentication after the
    # swap; neither can observe the restored database under its old identity.
    assert restore_client.get("/api/v1/session/context").status_code == 401
    assert (
        TestClient(server.api)
        .get(
            "/api/v1/session/context",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        .status_code
        == 401
    )


def test_restore_admission_drain_timeout_fails_closed(server, monkeypatch):
    """An undrainable old request aborts without reopening authentication."""
    lease = server.auth._acquire_restore_http_lease()
    assert lease is not None
    monkeypatch.setattr(server.auth, "_RESTORE_DRAIN_TIMEOUT_SECONDS", 0.01)
    try:
        with pytest.raises(RuntimeError, match="Timed out draining"):
            server.auth.close_auth_for_restore()
        assert server.auth.is_auth_closed_for_restore()
    finally:
        server.auth._release_restore_http_lease(lease)
        server.auth.reopen_auth_after_restore()


def test_full_restore_reset_failure_is_not_success_and_keeps_auth_closed(
    server, monkeypatch
):
    """A failed in-memory reset propagates and never reopens authentication."""
    from fastapi.testclient import TestClient

    owner = TestClient(server.api, raise_server_exceptions=True)
    login = owner.post(
        "/api/v1/login",
        json={"username": "owner", "password": "example-owner-password"},
    )
    assert login.status_code == 200, login.text
    _create_file(server, "restore-auth-reset-failure.jpg")
    _add_picture(server, filename="restore-auth-reset-failure.jpg")
    cp = server.vault.snapshot_service.create_snapshot("MANUAL")

    original_reset = server.auth.reset_after_restore

    def _fail_reset():
        raise RuntimeError("deterministic auth reset failure")

    monkeypatch.setattr(server.auth, "reset_after_restore", _fail_reset)
    try:
        with pytest.raises(
            RuntimeError, match="authentication state could not be reset"
        ):
            server.vault.restore_service.restore_full(cp.id)
        assert server.auth.is_auth_closed_for_restore()
        response = owner.get("/api/v1/session/context")
        assert response.status_code == 503, response.text
    finally:
        # Restore the module-scoped server fixture to a safe, usable state for
        # any subsequently selected tests. Production recovery is a restart.
        monkeypatch.setattr(server.auth, "reset_after_restore", original_reset)
        original_reset()
        server.auth.reopen_auth_after_restore()
