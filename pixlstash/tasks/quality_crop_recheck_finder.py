import threading
import time
from typing import Callable

from sqlalchemy import true
from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from pixlstash.db_models import (
    Picture,
    Tag,
    TAG_SENTINEL_LIKE_PATTERN,
    TAG_SENTINEL_ESCAPE_CHAR,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.services.set_lock_service import locked_picture_id_subquery
from pixlstash.task_runner import TaskCancelledError
from .base_task_finder import SimpleMissingFinder
from .quality_crop_recheck_task import QualityCropRecheckTask
from .task_type import TaskType


logger = get_logger(__name__)


class QualityCropRecheckFinder(SimpleMissingFinder):
    """Hand out pictures the owner asked to have their quality crop re-checked.

    Selects ``Picture.quality_crop_pending`` rows that are not scrapheaped, are
    not frozen by a locked set, and carry no retag sentinel (a pending full tag
    runs the crop pass itself, and its write clears the flag). Idles, leaving
    every flag in place, while the quality crop is off or tagging is switched
    off entirely.
    """

    #: How long the finder stays quiet after one of its tasks failed. A failure
    #: here is the tagger not loading, which the next sweep would repeat: without
    #: a pause the planner, which resets to its shortest interval whenever it
    #: submits, would retry the model load several times a second.
    FAILURE_BACKOFF_S = 60.0

    def __init__(self, database, engine_getter: Callable):
        super().__init__(database)
        self._engine_getter = engine_getter
        self._reset_generation = 0
        self._backoff_lock = threading.Lock()
        self._retry_after = 0.0

    def finder_name(self) -> str:
        return "QualityCropRecheckFinder"

    def depends_on(self) -> list[TaskType]:
        # Behind live work: face extraction has GPU priority, and new-import
        # tagging runs the crop pass itself.
        return [TaskType.FACE_EXTRACTION, TaskType.TAGGER]

    def on_task_complete(self, task, error) -> None:
        super().on_task_complete(task, error)
        if error is None or isinstance(error, TaskCancelledError):
            return
        logger.warning(
            "Quality crop re-check task %s failed (%s); pausing the re-check for "
            "%.0f s. Its pictures stay pending.",
            getattr(task, "id", None),
            error,
            self.FAILURE_BACKOFF_S,
        )
        with self._backoff_lock:
            self._retry_after = time.monotonic() + self.FAILURE_BACKOFF_S

    def _guard(self) -> bool:
        with self._backoff_lock:
            if time.monotonic() < self._retry_after:
                return False
        engine = self._engine_getter()
        if engine is None:
            return False
        # Tagging turned off entirely means no background tagger inference, as
        # MissingTagFinder and MissingTagPredictionFinder read it.
        tagger_settings = getattr(engine, "tagger_settings", None) or {}
        if not tagger_settings.get("active_tag_plugin"):
            return False
        return (
            engine.tagging_workflow.pixlstash_tagger_image_size_quality_crop()
            is not None
        )

    def _batch_size(self) -> int:
        engine = self._engine_getter()
        if engine is None:
            return 8
        return max(1, int(engine.tagging_workflow.suggested_task_size()))

    def _create_task(self, pictures: list):
        engine = self._engine_getter()
        if engine is None:
            return None
        return QualityCropRecheckTask(
            database=self._db,
            tagging_workflow=engine.tagging_workflow,
            pictures=pictures,
            reset_generation=self._reset_generation,
        )

    def find_task(self):
        # Before the read: a retag landing between the two must make the task
        # older than the reset, never newer (#1361).
        tag_resets = getattr(self._db, "tag_resets", None)
        self._reset_generation = tag_resets.current() if tag_resets else 0
        return super().find_task()

    def _fetch_candidates(self, session: Session, limit: int) -> list:
        registry = getattr(self._db, "unprocessable_images", None)
        suppressed = registry.active_suppressed_ids() if registry is not None else None
        return self.fetch_pending(session, limit, suppressed)

    @staticmethod
    def fetch_pending(session: Session, limit: int, suppressed_ids=None) -> list:
        """Pending pictures, oldest id first, narrowed to what the task reads.

        Suppressed (undecodable or unreachable) pictures are excluded at the
        query rather than only at the claim: the task leaves such a picture
        pending, and ordered by id a handful of them would otherwise fill the
        candidate window on every sweep and starve the rest, the shape
        ``MissingTagFinder`` documents on its own query.
        """
        pending_tag = select(Tag.picture_id).where(
            Tag.tag.like(TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR)
        )
        stmt = select(Picture).where(
            # `= 1`, not `IS 1`: the literal form of the partial index's WHERE,
            # so SQLite can prove the index covers the probe.
            Picture.quality_crop_pending == true(),
            Picture.deleted.is_(False),
            Picture.file_path.is_not(None),
            ~Picture.id.in_(pending_tag),
            ~Picture.id.in_(locked_picture_id_subquery()),
        )
        if suppressed_ids:
            stmt = stmt.where(Picture.id.notin_(tuple(suppressed_ids)))
        return session.exec(
            stmt.options(load_only(Picture.id, Picture.file_path))
            .order_by(Picture.id)
            .limit(limit)
        ).all()
