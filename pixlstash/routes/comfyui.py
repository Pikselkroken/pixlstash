import asyncio
import functools
import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from urllib.parse import quote

import websockets
from fastapi import APIRouter, Body, HTTPException, Query, Request, WebSocket
from fastapi.websockets import WebSocketDisconnect
from pydantic import BaseModel, ConfigDict
from send2trash import TrashPermissionError, send2trash

from typing import Any, Optional

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Picture,
    User,
)
from pixlstash.hub import workflow_cards, workflow_origin
from pixlstash.hub.workflows import (
    forget_input_modes,
    input_modes_by_workflow,
    record_api_graph,
    record_ui_graph,
    replace_parameter_pins,
)
from pixlstash.utils.adapter_header import FILE_ADAPTER, FILE_UNKNOWN
from pixlstash.utils.comfyui_utilities import (
    collect_seed_inputs,
    extract_comfy_workflow_info,
    extract_generation_info,
    extract_recipe_extras,
    NotAWorkflowError,
    check_comfy_workflow,
    find_comfy_api_prompt,
    find_comfy_workflow,
    summarize_comfy_workflow,
)
from pixlstash.services.a1111_recipe import A1111Recipe, reduce_a1111
from pixlstash.services.comfyui_recipe_service import (
    collect_node_classes,
    detect_lora_targets,
    detect_seed_targets,
    fetch_object_info,
    plan_lora_insertion,
    preflight_prompt,
    sanitize_prompt_graph,
    unchecked_preflight,
)
from pixlstash.services.comfyui_ui_graph import (
    NO_OBJECT_INFO,
    convert_ui_graph_to_api,
    is_ui_graph,
)
from pixlstash.services.model_shelf_service import (
    fetch_locations,
    fetch_model_by_hash,
)
from pixlstash.services.picture_recipe_service import (
    describe_recipe,
    resolution_lock_in_session,
)
from pixlstash.services.workflow_inputs import (
    SELECTION,
    resolve_input_modes,
)
from pixlstash.services import (
    workflow_bindings,
    workflow_inbox,
    workflow_parameters,
)
from pixlstash.services.workflow_events import announce_changed_workflows
from pixlstash.services.workflow_hash import (
    WorkflowGraphError,
    topology_hash as api_topology_hash,
    ui_topology_hash,
)
from pixlstash.services.workflow_io import api_graph, detect_workflow_io
from pixlstash.tasks.base_task import TaskStatus
from pixlstash.tasks.comfyui_workflow_pull_task import ComfyUIWorkflowPullTask
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.path_utils import resolve_path_within
from platformdirs import user_data_dir

# ComfyUI workflow-execution orchestration and the output-import pipeline live in
# the service layer (backend refactor Phase 2 §4.5); the route handlers below
# stay thin and delegate to it. See pixlstash/services/comfyui_service.py.
from pixlstash.services.comfyui_service import (
    _comfyui_abort,
    graph_has_pixlstash_nodes,
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


# The largest workflow document this process will parse.
#
# On the LOADER rather than on one caller, because all ten call sites read the
# same watched-folder files: the grid's per-card read, the workflow list, the
# menu, import, the inbox. A cap on one of them is a cap on one screen (#1483).
# Measured off the open handle so the size that is checked is the size that is
# read - a stat before the open races a rewrite.
MAX_WORKFLOW_FILE_BYTES = 32 * 1024 * 1024


class WorkflowFileTooLarge(ValueError):
    """*path* is past :data:`MAX_WORKFLOW_FILE_BYTES`, so it was not parsed.

    A ``ValueError`` because every caller already handles one from
    ``json.load`` and treats it the same way - the document did not read.
    """


def _load_workflow_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        size = os.fstat(handle.fileno()).st_size
        if size > MAX_WORKFLOW_FILE_BYTES:
            raise WorkflowFileTooLarge(
                f"{size} bytes, past the {MAX_WORKFLOW_FILE_BYTES} this reads"
            )
        return json.load(handle)


def _save_workflow_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)


# The API graph ComfyUI converted an editor-format file into (#1530), kept
# BESIDE the file rather than over it: the file stays byte-identical to what
# ComfyUI holds, so it still re-opens there and still deduplicates against a
# re-pull. Not ``.json``, so the folder listing never reads it as a workflow.
CONVERTED_SUFFIX = ".api"


def _editor_digest(workflow: dict) -> str:
    """Which version of an editor file a converted graph was made from."""
    return hashlib.sha256(
        workflow_bindings.canonical(workflow).encode("utf-8")
    ).hexdigest()


def converted_graph(path: str, workflow: dict) -> dict | None:
    """The API graph stored beside the editor file at *path*, or ``None``.

    ``None`` too when it will not read, or when it was converted from another
    version of the file: a file overwritten since is a different workflow, and
    running the old conversion would run the wrong graph.
    """
    sidecar = f"{path}{CONVERTED_SUFFIX}"
    if not os.path.isfile(sidecar):
        return None
    try:
        stored = _load_workflow_json(sidecar)
    except (OSError, ValueError, RecursionError) as exc:
        logger.warning(
            "Converted graph %s will not load, so %s runs as an editor file: %s",
            sidecar,
            path,
            exc,
        )
        return None
    if not isinstance(stored, dict):
        logger.warning("Converted graph %s is not a JSON object; ignored.", sidecar)
        return None
    if stored.get("converted_from") != _editor_digest(workflow):
        logger.info(
            "Converted graph %s was made from another version of %s; ignored "
            "until the file is converted again.",
            sidecar,
            path,
        )
        return None
    return api_graph(stored)


def runnable_document(path: str, workflow: dict) -> dict:
    """*workflow*, or the graph it was converted into if it is an editor file.

    What every reader that runs or parameterises a stored file goes through,
    so a converted editor file reads as the API graph it now has. PixlStash's
    own keys (bindings, output choice) are the file's and carry over.
    """
    if api_graph(workflow) is not None:
        return workflow
    graph = converted_graph(path, workflow)
    if graph is None:
        return workflow
    own = {k: v for k, v in workflow.items() if str(k).startswith("pixlstash_")}
    return {**own, **graph}


def _converted_mtime_ns(path: str) -> int:
    """The converted graph's mtime beside *path*, 0 when there is none."""
    try:
        return os.stat(f"{path}{CONVERTED_SUFFIX}").st_mtime_ns
    except FileNotFoundError:
        return 0
    except OSError as exc:
        logger.warning("Could not stat the converted graph of %s: %s", path, exc)
        return 0


def store_converted_graph(path: str, workflow: dict, graph: dict) -> None:
    """Write *graph* beside the editor file at *path*, which holds *workflow*."""
    _save_workflow_json(
        f"{path}{CONVERTED_SUFFIX}",
        {"converted_from": _editor_digest(workflow), "prompt": graph},
    )


def _store_workflow(
    hub, name: str, workflow: dict, *, overwrite: bool = False, keep_both: bool = False
) -> dict:
    """Store *workflow* as *name* in the user folder, or match a stored copy.

    Shared by the import route and the watched inbox, so a dropped file and a
    file put in the folder are stored the same way.

    Raises:
        FileExistsError: *name* holds a different workflow and neither
            *overwrite* nor *keep_both* is set.
        NotAWorkflowError: *workflow* is not shaped like a ComfyUI workflow.
        RecursionError: The document nests too deeply to compare.
        ValueError: *name* escapes the user folder.
    """
    check_comfy_workflow(workflow)
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
    found = _find_stored_copy(wanted)
    if found is not None:
        source, existing = found
        topology_hash, card_key = _file_in_hub(hub, existing, workflow)
        return {
            "status": "success",
            "name": existing,
            # Which folder matched: not on the wire, like `workflow_key`
            # below; the pull reads it to name a workflow PixlStash ships.
            "source": source,
            "workflow_dir": workflow_dir,
            "matched": True,
            "topology_hash": topology_hash,
            # Not in ``ComfyUIWorkflowImportResponse`` and therefore not on the
            # wire: it is here so the caller can name the card in its
            # ``CHANGED_WORKFLOWS`` event without filing the graph twice.
            "workflow_key": card_key,
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
    topology_hash, card_key = _file_in_hub(hub, name, workflow)
    return {
        "status": "success",
        "name": name,
        "workflow_dir": workflow_dir,
        "matched": False,
        "topology_hash": topology_hash,
        "workflow_key": card_key,
    }


def store_pulled_workflow(hub, name: str, workflow: dict) -> dict:
    """File one document pulled from ComfyUI the way an import files it (#1440).

    :func:`_store_workflow` with ``keep_both``, so a copy of a stored workflow
    is matched rather than stored twice and a name taken by a different
    workflow gets the ``(2)`` suffix rather than a refusal. **The caller holds
    ``workflow_inbox.INBOX_LOCK``** (the pull task does, around its dismissal
    check and its origin row as well). Adds ``builtin``: a match in the
    built-in folder is a workflow PixlStash ships, which has no user file to
    delete, so the pull reports it apart.
    """
    result = _store_workflow(hub, name, workflow, keep_both=True)
    result["builtin"] = bool(
        result.get("matched") and result.get("source") == "built-in"
    )
    return result


def claim_stored_workflow(hub, name: str) -> None:
    """The owner handed *name* over: it is theirs, not pull-written (#1440).

    Shared by both hand-over paths - the import route and the watched inbox -
    so a file the owner gives PixlStash is never folded into the one-offs a
    pull's files may be, whichever way it arrived. Logged, never raised: the
    file is stored either way.
    """
    if hub is None:
        return
    try:
        workflow_origin.claim_file(hub, name)
    except sqlite3.Error as exc:
        logger.warning(
            "Stored workflow %s, but could not record it as the owner's; if a "
            "pull wrote it first it can still be counted as a one-off: %s",
            name,
            exc,
        )


def store_workflow_copy(hub, name: str, workflow: dict) -> tuple[str, str | None]:
    """Write *workflow* into the user folder beside whatever is already there.

    What ``POST /workflows/{key}/duplicate`` and
    ``POST /workflows/{key}/insert-lora-loader`` write with, and deliberately
    **not** :func:`_store_workflow`: that one matches an identical stored copy
    and hands its name back, which is right for an import (a file the library
    already has is not a second workflow) and is exactly wrong here, where a
    second copy of the same workflow is the whole request.

    The name is made free by the same ``(2)`` counter the import uses, so a
    duplicate of a duplicate lands beside its sibling rather than over it.

    **The shape check and the placeholder migration are NOT skipped**, only the
    match: a file written here has to be as loadable as an imported one, and
    ``_store_workflow``'s own comment says why the migration matters - a
    workflow stored with placeholder tokens beside migrated siblings is one
    that will not run.

    Args:
        hub: The hub, or ``None``; filing is secondary to storing.
        name: The name asked for, with or without its ``.json``.
        workflow: The document to write.

    Returns:
        ``(stored name, card key)`` - the key being ``None`` when the graph
        could not be filed, which does not stop the file being written.

    Raises:
        NotAWorkflowError: *workflow* is not shaped like a ComfyUI workflow.
        OSError: The file could not be written.
    """
    check_comfy_workflow(workflow)
    workflow, _migrated = workflow_bindings.migrate_placeholders(workflow)
    stored = _normalize_workflow_name(name) or "workflow.json"
    workflow_dir = workflow_user_dir()
    os.makedirs(workflow_dir, exist_ok=True)
    # Under the lock the import and the delete take. Finding a free name and
    # then writing it is a check-then-act, and these handlers are sync, so
    # FastAPI runs two of them on the thread pool at once: without this, two
    # duplicates both see `… (copy).json` absent, both write it, and one 201
    # hands back a key pointing at the other's bytes. It also stops a delete
    # landing between the write and the filing and leaving a hub row naming a
    # file already in the trash.
    with workflow_inbox.INBOX_LOCK:
        path = resolve_path_within(workflow_dir, stored)
        stem = os.path.splitext(stored)[0]
        counter = 2
        while os.path.exists(path):
            stored = f"{stem} ({counter}).json"
            path = resolve_path_within(workflow_dir, stored)
            counter += 1
        _save_workflow_json(path, workflow)
        _topology_hash, card_key = _file_in_hub(hub, stored, workflow)
    logger.info("Stored a copy of a workflow as %s.", stored)
    return stored, card_key


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


def trash_user_workflow(hub, workflow_name: str) -> str:
    """Send one stored workflow to the trash and forget its rows.

    Shared with ``DELETE /workflows/{key}``, so the card route and the file
    route delete a workflow the same way: through the inbox, with the
    migration backup, the input modes, the pins and the place on its card
    each forgotten on its own. Only the **user** folder resolves here, so a
    built-in is a 404 rather than a deletion nobody can undo.

    Args:
        hub: The hub, or ``None``. The file goes either way; the rows it
            leaves behind are forgotten only if there is somewhere to forget
            them. Takes the hub rather than the whole server so it matches
            :func:`store_workflow_copy` above and needs no server to test.
        workflow_name: The stored file to delete.

    Returns:
        The normalized name that was deleted.
    """
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
            # Inside the lock, unlike the forgets below: a pull checks for a
            # dismissal and stores under this same lock, so a delete landing
            # mid-pull is seen by the very next entry instead of being undone.
            if hub is not None:
                _dismiss_from_pulls(hub, stored_name, normalized)
    except (TrashPermissionError, OSError, RecursionError, ValueError) as exc:
        logger.warning("Failed to delete workflow %s: %s", normalized, exc)
        raise HTTPException(status_code=500, detail="Failed to delete workflow")
    # The placeholder migration's backup and the converted graph go with the
    # workflow they were made from.
    for what, extra in (
        ("migration backup", f"{path}{workflow_bindings.BACKUP_SUFFIX}"),
        ("converted graph", f"{path}{CONVERTED_SUFFIX}"),
    ):
        if not os.path.exists(extra):
            continue
        try:
            os.remove(extra)
        except OSError as exc:
            logger.warning(
                "Deleted workflow %s but not its %s %s: %s",
                normalized,
                what,
                extra,
                exc,
            )
    if hub is not None:
        # The file is already gone, so its rows describe nothing; they would
        # only come back into force if a file of the same name is imported
        # later. Each is updated on its own, so one failing keeps the others.
        for what, forget in (
            ("picture-input modes", lambda: forget_input_modes(hub, stored_name)),
            (
                "parameter pins",
                lambda: replace_parameter_pins(hub, stored_name, None),
            ),
            (
                "place on its workflow card",
                lambda: workflow_cards.forget_file(hub, stored_name),
            ),
        ):
            try:
                forget()
            except Exception as exc:
                logger.warning(
                    "Deleted workflow %s but could not update its %s: %s",
                    normalized,
                    what,
                    exc,
                )
    return normalized


def _dismiss_from_pulls(hub, stored_name: str, normalized: str) -> None:
    """Mark every ComfyUI path *stored_name* was pulled from dismissed.

    The opposite of the forgets that follow a delete: it REMEMBERS, so the
    next pull does not bring the file back. Logged rather than raised - the
    file is already in the trash, and failing the delete now would report a
    deletion that happened as one that did not.
    """
    try:
        workflow_origin.dismiss_file(hub, stored_name)
    except sqlite3.Error as exc:
        logger.warning(
            "Deleted workflow %s but could not record the dismissal; a later "
            "pull from ComfyUI may bring it back: %s",
            normalized,
            exc,
        )


def _find_stored_copy(wanted: str) -> tuple[str, str] | None:
    """``(source, name)`` of a stored workflow whose canonical content is *wanted*.

    ``source`` is the folder it was found in (``user`` or ``built-in``), so a
    caller can tell a workflow PixlStash ships from a user file of the same
    name without guessing from the name.
    """
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
                    return source, entry
            except (OSError, ValueError, RecursionError) as exc:
                logger.warning(
                    "Could not read %s workflow %s to compare an import: %s",
                    source,
                    path,
                    exc,
                )
    return None


def _file_in_hub(hub, name: str, workflow: dict) -> tuple[str | None, str | None]:
    """File the workflow in the library: ``(topology hash, card key)``.

    Content-addressed and idempotent, so a workflow the library already has
    from its pictures lands on that same row. Not being filed does not stop
    the import: the file is what runs.

    *name* is the name the file is stored under, and it is what puts the FILE on
    the card its pictures already made (``hub/workflow_cards.py``). A UI-format
    file has only a topology, so it lands on a card with no assets.
    """
    if hub is None:
        return None, None
    try:
        graph = api_graph(workflow)
        if graph is None:
            # A converted editor file files its API graph (#1530): the card
            # gets model slots and a structural hash instead of a topology.
            try:
                path = resolve_path_within(workflow_user_dir(), name)
            except ValueError:
                path = None
            if path is not None:
                graph = converted_graph(path, workflow)
        if graph is None:
            topology_hash = record_ui_graph(hub, workflow)
            structural_hash = None
        else:
            keys = record_api_graph(hub, graph)
            topology_hash, structural_hash = keys.topology_hash, keys.structural_hash
        return topology_hash, _card_the_file(hub, name, topology_hash, structural_hash)
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
    return None, None


def _card_the_file(
    hub, name: str, topology_hash: str, structural_hash: str | None
) -> str | None:
    """Put the stored file on its card; return that card's key, or None.

    Its own handler rather than the one around the filing above: a card is the
    optional half, and letting it raise would make the import answer
    ``topology_hash: null`` for a graph that *was* filed - the essential half
    retracted by the secondary one. The backfill finder picks the card up.

    The key is returned so the import can name the card in its
    ``CHANGED_WORKFLOWS`` event. ``None`` means the card was not written, and
    the event then says only "look again", which is all it ever promises.
    """
    try:
        return workflow_cards.record_file(hub, name, topology_hash, structural_hash)
    except Exception as exc:
        logger.warning(
            "Imported workflow %s is filed but not on a card yet, which failed "
            "with %s: %s",
            name,
            type(exc).__name__,
            exc,
        )
    return None


# ponytail: one request submits its runs one after another, uploads included;
# a queue of its own (and a higher cap) if large batches become routine.
MAX_RUNS_PER_REQUEST = 200

# What a LoRA slot may be given: an adapter, or a file the header reader could
# not place, which on this shelf is usually an adapter in a format it has not
# been taught. Everything else it holds is refused by name (#1310).
LOADABLE_ADAPTER_FILE_KINDS = frozenset({FILE_ADAPTER, FILE_UNKNOWN})


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


def _describe_lora_insertion(
    graph: dict | None,
    object_info: dict | None,
    error: str | None,
    digest_loader: bool = True,
) -> dict | None:
    """Where a LoRA loader would go, for the owner to see before a run (#1376).

    Args:
        graph: The API-format graph, or ``None`` for a UI-format file.
        object_info: The map already read for this request, or ``None``.
        error: Why ComfyUI could not be asked, when it could not.
        digest_loader: Whether this surface's run would allow the
            ComfyUI-PixlStash loader; ``False`` for a replay, so the plan does
            not warn about a node that route will never insert.

    Returns:
        ``None`` when the graph already has a LoRA loader, else
        ``{"plan", "reason"}``: the plan from :func:`plan_lora_insertion`, or
        ``None`` and the reason no loader can be added.
    """
    if graph is None:
        return {
            "plan": None,
            "reason": (
                "This workflow is saved in ComfyUI's UI format, so PixlStash "
                "cannot add a LoRA loader to it."
            ),
        }
    if detect_lora_targets(graph):
        return None
    if object_info is None:
        return {
            "plan": None,
            "reason": f"PixlStash could not ask ComfyUI where a loader would go: {error}",
        }
    try:
        plan = plan_lora_insertion(graph, object_info)
        plan["pixlstash_loader"] = plan["pixlstash_loader"] and digest_loader
        return {"plan": plan, "reason": None}
    except LookupError as exc:
        logger.info("No LoRA loader can be added to this graph: %s", exc)
        return {"plan": None, "reason": str(exc)}


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


# ponytail: one entry per file version; stale versions age out of the LRU.
@functools.lru_cache(maxsize=512)
def _describe_workflow(
    path: str, source: str, mtime_ns: int, size: int, converted_mtime_ns: int = 0
) -> dict:
    """List metadata for one workflow file, recomputed only when the file changes.

    Keyed on mtime and size so detection runs once per file version, and a file
    that stays broken is logged once rather than on every menu open. The
    converted graph's mtime is in the key too, so converting a file (#1530)
    makes it runnable on the next list rather than on its next edit.
    """
    try:
        payload = runnable_document(path, _load_workflow_json(path))
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
        # request per workflow to know whether to offer the shelf. WITHOUT the
        # value each slot holds: this route is open to share-link tokens, and a
        # slot's value is a LoRA's filename or digest - the owner's model
        # inventory, which /models/ and /adapters/ keep from those tokens. The
        # owner-only card detail read carries the values.
        "lora_slots": [
            {key: slot[key] for key in ("node_id", "class_type", "field", "by")}
            for slot in detect_lora_targets(workflow_parameters.api_graph(payload))
        ],
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
    ``POST /workflows/run`` still fills only that one target, and checks
    it itself.
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


def _read_embedded_metadata(server, pic_id: int) -> dict:
    """The picture file's embedded metadata.

    Raises:
        HTTPException: 404 when the picture or its file cannot be resolved,
            500 when the file exists but its metadata cannot be read.
    """
    file_path = _resolve_picture_file(server, pic_id)
    try:
        return ImageUtils.extract_embedded_metadata(file_path)
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


def _load_embedded_api_prompt(
    server, pic_id: int, object_info: dict | None = None
) -> tuple[dict | None, list[str]]:
    """The graph a replay of *pic_id* would submit, and why there is none.

    The embedded API-format ``prompt`` chunk when the file has one, and
    otherwise the editor ``workflow`` chunk rebuilt into one against
    *object_info*.

    **The rebuild lives here rather than in a handler on purpose.** Every route
    that runs a picture's own graph comes through this function. It was written
    that way while ``POST /comfyui/run_recipe`` still existed and could have
    held it; #1410 has since retired that route, and the card run's
    embedded-picture tier in ``routes/workflows.py`` is now the only caller -
    so a rebuild written into the handler would have been deleted along with
    it, and the offer the Recipe tab makes would have quietly stopped meaning
    anything.

    ``object_info`` of ``None`` is "ComfyUI was not asked", which is a refusal
    and not a licence to guess: reading a positional widget array at all needs
    ComfyUI's own node definitions.

    Returns:
        ``(graph, [])`` when there is something to submit, ``(None, problems)``
        otherwise. **The two "no"s are different and the caller picks.** An
        empty ``problems`` means the picture carries no workflow - A1111, a
        stripped PNG, a photo - and the resolver moves on to the next candidate
        without a word. A non-empty one means this picture HAS a workflow that
        would not rebuild, which is worth saying: the resolver logs it against
        that candidate, and the recipe read hands the same sentences to the
        Recipe tab as ``conversion_problems``.

    Raises:
        HTTPException: 404 when the picture or its file cannot be resolved,
            500 when the file exists but its metadata cannot be read.
    """
    metadata = _read_embedded_metadata(server, pic_id)
    prompt_graph = find_comfy_api_prompt(metadata)
    if prompt_graph:
        return prompt_graph, []
    editor_graph = find_comfy_workflow(metadata)
    if not is_ui_graph(editor_graph):
        return None, []
    return convert_ui_graph_to_api(editor_graph, object_info)


def _picture_workflow_key(server, pic_id: int) -> str | None:
    """The workflow card this picture's variant is on, or ``None``.

    Two reads and no derivation: the picture names its variant
    (``workflow_structural_hash``) and the hub says which card that variant is
    on. A picture whose graph has not been filed yet, or whose card has not
    been derived yet, honestly has no key - deriving one here would report a
    card the hub does not hold and cannot be asked about.

    Never raises: this is one field of an advisory dialog, and a hub that
    cannot be read must not be what breaks the recipe read.
    """
    hub = getattr(server, "hub", None)
    if hub is None:
        return None
    try:
        pics = server.vault.db.run_immediate_read_task(
            Picture.find,
            id=pic_id,
            select_fields=["id", "workflow_structural_hash"],
        )
        structural_hash = (
            getattr(pics[0], "workflow_structural_hash", None) if pics else None
        )
        if not structural_hash:
            return None
        return workflow_cards.key_of_variant(hub, structural_hash)
    except Exception as exc:
        logger.warning(
            "[comfyui] Could not read the workflow card for picture id=%s: %s; "
            "reporting the recipe without a workflow_key.",
            pic_id,
            exc,
        )
        return None


def _a1111_strengths(value) -> dict:
    """An A1111 LoRA weight as ``{"model": float}``, or ``{}`` if it is not one.

    A1111 writes the weight as text, so it is parsed here to keep ``strengths``
    ONE type across both branches of this route - a client formatting a number
    must not have to find out that this branch hands it a string. Two cases
    give nothing rather than something of another shape: the same LoRA used
    twice in a picture, which ``services/a1111_recipe.py`` records as both
    weights joined by a comma (reporting either alone would be a claim about
    which applied), and a non-finite value, which cannot be rendered at all
    (``allow_nan=False``) and would turn this read into a 500.
    """
    try:
        weight = float(value)
    except (TypeError, ValueError) as exc:
        # Debug, not a warning: an absent weight, a bare `<lora:name>` and the
        # comma-joined double use are all ordinary A1111 input, so this fires
        # on the common case and says nothing is wrong. Logged all the same,
        # because a silently dropped field is how a parsing change hides.
        logger.debug("No A1111 LoRA strength from %r: %s", value, exc)
        return {}
    return {"model": weight} if math.isfinite(weight) else {}


def _a1111_setting(value: str):
    """An A1111 field value, as a number where it is wholly one.

    A1111 writes every field as text, so ``Steps: 20`` would arrive as
    ``"20"`` while the ComfyUI branch reports ``20`` - and ``steps`` is a key
    both branches carry, so that is precisely where a client reaches for a
    number and finds a string. Coerced here rather than left to the client.

    Only a value that is *entirely* a number is converted: ``512x768`` and
    ``Euler a`` stay the text they are, which is why the contract describes
    this block as name/value pairs and not as a typed shape.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if not math.isfinite(number):
        return value
    return int(number) if number.is_integer() and "." not in str(value) else number


def _int_or_none(value) -> int | None:
    """*value* as an ``int``, or ``None`` where it is not one."""
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "[comfyui] Reporting no seed for an A1111 picture: %r is not an "
            "integer (%s). The reduction should already have refused it, so "
            "this means the two disagree.",
            value,
            exc,
        )
        return None


def _a1111_recipe_payload(recipe: A1111Recipe) -> dict:
    """The recipe an A1111 picture can answer with, from its reduced graph.

    Read off :func:`reduce_a1111`'s nodes rather than re-parsing the infotext,
    so this endpoint and the hub agree about what the picture's recipe is: the
    node names here are that function's own (``checkpoint``, ``positive``,
    ``negative``, ``sampler``, ``lora_N``).

    ``node_count`` and ``node_classes`` are deliberately left at their defaults.
    They exist for the consent decision - what would execute on the owner's
    ComfyUI - and an A1111 picture executes nothing, so a count of the reduction
    the hub happens to build would be a number about PixlStash, not about the
    picture.
    """
    widgets = {
        node_id: dict(node.instance_widgets) for node_id, node in recipe.nodes.items()
    }
    sampler = widgets.get("sampler", {})
    loras = [
        (node_id, w)
        for node_id, w in sorted(widgets.items())
        if node_id.startswith("lora_") and w.get("lora_name")
    ]
    checkpoint = widgets.get("checkpoint", {}).get("ckpt_name")
    return {
        "available": False,
        # Not ``no_prompt_chunk``: there IS a recipe here, it is simply not a
        # graph any ComfyUI could be handed. A client that does not know the
        # value still falls through to "nothing to replay", which is true.
        "reason": "a1111",
        "source": "a1111",
        "summary": "A1111 parameters",
        "positive_prompt": widgets.get("positive", {}).get("text") or None,
        "negative_prompt": widgets.get("negative", {}).get("text") or None,
        # `_integer_text` has already refused anything `int()` would, so this
        # cannot raise; belt and braces so the two cannot drift apart, since
        # the version of this that trusted `str.isdigit()` was a 500 on a
        # crafted `parameters` chunk.
        "seed": _int_or_none(recipe.seed),
        "models": [checkpoint] if checkpoint else [],
        "loras": [w["lora_name"] for _, w in loras],
        "settings": {k: _a1111_setting(v) for k, v in sampler.items() if v is not None},
        # The same shape the ComfyUI branch reports, with no node named: the
        # reduction's own ids ("lora_0") are not in any graph, and `node_id` is
        # what a client sends back as `lora_node_id` to swap a slot in a
        # replay. There is no replay here, so there is no node to name.
        "lora_slots": [
            {
                "node_id": None,
                "class_type": None,
                "field": "lora_name",
                "value": w["lora_name"],
                "by": "filename",
                "strengths": _a1111_strengths(w.get("strength")),
            }
            for _node_id, w in loras
        ],
    }


def _editor_graph_recipe_payload(workflow: dict, problems: list[str]) -> dict:
    """The recipe an editor graph answers with when it would not convert.

    The picture WAS made in ComfyUI and its graph is right there, so the Recipe
    tab fills in - the prompt, the models, the seed - and only the offer to run
    it again is withheld, with the reason named. That is the difference this
    payload exists to draw: "nothing was made in ComfyUI here" and "this is a
    ComfyUI picture PixlStash cannot hand back to ComfyUI" are two answers, and
    a UI-only picture used to get the first one.

    ``settings`` and ``negative_prompt`` stay empty: both are read off the
    resolved API graph, and reading them off the editor's positional widget
    array is the guessing this whole path refuses to do.
    """
    gen_info = extract_generation_info(workflow)
    stats = summarize_comfy_workflow(workflow)
    summary_parts = [f"Editor Workflow · {stats['node_count']} nodes"]
    if stats["link_count"] is not None:
        summary_parts.append(f"{stats['link_count']} links")
    return {
        "available": False,
        # **Two different "no", because they send the reader to two different
        # places.** A graph this ComfyUI cannot be asked about is a machine
        # that is switched off, which the reader fixes by starting it; a graph
        # that would not rebuild is a fact about the file. Collapsing them told
        # somebody with no ComfyUI running that their workflow was unrebuildable.
        "reason": (
            "comfyui_unreachable" if problems == [NO_OBJECT_INFO] else "editor_graph"
        ),
        "source": "comfyui",
        "summary": " · ".join(summary_parts),
        "positive_prompt": gen_info["positive_prompt"],
        "negative_prompt": None,
        "settings": {},
        "seed": gen_info["seed"],
        "seed_text": None if gen_info["seed"] is None else str(gen_info["seed"]),
        "models": gen_info["models"],
        "loras": gen_info["loras"],
        "node_count": stats["node_count"],
        # One sentence per thing that could not be read, so the tab can say why
        # rather than "it did not work".
        "conversion_problems": problems,
    }


def _editor_graph_topology(rebuilt: dict | None, editor_graph: dict) -> str | None:
    """The topology of the graph the Recipe tab is actually showing.

    **Deliberately NOT the picture's stored ``workflow_topology_hash``.** That
    column is written by the extraction pass, which reads the API ``prompt``
    chunk or A1111 infotext and nothing else - neither of which this answer
    came from. So on a picture carrying an editor graph *and* A1111 text the
    column names a different reading of the same file, and the Recipe tab's
    Open sends the reader to a workflow they were never shown; on a picture
    carrying only an editor graph it is NULL, and Open is not offered at all.
    Reporting the stored value either way is how Open came to open nothing.

    A rebuilt graph is keyed by the ordinary API path, so it agrees exactly
    with the same workflow saved in API format and Open reaches the card those
    siblings are on. Without a rebuild the editor serialisation is keyed
    directly - which is what :func:`ui_topology_hash` is for - and that agrees
    with the API twin for most graphs but not all, so it is the fallback and
    not the rule.

    Never raises: this is one link in an advisory panel, and a graph the hash
    layer will not reduce must not be what breaks the whole recipe read.
    """
    try:
        if rebuilt is not None:
            return api_topology_hash(rebuilt)
        return ui_topology_hash(editor_graph)
    except WorkflowGraphError as exc:
        logger.info(
            "[comfyui] Editor graph will not reduce, so the recipe reports no "
            "topology to open: %s",
            exc,
        )
        return None
    except Exception as exc:
        logger.warning("[comfyui] Could not key this editor graph's topology: %s", exc)
        return None


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


# How long a fetched `/object_info` map may be reused, and by whom. It is
# several megabytes on an install with a few node packs, so the one caller that
# asks as fast as a person presses an arrow key - converting the editor graph
# of each picture the lightbox steps onto - cannot fetch it per step. A minute
# is short enough that a node or model installed while the app is open shows up
# on the next look rather than after a restart.
OBJECT_INFO_CACHE_TTL_S = 60.0
# `{url: (read_at, object_info, error)}`. A FAILURE is cached too: the read this
# serves is fired per arrow-key, `OBJECT_INFO_TIMEOUT_S` is 15 seconds, and the
# route is synchronous - so a ComfyUI that is merely not running would otherwise
# hold one worker thread per keypress for 15 seconds each and take the rest of
# the API down with it. A ComfyUI that comes back is picked up within the
# minute.
_object_info_cache: dict[str, tuple[float, dict | None, str | None]] = {}


def _forget_cached_object_info() -> None:
    """Drop the cached maps. For tests, which change what ComfyUI answers."""
    _object_info_cache.clear()


def _read_object_info(
    comfyui_url: str, *, cached: bool = False
) -> tuple[dict | None, str | None]:
    """Return ``(object_info, error)``; ``(None, why)`` when ComfyUI cannot be asked.

    Kept apart from :func:`_inspect_graph` so one fetch can serve both the
    pre-flight and a LoRA swap or insertion on the same run, and so the swap
    can be applied *before* the graph is judged.

    ``cached`` reuses the last minute's answer - the map OR the failure; see
    the cache's own note for why a failure counts. **Only for reading an editor
    graph**, which needs the map to be readable at all; never for deciding
    whether a graph may run, where the question is what ComfyUI has now.
    """
    now = time.monotonic()
    if cached:
        entry = _object_info_cache.get(comfyui_url)
        if entry is not None and (now - entry[0]) < OBJECT_INFO_CACHE_TTL_S:
            return entry[1], entry[2]
    result: tuple[dict | None, str | None]
    try:
        result = (fetch_object_info(comfyui_url), None)
    except RuntimeError as exc:
        logger.info(
            "[comfyui] Recipe pre-flight skipped, ComfyUI not reachable at %s: %s",
            comfyui_url,
            exc,
        )
        result = (None, str(exc))
    if cached:
        # Pruned on write rather than on a timer: these maps are megabytes, the
        # keys are ComfyUI URLs, and one the owner has changed away from must
        # not be pinned for the life of the process.
        for url, entry in list(_object_info_cache.items()):
            if (now - entry[0]) >= OBJECT_INFO_CACHE_TTL_S:
                del _object_info_cache[url]
        # Stamped when the answer ARRIVED, not when the request left: a fetch
        # can take `OBJECT_INFO_TIMEOUT_S`, and stamping it `now` would file an
        # entry a quarter of its own lifetime old.
        _object_info_cache[comfyui_url] = (time.monotonic(), result[0], result[1])
    return result


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


class ComfyUILoraInsertionResponse(BaseModel):
    """Where a LoRA loader would go in a graph that has none (#1376).

    ``plan`` names the model source (and CLIP source, ``None`` for a model-only
    loader) the loader would take, and every input it would rewire; it is
    ``None`` when no loader can be added, and ``reason`` says why.
    """

    plan: Optional[dict] = None
    reason: Optional[str] = None


class ComfyUIWorkflowLoraInsertionResponse(ComfyUILoraInsertionResponse):
    """A saved workflow's LoRA insertion; ``has_lora_loader`` needs none."""

    workflow: str
    # None for a UI-format file: whether it has a loader cannot be read from it.
    has_lora_loader: Optional[bool] = False


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


class ComfyUIWorkflowConvertResponse(BaseModel):
    """An editor file with the API graph ComfyUI converted it into (#1530)."""

    # The stored editor file the graph now sits beside.
    name: str
    # True when that file was already stored; False when this stored it.
    matched: bool
    # The card the file is on now; None when it could not be filed.
    workflow_key: Optional[str] = None


class ComfyUIWorkflowPullStartResponse(BaseModel):
    """A pull of ComfyUI's saved workflows, queued or already running."""

    # "started" or "already_running".
    status: str
    task_id: Optional[str] = None


class ComfyUIWorkflowPullSummary(BaseModel):
    """What one finished pull found, computed with it and never stored."""

    # How many workflows ComfyUI listed.
    listed: int = 0
    # Stored here for the first time.
    pulled: int = 0
    # A path pulled before that now holds different content, stored beside
    # the earlier copy (which stays as it was).
    changed: int = 0
    # Already stored here, matched by content.
    matched: int = 0
    # Identical to a workflow PixlStash ships.
    already_shipped: int = 0
    # Deleted here after an earlier pull, so not brought back.
    skipped_dismissed: int = 0
    # Could not be read from ComfyUI or stored here.
    failed: int = 0
    # Pulled before and no longer listed by ComfyUI. The local file stays.
    gone: int = 0
    # False when ComfyUI's node list could not be read, which makes every
    # workflow unchecked rather than fine.
    nodes_checked: bool = False
    # Workflows naming a node class this ComfyUI does not have.
    missing_nodes: int = 0
    # Workflows whose node classes were not checked.
    nodes_unchecked: int = 0
    # The absent classes, by name. Which pack provides one is not known here.
    missing_node_classes: list[str] = []
    # Stored workflows whose shape a picture in the library already made: the
    # ones the owner had, as opposed to new to PixlStash.
    known_from_pictures: int = 0
    # Workflows naming a model file this ComfyUI does not list. Advisory for an
    # editor-format file, whose model names are read by position.
    missing_models: int = 0
    # Model values across the pull that could not be read at all, so were
    # never checked. A short missing list is only as good as this is small.
    models_unread: int = 0
    # Workflows whose models were not checked (ComfyUI unreachable, or the
    # document would not read).
    models_unchecked: int = 0
    # The absent model files, by name.
    missing_model_files: list[str] = []
    # The cards the pull filed a file on.
    workflow_keys: list[str] = []


class ComfyUIWorkflowPullStateResponse(BaseModel):
    """The most recent pull since the server started.

    ``status`` is ``idle`` when there has been none, else the task state:
    ``pending``, ``running``, ``completed`` or ``failed``. ``summary`` is set
    once it completed, ``error`` once it failed.
    """

    status: str
    task_id: Optional[str] = None
    comfyui_url: Optional[str] = None
    error: Optional[str] = None
    summary: Optional[ComfyUIWorkflowPullSummary] = None


class ComfyUIRecipeModelSlot(BaseModel):
    """One model the graph loads, as the overlay's Recipe section shows it.

    ``model_id`` and ``verified`` are absent for a scoped token: which shelf row
    a file is, is a fact about the library rather than about this picture.
    ``verified`` true means the graph named the file by its digest and exactly
    one shelf model has it - the same tier the shelf's own picture counts use.
    """

    # No `extra="allow"` here, unlike its siblings in this module. This route
    # varies its answer by credential, so the model is a disclosure boundary: a
    # key some future version of the service starts returning must be declared
    # here before it reaches anybody, rather than passing through unread.

    name: str
    widget: str
    strength: Optional[float] = None
    # The precision the file was stored at, as one canonical id (`bf16`,
    # `fp8_e4m3`, `q4_k_m`, `mixed`), or null where nothing records it.
    #
    # **It crosses the disclosure boundary on the same rule as the rest**, and
    # by construction rather than by a check here: a scoped token's slot
    # carries only what `quant_from_filename(name)` derives, which is a pure
    # function of the `name` above it and so says nothing the response did not
    # already. The shelf's own column - read from the safetensors header, and
    # therefore a fact about the LIBRARY - is only ever mixed in by
    # `_resolve_against_shelf`, which the service runs for a fully-unscoped
    # owner alone.
    quant: Optional[str] = None
    model_id: Optional[int] = None
    # The name the owner gave that shelf row, for the chip to show instead of
    # the file's. A fact about the library, so it is set on the same owner-only
    # path as `model_id` and is absent for a scoped token.
    display_name: Optional[str] = None
    verified: bool = False


class ComfyUIRecipeSetting(BaseModel):
    """One sampler setting of the recipe: ``steps``, ``cfg``, ``width``…"""

    # No `extra="allow"` here, unlike its siblings in this module. This route
    # varies its answer by credential, so the model is a disclosure boundary: a
    # key some future version of the service starts returning must be declared
    # here before it reaches anybody, rather than passing through unread.

    label: str
    value: Any = None
    node: Optional[str] = None


class ComfyUIRecipeInput(BaseModel):
    """The resolution lock: which picture one input of the run loaded.

    **Served to a fully-unscoped owner only** - a row names another picture's id
    and its content hash, which a picture-scoped token is refused everywhere
    else. ``input_picture_id`` is null once that picture has left this library,
    which does not unmake what was made from it: ``pixel_sha`` still identifies
    it.
    """

    # No `extra="allow"` here, unlike its siblings in this module. This route
    # varies its answer by credential, so the model is a disclosure boundary: a
    # key some future version of the service starts returning must be declared
    # here before it reaches anybody, rather than passing through unread.

    node_ref: str
    position: int
    pixel_sha: str
    input_picture_id: Optional[int] = None


class ComfyUIPictureWorkflowResponse(BaseModel):
    """The displayable graph a picture carries.

    Deliberately only that: what a picture was MADE with is the recipe read
    (#1313), which reads the graph that executed and answers for A1111 pictures
    too. This one serves the editor's format, for Copy, Download and paste."""

    model_config = ConfigDict(extra="allow")

    workflow: dict
    is_api_format: bool = False
    summary: Optional[str] = None
    models: list[str] = []
    loras: list[str] = []
    positive_prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
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
    # Model-shaped values on loaders PixlStash cannot read, so never checked.
    unchecked_models: int = 0


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
    # Which generator wrote the recipe: "comfyui" for an embedded API graph,
    # "a1111" for a picture whose recipe is A1111 infotext.
    source: str = "comfyui"
    summary: Optional[str] = None
    # True when the graph behind this answer was rebuilt from the picture's
    # EDITOR chunk rather than read from the API one ComfyUI executed. It is a
    # faithful rebuild or no rebuild at all, but it is a rebuild, and a reader
    # deciding whether to trust a settings value is entitled to know.
    converted_from_editor_graph: bool = False
    # Why an editor graph could not be rebuilt, one sentence each - empty for
    # every other answer. Read by the Recipe tab, which prints them under its
    # refusal to run.
    conversion_problems: list[str] = []
    positive_prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    seed: Optional[int] = None
    # The seed as text as well as a number: ComfyUI draws seeds up to 2**64-1
    # and a JavaScript Number loses digits above 2**53, so a client rendering
    # `seed` prints the wrong one for about half of real seeds.
    seed_text: Optional[str] = None
    # The sampler settings (steps, cfg, guidance, sampler, scheduler, denoise,
    # width, height), or the A1111 fields. Read from the file, like the prompts
    # beside it.
    settings: dict = {}
    # Reconciled onto this read (#1313): everything about how a picture was made
    # answers here, so the lightbox's Recipe tab needs one call and `/workflow`
    # is asked only for the graph's bytes.
    topology_hash: Optional[str] = None
    model_slots: list[ComfyUIRecipeModelSlot] = []
    inputs: list[ComfyUIRecipeInput] = []
    # The workflow card this picture's variant is on, None when the hub has
    # not filed or keyed it. An opaque digest of the graph's topology and
    # model slots; what it GROUPS is an owner-only question, asked elsewhere.
    workflow_key: Optional[str] = None
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
    # does not. Each carries its own ``strengths`` (model / clip).
    lora_slots: list[dict] = []
    # Set only when lora_slots is empty: where a loader would be added (#1376).
    lora_insertion: Optional[ComfyUILoraInsertionResponse] = None
    preflight: Optional[ComfyUIPreflightResponse] = None


def _recipe_extras(server, request, pic_id: int, graph: Optional[dict]) -> dict:
    """The half of a picture's recipe that is about this LIBRARY, not the file.

    Which shelf row each model is, which pictures a run actually loaded, the
    seed as text and the topology the picture is filed under. Served from the
    recipe read rather than from the workflow read (#1313): one read answers
    "what was this made with", and a caller that wants the graph's bytes asks
    the other route for them.

    ``graph`` is the API graph, or ``None`` for an A1111 picture - whose models
    are read from its infotext, so it gets the resolution lock, the seed text
    and the topology without a graph to reduce.
    """
    owner = server.auth.is_unscoped_owner_request(request)
    pics = server.vault.db.run_immediate_read_task(
        Picture.find, id=pic_id, select_fields=["id", "workflow_topology_hash"]
    )
    topology = getattr(pics[0], "workflow_topology_hash", None) if pics else None
    return {
        **describe_recipe(
            getattr(server, "hub", None),
            graph,
            ([], []),
            # Not read at all for a caller who will not be served it: the rows
            # name other pictures, and one nobody is going to see is one worth
            # not fetching.
            server.vault.db.run_immediate_read_task(
                resolution_lock_in_session, picture_id=pic_id
            )
            if owner
            else [],
            owner=owner,
        ),
        "topology_hash": topology,
    }


_PULL_IN_FLIGHT = (TaskStatus.PENDING, TaskStatus.RUNNING)


def create_router(server) -> APIRouter:
    router = APIRouter()

    # The most recent pull of ComfyUI's saved workflows, whatever state it
    # ended in. It is the "already running" gate - a double-click must not list
    # and read the whole folder twice - and where a finished pull's summary
    # lives, because the TaskRunner forgets a task the moment it completes. Per
    # router and so per server, and in memory on purpose, the
    # `routes/model_folders.py::_scans` shape: after a restart nothing is
    # pulling.
    last_pull: dict[str, ComfyUIWorkflowPullTask] = {}
    last_pull_lock = threading.Lock()

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
                    _describe_workflow(
                        path,
                        source,
                        stat.st_mtime_ns,
                        stat.st_size,
                        _converted_mtime_ns(path),
                    )
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
        return {
            "status": "success",
            "name": trash_user_workflow(getattr(server, "hub", None), workflow_name),
        }

    def _load_stored_workflow(workflow_name: str) -> tuple[str, str, dict]:
        """``(on-disk name, path, document)`` of a stored workflow, or raise 4xx."""
        name = _normalize_workflow_name(workflow_name)
        if not name:
            raise HTTPException(status_code=400, detail="workflow_name is required")
        path, _source = _resolve_workflow_path(name)
        if not path:
            raise HTTPException(status_code=404, detail="Workflow not found")
        try:
            document = runnable_document(path, _load_workflow_json(path))
        except Exception as exc:
            logger.warning("Failed to read workflow %s: %s", path, exc)
            raise HTTPException(
                status_code=422, detail="This workflow's graph could not be read."
            ) from exc
        return _on_disk_name(path), path, document

    @router.get(
        "/comfyui/workflows/{workflow_name}/lora-insertion",
        summary="Where a LoRA loader would be added to a workflow",
        description=(
            "For a saved workflow with no LoRA loader: the model (and CLIP) "
            "source a loader would take and every input it would rewire, typed "
            "from ComfyUI's object_info, so the owner sees the change before a "
            "run sends insert_lora_loader. plan is null and reason says why "
            "when no loader can be added; has_lora_loader is true, with neither, "
            "when the workflow already has one to swap, and null for a "
            "UI-format file, which may have one PixlStash cannot read."
        ),
        response_model=ComfyUIWorkflowLoraInsertionResponse,
    )
    def get_comfyui_workflow_lora_insertion(request: Request, workflow_name: str):
        name, _path, document = _load_stored_workflow(workflow_name)
        graph = api_graph(document)
        if graph is not None and detect_lora_targets(graph):
            return {"workflow": name, "has_lora_loader": True}
        if graph is None:
            # Not `false`: a UI-format file may well have a loader, and saying
            # it has none would be the claim _resolve_lora_swap declines to
            # make. Unknown, with the reason.
            return {
                "workflow": name,
                "has_lora_loader": None,
                **_describe_lora_insertion(None, None, None),
            }
        object_info, error = None, None
        if graph is not None:
            comfyui_url = _comfyui_url(server.auth.get_user_for_request(request))
            object_info, error = _read_object_info(comfyui_url)
        return {
            "workflow": name,
            **_describe_lora_insertion(graph, object_info, error),
        }

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
        "/comfyui/workflows/import",
        include_in_schema=False,
        summary="Import ComfyUI workflow",
        description=(
            "Stores a workflow JSON, UI or API format, unchanged in the user "
            "workflow directory (a file still carrying placeholder tokens is "
            "stored with them migrated to bindings). A copy of a workflow already stored is matched "
            "to it rather than stored twice. A name taken by a different "
            "workflow is refused unless overwrite or keep_both is set. A "
            "document not shaped like a ComfyUI workflow is refused with 400."
        ),
        response_model=ComfyUIWorkflowImportResponse,
    )
    def import_comfyui_workflow(request: Request, payload: dict = Body(...)):
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
                result = _store_workflow(
                    getattr(server, "hub", None),
                    name,
                    workflow,
                    overwrite=bool(payload.get("overwrite")),
                    keep_both=bool(payload.get("keep_both")),
                )
            # Handed over by the owner, so theirs even if a pull wrote it
            # first: never folded into the hidden one-offs (#1440).
            claim_stored_workflow(getattr(server, "hub", None), result["name"])
            # An imported file lands on a card, so the Workflows view has a
            # new (or newly runnable) one to draw. A "look again" signal: the
            # card's counts and covers are computed per request, so nothing
            # about it is carried here.
            announce_changed_workflows(
                server,
                [key for key in (result.get("workflow_key"),) if key],
                "imported",
                origin_client_id=getattr(request.state, "origin_client_id", None),
            )
            return result
        except NotAWorkflowError as exc:
            logger.warning("Refused importing %s: %s", name, exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RecursionError as exc:
            logger.warning("Refused a workflow import that nests too deeply: %s", exc)
            raise HTTPException(
                status_code=400, detail="Workflow JSON nests too deeply"
            ) from exc
        except FileExistsError as exc:
            raise HTTPException(
                status_code=409, detail="Workflow already exists"
            ) from exc

    @router.post(
        "/comfyui/workflows/convert",
        summary="Store ComfyUI's API conversion of an editor-format workflow",
        description=(
            "Takes what ComfyUI's own `graphToPrompt` returns for the workflow "
            "open on its canvas - `workflow` (editor format) and `output` (API "
            "format) - and stores the API graph beside the matching stored "
            "editor file, never over it. A workflow not stored yet is stored "
            "first, as an import would. The file's card then runs and takes "
            "parameters from the API graph. Sent by the ComfyUI-PixlStash "
            "node's *Convert for PixlStash* command, one workflow at a time."
        ),
        response_model=ComfyUIWorkflowConvertResponse,
        responses={
            400: {"description": "Not an editor workflow and its API graph."},
            409: {"description": "A workflow PixlStash ships; nothing to convert."},
        },
    )
    def convert_comfyui_workflow(request: Request, payload: dict = Body(...)):
        workflow = payload.get("workflow")
        output = payload.get("output")
        if not isinstance(workflow, dict) or api_graph(workflow) is not None:
            raise HTTPException(
                status_code=400, detail="workflow must be an editor-format workflow"
            )
        if not isinstance(output, dict) or not output or api_graph(output) is None:
            raise HTTPException(
                status_code=400, detail="output must be an API-format graph"
            )
        name = _normalize_workflow_name(payload.get("name")) or "workflow.json"
        try:
            resolve_path_within(workflow_user_dir(), name)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid workflow name")
        hub = getattr(server, "hub", None)
        try:
            with workflow_inbox.INBOX_LOCK:
                result = _store_workflow(hub, name, workflow, keep_both=True)
                if result.get("source") == "built-in":
                    raise HTTPException(
                        status_code=409,
                        detail="PixlStash ships this workflow; it has nothing to convert.",
                    )
                stored_name = result["name"]
                path = resolve_path_within(workflow_user_dir(), stored_name)
                # Digested from the file as stored, which is the placeholder-
                # migrated document, so the check on read compares like with
                # like.
                store_converted_graph(path, _load_workflow_json(path), output)
                # Filed again now the graph is there: the store above filed
                # the editor file as a topology only.
                _topology_hash, card_key = _file_in_hub(
                    hub, stored_name, _load_workflow_json(path)
                )
        except NotAWorkflowError as exc:
            logger.warning("Refused converting %s: %s", name, exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RecursionError as exc:
            logger.warning("Refused a conversion that nests too deeply: %s", exc)
            raise HTTPException(
                status_code=400, detail="Workflow JSON nests too deeply"
            ) from exc
        except OSError as exc:
            logger.error("Could not store the converted graph of %s: %s", name, exc)
            raise HTTPException(
                status_code=500, detail="Could not store the converted graph"
            ) from exc
        if not result.get("matched"):
            claim_stored_workflow(hub, stored_name)
        logger.info("Stored ComfyUI's conversion of %s.", stored_name)
        announce_changed_workflows(
            server,
            sorted({key for key in (result.get("workflow_key"), card_key) if key}),
            "imported",
            origin_client_id=getattr(request.state, "origin_client_id", None),
        )
        return {
            "name": stored_name,
            "matched": bool(result.get("matched")),
            "workflow_key": card_key,
        }

    @router.post(
        "/comfyui/workflows/pull",
        summary="Pull every workflow ComfyUI has saved",
        description=(
            "Lists the workflows the configured ComfyUI has saved, over its "
            "userdata API, and stores each one the way an import does: a copy "
            "of a workflow already stored is matched rather than stored twice, "
            "so a pull is safe to repeat. A workflow deleted here after an "
            "earlier pull is skipped rather than brought back. Nothing is "
            "written to ComfyUI. Returns 202 with the id of the task now "
            "queued; watch it in `GET /workers/progress` under "
            "`workers.ComfyUIWorkflowPullTask` and read its summary from "
            "`GET /comfyui/workflows/pull`."
        ),
        status_code=202,
        response_model=ComfyUIWorkflowPullStartResponse,
    )
    def pull_comfyui_workflows(request: Request):
        hub = getattr(server, "hub", None)
        if hub is None:
            raise HTTPException(
                status_code=503,
                detail="The workflow library is not open, so nothing can be pulled.",
            )
        comfyui_url = _comfyui_url(server.auth.get_user_for_request(request))
        origin_client_id = getattr(request.state, "origin_client_id", None)

        def announce(keys: list[str]) -> None:
            announce_changed_workflows(
                server, keys, "imported", origin_client_id=origin_client_id
            )

        with last_pull_lock:
            running = last_pull.get("task")
            if running is not None and running.status in _PULL_IN_FLIGHT:
                return {"status": "already_running", "task_id": running.id}
            task = ComfyUIWorkflowPullTask(
                hub,
                comfyui_url,
                store=functools.partial(store_pulled_workflow, hub),
                lock=workflow_inbox.INBOX_LOCK,
                announce=announce,
            )
            # Claimed before submission, so the gate covers the queued window.
            last_pull["task"] = task
        try:
            task_id = server.vault.submit_task(task)
        except RuntimeError as exc:
            logger.error(
                "Could not queue a pull of ComfyUI workflows from %s: %s",
                comfyui_url,
                exc,
            )
            task_id = None
        if task_id is None:
            with last_pull_lock:
                if last_pull.get("task") is task:
                    del last_pull["task"]
            raise HTTPException(
                status_code=503,
                detail="The task runner is not available, so the pull cannot be queued.",
            )
        logger.info(
            "Pull of ComfyUI workflows from %s queued as task %s.", comfyui_url, task_id
        )
        return {"status": "started", "task_id": task_id}

    @router.get(
        "/comfyui/workflows/pull",
        summary="The most recent pull of ComfyUI's saved workflows",
        description=(
            "`idle` when nothing has been pulled since the server started; "
            "otherwise the pull's state, and once it completed, what it found: "
            "how many workflows were new, already stored, shipped with "
            "PixlStash, deleted here and skipped, or failed, and how many name "
            "a node class the configured ComfyUI does not have. When ComfyUI's "
            "node list could not be read, `nodes_checked` is false and every "
            "workflow counts as unchecked, never as fine."
        ),
        response_model=ComfyUIWorkflowPullStateResponse,
    )
    def get_comfyui_workflow_pull():
        with last_pull_lock:
            task = last_pull.get("task")
        if task is None:
            return {"status": "idle"}
        state = {
            "status": task.status.value,
            "task_id": task.id,
            "comfyui_url": task.params.get("comfyui_url"),
        }
        if task.status == TaskStatus.COMPLETED and isinstance(task.result, dict):
            state["summary"] = task.result
        elif task.status == TaskStatus.FAILED:
            state["error"] = str(task.error) if task.error else "The pull failed."
        return state

    @router.get(
        "/comfyui/pictures/{picture_id}/workflow",
        summary="Get the displayable ComfyUI workflow for a picture",
        description=(
            "The graph a picture carries, for SHOWING: the UI `workflow` chunk "
            "when the file has one, which is the format the ComfyUI editor "
            "opens and the only one worth copying back into it. Falls back to "
            "the `prompt` chunk for a file that carries nothing else (#628).\n\n"
            "**This is not the recipe read.** What a picture was made with - "
            "prompts, models with their strengths, settings, seed, the shelf "
            "rows and the resolution lock - is "
            "`GET /comfyui/pictures/{picture_id}/recipe`, which reads the graph "
            "that actually executed and answers for A1111 pictures too. This "
            "route exists for the bytes."
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
            "ComfyUI's /object_info. A picture carrying only the editor "
            "`workflow` chunk is converted to an API prompt against that same "
            "/object_info and answers exactly like one that carried the API "
            "chunk, with `converted_from_editor_graph: true`; a graph that "
            "cannot be rebuilt exactly is NOT approximated, and answers "
            '`available: false` with `reason: "editor_graph"`, its prompt and '
            "models still filled in and `conversion_problems` naming what "
            "could not be read. That conversion needs /object_info, so it is "
            "the one case where `preflight=false` still reads ComfyUI (from a "
            "one-minute cache). "
            "A picture with no graph but with A1111 infotext answers from that "
            'instead, as `source: "a1111"` with `available: false`: its recipe '
            "is readable but not submittable to ComfyUI. "
            '`available: false` with `reason: "no_prompt_chunk"` is the normal '
            "answer for imported photos and stripped files, not an "
            "error. A `preflight` with `checked: false` means ComfyUI could not "
            "be reached, not that the recipe passed."
        ),
        response_model=ComfyUIPictureRecipeResponse,
    )
    def get_picture_comfyui_recipe(
        request: Request,
        picture_id: str,
        preflight: bool = Query(
            True,
            description=(
                "False skips the ComfyUI `/object_info` read, so the answer "
                "costs one file read and no network. The lightbox's Recipe tab "
                "uses it: it re-reads on every filmstrip step, and a round-trip "
                "per arrow-key is not affordable. `preflight.checked` is then "
                "false, which already means the question was not asked - never "
                "that the recipe passed."
            ),
        ),
    ):
        try:
            pic_id = int(picture_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        embedded_metadata = _read_embedded_metadata(server, pic_id)
        prompt_graph = find_comfy_api_prompt(embedded_metadata)
        user = server.auth.get_user_for_request(request)
        comfyui_url = _comfyui_url(user)
        object_info: dict | None = None
        object_info_error: str | None = None
        editor_graph = None
        conversion_problems: list[str] = []
        from_editor_graph = False
        if not prompt_graph:
            # No API chunk is not the end of the question. The editor graph is
            # the same workflow in the frontend's own serialisation, and
            # ComfyUI's /object_info says how to read it - so it is converted
            # here rather than declared unreadable.
            #
            # **This is the one case `preflight=false` still costs a round
            # trip**, because without the map there is nothing to report at
            # all, not merely nothing to judge. The map is reused for a minute
            # so walking the filmstrip does not re-fetch several megabytes per
            # arrow key.
            editor_graph = find_comfy_workflow(embedded_metadata)
            if is_ui_graph(editor_graph):
                object_info, object_info_error = _read_object_info(
                    comfyui_url, cached=True
                )
                prompt_graph, conversion_problems = convert_ui_graph_to_api(
                    editor_graph, object_info
                )
                from_editor_graph = prompt_graph is not None
        if not prompt_graph:
            if is_ui_graph(editor_graph):
                source_is_imported, source_label = _picture_source_origin(
                    server, pic_id
                )
                return {
                    **_editor_graph_recipe_payload(editor_graph, conversion_problems),
                    "source_is_imported": source_is_imported,
                    "source_label": source_label,
                    "workflow_key": _picture_workflow_key(server, pic_id),
                    **_recipe_extras(server, request, pic_id, None),
                    # After the extras on purpose: it replaces the stored
                    # column, which describes a different reading of this file.
                    "topology_hash": _editor_graph_topology(None, editor_graph),
                }
            # An A1111 picture carries its recipe as text, and the same fields
            # can be read off it.
            a1111 = reduce_a1111(embedded_metadata)
            if a1111 is None:
                return {"available": False, "reason": "no_prompt_chunk"}
            source_is_imported, source_label = _picture_source_origin(server, pic_id)
            return {
                **_a1111_recipe_payload(a1111),
                "source_is_imported": source_is_imported,
                "source_label": source_label,
                "workflow_key": _picture_workflow_key(server, pic_id),
                **_recipe_extras(server, request, pic_id, None),
            }

        graph = sanitize_prompt_graph(prompt_graph)
        gen_info = extract_generation_info(graph)
        extras = extract_recipe_extras(graph)
        stats = summarize_comfy_workflow(graph)
        if not from_editor_graph:
            object_info, object_info_error = (
                _read_object_info(comfyui_url)
                if preflight
                else (None, "the pre-flight was not asked for")
            )
        # **A map read to REBUILD a graph does not become a pre-flight.** The
        # editor branch above reads `/object_info` whether or not the flag asked
        # for it, and from a cache up to a minute old; judging the graph against
        # that would answer `checked: true` on a request that asked for no check
        # and hand the reader a verdict about a ComfyUI that may have changed.
        # `preflight=false` keeps meaning "the question was not asked".
        judged_against = object_info if preflight else None
        judged_error = (
            object_info_error if preflight else "the pre-flight was not asked for"
        )
        preflight, seed_targets = _inspect_graph(graph, judged_against, judged_error)
        source_is_imported, source_label = _picture_source_origin(server, pic_id)
        # A graph that calls back into PixlStash cannot be replayed as "a
        # variant of this picture" - see the run route's refusal for why. Reported
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
            "source": "comfyui",
            # Named for the chunk it came out of, because the two are not
            # equally trustworthy: the API chunk is what ComfyUI executed, the
            # editor one is what PixlStash rebuilt from the editor's view.
            "summary": (
                f"Editor Workflow · {stats['node_count']} nodes"
                if from_editor_graph
                else f"API Workflow · {stats['node_count']} nodes"
            ),
            "converted_from_editor_graph": from_editor_graph,
            "positive_prompt": gen_info["positive_prompt"],
            "negative_prompt": extras["negative_prompt"],
            "settings": extras["settings"],
            # The card this picture's variant is on, in a form the owner-only
            # card routes can be asked about. An opaque digest: no filename, no
            # prompt, no pixels, and the graph it digests is one this same token
            # can already read whole from the `/workflow` sibling.
            #
            # **It is NOT purely a function of this file.** `workflow_key` folds
            # in which LoRA slots this topology marks structural, and that mark
            # was frozen from the filename of whichever picture of that topology
            # was filed FIRST in this library (`hub/schema.py`,
            # `workflow_slot_mark`, `INSERT OR IGNORE`).
            #
            # **And the mark set is recoverable, not merely hinted at.** A
            # holder has the graph, so they can compute the key for every
            # assignment of marks to its LoRA slots and match the one they were
            # given; the slot set is tiny, so 2^n over it recovers the whole
            # set exactly. What that discloses is one bit PER LORA SLOT of this
            # topology - whether the first-filed picture's file in that slot
            # looked like a speed LoRA under a published regex - and that
            # picture may be one the token cannot otherwise see. A
            # filename-derived classification, never a filename, a prompt or a
            # picture; low severity, and the honest bound rather than the
            # flattering one. Returning this owner-only would close it.
            "workflow_key": _picture_workflow_key(server, pic_id),
            **_recipe_extras(server, request, pic_id, graph),
            # A rebuilt graph is keyed from the rebuild, not from the column
            # the extraction pass wrote about a chunk this picture does not
            # have. Placed after the extras so it replaces theirs.
            **(
                {"topology_hash": _editor_graph_topology(graph, editor_graph)}
                if from_editor_graph
                else {}
            ),
            "seed": gen_info["seed"],
            # The seed as text as well as a number: ComfyUI draws seeds up to
            # 2**64-1 and a JavaScript Number loses digits above 2**53, so a
            # client rendering `seed` prints the wrong one about half the time.
            "seed_text": (None if gen_info["seed"] is None else str(gen_info["seed"])),
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
            "lora_insertion": _describe_lora_insertion(
                graph,
                object_info,
                object_info_error,
                # A replay never inserts it, so it is never a warning here.
                digest_loader=False,
            ),
            "preflight": preflight,
        }

    return router
