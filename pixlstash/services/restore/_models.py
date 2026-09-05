"""Shared data models, exceptions, and constants for the restore package.

Dependency-free leaf definitions imported by every restore submodule and
re-exported from :mod:`pixlstash.services.restore`.  Kept in one module so the
mixins can import them without any circular dependency.
"""

from dataclasses import dataclass, field
from typing import Optional


class RestoreInProgressError(RuntimeError):
    """Raised when a restore is requested while another one is still running.

    Two concurrent restores would both stop the planner, take safety snapshots
    and enqueue swap + cleanup tasks on the writer queue - the cleanups would
    then run against each other's swapped DB file with stale ``missing_ids``
    sets, corrupting the live database. Restore is intentionally serialised
    instead of being made concurrent; callers should retry after the running
    job (visible at ``GET /snapshots/status``) completes.
    """


class SafetySnapshotFailedError(RuntimeError):
    """Raised when the pre-restore safety snapshot fails and the caller did
    not opt into ``allow_without_safety``.

    The safety snapshot is the only rollback path if the swap or the
    post-restore cleanup later corrupts the live DB; silently continuing
    without it can turn a recoverable problem into an unrecoverable one.
    """


class MissingDependenciesError(RuntimeError):
    """Raised by ``restore_resource`` / ``restore_batch`` when the snapshot
    rows reference parent resources (Character / PictureSet / Project) that
    have been deleted from the live DB since the snapshot was taken.

    Restoring the picture without the parent would trigger ``IntegrityError``
    on commit (FK violations) and roll back the whole batch.  The caller
    surfaces ``self.missing`` to the user, who can either re-issue the
    request with ``confirm_restore_dependencies=True`` (restores the parents
    from the snapshot first) or decline and leave the live DB untouched.

    Attributes:
        missing: dict mapping resource-type plural (``"characters"`` /
            ``"picture_sets"`` / ``"projects"``) → sorted list of IDs that
            need to be restored from the snapshot before the requested
            resource can be safely upserted.
    """

    def __init__(self, missing: dict[str, list[int]]):
        self.missing = missing
        super().__init__(
            "Restore would reference resources that no longer exist in the "
            f"live DB: {missing}. Retry with confirm_restore_dependencies=True "
            "to restore them from the snapshot first, or cancel to leave the "
            "live DB untouched."
        )


# Resource types currently supported by ``restore_resource`` / ``preview_resource``.
#
# ``"project"`` is intentionally excluded for this release:
# Project's graph spans ``ProjectAttachment`` (CASCADE FK), ``Character.project_id``,
# ``PictureSet.project_id``, and ``PictureProjectMember`` rows. The current
# per-resource path only touches Project + pictures-via-PPM, and the PPM
# bulk-delete is keyed by ``picture_id`` rather than by ``project_id`` - which
# would over-delete PPMs to *other* projects for any picture also in the
# restored project. Use the full restore for project-level recovery until the
# proper graph-replace is implemented + tested.
_SUPPORTED_RESOURCE_TYPES: tuple[str, ...] = ("picture", "picture_set", "character")


# Refuse the post-restore cleanup if this fraction or more of the snapshot's
# pictures appear to be missing on disk. A partial network-mount failure
# would otherwise be silently treated as "the user deleted these files" and
# wipe their metadata. The check only kicks in once the snapshot contains
# more than ``_MIN_PICTURES_FOR_MISSING_RATIO_CHECK`` rows - at small scale
# "100% missing" is a legitimate one-picture deletion, not a mount blip.
_MAX_MISSING_RATIO_FOR_CLEANUP: float = 0.5
_MIN_PICTURES_FOR_MISSING_RATIO_CHECK: int = 10


@dataclass
class RestoreReport:
    """Summary of a completed restore operation.

    Attributes:
        snapshot_id: ID of the snapshot that was restored.
        resource_type: ``'full'``, ``'picture'``, ``'picture_set'``,
            ``'project'``, or ``'character'``.
        resource_id: Primary key of the specific resource (None for full
            restore).
        missing_files_count: Number of Picture rows skipped because their
            files were absent on disk.
        permanently_deleted_count: Number of Picture rows skipped because
            their file/content was recorded in ``deleted_file_log`` (a
            permanent deletion that restore must never resurrect).
        upserted_count: Number of rows upserted (per-resource restores only).
        errors: Non-fatal error messages accumulated during the restore.
    """

    snapshot_id: int
    resource_type: str
    resource_id: Optional[int] = None
    missing_files_count: int = 0
    permanently_deleted_count: int = 0
    upserted_count: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ResourcePreview:
    """Preview information for a single resource that would be affected by a restore.

    Attributes:
        type: Resource type string (``'picture'``, ``'picture_set'``, etc.).
        id: Primary key of the resource.
        exists_in_live: True if the resource exists in the live database.
        exists_in_snapshot: True if the resource exists in the snapshot.
        file_on_disk: True if the picture file exists on disk (always True
            for non-picture resources).
        changed_fields: List of column names that differ between live and
            snapshot (for picture resources).
        dependent_counts: Counts of dependent objects (e.g.
            ``{"faces": 2, "tags": 10}``).
    """

    type: str
    id: int
    exists_in_live: bool = True
    exists_in_snapshot: bool = True
    file_on_disk: bool = True
    changed_fields: list[str] = field(default_factory=list)
    dependent_counts: dict = field(default_factory=dict)


@dataclass
class RestorePreview:
    """Dry-run preview of a restore operation.

    Attributes:
        snapshot_id: ID of the snapshot to be restored.
        snapshot_kind: Kind of the snapshot.
        snapshot_label: Optional user label.
        snapshot_created_at: ISO timestamp string.
        resources: Per-resource preview entries (capped at 200).
        summary: High-level counts of what would change.
        warnings: Human-readable warning strings (e.g. missing files).
    """

    snapshot_id: int
    snapshot_kind: str
    snapshot_label: Optional[str]
    snapshot_created_at: str
    resources: list[ResourcePreview] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
