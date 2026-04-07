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
        traversal — for example, integer IDs formatted into a fixed filename
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
    if resolved != safe_base and not resolved.startswith(safe_base + os.sep):
        raise ValueError(
            f"Path would escape allowed directory: {segments!r} is not within {base_dir!r}"
        )
    return resolved
