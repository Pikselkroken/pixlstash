"""Models PixlStash downloads for a workflow, from ComfyUI's own template list.

A workflow that names a model this machine does not have is refused with
``missing_models`` (``services/workflow_run_service.py``), and each missing
file carries ``downloadable`` when :func:`download_source` knows where to get
it: a file of the same name in the committed template list
(``data/workflow_templates.json``, #1999), which gives its link and its
ComfyUI folder. **The link is the server's, never the caller's**: a request
names a file, so no client can point the server at an address of its choosing.
Where the committed list has no such file, the templates the owner's ComfyUI
serves are asked (:func:`live_download_source`), for one ComfyUI shipped after
the list was last rebuilt; a link from there is taken only when
:func:`trusted_live_link` holds (https on ``huggingface.co``, a format loading
cannot run as code), and carries no digest to check the download against.
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
from urllib.parse import urljoin, urlparse

import requests

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services import comfyui_templates
from pixlstash.services.managed_model_store import find_download_folder
from pixlstash.services.model_folder_scanner import (
    SHELF_MODEL_SUFFIXES,
    ModelFolderScanner,
)
from pixlstash.services.workflow_templates import directory_slot, load_templates
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

# What a link from a live ComfyUI template may be (`trusted_live_link`).
LIVE_HOSTS = frozenset({"huggingface.co"})
LIVE_SUFFIXES = (".safetensors", ".gguf")
# Where a live link's redirects may go: Hugging Face and its CDN. Every hop is
# checked before it is asked, since a live link has no digest to catch a file
# that came from somewhere else.
LIVE_HOP_DOMAINS = ("huggingface.co", "hf.co")
_MAX_HOPS = 10

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
                    {
                        "url": url,
                        "directory": entry.get("directory"),
                        "sha256": entry.get("sha256"),
                    },
                )
    return found


def download_source(file: str) -> Optional[dict]:
    """Where *file* can be downloaded from, ``{url, directory, sha256}``, or None.

    ``sha256`` is the digest the list records for the link, or None.

    By the file's own name, case folded and without the subfolder a loader may
    put in front of it: the list names files the way ComfyUI's loaders do.
    """
    name = os.path.basename((file or "").replace("\\", "/"))
    return _sources().get(name.casefold()) if name else None


def trusted_live_link(name: str, url: Optional[str]) -> bool:
    """Whether a link a live ComfyUI template gives for *name* may be fetched.

    Only ``https://huggingface.co/...``, exactly that host (no userinfo, no
    port), and only for a file format that loading cannot run as code: not
    ``.pth``, which is a pickle. The committed list's links were reviewed; a
    live template's were not, so it gets the narrower rule.
    """
    if not isinstance(url, str) or not name.lower().endswith(LIVE_SUFFIXES):
        return False
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc in LIVE_HOSTS


def live_sources(comfyui_url: Optional[str], object_info: Optional[dict]) -> dict:
    """Every file the templates *comfyui_url* serves link trustably, by name.

    The fallback to :func:`download_source`, for a template ComfyUI shipped
    after the committed list was last rebuilt: ``{case-folded name: {url,
    directory, sha256: None, live: True}}`` for each file whose link passes
    :func:`trusted_live_link` (a live template records no digest), the first
    template's link where two share a name, as the committed list does.

    The listing is the one ``GET /comfyui/templates`` keeps for ten minutes
    (:func:`~pixlstash.services.comfyui_templates.read_listing`). Empty while
    ComfyUI is not answering (*object_info* None): the read would only wait
    out its timeouts, and the listing it kept would say no pack loaded.
    """
    if not comfyui_url or object_info is None:
        return {}
    try:
        listing = comfyui_templates.read_listing(comfyui_url, object_info)
    except comfyui_templates.TemplatesUnavailable as exc:
        logger.info(
            "No templates from ComfyUI at %s to find download links in: %s",
            comfyui_url,
            exc,
        )
        return {}
    found: dict[str, dict] = {}
    for template in listing.templates:
        for item in template.files:
            name = os.path.basename(item.name.replace("\\", "/"))
            if trusted_live_link(name, item.url):
                found.setdefault(
                    name.casefold(),
                    {
                        "url": item.url,
                        "directory": item.directory,
                        "sha256": None,
                        "live": True,
                    },
                )
    return found


def live_download_source(
    file: str, comfyui_url: Optional[str], object_info: Optional[dict]
) -> Optional[dict]:
    """Where *file* can be downloaded from by the templates *comfyui_url*
    serves (:func:`live_sources`), or None."""
    name = os.path.basename((file or "").replace("\\", "/")).casefold()
    return live_sources(comfyui_url, object_info).get(name) if name else None


def digest_source(sha256: str) -> Optional[dict]:
    """The file the template list links under *sha256*, or None.

    ``{name, url, directory}``, for a loader that names its model by digest
    alone (ComfyUI-PixlStash's): the list records the digest Hugging Face
    states for each link (``scripts/build_template_suggestions.py``).
    """
    digest = (sha256 or "").strip().lower()
    for template in load_templates().get("templates") or []:
        for entry in template.get("files") or []:
            url = entry.get("url")
            if (
                digest
                and entry.get("sha256") == digest
                and entry.get("name")
                and isinstance(url, str)
                and url.startswith("https://")
            ):
                return {
                    "name": entry["name"],
                    "url": url,
                    "directory": entry.get("directory"),
                }
    return None


def _relpath(folder_path: str, file: str, comfyui_folder: Optional[str]) -> str:
    """Where *file* goes inside the download folder, relative to it."""
    name = file.replace("\\", "/").strip().strip("/")
    if not comfyui_folder:
        return name
    head, sep, rest = name.partition("/")
    if sep and head.casefold() == comfyui_folder.casefold():
        # Already under it, in whatever case: once, spelled as the list does.
        return f"{comfyui_folder}/{rest}"
    if os.path.isdir(os.path.join(folder_path, comfyui_folder)):
        name = f"{comfyui_folder}/{name}"
    return name


def start_download(
    hub: HubDatabase,
    file: str,
    on_arrived: Optional[Callable[[int], None]] = None,
    comfyui_url: Optional[str] = None,
    object_info: Optional[dict] = None,
) -> dict:
    """Start fetching *file* onto the shelf, and return its entry.

    Args:
        hub: The hub database holding the shelf tables.
        file: The model name the workflow asks for, as its loader spells it.
        on_arrived: Called with the new model id once it is on the shelf.
        comfyui_url: The owner's ComfyUI, whose templates are asked for a link
            when the committed list has none (:func:`live_download_source`).
        object_info: That ComfyUI's map, for the listing should it be read.

    Raises:
        DownloadRefused: Nothing was fetched; ``status_code`` says why.
    """
    source = download_source(file) or live_download_source(
        file, comfyui_url, object_info
    )
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
        # What a workflow or a set can name it by while it is still on its way:
        # the digest the list records (None for a gated link), the ComfyUI
        # folder it is for, and the set slot that folder fills.
        # Lowercased as every digest compared against it is.
        "sha256": (source.get("sha256") or "").lower() or None,
        "directory": source.get("directory"),
        "slot": directory_slot(source.get("directory")),
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
        args=(
            hub,
            entry,
            url,
            int(folder["id"]),
            relpath,
            on_arrived,
            source.get("sha256"),
            bool(source.get("live")),
        ),
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


def running_download(
    file: str, sha256: Optional[str] = None, folder: Optional[str] = None
) -> Optional[dict]:
    """The running download of *file* or of *sha256*, or None.

    *file* by its own name, case folded and without the loader's subfolder, as
    :func:`download_source` reads it; *sha256* where the list recorded one. A
    run that names a model still on its way is refused rather than submitted
    (``workflow_run_service.MODELS_DOWNLOADING``).

    *folder*, the ComfyUI folder the caller's loader reads, keeps a name match
    to a download of the same kind: a LoRA that shares its file name with a
    checkpoint being fetched is not on its way. Compared by the set slot each
    folder fills (:func:`directory_slot`), so ``unet`` and
    ``diffusion_models``, or ``clip`` and ``text_encoders``, are one kind. A
    digest names one file wherever it goes, so it is matched alone, and two
    known digests that differ are two files whatever their names.
    """
    name = os.path.basename((file or "").replace("\\", "/")).casefold()
    digest = (sha256 or "").strip().lower()
    kind = directory_slot(folder) if folder else None
    with _lock:
        for entry in _downloads.values():
            if entry["status"] != STATUS_RUNNING:
                continue
            if digest and entry.get("sha256") == digest:
                return _public(entry)
            same_kind = kind is None or entry.get("slot") in (None, kind)
            other_file = bool(digest and entry.get("sha256"))
            if (
                name
                and same_kind
                and not other_file
                and entry["file"].casefold() == name
            ):
                return _public(entry)
    return None


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
    expected: Optional[str] = None,
    live: bool = False,
) -> None:
    target = entry["_target"]
    partial = target + PARTIAL_SUFFIX
    cancel = entry["_cancel"]
    try:
        digest = _fetch(url, partial, entry, cancel, live=live)
        # The file the list names, or nothing: another file under the same
        # link would land on the shelf and leave a loader that asks for the
        # listed digest still missing, with nothing said.
        if expected and digest != expected:
            raise ValueError(
                f"{entry['file']} arrived, but it is not the file the template "
                f"list names (its SHA-256 is {digest}, the list says "
                f"{expected}), so it was removed again."
            )
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


def _left_https(url: str, response) -> bool:
    """Whether an https link was redirected onto plain http at any hop.

    The list's links are https. A plain hop in the middle of the chain lets
    anyone on that network point the rest of it somewhere else, so every hop
    counts, not only where it ended.
    """
    hops = [hop.url for hop in response.history] + [response.url]
    return url.startswith("https://") and not all(
        hop.startswith("https://") for hop in hops
    )


def _live_hop(url: str) -> bool:
    """Whether a live link may be asked at *url*: https on Hugging Face."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return (
        parsed.scheme == "https"
        and parsed.netloc == host
        and any(
            host == domain or host.endswith("." + domain) for domain in LIVE_HOP_DOMAINS
        )
    )


def _open(url: str, live: bool) -> requests.Response:
    """GET *url* as a stream; for a *live* link, redirects followed by hand,
    each hop's host checked (:func:`_live_hop`) before it is asked."""
    kwargs = {
        "stream": True,
        "timeout": _TIMEOUT_SECONDS,
        # Content-Length counts the bytes sent and requests decodes what it
        # is sent, so a compressed body would outgrow the space checked for it.
        "headers": {"User-Agent": "PixlStash", "Accept-Encoding": "identity"},
    }
    if not live:
        return requests.get(url, **kwargs)
    for _hop in range(_MAX_HOPS):
        if not _live_hop(url):
            raise ValueError(
                f"The link led to {urlparse(url).netloc or url}, which is not "
                "Hugging Face, so it was not followed."
            )
        response = requests.get(url, allow_redirects=False, **kwargs)
        if not response.is_redirect:
            return response
        url = urljoin(url, response.headers.get("Location", ""))
        response.close()
    raise ValueError(f"The link redirected more than {_MAX_HOPS} times.")


def _fetch(
    url: str, partial: str, entry: dict, cancel: threading.Event, live: bool = False
) -> str:
    """Stream *url* into *partial* and return the SHA-256 of what was written."""
    os.makedirs(os.path.dirname(partial), exist_ok=True)
    sha = hashlib.sha256()
    with _open(url, live) as response:
        response.raise_for_status()
        if _left_https(url, response):
            raise ValueError("The download was redirected away from https.")
        if response.headers.get("Content-Encoding", "identity") != "identity":
            raise ValueError(
                "The link sent the file compressed, so its size cannot be checked."
            )
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
