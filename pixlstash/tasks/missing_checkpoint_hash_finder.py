"""Finder for checkpoints the scan registered without a hash."""

from __future__ import annotations

import os

from pixlstash.hub.db import HubDatabase
from pixlstash.services.builtin_models import BUILTIN_OWNER
from pixlstash.task_runner import TaskCancelledError
from pixlstash.tasks.base_task_finder import BaseDeferringFinder
from pixlstash.tasks.checkpoint_hash_task import CheckpointHashTask


class MissingCheckpointHashFinder(BaseDeferringFinder):
    """Hand out registered models whose ``sha256`` is still NULL.

    In practice that is the checkpoints and the other long reads: the schema's
    ``CHECK (file_kind <> 'adapter' OR sha256 IS NOT NULL)`` forbids an unhashed
    adapter, and the scan hashes anything else **below**
    ``model_folder_scanner._DEFER_HASH_BYTES`` on sight because it is small.
    The rest arrive here whatever their kind, which is the point of a size rule
    rather than a kind one: a 23 GB text encoder and a multi-gigabyte ``.gguf``
    filed as ``unknown`` are exactly the reads this exists to keep out of the
    scan.
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

    A row the task could not hash - an unreadable file, a path that has moved -
    is *deferred* (:class:`BaseDeferringFinder`). A re-scan is what re-queues
    it, because a re-scan is what proves the file is back. A cancelled task
    never ran, so its rows stay eligible: deferring them would strand
    checkpoints over a plain planner stop or a queue drain (every restore does
    both).
    """

    _IDS_PARAM = "checkpoint_ids"
    _TRANSIENT = (TaskCancelledError,)
    _WHAT = "Checkpoint hashing"

    def __init__(self, hub: HubDatabase) -> None:
        """Initialise the finder.

        Args:
            hub: The hub database holding the ``model`` table.
        """
        super().__init__()
        self._hub = hub

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
        # ``state = 'present'`` is the whole path filter: a row whose only copy
        # is `missing` or `unreachable` has nothing to read, and handing it out
        # would defer it for the session over a drive that is merely unplugged.
        # GROUP BY, because a model legitimately has several locations and the
        # unit of work is one file read, not one path.
        batch = self._take(
            lambda limit: [
                (row["id"], os.path.join(row["folder_path"], row["relpath"]))
                for row in self._hub.fetchall(
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
            ],
            CheckpointHashTask.BATCH_SIZE,
            key=lambda item: item[0],
        )
        if not batch:
            return None
        return CheckpointHashTask(hub=self._hub, checkpoints=batch)
