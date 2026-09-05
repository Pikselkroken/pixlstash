"""Trusted-location guards for security-sensitive SQLite opens.

SQLite's main file cannot be safely redirected through ``/proc/self/fd`` when
WAL is enabled: its ``-wal`` and ``-shm`` siblings are derived from the path.
Instead, require a namespace in which another OS principal cannot replace the
main file or pre-position a sidecar, hold a no-follow guard, open SQLite by the
canonical path, and compare identities before doing decisive work.

**Mode bits warn; everything else refuses.** Ownership, symlinks, junctions and
non-regular files are refused outright. A loose mode (group/world-writable
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

**Windows, and what this cannot check.** Python exposes neither owner SID nor
directory DACL portably, so the "another principal cannot write this directory"
test - POSIX's ``mode & 0o022`` - has no Windows implementation. An earlier
revision substituted a blanket refusal of any ``private=True`` open outside
``user_config_dir("pixlstash")``. That predicate was false for every desktop
install - the Electron shell derives the hub path from its own config under
``%APPDATA%\\pixlstash-desktop`` while ``user_config_dir`` resolves under
``%LOCALAPPDATA%`` - so the server never started on Windows (W6/W7/W18). The
DACL refusal is gone.

A ``private=True`` open now requires a mandatory, no-default ``trusted_root``:
the parent directory the caller derived the hub path from. The only check is
containment - the canonical file must be inside that root. In correct code the
containment is tautologically true; the parameter exists so that *forgetting*
it fails at the first test run instead of silently opting out in production
(the root cause of W4-W7). It does not second-guess where the owner put the
hub: the hub is trusted at the root its own configuration placed it, with no
DACL verification until the native verifier (3c) exists.

*Accepted risk, for the vault.* On Windows a library on a network share,
removable media, or a folder someone deliberately loosened is not protected
against another local principal substituting ``vault.db`` or pre-positioning a
sidecar **before** startup. Default ACLs already exclude other standard users
from a user profile, and those three cases are the ones named above as this
lane's real concerns.

**Blast radius, stated accurately** (an independent review corrected an earlier
version of this note that claimed it was reads only):

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
  and is tracked separately - it is the fix that actually matters.
* Live credentials are not here: the password hash and token hashes are in the
  hub. The vault does hold ``guest_session.cookie_token`` and dormant
  ``user``/``usertoken`` tables the baseline still creates.

*Compensating controls that genuinely run on Windows* - the list is shorter
than it looks, because ``O_NOFOLLOW`` does not exist there and
``_require_owned_directory`` returns early on ``nt``, making the ancestor walk
an existence check rather than a trust check:

1. symlink **and junction** rejection on every component (``_is_redirect``);
2. the regular-file requirement, on the target and on every sidecar;
3. the ``(st_dev, st_ino)`` identity match across the open, plus
   ``verify_after_open``.

Revisit when a native ACL verifier exists (``win32security.GetNamedSecurityInfo``
or ctypes against advapi32), which is the route back to tightening this.

Accepted-risk record (W17): owner lindkvis, revisit 2026-11-08, together with
the native ACL verifier (3c) that would close the Windows residue. Recorded in
``docs/backend_architecture.md`` §13 ("Trusted SQLite locations, and the
accepted Windows residue") - not in ``docs/reviews/``, which is gitignored, so
a record kept there would exist only on the machine that wrote it.
"""

from __future__ import annotations

import os
import shlex
import stat
from dataclasses import dataclass

try:  # POSIX-only; only ever consulted where os.geteuid() also exists.
    import grp
    import pwd
except ImportError:  # pragma: no cover - Windows
    grp = None  # type: ignore[assignment]
    pwd = None  # type: ignore[assignment]

from pixlstash.pixl_logging import get_logger

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
        # Windows carries ACLs, not mode bits. `st_mode` there is synthesised
        # from the read-only attribute alone and reads 0o666 for an ordinary
        # file, so every check below would refuse every file - which is what
        # made both Windows shards fail with "must be mode 600" on a hub this
        # process had just created. The ownership check above is already
        # POSIX-only for the same reason; the DACL that does carry the answer
        # is unreadable from portable Python (module docstring, W17 record).
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
                "See 'Windows, and what this cannot check' in the module "
                "docstring."
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
            # is trusted (the module docstring's W4-W7 root cause). It is not
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
