from typing import Callable

from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from pixlstash.db_models import Picture, Tag, TAG_EMPTY_SENTINEL
from pixlstash.worker_config import TAGGER_MAX_INFLIGHT
from .base_task_finder import BaseTaskFinder
from .tag_task import TagTask
from .task_type import TaskType


class MissingTagFinder(BaseTaskFinder):
    """Find a batch of pictures missing tags and create a TagTask."""

    def __init__(
        self,
        database,
        engine_getter: Callable,
    ):
        super().__init__()
        self._db = database
        self._engine_getter = engine_getter

    def finder_name(self) -> str:
        return "MissingTagFinder"

    def max_inflight_tasks(self) -> int:
        return TAGGER_MAX_INFLIGHT

    def depends_on(self) -> list[TaskType]:
        # Never submit tag tasks while face extraction is inflight — face
        # extraction has GPU priority and must not be starved by queued tagging.
        return [TaskType.FACE_EXTRACTION]

    def on_all_tasks_complete(self) -> None:
        """Unload the WD14 ONNX session once all tagging work is done.

        ORT's CUDAExecutionProvider holds its entire activation arena inside the
        session object.  Deleting the session returns that memory (often tens of
        GB for large batches) so the next GPU pipeline stage starts with a clean
        VRAM budget.  The session is rebuilt lazily on the next tagging cycle.
        """
        tagger = self._engine_getter()
        if tagger is not None:
            tagger.unload_tagger_session()

    def find_task(self):
        engine = self._engine_getter()
        if engine is None:
            return None
        wd14_enabled = engine.wd14_enabled
        pixlstash_tagger_enabled = engine.pixlstash_tagger_enabled
        if not wd14_enabled and not pixlstash_tagger_enabled:
            return None

        batch_limit = max(
            1,
            int(engine.tagging_workflow.suggested_task_size()),
        )
        # Fetch enough candidates that _filter_and_claim can always fill one
        # additional task even when all max_inflight slots are already in-flight.
        # With max_inflight=3 and batch_limit=18, the 3 running tasks claim 54
        # picture IDs.  Fetching only batch_limit*3=54 returns the same 54 IDs
        # (all claimed) so find_task() returns None → _finder_exhausted=True →
        # on_all_tasks_complete() fires and destroys the ONNX session mid-run.
        # Fetching batch_limit*(max_inflight+1) guarantees unclaimed candidates.
        max_inflight = max(1, self.max_inflight_tasks())
        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_missing_tags(
                session, batch_limit * (max_inflight + 1)
            )
        )
        if not pictures:
            return None

        selected = self._filter_and_claim(pictures, batch_limit)

        if not selected:
            return None

        return TagTask(
            database=self._db,
            tagging_workflow=engine.tagging_workflow,
            pictures=selected,
        )

    @staticmethod
    def _fetch_missing_tags(session: Session, limit: int):
        has_real_tag = (Tag.tag.is_not(None)) & (Tag.tag != TAG_EMPTY_SENTINEL)
        return session.exec(
            select(Picture)
            .where(~Picture.tags.any(has_real_tag))
            .options(
                selectinload(Picture.tags),
            )
            .order_by(Picture.id)
            .limit(limit)
        ).all()
