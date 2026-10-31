"""Finder that keeps the registered model folders in step with the disk.

There is no Rescan. A folder is scanned when it is registered, as soon as its
watchdog watch is up (at start-up, and when an unplugged drive comes back),
again whenever a model file in it is created, changed, moved or deleted
(debounced), and at least every ``RESCAN_INTERVAL_S`` besides, for the changes
a watch cannot see, such as on a network mount. That is affordable because a scan of an unchanged
folder reads no bytes: a file whose size and mtime match its row is touched, not
hashed (``ModelFolderScanner._describe``), so a repeat walk costs one ``stat``
and one row upsert per model file.

The roots PixlStash declares rather than walks (its own downloads,
InsightFace, the HuggingFace cache) are re-declared on the same timer, so a
repo deleted from the cache leaves the shelf without a restart.

One scan at a time (the default ``max_inflight_tasks``), so a set of folders on
one drive is walked in turn rather than all at once, oldest first.

Registered in ``vault.py`` with the hub finders, because the shelf is a hub
fact; only one vault is live at a time.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

from pixlstash.event_types import EventType
from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services.builtin_models import BUILTIN_OWNER
from pixlstash.services.model_folder_scanner import (
    ARRIVING_SETTLE_S,
    TAKEN_FROM_KINDS,
)
from pixlstash.tasks.base_task import TaskStatus
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.tasks.model_folder_scan_task import claim_scan, release_scan

logger = get_logger(__name__)

# How often the registry is read. One small hub query; the interval only bounds
# how soon a due folder is noticed when nothing woke the planner.
_CHECK_INTERVAL_S = 2.0

# How soon a folder is scanned again after a scan left a file in it that was
# still arriving. A watch sees the last write, but a file that sits still
# without growing (a stalled copy of a .gguf) sends nothing more.
_ARRIVING_RETRY_S = ARRIVING_SETTLE_S

# The longest a folder goes without a scan, whatever its watch says.
RESCAN_INTERVAL_S = 600.0


def scanned_folders(hub: HubDatabase, include_declared: bool = False) -> list[dict]:
    """The registered folders a scan catalogues, in id order.

    Never a taken-from kind (an ai-toolkit root, the Downloads folder). A root
    PixlStash declares rather than walks (its own downloads, InsightFace, the
    HuggingFace cache) only with *include_declared*: its "scan" is the
    declaration again (``ModelFolderScanTask``), and anything that would walk it
    (a pick-up) must leave it out, because the walk yields model suffixes only.
    """
    return [
        dict(row)
        for row in hub.fetchall(
            "SELECT id, path, kind, owner, last_checked FROM model_folder ORDER BY id"
        )
        if row["kind"] not in TAKEN_FROM_KINDS
        and (include_declared or row["owner"] != BUILTIN_OWNER)
    ]


def _epoch(stamp: Optional[str]) -> float:
    """``model_folder.last_checked`` as a POSIX time, 0.0 when never scanned."""
    if not stamp:
        return 0.0
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        logger.warning(
            "model_folder.last_checked %r is not an ISO timestamp; treating the "
            "folder as never scanned.",
            stamp,
        )
        return 0.0


class ModelFolderScanFinder(BaseTaskFinder):
    """Hand out a scan for the folder that most needs one.

    A folder is due when its watch reported a change (:meth:`mark_due`), when
    its watch has just been set up, or when neither its last finished scan nor
    the last one handed out is within ``RESCAN_INTERVAL_S``. The second clock is what keeps a scan that keeps
    crashing (``last_checked`` never moves) from being handed out every check.
    A change reported while the folder is being scanned keeps it due, so a file
    landing mid-walk gets a scan of its own after.
    """

    def __init__(
        self,
        hub: HubDatabase,
        watcher=None,
        notifier: Optional[Callable[[EventType, Any], None]] = None,
    ) -> None:
        """Initialise the finder.

        Args:
            hub: The hub database holding ``model_folder``.
            watcher: A :class:`~pixlstash.utils.reference_folder_watcher.
                ReferenceFolderWatcher` whose callback calls :meth:`mark_due`.
                The finder keeps its watches in step with the registry. ``None``
                leaves only the timer.
            notifier: ``Vault.notify``, passed on to the task.
        """
        super().__init__()
        self._hub = hub
        self._watcher = watcher
        self._notifier = notifier
        # Written by the watcher's timer thread, read on the planner's.
        self._lock = threading.Lock()
        self._changed: set[int] = set()
        # folder id -> time.time() the finder last handed out its scan.
        self._handed_out_at: dict[int, float] = {}
        # folder id -> time.time() a scan of it finished leaving a file that
        # was still arriving.
        self._arriving_at: dict[int, float] = {}
        # folder id -> the path its watch was set up on, so a renamed folder
        # is watched again at its new path.
        self._watched: dict[int, str] = {}
        # Folders whose watch could not be set up (an inotify limit, say), so
        # the failure is logged once rather than every check.
        self._unwatchable: set[int] = set()

    def finder_name(self) -> str:
        return "ModelFolderScanFinder"

    def mark_due(self, folder_id: int) -> None:
        """Scan *folder_id* on the next check: its watch saw a change."""
        with self._lock:
            self._changed.add(folder_id)
        # Or the planner's wake lands inside the 2 s throttle, finds nothing,
        # and the planner backs off for up to ten seconds before asking again.
        self._last_check_at = None

    def find_task(self):
        if not self._due(_CHECK_INTERVAL_S):
            return None
        folders = scanned_folders(self._hub, include_declared=True)
        ids = {row["id"] for row in folders}
        # A declared root is re-read on the timer only: its contents are an
        # index (the HuggingFace cache's blobs and symlinks), not model files a
        # watch would see arrive.
        self._sync_watches(
            [row for row in folders if row["owner"] != BUILTIN_OWNER],
            ids,
        )
        with self._lock:
            self._changed &= ids
            for gone in [fid for fid in self._arriving_at if fid not in ids]:
                del self._arriving_at[gone]
            now = time.time()
            changed = set(self._changed) | {
                fid
                for fid, at in self._arriving_at.items()
                if now - at >= _ARRIVING_RETRY_S
            }
        for gone in [fid for fid in self._handed_out_at if fid not in ids]:
            del self._handed_out_at[gone]

        def last_scan(row: dict) -> float:
            return max(
                _epoch(row["last_checked"]), self._handed_out_at.get(row["id"], 0.0)
            )

        for row in sorted(
            folders, key=lambda row: (row["id"] not in changed, last_scan(row))
        ):
            folder_id = row["id"]
            if folder_id not in changed and now - last_scan(row) < RESCAN_INTERVAL_S:
                continue
            task = claim_scan(
                self._hub,
                folder_id,
                row["path"],
                row["kind"],
                owner=row["owner"],
                notifier=self._notifier,
            )
            if task is None:
                continue  # Already being scanned; a change stays due for after.
            with self._lock:
                self._changed.discard(folder_id)
                self._arriving_at.pop(folder_id, None)
            self._handed_out_at[folder_id] = now
            return task
        return None

    def on_task_complete(self, task, error) -> None:
        """Release the claim of a scan the planner never submitted, and come
        back for a file a finished one left because it was still arriving."""
        if task.status == TaskStatus.PENDING:
            release_scan(task)
            return
        if isinstance(task.result, dict) and task.result.get("arriving"):
            with self._lock:
                self._arriving_at[task.folder_id] = time.time()

    def _sync_watches(self, folders: list[dict], ids: set[int]) -> None:
        if self._watcher is None:
            return
        for gone in set(self._watched) - ids:
            self._watcher.unwatch_folder(gone)
            del self._watched[gone]
        self._unwatchable &= ids
        for row in folders:
            folder_id = row["id"]
            if self._watched.get(folder_id, row["path"]) != row["path"]:
                self._watcher.unwatch_folder(folder_id)
                del self._watched[folder_id]
            if folder_id in self._watched or folder_id in self._unwatchable:
                continue
            try:
                self._watcher.watch_folder(folder_id, row["path"])
            except OSError as exc:
                self._unwatchable.add(folder_id)
                logger.warning(
                    "Cannot watch model folder %s (id=%s) for changes: %s. It is "
                    "still rescanned every %d minutes.",
                    row["path"],
                    folder_id,
                    exc,
                    int(RESCAN_INTERVAL_S // 60),
                )
                continue
            # A folder that is not a directory right now (an unplugged drive)
            # is not watched; it is asked again next check.
            if folder_id in self._watcher.watched_ids():
                self._watched[folder_id] = row["path"]
                # Scanned once the watch is up, so nothing that changed before
                # it was (at start-up, while it was unplugged, in the second
                # after it was added) goes unseen until the timer.
                with self._lock:
                    self._changed.add(folder_id)
