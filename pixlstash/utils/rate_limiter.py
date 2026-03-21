"""Simple global rate limiter for unauthenticated routes.

Applied only to paths that don't require a session — the same set defined in
``pixlstash.auth.AUTH_EXCLUDED_PATHS`` / ``AUTH_EXCLUDED_PREFIXES``.
Uses a sliding window — no per-IP tracking, no external dependencies.
"""

import threading
import time
from collections import deque
from typing import Callable

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from pixlstash.auth import AUTH_EXCLUDED_PATHS, AUTH_EXCLUDED_PREFIXES

_LIMIT = 120  # max requests
_WINDOW = 60  # per this many seconds


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Global sliding-window rate limiter for unauthenticated routes.

    Authenticated routes are left unrestricted — the session requirement
    already gates them. Only paths listed in ``_PUBLIC_PATHS`` are counted.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._window: deque[float] = deque()
        self._lock = threading.Lock()

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path

        is_public = path in AUTH_EXCLUDED_PATHS or any(
            path.startswith(p) for p in AUTH_EXCLUDED_PREFIXES
        )
        if not is_public:
            return await call_next(request)

        now = time.monotonic()
        with self._lock:
            cutoff = now - _WINDOW
            while self._window and self._window[0] < cutoff:
                self._window.popleft()
            if len(self._window) >= _LIMIT:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please slow down."},
                    headers={"Retry-After": str(_WINDOW)},
                )
            self._window.append(now)

        return await call_next(request)
