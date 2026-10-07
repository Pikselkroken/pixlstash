"""Task that pulls every workflow a ComfyUI has saved into the library (#1440).

Offered once a minute by :class:`ComfyUIWorkflowPollFinder` while a ComfyUI
address is saved and the owner's "Pull workflows from ComfyUI" setting is on,
run once at the end of a Link, and still submitted by
``POST /comfyui/workflows/pull``. All three go through
:class:`~pixlstash.services.comfyui_workflow_pulls.WorkflowPulls`, which keeps
one pull in flight. Its counters feed the worker-progress snapshot, so the
pull draws as a row on the Tasks tab.

**A quiet poll is one request.** The listing carries each file's ``modified``,
and a path whose origin row holds that same time is not read: it is counted
``unchanged``. Only when a file is read does the pull ask whether ComfyUI is
multi-user, fetch ``object_info`` and (for a background poll) the run history.
A path whose content changed becomes a new version of the workflow its origin
row names (``store_pulled_workflow``), never a new workflow.

**One-way.** It lists and reads over ComfyUI's userdata API
(:mod:`pixlstash.services.comfyui_userdata`) and writes nothing back to ComfyUI.
Each document is filed through the same store an import uses, which matches a
copy of a workflow already stored by content, so a pull is idempotent and safe
to repeat. :mod:`pixlstash.hub.workflow_origin` remembers which path each came
from, which is what keeps a workflow the owner deleted here from coming back.

**So does ComfyUI's run history** (#1518): ``GET /history`` names which models
ran together, including runs whose pictures never reached PixlStash, and
:func:`~pixlstash.services.model_shelf_service.record_comfyui_history` files it
for companion proposals to read while ComfyUI is off.

**Triage rides along for free.** The sweep already holds every document, so one
``object_info`` fetch lets it count the workflows naming a node class this
ComfyUI does not have, and those naming a model file it does not list, with
how many model values it could not read at all (:func:`model_triage`). The
counts are computed with the pull and never stored: they go stale the moment a
node pack is installed, which is exactly what they prompt the owner to do. With
ComfyUI's class list unavailable the answer is *not checked*, never a clean
bill of health.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from typing import Any, Callable, ContextManager, Iterable, Optional

from pixlstash.hub import workflow_origin
from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services import comfyui_userdata, workflow_inbox
from pixlstash.services.comfyui_recipe_service import (
    advertised_model_names,
    collect_node_classes,
    fetch_object_info,
    preflight_prompt,
)
from pixlstash.services.model_shelf_service import record_comfyui_history
from pixlstash.services.workflow_hash import (
    MODEL_EXTENSIONS,
    WorkflowGraphError,
    reduce_ui_graph,
)
from pixlstash.services.workflow_card_service import converted_manual_document
from pixlstash.services.workflow_io import api_graph
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.tasks.task_type import TaskType
from pixlstash.utils.comfyui_utilities import (
    NotAWorkflowError,
    count_model_file_values_ui,
    loaded_model_widgets,
)

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

# ``routes/comfyui.store_pulled_workflow`` bound to the hub: ``store(name,
# document, (origin, remote_path, remote_modified, content_hash))``.
StoreFn = Callable[[str, dict, tuple], dict]

# What one pull may write before it stops and leaves the rest to the next one.
# ComfyUI has no authentication, so its saved workflows are written by whoever
# reaches it, and the poll takes them every minute without anybody pressing
# anything: without a budget a listing of 5000 changing files grows the hub by
# that much a minute. A pull that hits one is logged and the poll backs off.
MAX_NEW_WORKFLOWS_PER_PULL = 100
MAX_NEW_VERSIONS_PER_PULL = 200
MAX_WRITTEN_BYTES_PER_PULL = 64 * 1024 * 1024

# What `_pull_one` answers for a workflow the owner deleted here. A sentinel
# rather than a string, so it can never be mistaken for a result.
_DISMISSED = object()


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


def model_triage(
    document: dict, object_info: dict, advertised: set[str]
) -> Optional[tuple[list[str], int]]:
    """``(model files this ComfyUI does not have, model values not read)``.

    **Advisory, and never identity.** An API graph gets the full pre-flight;
    an editor graph names its widget values by position, so its model names
    are recovered through :func:`loaded_model_widgets`, which reads some and
    not others. What it could not name is counted rather than dropped, so a
    short list never passes for a complete one. Nothing here may feed a hash.

    A recovered name counts as present when ComfyUI advertises it by path or
    by basename: the lenient reading, because a false "missing" is worse than
    a missed one.

    Returns:
        ``None`` when the document could not be read, which is *unchecked*.
    """
    try:
        graph = api_graph(document)
        if graph is not None:
            checked = preflight_prompt(graph, object_info)
            absent = {miss["value"] for miss in checked["missing_models"]}
            # The pre-flight skips every input of a node whose class ComfyUI
            # lacks. Its models were never looked at, so they are counted as
            # unread here, as the editor-graph branch below would count them.
            skipped = sum(
                1
                for node in graph.values()
                if isinstance(node, dict)
                and node.get("class_type") not in object_info
                and isinstance(node.get("inputs"), dict)
                for value in node["inputs"].values()
                if isinstance(value, str) and value.lower().endswith(MODEL_EXTENSIONS)
            )
            unread = checked["unchecked_models"] + checked["unchecked_fields"]
            return sorted(absent), unread + skipped
        named = [
            value
            for _widget, value in loaded_model_widgets(document)
            if value.lower().endswith(MODEL_EXTENSIONS)
        ]
        unread = max(0, count_model_file_values_ui(document) - len(named))
        absent = set()
        for value in named:
            normalized = value.replace("\\", "/")
            if (
                normalized not in advertised
                and normalized.rsplit("/", 1)[-1] not in advertised
            ):
                absent.add(value)
        return sorted(absent), unread
    except Exception as exc:
        # The readers index into whatever the file holds; a malformed one is
        # unchecked, never fine.
        logger.warning(
            "Reading a pulled workflow's models failed with %s: %s",
            type(exc).__name__,
            exc,
        )
        return None


class ComfyUIWorkflowPullTask(BaseTask):
    """List ComfyUI's saved workflows and file each one in the library."""

    def __init__(
        self,
        hub: HubDatabase,
        comfyui_url: str,
        store: StoreFn,
        lock: Optional[ContextManager] = None,
        announce: Optional[Callable[[list[str]], None]] = None,
        background: bool = False,
    ):
        """Bind the task to one ComfyUI.

        Args:
            hub: The hub, where the origin rows live.
            comfyui_url: The ComfyUI base URL, without a trailing slash. Also
                the ``origin`` its rows are recorded under.
            store: Files one document under a name, writing its origin row
                (the third argument) with it, and answers with the import
                route's result (``name``, ``matched``, ``workflow_id``,
                ``topology_hash``, and ``builtin`` when the match is a workflow
                PixlStash ships). Called with *lock* held.
            lock: Held around each entry's dismissal check, store and origin
                row - ``workflow_inbox.INBOX_LOCK``, which the delete holds
                around its dismissal. Without it a workflow deleted mid-pull
                is written straight back by the same pull.
            announce: Called once at the end with the workflow ids that changed,
                so the Workflows screen looks again. A background poll that
                changed nothing does not call it, or every open tab would
                reload its Workflows view once a minute.
            background: The minute poll rather than somebody asking: low
                priority, and the run history is read only when a file was.
        """
        super().__init__(
            task_type=TaskType.COMFYUI_WORKFLOW_PULL.value,
            params={"comfyui_url": comfyui_url},
        )
        self._hub = hub
        self._comfyui_url = comfyui_url
        self._store = store
        self._lock = lock if lock is not None else contextlib.nullcontext()
        self._announce = announce
        self._background = background
        # Live progress, read by Vault._build_worker_progress_snapshot.
        self._total_count = 0
        self._processed_count = 0

    @property
    def priority(self) -> TaskPriority:
        # Asked for (a press, a Link) is HIGH: the owner is watching. The poll
        # is not.
        return TaskPriority.LOW if self._background else TaskPriority.HIGH

    def _run_task(self) -> dict[str, Any]:
        origin = self._comfyui_url
        entries = comfyui_userdata.list_saved_workflows(origin)
        self._total_count = len(entries)
        known = workflow_origin.origin_rows(self._hub, origin)
        result: dict[str, Any] = {
            "listed": len(entries),
            "pulled": 0,
            "changed": 0,
            "unchanged": 0,
            "matched": 0,
            "already_shipped": 0,
            "skipped_dismissed": 0,
            "failed": 0,
            "gone": 0,
            "missing_nodes": 0,
            "nodes_unchecked": 0,
            "nodes_checked": False,
            "missing_node_classes": [],
            "known_from_pictures": 0,
            "missing_models": 0,
            "models_unread": 0,
            "models_unchecked": 0,
            "missing_model_files": [],
            "workflow_ids": [],
            "budget_exhausted": None,
            "card_cap_reached": False,
        }
        to_read = []
        for entry in entries:
            row = known.get(entry.path)
            if row is not None and row["dismissed"]:
                # The path was deleted here; its content may be read under
                # another path, where the content check below catches it.
                result["skipped_dismissed"] += 1
                self._processed_count += 1
            elif row is not None and is_unchanged(row, entry):
                result["unchanged"] += 1
                self._processed_count += 1
            else:
                to_read.append(entry)
        listed = {entry.path for entry in entries}
        relinked = bool(listed) and any(
            not row["dismissed"] and (row["gone_at"] is None) != (path in listed)
            for path, row in known.items()
        )
        if to_read or relinked:
            # Before anything is stored or a link moves: the listing of a
            # multi-user ComfyUI is nobody's in particular.
            comfyui_userdata.ensure_single_user(origin)

        object_info: Optional[dict] = None
        if to_read:
            try:
                object_info = fetch_object_info(origin)
            except RuntimeError as exc:
                logger.info(
                    "Pulling ComfyUI workflows from %s without a node check: %s",
                    origin,
                    exc,
                )
        result["nodes_checked"] = object_info is not None
        advertised = (
            advertised_model_names(object_info) if object_info is not None else None
        )
        missing_classes: set[str] = set()
        missing_files: set[str] = set()
        workflow_ids: list[str] = []
        new_workflows = new_versions = written = 0
        for position, entry in enumerate(to_read):
            exhausted = _over_budget(new_workflows, new_versions, written, entry)
            if exhausted is not None:
                result["budget_exhausted"] = exhausted
                logger.warning(
                    "Stopped pulling ComfyUI workflows from %s at the budget of "
                    "%s for one pull; %d listed file(s) are left for the next "
                    "pull.",
                    origin,
                    exhausted,
                    len(to_read) - position,
                )
                break
            try:
                pulled = self._pull_one(entry)
                if pulled is None:
                    result["failed"] += 1
                    continue
                if pulled is _DISMISSED:
                    result["skipped_dismissed"] += 1
                    continue
                document, outcome = pulled
                if outcome.get("capped"):
                    if not result["card_cap_reached"]:
                        logger.warning(
                            "ComfyUI at %s already has %d pulled workflows, the "
                            "most one address may; its new files make no "
                            "workflow until some are deleted.",
                            origin,
                            workflow_origin.MAX_PULL_CARDS_PER_ORIGIN,
                        )
                    result["card_cap_reached"] = True
                    continue
                # One bucket per listed entry: shipped, matched, changed, new.
                if outcome.get("builtin"):
                    result["already_shipped"] += 1
                elif outcome.get("matched"):
                    result["matched"] += 1
                elif outcome.get("versioned"):
                    result["changed"] += 1
                    new_versions += 1
                    written += len(json.dumps(document))
                else:
                    result["pulled"] += 1
                    new_workflows += 1
                    written += len(json.dumps(document))
                if outcome.get("workflow_id"):
                    workflow_ids.append(outcome["workflow_id"])
                    if object_info is not None:
                        # The map is in hand: convert an editor document now
                        # and store it, as its first read otherwise would.
                        try:
                            converted_manual_document(
                                self._hub, outcome["workflow_id"], object_info
                            )
                        except (sqlite3.Error, RecursionError) as exc:
                            logger.warning(
                                "Pulled %s as %s but could not store its "
                                "conversion; its first read converts it: %s",
                                entry.path,
                                outcome["workflow_id"],
                                exc,
                            )
                if self._topology_has_pictures(outcome.get("topology_hash")):
                    result["known_from_pictures"] += 1
                if object_info is None:
                    result["nodes_unchecked"] += 1
                    result["models_unchecked"] += 1
                    continue
                models = model_triage(document, object_info, advertised)
                if models is None:
                    result["models_unchecked"] += 1
                else:
                    absent, unread = models
                    result["models_unread"] += unread
                    if absent:
                        result["missing_models"] += 1
                        missing_files.update(absent)
                missing = missing_node_classes(document, object_info)
                if missing is None:
                    result["nodes_unchecked"] += 1
                elif missing:
                    result["missing_nodes"] += 1
                    missing_classes.update(missing)
            finally:
                self._processed_count += 1

        try:
            result["gone"] = workflow_origin.mark_gone(self._hub, origin, listed)
        except sqlite3.Error as exc:
            logger.warning(
                "Pulled ComfyUI workflows from %s but could not mark the paths "
                "it no longer lists as gone: %s",
                origin,
                exc,
            )
        # The history is a large read: a poll pays it only when it read a
        # workflow, which is when the owner has been working in ComfyUI.
        result["history_runs"] = (
            self._read_history() if to_read or not self._background else None
        )
        result["missing_node_classes"] = sorted(missing_classes, key=str.lower)
        result["missing_model_files"] = sorted(missing_files, key=str.lower)
        result["workflow_ids"] = sorted(set(workflow_ids))
        log = logger.info if to_read or result["gone"] else logger.debug
        log(
            "Pulled ComfyUI workflows from %s: %d listed, %d unchanged, %d new, "
            "%d new versions, %d already stored, %d shipped with PixlStash, %d "
            "deleted here and skipped, %d failed, %d gone from ComfyUI.",
            origin,
            result["listed"],
            result["unchanged"],
            result["pulled"],
            result["changed"],
            result["matched"],
            result["already_shipped"],
            result["skipped_dismissed"],
            result["failed"],
            result["gone"],
        )
        if self._announce is not None and (
            result["workflow_ids"] or not self._background
        ):
            try:
                self._announce(result["workflow_ids"])
            except Exception as exc:
                logger.warning(
                    "Pulled ComfyUI workflows but could not announce the change: %s",
                    exc,
                )
        return result

    def _read_history(self) -> Optional[int]:
        """File ComfyUI's recent runs as companion evidence (#1518).

        Rides the pull because the pull is when ComfyUI is known to answer, and
        the rows it leaves are what proposals read afterwards, ComfyUI running
        or not. Never fails the pull: the saved workflows are already filed.

        Returns:
            How many finished runs named a shelf model, or ``None`` when the
            history could not be read or filed.
        """
        try:
            history = comfyui_userdata.read_history(self._comfyui_url)
            return record_comfyui_history(self._hub, history)
        except Exception as exc:
            # Broad on purpose: the workflows are already filed, and a history
            # this cannot read must not turn that into a failed pull.
            logger.warning(
                "Pulled ComfyUI workflows from %s but could not file its run "
                "history as companion evidence: %s: %s",
                self._comfyui_url,
                type(exc).__name__,
                exc,
            )
            return None

    def _topology_has_pictures(self, topology_hash: Optional[str]) -> bool:
        """Whether a picture in some library was made with this shape.

        The overlap the pull exists to measure. **Picture evidence, not a
        recipe row**: ``workflow_recipe`` is also written for every API-format
        file an import or a pull stores, so asking it would count a pulled
        workflow as known because of itself. ``workflow_recipe_instance`` is
        written only by the picture extraction, per library.
        """
        if not topology_hash:
            return False
        try:
            return (
                self._hub.fetchone(
                    "SELECT 1 FROM workflow_recipe r "
                    "JOIN workflow_recipe_instance i "
                    "ON i.structural_hash = r.structural_hash "
                    # Or one a model fix swapped a PixlStash loader into,
                    # whose pictures card as this shape's (#1605).
                    "WHERE r.topology_hash = ? OR r.topology_hash IN "
                    "(SELECT swapped_topology_hash FROM workflow_loader_swap "
                    "WHERE topology_hash = ?) LIMIT 1",
                    (topology_hash, topology_hash),
                )
                is not None
            )
        except sqlite3.Error as exc:
            logger.warning(
                "Could not ask whether topology %s has pictures: %s", topology_hash, exc
            )
            return False

    def _pull_one(self, entry):
        """Read and file one saved workflow (:func:`pull_entry`)."""
        return pull_entry(self._hub, self._comfyui_url, entry, self._store, self._lock)


def _over_budget(
    new_workflows: int, new_versions: int, written: int, entry
) -> Optional[str]:
    """The per-pull budget reading *entry* would pass, or ``None``.

    The byte budget counts the documents stored so far plus the listed size of
    the next, so a pull never starts a file it would end past it on.
    """
    if new_workflows >= MAX_NEW_WORKFLOWS_PER_PULL:
        return f"{MAX_NEW_WORKFLOWS_PER_PULL} new workflows"
    if new_versions >= MAX_NEW_VERSIONS_PER_PULL:
        return f"{MAX_NEW_VERSIONS_PER_PULL} new versions"
    if written + (entry.size or 0) > MAX_WRITTEN_BYTES_PER_PULL:
        return f"{MAX_WRITTEN_BYTES_PER_PULL // (1024 * 1024)} MB written"
    return None


def is_unchanged(row: dict, entry) -> bool:
    """Whether the listing *entry* is the file its origin *row* last read.

    By ComfyUI's ``modified`` alone, so an unchanged file is never fetched. A
    listing without times, or a row whose workflow is gone, reads as changed.
    """
    return (
        entry.modified_ms is not None
        and row["remote_modified"] == entry.modified_ms
        and (bool(row["live"]) or row["workflow_name"] is None)
    )


def pull_entry(
    hub: HubDatabase,
    origin: str,
    entry,
    store: StoreFn,
    lock: Optional[ContextManager] = None,
    *,
    timeout_s: Optional[float] = None,
):
    """Read and file one saved workflow.

    Shared by the pull and by the check Run and Open make on one file.

    Args:
        timeout_s: Bounds the read of the file (both the socket and the whole
            body); ``None`` keeps the pull's own limits.

    Returns:
        ``None`` when it could not be read or stored (logged with the path),
        :data:`_DISMISSED` when the owner deleted it here, else ``(document,
        store result)``.
    """
    lock = lock if lock is not None else contextlib.nullcontext()
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
            origin, entry.path, timeout_s=timeout_s
        )
    except RuntimeError as exc:
        # A quick check (a timeout given) falls back to what is stored.
        (logger.warning if timeout_s is None else logger.info)(
            "Could not read ComfyUI workflow %s: %s", entry.path, exc
        )
        return None
    try:
        digest = workflow_inbox.content_hash(document)
    except (RecursionError, TypeError, ValueError) as exc:
        logger.warning(
            "Could not hash ComfyUI workflow %s, so only its path is checked "
            "against what was deleted here: %s",
            entry.path,
            exc,
        )
        digest = None
    name = stored_name_for(entry.path)
    # One lock around the check, the write and the record: the delete takes
    # it around its dismissal, so a workflow deleted while this pull runs
    # is either dismissed before its entry is checked or deleted after its
    # origin row exists - never re-stored by the pull that was running.
    with lock:
        try:
            if workflow_origin.is_dismissed(hub, origin, entry.path, digest):
                return _DISMISSED
        except sqlite3.Error as exc:
            logger.error(
                "Could not ask whether ComfyUI workflow %s was deleted here, "
                "so it is not pulled: %s",
                entry.path,
                exc,
            )
            return None
        try:
            # The origin row goes in with the workflow, in one transaction
            # (#1694): one stored without it would come back after a delete.
            outcome = store(
                name, document, (origin, entry.path, entry.modified_ms, digest)
            )
        except (
            NotAWorkflowError,
            RecursionError,
            TypeError,
            ValueError,
            OSError,
        ) as exc:
            logger.warning(
                "Could not store ComfyUI workflow %s as %s: %s: %s",
                entry.path,
                name,
                type(exc).__name__,
                exc,
            )
            return None
        except sqlite3.Error as exc:
            logger.error(
                "Could not store ComfyUI workflow %s as %s with its origin "
                "row; no new workflow or version was stored: %s",
                entry.path,
                name,
                exc,
            )
            return None
    return document, outcome
