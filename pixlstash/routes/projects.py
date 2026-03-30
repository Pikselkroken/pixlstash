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
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, exists, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureProjectMember,
    PictureSet,
    PictureSetMember,
    Tag,
)
from pixlstash.db_models.project import Project, ProjectAttachment
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.caption_utils import _normalize_hidden_tags

logger = get_logger(__name__)

# Default maximum attachment size — overridden by server_config["max_attachment_size_mb"]
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
        path = os.path.join(
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

    def _resolve_project_id_by_name(session: Session, project_name: str) -> int:
        normalized_name = _normalise_project_name(project_name)
        if not normalized_name:
            raise HTTPException(status_code=404, detail="Project not found")

        project = session.exec(
            select(Project).where(func.lower(Project.name) == normalized_name.lower())
        ).first()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return int(project.id)

    # -------------------------------------------------------------------------
    # Projects CRUD
    # -------------------------------------------------------------------------

    @router.get(
        "/projects",
        summary="List all projects",
        response_model=list[ProjectResponse],
    )
    def list_projects(request: Request):
        server.auth.require_user_id(request)

        def fetch(session: Session):
            return session.exec(select(Project).order_by(Project.created_at)).all()

        projects = server.vault.db.run_task(fetch, priority=DBPriority.IMMEDIATE)
        return [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "cover_image_path": p.cover_image_path,
                "extra_metadata": p.extra_metadata,
                "created_at": p.created_at,
            }
            for p in projects
        ]

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

        project = server.vault.db.run_task(insert, priority=DBPriority.IMMEDIATE)
        return project

    @router.get(
        "/projects/by-name/{project_name}",
        summary="Get a project by name",
        response_model=ProjectResponse,
    )
    def get_project_by_name(request: Request, project_name: str):
        server.auth.require_user_id(request)

        resolved_project_id = server.vault.db.run_task(
            _resolve_project_id_by_name,
            project_name,
            priority=DBPriority.IMMEDIATE,
        )

        def fetch(session: Session, pid: int):
            project = session.get(Project, pid)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return project

        return server.vault.db.run_task(
            fetch,
            resolved_project_id,
            priority=DBPriority.IMMEDIATE,
        )

    @router.get(
        "/projects/{project_id}/picture_sets",
        summary="List picture sets for a project",
        description="Returns all picture sets that belong to the given project. "
        "``project_id`` may be a numeric ID or a project name (case-insensitive).",
    )
    def list_project_picture_sets(request: Request, project_id: str):
        server.auth.require_user_id(request)

        def fetch(session: Session, pid_or_name: str):
            # Resolve by numeric ID first, then fall back to case-insensitive name.
            project = None
            try:
                numeric_id = int(pid_or_name)
                project = session.get(Project, numeric_id)
            except (TypeError, ValueError):
                pass
            if project is None:
                project = session.exec(
                    select(Project).where(
                        func.lower(Project.name) == pid_or_name.lower()
                    )
                ).first()
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            sets = session.exec(
                select(PictureSet)
                .where(PictureSet.project_id == project.id)
                .order_by(PictureSet.name)
            ).all()
            return [
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "project_id": s.project_id,
                }
                for s in sets
            ]

        return server.vault.db.run_task(
            fetch, project_id, priority=DBPriority.IMMEDIATE
        )

    @router.get(
        "/projects/{project_id}",
        summary="Get a project by ID",
        response_model=ProjectResponse,
    )
    def get_project(request: Request, project_id: int):
        server.auth.require_user_id(request)

        def fetch(session: Session, pid: int):
            project = session.get(Project, pid)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return project

        return server.vault.db.run_task(
            fetch, project_id, priority=DBPriority.IMMEDIATE
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

            # Null out project_id on characters and picture sets.
            for character in session.exec(
                select(Character).where(Character.project_id == pid)
            ).all():
                character.project_id = None
                session.add(character)

            for picture_set in session.exec(
                select(PictureSet).where(PictureSet.project_id == pid)
            ).all():
                picture_set.project_id = None
                session.add(picture_set)

            for picture in session.exec(
                select(Picture).where(Picture.project_id == pid)
            ).all():
                picture.project_id = None
                session.add(picture)

            session.exec(
                delete(PictureProjectMember).where(
                    PictureProjectMember.project_id == pid
                )
            )

            session.delete(project)  # cascade-deletes ProjectAttachment rows
            session.commit()
            return attachment_paths

        attachment_paths = server.vault.db.run_task(
            do_delete, project_id, priority=DBPriority.IMMEDIATE
        )

        # Remove attachment files from disk after the transaction commits.
        for stored_path in attachment_paths:
            full_path = os.path.join(server.vault.image_root, stored_path)
            try:
                if os.path.isfile(full_path):
                    os.remove(full_path)
            except OSError as exc:
                logger.warning(
                    "Could not remove attachment file %s: %s", full_path, exc
                )

        # Remove the project attachments directory if it is now empty.
        project_dir = os.path.join(
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

        try:
            user = server.auth.get_user_for_request(request)
        except HTTPException:
            user = server.auth.get_user()
        hidden_tags = []
        if user and getattr(user, "apply_tag_filter", False):
            hidden_tags = (
                _normalize_hidden_tags(getattr(user, "hidden_tags", None)) or []
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

            server.vault.db.run_task(
                ensure_project_exists,
                pid,
                priority=DBPriority.IMMEDIATE,
            )
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
    )
    def export_project(
        request: Request,
        project_id: int,
        include_pictures: bool = Query(default=True),
    ):
        server.auth.require_user_id(request)

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
                    select(Character).where(Character.project_id == pid)
                ).all()
            ]
            picture_sets_data = [
                {"id": s.id, "name": s.name, "description": s.description}
                for s in session.exec(
                    select(PictureSet).where(PictureSet.project_id == pid)
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
                        full = os.path.join(server.vault.image_root, file_path)
                        if not os.path.isfile(full):
                            continue
                        ext = os.path.splitext(file_path)[1].lower()
                        fname = f"{char_slug}_{i:03d}{ext}"
                        try:
                            zf.write(full, f"{char_dir}/pictures/{fname}")
                        except OSError:
                            pass

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
                        full = os.path.join(server.vault.image_root, file_path)
                        if not os.path.isfile(full):
                            continue
                        ext = os.path.splitext(file_path)[1].lower()
                        fname = f"{set_slug}_{i:03d}{ext}"
                        try:
                            zf.write(full, f"{set_dir}/pictures/{fname}")
                        except OSError:
                            pass

            # Attachments
            used_attachment_names: set = set()
            for att in attachments_data:
                full = os.path.join(server.vault.image_root, att["stored_path"])
                if not os.path.isfile(full):
                    continue
                fname = _unique_name(used_attachment_names, att["original_filename"])
                try:
                    zf.write(full, f"{root}/attachments/{fname}")
                except OSError:
                    pass

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

        def fetch(session: Session, pid: int):
            if session.get(Project, pid) is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return session.exec(
                select(ProjectAttachment)
                .where(ProjectAttachment.project_id == pid)
                .order_by(ProjectAttachment.created_at)
            ).all()

        attachments = server.vault.db.run_task(
            fetch, project_id, priority=DBPriority.IMMEDIATE
        )
        return attachments

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
        ext = os.path.splitext(original_filename)[1]
        stored_filename = safe_stem + ext
        full_path = os.path.join(att_dir, stored_filename)

        with open(full_path, "wb") as f:
            f.write(contents)

        mime_type = file.content_type or mimetypes.guess_type(original_filename)[0]
        rel_path = os.path.join(
            "projects", str(project_id), "attachments", stored_filename
        )

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

        attachment = server.vault.db.run_task(
            insert_record, priority=DBPriority.IMMEDIATE
        )
        return attachment

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

        attachment = server.vault.db.run_task(
            check_and_insert, priority=DBPriority.IMMEDIATE
        )
        return attachment

    @router.get(
        "/projects/{project_id}/attachments/{attachment_id}",
        summary="Download a project attachment",
    )
    def download_attachment(request: Request, project_id: int, attachment_id: int):
        server.auth.require_user_id(request)

        def fetch(session: Session, pid: int, aid: int):
            attachment = session.get(ProjectAttachment, aid)
            if attachment is None or attachment.project_id != pid:
                raise HTTPException(status_code=404, detail="Attachment not found")
            return attachment

        attachment = server.vault.db.run_task(
            fetch, project_id, attachment_id, priority=DBPriority.IMMEDIATE
        )
        full_path = os.path.join(server.vault.image_root, attachment.stored_path)
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
            full_path = os.path.join(server.vault.image_root, stored_path)
            try:
                if os.path.isfile(full_path):
                    os.remove(full_path)
            except OSError as exc:
                logger.warning(
                    "Could not remove attachment file %s: %s", full_path, exc
                )

        return {"status": "deleted", "id": attachment_id}

    return router
