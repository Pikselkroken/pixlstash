"""Character face-assignment endpoints.

The two handlers that assign / unassign faces to a character
(``POST`` and ``DELETE`` on ``/characters/{character_id}/faces``) live here,
split out of :mod:`pixlstash.routes.characters` to keep that module focused on
character CRUD and search. Behaviour, paths, and methods are unchanged; the
router is mounted adjacently to the characters router in ``server.py``.

Scope enforcement for these mutations is handled by
:func:`_enforce_face_mutation_scope`, which resolves both the ``face_ids`` and
``picture_ids`` branches to the affected picture set and denies the whole request
if any targeted picture falls outside a scoped token's grant (BOLA guard).
"""

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureProjectMember,
)
from pixlstash.event_types import EventType
from pixlstash.picture_scoring import (
    compute_character_likeness_for_faces,
    select_reference_faces_for_character,
)
from pixlstash.services.stack_membership import expand_picture_ids_to_stacks
from pixlstash.utils.service.filter_helpers import fetch_scope_allowed_picture_ids


class CharacterFaceAssignmentResponse(BaseModel):
    """Result of assigning or unassigning faces for a character."""

    model_config = ConfigDict(extra="allow")

    status: str
    face_ids: Optional[list[int]] = None
    character_id: int
    already_assigned_ids: Optional[list[int]] = None


def _enforce_face_mutation_scope(
    server,
    request,
    *,
    face_ids: list | None,
    picture_ids: list | None,
) -> None:
    """Raise 403 if a scoped token targets faces/pictures outside its scope.

    The character face-assign / face-unassign handlers accept *either* a list
    of ``face_ids`` *or* a list of ``picture_ids``. This resolves both paths to
    the full set of affected picture ids and checks every one against the
    token's scope. Owner / unscoped tokens (``fetch_scope_allowed_picture_ids``
    returns ``None``) pass straight through. This is all-or-nothing: if *any*
    targeted picture is out of scope the whole request is denied, so neither the
    ``face_ids`` branch nor the ``picture_ids`` branch can mutate an
    out-of-scope picture.
    """
    scope_allowed = fetch_scope_allowed_picture_ids(server, request)
    if scope_allowed is None:
        return

    affected: set[int] = set()
    for raw in picture_ids or []:
        try:
            affected.add(int(raw))
        except (TypeError, ValueError):
            continue

    normalized_face_ids: list[int] = []
    for raw in face_ids or []:
        try:
            normalized_face_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if normalized_face_ids:

        def _resolve(session: Session, ids: list[int]) -> set[int]:
            rows = session.exec(select(Face.picture_id).where(Face.id.in_(ids))).all()
            return {int(r) for r in rows if r is not None}

        affected |= server.vault.db.run_immediate_read_task(
            _resolve, normalized_face_ids
        )

    if any(pid not in scope_allowed for pid in affected):
        raise HTTPException(
            status_code=403,
            detail="Token is not authorised to access these pictures",
        )


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/characters/{character_id}/faces",
        summary="Assign faces to character",
        description="Assigns provided face ids or largest faces from picture ids to a character.",
        response_model=CharacterFaceAssignmentResponse,
    )
    def assign_face_to_character(
        request: Request, character_id: int, payload: dict = Body(...)
    ):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        face_ids = payload.get("face_ids")
        picture_ids = payload.get("picture_ids")
        if face_ids is not None and not isinstance(face_ids, list):
            raise HTTPException(status_code=400, detail="face_ids must be a list")
        if picture_ids is not None and not isinstance(picture_ids, list):
            raise HTTPException(status_code=400, detail="picture_ids must be a list")
        # Scope guard (BOLA): a write-capable resource-scoped token may only
        # assign faces on pictures within its granted resource. Covers both the
        # face_ids and picture_ids branches.
        _enforce_face_mutation_scope(
            server, request, face_ids=face_ids, picture_ids=picture_ids
        )

        def assign_faces(
            session: Session,
            face_ids: list[int],
            picture_ids: list[str],
            character_id: int,
        ):
            faces_to_assign = []
            existing_faces = []
            if picture_ids:
                # Stacks move as a unit: assigning any stacked picture to a
                # character assigns every member of its stack, so a collapsed
                # stack dragged onto a character moves all of its pictures
                # (reassigning each member's face also moves it off the old
                # character, keeping character counts consistent).
                picture_ids = expand_picture_ids_to_stacks(session, picture_ids)
                reference_faces = select_reference_faces_for_character(
                    session, character_id
                )

                def face_area(face):
                    try:
                        return (face.width or 0) * (face.height or 0)
                    except Exception:
                        # Sort-key guard: a face missing usable dimensions sorts
                        # as zero area; 0 IS the answer, not an error.
                        return 0

                for pic_id in picture_ids:
                    faces = Face.find(session, picture_id=pic_id)
                    if not faces:
                        # Face.find excludes sentinel records (face_index == -1),
                        # so an empty result means either extraction hasn't run yet
                        # or ran and found nothing.  Check for any record at all.
                        any_face_id = session.exec(
                            select(Face.id).where(Face.picture_id == pic_id).limit(1)
                        ).first()
                        if any_face_id is None:
                            # Extraction not yet run; defer assignment until it does.
                            pic = session.get(Picture, pic_id)
                            if pic is not None:
                                pic.pending_character_id = character_id
                                session.add(pic)
                        continue

                    if reference_faces:
                        faces_with_features = [f for f in faces if f.features]
                        if faces_with_features:
                            likeness_map = compute_character_likeness_for_faces(
                                reference_faces, faces_with_features
                            )
                            best_face = max(
                                faces_with_features,
                                key=lambda f: (
                                    likeness_map.get(f.id, 0.0),
                                    face_area(f),
                                ),
                            )
                        else:
                            best_face = max(faces, key=face_area)
                    else:
                        best_face = max(faces, key=face_area)

                    if best_face.character_id == character_id:
                        existing_faces.append(best_face)
                    else:
                        faces_to_assign.append(best_face)
            if face_ids:
                for face_id in face_ids:
                    face = session.get(Face, face_id)
                    if not face:
                        raise HTTPException(
                            status_code=404, detail=f"Face {face_id} not found"
                        )
                    if face.character_id == character_id:
                        existing_faces.append(face)
                    else:
                        faces_to_assign.append(face)
            unique_faces = {face.id: face for face in faces_to_assign}.values()
            for face in unique_faces:
                face.character_id = character_id
                session.add(face)
            session.commit()
            for face in unique_faces:
                session.refresh(face)
            character = session.get(Character, character_id)
            if character and character.project_id is not None:
                for face in unique_faces:
                    if face.picture_id:
                        pic = session.get(Picture, face.picture_id)
                        if pic:
                            membership = session.exec(
                                select(PictureProjectMember).where(
                                    PictureProjectMember.picture_id == pic.id,
                                    PictureProjectMember.project_id
                                    == character.project_id,
                                )
                            ).first()
                            if membership is None:
                                session.add(
                                    PictureProjectMember(
                                        picture_id=pic.id,
                                        project_id=character.project_id,
                                    )
                                )
                            if pic.project_id is None:
                                pic.project_id = character.project_id
                                session.add(pic)
                if any(f.picture_id for f in unique_faces):
                    session.commit()
            faces_payload = [
                {
                    "id": face.id,
                    "picture_id": face.picture_id,
                    "character_id": face.character_id,
                }
                for face in unique_faces
            ]
            existing_face_ids = [face.id for face in existing_faces]
            return faces_payload, existing_face_ids

        faces, existing_face_ids = server.vault.db.run_task(
            assign_faces,
            face_ids,
            picture_ids,
            character_id,
            priority=DBPriority.IMMEDIATE,
        )
        if not faces and len(existing_face_ids) > 0:
            # All requested faces are already assigned to this character — the
            # desired state is already achieved.  Return success so callers
            # (e.g. the ComfyUI node re-importing a duplicate picture) do not
            # treat this as an error.
            return {
                "status": "success",
                "face_ids": [],
                "character_id": character_id,
                "already_assigned_ids": existing_face_ids,
            }
        server.vault.db.run_task(
            Picture.clear_field,
            [face["picture_id"] for face in faces],
            "text_embedding",
        )
        for face in faces:
            if face["character_id"] != character_id:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"Failed to set character {character_id} for face {face['id']}"
                    ),
                )
        server.vault.notify(
            EventType.CHANGED_CHARACTERS, {"origin_client_id": origin_client_id}
        )
        # CHANGED_FACES serializes to the ``characters_changed`` wire type, whose
        # payload carries no picture_ids/change_kind (the frontend reacts with a
        # sidebar refresh). Only origin_client_id is read into the envelope.
        server.vault.notify(
            EventType.CHANGED_FACES,
            {"origin_client_id": origin_client_id},
        )
        return {
            "status": "success",
            "face_ids": [face["id"] for face in faces],
            "character_id": character_id,
        }

    @router.delete(
        "/characters/{character_id}/faces",
        summary="Unassign faces from character",
        description="Removes character assignment from provided face ids or from faces in provided picture ids.",
        response_model=CharacterFaceAssignmentResponse,
    )
    def remove_character_from_faces(
        request: Request, character_id: int, payload: dict = Body(...)
    ):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        face_ids = payload.get("face_ids", None)
        picture_ids = payload.get("picture_ids", None)
        if not isinstance(face_ids, list) and not isinstance(picture_ids, list):
            raise HTTPException(
                status_code=400,
                detail="Must send a list of picture_ids or face_ids",
            )
        # Scope guard (BOLA): a write-capable resource-scoped token may only
        # unassign faces on pictures within its granted resource. Covers both
        # the face_ids and picture_ids branches.
        _enforce_face_mutation_scope(
            server, request, face_ids=face_ids, picture_ids=picture_ids
        )

        def remove_faces_from_character(
            session: Session,
            character_id: int,
            face_ids: list[int] = None,
            picture_ids: list[str] = None,
        ):
            faces = []
            if picture_ids:
                for pic_id in picture_ids:
                    pic_faces = Face.find(session, picture_id=pic_id)
                    for face in pic_faces:
                        if face.character_id == character_id:
                            face.character_id = None
                            session.add(face)
                            faces.append(face)
            elif face_ids:
                for face_id in face_ids:
                    face = session.get(Face, face_id)
                    if face and face.character_id == character_id:
                        face.character_id = None
                        session.add(face)
            session.commit()
            session.refresh(face)
            return faces

        server.vault.db.run_task(
            remove_faces_from_character,
            character_id,
            face_ids,
            picture_ids,
            priority=DBPriority.IMMEDIATE,
        )

        server.vault.db.run_task(Picture.clear_field, picture_ids, "text_embedding")
        server.vault.notify(
            EventType.CHANGED_CHARACTERS, {"origin_client_id": origin_client_id}
        )
        # CHANGED_FACES serializes to the ``characters_changed`` wire type, whose
        # payload carries no picture_ids/change_kind (the frontend reacts with a
        # sidebar refresh). Only origin_client_id is read into the envelope.
        server.vault.notify(
            EventType.CHANGED_FACES,
            {"origin_client_id": origin_client_id},
        )
        return {
            "status": "success",
            "face_ids": face_ids,
            "character_id": character_id,
        }

    return router
