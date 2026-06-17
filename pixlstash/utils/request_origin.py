"""Per-request origin attribution for the real-time event envelope.

Each browser tab sends an opaque ``X-Client-Id`` header on every mutating
request. The backend echoes that id back on the WebSocket event it raises so
the originating tab can recognise the echo of its own change and update its
grid surgically instead of doing a full reload.

This module provides:

- ``OriginClientMiddleware`` — reads ``X-Client-Id`` (capped at
  ``MAX_CLIENT_ID_LENGTH`` characters; longer values are ignored), and stashes
  it on both ``request.state.origin_client_id`` and the module-level
  ``origin_client_id_var`` contextvar.
- ``origin_client_id_var`` — a contextvar that lets a handler read the origin
  *synchronously, in-request* without threading ``request`` through helpers.

IMPORTANT (load-bearing): the contextvar is only valid on the request's own
task. Emits that happen on detached executor / worker threads (import, plugin,
in-app ComfyUI) run where the contextvar is dead, so those call sites MUST
capture the origin synchronously at request entry and carry it explicitly in
the event ``data`` dict. The broadcaster never reads the contextvar.

Security: ``X-Client-Id`` is attacker-controllable and is used ONLY for
echo-matching, NEVER for authorization or scoping. It is length-capped and is
not logged at INFO.
"""

import contextvars
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

CLIENT_ID_HEADER = "X-Client-Id"
MAX_CLIENT_ID_LENGTH = 200

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


class OriginClientMiddleware(BaseHTTPMiddleware):
    """Capture the originating tab's ``X-Client-Id`` for the event envelope."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable):
        client_id = _sanitize_client_id(request.headers.get(CLIENT_ID_HEADER))
        request.state.origin_client_id = client_id
        token = origin_client_id_var.set(client_id)
        try:
            return await call_next(request)
        finally:
            origin_client_id_var.reset(token)
