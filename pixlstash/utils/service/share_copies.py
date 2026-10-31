"""Share-ready copies of pictures, written into a staging folder (#2417).

Every way of sharing a picture from the desktop - the Share dialog, copying as
files, dragging out, sending to a folder - hands over a copy made here, never
the vault's own file. See the "Share copies" paragraph in
``docs/backend_architecture.md`` §5 and ``docs/integration_architecture.md`` §2.5.

**Where copies go.** ``<config dir>/share-copies/<batch>/``, beside
``server-config.json`` like the hub and the managed model store. The Electron
main process starts the backend with ``--server-config`` and so already knows
that root without being told by the renderer; it checks every path it is handed
is inside it (``shareCopiesRoot()`` in ``electron/src/config.ts``). A test
Server on a temp config dir gets its own root for the same reason.

**How long they live.** A receiving app may read a dropped or pasted file long
after the gesture, so a batch outlives it; :func:`sweep_share_copies` removes
batches older than :data:`SHARE_COPY_MAX_AGE_SECONDS` at start-up and before
every new batch.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from io import BytesIO

from PIL import Image, ImageOps, PngImagePlugin

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.generation_data import strip_generation_data
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.utils.service.export_utils import (
    _safe_archive_stem,
    _unique_export_stem,
)
from pixlstash.utils.watermark import apply_watermark

logger = get_logger(__name__)

SHARE_COPIES_DIRNAME = "share-copies"

# ponytail: swept at start-up and on each new batch, not on a timer, so an
# idle app keeps its last batch until the next share or restart.
SHARE_COPY_MAX_AGE_SECONDS = 12 * 60 * 60

FILE_NAMES_ORIGINAL = "original"
FILE_NAMES_PIXLSTASH = "pixlstash"

# What a copy says about its generation data.
METADATA_REMOVED = "removed"
METADATA_KEPT = "kept"
# A format the stripper does not handle (videos, GIF, TIFF, HEIC, AVIF, BMP):
# the copy is the original file, whatever it carries.
METADATA_UNCHANGED = "unchanged"

_REENCODE_FORMATS = {"JPEG", "PNG", "WEBP"}
_REENCODE_EXT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


def share_copies_root(config_dir: str) -> str:
    """The staging root for a server whose config file lives in *config_dir*."""
    return os.path.join(config_dir, SHARE_COPIES_DIRNAME)


def sweep_share_copies(
    root: str, max_age_seconds: float = SHARE_COPY_MAX_AGE_SECONDS
) -> int:
    """Remove batches under *root* older than *max_age_seconds*; return how many.

    Only directories named like a batch (32 hex digits) are touched, so a root
    that somehow holds anything else loses nothing.
    """
    try:
        entries = list(os.scandir(root))
    except FileNotFoundError:
        return 0
    except OSError as exc:
        logger.warning("Could not list share copies in %s: %s", root, exc)
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for entry in entries:
        if not _is_batch_name(entry.name) or entry.is_symlink() or not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(entry.path)
            removed += 1
        except OSError as exc:
            logger.warning("Could not remove share copies %s: %s", entry.path, exc)
    return removed


def make_share_copies(
    pictures: list[dict],
    image_root: str,
    root: str,
    *,
    keep_generation_data: bool = False,
    max_long_edge: int | None = None,
    watermark_bytes: bytes | None = None,
    file_names: str = FILE_NAMES_ORIGINAL,
) -> dict:
    """Write one share-ready copy of each picture into a new batch under *root*.

    Args:
        pictures: ``{"id", "file_path", "original_file_name"}`` per picture, in
            the order the copies should be named.
        image_root: The vault's image root, to resolve relative file paths.
        root: The staging root (:func:`share_copies_root`).
        keep_generation_data: Leave generation data in. With no resize and no
            watermark the copy is then byte-identical to the original.
        max_long_edge: Shrink images whose longer side exceeds this. Never
            enlarges.
        watermark_bytes: Composite this watermark onto each image.
        file_names: :data:`FILE_NAMES_ORIGINAL` (the imported file's name) or
            :data:`FILE_NAMES_PIXLSTASH` (the vault's own file name).

    Returns:
        ``{"batch", "directory", "files", "skipped"}``. Each file entry carries
        ``picture_id``, ``path``, ``name``, ``size``, ``metadata`` (one of the
        ``METADATA_*`` values), ``resized`` and ``watermarked``. A video or an
        animated image is copied as it is, so it reports neither.
    """
    batch = uuid.uuid4().hex
    directory = os.path.join(root, batch)
    os.makedirs(root, mode=0o700, exist_ok=True)
    os.mkdir(directory, mode=0o700)
    claimed: dict = {}
    files, skipped = [], []
    for pic in pictures:
        source = ImageUtils.resolve_picture_path(image_root, pic.get("file_path"))
        if not source or not os.path.isfile(source):
            skipped.append({"picture_id": pic["id"], "reason": "missing"})
            continue
        try:
            entry = _copy_one(
                pic,
                source,
                directory,
                claimed,
                keep_generation_data=keep_generation_data,
                max_long_edge=max_long_edge,
                watermark_bytes=watermark_bytes,
                file_names=file_names,
            )
        except Exception as exc:
            logger.warning(
                "Could not make a share copy of picture %s (%s): %s",
                pic["id"],
                source,
                exc,
            )
            skipped.append({"picture_id": pic["id"], "reason": "unreadable"})
            continue
        files.append(entry)
    return {"batch": batch, "directory": directory, "files": files, "skipped": skipped}


def _is_batch_name(name: str) -> bool:
    return len(name) == 32 and all(c in "0123456789abcdef" for c in name)


def _copy_name(pic: dict, source: str, ext: str, file_names: str, claimed: dict):
    fallback = f"picture_{pic['id']}"
    if file_names == FILE_NAMES_ORIGINAL and pic.get("original_file_name"):
        stem = os.path.splitext(pic["original_file_name"])[0]
    else:
        stem = os.path.splitext(os.path.basename(source))[0]
    # The extension is the copy's real format, never the stored name's.
    return f"{_unique_export_stem(_safe_archive_stem(stem, fallback), claimed)}{ext}"


def _copy_one(
    pic,
    source,
    directory,
    claimed,
    *,
    keep_generation_data,
    max_long_edge,
    watermark_bytes,
    file_names,
) -> dict:
    ext = os.path.splitext(source)[1].lower()
    entry = {"picture_id": pic["id"], "resized": False, "watermarked": False}

    if VideoUtils.is_video_file(source):
        # No container metadata writer here, so a video goes as it is.
        name = _copy_name(pic, source, ext, file_names, claimed)
        shutil.copyfile(source, os.path.join(directory, name))
        return _finish(entry, directory, name, METADATA_UNCHANGED)

    if max_long_edge or watermark_bytes:
        reencoded = _reencode(
            source, max_long_edge, watermark_bytes, keep_generation_data
        )
        if reencoded is not None:
            data, fmt, resized = reencoded
            name = _copy_name(pic, source, _REENCODE_EXT[fmt], file_names, claimed)
            with open(os.path.join(directory, name), "wb") as fh:
                fh.write(data)
            entry["resized"] = resized
            entry["watermarked"] = bool(watermark_bytes)
            return _finish(
                entry,
                directory,
                name,
                METADATA_KEPT if keep_generation_data else METADATA_REMOVED,
            )

    with open(source, "rb") as fh:
        data = fh.read()
    if keep_generation_data:
        metadata = METADATA_KEPT
    else:
        stripped = strip_generation_data(data)
        metadata = METADATA_UNCHANGED if stripped is None else METADATA_REMOVED
        data = data if stripped is None else stripped
    name = _copy_name(pic, source, ext, file_names, claimed)
    with open(os.path.join(directory, name), "wb") as fh:
        fh.write(data)
    return _finish(entry, directory, name, metadata)


def _finish(entry: dict, directory: str, name: str, metadata: str) -> dict:
    path = os.path.join(directory, name)
    entry.update(path=path, name=name, size=os.path.getsize(path), metadata=metadata)
    return entry


def _reencode(source, max_long_edge, watermark_bytes, keep_generation_data):
    """Resize and/or watermark *source*; ``(bytes, format, resized)`` or ``None``.

    ``None`` means "copy it as it is instead": an animated image (re-encoding
    every frame is out of scope), or a picture already within *max_long_edge*
    with no watermark asked for, which a re-encode would only degrade.
    """
    with Image.open(source) as img:
        if getattr(img, "is_animated", False):
            return None
        resized = bool(max_long_edge) and max(img.size) > max_long_edge
        if not resized and not watermark_bytes:
            return None
        fmt = img.format if img.format in _REENCODE_FORMATS else "PNG"
        info = dict(img.info)
        exif = img.getexif()
        out = ImageOps.exif_transpose(img)
    if resized:
        out.thumbnail((max_long_edge, max_long_edge), Image.LANCZOS)
    if watermark_bytes:
        # apply_watermark answers RGB; put the picture's own alpha back so a
        # transparent area stays transparent.
        alpha = (
            out.convert("RGBA").getchannel("A")
            if out.mode in ("RGBA", "LA", "PA") or "transparency" in out.info
            else None
        )
        out = apply_watermark(out, watermark_bytes)
        if alpha is not None:
            out.putalpha(alpha)

    save_kwargs = {}
    if info.get("icc_profile"):
        save_kwargs["icc_profile"] = info["icc_profile"]
    if keep_generation_data:
        # The pixels are now upright, so the orientation tag would turn them
        # a second time.
        exif.pop(0x0112, None)
        if len(exif):
            save_kwargs["exif"] = exif.tobytes()
        if fmt == "PNG":
            pnginfo = PngImagePlugin.PngInfo()
            for key, value in info.items():
                if isinstance(value, str):
                    pnginfo.add_text(key, value)
            save_kwargs["pnginfo"] = pnginfo
        elif info.get("xmp"):
            save_kwargs["xmp"] = info["xmp"]
        if fmt == "JPEG" and info.get("comment"):
            save_kwargs["comment"] = info["comment"]
    if fmt == "JPEG":
        out = out.convert("RGB")
        save_kwargs["quality"] = 92
    elif fmt == "WEBP":
        save_kwargs["quality"] = 92
    # Pillow falls back to ``im.info`` for some blocks (XMP, the ICC profile),
    # so the copy carries only what is passed above - and the palette
    # transparency, which is pixels rather than metadata.
    out.info = {k: v for k, v in out.info.items() if k == "transparency"}
    buffer = BytesIO()
    out.save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue(), fmt, resized
