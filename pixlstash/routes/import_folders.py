"""Import folder CRUD API for automatic watch-folder imports."""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import Integer, cast, func, update
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.import_folder import ImportFolder
from pixlstash.db_models.picture import Picture
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.host_path_utils import is_absolute_host_path, normalize_host_path
from pixlstash.utils.library_roots import (
    holding_folder_overlap_lock,
    refuse_overlapping_folder,
)
from pixlstash.utils.reference_folder_validator import (
    data_folder_conflict_detail,
    validate_reference_folder_path,
)

logger = get_logger(__name__)


class ImportFolderCreateRequest(BaseModel):
    folder: str
    label: Optional[str] = None
    host_path: Optional[str] = None
    delete_after_import: bool = False


class ImportFolderUpdateRequest(BaseModel):
    label: Optional[str] = None
    host_path: Optional[str] = None
    delete_after_import: Optional[bool] = None


class ImportFolderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    folder: str
    host_path: Optional[str]
    label: str
    delete_after_import: bool
    last_checked: Optional[float]
    picture_count: int = 0
    # How many of its pictures are still in the Inbox.
    unseen_count: int = 0
    # When the newest picture it brought in arrived, or None before the first.
    last_imported_at: Optional[datetime] = None
    # The folder is not on disk (unplugged drive, renamed, deleted).
    missing: bool = False


class ImportFoldersListResponse(BaseModel):
    folders: list[ImportFolderResponse]


class InboxBatch(BaseModel):
    """One arrival: unseen pictures from one folder with no hour-long gap."""

    import_source_folder: str
    label: str
    count: int
    first_imported_at: datetime
    last_imported_at: datetime


class InboxResponse(BaseModel):
    total: int
    folder_count: int
    batches: list[InboxBatch]


class InboxSeenRequest(BaseModel):
    """What to take out of the Inbox; at least one field must be given.

    ``all`` empties it. Otherwise the request names pictures, or a folder,
    optionally narrowed to one batch by its ``since``/``until`` arrival times.
    """

    all: bool = False
    picture_ids: Optional[list[int]] = None
    import_source_folder: Optional[str] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None

    @field_validator("since", "until")
    @classmethod
    def _read_naive_as_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        # `imported_at` is stored in UTC and refuses a naive bind, so a time
        # without an offset is read as UTC, as the server writes them.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class InboxSeenResponse(BaseModel):
    marked: int


# A gap longer than this between two arrivals from one folder starts a batch.
INBOX_BATCH_GAP = timedelta(hours=1)


class ImportFolderDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    id: int


def create_router(server) -> APIRouter:
    """Create the import-folders API router.

    Args:
        server: The Server instance providing vault/db/auth access.

    Returns:
        Configured APIRouter with import-folder endpoints.
    """

    router = APIRouter()

    def _normalize_optional_host_path(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        host_path = str(value).strip()
        if not host_path:
            return None
        normalized = normalize_host_path(host_path)
        if not is_absolute_host_path(normalized):
            raise HTTPException(
                status_code=400,
                detail="Host path must be an absolute path.",
            )
        return normalized

    def _to_response(
        folder: ImportFolder,
        picture_count: int = 0,
        unseen_count: int = 0,
        last_imported_at: Optional[datetime] = None,
    ) -> ImportFolderResponse:
        return ImportFolderResponse(
            id=folder.id,
            folder=folder.folder,
            host_path=folder.host_path,
            label=folder.label,
            delete_after_import=folder.delete_after_import,
            last_checked=folder.last_checked,
            picture_count=picture_count,
            unseen_count=unseen_count,
            last_imported_at=last_imported_at,
            missing=not os.path.isdir(folder.folder),
        )

    def _notify_changed(request: Request) -> None:
        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "source": "ui",
                "origin_client_id": getattr(request.state, "origin_client_id", None),
                "change_kind": "updated",
            },
        )

    @router.get(
        "/import-folders",
        summary="List import folders",
        description="Returns all folders used by the automatic import watcher.",
        response_model=ImportFoldersListResponse,
        tags=["folders"],
    )
    def list_import_folders(request: Request):
        server.auth.require_user_id(request)
        if getattr(request.state, "token_scope", None) is not None:
            return ImportFoldersListResponse(folders=[])

        def fetch(session: Session):
            folders = session.exec(select(ImportFolder).order_by(ImportFolder.id)).all()
            count_rows = session.exec(
                select(
                    Picture.import_source_folder,
                    func.count(Picture.id),
                    func.sum(cast(Picture.unseen, Integer)),
                    func.max(Picture.imported_at),
                )
                .where(
                    Picture.import_source_folder.is_not(None),
                    Picture.deleted.is_(False),
                )
                .group_by(Picture.import_source_folder)
            ).all()
            stats_by_folder = {
                str(path): (int(count), int(unseen or 0), latest)
                for path, count, unseen, latest in count_rows
            }
            return folders, stats_by_folder

        folders, stats_by_folder = server.vault.db.run_task(
            fetch,
            priority=DBPriority.IMMEDIATE,
        )
        responses = []
        for folder in folders:
            count, unseen, latest = stats_by_folder.get(folder.folder, (0, 0, None))
            responses.append(
                _to_response(
                    folder,
                    picture_count=count,
                    unseen_count=unseen,
                    last_imported_at=latest,
                )
            )
        return ImportFoldersListResponse(folders=responses)

    @router.get(
        "/import-folders/inbox",
        summary="What is in the Inbox",
        description=(
            "Pictures import folders brought in that the owner has not looked "
            "at yet, grouped by folder and then by batch: a gap of more than an "
            "hour between two arrivals starts a new batch. Newest batch first."
        ),
        response_model=InboxResponse,
        tags=["folders"],
    )
    def get_inbox(request: Request):
        server.auth.require_user_id(request)

        def fetch(session: Session):
            rows = session.exec(
                select(Picture.import_source_folder, Picture.imported_at)
                .where(
                    Picture.unseen.is_(True),
                    Picture.deleted.is_(False),
                    Picture.import_source_folder.is_not(None),
                    Picture.imported_at.is_not(None),
                )
                .order_by(Picture.import_source_folder, Picture.imported_at)
            ).all()
            labels = {
                folder.folder: folder.label
                for folder in session.exec(select(ImportFolder)).all()
            }
            return rows, labels

        rows, labels = server.vault.db.run_task(fetch, priority=DBPriority.IMMEDIATE)
        return InboxResponse(
            total=len(rows),
            folder_count=len({folder for folder, _ in rows}),
            batches=_group_into_batches(rows, labels),
        )

    @router.post(
        "/import-folders/inbox/seen",
        summary="Take pictures out of the Inbox",
        description=(
            "Marks pictures seen: all of them, the ones named, or one folder's, "
            "optionally only one batch of it. They stay in the library."
        ),
        response_model=InboxSeenResponse,
        tags=["folders"],
    )
    def mark_inbox_seen(request: Request, payload: InboxSeenRequest = Body(...)):
        conditions = [Picture.unseen.is_(True)]
        if not payload.all:
            if payload.picture_ids is None and not payload.import_source_folder:
                raise HTTPException(
                    status_code=400,
                    detail="Name pictures or a folder, or set all.",
                )
            if payload.picture_ids is not None:
                conditions.append(Picture.id.in_(payload.picture_ids))
            if payload.import_source_folder:
                conditions.append(
                    Picture.import_source_folder == payload.import_source_folder
                )
            if payload.since is not None:
                conditions.append(Picture.imported_at >= payload.since)
            if payload.until is not None:
                conditions.append(Picture.imported_at <= payload.until)

        def mark(session: Session) -> int:
            result = session.exec(
                update(Picture).where(*conditions).values(unseen=False)
            )
            session.commit()
            return int(result.rowcount or 0)

        marked = server.vault.db.run_task(mark, priority=DBPriority.IMMEDIATE)
        if marked:
            logger.info("Inbox: marked %d picture(s) seen", marked)
            _notify_changed(request)
        return InboxSeenResponse(marked=marked)

    @router.post(
        "/import-folders",
        summary="Add an import folder",
        description="Adds a folder watched for automatic imports.",
        response_model=ImportFolderResponse,
        tags=["folders"],
    )
    @holding_folder_overlap_lock
    def create_import_folder(
        request: Request,
        payload: ImportFolderCreateRequest = Body(...),
    ):

        folder = os.path.normpath(payload.folder)
        host_path = _normalize_optional_host_path(payload.host_path)
        error = validate_reference_folder_path(folder)
        if error:
            raise HTTPException(status_code=400, detail=error)

        if server.running_in_docker() and host_path is None:
            raise HTTPException(
                status_code=400,
                detail="Host path is required in Docker mode.",
            )

        # A watch folder may not overlap a library's folder, a reference
        # folder or another watch folder, in either direction: it would import,
        # and with `delete_after_import` unlink, pictures another root owns
        # (#1223).
        #
        # Outside the DB task on purpose: this reads the registry and a read
        # nested inside `run_task` would be a session inside a session.
        refuse_overlapping_folder(folder, server, server.vault)

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
                detail = data_folder_conflict_detail(
                    image_root,
                    inside=folder == image_root
                    or folder.startswith(image_root + os.sep),
                    contains=folder == image_root
                    or image_root.startswith(folder + os.sep),
                )
                if detail:
                    raise HTTPException(status_code=409, detail=detail)

            import_folder = ImportFolder(
                folder=folder,
                host_path=host_path,
                label=label,
                delete_after_import=bool(payload.delete_after_import),
            )
            session.add(import_folder)
            session.commit()
            session.refresh(import_folder)
            return import_folder

        import_folder = server.vault.db.run_task(insert, priority=DBPriority.IMMEDIATE)

        _notify_changed(request)
        return _to_response(import_folder)

    @router.patch(
        "/import-folders/{folder_id}",
        summary="Update an import folder",
        description="Updates label and import behavior for an import folder.",
        response_model=ImportFolderResponse,
        tags=["folders"],
    )
    def update_import_folder(
        folder_id: int,
        request: Request,
        payload: ImportFolderUpdateRequest = Body(...),
    ):

        def update(session: Session):
            folder = session.get(ImportFolder, folder_id)
            if folder is None:
                raise HTTPException(status_code=404, detail="Import folder not found.")
            if payload.label is not None:
                folder.label = payload.label
            if "host_path" in payload.model_fields_set:
                folder.host_path = _normalize_optional_host_path(payload.host_path)
            if payload.delete_after_import is not None:
                folder.delete_after_import = payload.delete_after_import
            session.add(folder)
            session.commit()
            session.refresh(folder)
            return folder

        folder = server.vault.db.run_task(update, priority=DBPriority.IMMEDIATE)

        _notify_changed(request)
        return _to_response(folder)

    @router.delete(
        "/import-folders/{folder_id}",
        summary="Remove an import folder",
        description="Removes an import folder from automatic monitoring.",
        tags=["folders"],
        response_model=ImportFolderDeleteResponse,
    )
    def delete_import_folder(folder_id: int, request: Request):

        def remove(session: Session):
            folder = session.get(ImportFolder, folder_id)
            if folder is None:
                raise HTTPException(status_code=404, detail="Import folder not found.")
            session.delete(folder)
            session.commit()
            return folder.folder

        deleted_path = server.vault.db.run_task(remove, priority=DBPriority.IMMEDIATE)
        logger.info("Import folder removed: %s", deleted_path)

        _notify_changed(request)
        return {"status": "success", "id": folder_id}

    return router


def _group_into_batches(rows, labels: dict[str, str]) -> list[InboxBatch]:
    """Split ``(folder, imported_at)`` rows, sorted by both, into batches.

    Args:
        rows: Unseen pictures' import folder and arrival time, ordered by
            folder and then by time.
        labels: Import-folder path to its label. A folder removed since keeps
            its pictures' batches under the folder's own name.

    Returns:
        The batches, newest arrival first.
    """
    batches: list[InboxBatch] = []
    current: Optional[InboxBatch] = None
    for folder, imported_at in rows:
        if (
            current is None
            or current.import_source_folder != folder
            or imported_at - current.last_imported_at > INBOX_BATCH_GAP
        ):
            current = InboxBatch(
                import_source_folder=folder,
                label=labels.get(folder) or os.path.basename(folder) or folder,
                count=0,
                first_imported_at=imported_at,
                last_imported_at=imported_at,
            )
            batches.append(current)
        current.count += 1
        current.last_imported_at = imported_at
    batches.sort(key=lambda batch: batch.last_imported_at, reverse=True)
    return batches
