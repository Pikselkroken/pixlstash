from typing import Callable

from sqlmodel import Session, select
from sqlalchemy import or_

from pixlstash.db_models import (
    Picture,
    DESCRIPTION_SENTINEL_LIKE_PATTERN,
    DESCRIPTION_SENTINEL_ESCAPE_CHAR,
    parse_engine_from_description_sentinel,
    is_description_sentinel,
)

from .description_task import DescriptionTask
from .task_type import TaskType
from .base_task_finder import BaseTaskFinder


class MissingDescriptionFinder(BaseTaskFinder):
    """Find a batch of pictures missing descriptions and create a DescriptionTask."""

    def __init__(
        self,
        database,
        engine_getter: Callable,
    ):
        super().__init__()
        self._db = database
        self._engine_getter = engine_getter

    def finder_name(self) -> str:
        return "MissingDescriptionFinder"

    def depends_on(self) -> list[TaskType]:
        return [TaskType.FACE_EXTRACTION, TaskType.TAGGER]

    def find_task(self):
        engine = self._engine_getter()
        if engine is None:
            return None

        # Only queue description work when an active description plugin is configured.
        tagger_settings = getattr(engine, "tagger_settings", None)
        if tagger_settings is not None:
            active_plugin = tagger_settings.get("active_description_plugin")
            if not active_plugin:
                return None
        # If no tagger_settings at all, fall through to the old behaviour
        # (Florence-2 always active).

        batch_limit = max(
            1,
            int(engine.description_batch_size()),
        )

        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_missing_descriptions(session, batch_limit * 3)
        )
        if not pictures:
            return None

        # Group pictures by the engine embedded in their sentinel (None = use
        # active_description_plugin).  Process only the first group per cycle so
        # that interactive requests with a specific engine are not starved by a
        # large backlog of NULL-description pictures.
        groups: dict[str | None, list] = {}
        for pic in pictures:
            engine_name = (
                parse_engine_from_description_sentinel(pic.description)
                if is_description_sentinel(pic.description)
                else None
            )
            groups.setdefault(engine_name, []).append(pic)

        # Prefer explicit-engine (sentinel) requests first to avoid starvation
        # by the NULL-description backlog.
        first_engine = next((k for k in groups if k is not None), None)
        first_pics = groups[first_engine] if first_engine is not None else groups[None]
        selected = self._filter_and_claim(first_pics, batch_limit)
        if not selected:
            return None

        return DescriptionTask(
            database=self._db,
            workflow=engine.description_workflow,
            pictures=selected,
            engine_override=first_engine,
        )

    @staticmethod
    def _fetch_missing_descriptions(session: Session, limit: int):
        return session.exec(
            select(Picture)
            .where(
                or_(
                    Picture.description.is_(None),
                    Picture.description.like(
                        DESCRIPTION_SENTINEL_LIKE_PATTERN,
                        escape=DESCRIPTION_SENTINEL_ESCAPE_CHAR,
                    ),
                )
            )
            .order_by(Picture.id)
            .limit(limit)
        ).all()

