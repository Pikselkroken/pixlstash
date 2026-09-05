"""Scrapheap auto-purge / retention tests (v1.8.0).

This is an AUTOMATIC file-destruction path, so the suite asserts both
directions everywhere: what MUST be destroyed once its window expires, and -
more importantly - everything that must NEVER be destroyed by a timer
(protected reference-folder originals, "Never", pictures inside their window,
pictures with no ``deleted_at``, and anything at all during a config save).

The background WorkPlanner is disabled in this module's server so the only
purge that ever runs is the one a test drives explicitly.
"""

import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select
from sqlalchemy import event

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
from pixlstash.tasks import (
    scrapheap_retention_purge_finder as scrapheap_retention_purge_finder_module,
)
from pixlstash.tasks.scrapheap_retention_purge_finder import (
    ScrapheapRetentionPurgeFinder,
)
from pixlstash.tasks.scrapheap_retention_purge_task import ScrapheapRetentionPurgeTask
from pixlstash.utils.image_processing.image_utils import ImageUtils
from tests.authz_guard import no_spa_fallback  # noqa: F401
from tests.utils import wipe_tables

# The SPA catch-all answers unmatched GETs with 200, so a wrong URL can make a
# positive assertion vacuous. See tests/authz_guard.py.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MIGRATIONS_DIR = os.path.join(_PROJECT_ROOT, "pixlstash")

# Auto-purge SHIPS OFF (``scrapheap_service.DEFAULT_RETENTION_DAYS is None``):
# nothing is destroyed on a timer the user never chose. So the baseline install
# this suite exercises is one where the user has EXPLICITLY turned auto-empty on
# at the shortest window - that is what makes the "what the sweep destroys"
# tests meaningful. The off-by-default behaviour has its own section below.
_ENABLED_RETENTION_DAYS = 30

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

    def _wipe_identity(session: Session):
        session.exec(delete(User))
        session.commit()

    # Two databases now: pictures and the ledger live in the vault, the user
    # lives in the hub.
    server.vault.db.run_task(wipe_tables, _RESET_TABLES)
    server.hub_engine.run_task(_wipe_identity)
    image_root = server.vault.image_root
    db_basenames = {"vault.db", "vault.db-wal", "vault.db-shm", "vault.db-journal"}
    for entry in os.listdir(image_root):
        if entry in db_basenames:
            continue
        path = os.path.join(image_root, entry)
        if os.path.isfile(path):
            os.remove(path)
    server.auth.ensure_user()
    server._server_config.pop(scrapheap_service.RETENTION_REDUCED_AT_KEY, None)
    # An install where the user turned auto-empty ON at 30 days. Written to both
    # server-config (what the endpoints read) and the vault (what the finder
    # reads) so the two can never disagree mid-test.
    server._server_config[scrapheap_service.RETENTION_DAYS_KEY] = (
        _ENABLED_RETENTION_DAYS
    )
    server.vault.set_scrapheap_retention(_ENABLED_RETENTION_DAYS, None)
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


def _rewire_retention_from_config(server):
    """Push server-config into the vault exactly as ``Server.__init__`` does.

    Lets a test put the process into the state a real boot would produce for a
    given server-config.json without paying for a second Server.
    """
    days = scrapheap_service.read_retention_days(server._server_config)
    reduced_at = scrapheap_service.read_retention_reduced_at(server._server_config)
    server.vault.set_scrapheap_retention(days, reduced_at)
    return days


# ── deleted_at stamping ───────────────────────────────────────────────────────


def test_soft_delete_stamps_deleted_at(server, tmp_path):
    """DELETE /pictures/{id} starts the retention clock."""
    client = _client(server)
    pic_id, _path = _make_reference_picture(
        server, str(tmp_path / "refs"), "a.png", allow_delete=True
    )
    assert _get_picture(server, pic_id).deleted_at is None

    before = datetime.now(timezone.utc).replace(tzinfo=None)
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
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
    clock - otherwise a stray client call silently grants an extra window."""
    client = _client(server)
    pic_id, _path = _make_reference_picture(
        server, str(tmp_path / "refs"), "c.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    original = _get_picture(server, pic_id).deleted_at
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5)
    _set_deleted_at(server, pic_id, old)

    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
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
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    resp = client.post("/pictures/scrapheap/restore", json={"picture_ids": [pic_id]})
    assert resp.status_code == 200, resp.text

    pic = _get_picture(server, pic_id)
    assert pic.deleted is False
    assert pic.deleted_at is None, "Restore must clear the retention stamp"

    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
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
    """F1 - the grace must protect pictures of ANY age, not just the [30,31) band.

    Measuring the grace from each picture's own ``deleted_at`` only ever moved
    the deadline of a picture already within a day of expiry. A 400-day-old
    picture stayed instantly purgeable, so `Never -> 30` (or `120 -> 30`) would
    wipe a long-lived scrapheap on the very next 15-minute sweep - seconds after
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

    # 31 days old - also the floor (deleted_at + 30 already passed).
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
    # The default is now None (auto-purge off), so turning it on for the FIRST
    # time is a reduction and earns the grace floor plus the impact confirm.
    # Enabling an unattended destruction path is the most consequential change
    # the control offers; it must not be the one that skips the safeguards.
    for choice in scrapheap_service.RETENTION_DAY_CHOICES:
        assert reduction(scrapheap_service.DEFAULT_RETENTION_DAYS, choice) is True, (
            f"turning auto-purge on at {choice} days must count as a reduction"
        )


# ── The finder's cadence gate must not depend on the machine's uptime ─────────


class _JustBootedClock:
    """``time`` shim whose ``monotonic()`` reads as a freshly booted host.

    ``time.monotonic()``'s reference point is undefined; on Linux it is seconds
    since BOOT. A long-lived workstation reports hundreds of thousands, so a
    ``_last_check_at = 0.0`` sentinel accidentally looks like "checked a very
    long time ago" and the bug stays invisible. A just-booted container or CI
    runner reports a few hundred, and the same sentinel then reads as "checked
    moments ago".
    """

    def __init__(self, uptime_s: float) -> None:
        self._uptime_s = uptime_s
        self._origin = time.monotonic()

    def __getattr__(self, name):
        return getattr(time, name)

    def monotonic(self) -> float:
        return self._uptime_s + (time.monotonic() - self._origin)


def test_the_first_sweep_runs_on_a_host_that_booted_moments_ago(
    server, tmp_path, monkeypatch
):
    """The finder must not skip its FIRST check just because uptime is low.

    ``_last_check_at`` was initialised to ``0.0`` and compared as an absolute
    monotonic instant, so on any host whose uptime is below the 15-minute check
    interval the very first ``find_task()`` returned ``None`` - the retention
    sweep silently did nothing until the machine had been up long enough. It
    self-heals, which is exactly why it went unnoticed: a finder that finds
    nothing is indistinguishable from "nothing to do".
    """
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "boot.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )

    monkeypatch.setattr(
        scrapheap_retention_purge_finder_module,
        "time",
        _JustBootedClock(uptime_s=1.0),
    )

    finder = ScrapheapRetentionPurgeFinder(vault=server.vault)
    task = finder.find_task()
    assert task is not None, (
        "The first sweep after start-up must run regardless of the host's "
        "uptime - _last_check_at must be a None sentinel, not an absolute "
        "monotonic instant"
    )
    assert task.run()["purged"] == 1
    assert _get_picture(server, pic_id) is None
    assert not os.path.isfile(path)


def test_the_finder_still_respects_its_interval_after_a_check(server, tmp_path):
    """Over-firing is its own regression: the cadence gate must still hold.

    The fix only changes what "never checked" means; a finder that HAS checked
    must still refuse to re-scan inside the interval.
    """
    client = _client(server)
    pic_id, _path = _make_reference_picture(
        server, str(tmp_path / "refs"), "cadence.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )

    finder = ScrapheapRetentionPurgeFinder(vault=server.vault)
    assert finder.find_task() is not None, "the first check must run"
    assert finder.find_task() is None, (
        "a second check inside the 15-minute interval must be refused"
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
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text

    long_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400)
    _set_deleted_at(server, unprot_id, long_ago)
    _set_deleted_at(server, prot_id, long_ago)

    result = _run_purge_sweep(server)
    assert result == {"purged": 1, "skipped": 0, "skipped_locked": 0, "retained": 0}, (
        result
    )

    # Unprotected: row gone, file destroyed, ledger says permanently removed.
    assert _get_picture(server, unprot_id) is None
    assert not os.path.isfile(unprot_path)
    assert _ledger_flags_for(server, unprot_path) == [True], (
        "An auto-purged picture must be logged file_removed=True so restore drops "
        "it rather than resurrecting it"
    )

    # Protected: completely untouched - this is the whole point of the policy.
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
    ``include_protected=False`` is otherwise untested - and a single flipped
    argument there would silently turn the timer into a destroyer of reference
    originals. This test drives the task with a protected id on purpose.
    """
    client = _client(server)
    prot_id, prot_path = _make_reference_picture(
        server, str(tmp_path / "prot"), "direct.png", allow_delete=False
    )
    delete_resp = client.delete(f"/pictures/{prot_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    _set_deleted_at(
        server,
        prot_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )

    task = ScrapheapRetentionPurgeTask(server.vault, [prot_id])
    assert task.run() == {
        "purged": 0,
        "skipped": 1,
        "skipped_locked": 0,
        "retained": 0,
    }

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
    delete_resp = client.delete(f"/pictures/{prot_id}")
    assert delete_resp.status_code == 200, delete_resp.text
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
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
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
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    _set_deleted_at(server, pic_id, None)

    assert _run_purge_sweep(server) is None
    assert _get_picture(server, pic_id) is not None
    assert os.path.isfile(path)


def test_never_disables_the_purge_entirely(server, tmp_path):
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "forever.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
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
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text

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
    assert result == {"purged": 2, "skipped": 0, "skipped_locked": 0, "retained": 0}, (
        result
    )
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
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
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
    assert result == {"purged": 1, "skipped": 0, "skipped_locked": 0, "retained": 0}, (
        result
    )
    assert _get_picture(server, pic_id) is None


def test_no_grace_for_pictures_deleted_after_the_reduction(server, tmp_path):
    """The grace is only for items that predate the reduction."""
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "after.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text

    now = datetime.now(timezone.utc)
    _set_deleted_at(
        server, pic_id, now.replace(tzinfo=None) - timedelta(days=30, hours=12)
    )
    # Reduction happened BEFORE this picture was deleted -> no grace, 30-day window.
    server.vault.set_scrapheap_retention(30, now - timedelta(days=90))

    result = _run_purge_sweep(server)
    assert result == {"purged": 1, "skipped": 0, "skipped_locked": 0, "retained": 0}, (
        result
    )
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
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 423, delete_resp.text


def test_auto_purge_never_destroys_a_locked_set_member(server, tmp_path):
    """F3 - a whole-set freeze must not be silently defeated by a timer.

    ``DELETE /pictures/{id}`` refuses a locked member with 423, so an unattended
    sweep 30 days later must not do what the user is forbidden to do by hand.
    """
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "locked.png", allow_delete=True
    )
    # Soft-delete FIRST, then lock: reaching the scrapheap is the precondition.
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
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

    # Layer 2: refused even when handed to the task directly. The lock is
    # reported as its own outcome, not folded into the retention "retained"
    # bucket - it is a different, path-independent reason to keep the row.
    assert ScrapheapRetentionPurgeTask(server.vault, [pic_id]).run() == {
        "purged": 0,
        "skipped": 0,
        "skipped_locked": 1,
        "retained": 0,
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
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
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
    assert result == {"purged": 1, "skipped": 0, "skipped_locked": 0, "retained": 0}, (
        result
    )
    assert _get_picture(server, pic_id) is None
    assert not os.path.isfile(path)


# ── F4: the second deadline guard ─────────────────────────────────────────────


def test_task_re_checks_the_deadline_and_refuses_an_in_window_picture(server, tmp_path):
    """F4 - a finder bug must not be able to destroy an in-window picture.

    The deadline used to be checked in exactly one place. Here the task is
    handed an id that is NOT due; the guard must retain it.
    """
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "inwindow.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
    )

    assert ScrapheapRetentionPurgeTask(server.vault, [pic_id]).run() == {
        "purged": 0,
        "skipped": 0,
        "skipped_locked": 0,
        "retained": 1,
    }
    assert _get_picture(server, pic_id) is not None
    assert os.path.isfile(path)
    assert _ledger_flags_for(server, path) == []


def test_restore_then_redelete_between_planning_and_purge_is_safe(server, tmp_path):
    """F4 - the real TOCTOU: the task runs at LOW priority and can be queued.

    The finder selects an expired picture; before the task runs the user restores
    it and deletes it again, so its deadline is now 30 days out. Re-checking at
    purge time is what stops the stale verdict from destroying it.
    """
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "toctou.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
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
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text

    assert task.run() == {
        "purged": 0,
        "skipped": 0,
        "skipped_locked": 0,
        "retained": 1,
    }
    assert _get_picture(server, pic_id) is not None, (
        "A picture re-deleted between planning and purge is inside a fresh "
        "window and must survive"
    )
    assert os.path.isfile(path)
    assert _ledger_flags_for(server, path) == []


# ── The manual delete-forever must respect a locked set too ───────────────────


def _confirm_token(client, ids=None):
    """Run the delete preview and return the confirmation it mints."""
    resp = client.post("/pictures/scrapheap/delete-preview", json={"ids": ids})
    assert resp.status_code == 200, resp.text
    token = resp.json().get("confirm_token")
    assert token, "the preview must mint a confirm_token"
    return token


def _delete_forever(client, include_protected, ids=None, confirm_token=None):
    """The real preview -> confirm flow: DELETE refuses without a confirmation."""
    body = {"include_protected": include_protected}
    if ids is not None:
        body["picture_ids"] = ids
    body["confirm_token"] = (
        confirm_token if confirm_token is not None else _confirm_token(client, ids)
    )
    resp = client.request("DELETE", "/api/v1/pictures/scrapheap", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize("include_protected", [False, True])
def test_delete_forever_never_destroys_a_locked_member(
    server, tmp_path, include_protected
):
    """The IRREVERSIBLE path must honour the lock at BOTH flag values.

    ``DELETE /pictures/{id}`` refuses a locked member with 423, the bulk
    soft-delete skips it, and the auto-purge sweep skips it - so this endpoint
    destroying it outright had the safety ordering exactly backwards.
    ``include_protected=true`` overrides the reference-folder protection; it does
    NOT override a locked set.
    """
    client = _client(server)
    locked_id, locked_path = _make_reference_picture(
        server, str(tmp_path / "locked"), "frozen.png", allow_delete=True
    )
    free_id, free_path = _make_reference_picture(
        server, str(tmp_path / "free"), "free.png", allow_delete=True
    )
    for pid in (locked_id, free_id):
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text
    _lock_picture_in_set(server, locked_id)

    body = _delete_forever(client, include_protected)
    assert body["skipped_locked"] == [locked_id], body
    assert body["deleted_count"] == 1, body

    # Locked: row kept AND still soft-deleted, file kept, NO ledger row.
    locked_pic = _get_picture(server, locked_id)
    assert locked_pic is not None and locked_pic.deleted is True
    assert os.path.isfile(locked_path), (
        "A locked member's file must survive delete-forever at any flag value"
    )
    assert _ledger_flags_for(server, locked_path) == [], (
        "A skipped locked picture must write no permanent-deletion ledger row"
    )

    # Over-blocking is its own regression: the unlocked sibling still dies.
    assert _get_picture(server, free_id) is None
    assert not os.path.isfile(free_path)


@pytest.mark.parametrize("include_protected", [False, True])
def test_delete_forever_of_a_locked_protected_picture_keeps_it(
    server, tmp_path, include_protected
):
    """Locked AND protected: the lock is the binding blocker either way."""
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "both"), "both.png", allow_delete=False
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    _lock_picture_in_set(server, pic_id)

    body = _delete_forever(client, include_protected)
    assert body["skipped_locked"] == [pic_id], body
    assert body["deleted_count"] == 0, body
    assert body["skipped_count"] == 0, (
        "A locked row is reported as locked, not double-counted as protected"
    )
    assert _get_picture(server, pic_id) is not None
    assert os.path.isfile(path)
    assert _ledger_flags_for(server, path) == []


def test_delete_forever_destroys_the_member_once_the_set_is_unlocked(server, tmp_path):
    """The other direction: the lock defers destruction, it does not forbid it."""
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "later.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    set_id = _lock_picture_in_set(server, pic_id)

    body = _delete_forever(client, False)
    assert body["skipped_locked"] == [pic_id]
    assert _get_picture(server, pic_id) is not None

    def _unlock(session: Session):
        pset = session.get(PictureSet, set_id)
        pset.locked = False
        session.add(pset)
        session.commit()

    server.vault.db.run_task(_unlock)

    body = _delete_forever(client, False)
    assert body["skipped_locked"] == [], body
    assert body["deleted_count"] == 1, body
    assert _get_picture(server, pic_id) is None
    assert not os.path.isfile(path)


def test_delete_forever_skips_a_locked_stack_sibling(server, tmp_path):
    """A stack sibling of a locked-set member is frozen transitively - the
    manual path uses the same shared lookup, so it inherits that."""
    client = _client(server)
    member_id, _ = _make_reference_picture(
        server, str(tmp_path / "m"), "m.png", allow_delete=True
    )
    sibling_id, sibling_path = _make_reference_picture(
        server, str(tmp_path / "s"), "s.png", allow_delete=True
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
    for pid in (member_id, sibling_id):
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text
    _lock_picture_in_set(server, member_id)

    body = _delete_forever(client, True)
    assert sorted(body["skipped_locked"]) == sorted([member_id, sibling_id]), body
    assert body["deleted_count"] == 0
    assert os.path.isfile(sibling_path)


def test_delete_forever_still_destroys_everything_it_should(server, tmp_path):
    """Over-blocking regression guard: with nothing locked, behaviour is
    unchanged from before the lock check existed."""
    client = _client(server)
    prot_id, prot_path = _make_reference_picture(
        server, str(tmp_path / "prot"), "p.png", allow_delete=False
    )
    unprot_id, unprot_path = _make_reference_picture(
        server, str(tmp_path / "unprot"), "u.png", allow_delete=True
    )
    for pid in (prot_id, unprot_id):
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text

    body = _delete_forever(client, False)
    assert body == {
        **body,
        "deleted_count": 1,
        "skipped_count": 1,
        "skipped_locked": [],
    }, body
    assert _get_picture(server, unprot_id) is None
    assert not os.path.isfile(unprot_path)
    assert _get_picture(server, prot_id) is not None
    assert os.path.isfile(prot_path)

    body = _delete_forever(client, True)
    assert body["deleted_count"] == 1, body
    assert body["skipped_locked"] == []
    assert _get_picture(server, prot_id) is None
    assert not os.path.isfile(prot_path)
    assert _ledger_flags_for(server, prot_path) == [True]


# ── The server must require its own confirmation, not trust the dialog ────────


def _scrapheap_two(server, tmp_path, client):
    """Two ordinary unprotected pictures, both in the scrapheap."""
    made = []
    for i in range(2):
        pic_id, path = _make_reference_picture(
            server, str(tmp_path / f"c{i}"), f"c{i}.png", allow_delete=True
        )
        delete_resp = client.delete(f"/pictures/{pic_id}")
        assert delete_resp.status_code == 200, delete_resp.text
        made.append((pic_id, path))
    return made


def test_delete_forever_refuses_without_a_confirmation(server, tmp_path):
    """BLOCKER #3 - the type-to-confirm dialog is client-side and proves nothing.

    A bare, bodyless DELETE used to destroy the ENTIRE scrapheap and its files.
    CORS admits any localhost/LAN-IP port with credentials, so a page on another
    local port could drive it. Every unconfirmed shape must now be refused with
    nothing destroyed.
    """
    client = _client(server)
    made = _scrapheap_two(server, tmp_path, client)

    # No body at all - the exact shape that emptied the whole scrapheap.
    resp = client.request("DELETE", "/api/v1/pictures/scrapheap")
    assert resp.status_code == 400, resp.text
    assert "confirm_token" in resp.json()["detail"]

    # A body, but no confirmation.
    resp = client.request(
        "DELETE", "/api/v1/pictures/scrapheap", json={"include_protected": True}
    )
    assert resp.status_code == 400, resp.text

    # A made-up confirmation.
    resp = client.request(
        "DELETE",
        "/api/v1/pictures/scrapheap",
        json={"confirm_token": "not-a-real-token"},
    )
    assert resp.status_code == 409, resp.text

    for pic_id, path in made:
        assert _get_picture(server, pic_id) is not None, (
            "a refusal must destroy nothing"
        )
        assert os.path.isfile(path)
        assert _ledger_flags_for(server, path) == []


def test_preview_then_confirm_still_empties_the_scrapheap(server, tmp_path):
    """Over-blocking regression guard: the legitimate flow must still work."""
    client = _client(server)
    made = _scrapheap_two(server, tmp_path, client)

    preview = client.post("/pictures/scrapheap/delete-preview", json={"ids": None})
    assert preview.status_code == 200, preview.text
    token = preview.json()["confirm_token"]
    assert token

    resp = client.request(
        "DELETE",
        "/api/v1/pictures/scrapheap",
        json={"include_protected": False, "confirm_token": token},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted_count"] == 2, resp.text
    for pic_id, path in made:
        assert _get_picture(server, pic_id) is None
        assert not os.path.isfile(path)


def test_a_confirmation_cannot_be_spent_twice(server, tmp_path):
    """Single-use: a captured confirmation must not authorise a second purge."""
    client = _client(server)
    (first_id, _first_path), (second_id, second_path) = _scrapheap_two(
        server, tmp_path, client
    )

    token = _confirm_token(client, [first_id])
    resp = client.request(
        "DELETE",
        "/api/v1/pictures/scrapheap",
        json={"picture_ids": [first_id], "confirm_token": token},
    )
    assert resp.status_code == 200, resp.text
    assert _get_picture(server, first_id) is None

    resp = client.request(
        "DELETE",
        "/api/v1/pictures/scrapheap",
        json={"picture_ids": [second_id], "confirm_token": token},
    )
    assert resp.status_code == 409, resp.text
    assert _get_picture(server, second_id) is not None
    assert os.path.isfile(second_path)


def test_a_confirmation_is_bound_to_the_selection_it_previewed(server, tmp_path):
    """A confirmation for one picture must not be spendable on the whole heap."""
    client = _client(server)
    (first_id, first_path), (second_id, second_path) = _scrapheap_two(
        server, tmp_path, client
    )

    token = _confirm_token(client, [first_id])
    resp = client.request(
        "DELETE", "/api/v1/pictures/scrapheap", json={"confirm_token": token}
    )
    assert resp.status_code == 409, resp.text
    for pic_id, path in ((first_id, first_path), (second_id, second_path)):
        assert _get_picture(server, pic_id) is not None
        assert os.path.isfile(path)


def test_a_confirmation_expires(server, tmp_path, monkeypatch):
    """A stale confirmation left in a closed tab is not a standing capability."""
    client = _client(server)
    made = _scrapheap_two(server, tmp_path, client)
    token = _confirm_token(client, None)

    real_monotonic = scrapheap_service.time.monotonic
    monkeypatch.setattr(
        scrapheap_service.time,
        "monotonic",
        lambda: real_monotonic() + scrapheap_service.CONFIRM_TOKEN_TTL_SECONDS + 1,
    )
    resp = client.request(
        "DELETE", "/api/v1/pictures/scrapheap", json={"confirm_token": token}
    )
    assert resp.status_code == 409, resp.text
    for pic_id, path in made:
        assert _get_picture(server, pic_id) is not None
        assert os.path.isfile(path)


def test_a_confirmation_is_bound_to_library_generation():
    """The same numeric picture id in another vault is never authorised."""
    confirmations = scrapheap_service.ScrapheapDeleteConfirmations()
    token = confirmations.issue([1], 1, library_uuid="library-a", generation=7)

    accepted, _reason = confirmations.redeem(
        token, [1], library_uuid="library-b", generation=8
    )

    assert accepted is False


def test_the_auto_purge_needs_no_confirmation(server, tmp_path):
    """The confirmation gates the HTTP endpoint, not the unattended sweep."""
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "old"), "old.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )

    assert _run_purge_sweep(server)["purged"] == 1
    assert _get_picture(server, pic_id) is None
    assert not os.path.isfile(path)


# ── A restore landing mid-purge must not be hard-deleted ──────────────────────


def _restore_between_planning_and_purge(monkeypatch, picture_id):
    """Commit a restore of ``picture_id`` in the window before the DELETE.

    Models a ``POST /pictures/scrapheap/restore`` that lands after the purge has
    selected its ids and before the rows are deleted. Injected at the real seam -
    ``purge_rows_in_session``, the function that issues the DELETE - so the test
    holds whether the purge is one DB submission or several.
    """
    original = scrapheap_service.purge_rows_in_session

    def _restore_then_purge(session, picture_ids, log_records):
        pic = session.get(Picture, picture_id)
        assert pic is not None
        pic.deleted = False
        pic.deleted_at = None
        session.add(pic)
        session.commit()
        return original(session, picture_ids, log_records)

    monkeypatch.setattr(scrapheap_service, "purge_rows_in_session", _restore_then_purge)


def test_delete_forever_does_not_destroy_a_picture_restored_mid_purge(
    server, tmp_path, monkeypatch
):
    """BLOCKER #4 - the purge must re-check ``deleted`` where it deletes.

    The purge selects its ids while the pictures are scrapheaped, then deletes
    BY ID. A restore landing in between makes those ids live again, so an
    unqualified ``DELETE ... WHERE id IN (...)`` hard-deletes rows the user just
    rescued - and removes their files from disk. Both directions asserted: the
    restored picture survives intact, and the one still in the scrapheap is
    still destroyed (under-deleting is its own regression).
    """
    client = _client(server)
    restored_id, restored_path = _make_reference_picture(
        server, str(tmp_path / "rescued"), "rescued.png", allow_delete=True
    )
    doomed_id, doomed_path = _make_reference_picture(
        server, str(tmp_path / "doomed"), "doomed.png", allow_delete=True
    )
    for pid in (restored_id, doomed_id):
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text

    _restore_between_planning_and_purge(monkeypatch, restored_id)

    body = _delete_forever(client, False)

    # Negative: the restored picture is live again and must be untouched.
    rescued = _get_picture(server, restored_id)
    assert rescued is not None, (
        "A picture restored between the id selection and the DELETE was "
        "permanently destroyed - the purge must re-check `deleted`"
    )
    assert rescued.deleted is False
    assert os.path.isfile(restored_path), (
        "The restored picture's file was removed from disk by the purge"
    )
    assert _ledger_flags_for(server, restored_path) == [], (
        "A skipped picture must not get a permanent-deletion ledger row"
    )

    # Positive: everything still scrapheaped is destroyed as before.
    assert _get_picture(server, doomed_id) is None
    assert not os.path.isfile(doomed_path)
    assert _ledger_flags_for(server, doomed_path) == [True]
    assert body["deleted_count"] == 1, body


def test_auto_purge_does_not_destroy_a_picture_restored_mid_purge(
    server, tmp_path, monkeypatch
):
    """The same race on the UNATTENDED path (the retention auto-purge)."""
    client = _client(server)
    restored_id, restored_path = _make_reference_picture(
        server, str(tmp_path / "rescued"), "rescued.png", allow_delete=True
    )
    doomed_id, doomed_path = _make_reference_picture(
        server, str(tmp_path / "doomed"), "doomed.png", allow_delete=True
    )
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400)
    for pid in (restored_id, doomed_id):
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text
        _set_deleted_at(server, pid, old)

    _restore_between_planning_and_purge(monkeypatch, restored_id)

    result = _run_purge_sweep(server)

    assert _get_picture(server, restored_id) is not None, (
        "The unattended sweep destroyed a picture restored mid-purge"
    )
    assert os.path.isfile(restored_path)
    assert _ledger_flags_for(server, restored_path) == []
    assert _get_picture(server, doomed_id) is None
    assert not os.path.isfile(doomed_path)
    assert result["purged"] == 1, result


def test_purge_rows_skips_ids_that_left_the_scrapheap(server, tmp_path):
    """Unit-level: the ledger + DELETE are both scoped to still-deleted rows."""
    live_id, live_path = _make_reference_picture(
        server, str(tmp_path / "live"), "live.png", allow_delete=True
    )
    gone_id, gone_path = _make_reference_picture(
        server, str(tmp_path / "gone"), "gone.png", allow_delete=True
    )

    def _mark(session: Session):
        # `gone` is in the scrapheap, `live` never was - exactly the state the
        # purge finds after a concurrent restore.
        pic = session.get(Picture, gone_id)
        pic.deleted = True
        pic.deleted_at = datetime.now(timezone.utc)
        session.add(pic)
        session.commit()

    server.vault.db.run_task(_mark)

    records = [
        {
            "picture_id": live_id,
            "path_sha": DeletedFileLog.hash_path(live_path),
            "pixel_sha": None,
            "file_removed": True,
        },
        {
            "picture_id": gone_id,
            "path_sha": DeletedFileLog.hash_path(gone_path),
            "pixel_sha": None,
            "file_removed": True,
        },
    ]
    deleted_count, owned, skipped = server.vault.db.run_task(
        scrapheap_service.purge_rows_in_session, [live_id, gone_id], records
    )

    assert skipped == {live_id}
    assert deleted_count == 1
    assert _get_picture(server, live_id) is not None
    assert _get_picture(server, gone_id) is None
    assert _ledger_flags_for(server, live_path) == []
    assert _ledger_flags_for(server, gone_path) == [True]
    assert owned == {DeletedFileLog.hash_path(gone_path)}


def test_a_row_the_delete_spares_keeps_its_file_and_gets_no_ledger_row(
    server, tmp_path, monkeypatch
):
    """F1 - the guarded DELETE, not the re-check, must decide what was destroyed.

    Deriving the skip list from the re-check SELECT alone saved the ROW but left
    the caller unlinking the FILE and the ledger asserting ``file_removed=True``
    - a live picture with no original on disk that neither restore nor a re-scan
    recovers. Here the re-check blesses both ids and the restore lands after it,
    so the DELETE's own ``deleted`` predicate spares one of them.
    """
    client = _client(server)
    saved_id, saved_path = _make_reference_picture(
        server, str(tmp_path / "saved"), "saved.png", allow_delete=True
    )
    doomed_id, doomed_path = _make_reference_picture(
        server, str(tmp_path / "doomed"), "doomed.png", allow_delete=True
    )
    for pid in (saved_id, doomed_id):
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text

    original_recheck = scrapheap_service.still_scrapheaped_ids_in_session

    def _restore_after_the_recheck(session, picture_ids):
        """Blessed by the re-check, then restored before the DELETE runs."""
        selected = original_recheck(session, picture_ids)
        if saved_id in selected:
            pic = session.get(Picture, saved_id)
            pic.deleted = False
            pic.deleted_at = None
            session.add(pic)
            session.commit()
        return selected

    monkeypatch.setattr(
        scrapheap_service,
        "still_scrapheaped_ids_in_session",
        _restore_after_the_recheck,
    )

    body = _delete_forever(client, False)

    # Negative: the spared row keeps its file, and the ledger must not claim a
    # permanent deletion that never happened.
    assert _get_picture(server, saved_id) is not None
    assert os.path.isfile(saved_path), (
        "A row the DELETE spared had its file unlinked - the skip list must "
        "come from what the DELETE actually removed"
    )
    assert _ledger_flags_for(server, saved_path) == [], (
        "The ledger asserted a permanent deletion for a picture that survived"
    )
    # The whole batch fails closed rather than committing a partial result.
    assert body["deleted_count"] == 0, body
    assert sorted(body["skipped_restored"]) == sorted([saved_id, doomed_id]), body
    assert _get_picture(server, doomed_id) is not None
    assert os.path.isfile(doomed_path)
    assert _ledger_flags_for(server, doomed_path) == []

    # Positive: with no mismatch injected, the ordinary purge still destroys it.
    monkeypatch.setattr(
        scrapheap_service, "still_scrapheaped_ids_in_session", original_recheck
    )
    body = _delete_forever(client, False)
    assert body["deleted_count"] == 1, body
    assert _get_picture(server, doomed_id) is None
    assert not os.path.isfile(doomed_path)
    assert _ledger_flags_for(server, doomed_path) == [True]
    # ...and the restored picture is still untouched by that second purge.
    assert _get_picture(server, saved_id) is not None
    assert os.path.isfile(saved_path)


def test_a_removal_target_with_no_picture_id_is_refused_not_unlinked(
    server, tmp_path, monkeypatch
):
    """F1 sibling - an unidentifiable target must fail CLOSED, not open.

    The skip filter matches removal targets by picture id. A target carrying no
    id cannot be shown to have been destroyed, so admitting it unconditionally
    unlinked a file for a row that may still exist - including on the rollback
    path, where nothing was destroyed at all. Unreachable today (a persisted
    Picture always has a PK), but "unknown" must never mean "delete it" on the
    one irreversible path.
    """
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "anon.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text

    original_plan = scrapheap_service.build_purge_plan

    def _anonymise_the_targets(*args, **kwargs):
        plan = original_plan(*args, **kwargs)
        plan.removal_targets = [
            (None, rel_path, protected)
            for _pid, rel_path, protected in plan.removal_targets
        ]
        return plan

    monkeypatch.setattr(scrapheap_service, "build_purge_plan", _anonymise_the_targets)

    body = _delete_forever(client, False)

    # The row is still purged - this guards the FILE, not the row.
    assert body["deleted_count"] == 1, body
    assert _get_picture(server, pic_id) is None
    assert os.path.isfile(path), (
        "A removal target with no picture id had its file unlinked; an "
        "unidentifiable target must be refused, not guessed"
    )

    # Positive: with real ids on the targets, the file is still destroyed.
    monkeypatch.setattr(scrapheap_service, "build_purge_plan", original_plan)
    other_id, other_path = _make_reference_picture(
        server, str(tmp_path / "refs2"), "named.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{other_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    assert _delete_forever(client, False)["deleted_count"] == 1
    assert _get_picture(server, other_id) is None
    assert not os.path.isfile(other_path)


# ── Delete preview counts ─────────────────────────────────────────────────────


def _preview(client, ids=None):
    resp = client.post("/pictures/scrapheap/delete-preview", json={"ids": ids})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_delete_preview_counts_are_disjoint_and_honest(server, tmp_path):
    """The three counts must partition the set, keyed on what each button kills.

    A locked+protected row counts as LOCKED, never as protected: counting it
    under ``protected_count`` would tell the user "Delete all" destroys it when
    the lock in fact stops that.
    """
    client = _client(server)
    locked_only, _ = _make_reference_picture(
        server, str(tmp_path / "l"), "l.png", allow_delete=True
    )
    protected_only, prot_path = _make_reference_picture(
        server, str(tmp_path / "p"), "p.png", allow_delete=False
    )
    both, _ = _make_reference_picture(
        server, str(tmp_path / "b"), "b.png", allow_delete=False
    )
    neither, _ = _make_reference_picture(
        server, str(tmp_path / "n"), "n.png", allow_delete=True
    )
    for pid in (locked_only, protected_only, both, neither):
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text
    _lock_picture_in_set(server, locked_only)
    _lock_picture_in_set(server, both)

    body = _preview(client)
    assert body["total_count"] == 4, body
    assert body["locked_count"] == 2, body
    assert body["protected_count"] == 1, "locked+protected counts as locked only"
    assert body["unprotected_count"] == 1, body
    assert (
        body["locked_count"] + body["protected_count"] + body["unprotected_count"]
        == body["total_count"]
    ), "the three buckets must partition the delete set"
    assert sorted(body["locked"]) == sorted([locked_only, both])
    assert [item["id"] for item in body["protected"]] == [protected_only]
    assert [item["file_path"] for item in body["protected"]] == [prot_path], (
        "the at-risk original must be named by absolute on-disk path"
    )


def test_delete_preview_counts_match_what_each_action_destroys(server, tmp_path):
    """The dialog's promise, verified against the endpoint that fulfils it."""
    client = _client(server)
    locked_id, _ = _make_reference_picture(
        server, str(tmp_path / "l"), "l.png", allow_delete=True
    )
    prot_id, _ = _make_reference_picture(
        server, str(tmp_path / "p"), "p.png", allow_delete=False
    )
    plain_id, _ = _make_reference_picture(
        server, str(tmp_path / "n"), "n.png", allow_delete=True
    )
    for pid in (locked_id, prot_id, plain_id):
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text
    _lock_picture_in_set(server, locked_id)

    body = _preview(client)
    unprotected_count = body["unprotected_count"]
    protected_count = body["protected_count"]

    # "Delete unprotected only" destroys exactly unprotected_count.
    assert _delete_forever(client, False)["deleted_count"] == unprotected_count
    # "Delete all" then destroys exactly the protected remainder.
    assert _delete_forever(client, True)["deleted_count"] == protected_count
    # ...and the locked one survived both, as the preview implied.
    assert _get_picture(server, locked_id) is not None


def test_delete_preview_empty_scrapheap(server):
    body = _preview(_client(server))
    assert body["total_count"] == 0
    assert body["locked_count"] == 0
    assert body["protected_count"] == 0
    assert body["unprotected_count"] == 0
    assert body["locked"] == []
    assert body["protected"] == []


def test_manual_delete_forever_is_not_subject_to_the_retention_guard(server, tmp_path):
    """Over-blocking is its own regression: a human's explicit confirmation must
    still purge immediately, with no timer standing in the way."""
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "manual.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    # Deleted seconds ago - nowhere near its deadline.

    resp = client.request(
        "DELETE",
        "/api/v1/pictures/scrapheap",
        json={
            "include_protected": False,
            "confirm_token": _confirm_token(client, None),
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted_count"] == 1
    assert _get_picture(server, pic_id) is None
    assert not os.path.isfile(path)


# ── Item 2 (F5): the ledger must not claim a deletion that did not happen ─────


def test_successful_removal_keeps_file_removed_true(server, tmp_path):
    """The success path is unchanged: genuinely gone means file_removed=True."""
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "gone.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text

    assert _delete_forever(client, False)["deleted_count"] == 1
    assert not os.path.isfile(path)
    assert _ledger_flags_for(server, path) == [True], (
        "a genuinely destroyed file must stay file_removed=True so restore never "
        "resurrects it"
    )


def test_failed_removal_corrects_the_ledger_to_file_kept(server, tmp_path, monkeypatch):
    """If os.remove raises, the ledger must not keep asserting "genuinely gone".

    The row is written before the file is touched (deliberately - writing it
    afterwards would leave a window with no ledger entry, which is how the
    reference-folder scan resurrects deleted content), so file_removed=True is a
    PREDICTION. When the removal fails, the prediction is wrong and the row must
    be corrected to False - the accurate "removed from library, file kept" -
    or restore would drop the picture forever on the strength of a deletion that
    never happened.
    """
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "stubborn.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text

    real_remove = os.remove

    def _boom(target, *a, **kw):
        if str(target) == path:
            raise OSError(30, "Read-only file system")
        return real_remove(target, *a, **kw)

    monkeypatch.setattr(os, "remove", _boom)
    assert _delete_forever(client, False)["deleted_count"] == 1
    monkeypatch.undo()

    assert os.path.isfile(path), "the file survived, which is the whole premise"
    assert _ledger_flags_for(server, path) == [False], (
        "a file that was NOT destroyed must be logged file_removed=False so "
        "restore can bring the picture back"
    )


def test_failed_removal_never_downgrades_another_purges_ledger_row(
    server, tmp_path, monkeypatch
):
    """A purge may only correct the ledger rows IT wrote.

    The ledger is keyed by PATH, not by picture identity, so without a
    write-ownership bound a later purge at the same path could reach back and
    rewrite a row describing content some earlier purge genuinely destroyed:

        purge A destroys content C1 at path P  -> ledger(P) = (True, C1)
        different content C2 is written at P and indexed as picture B
        purge B is denied by os.remove         -> unconfirmed
        ...and would downgrade A's row to False, so restoring a snapshot
        containing A resurrects it bound to C2's file.

    No API route can currently build that collision (reference folders reject
    overlapping roots with 409, the routine scan skips ledgered paths, explicit
    re-import clears the row) - which is exactly why this asserts the structural
    guarantee rather than relying on those surrounding guards holding forever.
    """
    client = _client(server)
    folder = tmp_path / "shared_path"

    # --- Purge A: content C1 at path P is genuinely destroyed. ---------------
    pic_a, path_p = _make_reference_picture(
        server, str(folder), "P.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_a}")
    assert delete_resp.status_code == 200, delete_resp.text
    assert _delete_forever(client, False)["deleted_count"] == 1
    assert not os.path.isfile(path_p)
    assert _ledger_flags_for(server, path_p) == [True], "purge A must log a real kill"

    # --- Different content C2 is written at the SAME path and indexed. -------
    pic_b, path_b = _make_reference_picture(
        server, str(folder), "P.png", allow_delete=True
    )
    assert path_b == path_p, "the collision requires the same path"
    delete_resp = client.delete(f"/pictures/{pic_b}")
    assert delete_resp.status_code == 200, delete_resp.text

    # --- Purge B: removal is denied. ----------------------------------------
    real_remove = os.remove

    def _boom(target, *a, **kw):
        if str(target) == path_p:
            raise PermissionError(13, "Permission denied")
        return real_remove(target, *a, **kw)

    monkeypatch.setattr(os, "remove", _boom)
    assert _delete_forever(client, False)["deleted_count"] == 1
    monkeypatch.undo()

    assert os.path.isfile(path_p), "C2's file survived, which is the premise"
    assert _ledger_flags_for(server, path_p) == [True], (
        "purge B did not write this row - it records purge A's genuinely "
        "destroyed content C1 - so a failed removal in B must NOT downgrade it"
    )


def test_failed_removal_still_downgrades_the_row_this_purge_wrote(
    server, tmp_path, monkeypatch
):
    """Over-blocking guard: the ownership bound must not disable the F5 fix.

    A row this purge DID write is still corrected to False on a failed removal -
    that correction is the whole point, and narrowing it to owned rows must not
    quietly turn it off.
    """
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "fresh"), "mine.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    assert _ledger_flags_for(server, path) == [], "no pre-existing row for this path"

    real_remove = os.remove

    def _boom(target, *a, **kw):
        if str(target) == path:
            raise OSError(30, "Read-only file system")
        return real_remove(target, *a, **kw)

    monkeypatch.setattr(os, "remove", _boom)
    assert _delete_forever(client, False)["deleted_count"] == 1
    monkeypatch.undo()

    assert os.path.isfile(path)
    assert _ledger_flags_for(server, path) == [False], (
        "this purge created the row, so its failed removal MUST correct it"
    )


def test_failed_removal_downgrades_a_row_this_purge_raised_from_false(
    server, tmp_path, monkeypatch
):
    """Ownership also covers a row this call RAISED False -> True.

    Such a row previously meant "file kept on disk"; this purge claimed it as a
    real deletion, so this purge owns the claim and must retract it when the
    removal fails.
    """
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "raised"), "kept_then_killed.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text

    # Pre-existing "removed from library, file kept" row for this path.
    def _seed(session: Session):
        session.add(
            DeletedFileLog(
                path_sha=DeletedFileLog.hash_path(path),
                pixel_sha=None,
                deleted_at=datetime.now(timezone.utc),
                file_removed=False,
            )
        )
        session.commit()

    server.vault.db.run_task(_seed)
    assert _ledger_flags_for(server, path) == [False]

    real_remove = os.remove

    def _boom(target, *a, **kw):
        if str(target) == path:
            raise OSError(30, "Read-only file system")
        return real_remove(target, *a, **kw)

    monkeypatch.setattr(os, "remove", _boom)
    assert _delete_forever(client, False)["deleted_count"] == 1
    monkeypatch.undo()

    assert os.path.isfile(path)
    assert _ledger_flags_for(server, path) == [False], (
        "this purge raised the row to True, so it owns the claim and must "
        "retract it when the removal fails"
    )


def test_unreachable_location_corrects_the_ledger_to_file_kept(server, tmp_path):
    """An unmounted volume looks exactly like a deleted file to os.path.isfile.

    Claiming file_removed=True there would permanently strand every picture on
    the volume: the row is gone and restore is told never to resurrect it.
    """
    client = _client(server)
    folder = tmp_path / "removable"
    pic_id, path = _make_reference_picture(
        server, str(folder), "on_a_usb_stick.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text

    # Simulate the volume going away between the soft delete and the purge.
    shutil.move(str(folder), str(tmp_path / "unmounted"))
    assert not os.path.isdir(folder)

    assert _delete_forever(client, False)["deleted_count"] == 1
    assert _ledger_flags_for(server, path) == [False], (
        "an unreachable location must never be recorded as a confirmed deletion"
    )
    # The file really is still out there.
    assert os.path.isfile(str(tmp_path / "unmounted" / "on_a_usb_stick.png"))


def test_missing_file_purge_task_skips_unreachable_locations(server, tmp_path):
    """The same trap in the orphan-row reaper: an unmounted folder is not a
    deleted file, so its rows must not be purged at all."""
    from pixlstash.tasks.missing_file_purge_task import MissingFilePurgeTask

    folder = tmp_path / "removable"
    pic_id, path = _make_reference_picture(
        server, str(folder), "still_there.png", allow_delete=True
    )
    shutil.move(str(folder), str(tmp_path / "unmounted"))

    pictures = server.vault.db.run_immediate_read_task(
        lambda s: [s.get(Picture, pic_id)]
    )
    result = MissingFilePurgeTask(
        database=server.vault.db, pictures=pictures
    )._run_task()
    assert result == {"purged": 0, "repaired": 0, "deferred": 0}, result
    assert _get_picture(server, pic_id) is not None, (
        "a picture on an unmounted volume must not be reaped as missing"
    )
    assert _ledger_flags_for(server, path) == []


def test_missing_file_purge_task_still_reaps_a_genuinely_deleted_file(server, tmp_path):
    """Over-blocking guard: a truly deleted file is still reaped and logged."""
    from pixlstash.tasks.missing_file_purge_task import MissingFilePurgeTask

    folder = tmp_path / "present"
    pic_id, path = _make_reference_picture(
        server, str(folder), "really_gone.png", allow_delete=True
    )
    os.remove(path)  # file gone, directory still there

    pictures = server.vault.db.run_immediate_read_task(
        lambda s: [s.get(Picture, pic_id)]
    )
    result = MissingFilePurgeTask(
        database=server.vault.db, pictures=pictures
    )._run_task()
    # Exact equality on purpose: nothing may be quietly repaired or deferred
    # instead of reaped. The move-journal guard must not become a blanket
    # exemption for every missing file.
    assert result == {"purged": 1, "repaired": 0, "deferred": 0}, result
    assert _get_picture(server, pic_id) is None
    assert _ledger_flags_for(server, path) == [True]


# ── Item 1: the retention impact endpoint ─────────────────────────────────────


def _impact(client, days, expect=200):
    resp = client.get(
        "/server-config/scrapheap-retention/impact", params={"days": days}
    )
    assert resp.status_code == expect, resp.text
    return resp.json()


def test_impact_count_matches_what_a_sweep_actually_destroys(server, tmp_path):
    """The number driving the confirmation must equal the real consequence.

    Asserted against an ACTUAL purge, not just against the arithmetic: the count
    exists to obtain informed consent, so a number that merely looks plausible
    is not good enough.
    """
    client = _client(server)
    old_ids = []
    for i in range(3):
        pid, _ = _make_reference_picture(
            server, str(tmp_path / f"old{i}"), f"old{i}.png", allow_delete=True
        )
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text
        _set_deleted_at(
            server,
            pid,
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=100),
        )
        old_ids.append(pid)
    # Inside a 30-day window, so not part of the impact.
    young_id, young_path = _make_reference_picture(
        server, str(tmp_path / "young"), "young.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{young_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    _set_deleted_at(
        server,
        young_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5),
    )

    # Current window is the default 30; preview a drop to... nothing lower
    # exists, so raise it first and preview the drop back down.
    assert (
        client.patch(
            "/server-config/scrapheap-retention",
            json={"scrapheap_retention_days": 120},
        ).status_code
        == 200
    )
    body = _impact(client, 30)
    assert body["would_purge_count"] == 3, body
    assert body["first_purge_at"] is not None

    # Now apply it for real and let the sweep run past the grace floor.
    assert (
        client.patch(
            "/server-config/scrapheap-retention",
            json={"scrapheap_retention_days": 30},
        ).status_code
        == 200
    )
    server.vault.set_scrapheap_retention(
        30, datetime.now(timezone.utc) - timedelta(days=2)
    )
    result = _run_purge_sweep(server)
    assert result["purged"] == body["would_purge_count"], (
        f"impact promised {body['would_purge_count']}, sweep destroyed "
        f"{result['purged']}"
    )
    for pid in old_ids:
        assert _get_picture(server, pid) is None
    assert _get_picture(server, young_id) is not None
    assert os.path.isfile(young_path)


def test_impact_excludes_protected_and_locked(server, tmp_path):
    """Neither is ever auto-purged, so counting them would overstate the harm."""
    client = _client(server)
    plain_id, _ = _make_reference_picture(
        server, str(tmp_path / "plain"), "plain.png", allow_delete=True
    )
    prot_id, _ = _make_reference_picture(
        server, str(tmp_path / "prot"), "prot.png", allow_delete=False
    )
    locked_id, _ = _make_reference_picture(
        server, str(tmp_path / "lock"), "lock.png", allow_delete=True
    )
    for pid in (plain_id, prot_id, locked_id):
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text
        _set_deleted_at(
            server,
            pid,
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
        )
    _lock_picture_in_set(server, locked_id)

    assert (
        client.patch(
            "/server-config/scrapheap-retention",
            json={"scrapheap_retention_days": 120},
        ).status_code
        == 200
    )
    body = _impact(client, 30)
    assert body["would_purge_count"] == 1, (
        f"only the unprotected, unlocked picture is at risk: {body}"
    )


def test_impact_counts_pictures_expiring_during_the_grace_day(server, tmp_path):
    """Evaluated at the grace floor, not at now - otherwise it understates.

    A picture that crosses its deadline DURING the grace day is destroyed by the
    first sweep after the floor elapses, so the confirmation must include it.
    """
    client = _client(server)
    pic_id, _ = _make_reference_picture(
        server, str(tmp_path / "edge"), "edge.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    # 29.5 days old: NOT past a 30-day window right now, but it will be within
    # the one-day grace period.
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=29, hours=12),
    )
    assert (
        client.patch(
            "/server-config/scrapheap-retention",
            json={"scrapheap_retention_days": 120},
        ).status_code
        == 200
    )
    assert _impact(client, 30)["would_purge_count"] == 1, (
        "a picture that expires during the grace day must be counted"
    )


def test_impact_is_zero_when_not_a_reduction(server, tmp_path):
    client = _client(server)
    pic_id, _ = _make_reference_picture(
        server, str(tmp_path / "refs"), "any.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )
    # Current window is the default 30.
    for candidate in (30, 60, 90, 120):
        body = _impact(client, candidate)
        assert body == {"would_purge_count": 0, "first_purge_at": None}, (
            f"{candidate} is not lower than the current 30: {body}"
        )


def test_impact_rejects_an_unsupported_window(server):
    client = _client(server)
    for bad in (7, 0, -30, 365):
        _impact(client, bad, expect=422)


def test_impact_has_no_side_effects(server, tmp_path):
    """A preview must not apply, stamp, or destroy anything."""
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "untouched.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )
    assert (
        client.patch(
            "/server-config/scrapheap-retention",
            json={"scrapheap_retention_days": 120},
        ).status_code
        == 200
    )
    before = client.get("/server-config/scrapheap-retention").json()

    assert _impact(client, 30)["would_purge_count"] == 1

    after = client.get("/server-config/scrapheap-retention").json()
    assert after == before, f"impact mutated the config: {before} -> {after}"
    assert server.vault.scrapheap_retention_days == 120
    assert _get_picture(server, pic_id) is not None, "impact must destroy nothing"
    assert os.path.isfile(path)
    assert _ledger_flags_for(server, path) == []


# ── Item 3: the SQL deadline push-down is equivalent ──────────────────────────


def _reference_due_ids(server, now, retention_days, reduced_at, limit):
    """The PREVIOUS implementation: load every scrapheap row, filter in Python.

    Kept verbatim as the equivalence oracle for the SQL push-down. The safety
    properties of the selection are certified, so the optimisation is only
    allowed if it selects exactly the same pictures.
    """

    def _impl(session):
        no_delete_folder_ids = scrapheap_service.fetch_no_delete_folder_ids_in_session(
            session
        )
        rows = scrapheap_service.fetch_scrapheap_rows_in_session(session, None)
        locked_ids = scrapheap_service.locked_scrapheap_picture_ids_in_session(
            session, [r.id for r in rows if r.id is not None]
        )
        due = []
        for row in rows:
            if row.id is None:
                continue
            if row.is_protected(no_delete_folder_ids):
                continue
            if int(row.id) in locked_ids:
                continue
            purge_at = scrapheap_service.compute_purge_at(
                row.deleted_at, retention_days, reduced_at, is_protected=False
            )
            if purge_at is None or purge_at > scrapheap_service._as_utc(now):
                continue
            due.append(int(row.id))
            if len(due) >= limit:
                break
        return due

    return server.vault.db.run_immediate_read_task(_impl)


def test_sql_pushdown_selects_exactly_what_the_python_scan_did(server, tmp_path):
    """Equivalence over a mixed population: protected, locked, stack-frozen,
    in-window, due, and NULL deleted_at."""
    client = _client(server)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    made = {}

    def _mk(name, *, allow_delete=True, age_days=400):
        pid, _ = _make_reference_picture(
            server, str(tmp_path / name), f"{name}.png", allow_delete=allow_delete
        )
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text
        _set_deleted_at(server, pid, now - timedelta(days=age_days))
        made[name] = pid
        return pid

    _mk("due_a")
    _mk("due_b", age_days=31)
    _mk("in_window", age_days=5)
    _mk("protected", allow_delete=False)
    locked = _mk("locked")
    stack_member = _mk("stack_member")
    stack_sibling = _mk("stack_sibling")
    no_stamp = _mk("no_stamp")
    _set_deleted_at(server, made["no_stamp"], None)

    def _stack(session: Session):
        stack = PictureStack()
        session.add(stack)
        session.commit()
        session.refresh(stack)
        for pos, pid in enumerate((stack_member, stack_sibling)):
            pic = session.get(Picture, pid)
            pic.stack_id = stack.id
            pic.stack_position = pos
            session.add(pic)
        session.commit()

    server.vault.db.run_task(_stack)
    _lock_picture_in_set(server, locked)
    _lock_picture_in_set(server, stack_member)

    check_now = datetime.now(timezone.utc)
    for retention_days, reduced_at, limit in (
        (30, None, 100),
        (30, None, 1),
        (60, None, 100),
        (120, None, 100),
        (30, check_now - timedelta(days=5), 100),
        (30, check_now, 100),  # inside the grace floor: nothing is due
    ):
        expected = _reference_due_ids(
            server, check_now, retention_days, reduced_at, limit
        )
        actual = scrapheap_service.find_due_retention_picture_ids(
            server.vault, check_now, retention_days, reduced_at, limit
        )
        assert sorted(actual) == sorted(expected), (
            f"push-down diverged for days={retention_days} reduced_at={reduced_at} "
            f"limit={limit}: {actual} != {expected}"
        )
    # Sanity: the fixture really does exercise the interesting cases.
    plain = scrapheap_service.find_due_retention_picture_ids(
        server.vault, check_now, 30, None, 100
    )
    assert sorted(plain) == sorted([made["due_a"], made["due_b"]]), plain
    assert no_stamp not in plain


def test_paging_is_correct_when_many_rows_share_one_deleted_at(server, tmp_path):
    """Keyset pagination must not skip or repeat inside a bulk-delete group.

    The bulk soft-delete stamps ONE identical ``deleted_at`` across the whole
    batch, so a keyset on the timestamp alone would advance the cursor past the
    entire group after the first page and silently lose the rest.

    Driven through the impact count, which scans with ``limit=None`` and so
    honours ``_DUE_SCAN_PAGE`` directly; the sweep's own page size is
    ``max(limit * 4, _DUE_SCAN_PAGE)`` and cannot be forced small without also
    breaking the scanner's page-exhaustion check. Both use the same scanner.
    """
    client = _client(server)
    ids = [
        _make_reference_picture(
            server, str(tmp_path / f"p{i}"), f"p{i}.png", allow_delete=True
        )[0]
        for i in range(12)
    ]
    resp = client.request("DELETE", "/api/v1/pictures", json={"picture_ids": ids})
    assert resp.status_code == 200, resp.text
    shared = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400)
    for pid in ids:
        _set_deleted_at(server, pid, shared)
    assert len({_get_picture(server, pid).deleted_at for pid in ids}) == 1, (
        "the fixture must really share one timestamp"
    )
    assert (
        client.patch(
            "/server-config/scrapheap-retention",
            json={"scrapheap_retention_days": 120},
        ).status_code
        == 200
    )

    original_page = scrapheap_service._DUE_SCAN_PAGE
    try:
        # 12 rows, one shared stamp, 3 per page -> 4 pages inside one group.
        scrapheap_service._DUE_SCAN_PAGE = 3
        paged = _impact(client, 30)["would_purge_count"]
    finally:
        scrapheap_service._DUE_SCAN_PAGE = original_page
    single_page = _impact(client, 30)["would_purge_count"]

    assert paged == len(ids), (
        f"paging across a shared-timestamp group lost rows: {paged} != {len(ids)}"
    )
    assert paged == single_page, (
        f"page size changed the result: {paged} (paged) != {single_page} (one page)"
    )


def test_deadline_boundary_is_inclusive(server, tmp_path):
    """A picture exactly AT its deadline is due (`<=`, not `<`).

    Closes reviewer mutation W6: the suite otherwise never pins the boundary,
    because a real-world sweep never lands on the exact microsecond. Both sides
    of the comparison are supplied here, so it is deterministic rather than a
    race.
    """
    client = _client(server)
    pic_id, _ = _make_reference_picture(
        server, str(tmp_path / "refs"), "exact.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text

    now = datetime.now(timezone.utc).replace(microsecond=0)
    _set_deleted_at(server, pic_id, (now - timedelta(days=30)).replace(tzinfo=None))

    assert scrapheap_service.find_due_retention_picture_ids(
        server.vault, now, 30, None, 100
    ) == [pic_id], "deleted_at exactly one window ago must be DUE"

    # One microsecond short of the window: not yet.
    assert (
        scrapheap_service.find_due_retention_picture_ids(
            server.vault, now - timedelta(microseconds=1), 30, None, 100
        )
        == []
    ), "a microsecond before the deadline must not be due"


def test_sweep_skips_the_scan_entirely_inside_the_grace_floor(server, tmp_path):
    """The early return is a real short-circuit, not just a filter."""
    client = _client(server)
    pic_id, _ = _make_reference_picture(
        server, str(tmp_path / "refs"), "graced.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )
    assert (
        scrapheap_service.find_due_retention_picture_ids(
            server.vault,
            datetime.now(timezone.utc),
            30,
            datetime.now(timezone.utc),
            100,
        )
        == []
    )


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
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
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


# ── Auto-purge ships OFF until the user turns it on ───────────────────────────
# v1.8.0 introduces a timer that permanently removes files from disk. Nobody may
# get that from a setting they never chose, so the default is "Never" - on a
# fresh install AND on one upgraded from a release that had no such setting.
# Both directions are asserted: OFF must destroy nothing however old the
# scrapheap, and an explicit opt-in must still purge (a default of OFF that
# quietly became a feature that never runs would be its own regression).


def test_a_config_that_was_never_asked_reads_as_off():
    read = scrapheap_service.read_retention_days
    assert scrapheap_service.DEFAULT_RETENTION_DAYS is None, (
        "The shipped default must be Never; auto-deletion is opt-in"
    )
    assert read({}) is None, "A pristine config must not enable auto-purge"
    # A v1.7.x server-config: it predates the setting entirely.
    assert read({"host": "localhost", "port": 9537, "image_root": "/tmp/x"}) is None
    # Fail-safe: a value we cannot parse is not a licence to delete files.
    assert read({scrapheap_service.RETENTION_DAYS_KEY: "soon"}) is None
    assert read({scrapheap_service.RETENTION_DAYS_KEY: 7}) is None


def test_an_explicit_choice_is_not_overridden_by_the_new_default():
    """Only an ABSENT key means "never chosen"; a stored value is honoured.

    The two cases ARE distinguishable, which is what makes flipping the default
    safe: ``apply_retention_config`` (i.e. an explicit save) is the only writer
    of the key, so a stored ``30`` is a deliberate choice and is left alone
    rather than being silently switched off with everyone else's.
    """
    read = scrapheap_service.read_retention_days
    for choice in scrapheap_service.RETENTION_DAY_CHOICES:
        assert read({scrapheap_service.RETENTION_DAYS_KEY: choice}) == choice
    assert read({scrapheap_service.RETENTION_DAYS_KEY: None}) is None, (
        "An explicit Never stays Never"
    )


def test_a_fresh_install_purges_nothing_however_old_the_scrapheap(server, tmp_path):
    """OFF is honoured by the FINDER, not merely by the settings UI."""
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "untouched.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=9999),
    )

    server._server_config.pop(scrapheap_service.RETENTION_DAYS_KEY, None)
    assert _rewire_retention_from_config(server) is None

    # A fresh finder each time, so the cadence gate cannot be what suppresses
    # the sweep: every one of these genuinely reaches the retention check.
    for _ in range(3):
        assert _run_purge_sweep(server) is None, (
            "An install that never opted in must schedule no purge at all"
        )
    assert _get_picture(server, pic_id) is not None
    assert os.path.isfile(path), "No file may be removed from disk by default"
    assert _ledger_flags_for(server, path) == []

    body = client.get("/server-config/scrapheap-retention").json()
    assert body["scrapheap_retention_days"] is None, (
        "The UI must be told auto-empty is off, not shown a window"
    )


def test_an_upgraded_install_boots_with_auto_purge_off(tmp_path):
    """A v1.7.x install upgraded to v1.8.0 must not start deleting files.

    Boots a real Server on a server-config.json shaped the way v1.7.x wrote it
    (no ``scrapheap_retention_*`` keys at all) and asserts both halves: the
    vault the timer reads is "Never", and startup does not MATERIALISE a window
    into the file. The second half is what keeps the "absent means never asked"
    signal intact for the next boot.
    """
    config_path = tmp_path / "server-config.json"
    legacy_config = {
        "host": "localhost",
        "port": 9537,
        "log_level": "info",
        "require_ssl": False,
        "image_root": str(tmp_path / "images"),
        "disable_background_workers": True,
    }
    config_path.write_text(json.dumps(legacy_config))

    with Server(str(config_path)) as upgraded:
        assert upgraded.vault.scrapheap_retention_days is None
        assert upgraded.vault.scrapheap_retention_reduced_at is None
        assert scrapheap_service.RETENTION_DAYS_KEY not in upgraded._server_config, (
            "Startup must not invent a retention window"
        )
        finder = ScrapheapRetentionPurgeFinder(vault=upgraded.vault)
        assert finder.find_task() is None

    on_disk = json.loads(config_path.read_text())
    assert scrapheap_service.RETENTION_DAYS_KEY not in on_disk, (
        "The upgrade must leave the install un-asked, not silently opted in"
    )


def test_turning_auto_purge_on_deliberately_still_purges(server, tmp_path):
    """The positive direction: an explicit opt-in must genuinely destroy."""
    client = _client(server)
    server._server_config.pop(scrapheap_service.RETENTION_DAYS_KEY, None)
    _rewire_retention_from_config(server)

    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "optin.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )
    assert _run_purge_sweep(server) is None, "Off means off"

    resp = client.patch(
        "/server-config/scrapheap-retention", json={"scrapheap_retention_days": 30}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scrapheap_retention_days"] == 30
    # Switching it on is a lowering (Never is an infinite window), so the grace
    # floor is stamped: nothing is destroyed for a day, which is the user's
    # window to change their mind about an unattended deletion.
    assert body["scrapheap_retention_reduced_at"] is not None
    assert server.vault.scrapheap_retention_days == 30
    assert _run_purge_sweep(server) is None, (
        "Switching auto-empty on must not purge inside its grace floor"
    )
    assert _get_picture(server, pic_id) is not None

    # Past the floor the opted-in window does exactly what it promises.
    server.vault.set_scrapheap_retention(
        30, datetime.now(timezone.utc) - timedelta(days=2)
    )
    result = _run_purge_sweep(server)
    assert result == {"purged": 1, "skipped": 0, "skipped_locked": 0, "retained": 0}, (
        result
    )
    assert _get_picture(server, pic_id) is None
    assert not os.path.isfile(path)


def test_turning_auto_purge_on_reports_its_blast_radius(server, tmp_path):
    """The impact preview must be honest about the switch-on, not only about a
    shortening: enabling the timer is the change that can expose an entire
    long-lived scrapheap at once."""
    client = _client(server)
    server._server_config.pop(scrapheap_service.RETENTION_DAYS_KEY, None)
    _rewire_retention_from_config(server)

    for index in range(2):
        pic_id, _ = _make_reference_picture(
            server, str(tmp_path / f"r{index}"), f"old{index}.png", allow_delete=True
        )
        delete_resp = client.delete(f"/pictures/{pic_id}")
        assert delete_resp.status_code == 200, delete_resp.text
        _set_deleted_at(
            server,
            pic_id,
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
        )

    resp = client.get("/server-config/scrapheap-retention/impact", params={"days": 30})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["would_purge_count"] == 2, (
        "Switching auto-empty on must state what it would destroy"
    )
    assert body["first_purge_at"] is not None
    # A pure read: it must not have enabled anything.
    assert server.vault.scrapheap_retention_days is None
    assert (
        client.get("/server-config/scrapheap-retention").json()[
            "scrapheap_retention_days"
        ]
        is None
    )


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
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text
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
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text

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
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
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
    """N-1 - the listing must not advertise a deadline the sweep will not act on.

    A locked scrapheap picture past its deadline used to be served
    ``purge_at=<past>`` + ``auto_purge_exempt=False``, so the grid rendered a
    permanent, urgent "purges today" badge for a picture the sweep skips forever.
    """
    client = _client(server)
    pic_id, path = _make_reference_picture(
        server, str(tmp_path / "refs"), "lockedrow.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
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
        "A locked picture must show no countdown - the sweep will never take it"
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
    assert result == {"purged": 1, "skipped": 0, "skipped_locked": 0, "retained": 0}, (
        result
    )
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
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text
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
    set membership - the listing uses the same helper as the sweep."""
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
    # only ONE of the two - the sibling is frozen transitively.
    for pid in (member_id, sibling_id):
        delete_resp = client.delete(f"/pictures/{pid}")
        assert delete_resp.status_code == 200, delete_resp.text
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


def test_locked_lookup_survives_a_large_scrapheap_scope(server):
    """N-2 - the lock lookup works at SQLite's historical 999-var ceiling."""
    engine = server.vault.db._engine

    def _set_limit(dbapi_conn, _record):
        dbapi_conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

    event.listen(engine, "connect", _set_limit)
    engine.dispose()
    try:
        absent_ids = list(range(10_000_000, 10_001_001))
        assert (
            scrapheap_service.locked_scrapheap_picture_ids(server.vault, absent_ids)
            == set()
        )
    finally:
        event.remove(engine, "connect", _set_limit)
        engine.dispose()


def test_sweep_still_finds_due_work(server, tmp_path):
    """The lock lookup must not make the finder miss due work."""
    client = _client(server)
    pic_id, _ = _make_reference_picture(
        server, str(tmp_path / "refs"), "amongmany.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    _set_deleted_at(
        server,
        pic_id,
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )
    due = scrapheap_service.find_due_retention_picture_ids(
        server.vault, datetime.now(timezone.utc), 30, None, 100
    )
    assert due == [pic_id], f"lock lookup must not drop due candidates: {due}"


def test_listing_purge_at_is_null_when_retention_is_never(server, tmp_path):
    client = _client(server)
    pic_id, _ = _make_reference_picture(
        server, str(tmp_path / "refs"), "never.png", allow_delete=True
    )
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text
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
    delete_resp = client.delete(f"/pictures/{pic_id}")
    assert delete_resp.status_code == 200, delete_resp.text

    resp = client.get("/pictures", params={"only_deleted": "true", "fields": "grid"})
    assert resp.status_code == 200, resp.text
    rows = {row["id"]: row for row in resp.json()}
    assert "purge_at" in rows[pic_id]
    assert rows[pic_id]["auto_purge_exempt"] is False
