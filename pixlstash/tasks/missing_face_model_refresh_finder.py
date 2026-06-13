"""Finder that selects pictures whose faces were produced by a different pack.

When ``insightface_model_pack`` changes, existing :class:`Face` rows still carry
embeddings from the previous pack. This finder selects pictures that have at
least one face whose ``model_pack`` differs from the currently configured pack
and schedules a :class:`FaceModelRefreshTask` to refresh those embeddings IN
PLACE (preserving ``character_id``).

It declares ``depends_on=[FACE_EXTRACTION]`` so the refresh sweep yields to
initial face extraction: brand-new pictures (which have no faces yet) are always
processed first and never starved by a refresh.
"""

from typing import Callable

from sqlalchemy import or_
from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from pixlstash.db_models import Picture
from pixlstash.db_models.face import Face

from .base_task_finder import BaseTaskFinder
from .face_model_refresh_task import FaceModelRefreshTask
from .task_type import TaskType
from .missing_face_extraction_finder import FACE_EXTRACTION_BATCH_LIMIT


class MissingFaceModelRefreshFinder(BaseTaskFinder):
    """Find pictures with stale-pack faces and create a refresh task."""

    def __init__(self, database, engine_getter: Callable):
        super().__init__()
        self._db = database
        self._engine_getter = engine_getter

    def finder_name(self) -> str:
        return "MissingFaceModelRefreshFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def depends_on(self) -> "list[TaskType]":
        # Yield to initial face extraction so new pictures are never starved by a
        # pack-refresh sweep.
        return [TaskType.FACE_EXTRACTION]

    def find_task(self):
        engine = self._engine_getter()
        if engine is None:
            return None

        current_pack = getattr(engine, "insightface_model_pack", None)
        if not current_pack:
            return None

        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_stale_pack_pictures(session, current_pack)
        )
        if not pictures:
            return None

        selected = self._filter_and_claim(pictures, FACE_EXTRACTION_BATCH_LIMIT)
        if not selected:
            return None

        return FaceModelRefreshTask(
            database=self._db,
            engine=engine,
            pictures=selected,
        )

    @staticmethod
    def _fetch_stale_pack_pictures(session: Session, current_pack: str):
        # A picture is stale if any of its faces was produced by a different pack
        # (or has no recorded pack — defensive; the 0053 migration backfills
        # pre-existing rows to buffalo_l so NULL should not normally occur).
        stale_face_subq = (
            select(Face.picture_id)
            .where(
                or_(
                    Face.model_pack != current_pack,
                    Face.model_pack.is_(None),
                )
            )
            .distinct()
        )
        return session.exec(
            select(Picture)
            .where(Picture.id.in_(stale_face_subq))
            .options(selectinload(Picture.faces))
            .order_by(Picture.id)
            .limit(FACE_EXTRACTION_BATCH_LIMIT)
        ).all()
