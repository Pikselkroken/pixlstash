"""Filesystem watcher for reference folders.

Uses watchdog to monitor reference folder directories for new, changed, or
deleted image files and sidecar caption files.  When a relevant change is
detected the owning folder's ``last_scanned`` timestamp is reset to zero so
the :class:`~pixlstash.tasks.reference_folder_scan_finder.ReferenceFolderScanFinder`
schedules an immediate rescan, and the work-planner is woken so the scan task
runs without waiting for the normal 5-minute poll interval.

Events are debounced per folder so that rapid sequences (e.g. an editor doing
an atomic write via temp-file → rename) are collapsed into a single rescan.
"""

import os
import threading

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# File extensions that should trigger a rescan when they change.
_SIDECAR_EXTS: frozenset[str] = frozenset({".txt", ".caption"})
_IMAGE_EXTS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif", ".avif"}
)
_WATCHED_EXTS: frozenset[str] = _SIDECAR_EXTS | _IMAGE_EXTS

# Seconds to wait after the last filesystem event before triggering a rescan.
# Coalesces rapid event bursts (editor saves, atomic renames, etc.).
_DEBOUNCE_S: float = 2.0


class ReferenceFolderWatcher:
    """Watches reference folder directories for filesystem changes.

    On changes to relevant files (image files or sidecar caption files) the
    caller-supplied *on_folder_changed* callback is invoked with the
    ``folder_id`` of the affected reference folder.  Calls are debounced so
    that burst events produce at most one callback per folder per
    ``_DEBOUNCE_S`` window.

    Args:
        on_folder_changed: Called with ``folder_id: int`` when a relevant
            filesystem change is detected for that folder.
    """

    def __init__(self, on_folder_changed) -> None:
        self._on_folder_changed = on_folder_changed
        self._observer = Observer()
        self._watches: dict[int, object] = {}  # folder_id → watchdog Watch
        self._timers: dict[int, threading.Timer] = {}  # folder_id → debounce timer
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the underlying filesystem observer thread."""
        self._observer.start()

    def stop(self) -> None:
        """Stop the observer and cancel any pending debounce timers."""
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
        self._observer.stop()
        self._observer.join()

    def watch_folder(self, folder_id: int, resolved_path: str) -> None:
        """Begin watching *resolved_path* for the given *folder_id*.

        No-ops if *resolved_path* is not a directory or if the folder is
        already being watched.

        Args:
            folder_id: Primary key of the :class:`~pixlstash.db_models.reference_folder.ReferenceFolder`.
            resolved_path: Absolute host-local (or container-local) path to watch.
        """
        if not os.path.isdir(resolved_path):
            logger.debug(
                "ReferenceFolderWatcher: skipping watch for folder %d — "
                "path is not a directory: %s",
                folder_id,
                resolved_path,
            )
            return
        with self._lock:
            if folder_id in self._watches:
                return
            handler = _ChangeHandler(folder_id, self._schedule_rescan)
            watch = self._observer.schedule(handler, resolved_path, recursive=True)
            self._watches[folder_id] = watch
            logger.debug(
                "ReferenceFolderWatcher: watching folder %d at %s",
                folder_id,
                resolved_path,
            )

    def unwatch_folder(self, folder_id: int) -> None:
        """Stop watching the directory for *folder_id*.

        Args:
            folder_id: Primary key of the reference folder to stop watching.
        """
        with self._lock:
            watch = self._watches.pop(folder_id, None)
            if watch:
                self._observer.unschedule(watch)
            timer = self._timers.pop(folder_id, None)
            if timer:
                timer.cancel()

    def _schedule_rescan(self, folder_id: int) -> None:
        """Debounce a rescan notification for *folder_id*."""
        with self._lock:
            existing = self._timers.pop(folder_id, None)
            if existing:
                existing.cancel()
            timer = threading.Timer(
                _DEBOUNCE_S, self._trigger_rescan, args=(folder_id,)
            )
            timer.daemon = True
            self._timers[folder_id] = timer
            timer.start()

    def _trigger_rescan(self, folder_id: int) -> None:
        """Invoke the caller's callback after the debounce window has elapsed."""
        with self._lock:
            self._timers.pop(folder_id, None)
        logger.debug(
            "ReferenceFolderWatcher: change detected — triggering rescan for folder %d",
            folder_id,
        )
        try:
            self._on_folder_changed(folder_id)
        except Exception as exc:
            logger.warning(
                "ReferenceFolderWatcher: on_folder_changed callback failed for folder %d: %s",
                folder_id,
                exc,
            )


class _ChangeHandler(FileSystemEventHandler):
    """Handles filesystem events for a single reference folder directory."""

    def __init__(self, folder_id: int, callback) -> None:
        self._folder_id = folder_id
        self._callback = callback

    def dispatch(self, event) -> None:
        if event.is_directory:
            return
        # For move events watchdog exposes `dest_path`; use that so a file
        # moved *into* the watched directory also triggers a rescan.
        path: str = getattr(event, "dest_path", None) or event.src_path
        ext = os.path.splitext(path)[1].lower()
        if ext in _WATCHED_EXTS:
            self._callback(self._folder_id)
