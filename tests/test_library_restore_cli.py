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

import json
import os
import sqlite3
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

    original_hub_bytes = open(source["hub_path"], "rb").read()
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
    assert open(os.path.join(preserved_dir, "hub.db"), "rb").read() == (
        original_hub_bytes
    )
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
    assert "not part of a PixlStash backup" in capsys.readouterr().err
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
    Without a direct test it would be unfalsifiable on the Linux gate — a guard
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
