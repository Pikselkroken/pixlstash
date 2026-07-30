"""DB-layer helpers for face-search and likeness-search queries.

These functions accept a *Database* instance (``vault.db``) and delegate
session management to it, keeping direct ``vault.db`` calls out of route
handlers.
"""

from __future__ import annotations

import numpy as np
from fastapi import HTTPException
from sqlmodel import select

from pixlstash.db_models import Face, Picture
from pixlstash.utils.likeness.likeness_utils import LikenessUtils
from pixlstash.utils.service.filter_helpers import (
    fetch_set_candidate_ids,
    project_membership_exists_clause,
    project_unassigned_clause,
)


def fetch_set_filter_candidate_ids(
    db,
    *,
    set_ids: list[int],
    set_mode: str,
) -> set[int]:
    """Return picture IDs matching *set_ids* under *set_mode*.

    Args:
        db: The ``vault.db`` Database instance.
        set_ids: Non-empty list of set ids.
        set_mode: One of ``"union"``, ``"intersection"``, ``"difference"``,
            or ``"xor"``.

    Returns:
        Set of matching picture ids.
    """
    return db.run_immediate_read_task(
        fetch_set_candidate_ids,
        set_ids=set_ids,
        set_mode=set_mode,
        deleted_only=False,
    )


def fetch_project_candidate_ids(db, project_id_str: str) -> set[int]:
    """Return picture IDs belonging to *project_id_str* (or UNASSIGNED).

    Args:
        db: The ``vault.db`` Database instance.
        project_id_str: A numeric project id or the string ``"UNASSIGNED"``.

    Returns:
        Set of non-deleted picture ids matching the project filter.

    Raises:
        HTTPException: 400 when *project_id_str* is not parseable as an int
            (and is not ``"UNASSIGNED"``).
    """

    def _fetch(session, project_id_value: str) -> set[int]:
        if project_id_value == "UNASSIGNED":
            stmt = select(Picture.id).where(
                Picture.deleted.is_(False),
                project_unassigned_clause(Picture),
            )
        else:
            try:
                parsed_project_id = int(project_id_value)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid project_id")
            stmt = select(Picture.id).where(
                Picture.deleted.is_(False),
                project_membership_exists_clause(parsed_project_id, Picture),
            )
        return {int(r) for r in session.exec(stmt).all()}

    return db.run_immediate_read_task(_fetch, project_id_str)


def fetch_character_candidate_ids(db, char_id: int) -> set[int]:
    """Return picture IDs that contain a face assigned to *char_id*.

    Args:
        db: The ``vault.db`` Database instance.
        char_id: Character id to filter by.

    Returns:
        Set of picture ids.
    """

    def _fetch(session, cid: int) -> set[int]:
        return {
            int(r)
            for r in session.exec(
                select(Face.picture_id).where(Face.character_id == cid)
            ).all()
        }

    return db.run_immediate_read_task(_fetch, char_id)


def fetch_face_candidates(
    db,
    candidate_ids: set[int] | None,
) -> list[tuple[int, list[tuple[int, np.ndarray]]]]:
    """Return ``(picture_id, [(face_id, embedding), ...])`` pairs for ArcFace search.

    Only non-deleted pictures with at least one non-null face embedding are
    returned.  Passing *candidate_ids=None* means all eligible pictures are
    considered.

    The face id travels with each embedding because a face search's caller needs
    to know *which* detection matched, not just which picture: a bulk character
    assignment writes to the face row, and re-deriving the winner from the
    picture id would repeat the whole comparison.

    Args:
        db: The ``vault.db`` Database instance.
        candidate_ids: Optional restriction set; ``None`` means unrestricted.

    Returns:
        List of ``(picture_id, [(face_id, embedding), ...])`` tuples.
    """

    def _fetch(session) -> list[tuple[int, list[tuple[int, np.ndarray]]]]:
        from sqlalchemy import select as sa_select

        query = (
            sa_select(Face.picture_id, Face.id, Face.features)
            .join(Picture, Face.picture_id == Picture.id)
            .where(
                Face.features.is_not(None),
                Picture.deleted.is_(False),
            )
        )
        if candidate_ids is not None:
            if not candidate_ids:
                return []
            query = query.where(Face.picture_id.in_(candidate_ids))

        rows = session.execute(query).all()

        pic_embs: dict[int, list[tuple[int, np.ndarray]]] = {}
        for pic_id, face_id, features in rows:
            pic_id = int(pic_id)
            emb = np.frombuffer(features, dtype=np.float32).copy()
            if emb.size > 0:
                pic_embs.setdefault(pic_id, []).append((int(face_id), emb))

        return list(pic_embs.items())

    return db.run_immediate_read_task(_fetch)


def fetch_character_reference_embeddings(db, character_id: int) -> list[np.ndarray]:
    """Return the ArcFace embeddings of a character's reference faces.

    Delegates the *choice* of reference faces to
    :func:`pixlstash.scoring.character_likeness.select_reference_faces_for_character`,
    the same selection the character-likeness sort and the picture-id branch of
    ``POST /characters/{id}/faces`` use.  Deriving the query set here by any
    other rule would let a search rank pictures against one set of references
    while the assignment it feeds picks the winning face against another.

    Args:
        db: The ``vault.db`` Database instance.
        character_id: The character whose reference faces are wanted.

    Returns:
        List of float32 embedding arrays; empty if the character has no
        reference face carrying an embedding.
    """

    def _fetch(session, char_id: int) -> list[np.ndarray]:
        from pixlstash.scoring.character_likeness import (
            select_reference_faces_for_character,
        )

        result = []
        for face in select_reference_faces_for_character(session, char_id):
            if not face.features:
                continue
            emb = np.frombuffer(face.features, dtype=np.float32).copy()
            if emb.size > 0:
                result.append(emb)
        return result

    return db.run_immediate_read_task(_fetch, character_id)


def fetch_face_embeddings_by_picture(db, picture_id: int) -> list[np.ndarray]:
    """Return the ArcFace embeddings stored for *picture_id*.

    Args:
        db: The ``vault.db`` Database instance.
        picture_id: The picture to fetch embeddings for.

    Returns:
        List of float32 embedding arrays; empty if none are stored.
    """

    def _fetch(session, pic_id: int) -> list[np.ndarray]:
        from sqlalchemy import select as sa_select

        rows = session.execute(
            sa_select(Face.features).where(
                Face.picture_id == pic_id, Face.features.is_not(None)
            )
        ).all()
        result = []
        for (features,) in rows:
            emb = np.frombuffer(features, dtype=np.float32).copy()
            if emb.size > 0:
                result.append(emb)
        return result

    return db.run_immediate_read_task(_fetch, picture_id)


def fetch_face_embedding_by_face_id(db, face_id: int) -> list[np.ndarray]:
    """Return the ArcFace embedding(s) for a specific *face_id*.

    Args:
        db: The ``vault.db`` Database instance.
        face_id: The face row id to fetch the embedding for.

    Returns:
        List of float32 embedding arrays; empty if the face has no embedding.
    """

    def _fetch(session, fid: int) -> list[np.ndarray]:
        from sqlalchemy import select as sa_select

        rows = session.execute(
            sa_select(Face.features).where(Face.id == fid, Face.features.is_not(None))
        ).all()
        result = []
        for (features,) in rows:
            emb = np.frombuffer(features, dtype=np.float32).copy()
            if emb.size > 0:
                result.append(emb)
        return result

    return db.run_immediate_read_task(_fetch, face_id)


def fetch_candidate_clip_embeddings(
    db,
    candidate_ids: list[int] | None,
) -> list[tuple[int, np.ndarray]]:
    """Return ``(picture_id, normalised_embedding)`` pairs for CLIP search.

    Only non-deleted pictures with a stored image embedding are returned.
    Passing *candidate_ids=None* means all eligible pictures are considered.

    Args:
        db: The ``vault.db`` Database instance.
        candidate_ids: Optional restriction list; ``None`` means unrestricted.

    Returns:
        List of ``(picture_id, normalised_float32_embedding)`` tuples.
    """

    def _query(session) -> list[tuple[int, bytes]]:
        stmt = (
            select(Picture.id, Picture.image_embedding)
            .where(Picture.deleted.is_(False))
            .where(Picture.image_embedding.is_not(None))
        )
        if candidate_ids is not None:
            stmt = stmt.where(Picture.id.in_(candidate_ids))
        return session.exec(stmt).all()

    rows = db.run_immediate_read_task(_query)

    results: list[tuple[int, np.ndarray]] = []
    for pic_id, blob in rows:
        emb = LikenessUtils.decode_embedding(blob)
        if emb is None or emb.size == 0:
            continue
        norm = float(np.linalg.norm(emb))
        if norm > 0:
            emb = emb / norm
        results.append((int(pic_id), emb))
    return results


def fetch_source_clip_embeddings(
    db,
    source_picture_ids: list[int],
) -> list[tuple[int, bytes]]:
    """Return raw ``(picture_id, embedding_blob)`` for *source_picture_ids*.

    Args:
        db: The ``vault.db`` Database instance.
        source_picture_ids: Picture ids whose stored CLIP embeddings to fetch.

    Returns:
        List of ``(picture_id, raw_blob)`` tuples for non-deleted pictures
        that have a stored embedding.
    """

    def _fetch(session) -> list[tuple[int, bytes]]:
        rows = session.exec(
            select(Picture.id, Picture.image_embedding)
            .where(Picture.id.in_(source_picture_ids))
            .where(Picture.deleted.is_(False))
            .where(Picture.image_embedding.is_not(None))
        ).all()
        return list(rows)

    return db.run_immediate_read_task(_fetch)
