"""The watched ``workflows/`` folder: an inbox for workflow files.

A ``.json`` put in the folder is imported the way a drag and drop is, then
renamed ``<stem>.<content hash>.json``. The folder is an inbox, not a mirror:
removing a file from it never deletes a workflow. Deleting a workflow goes the
other way, through :func:`trash_workflow`, which writes the workflow back into
the folder and moves that file to the system trash, so restoring it from the
trash puts it back in the inbox and imports it again.

The hash is taken over the workflow as it would be stored (placeholder tokens
migrated, PixlStash's own ``pixlstash_*`` keys ignored), so the file a delete
writes back carries the same name as the file the import consumed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from typing import Callable

from platformdirs import user_data_dir
from send2trash import send2trash
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from pixlstash.pixl_logging import get_logger
from pixlstash.services import workflow_bindings
from pixlstash.utils.path_utils import resolve_path_within

logger = get_logger(__name__)

# Hex digits of the content hash kept in a filename; 64 bits is plenty for one
# person's folder and keeps the name readable.
_HASH_LENGTH = 16
_HASHED_NAME_RE = re.compile(rf"^(?P<stem>.*)\.(?P<hash>[0-9a-f]{{{_HASH_LENGTH}}})$")
# Seconds after the last event before the folder is reconciled, so an editor's
# temp-file-and-rename save is read once, whole.
_DEBOUNCE_S = 1.0

# Held by a reconcile and by a delete for its whole trash-then-remove, so a
# reconcile never reads a file a delete then trashes and imports it again.
INBOX_LOCK = threading.Lock()


def workflow_inbox_dir() -> str:
    """The watched folder, in the app's data directory."""
    return os.path.join(user_data_dir("pixlstash"), "workflows")


def content_hash(workflow: dict) -> str:
    """The hash that names *workflow*'s file in the inbox.

    Raises:
        RecursionError: The document nests too deeply to serialise.
    """
    stored, _migrated = workflow_bindings.migrate_placeholders(workflow)
    digest = hashlib.sha256(workflow_bindings.canonical(stored).encode("utf-8"))
    return digest.hexdigest()[:_HASH_LENGTH]


def _split(filename: str) -> tuple[str, str | None]:
    """``("flow", "0123…")`` for ``flow.0123….json``; ``(stem, None)`` otherwise."""
    stem = filename[: -len(".json")]
    match = _HASHED_NAME_RE.match(stem)
    if match is None:
        return stem, None
    return match["stem"], match["hash"]


def _entries(folder: str) -> list[str]:
    """The workflow files in *folder*, skipping dotfiles (temp saves)."""
    return sorted(
        entry
        for entry in os.listdir(folder)
        if entry.lower().endswith(".json") and not entry.startswith(".")
    )


def reconcile(folder: str, store: Callable[[str, dict], dict]) -> int:
    """Import every file in *folder* and give it its content-addressed name.

    Idempotent: a file already imported matches its stored copy and keeps its
    name, so running this again, or on the rename it just made, does nothing.

    Args:
        folder: The inbox. Created when missing.
        store: Stores one workflow under a name, matching a stored copy; the
            import route's own ``_store_workflow`` with ``keep_both``.

    Returns:
        How many files were stored as new workflows.
    """
    with INBOX_LOCK:
        try:
            os.makedirs(folder, exist_ok=True)
            entries = _entries(folder)
        except OSError as exc:
            logger.error("Could not read the workflow inbox %s: %s", folder, exc)
            return 0
        imported = 0
        for entry in entries:
            path = os.path.join(folder, entry)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    workflow = json.load(handle)
                if not isinstance(workflow, dict):
                    raise ValueError("not a JSON object")
                stem, _old_hash = _split(entry)
                digest = content_hash(workflow)
                result = store(f"{stem}.json", workflow)
                imported += 0 if result.get("matched") else 1
                wanted = os.path.join(folder, f"{stem}.{digest}.json")
                # Never over another file: the hash ignores pixlstash_* keys,
                # so a same-named file can still hold different bytes.
                if path != wanted and not os.path.exists(wanted):
                    os.replace(path, wanted)
            except (OSError, ValueError, RecursionError) as exc:
                # Left where it is: fixing the file and saving it again is an
                # event, and the next start tries it too.
                logger.warning("Workflow inbox file %s was not imported: %s", path, exc)
        if imported:
            logger.info("Imported %d workflow(s) from the inbox %s.", imported, folder)
        return imported


def trash_workflow(folder: str, name: str, workflow: dict) -> None:
    """Write *workflow* back to the inbox and move it to the system trash.

    Every inbox file holding the workflow goes, not just the one written here,
    or a copy dropped under another name would import it again at the next
    reconcile. What a file holds is read from it, never taken from its name: a
    file edited in place still carries its old hash until the watcher renames
    it, and that edit must stay for the watcher to import. For the same reason
    the copy is written to a name no file has yet. Call it holding
    :data:`INBOX_LOCK`, and remove the stored workflow before releasing it.

    Raises:
        ValueError: *name* would put the copy outside *folder*.
        OSError, send2trash.TrashPermissionError: The file could not be written
            or trashed. The stored workflow must then be kept.
    """
    digest = content_hash(workflow)
    os.makedirs(folder, exist_ok=True)
    stem = _split(name)[0]
    written, counter = f"{stem}.{digest}.json", 2
    while True:
        # The name comes from the request the delete arrived on, so the inbox
        # gets the same guard the user workflow directory already gets: the
        # copy is written where it is meant to be or not at all.
        path = resolve_path_within(folder, written)
        try:
            # "x": never over a file already there.
            with open(path, "x", encoding="utf-8") as handle:
                json.dump(workflow, handle, indent=2, ensure_ascii=True)
            break
        except FileExistsError:
            written = f"{stem} ({counter}).{digest}.json"
            counter += 1
    for entry in _entries(folder):
        path = os.path.join(folder, entry)
        if _file_hash(path) == digest:
            send2trash(path)


def _file_hash(path: str) -> str | None:
    """The hash of what an inbox file holds, or None when it is not a workflow."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            workflow = json.load(handle)
        return content_hash(workflow) if isinstance(workflow, dict) else None
    except (OSError, ValueError, RecursionError) as exc:
        logger.warning(
            "Could not read inbox file %s to compare it with a deleted workflow: %s",
            path,
            exc,
        )
        return None


class _Handler(FileSystemEventHandler):
    def __init__(self, schedule: Callable[[], None]) -> None:
        self._schedule = schedule

    def on_any_event(self, event) -> None:
        paths = (getattr(event, "src_path", ""), getattr(event, "dest_path", ""))
        if not event.is_directory and any(
            str(path).lower().endswith(".json") for path in paths
        ):
            self._schedule()


class WorkflowInboxWatcher:
    """Reconciles the inbox shortly after anything in it changes.

    Args:
        folder: The inbox.
        store: Passed to :func:`reconcile`.
    """

    def __init__(self, folder: str, store: Callable[[str, dict], dict]) -> None:
        self._folder = folder
        self._store = store
        self._observer = Observer()
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Watch the folder, creating it when missing."""
        os.makedirs(self._folder, exist_ok=True)
        self._observer.schedule(_Handler(self._schedule), self._folder)
        self._observer.start()

    def stop(self) -> None:
        """Stop watching, drop a pending reconcile and wait out a running one."""
        # Observer first, so no event schedules a timer after the cancel.
        self._observer.stop()
        self._observer.join()
        with self._lock:
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
            timer.join()

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(_DEBOUNCE_S, self._run)
            self._timer.daemon = True
            self._timer.start()

    def _run(self) -> None:
        try:
            reconcile(self._folder, self._store)
        except Exception as exc:
            # A timer thread's exception is otherwise lost; the next event or
            # start tries again.
            logger.error(
                "Reconciling the workflow inbox %s failed with %s: %s",
                self._folder,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
