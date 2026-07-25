import asyncio
import json
import os
import threading
import uuid
from copy import deepcopy
from urllib.parse import quote

import websockets
from fastapi import APIRouter, Body, HTTPException, Request, WebSocket
from fastapi.websockets import WebSocketDisconnect
from pydantic import BaseModel, ConfigDict
from sqlmodel import select

from typing import Optional

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Picture,
    User,
)
from pixlstash.utils.comfyui_utilities import extract_comfy_workflow_info
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.path_utils import resolve_path_within
from pixlstash.stacking import (
    build_stack_filename_prefix,
    get_or_create_stack_for_picture,
)
from platformdirs import user_data_dir

# ComfyUI workflow-execution orchestration and the output-import pipeline live in
# the service layer (backend refactor Phase 2 §4.5); the route handlers below
# stay thin and delegate to it. See pixlstash/services/comfyui_service.py.
from pixlstash.services.comfyui_service import (
    _apply_filename_prefix,
    _apply_fixed_seed,
    _comfyui_abort,
    _extract_output_node_ids,
    _process_comfyui_outputs,
    _randomize_seeds,
    _replace_placeholders,
    _submit_comfyui_prompt,
    _upload_image_to_comfyui,
)

# Re-exported so existing call sites and tests that import these helpers from
# this module keep resolving after the move into services/comfyui_service.py.
from pixlstash.services.comfyui_service import (  # noqa: F401
    _assign_outputs_to_stack_top,
    _assign_pictures_to_view_context,
    _copy_face_assignments,
    _copy_set_and_project_assignments,
    _download_comfyui_image,
    _emit_comfyui_failure_progress,
    _extract_comfyui_output_images,
    _extract_history_entry,
    _extract_history_status_and_error,
    _extract_text_from_value,
    _fetch_comfyui_history,
    _import_comfyui_outputs,
    _set_source_picture_id_on_pictures,
    _unique_edit_filename,
    _wait_for_comfyui_outputs,
)

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

PLACEHOLDER_IMAGE = "{{image_path}}"
PLACEHOLDER_CAPTION = "{{caption}}"
DEFAULT_COMFYUI_URL = "http://127.0.0.1:8188/"


def _workflow_builtin_dir() -> str:
    return os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "..", "data", "comfyui-workflows", "built-in"
        )
    )


def _workflow_user_dir() -> str:
    return os.path.join(user_data_dir("pixlstash"), "comfyui-workflows", "user")


def _workflow_dirs() -> list[tuple[str, str]]:
    return [
        ("user", _workflow_user_dir()),
        ("built-in", _workflow_builtin_dir()),
    ]


def _resolve_workflow_path(name: str) -> tuple[str | None, str | None]:
    normalized = _normalize_workflow_name(name)
    if not normalized:
        return None, None
    for source, folder in _workflow_dirs():
        try:
            path = resolve_path_within(folder, normalized)
        except ValueError:
            return None, None
        if os.path.isfile(path):
            return path, source
    return None, None


def _normalize_workflow_name(name: str) -> str:
    safe = os.path.basename(name or "").strip()
    if not safe:
        return ""
    if not safe.lower().endswith(".json"):
        safe = f"{safe}.json"
    return safe


def _load_workflow_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_workflow_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)


def _find_placeholder_usage(payload: dict) -> tuple[bool, list[str]]:
    dump = json.dumps(payload, ensure_ascii=False)
    missing = []
    if PLACEHOLDER_IMAGE not in dump:
        missing.append(PLACEHOLDER_IMAGE)
    if PLACEHOLDER_CAPTION not in dump:
        missing.append(PLACEHOLDER_CAPTION)
    is_t2i = PLACEHOLDER_IMAGE in missing
    if is_t2i:
        # t2i workflow: valid when the caption placeholder is present
        valid = PLACEHOLDER_CAPTION not in missing
    else:
        # i2i workflow: valid as long as image placeholder is present;
        # caption is optional (hidden in UI when absent)
        valid = True
    return valid, missing


class ComfyUIWorkflowItemResponse(BaseModel):
    """A single discovered ComfyUI workflow with placeholder validation metadata."""

    model_config = ConfigDict(extra="allow")

    name: str
    display_name: Optional[str] = None
    valid: bool = False
    missing_placeholders: list[str] = []
    source: Optional[str] = None
    workflow_type: Optional[str] = None


class ComfyUIWorkflowListResponse(BaseModel):
    """List of ComfyUI workflows plus the directories they were discovered in."""

    model_config = ConfigDict(extra="allow")

    workflows: list[ComfyUIWorkflowItemResponse] = []
    workflow_dirs: dict[str, str] = {}


class ComfyUIWorkflowDeleteResponse(BaseModel):
    """Result of deleting a user workflow."""

    model_config = ConfigDict(extra="allow")

    status: str
    name: str


class ComfyUIAbortResponse(BaseModel):
    """Result of aborting the active ComfyUI run."""

    model_config = ConfigDict(extra="allow")

    status: str
    interrupted: bool = False
    queue_cleared: bool = False


class ComfyUIPromptItemResponse(BaseModel):
    """A single submitted ComfyUI prompt entry."""

    model_config = ConfigDict(extra="allow")

    prompt_id: Optional[str] = None
    picture_id: Optional[int] = None
    workflow: Optional[str] = None


class ComfyUIRunResponse(BaseModel):
    """Result of submitting one or more ComfyUI prompts."""

    model_config = ConfigDict(extra="allow")

    status: str
    prompts: list[ComfyUIPromptItemResponse] = []
    workflow: Optional[str] = None


class ComfyUIWorkflowImportResponse(BaseModel):
    """Result of importing/saving a user workflow."""

    model_config = ConfigDict(extra="allow")

    status: str
    name: str
    workflow_dir: str


class ComfyUIPictureWorkflowResponse(BaseModel):
    """ComfyUI workflow info extracted from a picture's embedded metadata."""

    model_config = ConfigDict(extra="allow")

    workflow: dict
    is_api_format: bool = False
    summary: Optional[str] = None
    models: list[str] = []
    loras: list[str] = []
    positive_prompt: Optional[str] = None
    seed: Optional[int] = None


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/comfyui")
    async def comfyui_progress_proxy(websocket: WebSocket):
        # The HTTP auth middleware does not cover WebSockets. Require an
        # authenticated OWNER before accepting — running ComfyUI is an owner
        # operation. Without this, an unauthenticated (or merely resource-
        # scoped) client would get a WebSocket proxy to the internal ComfyUI
        # service via the DEFAULT_COMFYUI_URL fallback. Also reject cross-site
        # handshakes (CSWSH).
        if not server.auth.is_websocket_origin_allowed(
            websocket, server.allow_origins, server.allow_origin_regex
        ):
            await websocket.close(code=1008)
            return
        ws_auth = server.auth.authenticate_websocket(websocket)
        if ws_auth is None or not ws_auth.is_owner:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        user = server.vault.db.run_task(
            lambda session: session.get(User, ws_auth.user_id),
            priority=DBPriority.IMMEDIATE,
        )

        comfyui_url = getattr(user, "comfyui_url", None) if user else None
        comfyui_url = (comfyui_url or DEFAULT_COMFYUI_URL).rstrip("/")
        client_id = (
            websocket.query_params.get("clientId")
            or websocket.query_params.get("client_id")
            or f"pixlstash-{uuid.uuid4().hex[:8]}"
        )
        ws_base = (
            comfyui_url.replace("https://", "wss://")
            if comfyui_url.startswith("https://")
            else comfyui_url.replace("http://", "ws://")
        )
        ws_url = f"{ws_base}/ws?clientId={quote(client_id)}"

        async def forward_upstream(upstream):
            try:
                async for message in upstream:
                    if isinstance(message, (bytes, bytearray)):
                        await websocket.send_bytes(bytes(message))
                    else:
                        await websocket.send_text(message)
            except asyncio.CancelledError:
                raise
            except WebSocketDisconnect:
                logger.debug(
                    "ComfyUI WebSocket client disconnected while forwarding upstream."
                )
            except Exception as exc:
                logger.debug("ComfyUI WebSocket upstream forward failed: %s", exc)

        async def forward_downstream(upstream):
            try:
                while True:
                    message = await websocket.receive_text()
                    if message:
                        await upstream.send(message)
            except asyncio.CancelledError:
                raise
            except WebSocketDisconnect:
                logger.debug("ComfyUI WebSocket client disconnected normally.")
            except Exception as exc:
                logger.debug("ComfyUI WebSocket downstream receive failed: %s", exc)

        try:
            async with websockets.connect(
                ws_url, ping_interval=None, close_timeout=2
            ) as upstream:
                upstream_task = asyncio.create_task(forward_upstream(upstream))
                downstream_task = asyncio.create_task(forward_downstream(upstream))
                done, pending = await asyncio.wait(
                    {upstream_task, downstream_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    exc = task.exception()
                    if exc:
                        raise exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("ComfyUI progress proxy failed: %s", exc)
        finally:
            try:
                await websocket.close()
            except Exception as exc:
                logger.debug("Failed to close WebSocket cleanly: %s", exc)

    @router.get(
        "/comfyui/workflows",
        summary="List ComfyUI workflows",
        description="Lists discovered built-in and user workflows with placeholder validation metadata.",
        response_model=ComfyUIWorkflowListResponse,
    )
    async def list_comfyui_workflows():
        workflow_dirs = {
            "built_in": _workflow_builtin_dir(),
            "user": _workflow_user_dir(),
        }
        workflows = []
        seen = set()
        for source, folder in _workflow_dirs():
            if not os.path.isdir(folder):
                continue
            for entry in sorted(os.listdir(folder)):
                if not entry.lower().endswith(".json"):
                    continue
                if entry in seen:
                    continue
                seen.add(entry)
                path = os.path.join(folder, entry)
                try:
                    payload = _load_workflow_json(path)
                    valid, missing = _find_placeholder_usage(payload)
                except Exception as exc:
                    logger.warning("Failed to read workflow %s: %s", entry, exc)
                    valid = False
                    missing = [PLACEHOLDER_IMAGE, PLACEHOLDER_CAPTION]
                workflows.append(
                    {
                        "name": entry,
                        "display_name": os.path.splitext(entry)[0],
                        "valid": valid,
                        "missing_placeholders": missing,
                        "source": source,
                        "workflow_type": "t2i"
                        if PLACEHOLDER_IMAGE in missing
                        else "i2i",
                    }
                )
        workflows.sort(key=lambda item: item.get("name", ""))
        return {
            "workflows": workflows,
            "workflow_dirs": workflow_dirs,
        }

    @router.delete(
        "/comfyui/workflows/{workflow_name}",
        include_in_schema=False,
        summary="Delete user workflow",
        description="Deletes a workflow JSON from the user workflow directory.",
        response_model=ComfyUIWorkflowDeleteResponse,
    )
    async def delete_comfyui_workflow(workflow_name: str):
        normalized = _normalize_workflow_name(workflow_name)
        if not normalized:
            raise HTTPException(status_code=400, detail="workflow_name is required")
        workflow_dir = _workflow_user_dir()
        try:
            path = resolve_path_within(workflow_dir, normalized)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid workflow name")
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="Workflow not found in user")
        try:
            os.remove(path)
        except OSError as exc:
            logger.warning("Failed to delete workflow %s: %s", normalized, exc)
            raise HTTPException(status_code=500, detail="Failed to delete workflow")
        return {"status": "success", "name": normalized}

    @router.post(
        "/comfyui/abort",
        include_in_schema=False,
        summary="Abort ComfyUI execution",
        description="Interrupts the currently running ComfyUI prompt and clears the pending queue.",
        response_model=ComfyUIAbortResponse,
    )
    async def abort_comfyui(request: Request):
        user = server.auth.get_user_for_request(request)
        comfyui_url = getattr(user, "comfyui_url", None) if user else None
        comfyui_url = (comfyui_url or DEFAULT_COMFYUI_URL).rstrip("/")
        result = _comfyui_abort(comfyui_url)
        return {"status": "success", **result}

    @router.post(
        "/comfyui/run_i2i",
        summary="Run ComfyUI image-to-image",
        description="Submits i2i prompts for one or more picture ids and imports generated outputs back into PixlStash. Outputs are placed in each source picture's stack by default; pass stack=false to skip stacking while still copying the source's character/set/project associations.",
        response_model=ComfyUIRunResponse,
    )
    async def run_comfyui_i2i(request: Request, payload: dict = Body(...)):
        workflow_name = _normalize_workflow_name(payload.get("workflow_name"))
        if not workflow_name:
            raise HTTPException(status_code=400, detail="workflow_name is required")

        raw_ids = payload.get("picture_ids")
        if raw_ids is None:
            raw_ids = [payload.get("picture_id")]
        if not isinstance(raw_ids, list) or not raw_ids:
            raise HTTPException(status_code=400, detail="picture_ids must be a list")
        try:
            picture_ids = [int(pid) for pid in raw_ids if pid is not None]
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="picture_ids must be integers")
        if not picture_ids:
            raise HTTPException(status_code=400, detail="picture_ids must be integers")

        caption = payload.get("caption") or ""
        if not isinstance(caption, str):
            caption = str(caption)
        client_id = payload.get("client_id") or payload.get("clientId") or None
        if client_id is not None:
            client_id = str(client_id)
        # Optional physical stacking of derived outputs. Default true preserves
        # the historical behaviour; when false, outputs skip the stack entirely
        # but still inherit the source's character/set/project associations.
        should_stack = bool(payload.get("stack", True))

        workflow_path, workflow_source = _resolve_workflow_path(workflow_name)
        if not workflow_path:
            raise HTTPException(status_code=404, detail="Workflow not found")

        workflow_payload = _load_workflow_json(workflow_path)
        valid, missing = _find_placeholder_usage(workflow_payload)
        if not valid and PLACEHOLDER_IMAGE in missing:
            raise HTTPException(
                status_code=400,
                detail=f"Workflow missing placeholders: {', '.join(missing)}",
            )
        output_node_ids = _extract_output_node_ids(workflow_payload, payload)

        user = server.auth.get_user_for_request(request)
        comfyui_url = getattr(user, "comfyui_url", None) if user else None
        comfyui_url = (comfyui_url or DEFAULT_COMFYUI_URL).rstrip("/")

        def fetch_pictures(session, ids: list[int]):
            return session.exec(select(Picture).where(Picture.id.in_(ids))).all()

        pics = server.vault.db.run_task(fetch_pictures, picture_ids)
        pic_map = {pic.id: pic for pic in pics}

        prompts = []
        for pic_id in picture_ids:
            pic = pic_map.get(pic_id)
            if not pic or not getattr(pic, "file_path", None):
                raise HTTPException(status_code=404, detail="Picture not found")
            resolved_path = ImageUtils.resolve_picture_path(
                server.vault.image_root, pic.file_path
            )
            if not resolved_path or not os.path.isfile(resolved_path):
                raise HTTPException(status_code=404, detail="Picture file missing")

            uploaded_name = _upload_image_to_comfyui(comfyui_url, resolved_path)
            replacements = {
                PLACEHOLDER_IMAGE: uploaded_name,
                PLACEHOLDER_CAPTION: caption,
            }
            workflow_instance = _replace_placeholders(
                deepcopy(workflow_payload), replacements
            )
            _randomize_seeds(workflow_instance)
            # Only create/join a stack and tag the SaveImage filename when
            # stacking is requested. When disabled, stack_id stays None so the
            # worker places nothing in a stack; associations are still copied
            # because is_i2i=True drives the face/set/project copy below.
            stack_id: int | None = None
            if should_stack:
                stack_id = server.vault.db.run_task(
                    get_or_create_stack_for_picture,
                    pic_id,
                )
                prefix_seed = ""
                for node in workflow_instance.values():
                    if not isinstance(node, dict):
                        continue
                    if node.get("class_type") != "SaveImage":
                        continue
                    inputs = node.get("inputs") or {}
                    prefix_seed = str(inputs.get("filename_prefix") or "")
                    break
                if stack_id:
                    prefix_value = build_stack_filename_prefix(
                        prefix_seed, stack_id, pic_id
                    )
                    if not _apply_filename_prefix(workflow_instance, prefix_value):
                        logger.warning(
                            "ComfyUI workflow has no SaveImage node to tag for stack %s",
                            stack_id,
                        )
            response_payload = _submit_comfyui_prompt(
                comfyui_url,
                workflow_instance,
                client_id,
            )
            prompt_id = response_payload.get("prompt_id") or response_payload.get("id")
            if prompt_id:
                worker = threading.Thread(
                    target=_process_comfyui_outputs,
                    args=(
                        server,
                        comfyui_url,
                        str(prompt_id),
                        output_node_ids,
                        stack_id,
                        pic_id,
                    ),
                    kwargs={"is_i2i": True},
                    daemon=True,
                )
                worker.start()
            prompts.append(
                {
                    "picture_id": pic_id,
                    "prompt_id": prompt_id,
                    "workflow": workflow_name,
                }
            )

        return {"status": "success", "prompts": prompts}

    @router.post(
        "/comfyui/run_t2i",
        summary="Run ComfyUI text-to-image",
        description="Submits a t2i prompt using only a caption and imports generated outputs back into PixlStash.",
        response_model=ComfyUIRunResponse,
    )
    async def run_comfyui_t2i(request: Request, payload: dict = Body(...)):
        workflow_name = _normalize_workflow_name(payload.get("workflow_name"))
        if not workflow_name:
            raise HTTPException(status_code=400, detail="workflow_name is required")

        caption = payload.get("caption") or ""
        if not isinstance(caption, str):
            caption = str(caption)
        client_id = payload.get("client_id") or payload.get("clientId") or None
        if client_id is not None:
            client_id = str(client_id)
        raw_source_id = payload.get("source_picture_id")
        source_picture_id: int | None = (
            int(raw_source_id) if raw_source_id is not None else None
        )
        raw_set_id = payload.get("set_id")
        raw_project_id = payload.get("project_id")
        raw_character_id = payload.get("character_id")
        view_context: dict | None = None
        ctx: dict = {}
        if raw_set_id is not None:
            try:
                ctx["set_id"] = int(raw_set_id)
            except (TypeError, ValueError):
                # Ignore invalid set_id values but log for debugging.
                logger.debug(
                    "Ignoring invalid set_id value in run_comfyui_t2i: %r", raw_set_id
                )
        if raw_project_id is not None:
            try:
                ctx["project_id"] = int(raw_project_id)
            except (TypeError, ValueError):
                # Ignore invalid project_id values but log for debugging.
                logger.debug(
                    "Ignoring invalid project_id value in run_comfyui_t2i: %r",
                    raw_project_id,
                )
        if raw_character_id is not None:
            try:
                ctx["character_id"] = int(raw_character_id)
            except (TypeError, ValueError):
                # Ignore invalid character_id values but log for debugging.
                logger.debug(
                    "Ignoring invalid character_id value in run_comfyui_t2i: %r",
                    raw_character_id,
                )
        if ctx:
            view_context = ctx

        workflow_path, _ = _resolve_workflow_path(workflow_name)
        if not workflow_path:
            raise HTTPException(status_code=404, detail="Workflow not found")

        workflow_payload = _load_workflow_json(workflow_path)
        if PLACEHOLDER_IMAGE in json.dumps(workflow_payload, ensure_ascii=False):
            raise HTTPException(
                status_code=400,
                detail="This workflow requires an image input and cannot be used for text-to-image generation.",
            )

        output_node_ids = _extract_output_node_ids(workflow_payload, payload)

        user = server.auth.get_user_for_request(request)
        comfyui_url = getattr(user, "comfyui_url", None) if user else None
        comfyui_url = (comfyui_url or DEFAULT_COMFYUI_URL).rstrip("/")

        seed_mode = payload.get("seed_mode", "random")
        fixed_seed = payload.get("seed")

        replacements = {PLACEHOLDER_CAPTION: caption}
        workflow_instance = _replace_placeholders(
            deepcopy(workflow_payload), replacements
        )
        if seed_mode == "fixed":
            if fixed_seed is None or (
                isinstance(fixed_seed, str) and fixed_seed.strip() == ""
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid seed: when seed_mode is 'fixed', seed must be an integer between 0 and 4294967295.",
                )
            try:
                seed_int = int(fixed_seed)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid seed: when seed_mode is 'fixed', seed must be an integer between 0 and 4294967295.",
                )
            if not (0 <= seed_int <= 2**32 - 1):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid seed: must be between 0 and 4294967295.",
                )
            _apply_fixed_seed(workflow_instance, seed_int)
        else:
            _randomize_seeds(workflow_instance)

        response_payload = _submit_comfyui_prompt(
            comfyui_url, workflow_instance, client_id
        )
        prompt_id = response_payload.get("prompt_id") or response_payload.get("id")
        if prompt_id:
            worker = threading.Thread(
                target=_process_comfyui_outputs,
                args=(
                    server,
                    comfyui_url,
                    str(prompt_id),
                    output_node_ids,
                    None,
                    source_picture_id,
                ),
                kwargs={"view_context": view_context},
                daemon=True,
            )
            worker.start()

        prompts = []
        if prompt_id:
            prompts.append({"prompt_id": prompt_id})
        return {
            "status": "success",
            "prompts": prompts,
            "workflow": workflow_name,
        }

    @router.post(
        "/comfyui/workflows/import",
        include_in_schema=False,
        summary="Import ComfyUI workflow",
        description="Saves a workflow JSON into the user workflow directory, optionally overwriting an existing file.",
        response_model=ComfyUIWorkflowImportResponse,
    )
    async def import_comfyui_workflow(payload: dict = Body(...)):
        name = _normalize_workflow_name(payload.get("name"))
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        workflow = payload.get("workflow")
        if not isinstance(workflow, dict):
            raise HTTPException(
                status_code=400, detail="workflow must be a JSON object"
            )
        overwrite = bool(payload.get("overwrite"))

        workflow_dir = _workflow_user_dir()
        os.makedirs(workflow_dir, exist_ok=True)
        try:
            path = resolve_path_within(workflow_dir, name)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid workflow name")
        if os.path.exists(path) and not overwrite:
            raise HTTPException(status_code=409, detail="Workflow already exists")

        _save_workflow_json(path, workflow)
        return {
            "status": "success",
            "name": name,
            "workflow_dir": workflow_dir,
        }

    @router.get(
        "/comfyui/pictures/{picture_id}/workflow",
        summary="Get ComfyUI workflow for a picture",
        description=(
            "Extracts and returns the ComfyUI workflow embedded in a picture's "
            "file metadata, if present."
        ),
        response_model=ComfyUIPictureWorkflowResponse,
    )
    def get_picture_comfyui_workflow(request: Request, picture_id: str):
        try:
            pic_id = int(picture_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        pics = server.vault.db.run_immediate_read_task(
            Picture.find, id=pic_id, select_fields=["id", "file_path"]
        )
        if not pics:
            raise HTTPException(status_code=404, detail="Picture not found")
        pic = pics[0]

        file_path = ImageUtils.resolve_picture_path(
            server.vault.image_root, pic.file_path
        )
        if not file_path:
            raise HTTPException(
                status_code=404, detail="Picture file path could not be resolved"
            )

        try:
            embedded_metadata = ImageUtils.extract_embedded_metadata(file_path)
        except Exception as exc:
            logger.warning(
                "[comfyui] Failed to read embedded metadata for picture id=%s: %s",
                pic.id,
                exc,
            )
            raise HTTPException(
                status_code=500, detail="Failed to read embedded metadata"
            ) from exc

        workflow_info = extract_comfy_workflow_info(embedded_metadata)
        if not workflow_info:
            raise HTTPException(
                status_code=404,
                detail="No ComfyUI workflow found in picture metadata",
            )

        return workflow_info

    return router
