"""The model shelf's icon store: content-addressed marks beside the hub.

**A sample and an icon are different objects and neither substitutes for the
other.** A *sample* is what a model produces - derived, plural, automatic. An
icon is what a model *is* - authored, singular, chosen once. It answers "which
one is this?", which is the question a 1,806-row shelf asks at a glance, and it
is the only one of the two a checkpoint can ever have: PixlStash registers a
checkpoint in place, possibly at 24 GB and possibly never used, so there is no
image anywhere in the system for it.

Three constraints already ruled elsewhere decide the storage, and inventing a
fourth mechanism would break one of them:

1. **Never written into the model file.** An icon is curation, and stamping it
   into a ``.safetensors`` would change the sha256 the whole identity chain
   resolves on.
2. **Never a pointer into the vault.** ``model`` is a hub table and pictures are
   vault rows; no foreign key spans the two, and SQLite recycles deleted ids, so
   an ``icon_picture_id`` would silently re-point at a different picture after a
   delete-plus-insert and break on every library switch. Picking a library
   picture therefore **copies** it in here.
3. **Identity is a content hash.** ``model.icon_sha256`` names
   ``<hub_dir>/icons/<sha256>.webp``. Dedup is then free and is the *point*:
   forty Flux checkpoints wanting one logo store one file, which is the normal
   case and the opposite of how samples behave. Hub-side means the icon survives
   a library switch exactly as the model row does.

**Orphans are left in place.** Because the store is content-addressed and shared,
clearing one model's icon cannot delete the file - another row may name the same
hash, and checking costs a scan of the column on every clear. An unreferenced
icon is a few KB of WebP; a wrongly deleted one is a mark forty rows lose at
once. A sweep can reclaim them later if it ever matters.
"""

from __future__ import annotations

import hashlib
import os

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.path_utils import resolve_path_within

logger = get_logger(__name__)

ICON_DIRNAME = "icons"
ICON_SUFFIX = ".webp"

# Ceiling on one stored icon. A mark is a tile in a list, not a picture: at the
# sizes the shelf and the ComfyUI picker draw it, anything past this is either a
# mistake or someone using the icon store as a file host.
MAX_ICON_BYTES = 2 * 1024 * 1024

# What an upload may be, checked on the BYTES rather than on a filename or a
# client-supplied content type. An icon is served back from our own origin, so
# "it is an image" has to be a fact about the payload, not a claim about it.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
)


class IconRefused(ValueError):
    """An upload could not be stored, with the reason the receipt reports."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def icon_dir(hub_path: str) -> str:
    """Where icons live for the hub at ``hub_path``.

    Beside the hub database rather than under a library, because the icon has to
    survive a library switch exactly as the ``model`` row does.
    """
    return os.path.join(os.path.dirname(hub_path), ICON_DIRNAME)


def icon_path(hub_path: str, sha256: str) -> str:
    """The absolute path of one stored icon.

    Contained against the icon directory even though the caller supplies a
    hash: it reaches this from a URL path segment, and a segment that is not
    actually a digest must not become a path. The digest shape is checked first
    so a refusal says *why* rather than surfacing as a containment error.

    Raises:
        IconRefused: If ``sha256`` is not a 64-character hex digest.
    """
    if len(sha256) != 64 or not all(c in "0123456789abcdef" for c in sha256.lower()):
        raise IconRefused("An icon is addressed by its sha256.", reason="not_a_digest")
    return resolve_path_within(icon_dir(hub_path), f"{sha256.lower()}{ICON_SUFFIX}")


def _sniff(data: bytes) -> str | None:
    """The format the bytes actually are, or None.

    WebP is checked as RIFF....WEBP rather than by a prefix, because the four
    size bytes sit in the middle of the magic.
    """
    for magic, name in _MAGIC:
        if data.startswith(magic):
            return name
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def store_icon(hub_path: str, data: bytes) -> str:
    """Put bytes in the icon store and return the hash that names them.

    Idempotent by construction: the same bytes always hash to the same name, so
    the second model to use a logo writes nothing and the store holds one copy.

    Stored as given rather than re-encoded. Re-encoding would be the moment to
    normalise to WebP, but it would also mean decoding attacker-influenced image
    data on the server for a file that is only ever handed back to a browser -
    and the browser decodes it either way. The name keeps the ``.webp`` suffix
    because that is what the ruling specified for the store's shape; the served
    media type comes from sniffing the bytes, never from the suffix.

    Args:
        hub_path: The hub database's path; the store sits beside it.
        data: The image bytes.

    Returns:
        The sha256 hex digest naming the stored file.

    Raises:
        IconRefused: If the payload is empty, too large, or not an image.
    """
    if not data:
        raise IconRefused("That file is empty.", reason="empty")
    if len(data) > MAX_ICON_BYTES:
        raise IconRefused(
            f"An icon may be at most {MAX_ICON_BYTES // 1024} KB.",
            reason="too_large",
        )
    if _sniff(data) is None:
        raise IconRefused(
            "That is not a PNG, JPEG or WebP image.", reason="not_an_image"
        )

    digest = hashlib.sha256(data).hexdigest()
    directory = icon_dir(hub_path)
    os.makedirs(directory, exist_ok=True)
    target = icon_path(hub_path, digest)
    if os.path.exists(target):
        logger.debug("Icon %s is already stored; reusing it.", digest[:12])
        return digest

    # Written to a temporary name and renamed, so a crash mid-write cannot leave
    # a truncated file under a name that claims to be the hash of its content -
    # which every later reader would then trust.
    partial = f"{target}.partial"
    with open(partial, "wb") as handle:
        handle.write(data)
    os.replace(partial, target)
    logger.info("Stored model icon %s (%d bytes).", digest[:12], len(data))
    return digest


def media_type_of(path: str) -> str:
    """The media type to serve one stored icon as, from its bytes.

    Read from the file rather than assumed from the suffix: the store names
    everything ``.webp`` and keeps the original encoding, so the suffix is a
    naming convention and would be a lie as a content type.
    """
    with open(path, "rb") as handle:
        head = handle.read(12)
    sniffed = _sniff(head)
    return f"image/{sniffed}" if sniffed else "application/octet-stream"
