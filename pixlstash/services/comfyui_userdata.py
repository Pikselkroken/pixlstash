"""Reading the workflows a ComfyUI has saved, over its own userdata API (#1440).

The workflow library is otherwise fed by pictures and by files the owner hands
over, so a workflow that never made an imported picture is invisible - which on
a working install is most of them. ComfyUI's own frontend lists saved workflows
through ``/api/userdata``, and so does this, which is what makes it work against
a ComfyUI on another machine rather than only a folder on this one.

**Read only.** Nothing here writes, moves or deletes anything in ComfyUI, and
that is the reason the watched inbox is never pointed at ComfyUI's folder: the
inbox renames what it consumes and writes back on delete.

Two details of the endpoint that are easy to get wrong, both verified against
ComfyUI 0.30.0:

* **The file route is a single path segment.** ``/api/userdata/{file}`` matches
  ``workflows%2FName.json`` and 404s on ``workflows/Name.json``, and ComfyUI
  unquotes only when the string contains a ``%``. The whole relative path is
  therefore encoded with ``safe=""``.
* **The listing's ``path`` is relative to ``dir``**, so the ``workflows/`` prefix
  has to be put back before a file is read.

Every failure raises ``RuntimeError`` - the contract
:func:`~pixlstash.services.comfyui_recipe_service.fetch_object_info` has - so a
caller degrades rather than fails.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import requests

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# Per socket read, as `requests` applies it.
USERDATA_TIMEOUT_S = 15.0

# For the whole of one request, body included: the per-read timeout alone lets
# a server that trickles bytes hold the pull open indefinitely.
USERDATA_DEADLINE_S = 60.0

# The listing and the users probe are small; a listing this large is not one.
MAX_LISTING_BYTES = 16 * 1024 * 1024

# Far past any real install (the one measured for #1440 held 82), and a bound
# on how many files one pull can write from a listing it does not control.
MAX_LISTED_WORKFLOWS = 5000

# The folder, under ComfyUI's per-user directory, its frontend saves into.
WORKFLOWS_DIR = "workflows"

# The largest saved workflow this reads, matching the loader's own cap on a
# stored file (``routes/comfyui.py::MAX_WORKFLOW_FILE_BYTES``): a document this
# refuses to fetch is one the Workflows screen would refuse to parse anyway.
MAX_SAVED_WORKFLOW_BYTES = 32 * 1024 * 1024


# How many of ComfyUI's most recent runs one pull reads from ``/history`` (#1518).
# Every entry carries its whole graph, so this bounds the download; ComfyUI
# keeps up to 10000 and forgets them all on restart, and each pull adds what it
# read to the hub, so repeated pulls accumulate past this.
MAX_HISTORY_ITEMS = 500

# The history body, which is a graph per entry: far past 500 real graphs.
MAX_HISTORY_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class SavedWorkflow:
    """One entry of ComfyUI's workflow listing.

    Attributes:
        path: Relative to the workflows folder, verbatim from the listing and
            always ``/``-separated (``Sub/Name.json``).
        size: Bytes, as ComfyUI reports them; ``None`` when not reported.
        modified_ms: ComfyUI's modification time in milliseconds. The remote
            machine's clock, so a hint and never an identity.
    """

    path: str
    size: Optional[int]
    modified_ms: Optional[int]


class MultiUserComfyUIError(RuntimeError):
    """ComfyUI runs with ``--multi-user``, so there is no one set of workflows.

    Refused rather than quietly reading the default user's folder: the owner
    would get somebody's workflows and no sign that they were not theirs. The
    ComfyUI-PixlStash node pack refuses multi-user for the same reason.
    """


def saved_workflow_url(base_url: str, relative_path: str) -> str:
    """The URL that reads one saved workflow.

    Args:
        base_url: The ComfyUI base URL, without a trailing slash.
        relative_path: A listing ``path``, relative to the workflows folder.

    Returns:
        ``{base}/api/userdata/workflows%2F<path>`` with every ``/`` encoded.
    """
    return (
        f"{base_url}/api/userdata/{quote(f'{WORKFLOWS_DIR}/{relative_path}', safe='')}"
    )


def ensure_single_user(base_url: str) -> None:
    """Refuse a ComfyUI that runs with ``--multi-user``.

    ``GET /api/users`` answers with a ``users`` key only when multi-user is
    on. A ComfyUI too old to have the route (404) has no multi-user mode to
    worry about, so that is not a refusal.

    Raises:
        MultiUserComfyUIError: ComfyUI keeps a set of workflows per user.
        RuntimeError: ComfyUI could not be asked.
    """
    url = f"{base_url}/api/users"
    status, body = _get(url, "users", MAX_LISTING_BYTES)
    if status == 404:
        return
    payload = _json(status, body, url, "users")
    if isinstance(payload, dict) and "users" in payload:
        logger.warning(
            "ComfyUI at %s runs with --multi-user; refusing to read its saved "
            "workflows, which would be the default user's and nobody's in "
            "particular.",
            base_url,
        )
        raise MultiUserComfyUIError(
            "ComfyUI runs with --multi-user, so it has no single set of saved "
            "workflows to read."
        )


def list_saved_workflows(base_url: str) -> list[SavedWorkflow]:
    """Every workflow ComfyUI has saved, subfolders included.

    Args:
        base_url: The ComfyUI base URL, without a trailing slash.

    Returns:
        One :class:`SavedWorkflow` per ``.json`` file, sorted by path. An
        install that has never saved a workflow has no folder, which ComfyUI
        answers with 404: that is an empty list, not a failure.

    Raises:
        RuntimeError: ComfyUI is unreachable, answers with something that is
            not a listing, or lists more than :data:`MAX_LISTED_WORKFLOWS`.
    """
    url = f"{base_url}/api/userdata?dir={WORKFLOWS_DIR}&recurse=true&full_info=true"
    status, body = _get(url, "the workflow listing", MAX_LISTING_BYTES)
    if status == 404:
        return []
    payload = _json(status, body, url, "the workflow listing")
    if not isinstance(payload, list):
        logger.warning(
            "ComfyUI's workflow listing at %s is a %s, expected a list",
            url,
            type(payload).__name__,
        )
        raise RuntimeError("ComfyUI returned an unexpected workflow listing")
    if len(payload) > MAX_LISTED_WORKFLOWS:
        logger.warning(
            "ComfyUI at %s lists %d saved workflows, past the %d a pull takes.",
            base_url,
            len(payload),
            MAX_LISTED_WORKFLOWS,
        )
        raise RuntimeError(
            f"ComfyUI lists {len(payload)} saved workflows, more than the "
            f"{MAX_LISTED_WORKFLOWS} a pull takes"
        )
    entries = []
    for item in payload:
        entry = _listing_entry(item)
        if entry is None:
            logger.info("Skipped a ComfyUI workflow listing entry: %r", item)
            continue
        entries.append(entry)
    return sorted(entries, key=lambda entry: entry.path)


def read_saved_workflow(base_url: str, relative_path: str) -> dict:
    """The parsed document of one saved workflow.

    Args:
        base_url: The ComfyUI base URL, without a trailing slash.
        relative_path: A listing ``path``, relative to the workflows folder.

    Raises:
        RuntimeError: ComfyUI is unreachable, answers non-200, or the file is
            too large, not JSON, or not a JSON object.
    """
    url = saved_workflow_url(base_url, relative_path)
    status, body = _get(url, relative_path, MAX_SAVED_WORKFLOW_BYTES)
    payload = _json(status, body, url, relative_path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{relative_path} is not a JSON object")
    return payload


def read_history(base_url: str) -> dict:
    """ComfyUI's most recent runs, as ``GET /history`` answers them.

    At most :data:`MAX_HISTORY_ITEMS`, newest last, keyed by prompt id. Each
    entry's ``prompt[2]`` is the API graph that ran and ``status`` says whether
    it finished.

    Raises:
        RuntimeError: ComfyUI is unreachable, has no history route, or answers
            with something that is not a mapping.
    """
    url = f"{base_url}/history?max_items={MAX_HISTORY_ITEMS}"
    status, body = _get(url, "the run history", MAX_HISTORY_BYTES)
    payload = _json(status, body, url, "run history")
    if not isinstance(payload, dict):
        raise RuntimeError("ComfyUI returned an unexpected run history")
    return payload


def _listing_entry(item) -> Optional[SavedWorkflow]:
    """One listing item as a :class:`SavedWorkflow`, or ``None`` if unusable.

    ``full_info=true`` answers with objects; an older ComfyUI answers with bare
    path strings, which carry no size or time and are still readable.
    """
    if isinstance(item, str):
        path, size, modified = item, None, None
    elif isinstance(item, dict) and isinstance(item.get("path"), str):
        path = item["path"]
        size = _int_or_none(item.get("size"))
        modified = _int_or_none(item.get("modified"))
    else:
        return None
    # ComfyUI builds the path with os.path.relpath, so a Windows host answers
    # with backslashes. The listing is ours to normalise; the file route takes
    # either once encoded.
    path = path.replace("\\", "/")
    if not path.lower().endswith(".json"):
        return None
    return SavedWorkflow(path=path, size=size, modified_ms=modified)


def _int_or_none(value) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _get(url: str, what: str, max_bytes: int) -> tuple[int, bytes]:
    """GET *url* as ``(status, body)``; raise ``RuntimeError`` rather than wait.

    Streamed with a running byte count and a whole-request deadline, because
    ``timeout`` bounds each socket read and not the download: a ComfyUI that
    trickles bytes would otherwise hold the pull, and its in-flight gate,
    forever. Redirects are refused rather than followed to another host.
    A 404 is returned for the caller to read; any other status past 299 raises.
    """
    deadline = time.monotonic() + USERDATA_DEADLINE_S
    try:
        response = requests.get(
            url, timeout=USERDATA_TIMEOUT_S, stream=True, allow_redirects=False
        )
    except requests.RequestException as exc:
        logger.warning("ComfyUI request for %s failed (%s): %s", what, url, exc)
        raise RuntimeError(f"Could not reach ComfyUI for {what}") from exc
    try:
        status = response.status_code
        if 300 <= status < 400:
            logger.warning(
                "ComfyUI redirected the request for %s (%s, status %s); not followed.",
                what,
                url,
                status,
            )
            raise RuntimeError(f"ComfyUI redirected the request for {what}")
        body = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                body += chunk
                if len(body) > max_bytes:
                    raise RuntimeError(
                        f"{what} is past the {max_bytes} bytes this reads"
                    )
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"ComfyUI took longer than {USERDATA_DEADLINE_S:.0f} s "
                        f"to send {what}"
                    )
        except requests.RequestException as exc:
            logger.warning("ComfyUI stopped sending %s (%s): %s", what, url, exc)
            raise RuntimeError(f"ComfyUI stopped sending {what}") from exc
    finally:
        response.close()
    if status >= 300 and status != 404:
        logger.warning(
            "ComfyUI request for %s failed: url=%s status=%s detail=%s",
            what,
            url,
            status,
            bytes(body[:200]).decode("utf-8", "replace").strip(),
        )
        raise RuntimeError(f"ComfyUI answered {status} for {what}")
    return status, bytes(body)


def _json(status: int, body: bytes, url: str, what: str):
    if status == 404:
        raise RuntimeError(f"ComfyUI has no {what}")
    try:
        return json.loads(body)
    except ValueError as exc:
        logger.warning("ComfyUI returned invalid JSON for %s from %s", what, url)
        raise RuntimeError(f"ComfyUI returned invalid JSON for {what}") from exc
