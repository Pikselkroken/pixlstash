"""Task that scans one registered model folder, and the record of those scans.

Nobody asks for a scan. ``ModelFolderScanFinder`` hands one out whenever a
folder's files change and on a slow timer besides, and ``POST /model-folders``
queues the first one straight away (:func:`claim_scan`). It used to be a bare
``threading.Thread``, which nothing observed: no progress on a 57 GB folder, no
way to tell a crash from a slow read, and a daemon thread alive at interpreter
shutdown (#856).

Two things the task system supplies for free:

* ``_total_count`` / ``_processed_count`` are read by
  :meth:`pixlstash.vault.Vault._build_worker_progress_snapshot`, so the scan
  renders as a task row in the existing task manager;
* ``status`` is a real terminal state. A crashed scan is ``failed`` with the
  message on ``error``, instead of being indistinguishable from a slow one
  because ``last_checked`` had not moved yet.
"""

from __future__ import annotations

import threading
from dataclasses import asdict
from typing import Any, Callable, Optional

from pixlstash.event_types import EventType
from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services.builtin_caches import redeclare_model_root
from pixlstash.services.builtin_models import BUILTIN_OWNER
from pixlstash.services.model_folder_scanner import ModelFolderScanner
from pixlstash.services.model_mover import SHELF_IO_LOCK
from pixlstash.tasks.base_task import BaseTask, TaskPriority, TaskStatus

logger = get_logger(__name__)

# The most recent scan claimed for each folder, whatever state it ended in.
# One entry per registered folder, dropped when the folder is forgotten, so it
# cannot grow.
#
# It serves two purposes. It is the "already running" gate: the scanner is
# correct under concurrent runs (its missing sweep is ``seen_at <`` the run's own
# stamp, not ``!=``), so this is not a correctness lock - it stops the finder
# and a registration from reading 438 GB twice. And it is where a *finished*
# scan's outcome lives, because ``TaskRunner`` forgets a task the moment it
# completes; a caller that had only ``last_checked`` to look at could not tell a
# crash from a slow read.
#
# In memory on purpose: after a restart nothing is scanning and nothing is
# pending, so there is no state worth persisting.
_scans: dict[int, "ModelFolderScanTask"] = {}
_scans_lock = threading.Lock()

# The two states that mean "this folder is spoken for". PENDING covers the
# window between being claimed and a worker picking the task up.
_SCAN_IN_FLIGHT = (TaskStatus.PENDING, TaskStatus.RUNNING)

# How long re-reading a root PixlStash declares waits for the shelf's I/O slot.
# A sibling declaration holds it for milliseconds; a move holds it for minutes,
# and then this pass is skipped and the next one tries again.
_REDECLARE_WAIT_S = 5.0


def claim_scan(
    hub: HubDatabase,
    folder_id: int,
    path: str,
    kind: str,
    *,
    owner: Optional[str] = None,
    priority: TaskPriority = TaskPriority.MEDIUM,
    notifier: Optional[Callable[[EventType, Any], None]] = None,
) -> Optional["ModelFolderScanTask"]:
    """Return a new scan of *folder_id*, or ``None`` while one is in flight.

    The task is recorded before it is returned, so the gate covers the queued
    window too. A caller that then fails to submit it calls
    :func:`release_scan`.
    """
    with _scans_lock:
        running = _scans.get(folder_id)
        if running is not None and running.status in _SCAN_IN_FLIGHT:
            return None
        task = ModelFolderScanTask(
            hub,
            folder_id,
            path,
            kind,
            owner=owner,
            priority=priority,
            notifier=notifier,
        )
        _scans[folder_id] = task
    return task


def release_scan(task: "ModelFolderScanTask") -> None:
    """Drop a claimed scan that was never submitted."""
    with _scans_lock:
        if _scans.get(task.folder_id) is task:
            del _scans[task.folder_id]


def forget_scan(folder_id: int) -> None:
    """Drop a forgotten folder's record. SQLite reuses rowids, so a folder
    registered later could otherwise inherit this one's outcome."""
    with _scans_lock:
        _scans.pop(folder_id, None)


def scan_state(folder_id: int) -> tuple[Optional[str], Optional[str]]:
    """Return ``(scan_status, scan_error)`` for a folder's most recent scan."""
    with _scans_lock:
        task = _scans.get(folder_id)
    if task is None:
        return None, None
    return task.status.value, task.error


class ModelFolderScanTask(BaseTask):
    """Walk one registered model folder and reconcile its rows with disk."""

    def __init__(
        self,
        hub: HubDatabase,
        folder_id: int,
        path: str,
        kind: str,
        *,
        owner: Optional[str] = None,
        priority: TaskPriority = TaskPriority.MEDIUM,
        notifier: Optional[Callable[[EventType, Any], None]] = None,
    ):
        """Bind the task to one folder.

        Args:
            hub: The hub database holding the shelf tables.
            folder_id: ``model_folder.id``.
            path: The folder as registered. Owner-chosen and therefore trusted;
                the scanner only reads it.
            kind: ``model_folder.kind``. ``source`` folders are skipped by the
                scanner itself.
            owner: ``model_folder.owner``. A folder PixlStash owns (its own
                downloads, InsightFace, the HuggingFace cache) is DECLARED, not
                walked: the scanner yields only model suffixes and sweeps what
                it did not see to ``missing``, so it would mark the ONNX tagger
                missing. Its scan is the declaration again, an index read.
            priority: ``HIGH`` for the first scan of a folder the owner just
                added, who is looking at it; ``MEDIUM`` for the rescans that
                follow a change, which should not wait behind a library sweep
                either but need not jump one.
            notifier: ``Vault.notify``, to announce the models that arrived.
        """
        super().__init__(
            task_type="ModelFolderScanTask",
            params={"folder_id": folder_id, "path": path, "kind": kind},
        )
        self._hub = hub
        self._folder_id = folder_id
        self._path = path
        self._kind = kind
        self._owner = owner
        self._priority = priority
        self._notifier = notifier
        # Live progress, read by Vault.get_worker_progress for the task manager.
        self._total_count = 0
        self._processed_count = 0

    @property
    def folder_id(self) -> int:
        """Which folder this scan is for, so a caller can match it to a row."""
        return self._folder_id

    @property
    def priority(self) -> TaskPriority:
        return self._priority

    def _run_task(self) -> dict[str, Any]:
        if self._owner == BUILTIN_OWNER:
            return self._redeclare()
        result = ModelFolderScanner(self._hub).scan_folder(
            self._folder_id, self._path, self._kind, progress=self._report_progress
        )
        # The same event a download announces: the shelf re-reads and badges
        # what is new, so a model saved into a folder shows up with nobody
        # pressing anything.
        if result.arrived_model_ids and self._notifier is not None:
            self._notifier(
                EventType.MODELS_ARRIVED, {"model_ids": result.arrived_model_ids}
            )
        return asdict(result)

    def _redeclare(self) -> dict[str, Any]:
        """Re-read a root PixlStash declares, so a repo deleted from the
        HuggingFace cache leaves the shelf without a restart.

        Under ``SHELF_IO_LOCK``: relocating one of these roots repoints this
        row's path, and a declaration at the old path would re-create the row
        there. The path is read again under the slot for the same reason.
        """
        if not SHELF_IO_LOCK.acquire(timeout=_REDECLARE_WAIT_S):
            logger.info(
                "Not re-reading model folder %s (id=%s) now: a move or an import "
                "holds the shelf. The next pass tries again.",
                self._path,
                self._folder_id,
            )
            return {"folder_id": self._folder_id, "declared": False}
        try:
            row = self._hub.fetchone(
                "SELECT path FROM model_folder WHERE id = ?", (self._folder_id,)
            )
            if row is None:
                return {"folder_id": self._folder_id, "declared": False}
            # Stamps `last_checked` itself, which is the finder's clock.
            declared = redeclare_model_root(self._hub, row["path"])
        finally:
            SHELF_IO_LOCK.release()
        return {"folder_id": self._folder_id, "declared": declared is not None}

    def _report_progress(self, processed: int, total: int) -> None:
        self._processed_count = processed
        self._total_count = total
