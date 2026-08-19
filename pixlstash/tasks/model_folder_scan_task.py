"""Task that rescans one registered model folder.

User-triggered, so there is no ``Missing*Finder``: the owner presses the button
and ``POST /model-folders/{id}/rescan`` submits this straight to the
``TaskRunner``, the way ``PictureImportTask`` and ``DetectionTask`` are
submitted. It used to be a bare ``threading.Thread``, which nothing observed:
no progress on a 57 GB folder, no way to tell a crash from a slow read, and a
daemon thread alive at interpreter shutdown (#856).

Two things the task system now supplies for free:

* ``_total_count`` / ``_processed_count`` are read by
  :meth:`pixlstash.vault.Vault._build_worker_progress_snapshot`, so the scan
  renders as a task row in the existing task manager;
* ``status`` is a real terminal state. A crashed scan is ``failed`` with the
  message on ``error``, instead of being indistinguishable from a slow one
  because ``last_checked`` had not moved yet.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_folder_scanner import ModelFolderScanner
from pixlstash.tasks.base_task import BaseTask, TaskPriority

logger = get_logger(__name__)


class ModelFolderScanTask(BaseTask):
    """Walk one registered model folder and reconcile its rows with disk."""

    def __init__(self, hub: HubDatabase, folder_id: int, path: str, kind: str):
        """Bind the task to one folder.

        Args:
            hub: The hub database holding the shelf tables.
            folder_id: ``model_folder.id``.
            path: The folder as registered. Owner-chosen and therefore trusted;
                the scanner only reads it.
            kind: ``model_folder.kind``. ``source`` folders are skipped by the
                scanner itself.
        """
        super().__init__(
            task_type="ModelFolderScanTask",
            params={"folder_id": folder_id, "path": path, "kind": kind},
        )
        self._hub = hub
        self._folder_id = folder_id
        self._path = path
        self._kind = kind
        # Live progress, read by Vault.get_worker_progress for the task manager.
        self._total_count = 0
        self._processed_count = 0

    @property
    def folder_id(self) -> int:
        """Which folder this scan is for, so a caller can match it to a row."""
        return self._folder_id

    @property
    def priority(self) -> TaskPriority:
        # User-initiated: the owner is looking at the folder they just added, so
        # it should not queue behind a library-wide tagging sweep.
        return TaskPriority.HIGH

    def _run_task(self) -> dict[str, Any]:
        result = ModelFolderScanner(self._hub).scan_folder(
            self._folder_id, self._path, self._kind, progress=self._report_progress
        )
        return asdict(result)

    def _report_progress(self, processed: int, total: int) -> None:
        self._processed_count = processed
        self._total_count = total
