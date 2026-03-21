from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from pixlstash.db_models import Picture

from .base_task_finder import BaseTaskFinder
from .comfyui_extraction_task import ComfyUIExtractionTask


class MissingComfyUIExtractionFinder(BaseTaskFinder):
    """Find pictures not yet checked for embedded ComfyUI workflow metadata."""

    BATCH_SIZE = ComfyUIExtractionTask.BATCH_SIZE

    def __init__(self, database, image_root: str):
        super().__init__()
        self._db = database
        self._image_root = image_root

    def finder_name(self) -> str:
        return "MissingComfyUIExtractionFinder"

    def find_task(self):
        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_unchecked(session, self.BATCH_SIZE * 3)
        )
        if not pictures:
            return None

        selected = self._filter_and_claim(pictures, self.BATCH_SIZE)
        if not selected:
            return None

        return ComfyUIExtractionTask(
            database=self._db,
            image_root=self._image_root,
            pictures=selected,
        )

    @staticmethod
    def _fetch_unchecked(session: Session, limit: int) -> list[Picture]:
        query = select(Picture)
        query = query.options(
            load_only(Picture.id, Picture.file_path, Picture.comfyui_models)
        )
        # comfyui_models IS NULL means never checked; "[]" is the checked-but-empty sentinel.
        query = query.where(Picture.comfyui_models.is_(None))
        query = query.where(Picture.deleted.is_(False))
        query = query.order_by(Picture.id)
        query = query.limit(limit)
        return session.exec(query).all()
