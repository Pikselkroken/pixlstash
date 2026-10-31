"""Open with…: an edit copy of a picture, watched, its saves imported stacked.

``POST /open-with`` makes a byte-identical copy of the picture in
a session folder of its own, launches the chosen app on it
(``pixlstash/utils/open_with.py``) and records the session in
``session.json``. :class:`EditCopyWatcher` watches every session folder; when
an app saves (over the copy, by renaming a temp file over it, or as a new
image file in the same folder), :func:`process_session` imports what changed as
a new picture derived from the original, at the top of its stack, through the
same :func:`~pixlstash.image_plugins.service.import_derived_outputs` an image
plugin's output goes through. See ``docs/integration_architecture.md`` §2.5.

**Where.** ``<config dir>/edit-copies/<session>/``, beside ``share-copies/``
but a root of its own: share copies are swept after 12 h, which would delete a
file still open in an editor. A session lives 24 h after the last change to its
folder or any file in it (a launch, an import, a save), and
:func:`sweep_edit_sessions` removes older ones at start-up, after the
start-up import, and before each new session.

**The edit is the person's work.** It is never imported half-written (a 2 s
debounce, then a full decode), never imported twice (``seen`` holds the hash
last imported per file, starting with the original's, so an app that writes
the file back unchanged imports nothing), and still imported when the original
was deleted meanwhile, without a stack: an unstacked picture is better than a
lost edit.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from io import BytesIO
from typing import Callable

from PIL import Image
from watchdog.events import (
    EVENT_TYPE_CLOSED_NO_WRITE,
    EVENT_TYPE_OPENED,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from pixlstash.db_models import Picture
from pixlstash.event_types import EventType
from pixlstash.image_plugins.service import (
    _import_output_images,
    import_derived_outputs,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.atomic_write import write_json_atomic
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.media_files import SUPPORTED_IMAGE_EXTS, is_hidden_entry
from pixlstash.utils.service.share_copies import _is_batch_name, make_share_copies

logger = get_logger(__name__)

EDIT_COPIES_DIRNAME = "edit-copies"
EDIT_COPY_MAX_AGE_SECONDS = 24 * 60 * 60
SESSION_FILE = "session.json"
# Seconds after the last event in a session folder before it is read, so an
# editor's temp-file-and-rename save is read once, whole.
DEBOUNCE_S = 2.0

# ponytail: one lock for every session's import; edits arrive one save at a
# time, per-session locks if that ever stops being true.
_IMPORT_LOCK = threading.Lock()

_READ_ONLY_EVENTS = frozenset({EVENT_TYPE_OPENED, EVENT_TYPE_CLOSED_NO_WRITE})
_FORMAT_EXT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "TIFF": ".tiff"}


def edit_copies_root(config_dir: str) -> str:
    """The edit-copy root for a server whose config file lives in *config_dir*."""
    return os.path.join(config_dir, EDIT_COPIES_DIRNAME)


def create_session(
    picture: dict,
    image_root: str,
    root: str,
    *,
    library_uuid: str | None,
    app_name: str | None,
) -> dict:
    """Copy *picture* into a new session folder and record the session.

    Args:
        picture: ``{"id", "file_path", "original_file_name", "created_at"}``.
        image_root: The vault's image root.
        root: :func:`edit_copies_root`.
        library_uuid: The library the picture is in; an edit is imported only
            while that library is the active one.
        app_name: What the copy was opened in, for the log.

    Returns:
        ``{"session", "path"}``: the session id and the copy's path.

    Raises:
        FileNotFoundError: The picture's file is missing or unreadable.
    """
    sweep_edit_sessions(root)
    result = make_share_copies([picture], image_root, root, keep_generation_data=True)
    if not result["files"]:
        raise FileNotFoundError(f"No copy could be made of picture {picture['id']}")
    entry = result["files"][0]
    with open(entry["path"], "rb") as handle:
        seen = ImageUtils.calculate_hash_from_bytes(handle.read())
    write_session(
        result["directory"],
        {
            "picture_id": picture["id"],
            "library_uuid": library_uuid,
            "file": entry["name"],
            "seen": {entry["name"]: seen},
            "app": app_name,
        },
    )
    return {"session": result["batch"], "path": entry["path"]}


def read_session(session_dir: str) -> dict | None:
    """The session recorded in *session_dir*, or None (logged) when unreadable."""
    path = os.path.join(session_dir, SESSION_FILE)
    try:
        with open(path, encoding="utf-8") as handle:
            session = json.load(handle)
        if not isinstance(session.get("picture_id"), int) or not isinstance(
            session.get("seen"), dict
        ):
            raise ValueError("missing picture_id or seen")
        return session
    except (OSError, ValueError) as exc:
        logger.warning(
            "Edit copy session %s has no readable %s and is left for the "
            "sweep, never imported: %s",
            session_dir,
            SESSION_FILE,
            exc,
        )
        return None


def write_session(session_dir: str, session: dict) -> None:
    """Write *session* and touch the folder, which keeps it from the sweep."""
    write_json_atomic(os.path.join(session_dir, SESSION_FILE), session)
    os.utime(session_dir)


def changed_file(path: str, seen_sha: str | None) -> tuple[bytes, str, str] | None:
    """``(bytes, ext, sha)`` when *path* is a whole image unlike *seen_sha*.

    None for anything that is not to be imported: not a regular file, hidden,
    an editor backup (``~``), not an image extension, unchanged, or not
    decodable (half-written: the next event reads it again). The extension
    comes from the decoded format, never the file name.
    """
    name = os.path.basename(path)
    if (
        is_hidden_entry(name)
        or name.endswith("~")
        or os.path.splitext(name)[1].lower() not in SUPPORTED_IMAGE_EXTS
        or os.path.islink(path)
        or not os.path.isfile(path)
    ):
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        logger.debug("Edit copy %s not readable yet: %s", path, exc)
        return None
    sha = ImageUtils.calculate_hash_from_bytes(data)
    if sha == seen_sha:
        return None
    try:
        # load(), not verify(): verify() reads headers and trailers only, and a
        # JPEG cut off mid-write passes it.
        with Image.open(BytesIO(data)) as img:
            fmt = img.format or ""
            img.load()
    except Exception as exc:
        logger.debug("Edit copy %s does not decode yet: %s", path, exc)
        return None
    return data, _FORMAT_EXT.get(fmt, f".{fmt.lower()}" if fmt else ".png"), sha


def process_session(server, session_dir: str) -> list[int]:
    """Import every changed image in *session_dir*; the new picture ids."""
    with _IMPORT_LOCK:
        session = read_session(session_dir)
        if session is None:
            return []
        # One vault for the whole pass, checked to be the session's library:
        # a library switch mid-pass then fails the write on the closed vault
        # (logged, retried on the next save) instead of importing into the
        # library that replaced it.
        vault = server.vault
        library_uuid = session.get("library_uuid")
        active = vault.library_uuid
        if library_uuid and active and library_uuid != active:
            logger.info(
                "Edit copy session %s belongs to library %s, not the active "
                "one; its edits wait until that library is active and the "
                "file is saved again or the server restarts.",
                session_dir,
                library_uuid,
            )
            return []
        try:
            names = sorted(os.listdir(session_dir))
        except OSError as exc:
            logger.warning("Could not list edit copy session %s: %s", session_dir, exc)
            return []
        created: list[int] = []
        for name in names:
            found = changed_file(
                os.path.join(session_dir, name), session["seen"].get(name)
            )
            if found is None:
                continue
            data, ext, sha = found
            created += _import_edit(
                _PinnedVaultServer(server, vault), session, data, ext, name
            )
            session["seen"][name] = sha
            write_session(session_dir, session)
        return created


def sweep_edit_sessions(
    root: str, max_age_seconds: float = EDIT_COPY_MAX_AGE_SECONDS
) -> int:
    """Remove sessions untouched for *max_age_seconds*; how many were removed.

    Untouched means the folder and every file in it: a save over the copy
    changes the file's time, not the folder's, and an edit not yet imported
    (the server was down, another library was active) must survive.

    Never raises on a filesystem error: it runs at start-up and before each
    new session, and a cleanup that cannot finish is logged and retried then.
    """
    try:
        entries = [e for e in os.scandir(root) if _is_batch_name(e.name)]
    except FileNotFoundError:
        return 0
    except OSError as exc:
        logger.warning("Could not list edit copies in %s: %s", root, exc)
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            newest = max(
                [entry.stat().st_mtime]
                + [
                    f.stat(follow_symlinks=False).st_mtime
                    for f in os.scandir(entry.path)
                ]
            )
            if newest >= cutoff:
                continue
            shutil.rmtree(entry.path)
            removed += 1
        except OSError as exc:
            logger.warning("Could not sweep edit copy session %s: %s", entry.path, exc)
    return removed


class _PinnedVaultServer:
    """The server, with ``vault`` fixed to one vault across a library switch."""

    def __init__(self, server, vault):
        self._server = server
        self.vault = vault

    def __getattr__(self, name):
        return getattr(self._server, name)


def reconcile_all(server, root: str) -> int:
    """Run :func:`process_session` once over every session; how many imported.

    At start-up, for an edit saved while the backend was not running.
    """
    try:
        entries = [e for e in os.scandir(root) if _is_batch_name(e.name)]
    except FileNotFoundError:
        return 0
    except OSError as exc:
        logger.warning("Could not list edit copies in %s: %s", root, exc)
        return 0
    imported = 0
    for entry in entries:
        if entry.is_dir() and not entry.is_symlink():
            imported += len(process_session(server, entry.path))
    return imported


def _import_edit(server, session: dict, data: bytes, ext: str, name: str) -> list[int]:
    picture_id = session["picture_id"]

    def fetch(db_session):
        return db_session.get(Picture, picture_id)

    original = server.vault.db.run_immediate_read_task(fetch)
    if original is None:
        logger.warning(
            "Picture %s was removed while %s was open in %s; importing the "
            "edit on its own.",
            picture_id,
            name,
            session.get("app"),
        )
        new_ids, _dupes, output_ids = _import_output_images(server, [(data, ext)])
        touched = output_ids
    else:
        if original.deleted:
            logger.warning(
                "Picture %s was deleted while %s was open in %s; importing "
                "the edit without a stack.",
                picture_id,
                name,
                session.get("app"),
            )
        new_ids, _dupes, output_ids = import_derived_outputs(
            server, [original], [(data, ext)], stack=not original.deleted
        )
        touched = [picture_id, *output_ids]
    logger.info(
        "Imported an edit of picture %s saved in %s (%s) as %s",
        picture_id,
        session.get("app"),
        name,
        output_ids,
    )
    # Nobody's tab made this picture, but someone asked for it: announced as a
    # UI change with no origin, so every tab inserts it rather than offering
    # a "new pictures" pill.
    if new_ids:
        server.vault.notify(
            EventType.PICTURE_IMPORTED,
            {
                "ids": new_ids,
                "source": "ui",
                "origin_client_id": None,
                "change_kind": "added",
            },
        )
    if touched:
        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": touched,
                "source": "ui",
                "origin_client_id": None,
                "change_kind": "updated",
            },
        )
    return new_ids


class _Handler(FileSystemEventHandler):
    def __init__(self, root: str, schedule: Callable[[str], None]) -> None:
        self._root = root
        self._schedule = schedule

    def on_any_event(self, event) -> None:
        if event.event_type in _READ_ONLY_EVENTS:
            return
        for path in (getattr(event, "src_path", ""), getattr(event, "dest_path", "")):
            if not path:
                continue
            try:
                rel = os.path.relpath(os.fsdecode(path), self._root)
            except ValueError:
                # Windows: a move from another drive has no relative path,
                # and an exception here would reach watchdog's own thread.
                continue
            session = rel.split(os.sep, 1)[0]
            if _is_batch_name(session):
                self._schedule(session)


class EditCopyWatcher:
    """Calls *on_settled* with a session folder once its events settle.

    One recursive observer on the root; each session folder debounces on its
    own timer, so a save in one never delays another.

    Args:
        root: :func:`edit_copies_root`.
        on_settled: Called with the session folder's path, on a timer thread.
        debounce_s: Seconds of quiet before *on_settled* runs.
    """

    def __init__(
        self,
        root: str,
        on_settled: Callable[[str], object],
        debounce_s: float = DEBOUNCE_S,
    ) -> None:
        self._root = root
        self._on_settled = on_settled
        self._debounce_s = debounce_s
        self._observer = Observer()
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        # Held around each callback, so stop() waits out one in flight and no
        # callback starts after it (the vault closes next).
        self._run_lock = threading.Lock()
        self._stopped = False

    def start(self) -> None:
        """Watch the root, creating it when missing."""
        os.makedirs(self._root, mode=0o700, exist_ok=True)
        self._observer.schedule(
            _Handler(self._root, self._schedule), self._root, recursive=True
        )
        self._observer.start()

    def stop(self) -> None:
        """Stop watching, drop pending reads and wait out a running one."""
        # Observer first, so no event schedules a timer after the cancel.
        if self._observer.is_alive():
            self._observer.stop()
            self._observer.join()
        with self._lock:
            timers, self._timers = list(self._timers.values()), {}
        for timer in timers:
            timer.cancel()
        with self._run_lock:
            self._stopped = True

    def _schedule(self, session: str) -> None:
        with self._lock:
            old = self._timers.get(session)
            if old is not None:
                old.cancel()
            timer = threading.Timer(self._debounce_s, self._run, [session])
            timer.daemon = True
            self._timers[session] = timer
            timer.start()

    def _run(self, session: str) -> None:
        with self._lock:
            if self._timers.get(session) is threading.current_thread():
                del self._timers[session]
        path = os.path.join(self._root, session)
        try:
            with self._run_lock:
                if not self._stopped and os.path.isdir(path):
                    self._on_settled(path)
        except Exception as exc:
            # A raising callback can deafen every watchdog watcher (the pin
            # note in pyproject.toml); the next save tries again.
            logger.error(
                "Reading edit copy session %s failed with %s: %s",
                path,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
