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
from pixlstash.utils.comfyui_utilities import (
    collect_seed_inputs,
    extract_comfy_workflow_info,
    extract_generation_info,
    find_comfy_api_prompt,
    summarize_comfy_workflow,
)
from pixlstash.services.comfyui_recipe_service import (
    MAX_SEED_64,
    apply_seeds,
    collect_node_classes,
    detect_seed_targets,
    fetch_object_info,
    preflight_prompt,
    sanitize_prompt_graph,
    unchecked_preflight,
)
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
    graph_has_pixlstash_nodes,
    graph_has_pixlstash_saver,
)

# Re-exported so existing call sites and tests that import these helpers from
# this module keep resolving after the move into services/comfyui_service.py.
from pixlstash.services.comfyui_service import (  # noqa: F401
    _assign_outputs_to_stack_top,
    _assign_pictures_to_view_context,
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


MAX_SEED = 2**32 - 1


def _resolve_fixed_seed(payload: dict, max_seed: int = MAX_SEED) -> int | None:
    """Return the validated fixed seed, or ``None`` when seeds should randomize.

    Shared by the t2i, i2i and recipe run handlers so all three accept the same
    ``seed_mode`` / ``seed`` pair.

    ``max_seed`` differs by caller on purpose. The template paths keep the
    historical 32-bit ceiling, which every sampler accepts. Recipe replay must
    allow the full 64-bit range ComfyUI's core samplers declare: the shipped
    ``Flux2-Klein-Image-Edit`` template's own ``noise_seed`` is 432262096973502,
    so a 32-bit check would reject reproducing our own built-in's default.

    Args:
        payload: The raw request body.
        max_seed: Inclusive upper bound to accept.

    Returns:
        The seed to pin, or ``None`` for ``seed_mode`` other than ``"fixed"``.

    Raises:
        HTTPException: 400 when ``seed_mode`` is ``"fixed"`` and ``seed`` is
            missing, non-numeric, or out of range.
    """
    if payload.get("seed_mode", "random") != "fixed":
        return None
    detail = (
        "Invalid seed: when seed_mode is 'fixed', seed must be an integer "
        f"between 0 and {max_seed}."
    )
    raw_seed = payload.get("seed")
    if raw_seed is None or (isinstance(raw_seed, str) and raw_seed.strip() == ""):
        raise HTTPException(status_code=400, detail=detail)
    try:
        seed_int = int(raw_seed)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=detail)
    if not (0 <= seed_int <= max_seed):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid seed: must be between 0 and {max_seed}.",
        )
    return seed_int


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


def _resolve_picture_file(server, pic_id: int) -> str:
    """Return the on-disk path for *pic_id*, or raise the matching HTTP error."""
    pics = server.vault.db.run_immediate_read_task(
        Picture.find, id=pic_id, select_fields=["id", "file_path"]
    )
    if not pics:
        raise HTTPException(status_code=404, detail="Picture not found")
    file_path = ImageUtils.resolve_picture_path(
        server.vault.image_root, pics[0].file_path
    )
    if not file_path:
        raise HTTPException(
            status_code=404, detail="Picture file path could not be resolved"
        )
    return file_path


def _picture_source_origin(server, pic_id: int) -> tuple[bool, str | None]:
    """Classify how *pic_id*'s file entered the vault.

    A replayed recipe is file metadata, so it is only as trustworthy as the file
    is. There is no dedicated provenance column, so this reads the three fields
    that are only ever written on an *inbound* path and are left NULL by
    PixlStash's own ComfyUI import (``_import_comfyui_outputs`` calls
    ``create_picture_from_bytes`` without any of them):

    - ``reference_folder_id`` - the reference folder the file is tracked in.
    - ``import_source_folder`` - the watch-folder root that produced the file.
    - ``original_file_name`` - stamped by the upload and staged-import paths and
      by the reference-folder scan, i.e. every file that arrived with a name of
      its own.

    Read in that order because it runs most-specific first: a reference-folder
    picture also carries an ``original_file_name``, and naming the folder it
    came from is the more useful answer.

    The label names the *route in*, never the path itself: a watch-folder root
    is a filesystem path on the owner's machine and the dialog does not need it
    to make its point.

    Deliberately fails toward "not imported": an unreadable picture row is
    reported as not-imported rather than raising, because this drives an
    advisory banner and must never be the thing that breaks the dialog. The
    control that actually gates a run is the unchecked-pre-flight refusal, which
    fails closed.

    Args:
        server: The running server, for vault DB access.
        pic_id: The picture whose origin to classify.

    Returns:
        ``(came_from_outside, label)``; the label is None when it did not.
    """
    try:
        pics = server.vault.db.run_immediate_read_task(
            Picture.find,
            id=pic_id,
            select_fields=[
                "id",
                "original_file_name",
                "import_source_folder",
                "reference_folder_id",
            ],
        )
    except Exception as exc:
        logger.warning(
            "[comfyui] Could not read the origin fields for picture id=%s (%s); "
            "reporting the recipe source as not-imported, so the dialog will "
            "show no external-workflow warning for it.",
            pic_id,
            exc,
        )
        return False, None
    if not pics:
        return False, None
    pic = pics[0]
    if getattr(pic, "reference_folder_id", None):
        return True, "Reference folder"
    if getattr(pic, "import_source_folder", None):
        return True, "Watched folder"
    if getattr(pic, "original_file_name", None):
        return True, "Imported file"
    return False, None


def _load_embedded_api_prompt(server, pic_id: int) -> dict | None:
    """Return the picture's embedded API-format ``prompt`` graph, or ``None``.

    ``None`` covers every honest "there is nothing to replay" case: a UI-graph
    only file, A1111 metadata, a stripped PNG, or a JPEG. It is not an error.

    Raises:
        HTTPException: 404 when the picture or its file cannot be resolved,
            500 when the file exists but its metadata cannot be read.
    """
    file_path = _resolve_picture_file(server, pic_id)
    try:
        embedded_metadata = ImageUtils.extract_embedded_metadata(file_path)
    except Exception as exc:
        logger.warning(
            "[comfyui] Failed to read embedded metadata for picture id=%s (%s): %s",
            pic_id,
            file_path,
            exc,
        )
        raise HTTPException(
            status_code=500, detail="Failed to read embedded metadata"
        ) from exc
    return find_comfy_api_prompt(embedded_metadata)


def _describe_preflight_failure(preflight: dict) -> str:
    """Turn a failed pre-flight into a sentence naming what to go and fix.

    The three buckets get three different sentences on purpose: a missing node
    pack, a missing model file and a missing input image send the user to three
    different places, and collapsing them into "something is missing" is the
    difference between an actionable message and a support ticket.
    """
    parts: list[str] = []
    classes = preflight.get("missing_node_classes") or []
    if classes:
        parts.append("missing node types: " + ", ".join(str(c) for c in classes))
    models = [
        str(item.get("value")) for item in preflight.get("missing_models") or [] if item
    ]
    if models:
        parts.append("missing models: " + ", ".join(models))
    inputs = [
        str(item.get("value"))
        for item in preflight.get("missing_input_images") or []
        if item
    ]
    if inputs:
        parts.append(
            "the source image this recipe loads is no longer in ComfyUI's input "
            "folder: " + ", ".join(inputs)
        )
    if not parts:
        return "This recipe cannot run on your ComfyUI."
    return "Your ComfyUI cannot run this recipe - " + "; ".join(parts) + "."


def _inspect_recipe(comfyui_url: str, prompt_graph: dict) -> tuple[dict, list[dict]]:
    """Return ``(preflight, seed_targets)`` for *prompt_graph*.

    Both answers come from the same ``/object_info`` fetch, so they are made
    together. When ComfyUI is unreachable the pre-flight degrades to
    *unchecked* (not *failed*) and seed detection falls back to the static
    class list, which covers the core samplers but not custom node packs.
    """
    try:
        object_info = fetch_object_info(comfyui_url)
    except RuntimeError as exc:
        logger.info(
            "[comfyui] Recipe pre-flight skipped, ComfyUI not reachable at %s: %s",
            comfyui_url,
            exc,
        )
        return unchecked_preflight(str(exc)), collect_seed_inputs(prompt_graph)
    return (
        preflight_prompt(prompt_graph, object_info),
        detect_seed_targets(prompt_graph, object_info),
    )


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
    """List of ComfyUI workflows, by name.

    The directories they were discovered in are deliberately absent: they are
    host paths under the owner's home directory and this route is ANY_TOKEN, so
    a share-link holder was reading them. Nothing consumed them (§16.3,
    2026-08-15 - the same sweep that moved the tagger folders).
    """

    model_config = ConfigDict(extra="allow")

    workflows: list[ComfyUIWorkflowItemResponse] = []


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


class ComfyUIPreflightResponse(BaseModel):
    """Result of checking an embedded recipe against the target ComfyUI.

    ``checked=False`` means the question could not be asked (ComfyUI
    unreachable) - NOT that the recipe passed. ``ok`` stays True in that case
    because the only thing actually known is that the check did not run.
    """

    model_config = ConfigDict(extra="allow")

    ok: bool = True
    checked: bool = False
    error: Optional[str] = None
    missing_node_classes: list[str] = []
    missing_models: list[dict] = []
    missing_input_images: list[dict] = []
    has_save_image: bool = False
    unchecked_fields: int = 0


class ComfyUIPictureRecipeResponse(BaseModel):
    """Whether a picture carries a replayable ComfyUI recipe, and its state.

    ``node_classes`` and ``source_is_imported`` exist for the owner's *consent*
    decision, not for display polish: the graph is attacker-authorable file
    metadata, so the confirm step has to say which node classes will run and
    whether the file came from outside this instance.
    """

    model_config = ConfigDict(extra="allow")

    available: bool = False
    reason: Optional[str] = None
    summary: Optional[str] = None
    positive_prompt: Optional[str] = None
    seed: Optional[int] = None
    models: list[str] = []
    loras: list[str] = []
    node_count: int = 0
    # Distinct class_type names the graph would execute, sorted.
    node_classes: list[str] = []
    # True when the source file entered the vault from outside this instance
    # (upload, watch folder, reference folder) rather than being generated here.
    source_is_imported: bool = False
    # How it got in ("Imported file" / "Watched folder" / "Reference folder"),
    # None when it was generated here. Names the route, never the path.
    source_label: Optional[str] = None
    seed_inputs: list[dict] = []
    preflight: Optional[ComfyUIPreflightResponse] = None


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/comfyui")
    async def comfyui_progress_proxy(websocket: WebSocket):
        lease = server.library_coordinator.acquire_read()
        if lease is None:
            await websocket.close(code=1013, reason="Library unavailable")
            return
        ws_client = None
        ws_auth = None
        admission_lease = None
        try:
            # The HTTP auth middleware does not cover WebSockets. Require an
            # authenticated OWNER before accepting - running ComfyUI is an owner
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
            admission_lease = server.auth.register_authenticated_websocket(websocket)
            if admission_lease is None:
                await websocket.close(code=1012)
                return
            await websocket.accept()
            # The proxy is library-bound even though its upstream URL is a machine
            # setting: an in-flight workflow and its eventual picture ids belong to
            # the old vault. Register it in the same lifecycle set as /ws/updates so
            # a switch terminates both sides instead of leaving a stale proxy alive.
            candidate = {
                "ws": websocket,
                "loop": asyncio.get_running_loop(),
                "owner": True,
                "broadcast": False,
            }
            with server._ws_clients_lock:
                server._ws_clients.append(candidate)
            ws_client = candidate
        finally:
            server.library_coordinator.release_read(lease)
        if ws_client is None or ws_auth is None:
            # Admission can be granted before a later step in the leased block
            # fails. The proxy's ``finally`` never runs on this path, so release
            # the admission lease here instead.
            if admission_lease is not None:
                server.auth.unregister_authenticated_websocket(admission_lease)
            return
        try:
            # comfyui_url is a machine setting, so it lives in the hub.
            user = server.hub_engine.run_task(
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
                    proxy_tasks = {upstream_task, downstream_task}
                    try:
                        done, _pending = await asyncio.wait(
                            proxy_tasks,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for task in done:
                            exc = task.exception()
                            if exc:
                                raise exc
                    finally:
                        # The restore barrier cancels this owning handler after
                        # closing the client socket. Always tear down both proxy
                        # directions as part of that cancellation; otherwise an
                        # orphaned upstream iterator could outlive cutover.
                        for task in proxy_tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*proxy_tasks, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("ComfyUI progress proxy failed: %s", exc)
        finally:
            with server._ws_clients_lock:
                if ws_client is not None and ws_client in server._ws_clients:
                    server._ws_clients.remove(ws_client)
            try:
                await websocket.close()
            except Exception as exc:
                logger.debug("Failed to close WebSocket cleanly: %s", exc)
            server.auth.unregister_authenticated_websocket(admission_lease)

    @router.get(
        "/comfyui/workflows",
        summary="List ComfyUI workflows",
        description="Lists discovered built-in and user workflows with placeholder validation metadata.",
        response_model=ComfyUIWorkflowListResponse,
    )
    async def list_comfyui_workflows():
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
        return {"workflows": workflows}

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
        description=(
            "Submits i2i prompts for one or more picture ids and imports generated "
            "outputs back into PixlStash. Outputs are placed in each source "
            "picture's stack by default; pass stack=false to skip stacking while "
            "still copying the source's character/set/project associations. Seeds "
            "randomize unless seed_mode='fixed' is sent with an integer seed "
            "(0-4294967295), in which case every sampler node is pinned to it."
        ),
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
        # Remix (v1.9) submits a seed from the "Generate from this" modal; the
        # historical callers send neither key and keep randomizing.
        fixed_seed = _resolve_fixed_seed(payload)

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
            if fixed_seed is not None:
                _apply_fixed_seed(workflow_instance, fixed_seed)
            else:
                _randomize_seeds(workflow_instance)
            # Only create/join a stack and tag the SaveImage filename when
            # stacking is requested. When disabled, stack_id stays None so the
            # worker places nothing in a stack; set/project associations and the
            # source_picture_id marker are propagated either way.
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
                    if not _apply_filename_prefix(
                        workflow_instance, prefix_value
                    ) and not graph_has_pixlstash_saver(workflow_instance):
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
                origin_lease = request.state.library_lease
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
                    kwargs={
                        "origin_generation": origin_lease.generation,
                        "origin_library_uuid": origin_lease.library_uuid,
                    },
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

        fixed_seed = _resolve_fixed_seed(payload)

        replacements = {PLACEHOLDER_CAPTION: caption}
        workflow_instance = _replace_placeholders(
            deepcopy(workflow_payload), replacements
        )
        if fixed_seed is not None:
            _apply_fixed_seed(workflow_instance, fixed_seed)
        else:
            _randomize_seeds(workflow_instance)

        response_payload = _submit_comfyui_prompt(
            comfyui_url, workflow_instance, client_id
        )
        prompt_id = response_payload.get("prompt_id") or response_payload.get("id")
        if prompt_id:
            origin_lease = request.state.library_lease
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
                kwargs={
                    "view_context": view_context,
                    "origin_generation": origin_lease.generation,
                    "origin_library_uuid": origin_lease.library_uuid,
                },
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

    @router.get(
        "/comfyui/pictures/{picture_id}/recipe",
        summary="Get the replayable ComfyUI recipe for a picture",
        description=(
            "Reports whether a picture carries a replayable recipe - the "
            "embedded API-format `prompt` chunk, i.e. the graph the ComfyUI "
            "server actually executed - and pre-flights it against the target "
            "ComfyUI's /object_info. The UI `workflow` chunk is deliberately "
            "NOT considered: it is not submittable and is never converted. "
            '`available: false` with `reason: "no_prompt_chunk"` is the normal '
            "answer for imported photos, A1111 output and stripped files, not an "
            "error. A `preflight` with `checked: false` means ComfyUI could not "
            "be reached, not that the recipe passed."
        ),
        response_model=ComfyUIPictureRecipeResponse,
    )
    def get_picture_comfyui_recipe(request: Request, picture_id: str):
        try:
            pic_id = int(picture_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        prompt_graph = _load_embedded_api_prompt(server, pic_id)
        if not prompt_graph:
            return {"available": False, "reason": "no_prompt_chunk"}

        user = server.auth.get_user_for_request(request)
        comfyui_url = getattr(user, "comfyui_url", None) if user else None
        comfyui_url = (comfyui_url or DEFAULT_COMFYUI_URL).rstrip("/")

        graph = sanitize_prompt_graph(prompt_graph)
        gen_info = extract_generation_info(graph)
        stats = summarize_comfy_workflow(graph)
        preflight, seed_targets = _inspect_recipe(comfyui_url, graph)
        source_is_imported, source_label = _picture_source_origin(server, pic_id)
        # A graph that calls back into PixlStash cannot be replayed as "a
        # variant of this picture" - see run_recipe's refusal for why. Reported
        # here so the dialog can say so before the user commits to a run, and
        # offer the workflow to paste into ComfyUI instead.
        has_pixlstash_nodes = graph_has_pixlstash_nodes(graph)
        return {
            # "Same workflow, new seed" is only a meaningful offer when there
            # IS a seed to change. Without one the re-run is byte-identical,
            # the import dedupes it on pixel_sha, and the user sees nothing
            # happen at all - so report it as unavailable, with the reason.
            "available": bool(seed_targets) and not has_pixlstash_nodes,
            "reason": (
                "pixlstash_nodes"
                if has_pixlstash_nodes
                else (None if seed_targets else "no_seed_input")
            ),
            "summary": f"API Workflow · {stats['node_count']} nodes",
            "positive_prompt": gen_info["positive_prompt"],
            "seed": gen_info["seed"],
            "models": gen_info["models"],
            "loras": gen_info["loras"],
            "node_count": stats["node_count"],
            # The consent disclosure: what will actually run, and whether the
            # file that carries it came from outside. See R3 in
            # docs/reviews/v1.9-authz-signoff.md.
            "node_classes": collect_node_classes(graph),
            "source_is_imported": source_is_imported,
            "source_label": source_label,
            "seed_inputs": seed_targets,
            "preflight": preflight,
        }

    @router.post(
        "/comfyui/run_recipe",
        summary="Re-run a picture's embedded ComfyUI recipe",
        description=(
            "Replays the API-format `prompt` graph embedded in a picture, with "
            "fresh (or pinned) seeds. The graph is re-extracted from the file "
            "server-side on every call - a client-supplied graph is never "
            "accepted - and pre-flighted first; a pre-flight that finds missing "
            "node classes or model files fails the request with 400 and names "
            "them. A pre-flight that could not run at all (ComfyUI unreachable, "
            "`preflight.checked: false`) also fails with 400 unless the caller "
            "sends `allow_unchecked: true`, which records that the owner "
            "knowingly approved an uninspected graph. Outputs land in the "
            "source picture's stack, exactly as run_i2i does."
        ),
        response_model=ComfyUIRunResponse,
    )
    async def run_comfyui_recipe(request: Request, payload: dict = Body(...)):
        raw_id = payload.get("picture_id")
        if raw_id is None:
            raise HTTPException(status_code=400, detail="picture_id is required")
        try:
            pic_id = int(raw_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        client_id = payload.get("client_id") or payload.get("clientId") or None
        if client_id is not None:
            client_id = str(client_id)
        should_stack = bool(payload.get("stack", True))
        # Consent must be the literal JSON true: any string - including
        # "false" - is truthy in Python and must not read as an acknowledgement.
        allow_unchecked = (
            payload.get("allow_unchecked") is True
            or payload.get("allowUnchecked") is True
        )
        # Replay allows the full 64-bit range the core samplers declare; the
        # template paths keep the 32-bit ceiling. See _resolve_fixed_seed.
        fixed_seed = _resolve_fixed_seed(payload, max_seed=MAX_SEED_64)

        prompt_graph = _load_embedded_api_prompt(server, pic_id)
        if not prompt_graph:
            raise HTTPException(
                status_code=400,
                detail=(
                    "This image has no executable workflow embedded, so it "
                    "cannot be re-run. Use a template instead."
                ),
            )
        workflow_instance = sanitize_prompt_graph(prompt_graph)

        # Refused before the pre-flight, because this is not about whether the
        # graph *can* run - it is that replaying it cannot mean what Generate
        # variants promises. A ComfyUI-PixlStash graph calls back into PixlStash
        # while PixlStash is running it, and every id it carries was frozen when
        # the file was written: the loaders serialise a choice as "<name> #<id>".
        # So the graph re-applies a project/set/character that may since have
        # been deleted or split into another library (the FOREIGN KEY failure
        # this refusal replaces), sources its input by a baked picture id rather
        # than the picture the user right-clicked - or, with that field empty,
        # auto-selects by its own sort and filters - and imports its outputs
        # itself, competing with the import PixlStash is already doing for the
        # variant. The owner's own associations are what a variant should
        # inherit, and PixlStash copies those from the source picture already.
        # Running such a graph from a template is fine: the ids are the ones the
        # owner picked just now, and nothing claims the result is a variant.
        if graph_has_pixlstash_nodes(workflow_instance):
            raise HTTPException(
                status_code=400,
                detail=(
                    "This workflow uses PixlStash nodes, which read and write "
                    "the library while it runs. Re-running it here would use "
                    "the projects, sets and pictures it was saved with, not "
                    "this picture's. Copy the workflow into ComfyUI and run it "
                    "there instead."
                ),
            )

        user = server.auth.get_user_for_request(request)
        comfyui_url = getattr(user, "comfyui_url", None) if user else None
        comfyui_url = (comfyui_url or DEFAULT_COMFYUI_URL).rstrip("/")

        preflight, seed_targets = _inspect_recipe(comfyui_url, workflow_instance)
        if not preflight.get("ok", True):
            raise HTTPException(
                status_code=400, detail=_describe_preflight_failure(preflight)
            )
        if not preflight.get("checked") and not allow_unchecked:
            # The graph is file metadata: whoever made the image authored it,
            # and it executes on the owner's ComfyUI. When the pre-flight could
            # not run, nothing at all is known about it - not even which node
            # classes exist on this install. Fail CLOSED and make the owner say
            # so explicitly, rather than letting an unreachable ComfyUI silently
            # read as "ok". Enforced here and not only in the dialog, because a
            # UI-only gate is not a gate.
            logger.warning(
                "[comfyui] Refusing recipe replay for picture id=%s: the "
                "pre-flight could not run (%s) and the request carried no "
                "allow_unchecked acknowledgement.",
                pic_id,
                preflight.get("error") or "ComfyUI unreachable",
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    "PixlStash could not reach ComfyUI to check this workflow, "
                    "so it has not been inspected. Embedded workflows come from "
                    "the image file itself and can run anything your ComfyUI "
                    "has installed. Start ComfyUI and try again, or confirm you "
                    "want to run it unchecked."
                ),
            )
        if allow_unchecked and not preflight.get("checked"):
            logger.warning(
                "[comfyui] Replaying an UNINSPECTED recipe for picture id=%s "
                "(node classes: %s) on the owner's explicit acknowledgement; "
                "the pre-flight could not run (%s).",
                pic_id,
                ", ".join(collect_node_classes(workflow_instance)) or "none",
                preflight.get("error") or "ComfyUI unreachable",
            )
        if preflight.get("checked") and not preflight.get("has_save_image"):
            # Would run to completion and import nothing - refuse now rather
            # than after the full generation wait.
            raise HTTPException(
                status_code=400,
                detail=(
                    "This workflow has no node that saves images (SaveImage or "
                    "PixlStash Picture Saver), so it produces nothing PixlStash "
                    "can import."
                ),
            )
        if not seed_targets:
            raise HTTPException(
                status_code=400,
                detail=(
                    "This workflow has no random seed, so re-running it would "
                    "produce the identical image. Edit the prompt or use a "
                    "template instead."
                ),
            )

        output_node_ids = _extract_output_node_ids(workflow_instance, payload)
        apply_seeds(workflow_instance, seed_targets, fixed_seed)

        stack_id: int | None = None
        if should_stack:
            stack_id = server.vault.db.run_task(get_or_create_stack_for_picture, pic_id)
            if stack_id:
                prefix_seed = ""
                for node in workflow_instance.values():
                    if not isinstance(node, dict):
                        continue
                    if node.get("class_type") != "SaveImage":
                        continue
                    prefix_seed = str(
                        (node.get("inputs") or {}).get("filename_prefix") or ""
                    )
                    break
                prefix_value = build_stack_filename_prefix(
                    prefix_seed, stack_id, pic_id
                )
                if not _apply_filename_prefix(
                    workflow_instance, prefix_value
                ) and not graph_has_pixlstash_saver(workflow_instance):
                    logger.warning(
                        "Embedded recipe for picture %s has no SaveImage node to tag "
                        "for stack %s; the output will import unstacked.",
                        pic_id,
                        stack_id,
                    )

        response_payload = _submit_comfyui_prompt(
            comfyui_url, workflow_instance, client_id
        )
        prompt_id = response_payload.get("prompt_id") or response_payload.get("id")
        if prompt_id:
            origin_lease = request.state.library_lease
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
                kwargs={
                    "origin_generation": origin_lease.generation,
                    "origin_library_uuid": origin_lease.library_uuid,
                },
                daemon=True,
            )
            worker.start()

        return {
            "status": "success",
            "prompts": [{"picture_id": pic_id, "prompt_id": prompt_id}],
        }

    return router
