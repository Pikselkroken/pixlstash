"""Per-request client correlation headers (origin tab, gesture batch).

Each browser tab sends an opaque ``X-Client-Id`` header on every mutating
request. The backend echoes that id back on the WebSocket event it raises so
the originating tab can recognise the echo of its own change and update its
grid surgically instead of doing a full reload.

A client may additionally send ``X-Operation-Batch-Id`` on the requests of one
user gesture that fans out into several mutations (deleting a tag chip issues a
``tags/remove_all`` **and** a ``tag_predictions/{tag}/reject``). The header
becomes the recorded operations' ``batch_id``, so the whole gesture is one undo
unit (docs/backend_architecture.md §21.2).

This module provides:

- ``OriginClientMiddleware`` - reads ``X-Client-Id`` (capped at
  ``MAX_CLIENT_ID_LENGTH`` characters; longer values are ignored), and stashes
  it on both ``request.state.origin_client_id`` and the module-level
  ``origin_client_id_var`` contextvar. It also reads
  ``X-Operation-Batch-Id`` into ``request.state.operation_batch_id``.
- ``origin_client_id_var`` - a contextvar that lets a handler read the origin
  *synchronously, in-request* without threading ``request`` through helpers.
- ``sanitize_operation_batch_id`` / ``require_client_batch_id``: the one place
  the ``cli-`` batch-id contract is enforced, for the ambient header (drop a bad
  value) and for a deliberately-named request body field (``400`` on a bad
  value) respectively.

IMPORTANT (load-bearing): the contextvar is only valid on the request's own
task. Emits that happen on detached executor / worker threads (import, plugin,
in-app ComfyUI) run where the contextvar is dead, so those call sites MUST
capture the origin synchronously at request entry and carry it explicitly in
the event ``data`` dict. The broadcaster never reads the contextvar.

Security: ``X-Client-Id`` is attacker-controllable and is used ONLY for
echo-matching, NEVER for authorization or scoping. It is length-capped and is
not logged at INFO. ``X-Operation-Batch-Id`` is likewise attacker-controllable
and is only a *grouping hint* over the caller's own operations - grouping never
widens what an operation may touch, and the whole ``/operations*`` surface is
OWNER_ONLY, so a batch id can only ever regroup the owner's own history. It is
strictly validated (namespaced ``cli-`` prefix, bounded length, safe charset)
so a client can never mint an id in the *server's* ``srv-`` namespace and graft
its requests onto a server-created batch.
"""

import contextvars
import logging
import re
from typing import Callable

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

CLIENT_ID_HEADER = "X-Client-Id"
MAX_CLIENT_ID_LENGTH = 200

# One user gesture, one undo unit: the client stamps every request of a
# compound gesture with the same id and the recorder stores it as ``batch_id``.
OPERATION_BATCH_ID_HEADER = "X-Operation-Batch-Id"
MAX_OPERATION_BATCH_ID_LENGTH = 80
# Client ids live in their own namespace. ``operation_log_service.new_batch_id``
# mints ``srv-…``; nothing a client sends can match that pattern, so a caller
# cannot attach its requests to a server-minted batch (and a future reader can
# tell the two apart in the log).
CLIENT_BATCH_ID_PREFIX = "cli-"
_CLIENT_BATCH_ID_RE = re.compile(r"^cli-[A-Za-z0-9_-]{4,76}$")

origin_client_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "origin_client_id", default=None
)


def _sanitize_client_id(raw: str | None) -> str | None:
    """Return a usable client id, or ``None`` if absent/oversized.

    The header is opaque and attacker-controllable, so we only validate length
    (longer than ``MAX_CLIENT_ID_LENGTH`` is dropped rather than truncated, so a
    crafted long value can never collide with a legitimate short one).
    """
    if not raw:
        return None
    if len(raw) > MAX_CLIENT_ID_LENGTH:
        return None
    return raw


def sanitize_operation_batch_id(raw: str | None) -> str | None:
    """Return a usable client gesture-batch id, or ``None`` when unusable.

    The header is attacker-controllable, so a malformed value is *ignored* (a
    header must never 500) and the operation is simply recorded unbatched, the
    behaviour every caller had before the header existed.

    Accepted: ``cli-`` followed by 4–76 characters of ``[A-Za-z0-9_-]``. The
    prefix is the namespace guard against the server's own ``srv-`` ids; the
    charset keeps the value safe to log and to put in a URL path
    (``/operations/batches/{batch_id}/undo``).

    Args:
        raw: The raw ``X-Operation-Batch-Id`` header value, or ``None``.

    Returns:
        The validated id, or ``None`` when absent, oversized or malformed.
    """
    if not raw:
        return None
    if len(raw) > MAX_OPERATION_BATCH_ID_LENGTH or not _CLIENT_BATCH_ID_RE.fullmatch(
        raw
    ):
        logger.debug(
            "Ignoring malformed %s header (length=%d, prefix=%r); the operation "
            "will be recorded unbatched",
            OPERATION_BATCH_ID_HEADER,
            len(raw),
            raw[:8],
        )
        return None
    return raw


def require_client_batch_id(raw: str | None) -> str | None:
    """Return a validated **body** batch id, or raise ``400``.

    The strict counterpart to :func:`sanitize_operation_batch_id`, and the only
    other place the ``cli-`` contract is enforced. Both apply the same pattern;
    they differ in **disposition**, deliberately:

    * a malformed ``X-Operation-Batch-Id`` *header* is ambient, so it is dropped
      and the operation records unbatched (a header must never 500);
    * a malformed ``batch_id`` *body field* was named deliberately by the client,
      so silently ignoring it would mis-group its undo. That is a ``400``.

    Validating it is not cosmetic. Taken verbatim, a client can submit an
    ``srv-…`` id so its rows read as a server batch, or graft them into an
    existing batch so one ``Ctrl+Z`` reverses more than the user did; and an
    unbounded string reaches the operation log and the
    ``/operations/batches/{batch_id}/undo`` path. Every route that accepts a
    body ``batch_id`` must call this. It lives here rather than in a route module
    so the next such route cannot ship with its own (or no) copy, which is
    exactly how ``POST /stacks/keep-cover-only`` shipped taking the field
    verbatim while its ``dedup.py`` siblings validated it.

    Args:
        raw: The client-supplied ``batch_id`` body field, or ``None``.

    Returns:
        The validated id, or ``None`` when absent (the server mints one).

    Raises:
        HTTPException: ``400`` when the value is present and not a well-formed
            client-namespaced id.
    """
    if raw is None:
        return None
    text = str(raw)
    if len(text) > MAX_OPERATION_BATCH_ID_LENGTH or not _CLIENT_BATCH_ID_RE.fullmatch(
        text
    ):
        logger.info("Rejected client batch_id (length=%d) %r", len(text), text[:120])
        raise HTTPException(
            status_code=400,
            detail=(
                f"batch_id must match {CLIENT_BATCH_ID_PREFIX}<4-76 chars of "
                "A-Z a-z 0-9 _ ->; omit it to have the server mint one"
            ),
        )
    return text


class OriginClientMiddleware(BaseHTTPMiddleware):
    """Capture the caller's ``X-Client-Id`` / ``X-Operation-Batch-Id``."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable):
        client_id = _sanitize_client_id(request.headers.get(CLIENT_ID_HEADER))
        request.state.origin_client_id = client_id
        request.state.operation_batch_id = sanitize_operation_batch_id(
            request.headers.get(OPERATION_BATCH_ID_HEADER)
        )
        token = origin_client_id_var.set(client_id)
        try:
            return await call_next(request)
        finally:
            origin_client_id_var.reset(token)
