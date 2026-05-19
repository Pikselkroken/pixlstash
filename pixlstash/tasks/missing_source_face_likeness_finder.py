from sqlalchemy import exists as sa_exists
from sqlmodel import Session, select

from pixlstash.db_models import Face, Picture
from .base_task_finder import SimpleMissingFinder
from .source_face_likeness_task import SourceFaceLikenessTask


class MissingSourceFaceLikenessCharacterFinder(SimpleMissingFinder):
    """Find T2I pictures with source_picture_id set and extracted face embeddings.

    A picture is eligible once:
    - ``source_picture_id`` is not NULL (set during T2I import), and
    - at least one of its faces has a non-NULL ``features`` embedding (face
      extraction has completed).
    """

    def finder_name(self) -> str:
        return "MissingSourceFaceLikenessCharacterFinder"

    def _batch_size(self) -> int:
        return SourceFaceLikenessTask.BATCH_SIZE

    def _fetch_candidates(self, session: Session, limit: int) -> list:
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
            .limit(limit)
        ).all()

    def _create_task(self, pictures: list):
        return SourceFaceLikenessTask(
            database=self._db,
            batch=[p.id for p in pictures],
        )
