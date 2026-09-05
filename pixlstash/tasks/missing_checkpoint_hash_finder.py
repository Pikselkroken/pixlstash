"""Finder for checkpoints the scan registered without a hash."""

from __future__ import annotations

import os

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services.builtin_models import BUILTIN_OWNER
from pixlstash.task_runner import TaskCancelledError
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.tasks.checkpoint_hash_task import CheckpointHashTask

logger = get_logger(__name__)


class MissingCheckpointHashFinder(BaseTaskFinder):
    """Hand out registered models whose ``sha256`` is still NULL.

    In practice that is exactly the checkpoints: the schema's
    ``CHECK (file_kind <> 'adapter' OR sha256 IS NOT NULL)`` forbids an unhashed
    adapter, and the scan hashes an ``unknown`` on sight because it is small.
    **``engine`` rows are excluded from both queries.** They are declared by
    ``services/builtin_models.py`` and carry no ``sha256`` by design - nothing
    hashes PixlStash's own tagger, because we know what it is without one.
    Without the exclusion they match ``sha256 IS NULL`` like any unhashed
    checkpoint, and this finder would hand a 339 MB tagger and a pile of ONNX to
    the hash worker to read, then write a digest onto a row that never wanted
    one.

    **A folder PixlStash declared is excluded too**, and that is not the same
    exclusion wearing a second hat. A declared root is described by an index
    rather than walked, so its ``relpath`` is whatever the index calls one
    entry - for the HuggingFace cache that is ``models--org--name``, a
    DIRECTORY. Now that a repo the owner downloaded themselves is theirs to
    reclassify (``builtin_caches``), any such row they correct to ``checkpoint``
    would match on ``file_kind`` and send the worker to open a directory, fail,
    and defer it - every start, forever. ``owner`` is the same marker that makes
    the folder scanner skip these roots.

    The query is left as the plain ``sha256 IS NULL`` all the same, so it
    matches ``ix_model_hash_queue`` exactly and cannot silently strand a row.

    One task at a time (the base ``max_inflight_tasks`` of 1), but that alone is
    not enough to stop a batch being handed out twice: the planner frees the
    inflight slot before it tells this finder how the task went, so the rows are
    tracked as handed out from the moment the task is built and released when
    its result arrives.

    A row the task could not hash - an unreadable file, a path that has moved -
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
        # Rows handed out whose result is not in yet.
        # ``WorkPlanner.on_task_complete`` frees this finder's inflight slot
        # under its lock and only then calls the finder's own callback, so the
        # planner can run ``find_task`` in between while ``_deferred`` is still
        # empty and re-issue the identical batch. Excluding what is already out
        # closes that window, and it costs nothing on the happy path: a hashed
        # row stops matching ``sha256 IS NULL`` anyway.
        self._handed_out: set[int] = set()

    def finder_name(self) -> str:
        return "MissingCheckpointHashFinder"

    def progress(self) -> tuple[int, int]:
        """``(models this worker owns, how many still await a hash)``.

        The task manager needs a denominator that is the model shelf, not the
        picture library, and a "remaining" that cannot disagree with the work
        actually left. Both counts therefore come from one query over one row
        set, and the pending count is the finder's own ``sha256 IS NULL``
        predicate rather than a ``file_kind`` guess: a row this finder would
        hand out must never be reported as nothing left to do.

        Deferred rows still count as pending. They are work this session has
        refused to retry, not work that finished, and hiding them would make a
        broken path look like a completed one.
        """
        row = self._hub.fetchone(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN m.sha256 IS NULL THEN 1 ELSE 0 END) AS pending "
            "FROM model m "
            "WHERE (m.sha256 IS NULL OR m.file_kind = 'checkpoint') "
            "AND m.file_kind <> 'engine' "
            "AND EXISTS (SELECT 1 FROM model_file mf "
            "JOIN model_folder f ON f.id = mf.model_folder_id "
            "WHERE mf.model_id = m.id AND mf.state = 'present' "
            "AND (f.owner IS NULL OR f.owner <> ?))",
            (BUILTIN_OWNER,),
        )
        if row is None:
            return 0, 0
        return int(row["total"] or 0), int(row["pending"] or 0)

    def find_task(self):
        skip = self._deferred | self._handed_out
        limit = CheckpointHashTask.BATCH_SIZE + len(skip)
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
            "WHERE m.sha256 IS NULL AND m.file_kind <> 'engine' "
            "AND mf.state = 'present' "
            "AND (f.owner IS NULL OR f.owner <> ?) "
            "GROUP BY m.id ORDER BY m.id LIMIT ?",
            (BUILTIN_OWNER, limit),
        )
        batch = [
            (row["id"], os.path.join(row["folder_path"], row["relpath"]))
            for row in rows
            if row["id"] not in skip
        ][: CheckpointHashTask.BATCH_SIZE]
        if not batch:
            return None
        self._handed_out.update(model_id for model_id, _ in batch)
        return CheckpointHashTask(hub=self._hub, checkpoints=batch)

    def on_task_complete(self, task, error) -> None:
        """Record which rows must not be handed out again this session.

        A cancelled task never ran, so its rows are released and left alone:
        deferring them would strand checkpoints over a plain planner stop or a
        queue drain (every restore does both) and log it as a hashing failure.
        """
        ids = (getattr(task, "params", None) or {}).get("checkpoint_ids") or []
        if isinstance(error, TaskCancelledError):
            logger.debug(
                "Checkpoint hash task for %s was cancelled before it ran: %s. "
                "Those rows stay eligible.",
                ids,
                error,
            )
            self._handed_out.difference_update(ids)
            return
        if error is not None:
            logger.warning(
                "Checkpoint hashing failed for %s: %s. Deferring those rows for "
                "the rest of this session.",
                ids,
                error,
            )
            self._deferred.update(ids)
            self._handed_out.difference_update(ids)
            return
        self._deferred.update((getattr(task, "result", None) or {}).get("deferred", []))
        self._handed_out.difference_update(ids)
