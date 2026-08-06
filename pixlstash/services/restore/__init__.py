"""RestoreService facade for the restore package.

Assembles the restore behaviour split across :mod:`schema_upgrade`,
:mod:`full_restore`, :mod:`resource_restore`, and :mod:`preview` into the
single :class:`RestoreService` class, and re-exports the public data models
and exceptions.  This package is the import path for all of them (see backend
refactor plan §4.4).
"""

import threading
from typing import TYPE_CHECKING, Optional

from pixlstash.pixl_logging import get_logger

from ._models import (
    MissingDependenciesError,
    ResourcePreview,
    RestoreInProgressError,
    RestorePreview,
    RestoreReport,
    SafetySnapshotFailedError,
)
from .full_restore import FullRestoreMixin
from .preview import PreviewMixin
from .resource_restore import ResourceRestoreMixin
from .schema_upgrade import SchemaUpgradeMixin

if TYPE_CHECKING:
    from pixlstash.event_types import EventType
    from pixlstash.vault import Vault

logger = get_logger(__name__)


class RestoreService(
    SchemaUpgradeMixin,
    FullRestoreMixin,
    ResourceRestoreMixin,
    PreviewMixin,
):
    """Restores vault metadata from a snapshot snapshot.

    Attributes:
        _vault: Back-reference to the owning Vault.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the service.

        Args:
            vault: The owning Vault instance.
        """
        self._vault = vault
        # Tracks the currently executing restore job for /snapshots/status.
        # Read via ``get_active_job()`` from outside this module.
        self._active_job: Optional[dict] = None
        # Mutual exclusion for restore_full / restore_resource / restore_batch.
        # Held for the entire duration of a restore (including the queued
        # _do_swap + _post_restore_cleanup tasks); any concurrent restore call
        # short-circuits with RestoreInProgressError → 409.
        self._restore_lock = threading.Lock()
        # Per-snapshot-file locks. compare_hashes can rewrite an old snapshot
        # in place to backfill metadata_hash; concurrent compare/preview/
        # restore on the same path would otherwise race on disk (corrupt
        # copy, partial read). Reentrant so a caller can hold the file lock
        # across multiple helpers (e.g. compare_hashes → _backfill_snapshot)
        # without self-deadlocking. The meta-lock guards the dict itself.
        self._snapshot_file_locks: dict[str, threading.RLock] = {}
        self._snapshot_file_locks_meta: threading.Lock = threading.Lock()

    def get_active_job(self) -> Optional[dict]:
        """Return the in-flight restore job descriptor, or ``None`` if idle.

        The shape is ``{"kind": "RESTORE", "snapshot_id": int,
        "started_at": isoformat, "progress": float}`` while a restore runs.
        Used by ``GET /snapshots/status``.
        """
        return self._active_job

    def _emit_lifecycle(self, event_type: "EventType", payload: dict) -> None:
        """Emit a RESTORE_STARTED / _COMPLETED / _FAILED event, swallowing
        emit failures so a flaky event bus cannot derail the restore itself.
        """
        try:
            self._vault.emit_event(event_type, payload)
        except Exception as exc:
            logger.warning(
                "RestoreService: failed to emit %s: %s", event_type.name, exc
            )


__all__ = [
    "RestoreService",
    "RestoreReport",
    "RestorePreview",
    "ResourcePreview",
    "RestoreInProgressError",
    "SafetySnapshotFailedError",
    "MissingDependenciesError",
]
