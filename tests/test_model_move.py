"""The move invariant (shelf plan B7), and what a crash in the middle of it leaves.

**copy → verify by SHA-256 → repoint the row and commit → then unlink.** Every
other order has a window whose residue is a ``model_file`` row naming a path
where no file is, and the shelf's whole tombstone design rests on a row that
names a file always naming a file that exists.

The two crash-window tests here are the acceptance bar (plan §6 item 3). They do
not assert the happy path and then claim a guarantee: each one **interrupts** the
mover inside a specific window and then reads the disk and the hub to see what
survived. The interruption is raised as a ``BaseException`` subclass on purpose -
``except Exception`` handlers do not catch it and no cleanup runs, so what the
test observes is what a killed process would have left, not what an error handler
tidied up.

Both windows must leave a **duplicate** - the file readable at both paths, and the
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
from pixlstash.services import model_dir_mover as dir_mover_module
from pixlstash.services import model_mover as mover_module
from pixlstash.services.model_dir_mover import (
    execute_directory_move,
    plan_directory_move,
)
from pixlstash.services.model_mover import (
    PARTIAL_SUFFIX,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_MOVED,
    STATUS_SKIPPED,
    ModelMover,
    MoveRefused,
)


class Crash(BaseException):
    """A process death, as far as the code under test can tell.

    ``BaseException`` rather than ``Exception`` deliberately: the mover catches
    ``OSError`` to report a failed file and would otherwise turn the simulated
    crash into a tidy, cleaned-up error - which is precisely the state a real
    crash does *not* leave behind.
    """


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
    """``{(folder_id, relpath): row}`` - every registered copy."""
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
    and the copy/verify/commit/unlink invariant - the thing under test - would
    never execute.
    """
    monkeypatch.setattr(mover_module, "same_device", lambda *_: False)


# ===========================================================================
# The two crash windows - the acceptance bar (plan §6 item 3)
# ===========================================================================


def test_crash_after_the_copy_and_before_the_commit_leaves_a_duplicate(
    two_folders, cross_device, monkeypatch
):
    """Window 1. The bytes are at both paths; the row still names the source.

    Nothing has been unlinked, because the unlink is last. The residue is a
    duplicate - one content row, one location row, and a second file on disk
    that the next scan of the destination folder will register as a second
    location of the same model, which is exactly what a duplicate is here.
    That scan may be ten minutes off (a watch can miss it), so the residue has
    to be serviceable on its own and not merely repairable.
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
        "the source was unlinked before the commit - the invariant is inverted"
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
    Proved by the inode surviving - a copy would allocate a new one - and by no
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
    destination re-read - which is the part that actually proves the copy."""
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
# The samples follow the file
# ===========================================================================
#
# A trained checkpoint's previews live beside it in ``<stem>_samples/``, put
# there by the ai-toolkit import. A move that left them behind would strand them
# in a folder the model no longer sits in, which is how they get deleted next.


def _with_samples(two_folders, *names):
    """Give the fixture's adapter a samples directory, as an import would."""
    samples = two_folders["source_dir"] / "alice_samples"
    samples.mkdir()
    for name in names:
        (samples / name).write_bytes(name.encode())
    return samples


@pytest.mark.parametrize("force_copy", [False, True], ids=["rename", "copy"])
def test_a_move_carries_the_samples_directory(two_folders, monkeypatch, force_copy):
    """Both paths, because both are real: same-drive is a rename and cross-drive
    is the copy/verify/commit/unlink invariant, and the samples ride inside each
    of them rather than being handled by one."""
    _with_samples(two_folders, "1712345678901__000000500_0.jpg")
    if force_copy:
        monkeypatch.setattr(mover_module, "same_device", lambda *_: False)

    mover = ModelMover(two_folders["hub"])
    report = mover.execute(
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_MOVED]
    assert report.outcomes[0].detail is None
    moved = two_folders["destination_dir"] / "alice_samples"
    assert os.listdir(moved) == ["1712345678901__000000500_0.jpg"]
    assert not (two_folders["source_dir"] / "alice_samples").exists()
    assert not (moved.parent / ("alice_samples" + PARTIAL_SUFFIX)).exists()


def test_the_samples_are_counted_into_the_space_check(two_folders, monkeypatch):
    """15 MB per run against a 1.9 GB copy: small, and not nothing when the
    destination is nearly full. Only on the copy path - a rename copies nothing,
    samples included."""
    _with_samples(two_folders, "a.jpg", "b.jpg")
    weights = os.path.getsize(two_folders["source_path"])
    previews = sum(
        os.path.getsize(two_folders["source_dir"] / "alice_samples" / name)
        for name in ("a.jpg", "b.jpg")
    )

    mover = ModelMover(two_folders["hub"])
    items = [(two_folders["source_id"], "alice.safetensors")]
    assert mover.plan(items, two_folders["destination_id"]).bytes_to_copy == 0

    monkeypatch.setattr(mover_module, "same_device", lambda *_: False)
    assert (
        mover.plan(items, two_folders["destination_id"]).bytes_to_copy
        == weights + previews
    )


@pytest.mark.parametrize("force_copy", [False, True], ids=["rename", "copy"])
def test_a_failed_samples_move_still_leaves_the_model_moved(
    two_folders, monkeypatch, force_copy
):
    """Non-fatal, and asserted rather than assumed: losing a preview must not
    cost the weights. The destination directory is occupied by something the
    move must not write over, so the carry genuinely fails."""
    _with_samples(two_folders, "1712345678901__000000500_0.jpg")
    squatter = two_folders["destination_dir"] / "alice_samples"
    squatter.mkdir()
    (squatter / "mine.jpg").write_bytes(b"somebody else's file")
    if force_copy:
        monkeypatch.setattr(mover_module, "same_device", lambda *_: False)

    mover = ModelMover(two_folders["hub"])
    report = mover.execute(
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_MOVED]
    assert "not carried" in (report.outcomes[0].detail or "")
    # The file moved and its row committed; only the previews stayed behind.
    assert os.path.exists(two_folders["destination_path"])
    assert not os.path.exists(two_folders["source_path"])
    assert os.listdir(squatter) == ["mine.jpg"]
    assert_no_dangling_rows(two_folders["hub"])


@pytest.mark.parametrize("force_copy", [False, True], ids=["rename", "copy"])
def test_an_empty_samples_directory_at_the_destination_is_not_replaced(
    two_folders, monkeypatch, force_copy
):
    """The one case where the two platforms disagreed, pinned on both.

    ``os.rename`` over an **empty** existing directory silently replaces it on
    POSIX and raises ``FileExistsError`` on Windows, where four CI shards run.
    The non-empty squatter above therefore proves nothing about it: it fails
    everywhere. Carrying the samples checks the name first, so the owner's
    directory survives on every platform and the move still lands.
    """
    _with_samples(two_folders, "1712345678901__000000500_0.jpg")
    (two_folders["destination_dir"] / "alice_samples").mkdir()
    if force_copy:
        monkeypatch.setattr(mover_module, "same_device", lambda *_: False)

    mover = ModelMover(two_folders["hub"])
    report = mover.execute(
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_MOVED]
    assert "already exists" in (report.outcomes[0].detail or "")
    assert os.listdir(two_folders["destination_dir"] / "alice_samples") == []
    assert os.listdir(two_folders["source_dir"] / "alice_samples") == [
        "1712345678901__000000500_0.jpg"
    ], "the previews were moved into, or over, a directory already at that name"
    assert os.path.exists(two_folders["destination_path"])


def test_previews_that_arrived_are_not_reported_as_lost(two_folders, monkeypatch):
    """The source removal is the LAST step, so it can fail with the previews
    already at the destination.

    Reporting that as "not carried" sends the owner hunting for previews that
    are exactly where they should be - and their obvious next move, re-running
    the move, hits the destination-exists refusal above. What is actually left
    is a duplicate at the source, which is the residue every other interruption
    in this module leaves and which belongs in the log, not the receipt.
    """
    _with_samples(two_folders, "1712345678901__000000500_0.jpg")
    monkeypatch.setattr(mover_module, "same_device", lambda *_: False)

    real_rmtree = mover_module.shutil.rmtree

    def fail_on_the_source(path, *args, **kwargs):
        if str(path) == str(two_folders["source_dir"] / "alice_samples"):
            raise OSError("the source directory is locked")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(mover_module.shutil, "rmtree", fail_on_the_source)

    mover = ModelMover(two_folders["hub"])
    report = mover.execute(
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_MOVED]
    assert report.outcomes[0].detail is None, (
        "previews that arrived were reported as lost; the receipt counts this "
        "as a file that moved without its training previews"
    )
    assert os.listdir(two_folders["destination_dir"] / "alice_samples") == [
        "1712345678901__000000500_0.jpg"
    ]
    # The duplicate the failed removal left. Logged, not reported.
    assert os.path.isdir(two_folders["source_dir"] / "alice_samples")


def test_a_model_with_no_samples_directory_moves_as_before(two_folders):
    """The common case, pinned: nothing is created beside a model that never had
    previews."""
    mover = ModelMover(two_folders["hub"])
    mover.execute(
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    )
    assert os.listdir(two_folders["destination_dir"]) == ["alice.safetensors"]


# ===========================================================================
# Refused before the first byte
# ===========================================================================


def test_space_is_checked_before_anything_is_written(
    two_folders, cross_device, monkeypatch
):
    """Per-file checking would fill the disk and fail on file 1,500 of 1,806,
    having already moved 1,499 - and there is no undo for shelf operations."""
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

    ``plan`` runs inside the POST and the write runs minutes later on the worker
    thread. Before the execution-time re-check this destroyed the file that
    arrived in between and reported ``moved``. Reproduced deterministically -
    write the destination between ``plan`` and ``execute`` - rather than by
    threading, because the window is the whole gap and needs no timing to enter.

    Publication refuses this too (the test below), so the detail is asserted as
    well as the status: it is the re-check's message, and it is what keeps this
    a refusal naming the file rather than a failure on the worker thread.
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
    assert "already exists in the destination folder" in (
        report.outcomes[0].detail or ""
    ), "the refusal did not come from the execution-time re-check"
    with open(two_folders["destination_path"], "rb") as handle:
        assert handle.read() == arrived, "the move overwrote a file it never named"
    assert os.path.exists(two_folders["source_path"])
    assert set(locations(two_folders["hub"])) == {
        (two_folders["source_id"], "alice.safetensors")
    }
    assert_no_dangling_rows(two_folders["hub"])


@pytest.mark.parametrize("force_copy", [False, True], ids=["rename", "copy"])
def test_a_destination_created_at_publication_time_is_refused_not_clobbered(
    two_folders, monkeypatch, force_copy
):
    """The window the execution-time check cannot close (#1012).

    The check above runs *before* a copy and digest that takes minutes on a real
    checkpoint; publication happens after it. A file written into the destination
    name inside that gap was silently replaced by ``os.replace`` (copy path) or
    ``os.rename`` (rename path), and the move reported ``moved`` and removed the
    source. Only the publication itself can refuse it, which is what
    ``publish_no_clobber`` does.

    Entered deterministically at the instant that matters - the destination is
    created from inside the publication call, after the copy has been written and
    verified - rather than by threading. That barrier proves the refusal happens
    at publication rather than at the check minutes earlier; it cannot prove the
    claim is one syscall, because nothing in-process can be scheduled between a
    syscall's entry and its return. The two tests below cover what a claim that
    is not one syscall would leave behind instead.
    """
    if force_copy:
        monkeypatch.setattr(mover_module, "same_device", lambda *_: False)
    original_publish = mover_module.publish_no_clobber
    arrived = b"written by a trainer while the move was copying"

    def racing_publish(temporary_path, destination_path):
        with open(destination_path, "wb") as handle:
            handle.write(arrived)
        return original_publish(temporary_path, destination_path)

    monkeypatch.setattr(mover_module, "publish_no_clobber", racing_publish)
    with open(two_folders["source_path"], "rb") as handle:
        source_bytes = handle.read()

    mover = ModelMover(two_folders["hub"])
    report = mover.execute(
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_FAILED]
    with open(two_folders["destination_path"], "rb") as handle:
        assert handle.read() == arrived, "the move overwrote a file it never named"
    with open(two_folders["source_path"], "rb") as handle:
        assert handle.read() == source_bytes, "the source did not survive the refusal"
    assert not os.path.exists(two_folders["destination_path"] + PARTIAL_SUFFIX)
    # No hub row moved, so no receipt claiming this file is in the destination.
    assert set(locations(two_folders["hub"])) == {
        (two_folders["source_id"], "alice.safetensors")
    }
    assert_no_dangling_rows(two_folders["hub"])


@pytest.mark.parametrize("force_copy", [False, True], ids=["rename", "copy"])
def test_a_publication_that_cannot_drop_its_source_leaves_the_name_free(
    two_folders, monkeypatch, force_copy
):
    """The half-state the old single ``os.rename`` could not produce.

    Publication is a claim and then a drop, and on Windows the drop is what
    fails: a file another process holds open cannot be unlinked, and that is
    exactly what ComfyUI does with a loaded model. Leaving the claim behind would
    put an unregistered copy at the destination *and* make that name refuse every
    later move of the same model - a move that fails and then cannot be retried.
    So the claim is removed and the failure reported.
    """
    if force_copy:
        monkeypatch.setattr(mover_module, "same_device", lambda *_: False)

    # Only the *drop* fails - the first unlink of the run. The rollback's own
    # unlink has to work, which is the whole thing under test, and patching
    # ``os.unlink`` wholesale would break it and pass for the wrong reason.
    real_unlink = os.unlink
    dropped = []

    def refuse_the_first_unlink(path):
        dropped.append(path)
        if len(dropped) == 1:
            raise PermissionError(f"{path} is held open by another process")
        real_unlink(path)

    monkeypatch.setattr(mover_module.os, "unlink", refuse_the_first_unlink)
    with open(two_folders["source_path"], "rb") as handle:
        source_bytes = handle.read()

    mover = ModelMover(two_folders["hub"])
    report = mover.execute(
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_FAILED]
    with open(two_folders["source_path"], "rb") as handle:
        assert handle.read() == source_bytes
    assert not os.path.exists(two_folders["destination_path"]), (
        "the destination name is still claimed, so the move can never be retried"
    )
    assert set(locations(two_folders["hub"])) == {
        (two_folders["source_id"], "alice.safetensors")
    }
    assert_no_dangling_rows(two_folders["hub"])


@pytest.mark.parametrize("force_copy", [False, True], ids=["rename", "copy"])
def test_a_move_still_lands_where_hard_links_are_refused(
    two_folders, monkeypatch, force_copy
):
    """exFAT, a share, or ``fs.protected_hardlinks`` over a file of another uid.

    ``os.rename`` never needed a link - only write on the two directories - so
    refusing to move at all would be this fix breaking folders that worked. The
    reservation is the second attempt and is no-clobber for the same reason:
    ``O_CREAT|O_EXCL`` either creates the name or raises.
    """
    if force_copy:
        monkeypatch.setattr(mover_module, "same_device", lambda *_: False)

    def no_hard_links(*args, **kwargs):
        raise PermissionError("the destination filesystem has no hard links")

    monkeypatch.setattr(mover_module.os, "link", no_hard_links)
    with open(two_folders["source_path"], "rb") as handle:
        source_bytes = handle.read()

    mover = ModelMover(two_folders["hub"])
    report = mover.execute(
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_MOVED]
    with open(two_folders["destination_path"], "rb") as handle:
        assert handle.read() == source_bytes
    assert not os.path.exists(two_folders["source_path"])
    assert set(locations(two_folders["hub"])) == {
        (two_folders["destination_id"], "alice.safetensors")
    }
    assert_no_dangling_rows(two_folders["hub"])


def test_the_reservation_refuses_a_taken_name_too(two_folders, monkeypatch):
    """The fallback is only worth having if it is no-clobber as well.

    Without this, a filesystem that cannot hard-link would quietly get the
    check-then-replace behaviour #1012 is about.
    """

    def no_hard_links(*args, **kwargs):
        raise PermissionError("the destination filesystem has no hard links")

    monkeypatch.setattr(mover_module.os, "link", no_hard_links)
    arrived = b"already at the destination name"
    with open(two_folders["destination_path"], "wb") as handle:
        handle.write(arrived)
    partial = two_folders["destination_path"] + PARTIAL_SUFFIX
    with open(partial, "wb") as handle:
        handle.write(b"the finished copy, waiting to be published")

    with pytest.raises(FileExistsError, match="was created while PixlStash"):
        mover_module.publish_no_clobber(partial, two_folders["destination_path"])

    with open(two_folders["destination_path"], "rb") as handle:
        assert handle.read() == arrived
    assert os.path.exists(partial), "the caller still owns its own copy"


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
    source was never unlinked and - on the rename path - never put back either,
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
    # The racing row is left for the rescan that owns it - deleting somebody
    # else's bookkeeping from inside a failed move is how a tombstone goes
    # missing - so ``assert_no_dangling_rows`` is deliberately not used here.
    assert (two_folders["destination_id"], "alice.safetensors") in locations(hub)


@pytest.mark.parametrize("force_copy", [False, True], ids=["rename", "copy"])
def test_forgetting_the_source_folder_mid_move_never_unlinks_the_source(
    two_folders, hub, monkeypatch, force_copy
):
    """Forget lands *inside* the move: the commit must refuse, not report zero rows.

    ``DELETE /model-folders/{id}`` drops the folder and every ``model_file`` row
    in it. If that lands after the copy and before ``_repoint``, the UPDATE
    matches nothing - and SQL calls that success, so the mover used to unlink the
    source and report ``moved`` with the destination bytes registered nowhere at
    all (#1017). That is the dangling residue inverted: a file no row names,
    after the only other copy was deleted.

    Simulated by running the forget from inside ``_repoint``, which is the exact
    instant it has to land on. The route cannot reach here any more - it takes
    ``SHELF_IO_LOCK`` - so this pins the mover's own half of the guard, which is
    what has to hold for a delete, a restored hub or a bug that gets there by
    another door.
    """
    if force_copy:
        monkeypatch.setattr(mover_module, "same_device", lambda *_: False)
    original_repoint = ModelMover._repoint

    def forget_then_repoint(self, move, plan, **kwargs):
        with hub.transaction() as conn:
            conn.execute(
                "DELETE FROM model_file WHERE model_folder_id = ?",
                (two_folders["source_id"],),
            )
            conn.execute(
                "DELETE FROM model_folder WHERE id = ?", (two_folders["source_id"],)
            )
        return original_repoint(self, move, plan, **kwargs)

    monkeypatch.setattr(ModelMover, "_repoint", forget_then_repoint)
    mover = ModelMover(hub)
    report = mover.execute(
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_FAILED], (
        "a move whose registration vanished reported success"
    )
    # The bytes are where the forget left every other file in that folder: on
    # disk at the source path, unregistered. Renamed back on the rename path,
    # the copy discarded on the copy path - the undo the ordering already had.
    assert os.path.exists(two_folders["source_path"]), (
        "the source was unlinked on the strength of a commit that moved no row"
    )
    assert not os.path.exists(two_folders["destination_path"])
    assert not os.path.exists(two_folders["destination_path"] + PARTIAL_SUFFIX)
    assert locations(hub) == {}, "the forget's own deletion did not stand"
    assert_no_dangling_rows(hub)


@pytest.mark.parametrize("force_copy", [False, True], ids=["rename", "copy"])
def test_a_duplicate_fold_during_the_move_does_not_fail_it(
    two_folders, hub, monkeypatch, force_copy
):
    """The over-blocking direction of the #1017 guard, and it is the live one.

    ``CheckpointHashTask`` folds two rows that hash the same by rewriting
    ``model_file.model_id`` to the survivor, on the task runner and deliberately
    outside ``SHELF_IO_LOCK``, so it overlaps a multi-minute copy freely. The row
    is still there and still names this file - only its model was consolidated -
    so the move must commit.

    An earlier revision of the guard matched on ``model_id`` as well as the
    source key, which turned every one of these into a failed move with a
    finished copy thrown away. The key is ``PRIMARY KEY (model_folder_id,
    relpath)``: it already matches at most one row, so the extra predicate could
    only ever miss.
    """
    if force_copy:
        monkeypatch.setattr(mover_module, "same_device", lambda *_: False)
    original_repoint = ModelMover._repoint

    def fold_then_repoint(self, move, plan, **kwargs):
        with hub.transaction() as conn:
            survivor = int(
                conn.execute(
                    "INSERT INTO model (file_kind, kind, sha256, filename, "
                    "provenance, file_size, created_at) VALUES ('adapter', "
                    "'lora', ?, 'alice.safetensors', 'external', 9, "
                    "'2026-08-09T00:00:00+00:00')",
                    ("d" * 64,),
                ).lastrowid
            )
            conn.execute(
                "UPDATE model_file SET model_id = ? WHERE model_id = ?",
                (survivor, two_folders["model_id"]),
            )
        return original_repoint(self, move, plan, **kwargs)

    monkeypatch.setattr(ModelMover, "_repoint", fold_then_repoint)
    mover = ModelMover(hub)
    report = mover.execute(
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_MOVED], (
        "a legitimate move was failed because another writer consolidated the "
        "row's model_id"
    )
    assert not os.path.exists(two_folders["source_path"])
    assert os.path.exists(two_folders["destination_path"])
    assert set(locations(hub)) == {(two_folders["destination_id"], "alice.safetensors")}
    assert_no_dangling_rows(hub)


def test_forgetting_the_source_folder_before_the_move_refuses_at_plan_time(
    two_folders, hub, cross_device
):
    """The other ordering, and the one that was always safe.

    Forget first and there is no folder to plan from, so the batch is refused
    before the first byte rather than half-executed. Pinned because the fix for
    the interleaved ordering must not be read as the only thing standing between
    a forgotten folder and a lost file.
    """
    with hub.transaction() as conn:
        conn.execute(
            "DELETE FROM model_file WHERE model_folder_id = ?",
            (two_folders["source_id"],),
        )
        conn.execute(
            "DELETE FROM model_folder WHERE id = ?", (two_folders["source_id"],)
        )

    with pytest.raises(MoveRefused):
        ModelMover(hub).plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    assert os.path.exists(two_folders["source_path"])
    assert not os.path.exists(two_folders["destination_path"])


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


def test_a_symlink_at_the_destination_name_is_refused_not_written_through(
    two_folders, cross_device
):
    """The destination containment is **reachable**, and this is the case that
    proves it.

    ``basename`` neutralises ``..``; it does nothing about a symlink standing at
    the destination filename, and ``resolve_path_within`` calls ``realpath``. A
    *dangling* link is the sharp case: ``os.path.exists`` is False for it, so the
    collision check waves it through, and without the containment the copy would
    ``os.replace`` straight through the link and write outside the registered
    folder.
    """
    outside = two_folders["destination_dir"].parent / "outside"
    outside.mkdir()
    os.symlink(
        str(outside / "alice.safetensors"),
        os.path.join(two_folders["destination_dir"], "alice.safetensors"),
    )

    mover = ModelMover(two_folders["hub"])
    with pytest.raises(MoveRefused, match="outside the destination folder"):
        mover.plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )

    assert not (outside / "alice.safetensors").exists(), "written outside the folder"
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


@pytest.mark.parametrize("same_model", [False, True], ids=["other-model", "same-model"])
def test_a_registered_destination_key_with_no_file_is_refused_at_plan_time(
    two_folders, hub, same_model
):
    """A tombstone at the destination key refuses the batch before the first byte.

    The `same-model` case is the one that was wrong: the check used to allow an
    existing row whose ``model_id`` matched the file being moved, on the reading
    that a row about the same content is harmless. ``model_file`` is keyed by
    ``(model_folder_id, relpath)``, so it is not - ``_repoint``'s UPDATE walks
    into ``UNIQUE`` at commit time, minutes in, instead of a clean 4xx before
    anything is written. Widened to refuse any existing row, and pinned here in
    both shapes because nothing else exercises this branch: deleting it left the
    whole suite green.
    """
    model_id = two_folders["model_id"]
    with hub.transaction() as conn:
        if not same_model:
            model_id = int(
                conn.execute(
                    "INSERT INTO model (file_kind, kind, sha256, filename, "
                    "provenance, file_size, created_at) VALUES ('adapter', "
                    "'lora', ?, 'alice.safetensors', 'external', 9, "
                    "'2026-08-09T00:00:00+00:00')",
                    ("c" * 64,),
                ).lastrowid
            )
        # No file on disk: the row is a tombstone, so the ``os.path.exists``
        # branch above it cannot be what refuses this.
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state, "
            "seen_at) VALUES (?, ?, 'alice.safetensors', 'missing', "
            "'2026-08-09T00:00:00+00:00')",
            (model_id, two_folders["destination_id"]),
        )

    with pytest.raises(MoveRefused, match="is registered to model"):
        ModelMover(hub).plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    assert os.path.exists(two_folders["source_path"])
    assert not os.path.exists(two_folders["destination_path"])


def test_a_file_already_in_the_destination_is_reported_skipped_not_dropped(
    two_folders,
):
    """A mixed selection dropped onto a folder must do the obvious thing rather
    than error on the files that are already there - **and say so**.

    Dropping them from the plan silently made ``STATUS_SKIPPED`` dead code and
    left a client unable to reconcile the items it sent against the results it
    got: three items in, two results out, no way to tell which one went missing
    or why. ``total`` counts them too, so ``done == total`` still means finished.
    """
    mover = ModelMover(two_folders["hub"])
    plan = mover.plan(
        [(two_folders["source_id"], "alice.safetensors")], two_folders["source_id"]
    )
    assert plan.moves == []
    assert plan.total == 1, "an item the caller sent vanished from the tally"

    seen = []
    report = mover.execute(plan, on_progress=seen.append)
    assert [
        (o.source_folder_id, o.source_relpath, o.status) for o in report.outcomes
    ] == [(two_folders["source_id"], "alice.safetensors", STATUS_SKIPPED)]
    assert seen == report.outcomes, "a skipped item never reached the progress hook"
    assert report.outcomes[0].detail


def test_a_file_at_the_root_moves_into_a_subfolder_of_its_own_folder(hub, tmp_path):
    """The case #2204 was filed for: LoRAs at the root of a StabilityMatrix
    ``Models`` folder dropped onto ``Models/Lora``. One registered folder, so the
    old "already there" test - folder ids alone - skipped them and nothing moved.

    A rename, because it is one filesystem, and the row keeps its model: the
    sets and people hung off that model come along by construction.
    """
    models = tmp_path / "Models"
    (models / "Lora").mkdir(parents=True)
    folder_id = register_folder(hub, models)
    model_id, source_path = register_file(hub, folder_id, models, "alice.safetensors")

    mover = ModelMover(hub)
    plan = mover.plan(
        [(folder_id, "alice.safetensors")], folder_id, destination_subpath="Lora"
    )
    assert plan.skipped == [], "a root file was taken as already in Lora"
    assert plan.destination_subpath == "Lora"
    assert [move.same_device for move in plan.moves] == [True]

    report = mover.execute(plan)

    assert [outcome.status for outcome in report.outcomes] == [STATUS_MOVED]
    assert not os.path.exists(source_path)
    assert (models / "Lora" / "alice.safetensors").exists()
    relpath = os.path.join("Lora", "alice.safetensors")
    assert set(locations(hub)) == {(folder_id, relpath)}
    assert locations(hub)[(folder_id, relpath)]["model_id"] == model_id
    assert_no_dangling_rows(hub)


def test_a_file_already_in_the_target_subfolder_is_skipped(hub, tmp_path):
    """The skip rule's other half: a file directly in ``Lora`` dropped onto
    ``Lora`` is nothing to do, and one in ``Lora/old`` is not already there."""
    models = tmp_path / "Models"
    (models / "Lora" / "old").mkdir(parents=True)
    folder_id = register_folder(hub, models)
    here = os.path.join("Lora", "here.safetensors")
    deeper = os.path.join("Lora", "old", "deeper.safetensors")
    register_file(hub, folder_id, models, here)
    register_file(hub, folder_id, models, deeper)

    plan = ModelMover(hub).plan(
        [(folder_id, here), (folder_id, deeper)],
        folder_id,
        destination_subpath="Lora",
    )

    assert [(o.source_relpath, o.status) for o in plan.skipped] == [
        (here, STATUS_SKIPPED)
    ]
    assert [move.destination_relpath for move in plan.moves] == [
        os.path.join("Lora", "deeper.safetensors")
    ]


def test_a_cross_device_move_into_a_subfolder_copies_there(
    two_folders, cross_device, monkeypatch
):
    """The copy path lands in the subfolder too, and the space check is made
    against the directory the bytes go to."""
    hub = two_folders["hub"]
    (two_folders["destination_dir"] / "Lora").mkdir()
    checked = []
    real_require_space = mover_module.require_space

    def spy(path, needed):
        checked.append((path, needed))
        return real_require_space(path, needed)

    monkeypatch.setattr(mover_module, "require_space", spy)
    mover = ModelMover(hub)
    plan = mover.plan(
        [(two_folders["source_id"], "alice.safetensors")],
        two_folders["destination_id"],
        destination_subpath="Lora",
    )
    landing = os.path.realpath(two_folders["destination_dir"] / "Lora")
    assert checked == [(landing, plan.bytes_to_copy)]
    assert plan.bytes_to_copy > 0

    report = mover.execute(plan)

    assert [outcome.status for outcome in report.outcomes] == [STATUS_MOVED]
    relpath = os.path.join("Lora", "alice.safetensors")
    assert (two_folders["destination_dir"] / relpath).exists()
    assert set(locations(hub)) == {(two_folders["destination_id"], relpath)}
    assert_no_dangling_rows(hub)


def test_two_files_landing_on_one_name_in_a_subfolder_are_refused(hub, tmp_path):
    """The duplicate-destination check holds with a subpath: ``v1/a`` and
    ``v2/a`` both flatten to ``Lora/a``."""
    models = tmp_path / "Models"
    (models / "Lora").mkdir(parents=True)
    folder_id = register_folder(hub, models)
    first = os.path.join("v1", "alice.safetensors")
    second = os.path.join("v2", "alice.safetensors")
    register_file(hub, folder_id, models, first)
    register_file(hub, folder_id, models, second)

    with pytest.raises(MoveRefused, match="would both land on"):
        ModelMover(hub).plan(
            [(folder_id, first), (folder_id, second)],
            folder_id,
            destination_subpath="Lora",
        )


def test_an_existing_file_in_the_subfolder_is_never_overwritten(two_folders):
    """The free check is made at the subfolder's name, not the root's."""
    lora = two_folders["destination_dir"] / "Lora"
    lora.mkdir()
    (lora / "alice.safetensors").write_bytes(b"theirs")

    with pytest.raises(MoveRefused, match="already exists"):
        ModelMover(two_folders["hub"]).plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
            destination_subpath="Lora",
        )
    assert (lora / "alice.safetensors").read_bytes() == b"theirs"


@pytest.mark.parametrize(
    "subpath",
    [
        "..",
        "../loras",
        "Lora/../..",
        "/etc",
        "Lora/",
        "./Lora",
        "Lora\\x",
        "out",
        "inner",
        ".hidden",
    ],
    ids=[
        "dotdot",
        "dotdot-sibling",
        "dotdot-nested",
        "absolute",
        "trailing",
        "dot",
        "backslash",
        "symlink-out",
        "symlink-inside",
        "hidden",
    ],
)
def test_a_subpath_that_leaves_the_folder_is_refused(two_folders, subpath):
    """Every escape is a 400 before anything is written. ``out`` is a symlink
    inside the destination pointing at the source folder: plain by name, and
    only ``resolve_path_within`` can see where it leads. ``inner`` links to
    ``Lora`` and ``.hidden`` is a dot-name: both stay inside, and both are
    refused because the listing never offers them and a rescan would not find
    a file moved through the link."""
    destination_dir = two_folders["destination_dir"]
    (destination_dir / "Lora").mkdir()
    (destination_dir / ".hidden").mkdir()
    os.symlink(two_folders["source_dir"], destination_dir / "out")
    os.symlink(destination_dir / "Lora", destination_dir / "inner")

    with pytest.raises(MoveRefused) as refused:
        ModelMover(two_folders["hub"]).plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
            destination_subpath=subpath,
        )
    assert refused.value.status_code == 400
    assert os.path.exists(two_folders["source_path"])
    assert sorted(os.listdir(destination_dir / "Lora")) == []


def test_a_subfolder_removed_after_the_plan_is_not_recreated(two_folders):
    """The plan saw ``Lora``; it is gone by the time the worker writes. The
    file fails and stays where it was, and no ``Lora`` appears."""
    lora = two_folders["destination_dir"] / "Lora"
    lora.mkdir()
    mover = ModelMover(two_folders["hub"])
    plan = mover.plan(
        [(two_folders["source_id"], "alice.safetensors")],
        two_folders["destination_id"],
        destination_subpath="Lora",
    )
    lora.rmdir()

    report = mover.execute(plan)

    assert [outcome.status for outcome in report.outcomes] == [STATUS_FAILED]
    assert not lora.exists()
    assert os.path.exists(two_folders["source_path"])
    assert_no_dangling_rows(two_folders["hub"])


def test_a_missing_subfolder_is_refused_not_created(two_folders):
    with pytest.raises(MoveRefused, match="Create it first") as refused:
        ModelMover(two_folders["hub"]).plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
            destination_subpath="Lora",
        )
    assert refused.value.status_code == 409
    assert not (two_folders["destination_dir"] / "Lora").exists()


@pytest.mark.parametrize("movable", ["root_only", "fixed"])
def test_a_locked_destination_is_refused(two_folders, movable):
    """The managed store, the HuggingFace cache and the InsightFace packs take
    no drop: files do not leave them one at a time, and none arrive either."""
    hub = two_folders["hub"]
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE model_folder SET movable = ? WHERE id = ?",
            (movable, two_folders["destination_id"]),
        )

    with pytest.raises(MoveRefused, match="locked") as refused:
        ModelMover(hub).plan(
            [(two_folders["source_id"], "alice.safetensors")],
            two_folders["destination_id"],
        )
    assert refused.value.status_code == 409
    assert os.path.exists(two_folders["source_path"])


def test_a_relocation_cannot_target_a_subfolder(hub, tmp_path):
    store = tmp_path / "store"
    target = tmp_path / "elsewhere"
    store.mkdir()
    (target / "sub").mkdir(parents=True)
    store_id = register_folder(hub, store)
    target_id = register_folder(hub, target)
    register_file(hub, store_id, store, "model.safetensors")

    with pytest.raises(MoveRefused, match="relocation"):
        ModelMover(hub).plan(
            [(store_id, "model.safetensors")],
            target_id,
            destination_subpath="sub",
            flatten=False,
            relocating=True,
        )


def test_a_cancel_after_a_skipped_item_still_cancels_every_move(hub, tmp_path):
    """The skipped items lead the report, so the cancelled tail must be indexed
    off ``plan.moves`` and not off how many outcomes exist. Off by the number of
    skipped items, the last file would silently never be reported at all."""
    source_dir = tmp_path / "loras"
    destination_dir = tmp_path / "archive"
    source_dir.mkdir()
    destination_dir.mkdir()
    source_id = register_folder(hub, source_dir)
    destination_id = register_folder(hub, destination_dir)
    register_file(hub, destination_id, destination_dir, "already.safetensors")
    for name in ("one.safetensors", "two.safetensors"):
        register_file(hub, source_id, source_dir, name)

    mover = ModelMover(hub)
    plan = mover.plan(
        [
            (destination_id, "already.safetensors"),
            (source_id, "one.safetensors"),
            (source_id, "two.safetensors"),
        ],
        destination_id,
    )
    report = mover.execute(plan, should_cancel=lambda: True)

    assert report.cancelled
    assert [outcome.status for outcome in report.outcomes] == [
        STATUS_SKIPPED,
        STATUS_CANCELLED,
        STATUS_CANCELLED,
    ]
    assert len(report.outcomes) == plan.total


def test_relocating_a_folder_keeps_its_subdirectories(hub, tmp_path, cross_device):
    """``flatten=False``: a folder that moves as a unit moves as a tree.

    Flattened, ``runA/model.safetensors`` and ``runB/model.safetensors`` collide
    on one name, which refuses the relocation permanently - and the refusal's
    advice ("move them separately") names a verb the shelf does not have.
    """
    store = tmp_path / "store"
    target = tmp_path / "elsewhere"
    store.mkdir()
    target.mkdir()
    store_id = register_folder(hub, store)
    target_id = register_folder(hub, target)
    nested = [
        os.path.join("runA", "model.safetensors"),
        os.path.join("runB", "model.safetensors"),
    ]
    for relpath in nested:
        register_file(hub, store_id, store, relpath)

    mover = ModelMover(hub)
    # Flattened, this same batch is the permanent 400.
    with pytest.raises(MoveRefused, match="would both land on"):
        mover.plan([(store_id, relpath) for relpath in nested], target_id)

    report = mover.execute(
        mover.plan(
            [(store_id, relpath) for relpath in nested], target_id, flatten=False
        )
    )

    assert [outcome.status for outcome in report.outcomes] == [STATUS_MOVED] * 2
    for relpath in nested:
        assert os.path.exists(os.path.join(target, relpath))
        assert not os.path.exists(os.path.join(store, relpath))
    assert set(locations(hub)) == {(target_id, relpath) for relpath in nested}
    assert_no_dangling_rows(hub)


# ===========================================================================
# Cancel
# ===========================================================================


def test_cancel_stops_the_queue_and_rolls_nothing_back(hub, tmp_path, cross_device):
    """The ruling. The files already moved stay moved - there is no undo, and a
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


def test_a_root_only_folder_refuses_a_per_item_move(tmp_path):
    """The containment site for the HuggingFace cache.

    That cache is `blobs/` under content hashes with `snapshots/` symlinking
    names onto them, it is shared with every other HF tool on the machine, and a
    declared row's relpath there is a whole repo directory. Moving one out does
    not relocate a model, it breaks HuggingFace's bookkeeping for ComfyUI too.
    `root_only` is the column that already says "this relocates as a whole", so
    the refusal is keyed on it and covers PixlStash's own engines and the
    InsightFace packs by the same stroke.
    """
    from pixlstash.services.model_mover import ModelMover, MoveRefused

    source = tmp_path / "hf-cache"
    source.mkdir()
    (source / "models--org--thing").mkdir()
    destination = tmp_path / "loras"
    destination.mkdir()

    hub = HubDatabase(str(tmp_path / "hub.db"))
    try:
        with hub.transaction() as conn:
            conn.execute(
                "INSERT INTO model_folder (id, path, kind, owner, movable, "
                "created_at) VALUES (1, ?, 'foreign', 'pixlstash', 'root_only', "
                "'2026-08-12T00:00:00Z')",
                (str(source),),
            )
            conn.execute(
                "INSERT INTO model_folder (id, path, kind, movable, created_at) "
                "VALUES (2, ?, 'user', 'per_item', '2026-08-12T00:00:00Z')",
                (str(destination),),
            )
            cursor = conn.execute(
                "INSERT INTO model (file_kind, kind, display_name, filename, "
                "provenance, file_size, created_at) VALUES ('engine', 'model', "
                "'org/thing', 'models--org--thing', 'builtin', 10, "
                "'2026-08-12T00:00:00Z')"
            )
            conn.execute(
                "INSERT INTO model_file (model_id, model_folder_id, relpath, "
                "state, seen_at) VALUES (?, 1, 'models--org--thing', 'present', "
                "'2026-08-12T00:00:00Z')",
                (int(cursor.lastrowid),),
            )

        mover = ModelMover(hub)
        with pytest.raises(MoveRefused) as refused:
            mover.plan([(1, "models--org--thing")], 2)
        assert "not moved one at a time" in str(refused.value)

        # The positive control: over-blocking is its own regression, so a
        # per_item folder must still move out in the same environment.
        (destination / "a.safetensors").write_bytes(b"x" * 10)
        with hub.transaction() as conn:
            # `sha256` is not optional on an adapter: the hub's CHECK is what
            # makes a tombstone re-linkable by content.
            cursor = conn.execute(
                "INSERT INTO model (file_kind, kind, sha256, display_name, "
                "filename, provenance, file_size, created_at) VALUES "
                "('adapter', 'lora', ?, 'A', 'a.safetensors', 'external', 10, "
                "'2026-08-12T00:00:00Z')",
                ("b" * 64,),
            )
            conn.execute(
                "INSERT INTO model_file (model_id, model_folder_id, relpath, "
                "state, seen_at) VALUES (?, 2, 'a.safetensors', 'present', "
                "'2026-08-12T00:00:00Z')",
                (int(cursor.lastrowid),),
            )
        # Into a third, unlocked folder: a locked one takes no drop (#2204).
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        with hub.transaction() as conn:
            conn.execute(
                "INSERT INTO model_folder (id, path, kind, movable, created_at) "
                "VALUES (3, ?, 'user', 'per_item', '2026-08-12T00:00:00Z')",
                (str(elsewhere),),
            )
        plan = mover.plan([(2, "a.safetensors")], 3)
        assert len(plan.moves) == 1, "a per_item source was wrongly refused"
    finally:
        hub.close()


# ===========================================================================
# Moving and renaming a directory inside a model folder (#2206)
# ===========================================================================
#
# A directory moves whole: its models, their `<stem>_samples/` and the files the
# shelf does not list. Every row under it is repointed in one transaction, and a
# refusal touches nothing. The route half is in tests/test_model_shelf_api.py.


@pytest.fixture
def dir_tree(hub, tmp_path):
    """``loras/sdxl`` with one catalogued model, its samples and a note, and an
    empty ``archive`` folder, both registered."""
    source_dir = tmp_path / "loras"
    destination_dir = tmp_path / "archive"
    source_dir.mkdir()
    destination_dir.mkdir()
    source_id = register_folder(hub, source_dir)
    destination_id = register_folder(hub, destination_dir)
    relpath = os.path.join("sdxl", "alice.safetensors")
    model_id, _ = register_file(hub, source_id, source_dir, relpath)
    samples = source_dir / "sdxl" / "alice_samples"
    samples.mkdir()
    (samples / "step_100.png").write_bytes(b"png")
    (source_dir / "sdxl" / "notes.txt").write_text("trained on 40 pictures")
    return {
        "hub": hub,
        "source_dir": source_dir,
        "destination_dir": destination_dir,
        "source_id": source_id,
        "destination_id": destination_id,
        "model_id": model_id,
        "relpath": relpath,
    }


@pytest.fixture
def dir_cross_device(monkeypatch):
    """Force the directory mover's copy path; see ``cross_device``."""
    monkeypatch.setattr(dir_mover_module, "same_device", lambda *_: False)


def _move_sdxl(tree, **kwargs):
    plan = plan_directory_move(
        tree["hub"],
        tree["source_id"],
        "sdxl",
        tree["destination_id"],
        kwargs.pop("destination_subpath", ""),
        kwargs.pop("new_name", None),
    )
    return plan, execute_directory_move(tree["hub"], plan, **kwargs)


def _assert_moved_to(tree, folder_dir, folder_id, new_dir):
    moved = folder_dir / new_dir
    assert (moved / "alice.safetensors").is_file()
    assert (moved / "alice_samples" / "step_100.png").read_bytes() == b"png"
    assert (moved / "notes.txt").read_text() == "trained on 40 pictures"
    assert not (tree["source_dir"] / "sdxl").exists()
    assert set(locations(tree["hub"])) == {
        (folder_id, os.path.join(new_dir, "alice.safetensors"))
    }
    row = locations(tree["hub"])[
        (folder_id, os.path.join(new_dir, "alice.safetensors"))
    ]
    assert row["model_id"] == tree["model_id"], "the move lost the model's curation"
    assert not list(folder_dir.glob("*" + PARTIAL_SUFFIX))
    assert_no_dangling_rows(tree["hub"])


def test_a_same_drive_directory_move_renames_and_repoints_every_row(dir_tree):
    plan, outcome = _move_sdxl(dir_tree)
    assert plan.same_drive is True
    assert (plan.models, plan.files, plan.other_files) == (1, 3, 2)
    assert outcome.status == STATUS_MOVED, outcome.detail
    _assert_moved_to(
        dir_tree, dir_tree["destination_dir"], dir_tree["destination_id"], "sdxl"
    )


def test_a_cross_drive_directory_move_copies_verifies_and_removes(
    dir_tree, dir_cross_device
):
    plan, outcome = _move_sdxl(dir_tree)
    assert plan.same_drive is False
    assert plan.bytes_to_copy == plan.bytes > 0
    assert outcome.status == STATUS_MOVED, outcome.detail
    _assert_moved_to(
        dir_tree, dir_tree["destination_dir"], dir_tree["destination_id"], "sdxl"
    )


def test_renaming_a_directory_is_a_move_into_its_own_parent(dir_tree):
    plan = plan_directory_move(
        dir_tree["hub"],
        dir_tree["source_id"],
        "sdxl",
        dir_tree["source_id"],
        "",
        "sdxl-1.0",
    )
    outcome = execute_directory_move(dir_tree["hub"], plan)
    assert outcome.status == STATUS_MOVED, outcome.detail
    _assert_moved_to(
        dir_tree, dir_tree["source_dir"], dir_tree["source_id"], "sdxl-1.0"
    )


def test_a_taken_name_is_refused_and_nothing_is_merged(dir_tree):
    (dir_tree["destination_dir"] / "sdxl").mkdir()
    (dir_tree["destination_dir"] / "sdxl" / "theirs.txt").write_text("keep")
    before = locations(dir_tree["hub"])
    with pytest.raises(MoveRefused) as refused:
        _move_sdxl(dir_tree)
    assert refused.value.status_code == 409
    assert (dir_tree["source_dir"] / "sdxl" / "alice.safetensors").is_file()
    assert sorted(p.name for p in (dir_tree["destination_dir"] / "sdxl").iterdir()) == [
        "theirs.txt"
    ]
    assert locations(dir_tree["hub"]).keys() == before.keys()


def test_a_directory_cannot_move_into_itself_or_below(dir_tree):
    (dir_tree["source_dir"] / "sdxl" / "inner").mkdir()
    for into in ("sdxl", "sdxl/inner"):
        with pytest.raises(MoveRefused) as refused:
            plan_directory_move(
                dir_tree["hub"],
                dir_tree["source_id"],
                "sdxl",
                dir_tree["source_id"],
                into,
            )
        assert refused.value.status_code == 400, into
    assert (dir_tree["source_dir"] / "sdxl" / "alice.safetensors").is_file()


def test_the_registered_root_and_a_locked_folder_are_refused(dir_tree):
    hub = dir_tree["hub"]
    with pytest.raises(MoveRefused) as refused:
        plan_directory_move(
            hub, dir_tree["source_id"], "", dir_tree["destination_id"], ""
        )
    assert refused.value.status_code == 400
    assert "registered folder itself" in str(refused.value)
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE model_folder SET movable = 'root_only' WHERE id = ?",
            (dir_tree["destination_id"],),
        )
    with pytest.raises(MoveRefused) as refused:
        _move_sdxl(dir_tree)
    assert refused.value.status_code == 409


def test_a_directory_holding_a_registered_folder_is_refused(dir_tree):
    nested = dir_tree["source_dir"] / "sdxl" / "watched"
    nested.mkdir()
    register_folder(dir_tree["hub"], nested)
    with pytest.raises(MoveRefused) as refused:
        _move_sdxl(dir_tree)
    assert refused.value.status_code == 409
    assert nested.is_dir()


def test_a_cancelled_cross_drive_move_leaves_no_partial_and_repoints_nothing(
    dir_tree, dir_cross_device
):
    before = locations(dir_tree["hub"])
    _, outcome = _move_sdxl(dir_tree, should_cancel=lambda: True)
    assert outcome.status == STATUS_CANCELLED
    assert not list(dir_tree["destination_dir"].iterdir()), "the partial was left"
    assert (dir_tree["source_dir"] / "sdxl" / "alice.safetensors").is_file()
    assert locations(dir_tree["hub"]).keys() == before.keys()
    assert_no_dangling_rows(dir_tree["hub"])


def test_a_copy_that_does_not_verify_is_discarded_and_the_source_kept(
    dir_tree, dir_cross_device, monkeypatch
):
    monkeypatch.setattr(dir_mover_module, "file_digest", lambda path: "0" * 64)
    before = locations(dir_tree["hub"])
    _, outcome = _move_sdxl(dir_tree)
    assert outcome.status == STATUS_FAILED
    assert "registered as" in outcome.detail
    assert not list(dir_tree["destination_dir"].iterdir())
    assert (dir_tree["source_dir"] / "sdxl" / "alice.safetensors").is_file()
    assert locations(dir_tree["hub"]).keys() == before.keys()


def test_a_failed_repoint_renames_the_directory_back(dir_tree, monkeypatch):
    def lost(hub, plan):
        raise mover_module.RepointLost("simulated: the rows went away")

    monkeypatch.setattr(dir_mover_module, "_repoint", lost)
    _, outcome = _move_sdxl(dir_tree)
    assert outcome.status == STATUS_FAILED
    assert (dir_tree["source_dir"] / "sdxl" / "alice.safetensors").is_file()
    assert not (dir_tree["destination_dir"] / "sdxl").exists()
    assert_no_dangling_rows(dir_tree["hub"])


def test_a_row_added_after_the_plan_is_repointed_too(dir_tree):
    """The rows are re-read inside the repoint: a rescan is not under the lock."""
    plan = plan_directory_move(
        dir_tree["hub"], dir_tree["source_id"], "sdxl", dir_tree["destination_id"], ""
    )
    register_file(
        dir_tree["hub"],
        dir_tree["source_id"],
        dir_tree["source_dir"],
        os.path.join("sdxl", "late.safetensors"),
    )
    assert execute_directory_move(dir_tree["hub"], plan).status == STATUS_MOVED
    assert_no_dangling_rows(dir_tree["hub"])


def test_a_file_written_into_the_source_during_the_copy_keeps_the_source(
    dir_tree, dir_cross_device, monkeypatch
):
    """The verify only knows the catalogued files; the removal must not take a
    preview a running job saved into the source after the copy began."""
    verify = dir_mover_module._verify
    late = dir_tree["source_dir"] / "sdxl" / "alice_samples" / "step_200.png"

    def verify_then_a_job_writes(plan, copy_root):
        verify(plan, copy_root)
        late.write_bytes(b"written mid-move")

    monkeypatch.setattr(dir_mover_module, "_verify", verify_then_a_job_writes)
    _, outcome = _move_sdxl(dir_tree)
    assert outcome.status == STATUS_MOVED
    assert "original was kept" in outcome.detail
    assert late.read_bytes() == b"written mid-move"
    assert (dir_tree["destination_dir"] / "sdxl" / "alice.safetensors").is_file()
    assert_no_dangling_rows(dir_tree["hub"])


def test_a_file_written_after_the_final_check_is_left_not_deleted(
    dir_tree, dir_cross_device, monkeypatch
):
    """The source goes file by file from the list just compared, never by
    ``rmtree``: a file that lands after that list is in no copy, so it stays."""
    listing = dir_mover_module._tree_listing
    late = dir_tree["source_dir"] / "sdxl" / "alice_samples" / "step_300.png"

    def list_then_a_job_writes(path):
        found = listing(path)
        if path == str(dir_tree["source_dir"] / "sdxl") or path.endswith(
            os.path.join("loras", "sdxl")
        ):
            late.write_bytes(b"written after the check")
        return found

    monkeypatch.setattr(dir_mover_module, "_tree_listing", list_then_a_job_writes)
    _, outcome = _move_sdxl(dir_tree)
    assert outcome.status == STATUS_MOVED
    assert "left there" in outcome.detail
    assert late.read_bytes() == b"written after the check"
    assert not (dir_tree["source_dir"] / "sdxl" / "alice.safetensors").exists()
    assert (dir_tree["destination_dir"] / "sdxl" / "alice.safetensors").is_file()
    assert_no_dangling_rows(dir_tree["hub"])
