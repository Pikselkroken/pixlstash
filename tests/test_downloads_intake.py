"""The watched Downloads folder: what is taken, where it goes, what stays."""

import json
import os
import sqlite3
import struct

import pytest

from pixlstash.event_types import EventType
from pixlstash.services import downloads_intake
from pixlstash.services.downloads_intake import (
    OUTCOME_RETRY,
    OUTCOME_LEFT,
    OUTCOME_MOVED,
    candidates,
    safetensors_complete,
    take_in,
)
from pixlstash.services.managed_model_store import find_default_folder
from pixlstash.services.model_folder_scanner import ModelFolderScanner
from pixlstash.services.model_mover import SHELF_IO_LOCK
from pixlstash.tasks import downloads_intake_finder
from pixlstash.tasks.downloads_intake_finder import DownloadsIntakeFinder


def _write_safetensors(path, tensors, payload=b""):
    blob = json.dumps(tensors).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(blob)) + blob + payload)
    return str(path)


def write_adapter(path):
    """A LoRA whose header states 16 bytes of tensor data, and has them."""
    tensors = {
        "blocks.0.lora_A.weight": {
            "dtype": "F16",
            "shape": [2, 2],
            "data_offsets": [0, 8],
        },
        "blocks.0.lora_B.weight": {
            "dtype": "F16",
            "shape": [2, 2],
            "data_offsets": [8, 16],
        },
    }
    return _write_safetensors(path, tensors, payload=b"\x00" * 16)


def _register(hub, path, kind, is_default=0):
    with hub.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO model_folder (path, kind, movable, is_default, created_at) "
            "VALUES (?, ?, 'per_item', ?, '2026-10-14T00:00:00+00:00')",
            (str(path), kind, is_default),
        )
        return int(cursor.lastrowid)


@pytest.fixture
def folders(hub, tmp_path):
    """A Downloads folder, a default folder, and an ordinary one beside it."""
    downloads = tmp_path / "Downloads"
    default = tmp_path / "loras"
    other = tmp_path / "other"
    for path in (downloads, default, other):
        path.mkdir()
    _register(hub, downloads, "downloads")
    _register(hub, other, "user")
    default_id = _register(hub, default, "user", is_default=1)
    return downloads, default, default_id


def _located(hub, folder_id):
    return {
        row["relpath"]
        for row in hub.fetchall(
            "SELECT relpath FROM model_file WHERE model_folder_id = ?", (folder_id,)
        )
    }


def test_the_marked_folder_is_the_default_and_the_managed_store_is_not(hub, folders):
    _, default, default_id = folders
    assert find_default_folder(hub)["id"] == default_id


def test_a_finished_download_is_moved_into_the_default_folder(hub, folders):
    downloads, default, default_id = folders
    source = write_adapter(downloads / "Jimmy.safetensors")

    outcome, model_id = take_in(hub, source)

    assert outcome == OUTCOME_MOVED and model_id is not None
    assert not os.path.exists(source)
    assert (default / "Jimmy.safetensors").is_file()
    assert _located(hub, default_id) == {"Jimmy.safetensors"}


def test_across_drives_it_is_copied_verified_and_only_then_removed(
    hub, folders, monkeypatch
):
    downloads, default, default_id = folders
    source = write_adapter(downloads / "Across.safetensors")
    monkeypatch.setattr(downloads_intake, "same_device", lambda *_: False)

    outcome, model_id = take_in(hub, source)

    assert outcome == OUTCOME_MOVED
    assert not os.path.exists(source)
    assert _located(hub, default_id) == {"Across.safetensors"}
    row = hub.fetchone("SELECT sha256 FROM model WHERE id = ?", (model_id,))
    assert row["sha256"], "the copy path verified a digest and must keep it"


def test_a_name_taken_in_the_default_folder_leaves_the_download_alone(hub, folders):
    downloads, default, default_id = folders
    source = write_adapter(downloads / "Taken.safetensors")
    (default / "Taken.safetensors").write_bytes(b"the owner's own file")

    assert take_in(hub, source) == (OUTCOME_LEFT, None)

    assert os.path.exists(source)
    assert (default / "Taken.safetensors").read_bytes() == b"the owner's own file"
    assert _located(hub, default_id) == set()


def test_a_file_that_is_not_a_model_goes_back_to_downloads(hub, folders):
    downloads, default, default_id = folders
    source = downloads / "broken.safetensors"
    source.write_bytes(b"not a header at all")

    assert take_in(hub, str(source)) == (OUTCOME_LEFT, None)

    assert source.read_bytes() == b"not a header at all"
    assert not (default / "broken.safetensors").exists()
    assert _located(hub, default_id) == set()


def test_a_busy_shelf_is_a_retry_not_a_refusal(hub, folders):
    downloads, _, _ = folders
    source = write_adapter(downloads / "Later.safetensors")
    with SHELF_IO_LOCK:
        assert take_in(hub, source) == (OUTCOME_RETRY, None)
    assert os.path.exists(source)


def test_a_truncated_safetensors_is_not_complete(tmp_path):
    whole = write_adapter(tmp_path / "whole.safetensors")
    assert safetensors_complete(whole)
    with open(whole, "rb") as handle:
        data = handle.read()
    cut = tmp_path / "cut.safetensors"
    cut.write_bytes(data[:-1])
    assert not safetensors_complete(str(cut))
    # A gguf states no total length; the settle window is all it gets.
    gguf = tmp_path / "model.gguf"
    gguf.write_bytes(b"GGUF")
    assert safetensors_complete(str(gguf))


def test_only_finished_model_files_at_the_top_are_candidates(tmp_path):
    folder = tmp_path / "Downloads"
    (folder / "nested").mkdir(parents=True)
    keep = write_adapter(folder / "keep.safetensors")
    (folder / "keep.gguf").write_bytes(b"GGUF")
    # Firefox: an empty file at the final name beside the real `.part`.
    (folder / "firefox.safetensors").write_bytes(b"")
    (folder / "firefox.safetensors.part").write_bytes(b"x")
    # Chrome: the real name exists only once the download is done, but a file
    # beside a `.crdownload` of the same name is still in flight.
    write_adapter(folder / "chrome.safetensors")
    (folder / "chrome.safetensors.crdownload").write_bytes(b"x")
    write_adapter(folder / "nested" / "deep.safetensors")
    (folder / "notes.txt").write_text("not a model")
    os.symlink(keep, folder / "link.safetensors")

    assert sorted(os.path.basename(p) for p in candidates(str(folder))) == [
        "keep.gguf",
        "keep.safetensors",
    ]


def test_the_finder_waits_for_a_file_to_settle_then_moves_it(hub, folders, monkeypatch):
    downloads, default, default_id = folders
    monkeypatch.setattr(downloads_intake_finder, "_CHECK_INTERVAL_S", 0.0)
    monkeypatch.setattr(DownloadsIntakeFinder, "SETTLE_SECONDS", 0.0)
    events = []
    finder = DownloadsIntakeFinder(hub, notifier=lambda *a: events.append(a))
    write_adapter(downloads / "Settled.safetensors")

    assert finder.find_task() is None, "the first sighting only starts the clock"
    task = finder.find_task()
    assert task is not None
    task._run_task()
    finder.on_task_complete(task, None)

    assert _located(hub, default_id) == {"Settled.safetensors"}
    assert [event for event, _ in events] == [EventType.MODELS_ARRIVED]
    assert finder.find_task() is None


def test_the_finder_never_hands_out_a_file_still_changing(hub, folders, monkeypatch):
    downloads, _, _ = folders
    monkeypatch.setattr(downloads_intake_finder, "_CHECK_INTERVAL_S", 0.0)
    monkeypatch.setattr(DownloadsIntakeFinder, "SETTLE_SECONDS", 3600.0)
    finder = DownloadsIntakeFinder(hub)
    write_adapter(downloads / "Slow.safetensors")
    assert finder.find_task() is None
    assert finder.find_task() is None


def test_a_file_kept_out_by_a_busy_shelf_is_offered_again(hub, folders, monkeypatch):
    downloads, _, default_id = folders
    monkeypatch.setattr(downloads_intake_finder, "_CHECK_INTERVAL_S", 0.0)
    monkeypatch.setattr(DownloadsIntakeFinder, "SETTLE_SECONDS", 0.0)
    finder = DownloadsIntakeFinder(hub)
    write_adapter(downloads / "Retry.safetensors")
    finder.find_task()
    task = finder.find_task()
    with SHELF_IO_LOCK:
        task._run_task()
    finder.on_task_complete(task, None)
    assert task.retry_paths

    finder.find_task()
    again = finder.find_task()
    assert again is not None, "a busy file was dropped instead of retried"
    again._run_task()
    assert _located(hub, default_id) == {"Retry.safetensors"}


def test_a_file_left_in_downloads_is_not_retried_every_sweep(hub, folders, monkeypatch):
    downloads, default, _ = folders
    monkeypatch.setattr(downloads_intake_finder, "_CHECK_INTERVAL_S", 0.0)
    monkeypatch.setattr(DownloadsIntakeFinder, "SETTLE_SECONDS", 0.0)
    finder = DownloadsIntakeFinder(hub)
    write_adapter(downloads / "Clash.safetensors")
    (default / "Clash.safetensors").write_bytes(b"already here")
    finder.find_task()
    task = finder.find_task()
    task._run_task()
    finder.on_task_complete(task, None)

    assert finder.find_task() is None
    assert finder.find_task() is None


def test_a_deleted_copy_left_as_a_tombstone_does_not_refuse_a_new_download(
    hub, folders
):
    """The scanner keeps a deleted file's row as `missing`; downloading the
    same model again must take that row back over, not be refused by it."""
    downloads, default, default_id = folders
    first = write_adapter(downloads / "Again.safetensors")
    assert take_in(hub, first)[0] == OUTCOME_MOVED
    os.unlink(default / "Again.safetensors")
    with hub.transaction() as conn:
        conn.execute("UPDATE model_file SET state = 'missing'")

    again = write_adapter(downloads / "Again.safetensors")
    assert take_in(hub, again)[0] == OUTCOME_MOVED
    row = hub.fetchone(
        "SELECT state FROM model_file WHERE model_folder_id = ? AND relpath = ?",
        (default_id, "Again.safetensors"),
    )
    assert row["state"] == "present"


@pytest.mark.parametrize("across_drives", [False, True])
def test_a_hub_that_refuses_the_row_undoes_the_move(
    hub, folders, monkeypatch, across_drives
):
    downloads, default, default_id = folders
    source = write_adapter(downloads / "Locked.safetensors")
    monkeypatch.setattr(downloads_intake, "same_device", lambda *_: not across_drives)

    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ModelFolderScanner, "register_file", locked)

    assert take_in(hub, source) == (OUTCOME_RETRY, None)
    assert os.path.exists(source)
    assert not (default / "Locked.safetensors").exists()


def test_across_drives_a_download_that_grew_during_the_copy_is_kept(
    hub, folders, monkeypatch
):
    downloads, default, default_id = folders
    source = write_adapter(downloads / "Growing.safetensors")
    monkeypatch.setattr(downloads_intake, "same_device", lambda *_: False)
    real_copy = downloads_intake.copy_and_digest

    def copy_then_grow(src, dst):
        digest = real_copy(src, dst)
        with open(src, "ab") as handle:
            handle.write(b"more")
        return digest

    monkeypatch.setattr(downloads_intake, "copy_and_digest", copy_then_grow)

    assert take_in(hub, source)[0] == OUTCOME_MOVED
    assert os.path.exists(source), "the longer original was unlinked"
    assert _located(hub, default_id) == {"Growing.safetensors"}


def test_an_unplugged_default_folder_is_retried_once_it_is_back(
    hub, folders, monkeypatch, tmp_path
):
    downloads, default, default_id = folders
    monkeypatch.setattr(downloads_intake_finder, "_CHECK_INTERVAL_S", 0.0)
    monkeypatch.setattr(DownloadsIntakeFinder, "SETTLE_SECONDS", 0.0)
    finder = DownloadsIntakeFinder(hub)
    write_adapter(downloads / "Wait.safetensors")
    away = tmp_path / "unplugged"
    default.rename(away)

    finder.find_task()
    task = finder.find_task()
    task._run_task()
    finder.on_task_complete(task, None)
    assert task.retry_paths

    away.rename(default)
    finder.find_task()
    again = finder.find_task()
    assert again is not None, "the download was stranded after the outage"
    again._run_task()
    assert _located(hub, default_id) == {"Wait.safetensors"}


@pytest.mark.parametrize("bad_end", ["16", -1, True, 1.5, None])
def test_a_malformed_header_is_incomplete_not_a_crash(tmp_path, bad_end):
    """The header is the download's own claim: a non-integer or negative end
    must neither raise nor make any length pass."""
    path = _write_safetensors(
        tmp_path / "bad.safetensors",
        {"t": {"dtype": "F16", "shape": [1], "data_offsets": [0, bad_end]}},
        payload=b"\x00" * 16,
    )
    assert safetensors_complete(path) is False


def test_a_settled_corrupt_file_is_read_once_until_it_changes(
    hub, folders, monkeypatch
):
    downloads, _, _ = folders
    monkeypatch.setattr(downloads_intake_finder, "_CHECK_INTERVAL_S", 0.0)
    monkeypatch.setattr(DownloadsIntakeFinder, "SETTLE_SECONDS", 0.0)
    reads = []
    monkeypatch.setattr(
        downloads_intake_finder,
        "safetensors_complete",
        lambda path: reads.append(path) or safetensors_complete(path),
    )
    finder = DownloadsIntakeFinder(hub)
    _write_safetensors(
        downloads / "Corrupt.safetensors",
        {"t": {"dtype": "F16", "shape": [1], "data_offsets": [0, "x"]}},
        payload=b"\x00" * 16,
    )
    for _ in range(4):
        assert finder.find_task() is None
    assert len(reads) == 1


def test_the_hub_holds_at_most_one_downloads_folder(hub, folders, tmp_path):
    """The route checks first, but the database is what holds the rule
    against two registrations racing past that check."""
    (tmp_path / "second").mkdir()
    with pytest.raises(sqlite3.IntegrityError):
        _register(hub, tmp_path / "second", "downloads")
