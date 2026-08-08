"""Path safety utilities for server-side file I/O."""

import os
import threading
from typing import Callable, Iterable

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


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

    The lexical check (``normpath``) neutralises ``..`` components without
    resolving symlinks, so a library whose *content* is reached through a
    symlink is not refused.  The ``realpath`` check then additionally accepts a
    path spelled through a different alias of the same directory (e.g. a
    symlinked root).  A symlink planted *inside* an allowed root that points
    outside it is therefore accepted — planting one requires filesystem write
    access, which is outside this containment's threat model (a substituted
    database row).
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


# ---------------------------------------------------------------------------
# Allowed picture roots
#
# A stored ``Picture.file_path`` may legitimately live under the vault's
# image_root (relative paths) or under any configured reference folder
# (absolute paths; see the ``reference_folder`` table).  The reference-folder
# list lives in the database, which ``resolve_picture_path`` cannot query
# directly, so each Vault registers a provider here keyed by its image_root.
# Roots are cached; a containment miss triggers one refresh before refusing,
# so a freshly added reference folder is honoured immediately.
# ---------------------------------------------------------------------------

_roots_lock = threading.Lock()
_reference_roots_providers: dict[str, Callable[[], Iterable[str]]] = {}
_reference_roots_cache: dict[str, tuple[str, ...]] = {}


def _roots_key(image_root: str) -> str:
    return os.path.normcase(os.path.normpath(image_root))


def register_reference_roots_provider(
    image_root: str, provider: Callable[[], Iterable[str]]
) -> None:
    """Register *provider* as the source of reference-folder roots for a vault.

    Args:
        image_root: The vault's image root (the key callers of
            ``is_allowed_picture_path`` pass).
        provider: Zero-argument callable returning the current reference-folder
            root directories.  Called lazily, only when a path is not already
            under a known root.
    """
    key = _roots_key(image_root)
    with _roots_lock:
        _reference_roots_providers[key] = provider
        _reference_roots_cache.pop(key, None)


def unregister_reference_roots_provider(
    image_root: str, provider: Callable[[], Iterable[str]] | None = None
) -> None:
    """Remove the provider (and cached roots) registered for *image_root*.

    When *provider* is given, only that exact provider is removed — so a
    closing vault cannot unhook a newer vault that has since registered for
    the same image root.
    """
    key = _roots_key(image_root)
    with _roots_lock:
        # Equality, not identity: bound methods are recreated on each attribute
        # access, but compare equal when they wrap the same function and object.
        if provider is not None and _reference_roots_providers.get(key) != provider:
            return
        _reference_roots_providers.pop(key, None)
        _reference_roots_cache.pop(key, None)


def is_allowed_picture_path(image_root: str, abs_path: str) -> bool:
    """Whether *abs_path* lies under the vault's legitimate picture roots.

    The legitimate root set is ``image_root`` plus every configured
    reference-folder root (both the stored host-side form and its path-mapped
    container-side form, as supplied by the registered provider).

    Args:
        image_root: The vault's image root.
        abs_path: An absolute stored picture path to check.

    Returns:
        True when the path is contained in a legitimate root.  False when it is
        not — including when no provider is registered for *image_root*, in
        which case only ``image_root`` itself is honoured.
    """
    if path_is_within(abs_path, image_root):
        return True
    key = _roots_key(image_root) if image_root else ""
    with _roots_lock:
        cached = _reference_roots_cache.get(key)
        provider = _reference_roots_providers.get(key)
    if cached is not None and any(path_is_within(abs_path, root) for root in cached):
        return True
    if provider is None:
        return False
    try:
        fresh = tuple(root for root in provider() if root)
    except Exception as exc:
        logger.warning(
            "Could not refresh reference-folder roots for image_root=%s "
            "while checking %s: %s — the path is refused against the "
            "last-known roots only",
            image_root,
            abs_path,
            exc,
        )
        return False
    with _roots_lock:
        _reference_roots_cache[key] = fresh
    return any(path_is_within(abs_path, root) for root in fresh)
