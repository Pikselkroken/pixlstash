import threading
import time
from types import SimpleNamespace

import pytest

from pixlstash.task_runner import TaskRunner
from pixlstash.tasks import TaskType
from pixlstash.tasks.base_task import BaseTask
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.vault import Vault
from pixlstash.work_planner import WorkPlanner


class _FakeTask:
    def __init__(self, task_id: str):
        self.id = task_id


class _OneShotFinder:
    def __init__(self):
        self._returned = False

    def finder_name(self) -> str:
        return "TestFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def depends_on(self) -> list[str]:
        return []

    def find_task(self):
        if self._returned:
            return None
        self._returned = True
        return _FakeTask("task-1")

    def on_task_complete(self, task, error):
        return None


class _FastCompleteRunner:
    def __init__(self):
        self.on_submit = None

    def submit(self, task):
        if callable(self.on_submit):
            self.on_submit(task)
        return task.id


def test_inflight_decrements_when_task_completes_during_submit():
    runner = _FastCompleteRunner()
    finder = _OneShotFinder()
    planner = WorkPlanner(task_runner=runner, task_finders=[finder])

    runner.on_submit = lambda task: planner.on_task_complete(task, None)

    submitted = planner._run_finders_once()

    assert submitted is True
    assert planner.inflight_count("TestFinder") == 0
    assert planner._finder_by_task_id == {}


def test_stop_while_finder_is_working_skips_submission():
    runner = _FastCompleteRunner()
    finder = _OneShotFinder()
    planner = WorkPlanner(task_runner=runner, task_finders=[finder])
    submitted_tasks = []
    runner.on_submit = submitted_tasks.append

    real_find_task = finder.find_task

    def find_then_stop():
        task = real_find_task()
        planner._stop.set()
        return task

    finder.find_task = find_then_stop

    assert planner._run_finders_once() is False
    assert submitted_tasks == []
    assert planner.inflight_count("TestFinder") == 0


def test_runner_stop_race_is_quiet_during_planner_shutdown():
    finder = _OneShotFinder()
    planner = None

    class _StoppingRunner:
        def submit(self, task):
            planner._stop.set()
            raise RuntimeError("TaskRunner test is stopped.")

    planner = WorkPlanner(task_runner=_StoppingRunner(), task_finders=[finder])

    assert planner._run_finders_once() is False
    assert planner.inflight_count("TestFinder") == 0
    assert planner._finder_by_task_id == {}


class _IdleFinder:
    """A finder that never has work, so the loop just cycles over it."""

    def __init__(self, name: str, find_delay_s: float = 0.0):
        self._name = name
        self._find_delay_s = find_delay_s
        # Interruptible delay. The wedged-thread tests use delays far longer
        # than the join they are testing, so a plain sleep() left the planner
        # thread alive for the rest of the pytest session — see _stopped().
        self._release = threading.Event()

    def finder_name(self) -> str:
        return self._name

    def max_inflight_tasks(self) -> int:
        return 1

    def depends_on(self) -> list:
        return []

    def find_task(self):
        if self._find_delay_s:
            self._release.wait(self._find_delay_s)
        return None

    def on_task_complete(self, task, error):
        return None

    def on_all_tasks_complete(self):
        return None


class _NullRunner:
    def submit(self, task):
        return getattr(task, "id", None)


def _stopped(planner):
    planner._stop.set()
    planner._wake.set()
    # Release any finder still parked in its artificial delay. The wedged
    # cases deliberately outlast stop()'s bounded join, and a 30-second sleep
    # the join cannot reach is a daemon thread that survives the whole session
    # into interpreter finalization.
    for finder in list(planner._task_finders):
        release = getattr(finder, "_release", None)
        if release is not None:
            release.set()
    if planner._thread is not None:
        planner._thread.join(timeout=10)
        assert not planner._thread.is_alive(), (
            f"WorkPlanner thread outlived the test: {planner._thread!r}"
        )


def test_finder_removed_mid_cycle_does_not_kill_the_loop():
    """The finder list may shrink while ``_run_finders_once`` is walking it.

    Module fixtures detach backfill finders from a live planner. The loop used
    to capture ``len(self._task_finders)`` and then index the live list, so a
    removal in between raised ``IndexError`` inside the planner thread, which
    died with no report — and every later import answered "Face worker is not
    running", naming a condition that was not the problem.
    """
    victim = _IdleFinder("Victim")
    planner = WorkPlanner(task_runner=_NullRunner(), task_finders=[])

    def shrink_then_report():
        planner._task_finders.remove(victim)
        return None

    shrinker = _IdleFinder("Shrinker")
    shrinker.find_task = shrink_then_report
    planner._task_finders = [shrinker, victim]

    assert planner._run_finders_once() is False


def test_planner_thread_survives_concurrent_finder_mutation(caplog):
    """The live loop and a mutating fixture must not race to an IndexError.

    Asserts on the cycle staying clean, not only on the thread staying up: the
    top-level guard in ``_run`` would otherwise absorb the IndexError and hide
    the fact that whole finder passes are being lost to it.
    """
    # The small find_task() delay is load-bearing: it releases the GIL inside
    # the cycle, which is what lets the mutating thread land between the loop's
    # length read and its index. Real finders do database work there.
    finders = [_IdleFinder(f"F{i}", find_delay_s=0.001) for i in range(6)]
    planner = WorkPlanner(task_runner=_NullRunner(), task_finders=list(finders))
    planner.MIN_INTERVAL_S = 0.0
    planner.MAX_INTERVAL_S = 0.0

    deaths = []
    real_run = planner._run

    def watched_run():
        try:
            real_run()
        except BaseException as exc:  # noqa: BLE001 - the point of the test
            deaths.append(exc)
            raise

    planner._run = watched_run
    planner.start()
    try:
        # Deliberately the unlocked in-place edit the existing module fixtures
        # perform, so the loop stays safe against callers that have not moved to
        # detach_finders() yet.
        victim = finders[-1]
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and planner.is_running():
            planner._task_finders.remove(victim)
            planner._task_finders.append(victim)
        assert planner.is_running(), f"planner thread died: {deaths}"
        assert deaths == []
        assert "WorkPlanner cycle raised" not in caplog.text, (
            "the loop raised while the finder list was being edited"
        )
    finally:
        _stopped(planner)


def test_a_raising_finder_does_not_silently_kill_the_planner(caplog):
    """A cycle that raises must be reported and the loop must keep running."""

    class _ExplodingFinder(_IdleFinder):
        def find_task(self):
            raise RuntimeError("finder blew up")

    planner = WorkPlanner(
        task_runner=_NullRunner(), task_finders=[_ExplodingFinder("Boom")]
    )
    planner.MIN_INTERVAL_S = 0.01
    planner.MAX_INTERVAL_S = 0.01
    planner.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and "finder blew up" not in caplog.text:
            time.sleep(0.05)
        assert planner.is_running(), "the planner thread died on a finder exception"
        assert "finder blew up" in caplog.text, "the failure was never reported"
    finally:
        _stopped(planner)


def test_restart_after_a_slow_stop_leaves_the_planner_alive():
    """``start()`` must not hand back a planner that is about to go dead.

    ``stop()`` joins with a bounded timeout and only logs when the thread
    outlives it. ``start()`` used to return early on ``is_alive()`` *before*
    clearing ``_stop``, so the survivor exited a moment later and left the
    planner permanently dead with ``is_running()`` having answered True at the
    instant it was asserted.
    """
    slow = _IdleFinder("Slow", find_delay_s=1.0)
    planner = WorkPlanner(task_runner=_NullRunner(), task_finders=[slow])
    planner.STOP_JOIN_TIMEOUT_S = 0.2
    planner.start()
    try:
        time.sleep(0.05)
        planner.stop()
        assert planner.is_running(), (
            "this test needs a stop() whose join times out; the finder returned "
            "too quickly to reproduce the race"
        )

        planner.STOP_JOIN_TIMEOUT_S = 10.0
        planner.start()
        assert not planner._stop.is_set()
        assert planner.is_running()

        time.sleep(1.5)
        assert planner.is_running(), (
            "the planner went dead after the outgoing thread finished winding down"
        )
    finally:
        _stopped(planner)


def test_start_refuses_to_run_two_loops_over_a_wedged_thread():
    """A finder that never returns must fail loudly, not double-schedule."""
    wedged = _IdleFinder("Wedged", find_delay_s=30.0)
    planner = WorkPlanner(task_runner=_NullRunner(), task_finders=[wedged])
    planner.STOP_JOIN_TIMEOUT_S = 0.2
    planner.start()
    try:
        time.sleep(0.05)
        planner.stop()
        with pytest.raises(RuntimeError, match="cannot restart"):
            planner.start()
    finally:
        _stopped(planner)


def test_detach_finders_prunes_every_structure_and_marks_exhausted():
    """The supported replacement for reaching into the planner's private lists."""
    face = _IdleFinder("FaceFinder")
    tagger = _IdleFinder("TagFinder")
    planner = WorkPlanner(
        task_runner=_NullRunner(),
        task_finders={
            TaskType.FACE_EXTRACTION: face,
            TaskType.TAGGER: tagger,
        },
    )

    assert planner.has_finder(TaskType.TAGGER)
    removed = planner.detach_finders([TaskType.TAGGER])

    assert removed == {"TagFinder"}
    assert planner.registered_finder_names() == {"FaceFinder"}
    assert planner._task_finders == [face]
    assert not planner.has_finder(TaskType.TAGGER)
    assert planner.has_finder(TaskType.FACE_EXTRACTION)
    # A detached finder never reports "nothing to do" again, so anything that
    # depends_on() it would block for ever unless it counts as exhausted.
    assert planner._finder_exhausted["TagFinder"] is True
    assert planner.detach_finders([TaskType.TAGGER]) == set(), "detach is idempotent"


class _ClaimedTask(BaseTask):
    """A real task carrying the ``picture_ids`` the release path reads."""

    def __init__(self, picture_ids):
        super().__init__(task_type="ClaimedTask", params={"picture_ids": picture_ids})

    def _run_task(self):
        return None


class _ClaimingFinder(BaseTaskFinder):
    """A real finder, so the claim bookkeeping under test is the real one."""

    def __init__(self, picture_ids):
        super().__init__()
        self._picture_ids = list(picture_ids)
        self.last_task = None

    def finder_name(self) -> str:
        return "ClaimingFinder"

    def find_task(self):
        candidates = [SimpleNamespace(id=pid) for pid in self._picture_ids]
        selected = self._filter_and_claim(candidates, len(candidates))
        if not selected:
            return None
        self.last_task = _ClaimedTask([picture.id for picture in selected])
        return self.last_task


def test_a_task_cancelled_off_the_queue_releases_its_claims():
    """A cancelled task must give back its slot and its picture ids.

    ``cancel_pending_tasks()`` used to drain the queues without firing the
    completion callbacks, which are the only path that discards claims and
    decrements the in-flight count. One full restore therefore pinned the
    tagger finder at max in-flight for the life of the process, starved every
    finder that ``depends_on()`` it, and left the claimed pictures permanently
    unselectable.
    """
    runner = TaskRunner(name="cancel-claims-test", num_workers=1)
    finder = _ClaimingFinder([1, 2, 3])
    planner = WorkPlanner(task_runner=runner, task_finders=[finder])
    runner.add_task_complete_callback(planner.on_task_complete)

    try:
        # The workers are deliberately not started, so the task can only leave
        # the queue through the cancel path.
        assert planner._run_finders_once() is True
        assert planner.inflight_count("ClaimingFinder") == 1
        assert finder._claimed_picture_ids == {1, 2, 3}

        assert runner.cancel_pending_tasks() == 1

        assert finder._claimed_picture_ids == set()
        assert planner.inflight_count("ClaimingFinder") == 0
        assert planner._finder_by_task_id == {}

        # And the finder can pick the same work up again.
        assert planner._run_finders_once() is True
        assert finder._claimed_picture_ids == {1, 2, 3}
    finally:
        runner.stop()


def test_stop_between_find_and_submit_releases_the_claims():
    """``find_task()`` claims before the planner decides not to submit."""
    runner = _FastCompleteRunner()
    finder = _ClaimingFinder([4, 5])
    planner = WorkPlanner(task_runner=runner, task_finders=[finder])
    submitted_tasks = []
    runner.on_submit = submitted_tasks.append

    real_find_task = finder.find_task

    def find_then_stop():
        task = real_find_task()
        planner._stop.set()
        return task

    finder.find_task = find_then_stop

    assert planner._run_finders_once() is False
    assert submitted_tasks == []
    assert planner.inflight_count("ClaimingFinder") == 0
    assert finder._claimed_picture_ids == set()


def test_a_failed_submit_releases_the_claims():
    """The submit-raised handler unwound its own bookkeeping but not the claims."""
    finder = _ClaimingFinder([6, 7])
    planner = None

    class _StoppingRunner:
        def submit(self, task):
            planner._stop.set()
            raise RuntimeError("TaskRunner test is stopped.")

    planner = WorkPlanner(task_runner=_StoppingRunner(), task_finders=[finder])

    assert planner._run_finders_once() is False
    assert planner.inflight_count("ClaimingFinder") == 0
    assert planner._finder_by_task_id == {}
    assert finder._claimed_picture_ids == set()


def test_a_completed_task_releases_its_claims_exactly_once():
    """The positive control: the happy path still releases, and only once."""
    runner = TaskRunner(name="complete-claims-test", num_workers=1)
    finder = _ClaimingFinder([8, 9])
    releases = []
    real_on_task_complete = finder.on_task_complete

    def counting_on_task_complete(task, error):
        releases.append((task.id, error))
        real_on_task_complete(task, error)

    finder.on_task_complete = counting_on_task_complete
    planner = WorkPlanner(task_runner=runner, task_finders=[finder])
    runner.add_task_complete_callback(planner.on_task_complete)
    runner.start()
    try:
        assert planner._run_finders_once() is True
        task = finder.last_task
        assert task._done_event.wait(timeout=10.0), "the task never completed"
        # The callbacks fire after the worker sets the done event.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not releases:
            time.sleep(0.01)

        assert releases == [(task.id, None)], (
            f"expected exactly one release with no error, got {releases}"
        )
        assert finder._claimed_picture_ids == set()
        assert planner.inflight_count("ClaimingFinder") == 0
    finally:
        runner.stop()

    assert releases == [(task.id, None)], "stop() released the task a second time"


class _VaultWorkerProbe:
    """Just the two Vault methods under test, over a planner we control.

    Building a real ``Vault`` costs a Server; these two methods only read the
    planner, so binding them to a stub keeps the check in this fast module.
    """

    is_worker_running = Vault.is_worker_running
    worker_unavailable_reason = Vault.worker_unavailable_reason

    def __init__(self, work_planner):
        self._work_planner = work_planner


def test_is_worker_running_answers_per_worker_type():
    """It used to ignore its argument, so a detached finder read as healthy."""
    planner = WorkPlanner(
        task_runner=_NullRunner(),
        task_finders={
            TaskType.FACE_EXTRACTION: _IdleFinder("FaceFinder"),
            TaskType.TAGGER: _IdleFinder("TagFinder"),
        },
    )
    vault = _VaultWorkerProbe(planner)
    planner.start()
    try:
        assert vault.is_worker_running(TaskType.FACE_EXTRACTION)
        assert vault.worker_unavailable_reason(TaskType.FACE_EXTRACTION) is None

        planner.detach_finders([TaskType.TAGGER])
        # The planner is still alive, so the old implementation said yes here.
        assert not vault.is_worker_running(TaskType.TAGGER)
        assert "TAGGER" in str(vault.worker_unavailable_reason(TaskType.TAGGER))
        assert vault.is_worker_running(TaskType.FACE_EXTRACTION), (
            "detaching one finder must not refuse the others"
        )
    finally:
        _stopped(planner)

    # A dead planner and a detached finder must not read the same any more.
    assert not vault.is_worker_running(TaskType.FACE_EXTRACTION)
    assert "not alive" in vault.worker_unavailable_reason(TaskType.FACE_EXTRACTION)


class _StubDatabase:
    """A database whose reads run inline against a real SQLite vault."""

    def __init__(self, vault_path: str, image_root: str):
        self._vault_path = vault_path
        self.image_root = image_root

    def run_immediate_read_task(self, func, *args, **kwargs):
        from sqlmodel import Session, create_engine

        engine = create_engine(f"sqlite:///{self._vault_path}")
        try:
            with Session(engine) as session:
                return func(session, *args, **kwargs)
        finally:
            engine.dispose()


def _vault_with_snapshots(tmp_path, rows):
    """Build a vault whose ``snapshot`` table holds *rows* of (id, scrubbed)."""
    import sqlite3

    vault = tmp_path / "vault.db"
    conn = sqlite3.connect(vault)
    conn.execute(
        "CREATE TABLE snapshot (id INTEGER PRIMARY KEY, kind TEXT, "
        "created_at TIMESTAMP, relative_path TEXT, manifest_relative_path TEXT, "
        "byte_size INTEGER, picture_count INTEGER, schema_version TEXT, "
        "label TEXT, identity_scrubbed_at TIMESTAMP)"
    )
    for snapshot_id, scrubbed in rows:
        conn.execute(
            "INSERT INTO snapshot (id, kind, created_at, relative_path, "
            "manifest_relative_path, byte_size, picture_count, schema_version, "
            "identity_scrubbed_at) VALUES (?, 'MANUAL', '2026-01-01', ?, ?, 0, 0, "
            "'x', ?)",
            (
                snapshot_id,
                f"snapshots/{snapshot_id}.sqlite",
                f"snapshots/{snapshot_id}.manifest.json",
                "2026-01-01" if scrubbed else None,
            ),
        )
    conn.commit()
    conn.close()
    return str(vault)


def test_snapshot_identity_finder_claims_only_unscrubbed_archives(tmp_path):
    """NULL means legacy. A stamped archive must never be rewritten again."""
    from pixlstash.tasks.missing_snapshot_identity_scrub_finder import (
        MissingSnapshotIdentityScrubFinder,
    )

    vault = _vault_with_snapshots(tmp_path, [(1, True), (2, False), (3, True)])
    finder = MissingSnapshotIdentityScrubFinder(
        database=_StubDatabase(vault, str(tmp_path))
    )

    task = finder.find_task()
    assert task is not None
    assert task.params["snapshot_id"] == 2, "only the NULL row is owed work"


def test_snapshot_identity_finder_stops_asking_once_drained(tmp_path):
    """Every later snapshot is stamped at creation, so empty stays empty."""
    from pixlstash.tasks.missing_snapshot_identity_scrub_finder import (
        MissingSnapshotIdentityScrubFinder,
    )

    vault = _vault_with_snapshots(tmp_path, [(1, True)])
    database = _StubDatabase(vault, str(tmp_path))
    reads = []
    original = database.run_immediate_read_task

    def counting(func, *args, **kwargs):
        reads.append(func)
        return original(func, *args, **kwargs)

    database.run_immediate_read_task = counting
    finder = MissingSnapshotIdentityScrubFinder(database=database)

    assert finder.find_task() is None
    assert finder.find_task() is None
    assert len(reads) == 1, "a drained finder must not re-query every cycle"


def test_snapshot_identity_finder_stops_reissuing_a_failing_archive(tmp_path):
    """One unscrubbable archive must not become a spin loop.

    Observed on real data: a snapshot row whose archive was gone from disk
    failed instantly, stayed NULL so it would be retried, and was handed straight
    back to the planner, producing hundreds of failing tasks a minute. The row
    must stay NULL (the work is not silently forgotten) while the finder moves
    on to the archives it can still make progress on.
    """
    from pixlstash.tasks.missing_snapshot_identity_scrub_finder import (
        MissingSnapshotIdentityScrubFinder,
    )

    vault = _vault_with_snapshots(tmp_path, [(1, False), (2, False)])
    finder = MissingSnapshotIdentityScrubFinder(
        database=_StubDatabase(vault, str(tmp_path))
    )

    first = finder.find_task()
    assert first.params["snapshot_id"] == 1
    finder.on_task_complete(first, RuntimeError("cannot scrub this one"))

    second = finder.find_task()
    assert second is not None
    assert second.params["snapshot_id"] == 2, (
        "the failing archive must not be reissued ahead of the workable one"
    )
    finder.on_task_complete(second, None)


def test_snapshot_identity_task_marks_a_missing_archive_done(tmp_path):
    """A dangling registration has no bytes at rest, so nothing can leak."""
    import sqlite3

    from pixlstash.tasks.snapshot_identity_scrub_task import SnapshotIdentityScrubTask

    vault = _vault_with_snapshots(tmp_path, [(1, False)])

    class _WritableStub(_StubDatabase):
        def run_task(self, func, *args, **kwargs):
            from sqlmodel import Session, create_engine

            engine = create_engine(f"sqlite:///{self._vault_path}")
            try:
                with Session(engine) as session:
                    return func(session, *args, **kwargs)
            finally:
                engine.dispose()

    task = SnapshotIdentityScrubTask(
        database=_WritableStub(vault, str(tmp_path)),
        snapshot_id=1,
        relative_path="snapshots/does-not-exist.sqlite",
    )
    task._run_task()

    conn = sqlite3.connect(vault)
    marked = conn.execute(
        "SELECT identity_scrubbed_at FROM snapshot WHERE id=1"
    ).fetchone()[0]
    conn.close()
    assert marked is not None, "a missing archive must not be retried forever"


def test_snapshot_scrub_progress_is_counted_in_archives_not_pictures(tmp_path):
    """The worker panel must show the real backlog, not a picture count.

    Falling through to the generic ``planner_managed`` branch reports the
    library's picture total with nothing remaining, i.e. "N / N, 0 left" while
    the scrub still has archives to rewrite. This is a one-time migration the
    user is waiting to finish, so a bar that claims completion is worse than no
    bar at all.
    """
    from sqlmodel import Session, create_engine

    from pixlstash.vault import Vault

    vault = _vault_with_snapshots(
        tmp_path, [(1, True), (2, False), (3, False), (4, True), (5, False)]
    )
    engine = create_engine(f"sqlite:///{vault}")
    try:
        with Session(engine) as session:
            total = Vault._count_total_snapshots(session)
            remaining = Vault._count_unscrubbed_snapshots(session)
    finally:
        engine.dispose()

    assert (total, remaining) == (5, 3)
    assert max(total - remaining, 0) == 2, "two archives are genuinely done"
