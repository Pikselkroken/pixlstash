import json
import os
import sys
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select
from sqlalchemy import desc, exists, func, nullslast
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from pixlstash.authz.membership import enforce_set_scope
from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Character,
    Picture,
    PictureProjectMember,
    Project,
    PictureSet,
    PictureSetMember,
    SortMechanism,
    Tag,
)
from pixlstash.event_types import EventType
from pixlstash.services import operation_log_service
from pixlstash.services.project_membership_service import (
    reconcile_entity_project_change,
)
from pixlstash.services.set_lock_service import (
    enforce_set_not_locked,
)
from pixlstash.services.stack_membership import expand_picture_ids_to_stacks
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.filter_helpers import fetch_scope_allowed_picture_ids
from pixlstash.picture_scoring import (
    find_pictures_by_character_likeness,
    find_pictures_by_smart_score,
    get_smart_score_penalised_tags_from_request,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.service.caption_utils import normalize_hidden_tags
from pixlstash.utils.path_utils import resolve_path_within
from pixlstash.utils.serialization_utils import safe_model_dict
from pixlstash.utils.stack.stack_utils import deduplicate_by_stack
from pixlstash.utils.query.predicate_filter import PredicateFilter

logger = get_logger(__name__)

_UNSET = object()


class PictureSetResponse(BaseModel):
    """Picture set metadata as returned by set listing and lookup endpoints."""

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    project_id: Optional[int] = None
    set_icon: Optional[str] = None
    set_color: Optional[str] = None
    locked: bool = False
    picture_count: Optional[int] = None
    top_picture_ids: Optional[list[int]] = None
    thumbnail_url: Optional[str] = None


class PictureSetCreateResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    picture_set: dict


class PictureSetPicturesResponse(BaseModel):
    """Set contents response.

    Covers both shapes returned by ``get_picture_set``: the ``info=true`` path
    returns picture set metadata (id/name/.../picture_count), while the default
    path returns ``{"pictures": [...], "set": {...}}``. All fields are optional
    so neither shape drops data, and ``extra="allow"`` keeps anything else.
    """

    model_config = ConfigDict(extra="allow")

    pictures: Optional[list[dict]] = None
    set: Optional[PictureSetResponse] = None
    id: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    project_id: Optional[int] = None
    set_icon: Optional[str] = None
    set_color: Optional[str] = None
    locked: bool = False
    picture_count: Optional[int] = None


class PictureSetUpdateResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str


class PictureSetDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    deleted_id: int


class PictureSetMembersResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    picture_ids: list[int]


class PictureSetAddPictureResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str


class PictureSetRemovePictureResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str


class PictureSetBulkAddResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    added: int


class PictureSetBulkReplaceResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    members: int


class LockedSetSummary(BaseModel):
    """One locked set and the ids of the pictures it currently freezes."""

    model_config = ConfigDict(extra="allow")

    id: int
    name: str
    picture_ids: list[int] = []


class LockedSetMembersResponse(BaseModel):
    """All locked sets visible to the caller, with their frozen member ids.

    Lets the frontend learn which pictures are read-only (and by which set) in one
    round-trip, for grid badges, overlay reasons, and context-menu tooltips.
    """

    model_config = ConfigDict(extra="allow")

    sets: list[LockedSetSummary] = []


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _ensure_unique_set_name(session, name: str, project_id, exclude_set_id=None):
        """Raises 409 if a set with the same name (case-insensitive) already
        exists in the given project.  Unscoped sets (project_id None) are
        exempt — they have no uniqueness requirement.
        """
        if project_id is None:
            return
        stmt = select(PictureSet).where(
            PictureSet.project_id == project_id,
            func.lower(PictureSet.name) == name.lower(),
        )
        if exclude_set_id is not None:
            stmt = stmt.where(PictureSet.id != exclude_set_id)
        if session.exec(stmt).first():
            raise HTTPException(
                status_code=409,
                detail=f"A picture set named '{name}' already exists in this project.",
            )

    def _project_membership_exists(project_id_value: int):
        return exists(
            select(PictureProjectMember.picture_id).where(
                PictureProjectMember.picture_id == Picture.id,
                PictureProjectMember.project_id == project_id_value,
            )
        )

    def _project_membership_unassigned():
        return ~exists(
            select(PictureProjectMember.picture_id).where(
                PictureProjectMember.picture_id == Picture.id
            )
        )

    def _enrich_with_stack_counts(pictures: list[dict]) -> list[dict]:
        if not pictures:
            return pictures

        picture_ids = [
            int(pic.get("id"))
            for pic in pictures
            if isinstance(pic, dict) and pic.get("id") is not None
        ]
        if not picture_ids:
            return pictures

        def fetch_stack_info(session: Session, ids: list[int]):
            id_stack_rows = session.exec(
                select(Picture.id, Picture.stack_id).where(
                    Picture.id.in_(ids),
                    Picture.deleted.is_(False),
                )
            ).all()
            stack_ids = sorted(
                {
                    int(stack_id)
                    for _pic_id, stack_id in id_stack_rows
                    if stack_id is not None
                }
            )
            if not stack_ids:
                return id_stack_rows, []

            stack_count_rows = session.exec(
                select(Picture.stack_id, func.count(Picture.id))
                .where(
                    Picture.stack_id.in_(stack_ids),
                    Picture.deleted.is_(False),
                )
                .group_by(Picture.stack_id)
            ).all()
            return id_stack_rows, stack_count_rows

        id_stack_rows, stack_count_rows = server.vault.db.run_immediate_read_task(
            fetch_stack_info, picture_ids
        )
        stack_id_by_picture_id = {
            int(pic_id): stack_id for pic_id, stack_id in id_stack_rows
        }
        stack_count_by_stack_id = {
            int(stack_id): int(count)
            for stack_id, count in stack_count_rows
            if stack_id is not None
        }

        enriched: list[dict] = []
        for pic in pictures:
            if not isinstance(pic, dict):
                enriched.append(pic)
                continue
            picture_id = pic.get("id")
            if picture_id is None:
                enriched.append(pic)
                continue
            numeric_id = int(picture_id)
            stack_id = pic.get("stack_id")
            if stack_id is None:
                stack_id = stack_id_by_picture_id.get(numeric_id)
            stack_count = 0
            if stack_id is not None:
                stack_count = stack_count_by_stack_id.get(int(stack_id), 1)
            enriched.append(
                {
                    **pic,
                    "stack_id": stack_id,
                    "stack_count": stack_count,
                }
            )
        return enriched

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

    def _filter_hidden_picture_ids(
        session, picture_ids: list[int], hidden_tags: list[str]
    ) -> list[int]:
        if not picture_ids or not hidden_tags:
            return picture_ids
        hidden_tag_set = {str(tag).strip().lower() for tag in hidden_tags if tag}
        rows = session.exec(
            select(Tag.picture_id).where(
                Tag.picture_id.in_(picture_ids),
                Tag.tag.is_not(None),
                func.lower(Tag.tag).in_(hidden_tag_set),
            )
        ).all()
        hidden_ids = {row for row in rows if row is not None}
        return [pic_id for pic_id in picture_ids if pic_id not in hidden_ids]

    def _find_reference_character_id_for_set(picture_set_id):
        # Find reference_character_id if this is a reference set
        def find_reference_character(session, picture_set_id):
            character = Character.find(
                session,
                select_fields=["reference_picture_set_id"],
                reference_picture_set_id=picture_set_id,
            )
            logger.debug(
                f"Found reference character for set {picture_set_id}: {character}"
            )
            return character[0].id if character else None

        return server.vault.db.run_immediate_read_task(
            find_reference_character, picture_set_id
        )

    def _require_scope_allows_picture_set(request: Request, set_id: int):
        """Raise 403 if the token scope does not cover the requested picture set.

        Thin delegation to the single membership implementation in
        ``pixlstash/authz/membership.py`` (backend refactor plan §3.7, Step 4).
        The authz gate calls the same function directly for SET_SCOPED routes;
        Step 5 removes this shim.
        """
        enforce_set_scope(server, request, set_id)

    @router.get(
        "/picture_sets",
        summary="List picture sets",
        description="Returns picture sets with visible member counts, top pictures, and thumbnail URLs.",
        response_model=list[PictureSetResponse],
    )
    def get_picture_sets(request: Request, project_id: str | None = Query(None)):
        # Restrict listing to the token's resource when a scoped READ token is used
        token_scope = getattr(request.state, "token_scope", None)
        scope_set_id_filter = None
        if token_scope is not None and token_scope.resource_type == "picture_set":
            scope_set_id_filter = token_scope.resource_id
        elif token_scope is not None and token_scope.resource_type == "project":
            # Restrict to sets belonging to the authorised project
            project_id = str(token_scope.resource_id)
        elif token_scope is not None and token_scope.resource_type is not None:
            # Any other scoped token (e.g. character) has no access to picture sets
            return []
        hidden_tags = _get_hidden_tags_from_request(request)

        project_filter = None
        set_project_id_filter = None  # filters which sets are returned
        if project_id is not None:
            if project_id == "UNASSIGNED":
                project_filter = _project_membership_unassigned()
                set_project_id_filter = PictureSet.project_id.is_(None)
            else:
                try:
                    _pid_int = int(project_id)
                    project_filter = _project_membership_exists(_pid_int)
                    set_project_id_filter = PictureSet.project_id == _pid_int
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="Invalid project_id")

        def fetch_sets(session):
            sets_query = (
                select(PictureSet)
                .options(selectinload(PictureSet.reference_character))
                .order_by(func.lower(PictureSet.name))
            )
            if set_project_id_filter is not None:
                sets_query = sets_query.where(set_project_id_filter)
            if scope_set_id_filter is not None:
                sets_query = sets_query.where(PictureSet.id == scope_set_id_filter)
            sets = session.exec(sets_query).all()
            result = []
            for s in sets:
                members_query = (
                    select(PictureSetMember.picture_id)
                    .join(Picture, Picture.id == PictureSetMember.picture_id)
                    .where(
                        PictureSetMember.set_id == s.id,
                        Picture.deleted.is_(False),
                    )
                )
                if project_filter is not None:
                    members_query = members_query.where(project_filter)
                members = session.exec(members_query).all()
                filtered_ids = _filter_hidden_picture_ids(
                    session,
                    [m for m in members if m is not None],
                    hidden_tags,
                )
                count = len(set(filtered_ids))
                set_dict = safe_model_dict(s)
                set_dict["picture_count"] = count
                top_picture_ids = []
                if filtered_ids:
                    top_rows = session.exec(
                        select(Picture.id)
                        .where(Picture.id.in_(filtered_ids))
                        .order_by(
                            nullslast(desc(Picture.score)),
                            nullslast(desc(Picture.aesthetic_score)),
                            nullslast(desc(Picture.imported_at)),
                            desc(Picture.id),
                        )
                        .limit(3)
                    ).all()
                    top_picture_ids = [row for row in top_rows if row is not None]
                set_dict["top_picture_ids"] = top_picture_ids
                set_dict["thumbnail_url"] = f"/picture_sets/{s.id}/thumbnail"
                result.append(set_dict)
            return result

        result = safe_model_dict(server.vault.db.run_immediate_read_task(fetch_sets))
        logger.debug(f"Fetched picture set {result}")
        return result

    @router.get(
        "/picture_sets/locked-members",
        summary="List locked sets and their frozen pictures",
        description=(
            "Returns every locked picture set the caller may see, each with the ids "
            "of its non-deleted member pictures. Read-only, cheap (touches only "
            "locked sets), and the single source the frontend uses to badge locked "
            "pictures. Registered before /picture_sets/{id} so the literal segment "
            "is not captured by the numeric id path parameter."
        ),
        response_model=LockedSetMembersResponse,
    )
    def get_locked_set_members(request: Request):
        # Scope: owner/unscoped sees all locked sets; a picture_set-scoped token
        # sees only its own set; a project-scoped token only that project's sets;
        # any other scoped token has no visibility into sets.
        token_scope = getattr(request.state, "token_scope", None)
        scope_set_id = None
        scope_project_id = None
        if token_scope is not None and token_scope.resource_type == "picture_set":
            scope_set_id = token_scope.resource_id
        elif token_scope is not None and token_scope.resource_type == "project":
            scope_project_id = token_scope.resource_id
        elif token_scope is not None and token_scope.resource_type is not None:
            return {"sets": []}

        def fetch_locked(session):
            sets_query = select(PictureSet).where(PictureSet.locked.is_(True))
            if scope_set_id is not None:
                sets_query = sets_query.where(PictureSet.id == scope_set_id)
            if scope_project_id is not None:
                sets_query = sets_query.where(PictureSet.project_id == scope_project_id)
            locked_sets = session.exec(
                sets_query.order_by(func.lower(PictureSet.name))
            ).all()
            payload = []
            for s in locked_sets:
                member_ids = [
                    int(m)
                    for m in session.exec(
                        select(PictureSetMember.picture_id)
                        .join(Picture, Picture.id == PictureSetMember.picture_id)
                        .where(
                            PictureSetMember.set_id == s.id,
                            Picture.deleted.is_(False),
                        )
                    ).all()
                    if m is not None
                ]
                payload.append(
                    {
                        "id": int(s.id),
                        "name": s.name,
                        "picture_ids": sorted(set(member_ids)),
                    }
                )
            return payload

        sets = server.vault.db.run_immediate_read_task(fetch_locked)
        return {"sets": sets}

    @router.get(
        "/projects/{project_name}/picture_sets/{picture_set_name}",
        summary="Get picture set by project name and set name",
        description="Returns picture set metadata for a named set within a named project.",
        response_model=PictureSetResponse,
    )
    def get_picture_set_by_name(
        request: Request, project_name: str, picture_set_name: str
    ):
        def fetch(session):
            project = session.exec(
                select(Project).where(func.lower(Project.name) == project_name.lower())
            ).first()
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            picture_set = session.exec(
                select(PictureSet).where(
                    PictureSet.project_id == project.id,
                    func.lower(PictureSet.name) == picture_set_name.lower(),
                )
            ).first()
            if picture_set is None:
                raise HTTPException(status_code=404, detail="Picture set not found")
            return safe_model_dict(picture_set)

        result = server.vault.db.run_immediate_read_task(fetch)
        # Scope guard (BOLA): a resource-scoped token may only read its own set —
        # the id-based twin (get_picture_set) already does this.
        _require_scope_allows_picture_set(request, int(result["id"]))
        return result

    # Palette and icon list kept in sync with the frontend constants.
    _SET_COLORS = [
        "#e53935",
        "#00acc1",
        "#f4511e",
        "#039be5",
        "#ff7043",
        "#546e7a",
        "#fb8c00",
        "#1e88e5",
        "#fdd835",
        "#3949ab",
        "#c0ca33",
        "#9c27b0",
        "#7cb342",
        "#8e24aa",
        "#43a047",
        "#d81b60",
        "#00897b",
        "#f06292",
        "#00bfa5",
        "#6d4c41",
        "#ff5252",
        "#00e5ff",
        "#ff6d00",
        "#2979ff",
        "#ffd740",
        "#651fff",
        "#64dd17",
        "#e040fb",
        "#1de9b6",
        "#f50057",
        "#b71c1c",
        "#006064",
        "#e65100",
        "#0d47a1",
        "#827717",
        "#4a148c",
        "#1b5e20",
        "#880e4f",
        "#004d40",
        "#37474f",
        "#ce93d8",
        "#ef9a9a",
        "#81d4fa",
        "#ff8a65",
        "#ffb300",
        "#80cbc4",
        "#a1887f",
        "#76ff03",
    ]
    _SET_ICONS = [
        "mdi-star",
        "mdi-heart",
        "mdi-camera",
        "mdi-music",
        "mdi-run",
        "mdi-bike",
        "mdi-hiking",
        "mdi-pine-tree",
        "mdi-flower",
        "mdi-beach",
        "mdi-airplane",
        "mdi-city-variant",
        "mdi-food-apple",
        "mdi-trophy",
        "mdi-home",
        "mdi-bookmark",
        "mdi-crown",
        "mdi-silverware-fork-knife",
        "mdi-briefcase",
        "mdi-gamepad-variant",
    ]

    def _auto_assign_icon_color(session, project_id):
        """Return (icon, color) not already used by sibling sets."""
        existing = session.exec(
            select(PictureSet.set_icon, PictureSet.set_color).where(
                PictureSet.project_id == project_id
            )
            if project_id is not None
            else select(PictureSet.set_icon, PictureSet.set_color)
        ).all()
        used_icons = {row[0] for row in existing if row[0] and row[0] != "cards"}
        used_colors = {row[1] for row in existing if row[1]}
        icon = next(
            (i for i in _SET_ICONS if i not in used_icons),
            _SET_ICONS[len(existing) % len(_SET_ICONS)],
        )
        color = next(
            (c for c in _SET_COLORS if c not in used_colors),
            _SET_COLORS[len(existing) % len(_SET_COLORS)],
        )
        return icon, color

    @router.post(
        "/picture_sets",
        summary="Create picture set",
        description="Creates a new picture set with name and optional description.",
        response_model=PictureSetCreateResponse,
    )
    def create_picture_set(payload: dict = Body(...)):
        name = payload.get("name")
        description = payload.get("description", "")
        project_id = payload.get("project_id") or None
        set_icon = payload.get("set_icon", _UNSET)
        set_color = payload.get("set_color", _UNSET)
        if not name:
            raise HTTPException(status_code=400, detail="name is required")

        def create_set(session, name, description, project_id, set_icon, set_color):
            _ensure_unique_set_name(session, name, project_id)
            auto_icon, auto_color = _auto_assign_icon_color(session, project_id)
            picture_set = PictureSet(
                name=name,
                description=description,
                project_id=project_id,
                set_icon=set_icon if set_icon is not _UNSET else auto_icon,
                set_color=set_color if set_color is not _UNSET else auto_color,
            )
            session.add(picture_set)
            session.commit()
            session.refresh(picture_set)
            return picture_set.dict()

        set_dict = server.vault.db.run_task(
            create_set,
            name,
            description,
            project_id,
            set_icon,
            set_color,
            priority=DBPriority.IMMEDIATE,
        )
        return {"status": "success", "picture_set": set_dict}

    @router.post(
        "/picture_sets/membership",
        summary="Batch set membership lookup",
        description=(
            "Given a list of picture IDs, returns a map of set_id → [picture_ids] "
            "for every set that contains at least one of the requested pictures. "
            "Also expands stack siblings so all stack members are considered. "
            "Used by the AddToSet menu to load membership in a single request."
        ),
        response_model=dict,
    )
    def get_batch_membership(
        request: Request,
        picture_ids: list[int] = Body(default=[]),
        include_deleted: bool = Body(False),
    ):
        if not picture_ids:
            return {}

        # Scope guard (BOLA): restrict a READ-scoped share token to picture ids
        # within its granted resource.  None == owner / unscoped == no filter.
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        if scope_allowed is not None:
            picture_ids = [pid for pid in picture_ids if pid in scope_allowed]
            if not picture_ids:
                return {}

        def fetch_membership(session, ids: list[int], include_deleted: bool):
            # Expand stacks: include all stack siblings of requested pictures.
            id_stack_rows = session.exec(
                select(Picture.id, Picture.stack_id).where(
                    Picture.id.in_(ids),
                    Picture.deleted.is_(False),
                )
            ).all()
            stack_ids = [int(sid) for _pid, sid in id_stack_rows if sid is not None]
            expanded_ids = set(ids)
            if stack_ids:
                extra_q = select(Picture.id).where(Picture.stack_id.in_(stack_ids))
                if not include_deleted:
                    extra_q = extra_q.where(Picture.deleted.is_(False))
                extra = session.exec(extra_q).all()
                expanded_ids |= {e for e in extra if e is not None}

            # Find all set memberships for the expanded id set.
            filters = [PictureSetMember.picture_id.in_(expanded_ids)]
            if not include_deleted:
                filters.append(Picture.deleted.is_(False))
            rows = session.exec(
                select(PictureSetMember.set_id, PictureSetMember.picture_id)
                .join(Picture, Picture.id == PictureSetMember.picture_id)
                .where(*filters)
            ).all()

            result: dict[int, list[int]] = {}
            for set_id, pid in rows:
                result.setdefault(int(set_id), []).append(int(pid))
            return result

        return server.vault.db.run_immediate_read_task(
            fetch_membership, picture_ids, include_deleted
        )

    @router.get(
        "/picture_sets/{id}/thumbnail",
        summary="Get picture set thumbnail",
        description="Returns or generates a cached composite thumbnail representing top-scoring pictures in a set.",
        response_class=Response,
        responses={200: {"content": {"image/png": {}}}},
    )
    def get_picture_set_thumbnail(id: int, request: Request):
        thumbnail_cache_version = 16
        cache_dir = os.path.join(server.vault.image_root, "tmp", "set_thumbnails")
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = resolve_path_within(cache_dir, f"picture_set_{id}.png")
        meta_path = resolve_path_within(cache_dir, f"picture_set_{id}.json")
        hidden_tags = _get_hidden_tags_from_request(request)
        hidden_key = "|".join(sorted(tag for tag in hidden_tags if tag))

        def fetch_top_picture_ids(
            session: Session,
            set_id: int,
            active_hidden_tags: list[str],
        ):
            member_rows = session.exec(
                select(PictureSetMember.picture_id)
                .join(Picture, Picture.id == PictureSetMember.picture_id)
                .where(
                    PictureSetMember.set_id == set_id,
                    Picture.deleted.is_(False),
                )
            ).all()
            member_ids = [row for row in member_rows if row is not None]
            filtered_ids = _filter_hidden_picture_ids(
                session,
                member_ids,
                active_hidden_tags,
            )
            if not filtered_ids:
                return []
            rows = session.exec(
                select(Picture.id)
                .where(Picture.id.in_(filtered_ids))
                .order_by(
                    nullslast(desc(Picture.score)),
                    nullslast(desc(Picture.aesthetic_score)),
                    nullslast(desc(Picture.imported_at)),
                    desc(Picture.id),
                )
                .limit(3)
            ).all()
            return [row for row in rows if row is not None]

        top_ids = server.vault.db.run_immediate_read_task(
            fetch_top_picture_ids,
            set_id=id,
            active_hidden_tags=hidden_tags,
        )
        if not top_ids:
            raise HTTPException(status_code=404, detail="No pictures found for set")

        if os.path.exists(cache_path) and os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as handle:
                    meta = json.load(handle)
                if (
                    meta.get("version") == thumbnail_cache_version
                    and meta.get("picture_ids") == top_ids
                    and meta.get("hidden_key") == hidden_key
                ):
                    return FileResponse(cache_path, media_type="image/png")
            except Exception as exc:
                logger.debug("Failed to read picture set thumbnail cache: %s", exc)

        def fetch_picture_paths(session: Session, picture_ids: list[int]):
            rows = session.exec(
                select(Picture.id, Picture.file_path).where(Picture.id.in_(picture_ids))
            ).all()
            return {int(row[0]): row[1] for row in rows if row and row[0] is not None}

        path_map = server.vault.db.run_immediate_read_task(
            fetch_picture_paths, picture_ids=top_ids
        )
        target_size = 64
        work_size = 256
        card_height = int(target_size * 0.75)
        card_width = max(1, int(card_height * 0.7))
        card_size = (card_width, card_height)
        angles = [20, 5, -20]
        offsets = [(0, 0), (0, 0), (0, -4)]
        base = Image.new("RGBA", (work_size, work_size), (0, 0, 0, 0))
        pivot_x = work_size // 2
        pivot_y = work_size // 2

        def build_card(image: Image.Image | None):
            if image is None:
                card = Image.new("RGBA", card_size, (255, 255, 255, 255))
            else:
                card = ImageOps.fit(image, card_size, Image.LANCZOS)
                if card.mode != "RGBA":
                    card = card.convert("RGBA")
            mask = Image.new("L", card_size, 0)
            draw = ImageDraw.Draw(mask)
            draw.rounded_rectangle(
                (0, 0, card_size[0], card_size[1]), radius=6, fill=255
            )
            card.putalpha(mask)
            draw = ImageDraw.Draw(card)
            draw.rounded_rectangle(
                (-2, -2, card_size[0] + 1, card_size[1] + 1),
                radius=7,
                outline=(170, 170, 170, 255),
                width=2,
            )
            return card

        cards = []
        for picture_id in top_ids:
            file_path = path_map.get(picture_id)
            resolved_path = (
                ImageUtils.resolve_picture_path(server.vault.image_root, file_path)
                if file_path
                else None
            )
            if not resolved_path:
                cards.append(build_card(None))
                continue
            try:
                img = Image.open(resolved_path).convert("RGB")
            except Exception:
                img = None
            cards.append(build_card(img))

        while len(cards) < 3:
            cards.append(build_card(None))

        # Map highest score to right/front, then middle, then left/back
        right_card = cards[0]
        middle_card = cards[1]
        left_card = cards[2]
        cards = [left_card, middle_card, right_card]

        # Layering: left (bottom), middle, right (top)
        layer_order = [0, 1, 2]

        for layer_index, card_index in enumerate(layer_order):
            card = cards[card_index]
            angle = angles[card_index]
            offset = offsets[card_index]
            layer = Image.new("RGBA", (work_size, work_size), (0, 0, 0, 0))
            paste_x = pivot_x + offset[0]
            paste_y = pivot_y - card_size[1] + offset[1]
            layer.paste(card, (paste_x, paste_y), card)
            rotated_layer = layer.rotate(
                angle,
                resample=Image.BICUBIC,
                expand=False,
                center=(pivot_x, pivot_y),
                fillcolor=(0, 0, 0, 0),
            )
            base.alpha_composite(rotated_layer)

        alpha = base.split()[-1]
        bbox = alpha.getbbox()
        if bbox:
            pad = 0
            left = max(0, bbox[0] - pad)
            top = max(0, bbox[1] - pad)
            right = min(work_size, bbox[2] + pad)
            bottom = min(work_size, bbox[3] + pad)
            base = base.crop((left, top, right, bottom))

        # Add a subtle drop shadow behind the whole fan
        shadow_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        shadow_alpha = base.split()[-1]
        shadow_layer.putalpha(shadow_alpha)
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=4))
        shadow_tint = Image.new("RGBA", base.size, (0, 0, 0, 90))
        shadow = Image.composite(
            shadow_tint,
            Image.new("RGBA", base.size, (0, 0, 0, 0)),
            shadow_layer.split()[-1],
        )
        fan_with_shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
        fan_with_shadow.alpha_composite(shadow, (2, 3))
        fan_with_shadow.alpha_composite(base, (0, 0))
        base = fan_with_shadow

        shadow_alpha = base.split()[-1]
        shadow_bbox = shadow_alpha.getbbox()
        if shadow_bbox:
            shadow_pad = 0
            left = max(0, shadow_bbox[0] - shadow_pad)
            top = max(0, shadow_bbox[1] - shadow_pad)
            right = min(base.width, shadow_bbox[2] + shadow_pad)
            bottom = min(base.height, shadow_bbox[3] + shadow_pad)
            base = base.crop((left, top, right, bottom))

        final_img = ImageOps.fit(
            base,
            (target_size, target_size),
            Image.LANCZOS,
            centering=(0.5, 0.5),
        )

        try:
            final_img.save(cache_path, format="PNG")
            try:
                with open(meta_path, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "version": thumbnail_cache_version,
                            "picture_ids": top_ids,
                            "hidden_key": hidden_key,
                        },
                        handle,
                    )
            except Exception as exc:
                logger.debug("Failed to write picture set thumbnail metadata: %s", exc)
            return FileResponse(cache_path, media_type="image/png")
        except Exception:
            from io import BytesIO

            buf = BytesIO()
            final_img.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")

    @router.get(
        "/picture_sets/{id}",
        summary="Get picture set",
        description=(
            "Returns set metadata or member pictures with optional sort, format, and "
            "character-likeness/smart-score modes. By default only the stack leader "
            "picture is returned for each stack. Pass expand_stacks=true to receive "
            "every picture in every stack."
        ),
        response_model=PictureSetPicturesResponse,
    )
    def get_picture_set(
        request: Request,
        id: int,
        info: bool = Query(False),
        sort: str = Query(None),
        descending: bool = Query(True),
        format: list[str] = Query(None),
        character_id: str | None = Query(None),
        reference_character_id: str | None = Query(None),
        project_id: str | None = Query(None),
        fields: str = Query(None),
        min_score: int | None = Query(None),
        max_score: int | None = Query(None),
        smart_score_bucket: str | None = Query(None),
        resolution_bucket: str | None = Query(None),
        expand_stacks: bool = Query(False),
    ):
        # Intrinsic tag/confidence query params are parsed by the shared parser; the
        # remaining filters (format/scores/buckets) arrive as typed Query args above.
        _predicate_filter = PredicateFilter.from_query_params(request)
        tags_filter = _predicate_filter.tags_filter
        tags_rejected_filter = _predicate_filter.tags_rejected_filter
        tags_confidence_above_filter = _predicate_filter.tags_confidence_above_filter
        tags_confidence_below_filter = _predicate_filter.tags_confidence_below_filter
        try:
            id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Picture set names are not globally unique, so '{id}' "
                    "cannot be used to identify a set unambiguously. "
                    "Use the numeric set ID instead, or look up the set via "
                    "/api/v1/projects/{project_id_or_name}/picture_sets/{name}."
                ),
            )

        sort_mech = None
        if sort:
            try:
                sort_mech = SortMechanism.from_string(sort, descending=descending)
            except ValueError as ve:
                logger.error("Invalid sort mechanism: %s - %s", sort, ve)
                raise HTTPException(status_code=400, detail=str(ve))

        project_filter = None
        if project_id is not None:
            if project_id == "UNASSIGNED":
                project_filter = _project_membership_unassigned()
            else:
                try:
                    project_filter = _project_membership_exists(int(project_id))
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="Invalid project_id")

        def fetch_set(session, id):
            picture_set = session.get(PictureSet, id)
            if not picture_set:
                return None, None
            members_query = (
                select(PictureSetMember.picture_id)
                .join(Picture, Picture.id == PictureSetMember.picture_id)
                .where(
                    PictureSetMember.set_id == id,
                    Picture.deleted.is_(False),
                )
            )
            if project_filter is not None:
                members_query = members_query.where(project_filter)
            members = session.exec(members_query).all()
            seen = set()
            picture_ids = []
            for pic_id in members:
                if pic_id is None:
                    continue
                if pic_id in seen:
                    continue
                seen.add(pic_id)
                picture_ids.append(pic_id)
            return picture_set, picture_ids

        picture_set, picture_ids = server.vault.db.run_immediate_read_task(
            fetch_set, id
        )
        if not picture_set:
            raise HTTPException(status_code=404, detail="Picture set not found")
        hidden_tags = _get_hidden_tags_from_request(request)

        def filter_hidden_ids(session, ids):
            return _filter_hidden_picture_ids(session, ids, hidden_tags)

        picture_ids = server.vault.db.run_immediate_read_task(
            filter_hidden_ids, picture_ids
        )

        # Determine whether to collapse stacks to their leaders.
        # expand_stacks=true opt-in from the caller overrides the default.
        # The legacy fields=grid query param also triggers deduplication.
        deduplicate_stacks = not expand_stacks and fields != "full"

        # If any picture in the set belongs to a stack, treat the entire stack
        # as part of the set — mirroring how stacks work in the regular view.
        def expand_with_stack_members(session, ids):
            if not ids:
                return ids
            base_query = select(Picture.id, Picture.stack_id).where(
                Picture.id.in_(ids),
                Picture.deleted.is_(False),
            )
            if project_filter is not None:
                base_query = base_query.where(project_filter)
            rows = session.exec(base_query).all()
            stack_ids = [int(stack_id) for _, stack_id in rows if stack_id is not None]
            if not stack_ids:
                return ids
            extra_query = select(Picture.id).where(
                Picture.stack_id.in_(stack_ids),
                Picture.deleted.is_(False),
            )
            if project_filter is not None:
                extra_query = extra_query.where(project_filter)
            extra = session.exec(extra_query).all()
            return list(set(ids) | set(extra))

        picture_ids = server.vault.db.run_immediate_read_task(
            expand_with_stack_members, picture_ids
        )

        if info:
            set_dict = picture_set.dict()
            set_dict["picture_count"] = len(picture_ids)
            return set_dict

        if sort_mech and sort_mech.key == SortMechanism.Keys.SMART_SCORE:
            penalised_tags = get_smart_score_penalised_tags_from_request(
                server, request
            )
            pictures = find_pictures_by_smart_score(
                server,
                format,
                0,
                sys.maxsize,
                descending,
                candidate_ids=picture_ids,
                penalised_tags=penalised_tags,
            )
            if deduplicate_stacks:
                pictures = deduplicate_by_stack(pictures)
            pictures = _enrich_with_stack_counts(pictures)
            return {"pictures": pictures, "set": safe_model_dict(picture_set)}

        if sort_mech and sort_mech.key == SortMechanism.Keys.CHARACTER_LIKENESS:
            if not reference_character_id:
                raise HTTPException(
                    status_code=400,
                    detail="reference_character_id is required for CHARACTER_LIKENESS sort",
                )
            pictures = find_pictures_by_character_likeness(
                server,
                character_id,
                reference_character_id,
                0,
                sys.maxsize,
                descending,
                candidate_ids=picture_ids,
            )
            if deduplicate_stacks:
                pictures = deduplicate_by_stack(pictures)
            pictures = _enrich_with_stack_counts(pictures)
            return {"pictures": pictures, "set": safe_model_dict(picture_set)}

        def fetch_pics(session, picture_ids):
            pics = Picture.find(
                session,
                id=picture_ids,
                sort_mech=sort_mech,
                select_fields=Picture.metadata_fields(),
                format=format,
                include_unimported=True,
                stack_leaders_only=deduplicate_stacks,
                min_score=min_score,
                max_score=max_score,
                smart_score_bucket=smart_score_bucket,
                resolution_bucket=resolution_bucket,
                tags_filter=tags_filter,
                tags_rejected_filter=tags_rejected_filter,
                tags_confidence_above_filter=tags_confidence_above_filter,
                tags_confidence_below_filter=tags_confidence_below_filter,
            )
            return [
                pic.dict(
                    exclude={
                        "file_path",
                        "thumbnail",
                        "text_embedding",
                        "image_embedding",
                    }
                )
                for pic in pics
            ]

        pictures = server.vault.db.run_immediate_read_task(fetch_pics, picture_ids)
        pictures = _enrich_with_stack_counts(pictures)
        return {"pictures": pictures, "set": safe_model_dict(picture_set)}

    @router.patch(
        "/picture_sets/{id}",
        summary="Update picture set",
        description="Updates picture set name and/or description.",
        response_model=PictureSetUpdateResponse,
    )
    def update_picture_set(id: int, request: Request, payload: dict = Body(...)):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        name = payload.get("name")
        description = payload.get("description")
        raw_project_id = payload.get("project_id", _UNSET)
        set_icon = payload.get("set_icon", _UNSET)
        set_color = payload.get("set_color", _UNSET)
        locked_param = payload.get("locked", _UNSET)
        if locked_param is not _UNSET:
            locked_param = bool(locked_param)
        project_id = raw_project_id
        if raw_project_id is not _UNSET:
            if raw_project_id is None:
                project_id = None
            else:
                try:
                    project_id = int(raw_project_id)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(
                        status_code=400, detail="Invalid project_id"
                    ) from exc

        def update_set(
            session, id, name, description, project_id, set_icon, set_color, locked
        ):
            picture_set = session.get(PictureSet, id)
            if not picture_set:
                return False

            # Capture the project the set is leaving before we mutate it, so we
            # can disassociate its member pictures from that old project.
            old_project_id = picture_set.project_id

            # Lock rule: while a set is locked the only accepted PATCH is one that
            # changes nothing but `locked` (i.e. an unlock). Compare each field to
            # its CURRENT value — not mere key presence — so the frontend echoing
            # unchanged fields back is a no-op, not a rejection.
            description_changing = (
                description is not None and description != picture_set.description
            )
            icon_changing = set_icon is not _UNSET and set_icon != picture_set.set_icon
            color_changing = (
                set_color is not _UNSET and set_color != picture_set.set_color
            )
            name_effective_change = name is not None and name != picture_set.name
            project_effective_change = (
                project_id is not _UNSET and project_id != picture_set.project_id
            )
            other_effective_change = (
                name_effective_change
                or description_changing
                or project_effective_change
                or icon_changing
                or color_changing
            )
            if other_effective_change:
                # Raises 423 iff the set is currently locked; passes through for an
                # unlocked set. A pure unlock (only `locked` differs) is allowed.
                enforce_set_not_locked(session, picture_set, "edit a locked set")

            locked_changed = locked is not _UNSET and bool(locked) != picture_set.locked

            # Resolve the final (name, project_id) that would result from this
            # update and check uniqueness before touching anything.
            final_name = name if name is not None else picture_set.name
            final_project_id = (
                project_id if project_id is not _UNSET else picture_set.project_id
            )
            name_changing = name is not None and name != picture_set.name
            project_changing = (
                project_id is not _UNSET and project_id != picture_set.project_id
            )
            if name_changing or project_changing:
                _ensure_unique_set_name(
                    session, final_name, final_project_id, exclude_set_id=id
                )

            project_id_payload_provided = project_id is not _UNSET
            project_assignment_requested = (
                project_id_payload_provided and project_id is not None
            )
            project_id_changed = (
                project_id_payload_provided and project_id != picture_set.project_id
            )
            pictures_changed = False
            if name is not None:
                picture_set.name = name
            if description is not None:
                picture_set.description = description
            if project_id_payload_provided and project_id is not None:
                project = session.get(Project, project_id)
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")

            if project_id_changed:
                picture_set.project_id = project_id
            if set_icon is not _UNSET:
                picture_set.set_icon = set_icon
            if set_color is not _UNSET:
                picture_set.set_color = set_color

            # Reconcile member-picture project membership when this update sets
            # or changes the project. A same-project re-assign (project provided
            # but unchanged) is the idempotent-repair path that heals historical
            # drift where members are missing membership rows; a project change
            # also removes members from the old project (reference-aware). Shared
            # with character updates via project_membership_service.
            if project_assignment_requested or project_id_changed:
                member_ids = [
                    pic_id
                    for pic_id in session.exec(
                        select(PictureSetMember.picture_id).where(
                            PictureSetMember.set_id == id
                        )
                    ).all()
                    if pic_id is not None
                ]
                reconcile_result = reconcile_entity_project_change(
                    session,
                    picture_ids=member_ids,
                    old_project_id=old_project_id,
                    new_project_id=project_id,
                    exclude_set_id=id,
                )
                pictures_changed = reconcile_result.changed

            # Apply the lock toggle last (after the read-only guard above) and
            # collect the member ids so a lock/unlock can refresh their badges.
            locked_member_ids: list[int] = []
            if locked_changed:
                picture_set.locked = bool(locked)
                locked_member_ids = [
                    int(m)
                    for m in session.exec(
                        select(PictureSetMember.picture_id).where(
                            PictureSetMember.set_id == id
                        )
                    ).all()
                    if m is not None
                ]

            session.commit()
            return (
                True,
                project_id_changed or pictures_changed,
                locked_changed,
                locked_member_ids,
            )

        success, project_changed, locked_changed, locked_member_ids = (
            server.vault.db.run_task(
                update_set,
                id,
                name,
                description,
                project_id,
                set_icon,
                set_color,
                locked_param,
                priority=DBPriority.IMMEDIATE,
            )
        )
        if not success:
            raise HTTPException(status_code=404, detail="Picture set not found")
        if project_changed:
            server.vault.notify(
                EventType.CHANGED_PICTURES,
                {
                    "source": "ui",
                    "origin_client_id": origin_client_id,
                    "change_kind": "updated",
                },
            )
        if locked_changed:
            # Refresh every member's card so lock badges appear/clear across tabs.
            server.vault.notify(
                EventType.CHANGED_PICTURES,
                {
                    "picture_ids": locked_member_ids,
                    "source": "ui",
                    "origin_client_id": origin_client_id,
                    "change_kind": "updated",
                },
            )
        return {"status": "success"}

    @router.delete(
        "/picture_sets/{id}",
        summary="Delete picture set",
        description="Deletes a picture set and all its membership links.",
        response_model=PictureSetDeleteResponse,
    )
    def delete_picture_set(id: int):
        def delete_set(session, id):
            picture_set = session.get(PictureSet, id)
            if not picture_set:
                return False
            # A locked set must be unlocked before it can be deleted (deliberate
            # friction so a misclick can't destroy a frozen eval set).
            enforce_set_not_locked(session, picture_set, "delete a locked set")
            session.delete(picture_set)
            session.commit()
            return True

        success = server.vault.db.run_task(
            delete_set, id, priority=DBPriority.IMMEDIATE
        )
        if not success:
            raise HTTPException(status_code=404, detail="Picture set not found")
        return {"status": "success", "deleted_id": id}

    @router.get(
        "/picture_sets/{id}/members",
        summary="List picture set members",
        description=(
            "Returns unique picture ids that belong to a set, with optional deleted inclusion. "
            "By default only explicitly stored members are returned. "
            "Pass expand_stacks=true to also include all stack siblings of any member, "
            "which is useful for checking whether a stack is part of the set."
        ),
        response_model=PictureSetMembersResponse,
    )
    def get_picture_set_pictures(
        id: int,
        request: Request,
        include_deleted: bool = Query(False),
        expand_stacks: bool = Query(False),
    ):
        def fetch_members(session, id, include_deleted, expand_stacks):
            picture_set = session.get(PictureSet, id)
            if not picture_set:
                return None
            filters = [
                PictureSetMember.set_id == id,
            ]
            if not include_deleted:
                filters.append(Picture.deleted.is_(False))
            members = session.exec(
                select(PictureSetMember.picture_id)
                .join(Picture, Picture.id == PictureSetMember.picture_id)
                .where(*filters)
            ).all()
            picture_ids = list({m for m in members if m is not None})

            if not expand_stacks or not picture_ids:
                return picture_ids

            # Expand stacks: if any member of a stack is in the set, include
            # all other non-deleted members of that stack so the stack leader
            # is recognised as a set member in the frontend.
            id_stack_rows = session.exec(
                select(Picture.id, Picture.stack_id).where(
                    Picture.id.in_(picture_ids),
                    Picture.deleted.is_(False),
                )
            ).all()
            stack_ids = [
                int(stack_id)
                for _pic_id, stack_id in id_stack_rows
                if stack_id is not None
            ]
            if stack_ids:
                extra_query = select(Picture.id).where(
                    Picture.stack_id.in_(stack_ids),
                )
                if not include_deleted:
                    extra_query = extra_query.where(Picture.deleted.is_(False))
                extra = session.exec(extra_query).all()
                picture_ids = list(
                    set(picture_ids) | {e for e in extra if e is not None}
                )

            return picture_ids

        picture_ids = server.vault.db.run_immediate_read_task(
            fetch_members, id, include_deleted, expand_stacks
        )
        if picture_ids is None:
            raise HTTPException(status_code=404, detail="Picture set not found")
        return {"picture_ids": picture_ids}

    @router.post(
        "/picture_sets/{id}/members/{picture_id}",
        summary="Add picture to set",
        description="Adds one picture to a set when the set and picture are valid and membership does not already exist.",
        response_model=PictureSetAddPictureResponse,
    )
    def add_picture_to_set(id: int, picture_id: str, request: Request):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        reference_character_id = _find_reference_character_id_for_set(id)

        def add_member(session, id, picture_id, reference_character_id=None):
            picture_set = session.get(PictureSet, id)
            if not picture_set:
                return False
            # Membership of a locked set is frozen — no additions until unlocked.
            enforce_set_not_locked(session, picture_set, "add pictures to a locked set")
            # Sets are atomic for stacks: adding any stacked picture adds every
            # member of its stack.
            target_ids = expand_picture_ids_to_stacks(session, [int(picture_id)])
            pictures = session.exec(
                select(Picture).where(
                    Picture.id.in_(target_ids),
                    Picture.deleted.is_(False),
                )
            ).all()
            if not pictures:
                return False
            added_any = False
            for picture in pictures:
                exists = session.exec(
                    select(PictureSetMember).where(
                        PictureSetMember.set_id == id,
                        PictureSetMember.picture_id == picture.id,
                    )
                ).first()
                if exists is None:
                    session.add(PictureSetMember(set_id=id, picture_id=picture.id))
                    added_any = True
                if picture_set.project_id is not None:
                    membership = session.exec(
                        select(PictureProjectMember).where(
                            PictureProjectMember.picture_id == picture.id,
                            PictureProjectMember.project_id == picture_set.project_id,
                        )
                    ).first()
                    if membership is None:
                        session.add(
                            PictureProjectMember(
                                picture_id=picture.id,
                                project_id=picture_set.project_id,
                            )
                        )
                    if picture.project_id != picture_set.project_id:
                        picture.project_id = picture_set.project_id
                    session.add(picture)
            session.add(picture_set)
            session.commit()
            return added_any

        # Set membership is stack-atomic, so the snapshot expands to the whole
        # stack — otherwise undo would leave the stack siblings in the set.
        success, _operation = operation_log_service.run_recorded_metadata_task(
            server.vault,
            add_member,
            id,
            picture_id,
            reference_character_id=reference_character_id,
            op_type="picture_sets.members.add",
            picture_ids=[picture_id],
            expand_stacks=True,
            summary="Added a picture to a set",
            **operation_log_service.request_context(request),
        )
        if success:
            try:
                changed_ids = [int(picture_id)]
            except (TypeError, ValueError):
                changed_ids = []
            server.vault.notify(
                EventType.CHANGED_PICTURES,
                {
                    "picture_ids": changed_ids,
                    "source": "ui",
                    "origin_client_id": origin_client_id,
                    "change_kind": "updated",
                },
            )
            if reference_character_id is not None:
                server.vault.notify(
                    EventType.CHANGED_CHARACTERS,
                    {
                        "source": "ui",
                        "origin_client_id": origin_client_id,
                    },
                )
        else:
            raise HTTPException(
                status_code=400,
                detail="Failed to add picture to set (set may not exist or picture already in set)",
            )
        return {"status": "success"}

    @router.delete(
        "/picture_sets/{id}/members/{picture_id}",
        summary="Remove picture from set",
        description="Removes one picture membership from a picture set.",
        response_model=PictureSetRemovePictureResponse,
    )
    def remove_picture_from_set(id: int, picture_id: str, request: Request):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        reference_character_id = _find_reference_character_id_for_set(id)

        def remove_member(session, id, picture_id, reference_character_id=None):
            # Membership of a locked set is frozen — no removals until unlocked.
            picture_set = session.get(PictureSet, id)
            if picture_set is not None:
                enforce_set_not_locked(
                    session, picture_set, "remove pictures from a locked set"
                )
            # Sets are atomic for stacks: removing any stacked picture removes
            # every member of its stack from the set. This also covers the case
            # where the requested id is a collapsed-stack leader shown because a
            # different member of its stack is the one actually in the set.
            target_ids = expand_picture_ids_to_stacks(session, [int(picture_id)])
            members = session.exec(
                select(PictureSetMember).where(
                    PictureSetMember.set_id == id,
                    PictureSetMember.picture_id.in_(target_ids),
                )
            ).all()
            if not members:
                return False
            for m in members:
                session.delete(m)
            session.commit()
            return True

        # Stack-atomic like the add above: the removal takes the whole stack out,
        # so the snapshot has to cover the whole stack too.
        success, _operation = operation_log_service.run_recorded_metadata_task(
            server.vault,
            remove_member,
            id,
            picture_id,
            reference_character_id=reference_character_id,
            op_type="picture_sets.members.remove",
            picture_ids=[picture_id],
            expand_stacks=True,
            summary="Removed a picture from a set",
            **operation_log_service.request_context(request),
        )
        if success:
            if reference_character_id is not None:
                server.vault.notify(
                    EventType.CHANGED_CHARACTERS,
                    {
                        "source": "ui",
                        "origin_client_id": origin_client_id,
                    },
                )
        else:
            raise HTTPException(status_code=404, detail="Picture not in set")
        return {"status": "success"}

    @router.post(
        "/picture_sets/{id}/members",
        summary="Bulk add pictures to set",
        description="Adds a batch of pictures to a set (non-destructive). Skips pictures already in the set.",
        response_model=PictureSetBulkAddResponse,
    )
    def bulk_add_pictures_to_set(id: int, request: Request, payload: dict = Body(...)):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        raw_ids = payload.get("picture_ids", [])
        if not isinstance(raw_ids, list):
            raise HTTPException(status_code=400, detail="picture_ids must be a list")
        try:
            picture_ids = [int(i) for i in raw_ids]
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail="All picture ids must be integers"
            )
        if not picture_ids:
            return {"status": "success", "added": 0}

        def bulk_add(session, set_id, picture_ids):
            picture_set = session.get(PictureSet, set_id)
            if not picture_set:
                return None
            # Membership of a locked set is frozen — no additions until unlocked.
            enforce_set_not_locked(session, picture_set, "add pictures to a locked set")
            # Sets are atomic for stacks: pull in every member of any stack.
            picture_ids = expand_picture_ids_to_stacks(session, picture_ids)
            existing = set(
                session.exec(
                    select(PictureSetMember.picture_id).where(
                        PictureSetMember.set_id == set_id
                    )
                ).all()
            )
            added = 0
            for pic_id in picture_ids:
                if pic_id in existing:
                    continue
                pic = session.get(Picture, pic_id)
                if not pic or pic.deleted:
                    continue
                session.add(PictureSetMember(set_id=set_id, picture_id=pic_id))
                if picture_set.project_id is not None:
                    membership = session.exec(
                        select(PictureProjectMember).where(
                            PictureProjectMember.picture_id == pic_id,
                            PictureProjectMember.project_id == picture_set.project_id,
                        )
                    ).first()
                    if membership is None:
                        session.add(
                            PictureProjectMember(
                                picture_id=pic_id,
                                project_id=picture_set.project_id,
                            )
                        )
                    pic.project_id = picture_set.project_id
                    session.add(pic)
                existing.add(pic_id)
                added += 1
            session.commit()
            return added

        added, _operation = operation_log_service.run_recorded_metadata_task(
            server.vault,
            bulk_add,
            id,
            picture_ids,
            op_type="picture_sets.members.add",
            picture_ids=picture_ids,
            expand_stacks=True,
            summary=f"Added {len(picture_ids)} picture(s) to a set",
            **operation_log_service.request_context(request),
        )
        if added is None:
            raise HTTPException(status_code=404, detail="Picture set not found")
        if added > 0:
            server.vault.notify(
                EventType.CHANGED_PICTURES,
                {
                    "picture_ids": picture_ids,
                    "source": "ui",
                    "origin_client_id": origin_client_id,
                    "change_kind": "updated",
                },
            )
        return {"status": "success", "added": added}

    @router.put(
        "/picture_sets/{id}/members",
        summary="Bulk replace picture set members",
        description="Atomically replaces the entire member list of a set. All existing members are removed and the provided picture ids become the new members.",
        response_model=PictureSetBulkReplaceResponse,
    )
    def bulk_replace_pictures_in_set(
        id: int, request: Request, payload: dict = Body(...)
    ):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        raw_ids = payload.get("picture_ids", [])
        if not isinstance(raw_ids, list):
            raise HTTPException(status_code=400, detail="picture_ids must be a list")
        try:
            picture_ids = [int(i) for i in raw_ids]
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail="All picture ids must be integers"
            )

        def bulk_replace(session, set_id, picture_ids):
            picture_set = session.get(PictureSet, set_id)
            if not picture_set:
                return None
            # Membership of a locked set is frozen — no replacement until unlocked.
            enforce_set_not_locked(
                session, picture_set, "replace the members of a locked set"
            )
            # Sets are atomic for stacks: keep every member of any stack together.
            picture_ids = expand_picture_ids_to_stacks(session, picture_ids)
            # Remove all existing members
            existing_members = session.exec(
                select(PictureSetMember).where(PictureSetMember.set_id == set_id)
            ).all()
            for m in existing_members:
                session.delete(m)
            # Add new members
            added = 0
            seen = set()
            for pic_id in picture_ids:
                if pic_id in seen:
                    continue
                seen.add(pic_id)
                pic = session.get(Picture, pic_id)
                if not pic or pic.deleted:
                    continue
                session.add(PictureSetMember(set_id=set_id, picture_id=pic_id))
                if picture_set.project_id is not None:
                    membership = session.exec(
                        select(PictureProjectMember).where(
                            PictureProjectMember.picture_id == pic_id,
                            PictureProjectMember.project_id == picture_set.project_id,
                        )
                    ).first()
                    if membership is None:
                        session.add(
                            PictureProjectMember(
                                picture_id=pic_id,
                                project_id=picture_set.project_id,
                            )
                        )
                    pic.project_id = picture_set.project_id
                    session.add(pic)
                added += 1
            session.commit()
            return added

        def _current_members(session):
            # A replace-all also EVICTS members the request never named. They must
            # be in the snapshot or undo would re-add the new members and never
            # restore the evicted ones — a half-reversible operation.
            return session.exec(
                select(PictureSetMember.picture_id).where(PictureSetMember.set_id == id)
            ).all()

        added, _operation = operation_log_service.run_recorded_metadata_task(
            server.vault,
            bulk_replace,
            id,
            picture_ids,
            op_type="picture_sets.members.replace",
            picture_ids=picture_ids,
            resolve_picture_ids=_current_members,
            expand_stacks=True,
            summary=f"Replaced a set's members with {len(picture_ids)} picture(s)",
            **operation_log_service.request_context(request),
        )
        if added is None:
            raise HTTPException(status_code=404, detail="Picture set not found")
        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": picture_ids,
                "source": "ui",
                "origin_client_id": origin_client_id,
                "change_kind": "updated",
            },
        )
        return {"status": "success", "members": added}

    return router
