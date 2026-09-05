"""Path validation helpers for reference folders."""

import os
import sys

# Paths that must never be used as reference folder roots.
# Applied on both Linux and macOS; extended lists handle platform differences.
_LINUX_BLOCKLIST: frozenset[str] = frozenset(
    [
        "/proc",
        "/sys",
        "/dev",
        "/run",
        "/etc",
        "/boot",
        "/usr",
        "/bin",
        "/sbin",
        "/lib",
        "/lib64",
        "/lib32",
        "/libx32",
        "/snap",
    ]
)

_MACOS_BLOCKLIST: frozenset[str] = frozenset(
    [
        "/private",
        "/System",
        "/Library",
        "/Applications",
        "/usr",
        "/bin",
        "/sbin",
        "/etc",
        "/dev",
        "/cores",
        "/Volumes/Recovery",
    ]
)

# Prefixes that a *resolved* path legitimately lands under even though a
# blocklist entry contains them. The blocklist was written for the strings
# people type; resolving turns three ordinary locations into system ones:
# macOS resolves `/tmp` to `/private/tmp` and `$TMPDIR` to
# `/private/var/folders/...`, both of which `/private` would then swallow - and
# `/private` has to stay, because it is the only entry that catches `/etc`
# once that resolves to `/private/etc`. FreeBSD and TrueNAS ship `/home` as a
# symlink to `/usr/home`, which `/usr` would swallow, stranding the whole
# `/tmp/x` and `/home/me/x` were accepted before paths were resolved and stay
# accepted. Their resolved spellings - `/private/tmp/x`, `/usr/home/me/x` - were
# refused when typed literally, and now pass, so this is a small widening rather
# than a pure restoration: it makes one directory answer the same way under both
# of its names, which is what resolving is for.
_RESOLVED_ALIASES: frozenset[str] = frozenset(
    [
        "/private/tmp",
        "/private/var/folders",
        "/usr/home",
    ]
)

_WINDOWS_BLOCKLIST: frozenset[str] = frozenset(
    [
        "C:\\Windows",
        "C:\\Program Files",
        "C:\\Program Files (x86)",
    ]
)


def _get_blocklist() -> frozenset[str]:
    if sys.platform.startswith("win"):
        return _WINDOWS_BLOCKLIST
    if sys.platform == "darwin":
        return _LINUX_BLOCKLIST | _MACOS_BLOCKLIST
    return _LINUX_BLOCKLIST


def validate_reference_folder_path(path: str) -> str | None:
    """Validate a proposed reference folder path.

    Args:
        path: Candidate folder path supplied by the user.

    Returns:
        An error message string if the path is invalid, or ``None`` if it
        passes all Phase-1 checks (absolute + not on the blocklist).
    """
    if not os.path.isabs(path):
        return "Path must be absolute."

    # Resolved before it is compared, because the blocklist is literal. Checking
    # the string as given lets a symlink the caller owns through: `~/to-etc`
    # matches no entry, and the route behind this check then operates on `/etc`.
    # Callers pass every shape - some resolve first, most do not - so the
    # resolution belongs here rather than at each call site, which is how seven
    # of them came to be missing it. `realpath` never raises: an unmounted or
    # not-yet-created path resolves as far as it exists, which keeps the Docker
    # pending-mount callers working.
    norm = os.path.normcase(os.path.realpath(os.path.normpath(path)))

    for alias in _RESOLVED_ALIASES:
        alias_norm = os.path.normcase(os.path.normpath(alias))
        if norm == alias_norm or norm.startswith(alias_norm + os.sep):
            return None

    for blocked in _get_blocklist():
        # Both sides are case-folded, or `c:\windows` walks past an entry
        # spelled `C:\Windows`. `normcase` is identity on POSIX.
        blocked_norm = os.path.normcase(os.path.normpath(blocked))
        if norm == blocked_norm or norm.startswith(blocked_norm + os.sep):
            return f"Path is in a restricted system directory: {blocked}"

    return None


def validate_reference_folder_accessible(path: str) -> str | None:
    """Check that a reference folder path is readable at scan time.

    Args:
        path: Resolved (container-side) folder path.

    Returns:
        An error message string if the path is inaccessible, or ``None`` when
        the path is a readable directory.
    """
    if not os.path.isabs(path):
        return "Path must be absolute."

    # Canonicalize before touching the filesystem so checks operate on a
    # normalized, symlink-resolved path.
    secured_path = os.path.realpath(os.path.normpath(path))
    restricted_error = validate_reference_folder_path(secured_path)
    if restricted_error:
        return restricted_error

    if not os.path.isdir(secured_path):
        return f"Path is not a directory or does not exist: {secured_path}"
    if not os.access(secured_path, os.R_OK | os.X_OK):
        return f"Path is not readable: {secured_path}"
    return None
