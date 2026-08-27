"""End-to-end cover for `pixlstash-cli libraries restore`.

Backs a library up and restores it, because the two halves only mean anything
together: the archive's ``images/`` prefix, the hub-versus-server-config
question and the preserved pair are all round-trip properties. A test that
asserted against a hand-built tar would be asserting against this file's idea of
a backup rather than against what ``backup`` writes.

The environment is module-scoped per the repository's fixture policy: a restore
is filesystem work, so the cost here is the vault and hub each case needs, not a
server. Every case gets its own config dir and destination inside one shared
temp root, which keeps them independent without rebuilding anything expensive.
"""

from __future__ import annotations

import errno
import io
import json
import os
import sqlite3
import stat
import tarfile
import tempfile

import pytest

from pixlstash.cli import main
from pixlstash.hub.db import HubDatabase
from pixlstash.hub.registry import VAULT_FILENAME, LibraryRegistry
from pixlstash.services.library_restore_service import SERVER_CONFIG_FILENAME

_VAULT_SCHEMA = (
    "CREATE TABLE picture (id INTEGER PRIMARY KEY, file_path TEXT)",
    "CREATE TABLE tag (id INTEGER PRIMARY KEY)",
    "CREATE TABLE alembic_version (version_num TEXT)",
    "CREATE TABLE library_settings (library_uuid TEXT)",
)


@pytest.fixture(scope="module")
def temp_root():
    """One temp root for the module; each case gets a subdirectory of it."""
    with tempfile.TemporaryDirectory(prefix="pixlstash_restore_tests_") as root:
        yield root


def _make_library(folder: str, *, pictures: int, revision: str) -> str:
    """Create a minimal but genuine library folder, returning its uuid."""
    os.makedirs(folder, exist_ok=True)
    os.chmod(folder, 0o700)
    conn = sqlite3.connect(os.path.join(folder, VAULT_FILENAME))
    try:
        for statement in _VAULT_SCHEMA:
            conn.execute(statement)
        library_uuid = f"uuid-for-{os.path.basename(folder)}"
        conn.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
        conn.execute("INSERT INTO library_settings VALUES (?)", (library_uuid,))
        for index in range(pictures):
            name = f"picture-{index}.jpg"
            with open(os.path.join(folder, name), "wb") as handle:
                handle.write(b"\xff\xd8\xff" + bytes([index]))
            conn.execute("INSERT INTO picture (file_path) VALUES (?)", (name,))
        conn.commit()
    finally:
        conn.close()
    return library_uuid


def _current_revision() -> str:
    """A revision the backup's validation will accept."""
    from pixlstash.services.library_switch_service import known_vault_revisions

    return sorted(known_vault_revisions())[0]


def _install(root: str, name: str, *, pictures: int = 3) -> dict:
    """Build a config dir with a hub and one registered, active library."""
    config_dir = os.path.join(root, name, "config")
    library_dir = os.path.join(root, name, "images")
    # 0700 on every level: HubDatabase refuses a group/world-writable directory
    # *or ancestor*, because another account could swap the database or its WAL
    # out from under it. makedirs applies its mode only to the leaf.
    os.makedirs(config_dir, exist_ok=True)
    os.chmod(os.path.join(root, name), 0o700)
    os.chmod(config_dir, 0o700)
    _make_library(library_dir, pictures=pictures, revision=_current_revision())

    hub_path = os.path.join(config_dir, "hub.db")
    hub = HubDatabase(hub_path)
    try:
        registry = LibraryRegistry(hub)
        library = registry.attach(library_dir, name)
        registry.set_active(library.id)
    finally:
        hub.close()

    with open(os.path.join(config_dir, SERVER_CONFIG_FILENAME), "w") as handle:
        json.dump({"image_root": library_dir, "port": 9999}, handle)

    return {
        "config_dir": config_dir,
        "hub_path": hub_path,
        "library_dir": library_dir,
        "name": name,
    }


def _backup(install: dict, destination: str) -> int:
    return main(
        [
            "--hub",
            install["hub_path"],
            "libraries",
            "backup",
            install["name"],
            destination,
        ]
    )


def _restore(install: dict, archive: str, folder: str, *, yes: bool = True) -> int:
    argv = ["--hub", install["hub_path"], "libraries", "restore", archive, folder]
    if yes:
        argv.append("--yes")
    return main(argv)


def _scratch_dirs(root: str) -> set[str]:
    """Staging directories currently under *root*, so a leak can be attributed."""
    return {
        entry for entry in os.listdir(root) if entry.startswith(".pixlstash-restore-")
    }


def _active_library(hub_path: str):
    hub = HubDatabase(hub_path)
    try:
        return LibraryRegistry(hub).active_library()
    finally:
        hub.close()


def test_restore_round_trip_publishes_library_and_preserves_the_old_pair(
    temp_root, capsys
):
    """The core promise: pictures land, the restored library opens, the old one survives."""
    # A space in the name on purpose: it reaches the printed launch commands,
    # which are useless if they cannot be pasted.
    source = _install(temp_root, "round trip", pictures=4)
    archive = os.path.join(temp_root, "round-trip.tar.zst")
    assert _backup(source, archive) == 0
    capsys.readouterr()

    # Read through a context manager: the restore MOVES hub.db, and on Windows
    # an open handle blocks the rename, which would fail here and nowhere else.
    with open(source["hub_path"], "rb") as handle:
        original_hub_bytes = handle.read()
    destination = os.path.join(temp_root, "round-trip-restored")
    assert _restore(source, archive, destination) == 0
    out = capsys.readouterr().out

    # The library files sit beside vault.db, not under an images/ subfolder.
    assert os.path.isfile(os.path.join(destination, VAULT_FILENAME))
    assert not os.path.isdir(os.path.join(destination, "images"))
    assert sorted(
        name for name in os.listdir(destination) if name.endswith(".jpg")
    ) == [f"picture-{index}.jpg" for index in range(4)]

    # The restored hub is live and its library points at the new folder.
    active = _active_library(source["hub_path"])
    assert active is not None
    assert active.path == os.path.realpath(destination)

    # The previous pair is preserved, intact, and launchable on its own.
    preserved = [
        os.path.join(source["config_dir"], entry)
        for entry in os.listdir(source["config_dir"])
        if entry.startswith("pre-restore-")
    ]
    assert len(preserved) == 1
    preserved_dir = preserved[0]
    with open(os.path.join(preserved_dir, "hub.db"), "rb") as handle:
        assert handle.read() == original_hub_bytes
    preserved_active = _active_library(os.path.join(preserved_dir, "hub.db"))
    assert preserved_active is not None
    assert preserved_active.path == os.path.realpath(source["library_dir"])

    # Both launch commands are printed, labelled, and shell-safe.
    assert "RESTORED" in out and "PREVIOUS" in out
    restored_config = os.path.join(source["config_dir"], SERVER_CONFIG_FILENAME)
    previous_config = os.path.join(preserved_dir, SERVER_CONFIG_FILENAME)
    assert f'--server-config "{restored_config}"' in out
    assert f'--server-config "{previous_config}"' in out

    # The original library folder was not touched.
    assert os.path.isfile(os.path.join(source["library_dir"], VAULT_FILENAME))

    # image_root follows the restored library; machine settings are carried over.
    with open(os.path.join(source["config_dir"], SERVER_CONFIG_FILENAME)) as handle:
        config = json.load(handle)
    assert config["image_root"] == destination
    assert config["port"] == 9999


class _FakeTerminal(io.StringIO):
    """A stderr that claims to be a terminal, so the bar is not auto-suppressed."""

    def isatty(self) -> bool:
        return True


def test_progress_is_drawn_on_a_terminal_and_silent_off_one(temp_root, monkeypatch):
    """Both directions, because either failure is invisible.

    A bar that never draws looks like a hung command; one that draws into a cron
    log fills the mail with redraw escapes. Neither shows up in a test that only
    checks the command succeeded.
    """
    from pixlstash.services import library_restore_service as restore

    source = _install(temp_root, "progress")
    archive = os.path.join(temp_root, "progress.tar.zst")
    assert _backup(source, archive) == 0

    terminal = _FakeTerminal()
    monkeypatch.setattr("sys.stderr", terminal)
    assert restore.progress_disabled() is False
    # Cleaned up explicitly: restore_scratch stages beside the destination, so a
    # leaked one lands in the shared temp_root and shows up as another test's
    # leftover.
    scratch = restore.restore_scratch(os.path.join(temp_root, "p1"))
    try:
        restore._extract(archive, scratch)
    finally:
        restore.remove_scratch(scratch)
    assert "Restoring" in terminal.getvalue(), "no progress bar on a terminal"

    # Backup draws one too, from the same decision.
    backup_terminal = _FakeTerminal()
    monkeypatch.setattr("sys.stderr", backup_terminal)
    assert _backup(source, os.path.join(temp_root, "progress-2.tar.zst")) == 0
    assert "Archiving" in backup_terminal.getvalue(), "no progress bar on backup"

    quiet = io.StringIO()  # a plain buffer is not a tty
    monkeypatch.setattr("sys.stderr", quiet)
    assert restore.progress_disabled() is True
    scratch = restore.restore_scratch(os.path.join(temp_root, "p2"))
    try:
        restore._extract(archive, scratch)
    finally:
        restore.remove_scratch(scratch)
    assert quiet.getvalue() == "", "progress bar drawn into a non-terminal"


def test_restore_warns_about_space_in_the_one_prompt_it_already_asks(
    temp_root, capsys, monkeypatch
):
    """A shortfall is a warning beside the existing question, not a second one.

    Two prompts about one decision is how people learn to hold down `y`. It also
    has to appear *before* anything is unpacked - the whole reason planning
    reads only the front of the archive.
    """
    from pixlstash.services import library_restore_service as restore

    source = _install(temp_root, "no-room")
    archive = os.path.join(temp_root, "no-room.tar.zst")
    assert _backup(source, archive) == 0
    capsys.readouterr()

    # Pretend the filesystem is nearly full, whatever it actually is.
    monkeypatch.setattr(restore, "space_shortfall", lambda path, needed: (needed, 1024))
    destination = os.path.join(temp_root, "no-room-restored")
    scratch_before = _scratch_dirs(temp_root)

    asked: list[str] = []

    def decline(prompt: str) -> str:
        asked.append(prompt)
        return "n"

    monkeypatch.setattr("builtins.input", decline)
    assert _restore(source, archive, destination, yes=False) == 1

    captured = capsys.readouterr()
    assert "may not have room" in captured.err
    assert "is free" in captured.err
    # Exactly one question, and it is the ordinary one.
    assert len(asked) == 1, asked
    assert "Restore it?" in asked[0]

    assert "Cancelled" in captured.out
    assert not os.path.exists(destination)
    assert not any(
        entry.startswith("pre-restore-") for entry in os.listdir(source["config_dir"])
    )
    assert scratch_before == _scratch_dirs(temp_root), (
        "a declined restore staged something anyway"
    )


def test_restore_survives_scratch_and_config_on_different_filesystems(
    temp_root, capsys, monkeypatch
):
    """The reported crash: ~/Pictures and ~/.config on separate mounts.

    Staging happens beside the restored library so publishing it is a rename;
    the hub has to reach the config directory, which is routinely a different
    filesystem, and ``os.replace`` across one raises EXDEV. Simulated by making
    ``os.replace`` refuse exactly the cross-directory hub move, because a real
    second filesystem is not available in the gate.
    """
    source = _install(temp_root, "cross-device")
    archive = os.path.join(temp_root, "cross-device.tar.zst")
    assert _backup(source, archive) == 0
    capsys.readouterr()

    real_replace = os.replace
    config_dir = source["config_dir"]

    def refuse_cross_device(src, dst, *args, **kwargs):
        # Exactly the reported shape: the config directory is its own mount, so
        # anything renamed *into* it from elsewhere fails. Renames within one
        # directory, and the library publication inside the temp root, are
        # ordinary same-filesystem moves and must still work.
        entering_config = os.path.dirname(str(dst)) == config_dir
        from_elsewhere = os.path.dirname(str(src)) != config_dir
        if entering_config and from_elsewhere:
            raise OSError(errno.EXDEV, "Invalid cross-device link", str(src))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", refuse_cross_device)
    destination = os.path.join(temp_root, "cross-device-restored")
    assert _restore(source, archive, destination) == 0, capsys.readouterr().err

    # The hub really did land, and the restored library is the active one.
    active = _active_library(source["hub_path"])
    assert active is not None
    assert active.path == os.path.realpath(destination)


def test_backup_announces_itself_before_the_bar(temp_root, capsys):
    """The bar appears with no context otherwise: say what and where first."""
    source = _install(temp_root, "announce", pictures=4)
    destination = os.path.join(temp_root, "announce.tar.zst")
    assert _backup(source, destination) == 0
    err = capsys.readouterr().err
    assert "Archiving" in err and "file(s)" in err
    assert destination in err


@pytest.mark.parametrize(
    ("given", "compress", "expected"),
    [
        ("monday", True, "monday.tar.zst"),
        ("monday", False, "monday.tar"),
        ("monday.tar.zst", True, "monday.tar.zst"),
        ("monday.tar", False, "monday.tar"),
        # The suffix must not lie about the contents: --no-compress on a name
        # ending .tar.zst swaps rather than appends.
        ("monday.tar.zst", False, "monday.tar"),
        ("monday.tar", True, "monday.tar.zst"),
    ],
)
def test_backup_gives_the_file_the_right_ending(temp_root, given, compress, expected):
    """An explicit filename used to keep whatever the user typed, extension or not."""
    from pixlstash.services.library_backup_service import _resolve_destination

    class _Library:
        name = "whatever"

    resolved = _resolve_destination(
        _Library(), os.path.join(temp_root, given), compress
    )
    assert os.path.basename(resolved) == expected


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_restored_files_are_owner_only(temp_root, capsys):
    """Extraction must not widen what the backup kept private.

    hub.db is 0600 by contract and snapshot archives are chmodded 0600 on the
    way in, so writing them back at the process umask would undo that for the
    files that carry credentials.
    """
    source = _install(temp_root, "modes")
    archive = os.path.join(temp_root, "modes.tar.zst")
    assert _backup(source, archive) == 0
    capsys.readouterr()

    destination = os.path.join(temp_root, "modes-restored")
    assert _restore(source, archive, destination) == 0

    for entry in sorted(os.listdir(destination)):
        mode = stat.S_IMODE(os.stat(os.path.join(destination, entry)).st_mode)
        assert mode == 0o600, f"{entry} is {oct(mode)}, not owner-only"
    hub_mode = stat.S_IMODE(os.stat(source["hub_path"]).st_mode)
    assert hub_mode == 0o600, f"restored hub is {oct(hub_mode)}"


def test_restore_refuses_a_destination_with_contents(temp_root, capsys):
    """A non-empty destination is the one way a restore could destroy data."""
    source = _install(temp_root, "occupied")
    archive = os.path.join(temp_root, "occupied.tar.zst")
    assert _backup(source, archive) == 0
    capsys.readouterr()

    destination = os.path.join(temp_root, "occupied-restored")
    os.makedirs(destination)
    with open(os.path.join(destination, "keep-me.txt"), "w") as handle:
        handle.write("not yours")

    assert _restore(source, archive, destination) == 1
    assert "already has contents" in capsys.readouterr().err
    # Nothing moved: the refusal is before any publication.
    assert os.path.isfile(os.path.join(destination, "keep-me.txt"))
    assert not any(
        entry.startswith("pre-restore-") for entry in os.listdir(source["config_dir"])
    )


def test_declining_the_prompt_changes_nothing(temp_root, capsys, monkeypatch):
    """Answering no must leave both the destination and the config dir alone."""
    source = _install(temp_root, "declined")
    archive = os.path.join(temp_root, "declined.tar.zst")
    assert _backup(source, archive) == 0
    capsys.readouterr()

    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    destination = os.path.join(temp_root, "declined-restored")
    assert _restore(source, archive, destination, yes=False) == 1

    assert "Cancelled" in capsys.readouterr().out
    assert not os.path.exists(destination)
    assert not any(
        entry.startswith("pre-restore-") for entry in os.listdir(source["config_dir"])
    )


def test_restore_refuses_an_archive_it_did_not_write(temp_root, capsys):
    """A foreign tar is refused whole rather than partially unpacked."""
    source = _install(temp_root, "foreign")
    archive = os.path.join(temp_root, "foreign.tar")
    payload = os.path.join(temp_root, "payload.txt")
    with open(payload, "w") as handle:
        handle.write("not a backup")
    with tarfile.open(archive, "w") as tar:
        tar.add(payload, arcname="somewhere/else.txt")

    destination = os.path.join(temp_root, "foreign-restored")
    assert _restore(source, archive, destination) == 1
    assert "not a PixlStash backup" in capsys.readouterr().err
    assert not os.path.exists(destination)


@pytest.mark.parametrize(
    "member_name",
    [
        "images/../../escaped.txt",
        "../escaped.txt",
        "/etc/escaped.txt",
        "images/..\\..\\escaped.txt",
        # Windows only in effect, but refused everywhere: os.path.join discards
        # the root when a component carries a drive letter, so this name lands
        # outside the staging directory on Windows while passing every check
        # that reasons about the string.
        "images/C:escaped.txt",
        "images/C:/Windows/escaped.txt",
    ],
)
def test_archive_member_cannot_escape_the_restore_directory(member_name):
    """Every path that leaves the staging root is refused, not sanitised."""
    from pixlstash.services.library_restore_service import (
        RestoreError,
        _safe_member_target,
    )

    member = tarfile.TarInfo(member_name)
    member.type = tarfile.REGTYPE
    member.size = 1
    with pytest.raises(RestoreError):
        _safe_member_target(member, os.path.join(os.sep, "scratch", "root"))


def test_containment_backstop_refuses_a_target_outside_the_root():
    """Asserted directly: on POSIX no name reaching it can escape, so nothing else covers it.

    The check exists for the Windows join behaviour the name checks cannot see.
    Without a direct test it would be unfalsifiable on the Linux gate - a guard
    that no test can fail is indistinguishable from one that does nothing.
    """
    from pixlstash.services.library_restore_service import RestoreError, _contained

    member = tarfile.TarInfo("images/whatever.jpg")
    root = os.path.join(os.sep, "scratch", "root")
    assert _contained(os.path.join(root, "library", "a.jpg"), root, member)
    with pytest.raises(RestoreError, match="outside the restore directory"):
        _contained(os.path.join(os.sep, "elsewhere", "a.jpg"), root, member)


def test_archive_symlink_members_are_refused():
    """A symlink member could redirect a later write outside the library."""
    from pixlstash.services.library_restore_service import (
        RestoreError,
        _safe_member_target,
    )

    member = tarfile.TarInfo("images/evil.jpg")
    member.type = tarfile.SYMTYPE
    member.linkname = "/etc/passwd"
    with pytest.raises(RestoreError, match="non-regular"):
        _safe_member_target(member, os.path.join(os.sep, "scratch", "root"))


def test_restore_warns_that_the_archive_supplies_the_credentials(temp_root, capsys):
    """The reassuring output must not bury who ends up owning the install."""
    source = _install(temp_root, "credential-warning")
    archive = os.path.join(temp_root, "credential-warning.tar.zst")
    assert _backup(source, archive) == 0
    capsys.readouterr()

    destination = os.path.join(temp_root, "credential-warning-restored")
    assert _restore(source, archive, destination) == 0
    out = capsys.readouterr().out
    assert "owner access to this machine" in out
    assert "archive you made yourself" in out


def test_restore_refuses_while_the_hub_is_locked(temp_root, capsys):
    """A held write lock means the server is running, and moving hub.db would lose data."""
    source = _install(temp_root, "locked")
    archive = os.path.join(temp_root, "locked.tar.zst")
    assert _backup(source, archive) == 0
    capsys.readouterr()

    holder = sqlite3.connect(source["hub_path"], timeout=0.5)
    try:
        holder.execute("BEGIN IMMEDIATE")
        destination = os.path.join(temp_root, "locked-restored")
        assert _restore(source, archive, destination) == 1
        assert "PixlStash is running" in capsys.readouterr().err
        assert not os.path.exists(destination)
    finally:
        holder.rollback()
        holder.close()
