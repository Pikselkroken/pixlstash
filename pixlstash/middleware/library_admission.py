"""Outermost ASGI admission fence for active-library reads."""

import json
import re

from starlette.routing import compile_path

from pixlstash.authz.policy import LibraryAccessMode
from pixlstash.authz.registry import ROUTE_POLICIES


class LibraryAdmissionMiddleware:
    def __init__(self, app, *, server):
        self.app = app
        self.server = server
        self._exact_modes = {
            (method, path): policy.library_access
            for (method, path), policy in ROUTE_POLICIES.items()
            if "{" not in path
        }
        self._modes = [
            (
                method,
                compile_path(path)[0],
                policy.library_access,
                len(re.sub(r"\{[^}]+\}", "", path)),
            )
            for (method, path), policy in ROUTE_POLICIES.items()
            if "{" in path
        ]
        # The SPA catch-all is declared near the public routes but must never
        # shadow a more-specific API template. Starlette resolves routes by
        # specificity/order; mirror that here by trying the longest literal
        # template first and the root catch-all last.
        self._modes.sort(key=lambda item: item[3], reverse=True)

    def _mode(self, scope) -> LibraryAccessMode:
        method = scope.get("method")
        path = scope.get("path", "")
        exact = self._exact_modes.get((method, path))
        if exact is not None:
            return exact
        for declared_method, regex, mode, literal_length in self._modes:
            # The frontend's root catch-all also regex-matches every unknown
            # API URL. Undeclared API work must retain the safe ACTIVE default;
            # it must never gain HUB_ONLY admission by falling through to SPA.
            if path.startswith("/api/") and literal_length <= 1:
                continue
            if declared_method == method and regex.match(path):
                return mode
        return LibraryAccessMode.ACTIVE_VAULT

    @staticmethod
    async def _reject(send, unavailable: bool) -> None:
        detail = (
            "PixlStash has no verified open library. Restart the server."
            if unavailable
            else "PixlStash is switching library. Try again in a moment."
        )
        body = json.dumps({"detail": detail}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", b"2"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        mode = self._mode(scope)
        if mode is not LibraryAccessMode.ACTIVE_VAULT:
            return await self.app(scope, receive, send)
        lease = self.server.library_coordinator.acquire_read()
        if lease is None:
            return await self._reject(
                send,
                self.server.library_coordinator.state.value == "unavailable",
            )
        scope.setdefault("state", {})["library_lease"] = lease
        try:
            return await self.app(scope, receive, send)
        finally:
            self.server.library_coordinator.release_read(lease)
