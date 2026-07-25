"""Manual face create / delete endpoints.

``POST /pictures/{id}/face`` adds a manual face bounding box; ``DELETE
/pictures/{id}/face/{index}`` removes one and reindexes the rest. Also hosts the
``_DetectedFace`` adapter, which exposes an in-memory face detection as the
``(.id, .features)`` shape ``compute_character_likeness_for_faces`` consumes so
an uploaded image can be scored without persisting any ``Picture``/``Face`` rows.

Object scope: the create/delete routes are per-object data endpoints declared
``PICTURE_SCOPED`` (id_param ``id``) in ``pixlstash/authz/registry.py``; the
centralised authz gate authorizes before the handler body.
"""

from typing import Optional

import numpy as np
from fastapi import Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import Face, Picture
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.serialization_utils import safe_model_dict


logger = get_logger(__name__)


class _DetectedFace:
    """Adapter exposing an in-memory face detection as the ``(.id, .features)``
    shape ``compute_character_likeness_for_faces`` consumes.

    ``FaceResult.embedding`` (the normalised ArcFace vector from the recognition
    model) is the same value face extraction stores in ``Face.features`` as
    ``embedding.astype("float32").tobytes()``, so scoring an uploaded image this
    way is bit-for-bit identical to scoring a stored picture — without writing
    any ``Picture``/``Face`` rows.
    """

    __slots__ = ("id", "features")

    def __init__(self, face_id: int, embedding):
        self.id = face_id
        self.features = np.asarray(embedding, dtype=np.float32).tobytes()


class PictureFaceResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    picture_id: Optional[int] = None
    frame_index: Optional[int] = None
    face_index: Optional[int] = None
    bbox: Optional[list] = None
    character_id: Optional[int] = None


class FaceDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    message: str


def register_routes(router, server):
    @router.post(
        "/pictures/{id}/face",
        include_in_schema=False,
        summary="Create manual face entry",
        description="Adds a face bounding box to a picture and frame index, updating sentinel/ordering behavior for manual annotations.",
        response_model=PictureFaceResponse,
    )
    def create_picture_face(request: Request, id: str, payload: dict = Body(...)):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        bbox = payload.get("bbox") if isinstance(payload, dict) else None
        frame_index = payload.get("frame_index", 0) if isinstance(payload, dict) else 0
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise HTTPException(status_code=400, detail="bbox must be [x1, y1, x2, y2]")
        try:
            bbox_vals = [int(round(float(v))) for v in bbox]
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="bbox values must be numbers")
        try:
            frame_index = int(frame_index)
        except (TypeError, ValueError):
            frame_index = 0

        def create_face(session: Session):
            pic = session.get(Picture, pic_id)
            if not pic:
                return None
            sentinel = session.exec(
                select(Face).where(
                    Face.picture_id == pic_id,
                    Face.frame_index == frame_index,
                    Face.face_index == -1,
                )
            ).first()
            if sentinel is not None:
                session.delete(sentinel)
            max_index = session.exec(
                select(func.max(Face.face_index)).where(
                    Face.picture_id == pic_id,
                    Face.frame_index == frame_index,
                )
            ).one()
            next_index = (max_index or 0) + 1 if max_index is not None else 0
            face = Face(
                picture_id=pic_id,
                frame_index=frame_index,
                face_index=next_index,
                bbox=bbox_vals,
            )
            session.add(face)
            session.commit()
            session.refresh(face)
            return face

        face = server.vault.db.run_task(create_face, priority=DBPriority.IMMEDIATE)
        if not face:
            raise HTTPException(status_code=404, detail="Picture not found")
        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": [pic_id],
                "origin_client_id": origin_client_id,
                "change_kind": "updated",
            },
        )
        return safe_model_dict(face)

    @router.delete(
        "/pictures/{id}/face/{index}",
        include_in_schema=False,
        summary="Delete face by index",
        description="Deletes a face at frame 0 by index and reindexes remaining faces for stable ordering.",
        response_model=FaceDeleteResponse,
    )
    def delete_picture_face(request: Request, id: str, index: int):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        def delete_face(session: Session):
            face = session.exec(
                select(Face).where(
                    Face.picture_id == pic_id,
                    Face.frame_index == 0,
                    Face.face_index == index,
                )
            ).first()
            if not face:
                return False
            session.delete(face)
            remaining = session.exec(
                select(Face)
                .where(
                    Face.picture_id == pic_id,
                    Face.frame_index == 0,
                    Face.face_index >= 0,
                )
                .order_by(Face.face_index, Face.id)
            ).all()
            for next_idx, entry in enumerate(remaining):
                if entry.face_index != next_idx:
                    entry.face_index = next_idx
                    session.add(entry)
            if not remaining:
                sentinel = session.exec(
                    select(Face).where(
                        Face.picture_id == pic_id,
                        Face.frame_index == 0,
                        Face.face_index == -1,
                    )
                ).first()
                if sentinel is None:
                    session.add(
                        Face(
                            picture_id=pic_id,
                            frame_index=0,
                            face_index=-1,
                            character_id=None,
                            bbox=None,
                        )
                    )
            session.commit()
            return True

        deleted = server.vault.db.run_task(delete_face, priority=DBPriority.IMMEDIATE)
        if not deleted:
            raise HTTPException(status_code=404, detail="Face not found")
        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": [pic_id],
                "origin_client_id": origin_client_id,
                "change_kind": "updated",
            },
        )
        return {"status": "success", "message": "Face deleted."}
