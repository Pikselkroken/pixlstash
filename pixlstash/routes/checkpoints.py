"""HTTP routes for checkpoint creation, listing, deletion, and restore/undo."""

from typing import List, Optional

from fastapi import APIRouter, Body, HTTPException, Request

from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# Maximum number of resources returned in preview responses.
_PREVIEW_RESOURCE_LIMIT = 200


def _serialize_checkpoint(cp, manifest: dict, live_schema: str) -> dict:
    """Serialize a Checkpoint row, enriched with manifest counts and compat flag.

    Args:
        cp: Checkpoint ORM row.
        manifest: Sidecar manifest dict (may be empty for old checkpoints).
        live_schema: Current live-DB alembic schema version.

    Returns:
        JSON-serialisable dict.
    """
    is_compatible = True
    if cp.schema_version and live_schema:
        # Downgrade (snapshot is newer than live) is not supported.
        is_compatible = cp.schema_version <= live_schema
    return {
        "id": cp.id,
        "kind": cp.kind,
        "label": cp.label,
        "created_at": cp.created_at.isoformat(),
        "byte_size": cp.byte_size,
        "picture_count": cp.picture_count,
        "picture_set_count": manifest.get("picture_set_count", 0),
        "project_count": manifest.get("project_count", 0),
        "character_count": manifest.get("character_count", 0),
        "schema_version": cp.schema_version,
        "is_compatible": is_compatible,
    }


def _serialize_preview(preview, is_compatible: bool = True) -> dict:
    """Serialize a RestorePreview to a JSON-safe dict.

    Args:
        preview: RestorePreview dataclass instance.
        is_compatible: Whether the checkpoint schema is compatible with the
            live DB (False when the snapshot is newer than the live DB).

    Returns:
        JSON-serialisable dict.
    """
    return {
        "checkpoint": {
            "id": preview.checkpoint_id,
            "kind": preview.checkpoint_kind,
            "label": preview.checkpoint_label,
            "created_at": preview.checkpoint_created_at,
            "is_compatible": is_compatible,
        },
        "resources": [
            {
                "type": r.type,
                "id": r.id,
                "exists_in_live": r.exists_in_live,
                "exists_in_snapshot": r.exists_in_snapshot,
                "file_on_disk": r.file_on_disk,
                "changed_fields": r.changed_fields,
                "dependent_counts": r.dependent_counts,
            }
            for r in preview.resources[:_PREVIEW_RESOURCE_LIMIT]
        ],
        "summary": preview.summary,
        "warnings": preview.warnings,
    }


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
        """Return all checkpoints ordered by creation date (newest first).

        Each row is enriched with manifest resource counts and an
        ``is_compatible`` flag (``false`` when the snapshot schema version is
        newer than the live DB — restore would require a downgrade, which is
        unsupported).
        """
        checkpoints = server.vault.checkpoint_service.list_checkpoints()
        live_schema = server.vault.checkpoint_service.get_live_schema_version()
        result = []
        for cp in checkpoints:
            manifest = server.vault.checkpoint_service.load_manifest(cp.id)
            result.append(_serialize_checkpoint(cp, manifest, live_schema))
        return result

    # ------------------------------------------------------------------
    # GET /checkpoints/status
    # ------------------------------------------------------------------

    @router.get("/checkpoints/status")
    def checkpoints_status(request: Request):
        """Return the currently-running restore or checkpoint job, if any.

        ``active_job`` is ``null`` when no job is in progress.
        """
        active_job = getattr(server.vault.restore_service, "_active_job", None)
        return {"active_job": active_job}

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
        manifest = server.vault.checkpoint_service.load_manifest(cp.id)
        live_schema = server.vault.checkpoint_service.get_live_schema_version()
        return _serialize_checkpoint(cp, manifest, live_schema)

    # ------------------------------------------------------------------
    # PATCH /checkpoints/{id}  — rename label
    # ------------------------------------------------------------------

    @router.patch("/checkpoints/{checkpoint_id}")
    def rename_checkpoint(
        checkpoint_id: int,
        request: Request,
        label: Optional[str] = Body(default=None, embed=True),
    ):
        """Update the label of a checkpoint.

        Authentication is required.  Works for all checkpoint kinds.
        """
        server.auth.require_user_id(request)
        cp = server.vault.checkpoint_service.rename_checkpoint(checkpoint_id, label)
        if cp is None:
            raise HTTPException(status_code=404, detail="Checkpoint not found.")
        manifest = server.vault.checkpoint_service.load_manifest(cp.id)
        live_schema = server.vault.checkpoint_service.get_live_schema_version()
        return _serialize_checkpoint(cp, manifest, live_schema)

    # ------------------------------------------------------------------
    # DELETE /checkpoints/{id}
    # ------------------------------------------------------------------

    @router.delete("/checkpoints/{checkpoint_id}", status_code=204)
    def delete_checkpoint(checkpoint_id: int, request: Request):
        """Delete a MANUAL checkpoint and its snapshot files.

        Returns ``403 Forbidden`` for DAILY/WEEKLY/MONTHLY/OPPORTUNISTIC
        checkpoints (those are managed by the GFS schedule).

        Authentication is required.
        """
        server.auth.require_user_id(request)
        cp = server.vault.checkpoint_service.get_checkpoint(checkpoint_id)
        if cp is None:
            raise HTTPException(status_code=404, detail="Checkpoint not found.")
        if cp.kind != "MANUAL":
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Cannot delete a {cp.kind} checkpoint. "
                    "Only MANUAL checkpoints may be deleted by the user; "
                    "GFS-scheduled checkpoints are pruned automatically."
                ),
            )
        try:
            server.vault.checkpoint_service.delete_checkpoint(checkpoint_id)
        except Exception as exc:
            logger.error(
                "Failed to delete checkpoint %d: %s",
                checkpoint_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    # GET /checkpoints/{id}/restore/preview  (full-restore dry-run preview)
    # ------------------------------------------------------------------

    @router.get("/checkpoints/{checkpoint_id}/restore/preview")
    def preview_full_restore(checkpoint_id: int, request: Request):
        """Return a dry-run preview of a full restore.

        No data is written.  The response includes a summary of what would
        change and per-resource diff entries (capped at 200).
        """
        try:
            preview = server.vault.restore_service.preview_full(checkpoint_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "preview_full for checkpoint %d failed: %s",
                checkpoint_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        cp = server.vault.checkpoint_service.get_checkpoint(checkpoint_id)
        live_schema = server.vault.checkpoint_service.get_live_schema_version()
        is_compatible = True
        if cp and cp.schema_version and live_schema:
            is_compatible = cp.schema_version <= live_schema
        return _serialize_preview(preview, is_compatible=is_compatible)

    # ------------------------------------------------------------------
    # GET /checkpoints/{id}/restore/{resource_type}/{resource_id}/preview
    # ------------------------------------------------------------------

    @router.get(
        "/checkpoints/{checkpoint_id}/restore/{resource_type}/{resource_id}/preview"
    )
    def preview_resource_restore(
        checkpoint_id: int,
        resource_type: str,
        resource_id: int,
        request: Request,
    ):
        """Return a dry-run preview of a single-resource restore."""
        try:
            preview = server.vault.restore_service.preview_resource(
                checkpoint_id, resource_type, resource_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "preview_resource for checkpoint %d (%s/%s) failed: %s",
                checkpoint_id,
                resource_type,
                resource_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        cp = server.vault.checkpoint_service.get_checkpoint(checkpoint_id)
        live_schema = server.vault.checkpoint_service.get_live_schema_version()
        is_compatible = True
        if cp and cp.schema_version and live_schema:
            is_compatible = cp.schema_version <= live_schema
        return _serialize_preview(preview, is_compatible=is_compatible)

    # ------------------------------------------------------------------
    # POST /checkpoints/{id}/restore/preview/batch
    # ------------------------------------------------------------------

    @router.post("/checkpoints/{checkpoint_id}/restore/preview/batch")
    def preview_batch_restore(
        checkpoint_id: int,
        request: Request,
        resources: List[dict] = Body(embed=True),
    ):
        """Return a dry-run preview for a batch of resources.

        Body: ``{"resources": [{"type": "picture", "id": 42}, …]}``
        """
        try:
            preview = server.vault.restore_service.preview_batch(
                checkpoint_id, resources
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "preview_batch for checkpoint %d failed: %s",
                checkpoint_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        cp = server.vault.checkpoint_service.get_checkpoint(checkpoint_id)
        live_schema = server.vault.checkpoint_service.get_live_schema_version()
        is_compatible = True
        if cp and cp.schema_version and live_schema:
            is_compatible = cp.schema_version <= live_schema
        return _serialize_preview(preview, is_compatible=is_compatible)

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
                "Full restore of checkpoint %d failed: %s",
                checkpoint_id,
                exc,
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
    # POST /checkpoints/{id}/restore/batch
    # ------------------------------------------------------------------

    @router.post("/checkpoints/{checkpoint_id}/restore/batch")
    def restore_batch(
        checkpoint_id: int,
        request: Request,
        resources: List[dict] = Body(embed=True),
    ):
        """Restore a batch of resources from a checkpoint in one operation.

        Body: ``{"resources": [{"type": "picture", "id": 42}, …]}``

        Authentication is required.  Returns a combined RestoreReport.
        """
        server.auth.require_user_id(request)
        server.vault.notify(
            EventType.RESTORE_STARTED,
            {"checkpoint_id": checkpoint_id, "resource_type": "batch"},
        )
        try:
            report = server.vault.restore_service.restore_batch(
                checkpoint_id, resources
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "Batch restore from checkpoint %d failed: %s",
                checkpoint_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "checkpoint_id": report.checkpoint_id,
            "resource_type": report.resource_type,
            "missing_files_count": report.missing_files_count,
            "upserted_count": report.upserted_count,
            "errors": report.errors,
        }

    # ------------------------------------------------------------------
    # POST /checkpoints/{id}/hash-compare
    # ------------------------------------------------------------------

    @router.post("/checkpoints/{checkpoint_id}/hash-compare")
    def hash_compare(
        checkpoint_id: int,
        request: Request,
        picture_ids: List[int] = Body(embed=True),
    ):
        """Compare live metadata_hash values against a checkpoint snapshot.

        For each requested picture ID, compares the ``metadata_hash`` stored
        in the live DB against the value in the snapshot.  A NULL hash on
        either side means "potentially changed" (conservative).

        Body: ``{"picture_ids": [42, 43, …]}``

        Returns:
            ``{"identical_ids": [...], "changed_ids": [...]}``
        """
        try:
            result = server.vault.restore_service.compare_hashes(
                checkpoint_id, picture_ids
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return result

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
