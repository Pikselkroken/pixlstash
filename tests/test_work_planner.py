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
