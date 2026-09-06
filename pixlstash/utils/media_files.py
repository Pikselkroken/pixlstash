"""Which files PixlStash counts as pictures, and how many sit under a folder.

The extension set lived in two copies (the filesystem picker and the reference
folder scanner) before the library picker needed a third. One copy, because the
three answers have to agree: a folder the picker calls "1,200 pictures" is the
folder the scanner is about to index.
"""

import os

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.image_utils import THUMBNAIL_EXTENSION
from pixlstash.utils.image_processing.video_utils import VideoUtils

logger = get_logger(__name__)

SUPPORTED_IMAGE_EXTS: frozenset[str] = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".bmp",
        ".heic",
        ".heif",
        ".avif",
    }
)

# How many directory entries a count is allowed to visit before it gives up and
# says so. A folder picker must answer while somebody is looking at it, and a
# network share holding a few hundred thousand files does not.
DEFAULT_ENTRY_CAP = 200_000


#: The suffix every thumbnail PixlStash writes carries.
THUMBNAIL_SUFFIX = f"_thumb{THUMBNAIL_EXTENSION}"


def is_pixlstash_thumbnail(name_or_path: str) -> bool:
    """True when this file is a thumbnail PixlStash itself wrote.

    Until #1164 a managed picture kept its thumbnail *beside* the original as
    ``<name>_thumb.webp``, so a walk of a library's own folder finds them, and
    ``.webp`` is a supported extension. Indexing one makes it a picture, which
    earns it a thumbnail of its own - ``<name>_thumb_thumb.webp`` - which the
    next walk indexes in turn. That is not a slow leak: it is a generation per
    pass, and it was found four deep in a real library. New thumbnails live in
    ``.pixlstash-thumbnails/``, a dot-folder every walk prunes, but a library
    is migrated one picture at a time, so the old siblings stay excluded.
    """
    return name_or_path.lower().endswith(THUMBNAIL_SUFFIX)


def is_hidden_entry(name: str) -> bool:
    """True for a dot-prefixed file or directory name.

    The one rule every walk of a picture tree has to agree on: the Phase 2
    folder-structure read, ``local_import_pictures``, and
    ``ReferenceFolderScanTask``. A dot-folder is either a vault's own cache
    (``.pixlstash-thumbnails/``, the older ``.ref_thumbs/``, ``.pixlstash``
    sidecar stores) or something the owner deliberately hid; neither is content
    to index. Two of the three pruned them and the scan did not, so a
    reference-mode commit's read and its indexing pass disagreed about what was
    under one root - the read's count excluded the cache and the scan indexed
    it as pictures, which the mapping then filed.
    """
    return name.startswith(".")


def has_hidden_component(path: str, root: str) -> bool:
    """True when *path* is, or lies under, a hidden entry below *root*.

    The pruning rule asked of a whole path rather than one name, for the caller
    that has to decide what a *pruned* walk did not look at. *root* itself is
    not examined: a library whose own folder happens to be dotted is still a
    library.

    Two spellings ``relpath`` produces that are not names at all:

    * ``path == root`` gives ``"."``, which is dot-prefixed and so read as
      hidden - the exact opposite of the sentence above. Answered ``False``
      explicitly rather than left to ``is_hidden_entry``.
    * a *path outside root* gives leading ``".."`` components, also read as
      hidden. That one is left as ``True`` on purpose. The caller subtracts
      this set from the rows it is about to hard-delete, so ``True`` means
      "keep the row"; a path the walk never covered is exactly the case where
      absence from the walk's results proves nothing, and answering ``False``
      would turn a keep into a delete.
    """
    relative = os.path.relpath(path, root)
    if relative == os.curdir:
        return False
    return any(is_hidden_entry(part) for part in relative.split(os.sep))


def is_supported_media_file(name_or_path: str) -> bool:
    """True when *name_or_path* names an image or video PixlStash can index.

    The one chokepoint for that question, so the count a folder picker shows,
    the count a library card shows, and the files an import actually indexes
    cannot disagree - including about our own thumbnails, which none of them
    should ever count as pictures.
    """
    if is_pixlstash_thumbnail(name_or_path):
        return False
    ext = os.path.splitext(name_or_path)[1].lower()
    if ext in SUPPORTED_IMAGE_EXTS:
        return True
    return VideoUtils.is_video_file(name_or_path)


def count_media_files(
    root: str, *, entry_cap: int = DEFAULT_ENTRY_CAP
) -> tuple[int, bool]:
    """Count indexable files under *root*, recursively.

    Hidden directories are skipped, which is what keeps ``.pixlstash`` sidecars
    and a vault's own thumbnail cache out of the total. Symlinked directories
    are not followed, so a link back up the tree cannot make the walk unbounded.

    Args:
        root: Folder to walk.
        entry_cap: Give up after visiting this many directory entries.

    Returns:
        ``(count, capped)``. ``capped`` is True when the walk stopped early, so
        the caller can say "at least" rather than state a number it did not
        finish counting.
    """
    count = 0
    visited = 0

    def _note(error: OSError) -> None:
        # os.walk's default is to swallow this, which would turn an unreadable
        # subtree into a smaller number with nothing to say about it - and a
        # folder of pictures whose top level is unreadable into "Empty".
        logger.warning(
            "Skipping %s while counting under %s: %s", error.filename, root, error
        )

    for _, dirnames, filenames in os.walk(root, onerror=_note):
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        visited += len(dirnames)
        for name in filenames:
            # Counted per entry, not per directory: a flat folder of half a
            # million images is one iteration of the outer loop, so a cap
            # tested only out here would never fire on the shape this exists
            # to bound.
            visited += 1
            if is_supported_media_file(name):
                count += 1
            if visited >= entry_cap:
                return count, True
        if visited >= entry_cap:
            return count, True
    return count, False
