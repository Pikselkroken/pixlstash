"""The trusted-SQLite guard's Windows owner/DACL check.

Two halves, matching the code's split. The classifier is a pure function of
owner SID, user SID and ACE list, so its whole decision table runs here on the
Linux gate with synthetic ACLs, and the guard's wiring runs under a simulated
``nt`` with a fake reader. The ctypes reader can only be exercised against real
files, so the last class runs on the Windows lane only (``backend_windows`` in
``.github/workflows/ci.yml`` names this file).

Both directions throughout: a principal that can write must be refused, and the
default ACL of a user profile - and read-only grants to everyone - must pass,
because refusing a default install is how an earlier Windows check stopped the
server starting at all.
"""

import os
import subprocess

import pytest

from pixlstash import trusted_sqlite
from pixlstash.hub import db as hub_db
from pixlstash.trusted_sqlite import (
    TrustedSQLiteLocation,
    TrustedSQLiteLocationError,
    WINDOWS_WRITE_RIGHTS,
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


def _classify(dacl, *, kind, owner=ME):
    return classify_windows_acl(
        owner,
        ME,
        None if dacl is None else tuple(dacl),
        write_rights=WINDOWS_WRITE_RIGHTS[kind],
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
    def test_an_inherit_only_full_control_grant_is_ignored(self, kind):
        """It describes children, not this object (``C:\\``'s ``(OI)(CI)(IO)(M)``)."""
        dacl = profile_default_dacl() + (
            allow(EVERYONE, FULL_CONTROL, flags=INHERIT_ONLY | 0x03),
        )

        assert _classify(dacl, kind=kind).writers == ()

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


class TestClassifierRefuses:
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

    def test_an_allow_whose_sid_could_not_be_read_is_a_stranger(self):
        dacl = profile_default_dacl() + (WindowsAce(0x0, 0, FILE_WRITE_EA, None),)

        assert _classify(dacl, kind="file").writers != ()


class TestGuardOnSimulatedWindows:
    """The wiring: which objects are asked about, and what each verdict does.

    ``os.name`` is forced to ``nt`` and the ctypes reader replaced with
    vectors, so the namespace walk, the file check and the sidecar loop all run
    their Windows branch on this Linux gate.
    """

    @pytest.fixture
    def library(self, tmp_path, monkeypatch):
        directory = tmp_path / "library"
        directory.mkdir(mode=0o700)
        monkeypatch.setattr(os, "name", "nt")
        return directory

    def test_a_default_profile_namespace_opens_and_every_object_is_read(
        self, library, monkeypatch
    ):
        seen = install_fake_windows_security(monkeypatch)
        path = library / "hub.db"

        TrustedSQLiteLocation.open(
            str(path), private=True, create=True, trusted_root=str(library)
        ).close()

        assert str(library) in seen
        assert str(path) in seen
        assert os.path.dirname(str(library)) in seen

    def test_a_stranger_writable_directory_is_refused_with_the_fix(
        self, library, monkeypatch
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

        with pytest.raises(TrustedSQLiteLocationError) as raised:
            TrustedSQLiteLocation.open(str(library / "vault.db"), create=True)

        message = str(raised.value)
        assert str(library) in message
        assert "Everyone (S-1-1-0)" in message
        assert "/remove:g *S-1-1-0" in message
        # Refused before the file was created, never mind connected to.
        assert not (library / "vault.db").exists()

    def test_a_foreign_owned_file_is_refused(self, library, monkeypatch):
        path = library / "vault.db"
        path.touch()
        install_fake_windows_security(monkeypatch, {path: security(owner=SOMEONE_ELSE)})

        with pytest.raises(TrustedSQLiteLocationError, match="takeown"):
            TrustedSQLiteLocation.open(str(path))

    def test_an_ancestor_that_lets_a_stranger_rename_it_is_refused(
        self, library, monkeypatch
    ):
        """``D:\\Photos`` inheriting Authenticated Users Modify (``M`` has DELETE)."""
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

        with pytest.raises(TrustedSQLiteLocationError, match="Authenticated Users"):
            TrustedSQLiteLocation.open(str(library / "vault.db"), create=True)

    def test_a_system_drive_root_with_add_only_rights_still_opens(
        self, library, monkeypatch
    ):
        """The ``C:\\`` shape, applied to every ancestor above the library."""
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

    def test_an_unverifiable_volume_warns_naming_path_api_and_error(
        self, library, monkeypatch, caplog
    ):
        unverifiable = trusted_sqlite._UnverifiableWindowsAcl(
            "GetNamedSecurityInfoW", "Windows error 50 (not supported)"
        )
        install_fake_windows_security(monkeypatch, {library: unverifiable})
        monkeypatch.setattr(trusted_sqlite, "_unverifiable_reported", set())

        TrustedSQLiteLocation.open(str(library / "vault.db"), create=True).close()

        assert _warned(caplog, str(library))
        assert _warned(caplog, "GetNamedSecurityInfoW")
        assert _warned(caplog, "Windows error 50")

    def test_the_unverifiable_decision_is_one_switch(self, library, monkeypatch):
        """Flipping ``_accept_unverifiable_windows_acl`` to raise refuses the open."""
        install_fake_windows_security(
            monkeypatch,
            {
                library: trusted_sqlite._UnverifiableWindowsAcl(
                    "GetVolumeInformationW", "no FILE_PERSISTENT_ACLS"
                )
            },
        )

        def refuse(path, api, error):
            raise TrustedSQLiteLocationError(f"{path}: {api}: {error}")

        monkeypatch.setattr(trusted_sqlite, "_accept_unverifiable_windows_acl", refuse)

        with pytest.raises(TrustedSQLiteLocationError, match="GetVolumeInformationW"):
            TrustedSQLiteLocation.open(str(library / "vault.db"), create=True)


def _icacls(*args: str) -> None:
    result = subprocess.run(
        ["icacls", *args], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, (
        f"icacls {' '.join(args)} failed: {result.stdout}{result.stderr}"
    )


@pytest.mark.skipif(os.name != "nt", reason="reads real Windows ACLs")
class TestRealWindowsAcls:
    """The ctypes reader against real NTFS files. Windows lane only."""

    def test_the_reader_sees_this_user_or_the_os_as_owner(self, tmp_path):
        found = trusted_sqlite._read_windows_security(str(tmp_path))

        user = trusted_sqlite._windows_user_sid()
        assert user.startswith("S-1-5-")
        assert found.owner_sid in {user, SYSTEM, ADMINISTRATORS}
        assert found.dacl is not None

    def test_a_fresh_temp_directory_opens_without_a_warning(self, tmp_path, caplog):
        directory = tmp_path / "hub"
        directory.mkdir()

        TrustedSQLiteLocation.open(
            str(directory / "hub.db"),
            private=True,
            create=True,
            trusted_root=str(directory),
        ).close()

        assert not _warned(caplog, "Cannot verify")
        assert not _warned(caplog, "can be read by")

    def test_the_reader_parses_an_everyone_grant(self, tmp_path):
        directory = tmp_path / "loose"
        directory.mkdir()
        _icacls(str(directory), "/grant", "*S-1-1-0:(OI)(CI)W")

        found = trusted_sqlite._read_windows_security(str(directory))

        grants = [ace for ace in found.dacl if ace.sid == EVERYONE]
        assert grants, f"no Everyone ACE read back: {found.dacl}"
        assert any(ace.mask & FILE_WRITE_DATA for ace in grants)

    def test_an_everyone_writable_hub_directory_is_refused_before_connect(
        self, tmp_path, monkeypatch
    ):
        directory = tmp_path / "hub"
        directory.mkdir()
        _icacls(str(directory), "/grant", "*S-1-1-0:(OI)(CI)W")

        def no_connect(*_args, **_kwargs):
            raise AssertionError("sqlite3.connect ran before the refusal")

        monkeypatch.setattr(hub_db.sqlite3, "connect", no_connect)

        with pytest.raises(hub_db.HubPermissionError, match="Everyone"):
            hub_db.HubDatabase(str(directory / "hub.db"))

    def test_a_hostile_sidecar_acl_is_refused(self, tmp_path):
        directory = tmp_path / "hub"
        directory.mkdir()
        path = directory / "hub.db"
        TrustedSQLiteLocation.open(
            str(path), private=True, create=True, trusted_root=str(directory)
        ).close()
        sidecar = directory / "hub.db-wal"
        sidecar.write_bytes(b"hostile frames")
        _icacls(str(sidecar), "/grant", "*S-1-1-0:W")

        with pytest.raises(TrustedSQLiteLocationError) as raised:
            TrustedSQLiteLocation.open(
                str(path), private=True, trusted_root=str(directory)
            )

        assert os.path.realpath(sidecar) in str(raised.value)
