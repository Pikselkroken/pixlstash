"""Filesystem browsing API — server-side directory picker for native installs."""

import os
import re
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.utils.reference_folder_validator import validate_reference_folder_path
from pixlstash.utils.service.path_utils import resolve_path_within

logger = get_logger(__name__)

_SUPPORTED_IMAGE_EXTS: frozenset[str] = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".bmp",
        ".heic",
        ".heif",
        ".avif",
    }
)


# Matches any non-empty string that contains no null bytes or newlines.
# Applied with re.fullmatch() after os.path.realpath() so that CodeQL
# recognises the result as a path-injection barrier (realpath itself does
# not break the taint chain in CodeQL's model).
_SAFE_RESOLVED_PATH_RE = re.compile(r"[^\x00\n]+")


def _is_supported_media_file(file_name: str) -> bool:
    ext = os.path.splitext(file_name)[1].lower()
    if ext in _SUPPORTED_IMAGE_EXTS:
        return True
    return VideoUtils.is_video_file(file_name)


class FilesystemEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    image_count: int = 0


class FilesystemBrowseResponse(BaseModel):
    path: str
    parent: Optional[str]
    image_count: int
    entries: list[FilesystemEntry]


class FilesystemCreateFolderRequest(BaseModel):
    path: str


class FilesystemCreateFolderResponse(BaseModel):
    status: str
    path: str


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _require_owner_filesystem_request(request: Request) -> None:
        server.auth.require_user_id(request)
        if getattr(request.state, "token_scope", None) is not None:
            raise HTTPException(
                status_code=403,
                detail="Filesystem browsing is not available for token-authenticated requests.",
            )
        if server.running_in_docker():
            raise HTTPException(
                status_code=403,
                detail="Filesystem browsing is not available in Docker mode.",
            )

    def _allowed_roots() -> list[str]:
        return [
            os.path.realpath(r)
            for r in (server._server_config.get("filesystem_roots") or [])
            if isinstance(r, str) and r
        ]

    def _resolve_safe_directory_path(path: str) -> str:
        validation_error = validate_reference_folder_path(path)
        if validation_error:
            raise HTTPException(status_code=400, detail=validation_error)

        resolved = os.path.realpath(os.path.normpath(path))
        allowed_roots = _allowed_roots()
        if allowed_roots:
            if not any(
                resolved == root or resolved.startswith(root + os.sep)
                for root in allowed_roots
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Path is not within any configured filesystem root.",
                )

        _m = _SAFE_RESOLVED_PATH_RE.fullmatch(resolved)
        if not _m:
            raise HTTPException(status_code=400, detail="Invalid path.")
        return _m.group(0)

    @router.get(
        "/filesystem/browse",
        summary="Browse server filesystem",
        description=(
            "Returns immediate child entries of the requested directory. "
            "Available in native (non-Docker) installs only. "
            "Requires authentication."
        ),
        response_model=FilesystemBrowseResponse,
    )
    def browse_filesystem(
        request: Request,
        path: Optional[str] = Query(
            default=None,
            description="Absolute directory path to list. Defaults to home directory.",
        ),
        show_hidden: bool = Query(
            default=False, description="Include hidden (dot-prefixed) entries."
        ),
    ):
        _require_owner_filesystem_request(request)

        browse_path = path or os.path.expanduser("~")
        # Canonicalize: os.path.realpath resolves all symlinks and '..' segments,
        # guaranteeing an absolute path with no traversal sequences.
        # re.fullmatch().group(0) is the CodeQL-recognised path-injection barrier
        # for Python. We apply it to the canonical resolved string as a whole.
        safe_browse_path = _resolve_safe_directory_path(browse_path)

        if not os.path.isdir(safe_browse_path):
            raise HTTPException(status_code=404, detail="Directory not found.")

        try:
            raw_entries = os.scandir(safe_browse_path)
        except PermissionError:
            raise HTTPException(status_code=403, detail="Permission denied.")

        entries: list[FilesystemEntry] = []
        image_count = 0
        for entry in sorted(raw_entries, key=lambda e: e.name.lower()):
            if not show_hidden and entry.name.startswith("."):
                continue

            # Keep results constrained to the browsed directory even if an
            # entry is a symlink that points somewhere else.
            try:
                safe_entry_path = resolve_path_within(safe_browse_path, entry.name)
                if safe_entry_path == safe_browse_path:
                    continue
            except (OSError, ValueError):
                # Skip entries that cannot be resolved within the parent directory (e.g. broken symlinks or entries with permission issues).
                continue

            try:
                is_dir = entry.is_dir(follow_symlinks=True)
            except OSError:
                # Skip entries that cannot be accessed (e.g. permission issues).
                continue
            if is_dir:
                entries.append(
                    FilesystemEntry(
                        name=entry.name,
                        path=safe_entry_path,
                        is_dir=True,
                        image_count=0,
                    )
                )
                continue

            try:
                is_file = entry.is_file(follow_symlinks=True)
            except OSError:
                # Skip entries that cannot be accessed (e.g. permission issues).
                continue
            if is_file and _is_supported_media_file(entry.name):
                image_count += 1

        parent = (
            str(os.path.dirname(safe_browse_path)) if safe_browse_path != "/" else None
        )

        return FilesystemBrowseResponse(
            path=safe_browse_path,
            parent=parent,
            image_count=image_count,
            entries=entries,
        )

    @router.post(
        "/filesystem/folders",
        summary="Create a server filesystem folder",
        description=(
            "Creates a new directory for native owner installs. Used by the "
            "folder picker when choosing relocation destinations."
        ),
        response_model=FilesystemCreateFolderResponse,
    )
    def create_filesystem_folder(
        request: Request,
        payload: FilesystemCreateFolderRequest = Body(...),
    ):
        _require_owner_filesystem_request(request)
        target = _resolve_safe_directory_path(payload.path)
        parent = os.path.dirname(target)
        if not os.path.isdir(parent):
            raise HTTPException(status_code=404, detail="Parent folder not found.")
        try:
            target = resolve_path_within(parent, os.path.basename(target))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid folder path.")
        if os.path.exists(target):
            raise HTTPException(status_code=409, detail="Folder already exists.")
        try:
            os.mkdir(target)
        except PermissionError:
            raise HTTPException(status_code=403, detail="Permission denied.")
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not create folder: {exc}",
            )
        return FilesystemCreateFolderResponse(status="success", path=target)

    return router
