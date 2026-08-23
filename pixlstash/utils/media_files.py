"""Which files PixlStash counts as pictures, and how many sit under a folder.

The extension set lived in two copies (the filesystem picker and the reference
folder scanner) before the library picker needed a third. One copy, because the
three answers have to agree: a folder the picker calls "1,200 pictures" is the
folder the scanner is about to index.
"""

import os

from pixlstash.pixl_logging import get_logger
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


def is_supported_media_file(name_or_path: str) -> bool:
    """True when *name_or_path* names an image or video PixlStash can index."""
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
        # subtree into a smaller number with nothing to say about it — and a
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
