import ast
import asyncio
import json
import os
import random as _random
import time
from io import BytesIO
from typing import List, Optional

import cv2
import numpy as np
from fastapi import (
    APIRouter,
    Body,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, ConfigDict
from sqlalchemy import case as sa_case, exists, func
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureProjectMember,
    Project,
    PictureSet,
    PictureSetMember,
    Tag,
)
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.routes._helpers import picture_referenced_by_project
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.picture_scoring import (
    compute_character_likeness_for_faces,
    select_reference_faces_for_character,
)
from pixlstash.utils.service.caption_utils import normalize_hidden_tags
from pixlstash.utils.service.filter_helpers import (
    combine_likeness_scores,
    fetch_scope_allowed_character_ids,
    VALID_COMBINE_MODES,
)
from pixlstash.utils.service.path_utils import resolve_path_within
from pixlstash.utils.service.serialization_utils import safe_model_dict

logger = get_logger(__name__)

_UNSET = object()

_LIKENESS_SEARCH_DEFAULT_TOP_N = 20
_LIKENESS_SEARCH_MAX_TOP_N = 500
_LIKENESS_SEARCH_MAX_POOL_M = 2000
# Maximum reference faces loaded per character for query-time likeness scoring.
_MAX_REFS_PER_CHARACTER = 10


def _fetch_character_candidate_embeddings(
    server, scope_allowed: set[int] | None
) -> list[tuple[int, list[np.ndarray]]]:
    """Fetch reference face embeddings for all candidate characters.

    Args:
        server: The server instance providing DB access.
        scope_allowed: Optional set of character IDs to restrict results to.
            ``None`` means all characters are eligible.

    Returns:
        A list of ``(character_id, [embedding, ...])`` tuples.  Characters
        with no usable face embeddings are excluded.
    """

    def _fetch(session) -> list[tuple[int, list[np.ndarray]]]:
        from sqlalchemy import select as sa_select

        query = (
            sa_select(Face.character_id, Face.features)
            .join(Picture, Face.picture_id == Picture.id)
            .where(
                Face.character_id.is_not(None),
                Face.features.is_not(None),
                Picture.deleted.is_(False),
            )
        )
        if scope_allowed is not None:
            if not scope_allowed:
                return []
            query = query.where(Face.character_id.in_(scope_allowed))

        rows = session.execute(query).all()

        char_embs: dict[int, list[np.ndarray]] = {}
        for char_id, features in rows:
            char_id = int(char_id)
            if len(char_embs.get(char_id, [])) >= _MAX_REFS_PER_CHARACTER:
                continue
            emb = np.frombuffer(features, dtype=np.float32).copy()
            if emb.size > 0:
                char_embs.setdefault(char_id, []).append(emb)

        return list(char_embs.items())

    return server.vault.db.run_immediate_read_task(_fetch)


def _compute_character_query_likeness(
    query_emb: np.ndarray, ref_embs: list[np.ndarray]
) -> float:
    """Compute softmax-weighted cosine similarity of a query face against reference faces.

    Uses the same alpha=5 softmax weighting as the database
    ``character_face_likeness`` scalar function so scores are consistent.

    Args:
        query_emb: Normalised query face embedding (float32 array).
        ref_embs: List of reference face embeddings for one character.

    Returns:
        Softmax-weighted cosine similarity in ``[-1, 1]``, or ``0.0`` on
        any error.
    """
    if not ref_embs:
        return 0.0
    ref = np.stack(ref_embs)
    ref_norm = ref / np.maximum(np.linalg.norm(ref, axis=1, keepdims=True), 1e-8)
    sims = ref_norm @ query_emb  # (n_refs,)
    sims = np.clip(sims, -1.0, 1.0)
    alpha = 5.0
    weights = np.exp(alpha * sims)
    denom = weights.sum()
    if denom < 1e-8:
        return 0.0
    return float((weights * sims).sum() / denom)


class CharacterResponse(BaseModel):
    """A single character record (scalar fields of the Character model)."""

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    extra_metadata: Optional[str] = None
    reference_picture_set_id: Optional[int] = None
    project_id: Optional[int] = None


class CharacterListItemResponse(BaseModel):
    """A character in the list endpoint, annotated with reference-face presence."""

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    extra_metadata: Optional[str] = None
    reference_picture_set_id: Optional[int] = None
    project_id: Optional[int] = None
    has_reference_faces: bool = False


class CharacterSummaryResponse(BaseModel):
    """Category summary counts and thumbnail reference for a character/category."""

    model_config = ConfigDict(extra="allow")

    character_id: Optional[int] = None
    image_count: int = 0
    thumbnail_url: Optional[str] = None


class CharacterReferencePicturesResponse(BaseModel):
    """Reference picture ids selected for a character."""

    model_config = ConfigDict(extra="allow")

    reference_picture_ids: list[int] = []


class CharacterMutationResponse(BaseModel):
    """Result of creating or updating a character."""

    model_config = ConfigDict(extra="allow")

    status: str
    character: Optional[CharacterResponse] = None


class CharacterDeleteResponse(BaseModel):
    """Result of deleting a character."""

    model_config = ConfigDict(extra="allow")

    status: str
    deleted_id: int


class CharacterMembershipResponse(BaseModel):
    """Batch character membership lookup result."""

    model_config = ConfigDict(extra="allow")

    character_assignments: dict[str, list[int]] = {}
    pictures_with_faces: list[int] = []


class CharacterFaceAssignmentResponse(BaseModel):
    """Result of assigning or unassigning faces for a character."""

    model_config = ConfigDict(extra="allow")

    status: str
    face_ids: Optional[list[int]] = None
    character_id: int
    already_assigned_ids: Optional[list[int]] = None


class CharacterLikenessResultResponse(BaseModel):
    """A single character likeness-search result."""

    model_config = ConfigDict(extra="allow")

    character_id: int
    likeness: float


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _ensure_unique_character_name(
        session, name: str, project_id, exclude_char_id=None
    ):
        """Raises 409 if a character with the same name (case-insensitive)
        already exists in the given project.  Unscoped characters
        (project_id None) are exempt.
        """
        if project_id is None:
            return
        stmt = select(Character).where(
            Character.project_id == project_id,
            func.lower(Character.name) == name.lower(),
        )
        if exclude_char_id is not None:
            stmt = stmt.where(Character.id != exclude_char_id)
        if session.exec(stmt).first():
            raise HTTPException(
                status_code=409,
                detail=f"A character named '{name}' already exists in this project.",
            )

    def _project_membership_exists(project_id_value: int):
        return exists(
            select(PictureProjectMember.picture_id).where(
                PictureProjectMember.picture_id == Picture.id,
                PictureProjectMember.project_id == project_id_value,
            )
        )

    def _project_unassigned_membership():
        return ~exists(
            select(PictureProjectMember.picture_id).where(
                PictureProjectMember.picture_id == Picture.id
            )
        )

    def _require_scope_allows_character(request: Request, character_id: int):
        """Raise 403 if the token scope does not cover the requested character."""
        scope = getattr(request.state, "token_scope", None)
        if scope is None:
            return
        if scope.resource_type == "character":
            if scope.resource_id != character_id:
                raise HTTPException(
                    status_code=403,
                    detail="Token is not authorised for this character",
                )
        elif scope.resource_type == "project":

            def check_char_project(session: Session, cid: int, pid: int) -> bool:
                char = session.get(Character, cid)
                return char is not None and char.project_id == pid

            if not server.vault.db.run_immediate_read_task(
                check_char_project, character_id, scope.resource_id
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Token is not authorised for this character",
                )
        elif scope.resource_type is not None:
            raise HTTPException(
                status_code=403,
                detail="Token is not authorised for this resource type",
            )

    def _get_hidden_tags_from_request(request: Request) -> list[str]:
        if request.query_params.get("apply_tag_filter", "").lower() != "true":
            return []
        try:
            user = server.auth.get_user_for_request(request)
        except HTTPException:
            user = server.auth.get_user()
        if not user:
            return []
        normalized = normalize_hidden_tags(getattr(user, "hidden_tags", None))
        return normalized or []

    @router.get(
        "/characters/{id}/summary",
        summary="Get character category summary",
        description="Returns summary counts and thumbnail reference for ALL, UNASSIGNED, SCRAPHEAP, or a specific character id.",
        response_model=CharacterSummaryResponse,
    )
    def get_characters_summary(
        request: Request,
        id: str = None,
        project_id: str | None = Query(default=None),
    ):
        """
        Return summary statistics for a single category:
        - If character_id is ALL: all pictures
        - If character_id is UNASSIGNED: unassigned pictures
        - If character_id is set: that character's pictures
        """
        start = time.time()
        hidden_tags = _get_hidden_tags_from_request(request)
        hidden_tag_set = {str(tag).strip().lower() for tag in hidden_tags if tag}
        hidden_tag_filter = None
        if hidden_tag_set:
            hidden_tag_filter = ~exists(
                select(Tag.id).where(
                    Tag.picture_id == Picture.id,
                    Tag.tag.is_not(None),
                    func.lower(Tag.tag).in_(hidden_tag_set),
                )
            )

        if id == "ALL":

            def count_all(session: Session) -> int:
                conditions = [
                    Picture.deleted.is_(False),
                ]
                if hidden_tag_filter is not None:
                    conditions.append(hidden_tag_filter)
                return session.exec(
                    select(func.count(Picture.id)).where(*conditions)
                ).one()

            image_count = server.vault.db.run_immediate_read_task(count_all)
            logger.debug("ALL pics count: {}".format(image_count))
            char_id = None
        elif id == "SCRAPHEAP":

            def count_scrapheap(session: Session) -> int:
                conditions = [
                    Picture.deleted.is_(True),
                ]
                if hidden_tag_filter is not None:
                    conditions.append(hidden_tag_filter)
                return session.exec(
                    select(func.count(Picture.id)).where(*conditions)
                ).one()

            image_count = server.vault.db.run_immediate_read_task(count_scrapheap)
            logger.debug("SCRAPHEAP pics count: {}".format(image_count))
            char_id = None
        elif id == "UNASSIGNED":
            unassigned_project_id: int | None = None
            unassigned_project_only = False
            if project_id == "UNASSIGNED":
                unassigned_project_only = True
            elif project_id is not None:
                try:
                    unassigned_project_id = int(project_id)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="Invalid project_id")

            def count_unassigned(session: Session) -> int:
                unassigned_conditions = Picture.build_unassigned_conditions(
                    enforce_stack_assignment=True,
                    assignment_project_id=unassigned_project_id,
                    assignment_unassigned_project=unassigned_project_only,
                )
                conditions = [
                    Picture.deleted.is_(False),
                    *unassigned_conditions,
                ]
                if unassigned_project_only:
                    conditions.append(_project_unassigned_membership())
                elif unassigned_project_id is not None:
                    conditions.append(_project_membership_exists(unassigned_project_id))
                if hidden_tag_filter is not None:
                    conditions.append(hidden_tag_filter)
                return session.exec(
                    select(func.count(Picture.id)).where(*conditions)
                ).one()

            image_count = server.vault.db.run_immediate_read_task(count_unassigned)
            logger.debug("UNASSIGNED pics count: {}".format(image_count))
            char_id = None
        else:
            assigned_project_id: int | None = None
            assigned_project_unassigned = False
            if project_id == "UNASSIGNED":
                assigned_project_unassigned = True
            elif project_id is not None:
                try:
                    assigned_project_id = int(project_id)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="Invalid project_id")

            def count_assigned(session: Session, character_id: int) -> int:
                conditions = [
                    Face.character_id == character_id,
                    Picture.deleted.is_(False),
                ]
                if assigned_project_unassigned:
                    conditions.append(_project_unassigned_membership())
                elif assigned_project_id is not None:
                    conditions.append(_project_membership_exists(assigned_project_id))
                if hidden_tag_filter is not None:
                    conditions.append(hidden_tag_filter)
                return session.exec(
                    select(func.count(func.distinct(Face.picture_id)))
                    .join(Picture, Face.picture_id == Picture.id)
                    .where(*conditions)
                ).one()

            image_count = server.vault.db.run_immediate_read_task(
                count_assigned, character_id=int(id)
            )
            char_id = int(id)

        if char_id:
            thumb_url = None
            if char_id not in (None, "", "null"):
                thumb_url = f"/characters/{char_id}/thumbnail"
        else:
            thumb_url = None

        summary = {
            "character_id": char_id,
            "image_count": image_count,
            "thumbnail_url": thumb_url,
        }
        elapsed = time.time() - start
        logger.debug(f"Category summary computed in {elapsed:.4f} seconds")
        logger.debug(f"Category summary: {summary}")
        return summary

    @router.get(
        "/characters/{id}/reference_pictures",
        summary="List reference pictures",
        description="Returns picture ids selected as reference faces for the given character.",
        response_model=CharacterReferencePicturesResponse,
    )
    def get_character_reference_pictures(request: Request, id: int):
        """Return reference picture ids for a character.

        Args:
            id: Character id to fetch reference pictures for.

        Returns:
            A dict containing reference picture ids.
        """
        _require_scope_allows_character(request, id)

        def fetch_reference_pictures(session: Session, character_id: int):
            faces = select_reference_faces_for_character(
                session,
                character_id=character_id,
                max_refs=10,
            )
            picture_ids = []
            seen = set()
            for face in faces:
                pic_id = getattr(face, "picture_id", None)
                if pic_id is None or pic_id in seen:
                    continue
                seen.add(pic_id)
                picture_ids.append(pic_id)
            return picture_ids

        picture_ids = server.vault.db.run_task(
            fetch_reference_pictures,
            id,
            priority=DBPriority.IMMEDIATE,
        )
        logger.info(
            "[reference_pictures] character_id=%s picture_ids=%s",
            id,
            picture_ids,
        )
        return {"reference_picture_ids": picture_ids}

    @router.patch(
        "/characters/{id}",
        summary="Update character",
        description="Updates character fields and clears dependent picture text embeddings when identity data changes.",
        response_model=CharacterMutationResponse,
    )
    async def patch_character(id: int, request: Request):
        data = await request.json()
        name = data.get("name")
        description = data.get("description")
        raw_project_id = data.get("project_id", _UNSET)
        project_id = raw_project_id
        if raw_project_id is not _UNSET:
            if raw_project_id is None:
                project_id = None
            else:
                try:
                    project_id = int(raw_project_id)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid project_id",
                    ) from exc
        char = None
        project_membership_updated = False
        try:

            def alter_char(
                session: Session, id: int, name: str, description: str, project_id
            ):
                character = session.get(Character, id)
                if character is None:
                    raise KeyError("Character not found")
                # Capture the project the character is leaving before we mutate
                # it, so we can disassociate its pictures from that old project.
                old_project_id = character.project_id
                # Check uniqueness before mutating anything.
                final_name = name if name is not None else character.name
                final_project_id = (
                    project_id if project_id is not _UNSET else character.project_id
                )
                name_changing = name is not None and name != character.name
                project_changing = (
                    project_id is not _UNSET and project_id != character.project_id
                )
                if name_changing or project_changing:
                    _ensure_unique_character_name(
                        session, final_name, final_project_id, exclude_char_id=id
                    )
                updated = False
                if name is not None and name != character.name:
                    character.name = name
                    updated = True
                if description is not None and description != character.description:
                    character.description = description
                    updated = True
                project_id_changed = (
                    project_id is not _UNSET and project_id != character.project_id
                )
                if project_id_changed:
                    if project_id is not None:
                        project = session.get(Project, project_id)
                        if project is None:
                            raise HTTPException(
                                status_code=404,
                                detail="Project not found",
                            )
                    character.project_id = project_id
                    updated = True
                local_project_membership_updated = False
                if updated:
                    session.add(character)

                    if project_id_changed:
                        picture_ids = list(
                            {
                                face.picture_id
                                for face in session.exec(
                                    select(Face).where(Face.character_id == id)
                                ).all()
                                if face.picture_id is not None
                            }
                        )
                        for pic in session.exec(
                            select(Picture).where(Picture.id.in_(picture_ids))
                        ).all():
                            # Associate the picture with the new project.
                            if project_id is not None:
                                membership = session.exec(
                                    select(PictureProjectMember).where(
                                        PictureProjectMember.picture_id == pic.id,
                                        PictureProjectMember.project_id == project_id,
                                    )
                                ).first()
                                if membership is None:
                                    session.add(
                                        PictureProjectMember(
                                            picture_id=pic.id,
                                            project_id=project_id,
                                        )
                                    )
                            # Disassociate the picture from the old project,
                            # unless another character or picture set still
                            # assigned to that project anchors it there.
                            if (
                                old_project_id is not None
                                and old_project_id != project_id
                                and not picture_referenced_by_project(
                                    session,
                                    pic.id,
                                    old_project_id,
                                    exclude_character_id=id,
                                )
                            ):
                                old_membership = session.exec(
                                    select(PictureProjectMember).where(
                                        PictureProjectMember.picture_id == pic.id,
                                        PictureProjectMember.project_id
                                        == old_project_id,
                                    )
                                ).first()
                                if old_membership is not None:
                                    session.delete(old_membership)
                            # Update the picture's primary project pointer.
                            if project_id is not None:
                                pic.project_id = project_id
                            elif pic.project_id == old_project_id:
                                # Character left the project entirely; fall back
                                # to any project the picture still belongs to.
                                session.flush()
                                fallback_project_id = session.exec(
                                    select(PictureProjectMember.project_id)
                                    .where(PictureProjectMember.picture_id == pic.id)
                                    .order_by(PictureProjectMember.project_id.asc())
                                ).first()
                                pic.project_id = (
                                    int(fallback_project_id)
                                    if fallback_project_id is not None
                                    else None
                                )
                            session.add(pic)
                        local_project_membership_updated = bool(picture_ids)

                    # Clear text embeddings for all pictures of this character
                    for face in session.exec(
                        select(Face).where(Face.character_id == id)
                    ).all():
                        pic = session.get(Picture, face.picture_id)
                        if pic:
                            pic.description = None
                            pic.text_embedding = None
                            session.add(pic)

                    session.commit()
                    session.refresh(character)
                # Serialize while the session is open; the row may be detached
                # (and its attributes expired) by the time the handler returns.
                return (
                    character.model_dump(exclude_unset=False),
                    local_project_membership_updated,
                )

            char, project_membership_updated = server.vault.db.run_task(
                alter_char,
                id,
                name,
                description,
                project_id,
                priority=DBPriority.IMMEDIATE,
            )
            server.vault.notify(EventType.CHANGED_CHARACTERS)
            if project_membership_updated:
                server.vault.notify(EventType.CHANGED_PICTURES)

        except KeyError:
            raise HTTPException(status_code=404, detail="Character not found")

        return {"status": "success", "character": char}

    @router.delete(
        "/characters/{id}",
        summary="Delete character",
        description="Deletes a character, clears character assignment from faces, and removes its reference set when present.",
        response_model=CharacterDeleteResponse,
    )
    def delete_character(id: int):
        try:

            def clear_character_and_nullify_faces(session: Session, character_id: int):
                character = session.get(Character, character_id)
                if character is None:
                    raise KeyError("Character not found")
                reference_set_id = character.reference_picture_set_id
                faces = session.exec(
                    select(Face).where(Face.character_id == character_id)
                ).all()
                for face in faces:
                    face.character_id = None
                    session.add(face)
                session.commit()
                session.delete(character)
                session.commit()

                if reference_set_id is None:
                    return

                members = session.exec(
                    select(PictureSetMember).where(
                        PictureSetMember.set_id == reference_set_id
                    )
                ).all()
                for member in members:
                    session.delete(member)

                reference_set = session.get(PictureSet, reference_set_id)
                if reference_set is not None:
                    session.delete(reference_set)
                session.commit()

            server.vault.db.run_task(
                clear_character_and_nullify_faces,
                id,
                priority=DBPriority.IMMEDIATE,
            )
            server.vault.notify(EventType.CHANGED_CHARACTERS)
            return {"status": "success", "deleted_id": id}
        except KeyError:
            raise HTTPException(status_code=404, detail="Character not found")

    @router.post(
        "/characters/membership",
        summary="Batch character membership lookup",
        description=(
            "Given a list of picture IDs, returns character_assignments "
            "(character_id → [picture_ids]) and pictures_with_faces ([picture_ids]). "
            "Used by the AddToCharacter menu to load membership in a single request."
        ),
        response_model=CharacterMembershipResponse,
    )
    def get_batch_character_membership(
        picture_ids: list[int] = Body(default=[], embed=True),
    ):
        if not picture_ids:
            return {"character_assignments": {}, "pictures_with_faces": []}

        def fetch(session, ids: list[int]):
            rows = session.exec(
                select(Face.character_id, Face.picture_id).where(
                    Face.picture_id.in_(ids),
                    Face.face_index != -1,
                )
            ).all()
            assignments: dict[int, list[int]] = {}
            pictures_with_faces: set[int] = set()
            for character_id, pid in rows:
                pictures_with_faces.add(int(pid))
                if character_id is not None:
                    assignments.setdefault(int(character_id), []).append(int(pid))
            return {
                "character_assignments": assignments,
                "pictures_with_faces": sorted(pictures_with_faces),
            }

        return server.vault.db.run_immediate_read_task(fetch, picture_ids)

    @router.get(
        "/characters/{id}",
        summary="Get character by id",
        description="Returns a single character record by id.",
        response_model=Optional[CharacterResponse],
    )
    def get_character_by_id(request: Request, id: int):
        _require_scope_allows_character(request, id)
        try:
            char = server.vault.db.run_immediate_read_task(
                lambda session: Character.find(session, id=id)
            )
            return char[0] if char else None
        except KeyError:
            raise HTTPException(status_code=404, detail="Character not found")

    @router.get(
        "/projects/{project_name}/characters/{character_name}",
        summary="Get character by project name and character name",
        description="Returns a character record by name within a named project.",
        response_model=CharacterResponse,
    )
    def get_character_by_project_and_name(project_name: str, character_name: str):
        def fetch(session):
            project = session.exec(
                select(Project).where(func.lower(Project.name) == project_name.lower())
            ).first()
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            character = session.exec(
                select(Character).where(
                    Character.project_id == project.id,
                    func.lower(Character.name) == character_name.lower(),
                )
            ).first()
            if character is None:
                raise HTTPException(status_code=404, detail="Character not found")
            return safe_model_dict(character)

        return server.vault.db.run_immediate_read_task(fetch)

    @router.get(
        "/characters/{id}/{field}",
        summary="Get character field",
        description="Returns one character field value, including generated thumbnail handling for field=thumbnail.",
        responses={
            200: {
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "additionalProperties": True}
                    },
                    "image/png": {},
                }
            }
        },
    )
    def get_character_field_by_id(request: Request, id: int, field: str):
        _require_scope_allows_character(request, id)
        if field == "thumbnail":
            thumbnail_cache_version = 6
            cache_dir = os.path.join(server.vault.image_root, "tmp", "face_thumbnails")
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = resolve_path_within(cache_dir, f"character_{id}.png")
            meta_path = resolve_path_within(cache_dir, f"character_{id}.json")

            def fetch_best_picture_id(session: Session, character_id: int):
                _video_exts = (".mp4", ".mov", ".webm", ".avi", ".mkv")
                is_video_expr = sa_case(
                    *[(Picture.file_path.ilike(f"%{ext}"), 1) for ext in _video_exts],
                    else_=0,
                )
                row = session.exec(
                    select(Picture.id, Picture.score)
                    .join(Face, Face.picture_id == Picture.id)
                    .where(
                        Face.character_id == character_id,
                        Picture.deleted.is_(False),
                    )
                    .order_by(
                        is_video_expr,  # prefer still images over videos
                        Picture.score.is_(None),
                        Picture.score.desc(),
                        Picture.id.desc(),
                    )
                    .limit(1)
                ).first()
                if not row:
                    return None
                pic_id, score = row
                return {
                    "picture_id": int(pic_id),
                    "score": float(score) if score is not None else None,
                }

            best_picture = server.vault.db.run_immediate_read_task(
                fetch_best_picture_id, character_id=id
            )
            if not best_picture:
                raise HTTPException(
                    status_code=404, detail="No face thumbnail found for character"
                )
            if os.path.exists(cache_path) and os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as handle:
                        meta = json.load(handle)
                    if (
                        meta.get("picture_id") == best_picture.get("picture_id")
                        and meta.get("version") == thumbnail_cache_version
                    ):
                        return FileResponse(cache_path, media_type="image/png")
                except Exception as exc:
                    logger.debug("Failed to read character thumbnail cache: %s", exc)
            char = server.vault.db.run_immediate_read_task(
                Character.find,
                select_fields=["reference_picture_set_id", "faces"],
                id=id,
            )
            if not char:
                raise HTTPException(status_code=404, detail="Character not found")
            char = char[0]
            best_pic = None
            best_face = None

            def get_reference_set_and_members(session, reference_picture_set_id):
                ref_set = (
                    session.get(PictureSet, reference_picture_set_id)
                    if reference_picture_set_id
                    else None
                )
                if ref_set:
                    session.refresh(ref_set)
                    members = list(ref_set.members)
                    return ref_set, members
                return None, []

            ref_set, members = server.vault.db.run_immediate_read_task(
                get_reference_set_and_members, char.reference_picture_set_id
            )
            if ref_set and ref_set.members:
                pics = sorted(members, key=lambda p: p.score or 0, reverse=True)
                for pic in pics:
                    faces = server.vault.db.run_immediate_read_task(
                        Face.find, picture_id=pic.id
                    )
                    for face in faces:
                        if face.character_id == char.id:
                            best_pic = pic
                            best_face = face
                            break
                    if best_pic and best_face:
                        logger.debug("Found thumbnail from reference set!")
                        break
            if not best_pic or not best_face:
                for face in char.faces:
                    pic = server.vault.db.run_immediate_read_task(
                        Picture.find,
                        id=face.picture_id,
                        sort_field="score",
                    )
                    if pic:
                        best_pic = pic
                        best_face = face
                        break
            if not best_pic or not best_face:
                raise HTTPException(
                    status_code=404, detail="No face thumbnail found for character"
                )

            bbox = best_face.bbox

            if isinstance(best_pic, list):
                best_pic = best_pic[0]

            picture_path = ImageUtils.resolve_picture_path(
                server.vault.image_root, best_pic.file_path
            )
            if isinstance(bbox, str):
                try:
                    bbox = ast.literal_eval(bbox)
                except Exception:
                    bbox = None
            if not bbox or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                raise HTTPException(
                    status_code=404, detail="Failed to crop face thumbnail"
                )
            try:
                if VideoUtils.is_video_file(picture_path):
                    frame_bgr = VideoUtils.read_first_video_frame_bgr(picture_path)
                    if frame_bgr is None:
                        raise ValueError("Could not read first frame from video")
                    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                    image = Image.fromarray(frame_rgb)
                else:
                    raw = Image.open(picture_path)
                    raw.load()  # force HEIF/lazy decoders to materialise before conversion
                    image = raw.convert(
                        "RGB"
                    ).copy()  # detach from any HEIF CtxImage context
            except Exception:
                raise HTTPException(
                    status_code=404, detail="Failed to crop face thumbnail"
                )
            image_width, image_height = image.size
            x1, y1, x2, y2 = [float(v) for v in bbox]
            x1 = max(0.0, min(float(image_width - 1), x1))
            y1 = max(0.0, min(float(image_height - 1), y1))
            x2 = max(0.0, min(float(image_width), x2))
            y2 = max(0.0, min(float(image_height), y2))
            if x2 <= x1 or y2 <= y1:
                raise HTTPException(
                    status_code=404, detail="Failed to crop face thumbnail"
                )
            side = max(x2 - x1, y2 - y1)
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            new_x1 = cx - side / 2.0
            new_x2 = cx + side / 2.0
            new_y1 = cy - side / 2.0
            new_y2 = cy + side / 2.0
            if new_x1 < 0:
                new_x2 -= new_x1
                new_x1 = 0.0
            if new_x2 > image_width:
                shift = new_x2 - image_width
                new_x1 -= shift
                new_x2 = float(image_width)
            if new_y1 < 0:
                new_y2 -= new_y1
                new_y1 = 0.0
            if new_y2 > image_height:
                shift = new_y2 - image_height
                new_y1 -= shift
                new_y2 = float(image_height)
            new_x1 = max(0.0, min(float(image_width - 1), new_x1))
            new_y1 = max(0.0, min(float(image_height - 1), new_y1))
            new_x2 = max(0.0, min(float(image_width), new_x2))
            new_y2 = max(0.0, min(float(image_height), new_y2))
            crop = image.crop(
                (
                    int(round(new_x1)),
                    int(round(new_y1)),
                    int(round(new_x2)),
                    int(round(new_y2)),
                )
            )
            crop = crop.resize((64, 64), Image.LANCZOS)
            try:
                crop.save(cache_path, format="PNG")
                try:
                    with open(meta_path, "w", encoding="utf-8") as handle:
                        meta_payload = dict(best_picture)
                        meta_payload["version"] = thumbnail_cache_version
                        json.dump(meta_payload, handle)
                except Exception as exc:
                    logger.debug(
                        "Failed to write character thumbnail metadata: %s", exc
                    )
                return FileResponse(cache_path, media_type="image/png")
            except Exception:
                from io import BytesIO

                buf = BytesIO()
                crop.save(buf, format="PNG")
                return Response(content=buf.getvalue(), media_type="image/png")
        try:
            char = server.vault.db.run_immediate_read_task(
                Character.find, select_fields=[field], id=id
            )
            if not char:
                raise KeyError("Character not found")
            char = char[0]
            logger.debug(
                "Data type for Character field {}: {}".format(field, type(char))
            )
            if not hasattr(char, field):
                raise HTTPException(
                    status_code=404, detail=f"Field {field} not found in Character"
                )
            returnValue = {field: safe_model_dict(getattr(char, field))}
            logger.debug(
                f"Returning character id={id} field={field} value={returnValue}"
            )
            return returnValue
        except KeyError:
            raise HTTPException(status_code=404, detail="Character not found")

    @router.get(
        "/characters",
        summary="List characters",
        description="Lists characters, optionally filtered by exact name or project. "
        "Pass ``project_id`` as a numeric ID to restrict to one project, "
        "or ``UNASSIGNED`` for characters with no project.",
        response_model=list[CharacterListItemResponse],
    )
    def get_characters(
        request: Request,
        name: str = Query(None),
        project_id: str | None = Query(default=None),
    ):
        token_scope = getattr(request.state, "token_scope", None)
        try:
            logger.debug(
                f"Fetching characters with name: {name}, project_id: {project_id}"
            )
            scope_character_id = None
            if token_scope is not None and token_scope.resource_type == "character":
                # Restrict to the single authorised character; project_id filter still applies
                scope_character_id = token_scope.resource_id
            elif token_scope is not None and token_scope.resource_type == "project":
                # Force project_id to the token's authorised project
                project_id = str(token_scope.resource_id)
            elif token_scope is not None and token_scope.resource_type is not None:
                # Any other scoped token (e.g. picture_set) has no access to characters
                return []

            def fetch(session: Session):
                query = select(Character).order_by(Character.name)
                if scope_character_id is not None:
                    query = query.where(Character.id == scope_character_id)
                if name is not None:
                    query = query.where(Character.name == name)
                if project_id is not None:
                    if project_id == "UNASSIGNED":
                        query = query.where(Character.project_id.is_(None))
                    else:
                        try:
                            query = query.where(Character.project_id == int(project_id))
                        except (TypeError, ValueError):
                            raise HTTPException(
                                status_code=400, detail="Invalid project_id"
                            )
                characters = session.exec(query).all()

                # Annotate each character with whether it has at least one face
                # embedding so the UI can filter the similarity-sort dropdown.
                char_ids = [c.id for c in characters]
                if char_ids:
                    has_faces_query = (
                        select(Face.character_id)
                        .where(
                            Face.character_id.in_(char_ids),
                            Face.features.is_not(None),
                        )
                        .distinct()
                    )
                    chars_with_faces = set(session.exec(has_faces_query).all())
                else:
                    chars_with_faces = set()

                return [
                    {
                        **c.model_dump(exclude_unset=False),
                        "has_reference_faces": c.id in chars_with_faces,
                    }
                    for c in characters
                ]

            return server.vault.db.run_immediate_read_task(fetch)
        except HTTPException:
            raise
        except KeyError:
            logger.error("Character not found")
            raise HTTPException(status_code=404, detail="Character not found")
        except Exception as e:
            logger.error(f"Error fetching characters: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")

    @router.post(
        "/characters",
        summary="Create character",
        description="Creates a character and its linked reference picture set.",
        response_model=CharacterMutationResponse,
    )
    def create_character(payload: dict = Body(...)):
        try:

            def create_character_and_reference_set(session, payload):
                char_name = payload.get("name")
                char_project_id = payload.get("project_id")
                if char_name:
                    _ensure_unique_character_name(session, char_name, char_project_id)
                character = Character(**payload)
                session.add(character)
                session.commit()
                session.refresh(character)
                logger.debug("Created character with ID: {}".format(character.id))
                reference_set = PictureSet(
                    name="reference_pictures", description=str(character.name)
                )
                session.add(reference_set)
                session.commit()
                session.refresh(reference_set)
                character.reference_picture_set_id = reference_set.id
                session.add(character)
                session.commit()
                session.refresh(character)
                return character.model_dump(exclude_unset=False)

            char_dict = server.vault.db.run_task(
                create_character_and_reference_set,
                payload,
                priority=DBPriority.IMMEDIATE,
            )
            logger.debug("Created character: {}".format(char_dict))
            server.vault.notify(EventType.CHANGED_CHARACTERS)
            return {"status": "success", "character": char_dict}
        except Exception as e:
            logger.error(f"Error creating character: {e}")
            raise HTTPException(status_code=400, detail="Invalid character data")

    @router.post(
        "/characters/{character_id}/faces",
        summary="Assign faces to character",
        description="Assigns provided face ids or largest faces from picture ids to a character.",
        response_model=CharacterFaceAssignmentResponse,
    )
    def assign_face_to_character(character_id: int, payload: dict = Body(...)):
        face_ids = payload.get("face_ids")
        picture_ids = payload.get("picture_ids")
        if face_ids is not None and not isinstance(face_ids, list):
            raise HTTPException(status_code=400, detail="face_ids must be a list")
        if picture_ids is not None and not isinstance(picture_ids, list):
            raise HTTPException(status_code=400, detail="picture_ids must be a list")

        def assign_faces(
            session: Session,
            face_ids: list[int],
            picture_ids: list[str],
            character_id: int,
        ):
            faces_to_assign = []
            existing_faces = []
            if picture_ids:
                reference_faces = select_reference_faces_for_character(
                    session, character_id
                )

                def face_area(face):
                    try:
                        return (face.width or 0) * (face.height or 0)
                    except Exception:
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
        server.vault.notify(EventType.CHANGED_CHARACTERS)
        server.vault.notify(EventType.CHANGED_FACES)
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
    def remove_character_from_faces(character_id: int, payload: dict = Body(...)):
        face_ids = payload.get("face_ids", None)
        picture_ids = payload.get("picture_ids", None)
        if not isinstance(face_ids, list) and not isinstance(picture_ids, list):
            raise HTTPException(
                status_code=400,
                detail="Must send a list of picture_ids or face_ids",
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
        server.vault.notify(EventType.CHANGED_CHARACTERS)
        server.vault.notify(EventType.CHANGED_FACES)
        return {
            "status": "success",
            "face_ids": face_ids,
            "character_id": character_id,
        }

    @router.post(
        "/characters/likeness-search",
        summary="Search characters by face likeness",
        description=(
            "Upload one or more images and retrieve vault characters ranked by face "
            "similarity (softmax-weighted cosine similarity on InsightFace ArcFace "
            "embeddings).\n\n"
            "When multiple query images are provided, per-character scores from each "
            "image are combined using the ``combine`` strategy before ranking.\n\n"
            "**Combine modes**\n"
            "- `mean` (default): arithmetic mean across query images.\n"
            "- `max`: best match to any query image.\n"
            "- `min`: must match all query images.\n"
            "- `harmonic_mean`: emphasises the worst-matching query.\n"
            "- `geometric_mean`: product-like balance.\n\n"
            "**Random modes**\n"
            "- `random=false` (default): returns the top `top_n` most similar characters.\n"
            "- `random=true`: selects `top_n` characters at random from the `pool_m` "
            "most similar candidates.\n\n"
            "Results are ordered by descending similarity score. "
            "Only characters with at least one pre-computed face embedding are considered. "
            "The most prominent face (largest bounding box) in each uploaded image is used "
            "as the query. Images with no detectable face are skipped; returns 422 when "
            "no face is detected in any image."
        ),
        response_model=list[CharacterLikenessResultResponse],
    )
    async def search_by_character_likeness(
        request: Request,
        files: List[UploadFile] = File(
            ...,
            description="One or more query images containing a face to search against.",
        ),
        top_n: int = Query(
            _LIKENESS_SEARCH_DEFAULT_TOP_N,
            ge=1,
            le=_LIKENESS_SEARCH_MAX_TOP_N,
            description="Maximum number of results to return.",
        ),
        pool_m: int = Query(
            0,
            ge=0,
            le=_LIKENESS_SEARCH_MAX_POOL_M,
            description=(
                "Pool size for random mode. When >0 and `random=true`, the top "
                "`pool_m` matches are collected first and then `top_n` are drawn "
                "at random. Ignored when `random=false`."
            ),
        ),
        use_random: bool = Query(
            False,
            alias="random",
            description="When true, return a random sample from the top-M pool.",
        ),
        threshold: float = Query(
            0.0,
            ge=0.0,
            le=1.0,
            description="Minimum similarity score required to include a result.",
        ),
        combine: str = Query(
            "mean",
            description=(
                "How to combine scores when multiple query images are uploaded. "
                "One of: mean, max, min, harmonic_mean, geometric_mean."
            ),
        ),
    ):
        # ── Authentication ────────────────────────────────────────────────
        server.auth.require_user_id(request)

        if combine not in VALID_COMBINE_MODES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid combine mode {combine!r}. Must be one of: {', '.join(sorted(VALID_COMBINE_MODES))}",
            )

        # ── Scope-based candidate restriction ────────────────────────────
        scope_allowed = fetch_scope_allowed_character_ids(server, request)

        # ── Load images and detect faces ──────────────────────────────────
        if not files:
            raise HTTPException(
                status_code=400, detail="At least one file must be uploaded."
            )

        bgr_images: list[np.ndarray] = []
        for idx, file in enumerate(files):
            content_type = file.content_type or ""
            if not content_type.startswith("image/"):
                raise HTTPException(
                    status_code=400,
                    detail=f"File {idx + 1}: uploaded file must be an image.",
                )

            raw_bytes = await file.read()
            if not raw_bytes:
                raise HTTPException(
                    status_code=400,
                    detail=f"File {idx + 1}: uploaded file is empty.",
                )

            try:
                pil_image = Image.open(BytesIO(raw_bytes)).convert("RGB")
            except Exception as exc:
                logger.warning(
                    "characters/likeness-search: could not open uploaded image %d (%s bytes): %s",
                    idx + 1,
                    len(raw_bytes),
                    exc,
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"File {idx + 1}: could not decode uploaded image.",
                ) from exc

            bgr_images.append(cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR))

        # ── Run face detection via the GPU task queue ─────────────────────
        from pixlstash.tasks.face_detection_task import FaceDetectionTask

        engine = getattr(server.vault, "_engine", None)
        if engine is None:
            raise HTTPException(
                status_code=503, detail="Inference engine not available."
            )
        task_runner = getattr(server.vault, "_task_runner", None)
        if task_runner is None:
            raise HTTPException(status_code=503, detail="Task runner not available.")

        detection_task = FaceDetectionTask(engine, bgr_images)
        loop = asyncio.get_event_loop()
        try:
            all_face_results = await loop.run_in_executor(
                None, task_runner.submit_and_wait, detection_task, 60.0
            )
        except TimeoutError as exc:
            logger.error(
                "characters/likeness-search: face detection timed out: %s", exc
            )
            raise HTTPException(
                status_code=503,
                detail="Face detection timed out; the server may be under heavy load.",
            ) from exc
        except RuntimeError as exc:
            logger.error(
                "characters/likeness-search: face detection task failed: %s", exc
            )
            raise HTTPException(
                status_code=503,
                detail="Face detection failed.",
            ) from exc

        query_embeddings: list[np.ndarray] = []
        for idx, face_results in enumerate(all_face_results):
            if not face_results:
                logger.debug(
                    "characters/likeness-search: no face detected in file %d; skipping",
                    idx + 1,
                )
                continue

            # Pick the face with the largest bounding box area.
            best_face_result = max(
                face_results,
                key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
            )
            if best_face_result.embedding is None:
                logger.warning(
                    "characters/likeness-search: face in file %d has no embedding; skipping",
                    idx + 1,
                )
                continue

            q_emb = best_face_result.embedding.astype(np.float32)
            norm = np.linalg.norm(q_emb)
            if norm > 1e-8:
                q_emb = q_emb / norm
            query_embeddings.append(q_emb)

        if not query_embeddings:
            raise HTTPException(
                status_code=422,
                detail="No face detected in any of the uploaded images.",
            )

        # ── Fetch candidate character embeddings from DB ──────────────────
        candidates = _fetch_character_candidate_embeddings(server, scope_allowed)
        if not candidates:
            return []

        # ── Compute per-character, per-query similarity ───────────────────
        char_ids = [cid for cid, _ in candidates]
        char_ref_embs = [refs for _, refs in candidates]

        # scores_matrix shape: (Q, N_chars)
        scores_matrix = np.array(
            [
                [
                    _compute_character_query_likeness(q_emb, refs)
                    for refs in char_ref_embs
                ]
                for q_emb in query_embeddings
            ],
            dtype=np.float32,
        )

        # Combine across queries → (N_chars,)
        combined = combine_likeness_scores(scores_matrix, combine)

        scored: list[tuple[int, float]] = [
            (char_ids[i], float(combined[i]))
            for i in range(len(char_ids))
            if combined[i] >= threshold
        ]

        if not scored:
            return []

        # Sort descending by similarity.
        scored.sort(key=lambda x: -x[1])

        # ── Select results ────────────────────────────────────────────────
        effective_pool = top_n if not use_random or pool_m <= 0 else pool_m
        pool = scored[:effective_pool]

        if use_random and pool_m > 0 and len(pool) > top_n:
            indices = _random.sample(range(len(pool)), top_n)
            indices.sort(key=lambda i: -pool[i][1])
            pool = [pool[i] for i in indices]
        else:
            pool = pool[:top_n]

        return [{"character_id": cid, "likeness": round(sim, 6)} for cid, sim in pool]

    return router
