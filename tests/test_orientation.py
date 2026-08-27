"""In-place EXIF orientation writing: the rotate that never touches pixels.

The point of these tests is that a rotate must be *lossless and complete*: the
compressed pixel data comes through byte for byte, every sidecar the file
carried is still there afterwards, and rotating back restores the original
exactly. Each of those has a specific way of going wrong that a casual
implementation hits - a Pillow round-trip re-encodes the JPEG, drops the PNG
text chunks, and loses the camera EXIF - so each gets its own assertion rather
than being folded into "the file still opens".

Browser support was measured out-of-band on 2026-08-15 (Chromium 148, Firefox
150) and is recorded in :mod:`pixlstash.utils.image_processing.orientation`'s
docstring; it cannot be asserted here because it is a property of the renderer,
not of this code. That is exactly why WebP is excluded despite writing cleanly,
and :func:`test_webp_is_not_offered_in_place` pins the exclusion so it is not
casually undone.
"""

from __future__ import annotations

import io
import os
import struct

import piexif
import pytest
from PIL import Image, ImageOps, PngImagePlugin

from pixlstash.utils.image_processing.orientation import (
    ORIENTATION_NORMAL,
    ROTATE_180,
    ROTATE_CCW,
    ROTATE_CW,
    read_orientation,
    rotate_orientation,
    supports_in_place_rotation,
    write_orientation,
)

# A 3x2 tile with six distinct pixels: asymmetric in both axes and in colour,
# so all eight dihedral transforms of it are distinguishable from one another.
# A symmetric fixture would let a wrong rotation table pass.
_TILE_SIZE = (3, 2)
_TILE_PIXELS = [
    (0, 0, 0),
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 0),
    (255, 255, 255),
]

COMFY_PROMPT = "masterpiece, best quality, a cat"
COMFY_WORKFLOW = '{"nodes":[{"id":1,"type":"KSampler"}]}'


def _tile() -> Image.Image:
    image = Image.new("RGB", _TILE_SIZE)
    image.putdata(_TILE_PIXELS)
    return image


def _identity(image: Image.Image) -> tuple:
    """A hashable identity for an image's visible content."""
    return (image.size, image.convert("RGB").tobytes())


def _displayed(path) -> Image.Image:
    """What a viewer honouring the orientation tag shows for the file."""
    with Image.open(path) as handle:
        return ImageOps.exif_transpose(handle).convert("RGB")


def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    chunks = []
    position = 8
    while position + 8 <= len(data):
        (length,) = struct.unpack(">I", data[position : position + 4])
        chunk_type = data[position + 4 : position + 8]
        chunks.append((chunk_type, data[position + 8 : position + 8 + length]))
        position += 12 + length
    return chunks


@pytest.fixture
def jpeg_with_camera_exif(tmp_path):
    """A JPEG carrying a capture date and a camera model, like a real photo."""
    path = tmp_path / "photo.jpg"
    exif = {
        "0th": {piexif.ImageIFD.Model: b"PixlCam 9000"},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2026:03:15 14:32:04"},
        "GPS": {},
        "1st": {},
        "thumbnail": None,
    }
    buffer = io.BytesIO()
    _tile().save(buffer, "JPEG", quality=95, exif=piexif.dump(exif))
    path.write_bytes(buffer.getvalue())
    return path


@pytest.fixture
def png_with_comfy_metadata(tmp_path):
    """A PNG carrying ComfyUI text chunks, like a generated image."""
    path = tmp_path / "generated.png"
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", COMFY_PROMPT)
    info.add_text("workflow", COMFY_WORKFLOW)
    _tile().save(path, "PNG", pnginfo=info)
    return path


# ---------------------------------------------------------------------------
# The rotation table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("direction", [ROTATE_CW, ROTATE_CCW, ROTATE_180])
def test_rotation_table_matches_pillow(tmp_path, direction):
    """Rebuild the composition table from Pillow and compare, per orientation.

    The eight orientations are the dihedral group of the square, so a rotation
    permutes them in two 4-cycles: the unmirrored 1-6-3-8 and the mirrored
    2-7-4-5. The mirrored cycle is the one that is easy to write backwards from
    memory, and a backwards entry would flip mirrored photos the wrong way while
    every ordinary photo kept working - invisible in casual testing.

    So the expected value is *derived*: tag the tile with each orientation,
    render what a viewer would show, rotate that rendering with Pillow, then ask
    which orientation would have displayed the rotated result.
    """
    rendered_by_orientation = {}
    for orientation in range(1, 9):
        path = tmp_path / f"o{orientation}.png"
        _tile().save(path, "PNG")
        write_orientation(path, orientation)
        rendered_by_orientation[_identity(_displayed(path))] = orientation

    assert len(rendered_by_orientation) == 8, (
        "the fixture is too symmetric to tell the eight orientations apart"
    )

    transpose = {
        ROTATE_CW: Image.Transpose.ROTATE_270,  # PIL names rotations counter-clockwise
        ROTATE_CCW: Image.Transpose.ROTATE_90,
        ROTATE_180: Image.Transpose.ROTATE_180,
    }[direction]

    for orientation in range(1, 9):
        path = tmp_path / f"o{orientation}.png"
        turned = _displayed(path).transpose(transpose)
        expected = rendered_by_orientation[_identity(turned)]
        assert rotate_orientation(orientation, direction) == expected, (
            f"rotating orientation {orientation} {direction} should give {expected}"
        )


def test_four_quarter_turns_return_to_the_start():
    for orientation in range(1, 9):
        value = orientation
        for _ in range(4):
            value = rotate_orientation(value, ROTATE_CW)
        assert value == orientation


def test_counter_clockwise_undoes_clockwise():
    for orientation in range(1, 9):
        assert (
            rotate_orientation(rotate_orientation(orientation, ROTATE_CW), ROTATE_CCW)
            == orientation
        )


def test_half_turn_is_two_quarter_turns():
    for orientation in range(1, 9):
        assert rotate_orientation(orientation, ROTATE_180) == rotate_orientation(
            rotate_orientation(orientation, ROTATE_CW), ROTATE_CW
        )


@pytest.mark.parametrize("bogus", [0, 9, -1, 99])
def test_out_of_range_orientation_is_treated_as_normal(bogus):
    """Some cameras write 0. Every decoder reads that as 1, and so do we."""
    assert rotate_orientation(bogus, ROTATE_CW) == rotate_orientation(
        ORIENTATION_NORMAL, ROTATE_CW
    )


def test_unknown_direction_is_refused():
    with pytest.raises(ValueError, match="Unknown rotation direction"):
        rotate_orientation(1, "sideways")


# ---------------------------------------------------------------------------
# JPEG: pixels and camera metadata survive
# ---------------------------------------------------------------------------


def test_jpeg_rotate_does_not_re_encode_the_pixels(jpeg_with_camera_exif):
    """The scan data must come through untouched - a re-encode loses quality.

    Every rotate would otherwise cost one JPEG generation, and a user correcting
    a batch of sideways photos would silently degrade all of them.
    """
    before = jpeg_with_camera_exif.read_bytes()
    with Image.open(io.BytesIO(before)) as handle:
        pixels_before = handle.convert("RGB").tobytes()

    write_orientation(jpeg_with_camera_exif, 6)

    with Image.open(jpeg_with_camera_exif) as handle:
        assert handle.convert("RGB").tobytes() == pixels_before
        assert handle.size == _TILE_SIZE, "the stored bitmap must not be rotated"
    assert _displayed(jpeg_with_camera_exif).size == (2, 3), "the view must rotate"


def test_jpeg_rotate_keeps_camera_exif(jpeg_with_camera_exif):
    """Capture date and camera model must survive; the date dates the picture."""
    write_orientation(jpeg_with_camera_exif, 8)

    exif = piexif.load(str(jpeg_with_camera_exif))
    assert exif["0th"][piexif.ImageIFD.Model] == b"PixlCam 9000"
    assert exif["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:03:15 14:32:04"
    assert exif["0th"][piexif.ImageIFD.Orientation] == 8


def test_jpeg_without_any_exif_still_rotates(tmp_path):
    path = tmp_path / "bare.jpg"
    _tile().save(path, "JPEG", quality=95)
    assert read_orientation(path) == ORIENTATION_NORMAL

    write_orientation(path, 6)

    assert read_orientation(path) == 6


def test_jpeg_embedded_thumbnail_is_dropped(tmp_path):
    """An EXIF thumbnail is a stale unrotated copy some viewers prefer."""
    path = tmp_path / "thumbed.jpg"
    thumb = io.BytesIO()
    _tile().save(thumb, "JPEG", quality=95)
    exif = {
        "0th": {},
        "Exif": {},
        "GPS": {},
        "1st": {piexif.ImageIFD.Orientation: 1},
        "thumbnail": thumb.getvalue(),
    }
    buffer = io.BytesIO()
    _tile().save(buffer, "JPEG", quality=95, exif=piexif.dump(exif))
    path.write_bytes(buffer.getvalue())

    write_orientation(path, 6)

    assert piexif.load(str(path))["thumbnail"] is None


# ---------------------------------------------------------------------------
# PNG: pixels and ComfyUI provenance survive
# ---------------------------------------------------------------------------


def test_png_rotate_copies_idat_through_byte_for_byte(png_with_comfy_metadata):
    before = png_with_comfy_metadata.read_bytes()

    write_orientation(png_with_comfy_metadata, 6)

    after = png_with_comfy_metadata.read_bytes()
    idat_before = [body for kind, body in _png_chunks(before) if kind == b"IDAT"]
    idat_after = [body for kind, body in _png_chunks(after) if kind == b"IDAT"]
    assert idat_before == idat_after
    assert _displayed(png_with_comfy_metadata).size == (2, 3)


def test_png_rotate_keeps_comfyui_prompt_and_workflow(png_with_comfy_metadata):
    """The regression this whole module's design is built around.

    ``Image.open(...).transpose(...).save(...)`` silently drops every text
    chunk, and those chunks are how the library recovers which workflow and
    prompt produced a generated image. Losing them is unrecoverable.
    """
    write_orientation(png_with_comfy_metadata, 6)

    with Image.open(png_with_comfy_metadata) as handle:
        assert handle.info.get("parameters") == COMFY_PROMPT
        assert handle.info.get("workflow") == COMFY_WORKFLOW


def test_png_exif_chunk_precedes_the_pixel_data(png_with_comfy_metadata):
    """The PNG spec requires eXIf before IDAT; decoders may ignore it after."""
    write_orientation(png_with_comfy_metadata, 3)

    kinds = [kind for kind, _ in _png_chunks(png_with_comfy_metadata.read_bytes())]
    assert kinds.index(b"eXIf") < kinds.index(b"IDAT")


def test_png_rotated_twice_has_exactly_one_exif_chunk(png_with_comfy_metadata):
    """A second rotate must replace the chunk, not append another."""
    write_orientation(png_with_comfy_metadata, 6)
    write_orientation(png_with_comfy_metadata, 3)

    kinds = [kind for kind, _ in _png_chunks(png_with_comfy_metadata.read_bytes())]
    assert kinds.count(b"eXIf") == 1
    assert read_orientation(png_with_comfy_metadata) == 3


# ---------------------------------------------------------------------------
# Reversibility - this is what lets undo replace a backup copy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture", ["jpeg_with_camera_exif", "png_with_comfy_metadata"]
)
def test_rotating_back_restores_the_image_exactly(request, fixture):
    """Undo is the inverse write, so no backup copy of the original is needed.

    The issue proposed backing the source up until the user confirmed the
    result. This is why that mechanism is unnecessary: a rotate and its inverse
    restore the pixels and the displayed image exactly, so Ctrl+Z is exact
    rather than approximate.
    """
    path = request.getfixturevalue(fixture)
    with Image.open(path) as handle:
        pixels_before = handle.convert("RGB").tobytes()
    shown_before = _identity(_displayed(path))
    start = read_orientation(path)

    write_orientation(path, rotate_orientation(start, ROTATE_CW))
    assert _identity(_displayed(path)) != shown_before, "the rotate must do something"

    write_orientation(path, start)

    with Image.open(path) as handle:
        assert handle.convert("RGB").tobytes() == pixels_before
    assert _identity(_displayed(path)) == shown_before


@pytest.mark.parametrize(
    "fixture", ["jpeg_with_camera_exif", "png_with_comfy_metadata"]
)
def test_rotating_back_is_byte_identical_once_the_file_carries_an_orientation(
    request, fixture
):
    """From the second write on, a rotate and its undo cancel out exactly.

    The *first* write is necessarily not byte-identical: a file with no
    orientation block gains one, some tens of bytes. Writing 1 does not delete
    the block again - for a JPEG that block also holds the camera fields and
    the capture date, and for any file an explicit "orientation 1" is a legal
    thing to have meant. So byte-identity is the contract from the second write
    onward, which is every undo, since an undo always follows a rotate.
    """
    path = request.getfixturevalue(fixture)
    start = read_orientation(path)
    write_orientation(path, start)  # normalise: the block now exists
    settled = path.read_bytes()

    write_orientation(path, rotate_orientation(start, ROTATE_CW))
    assert path.read_bytes() != settled

    write_orientation(path, start)
    assert path.read_bytes() == settled


# ---------------------------------------------------------------------------
# Hostile inputs - from the pre-merge security review
# ---------------------------------------------------------------------------


def test_a_planted_symlink_temp_file_cannot_hijack_the_write(tmp_path):
    """The temp file must not be a predictable name opened through a symlink.

    With `<photo>.rotate.tmp` as the fixed name and a plain `open(..., "wb")`,
    anyone able to write to the pictures directory could pre-plant that name as
    a symlink: the write lands on the link's target, and `os.replace` then moves
    the *symlink* over the photo - clobbering an unrelated file and destroying
    the original in one go. Vaults commonly sit on synced or network folders, so
    "they can already write there" is not the same as "this is safe".
    """
    photo = tmp_path / "photo.jpg"
    _tile().save(photo, "JPEG", quality=95)
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched")
    (tmp_path / "photo.jpg.rotate.tmp").symlink_to(victim)

    write_orientation(photo, 6)

    assert victim.read_text() == "untouched", "the write followed a planted symlink"
    assert not photo.is_symlink(), "the photo was replaced by a symlink"
    assert read_orientation(photo) == 6
    with Image.open(photo) as handle:
        assert handle.size == _TILE_SIZE


def test_the_file_permissions_survive_a_rotate(tmp_path):
    """mkstemp creates 0600 and os.replace keeps the replacement's mode.

    Without an explicit copy, every rotate would quietly tighten the photo's
    permissions - a rotate must not change who can read the picture.
    """
    photo = tmp_path / "photo.jpg"
    _tile().save(photo, "JPEG", quality=95)
    photo.chmod(0o644)

    write_orientation(photo, 6)

    assert photo.stat().st_mode & 0o777 == 0o644


def test_extended_attributes_survive_a_rotate(tmp_path):
    """Finder tags and other `user.*` labels are part of the user's file.

    The premise of an in-place rotate is that it preserves everything except one
    enumerated value; silently dropping xattrs on a NAS or a tagged library
    breaks that promise in a way nobody would look for.
    """
    photo = tmp_path / "photo.jpg"
    _tile().save(photo, "JPEG", quality=95)
    try:
        os.setxattr(photo, "user.finder_tag", b"holiday")
    except (AttributeError, OSError) as exc:
        pytest.skip(f"extended attributes unavailable here: {exc}")

    write_orientation(photo, 6)

    assert os.getxattr(photo, "user.finder_tag") == b"holiday"


def test_the_modification_time_is_bumped_not_preserved(tmp_path):
    """The one piece of file metadata a rotate must NOT carry over.

    `ImageUtils._extract_embedded_metadata_cached` is an lru_cache keyed on
    `(path, mtime)`. Restoring the old stamp would serve the pre-rotate EXIF out
    of that cache for as long as the entry lived - and the bytes really did
    change, so a fresh stamp is also just true.
    """
    photo = tmp_path / "photo.jpg"
    _tile().save(photo, "JPEG", quality=95)
    os.utime(photo, (1_000_000, 1_000_000))

    write_orientation(photo, 6)

    assert photo.stat().st_mtime > 1_000_000


def test_contents_that_do_not_match_the_extension_are_refused_cleanly(tmp_path):
    """The refusal must not carry the file's bytes into the exception.

    piexif reads a non-JPEG argument as a *filename*, so it raises
    ``OSError: File name too long: b'<the entire file>'`` - which a caller then
    logs, writing the file's contents into the application log. Sniff the magic
    bytes instead of trusting the name.
    """
    impostor = tmp_path / "not-really.jpg"
    secret = b"sk-SUPERSECRET-KEY-MATERIAL " * 300
    impostor.write_bytes(secret)

    with pytest.raises(ValueError, match="neither PNG nor JPEG") as caught:
        write_orientation(impostor, 6)

    assert b"SUPERSECRET" not in str(caught.value).encode(), (
        "the refusal must name the path, never quote the file's contents"
    )
    assert impostor.read_bytes() == secret, "a refused file must not be modified"
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_file_over_the_size_cap_is_refused_before_it_is_read(tmp_path, monkeypatch):
    """The whole file is buffered twice on the single DB writer thread.

    Unbounded, one request stalls every other write in the product for as long
    as the read takes, and peaks at ~2x the file in RAM.
    """
    import pixlstash.utils.image_processing.orientation as orientation_module

    photo = tmp_path / "huge.jpg"
    _tile().save(photo, "JPEG", quality=95)
    monkeypatch.setattr(orientation_module, "MAX_IN_PLACE_BYTES", 10)

    assert supports_in_place_rotation(photo) is False
    with pytest.raises(ValueError, match="exceeds the"):
        write_orientation(photo, 6)


def test_a_failed_write_leaves_the_original_intact_and_no_temp_file(tmp_path):
    """An interrupted rotate must not truncate the user's photo."""
    path = tmp_path / "photo.jpg"
    _tile().save(path, "JPEG", quality=95)
    original = path.read_bytes()

    with pytest.raises(ValueError):
        write_orientation(path, 42)

    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Format gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("a.jpg", True),
        ("a.jpeg", True),
        ("A.JPG", True),
        ("a.png", True),
        ("a.PNG", True),
        ("a.webp", False),
        ("a.tif", False),
        ("a.tiff", False),
        ("a.bmp", False),
        ("a.gif", False),
        ("a.mp4", False),
        ("", False),
    ],
)
def test_supports_in_place_rotation(name, expected):
    assert supports_in_place_rotation(name) is expected


def test_webp_is_not_offered_in_place(tmp_path):
    """WebP writes cleanly but no browser reads it back - verified 2026-08-15.

    piexif handles WebP and the backend's ``exif_transpose`` honours the result,
    so this looks like an oversight from inside Python. It is not: Chromium 148
    and Firefox 150 both ignore WebP EXIF orientation, so rotating one in place
    would give a rotated thumbnail beside an unrotated full-size view. Removing
    this gate needs the browsers re-checked, not just the writer.
    """
    path = tmp_path / "image.webp"
    _tile().save(path, "WEBP")

    assert supports_in_place_rotation(path) is False
    with pytest.raises(ValueError, match="No in-place orientation writer"):
        write_orientation(path, 6)


def test_reading_orientation_never_raises(tmp_path):
    missing = tmp_path / "gone.jpg"
    assert read_orientation(missing) == ORIENTATION_NORMAL

    corrupt = tmp_path / "corrupt.jpg"
    corrupt.write_bytes(b"not an image at all")
    assert read_orientation(corrupt) == ORIENTATION_NORMAL


# ---------------------------------------------------------------------------
# Import records the orientation, rather than leaving it to the backfill
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stored,expected", [(None, 1), (1, 1), (6, 6), (8, 8)])
def test_import_records_the_orientation_up_front(tmp_path, stored, expected):
    """A newly imported picture must never sit at ``orientation IS NULL``.

    ``MissingOrientationFinder`` exists to fill rows that predate the column, not
    to finish importing a new one. Left to the finder, the value is a race for
    anything reading it just after import - and because orientation is an
    operation-log facet, a rotate landing in that window would snapshot ``None``
    as the prior state and its undo would have nothing to write back.

    Asserted against ``create_picture_from_bytes`` directly, NOT through an
    upload: with a server running, the backfill finder fills the column either
    way, so an end-to-end assertion passes whether or not the import does its
    job. That version of this test was written first and proved to be vacuous -
    removing the import-time write left it green.
    """
    from pixlstash.utils.image_processing.image_utils import ImageUtils

    buffer = io.BytesIO()
    image = _tile()
    if stored is None:
        image.save(buffer, "JPEG", quality=95)
    else:
        exif = image.getexif()
        exif[0x0112] = stored
        image.save(buffer, "JPEG", quality=95, exif=exif)

    picture = ImageUtils.create_picture_from_bytes(
        image_root_path=str(tmp_path),
        image_bytes=buffer.getvalue(),
        picture_uuid="imported.jpg",
    )

    assert picture.orientation == expected


def test_import_records_a_corrupt_orientation_as_normal(tmp_path):
    """An out-of-range tag must import as 1, not as itself or as NULL."""
    from pixlstash.utils.image_processing.image_utils import ImageUtils

    buffer = io.BytesIO()
    image = _tile()
    exif = image.getexif()
    exif[0x0112] = 99
    image.save(buffer, "JPEG", quality=95, exif=exif)

    picture = ImageUtils.create_picture_from_bytes(
        image_root_path=str(tmp_path),
        image_bytes=buffer.getvalue(),
        picture_uuid="corrupt.jpg",
    )

    assert picture.orientation == ORIENTATION_NORMAL
