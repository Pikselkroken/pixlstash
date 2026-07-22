"""Scrapheap auto-purge / retention tests (v1.8.0).

This is an AUTOMATIC file-destruction path, so the suite asserts both
directions everywhere: what MUST be destroyed once its window expires, and —
more importantly — everything that must NEVER be destroyed by a timer
(protected reference-folder originals, "Never", pictures inside their window,
pictures with no ``deleted_at``, and anything at all during a config save).

The background WorkPlanner is disabled in this module's server so the only
purge that ever runs is the one a test drives explicitly.
"""

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select
from sqlalchemy import text

from pixlstash.db_models import (
    DeletedFileLog,
    Picture,
    PictureSet,
    PictureSetMember,
    PictureStack,
    ReferenceFolder,
    User,
)
from pixlstash.server import Server
from pixlstash.services import scrapheap_service
from pixlstash.tasks.scrapheap_retention_purge_finder import (
    ScrapheapRetentionPurgeFinder,
)
from pixlstash.tasks.scrapheap_retention_purge_task import ScrapheapRetentionPurgeTask
from pixlstash.utils.image_processing.image_utils import ImageUtils

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MIGRATIONS_DIR = os.path.join(_PROJECT_ROOT, "pixlstash")

_RESET_TABLES = [
    DeletedFileLog,
    PictureSetMember,
    PictureSet,
    Picture,
    PictureStack,
    ReferenceFolder,
]


@pytest.fixture(scope="module")
def server():
    """Server with background workers OFF.

    The retention finder is registered on a live vault, so leaving the planner
    running would let it purge in the background mid-assertion. Every test here
    drives the finder itself.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with open(server_config_path, "w") as fh:
            json.dump(
                {
                    "host": "localhost",
                    "port": 9537,
                    "image_root": os.path.join(temp_dir, "images"),
                    "disable_background_workers": True,
                },
                fh,
            )
        with Server(server_config_path) as srv:
            yield srv


@pytest.fixture(autouse=True)
def reset_vault(server):
    """Wipe pictures/folders/ledger and reset retention config between tests."""

    def _wipe(session: Session):
        session.exec(text("PRAGMA foreign_keys = OFF"))
        for model in _RESET_TABLES:
            session.exec(delete(model))
        session.exec(delete(User))
        session.commit()
        session.exec(text("PRAGMA foreign_keys = ON"))

    server.vault.db.run_task(_wipe)
    image_root = server.vault.image_root
    db_basenames = {"vault.db", "vault.db-wal", "vault.db-shm", "vault.db-journal"}
    for entry in os.listdir(image_root):
        if entry in db_basenames:
            continue
        path = os.path.join(image_root, entry)
        if os.path.isfile(path):
            os.remove(path)
    server.auth.ensure_user()
    server._server_config.pop(scrapheap_service.RETENTION_DAYS_KEY, None)
    server._server_config.pop(scrapheap_service.RETENTION_REDUCED_AT_KEY, None)
    server.vault.set_scrapheap_retention(scrapheap_service.DEFAULT_RETENTION_DAYS, None)
    yield


def _client(server):
    client = TestClient(server.api)
    assert (
        client.post(
            "/login", json={"username": "testuser", "password": "testpassword"}
        ).status_code
        == 200
    )
    return client


def _make_reference_picture(server, folder_dir, file_name, *, allow_delete):
    """Create a reference folder + a real file + an indexed Picture row."""
    os.makedirs(folder_dir, exist_ok=True)
    abs_file_path = os.path.join(folder_dir, file_name)
    Image.new("RGB", (8, 8), color=(10, 20, 30)).save(abs_file_path, format="PNG")
    pixel_sha = ImageUtils.calculate_hash_from_file_path(abs_file_path)

    def _insert(session: Session):
        folder = ReferenceFolder(
            folder=folder_dir,
            label="refs",
            allow_delete_file=allow_delete,
            status="active",
        )
        session.add(folder)
        session.commit()
        session.refresh(folder)
        pic = Picture(
            file_path=abs_file_path,
            reference_folder_id=folder.id,
            pixel_sha=pixel_sha,
            format="PNG",
            width=8,
            height=8,
            original_file_name=file_name,
        )
        session.add(pic)
        session.commit()
        session.refresh(pic)
        return pic.id

    return server.vault.db.run_task(_insert), abs_file_path


def _set_deleted_at(server, picture_id, when):
    def _update(session: Session):
        pic = session.get(Picture, picture_id)
        pic.deleted_at = when
        session.add(pic)
        session.commit()

    server.vault.db.run_task(_update)


def _get_picture(server, picture_id):
    return server.vault.db.run_task(lambda s, i=picture_id: s.get(Picture, i))


def _ledger_flags_for(server, abs_file_path):
    path_sha = DeletedFileLog.hash_path(abs_file_path)

    def _fetch(session: Session):
        return [
            row.file_removed
            for row in session.exec(
                select(DeletedFileLog).where(DeletedFileLog.path_sha == path_sha)
            ).all()
        ]

    return server.vault.db.run_task(_fetch)


def _run_purge_sweep(server):
    """Run one retention finder cycle + its task, synchronously."""
    finder = ScrapheapRetentionPurgeFinder(vault=server.vault)
    task = finder.find_task()
    if task is None:
        return None
    return task.run()


# ── deleted_at stamping ───────────────────────────────────────────────────────


def test_soft_delete_stamps_deleted_at(server, tmp_path):
    """DELETE /pictures/{id} starts the retention clock."""
    client = _client(server)
    pic_id, _path = _make_reference_picture(
        server, str(tmp_path / "refs"), "a.png", allow_delete=True
    )
    assert _get_picture(server, pic_id).deleted_at is None

    before = datetime.now(timezone.utc).replace(tzinfo=None)
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    after = datetime.now(timezone.utc).replace(tzinfo=None)

    pic = _get_picture(server, pic_id)
    assert pic.deleted is True
    assert pic.deleted_at is not None, "Soft-delete must stamp deleted_at"
    stamped = pic.deleted_at.replace(tzinfo=None)
    assert before <= stamped <= after


def test_bulk_soft_delete_stamps_deleted_at(server, tmp_path):
    """The bulk soft-delete uses the same retention clock."""
    client = _client(server)
    ids = [
        _make_reference_picture(
            server, str(tmp_path / f"refs{i}"), f"b{i}.png", allow_delete=True
        )[0]
        for i in range(2)
    ]
    resp = client.request("DELETE", "/api/v1/pictures", json={"picture_ids": ids})
    assert resp.status_code == 200, resp.text
    for pic_id in ids:
        assert _get_picture(server, pic_id).deleted_at is not None


def test_redelete_does_not_extend_the_window(server, tmp_path):
    """Re-issuing DELETE on an already-scrapheaped picture must not restart the
    clock — otherwise a stray client call silently grants an extra window."""
    client = _client(server)
    pic_id, _path = _make_reference_picture(
        server, str(tmp_path / "refs"), "c.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    original = _get_picture(server, pic_id).deleted_at
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5)
    _set_deleted_at(server, pic_id, old)

    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    assert _get_picture(server, pic_id).deleted_at.replace(tzinfo=None) == old, (
        "A second DELETE on an already-deleted picture must not restamp deleted_at"
    )
    assert original is not None


def test_restore_clears_deleted_at(server, tmp_path):
    """Restoring out of the scrapheap clears the stamp; a later delete restamps."""
    client = _client(server)
    pic_id, _path = _make_reference_picture(
        server, str(tmp_path / "refs"), "d.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    resp = client.post("/pictures/scrapheap/restore", json={"picture_ids": [pic_id]})
    assert resp.status_code == 200, resp.text

    pic = _get_picture(server, pic_id)
    assert pic.deleted is False
    assert pic.deleted_at is None, "Restore must clear the retention stamp"

    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    assert _get_picture(server, pic_id).deleted_at is not None


# ── Migration backfill ────────────────────────────────────────────────────────


def _run_alembic(args, db_url):
    env = {**os.environ, "PIXLSTASH_DB_URL": db_url, "PYTHONPATH": _PROJECT_ROOT}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini"] + args,
        cwd=_MIGRATIONS_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


def test_migration_backfills_deleted_at_for_existing_scrapheap_rows():
    """0079 gives every pre-existing scrapheap row a FULL window from upgrade.

    Backfilling to the migration time (not to some unknown original deletion
    time) is what stops the first post-upgrade sweep from destroying items that
    have been sitting in a user's scrapheap for months.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_vault.db")
        db_url = f"sqlite:///{db_path}"

        up = _run_alembic(["upgrade", "head"], db_url)
        assert up.returncode == 0, f"{up.stdout}\n{up.stderr}"

        # Rewind to 0078: drop deleted_at so the DB looks like a real v1.7 install.
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            conn.execute("DROP INDEX IF EXISTS ix_picture_deleted_at")
            conn.execute("ALTER TABLE picture DROP COLUMN deleted_at")
            conn.execute(
                "UPDATE alembic_version SET version_num = "
                "'0078_add_reference_folder_pending_reimport'"
            )
            conn.execute(
                "INSERT INTO picture (id, file_path, original_file_name, deleted) "
                "VALUES (2001, 'a/old_deleted.jpg', 'old_deleted.jpg', 1), "
                "(2002, 'a/live.jpg', 'live.jpg', 0)"
            )
            conn.commit()
            cols = {r[1] for r in conn.execute("PRAGMA table_info(picture)").fetchall()}
            assert "deleted_at" not in cols

        before = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        up = _run_alembic(["upgrade", "head"], db_url)
        assert up.returncode == 0, f"{up.stdout}\n{up.stderr}"

        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(picture)").fetchall()}
            assert "deleted_at" in cols, "0079 must add picture.deleted_at"
            rows = dict(
                conn.execute(
                    "SELECT id, deleted_at FROM picture WHERE id IN (2001, 2002)"
                ).fetchall()
            )
            assert rows[2002] is None, (
                "A live (deleted=0) picture must not be given a scrapheap stamp"
            )
            assert rows[2001] is not None, (
                "An already-scrapheaped row must be backfilled to the migration time"
            )
            backfilled = datetime.fromisoformat(str(rows[2001]))
            assert backfilled >= before - timedelta(seconds=5), (
                "Backfill must be the MIGRATION time (a full fresh window), not an "
                f"older value: {backfilled} < {before}"
            )


# ── Retention maths (pure) ────────────────────────────────────────────────────


def test_reduction_grace_is_a_floor_not_a_per_picture_extension():
    """F1 — the grace must protect pictures of ANY age, not just the [30,31) band.

    Measuring the grace from each picture's own ``deleted_at`` only ever moved
    the deadline of a picture already within a day of expiry. A 400-day-old
    picture stayed instantly purgeable, so `Never -> 30` (or `120 -> 30`) would
    wipe a long-lived scrapheap on the very next 15-minute sweep — seconds after
    a dropdown that saves on change with no confirmation.
    """
    reduced_at = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    floor = reduced_at + timedelta(days=1)

    # 400 days old: the floor, not deleted_at + 30, decides. This is the case
    # the old per-picture grace got wrong.
    ancient = reduced_at - timedelta(days=400)
    assert (
        scrapheap_service.compute_purge_at(ancient, 30, reduced_at, is_protected=False)
        == floor
    )

    # 31 days old — also the floor (deleted_at + 30 already passed).
    old_ish = reduced_at - timedelta(days=31)
    assert (
        scrapheap_service.compute_purge_at(old_ish, 30, reduced_at, is_protected=False)
        == floor
    )

    # 10 days old: its own deadline is later than the floor, so it wins.
    young = reduced_at - timedelta(days=10)
    assert scrapheap_service.compute_purge_at(
        young, 30, reduced_at, is_protected=False
    ) == young + timedelta(days=30)

    # Post-reduction picture: the floor is inert, plain window applies.
    after = reduced_at + timedelta(hours=1)
    assert scrapheap_service.compute_purge_at(
        after, 30, reduced_at, is_protected=False
    ) == after + timedelta(days=30)


def test_no_picture_is_purgeable_within_the_grace_of_a_lowering():
    """The property stated as one invariant over a wide spread of ages."""
    reduced_at = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    floor = reduced_at + timedelta(days=1)
    for age_days in (0, 1, 29, 30, 31, 60, 121, 400, 5000):
        deleted_at = reduced_at - timedelta(days=age_days)
        for window in scrapheap_service.RETENTION_DAY_CHOICES:
            purge_at = scrapheap_service.compute_purge_at(
                deleted_at, window, reduced_at, is_protected=False
            )
            assert purge_at >= floor, (
                f"age={age_days}d window={window}d became purgeable at "
                f"{purge_at}, inside the grace floor {floor}"
            )


def test_no_grace_without_a_reduction():
    """A raise / first-set / never-changed window grants no grace."""
    deleted_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert scrapheap_service.compute_purge_at(
        deleted_at, 60, None, is_protected=False
    ) == deleted_at + timedelta(days=60)
    assert scrapheap_service.reduction_grace_floor(None) is None


def test_never_and_protected_have_no_deadline():
    deleted_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert (
        scrapheap_service.compute_purge_at(deleted_at, None, None, is_protected=False)
        is None
    )
    assert (
        scrapheap_service.compute_purge_at(deleted_at, 30, None, is_protected=True)
        is None
    )
    # No stamp -> no deadline (fail-closed).
    assert (
        scrapheap_service.compute_purge_at(None, 30, None, is_protected=False) is None
    )


def test_is_retention_reduction_matrix():
    reduction = scrapheap_service.is_retention_reduction
    assert reduction(60, 30) is True
    assert reduction(None, 120) is True, "Never -> a finite window is a reduction"
    assert reduction(30, 60) is False, "A raise is not a reduction"
    assert reduction(30, 30) is False, "A no-op save is not a reduction"
    assert reduction(30, None) is False, "Setting Never is not a reduction"
    # The default is also the shortest choice, so a first explicit set can never
    # be a reduction — that is the "untouched on first-set" rule.
    for choice in scrapheap_service.RETENTION_DAY_CHOICES:
        assert reduction(scrapheap_service.DEFAULT_RETENTION_DAYS, choice) is False, (
            f"first-set of {choice} must not count as a reduction"
        )


# ── The purge sweep ───────────────────────────────────────────────────────────


def test_purge_removes_expired_unprotected_and_skips_protected(server, tmp_path):
    """The timer destroys an expired UNPROTECTED picture and never a protected one."""
    client = _client(server)
    unprot_id, unprot_path = _make_reference_picture(
        server, str(tmp_path / "unprot"), "gone.png", allow_delete=True
    )
    prot_id, prot_path = _make_reference_picture(
        server, str(tmp_path / "prot"), "kept.png", allow_delete=False
    )
    for pid in (unprot_id, prot_id):
        assert client.delete(f"/pictures/{pid}").status_code == 200

    long_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400)
    _set_deleted_at(server, unprot_id, long_ago)
    _set_deleted_at(server, prot_id, long_ago)

    result = _run_purge_sweep(server)
    assert result == {"purged": 1, "skipped": 0, "retained": 0}, result

    # Unprotected: row gone, file destroyed, ledger says permanently removed.
    assert _get_picture(server, unprot_id) is None
    assert not os.path.isfile(unprot_path)
    assert _ledger_flags_for(server, unprot_path) == [True], (
        "An auto-purged picture must be logged file_removed=True so restore drops "
        "it rather than resurrecting it"
    )

    # Protected: completely untouched — this is the whole point of the policy.
    prot = _get_picture(server, prot_id)
    assert prot is not None and prot.deleted is True, (
        "A protected reference original must stay in the scrapheap forever"
    )
    assert os.path.isfile(prot_path), "The timer must never destroy a protected file"
    assert _ledger_flags_for(server, prot_path) == [], (
        "A skipped protected picture must write no permanent-deletion ledger row"
    )


def test_purge_task_refuses_a_protected_id_handed_to_it_directly(server, tmp_path):
    """Second layer: even if the finder mis-selects, the TASK must not destroy
    a protected original.

    The finder filters protected rows out of its candidate query, so the task's
    ``include_protected=False`` is otherwise untested — and a single flipped
    argument there would silently turn the timer into a destroyer of reference
    originals. This test drives the task with a protected id on purpose.
    """
    client = _client(server)
    prot_id, prot_path = _make_reference_picture(
        server, str(tmp_path / "prot"), "direct.png", allow_delete=False
    )
    assert client.delete(f"/pictures/{prot_id}").status_code == 200
    _set_deleted_at(
        server,
        prot_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )

    task = ScrapheapRetentionPurgeTask(server.vault, [prot_id])
    assert task.run() == {"purged": 0, "skipped": 1, "retained": 0}

    prot = _get_picture(server, prot_id)
    assert prot is not None and prot.deleted is True
    assert os.path.isfile(prot_path), (
        "The auto-purge task must never destroy a protected reference original, "
        "even when handed its id directly"
    )
    assert _ledger_flags_for(server, prot_path) == []


def test_finder_never_selects_a_protected_picture(server, tmp_path):
    """First layer: the candidate query itself excludes protected originals."""
    client = _client(server)
    prot_id, _ = _make_reference_picture(
        server, str(tmp_path / "prot"), "unselected.png", allow_delete=False
    )
    assert client.delete(f"/pictures/{prot_id}").status_code == 200
    _set_deleted_at(
        server,
        prot_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )

    due = scrapheap_service.find_due_retention_picture_ids(
        server.vault, datetime.now(timezone.utc), 30, None, 100
    )
    assert due == [], f"A protected original must never be a purge candidate: {due}"

    finder = ScrapheapRetentionPurgeFinder(vault=server.vault)
    assert finder.find_task() is None


def test_purge_keeps_pictures_inside_the_window(server, tmp_path):
    """Over-purging is its own regression: nothing inside its window is touched."""
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "fresh.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=29),
    )

    assert _run_purge_sweep(server) is None, "29 days < the 30-day window"
    assert _get_picture(server, pic_id) is not None
    assert os.path.isfile(path)


def test_purge_skips_rows_without_a_deleted_at_stamp(server, tmp_path):
    """Fail-closed: no timestamp means no deadline, so never destroy it."""
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "nostamp.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    _set_deleted_at(server, pic_id, None)

    assert _run_purge_sweep(server) is None
    assert _get_picture(server, pic_id) is not None
    assert os.path.isfile(path)


def test_never_disables_the_purge_entirely(server, tmp_path):
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "forever.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=9999),
    )
    server.vault.set_scrapheap_retention(None, None)

    assert _run_purge_sweep(server) is None, "Never must schedule no purge at all"
    assert _get_picture(server, pic_id) is not None
    assert os.path.isfile(path)


def test_a_fresh_reduction_spares_every_age_then_expires(server, tmp_path):
    """Both directions of the grace floor, driven through a real sweep.

    Directly after the lowering NOTHING is purged whatever its age; once the
    floor has passed, the same pictures are destroyed. The grace defers, it does
    not exempt.
    """
    client = _client(server)
    young_id, young_path = _make_reference_picture(
        server, str(tmp_path / "u1"), "young.png", allow_delete=True
    )
    old_id, old_path = _make_reference_picture(
        server, str(tmp_path / "u2"), "old.png", allow_delete=True
    )
    for pid in (young_id, old_id):
        assert client.delete(f"/pictures/{pid}").status_code == 200

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _set_deleted_at(server, young_id, now - timedelta(days=30, hours=12))
    _set_deleted_at(server, old_id, now - timedelta(days=400))
    # The reduction happened just now, so the floor is ~1 day out.
    server.vault.set_scrapheap_retention(30, datetime.now(timezone.utc))

    assert _run_purge_sweep(server) is None, (
        "Nothing may be purged inside the grace floor of a fresh lowering"
    )
    for pid, path in ((young_id, young_path), (old_id, old_path)):
        assert _get_picture(server, pid) is not None
        assert os.path.isfile(path)

    # Move the reduction into the past so the floor has elapsed; both are now due.
    server.vault.set_scrapheap_retention(
        30, datetime.now(timezone.utc) - timedelta(days=2)
    )
    result = _run_purge_sweep(server)
    assert result == {"purged": 2, "skipped": 0, "retained": 0}, result
    for pid, path in ((young_id, young_path), (old_id, old_path)):
        assert _get_picture(server, pid) is None
        assert not os.path.isfile(path)


def test_lowering_the_window_spares_an_ancient_scrapheap(server, tmp_path):
    """F1 end-to-end: `Never -> 30` must not wipe a long-lived scrapheap.

    The reproduction from the data-safety review: a 400-day-old picture under a
    fresh 30-day window used to be destroyed by the very next sweep.
    """
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "ancient.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )

    # "Never" -> 30 through the real endpoint, exactly as the dropdown does.
    assert (
        client.patch(
            "/server-config/scrapheap-retention",
            json={"scrapheap_retention_days": None},
        ).status_code
        == 200
    )
    assert (
        client.patch(
            "/server-config/scrapheap-retention",
            json={"scrapheap_retention_days": 30},
        ).status_code
        == 200
    )

    assert _run_purge_sweep(server) is None, (
        "A lowering must not make an ancient scrapheap purgeable on the next sweep"
    )
    assert _get_picture(server, pic_id) is not None
    assert os.path.isfile(path)

    # ... and it is genuinely only deferred, not exempted: once the grace floor
    # passes, the picture does become due.
    server.vault.set_scrapheap_retention(
        30, datetime.now(timezone.utc) - timedelta(days=2)
    )
    result = _run_purge_sweep(server)
    assert result == {"purged": 1, "skipped": 0, "retained": 0}, result
    assert _get_picture(server, pic_id) is None


def test_no_grace_for_pictures_deleted_after_the_reduction(server, tmp_path):
    """The grace is only for items that predate the reduction."""
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "after.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200

    now = datetime.now(timezone.utc)
    _set_deleted_at(
        server, pic_id, now.replace(tzinfo=None) - timedelta(days=30, hours=12)
    )
    # Reduction happened BEFORE this picture was deleted -> no grace, 30-day window.
    server.vault.set_scrapheap_retention(30, now - timedelta(days=90))

    result = _run_purge_sweep(server)
    assert result == {"purged": 1, "skipped": 0, "retained": 0}, result
    assert _get_picture(server, pic_id) is None
    assert not os.path.isfile(path)


# ── F3: locked-set members ────────────────────────────────────────────────────


def _lock_picture_in_set(server, picture_id):
    """Put ``picture_id`` in a locked PictureSet and return the set id."""

    def _create(session: Session):
        pset = PictureSet(name="frozen", locked=True)
        session.add(pset)
        session.commit()
        session.refresh(pset)
        session.add(PictureSetMember(set_id=pset.id, picture_id=picture_id))
        session.commit()
        return pset.id

    return server.vault.db.run_task(_create)


def test_soft_delete_of_a_locked_member_is_refused(server, tmp_path):
    """Baseline for F3: the interactive path already refuses with 423."""
    client = _client(server)
    pic_id, _ = _make_reference_picture(
        server, str(tmp_path / "refs"), "frozen.png", allow_delete=True
    )
    _lock_picture_in_set(server, pic_id)
    assert client.delete(f"/pictures/{pic_id}").status_code == 423


def test_auto_purge_never_destroys_a_locked_set_member(server, tmp_path):
    """F3 — a whole-set freeze must not be silently defeated by a timer.

    ``DELETE /pictures/{id}`` refuses a locked member with 423, so an unattended
    sweep 30 days later must not do what the user is forbidden to do by hand.
    """
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "locked.png", allow_delete=True
    )
    # Soft-delete FIRST, then lock: reaching the scrapheap is the precondition.
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    _lock_picture_in_set(server, pic_id)
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )

    # Layer 1: never a candidate.
    assert (
        scrapheap_service.find_due_retention_picture_ids(
            server.vault, datetime.now(timezone.utc), 30, None, 100
        )
        == []
    )
    assert _run_purge_sweep(server) is None

    # Layer 2: refused even when handed to the task directly.
    assert ScrapheapRetentionPurgeTask(server.vault, [pic_id]).run() == {
        "purged": 0,
        "skipped": 0,
        "retained": 1,
    }
    assert _get_picture(server, pic_id) is not None
    assert os.path.isfile(path)
    assert _ledger_flags_for(server, path) == []


def test_auto_purge_resumes_once_the_set_is_unlocked(server, tmp_path):
    """The other direction: locking defers, it does not exempt forever."""
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "unlockme.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    set_id = _lock_picture_in_set(server, pic_id)
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )
    assert _run_purge_sweep(server) is None

    def _unlock(session: Session):
        pset = session.get(PictureSet, set_id)
        pset.locked = False
        session.add(pset)
        session.commit()

    server.vault.db.run_task(_unlock)

    result = _run_purge_sweep(server)
    assert result == {"purged": 1, "skipped": 0, "retained": 0}, result
    assert _get_picture(server, pic_id) is None
    assert not os.path.isfile(path)


# ── F4: the second deadline guard ─────────────────────────────────────────────


def test_task_re_checks_the_deadline_and_refuses_an_in_window_picture(server, tmp_path):
    """F4 — a finder bug must not be able to destroy an in-window picture.

    The deadline used to be checked in exactly one place. Here the task is
    handed an id that is NOT due; the guard must retain it.
    """
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "inwindow.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
    )

    assert ScrapheapRetentionPurgeTask(server.vault, [pic_id]).run() == {
        "purged": 0,
        "skipped": 0,
        "retained": 1,
    }
    assert _get_picture(server, pic_id) is not None
    assert os.path.isfile(path)
    assert _ledger_flags_for(server, path) == []


def test_restore_then_redelete_between_planning_and_purge_is_safe(server, tmp_path):
    """F4 — the real TOCTOU: the task runs at LOW priority and can be queued.

    The finder selects an expired picture; before the task runs the user restores
    it and deletes it again, so its deadline is now 30 days out. Re-checking at
    purge time is what stops the stale verdict from destroying it.
    """
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "toctou.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )

    finder = ScrapheapRetentionPurgeFinder(vault=server.vault)
    task = finder.find_task()
    assert task is not None, "the picture must be due at planning time"

    # ... user restores and re-deletes before the queued task gets its turn.
    assert (
        client.post(
            "/pictures/scrapheap/restore", json={"picture_ids": [pic_id]}
        ).status_code
        == 200
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200

    assert task.run() == {"purged": 0, "skipped": 0, "retained": 1}
    assert _get_picture(server, pic_id) is not None, (
        "A picture re-deleted between planning and purge is inside a fresh "
        "window and must survive"
    )
    assert os.path.isfile(path)
    assert _ledger_flags_for(server, path) == []


def test_manual_delete_forever_is_not_subject_to_the_retention_guard(server, tmp_path):
    """Over-blocking is its own regression: a human's explicit confirmation must
    still purge immediately, with no timer standing in the way."""
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "manual.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    # Deleted seconds ago — nowhere near its deadline.

    resp = client.request(
        "DELETE", "/api/v1/pictures/scrapheap", json={"include_protected": False}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted_count"] == 1
    assert _get_picture(server, pic_id) is None
    assert not os.path.isfile(path)


# ── Config endpoint ───────────────────────────────────────────────────────────


def test_get_and_patch_retention_config(server):
    client = _client(server)
    resp = client.get("/server-config/scrapheap-retention")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scrapheap_retention_days"] == 30
    assert body["scrapheap_retention_reduced_at"] is None
    assert body["scrapheap_retention_choices"] == [30, 60, 90, 120]
    assert body["scrapheap_retention_grace_days"] == 1

    # Raise: no reduced_at stamp.
    resp = client.patch(
        "/server-config/scrapheap-retention", json={"scrapheap_retention_days": 90}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scrapheap_retention_days"] == 90
    assert resp.json()["scrapheap_retention_reduced_at"] is None
    assert server.vault.scrapheap_retention_days == 90
    assert server.vault.scrapheap_retention_reduced_at is None

    # Lower: reduced_at is stamped.
    resp = client.patch(
        "/server-config/scrapheap-retention", json={"scrapheap_retention_days": 30}
    )
    assert resp.status_code == 200, resp.text
    stamped = resp.json()["scrapheap_retention_reduced_at"]
    assert stamped is not None
    assert server.vault.scrapheap_retention_reduced_at is not None

    # Raise again: the existing stamp is left untouched, not cleared.
    resp = client.patch(
        "/server-config/scrapheap-retention", json={"scrapheap_retention_days": 120}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scrapheap_retention_reduced_at"] == stamped

    # Never.
    resp = client.patch(
        "/server-config/scrapheap-retention", json={"scrapheap_retention_days": None}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scrapheap_retention_days"] is None
    assert server.vault.scrapheap_retention_days is None


def test_patch_retention_rejects_an_unsupported_window(server):
    client = _client(server)
    resp = client.patch(
        "/server-config/scrapheap-retention", json={"scrapheap_retention_days": 7}
    )
    assert resp.status_code == 422, resp.text
    assert server.vault.scrapheap_retention_days == 30, "A rejected save must not apply"


def test_config_save_never_purges_synchronously(server, tmp_path):
    """Saving a (much shorter) window must not destroy anything in the request.

    This is the load-bearing safety property: destruction only ever happens on
    the scheduled sweep, so a mis-click is always recoverable until the timer
    (plus its grace day) actually elapses.
    """
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "survivor.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=9999),
    )

    client.patch(
        "/server-config/scrapheap-retention", json={"scrapheap_retention_days": 120}
    )
    resp = client.patch(
        "/server-config/scrapheap-retention", json={"scrapheap_retention_days": 30}
    )
    assert resp.status_code == 200, resp.text

    assert _get_picture(server, pic_id) is not None, (
        "A config save must never purge synchronously"
    )
    assert os.path.isfile(path), "A config save must never remove a file"
    assert _ledger_flags_for(server, path) == []


# ── Per-picture contract in the scrapheap listing ─────────────────────────────


def _scrapheap_listing(client):
    resp = client.get("/pictures", params={"only_deleted": "true"})
    assert resp.status_code == 200, resp.text
    return {row["id"]: row for row in resp.json()}


def test_listing_exposes_purge_at_and_auto_purge_exempt(server, tmp_path):
    client = _client(server)
    unprot_id, _ = _make_reference_picture(
        server, str(tmp_path / "unprot"), "managed.png", allow_delete=True
    )
    prot_id, _ = _make_reference_picture(
        server, str(tmp_path / "prot"), "reference.png", allow_delete=False
    )
    for pid in (unprot_id, prot_id):
        assert client.delete(f"/pictures/{pid}").status_code == 200
    deleted_at = datetime(2026, 7, 1, 12, 0)
    _set_deleted_at(server, unprot_id, deleted_at)
    _set_deleted_at(server, prot_id, deleted_at)

    rows = _scrapheap_listing(client)
    assert rows[unprot_id]["auto_purge_exempt"] is False
    assert rows[unprot_id]["auto_purge_exempt_reason"] is None
    assert (
        rows[unprot_id]["purge_at"]
        == (deleted_at.replace(tzinfo=timezone.utc) + timedelta(days=30)).isoformat()
    )

    assert rows[prot_id]["auto_purge_exempt"] is True, (
        "A protected reference original is exempt from any timer"
    )
    assert rows[prot_id]["auto_purge_exempt_reason"] == "protected"
    assert rows[prot_id]["purge_at"] is None, "An exempt picture shows no countdown"


def test_listing_purge_at_reflects_the_reduction_grace_floor(server, tmp_path):
    """The countdown the UI renders must equal what the sweep will actually do.

    The floor is the interesting case: an old picture's own deadline is long
    past, so `purge_at` must show the post-reduction floor rather than a date in
    the past (which the grid would render as "overdue" while the sweep in fact
    still spares it).
    """
    client = _client(server)
    old_id, _ = _make_reference_picture(
        server, str(tmp_path / "old"), "floored.png", allow_delete=True
    )
    young_id, _ = _make_reference_picture(
        server, str(tmp_path / "young"), "unfloored.png", allow_delete=True
    )
    for pid in (old_id, young_id):
        assert client.delete(f"/pictures/{pid}").status_code == 200

    reduced_at = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    old_deleted_at = datetime(2026, 1, 1, 12, 0)  # deadline long past
    young_deleted_at = datetime(2026, 7, 5, 12, 0)  # deadline still ahead
    _set_deleted_at(server, old_id, old_deleted_at)
    _set_deleted_at(server, young_id, young_deleted_at)
    server.vault.set_scrapheap_retention(30, reduced_at)

    rows = _scrapheap_listing(client)
    assert rows[old_id]["purge_at"] == (reduced_at + timedelta(days=1)).isoformat(), (
        "An old picture's deadline must be lifted to the post-reduction floor"
    )
    assert (
        rows[young_id]["purge_at"]
        == (
            young_deleted_at.replace(tzinfo=timezone.utc) + timedelta(days=30)
        ).isoformat()
    ), "A picture whose own deadline is later than the floor keeps its own"


def test_listing_purge_at_matches_what_the_sweep_does(server, tmp_path):
    """The UI countdown and the destroyer must never disagree."""
    client = _client(server)
    pic_id, _ = _make_reference_picture(
        server, str(tmp_path / "refs"), "agree.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )
    server.vault.set_scrapheap_retention(30, datetime.now(timezone.utc))

    purge_at = _scrapheap_listing(client)[pic_id]["purge_at"]
    assert datetime.fromisoformat(purge_at) > datetime.now(timezone.utc), (
        "The UI must show a FUTURE deadline while the grace floor holds"
    )
    assert _run_purge_sweep(server) is None, (
        "...and the sweep must agree by purging nothing"
    )


def test_listing_agrees_with_the_sweep_about_a_locked_picture(server, tmp_path):
    """N-1 — the listing must not advertise a deadline the sweep will not act on.

    A locked scrapheap picture past its deadline used to be served
    ``purge_at=<past>`` + ``auto_purge_exempt=False``, so the grid rendered a
    permanent, urgent "purges today" badge for a picture the sweep skips forever.
    """
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "lockedrow.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    set_id = _lock_picture_in_set(server, pic_id)
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )

    row = _scrapheap_listing(client)[pic_id]
    assert row["auto_purge_exempt"] is True
    assert row["auto_purge_exempt_reason"] == "locked"
    assert row["purge_at"] is None, (
        "A locked picture must show no countdown — the sweep will never take it"
    )
    # ...and the sweep agrees.
    assert _run_purge_sweep(server) is None
    assert _get_picture(server, pic_id) is not None

    # Other direction: unlocking restores a real deadline AND real destruction.
    def _unlock(session: Session):
        pset = session.get(PictureSet, set_id)
        pset.locked = False
        session.add(pset)
        session.commit()

    server.vault.db.run_task(_unlock)

    row = _scrapheap_listing(client)[pic_id]
    assert row["auto_purge_exempt"] is False
    assert row["auto_purge_exempt_reason"] is None
    assert row["purge_at"] is not None, "An unlocked picture gets its countdown back"
    assert datetime.fromisoformat(row["purge_at"]) <= datetime.now(timezone.utc), (
        "...and it is genuinely overdue"
    )
    result = _run_purge_sweep(server)
    assert result == {"purged": 1, "skipped": 0, "retained": 0}, result
    assert _get_picture(server, pic_id) is None
    assert not os.path.isfile(path)


def test_listing_exempt_reason_protected_wins_over_locked(server, tmp_path):
    """A picture that is BOTH protected and locked reports the stronger reason."""
    client = _client(server)
    both_id, _ = _make_reference_picture(
        server, str(tmp_path / "both"), "both.png", allow_delete=False
    )
    prot_only_id, _ = _make_reference_picture(
        server, str(tmp_path / "prot"), "protonly.png", allow_delete=False
    )
    locked_only_id, _ = _make_reference_picture(
        server, str(tmp_path / "lock"), "lockonly.png", allow_delete=True
    )
    plain_id, _ = _make_reference_picture(
        server, str(tmp_path / "plain"), "plain.png", allow_delete=True
    )
    for pid in (both_id, prot_only_id, locked_only_id, plain_id):
        assert client.delete(f"/pictures/{pid}").status_code == 200
    _lock_picture_in_set(server, both_id)
    _lock_picture_in_set(server, locked_only_id)

    rows = _scrapheap_listing(client)
    assert rows[both_id]["auto_purge_exempt_reason"] == "protected", (
        "protected is permanent and intrinsic; it outranks a clearable lock"
    )
    assert rows[prot_only_id]["auto_purge_exempt_reason"] == "protected"
    assert rows[locked_only_id]["auto_purge_exempt_reason"] == "locked"
    assert rows[plain_id]["auto_purge_exempt_reason"] is None
    for pid in (both_id, prot_only_id, locked_only_id):
        assert rows[pid]["auto_purge_exempt"] is True
        assert rows[pid]["purge_at"] is None
    assert rows[plain_id]["auto_purge_exempt"] is False
    assert rows[plain_id]["purge_at"] is not None


def test_listing_marks_a_stack_sibling_freeze_as_locked(server, tmp_path):
    """The lock lookup must catch the live-stack-sibling freeze, not just direct
    set membership — the listing uses the same helper as the sweep."""
    client = _client(server)
    member_id, _ = _make_reference_picture(
        server, str(tmp_path / "m"), "stack_member.png", allow_delete=True
    )
    sibling_id, _ = _make_reference_picture(
        server, str(tmp_path / "s"), "stack_sibling.png", allow_delete=True
    )

    def _stack(session: Session):
        stack = PictureStack()
        session.add(stack)
        session.commit()
        session.refresh(stack)
        for pos, pid in enumerate((member_id, sibling_id)):
            pic = session.get(Picture, pid)
            pic.stack_id = stack.id
            pic.stack_position = pos
            session.add(pic)
        session.commit()

    server.vault.db.run_task(_stack)
    # Soft-delete FIRST (a locked stack refuses the delete with 423), then lock
    # only ONE of the two — the sibling is frozen transitively.
    for pid in (member_id, sibling_id):
        assert client.delete(f"/pictures/{pid}").status_code == 200
        _set_deleted_at(
            server,
            pid,
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
        )
    _lock_picture_in_set(server, member_id)

    rows = _scrapheap_listing(client)
    assert rows[sibling_id]["auto_purge_exempt_reason"] == "locked", (
        "A stack sibling of a locked-set member is frozen too, and the listing "
        "must say so"
    )
    assert rows[sibling_id]["purge_at"] is None
    assert _run_purge_sweep(server) is None, "...and the sweep must skip both"


def test_locked_lookup_is_chunked_for_a_large_scrapheap(server, monkeypatch):
    """N-2 — the lock lookup must never issue one huge ``IN (...)``.

    ``SQLITE_LIMIT_VARIABLE_NUMBER`` is 999 on SQLite builds older than 3.32.
    There, an unchunked lookup over a large scrapheap raises; the finder catches
    it and returns no work, so auto-purge would silently stop altogether.

    This asserts the batching DIRECTLY rather than by running a big query,
    because the assertion has to hold on every build — this test host reports a
    limit of 250000, so an unchunked query would pass here and the regression
    would only ever appear on a user's older SQLite.
    """
    assert scrapheap_service.LOCK_QUERY_CHUNK <= 999, (
        "the chunk must stay under the 999-variable limit of SQLite < 3.32"
    )

    batch_sizes: list[int] = []
    real = scrapheap_service.locked_picture_ids

    def _spy(session, picture_ids):
        ids = list(picture_ids)
        batch_sizes.append(len(ids))
        return real(session, ids)

    monkeypatch.setattr(scrapheap_service, "locked_picture_ids", _spy)

    over_limit = list(range(1, scrapheap_service.LOCK_QUERY_CHUNK * 2 + 50))
    assert (
        scrapheap_service.locked_scrapheap_picture_ids(server.vault, over_limit)
        == set()
    )
    assert len(batch_sizes) == 3, (
        f"{len(over_limit)} ids must be split into 3 batches, got {batch_sizes}"
    )
    assert max(batch_sizes) <= scrapheap_service.LOCK_QUERY_CHUNK, (
        f"a batch exceeded the chunk size: {batch_sizes}"
    )
    assert sum(batch_sizes) == len(over_limit), "every id must still be looked up"


def test_sweep_still_finds_work_in_a_large_scrapheap(server, tmp_path):
    """The other direction: chunking must not make the finder miss anything."""
    client = _client(server)
    pic_id, _ = _make_reference_picture(
        server, str(tmp_path / "refs"), "amongmany.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )
    # Force multiple chunks even for a small scrapheap.
    monkey = scrapheap_service.LOCK_QUERY_CHUNK
    try:
        scrapheap_service.LOCK_QUERY_CHUNK = 1
        due = scrapheap_service.find_due_retention_picture_ids(
            server.vault, datetime.now(timezone.utc), 30, None, 100
        )
    finally:
        scrapheap_service.LOCK_QUERY_CHUNK = monkey
    assert due == [pic_id], f"chunked lookup must not drop due candidates: {due}"


def test_listing_purge_at_is_null_when_retention_is_never(server, tmp_path):
    client = _client(server)
    pic_id, _ = _make_reference_picture(
        server, str(tmp_path / "refs"), "never.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200
    server.vault.set_scrapheap_retention(None, None)

    rows = _scrapheap_listing(client)
    assert rows[pic_id]["purge_at"] is None
    assert rows[pic_id]["auto_purge_exempt"] is False, (
        "Never disables the timer for everyone; it does not make managed "
        "pictures permanently exempt"
    )


def test_listing_exposes_retention_fields_in_the_grid_projection(server, tmp_path):
    """fields=grid must still carry the countdown contract."""
    client = _client(server)
    pic_id, _ = _make_reference_picture(
        server, str(tmp_path / "refs"), "grid.png", allow_delete=True
    )
    assert client.delete(f"/pictures/{pic_id}").status_code == 200

    resp = client.get("/pictures", params={"only_deleted": "true", "fields": "grid"})
    assert resp.status_code == 200, resp.text
    rows = {row["id"]: row for row in resp.json()}
    assert "purge_at" in rows[pic_id]
    assert rows[pic_id]["auto_purge_exempt"] is False
