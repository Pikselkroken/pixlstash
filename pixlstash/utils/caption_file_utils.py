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

# Extensions a suffix must not end in, because appending one names a media
# file rather than a caption: ``photo.jpg`` plus ``.png`` resolves to
# ``photo.png``, and when that picture sits beside it the first write-back
# replaces the picture with text. Both extension sets, so a video is as
# protected as a still, and the thumbnail carve-out in
# ``is_supported_media_file`` is deliberately not reused: ``_thumb.webp`` is
# one of the names a write-back must not take.
_MEDIA_EXTS = tuple(sorted(SUPPORTED_IMAGE_EXTS | frozenset(VIDEO_EXTENSIONS)))


def _names_same_file(path: str, other: str) -> bool:
    """Whether two paths name one file on any platform PixlStash runs on.

    ``os.path.normcase`` folds case on Windows only, so on a default
    case-insensitive macOS volume ``photo.png`` and ``photo.PNG`` compared as
    different files and a sidecar was allowed to name the picture itself.
    Compared lowercased everywhere instead: on a case-sensitive volume that
    costs at most a refused sidecar spelling, while the miss overwrites a
    picture with text.
    """
    return path.lower() == other.lower()


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


def suffixes_collide(tags_suffix: str | None, description_suffix: str | None) -> bool:
    """Whether tags and descriptions would end up in the same file.

    One suffix for both kinds is one file for both: the two write-backs
    overwrite each other and the next scan reads the survivor back in as the
    other kind. This is the single definition of that rule, applied by the
    library and reference-folder routes, by the import seeding, and by the
    folder scan before it persists a detected suffix.

    An unset suffix is the module default rather than "no file", so the
    defaults take part in the comparison. The comparison is case-insensitive
    because Windows and macOS resolve ``_notes.txt`` and ``_NOTES.TXT`` to one
    file; ``_SAFE_SUFFIX_RE`` keeps suffixes ASCII, so ``lower()`` is enough.

    Args:
        tags_suffix: The configured tags suffix, or None for the default.
        description_suffix: The configured description suffix, or None.

    Returns:
        True when the two effective suffixes name the same file.
    """
    return (tags_suffix or DEFAULT_TAGS_SUFFIX).lower() == (
        description_suffix or DEFAULT_DESCRIPTION_SUFFIX
    ).lower()


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

    A suffix equal to the image's own extension names the image itself, and a
    caption file that IS the picture is read as mojibake tags and truncated by
    the first write-back. Refused here for the same reason: this is the one
    place every read and write path derives the name.

    Raises:
        ValueError: If *suffix* is not a bare filename fragment, or names the
            image itself.
    """
    if not is_safe_sidecar_suffix(suffix):
        raise ValueError(f"Sidecar suffix is not a usable caption name: {suffix!r}")
    path = os.path.splitext(image_path)[0] + suffix
    if _names_same_file(path, image_path):
        raise ValueError(f"Sidecar suffix {suffix!r} names the picture itself")
    return path


def classify_sidecar(path: str) -> str | None:
    """The kind `sniff_caption` reads out of *path*, or ``None`` for a non-caption.

    One classifier, deliberately: the read's probe and the import's answered
    read both come through here, so a file the screen offered as a description
    cannot be resolved as tags by a different rule further down.
    """
    sniffed = sniff_caption(path)
    return sniffed and sniffed[0]


def is_recorded_sidecar_shape(image_path: str, path: str | None) -> bool:
    """Whether *path* is a sidecar a legitimate row could have recorded.

    Exactly the image stem plus a safe suffix (#776), and not the image
    itself: a suffix equal to the picture's own extension names the picture,
    which `sidecar_path` refuses for new files and a recorded value from
    before that guard must not smuggle back in - a write-back through it
    would overwrite the original image. One rule for every recorded or
    already-resolved path, read or write.
    """
    if not path:
        return False
    stem = os.path.splitext(image_path)[0]
    tail = path[len(stem) :] if path.startswith(stem) else ""
    if not tail or not is_safe_sidecar_suffix(tail):
        return False
    return not _names_same_file(path, image_path)


def recorded_sidecar(image_path: str, stored_path: str | None) -> str | None:
    """The picture's own recorded sidecar, when it is still there.

    A configured suffix names the files PixlStash *creates*; a picture whose
    file was found under another name keeps that file. Honoured only when the
    recorded value has the shape `is_recorded_sidecar_shape` allows and the
    file exists.
    """
    if not is_recorded_sidecar_shape(image_path, stored_path):
        return None
    return stored_path if os.path.isfile(stored_path) else None


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
        try:
            candidate = sidecar_path(image_path, suffix)
        except ValueError as exc:
            # A known convention that names this picture itself (a ``.txt``
            # beside a ``.txt``). Not a sidecar; try the next one.
            logger.warning(
                "Skipping %s probe for %s: %s", sidecar_type, image_path, exc
            )
            continue
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
        if is_recorded_sidecar_shape(image_path, existing_path):
            return existing_path
        logger.warning(
            "Ignoring recorded %s sidecar path %r for %s: not the image "
            "stem plus a safe suffix, or the image itself; using the "
            "configured suffix instead",
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


def writeback_target(
    image_path: str,
    sidecar_type: str,
    configured_suffix: str | None,
    stored_path: str | None,
    has_content: bool,
) -> str | None:
    """The file an edit may write *sidecar_type* back to, or ``None`` to skip.

    The write-side twin of `recorded_sidecar`'s read rule. The picture's own
    recorded file is the one an edit replaces; a file that exists beside the
    picture but is **not** recorded is one nothing has read in yet, and
    replacing it would destroy a caption before the promised read-in. Sync
    turning on (`PATCH /server-config/captions`, `seed_caption_suffixes`) only
    queues the scan that records those files, so between the toggle and the
    scan every sidecar on disk is in exactly that state.

    A brand-new file is still created, from *configured_suffix* or the module
    default, but only when *has_content* says there is something to put in it
    and nothing is already there. ``None`` also comes back for a suffix that
    is not a usable filename fragment, as `writeback_path` documents.
    """
    recorded = recorded_sidecar(image_path, stored_path)
    if recorded:
        return recorded
    target = resolve_typed_sidecar(image_path, sidecar_type, configured_suffix)
    if target is None:
        if not has_content:
            return None
        target = writeback_path(image_path, sidecar_type, configured_suffix, None)
        if target is None or not os.path.isfile(target):
            return target
    logger.info(
        "Skipping the %s write-back for %s: %s exists but is not this picture's "
        "recorded sidecar; a scan reads it in before an edit replaces it.",
        sidecar_type,
        image_path,
        target,
    )
    return None


def read_caption_text(path: str) -> str | None:
    """The raw text of a caption file, or ``None`` when it could not be read.

    ``None`` means **only** that the read failed (permissions, a vanished file,
    a bad decode); an empty file reads as ``""``. Callers that turn a sidecar
    into stored data need the two apart: an empty file is the owner clearing
    their caption, an unreadable one is nothing at all, and collapsing the two
    makes a transient `OSError` look like an intentional clear.
    """
    # ``utf-8-sig``, as `sniff_caption` does: a BOM survives ``.strip()`` and
    # would otherwise ride along on the first tag, as "﻿1girl".
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
    """Read a tags sidecar into a normalised, de-duplicated list of tags.

    An unreadable file and an empty one both give ``[]``; use
    `read_caption_text` plus `parse_caption_tags` where the difference matters.
    """
    raw = read_caption_text(path)
    return parse_caption_tags(raw) if raw is not None else []


def read_description_sidecar(path: str) -> str | None:
    """Read a description sidecar into stripped text, or ``None`` when empty.

    ``None`` also covers an unreadable file; use `read_caption_text` where the
    difference matters.
    """
    raw = read_caption_text(path)
    if raw is None:
        return None
    text = raw.strip()
    return text or None


def _existing_sidecars(image_path: str, suffixes) -> list[str]:
    """Each of *suffixes* naming an existing file beside *image_path*, in order.

    Every one of them, not just the first: the read reports a suffix per row
    and the owner answers each row on its own, so on a case-sensitive
    filesystem ``.txt`` and ``.TXT`` are two files both confirmed as tags and
    both have to be opened. Stopping at the first would drop the second file's
    content, and silently so when the first is empty.
    """
    paths: list[str] = []
    for suffix in suffixes:
        try:
            candidate = sidecar_path(image_path, suffix)
        except ValueError as exc:
            logger.warning("Cannot resolve sidecar for %s: %s", image_path, exc)
            continue
        if os.path.isfile(candidate) and candidate not in paths:
            paths.append(candidate)
    return paths


def attach_sidecars(
    pic,
    file_path: str,
    tags_suffixes=None,
    description_suffixes=None,
    *,
    overwrite_description: bool = False,
) -> list[str]:
    """Record the sidecars beside *file_path* on an unsaved picture.

    Sets ``tags_file``/``description_file`` (and their mtimes) when a sidecar
    exists, fills ``description`` from the description sidecar when the
    picture has none, and returns the tags read from the tags sidecar. The
    list is empty when there is no tags sidecar AND when the file exists but
    is empty or unreadable, so it says "no tags were read", not "no file":
    ``pic.tags_file`` is what says whether a file was found. The caller
    decides what an empty list means - the scan and the local import both
    fall back to the pending-tag sentinel so the tagger runs instead.

    **A file that would not open leaves its mtime ``None``.** The path is
    still recorded, because a file is there, but the mtime is what the scan
    compares against to decide whether to re-read: stamping the current one
    after a failed read tells the next pass "already imported, unchanged", and
    the confirmed tags are then missed forever (the tagger overwrites them)
    and a confirmed description never arrives. ``None`` differs from any mtime
    on disk, so the next scan reads the file again and picks the content up
    once the permissions recover. Content is applied only from a read that
    succeeded; an empty file still reads as empty and is honoured as such.

    Tags cannot be set on an unsaved `Picture` (they are a relationship), so
    any that were read are stashed on ``pic._sidecar_tags`` for the inserter
    to persist once the row has an id. Done here rather than by each caller:
    three of them repeated the same two lines, and one that forgot them would
    read the files and drop the tags on the floor.

    *tags_suffixes* / *description_suffixes* are the suffixes the owner (or a
    folder's configuration) said hold that kind, tried in order. ``None``
    probes the known conventions instead, content-sniffing a bare ``.txt``;
    an empty list reads nothing of that kind - the owner's "ignore".
    *overwrite_description* is for a row that already exists: the owner has
    just confirmed the file is the caption, so it replaces what the row had -
    including with nothing, when the confirmed file reads empty. Only a file
    that actually read counts: an unreadable one is not a cleared caption, so
    the row keeps its description. Without the flag (a new row) an empty file
    never overwrites what the row came with.

    **Every confirmed suffix that names an existing file is read**, not just
    the first: the read reports one row per suffix and the owner answers each
    row separately, so two rows both answered ``tags`` are two files to read.
    The tags are their union, in suffix order and de-duplicated, and
    ``tags_file`` records the first of them. A description cannot be a union,
    so the first confirmed file with content wins and is what
    ``description_file`` records, falling back to the first existing file when
    none of them has any.
    """
    if tags_suffixes is None:
        probed = resolve_typed_sidecar(file_path, SIDECAR_TYPE_TAGS, None)
        tags_paths = [probed] if probed else []
    else:
        tags_paths = _existing_sidecars(file_path, tags_suffixes)
    tags: list[str] = []
    seen: set[str] = set()
    tags_unread = False
    for path in tags_paths:
        text = read_caption_text(path)
        if text is None:
            tags_unread = True
            continue
        for tag in parse_caption_tags(text):
            if tag not in seen:
                seen.add(tag)
                tags.append(tag)
    if tags_paths:
        pic.tags_file = tags_paths[0]
        pic.tags_file_mtime = None if tags_unread else get_sidecar_mtime(tags_paths[0])

    if description_suffixes is None:
        probed = resolve_typed_sidecar(file_path, SIDECAR_TYPE_DESCRIPTION, None)
        description_paths = [probed] if probed else []
    else:
        description_paths = _existing_sidecars(file_path, description_suffixes)
    description_path = None
    description = None
    read_empty = False
    description_unread = False
    for path in description_paths:
        raw = read_caption_text(path)
        if raw is None:
            description_unread = True
            continue
        text = raw.strip()
        if text:
            description_path, description = path, text
            break
        read_empty = True
    if description_path is None and description_paths:
        description_path = description_paths[0]
    if description_path:
        pic.description_file = description_path
        # A file that would not open records no mtime, so the next scan reads
        # it again rather than seeing "unchanged" and skipping it for good.
        pic.description_file_mtime = (
            None if description_unread else get_sidecar_mtime(description_path)
        )
        if description:
            if overwrite_description or not pic.description:
                pic.description = description
        elif overwrite_description and read_empty:
            # The owner confirmed this file is the caption and it is empty:
            # that is a cleared description, not a failed read. An unreadable
            # file leaves ``read_empty`` False and the row keeps what it had.
            pic.description = None
    if tags:
        pic._sidecar_tags = tags
    return tags


#: How much of a caption file the sniff reads. A caption is a line or a
#: paragraph; anything that needs more than this to classify is not one.
_SNIFF_BYTES = 4096
_EXCERPT_CHARS = 100


def sniff_caption(path: str) -> tuple[str, str] | None:
    """Classify one caption file, and say what it starts with.

    **The single place a kind is decided from a file.** The folder read's
    screen, `classify_sidecar` and through it the suffix probe all come here,
    so what the owner was shown and what the import resolves agree by
    construction rather than by two rules happening to match.

    Returns ``(kind, excerpt)`` with *kind* ``"tags"`` or ``"description"``
    and *excerpt* the first line or so, whitespace collapsed, for a screen to
    show beside the choice. ``None`` when the file is not a caption at all:
    binary (a NUL byte in the head), markup/JSON, or empty or unreadable
    without a name that says what it is.

    In order:

    * a file that will not open is still classified by an unambiguous name,
      with an empty excerpt, and logged: an unreadable ``_tags.txt`` is a tags
      sidecar nothing could be read from, and `attach_sidecars` records that
      rather than reporting the file as absent;
    * a NUL byte in the first 4 KB is binary, whatever its extension;
    * the head is decoded as ``utf-8-sig``, so a BOM is dropped rather than
      surviving ``.strip()`` and becoming part of the first tag;
    * a first character of ``{``, ``[`` or ``<`` is a generator's JSON
      metadata or an XMP/XML sidecar - no reading turns either into a tag list
      or a description;
    * an unambiguous name (``_tags.txt``, ``_description.txt``, ``.caption``
      …) decides, because the exporter that wrote it said so - including for
      an empty file, so the suffix probe still resolves it, `attach_sidecars`
      records ``tags_file`` and the caller falls back to the sentinel;
    * an empty file with no such name says nothing, so it is not a caption;
    * otherwise the content decides (`_looks_like_tags`).
    """
    name = os.path.basename(path)
    try:
        with open(path, "rb") as fh:
            head = fh.read(_SNIFF_BYTES)
    except OSError as exc:
        # Unreadable, but a name the exporter made unambiguous still says what
        # the file holds, and that is the answer `attach_sidecars` needs: a
        # `_tags.txt` it cannot open is recorded as a tags sidecar with no tags
        # read, which is "unreadable", where `None` would say "absent" and send
        # the picture to the tagger as though nothing was ever written for it.
        # The excerpt is empty because nothing was read; there is nothing to
        # show. A file with no such name says nothing either way and stays None.
        logger.warning("Could not read caption file %s: %s", path, exc)
        if _TAGS_NAME_RE.search(name):
            return SIDECAR_TYPE_TAGS, ""
        if _DESCRIPTION_NAME_RE.search(name):
            return SIDECAR_TYPE_DESCRIPTION, ""
        return None
    if b"\x00" in head:
        return None
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
    except (OSError, UnicodeDecodeError) as exc:
        # Missing, unreadable, or not text at all - fall through and (re)write
        # it. Logged at DEBUG so it satisfies the no-silent-failure policy
        # without noising up a normal first-time write (there is nothing to
        # read yet). UnicodeDecodeError is in here because the compare read is
        # the first thing to touch a file that is not a caption at all, and it
        # escaped to abort a whole scan.
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
    folder: str,
    sample_limit: int = 200,
    skip_dirs: Collection[str] = (),
) -> dict:
    """Infer the sidecar naming convention already in use inside *folder*.

    Walks the folder, matches each sidecar text file to its image, derives the
    suffix that follows the image stem, classifies the sidecar (filename then
    content), and returns the most common suffix observed for each type.

    The walk prunes what every walk of a picture tree prunes - hidden entries
    (``is_hidden_entry``) - plus the absolute directory paths in *skip_dirs*,
    which the caller fills with the subtrees its own scan will not look inside:
    reference folders registered under this one, and the directories PixlStash
    writes itself under the library root. A convention read out of a subtree
    nobody indexes is not this folder's convention, and persisting it as one
    names every sidecar the scan goes on to write.

    Returns a dict ``{"tags_suffix", "description_suffix", "found_tags",
    "found_descriptions"}``; the suffix values are ``None`` when that type was
    not found.
    """
    skipped = {os.path.normpath(path) for path in skip_dirs}
    # Case-folded, spellings kept: the same rule the folder read applies
    # (`folder_structure_service._collect_captions`). `img.txt` beside
    # `IMG.JPG` is one convention on Windows and macOS, and matching
    # case-sensitively would report none at all; the picture's own spelling is
    # what a write-back joins the suffix to, so a stem that matches only
    # case-folded has to be checked against the filesystem.
    image_stems: dict[str, set[str]] = {}
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
                stem = os.path.splitext(full)[0]
                image_stems.setdefault(stem.lower(), set()).add(stem)
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


def _suffix_for_sidecar(
    sidecar_path_str: str, image_stems: dict[str, set[str]]
) -> str | None:
    """Return the suffix of *sidecar_path_str* relative to its image stem.

    *image_stems* maps a case-folded stem to the spellings seen for it.
    Matches the longest image stem that is a prefix of the sidecar path (so
    ``foo_tags.txt`` is read as ``foo_tags`` + ``.txt`` when an image
    ``foo_tags.png`` exists, otherwise as ``foo`` + ``_tags.txt``).  Returns
    ``None`` when no image in the folder owns this sidecar.

    Matching is case-folded but the suffix keeps its spelling, and a stem that
    matches only case-folded counts only where the path a write-back would
    build - the picture's stem as spelled plus this suffix - actually exists.
    That is true on a case-insensitive filesystem, where ``img.txt`` beside
    ``IMG.JPG`` is the convention the owner has, and false on Linux, where
    claiming it would name a file nothing can read back. One ``isfile`` per
    case-differing stem; an exactly-spelled stem costs nothing extra.

    Only stems in the sidecar's *own directory* are considered. ``os.walk``
    spans subdirectories, so a bare prefix match would let image ``/root/a.png``
    claim sidecar ``/root/ab/c.txt`` and yield the suffix ``"b/c.txt"`` - a
    separator-bearing value that must never reach the folder's configuration.
    The result is validated for the same reason before it is returned.
    """
    sidecar_dir = os.path.dirname(sidecar_path_str).lower()
    base = os.path.splitext(sidecar_path_str)[0].lower()
    best_stem: str | None = None
    for stem, spellings in image_stems.items():
        if os.path.dirname(stem) != sidecar_dir:
            continue
        if not base.startswith(stem):
            continue
        if best_stem is not None and len(stem) <= len(best_stem):
            continue
        candidate = sidecar_path_str[len(stem) :]
        if sidecar_path_str[: len(stem)] not in spellings and not any(
            os.path.isfile(spelled + candidate) for spelled in spellings
        ):
            continue
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
