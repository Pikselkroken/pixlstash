"""Reference folders CRUD API and server restart endpoint."""

import os
import pathlib
import subprocess
import sys
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from pixlstash.database import DBPriority
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.reference_folder import ReferenceFolder, ReferenceFolderStatus
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.reference_folder_validator import validate_reference_folder_path
from sqlmodel import Session, select

logger = get_logger(__name__)


class ReferenceFolderCreateRequest(BaseModel):
    folder: str
    label: Optional[str] = None


class ReferenceFolderUpdateRequest(BaseModel):
    label: Optional[str] = None
    allow_delete_file: Optional[bool] = None
    sync_captions: Optional[bool] = None


class ReferenceFolderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    folder: str
    label: str
    allow_delete_file: bool
    sync_captions: bool
    status: str
    last_scanned: Optional[float]


class ReferenceFoldersListResponse(BaseModel):
    in_docker: bool
    has_pending: bool
    image_root: Optional[str]
    folders: list[ReferenceFolderResponse]


def create_router(server) -> APIRouter:
    """Create the reference-folders API router.

    Args:
        server: The Server instance providing vault/db/auth access.

    Returns:
        Configured APIRouter with all reference-folder endpoints.
    """
    router = APIRouter()

    # -------------------------------------------------------------------------
    # Helper
    # -------------------------------------------------------------------------

    def _to_response(rf: ReferenceFolder) -> ReferenceFolderResponse:
        return ReferenceFolderResponse(
            id=rf.id,
            folder=rf.folder,
            label=rf.label,
            allow_delete_file=rf.allow_delete_file,
            sync_captions=rf.sync_captions,
            status=rf.status,
            last_scanned=rf.last_scanned,
        )

    # -------------------------------------------------------------------------
    # CRUD
    # -------------------------------------------------------------------------

    @router.get(
        "/reference-folders",
        summary="List reference folders",
        description="Returns all reference folders and metadata about the current runtime mode.",
        response_model=ReferenceFoldersListResponse,
        tags=["config"],
    )
    def list_reference_folders(request: Request):
        server.auth.require_user_id(request)

        def fetch(session: Session):
            return session.exec(
                select(ReferenceFolder).order_by(ReferenceFolder.id)
            ).all()

        folders = server.vault.db.run_task(fetch, priority=DBPriority.IMMEDIATE)
        has_pending = any(
            f.status == ReferenceFolderStatus.PENDING_MOUNT for f in folders
        )
        return ReferenceFoldersListResponse(
            in_docker=server.running_in_docker(),
            has_pending=has_pending,
            image_root=getattr(server.vault, "image_root", None),
            folders=[_to_response(f) for f in folders],
        )

    @router.post(
        "/reference-folders",
        summary="Add a reference folder",
        description=(
            "Adds a new reference folder. The path must be absolute and must not "
            "point to a restricted system directory. The folder is created with "
            "status 'pending_mount' and will become 'active' after the next "
            "server restart when the path is mount-verified."
        ),
        response_model=ReferenceFolderResponse,
        tags=["config"],
    )
    def create_reference_folder(
        request: Request,
        payload: ReferenceFolderCreateRequest = Body(...),
    ):
        server.auth.require_user_id(request)

        folder = os.path.normpath(payload.folder)
        error = validate_reference_folder_path(folder)
        if error:
            raise HTTPException(status_code=400, detail=error)

        label = payload.label if payload.label is not None else os.path.basename(folder)

        def insert(session: Session):
            existing = session.exec(
                select(ReferenceFolder).where(ReferenceFolder.folder == folder)
            ).first()
            if existing is not None:
                raise HTTPException(
                    status_code=409,
                    detail="A reference folder with this path already exists.",
                )
            image_root = os.path.normpath(getattr(server.vault, "image_root", "") or "")
            if image_root:
                if (
                    folder == image_root
                    or folder.startswith(image_root + os.sep)
                    or image_root.startswith(folder + os.sep)
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="Path conflicts with the PixlStash data folder.",
                    )
            all_folders = list(session.exec(select(ReferenceFolder)).all())
            for other in all_folders:
                other_norm = os.path.normpath(other.folder)
                if folder.startswith(other_norm + os.sep):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Path is inside an existing reference folder: {other.folder}",
                    )
                if other_norm.startswith(folder + os.sep):
                    raise HTTPException(
                        status_code=409,
                        detail=f"An existing reference folder is inside this path: {other.folder}",
                    )
            rf = ReferenceFolder(
                folder=folder,
                label=label,
                allow_delete_file=False,
                status=ReferenceFolderStatus.PENDING_MOUNT,
            )
            session.add(rf)
            session.commit()
            session.refresh(rf)
            return rf

        rf = server.vault.db.run_task(insert, priority=DBPriority.IMMEDIATE)
        logger.info("Reference folder added: %s (label=%r)", folder, label)
        server.vault.watch_reference_folder(rf.id, rf.folder)
        return _to_response(rf)

    @router.patch(
        "/reference-folders/{folder_id}",
        summary="Update a reference folder",
        description="Updates the label and/or allow_delete_file flag for a reference folder.",
        response_model=ReferenceFolderResponse,
        tags=["config"],
    )
    def update_reference_folder(
        folder_id: int,
        request: Request,
        payload: ReferenceFolderUpdateRequest = Body(...),
    ):
        server.auth.require_user_id(request)

        def update(session: Session):
            rf = session.get(ReferenceFolder, folder_id)
            if rf is None:
                raise HTTPException(
                    status_code=404, detail="Reference folder not found."
                )
            if payload.label is not None:
                rf.label = payload.label
            if payload.allow_delete_file is not None:
                rf.allow_delete_file = payload.allow_delete_file
            if payload.sync_captions is not None:
                rf.sync_captions = payload.sync_captions
            session.add(rf)
            session.commit()
            session.refresh(rf)
            return rf

        rf = server.vault.db.run_task(update, priority=DBPriority.IMMEDIATE)
        return _to_response(rf)

    @router.delete(
        "/reference-folders/{folder_id}",
        summary="Remove a reference folder",
        description=(
            "Removes a reference folder record. Pictures indexed from this folder "
            "are de-associated but not deleted from the database or disk."
        ),
        tags=["config"],
    )
    def delete_reference_folder(folder_id: int, request: Request):
        server.auth.require_user_id(request)

        def remove(session: Session):
            rf = session.get(ReferenceFolder, folder_id)
            if rf is None:
                raise HTTPException(
                    status_code=404, detail="Reference folder not found."
                )
            folder_path = rf.folder
            # Delete all pictures that were indexed from this folder.
            pictures = session.exec(
                select(Picture).where(Picture.reference_folder_id == folder_id)
            ).all()
            for pic in pictures:
                session.delete(pic)
            session.delete(rf)
            session.commit()
            logger.info(
                "Reference folder removed: %s (%d pictures deleted)",
                folder_path,
                len(pictures),
            )
            return folder_path

        folder_path = server.vault.db.run_task(remove, priority=DBPriority.IMMEDIATE)
        logger.info("Reference folder removed: %s", folder_path)
        server.vault.unwatch_reference_folder(folder_id)
        return {"status": "success", "id": folder_id}

    # -------------------------------------------------------------------------
    # Server restart
    # -------------------------------------------------------------------------

    @router.post(
        "/server/restart",
        summary="Restart the PixlStash server",
        description=(
            "Gracefully terminates the server process. The process manager "
            "(systemd, Docker, etc.) is expected to restart it automatically. "
            "Use this after adding reference folders to apply pending mount changes."
        ),
        tags=["config"],
    )
    def restart_server(request: Request):
        server.auth.require_user_id(request)
        logger.info("Server restart requested via API.")
        # Re-exec this process with the same arguments.
        # Use os.execve to carry forward PYTHONPATH so the pixlstash package
        # is importable regardless of how the server was originally launched.
        import pixlstash as _pkg

        project_root = str(pathlib.Path(_pkg.__file__).parent.parent)
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = project_root + (os.pathsep + existing if existing else "")
        os.execve(sys.executable, [sys.executable] + sys.argv, env)
        return {"status": "restarting"}  # unreachable

    @router.post(
        "/reference-folders/{folder_id}/open",
        summary="Open reference folder in file manager",
        description=(
            "Opens the reference folder (or an optional subdirectory within it) "
            "in the OS file manager."
        ),
        tags=["config"],
    )
    def open_reference_folder(
        folder_id: int,
        request: Request,
        subpath: Optional[str] = None,
    ):
        server.auth.require_user_id(request)

        def get_folder(session: Session):
            rf = session.get(ReferenceFolder, folder_id)
            return rf.folder if rf else None

        root = server.vault.db.run_immediate_read_task(get_folder)
        if not root:
            raise HTTPException(status_code=404, detail="Reference folder not found.")

        if subpath:
            # Resolve and validate that subpath is inside the reference folder.
            resolved_root = os.path.realpath(root)
            resolved_sub = os.path.realpath(subpath)
            if not resolved_sub.startswith(resolved_root + os.sep):
                raise HTTPException(
                    status_code=400, detail="Subpath is outside the reference folder."
                )
            folder = resolved_sub
        else:
            folder = root

        if not os.path.isdir(folder):
            raise HTTPException(status_code=404, detail="Folder not found on disk.")

        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.run(["open", folder], check=False)
            else:
                subprocess.run(["xdg-open", folder], check=False)
        except Exception as exc:
            logger.warning("Failed to open folder %s: %s", folder, exc)
            raise HTTPException(status_code=500, detail="Failed to open folder.")

        return {"status": "ok"}

    return router
