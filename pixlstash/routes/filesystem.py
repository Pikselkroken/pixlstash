"""Filesystem browsing API — server-side directory picker for native installs."""

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.reference_folder_validator import validate_reference_folder_path

logger = get_logger(__name__)


class FilesystemEntry(BaseModel):
    name: str
    path: str
    is_dir: bool


class FilesystemBrowseResponse(BaseModel):
    path: str
    parent: Optional[str]
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
        for entry in sorted(raw_entries, key=lambda e: e.name.lower()):
            if not show_hidden and entry.name.startswith("."):
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=True)
            except OSError:
                continue
            if not is_dir:
                continue
            entries.append(
                FilesystemEntry(name=entry.name, path=entry.path, is_dir=True)
            )

        parent = str(os.path.dirname(browse_path)) if browse_path != "/" else None

        return FilesystemBrowseResponse(
            path=browse_path,
            parent=parent,
            entries=entries,
        )

    return router
