"""Reference folders CRUD API and server restart endpoint."""

import os
import pathlib
import re
import shutil
import subprocess
import sys
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from pixlstash.database import DBPriority
from pixlstash.db_models.tag import TAG_PENDING_SENTINEL, Tag, is_tag_sentinel
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.reference_folder import ReferenceFolder, ReferenceFolderStatus
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.caption_file_utils import (
    SIDECAR_TYPE_DESCRIPTION,
    SIDECAR_TYPE_TAGS,
    detect_folder_suffixes,
    get_sidecar_mtime,
    read_description_sidecar,
    read_tags_sidecar,
    resolve_typed_sidecar,
    write_sidecar,
    writeback_path,
)
from pixlstash.utils.host_path_utils import is_absolute_host_path, normalize_host_path
from pixlstash.utils.reference_folder_validator import (
    validate_reference_folder_path,
    validate_reference_folder_accessible,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.service.path_utils import resolve_path_within
from sqlmodel import Session, delete, select

logger = get_logger(__name__)

# A sidecar suffix is concatenated directly onto an image's path stem to locate
# and write its sidecar (see ``caption_file_utils.sidecar_path``), so it must
# stay a bare filename fragment. Allow only letters, digits, '.', '_' and '-',
# and reject anything with a path separator or "..": a value such as
# ``"_t.txt/../../../etc/cron.d/evil"`` would otherwise redirect the write-back
# outside the reference folder (CWE-22 path traversal -> arbitrary file write).
_SIDECAR_SUFFIX_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _validate_sidecar_suffix(suffix: str) -> None:
    if (
        ".." in suffix
        or "/" in suffix
        or "\\" in suffix
        or not _SIDECAR_SUFFIX_RE.match(suffix)
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Sidecar suffix may only contain letters, digits, '.', '_' and "
                "'-' (no path separators or '..')."
            ),
        )


class ReferenceFolderCreateRequest(BaseModel):
    folder: str
    label: Optional[str] = None
    host_path: Optional[str] = None
    sync_descriptions: Optional[bool] = None
    sync_tags: Optional[bool] = None
    description_suffix: Optional[str] = None
    tags_suffix: Optional[str] = None


class ReferenceFolderUpdateRequest(BaseModel):
    folder: Optional[str] = None
    label: Optional[str] = None
    allow_delete_file: Optional[bool] = None
    sync_descriptions: Optional[bool] = None
    sync_tags: Optional[bool] = None
    description_suffix: Optional[str] = None
    tags_suffix: Optional[str] = None
    host_path: Optional[str] = None


class ReferenceFolderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    folder: str
    host_path: Optional[str]
    label: str
    allow_delete_file: bool
    sync_descriptions: bool
    sync_tags: bool
    description_suffix: Optional[str]
    tags_suffix: Optional[str]
    status: str
    last_scanned: Optional[float]
    relocation: Optional[dict] = None


class MoveReferencePicturesRequest(BaseModel):
    picture_ids: list[int]
    destination_subpath: Optional[str] = None


class MoveReferencePicturesResponse(BaseModel):
    status: str
    moved_count: int
    moved_picture_ids: list[int]
    failures: list[dict] = Field(default_factory=list)


class RelocateReferenceFolderRequest(BaseModel):
    destination_folder: str


class RelocateReferenceFolderResponse(BaseModel):
    status: str
    id: int
    old_folder: str
    new_folder: str
    moved_entry_count: int
    rewritten_count: int
    moved_picture_ids: list[int]


class ReferenceFolderMetadataRequest(BaseModel):
    scope_path: Optional[str] = None
    types: list[str]


class ReferenceFolderMetadataResponse(BaseModel):
    status: str
    scope_path: str
    types: list[str]
    tags_count: int = 0
    descriptions_count: int = 0
    skipped_count: int = 0


class ReferenceFolderDetectResponse(BaseModel):
    """Sidecar naming convention detected inside a folder, used to prefill the
    Add-folder dialog."""

    tags_suffix: Optional[str]
    description_suffix: Optional[str]
    found_tags: bool
    found_descriptions: bool


class ReferenceFoldersListResponse(BaseModel):
    in_docker: bool
    has_pending: bool
    image_root: Optional[str]
    folders: list[ReferenceFolderResponse]


class ReferenceFolderDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    id: int


class ServerRestartResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str


class ReferenceFolderOpenResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str


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

    def _require_owner_request(request: Request) -> None:
        server.auth.require_user_id(request)
        if getattr(request.state, "token_scope", None) is not None:
            raise HTTPException(
                status_code=403,
                detail="This folder operation is only available to the owner.",
            )

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

    def _normalize_suffix(value: Optional[str]) -> Optional[str]:
        """Trim and validate a sidecar suffix; empty means "auto / not configured".

        The suffix is concatenated onto an image's path stem to locate and write
        its sidecar, so it must be a bare filename fragment with no path
        separators or ``..``; otherwise a crafted value would let the write-back
        escape the reference folder (path traversal to arbitrary file write).
        """
        if value is None:
            return None
        suffix = str(value).strip()
        if not suffix:
            return None
        _validate_sidecar_suffix(suffix)
        return suffix

    def _to_response(rf: ReferenceFolder) -> ReferenceFolderResponse:
        return ReferenceFolderResponse(
            id=rf.id,
            folder=rf.folder,
            host_path=rf.host_path,
            label=rf.label,
            allow_delete_file=rf.allow_delete_file,
            sync_descriptions=rf.sync_descriptions,
            sync_tags=rf.sync_tags,
            description_suffix=rf.description_suffix,
            tags_suffix=rf.tags_suffix,
            status=rf.status,
            last_scanned=rf.last_scanned,
        )

    def _is_within_path(path: str, root: str) -> bool:
        try:
            path_real = os.path.realpath(os.path.normpath(path))
            root_real = os.path.realpath(os.path.normpath(root))
            return os.path.commonpath([path_real, root_real]) == root_real
        except (OSError, ValueError):
            return False

    def _resolve_scope_path(root: str, scope_path: Optional[str]) -> str:
        if not scope_path:
            return os.path.realpath(os.path.normpath(root))
        raw_scope = str(scope_path).strip()
        if not raw_scope:
            return os.path.realpath(os.path.normpath(root))
        if os.path.isabs(raw_scope):
            resolved = os.path.realpath(os.path.normpath(raw_scope))
        else:
            resolved = os.path.realpath(os.path.normpath(os.path.join(root, raw_scope)))
        root_real = os.path.realpath(os.path.normpath(root))
        if not _is_within_path(resolved, root_real):
            raise HTTPException(
                status_code=400,
                detail="Scope path is outside the reference folder.",
            )
        return resolved

    def _validate_destination_dir(root: str, subpath: Optional[str]) -> str:
        destination = _resolve_scope_path(root, subpath)
        if not os.path.isdir(destination):
            raise HTTPException(
                status_code=404,
                detail="Destination folder not found on disk.",
            )
        return destination

    def _validate_reference_folder_conflicts(
        session: Session,
        folder: str,
        *,
        exclude_id: int | None = None,
    ) -> None:
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
            if exclude_id is not None and other.id == exclude_id:
                continue
            other_norm = os.path.normpath(other.folder)
            if folder == other_norm:
                raise HTTPException(
                    status_code=409,
                    detail="A reference folder with this path already exists.",
                )
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

    def _sidecar_suffix_for_move(image_path: str, sidecar_path: str) -> str | None:
        if not sidecar_path:
            return None
        if os.path.normpath(os.path.dirname(image_path)) != os.path.normpath(
            os.path.dirname(sidecar_path)
        ):
            return None
        image_stem = os.path.splitext(image_path)[0]
        if not sidecar_path.startswith(image_stem):
            return None
        suffix = sidecar_path[len(image_stem) :]
        return suffix or None

    def _unique_destination_file(
        source_path: str,
        destination_dir: str,
        sidecar_suffixes: list[str],
    ) -> str:
        base_name = os.path.basename(source_path)
        stem, ext = os.path.splitext(base_name)
        counter = 0
        while True:
            candidate_name = base_name if counter == 0 else f"{stem}_{counter}{ext}"
            try:
                candidate = resolve_path_within(destination_dir, candidate_name)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Destination path would escape the reference folder.",
                )
            candidate_stem = os.path.splitext(candidate)[0]
            collisions = [candidate]
            collisions.extend(candidate_stem + suffix for suffix in sidecar_suffixes)
            if not any(os.path.exists(path) for path in collisions):
                return candidate
            counter += 1

    def _rewrite_path_under_root(path: str | None, old_root: str, new_root: str):
        if not path:
            return None, False
        if not _is_within_path(path, old_root):
            return path, False
        rel = os.path.relpath(os.path.realpath(os.path.normpath(path)), old_root)
        try:
            return resolve_path_within(new_root, rel), True
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Rewritten path would escape the reference folder.",
            )

    def _validate_relocation_destination(
        session: Session,
        folder_id: int,
        old_root: str,
        destination_folder: str,
    ) -> str:
        if server.running_in_docker():
            raise HTTPException(
                status_code=400,
                detail="Reference folder relocation is not available in Docker mode.",
            )
        new_root = os.path.realpath(os.path.normpath(destination_folder))
        error = validate_reference_folder_path(new_root)
        if error:
            raise HTTPException(status_code=400, detail=error)
        if new_root == old_root:
            raise HTTPException(
                status_code=400,
                detail="Choose a different destination folder.",
            )
        if _is_within_path(new_root, old_root) or _is_within_path(old_root, new_root):
            raise HTTPException(
                status_code=409,
                detail="Destination folder must not be inside or contain the current reference folder.",
            )
        _validate_reference_folder_conflicts(session, new_root, exclude_id=folder_id)
        return new_root

    def _rewrite_reference_folder_picture_paths(
        session: Session,
        folder_id: int,
        old_root: str,
        new_root: str,
    ) -> tuple[int, list[int]]:
        pictures = session.exec(
            select(Picture).where(Picture.reference_folder_id == folder_id)
        ).all()
        rewritten = 0
        moved_picture_ids: list[int] = []
        for pic in pictures:
            path_rewritten = False
            new_path, path_rewritten = _rewrite_path_under_root(
                pic.file_path, old_root, new_root
            )
            new_tags, tags_rewritten = _rewrite_path_under_root(
                pic.tags_file, old_root, new_root
            )
            new_description, description_rewritten = _rewrite_path_under_root(
                pic.description_file, old_root, new_root
            )
            if path_rewritten:
                pic.file_path = new_path
                pic.original_file_name = os.path.basename(new_path)
                rewritten += 1
                if pic.id is not None:
                    moved_picture_ids.append(pic.id)
            if tags_rewritten:
                pic.tags_file = new_tags
                pic.tags_file_mtime = get_sidecar_mtime(new_tags)
            if description_rewritten:
                pic.description_file = new_description
                pic.description_file_mtime = get_sidecar_mtime(new_description)
            if path_rewritten or tags_rewritten or description_rewritten:
                session.add(pic)
        return rewritten, moved_picture_ids

    def _metadata_types(types: list[str]) -> set[str]:
        allowed = {SIDECAR_TYPE_TAGS, SIDECAR_TYPE_DESCRIPTION, "descriptions"}
        requested = {str(t).strip().lower() for t in types or []}
        if not requested or not requested.issubset(allowed):
            raise HTTPException(
                status_code=400,
                detail="types must include tags and/or descriptions.",
            )
        if "descriptions" in requested:
            requested.remove("descriptions")
            requested.add(SIDECAR_TYPE_DESCRIPTION)
        return requested

    def _pictures_for_metadata_scope(
        session: Session,
        folder_id: int,
        root: str,
        scope_path: str,
    ) -> list[Picture]:
        pictures = session.exec(
            select(Picture).where(Picture.reference_folder_id == folder_id)
        ).all()
        scoped = []
        for pic in pictures:
            if not pic.file_path:
                continue
            if not _is_within_path(pic.file_path, root):
                continue
            if _is_within_path(pic.file_path, scope_path):
                scoped.append(pic)
        return scoped

    # -------------------------------------------------------------------------
    # CRUD
    # -------------------------------------------------------------------------

    @router.get(
        "/reference-folders",
        summary="List reference folders",
        description="Returns all reference folders and metadata about the current runtime mode.",
        response_model=ReferenceFoldersListResponse,
        tags=["folders"],
    )
    def list_reference_folders(request: Request):
        server.auth.require_user_id(request)
        if getattr(request.state, "token_scope", None) is not None:
            return ReferenceFoldersListResponse(
                in_docker=server.running_in_docker(),
                has_pending=False,
                image_root=None,
                folders=[],
            )

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

    @router.get(
        "/reference-folders/detect-sidecars",
        summary="Detect sidecar naming convention in a folder",
        description=(
            "Scans a candidate folder for existing tags/description sidecar files "
            "and returns the most common filename suffix found for each type, so "
            "the Add-folder dialog can pre-fill sensible defaults. Returns empty "
            "results when the path is not yet accessible (e.g. an unmounted Docker "
            "volume)."
        ),
        response_model=ReferenceFolderDetectResponse,
        tags=["folders"],
    )
    def detect_reference_folder_sidecars(request: Request, path: str):
        _require_owner_request(request)
        folder = os.path.normpath(path)
        error = validate_reference_folder_path(folder)
        if error:
            raise HTTPException(status_code=400, detail=error)
        if not os.path.isdir(folder):
            # Path not reachable yet (e.g. Docker pending mount) — no detection.
            return ReferenceFolderDetectResponse(
                tags_suffix=None,
                description_suffix=None,
                found_tags=False,
                found_descriptions=False,
            )
        return ReferenceFolderDetectResponse(**detect_folder_suffixes(folder))

    @router.post(
        "/reference-folders",
        summary="Add a reference folder",
        description=(
            "Adds a new reference folder. The path must be absolute and must not "
            "point to a restricted system directory. In Docker mode the folder is "
            "created with status 'pending_mount' and requires a server restart to "
            "mount-verify the path. Outside Docker the path is verified immediately "
            "and the folder becomes 'active' (or 'mount_error') right away."
        ),
        response_model=ReferenceFolderResponse,
        tags=["folders"],
    )
    def create_reference_folder(
        request: Request,
        payload: ReferenceFolderCreateRequest = Body(...),
    ):
        _require_owner_request(request)

        folder = os.path.normpath(payload.folder)
        host_path = _normalize_optional_host_path(payload.host_path)
        error = validate_reference_folder_path(folder)
        if error:
            raise HTTPException(status_code=400, detail=error)

        label = payload.label if payload.label is not None else os.path.basename(folder)

        # Outside Docker we can check accessibility right now; inside Docker the
        # volume may not be mounted yet so we defer to the next server restart.
        in_docker = server.running_in_docker()
        if in_docker and host_path is None:
            raise HTTPException(
                status_code=400,
                detail="Host path is required in Docker mode.",
            )
        if not in_docker:
            access_error = validate_reference_folder_accessible(folder)
            initial_status = (
                ReferenceFolderStatus.ACTIVE
                if access_error is None
                else ReferenceFolderStatus.MOUNT_ERROR
            )
        else:
            initial_status = ReferenceFolderStatus.PENDING_MOUNT

        def insert(session: Session):
            existing = session.exec(
                select(ReferenceFolder).where(ReferenceFolder.folder == folder)
            ).first()
            if existing is not None:
                raise HTTPException(
                    status_code=409,
                    detail="A reference folder with this path already exists.",
                )
            _validate_reference_folder_conflicts(session, folder)
            rf = ReferenceFolder(
                folder=folder,
                host_path=host_path,
                label=label,
                allow_delete_file=False,
                sync_descriptions=bool(payload.sync_descriptions),
                sync_tags=bool(payload.sync_tags),
                description_suffix=_normalize_suffix(payload.description_suffix),
                tags_suffix=_normalize_suffix(payload.tags_suffix),
                status=initial_status,
            )
            session.add(rf)
            session.commit()
            session.refresh(rf)
            return rf

        rf = server.vault.db.run_task(insert, priority=DBPriority.IMMEDIATE)
        logger.info(
            "Reference folder added: %s (label=%r, status=%s)",
            folder,
            label,
            initial_status,
        )
        server.vault.watch_reference_folder(rf.id, rf.folder)
        # For non-Docker activations wake the work planner so the initial scan
        # starts promptly rather than waiting for the next scheduler tick.
        if not in_docker and initial_status == ReferenceFolderStatus.ACTIVE:
            from pixlstash.event_types import EventType

            server.vault.notify(EventType.CHANGED_PICTURES)
        return _to_response(rf)

    @router.patch(
        "/reference-folders/{folder_id}",
        summary="Update a reference folder",
        description="Updates editable fields for a reference folder.",
        response_model=ReferenceFolderResponse,
        tags=["folders"],
    )
    def update_reference_folder(
        folder_id: int,
        request: Request,
        payload: ReferenceFolderUpdateRequest = Body(...),
    ):
        _require_owner_request(request)

        def update(session: Session):
            rf = session.get(ReferenceFolder, folder_id)
            if rf is None:
                raise HTTPException(
                    status_code=404, detail="Reference folder not found."
                )
            relocation_report = None
            old_folder = os.path.normpath(rf.folder)
            new_folder = old_folder
            if "folder" in payload.model_fields_set and payload.folder is not None:
                new_folder = os.path.normpath(payload.folder)
                error = validate_reference_folder_path(new_folder)
                if error:
                    raise HTTPException(status_code=400, detail=error)
                _validate_reference_folder_conflicts(
                    session, new_folder, exclude_id=folder_id
                )
                if new_folder != old_folder:
                    missing: list[dict] = []
                    unmatched: list[dict] = []
                    rewritten = 0
                    pictures = session.exec(
                        select(Picture).where(Picture.reference_folder_id == folder_id)
                    ).all()
                    for pic in pictures:
                        new_path, path_rewritten = _rewrite_path_under_root(
                            pic.file_path, old_folder, new_folder
                        )
                        new_tags, tags_rewritten = _rewrite_path_under_root(
                            pic.tags_file, old_folder, new_folder
                        )
                        new_description, description_rewritten = (
                            _rewrite_path_under_root(
                                pic.description_file, old_folder, new_folder
                            )
                        )
                        if path_rewritten:
                            pic.file_path = new_path
                            pic.original_file_name = os.path.basename(new_path)
                            rewritten += 1
                            if os.path.isfile(new_path):
                                try:
                                    new_sha = ImageUtils.calculate_hash_from_file_path(
                                        new_path
                                    )
                                    if pic.pixel_sha and new_sha != pic.pixel_sha:
                                        unmatched.append(
                                            {
                                                "picture_id": pic.id,
                                                "path": new_path,
                                                "expected_pixel_sha": pic.pixel_sha,
                                                "actual_pixel_sha": new_sha,
                                            }
                                        )
                                    elif not pic.pixel_sha:
                                        pic.pixel_sha = new_sha
                                except Exception as exc:
                                    unmatched.append(
                                        {
                                            "picture_id": pic.id,
                                            "path": new_path,
                                            "error": str(exc),
                                        }
                                    )
                            else:
                                missing.append(
                                    {
                                        "picture_id": pic.id,
                                        "path": new_path,
                                    }
                                )
                        if tags_rewritten:
                            pic.tags_file = new_tags
                            pic.tags_file_mtime = get_sidecar_mtime(new_tags)
                        if description_rewritten:
                            pic.description_file = new_description
                            pic.description_file_mtime = get_sidecar_mtime(
                                new_description
                            )
                        if path_rewritten or tags_rewritten or description_rewritten:
                            session.add(pic)

                    rf.folder = new_folder
                    access_error = (
                        None
                        if server.running_in_docker()
                        else validate_reference_folder_accessible(new_folder)
                    )
                    rf.status = (
                        ReferenceFolderStatus.PENDING_MOUNT
                        if server.running_in_docker()
                        else (
                            ReferenceFolderStatus.ACTIVE
                            if access_error is None
                            else ReferenceFolderStatus.MOUNT_ERROR
                        )
                    )
                    rf.last_scanned = None
                    relocation_report = {
                        "old_folder": old_folder,
                        "new_folder": new_folder,
                        "rewritten_count": rewritten,
                        "missing_count": len(missing),
                        "unmatched_count": len(unmatched),
                        "missing": missing[:100],
                        "unmatched": unmatched[:100],
                    }
            if payload.label is not None:
                rf.label = payload.label
            if payload.allow_delete_file is not None:
                rf.allow_delete_file = payload.allow_delete_file

            # Turning a sync type on schedules a re-scan so existing pictures get
            # their sidecars exported to disk.
            newly_enabled = False
            if payload.sync_descriptions is not None:
                newly_enabled = newly_enabled or (
                    payload.sync_descriptions and not rf.sync_descriptions
                )
                rf.sync_descriptions = payload.sync_descriptions
            if payload.sync_tags is not None:
                newly_enabled = newly_enabled or (
                    payload.sync_tags and not rf.sync_tags
                )
                rf.sync_tags = payload.sync_tags

            if "description_suffix" in payload.model_fields_set:
                rf.description_suffix = _normalize_suffix(payload.description_suffix)
            if "tags_suffix" in payload.model_fields_set:
                rf.tags_suffix = _normalize_suffix(payload.tags_suffix)
            if "host_path" in payload.model_fields_set:
                rf.host_path = _normalize_optional_host_path(payload.host_path)

            if newly_enabled:
                # The scan finder treats last_scanned=None as "scan now".
                rf.last_scanned = None
            session.add(rf)
            session.commit()
            session.refresh(rf)
            return rf, relocation_report

        rf, relocation_report = server.vault.db.run_task(
            update, priority=DBPriority.IMMEDIATE
        )
        if relocation_report is not None:
            server.vault.unwatch_reference_folder(folder_id)
            server.vault.watch_reference_folder(folder_id, rf.folder)
            from pixlstash.event_types import EventType

            server.vault.notify(EventType.CHANGED_PICTURES)
            response = _to_response(rf)
            response.relocation = relocation_report
            return response
        return _to_response(rf)

    @router.post(
        "/reference-folders/{folder_id}/relocate",
        summary="Relocate a reference folder",
        description=(
            "Moves the contents of a reference folder to a different empty "
            "folder and rewrites the existing Picture paths in place. Native "
            "installs only."
        ),
        response_model=RelocateReferenceFolderResponse,
        tags=["folders"],
    )
    def relocate_reference_folder(
        folder_id: int,
        request: Request,
        payload: RelocateReferenceFolderRequest = Body(...),
    ):
        _require_owner_request(request)

        def fetch_and_validate(session: Session):
            rf = session.get(ReferenceFolder, folder_id)
            if rf is None:
                raise HTTPException(
                    status_code=404, detail="Reference folder not found."
                )
            old_root = os.path.realpath(os.path.normpath(rf.folder))
            new_root = _validate_relocation_destination(
                session,
                folder_id,
                old_root,
                payload.destination_folder,
            )
            picture_ids = [
                pic_id
                for pic_id in session.exec(
                    select(Picture.id).where(Picture.reference_folder_id == folder_id)
                ).all()
                if pic_id is not None
            ]
            return old_root, new_root, picture_ids

        old_root, new_root, picture_ids = server.vault.db.run_task(
            fetch_and_validate, priority=DBPriority.IMMEDIATE
        )

        if not os.path.isdir(old_root):
            raise HTTPException(
                status_code=404,
                detail="Current reference folder was not found on disk.",
            )

        destination_existed = os.path.exists(new_root)
        if destination_existed:
            if not os.path.isdir(new_root):
                raise HTTPException(
                    status_code=400,
                    detail="Destination exists but is not a folder.",
                )
            if os.listdir(new_root):
                raise HTTPException(
                    status_code=409,
                    detail="Destination folder must be empty.",
                )
        else:
            try:
                os.makedirs(new_root, exist_ok=False)
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Could not create destination folder: {exc}",
                )

        rollback_moves: list[tuple[str, str]] = []
        moved_entry_count = 0
        try:
            for name in os.listdir(old_root):
                try:
                    source = resolve_path_within(old_root, name)
                    destination = resolve_path_within(new_root, name)
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail="Relocation path would escape the reference folder.",
                    )
                if os.path.exists(destination):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Destination already contains: {name}",
                    )
                shutil.move(source, destination)
                rollback_moves.append((destination, source))
                moved_entry_count += 1

            def apply_relocation(session: Session):
                rf = session.get(ReferenceFolder, folder_id)
                if rf is None:
                    raise HTTPException(
                        status_code=404, detail="Reference folder not found."
                    )
                rewritten, moved_ids = _rewrite_reference_folder_picture_paths(
                    session,
                    folder_id,
                    old_root,
                    new_root,
                )
                rf.folder = new_root
                rf.status = ReferenceFolderStatus.ACTIVE
                rf.last_scanned = None
                session.add(rf)
                session.commit()
                return rewritten, moved_ids

            rewritten_count, moved_picture_ids = server.vault.db.run_task(
                apply_relocation, priority=DBPriority.IMMEDIATE
            )
        except HTTPException:
            for destination, source in reversed(rollback_moves):
                if os.path.exists(destination):
                    try:
                        shutil.move(destination, source)
                    except Exception as exc:
                        logger.warning(
                            "Failed to roll back relocation move %s: %s",
                            destination,
                            exc,
                        )
            if not destination_existed and os.path.isdir(new_root):
                try:
                    os.rmdir(new_root)
                except OSError:
                    pass
            raise
        except Exception as exc:
            for destination, source in reversed(rollback_moves):
                if os.path.exists(destination):
                    try:
                        shutil.move(destination, source)
                    except Exception as rollback_exc:
                        logger.warning(
                            "Failed to roll back relocation move %s: %s",
                            destination,
                            rollback_exc,
                        )
            if not destination_existed and os.path.isdir(new_root):
                try:
                    os.rmdir(new_root)
                except OSError:
                    pass
            raise HTTPException(status_code=500, detail=f"Relocation failed: {exc}")

        try:
            os.rmdir(old_root)
        except OSError:
            pass

        server.vault.unwatch_reference_folder(folder_id)
        server.vault.watch_reference_folder(folder_id, new_root)
        from pixlstash.event_types import EventType

        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": moved_picture_ids or picture_ids,
                "change_kind": "updated",
                "fields": ["file_path", "reference_folder_id"],
                "source": "ui",
                "origin_client_id": getattr(request.state, "origin_client_id", None),
            },
        )
        return RelocateReferenceFolderResponse(
            status="success",
            id=folder_id,
            old_folder=old_root,
            new_folder=new_root,
            moved_entry_count=moved_entry_count,
            rewritten_count=rewritten_count,
            moved_picture_ids=moved_picture_ids,
        )

    @router.post(
        "/reference-folders/{folder_id}/move-pictures",
        summary="Move reference-folder pictures",
        description=(
            "Moves existing reference-folder image files into this reference "
            "folder (or one of its subfolders) and updates the existing Picture "
            "rows in place."
        ),
        response_model=MoveReferencePicturesResponse,
        tags=["folders"],
    )
    def move_reference_folder_pictures(
        folder_id: int,
        request: Request,
        payload: MoveReferencePicturesRequest = Body(...),
    ):
        _require_owner_request(request)
        if not payload.picture_ids:
            raise HTTPException(
                status_code=400, detail="picture_ids must be a non-empty list."
            )

        def fetch_destination(session: Session):
            rf = session.get(ReferenceFolder, folder_id)
            if rf is None:
                raise HTTPException(
                    status_code=404, detail="Reference folder not found."
                )
            if rf.status != ReferenceFolderStatus.ACTIVE:
                raise HTTPException(
                    status_code=400,
                    detail="Destination reference folder is not active.",
                )
            return rf.folder

        destination_root = server.vault.db.run_task(
            fetch_destination, priority=DBPriority.IMMEDIATE
        )
        destination_dir = _validate_destination_dir(
            destination_root, payload.destination_subpath
        )

        def fetch_picture_records(session: Session):
            ids = [int(pid) for pid in payload.picture_ids]
            pictures = session.exec(select(Picture).where(Picture.id.in_(ids))).all()
            by_id = {pic.id: pic for pic in pictures}
            folders = {
                rf.id: rf
                for rf in session.exec(select(ReferenceFolder)).all()
                if rf.id is not None
            }
            failures = []
            records = []
            for pic_id in ids:
                pic = by_id.get(pic_id)
                if pic is None:
                    failures.append(
                        {"picture_id": pic_id, "reason": "picture_not_found"}
                    )
                    continue
                if not pic.reference_folder_id:
                    failures.append(
                        {"picture_id": pic_id, "reason": "not_reference_picture"}
                    )
                    continue
                source_folder = folders.get(pic.reference_folder_id)
                if source_folder is None:
                    failures.append(
                        {
                            "picture_id": pic_id,
                            "reason": "source_reference_folder_not_found",
                        }
                    )
                    continue
                if not pic.file_path or not os.path.isfile(pic.file_path):
                    failures.append(
                        {"picture_id": pic_id, "reason": "source_file_missing"}
                    )
                    continue
                if not _is_within_path(pic.file_path, source_folder.folder):
                    failures.append(
                        {"picture_id": pic_id, "reason": "source_path_outside_root"}
                    )
                    continue
                records.append(
                    {
                        "picture_id": pic.id,
                        "source_path": pic.file_path,
                        "source_folder": source_folder.folder,
                        "tags_file": pic.tags_file,
                        "description_file": pic.description_file,
                    }
                )
            return records, failures

        records, failures = server.vault.db.run_task(
            fetch_picture_records, priority=DBPriority.IMMEDIATE
        )
        if failures:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Some pictures cannot be moved.",
                    "failures": failures,
                },
            )

        moved: list[dict] = []
        rollback_moves: list[tuple[str, str]] = []
        try:
            for record in records:
                source_path = record["source_path"]
                source_sidecars = []
                sidecar_suffixes = []
                for key in ("tags_file", "description_file"):
                    sidecar = record.get(key)
                    if not sidecar or not os.path.isfile(sidecar):
                        continue
                    if not _is_within_path(sidecar, record["source_folder"]):
                        continue
                    suffix = _sidecar_suffix_for_move(source_path, sidecar)
                    if not suffix:
                        continue
                    source_sidecars.append((key, sidecar, suffix))
                    sidecar_suffixes.append(suffix)

                destination_path = _unique_destination_file(
                    source_path, destination_dir, sidecar_suffixes
                )
                os.makedirs(os.path.dirname(destination_path), exist_ok=True)
                shutil.move(source_path, destination_path)
                rollback_moves.append((destination_path, source_path))

                moved_sidecars = {}
                destination_stem = os.path.splitext(destination_path)[0]
                for key, sidecar, suffix in source_sidecars:
                    destination_sidecar = destination_stem + suffix
                    if os.path.exists(destination_sidecar):
                        destination_sidecar = _unique_destination_file(
                            sidecar,
                            os.path.dirname(destination_path),
                            [],
                        )
                    shutil.move(sidecar, destination_sidecar)
                    rollback_moves.append((destination_sidecar, sidecar))
                    moved_sidecars[key] = destination_sidecar

                try:
                    pixel_sha = ImageUtils.calculate_hash_from_file_path(
                        destination_path
                    )
                except Exception:
                    pixel_sha = None
                moved.append(
                    {
                        "picture_id": record["picture_id"],
                        "file_path": destination_path,
                        "pixel_sha": pixel_sha,
                        "tags_file": moved_sidecars.get("tags_file"),
                        "description_file": moved_sidecars.get("description_file"),
                    }
                )

            def apply_moves(session: Session, move_records: list[dict]):
                destination_rf = session.get(ReferenceFolder, folder_id)
                if destination_rf is None:
                    raise HTTPException(
                        status_code=404, detail="Reference folder not found."
                    )
                source_folder_ids = set()
                moved_ids = []
                for move in move_records:
                    pic = session.get(Picture, move["picture_id"])
                    if pic is None:
                        continue
                    if pic.reference_folder_id is not None:
                        source_folder_ids.add(pic.reference_folder_id)
                    if pic.pixel_sha and move.get("pixel_sha"):
                        if pic.pixel_sha != move["pixel_sha"]:
                            raise HTTPException(
                                status_code=409,
                                detail={
                                    "message": "Moved file hash did not match the picture identity.",
                                    "picture_id": pic.id,
                                },
                            )
                    elif move.get("pixel_sha"):
                        pic.pixel_sha = move["pixel_sha"]
                    pic.file_path = move["file_path"]
                    pic.reference_folder_id = folder_id
                    pic.original_file_name = os.path.basename(move["file_path"])
                    pic.tags_file = move.get("tags_file")
                    pic.tags_file_mtime = (
                        get_sidecar_mtime(pic.tags_file) if pic.tags_file else None
                    )
                    pic.description_file = move.get("description_file")
                    pic.description_file_mtime = (
                        get_sidecar_mtime(pic.description_file)
                        if pic.description_file
                        else None
                    )
                    session.add(pic)
                    moved_ids.append(pic.id)
                destination_rf.last_scanned = None
                session.add(destination_rf)
                for source_folder_id in source_folder_ids:
                    if source_folder_id == folder_id:
                        continue
                    source_rf = session.get(ReferenceFolder, source_folder_id)
                    if source_rf is not None:
                        source_rf.last_scanned = None
                        session.add(source_rf)
                session.commit()
                return moved_ids

            moved_ids = server.vault.db.run_task(
                apply_moves, moved, priority=DBPriority.IMMEDIATE
            )
        except HTTPException:
            for destination, source in reversed(rollback_moves):
                if os.path.exists(destination):
                    try:
                        shutil.move(destination, source)
                    except Exception as exc:
                        logger.warning(
                            "Failed to roll back move %s: %s",
                            destination,
                            exc,
                        )
            raise
        except Exception as exc:
            for destination, source in reversed(rollback_moves):
                if os.path.exists(destination):
                    try:
                        shutil.move(destination, source)
                    except Exception as rollback_exc:
                        logger.warning(
                            "Failed to roll back move %s: %s",
                            destination,
                            rollback_exc,
                        )
            raise HTTPException(status_code=500, detail=f"Move failed: {exc}")

        from pixlstash.event_types import EventType

        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": moved_ids,
                "change_kind": "updated",
                "fields": ["file_path", "reference_folder_id"],
                "source": "ui",
                "origin_client_id": getattr(request.state, "origin_client_id", None),
            },
        )
        return MoveReferencePicturesResponse(
            status="success",
            moved_count=len(moved_ids),
            moved_picture_ids=moved_ids,
            failures=[],
        )

    @router.post(
        "/reference-folders/{folder_id}/metadata/export",
        summary="Export reference folder TXT metadata",
        description="Writes tags and/or descriptions for indexed pictures to TXT sidecars.",
        response_model=ReferenceFolderMetadataResponse,
        tags=["folders"],
    )
    def export_reference_folder_metadata(
        folder_id: int,
        request: Request,
        payload: ReferenceFolderMetadataRequest = Body(...),
    ):
        _require_owner_request(request)
        requested_types = _metadata_types(payload.types)

        def export_metadata(session: Session):
            rf = session.get(ReferenceFolder, folder_id)
            if rf is None:
                raise HTTPException(
                    status_code=404, detail="Reference folder not found."
                )
            scope = _resolve_scope_path(rf.folder, payload.scope_path)
            pictures = _pictures_for_metadata_scope(
                session, folder_id, rf.folder, scope
            )
            tag_rows = session.exec(
                select(Tag.picture_id, Tag.tag).where(
                    Tag.picture_id.in_([p.id for p in pictures if p.id is not None])
                )
            ).all()
            tags_by_picture: dict[int, list[str]] = {}
            for picture_id, tag in tag_rows:
                if tag and not is_tag_sentinel(tag):
                    tags_by_picture.setdefault(picture_id, []).append(tag)

            tags_count = 0
            descriptions_count = 0
            skipped_count = 0
            for pic in pictures:
                if not pic.file_path or not os.path.isfile(pic.file_path):
                    skipped_count += 1
                    continue
                dirty = False
                if SIDECAR_TYPE_TAGS in requested_types:
                    tags = tags_by_picture.get(pic.id, [])
                    if tags or pic.tags_file:
                        target = writeback_path(
                            pic.file_path,
                            SIDECAR_TYPE_TAGS,
                            rf.tags_suffix,
                            pic.tags_file,
                        )
                        mtime = write_sidecar(target, ", ".join(tags))
                        if mtime is not None:
                            pic.tags_file = target
                            pic.tags_file_mtime = mtime
                            tags_count += 1
                            dirty = True
                if SIDECAR_TYPE_DESCRIPTION in requested_types:
                    description = (pic.description or "").strip()
                    if description or pic.description_file:
                        target = writeback_path(
                            pic.file_path,
                            SIDECAR_TYPE_DESCRIPTION,
                            rf.description_suffix,
                            pic.description_file,
                        )
                        mtime = write_sidecar(target, description)
                        if mtime is not None:
                            pic.description_file = target
                            pic.description_file_mtime = mtime
                            descriptions_count += 1
                            dirty = True
                if dirty:
                    session.add(pic)
            session.commit()
            return scope, tags_count, descriptions_count, skipped_count

        scope, tags_count, descriptions_count, skipped_count = server.vault.db.run_task(
            export_metadata, priority=DBPriority.IMMEDIATE
        )
        return ReferenceFolderMetadataResponse(
            status="success",
            scope_path=scope,
            types=sorted(requested_types),
            tags_count=tags_count,
            descriptions_count=descriptions_count,
            skipped_count=skipped_count,
        )

    @router.post(
        "/reference-folders/{folder_id}/metadata/import",
        summary="Import reference folder TXT metadata",
        description="Reads tags and/or descriptions from TXT sidecars for indexed pictures.",
        response_model=ReferenceFolderMetadataResponse,
        tags=["folders"],
    )
    def import_reference_folder_metadata(
        folder_id: int,
        request: Request,
        payload: ReferenceFolderMetadataRequest = Body(...),
    ):
        _require_owner_request(request)
        requested_types = _metadata_types(payload.types)

        def import_metadata(session: Session):
            rf = session.get(ReferenceFolder, folder_id)
            if rf is None:
                raise HTTPException(
                    status_code=404, detail="Reference folder not found."
                )
            scope = _resolve_scope_path(rf.folder, payload.scope_path)
            pictures = _pictures_for_metadata_scope(
                session, folder_id, rf.folder, scope
            )
            tags_count = 0
            descriptions_count = 0
            skipped_count = 0
            for pic in pictures:
                if not pic.file_path or not os.path.isfile(pic.file_path):
                    skipped_count += 1
                    continue
                dirty = False
                if SIDECAR_TYPE_TAGS in requested_types:
                    tags_path = resolve_typed_sidecar(
                        pic.file_path, SIDECAR_TYPE_TAGS, rf.tags_suffix
                    )
                    if tags_path:
                        tags = read_tags_sidecar(tags_path)
                        session.exec(delete(Tag).where(Tag.picture_id == pic.id))
                        if tags:
                            session.add_all(
                                [Tag(picture_id=pic.id, tag=tag) for tag in tags]
                            )
                        else:
                            session.add(
                                Tag(picture_id=pic.id, tag=TAG_PENDING_SENTINEL)
                            )
                        pic.tags_file = tags_path
                        pic.tags_file_mtime = get_sidecar_mtime(tags_path)
                        tags_count += 1
                        dirty = True
                if SIDECAR_TYPE_DESCRIPTION in requested_types:
                    description_path = resolve_typed_sidecar(
                        pic.file_path,
                        SIDECAR_TYPE_DESCRIPTION,
                        rf.description_suffix,
                    )
                    if description_path:
                        pic.description = read_description_sidecar(description_path)
                        pic.description_file = description_path
                        pic.description_file_mtime = get_sidecar_mtime(description_path)
                        descriptions_count += 1
                        dirty = True
                if dirty:
                    session.add(pic)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=f"Could not import metadata: {exc}",
                )
            return scope, tags_count, descriptions_count, skipped_count

        scope, tags_count, descriptions_count, skipped_count = server.vault.db.run_task(
            import_metadata, priority=DBPriority.IMMEDIATE
        )
        from pixlstash.event_types import EventType

        server.vault.notify(EventType.CHANGED_PICTURES)
        return ReferenceFolderMetadataResponse(
            status="success",
            scope_path=scope,
            types=sorted(requested_types),
            tags_count=tags_count,
            descriptions_count=descriptions_count,
            skipped_count=skipped_count,
        )

    @router.delete(
        "/reference-folders/{folder_id}",
        summary="Remove a reference folder",
        description=(
            "Removes a reference folder record. Pictures indexed from this folder "
            "are de-associated but not deleted from the database or disk."
        ),
        tags=["folders"],
        response_model=ReferenceFolderDeleteResponse,
    )
    def delete_reference_folder(folder_id: int, request: Request):
        _require_owner_request(request)

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
            file_paths = [p.file_path for p in pictures if p.file_path]
            for pic in pictures:
                session.delete(pic)
            session.delete(rf)
            session.commit()
            logger.info(
                "Reference folder removed: %s (%d pictures deleted)",
                folder_path,
                len(pictures),
            )
            return folder_path, file_paths

        folder_path, file_paths = server.vault.db.run_task(
            remove, priority=DBPriority.IMMEDIATE
        )
        logger.info("Reference folder removed: %s", folder_path)
        server.vault.unwatch_reference_folder(folder_id)

        # Remove orphaned thumbnails generated for this folder's pictures.
        image_root = getattr(server.vault, "image_root", None)
        if image_root:
            removed_thumbs = 0
            for rel_path in file_paths:
                thumb_path = ImageUtils.get_thumbnail_path(image_root, rel_path)
                if thumb_path and os.path.isfile(thumb_path):
                    try:
                        os.remove(thumb_path)
                        removed_thumbs += 1
                    except Exception as exc:
                        logger.warning(
                            "Failed to delete orphan thumbnail %s: %s", thumb_path, exc
                        )
            if removed_thumbs:
                logger.info(
                    "Removed %d orphan thumbnails for reference folder: %s",
                    removed_thumbs,
                    folder_path,
                )

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
        tags=["server"],
        response_model=ServerRestartResponse,
    )
    def restart_server(request: Request):
        _require_owner_request(request)
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
        tags=["folders"],
        response_model=ReferenceFolderOpenResponse,
    )
    def open_reference_folder(
        folder_id: int,
        request: Request,
        subpath: Optional[str] = None,
    ):
        _require_owner_request(request)

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
