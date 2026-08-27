"""Fill in the SHA-256 a checkpoint was registered without.

A checkpoint may be 24 GB. The scan registers it the instant it sees it, with
``sha256`` NULL, so the shelf lists it immediately; this task supplies the hash
afterwards, which is what ``model.hashed_at`` was always for. The unit of work
is a whole file: a part-finished ``hashlib`` object cannot be pickled and
``sha256`` of the first N% has no relationship to the real digest, so
"percentage" here means files done, never bytes within a file.

**Two paths, one hash, is a merge and not an error.** ``model.sha256`` is
UNIQUE, and two rows legitimately arrive at the same digest: an unhashed
checkpoint is identified by the location it was found at, so the same file in
two registered folders is two rows, as is the duplicate an interrupted move
leaves behind (copy, verify, repoint, unlink - a crash between the copy and the
unlink is *designed* to leave two paths holding one file). Raising there would
fail a background task on a state the product deliberately allows, and would do
it repeatedly, since the finder would hand the same row back on the next sweep.

So the collision resolves in place: the two content rows become one, keeping the
older id and whatever either row knows, and **every location either row knew
moves to the survivor**. That last part is what ``model_file`` is for. With the
path stored inline on the content row there was nowhere to put the second one,
so the merge silently forgot it, the next scan re-registered it unhashed, this
task read all 24 GB again, and the merge dropped it again - forever, once per
scan cycle.

**A merge can also take a file out of a run.** The losing row is deleted, and
if it was in an ``adapter_stack`` that leaves the same hole Forget and Delete
leave, so the merge calls
:func:`~pixlstash.services.stack_detector.repair_stacks` for it. The stack is
not inherited by the survivor: that row may be in a run of its own, and a model
belongs to one.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_folder_scanner import sha256_file
from pixlstash.services.stack_detector import repair_stacks
from pixlstash.tasks.base_task import BaseTask, TaskPriority

logger = get_logger(__name__)


class CheckpointHashTask(BaseTask):
    """Hash a small batch of registered checkpoints and store the result."""

    BATCH_SIZE = 4

    def __init__(self, hub: HubDatabase, checkpoints: list[tuple[int, str]]):
        """Initialise the task.

        Args:
            hub: The hub database holding the ``model`` table.
            checkpoints: ``(model_id, absolute_path)`` pairs to hash.
        """
        super().__init__(
            task_type="CheckpointHashTask",
            params={"checkpoint_ids": [row[0] for row in checkpoints]},
        )
        self._hub = hub
        self._checkpoints = checkpoints

    @property
    def priority(self) -> TaskPriority:
        """LOW: reading tens of gigabytes must never delay interactive work."""
        return TaskPriority.LOW

    def _run_task(self):
        hashed = 0
        merged = 0
        deferred: list[int] = []
        for checkpoint_id, path in self._checkpoints:
            try:
                size = os.path.getsize(path)
                digest = sha256_file(path)
            except OSError as exc:
                logger.warning(
                    "Could not hash checkpoint %s at %s: %s. Leaving sha256 NULL "
                    "and not retrying it this session; a re-scan re-queues it once "
                    "the file is readable again.",
                    checkpoint_id,
                    path,
                    exc,
                )
                deferred.append(checkpoint_id)
                continue
            if self._store(checkpoint_id, path, digest, size, deferred):
                merged += 1
            else:
                hashed += 1
        return {"hashed": hashed, "merged": merged, "deferred": deferred}

    def _store(
        self,
        checkpoint_id: int,
        path: str,
        digest: str,
        size: int,
        deferred: list[int],
    ) -> bool:
        """Write the digest, merging instead of raising when it is already taken.

        Returns:
            True when the write collided and the two rows were merged.
        """
        hashed_at = datetime.now(timezone.utc).isoformat()
        with self._hub.transaction() as conn:
            try:
                conn.execute(
                    "UPDATE model SET sha256 = ?, hashed_at = ?, file_size = ? "
                    "WHERE id = ?",
                    (digest, hashed_at, size, checkpoint_id),
                )
                return False
            except sqlite3.IntegrityError:
                # The only UNIQUE column here is sha256, so this is the
                # two-paths-one-file case. SQLite rolls back the statement, not
                # the transaction, so the merge below runs in the same one.
                logger.info(
                    "Checkpoint %s at %s hashes to %s, which is already "
                    "registered. Merging the two rows.",
                    checkpoint_id,
                    path,
                    digest,
                )
            return self._merge(conn, checkpoint_id, digest, hashed_at, size, deferred)

    @staticmethod
    def _merge(
        conn: sqlite3.Connection,
        checkpoint_id: int,
        digest: str,
        hashed_at: str,
        size: int,
        deferred: list[int],
    ) -> bool:
        """Fold two content rows for one file into one, keeping both locations.

        The lower id survives, because it is the earlier registration and so the
        one anything else is more likely to have referenced. Every ``model_file``
        row the dropped id held is repointed at it first, so a checkpoint that is
        legitimately present at two paths ends as one row with two locations
        rather than one row and a forgotten path. The survivor fills any column
        it has no value for from the row being dropped, so a base model somebody
        typed is not lost to the merge.

        There is deliberately no filesystem call here. "Which path exists" was
        the old way of choosing between two paths only one column could hold;
        both are kept now, and an ``os.path.exists`` inside an open hub write
        transaction was against the short-transactions contract anyway.
        """
        columns = (
            "id, filename, display_name, base_model, trigger_words, "
            "training_step, param_count, file_size, stack_id"
        )
        holder = conn.execute(
            f"SELECT {columns} FROM model WHERE sha256 = ?", (digest,)
        ).fetchone()
        if holder is None:
            logger.error(
                "Checkpoint %s could not store sha256 %s and no other row holds "
                "it. The row keeps sha256 NULL and is not retried this session.",
                checkpoint_id,
                digest,
            )
            deferred.append(checkpoint_id)
            return False
        if holder["id"] == checkpoint_id:
            return False

        mine = conn.execute(
            f"SELECT {columns} FROM model WHERE id = ?", (checkpoint_id,)
        ).fetchone()
        if mine is None:
            logger.warning(
                "Checkpoint %s vanished while it was being hashed; row %s already "
                "carries sha256 %s, so nothing is lost.",
                checkpoint_id,
                holder["id"],
                digest,
            )
            return True

        survivor, doomed = (
            (mine, holder) if checkpoint_id < holder["id"] else (holder, mine)
        )
        # Locations first: the FK forbids deleting a model row that still has
        # them, and moving them is the whole point of the merge.
        moved = conn.execute(
            "UPDATE model_file SET model_id = ? WHERE model_id = ?",
            (survivor["id"], doomed["id"]),
        ).rowcount
        # The other child of `model`, and the same FK rule. Carried across
        # rather than dropped: the two rows are the same bytes, so whatever the
        # doomed one was declared to serve, the survivor serves. `OR IGNORE`
        # because the survivor may already claim it, and the leftovers then go
        # - an orphan here would abort the delete below, not leak quietly.
        conn.execute(
            "INSERT OR IGNORE INTO model_capability (model_id, capability) "
            "SELECT ?, capability FROM model_capability WHERE model_id = ?",
            (survivor["id"], doomed["id"]),
        )
        conn.execute("DELETE FROM model_capability WHERE model_id = ?", (doomed["id"],))
        conn.execute("DELETE FROM model WHERE id = ?", (doomed["id"],))
        # A duplicate can be one file of a run, and the row that loses the merge
        # leaves it without asking the stack module - the same hole Forget and
        # Delete leave, and the same repair: the survivors are renumbered, and a
        # run left with one file stops being a run. Not inherited by the
        # survivor: it may be in a run of its own, and a member cannot be in
        # two.
        if doomed["stack_id"] is not None:
            repair_stacks(conn, [doomed["stack_id"]])
        conn.execute(
            "UPDATE model SET sha256 = ?, hashed_at = ?, file_size = ?, "
            "filename = COALESCE(filename, ?), "
            "display_name = COALESCE(display_name, ?), "
            "base_model = COALESCE(base_model, ?), "
            "trigger_words = COALESCE(trigger_words, ?), "
            "training_step = COALESCE(training_step, ?), "
            "param_count = COALESCE(param_count, ?) WHERE id = ?",
            (
                digest,
                hashed_at,
                size,
                doomed["filename"],
                doomed["display_name"],
                doomed["base_model"],
                doomed["trigger_words"],
                doomed["training_step"],
                doomed["param_count"],
                survivor["id"],
            ),
        )
        logger.info(
            "Merged model %s into %s on sha256 %s; %d location(s) moved across.",
            doomed["id"],
            survivor["id"],
            digest,
            moved,
        )
        return True
