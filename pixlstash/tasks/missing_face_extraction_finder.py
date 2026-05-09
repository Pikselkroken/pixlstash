from typing import Callable

from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from pixlstash.db_models import Picture

from .base_task_finder import BaseTaskFinder
from .face_extraction_task import FaceExtractionTask

FACE_EXTRACTION_BATCH_LIMIT = 100


class MissingFaceExtractionFinder(BaseTaskFinder):
    """Find pictures missing faces and create a feature extraction task."""

    def __init__(self, database, picture_tagger_getter: Callable):
        super().__init__()
        self._db = database
        self._picture_tagger_getter = picture_tagger_getter

    def finder_name(self) -> str:
        return "MissingFaceExtractionFinder"

    def max_inflight_tasks(self) -> int:
        return 3

    def find_task(self):
        picture_tagger = self._picture_tagger_getter()
        if picture_tagger is None:
            return None

        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_missing_features(session)
        )
        if not pictures:
            return None

        selected = self._filter_and_claim(pictures, FACE_EXTRACTION_BATCH_LIMIT)
        if not selected:
            return None

        return FaceExtractionTask(
            database=self._db,
            picture_tagger=picture_tagger,
            pictures=selected,
        )

    def on_all_tasks_complete(self) -> None:
        """Release InsightFace ORT sessions and their CUDA arena once all face
        extraction work is done.

        ORT's CUDAExecutionProvider arena grows with each batch and never shrinks
        on its own.  Destroying the session here frees that memory (often 20+ GB)
        so the next pipeline stage (tagging, embeddings) has a clean VRAM budget.
        The model is small (~400 MB) and reloads quickly if more faces arrive later.
        """
        FaceExtractionTask.release_detection_models()

    @staticmethod
    def _fetch_missing_features(session: Session):
        return session.exec(
            select(Picture)
            .where(~Picture.faces.any())
            .options(selectinload(Picture.faces))
            .order_by(Picture.id)
            .limit(FACE_EXTRACTION_BATCH_LIMIT)
        ).all()
