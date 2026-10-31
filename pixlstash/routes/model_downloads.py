"""Download a model a workflow is missing onto the shelf, and cancel one.

``POST /model-downloads`` starts the fetch and answers at once; the download
itself runs in :mod:`pixlstash.services.model_downloads`, and its progress is
``downloads`` on ``GET /workers/progress``, where the Tasks tab draws it.
``POST /model-downloads/{download_id}/cancel`` stops one and discards what it
had fetched.

Authorization is declared in ``pixlstash/authz/registry.py``: the start writes
a new file into a registered host folder, ``POST /model-files``' class, so it
carries the §16.3 locality tier; the cancel writes nothing and is owner-only.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.event_types import EventType
from pixlstash.routes.comfyui import _comfyui_url, _read_object_info
from pixlstash.services.model_downloads import (
    DownloadRefused,
    cancel_download,
    start_download,
)


class ModelDownloadRequest(BaseModel):
    """Body of ``POST /model-downloads``."""

    model_config = ConfigDict(extra="forbid")

    file: str = Field(
        description=(
            "The model name the workflow asks for, as its loader spells it "
            "(`missing_models[].file`, one marked `downloadable`), subfolders "
            "included. The link is looked up by the server, never sent."
        )
    )


class ModelDownloadResponse(BaseModel):
    """One download, as ``GET /workers/progress`` lists it under ``downloads``."""

    model_config = ConfigDict(extra="allow")

    id: str
    file: str
    host: str = Field(description="The host the file is fetched from.")
    folder_path: str = Field(description="The model folder it goes into.")
    status: str = Field(description="`running`, `done`, `failed` or `cancelled`.")
    received: int = 0
    total: int = Field(default=0, description="0 while the size is not known.")
    percent: float = 0
    error: Optional[str] = None
    model_id: Optional[int] = None
    sha256: Optional[str] = Field(
        default=None,
        description=(
            "The digest the template list records for its link, None where it "
            "has none: a set can hold the file by it before it lands."
        ),
    )
    directory: Optional[str] = Field(
        default=None, description="The ComfyUI models folder it is for."
    )
    slot: str = Field(default="other", description="The set slot it would fill.")


class ModelDownloadCancelResponse(BaseModel):
    """Body of the cancel's answer."""

    cancelled: bool


def create_router(server) -> APIRouter:
    """Create the model-download router.

    Args:
        server: The Server instance, for ``hub``, ``vault`` and ``auth``.

    Returns:
        The configured router.
    """
    router = APIRouter()

    def _arrived(model_id: int) -> None:
        server.vault.notify(EventType.MODELS_ARRIVED, {"model_ids": [model_id]})

    @router.post(
        "/model-downloads",
        summary="Download a missing model onto the shelf",
        description=(
            "Starts fetching a model a workflow names and this machine lacks, "
            "from the link ComfyUI's template list gives for that file name "
            "(the list PixlStash ships, else the templates the owner's ComfyUI "
            "serves, taken only for an https huggingface.co link to a "
            ".safetensors or .gguf file), "
            "into the download folder (the one marked in Model folders, else "
            "the default folder) and answers at once. When it is complete it "
            "is registered on the shelf and `models_arrived` is announced. "
            "Refused before any byte is fetched when no link is known for the "
            "name (404), the name is not a model file, or the file is already "
            "there or already being downloaded (409)."
        ),
        tags=["model_shelf"],
        response_model=ModelDownloadResponse,
    )
    def create_model_download(
        request: Request, payload: ModelDownloadRequest = Body(...)
    ):
        server.auth.ensure_secure_when_required(request)
        comfyui_url = _comfyui_url(server.auth.get_user_for_request(request))
        try:
            return start_download(
                server.hub,
                payload.file,
                on_arrived=_arrived,
                comfyui_url=comfyui_url,
                object_info=_read_object_info(comfyui_url, cached=True)[0],
            )
        except DownloadRefused as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.post(
        "/model-downloads/{download_id}/cancel",
        summary="Cancel a model download",
        description=(
            "Stops a running download and discards what it had fetched. "
            "`cancelled: false` when no such download is running."
        ),
        tags=["model_shelf"],
        response_model=ModelDownloadCancelResponse,
    )
    def cancel_model_download(download_id: str, request: Request):
        server.auth.ensure_secure_when_required(request)
        return ModelDownloadCancelResponse(cancelled=cancel_download(download_id))

    return router
