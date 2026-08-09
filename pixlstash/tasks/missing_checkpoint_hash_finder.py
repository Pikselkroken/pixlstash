"""Finder for checkpoints the scan registered without a hash."""

from __future__ import annotations

import os

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.tasks.checkpoint_hash_task import CheckpointHashTask

logger = get_logger(__name__)


class MissingCheckpointHashFinder(BaseTaskFinder):
    """Hand out registered models whose ``sha256`` is still NULL.

    In practice that is exactly the checkpoints: the schema's
    ``CHECK (file_kind <> 'adapter' OR sha256 IS NOT NULL)`` forbids an unhashed
    adapter, and the scan hashes an ``unknown`` on sight because it is small.
    The query is left as the plain ``sha256 IS NULL`` all the same, so it
    matches ``ix_model_hash_queue`` exactly and cannot silently strand a row.

    One task at a time (the base ``max_inflight_tasks`` of 1), so no claim
    bookkeeping is needed: the batch this finder hands out is the only one in
    flight, and the rows in it stop matching the moment they are hashed.

    A row the task could not hash — an unreadable file, a path that has moved —
    is *deferred* for the life of the process rather than handed out again. The
    planner sweeps continuously, so without that a single broken path would make
    this finder return a task on every cycle forever, keeping the CPU queue and
    the planner's backoff permanently awake for work that cannot succeed. A
    re-scan is what re-queues it, because a re-scan is what proves the file is
    back.
    """

    def __init__(self, hub: HubDatabase) -> None:
        """Initialise the finder.

        Args:
            hub: The hub database holding the ``model`` table.
        """
        super().__init__()
        self._hub = hub
        self._deferred: set[int] = set()

    def finder_name(self) -> str:
        return "MissingCheckpointHashFinder"

    def find_task(self):
        limit = CheckpointHashTask.BATCH_SIZE + len(self._deferred)
        # ``state = 'present'`` is the whole path filter: a row whose only copy
        # is `missing` or `unreachable` has nothing to read, and handing it out
        # would defer it for the session over a drive that is merely unplugged.
        # GROUP BY, because a model legitimately has several locations and the
        # unit of work is one file read, not one path.
        rows = self._hub.fetchall(
            "SELECT m.id AS id, f.path AS folder_path, mf.relpath AS relpath "
            "FROM model m "
            "JOIN model_file mf ON mf.model_id = m.id "
            "JOIN model_folder f ON f.id = mf.model_folder_id "
            "WHERE m.sha256 IS NULL AND mf.state = 'present' "
            "GROUP BY m.id ORDER BY m.id LIMIT ?",
            (limit,),
        )
        batch = [
            (row["id"], os.path.join(row["folder_path"], row["relpath"]))
            for row in rows
            if row["id"] not in self._deferred
        ][: CheckpointHashTask.BATCH_SIZE]
        if not batch:
            return None
        return CheckpointHashTask(hub=self._hub, checkpoints=batch)

    def on_task_complete(self, task, error) -> None:
        """Record which rows must not be handed out again this session."""
        if error is not None:
            ids = (getattr(task, "params", None) or {}).get("checkpoint_ids") or []
            logger.warning(
                "Checkpoint hashing failed for %s: %s. Deferring those rows for "
                "the rest of this session.",
                ids,
                error,
            )
            self._deferred.update(ids)
            return
        self._deferred.update((getattr(task, "result", None) or {}).get("deferred", []))
