"""Ask Civitai which base model a file was made for, by its SHA-256.

The one outbound call the model shelf makes, and only when the owner asks
(*Look up base model online…*). **Only the hash leaves the machine**: it is the
whole of the URL path, it is checked to be 64 lowercase hex digits before the
URL is built, and nothing else - no filename, no cookie, no key - is sent.
Redirects are refused, so the hash goes to civitai.com and nowhere else.

A miss (404, or a version with no ``baseModel``) is an answer. Anything that
says Civitai could not be asked - a timeout, a refused connection, 429, a 5xx,
a redirect, a body that is not JSON - raises :class:`LookupUnavailable`, so the
caller stops the batch instead of waiting out the same failure once per file.
"""

from __future__ import annotations

import re
import time
from typing import Optional

import requests

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

CIVITAI_BY_HASH = "https://civitai.com/api/v1/model-versions/by-hash/{sha256}"

#: Seconds per request. Short: the owner is waiting on the answer.
TIMEOUT_SECONDS = 10

#: Longer than any base model's name; a longer answer is not stored.
MAX_LABEL_LENGTH = 100

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class LookupUnavailable(Exception):
    """Civitai could not be asked; the batch should stop rather than retry."""


def lookup_base_model(sha256: str) -> Optional[str]:
    """Return Civitai's ``baseModel`` for the file with *sha256*, or ``None``.

    Raises:
        ValueError: *sha256* is not 64 lowercase hex digits. Checked before
            any request is built.
        LookupUnavailable: Civitai could not be asked (see the module docstring).
    """
    if not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
        raise ValueError("not a lowercase hex SHA-256")
    prefix = sha256[:12]
    started = time.monotonic()
    try:
        response = requests.get(
            CIVITAI_BY_HASH.format(sha256=sha256),
            timeout=TIMEOUT_SECONDS,
            allow_redirects=False,
            headers={"User-Agent": "PixlStash", "Accept": "application/json"},
        )
    except requests.RequestException as exc:
        logger.warning(
            "Civitai lookup for %s… failed after %.1fs: %s",
            prefix,
            time.monotonic() - started,
            exc,
        )
        raise LookupUnavailable(str(exc)) from exc
    elapsed = time.monotonic() - started
    status = response.status_code
    if status == 404:
        logger.info("Civitai has no file %s… (%.1fs).", prefix, elapsed)
        return None
    if status != 200:
        logger.warning(
            "Civitai lookup for %s… answered %d after %.1fs.", prefix, status, elapsed
        )
        raise LookupUnavailable(f"HTTP {status}")
    try:
        body = response.json()
    except ValueError as exc:
        logger.warning(
            "Civitai lookup for %s… answered 200 with a body that is not JSON (%.1fs).",
            prefix,
            elapsed,
        )
        raise LookupUnavailable("not JSON") from exc
    label = body.get("baseModel") if isinstance(body, dict) else None
    label = label.strip() if isinstance(label, str) else ""
    if not label or len(label) > MAX_LABEL_LENGTH:
        logger.info("Civitai knows %s… but names no usable base model.", prefix)
        return None
    return label
