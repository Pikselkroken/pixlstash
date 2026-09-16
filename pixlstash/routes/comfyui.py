import asyncio
import functools
import json
import os
import sqlite3
import threading
import uuid
from copy import deepcopy
from urllib.parse import quote

import websockets
from fastapi import APIRouter, Body, HTTPException, Request, WebSocket
from fastapi.websockets import WebSocketDisconnect
from pydantic import BaseModel, ConfigDict
from send2trash import TrashPermissionError, send2trash
from sqlmodel import select

from typing import Any, Optional

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Picture,
    User,
)
from pixlstash.hub.workflows import (
    forget_input_modes,
    input_modes_by_workflow,
    parameter_pins,
    record_api_graph,
    record_ui_graph,
    replace_input_modes,
    replace_parameter_pins,
)
from pixlstash.utils.adapter_header import FILE_ADAPTER, FILE_UNKNOWN
from pixlstash.utils.comfyui_utilities import (
    collect_seed_inputs,
    extract_comfy_workflow_info,
    extract_generation_info,
    find_comfy_api_prompt,
    summarize_comfy_workflow,
)
from pixlstash.services.comfyui_recipe_service import (
    MAX_SEED_64,
    apply_adapter,
    apply_seeds,
    collect_node_classes,
    detect_lora_targets,
    detect_seed_targets,
    fetch_object_info,
    preflight_prompt,
    sanitize_prompt_graph,
    unchecked_preflight,
)
from pixlstash.services.model_shelf_service import (
    fetch_locations,
    fetch_model_by_hash,
)
from pixlstash.services.workflow_inputs import (
    FIXED,
    PICKER,
    SELECTION,
    resolve_input_modes,
    validate_requested_modes,
)
from pixlstash.services import (
    workflow_bindings,
    workflow_inbox,
    workflow_parameters,
)
from pixlstash.services.workflow_hash import WorkflowGraphError
from pixlstash.services.workflow_io import api_graph, detect_workflow_io
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


def workflow_user_dir() -> str:
    return os.path.join(user_data_dir("pixlstash"), "comfyui-workflows", "user")


def _workflow_dirs() -> list[tuple[str, str]]:
    return [
        ("user", workflow_user_dir()),
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


def _store_workflow(
    hub, name: str, workflow: dict, *, overwrite: bool = False, keep_both: bool = False
) -> dict:
    """Store *workflow* as *name* in the user folder, or match a stored copy.

    Shared by the import route and the watched inbox, so a dropped file and a
    file put in the folder are stored the same way.

    Raises:
        FileExistsError: *name* holds a different workflow and neither
            *overwrite* nor *keep_both* is set.
        RecursionError: The document nests too deeply to compare.
        ValueError: *name* escapes the user folder.
    """
    # A file exported while workflows carried placeholder tokens is stored
    # the way the start-up migration left its siblings, so it can run and
    # so a copy of a migrated workflow matches it.
    workflow, _migrated = workflow_bindings.migrate_placeholders(workflow)
    wanted = workflow_bindings.canonical(workflow)

    workflow_dir = workflow_user_dir()
    os.makedirs(workflow_dir, exist_ok=True)
    path = resolve_path_within(workflow_dir, name)

    # A copy is the same workflow whatever it is called, so it matches
    # before the name is looked at, and keep_both has nothing to keep.
    existing = _find_stored_copy(wanted)
    if existing is not None:
        return {
            "status": "success",
            "name": existing,
            "workflow_dir": workflow_dir,
            "matched": True,
            "topology_hash": _file_in_hub(hub, workflow),
        }
    if os.path.exists(path) and not overwrite:
        if not keep_both:
            raise FileExistsError(path)
        stem = os.path.splitext(name)[0]
        counter = 2
        while os.path.exists(path):
            name = f"{stem} ({counter}).json"
            path = resolve_path_within(workflow_dir, name)
            counter += 1

    _save_workflow_json(path, workflow)
    return {
        "status": "success",
        "name": name,
        "workflow_dir": workflow_dir,
        "matched": False,
        "topology_hash": _file_in_hub(hub, workflow),
    }


def _trash_stored_workflow(path: str, name: str) -> None:
    """Move a stored workflow to the system trash by way of the inbox.

    The trash copy comes first and the stored file goes only once it is there,
    so a failed trash never loses the workflow.
    """
    try:
        workflow = _load_workflow_json(path)
        if not isinstance(workflow, dict):
            raise ValueError("not a JSON object")
    except (ValueError, RecursionError) as exc:
        # Nothing to write back that the inbox could import; the file itself
        # is what goes to the trash.
        logger.warning(
            "Workflow %s is not a readable workflow, trashing the file as it is: %s",
            name,
            exc,
        )
        send2trash(path)
        return
    workflow_inbox.trash_workflow(workflow_inbox.workflow_inbox_dir(), name, workflow)
    os.remove(path)


def _find_stored_copy(wanted: str) -> str | None:
    """The name of a stored workflow whose canonical content is *wanted*."""
    for source, folder in _workflow_dirs():
        if not os.path.isdir(folder):
            continue
        for entry in sorted(os.listdir(folder)):
            if not entry.lower().endswith(".json"):
                continue
            path = os.path.join(folder, entry)
            try:
                stored = _load_workflow_json(path)
                if (
                    isinstance(stored, dict)
                    and workflow_bindings.canonical(stored) == wanted
                ):
                    return entry
            except (OSError, ValueError, RecursionError) as exc:
                logger.warning(
                    "Could not read %s workflow %s to compare an import: %s",
                    source,
                    path,
                    exc,
                )
    return None


def _file_in_hub(hub, workflow: dict) -> str | None:
    """File the workflow in the library, returning its topology hash.

    Content-addressed and idempotent, so a workflow the library already has
    from its pictures lands on that same row. Not being filed does not stop
    the import: the file is what runs.
    """
    if hub is None:
        return None
    try:
        if isinstance(workflow.get("nodes"), list):
            return record_ui_graph(hub, workflow)
        graph = workflow.get("prompt")
        if not isinstance(graph, dict):
            graph = workflow
        return record_api_graph(hub, graph).topology_hash
    except WorkflowGraphError as exc:
        logger.info(
            "Imported workflow is not filed in the library, its graph "
            "cannot be keyed: %s",
            exc,
        )
    except sqlite3.Error as exc:
        logger.error(
            "Could not file an imported workflow in the library; it is "
            "stored and runs, but the Workflows view will not list it: %s",
            exc,
        )
    except Exception as exc:
        # The graph reducers index into whatever the file holds, and a
        # malformed file (a non-dict `definitions`, say) raises something
        # other than WorkflowGraphError. Filing is secondary to storing, so
        # it is logged with its type and the import still succeeds.
        logger.error(
            "Imported workflow is not filed in the library, reading its "
            "graph failed with %s: %s",
            type(exc).__name__,
            exc,
        )
    return None


MAX_SEED = 2**32 - 1

# ponytail: one request submits its runs one after another, uploads included;
# a queue of its own (and a higher cap) if large batches become routine.
MAX_RUNS_PER_REQUEST = 200

# What a LoRA slot may be given: an adapter, or a file the header reader could
# not place, which on this shelf is usually an adapter in a format it has not
# been taught. Everything else it holds is refused by name (#1310).
LOADABLE_ADAPTER_FILE_KINDS = frozenset({FILE_ADAPTER, FILE_UNKNOWN})


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


def _view_context(payload: dict) -> dict | None:
    """The set, project and character a run with no source picture files into.

    An id that is not an integer is ignored, and logged.
    """
    context = {}
    for key in ("set_id", "project_id", "character_id"):
        raw = payload.get(key)
        if raw is None:
            continue
        try:
            context[key] = int(raw)
        except (TypeError, ValueError):
            logger.debug("Ignoring invalid %s value in a ComfyUI run: %r", key, raw)
    return context or None


def _int_list(raw, field: str) -> list[int]:
    """A list of integer ids from a request body, in order and without repeats.

    Raises:
        HTTPException: 400 when it is not a list of integers.
    """
    if raw is None:
        return []
    if not isinstance(raw, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in raw
    ):
        raise HTTPException(status_code=400, detail=f"{field} must be a list of ids")
    return list(dict.fromkeys(raw))


def _shelf_adapter(hub, sha256) -> dict:
    """The shelf model *sha256* names, as the names a LoRA slot can be given.

    Both names are collected because the two sides count from different roots:
    ComfyUI lists a file relative to its own ``loras`` folder and the shelf
    knows it relative to the folder PixlStash scanned, so the copy's ``relpath``
    is the one that matches an install sharing that tree and ``filename`` the
    one that matches anything else.

    What may be loaded is an **allow-list**: an adapter, or a file the shelf has
    not classified yet, which is first-class there and is usually an adapter the
    header reader could not place. Everything else the shelf holds - a
    checkpoint, a VAE, a text encoder, one of PixlStash's own engines - is
    refused by name, because writing any of them into ``lora_name`` produces a
    run that fails somewhere further in.

    Raises:
        HTTPException: 400 when *sha256* is not a string or names something no
            LoRA loader can load, 404 when the shelf does not have it, 503 when
            no hub is attached to ask.
    """
    if not isinstance(sha256, str) or not sha256.strip():
        raise HTTPException(
            status_code=400, detail="adapter_sha256 must be a model's SHA-256"
        )
    if hub is None:
        # Not a 404: the model may be perfectly well on the shelf, and saying it
        # is not would send the owner looking for a file that is right there.
        raise HTTPException(
            status_code=503,
            detail="No hub is attached, so PixlStash cannot look up that LoRA.",
        )
    digest = sha256.strip().lower()
    row = fetch_model_by_hash(hub, digest)
    if row is None:
        raise HTTPException(
            status_code=404, detail="That LoRA is not on this PixlStash's shelf."
        )
    if row["file_kind"] not in LOADABLE_ADAPTER_FILE_KINDS:
        logger.warning(
            "Refused a %s (model %s) for a LoRA slot", row["file_kind"], row["id"]
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"That hash is a {row['file_kind']}, which no LoRA loader can load."
            ),
        )
    model_id = int(row["id"])
    copies = fetch_locations(hub, model_id).get(model_id, [])
    return {
        "sha256": digest,
        "filenames": [
            name
            for name in [
                *(copy["relpath"] for copy in copies if copy.get("relpath")),
                row["filename"],
            ]
            if name
        ],
    }


def _resolve_lora_swap(
    hub,
    payload: dict,
    graph: dict,
    label: str,
    comfyui_url: str,
    object_info: dict | None = None,
) -> dict | None:
    """What a run's ``adapter_sha256`` resolves to, or ``None`` when it asks for none.

    Shared by the saved-workflow run and the recipe replay so the two refuse
    the same things in the same words (#1310). Nothing is written here: the
    caller applies the result to each instance it submits, and everything that
    can be refused has been refused before it does.

    Args:
        hub: The hub database, for the shelf lookup.
        payload: The run body; ``adapter_sha256`` and optional ``lora_node_id``.
        graph: The API-format graph the run will submit.
        label: What to call this run in a log line ("workflow x", "picture 12").
        comfyui_url: The ComfyUI this run goes to, asked for its file list when
            *object_info* is not given.
        object_info: A map already fetched for this run, or ``None`` to fetch
            one when a filename slot needs resolving. An empty map is not the
            same as ``None``: it means ComfyUI was asked and said nothing, which
            makes a filename slot refuse rather than guess.

    Returns:
        ``{"adapter", "targets", "object_info"}`` for :func:`apply_adapter`, or
        ``None`` when the body names no LoRA.

    Raises:
        HTTPException: 400 for a graph with no LoRA loader, an unnamed choice
            between several, a node that is not one of them, or a LoRA this
            ComfyUI cannot be given; 404/400/503 from :func:`_shelf_adapter`;
            502 when ComfyUI cannot be asked at all.
    """
    if payload.get("adapter_sha256") is None:
        return None
    adapter = _shelf_adapter(hub, payload["adapter_sha256"])
    targets = detect_lora_targets(graph)
    if not targets:
        logger.warning("%s was asked for a LoRA and has no loader to put one in", label)
        raise HTTPException(
            status_code=400,
            detail=(
                "This workflow has no LoRA loader, so there is nothing to put a "
                "LoRA into. Add one in ComfyUI and import it again."
            ),
        )
    # One slot, not all of them: a workflow chaining a style LoRA and a
    # character LoRA would otherwise come back loading the chosen file twice,
    # with the other one gone and nothing said about it.
    wanted_node = payload.get("lora_node_id")
    if wanted_node is None and len(targets) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This workflow has {len(targets)} LoRA loaders; name the one to "
                "swap as lora_node_id (nodes "
                + ", ".join(t["node_id"] for t in targets)
                + ")."
            ),
        )
    if wanted_node is not None:
        targets = [t for t in targets if t["node_id"] == str(wanted_node)]
        if not targets:
            raise HTTPException(
                status_code=400,
                detail=f"Node {wanted_node} is not a LoRA loader of this workflow.",
            )
    if object_info is None:
        object_info = {}
        if any(t["by"] == "filename" for t in targets):
            try:
                object_info = fetch_object_info(comfyui_url)
            except RuntimeError as exc:
                logger.warning("Could not read object_info for %s: %s", label, exc)
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "PixlStash could not ask ComfyUI which LoRA files it "
                        f"has, so the swap would be a guess: {exc}"
                    ),
                ) from exc
    # Applied once to a copy, the way a workflow's bindings are filled once: a
    # name this ComfyUI does not have is refused before a single picture is
    # uploaded, not between two runs of a batch.
    try:
        apply_adapter(deepcopy(graph), targets, adapter, object_info)
    except LookupError as exc:
        logger.warning("LoRA %s cannot go into %s: %s", adapter["sha256"], label, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"adapter": adapter, "targets": targets, "object_info": object_info}


def _apply_lora_swap(workflow_instance: dict, swap: dict, label: str) -> None:
    """Write a resolved swap into one instance about to be submitted.

    Cannot raise - :func:`_resolve_lora_swap` applied the same target to a copy
    of the same graph - but a zero write would mean this instance lost the node
    between the two, and submitting the graph's own LoRA silently is the one
    outcome worth a log.
    """
    if not apply_adapter(
        workflow_instance, swap["targets"], swap["adapter"], swap["object_info"]
    ):
        logger.error(
            "LoRA %s reached no slot of %s; it runs with its own",
            swap["adapter"]["sha256"],
            label,
        )


def _oldest_kept_by_sha(session, shas) -> dict:
    """Kept pictures by content, a duplicate resolving to its oldest copy.

    The one lookup behind a Fixed input, shared by its setup and its run so the
    two can never name different copies of the same picture.
    """
    if not shas:
        return {}
    rows = session.exec(
        select(Picture)
        .where(Picture.pixel_sha.in_(shas), Picture.deleted.is_(False))
        .order_by(Picture.id.desc())
    ).all()
    # Descending, so the oldest copy is written last and wins.
    return {pic.pixel_sha: pic for pic in rows}


def _missing_placeholders(payload: dict, detected=None) -> list[str]:
    """What a run of *payload* cannot fill, under the names the menus know.

    No file carries a token any more; the names are kept so the ComfyUI menus
    keep choosing workflows by what the run routes accept. A role is missing
    when neither a binding nor detection gives it a target
    (``services/workflow_bindings.py``).
    """
    targets = workflow_bindings.run_targets(payload, detected)
    return [
        placeholder
        for placeholder, role in (
            (PLACEHOLDER_IMAGE, workflow_bindings.IMAGE),
            (PLACEHOLDER_CAPTION, workflow_bindings.CAPTION),
        )
        if not targets[role]
    ]


def _fill_run_inputs(workflow: dict, image: str | None, caption: str) -> dict:
    """A copy of *workflow* with the picture and caption put where a run reads them.

    An empty caption leaves the prompt as the workflow has it: a migrated
    binding already holds the neutral empty prompt, and a detected prompt is the
    workflow's own text, which an unset caption must not wipe.
    """
    instance = deepcopy(workflow)
    try:
        workflow_bindings.fill(
            instance,
            workflow_bindings.run_targets(workflow),
            {
                workflow_bindings.IMAGE: image,
                workflow_bindings.CAPTION: caption or None,
            },
        )
    except workflow_bindings.BindingError as exc:
        logger.warning("Workflow binding does not resolve: %s", exc)
        raise HTTPException(
            status_code=400,
            detail=f"Workflow input binding no longer matches the graph: {exc}",
        ) from exc
    return instance


# ponytail: one entry per file version; stale versions age out of the LRU.
@functools.lru_cache(maxsize=512)
def _describe_workflow(path: str, source: str, mtime_ns: int, size: int) -> dict:
    """List metadata for one workflow file, recomputed only when the file changes.

    Keyed on mtime and size so detection runs once per file version, and a file
    that stays broken is logged once rather than on every menu open.
    """
    try:
        payload = _load_workflow_json(path)
    except Exception as exc:
        logger.warning("Failed to read %s workflow %s: %s", source, path, exc)
        return {
            "valid": False,
            "missing_placeholders": [PLACEHOLDER_IMAGE, PLACEHOLDER_CAPTION],
            "workflow_type": "t2i",
            "flagged": False,
        }
    try:
        detected = detect_workflow_io(payload)
    except Exception as exc:
        logger.warning(
            "Failed to detect inputs of %s workflow %s, listing it invalid: %s",
            source,
            path,
            exc,
        )
        return {
            "valid": False,
            "missing_placeholders": _missing_placeholders(payload),
            "workflow_type": "t2i",
            "flagged": workflow_bindings.is_flagged(payload),
        }
    return {
        "valid": detected.valid,
        "missing_placeholders": _missing_placeholders(payload, detected),
        "workflow_type": detected.workflow_type,
        "picture_inputs": _picture_input_classes(detected),
        "flagged": workflow_bindings.is_flagged(payload),
        # The run route submits API format only; a UI-format file lists but
        # cannot run.
        "runnable": detected.valid
        and workflow_parameters.api_graph(payload) is not None,
        # Every LoRA a run can swap (#1310), on the list because every menu
        # that runs a workflow reads this and would otherwise need its own
        # request per workflow to know whether to offer the shelf.
        "lora_slots": detect_lora_targets(workflow_parameters.api_graph(payload)),
    }


def _comfyui_url(user) -> str:
    """The ComfyUI base URL a user has set, without a trailing slash."""
    url = getattr(user, "comfyui_url", None) if user else None
    return (url or DEFAULT_COMFYUI_URL).rstrip("/")


def _on_disk_name(path: str) -> str:
    """The file's own spelling of its name, which keys its stored modes.

    A client's spelling can differ in case, and on a case-insensitive
    filesystem it still opens the file; keying on it would split one file's
    setup between two names.
    """
    folder, base = os.path.split(path)
    try:
        entries = os.listdir(folder)
    except OSError as exc:
        logger.warning("Could not list %s to spell workflow %s: %s", folder, base, exc)
        return base
    if base in entries:
        return base
    return next((entry for entry in entries if entry.lower() == base.lower()), base)


def _picture_input_classes(detected) -> dict[str, str]:
    """Detected picture inputs as ``node_id -> class_type``, in detection order."""
    return dict(zip(detected.picture_inputs, detected.picture_input_classes))


def _stored_input_modes(server, workflow_name: str | None = None):
    """This library's stored picture-input modes, all of them or one file's.

    Empty without a hub or an attached library, or when the hub cannot be read,
    so every input takes its default and a listing still answers.
    """
    hub = getattr(server, "hub", None)
    library_uuid = getattr(server.vault, "library_uuid", None)
    if hub is None or not library_uuid:
        return {} if workflow_name is None else []
    try:
        stored = input_modes_by_workflow(hub, library_uuid)
    except Exception as exc:
        logger.warning(
            "Could not read the picture-input modes of library %s, using the "
            "defaults for workflow %s: %s",
            library_uuid,
            workflow_name or "(all)",
            exc,
        )
        stored = {}
    return stored if workflow_name is None else stored.get(workflow_name, [])


def _offered_on_selection(picture_inputs: dict, stored, missing: list) -> bool:
    """Whether the selection path may offer this workflow.

    It needs a Selection input, which the run route fills by mode (#1307). A
    workflow whose binding sits on a loader detection does not recognise has no
    detected input, and is offered while that binding gives the selection
    somewhere to go (``missing`` names ``{{image_path}}`` when it does not).
    ``run_i2i`` still fills only that one target, and checks it itself.
    """
    if not picture_inputs:
        return PLACEHOLDER_IMAGE not in missing
    # No document: it only decides WHICH input defaults to Selection, never
    # whether one does, and that is all this asks.
    return any(
        item.mode == SELECTION
        for item in resolve_input_modes({}, picture_inputs, stored)
    )


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


def _read_object_info(comfyui_url: str) -> tuple[dict | None, str | None]:
    """Return ``(object_info, error)``; ``(None, why)`` when ComfyUI cannot be asked.

    Split out of :func:`_inspect_recipe` so one fetch can serve both the
    pre-flight and a LoRA swap on the same run, and so the swap can be applied
    *before* the graph is judged.
    """
    try:
        return fetch_object_info(comfyui_url), None
    except RuntimeError as exc:
        logger.info(
            "[comfyui] Recipe pre-flight skipped, ComfyUI not reachable at %s: %s",
            comfyui_url,
            exc,
        )
        return None, str(exc)


def _inspect_graph(
    prompt_graph: dict, object_info: dict | None, error: str | None
) -> tuple[dict, list[dict]]:
    """Return ``(preflight, seed_targets)`` for a graph and an already-read map.

    ``object_info`` of ``None`` is "ComfyUI could not be asked": the pre-flight
    degrades to *unchecked* (not *failed*) and seed detection falls back to the
    static class list, which covers the core samplers but not custom packs.
    """
    if object_info is None:
        return unchecked_preflight(error or "ComfyUI unreachable"), collect_seed_inputs(
            prompt_graph
        )
    return (
        preflight_prompt(prompt_graph, object_info),
        detect_seed_targets(prompt_graph, object_info),
    )


def _inspect_recipe(comfyui_url: str, prompt_graph: dict) -> tuple[dict, list[dict]]:
    """Return ``(preflight, seed_targets)`` for *prompt_graph*, asking ComfyUI once."""
    object_info, error = _read_object_info(comfyui_url)
    return _inspect_graph(prompt_graph, object_info, error)


class ComfyUIWorkflowItemResponse(BaseModel):
    """A single discovered ComfyUI workflow. ``valid`` and ``workflow_type`` are detected from the graph."""

    model_config = ConfigDict(extra="allow")

    name: str
    display_name: Optional[str] = None
    valid: bool = False
    missing_placeholders: list[str] = []
    source: Optional[str] = None
    workflow_type: Optional[str] = None
    # False when no picture input is filled by the selection, so the selection
    # pill leaves it out and only the toolbar offers it.
    has_selection_input: bool = False
    # A placeholder migration could not restore a value a token replaced.
    flagged: bool = False
    # The run route can submit it: a save node, in API format.
    runnable: bool = False


class ComfyUIPictureInputResponse(BaseModel):
    """One picture input of a workflow and how it is filled.

    ``picture_id`` is the Fixed picture in this library, or for another mode the
    picture it would return to. ``picture_missing`` is a Fixed input whose
    picture is no longer in it.
    """

    node_id: str
    title: str
    mode: str
    picture_id: Optional[int] = None
    picture_missing: bool = False


class ComfyUIWorkflowInputsResponse(BaseModel):
    """A workflow file's picture inputs, for its setup."""

    workflow: str
    inputs: list[ComfyUIPictureInputResponse] = []
    # Every LoRA slot a run can swap a shelf adapter into, so the run panel can
    # say a workflow has none instead of offering a choice the run refuses.
    lora_slots: list[dict] = []


class ComfyUIWorkflowParameterResponse(BaseModel):
    """One settable value of a workflow, typed for a form control.

    ``kind`` is ``int``, ``float``, ``seed``, ``boolean``, ``string``,
    ``choice`` or ``model``. The range and ``options`` are ``None`` when
    ComfyUI did not describe them.
    """

    node_id: str
    node_title: str
    class_type: str
    name: str
    kind: str
    # Kept as sent: a seed can exceed 2**53, which a float (or a JavaScript
    # number) cannot hold exactly.
    value: Any = None
    typed: bool = False
    min: Optional[int | float] = None
    max: Optional[int | float] = None
    step: Optional[int | float] = None
    options: Optional[list[Any]] = None
    multiline: bool = False
    pinned: bool = False


class ComfyUIParameterKey(BaseModel):
    """A parameter's name within its workflow."""

    node_id: str
    name: str


class ComfyUIWorkflowParametersResponse(BaseModel):
    """A workflow file's parameters, and whether ComfyUI typed them.

    ``typed`` is True when ComfyUI's ``object_info`` was read for them.
    ``comfyui_error`` is set only when ComfyUI could not be reached, and the
    values are then the file's own, with no ranges; ``typed`` False with no
    error means there was nothing to ask about. ``readable`` is False for a
    UI-format file, whose widget values carry no names. Each parameter's own
    ``typed`` says whether ComfyUI knew that input.
    ``pins`` is the pinned parameters in the order they are shown, and
    ``pins_saved`` is False while the default pins apply.
    """

    workflow: str
    readable: bool
    typed: bool
    comfyui_error: Optional[str] = None
    pins_saved: bool = False
    pins: list[ComfyUIParameterKey] = []
    parameters: list[ComfyUIWorkflowParameterResponse] = []


class ComfyUIWorkflowPinsResponse(BaseModel):
    """A workflow file's stored pins, in order; ``None`` when none are stored."""

    workflow: str
    pins_saved: bool
    pins: Optional[list[ComfyUIParameterKey]] = None


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
    # True when the workflow was already stored, under ``name``.
    matched: bool = False
    # The Workflows view row it is filed under; None when it could not be.
    topology_hash: Optional[str] = None


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
    # Every LoRA slot a replay can swap, so "Generate variants" can offer the
    # shelf where the picture's own graph has a loader, and say so where it
    # does not.
    lora_slots: list[dict] = []
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

            comfyui_url = _comfyui_url(user)
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
        description="Lists discovered built-in and user workflows. A workflow is valid when it has a save node, and i2i when it has a picture input.",
        response_model=ComfyUIWorkflowListResponse,
    )
    def list_comfyui_workflows():
        workflows = []
        seen = set()
        stored = _stored_input_modes(server)
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
                    stat = os.stat(path)
                except OSError as exc:
                    logger.warning(
                        "Failed to stat %s workflow %s: %s", source, path, exc
                    )
                    continue
                # Copied, because the description is the cache's own dict.
                described = dict(
                    _describe_workflow(path, source, stat.st_mtime_ns, stat.st_size)
                )
                picture_inputs = described.pop("picture_inputs", {})
                workflows.append(
                    {
                        "name": entry,
                        "display_name": os.path.splitext(entry)[0],
                        "source": source,
                        **described,
                        "has_selection_input": _offered_on_selection(
                            picture_inputs,
                            stored.get(entry, []),
                            described["missing_placeholders"],
                        ),
                    }
                )
        workflows.sort(key=lambda item: item.get("name", ""))
        return {"workflows": workflows}

    @router.delete(
        "/comfyui/workflows/{workflow_name}",
        include_in_schema=False,
        summary="Delete user workflow",
        description=(
            "Writes the workflow back to the watched workflows folder, moves "
            "every inbox file holding it to the system trash, then deletes it "
            "from the user workflow directory. A stored file that is not a "
            "readable workflow is moved to the trash as it is."
        ),
        response_model=ComfyUIWorkflowDeleteResponse,
    )
    def delete_comfyui_workflow(workflow_name: str):
        # Sync on purpose: the trash is file I/O, so this runs on the thread pool.
        normalized = _normalize_workflow_name(workflow_name)
        if not normalized:
            raise HTTPException(status_code=400, detail="workflow_name is required")
        workflow_dir = workflow_user_dir()
        try:
            path = resolve_path_within(workflow_dir, normalized)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid workflow name")
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="Workflow not found in user")
        stored_name = _on_disk_name(path)
        try:
            with workflow_inbox.INBOX_LOCK:
                _trash_stored_workflow(path, normalized)
        except (TrashPermissionError, OSError, RecursionError) as exc:
            logger.warning("Failed to delete workflow %s: %s", normalized, exc)
            raise HTTPException(status_code=500, detail="Failed to delete workflow")
        # The placeholder migration's backup goes with the workflow it copied.
        backup = f"{path}{workflow_bindings.BACKUP_SUFFIX}"
        if os.path.exists(backup):
            try:
                os.remove(backup)
            except OSError as exc:
                logger.warning(
                    "Deleted workflow %s but not its migration backup %s: %s",
                    normalized,
                    backup,
                    exc,
                )
        hub = getattr(server, "hub", None)
        if hub is not None:
            # The file is already gone, so its rows describe nothing; they would
            # only come back into force if a file of the same name is imported
            # later. Each is forgotten on its own, so one failing keeps the other.
            for what, forget in (
                ("picture-input modes", lambda: forget_input_modes(hub, stored_name)),
                (
                    "parameter pins",
                    lambda: replace_parameter_pins(hub, stored_name, None),
                ),
            ):
                try:
                    forget()
                except Exception as exc:
                    logger.warning(
                        "Deleted workflow %s but could not forget its %s: %s",
                        normalized,
                        what,
                        exc,
                    )
        return {"status": "success", "name": normalized}

    def _load_stored_workflow(workflow_name: str) -> tuple[str, str, dict]:
        """``(on-disk name, path, document)`` of a stored workflow, or raise 4xx."""
        name = _normalize_workflow_name(workflow_name)
        if not name:
            raise HTTPException(status_code=400, detail="workflow_name is required")
        path, _source = _resolve_workflow_path(name)
        if not path:
            raise HTTPException(status_code=404, detail="Workflow not found")
        try:
            document = _load_workflow_json(path)
        except Exception as exc:
            logger.warning("Failed to read workflow %s: %s", path, exc)
            raise HTTPException(
                status_code=422, detail="This workflow's graph could not be read."
            ) from exc
        return _on_disk_name(path), path, document

    def _read_picture_inputs(workflow_name: str) -> tuple[str, dict, dict[str, str]]:
        """Load a workflow file and detect its picture inputs, or raise 4xx."""
        name, path, document = _load_stored_workflow(workflow_name)
        try:
            detected = detect_workflow_io(document)
        except Exception as exc:
            logger.warning("Failed to read the inputs of workflow %s: %s", path, exc)
            raise HTTPException(
                status_code=422, detail="This workflow's graph could not be read."
            ) from exc
        return name, document, _picture_input_classes(detected)

    def _describe_picture_inputs(name: str, document: dict, picture_inputs) -> dict:
        resolved = resolve_input_modes(
            document, picture_inputs, _stored_input_modes(server, name)
        )
        shas = {item.pixel_sha for item in resolved if item.pixel_sha}

        ids = {
            pixel_sha: pic.id
            for pixel_sha, pic in server.vault.db.run_immediate_read_task(
                lambda session: _oldest_kept_by_sha(session, shas)
            ).items()
        }
        return {
            "workflow": name,
            "inputs": [
                {
                    "node_id": item.node_id,
                    "title": item.title,
                    "mode": item.mode,
                    # A non-Fixed input may still hold the picture it had, so
                    # choosing Fixed again needs no new pick.
                    "picture_id": ids.get(item.pixel_sha),
                    "picture_missing": item.mode == FIXED and item.pixel_sha not in ids,
                }
                for item in resolved
            ],
            "lora_slots": detect_lora_targets(workflow_parameters.api_graph(document)),
        }

    @router.get(
        "/comfyui/workflows/{workflow_name}/inputs",
        summary="A workflow's picture inputs",
        description=(
            "Each picture input of a saved workflow and how it is filled: by the "
            "selection, by a picker at run time, or by a fixed picture. "
            "lora_slots lists every LoRA a run can swap, empty when the graph "
            "has no LoRA loader."
        ),
        response_model=ComfyUIWorkflowInputsResponse,
    )
    def get_comfyui_workflow_inputs(workflow_name: str):
        name, document, picture_inputs = _read_picture_inputs(workflow_name)
        return _describe_picture_inputs(name, document, picture_inputs)

    @router.put(
        "/comfyui/workflows/{workflow_name}/inputs",
        summary="Set how a workflow's picture inputs are filled",
        description=(
            "Replaces the setup of every picture input. At most one input is "
            "filled by the selection; a fixed input names a picture in this "
            "library. Stored beside the workflow, never written into it."
        ),
        response_model=ComfyUIWorkflowInputsResponse,
    )
    def put_comfyui_workflow_inputs(workflow_name: str, payload: dict = Body(...)):
        name, document, picture_inputs = _read_picture_inputs(workflow_name)
        hub = getattr(server, "hub", None)
        library_uuid = getattr(server.vault, "library_uuid", None)
        if hub is None or not library_uuid:
            raise HTTPException(
                status_code=503,
                detail="No hub or library is attached, so the setup cannot be kept.",
            )
        try:
            requested = validate_requested_modes(picture_inputs, payload.get("inputs"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        picture_ids = {pid for _, _, pid in requested if pid is not None}
        # Every stored picture, whatever the input's mode now: leaving Fixed
        # keeps it, so stepping back to Fixed does not lose it.
        kept = {
            row["node_id"]: row["pixel_sha"]
            for row in _stored_input_modes(server, name)
            if row["pixel_sha"]
        }

        def shas_by_id(session):
            rows = session.exec(
                select(Picture.id, Picture.pixel_sha).where(
                    Picture.id.in_(picture_ids), Picture.deleted.is_(False)
                )
            ).all()
            return {pic_id: pixel_sha for pic_id, pixel_sha in rows}

        shas = (
            server.vault.db.run_immediate_read_task(shas_by_id) if picture_ids else {}
        )
        modes = []
        for node_id, mode, picture_id in requested:
            pixel_sha = kept.get(node_id)
            if mode == FIXED and picture_id is None:
                # Unchanged, so it keeps its picture, even one that has since
                # left the library: changing another input must not drop it.
                if pixel_sha is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"fixed input {node_id} needs a picture_id",
                    )
            elif picture_id is not None:
                if picture_id not in shas:
                    raise HTTPException(status_code=404, detail="Picture not found")
                pixel_sha = shas[picture_id]
                if not pixel_sha:
                    # Named by content so a reused id cannot swap the picture;
                    # one not hashed yet has nothing to name it by.
                    raise HTTPException(
                        status_code=409,
                        detail="That picture is still being read. Try again shortly.",
                    )
            modes.append((node_id, mode, pixel_sha))
        replace_input_modes(hub, library_uuid, name, modes)
        return _describe_picture_inputs(name, document, picture_inputs)

    def _parameters_of(
        name: str, document: dict, object_info=None, detected=None
    ) -> list:
        """The file's parameters, or 422 when an API-format graph cannot be read.

        A UI-format file has none and is never reduced, so a graph the UI
        reduction would refuse still answers ``readable: false``.
        """
        try:
            return workflow_parameters.describe_parameters(
                document, object_info, detected
            )
        except WorkflowGraphError as exc:
            logger.warning(
                "Failed to read the parameters of workflow %s: %s", name, exc
            )
            raise HTTPException(
                status_code=422, detail="This workflow's graph could not be read."
            ) from exc

    def _stored_pins(name: str) -> Optional[list]:
        hub = getattr(server, "hub", None)
        if hub is None:
            return None
        try:
            return parameter_pins(hub, name)
        except Exception as exc:
            logger.warning(
                "Could not read the parameter pins of workflow %s, using the "
                "defaults: %s",
                name,
                exc,
            )
            return None

    @router.get(
        "/comfyui/workflows/{workflow_name}/parameters",
        summary="A workflow's parameters",
        description=(
            "Every settable value of a saved workflow, typed from ComfyUI's "
            "object_info with its real ranges and options. Connected inputs and "
            "inputs a run fills are left out. When ComfyUI cannot be reached the "
            "file's own values are returned with no ranges, and comfyui_error "
            "says why."
        ),
        response_model=ComfyUIWorkflowParametersResponse,
    )
    def get_comfyui_workflow_parameters(request: Request, workflow_name: str):
        name, _path, document = _load_stored_workflow(workflow_name)
        # Described untyped first: a file with nothing to set (a UI-format one
        # included) never waits on ComfyUI.
        readable = api_graph(document) is not None
        detected = None
        if readable:
            try:
                detected = detect_workflow_io(document)
            except WorkflowGraphError as exc:
                logger.warning(
                    "Failed to read the parameters of workflow %s: %s", name, exc
                )
                raise HTTPException(
                    status_code=422, detail="This workflow's graph could not be read."
                ) from exc
        parameters = _parameters_of(name, document, None, detected)
        typed, comfyui_error = False, None
        if parameters:
            comfyui_url = _comfyui_url(server.auth.get_user_for_request(request))
            # ponytail: one object_info fetch per read; cache it if forms open slowly.
            try:
                object_info = fetch_object_info(comfyui_url)
            except RuntimeError as exc:
                logger.info(
                    "[comfyui] Parameters of %s are untyped, ComfyUI not reachable "
                    "at %s: %s",
                    name,
                    comfyui_url,
                    exc,
                )
                comfyui_error = str(exc)
            else:
                parameters = _parameters_of(name, document, object_info, detected)
                typed = True
        stored = _stored_pins(name)
        known = {p.key for p in parameters}
        # A stored pin naming a node the file has lost is skipped, not pruned,
        # so a replaced file that keeps the node keeps the pin.
        ordered = (
            [tuple(pin) for pin in stored if tuple(pin) in known]
            if stored is not None
            else workflow_parameters.default_pins(parameters)
        )
        pinned = set(ordered)
        return {
            "workflow": name,
            "readable": readable,
            "typed": typed,
            "comfyui_error": comfyui_error,
            "pins_saved": stored is not None,
            "pins": [{"node_id": n, "name": k} for n, k in ordered],
            "parameters": [
                {
                    "node_id": p.node_id,
                    "node_title": p.node_title,
                    "class_type": p.class_type,
                    "name": p.name,
                    "kind": p.kind,
                    "value": p.value,
                    "typed": p.typed,
                    "min": p.minimum,
                    "max": p.maximum,
                    "step": p.step,
                    "options": list(p.options) if p.options is not None else None,
                    "multiline": p.multiline,
                    "pinned": p.key in pinned,
                }
                for p in parameters
            ],
        }

    @router.put(
        "/comfyui/workflows/{workflow_name}/pins",
        summary="Set which of a workflow's parameters are shown first",
        description=(
            "Replaces the pinned parameters of a saved workflow, as "
            "{pins: [{node_id, name}]}. pins: null forgets them, so the "
            "defaults apply again. Stored beside the workflow, never in it. "
            "Answers with the stored pins only, without asking ComfyUI."
        ),
        response_model=ComfyUIWorkflowPinsResponse,
    )
    def put_comfyui_workflow_pins(workflow_name: str, payload: dict = Body(...)):
        hub = getattr(server, "hub", None)
        if hub is None:
            raise HTTPException(
                status_code=503,
                detail="No hub is attached, so the pins cannot be kept.",
            )
        if "pins" not in payload:
            raise HTTPException(status_code=400, detail="pins is required")
        # Under the lock delete takes, so the file cannot be trashed (and its
        # pins forgotten) between reading it and writing a row for it.
        with workflow_inbox.INBOX_LOCK:
            name, _path, document = _load_stored_workflow(workflow_name)
            pins = None
            if payload["pins"] is not None:
                try:
                    pins = workflow_parameters.validate_pins(
                        _parameters_of(name, document), payload["pins"]
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
            replace_parameter_pins(hub, name, pins)
        return {
            "workflow": name,
            "pins_saved": pins is not None,
            "pins": (
                None if pins is None else [{"node_id": n, "name": k} for n, k in pins]
            ),
        }

    def _stack_outputs_with(workflow_instance: dict, pic_id: int) -> int | None:
        """Join *pic_id*'s stack and tag the save node so its outputs land in it.

        Returns the stack id, or ``None`` when there is none to join.
        """
        stack_id = server.vault.db.run_task(get_or_create_stack_for_picture, pic_id)
        if not stack_id:
            return None
        prefix_seed = ""
        for node in workflow_instance.values():
            if isinstance(node, dict) and node.get("class_type") == "SaveImage":
                prefix_seed = str(
                    (node.get("inputs") or {}).get("filename_prefix") or ""
                )
                break
        prefix_value = build_stack_filename_prefix(prefix_seed, stack_id, pic_id)
        if not _apply_filename_prefix(
            workflow_instance, prefix_value
        ) and not graph_has_pixlstash_saver(workflow_instance):
            logger.warning(
                "ComfyUI workflow has no SaveImage node to tag for stack %s",
                stack_id,
            )
        return stack_id

    def _start_output_import(
        request: Request,
        comfyui_url: str,
        prompt_id,
        output_node_ids: list[str],
        stack_id: int | None,
        source_picture_id: int | None,
        view_context: dict | None = None,
    ) -> None:
        """Collect a submitted prompt's outputs in the background."""
        origin_lease = request.state.library_lease
        threading.Thread(
            target=_process_comfyui_outputs,
            args=(
                server,
                comfyui_url,
                str(prompt_id),
                output_node_ids,
                stack_id,
                source_picture_id,
            ),
            kwargs={
                "view_context": view_context,
                "origin_generation": origin_lease.generation,
                "origin_library_uuid": origin_lease.library_uuid,
            },
            daemon=True,
        ).start()

    @router.post(
        "/comfyui/workflows/{workflow_name}/run",
        summary="Run a saved workflow",
        description=(
            "Runs a saved API-format workflow, filling each picture input by its "
            "mode: the selection (picture_ids), a picker (pictures: [{node_id, "
            "picture_id}]) or its fixed picture. A workflow with a Selection "
            "input runs once per selected picture and stacks each output with "
            "it; one without runs once and takes no selection. Optional: "
            "caption, values ([{node_id, name, value}], see /parameters), "
            "seed_mode ('random', 'fixed' with seed, or 'keep'), stack, "
            "client_id, and set_id/project_id/character_id for a run with no "
            "selection. adapter_sha256 puts that shelf LoRA into the graph's "
            "LoRA slot, written the way its own loader reads it, with "
            "lora_node_id naming which slot when the workflow has more than "
            "one; a workflow with no LoRA loader is refused. Outputs are "
            "collected from the detected save nodes."
        ),
        response_model=ComfyUIRunResponse,
    )
    def run_comfyui_workflow(
        request: Request, workflow_name: str, payload: dict = Body(...)
    ):
        name, document, picture_inputs = _read_picture_inputs(workflow_name)
        graph = workflow_parameters.api_graph(document)
        if graph is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "This workflow is saved in ComfyUI's UI format, which cannot "
                    "be submitted. Export it from ComfyUI in API format."
                ),
            )
        pixlstash_keys = {
            key: value
            for key, value in document.items()
            if str(key).startswith("pixlstash_")
        }
        output_node_ids = _extract_output_node_ids({**graph, **pixlstash_keys}, {})
        if not output_node_ids:
            raise HTTPException(
                status_code=400,
                detail="This workflow has no save node, so a run would import nothing.",
            )

        selection_ids = _int_list(payload.get("picture_ids"), "picture_ids")
        modes = resolve_input_modes(
            document, picture_inputs, _stored_input_modes(server, name)
        )
        targets = {}
        for item in modes:
            target = workflow_bindings.picture_target(
                document, item.node_id, picture_inputs[item.node_id]
            )
            if target is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"PixlStash cannot fill picture input {item.title} "
                        f"({item.node_id}): {picture_inputs[item.node_id]} has "
                        "no image field it knows."
                    ),
                )
            targets[item.node_id] = target
        selection_node = next((m.node_id for m in modes if m.mode == SELECTION), None)
        # A binding on a loader detection does not recognise: the selection
        # fills it the way run_i2i always has.
        legacy_targets = (
            []
            if picture_inputs
            else workflow_bindings.run_targets(document)[workflow_bindings.IMAGE]
        )
        takes_selection = selection_node is not None or bool(legacy_targets)
        if takes_selection and not selection_ids:
            raise HTTPException(
                status_code=400,
                detail="This workflow runs once for each selected picture; select one.",
            )
        if len(selection_ids) > MAX_RUNS_PER_REQUEST:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{len(selection_ids)} runs is more than one request starts; "
                    f"select at most {MAX_RUNS_PER_REQUEST} pictures."
                ),
            )
        if not takes_selection and selection_ids:
            # Its outputs would be stacked onto pictures the run never read.
            raise HTTPException(
                status_code=400,
                detail="No picture input of this workflow is filled by the selection.",
            )

        pickers = {m.node_id for m in modes if m.mode == PICKER}
        picked: dict[str, int] = {}
        raw_pictures = payload.get("pictures") or []
        if not isinstance(raw_pictures, list):
            raise HTTPException(status_code=400, detail="pictures must be a list")
        for entry in raw_pictures:
            node_id = entry.get("node_id") if isinstance(entry, dict) else None
            picture_id = entry.get("picture_id") if isinstance(entry, dict) else None
            if node_id not in pickers or node_id in picked:
                raise HTTPException(
                    status_code=400,
                    detail=f"pictures names {node_id!r}, which is not a picker input "
                    "of this workflow, or names it twice",
                )
            if isinstance(picture_id, bool) or not isinstance(picture_id, int):
                raise HTTPException(
                    status_code=400,
                    detail=f"the picture for input {node_id} must be an integer id",
                )
            picked[node_id] = picture_id
        unpicked = sorted(pickers - set(picked))
        if unpicked:
            raise HTTPException(
                status_code=400,
                detail="Choose a picture for input " + ", ".join(unpicked) + ".",
            )

        fixed = {m.node_id: m for m in modes if m.mode == FIXED}
        wanted_ids = set(selection_ids) | set(picked.values())
        wanted_shas = {m.pixel_sha for m in fixed.values() if m.pixel_sha}

        def kept_pictures(session):
            by_id = {
                pic.id: pic
                for pic in session.exec(
                    select(Picture).where(
                        Picture.id.in_(wanted_ids), Picture.deleted.is_(False)
                    )
                ).all()
            }
            return by_id, _oldest_kept_by_sha(session, wanted_shas)

        by_id, by_sha = server.vault.db.run_immediate_read_task(kept_pictures)

        def file_of(pic) -> str:
            path = ImageUtils.resolve_picture_path(
                server.vault.image_root, pic.file_path
            )
            if not path or not os.path.isfile(path):
                raise HTTPException(status_code=404, detail="Picture file missing")
            return path

        missing_ids = sorted(wanted_ids - set(by_id))
        if missing_ids:
            # Named, so the caller can tell which pictures left the library.
            raise HTTPException(
                status_code=404,
                detail="Pictures not found: " + ", ".join(map(str, missing_ids)),
            )
        shared: dict[str, object] = {
            node_id: by_id[picture_id] for node_id, picture_id in picked.items()
        }
        for node_id, item in fixed.items():
            pic = by_sha.get(item.pixel_sha)
            if pic is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"The fixed picture of input {item.title} ({node_id}) is no "
                        "longer in this library. Choose another in Workflows."
                    ),
                )
            shared[node_id] = pic
        files = {pic.id: file_of(pic) for pic in [*shared.values(), *by_id.values()]}

        if payload.get("values"):
            try:
                parameters = workflow_parameters.describe_parameters(document)
                document = workflow_parameters.apply_values(
                    document, parameters, payload["values"]
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        seed_mode = payload.get("seed_mode", "random")
        if seed_mode not in ("random", "fixed", "keep"):
            raise HTTPException(
                status_code=400,
                detail="seed_mode must be 'random', 'fixed' or 'keep'",
            )
        fixed_seed = _resolve_fixed_seed(payload)
        caption = payload.get("caption") or ""
        if not isinstance(caption, str):
            caption = str(caption)
        client_id = payload.get("client_id") or payload.get("clientId") or None
        if client_id is not None:
            client_id = str(client_id)
        should_stack = payload.get("stack", True)
        if not isinstance(should_stack, bool):
            raise HTTPException(status_code=400, detail="stack must be true or false")
        view_context = None if takes_selection else _view_context(payload)
        caption_targets = workflow_bindings.run_targets(document)[
            workflow_bindings.CAPTION
        ]

        def filled(names: dict[str, str], selected_name: str | None) -> dict:
            """A copy of the document with every picture and the caption in place.

            A templated binding can hold the picture and the caption at once, so
            each picture target is filled together with any caption target on
            the same spot: filled apart, the second pass rebuilds the string from
            its template and drops the first value.
            """
            instance = deepcopy(document)
            image_fills = [(targets[node_id], name) for node_id, name in names.items()]
            if selected_name is not None:
                image_fills += [(target, selected_name) for target in legacy_targets]
            image_paths = set()
            try:
                for target, uploaded in image_fills:
                    key = json.dumps(target.get("path"))
                    image_paths.add(key)
                    workflow_bindings.fill(
                        instance,
                        {
                            workflow_bindings.IMAGE: [target],
                            workflow_bindings.CAPTION: [
                                t
                                for t in caption_targets
                                if json.dumps(t.get("path")) == key
                            ],
                        },
                        {
                            workflow_bindings.IMAGE: uploaded,
                            workflow_bindings.CAPTION: caption or None,
                        },
                    )
                workflow_bindings.fill(
                    instance,
                    {
                        workflow_bindings.CAPTION: [
                            t
                            for t in caption_targets
                            if json.dumps(t.get("path")) not in image_paths
                        ]
                    },
                    {workflow_bindings.CAPTION: caption or None},
                )
            except workflow_bindings.BindingError as exc:
                logger.warning("Workflow %s binding does not resolve: %s", name, exc)
                raise HTTPException(
                    status_code=400,
                    detail=f"Workflow input binding no longer matches the graph: {exc}",
                ) from exc
            return {**workflow_parameters.api_graph(instance), **pixlstash_keys}

        # Filled once with stand-in names, so a binding that no longer matches
        # the graph is refused before anything reaches ComfyUI.
        filled(
            {node_id: "example.png" for node_id in targets},
            "example.png" if takes_selection else None,
        )

        comfyui_url = _comfyui_url(server.auth.get_user_for_request(request))

        # A LoRA from the shelf, resolved before anything is uploaded: a
        # graph with no loader to put it in is refused here rather than run
        # without the adapter the caller asked for (#1310).
        swap = _resolve_lora_swap(
            getattr(server, "hub", None),
            payload,
            graph,
            f"Workflow {name}",
            comfyui_url,
        )

        def upload(pic) -> str:
            # Named by the picture, not by the file's own name: two pictures
            # called image.png would otherwise overwrite each other in ComfyUI's
            # input folder before the queue reaches the first run. The id tells
            # apart two pictures whose pixels hash alike.
            ext = os.path.splitext(files[pic.id])[1]
            return _upload_image_to_comfyui(
                comfyui_url,
                files[pic.id],
                upload_name=f"pixlstash-{pic.id}-{pic.pixel_sha or 'unhashed'}{ext}",
            )

        prompts = []
        try:
            shared_names = {node_id: upload(pic) for node_id, pic in shared.items()}
            for pic_id in selection_ids if takes_selection else [None]:
                names = dict(shared_names)
                selected_name = None
                if pic_id is not None:
                    selected_name = upload(by_id[pic_id])
                    if selection_node is not None:
                        names[selection_node] = selected_name
                workflow_instance = filled(names, selected_name)
                if swap is not None:
                    # After apply_values, so the LoRA chosen for this run wins
                    # over a lora_name set in the parameter form: it is the
                    # later and more specific of the two gestures.
                    _apply_lora_swap(workflow_instance, swap, f"workflow {name}")
                if fixed_seed is not None:
                    _apply_fixed_seed(workflow_instance, fixed_seed)
                elif seed_mode != "keep":
                    _randomize_seeds(workflow_instance)
                stack_id = (
                    _stack_outputs_with(workflow_instance, pic_id)
                    if pic_id is not None and should_stack
                    else None
                )
                response_payload = _submit_comfyui_prompt(
                    comfyui_url, workflow_instance, client_id
                )
                prompt_id = response_payload.get("prompt_id") or response_payload.get(
                    "id"
                )
                if prompt_id:
                    _start_output_import(
                        request,
                        comfyui_url,
                        prompt_id,
                        output_node_ids,
                        stack_id,
                        pic_id,
                        view_context,
                    )
                prompts.append({"picture_id": pic_id, "prompt_id": prompt_id})
        except HTTPException as exc:
            if not prompts:
                raise
            # Earlier runs are already queued and importing, so they are
            # returned for the client to follow rather than lost behind an error.
            logger.warning(
                "Workflow %s stopped after %d of its runs: %s",
                name,
                len(prompts),
                exc.detail,
            )
            return {
                "status": "partial",
                "workflow": name,
                "prompts": prompts,
                "error": str(exc.detail),
            }
        return {"status": "success", "workflow": name, "prompts": prompts}

    @router.post(
        "/comfyui/abort",
        include_in_schema=False,
        summary="Abort ComfyUI execution",
        description="Interrupts the currently running ComfyUI prompt and clears the pending queue.",
        response_model=ComfyUIAbortResponse,
    )
    async def abort_comfyui(request: Request):
        user = server.auth.get_user_for_request(request)
        comfyui_url = _comfyui_url(user)
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
            "(0-4294967295), in which case every sampler node is pinned to it. "
            "adapter_sha256 swaps that shelf LoRA into the workflow's LoRA slot "
            "(lora_node_id names which one when it has several); a workflow "
            "with no LoRA loader is refused."
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
        missing = _missing_placeholders(workflow_payload)
        # Refused even when the workflow is a valid t2i: with no picture input
        # to fill, the selected picture never reaches the graph, and its output
        # would be stacked onto a picture it ignored.
        if PLACEHOLDER_IMAGE in missing:
            raise HTTPException(
                status_code=400,
                detail="Workflow has no picture input to fill.",
            )
        try:
            picture_inputs = _picture_input_classes(
                detect_workflow_io(workflow_payload)
            )
        except Exception as exc:
            logger.warning(
                "Failed to detect inputs of workflow %s, running it on its "
                "binding alone: %s",
                workflow_path,
                exc,
            )
            picture_inputs = {}
        # The list leaves this out of the selection pill; a stale client or a
        # hand-made request must not run it on a selection anyway.
        if not _offered_on_selection(
            picture_inputs,
            _stored_input_modes(server, _on_disk_name(workflow_path)),
            missing,
        ):
            raise HTTPException(
                status_code=400,
                detail="No picture input of this workflow is filled by the selection.",
            )
        output_node_ids = _extract_output_node_ids(workflow_payload, payload)

        user = server.auth.get_user_for_request(request)
        comfyui_url = _comfyui_url(user)

        # "Edit with ComfyUI" runs a stored workflow, so it swaps a shelf LoRA
        # the same way the run panel does - resolved once, before the first
        # upload, and applied to each picture's own instance (#1310).
        swap = _resolve_lora_swap(
            getattr(server, "hub", None),
            payload,
            workflow_parameters.api_graph(workflow_payload) or {},
            f"Workflow {workflow_name}",
            comfyui_url,
        )

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
            workflow_instance = _fill_run_inputs(
                workflow_payload, uploaded_name, caption
            )
            if fixed_seed is not None:
                _apply_fixed_seed(workflow_instance, fixed_seed)
            else:
                _randomize_seeds(workflow_instance)
            if swap is not None:
                _apply_lora_swap(workflow_instance, swap, f"workflow {workflow_name}")
            # Only create/join a stack and tag the SaveImage filename when
            # stacking is requested. When disabled, stack_id stays None so the
            # worker places nothing in a stack; set/project associations and the
            # source_picture_id marker are propagated either way.
            stack_id = (
                _stack_outputs_with(workflow_instance, pic_id) if should_stack else None
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
        view_context = _view_context(payload)

        workflow_path, _ = _resolve_workflow_path(workflow_name)
        if not workflow_path:
            raise HTTPException(status_code=404, detail="Workflow not found")

        workflow_payload = _load_workflow_json(workflow_path)
        if PLACEHOLDER_IMAGE not in _missing_placeholders(workflow_payload):
            raise HTTPException(
                status_code=400,
                detail="This workflow requires an image input and cannot be used for text-to-image generation.",
            )

        output_node_ids = _extract_output_node_ids(workflow_payload, payload)

        user = server.auth.get_user_for_request(request)
        comfyui_url = _comfyui_url(user)

        fixed_seed = _resolve_fixed_seed(payload)

        workflow_instance = _fill_run_inputs(workflow_payload, None, caption)
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
        description=(
            "Stores a workflow JSON, UI or API format, unchanged in the user "
            "workflow directory (a file still carrying placeholder tokens is "
            "stored with them migrated to bindings). A copy of a workflow already stored is matched "
            "to it rather than stored twice. A name taken by a different "
            "workflow is refused unless overwrite or keep_both is set."
        ),
        response_model=ComfyUIWorkflowImportResponse,
    )
    def import_comfyui_workflow(payload: dict = Body(...)):
        # Sync on purpose: finding a stored copy reads every workflow file, so
        # FastAPI runs this on its thread pool rather than the event loop.
        name = _normalize_workflow_name(payload.get("name"))
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        workflow = payload.get("workflow")
        if not isinstance(workflow, dict):
            raise HTTPException(
                status_code=400, detail="workflow must be a JSON object"
            )
        try:
            resolve_path_within(workflow_user_dir(), name)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid workflow name")
        try:
            with workflow_inbox.INBOX_LOCK:
                return _store_workflow(
                    getattr(server, "hub", None),
                    name,
                    workflow,
                    overwrite=bool(payload.get("overwrite")),
                    keep_both=bool(payload.get("keep_both")),
                )
        except RecursionError as exc:
            logger.warning("Refused a workflow import that nests too deeply: %s", exc)
            raise HTTPException(
                status_code=400, detail="Workflow JSON nests too deeply"
            ) from exc
        except FileExistsError as exc:
            raise HTTPException(
                status_code=409, detail="Workflow already exists"
            ) from exc

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
        comfyui_url = _comfyui_url(user)

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
            "lora_slots": detect_lora_targets(graph),
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
            "knowingly approved an uninspected graph. adapter_sha256 swaps that "
            "shelf LoRA into the recipe's LoRA slot (lora_node_id names which "
            "one when it has several); a recipe with no LoRA loader is refused. "
            "Outputs land in the source picture's stack, exactly as run_i2i "
            "does."
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
        comfyui_url = _comfyui_url(user)

        # The swap is resolved and applied BEFORE the graph is judged, so the
        # pre-flight checks what will actually run: a recipe whose own LoRA has
        # since left this ComfyUI is exactly the one worth re-running with
        # another, and checking the file it no longer uses would refuse it.
        object_info, object_info_error = _read_object_info(comfyui_url)
        swap = _resolve_lora_swap(
            getattr(server, "hub", None),
            payload,
            workflow_instance,
            f"Picture {pic_id}'s recipe",
            comfyui_url,
            object_info or {},
        )
        if swap is not None:
            _apply_lora_swap(workflow_instance, swap, f"picture {pic_id}'s recipe")
        preflight, seed_targets = _inspect_graph(
            workflow_instance, object_info, object_info_error
        )
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
