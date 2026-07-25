"""Backward-compatible re-export shim for the restore service.

The implementation was decomposed into the :mod:`pixlstash.services.restore`
package (backend refactor plan §4.4).  This module preserves the original
``pixlstash.services.restore_service`` import path so existing call sites
(``vault.py``, ``routes/snapshots.py``, and the restore tests) keep working
unchanged.
"""

from pixlstash.services.restore import (
    MissingDependenciesError,
    ResourcePreview,
    RestoreInProgressError,
    RestorePreview,
    RestoreReport,
    RestoreService,
    SafetySnapshotFailedError,
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
