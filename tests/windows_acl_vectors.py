"""Synthetic Windows security descriptors for the trusted-SQLite guard.

``pixlstash.trusted_sqlite`` reads owner and DACL through ctypes on Windows and
hands them to the pure ``classify_windows_acl``. Tests that simulate ``nt`` on
Linux cannot reach advapi32, so they install a fake reader built from these
vectors instead. The SIDs are well-known principals plus invented machine SIDs
that identify nobody.
"""

from __future__ import annotations

import os

from pixlstash import trusted_sqlite
from pixlstash.trusted_sqlite import WindowsAce

EVERYONE = "S-1-1-0"
AUTHENTICATED_USERS = "S-1-5-11"
USERS = "S-1-5-32-545"
SYSTEM = "S-1-5-18"
ADMINISTRATORS = "S-1-5-32-544"
TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
CREATOR_OWNER = "S-1-3-0"
OWNER_RIGHTS = "S-1-3-4"
# Invented domain-style SIDs: this process's user, and somebody else.
ME = "S-1-5-21-1000000001-1000000002-1000000003-1001"
SOMEONE_ELSE = "S-1-5-21-1000000001-1000000002-1000000003-1002"

ALLOW = 0x0
DENY = 0x1
ALLOW_OBJECT = 0x5
ALLOW_CALLBACK = 0x9
INHERIT_ONLY = 0x08
CONTAINER_AND_OBJECT_INHERIT = 0x03

FULL_CONTROL = 0x001F01FF
# FILE_GENERIC_READ | FILE_GENERIC_EXECUTE: what "RX" grants.
READ_AND_EXECUTE = 0x001200A9


def allow(sid: str, mask: int = FULL_CONTROL, flags: int = 0) -> WindowsAce:
    return WindowsAce(ALLOW, flags, mask, sid)


def profile_default_dacl(user: str = ME) -> tuple[WindowsAce, ...]:
    """What ``%APPDATA%``, ``%LOCALAPPDATA%`` and profile folders carry."""
    return (
        allow(SYSTEM, flags=CONTAINER_AND_OBJECT_INHERIT),
        allow(ADMINISTRATORS, flags=CONTAINER_AND_OBJECT_INHERIT),
        allow(user, flags=CONTAINER_AND_OBJECT_INHERIT),
    )


class _Seen(list):
    """The paths read, plus ``created`` and ``locked`` (see the installer)."""

    created: list
    locked: list


def install_fake_windows_security(monkeypatch, overrides=None, *, user: str = ME):
    """Make the guard's Windows reader answer from vectors instead of advapi32.

    Every path gets the profile default, owned by *user*, unless *overrides*
    maps its string form to a ``_WindowsSecurity`` or to an exception to raise.
    The Windows creators are replaced by their POSIX equivalents, recording
    ``(path, is_directory)`` on the returned list's ``created`` attribute; the
    sidecar lock-down writer records the path on ``locked``; and the
    once-per-finding notice memory is cleared so each test sees its own
    warnings.

    Returns the list of paths read, so a test can show which objects the guard
    actually asked about.
    """
    overrides = {str(key): value for key, value in (overrides or {}).items()}
    seen = _Seen()

    def read(path: str):
        seen.append(path)
        answer = overrides.get(str(path))
        if isinstance(answer, BaseException):
            raise answer
        if answer is not None:
            return answer
        return trusted_sqlite._WindowsSecurity(
            owner_sid=user, dacl=profile_default_dacl(user)
        )

    def create_file(path: str) -> int:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        created.append((str(path), False))
        return fd

    def create_directory(path: str) -> None:
        os.mkdir(path, 0o700)
        created.append((str(path), True))

    def lock_down(path: str, expected, user_sid: str, volume_serial) -> bool:
        locked.append(str(path))
        return True

    created: list[tuple[str, bool]] = []
    locked: list[str] = []
    monkeypatch.setattr(trusted_sqlite, "_read_windows_security", read)
    monkeypatch.setattr(trusted_sqlite, "_windows_user_sid", lambda: user)
    monkeypatch.setattr(trusted_sqlite, "_windows_create_private_file", create_file)
    monkeypatch.setattr(
        trusted_sqlite, "_windows_create_private_directory", create_directory
    )
    monkeypatch.setattr(trusted_sqlite, "_lock_down_sidecar_by_handle", lock_down)
    monkeypatch.setattr(trusted_sqlite, "_notices_logged", set())
    seen.created = created
    seen.locked = locked
    return seen


def security(
    owner: str = ME, dacl=None, *, null_dacl: bool = False, protected: bool = False
):
    """A ``_WindowsSecurity`` for one path; ``null_dacl`` means no DACL at all."""
    if null_dacl:
        return trusted_sqlite._WindowsSecurity(
            owner_sid=owner, dacl=None, dacl_protected=protected
        )
    return trusted_sqlite._WindowsSecurity(
        owner_sid=owner,
        dacl=tuple(dacl if dacl is not None else profile_default_dacl()),
        dacl_protected=protected,
    )
