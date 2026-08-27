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
    way is bit-for-bit identical to scoring a stored picture - without writing
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


class FaceProjectionResponse(BaseModel):
    """One face row as served by the dedicated face-listing routes.

    Deliberately **not** ``extra="allow"``: this model is the second of the two
    filters on these routes (the handler's ``Face.to_public_dict`` is the
    first), so an added ``Face`` column cannot ride onto the wire by accident.
    ``extra="allow"`` here would make the response model decorative, which is
    the containment failure §16.6 records for the picture payload models.
    """

    model_config = ConfigDict(extra="ignore")

    id: Optional[int] = None
    picture_id: Optional[int] = None
    character_id: Optional[int] = None
    frame_index: Optional[int] = None
    face_index: Optional[int] = None
    bbox: Optional[list] = None


class FaceListResponse(BaseModel):
    """``{"faces": [...]}`` - the wire shape the generic by-name reader used.

    Kept identical on purpose so the SPA (``api/pictures.js::listPictureFaces``)
    and ``tests/utils.py::wait_for_faces`` need no change when #721 moved these
    off the relationship.
    """

    model_config = ConfigDict(extra="ignore")

    faces: list[FaceProjectionResponse]


class FaceDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    message: str


def register_routes(router, server):
    @router.get(
        "/pictures/{id}/faces",
        summary="List picture faces",
        description=(
            "Returns the stored face rows for a picture: `id`, `picture_id`, "
            "`character_id`, `frame_index`, `face_index` and the pixel `xyxy` "
            "`bbox`.\n\n"
            "The face **embedding** (`features`) and the embedding's model pack "
            "are not served. Rows are returned in insertion order, and include "
            "the `face_index = -1` sentinel written when extraction found no "
            "face, so a caller can tell 'extraction ran and found nothing' from "
            "'extraction has not run yet'."
        ),
        response_model=FaceListResponse,
    )
    def list_picture_faces(request: Request, id: str):
        # Dedicated, projected replacement for `GET /pictures/{id}/{field}` with
        # field="faces", which served the ORM relationship and therefore the
        # embedding too (issue #721).
        #
        # ORDERING IS LOAD-BEARING: this module is registered BEFORE `_crud` in
        # `routes/pictures/__init__.py`, which is what stops the
        # `/pictures/{id}/{field}` catch-all in `_crud` from swallowing this
        # route. Moving `_faces.register_routes` after `_crud.register_routes`
        # makes this handler dead code that silently never runs.
        try:
            pic_id = int(id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid picture id") from exc

        def fetch_faces(session: Session):
            # Every face row for the picture, sentinels included, ordered by id.
            # This reproduces the row set and the order the `Picture.faces`
            # relationship produced. `Face.find` is NOT used: it filters out
            # `face_index == -1`, and `tests/utils.py::wait_for_faces` returns as
            # soon as the list is non-empty, so dropping the sentinel would turn
            # a "no face detected" picture into a full poll timeout.
            rows = session.exec(
                select(Face).where(Face.picture_id == pic_id).order_by(Face.id)
            ).all()
            return [face.to_public_dict() for face in rows]

        return {"faces": server.vault.db.run_immediate_read_task(fetch_faces)}

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
