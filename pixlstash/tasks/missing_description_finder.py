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
from pixlstash.pixl_logging import get_logger
from pixlstash.services.set_lock_service import locked_picture_id_subquery
from pixlstash.tagger_plugins.registry import get_tagger_plugin_manager

from .description_task import DescriptionTask
from .task_type import TaskType
from .base_task_finder import BaseTaskFinder

logger = get_logger(__name__)


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

        engine_batch_size = max(
            1,
            int(engine.description_batch_size()),
        )

        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_missing_descriptions(
                session, engine_batch_size * 3
            )
        )
        if not pictures:
            return None

        # A sentinel is a user's reset (#1162): those pictures come first, as
        # an urgent task, ahead of the NULL backlog the import left behind.
        # Group them by the engine embedded in the sentinel (None = use
        # active_description_plugin) and process one group per cycle.
        requested = [
            pic for pic in pictures if is_description_sentinel(pic.description)
        ]
        if requested:
            groups: dict[str | None, list] = {}
            for pic in requested:
                engine_name = parse_engine_from_description_sentinel(pic.description)
                groups.setdefault(engine_name, []).append(pic)
            first_engine = next((k for k in groups if k is not None), None)
            first_pics = groups[first_engine]
        else:
            first_engine = None
            first_pics = pictures
        task_size = self._task_size(engine, first_engine, engine_batch_size)
        selected = self._filter_and_claim(first_pics, task_size)
        if not selected:
            return None

        return DescriptionTask(
            database=self._db,
            workflow=engine.description_workflow,
            pictures=selected,
            engine_override=first_engine,
            interactive=bool(requested),
        )

    @staticmethod
    def _task_size(engine, engine_override: str | None, engine_batch_size: int) -> int:
        """Return how many pictures the next description task carries.

        The plugin that will caption the task has the say, through
        ``TaggerPlugin.description_task_size``: the group's own engine for a
        re-description request, the active description plugin otherwise. It is
        resolved the way ``DescriptionWorkflow.generate_batch`` dispatches, so
        Florence-2, and a plugin that workflow would replace with Florence-2
        because it is missing or cannot caption, keep the engine's size. A
        plugin's answer can only shrink the task, and a hook that raises or
        answers something ``int()`` cannot read is logged and keeps it.

        Args:
            engine: The inference engine the task will run on.
            engine_override: Plugin named by the selected re-description group,
                or ``None`` for the active description plugin.
            engine_batch_size: The engine's description batch size.

        Returns:
            The task size, at least 1.
        """
        tagger_settings = getattr(engine, "tagger_settings", None) or {}
        plugin_name = (
            engine_override
            if engine_override is not None
            else tagger_settings.get("active_description_plugin", "florence2")
        )
        if not plugin_name or plugin_name == "florence2":
            return engine_batch_size
        plugin = get_tagger_plugin_manager().get_plugin(plugin_name)
        if plugin is None or not plugin.supports_descriptions:
            return engine_batch_size
        try:
            plugin_size = plugin.description_task_size(engine.device)
            if plugin_size is None:
                return engine_batch_size
            return max(1, min(engine_batch_size, int(plugin_size)))
        except Exception as exc:
            # A third-party hook. Raised here it would fail find_task on every
            # sweep, and the description backlog would never get a task.
            logger.warning(
                "Description plugin %r could not size a task on %s; using the "
                "engine's batch size of %d: %s: %s",
                plugin_name,
                engine.device,
                engine_batch_size,
                type(exc).__name__,
                exc,
            )
            return engine_batch_size

    @staticmethod
    def _fetch_missing_descriptions(session: Session, limit: int):
        # A picture frozen by a locked set has a read-only description (rule 3):
        # never re-queue it for machine (re)description. Parity with the tagger's
        # MissingTagFinder exclusion; the description_task write-side also skips
        # locked pics as defense in depth.
        #
        # Must be the shared set_lock_service predicate, not a local
        # PictureSetMember join. The local join had no stack arm, while
        # DescriptionTask's write guard (`locked_picture_ids`) does: a picture
        # merely *sharing a stack* with a locked-set member was selected here, ran
        # full captioning inference, had its write skipped, kept its NULL
        # description, and was selected again next sweep - an unbounded loop.
        not_locked = ~Picture.id.in_(locked_picture_id_subquery())
        return session.exec(
            select(Picture)
            .where(
                or_(
                    Picture.description.is_(None),
                    Picture.description.like(
                        DESCRIPTION_SENTINEL_LIKE_PATTERN,
                        escape=DESCRIPTION_SENTINEL_ESCAPE_CHAR,
                    ),
                ),
                not_locked,
            )
            # Sentinels (a reset) ahead of NULLs (never captioned), then by id,
            # so a request never waits behind the backlog.
            .order_by(Picture.description.is_(None), Picture.id)
            .limit(limit)
        ).all()
