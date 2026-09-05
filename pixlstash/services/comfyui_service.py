"""Service layer for ComfyUI workflow execution and output import.

Extracted from ``pixlstash/routes/comfyui.py`` to keep the route handlers thin
(backend refactor Phase 2 §4.5). Owns the orchestration that talks to a ComfyUI
host: uploading source images, submitting prompts, polling history, downloading
the produced images, and importing them back into PixlStash (stack placement,
face/set/project propagation, view-context assignment, abort).

The single-event import path ``_process_comfyui_outputs`` is a deliberate
exception to the origin-aware event envelope; its contract is documented on the
function itself and in ``docs/backend_architecture.md`` §15. Preserve it exactly.
"""

import json
import mimetypes
import os
import random
import time
import uuid
from datetime import datetime

import requests
from fastapi import HTTPException
from sqlmodel import select

from pixlstash.db_models import (
    Character,
    Picture,
    PictureProjectMember,
    PictureSetMember,
    PictureStack,
    Tag,
    TAG_PENDING_SENTINEL,
)
from pixlstash.event_types import EventType
from pixlstash.services import import_dedup_service
from pixlstash.services.layout_move_service import resolve_placement
from pixlstash.services.comfyui_recipe_service import format_prompt_rejection
from pixlstash.services.set_lock_service import drop_locked_set_ids
from pixlstash.stacking import normalize_stack_positions
from pixlstash.utils.image_processing.image_utils import ImageUtils

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

SEED_FIELDS = {"noise_seed", "seed"}
SEED_NODE_CLASSES = {"RandomNoise", "KSampler", "KSamplerAdvanced"}

# Nodes from the ComfyUI-PixlStash pack that upload straight into the vault
# instead of writing a file for PixlStash to collect. Their history entry
# carries the ids they created under PIXLSTASH_IDS_KEY, and the images they do
# report are `type: "temp"` previews of pictures that are already imported.
PIXLSTASH_SAVER_CLASSES = frozenset({"PixlStashPictureSaver"})
PIXLSTASH_IDS_KEY = "picture_ids"

# Every class in the ComfyUI-PixlStash pack starts with this.
PIXLSTASH_NODE_PREFIX = "PixlStash"

# Every class that ends a graph with an image PixlStash can end up owning.
SAVE_NODE_CLASSES = frozenset({"SaveImage"}) | PIXLSTASH_SAVER_CLASSES


def _extract_history_entry(history_payload: dict, prompt_id: str) -> dict:
    if not isinstance(history_payload, dict):
        return {}
    if prompt_id in history_payload and isinstance(history_payload[prompt_id], dict):
        return history_payload[prompt_id]
    if "status" in history_payload or "outputs" in history_payload:
        return history_payload
    return {}


def _extract_text_from_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        for item in value:
            text = _extract_text_from_value(item)
            if text:
                return text
        return ""
    if isinstance(value, dict):
        for key in (
            "exception_message",
            "error",
            "message",
            "details",
            "detail",
            "exception",
            "reason",
            "node_errors",
        ):
            if key in value:
                text = _extract_text_from_value(value.get(key))
                if text:
                    return text
        try:
            return json.dumps(value, ensure_ascii=True)
        except Exception:
            # Deliberate best-effort fallback (allowlisted in the except-hygiene
            # guardrail): a non-JSON-serialisable value is normal here, and
            # str(value) IS the intended result, not an error path to log.
            return str(value)
    return str(value)


def _extract_history_status_and_error(
    history_payload: dict, prompt_id: str
) -> tuple[str | None, str | None]:
    entry = _extract_history_entry(history_payload, prompt_id)
    status = entry.get("status") or {}
    status_str = None
    if isinstance(status, dict):
        raw_status = status.get("status_str") or status.get("status")
        if raw_status is not None:
            status_str = str(raw_status).strip().lower() or None

    error_text = ""
    message_items = status.get("messages") if isinstance(status, dict) else None
    if isinstance(message_items, list):
        for item in reversed(message_items):
            event_name = ""
            event_payload = None
            if isinstance(item, (list, tuple)) and item:
                event_name = str(item[0] or "").strip().lower()
                event_payload = item[1] if len(item) > 1 else None
            elif isinstance(item, dict):
                event_name = str(item.get("type") or "").strip().lower()
                event_payload = item
            if event_name in {
                "execution_error",
                "execution_failed",
                "error",
                "execution_interrupted",
            }:
                if status_str is None:
                    status_str = "error"
                error_text = _extract_text_from_value(event_payload)
                if error_text:
                    break

    if not error_text:
        for candidate in (
            entry.get("error"),
            entry.get("exception_message"),
            status.get("error") if isinstance(status, dict) else None,
            status.get("message") if isinstance(status, dict) else None,
            status.get("details") if isinstance(status, dict) else None,
        ):
            error_text = _extract_text_from_value(candidate)
            if error_text:
                break

    return status_str, (error_text or None)


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
    # Do NOT pass the graph under extra_data.extra_pnginfo.workflow: that PNG
    # chunk is where the ComfyUI frontend stores the *UI* node graph, and the
    # frontend feeds it to loadGraphData unguarded when an image is dropped on
    # the canvas. Embedding our API-format graph there breaks drag-back-in
    # (issue #628). ComfyUI itself always writes the correct ``prompt`` chunk
    # (the executed API graph), which is what recipe replay and workflow
    # display read.
    payload = {
        "prompt": clean_workflow,
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
        raw_detail = (response.text or "").strip()
        # ComfyUI answers a validation failure with a structured body naming the
        # offending node and input. That is the only authoritative account of why
        # a graph will not run, so surface it rather than a JSON dump.
        structured = None
        try:
            structured = format_prompt_rejection(response.json())
        except ValueError:
            logger.debug("ComfyUI prompt error body was not JSON: %s", raw_detail[:200])
        detail = structured or raw_detail
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
        if node.get("class_type") in SAVE_NODE_CLASSES:
            save_nodes.append(str(node_id))
    return save_nodes


def graph_has_pixlstash_nodes(workflow: dict) -> bool:
    """True when *workflow* calls back into PixlStash from inside the graph.

    Every class in the ComfyUI-PixlStash pack is prefixed, so the prefix is the
    rule: a node added to the pack later is covered without editing this.

    Such a graph is a cycle - PixlStash runs ComfyUI, which calls PixlStash -
    and it carries **frozen ids**: the loaders serialise a choice as
    ``"<name> #<id>"``, so a replayed file re-applies whatever project, set,
    character or picture id was current when it was authored. That is fine for a
    template the owner picks now, and wrong for a recipe replay, where the ids
    can name a deleted project or one belonging to a different library.
    """
    if not isinstance(workflow, dict):
        return False
    return any(
        isinstance(node, dict)
        and isinstance(node.get("class_type"), str)
        and node["class_type"].startswith(PIXLSTASH_NODE_PREFIX)
        for node in workflow.values()
    )


def graph_has_pixlstash_saver(workflow: dict) -> bool:
    """True when *workflow* ends in a node that imports into the vault itself.

    Such a graph needs no ``filename_prefix`` tagging to be stacked: the saver
    reports the picture ids it created and ``_process_comfyui_outputs`` adopts
    them directly.
    """
    if not isinstance(workflow, dict):
        return False
    return any(
        isinstance(node, dict) and node.get("class_type") in PIXLSTASH_SAVER_CLASSES
        for node in workflow.values()
    )


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


def _iter_output_nodes(
    history_payload: dict,
    prompt_id: str,
    output_node_ids: list[str] | None,
):
    """Yield the ``outputs`` payload of each history node in scope."""
    outputs = {}
    if isinstance(history_payload, dict):
        if "outputs" in history_payload:
            outputs = history_payload.get("outputs") or {}
        elif prompt_id in history_payload:
            outputs = history_payload.get(prompt_id, {}).get("outputs") or {}

    if not isinstance(outputs, dict):
        return

    node_filter = set(output_node_ids or [])
    for node_id, node_payload in outputs.items():
        if node_filter and str(node_id) not in node_filter:
            continue
        if not isinstance(node_payload, dict):
            continue
        yield node_payload


def _extract_pixlstash_picture_ids(
    history_payload: dict,
    prompt_id: str,
    output_node_ids: list[str] | None,
) -> list[int] | None:
    """Picture ids a PixlStash saver node imported, or None if none ran.

    The empty list is meaningful and distinct from None: the node ran but every
    image it uploaded was a duplicate of one already in the vault, so there is
    nothing new to stack - and nothing to gain from downloading its previews.
    """
    ids: list[int] | None = None
    for node_payload in _iter_output_nodes(history_payload, prompt_id, output_node_ids):
        if PIXLSTASH_IDS_KEY not in node_payload:
            continue
        if ids is None:
            ids = []
        for value in node_payload.get(PIXLSTASH_IDS_KEY) or []:
            # The node joins its ids into one comma-separated string so that
            # downstream ComfyUI nodes can consume them as a STRING.
            for part in str(value).split(","):
                part = part.strip()
                if part.isdigit():
                    ids.append(int(part))
    return ids


def _extract_comfyui_output_images(
    history_payload: dict,
    prompt_id: str,
    output_node_ids: list[str] | None,
) -> list[dict]:
    images = []
    for node_payload in _iter_output_nodes(history_payload, prompt_id, output_node_ids):
        if PIXLSTASH_IDS_KEY in node_payload:
            # A PixlStash saver's images are temp previews of pictures it has
            # already imported. Downloading them would re-import a duplicate.
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
    timeout_s: float = 300.0,
    poll_s: float = 1.0,
) -> tuple[list[dict], list[int] | None]:
    """Poll history until the prompt produces output.

    Returns the images to download and import, plus the picture ids a PixlStash
    saver node imported on its own (None when no such node ran).
    """
    # 5 min budget covers cold model loading plus generation before giving up.
    deadline = time.time() + timeout_s
    last_images = []
    while time.time() < deadline:
        history_payload = _fetch_comfyui_history(base_url, prompt_id)
        images = _extract_comfyui_output_images(
            history_payload, prompt_id, output_node_ids
        )
        pixlstash_ids = _extract_pixlstash_picture_ids(
            history_payload, prompt_id, output_node_ids
        )
        status_str, error_text = _extract_history_status_and_error(
            history_payload, prompt_id
        )
        if images or pixlstash_ids is not None:
            return images, pixlstash_ids
        if status_str in {"error", "failed", "failure", "interrupted", "cancelled"}:
            raise RuntimeError(error_text or f"ComfyUI status={status_str}")
        if error_text and status_str != "success":
            raise RuntimeError(error_text)
        last_images = images
        time.sleep(poll_s)
    return last_images, None


def _emit_comfyui_failure_progress(server, prompt_id: str, message: str) -> None:
    try:
        server.vault.notify(
            EventType.PLUGIN_PROGRESS,
            {
                "plugin": "ComfyUI",
                "status": "failed",
                "run_id": f"comfyui-{prompt_id}",
                "message": str(message or "ComfyUI failed"),
                "current": 0,
                "total": 0,
                "progress": 0,
            },
        )
    except Exception as exc:
        logger.debug("Failed to emit ComfyUI failure progress event: %s", exc)


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


def _unique_edit_filename(output_dir: str, stem: str, ext: str) -> str:
    """Return ``{stem}_edit{n}{ext}`` with the first n >= 1 that is unused in output_dir."""
    n = 1
    while True:
        candidate = f"{stem}_edit{n}{ext}"
        if not os.path.exists(os.path.join(output_dir, candidate)):
            return candidate
        n += 1


def _import_comfyui_outputs(
    server,
    image_entries: list[tuple[bytes, str]],
    output_dir: str | None = None,
    reference_folder_id: int | None = None,
    source_file_stem: str | None = None,
) -> tuple[list[int], list[int]]:
    if not image_entries:
        return [], []

    fingerprints = [
        (
            ImageUtils.calculate_hash_from_bytes(img_bytes),
            len(img_bytes),
            ImageUtils.calculate_full_hash_from_bytes(img_bytes),
        )
        for img_bytes, _ext in image_entries
    ]

    candidates = server.vault.db.run_immediate_read_task(
        import_dedup_service.load_match_candidates_in_session,
        [(sampled, size) for sampled, size, _full in fingerprints],
        False,
    )
    existing_map, _scrapheaped_map = import_dedup_service.partition_confirmed_matches(
        candidates, fingerprints, server.vault.image_root
    )

    # Placement on write (v1.11 Phase 4b). `None` whenever `output_dir` is set:
    # an edit written beside its original in a reference folder is already where
    # the owner's own tree put it. A generation with no project or set yet lands
    # in the unfiled folder and is filed by the engine one debounce after the
    # assignment below lands.
    subfolder = resolve_placement(server.vault.db, output_dir)

    new_picture_map = {}
    for (img_bytes, ext), fingerprint in zip(image_entries, fingerprints):
        if fingerprint in existing_map or fingerprint in new_picture_map:
            continue
        sampled_sha, _size_bytes, _full_sha = fingerprint
        if output_dir and source_file_stem:
            pic_uuid = _unique_edit_filename(output_dir, source_file_stem, ext)
        else:
            pic_uuid = f"{uuid.uuid4()}{ext}"
        new_picture_map[fingerprint] = ImageUtils.create_picture_from_bytes(
            image_root_path=server.vault.image_root,
            image_bytes=img_bytes,
            picture_uuid=pic_uuid,
            pixel_sha=sampled_sha,
            output_dir=output_dir,
            reference_folder_id=reference_folder_id,
            subfolder=subfolder,
        )
    new_pictures = list(new_picture_map.values())

    def import_task(session):
        if new_pictures:
            session.add_all(new_pictures)
            session.flush()
            for pic in new_pictures:
                session.add(Tag(tag=TAG_PENDING_SENTINEL, picture_id=pic.id))
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
    duplicate_ids = []
    seen_new = set()
    for fingerprint in fingerprints:
        pic = existing_map.get(fingerprint)
        if pic is not None and pic.id is not None:
            duplicate_ids.append(pic.id)
            continue
        if fingerprint in seen_new:
            pic = new_picture_map.get(fingerprint)
            if pic is not None and pic.id is not None:
                duplicate_ids.append(pic.id)
        else:
            seen_new.add(fingerprint)
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

        # Guarantee a contiguous 0-based ordering (and a position-0 leader for
        # the grid) regardless of any pre-existing NULL/gapped positions.
        normalize_stack_positions(session, stack_id)

        stack.updated_at = datetime.utcnow()
        session.add(stack)
        session.commit()

    server.vault.db.run_task(update_stack)


def _copy_set_and_project_assignments(
    server,
    source_picture_id: int | None,
    target_picture_ids: list[int],
) -> None:
    if not source_picture_id or not target_picture_ids:
        return

    def copy_task(session):
        # A locked set's membership cannot change. This is a *propagation* path -
        # the user asked for a generation, not to edit the set - so a locked
        # source set is skipped (and logged) rather than failing the whole
        # generation and discarding images already imported.
        source_set_ids = drop_locked_set_ids(
            session,
            [
                row.set_id
                for row in session.exec(
                    select(PictureSetMember).where(
                        PictureSetMember.picture_id == source_picture_id
                    )
                ).all()
            ],
            "copy generated outputs into the source picture's sets",
            picture_ids=target_picture_ids,
        )
        source_project_ids = [
            row.project_id
            for row in session.exec(
                select(PictureProjectMember).where(
                    PictureProjectMember.picture_id == source_picture_id
                )
            ).all()
        ]
        if not source_set_ids and not source_project_ids:
            return 0

        new_set_members = []
        new_project_members = []
        for target_id in target_picture_ids:
            existing_sets = {
                row.set_id
                for row in session.exec(
                    select(PictureSetMember).where(
                        PictureSetMember.picture_id == target_id
                    )
                ).all()
            }
            for set_id in source_set_ids:
                if set_id not in existing_sets:
                    new_set_members.append(
                        PictureSetMember(set_id=set_id, picture_id=target_id)
                    )
            existing_projects = {
                row.project_id
                for row in session.exec(
                    select(PictureProjectMember).where(
                        PictureProjectMember.picture_id == target_id
                    )
                ).all()
            }
            for project_id in source_project_ids:
                if project_id not in existing_projects:
                    new_project_members.append(
                        PictureProjectMember(
                            project_id=project_id, picture_id=target_id
                        )
                    )

        if new_set_members:
            session.add_all(new_set_members)
        if new_project_members:
            session.add_all(new_project_members)
        if new_set_members or new_project_members:
            session.commit()
        return len(new_set_members) + len(new_project_members)

    total = server.vault.db.run_task(copy_task)
    if total:
        logger.info(
            "Copied set/project assignments (%s entries) to %s picture(s) from %s",
            total,
            len(target_picture_ids),
            source_picture_id,
        )


def _assign_pictures_to_view_context(
    server,
    new_ids: list[int],
    set_id: int | None,
    project_id: int | None,
    character_id: int | None,
) -> None:
    """Assign newly generated pictures directly to the current view context.

    Called for T2I outputs when the user is browsing a specific set, project,
    or character.  Unlike _copy_set_and_project_assignments this works without
    a source picture to copy from.
    """
    if not new_ids:
        return
    if not any([set_id, project_id, character_id]):
        return

    def assign(session):
        # Resolve character → reference set so the picture appears in the
        # character view as well as the regular set view.
        effective_set_ids = [s for s in [set_id] if s is not None]
        if character_id is not None:
            char = session.get(Character, character_id)
            if char and char.reference_picture_set_id:
                ref_sid = char.reference_picture_set_id
                if ref_sid not in effective_set_ids:
                    effective_set_ids.append(ref_sid)

        # Same rule as _copy_set_and_project_assignments: adding the outputs to
        # the set the user happened to be viewing (or a character's reference
        # set) is propagation, not an explicit set edit, so a locked target is
        # skipped and logged instead of failing the generation.
        effective_set_ids = drop_locked_set_ids(
            session,
            effective_set_ids,
            "assign generated outputs to the active view's set",
            picture_ids=new_ids,
        )

        for pic_id in new_ids:
            existing_sets = {
                row.set_id
                for row in session.exec(
                    select(PictureSetMember).where(
                        PictureSetMember.picture_id == pic_id
                    )
                ).all()
            }
            for sid in effective_set_ids:
                if sid not in existing_sets:
                    session.add(PictureSetMember(set_id=sid, picture_id=pic_id))

            if project_id is not None:
                existing_projects = {
                    row.project_id
                    for row in session.exec(
                        select(PictureProjectMember).where(
                            PictureProjectMember.picture_id == pic_id
                        )
                    ).all()
                }
                if project_id not in existing_projects:
                    session.add(
                        PictureProjectMember(project_id=project_id, picture_id=pic_id)
                    )
        session.commit()

    server.vault.db.run_task(assign)
    logger.info(
        "Assigned %s T2I picture(s) to view context (set=%s, project=%s, character=%s)",
        len(new_ids),
        set_id,
        project_id,
        character_id,
    )


def _set_source_picture_id_on_pictures(
    server,
    source_picture_id: int | None,
    target_picture_ids: list[int],
) -> None:
    if not source_picture_id or not target_picture_ids:
        return

    def update(session):
        for pid in target_picture_ids:
            pic = session.get(Picture, pid)
            if pic is not None:
                pic.source_picture_id = source_picture_id
                session.add(pic)
        session.commit()

    server.vault.db.run_task(update)


def _process_comfyui_outputs(
    server,
    base_url: str,
    prompt_id: str,
    output_node_ids: list[str] | None,
    stack_id: int | None,
    source_picture_id: int | None,
    view_context: dict | None = None,
    origin_generation: int | None = None,
    origin_library_uuid: str | None = None,
) -> None:
    """Poll ComfyUI for a prompt's outputs, import them, and emit ONE event.

    This is the documented single-event import path (see
    ``docs/backend_architecture.md`` §15). It is a deliberate exception to the
    origin-aware event envelope and its emission contract must be preserved
    byte-for-byte:

    - On success with newly imported pictures it emits exactly ONE
      ``EventType.PICTURE_IMPORTED`` event - never a second event, and none for
      already-existing re-imports (``duplicate_ids`` are intentionally ignored;
      they are already in the grid and need no event).
    - The payload carries ``source: "ui"`` and ``change_kind: "added"``.
    - It deliberately does NOT echo an ``origin_client_id``. In-app ComfyUI
      generation is UI-initiated but async: there is no optimistic client-side
      copy to suppress, so every owner tab (including the originator) performs a
      slick in-place insert rather than the originator suppressing its own echo.
      Externally-run ComfyUI arrives via the watch/reference finders, which stay
      external/null.

    Failures emit a ``PLUGIN_PROGRESS`` failure event via
    ``_emit_comfyui_failure_progress`` and never a ``PICTURE_IMPORTED`` event.
    """
    lease = None
    pinned_server = None

    def acquire_origin():
        coordinator = getattr(server, "library_coordinator", None)
        if coordinator is None:
            return None, server
        candidate = coordinator.acquire_read()
        if candidate is None:
            return None, None
        if (
            origin_generation is not None and candidate.generation != origin_generation
        ) or (
            origin_library_uuid is not None
            and candidate.library_uuid != origin_library_uuid
        ):
            coordinator.release_read(candidate)
            return None, None

        class PinnedServer:
            def __init__(self, original, vault):
                self._original = original
                self.vault = vault

            def __getattr__(self, name):
                return getattr(self._original, name)

        return candidate, PinnedServer(server, candidate.vault)

    def emit_failure_if_current(message: str) -> None:
        failure_lease, failure_server = acquire_origin()
        if failure_server is None:
            logger.info(
                "Discarding stale ComfyUI failure for prompt %s after library change",
                prompt_id,
            )
            return
        try:
            _emit_comfyui_failure_progress(failure_server, prompt_id, message)
        finally:
            if failure_lease is not None:
                server.library_coordinator.release_read(failure_lease)

    try:
        images, pixlstash_ids = _wait_for_comfyui_outputs(
            base_url, prompt_id, output_node_ids
        )
        if not images and pixlstash_ids is None:
            logger.warning("ComfyUI produced no outputs for prompt %s", prompt_id)
            emit_failure_if_current("ComfyUI finished without outputs.")
            return
        entries = []
        for entry in images:
            img_bytes, ext = _download_comfyui_image(base_url, entry)
            if img_bytes:
                entries.append((img_bytes, ext))

        lease, pinned_server = acquire_origin()
        if pinned_server is None:
            logger.info(
                "Discarding stale ComfyUI outputs for prompt %s after library change",
                prompt_id,
            )
            return

        output_dir: str | None = None
        ref_folder_id: int | None = None
        source_file_stem: str | None = None
        if source_picture_id is not None:
            src_pic = pinned_server.vault.db.run_immediate_read_task(
                lambda session: session.get(Picture, source_picture_id)
            )
            if (
                src_pic is not None
                and src_pic.reference_folder_id is not None
                and src_pic.file_path
                and os.path.isabs(src_pic.file_path)
            ):
                output_dir = os.path.dirname(src_pic.file_path)
                ref_folder_id = src_pic.reference_folder_id
                raw = src_pic.original_file_name or os.path.basename(src_pic.file_path)
                source_file_stem = os.path.splitext(raw)[0] if raw else None

        # Already-existing re-imports (`duplicate_ids`) are deliberately ignored:
        # they are already in the grid and need no event.
        new_ids, _duplicate_ids = _import_comfyui_outputs(
            pinned_server,
            entries,
            output_dir=output_dir,
            reference_folder_id=ref_folder_id,
            source_file_stem=source_file_stem,
        )
        if pixlstash_ids:
            # A PixlStash saver node uploaded these itself, so there is nothing
            # left to import - but everything below (stacking, source lineage,
            # set/project inheritance, the single import event) still has to
            # run, and it needs the ids the node reported.
            #
            # ponytail: assumes the node points at this vault, which is what
            # ComfyUI Settings is configured with. Cross-wiring it at a second
            # vault would adopt ids belonging to unrelated pictures; the fix is
            # for the node to report its target URL, not a heuristic here.
            new_ids = new_ids + [pid for pid in pixlstash_ids if pid not in new_ids]
        if stack_id and new_ids:
            _assign_outputs_to_stack_top(pinned_server, stack_id, new_ids)
        if new_ids:
            # Both I2I and T2I defer to the same mechanism: mark the output with
            # its source, let face extraction find the output's REAL faces, and
            # let SourceFaceLikenessTask inherit a character only where the two
            # faces actually match at >= 0.7.
            #
            # I2I used to copy the source's face rows outright, on the reasoning
            # that "positions are structurally similar". They are not reliably:
            # a bbox is pixel coordinates, and an I2I output at a different
            # resolution puts the source's numbers over a different region
            # entirely (on a much larger canvas they collapse into the top-left
            # corner and capture nothing). It also asserted the person is in the
            # output without looking at the output, which for a regenerated face
            # is a guess. Deferring costs one extraction pass and is correct.
            _set_source_picture_id_on_pictures(
                pinned_server, source_picture_id, new_ids
            )
            _copy_set_and_project_assignments(pinned_server, source_picture_id, new_ids)
        if new_ids and view_context:
            _assign_pictures_to_view_context(
                pinned_server,
                new_ids,
                set_id=view_context.get("set_id"),
                project_id=view_context.get("project_id"),
                character_id=view_context.get("character_id"),
            )

        if new_ids:
            # In-app ComfyUI generation is UI-initiated, but async: there is no
            # optimistic client-side copy to suppress. Emit `picture_imported`
            # with source "ui" and NO origin echo so every owner tab (including
            # the originator) performs a slick in-place insert rather than the
            # originator suppressing its own echo. Externally-run ComfyUI arrives
            # via the watch/reference finders, which stay external/null.
            pinned_server.vault.notify(
                EventType.PICTURE_IMPORTED,
                {
                    "ids": new_ids,
                    "source": "ui",
                    "change_kind": "added",
                },
            )
    except RuntimeError as exc:
        logger.warning("ComfyUI prompt %s failed before outputs: %s", prompt_id, exc)
        if pinned_server is not None:
            _emit_comfyui_failure_progress(pinned_server, prompt_id, str(exc))
        else:
            emit_failure_if_current(str(exc))
    except Exception as exc:
        logger.warning("Failed to import ComfyUI outputs: %s", exc)
        if pinned_server is not None:
            _emit_comfyui_failure_progress(pinned_server, prompt_id, str(exc))
        else:
            emit_failure_if_current(str(exc))
    finally:
        if lease is not None:
            server.library_coordinator.release_read(lease)


def _comfyui_abort(base_url: str) -> dict:
    """Interrupt the currently running ComfyUI execution and clear the queue.

    Calls ComfyUI's ``POST /interrupt`` to stop the active run, then
    ``POST /queue`` with ``{"clear": true}`` to remove pending items.
    Returns a dict with ``interrupted`` and ``queue_cleared`` booleans.
    """
    result = {"interrupted": False, "queue_cleared": False}
    try:
        resp = requests.post(f"{base_url}/interrupt", timeout=10)
        result["interrupted"] = resp.status_code < 300
        if not result["interrupted"]:
            logger.warning(
                "ComfyUI /interrupt returned %s: %s",
                resp.status_code,
                (resp.text or "").strip()[:200],
            )
    except requests.RequestException as exc:
        logger.warning("ComfyUI /interrupt request failed: %s", exc)

    try:
        resp = requests.post(f"{base_url}/queue", json={"clear": True}, timeout=10)
        result["queue_cleared"] = resp.status_code < 300
        if not result["queue_cleared"]:
            logger.warning(
                "ComfyUI /queue clear returned %s: %s",
                resp.status_code,
                (resp.text or "").strip()[:200],
            )
    except requests.RequestException as exc:
        logger.warning("ComfyUI /queue clear request failed: %s", exc)

    return result
