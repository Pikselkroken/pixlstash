from sqlalchemy import exists as sa_exists
from sqlmodel import Session, select

from pixlstash.db_models import Face, Picture
from .base_task_finder import BaseTaskFinder
from .source_face_likeness_task import SourceFaceLikenessTask


class MissingSourceFaceLikenessCharacterFinder(BaseTaskFinder):
    """Find T2I pictures with source_picture_id set and extracted face embeddings.

    A picture is eligible once:
    - ``source_picture_id`` is not NULL (set during T2I import), and
    - at least one of its faces has a non-NULL ``features`` embedding (face
      extraction has completed).
    """

    def __init__(self, database):
        super().__init__()
        self._db = database

    def finder_name(self) -> str:
        return "MissingSourceFaceLikenessCharacterFinder"

    def find_task(self):
        pictures = self._db.run_immediate_read_task(self._fetch_pending)
        if not pictures:
            return None
        return SourceFaceLikenessTask(
            database=self._db,
            batch=[p.id for p in pictures],
        )

    @staticmethod
    def _fetch_pending(session: Session):
        return session.exec(
            select(Picture)
            .where(Picture.source_picture_id.is_not(None))
            .where(
                sa_exists(
                    select(Face.id).where(
                        Face.picture_id == Picture.id,
                        Face.features.is_not(None),
                    )
                )
            )
            .limit(SourceFaceLikenessTask.BATCH_SIZE)
        ).all()
