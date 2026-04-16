"""Path mapping utility for translating host-side paths to container-side paths."""

import os


class PathMapper:
    """Translates host-side path prefixes to mounted container paths.

    Attributes:
        _mappings: Ordered list of (host_prefix, container_prefix) pairs,
            longest host prefix first for deterministic matching.

    Example:
        mapper = PathMapper({"/mnt/photos": "/data/photos"})
        mapper.resolve("/mnt/photos/vacation/img.jpg")
        # -> "/data/photos/vacation/img.jpg"
    """

    def __init__(self, mappings: dict[str, str] | None = None) -> None:
        """Initialize the PathMapper.

        Args:
            mappings: Dict mapping host path prefixes to container paths.
                Keys and values should be absolute directory paths.
        """
        raw = mappings or {}
        # Sort longest prefix first so more-specific mappings take priority.
        self._mappings: list[tuple[str, str]] = sorted(
            (
                (os.path.normpath(host), os.path.normpath(container))
                for host, container in raw.items()
            ),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )

    def resolve(self, path: str) -> str:
        """Translate a host path to its container equivalent.

        If no mapping prefix matches, the path is returned unchanged.

        Args:
            path: Absolute file or directory path (host-side).

        Returns:
            The container-side path if a mapping prefix matches,
            otherwise the original path.
        """
        norm = os.path.normpath(path)
        for host_prefix, container_prefix in self._mappings:
            if norm == host_prefix or norm.startswith(host_prefix + os.sep):
                rel = os.path.relpath(norm, host_prefix)
                return os.path.join(container_prefix, rel)
        return path

    def has_mappings(self) -> bool:
        """Return True if any path mappings are configured."""
        return bool(self._mappings)
