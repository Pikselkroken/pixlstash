"""Simple global rate limiter for unauthenticated routes.

Applied only to paths that don't require a session — the same set defined in
``pixlstash.auth.is_auth_excluded_path``.
Uses a sliding window — no per-IP tracking, no external dependencies.

The limit/window are configurable via server-config, and the whole limiter can
be disabled (``disable_rate_limit``) for trusted environments such as the
Playwright e2e backend, where a fast test suite would otherwise trip the
global cap on public-asset requests.
"""

import threading
import time
from collections import deque
from typing import Callable

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from pixlstash.auth import is_auth_excluded_path

_LIMIT = 120  # max requests
_WINDOW = 60  # per this many seconds


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Global sliding-window rate limiter for unauthenticated routes.

    Authenticated routes are left unrestricted — the session requirement
    already gates them. Only public (auth-excluded) paths are counted.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        enabled: bool = True,
        limit: int | None = None,
        window: int | None = None,
    ) -> None:
        super().__init__(app)
        self._enabled = enabled
        # None → fall back to the module defaults at dispatch time, so tests
        # that patch ``_LIMIT`` / ``_WINDOW`` keep working. server.py passes
        # explicit values sourced from server-config.
        self._limit = limit
        self._window_seconds = window
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    async def dispatch(self, request: Request, call_next: Callable):
        if not self._enabled:
            return await call_next(request)

        path = request.url.path

        is_public = is_auth_excluded_path(path)
        if not is_public:
            return await call_next(request)

        limit = _LIMIT if self._limit is None else self._limit
        window = _WINDOW if self._window_seconds is None else self._window_seconds

        now = time.monotonic()
        with self._lock:
            cutoff = now - window
            while self._events and self._events[0] < cutoff:
                self._events.popleft()
            if len(self._events) >= limit:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please slow down."},
                    headers={"Retry-After": str(window)},
                )
            self._events.append(now)

        return await call_next(request)
