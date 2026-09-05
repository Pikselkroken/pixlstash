"""HTTP routes for the operation log - history, undo and redo (DAM 1.2).

The log is append-only and vault-wide: it is the undo/redo stack today and the
audit log / activity feed later (DAM roadmap §4.3). Every route here is declared
``OWNER_ONLY`` in ``pixlstash/authz/registry.py`` - a resource-scoped share token
must never enumerate the owner's whole change history, and must never revert a
change. There is deliberately **no** authorization code in these handlers; the
authz gate owns it (``docs/backend_architecture.md`` §16.1).

Origin discipline (§15): each mutating route reads ``X-Client-Id`` off the
request via ``request.state.origin_client_id`` and hands it to the service
**explicitly**, which carries it in the WebSocket event ``data`` dict. Nothing
downstream reads the contextvar - it is dead on the DB worker thread and on the
broadcaster's loop.

See :mod:`pixlstash.services.operation_log_service` for the semantics.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from pixlstash.db_models.operation import VALID_STATUSES
from pixlstash.pixl_logging import get_logger
from pixlstash.services import operation_log_service
from pixlstash.services.operation_log_service import OperationLogError

logger = get_logger(__name__)


class OperationResponse(BaseModel):
    """One recorded operation as the API returns it."""

    model_config = ConfigDict(extra="allow")

    id: int
    batch_id: Optional[str] = None
    created_at: Optional[str] = None
    actor: Optional[str] = None
    op_type: str
    target_type: str
    target_ids: list[int] = []
    target_count: int = 0
    source: str = "external"
    origin_client_id: Optional[str] = None
    undoable: bool = False
    status: str
    undone_at: Optional[str] = None
    summary: Optional[str] = None


class UndoStateResponse(BaseModel):
    """What the Ctrl+Z / Ctrl+Shift+Z affordances should offer right now."""

    can_undo: bool
    can_redo: bool
    next_undo: Optional[OperationResponse] = None
    next_redo: Optional[OperationResponse] = None


class UndoResultResponse(BaseModel):
    """Which operations were reverted (or replayed) and what they touched."""

    model_config = ConfigDict(extra="allow")

    operations: list[OperationResponse] = []
    picture_ids: list[int] = []
    picture_count: int = 0
    # The scrapheap lifecycle subset of ``picture_ids``: pictures this call moved
    # INTO the scrapheap (they left the active grid) and pictures it brought back
    # OUT of it. Both are empty for a pure metadata undo.
    scrapheaped_picture_ids: list[int] = []
    restored_picture_ids: list[int] = []


class UndoRequest(BaseModel):
    """Optional body for ``POST /operations/undo``."""

    # Undo this specific operation instead of the newest reversible one. Its
    # whole batch goes with it - a bulk action is one undoable unit.
    operation_id: Optional[int] = None


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _origin(request: Request) -> Optional[str]:
        # Read at request time, on the request's own task, and passed explicitly
        # from here on (§15 threading caveat).
        return getattr(request.state, "origin_client_id", None)

    @router.get(
        "/operations",
        summary="List recorded operations (newest first)",
        description=(
            "Returns the append-only operation log, newest first: what changed, "
            "how many targets it touched, who did it, where it came from and "
            "whether it is still reversible. Filter with ``status`` "
            "(applied | undone | superseded), ``batch_id`` (all operations of "
            "one bulk action) or ``op_type``. The before/after payloads are "
            "omitted here - fetch a single operation to see them."
        ),
        response_model=list[OperationResponse],
    )
    def list_operations(
        request: Request,
        limit: int = 50,
        status: Optional[str] = None,
        batch_id: Optional[str] = None,
        op_type: Optional[str] = None,
    ):
        if status and status not in VALID_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"status must be one of: {', '.join(VALID_STATUSES)}",
            )
        if limit < 1:
            raise HTTPException(status_code=400, detail="limit must be >= 1")
        return operation_log_service.list_operations(
            server.vault,
            limit=limit,
            status=status,
            batch_id=batch_id,
            op_type=op_type,
        )

    # Registered before /operations/{operation_id} so the literal path is not
    # swallowed by the id template.
    @router.get(
        "/operations/undo-state",
        summary="What undo and redo would do next",
        description=(
            "Returns ``can_undo`` / ``can_redo`` plus the operation each would "
            "act on, so the UI can label and enable its undo affordances "
            "without fetching the whole log."
        ),
        response_model=UndoStateResponse,
    )
    def get_undo_state(request: Request):
        return operation_log_service.undo_state(server.vault)

    @router.get(
        "/operations/{operation_id}",
        summary="Get one operation including its before/after state",
        description=(
            "Returns a single operation with the full recorded ``before`` and "
            "``after`` metadata state of its targets - the payload undo and redo "
            "write back."
        ),
        response_model=OperationResponse,
    )
    def get_operation(request: Request, operation_id: int):
        operation = operation_log_service.get_operation(server.vault, operation_id)
        if operation is None:
            raise HTTPException(status_code=404, detail="Operation not found")
        return operation

    @router.post(
        "/operations/undo",
        summary="Undo the newest reversible operation",
        description=(
            "Restores the recorded *before* state of the newest still-applied, "
            "reversible operation - or of ``operation_id`` when the body names "
            "one. If the operation belongs to a batch (one bulk action), the "
            "**whole batch** is reverted, so a partially-undone bulk action "
            "cannot exist. 409 when there is nothing to undo or the named "
            "operation is not reversible; 423 when a locked picture set freezes "
            "one of the targets; 410 when undoing a move to the Scrapheap whose "
            "picture has since been permanently purged (retention sweep or Empty "
            "Scrapheap) - the whole request is refused, the operation stays "
            "applied, and nothing is written."
        ),
        response_model=UndoResultResponse,
    )
    def undo_operation(request: Request, payload: UndoRequest | None = None):
        try:
            return operation_log_service.undo(
                server.vault,
                operation_id=(payload.operation_id if payload else None),
                origin_client_id=_origin(request),
            )
        except OperationLogError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    # Registered before /operations/{operation_id}/undo: "batches" would
    # otherwise be matched against the int id template and 422.
    @router.post(
        "/operations/batches/{batch_id}/undo",
        summary="Undo one whole bulk action by its batch id",
        description=(
            "The single-call revert behind a bulk action's report ('Collapsed "
            "2,700 groups - Undo'). Reverts every still-applied, reversible "
            "operation carrying this ``batch_id``, newest first. 409 when the "
            "batch has nothing left to undo."
        ),
        response_model=UndoResultResponse,
    )
    def undo_batch(request: Request, batch_id: str):
        try:
            return operation_log_service.undo_batch(
                server.vault,
                batch_id,
                origin_client_id=_origin(request),
            )
        except OperationLogError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.post(
        "/operations/{operation_id}/undo",
        summary="Undo one specific operation (and its batch)",
        description=(
            "Same semantics as ``POST /operations/undo`` with a body, addressed "
            "by path instead. Undoing any member of a batch reverts the whole "
            "batch."
        ),
        response_model=UndoResultResponse,
    )
    def undo_specific_operation(request: Request, operation_id: int):
        try:
            return operation_log_service.undo(
                server.vault,
                operation_id=operation_id,
                origin_client_id=_origin(request),
            )
        except OperationLogError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.post(
        "/operations/redo",
        summary="Re-apply the most recently undone operation",
        description=(
            "Restores the recorded *after* state of the most recently undone "
            "operation (and its whole batch). Recording any new operation "
            "invalidates the redo stack, so redo only ever replays onto the "
            "history it was undone from. 409 when there is nothing to redo."
        ),
        response_model=UndoResultResponse,
    )
    def redo_operation(request: Request):
        try:
            return operation_log_service.redo(
                server.vault, origin_client_id=_origin(request)
            )
        except OperationLogError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    return router
