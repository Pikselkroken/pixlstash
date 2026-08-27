"""Path safety utilities for server-side file I/O."""

import os


def resolve_path_within(base_dir: str, *segments: str) -> str:
    """Resolve a path and confirm it remains strictly within *base_dir*.

    Args:
        base_dir: The permitted root directory.
        *segments: Path segments to join under *base_dir*. These may contain
            user-supplied values (e.g. filenames from HTTP requests or DB
            rows) and must not escape the root even through ``..`` components
            or symbolic links.

    Returns:
        The resolved absolute path.

    Raises:
        ValueError: If the resolved path would escape *base_dir*.

    Note:
        Some call sites pass values that are structurally incapable of path
        traversal - for example, integer IDs formatted into a fixed filename
        template such as ``f"character_{id}.png"`` where FastAPI has already
        validated the ``int`` type.  Those uses are redundant from a security
        standpoint but are kept intentionally so that CodeQL's taint-tracking
        analysis sees a recognised sanitizer at every path-construction site
        and does not emit false-positive findings that would need to be
        manually dismissed.
    """
    joined = os.path.join(base_dir, *segments)
    resolved = os.path.realpath(joined)
    safe_base = os.path.realpath(base_dir)
    try:
        common = os.path.commonpath([resolved, safe_base])
    except ValueError as exc:
        # Different drives / roots (not within base).
        raise ValueError(
            f"Path would escape allowed directory: {segments!r} is not within {base_dir!r}"
        ) from exc

    if common != safe_base:
        raise ValueError(
            f"Path would escape allowed directory: {segments!r} is not within {base_dir!r}"
        )
    return resolved


def path_is_within(path: str, base: str) -> bool:
    """Whether *path* lies within *base*, lexically or after symlink resolution.

    Unlike :func:`resolve_path_within` this answers a question instead of
    raising, and it does not rewrite the path. Use it where a caller holds an
    already-absolute path and must decide whether to act on it.

    The lexical check (``normpath``) neutralises ``..`` components without
    resolving symlinks, so a library whose *content* is reached through a
    symlink is not refused. The ``realpath`` check then additionally accepts a
    path spelled through a different alias of the same directory (e.g. a
    symlinked root). A symlink planted *inside* an allowed root that points
    outside it is therefore accepted: planting one requires filesystem write
    access, which is a bigger problem than this check is for.

    Args:
        path: The path to test. An empty value is never within anything.
        base: The directory *path* must be under.

    Returns:
        True when *path* is contained in *base*.
    """
    if not path or not base:
        return False
    try:
        norm_path = os.path.normcase(os.path.normpath(path))
        norm_base = os.path.normcase(os.path.normpath(base))
        if os.path.commonpath([norm_path, norm_base]) == norm_base:
            return True
        real_path = os.path.normcase(os.path.realpath(path))
        real_base = os.path.normcase(os.path.realpath(base))
        return os.path.commonpath([real_path, real_base]) == real_base
    except ValueError:
        # Mixed absolute/relative paths or different drives: not within.
        return False
