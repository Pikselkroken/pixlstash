import threading
from typing import Callable

from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from pixlstash.db_models import Picture

from .base_task_finder import BaseTaskFinder
from .face_extraction_task import FaceExtractionTask

FACE_EXTRACTION_BATCH_LIMIT = 100


class MissingFaceExtractionFinder(BaseTaskFinder):
    """Find pictures missing faces and create a feature extraction task."""

    def __init__(self, database, engine_getter: Callable):
        super().__init__()
        self._db = database
        self._engine_getter = engine_getter

    def finder_name(self) -> str:
        return "MissingFaceExtractionFinder"

    def max_inflight_tasks(self) -> int:
        # Six, not three. The GPU queue is one worker with HIGH-priority face
        # tasks ahead of MEDIUM tags and embeddings, so faces only ever wait
        # when the face queue is momentarily EMPTY - and with three in flight
        # it emptied every few seconds: three ~1.5 s tasks finish faster than
        # a planner sweep over 25 finders refills them, the worker took a 5 s
        # CLIP batch and a 6 s tag batch, and the next face task queued behind
        # both ([PIPELINE_STALL], 11 s apart in a real pass). Each in-flight
        # task holds ~80 MB of 512 px preloads; six is under half a gigabyte.
        return 6

    def find_task(self):
        engine = self._engine_getter()
        if engine is None:
            return None

        # Fetch more than one task's worth, and exclude undecodable pictures at
        # the query - the same two things every other batch finder here already
        # does, and this one did neither.
        #
        # The candidate window was exactly one batch while `max_inflight_tasks`
        # promises three. A picture keeps matching `~faces.any()` until its task
        # finishes, so the moment the first 100 were claimed, every later sweep
        # re-read those same 100, filtered all of them out as claimed, and
        # returned None - which the planner reads as "no work" and answers with
        # a backoff that grows by 1.8x each time. Two slots that could never
        # fill, and a growing sleep between the batches that did: the stall was
        # the finder starving itself, not the GPU.
        suppressed_ids = self._db.unprocessable_images.active_suppressed_ids()
        limit = FACE_EXTRACTION_BATCH_LIMIT * (max(1, self.max_inflight_tasks()) + 1)
        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_missing_features(
                session, limit=limit, suppressed_ids=suppressed_ids
            )
        )
        if not pictures:
            return None

        selected = self._filter_and_claim(pictures, FACE_EXTRACTION_BATCH_LIMIT)
        if not selected:
            return None

        return FaceExtractionTask(
            database=self._db,
            engine=engine,
            pictures=selected,
        )

    def on_task_complete(self, task, error) -> None:
        """Release the claims only once the task's rows are on disk.

        The task frees the GPU worker before its write lands (see
        `FaceExtractionTask._run_task`); releasing the claims at that moment
        would let the next sweep re-offer pictures whose Face rows are still
        queued behind another stage's write. So the release rides the write
        futures: immediately when there are none or they are done, otherwise
        from the last future's completion callback on the writer thread.
        """
        pending = [f for f in getattr(task, "pending_writes", []) if not f.done()]
        if not pending:
            super().on_task_complete(task, error)
            return
        remaining = {"count": len(pending)}
        lock = threading.Lock()

        def _one_landed(_future):
            with lock:
                remaining["count"] -= 1
                last = remaining["count"] == 0
            if last:
                super(MissingFaceExtractionFinder, self).on_task_complete(task, error)

        for future in pending:
            future.add_done_callback(_one_landed)

    def on_all_tasks_complete(self) -> None:
        """Release InsightFace ORT sessions and their CUDA arena once all face
        extraction work is done.

        ORT's CUDAExecutionProvider arena grows with each batch and never shrinks
        on its own.  Destroying the session here frees that memory (often 20+ GB)
        so the next pipeline stage (tagging, embeddings) has a clean VRAM budget.
        The model is small (~400 MB) and reloads quickly if more faces arrive later.

        **Unless the owner asked us to keep models in memory.** "Reloads
        quickly" is a claim about a model that is 400 MB on disk; what actually
        happens is five ONNX sessions rebuilt against the CUDA provider with an
        EXHAUSTIVE cudnn algo search, seconds each, and it was happening between
        batches rather than at the end of the work - every exhaustion of the
        finder counts, and the finder used to exhaust itself constantly (see
        `find_task`). A setting whose whole purpose is to stop that cannot be
        overridden by the one path that reloads most often.
        """
        engine = self._engine_getter()
        if engine is not None and getattr(engine, "keep_models_in_memory", False):
            return
        FaceExtractionTask.release_detection_models()

    @staticmethod
    def _fetch_missing_features(session: Session, limit: int, suppressed_ids=None):
        stmt = select(Picture).where(~Picture.faces.any())
        if suppressed_ids:
            # At the query, not in `_filter_and_claim`: a run of undecodable
            # pictures long enough to fill the window would otherwise hand back
            # a candidate list that claims nothing, forever, and the work behind
            # them would never be reached at all.
            stmt = stmt.where(Picture.id.notin_(tuple(suppressed_ids)))
        return session.exec(
            stmt.options(selectinload(Picture.faces)).order_by(Picture.id).limit(limit)
        ).all()
