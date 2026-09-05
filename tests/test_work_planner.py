import logging
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from pixlstash.task_runner import TaskRunner
from pixlstash.tasks import TaskType, smart_score_task
from pixlstash.tasks.base_task import BaseTask
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.tasks.image_embedding_task import ImageEmbeddingTask
from pixlstash.tasks.smart_score_task import SmartScoreTask
from pixlstash.tasks.tag_task import TagTask
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
        # thread alive for the rest of the pytest session - see _stopped().
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
    died with no report - and every later import answered "Face worker is not
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
    """A raising finder must be reported, and cost only its own turn.

    Three finders, not one: a single-finder planner cannot see the real damage.
    ``_finder_order_idx`` advances only on a successful submit or in the
    ``not submitted_any`` tail, so catching the exception around the whole cycle
    parked the order index on the raiser and every finder behind it stopped
    being swept for the life of the process, while ``is_running()`` went on
    answering True.
    """
    swept = []

    class _ExplodingFinder(_IdleFinder):
        def find_task(self):
            swept.append(self.finder_name())
            raise RuntimeError("finder blew up")

    class _SweptFinder(_IdleFinder):
        def find_task(self):
            swept.append(self.finder_name())
            return None

    planner = WorkPlanner(
        task_runner=_NullRunner(),
        task_finders=[
            _ExplodingFinder("Boom"),
            _SweptFinder("Behind1"),
            _SweptFinder("Behind2"),
        ],
    )
    planner.MIN_INTERVAL_S = 0.01
    planner.MAX_INTERVAL_S = 0.01
    planner.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not {"Behind1", "Behind2"} <= set(swept):
            time.sleep(0.05)
        assert planner.is_running(), "the planner thread died on a finder exception"
        assert "finder blew up" in caplog.text, "the failure was never reported"
        assert {"Behind1", "Behind2"} <= set(swept), (
            f"the finders behind the raiser were never swept: {sorted(set(swept))}"
        )
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


def test_a_dependent_finder_unblocks_when_its_blocker_is_detached():
    """The outcome the exhausted-marking promises, not just the flag being written.

    ``detach_finders`` used to pop the TaskType to name mapping before writing
    ``_finder_exhausted[name]``, so ``depends_on()`` resolution fell through to
    its ``str(task_type)`` default, never found the flag, and blocked the
    dependent finder for ever.
    """

    class _DependentFinder(_IdleFinder):
        def depends_on(self) -> list:
            return [TaskType.TAGGER]

    swept = []
    tagger = _IdleFinder("TagFinder")
    dependent = _DependentFinder("DependentFinder")
    dependent.find_task = lambda: swept.append("DependentFinder") and None

    planner = WorkPlanner(
        task_runner=_NullRunner(),
        task_finders={
            TaskType.TAGGER: tagger,
            TaskType.DESCRIPTION: dependent,
        },
    )

    # A blocker with work in flight blocks its dependent.
    planner._inflight_by_finder["TagFinder"] = 1
    assert planner._run_finders_once() is False
    assert swept == [], "the dependent ran while its blocker still had work"

    assert planner.detach_finders([TaskType.TAGGER]) == {"TagFinder"}
    planner._inflight_by_finder["TagFinder"] = 0

    assert planner._run_finders_once() is False
    assert swept == ["DependentFinder"], (
        "the dependent stayed blocked on a finder that will never report again"
    )


class _TwoSlotOneTaskFinder(_OneShotFinder):
    """One task, two in-flight slots, so one cycle both submits and runs dry."""

    def max_inflight_tasks(self) -> int:
        return 2


def test_the_drain_fires_when_the_last_task_finishes_before_the_finder_runs_dry():
    """``on_all_tasks_complete()`` must not depend on which edge comes first.

    ``exhausted`` is set only when ``find_task()`` returns None, which is after
    the in-flight count reaches zero whenever the last task completes quickly.
    The completion edge then computed ``all_done`` as ``(0 == 0) and False`` and
    the drain was missed entirely, so the tagger's CUDA arena stayed held.
    """
    runner = _FastCompleteRunner()
    finder = _OneShotFinder()
    drained = []
    finder.on_all_tasks_complete = lambda: drained.append("drained")
    planner = WorkPlanner(task_runner=runner, task_finders=[finder])
    # Complete the task inside submit(), so in-flight hits zero while the finder
    # has not yet reported that it is out of work.
    runner.on_submit = lambda task: planner.on_task_complete(task, None)

    assert planner._run_finders_once() is True
    assert drained == ["drained"], "the drain was missed on this edge ordering"

    assert planner._run_finders_once() is False
    assert drained == ["drained"], "the drain fired more than once per burst"


def test_the_drain_still_fires_on_the_last_task_completing():
    """Positive control for the other edge ordering, which already worked."""
    runner = _FastCompleteRunner()
    finder = _TwoSlotOneTaskFinder()
    drained = []
    finder.on_all_tasks_complete = lambda: drained.append("drained")
    planner = WorkPlanner(task_runner=runner, task_finders=[finder])
    submitted_tasks = []
    runner.on_submit = submitted_tasks.append

    # The spare slot lets the same cycle submit the one task and then see the
    # finder run dry, so exhausted is set while the task is still in flight.
    assert planner._run_finders_once() is True
    assert drained == [], "the drain fired while a task was still in flight"

    planner.on_task_complete(submitted_tasks[0], None)
    assert drained == ["drained"], "the drain was missed on the completion edge"


def test_the_drain_is_skipped_when_a_new_task_lands_while_the_claims_release():
    """Releasing the claims is what lets the loop thread take the next task.

    ``all_done`` was computed at the top of ``on_task_complete``, the lock was
    dropped, the finder released its claims, and only then was
    ``on_all_tasks_complete()`` called. For MissingTagFinder that is a GPU
    session teardown, and it landed on the task the loop thread had taken in
    between.
    """
    runner = _FastCompleteRunner()
    finder = _TwoSlotOneTaskFinder()
    drained = []
    finder.on_all_tasks_complete = lambda: drained.append("drained")
    planner = WorkPlanner(task_runner=runner, task_finders=[finder])
    submitted_tasks = []
    runner.on_submit = submitted_tasks.append

    assert planner._run_finders_once() is True
    assert drained == []

    def take_a_new_task_while_releasing(task, error):
        # Exactly the bookkeeping the loop thread does when it submits, run in
        # the window the re-validation exists to cover.
        with planner._lock:
            planner._inflight_by_finder["TestFinder"] = 1
            planner._finder_exhausted["TestFinder"] = False
            planner._all_done_pending["TestFinder"] = True

    finder.on_task_complete = take_a_new_task_while_releasing

    planner.on_task_complete(submitted_tasks[0], None)
    assert drained == [], "the drain tore the engine down under a live task"


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


def test_snapshot_identity_finder_reissues_an_archive_that_was_only_cancelled(tmp_path):
    """A cancelled scrub never ran, so the archive must stay eligible.

    Vault.stop() stops the planner and drains the queues, so both cancel paths
    are ordinary shutdown, not a rare race. Treating them as a scrub failure
    skipped the archive for the rest of the process and logged that it could not
    be scrubbed, which is a lie about data still carrying portable identity.
    """
    from pixlstash.task_runner import TaskCancelledError
    from pixlstash.tasks.missing_snapshot_identity_scrub_finder import (
        MissingSnapshotIdentityScrubFinder,
    )

    vault = _vault_with_snapshots(tmp_path, [(1, False)])
    finder = MissingSnapshotIdentityScrubFinder(
        database=_StubDatabase(vault, str(tmp_path))
    )

    first = finder.find_task()
    assert first.params["snapshot_id"] == 1
    finder.on_task_complete(first, TaskCancelledError("drained from the queue"))

    assert finder._failed == set(), "a task that never ran was recorded as failed"
    again = finder.find_task()
    assert again is not None
    assert again.params["snapshot_id"] == 1


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


class _SubmitRaisesRunner:
    """A runner whose ``submit()`` always fails, with the planner still live."""

    def submit(self, task):
        raise RuntimeError("runner refused the task")


def test_a_failed_submit_does_not_arm_the_drain_for_work_that_never_ran():
    """`on_all_tasks_complete()` is a burst-ended signal, not a cycle-ended one.

    `_all_done_pending` is armed *before* `submit()`, because a runner that
    completes synchronously calls back before `submit()` returns. That arming
    has to be undone when the submit fails: nothing ran, so the burst never
    earned its callback. Left armed, the flag is claimed by the next
    `find_task() -> None` - the finder reports no work, in-flight is zero,
    exhausted is true - and the drain fires for a task that was never submitted.
    For the tagger that means tearing down a CUDA arena that was never built.
    """
    finder = _OneShotFinder()
    drained = []
    finder.on_all_tasks_complete = lambda: drained.append("drained")
    planner = WorkPlanner(task_runner=_SubmitRaisesRunner(), task_finders=[finder])

    # The sweep catches the finder's failure and carries on -- that is this
    # PR's subject -- so nothing propagates here.
    planner._run_finders_once()

    assert planner._all_done_pending.get("TestFinder", False) is False, (
        "a failed submit left the drain armed"
    )

    # The next sweep finds no work. Nothing was ever submitted, so nothing is
    # owed a completion callback.
    assert planner._run_finders_once() is False
    assert drained == [], f"drain fired for work that never ran: {drained}"


# --- Throughput plan step 0: the pass and batch timing lines -----------------


def _one_line(caplog, marker: str) -> str:
    lines = [r.getMessage() for r in caplog.records if marker in r.getMessage()]
    assert len(lines) == 1, f"expected exactly one {marker} line, got {lines}"
    return lines[0]


class _CountedTask:
    def __init__(self, task_id: str, picture_ids: list):
        self.id = task_id
        self.params = {"picture_ids": list(picture_ids)}


class _TwoTaskFinder(_OneShotFinder):
    """Two tasks of three pictures each, then dry."""

    def __init__(self):
        self._tasks = [_CountedTask("t1", [1, 2, 3]), _CountedTask("t2", [4, 5, 6])]

    def find_task(self):
        return self._tasks.pop(0) if self._tasks else None

    def on_all_tasks_complete(self):
        return None


def test_a_finder_burst_emits_exactly_one_pipeline_pass_line(caplog):
    runner = _FastCompleteRunner()
    finder = _TwoTaskFinder()
    planner = WorkPlanner(task_runner=runner, task_finders=[finder])
    runner.on_submit = lambda task: planner.on_task_complete(task, None)

    with caplog.at_level(logging.INFO):
        # Both tasks submit, complete and run the finder dry in one cycle; the
        # second cycle finds nothing and must not report the burst again.
        assert planner._run_finders_once() is True
        assert planner._run_finders_once() is False

    line = _one_line(caplog, "[PIPELINE_PASS]")
    assert "finder=TestFinder pictures=6 tasks=2" in line
    for field in ("wall_s=", "img_per_s=", "gpu_busy="):
        assert field in line, line
    assert planner._pass_stats == {}, "the burst's accounting outlived its drain"


class _FakeClipWorkflow:
    device = "cpu"

    def is_ready(self) -> bool:
        return True

    def ensure_ready(self) -> None:
        return None

    def encode_images(self, images):
        return np.ones((len(images), 4), dtype=np.float32)


class _EmbedDb:
    """Persistence is not under test: answer with the ids the task handed over."""

    image_root = ""

    def run_task(self, fn, updates, **kwargs):
        return [update[0] for update in updates]


def test_embed_timing_line_carries_every_field(caplog):
    task = ImageEmbeddingTask(
        database=_EmbedDb(),
        clip_workflow=_FakeClipWorkflow(),
        batch=[(1, "a.png"), (2, "b.png")],
    )
    img = Image.new("RGB", (16, 16))
    with caplog.at_level(logging.DEBUG):
        task._process_preloaded([(1, "a.png", img), (2, "b.png", img)])

    line = _one_line(caplog, "[EMBED_TIMING]")
    for field in (
        f"task_id={task.id}",
        "n=2",
        "device=cpu",
        "preload_wait_s=",
        "inference_s=",
        "db_s=",
        "total_s=",
        "throughput=",
    ):
        assert field in line, line


class _SmartScoreDb:
    def __init__(self):
        vec = np.ones(4, dtype=np.float32).tobytes()
        self._anchors = [SimpleNamespace(image_embedding=vec, score=1.0)]
        self._candidates = [SimpleNamespace(id=1, image_embedding=vec)]

    def run_immediate_read_task(self, fn, *args, **kwargs):
        return self._anchors, self._anchors, self._candidates, None, {}

    def run_task(self, fn, id_to_score, before_signature, **kwargs):
        return list(id_to_score)


def test_smart_score_timing_line_carries_every_field(caplog, monkeypatch):
    # Both resolvers read the tagger and the hub; neither is what is timed.
    monkeypatch.setattr(
        smart_score_task, "resolve_anomaly_apply_thresholds", lambda vault: {}
    )
    monkeypatch.setattr(
        smart_score_task, "resolve_penalised_tag_weights", lambda auth: {}
    )
    vault = SimpleNamespace(db=_SmartScoreDb(), auth_service=None)
    task = SmartScoreTask(vault, [SimpleNamespace(id=1)])

    with caplog.at_level(logging.DEBUG):
        assert task._run_task()["changed_count"] == 1

    line = _one_line(caplog, "[SMART_SCORE_TIMING]")
    for field in (
        f"task_id={task.id}",
        "n=1",
        "device=cpu",
        "preload_wait_s=",
        "fetch_s=",
        "inference_s=",
        "db_s=",
        "total_s=",
        "throughput=",
    ):
        assert field in line, line


class _FakeTaggingWorkflow:
    is_pixlstash_tagger_enabled = False
    _engine = SimpleNamespace(device="cpu")

    def active_plugin_name(self, engine_override=None):
        return engine_override or "wd14"

    def ensure_active_plugin_ready(self, engine_override=None):
        return None

    def tag_images(self, image_paths, **kwargs):
        return {path: ["tag"] for path in image_paths}

    def pixlstash_tagger_image_size_quality_crop(self) -> int:
        return 32

    def tag_quality_crops(self, items, **kwargs):
        return {}


class _TagDb:
    def __init__(self, image_root: str):
        self.image_root = image_root

    def run_immediate_read_task(self, fn, *args, **kwargs):
        return {}  # no faces: the crop pass takes the centre-crop fallback

    def run_task(self, fn, payload, **kwargs):
        # `_add_tags_bulk` gets payload dicts and answers with the ids it
        # wrote; `_resolve_pending_predictions` gets bare ids and answers
        # nothing.
        if payload and isinstance(payload[0], dict):
            return [item["pic_id"] for item in payload]
        return None


def test_tag_timing_line_splits_the_models_and_the_crop_build(caplog, tmp_path):
    png = tmp_path / "a.png"
    Image.new("RGB", (64, 64)).save(png)
    task = TagTask(
        database=_TagDb(str(tmp_path)),
        tagging_workflow=_FakeTaggingWorkflow(),
        pictures=[SimpleNamespace(id=1, file_path=str(png))],
        engine_override="wd14",
    )

    with caplog.at_level(logging.INFO):
        task._tag_pictures_batch()

    line = _one_line(caplog, "[TAG_TIMING] task_id=")
    for field in (
        "n=1",
        "device=cpu",
        "preload_wait_s=",
        "full_pass=wd14",
        "inference_s=",
        "wd14_s=",
        "pixlstash_tagger_s=0.000",
        "crop_fetch_s=",
        "crop_build_s=",
        "crop_inference_s=",
        "total_s=",
        "wall_throughput=",
    ):
        assert field in line, line
