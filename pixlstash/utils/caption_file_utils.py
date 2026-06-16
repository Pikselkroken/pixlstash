"""Utilities for reading, writing, and detecting sidecar caption files.

A sidecar file sits next to an image and carries either comma-separated **tags**
(training-data convention) or a free-form **description**.  Unlike the original
single-file scheme, PixlStash now treats tags and descriptions as two
independent sidecars, each with its own filename *suffix* applied to the image
stem:

    image.png  ->  image_tags.txt          (tags,        suffix "_tags.txt")
    image.png  ->  image_description.txt    (description, suffix "_description.txt")

Suffixes are configurable per reference folder.  When a folder has no explicit
suffix configured, known conventions are probed and each candidate file is
classified by its name (and, for an ambiguous bare ``.txt``, by its content).
"""

import os
import re
import tempfile
from collections import Counter

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# Sidecar type identifiers.
SIDECAR_TYPE_TAGS = "tags"
SIDECAR_TYPE_DESCRIPTION = "description"

# Default suffixes used when creating a brand-new sidecar for a folder that has
# no explicit suffix configured and no existing convention to inherit.
DEFAULT_TAGS_SUFFIX = "_tags.txt"
DEFAULT_DESCRIPTION_SUFFIX = "_description.txt"

# Known suffixes probed (in priority order) when a folder has no suffix
# configured for the given type.  ``.txt`` is last for both because it is
# ambiguous and only kept after a content check.
_KNOWN_SUFFIXES = {
    SIDECAR_TYPE_TAGS: ("_tags.txt", "_tag.txt", "_wd14.txt", ".txt"),
    SIDECAR_TYPE_DESCRIPTION: (
        "_description.txt",
        "_desc.txt",
        "_caption.txt",
        "_prompt.txt",
        ".caption",
        ".txt",
    ),
}

# Filename-suffix patterns that unambiguously indicate a type (matched against
# the suffix that follows the image stem, e.g. ``_tags.txt``).
_TAGS_NAME_RE = re.compile(r"_(tags?|wd14|booru)\.txt$", re.IGNORECASE)
_DESCRIPTION_NAME_RE = re.compile(
    r"(_(description|desc|caption|prompt)\.txt|\.caption)$", re.IGNORECASE
)


def get_sidecar_mtime(path: str) -> float | None:
    """Return the modification time of *path* as a Unix timestamp, or ``None``."""
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def sidecar_path(image_path: str, suffix: str) -> str:
    """Return the sidecar path for *image_path* using *suffix*.

    The image extension is stripped and *suffix* appended, so
    ``("photo.png", "_tags.txt")`` -> ``"photo_tags.txt"`` and
    ``("photo.png", ".txt")`` -> ``"photo.txt"``.
    """
    return os.path.splitext(image_path)[0] + suffix


def classify_sidecar(path: str) -> str | None:
    """Classify a sidecar file as tags or a description.

    Filename first: an unambiguous suffix (``_tags.txt``, ``_description.txt``,
    ``.caption`` …) decides immediately.  An ambiguous bare ``.txt`` falls back
    to a content sniff (comma-separated short tokens -> tags; prose ->
    description; tags when unsure).

    Returns ``SIDECAR_TYPE_TAGS`` / ``SIDECAR_TYPE_DESCRIPTION``, or ``None`` if
    the file cannot be read.
    """
    name = os.path.basename(path)
    if _TAGS_NAME_RE.search(name):
        return SIDECAR_TYPE_TAGS
    if _DESCRIPTION_NAME_RE.search(name):
        return SIDECAR_TYPE_DESCRIPTION

    # Ambiguous (bare ``.txt`` or unknown) — decide by content.
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError as exc:
        logger.warning("Could not read sidecar %s for classification: %s", path, exc)
        return None
    return SIDECAR_TYPE_TAGS if _looks_like_tags(raw) else SIDECAR_TYPE_DESCRIPTION


def resolve_typed_sidecar(
    image_path: str, sidecar_type: str, configured_suffix: str | None
) -> str | None:
    """Return the existing sidecar path of *sidecar_type* for *image_path*.

    When *configured_suffix* is set, the file at exactly that suffix is used.
    Otherwise the known suffixes for the type are probed in priority order and
    the first existing file that *classifies* as the requested type is returned
    (so a bare ``.txt`` is only treated as tags when its content looks like
    tags, and as a description otherwise).  Returns ``None`` when nothing exists.
    """
    if configured_suffix:
        candidate = sidecar_path(image_path, configured_suffix)
        return candidate if os.path.isfile(candidate) else None

    for suffix in _KNOWN_SUFFIXES.get(sidecar_type, ()):  # type: ignore[arg-type]
        candidate = sidecar_path(image_path, suffix)
        if not os.path.isfile(candidate):
            continue
        # Unambiguous suffixes classify by name; the trailing ".txt" needs the
        # content check so it is only claimed by the matching type.
        if classify_sidecar(candidate) == sidecar_type:
            return candidate
    return None


def writeback_path(
    image_path: str,
    sidecar_type: str,
    configured_suffix: str | None,
    existing_path: str | None,
) -> str:
    """Return the path to write a sidecar of *sidecar_type* to.

    Prefers an already-resolved *existing_path*; otherwise builds one from the
    *configured_suffix* (or the module default for the type).
    """
    if existing_path:
        return existing_path
    suffix = configured_suffix or (
        DEFAULT_TAGS_SUFFIX
        if sidecar_type == SIDECAR_TYPE_TAGS
        else DEFAULT_DESCRIPTION_SUFFIX
    )
    return sidecar_path(image_path, suffix)


def read_tags_sidecar(path: str) -> list[str]:
    """Read a tags sidecar into a normalised, de-duplicated list of tags."""
    raw = _read_text(path)
    return _parse_txt_tags(raw) if raw is not None else []


def read_description_sidecar(path: str) -> str | None:
    """Read a description sidecar into stripped text, or ``None`` when empty."""
    raw = _read_text(path)
    if raw is None:
        return None
    text = raw.strip()
    return text or None


def write_sidecar(path: str, content: str) -> float | None:
    """Write *content* to *path* atomically (temp file + rename).

    Returns the modification time of the written file as a Unix timestamp, or
    ``None`` on failure.  Callers should persist this so the next folder scan
    does not re-import their own write-back as an external change.

    Writing is skipped when the file already holds exactly *content*, so a
    no-op write-back (e.g. tags changed but the description did not) leaves the
    file's mtime untouched.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            if fh.read() == content:
                return get_sidecar_mtime(path)
    except OSError:
        # Missing or unreadable — fall through and (re)write it.
        pass

    dir_path = os.path.dirname(path)
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                # Best effort to clean up the temp file; ignore errors.
                pass
            raise
    except OSError as exc:
        logger.warning("Could not write sidecar %s: %s", path, exc)
        return None
    return get_sidecar_mtime(path)


def detect_folder_suffixes(folder: str, sample_limit: int = 200) -> dict:
    """Infer the sidecar naming convention already in use inside *folder*.

    Walks the folder, matches each sidecar text file to its image, derives the
    suffix that follows the image stem, classifies the sidecar (filename then
    content), and returns the most common suffix observed for each type.

    Returns a dict ``{"tags_suffix", "description_suffix", "found_tags",
    "found_descriptions"}``; the suffix values are ``None`` when that type was
    not found.
    """
    image_stems: set[str] = set()
    sidecar_files: list[str] = []
    seen_images = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            full = os.path.join(root, name)
            ext = os.path.splitext(name)[1].lower()
            if ext in _IMAGE_EXTS_FOR_DETECTION:
                image_stems.add(os.path.splitext(full)[0])
                seen_images += 1
            elif ext in _SIDECAR_EXTS_FOR_DETECTION:
                sidecar_files.append(full)
        if seen_images >= sample_limit:
            break

    tag_suffixes: Counter = Counter()
    desc_suffixes: Counter = Counter()
    for sidecar in sidecar_files:
        suffix = _suffix_for_sidecar(sidecar, image_stems)
        if suffix is None:
            continue
        kind = classify_sidecar(sidecar)
        if kind == SIDECAR_TYPE_TAGS:
            tag_suffixes[suffix] += 1
        elif kind == SIDECAR_TYPE_DESCRIPTION:
            desc_suffixes[suffix] += 1

    tags_suffix = tag_suffixes.most_common(1)[0][0] if tag_suffixes else None
    description_suffix = desc_suffixes.most_common(1)[0][0] if desc_suffixes else None
    return {
        "tags_suffix": tags_suffix,
        "description_suffix": description_suffix,
        "found_tags": bool(tag_suffixes),
        "found_descriptions": bool(desc_suffixes),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_IMAGE_EXTS_FOR_DETECTION = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif", ".avif", ".gif"}
)
_SIDECAR_EXTS_FOR_DETECTION = frozenset({".txt", ".caption"})


def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        logger.warning("Could not read sidecar %s: %s", path, exc)
        return None


def _suffix_for_sidecar(sidecar_path_str: str, image_stems: set[str]) -> str | None:
    """Return the suffix of *sidecar_path_str* relative to its image stem.

    Matches the longest image stem that is a prefix of the sidecar path (so
    ``foo_tags.txt`` is read as ``foo_tags`` + ``.txt`` when an image
    ``foo_tags.png`` exists, otherwise as ``foo`` + ``_tags.txt``).  Returns
    ``None`` when no image in the folder owns this sidecar.
    """
    base = os.path.splitext(sidecar_path_str)[0]
    best_stem: str | None = None
    for stem in image_stems:
        if base == stem or base.startswith(stem):
            if best_stem is None or len(stem) > len(best_stem):
                best_stem = stem
    if best_stem is None:
        return None
    return sidecar_path_str[len(best_stem):]


def _looks_like_tags(text: str) -> bool:
    """Heuristic: does *text* read as a comma-separated tag list (vs prose)?

    Used only to disambiguate a bare ``.txt`` sidecar.  Defaults to tags when
    unsure (the WD14 training convention).
    """
    t = (text or "").strip()
    if not t:
        return True

    parts = [p.strip() for p in re.split(r"[,\n]+", t) if p.strip()]
    # Prose typically ends sentences with punctuation mid-text or at the end.
    has_sentence_punct = bool(re.search(r"[.!?](\s|$)", t))
    word_count = len(t.split())

    # Many short comma/newline chunks with no sentence punctuation -> tags.
    if len(parts) >= 3:
        avg_words = sum(len(p.split()) for p in parts) / len(parts)
        if avg_words <= 4 and not has_sentence_punct:
            return True

    # A long, sentence-like run reads as a description.
    if word_count >= 10:
        return False
    if has_sentence_punct and word_count >= 5:
        return False

    # Short content without prose signals -> tags.
    return not has_sentence_punct


def _parse_txt_tags(raw_text: str) -> list[str]:
    """Parse a comma-separated tag string into a deduplicated, normalised list."""
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
