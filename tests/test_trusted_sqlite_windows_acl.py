"""The trusted-SQLite guard's Windows owner/DACL check.

Four layers, matching the code's split. The classifier is a pure function of
owner SID, user SID and ACE list, so its whole decision table runs here on the
Linux gate with synthetic ACLs. The guard's wiring runs under a simulated
``nt`` with a fake reader. The ctypes reader and the DACL writer run on Linux
too, against a fake ``_win32()`` over real ctypes buffers, which pins the byte
layouts and the error dispatch. The last class runs against real files on the
Windows lane only (``backend_windows`` in ``.github/workflows/ci.yml`` names
this file).

On Windows every permission finding is reported and the open proceeds (the
owner's decision); the protection is the locked-down DACL PixlStash puts on
what it creates. Both directions throughout: another account that can write
must be reported, and the default ACL of a user profile - and read-only grants
to everyone - must stay silent.
"""

import ctypes
import sqlite3
import errno
import os
import shutil
import struct
import subprocess
import types
from pathlib import Path

import pytest

from pixlstash import trusted_sqlite
from pixlstash.hub import db as hub_db
from pixlstash.startup_permissions import makedirs_private, mkdir_private
from pixlstash.trusted_sqlite import (
    WINDOWS_INHERITED_WRITE_RIGHTS,
    WINDOWS_WRITE_RIGHTS,
    TrustedSQLiteLocation,
    TrustedSQLiteLocationError,
    WindowsAce,
    classify_windows_acl,
)
from tests.windows_acl_vectors import (
    ADMINISTRATORS,
    ALLOW_CALLBACK,
    ALLOW_OBJECT,
    AUTHENTICATED_USERS,
    CREATOR_OWNER,
    DENY,
    EVERYONE,
    FULL_CONTROL,
    INHERIT_ONLY,
    ME,
    OWNER_RIGHTS,
    READ_AND_EXECUTE,
    SOMEONE_ELSE,
    SYSTEM,
    TRUSTED_INSTALLER,
    USERS,
    allow,
    install_fake_windows_security,
    profile_default_dacl,
    security,
)

OBJECT_INHERIT = 0x01
CONTAINER_INHERIT = 0x02
FILE_WRITE_DATA = 0x0002  # FILE_ADD_FILE on a directory
FILE_APPEND_DATA = 0x0004  # FILE_ADD_SUBDIRECTORY on a directory
FILE_WRITE_EA = 0x0010
FILE_DELETE_CHILD = 0x0040
FILE_WRITE_ATTRIBUTES = 0x0100
DELETE = 0x00010000
WRITE_DAC = 0x00040000
WRITE_OWNER = 0x00080000
GENERIC_ALL = 0x10000000
GENERIC_WRITE = 0x40000000
GENERIC_READ = 0x80000000
GENERIC_EXECUTE = 0x20000000

DIRECTORY_RIGHTS = {
    "FILE_ADD_FILE": FILE_WRITE_DATA,
    "FILE_ADD_SUBDIRECTORY": FILE_APPEND_DATA,
    "FILE_DELETE_CHILD": FILE_DELETE_CHILD,
    "DELETE": DELETE,
    "WRITE_DAC": WRITE_DAC,
    "WRITE_OWNER": WRITE_OWNER,
    "GENERIC_WRITE": GENERIC_WRITE,
    "GENERIC_ALL": GENERIC_ALL,
}
FILE_RIGHTS = {
    "FILE_WRITE_DATA": FILE_WRITE_DATA,
    "FILE_APPEND_DATA": FILE_APPEND_DATA,
    "DELETE": DELETE,
    "WRITE_DAC": WRITE_DAC,
    "WRITE_OWNER": WRITE_OWNER,
    "GENERIC_WRITE": GENERIC_WRITE,
    "GENERIC_ALL": GENERIC_ALL,
}
# Above the immediate parent only replacing an existing entry counts.
ANCESTOR_REFUSED = {
    name: DIRECTORY_RIGHTS[name]
    for name in ("FILE_DELETE_CHILD", "DELETE", "WRITE_DAC", "WRITE_OWNER")
} | {"GENERIC_ALL": GENERIC_ALL}
ANCESTOR_ADD_ONLY = {
    name: DIRECTORY_RIGHTS[name]
    for name in ("FILE_ADD_FILE", "FILE_ADD_SUBDIRECTORY", "GENERIC_WRITE")
}
STRANGERS = {
    "Everyone": EVERYONE,
    "Authenticated Users": AUTHENTICATED_USERS,
    "Users": USERS,
    "another user": SOMEONE_ELSE,
}


def _classify(dacl, *, kind, owner=ME, volume_root=False):
    return classify_windows_acl(
        owner,
        ME,
        None if dacl is None else tuple(dacl),
        write_rights=WINDOWS_WRITE_RIGHTS[kind],
        inherited_file_rights=WINDOWS_INHERITED_WRITE_RIGHTS[kind],
        volume_root=volume_root,
    )


def _warned(caplog, text: str) -> bool:
    return any(
        text in record.getMessage() and record.levelname == "WARNING"
        for record in caplog.records
    )


class TestClassifierPasses:
    """Over-blocking is its own regression: these must come back clean."""

    @pytest.mark.parametrize("kind", ["directory", "ancestor", "file"])
    def test_the_profile_default_acl_passes(self, kind):
        verdict = _classify(profile_default_dacl(), kind=kind)

        assert verdict.owner_trusted
        assert verdict.writers == ()
        assert verdict.readers == ()

    @pytest.mark.parametrize("owner", [ME, SYSTEM, ADMINISTRATORS, TRUSTED_INSTALLER])
    def test_the_user_and_the_os_principals_may_own_it(self, owner):
        assert _classify(profile_default_dacl(), kind="file", owner=owner).owner_trusted

    @pytest.mark.parametrize("kind", ["directory", "ancestor", "file"])
    @pytest.mark.parametrize("sid", sorted(STRANGERS.values()))
    def test_read_only_grants_to_strangers_pass(self, kind, sid):
        """``C:\\Users`` grants Everyone RX; that is not a way to write."""
        dacl = profile_default_dacl() + (
            allow(sid, READ_AND_EXECUTE),
            allow(sid, GENERIC_READ | GENERIC_EXECUTE),
            allow(sid, FILE_WRITE_ATTRIBUTES | FILE_WRITE_EA),
        )

        verdict = _classify(dacl, kind=kind)

        assert verdict.writers == ()
        assert sid in verdict.readers

    @pytest.mark.parametrize("kind", ["directory", "ancestor", "file"])
    def test_an_inherit_only_container_grant_is_ignored(self, kind):
        """It describes child folders only, never this object or its files."""
        dacl = profile_default_dacl() + (
            allow(EVERYONE, FULL_CONTROL, flags=INHERIT_ONLY | CONTAINER_INHERIT),
        )

        assert _classify(dacl, kind=kind).writers == ()

    @pytest.mark.parametrize("kind", ["ancestor", "file"])
    def test_an_inherit_only_object_grant_is_ignored_off_the_database_folder(
        self, kind
    ):
        """``C:\\``'s ``(OI)(CI)(IO)(M)``: only the database folder's
        inheritance reaches SQLite's later sidecars."""
        dacl = profile_default_dacl() + (
            allow(EVERYONE, FULL_CONTROL, flags=INHERIT_ONLY | 0x03),
        )

        assert _classify(dacl, kind=kind).writers == ()

    def test_a_file_only_read_grant_inherited_to_new_files_is_not_a_writer(self):
        dacl = profile_default_dacl() + (
            allow(USERS, READ_AND_EXECUTE, flags=INHERIT_ONLY | OBJECT_INHERIT),
        )

        assert _classify(dacl, kind="directory").writers == ()

    def test_delete_on_a_volume_root_grants_nothing(self):
        """A drive root cannot be renamed or deleted."""
        dacl = profile_default_dacl() + (allow(AUTHENTICATED_USERS, DELETE),)

        assert _classify(dacl, kind="ancestor", volume_root=True).writers == ()
        assert _classify(dacl, kind="directory", volume_root=True).writers == ()

    @pytest.mark.parametrize("name,mask", sorted(ANCESTOR_ADD_ONLY.items()))
    def test_add_only_rights_on_an_ancestor_are_the_sticky_bit(self, name, mask):
        """``C:\\`` grants Authenticated Users FILE_ADD_SUBDIRECTORY on itself.

        Refusing that would refuse every path on the system drive.
        """
        dacl = profile_default_dacl() + (allow(AUTHENTICATED_USERS, mask),)

        assert _classify(dacl, kind="ancestor").writers == ()

    @pytest.mark.parametrize("kind", ["directory", "ancestor", "file"])
    def test_creator_owner_and_owner_rights_placeholders_grant_nothing(self, kind):
        """Python 3.13's ``mkdir(mode=0o700)`` writes ``OWNER RIGHTS`` ACEs."""
        dacl = (
            allow(SYSTEM),
            allow(ADMINISTRATORS),
            allow(OWNER_RIGHTS),
            allow(CREATOR_OWNER),
        )

        assert _classify(dacl, kind=kind).writers == ()

    def test_sid_comparison_ignores_case(self):
        verdict = _classify((allow(ME.lower()),), kind="directory", owner=ME.lower())

        assert verdict.owner_trusted
        assert verdict.writers == ()


class TestClassifierFindsWriters:
    @pytest.mark.parametrize("who", sorted(STRANGERS))
    @pytest.mark.parametrize("name", sorted(DIRECTORY_RIGHTS))
    def test_a_stranger_with_any_directory_write_right_is_named(self, who, name):
        sid = STRANGERS[who]
        dacl = profile_default_dacl() + (allow(sid, DIRECTORY_RIGHTS[name]),)

        assert _classify(dacl, kind="directory").writers == (sid,)

    @pytest.mark.parametrize("who", sorted(STRANGERS))
    @pytest.mark.parametrize("name", sorted(FILE_RIGHTS))
    def test_a_stranger_with_any_file_write_right_is_named(self, who, name):
        sid = STRANGERS[who]
        dacl = profile_default_dacl() + (allow(sid, FILE_RIGHTS[name]),)

        assert _classify(dacl, kind="file").writers == (sid,)

    @pytest.mark.parametrize("name", sorted(ANCESTOR_REFUSED))
    def test_a_right_that_replaces_an_entry_is_refused_on_an_ancestor(self, name):
        dacl = profile_default_dacl() + (
            allow(AUTHENTICATED_USERS, ANCESTOR_REFUSED[name]),
        )

        assert _classify(dacl, kind="ancestor").writers == (AUTHENTICATED_USERS,)

    @pytest.mark.parametrize("kind", ["directory", "ancestor", "file"])
    def test_a_null_dacl_is_everyone_with_full_control(self, kind):
        verdict = _classify(None, kind=kind)

        assert verdict.null_dacl
        assert verdict.writers == (EVERYONE,)

    @pytest.mark.parametrize("owner", [SOMEONE_ELSE, USERS, EVERYONE])
    def test_a_foreign_owner_is_not_trusted(self, owner):
        assert not _classify(
            profile_default_dacl(), kind="file", owner=owner
        ).owner_trusted

    def test_a_deny_never_cancels_an_allow(self):
        """Conservative on purpose: ordering is not reasoned about."""
        dacl = (
            WindowsAce(DENY, 0, FULL_CONTROL, EVERYONE),
            *profile_default_dacl(),
            allow(EVERYONE, FILE_WRITE_DATA),
        )

        assert _classify(dacl, kind="file").writers == (EVERYONE,)

    @pytest.mark.parametrize("ace_type", [ALLOW_OBJECT, ALLOW_CALLBACK])
    def test_object_and_callback_allows_count_unconditionally(self, ace_type):
        dacl = profile_default_dacl() + (
            WindowsAce(ace_type, 0, FILE_WRITE_DATA, SOMEONE_ELSE),
        )

        assert _classify(dacl, kind="file").writers == (SOMEONE_ELSE,)

    @pytest.mark.parametrize("ace_type", [0x4, 0x11, 0x7F])
    def test_an_unknown_ace_type_is_a_stranger_with_full_control(self, ace_type):
        dacl = profile_default_dacl() + (WindowsAce(ace_type, 0, 0, None),)

        (writer,) = _classify(dacl, kind="ancestor").writers
        assert "unreadable ACE type" in writer

    @pytest.mark.parametrize("name", sorted(FILE_RIGHTS))
    def test_an_object_inherit_grant_on_the_database_folder_is_a_writer(self, name):
        """SQLite's ``-wal``/``-shm``/``-journal`` inherit it when created later."""
        dacl = profile_default_dacl() + (
            allow(SOMEONE_ELSE, FILE_RIGHTS[name], flags=INHERIT_ONLY | OBJECT_INHERIT),
        )

        assert _classify(dacl, kind="directory").writers == (SOMEONE_ELSE,)

    @pytest.mark.parametrize("name", sorted(set(DIRECTORY_RIGHTS) - {"DELETE"}))
    def test_a_volume_root_still_reports_every_right_but_delete(self, name):
        dacl = profile_default_dacl() + (allow(EVERYONE, DIRECTORY_RIGHTS[name]),)

        assert _classify(dacl, kind="directory", volume_root=True).writers == (
            EVERYONE,
        )

    def test_an_allow_whose_sid_could_not_be_read_is_a_stranger(self):
        dacl = profile_default_dacl() + (WindowsAce(0x0, 0, FILE_WRITE_EA, None),)

        assert _classify(dacl, kind="file").writers != ()


class TestGuardOnSimulatedWindows:
    """The wiring: which objects are asked about, and what each finding does.

    ``os.name`` is forced to ``nt`` and the ctypes reader replaced with
    vectors, so the namespace walk, the file check and the sidecar loop all run
    their Windows branch on this Linux gate. Every finding warns; the open
    always goes ahead.
    """

    @pytest.fixture
    def library(self, tmp_path, monkeypatch):
        directory = tmp_path / "library"
        directory.mkdir(mode=0o700)
        monkeypatch.setattr(os, "name", "nt")
        return directory

    def test_a_default_profile_namespace_opens_silently_and_every_object_is_read(
        self, library, monkeypatch, caplog
    ):
        seen = install_fake_windows_security(monkeypatch)
        path = library / "hub.db"

        TrustedSQLiteLocation.open(
            str(path), private=True, create=True, trusted_root=str(library)
        ).close()

        assert str(library) in seen
        assert str(path) in seen
        assert os.path.dirname(str(library)) in seen
        assert not [r for r in caplog.records if r.levelname == "WARNING"]

    def test_a_stranger_writable_directory_is_reported_with_the_fix(
        self, library, monkeypatch, caplog
    ):
        install_fake_windows_security(
            monkeypatch,
            {
                library: security(
                    dacl=profile_default_dacl()
                    + (allow(EVERYONE, GENERIC_WRITE, flags=0x03),)
                )
            },
        )

        TrustedSQLiteLocation.open(str(library / "vault.db"), create=True).close()

        assert (library / "vault.db").exists()
        assert _warned(caplog, f"{library} can also be changed by Everyone (S-1-1-0)")
        assert _warned(caplog, "/remove:g *S-1-1-0")
        assert _warned(caplog, "Opening it as usual")

    def test_a_folder_whose_new_files_inherit_stranger_write_is_reported(
        self, library, monkeypatch, caplog
    ):
        """F7: the grant is inherit-only, so only SQLite's later sidecars get it."""
        install_fake_windows_security(
            monkeypatch,
            {
                library: security(
                    dacl=profile_default_dacl()
                    + (
                        allow(
                            SOMEONE_ELSE,
                            FILE_WRITE_DATA,
                            flags=INHERIT_ONLY | OBJECT_INHERIT,
                        ),
                    )
                )
            },
        )

        TrustedSQLiteLocation.open(str(library / "vault.db"), create=True).close()

        assert _warned(caplog, f"{library} can also be changed by {SOMEONE_ELSE}")

    def test_a_foreign_owned_file_is_reported_and_opens(
        self, library, monkeypatch, caplog
    ):
        path = library / "vault.db"
        path.touch()
        install_fake_windows_security(monkeypatch, {path: security(owner=SOMEONE_ELSE)})

        TrustedSQLiteLocation.open(str(path)).close()

        assert _warned(caplog, f"{path} is owned by {SOMEONE_ELSE}")
        assert _warned(caplog, f'takeown /F "{path}"')

    def test_an_ancestor_a_stranger_could_rename_is_reported(
        self, library, monkeypatch, caplog
    ):
        """``D:\\\\Photos`` inheriting Authenticated Users Modify (``M`` has DELETE)."""
        ancestor = os.path.dirname(str(library))
        install_fake_windows_security(
            monkeypatch,
            {
                ancestor: security(
                    dacl=profile_default_dacl()
                    + (allow(AUTHENTICATED_USERS, DELETE | FILE_WRITE_DATA),)
                )
            },
        )

        TrustedSQLiteLocation.open(str(library / "vault.db"), create=True).close()

        assert _warned(caplog, f"{ancestor} can also be changed by Authenticated Users")

    def test_a_system_drive_root_with_add_only_rights_stays_silent(
        self, library, monkeypatch, caplog
    ):
        """The ``C:\\\\`` shape, applied to every ancestor above the library."""
        add_only = security(
            owner=TRUSTED_INSTALLER,
            dacl=profile_default_dacl()
            + (
                allow(AUTHENTICATED_USERS, FILE_APPEND_DATA),
                allow(AUTHENTICATED_USERS, FULL_CONTROL, flags=INHERIT_ONLY | 0x03),
                allow(USERS, READ_AND_EXECUTE),
            ),
        )
        overrides = {}
        current = os.path.dirname(str(library))
        while True:
            overrides[current] = add_only
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        install_fake_windows_security(monkeypatch, overrides)

        TrustedSQLiteLocation.open(str(library / "vault.db"), create=True).close()

        assert not [r for r in caplog.records if r.levelname == "WARNING"]

    def test_a_drive_root_is_never_offered_an_icacls_command(
        self, library, monkeypatch, caplog
    ):
        """F8: rewriting a whole drive's permissions is never the advice."""
        root_security = trusted_sqlite._WindowsSecurity(
            owner_sid=SOMEONE_ELSE,
            dacl=profile_default_dacl() + (allow(EVERYONE, FULL_CONTROL),),
            is_volume_root=True,
        )
        install_fake_windows_security(monkeypatch, {library: root_security})

        TrustedSQLiteLocation.open(str(library / "vault.db"), create=True).close()

        notices = [
            r.getMessage() for r in caplog.records if str(library) in r.getMessage()
        ]
        assert any("folder of its own" in text for text in notices)
        assert not any("icacls" in text or "takeown" in text for text in notices)

    def test_a_private_file_readable_by_a_stranger_warns_and_opens(
        self, library, monkeypatch, caplog
    ):
        path = library / "hub.db"
        path.touch()
        install_fake_windows_security(
            monkeypatch,
            {
                path: security(
                    dacl=profile_default_dacl() + (allow(USERS, READ_AND_EXECUTE),)
                )
            },
        )

        TrustedSQLiteLocation.open(
            str(path), private=True, trusted_root=str(library)
        ).close()

        assert _warned(caplog, "can be read by BUILTIN\\Users")

    def test_an_unreadable_descriptor_names_its_true_cause(
        self, library, monkeypatch, caplog
    ):
        """F1: an NTFS read failure is not a FAT32 volume and must not say so."""
        reason = "GetNamedSecurityInfoW failed with Windows error 5 (Access is denied.)"
        install_fake_windows_security(
            monkeypatch,
            {library: trusted_sqlite._UnverifiableWindowsAcl(reason, volume_serial=7)},
        )

        TrustedSQLiteLocation.open(str(library / "vault.db"), create=True).close()

        notices = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(str(library) in t and reason in t for t in notices)
        assert not any("FAT32" in t for t in notices)

    def test_each_finding_is_logged_once_per_volume(self, library, monkeypatch, caplog):
        """F4: the same drive is quiet the second time; a swapped one is not."""
        loose = profile_default_dacl() + (allow(EVERYONE, GENERIC_WRITE),)
        overrides = {library: security(dacl=loose)}
        install_fake_windows_security(monkeypatch, overrides)
        path = str(library / "vault.db")

        TrustedSQLiteLocation.open(path, create=True).close()
        TrustedSQLiteLocation.open(path).close()
        first = [r for r in caplog.records if str(library) in r.getMessage()]

        overrides[library] = trusted_sqlite._WindowsSecurity(
            owner_sid=ME, dacl=tuple(loose), volume_serial=0xBEEF
        )
        install_fake_windows_security(monkeypatch, overrides)
        monkeypatch.setattr(
            trusted_sqlite,
            "_notices_logged",
            {(None, str(library), first[0].getMessage())},
        )
        TrustedSQLiteLocation.open(path).close()
        after = [r for r in caplog.records if str(library) in r.getMessage()]

        assert len(first) == 1
        assert len(after) == 2

    def test_the_unverifiable_decision_is_one_switch(self, library, monkeypatch):
        """Flipping ``_accept_unverifiable_windows_acl`` to raise refuses the open."""
        install_fake_windows_security(
            monkeypatch,
            {
                library: trusted_sqlite._UnverifiableWindowsAcl(
                    "the volume keeps no file permissions"
                )
            },
        )

        def refuse(path, reason, volume_serial=None):
            raise TrustedSQLiteLocationError(f"{path}: {reason}")

        monkeypatch.setattr(trusted_sqlite, "_accept_unverifiable_windows_acl", refuse)

        with pytest.raises(TrustedSQLiteLocationError, match="no file permissions"):
            TrustedSQLiteLocation.open(str(library / "vault.db"), create=True)


class TestCreationIsLockedDown:
    """F2/R2-5: what PixlStash creates is created locked; what it finds is not."""

    @pytest.fixture
    def windows(self, monkeypatch):
        monkeypatch.setattr(os, "name", "nt")
        return install_fake_windows_security(monkeypatch)

    def test_a_vault_the_guard_creates_is_created_locked(self, tmp_path, windows):
        path = tmp_path / "vault.db"

        TrustedSQLiteLocation.open(str(path), create=True).close()

        assert windows.created == [(os.path.realpath(path), False)]

    def test_an_existing_vault_is_left_as_it_is(self, tmp_path, windows):
        path = tmp_path / "vault.db"
        path.touch()

        TrustedSQLiteLocation.open(str(path), create=True).close()

        assert windows.created == []
        assert windows.locked == []

    def test_a_hub_file_created_on_first_open_is_created_locked(
        self, tmp_path, windows
    ):
        path = str(tmp_path / "hub.db")

        created, _identity = hub_db._prepare_hub_file(path, repair=False)
        again, _identity = hub_db._prepare_hub_file(path, repair=False)

        assert created and not again
        assert windows.created == [(path, False)]

    def test_mkdir_private_creates_only_the_missing_folders_locked(
        self, tmp_path, windows
    ):
        existing = tmp_path / "existing"
        existing.mkdir()

        mkdir_private(existing / "a" / "b")

        assert windows.created == [
            (str(existing / "a"), True),
            (str(existing / "a" / "b"), True),
        ]

    def test_makedirs_private_creates_only_the_missing_folders_locked(
        self, tmp_path, windows
    ):
        existing = tmp_path / "existing"
        existing.mkdir()

        makedirs_private(str(existing / "a" / "b"))
        makedirs_private(str(existing / "a" / "b"))

        assert windows.created == [
            (str(existing / "a"), True),
            (str(existing / "a" / "b"), True),
        ]

    @pytest.mark.parametrize("maker", [mkdir_private, makedirs_private])
    def test_a_folder_another_process_made_first_is_left_alone(
        self, tmp_path, monkeypatch, windows, maker
    ):
        """R2-6: missing at the probe, made by somebody else before our create."""
        target = tmp_path / "raced"
        real_create = trusted_sqlite._windows_create_private_directory

        def made_by_someone_else(path):
            if str(path) == str(target):
                os.mkdir(target)
                raise FileExistsError(errno.EEXIST, "exists", str(target))
            return real_create(path)

        monkeypatch.setattr(
            trusted_sqlite, "_windows_create_private_directory", made_by_someone_else
        )

        maker(target if maker is mkdir_private else str(target))

        assert target.is_dir()
        assert windows.created == []

    def test_makedirs_private_still_raises_on_an_existing_file(self, tmp_path, windows):
        blocker = tmp_path / "file"
        blocker.write_text("")

        with pytest.raises(FileExistsError):
            makedirs_private(str(blocker))
        assert windows.created == []

    @pytest.mark.skipif(
        os.name == "nt",
        reason="asserts the POSIX branch never reaches the Windows creators; on "
        "Windows reaching them is the point, and the real-ACL class covers it",
    )
    def test_nothing_windows_runs_off_windows(self, tmp_path, monkeypatch):
        def boom(*_args, **_kwargs):
            raise AssertionError("a Windows creator ran on POSIX")

        monkeypatch.setattr(trusted_sqlite, "_windows_create_private_file", boom)
        monkeypatch.setattr(trusted_sqlite, "_windows_create_private_directory", boom)

        mkdir_private(Path(tmp_path / "x" / "y"))
        TrustedSQLiteLocation.open(
            str(tmp_path / "x" / "y" / "v.db"), create=True
        ).close()


def _touch(path: str) -> None:
    """Create an empty file; ``Path`` cannot be built while ``os.name`` is faked."""
    with open(path, "wb"):
        pass


class TestTheCreationPathsAreWellFormed:
    """The path barrier in front of ``os.open``/``mkdir`` (CodeQL path-injection)."""

    @pytest.mark.parametrize("bad", ["", "lib/vault\n.db", "lib/vault\x00.db"])
    def test_an_empty_or_control_character_path_is_refused(self, tmp_path, bad):
        with pytest.raises(OSError) as raised:
            trusted_sqlite._safe_resolved_path(str(tmp_path / bad) if bad else bad)
        assert raised.value.errno == errno.EINVAL

    @pytest.mark.skipif(os.name == "nt", reason="creates a POSIX symlink")
    def test_only_the_directory_is_resolved_never_the_final_name(self, tmp_path):
        """Resolving the leaf would follow the link O_NOFOLLOW exists to refuse."""
        real = tmp_path / "real"
        real.mkdir()
        via = tmp_path / "via"
        via.symlink_to(real, target_is_directory=True)
        (real / "leaf").symlink_to(tmp_path / "elsewhere")

        resolved = trusted_sqlite._safe_resolved_path(str(via / "leaf"))

        assert resolved == str(real / "leaf")
        with pytest.raises(FileExistsError):
            descriptor = trusted_sqlite.create_private_file(str(via / "leaf"))
            # Only reached if the refusal regressed; do not leak the file.
            os.close(descriptor)
        assert not (tmp_path / "elsewhere").exists()


class TestSidecarsAreLockedDown:
    """R2-3: a sidecar this account owns gets the owner-only DACL at open."""

    @pytest.fixture
    def hub(self, tmp_path, monkeypatch):
        directory = tmp_path / "lib"
        directory.mkdir()
        path = directory / "vault.db"
        path.touch()
        monkeypatch.setattr(os, "name", "nt")
        return path

    @pytest.mark.parametrize("suffix", trusted_sqlite._SIDECAR_SUFFIXES)
    def test_an_unprotected_sidecar_of_ours_is_locked_down(
        self, hub, monkeypatch, suffix
    ):
        sidecar = str(hub) + suffix
        _touch(sidecar)
        windows = install_fake_windows_security(monkeypatch)

        TrustedSQLiteLocation.open(str(hub)).close()

        assert windows.locked == [sidecar]

    def test_a_protected_sidecar_is_left_alone(self, hub, monkeypatch):
        sidecar = str(hub) + "-wal"
        _touch(sidecar)
        windows = install_fake_windows_security(
            monkeypatch, {sidecar: security(protected=True)}
        )

        TrustedSQLiteLocation.open(str(hub)).close()

        assert windows.locked == []

    def test_a_sidecar_another_account_owns_is_not_touched(self, hub, monkeypatch):
        sidecar = str(hub) + "-wal"
        _touch(sidecar)
        windows = install_fake_windows_security(
            monkeypatch, {sidecar: security(owner=SOMEONE_ELSE)}
        )

        TrustedSQLiteLocation.open(str(hub)).close()

        assert windows.locked == []

    def test_the_folder_and_the_database_are_never_rewritten(self, hub, monkeypatch):
        _touch(str(hub) + "-shm")
        windows = install_fake_windows_security(monkeypatch)

        guard = TrustedSQLiteLocation.open(str(hub))
        guard.verify_after_open()
        guard.close()

        assert set(windows.locked) == {str(hub) + "-shm"}


# --- The ctypes layer over a fake _win32(), on real ctypes buffers ---------


def _sid_bytes(sid: str) -> bytes:
    parts = sid.split("-")
    authority, subs = int(parts[2]), [int(p) for p in parts[3:]]
    return (
        struct.pack("<BB", int(parts[1]), len(subs))
        + authority.to_bytes(6, "big")
        + b"".join(struct.pack("<I", x) for x in subs)
    )


def _sid_string_at(address: int) -> str:
    count = ctypes.c_ubyte.from_address(address + 1).value
    authority = int.from_bytes(ctypes.string_at(address + 2, 6), "big")
    subs = struct.unpack(f"<{count}I", ctypes.string_at(address + 8, 4 * count))
    return f"S-1-{authority}" + "".join(f"-{x}" for x in subs)


def _ace_bytes(ace_type, flags, mask, sid, *, object_flags=None, size=None, extra=b""):
    body = struct.pack("<I", mask)
    if object_flags is not None:
        body += struct.pack("<I", object_flags)
        if object_flags & 1:
            body += b"\x11" * 16
        if object_flags & 2:
            body += b"\x22" * 16
    body += _sid_bytes(sid) + extra
    return struct.pack("<BBH", ace_type, flags, size or 4 + len(body)) + body


class _FakeWin32:
    """Just enough advapi32/kernel32 over real memory, for one test."""

    def __init__(self, aces=(), *, code=0, owner=ME, persistent=True, serial=0x1234):
        self.keep = [ctypes.create_string_buffer(a, len(a)) for a in aces]
        self.aces = list(self.keep)
        self.owner = ctypes.create_string_buffer(_sid_bytes(owner)) if owner else None
        self.code = code
        self.persistent = persistent
        self.serial = serial
        self.root = "C:\\"
        self.set_calls = []
        self.added = []
        self.set_code = 0
        self.protected = False
        self.acls = {}
        self.descriptors = {}
        self.created = []
        self.create_code = 0
        self.opened = []
        self.opened_path = None
        self.file_info = {}

    def namespace(self):
        return types.SimpleNamespace(
            **{
                name: getattr(self, name)
                for name in dir(self)
                if name[0].isupper() and callable(getattr(self, name))
            }
        )

    def GetVolumePathNameW(self, path, buffer, size):
        buffer.value = self.root
        return 1

    def GetVolumeInformationW(self, root, a, b, serial, c, flags, d, e):
        serial._obj.value = self.serial
        flags._obj.value = 8 if self.persistent else 0
        return 1

    def GetNamedSecurityInfoW(self, path, kind, info, owner, group, dacl, sacl, sd):
        if self.code:
            return self.code
        owner._obj.value = ctypes.addressof(self.owner) if self.owner else None
        dacl._obj.value = 0x1000
        sd._obj.value = 0x2000
        return 0

    def GetAclInformation(self, dacl, info, size, cls):
        info._obj.AceCount = len(self.aces)
        return 1

    def GetAce(self, dacl, index, out):
        out._obj.value = ctypes.addressof(self.aces[index])
        return 1

    def IsValidSid(self, address):
        revision = ctypes.c_ubyte.from_address(address).value
        count = ctypes.c_ubyte.from_address(address + 1).value
        return int(revision == 1 and count <= 15)

    def GetLengthSid(self, address):
        return 8 + 4 * ctypes.c_ubyte.from_address(address + 1).value

    def ConvertSidToStringSidW(self, address, out):
        text = ctypes.create_unicode_buffer(_sid_string_at(address))
        self.keep.append(text)
        out._obj.value = ctypes.addressof(text)
        return 1

    def ConvertStringSidToSidW(self, sid, out):
        raw = ctypes.create_string_buffer(_sid_bytes(sid))
        self.keep.append(raw)
        out._obj.value = ctypes.addressof(raw)
        return 1

    def InitializeAcl(self, acl, size, revision):
        self.acl_size = size
        self.acls[ctypes.addressof(acl)] = []
        return 1

    def AddAccessAllowedAceEx(self, acl, revision, flags, mask, sid):
        entry = (flags, mask, _sid_string_at(sid))
        self.added.append(entry)
        self.acls[ctypes.addressof(acl)].append(entry)
        return 1

    def GetSecurityDescriptorControl(self, sd, control, revision):
        control._obj.value = 0x1000 if self.protected else 0
        return 1

    def InitializeSecurityDescriptor(self, sd, revision):
        self.descriptors[ctypes.addressof(sd)] = {"dacl": None, "control": 0}
        return 1

    def SetSecurityDescriptorDacl(self, sd, present, acl, defaulted):
        self.descriptors[ctypes.addressof(sd)]["dacl"] = ctypes.addressof(acl)
        return 1

    def SetSecurityDescriptorControl(self, sd, mask, bits):
        self.descriptors[ctypes.addressof(sd)]["control"] |= mask & bits
        return 1

    def _attributes(self, sa):
        """What a SECURITY_ATTRIBUTES pointer carries: (protected, ACEs) or None."""
        if sa is None:
            return None
        attributes = sa._obj
        assert attributes.nLength == ctypes.sizeof(attributes)
        descriptor = self.descriptors[attributes.lpSecurityDescriptor]
        return descriptor["control"] & 0x1000 == 0x1000, self.acls[descriptor["dacl"]]

    def CreateFileW(self, path, access, share, sa, disposition, flags, template):
        if disposition == 3:  # OPEN_EXISTING: the sidecar lock-down's handle
            self.opened.append((path, access, flags))
            self.opened_path = path
            return 777
        self.created.append((path, access, disposition, flags, self._attributes(sa)))
        if self.create_code:
            return trusted_sqlite._INVALID_HANDLE_VALUE
        return 4242

    def GetFileInformationByHandle(self, handle, out):
        """By default the truth about the opened file, from the real filesystem."""
        real = os.lstat(self.opened_path)
        info = out._obj
        info.dwFileAttributes = self.file_info.get("attributes", 0x80)
        info.nNumberOfLinks = self.file_info.get("links", real.st_nlink)
        index = self.file_info.get("index", real.st_ino)
        info.nFileIndexHigh = index >> 32
        info.nFileIndexLow = index & 0xFFFFFFFF
        info.dwVolumeSerialNumber = self.file_info.get(
            "serial", real.st_dev & 0xFFFFFFFF
        )
        return 1

    def GetSecurityInfo(self, handle, kind, info, owner, group, dacl, sacl, sd):
        owner._obj.value = ctypes.addressof(self.owner) if self.owner else None
        sd._obj.value = 0x2000
        return 0

    def SetSecurityInfo(self, handle, kind, info, owner, group, dacl, sacl):
        """Succeeding, the file then reads back as Windows would show it: only
        the owner-only entries, protected."""
        self.set_calls.append((handle, info))
        if not self.set_code:
            self.aces = [
                ctypes.create_string_buffer(entry, len(entry))
                for entry in (
                    _ace_bytes(0x0, 0, FULL_CONTROL, sid)
                    for sid in (ME, SYSTEM, ADMINISTRATORS)
                )
            ]
            self.keep.extend(self.aces)
            self.protected = True
        return self.set_code

    def CreateDirectoryW(self, path, sa):
        self.created.append((path, self._attributes(sa)))
        return 0 if self.create_code else 1

    def CloseHandle(self, handle):
        return 1

    def SetNamedSecurityInfoW(self, path, kind, info, owner, group, dacl, sacl):
        """Only for the round-3 code; nothing current may reach it."""
        self.set_calls.append((path, info))
        return self.set_code

    def LocalFree(self, pointer):
        return None


@pytest.fixture
def fake_win32(monkeypatch):
    """Install a ``_FakeWin32`` factory; the Windows-only ctypes helpers stubbed."""
    monkeypatch.setattr(
        ctypes, "FormatError", lambda code: f"message {code}", raising=False
    )
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 0, raising=False)
    monkeypatch.setattr(
        trusted_sqlite,
        "msvcrt",
        types.SimpleNamespace(open_osfhandle=lambda handle, flags: handle),
    )
    monkeypatch.setattr(trusted_sqlite, "_windows_user_sid", lambda: ME)
    monkeypatch.setattr(trusted_sqlite, "_notices_logged", set())

    def install(*args, **kwargs):
        fake = _FakeWin32(*args, **kwargs)
        monkeypatch.setattr(trusted_sqlite, "_win32", fake.namespace)
        return fake

    return install


WRITE = FILE_WRITE_DATA


class TestReaderOverAFakeApi:
    @pytest.mark.parametrize(
        "ace",
        [
            _ace_bytes(0x0, 0, WRITE, SOMEONE_ELSE),
            _ace_bytes(0x9, 0, WRITE, SOMEONE_ELSE, extra=b"artx" + b"\0" * 8),
            *[
                _ace_bytes(ace_type, 0, WRITE, SOMEONE_ELSE, object_flags=flags)
                for ace_type in (0x5, 0xB)
                for flags in (0, 1, 2, 3)
            ],
        ],
        ids=["allowed", "callback"]
        + [f"object{t:#x}-flags{f}" for t in (0x5, 0xB) for f in (0, 1, 2, 3)],
    )
    def test_every_allow_layout_yields_its_trustee(self, fake_win32, ace):
        fake_win32([ace])

        found = trusted_sqlite._read_windows_security("C:\\lib\\vault.db")

        assert found.dacl[0].sid == SOMEONE_ELSE
        assert found.dacl[0].mask == WRITE
        assert found.owner_sid == ME
        assert found.volume_serial == 0x1234
        assert not found.is_volume_root

    @pytest.mark.parametrize("ace_type", [0x1, 0x4])
    def test_deny_and_unknown_types_carry_no_trustee(self, fake_win32, ace_type):
        fake_win32([_ace_bytes(ace_type, 0, WRITE, SOMEONE_ELSE)])

        assert trusted_sqlite._read_windows_security("C:\\v.db").dacl[0].sid is None

    def test_a_sid_running_past_its_ace_is_not_read(self, fake_win32):
        """F6: AceSize says 12 bytes; the SID would be read from beyond it."""
        fake_win32([_ace_bytes(0x0, 0, WRITE, SOMEONE_ELSE, size=12)])

        (ace,) = trusted_sqlite._read_windows_security("C:\\v.db").dacl
        assert ace.sid is None
        assert classify_windows_acl(
            ME, ME, (ace,), write_rights=WINDOWS_WRITE_RIGHTS["file"]
        ).writers

    def test_a_sid_whose_reported_length_overruns_the_ace_is_not_read(self, fake_win32):
        """F6, the ``GetLengthSid`` bound: behind the header arithmetic it is
        defence in depth, so the API is made to disagree with the bytes."""
        fake = fake_win32([_ace_bytes(0x0, 0, WRITE, SOMEONE_ELSE)])
        fake.GetLengthSid = lambda address: 4096

        (ace,) = trusted_sqlite._read_windows_security("C:\\v.db").dacl
        assert ace.sid is None

    def test_a_volume_root_is_recognised(self, fake_win32):
        fake_win32([])

        assert trusted_sqlite._read_windows_security("C:\\").is_volume_root

    @pytest.mark.parametrize("code", [2, 3])
    def test_a_vanished_path_is_file_not_found(self, fake_win32, code):
        fake_win32([], code=code)

        with pytest.raises(TrustedSQLiteLocationError) as raised:
            trusted_sqlite._read_windows_security("C:\\v.db-journal")
        assert isinstance(raised.value.__cause__, FileNotFoundError)

    @pytest.mark.parametrize("code", [5, 32, 50, 1920])
    def test_any_other_failure_is_unverifiable_and_named(self, fake_win32, code):
        fake_win32([], code=code, serial=99)

        with pytest.raises(trusted_sqlite._UnverifiableWindowsAcl) as raised:
            trusted_sqlite._read_windows_security("C:\\v.db")
        assert f"GetNamedSecurityInfoW failed with Windows error {code}" in str(
            raised.value
        )
        assert "FAT" not in str(raised.value)
        assert raised.value.volume_serial == 99

    def test_a_descriptor_without_an_owner_is_unverifiable(self, fake_win32):
        fake_win32([_ace_bytes(0x0, 0, FULL_CONTROL, EVERYONE)], owner=None)

        with pytest.raises(trusted_sqlite._UnverifiableWindowsAcl, match="no owner"):
            trusted_sqlite._read_windows_security("C:\\v.db")

    def test_a_volume_without_acls_is_unverifiable_and_says_so(self, fake_win32):
        fake_win32([], persistent=False)

        with pytest.raises(
            trusted_sqlite._UnverifiableWindowsAcl, match="FILE_PERSISTENT_ACLS"
        ):
            trusted_sqlite._read_windows_security("E:\\v.db")

    def test_access_denied_warns_through_the_check_and_does_not_refuse(
        self, fake_win32, caplog
    ):
        fake_win32([], code=5)

        trusted_sqlite._check_windows_acl("C:\\lib", kind="directory")

        assert _warned(caplog, "Windows error 5")
        assert not _warned(caplog, "FAT32")


OWNER_ONLY = [
    (0x001F01FF, ME),
    (0x001F01FF, SYSTEM),
    (0x001F01FF, ADMINISTRATORS),
]


class TestCreationOverAFakeApi:
    """R2-5: the security attributes passed at creation carry the protected DACL."""

    def test_a_directory_is_created_protected_and_inheritable(self, fake_win32):
        fake = fake_win32()

        trusted_sqlite._windows_create_private_directory("C:\\lib")

        ((path, (protected, aces)),) = fake.created
        assert path == "C:\\lib"
        assert protected
        assert aces == [(0x03, mask, sid) for mask, sid in OWNER_ONLY]
        assert fake.acl_size % 4 == 0
        assert fake.acl_size >= 8 + sum(8 + len(_sid_bytes(s)) for _m, s in OWNER_ONLY)

    def test_a_file_is_created_new_without_following_a_link(self, fake_win32):
        fake = fake_win32()

        fd = trusted_sqlite._windows_create_private_file("C:\\lib\\vault.db")

        assert fd == 4242
        ((path, access, disposition, flags, (protected, aces)),) = fake.created
        assert path == "C:\\lib\\vault.db"
        assert access == 0xC0000000  # GENERIC_READ | GENERIC_WRITE
        assert disposition == 1  # CREATE_NEW
        assert flags & 0x00200000  # FILE_FLAG_OPEN_REPARSE_POINT
        assert protected
        assert aces == [(0, mask, sid) for mask, sid in OWNER_ONLY]

    def test_an_existing_name_is_file_exists(self, fake_win32, monkeypatch):
        fake = fake_win32()
        fake.create_code = 80
        monkeypatch.setattr(ctypes, "get_last_error", lambda: 80, raising=False)

        with pytest.raises(FileExistsError):
            trusted_sqlite._windows_create_private_file("C:\\lib\\vault.db")

    def test_a_volume_without_acls_creates_without_attributes(self, fake_win32, caplog):
        fake = fake_win32(persistent=False)

        trusted_sqlite._windows_create_private_directory("E:\\lib")

        assert fake.created == [("E:\\lib", None)]
        assert not _warned(caplog, "E:\\lib")

    def test_a_descriptor_that_cannot_be_built_is_a_warning(self, fake_win32, caplog):
        fake = fake_win32()
        fake.InitializeAcl = lambda acl, size, revision: 0

        trusted_sqlite._windows_create_private_directory("C:\\lib")

        assert fake.created == [("C:\\lib", None)]
        assert _warned(caplog, "Could not prepare an owner-only permission list")
        assert _warned(caplog, "InitializeAcl failed with Windows error")


class TestSidecarLockDownOverAFakeApi:
    """R3-2: the sidecar is re-DACLed through a handle on the checked file only.

    Driven through ``_validate_file`` (what the sidecar loop calls) on real
    files, with ``os.name`` forced to ``nt`` and the fake API answering
    ``GetFileInformationByHandle`` from the real filesystem.
    """

    @pytest.fixture
    def sidecar(self, tmp_path, fake_win32, monkeypatch):
        path = tmp_path / "vault.db-journal"
        path.write_bytes(b"")
        fake = fake_win32([_ace_bytes(0x0, 0, FULL_CONTROL, ME)])
        monkeypatch.setattr(os, "name", "nt")
        return str(path), fake

    def test_our_unprotected_sidecar_is_locked_down_on_its_handle(self, sidecar):
        path, fake = sidecar

        trusted_sqlite._validate_file(path, private=False, sidecar=True)

        ((opened, access, flags),) = fake.opened
        assert opened == path
        assert access == 0x00060000  # READ_CONTROL | WRITE_DAC
        assert flags & 0x00200000 and flags & 0x02000000
        assert fake.set_calls == [(777, 0x80000004)]  # the handle, PROTECTED | DACL
        assert fake.added == [(0, mask, sid) for mask, sid in OWNER_ONLY]

    def test_a_hostile_entry_is_reported_before_the_sidecar_is_locked_down(
        self, tmp_path, fake_win32, monkeypatch, caplog
    ):
        """The Windows lane's finding: the lock-down ran first, stripped the
        Everyone entry, and the re-read found nothing to report - a hostile
        ACL fixed out of sight. What the sidecar arrived with is reported, then
        it is locked down."""
        path = str(tmp_path / "hub.db-wal")
        with open(path, "wb") as handle:
            handle.write(b"hostile frames")
        fake = fake_win32(
            [
                _ace_bytes(0x0, 0, FULL_CONTROL, ME),
                _ace_bytes(0x0, 0, FILE_WRITE_DATA, EVERYONE),
            ]
        )
        monkeypatch.setattr(os, "name", "nt")

        trusted_sqlite._validate_file(path, private=True, sidecar=True)

        assert _warned(caplog, f"{path} can also be changed by Everyone")
        assert fake.set_calls == [(777, 0x80000004)]

    def test_a_hard_link_to_another_file_is_not_rewritten(
        self, tmp_path, fake_win32, monkeypatch, caplog
    ):
        """The reviewer's reproduction: a planted ``-journal`` hard-linked to a
        photo of the user's. Rewriting it would rewrite the photo's DACL."""
        photo = tmp_path / "family-shared.jpg"
        photo.write_bytes(b"jpeg")
        journal = tmp_path / "vault.db-journal"
        os.link(photo, journal)
        fake = fake_win32([_ace_bytes(0x0, 0, FULL_CONTROL, ME)])
        monkeypatch.setattr(os, "name", "nt")

        trusted_sqlite._validate_file(str(journal), private=False, sidecar=True)

        assert fake.set_calls == []
        assert _warned(caplog, "it has 2 names")

    @pytest.mark.parametrize("attributes", [0x10, 0x400], ids=["directory", "reparse"])
    def test_a_directory_or_reparse_point_is_not_rewritten(
        self, sidecar, attributes, caplog
    ):
        path, fake = sidecar
        fake.file_info["attributes"] = attributes

        trusted_sqlite._validate_file(path, private=False, sidecar=True)

        assert fake.set_calls == []
        assert _warned(caplog, "not a regular file")

    @pytest.mark.parametrize("field", ["index", "serial"])
    def test_a_file_swapped_after_the_check_is_not_rewritten(
        self, sidecar, field, caplog
    ):
        path, fake = sidecar
        fake.file_info[field] = 0xDEAD

        trusted_sqlite._validate_file(path, private=False, sidecar=True)

        assert fake.set_calls == []
        assert _warned(caplog, "replaced while it was being checked")

    def test_a_sidecar_the_handle_shows_another_account_owns_is_not_rewritten(
        self, sidecar, monkeypatch
    ):
        path, fake = sidecar
        foreign = ctypes.create_string_buffer(_sid_bytes(SOMEONE_ELSE))
        fake.keep.append(foreign)

        def foreign_owner(handle, kind, info, owner, group, dacl, sacl, sd):
            owner._obj.value = ctypes.addressof(foreign)
            sd._obj.value = 0x2000
            return 0

        fake.GetSecurityInfo = foreign_owner

        trusted_sqlite._validate_file(path, private=False, sidecar=True)

        assert fake.set_calls == []

    def test_the_reader_reports_a_protected_descriptor(self, fake_win32):
        fake = fake_win32([])
        fake.protected = True

        assert trusted_sqlite._read_windows_security("C:\\v.db-wal").dacl_protected

    def test_a_lock_down_failure_is_a_warning_with_path_and_code(self, sidecar, caplog):
        path, fake = sidecar
        fake.set_code = 5

        trusted_sqlite._validate_file(path, private=False, sidecar=True)

        assert _warned(caplog, f"Did not restrict SQLite sidecar {path}")
        assert _warned(caplog, "SetSecurityInfo failed with Windows error 5")


def _plant(planted: str, target: str) -> bool:
    """Put the attacker's file at *target* the only ways an account can.

    Where no ``-wal`` exists yet it is a plain rename, on every platform. Where
    SQLite already holds one open, POSIX lets it be replaced (SQLite keeps
    reading the unlinked original); Windows refuses with ``ERROR_ACCESS_DENIED``,
    because SQLite opens it without ``FILE_SHARE_DELETE`` - that form of the
    attack does not exist there, and the refusal is the outcome. Returns
    whether the file was planted.
    """
    try:
        os.replace(planted, target)
    except PermissionError:
        if os.name != "nt":
            raise
        return False
    return True


class TestTheLoginImportRequiresATrustedOwner:
    """R3-1: ``require_trusted_owner`` refuses inside the file validation."""

    @pytest.fixture
    def vault(self, tmp_path, monkeypatch):
        directory = tmp_path / "lib"
        directory.mkdir(mode=0o700)
        path = str(directory / "vault.db")
        sqlite3.connect(path).close()
        monkeypatch.setattr(os, "name", "nt")
        return path

    def test_a_foreign_owned_vault_is_refused_only_when_asked(
        self, vault, monkeypatch, caplog
    ):
        install_fake_windows_security(
            monkeypatch, {vault: security(owner=SOMEONE_ELSE)}
        )

        with trusted_sqlite.checked_read_connection(vault):
            pass
        assert _warned(caplog, f"{vault} is owned by {SOMEONE_ELSE}")

        with pytest.raises(TrustedSQLiteLocationError, match="does not import a login"):
            with trusted_sqlite.checked_read_connection(
                vault, require_trusted_owner=True
            ):
                pass

    def test_a_foreign_owned_vault_is_refused_at_open_before_sqlite_runs(
        self, vault, monkeypatch
    ):
        install_fake_windows_security(
            monkeypatch, {vault: security(owner=SOMEONE_ELSE)}
        )

        with pytest.raises(TrustedSQLiteLocationError, match="does not import a login"):
            TrustedSQLiteLocation.open(vault, require_trusted_owner=True)

    def test_a_foreign_sidecar_that_appears_after_open_is_refused_at_verify(
        self, vault, monkeypatch
    ):
        install_fake_windows_security(monkeypatch)
        with TrustedSQLiteLocation.open(vault, require_trusted_owner=True) as guard:
            with open(vault + "-wal", "wb"):
                pass
            install_fake_windows_security(
                monkeypatch, {vault + "-wal": security(owner=SOMEONE_ELSE)}
            )
            with pytest.raises(TrustedSQLiteLocationError, match="does not import"):
                guard.verify_after_open()

    def test_an_owner_change_on_the_same_file_after_open_is_refused_at_verify(
        self, vault, monkeypatch
    ):
        """R4-2: the identity pin proves it is the same file, not the same
        owner. An account with WRITE_OWNER, or the take-ownership privilege,
        can make the approved file its own between open and verify; for a
        login import that owner change is itself the signal."""
        install_fake_windows_security(monkeypatch)
        with TrustedSQLiteLocation.open(vault, require_trusted_owner=True) as guard:
            install_fake_windows_security(
                monkeypatch, {vault: security(owner=SOMEONE_ELSE)}
            )
            with pytest.raises(TrustedSQLiteLocationError, match="does not import"):
                guard.verify_after_open()

    def test_a_wal_planted_after_the_check_is_not_what_the_import_reads(
        self, tmp_path, monkeypatch
    ):
        """R4-1, the reviewer's reproduction: SQLite opens a -wal at the first
        read, not at connect. One planted between ``verify_after_open`` and
        the caller's first row - the attacker's frames, owned by them - must
        not be what the import reads."""
        directory = tmp_path / "lib"
        directory.mkdir(mode=0o700)
        vault = str(directory / "vault.db")
        connection = sqlite3.connect(vault)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE user (name TEXT)")
        connection.execute("INSERT INTO user VALUES ('legit-owner')")
        connection.commit()
        connection.close()
        assert not os.path.exists(vault + "-wal")
        attacker = tmp_path / "attacker"
        attacker.mkdir(mode=0o700)
        shutil.copyfile(vault, attacker / "vault.db")
        theirs = sqlite3.connect(str(attacker / "vault.db"))
        theirs.execute("PRAGMA wal_autocheckpoint=0")
        theirs.execute("UPDATE user SET name = 'attacker'")
        theirs.commit()
        planted = str(tmp_path / "planted-wal")
        shutil.copyfile(str(attacker / "vault.db-wal"), planted)
        theirs.close()
        planted_inode = os.stat(planted).st_ino
        monkeypatch.setattr(os, "name", "nt")
        install_fake_windows_security(monkeypatch)
        monkeypatch.setattr(
            trusted_sqlite,
            "_read_windows_security",
            lambda path: security(
                owner=SOMEONE_ELSE if os.lstat(path).st_ino == planted_inode else ME
            ),
        )

        try:
            with trusted_sqlite.checked_read_connection(
                vault, require_trusted_owner=True
            ) as read:
                _plant(planted, vault + "-wal")
                row = read.execute("SELECT name FROM user").fetchone()
        except TrustedSQLiteLocationError:
            row = None

        assert row != ("attacker",)

    def test_our_own_vault_in_a_loose_folder_is_read(self, vault, monkeypatch):
        loose = security(
            dacl=profile_default_dacl() + (allow(EVERYONE, FULL_CONTROL, flags=0x03),)
        )
        install_fake_windows_security(
            monkeypatch, {vault: loose, os.path.dirname(vault): loose}
        )

        with trusted_sqlite.checked_read_connection(
            vault, require_trusted_owner=True
        ) as connection:
            assert connection.execute("SELECT 1").fetchone() == (1,)


def _icacls(*args: str) -> None:
    result = subprocess.run(
        ["icacls", *args], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, (
        f"icacls {' '.join(args)} failed: {result.stdout}{result.stderr}"
    )


INHERITED_ACE = 0x10


@pytest.mark.skipif(os.name != "nt", reason="reads real Windows ACLs")
class TestRealWindowsAcls:
    """The ctypes reader and writer against real NTFS files. Windows lane only."""

    def test_the_reader_sees_this_user_or_the_os_as_owner(self, tmp_path):
        found = trusted_sqlite._read_windows_security(str(tmp_path))

        user = trusted_sqlite._windows_user_sid()
        assert user.startswith("S-1-5-")
        assert found.owner_sid in {user, SYSTEM, ADMINISTRATORS}
        assert found.dacl is not None
        assert found.volume_serial is not None

    def test_a_fresh_temp_directory_opens_without_a_notice(self, tmp_path, caplog):
        directory = tmp_path / "hub"
        directory.mkdir()

        TrustedSQLiteLocation.open(
            str(directory / "hub.db"),
            private=True,
            create=True,
            trusted_root=str(directory),
        ).close()

        assert not [r for r in caplog.records if r.levelname == "WARNING"]

    def test_the_reader_parses_an_everyone_grant(self, tmp_path):
        directory = tmp_path / "loose"
        directory.mkdir()
        _icacls(str(directory), "/grant", "*S-1-1-0:(OI)(CI)W")

        found = trusted_sqlite._read_windows_security(str(directory))

        grants = [ace for ace in found.dacl if ace.sid == EVERYONE]
        assert grants, f"no Everyone ACE read back: {found.dacl}"
        assert any(ace.mask & FILE_WRITE_DATA for ace in grants)

    def test_a_hub_in_an_everyone_writable_folder_opens_locked_down(
        self, tmp_path, caplog
    ):
        directory = tmp_path / "hub"
        directory.mkdir()
        _icacls(str(directory), "/grant", "*S-1-1-0:(OI)(CI)W")

        with hub_db.HubDatabase(str(directory / "hub.db")):
            assert _warned(caplog, "Everyone")
            trusted = {trusted_sqlite._windows_user_sid(), SYSTEM, ADMINISTRATORS}
            found = trusted_sqlite._read_windows_security(
                os.path.realpath(directory / "hub.db")
            )
            assert found.dacl_protected
            assert {ace.sid for ace in found.dacl} <= trusted
            assert not any(ace.flags & INHERITED_ACE for ace in found.dacl)
            # R2-3: the first hub's WAL sidecars were created after its check
            # and inherited the folder's Everyone entry; the next open, while
            # they exist, locks them down.
            second = hub_db.HubDatabase(str(directory / "hub.db"))
            second.close()
            for suffix in ("-wal", "-shm"):
                sidecar = os.path.realpath(directory / "hub.db") + suffix
                assert os.path.exists(sidecar), sidecar
                locked = trusted_sqlite._read_windows_security(sidecar)
                assert locked.dacl_protected, sidecar
                assert {ace.sid for ace in locked.dacl} <= trusted, sidecar

    def test_a_folder_pixlstash_creates_is_protected_and_inheritable(self, tmp_path):
        target = tmp_path / "made" / "here"

        mkdir_private(target)

        for folder in (tmp_path / "made", target):
            found = trusted_sqlite._read_windows_security(str(folder))
            assert found.dacl_protected
            assert len(found.dacl) == 3, found.dacl
            assert all(ace.flags & 0x03 == 0x03 for ace in found.dacl)
            assert not any(ace.flags & INHERITED_ACE for ace in found.dacl)

    def test_a_hostile_sidecar_acl_is_reported(self, tmp_path, caplog):
        directory = tmp_path / "hub"
        directory.mkdir()
        path = directory / "hub.db"
        TrustedSQLiteLocation.open(
            str(path), private=True, create=True, trusted_root=str(directory)
        ).close()
        sidecar = directory / "hub.db-wal"
        sidecar.write_bytes(b"hostile frames")
        _icacls(str(sidecar), "/grant", "*S-1-1-0:W")

        TrustedSQLiteLocation.open(
            str(path), private=True, trusted_root=str(directory)
        ).close()

        assert _warned(caplog, os.path.realpath(sidecar))
