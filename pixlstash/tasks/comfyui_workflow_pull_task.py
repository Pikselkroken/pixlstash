"""Task that pulls every workflow a ComfyUI has saved into the library (#1440).

User-triggered, so there is no finder: ``POST /comfyui/workflows/pull`` submits
it straight to the ``TaskRunner``, the ``ModelFolderScanTask`` shape. Its
counters feed the worker-progress snapshot, so the pull draws as a row on the
Tasks tab.

**One-way.** It lists and reads over ComfyUI's userdata API
(:mod:`pixlstash.services.comfyui_userdata`) and writes nothing back to ComfyUI.
Each document is filed through the same store an import uses, which matches a
copy of a workflow already stored by content, so a pull is idempotent and safe
to repeat. :mod:`pixlstash.hub.workflow_origin` remembers which path each came
from, which is what keeps a workflow the owner deleted here from coming back.

**Triage rides along for free.** The sweep already holds every document, so one
``object_info`` fetch lets it count the workflows naming a node class this
ComfyUI does not have. The count is computed with the pull and never stored: it
goes stale the moment a node pack is installed, which is exactly what it
prompts the owner to do. With ComfyUI's class list unavailable the answer is
*not checked*, never a clean bill of health.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Callable, Iterable, Optional

from pixlstash.hub import workflow_origin
from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services import comfyui_userdata
from pixlstash.services.comfyui_recipe_service import (
    collect_node_classes,
    fetch_object_info,
)
from pixlstash.services.workflow_hash import WorkflowGraphError, reduce_ui_graph
from pixlstash.services.workflow_io import api_graph
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.tasks.task_type import TaskType
from pixlstash.utils.comfyui_utilities import NotAWorkflowError

logger = get_logger(__name__)

# Characters no Windows filename may hold, plus the separators. A ComfyUI path
# is filed as ONE flat name: some saved names are URL fragments
# (``https:/host/path.json``), which must not become a directory tree here.
_UNSAFE_NAME_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')

# Names Windows refuses as a file stem whatever the extension.
_RESERVED_WINDOWS_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{port}{n}" for port in ("COM", "LPT") for n in "123456789\u00b9\u00b2\u00b3"}
)

# Room under the usual 255-character filename limit for ``.json`` and the
# `` (N)`` suffix a name collision adds.
_MAX_STEM_CHARS = 200

# What ``store`` answers with, per the import route's ``_store_workflow``.
StoreFn = Callable[[str, dict], dict]


def stored_name_for(remote_path: str) -> str:
    """The flat file name a ComfyUI path is stored under.

    Subfolders are joined with `` - `` rather than kept, characters a Windows
    filename cannot hold become ``_``, trailing dots and spaces (which
    Windows strips silently) go, a reserved device name is prefixed and an
    over-long name is cut. Always ends in ``.json``.

    Args:
        remote_path: A listing ``path``, relative to ComfyUI's workflows
            folder and ``/``-separated.
    """
    stem = remote_path[:-5] if remote_path.lower().endswith(".json") else remote_path
    parts = [
        _UNSAFE_NAME_CHARS.sub("_", part).strip().rstrip(".")
        for part in re.split(r"[\\/]+", stem)
    ]
    flat = " - ".join(part for part in parts if part and part not in (".", ".."))
    flat = flat[:_MAX_STEM_CHARS].rstrip(" .") or "workflow"
    # Windows reads the part before the FIRST dot, so `CON.foo.json` is the
    # console as surely as `CON.json` - and the `(2)` a collision adds lands
    # after that part, so an exists() that answers for a device would never
    # find a free name.
    if flat.split(".", 1)[0].rstrip(" ").upper() in _RESERVED_WINDOWS_NAMES:
        flat = f"_{flat}"
    return f"{flat}.json"


def missing_node_classes(document: dict, object_info: dict) -> Optional[list[str]]:
    """The node classes *document* uses that *object_info* does not declare.

    Reads an API graph by its ``class_type`` and an editor graph through the
    topology reducer, which inlines subgraphs and drops the editor-only nodes
    (notes, reroutes, primitives) ComfyUI never declares.

    Returns:
        The missing class names, sorted; ``None`` when the graph could not be
        read, which is *unchecked* and must never be counted as fine.
    """
    graph = api_graph(document)
    try:
        if graph is not None:
            classes: Iterable[str] = collect_node_classes(graph)
        else:
            classes = {node.class_type for node in reduce_ui_graph(document).values()}
    except (WorkflowGraphError, RecursionError) as exc:
        logger.info("Could not read a pulled workflow's node classes: %s", exc)
        return None
    except Exception as exc:
        # The reducer indexes into whatever the file holds, and a malformed one
        # raises something other than WorkflowGraphError (the import's own
        # filing catches the same). Unchecked is the honest answer either way.
        logger.warning(
            "Reading a pulled workflow's node classes failed with %s: %s",
            type(exc).__name__,
            exc,
        )
        return None
    return sorted({name for name in classes if name and name not in object_info})


class ComfyUIWorkflowPullTask(BaseTask):
    """List ComfyUI's saved workflows and file each one in the library."""

    def __init__(
        self,
        hub: HubDatabase,
        comfyui_url: str,
        store: StoreFn,
        announce: Optional[Callable[[list[str]], None]] = None,
    ):
        """Bind the task to one ComfyUI.

        Args:
            hub: The hub, where the origin rows live.
            comfyui_url: The ComfyUI base URL, without a trailing slash. Also
                the ``origin`` its rows are recorded under.
            store: Files one document under a name and answers with the import
                route's result (``name``, ``matched``, ``workflow_key``, and
                ``builtin`` when the match is a workflow PixlStash ships). The
                caller holds the knowledge of where workflows are stored and
                of the lock that guards them.
            announce: Called once at the end with the card keys that changed,
                so the Workflows screen looks again.
        """
        super().__init__(
            task_type=TaskType.COMFYUI_WORKFLOW_PULL.value,
            params={"comfyui_url": comfyui_url},
        )
        self._hub = hub
        self._comfyui_url = comfyui_url
        self._store = store
        self._announce = announce
        # Live progress, read by Vault._build_worker_progress_snapshot.
        self._total_count = 0
        self._processed_count = 0

    @property
    def priority(self) -> TaskPriority:
        # User-initiated: the owner pressed the button and is watching.
        return TaskPriority.HIGH

    def _run_task(self) -> dict[str, Any]:
        origin = self._comfyui_url
        comfyui_userdata.ensure_single_user(origin)
        entries = comfyui_userdata.list_saved_workflows(origin)
        self._total_count = len(entries)
        dismissed = workflow_origin.dismissed_paths(self._hub, origin)

        object_info: Optional[dict]
        try:
            object_info = fetch_object_info(origin)
        except RuntimeError as exc:
            logger.info(
                "Pulling ComfyUI workflows from %s without a node check: %s",
                origin,
                exc,
            )
            object_info = None

        result: dict[str, Any] = {
            "listed": len(entries),
            "pulled": 0,
            "matched": 0,
            "already_shipped": 0,
            "skipped_dismissed": 0,
            "failed": 0,
            "gone": 0,
            "missing_nodes": 0,
            "nodes_unchecked": 0,
            "nodes_checked": object_info is not None,
            "missing_node_classes": [],
            "workflow_keys": [],
        }
        missing_classes: set[str] = set()
        keys: list[str] = []
        for entry in entries:
            try:
                if entry.path in dismissed:
                    result["skipped_dismissed"] += 1
                    continue
                stored = self._pull_one(entry)
                if stored is None:
                    result["failed"] += 1
                    continue
                document, outcome = stored
                if outcome.get("builtin"):
                    result["already_shipped"] += 1
                elif outcome.get("matched"):
                    result["matched"] += 1
                else:
                    result["pulled"] += 1
                if outcome.get("workflow_key"):
                    keys.append(outcome["workflow_key"])
                if object_info is None:
                    result["nodes_unchecked"] += 1
                    continue
                missing = missing_node_classes(document, object_info)
                if missing is None:
                    result["nodes_unchecked"] += 1
                elif missing:
                    result["missing_nodes"] += 1
                    missing_classes.update(missing)
            finally:
                self._processed_count += 1

        result["gone"] = workflow_origin.prune_gone(
            self._hub, origin, [entry.path for entry in entries]
        )
        result["missing_node_classes"] = sorted(missing_classes, key=str.lower)
        result["workflow_keys"] = sorted(set(keys))
        logger.info(
            "Pulled ComfyUI workflows from %s: %d listed, %d new, %d already "
            "stored, %d shipped with PixlStash, %d deleted here and skipped, "
            "%d failed, %d gone from ComfyUI.",
            origin,
            result["listed"],
            result["pulled"],
            result["matched"],
            result["already_shipped"],
            result["skipped_dismissed"],
            result["failed"],
            result["gone"],
        )
        if self._announce is not None:
            try:
                self._announce(result["workflow_keys"])
            except Exception as exc:
                logger.warning(
                    "Pulled ComfyUI workflows but could not announce the change: %s",
                    exc,
                )
        return result

    def _pull_one(self, entry) -> Optional[tuple[dict, dict]]:
        """Read and file one saved workflow: ``(document, store result)``.

        ``None`` when it could not be read or stored, logged with the path.
        """
        if (
            entry.size is not None
            and entry.size > comfyui_userdata.MAX_SAVED_WORKFLOW_BYTES
        ):
            logger.warning(
                "Skipped ComfyUI workflow %s: %d bytes, past the %d a pull reads.",
                entry.path,
                entry.size,
                comfyui_userdata.MAX_SAVED_WORKFLOW_BYTES,
            )
            return None
        try:
            document = comfyui_userdata.read_saved_workflow(
                self._comfyui_url, entry.path
            )
        except RuntimeError as exc:
            logger.warning("Could not read ComfyUI workflow %s: %s", entry.path, exc)
            return None
        name = stored_name_for(entry.path)
        try:
            outcome = self._store(name, document)
        except (NotAWorkflowError, RecursionError, ValueError, OSError) as exc:
            logger.warning(
                "Could not store ComfyUI workflow %s as %s: %s: %s",
                entry.path,
                name,
                type(exc).__name__,
                exc,
            )
            return None
        try:
            workflow_origin.record_pulled(
                self._hub,
                self._comfyui_url,
                entry.path,
                outcome["name"],
                entry.modified_ms,
            )
        except sqlite3.Error as exc:
            # The file is stored and on its card; what is lost is the memory
            # of where it came from, so deleting it here will not stop a later
            # pull from bringing it back.
            logger.error(
                "Stored ComfyUI workflow %s as %s but could not record its "
                "origin; deleting it will not keep it from being pulled again: %s",
                entry.path,
                outcome["name"],
                exc,
            )
        return document, outcome
