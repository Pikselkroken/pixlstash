import io
import json
import mimetypes
import os
import re
import shutil
import uuid
import zipfile
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field as PydanticField
from sqlalchemy import delete, exists, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from pixlstash.authz.membership import (
    enforce_project_path_scope,
    enforce_project_scope,
)
from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Character,
    CharacterProjectMember,
    Face,
    Picture,
    PictureProjectMember,
    PictureSet,
    PictureSetMember,
    PictureSetProjectMember,
    Tag,
    character_in_project,
    picture_set_in_project,
)
from pixlstash.db_models.project import Project, ProjectAttachment
from pixlstash.pixl_logging import get_logger
from pixlstash.services.layout_move_service import rename_entity_folders
from pixlstash.utils.library_layout import Facet
from pixlstash.services.project_membership_service import (
    character_project_ids,
    picture_set_project_ids,
)
from pixlstash.utils.service.caption_utils import normalize_hidden_tags
from pixlstash.utils.path_utils import resolve_path_within
from pixlstash.utils.service.filter_helpers import (
    fetch_scope_allowed_picture_ids,
    narrow_project_assignments,
    narrow_project_fields,
    visible_project_ids,
)

logger = get_logger(__name__)

# Default maximum attachment size - overridden by server_config["max_attachment_size_mb"]
_DEFAULT_MAX_ATTACHMENT_MB = 50


class ProjectCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    cover_image_path: Optional[str] = None
    extra_metadata: Optional[str] = None


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cover_image_path: Optional[str] = None
    extra_metadata: Optional[str] = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    cover_image_path: Optional[str] = None
    extra_metadata: Optional[str] = None
    created_at: Optional[datetime] = None
    image_count: Optional[int] = PydanticField(
        default=None,
        description=(
            "Number of non-deleted pictures assigned to this project. Populated "
            "by ``GET /projects?include_counts=true`` only; ``null`` otherwise "
            "and on every single-project response. Same number as "
            "``GET /projects/{project_id}/summary`` returns, so a sidebar can "
            "render its counts from this one list response instead of one "
            "request per project (issue #651)."
        ),
    )


class ProjectDeleteResponse(BaseModel):
    status: str
    id: int


class ProjectSummaryResponse(BaseModel):
    image_count: int


class ProjectAttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    mime_type: Optional[str] = None
    file_size: int
    url: Optional[str] = None
    created_at: Optional[datetime] = None


class ProjectUrlAttachmentRequest(BaseModel):
    url: str
    title: Optional[str] = None


class ProjectMembershipResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    project_assignments: dict[int, list[int]] = {}
    unassigned_picture_ids: list[int] = []


class ProjectPictureSetResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    name: str
    description: Optional[str] = None
    project_id: Optional[int] = None


def create_router(server) -> APIRouter:
    """Create the projects API router.

    Args:
        server: The Server instance providing vault/db/config access.

    Returns:
        Configured APIRouter with all project endpoints mounted.
    """
    router = APIRouter()

    def _attachments_dir(project_id: int) -> str:
        """Return (and create) the on-disk directory for a project's attachments."""
        path = resolve_path_within(
            server.vault.image_root, "projects", str(project_id), "attachments"
        )
        os.makedirs(path, exist_ok=True)
        return path

    def _max_attachment_bytes() -> int:
        mb = server._server_config.get(
            "max_attachment_size_mb", _DEFAULT_MAX_ATTACHMENT_MB
        )
        return int(mb * 1024 * 1024)

    def _normalise_project_name(name: Optional[str]) -> str:
        value = "" if name is None else str(name)
        return re.sub(r"\s+", " ", value).strip()

    def _validate_project_name(name: Optional[str]) -> str:
        normalized = _normalise_project_name(name)
        if not normalized:
            raise HTTPException(status_code=422, detail="Project name is required")
        if normalized.upper() == "UNASSIGNED":
            raise HTTPException(
                status_code=422,
                detail="Project name 'UNASSIGNED' is reserved",
            )
        if normalized.isdigit():
            raise HTTPException(
                status_code=422,
                detail="Project name cannot be numeric-only",
            )
        return normalized

    def _ensure_unique_project_name(
        session: Session,
        name: str,
        *,
        exclude_id: Optional[int] = None,
    ) -> None:
        query = select(Project).where(func.lower(Project.name) == name.lower())
        if exclude_id is not None:
            query = query.where(Project.id != exclude_id)
        if session.exec(query).first() is not None:
            raise HTTPException(status_code=409, detail="Project name already exists")

    def _require_scope_allows_project(request: Request, project_id: int):
        """Raise 403 if the token scope does not cover the requested project.

        Thin delegation to the single membership implementation in
        ``pixlstash/authz/membership.py`` (backend refactor plan §3.7, Step 4).
        The authz gate calls the same function directly for PROJECT_SCOPED
        routes; Step 5 removes this shim.
        """
        enforce_project_scope(server, request, project_id)

    # -------------------------------------------------------------------------
    # Projects CRUD
    # -------------------------------------------------------------------------

    @router.get(
        "/projects",
        summary="List all projects",
        description=(
            "Lists every project the caller may see, oldest first.\n\n"
            "Pass ``include_counts=true`` to get each project's picture count "
            "inline as ``image_count``, so a sidebar does not need one "
            "``GET /projects/{project_id}/summary`` request per project."
        ),
        response_model=list[ProjectResponse],
    )
    def list_projects(
        request: Request,
        include_counts: bool = Query(
            default=False,
            description=(
                "When true, every row carries ``image_count``: the number of "
                "non-deleted pictures in that project. Costs one extra query "
                "for the whole listing, whatever the number of projects. "
                "Defaults to false, so existing callers pay nothing."
            ),
        ),
    ):
        server.auth.require_user_id(request)
        token_scope = getattr(request.state, "token_scope", None)
        if (
            token_scope is not None
            and token_scope.resource_type is not None
            and token_scope.resource_type != "project"
        ):
            raise HTTPException(
                status_code=403, detail="Token is not authorised for this resource type"
            )
        scope_project_id = (
            token_scope.resource_id
            if token_scope is not None and token_scope.resource_type == "project"
            else None
        )

        def fetch(session: Session):
            query = select(Project).order_by(Project.created_at)
            if scope_project_id is not None:
                query = query.where(Project.id == scope_project_id)
            projects = session.exec(query).all()

            # One grouped count for the WHOLE listing, not one summary request
            # per project (issue #651). Only for the projects the scope filter
            # above already returned, so a scoped token learns nothing beyond
            # the per-id summary it is already granted. Semantics match
            # GET /projects/{project_id}/summary exactly: non-deleted pictures
            # with a membership row. (picture_id, project_id) is the join
            # table's composite primary key, so plain count() already counts
            # each picture once.
            counts_by_project: dict[int, int] = {}
            listed_ids = [int(p.id) for p in projects if p.id is not None]
            if include_counts and listed_ids:
                for project_id_value, picture_count in session.exec(
                    select(PictureProjectMember.project_id, func.count())
                    .select_from(PictureProjectMember)
                    .join(Picture, Picture.id == PictureProjectMember.picture_id)
                    .where(
                        PictureProjectMember.project_id.in_(listed_ids),
                        Picture.deleted.is_(False),
                    )
                    .group_by(PictureProjectMember.project_id)
                ).all():
                    counts_by_project[int(project_id_value)] = int(picture_count)

            return [
                {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "cover_image_path": p.cover_image_path,
                    "extra_metadata": p.extra_metadata,
                    "created_at": p.created_at,
                    "image_count": (
                        counts_by_project.get(int(p.id), 0) if include_counts else None
                    ),
                }
                for p in projects
            ]

        # A read belongs on the read path, not on the single writer queue: a
        # DBPriority.IMMEDIATE run_task queues this listing behind whatever the
        # writer is doing, which is the sidebar stall issue #651 is about.
        return server.vault.db.run_immediate_read_task(fetch)

    @router.post(
        "/projects/membership",
        summary="Batch project membership lookup",
        description=(
            "Given a list of picture IDs, returns project_assignments "
            "(project_id → [picture_ids]) and unassigned_picture_ids "
            "([picture_ids with no project membership]). "
            "Used by the AddToProject menu to load membership in a single request."
        ),
        response_model=ProjectMembershipResponse,
    )
    def get_batch_project_membership(
        request: Request,
        picture_ids: list[int] = Body(default=[], embed=True),
    ):
        if not picture_ids:
            return {"project_assignments": {}, "unassigned_picture_ids": []}

        # Scope guard (BOLA): restrict a READ-scoped share token to picture ids
        # within its granted resource.  None == owner / unscoped == no filter.
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        if scope_allowed is not None:
            picture_ids = [pid for pid in picture_ids if pid in scope_allowed]
            if not picture_ids:
                return {"project_assignments": {}, "unassigned_picture_ids": []}

        # Filtering the picture ids is only half the guard: every *key* of this
        # payload is a project id, which is membership metadata about projects
        # the token may not be allowed to learn about at all (issue #125 / R1b,
        # #708 F1). Narrow the keys on the same ladder every other serialisation
        # of a project id uses.
        visible_projects = visible_project_ids(server, request)

        def fetch(session, ids: list[int]):
            rows = session.exec(
                select(
                    PictureProjectMember.project_id, PictureProjectMember.picture_id
                ).where(
                    PictureProjectMember.picture_id.in_(ids),
                )
            ).all()
            assignments: dict[int, list[int]] = {}
            for project_id, pid in rows:
                if project_id is not None:
                    assignments.setdefault(int(project_id), []).append(int(pid))
            assignments = narrow_project_assignments(assignments, visible_projects)
            # "Unassigned" is derived from the *narrowed* mapping, so it means
            # "in no project you can see". Deriving it from the raw membership
            # would re-leak what the narrowing just removed: a picture missing
            # from both lists would tell the token some invisible project holds
            # it.
            assigned_ids = {pid for pids in assignments.values() for pid in pids}
            unassigned = sorted(set(ids) - assigned_ids)
            return {
                "project_assignments": assignments,
                "unassigned_picture_ids": unassigned,
            }

        return server.vault.db.run_immediate_read_task(fetch, picture_ids)

    @router.post(
        "/projects",
        summary="Create a project",
        response_model=ProjectResponse,
    )
    def create_project(
        request: Request,
        payload: ProjectCreateRequest = Body(...),
    ):
        server.auth.require_user_id(request)

        normalized_name = _validate_project_name(payload.name)

        def insert(session: Session):
            _ensure_unique_project_name(session, normalized_name)
            project = Project(
                name=normalized_name,
                description=payload.description,
                cover_image_path=payload.cover_image_path,
                extra_metadata=payload.extra_metadata,
                created_at=datetime.utcnow(),
            )
            session.add(project)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="Project name already exists",
                )
            session.refresh(project)
            return project

        return server.vault.db.run_task(insert, priority=DBPriority.IMMEDIATE)

    @router.get(
        "/projects/{id_or_name}/picture_sets",
        summary="List picture sets for a project",
        description="Returns all picture sets that belong to the given project. "
        "``id_or_name`` may be a numeric ID or a project name (case-insensitive).",
        response_model=list[ProjectPictureSetResponse],
    )
    def list_project_picture_sets(request: Request, id_or_name: str):
        server.auth.require_user_id(request)
        # A set listed under this project may *also* belong to others, and its
        # stored scalar ``project_id`` names its primary project - which is not
        # necessarily this one. Serialising it raw hands a project-scoped token
        # another project's id (issue #125 / R1b, #708 F4).
        visible_projects = visible_project_ids(server, request)

        def fetch(session: Session, pid_or_name: str):
            # Resolve by numeric ID first, then fall back to case-insensitive name.
            project = None
            try:
                numeric_id = int(pid_or_name)
                project = session.get(Project, numeric_id)
            except (TypeError, ValueError):
                # If parsing as an integer fails, treat pid_or_name as a project name instead.
                logger.debug(
                    "Could not parse project identifier %r as integer.", pid_or_name
                )
            if project is None:
                project = session.exec(
                    select(Project).where(
                        func.lower(Project.name) == pid_or_name.lower()
                    )
                ).first()
            # Resolve first, then refuse identically (#708 condition 2). The old
            # order raised 404 "Project not found" for a project that does not
            # exist and 403 for one the token may not see, which made this route
            # an existence oracle by numeric id and by name alike.
            enforce_project_path_scope(
                server, request, int(project.id) if project is not None else None
            )
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            sets = session.exec(
                select(PictureSet)
                .where(picture_set_in_project(project.id))
                .order_by(PictureSet.name)
            ).all()
            # One query for every listed set's membership, so the narrowing
            # costs no N+1 (issue #125).
            listed_ids = [int(s.id) for s in sets if s.id is not None]
            project_ids_by_set: dict[int, list[int]] = {}
            if listed_ids:
                for sid, pid in session.exec(
                    select(
                        PictureSetProjectMember.set_id,
                        PictureSetProjectMember.project_id,
                    ).where(PictureSetProjectMember.set_id.in_(listed_ids))
                ).all():
                    project_ids_by_set.setdefault(int(sid), []).append(int(pid))
            return [
                narrow_project_fields(
                    {
                        "id": s.id,
                        "name": s.name,
                        "description": s.description,
                    },
                    project_ids_by_set.get(int(s.id), []),
                    visible_projects,
                )
                for s in sets
            ]

        return server.vault.db.run_task(
            fetch, id_or_name, priority=DBPriority.IMMEDIATE
        )

    @router.get(
        "/projects/{id_or_name}",
        summary="Get a project by ID or name",
        description="Returns the project matching the given numeric ID or name (case-insensitive).",
        response_model=ProjectResponse,
    )
    def get_project(request: Request, id_or_name: str):
        server.auth.require_user_id(request)

        def fetch(session: Session, value: str):
            project = None
            try:
                project = session.get(Project, int(value))
            except (TypeError, ValueError):
                # Value is not a valid integer project ID; fall back to lookup by name.
                logger.debug("Could not parse project value %r as integer ID.", value)
            if project is None:
                project = session.exec(
                    select(Project).where(func.lower(Project.name) == value.lower())
                ).first()
            # Same reordering as list_project_picture_sets (#708 condition 2):
            # 403-for-existing / 404-for-missing was a project existence oracle.
            enforce_project_path_scope(
                server, request, int(project.id) if project is not None else None
            )
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return project

        return server.vault.db.run_task(
            fetch, id_or_name, priority=DBPriority.IMMEDIATE
        )

    @router.put(
        "/projects/{project_id}",
        summary="Update a project",
        response_model=ProjectResponse,
    )
    def update_project(
        request: Request,
        project_id: int,
        payload: ProjectUpdateRequest = Body(...),
    ):
        server.auth.require_user_id(request)

        normalized_name = (
            _validate_project_name(payload.name) if payload.name is not None else None
        )

        def update(session: Session, pid: int):
            project = session.get(Project, pid)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            previous_name = project.name
            if normalized_name is not None:
                _ensure_unique_project_name(
                    session,
                    normalized_name,
                    exclude_id=pid,
                )
                project.name = normalized_name
            if payload.description is not None:
                project.description = payload.description
            if payload.cover_image_path is not None:
                project.cover_image_path = payload.cover_image_path
            if payload.extra_metadata is not None:
                project.extra_metadata = payload.extra_metadata
            session.add(project)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="Project name already exists",
                )
            session.refresh(project)
            if normalized_name is not None and previous_name != normalized_name:
                # **Renaming a project renames its FOLDER; it moves no files**
                # (v1.11 §4). It is also not cosmetic: the layout reads folder
                # names against the library's *current* vocabulary, so a folder
                # left under the old name would name nothing PixlStash knows
                # and its pictures would drop out of the layout for good.
                # Commits for itself, and rolls the directories back if it
                # cannot: the renames and the ``file_path`` rewrites describing
                # them have to land together.
                rename_entity_folders(
                    session,
                    Facet.PROJECT,
                    previous_name,
                    normalized_name,
                    image_root=server.vault.image_root,
                )
            return project

        return server.vault.db.run_task(
            update, project_id, priority=DBPriority.IMMEDIATE
        )

    @router.delete(
        "/projects/{project_id}",
        summary="Delete a project",
        response_model=ProjectDeleteResponse,
    )
    def delete_project(request: Request, project_id: int):
        """Delete a project.

        Characters and picture sets belonging to the project have their
        project_id nulled (they become unassigned).  Attachments and their
        files are permanently removed.
        """
        server.auth.require_user_id(request)

        def do_delete(session: Session, pid: int):
            project = session.get(Project, pid)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")

            # Collect attachment paths before cascade-delete removes the rows.
            attachment_paths = [a.stored_path for a in project.attachments]

            # Drop the project from every character / picture set that belongs to
            # it, then re-derive each entity's primary-project pointer from the
            # memberships that survive (issue #125: an entity may be in several
            # projects, so losing one does not necessarily unassign it).
            affected_characters = session.exec(
                select(Character).where(character_in_project(pid))
            ).all()
            affected_sets = session.exec(
                select(PictureSet).where(picture_set_in_project(pid))
            ).all()

            session.exec(
                delete(CharacterProjectMember).where(
                    CharacterProjectMember.project_id == pid
                )
            )
            session.exec(
                delete(PictureSetProjectMember).where(
                    PictureSetProjectMember.project_id == pid
                )
            )
            session.flush()

            for character in affected_characters:
                remaining = character_project_ids(session, int(character.id))
                character.project_id = remaining[0] if remaining else None
                session.add(character)

            for picture_set in affected_sets:
                remaining = picture_set_project_ids(session, int(picture_set.id))
                picture_set.project_id = remaining[0] if remaining else None
                session.add(picture_set)

            session.exec(
                delete(PictureProjectMember).where(
                    PictureProjectMember.project_id == pid
                )
            )
            session.flush()

            for picture in session.exec(
                select(Picture).where(Picture.project_id == pid)
            ).all():
                # The picture's own memberships are gone for this project; fall
                # back to any project it still belongs to, mirroring the
                # reconciliation service's repoint rule.
                picture.project_id = session.exec(
                    select(PictureProjectMember.project_id)
                    .where(PictureProjectMember.picture_id == picture.id)
                    .order_by(PictureProjectMember.project_id.asc())
                ).first()
                session.add(picture)

            session.delete(project)  # cascade-deletes ProjectAttachment rows
            session.commit()
            return attachment_paths

        attachment_paths = server.vault.db.run_task(
            do_delete, project_id, priority=DBPriority.IMMEDIATE
        )

        # Remove attachment files from disk after the transaction commits.
        for stored_path in attachment_paths:
            try:
                full_path = resolve_path_within(server.vault.image_root, stored_path)
            except ValueError:
                logger.warning(
                    "Refusing to delete suspicious stored_path: %r", stored_path
                )
                continue
            try:
                if os.path.isfile(full_path):
                    os.remove(full_path)
            except OSError as exc:
                logger.warning(
                    "Could not remove attachment file %s: %s", full_path, exc
                )

        # Remove the project attachments directory if it is now empty.
        project_dir = resolve_path_within(
            server.vault.image_root,
            "projects",
            str(project_id),
        )
        try:
            if os.path.isdir(project_dir):
                shutil.rmtree(project_dir, ignore_errors=True)
        except OSError as exc:
            logger.warning(
                "Could not remove project directory %s: %s", project_dir, exc
            )

        return {"status": "deleted", "id": project_id}

    @router.get(
        "/projects/{project_id}/summary",
        summary="Get project picture count",
        description="Returns the number of pictures assigned to a project. Use 'UNASSIGNED' as project_id to count pictures with no project.",
        response_model=ProjectSummaryResponse,
    )
    def get_project_summary(request: Request, project_id: str):
        server.auth.require_user_id(request)
        # Scope guard (BOLA): a resource-scoped token may only summarise its own
        # project; the aggregate UNASSIGNED view is owner-only.
        scope = getattr(request.state, "token_scope", None)
        if project_id == "UNASSIGNED":
            if scope is not None and scope.resource_type is not None:
                raise HTTPException(
                    status_code=403,
                    detail="Token is not authorised for aggregate summaries",
                )
        else:
            try:
                _require_scope_allows_project(request, int(project_id))
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="Invalid project_id"
                ) from exc

        hidden_tags = []
        if request.query_params.get("apply_tag_filter", "").lower() == "true":
            try:
                user = server.auth.get_user_for_request(request)
            except HTTPException:
                user = server.auth.get_user()
            if user:
                hidden_tags = (
                    normalize_hidden_tags(getattr(user, "hidden_tags", None)) or []
                )
        hidden_tag_set = {str(t).strip().lower() for t in hidden_tags if t}
        hidden_tag_filter = None
        if hidden_tag_set:
            hidden_tag_filter = ~exists(
                select(Tag.id).where(
                    Tag.picture_id == Picture.id,
                    Tag.tag.is_not(None),
                    func.lower(Tag.tag).in_(hidden_tag_set),
                )
            )

        if project_id == "UNASSIGNED":
            conditions = [
                Picture.deleted.is_(False),
                ~exists(
                    select(PictureProjectMember.picture_id).where(
                        PictureProjectMember.picture_id == Picture.id
                    )
                ),
            ]
        else:
            try:
                pid = int(project_id)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="Invalid project_id"
                ) from exc

            def ensure_project_exists(session: Session, pid_value: int):
                if session.get(Project, pid_value) is None:
                    raise HTTPException(status_code=404, detail="Project not found")

            # Pure existence read - keep it off the writer queue (issue #651).
            server.vault.db.run_immediate_read_task(ensure_project_exists, pid)
            conditions = [
                Picture.deleted.is_(False),
                exists(
                    select(PictureProjectMember.picture_id).where(
                        PictureProjectMember.picture_id == Picture.id,
                        PictureProjectMember.project_id == pid,
                    )
                ),
            ]

        if hidden_tag_filter is not None:
            conditions.append(hidden_tag_filter)

        def count_for_project(session: Session) -> int:
            return session.exec(select(func.count(Picture.id)).where(*conditions)).one()

        image_count = server.vault.db.run_immediate_read_task(count_for_project)
        return {"image_count": image_count}

    # -------------------------------------------------------------------------
    # Export
    # -------------------------------------------------------------------------

    @router.get(
        "/projects/{project_id}/export",
        summary="Export project as ZIP",
        description="Download a ZIP archive of the project: metadata, attachment files, and optionally all pictures belonging to its characters and picture sets.",
        response_class=StreamingResponse,
        responses={200: {"content": {"application/zip": {}}}},
    )
    def export_project(
        request: Request,
        project_id: int,
        include_pictures: bool = Query(default=True),
        include_attachments: bool = Query(default=True),
    ):
        server.auth.require_user_id(request)
        token_scope = getattr(request.state, "token_scope", None)
        # Every scoped token, not only a READ one: the attachment opt-in is a
        # property of the grant, so a scope that is not READ must not skip it.
        if token_scope is not None and not token_scope.include_attachments:
            include_attachments = False

        def _safe(name: str) -> str:
            """Slugify a name for use as a directory component."""
            slug = re.sub(r"[^\w\-. ]", "_", name or "unnamed").strip()
            return slug[:64] or "unnamed"

        def _unique_name(used: set, name: str) -> str:
            """Return a deduplicated variant of name, updating used in-place."""
            if name not in used:
                used.add(name)
                return name
            stem, _, ext = name.rpartition(".")
            if not stem:
                stem, ext = name, ""
                ext_dot = ""
            else:
                ext_dot = "." + ext
            i = 1
            while True:
                candidate = f"{stem} ({i}){ext_dot}"
                if candidate not in used:
                    used.add(candidate)
                    return candidate
                i += 1

        def gather(session: Session, pid: int):
            project = session.get(Project, pid)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")

            project_data = {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "extra_metadata": project.extra_metadata,
                "created_at": project.created_at.isoformat()
                if project.created_at
                else None,
            }

            characters_data = [
                {"id": c.id, "name": c.name, "description": c.description}
                for c in session.exec(
                    select(Character).where(character_in_project(pid))
                ).all()
            ]
            picture_sets_data = [
                {"id": s.id, "name": s.name, "description": s.description}
                for s in session.exec(
                    select(PictureSet).where(picture_set_in_project(pid))
                ).all()
            ]
            attachments_data = [
                {"stored_path": a.stored_path, "original_filename": a.original_filename}
                for a in session.exec(
                    select(ProjectAttachment)
                    .where(ProjectAttachment.project_id == pid)
                    .order_by(ProjectAttachment.created_at)
                ).all()
            ]

            char_pictures: dict = {}
            set_pictures: dict = {}
            if include_pictures:
                for char in characters_data:
                    pic_ids = session.exec(
                        select(Face.picture_id)
                        .where(Face.character_id == char["id"])
                        .distinct()
                    ).all()
                    paths = []
                    for pid_inner in pic_ids:
                        pic = session.get(Picture, pid_inner)
                        if pic and not pic.deleted and pic.file_path:
                            paths.append(pic.file_path)
                    char_pictures[char["id"]] = paths

                for pset in picture_sets_data:
                    pic_ids = session.exec(
                        select(PictureSetMember.picture_id).where(
                            PictureSetMember.set_id == pset["id"]
                        )
                    ).all()
                    paths = []
                    for pid_inner in pic_ids:
                        pic = session.get(Picture, pid_inner)
                        if pic and not pic.deleted and pic.file_path:
                            paths.append(pic.file_path)
                    set_pictures[pset["id"]] = paths

            return (
                project_data,
                characters_data,
                picture_sets_data,
                attachments_data,
                char_pictures,
                set_pictures,
            )

        (
            project_data,
            characters_data,
            picture_sets_data,
            attachments_data,
            char_pictures,
            set_pictures,
        ) = server.vault.db.run_immediate_read_task(gather, project_id)

        root = _safe(project_data["name"])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            # project.json
            zf.writestr(
                f"{root}/project.json",
                json.dumps(
                    {
                        **project_data,
                        "exported_at": datetime.utcnow().isoformat(),
                    },
                    indent=2,
                ),
            )

            # Characters
            for char in characters_data:
                char_dir = f"{root}/characters/{_safe(char['name'])}"
                zf.writestr(
                    f"{char_dir}/character.json",
                    json.dumps(char, indent=2),
                )
                if include_pictures:
                    char_slug = _safe(char["name"])
                    for i, file_path in enumerate(
                        char_pictures.get(char["id"], []), start=1
                    ):
                        try:
                            full = resolve_path_within(
                                server.vault.image_root, file_path
                            )
                        except ValueError:
                            continue
                        if not os.path.isfile(full):
                            continue
                        ext = os.path.splitext(file_path)[1].lower()
                        fname = f"{char_slug}_{i:03d}{ext}"
                        try:
                            zf.write(full, f"{char_dir}/pictures/{fname}")
                        except OSError as exc:
                            logger.debug(
                                "Failed to add character picture to export ZIP: %s", exc
                            )

            # Picture sets
            for pset in picture_sets_data:
                set_dir = f"{root}/picture_sets/{_safe(pset['name'])}"
                zf.writestr(
                    f"{set_dir}/pictureset.json",
                    json.dumps(pset, indent=2),
                )
                if include_pictures:
                    set_slug = _safe(pset["name"])
                    for i, file_path in enumerate(
                        set_pictures.get(pset["id"], []), start=1
                    ):
                        try:
                            full = resolve_path_within(
                                server.vault.image_root, file_path
                            )
                        except ValueError:
                            continue
                        if not os.path.isfile(full):
                            continue
                        ext = os.path.splitext(file_path)[1].lower()
                        fname = f"{set_slug}_{i:03d}{ext}"
                        try:
                            zf.write(full, f"{set_dir}/pictures/{fname}")
                        except OSError as exc:
                            logger.debug(
                                "Failed to add picture set image to export ZIP: %s", exc
                            )

            # Attachments
            if include_attachments:
                used_attachment_names: set = set()
                for att in attachments_data:
                    try:
                        full = resolve_path_within(
                            server.vault.image_root, att["stored_path"]
                        )
                    except ValueError:
                        continue
                    if not os.path.isfile(full):
                        continue
                    fname = _unique_name(
                        used_attachment_names, att["original_filename"]
                    )
                    try:
                        zf.write(full, f"{root}/attachments/{fname}")
                    except OSError as exc:
                        logger.debug("Failed to add attachment to export ZIP: %s", exc)

        buf.seek(0)
        safe_filename = re.sub(r"[^\w\-.]", "_", project_data["name"] or "project")
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename}.zip"'
            },
        )

    # -------------------------------------------------------------------------
    # Attachments
    # -------------------------------------------------------------------------

    @router.get(
        "/projects/{project_id}/attachments",
        summary="List attachments for a project",
        response_model=list[ProjectAttachmentResponse],
    )
    def list_attachments(request: Request, project_id: int):
        server.auth.require_user_id(request)
        token_scope = getattr(request.state, "token_scope", None)
        # Every scoped token, not only a READ one: the attachment opt-in is a
        # property of the grant, so a scope that is not READ must not skip it.
        if token_scope is not None and not token_scope.include_attachments:
            raise HTTPException(
                status_code=403,
                detail="This token does not allow access to project attachments",
            )

        def fetch(session: Session, pid: int):
            if session.get(Project, pid) is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return session.exec(
                select(ProjectAttachment)
                .where(ProjectAttachment.project_id == pid)
                .order_by(ProjectAttachment.created_at)
            ).all()

        return server.vault.db.run_task(
            fetch, project_id, priority=DBPriority.IMMEDIATE
        )

    @router.post(
        "/projects/{project_id}/attachments",
        summary="Upload an attachment to a project",
        response_model=ProjectAttachmentResponse,
    )
    async def upload_attachment(request: Request, project_id: int, file: UploadFile):
        server.auth.require_user_id(request)

        max_bytes = _max_attachment_bytes()
        contents = await file.read()
        if len(contents) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File exceeds the maximum allowed size of "
                    f"{max_bytes // (1024 * 1024)} MB."
                ),
            )

        def check_project(session: Session, pid: int):
            if session.get(Project, pid) is None:
                raise HTTPException(status_code=404, detail="Project not found")

        server.vault.db.run_task(
            check_project, project_id, priority=DBPriority.IMMEDIATE
        )

        att_dir = _attachments_dir(project_id)
        safe_stem = uuid.uuid4().hex
        original_filename = file.filename or "attachment"
        raw_ext = os.path.splitext(original_filename)[1]
        ext = re.sub(r"[^a-zA-Z0-9.]", "", raw_ext)[:16]
        stored_filename = safe_stem + ext
        full_path = resolve_path_within(att_dir, stored_filename)

        with open(full_path, "wb") as f:
            f.write(contents)

        mime_type = file.content_type or mimetypes.guess_type(original_filename)[0]
        # Derive rel_path from the already-validated full_path so the stored
        # value is guaranteed to be within image_root (CodeQL sanitizer path).
        rel_path = os.path.relpath(full_path, server.vault.image_root)

        def insert_record(session: Session):
            attachment = ProjectAttachment(
                project_id=project_id,
                original_filename=original_filename,
                stored_path=rel_path,
                mime_type=mime_type,
                file_size=len(contents),
                created_at=datetime.utcnow(),
            )
            session.add(attachment)
            session.commit()
            session.refresh(attachment)
            return attachment

        return server.vault.db.run_task(insert_record, priority=DBPriority.IMMEDIATE)

    @router.post(
        "/projects/{project_id}/attachments/url",
        summary="Add a URL bookmark to a project",
        response_model=ProjectAttachmentResponse,
    )
    def add_url_attachment(
        request: Request,
        project_id: int,
        body: ProjectUrlAttachmentRequest,
    ):
        server.auth.require_user_id(request)
        url = (body.url or "").strip()
        title = (body.title or "").strip() or url
        if not url:
            raise HTTPException(status_code=400, detail="url is required")

        def check_and_insert(session: Session):
            if session.get(Project, project_id) is None:
                raise HTTPException(status_code=404, detail="Project not found")
            attachment = ProjectAttachment(
                project_id=project_id,
                original_filename=title,
                stored_path="",
                mime_type=None,
                file_size=0,
                url=url,
                created_at=datetime.utcnow(),
            )
            session.add(attachment)
            session.commit()
            session.refresh(attachment)
            return attachment

        return server.vault.db.run_task(check_and_insert, priority=DBPriority.IMMEDIATE)

    @router.get(
        "/projects/{project_id}/attachments/{attachment_id}",
        summary="Download a project attachment",
        response_class=FileResponse,
        responses={200: {"content": {"application/octet-stream": {}}}},
    )
    def download_attachment(request: Request, project_id: int, attachment_id: int):
        server.auth.require_user_id(request)
        token_scope = getattr(request.state, "token_scope", None)
        # Every scoped token, not only a READ one: the attachment opt-in is a
        # property of the grant, so a scope that is not READ must not skip it.
        if token_scope is not None and not token_scope.include_attachments:
            raise HTTPException(
                status_code=403,
                detail="This token does not allow access to project attachments",
            )

        def fetch(session: Session, pid: int, aid: int):
            attachment = session.get(ProjectAttachment, aid)
            if attachment is None or attachment.project_id != pid:
                raise HTTPException(status_code=404, detail="Attachment not found")
            return attachment

        attachment = server.vault.db.run_task(
            fetch, project_id, attachment_id, priority=DBPriority.IMMEDIATE
        )
        try:
            full_path = resolve_path_within(
                server.vault.image_root, attachment.stored_path
            )
        except ValueError:
            raise HTTPException(status_code=403, detail="Forbidden")
        if not os.path.isfile(full_path):
            raise HTTPException(
                status_code=404, detail="Attachment file not found on disk"
            )
        return FileResponse(
            full_path,
            filename=attachment.original_filename,
            media_type=attachment.mime_type or "application/octet-stream",
        )

    @router.delete(
        "/projects/{project_id}/attachments/{attachment_id}",
        summary="Delete a project attachment",
        response_model=ProjectDeleteResponse,
    )
    def delete_attachment(request: Request, project_id: int, attachment_id: int):
        server.auth.require_user_id(request)

        def remove(session: Session, pid: int, aid: int):
            attachment = session.get(ProjectAttachment, aid)
            if attachment is None or attachment.project_id != pid:
                raise HTTPException(status_code=404, detail="Attachment not found")
            stored_path = attachment.stored_path
            session.delete(attachment)
            session.commit()
            return stored_path

        stored_path = server.vault.db.run_task(
            remove, project_id, attachment_id, priority=DBPriority.IMMEDIATE
        )
        if stored_path:
            try:
                full_path = resolve_path_within(server.vault.image_root, stored_path)
            except ValueError:
                logger.warning(
                    "Refusing to delete suspicious stored_path: %r", stored_path
                )
                return {"status": "deleted", "id": attachment_id}
            try:
                if os.path.isfile(full_path):
                    os.remove(full_path)
            except OSError as exc:
                logger.warning(
                    "Could not remove attachment file %s: %s", full_path, exc
                )

        return {"status": "deleted", "id": attachment_id}

    return router
