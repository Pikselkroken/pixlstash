"""The colour summary of a picture: twelve swatch shares that add to 100.

Each pixel of the stored thumbnail counts toward its NEAREST swatch centre in
CIELAB (D65, plain Euclidean distance, i.e. CIE76). A swatch therefore reaches
halfway to its neighbours. The order of :data:`SWATCHES` is the storage contract:
``picture.colour_shares`` is a JSON array of 12 numbers in exactly this order,
and the ``colour`` query parameter names swatches by these names.

To tune the search, edit the hex centres in :data:`SWATCHES`; the Lab centres are
derived from them at import.
"""

import json
from io import BytesIO
from typing import Sequence

import numpy as np
from PIL import Image

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# (name, sRGB centre). THE tuning table. Order is the storage contract: do not
# reorder without a migration that resets ``picture.colour_shares``.
SWATCHES: tuple[tuple[str, str], ...] = (
    ("red", "#b8312c"),
    ("orange", "#dd7a2c"),
    ("yellow", "#dcc23e"),
    ("green", "#5b8a3c"),
    ("teal", "#3b9690"),
    ("blue", "#3c66ae"),
    ("purple", "#764aa0"),
    ("pink", "#dc8fac"),
    ("brown", "#86593a"),
    ("white", "#efede6"),
    ("grey", "#8b8b88"),
    ("black", "#1b1b1b"),
)

SWATCH_NAMES: tuple[str, ...] = tuple(name for name, _ in SWATCHES)
SWATCH_COUNT = len(SWATCHES)

# "No summary" to a reader. Nothing writes it any more (a read failure leaves
# the column NULL, see ColourSharesTask); kept so an early value reads as none.
UNREADABLE_SENTINEL = "[]"

_D65_WHITE = np.array([0.95047, 1.0, 1.08883])
_SRGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)
_LINEAR_LUT = np.array(
    [
        (c / 255.0 / 12.92)
        if c / 255.0 <= 0.04045
        else ((c / 255.0 + 0.055) / 1.055) ** 2.4
        for c in range(256)
    ]
)
_EPSILON = 0.008856
_KAPPA = 903.3


def _lab_of_hex(hex_colour: str) -> np.ndarray:
    """CIELAB (D65) of an sRGB ``#rrggbb`` colour."""
    rgb = [int(hex_colour.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
    xyz = _SRGB_TO_XYZ @ _LINEAR_LUT[rgb] / _D65_WHITE
    f = np.where(xyz > _EPSILON, np.cbrt(xyz), (_KAPPA * xyz + 16.0) / 116.0)
    return np.array([116.0 * f[1] - 16.0, 500.0 * (f[0] - f[1]), 200.0 * (f[1] - f[2])])


SWATCH_LAB: np.ndarray = np.array([_lab_of_hex(hex_) for _, hex_ in SWATCHES])


def swatch_index(name: str) -> int | None:
    """The storage index of a swatch name, or ``None`` if it is not one."""
    try:
        return SWATCH_NAMES.index(name)
    except ValueError:
        return None


def shares_from_rgb(rgb: np.ndarray) -> list[float]:
    """Twelve shares (percent, 1 decimal, summing to exactly 100) of an HxWx3 uint8 array."""
    pixels = rgb.reshape(-1, 3)
    if pixels.shape[0] == 0:
        raise ValueError("colour shares need at least one pixel")
    linear = _LINEAR_LUT[pixels]
    xyz = linear @ _SRGB_TO_XYZ.T / _D65_WHITE
    f = np.where(xyz > _EPSILON, np.cbrt(xyz), (_KAPPA * xyz + 16.0) / 116.0)
    lab = np.stack(
        [
            116.0 * f[:, 1] - 16.0,
            500.0 * (f[:, 0] - f[:, 1]),
            200.0 * (f[:, 1] - f[:, 2]),
        ],
        axis=1,
    )
    # One centre at a time, so the working set is a few N-length arrays rather
    # than an N x 12 x 3 block (about 0.2 GB for a 1024 px thumbnail).
    nearest = np.zeros(pixels.shape[0], dtype=np.intp)
    best = np.full(pixels.shape[0], np.inf)
    for index, centre in enumerate(SWATCH_LAB):
        distance = ((lab - centre) ** 2).sum(axis=1)
        closer = distance < best
        best[closer] = distance[closer]
        nearest[closer] = index
    counts = np.bincount(nearest, minlength=SWATCH_COUNT)
    # Work in tenths of a percent and hand out the rounding remainder by largest
    # fractional part, so the twelve values are exact tenths and sum to 1000.
    units = counts * 1000.0 / pixels.shape[0]
    floored = np.floor(units)
    remainder = int(1000 - floored.sum())
    order = np.argsort(-(units - floored), kind="stable")
    floored[order[:remainder]] += 1
    return [float(v) / 10.0 for v in floored]


def shares_from_bytes(data: bytes) -> list[float]:
    """Shares of an encoded image (the stored WebP thumbnail, in practice).

    Alpha is composited over white so a transparent area reads as the paper it
    would be shown on, not as black.
    """
    with Image.open(BytesIO(data)) as img:
        img.load()
        if img.mode in ("RGBA", "LA") or (
            img.mode == "P" and "transparency" in img.info
        ):
            rgba = img.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            img = Image.alpha_composite(background, rgba)
        rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    return shares_from_rgb(rgb)


def shares_as_json(shares: Sequence[float]) -> str:
    """The stored form: a JSON array of 12 numbers."""
    return json.dumps([float(v) for v in shares])


def shares_from_column(
    raw: str | None, picture_id: int | None = None
) -> list[float] | None:
    """The API form of ``picture.colour_shares``: twelve numbers, or ``None``.

    ``None``, empty, the unreadable sentinel and anything that is not a 12-number
    array all read as "no summary". A malformed stored value is logged, because
    it means something other than this module wrote it.
    """
    if not raw or raw == UNREADABLE_SENTINEL:
        return None
    try:
        values = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "picture %s has a malformed colour_shares value %r (%s); reporting none",
            picture_id,
            raw,
            exc,
        )
        return None
    if not isinstance(values, list) or len(values) != SWATCH_COUNT:
        logger.warning(
            "picture %s has a colour_shares value of the wrong shape %r; reporting none",
            picture_id,
            raw,
        )
        return None
    return [float(v) for v in values]
