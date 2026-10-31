"""E2E-only test hooks.

This router exists **solely** for Playwright, and holds two hooks.

``ws-event`` deterministically injects a WebSocket grid event by driving
``vault.notify`` with a controlled payload. The e2e backend runs only the
folder sweeps (and a face finder that queues nothing) and loads no models, so the tag/quality/smart-score/face events
behind most "continuous refresh" and "pill-on-own-change" reports never fire on
their own. This endpoint reproduces those flood / echo scenarios on demand,
without real-timing nondeterminism.

``reference-folders/{folder_id}/rescan`` marks a reference folder changed on
disk, which is the filesystem watcher's job. The watcher needs an inotify
instance, and a machine running many processes may have none left to give it,
so a spec that changes a folder's files says so here instead of waiting for
the five-minute rescan.

Security posture (do not weaken):

* **Off by default, off in production.** The router is registered **only** when
  the server config flag ``enable_test_hooks`` is true (default ``False``).
  When the flag is false the route does not exist at all (404), not merely 403.
  Production never sets the flag; only ``frontend/e2e/serve_e2e_backend.py``
  does, for the hermetic test backend.
* **Owner-only, defense in depth.** Even when the flag is on, the handler calls
  ``auth.require_unscoped_owner`` - a full cookie session or an unscoped
  ALL-token. READ tokens and resource-scoped tokens are rejected (the auth
  middleware already blocks READ-token writes; the handler re-checks). The
  endpoint emits broadcast events but never reads or returns per-object
  resource data, so it requires no ``enforce_picture_scope`` chokepoint:
  scoped tokens can never reach it.

See ``docs/reviews/2026-06-grid-refresh-cleanup-plan.md`` §4 Phase 2 and
§6 decision 1.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from typing import Optional

import send2trash as send2trash_package
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from send2trash import send2trash

from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.resources.arbiter import Lease, LeaseKind, LeaseState, Tenant
from pixlstash.routes.model_folders import register_model_folder
from pixlstash.services import workflow_inbox
from pixlstash.services.model_folder_scanner import ModelFolderScanner

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
        description=(
            'Optional grid hint: "added", "updated", "removed", or "restored" '
            "(a scrapheap undo/restore - the card comes back, but the picture "
            "is not a new import)."
        ),
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


def seed_model_folder(server, path: str) -> None:
    """Register the e2e launcher's model folder and scan it synchronously.

    The e2e backend runs with background workers off, so the scan a new folder
    normally gets never runs and the shelf would stay empty. Called at start-up,
    only when ``enable_test_hooks`` is on. A failure is logged rather than
    raised: the shelf specs then fail on their own assertions, and every other
    spec still gets a server.
    """
    try:
        folder_id = register_model_folder(server, path, "user")
        result = ModelFolderScanner(server.hub).scan_folder(folder_id, path, "user")
    except Exception as exc:
        logger.error(
            "[test-hooks] could not seed the e2e model folder %s: %s",
            path,
            exc,
            exc_info=True,
        )
        return
    logger.info(
        "[test-hooks] seeded e2e model folder %s (id=%s): %d adapters, %d checkpoints",
        path,
        folder_id,
        result.adapters,
        result.checkpoints,
    )


def fake_training_card(server, card: dict) -> None:
    """Train on a pretend card instead of leasing this machine's GPU.

    *card* is ``{"vram_mb", "ram_mb"}``: what ``POST /training/jobs`` sizes a
    run against. A CI runner has no GPU, so the arbiter reads no card and the
    route refuses every run; a developer's machine has one, and an exclusive
    lease on it would make PixlStash yield its GPU work for a trainer that is
    ``tests/training/fake_run.py`` and uses none. The run's lease is granted at
    once, holds nothing and has no arbiter, so the Tasks tab lists no lease for
    it and its shrink and release do nothing. Called at start-up, only when
    ``enable_test_hooks`` is on.
    """
    vault = server.vault
    vault.training_room = lambda: dict(card)

    def lease(label: str, job_uuid: str, vram_mb: int, ram_mb: int) -> Lease:
        return Lease(
            tenant=Tenant.AITOOLKIT,
            label=label,
            kind=LeaseKind.EXCLUSIVE,
            device=None,
            need_mb=vram_mb,
            ram_mb=ram_mb,
            job_id=job_uuid,
            state=LeaseState.GRANTED,
        )

    vault.training_service._request_gpu = lease
    vault.training_service._adopt_gpu = lease
    logger.warning("[test-hooks] training runs on a pretend card: %s", card)


def isolate_e2e_data(path: str) -> None:
    """Keep the e2e backend's workflow folders and its trash under *path*.

    The workflow inbox and the user workflow folder are machine-global, so an
    e2e run would otherwise import whatever the owner's inbox holds, migrate
    their workflows, and write a deleted workflow into the inbox their own
    server watches. Every ``send2trash`` PixlStash calls moves the file into
    ``<path>/trash`` instead of the OS trash, the way the pytest suites'
    ``fake_trash`` does. Called by ``Server.__init__`` just before the
    hub's data steps read the user folder, and only when ``enable_test_hooks``
    is on.
    """
    workflow_inbox.DATA_ROOT = path
    trash = os.path.join(path, "trash")
    os.makedirs(trash, exist_ok=True)

    def fake_trash(target) -> None:
        # A folder per file, so two files of one name both keep theirs.
        shutil.move(os.fspath(target), tempfile.mkdtemp(dir=trash))

    # Every module that already bound the real one, not a list of them, and
    # the package itself, so a module imported later binds the fake too.
    send2trash_package.send2trash = fake_trash
    for name, module in list(sys.modules.items()):
        if name.startswith("pixlstash") and (
            getattr(module, "send2trash", None) is send2trash
        ):
            module.send2trash = fake_trash
    logger.warning("[test-hooks] workflow folders and trash moved under %s", path)


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

    @router.post(
        "/test-hooks/reference-folders/{folder_id}/rescan",
        summary="[E2E ONLY] Mark a reference folder changed on disk",
        description=(
            "Owner-only, e2e-only hook. Does what the filesystem watcher does "
            "when a file in the folder changes: marks the folder due, so the "
            "planner's reference-folder scan picks it up at once. The watcher "
            "itself needs an inotify instance, which a busy machine may not "
            "have to give. Registered only when ``enable_test_hooks`` is "
            "true; absent otherwise."
        ),
        include_in_schema=False,
    )
    def rescan_reference_folder(folder_id: int, request: Request) -> dict:
        server.auth.require_unscoped_owner(request)
        # The watcher's own callback, so the scan that follows is the real one.
        if not server.vault.mark_reference_folder_changed(folder_id):
            raise HTTPException(status_code=404, detail="Reference folder not found.")
        logger.info("[test-hooks] marked reference folder %s changed", folder_id)
        return {"status": "success", "folder_id": folder_id}

    return router
