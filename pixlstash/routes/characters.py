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
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field as PydanticField
from sqlalchemy import exists, func
from sqlmodel import Session, select

from pixlstash.authz.membership import (
    enforce_character_scope,
    enforce_project_path_scope,
)
from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Character,
    CharacterProjectMember,
    Face,
    Picture,
    PictureProjectMember,
    Project,
    PictureSet,
    PictureSetMember,
    Tag,
    character_in_no_project,
    character_in_project,
)
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services.project_membership_service import (
    character_project_ids,
    reconcile_entity_projects_change,
    set_character_projects,
)
from pixlstash.services.layout_move_service import rename_entity_folders
from pixlstash.services.set_lock_service import locked_picture_ids
from pixlstash.services.stack_membership import expand_picture_ids_to_stacks
from pixlstash.routes.pictures import FaceListResponse
from pixlstash.utils.library_layout import Facet
from pixlstash.utils.field_allowlist import (
    CHARACTER_EXTRA_SERVABLE_FIELDS,
    require_servable_field,
)
from pixlstash.utils.http_cache import conditional_file_response
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.scoring import (
    select_reference_faces_for_character,
)
from pixlstash.utils.service.caption_utils import normalize_hidden_tags
from pixlstash.utils.service.filter_helpers import (
    combine_likeness_scores,
    fetch_scope_allowed_character_ids,
    fetch_scope_allowed_picture_ids,
    filter_visible_project_ids,
    narrow_project_fields,
    VALID_COMBINE_MODES,
    visible_project_ids,
)
from pixlstash.utils.path_utils import resolve_path_within
from pixlstash.utils.serialization_utils import safe_model_dict

logger = get_logger(__name__)

_UNSET = object()

_LIKENESS_SEARCH_DEFAULT_TOP_N = 20
_LIKENESS_SEARCH_MAX_TOP_N = 500
_LIKENESS_SEARCH_MAX_POOL_M = 2000
# Maximum reference faces loaded per character for query-time likeness scoring.
_MAX_REFS_PER_CHARACTER = 10


def _may_learn_a_project_scoped_count(visible_projects: set[int] | None) -> bool:
    """Whether a project-scoped aggregate may be serialised to this caller.

    This is the exact complement of the refusal that guards a caller-supplied
    ``project_id`` filter: a token may name a project scope (an id, or the
    ``UNASSIGNED`` sentinel) precisely when it has some project visibility, and
    :func:`visible_project_ids` returns ``None`` for the owner and a non-empty
    set for a project-scoped token. Writing the outbound rule as the complement
    of the inbound one is what makes the list endpoint and
    ``GET /characters/{id}/summary`` agree BY CONSTRUCTION: no number is
    serialised here that the caller could not have asked that endpoint for
    directly, so neither can drift into being the more generous of the two.

    The suppression has to key off the *token*, not off whether a particular row
    turned out to have hidden memberships. A per-row rule would answer for the
    genuinely unassigned character and stay silent for the one filed under an
    invisible project, and the presence of an answer would then be the oracle
    the suppression exists to remove (issue #718).

    Args:
        visible_projects: The result of :func:`visible_project_ids`: ``None``
            for an owner / unscoped token, otherwise the project ids the token
            may learn about.

    Returns:
        ``True`` for an owner or a project-scoped token; ``False`` for a
        character-, picture_set- or picture-scoped token, which has no project
        visibility at all.
    """
    return visible_projects is None or bool(visible_projects)


def characters_with_reference_faces_query():
    """Select every character id that has at least one embedded face.

    Deliberately NOT narrowed to the characters being listed, with the
    intersection done in Python instead. Narrowing makes the predicate
    ``character_id = ?``, which BOTH ``ix_face_character_id`` and the partial
    ``ix_face_character_features`` can serve. Nothing here runs ``ANALYZE``, so
    with no ``sqlite_stat1`` the two tie and SQLite breaks the tie on index
    creation order, which ``create_all`` iterates from a *set*: roughly half of
    all databases got the plain index, which cannot answer ``features IS NOT
    NULL`` from the index and so read every candidate face row, embedding BLOB
    and all. Measured on 200k faces, per sidebar refresh: 4.0 ms on the partial
    index against 180.7 ms on the plain one.

    As a one-pass rollup the partial index is strictly the smaller object and is
    chosen unconditionally. It is also index-only, and the result is bounded by
    the number of characters rather than the number of faces.

    Extracted so the query-plan test asserts on the statement this endpoint
    actually issues. An earlier revision asserted a hand-written query that no
    production code ran, which let the index look useful while the real endpoint
    quietly used the other one.

    Returns:
        A SQLModel ``select`` yielding one ``character_id`` per character that
        has an embedded face. Characters with no faces are simply absent.
    """
    return (
        select(Face.character_id)
        .where(Face.features.is_not(None))
        .group_by(Face.character_id)
    )


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
    project_id: Optional[int] = PydanticField(
        default=None,
        description=(
            "The character's primary project - the lowest id in ``project_ids``, "
            "or null when it belongs to no project. Kept for backwards "
            "compatibility; prefer ``project_ids``, which lists every project."
        ),
    )
    project_ids: list[int] = PydanticField(
        default_factory=list,
        description=(
            "Every project this character belongs to, lowest id first. A character "
            "may be shared across several projects."
        ),
    )
    reference_picture_set_id: Optional[int] = None
    thumbnail_picture_id: Optional[int] = PydanticField(
        default=None,
        description=(
            "The picture ``GET /characters/{id}/thumbnail`` crops this person's "
            "face from, when the user pinned one; ``null`` means the thumbnail "
            "follows the automatic choice."
        ),
    )


class CharacterListItemResponse(BaseModel):
    """A character in the list endpoint, annotated with reference-face presence."""

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    extra_metadata: Optional[str] = None
    project_id: Optional[int] = PydanticField(
        default=None,
        description=(
            "The character's primary project - the lowest id in ``project_ids``, "
            "or null when it belongs to no project. Kept for backwards "
            "compatibility; prefer ``project_ids``, which lists every project."
        ),
    )
    project_ids: list[int] = PydanticField(
        default_factory=list,
        description=(
            "Every project this character belongs to, lowest id first. A character "
            "may be shared across several projects."
        ),
    )
    reference_picture_set_id: Optional[int] = None
    thumbnail_picture_id: Optional[int] = PydanticField(
        default=None,
        description=(
            "The picture ``GET /characters/{id}/thumbnail`` crops this person's "
            "face from, when the user pinned one; ``null`` means the thumbnail "
            "follows the automatic choice."
        ),
    )
    has_reference_faces: bool = False
    image_count: Optional[int] = PydanticField(
        default=None,
        description=(
            "Number of non-deleted pictures with at least one face assigned to "
            "this character, across the whole vault. Populated only when the "
            "request passes ``include_counts=true``; ``null`` otherwise. Same "
            "number as ``GET /characters/{id}/summary`` returns with no "
            "``project_id``, so the sidebar can render its counts from this one "
            "list response instead of one request per character (issue #651). "
            "Hidden tags are NOT applied: this count has no ``apply_tag_filter`` "
            "equivalent, so it matches that endpoint called without one. Use the "
            "per-id summary if you need a hidden-tag-filtered number."
        ),
    )
    project_image_count: Optional[int] = PydanticField(
        default=None,
        description=(
            "The same count as ``image_count``, narrowed to the project named by "
            "this row's ``project_id`` - or, when ``project_id`` is ``null``, to "
            "pictures that belong to no project at all. Populated only when the "
            "request passes ``include_counts=true``; ``null`` otherwise. Same "
            "number as ``GET /characters/{id}/summary?project_id=<project_id>`` "
            "(or ``project_id=UNASSIGNED``) returns, again without any "
            "hidden-tag filtering. Also ``null`` for a credential with no "
            "project visibility at all (a character-, set- or picture-scoped "
            "token), which may name no project scope on that endpoint either; "
            "such a caller reads ``image_count`` and renders no project view."
        ),
    )


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

    character_assignments: dict[int, list[int]] = {}
    pictures_with_faces: list[int] = []


class CharacterLikenessResultResponse(BaseModel):
    """A single character likeness-search result."""

    model_config = ConfigDict(extra="allow")

    character_id: int
    likeness: float


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _ensure_unique_character_name(
        session, name: str, project_ids, exclude_char_id=None
    ):
        """Raises 409 if a character with the same name (case-insensitive)
        already exists in **any** of the given projects.  Unscoped characters
        (no projects) are exempt.

        Since issue #125 a character can be in several projects at once, so the
        clash check spans every project it is joining, not just its primary one.
        """
        wanted = sorted({int(pid) for pid in (project_ids or []) if pid is not None})
        if not wanted:
            return
        stmt = (
            select(Character.id)
            .join(
                CharacterProjectMember,
                CharacterProjectMember.character_id == Character.id,
            )
            .where(
                CharacterProjectMember.project_id.in_(wanted),
                func.lower(Character.name) == name.lower(),
            )
        )
        if exclude_char_id is not None:
            stmt = stmt.where(Character.id != exclude_char_id)
        if session.exec(stmt).first():
            raise HTTPException(
                status_code=409,
                detail=f"A character named '{name}' already exists in this project.",
            )

    def _resolve_target_project_ids(data: dict, current: list[int] | None):
        """Resolve the requested project membership set from a request payload.

        Accepts the multi-project ``project_ids`` list (issue #125) and the legacy
        single ``project_id`` scalar, in that precedence order.

        Args:
            data: The parsed request body.
            current: The entity's existing project ids, returned unchanged when
                the payload mentions neither key. ``None`` for a create.

        Returns:
            ``(target_project_ids, provided)`` - the full target membership set,
            and whether the payload asked for a project change at all.

        Raises:
            HTTPException: ``400`` when either key is not an integer / list of
                integers.
        """
        if "project_ids" in data:
            raw_ids = data.get("project_ids")
            if raw_ids is None:
                return [], True
            if not isinstance(raw_ids, (list, tuple, set)):
                raise HTTPException(
                    status_code=400, detail="project_ids must be a list"
                )
            try:
                return sorted({int(v) for v in raw_ids if v is not None}), True
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="Invalid project_id"
                ) from exc
        if "project_id" in data:
            raw = data.get("project_id")
            if raw is None:
                return [], True
            try:
                return [int(raw)], True
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="Invalid project_id"
                ) from exc
        return (list(current) if current is not None else []), False

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

    def _character_picture_counts(
        session: Session,
        char_ids: list[int],
        extra_conditions=None,
        join_character: bool = False,
    ) -> dict[int, int]:
        """Count each character's visible pictures in ONE grouped query.

        The per-row shape is exactly the assigned-character branch of
        ``GET /characters/{id}/summary``: ``count(distinct Face.picture_id)``
        over ``Face`` joined to a non-deleted ``Picture``. The only difference
        is that the character id is an ``IN`` list plus a ``GROUP BY`` instead
        of an equality, so N characters cost one query rather than N.

        One deliberate exception to that parity: the hidden-tag filter. The
        per-id endpoint applies it when the caller passes ``apply_tag_filter``,
        and this path has no equivalent parameter, so the numbers here always
        match that endpoint called WITHOUT one. That is what the only caller
        needs (the sidebar has never passed it), but it does mean these counts
        and a tag-filtered summary can legitimately disagree.

        Args:
            session: Open read session.
            char_ids: The character ids to count for. Always the ids the list
                endpoint's own scope filtering already returned, never a
                widened set.
            extra_conditions: Extra WHERE clauses, e.g. a project-membership
                predicate.
            join_character: Join ``Character`` so a condition may correlate on
                ``Character.project_id``.

        Returns:
            ``{character_id: count}``. A character with no matching picture is
            absent, so callers default it to 0.
        """
        if not char_ids:
            return {}
        query = (
            select(Face.character_id, func.count(func.distinct(Face.picture_id)))
            .select_from(Face)
            .join(Picture, Face.picture_id == Picture.id)
        )
        if join_character:
            query = query.join(Character, Character.id == Face.character_id)
        query = query.where(
            Face.character_id.in_(char_ids),
            Picture.deleted.is_(False),
            *(extra_conditions or []),
        ).group_by(Face.character_id)
        return {
            int(char_id): int(count)
            for char_id, count in session.exec(query).all()
            if char_id is not None
        }

    def _inline_character_counts(
        session: Session,
        characters,
        narrowed_by_char: dict[int, list[int]],
        visible_projects: set[int] | None,
    ) -> tuple[dict[int, int], dict[int, int] | None]:
        """Global and primary-project picture counts for every listed character.

        Serves the sidebar's counts from the list response so it no longer
        fires one ``GET /characters/{id}/summary`` per character on every
        refresh (issue #651). The sidebar asks each character for the scope of
        its OWN primary project, so both numbers are independent of the
        selected project and one cached list response serves both view modes.

        ``project_image_count`` is computed against the project this response
        actually reports in ``project_id`` - the *narrowed* primary project,
        not the raw ``Character.project_id`` column, since
        :func:`narrow_project_fields` hides project ids a scoped token may not
        learn (issue #125 / R1b). That keeps the two fields self-consistent for
        every caller and never scopes a count to a project the caller cannot
        see.

        A caller with no project visibility at all gets no project-scoped
        number: see :func:`_may_learn_a_project_scoped_count`. Narrowing alone
        was not enough there, because an empty narrowed list dropped the row
        into the "in no project whatsoever" bucket, whose ``NOT EXISTS``
        predicate answers a question about projects the caller cannot see
        (issue #718).

        Cost is a constant 1-3 queries regardless of how many characters are
        listed: one global, plus at most one per distinct *narrowed* primary
        project. The owner case correlates on ``Character.project_id`` inside
        SQL, so its many distinct primary projects still cost a single query;
        a scoped token's narrowed ids are a subset of
        :func:`visible_project_ids`, which holds at most one project.

        Args:
            session: Open read session.
            characters: The ``Character`` rows the endpoint is returning.
            narrowed_by_char: Each character's scope-narrowed project ids.
            visible_projects: The result of :func:`visible_project_ids` for this
                request.

        Returns:
            ``(global_counts, project_counts)``. ``global_counts`` is always
            ``{character_id: count}``. ``project_counts`` is the same shape, or
            ``None`` when the caller may learn no project-scoped number at all,
            which the serialiser renders as ``project_image_count: null``.
        """
        char_ids = [int(c.id) for c in characters if c.id is not None]
        if not char_ids:
            return {}, {}

        global_counts = _character_picture_counts(session, char_ids)
        if not _may_learn_a_project_scoped_count(visible_projects):
            # No project visibility: skip the bucketing entirely, so there is no
            # project-derived aggregate to serialise and one fewer query to run.
            return global_counts, None

        # Bucket by the project each row reports, so every bucket is one query.
        correlated_ids: list[int] = []  # narrowed primary == Character.project_id
        unassigned_ids: list[int] = []  # no visible project at all
        by_project_ids: dict[int, list[int]] = {}  # narrowed primary != the column
        for character in characters:
            if character.id is None:
                continue
            char_id = int(character.id)
            narrowed = narrowed_by_char.get(char_id) or []
            effective_project_id = narrowed[0] if narrowed else None
            if effective_project_id is None:
                unassigned_ids.append(char_id)
            elif effective_project_id == character.project_id:
                correlated_ids.append(char_id)
            else:
                by_project_ids.setdefault(int(effective_project_id), []).append(char_id)

        project_counts: dict[int, int] = {}
        if correlated_ids:
            project_counts.update(
                _character_picture_counts(
                    session,
                    correlated_ids,
                    [_project_membership_exists(Character.project_id)],
                    join_character=True,
                )
            )
        if unassigned_ids:
            project_counts.update(
                _character_picture_counts(
                    session,
                    unassigned_ids,
                    [_project_unassigned_membership()],
                )
            )
        for project_id_value, ids in by_project_ids.items():
            project_counts.update(
                _character_picture_counts(
                    session,
                    ids,
                    [_project_membership_exists(project_id_value)],
                )
            )
        return global_counts, project_counts

    def _require_scope_allows_character(request: Request, character_id: int):
        """Raise 403 if the token scope does not cover the requested character.

        Thin delegation to the single membership implementation in
        ``pixlstash/authz/membership.py`` (backend refactor plan §3.7, Step 4).
        The authz gate calls the same function directly for CHARACTER_SCOPED
        routes; Step 5 removes this shim.
        """
        enforce_character_scope(server, request, character_id)

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

        # Scope guard (BOLA): a resource-scoped token may only summarise its own
        # character; the aggregate ALL/UNASSIGNED/SCRAPHEAP views are owner-only.
        scope = getattr(request.state, "token_scope", None)
        if id in ("ALL", "UNASSIGNED", "SCRAPHEAP"):
            if scope is not None and scope.resource_type is not None:
                raise HTTPException(
                    status_code=403,
                    detail="Token is not authorised for aggregate summaries",
                )
        else:
            try:
                _require_scope_allows_character(request, int(id))
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="Invalid character id"
                ) from exc

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
        description=(
            "Updates character fields and clears dependent picture text embeddings "
            "when identity data changes.\n\n"
            "**Project membership.** Send ``project_ids`` (a list) to set the full "
            "set of projects the character belongs to - a character may be in "
            "several at once. The legacy single ``project_id`` is still accepted "
            "and means the same as a one-element ``project_ids``; ``null`` on "
            "either key removes the character from every project. ``project_ids`` "
            "wins when both are present. Member pictures follow: they join every "
            "project the character joins, and leave a project it leaves unless "
            "another character or picture set still anchors them there. Returns "
            "409 on a name clash inside any target project and 404 for an unknown "
            "project id.\n\n"
            "**Thumbnail.** Send ``thumbnail_picture_id`` to pin which picture "
            "``GET /characters/{id}/thumbnail`` crops the face from; ``null`` "
            "restores the automatic choice (the highest-scoring picture of this "
            "person). The picture must carry a face assigned to this character "
            "and must not be in the scrapheap, or the call answers 400 - the "
            "renderer skips deleted pictures, so a pin naming one could never "
            "be honoured."
        ),
        response_model=CharacterMutationResponse,
    )
    async def patch_character(id: int, request: Request):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        data = await request.json()
        name = data.get("name")
        description = data.get("description")
        # Sentinel, because ``null`` is a meaningful value here: it clears the
        # pin back to the automatic choice, while an absent key leaves it alone.
        thumbnail_picture_id = data.get("thumbnail_picture_id", _UNSET)
        if thumbnail_picture_id is not _UNSET and thumbnail_picture_id is not None:
            try:
                thumbnail_picture_id = int(thumbnail_picture_id)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail="thumbnail_picture_id must be a picture id or null",
                )
        char = None
        project_membership_updated = False
        try:

            def alter_char(
                session: Session,
                id: int,
                name: str,
                description: str,
                thumbnail_picture_id,
            ):
                character = session.get(Character, id)
                if character is None:
                    raise KeyError("Character not found")
                # Capture the projects the character is in before we mutate it, so
                # we can disassociate its pictures from the ones it leaves.
                old_project_ids = character_project_ids(session, id)
                target_project_ids, project_provided = _resolve_target_project_ids(
                    data, old_project_ids
                )
                # Check uniqueness before mutating anything.
                final_name = name if name is not None else character.name
                name_changing = name is not None and name != character.name
                project_changing = project_provided and sorted(
                    target_project_ids
                ) != sorted(old_project_ids)
                if name_changing or project_changing:
                    _ensure_unique_character_name(
                        session, final_name, target_project_ids, exclude_char_id=id
                    )
                previous_name = character.name
                updated = False
                # Tracked apart from ``updated`` because the invalidation below
                # is about IDENTITY: the name and description are baked into
                # every derived caption and text embedding of this person's
                # pictures, so changing them has to throw those away. The
                # thumbnail pin is not identity - it decides which existing crop
                # is shown - and letting it flip this flag would null the
                # description and text_embedding of EVERY picture the person
                # appears in on a single click, deleting hand-written
                # descriptions and queueing a library-wide re-derive.
                identity_changed = False
                if name is not None and name != character.name:
                    character.name = name
                    updated = True
                    identity_changed = True
                if description is not None and description != character.description:
                    character.description = description
                    updated = True
                    identity_changed = True
                if thumbnail_picture_id is not _UNSET:
                    pinned = thumbnail_picture_id
                    # Only a picture this person actually appears in may be
                    # pinned: the thumbnail is a crop of THEIR face, so an id
                    # with no face of theirs would render nothing and silently
                    # fall back.
                    #
                    # ``deleted`` is part of that, and not decoration: a
                    # scrapheaped picture keeps its faces, so without this the
                    # PATCH would accept an id the renderer refuses (its
                    # selection filters on ``deleted``) and store a pin that
                    # nothing can ever honour. Accept only what will be used.
                    if (
                        pinned is not None
                        and not session.exec(
                            select(Face.id)
                            .join(Picture, Picture.id == Face.picture_id)
                            .where(
                                Face.picture_id == pinned,
                                Face.character_id == id,
                                Picture.deleted.is_(False),
                            )
                        ).first()
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                "thumbnail_picture_id must name a picture, not in "
                                "the scrapheap, with a face assigned to this "
                                "character"
                            ),
                        )
                    if pinned != character.thumbnail_picture_id:
                        character.thumbnail_picture_id = pinned
                        updated = True
                # Single write path for both the join rows and the primary-project
                # FK; raises 404 for an unknown project id.
                project_change = None
                if project_changing:
                    project_change = set_character_projects(
                        session, character, target_project_ids
                    )
                    updated = updated or project_change.changed
                    # A project move invalidated the derived fields before the
                    # pin existed; keep it doing so. The pin is the ONLY thing
                    # this change takes out of that set.
                    identity_changed = identity_changed or project_change.changed
                local_project_membership_updated = False
                if updated:
                    session.add(character)

                    if project_change is not None and project_change.changed:
                        picture_ids = list(
                            {
                                face.picture_id
                                for face in session.exec(
                                    select(Face).where(Face.character_id == id)
                                ).all()
                                if face.picture_id is not None
                            }
                        )
                        # Project membership is stack-atomic: a character on one
                        # member of a stack moves the whole stack's membership.
                        picture_ids = expand_picture_ids_to_stacks(session, picture_ids)
                        # Reference-aware add/remove/repoint across every project
                        # joined and left - shared with picture set updates.
                        reconcile_entity_projects_change(
                            session,
                            picture_ids=picture_ids,
                            ensure_project_ids=project_change.target_project_ids,
                            remove_project_ids=project_change.removed,
                            exclude_character_id=id,
                        )
                        local_project_membership_updated = bool(picture_ids)

                    # Invalidate machine-derived fields for this character's
                    # pictures so they are re-derived after the change. The text
                    # embedding is machine-derived (rule 4) and always cleared, but
                    # the description is frozen on a locked picture (rule 3): skip
                    # clearing it there. Character reassignment itself stays allowed.
                    if identity_changed:
                        character_pic_ids = [
                            face.picture_id
                            for face in session.exec(
                                select(Face).where(Face.character_id == id)
                            ).all()
                            if face.picture_id is not None
                        ]
                        locked_pics = locked_picture_ids(session, character_pic_ids)
                        for pic_id in character_pic_ids:
                            pic = session.get(Picture, pic_id)
                            if pic:
                                if pic.id not in locked_pics:
                                    pic.description = None
                                pic.text_embedding = None
                                session.add(pic)

                    session.commit()
                    session.refresh(character)
                if name_changing:
                    # **Renaming a person renames their FOLDER; it moves no
                    # files** (v1.11 §4). Required rather than tidy: the layout
                    # reads folder names against the library's current
                    # vocabulary, so a folder left under the old name names
                    # nobody and its pictures fall out of the rule for good.
                    # Commits for itself, and rolls the directories back if
                    # it cannot: the renames and the ``file_path`` rewrites
                    # describing them have to land together.
                    rename_entity_folders(
                        session,
                        Facet.PERSON,
                        previous_name,
                        character.name,
                        image_root=server.vault.image_root,
                    )
                # Serialize while the session is open; the row may be detached
                # (and its attributes expired) by the time the handler returns.
                payload = character.model_dump(exclude_unset=False)
                payload["project_ids"] = character_project_ids(session, id)
                return (payload, local_project_membership_updated)

            char, project_membership_updated = server.vault.db.run_task(
                alter_char,
                id,
                name,
                description,
                thumbnail_picture_id,
                priority=DBPriority.IMMEDIATE,
            )
            server.vault.notify(
                EventType.CHANGED_CHARACTERS,
                {"origin_client_id": origin_client_id},
            )
            if project_membership_updated:
                server.vault.notify(
                    EventType.CHANGED_PICTURES,
                    {
                        "source": "ui",
                        "origin_client_id": origin_client_id,
                        "change_kind": "updated",
                    },
                )

        except KeyError:
            raise HTTPException(status_code=404, detail="Character not found")

        return {"status": "success", "character": char}

    @router.delete(
        "/characters/{id}",
        summary="Delete character",
        description="Deletes a character, clears character assignment from faces, and removes its reference set when present.",
        response_model=CharacterDeleteResponse,
    )
    def delete_character(id: int, request: Request):
        origin_client_id = getattr(request.state, "origin_client_id", None)
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
            server.vault.notify(
                EventType.CHANGED_CHARACTERS,
                {"origin_client_id": origin_client_id},
            )
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
        request: Request,
        picture_ids: list[int] = Body(default=[], embed=True),
    ):
        if not picture_ids:
            return {"character_assignments": {}, "pictures_with_faces": []}

        # Scope guard (BOLA): restrict a READ-scoped share token to picture ids
        # within its granted resource.  None == owner / unscoped == no filter.
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        if scope_allowed is not None:
            picture_ids = [pid for pid in picture_ids if pid in scope_allowed]
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
        try:
            # A scoped token may read the character but must not learn which
            # *other* projects it belongs to (issue #125 / R1).
            visible_projects = visible_project_ids(server, request)

            def fetch(session):
                found = Character.find(session, id=id)
                if not found:
                    return None
                payload = safe_model_dict(found[0])
                narrow_project_fields(
                    payload, character_project_ids(session, id), visible_projects
                )
                return payload

            return server.vault.db.run_immediate_read_task(fetch)
        except KeyError:
            raise HTTPException(status_code=404, detail="Character not found")

    @router.get(
        "/projects/{project_name}/characters/{character_name}",
        summary="Get character by project name and character name",
        description="Returns a character record by name within a named project.",
        response_model=CharacterResponse,
    )
    def get_character_by_project_and_name(
        request: Request, project_name: str, character_name: str
    ):
        visible_projects = visible_project_ids(server, request)

        def fetch(session):
            project = session.exec(
                select(Project).where(func.lower(Project.name) == project_name.lower())
            ).first()
            # Scope guard on the PROJECT half of the path - the picture-set twin
            # in picture_sets.py::get_picture_set_by_name has the same guard for
            # the same reason (#708 condition 2): the 404 branches below answer
            # from the project space, which a character-scoped token may not
            # probe. One uniform 403 for a project it may not see; an owner is
            # unaffected and still gets the 404s.
            enforce_project_path_scope(
                server, request, int(project.id) if project is not None else None
            )
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            character = session.exec(
                select(Character).where(
                    character_in_project(project.id),
                    func.lower(Character.name) == character_name.lower(),
                )
            ).first()
            if character is None:
                raise HTTPException(status_code=404, detail="Character not found")
            payload = safe_model_dict(character)
            narrow_project_fields(
                payload,
                character_project_ids(session, int(character.id)),
                visible_projects,
            )
            return payload

        result = server.vault.db.run_immediate_read_task(fetch)
        # Scope guard (BOLA): a resource-scoped token may only read its own
        # character - the id-based twin (get_character_by_id) already does this.
        _require_scope_allows_character(request, int(result["id"]))
        return result

    @router.get(
        "/characters/{id}/faces",
        summary="List character faces",
        description=(
            "Returns the face rows assigned to a character: `id`, `picture_id`, "
            "`character_id`, `frame_index`, `face_index` and the pixel `xyxy` "
            "`bbox`.\n\n"
            "The face **embedding** (`features`) and the embedding's model pack "
            "are not served."
        ),
        response_model=FaceListResponse,
    )
    def list_character_faces(request: Request, id: int):
        # Dedicated, projected replacement for `GET /characters/{id}/{field}`
        # with field="faces", which served the ORM relationship and therefore
        # the embedding too (issue #721).
        #
        # DECLARED HERE, NOT IN characters_faces.py, AND ORDERING IS
        # LOAD-BEARING. `server.py` includes this router BEFORE
        # `characters_faces`, so a GET declared over there would be swallowed by
        # the `/characters/{id}/{field}` catch-all below. Within this router the
        # order is definition order, so this must stay ABOVE that route. Its
        # POST/DELETE siblings live in `characters_faces.py` and are unaffected:
        # they differ by method, and Starlette falls through a path-only match.
        def fetch_faces(session: Session):
            # Ordered by id to reproduce the row set and order that the
            # `Character.faces` relationship produced on the old path.
            rows = session.exec(
                select(Face).where(Face.character_id == id).order_by(Face.id)
            ).all()
            return [face.to_public_dict() for face in rows]

        return {"faces": server.vault.db.run_immediate_read_task(fetch_faces)}

    @router.get(
        "/characters/{id}/{field}",
        summary="Get character field",
        description=(
            "Returns one character field value, including generated thumbnail "
            "handling for `field=thumbnail`.\n\n"
            "Only the character's own **columns** are readable here, plus the "
            "synthesised `thumbnail`. ORM relationship names (`project`, "
            "`pictures`, `reference_picture_set`) are **not** readable and "
            "answer `400`; use their dedicated endpoints instead. A `400` means "
            "'not a readable field' and is distinct from `404` "
            "('character does not exist') and `403` ('not in this token's scope')."
        ),
        responses={
            200: {
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "additionalProperties": True}
                    },
                    "image/png": {},
                }
            },
            400: {
                "description": (
                    "`field` is not a readable field on this endpoint (a "
                    "relationship name, or a name the model does not have)."
                )
            },
            404: {"description": "The character does not exist."},
        },
    )
    def get_character_field_by_id(request: Request, id: int, field: str):
        # Deny-by-default: only the character's own column namespace (plus the
        # declared exceptions, which include the synthesised ``thumbnail``) is
        # servable. This runs BEFORE any lookup so the refusal cannot depend on
        # whether the character exists. Object authorization is not this check's
        # job and must not be added here -- the AuthzGate has already run
        # (issue #721, §16.6).
        require_servable_field(Character, field, CHARACTER_EXTRA_SERVABLE_FIELDS)

        if field == "thumbnail":
            # 8, not 7: the cached metadata gained ``pinned_picture_id`` and the
            # selection it records changed meaning, so every library's existing
            # crop has to be re-derived once rather than served under the new
            # contract.
            thumbnail_cache_version = 8
            cache_dir = os.path.join(server.vault.image_root, "tmp", "face_thumbnails")
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = resolve_path_within(cache_dir, f"character_{id}.png")
            meta_path = resolve_path_within(cache_dir, f"character_{id}.json")

            def fetch_best_picture_id(session: Session, character_id: int):
                candidates = (
                    select(Picture.id, Picture.score)
                    .join(Face, Face.picture_id == Picture.id)
                    .where(
                        Face.character_id == character_id,
                        Picture.deleted.is_(False),
                    )
                )
                # A pinned picture wins outright, but only while it still holds
                # a face of this character (the same query, narrowed) - the pin
                # is a plain id, so a purged or reassigned picture has to fall
                # through to the automatic choice rather than 404 the person's
                # avatar.
                character = session.get(Character, character_id)
                pinned_id = character.thumbnail_picture_id if character else None
                row = None
                if pinned_id is not None:
                    row = session.exec(
                        candidates.where(Picture.id == pinned_id).limit(1)
                    ).first()
                pinned = row is not None
                if row is None:
                    row = session.exec(
                        candidates.order_by(
                            Picture.is_video,  # prefer stills over videos
                            Picture.score.is_(None),
                            Picture.score.desc(),
                            Picture.id.desc(),
                        ).limit(1)
                    ).first()
                if not row:
                    return None
                pic_id, score = row
                return {
                    "picture_id": int(pic_id),
                    "score": float(score) if score is not None else None,
                    "pinned": pinned,
                    # Part of the cache identity in its own right, and not
                    # derivable from ``picture_id``: on the automatic path that
                    # id names the query's winner, which is NOT always the
                    # picture the render below crops (it can fall through to the
                    # reference set or to `char.faces`). Pinning the picture the
                    # query already named would therefore leave the key
                    # unchanged and serve the old crop - the most likely click
                    # of all, since the grid is ordered by the same scorer.
                    "pinned_picture_id": (
                        int(pinned_id) if pinned_id is not None else None
                    ),
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
                        and meta.get("pinned_picture_id")
                        == best_picture.get("pinned_picture_id")
                        and meta.get("version") == thumbnail_cache_version
                    ):
                        return conditional_file_response(request, cache_path)
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

            # The pin, resolved through the same two helpers the fallback path
            # below uses. `pinned` was only set by a query that joined a face of
            # this character onto a non-deleted picture, so both lookups hit.
            if best_picture.get("pinned"):
                pinned_id = best_picture["picture_id"]
                pics = server.vault.db.run_immediate_read_task(
                    Picture.find, id=pinned_id
                )
                faces = server.vault.db.run_immediate_read_task(
                    Face.find, picture_id=pinned_id
                )
                best_pic = pics[0] if pics else None
                best_face = next((f for f in faces if f.character_id == char.id), None)
                if not (best_pic and best_face):
                    best_pic = best_face = None

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
            if not (best_pic and best_face) and ref_set and ref_set.members:
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
            # 256, not 64: the in-app consumer is a 24 px ModelMark, but this
            # is the only character image the API serves, and a picker that
            # renders it in a ~150 px HiDPI grid cell had nothing else to ask
            # for. Bump ``thumbnail_cache_version`` above when this changes.
            crop = crop.resize((256, 256), Image.LANCZOS)
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
                return conditional_file_response(request, cache_path)
            except Exception:
                from io import BytesIO

                buf = BytesIO()
                crop.save(buf, format="PNG")
                return Response(content=buf.getvalue(), media_type="image/png")
        try:
            if field == "project_id":
                # The stored scalar names the character's *primary* project,
                # which a token scoped to the character (or to a secondary
                # project) has no grant to learn. Derive it from the narrowed
                # membership list like every other serialisation site does
                # (issue #125 / R1b, #708 F5).
                visible_projects = visible_project_ids(server, request)

                def fetch_project_ids(session: Session):
                    if session.get(Character, id) is None:
                        raise KeyError("Character not found")
                    return character_project_ids(session, id)

                payload: dict = {}
                narrow_project_fields(
                    payload,
                    server.vault.db.run_immediate_read_task(fetch_project_ids),
                    visible_projects,
                )
                return {"project_id": payload["project_id"]}
            char = server.vault.db.run_immediate_read_task(
                Character.find, select_fields=[field], id=id
            )
            if not char:
                raise KeyError("Character not found")
            char = char[0]
            logger.debug(
                "Data type for Character field {}: {}".format(field, type(char))
            )
            # Backstop only. `require_servable_field` above has already refused
            # anything outside the column namespace with a 400, and a column
            # name always resolves to an attribute, so this branch is not
            # reachable today. Kept because it fails closed if the allowlist and
            # the model ever disagree; it is NOT the load-bearing check.
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
        "or ``UNASSIGNED`` for characters with no project.\n\n"
        "Pass ``include_counts=true`` to get each character's picture counts "
        "(``image_count`` and ``project_image_count``) inline, so a sidebar does "
        "not need one ``GET /characters/{id}/summary`` request per character.",
        response_model=list[CharacterListItemResponse],
    )
    def get_characters(
        request: Request,
        name: str = Query(
            None, description="Return only the character with this exact name."
        ),
        project_id: str | None = Query(
            default=None,
            description=(
                "Restrict the listing to one project: a numeric project id, or "
                "``UNASSIGNED`` for characters that belong to no project. Omit "
                "for every character the caller may see. Note this filters "
                "WHICH characters are listed; it does not change the scope of "
                "``project_image_count``, which always follows each row's own "
                "``project_id``."
            ),
        ),
        include_counts: bool = Query(
            default=False,
            description=(
                "When true, every row carries ``image_count`` (whole-vault) and "
                "``project_image_count`` (this row's own project). Both cost a "
                "constant number of extra queries for the whole listing. "
                "Defaults to false, so existing callers pay nothing."
            ),
        ),
    ):
        token_scope = getattr(request.state, "token_scope", None)
        visible_projects = visible_project_ids(server, request)
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
                        query = query.where(character_in_no_project())
                    else:
                        try:
                            query = query.where(character_in_project(int(project_id)))
                        except (TypeError, ValueError):
                            raise HTTPException(
                                status_code=400, detail="Invalid project_id"
                            )
                characters = session.exec(query).all()

                # Annotate each character with whether it has at least one face
                # embedding so the UI can filter the similarity-sort dropdown.
                char_ids = [c.id for c in characters]
                if char_ids:
                    listed = set(char_ids)
                    chars_with_faces = {
                        cid
                        for cid in session.exec(
                            characters_with_reference_faces_query()
                        ).all()
                        if cid in listed
                    }
                else:
                    chars_with_faces = set()

                # One query for every listed character's project membership, so
                # the multi-project set is exposed without an N+1 (issue #125).
                project_ids_by_char: dict[int, list[int]] = {}
                if char_ids:
                    for cid, pid in session.exec(
                        select(
                            CharacterProjectMember.character_id,
                            CharacterProjectMember.project_id,
                        ).where(CharacterProjectMember.character_id.in_(char_ids))
                    ).all():
                        project_ids_by_char.setdefault(int(cid), []).append(int(pid))

                # Narrow once: the counts below must be scoped to the project
                # each row REPORTS, and narrow_project_fields derives that same
                # scalar from this list. Re-narrowing an already-narrowed list
                # is idempotent, so the payload still goes through the one
                # helper that owns both project fields.
                narrowed_by_char = {
                    int(c.id): filter_visible_project_ids(
                        project_ids_by_char.get(int(c.id), []), visible_projects
                    )
                    for c in characters
                    if c.id is not None
                }

                # Sidebar counts, inline and for the WHOLE listing in a constant
                # number of queries (issue #651). Computed only for the rows the
                # scope filtering above already returned, so a scoped token
                # learns nothing it could not read from the per-id summary
                # endpoint it is already granted.
                global_counts: dict[int, int] = {}
                project_counts: dict[int, int] | None = {}
                if include_counts:
                    global_counts, project_counts = _inline_character_counts(
                        session, characters, narrowed_by_char, visible_projects
                    )

                rows = []
                for c in characters:
                    payload = {
                        **c.model_dump(exclude_unset=False),
                        "has_reference_faces": c.id in chars_with_faces,
                    }
                    if include_counts:
                        char_id = int(c.id)
                        payload["image_count"] = global_counts.get(char_id, 0)
                        # ``None`` means "you may learn no project-scoped number",
                        # which is NOT the same answer as 0 and must not collapse
                        # into one via ``.get(char_id, 0)``.
                        payload["project_image_count"] = (
                            None
                            if project_counts is None
                            else project_counts.get(char_id, 0)
                        )
                    rows.append(
                        narrow_project_fields(
                            payload,
                            narrowed_by_char.get(int(c.id), []),
                            visible_projects,
                        )
                    )
                return rows

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
        description=(
            "Creates a character and its linked reference picture set. Accepts "
            "``project_ids`` (a list of projects the character joins) or the "
            "legacy single ``project_id``."
        ),
        response_model=CharacterMutationResponse,
    )
    def create_character(request: Request, payload: dict = Body(...)):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:

            def create_character_and_reference_set(session, payload):
                char_name = payload.get("name")
                target_project_ids, _provided = _resolve_target_project_ids(
                    payload, None
                )
                if char_name:
                    _ensure_unique_character_name(
                        session, char_name, target_project_ids
                    )
                # ``project_id`` / ``project_ids`` are owned by
                # ``set_character_projects`` below (write both, read the join), so
                # they must not reach the Character constructor.
                fields = {
                    key: value
                    for key, value in payload.items()
                    if key not in ("project_id", "project_ids")
                }
                character = Character(**fields)
                session.add(character)
                session.commit()
                session.refresh(character)
                if target_project_ids:
                    set_character_projects(session, character, target_project_ids)
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
                created = character.model_dump(exclude_unset=False)
                created["project_ids"] = character_project_ids(
                    session, int(character.id)
                )
                return created

            char_dict = server.vault.db.run_task(
                create_character_and_reference_set,
                payload,
                priority=DBPriority.IMMEDIATE,
            )
            logger.debug("Created character: {}".format(char_dict))
            server.vault.notify(
                EventType.CHANGED_CHARACTERS,
                {"origin_client_id": origin_client_id},
            )
            return {"status": "success", "character": char_dict}
        except HTTPException:
            # Deliberate, already-typed failures (409 duplicate name, 404 unknown
            # project, 400 malformed project_ids) must keep their status; the
            # blanket handler below would otherwise flatten them all to 400.
            raise
        except Exception as e:
            logger.error(f"Error creating character: {e}")
            raise HTTPException(status_code=400, detail="Invalid character data")

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
