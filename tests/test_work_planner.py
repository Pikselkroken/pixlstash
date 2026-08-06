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
