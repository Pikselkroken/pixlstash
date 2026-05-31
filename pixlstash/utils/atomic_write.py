"""Atomic file-write helpers.

Writing config via ``open(path, "w")`` truncates the file at open, so a crash
or power loss mid-write leaves a zero-length or half-written file.  For
``server-config.json`` that is fatal: the server cannot parse it on the next
boot.  These helpers stage the new content in a sibling temp file, fsync it,
then ``os.replace`` it over the target (an atomic rename on POSIX and Windows),
and fsync the parent directory so the rename itself survives a crash — matching
the durability the restore subsystem already uses for the DB swap.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

logger = logging.getLogger(__name__)


def write_json_atomic(path: str, data: Any, *, indent: int = 2) -> None:
    """Serialise *data* as JSON and write it to *path* atomically.

    The file at *path* is either fully replaced with the new content or left
    untouched — it is never observed truncated or half-written, even if the
    process crashes mid-write.

    Args:
        path: Destination file path. Its parent directory must already exist.
        data: JSON-serialisable object.
        indent: ``json.dump`` indent (defaults to 2 to match existing config
            files).

    Raises:
        OSError: If the temp file cannot be written or the replace fails.
        TypeError / ValueError: If *data* is not JSON-serialisable (raised
            before the target is touched, so the existing file is preserved).
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=indent)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            logger.warning(
                "write_json_atomic: failed to clean up temp file %s", tmp_path
            )
        raise

    # fsync the directory so the rename is durable across a crash/power loss.
    # Best-effort: directory fsync isn't supported on every platform/filesystem
    # (e.g. Windows), but the os.replace above is already atomic without it.
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        logger.debug(
            "write_json_atomic: directory fsync skipped for %s: %s", directory, exc
        )
