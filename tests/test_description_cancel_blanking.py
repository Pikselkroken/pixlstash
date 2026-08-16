"""A cancelled description batch must not blank the pictures it skipped.

Cancelling mid-batch makes the workflow return early with a partial result.
``DescriptionTask`` then writes ``""`` to every picture the workflow did not
caption, and ``MissingDescriptionFinder`` selects only ``NULL`` or a
``__description::`` sentinel — so those pictures are permanently uncaptionable.

Two of these three tests fail as things stand; they are the specification for
the fix, not a description of current behaviour.

Deliberately Server-free. An in-memory SQLite engine and a two-method stub are
everything ``DescriptionTask`` and the finder's query touch, so this file builds
no environment (CLAUDE.md, "Tests: reuse the environment, don't rebuild it") and
costs milliseconds rather than the ~1.35 s a ``Server`` does.
"""

import types

from sqlmodel import Session, SQLModel, create_engine

from pixlstash.db_models import Picture
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

    def generate_batch(self, pictures, engine_override=None):
        first = pictures[0]
        self.task.on_cancel()  # what TaskRunner.stop() does to an active task
        return {first.id: "captioned-before-the-cancel"}

    def estimate_vram_mb(self, image_count):
        return 0

    def on_cancel(self):
        return None


def _run_cancelled_batch(db, picture_ids):
    workflow = _CancelledMidBatchWorkflow()
    pics = [types.SimpleNamespace(id=pid, description=None) for pid in picture_ids]
    task = DescriptionTask(db, workflow, pics)
    workflow.task = task
    task._run_task()
    return task


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

    _run_cancelled_batch(db, [captioned, skipped])
    got_captioned, got_skipped = _descriptions(db, [captioned, skipped])

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
    # One picture, and the workflow cancels after captioning pictures[0] — so the
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
    """Guard against over-correcting. Blanking on a real failure is deliberate —
    it is what stops a picture the model cannot caption being retried for ever —
    so the fix must key on cancellation, not on 'no caption came back'."""
    db = _StubDB()
    (failed,) = _seed(db, 1)

    class _FailingWorkflow:
        def generate_batch(self, pictures, engine_override=None):
            return {}

        def estimate_vram_mb(self, image_count):
            return 0

        def on_cancel(self):
            return None

    pics = [types.SimpleNamespace(id=failed, description=None)]
    DescriptionTask(db, _FailingWorkflow(), pics)._run_task()

    (got,) = _descriptions(db, [failed])
    assert got == ""
    assert failed not in _selectable(db)
