from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from pixlstash.db_models import Picture

from .base_task_finder import SimpleMissingFinder
from .comfyui_extraction_task import ComfyUIExtractionTask


class MissingComfyUIExtractionFinder(SimpleMissingFinder):
    """Find pictures not yet checked for embedded ComfyUI workflow metadata."""

    def __init__(self, database, image_root: str):
        super().__init__(database)
        self._image_root = image_root

    def finder_name(self) -> str:
        return "MissingComfyUIExtractionFinder"

    def _batch_size(self) -> int:
        return ComfyUIExtractionTask.BATCH_SIZE

    def _fetch_candidates(self, session: Session, limit: int) -> list[Picture]:
        # comfyui_models IS NULL means never checked; "[]" is the checked-but-empty sentinel.
        return session.exec(
            select(Picture)
            .options(load_only(Picture.id, Picture.file_path, Picture.comfyui_models))
            .where(Picture.comfyui_models.is_(None))
            .where(Picture.deleted.is_(False))
            .order_by(Picture.id)
            .limit(limit)
        ).all()

    def _create_task(self, pictures: list):
        return ComfyUIExtractionTask(
            database=self._db,
            image_root=self._image_root,
            pictures=pictures,
        )
