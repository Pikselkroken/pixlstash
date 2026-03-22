import mimetypes
import os
import shutil
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import Character, PictureSet
from pixlstash.db_models.project import Project, ProjectAttachment
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# Default maximum attachment size — overridden by server_config["max_attachment_size_mb"]
_DEFAULT_MAX_ATTACHMENT_MB = 50


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

    # -------------------------------------------------------------------------
    # Projects CRUD
    # -------------------------------------------------------------------------

    @router.get("/projects", summary="List all projects")
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

    @router.post("/projects", summary="Create a project")
    def create_project(
        request: Request,
        name: str = Body(...),
        description: Optional[str] = Body(default=None),
        cover_image_path: Optional[str] = Body(default=None),
        extra_metadata: Optional[str] = Body(default=None),
    ):
        server.auth.require_user_id(request)

        def insert(session: Session):
            project = Project(
                name=name,
                description=description,
                cover_image_path=cover_image_path,
                extra_metadata=extra_metadata,
                created_at=datetime.utcnow(),
            )
            session.add(project)
            session.commit()
            session.refresh(project)
            return project

        project = server.vault.db.run_task(insert, priority=DBPriority.IMMEDIATE)
        return {
            "id": project.id,
            "name": project.name,
            "created_at": project.created_at,
        }

    @router.get("/projects/{project_id}", summary="Get a project by ID")
    def get_project(request: Request, project_id: int):
        server.auth.require_user_id(request)

        def fetch(session: Session, pid: int):
            project = session.get(Project, pid)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return project

        project = server.vault.db.run_task(
            fetch, project_id, priority=DBPriority.IMMEDIATE
        )
        return {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "cover_image_path": project.cover_image_path,
            "extra_metadata": project.extra_metadata,
            "created_at": project.created_at,
        }

    @router.put("/projects/{project_id}", summary="Update a project")
    def update_project(
        request: Request,
        project_id: int,
        name: Optional[str] = Body(default=None),
        description: Optional[str] = Body(default=None),
        cover_image_path: Optional[str] = Body(default=None),
        extra_metadata: Optional[str] = Body(default=None),
    ):
        server.auth.require_user_id(request)

        def update(session: Session, pid: int):
            project = session.get(Project, pid)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            if name is not None:
                project.name = name
            if description is not None:
                project.description = description
            if cover_image_path is not None:
                project.cover_image_path = cover_image_path
            if extra_metadata is not None:
                project.extra_metadata = extra_metadata
            session.add(project)
            session.commit()
            session.refresh(project)
            return project

        project = server.vault.db.run_task(
            update, project_id, priority=DBPriority.IMMEDIATE
        )
        return {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "cover_image_path": project.cover_image_path,
            "extra_metadata": project.extra_metadata,
            "created_at": project.created_at,
        }

    @router.delete("/projects/{project_id}", summary="Delete a project")
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
        project_dir = os.path.join(server.vault.image_root, "projects", str(project_id))
        try:
            if os.path.isdir(project_dir):
                shutil.rmtree(project_dir, ignore_errors=True)
        except OSError as exc:
            logger.warning(
                "Could not remove project directory %s: %s", project_dir, exc
            )

        return {"status": "deleted", "id": project_id}

    # -------------------------------------------------------------------------
    # Attachments
    # -------------------------------------------------------------------------

    @router.get(
        "/projects/{project_id}/attachments",
        summary="List attachments for a project",
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
        return [
            {
                "id": a.id,
                "original_filename": a.original_filename,
                "mime_type": a.mime_type,
                "file_size": a.file_size,
                "created_at": a.created_at,
            }
            for a in attachments
        ]

    @router.post(
        "/projects/{project_id}/attachments",
        summary="Upload an attachment to a project",
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
        return {
            "id": attachment.id,
            "original_filename": attachment.original_filename,
            "mime_type": attachment.mime_type,
            "file_size": attachment.file_size,
            "created_at": attachment.created_at,
        }

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
        full_path = os.path.join(server.vault.image_root, stored_path)
        try:
            if os.path.isfile(full_path):
                os.remove(full_path)
        except OSError as exc:
            logger.warning("Could not remove attachment file %s: %s", full_path, exc)

        return {"status": "deleted", "id": attachment_id}

    return router
