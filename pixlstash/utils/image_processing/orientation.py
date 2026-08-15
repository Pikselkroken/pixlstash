"""Read and write a file's EXIF orientation tag without touching its pixels.

Rotating a photo is a *correction*, not a new picture, and the whole decoding
stack already honours the orientation tag: every read path in
:mod:`~pixlstash.utils.image_processing.image_utils` runs
``ImageOps.exif_transpose`` (thumbnails, the ML loaders, the plugin pipeline),
and browsers apply it to the full-size original they are served. So the
cheapest correct rotate is to change the tag and leave the bitmap alone.

**Container surgery, never a re-encode.** Both writers here splice the metadata
block and copy every other byte through:

* JPEG — ``piexif.insert``, which rewrites the APP1 segment only.
* PNG — the ``eXIf`` chunk (PNG spec 1.5.0, 2017), inserted before ``IDAT``.

That is not merely faster than a Pillow round-trip, it is the only *safe*
option. A round-trip re-encodes a JPEG (generational loss on every rotate) and
silently drops PNG text chunks unless every one is threaded back through by
hand — and those chunks are ``metadata["png"]["workflow"]`` / ``["prompt"]``,
how this library recovers ComfyUI provenance
(:mod:`pixlstash.utils.comfyui_utilities`). Surgery cannot lose them because it
never parses them.

**Formats: JPEG and PNG only, and WebP's exclusion is the load-bearing one.**
An in-place rotate is only correct if *every* renderer agrees with it, and two
of them are outside this codebase: the backend transposes on decode, while the
browser paints the full-size original itself. Measured on 2026-08-15:

===========  ==================  =============  =============  ==========
Format       Writer              Backend        Chromium 148   Firefox 150
===========  ==================  =============  =============  ==========
JPEG         ``piexif.insert``   honours        honours        honours
PNG          ``eXIf`` chunk      honours        honours        honours
WebP         ``piexif.insert``   honours        **ignores**    **ignores**
===========  ==================  =============  =============  ==========

WebP is therefore excluded even though the write works perfectly — piexif 1.1.3
handles WebP despite its docstring, and the bytes come out with the pixels
untouched. It is excluded because the browsers do not read it back, which would
show a rotated thumbnail beside an unrotated full view: worse than not offering
the rotate at all. **Do not add ``.webp`` here without re-running
``tests/test_orientation.py``'s browser note and checking the engines again.**
TIFF is out for the ordinary reason — its orientation lives in its own IFD and
nothing here writes that.

Anything not listed falls back to producing a rotated *copy*.
:func:`supports_in_place_rotation` is the one place that answers "can this file
be rotated in place".
"""

from __future__ import annotations

import os
import shutil
import struct
import tempfile
import zlib
from io import BytesIO

import piexif
from PIL import Image

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# Standard TIFF/EXIF tag id for the Orientation field.
ORIENTATION_TAG = 0x0112

# The unrotated, unmirrored default, and the value assumed for any file that
# carries no orientation at all.
ORIENTATION_NORMAL = 1

ROTATE_CW = "cw"
ROTATE_CCW = "ccw"
ROTATE_180 = "180"
ROTATE_DIRECTIONS = (ROTATE_CW, ROTATE_CCW, ROTATE_180)

# Composing a 90° clockwise display rotation onto an existing orientation.
#
# The eight orientations are the dihedral group of the square, so a rotation
# permutes them in two 4-cycles — the unmirrored 1→6→3→8 and the mirrored
# 2→7→4→5. The table is *derived*, not remembered: the mirrored cycle is easy
# to get backwards by hand, and getting it backwards would flip mirrored photos
# the wrong way while every ordinary photo kept working.
# ``tests/test_orientation.py::test_rotation_table_matches_pillow`` rebuilds it
# by rotating a real tagged image through Pillow and re-identifying the result,
# so a wrong entry fails the build rather than shipping.
_ROTATE_CW_STEP = {1: 6, 2: 7, 3: 8, 4: 5, 5: 2, 6: 3, 7: 4, 8: 1}
_ROTATE_CCW_STEP = {after: before for before, after in _ROTATE_CW_STEP.items()}

# Extensions whose container we can splice an orientation into.
_IN_PLACE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8"

# The whole file is read into memory and then copied once more into the spliced
# output, so peak cost is ~2x the file. That runs on the single DB writer thread
# inside the rotate's transaction, so an unbounded file does not merely use
# memory — it stalls every other write in the product for as long as it takes.
# 512 MiB is far above any photograph and far below the point where that stall
# is measurable in minutes.
MAX_IN_PLACE_BYTES = 512 * 1024 * 1024


def rotate_orientation(current: int, direction: str) -> int:
    """Return the orientation showing *current* turned a quarter (or half) turn.

    Args:
        current: The file's existing orientation, 1-8. Anything outside that
            range — including the ``0`` some cameras write — is treated as
            :data:`ORIENTATION_NORMAL`, matching what every decoder does with it.
        direction: One of :data:`ROTATE_DIRECTIONS`.

    Returns:
        The orientation value to store, 1-8.

    Raises:
        ValueError: If *direction* is not a known direction.
    """
    if direction not in ROTATE_DIRECTIONS:
        raise ValueError(
            f"Unknown rotation direction {direction!r}; expected one of {ROTATE_DIRECTIONS}"
        )
    if current not in _ROTATE_CW_STEP:
        current = ORIENTATION_NORMAL
    if direction == ROTATE_CW:
        return _ROTATE_CW_STEP[current]
    if direction == ROTATE_CCW:
        return _ROTATE_CCW_STEP[current]
    return _ROTATE_CW_STEP[_ROTATE_CW_STEP[current]]


def supports_in_place_rotation(file_path: str) -> bool:
    """Whether *file_path* can be rotated in place: right container, sane size.

    The caller uses this to choose between the in-place rotate and producing a
    rotated copy, so it answers from the *name* wherever it can — the choice has
    to be made per picture, in bulk, before anything is opened.

    The size check is therefore best-effort by design: a path that does not
    exist yet (or cannot be stat-ed) is judged on its extension alone, because
    this is also the oracle the API and the UI consult about pictures they are
    only naming. The real enforcement is in :func:`write_orientation`, which
    refuses regardless.
    """
    if not _extension_supported(file_path):
        return False
    try:
        return os.path.getsize(file_path) <= MAX_IN_PLACE_BYTES
    except OSError:
        return True


def _extension_supported(file_path: str) -> bool:
    """Whether the name alone says this container can carry an orientation.

    Kept apart from the size question so each refusal in :func:`write_orientation`
    can name its actual reason — "this format has no writer" and "this file is too
    big" are different problems, and reporting the first for the second sends
    whoever reads the log looking at the wrong thing.
    """
    if not file_path:
        return False
    return os.path.splitext(file_path)[1].lower() in _IN_PLACE_EXTENSIONS


def read_orientation(file_path: str) -> int:
    """Return the orientation stored in *file_path*, or 1 when it has none.

    Never raises: an unreadable or orientation-less file is reported as
    :data:`ORIENTATION_NORMAL`, which is what the decoders assume anyway.
    """
    try:
        with Image.open(file_path) as img:
            value = (img.getexif() or {}).get(ORIENTATION_TAG, ORIENTATION_NORMAL)
    except Exception as exc:
        logger.debug(
            "Could not read EXIF orientation from %s (%s); assuming %d",
            file_path,
            exc,
            ORIENTATION_NORMAL,
        )
        return ORIENTATION_NORMAL
    try:
        value = int(value)
    except (TypeError, ValueError):
        logger.warning(
            "Ignoring non-numeric EXIF orientation %r in %s; assuming %d",
            value,
            file_path,
            ORIENTATION_NORMAL,
        )
        return ORIENTATION_NORMAL
    if value not in _ROTATE_CW_STEP:
        logger.warning(
            "Ignoring out-of-range EXIF orientation %d in %s; assuming %d",
            value,
            file_path,
            ORIENTATION_NORMAL,
        )
        return ORIENTATION_NORMAL
    return value


def write_orientation(file_path: str, orientation: int) -> None:
    """Write *orientation* into *file_path*, leaving its pixel data untouched.

    The write is atomic: the spliced bytes go to a sibling temp file which is
    then :func:`os.replace`-d over the original, so an interrupted rotate leaves
    the user's photo intact rather than truncated.

    Args:
        file_path: A JPEG or PNG — see :func:`supports_in_place_rotation`.
        orientation: The value to store, 1-8.

    Raises:
        ValueError: If *orientation* is out of range or the format has no
            in-place writer.
        OSError: If the file cannot be read or replaced.
    """
    if orientation not in _ROTATE_CW_STEP:
        raise ValueError(f"EXIF orientation must be 1-8, got {orientation!r}")
    if not _extension_supported(file_path):
        raise ValueError(
            f"No in-place orientation writer for {file_path!r}; "
            f"supported extensions are {sorted(_IN_PLACE_EXTENSIONS)}"
        )

    size = os.path.getsize(file_path)
    if size > MAX_IN_PLACE_BYTES:
        raise ValueError(
            f"Refusing to rotate {file_path!r} in place: {size} bytes exceeds the "
            f"{MAX_IN_PLACE_BYTES}-byte limit. The whole file is buffered twice on "
            f"the DB writer thread, so a larger one stalls every other write."
        )

    with open(file_path, "rb") as handle:
        # Bounded, not a bare read(): the getsize above is a moment earlier, and
        # a file that grows in between would otherwise be read in full anyway.
        # Reading one byte past the limit is what makes the check the real one.
        original = handle.read(MAX_IN_PLACE_BYTES + 1)
    if len(original) > MAX_IN_PLACE_BYTES:
        raise ValueError(
            f"Refusing to rotate {file_path!r} in place: it grew past the "
            f"{MAX_IN_PLACE_BYTES}-byte limit while being read"
        )

    # Trust the bytes, not the name. The extension gate above is what the API and
    # UI consult in bulk, but a file *named* .jpg whose content is something else
    # would otherwise reach piexif, which reads a non-JPEG argument as a FILENAME
    # and raises `OSError: File name too long: b'<the entire file>'` — putting the
    # file's contents into the exception, and from there into the application log.
    if not original.startswith((_PNG_SIGNATURE, _JPEG_SIGNATURE)):
        raise ValueError(
            f"Refusing to rotate {file_path!r} in place: its contents are neither "
            f"PNG nor JPEG despite its extension"
        )

    if original.startswith(_PNG_SIGNATURE):
        updated = _png_with_orientation(original, orientation)
    else:
        updated = _jpeg_with_orientation(original, orientation)

    # A predictable sibling name opened with plain open(..., "wb") follows a
    # symlink: anyone able to write to the pictures directory could pre-plant
    # `<photo>.rotate.tmp` pointing anywhere, and this would overwrite the target
    # AND then rename the symlink over the user's photo, destroying the original.
    # mkstemp picks an unpredictable name and opens O_CREAT|O_EXCL, which refuses
    # to follow or reuse anything already there. Vaults live on synced and network
    # folders, so "they can already write there" is not the same as "this is safe".
    directory = os.path.dirname(os.path.abspath(file_path))
    handle_fd, temp_path = tempfile.mkstemp(dir=directory, suffix=".rotate.tmp")
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        # mkstemp creates 0600. os.replace keeps the *replacement's* mode, so
        # without this every rotate would quietly tighten the user's file
        # permissions — a rotate must not change who can read the photo.
        shutil.copymode(file_path, temp_path)
        os.replace(temp_path, file_path)
    except Exception:
        # Leaving a stray temp beside a user's photo would show up in their
        # folder and in the next scan, so clean up before re-raising.
        try:
            os.unlink(temp_path)
        except OSError as cleanup_exc:
            logger.warning(
                "Could not remove temp file %s after a failed orientation "
                "write (%s); it may appear in the next folder scan",
                temp_path,
                cleanup_exc,
            )
        raise


def _exif_payload(orientation: int) -> bytes:
    """A minimal TIFF/EXIF block carrying nothing but the orientation."""
    exif = Image.Exif()
    exif[ORIENTATION_TAG] = orientation
    return exif.tobytes()


def _jpeg_with_orientation(data: bytes, orientation: int) -> bytes:
    """Return *data* with its APP1 EXIF segment carrying *orientation*.

    Every other EXIF field the photo already carries is preserved — camera,
    lens, timestamps, GPS — because the existing block is loaded and edited
    rather than replaced. That matters beyond politeness:
    ``ImageUtils.extract_created_at_from_metadata`` reads ``DateTimeOriginal``
    out of it to date the picture.
    """
    try:
        exif_dict = piexif.load(data)
    except Exception as exc:
        # A JPEG with no or corrupt EXIF still gets an orientation; starting
        # from an empty block is correct here rather than a silent failure.
        logger.debug(
            "Could not parse existing EXIF while rotating (%s); writing a fresh block",
            exc,
        )
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

    exif_dict.setdefault("0th", {})[piexif.ImageIFD.Orientation] = orientation
    # A thumbnail embedded in the EXIF is a second, stale copy of the image that
    # some viewers prefer over the real one; piexif re-emits it unrotated, so
    # dropping it is what keeps the file self-consistent after a rotate.
    exif_dict["thumbnail"] = None
    exif_dict.pop("1st", None)

    # piexif.insert writes to its third argument and returns nothing; handed
    # bytes with no sink it raises instead of returning the new data.
    sink = BytesIO()
    piexif.insert(piexif.dump(exif_dict), data, sink)
    return sink.getvalue()


def _iter_png_chunks(data: bytes):
    """Yield ``(type, body)`` for each chunk in a PNG, in file order."""
    position = len(_PNG_SIGNATURE)
    total = len(data)
    while position + 8 <= total:
        (length,) = struct.unpack(">I", data[position : position + 4])
        chunk_type = data[position + 4 : position + 8]
        body_start = position + 8
        body_end = body_start + length
        if body_end + 4 > total:
            raise ValueError(
                f"Truncated PNG: chunk {chunk_type!r} claims {length} bytes but "
                f"only {total - body_start} remain"
            )
        yield chunk_type, data[body_start:body_end]
        position = body_end + 4


def _png_with_orientation(data: bytes, orientation: int) -> bytes:
    """Return *data* with an ``eXIf`` chunk carrying *orientation*.

    The ``IDAT`` chunks — the compressed pixels — are copied through byte for
    byte, as are ``tEXt``/``iTXt`` (the ComfyUI prompt and workflow), ``iCCP``
    and everything else. Only the ``eXIf`` chunk is replaced, and it is placed
    before the first ``IDAT`` as the spec requires.
    """
    payload = _exif_payload(orientation)
    out = bytearray(data[: len(_PNG_SIGNATURE)])
    written = False

    def emit(chunk_type: bytes, body: bytes) -> None:
        out.extend(struct.pack(">I", len(body)))
        out.extend(chunk_type)
        out.extend(body)
        out.extend(struct.pack(">I", zlib.crc32(chunk_type + body)))

    for chunk_type, body in _iter_png_chunks(data):
        if chunk_type == b"eXIf":
            continue  # replaced below
        if chunk_type == b"IDAT" and not written:
            emit(b"eXIf", payload)
            written = True
        emit(chunk_type, body)

    if not written:
        raise ValueError("Not a usable PNG: no IDAT chunk to place an eXIf before")
    return bytes(out)
