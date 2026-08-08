"""Trusted-location guards for security-sensitive SQLite opens.

SQLite's main file cannot be safely redirected through ``/proc/self/fd`` when
WAL is enabled: its ``-wal`` and ``-shm`` siblings are derived from the path.
Instead, require a namespace in which another OS principal cannot replace the
main file or pre-position a sidecar, hold a no-follow guard, open SQLite by the
canonical path, and compare identities before doing decisive work.

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

**Windows, and what this cannot check.** Python exposes neither owner SID nor
directory DACL portably, so the "another principal cannot write this directory"
test — POSIX's ``mode & 0o022`` — has no Windows implementation. It was
substituted with a blanket refusal for anything outside the app config
directory, and applied to every caller. That made ``Vault.__init__`` raise for
every path on Windows: the product could not open its library at all, so the
"control" protected nobody and stopped everybody.

The refusal is therefore scoped to ``private=True`` — the hub, which holds the
password hash and every token hash and lives in the config directory anyway.

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
  and is tracked separately — it is the fix that actually matters.
* Live credentials are not here: the password hash and token hashes are in the
  hub. The vault does hold ``guest_session.cookie_token`` and dormant
  ``user``/``usertoken`` tables the baseline still creates.

*Compensating controls that genuinely run on Windows* — the list is shorter
than it looks, because ``O_NOFOLLOW`` does not exist there and
``_require_owned_directory`` returns early on ``nt``, making the ancestor walk
an existence check rather than a trust check:

1. symlink **and junction** rejection on every component (``_is_redirect``);
2. the regular-file requirement, on the target and on every sidecar;
3. the ``(st_dev, st_ino)`` identity match across the open, plus
   ``verify_after_open``.

Revisit when a native ACL verifier exists (``win32security.GetNamedSecurityInfo``
or ctypes against advapi32), which is the route back to tightening this.

TODO(owner): this risk has no named owner or revisit date. The reviewer
declined to accept it on the author's behalf; both must be filled in before
this is treated as accepted rather than merely documented.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass

from platformdirs import user_config_dir


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
    writable = stat.S_IMODE(info.st_mode) & 0o022
    if writable and (immediate or not (info.st_mode & stat.S_ISVTX)):
        raise TrustedSQLiteLocationError(
            f"SQLite directory {path} is group/world-writable; another account "
            "could replace the database or its WAL/SHM files."
        )
    return info


def _is_redirect(path: str) -> bool:
    """True when *path* is a symlink, or a Windows junction.

    ``os.path.islink`` is not sufficient on Windows: it returns **False** for a
    directory junction, while ``os.path.realpath`` resolves one. Checking only
    ``islink`` there would refuse the *privileged* redirect and accept the
    unprivileged one — creating a symlink needs
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
    — the form ``%TEMP%`` takes on a GitHub runner — was reported as "contains a
    symlink" when it contains none. That misdiagnosis took down every Windows
    test that opens a hub.

    Testing each component is the property that was actually meant, and it
    names the offending component instead of leaving the caller to infer it.
    A component that does not exist yet is not a symlink; ``create=True`` opens
    rely on that.

    The old comparison did catch one thing a bare ``islink`` walk does not: a
    Windows **junction**, which ``realpath`` resolves and ``islink`` reports as
    False. Dropping that would have been a straight downgrade, since a junction
    is the redirect an unprivileged account can create — see ``_is_redirect``.
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
        # file, so every check below would refuse every file — which is what
        # made both Windows shards fail with "must be mode 600" on a hub this
        # process had just created. The ownership check above is already
        # POSIX-only for the same reason, and the DACL that does carry the
        # answer is why `open()` fails closed outside the config directory.
        return info
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise TrustedSQLiteLocationError(
            f"SQLite file {path} is owned by uid {info.st_uid}, not this user."
        )
    if private and stat.S_IMODE(info.st_mode) & 0o077:
        raise TrustedSQLiteLocationError(
            f"SQLite credential file {path} must be mode 600."
        )
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise TrustedSQLiteLocationError(
            f"SQLite file {path} is group/world-writable; refusing to open it."
        )
    return info


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
        allow_windows_app_config: bool = False,
    ) -> "TrustedSQLiteLocation":
        absolute = os.path.abspath(os.path.expanduser(path))
        canonical = os.path.realpath(absolute)
        # A canonical path is used for SQLite, but accepting a symlink in the
        # caller-provided path would make the visible target mutable.
        _reject_symlinked_path(absolute)
        if os.name == "nt" and private:
            # Python exposes neither owner SID nor directory DACL portably.
            # Until a native ACL verifier is available, fail closed for a
            # CREDENTIAL store: the hub holds the password hash and every token
            # hash, and it lives in PixlStash's own per-user configuration
            # directory, so requiring that location costs nothing.
            #
            # Non-credential opens (the vault: picture metadata, in a folder the
            # user chose) deliberately do NOT fail closed here. See "Windows,
            # and what this cannot check" in the module docstring — applying
            # this to the vault made `Vault.__init__` raise for every path on
            # Windows, which is not a control, it is an outage.
            app_config = os.path.realpath(user_config_dir("pixlstash"))
            try:
                in_app_config = (
                    os.path.commonpath((canonical, app_config)) == app_config
                )
            except ValueError:
                in_app_config = False
            if not (allow_windows_app_config and in_app_config):
                raise TrustedSQLiteLocationError(
                    "Cannot verify the Windows DACL for this custom SQLite "
                    "credential location; refusing a security-sensitive open."
                )
        _validate_namespace(canonical)
        if create and not os.path.lexists(canonical):
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                created_fd = os.open(canonical, flags, 0o600)
            except FileExistsError:
                pass
            except OSError as exc:
                raise TrustedSQLiteLocationError(
                    f"Could not securely create SQLite file {canonical}: {exc}"
                ) from exc
            else:
                os.close(created_fd)
        parent_identity = _identity(os.lstat(os.path.dirname(canonical)))
        expected = _validate_file(canonical, private=private)
        for suffix in _SIDECAR_SUFFIXES:
            sidecar = canonical + suffix
            if os.path.lexists(sidecar):
                _validate_file(sidecar, private=private)

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
        for suffix in _SIDECAR_SUFFIXES:
            sidecar = self.path + suffix
            if os.path.lexists(sidecar):
                _validate_file(sidecar, private=self.private)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> "TrustedSQLiteLocation":
        return self

    def __exit__(self, *_args) -> None:
        self.close()
