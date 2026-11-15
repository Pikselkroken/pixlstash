"""Character sheets: one MOSS-Image picture of one person, drawn six times.

The "Create a training set…" dialog can draw a character instead of picking
from pictures that exist. A sheet is a 2560×1536 grid: four passport photos on
the left half (front neutral, three-quarter smiling, profile neutral, other
three-quarter laughing) and two tall body photos on the right (front, three
quarter) at the framing the person picked. Text pins down a type, not a person,
so each sheet is a slightly different person; within one sheet the six faces
agree, which is why the person picks one sheet and its six panels become the
character's pictures.

A sheet can also be drawn from a reference picture plus the words (#2679):
an edit workflow (Qwen-Image-Edit) reads the picture for the face and draws on
an empty canvas of the sheet's size. No probe has measured that route yet.

Four pure pieces live here, the routes in ``routes/character_sheets.py``:

* :func:`sheet_prompt` builds the MOSS layout prompt (the JSON
  ``MOSSImageLayoutPrompt`` emits). The boxes and captions are the ones that
  held their layout on 16 of 16 probe sheets; change them only with new probes.
  With ``layout="prose"`` it is the same sheet in one sentence.
* :func:`convert_to_sheet` rewrites an edit workflow's graph to draw at the
  sheet's size (``POST /workflows/{workflow_id}/sheet-copy``).
* :func:`split_sheet` cuts a drawn sheet into its six panels.
* :func:`agreement` / :func:`closeness` score a sheet's faces, against each
  other and against the character's reference faces.

And two that touch the library, each a ``*_in_session`` function behind a thin
vault wrapper (backend architecture §10.1): :func:`rank_sheets` and
:func:`use_sheet`.
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
from datetime import timezone
from itertools import combinations
from typing import Literal

import numpy as np
from PIL import Image
from sqlalchemy import or_
from sqlmodel import Session, select

from pixlstash.db_models import Character, CharacterSheetDraw, Face, Picture
from pixlstash.image_plugins.service import import_derived_outputs
from pixlstash.pixl_logging import get_logger
from pixlstash.scoring import select_reference_faces_for_character
from pixlstash.services import comfyui_service
from pixlstash.services.move_reconciliation_service import add_person
from pixlstash.services.run_log import LOST_MESSAGE
from pixlstash.services.workflow_hash import (
    WorkflowGraphError,
    is_link,
    reduce_api_graph,
)
from pixlstash.services.workflow_identity import reference_input_count
from pixlstash.services.workflow_io import is_picture_loader
from pixlstash.utils.image_processing.image_utils import ImageUtils

logger = get_logger(__name__)

SHEET_WIDTH = 2560
SHEET_HEIGHT = 1536
PANEL_COUNT = 6
# More faces than panels means a panel holds more than one person: the sheet
# broke its layout and is set aside.
MAX_FACES = PANEL_COUNT

Framing = Literal["waist_up", "mid_thigh", "knees_up", "full_body"]
Subject = Literal["woman", "man"]
Size = Literal["small", "medium", "large"]
# How the prompt is written: MOSS's layout JSON, or the sheet in one sentence.
Layout = Literal["moss", "prose"]
# The sheet's canvas at each size, all 5:3 so the layout only scales, and every
# side a multiple of 16 as MOSS needs: ~1, ~2 and ~4 megapixels, MOSS's own
# range (it is trained at 1-2 MP and stretches to about 4).
SIZES = {"small": (1280, 768), "medium": (1840, 1104), "large": (2560, 1536)}

# ponytail: words from scores; thresholds read off the 2026-11 probes (one
# sheet 0.57-0.70, across sheets 0.37). P6 validation tunes them.
STRONG_AGREEMENT = 0.55
SOME_AGREEMENT = 0.45
CLOSE_TO_REFERENCES = 0.45
# Grey level over which a line of the sheet counts as its white frame: the
# frame is ~250, an overcast sky in a photo ~237.
FRAME_LEVEL = 244

_PRONOUN = {"woman": ("her", "Her"), "man": ("his", "His")}

# The passport half, in MOSS `abs_centralized` / `yxyx` boxes on the 2560×1536
# canvas (y -768..768, x -1280..1280). Panel 1's boxes; panels 2-4 are offset.
_PASSPORT_OFFSETS = ((0, 0), (0, 600), (768, 0), (768, 600))
_PASSPORT_BOXES = (
    (-748, -1260, -20, -700),  # the whole panel
    (-718, -1170, -326, -790),  # head
    (-326, -1260, -20, -700),  # shoulders
)
_PASSPORT_VIEWS = (
    (
        "front view, neutral expression",
        "{P} head faces the camera straight on, neutral relaxed expression, "
        "eyes looking into the lens.",
    ),
    (
        "three-quarter view turned to {p} left, smiling",
        "{P} head is turned in three-quarter view to {p} left, both eyes "
        "visible, smiling warmly.",
    ),
    (
        "side profile facing right, neutral expression",
        "{P} head is in strict side profile facing right, only one eye visible, "
        "neutral expression.",
    ),
    (
        "three-quarter view turned to {p} right, laughing",
        "{P} head is turned in three-quarter view to {p} right, both eyes "
        "visible, laughing with {p} mouth open.",
    ),
)

_WAIST_UP_P5 = (
    (
        (-740, -70, 748, 590),
        "Half-length photo from {p} head down to {p} waist, standing upright "
        "facing the camera straight on, arms relaxed at the sides. The photo is "
        "cropped at the waist.",
    ),
    (
        (-700, 85, -280, 435),
        "{P} head faces the camera straight on, eyes looking into the lens.",
    ),
    (
        (-280, -70, 748, 590),
        "{p} chest and stomach in the {top}, shoulders relaxed, the arms cropped "
        "at the edges; the photo ends at {p} waist. {P} body shape: {body}.",
    ),
)
_MID_THIGH_P5 = (
    (
        (-740, -70, 748, 590),
        "Three-quarter-length photo from {p} head down to mid-thigh, standing "
        "upright facing the camera straight on, arms relaxed at the sides. The "
        "photo is cropped at mid-thigh.",
    ),
    (
        (-725, 125, -410, 395),
        "{P} head faces the camera straight on, eyes looking into the lens.",
    ),
    (
        (-410, -75, 330, 595),
        "{p} torso, upright and relaxed, arms hanging naturally at the sides. "
        "{P} body shape: {body}.",
    ),
    (
        (330, -55, 748, 575),
        "{p} hips and upper thighs in {trousers}; the photo ends at mid-thigh.",
    ),
)
_KNEES_UP_P5 = (
    (
        (-740, -40, 748, 560),
        "Three-quarter-length photo from {p} head down to just above {p} knees, "
        "standing upright facing the camera straight on, arms relaxed at the "
        "sides. The photo is cropped above the knees.",
    ),
    (
        (-720, 145, -460, 375),
        "{P} head faces the camera straight on, eyes looking into the lens.",
    ),
    (
        (-460, -20, 170, 540),
        "{p} torso, upright and relaxed, arms hanging naturally at the sides. "
        "{P} body shape: {body}.",
    ),
    (
        (170, 0, 748, 520),
        "{p} hips and thighs in {trousers}; the photo ends just above {p} knees.",
    ),
)
# Panel 6 of the cropped framings is panel 5 moved one body cell right, turned.
_TURNED = {
    "facing the camera straight on, arms relaxed at the sides": (
        "with {p} body turned 45 degrees to {p} left, three-quarter view, arms relaxed"
    ),
    "{P} head faces the camera straight on, eyes looking into the lens.": (
        "{P} head is turned in three-quarter view to {p} left, both eyes "
        "visible, looking toward the camera."
    ),
}
_FULL_BODY = (
    (
        (
            (-675, 99, 691, 421),
            "The {subject} stands upright facing the camera straight on, full "
            "body, seen from the front at eye level, arms relaxed at the sides.",
        ),
        (
            (-675, 193, -483, 327),
            "The head faces the camera straight on, eyes looking into the lens.",
        ),
        (
            (-483, 99, 8, 421),
            "The torso is upright and relaxed, arms hanging naturally at the "
            "sides. {P} body shape: {body}.",
        ),
        (
            (8, 131, 645, 389),
            "The legs are straight and slightly apart, facing the camera.",
        ),
        ((599, 125, 691, 395), "Both feet stand flat on the floor."),
    ),
    (
        (
            (-675, 802, 691, 1078),
            "The {subject} stands upright with the body turned 45 degrees to its "
            "left, full body, three-quarter view at eye level, arms relaxed.",
        ),
        (
            (-675, 883, -483, 997),
            "The head is turned in three-quarter view to the {subject}'s left, "
            "both eyes visible, looking toward the camera.",
        ),
        (
            (-483, 802, 8, 1078),
            "The torso is upright and relaxed, arms hanging naturally at the "
            "sides. {P} body shape: {body}.",
        ),
        (
            (8, 829, 645, 1051),
            "The legs are straight, the body turned 45 degrees away from the camera.",
        ),
        ((599, 824, 691, 1056), "Both feet stand flat on the floor."),
    ),
)
_FRAMING_SUMMARY = {
    "waist_up": "half-length photos side by side, each spanning the full "
    "height and cropped at the waist",
    "mid_thigh": "three-quarter-length photos side by side, each spanning the "
    "full height and cropped at mid-thigh",
    "knees_up": "three-quarter-length photos side by side, each spanning the "
    "full height and cropped just above the knees",
    "full_body": "full-body photos side by side, each spanning the full height",
}

NEGATIVE_PROMPT = "nsfw, worst quality, bad anatomy, watermark, jpeg artifact"


def _phrase(text: str) -> str:
    """*text* as a clause inside a sentence: trimmed, no closing full stop."""
    text = " ".join((text or "").split()).rstrip(".")
    # "Adult woman…" reads "adult woman…" mid-sentence; an acronym stays.
    if len(text) > 1 and text[0].isupper() and not text[1].isupper():
        text = text[0].lower() + text[1:]
    return text


def _body_items(framing: Framing) -> list[tuple[tuple[int, ...], str]]:
    """Panel 5 and 6's boxes and captions for *framing*, in panel order."""
    if framing == "full_body":
        return [
            (box, f"Panel {n}: {cap}")
            for n, items in ((5, _FULL_BODY[0]), (6, _FULL_BODY[1]))
            for box, cap in items
        ]
    p5 = {
        "waist_up": _WAIST_UP_P5,
        "mid_thigh": _MID_THIGH_P5,
        "knees_up": _KNEES_UP_P5,
    }[framing]
    out = [(box, f"Panel 5: {cap}") for box, cap in p5]
    for (y1, x1, y2, x2), cap in p5:
        for front, turned in _TURNED.items():
            cap = cap.replace(front, turned)
        out.append(((y1, x1 + 680, y2, x2 + 680), f"Panel 6: {cap}"))
    return out


def sheet_prompt(
    *,
    description: str,
    body: str = "",
    top: str = "",
    trousers: str = "",
    shoes: str = "",
    framing: Framing = "mid_thigh",
    subject: Subject = "woman",
    trigger: str = "",
    size: Size = "large",
    reference: bool = False,
    layout: Layout = "moss",
) -> str:
    """The prompt for one grid sheet of the character described.

    The MOSS layout prompt by default; with *layout* ``prose``, the same
    sheet as one plain sentence, for a model that takes no layout (an edit
    model drawing from a reference picture). With *reference*, every photo
    names the person in the reference picture ahead of the description: the
    picture sets the face, the words the build and the clothes.

    Every panel's caption carries the whole character, the body included: a
    different build draws a different face, so the build is part of who it is.
    Trousers are always named (left out, MOSS improvises bare legs); shoes only
    at full body, where they belong, since naming them pulls a cropped framing
    to full length. *trigger* is a character LoRA's trigger word, put first.
    The boxes are laid out on the large canvas and scaled to *size*'s.
    """
    scale = SIZES[size][0] / SHEET_WIDTH
    p, cap_p = _PRONOUN[subject]
    body = _phrase(body) or "average build"
    top = _phrase(top) or "a plain crew-neck sweater"
    trousers = _phrase(trousers) or "dark straight jeans"
    shoes = _phrase(shoes) if framing == "full_body" else ""
    full_outfit = f"{top}, {trousers} and {shoes}" if shoes else f"{top} and {trousers}"
    lead = f"{_phrase(trigger)}, " if _phrase(trigger) else ""
    described = _phrase(description)
    if reference:
        # ponytail: provisional wording, no probe has measured it (#2679).
        same = f"the same {subject} as in the reference picture"
        described = f"{same}, {described}" if described else same
    who = f"{lead}{described or f'an adult {subject}'}, {body}"
    character = f"{who}, wearing {full_outfit}"
    # A passport photo shows only the top; at full body the trousers and shoes
    # would pull those panels to full length too.
    passport_character = (
        f"{who}, wearing {top}" if framing == "full_body" else character
    )

    def fill(text: str) -> str:
        return text.format(
            p=p, P=cap_p, subject=subject, body=body, trousers=trousers, top=top
        )

    items = []
    for n, ((dy, dx), (view, head)) in enumerate(
        zip(_PASSPORT_OFFSETS, _PASSPORT_VIEWS), start=1
    ):
        whole, head_box, shoulders = (
            (y1 + dy, x1 + dx, y2 + dy, x2 + dx) for y1, x1, y2, x2 in _PASSPORT_BOXES
        )
        captions = (
            f"{passport_character}. Passport-style head and shoulders photo, "
            f"{fill(view)}.",
            fill(head),
            fill("{p} shoulders and the top of the {top}, relaxed."),
        )
        for label, box, caption in zip(
            ("character", "object", "object"), (whole, head_box, shoulders), captions
        ):
            items.append(
                {
                    "label": label,
                    "caption": f"Panel {n}: {caption}",
                    "bbox": [round(v * scale) for v in box],
                }
            )
    body_items = _body_items(framing)
    per_panel = len(body_items) // 2
    for index, (box, caption) in enumerate(body_items):
        first = index % per_panel == 0
        prefix, _, rest = caption.partition(": ")
        text = f"{character}. {fill(rest)}" if first else fill(rest)
        items.append(
            {
                "label": "character" if first else "object",
                "caption": f"{prefix}: {text}",
                "bbox": [round(v * scale) for v in box],
            }
        )
    views = ", ".join(
        f"({n}) {fill(view)}" for n, (view, _) in enumerate(_PASSPORT_VIEWS, start=1)
    )
    summary = (
        f"Character reference sheet of one and the same {subject} on a plain "
        "light-grey studio background, soft even studio lighting, photorealistic "
        "photographs. On the left half, four passport-style head-and-shoulders "
        f"photos in two rows of two: {views}. On the right half, two tall "
        f"{_FRAMING_SUMMARY[framing]}: (5) standing, front view at eye level, "
        f"(6) standing, three-quarter view. The {subject} is {character}. "
        "Identical face, hair, body shape and clothing in every photo."
    )
    if layout == "prose":
        return summary
    return json.dumps(
        {
            "summary": {"caption": summary},
            "items": items,
            "bbox_coord_space": "abs_centralized",
            "bbox_format": "yxyx",
        }
    )


class SheetConversionRefused(ValueError):
    """The graph is not one :func:`convert_to_sheet` can rewrite; ``reason`` says why."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def convert_to_sheet(graph: dict) -> dict:
    """A copy of an image-edit API graph that draws on a canvas of its own.

    An edit workflow draws at its picture's size: the sampler starts from an
    empty latent sized from the picture, or from the picture's own latent. A
    sheet is drawn from a reference picture at the sheet's size, so the copy's
    sampler starts from an empty latent with a literal ``width`` and
    ``height`` (the large sheet's; a run sets its own), and the picture
    reaches the reference input only. *graph* is not modified.

    Raises:
        SheetConversionRefused: No picture is read, there is not exactly one
            sampler, the canvas already has a size of its own, the sampler
            starts from something this cannot replace, or the picture does
            not reach a reference input.
    """
    out = copy.deepcopy(graph)
    nodes = {
        node_id: node
        for node_id, node in out.items()
        if isinstance(node, dict) and isinstance(node.get("class_type"), str)
    }
    classes = [node["class_type"] for node in nodes.values()]
    if not any(is_picture_loader(cls) for cls in classes):
        raise SheetConversionRefused(
            "This workflow reads no picture, so it has nowhere to take a reference."
        )
    samplers = [
        node
        for node in nodes.values()
        if "Sampler" in node["class_type"]
        and is_link((node.get("inputs") or {}).get("latent_image"))
    ]
    if len(samplers) != 1:
        raise SheetConversionRefused(
            f"This workflow samples in {len(samplers)} passes. Only a "
            "single-pass workflow can be made into a sheet workflow."
        )
    inputs = samplers[0]["inputs"]
    source = nodes.get(str(inputs["latent_image"][0])) or {}
    source_class = source.get("class_type", "")
    width, height = SIZES["large"]
    if re.search(r"Empty.*Latent", source_class):
        size = source.setdefault("inputs", {})
        if not is_link(size.get("width")) and not is_link(size.get("height")):
            raise SheetConversionRefused(
                "This workflow already draws at a size of its own, so it can "
                "draw a sheet as it is."
            )
        size["width"], size["height"] = width, height
    elif source_class == "VAEEncode" and any(
        cls.startswith("TextEncodeQwenImageEdit") for cls in classes
    ):
        # ponytail: Qwen-Image-Edit only, the one model whose template starts
        # from the picture's own latent that sheets are drawn with. Another
        # needs its own empty latent class (4, 16 or 32 channels).
        new_id = str(max((int(k) for k in out if str(k).isdigit()), default=0) + 1)
        out[new_id] = {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
            "_meta": {"title": "Sheet canvas"},
        }
        inputs["latent_image"] = [new_id, 0]
    else:
        raise SheetConversionRefused(
            f"Its sampler starts from {source_class or 'nothing'}, which "
            "PixlStash cannot swap for a canvas of the sheet's size."
        )
    # A picture's latent can be drawn over in part; an empty canvas cannot.
    if "denoise" in inputs and not is_link(inputs["denoise"]):
        inputs["denoise"] = 1.0
    # A picture that only poses a ControlNet, or reaches nothing, is no
    # reference: the copy would draw a sheet from words alone.
    try:
        references = reference_input_count(reduce_api_graph(out))
    except (WorkflowGraphError, TypeError, KeyError) as exc:
        logger.warning("A sheet copy's graph could not be read: %s", exc)
        references = 0
    if not references:
        raise SheetConversionRefused(
            "This workflow's picture does not reach an edit model's reference "
            "input, so a sheet would be drawn from words alone."
        )
    return out


def _strongest_cut(
    gray: np.ndarray, nominal: int, across: slice, window: int, vertical: bool
) -> int:
    """The line near *nominal* that a full-length luminance step runs along.

    A divider between panels is an edge that runs the whole length of the cut;
    a person's outline is not. Each candidate line scores the share of its
    pixels (over *across*) whose step to the previous line is over 4 grey
    levels. The outer border is never a candidate. With no candidate scoring
    over 0.6 (a seamless studio grey), the nominal line stands.
    """
    plane = gray if vertical else gray.T
    size = plane.shape[1]
    lo = max(1, nominal - window)
    hi = min(size - 1, nominal + window)
    if hi <= lo:
        return nominal
    steps = np.abs(np.diff(plane[across, lo - 1 : hi + 1], axis=1)) > 4
    share = steps.mean(axis=0)
    best = int(np.argmax(share))
    return lo + best if share[best] > 0.6 else nominal


def sheet_cells(image: Image.Image) -> list[tuple[int, int, int, int]]:
    """The six panels' ``(left, top, right, bottom)`` boxes on *image*, in order.

    The grid's own geometry (passport cells 600×768, body cells 680×1536 at
    2560×1536), scaled to the picture, each cut then moved to the strongest
    full-length edge within ±50 px: MOSS draws its dividers up to ~50 px off
    the layout. A gutter of 8 px either side of a cut is trimmed off, and then
    whatever is left of the white frame round each photo.
    """
    width, height = image.size
    sx, sy = width / SHEET_WIDTH, height / SHEET_HEIGHT
    gray = np.asarray(image.convert("L"), dtype=np.int16)
    window = max(1, round(50 * sx))
    gutter = round(8 * sx)
    half = _strongest_cut(gray, round(1200 * sx), slice(0, height), window, True)
    bodies = _strongest_cut(gray, round(1880 * sx), slice(0, height), window, True)
    column = _strongest_cut(gray, round(600 * sx), slice(0, height), window, True)
    row = _strongest_cut(gray, round(768 * sy), slice(0, half), window, False)
    left = (0, column - gutter, column + gutter, half - gutter)
    top = (0, row - gutter, row + gutter, height)
    cells = [
        (left[0], top[0], left[1], top[1]),
        (left[2], top[0], left[3], top[1]),
        (left[0], top[2], left[1], top[3]),
        (left[2], top[2], left[3], top[3]),
        (half + gutter, 0, bodies - gutter, height),
        (bodies + gutter, 0, width, height),
    ]
    limit, bleed = round(64 * sx), max(1, round(2 * sx))
    return [_inside_frame(gray, cell, limit, bleed) for cell in cells]


def _inside_frame(
    gray: np.ndarray, cell: tuple[int, int, int, int], limit: int, bleed: int
) -> tuple[int, int, int, int]:
    """*cell* shrunk past the white frame MOSS draws round each photo.

    The sheet's outer border and the gutters between photos are white, wider
    than the cut's own gutter. From each edge, every line that is almost all
    near-white is frame, up to *limit* lines. The photos have rounded corners,
    so each edge then also steps in as far as the white runs diagonally in from
    its corners. *bleed* more go with any trim, for the soft seam.
    """
    # ponytail: a photo whose own edge is blown-out white loses up to *limit*
    # lines with the frame; tell frame from photo by texture if that shows.

    def run(flags: np.ndarray) -> int:
        flags = flags[:limit]
        return len(flags) if flags.all() else int(np.argmin(flags))

    def trimmed(n: int) -> int:
        return n + bleed if n else 0

    left, top, right, bottom = cell
    white = gray[top:bottom, left:right] > FRAME_LEVEL
    rows, cols = white.mean(axis=1) > 0.95, white.mean(axis=0) > 0.95
    left += trimmed(run(cols))
    top += trimmed(run(rows))
    right -= trimmed(run(cols[::-1]))
    bottom -= trimmed(run(rows[::-1]))
    white = gray[top:bottom, left:right] > FRAME_LEVEL
    corner = {
        (ys, xs): run(np.diagonal(white[::ys, ::xs]))
        for ys in (1, -1)
        for xs in (1, -1)
    }
    return (
        left + trimmed(max(corner[1, 1], corner[-1, 1])),
        top + trimmed(max(corner[1, 1], corner[1, -1])),
        right - trimmed(max(corner[1, -1], corner[-1, -1])),
        bottom - trimmed(max(corner[-1, 1], corner[-1, -1])),
    )


def split_sheet(image: Image.Image) -> list[Image.Image]:
    """The sheet's six panels as pictures: four passport photos, then two bodies."""
    return [image.crop(box) for box in sheet_cells(image)]


def _unit(vectors: list[np.ndarray]) -> np.ndarray:
    matrix = np.stack(vectors).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1, norms)


def agreement(embeddings: list[np.ndarray]) -> float | None:
    """How alike a sheet's faces are: the mean pairwise cosine similarity.

    ``None`` with fewer than two faces, which is nothing to agree.
    """
    if len(embeddings) < 2:
        return None
    unit = _unit(embeddings)
    return float(
        np.mean([unit[a] @ unit[b] for a, b in combinations(range(len(unit)), 2)])
    )


def closeness(
    embeddings: list[np.ndarray], references: list[np.ndarray]
) -> float | None:
    """How close a sheet's faces come to the character's: the mean cosine similarity."""
    if not embeddings or not references:
        return None
    return float(np.mean(_unit(embeddings) @ _unit(references).T))


def agreement_words(score: float | None) -> str:
    """A sheet's agreement as the dialog says it."""
    if score is None or score < SOME_AGREEMENT:
        return "faces don't hold"
    return (
        "faces agree strongly" if score >= STRONG_AGREEMENT else "faces agree somewhat"
    )


class SheetNotFound(LookupError):
    """The character or the sheet picture does not exist."""


class SheetUnreadable(ValueError):
    """The sheet's file is missing or is not an image."""


class SheetUsed(ValueError):
    """The sheet was cut into pictures already, in the draw named."""


def _vectors(rows: list) -> list[np.ndarray]:
    return [np.frombuffer(features, dtype=np.float32) for features in rows if features]


def sheet_faces_in_session(
    session: Session,
    character_id: int,
    picture_ids: list[int],
    reference_picture_ids: list[int] | None = None,
) -> tuple[dict[int, list], list, dict[int, str]] | None:
    """Each sheet's face embeddings, the reference embeddings, each sheet's format.

    ``None`` when the character does not exist. A sheet's list holds a ``None``
    for the row face extraction leaves on a picture with no face, so an empty
    list means its faces are not read yet. The references are the character's
    reference faces, or, given *reference_picture_ids*, the faces of those
    pictures: what the sheets were drawn from. The sheets' own faces are never
    their own references. The format is what the picture's file is served as.
    """
    if session.get(Character, character_id) is None:
        return None
    faces: dict[int, list] = {pid: [] for pid in picture_ids}
    for picture_id, features in session.exec(
        select(Face.picture_id, Face.features).where(Face.picture_id.in_(picture_ids))
    ).all():
        faces[picture_id].append(features)
    if reference_picture_ids is None:
        references = [
            face.features
            for face in select_reference_faces_for_character(session, character_id)
            if face.picture_id not in faces
        ]
    else:
        # ponytail: every face of a picked picture counts; a group photo as
        # the reference measures against everyone in it.
        references = session.exec(
            select(Face.features).where(
                Face.picture_id.in_(set(reference_picture_ids) - set(faces))
            )
        ).all()
    formats = dict(
        session.exec(
            select(Picture.id, Picture.format).where(Picture.id.in_(picture_ids))
        ).all()
    )
    return faces, references, formats


def rank_sheets(
    vault,
    character_id: int,
    picture_ids: list[int],
    reference_picture_ids: list[int] | None = None,
) -> tuple[list[dict], int]:
    """Score each sheet by its own faces and by the reference faces.

    The references are the character's, or the faces of
    *reference_picture_ids* when given (an unknown id has no faces and is
    ignored). Returns one dict per sheet, in the order asked, and how many
    reference faces closeness was measured against. Raises
    :class:`SheetNotFound` for an unknown character.
    """
    ids = list(dict.fromkeys(picture_ids))
    found = vault.db.run_immediate_read_task(
        sheet_faces_in_session, character_id, ids, reference_picture_ids
    )
    if found is None:
        raise SheetNotFound(f"No character {character_id}.")
    faces, reference_rows, formats = found
    references = _vectors(reference_rows)
    vectors_of = {pid: _vectors(faces[pid]) for pid in ids}
    out = []
    for picture_id in ids:
        rows = faces[picture_id]
        vectors = vectors_of[picture_id]
        score = agreement(vectors)
        near = closeness(vectors, references)
        out.append(
            {
                "picture_id": picture_id,
                "format": (formats.get(picture_id) or "").lower() or None,
                "faces_read": bool(rows),
                "faces": len(vectors),
                "agreement": score,
                "agreement_words": agreement_words(score),
                "closeness": near,
                "close": None if near is None else near >= CLOSE_TO_REFERENCES,
                "broken": len(vectors) > MAX_FACES,
                # Kept together, these would mix two people into one set: their
                # faces agree across sheets less than one sheet's need to hold.
                "different_from": [
                    other
                    for other in ids
                    if other != picture_id
                    and vectors
                    and vectors_of[other]
                    and closeness(vectors, vectors_of[other]) < SOME_AGREEMENT
                ],
            }
        )
    return out, len(references)


def sheets_in_session(
    session: Session, character_id: int, picture_ids: list[int]
) -> list[Picture] | None:
    """The sheet pictures, in the order asked; ``None`` when the character or
    any of them does not exist. A held sheet (soft-deleted on import) counts.
    """
    if session.get(Character, character_id) is None:
        return None
    sheets = [session.get(Picture, pid) for pid in picture_ids]
    return None if any(sheet is None for sheet in sheets) else sheets


def file_on_character_in_session(
    session: Session, picture_ids: list[int], character_id: int
) -> None:
    """File each picture on the character: its largest face, or once one is read.

    Each also drops the link to its sheet: the sheet stays in the Scrapheap,
    and a picture naming it as source would fail its purge on the foreign key.
    The link only lets source-face likeness pass on a character, and a sheet's
    faces are nobody's.
    """
    for picture in session.exec(
        select(Picture).where(Picture.id.in_(picture_ids))
    ).all():
        add_person(session, picture, character_id)
        picture.source_picture_id = None
        session.add(picture)
    session.commit()


def _cut(server, sheet: Picture) -> list[tuple[bytes, str]]:
    """The sheet's six panels as PNG bytes, ready to import."""
    path = ImageUtils.resolve_picture_path(server.vault.image_root, sheet.file_path)
    if not path or not os.path.isfile(path):
        raise SheetUnreadable(
            f"Picture {sheet.id}'s file is missing, so it can't be cut."
        )
    try:
        with Image.open(path) as image:
            panels = split_sheet(image.convert("RGB"))
    except OSError as exc:
        logger.warning(
            "Couldn't read character sheet %s at %s to cut it: %s", sheet.id, path, exc
        )
        raise SheetUnreadable(f"Picture {sheet.id} can't be read as an image.") from exc
    entries = []
    for panel in panels:
        buffer = io.BytesIO()
        panel.save(buffer, format="PNG")
        entries.append((buffer.getvalue(), ".png"))
    return entries


def use_sheets(
    server,
    character_id: int,
    picture_ids: list[int],
    draw_id: int | None = None,
) -> list[int]:
    """Cut each sheet into its six panels and file them on the character.

    Passport photos first, sheet by sheet. Every sheet is read before anything
    is imported, so an unreadable one imports nothing. Returns the panels'
    picture ids. With *draw_id*, the panels and the sheets cut are recorded on
    that draw once the cut succeeds. Raises :class:`SheetNotFound` (also for a
    *draw_id* that is not this character's), :class:`SheetUsed` for a sheet
    that draw has cut already, and :class:`SheetUnreadable`.
    The sheets themselves stay in the Scrapheap.
    """
    ids = list(dict.fromkeys(picture_ids))
    if draw_id is not None:
        used = server.vault.db.run_immediate_read_task(
            used_sheets_in_session, character_id, draw_id
        )
        if used is None:
            raise SheetNotFound(f"No draw {draw_id} on character {character_id}.")
        # The screen offers a cut sheet no more; a second tab or a repeated
        # request must not file its panels twice either.
        again = [sheet_id for sheet_id in ids if sheet_id in used]
        if again:
            raise SheetUsed(f"Sheets {again} were cut already.")
    sheets = server.vault.db.run_immediate_read_task(
        sheets_in_session, character_id, ids
    )
    if sheets is None:
        raise SheetNotFound(f"No character {character_id} or no picture among {ids}.")
    cuts = [(sheet, _cut(server, sheet)) for sheet in sheets]
    panel_ids: list[int] = []
    for sheet, entries in cuts:
        _new, _duplicates, made = import_derived_outputs(
            server, [sheet] * len(entries), entries, stack=False
        )
        # Filed sheet by sheet: a later sheet failing to import must not leave
        # these on no character, still linked to a sheet the purge removes.
        server.vault.db.run_task(file_on_character_in_session, made, character_id)
        panel_ids.extend(made)
    logger.info(
        "Character sheets %s cut into pictures %s, filed on character %s",
        ids,
        panel_ids,
        character_id,
    )
    if draw_id is not None and not server.vault.db.run_task(
        record_used_in_session, character_id, draw_id, panel_ids, ids
    ):
        logger.warning(
            "Draw %s on character %s was deleted while sheets %s were cut; "
            "panels %s are filed but not recorded on it.",
            draw_id,
            character_id,
            ids,
            panel_ids,
        )
    return panel_ids


# --- Character sheet draws: the dialog's state, kept on the character ---------
#
# A draw is one run of "Draw" on a character (``db_models/character_sheet_draw``):
# its settings (opaque here), its runs (ComfyUI runs keyed by run id or prompt
# id, each settled to queued/running/completed/failed), the sheets they made,
# the sheets kept, the panels filed from them, and whether it was seen. A run
# is settled from ``comfyui_service.run_outcome`` when the draws are read, and
# from ``comfyui_service``'s ending listener (``settle_run``) when it ends with
# nobody reading. Both go through ``_settle_runs`` / ``_take``.

# A draw's settings, serialised: more is refused before it is stored.
DRAW_SETTINGS_MAX = 20_000
DRAW_RUN_KEY_MAX = 64
_RUN_STATUSES = ("queued", "running", "completed", "failed")
_RUN_TERMINAL = ("completed", "failed")


def _ints(values) -> list[int]:
    """The integers in *values*: booleans and anything else are dropped."""
    return [v for v in values or [] if isinstance(v, int) and not isinstance(v, bool)]


def _json_list(text: str | None, column: str, draw_id: int) -> list:
    """A stored JSON list, read tolerantly: unreadable is logged and empty."""
    try:
        value = json.loads(text or "[]")
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Character sheet draw %s has an unreadable %s column (%s); reading "
            "it as empty.",
            draw_id,
            column,
            exc,
        )
        return []
    if not isinstance(value, list):
        logger.warning(
            "Character sheet draw %s has a %s column that is not a list; reading "
            "it as empty.",
            draw_id,
            column,
        )
        return []
    return value


def _settings_of(text: str | None, draw_id: int) -> dict:
    """The stored settings object, read tolerantly: unreadable is logged and empty."""
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Character sheet draw %s has unreadable settings (%s); reading them "
            "as empty.",
            draw_id,
            exc,
        )
        return {}
    if not isinstance(value, dict):
        logger.warning(
            "Character sheet draw %s has settings that are not an object; "
            "reading them as empty.",
            draw_id,
        )
        return {}
    return value


def _runs_of(text: str | None, draw_id: int) -> list[dict]:
    """The stored runs, each checked: a malformed one is logged and dropped."""
    runs = []
    for item in _json_list(text, "runs", draw_id):
        if (
            isinstance(item, dict)
            and isinstance(item.get("key"), str)
            and item.get("status") in _RUN_STATUSES
        ):
            runs.append(
                {
                    "key": item["key"],
                    "status": item["status"],
                    "message": str(item.get("message") or ""),
                    "picture_ids": _ints(item.get("picture_ids")),
                }
            )
        else:
            logger.warning(
                "Character sheet draw %s has a malformed run %r; dropping it.",
                draw_id,
                item,
            )
    return runs


def _new_run(key: str) -> dict:
    return {"key": key, "status": "queued", "message": "", "picture_ids": []}


def _take(run: dict, outcome: dict) -> bool:
    """Copy what *outcome* says of *run* onto it. True when something changed."""
    status = outcome.get("status")
    if status in _RUN_TERMINAL:
        new = {
            "status": status,
            "message": str(outcome.get("message") or ""),
            "picture_ids": _ints(outcome.get("picture_ids")),
        }
    elif status == "running" and run["status"] != "running":
        new = {"status": "running"}
    else:
        return False
    if all(run.get(field) == value for field, value in new.items()):
        return False
    run.update(new)
    return True


def _settle_runs(runs: list[dict], lost_when_unknown: bool) -> bool:
    """Settle *runs* from ``comfyui_service.run_outcome``; True when any changed.

    A run with no recorded outcome is lost (PixlStash restarted before it
    ended) when *lost_when_unknown*; otherwise it is left as it is, for a run
    just handed in whose ending may not be recorded yet.
    """
    changed = False
    for run in runs:
        if run["status"] in _RUN_TERMINAL:
            continue
        outcome = comfyui_service.run_outcome(run["key"])
        if outcome is None:
            if lost_when_unknown:
                run.update(status="failed", message=LOST_MESSAGE, picture_ids=[])
                changed = True
            continue
        changed = _take(run, outcome) or changed
    return changed


def _draw_out(row: CharacterSheetDraw, runs: list[dict]) -> dict:
    """A draw as the API returns it."""
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return {
        "id": row.id,
        "character_id": row.character_id,
        "created_at": created.isoformat(),
        "settings": _settings_of(row.settings, row.id),
        "drawn_from": row.drawn_from,
        "runs": runs,
        "kept": _ints(_json_list(row.kept, "kept", row.id)),
        "used": _ints(_json_list(row.used, "used", row.id)),
        "used_sheets": _ints(_json_list(row.used_sheets, "used_sheets", row.id)),
        "seen": bool(row.seen),
    }


def _settle_row(session: Session, row: CharacterSheetDraw, lost: bool) -> dict:
    """Settle one draw's runs, persist what changed, and return it as a dict."""
    runs = _runs_of(row.runs, row.id)
    if _settle_runs(runs, lost):
        row.runs = json.dumps(runs)
        session.add(row)
    return _draw_out(row, runs)


def _draw_of(session: Session, character_id: int, draw_id: int):
    """The draw row, when it exists and is *character_id*'s; else ``None``."""
    row = session.get(CharacterSheetDraw, draw_id)
    return row if row is not None and row.character_id == character_id else None


def _newest_first():
    return (CharacterSheetDraw.created_at.desc(), CharacterSheetDraw.id.desc())


def used_sheets_in_session(
    session: Session, character_id: int, draw_id: int
) -> set[int] | None:
    """The sheets of *draw_id* cut already; ``None`` when it is not *character_id*'s."""
    row = _draw_of(session, character_id, draw_id)
    if row is None:
        return None
    return set(_ints(_json_list(row.used_sheets, "used_sheets", row.id)))


def _without_purged(session: Session, draws: list[dict]) -> list[dict]:
    """The draws as shown: a sheet the Scrapheap's purge removed is left out
    of its run and its kept marks, so nothing waits on faces it will never
    have. The stored row keeps it."""
    named = {
        picture_id
        for draw in draws
        for run in draw["runs"]
        for picture_id in run["picture_ids"]
    }
    if not named:
        return draws
    alive = set(session.exec(select(Picture.id).where(Picture.id.in_(named))).all())
    if alive == named:
        return draws
    for draw in draws:
        for run in draw["runs"]:
            run["picture_ids"] = [i for i in run["picture_ids"] if i in alive]
        draw["kept"] = [i for i in draw["kept"] if i in alive]
    return draws


def list_draws_in_session(session: Session, character_id: int) -> list[dict] | None:
    """A character's draws, newest first, settled; ``None`` for an unknown character."""
    if session.get(Character, character_id) is None:
        return None
    rows = session.exec(
        select(CharacterSheetDraw)
        .where(CharacterSheetDraw.character_id == character_id)
        .order_by(*_newest_first())
    ).all()
    draws = [_settle_row(session, row, lost=True) for row in rows]
    session.commit()
    return _without_purged(session, draws)


def summaries_in_session(session: Session) -> list[dict]:
    """Every character with a draw: its draw count, newest draw, and run keys."""
    rows = session.exec(select(CharacterSheetDraw).order_by(*_newest_first())).all()
    by_character: dict[int, list[dict]] = {}
    for row in rows:
        by_character.setdefault(row.character_id, []).append(
            _settle_row(session, row, lost=True)
        )
    session.commit()
    return [
        {
            "character_id": character_id,
            "draws": len(draws),
            "newest": draws[0],
            "run_keys": [run["key"] for draw in draws for run in draw["runs"]],
        }
        for character_id, draws in by_character.items()
    ]


def create_draw_in_session(
    session: Session,
    character_id: int,
    settings: dict,
    drawn_from: int | None,
    run_keys: list[str],
) -> dict | None:
    """Store a draw of *run_keys* on *character_id*; ``None`` for an unknown character."""
    if session.get(Character, character_id) is None:
        return None
    runs = [_new_run(key) for key in dict.fromkeys(run_keys)]
    row = CharacterSheetDraw(
        character_id=character_id,
        settings=json.dumps(settings),
        drawn_from=drawn_from,
        runs=json.dumps(runs),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    # A run that has already ended is settled now; one still going stays queued.
    if _settle_runs(runs, lost_when_unknown=False):
        row.runs = json.dumps(runs)
        session.add(row)
        session.commit()
    return _draw_out(row, runs)


def update_draw_in_session(
    session: Session,
    character_id: int,
    draw_id: int,
    add_run_keys: list[str] | None,
    kept: list[int] | None,
    seen: bool | None,
) -> dict | None:
    """Change a draw; ``None`` when it is not *character_id*'s."""
    row = _draw_of(session, character_id, draw_id)
    if row is None:
        return None
    runs = _runs_of(row.runs, row.id)
    _settle_runs(runs, lost_when_unknown=True)
    known = {run["key"] for run in runs}
    fresh = [
        _new_run(key) for key in dict.fromkeys(add_run_keys or []) if key not in known
    ]
    _settle_runs(fresh, lost_when_unknown=False)
    runs.extend(fresh)
    row.runs = json.dumps(runs)
    if kept is not None:
        row.kept = json.dumps(list(dict.fromkeys(kept)))
    if seen is not None:
        row.seen = seen
    session.add(row)
    session.commit()
    return _draw_out(row, runs)


def delete_draw_in_session(session: Session, character_id: int, draw_id: int) -> bool:
    """Delete a draw of *character_id*. The sheets it made stay where they are."""
    row = _draw_of(session, character_id, draw_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def record_used_in_session(
    session: Session,
    character_id: int,
    draw_id: int,
    panel_ids: list[int],
    sheet_ids: list[int],
) -> bool:
    """Record the panels filed and the sheets cut on a draw; False when it is gone."""
    row = _draw_of(session, character_id, draw_id)
    if row is None:
        return False
    # Panels are appended as the cut returned them; the sheets cut once each.
    used = _ints(_json_list(row.used, "used", row.id)) + panel_ids
    used_sheets = _ints(_json_list(row.used_sheets, "used_sheets", row.id)) + sheet_ids
    row.used = json.dumps(used)
    row.used_sheets = json.dumps(list(dict.fromkeys(used_sheets)))
    session.add(row)
    session.commit()
    return True


def settle_run_in_session(session: Session, keys: set[str], outcome: dict) -> int:
    """Write an ending onto the draws that hold one of *keys*; how many changed.

    Only a completed or failed ending is written: a run that is still going,
    or is waiting for the GPU, changes nothing here.
    """
    if outcome.get("status") not in _RUN_TERMINAL:
        return 0
    rows = session.exec(
        select(CharacterSheetDraw).where(
            or_(*[CharacterSheetDraw.runs.contains(key) for key in keys])
        )
    ).all()
    changed = 0
    for row in rows:
        runs = _runs_of(row.runs, row.id)
        hit = False
        for run in runs:
            if run["key"] in keys and run["status"] not in _RUN_TERMINAL:
                hit = _take(run, outcome) or hit
        if hit:
            row.runs = json.dumps(runs)
            session.add(row)
            changed += 1
    session.commit()
    return changed


def list_draws(vault, character_id: int) -> list[dict] | None:
    """Vault wrapper: :func:`list_draws_in_session`."""
    return vault.db.run_task(list_draws_in_session, character_id)


def draw_summaries(vault) -> list[dict]:
    """Vault wrapper: :func:`summaries_in_session`."""
    return vault.db.run_task(summaries_in_session)


def create_draw(
    vault,
    character_id: int,
    settings: dict,
    drawn_from: int | None,
    run_keys: list[str],
) -> dict | None:
    """Vault wrapper: :func:`create_draw_in_session`."""
    return vault.db.run_task(
        create_draw_in_session, character_id, settings, drawn_from, run_keys
    )


def update_draw(
    vault,
    character_id: int,
    draw_id: int,
    add_run_keys: list[str] | None,
    kept: list[int] | None,
    seen: bool | None,
) -> dict | None:
    """Vault wrapper: :func:`update_draw_in_session`."""
    return vault.db.run_task(
        update_draw_in_session, character_id, draw_id, add_run_keys, kept, seen
    )


def delete_draw(vault, character_id: int, draw_id: int) -> bool:
    """Vault wrapper: :func:`delete_draw_in_session`."""
    return vault.db.run_task(delete_draw_in_session, character_id, draw_id)


def settle_run(vault, outcome: dict) -> None:
    """Write a run's ending onto its draw, whoever is reading. No library, no-op.

    The run is found by its run id and by its prompt id: the frontend keys a
    run by the first and falls back to the second.
    """
    if vault is None:
        return
    keys = {key for key in (outcome.get("run_id"), outcome.get("prompt_id")) if key}
    if not keys:
        return
    vault.db.run_task(settle_run_in_session, keys, outcome)
