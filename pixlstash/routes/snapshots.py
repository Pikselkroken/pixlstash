"""HTTP routes for snapshot creation, listing, deletion, and restore/undo."""

from typing import Any, List, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# Maximum number of resources returned in preview responses.
_PREVIEW_RESOURCE_LIMIT = 200


# ----------------------------------------------------------------------
# Response models (declared so Scalar renders real 200 bodies, not null)
# ----------------------------------------------------------------------


class SnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    kind: Optional[str] = None
    label: Optional[str] = None
    created_at: Optional[str] = None
    byte_size: Optional[int] = None
    picture_count: Optional[int] = None
    picture_set_count: Optional[int] = None
    project_count: Optional[int] = None
    character_count: Optional[int] = None
    schema_version: Optional[str] = None
    is_compatible: Optional[bool] = None


class SnapshotsStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    active_job: Optional[Any] = None


class RestorePreviewSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    kind: Optional[str] = None
    label: Optional[str] = None
    created_at: Optional[str] = None
    is_compatible: Optional[bool] = None


class RestorePreviewResource(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Optional[str] = None
    id: Optional[int] = None
    exists_in_live: Optional[bool] = None
    exists_in_snapshot: Optional[bool] = None
    file_on_disk: Optional[bool] = None
    changed_fields: Optional[Any] = None
    dependent_counts: Optional[Any] = None


class RestorePreviewResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    snapshot: Optional[RestorePreviewSnapshot] = None
    resources: Optional[List[RestorePreviewResource]] = None
    summary: Optional[Any] = None
    warnings: Optional[Any] = None


class RestoreReportResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    snapshot_id: Optional[int] = None
    resource_type: Optional[str] = None
    resource_id: Optional[int] = None
    missing_files_count: Optional[int] = None
    upserted_count: Optional[int] = None
    errors: Optional[Any] = None
    dry_run: Optional[bool] = None


class HashCompareResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    identical_ids: Optional[List[int]] = None
    changed_ids: Optional[List[int]] = None


def _serialize_snapshot(cp, manifest: dict, live_schema: str) -> dict:
    """Serialize a Snapshot row, enriched with manifest counts and compat flag.

    Args:
        cp: Snapshot ORM row.
        manifest: Sidecar manifest dict (may be empty for old snapshots).
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
        is_compatible: Whether the snapshot schema is compatible with the
            live DB (False when the snapshot is newer than the live DB).

    Returns:
        JSON-serialisable dict.
    """
    return {
        "snapshot": {
            "id": preview.snapshot_id,
            "kind": preview.snapshot_kind,
            "label": preview.snapshot_label,
            "created_at": preview.snapshot_created_at,
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
    """Create the snapshots APIRouter.

    Args:
        server: The Server instance providing ``vault`` and ``auth``.

    Returns:
        A fully configured ``APIRouter``.
    """
    router = APIRouter()

    # ------------------------------------------------------------------
    # GET /snapshots
    # ------------------------------------------------------------------

    @router.get("/snapshots", response_model=list[SnapshotResponse])
    def list_snapshots(request: Request):
        """Return all snapshots ordered by creation date (newest first).

        Each row is enriched with manifest resource counts and an
        ``is_compatible`` flag (``false`` when the snapshot schema version is
        newer than the live DB — restore would require a downgrade, which is
        unsupported).
        """
        snapshots = server.vault.snapshot_service.list_snapshots()
        live_schema = server.vault.snapshot_service.get_live_schema_version()
        result = []
        for cp in snapshots:
            manifest = server.vault.snapshot_service.load_manifest(cp.id)
            result.append(_serialize_snapshot(cp, manifest, live_schema))
        return result

    # ------------------------------------------------------------------
    # GET /snapshots/status
    # ------------------------------------------------------------------

    @router.get("/snapshots/status", response_model=SnapshotsStatusResponse)
    def snapshots_status(request: Request):
        """Return the currently-running restore or snapshot job, if any.

        ``active_job`` is ``null`` when no job is in progress.
        """
        active_job = getattr(server.vault.restore_service, "_active_job", None)
        return {"active_job": active_job}

    # ------------------------------------------------------------------
    # POST /snapshots
    # ------------------------------------------------------------------

    @router.post("/snapshots", status_code=201, response_model=SnapshotResponse)
    def create_snapshot(
        request: Request,
        label: Optional[str] = Body(default=None, embed=True),
    ):
        """Create a manual snapshot snapshot.

        Authentication is required.  Returns the new snapshot record.
        """
        server.auth.require_user_id(request)
        try:
            cp = server.vault.snapshot_service.create_snapshot(
                kind="MANUAL", label=label
            )
        except Exception as exc:
            logger.error("Failed to create snapshot: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        manifest = server.vault.snapshot_service.load_manifest(cp.id)
        live_schema = server.vault.snapshot_service.get_live_schema_version()
        return _serialize_snapshot(cp, manifest, live_schema)

    # ------------------------------------------------------------------
    # PATCH /snapshots/{id}  — rename label
    # ------------------------------------------------------------------

    @router.patch("/snapshots/{snapshot_id}", response_model=SnapshotResponse)
    def rename_snapshot(
        snapshot_id: int,
        request: Request,
        label: Optional[str] = Body(default=None, embed=True),
    ):
        """Update the label of a snapshot.

        Authentication is required.  Works for all snapshot kinds.
        """
        server.auth.require_user_id(request)
        cp = server.vault.snapshot_service.rename_snapshot(snapshot_id, label)
        if cp is None:
            raise HTTPException(status_code=404, detail="Snapshot not found.")
        manifest = server.vault.snapshot_service.load_manifest(cp.id)
        live_schema = server.vault.snapshot_service.get_live_schema_version()
        return _serialize_snapshot(cp, manifest, live_schema)

    # ------------------------------------------------------------------
    # DELETE /snapshots/{id}
    # ------------------------------------------------------------------

    @router.delete("/snapshots/{snapshot_id}", status_code=204)
    def delete_snapshot(snapshot_id: int, request: Request):
        """Delete a snapshot and its snapshot files.

        MANUAL and OPPORTUNISTIC snapshots may always be deleted.  For
        GFS-scheduled kinds (DAILY, WEEKLY, MONTHLY) deletion is refused
        when this is the last remaining snapshot of that kind — the system
        always keeps at least one of each GFS tier.

        Authentication is required.
        """
        server.auth.require_user_id(request)
        cp = server.vault.snapshot_service.get_snapshot(snapshot_id)
        if cp is None:
            raise HTTPException(status_code=404, detail="Snapshot not found.")
        if cp.kind in ("DAILY", "WEEKLY", "MONTHLY"):
            all_of_kind = [
                s
                for s in server.vault.snapshot_service.list_snapshots()
                if s.kind == cp.kind
            ]
            if len(all_of_kind) <= 1:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot delete the only remaining {cp.kind} snapshot. "
                        "The system keeps at least one of each GFS tier."
                    ),
                )
        try:
            server.vault.snapshot_service.delete_snapshot(snapshot_id)
        except Exception as exc:
            logger.error(
                "Failed to delete snapshot %d: %s",
                snapshot_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    # GET /snapshots/{id}/restore/preview  (full-restore dry-run preview)
    # ------------------------------------------------------------------

    @router.get(
        "/snapshots/{snapshot_id}/restore/preview",
        response_model=RestorePreviewResponse,
    )
    def preview_full_restore(snapshot_id: int, request: Request):
        """Return a dry-run preview of a full restore.

        No data is written.  The response includes a summary of what would
        change and per-resource diff entries (capped at 200).
        """
        try:
            preview = server.vault.restore_service.preview_full(snapshot_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "preview_full for snapshot %d failed: %s",
                snapshot_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        cp = server.vault.snapshot_service.get_snapshot(snapshot_id)
        live_schema = server.vault.snapshot_service.get_live_schema_version()
        is_compatible = True
        if cp and cp.schema_version and live_schema:
            is_compatible = cp.schema_version <= live_schema
        return _serialize_preview(preview, is_compatible=is_compatible)

    # ------------------------------------------------------------------
    # GET /snapshots/{id}/restore/{resource_type}/{resource_id}/preview
    # ------------------------------------------------------------------

    @router.get(
        "/snapshots/{snapshot_id}/restore/{resource_type}/{resource_id}/preview",
        response_model=RestorePreviewResponse,
    )
    def preview_resource_restore(
        snapshot_id: int,
        resource_type: str,
        resource_id: int,
        request: Request,
    ):
        """Return a dry-run preview of a single-resource restore."""
        try:
            preview = server.vault.restore_service.preview_resource(
                snapshot_id, resource_type, resource_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "preview_resource for snapshot %d (%s/%s) failed: %s",
                snapshot_id,
                resource_type,
                resource_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        cp = server.vault.snapshot_service.get_snapshot(snapshot_id)
        live_schema = server.vault.snapshot_service.get_live_schema_version()
        is_compatible = True
        if cp and cp.schema_version and live_schema:
            is_compatible = cp.schema_version <= live_schema
        return _serialize_preview(preview, is_compatible=is_compatible)

    # ------------------------------------------------------------------
    # POST /snapshots/{id}/restore/preview/batch
    # ------------------------------------------------------------------

    @router.post(
        "/snapshots/{snapshot_id}/restore/preview/batch",
        response_model=RestorePreviewResponse,
    )
    def preview_batch_restore(
        snapshot_id: int,
        request: Request,
        resources: List[dict] = Body(embed=True),
    ):
        """Return a dry-run preview for a batch of resources.

        Body: ``{"resources": [{"type": "picture", "id": 42}, …]}``
        """
        try:
            preview = server.vault.restore_service.preview_batch(snapshot_id, resources)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "preview_batch for snapshot %d failed: %s",
                snapshot_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        cp = server.vault.snapshot_service.get_snapshot(snapshot_id)
        live_schema = server.vault.snapshot_service.get_live_schema_version()
        is_compatible = True
        if cp and cp.schema_version and live_schema:
            is_compatible = cp.schema_version <= live_schema
        return _serialize_preview(preview, is_compatible=is_compatible)

    # ------------------------------------------------------------------
    # POST /snapshots/{id}/restore  (full restore)
    # ------------------------------------------------------------------

    @router.post(
        "/snapshots/{snapshot_id}/restore", response_model=RestoreReportResponse
    )
    def restore_snapshot(
        snapshot_id: int,
        request: Request,
        dry_run: bool = Body(default=False, embed=True),
    ):
        """Replace the live database with the given snapshot snapshot.

        Authentication is required.  Returns a summary of the restore.
        """
        server.auth.require_user_id(request)
        server.vault.notify(EventType.RESTORE_STARTED, {"snapshot_id": snapshot_id})
        try:
            report = server.vault.restore_service.restore_full(
                snapshot_id, dry_run=dry_run
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "Full restore of snapshot %d failed: %s",
                snapshot_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "snapshot_id": report.snapshot_id,
            "resource_type": report.resource_type,
            "missing_files_count": report.missing_files_count,
            "upserted_count": report.upserted_count,
            "errors": report.errors,
            "dry_run": dry_run,
        }

    # ------------------------------------------------------------------
    # POST /snapshots/{id}/restore/batch
    # ------------------------------------------------------------------

    @router.post(
        "/snapshots/{snapshot_id}/restore/batch", response_model=RestoreReportResponse
    )
    def restore_batch(
        snapshot_id: int,
        request: Request,
        resources: List[dict] = Body(embed=True),
    ):
        """Restore a batch of resources from a snapshot in one operation.

        Body: ``{"resources": [{"type": "picture", "id": 42}, …]}``

        Authentication is required.  Returns a combined RestoreReport.
        """
        server.auth.require_user_id(request)
        server.vault.notify(
            EventType.RESTORE_STARTED,
            {"snapshot_id": snapshot_id, "resource_type": "batch"},
        )
        try:
            report = server.vault.restore_service.restore_batch(snapshot_id, resources)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "Batch restore from snapshot %d failed: %s",
                snapshot_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "snapshot_id": report.snapshot_id,
            "resource_type": report.resource_type,
            "missing_files_count": report.missing_files_count,
            "upserted_count": report.upserted_count,
            "errors": report.errors,
        }

    # ------------------------------------------------------------------
    # POST /snapshots/{id}/hash-compare
    # ------------------------------------------------------------------

    @router.post(
        "/snapshots/{snapshot_id}/hash-compare", response_model=HashCompareResponse
    )
    def hash_compare(
        snapshot_id: int,
        request: Request,
        picture_ids: List[int] = Body(embed=True),
    ):
        """Compare live metadata_hash values against a snapshot snapshot.

        For each requested picture ID, compares the ``metadata_hash`` stored
        in the live DB against the value in the snapshot.  A NULL hash on
        either side means "potentially changed" (conservative).

        Body: ``{"picture_ids": [42, 43, …]}``

        Returns:
            ``{"identical_ids": [...], "changed_ids": [...]}``
        """
        try:
            result = server.vault.restore_service.compare_hashes(
                snapshot_id, picture_ids
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return result

    # ------------------------------------------------------------------
    # POST /snapshots/{id}/restore/{resource_type}/{resource_id}
    # ------------------------------------------------------------------

    @router.post(
        "/snapshots/{snapshot_id}/restore/{resource_type}/{resource_id}",
        response_model=RestoreReportResponse,
    )
    def restore_resource(
        snapshot_id: int,
        resource_type: str,
        resource_id: int,
        request: Request,
    ):
        """Restore a single resource from a snapshot snapshot.

        ``resource_type`` must be one of ``picture``, ``picture_set``,
        ``project``, or ``character``.

        Authentication is required.
        """
        server.auth.require_user_id(request)
        server.vault.notify(
            EventType.RESTORE_STARTED,
            {
                "snapshot_id": snapshot_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
            },
        )
        try:
            report = server.vault.restore_service.restore_resource(
                snapshot_id, resource_type, resource_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "Per-resource restore of snapshot %d (%s/%s) failed: %s",
                snapshot_id,
                resource_type,
                resource_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "snapshot_id": report.snapshot_id,
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
        snapshot_id: Optional[int] = Body(default=None, embed=True),
    ):
        """Undo recent metadata changes.

        If ``snapshot_id`` is provided, undo all changes back to that
        snapshot (hybrid ChangeLog + snapshot strategy).  Otherwise undo
        only the most recent writer transaction.

        Authentication is required.
        """
        server.auth.require_user_id(request)
        try:
            if snapshot_id is not None:
                report = server.vault.undo_service.undo_to_snapshot(snapshot_id)
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
            "escalated_to_full_restore": report.escalated_to_full_restore,
            "escalated_tables": report.escalated_tables,
        }

    return router
