import time
from collections import defaultdict

import numpy as np
from sqlmodel import Session, func, select

from pixlstash.database import DBPriority
from pixlstash.db_models import Face, Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask

logger = get_logger(__name__)


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two 1-D embedding vectors."""
    a_norm = a / (np.linalg.norm(a) + 1e-8)
    b_norm = b / (np.linalg.norm(b) + 1e-8)
    return float(np.dot(a_norm, b_norm))


class SourceFaceLikenessTask(BaseTask):
    """Assign character IDs to generated images using face embedding similarity.

    For each picture with ``source_picture_id`` set and at least one face with
    an embedding, compares target face embeddings against the source picture's
    faces.  Characters are assigned where cosine similarity >=
    ``SIMILARITY_THRESHOLD``.  After processing (regardless of outcome),
    ``source_picture_id`` is cleared so the picture is not picked up again.

    Attributes:
        BATCH_SIZE: Maximum number of pictures processed per task run.
        SIMILARITY_THRESHOLD: Minimum cosine similarity required to inherit a
            character from a source face.
    """

    BATCH_SIZE = 32
    SIMILARITY_THRESHOLD = 0.7

    def __init__(self, database, batch: list):
        super().__init__(
            task_type="SourceFaceLikenessTask",
            params={"batch_size": len(batch)},
        )
        self._db = database
        self._batch = batch  # list[int] of picture_ids

    def _run_task(self):
        start = time.time()
        assigned_total = 0
        for picture_id in self._batch:
            assigned_total += self._process_picture(picture_id)
        logger.info(
            "SourceFaceLikenessTask: processed %s picture(s), assigned %s character(s) in %.2fs",
            len(self._batch),
            assigned_total,
            time.time() - start,
        )
        return {"processed": len(self._batch), "assigned": assigned_total}

    def _process_picture(self, picture_id: int) -> int:
        """Process one picture. Returns the number of character assignments made."""

        def fetch(session):
            pic = session.get(Picture, picture_id)
            if pic is None or pic.source_picture_id is None:
                return None, [], []
            source_faces = session.exec(
                select(Face).where(
                    Face.picture_id == pic.source_picture_id,
                    Face.features.is_not(None),
                    Face.character_id.is_not(None),
                )
            ).all()
            target_faces = session.exec(
                select(Face).where(
                    Face.picture_id == picture_id,
                    Face.features.is_not(None),
                )
            ).all()
            return pic.source_picture_id, list(source_faces), list(target_faces)

        source_picture_id, source_faces, target_faces = (
            self._db.run_immediate_read_task(fetch)
        )
        # Always clear the marker so the picture is not retried.
        self._clear_source_picture_id(picture_id)

        if not source_faces or not target_faces:
            return 0

        # Build character_id -> list[embedding] map from source faces.
        char_embeddings: dict = defaultdict(list)
        for face in source_faces:
            emb = np.frombuffer(face.features, dtype=np.float32).copy()
            char_embeddings[face.character_id].append(emb)

        assignments: dict = {}  # face_id -> character_id
        for target_face in target_faces:
            if target_face.character_id is not None:
                continue  # already assigned
            target_emb = np.frombuffer(target_face.features, dtype=np.float32).copy()
            best_char = None
            best_sim = self.SIMILARITY_THRESHOLD - 1e-9
            for char_id, emb_list in char_embeddings.items():
                for src_emb in emb_list:
                    sim = _cosine_sim(target_emb, src_emb)
                    if sim > best_sim:
                        best_sim = sim
                        best_char = char_id
            if best_char is not None:
                assignments[target_face.id] = best_char

        if assignments:

            def apply_assignments(session):
                for face_id, char_id in assignments.items():
                    face = session.get(Face, face_id)
                    if face is not None and face.character_id is None:
                        face.character_id = char_id
                        session.add(face)
                session.commit()

            self._db.run_task(apply_assignments)
            logger.info(
                "SourceFaceLikenessTask: assigned %s character(s) to picture %s from source %s",
                len(assignments),
                picture_id,
                source_picture_id,
            )

        return len(assignments)

    def _clear_source_picture_id(self, picture_id: int) -> None:
        def clear(session):
            pic = session.get(Picture, picture_id)
            if pic is not None and pic.source_picture_id is not None:
                pic.source_picture_id = None
                session.add(pic)
                session.commit()

        self._db.run_task(clear, priority=DBPriority.LOW)

    @staticmethod
    def count_pending(session: Session) -> int:
        from sqlalchemy import exists as sa_exists

        return session.exec(
            select(func.count(Picture.id)).where(
                Picture.source_picture_id.is_not(None),
                sa_exists(
                    select(Face.id).where(
                        Face.picture_id == Picture.id,
                        Face.features.is_not(None),
                    )
                ),
            )
        ).one()
