"""Importing an ai-toolkit run onto the shelf (shelf plan B7).

An import is a move with the row created rather than repointed, so it runs the
same ordering - copy → verify by SHA-256 → register and commit → then unlink -
and the same crash window applies. The two interruption tests here are the
import's half of the acceptance bar; the move's half is in
``tests/test_model_move.py``, and both use the same ``BaseException`` trick so no
error handler tidies up what a killed process would have left.

What is specific to import and asserted here:

* **the listing changes nothing.** ``read_output_root`` costs filenames and one
  config per run, and the card grid is drawn from it before the user decides
  about anything. A test pins that no bytes are hashed and no row appears.
* **``delete_after_import`` decides only whether the last step runs.** Off, the
  run keeps its files and the shelf holds a second copy; on, the run's file goes
  - after the row is committed, never before.
* **provenance is ``trained``**, the one value the scanner never writes.
* **one stack per run**, cover first: the bare final leads, and a run with no
  bare final falls back to its highest step (the fixtures' "unconfirmed cover").

Environment: no ``Server``; a hub plus tmp directories. The route half lives in
the warm ``tests/test_model_shelf_api.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct

import pytest

from pixlstash.hub.db import HubDatabase
from pixlstash.services import model_mover as mover_module
from pixlstash.services import run_importer as importer_module
from pixlstash.services.model_mover import MoveRefused
from pixlstash.services.stack_detector import set_cover
from pixlstash.services.run_importer import (
    PROVENANCE_TRAINED,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_IMPORTED,
    RunImporter,
)


class Crash(BaseException):
    """A process death. See the note in ``tests/test_model_move.py``."""


@pytest.fixture
def hub(tmp_path):
    database = HubDatabase(str(tmp_path / "hub.db"))
    yield database
    database.close()


def _tensor(shape):
    return {"dtype": "F16", "shape": list(shape), "data_offsets": [0, 0]}


def write_adapter(path, *, name=None, seed=b""):
    """A header-only safetensors with LoRA markers, so it parses as an adapter."""
    header = {f"blocks.{i}.lora_A.weight": _tensor([8, 16]) for i in range(2)}
    header["blocks.0.lora_B.weight"] = _tensor([16, 8])
    metadata = {"format": "pt"}
    if name:
        metadata["ss_output_name"] = name
    header["__metadata__"] = metadata
    blob = json.dumps(header).encode("utf-8")
    # The seed goes in the payload, so two steps of one run are two different
    # files. Without it every step would hash identically and `model.sha256`
    # being UNIQUE would collapse the whole run into one row.
    with open(path, "wb") as handle:
        handle.write(struct.pack("<Q", len(blob)) + blob + seed)
    return str(path)


@pytest.fixture
def run_folder(tmp_path):
    """One ai-toolkit run: two steps, a bare final, samples, and a config."""
    output_root = tmp_path / "output"
    run_dir = output_root / "Clementine"
    (run_dir / "samples").mkdir(parents=True)
    write_adapter(run_dir / "Clementine_000000250.safetensors", seed=b"step250")
    write_adapter(run_dir / "Clementine_000000500.safetensors", seed=b"step500")
    write_adapter(run_dir / "Clementine.safetensors", seed=b"final")
    (run_dir / "samples" / "1712345678901__000000250_0.jpg").write_bytes(b"jpeg-250")
    (run_dir / "samples" / "1712345678901__000000500_0.jpg").write_bytes(b"jpeg")
    (run_dir / "config.yaml").write_text(
        "config:\n"
        "  process:\n"
        "    - model:\n"
        "        name_or_path: black-forest-labs/FLUX.1-dev\n"
        "      trigger_word: clemntn\n"
        "      network:\n"
        "        linear: 32\n"
    )
    return output_root, run_dir


@pytest.fixture
def shelf(hub, tmp_path, run_folder):
    """A registered source folder, a registered destination, and a run in it."""
    output_root, run_dir = run_folder
    destination_dir = tmp_path / "loras"
    destination_dir.mkdir()
    with hub.transaction() as conn:
        source_id = int(
            conn.execute(
                "INSERT INTO model_folder (path, kind, owner, movable, "
                "delete_after_import, created_at) VALUES (?, 'source', "
                "'ai-toolkit', 'external', 0, '2026-08-09T00:00:00+00:00')",
                (str(output_root),),
            ).lastrowid
        )
        destination_id = int(
            conn.execute(
                "INSERT INTO model_folder (path, kind, movable, created_at) "
                "VALUES (?, 'user', 'per_item', '2026-08-09T00:00:00+00:00')",
                (str(destination_dir),),
            ).lastrowid
        )
    return {
        "hub": hub,
        "output_root": output_root,
        "run_dir": run_dir,
        "destination_dir": destination_dir,
        "source_id": source_id,
        "destination_id": destination_id,
    }


def models(hub):
    return {row["filename"]: row for row in hub.fetchall("SELECT * FROM model")}


def assert_no_dangling_rows(hub):
    dangling = [
        os.path.join(row["path"], row["relpath"])
        for row in hub.fetchall(
            "SELECT f.path, mf.relpath FROM model_file mf "
            "JOIN model_folder f ON f.id = mf.model_folder_id"
        )
        if not os.path.exists(os.path.join(row["path"], row["relpath"]))
    ]
    assert not dangling, f"model_file row(s) name files that do not exist: {dangling}"


# ===========================================================================
# The crash windows - the same invariant as a move
# ===========================================================================


def test_crash_after_the_copy_and_before_the_register_leaves_no_row(shelf, monkeypatch):
    """Window 1. The bytes are at the destination and nothing claims them.

    The run still has its own copy, because the unlink is last and the register
    never happened. Residue: an unregistered file that a **manual** rescan of the
    destination folder picks up as a normal adapter - nothing scans a model
    folder on start or on a schedule. Not a dangling row: there is no row.
    """
    monkeypatch.setattr(
        RunImporter,
        "_register",
        lambda *args, **kwargs: (_ for _ in ()).throw(Crash("killed after the copy")),
    )
    with pytest.raises(Crash):
        RunImporter(shelf["hub"]).import_run(
            str(shelf["run_dir"]), shelf["destination_id"], delete_source=True
        )

    assert os.path.exists(shelf["run_dir"] / "Clementine.safetensors"), (
        "the run's file was unlinked before its row was committed"
    )
    assert os.path.exists(shelf["destination_dir"] / "Clementine.safetensors")
    assert models(shelf["hub"]) == {}
    assert_no_dangling_rows(shelf["hub"])


def test_crash_after_the_register_and_before_the_unlink_leaves_a_duplicate(
    shelf, monkeypatch
):
    """Window 2. The bytes are at both paths and the row names the destination.

    Reverse the register and the unlink and this becomes a committed row naming
    a file that was deleted before anything recorded where its copy went.
    """
    # Patched on the importer, not on the mover: ``run_importer`` binds the
    # helper by name at import time, so the mover's global is the wrong seam.
    monkeypatch.setattr(
        importer_module,
        "unlink_source",
        lambda *args: (_ for _ in ()).throw(Crash("killed after the commit")),
    )
    with pytest.raises(Crash):
        RunImporter(shelf["hub"]).import_run(
            str(shelf["run_dir"]), shelf["destination_id"], delete_source=True
        )

    assert os.path.exists(shelf["run_dir"] / "Clementine.safetensors")
    assert os.path.exists(shelf["destination_dir"] / "Clementine.safetensors")
    rows = models(shelf["hub"])
    assert "Clementine.safetensors" in rows
    assert_no_dangling_rows(shelf["hub"])


def test_a_copy_that_does_not_verify_registers_nothing(shelf, monkeypatch):
    monkeypatch.setattr(importer_module, "file_digest", lambda path: "0" * 64)
    report = RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), shelf["destination_id"], delete_source=True
    )

    assert {outcome.status for outcome in report.outcomes} == {STATUS_FAILED}
    assert models(shelf["hub"]) == {}
    assert os.path.exists(shelf["run_dir"] / "Clementine.safetensors")
    assert sorted(os.listdir(shelf["destination_dir"])) == []


# ===========================================================================
# What an import produces
# ===========================================================================


def test_a_run_lands_as_one_stack_with_the_final_as_its_cover(shelf):
    report = RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), shelf["destination_id"]
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_IMPORTED] * 3
    rows = models(shelf["hub"])
    assert set(rows) == {
        "Clementine.safetensors",
        "Clementine_000000500.safetensors",
        "Clementine_000000250.safetensors",
    }
    assert {row["stack_id"] for row in rows.values()} == {report.stack_id}
    # Cover first: the bare final is what a user means by "the LoRA".
    assert rows["Clementine.safetensors"]["stack_position"] == 0
    assert rows["Clementine_000000500.safetensors"]["stack_position"] == 1
    assert rows["Clementine_000000250.safetensors"]["stack_position"] == 2
    assert rows["Clementine_000000500.safetensors"]["training_step"] == 500
    assert rows["Clementine.safetensors"]["training_step"] is None
    assert_no_dangling_rows(shelf["hub"])


def test_re_importing_a_run_keeps_the_cover_its_owner_chose(shelf):
    """The claim `set_cover` rests on, tested against the path that could break it.

    A hand-picked cover is `stack_position` 0 and nothing else - no column
    records that a person chose it - so the whole design depends on the
    importer's `COALESCE` leaving an existing position alone. Re-importing the
    same run is the one routine way those rows get written again.
    """
    RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), shelf["destination_id"], delete_source=False
    )
    rows = models(shelf["hub"])
    stack_id = rows["Clementine.safetensors"]["stack_id"]
    chosen = rows["Clementine_000000500.safetensors"]["id"]
    set_cover(shelf["hub"], stack_id, chosen)

    # Into a SECOND folder, because importing a run back into the folder it is
    # already in is refused outright by the collision check. Same files, same
    # digests, so this is the upsert path - the one that rewrites the columns
    # the cover lives in.
    second = shelf["destination_dir"].parent / "loras-2"
    second.mkdir()
    with shelf["hub"].transaction() as conn:
        second_id = int(
            conn.execute(
                "INSERT INTO model_folder (path, kind, movable, created_at) "
                "VALUES (?, 'user', 'per_item', '2026-08-16T00:00:00+00:00')",
                (str(second),),
            ).lastrowid
        )
    RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), second_id, delete_source=False
    )

    after = models(shelf["hub"])
    assert after["Clementine_000000500.safetensors"]["stack_position"] == 0
    assert after["Clementine.safetensors"]["stack_position"] == 1
    assert after["Clementine_000000250.safetensors"]["stack_position"] == 2


def test_an_import_records_trained_provenance_and_the_config_base_model(shelf):
    """``trained`` is the one provenance the scanner never writes, and the run's
    config names a base model the header does not carry - 37 % of real adapters
    record none at all."""
    RunImporter(shelf["hub"]).import_run(str(shelf["run_dir"]), shelf["destination_id"])
    row = models(shelf["hub"])["Clementine.safetensors"]
    assert row["provenance"] == PROVENANCE_TRAINED
    assert row["base_model"] == "black-forest-labs/FLUX.1-dev"
    assert json.loads(row["trigger_words"]) == ["clemntn"]
    assert row["run_key"] == "Clementine"
    assert row["file_kind"] == "adapter"


def test_delete_after_import_off_keeps_the_run_and_makes_a_second_copy(shelf):
    """The default. The run stays where the trainer left it; the shelf holds a
    copy. Two paths, and only one of them registered - which is fine, because the
    output root is never catalogued."""
    RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), shelf["destination_id"], delete_source=False
    )
    assert os.path.exists(shelf["run_dir"] / "Clementine.safetensors")
    assert os.path.exists(shelf["destination_dir"] / "Clementine.safetensors")


def test_delete_after_import_on_empties_the_run(shelf):
    RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), shelf["destination_id"], delete_source=True
    )
    assert not os.path.exists(shelf["run_dir"] / "Clementine.safetensors")
    assert os.path.exists(shelf["destination_dir"] / "Clementine.safetensors")
    assert_no_dangling_rows(shelf["hub"])


def test_selecting_steps_takes_only_those(shelf):
    report = RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), shelf["destination_id"], steps=[None, 500]
    )
    assert len(report.outcomes) == 2
    assert set(models(shelf["hub"])) == {
        "Clementine.safetensors",
        "Clementine_000000500.safetensors",
    }
    assert os.path.exists(shelf["run_dir"] / "Clementine_000000250.safetensors")


def test_a_run_with_no_bare_final_covers_with_its_highest_step(shelf):
    """The fixtures' *unconfirmed cover*: nothing says which step the user meant,
    so the newest is the best available answer rather than a certain one."""
    os.unlink(shelf["run_dir"] / "Clementine.safetensors")
    RunImporter(shelf["hub"]).import_run(str(shelf["run_dir"]), shelf["destination_id"])
    rows = models(shelf["hub"])
    assert rows["Clementine_000000500.safetensors"]["stack_position"] == 0
    assert rows["Clementine_000000250.safetensors"]["stack_position"] == 1


# ===========================================================================
# The samples come with the weights
# ===========================================================================
#
# The one gap in this module that lost data rather than deferring a feature: the
# importer took the ``.safetensors`` and nothing else, so a source folder
# carrying ``delete_after_import`` destroyed the run's previews with it.


def test_each_checkpoints_samples_land_beside_it_with_the_trainers_names(shelf):
    report = RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), shelf["destination_id"]
    )

    final = shelf["destination_dir"] / "Clementine_samples"
    stepped = shelf["destination_dir"] / "Clementine_000000250_samples"
    # ai-toolkit's own filenames, unchanged: no renumbering.
    assert os.listdir(final) == ["1712345678901__000000500_0.jpg"]
    assert os.listdir(stepped) == ["1712345678901__000000250_0.jpg"]
    assert (final / "1712345678901__000000500_0.jpg").read_bytes() == b"jpeg"
    assert {o.filename: o.sample_count for o in report.outcomes} == {
        "Clementine.safetensors": 1,
        "Clementine_000000500.safetensors": 1,
        "Clementine_000000250.safetensors": 1,
    }


def test_the_bare_final_takes_the_highest_steps_samples_even_when_it_is_imported_too(
    shelf,
):
    """The cover rule, at the point it actually costs something: step 500's
    previews are copied twice, into its own folder and into the final's. That
    duplication is the accepted price of the most visible row of a fresh import
    not being the only empty one."""
    RunImporter(shelf["hub"]).import_run(str(shelf["run_dir"]), shelf["destination_id"])

    final = shelf["destination_dir"] / "Clementine_samples"
    highest = shelf["destination_dir"] / "Clementine_000000500_samples"
    assert (
        os.listdir(final) == os.listdir(highest) == ["1712345678901__000000500_0.jpg"]
    )


def test_a_checkpoint_whose_step_has_no_previews_gets_no_samples_folder(shelf):
    """Not an empty directory, and not the highest step's either - that rule is
    the bare final's alone."""
    write_adapter(shelf["run_dir"] / "Clementine_000000900.safetensors", seed=b"s900")
    report = RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), shelf["destination_id"], steps=[900]
    )
    assert [o.sample_count for o in report.outcomes] == [0]
    assert not (shelf["destination_dir"] / "Clementine_000000900_samples").exists()


def test_delete_after_import_unlinks_the_checkpoint_only_after_the_samples_are_copied(
    shelf,
):
    """The ordering that makes this a data-loss fix rather than a feature.

    Asserted at the seam rather than after the fact: an import that copied the
    previews *after* the unlink would leave exactly the same directory on disk
    at the end, and would still have had a window where the only copy was gone.
    """
    seen: dict = {}
    real_unlink = importer_module.unlink_source

    def record_then_unlink(path):
        seen.setdefault(
            path, sorted(os.listdir(shelf["destination_dir"] / "Clementine_samples"))
        )
        return real_unlink(path)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(importer_module, "unlink_source", record_then_unlink)
    try:
        RunImporter(shelf["hub"]).import_run(
            str(shelf["run_dir"]),
            shelf["destination_id"],
            steps=[None],
            delete_source=True,
        )
    finally:
        monkeypatch.undo()

    assert seen == {
        str(shelf["run_dir"] / "Clementine.safetensors"): [
            "1712345678901__000000500_0.jpg"
        ]
    }, "the run's file was unlinked before its previews were copied"
    assert not os.path.exists(shelf["run_dir"] / "Clementine.safetensors")


def test_an_existing_samples_folder_refuses_the_whole_batch(shelf):
    """The only path here that can write into a directory the owner put there,
    and there is no undo. Refused in the same pass as the filename collision and
    before the first byte, so nothing at all is imported."""
    squatter = shelf["destination_dir"] / "Clementine_000000500_samples"
    squatter.mkdir()
    (squatter / "mine.jpg").write_bytes(b"somebody else's file")

    with pytest.raises(MoveRefused, match="never merged"):
        RunImporter(shelf["hub"]).import_run(
            str(shelf["run_dir"]), shelf["destination_id"]
        )

    assert os.listdir(shelf["destination_dir"]) == ["Clementine_000000500_samples"]
    assert os.listdir(squatter) == ["mine.jpg"]
    assert models(shelf["hub"]) == {}


def test_a_symlink_at_a_samples_destination_name_is_refused_not_written_through(shelf):
    """The containment on the samples directory, and *which* check does it.

    The comment in ``_resolve_targets`` used to credit `lexists`; it is the
    containment call, which realpaths and so refuses the link whether or not it
    dangles. Asserted here because the existence check runs on the *resolved*
    path, where a dangling link is simply absent - so `lexists` and `exists`
    cannot be told apart at that line, and only this proves the guard is real.
    """
    outside = shelf["destination_dir"].parent / "outside"
    outside.mkdir()
    os.symlink(
        str(outside / "Clementine_samples"),
        str(shelf["destination_dir"] / "Clementine_samples"),
    )

    with pytest.raises(MoveRefused, match="outside the destination folder"):
        RunImporter(shelf["hub"]).import_run(
            str(shelf["run_dir"]), shelf["destination_id"], steps=[None]
        )

    assert not (outside / "Clementine_samples").exists(), (
        "the import wrote through the link, outside the registered folder"
    )
    assert models(shelf["hub"]) == {}


def test_a_samples_copy_that_fails_leaves_the_checkpoint_imported(shelf, monkeypatch):
    """Non-fatal, asserted rather than assumed: losing a preview must not cost
    the weights. The half-written directory goes with it."""

    def refuse(*args, **kwargs):
        raise OSError("no room for previews")

    monkeypatch.setattr(importer_module.shutil, "copy2", refuse)

    report = RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), shelf["destination_id"], steps=[None], delete_source=True
    )

    outcome = report.outcomes[0]
    assert outcome.status == STATUS_IMPORTED
    assert outcome.sample_count == 0
    assert "no room for previews" in (outcome.detail or "")
    assert os.path.exists(shelf["destination_dir"] / "Clementine.safetensors")
    assert not os.path.exists(shelf["run_dir"] / "Clementine.safetensors")
    assert os.listdir(shelf["destination_dir"]) == ["Clementine.safetensors"], (
        "a half-written samples directory was left behind"
    )
    assert_no_dangling_rows(shelf["hub"])


def test_the_space_check_counts_the_samples(shelf, monkeypatch):
    """A run measured 1.9 GB of which samples/ was 15 MB. Counting only the
    weights makes the check wrong by that much, per run."""
    seen: list[int] = []
    real_require_space = mover_module.require_space

    def record(destination, byte_count):
        seen.append(byte_count)
        return real_require_space(destination, byte_count)

    monkeypatch.setattr(importer_module, "require_space", record)
    RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), shelf["destination_id"], steps=[None]
    )

    weights = os.path.getsize(shelf["run_dir"] / "Clementine.safetensors")
    previews = os.path.getsize(
        shelf["run_dir"] / "samples" / "1712345678901__000000500_0.jpg"
    )
    assert seen == [weights + previews]


# ===========================================================================
# Refused before the first byte
# ===========================================================================


def test_a_source_folder_is_never_an_import_destination(shelf):
    with pytest.raises(MoveRefused, match="taken from"):
        RunImporter(shelf["hub"]).import_run(str(shelf["run_dir"]), shelf["source_id"])


def test_a_run_with_nothing_selected_is_refused(shelf):
    with pytest.raises(MoveRefused, match="no checkpoint to import"):
        RunImporter(shelf["hub"]).import_run(
            str(shelf["run_dir"]), shelf["destination_id"], steps=[9999]
        )
    assert os.listdir(shelf["destination_dir"]) == []


def test_a_name_already_in_the_destination_is_refused_before_anything_copies(shelf):
    """Refused as a batch: importing two of three steps and stopping is a
    half-done operation with no undo."""
    write_adapter(shelf["destination_dir"] / "Clementine.safetensors", seed=b"other")
    with pytest.raises(MoveRefused, match="already exists"):
        RunImporter(shelf["hub"]).import_run(
            str(shelf["run_dir"]), shelf["destination_id"]
        )
    assert os.listdir(shelf["destination_dir"]) == ["Clementine.safetensors"]
    assert models(shelf["hub"]) == {}


def test_a_file_that_lands_on_the_destination_name_mid_copy_is_not_overwritten(
    shelf, monkeypatch
):
    """The TOCTOU between ``_resolve_targets`` and ``os.replace``.

    The batch is planned in the POST and the copy runs minutes later, so the
    destination being free at plan time proves nothing about it being free at
    write time - ``SHELF_IO_LOCK`` holds off the other shelf operation, not the
    owner or a trainer. ``os.replace`` overwrites in silence, so without the
    re-check this destroys a file nobody named and there is no undo.
    """
    target = shelf["destination_dir"] / "Clementine.safetensors"
    real_digest = importer_module.file_digest

    def gatecrash_then_digest(path):
        if not target.exists():
            target.write_bytes(b"somebody else's file")
        return real_digest(path)

    monkeypatch.setattr(importer_module, "file_digest", gatecrash_then_digest)

    report = RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), shelf["destination_id"], steps=[None], delete_source=True
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_FAILED]
    assert target.read_bytes() == b"somebody else's file", (
        "the import wrote over a file that appeared after the batch was planned"
    )
    assert os.path.exists(shelf["run_dir"] / "Clementine.safetensors"), (
        "the run's own file was unlinked despite the import failing"
    )
    assert models(shelf["hub"]) == {}
    assert [
        name
        for name in os.listdir(shelf["destination_dir"])
        if name.endswith(mover_module.PARTIAL_SUFFIX)
    ] == [], "the discarded copy was left behind"


def test_a_destination_row_registered_mid_copy_fails_the_file_and_leaves_it_alone(
    shelf, monkeypatch
):
    """``ON CONFLICT(model_folder_id, relpath) DO UPDATE`` would repoint it.

    ``_resolve_targets`` proves the destination key free at plan time and a
    rescan is deliberately **not** under ``SHELF_IO_LOCK``, so it can take that
    key while the copy runs. Updating the row would silently point somebody
    else's location at this import's file - the bookkeeping version of the
    overwrite the whole module refuses. Fail closed and leave their row for the
    rescan that owns it, exactly as ``ModelMover`` does.
    """
    hub = shelf["hub"]
    gatecrasher = {}
    real_digest = importer_module.file_digest

    def gatecrash_then_digest(path):
        if not gatecrasher:
            with hub.transaction() as conn:
                gatecrasher["model_id"] = int(
                    conn.execute(
                        "INSERT INTO model (file_kind, kind, sha256, filename, "
                        "provenance, file_size, created_at) VALUES ('adapter', "
                        "'lora', ?, 'Clementine.safetensors', 'external', 10, "
                        "'2026-08-09T00:00:00+00:00')",
                        ("f" * 64,),
                    ).lastrowid
                )
                conn.execute(
                    "INSERT INTO model_file (model_id, model_folder_id, relpath, "
                    "state, seen_at) VALUES (?, ?, 'Clementine.safetensors', "
                    "'missing', '2026-08-09T00:00:00+00:00')",
                    (gatecrasher["model_id"], shelf["destination_id"]),
                )
        return real_digest(path)

    monkeypatch.setattr(importer_module, "file_digest", gatecrash_then_digest)

    report = RunImporter(hub).import_run(
        str(shelf["run_dir"]), shelf["destination_id"], steps=[None], delete_source=True
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_FAILED]
    row = hub.fetchone(
        "SELECT model_id, state FROM model_file WHERE model_folder_id = ? "
        "AND relpath = 'Clementine.safetensors'",
        (shelf["destination_id"],),
    )
    assert row["model_id"] == gatecrasher["model_id"], (
        "the import repointed a location row it did not create"
    )
    assert row["state"] == "missing", "the racing writer's row was rewritten"
    assert list(models(hub)) == ["Clementine.safetensors"], (
        "the failed import left a content row behind"
    )
    assert os.path.exists(shelf["run_dir"] / "Clementine.safetensors"), (
        "the run's file was unlinked despite the import failing"
    )
    assert not os.path.exists(shelf["destination_dir"] / "Clementine.safetensors"), (
        "the copy this import made was left where no row names it"
    )


def test_a_symlink_at_a_destination_name_is_refused_not_written_through(shelf):
    """The importer's destination containment is **reachable** too.

    Same shape as the mover's (``tests/test_model_move.py``), and it was never
    flagged: ``basename`` flattens the filename but ``resolve_path_within``
    calls ``realpath``, so a symlink standing at a checkpoint's destination name
    resolves outside. A *dangling* one is the sharp case - ``os.path.exists`` is
    False for it, so the collision check below the containment would wave it
    through into an ``os.replace`` that writes outside the registered folder.
    """
    outside = shelf["destination_dir"].parent / "outside"
    outside.mkdir()
    os.symlink(
        str(outside / "Clementine.safetensors"),
        str(shelf["destination_dir"] / "Clementine.safetensors"),
    )

    with pytest.raises(MoveRefused, match="outside the destination folder"):
        RunImporter(shelf["hub"]).import_run(
            str(shelf["run_dir"]), shelf["destination_id"]
        )

    assert not (outside / "Clementine.safetensors").exists(), (
        "the import wrote through the link, outside the registered folder"
    )
    assert models(shelf["hub"]) == {}
    assert os.path.exists(shelf["run_dir"] / "Clementine.safetensors")


def test_space_is_checked_before_the_first_byte(shelf, monkeypatch):
    import shutil

    monkeypatch.setattr(
        mover_module.shutil,
        "disk_usage",
        lambda path: shutil._ntuple_diskusage(total=1, used=1, free=1),
    )
    with pytest.raises(MoveRefused) as excinfo:
        RunImporter(shelf["hub"]).import_run(
            str(shelf["run_dir"]), shelf["destination_id"]
        )
    assert excinfo.value.status_code == 507
    assert os.listdir(shelf["destination_dir"]) == []


def test_cancel_stops_the_queue_and_rolls_nothing_back(shelf):
    seen: list = []

    report = RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]),
        shelf["destination_id"],
        delete_source=True,
        should_cancel=lambda: bool(seen),
        on_progress=seen.append,
    )

    assert report.cancelled is True
    assert [outcome.status for outcome in report.outcomes] == [
        STATUS_IMPORTED,
        STATUS_CANCELLED,
        STATUS_CANCELLED,
    ]
    # Not rolled back.
    assert os.path.exists(shelf["destination_dir"] / "Clementine.safetensors")
    assert not os.path.exists(shelf["run_dir"] / "Clementine.safetensors")
    assert os.path.exists(shelf["run_dir"] / "Clementine_000000500.safetensors")
    assert_no_dangling_rows(shelf["hub"])


# ===========================================================================
# Listing a run costs nothing
# ===========================================================================


def test_listing_runs_hashes_nothing_and_writes_nothing(shelf, monkeypatch):
    """The property the whole card grid rests on: an output root can be browsed
    without importing, hashing or moving anything."""
    from pixlstash.utils.aitoolkit_run import read_output_root

    def refuse(*args, **kwargs):
        raise AssertionError("listing a run must not hash anything")

    monkeypatch.setattr(hashlib, "sha256", refuse)
    runs = read_output_root(str(shelf["output_root"]))

    assert [run.name for run in runs] == ["Clementine"]
    assert len(runs[0].checkpoints) == 3
    assert runs[0].base_model == "black-forest-labs/FLUX.1-dev"
    assert models(shelf["hub"]) == {}
    assert os.listdir(shelf["destination_dir"]) == []
