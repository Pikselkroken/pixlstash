"""Invariants of the model-shelf scan that no other test defends.

Written from an adversarial verification of B4 rather than alongside it, so the
tests here are the ones a mutation sweep found nothing objecting to: break the
invariant in the source and every other test still passes.

Four of them were marked ``xfail(strict=True)`` when this suite was written,
because the behaviour they describe was what the design promised and *not* what
the code did. All four are now plain tests: D1 (an unreadable subdirectory read
as a deletion), D2 (two concurrent scans marking everything missing), D3 (a
corrected ``kind`` reverted on rescan) and D4 (a checkpoint at two paths
re-hashed forever) are fixed. A fifth, the size-only re-hash short circuit, was
pinned here as an accepted blind spot and has since been closed by comparing
``st_mtime_ns``; the test now asserts the closure rather than the hole.
"""

import hashlib
import json
import os
import sqlite3
import struct
import threading

import pytest

from pixlstash.hub.db import HubDatabase
from pixlstash.services import model_folder_scanner as scanner_module
from pixlstash.services.model_folder_scanner import (
    STATE_MISSING,
    STATE_PRESENT,
    STATE_UNREACHABLE,
    ModelFolderScanner,
)
from pixlstash.tasks.checkpoint_hash_task import CheckpointHashTask
from pixlstash.tasks.missing_checkpoint_hash_finder import MissingCheckpointHashFinder

SKIP_AS_ROOT = pytest.mark.skipif(
    hasattr(os, "getuid") and os.getuid() == 0,
    reason="root ignores the permission bits this test removes",
)


def _tensor(shape):
    return {"dtype": "F16", "shape": list(shape), "data_offsets": [0, 0]}


def _write_safetensors(path, tensors, metadata=None):
    header = dict(tensors)
    if metadata is not None:
        header["__metadata__"] = metadata
    blob = json.dumps(header).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(blob)) + blob)
    return str(path)


def write_adapter(path, *, name=None, pad=0, filler="A"):
    """An adapter whose bytes depend on *filler* but whose size does not."""
    tensors = {f"blocks.{i}.lora_A.weight": _tensor([8, 16]) for i in range(2 + pad)}
    tensors["blocks.0.lora_B.weight"] = _tensor([16, 8])
    metadata = {"format": "pt", "filler": filler}
    if name:
        metadata["ss_output_name"] = name
    return _write_safetensors(path, tensors, metadata)


def write_checkpoint(path):
    return _write_safetensors(path, {"model.weight": _tensor([40000, 40000])})


@pytest.fixture
def hub(tmp_path):
    database = HubDatabase(str(tmp_path / "hub.db"))
    yield database
    database.close()


@pytest.fixture
def scanner(hub):
    return ModelFolderScanner(hub)


def register_folder(hub, path, kind="user"):
    with hub.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO model_folder (path, kind, movable, created_at) "
            "VALUES (?, ?, 'per_item', '2026-08-09T00:00:00+00:00')",
            (str(path), kind),
        )
        return int(cursor.lastrowid)


def states(hub):
    return {
        (row["model_folder_id"], row["relpath"]): row["state"]
        for row in hub.fetchall("SELECT * FROM model_file")
    }


def only_model(hub):
    rows = hub.fetchall("SELECT * FROM model")
    assert len(rows) == 1, rows
    return rows[0]


def digest_at(hub, folder_id, relpath):
    row = hub.fetchone(
        "SELECT m.sha256 FROM model_file mf JOIN model m ON m.id = mf.model_id "
        "WHERE mf.model_folder_id = ? AND mf.relpath = ?",
        (folder_id, relpath),
    )
    return row["sha256"]


def checkpoints(hub):
    return hub.fetchall("SELECT * FROM model WHERE file_kind = 'checkpoint'")


class TestWeCouldNotLookIsNotGone:
    """``missing`` is a fact. Anything the scan could not read is not one."""

    @SKIP_AS_ROOT
    def test_an_unreadable_subdirectory_does_not_declare_its_files_missing(
        self, hub, scanner, tmp_path
    ):
        # Was D1. `os.walk` swallowed the unreadable subdirectory, so its rows
        # were swept to `missing` as if the files had been deleted: a registered
        # folder with a network mount under it reported the whole mount gone the
        # moment it dropped, which is exactly what the module docstring promises
        # never to do. `_walk` now passes an `onerror` handler.
        folder = tmp_path / "models"
        (folder / "nas").mkdir(parents=True)
        write_adapter(folder / "local.safetensors")
        write_adapter(folder / "nas" / "remote.safetensors", pad=1)
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")

        os.chmod(folder / "nas", 0o000)
        try:
            scanner.scan_folder(folder_id, str(folder), "user")
        finally:
            os.chmod(folder / "nas", 0o755)

        remote = states(hub)[(folder_id, os.path.join("nas", "remote.safetensors"))]
        assert remote != STATE_MISSING
        assert remote == STATE_UNREACHABLE

    @SKIP_AS_ROOT
    def test_a_folder_that_exists_but_cannot_be_read_is_unreachable(
        self, hub, scanner, tmp_path
    ):
        # The other test for this removes the directory entirely, which also
        # passes if the readability check is dropped and only isdir() is kept.
        folder = tmp_path / "usb"
        folder.mkdir()
        write_adapter(folder / "a.safetensors")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")

        os.chmod(folder, 0o000)
        try:
            result = scanner.scan_folder(folder_id, str(folder), "user")
        finally:
            os.chmod(folder, 0o755)

        assert result.state == STATE_UNREACHABLE
        assert set(states(hub).values()) == {STATE_UNREACHABLE}

    def test_a_scan_that_dies_mid_sweep_flips_nothing(self, hub, scanner, tmp_path):
        # The missing sweep runs last and all at once, so an interrupted scan
        # must leave every row exactly as it found it rather than declaring the
        # part it never reached gone.
        folder = tmp_path / "loras"
        folder.mkdir()
        for index in range(6):
            write_adapter(folder / f"{index}.safetensors", pad=index)
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")

        def die(*_args, **_kwargs):
            raise KeyboardInterrupt("simulated crash mid-sweep")

        original = ModelFolderScanner._write_batch
        ModelFolderScanner._write_batch = die
        try:
            with pytest.raises(KeyboardInterrupt):
                scanner.scan_folder(folder_id, str(folder), "user")
        finally:
            ModelFolderScanner._write_batch = original

        assert set(states(hub).values()) == {STATE_PRESENT}

    def test_a_folder_forgotten_mid_scan_ends_the_scan_without_a_traceback(
        self, hub, scanner, tmp_path
    ):
        # A scan of 1,800 adapters is minutes long and `DELETE
        # /model-folders/{id}` can land inside it. `model_file.model_folder_id`
        # is NOT NULL REFERENCES and the hub runs with foreign keys on, so the
        # next batch commits against a parent that is gone. Nothing is lost, so
        # the operator gets a message rather than an IntegrityError traceback
        # out of the rescan thread.
        folder = tmp_path / "loras"
        folder.mkdir()
        for index in range(3):
            write_adapter(folder / f"{index}.safetensors", pad=index)
        folder_id = register_folder(hub, folder)

        original = ModelFolderScanner._write_batch

        def forget_then_write(self, *args, **kwargs):
            with hub.transaction() as conn:
                conn.execute("DELETE FROM model_folder WHERE id = ?", (folder_id,))
            return original(self, *args, **kwargs)

        ModelFolderScanner._write_batch = forget_then_write
        try:
            result = scanner.scan_folder(folder_id, str(folder), "user")
        finally:
            ModelFolderScanner._write_batch = original

        assert result.skipped, "the abandoned scan did not report itself skipped"
        assert result.state == STATE_MISSING
        assert hub.fetchall("SELECT * FROM model_file") == []

    def test_an_integrity_error_with_the_folder_still_there_is_still_raised(
        self, hub, scanner, tmp_path
    ):
        # The positive control for the clause above: swallowing every
        # IntegrityError would hide a real constraint defect behind the same
        # "the folder was forgotten" message.
        folder = tmp_path / "loras"
        folder.mkdir()
        write_adapter(folder / "a.safetensors")
        folder_id = register_folder(hub, folder)

        original = ModelFolderScanner._write_batch

        def die(*_args, **_kwargs):
            raise sqlite3.IntegrityError("a genuine constraint violation")

        ModelFolderScanner._write_batch = die
        try:
            with pytest.raises(sqlite3.IntegrityError):
                scanner.scan_folder(folder_id, str(folder), "user")
        finally:
            ModelFolderScanner._write_batch = original

    def test_emptying_one_folder_leaves_the_other_folder_alone(
        self, hub, scanner, tmp_path
    ):
        kept = tmp_path / "kept"
        emptied = tmp_path / "emptied"
        kept.mkdir()
        emptied.mkdir()
        write_adapter(kept / "a.safetensors")
        write_adapter(emptied / "b.safetensors", pad=1)
        kept_id = register_folder(hub, kept)
        emptied_id = register_folder(hub, emptied)
        scanner.scan_all()

        os.remove(emptied / "b.safetensors")
        scanner.scan_folder(emptied_id, str(emptied), "user")

        assert states(hub) == {
            (kept_id, "a.safetensors"): STATE_PRESENT,
            (emptied_id, "b.safetensors"): STATE_MISSING,
        }

    def test_two_concurrent_scans_do_not_flip_present_files_to_missing(
        self, hub, tmp_path
    ):
        # Was D2. The sweep was `WHERE seen_at != <this scan's stamp>`, so two
        # scans running at once each swept away the rows the other had just
        # stamped and a shelf whose files were all on disk read as entirely
        # missing (4 of 5 unsynchronised runs flipped all 60 files). The sweep
        # is `<` now, so a concurrent scan's newer stamp survives and a row is
        # only demoted by a scan that walked the folder and did not find it.
        folder = tmp_path / "loras"
        folder.mkdir()
        for index in range(4):
            write_adapter(folder / f"{index}.safetensors", pad=index)
        folder_id = register_folder(hub, folder)
        first = ModelFolderScanner(hub)
        second = ModelFolderScanner(hub)
        first.scan_folder(folder_id, str(folder), "user")

        # Deterministic interleaving: hold the first scan at its sweep until
        # the second has finished stamping every row with its own timestamp.
        at_the_sweep = threading.Event()
        second_done = threading.Event()
        original = ModelFolderScanner._mark_missing

        def gated(self, *args, **kwargs):
            if self is first:
                at_the_sweep.set()
                assert second_done.wait(10)
            return original(self, *args, **kwargs)

        ModelFolderScanner._mark_missing = gated
        try:
            worker = threading.Thread(
                target=first.scan_folder, args=(folder_id, str(folder), "user")
            )
            worker.start()
            assert at_the_sweep.wait(10)
            second.scan_folder(folder_id, str(folder), "user")
            second_done.set()
            worker.join(10)
        finally:
            ModelFolderScanner._mark_missing = original

        assert set(states(hub).values()) == {STATE_PRESENT}


class TestCurationSurvivesAScan:
    """A scan re-derives facts. It must never overwrite what a person chose."""

    def _scan_twice_with_edit(self, hub, scanner, tmp_path, column, value):
        first = tmp_path / "one"
        second = tmp_path / "two"
        first.mkdir()
        second.mkdir()
        write_adapter(first / "a.safetensors", name="From the file")
        write_adapter(second / "copy.safetensors", name="From the file")
        first_id = register_folder(hub, first)
        second_id = register_folder(hub, second)
        scanner.scan_folder(first_id, str(first), "user")
        with hub.transaction() as conn:
            conn.execute(f"UPDATE model SET {column} = ?", (value,))
        scanner.scan_folder(second_id, str(second), "user")
        return only_model(hub)

    def test_a_base_model_the_user_typed_is_not_overwritten(
        self, hub, scanner, tmp_path
    ):
        row = self._scan_twice_with_edit(
            hub, scanner, tmp_path, "base_model", "flux.1-dev"
        )
        assert row["base_model"] == "flux.1-dev"

    def test_trigger_words_the_user_typed_are_not_overwritten(
        self, hub, scanner, tmp_path
    ):
        row = self._scan_twice_with_edit(
            hub, scanner, tmp_path, "trigger_words", '["ohwx"]'
        )
        assert json.loads(row["trigger_words"]) == ["ohwx"]

    def test_a_training_step_the_user_typed_is_not_overwritten(
        self, hub, scanner, tmp_path
    ):
        row = self._scan_twice_with_edit(hub, scanner, tmp_path, "training_step", 1500)
        assert row["training_step"] == 1500

    def test_re_adding_a_folder_relinks_by_sha256_with_curation_intact(
        self, hub, scanner, tmp_path
    ):
        # Definition of done, item 4. Folder removal drops the location rows and
        # keeps the content row, so the re-add must find the same model by hash
        # rather than mint a second one.
        folder = tmp_path / "loras"
        folder.mkdir()
        write_adapter(folder / "a.safetensors", name="From the file")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        digest = only_model(hub)["sha256"]
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE model SET display_name = 'Clementine', "
                "base_model = 'flux.1-dev', trigger_words = '[\"ohwx\"]'"
            )
            conn.execute(
                "DELETE FROM model_file WHERE model_folder_id = ?", (folder_id,)
            )
            conn.execute("DELETE FROM model_folder WHERE id = ?", (folder_id,))

        readded_id = register_folder(hub, folder)
        scanner.scan_folder(readded_id, str(folder), "user")

        assert readded_id != folder_id
        row = only_model(hub)
        assert row["sha256"] == digest
        assert row["display_name"] == "Clementine"
        assert row["base_model"] == "flux.1-dev"
        assert json.loads(row["trigger_words"]) == ["ohwx"]
        assert states(hub) == {(readded_id, "a.safetensors"): STATE_PRESENT}

    def test_a_kind_the_user_corrected_survives_a_re_add(self, hub, scanner, tmp_path):
        # Was D3. `kind` was the one column the upsert wrote without COALESCE,
        # so a kind the user corrected was reverted to the parser's guess the
        # next time the file was hashed. A correction that does not survive a
        # folder being re-added is not a correction.
        folder = tmp_path / "loras"
        folder.mkdir()
        write_adapter(folder / "a.safetensors")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        with hub.transaction() as conn:
            conn.execute("UPDATE model SET kind = 'dora'")
            conn.execute(
                "DELETE FROM model_file WHERE model_folder_id = ?", (folder_id,)
            )
            conn.execute("DELETE FROM model_folder WHERE id = ?", (folder_id,))

        scanner.scan_folder(register_folder(hub, folder), str(folder), "user")

        assert only_model(hub)["kind"] == "dora"


class TestScanCostAndIdentity:
    def test_the_no_rehash_short_circuit_only_trusts_this_folders_own_row(
        self, hub, scanner, tmp_path
    ):
        # Two folders, one relpath, same size, different bytes. Trusting the
        # other folder's row would file the second file under the first one's
        # digest, which is a wrong identity in the column Civitai lookup and the
        # public `{sha256}/file` route both resolve on.
        first = tmp_path / "one"
        second = tmp_path / "two"
        first.mkdir()
        second.mkdir()
        write_adapter(first / "a.safetensors", filler="A")
        path = write_adapter(second / "a.safetensors", filler="B")
        first_id = register_folder(hub, first)
        second_id = register_folder(hub, second)
        assert os.path.getsize(first / "a.safetensors") == os.path.getsize(path)

        scanner.scan_folder(first_id, str(first), "user")
        scanner.scan_folder(second_id, str(second), "user")

        located = states(hub)
        assert set(located) == {
            (first_id, "a.safetensors"),
            (second_id, "a.safetensors"),
        }
        expected = hashlib.sha256(open(path, "rb").read()).hexdigest()
        assert digest_at(hub, second_id, "a.safetensors") == expected

    def test_a_size_preserving_edit_is_caught_by_the_mtime(
        self, hub, scanner, tmp_path
    ):
        # This was pinned as the accepted blind spot in the no-rehash short
        # circuit: the stored digest was the OLD file's, so `model.sha256` could
        # disagree with the bytes on disk, in an interop column that B5 serves
        # `{sha256}/file` from. Closing it costs one mtime comparison off a
        # `stat` the scan was already making, and B7 stores `file_mtime` anyway.
        folder = tmp_path / "loras"
        folder.mkdir()
        path = write_adapter(folder / "a.safetensors", filler="A")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        stale = only_model(hub)["sha256"]

        write_adapter(folder / "a.safetensors", filler="B")
        # The size is unchanged, so nothing but the mtime can tell these apart.
        assert (
            os.path.getsize(path)
            == hub.fetchone("SELECT file_size FROM model WHERE sha256 = ?", (stale,))[
                "file_size"
            ]
        )
        scanner.scan_folder(folder_id, str(folder), "user")

        on_disk = hashlib.sha256(open(path, "rb").read()).hexdigest()
        assert on_disk != stale
        assert digest_at(hub, folder_id, "a.safetensors") == on_disk

    def test_changing_one_copy_leaves_the_other_folders_digest_alone(
        self, hub, scanner, tmp_path
    ):
        # One content row legitimately holds many locations, so looking the row
        # up by `(folder, relpath)` and then writing to `model` BY ID cleared
        # the digest -- and overwrote the size -- for every OTHER path pointing
        # at it. `model.sha256` is the interop identity behind Civitai lookup
        # and the public `{sha256}/file` route, so the untouched copy in the
        # other folder ended up served under a digest naming bytes that are
        # only at the path that changed.
        first = tmp_path / "one"
        second = tmp_path / "two"
        first.mkdir()
        second.mkdir()
        write_checkpoint(first / "sdxl.safetensors")
        (second / "sdxl.safetensors").write_bytes(
            (first / "sdxl.safetensors").read_bytes()
        )
        first_id = register_folder(hub, first)
        second_id = register_folder(hub, second)
        scanner.scan_folder(first_id, str(first), "user")
        scanner.scan_folder(second_id, str(second), "user")
        finder = MissingCheckpointHashFinder(hub)
        while (task := finder.find_task()) is not None:
            task.run()
            finder.on_task_complete(task, None)
        # The merge the hash task performs: one row, two locations.
        shared = only_model(hub)
        assert shared["sha256"] is not None
        assert len(hub.fetchall("SELECT * FROM model_file")) == 2

        # Only folder one's copy changes.
        _write_safetensors(
            first / "sdxl.safetensors",
            {"model.weight": _tensor([40000, 40000]), "extra": _tensor([2])},
        )
        scanner.scan_folder(first_id, str(first), "user")

        untouched = os.path.join(str(second), "sdxl.safetensors")
        assert digest_at(hub, second_id, "sdxl.safetensors") == shared["sha256"]
        survivor = hub.fetchone(
            "SELECT file_size FROM model WHERE sha256 = ?", (shared["sha256"],)
        )
        assert survivor["file_size"] == os.path.getsize(untouched)
        # The changed copy forks onto its own row, back in the hash queue.
        assert digest_at(hub, first_id, "sdxl.safetensors") is None
        assert len(hub.fetchall("SELECT * FROM model")) == 2

    def test_an_unparseable_file_costs_the_same_on_every_scan(
        self, hub, scanner, tmp_path, monkeypatch
    ):
        # A corrupt file is retried forever by design. That is only affordable
        # while the retry is a header read: hashing it, or minting a row per
        # scan, would make one bad file cost the shelf something every sweep.
        folder = tmp_path / "loras"
        folder.mkdir()
        (folder / "truncated.safetensors").write_bytes(b"\x00\x01")
        folder_id = register_folder(hub, folder)
        hashed = []
        monkeypatch.setattr(
            scanner_module,
            "sha256_file",
            lambda path: hashed.append(path) or "",
        )

        results = [
            scanner.scan_folder(folder_id, str(folder), "user") for _ in range(3)
        ]

        assert [result.unreadable for result in results] == [1, 1, 1]
        assert hashed == []
        assert hub.fetchall("SELECT * FROM model") == []
        assert hub.fetchall("SELECT * FROM model_file") == []


class TestCheckpointCollision:
    def test_a_unique_violation_rolls_back_the_statement_not_the_transaction(self, hub):
        # The merge catches IntegrityError from the UPDATE and then keeps
        # writing in the same transaction. That is a claim about SQLite's
        # ON CONFLICT ABORT and about the hub connection being in implicit-BEGIN
        # mode, and both have to keep holding for the merge to be atomic. Under
        # `isolation_level=None` / `autocommit=True`, where that deprecated
        # parameter is heading in 3.12+, `with conn` is not a transaction at all.
        with hub.transaction() as conn:
            conn.execute(
                "INSERT INTO model (id, file_kind, filename, sha256, provenance, "
                "created_at) VALUES (1, 'checkpoint', 'a', 'taken', 'external', 'now')"
            )
            conn.execute(
                "INSERT INTO model (id, file_kind, filename, provenance, created_at) "
                "VALUES (2, 'checkpoint', 'b', 'external', 'now')"
            )

        with hub.transaction() as conn:
            conn.execute("UPDATE model SET filename = 'edited' WHERE id = 2")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE model SET sha256 = 'taken' WHERE id = 2")
            assert conn.in_transaction, (
                "the failed statement closed the transaction; the merge's "
                "DELETE and UPDATE would no longer be atomic"
            )
            conn.execute("DELETE FROM model WHERE id = 1")
            conn.execute("UPDATE model SET sha256 = 'taken' WHERE id = 2")

        rows = hub.fetchall("SELECT id, filename, sha256 FROM model")
        assert [dict(row) for row in rows] == [
            {"id": 2, "filename": "edited", "sha256": "taken"}
        ]

    def test_two_workers_racing_one_digest_never_raise(self, hub, scanner, tmp_path):
        first = tmp_path / "one"
        second = tmp_path / "two"
        first.mkdir()
        second.mkdir()
        write_checkpoint(first / "sdxl.safetensors")
        (second / "sdxl.safetensors").write_bytes(
            (first / "sdxl.safetensors").read_bytes()
        )
        first_id = register_folder(hub, first)
        second_id = register_folder(hub, second)
        scanner.scan_folder(first_id, str(first), "user")
        scanner.scan_folder(second_id, str(second), "user")
        ids = [row["id"] for row in checkpoints(hub)]
        assert len(ids) == 2

        failures = []
        start = threading.Barrier(2)

        def hash_one(model_id, path):
            try:
                start.wait(10)
                CheckpointHashTask(hub, [(model_id, str(path))]).run()
            except Exception as exc:  # noqa: BLE001 - the whole point is to see it
                failures.append(repr(exc))

        workers = [
            threading.Thread(
                target=hash_one, args=(ids[0], first / "sdxl.safetensors")
            ),
            threading.Thread(
                target=hash_one, args=(ids[1], second / "sdxl.safetensors")
            ),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(20)

        assert failures == []
        rows = checkpoints(hub)
        assert len(rows) == 1
        assert rows[0]["sha256"] is not None
        # Both locations survive the race, not just the winner's.
        assert len(hub.fetchall("SELECT * FROM model_file")) == 2

    def test_a_merge_where_neither_path_exists_still_settles(
        self, hub, scanner, tmp_path
    ):
        first = tmp_path / "one"
        second = tmp_path / "two"
        first.mkdir()
        second.mkdir()
        write_checkpoint(first / "sdxl.safetensors")
        (second / "sdxl.safetensors").write_bytes(
            (first / "sdxl.safetensors").read_bytes()
        )
        digest = hashlib.sha256((first / "sdxl.safetensors").read_bytes()).hexdigest()
        first_id = register_folder(hub, first)
        second_id = register_folder(hub, second)
        scanner.scan_folder(first_id, str(first), "user")
        scanner.scan_folder(second_id, str(second), "user")
        survivor, doomed = (row["id"] for row in checkpoints(hub))
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE model SET sha256 = ?, base_model = 'sdxl' WHERE id = ?",
                (digest, survivor),
            )
        os.remove(first / "sdxl.safetensors")

        result = CheckpointHashTask(
            hub, [(doomed, str(second / "sdxl.safetensors"))]
        ).run()

        assert result["merged"] == 1
        rows = checkpoints(hub)
        assert [row["id"] for row in rows] == [survivor]
        assert rows[0]["sha256"] == digest
        # Nothing either row knew is lost, even when neither path is live.
        assert rows[0]["base_model"] == "sdxl"

    def test_two_paths_holding_one_checkpoint_settle_after_one_merge(
        self, hub, scanner, tmp_path
    ):
        # Was D4. The merge dropped the second row and with it the only record
        # of the second path, because checkpoints had no location table. The
        # next scan re-registered that path with sha256 NULL, the finder hashed
        # it again, the merge dropped it again -- so a checkpoint legitimately
        # present at two paths was re-read in full on every scan cycle, forever,
        # and a 24 GB base model is the normal size here.
        first = tmp_path / "one"
        second = tmp_path / "two"
        first.mkdir()
        second.mkdir()
        write_checkpoint(first / "sdxl.safetensors")
        (second / "sdxl.safetensors").write_bytes(
            (first / "sdxl.safetensors").read_bytes()
        )
        first_id = register_folder(hub, first)
        second_id = register_folder(hub, second)

        def sweep():
            scanner.scan_folder(first_id, str(first), "user")
            scanner.scan_folder(second_id, str(second), "user")
            finder = MissingCheckpointHashFinder(hub)
            hashed = []
            while (task := finder.find_task()) is not None:
                hashed.extend(task.params["checkpoint_ids"])
                task.run()
                finder.on_task_complete(task, None)
            return hashed

        sweep()
        assert len(checkpoints(hub)) == 1
        # Both paths kept, which is what stops the loop.
        assert len(hub.fetchall("SELECT * FROM model_file")) == 2

        assert sweep() == [], "the same checkpoint was handed out to be re-hashed"


class TestFinderBookkeeping:
    def test_the_batch_stays_bounded_once_rows_are_deferred(self, hub, tmp_path):
        # The SQL limit is widened by the deferred count so the batch can still
        # be filled. A deferred row that no longer exists (a merge dropped it)
        # widens the window without consuming it, so the slice is what actually
        # holds the batch size.
        folder_id = register_folder(hub, tmp_path)
        for index in range(CheckpointHashTask.BATCH_SIZE + 6):
            name = f"{index}.safetensors"
            (tmp_path / name).write_bytes(bytes([index]) * (index + 1))
            with hub.transaction() as conn:
                model_id = int(
                    conn.execute(
                        "INSERT INTO model (file_kind, filename, provenance, "
                        "created_at) VALUES ('checkpoint', ?, 'external', 'now')",
                        (name,),
                    ).lastrowid
                )
                conn.execute(
                    "INSERT INTO model_file (model_id, model_folder_id, relpath, "
                    "state, seen_at) VALUES (?, ?, ?, 'present', 'now')",
                    (model_id, folder_id, name),
                )
        finder = MissingCheckpointHashFinder(hub)
        finder._deferred.update({9000, 9001, 9002})

        task = finder.find_task()

        assert len(task.params["checkpoint_ids"]) == CheckpointHashTask.BATCH_SIZE
