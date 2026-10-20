"""A selection on a picture, as JSON, and the mask it rasterises to.

The Edit tab's Paint tools (rectangle, ellipse, lasso, brush, eraser, colour
wand, invert) do not send a bitmap. They send the list of steps the owner took,
so an edit can be saved as a small document and repeated on another picture:

    {"version": 1,
     "steps": [{"tool": "rect", "mode": "new", "box": [0.1, 0.2, 0.3, 0.4]},
               {"tool": "brush", "mode": "subtract", "radius": 0.01,
                "points": [[0.2, 0.3], [0.25, 0.31]]},
               {"tool": "wand", "mode": "add", "point": [0.5, 0.5],
                "tolerance": 32},
               {"tool": "invert"}],
     "refine": {"grow": 6, "feather": 8, "fill_holes": 40}}

**Geometry is a fraction of the picture** (``x`` of its width, ``y`` of its
height, a brush ``radius`` of its shorter side), so the same steps land on the
same place of a resized copy. **Refine values are pixels** of the picture the
mask is rendered for, as the Edit tab shows them. White (255) is what the
workflow changes.

This module is the only rasteriser: the Edit tab's preview
(``POST /pictures/{id}/mask``) and a run's ``MASK`` (``POST /workflows/run``
with ``selection``) both come from :func:`render_mask`, so what the owner sees
is what the workflow gets. A segmenter's named pick is meant to join ``tool``
as one more step kind, ``{"tool": "segment", "label": "scarf"}``.
"""

from __future__ import annotations

import io
import os
from functools import lru_cache
from typing import Literal

import cv2
import numpy as np
from PIL import Image, ImageOps
from pydantic import BaseModel, Field, model_validator

MAX_STEPS = 200
MAX_POINTS = 20000
MAX_GROW = 100
MAX_FEATHER = 100
MAX_FILL_HOLES = 200


class SelectionStep(BaseModel):
    """One gesture: a shape, a stroke, a wand click or an invert.

    ``box`` is ``[x, y, width, height]`` (rect, ellipse); ``points`` is a
    polygon (lasso) or a path (brush); ``point`` and ``tolerance`` are the
    wand's seed and how far a colour may stray from it per channel (0-255).
    The eraser is a brush with ``mode: "subtract"``.
    """

    tool: Literal["rect", "ellipse", "lasso", "brush", "wand", "invert"]
    mode: Literal["new", "add", "subtract", "intersect"] = "add"
    box: list[float] | None = Field(None, min_length=4, max_length=4)
    points: list[tuple[float, float]] | None = Field(None, max_length=MAX_POINTS)
    radius: float | None = Field(None, gt=0, le=1)
    point: tuple[float, float] | None = None
    tolerance: int = Field(32, ge=0, le=255)

    @model_validator(mode="after")
    def _has_its_geometry(self) -> "SelectionStep":
        needs = {
            "rect": self.box,
            "ellipse": self.box,
            "lasso": self.points,
            "brush": self.points,
            "wand": self.point,
        }
        if self.tool in needs and not needs[self.tool]:
            raise ValueError(f"A {self.tool} step needs its geometry.")
        if self.tool == "lasso" and len(self.points or []) < 3:
            raise ValueError("A lasso step needs at least three points.")
        if self.tool == "brush" and self.radius is None:
            raise ValueError("A brush step needs a radius.")
        return self


class SelectionRefine(BaseModel):
    """What happens to the whole mask after its steps, in this order:
    fill holes (close every gap narrower than the size), grow (negative
    shrinks), feather."""

    grow: int = Field(0, ge=-MAX_GROW, le=MAX_GROW)
    feather: int = Field(0, ge=0, le=MAX_FEATHER)
    fill_holes: int = Field(0, ge=0, le=MAX_FILL_HOLES)


class Selection(BaseModel):
    """A whole selection: its steps, then its refinement."""

    version: Literal[1] = 1
    steps: list[SelectionStep] = Field(default_factory=list, max_length=MAX_STEPS)
    refine: SelectionRefine = Field(default_factory=SelectionRefine)

    @model_validator(mode="after")
    def _bounded(self) -> "Selection":
        if sum(len(step.points or []) for step in self.steps) > MAX_POINTS:
            raise ValueError(f"A selection holds at most {MAX_POINTS} points.")
        return self


def _px(point: tuple[float, float], width: int, height: int) -> tuple[int, int]:
    return int(round(point[0] * width)), int(round(point[1] * height))


def _shape(step: SelectionStep, image: np.ndarray) -> np.ndarray:
    """The pixels one step covers, 0 or 255."""
    height, width = image.shape[:2]
    shape = np.zeros((height, width), dtype=np.uint8)
    if step.tool in ("rect", "ellipse"):
        x, y, w, h = step.box
        x0, y0 = _px((x, y), width, height)
        x1, y1 = _px((x + w, y + h), width, height)
        if step.tool == "rect":
            cv2.rectangle(shape, (x0, y0), (x1 - 1, y1 - 1), 255, thickness=-1)
        else:
            centre = ((x0 + x1) // 2, (y0 + y1) // 2)
            axes = (max(abs(x1 - x0) // 2, 1), max(abs(y1 - y0) // 2, 1))
            cv2.ellipse(shape, centre, axes, 0, 0, 360, 255, thickness=-1)
    elif step.tool == "lasso":
        polygon = np.array([_px(p, width, height) for p in step.points], np.int32)
        cv2.fillPoly(shape, [polygon], 255)
    elif step.tool == "brush":
        radius = max(int(round(step.radius * min(width, height))), 1)
        path = [_px(p, width, height) for p in step.points]
        for point in path:
            cv2.circle(shape, point, radius, 255, thickness=-1)
        for start, end in zip(path, path[1:]):
            cv2.line(shape, start, end, 255, thickness=2 * radius)
    elif step.tool == "wand":
        x, y = _px(step.point, width, height)
        x, y = min(max(x, 0), width - 1), min(max(y, 0), height - 1)
        flood = np.zeros((height + 2, width + 2), dtype=np.uint8)
        diff = (step.tolerance,) * 3
        cv2.floodFill(
            image.copy(),
            flood,
            (x, y),
            (0, 0, 0),
            diff,
            diff,
            4 | cv2.FLOODFILL_FIXED_RANGE | cv2.FLOODFILL_MASK_ONLY | (255 << 8),
        )
        shape = flood[1:-1, 1:-1]
    return shape


def _ellipse_kernel(size: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * size + 1, 2 * size + 1))


def _refine(mask: np.ndarray, refine: SelectionRefine) -> np.ndarray:
    if refine.fill_holes:
        # A closing with a disc of the size fills every gap and enclosed hole
        # the disc cannot fit in, so a speckled wand pick becomes solid.
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_CLOSE, _ellipse_kernel(refine.fill_holes // 2)
        )
    if refine.grow > 0:
        mask = cv2.dilate(mask, _ellipse_kernel(refine.grow))
    elif refine.grow < 0:
        mask = cv2.erode(mask, _ellipse_kernel(-refine.grow))
    if refine.feather:
        mask = cv2.GaussianBlur(mask, (0, 0), refine.feather / 2)
    return mask


def render_mask(selection: Selection, image: np.ndarray) -> np.ndarray:
    """The selection on *image* (``H x W x 3`` RGB) as a ``H x W`` uint8 mask."""
    height, width = image.shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)
    for step in selection.steps:
        if step.tool == "invert":
            mask = 255 - mask
            continue
        shape = _shape(step, image)
        if step.mode == "new":
            mask = shape
        elif step.mode == "add":
            mask = np.maximum(mask, shape)
        elif step.mode == "subtract":
            mask = np.minimum(mask, 255 - shape)
        else:
            mask = np.minimum(mask, shape)
    return _refine(mask, selection.refine)


# What reading a picture can raise: not an image, unreadable, or past PIL's
# decompression-bomb limit (which is not an OSError).
UNREADABLE_PICTURE = (OSError, Image.DecompressionBombError)


# ponytail: one picture cached, the one the Edit tab is on; a full-size RGB
# array is the cost, so more would be memory for a reader who stepped away.
@lru_cache(maxsize=1)
def _load_upright(path: str, mtime_ns: int) -> np.ndarray:
    """The picture as the browser shows it: EXIF-rotated RGB. Cached because
    the Edit tab asks again after every gesture."""
    with Image.open(path) as image:
        return np.array(ImageOps.exif_transpose(image).convert("RGB"))


def load_upright(path: str) -> np.ndarray:
    """:func:`_load_upright` keyed on the file's mtime, so an edit in place is seen."""
    return _load_upright(path, os.stat(path).st_mtime_ns)


def mask_png(selection: Selection, path: str) -> bytes:
    """The selection's mask for the picture at *path*, as a greyscale PNG."""
    buffer = io.BytesIO()
    Image.fromarray(render_mask(selection, load_upright(path)), "L").save(
        buffer, format="PNG"
    )
    return buffer.getvalue()


def write_masked_picture(selection: Selection, path: str, out_path: str) -> None:
    """Write the picture at *path* with the mask in its alpha channel to *out_path*.

    ComfyUI's ``LoadImage`` and the ComfyUI-PixlStash picture loader both read
    ``MASK`` as ``1 - alpha``, so the masked area is written transparent.
    """
    image = load_upright(path)
    alpha = 255 - render_mask(selection, image)
    Image.fromarray(np.dstack([image, alpha]), "RGBA").save(out_path, format="PNG")
