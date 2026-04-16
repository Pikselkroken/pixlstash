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


def _count_supported_media_files(path: str, show_hidden: bool) -> int:
    count = 0
    try:
        with os.scandir(path) as child_entries:
            for child in child_entries:
                if not show_hidden and child.name.startswith("."):
                    continue
                try:
                    if not child.is_file(follow_symlinks=True):
                        continue
                except OSError:
                    continue
                if _is_supported_media_file(child.name):
                    count += 1
    except (PermissionError, FileNotFoundError, OSError):
        return 0
    return count


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
            try:
                is_dir = entry.is_dir(follow_symlinks=True)
            except OSError:
                continue
            if is_dir:
                child_image_count = _count_supported_media_files(
                    entry.path,
                    show_hidden,
                )
                entries.append(
                    FilesystemEntry(
                        name=entry.name,
                        path=entry.path,
                        is_dir=True,
                        image_count=child_image_count,
                    )
                )
                continue

            try:
                is_file = entry.is_file(follow_symlinks=True)
            except OSError:
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
