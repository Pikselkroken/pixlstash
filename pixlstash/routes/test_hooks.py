"""E2E-only test hooks for the WebSocket grid-event system.

This router exists **solely** to let Playwright deterministically inject a
WebSocket grid event by driving ``vault.notify`` with a controlled payload.
The e2e backend boots with ``disable_background_workers: true``, so the
tag/quality/smart-score/face events behind most "continuous refresh" and
"pill-on-own-change" reports never fire on their own. This endpoint reproduces
those flood / echo scenarios on demand, without real-timing nondeterminism.

Security posture (do not weaken):

* **Off by default, off in production.** The router is registered **only** when
  the server config flag ``enable_test_hooks`` is true (default ``False``).
  When the flag is false the route does not exist at all (404), not merely 403.
  Production never sets the flag; only ``frontend/e2e/serve_e2e_backend.py``
  does, for the hermetic test backend.
* **Owner-only, defense in depth.** Even when the flag is on, the handler calls
  ``auth.require_unscoped_owner`` — a full cookie session or an unscoped
  ALL-token. READ tokens and resource-scoped tokens are rejected (the auth
  middleware already blocks READ-token writes; the handler re-checks). The
  endpoint emits broadcast events but never reads or returns per-object
  resource data, so it requires no ``enforce_picture_scope`` chokepoint:
  scoped tokens can never reach it.

See ``docs/reviews/2026-06-grid-refresh-cleanup-plan.md`` §4 Phase 2 and
§6 decision 1.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# The grid-driving event types a test may inject. Restricted to the events that
# actually flow to the grid (see ``server._broadcast_ws_event``); anything else
# is rejected so a test cannot fabricate snapshot/restore or progress traffic
# through this hook.
_ALLOWED_EVENT_TYPES: dict[str, EventType] = {
    "CHANGED_PICTURES": EventType.CHANGED_PICTURES,
    "PICTURE_IMPORTED": EventType.PICTURE_IMPORTED,
    "CHANGED_TAGS": EventType.CHANGED_TAGS,
    "CHANGED_DESCRIPTIONS": EventType.CHANGED_DESCRIPTIONS,
    "CHANGED_CHARACTERS": EventType.CHANGED_CHARACTERS,
    "CHANGED_FACES": EventType.CHANGED_FACES,
}

# Upper bound on ``repeat`` so a test cannot wedge the broadcaster with an
# unbounded loop. 500 comfortably covers the largest realistic flood
# (a per-id ``CHANGED_TAGS`` burst on a bulk tag-fix).
_MAX_REPEAT = 500


class InjectWsEventRequest(BaseModel):
    """Controlled payload for a single (or repeated) ``vault.notify`` call.

    The fields mirror the real WebSocket envelope so the broadcast path is
    byte-for-byte identical to a production emit site (see
    ``server._broadcast_ws_event``).
    """

    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(
        ...,
        description=(
            "Grid-driving event type to emit. One of: "
            "CHANGED_PICTURES, PICTURE_IMPORTED, CHANGED_TAGS, "
            "CHANGED_DESCRIPTIONS, CHANGED_CHARACTERS, CHANGED_FACES."
        ),
        examples=["CHANGED_PICTURES"],
    )
    picture_ids: list[int] = Field(
        default_factory=list,
        description=(
            "Picture ids the event concerns. Maps to the envelope "
            "``picture_ids`` (and ``ids`` for PICTURE_IMPORTED). Empty for "
            "character/face events that carry no ids."
        ),
        examples=[[1, 2, 3]],
    )
    source: Optional[str] = Field(
        default=None,
        description=(
            'Coarse origin class. "ui" marks the event as the current tab\'s '
            'own change; "external" (or omitted) marks it foreign. Used by the '
            "frontend echo-suppression to decide pill vs. silent reconcile."
        ),
        examples=["external"],
    )
    origin_client_id: Optional[str] = Field(
        default=None,
        description=(
            "Originating tab's X-Client-Id. When it matches the receiving "
            "tab's client id the frontend treats the event as its own echo. "
            "Leave null to simulate a foreign/background change."
        ),
        examples=["client-abc-123"],
    )
    change_kind: Optional[str] = Field(
        default=None,
        description='Optional grid hint: "added", "updated", or "removed".',
        examples=["updated"],
    )
    fields: Optional[list[str]] = Field(
        default=None,
        description=(
            "Optional list of changed field names. Lets the frontend skip a "
            "grid reload when the changed fields don't affect its current "
            "sort/filter (e.g. a background smart_score recompute)."
        ),
        examples=[["smart_score"]],
    )
    repeat: int = Field(
        default=1,
        ge=1,
        le=_MAX_REPEAT,
        description=(
            "How many times to fire the identical event, to simulate a flood / "
            f"per-id loop. Capped at {_MAX_REPEAT}."
        ),
        examples=[1],
    )


class InjectWsEventResponse(BaseModel):
    """Result of an injection: which event fired and how many times."""

    model_config = ConfigDict(extra="allow")

    status: str = Field(..., description='"success" on a completed injection.')
    event_type: str = Field(..., description="The event type that was emitted.")
    emitted: int = Field(
        ..., description="Number of ``vault.notify`` calls actually made."
    )


def create_router(server) -> APIRouter:
    """Build the e2e test-hooks router.

    The caller (``server._setup_routes``) only invokes this when
    ``enable_test_hooks`` is true, so simply being reachable already implies the
    flag is on. The owner check below is the second, independent gate.
    """
    router = APIRouter()

    @router.post(
        "/test-hooks/ws-event",
        summary="[E2E ONLY] Inject a WebSocket grid event",
        description=(
            "Owner-only, e2e-only hook. Calls ``vault.notify`` with a "
            "controlled payload so Playwright can deterministically reproduce "
            "grid floods and echo scenarios. Registered only when the server "
            "config flag ``enable_test_hooks`` is true; absent (404) otherwise."
        ),
        response_model=InjectWsEventResponse,
        include_in_schema=False,
    )
    def inject_ws_event(
        request: Request, payload: InjectWsEventRequest
    ) -> InjectWsEventResponse:
        # Defense in depth: full unscoped owner only. Rejects READ tokens and
        # resource-scoped tokens (the auth middleware already blocks READ-token
        # writes; this is the explicit second gate). 401/403 raised here.
        server.auth.require_unscoped_owner(request)

        event_type = _ALLOWED_EVENT_TYPES.get(payload.event_type)
        if event_type is None:
            allowed = ", ".join(sorted(_ALLOWED_EVENT_TYPES))
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported event_type {payload.event_type!r}. Allowed: {allowed}.",
            )

        # Build the envelope dict exactly as a real emit site would. Only set
        # the optional keys when provided so the broadcaster's defaults apply
        # (source -> "external", origin -> None) just like an untagged emit.
        data: dict = {"picture_ids": list(payload.picture_ids)}
        # PICTURE_IMPORTED reads ids from the ``ids`` key; mirror both so the
        # wire shape matches whichever path the broadcaster takes.
        if payload.event_type == "PICTURE_IMPORTED":
            data["ids"] = list(payload.picture_ids)
        if payload.source is not None:
            data["source"] = payload.source
        if payload.origin_client_id is not None:
            data["origin_client_id"] = payload.origin_client_id
        if payload.change_kind is not None:
            data["change_kind"] = payload.change_kind
        if payload.fields is not None:
            data["fields"] = list(payload.fields)

        logger.info(
            "[test-hooks] injecting %s x%d (picture_ids=%d, source=%r, origin=%r)",
            payload.event_type,
            payload.repeat,
            len(payload.picture_ids),
            payload.source,
            payload.origin_client_id,
        )

        emitted = 0
        for _ in range(payload.repeat):
            try:
                server.vault.notify(event_type, data)
                emitted += 1
            except Exception as exc:
                # vault.notify swallows individual listener failures itself, so
                # reaching here means a hard failure in the notify call. Surface
                # it with context rather than silently dropping the injection.
                logger.error(
                    "[test-hooks] vault.notify(%s) failed after %d/%d emits: %s",
                    payload.event_type,
                    emitted,
                    payload.repeat,
                    exc,
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to inject event after {emitted} emits: {exc}",
                ) from exc

        return InjectWsEventResponse(
            status="success",
            event_type=payload.event_type,
            emitted=emitted,
        )

    return router
