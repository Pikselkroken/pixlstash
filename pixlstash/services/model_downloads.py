"""Models PixlStash downloads for a workflow, from ComfyUI's own template list.

A workflow that names a model this machine does not have is refused with
``missing_models`` (``services/workflow_run_service.py``), and each missing
file carries ``downloadable`` when :func:`download_source` knows where to get
it: a file of the same name in the committed template list
(``data/workflow_templates.json``, #1999), which gives its link and its
ComfyUI folder. **The link is the server's, never the caller's**: a request
names a file, so no client can point the server at an address of its choosing.
:func:`start_download` fetches it into the download folder (:func:`~pixlstash.services.managed_model_store.find_download_folder`:
the one marked in the Model folders section, else the default folder) and
registers it on the shelf, so the next run finds it there.

**One thread per download, followed in memory.** A download is minutes of
waiting on a socket, not work for the task runner's queues, and the runs in
flight are followed the same way (``comfyui_service.live_runs``).
:func:`live_downloads` is what ``GET /workers/progress`` reports as
``downloads``; a finished one stays in it for :data:`FINISHED_KEEP_SECONDS` so
every client polling sees how it ended. ponytail: in memory, so a restart
forgets a download in flight and leaves its partial file behind (inert: no row
names it and the scanner skips the suffix).

**The order cannot leave a half file under the real name.** Bytes go to
``<name>.pixlstash-partial`` beside the destination, digested on the way in;
only a complete body is published (no clobber) and registered with that
digest. A file the scanner cannot read as a model (a login page saved under a
model's name) is removed again and reported, rather than left on the shelf.
Cancel, failure and refusal all discard the partial file, and a file the
hub then refuses to register is removed again. A response with no
``Content-Length`` is refused, since there would be no space check.

**Where it lands inside the folder.** The name is the one the workflow asked
for, its own subfolders included (``SDXL/foo.safetensors``). When the folder
has a subfolder named for the ComfyUI model folder the loader reads
(``loras``, ``checkpoints``), the file goes in it, so a ComfyUI ``models``
folder registered on the shelf is filled the way ComfyUI itself would read it.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from functools import lru_cache
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services.managed_model_store import find_download_folder
from pixlstash.services.model_folder_scanner import (
    SHELF_MODEL_SUFFIXES,
    ModelFolderScanner,
)
from pixlstash.services.workflow_templates import load_templates
from pixlstash.services.model_mover import (
    PARTIAL_SUFFIX,
    SHELF_IO_LOCK,
    MoveRefused,
    discard_partial,
    publish_no_clobber,
    require_space,
)
from pixlstash.utils.path_utils import resolve_path_within

logger = get_logger(__name__)

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

# Long enough for a client on the idle 5 s poll to see the ending twice.
FINISHED_KEEP_SECONDS = 30

_CHUNK_BYTES = 1 << 20
# Connect, then the longest silence between two reads.
_TIMEOUT_SECONDS = (15, 120)

_downloads: dict[str, dict] = {}
_lock = threading.Lock()


class DownloadRefused(ValueError):
    """The download was refused before a byte was fetched."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class _Cancelled(Exception):
    """The owner cancelled the download."""


@lru_cache(maxsize=1)
def _sources() -> dict[str, dict]:
    """Every file the template list links, by case-folded name."""
    found: dict[str, dict] = {}
    for template in load_templates().get("templates") or []:
        for entry in template.get("files") or []:
            name, url = entry.get("name"), entry.get("url")
            if name and isinstance(url, str) and url.startswith("https://"):
                found.setdefault(
                    name.casefold(),
                    {"url": url, "directory": entry.get("directory")},
                )
    return found


def download_source(file: str) -> Optional[dict]:
    """Where *file* can be downloaded from, ``{url, directory}``, or None.

    By the file's own name, case folded and without the subfolder a loader may
    put in front of it: the list names files the way ComfyUI's loaders do.
    """
    name = os.path.basename((file or "").replace("\\", "/"))
    return _sources().get(name.casefold()) if name else None


def _relpath(folder_path: str, file: str, comfyui_folder: Optional[str]) -> str:
    """Where *file* goes inside the download folder, relative to it."""
    name = file.replace("\\", "/").strip().strip("/")
    if (
        comfyui_folder
        and not name.startswith(f"{comfyui_folder}/")
        and os.path.isdir(os.path.join(folder_path, comfyui_folder))
    ):
        name = f"{comfyui_folder}/{name}"
    return name


def start_download(
    hub: HubDatabase,
    file: str,
    on_arrived: Optional[Callable[[int], None]] = None,
) -> dict:
    """Start fetching *file* onto the shelf, and return its entry.

    Args:
        hub: The hub database holding the shelf tables.
        file: The model name the workflow asks for, as its loader spells it.
        on_arrived: Called with the new model id once it is on the shelf.

    Raises:
        DownloadRefused: Nothing was fetched; ``status_code`` says why.
    """
    source = download_source(file)
    if source is None:
        raise DownloadRefused(
            f"PixlStash does not know where to download {file} from.", 404
        )
    url = source["url"]
    parsed = urlparse(url)
    if not file.lower().endswith(SHELF_MODEL_SUFFIXES):
        raise DownloadRefused(
            f"{file} is not a model file the shelf keeps "
            f"({', '.join(SHELF_MODEL_SUFFIXES)})."
        )
    folder = find_download_folder(hub)
    if folder is None:
        raise DownloadRefused(
            "There is no folder to download into. Add a model folder first.", 409
        )
    if not os.path.isdir(folder["path"]):
        raise DownloadRefused(
            f"{folder['path']} is not a readable directory right now.", 409
        )
    relpath = _relpath(folder["path"], file, source["directory"])
    try:
        target = resolve_path_within(folder["path"], relpath)
    except ValueError as exc:
        logger.error(
            "Refusing to download %r into %s: it resolves outside it (%s).",
            file,
            folder["path"],
            exc,
        )
        raise DownloadRefused(f"{file!r} would be written outside the folder.") from exc
    if os.path.lexists(target):
        raise DownloadRefused(f"{relpath} is already in {folder['path']}.", 409)

    entry = {
        "id": uuid.uuid4().hex,
        "file": os.path.basename(target),
        "host": parsed.netloc,
        "folder_path": folder["path"],
        "status": STATUS_RUNNING,
        "received": 0,
        "total": 0,
        "error": None,
        "model_id": None,
        "ended_at": None,
    }
    with _lock:
        # Case folded: on macOS and Windows two spellings are one partial file.
        if any(
            d["_target"].casefold() == target.casefold()
            and d["status"] == STATUS_RUNNING
            for d in _downloads.values()
        ):
            raise DownloadRefused(f"{relpath} is already being downloaded.", 409)
        entry["_target"] = target
        entry["_cancel"] = threading.Event()
        _downloads[entry["id"]] = entry
    thread = threading.Thread(
        target=_run,
        args=(hub, entry, url, int(folder["id"]), relpath, on_arrived),
        name=f"model-download-{entry['id'][:8]}",
        daemon=True,
    )
    try:
        thread.start()
    except RuntimeError as exc:
        # Left registered as running, it would refuse every retry as "already
        # being downloaded" until a restart.
        with _lock:
            _downloads.pop(entry["id"], None)
        logger.error("Could not start the download of %s: %s", target, exc)
        raise DownloadRefused(
            f"The download of {relpath} could not be started.", 503
        ) from exc
    logger.info("Downloading %s from %s into %s.", relpath, parsed.netloc, target)
    return _public(entry)


def cancel_download(download_id: str) -> bool:
    """Ask a running download to stop. False when no such download is running."""
    with _lock:
        entry = _downloads.get(download_id)
        # Past `_committed` the file is being published and will land.
        if (
            entry is None
            or entry["status"] != STATUS_RUNNING
            or entry.get("_committed")
        ):
            return False
        entry["_cancel"].set()
    return True


def live_downloads() -> list[dict]:
    """The downloads running, and those that ended in the last few seconds."""
    now = time.monotonic()
    with _lock:
        for key in [
            key
            for key, d in _downloads.items()
            if d["ended_at"] is not None and now - d["ended_at"] > FINISHED_KEEP_SECONDS
        ]:
            del _downloads[key]
        return [_public(d) for d in _downloads.values()]


def _public(entry: dict) -> dict:
    shown = {k: v for k, v in entry.items() if not k.startswith("_")}
    shown.pop("ended_at", None)
    total = entry["total"]
    shown["percent"] = round(100 * entry["received"] / total, 1) if total else 0
    return shown


def _end(entry: dict, status: str, error: Optional[str] = None) -> None:
    with _lock:
        entry["status"] = status
        entry["error"] = error
        entry["ended_at"] = time.monotonic()


def _run(
    hub: HubDatabase,
    entry: dict,
    url: str,
    folder_id: int,
    relpath: str,
    on_arrived: Optional[Callable[[int], None]],
) -> None:
    target = entry["_target"]
    partial = target + PARTIAL_SUFFIX
    cancel = entry["_cancel"]
    try:
        digest = _fetch(url, partial, entry, cancel)
        model_id = _publish(hub, partial, entry, folder_id, relpath, digest)
    except _Cancelled:
        discard_partial(partial)
        logger.info("Download of %s cancelled.", target)
        _end(entry, STATUS_CANCELLED)
        return
    except (OSError, requests.RequestException, MoveRefused, ValueError) as exc:
        discard_partial(partial)
        logger.warning("Download of %s from %s failed: %s", target, entry["host"], exc)
        _end(entry, STATUS_FAILED, str(exc))
        return
    except Exception as exc:
        # A thread's last stop: anything else (the hub refusing the row) would
        # otherwise leave the entry reading "running" forever.
        discard_partial(partial)
        logger.exception("Download of %s failed unexpectedly.", target)
        _end(entry, STATUS_FAILED, f"The download failed: {exc}")
        return
    entry["model_id"] = model_id
    _end(entry, STATUS_DONE)
    logger.info("Downloaded %s onto the shelf as model %s.", target, model_id)
    if on_arrived is not None:
        try:
            on_arrived(model_id)
        except Exception as exc:  # A missed nudge must not fail a download.
            logger.warning(
                "Model %s arrived but its announcement failed: %s", model_id, exc
            )


def _fetch(url: str, partial: str, entry: dict, cancel: threading.Event) -> str:
    """Stream *url* into *partial* and return the SHA-256 of what was written."""
    os.makedirs(os.path.dirname(partial), exist_ok=True)
    sha = hashlib.sha256()
    with requests.get(
        url,
        stream=True,
        timeout=_TIMEOUT_SECONDS,
        headers={"User-Agent": "PixlStash"},
    ) as response:
        response.raise_for_status()
        # The list's links are https; a redirect must not drop to plain http.
        if url.startswith("https://") and not response.url.startswith("https://"):
            raise ValueError("The download was redirected away from https.")
        if "text/html" in response.headers.get("Content-Type", ""):
            raise ValueError(
                "The link answered with a web page, not a file. It may need "
                "you to sign in, or it is the model's page rather than its "
                "download link."
            )
        total = int(response.headers.get("Content-Length") or 0)
        if total <= 0:
            # Without a size there is no space check, and a stream that never
            # ends would fill the disk.
            raise ValueError("The server did not say how large the file is.")
        entry["total"] = total
        require_space(os.path.dirname(partial), total)
        with open(partial, "wb") as out:
            for chunk in response.iter_content(_CHUNK_BYTES):
                if cancel.is_set():
                    raise _Cancelled()
                out.write(chunk)
                sha.update(chunk)
                entry["received"] += len(chunk)
    # A body cut short of its Content-Length is raised by urllib3 itself
    # (`enforce_content_length`), so it never reaches here as complete.
    return sha.hexdigest()


def _publish(
    hub: HubDatabase,
    partial: str,
    entry: dict,
    folder_id: int,
    relpath: str,
    digest: str,
) -> int:
    target = entry["_target"]
    cancel = entry["_cancel"]
    # Waited for rather than refused: the bytes are already here, and a move
    # running now is no reason to fetch gigabytes again.
    while not SHELF_IO_LOCK.acquire(timeout=1):
        if cancel.is_set():
            raise _Cancelled()
    try:
        # The last moment a cancel can win; after it `cancel_download` says no,
        # so it never answers "cancelled" for a file that then lands.
        with _lock:
            if cancel.is_set():
                raise _Cancelled()
            entry["_committed"] = True
        publish_no_clobber(partial, target)
        try:
            model_id = ModelFolderScanner(hub).register_file(
                folder_id, target, relpath, sha256=digest
            )
        except Exception:
            # The hub refused the row (the folder was forgotten meanwhile): a
            # file no row names would also refuse every retry as "already
            # there", so it goes too.
            discard_partial(target)
            raise
        if model_id is None:
            discard_partial(target)
            raise ValueError(
                f"{os.path.basename(target)} downloaded but does not read as a "
                "model file, so it was removed again. The server log says why."
            )
        return model_id
    finally:
        SHELF_IO_LOCK.release()
