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

import ipaddress
import json
import mimetypes
import ntpath
import os
import posixpath
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from urllib3.util import parse_url

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
from pixlstash.services.move_reconciliation_service import add_person
from pixlstash.services.comfyui_recipe_service import (
    VIDEO_SAVE_CLASSES,
    format_prompt_rejection,
)
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

# The savers that write a file under their own ``filename_prefix`` and report
# it in the prompt's history, for PixlStash to download.
FILE_SAVE_NODE_CLASSES = frozenset({"SaveImage"}) | VIDEO_SAVE_CLASSES

# Every class that ends a graph with a picture or video PixlStash can end up
# owning.
SAVE_NODE_CLASSES = FILE_SAVE_NODE_CLASSES | PIXLSTASH_SAVER_CLASSES

# Where VideoHelperSuite's combine lists the file it wrote; every ComfyUI saver
# lists its own, a video included, under ``images``.
VHS_FILES_KEY = "gifs"

# Requests that vet or link a ComfyUI go straight to the address asked.
# ``requests`` otherwise honours HTTP_PROXY / HTTPS_PROXY / ALL_PROXY from the
# environment, and a proxy is a host nobody checked. A ``None`` value drops the
# environment's entry for that key, so ``all`` must be named as well.
NO_PROXY = {"http": None, "https": None, "all": None}

# How many followed prompts' endings are kept for GET /workflows/runs/{id}.
MAX_RUN_OUTCOMES = 500
_run_outcomes: dict[str, dict] = {}
_run_outcomes_lock = threading.Lock()

# The most of ComfyUI's own exception text a failure sentence carries: a
# state_dict size mismatch runs to kilobytes, and the sentence is drawn on a
# card and spoken as part of its name.
MAX_FAILURE_REASON = 300

# Bumped each time PixlStash itself has cleared ComfyUI's queue, so every poller
# asks the queue about its prompt at once instead of at its next check.
_queue_cleared = 0


class ComfyUIRunStopped(RuntimeError):
    """A run that was stopped (interrupted, or taken off ComfyUI's queue)
    rather than one a node failed in."""


def record_run_outcome(
    prompt_id: str,
    status: str,
    message: str | None = None,
    stopped: bool = False,
    picture_ids: list[int] | None = None,
    library_uuid: str | None = None,
) -> None:
    """Remember how a followed prompt stands: ``running``, ``completed`` or
    ``failed``. In memory, the newest ``MAX_RUN_OUTCOMES``.

    *library_uuid* is the library the run was started in, given when it is
    queued and kept by every later update: its picture ids mean nothing in
    another library. A run still going is the last to be dropped.
    """
    with _run_outcomes_lock:
        previous = _run_outcomes.pop(prompt_id, None) or {}
        _run_outcomes[prompt_id] = {
            "prompt_id": prompt_id,
            "status": status,
            "message": message,
            "stopped": stopped,
            "picture_ids": list(picture_ids or []),
            "library_uuid": library_uuid or previous.get("library_uuid"),
        }
        while len(_run_outcomes) > MAX_RUN_OUTCOMES:
            ended = (
                key
                for key, outcome in _run_outcomes.items()
                if outcome["status"] != "running"
            )
            del _run_outcomes[next(ended, None) or next(iter(_run_outcomes))]


def run_outcome(prompt_id: str) -> dict | None:
    """What ``record_run_outcome`` last said of *prompt_id*, or None."""
    with _run_outcomes_lock:
        outcome = _run_outcomes.get(prompt_id)
        return dict(outcome) if outcome else None


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


def _node_label(entry: dict, event: dict) -> str:
    """``Title (Class)`` for the node an execution event names.

    The class is the event's ``node_type``; the title is the node's own in the
    graph the history entry carries (``prompt[2]``), left out where it only
    repeats the class. Empty when the event names no node.
    """
    class_type = str(event.get("node_type") or "").strip()
    prompt = entry.get("prompt")
    graph = prompt[2] if isinstance(prompt, (list, tuple)) and len(prompt) > 2 else None
    node = graph.get(str(event.get("node_id"))) if isinstance(graph, dict) else None
    title = ""
    if isinstance(node, dict):
        meta = node.get("_meta")
        title = str((meta.get("title") if isinstance(meta, dict) else "") or "").strip()
        class_type = class_type or str(node.get("class_type") or "").strip()
    if title and class_type and title != class_type:
        return f"{title} ({class_type})"
    return title or class_type


def _describe_execution_event(entry: dict, name: str, event) -> str:
    """Which node ended a prompt and why, from ComfyUI's own account of it.

    ``Load Diffusion Model (UNETLoader) failed: KeyError 'asym_w4a8_int8'`` for
    an ``execution_error``, ``Interrupted at <node>`` for an
    ``execution_interrupted``. Empty when the event does not say enough, and
    the caller falls back to whatever text it holds.
    """
    if not isinstance(event, dict):
        return ""
    node = _node_label(entry, event)
    if name == "execution_interrupted":
        return f"Interrupted at {node}" if node else "Interrupted"
    if name != "execution_error":
        return ""
    # The exception's message alone can be a bare key ("'asym_w4a8_int8'" for
    # a KeyError), so its type goes in front. One line: a traceback-style
    # message would otherwise fill the card it is shown on.
    reason = " ".join(
        " ".join(str(event.get(key) or "").split())
        for key in ("exception_type", "exception_message")
        if str(event.get(key) or "").strip()
    )
    if len(reason) > MAX_FAILURE_REASON:
        reason = reason[:MAX_FAILURE_REASON].rstrip() + "…"
    if not node:
        return reason
    return f"{node} failed: {reason}" if reason else f"{node} failed"


def _extract_history_status_and_error(
    history_payload: dict, prompt_id: str
) -> tuple[str | None, str | None]:
    """A prompt's status and, where it ended badly, the sentence that says why.

    An interrupted prompt answers ``interrupted`` whatever ComfyUI's own
    ``status_str`` (it files one under ``error``), so the caller can tell a
    stopped run from a failed one.
    """
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
                if event_name == "execution_interrupted":
                    status_str = "interrupted"
                elif status_str is None:
                    status_str = "error"
                error_text = _describe_execution_event(
                    entry, event_name, event_payload
                ) or _extract_text_from_value(event_payload)
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


def _upload_image_to_comfyui(
    base_url: str, file_path: str, upload_name: str | None = None
) -> str:
    """Upload a picture to ComfyUI's input folder, returning the name to load it by.

    ``upload_name`` names it there instead of the file's own name.

    The only code that POSTs to ComfyUI's ``/upload/image``, so the only way a
    picture reaches a run: ``POST /workflows/run`` calls it to fill a card's
    picture inputs (#1457), once per distinct picture and only after every
    refusal has been decided.
    """
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "application/octet-stream"
    with open(file_path, "rb") as handle:
        files = {
            "image": (upload_name or os.path.basename(file_path), handle, mime_type),
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
    # `is_changed` is ComfyUI's own cache annotation, copied into every prompt
    # chunk it writes. ComfyUI recomputes it, and an unreadable file leaves it
    # NaN, which no JSON request can carry: drop it rather than send it back.
    clean_workflow = {
        k: (
            {key: value for key, value in v.items() if key != "is_changed"}
            if isinstance(v, dict)
            else v
        )
        for k, v in workflow.items()
        if not k.startswith("pixlstash_")
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


# What a run of each ComfyUI-PixlStash node needs (#1521). A class of the
# pack that is not named below is refused, so a node added to the pack later
# stays refused until it has an entry here. The problems each entry answers are
# in ``docs/backend_architecture.md`` §4.
#
# A loader that names a project, set or character serialises its choice as
# ``"<name> #<id>"`` in this widget, and the id is frozen: it runs only when
# the id is one this library has.
PIXLSTASH_LIBRARY_LOADERS: dict[str, tuple[str, str]] = {
    "PixlStashProjectLoader": ("pixlstash_project", "project"),
    "PixlStashSetLoader": ("pixlstash_set", "set"),
    "PixlStashCharacterLoader": ("pixlstash_character", "character"),
}
# Allowed wherever they appear. The model loaders address a file by its
# digest, which is the same on every hub; the searches and gates read the
# library through ids wired from a loader above, and write nothing.
PIXLSTASH_ALLOWED_NODES = frozenset(
    {
        "PixlStashAdapterLoader",
        "PixlStashVAELoader",
        "PixlStashCLIPLoader",
        "PixlStashLikenessSearch",
        "PixlStashSemanticSearch",
        "PixlStashFaceLikenessGate",
        "PixlStashPictureLikenessGate",
    }
)
# Sources its own input: baked ``picture_ids``, or its own sort and filters
# when that is empty. Allowed only where the run writes the ids in itself.
PIXLSTASH_PICTURE_LOADER = "PixlStashPictureLoader"
# Names a shelf row id, which is per-hub. Allowed only from a stored workflow
# file, which was authored against this hub's shelf.
PIXLSTASH_CHECKPOINT_LOADER = "PixlStashCheckpointLoader"

# The pack's own rule for reading the id out of a loader's choice.
_LIBRARY_ID_RE = re.compile(r"#(\d+)\s*$")


def _pixlstash_nodes(workflow: dict) -> list[tuple[str, dict]]:
    """Every ComfyUI-PixlStash node in *workflow*, as ``(node_id, node)``.

    Every class in the pack is prefixed, so the prefix is the rule: a node
    added to the pack later is found without editing this, and refused as
    ``no_policy`` until it has an entry above.
    """
    if not isinstance(workflow, dict):
        return []
    return [
        (str(node_id), node)
        for node_id, node in workflow.items()
        if isinstance(node, dict)
        and isinstance(node.get("class_type"), str)
        and node["class_type"].startswith(PIXLSTASH_NODE_PREFIX)
    ]


def _library_choice(node: dict) -> tuple[str, int | None | bool, str]:
    """``(kind, id, name)`` a library loader names: id ``None`` for no id,
    ``False`` for a value that cannot be read (a link, where the choice is made
    elsewhere)."""
    field, kind = PIXLSTASH_LIBRARY_LOADERS[node["class_type"]]
    value = (node.get("inputs") or {}).get(field, "")
    if not isinstance(value, str):
        return kind, False, ""
    match = _LIBRARY_ID_RE.search(value)
    if not match:
        return kind, None, ""
    return kind, int(match.group(1)), value[: match.start()].strip()


def library_ids_named(workflow: dict) -> dict[str, set[int]]:
    """Every project, set and character id the graph's loaders name, by kind.

    What the caller looks up in the vault before asking
    :func:`pixlstash_node_refusals`, which is pure.
    """
    named: dict[str, set[int]] = {}
    for _node_id, node in _pixlstash_nodes(workflow):
        if node["class_type"] not in PIXLSTASH_LIBRARY_LOADERS:
            continue
        kind, library_id, _name = _library_choice(node)
        if library_id is not None and library_id is not False:
            named.setdefault(kind, set()).add(library_id)
    return named


def pixlstash_node_refusals(
    workflow: dict,
    *,
    library_ids: dict[str, dict[int, str]] | None = None,
    picture_loader: bool = False,
    from_file: bool = False,
) -> list[dict]:
    """Every ComfyUI-PixlStash node in *workflow* that may not run, and why.

    Each entry is ``{node_id, class_type, title, why}``; ``why`` is one of
    ``not_in_library`` (with ``kind`` and ``id``), ``unreadable_id``,
    ``picks_its_own_picture``, ``per_hub_checkpoint``, ``imports_itself`` and
    ``no_policy``. Empty means every such node may run.

    The defaults are the strictest answer, so a caller that says nothing about
    its run refuses rather than allows.

    Args:
        workflow: The API-format graph, as it will be submitted.
        library_ids: The ids :func:`library_ids_named` found that this library
            has, by kind, each with its name. A loader runs only when both
            match: ids start at 1 in every library, so an id alone would run
            another library's "Portraits #3" against this one's project 3.
        picture_loader: Whether the run writes the picture loader's ids itself
            (a card run's picture inputs). The caller then owns refusing a
            loader it did not feed.
        from_file: Whether the graph is a stored workflow file.
    """
    present = library_ids or {}
    refused = []
    for node_id, node in _pixlstash_nodes(workflow):
        class_type = node["class_type"]
        why: dict = {}
        if class_type in PIXLSTASH_ALLOWED_NODES:
            continue
        if class_type in PIXLSTASH_LIBRARY_LOADERS:
            kind, library_id, name = _library_choice(node)
            if library_id is False:
                why = {"why": "unreadable_id", "kind": kind}
            elif library_id is not None and (
                library_id not in present.get(kind, {})
                or (name and present[kind][library_id] != name)
            ):
                why = {"why": "not_in_library", "kind": kind, "id": library_id}
        elif class_type == PIXLSTASH_PICTURE_LOADER:
            if not picture_loader:
                why = {"why": "picks_its_own_picture"}
        elif class_type == PIXLSTASH_CHECKPOINT_LOADER:
            if not from_file:
                why = {"why": "per_hub_checkpoint"}
        elif class_type in PIXLSTASH_SAVER_CLASSES:
            # Never submitted: swap_pixlstash_savers takes it out first.
            why = {"why": "imports_itself"}
        else:
            why = {"why": "no_policy"}
        if why:
            refused.append(
                {
                    "node_id": node_id,
                    "class_type": class_type,
                    "title": (node.get("_meta") or {}).get("title") or class_type,
                    **why,
                }
            )
    return refused


def unfed_picture_loaders(workflow: dict, fed: set[str]) -> list[dict]:
    """The picture loaders a run allowed but did not write ids into.

    The other half of ``picture_loader=True``: such a loader would read the
    ids baked into the file, or pick pictures by its own sort, so it is refused
    the way :func:`pixlstash_node_refusals` would have.
    """
    return [
        {
            "node_id": node_id,
            "class_type": node["class_type"],
            "title": (node.get("_meta") or {}).get("title") or node["class_type"],
            "why": "picks_its_own_picture",
        }
        for node_id, node in _pixlstash_nodes(workflow)
        if node["class_type"] == PIXLSTASH_PICTURE_LOADER and node_id not in fed
    ]


def swap_pixlstash_savers(workflow: dict) -> list[str]:
    """Replace every ComfyUI-PixlStash saver with ``SaveImage``, in place.

    The saver imports its outputs itself, which competes with the import the
    run is already doing and applies the project, set and character ids baked
    into the file. ``SaveImage`` on the same images and prefix leaves the run's
    own import as the only one. The node keeps its id and title, so an explicit
    ``pixlstash_output_nodes`` still names it. A saver whose output another
    node reads is left alone, since ``SaveImage`` has none to hand on.

    Returns:
        The ids of the nodes swapped.
    """
    read = {
        str(value[0])
        for node in workflow.values()
        if isinstance(node, dict)
        for value in (node.get("inputs") or {}).values()
        if isinstance(value, list) and len(value) == 2
    }
    swapped = []
    for node_id, node in _pixlstash_nodes(workflow):
        if node["class_type"] not in PIXLSTASH_SAVER_CLASSES:
            continue
        if node_id in read:
            # Something reads its `picture_ids` output, which SaveImage does
            # not have: left as it is, and refused as `imports_itself`.
            continue
        inputs = node.get("inputs") or {}
        if inputs.get("images") is None:
            # Nothing to hand to SaveImage: left as it is, and refused as
            # `imports_itself` rather than queued as a SaveImage ComfyUI
            # then rejects.
            continue
        node["class_type"] = "SaveImage"
        node["inputs"] = {
            "images": inputs.get("images"),
            "filename_prefix": inputs.get("filename_prefix") or "PixlStash",
        }
        swapped.append(node_id)
    return swapped


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
            detail="ComfyUI stopped answering before the run finished",
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
        # A VideoHelperSuite node with `save_output` off is a preview: it
        # writes a `temp` file its author chose not to keep, so it is not
        # collected. (A `temp` under `images` is: an explicit
        # `pixlstash_output_nodes` choice may name a preview node.)
        kept = [
            entry
            for entry in node_payload.get(VHS_FILES_KEY) or []
            if isinstance(entry, dict) and entry.get("type") != "temp"
        ]
        for image in [*(node_payload.get("images") or []), *kept]:
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


def _comfyui_prompt_queued(base_url: str, prompt_id: str) -> bool:
    """Whether ComfyUI still holds *prompt_id*, running or waiting its turn.

    True when the queue cannot be read: only an answer that does not list the
    prompt says it is gone, and one slow answer from a busy ComfyUI must not
    abandon a run that is still going. A ComfyUI that is down ends the wait
    through the history read instead, which raises.
    """
    try:
        response = requests.get(f"{base_url}/queue", timeout=30)
        response.raise_for_status()
        queue = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "ComfyUI queue could not be read for prompt %s, so it is taken as "
            "still queued: %s",
            prompt_id,
            exc,
        )
        return True
    if not isinstance(queue, dict):
        logger.warning(
            "ComfyUI /queue returned %s, not an object, so prompt %s is taken "
            "as still queued",
            type(queue).__name__,
            prompt_id,
        )
        return True
    # Each entry is ``[number, prompt_id, prompt, ...]``.
    return any(
        isinstance(item, (list, tuple)) and len(item) > 1 and item[1] == prompt_id
        for key in ("queue_running", "queue_pending")
        for item in queue.get(key) or []
    )


def _wait_for_comfyui_outputs(
    base_url: str,
    prompt_id: str,
    output_node_ids: list[str] | None,
    timeout_s: float = 30.0,
    poll_s: float = 1.0,
) -> tuple[list[dict], list[int] | None]:
    """Poll history until the prompt produces output.

    *timeout_s* is how often ComfyUI's queue is asked whether it still holds
    the prompt, not a cap on the run: a video, or a prompt behind others in
    the queue, takes longer than any fixed budget, so while ComfyUI still lists
    the prompt as running or pending the wait goes on.

    Returns the files to download and import, plus the picture ids a PixlStash
    saver node imported on its own (None when no such node ran).

    Raises ``ComfyUIRunStopped`` for a prompt that was interrupted, and for one
    ComfyUI holds neither in its queue nor in its history: taken off the queue
    before its turn, or lost to a restart. Any other failure is a
    ``RuntimeError`` carrying the sentence to show.

    ponytail: one ``GET /queue`` per followed prompt per *timeout_s*, and the
    answer carries every queued graph. Fine for tens of prompts; a 200-run
    batch wants one shared queue read instead of one per poller.
    """
    deadline = time.time() + timeout_s
    cleared = _queue_cleared
    while True:
        # Asked BEFORE the history read, so a prompt that finishes in between
        # is still collected by this pass rather than given up on.
        gone = False
        if cleared != _queue_cleared:
            # PixlStash's own Abort just emptied the queue: say so now, not
            # up to *timeout_s* after the owner has moved on to the next run.
            cleared = _queue_cleared
            deadline = 0.0
        if time.time() >= deadline:
            if _comfyui_prompt_queued(base_url, prompt_id):
                deadline = time.time() + timeout_s
            else:
                gone = True
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
        if status_str == "success":
            # Finished with nothing to import: ComfyUI writes a prompt's outputs
            # and its status together, so polling on would only wait out the
            # timeout before saying the same thing.
            return [], None
        if status_str in {"interrupted", "cancelled"}:
            raise ComfyUIRunStopped(error_text or "Interrupted")
        if status_str in {"error", "failed", "failure"}:
            raise RuntimeError(error_text or f"ComfyUI status={status_str}")
        if error_text and status_str != "success":
            raise RuntimeError(error_text)
        if gone:
            if not _extract_history_entry(history_payload, prompt_id):
                raise ComfyUIRunStopped(
                    "ComfyUI no longer has this run: it was taken off the "
                    "queue, or ComfyUI restarted"
                )
            return [], None
        time.sleep(poll_s)


def _emit_comfyui_progress(
    server,
    prompt_id: str,
    status: str,
    message: str,
    progress: int = 0,
    workflow_id: str | None = None,
    stopped: bool = False,
) -> None:
    """Say how a prompt's run ended. *workflow_id* is the workflow that ran,
    so the failure can be shown on it; *stopped* marks a failure that is a
    run somebody stopped."""
    try:
        server.vault.notify(
            EventType.PLUGIN_PROGRESS,
            {
                "plugin": "ComfyUI",
                "status": status,
                "run_id": f"comfyui-{prompt_id}",
                "workflow_id": workflow_id,
                "stopped": stopped,
                "message": message,
                "current": 0,
                "total": 0,
                "progress": progress,
            },
        )
    except Exception as exc:
        logger.debug(
            "Failed to emit ComfyUI %s progress event for prompt %s: %s",
            status,
            prompt_id,
            exc,
        )


def _emit_comfyui_failure_progress(
    server,
    prompt_id: str,
    message: str,
    workflow_id: str | None = None,
    stopped: bool = False,
) -> None:
    _emit_comfyui_progress(
        server,
        prompt_id,
        "failed",
        str(message or "ComfyUI failed"),
        workflow_id=workflow_id,
        stopped=stopped,
    )


def _emit_comfyui_completed_progress(
    server, prompt_id: str, workflow_id: str | None = None
) -> None:
    """Say a prompt's run is over, once what it made is in the library.

    The tab following a run cannot learn this from ComfyUI's socket: ComfyUI
    sends ``execution_success`` and the closing ``executing`` only to the
    client a prompt named, and a run names none.
    """
    _emit_comfyui_progress(
        server, prompt_id, "completed", "ComfyUI complete", 100, workflow_id
    )


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
            now = datetime.now(timezone.utc)
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

        stack.updated_at = datetime.now(timezone.utc)
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


def _assign_outputs_to_lora_person(
    server, character_id: int | None, new_ids: list[int]
) -> None:
    """Say the run's pictures are of the person whose LoRA it loaded.

    A fresh output has no faces yet, so this is the deferral a drop onto a
    person uses (``Picture.pending_character_id``, which face extraction turns
    into the largest face it finds, and drops when it finds none). One whose
    faces are already in takes its largest unassigned one instead, and never a
    face that names somebody else.

    Projects are left alone, as every automatic naming of a face leaves them:
    the run's own destination decides where its pictures are filed.
    """
    if character_id is None or not new_ids:
        return

    def assign(session) -> tuple[int, int]:
        if session.get(Character, character_id) is None:
            logger.warning(
                "Person %s was deleted before the run's pictures %s could be "
                "linked to them.",
                character_id,
                new_ids,
            )
            return 0, 0
        named = deferred = 0
        for pic in session.exec(select(Picture).where(Picture.id.in_(new_ids))).all():
            if not add_person(session, pic, character_id):
                continue
            if pic.pending_character_id == character_id:
                deferred += 1
            else:
                named += 1
        session.commit()
        return named, deferred

    named, deferred = server.vault.db.run_task(assign)
    if named:
        # Face extraction announces the ones it names later.
        server.vault.notify(EventType.CHANGED_CHARACTERS)
        server.vault.notify(EventType.CHANGED_FACES)
    logger.info(
        "Person %s, whose LoRA the run loaded: %s of its %s picture(s) linked "
        "now, %s when their faces are read",
        character_id,
        named,
        len(new_ids),
        deferred,
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


def _set_run_workflow_id(
    server,
    workflow_id: str,
    picture_ids: list[int],
    version: int | None = None,
) -> None:
    """File the pictures a manual workflow's run made on that workflow.

    Only the ones this run imported or its saver reported: a duplicate was
    already in the library, made by something else, and keeps its filing.
    *version* is the version of the workflow's document the run submitted
    (``Picture.run_workflow_version``), written with the id. A picture the
    filename tag or the extraction already filed on this same workflow gets
    the version too: they learn the id before this poller does, never the
    version a Run submitted.
    """

    def update(session):
        for pid in picture_ids:
            pic = session.get(Picture, pid)
            if pic is None:
                continue
            # Written once: a saver can report a picture filed by another run.
            if pic.run_workflow_id is None:
                pic.run_workflow_id = workflow_id
            if (
                pic.run_workflow_id == workflow_id
                and pic.run_workflow_version is None
                and version is not None
            ):
                pic.run_workflow_version = version
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
    run_workflow_id: str | None = None,
    rejected: str | None = None,
    run_workflow_version: int | None = None,
    workflow_id: str | None = None,
    lora_character_id: int | None = None,
) -> None:
    """Poll ComfyUI for a prompt's outputs, import them, and say how it ended.

    *workflow_id* is the workflow the run was of, manual or automatic; it
    travels on the ending's event so a failure is shown on that workflow.
    Every ending is also recorded (``record_run_outcome``) for
    ``GET /workflows/runs/{prompt_id}``.

    *rejected* is ComfyUI's account of the outputs it dropped at validation
    while still accepting the prompt (``format_prompt_rejection``); a run that
    then produces nothing reports it instead of a bare "no outputs".

    *run_workflow_id* is the manual workflow that ran, whose pictures these
    are (``Picture.run_workflow_id``), and *run_workflow_version* the version
    of its document the run submitted (``Picture.run_workflow_version``). An output the watch folder imports
    before this poller sees it is filed by the tag ``_tag_for_workflow`` put
    in its filename instead.

    *lora_character_id* is the person whose LoRA the run loaded, whom its
    pictures are then of (``_assign_outputs_to_lora_person``). Like the
    workflow, only what this run imported or its saver reported: a duplicate
    keeps the people it has.

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
    Its ``message`` names the node that failed and why
    (``_describe_execution_event``), and ``stopped`` is true for a run that was
    interrupted or taken off ComfyUI's queue (``ComfyUIRunStopped``).

    A run that did not fail ends with a ``PLUGIN_PROGRESS`` ``completed`` event
    (``_emit_comfyui_completed_progress``), after the import event and also
    when every output was a duplicate: it is what takes the run's row out of
    the Tasks tab.
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

    def emit_failure(message: str, stopped: bool = False) -> None:
        """Say the run failed: on the library already pinned, else on the
        origin if it is still the current one."""
        record_run_outcome(prompt_id, "failed", message=message, stopped=stopped)
        failure_lease, failure_server = None, pinned_server
        if failure_server is None:
            failure_lease, failure_server = acquire_origin()
        if failure_server is None:
            logger.info(
                "Discarding stale ComfyUI failure for prompt %s after library change",
                prompt_id,
            )
            return
        try:
            _emit_comfyui_failure_progress(
                failure_server,
                prompt_id,
                message,
                workflow_id=workflow_id,
                stopped=stopped,
            )
        finally:
            if failure_lease is not None:
                server.library_coordinator.release_read(failure_lease)

    try:
        images, pixlstash_ids = _wait_for_comfyui_outputs(
            base_url, prompt_id, output_node_ids
        )
        if not images and pixlstash_ids is None:
            logger.warning("ComfyUI produced no outputs for prompt %s", prompt_id)
            emit_failure(rejected or "ComfyUI finished without outputs.")
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
            record_run_outcome(
                prompt_id,
                "failed",
                message="The library changed before this run's outputs were imported",
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
        if run_workflow_id and new_ids:
            _set_run_workflow_id(
                pinned_server, run_workflow_id, new_ids, run_workflow_version
            )
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
        # After the event: a failure here must not cost the pictures their
        # import event. Before the ending, so a run has one.
        _assign_outputs_to_lora_person(pinned_server, lora_character_id, new_ids)
        record_run_outcome(prompt_id, "completed", picture_ids=new_ids)
        _emit_comfyui_completed_progress(pinned_server, prompt_id, workflow_id)
    except ComfyUIRunStopped as exc:
        logger.warning("ComfyUI prompt %s was stopped: %s", prompt_id, exc)
        emit_failure(str(exc), stopped=True)
    except RuntimeError as exc:
        logger.warning("ComfyUI prompt %s failed before outputs: %s", prompt_id, exc)
        emit_failure(str(exc))
    except Exception as exc:
        logger.warning(
            "Failed to import ComfyUI outputs of prompt %s: %s", prompt_id, exc
        )
        # An HTTPException's str() is "502: <detail>"; the detail is the sentence.
        emit_failure(str(getattr(exc, "detail", None) or exc))
    finally:
        if lease is not None:
            server.library_coordinator.release_read(lease)


def _comfyui_abort(base_url: str) -> dict:
    """Interrupt the currently running ComfyUI execution and clear the queue.

    Calls ComfyUI's ``POST /interrupt`` to stop the active run, then
    ``POST /queue`` with ``{"clear": true}`` to remove pending items.
    Returns a dict with ``interrupted`` and ``queue_cleared`` booleans.
    """
    global _queue_cleared
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

    # Only a queue that was really emptied: the pollers are told to ask it
    # about their prompt now, and one that still holds them answers nothing new.
    if result["queue_cleared"]:
        _queue_cleared += 1
    return result


def comfyui_can_open_workflows(base_url: str) -> bool | None:
    """Whether ComfyUI can open a ``?pixlstash_workflow=<key>`` link.

    Only the ComfyUI-PixlStash node's ``open_workflow.js`` reads that
    parameter; without it ComfyUI opens on whatever it had last. ComfyUI's
    ``GET /extensions`` lists every script it hands the browser. ``None`` when
    ComfyUI cannot be asked.

    ponytail: matches the pack by a "pixlstash" folder name, which holds for the
    registry and git installs; ask the node for its version if that ever breaks.
    """
    try:
        response = requests.get(f"{base_url}/extensions", timeout=5)
        response.raise_for_status()
        scripts = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.info("Could not list ComfyUI extensions at %s: %s", base_url, exc)
        return None
    if not isinstance(scripts, list):
        logger.warning(
            "ComfyUI /extensions at %s returned %s, not a list",
            base_url,
            type(scripts).__name__,
        )
        return None
    return any(
        isinstance(script, str)
        and "pixlstash" in script.lower()
        and script.endswith("/open_workflow.js")
        for script in scripts
    )


def normalize_comfyui_url(url: str) -> str:
    """``scheme://host[:port][/path]/`` for an http(s) URL, else ``ValueError``.

    The trailing slash is the form ``comfyui_url`` has always been saved in. The
    scheme's default port (``:80``, ``:443``) is dropped.
    """
    text = str(url or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(
            f"A ComfyUI address needs http:// or https:// and a host, not {url!r}."
        )
    # Scheme, host, port and path only. A user part, a backslash, a query or a
    # fragment is where URL parsers disagree on the host: `urlsplit` reads
    # ``http://evil.example\\@127.0.0.1/`` as 127.0.0.1 while urllib3, which
    # sends the request, connects to evil.example. Refused, and the host is
    # then required to read the same to both.
    if any(mark in text for mark in "@\\?#") or parts.username is not None:
        raise ValueError(
            f"A ComfyUI address is scheme, host, port and path only, not {url!r}."
        )
    sent_to = (parse_url(text).host or "").strip("[]").lower()
    if sent_to != parts.hostname.lower():
        raise ValueError(f"The host in {url!r} is ambiguous.")
    netloc = parts.netloc
    # The scheme's own port is the same address without it; one spelling only.
    if parts.port == {"http": 80, "https": 443}[parts.scheme]:
        netloc = netloc.rsplit(":", 1)[0]
    return f"{parts.scheme}://{netloc}{parts.path.rstrip('/')}/"


def comfyui_listens_on_network(argv) -> bool | None:
    """Whether ComfyUI's ``--listen`` lets other computers reach it.

    Read from the ``argv`` ComfyUI's ``system_stats`` reports. ``--listen``
    alone means every interface (ComfyUI's ``0.0.0.0,::``); its value is a
    comma-separated list of addresses; without it ComfyUI listens on
    127.0.0.1. None when ComfyUI did not report its arguments.
    """
    if not isinstance(argv, list):
        return None
    args = [str(arg) for arg in argv]
    listen = None
    for i, arg in enumerate(args):
        if arg == "--listen":
            value = args[i + 1] if i + 1 < len(args) else ""
            listen = value if value and not value.startswith("-") else "0.0.0.0"
        elif arg.startswith("--listen="):
            listen = arg.split("=", 1)[1] or "0.0.0.0"
    if listen is None:
        return False
    # Not skipping an empty entry (``--listen 127.0.0.1,``): ComfyUI binds
    # every entry as given, and asyncio binds an empty host to every
    # interface, so it falls through to "reachable" below.
    for host in (h.strip().strip("[]") for h in listen.split(",")):
        if host.lower() == "localhost":
            continue
        try:
            if not ipaddress.ip_address(host).is_loopback:
                return True
        except ValueError:
            # A host name: it may name any interface, so it may be reachable.
            return True
    return False


def probe_comfyui(url: str, timeout: float = 2.0) -> dict:
    """Whether ComfyUI answers at *url*, asked from this server.

    ``GET /system_stats`` is ComfyUI's own and carries a ``system`` object, so
    another web server answering on the port is told apart from ComfyUI.
    Asked from the backend because the backend is what submits runs: an
    address the browser can reach and the server cannot is no use.

    Returns:
        ``{"reachable", "url", "version", "detail", "listens_on_network"}``:
        ``url`` normalised, ``version`` ComfyUI's own when it reports one,
        ``detail`` the reason when not reachable, written for the person who
        typed the address, ``listens_on_network`` from
        :func:`comfyui_listens_on_network` (None when not known).

    Raises:
        ValueError: *url* is not an http(s) URL with a host.
    """
    base = normalize_comfyui_url(url)
    where = urlsplit(base).netloc
    result = {
        "reachable": False,
        "url": base,
        "version": None,
        "detail": None,
        "listens_on_network": None,
    }
    try:
        # A redirect is not ComfyUI answering: it is another host's address.
        response = requests.get(
            f"{base}system_stats",
            timeout=timeout,
            allow_redirects=False,
            proxies=NO_PROXY,
        )
    except requests.exceptions.SSLError as exc:
        logger.info("ComfyUI probe of %s: TLS failed: %s", base, exc)
        result["detail"] = (
            f"{where} answered, but its HTTPS certificate could not be checked."
        )
        return result
    except requests.Timeout:
        logger.info("ComfyUI probe of %s: no answer within %ss", base, timeout)
        result["detail"] = f"{where} did not answer within {timeout:g} seconds."
        return result
    except requests.RequestException as exc:
        logger.info("ComfyUI probe of %s: %s", base, exc)
        result["detail"] = (
            f"Nothing answered at {where}. Check that ComfyUI is running and "
            "that the port matches the one in its console."
        )
        return result
    try:
        stats = response.json() if response.status_code == 200 else None
    except ValueError:
        stats = None
    system = stats.get("system") if isinstance(stats, dict) else None
    if not isinstance(system, dict):
        logger.info(
            "ComfyUI probe of %s: HTTP %s without ComfyUI's system_stats",
            base,
            response.status_code,
        )
        result["detail"] = f"Something answered at {where}, but it is not ComfyUI."
        return result
    version = system.get("comfyui_version")
    result["reachable"] = True
    result["version"] = str(version) if version else None
    result["listens_on_network"] = comfyui_listens_on_network(system.get("argv"))
    return result


def comfyui_folder_paths(base_url: str):
    """ComfyUI's ``GET /internal/folder_paths``, parsed; only a 200 counts.

    Never follows a redirect and never goes through a proxy: the answer names
    folders on this machine that PixlStash then scans or writes into.

    Raises:
        requests.RequestException: ComfyUI did not answer with a 200.
        ValueError: the answer is not JSON.
    """
    url = f"{base_url.rstrip('/')}/internal/folder_paths"
    response = requests.get(url, timeout=5, allow_redirects=False, proxies=NO_PROXY)
    if response.status_code != 200:
        raise requests.HTTPError(
            f"ComfyUI answered HTTP {response.status_code} at {url}"
        )
    return response.json()


def _path_module(path: str):
    """``ntpath`` for a Windows path (drive or UNC), else ``posixpath``.

    The paths are spelled by ComfyUI's filesystem, which need not be this one's.
    """
    return ntpath if re.match(r"^([A-Za-z]:[\\/]|\\\\)", path) else posixpath


def comfyui_model_roots(base_url: str) -> list[str]:
    """The folders ComfyUI loads models from, reduced to their roots.

    ComfyUI's ``GET /internal/folder_paths`` maps each model category to its
    directories (``extra_model_paths.yaml`` included), as ComfyUI's own
    filesystem spells them. A parent holding folders of two or more categories
    is a models root (``ComfyUI/models``) and is returned in their place; folders
    of one category alone under their parent are returned as themselves, so a
    ``loras:`` entry pointing straight at ``~/loras`` does not turn into the
    whole home folder. ``custom_nodes`` is skipped: its parent is the ComfyUI
    install, not models.

    Empty when ComfyUI cannot be asked. Whether a root is reachable from this
    machine is the caller's question.

    ponytail: ``/internal`` is ComfyUI's frontend API, not a stable one; if it
    moves, ask the PixlStash node pack to report ``folder_paths`` instead.
    """
    try:
        folder_paths = comfyui_folder_paths(base_url)
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "Could not read ComfyUI model folders from %s: %s", base_url, exc
        )
        return []
    if not isinstance(folder_paths, dict):
        logger.warning(
            "ComfyUI /internal/folder_paths at %s returned %s, not an object",
            base_url,
            type(folder_paths).__name__,
        )
        return []
    # parent -> {child folder -> the categories that use it}
    by_parent: dict[str, dict[str, set[str]]] = {}
    seps: dict[str, str] = {}
    for category, paths in folder_paths.items():
        if category == "custom_nodes" or not isinstance(paths, list):
            continue
        for path in paths:
            if isinstance(path, str) and path.strip():
                pathmod = _path_module(path)
                path = pathmod.normpath(path)
                seps[path] = pathmod.sep
                children = by_parent.setdefault(pathmod.dirname(path), {})
                children.setdefault(path, set()).add(category)
    roots = set()
    for parent, children in by_parent.items():
        categories = set().union(*children.values())
        if len(children) > 1 and len(categories) > 1:
            roots.add(parent)
            seps[parent] = seps[next(iter(children))]
        else:
            roots.update(children)
    return sorted(
        root
        for root in roots
        if not any(
            other != root and root.startswith(other.rstrip(seps[other]) + seps[other])
            for other in roots
        )
    )
