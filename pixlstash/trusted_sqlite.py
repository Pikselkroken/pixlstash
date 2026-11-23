"""Trusted-location guards for security-sensitive SQLite opens.

SQLite's main file cannot be safely redirected through ``/proc/self/fd`` when
WAL is enabled: its ``-wal`` and ``-shm`` siblings are derived from the path.
Instead, require a namespace in which another OS principal cannot replace the
main file or pre-position a sidecar, hold a no-follow guard, open SQLite by the
canonical path, and compare identities before doing decisive work.

**Mode bits warn; everything else refuses.** Ownership, symlinks, junctions and
non-regular files are refused outright (on Windows, ownership and every other
permission finding is reported instead; see "Windows" below). A loose mode (group/world-writable
directory or file, a credential file wider than 0600) is logged as a WARNING
with the ``chmod`` that fixes it, and the open goes ahead. Refusing on mode
alone took the app down twice for nobody's benefit (a 0775 directory under a
stock Ubuntu umask, then a 0755 library made by the Docker entrypoint), and a
loose mode on the owner's own files has never been an observed attack.

**What these checks actually cover: the moment of open, not "the database".**
``TrustedSQLiteLocation.open`` validates the main file and any pre-existing
sidecar before SQLite touches the path, and ``verify_after_open`` re-checks the
main file's identity and the sidecars **at one instant, on one connection**.
Then both return, and everything after that happens by path, unguarded:

* the hub closes the guard fd (``hub/db.py``, the ``finally`` after
  ``verify_after_open``) before ``_configure()`` issues its first PRAGMA, and
  SQLite re-opens ``-wal``/``-shm`` by name whenever it needs them;
* the vault engine's pool opens up to 15 further connections
  (``database.py``: QueuePool size 5 + max_overflow 10) by path over the
  process lifetime; ``verify_after_open`` runs on the first connection only.

Neither window can be closed in Python (``sqlite3`` accepts no pinned dirfd),
so tampering that begins after the guard returns is excluded only by the
directory permissions job 1 established, not by anything here.

**Who this defends against, and who it does not.** The actor is a *different* OS
principal: another account that can reach a shared or badly-permissioned
directory and substitute the database or one of its sidecars. That is what
``_validate_namespace`` excludes, and it is the only actor the checks here can
meaningfully stop.

A *same-uid* attacker is explicitly out of scope, and not because it would be
hard: the databases are mode 600 owned by this uid, so a same-uid process can
already open, read and rewrite them directly, with no race to win and no guard
to defeat. Anything it could achieve by racing an open, it can achieve more
simply by editing the file. The multi-library plan §8 says the same thing from
the product side, classifying this lane "Severity LOW single-owner" and naming
its real concerns as removable media, network shares and replaced symlinks, all
of which change ``st_dev``/``st_ino`` and are caught here.

State the actor before adding a check. A control justified by an actor outside
the threat model reads as free protection and is not: the parent-directory
timestamp comparison bought same-uid swap detection nobody needed and refused
roughly a fifth of concurrent opens, because SQLite creating our own WAL is
indistinguishable from tampering when you are watching a directory's mtime.

The same rule cuts the other way, and cost a startup: ``mode & 0o022`` is a
*proxy* for "another principal can write here", and for the group bit the proxy
is wrong wherever the group is the owner's own. Debian and Ubuntu give every
account a group of its own and default to umask 002, so a directory this app
created before it started passing 0700 is 0775 with a one-member group - no
other principal at all, and the blanket test refused it and exited 1 with no way
back except a manual ``chmod``. ``_is_private_group`` names the actor instead of
the bit: group-write is not even warned about when the group is the owner's
own, same-named, and has no other member, on a directory this user owns.
World-write and root-owned group-writable ancestors warn, and the two limits of
the group answer - supplementary members only, and NSS resolved in this process
- are recorded as accepted risk beside W17 in ``docs/backend_architecture.md``
§13.

**Windows: what PixlStash creates is locked down; what it finds is reported.**
``st_mode`` and ``st_uid`` are synthesised on Windows (an ordinary file reads
0o666 and ``st_uid`` is always 0), so the mode argument to ``os.open`` and
``mkdir`` protects nothing there. Two things stand in for it:

* **Creation.** When PixlStash itself creates a vault, the hub, or a folder
  through ``mkdir_private``/``makedirs_private``,
  ``restrict_new_path_to_owner`` gives it a *protected* DACL (inheritance from
  the parent switched off) granting full control to this account, ``SYSTEM``
  and ``BUILTIN\\Administrators`` only, with ``(OI)(CI)`` on folders so the
  ``-wal``/``-shm``/``-journal`` SQLite creates later inherit the same three
  entries. Only ever on something this process has just created; an existing
  file or folder keeps the permissions its owner gave it. A failure is a
  WARNING naming the path and the Windows error.
* **Opening.** Every ``TrustedSQLiteLocation`` open reads each object's owner
  SID and DACL (``_read_windows_security``, a thin ctypes reader over
  advapi32/kernel32) and ``classify_windows_acl`` - a pure function of owner
  SID, user SID and ACE list, tested on the Linux gate with synthetic ACLs -
  decides who besides this account and the OS could write it. Every finding
  is a WARNING that names the cause and the ``icacls`` or ``takeown`` command
  that changes it, and the open goes ahead: where a library lives is the
  owner's choice.

What counts as "could write", per object, mirroring the POSIX walk:

* **Owner**: anyone but this account, ``SYSTEM``, ``BUILTIN\\Administrators``
  or ``NT SERVICE\\TrustedInstaller`` (the analogue of ``st_uid in (uid, 0)``).
  Usual for a drive formatted on another computer.
* **The directory holding the database**: ``FILE_ADD_FILE``,
  ``FILE_ADD_SUBDIRECTORY``, ``FILE_DELETE_CHILD``, ``DELETE``, ``WRITE_DAC``,
  ``WRITE_OWNER``, ``GENERIC_WRITE``, ``GENERIC_ALL``, plus any object-inherit
  ACE (inherit-only or not) granting a file-write right, because the sidecars
  SQLite creates later inherit it.
* **Ancestors**: only rights that remove or rename an *existing* entry or take
  the ACL over (``FILE_DELETE_CHILD``, ``DELETE``, ``WRITE_DAC``,
  ``WRITE_OWNER``, ``GENERIC_ALL``). Add-only rights are the Windows form of the
  sticky bit: ``C:\\`` grants Authenticated Users ``FILE_ADD_SUBDIRECTORY`` on
  itself, which lets them create ``C:\\Other`` and never replace ``C:\\Users``.
  ``DELETE`` on a volume root grants nothing (a root cannot be renamed), and no
  ``icacls`` or ``takeown`` command is ever suggested for a drive root.
* **The file and every sidecar**: ``FILE_WRITE_DATA``, ``FILE_APPEND_DATA``,
  ``DELETE``, ``WRITE_DAC``, ``WRITE_OWNER``, ``GENERIC_WRITE``,
  ``GENERIC_ALL``. A credential file (``private=True``) another account can
  read is reported too, as a POSIX mode wider than 0600 is.

Groups the user belongs to (``Users``, ``Authenticated Users``, ``Everyone``)
count as other accounts, because other accounts belong to them too. The
default ACLs of ``%APPDATA%``, ``%LOCALAPPDATA%`` and the profile folders grant
only the user, ``SYSTEM`` and ``Administrators``, so a desktop install is
silent.

Conservative readings, each of which can only over-report: a deny ACE never
cancels an allow (ordering is where ACL checks go wrong); callback and object
allow ACEs count as if their condition held; an ACE type the reader does not
know, or one whose SID would run past the ACE's own size, is a stranger with
full control; a NULL DACL is Everyone with full control; a logon-session SID
(``S-1-5-5-x-y``) is another account. The ``CREATOR OWNER``/``CREATOR GROUP``
placeholders grant nothing (on inheritance they become whoever creates the
child, which for the sidecars is this process) and ``OWNER RIGHTS`` resolves to
the owner, reported separately.

**Paths whose permissions cannot be read.** A FAT32/exFAT volume keeps none
(``GetVolumeInformationW`` reports no ``FILE_PERSISTENT_ACLS``), a network
server may not report them, and ``GetNamedSecurityInfoW`` can fail on NTFS too
(access denied, a sharing violation, a descriptor with no owner).
``_accept_unverifiable_windows_acl`` logs each with its true cause - the API and
Windows error code, or the missing volume flag - and the open proceeds. That
function is the single switch: making it raise turns these into refusals.

Every Windows notice is logged once per volume serial, path and text, so a
different drive mounted at the same letter is reported again. What refuses on
Windows is what refuses everywhere: redirects (symlinks **and** junctions), a
non-regular file, an identity that changes across the open, and a path that
vanishes (a sidecar that vanishes is tolerated, as on POSIX).

Short reads outside a database's long-lived engine - the legacy-owner and
fingerprint checks before migration, and the registry's folder and
fingerprint readers - go through ``checked_read_connection``, which runs these
same checks before ``sqlite3.connect`` and ``verify_after_open`` before the
first row is read.

A ``private=True`` open also requires a mandatory, no-default ``trusted_root``:
the directory the caller derived the credential store's path from. The check is
containment - the canonical file must be inside that root. In correct code it
is tautologically true; the parameter exists so that *forgetting* it fails at
the first test run instead of silently opting out in production. It is not a
policy on where the owner may put the hub.

**Blast radius, where nothing is enforced** (a database PixlStash did not
create, sitting in a folder other accounts can write, or on a volume with no
permissions; or tampering after the guard returns):

* The vault is **authorization-bearing**. ``authz/membership.py`` answers
  "is this picture in that project?" out of the vault, so a substituted vault
  is a substituted ACL: a scoped share token can be widened to the whole
  library. The hub authenticates; the vault authorises.
* ``Picture.file_path`` is attacker-chosen after a substitution, and an
  absolute path is currently returned verbatim by
  ``image_utils.resolve_picture_path``. That reaches unattended file **deletes**
  (snapshot GFS retention, scrapheap purge), sidecar **writes** whose name and
  suffix come from the row, and file **reads** served over HTTP including the
  share route. Containing that resolver shrinks this risk on **every** platform
  and is tracked separately.
* Live credentials are not here: the password hash and token hashes are in the
  hub. The vault does hold ``guest_session.cookie_token`` and dormant
  ``user``/``usertoken`` tables the baseline still creates.

Controls that run on Windows whatever the volume: symlink **and junction**
rejection on every component (``_is_redirect``), the regular-file requirement on
the target and every sidecar, and the ``(st_dev, st_ino)`` identity match across
the open plus ``verify_after_open``. The record of what is accepted is
``docs/backend_architecture.md`` §13 ("Trusted SQLite locations"), not
``docs/reviews/``, which is gitignored.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import os
import shlex
import sqlite3
import stat
import types
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

try:  # POSIX-only; only ever consulted where os.geteuid() also exists.
    import grp
    import pwd
except ImportError:  # pragma: no cover - Windows
    grp = None  # type: ignore[assignment]
    pwd = None  # type: ignore[assignment]

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.sqlite_uri import sqlite_file_uri

logger = get_logger(__name__)


class TrustedSQLiteLocationError(RuntimeError):
    """A SQLite main file or its namespace cannot be trusted."""


_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _require_owned_directory(path: str, *, immediate: bool) -> os.stat_result:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise TrustedSQLiteLocationError(
            f"Could not inspect SQLite directory {path}: {exc}"
        ) from exc
    if not stat.S_ISDIR(info.st_mode):
        raise TrustedSQLiteLocationError(
            f"SQLite directory component {path} is not a real directory."
        )
    if os.name == "nt":
        # Owner and DACL stand in for st_uid and the mode bits, which Windows
        # synthesises (module docstring, "Windows: the same verdict").
        _check_windows_acl(path, kind="directory" if immediate else "ancestor")
        return info
    if not hasattr(os, "geteuid"):
        raise TrustedSQLiteLocationError(
            f"Cannot verify ownership of SQLite directory {path} on this platform."
        )
    uid = os.geteuid()
    if info.st_uid not in (uid, 0):
        raise TrustedSQLiteLocationError(
            f"SQLite directory {path} is owned by uid {info.st_uid}, not this "
            "user or root."
        )
    exposed = stat.S_IMODE(info.st_mode) & 0o022
    # `info.st_uid == uid`, not the `(uid, 0)` the ownership check above admits.
    # The tolerance is only ever about *this* user's own group - that is why the
    # helper is asked about `uid` rather than `info.st_uid`, which by itself
    # already refuses `root:root`, whose members are administrators rather than
    # one single owner. What the precondition adds is the odder shape:
    # `chown root:<this user's group>` with g+w, where the group name does match.
    # A directory this user does not own keeps the blanket refusal either way.
    if (
        exposed == stat.S_IWGRP
        and info.st_uid == uid
        and _is_private_group(uid, info.st_gid)
    ):
        logger.info(
            "SQLite directory %s is group-writable (gid %d), accepted because "
            "that group is this user's own and has no other member, so no other "
            "account can write it. Tighten it with %s if that is not intended.",
            path,
            info.st_gid,
            f"chmod g-w {shlex.quote(path)}",
        )
        exposed = 0
    # Mode bits warn rather than refuse: a loose mode on the owner's own
    # directory has never been an observed attack, and refusing it took the
    # app down on stock umasks and in Docker for nobody's benefit. Ownership,
    # symlink and type checks above still refuse.
    if exposed and (immediate or not (info.st_mode & stat.S_ISVTX)):
        logger.warning(
            "SQLite directory %s is group/world-writable; another account "
            "could replace the database or its WAL/SHM files. Tighten it with "
            "chmod g-w,o-w %s",
            path,
            shlex.quote(path),
        )
    return info


def _is_private_group(uid: int, gid: int) -> bool:
    """True when *gid* is *uid*'s own one-member group.

    ``mode & 0o020`` is only an exposure when somebody else is in the group, and
    on Debian, Ubuntu and every other distro that runs ``useradd -U`` nobody
    else is: each account gets a group of its own, named after it, and the
    default umask is 002. Every directory created before this code started
    passing 0700 explicitly is therefore 0775 with a group of exactly one
    member, which the blanket bit test read as "another account could replace
    the database" and refused - taking the server down at startup, for good, on
    a stock Linux install with a library from an earlier release.

    Deliberately narrow, and deliberately not ``pwd.getpwall()``:

    * The name must match the owner's login. That is what makes this the
      *private* group rather than any group that happens to be empty today, and
      it is the property an admin has to undo on purpose.
    * ``gr_mem`` carries **supplementary** members only, so an empty one is the
      default state of every private group rather than evidence of anything.
      What it cannot see is an account whose *primary* gid is this group, which
      ``pwd.getpwall()`` would - and which this deliberately does not call:
      ``getpwall`` is unreliable exactly where it would matter (SSSD defaults to
      ``enumerate = false``, so it answers "nobody" on the managed hosts that
      have real user directories) and slow where it works. That residue is a
      stated accepted risk, recorded beside W17 in
      ``docs/backend_architecture.md`` §13 rather than only here: it takes
      ``useradd -g <this user> <account>``, an administrator putting a second
      account into one user's own group, and the module docstring rates this
      whole lane LOW single-owner.

    Two further limits, for the same record. Names are resolved through *this*
    process's NSS while the writers come from the kernel's uid/gid on the
    filesystem, so a container or idmapped mount whose ``/etc/group`` disagrees
    with the host answers about a different group than the one that can write.
    And the caller asks only about a directory **this** user owns
    (``info.st_uid == uid``), so nothing here relaxes a root-owned ancestor.

    Any lookup failure returns False, so the caller refuses rather than trusts
    an answer it did not get. ``OverflowError`` is in that list because the two
    modules disagree about the same input: measured on CPython 3.12,
    ``grp.getgrgid(-2)`` raises ``OverflowError`` while ``pwd.getpwuid(-2)``
    raises ``KeyError``. A ``st_gid`` from the kernel is a ``gid_t`` and cannot
    be out of range, so this is unreachable from the caller - but a fail-closed
    promise that holds only for the inputs one caller happens to pass is not one.
    """
    if grp is None or pwd is None:
        return False
    try:
        owner = pwd.getpwuid(uid).pw_name
        group = grp.getgrgid(gid)
    except (KeyError, OSError, OverflowError) as exc:
        logger.warning(
            "Could not resolve uid %d / gid %d while checking whether a "
            "group-writable SQLite directory is exposed (%s); treating the "
            "group as shared.",
            uid,
            gid,
            exc,
        )
        return False
    return group.gr_name == owner and not set(group.gr_mem) - {owner}


# --- Windows: owner and DACL, read natively -------------------------------
#
# Access-mask bits from winnt.h. On a directory FILE_WRITE_DATA is
# FILE_ADD_FILE and FILE_APPEND_DATA is FILE_ADD_SUBDIRECTORY (same bits).
_FILE_READ_DATA = 0x0001
_FILE_WRITE_DATA = 0x0002
_FILE_ADD_FILE = 0x0002
_FILE_APPEND_DATA = 0x0004
_FILE_ADD_SUBDIRECTORY = 0x0004
_FILE_DELETE_CHILD = 0x0040
_DELETE = 0x00010000
_WRITE_DAC = 0x00040000
_WRITE_OWNER = 0x00080000
_GENERIC_ALL = 0x10000000
_GENERIC_WRITE = 0x40000000
_GENERIC_READ = 0x80000000

# Rights that hand the object to whoever holds them, whatever it is.
_TAKEOVER_RIGHTS = _DELETE | _WRITE_DAC | _WRITE_OWNER | _GENERIC_ALL
# The directory that holds the database: adding an entry is enough to
# pre-position a sidecar, removing or renaming one is enough to substitute.
_DIRECTORY_WRITE_RIGHTS = (
    _FILE_ADD_FILE
    | _FILE_ADD_SUBDIRECTORY
    | _FILE_DELETE_CHILD
    | _GENERIC_WRITE
    | _TAKEOVER_RIGHTS
)
# Above the immediate parent only replacing an existing entry matters; add-only
# rights are the sticky bit. GENERIC_WRITE maps to FILE_GENERIC_WRITE, which
# carries no FILE_DELETE_CHILD and no DELETE, so it is add-only as well.
_ANCESTOR_WRITE_RIGHTS = _FILE_DELETE_CHILD | _TAKEOVER_RIGHTS
_FILE_WRITE_RIGHTS = (
    _FILE_WRITE_DATA | _FILE_APPEND_DATA | _GENERIC_WRITE | _TAKEOVER_RIGHTS
)
_FILE_READ_RIGHTS = _FILE_READ_DATA | _GENERIC_READ | _GENERIC_ALL

WINDOWS_WRITE_RIGHTS = {
    "directory": _DIRECTORY_WRITE_RIGHTS,
    "ancestor": _ANCESTOR_WRITE_RIGHTS,
    "file": _FILE_WRITE_RIGHTS,
}
# What an object-inherit ACE on the database's own directory hands every file
# created in it later - the -wal, -shm and -journal SQLite creates after the
# check. Only that directory's inheritance reaches them.
WINDOWS_INHERITED_WRITE_RIGHTS = {
    "directory": _FILE_WRITE_RIGHTS,
    "ancestor": 0,
    "file": 0,
}

# ACE types (winnt.h). Only the allow family is parsed for a trustee; denies
# are deliberately never subtracted (module docstring).
_ACCESS_ALLOWED_ACE_TYPE = 0x0
_ACCESS_DENIED_ACE_TYPE = 0x1
_ACCESS_ALLOWED_OBJECT_ACE_TYPE = 0x5
_ACCESS_DENIED_OBJECT_ACE_TYPE = 0x6
_ACCESS_ALLOWED_CALLBACK_ACE_TYPE = 0x9
_ACCESS_DENIED_CALLBACK_ACE_TYPE = 0xA
_ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE = 0xB
_ACCESS_DENIED_CALLBACK_OBJECT_ACE_TYPE = 0xC
_ALLOW_ACE_TYPES = frozenset(
    {
        _ACCESS_ALLOWED_ACE_TYPE,
        _ACCESS_ALLOWED_OBJECT_ACE_TYPE,
        _ACCESS_ALLOWED_CALLBACK_ACE_TYPE,
        _ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE,
    }
)
_DENY_ACE_TYPES = frozenset(
    {
        _ACCESS_DENIED_ACE_TYPE,
        _ACCESS_DENIED_OBJECT_ACE_TYPE,
        _ACCESS_DENIED_CALLBACK_ACE_TYPE,
        _ACCESS_DENIED_CALLBACK_OBJECT_ACE_TYPE,
    }
)
_OBJECT_ACE_TYPES = frozenset(
    {
        _ACCESS_ALLOWED_OBJECT_ACE_TYPE,
        _ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE,
    }
)
_OBJECT_INHERIT_ACE = 0x01
_CONTAINER_INHERIT_ACE = 0x02
_INHERIT_ONLY_ACE = 0x08

_SYSTEM_SID = "S-1-5-18"
_ADMINISTRATORS_SID = "S-1-5-32-544"
_TRUSTED_INSTALLER_SID = (
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
)
# The analogue of root: principals that own the OS and can already do anything.
_TRUSTED_OS_SIDS = frozenset({_SYSTEM_SID, _ADMINISTRATORS_SID, _TRUSTED_INSTALLER_SID})
# Placeholders that grant nothing to a stranger: no token carries CREATOR OWNER
# / CREATOR GROUP (on inheritance they become whoever creates the child, which
# for SQLite's sidecars is this process), and OWNER RIGHTS resolves to the
# owner, which the owner check reports on separately.
_PLACEHOLDER_SIDS = frozenset({"S-1-3-0", "S-1-3-1", "S-1-3-4"})
_EVERYONE_SID = "S-1-1-0"

_WELL_KNOWN_SID_NAMES = {
    _EVERYONE_SID: "Everyone",
    "S-1-5-11": "Authenticated Users",
    "S-1-5-32-545": "BUILTIN\\Users",
    "S-1-5-32-546": "BUILTIN\\Guests",
    "S-1-5-4": "INTERACTIVE",
    "S-1-5-2": "NETWORK",
    "S-1-5-7": "ANONYMOUS LOGON",
    "S-1-5-19": "LOCAL SERVICE",
    "S-1-5-20": "NETWORK SERVICE",
    "S-1-15-2-1": "ALL APPLICATION PACKAGES",
    _SYSTEM_SID: "SYSTEM",
    _ADMINISTRATORS_SID: "BUILTIN\\Administrators",
    _TRUSTED_INSTALLER_SID: "NT SERVICE\\TrustedInstaller",
}


@dataclass(frozen=True)
class WindowsAce:
    """One DACL entry as the classifier sees it.

    ``sid`` is the trustee in string form (``S-1-...``), or ``None`` when the
    reader could not locate one (an ACE type it does not know, or a malformed
    entry).
    """

    ace_type: int
    flags: int
    mask: int
    sid: str | None


@dataclass(frozen=True)
class WindowsAclVerdict:
    """What :func:`classify_windows_acl` found.

    ``writers`` and ``readers`` name untrusted trustees (SID strings, or an
    ``<...>`` description for an entry with no readable SID) holding a
    write-equivalent or read right. ``null_dacl`` records the one case where no
    ACE exists to name.
    """

    owner_trusted: bool
    writers: tuple[str, ...]
    readers: tuple[str, ...]
    null_dacl: bool = False


def _normalise_sid(sid: str) -> str:
    return sid.strip().upper()


def classify_windows_acl(
    owner_sid: str,
    user_sid: str,
    dacl: Sequence[WindowsAce] | None,
    *,
    write_rights: int,
    inherited_file_rights: int = 0,
    volume_root: bool = False,
) -> WindowsAclVerdict:
    """Decide who other than this user and the OS can write (and read) an object.

    Pure: no Windows API, so the Linux gate exercises every branch with
    synthetic ACLs. The ctypes layer only reads.

    Args:
        owner_sid: The object's owner, ``S-1-...``.
        user_sid: The process token's user SID.
        dacl: The DACL's ACEs in order, or ``None`` for a NULL DACL, which
            grants Everyone full control.
        write_rights: The access mask that counts as "can write" for this kind
            of object (``WINDOWS_WRITE_RIGHTS``).
        inherited_file_rights: Rights that, granted by an object-inherit ACE
            (inherit-only or not), reach files created in this directory later
            (``WINDOWS_INHERITED_WRITE_RIGHTS``). Zero for anything but the
            database's own directory.
        volume_root: The object is the root of its volume. A volume root
            cannot be renamed or deleted, so ``DELETE`` on it grants nothing.

    Returns:
        The verdict. The caller decides how to report it.

    Conservative by design, every choice can only over-report: deny ACEs never
    cancel an allow (order reasoning is the trap), callback and object allows
    count as if their condition held, and an ACE type outside the allow/deny
    families is treated as an unknown trustee with full control, because its
    SID cannot be located without knowing its layout. An inherit-only ACE does
    not apply to the object it sits on, so it counts only through
    *inherited_file_rights*.
    """
    if volume_root:
        write_rights &= ~_DELETE
    trusted = {_normalise_sid(user_sid)} | _TRUSTED_OS_SIDS
    owner_trusted = _normalise_sid(owner_sid) in trusted
    if dacl is None:
        return WindowsAclVerdict(
            owner_trusted=owner_trusted,
            writers=(_EVERYONE_SID,),
            readers=(_EVERYONE_SID,),
            null_dacl=True,
        )
    writers: list[str] = []
    readers: list[str] = []
    for ace in dacl:
        if ace.ace_type in _DENY_ACE_TYPES:
            continue
        if ace.ace_type in _ALLOW_ACE_TYPES and ace.sid is not None:
            trustee = _normalise_sid(ace.sid)
            mask = ace.mask
        else:
            trustee = f"<unreadable ACE type {ace.ace_type:#x}>"
            mask = _GENERIC_ALL
        if trustee in trusted or trustee in _PLACEHOLDER_SIDS:
            continue
        applies_here = not ace.flags & _INHERIT_ONLY_ACE
        writes = applies_here and mask & write_rights
        writes_later = ace.flags & _OBJECT_INHERIT_ACE and mask & inherited_file_rights
        if (writes or writes_later) and trustee not in writers:
            writers.append(trustee)
        if applies_here and mask & _FILE_READ_RIGHTS and trustee not in readers:
            readers.append(trustee)
    return WindowsAclVerdict(
        owner_trusted=owner_trusted,
        writers=tuple(writers),
        readers=tuple(readers),
    )


def _describe_sid(sid: str) -> str:
    name = _WELL_KNOWN_SID_NAMES.get(sid)
    return f"{name} ({sid})" if name else sid


class _UnverifiableWindowsAcl(Exception):
    """The volume or the API gave no security descriptor this code can judge.

    ``reason`` is the true cause, ready to log: the API and Windows error
    code, or the volume flag that is missing.
    """

    def __init__(self, reason: str, *, volume_serial: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.volume_serial = volume_serial


@dataclass(frozen=True)
class _WindowsSecurity:
    owner_sid: str
    dacl: tuple[WindowsAce, ...] | None
    volume_serial: int | None = None
    is_volume_root: bool = False


_windows_notices_logged: set[tuple[int | None, str, str]] = set()


def _log_windows_notice(path: str, volume_serial: int | None, text: str) -> None:
    """Log one Windows permission finding, once per volume, path and finding.

    Keyed on the volume serial as well as the path, so a different drive
    mounted at the same letter (a USB stick swapped) is reported again, and on
    the text, so a changed finding is reported again too.
    """
    key = (volume_serial, path, text)
    if key in _windows_notices_logged:
        return
    _windows_notices_logged.add(key)
    logger.warning(text)


def _accept_unverifiable_windows_acl(
    path: str, reason: str, volume_serial: int | None = None
) -> None:
    """Decide what a path whose permissions cannot be read means: note it, open.

    A FAT32/exFAT volume keeps no permissions at all, a network server may not
    report them, and ``GetNamedSecurityInfoW`` can fail on an NTFS volume
    (access denied, a sharing violation). None of these refuses the open; each
    is logged once with its true cause. This is the one place that decides:
    raising ``TrustedSQLiteLocationError`` here turns them into refusals.
    """
    _log_windows_notice(
        path,
        volume_serial,
        f"Could not read the Windows permissions of {path} ({reason}), so "
        "PixlStash cannot tell whether other accounts can change it. Opening "
        "it as usual.",
    )


def _is_drive_root(path: str) -> bool:
    """``C:\\`` or ``\\\\server\\share``: the top of a volume, by its spelling."""
    drive, rest = os.path.splitdrive(path)
    return bool(drive) and rest.strip("\\/") == ""


def _check_windows_acl(path: str, *, kind: str, private: bool = False) -> None:
    """Report, never refuse, when *path*'s owner or DACL admits another account.

    *kind* is ``"directory"`` (the database's own directory), ``"ancestor"``
    or ``"file"`` (the database or a sidecar); see ``WINDOWS_WRITE_RIGHTS``.
    Every finding is a WARNING that names the cause and the ``icacls`` or
    ``takeown`` command that changes it; where a library lives is the owner's
    choice. Only a path that vanished raises (from the reader), which the
    sidecar loop tolerates and the main file treats like a failed ``lstat``.
    """
    try:
        security = _read_windows_security(path)
    except _UnverifiableWindowsAcl as exc:
        _accept_unverifiable_windows_acl(path, exc.reason, exc.volume_serial)
        return
    try:
        user_sid = _windows_user_sid()
    except _UnverifiableWindowsAcl as exc:
        _accept_unverifiable_windows_acl(
            path, f"this account's SID: {exc.reason}", security.volume_serial
        )
        return
    verdict = classify_windows_acl(
        security.owner_sid,
        user_sid,
        security.dacl,
        write_rights=WINDOWS_WRITE_RIGHTS[kind],
        inherited_file_rights=WINDOWS_INHERITED_WRITE_RIGHTS[kind],
        volume_root=security.is_volume_root,
    )
    serial = security.volume_serial
    # Never suggest rewriting the permissions of a whole drive.
    drive_root = security.is_volume_root or _is_drive_root(path)
    quoted = f'"{path}"'
    if not verdict.owner_trusted:
        fix = "" if drive_root else f" If it should be yours: takeown /F {quoted}"
        _log_windows_notice(
            path,
            serial,
            f"{path} is owned by "
            f"{_describe_sid(_normalise_sid(security.owner_sid))}, not this "
            f"account ({user_sid}), SYSTEM or Administrators; that is usual for a "
            "drive or folder made on another computer. Opening it as usual." + fix,
        )
    if verdict.writers:
        if verdict.null_dacl:
            what = "has no permission list, so every account can change it"
            fix = "" if drive_root else f" To limit it: icacls {quoted} /reset"
        else:
            who = ", ".join(_describe_sid(sid) for sid in verdict.writers)
            what = f"can also be changed by {who}"
            removable = " ".join(
                f"*{sid}" for sid in verdict.writers if sid.startswith("S-")
            )
            if drive_root:
                fix = (
                    " To keep it to your account, keep the library in a folder "
                    "of its own rather than at the top of the drive."
                )
            elif removable:
                fix = (
                    f" To keep it to your account: icacls {quoted} "
                    f"/inheritance:d and then icacls {quoted} /remove:g {removable}"
                )
            else:
                fix = f" Its entries can be inspected with: icacls {quoted}"
        _log_windows_notice(
            path,
            serial,
            f"{path} {what}, who could then replace the database or add a "
            "WAL/journal file it would read. Opening it as usual." + fix,
        )
    if private and kind == "file" and verdict.readers:
        readers = " ".join(f"*{sid}" for sid in verdict.readers if sid.startswith("S-"))
        _log_windows_notice(
            path,
            serial,
            f"Credential file {path} can be read by "
            f"{', '.join(_describe_sid(sid) for sid in verdict.readers)}. To "
            f"keep it to your account: icacls {quoted} /inheritance:d and then "
            f"icacls {quoted} /remove:g {readers}",
        )


# The ctypes reader. Everything below touches the Windows API and is only
# reached when os.name == "nt"; `_win32()` resolves the DLLs on first use so
# importing this module on POSIX never needs them.

_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x1
_DACL_SECURITY_INFORMATION = 0x4
_ACL_SIZE_INFORMATION_CLASS = 2
_TOKEN_QUERY = 0x0008
_TOKEN_USER_CLASS = 1
_FILE_PERSISTENT_ACLS = 0x00000008
_ACE_OBJECT_TYPE_PRESENT = 0x1
_ACE_INHERITED_OBJECT_TYPE_PRESENT = 0x2
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_INSUFFICIENT_BUFFER = 122
_ACL_REVISION = 2
_FILE_ALL_ACCESS = 0x001F01FF
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
# A SID's fixed part: revision, sub-authority count, 6-byte authority.
_SID_HEADER_BYTES = 8


class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", ctypes.c_ushort),
    ]


class _AclSizeInformation(ctypes.Structure):
    _fields_ = [
        ("AceCount", ctypes.c_uint32),
        ("AclBytesInUse", ctypes.c_uint32),
        ("AclBytesFree", ctypes.c_uint32),
    ]


class _TokenUser(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", ctypes.c_uint32)]


@functools.lru_cache(maxsize=1)
def _win32() -> types.SimpleNamespace:
    """advapi32 and kernel32 with explicit signatures, resolved on first use."""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    void_p = ctypes.c_void_p
    out_p = ctypes.POINTER(ctypes.c_void_p)
    dword = ctypes.c_uint32
    bool_ = ctypes.c_int

    def bind(dll, name, restype, *argtypes):
        function = getattr(dll, name)
        function.restype = restype
        function.argtypes = list(argtypes)
        return function

    return types.SimpleNamespace(
        GetNamedSecurityInfoW=bind(
            advapi32,
            "GetNamedSecurityInfoW",
            dword,
            ctypes.c_wchar_p,
            ctypes.c_int,
            dword,
            out_p,
            out_p,
            out_p,
            out_p,
            out_p,
        ),
        GetAclInformation=bind(
            advapi32, "GetAclInformation", bool_, void_p, void_p, dword, ctypes.c_int
        ),
        GetAce=bind(advapi32, "GetAce", bool_, void_p, dword, out_p),
        IsValidSid=bind(advapi32, "IsValidSid", bool_, void_p),
        ConvertSidToStringSidW=bind(
            advapi32, "ConvertSidToStringSidW", bool_, void_p, out_p
        ),
        OpenProcessToken=bind(
            advapi32, "OpenProcessToken", bool_, void_p, dword, out_p
        ),
        GetTokenInformation=bind(
            advapi32,
            "GetTokenInformation",
            bool_,
            void_p,
            ctypes.c_int,
            void_p,
            dword,
            ctypes.POINTER(dword),
        ),
        GetLengthSid=bind(advapi32, "GetLengthSid", dword, void_p),
        ConvertStringSidToSidW=bind(
            advapi32, "ConvertStringSidToSidW", bool_, ctypes.c_wchar_p, out_p
        ),
        InitializeAcl=bind(advapi32, "InitializeAcl", bool_, void_p, dword, dword),
        AddAccessAllowedAceEx=bind(
            advapi32,
            "AddAccessAllowedAceEx",
            bool_,
            void_p,
            dword,
            dword,
            dword,
            void_p,
        ),
        SetNamedSecurityInfoW=bind(
            advapi32,
            "SetNamedSecurityInfoW",
            dword,
            ctypes.c_wchar_p,
            ctypes.c_int,
            dword,
            void_p,
            void_p,
            void_p,
            void_p,
        ),
        GetCurrentProcess=bind(kernel32, "GetCurrentProcess", void_p),
        CloseHandle=bind(kernel32, "CloseHandle", bool_, void_p),
        LocalFree=bind(kernel32, "LocalFree", void_p, void_p),
        GetVolumePathNameW=bind(
            kernel32,
            "GetVolumePathNameW",
            bool_,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            dword,
        ),
        GetVolumeInformationW=bind(
            kernel32,
            "GetVolumeInformationW",
            bool_,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            dword,
            ctypes.POINTER(dword),
            ctypes.POINTER(dword),
            ctypes.POINTER(dword),
            ctypes.c_wchar_p,
            dword,
        ),
    )


def _win_code(code: int) -> str:
    return f"Windows error {code} ({ctypes.FormatError(code).strip()})"


def _win_error(api: str, code: int) -> str:
    return f"{api} failed with {_win_code(code)}"


def _local_free(api: types.SimpleNamespace, pointer: int | None, what: str) -> None:
    """Free a ``LocalAlloc`` buffer the API handed back, logging a failure."""
    if not pointer:
        return
    if api.LocalFree(pointer):
        logger.warning(
            "LocalFree of the %s buffer failed with Windows error %d; the "
            "buffer leaks for the life of the process.",
            what,
            ctypes.get_last_error(),
        )


def _sid_to_string(api: types.SimpleNamespace, sid: int) -> str:
    if not sid or not api.IsValidSid(sid):
        raise _UnverifiableWindowsAcl("Windows returned an invalid SID")
    out = ctypes.c_void_p()
    if not api.ConvertSidToStringSidW(sid, ctypes.byref(out)):
        raise _UnverifiableWindowsAcl(
            _win_error("ConvertSidToStringSidW", ctypes.get_last_error())
        )
    try:
        return ctypes.wstring_at(out.value)
    finally:
        _local_free(api, out.value, "SID string")


@functools.lru_cache(maxsize=1)
def _windows_user_sid() -> str:
    """The process token's user SID, ``S-1-...``; constant for the process.

    Raises:
        _UnverifiableWindowsAcl: the token could not be read; nothing can be
            judged without it, and that is reported like any unreadable path.
    """
    api = _win32()
    token = ctypes.c_void_p()
    if not api.OpenProcessToken(
        api.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
    ):
        raise _UnverifiableWindowsAcl(
            _win_error("OpenProcessToken", ctypes.get_last_error())
        )
    try:
        needed = ctypes.c_uint32(0)
        if not api.GetTokenInformation(
            token, _TOKEN_USER_CLASS, None, 0, ctypes.byref(needed)
        ):
            code = ctypes.get_last_error()
            if code != _ERROR_INSUFFICIENT_BUFFER or not needed.value:
                raise _UnverifiableWindowsAcl(_win_error("GetTokenInformation", code))
        buffer = ctypes.create_string_buffer(needed.value)
        if not api.GetTokenInformation(
            token, _TOKEN_USER_CLASS, buffer, needed.value, ctypes.byref(needed)
        ):
            raise _UnverifiableWindowsAcl(
                _win_error("GetTokenInformation", ctypes.get_last_error())
            )
        return _normalise_sid(_sid_to_string(api, _TokenUser.from_buffer(buffer).Sid))
    finally:
        if not api.CloseHandle(token):
            logger.warning(
                "CloseHandle on the process token failed with Windows error %d.",
                ctypes.get_last_error(),
            )


def _volume_info(api: types.SimpleNamespace, volume_root: str) -> tuple[int, int]:
    """``(serial, flags)`` of the volume at *volume_root*; asked per call.

    Never cached: a drive letter can be re-pointed at another volume while the
    process runs (a USB stick swapped), and the serial is what tells them apart.
    """
    serial = ctypes.c_uint32(0)
    flags = ctypes.c_uint32(0)
    if not api.GetVolumeInformationW(
        volume_root, None, 0, ctypes.byref(serial), None, ctypes.byref(flags), None, 0
    ):
        raise _UnverifiableWindowsAcl(
            _win_error("GetVolumeInformationW", ctypes.get_last_error())
        )
    return serial.value, flags.value


def _volume_root(api: types.SimpleNamespace, path: str) -> str:
    # The volume path is a prefix of *path*, so this is always long enough.
    buffer = ctypes.create_unicode_buffer(max(len(path), 260) + 64)
    if not api.GetVolumePathNameW(path, buffer, len(buffer)):
        raise _UnverifiableWindowsAcl(
            _win_error("GetVolumePathNameW", ctypes.get_last_error())
        )
    return buffer.value


def _ace_sid_offset(
    api: types.SimpleNamespace, ace: int, header: _AceHeader
) -> int | None:
    """Byte offset of the trustee SID in an allow ACE, or None if unreadable."""
    if header.AceType in _OBJECT_ACE_TYPES:
        # ACCESS_ALLOWED_OBJECT_ACE: header, Mask, Flags, then the GUIDs
        # its Flags say are present, then the SID.
        object_flags = ctypes.c_uint32.from_address(ace + 8).value
        offset = 12
        if object_flags & _ACE_OBJECT_TYPE_PRESENT:
            offset += 16
        if object_flags & _ACE_INHERITED_OBJECT_TYPE_PRESENT:
            offset += 16
    else:
        # ACCESS_ALLOWED_ACE and the callback form: header, Mask, SidStart.
        offset = 8
    # Every byte of the SID must lie inside this ACE: a SID that runs past
    # AceSize would be read out of the next ACE, or past the ACL.
    if offset + _SID_HEADER_BYTES > header.AceSize:
        return None
    sub_authorities = ctypes.c_ubyte.from_address(ace + offset + 1).value
    if offset + _SID_HEADER_BYTES + 4 * sub_authorities > header.AceSize:
        return None
    if not api.IsValidSid(ace + offset):
        return None
    if offset + api.GetLengthSid(ace + offset) > header.AceSize:
        return None
    return offset


def _read_dacl(api: types.SimpleNamespace, dacl: int) -> tuple[WindowsAce, ...]:
    info = _AclSizeInformation()
    if not api.GetAclInformation(
        dacl,
        ctypes.byref(info),
        ctypes.sizeof(info),
        _ACL_SIZE_INFORMATION_CLASS,
    ):
        raise _UnverifiableWindowsAcl(
            _win_error("GetAclInformation", ctypes.get_last_error())
        )
    aces: list[WindowsAce] = []
    for index in range(info.AceCount):
        pointer = ctypes.c_void_p()
        if not api.GetAce(dacl, index, ctypes.byref(pointer)):
            raise _UnverifiableWindowsAcl(_win_error("GetAce", ctypes.get_last_error()))
        ace = pointer.value
        header = _AceHeader.from_address(ace)
        mask = ctypes.c_uint32.from_address(ace + 4).value
        sid = None
        if header.AceType in _ALLOW_ACE_TYPES:
            offset = _ace_sid_offset(api, ace, header)
            if offset is not None:
                sid = _sid_to_string(api, ace + offset)
        aces.append(WindowsAce(header.AceType, header.AceFlags, mask, sid))
    return tuple(aces)


def _read_windows_security(path: str) -> _WindowsSecurity:
    """Owner SID and DACL of *path*, read with ``GetNamedSecurityInfoW``.

    Raises:
        _UnverifiableWindowsAcl: the volume keeps no ACLs, or any Windows call
            failed (access denied included), or the descriptor carries no
            owner; the reason names the API and error code.
        TrustedSQLiteLocationError: the path vanished (chained from
            ``FileNotFoundError``, which the sidecar loop tolerates).
    """
    api = _win32()
    root = _volume_root(api, path)
    serial, flags = _volume_info(api, root)
    if not flags & _FILE_PERSISTENT_ACLS:
        raise _UnverifiableWindowsAcl(
            "the volume keeps no file permissions (no FILE_PERSISTENT_ACLS, "
            "as on FAT32/exFAT)",
            volume_serial=serial,
        )
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    code = api.GetNamedSecurityInfoW(
        path,
        _SE_FILE_OBJECT,
        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if code in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
        raise TrustedSQLiteLocationError(
            f"Could not inspect SQLite path {path}: "
            + _win_error("GetNamedSecurityInfoW", code)
        ) from FileNotFoundError(code, "not found", path)
    if code:
        raise _UnverifiableWindowsAcl(
            _win_error("GetNamedSecurityInfoW", code), volume_serial=serial
        )
    try:
        if not owner.value:
            raise _UnverifiableWindowsAcl(
                "GetNamedSecurityInfoW returned a security descriptor with no owner",
                volume_serial=serial,
            )
        try:
            return _WindowsSecurity(
                owner_sid=_sid_to_string(api, owner.value),
                dacl=None if not dacl.value else _read_dacl(api, dacl.value),
                volume_serial=serial,
                is_volume_root=os.path.normcase(path.rstrip("\\/"))
                == os.path.normcase(root.rstrip("\\/")),
            )
        except _UnverifiableWindowsAcl as exc:
            raise _UnverifiableWindowsAcl(exc.reason, volume_serial=serial) from exc
    finally:
        _local_free(api, descriptor.value, "security descriptor")


def _apply_private_windows_dacl(path: str, *, is_directory: bool) -> None:
    """Replace *path*'s DACL with a protected one: this account, SYSTEM, Admins.

    Inheritance from the parent is switched off, so the folder the object sits
    in no longer decides who can write it. On a directory the three ACEs carry
    ``(OI)(CI)``, so what SQLite creates inside later - the ``-wal``, ``-shm``
    and ``-journal`` sidecars - inherits the same three entries.

    Raises:
        _UnverifiableWindowsAcl: any Windows call failed; the reason names the
            API and the error code.
    """
    api = _win32()
    _serial, flags = _volume_info(api, _volume_root(api, path))
    if not flags & _FILE_PERSISTENT_ACLS:
        logger.info(
            "Not restricting %s to this account: its volume keeps no file "
            "permissions (no FILE_PERSISTENT_ACLS, as on FAT32/exFAT).",
            path,
        )
        return
    sids = list(dict.fromkeys((_windows_user_sid(), _SYSTEM_SID, _ADMINISTRATORS_SID)))
    binary: list[int] = []
    try:
        for sid in sids:
            pointer = ctypes.c_void_p()
            if not api.ConvertStringSidToSidW(sid, ctypes.byref(pointer)):
                raise _UnverifiableWindowsAcl(
                    _win_error("ConvertStringSidToSidW", ctypes.get_last_error())
                )
            binary.append(pointer.value)
        # ACL header (8) plus, per ACCESS_ALLOWED_ACE, header and mask (8) and
        # the SID; DWORD-aligned as InitializeAcl requires.
        size = 8 + sum(8 + api.GetLengthSid(sid) for sid in binary)
        size = (size + 3) & ~3
        acl = ctypes.create_string_buffer(size)
        if not api.InitializeAcl(acl, size, _ACL_REVISION):
            raise _UnverifiableWindowsAcl(
                _win_error("InitializeAcl", ctypes.get_last_error())
            )
        inherit = (_OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE) if is_directory else 0
        for sid in binary:
            if not api.AddAccessAllowedAceEx(
                acl, _ACL_REVISION, inherit, _FILE_ALL_ACCESS, sid
            ):
                raise _UnverifiableWindowsAcl(
                    _win_error("AddAccessAllowedAceEx", ctypes.get_last_error())
                )
        code = api.SetNamedSecurityInfoW(
            path,
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            acl,
            None,
        )
        if code:
            raise _UnverifiableWindowsAcl(_win_error("SetNamedSecurityInfoW", code))
    finally:
        for sid in binary:
            _local_free(api, sid, "SID")


def restrict_new_path_to_owner(path: str, *, is_directory: bool) -> None:
    """On Windows, lock a file or folder PixlStash just created to this account.

    The Windows counterpart of creating it ``0600``/``0700``: the mode argument
    to ``os.open``/``mkdir`` means nothing there (before Python 3.13), so the
    object would otherwise inherit whatever its parent folder grants. Call it
    only on something this process has just created - never on a folder or
    file the owner already had. A no-op elsewhere. A failure is logged as a
    WARNING with the path and the Windows error and the object keeps the
    permissions it inherited.
    """
    if os.name != "nt":
        return
    try:
        _apply_private_windows_dacl(path, is_directory=is_directory)
    except _UnverifiableWindowsAcl as exc:
        logger.warning(
            "Could not restrict %s to this account (%s); it keeps the "
            "permissions inherited from its folder.",
            path,
            exc.reason,
        )


def _is_redirect(path: str) -> bool:
    """True when *path* is a symlink, or a Windows junction.

    ``os.path.islink`` is not sufficient on Windows: it returns **False** for a
    directory junction, while ``os.path.realpath`` resolves one. Checking only
    ``islink`` there would refuse the *privileged* redirect and accept the
    unprivileged one - creating a symlink needs
    ``SeCreateSymbolicLinkPrivilege`` (admin or Developer Mode), creating a
    junction needs nothing but write access to the directory (``mklink /J``).
    That is the redirection primitive an unprivileged local account actually
    has, so it is the one that matters most here.

    ``os.path.isjunction`` would say this in one call but is 3.12+, and the
    floor is 3.11 (``pyproject.toml``), so read the reparse tag. The constants
    and ``st_reparse_tag`` are Windows-only, hence ``getattr``: the tests
    simulate ``nt`` while running on Linux, where neither exists.
    """
    if os.path.islink(path):
        return True
    if os.name != "nt":
        return False
    tags = {
        tag
        for tag in (
            getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None),
            getattr(stat, "IO_REPARSE_TAG_SYMLINK", None),
        )
        if tag is not None
    }
    try:
        return getattr(os.lstat(path), "st_reparse_tag", None) in tags
    except OSError:
        # A component that does not exist yet cannot redirect anywhere;
        # `create=True` opens depend on this.
        return False


def _reject_symlinked_path(path: str) -> None:
    """Refuse a caller-supplied path that reaches its target via a symlink.

    Deliberately not ``os.path.abspath(p) != os.path.realpath(p)``. That
    comparison holds on POSIX, where ``realpath`` differs from ``abspath`` only
    where a symlink was resolved, but it is wrong on Windows: ``realpath`` also
    expands 8.3 short names and normalises case, so ``C:\\Users\\RUNNER~1\\...``
    - the form ``%TEMP%`` takes on a GitHub runner - was reported as "contains a
    symlink" when it contains none. That misdiagnosis took down every Windows
    test that opens a hub.

    Testing each component is the property that was actually meant, and it
    names the offending component instead of leaving the caller to infer it.
    A component that does not exist yet is not a symlink; ``create=True`` opens
    rely on that.

    The old comparison did catch one thing a bare ``islink`` walk does not: a
    Windows **junction**, which ``realpath`` resolves and ``islink`` reports as
    False. Dropping that would have been a straight downgrade, since a junction
    is the redirect an unprivileged account can create - see ``_is_redirect``.
    """
    current = path
    while True:
        if _is_redirect(current):
            raise TrustedSQLiteLocationError(
                f"SQLite path {path} reaches its target through a symlink or "
                f"junction at {current}; refusing to open it."
            )
        parent = os.path.dirname(current)
        if parent == current:
            return
        current = parent


def _validate_namespace(canonical_path: str) -> None:
    parent = os.path.dirname(canonical_path)
    current = parent
    immediate = True
    while True:
        _require_owned_directory(current, immediate=immediate)
        next_parent = os.path.dirname(current)
        if next_parent == current:
            break
        current = next_parent
        immediate = False


def _validate_file(path: str, *, private: bool) -> os.stat_result:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise TrustedSQLiteLocationError(
            f"Could not inspect SQLite file {path}: {exc}"
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        kind = "symlink" if stat.S_ISLNK(info.st_mode) else "non-regular file"
        raise TrustedSQLiteLocationError(
            f"SQLite file {path} is a {kind}; refusing to open it."
        )
    if os.name == "nt":
        # Windows carries ACLs, not mode bits: `st_mode` there is synthesised
        # from the read-only attribute and reads 0o666 for an ordinary file, so
        # the POSIX checks below would refuse every file. The owner SID and the
        # DACL carry the same answer.
        _check_windows_acl(path, kind="file", private=private)
        return info
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise TrustedSQLiteLocationError(
            f"SQLite file {path} is owned by uid {info.st_uid}, not this user."
        )
    # Mode bits warn rather than refuse (see _require_owned_directory).
    if private and stat.S_IMODE(info.st_mode) & 0o077:
        logger.warning(
            "SQLite credential file %s should be mode 600; other accounts can "
            "read it. Tighten it with chmod 600 %s",
            path,
            shlex.quote(path),
        )
    elif stat.S_IMODE(info.st_mode) & 0o022:
        logger.warning(
            "SQLite file %s is group/world-writable; another account could "
            "modify it. Tighten it with chmod g-w,o-w %s",
            path,
            shlex.quote(path),
        )
    return info


def _validate_sidecars(canonical: str, *, private: bool) -> None:
    """Validate every existing ``-wal``/``-shm``/``-journal`` beside *canonical*.

    A sidecar that VANISHES between the existence probe and the ``lstat`` is
    tolerated: a concurrent opener's SQLite creates and deletes a transient
    ``-journal`` during first migration (before WAL is set), and refusing that
    made 1-in-20 four-opener races fail with "Could not inspect ... -journal:
    No such file or directory" - the same concurrent-open-mistaken-for-tampering
    class the creation-race test exists to catch. A vanished file has no
    attributes to be hostile with; a hostile sidecar that still exists is
    refused exactly as before.
    """
    for suffix in _SIDECAR_SUFFIXES:
        sidecar = canonical + suffix
        if not os.path.lexists(sidecar):
            continue
        try:
            _validate_file(sidecar, private=private)
        except TrustedSQLiteLocationError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                logger.info(
                    "Sidecar %s vanished during validation; a concurrent "
                    "opener's transient journal, not tampering.",
                    sidecar,
                )
                continue
            raise


@dataclass
class TrustedSQLiteLocation:
    """A guarded canonical SQLite path whose surrounding namespace is trusted."""

    path: str
    fd: int
    identity: tuple[int, int]
    parent_identity: tuple[int, int]
    private: bool = False

    @classmethod
    def open(
        cls,
        path: str,
        *,
        private: bool = False,
        create: bool = False,
        trusted_root: str | None = None,
    ) -> "TrustedSQLiteLocation":
        if private and trusted_root is None:
            raise TypeError(
                "private=True requires trusted_root=: pass the directory the "
                "credential store's path was derived from (its own parent). "
                "See trusted_root in the module docstring."
            )
        absolute = os.path.abspath(os.path.expanduser(path))
        canonical = os.path.realpath(absolute)
        # A canonical path is used for SQLite, but accepting a symlink in the
        # caller-provided path would make the visible target mutable.
        _reject_symlinked_path(absolute)
        if private:
            # Containment in the root the caller derived the path from. In
            # correct code this is tautologically true; it exists so a caller
            # cannot silently opt out of declaring where the credential store
            # is trusted (see trusted_root in the module docstring). It is not
            # a policy on where the owner may put the hub.
            root = os.path.realpath(os.path.abspath(os.path.expanduser(trusted_root)))
            try:
                contained = os.path.commonpath((canonical, root)) == root
            except ValueError:
                # Different drives (or mixed absolute/relative on Windows)
                # share no common path, so the file is not inside the root.
                contained = False
            if not contained:
                raise TrustedSQLiteLocationError(
                    f"SQLite credential file {canonical} is outside its "
                    f"trusted root {root}; refusing a security-sensitive open."
                )
        _validate_namespace(canonical)
        if create and not os.path.lexists(canonical):
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                created_fd = os.open(canonical, flags, 0o600)
            except FileExistsError:
                # Another process - or an attacker - created the file between
                # the lexists check and this open. Not fatal by itself: the
                # _validate_file call below decides whether the file that won
                # the race is acceptable. Logged so a hostile win is visible.
                logger.warning(
                    "Lost the creation race for SQLite file %s; validating the "
                    "existing file instead of the one this process would have "
                    "created.",
                    canonical,
                )
            except OSError as exc:
                raise TrustedSQLiteLocationError(
                    f"Could not securely create SQLite file {canonical}: {exc}"
                ) from exc
            else:
                os.close(created_fd)
                restrict_new_path_to_owner(canonical, is_directory=False)
        parent_identity = _identity(os.lstat(os.path.dirname(canonical)))
        expected = _validate_file(canonical, private=private)
        _validate_sidecars(canonical, private=private)

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(canonical, flags)
        except OSError as exc:
            raise TrustedSQLiteLocationError(
                f"Could not securely guard SQLite file {canonical}: {exc}"
            ) from exc
        guarded = os.fstat(fd)
        if _identity(guarded) != _identity(expected):
            os.close(fd)
            raise TrustedSQLiteLocationError(
                f"SQLite file {canonical} changed while it was guarded."
            )
        return cls(
            canonical,
            fd,
            _identity(guarded),
            parent_identity,
            private,
        )

    def verify_after_open(self) -> None:
        current = _validate_file(self.path, private=False)
        if _identity(current) != self.identity:
            raise TrustedSQLiteLocationError(
                f"SQLite file {self.path} changed while it was being opened."
            )
        # Re-check the directory for the PROPERTY that matters rather than
        # comparing it against a snapshot of its timestamps. Those are not the
        # same question. mtime/ctime move whenever any entry is created in the
        # directory, so SQLite creating our own -wal/-shm, or a second process
        # opening the same database, was indistinguishable from tampering: it
        # refused ~22% of concurrent opens (measured at four openers) while the
        # only thing it could observe was same-uid activity, which is out of
        # scope per the module docstring. Asking _require_owned_directory again
        # is stable under concurrency (creating a sidecar changes neither the
        # owner nor the mode) and is strictly MORE than the old comparison: a
        # chmod between open and verify used to be caught only incidentally,
        # via the ctime it happened to bump.
        parent = _require_owned_directory(os.path.dirname(self.path), immediate=True)
        if _identity(parent) != self.parent_identity:
            raise TrustedSQLiteLocationError(
                f"SQLite namespace for {self.path} was replaced while it was "
                "being opened."
            )
        # The sidecars SQLite just created are new entries in a directory we
        # have re-verified as unwritable by anyone else. Validate them directly
        # anyway: this is the check that actually catches a hostile sidecar.
        _validate_sidecars(self.path, private=self.private)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> "TrustedSQLiteLocation":
        return self

    def __exit__(self, *_args) -> None:
        self.close()


@contextlib.contextmanager
def checked_read_connection(
    path: str, *, timeout: float = 5.0
) -> Iterator[sqlite3.Connection]:
    """A read-only connection to *path*, opened through the same checks.

    For a short read of a vault or hub outside its long-lived engine: the
    namespace, file and sidecars are validated before ``sqlite3.connect``,
    and ``verify_after_open`` runs before the caller reads a row. The
    connection closes before the guard does, because closing any descriptor on
    the file drops this process's POSIX locks on it.

    Raises:
        TrustedSQLiteLocationError: the location failed a check.
        sqlite3.Error: SQLite could not open the file.
    """
    with TrustedSQLiteLocation.open(path) as guard:
        connection = sqlite3.connect(
            sqlite_file_uri(guard.path, read_only=True), uri=True, timeout=timeout
        )
        try:
            guard.verify_after_open()
            yield connection
        finally:
            connection.close()
