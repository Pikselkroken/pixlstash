"""The move invariant (shelf plan B7), and what a crash in the middle of it leaves.

**copy → verify by SHA-256 → repoint the row and commit → then unlink.** Every
other order has a window whose residue is a ``model_file`` row naming a path
where no file is, and the shelf's whole tombstone design rests on a row that
names a file always naming a file that exists.

The two crash-window tests here are the acceptance bar (plan §6 item 3). They do
not assert the happy path and then claim a guarantee: each one **interrupts** the
mover inside a specific window and then reads the disk and the hub to see what
survived. The interruption is raised as a ``BaseException`` subclass on purpose —
``except Exception`` handlers do not catch it and no cleanup runs, so what the
test observes is what a killed process would have left, not what an error handler
tidied up.

Both windows must leave a **duplicate** — the file readable at both paths, and the
row naming one of them that exists. Neither may leave a dangling row.

Environment: no ``Server``. A hub is a stdlib sqlite3 file and the fixtures are
two directories and a few small files, so the whole module costs milliseconds;
standing up a server would cost 1.35 s and prove nothing extra. The route half
(both authz directions) lives in the warm ``tests/test_model_shelf_api.py``.
"""

from __future__ import annotations

import hashlib
import os

import pytest

from pixlstash.hub.db import HubDatabase
from pixlstash.services import model_mover as mover_module
from pixlstash.services.model_mover import (
    PARTIAL_SUFFIX,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_MOVED,
    ModelMover,
    MoveRefused,
)


class Crash(BaseException):
    """A process death, as far as the code under test can tell.

    ``BaseException`` rather than ``Exception`` deliberately: the mover catches
    ``OSError`` to report a failed file and would otherwise turn the simulated
    crash into a tidy, cleaned-up error — which is precisely the state a real
    crash does *not* leave behind.
    """


@pytest.fixture
def hub(tmp_path):
    database = HubDatabase(str(tmp_path / "hub.db"))
    yield database
    database.close()


def register_folder(hub, path, kind="user"):
    with hub.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO model_folder (path, kind, movable, created_at) "
            "VALUES (?, ?, 'per_item', '2026-08-09T00:00:00+00:00')",
            (str(path), kind),
        )
        return int(cursor.lastrowid)


def register_file(hub, folder_id, path, relpath, *, sha256="auto"):
    """Put a real file on disk and the two rows that describe it in the hub."""
    # Seeded by the whole relpath, not the basename: two copies at the same
    # basename in different subdirectories are two *different* adapters here, and
    # ``model.sha256`` is UNIQUE.
    body = relpath.encode() * 512
    abs_path = os.path.join(str(path), relpath)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as handle:
        handle.write(body)
    if sha256 == "auto":
        sha256 = hashlib.sha256(body).hexdigest()
    with hub.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO model (file_kind, kind, sha256, filename, provenance, "
            "file_size, created_at) VALUES ('adapter', 'lora', ?, ?, 'external', "
            "?, '2026-08-09T00:00:00+00:00')",
            (sha256, os.path.basename(relpath), len(body)),
        )
        model_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state, "
            "seen_at, file_mtime) VALUES (?, ?, ?, 'present', "
            "'2026-08-09T00:00:00+00:00', ?)",
            (model_id, folder_id, relpath, os.stat(abs_path).st_mtime_ns),
        )
    return model_id, abs_path


def locations(hub):
    """``{(folder_id, relpath): row}`` — every registered copy."""
    return {
        (row["model_folder_id"], row["relpath"]): row
        for row in hub.fetchall("SELECT * FROM model_file")
    }


def assert_no_dangling_rows(hub):
    """Every registered copy names a file that is on disk.

    The single property the ordering exists to preserve, asserted directly
    rather than inferred from a status string.
    """
    dangling = []
    for row in hub.fetchall(
        "SELECT f.path, mf.relpath FROM model_file mf "
        "JOIN model_folder f ON f.id = mf.model_folder_id"
    ):
        full = os.path.join(row["path"], row["relpath"])
        if not os.path.exists(full):
            dangling.append(full)
    assert not dangling, f"model_file row(s) name files that do not exist: {dangling}"


@pytest.fixture
def two_folders(hub, tmp_path):
    """A source folder with one adapter in it, and an empty destination."""
    source_dir = tmp_path / "loras"
    destination_dir = tmp_path / "archive"
    source_dir.mkdir()
    destination_dir.mkdir()
    source_id = register_folder(hub, source_dir)
    destination_id = register_folder(hub, destination_dir)
    model_id, source_path = register_file(
        hub, source_id, source_dir, "alice.safetensors"
    )
    return {
        "hub": hub,
        "source_dir": source_dir,
        "destination_dir": destination_dir,
        "source_id": source_id,
        "destination_id": destination_id,
        "model_id": model_id,
        "source_path": source_path,
        "destination_path": str(destination_dir / "alice.safetensors"),
    }


@pytest.fixture
def cross_device(monkeypatch):
    """Force the copy path.

    ``tmp_path`` puts both directories on one filesystem on every machine this
    suite runs on, so without this the mover would correctly choose ``rename``
    and the copy/verify/commit/unlink invariant — the thing under test — would
    never execute.
    """
    monkeypatch.setattr(mover_module, "same_device", lambda *_: False)


# ===========================================================================
# The two crash windows — the acceptance bar (plan §6 item 3)
# ===========================================================================


def test_crash_after_the_copy_and_before_the_commit_leaves_a_duplicate(
    two_folders, cross_device, monkeypatch
):
    """Window 1. The bytes are at both paths; the row still names the source.

    Nothing has been unlinked, because the unlink is last. The residue is a
    duplicate — one content row, one location row, and a second file on disk
    that the next scan of the destination folder will register as a second
    location of the same model, which is exactly what a duplicate is here.
    """
    monkeypatch.setattr(
        ModelMover,
        "_repoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(Crash("killed after the copy")),
    )
    mover = ModelMover(two_folders["hub"])
    plan = mover.plan(
        [(two_folders["source_id"], "alice.safetensors")], two_folders["destination_id"]
    )

    with pytest.raises(Crash):
        mover.execute(plan)

    assert os.path.exists(two_folders["source_path"]), (
        "the source was unlinked before the commit — the invariant is inverted"
    )
    assert os.path.exists(two_folders["destination_path"])
    rows = locations(two_folders["hub"])
    assert set(rows) == {(two_folders["source_id"], "alice.safetensors")}
    assert_no_dangling_rows(two_folders["hub"])


def test_crash_after_the_commit_and_before_the_unlink_leaves_a_duplicate(
    two_folders, cross_device, monkeypatch
):
    """Window 2. The bytes are at both paths; the row names the destination.

    This is the window the ordering was chosen for. Reverse commit and unlink and
    the residue here becomes a row naming a file that no longer exists, which is
    the dangling row the shelf must never produce.
    """
    monkeypatch.setattr(
        mover_module,
        "unlink_source",
        lambda *args: (_ for _ in ()).throw(Crash("killed after the commit")),
    )
    mover = ModelMover(two_folders["hub"])
    plan = mover.plan(
        [(two_folders["source_id"], "alice.safetensors")], two_folders["destination_id"]
    )

    with pytest.raises(Crash):
        mover.execute(plan)

    assert os.path.exists(two_folders["source_path"])
    assert os.path.exists(two_folders["destination_path"])
    rows = locations(two_folders["hub"])
    assert set(rows) == {(two_folders["destination_id"], "alice.safetensors")}, (
        "the row was not repointed before the unlink was attempted"
    )
    assert_no_dangling_rows(two_folders["hub"])


def test_the_steps_happen_in_the_only_safe_order(
    two_folders, cross_device, monkeypatch
):
    """Structural, so a refactor that keeps every test above passing but reorders
    the steps still fails. The two crash tests can only observe the boundaries
    they interrupt; this one observes the whole sequence."""
    events: list[str] = []

    original_copy = mover_module.copy_and_digest
    original_digest = mover_module.file_digest
    original_repoint = ModelMover._repoint
    original_unlink = mover_module.unlink_source

    def traced_copy(source, destination):
        events.append("copy")
        return original_copy(source, destination)

    def traced_digest(path):
        events.append("verify")
        return original_digest(path)

    def traced_repoint(self, *args, **kwargs):
        events.append("commit")
        return original_repoint(self, *args, **kwargs)

    def traced_unlink(path):
        events.append("unlink")
        return original_unlink(path)

    monkeypatch.setattr(mover_module, "copy_and_digest", traced_copy)
    monkeypatch.setattr(mover_module, "file_digest", traced_digest)
    monkeypatch.setattr(ModelMover, "_repoint", traced_repoint)
    monkeypatch.setattr(mover_module, "unlink_source", traced_unlink)

    mover = ModelMover(two_folders["hub"])
    plan = mover.plan(
        [(two_folders["source_id"], "alice.safetensors")], two_folders["destination_id"]
    )
    mover.execute(plan)

    assert events == ["copy", "verify", "commit", "unlink"], events


# ===========================================================================
# The move itself
# ===========================================================================


def test_a_cross_device_move_ends_with_one_copy_and_a_repointed_row(
    two_folders, cross_device
):
    mover = ModelMover(two_folders["hub"])
    before = os.stat(two_folders["source_path"]).st_mtime_ns
    report = mover.execute(
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_MOVED]
    assert not os.path.exists(two_folders["source_path"])
    assert os.path.exists(two_folders["destination_path"])
    assert not os.path.exists(two_folders["destination_path"] + PARTIAL_SUFFIX)
    rows = locations(two_folders["hub"])
    assert set(rows) == {(two_folders["destination_id"], "alice.safetensors")}
    assert (
        rows[(two_folders["destination_id"], "alice.safetensors")]["state"] == "present"
    )
    # Preserved, or the next scan re-hashes every byte it just moved: the scan's
    # short circuit compares st_mtime_ns against the stored value.
    assert os.stat(two_folders["destination_path"]).st_mtime_ns == before
    assert_no_dangling_rows(two_folders["hub"])


def test_a_same_drive_move_is_a_rename_and_copies_nothing(two_folders):
    """The ruling: same drive skips the copy, the verify and the space check.
    Proved by the inode surviving — a copy would allocate a new one — and by no
    partial file ever appearing."""
    source_inode = os.stat(two_folders["source_path"]).st_ino

    mover = ModelMover(two_folders["hub"])
    plan = mover.plan(
        [(two_folders["source_id"], "alice.safetensors")], two_folders["destination_id"]
    )
    assert plan.bytes_to_copy == 0, "a rename must not be counted as bytes to copy"
    assert plan.moves[0].same_device is True
    mover.execute(plan)

    # A copy allocates a new inode; a rename does not. This is the whole claim.
    assert os.stat(two_folders["destination_path"]).st_ino == source_inode
    assert not os.path.exists(two_folders["destination_path"] + PARTIAL_SUFFIX)
    assert not os.path.exists(two_folders["source_path"])
    assert_no_dangling_rows(two_folders["hub"])


def test_a_copy_that_does_not_verify_leaves_the_original_alone(
    two_folders, cross_device, monkeypatch
):
    """A bad write must cost the copy, never the original. The failure is
    reported per file and the source is still exactly where the row says."""
    # The destination reads back as something else entirely.
    monkeypatch.setattr(mover_module, "file_digest", lambda path: "0" * 64)
    mover = ModelMover(two_folders["hub"])
    report = mover.execute(
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_FAILED]
    assert os.path.exists(two_folders["source_path"])
    assert not os.path.exists(two_folders["destination_path"])
    assert not os.path.exists(two_folders["destination_path"] + PARTIAL_SUFFIX)
    assert set(locations(two_folders["hub"])) == {
        (two_folders["source_id"], "alice.safetensors")
    }
    assert_no_dangling_rows(two_folders["hub"])


def test_a_stale_recorded_hash_stops_the_move(two_folders, cross_device, hub):
    """``model.sha256`` is the interop identity: the Civitai lookup and the
    public ``{sha256}/file`` route both resolve on it. Carrying a hash that no
    longer names the bytes to a new path would spread the lie rather than fix
    it, so the move refuses and asks for a rescan."""
    with hub.transaction() as conn:
        conn.execute("UPDATE model SET sha256 = ?", ("f" * 64,))
    mover = ModelMover(hub)
    report = mover.execute(
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    )
    assert [outcome.status for outcome in report.outcomes] == [STATUS_FAILED]
    assert "registered as" in report.outcomes[0].detail
    assert os.path.exists(two_folders["source_path"])
    assert not os.path.exists(two_folders["destination_path"])


def test_an_unhashed_checkpoint_still_verifies(two_folders, cross_device, hub):
    """A 24 GB checkpoint registers with ``sha256`` NULL. There is no recorded
    hash to compare against, so the verification is source-stream digest against
    destination re-read — which is the part that actually proves the copy."""
    with hub.transaction() as conn:
        conn.execute("UPDATE model SET sha256 = NULL, file_kind = 'checkpoint'")
    mover = ModelMover(hub)
    report = mover.execute(
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    )
    assert [outcome.status for outcome in report.outcomes] == [STATUS_MOVED]
    assert os.path.exists(two_folders["destination_path"])
    assert_no_dangling_rows(hub)


# ===========================================================================
# Refused before the first byte
# ===========================================================================


def test_space_is_checked_before_anything_is_written(
    two_folders, cross_device, monkeypatch
):
    """Per-file checking would fill the disk and fail on file 1,500 of 1,806,
    having already moved 1,499 — and there is no undo for shelf operations."""
    import shutil

    monkeypatch.setattr(
        mover_module.shutil,
        "disk_usage",
        lambda path: shutil._ntuple_diskusage(total=1, used=1, free=1),
    )
    mover = ModelMover(two_folders["hub"])
    with pytest.raises(MoveRefused) as excinfo:
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )

    assert excinfo.value.status_code == 507
    assert os.path.exists(two_folders["source_path"])
    assert os.listdir(two_folders["destination_dir"]) == []


def test_a_relpath_that_escapes_its_folder_is_refused_not_unlinked(hub, tmp_path):
    """Containment (#776) on the delete/write path. ``model_file.relpath`` is a
    database value: a faulty scan, a restored hub or a bug can put anything in
    it, and this module is the one that unlinks what it names."""
    source_dir = tmp_path / "loras"
    destination_dir = tmp_path / "archive"
    source_dir.mkdir()
    destination_dir.mkdir()
    outsider = tmp_path / "precious.safetensors"
    outsider.write_bytes(b"not the shelf's to delete")

    source_id = register_folder(hub, source_dir)
    destination_id = register_folder(hub, destination_dir)
    with hub.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO model (file_kind, kind, sha256, filename, provenance, "
            "file_size, created_at) VALUES ('adapter', 'lora', ?, 'precious.safetensors',"
            " 'external', 25, '2026-08-09T00:00:00+00:00')",
            ("a" * 64,),
        )
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state, "
            "seen_at) VALUES (?, ?, '../precious.safetensors', 'present', "
            "'2026-08-09T00:00:00+00:00')",
            (int(cursor.lastrowid), source_id),
        )

    mover = ModelMover(hub)
    with pytest.raises(MoveRefused, match="outside its registered folder"):
        mover.plan([(source_id, "../precious.safetensors")], destination_id)

    assert outsider.exists(), "a row outside the folder was acted on"
    assert os.listdir(destination_dir) == []


@pytest.mark.parametrize("force_copy", [False, True], ids=["rename", "copy"])
def test_a_destination_taken_after_planning_is_refused_not_clobbered(
    two_folders, monkeypatch, force_copy
):
    """The plan-time check is not enough, on either path.

    ``plan`` runs inside the POST and ``os.replace`` / ``os.rename`` run minutes
    later on the worker thread, and both overwrite in silence. Before the
    execution-time re-check this destroyed the file that arrived in between and
    reported ``moved``. Reproduced deterministically — write the destination
    between ``plan`` and ``execute`` — rather than by threading, because the
    window is the whole gap and needs no timing to enter.
    """
    if force_copy:
        monkeypatch.setattr(mover_module, "same_device", lambda *_: False)
    mover = ModelMover(two_folders["hub"])
    plan = mover.plan(
        [(two_folders["source_id"], "alice.safetensors")], two_folders["destination_id"]
    )

    arrived = b"written after the move was planned"
    with open(two_folders["destination_path"], "wb") as handle:
        handle.write(arrived)

    report = mover.execute(plan)

    assert [outcome.status for outcome in report.outcomes] == [STATUS_FAILED]
    with open(two_folders["destination_path"], "rb") as handle:
        assert handle.read() == arrived, "the move overwrote a file it never named"
    assert os.path.exists(two_folders["source_path"])
    assert set(locations(two_folders["hub"])) == {
        (two_folders["source_id"], "alice.safetensors")
    }
    assert_no_dangling_rows(two_folders["hub"])


@pytest.mark.parametrize("force_copy", [False, True], ids=["rename", "copy"])
def test_a_destination_row_that_appears_at_commit_time_leaves_no_dangling_row(
    two_folders, hub, monkeypatch, force_copy
):
    """The second-order damage the ordering is supposed to make impossible.

    A rescan writes ``model_file`` and is deliberately **not** under
    ``SHELF_IO_LOCK``, so a row can appear at the destination key after the
    execution-time check and before the commit. ``_repoint``'s UPDATE then
    violates ``UNIQUE(model_folder_id, relpath)``, and an ``IntegrityError`` is
    not an ``OSError``: it used to escape the per-file handler entirely, so the
    source was never unlinked and — on the rename path — never put back either,
    leaving a committed row naming content it does not describe.

    Simulated by inserting the racing row from inside ``_repoint``, which is the
    exact instant the race has to land on.
    """
    if force_copy:
        monkeypatch.setattr(mover_module, "same_device", lambda *_: False)
    original_repoint = ModelMover._repoint

    def racing_repoint(self, move, plan, **kwargs):
        with hub.transaction() as conn:
            intruder = int(
                conn.execute(
                    "INSERT INTO model (file_kind, kind, sha256, filename, "
                    "provenance, file_size, created_at) VALUES ('adapter', "
                    "'lora', ?, 'alice.safetensors', 'external', 9, "
                    "'2026-08-09T00:00:00+00:00')",
                    ("b" * 64,),
                ).lastrowid
            )
            conn.execute(
                "INSERT INTO model_file (model_id, model_folder_id, relpath, "
                "state, seen_at) VALUES (?, ?, 'alice.safetensors', 'present', "
                "'2026-08-09T00:00:00+00:00')",
                (intruder, two_folders["destination_id"]),
            )
        return original_repoint(self, move, plan, **kwargs)

    monkeypatch.setattr(ModelMover, "_repoint", racing_repoint)
    mover = ModelMover(hub)
    report = mover.execute(
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_FAILED]
    # The file is back where its row says it is, on both paths: renamed back
    # after the rename, or the copy discarded after the copy.
    assert os.path.exists(two_folders["source_path"])
    assert not os.path.exists(two_folders["destination_path"])
    assert (two_folders["source_id"], "alice.safetensors") in locations(hub)
    # The racing row is left for the rescan that owns it — deleting somebody
    # else's bookkeeping from inside a failed move is how a tombstone goes
    # missing — so ``assert_no_dangling_rows`` is deliberately not used here.
    assert (two_folders["destination_id"], "alice.safetensors") in locations(hub)


def test_an_existing_destination_file_is_never_overwritten(two_folders, cross_device):
    """There is no undo for shelf operations, so a move must not destroy a file
    the caller never named."""
    existing = os.path.join(two_folders["destination_dir"], "alice.safetensors")
    with open(existing, "wb") as handle:
        handle.write(b"somebody else's adapter")

    mover = ModelMover(two_folders["hub"])
    with pytest.raises(MoveRefused, match="already exists"):
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )

    with open(existing, "rb") as handle:
        assert handle.read() == b"somebody else's adapter"
    assert os.path.exists(two_folders["source_path"])


def test_two_files_that_would_collide_are_refused_before_either_moves(hub, tmp_path):
    """Filenames are flattened to the basename, so two copies in different
    subdirectories can land on one name. Refused as a batch: moving the first and
    failing the second would be a half-done operation with no undo."""
    source_dir = tmp_path / "loras"
    destination_dir = tmp_path / "archive"
    source_dir.mkdir()
    destination_dir.mkdir()
    source_id = register_folder(hub, source_dir)
    destination_id = register_folder(hub, destination_dir)
    register_file(hub, source_id, source_dir, os.path.join("v1", "alice.safetensors"))
    register_file(hub, source_id, source_dir, os.path.join("v2", "alice.safetensors"))

    mover = ModelMover(hub)
    with pytest.raises(MoveRefused, match="would both land on"):
        mover.plan(
            [
                (source_id, os.path.join("v1", "alice.safetensors")),
                (source_id, os.path.join("v2", "alice.safetensors")),
            ],
            destination_id,
        )
    assert os.listdir(destination_dir) == []


def test_a_source_folder_is_never_written_into(hub, tmp_path):
    """``kind='source'`` is an ai-toolkit output root: taken from, never
    catalogued in place, and never a destination."""
    source_dir = tmp_path / "loras"
    output_root = tmp_path / "output"
    source_dir.mkdir()
    output_root.mkdir()
    source_id = register_folder(hub, source_dir)
    output_id = register_folder(hub, output_root, kind="source")
    register_file(hub, source_id, source_dir, "alice.safetensors")

    with pytest.raises(MoveRefused, match="taken from"):
        ModelMover(hub).plan([(source_id, "alice.safetensors")], output_id)


def test_a_file_already_in_the_destination_is_nothing_to_do(two_folders):
    """A mixed selection dropped onto a folder must do the obvious thing rather
    than error on the files that are already there."""
    plan = ModelMover(two_folders["hub"]).plan(
        [(two_folders["source_id"], "alice.safetensors")], two_folders["source_id"]
    )
    assert plan.moves == []


# ===========================================================================
# Cancel
# ===========================================================================


def test_cancel_stops_the_queue_and_rolls_nothing_back(hub, tmp_path, cross_device):
    """The ruling. The files already moved stay moved — there is no undo, and a
    rollback would need its own crash-window argument."""
    source_dir = tmp_path / "loras"
    destination_dir = tmp_path / "archive"
    source_dir.mkdir()
    destination_dir.mkdir()
    source_id = register_folder(hub, source_dir)
    destination_id = register_folder(hub, destination_dir)
    for name in ("a.safetensors", "b.safetensors", "c.safetensors"):
        register_file(hub, source_id, source_dir, name)

    cancelled_after = []

    def should_cancel():
        return bool(cancelled_after)

    def on_progress(outcome):
        cancelled_after.append(outcome)

    mover = ModelMover(hub)
    report = mover.execute(
        mover.plan(
            [
                (source_id, name)
                for name in ("a.safetensors", "b.safetensors", "c.safetensors")
            ],
            destination_id,
        ),
        should_cancel=should_cancel,
        on_progress=on_progress,
    )

    assert report.cancelled is True
    assert [outcome.status for outcome in report.outcomes] == [
        STATUS_MOVED,
        STATUS_CANCELLED,
        STATUS_CANCELLED,
    ]
    # Not rolled back: the first file is where the shelf now says it is.
    assert os.path.exists(destination_dir / "a.safetensors")
    assert not os.path.exists(source_dir / "a.safetensors")
    assert os.path.exists(source_dir / "b.safetensors")
    assert set(locations(hub)) == {
        (destination_id, "a.safetensors"),
        (source_id, "b.safetensors"),
        (source_id, "c.safetensors"),
    }
    assert_no_dangling_rows(hub)
