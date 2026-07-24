"""WebSocket broadcaster for PixlStash vault events.

Extracted verbatim from ``pixlstash.server`` (Phase 2, §4.1 of the backend
refactor). ``WsBroadcasterMixin`` fans the filtered subset of vault ``EventType``
values out to owner-level WebSocket clients, and owns the ``/ws/updates`` route.
``Server`` inherits the mixin so the original ``self.``-bound call sites (the
vault event listener, the route registration) are unchanged.

Load-bearing invariant (see docs/backend_architecture.md §15): the broadcaster
runs on ``self._ws_loop`` — a different task/thread than the request that emitted
the event — where the ``origin_client_id`` contextvar is **dead**. Every envelope
field (``source``, ``origin_client_id``, ``change_kind``, ``picture_ids``) is
therefore read from the event ``data`` dict ONLY, never from the contextvar. The
attribution-critical emitters capture origin synchronously at request entry and
carry it explicitly in ``data``. ``test_source_origin_read_from_data_only`` pins
this rule.
"""

import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect

from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.routes.pictures import clear_stats_cache

logger = get_logger(__name__)


_WS_SNAPSHOT_EVENT_TYPES = {
    EventType.SNAPSHOT_CREATED: "snapshot_created",
    EventType.SNAPSHOT_DELETED: "snapshot_deleted",
    EventType.RESTORE_STARTED: "restore_started",
    EventType.RESTORE_COMPLETED: "restore_completed",
    EventType.RESTORE_FAILED: "restore_failed",
}


class WsBroadcasterMixin:
    """WebSocket vault-event broadcasting for ``Server``."""

    def handle_vault_event(self, event_type: EventType, data=None):
        if event_type in (
            EventType.CHANGED_TAGS,
            EventType.CLEARED_TAGS,
            EventType.CHANGED_PICTURES,
            EventType.PICTURE_IMPORTED,
            EventType.QUALITY_UPDATED,
        ):
            clear_stats_cache()
        if not self._ws_loop:
            return
        coro = self._broadcast_ws_event(event_type, data)
        try:
            logger.debug("Got the following event from vault: %s", event_type)
            asyncio.run_coroutine_threadsafe(coro, self._ws_loop)
        except Exception as exc:
            logger.warning("Failed to dispatch websocket event: %s", exc)
            coro.close()  # prevent 'coroutine never awaited' ResourceWarning

    def _should_send_ws_update(self, event_type: EventType, filters: dict) -> bool:
        return (
            event_type
            in (
                EventType.CHANGED_PICTURES,
                EventType.PICTURE_IMPORTED,
                EventType.PLUGIN_PROGRESS,
                EventType.CHANGED_TAGS,
                EventType.CLEARED_TAGS,
                EventType.CHANGED_CHARACTERS,
                EventType.CHANGED_FACES,
                EventType.CHANGED_DESCRIPTIONS,
            )
            or event_type in _WS_SNAPSHOT_EVENT_TYPES
        )

    @staticmethod
    def _source_from(data) -> str:
        """Coarse origin class for the envelope. Defaults to ``"external"``.

        Reads from the ``data`` dict ONLY — the broadcaster runs on
        ``self._ws_loop`` (a different thread/task than the request), so the
        origin contextvar is dead here and must never be consulted.
        """
        if isinstance(data, dict):
            source = data.get("source")
            if source == "user":  # legacy value migrated to "ui"
                return "ui"
            if source in ("ui", "external"):
                return source
        return "external"

    @staticmethod
    def _origin_from(data) -> str | None:
        """Originating tab's ``X-Client-Id`` from the event ``data`` dict.

        Defaults to ``None`` (background/external work). Read from ``data`` only
        for the same threading reason as ``_source_from``.
        """
        if isinstance(data, dict):
            origin = data.get("origin_client_id")
            if isinstance(origin, str):
                return origin
        return None

    @staticmethod
    def _picture_ids_from(data) -> list:
        """Picture ids from either a bare collection or an envelope dict.

        Accepts a dict carrying ``picture_ids`` (or ``ids``) alongside the
        origin/source envelope fields, or a bare ``list``/``tuple``/``set``.
        """
        if isinstance(data, dict):
            ids = data.get("picture_ids")
            if ids is None:
                ids = data.get("ids")
            return list(ids) if ids else []
        if isinstance(data, (list, tuple, set)):
            return list(data)
        return []

    @staticmethod
    def _change_kind_from(data) -> str | None:
        """Optional ``added``/``updated``/``removed`` hint from ``data``."""
        if isinstance(data, dict):
            kind = data.get("change_kind")
            if kind in ("added", "updated", "removed"):
                return kind
        return None

    async def _broadcast_ws_event(self, event_type: EventType, data=None):
        with self._ws_clients_lock:
            clients = list(self._ws_clients)
        if not clients:
            return
        if event_type in (EventType.CHANGED_CHARACTERS, EventType.CHANGED_FACES):
            payload = {
                "type": "characters_changed",
                "event": event_type.name,
            }
        elif event_type == EventType.CHANGED_DESCRIPTIONS:
            picture_ids = self._picture_ids_from(data)
            payload = {
                "type": "descriptions_changed",
                "event": event_type.name,
                "picture_ids": picture_ids,
            }
        elif event_type in (EventType.CHANGED_TAGS, EventType.CLEARED_TAGS):
            picture_ids = self._picture_ids_from(data)
            payload = {
                "type": "tags_changed",
                "event": event_type.name,
                "picture_ids": picture_ids,
            }
        elif event_type == EventType.PICTURE_IMPORTED:
            if isinstance(data, dict):
                picture_ids = data.get("ids") or []
            else:
                picture_ids = data if isinstance(data, (list, tuple, set)) else []
            # ``source`` / ``origin_client_id`` / ``change_kind`` are added by
            # the uniform envelope below (read from ``data`` via the helpers).
            payload = {
                "type": "picture_imported",
                "event": event_type.name,
                "picture_ids": list(picture_ids),
            }
        elif event_type == EventType.PLUGIN_PROGRESS:
            progress_payload = data if isinstance(data, dict) else {}
            payload = {
                "type": "plugin_progress",
                "event": event_type.name,
                **progress_payload,
            }
        elif event_type in _WS_SNAPSHOT_EVENT_TYPES:
            info = data if isinstance(data, dict) else {}
            payload = {
                **info,
                "type": _WS_SNAPSHOT_EVENT_TYPES[event_type],
                "event": event_type.name,
            }
        else:
            # ``data`` may be a bare id collection, or a dict carrying both the
            # ids and the names of the fields that changed. The optional
            # ``fields`` list lets the SPA skip a grid reload when the changed
            # fields don't affect its current sort/filters (e.g. a background
            # ``smart_score`` recompute while sorting by date).
            fields = None
            if isinstance(data, dict):
                picture_ids = data.get("picture_ids") or []
                fields = data.get("fields")
            elif isinstance(data, (list, tuple, set)):
                picture_ids = data
            else:
                picture_ids = []
            payload = {
                "type": "pictures_changed",
                "event": event_type.name,
                "picture_ids": list(picture_ids) if picture_ids else [],
            }
            if fields:
                payload["fields"] = list(fields)
        # Uniform origin-aware envelope on EVERY event. ``source`` and
        # ``origin_client_id`` let the frontend recognise the echo of its own
        # change (origin id match) and apply a targeted grid update instead of a
        # full reload. ``change_kind`` is carried through when the emit site set
        # it. All three are read from ``data`` only (see ``_source_from``).
        payload["source"] = self._source_from(data)
        payload["origin_client_id"] = self._origin_from(data)
        change_kind = self._change_kind_from(data)
        if change_kind:
            payload["change_kind"] = change_kind
        stale = []
        for client in clients:
            ws = client.get("ws")
            if not ws:
                stale.append(client)
                continue
            # The global vault-activity stream is owner-level. A resource-scoped
            # / READ token authenticates the connection but is not entitled to
            # it, so it receives no events (we never deliver vault-wide activity
            # outside a token's grant). Per-resource scoped delivery, if ever
            # wanted, would be an additive change here.
            if not client.get("owner"):
                continue
            filters = client.get("filters") or {}
            if not self._should_send_ws_update(event_type, filters):
                continue
            try:
                logger.debug("Sending websocket event: %s", payload)
                await ws.send_json(payload)
            except Exception:
                stale.append(client)
        if stale:
            with self._ws_clients_lock:
                for client in stale:
                    if client in self._ws_clients:
                        self._ws_clients.remove(client)

    def register_ws_updates_route(self):
        """Register the ``/ws/updates`` WebSocket route on ``self.api``.

        Kept as a method (not an inline ``_setup_routes`` closure) so the
        broadcaster owns the full WS lifecycle. The route handler closes over
        ``self`` exactly as the original inline handler did.
        """
        # Local import avoids a server <-> ws.broadcaster import cycle
        from pixlstash.server import API_V1_PREFIX

        @self.api.websocket(f"{API_V1_PREFIX}/ws/updates")
        async def websocket_updates(websocket: WebSocket):
            # The HTTP auth middleware does not run for WebSocket connections,
            # so authenticate here BEFORE accepting. Without this, any reachable
            # client — including a cross-site page via CSWSH, since the browser
            # auto-attaches the session cookie to the handshake — could
            # subscribe to live vault activity.
            if not self.auth.is_websocket_origin_allowed(
                websocket, self.allow_origins, self.allow_origin_regex
            ):
                await websocket.close(code=1008)
                return
            ws_auth = self.auth.authenticate_websocket(websocket)
            if ws_auth is None:
                await websocket.close(code=1008)
                return
            await websocket.accept()
            # Always refresh _ws_loop so it tracks the currently-running event loop.
            # In production (uvicorn) this is always the same loop; in tests each
            # WebSocket session may run on a different loop than HTTP requests.
            self._ws_loop = asyncio.get_running_loop()
            # Only owner-level connections receive the global vault-activity
            # stream. A resource-scoped / READ token may connect (authenticated)
            # but is never sent events outside its grant — see
            # ``_broadcast_ws_event``.
            client = {
                "ws": websocket,
                "filters": {},
                "owner": ws_auth.is_owner,
                "client_id": None,
            }
            with self._ws_clients_lock:
                self._ws_clients.append(client)
            try:
                while True:
                    message = await websocket.receive_text()
                    if not message:
                        continue
                    try:
                        payload = json.loads(message)
                    except Exception as exc:
                        logger.debug(
                            "Ignoring unparseable websocket message (%s).", exc
                        )
                        continue
                    if payload.get("type") == "set_filters":
                        filters = {
                            "selected_character": payload.get("selected_character"),
                            "selected_set": payload.get("selected_set"),
                            "search_query": payload.get("search_query"),
                        }
                        client["filters"] = filters
                        # The originating tab's opaque client id. Stored for
                        # forward-looking server-side echo matching; the FE
                        # matches locally for v1. Echo-matching only — never
                        # used for authz/scoping. Cap length defensively.
                        raw_client_id = payload.get("client_id")
                        if isinstance(raw_client_id, str) and len(raw_client_id) <= 200:
                            client["client_id"] = raw_client_id
            except WebSocketDisconnect:
                logger.debug("WebSocket client disconnected normally.")
            finally:
                with self._ws_clients_lock:
                    if client in self._ws_clients:
                        self._ws_clients.remove(client)
