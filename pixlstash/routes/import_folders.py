"""Import folder CRUD API for automatic watch-folder imports."""

import os
import json
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.import_folder import ImportFolder
from pixlstash.db_models.picture import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.reference_folder_validator import validate_reference_folder_path

logger = get_logger(__name__)


class ImportFolderCreateRequest(BaseModel):
    folder: str
    label: Optional[str] = None
    delete_after_import: bool = False


class ImportFolderUpdateRequest(BaseModel):
    label: Optional[str] = None
    delete_after_import: Optional[bool] = None


class ImportFolderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    folder: str
    label: str
    delete_after_import: bool
    last_checked: Optional[float]
    picture_count: int = 0


class ImportFoldersListResponse(BaseModel):
    folders: list[ImportFolderResponse]


def create_router(server) -> APIRouter:
    """Create the import-folders API router.

    Args:
        server: The Server instance providing vault/db/auth access.

    Returns:
        Configured APIRouter with import-folder endpoints.
    """

    router = APIRouter()

    def _to_response(
        folder: ImportFolder, picture_count: int = 0
    ) -> ImportFolderResponse:
        return ImportFolderResponse(
            id=folder.id,
            folder=folder.folder,
            label=folder.label,
            delete_after_import=folder.delete_after_import,
            last_checked=folder.last_checked,
            picture_count=picture_count,
        )

    def _read_legacy_watch_folders() -> list[dict]:
        config_path = getattr(server, "_server_config_path", None)
        if not config_path or not os.path.exists(config_path):
            return []
        try:
            with open(config_path, "r") as handle:
                config = json.load(handle)
            raw = config.get("watch_folders", []) or []
        except Exception:
            return []

        legacy_folders = []
        for entry in raw:
            if isinstance(entry, str):
                folder = entry
                delete_after_import = False
                last_checked = None
            elif isinstance(entry, dict):
                folder = entry.get("folder")
                delete_after_import = bool(entry.get("delete_after_import", False))
                raw_checked = entry.get("last_checked")
                try:
                    last_checked = (
                        float(raw_checked) if raw_checked is not None else None
                    )
                except (TypeError, ValueError):
                    last_checked = None
            else:
                folder = None
                delete_after_import = False
                last_checked = None

            if not folder:
                continue
            normalized = os.path.normpath(folder)
            label = os.path.basename(normalized)
            legacy_folders.append(
                {
                    "folder": normalized,
                    "label": label,
                    "delete_after_import": delete_after_import,
                    "last_checked": last_checked,
                }
            )
        return legacy_folders

    def _seed_from_legacy_config_if_needed(session: Session) -> None:
        has_any = session.exec(select(ImportFolder.id).limit(1)).first()
        if has_any is not None:
            return

        legacy_folders = _read_legacy_watch_folders()
        if not legacy_folders:
            return

        seen = set()
        for entry in legacy_folders:
            folder = entry["folder"]
            if folder in seen:
                continue
            seen.add(folder)
            session.add(
                ImportFolder(
                    folder=folder,
                    label=entry["label"],
                    delete_after_import=entry["delete_after_import"],
                    last_checked=entry["last_checked"],
                )
            )
        session.commit()

    @router.get(
        "/import-folders",
        summary="List import folders",
        description="Returns all folders used by the automatic import watcher.",
        response_model=ImportFoldersListResponse,
        tags=["config"],
    )
    def list_import_folders(request: Request):
        server.auth.require_user_id(request)

        def fetch(session: Session):
            _seed_from_legacy_config_if_needed(session)
            folders = session.exec(select(ImportFolder).order_by(ImportFolder.id)).all()
            count_rows = session.exec(
                select(
                    Picture.import_source_folder,
                    func.count(Picture.id),
                )
                .where(
                    Picture.import_source_folder.is_not(None),
                    Picture.deleted.is_(False),
                    Picture.import_excluded.is_(False),
                )
                .group_by(Picture.import_source_folder)
            ).all()
            counts_by_folder = {str(path): int(count) for path, count in count_rows}
            return folders, counts_by_folder

        folders, counts_by_folder = server.vault.db.run_task(
            fetch,
            priority=DBPriority.IMMEDIATE,
        )
        return ImportFoldersListResponse(
            folders=[
                _to_response(
                    folder,
                    picture_count=counts_by_folder.get(folder.folder, 0),
                )
                for folder in folders
            ]
        )

    @router.post(
        "/import-folders",
        summary="Add an import folder",
        description="Adds a folder watched for automatic imports.",
        response_model=ImportFolderResponse,
        tags=["config"],
    )
    def create_import_folder(
        request: Request,
        payload: ImportFolderCreateRequest = Body(...),
    ):
        server.auth.require_user_id(request)

        folder = os.path.normpath(payload.folder)
        error = validate_reference_folder_path(folder)
        if error:
            raise HTTPException(status_code=400, detail=error)

        label = payload.label if payload.label is not None else os.path.basename(folder)

        def insert(session: Session):
            existing = session.exec(
                select(ImportFolder).where(ImportFolder.folder == folder)
            ).first()
            if existing is not None:
                raise HTTPException(
                    status_code=409,
                    detail="An import folder with this path already exists.",
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

            import_folder = ImportFolder(
                folder=folder,
                label=label,
                delete_after_import=bool(payload.delete_after_import),
            )
            session.add(import_folder)
            session.commit()
            session.refresh(import_folder)
            return import_folder

        import_folder = server.vault.db.run_task(insert, priority=DBPriority.IMMEDIATE)

        from pixlstash.event_types import EventType

        server.vault.notify(EventType.CHANGED_PICTURES)
        return _to_response(import_folder)

    @router.patch(
        "/import-folders/{folder_id}",
        summary="Update an import folder",
        description="Updates label and import behavior for an import folder.",
        response_model=ImportFolderResponse,
        tags=["config"],
    )
    def update_import_folder(
        folder_id: int,
        request: Request,
        payload: ImportFolderUpdateRequest = Body(...),
    ):
        server.auth.require_user_id(request)

        def update(session: Session):
            folder = session.get(ImportFolder, folder_id)
            if folder is None:
                raise HTTPException(status_code=404, detail="Import folder not found.")
            if payload.label is not None:
                folder.label = payload.label
            if payload.delete_after_import is not None:
                folder.delete_after_import = payload.delete_after_import
            session.add(folder)
            session.commit()
            session.refresh(folder)
            return folder

        folder = server.vault.db.run_task(update, priority=DBPriority.IMMEDIATE)

        from pixlstash.event_types import EventType

        server.vault.notify(EventType.CHANGED_PICTURES)
        return _to_response(folder)

    @router.delete(
        "/import-folders/{folder_id}",
        summary="Remove an import folder",
        description="Removes an import folder from automatic monitoring.",
        tags=["config"],
    )
    def delete_import_folder(folder_id: int, request: Request):
        server.auth.require_user_id(request)

        def remove(session: Session):
            folder = session.get(ImportFolder, folder_id)
            if folder is None:
                raise HTTPException(status_code=404, detail="Import folder not found.")
            session.delete(folder)
            session.commit()
            return folder.folder

        deleted_path = server.vault.db.run_task(remove, priority=DBPriority.IMMEDIATE)
        logger.info("Import folder removed: %s", deleted_path)

        from pixlstash.event_types import EventType

        server.vault.notify(EventType.CHANGED_PICTURES)
        return {"status": "success", "id": folder_id}

    return router
