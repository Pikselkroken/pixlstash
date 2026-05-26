"""HTTP routes for checkpoint creation, listing, deletion, and restore/undo."""

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request

from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


def create_router(server) -> APIRouter:
    """Create the checkpoints APIRouter.

    Args:
        server: The Server instance providing ``vault`` and ``auth``.

    Returns:
        A fully configured ``APIRouter``.
    """
    router = APIRouter()

    # ------------------------------------------------------------------
    # GET /checkpoints
    # ------------------------------------------------------------------

    @router.get("/checkpoints")
    def list_checkpoints(request: Request):
        """Return all checkpoints ordered by creation date (newest first)."""
        checkpoints = server.vault.checkpoint_service.list_checkpoints()
        return [
            {
                "id": cp.id,
                "kind": cp.kind,
                "label": cp.label,
                "created_at": cp.created_at.isoformat(),
                "byte_size": cp.byte_size,
                "picture_count": cp.picture_count,
                "schema_version": cp.schema_version,
            }
            for cp in checkpoints
        ]

    # ------------------------------------------------------------------
    # POST /checkpoints
    # ------------------------------------------------------------------

    @router.post("/checkpoints", status_code=201)
    def create_checkpoint(
        request: Request,
        label: Optional[str] = Body(default=None, embed=True),
    ):
        """Create a manual checkpoint snapshot.

        Authentication is required.  Returns the new checkpoint record.
        """
        server.auth.require_user_id(request)
        try:
            cp = server.vault.checkpoint_service.create_checkpoint(
                kind="MANUAL", label=label
            )
        except Exception as exc:
            logger.error("Failed to create checkpoint: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "id": cp.id,
            "kind": cp.kind,
            "label": cp.label,
            "created_at": cp.created_at.isoformat(),
            "byte_size": cp.byte_size,
            "picture_count": cp.picture_count,
            "schema_version": cp.schema_version,
        }

    # ------------------------------------------------------------------
    # DELETE /checkpoints/{id}
    # ------------------------------------------------------------------

    @router.delete("/checkpoints/{checkpoint_id}", status_code=204)
    def delete_checkpoint(checkpoint_id: int, request: Request):
        """Delete a checkpoint and its snapshot files.

        Authentication is required.
        """
        server.auth.require_user_id(request)
        cp = server.vault.checkpoint_service.get_checkpoint(checkpoint_id)
        if cp is None:
            raise HTTPException(status_code=404, detail="Checkpoint not found.")
        try:
            server.vault.checkpoint_service.delete_checkpoint(checkpoint_id)
        except Exception as exc:
            logger.error(
                "Failed to delete checkpoint %d: %s", checkpoint_id, exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    # POST /checkpoints/{id}/restore  (full restore)
    # ------------------------------------------------------------------

    @router.post("/checkpoints/{checkpoint_id}/restore")
    def restore_checkpoint(
        checkpoint_id: int,
        request: Request,
        dry_run: bool = Body(default=False, embed=True),
    ):
        """Replace the live database with the given checkpoint snapshot.

        Authentication is required.  Returns a summary of the restore.
        """
        server.auth.require_user_id(request)
        server.vault.notify(EventType.RESTORE_STARTED, {"checkpoint_id": checkpoint_id})
        try:
            report = server.vault.restore_service.restore_full(
                checkpoint_id, dry_run=dry_run
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "Full restore of checkpoint %d failed: %s", checkpoint_id, exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "checkpoint_id": report.checkpoint_id,
            "resource_type": report.resource_type,
            "missing_files_count": report.missing_files_count,
            "upserted_count": report.upserted_count,
            "errors": report.errors,
            "dry_run": dry_run,
        }

    # ------------------------------------------------------------------
    # POST /checkpoints/{id}/restore/{resource_type}/{resource_id}
    # ------------------------------------------------------------------

    @router.post("/checkpoints/{checkpoint_id}/restore/{resource_type}/{resource_id}")
    def restore_resource(
        checkpoint_id: int,
        resource_type: str,
        resource_id: int,
        request: Request,
    ):
        """Restore a single resource from a checkpoint snapshot.

        ``resource_type`` must be one of ``picture``, ``picture_set``,
        ``project``, or ``character``.

        Authentication is required.
        """
        server.auth.require_user_id(request)
        server.vault.notify(
            EventType.RESTORE_STARTED,
            {
                "checkpoint_id": checkpoint_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
            },
        )
        try:
            report = server.vault.restore_service.restore_resource(
                checkpoint_id, resource_type, resource_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "Per-resource restore of checkpoint %d (%s/%s) failed: %s",
                checkpoint_id,
                resource_type,
                resource_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "checkpoint_id": report.checkpoint_id,
            "resource_type": report.resource_type,
            "resource_id": report.resource_id,
            "missing_files_count": report.missing_files_count,
            "upserted_count": report.upserted_count,
            "errors": report.errors,
        }

    # ------------------------------------------------------------------
    # POST /undo
    # ------------------------------------------------------------------

    @router.post("/undo")
    def undo(
        request: Request,
        checkpoint_id: Optional[int] = Body(default=None, embed=True),
    ):
        """Undo recent metadata changes.

        If ``checkpoint_id`` is provided, undo all changes back to that
        checkpoint (hybrid ChangeLog + snapshot strategy).  Otherwise undo
        only the most recent writer transaction.

        Authentication is required.
        """
        server.auth.require_user_id(request)
        try:
            if checkpoint_id is not None:
                report = server.vault.undo_service.undo_to_checkpoint(checkpoint_id)
            else:
                report = server.vault.undo_service.undo_last_transaction()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("Undo failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "reverted_txn_count": report.reverted_txn_count,
            "reverted_row_count": report.reverted_row_count,
            "errors": report.errors,
        }

    return router
