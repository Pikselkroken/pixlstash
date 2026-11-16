"""Character sheets: one picture of one person, drawn six times.

Drawn by MOSS-Image, Krea 2, Qwen-Image, FLUX.2 Klein or MiniMax H3.

The "Create a training set…" dialog can draw a character instead of picking
from pictures that exist. A sheet is a 2560×1536 grid: four passport photos on
the left half (front neutral, three-quarter smiling, profile neutral, other
three-quarter laughing) and two tall body photos on the right (front, three
quarter) at the framing the person picked. Text pins down a type, not a person,
so each sheet is a slightly different person; within one sheet the six faces
agree, which is why the person picks one sheet and its six panels become the
character's pictures.

A sheet can also be drawn from a reference picture plus the words (#2679):
an edit workflow (Qwen-Image-Edit, FLUX.2 Klein, #2700, or MiniMax H3's
reference model) reads the picture
for the face and draws on an empty canvas of the sheet's size. No probe has
measured that route yet.

Four pure pieces live here, the routes in ``routes/character_sheets.py``:

* :func:`sheet_prompt` builds the MOSS layout prompt (the JSON
  ``MOSSImageLayoutPrompt`` emits). The boxes and captions are the ones that
  held their layout on 16 of 16 probe sheets; change them only with new probes.
  With ``layout="prose"`` it is the same sheet in one sentence.
* :func:`convert_to_sheet` rewrites an edit workflow's graph to draw at the
  sheet's size (``POST /workflows/{workflow_id}/sheet-copy``).
* :func:`split_sheet` cuts a drawn sheet into its six panels.
* :func:`agreement` / :func:`closeness` score a sheet's faces, against each
  other and against the character's reference faces, and
  :func:`panel_scores` scores each panel's face on its own.

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
from functools import lru_cache
from itertools import combinations
from typing import Literal

import numpy as np
from PIL import Image
from sqlmodel import Session, select

from pixlstash.db_models import Character, Face, Picture
from pixlstash.image_plugins.service import import_derived_outputs
from pixlstash.pixl_logging import get_logger
from pixlstash.scoring import select_reference_faces_for_character
from pixlstash.services.move_reconciliation_service import add_person
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
# The figure's proportions: a typical adult's, a little person's (#1079) or a
# cartoon's. The probed boxes are a typical adult's, about seven heads tall.
Proportions = Literal["typical", "little_person", "cartoon"]
Size = Literal["small", "medium", "large"]
# How the prompt is written: MOSS's layout JSON, or the sheet in one sentence.
Layout = Literal["moss", "prose"]
# The sheet's canvas at each size, all 5:3 so the layout only scales, and every
# side a multiple of 16 as MOSS needs: ~1, ~2 and ~4 megapixels, MOSS's own
# range (it is trained at 1-2 MP and stretches to about 4), and ~17 for
# MiniMax H3, which wants a large canvas (MiniMax's "4K" is 16.6 MP or more;
# every side a multiple of its 32 too). The step offers each model its own.
SIZES = {
    "small": (1280, 768),
    "medium": (1840, 1104),
    "large": (2560, 1536),
    "4k": (5280, 3168),
}
# MiniMax H3's own size joins the three every model has (a nested ``Literal``
# flattens to all four).
Size = Literal[Size, "4k"]

# ponytail: words from scores; thresholds read off the 2026-11 probes (one
# sheet 0.57-0.70, across sheets 0.37). P6 validation tunes them.
STRONG_AGREEMENT = 0.55
SOME_AGREEMENT = 0.45
CLOSE_TO_REFERENCES = 0.45
# Grey level over which a line of the sheet counts as its white frame: the
# frame is ~250, an overcast sky in a photo ~237.
FRAME_LEVEL = 244
# A gutter is a band of lines each the same all along: within UNIFORM_LEVELS
# grey levels of its own median over UNIFORM_SHARE of its length. A line through
# a photo is not, even one running along a door frame, and a gutter stopping
# short of the edge (85 % of the height) still is.
# ponytail: read off the 130 sheets in the library on 2026-11-16 (MOSS, Krea 2,
# Qwen-Image, FLUX.2 Klein, MiniMax H3); re-read them when a model draws a
# gutter these miss.
UNIFORM_LEVELS = 12
UNIFORM_SHARE = 0.8
# A uniform band at most this wide (at 2560) with a step at its edges is a
# gutter or a drawn line; a wider one is background the photos share.
MAX_GUTTER = 64
# A cut never runs closer to a face's centre than this share of its stored
# box's shorter side. Stored boxes are widened and rounded up to 64 px at the
# 512 px working size, so only the centre is the face's own.
FACE_FENCE = 0.2

# (left, top, right, bottom) on the sheet; a face is its stored box.
Box = tuple[int, int, int, int]

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

# How far down the standing figure each landmark sits, as a fraction of its
# height from the top of the head: chin, waist, hip, mid-thigh, knee. A body
# photo's boxes are reshaped by moving these (`_reshape`); the typical ones
# leave the probed boxes as they are.
# ponytail: textbook proportions, no probe has drawn a little person's or a
# cartoon's sheet yet (#1079); adjust the fractions from probes.
_LANDMARKS = {
    "typical": (0.13, 0.38, 0.5, 0.62, 0.72),
    # Short arms and legs, an average-length torso, a head large for the body.
    # About four heads tall, the legs a third of the height.
    "little_person": (0.24, 0.53, 0.66, 0.75, 0.83),
    # About three heads tall.
    "cartoon": (0.33, 0.6, 0.7, 0.79, 0.86),
}
# Where each framing's photo ends, as an index into a landmark tuple; full
# body ends at the soles.
_CROP = {"waist_up": 1, "mid_thigh": 3, "knees_up": 4, "full_body": None}
_PROPORTION_WORDS = {
    "typical": "",
    "little_person": "a little person with dwarfism, very short stature, about "
    "four heads tall: very short arms and legs, the hands reaching only to "
    "the hips, an average-length torso and a head large for the body",
    # Said adult twice over: big-headed proportions otherwise drift young.
    "cartoon": "a grown adult with an adult face, drawn with stylised cartoon "
    "proportions about three heads tall: an oversized head, a small compact "
    "body and short arms and legs",
}

NEGATIVE_PROMPT = "nsfw, worst quality, bad anatomy, watermark, jpeg artifact"


def _phrase(text: str) -> str:
    """*text* as a clause inside a sentence: trimmed, no closing full stop."""
    text = " ".join((text or "").split()).rstrip(".")
    # "Adult woman…" reads "adult woman…" mid-sentence; an acronym stays.
    if len(text) > 1 and text[0].isupper() and not text[1].isupper():
        text = text[0].lower() + text[1:]
    return text


def _reshape(
    items: tuple, framing: Framing, proportions: Proportions
) -> list[tuple[tuple[int, ...], str]]:
    """One body photo's *items* redrawn for a figure of *proportions*.

    The first item is the whole photo, from the top of the head to where
    *framing* crops it; the second is the head. Every box's top and bottom
    move with the landmarks between them, and the head box widens or narrows
    as much as it grew or shrank, kept inside the photo.
    """
    if proportions == "typical":
        return list(items)
    old = (0.0, *_LANDMARKS["typical"], 1.0)
    new = (0.0, *_LANDMARKS[proportions], 1.0)
    crop = _CROP[framing]
    old_end, new_end = (1.0, 1.0) if crop is None else (old[crop + 1], new[crop + 1])
    (top, left, bottom, right), _ = items[0]

    def move(y: int) -> int:
        t = (y - top) / (bottom - top) * old_end
        return round(top + (bottom - top) * float(np.interp(t, old, new)) / new_end)

    out = []
    for index, ((y1, x1, y2, x2), caption) in enumerate(items):
        n1, n2 = move(y1), move(y2)
        if index == 1:
            half = (x2 - x1) * (n2 - n1) / (y2 - y1) / 2
            middle = (x1 + x2) / 2
            x1, x2 = max(left, round(middle - half)), min(right, round(middle + half))
        out.append(((n1, x1, n2, x2), caption))
    return out


def _body_items(
    framing: Framing, proportions: Proportions = "typical"
) -> list[tuple[tuple[int, ...], str]]:
    """Panel 5 and 6's boxes and captions for *framing*, in panel order."""
    if framing == "full_body":
        return [
            (box, f"Panel {n}: {cap}")
            for n, items in ((5, _FULL_BODY[0]), (6, _FULL_BODY[1]))
            for box, cap in _reshape(items, framing, proportions)
        ]
    p5 = {
        "waist_up": _WAIST_UP_P5,
        "mid_thigh": _MID_THIGH_P5,
        "knees_up": _KNEES_UP_P5,
    }[framing]
    p5 = _reshape(p5, framing, proportions)
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
    background: str = "",
    framing: Framing = "mid_thigh",
    subject: Subject = "woman",
    proportions: Proportions = "typical",
    trigger: str = "",
    size: Size = "large",
    reference: bool = False,
    layout: Layout = "moss",
) -> str:
    """The prompt for one grid sheet of the character described.

    The MOSS layout prompt by default; with *layout* ``prose``, the same
    sheet as one plain sentence, for a model that takes no layout (Krea 2,
    Qwen-Image, FLUX.2 Klein, or an edit model drawing from a reference
    picture). With *reference*, every photo
    names the person in the reference picture ahead of the description: the
    picture sets the face, the words the build and the clothes.

    Every panel's caption carries the whole character, the build included: a
    different build draws a different face, so the build is part of who it is.
    The build is usually in the description; *body* is said on its own as
    well, and on the torso of the body photos ("body shape"), only when given.
    Without it nothing stands in for it: an "average build" default would
    contradict a build the description states.
    *background* is what every photo stands in front of; without it, a plain
    light-grey studio, the backdrop the probes drew.
    Trousers are always named (left out, MOSS improvises bare legs); shoes only
    at full body, where they belong, since naming them pulls a cropped framing
    to full length. *trigger* is a character LoRA's trigger word, put first.
    *proportions* other than ``typical`` (#1079) is said with the character
    and reshapes the body photos' boxes to that figure; the words alone lose
    to boxes laid out for a seven-heads-tall adult.
    The boxes are laid out on the large canvas and scaled to *size*'s.
    """
    scale = SIZES[size][0] / SHEET_WIDTH
    p, cap_p = _PRONOUN[subject]
    body = _phrase(body)
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
    who = f"{lead}{described or f'an adult {subject}'}"
    if body:
        who = f"{who}, {body}"
    if _PROPORTION_WORDS[proportions]:
        who = f"{who}, {_PROPORTION_WORDS[proportions]}"
    character = f"{who}, wearing {full_outfit}"
    # A passport photo shows only the top; at full body the trousers and shoes
    # would pull those panels to full length too.
    passport_character = (
        f"{who}, wearing {top}" if framing == "full_body" else character
    )

    def fill(text: str) -> str:
        if not body:
            text = text.replace(" {P} body shape: {body}.", "")
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
    body_items = _body_items(framing, proportions)
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
    backdrop = _phrase(background)
    setting = (
        # Said as a label, so "a sunlit park", "in a kitchen" and "white" all read.
        f"with the background: {backdrop}"
        if backdrop
        else "on a plain light-grey studio background, soft even studio lighting"
    )
    summary = (
        f"Character reference sheet of one and the same {subject} {setting}, "
        "photorealistic photographs. On the left half, four passport-style "
        "head-and-shoulders "
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
    empty latent sized from the picture, from the picture's own latent, or
    from a switch between the two (Qwen-Image 2.1). A sheet is drawn from a
    reference picture at the sheet's size, so the copy's sampler starts from
    an empty latent with a literal ``width`` and ``height`` (the large
    sheet's; a run sets its own), anything else sized from the picture takes
    the same size, and the picture reaches the reference input only. The copy
    saves with ``SaveImage``. *graph* is not modified.

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
    switched = False
    if "Switch" in source_class:
        # A switch between the picture's own latent and an empty canvas
        # (Qwen-Image 2.1's template): the copy takes the canvas.
        canvas = next(
            (
                value
                for value in (source.get("inputs") or {}).values()
                if is_link(value)
                and re.search(
                    r"Empty.*Latent",
                    (nodes.get(str(value[0])) or {}).get("class_type", ""),
                )
            ),
            None,
        )
        if canvas is not None:
            inputs["latent_image"] = list(canvas)
            source = nodes[str(canvas[0])]
            source_class = source["class_type"]
            switched = True
    width, height = SIZES["large"]
    if re.search(r"Empty.*Latent", source_class):
        size = source.setdefault("inputs", {})
        linked = {
            name: size.get(name)
            for name in ("width", "height")
            if is_link(size.get(name))
        }
        if not linked and not switched:
            raise SheetConversionRefused(
                "This workflow already draws at a size of its own, so it can "
                "draw a sheet as it is."
            )
        size["width"], size["height"] = width, height
        # Whatever else was sized from the picture (FLUX.2's scheduler, which
        # shifts its sigmas by the canvas's area) follows the canvas.
        for node in nodes.values():
            node_inputs = node.get("inputs") or {}
            for name, link in linked.items():
                if node_inputs.get(name) == link:
                    node_inputs[name] = size[name]
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
    _save_with_save_image(nodes)
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


def _save_with_save_image(nodes: dict) -> None:
    """Swap each ``SaveImageAdvanced`` in *nodes* for a ``SaveImage``.

    PixlStash collects a run's pictures from the savers it knows, and the
    advanced one is not among them (it can write EXR or AVIF too). A sheet is
    a PNG either way. The advanced saver hands its picture on; whatever read
    that reads the decoded picture instead.
    """
    for node_id, node in nodes.items():
        if node["class_type"] != "SaveImageAdvanced":
            continue
        old = node.get("inputs") or {}
        images = old.get("images")
        node["class_type"] = "SaveImage"
        node["inputs"] = {
            "images": images,
            "filename_prefix": old.get("filename_prefix", "ComfyUI"),
        }
        for other in nodes.values():
            other_inputs = other.get("inputs") or {}
            for name, value in other_inputs.items():
                if is_link(value) and str(value[0]) == str(node_id):
                    other_inputs[name] = images


def _find_cut(
    gray: np.ndarray,
    nominal: int,
    window: tuple[int, int],
    across: tuple[int, int],
    vertical: bool,
    fences: list[tuple[float, float, float]],
    scale: float,
) -> tuple[int, int, bool]:
    """Where to cut near *nominal*: ``(end, start, sure)``.

    The panel before the cut ends at *end* and the one after starts at
    *start*. Lines are searched over *window* and read over *across* (the span
    the cut runs). A line within a fence ``(along, across, reach)`` (a face's
    centre on the cut's axis, across it, and how near a cut may come) whose
    centre lies in *across* is never a candidate. *scale* is the sheet's
    size against 2560×1536 across the cut: its width for a column cut, its
    height for a row cut.

    The cut lands, in order of preference, in the uniform band nearest
    *nominal* that has a step at its edges (a gutter or a drawn line, trimmed
    to its edges plus a soft seam's width); at *nominal* moved into the nearest wider
    or step-less band (background the photos share, nothing trimmed); or, with
    neither, on the most uniform line, which is not ``sure``.
    """
    plane = gray if vertical else gray.T
    size = plane.shape[1]
    lo, hi = max(1, window[0]), min(size - 1, window[1])
    if hi <= lo:
        return nominal, nominal, False
    strip = plane[across[0] : across[1], lo - 1 : hi + 1]
    median = np.median(strip, axis=0)
    share = (np.abs(strip - median) <= UNIFORM_LEVELS).mean(axis=0)
    lines = np.arange(lo, hi)
    # How far each line keeps from the nearest fence; inside one, negative.
    clearance = np.full(hi - lo, np.inf)
    for along, at, reach in fences:
        if across[0] <= at < across[1]:
            clearance = np.minimum(clearance, np.abs(lines - along) - reach)
    fenced = clearance < 0
    open_ = share[1:-1] >= UNIFORM_SHARE
    open_ &= ~fenced
    # A band holds one level: measured from its first line, since a gutter's
    # soft edge climbs to its own level in steps each too small to split on.
    runs: list[tuple[int, int]] = []
    for index in np.flatnonzero(open_):
        line = lo + int(index)
        if (
            runs
            and runs[-1][1] == line
            and abs(median[index + 1] - median[runs[-1][0] - lo + 1]) <= UNIFORM_LEVELS
        ):
            runs[-1] = (runs[-1][0], line + 1)
        else:
            runs.append((line, line + 1))
    if not runs:
        # ponytail: no clear line anywhere in the window (two photos drawn
        # touching, an arm across the gutter); the least busy line is a guess.
        if fenced.all():
            # Faces across the whole window: the line furthest from them.
            line = lo + int(np.argmax(clearance))
        else:
            line = lo + int(np.argmax(np.where(fenced, -1.0, share[1:-1])))
        return line, line, False
    gutter = max(1, round(MAX_GUTTER * scale))
    bleed = max(1, round(2 * scale))

    def level(line: int) -> float:
        return float(np.median(plane[across[0] : across[1], line]))

    def stepped(start: int, end: int) -> bool:
        # Lighter or darker than both sides, read a soft seam's width out past
        # its blurred edge: a band that only climbs from one level to the
        # next is the seam between a photo and its frame, not a gutter.
        inside = level((start + end - 1) // 2)
        steps = [
            level(edge) - inside
            for edge in (start - 1 - bleed, end + bleed)
            if 0 <= edge < size
        ]
        return all(abs(step) > UNIFORM_LEVELS for step in steps) and (
            all(step > 0 for step in steps) or all(step < 0 for step in steps)
        )

    def distance(run: tuple[int, int]) -> int:
        start, end = run
        return (
            0
            if start <= nominal < end
            else min(abs(start - nominal), abs(end - 1 - nominal))
        )

    dividers = [r for r in runs if r[1] - r[0] <= gutter and stepped(*r)]
    if dividers:
        start, end = min(dividers, key=distance)
        return start - bleed, end + bleed, True
    start, end = min(runs, key=distance)
    margin = gutter // 4
    line = min(max(nominal, start + margin), end - 1 - margin)
    if line < start:
        line = (start + end) // 2
    # Background pressed against the window's edge, away from the grid line,
    # most likely goes on past it: the sheet is drawn at another scale.
    sure = start <= nominal < end or lo + margin <= line < hi - margin
    return line, line, sure


def sheet_cells(
    image: Image.Image, faces: list[list[int]] | None = None
) -> tuple[list[Box], bool]:
    """The six panels' boxes on *image*, in order, and whether the cut is clean.

    Each cut starts from the grid's own line (passport cells 600×768, body
    cells 680×1536 at 2560×1536, scaled to the picture) and searches halfway to
    the next line on either side, so no model's drift is a constant here: see
    :func:`_find_cut` for where in that window it lands. The passport row is
    cut once per passport column, each over its own span. Whatever is left of
    the white frame round each photo is then trimmed.

    *faces* are the sheet's stored face boxes. A cut never runs through a
    face's centre, and the cut is clean only when every cut found a clear line
    and no panel holds two faces' centres. A cut that is not clean still cuts:
    it is for the person to judge, from the cells drawn on the sheet.
    """
    width, height = image.size
    sx, sy = width / SHEET_WIDTH, height / SHEET_HEIGHT
    gray = np.asarray(image.convert("L"), dtype=np.int16)
    centres = [
        (
            (b[0] + b[2]) / 2,
            (b[1] + b[3]) / 2,
            FACE_FENCE * min(b[2] - b[0], b[3] - b[1]),
        )
        for b in faces or ()
        if b and len(b) == 4
    ]
    across_x = [(x, y, reach) for x, y, reach in centres]
    across_y = [(y, x, reach) for x, y, reach in centres]
    grid = [round(x * sx) for x in (0, 600, 1200, 1880)] + [width]

    def column_cut(i: int) -> tuple[int, int, bool]:
        window = ((grid[i - 1] + grid[i]) // 2, (grid[i] + grid[i + 1]) // 2)
        return _find_cut(gray, grid[i], window, (0, height), True, across_x, sx)

    column, half, bodies = column_cut(1), column_cut(2), column_cut(3)
    row = round(768 * sy)
    window = (row // 2, (row + height) // 2)
    rows = [
        _find_cut(gray, row, window, span, False, across_y, sy)
        for span in ((0, column[0]), (column[1], half[0]))
    ]
    cells = [
        (0, 0, column[0], rows[0][0]),
        (column[1], 0, half[0], rows[1][0]),
        (0, rows[0][1], column[0], height),
        (column[1], rows[1][1], half[0], height),
        (half[1], 0, bodies[0], height),
        (bodies[1], 0, width, height),
    ]
    limit, bleed = round(64 * sx), max(1, round(2 * sx))
    cells = [_inside_frame(gray, cell, limit, bleed) for cell in cells]
    clean = all(cut[2] for cut in (column, half, bodies, *rows)) and all(
        sum(left <= x < right and top <= y < bottom for x, y, _ in centres) <= 1
        for left, top, right, bottom in cells
    )
    return cells, clean


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
    inside = (
        left + trimmed(max(corner[1, 1], corner[-1, 1])),
        top + trimmed(max(corner[1, 1], corner[1, -1])),
        right - trimmed(max(corner[1, -1], corner[-1, -1])),
        bottom - trimmed(max(corner[-1, 1], corner[-1, -1])),
    )
    if inside[0] < inside[2] and inside[1] < inside[3]:
        return inside
    # A cell narrower than two frames' worth of white (two dividers close
    # together on a sheet that broke its layout) would trim past itself, and
    # cropping an inverted box raises. The cell stays as cut instead.
    logger.warning(
        "Sheet cell %s is all frame (trimmed to %s); keeping it untrimmed.",
        cell,
        inside,
    )
    return cell


def split_sheet(
    image: Image.Image, faces: list[list[int]] | None = None
) -> list[Image.Image]:
    """The sheet's six panels as pictures: four passport photos, then two bodies."""
    return [image.crop(box) for box in sheet_cells(image, faces)[0]]


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


def panel_of(bbox: list[int], width: int, height: int) -> int:
    """Which of the six panels (in :func:`sheet_cells` order) a face box sits in."""
    # ponytail: the grid's own lines, not the dividers as drawn (up to ~190 px
    # off); a face centred in its photo is further than that from any cut.
    x = (bbox[0] + bbox[2]) / 2 / width * SHEET_WIDTH
    y = (bbox[1] + bbox[3]) / 2 / height * SHEET_HEIGHT
    if x >= 1880:
        return 5
    if x >= 1200:
        return 4
    return (2 if y >= 768 else 0) + (1 if x >= 600 else 0)


def _area(box: list[int]) -> int:
    return (box[2] - box[0]) * (box[3] - box[1])


def panel_scores(
    faces: list[tuple[np.ndarray, list[int] | None]],
    width: int | None,
    height: int | None,
    references: list[np.ndarray],
) -> list[dict]:
    """Each panel's face scored on its own, in panel order.

    *faces* are a sheet's ``(embedding, box)`` pairs, *width* and *height* its
    picture's. A panel's face is its largest; a panel with none is left out.
    Its ``agreement`` is its mean similarity to the other panels' faces, its
    ``closeness`` to *references*, and its ``box`` the face's box as fractions
    of the sheet, for marking it on the picture.
    """
    if not width or not height:
        return []
    largest: dict[int, tuple[np.ndarray, list[int]]] = {}
    for vector, box in faces:
        if not box or len(box) != 4:
            continue
        panel = panel_of(box, width, height)
        if panel not in largest or _area(box) > _area(largest[panel][1]):
            largest[panel] = (vector, box)
    out = []
    for panel in sorted(largest):
        vector, box = largest[panel]
        others = [other for p, (other, _) in largest.items() if p != panel]
        agrees = closeness([vector], others)
        near = closeness([vector], references)
        out.append(
            {
                "panel": panel,
                "box": [
                    min(1.0, max(0.0, edge / side))
                    for edge, side in zip(box, (width, height, width, height))
                ],
                "agreement": agrees,
                "agrees": None if agrees is None else agrees >= SOME_AGREEMENT,
                "closeness": near,
                "close": None if near is None else near >= CLOSE_TO_REFERENCES,
            }
        )
    return out


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


def _vectors(rows: list) -> list[np.ndarray]:
    return [np.frombuffer(features, dtype=np.float32) for features in rows if features]


def _face_boxes(bboxes: list[str | None]) -> list[list[int]]:
    """The stored face boxes among *bboxes* (``Face.bbox_`` JSON); the row
    face extraction leaves on a picture with no face has none."""
    boxes = [json.loads(bbox) for bbox in bboxes if bbox]
    return [box for box in boxes if box and len(box) == 4]


def sheet_faces_in_session(
    session: Session,
    character_id: int,
    picture_ids: list[int],
    reference_picture_ids: list[int] | None = None,
) -> tuple[dict[int, list], list, dict[int, tuple]] | None:
    """Each sheet's faces, the reference embeddings, each sheet's format and size.

    ``None`` when the character does not exist. A sheet's list holds a
    ``(features, bbox)`` row per face, *features* ``None`` on the row face
    extraction leaves on a picture with no face, so an empty list means its
    faces are not read yet. The references are the character's
    reference faces, or, given *reference_picture_ids*, the faces of those
    pictures: what the sheets were drawn from. The sheets' own faces are never
    their own references. Each sheet's ``(format, width, height, file_path)``:
    the format is what the picture's file is served as.
    """
    if session.get(Character, character_id) is None:
        return None
    faces: dict[int, list] = {pid: [] for pid in picture_ids}
    for picture_id, features, bbox in session.exec(
        select(Face.picture_id, Face.features, Face.bbox_).where(
            Face.picture_id.in_(picture_ids)
        )
    ).all():
        faces[picture_id].append((features, bbox))
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
    pictures = {
        picture_id: (format_, width, height, file_path)
        for picture_id, format_, width, height, file_path in session.exec(
            select(
                Picture.id,
                Picture.format,
                Picture.width,
                Picture.height,
                Picture.file_path,
            ).where(Picture.id.in_(picture_ids))
        ).all()
    }
    return faces, references, pictures


@lru_cache(maxsize=64)
def _cut_of_file(
    path: str, stamp: int, faces: tuple[tuple[int, ...], ...]
) -> tuple[tuple[tuple[float, ...], ...], bool]:
    """:func:`sheet_cells` of the file at *path*, the boxes as fractions.

    The step ranks its sheets every few seconds while it draws, and a sheet's
    file never changes: *stamp* (its mtime) keys the cache so one that did is
    cut again.
    """
    with Image.open(path) as image:
        image = image.convert("RGB")
    cells, clean = sheet_cells(image, [list(box) for box in faces])
    width, height = image.size
    fractions = tuple(
        (left / width, top / height, right / width, bottom / height)
        for left, top, right, bottom in cells
    )
    return fractions, clean


def _sheet_cut(
    image_root: str, picture_id: int, file_path: str, faces: list[list[int]]
) -> tuple[tuple[tuple[float, ...], ...], bool] | None:
    """The sheet's cells as fractions and whether it cuts cleanly; ``None``
    when its file cannot be read, which leaves the cut unshown, not wrong."""
    path = ImageUtils.resolve_picture_path(image_root, file_path)
    try:
        if not path:
            raise FileNotFoundError(file_path)
        stamp = os.stat(path).st_mtime_ns
        return _cut_of_file(path, stamp, tuple(tuple(box) for box in faces))
    except (OSError, Image.DecompressionBombError) as exc:
        logger.warning(
            "Couldn't read character sheet %s at %s to show its cut: %s",
            picture_id,
            path,
            exc,
        )
        return None


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
    faces, reference_rows, pictures = found
    references = _vectors(reference_rows)
    vectors_of = {pid: _vectors([row[0] for row in faces[pid]]) for pid in ids}
    out = []
    for picture_id in ids:
        rows = faces[picture_id]
        vectors = vectors_of[picture_id]
        score = agreement(vectors)
        near = closeness(vectors, references)
        format_, width, height, file_path = pictures.get(picture_id, (None,) * 4)
        boxed = [
            (
                np.frombuffer(features, dtype=np.float32),
                json.loads(bbox) if bbox else None,
            )
            for features, bbox in rows
            if features
        ]
        # Cut only once the faces are read, so the polling while a sheet is
        # drawn reads no files, and with them, as using it will cut.
        cut = (
            _sheet_cut(
                vault.image_root,
                picture_id,
                file_path,
                _face_boxes([bbox for _, bbox in rows]),
            )
            if rows and file_path
            else None
        )
        out.append(
            {
                "picture_id": picture_id,
                "format": (format_ or "").lower() or None,
                "faces_read": bool(rows),
                "faces": len(vectors),
                "agreement": score,
                "agreement_words": agreement_words(score),
                "closeness": near,
                "close": None if near is None else near >= CLOSE_TO_REFERENCES,
                "broken": len(vectors) > MAX_FACES,
                "panels": panel_scores(boxed, width, height, references),
                "cuts_cleanly": None if cut is None else cut[1],
                "cells": [] if cut is None else [list(cell) for cell in cut[0]],
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


def sheet_face_boxes_in_session(
    session: Session, picture_ids: list[int]
) -> dict[int, list[list[int]]]:
    """Each picture's stored face boxes, for cutting it as it was ranked."""
    boxes: dict[int, list] = {pid: [] for pid in picture_ids}
    for picture_id, bbox in session.exec(
        select(Face.picture_id, Face.bbox_).where(Face.picture_id.in_(picture_ids))
    ).all():
        boxes[picture_id].extend(_face_boxes([bbox]))
    return boxes


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


def _cut(server, sheet: Picture, faces: list[list[int]]) -> list[tuple[bytes, str]]:
    """The sheet's six panels as PNG bytes, ready to import, cut round *faces*."""
    path = ImageUtils.resolve_picture_path(server.vault.image_root, sheet.file_path)
    if not path or not os.path.isfile(path):
        raise SheetUnreadable(
            f"Picture {sheet.id}'s file is missing, so it can't be cut."
        )
    try:
        with Image.open(path) as image:
            panels = split_sheet(image.convert("RGB"), faces)
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


def use_sheets(server, character_id: int, picture_ids: list[int]) -> list[int]:
    """Cut each sheet into its six panels and file them on the character.

    Passport photos first, sheet by sheet, each cut round its stored faces
    as :func:`rank_sheets` cuts it (faces read since the last rank count
    too); one that does not cut cleanly is cut all the same. Every sheet is
    read before anything is imported, so an unreadable one imports nothing.
    Returns the panels' picture ids. Raises :class:`SheetNotFound` and
    :class:`SheetUnreadable`. The sheets themselves stay in the Scrapheap.
    """
    ids = list(dict.fromkeys(picture_ids))
    sheets = server.vault.db.run_immediate_read_task(
        sheets_in_session, character_id, ids
    )
    if sheets is None:
        raise SheetNotFound(f"No character {character_id} or no picture among {ids}.")
    faces = server.vault.db.run_immediate_read_task(sheet_face_boxes_in_session, ids)
    cuts = [(sheet, _cut(server, sheet, faces.get(sheet.id, []))) for sheet in sheets]
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
    return panel_ids
