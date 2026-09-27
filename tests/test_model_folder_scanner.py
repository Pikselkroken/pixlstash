"""What the model-folder scan records, and what it refuses to record.

The scan is the only thing that puts rows on the shelf, so the tests here are
about the claims the rest of the feature is built on:

1. **One content row, many location rows.** ``model`` says what a file is,
   ``model_file`` says where a copy of it lives, for a checkpoint exactly as
   much as for an adapter. Two copies of one file are one row with two
   locations.
2. **An adapter's identity is its full-file SHA-256**, computed on sight.
   A checkpoint registers instantly with no hash, because it may be 24 GB and
   the shelf must not stall behind it.
3. **Nothing is ever deleted by a scan.** A vanished file flips a state; a
   folder or subdirectory we could not read flips a *different* state, because
   "gone" and "we could not look" are different facts and only one of them is
   safe to act on.
4. **A scan re-derives facts and never overwrites choices.**
"""

import json
import os
import struct

import pytest

from pixlstash.hub.db import HubDatabase
from pixlstash.services import model_folder_scanner as scanner_module
from pixlstash.services.model_folder_scanner import (
    STATE_MISSING,
    STATE_PRESENT,
    STATE_REMOVED,
    STATE_UNREACHABLE,
    ModelFolderScanner,
    sha256_file,
)
from pixlstash.hub.schema import _backfill_base_model_canonical
from pixlstash.services.model_shelf_service import update_models

SKIP_AS_ROOT = pytest.mark.skipif(
    hasattr(os, "getuid") and os.getuid() == 0,
    reason="root ignores the permission bits this test removes",
)


def _tensor(shape):
    return {"dtype": "F16", "shape": list(shape), "data_offsets": [0, 0]}


def _write_safetensors(path, tensors, metadata=None):
    """Write a header-only safetensors file. The payload is never read."""
    header = dict(tensors)
    if metadata is not None:
        header["__metadata__"] = metadata
    blob = json.dumps(header).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(blob)) + blob)
    return str(path)


def write_adapter(path, *, name=None, base_model=None, triggers=None, pad=0):
    """A file with LoRA tensor markers, so it is *proven* to be an adapter."""
    tensors = {f"blocks.{i}.lora_A.weight": _tensor([8, 16]) for i in range(2 + pad)}
    tensors["blocks.0.lora_B.weight"] = _tensor([16, 8])
    metadata = {"format": "pt"}
    if name:
        metadata["ss_output_name"] = name
    if base_model:
        metadata["ss_base_model_version"] = base_model
    if triggers:
        metadata["ss_tag_frequency"] = json.dumps({"1_set": {t: 1 for t in triggers}})
    return _write_safetensors(path, tensors, metadata)


def write_checkpoint(path):
    """Marker-free and far over the checkpoint parameter threshold."""
    return _write_safetensors(path, {"model.weight": _tensor([40000, 40000])})


def write_unknown(path):
    """Marker-free and far under it: the band the shelf must show as unknown."""
    return _write_safetensors(path, {"vae.weight": _tensor([64, 64])})


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


def models(hub, file_kind=None):
    """Return ``{id: row}`` for the content rows, optionally of one kind."""
    if file_kind is None:
        rows = hub.fetchall("SELECT * FROM model")
    else:
        rows = hub.fetchall("SELECT * FROM model WHERE file_kind = ?", (file_kind,))
    return {row["id"]: row for row in rows}


def adapters(hub):
    return {row["sha256"]: row for row in models(hub, "adapter").values()}


def checkpoints(hub):
    return list(models(hub, "checkpoint").values())


def located(hub):
    """Return ``{relpath: row}`` for the location rows."""
    return {row["relpath"]: row for row in hub.fetchall("SELECT * FROM model_file")}


def digest_at(hub, relpath):
    row = hub.fetchone(
        "SELECT m.sha256 FROM model_file mf JOIN model m ON m.id = mf.model_id "
        "WHERE mf.relpath = ?",
        (relpath,),
    )
    return row["sha256"]


class TestAdapters:
    def test_an_adapter_is_keyed_by_its_full_file_sha256(self, hub, scanner, tmp_path):
        folder = tmp_path / "loras"
        folder.mkdir()
        path = write_adapter(folder / "jimmy.safetensors", name="Jimmy")
        folder_id = register_folder(hub, folder)

        scanner.scan_folder(folder_id, str(folder), "user")

        expected = sha256_file(path)
        rows = adapters(hub)
        assert set(rows) == {expected}
        assert rows[expected]["file_kind"] == "adapter"
        assert rows[expected]["kind"] == "lora"
        assert rows[expected]["display_name"] == "Jimmy"
        assert rows[expected]["provenance"] == "external"
        assert rows[expected]["file_size"] == os.path.getsize(path)

        row = located(hub)["jimmy.safetensors"]
        assert row["state"] == STATE_PRESENT
        assert row["model_id"] == rows[expected]["id"]
        assert row["file_mtime"] == os.stat(path).st_mtime_ns

    def test_metadata_the_file_carries_is_stored(self, hub, scanner, tmp_path):
        folder = tmp_path / "loras"
        folder.mkdir()
        write_adapter(
            folder / "a.safetensors", base_model="flux.1-dev", triggers=["ohwx"]
        )
        folder_id = register_folder(hub, folder)

        scanner.scan_folder(folder_id, str(folder), "user")

        row = next(iter(adapters(hub).values()))
        assert row["base_model"] == "flux.1-dev"
        assert json.loads(row["trigger_words"]) == ["ohwx"]
        assert row["param_count"] > 0

    def test_a_file_that_did_not_name_itself_is_left_unnamed(
        self, hub, scanner, tmp_path
    ):
        # `display_name` NULL is the "Needs a name" work queue. Deriving a name
        # here would fill that queue with guesses and make a guess
        # indistinguishable from a choice.
        folder = tmp_path / "loras"
        folder.mkdir()
        write_adapter(folder / "JimmyVehicle_000002750.safetensors")
        folder_id = register_folder(hub, folder)

        scanner.scan_folder(folder_id, str(folder), "user")

        assert next(iter(adapters(hub).values()))["display_name"] is None

    def test_nested_files_keep_their_relative_path(self, hub, scanner, tmp_path):
        folder = tmp_path / "loras"
        (folder / "flux" / "people").mkdir(parents=True)
        write_adapter(folder / "flux" / "people" / "a.safetensors")
        folder_id = register_folder(hub, folder)

        scanner.scan_folder(folder_id, str(folder), "user")

        assert set(located(hub)) == {os.path.join("flux", "people", "a.safetensors")}

    def test_one_file_in_two_folders_is_one_model_at_two_locations(
        self, hub, scanner, tmp_path
    ):
        first = tmp_path / "one"
        second = tmp_path / "two"
        first.mkdir()
        second.mkdir()
        write_adapter(first / "a.safetensors")
        write_adapter(second / "a.safetensors")
        first_id = register_folder(hub, first)
        second_id = register_folder(hub, second)

        scanner.scan_folder(first_id, str(first), "user")
        scanner.scan_folder(second_id, str(second), "user")

        assert len(models(hub)) == 1
        rows = hub.fetchall("SELECT model_folder_id FROM model_file")
        assert sorted(row["model_folder_id"] for row in rows) == [first_id, second_id]

    def test_a_name_the_user_typed_survives_the_file_turning_up_elsewhere(
        self, hub, scanner, tmp_path
    ):
        # The second folder re-upserts the same content row, and the file's own
        # metadata name would otherwise overwrite what the user chose. Curation
        # wins over anything a scan re-derives.
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
            conn.execute("UPDATE model SET display_name = 'Clementine'")

        scanner.scan_folder(second_id, str(second), "user")

        assert next(iter(models(hub).values()))["display_name"] == "Clementine"

    def test_only_safetensors_files_are_considered(self, hub, scanner, tmp_path):
        folder = tmp_path / "loras"
        folder.mkdir()
        (folder / "notes.txt").write_text("not a model")
        (folder / "old.ckpt").write_bytes(b"pickled")
        folder_id = register_folder(hub, folder)

        scanner.scan_folder(folder_id, str(folder), "user")

        assert models(hub) == {}


class TestCheckpointsAndUnknowns:
    def test_a_checkpoint_registers_with_no_hash_and_a_location_row(
        self, hub, scanner, tmp_path
    ):
        folder = tmp_path / "ckpt"
        folder.mkdir()
        write_checkpoint(folder / "sdxl.safetensors")
        folder_id = register_folder(hub, folder)

        scanner.scan_folder(folder_id, str(folder), "user")

        rows = checkpoints(hub)
        assert len(rows) == 1
        assert rows[0]["sha256"] is None
        assert rows[0]["hashed_at"] is None
        assert rows[0]["filename"] == "sdxl.safetensors"
        assert rows[0]["kind"] is None
        assert adapters(hub) == {}
        # The location row is what the superseded inline `local_path` could not
        # give a checkpoint: a folder reference, a state, and a tombstone.
        location = located(hub)["sdxl.safetensors"]
        assert location["model_folder_id"] == folder_id
        assert location["state"] == STATE_PRESENT
        assert location["model_id"] == rows[0]["id"]

    def test_a_checkpoint_in_two_folders_is_one_row_at_two_locations(
        self, hub, scanner, tmp_path
    ):
        # Not two content rows racing for one `sha256 UNIQUE`. Under the
        # superseded shape this was two `checkpoint` rows that the hash task had
        # to merge, losing one of the two paths every time.
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

        # Two rows here, one per location, because nothing has hashed them yet
        # and an unhashed checkpoint has no identity beyond where it was found.
        assert len(checkpoints(hub)) == 2
        rows = hub.fetchall("SELECT model_folder_id FROM model_file")
        assert sorted(row["model_folder_id"] for row in rows) == [first_id, second_id]

    def test_an_unknown_file_is_never_recorded_as_a_checkpoint(
        self, hub, scanner, tmp_path
    ):
        folder = tmp_path / "mixed"
        folder.mkdir()
        write_unknown(folder / "vae.safetensors")
        folder_id = register_folder(hub, folder)

        scanner.scan_folder(folder_id, str(folder), "user")

        assert checkpoints(hub) == []
        row = next(iter(models(hub).values()))
        assert row["file_kind"] == "unknown"
        # `kind` names an adapter algorithm. This file is not an adapter, so the
        # column carries nothing rather than a guess.
        assert row["kind"] is None
        # Hashed all the same: it is under the checkpoint threshold, so it is
        # cheap, and it gives the row an identity the shelf can link to.
        assert row["sha256"] is not None

    def test_a_file_kind_the_user_corrected_is_not_re_derived_away(
        self, hub, scanner, tmp_path
    ):
        # The whole point of storing `unknown` rather than guessing is that the
        # owner can correct it. The parser will keep saying `unknown` forever,
        # so a re-scan that rewrote `file_kind` would undo every correction.
        folder = tmp_path / "mixed"
        folder.mkdir()
        path = folder / "vae.safetensors"
        write_unknown(path)
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        with hub.transaction() as conn:
            conn.execute("UPDATE model SET file_kind = 'checkpoint'")

        # Re-hashed, not short-circuited, so the upsert really runs.
        os.utime(path, ns=(0, 0))
        scanner.scan_folder(folder_id, str(folder), "user")

        assert next(iter(models(hub).values()))["file_kind"] == "checkpoint"

    def test_a_resized_checkpoint_loses_its_stale_hash(self, hub, scanner, tmp_path):
        folder = tmp_path / "ckpt"
        folder.mkdir()
        path = folder / "sdxl.safetensors"
        write_checkpoint(path)
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        with hub.transaction() as conn:
            conn.execute("UPDATE model SET sha256 = 'deadbeef', hashed_at = 'then'")

        _write_safetensors(
            path, {"model.weight": _tensor([40000, 40000]), "extra": _tensor([2])}
        )
        scanner.scan_folder(folder_id, str(folder), "user")

        rows = checkpoints(hub)
        assert len(rows) == 1
        assert rows[0]["sha256"] is None
        assert rows[0]["hashed_at"] is None
        # Still one row at one location: the path is the identity here.
        assert len(located(hub)) == 1

    def test_a_hashed_checkpoint_keeps_its_hash_across_a_rescan(
        self, hub, scanner, tmp_path
    ):
        # The stale-hash clear must fire on a changed file and only then, or
        # every sweep would re-queue every checkpoint on the machine.
        folder = tmp_path / "ckpt"
        folder.mkdir()
        write_checkpoint(folder / "sdxl.safetensors")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        with hub.transaction() as conn:
            conn.execute("UPDATE model SET sha256 = 'abc', hashed_at = 'then'")

        scanner.scan_folder(folder_id, str(folder), "user")

        assert checkpoints(hub)[0]["sha256"] == "abc"

    def test_an_adapter_replaced_by_a_checkpoint_becomes_a_new_row(
        self, hub, scanner, tmp_path
    ):
        # The stale-hash clear used to run on whatever row the path pointed at
        # without looking at its `file_kind`, and the schema's
        # `CHECK (file_kind <> 'adapter' OR sha256 IS NOT NULL)` rejects a
        # cleared adapter. The IntegrityError rolled back the whole write batch
        # and escaped `scan_folder`, so the missing sweep and `last_checked`
        # never ran -- and nothing self-heals it, because a changed file never
        # matches the size-and-mtime fast path again.
        folder = tmp_path / "loras"
        folder.mkdir()
        path = folder / "thing.safetensors"
        write_adapter(path, name="Thing")
        write_adapter(folder / "bystander.safetensors", name="Bystander")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")

        write_checkpoint(path)
        scanner.scan_folder(folder_id, str(folder), "user")

        # A file whose kind changed at a path is a new identity, not the old row
        # with its hash cleared: that row still claimed `kind = 'lora'`.
        rows = models(hub)
        replaced = rows[located(hub)["thing.safetensors"]["model_id"]]
        assert replaced["file_kind"] == "checkpoint"
        assert replaced["kind"] is None
        assert replaced["sha256"] is None
        # The rest of the batch survived, and so did the sweeps that run after
        # it. Both were lost with the rolled-back transaction.
        assert located(hub)["bystander.safetensors"]["state"] == STATE_PRESENT
        folder_row = hub.fetchone(
            "SELECT last_checked FROM model_folder WHERE id = ?", (folder_id,)
        )
        assert folder_row["last_checked"] is not None

        # Settled: the third scan takes the fast path and changes nothing.
        scanner.scan_folder(folder_id, str(folder), "user")
        assert located(hub)["thing.safetensors"]["model_id"] == replaced["id"]


class TestStates:
    def test_a_vanished_file_goes_missing_and_keeps_its_model_row(
        self, hub, scanner, tmp_path
    ):
        folder = tmp_path / "loras"
        folder.mkdir()
        path = folder / "a.safetensors"
        write_adapter(path)
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        with hub.transaction() as conn:
            conn.execute("UPDATE model SET display_name = 'Named by hand'")

        os.remove(path)
        scanner.scan_folder(folder_id, str(folder), "user")

        assert located(hub)["a.safetensors"]["state"] == STATE_MISSING
        assert next(iter(models(hub).values()))["display_name"] == "Named by hand"

    def test_a_vanished_checkpoint_goes_missing_too(self, hub, scanner, tmp_path):
        # Free once a checkpoint has a location row: the same sweep covers it,
        # with no code that knows what a checkpoint is.
        folder = tmp_path / "ckpt"
        folder.mkdir()
        path = folder / "sdxl.safetensors"
        write_checkpoint(path)
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")

        os.remove(path)
        result = scanner.scan_folder(folder_id, str(folder), "user")

        assert result.missing == 1
        assert located(hub)["sdxl.safetensors"]["state"] == STATE_MISSING
        assert len(checkpoints(hub)) == 1

    def test_a_deliberately_removed_copy_is_not_re_labelled_missing(
        self, hub, scanner, tmp_path
    ):
        """The fact that survives the file (#1439).

        A copy the owner removed to keep one of several is recorded ``removed``,
        and the scan will keep finding it absent for the rest of the folder's
        life. Every sweep therefore skips it: without that, the first scan after
        the merge turns "I removed this on purpose" into the scanner's own
        "I looked and it was gone", and the distinction is destroyed by the one
        thing that runs unattended.
        """
        folder = tmp_path / "loras"
        folder.mkdir()
        path = folder / "a.safetensors"
        write_adapter(path)
        write_adapter(folder / "bystander.safetensors", pad=1)
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")

        os.remove(path)
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE model_file SET state = ? WHERE relpath = 'a.safetensors'",
                (STATE_REMOVED,),
            )

        result = scanner.scan_folder(folder_id, str(folder), "user")

        assert located(hub)["a.safetensors"]["state"] == STATE_REMOVED
        assert result.missing == 0, "the removed copy was swept as missing"
        assert located(hub)["bystander.safetensors"]["state"] == STATE_PRESENT

    def test_an_unreadable_folder_leaves_a_removed_copy_alone(
        self, hub, scanner, tmp_path
    ):
        """Unplugging the drive does not un-decide the removal.

        ``unreachable`` is "we could not look", and we do not need to look: that
        copy is gone because the owner said so, and a row that flipped would come
        back as ``missing`` on the next readable scan.
        """
        folder = tmp_path / "usb"
        folder.mkdir()
        write_adapter(folder / "a.safetensors")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        with hub.transaction() as conn:
            conn.execute("UPDATE model_file SET state = ?", (STATE_REMOVED,))

        os.remove(folder / "a.safetensors")
        folder.rmdir()
        result = scanner.scan_folder(folder_id, str(folder), "user")

        assert result.state == STATE_UNREACHABLE
        assert located(hub)["a.safetensors"]["state"] == STATE_REMOVED

    @SKIP_AS_ROOT
    def test_an_unreadable_subdirectory_leaves_a_removed_copy_alone(
        self, hub, scanner, tmp_path
    ):
        """The subtree sweep is a second query and needs its own assertion.

        A NAS mounted inside a registered folder flips only the rows under it, by
        a `relpath LIKE` predicate rather than by folder - so the folder-wide test
        above does not exercise this branch at all. A `removed` row flipped to
        `unreachable` here is worse than cosmetic: `unreachable` is in
        `_KEEPS_A_MODEL_ALIVE`, so it would wrongly block Forget, and the next
        readable scan's missing sweep - which guards `removed`, not
        `unreachable` - would write `missing` over it.
        """
        folder = tmp_path / "models"
        (folder / "nas").mkdir(parents=True)
        write_adapter(folder / "local.safetensors")
        write_adapter(folder / "nas" / "remote.safetensors", pad=1)
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        under = os.path.join("nas", "remote.safetensors")
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE model_file SET state = ? WHERE relpath = ?",
                (STATE_REMOVED, under),
            )

        os.chmod(folder / "nas", 0o000)
        try:
            result = scanner.scan_folder(folder_id, str(folder), "user")
        finally:
            os.chmod(folder / "nas", 0o755)

        rows = located(hub)
        assert rows[under]["state"] == STATE_REMOVED
        assert rows["local.safetensors"]["state"] == STATE_PRESENT
        assert result.unreachable == 0, "the removed copy was swept as unreachable"

    def test_an_unlistable_root_leaves_a_removed_copy_alone(
        self, hub, scanner, tmp_path
    ):
        """The subtree sweep's other branch, driven directly.

        `_mark_unreachable_subtrees` has two UPDATEs: one keyed on
        `relpath LIKE prefix` (above) and one with no path predicate at all, for
        `blocked = {"."}` - the root became unlistable *after* `scan_folder`'s
        readability check passed, which is a race no test can force and
        `on_error` genuinely produces (`os.path.relpath(root, root)` is `"."`).
        Called directly rather than mocked into a scan, because a test that
        cannot reach the branch is worse than none: a `chmod` before the scan is
        answered by `_mark_folder_unreachable` instead, and the assertion then
        passes with this branch's guard deleted.
        """
        folder = tmp_path / "models"
        folder.mkdir()
        write_adapter(folder / "a.safetensors")
        write_adapter(folder / "b.safetensors", pad=1)
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE model_file SET state = ? WHERE relpath = 'a.safetensors'",
                (STATE_REMOVED,),
            )

        touched = scanner._mark_unreachable_subtrees(
            folder_id, {"."}, scanner_module._utcnow()
        )

        rows = located(hub)
        assert rows["a.safetensors"]["state"] == STATE_REMOVED
        assert rows["b.safetensors"]["state"] == STATE_UNREACHABLE
        assert touched == 1, "the removed copy was counted as newly unreachable"

    def test_a_removed_copy_that_comes_back_is_present_again(
        self, hub, scanner, tmp_path
    ):
        """Skipping the sweeps must not make the row unreachable by the scan.

        Putting the file back is the owner un-doing the merge with a file
        manager, and the shelf has to notice: the fast path reads ``present`` rows
        only, so this one is re-read and re-registered rather than skipped.
        """
        folder = tmp_path / "loras"
        folder.mkdir()
        path = folder / "a.safetensors"
        write_adapter(path)
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        os.remove(path)
        with hub.transaction() as conn:
            conn.execute("UPDATE model_file SET state = ?", (STATE_REMOVED,))

        write_adapter(path)
        scanner.scan_folder(folder_id, str(folder), "user")

        assert located(hub)["a.safetensors"]["state"] == STATE_PRESENT
        assert len(models(hub)) == 1

    def test_a_returning_file_goes_back_to_present(self, hub, scanner, tmp_path):
        folder = tmp_path / "loras"
        folder.mkdir()
        path = folder / "a.safetensors"
        write_adapter(path)
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        os.remove(path)
        scanner.scan_folder(folder_id, str(folder), "user")

        write_adapter(path)
        scanner.scan_folder(folder_id, str(folder), "user")

        assert located(hub)["a.safetensors"]["state"] == STATE_PRESENT
        assert len(models(hub)) == 1

    def test_an_unreadable_folder_is_unreachable_never_missing(
        self, hub, scanner, tmp_path
    ):
        folder = tmp_path / "usb"
        folder.mkdir()
        write_adapter(folder / "a.safetensors")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")

        os.remove(folder / "a.safetensors")
        folder.rmdir()
        result = scanner.scan_folder(folder_id, str(folder), "user")

        assert result.state == STATE_UNREACHABLE
        assert located(hub)["a.safetensors"]["state"] == STATE_UNREACHABLE
        # And nothing was removed on the strength of a folder we could not read.
        assert len(models(hub)) == 1
        assert len(located(hub)) == 1

    @SKIP_AS_ROOT
    def test_an_unreadable_subdirectory_is_unreachable_never_missing(
        self, hub, scanner, tmp_path
    ):
        # A NAS mounted inside a registered folder. `os.walk` discards the
        # listing error by default, which would report the whole mount deleted
        # the moment it drops -- and `missing` is what "Forget unreferenced
        # adapters" acts on.
        folder = tmp_path / "models"
        (folder / "nas").mkdir(parents=True)
        write_adapter(folder / "local.safetensors")
        write_adapter(folder / "nas" / "remote.safetensors", pad=1)
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")

        os.chmod(folder / "nas", 0o000)
        try:
            result = scanner.scan_folder(folder_id, str(folder), "user")
        finally:
            os.chmod(folder / "nas", 0o755)

        rows = located(hub)
        assert rows[os.path.join("nas", "remote.safetensors")]["state"] == (
            STATE_UNREACHABLE
        )
        assert rows["local.safetensors"]["state"] == STATE_PRESENT
        assert result.missing == 0
        assert result.unreachable == 1

    def test_last_checked_is_stamped_on_both_paths(self, hub, scanner, tmp_path):
        folder = tmp_path / "loras"
        folder.mkdir()
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        reachable = hub.fetchone(
            "SELECT last_checked FROM model_folder WHERE id = ?", (folder_id,)
        )["last_checked"]
        assert reachable is not None

        folder.rmdir()
        scanner.scan_folder(folder_id, str(folder), "user")
        after = hub.fetchone(
            "SELECT last_checked FROM model_folder WHERE id = ?", (folder_id,)
        )["last_checked"]
        assert after is not None and after != reachable


class TestScanCost:
    def test_an_unchanged_file_is_not_rehashed(
        self, hub, scanner, tmp_path, monkeypatch
    ):
        folder = tmp_path / "loras"
        folder.mkdir()
        write_adapter(folder / "a.safetensors")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")

        calls = []
        real = scanner_module.sha256_file
        monkeypatch.setattr(
            scanner_module,
            "sha256_file",
            lambda path: (calls.append(path), real(path))[1],
        )
        scanner.scan_folder(folder_id, str(folder), "user")

        assert calls == []

    def test_a_settled_folder_still_counts_its_checkpoints(
        self, hub, scanner, tmp_path
    ):
        # The fast path counted every unchanged file as an adapter, so a folder
        # that had settled reported `checkpoints: 0` and an adapter count equal
        # to its whole file count. `_known_files` already joins `model`, so the
        # stored `file_kind` is there to be read back.
        folder = tmp_path / "mixed"
        folder.mkdir()
        write_adapter(folder / "a.safetensors", name="A")
        write_checkpoint(folder / "sdxl.safetensors")
        folder_id = register_folder(hub, folder)
        first = scanner.scan_folder(folder_id, str(folder), "user")

        second = scanner.scan_folder(folder_id, str(folder), "user")

        assert (first.adapters, first.checkpoints) == (1, 1)
        assert (second.adapters, second.checkpoints) == (1, 1)

    def test_a_changed_file_is_rehashed(self, hub, scanner, tmp_path, monkeypatch):
        folder = tmp_path / "loras"
        folder.mkdir()
        path = folder / "a.safetensors"
        write_adapter(path)
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        before = set(adapters(hub))

        write_adapter(path, pad=3)
        calls = []
        real = scanner_module.sha256_file
        monkeypatch.setattr(
            scanner_module,
            "sha256_file",
            lambda p: (calls.append(p), real(p))[1],
        )
        scanner.scan_folder(folder_id, str(folder), "user")

        assert len(calls) == 1
        # The content row is new; the old one is a tombstone, not a deletion.
        assert set(adapters(hub)) > before
        assert digest_at(hub, "a.safetensors") not in before

    def test_a_same_size_edit_is_caught_by_the_mtime(self, hub, scanner, tmp_path):
        # Size alone would take this file as unchanged, leaving `model.sha256`
        # naming bytes that are no longer on disk -- in the column the public
        # `{sha256}/file` route resolves on.
        folder = tmp_path / "loras"
        folder.mkdir()
        path = folder / "a.safetensors"
        write_adapter(path, name="AAAA")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        stale = digest_at(hub, "a.safetensors")

        write_adapter(path, name="BBBB")
        assert os.path.getsize(path) == next(iter(models(hub).values()))["file_size"]
        scanner.scan_folder(folder_id, str(folder), "user")

        on_disk = sha256_file(str(path))
        assert on_disk != stale
        assert digest_at(hub, "a.safetensors") == on_disk

    def test_a_returning_file_is_rehashed_even_when_size_and_mtime_match(
        self, hub, scanner, tmp_path
    ):
        # A restore that preserves timestamps (`cp -p`, `rsync -a`) can put
        # different bytes back under the same name with the same size and the
        # same mtime. Nothing watched the file while it was missing, so the fast
        # path must not trust the digest it recorded before it vanished.
        folder = tmp_path / "loras"
        folder.mkdir()
        path = folder / "a.safetensors"
        write_adapter(path, name="AAAA")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        stale = digest_at(hub, "a.safetensors")
        mtime_ns = os.stat(path).st_mtime_ns

        os.remove(path)
        scanner.scan_folder(folder_id, str(folder), "user")
        assert located(hub)["a.safetensors"]["state"] == STATE_MISSING

        write_adapter(path, name="BBBB")
        os.utime(path, ns=(mtime_ns, mtime_ns))
        # Both halves of the fast-path comparison still match, which is exactly
        # what makes this the case the previous state has to veto.
        assert located(hub)["a.safetensors"]["file_mtime"] == os.stat(path).st_mtime_ns
        assert next(iter(models(hub).values()))["file_size"] == os.path.getsize(path)

        scanner.scan_folder(folder_id, str(folder), "user")

        on_disk = sha256_file(str(path))
        assert on_disk != stale
        assert digest_at(hub, "a.safetensors") == on_disk
        assert located(hub)["a.safetensors"]["state"] == STATE_PRESENT


class TestFolderKinds:
    def test_source_folders_are_never_catalogued_in_place(self, hub, scanner, tmp_path):
        # An ai-toolkit output root is a place things are taken FROM. Its
        # checkpoints are imported as a run, never listed as shelf rows.
        folder = tmp_path / "output"
        folder.mkdir()
        write_adapter(folder / "a.safetensors")
        folder_id = register_folder(hub, folder, kind="source")

        result = scanner.scan_folder(folder_id, str(folder), "source")

        assert result.skipped is True
        assert models(hub) == {}
        assert located(hub) == {}

    def test_scan_all_covers_every_registered_folder(self, hub, scanner, tmp_path):
        first = tmp_path / "one"
        second = tmp_path / "two"
        first.mkdir()
        second.mkdir()
        write_adapter(first / "a.safetensors")
        write_adapter(second / "b.safetensors")
        register_folder(hub, first)
        register_folder(hub, second)

        results = scanner.scan_all()

        assert len(results) == 2
        assert sum(r.adapters for r in results) == 2
        assert set(located(hub)) == {"a.safetensors", "b.safetensors"}


class TestUnreadableFiles:
    def test_a_file_with_no_readable_header_is_left_unregistered(
        self, hub, scanner, tmp_path
    ):
        folder = tmp_path / "loras"
        folder.mkdir()
        (folder / "truncated.safetensors").write_bytes(b"\x00\x01")
        folder_id = register_folder(hub, folder)

        result = scanner.scan_folder(folder_id, str(folder), "user")

        assert result.unreadable == 1
        assert models(hub) == {}
        assert located(hub) == {}


class TestProgressReporting:
    def test_the_denominator_arrives_before_the_first_file_is_read(
        self, hub, scanner, tmp_path
    ):
        """A caller cannot draw a progress bar without a total, and hashing is
        what makes the scan minutes long - so the walk is materialised and the
        count is handed over *before* the first file is described, not
        discovered as the generator drains. Only model files are counted: the
        denominator has to match what the loop will actually work through.
        """
        folder = tmp_path / "loras"
        folder.mkdir()
        for name in ("a", "b", "c"):
            write_adapter(folder / f"{name}.safetensors")
        (folder / "notes.txt").write_text("not a model")
        folder_id = register_folder(hub, folder)

        seen: list[tuple[int, int]] = []
        scanner.scan_folder(
            folder_id,
            str(folder),
            "user",
            progress=lambda done, total: seen.append((done, total)),
        )

        assert seen == [(0, 3), (1, 3), (2, 3), (3, 3)]

    def test_a_scan_with_no_callback_still_works(self, hub, scanner, tmp_path):
        """The callback is optional; every other caller passes nothing."""
        folder = tmp_path / "loras"
        folder.mkdir()
        write_adapter(folder / "a.safetensors")
        folder_id = register_folder(hub, folder)

        assert scanner.scan_folder(folder_id, str(folder), "user").adapters == 1


class TestWriteCadence:
    """When rows become *visible*, not just when they are eventually correct.

    ``MissingCheckpointHashFinder`` is a second worker that can only start once a
    checkpoint row exists. With ``_WRITE_BATCH`` alone, a 91-file folder is one
    commit at the very end and a measured 6.11 GB scan showed the finder **zero**
    rows at any point while it ran: the two workers went nose to tail instead of
    overlapping. The count still bounds the transaction; time bounds the wait.
    """

    def _folder_with(self, tmp_path, count):
        folder = tmp_path / "loras"
        folder.mkdir()
        write_checkpoint(folder / "big.safetensors")
        for index in range(count - 1):
            write_adapter(folder / f"a{index}.safetensors")
        return folder

    def test_rows_are_visible_before_the_scan_finishes(
        self, hub, scanner, tmp_path, monkeypatch
    ):
        """The elapsed-time flush, exercised by making every file overdue."""
        monkeypatch.setattr(scanner_module, "_WRITE_INTERVAL_S", 0.0)
        folder = self._folder_with(tmp_path, 4)
        folder_id = register_folder(hub, folder)

        # What a reader in another thread - the work planner - would have seen.
        visible: list[int] = []
        scanner.scan_folder(
            folder_id,
            str(folder),
            "user",
            progress=lambda done, total: visible.append(
                hub.fetchone("SELECT COUNT(*) AS n FROM model_file")["n"]
            ),
        )

        # The leading 0 is the denominator callback, before any file is read.
        assert visible == [0, 1, 2, 3, 4], (
            "rows only appeared once the scan was over, so the checkpoint hash "
            f"worker could not overlap it: {visible}"
        )

    def test_a_quick_folder_is_still_one_transaction(
        self, hub, scanner, tmp_path, monkeypatch
    ):
        """The positive control. Files that cost nothing to read must not each
        earn their own commit - the flush is a latency ceiling, not a per-file
        write policy, and turning it into one is its own regression."""
        commits = []
        original = ModelFolderScanner._write_batch

        def counting(self, folder_id, batch, scanned_at):
            commits.append(len(batch))
            return original(self, folder_id, batch, scanned_at)

        monkeypatch.setattr(ModelFolderScanner, "_write_batch", counting)
        folder = self._folder_with(tmp_path, 5)
        folder_id = register_folder(hub, folder)

        scanner.scan_folder(folder_id, str(folder), "user")

        assert commits == [5], commits


class TestHeaderFacts:
    """``family``, ``quant`` and ``weights_id`` (#1314): header reads, never hashes."""

    @staticmethod
    def _vae(path, dtype="BF16"):
        return _write_safetensors(
            path,
            {"decoder.conv_in.weight": {"dtype": dtype, "shape": [512, 16, 3, 3]}},
        )

    def test_a_new_file_is_registered_with_its_header_facts(
        self, hub, scanner, tmp_path
    ):
        folder = tmp_path / "vae"
        folder.mkdir()
        self._vae(folder / "ae.safetensors")
        folder_id = register_folder(hub, folder)

        scanner.scan_folder(folder_id, str(folder), "user")

        (row,) = models(hub, "vae").values()
        assert (row["family"], row["quant"]) == ("vae_16ch", "bf16")
        assert row["weights_id"]

    def test_two_casts_of_one_model_share_a_weights_id(self, hub, scanner, tmp_path):
        folder = tmp_path / "vae"
        folder.mkdir()
        self._vae(folder / "ae_bf16.safetensors")
        self._vae(folder / "ae_fp8.safetensors", dtype="F8_E4M3")
        folder_id = register_folder(hub, folder)

        scanner.scan_folder(folder_id, str(folder), "user")

        rows = list(models(hub, "vae").values())
        assert len(rows) == 2
        assert rows[0]["weights_id"] == rows[1]["weights_id"]
        assert {row["quant"] for row in rows} == {"bf16", "f8_e4m3"}

    def test_an_unchanged_row_from_before_the_columns_is_filled_without_a_hash(
        self, hub, scanner, tmp_path, monkeypatch
    ):
        """The backfill: a rescan reads the header of a row that lacks the facts
        and never re-reads its bytes, so an existing shelf gains them for the
        price of a header per file."""
        folder = tmp_path / "vae"
        folder.mkdir()
        self._vae(folder / "ae.safetensors")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE model SET family = NULL, quant = NULL, weights_id = NULL"
            )

        def no_hash(path):
            raise AssertionError(f"an unchanged file was re-hashed: {path}")

        monkeypatch.setattr(scanner_module, "sha256_file", no_hash)
        scanner.scan_folder(folder_id, str(folder), "user")

        (row,) = models(hub, "vae").values()
        assert (row["family"], row["quant"]) == ("vae_16ch", "bf16")
        assert row["weights_id"]


class TestFilesWithNoReadableHeader:
    """``.gguf``: on the shelf, named and badged off its filename alone.

    A GGUF header is a real subsystem and deliberately not parsed, so
    everything the safetensors header answers is NULL here. The two facts the
    file still carries are the two that make it shelvable: the folder's
    declared role, and the quant postfix in its own name.
    """

    @staticmethod
    def _gguf(path, size=64):
        # GGUF's own magic, so nothing in this test passes because the bytes
        # happened to be unparseable: they are a valid start of a real format
        # whose header this build does not read.
        path.write_bytes(b"GGUF" + struct.pack("<I", 3) + b"\0" * size)
        return str(path)

    def test_a_gguf_is_registered_with_the_quant_from_its_name(
        self, hub, scanner, tmp_path
    ):
        folder = tmp_path / "unet"
        folder.mkdir()
        self._gguf(folder / "flux1-dev-Q4_K_M.gguf")
        folder_id = register_folder(hub, folder)

        result = scanner.scan_folder(folder_id, str(folder), "user")

        assert result.unreadable == 0
        (row,) = models(hub).values()
        assert row["filename"] == "flux1-dev-Q4_K_M.gguf"
        # The level kept whole and lowercased: `Q4_K_M` is the name a person
        # recognises, so it is not folded into a family the way `f16` is.
        assert row["quant"] == "q4_k_m"
        # Everything the header would have said stays NULL - "the file did not
        # say", which is what an undescribed `.safetensors` records too.
        assert (row["family"], row["weights_id"], row["kind"]) == (None, None, None)

    def test_a_gguf_outside_a_role_folder_is_unknown_rather_than_a_guess(
        self, hub, scanner, tmp_path
    ):
        # No parameter count to reason from, so there is no evidence for any
        # kind at all. `unknown` is a state the shelf shows and the owner can
        # correct; `checkpoint` would be a guess stored as a fact.
        folder = tmp_path / "downloads"
        folder.mkdir()
        self._gguf(folder / "mystery.gguf")
        folder_id = register_folder(hub, folder)

        scanner.scan_folder(folder_id, str(folder), "user")

        (row,) = models(hub).values()
        assert (row["file_kind"], row["quant"]) == ("unknown", None)

    def test_a_gguf_the_folder_calls_a_vae_takes_the_folders_word(
        self, hub, scanner, tmp_path
    ):
        folder = tmp_path / "vae"
        folder.mkdir()
        self._gguf(folder / "ae-Q8_0.gguf")
        folder_id = register_folder(hub, folder)

        scanner.scan_folder(folder_id, str(folder), "user")

        (row,) = models(hub).values()
        assert (row["file_kind"], row["quant"]) == ("vae", "q8_0")

    @pytest.mark.parametrize("name", ["unet", "diffusion_models"])
    def test_a_gguf_in_a_diffusion_model_folder_is_a_checkpoint(
        self, hub, scanner, tmp_path, name
    ):
        # What `GET /checkpoints` lists, so a quantised Flux or Wan UNet is
        # offered to a checkpoint loader (#1607).
        folder = tmp_path / name
        folder.mkdir()
        self._gguf(folder / "Wan2.1-T2V-14B-Q5_K_M.gguf")
        folder_id = register_folder(hub, folder)

        scanner.scan_folder(folder_id, str(folder), "user")

        (row,) = models(hub).values()
        assert (row["file_kind"], row["quant"]) == ("checkpoint", "q5_k_m")

    def test_a_large_gguf_is_left_for_the_hash_finder_like_any_other(
        self, hub, scanner, tmp_path, monkeypatch
    ):
        """The scan must not read tens of gigabytes to shelve one file.

        The size rule is the same one every non-adapter kind already follows,
        so turning the suffix on adds no reading the shelf was not already
        deferring - which is the whole answer to "can a folder of GGUF
        checkpoints be scanned at all". The folder names no role, so the
        size rule rather than `checkpoint` is what defers the hash.
        """
        folder = tmp_path / "downloads"
        folder.mkdir()
        self._gguf(folder / "huge-Q6_K.gguf")
        folder_id = register_folder(hub, folder)
        monkeypatch.setattr(scanner_module, "_DEFER_HASH_BYTES", 1)

        def no_hash(path):
            raise AssertionError(f"a large file was hashed inline: {path}")

        monkeypatch.setattr(scanner_module, "sha256_file", no_hash)
        scanner.scan_folder(folder_id, str(folder), "user")

        (row,) = models(hub).values()
        assert (row["sha256"], row["quant"]) == (None, "q6_k")

    def test_a_rescan_does_not_keep_reaching_for_a_header_that_is_not_there(
        self, hub, scanner, tmp_path, monkeypatch
    ):
        """`weights_id` is NULL by construction here, which is the backfill's
        own signal - so without the suffix check a GGUF would be re-opened on
        every sweep for a header it will never have."""
        folder = tmp_path / "unet"
        folder.mkdir()
        self._gguf(folder / "flux1-dev-Q4_K_M.gguf")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")

        def no_describe(path):
            raise AssertionError(f"a headerless file was re-opened: {path}")

        monkeypatch.setattr(scanner_module, "describe_adapter", no_describe)
        scanner.scan_folder(folder_id, str(folder), "user")

        assert len(models(hub)) == 1

    def test_register_file_keeps_the_digest_its_caller_already_verified(
        self, hub, scanner, tmp_path, monkeypatch
    ):
        """`POST /model-files` copies the bytes through a hash on the way in.

        Recomputing it is a second full read of a file that may be 24 GB, and
        DROPPING it is worse than that: a row with no digest misses
        `_upsert_model`'s `ON CONFLICT(sha256)` join, so one file registered
        into two folders would be two shelf rows until the hash finder merged
        them.
        """
        folder = tmp_path / "unet"
        folder.mkdir()
        path = self._gguf(folder / "flux1-dev-Q4_K_M.gguf")
        folder_id = register_folder(hub, folder)

        def no_hash(_path):
            raise AssertionError("re-read a file the caller had already hashed")

        monkeypatch.setattr(scanner_module, "sha256_file", no_hash)
        scanner.register_file(folder_id, path, "flux1-dev-Q4_K_M.gguf", sha256="a" * 64)

        (row,) = models(hub).values()
        assert (row["sha256"], row["quant"]) == ("a" * 64, "q4_k_m")

    def test_a_safetensors_whose_header_will_not_read_is_still_refused(
        self, hub, scanner, tmp_path
    ):
        """The filename fallback is for a suffix with no reader, never a
        licence to shelve a broken file of the one suffix that has one."""
        folder = tmp_path / "loras"
        folder.mkdir()
        (folder / "broken_fp16.safetensors").write_bytes(b"not a header at all")
        folder_id = register_folder(hub, folder)

        result = scanner.scan_folder(folder_id, str(folder), "user")

        assert result.unreadable == 1
        assert models(hub) == {}


class TestBaseModelIdentification:
    """The identified base model: written with its source, upgraded only by a
    stronger source, never over a person's, and nothing when nothing matched."""

    @staticmethod
    def _identified(hub):
        return {
            row["filename"]: (row["base_model_canonical"], row["base_model_source"])
            for row in models(hub).values()
        }

    def test_a_scan_records_the_match_and_where_it_came_from(
        self, hub, scanner, tmp_path
    ):
        folder = tmp_path / "loras"
        folder.mkdir()
        write_adapter(folder / "MyFlux2LoRA.safetensors")
        write_adapter(folder / "example.safetensors", base_model="SDXL_Base_V1-0")
        _write_safetensors(
            folder / "Style_v2.safetensors",
            {"blocks.0.lora_A.weight": _tensor([8, 16])},
            {"modelspec.architecture": "stable-diffusion-xl-v1-base/lora"},
        )
        folder_id = register_folder(hub, folder)

        scanner.scan_folder(folder_id, str(folder), "user")

        assert self._identified(hub) == {
            "MyFlux2LoRA.safetensors": ("FLUX.2", "filename_fuzzy"),
            "example.safetensors": ("SDXL 1.0", "declared"),
            "Style_v2.safetensors": ("SDXL 1.0", "declared"),
        }
        # The trainer's own string is kept, never rewritten to the label.
        (example,) = [
            r for r in models(hub).values() if r["filename"] == "example.safetensors"
        ]
        assert example["base_model"] == "SDXL_Base_V1-0"

    def test_nothing_matched_writes_nothing_and_is_looked_at_again(
        self, hub, scanner, tmp_path, monkeypatch
    ):
        folder = tmp_path / "loras"
        folder.mkdir()
        write_adapter(folder / "example_v3.safetensors")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        assert self._identified(hub) == {"example_v3.safetensors": (None, None)}

        reads = []
        real = scanner_module.describe_adapter
        monkeypatch.setattr(
            scanner_module,
            "describe_adapter",
            lambda path: reads.append(path) or real(path),
        )
        scanner.scan_folder(folder_id, str(folder), "user")
        # The unchanged file is not re-hashed, but its header is re-read, so a
        # base model a later release learns reaches it with no bookkeeping.
        assert len(reads) == 1, reads

    def test_an_identified_row_is_not_re_read(
        self, hub, scanner, tmp_path, monkeypatch
    ):
        folder = tmp_path / "loras"
        folder.mkdir()
        write_adapter(folder / "example.safetensors", base_model="sdxl")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")

        reads = []
        monkeypatch.setattr(
            scanner_module, "describe_adapter", lambda path: reads.append(path)
        )
        scanner.scan_folder(folder_id, str(folder), "user")
        assert reads == []

    def test_a_stronger_source_upgrades_and_a_person_is_never_overwritten(
        self, hub, scanner, tmp_path
    ):
        # One file, three names. Each copy is the same content row, so each
        # scan is a rescan of that row with different evidence.
        first = tmp_path / "a"
        second = tmp_path / "b"
        third = tmp_path / "c"
        for folder in (first, second, third):
            folder.mkdir()
        write_adapter(first / "myqwenlora.safetensors")
        (second / "flux2.safetensors").write_bytes(
            (first / "myqwenlora.safetensors").read_bytes()
        )
        (third / "Pony.safetensors").write_bytes(
            (first / "myqwenlora.safetensors").read_bytes()
        )

        scanner.scan_folder(register_folder(hub, first), str(first), "user")
        (row,) = models(hub).values()
        assert (row["base_model_canonical"], row["base_model_source"]) == (
            "Qwen-Image",
            "filename_fuzzy",
        )

        scanner.scan_folder(register_folder(hub, second), str(second), "user")
        (row,) = models(hub).values()
        assert (row["base_model_canonical"], row["base_model_source"]) == (
            "FLUX.2",
            "filename",
        )

        update_models(hub, [row["id"]], {"base_model": "Illustrious"})
        scanner.scan_folder(register_folder(hub, third), str(third), "user")
        (row,) = models(hub).values()
        assert (row["base_model_canonical"], row["base_model_source"]) == (
            "Illustrious XL",
            "user",
        )

    def test_an_equal_source_does_not_replace_the_stored_answer(
        self, hub, scanner, tmp_path
    ):
        first = tmp_path / "a"
        second = tmp_path / "b"
        for folder in (first, second):
            folder.mkdir()
        write_adapter(first / "flux2.safetensors")
        (second / "pony.safetensors").write_bytes(
            (first / "flux2.safetensors").read_bytes()
        )
        scanner.scan_folder(register_folder(hub, first), str(first), "user")
        scanner.scan_folder(register_folder(hub, second), str(second), "user")
        (row,) = models(hub).values()
        assert row["base_model_canonical"] == "FLUX.2", dict(row)


class TestCuratedBaseModel:
    """A person's base model moves the canonical column in the same write."""

    def test_setting_a_base_model_recomputes_the_canonical_label(
        self, hub, scanner, tmp_path
    ):
        folder = tmp_path / "loras"
        folder.mkdir()
        write_adapter(folder / "myflux2lora.safetensors")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        (row,) = models(hub).values()

        update_models(hub, [row["id"]], {"base_model": "Stable Diffusion XL"})

        (row,) = models(hub).values()
        assert (
            row["base_model"],
            row["base_model_canonical"],
            row["base_model_source"],
        ) == ("Stable Diffusion XL", "SDXL 1.0", "user")

    def test_clearing_a_guess_sticks_across_a_rescan(self, hub, scanner, tmp_path):
        folder = tmp_path / "loras"
        folder.mkdir()
        write_adapter(folder / "myqwenlora.safetensors")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        (row,) = models(hub).values()

        update_models(hub, [row["id"]], {"base_model": None})
        scanner.scan_folder(folder_id, str(folder), "user")
        # The same bytes under a name that states a base model exactly: this
        # goes through the identification write, not the unchanged-file touch,
        # so it is the write itself that has to leave the answer alone.
        other = tmp_path / "other"
        other.mkdir()
        (other / "flux2.safetensors").write_bytes(
            (folder / "myqwenlora.safetensors").read_bytes()
        )
        scanner.scan_folder(register_folder(hub, other), str(other), "user")

        (row,) = models(hub).values()
        assert (row["base_model_canonical"], row["base_model_source"]) == (
            None,
            "user",
        )

    def test_a_row_the_backfill_leaves_is_identified_from_its_header(
        self, hub, scanner, tmp_path
    ):
        # A row registered before identification existed. Its filename alone
        # suggests Qwen-Image; its header declares SDXL. The backfill must leave it
        # for the scan, which reads the header of the unchanged file once.
        folder = tmp_path / "loras"
        folder.mkdir()
        _write_safetensors(
            folder / "myqwenlora.safetensors",
            {"blocks.0.lora_A.weight": _tensor([8, 16])},
            {"modelspec.architecture": "stable-diffusion-xl-v1-base/lora"},
        )
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE model SET base_model_canonical = NULL, base_model_source = NULL"
            )
            _backfill_base_model_canonical(conn)

        scanner.scan_folder(folder_id, str(folder), "user")

        (row,) = models(hub).values()
        assert (row["base_model_canonical"], row["base_model_source"]) == (
            "SDXL 1.0",
            "declared",
        )

    def test_editing_something_else_leaves_the_identification_alone(
        self, hub, scanner, tmp_path
    ):
        folder = tmp_path / "loras"
        folder.mkdir()
        write_adapter(folder / "myflux2lora.safetensors")
        folder_id = register_folder(hub, folder)
        scanner.scan_folder(folder_id, str(folder), "user")
        (row,) = models(hub).values()

        update_models(hub, [row["id"]], {"display_name": "Example"})

        (row,) = models(hub).values()
        assert row["base_model_source"] == "filename_fuzzy", dict(row)
