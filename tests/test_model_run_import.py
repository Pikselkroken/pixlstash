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
import shutil
import struct
from types import SimpleNamespace

import pytest

import pixlstash.vault as vault_module
from pixlstash.hub import training_runs
from pixlstash.routes.model_imports import _to_run_response
from pixlstash.services import model_mover as mover_module
from pixlstash.services import run_importer as importer_module
from pixlstash.services.model_mover import MoveRefused
from pixlstash.services.run_importer import (
    PROVENANCE_TRAINED,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_IMPORTED,
    RunImporter,
)
from pixlstash.services.stack_detector import set_cover
from pixlstash.utils.aitoolkit_run import read_output_root
from pixlstash.vault import Vault


class Crash(BaseException):
    """A process death. See the note in ``tests/test_model_move.py``."""


def _tensor(shape):
    return {"dtype": "F16", "shape": list(shape), "data_offsets": [0, 0]}


def write_adapter(path, *, name=None, seed=b"", tag_frequency=None):
    """A header-only safetensors with LoRA markers, so it parses as an adapter."""
    header = {f"blocks.{i}.lora_A.weight": _tensor([8, 16]) for i in range(2)}
    header["blocks.0.lora_B.weight"] = _tensor([16, 8])
    metadata = {"format": "pt"}
    if name:
        metadata["ss_output_name"] = name
    if tag_frequency:
        metadata["ss_tag_frequency"] = json.dumps(tag_frequency)
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
    never happened. Residue: an unregistered file that the next scan of the
    destination folder picks up as a normal adapter. Not a dangling row: there is no row.
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


def test_a_subfolder_that_does_not_exist_yet_is_created_for_the_import(shelf):
    """The subfolder need not exist: the import creates it and lands the run."""
    subfolder = shelf["destination_dir"] / "loras"
    assert not subfolder.exists()
    report = RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), shelf["destination_id"], subfolder="loras"
    )
    assert {outcome.status for outcome in report.outcomes} == {STATUS_IMPORTED}
    assert (subfolder / "Clementine.safetensors").is_file()
    assert_no_dangling_rows(shelf["hub"])


def test_a_refused_subfolder_import_creates_no_folder(shelf, monkeypatch):
    """The subfolder is made only once every check has passed."""

    def refuse(destination, bytes_to_copy):
        raise MoveRefused("not enough space", status_code=507)

    monkeypatch.setattr(importer_module, "require_space", refuse)
    with pytest.raises(MoveRefused):
        RunImporter(shelf["hub"]).import_run(
            str(shelf["run_dir"]), shelf["destination_id"], subfolder="loras"
        )
    assert not (shelf["destination_dir"] / "loras").exists()


def test_a_run_imported_into_a_subfolder_lands_under_it(shelf):
    """With a ``subfolder`` the files and their ``model_file`` rows sit under
    it - the shape a LoRA download gives a file that belongs in ``loras/``."""
    subfolder = shelf["destination_dir"] / "loras"
    subfolder.mkdir()
    report = RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), shelf["destination_id"], subfolder="loras"
    )
    assert {outcome.status for outcome in report.outcomes} == {STATUS_IMPORTED}
    assert (subfolder / "Clementine.safetensors").is_file()
    assert not (shelf["destination_dir"] / "Clementine.safetensors").exists()
    relpaths = sorted(
        row["relpath"]
        for row in shelf["hub"].fetchall(
            "SELECT relpath FROM model_file WHERE model_folder_id = ?",
            (shelf["destination_id"],),
        )
    )
    assert relpaths == sorted(
        f"loras/{name}"
        for name in (
            "Clementine_000000250.safetensors",
            "Clementine_000000500.safetensors",
            "Clementine.safetensors",
        )
    )
    assert_no_dangling_rows(shelf["hub"])


def test_the_configs_trigger_word_beats_the_headers_tag_table(shelf):
    """The config names the word the trainer was told to use. The header's
    frequency table is every tag in the captions, with the commonest first -
    which is not the trigger whenever a caption tag outnumbers it."""
    write_adapter(
        shelf["run_dir"] / "Clementine.safetensors",
        seed=b"final",
        tag_frequency={"1_clementine": {"portrait": 9, "clemntn": 3}},
    )
    RunImporter(shelf["hub"]).import_run(str(shelf["run_dir"]), shelf["destination_id"])
    row = models(shelf["hub"])["Clementine.safetensors"]
    assert json.loads(row["trigger_words"]) == ["clemntn"]


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
# The samples stay in the run
# ===========================================================================


def test_an_import_takes_the_weights_and_leaves_the_samples_in_the_run(shelf):
    """Even with ``delete_source``: the run's previews are the trainer's working
    output, read in place, and never copied onto the shelf or unlinked."""
    run_samples = sorted(os.listdir(shelf["run_dir"] / "samples"))
    report = RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), shelf["destination_id"], delete_source=True
    )

    assert {o.status for o in report.outcomes} == {STATUS_IMPORTED}
    assert sorted(os.listdir(shelf["destination_dir"])) == [
        "Clementine.safetensors",
        "Clementine_000000250.safetensors",
        "Clementine_000000500.safetensors",
    ]
    assert sorted(os.listdir(shelf["run_dir"] / "samples")) == run_samples


def test_an_existing_samples_folder_is_left_alone(shelf):
    """No previews are written, so a directory already at a checkpoint's
    ``<stem>_samples`` name is neither a collision nor touched."""
    squatter = shelf["destination_dir"] / "Clementine_000000500_samples"
    squatter.mkdir()
    (squatter / "mine.jpg").write_bytes(b"somebody else's file")

    RunImporter(shelf["hub"]).import_run(str(shelf["run_dir"]), shelf["destination_id"])

    assert os.listdir(squatter) == ["mine.jpg"]


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

    def refuse(*args, **kwargs):
        raise AssertionError("listing a run must not hash anything")

    monkeypatch.setattr(hashlib, "sha256", refuse)
    runs = read_output_root(str(shelf["output_root"]))

    assert [run.name for run in runs] == ["Clementine"]
    assert len(runs[0].checkpoints) == 3
    assert runs[0].base_model == "black-forest-labs/FLUX.1-dev"
    assert models(shelf["hub"]) == {}
    assert os.listdir(shelf["destination_dir"]) == []


def test_a_listed_run_is_dated_by_its_newest_checkpoint(shelf):
    """The card's date: the last write of any checkpoint, read by a stat."""

    run = read_output_root(str(shelf["output_root"]))[0]
    newest = run.checkpoints[-1].path
    os.utime(newest, (2_000_000_000, 2_000_000_000))

    assert _to_run_response(run).modified_at == 2_000_000_000


def test_a_listed_run_carries_its_base_models_tile(shelf):
    """The card's mark: FLUX.1 dev's release tile, and none for a base unknown."""
    run = read_output_root(str(shelf["output_root"]))[0]
    tile = _to_run_response(run).base_tile
    assert (tile["label"], tile["mark_icon"], tile["mark_band"]) == (
        "FLUX.1 dev",
        "flux",
        True,
    )
    run.base_model = "someone/my-private-base"
    assert _to_run_response(run).base_tile is None


def _a_training_run(hub, run_uuid="run-clementine"):
    """The hub row a PixlStash training run writes before it starts."""
    return training_runs.create_run(
        hub,
        library_uuid="library-under-test",
        uuid=run_uuid,
        name="Clementine",
        set_id=None,
        character_id=None,
        base_model="black-forest-labs/FLUX.1-dev",
        trigger_word="clemntn",
    )


def test_every_model_an_import_creates_names_the_run_that_made_it(shelf):
    run_id = _a_training_run(shelf["hub"])

    RunImporter(shelf["hub"]).import_run(
        str(shelf["run_dir"]), shelf["destination_id"], training_run_id=run_id
    )

    rows = models(shelf["hub"])
    assert len(rows) == 3
    assert {row["training_run_id"] for row in rows.values()} == {run_id}


def test_a_run_trained_elsewhere_names_no_run(shelf):
    RunImporter(shelf["hub"]).import_run(str(shelf["run_dir"]), shelf["destination_id"])

    assert {row["training_run_id"] for row in models(shelf["hub"]).values()} == {None}


def test_the_vault_hands_the_finished_runs_id_to_the_importer(shelf):
    """The vault's hook is the one call site that knows the run's id.

    ``_import_training_run`` reads only the hub, so it runs unbound on a
    stand-in: the shelf's download folder receives the run, and every model it
    lands names the run.
    """
    run_id = _a_training_run(shelf["hub"])
    with shelf["hub"].transaction() as conn:
        conn.execute(
            "UPDATE model_folder SET is_download_target = 1 WHERE id = ?",
            (shelf["destination_id"],),
        )

    stand_in = SimpleNamespace(_hub=shelf["hub"])
    result = Vault._import_training_run(
        stand_in,
        str(shelf["run_dir"]),
        {"id": run_id, "name": "Clementine", "set_id": None, "character_id": None},
    )

    assert result["imported"] == 3
    assert {row["training_run_id"] for row in models(shelf["hub"]).values()} == {run_id}


@pytest.mark.parametrize("delete_after_import", [0, 1])
def test_a_trained_run_follows_its_source_folders_delete_after_import(
    shelf, delete_after_import
):
    """The run lands in the shelf's ai-toolkit folder, so that folder decides.

    Off, the trainer's files stay beside the shelf's copies; on, they go once
    the copies are committed - the same rule an import from the shelf follows.
    """
    run_id = _a_training_run(shelf["hub"])
    with shelf["hub"].transaction() as conn:
        conn.execute(
            "UPDATE model_folder SET is_download_target = 1 WHERE id = ?",
            (shelf["destination_id"],),
        )
        conn.execute(
            "UPDATE model_folder SET delete_after_import = ? WHERE id = ?",
            (delete_after_import, shelf["source_id"]),
        )

    Vault._import_training_run(
        SimpleNamespace(_hub=shelf["hub"]),
        str(shelf["run_dir"]),
        {"id": run_id, "name": "Clementine", "set_id": None, "character_id": None},
    )

    left = sorted(p.name for p in shelf["run_dir"].glob("*.safetensors"))
    assert left == (
        []
        if delete_after_import
        else [
            "Clementine.safetensors",
            "Clementine_000000250.safetensors",
            "Clementine_000000500.safetensors",
        ]
    )


def test_a_run_outside_the_source_folder_keeps_its_files(shelf, tmp_path):
    """Only the folder holding the run decides: another one's switch is not it."""
    run_id = _a_training_run(shelf["hub"])
    with shelf["hub"].transaction() as conn:
        conn.execute(
            "UPDATE model_folder SET is_download_target = 1 WHERE id = ?",
            (shelf["destination_id"],),
        )
        conn.execute(
            "UPDATE model_folder SET delete_after_import = 1 WHERE id = ?",
            (shelf["source_id"],),
        )
    elsewhere = tmp_path / "elsewhere" / "Clementine"
    shutil.copytree(shelf["run_dir"], elsewhere)

    Vault._import_training_run(
        SimpleNamespace(_hub=shelf["hub"]),
        str(elsewhere),
        {"id": run_id, "name": "Clementine", "set_id": None, "character_id": None},
    )

    assert len(list(elsewhere.glob("*.safetensors"))) == 3


def test_a_source_on_another_drive_is_skipped_not_fatal(shelf, monkeypatch):
    """Windows: commonpath raises across drives. The run still imports.

    The source that cannot share a path with the run is not the run's folder,
    so its delete_after_import switch does not apply and the files stay.
    """
    run_id = _a_training_run(shelf["hub"])
    with shelf["hub"].transaction() as conn:
        conn.execute(
            "UPDATE model_folder SET is_download_target = 1 WHERE id = ?",
            (shelf["destination_id"],),
        )
        conn.execute(
            "UPDATE model_folder SET delete_after_import = 1 WHERE id = ?",
            (shelf["source_id"],),
        )
    source_root = os.path.realpath(str(shelf["output_root"]))
    real_commonpath = os.path.commonpath

    def other_drive(paths):
        if list(paths)[0] == source_root:
            raise ValueError("Paths don't have the same drive")
        return real_commonpath(paths)

    monkeypatch.setattr(os.path, "commonpath", other_drive)

    result = Vault._import_training_run(
        SimpleNamespace(_hub=shelf["hub"]),
        str(shelf["run_dir"]),
        {"id": run_id, "name": "Clementine", "set_id": None, "character_id": None},
    )

    assert result["imported"] == 3
    assert len(list(shelf["run_dir"].glob("*.safetensors"))) == 3


def test_a_cover_whose_row_vanished_does_not_fail_the_run(shelf, monkeypatch):
    """The files have landed by the time the cover's hash is read.

    A row that is gone then must not turn a finished import into a failed job:
    the run still lands and the cover simply has no hash to attach.
    """
    run_id = _a_training_run(shelf["hub"])
    with shelf["hub"].transaction() as conn:
        conn.execute(
            "UPDATE model_folder SET is_download_target = 1 WHERE id = ?",
            (shelf["destination_id"],),
        )
    real_fetchone = shelf["hub"].fetchone

    def vanished(sql, *args):
        if sql.startswith("SELECT sha256 FROM model WHERE id"):
            return None
        return real_fetchone(sql, *args)

    monkeypatch.setattr(shelf["hub"], "fetchone", vanished)

    result = Vault._import_training_run(
        SimpleNamespace(_hub=shelf["hub"]),
        str(shelf["run_dir"]),
        {"id": run_id, "name": "Clementine", "set_id": None, "character_id": None},
    )

    assert result["imported"] == 3
    assert result["cover_sha256"] is None


def _cover_of(hub, run_id):
    """The step of the run's stack cover; None names the bare final."""
    row = hub.fetchone(
        "SELECT training_step FROM model WHERE training_run_id = ? "
        "AND stack_position = 0",
        (run_id,),
    )
    return row["training_step"] if row is not None else "no cover"


def _download_target(shelf):
    with shelf["hub"].transaction() as conn:
        conn.execute(
            "UPDATE model_folder SET is_download_target = 1 WHERE id = ?",
            (shelf["destination_id"],),
        )


def test_a_step_chosen_while_training_is_the_cover_when_the_run_lands(shelf):
    """The live view's "Use this step", made before the run was on the shelf."""
    run_id = _a_training_run(shelf["hub"])
    training_runs.update_run(shelf["hub"], "run-clementine", cover_step=250)
    _download_target(shelf)

    Vault._import_training_run(
        SimpleNamespace(_hub=shelf["hub"]),
        str(shelf["run_dir"]),
        {"id": run_id, "uuid": "run-clementine", "name": "Clementine"},
    )

    assert _cover_of(shelf["hub"], run_id) == 250


def test_a_chosen_step_the_trainer_pruned_leaves_the_final_the_cover(shelf):
    run_id = _a_training_run(shelf["hub"])
    training_runs.update_run(shelf["hub"], "run-clementine", cover_step=125)
    _download_target(shelf)

    Vault._import_training_run(
        SimpleNamespace(_hub=shelf["hub"]),
        str(shelf["run_dir"]),
        {"id": run_id, "uuid": "run-clementine", "name": "Clementine"},
    )

    assert _cover_of(shelf["hub"], run_id) is None


def test_choosing_a_step_after_the_import_moves_the_cover_and_the_sets_lora(
    shelf, monkeypatch
):
    """The set's attached LoRA is the step chosen, not the last one."""

    run_id = _a_training_run(shelf["hub"])
    _download_target(shelf)
    held: dict[str, list] = {}

    def fetch(vault, *, sha256=None):
        return {
            sha256: [
                {"entity_type": t, "entity_id": i} for t, i in held.get(sha256, [])
            ]
        }

    def replace(vault, sha256, wanted):
        held[sha256] = list(wanted)
        return []

    monkeypatch.setattr(vault_module, "fetch_attachments", fetch)
    monkeypatch.setattr(vault_module, "replace_attachments", replace)
    stand_in = SimpleNamespace(_hub=shelf["hub"])
    run = {"id": run_id, "uuid": "run-clementine", "name": "Clementine", "set_id": 7}
    Vault._import_training_run(stand_in, str(shelf["run_dir"]), run)
    shas = {
        row["training_step"]: row["sha256"]
        for row in shelf["hub"].fetchall(
            "SELECT training_step, sha256 FROM model WHERE training_run_id = ?",
            (run_id,),
        )
    }
    assert held == {shas[None]: [("set", 7)]}

    assert Vault.choose_training_cover(stand_in, {**run, "total_steps": 750}, 500)

    assert _cover_of(shelf["hub"], run_id) == 500
    assert held[shas[None]] == []
    assert held[shas[500]] == [("set", 7)]
    with pytest.raises(LookupError):
        Vault.choose_training_cover(stand_in, {**run, "total_steps": 750}, 125)
