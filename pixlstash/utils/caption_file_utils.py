"""Utilities for reading and writing sidecar caption files.

Sidecar files sit next to an image file and carry either comma-separated tags
(``image.txt``) or a free-form description (``image.caption``).  PixlStash
checks for both at scan time and writes back to whichever was found (or
creates a ``{stem}.txt`` when none existed yet and write-back is enabled).
"""

import os
import tempfile

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# Ordered by preference: .txt is the dominant training-data convention.
_SIDECAR_EXTENSIONS = (".txt", ".caption")


def find_caption_file(image_path: str) -> str | None:
    """Return the path of the first sidecar file found alongside *image_path*.

    Checks for ``{stem}.txt`` then ``{stem}.caption`` in the same directory.
    Returns ``None`` when neither exists.

    Args:
        image_path: Absolute path to the image file.

    Returns:
        Absolute path to the sidecar file, or ``None``.
    """
    stem = os.path.splitext(image_path)[0]
    for ext in _SIDECAR_EXTENSIONS:
        candidate = stem + ext
        if os.path.isfile(candidate):
            return candidate
    return None


def get_caption_file_mtime(caption_path: str) -> float | None:
    """Return the modification time of *caption_path* as a Unix timestamp.

    Uses a single ``os.stat()`` call.  Returns ``None`` on any OS error.

    Args:
        caption_path: Absolute path to the sidecar file.

    Returns:
        Modification time as a float, or ``None``.
    """
    try:
        return os.stat(caption_path).st_mtime
    except OSError:
        return None


def read_caption_file(caption_path: str) -> tuple[list[str], str | None]:
    """Parse a sidecar caption file into tags and an optional description.

    A ``.txt`` file is treated as comma-separated tags (the standard training
    dataset format).  A ``.caption`` file is treated as a free-form description
    and returned as-is with an empty tag list.

    Args:
        caption_path: Absolute path to the sidecar file.

    Returns:
        A ``(tags, description)`` tuple.  ``tags`` is a (possibly empty) list
        of normalised tag strings.  ``description`` is the raw text for
        ``.caption`` files, or ``None`` for ``.txt`` files.
    """
    try:
        with open(caption_path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError as exc:
        logger.warning("Could not read caption file %s: %s", caption_path, exc)
        return [], None

    ext = os.path.splitext(caption_path)[1].lower()

    if ext == ".caption":
        text = raw.strip()
        return [], text if text else None

    # .txt — comma-separated tags
    tags = _parse_txt_tags(raw)
    return tags, None


def write_caption_file(
    caption_path: str, tags: list[str], description: str | None = None
) -> float | None:
    """Write tags (and optionally a description) back to a sidecar file atomically.

    For ``.txt`` files the content is the comma-separated tag list.
    For ``.caption`` files the content is *description* (tags are ignored).
    Uses a temp-file + rename so the file is never left in a half-written state.

    Args:
        caption_path: Absolute path to the sidecar file to write.
        tags: Tag strings to serialise (used for ``.txt``).
        description: Free-form description (used for ``.caption``).

    Returns:
        The modification time of the written file as a Unix timestamp float,
        or ``None`` on failure.  Callers should persist this value as
        ``caption_file_mtime`` so the next scan does not wrongly re-import
        the file as an external change.
    """
    ext = os.path.splitext(caption_path)[1].lower()

    if ext == ".caption":
        content = (description or "").strip()
    else:
        content = ", ".join(tags)

    dir_path = os.path.dirname(caption_path)
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp_path, caption_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                # Best effort to clean up the temp file, but ignore any errors since it's not critical.
                pass
            raise
    except OSError as exc:
        logger.warning("Could not write caption file %s: %s", caption_path, exc)
        return None

    return get_caption_file_mtime(caption_path)


def default_caption_path(image_path: str) -> str:
    """Return the default ``{stem}.txt`` sidecar path for *image_path*.

    Used when no sidecar exists yet but write-back is enabled.

    Args:
        image_path: Absolute path to the image file.

    Returns:
        Path with the image extension replaced by ``.txt``.
    """
    return os.path.splitext(image_path)[0] + ".txt"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_txt_tags(raw_text: str) -> list[str]:
    """Parse a comma-separated tag string into a deduplicated list."""
    text = (raw_text or "").strip()
    if not text:
        return []

    parts = [p.strip() for p in text.replace("\n", ",").split(",")]
    seen: set[str] = set()
    result: list[str] = []
    for raw_tag in parts:
        normalised = " ".join(raw_tag.replace("_", " ").lower().split())
        if normalised and normalised not in seen:
            seen.add(normalised)
            result.append(normalised)
    return result
