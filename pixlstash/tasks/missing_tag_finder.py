from typing import Callable

from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from pixlstash.db_models import (
    Picture,
    Tag,
    TAG_SENTINEL_LIKE_PATTERN,
    TAG_SENTINEL_ESCAPE_CHAR,
    is_tag_sentinel,
    parse_tag_engine_from_sentinel,
)
from pixlstash.worker_config import TAGGER_MAX_INFLIGHT
from .base_task_finder import BaseTaskFinder
from .tag_task import TagTask
from .task_type import TaskType


class MissingTagFinder(BaseTaskFinder):
    """Find a batch of pictures with a pending-retag sentinel and create a TagTask."""

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
        # Only queue tagging when an active tag plugin is configured.
        tagger_settings = getattr(engine, "tagger_settings", None)
        active_tag_plugin = (tagger_settings or {}).get("active_tag_plugin")
        if not active_tag_plugin:
            return None

        batch_limit = max(
            1,
            int(engine.tagging_workflow.suggested_task_size()),
        )
        # Fetch enough candidates that _filter_and_claim can always fill one
        # additional task even when all max_inflight slots are already in-flight.
        max_inflight = max(1, self.max_inflight_tasks())
        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_missing_tags(
                session, batch_limit * (max_inflight + 1)
            )
        )
        if not pictures:
            return None

        # Group pictures by their requested engine (None = use active_tag_plugin).
        groups: dict[str | None, list] = {}
        for pic in pictures:
            sentinel_tag = next(
                (t.tag for t in pic.tags if is_tag_sentinel(t.tag)),
                None,
            )
            engine_name = parse_tag_engine_from_sentinel(sentinel_tag)
            groups.setdefault(engine_name, []).append(pic)

        # Process the first group only (subsequent calls will handle the rest).
        first_engine, first_pics = next(iter(groups.items()))
        selected = self._filter_and_claim(first_pics, batch_limit)
        if not selected:
            return None

        return TagTask(
            database=self._db,
            tagging_workflow=engine.tagging_workflow,
            pictures=selected,
            engine_override=first_engine,
        )

    @staticmethod
    def _fetch_missing_tags(session: Session, limit: int):
        has_sentinel = Tag.tag.like(
            TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR
        )
        return session.exec(
            select(Picture)
            .where(Picture.tags.any(has_sentinel))
            .options(
                selectinload(Picture.tags),
            )
            .order_by(Picture.id)
            .limit(limit)
        ).all()
