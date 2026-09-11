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
from collections.abc import Collection

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.video_utils import VIDEO_EXTENSIONS
from pixlstash.utils.media_files import SUPPORTED_IMAGE_EXTS, is_hidden_entry

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

# A sidecar suffix is appended directly to an image's path stem to locate and
# write its sidecar (see ``sidecar_path``), so it must stay a bare filename
# fragment. A value carrying a path separator or ".." would redirect the write
# outside the image's own directory (CWE-22 path traversal -> arbitrary file
# write). This is the single definition of that rule: the API validates it at
# the trust boundary, the folder scan validates it before persisting a detected
# suffix, and ``sidecar_path`` enforces it again at the point of use.
_SAFE_SUFFIX_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# A suffix ending in a media extension names a picture, not a caption:
# ``photo.jpg`` + ``.png`` is ``photo.png``, and a write-back there replaces
# a picture with text.
_MEDIA_EXTS = tuple(sorted(SUPPORTED_IMAGE_EXTS | frozenset(VIDEO_EXTENSIONS)))


def is_safe_sidecar_suffix(suffix: str | None) -> bool:
    """Return True when *suffix* is a bare filename fragment safe to append.

    Args:
        suffix: The candidate sidecar suffix, e.g. ``"_tags.txt"``.

    Returns:
        True when appending *suffix* to an image stem cannot leave the image's
        directory and cannot name a picture or video; False for empty values,
        ``..``, any path separator, or a media extension.
    """
    if not suffix or ".." in suffix:
        return False
    if "/" in suffix or "\\" in suffix:
        return False
    if os.sep in suffix or (os.altsep and os.altsep in suffix):
        return False
    if suffix.lower().endswith(_MEDIA_EXTS):
        return False
    return bool(_SAFE_SUFFIX_RE.match(suffix))


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

    *suffix* must be a bare filename fragment (``is_safe_sidecar_suffix``). The
    API validates this at the trust boundary and the folder scan validates a
    detected suffix before persisting it; this is the defence-in-depth guard at
    the point of use, so a malicious suffix can never redirect a read/write
    outside the image's own directory (path traversal to arbitrary file write).

    The check is on the suffix rather than on the resulting path: a fragment
    with no separator provably cannot leave the directory, whereas comparing
    the joined path's dirname is blind to symlinks in the traversal.

    Raises:
        ValueError: If *suffix* is not a bare filename fragment.
    """
    if not is_safe_sidecar_suffix(suffix):
        raise ValueError(f"Sidecar suffix escapes the image directory: {suffix!r}")
    return os.path.splitext(image_path)[0] + suffix


def classify_sidecar(path: str) -> str | None:
    """The kind `sniff_caption` reads out of *path*, or ``None`` for a non-caption."""
    sniffed = sniff_caption(path)
    return sniffed and sniffed[0]


#: How much of a caption file the sniff reads. A caption is a line or a
#: paragraph; anything that needs more than this to classify is not one.
_SNIFF_BYTES = 4096
_EXCERPT_CHARS = 100


def sniff_caption(path: str) -> tuple[str, str] | None:
    """Classify one caption file and say what it starts with.

    The one place a kind is decided from a file: the folder read's caption
    card and the suffix probe both come through here, so what the owner is
    shown and what the import reads agree by construction.

    Returns ``(kind, excerpt)`` with *kind* ``"tags"`` or ``"description"``
    and *excerpt* the first line or so, whitespace collapsed. ``None`` when
    the file is not a caption at all: unreadable, binary (a NUL byte in the
    head), JSON or markup (a generator's metadata, an XMP sidecar), or empty
    without a name that says what it is. An unambiguous name (``_tags.txt``,
    ``.caption`` ...) decides before the content does, an empty file
    included; a bare ``.txt`` is decided by `_looks_like_tags`.
    """
    name = os.path.basename(path)
    try:
        with open(path, "rb") as fh:
            head = fh.read(_SNIFF_BYTES)
    except OSError as exc:
        logger.warning("Could not read caption file %s: %s", path, exc)
        return None
    if b"\x00" in head:
        return None
    # utf-8-sig: a BOM would otherwise survive .strip() and become part of
    # the first tag.
    text = head.decode("utf-8-sig", errors="replace").strip()
    if text[:1] in ("{", "[", "<"):
        return None
    excerpt = " ".join(text.split())[:_EXCERPT_CHARS]
    if _TAGS_NAME_RE.search(name):
        return SIDECAR_TYPE_TAGS, excerpt
    if _DESCRIPTION_NAME_RE.search(name):
        return SIDECAR_TYPE_DESCRIPTION, excerpt
    if not text:
        return None
    kind = SIDECAR_TYPE_TAGS if _looks_like_tags(text) else SIDECAR_TYPE_DESCRIPTION
    return kind, excerpt


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
        try:
            candidate = sidecar_path(image_path, configured_suffix)
        except ValueError as exc:
            # A folder configured before the suffix rule was enforced on every
            # write path. There is no resolvable sidecar for an unusable
            # suffix; say so loudly rather than wedging the caller's scan.
            logger.warning(
                "Cannot resolve %s sidecar for %s: %s", sidecar_type, image_path, exc
            )
            return None
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
) -> str | None:
    """Return the path to write a sidecar of *sidecar_type* to.

    Prefers an already-resolved *existing_path*; otherwise builds one from the
    *configured_suffix* (or the module default for the type).

    *existing_path* is typically the ``tags_file`` / ``description_file``
    column, so it is a database value and is only honoured when it is exactly
    the image stem plus a safe suffix, the one shape a legitimately recorded
    sidecar path can have. Anything else is ignored (logged) and the standard
    suffix-derived path is used instead, so a fabricated column cannot direct
    a file write somewhere else (#776).

    Returns ``None`` when the folder's configured suffix is not a usable
    filename fragment, so a folder configured before the rule was enforced on
    every write path skips its write-back (logged) instead of raising through
    the caller and wedging a scan or returning a 500. Callers must handle it.
    """
    if existing_path:
        stem = os.path.splitext(image_path)[0]
        tail = existing_path[len(stem) :] if existing_path.startswith(stem) else ""
        if tail and is_safe_sidecar_suffix(tail):
            return existing_path
        logger.warning(
            "Ignoring recorded %s sidecar path %r for %s: not the image "
            "stem plus a safe suffix; using the configured suffix instead",
            sidecar_type,
            existing_path,
            image_path,
        )
    suffix = configured_suffix or (
        DEFAULT_TAGS_SUFFIX
        if sidecar_type == SIDECAR_TYPE_TAGS
        else DEFAULT_DESCRIPTION_SUFFIX
    )
    try:
        return sidecar_path(image_path, suffix)
    except ValueError as exc:
        logger.warning(
            "Skipping %s write-back for %s: %s", sidecar_type, image_path, exc
        )
        return None


def read_caption_text(path: str) -> str | None:
    """The raw text of a caption file, or ``None`` when it could not be read.

    An empty file reads as ``""``, so callers can tell the owner clearing a
    caption from a read that failed.
    """
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        logger.warning("Could not read sidecar %s: %s", path, exc)
        return None


def parse_caption_tags(raw_text: str) -> list[str]:
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


def read_tags_sidecar(path: str) -> list[str]:
    """Read a tags sidecar into a normalised, de-duplicated list of tags."""
    raw = read_caption_text(path)
    return parse_caption_tags(raw) if raw is not None else []


def read_description_sidecar(path: str) -> str | None:
    """Read a description sidecar into stripped text, or ``None`` when empty."""
    raw = read_caption_text(path)
    if raw is None:
        return None
    text = raw.strip()
    return text or None


def _first_existing_sidecar(
    image_path: str, sidecar_type: str, suffixes: list[str] | None
) -> str | None:
    """The sidecar of *sidecar_type* beside *image_path*, by the owner's rule.

    ``None`` probes the known conventions (a bare ``.txt`` is content-sniffed);
    a list is tried in order and the first existing file wins, so ``[]`` reads
    nothing of that kind.
    """
    if suffixes is None:
        return resolve_typed_sidecar(image_path, sidecar_type, None)
    for suffix in suffixes:
        try:
            candidate = sidecar_path(image_path, suffix)
        except ValueError as exc:
            logger.warning("Cannot resolve sidecar for %s: %s", image_path, exc)
            continue
        if os.path.isfile(candidate):
            return candidate
    return None


def attach_sidecars(
    pic,
    file_path: str,
    tags_suffixes: list[str] | None = None,
    description_suffixes: list[str] | None = None,
) -> list[str]:
    """Record the sidecars beside *file_path* on an unsaved picture.

    Sets ``tags_file`` / ``description_file`` and their mtimes when a sidecar
    exists and could be read, fills ``description`` from the description
    sidecar when the picture has none, and returns the tags read. The list is
    empty when there is no tags sidecar and when the file is empty; the caller
    decides what that means (the scan and the local import both fall back to
    the pending-tag sentinel so the tagger runs). A file that would not open
    is not recorded at all, so the next scan tries it again.

    Tags cannot be set on an unsaved `Picture` (they are a relationship), so
    they are stashed on ``pic._sidecar_tags`` for the inserter to persist.

    *tags_suffixes* / *description_suffixes*: ``None`` probes the known
    conventions; a list is the suffixes the owner confirmed for that kind, in
    order of preference; ``[]`` reads nothing of that kind.
    """
    tags: list[str] = []
    tags_path = _first_existing_sidecar(file_path, SIDECAR_TYPE_TAGS, tags_suffixes)
    if tags_path:
        text = read_caption_text(tags_path)
        if text is not None:
            pic.tags_file = tags_path
            pic.tags_file_mtime = get_sidecar_mtime(tags_path)
            tags = parse_caption_tags(text)

    description_path = _first_existing_sidecar(
        file_path, SIDECAR_TYPE_DESCRIPTION, description_suffixes
    )
    if description_path:
        raw = read_caption_text(description_path)
        if raw is not None:
            pic.description_file = description_path
            pic.description_file_mtime = get_sidecar_mtime(description_path)
            if raw.strip() and not pic.description:
                pic.description = raw.strip()

    if tags:
        pic._sidecar_tags = tags  # type: ignore[attr-defined]
    return tags


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
    except OSError as exc:
        # Missing or unreadable - fall through and (re)write it. Logged at DEBUG
        # so it satisfies the no-silent-failure policy without noising up a
        # normal first-time write (there is nothing to read yet).
        logger.debug(
            "Could not read sidecar %s for compare (will rewrite): %s", path, exc
        )

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
            except OSError as cleanup_exc:
                # Best effort to clean up the temp file; record at DEBUG and
                # still re-raise the original write failure below.
                logger.debug(
                    "Could not remove temp sidecar %s after write failure: %s",
                    tmp_path,
                    cleanup_exc,
                )
            raise
    except OSError as exc:
        logger.warning("Could not write sidecar %s: %s", path, exc)
        return None
    return get_sidecar_mtime(path)


def detect_folder_suffixes(
    folder: str, sample_limit: int = 200, skip_dirs: Collection[str] = ()
) -> dict:
    """Infer the sidecar naming convention already in use inside *folder*.

    Walks the folder, matches each sidecar text file to its image, derives the
    suffix that follows the image stem, classifies the sidecar (filename then
    content), and returns the most common suffix observed for each type.
    Hidden entries and the absolute directories in *skip_dirs* (subtrees the
    caller's own scan does not index) are not walked: a convention found in
    one of those is not this folder's.

    Returns a dict ``{"tags_suffix", "description_suffix", "found_tags",
    "found_descriptions"}``; the suffix values are ``None`` when that type was
    not found.
    """
    skipped = {os.path.normpath(path) for path in skip_dirs}
    image_stems: set[str] = set()
    sidecar_files: list[str] = []
    seen_images = 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [
            name
            for name in dirs
            if not is_hidden_entry(name)
            and os.path.normpath(os.path.join(root, name)) not in skipped
        ]
        for name in files:
            if is_hidden_entry(name):
                continue
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


def _suffix_for_sidecar(sidecar_path_str: str, image_stems: set[str]) -> str | None:
    """Return the suffix of *sidecar_path_str* relative to its image stem.

    Matches the longest image stem that is a prefix of the sidecar path (so
    ``foo_tags.txt`` is read as ``foo_tags`` + ``.txt`` when an image
    ``foo_tags.png`` exists, otherwise as ``foo`` + ``_tags.txt``).  Returns
    ``None`` when no image in the folder owns this sidecar.

    Only stems in the sidecar's *own directory* are considered. ``os.walk``
    spans subdirectories, so a bare prefix match would let image ``/root/a.png``
    claim sidecar ``/root/ab/c.txt`` and yield the suffix ``"b/c.txt"`` - a
    separator-bearing value that must never reach the folder's configuration.
    The result is validated for the same reason before it is returned.
    """
    sidecar_dir = os.path.dirname(sidecar_path_str)
    base = os.path.splitext(sidecar_path_str)[0]
    best_stem: str | None = None
    for stem in image_stems:
        if os.path.dirname(stem) != sidecar_dir:
            continue
        if base == stem or base.startswith(stem):
            if best_stem is None or len(stem) > len(best_stem):
                best_stem = stem
    if best_stem is None:
        return None
    suffix = sidecar_path_str[len(best_stem) :]
    if not is_safe_sidecar_suffix(suffix):
        logger.warning(
            "Ignoring unsafe detected sidecar suffix %r for %s",
            suffix,
            sidecar_path_str,
        )
        return None
    return suffix


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
