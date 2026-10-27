"""Newer training recipes from pixlstash.dev, so a recipe can be corrected
without shipping PixlStash.

The site publishes ``training-presets/v1/index.json`` (id, version, sha256 and
``min_pixlstash`` per recipe) and the recipe files beside it, generated from
``pixlstash/data/training-presets/`` by :func:`site_files`
(``scripts/build_training_presets_site.py``; a guardrail test fails when the
committed copy drifts).

The backend fetches, never the browser, and only when the owner presses the
Train dialog's check button or has turned on the automatic check (at most once
a day). A downloaded file is kept, in :func:`pixlstash.training.presets.cache_dir`
beside the hub, only when its version is higher than the one in use, its sha256
matches the index, ``min_pixlstash`` is met and it passes
:func:`pixlstash.training.presets.validate_update`. Anything else is logged and
ignored.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from importlib.metadata import PackageNotFoundError, version as package_version

import requests
import urllib3

from pixlstash.pixl_logging import get_logger
from pixlstash.training import presets

logger = get_logger(__name__)

SITE_URL = "https://pixlstash.dev/training-presets/v1"
TIMEOUT_SECONDS = 3
MAX_BYTES = 1024 * 1024
AUTO_INTERVAL_SECONDS = 24 * 60 * 60
# The oldest PixlStash that reads the published files. Raise it when the recipe
# schema changes in a way an older app would misread.
MIN_PIXLSTASH = "1.13.0"
_STAMP = "last-check"


class FetchError(RuntimeError):
    """pixlstash.dev did not answer with what was asked for."""


def site_files() -> dict[str, bytes]:
    """The published tree, by file name: ``index.json`` and each shipped recipe.

    A recipe is published byte for byte, so its sha256 is the shipped file's.
    """
    entries = []
    files: dict[str, bytes] = {}
    for pid, preset in sorted(presets.shipped_presets().items()):
        name = f"{pid}.json"
        with open(os.path.join(presets.PRESET_DIR, name), "rb") as handle:
            raw = handle.read()
        files[name] = raw
        entries.append(
            {
                "id": pid,
                "version": preset["version"],
                "file": name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "min_pixlstash": MIN_PIXLSTASH,
            }
        )
    index = {"schema": 1, "presets": entries}
    files["index.json"] = (json.dumps(index, indent=2) + "\n").encode("utf-8")
    return files


def check(*, automatic: bool = False) -> dict:
    """Fetch the index and keep each recipe that may replace the one in use.

    Args:
        automatic: The opt-in daily check: skipped when the last check, of
            either kind, was under :data:`AUTO_INTERVAL_SECONDS` ago.

    Returns:
        ``{"checked", "updated", "error"}``: ``checked`` is False when an
        automatic check was not due; ``updated`` lists ``{id, label, version}``
        for each recipe replaced; ``error`` is a sentence when the index could
        not be read, else None.
    """
    cache = presets.cache_dir()
    if cache is None:
        raise RuntimeError("No recipe cache directory is set.")
    stamp = os.path.join(cache, _STAMP)
    if (
        automatic
        and os.path.exists(stamp)
        and time.time() - os.path.getmtime(stamp) < AUTO_INTERVAL_SECONDS
    ):
        return {"checked": False, "updated": [], "error": None}
    os.makedirs(cache, exist_ok=True)
    # Stamped before the request: a failing check is the one that must not
    # repeat on every dialog open.
    with open(stamp, "w", encoding="utf-8") as handle:
        handle.write(str(int(time.time())))
    try:
        index = _loads(_fetch("index.json"))
        entries = index["presets"]
        if not isinstance(entries, list):
            raise ValueError("'presets' is not a list")
    except (FetchError, ValueError, KeyError, TypeError, RecursionError) as exc:
        logger.warning(
            "Recipe update check: could not read %s/index.json: %s", SITE_URL, exc
        )
        return {
            "checked": True,
            "updated": [],
            "error": "Could not read the recipe list from pixlstash.dev.",
        }
    updated = []
    seen = set()
    for entry in entries:
        pid = entry.get("id") if isinstance(entry, dict) else None
        if pid in seen:
            logger.warning("Recipe update check: ignoring a second entry for %r", pid)
            continue
        seen.add(pid)
        try:
            preset = _take(entry, cache)
        except (
            FetchError,
            ValueError,
            KeyError,
            TypeError,
            OSError,
            RecursionError,
        ) as exc:
            logger.warning("Recipe update check: ignoring %r: %s", entry, exc)
            continue
        if preset is not None:
            updated.append(
                {
                    "id": preset["id"],
                    "label": preset["label"],
                    "version": preset["version"],
                }
            )
    if updated:
        presets.reload()
    logger.info(
        "Recipe update check: %d recipe(s) updated: %s",
        len(updated),
        ", ".join(f"{u['id']}@{u['version']}" for u in updated) or "none",
    )
    return {"checked": True, "updated": updated, "error": None}


def installed_version() -> str | None:
    """This PixlStash's version, or None when it is not installed as a package."""
    try:
        return package_version("pixlstash")
    except PackageNotFoundError:
        return None


def _version_tuple(value) -> tuple[int, int, int]:
    parts = re.findall(r"\d+", str(value))[:3]
    if len(parts) < 3:
        raise ValueError(f"not a version: {value!r}")
    return tuple(int(p) for p in parts)


def _take(entry: dict, cache: str) -> dict | None:
    """Download and keep the recipe *entry* names, when it is an update."""
    pid = entry["id"]
    shipped = presets.shipped_presets().get(pid)
    if shipped is None:
        raise ValueError(f"{pid!r} is not a recipe this PixlStash ships")
    version = entry["version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError(f"version {version!r} is not a whole number")
    if version <= presets.get_preset(pid)["version"]:
        return None
    installed = installed_version()
    if installed is None or _version_tuple(installed) < _version_tuple(
        entry["min_pixlstash"]
    ):
        raise ValueError(
            f"version {version} needs PixlStash {entry['min_pixlstash']}; "
            f"this is {installed}"
        )
    # The URL is built from a shipped id, never from the index's file name.
    raw = _fetch(f"{pid}.json")
    if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise ValueError("its sha256 does not match the index")
    preset = presets.validate_update(_loads(raw), shipped)
    if preset["version"] != version:
        raise ValueError(
            f"the file is version {preset['version']}, the index says {version}"
        )
    fd, tmp = tempfile.mkstemp(dir=cache, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        os.replace(tmp, os.path.join(cache, f"{pid}.json"))
    except OSError:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return preset


def _fetch(name: str) -> bytes:
    """GET ``SITE_URL/name``: no redirects, :data:`MAX_BYTES` at most, and
    :data:`TIMEOUT_SECONDS` in all, however slowly the bytes arrive.
    """
    url = f"{SITE_URL}/{name}"
    deadline = time.monotonic() + TIMEOUT_SECONDS
    try:
        with requests.get(
            url,
            timeout=TIMEOUT_SECONDS,
            allow_redirects=False,
            stream=True,
            # Undecoded bytes, so the size cap and the sha256 are on what was sent.
            headers={"Accept-Encoding": "identity"},
        ) as response:
            if response.status_code != 200:
                raise FetchError(f"{url} answered {response.status_code}")
            body = b""
            while True:
                if time.monotonic() > deadline:
                    raise FetchError(f"{url} took longer than {TIMEOUT_SECONDS} s")
                # One recv at most, so a trickle cannot hold past the deadline.
                chunk = response.raw.read1(64 * 1024)
                if not chunk:
                    return body
                body += chunk
                if len(body) > MAX_BYTES:
                    raise FetchError(f"{url} is larger than {MAX_BYTES} bytes")
    except (requests.RequestException, urllib3.exceptions.HTTPError, OSError) as exc:
        raise FetchError(f"{url}: {exc}") from exc


def _loads(raw: bytes):
    """JSON without NaN or Infinity, which no recipe value may be."""

    def refuse(token):
        raise ValueError(f"{token} is not allowed")

    return json.loads(raw.decode("utf-8"), parse_constant=refuse)
