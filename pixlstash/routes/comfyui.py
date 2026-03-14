import asyncio
import json
import mimetypes
import os
import random
import threading
import time
import uuid
from copy import deepcopy
from urllib.parse import quote

import requests
import websockets
from fastapi import APIRouter, Body, HTTPException, Request, WebSocket
from fastapi.websockets import WebSocketDisconnect
from sqlmodel import select

from datetime import datetime

from pixlstash.database import DBPriority
from pixlstash.db_models import Face, Picture, PictureStack, User
from pixlstash.event_types import EventType
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.stacking import (
    build_stack_filename_prefix,
    get_or_create_stack_for_picture,
)
from platformdirs import user_data_dir

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

PLACEHOLDER_IMAGE = "{{image_path}}"
PLACEHOLDER_CAPTION = "{{caption}}"
DEFAULT_COMFYUI_URL = "http://127.0.0.1:8188/"
SEED_FIELDS = {"noise_seed", "seed"}
SEED_NODE_CLASSES = {"RandomNoise", "KSampler", "KSamplerAdvanced"}


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
        path = os.path.join(folder, normalized)
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
    return PLACEHOLDER_IMAGE not in missing, missing


def _replace_placeholders(value, replacements: dict[str, str]):
    if isinstance(value, str):
        updated = value
        for key, replacement in replacements.items():
            if key in updated:
                updated = updated.replace(key, replacement)
        return updated
    if isinstance(value, list):
        return [_replace_placeholders(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_placeholders(val, replacements) for key, val in value.items()
        }
    return value


def _randomize_seeds(workflow: dict) -> None:
    """Replace seed values in sampler/noise nodes with a fresh random integer.

    This mirrors what the ComfyUI frontend does when 'randomize' is enabled,
    ensuring repeated runs on the same prompt produce different images.
    """
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") not in SEED_NODE_CLASSES:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for field in SEED_FIELDS:
            if field in inputs and isinstance(inputs[field], (int, float)):
                inputs[field] = random.randint(0, 2**32 - 1)


def _apply_fixed_seed(workflow: dict, seed: int) -> None:
    """Set seed values in sampler/noise nodes to a specific fixed integer."""
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") not in SEED_NODE_CLASSES:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for field in SEED_FIELDS:
            if field in inputs and isinstance(inputs[field], (int, float)):
                inputs[field] = seed


def _apply_filename_prefix(workflow: dict, prefix: str) -> bool:
    updated = False
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") != "SaveImage":
            continue
        inputs = node.get("inputs") or {}
        if not isinstance(inputs, dict):
            inputs = {}
        inputs["filename_prefix"] = prefix
        node["inputs"] = inputs
        updated = True
    return updated


def _upload_image_to_comfyui(base_url: str, file_path: str) -> str:
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "application/octet-stream"
    with open(file_path, "rb") as handle:
        files = {
            "image": (os.path.basename(file_path), handle, mime_type),
        }
        data = {
            "type": "input",
            "overwrite": "true",
        }
        try:
            response = requests.post(
                f"{base_url}/upload/image",
                files=files,
                data=data,
                timeout=30,
            )
        except requests.RequestException as exc:
            logger.warning("ComfyUI upload request failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="ComfyUI upload request failed",
            ) from exc
    if response.status_code >= 300:
        detail = (response.text or "").strip()
        logger.warning(
            "ComfyUI upload failed: status=%s detail=%s",
            response.status_code,
            detail,
        )
        raise HTTPException(
            status_code=502,
            detail=f"ComfyUI upload failed: {response.status_code} {detail}",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        detail = (response.text or "").strip()
        logger.warning("ComfyUI upload invalid JSON: %s", detail)
        raise HTTPException(
            status_code=502,
            detail="ComfyUI upload returned invalid JSON",
        ) from exc
    name = payload.get("name") or payload.get("filename")
    if not name:
        raise HTTPException(
            status_code=502, detail="ComfyUI upload response missing name"
        )
    subfolder = payload.get("subfolder") or ""
    if subfolder:
        return f"{subfolder}/{name}"
    return name


def _submit_comfyui_prompt(
    base_url: str,
    workflow: dict,
    client_id: str | None = None,
) -> dict:
    # Strip PixlStash-specific metadata keys before sending to ComfyUI.
    # ComfyUI iterates all top-level entries as nodes and will crash on any
    # non-dict value (e.g. the list stored in "pixlstash_output_nodes").
    clean_workflow = {
        k: v for k, v in workflow.items() if not k.startswith("pixlstash_")
    }
    # Pass the workflow under extra_pnginfo so ComfyUI embeds it in the
    # generated PNG automatically (same as the ComfyUI frontend does).
    payload = {
        "prompt": clean_workflow,
        "extra_data": {
            "extra_pnginfo": {
                "workflow": clean_workflow,
            }
        },
    }
    if client_id:
        payload["client_id"] = client_id
    try:
        response = requests.post(
            f"{base_url}/prompt",
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        logger.warning("ComfyUI prompt request failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="ComfyUI prompt request failed",
        ) from exc
    if response.status_code >= 300:
        detail = (response.text or "").strip()
        logger.warning(
            "ComfyUI prompt failed: status=%s detail=%s",
            response.status_code,
            detail,
        )
        raise HTTPException(
            status_code=502,
            detail=f"ComfyUI prompt failed: {response.status_code} {detail}",
        )
    try:
        return response.json()
    except ValueError as exc:
        detail = (response.text or "").strip()
        logger.warning("ComfyUI prompt invalid JSON: %s", detail)
        raise HTTPException(
            status_code=502,
            detail="ComfyUI prompt returned invalid JSON",
        ) from exc


def _extract_output_node_ids(workflow: dict, payload: dict) -> list[str]:
    nodes = []
    raw_payload_nodes = payload.get("output_node_ids") or payload.get("output_node_id")
    if raw_payload_nodes is not None:
        if isinstance(raw_payload_nodes, list):
            nodes = [str(node) for node in raw_payload_nodes if node is not None]
        else:
            nodes = [str(raw_payload_nodes)]

    workflow_nodes = []
    if isinstance(workflow, dict):
        raw_workflow_nodes = workflow.get("pixlstash_output_nodes")
        if raw_workflow_nodes is None:
            raw_workflow_nodes = workflow.get("pixlstash_output_node")
        if raw_workflow_nodes is not None:
            if isinstance(raw_workflow_nodes, list):
                workflow_nodes = [
                    str(node) for node in raw_workflow_nodes if node is not None
                ]
            else:
                workflow_nodes = [str(raw_workflow_nodes)]

    if nodes:
        return nodes
    if workflow_nodes:
        return workflow_nodes

    save_nodes = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") == "SaveImage":
            save_nodes.append(str(node_id))
    return save_nodes


def _fetch_comfyui_history(base_url: str, prompt_id: str) -> dict:
    try:
        response = requests.get(
            f"{base_url}/history/{prompt_id}",
            timeout=30,
        )
    except requests.RequestException as exc:
        logger.warning("ComfyUI history request failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="ComfyUI history request failed",
        ) from exc
    if response.status_code >= 300:
        detail = (response.text or "").strip()
        logger.warning(
            "ComfyUI history failed: status=%s detail=%s",
            response.status_code,
            detail,
        )
        raise HTTPException(
            status_code=502,
            detail=f"ComfyUI history failed: {response.status_code} {detail}",
        )
    try:
        return response.json()
    except ValueError as exc:
        detail = (response.text or "").strip()
        logger.warning("ComfyUI history invalid JSON: %s", detail)
        raise HTTPException(
            status_code=502,
            detail="ComfyUI history returned invalid JSON",
        ) from exc


def _extract_comfyui_output_images(
    history_payload: dict,
    prompt_id: str,
    output_node_ids: list[str] | None,
) -> list[dict]:
    outputs = {}
    if isinstance(history_payload, dict):
        if "outputs" in history_payload:
            outputs = history_payload.get("outputs") or {}
        elif prompt_id in history_payload:
            outputs = history_payload.get(prompt_id, {}).get("outputs") or {}

    if not isinstance(outputs, dict):
        return []

    node_filter = set(output_node_ids or [])
    images = []
    for node_id, node_payload in outputs.items():
        if node_filter and str(node_id) not in node_filter:
            continue
        if not isinstance(node_payload, dict):
            continue
        for image in node_payload.get("images") or []:
            if not isinstance(image, dict):
                continue
            filename = image.get("filename")
            if not filename:
                continue
            images.append(
                {
                    "filename": filename,
                    "subfolder": image.get("subfolder") or "",
                    "type": image.get("type") or "output",
                }
            )
    return images


def _wait_for_comfyui_outputs(
    base_url: str,
    prompt_id: str,
    output_node_ids: list[str] | None,
    timeout_s: float = 180.0,
    poll_s: float = 1.0,
) -> list[dict]:
    deadline = time.time() + timeout_s
    last_images = []
    while time.time() < deadline:
        history_payload = _fetch_comfyui_history(base_url, prompt_id)
        images = _extract_comfyui_output_images(
            history_payload, prompt_id, output_node_ids
        )
        if images:
            return images
        last_images = images
        time.sleep(poll_s)
    return last_images


def _download_comfyui_image(base_url: str, entry: dict) -> tuple[bytes, str]:
    filename = entry.get("filename")
    subfolder = entry.get("subfolder") or ""
    file_type = entry.get("type") or "output"
    params = {
        "filename": filename,
        "subfolder": subfolder,
        "type": file_type,
    }
    try:
        response = requests.get(
            f"{base_url}/view",
            params=params,
            timeout=30,
        )
    except requests.RequestException as exc:
        logger.warning("ComfyUI image fetch failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="ComfyUI image fetch failed",
        ) from exc
    if response.status_code >= 300:
        detail = (response.text or "").strip()
        logger.warning(
            "ComfyUI image fetch failed: status=%s detail=%s",
            response.status_code,
            detail,
        )
        raise HTTPException(
            status_code=502,
            detail=f"ComfyUI image fetch failed: {response.status_code} {detail}",
        )
    ext = os.path.splitext(filename or "")[1].lower() or ".png"
    return response.content, ext


def _import_comfyui_outputs(
    server,
    image_entries: list[tuple[bytes, str]],
) -> tuple[list[int], list[int]]:
    if not image_entries:
        return [], []

    shas = [
        ImageUtils.calculate_hash_from_bytes(img_bytes)
        for img_bytes, _ in image_entries
    ]

    existing_pictures = server.vault.db.run_immediate_read_task(
        lambda session: Picture.find(session, pixel_shas=shas, include_unimported=True)
    )
    existing_map = {pic.pixel_sha: pic for pic in existing_pictures}

    new_entries = [
        (entry, sha)
        for entry, sha in zip(image_entries, shas)
        if sha not in existing_map
    ]

    new_pictures = []
    for (img_bytes, ext), sha in new_entries:
        pic_uuid = f"{uuid.uuid4()}{ext}"
        new_pictures.append(
            ImageUtils.create_picture_from_bytes(
                image_root_path=server.vault.image_root,
                image_bytes=img_bytes,
                picture_uuid=pic_uuid,
                pixel_sha=sha,
            )
        )

    def import_task(session):
        if new_pictures:
            session.add_all(new_pictures)
            session.commit()
            for pic in new_pictures:
                session.refresh(pic)
        return new_pictures

    if new_pictures:
        new_pictures = server.vault.db.run_task(import_task)

        def mark_imported(session, ids: list[int]):
            if not ids:
                return []
            now = datetime.utcnow()
            pics = session.exec(select(Picture).where(Picture.id.in_(ids))).all()
            updated = []
            for pic in pics:
                if pic.imported_at is None:
                    pic.imported_at = now
                    session.add(pic)
                    updated.append(pic.id)
            session.commit()
            return updated

        server.vault.db.run_task(mark_imported, [pic.id for pic in new_pictures])

    new_ids = [pic.id for pic in new_pictures if pic.id is not None]
    duplicate_ids = [
        pic.id
        for sha in shas
        if (pic := existing_map.get(sha)) is not None and pic.id is not None
    ]
    return new_ids, duplicate_ids


def _assign_outputs_to_stack_top(
    server,
    stack_id: int,
    picture_ids: list[int],
) -> None:
    if not stack_id or not picture_ids:
        return

    def update_stack(session):
        stack = session.get(PictureStack, stack_id)
        if stack is None:
            return
        pics = session.exec(select(Picture).where(Picture.stack_id == stack_id)).all()
        has_positions = any(pic.stack_position is not None for pic in pics)
        shift = len(picture_ids)
        if has_positions and shift:
            for pic in pics:
                if pic.id in picture_ids:
                    continue
                if pic.stack_position is not None:
                    pic.stack_position += shift
                    session.add(pic)

        for idx, pic_id in enumerate(picture_ids):
            pic = session.get(Picture, pic_id)
            if pic is None:
                continue
            pic.stack_id = stack_id
            pic.stack_position = idx
            session.add(pic)

        stack.updated_at = datetime.utcnow()
        session.add(stack)
        session.commit()

    server.vault.db.run_task(update_stack)


def _copy_face_assignments(
    server,
    source_picture_id: int | None,
    target_picture_ids: list[int],
) -> None:
    if not source_picture_id or not target_picture_ids:
        return

    def copy_task(session):
        source_faces = session.exec(
            select(Face).where(Face.picture_id == source_picture_id)
        ).all()
        if not source_faces:
            return 0
        target_ids = [pid for pid in target_picture_ids if pid is not None]
        if not target_ids:
            return 0
        existing_targets = session.exec(
            select(Face.picture_id).where(Face.picture_id.in_(target_ids))
        ).all()
        skip_ids = set(existing_targets)
        new_faces = []
        for target_id in target_ids:
            if target_id in skip_ids:
                continue
            for face in source_faces:
                new_faces.append(
                    Face(
                        picture_id=target_id,
                        frame_index=face.frame_index,
                        face_index=face.face_index,
                        character_id=face.character_id,
                        bbox=face.bbox,
                    )
                )
        if new_faces:
            session.add_all(new_faces)
            session.commit()
        return len(new_faces)

    copied = server.vault.db.run_task(copy_task)
    if copied:
        logger.info(
            "Copied %s face assignments to %s picture(s) from %s",
            copied,
            len(target_picture_ids),
            source_picture_id,
        )


def _process_comfyui_outputs(
    server,
    base_url: str,
    prompt_id: str,
    output_node_ids: list[str] | None,
    stack_id: int | None,
    source_picture_id: int | None,
) -> None:
    try:
        images = _wait_for_comfyui_outputs(base_url, prompt_id, output_node_ids)
        if not images:
            logger.warning("ComfyUI produced no outputs for prompt %s", prompt_id)
            return
        entries = []
        for entry in images:
            img_bytes, ext = _download_comfyui_image(base_url, entry)
            if img_bytes:
                entries.append((img_bytes, ext))

        new_ids, duplicate_ids = _import_comfyui_outputs(server, entries)
        all_ids = [pid for pid in new_ids + duplicate_ids if pid is not None]
        if stack_id and new_ids:
            _assign_outputs_to_stack_top(server, stack_id, new_ids)
        if new_ids:
            _copy_face_assignments(server, source_picture_id, new_ids)

        if new_ids:
            server.vault.notify(EventType.PICTURE_IMPORTED, new_ids)
        if all_ids:
            server.vault.notify(EventType.CHANGED_PICTURES, all_ids)
    except Exception as exc:
        logger.warning("Failed to import ComfyUI outputs: %s", exc)


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/comfyui")
    async def comfyui_progress_proxy(websocket: WebSocket):
        await websocket.accept()
        session_id = websocket.cookies.get("session_id")
        user = None
        if session_id:
            user_id = server.auth.active_session_ids.get(session_id)
            if user_id is not None:
                user = server.vault.db.run_task(
                    lambda session: session.get(User, user_id),
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
                    await websocket.send_text(message)
            except Exception:
                pass

        async def forward_downstream(upstream):
            try:
                while True:
                    message = await websocket.receive_text()
                    if message:
                        await upstream.send(message)
            except WebSocketDisconnect:
                pass
            except Exception:
                pass

        try:
            async with websockets.connect(ws_url, ping_interval=None) as upstream:
                await asyncio.gather(
                    forward_upstream(upstream),
                    forward_downstream(upstream),
                )
        except Exception as exc:
            logger.warning("ComfyUI progress proxy failed: %s", exc)
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    @router.get(
        "/comfyui/workflows",
        summary="List ComfyUI workflows",
        description="Lists discovered built-in and user workflows with placeholder validation metadata.",
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
        summary="Delete user workflow",
        description="Deletes a workflow JSON from the user workflow directory.",
    )
    async def delete_comfyui_workflow(workflow_name: str):
        normalized = _normalize_workflow_name(workflow_name)
        if not normalized:
            raise HTTPException(status_code=400, detail="workflow_name is required")
        workflow_dir = _workflow_user_dir()
        path = os.path.join(workflow_dir, normalized)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="Workflow not found in user")
        try:
            os.remove(path)
        except OSError as exc:
            logger.warning("Failed to delete workflow %s: %s", normalized, exc)
            raise HTTPException(status_code=500, detail="Failed to delete workflow")
        return {"status": "success", "name": normalized}

    @router.post(
        "/comfyui/run_i2i",
        summary="Run ComfyUI image-to-image",
        description="Submits i2i prompts for one or more picture ids and imports generated outputs back into PixlStash.",
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
        if seed_mode == "fixed" and fixed_seed is not None:
            _apply_fixed_seed(workflow_instance, int(fixed_seed))
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
                    None,
                ),
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
        summary="Import ComfyUI workflow",
        description="Saves a workflow JSON into the user workflow directory, optionally overwriting an existing file.",
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
        path = os.path.join(workflow_dir, name)
        if os.path.exists(path) and not overwrite:
            raise HTTPException(status_code=409, detail="Workflow already exists")

        _save_workflow_json(path, workflow)
        return {
            "status": "success",
            "name": name,
            "workflow_dir": workflow_dir,
        }

    return router
