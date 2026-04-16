"""Filesystem browsing API — server-side directory picker for native installs."""

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.utils.reference_folder_validator import validate_reference_folder_path

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


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/filesystem/browse",
        summary="Browse server filesystem",
        description=(
            "Returns immediate child entries of the requested directory. "
            "Available in native (non-Docker) installs only. "
            "Requires authentication."
        ),
        response_model=FilesystemBrowseResponse,
        tags=["config"],
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
        server.auth.require_user_id(request)

        if server.running_in_docker():
            raise HTTPException(
                status_code=403,
                detail="Filesystem browsing is not available in Docker mode.",
            )

        browse_path = path or os.path.expanduser("~")

        validation_error = validate_reference_folder_path(browse_path)
        if validation_error:
            raise HTTPException(status_code=400, detail=validation_error)

        # Canonicalize through root-anchored path safety checks.
        try:
            resolved = os.path.realpath(os.path.normpath(browse_path))
            if os.name == "nt":
                drive, tail = os.path.splitdrive(resolved)
                if not drive:
                    raise ValueError("Windows path must include a drive root")
                safe_base = os.path.realpath(drive + os.sep)
                relative = tail.lstrip("\\/")
            else:
                safe_base = os.path.realpath(os.path.abspath(os.sep))
                relative = resolved.lstrip(os.sep)

            candidate = os.path.realpath(os.path.join(safe_base, relative))
            if candidate != safe_base and not candidate.startswith(
                safe_base + os.sep,
            ):
                raise ValueError("Resolved path escapes allowed root")
            browse_path = candidate
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        if not os.path.isdir(browse_path):
            raise HTTPException(status_code=404, detail="Directory not found.")

        try:
            raw_entries = os.scandir(browse_path)
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
                safe_entry_path = os.path.realpath(
                    os.path.join(browse_path, entry.name)
                )
                if safe_entry_path != browse_path and not safe_entry_path.startswith(
                    browse_path + os.sep,
                ):
                    continue
            except OSError:
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

        parent = str(os.path.dirname(browse_path)) if browse_path != "/" else None

        return FilesystemBrowseResponse(
            path=browse_path,
            parent=parent,
            image_count=image_count,
            entries=entries,
        )

    return router
