"""Remove generation data from an image file without re-encoding its pixels.

Generation data is how a picture was made: ComfyUI's ``prompt`` and
``workflow`` PNG text chunks, A1111's ``parameters`` chunk, and the EXIF/XMP
comments other tools (and ComfyUI's WebP saver) put the same text into. Shared
copies leave without it by default (#2417, owner ruling 2026-10-31).

**Container surgery, as in** :mod:`.orientation`: the compressed pixel data is
copied through byte for byte and only metadata blocks are dropped, so a stripped
copy decodes to exactly the pixels of the original.

**What survives is a keep-list, not a drop-list**, because the drop-list is
unbounded: a tool that invents its own text chunk or APP segment would leak
through a list of the ones we know about. Kept are the blocks that change how
the picture looks - the colour profile, gamma/chromaticity, transparency,
animation control - and the EXIF orientation, rewritten as a block that carries
nothing else. Orientation matters here more than usual: PixlStash's own rotate
(:func:`.orientation.write_orientation`) is an orientation tag, so dropping it
would undo the owner's rotation in every shared copy. Everything else EXIF holds
(camera, GPS, timestamps, the A1111 ``UserComment``) goes with it.

JPEG, PNG and WebP are handled. :func:`strip_generation_data` returns ``None``
for anything else, and the caller says so rather than pretending.
"""

from __future__ import annotations

import struct
import zlib
from io import BytesIO

from PIL import Image

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.orientation import (
    ORIENTATION_NORMAL,
    ORIENTATION_TAG,
)

logger = get_logger(__name__)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_EXIF_PREFIX = b"Exif\x00\x00"

# Ancillary PNG chunks that change how the picture renders. Every other
# ancillary chunk (tEXt/zTXt/iTXt - the ComfyUI and A1111 text, XMP - tIME,
# private chunks) is dropped. Unknown *critical* chunks are kept: a decoder must
# understand them, so dropping one would break the file.
_PNG_KEEP = frozenset(
    {
        b"tRNS",
        b"cHRM",
        b"gAMA",
        b"iCCP",
        b"sBIT",
        b"sRGB",
        b"cICP",
        b"mDCv",
        b"cLLi",
        b"bKGD",
        b"hIST",
        b"pHYs",
        b"sPLT",
        b"acTL",
        b"fcTL",
        b"fdAT",
    }
)

# JPEG markers with no length field.
_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})
_JPEG_SOS = 0xDA
_JPEG_EOI = 0xD9
_JPEG_APP0 = 0xE0  # JFIF / JFXX
_JPEG_APP1 = 0xE1  # EXIF and XMP
_JPEG_APP2 = 0xE2  # ICC profile (also MPF, which is dropped)
_JPEG_APP14 = 0xEE  # Adobe colour transform: changes how the pixels decode
_JPEG_COM = 0xFE
_ICC_SIGNATURE = b"ICC_PROFILE\x00"

# WebP chunks that are image data or change how it renders.
_WEBP_KEEP = frozenset({b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF", b"ICCP"})
_VP8X_EXIF = 0x08
_VP8X_XMP = 0x04


def strip_generation_data(data: bytes) -> bytes | None:
    """Return *data* with its generation data removed, pixels untouched.

    Args:
        data: A whole image file.

    Returns:
        The stripped file, or ``None`` when the format is not one handled here
        (anything but JPEG, PNG and WebP).

    Raises:
        ValueError: The file claims a handled format but its structure is
            broken (a truncated chunk or segment).
    """
    if data.startswith(_PNG_SIGNATURE):
        return _strip_png(data)
    if data.startswith(b"\xff\xd8"):
        return _strip_jpeg(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _strip_webp(data)
    return None


def _orientation(data: bytes) -> int:
    """The EXIF orientation of *data*, or 1 when it has none or it is unreadable."""
    try:
        with Image.open(BytesIO(data)) as img:
            value = int(img.getexif().get(ORIENTATION_TAG, ORIENTATION_NORMAL))
    except Exception as exc:
        logger.warning(
            "Could not read EXIF orientation while stripping (%s); dropping it", exc
        )
        return ORIENTATION_NORMAL
    return value if 1 <= value <= 8 else ORIENTATION_NORMAL


def _orientation_exif(data: bytes) -> bytes | None:
    """A TIFF block holding only *data*'s orientation, without the ``Exif`` prefix.

    ``None`` when the orientation is the default, so no EXIF is written at all.
    """
    orientation = _orientation(data)
    if orientation == ORIENTATION_NORMAL:
        return None
    exif = Image.Exif()
    exif[ORIENTATION_TAG] = orientation
    return exif.tobytes().removeprefix(_EXIF_PREFIX)


def _strip_png(data: bytes) -> bytes:
    out = bytearray(_PNG_SIGNATURE)

    def emit(chunk_type: bytes, body: bytes) -> None:
        out.extend(struct.pack(">I", len(body)))
        out.extend(chunk_type)
        out.extend(body)
        out.extend(struct.pack(">I", zlib.crc32(chunk_type + body)))

    exif = _orientation_exif(data)
    position = len(_PNG_SIGNATURE)
    while position + 8 <= len(data):
        (length,) = struct.unpack(">I", data[position : position + 4])
        chunk_type = data[position + 4 : position + 8]
        body_end = position + 8 + length
        if body_end + 4 > len(data):
            raise ValueError(f"Truncated PNG chunk {chunk_type!r}")
        if chunk_type == b"IDAT" and exif is not None:
            emit(b"eXIf", exif)  # the spec puts it before the first IDAT
            exif = None
        critical = not chunk_type[0] & 0x20
        if critical or chunk_type in _PNG_KEEP:
            # Copied with its CRC exactly as it was.
            out.extend(data[position : body_end + 4])
        position = body_end + 4
        if chunk_type == b"IEND":
            return bytes(out)
    # Cut off on a chunk boundary: say so, as a JPEG without EOI does, rather
    # than hand on a copy reported clean that will not open.
    raise ValueError("PNG ended before its IEND chunk")


def _strip_jpeg(data: bytes) -> bytes:
    out = bytearray(b"\xff\xd8")
    exif = _orientation_exif(data)
    position = 2
    while position + 2 <= len(data):
        if data[position] != 0xFF:
            raise ValueError(f"JPEG marker expected at byte {position}")
        marker = data[position + 1]
        if marker == 0xFF:  # fill byte
            position += 1
            continue
        if marker == _JPEG_EOI:
            # Anything after EOI (a trailer, an MPF second image) is not this
            # picture and is dropped with it.
            out.extend(b"\xff\xd9")
            return bytes(out)
        if marker in _JPEG_STANDALONE:
            out.extend(data[position : position + 2])
            position += 2
            continue
        if position + 4 > len(data):
            break
        (length,) = struct.unpack(">H", data[position + 2 : position + 4])
        end = position + 2 + length
        if marker == _JPEG_SOS:
            if exif is not None:
                _jpeg_emit_exif(out, exif)
                exif = None
            # The scan header, then its entropy-coded data up to the next real
            # marker: pixels, copied. Segments between progressive scans go
            # back through the keep-list like any other.
            scan_end = _jpeg_scan_end(data, end)
            out.extend(data[position:scan_end])
            position = scan_end
            continue
        if end > len(data):
            raise ValueError(f"Truncated JPEG segment 0x{marker:02X}")
        body = data[position + 4 : end]
        if marker != _JPEG_APP0 and exif is not None:
            # EXIF goes straight after SOI, or after JFIF when there is one.
            _jpeg_emit_exif(out, exif)
            exif = None
        keep = (
            marker == _JPEG_APP0
            or marker == _JPEG_APP14
            or (marker == _JPEG_APP2 and body.startswith(_ICC_SIGNATURE))
            or not (0xE0 <= marker <= 0xEF or marker == _JPEG_COM)
        )
        if keep:
            out.extend(data[position:end])
        position = end
    raise ValueError("JPEG ended before its EOI marker")


def _jpeg_scan_end(data: bytes, start: int) -> int:
    """The offset of the first marker after the entropy-coded data at *start*.

    Inside a scan ``FF 00`` is a stuffed data byte and ``FF D0``-``FF D7`` are
    restart markers; both belong to the scan. ``FF FF`` is fill before a marker.
    """
    scan = start
    while True:
        found = data.find(b"\xff", scan)
        if found < 0 or found + 1 >= len(data):
            raise ValueError("JPEG ended inside its image data")
        following = data[found + 1]
        if following == 0x00 or 0xD0 <= following <= 0xD7:
            scan = found + 2
        elif following == 0xFF:
            scan = found + 1
        else:
            return found


def _jpeg_emit_exif(out: bytearray, exif: bytes) -> None:
    body = _EXIF_PREFIX + exif
    out.extend(struct.pack(">BBH", 0xFF, _JPEG_APP1, len(body) + 2))
    out.extend(body)


def _strip_webp(data: bytes) -> bytes:
    exif = _orientation_exif(data)
    chunks = []
    position = 12
    while position + 8 <= len(data):
        fourcc = data[position : position + 4]
        (size,) = struct.unpack("<I", data[position + 4 : position + 8])
        end = position + 8 + size + (size & 1)
        if position + 8 + size > len(data):
            raise ValueError(f"Truncated WebP chunk {fourcc!r}")
        if fourcc in _WEBP_KEEP:
            chunks.append([fourcc, bytearray(data[position + 8 : position + 8 + size])])
        position = end

    vp8x = next((c for c in chunks if c[0] == b"VP8X"), None)
    if vp8x is None:
        # A simple WebP cannot carry EXIF, so the orientation is lost with it.
        exif = None
    else:
        vp8x[1][0] &= ~(_VP8X_EXIF | _VP8X_XMP) & 0xFF
        if exif is not None:
            vp8x[1][0] |= _VP8X_EXIF
            chunks.append([b"EXIF", exif])  # the spec puts it after the image

    body = bytearray(b"WEBP")
    for fourcc, payload in chunks:
        body.extend(fourcc)
        body.extend(struct.pack("<I", len(payload)))
        body.extend(payload)
        if len(payload) & 1:
            body.append(0)
    return b"RIFF" + struct.pack("<I", len(body)) + bytes(body)
