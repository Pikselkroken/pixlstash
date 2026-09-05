"""A cancelled description batch must not blank the pictures it skipped.

Cancelling mid-batch makes the workflow return early with a partial result. If
``DescriptionTask`` then writes ``""`` to every picture the workflow did not
caption, those pictures are permanently uncaptionable: ``MissingDescriptionFinder``
selects only ``NULL`` or a ``__description::`` sentinel, and an empty string is
neither. So a cancel must persist nothing for the pictures it skipped, while a
genuine failure must still blank - that is what stops an uncaptionable picture
being retried for ever.

Three tests cover the routing rather than the writes: the cancel event is the
task's own (the workflow object running a batch is not always the one the task
was constructed with), it is checked per picture in the Florence video loop, and
``BaseTask.run`` refuses a task cancelled before it started while still reporting
COMPLETED for one that returned normally.

Deliberately Server-free. An in-memory SQLite engine and a two-method stub are
everything ``DescriptionTask`` and the finder's query touch, so this file builds
no environment (CLAUDE.md, "Tests: reuse the environment, don't rebuild it") and
costs milliseconds rather than the ~1.35 s a ``Server`` does.
"""

import threading
import types

from sqlmodel import Session, SQLModel, create_engine

from pixlstash.db_models import Picture
from pixlstash.inference.workflows.description import DescriptionWorkflow
from pixlstash.tasks.base_task import BaseTask, TaskStatus
from pixlstash.tasks.description_task import DescriptionTask
from pixlstash.tasks.missing_description_finder import MissingDescriptionFinder


class _StubDB:
    """The two VaultDatabase entry points ``DescriptionTask`` uses."""

    def __init__(self):
        self._engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self._engine)

    def run_task(self, func, *args, priority=None, **kwargs):
        with Session(self._engine) as session:
            return func(session, *args, **kwargs)

    def run_immediate_read_task(self, func, *args, **kwargs):
        return self.run_task(func, *args, **kwargs)


def _seed(db, count):
    def insert(session):
        ids = []
        for n in range(count):
            pic = Picture(
                file_path=f"/nonexistent/cancel-{n}.png",
                format="png",
                width=8,
                height=8,
                pixel_sha=f"cancel-{n}",
            )
            session.add(pic)
            session.flush()
            ids.append(int(pic.id))
        session.commit()
        return ids

    return db.run_task(insert)


class _CancelledMidBatchWorkflow:
    """Captions the first picture, then takes the cancel the runner would send."""

    def __init__(self):
        self.task = None
        self.stop_event = None

    def generate_batch(self, pictures, engine_override=None, stop_event=None):
        first = pictures[0]
        self.task.on_cancel()  # what TaskRunner.stop() does to an active task
        self.stop_event = stop_event
        return {first.id: "captioned-before-the-cancel"}

    def estimate_vram_mb(self, image_count, plugin_name=None):
        return 0


def _run_cancelled_batch(db, picture_ids):
    workflow = _CancelledMidBatchWorkflow()
    pics = [types.SimpleNamespace(id=pid, description=None) for pid in picture_ids]
    task = DescriptionTask(db, workflow, pics)
    workflow.task = task
    task._run_task()
    return task, workflow


def _descriptions(db, picture_ids):
    return db.run_immediate_read_task(
        lambda s: [s.get(Picture, pid).description for pid in picture_ids]
    )


def _selectable(db):
    return {
        p.id
        for p in db.run_immediate_read_task(
            lambda s: MissingDescriptionFinder._fetch_missing_descriptions(s, 100)
        )
    }


def test_cancelled_batch_leaves_skipped_pictures_uncaptioned_not_blank():
    """The blocker: a picture the cancel skipped keeps a NULL description, so the
    finder picks it up again. An empty string is a permanent, silent exclusion."""
    db = _StubDB()
    captioned, skipped = _seed(db, 2)

    task, workflow = _run_cancelled_batch(db, [captioned, skipped])
    got_captioned, got_skipped = _descriptions(db, [captioned, skipped])

    assert workflow.stop_event is task._cancel_event, (
        "the task must hand the workflow its own cancel event; a workflow that "
        "owns the event is the wrong object under CPU spillover, which builds a "
        "fresh DescriptionWorkflow on every access"
    )

    assert got_captioned == "captioned-before-the-cancel"
    assert got_skipped is None, (
        f"the cancelled batch wrote {got_skipped!r} to a picture it never "
        "captioned; MissingDescriptionFinder selects only NULL or a "
        "__description:: sentinel, so that picture can never be captioned again"
    )

    selectable = _selectable(db)
    assert skipped in selectable, "skipped picture must be re-queued"
    assert captioned not in selectable, "captioned picture must not be re-queued"


def test_cancelled_batch_does_not_destroy_a_pending_recaption_request():
    """A ``__description::`` sentinel is a user asking for a re-caption. A cancel
    must not consume the request by overwriting it with an empty string."""
    db = _StubDB()
    (pending,) = _seed(db, 1)

    def set_sentinel(session):
        pic = session.get(Picture, pending)
        pic.description = "__description::joycaption"
        session.add(pic)
        session.commit()

    db.run_task(set_sentinel)
    # One picture, and the workflow cancels after captioning pictures[0] - so the
    # batch that carries only this picture returns a caption for it. Give it a
    # second, unrelated picture so this one is the skipped half.
    (filler,) = _seed(db, 1)
    _run_cancelled_batch(db, [filler, pending])

    (got,) = _descriptions(db, [pending])
    assert got == "__description::joycaption", (
        f"the cancel overwrote a pending re-caption request with {got!r}"
    )
    assert pending in _selectable(db)


def test_a_genuine_failure_still_clears_the_description():
    """Guard against over-correcting. Blanking on a real failure is deliberate -
    it is what stops a picture the model cannot caption being retried for ever -
    so the fix must key on cancellation, not on 'no caption came back'."""
    db = _StubDB()
    (failed,) = _seed(db, 1)

    class _FailingWorkflow:
        def generate_batch(self, pictures, engine_override=None, stop_event=None):
            return {}

        def estimate_vram_mb(self, image_count, plugin_name=None):
            return 0

    pics = [types.SimpleNamespace(id=failed, description=None)]
    DescriptionTask(db, _FailingWorkflow(), pics)._run_task()

    (got,) = _descriptions(db, [failed])
    assert got == ""
    assert failed not in _selectable(db)


def test_a_cancel_stops_the_florence_video_loop_before_the_next_video():
    """Videos are captioned one at a time in ``_generate_batch_florence``'s first
    loop, and a video is the slowest single item there is - so the check has to
    be in that loop, not only on the still-image chunks after it."""
    stop = threading.Event()
    captioned: list[str] = []

    def generate_caption(path, _retry_on_cpu=True):
        captioned.append(path)
        stop.set()  # the cancel lands while the first video is being captioned
        return "first-video"

    engine = types.SimpleNamespace(
        tagger_settings={"active_description_plugin": "florence2"},
        ensure_captioning_ready=lambda: None,
        florence_service=types.SimpleNamespace(
            generate_caption=generate_caption,
            description_batch_size=lambda: 8,
            generate_captions_batch=lambda paths, stop_event=None: {},
        ),
    )
    pictures = [
        types.SimpleNamespace(id=1, file_path="/nonexistent/one.mp4"),
        types.SimpleNamespace(id=2, file_path="/nonexistent/two.mp4"),
    ]

    results = DescriptionWorkflow(engine, image_root=None).generate_batch(
        pictures, stop_event=stop
    )

    assert captioned == ["/nonexistent/one.mp4"], (
        "the cancelled batch captioned a second video: the stop event is not "
        "checked in the per-picture loop"
    )
    assert 2 not in results


def test_run_refuses_a_task_cancelled_before_it_started():
    """``BaseTask.on_cancel`` sets the event and ``run()`` reads it before the
    first attempt, so a task cancelled while queued and then picked up by a
    worker does no work at all."""

    class _CountingTask(BaseTask):
        def __init__(self):
            super().__init__(task_type="CountingTask")
            self.attempts = 0

        def _run_task(self):
            self.attempts += 1
            return "did the work"

    task = _CountingTask()
    task.on_cancel()

    assert task.run() is None
    assert task.attempts == 0
    assert task.status == TaskStatus.CANCELLED

    # And a requeue is a fresh run: on_queued clears the event, or the task
    # would refuse its work for the rest of the process.
    task.on_queued()
    assert task.run() == "did the work"
    assert task.attempts == 1
    assert task.status == TaskStatus.COMPLETED


def test_a_cancel_landing_after_the_work_still_reports_completed():
    """The cancel event is set by the shutdown thread and can land at any
    instant, including after ``_run_task`` has committed its rows. Reporting
    CANCELLED there would race a finished task - and ``Vault._on_task_complete``
    fires only for COMPLETED, so it would swallow the notification for work that
    is already in the database."""

    class _SelfCancellingTask(BaseTask):
        def _run_task(self):
            self.on_cancel()  # what TaskRunner.stop() does mid-flight
            return "committed before the cancel"

    task = _SelfCancellingTask(task_type="SelfCancellingTask")

    assert task.run() == "committed before the cancel"
    assert task.status == TaskStatus.COMPLETED
