"""Utilities for validating host-side filesystem paths.

These helpers treat both POSIX and Windows path formats as valid host paths,
which is required when a Linux container receives Windows host paths from the
frontend in Docker setups.
"""

import ntpath
import os


def is_absolute_host_path(path: str) -> bool:
    """Return True when *path* is absolute on either POSIX or Windows.

    This intentionally supports Windows drive/UNC paths even when the server
    itself runs on Linux inside Docker.
    """

    return os.path.isabs(path) or ntpath.isabs(path)


def normalize_host_path(path: str) -> str:
    """Normalize a host path while preserving the platform semantics.

    For Windows paths (e.g. ``C:\\Users\\name``) received by a Linux
    container, ``ntpath.normpath`` keeps backslash/drive semantics intact.
    POSIX paths continue to use ``os.path.normpath``.
    """

    if ntpath.isabs(path) and not os.path.isabs(path):
        return ntpath.normpath(path)
    return os.path.normpath(path)
