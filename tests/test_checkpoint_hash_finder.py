"""Hashing a registered checkpoint, and the collision that must never raise.

``model.sha256`` is UNIQUE and filled in by a background task long after the row
is created, so two rows for one file - the same checkpoint in two folders, or
the duplicate an interrupted move is *designed* to leave behind - meet at the
moment the second one is hashed. Two rules under test throughout:

1. that meeting is a **merge**, decided in the finder's own task, and never an
   exception thrown out of a background worker;
2. the merge keeps **both locations**. One content row, two ``model_file`` rows.
   With the path stored inline there was nowhere to put the second one, so it
   was forgotten, re-registered by the next scan, re-hashed, and dropped again,
   once per scan cycle, at 24 GB a time.
"""

import hashlib
import os

import pytest

from pixlstash.hub.db import HubDatabase
from pixlstash.task_runner import TaskRunner
from pixlstash.tasks.checkpoint_hash_task import CheckpointHashTask
from pixlstash.tasks.missing_checkpoint_hash_finder import MissingCheckpointHashFinder
from pixlstash.work_planner import WorkPlanner


@pytest.fixture
def hub(tmp_path):
    database = HubDatabase(str(tmp_path / "hub.db"))
    yield database
    database.close()


def register(
    hub, path, *, sha256=None, base_model=None, state="present", file_kind="checkpoint"
):
    """Register one checkpoint the way a scan would: content row plus location."""
    path = str(path)
    folder, relpath = os.path.split(path)
    with hub.transaction() as conn:
        folder_row = conn.execute(
            "SELECT id FROM model_folder WHERE path = ?", (folder,)
        ).fetchone()
        folder_id = (
            int(folder_row["id"])
            if folder_row
            else int(
                conn.execute(
                    "INSERT INTO model_folder (path, kind, movable, created_at) "
                    "VALUES (?, 'user', 'per_item', 'now')",
                    (folder,),
                ).lastrowid
            )
        )
        model_id = int(
            conn.execute(
                "INSERT INTO model (file_kind, kind, filename, file_size, sha256, "
                "base_model, provenance, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'external', 'now')",
                (
                    file_kind,
                    # The schema demands an algorithm for an adapter and forbids
                    # guessing one for anything else.
                    "lora" if file_kind == "adapter" else None,
                    relpath,
                    os.path.getsize(path) if os.path.exists(path) else None,
                    sha256,
                    base_model,
                ),
            ).lastrowid
        )
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state, "
            "seen_at) VALUES (?, ?, ?, ?, 'now')",
            (model_id, folder_id, relpath, state),
        )
    return model_id


def rows(hub):
    return hub.fetchall("SELECT * FROM model ORDER BY id")


def locations(hub):
    return hub.fetchall("SELECT * FROM model_file ORDER BY model_folder_id, relpath")


def write_model(path, content=b"a base model, notionally 24 GB"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class TestHashing:
    def test_a_registered_checkpoint_gets_its_digest(self, hub, tmp_path):
        path = write_model(tmp_path / "sdxl.safetensors")
        checkpoint_id = register(hub, path)

        result = CheckpointHashTask(hub, [(checkpoint_id, str(path))]).run()

        stored = rows(hub)
        assert result["hashed"] == 1
        assert stored[0]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert stored[0]["hashed_at"] is not None

    def test_an_unreadable_file_defers_instead_of_raising(self, hub, tmp_path):
        path = tmp_path / "gone.safetensors"
        checkpoint_id = register(hub, write_model(path))
        os.remove(path)

        result = CheckpointHashTask(hub, [(checkpoint_id, str(path))]).run()

        assert result["deferred"] == [checkpoint_id]
        assert rows(hub)[0]["sha256"] is None


class TestCollisionIsAMerge:
    def test_two_paths_one_file_becomes_one_row_with_two_locations(self, hub, tmp_path):
        """The reason ``model_file`` exists.

        This fails outright against the pre-reshape shape, where the surviving
        row had one ``local_path`` column and the second path was simply gone.
        """
        first = write_model(tmp_path / "one" / "sdxl.safetensors")
        second = write_model(tmp_path / "two" / "sdxl.safetensors")
        first_id = register(hub, first)
        second_id = register(hub, second)

        result = CheckpointHashTask(
            hub, [(first_id, str(first)), (second_id, str(second))]
        ).run()

        assert result["merged"] == 1
        surviving = rows(hub)
        assert [row["id"] for row in surviving] == [first_id]
        assert surviving[0]["sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()
        # Both paths survive, pointing at the one content row.
        held = locations(hub)
        assert len(held) == 2
        assert {row["model_id"] for row in held} == {first_id}

    def test_a_merged_checkpoint_is_never_handed_out_again(self, hub, tmp_path):
        # The loop the forgotten path caused: re-registered, re-hashed, re-merged,
        # forever, once per scan cycle. Both paths are now known, so the finder's
        # `sha256 IS NULL` query stops matching after the first pass.
        first = write_model(tmp_path / "one" / "sdxl.safetensors")
        second = write_model(tmp_path / "two" / "sdxl.safetensors")
        register(hub, first)
        register(hub, second)
        finder = MissingCheckpointHashFinder(hub)

        handed = []
        while (task := finder.find_task()) is not None:
            handed.extend(task.params["checkpoint_ids"])
            task.run()
            finder.on_task_complete(task, None)

        assert len(handed) == 2
        assert len(rows(hub)) == 1
        assert len(locations(hub)) == 2
        assert finder.find_task() is None

    def test_the_older_row_survives_even_when_it_is_hashed_second(self, hub, tmp_path):
        # The second row wins the race to the UNIQUE column, so the merge has to
        # drop the row that already holds the hash before writing it onto the
        # older one. Same outcome either way, which is the point.
        first = write_model(tmp_path / "one" / "a.safetensors")
        second = write_model(tmp_path / "two" / "b.safetensors")
        first_id = register(hub, first)
        second_id = register(hub, second)

        CheckpointHashTask(
            hub, [(second_id, str(second)), (first_id, str(first))]
        ).run()

        surviving = rows(hub)
        assert [row["id"] for row in surviving] == [first_id]
        assert surviving[0]["sha256"] is not None
        assert {row["model_id"] for row in locations(hub)} == {first_id}

    def test_a_merge_that_empties_a_run_leaves_no_stack_of_one(self, hub, tmp_path):
        # A duplicate can be one file of a run. The losing row is deleted here,
        # which takes a member out of a stack without the stack module being
        # asked - and a run of two left with one file is a grouping the shelf
        # draws as a plain row and nobody can see or undo.
        first = write_model(tmp_path / "one" / "a.safetensors")
        second = write_model(tmp_path / "two" / "b.safetensors")
        first_id = register(hub, first)
        second_id = register(hub, second)
        other_id = register(hub, write_model(tmp_path / "three" / "c.safetensors"))
        with hub.transaction() as conn:
            stack_id = int(
                conn.execute(
                    "INSERT INTO adapter_stack (name, created_at, updated_at) "
                    "VALUES ('Run', 'now', 'now')"
                ).lastrowid
            )
            for position, model_id in enumerate((other_id, second_id)):
                conn.execute(
                    "UPDATE model SET stack_id = ?, stack_position = ? WHERE id = ?",
                    (stack_id, position, model_id),
                )

        CheckpointHashTask(
            hub, [(first_id, str(first)), (second_id, str(second))]
        ).run()

        survivor = hub.fetchone(
            "SELECT stack_id, stack_position FROM model WHERE id = ?", (other_id,)
        )
        assert survivor["stack_id"] is None
        assert survivor["stack_position"] is None
        assert (
            hub.fetchone("SELECT id FROM adapter_stack WHERE id = ?", (stack_id,))
            is None
        )

    def test_the_merge_keeps_what_only_the_dropped_row_knew(self, hub, tmp_path):
        first = write_model(tmp_path / "one" / "a.safetensors")
        second = write_model(tmp_path / "two" / "b.safetensors")
        first_id = register(hub, first)
        second_id = register(hub, second, base_model="flux.1-dev")

        CheckpointHashTask(
            hub, [(first_id, str(first)), (second_id, str(second))]
        ).run()

        assert rows(hub)[0]["base_model"] == "flux.1-dev"

    def test_the_merge_never_overwrites_what_the_survivor_already_knew(
        self, hub, tmp_path
    ):
        # COALESCE on the survivor's own value, not on the dropped one: a merge
        # fills blanks and never replaces a curated field.
        first = write_model(tmp_path / "one" / "a.safetensors")
        second = write_model(tmp_path / "two" / "b.safetensors")
        first_id = register(hub, first, base_model="typed by hand")
        second_id = register(hub, second, base_model="flux.1-dev")

        CheckpointHashTask(
            hub, [(first_id, str(first)), (second_id, str(second))]
        ).run()

        surviving = rows(hub)[0]
        assert surviving["base_model"] == "typed by hand"
        assert surviving["filename"] == "a.safetensors"

    def test_a_merge_where_the_survivors_file_is_gone_still_keeps_both_rows(
        self, hub, tmp_path
    ):
        # The interrupted-move residue: the copy is real and the original is
        # already unlinked. Nothing here asks the filesystem anything -- both
        # locations are recorded and the next scan flips the dead one to
        # `missing` on its own.
        first = write_model(tmp_path / "one" / "a.safetensors")
        second = write_model(tmp_path / "two" / "b.safetensors")
        first_id = register(hub, first)
        second_id = register(hub, second)
        digest = hashlib.sha256(first.read_bytes()).hexdigest()
        os.remove(first)
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE model SET sha256 = ?, hashed_at = 'then' WHERE id = ?",
                (digest, first_id),
            )

        CheckpointHashTask(hub, [(second_id, str(second))]).run()

        assert [row["id"] for row in rows(hub)] == [first_id]
        assert len(locations(hub)) == 2

    def test_rehashing_a_row_that_already_holds_the_digest_changes_nothing(
        self, hub, tmp_path
    ):
        path = write_model(tmp_path / "a.safetensors")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checkpoint_id = register(hub, path, sha256=digest)

        result = CheckpointHashTask(hub, [(checkpoint_id, str(path))]).run()

        assert result["deferred"] == []
        assert [row["id"] for row in rows(hub)] == [checkpoint_id]
        assert rows(hub)[0]["sha256"] == digest


class TestFinder:
    def test_it_selects_only_unhashed_rows_that_are_present(self, hub, tmp_path):
        wanted = register(hub, write_model(tmp_path / "a.safetensors"))
        register(hub, write_model(tmp_path / "b.safetensors"), sha256="already")
        # A row whose only copy is gone has nothing to read. Handing it out
        # would defer it for the session over a drive that is merely unplugged.
        register(hub, write_model(tmp_path / "c.safetensors"), state="unreachable")

        task = MissingCheckpointHashFinder(hub).find_task()

        assert task.params["checkpoint_ids"] == [wanted]

    def test_a_folder_pixlstash_declared_is_never_handed_out(self, hub, tmp_path):
        """A declared root is described by an index, not walked, so its
        `relpath` is whatever the index calls one entry - for the HuggingFace
        cache that is `models--org--name`, a DIRECTORY. Those rows are the
        owner's to reclassify now (`builtin_caches`), so one corrected to
        `checkpoint` would match on `file_kind` and send the worker to open a
        directory, fail, and defer it on every start."""
        cache = tmp_path / "hf"
        (cache / "models--krea--Krea-2-Raw").mkdir(parents=True)
        wanted = register(hub, write_model(tmp_path / "mine.safetensors"))
        register(hub, cache / "models--krea--Krea-2-Raw")
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE model_folder SET owner = 'pixlstash' WHERE path = ?",
                (str(cache),),
            )

        task = MissingCheckpointHashFinder(hub).find_task()

        assert task.params["checkpoint_ids"] == [wanted]
        # And it is not counted as work still owed, or the shelf would report a
        # queue that can never drain.
        assert MissingCheckpointHashFinder(hub).progress() == (1, 1)

    def test_the_path_it_hands_out_is_the_folder_plus_the_relpath(self, hub, tmp_path):
        path = write_model(tmp_path / "sdxl.safetensors")
        register(hub, path)

        task = MissingCheckpointHashFinder(hub).find_task()
        task.run()

        assert rows(hub)[0]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()

    def test_a_model_at_two_locations_is_handed_out_once(self, hub, tmp_path):
        # One file read is the unit of work, not one path.
        path = write_model(tmp_path / "sdxl.safetensors")
        model_id = register(hub, path)
        with hub.transaction() as conn:
            folder_id = int(
                conn.execute(
                    "INSERT INTO model_folder (path, kind, movable, created_at) "
                    "VALUES (?, 'user', 'per_item', 'now')",
                    (str(tmp_path / "elsewhere"),),
                ).lastrowid
            )
            conn.execute(
                "INSERT INTO model_file (model_id, model_folder_id, relpath, state, "
                "seen_at) VALUES (?, ?, 'sdxl.safetensors', 'present', 'now')",
                (model_id, folder_id),
            )

        task = MissingCheckpointHashFinder(hub).find_task()

        assert task.params["checkpoint_ids"] == [model_id]

    def test_it_stops_when_there_is_nothing_left(self, hub, tmp_path):
        finder = MissingCheckpointHashFinder(hub)
        register(hub, write_model(tmp_path / "a.safetensors"), sha256="already")

        assert finder.find_task() is None

    def test_a_batch_is_bounded(self, hub, tmp_path):
        for index in range(CheckpointHashTask.BATCH_SIZE + 3):
            register(
                hub, write_model(tmp_path / f"{index}.safetensors", bytes([index]))
            )

        task = MissingCheckpointHashFinder(hub).find_task()

        assert len(task.params["checkpoint_ids"]) == CheckpointHashTask.BATCH_SIZE

    def test_a_row_that_could_not_be_hashed_is_not_handed_out_again(
        self, hub, tmp_path
    ):
        # Otherwise one broken path keeps the planner submitting a task every
        # cycle, forever, for work that cannot succeed.
        path = tmp_path / "gone.safetensors"
        checkpoint_id = register(hub, write_model(path))
        os.remove(path)
        finder = MissingCheckpointHashFinder(hub)

        task = finder.find_task()
        task.run()
        finder.on_task_complete(task, None)

        assert task.params["checkpoint_ids"] == [checkpoint_id]
        assert finder.find_task() is None

    def test_a_batch_still_in_flight_is_not_handed_out_again(self, hub, tmp_path):
        # WorkPlanner.on_task_complete decrements the inflight count under its
        # lock and calls the finder's callback afterwards, so a find_task lands
        # in between with _deferred still empty. Without the handed-out set that
        # re-issues the identical batch, which for a checkpoint is a second
        # multi-gigabyte read of a file that just failed to hash.
        register(hub, write_model(tmp_path / "a.safetensors"))
        finder = MissingCheckpointHashFinder(hub)

        task = finder.find_task()
        assert task is not None

        assert finder.find_task() is None, (
            "the finder re-issued a batch whose result had not come back yet"
        )

    def test_the_batch_is_released_once_its_result_is_in(self, hub, tmp_path):
        # The positive control: holding rows back forever would strand a
        # checkpoint whose hash simply has not been written yet, which is
        # over-blocking and its own regression.
        path = write_model(tmp_path / "a.safetensors")
        checkpoint_id = register(hub, path)
        finder = MissingCheckpointHashFinder(hub)

        task = finder.find_task()
        finder.on_task_complete(task, RuntimeError("hub went away"))
        # The row is deferred by the failure, so clear that to isolate the
        # release: what is under test is that _handed_out no longer holds it.
        finder._deferred.clear()

        again = finder.find_task()
        assert again is not None
        assert again.params["checkpoint_ids"] == [checkpoint_id]

    def test_a_failed_task_defers_its_whole_batch(self, hub, tmp_path):
        register(hub, write_model(tmp_path / "a.safetensors"))
        finder = MissingCheckpointHashFinder(hub)
        task = finder.find_task()

        finder.on_task_complete(task, RuntimeError("hub went away"))

        assert finder.find_task() is None

    def test_a_planner_stop_leaves_the_batch_eligible(self, hub, tmp_path):
        # A task the planner found but never submitted did not fail, it never
        # ran. Releasing its claims is right; deferring its rows for the rest of
        # the session is not, and Vault.stop() takes this path on every restore.
        checkpoint_id = register(hub, write_model(tmp_path / "a.safetensors"))
        finder = MissingCheckpointHashFinder(hub)
        planner = WorkPlanner(task_runner=_NeverSubmits(), task_finders=[finder])
        real_find_task = finder.find_task

        def find_then_stop():
            task = real_find_task()
            planner._stop.set()
            return task

        finder.find_task = find_then_stop
        assert planner._run_finders_once() is False
        finder.find_task = real_find_task

        assert finder._handed_out == set(), "the claim was not released"
        assert finder._deferred == set(), "a task that never ran was deferred"
        again = finder.find_task()
        assert again is not None
        assert again.params["checkpoint_ids"] == [checkpoint_id]

    def test_a_task_cancelled_off_the_queue_leaves_the_batch_eligible(
        self, hub, tmp_path
    ):
        # The common path: Vault.stop() and every full restore drain the queues.
        checkpoint_id = register(hub, write_model(tmp_path / "a.safetensors"))
        finder = MissingCheckpointHashFinder(hub)
        # No workers are started, so the task can only leave through the drain.
        runner = TaskRunner(name="checkpoint-cancel-test", num_workers=1)
        planner = WorkPlanner(task_runner=runner, task_finders=[finder])
        runner.add_task_complete_callback(planner.on_task_complete)

        try:
            assert planner._run_finders_once() is True
            assert runner.cancel_pending_tasks() == 1

            assert finder._handed_out == set(), "the claim was not released"
            assert finder._deferred == set(), "a cancelled task was deferred"
            again = finder.find_task()
            assert again is not None
            assert again.params["checkpoint_ids"] == [checkpoint_id]
        finally:
            runner.stop()


class _NeverSubmits:
    """A runner stand-in for the stop-before-submit path, which never calls it."""

    def submit(self, task):
        raise AssertionError("the planner submitted a task after it had stopped")


class TestProgress:
    """What the task manager is fed while this worker is running.

    Hashing 57 GB is genuinely minutes of disk-bound work, so the row is
    legitimately active for a long time. It looked stuck rather than busy
    because it reported no progress at all: the snapshot fell through to the
    generic branch, which advertised the *picture library's* total and a
    hardcoded zero remaining. "N / N, nothing left" on a row that says running
    is the report a healthy long task must never produce.
    """

    def test_it_counts_the_shelf_and_not_the_picture_library(self, hub, tmp_path):
        register(hub, write_model(tmp_path / "a.safetensors"))
        register(hub, write_model(tmp_path / "b.safetensors", b"b"))
        register(hub, write_model(tmp_path / "c.safetensors", b"c"), sha256="done")

        assert MissingCheckpointHashFinder(hub).progress() == (3, 2)

    def test_an_adapter_the_scan_already_hashed_is_not_in_the_denominator(
        self, hub, tmp_path
    ):
        # Adapters are hashed inline by the scan and are never this worker's
        # business. Counting them would park the bar at ~100% on any real LoRA
        # folder, which is the same "nothing left to do" lie by another route.
        register(hub, write_model(tmp_path / "a.safetensors"))
        register(
            hub,
            write_model(tmp_path / "lora.safetensors", b"lora"),
            sha256="hashed-by-the-scan",
            file_kind="adapter",
        )

        assert MissingCheckpointHashFinder(hub).progress() == (1, 1)

    def test_a_row_with_no_present_copy_is_not_counted(self, hub, tmp_path):
        # find_task() skips it, so counting it would leave the bar permanently
        # short of its total over a drive that is merely unplugged.
        register(hub, write_model(tmp_path / "gone.safetensors"), state="unreachable")

        assert MissingCheckpointHashFinder(hub).progress() == (0, 0)

    def test_it_never_reports_nothing_left_while_a_batch_is_still_owed(
        self, hub, tmp_path
    ):
        """The invariant that makes the number worth showing.

        Whatever ``find_task`` would still hand out has to appear in the
        remaining count, including rows deferred for the session: those are
        work this process refuses to retry, not work that finished.
        """
        for index in range(3):
            register(
                hub, write_model(tmp_path / f"{index}.safetensors", bytes([index]))
            )
        finder = MissingCheckpointHashFinder(hub)

        seen = []
        while (task := finder.find_task()) is not None:
            _total, pending = finder.progress()
            seen.append(pending)
            assert pending > 0, "progress said nothing was left while work was in hand"
            task.run()
            finder.on_task_complete(task, None)

        assert seen, "the finder never handed anything out"
        assert finder.progress() == (3, 0)
