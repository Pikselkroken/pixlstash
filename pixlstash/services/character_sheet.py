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
  With ``layout="prose"`` it is the same sheet in one sentence; with
  ``layout="regions"`` the layout for ComfyUI-PixlStash's Layout Text Encode,
  which draws it on any model as regional conditioning.
* :func:`convert_to_sheet` rewrites an edit workflow's graph to draw at the
  sheet's size, and a text-to-image workflow's to encode its prompt with
  Layout Text Encode (``POST /workflows/{workflow_id}/sheet-copy``).
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
from scipy import ndimage
from sqlmodel import Session, select

from pixlstash.db_models import Character, Face, Picture
from pixlstash.image_plugins.service import import_derived_outputs
from pixlstash.pixl_logging import get_logger
from pixlstash.scoring import select_reference_faces_for_character
from pixlstash.services.face_clusters import reference_cluster_faces
from pixlstash.services.move_reconciliation_service import add_person
from pixlstash.services.workflow_hash import (
    WorkflowGraphError,
    is_link,
    reduce_api_graph,
)
from pixlstash.services.workflow_identity import (
    SHEET_LAYOUT_ENCODER,
    reference_input_count,
)
from pixlstash.services.workflow_io import detect_workflow_io, is_picture_loader
from pixlstash.utils.image_processing.image_utils import ImageUtils

logger = get_logger(__name__)

SHEET_WIDTH = 2560
SHEET_HEIGHT = 1536
PANEL_COUNT = 6
# More faces than panels means a panel holds more than one person: the sheet
# broke its layout and is set aside.
MAX_FACES = PANEL_COUNT

Framing = Literal["waist_up", "mid_thigh", "knees_up", "full_body"]
Subject = Literal["person", "woman", "man"]
# The figure's proportions: a typical adult's, a little person's (#1079) or a
# cartoon's. The probed boxes are a typical adult's, about seven heads tall.
Proportions = Literal["typical", "little_person", "cartoon"]
Size = Literal["small", "medium", "large"]
# How the prompt is written: MOSS's layout JSON, the sheet in one sentence, or
# the layout JSON for ComfyUI-PixlStash's Layout Text Encode (the canvas size
# added, every caption standing alone).
Layout = Literal["moss", "prose", "regions"]
# The sheet's canvas at each size, all 5:3 so the layout only scales, and every
# side a multiple of 16 as MOSS needs: ~1, ~2 and ~4 megapixels, MOSS's own
# range (it is trained at 1-2 MP and stretches to about 4), and ~17 for
# MiniMax H3, which wants a large canvas (MiniMax's "4K" is 16.6 MP or more;
# every side a multiple of its 32 too). The step offers each model its own.
SIZES = {
    "small": (1280, 768),
    "medium": (1840, 1104),
    "large": (2560, 1536),
    # MiniMax H3 Image's sizes: its reference sheets draw waxy faces at
    # large and keep a face close only from 4K up (the 2026-11-22 probe).
    "4k": (3840, 2304),
    "5k": (5280, 3168),
    # The nearest 5:3 to 6K on the 16-pixel grid; MiniMax H3 Image only,
    # about 40 s a sheet on a 32 GB card and no closer a face than 5K.
    "6k": (6240, 3744),
}
# MiniMax H3's own size joins the three every model has (a nested ``Literal``
# flattens to all four).
Size = Literal[Size, "4k", "5k", "6k"]

# ponytail: words from scores; thresholds read off the 2026-11 probes (one
# sheet 0.57-0.70, across sheets 0.37). P6 validation tunes them.
STRONG_AGREEMENT = 0.55
SOME_AGREEMENT = 0.45
CLOSE_TO_REFERENCES = 0.45
# Every face of a reference cluster counts against a sheet: the route caps a
# cluster at this many pictures.
CLUSTER_FACES = 5000
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
# A photo cut by hand (the step's edited boxes) is at least this many pixels a
# side on the sheet: a sliver is not a photo. The step keeps its boxes at 5 %
# of the sheet, which is over this on every size the step draws.
MIN_EDITED_PX = 32
# A seam, where two photos drawn edge to edge meet with no gutter (MiniMax H3,
# FLUX.2 Klein), is a line whose step (the grey levels between the lines either
# side of it) is at least SEAM_STEP and the largest within SEAM_REACH lines, on
# SEAM_SHARE of the cut's span. Inside a photo the largest step wanders from
# row to row, so a line holds it on about 0.3 of them by chance.
# ponytail: read off 52 probe sheets on 2026-11-22 (MiniMax H3, FLUX.2 Klein);
# re-read them when a model draws a seam this misses.
SEAM_STEP = 3
SEAM_REACH = 24
SEAM_SHARE = 0.4
SEAM_PROMINENCE = 2
# Texture stops at a seam when the TEXTURE_LINES lines on one side change by
# TEXTURE_STEP grey levels a line on average and those on the other by
# TEXTURE_RATIO times less.
TEXTURE_LINES = 4
TEXTURE_STEP = 4
TEXTURE_RATIO = 3
# Colour gutters: a prose sheet can ask for its photos on a magenta sheet. The
# colour is learnt from the sheet's outermost GUTTER_RING lines, where most of
# the pixels must be saturated and one hue in the magenta family (PIL hue,
# 0-255: plum ~205 to hot pink ~245), since models draw anything in that range
# and let it drift across one sheet. A pixel is gutter within GUTTER_HUE of it.
GUTTER_RING = 8
GUTTER_HUES = (195, 250)
GUTTER_HUE = 18
GUTTER_SATURATION = 90
GUTTER_VALUE = 60
# An edge line of a cut panel is gutter while more than this share of it is
# gutter running in from its ends (an askew or rounded photo edge).
EDGE_GUTTER = 0.01
# The four passport photos are drawn one size, and the two body photos
# another: each within this share of the others of its kind, or the cut is
# not the sheet's layout. Models do not draw them quite alike (passports
# measured up to 21 % apart on 59 probe sheets; body photos up to 27 % on
# FLUX.2 Klein, and 40 % on an edit model's sheet drawn from a reference),
# so this catches a cut gone astray, not a model's drift.
SAME_SIZE = {"passport": 0.3, "body": 0.5}

# (left, top, right, bottom) on the sheet; a face is its stored box.
Box = tuple[int, int, int, int]

_PRONOUN = {
    "person": ("their", "Their"),
    "woman": ("her", "Her"),
    "man": ("his", "His"),
}

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
# The prose sheet as ordinary unedited photographs rather than renders: a model
# agency's "digitals". MiniMax H3 Image draws a glossy beauty-filter look
# without it, and exaggerates every skin detail named, so the skin is said
# once and gently. ponytail: read off the 2026-11-21 probes (MiniMax H3 Image,
# Qwen-Image); re-probe before adding to it.
PHOTO_LOOK = (
    "The photos look like a model agency's digitals: plain unedited camera "
    "photos, natural colour, true-to-life skin, little or no "
    "makeup, soft focus falloff, nothing retouched or sharpened."
)


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
    subject: Subject = "person",
    proportions: Proportions = "typical",
    trigger: str = "",
    size: Size = "large",
    reference: bool = False,
    layout: Layout = "moss",
    photo_look: bool = True,
    look_added_by_run: bool = False,
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
    *photo_look* adds :data:`PHOTO_LOOK` to a prose prompt and drops
    "photorealistic", render vocabulary. A prose prompt also names the eye
    colour among what every photo shares. The MOSS and regions layouts are
    the probed ones and stay as they are. *look_added_by_run* says the run's
    prompt assist adds the look (backend §25): a prose prompt drops
    "photorealistic" and appends nothing, whatever *photo_look* says.
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
    prose = layout == "prose"
    look = prose and photo_look and not look_added_by_run
    unrendered = prose and (photo_look or look_added_by_run)
    summary = (
        f"Character reference sheet of one and the same {subject} {setting}, "
        f"{'' if unrendered else 'photorealistic '}photographs. On the left half, "
        "four passport-style head-and-shoulders "
        f"photos in two rows of two: {views}. On the right half, two tall "
        f"{_FRAMING_SUMMARY[framing]}: (5) standing, front view at eye level, "
        f"(6) standing, three-quarter view. The {subject} is {character}. "
        f"Identical face, {'eye colour, ' if prose else ''}hair, body shape and "
        "clothing in every photo."
    )
    if prose:
        return f"{summary} {PHOTO_LOOK}" if look else summary
    document = {
        "summary": {"caption": summary},
        "items": items,
        "bbox_coord_space": "abs_centralized",
        "bbox_format": "yxyx",
    }
    if layout == "regions":
        # Each caption is encoded over its box alone, so it has to say who it
        # is of: "Panel 3" means nothing there, and a head with no character
        # beside it is anybody's.
        for item in items:
            _panel, _, text = item["caption"].partition(": ")
            item["caption"] = text if item["label"] == "character" else f"{who}. {text}"
        # Centred pixel boxes need the canvas they were laid out on.
        document["canvas"] = list(SIZES[size])
    return json.dumps(document)


# Outfits, locations and props are drawn the way characters are: described
# once, every angle on one 5:3 sheet so the angles agree, and the best sheet
# kept and cut. Four panels each: an outfit's four tall ones side by side, a
# location's and a prop's two by two, numbered row by row.
ItemKind = Literal["outfit", "location", "prop"]
Kind = Literal["character", ItemKind]
ITEM_GRIDS: dict[str, tuple[int, int]] = {
    "outfit": (1, 4),
    "location": (2, 2),
    "prop": (2, 2),
}
ITEM_ANGLES: dict[str, tuple[str, ...]] = {
    "outfit": ("Front", "¾", "Side", "Back"),
    "location": ("Wide", "Reverse", "Left", "Right"),
    "prop": ("Front", "¾", "Side", "Back"),
}
_ITEM_VIEWS = {
    "outfit": (
        "front view",
        "three-quarter view turned to the left",
        "side view facing right",
        "back view",
    ),
    "location": (
        "top left, a wide establishing view",
        "top right, the reverse view, looking back the other way",
        "bottom left, looking to the left",
        "bottom right, looking to the right",
    ),
    "prop": (
        "top left, front view",
        "top right, three-quarter view",
        "bottom left, side view",
        "bottom right, back view",
    ),
}
_ITEM_NOUN = {"outfit": "outfit", "location": "place", "prop": "object"}
# The prose item sheet as plain product or location photographs. ponytail:
# written beside PHOTO_LOOK, not probed; re-probe on MiniMax H3 Image.
ITEM_PHOTO_LOOK = (
    "The photos look like plain unedited camera photos: natural colour, "
    "true-to-life materials, nothing retouched or sharpened."
)


def item_sheet_prompt(
    *,
    kind: ItemKind,
    description: str,
    top: str = "",
    trousers: str = "",
    shoes: str = "",
    background: str = "",
    contents: str = "",
    ground: str = "",
    surroundings: str = "",
    material: str = "",
    scale: str = "",
    daylight: bool = True,
    reference: bool = False,
    photo_look: bool = True,
    look_added_by_run: bool = False,
) -> str:
    """The prose prompt for one sheet of an outfit, a location or a prop.

    Prose only: the layouts MOSS-Image reads are probed for a character's six
    panels. Every view is named by its place on the sheet, and the whole item
    is said once for all four, so every panel draws the same one. An outfit is
    worn, chin to feet, so no face competes with it; a location is always
    empty, in plain daylight unless *daylight* is off (each scene sets its own
    time and weather); a prop stands alone. *reference* says the item is the
    one in the reference picture. *photo_look* and *look_added_by_run* work as
    in :func:`sheet_prompt`, with :data:`ITEM_PHOTO_LOOK` as the look.
    """
    noun = _ITEM_NOUN[kind]
    described = _phrase(description)
    if reference:
        same = f"the same {noun} as in the reference picture"
        if kind == "location":
            same = f"{same}, without any people in it"
        described = f"{same}, {described}" if described else same
    described = described or f"an everyday {noun}"
    views = ", ".join(f"({n}) {view}" for n, view in enumerate(_ITEM_VIEWS[kind], 1))
    unrendered = photo_look or look_added_by_run
    photos = "photographs" if unrendered else "photorealistic photographs"
    backdrop = _phrase(background)
    setting = (
        f"with the background: {backdrop}"
        if backdrop
        else "on a plain light-grey studio background, soft even studio lighting"
    )
    if kind == "outfit":
        parts = [
            _phrase(top),
            _phrase(trousers),
            _phrase(shoes),
        ]
        pieces = "; ".join(part for part in parts if part)
        text = (
            f"Outfit reference sheet {setting}, {photos}: one and the same "
            "outfit worn by the same adult, in four tall photos of equal size "
            "side by side, each spanning the full height of the sheet, "
            "separated by thin white borders. Every photo is cropped at the "
            "chin, so no face shows, and runs down to the feet: "
            f"{views}. The outfit is {described}"
            f"{f': {pieces}' if pieces else ''}. Identical garments, colours, "
            "fabric, fit and details in every photo."
        )
    elif kind == "location":
        details = [
            f"In it: {_phrase(contents)}." if _phrase(contents) else "",
            f"The ground: {_phrase(ground)}." if _phrase(ground) else "",
            f"Around it: {_phrase(surroundings)}." if _phrase(surroundings) else "",
            "Plain daylight at midday, clear weather." if daylight else "",
        ]
        text = (
            f"Location reference sheet, {photos} of one and the same place, "
            f"{described}, in four photos of equal size, two by two, "
            f"separated by thin white borders: {views}. "
            f"{' '.join(d for d in details if d)} The place is empty: no "
            "people and no animals. Identical place, layout, materials and "
            "details in every photo."
        )
    else:
        details = [
            f"Material: {_phrase(material)}." if _phrase(material) else "",
            f"Size: {_phrase(scale)}." if _phrase(scale) else "",
        ]
        text = (
            f"Prop reference sheet {setting}, {photos} of one and the same "
            f"object, {described}, on its own in four photos of equal size, "
            f"two by two, separated by thin white borders: {views}. "
            f"{' '.join(d for d in details if d)} Nothing else is in the "
            "photos: no people and no hands. Identical shape, colours, "
            "material and details in every photo."
        )
    text = " ".join(text.split())
    look = photo_look and not look_added_by_run
    return f"{text} {ITEM_PHOTO_LOOK}" if look else text


class SheetConversionRefused(ValueError):
    """The graph is not one :func:`convert_to_sheet` can rewrite; ``reason`` says why."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _text_to_sheet(out: dict, nodes: dict, inputs: dict) -> dict:
    """*out*, a text-to-image graph, with its prompt read as a sheet's layout.

    MOSS-Image reads a sheet's layout prompt and puts each photo in its box;
    any other model reads the JSON as words and draws the grid as it likes,
    which the cut into panels cannot follow. The copy's prompt encoder becomes
    ComfyUI-PixlStash's Layout Text Encode, which encodes the summary over the
    whole canvas and each caption over its own box (regional conditioning),
    so the photos should land where the cut expects them whatever the model
    (no probe has measured it yet). Its ``clip``
    and ``text`` are the encoder's own, so the run's prompt goes where it
    went. The canvas gets the large sheet's literal size, which a run then
    sets: the layout is 5:3, and on a canvas of another shape the regions
    would stretch. *inputs* are the one sampler's.

    Raises:
        SheetConversionRefused: The sampler does not start from an empty
            latent, the prompt is not read from one ``CLIPTextEncode`` of its
            own (the negative's too would be read as regions), or the graph
            already encodes it as a layout.
    """
    source = nodes.get(str(inputs["latent_image"][0])) or {}
    source_class = source.get("class_type", "")
    if not re.search(r"Empty.*Latent", source_class):
        raise SheetConversionRefused(
            f"Its sampler starts from {source_class or 'nothing'}, not an "
            "empty canvas, so it cannot draw a sheet."
        )
    size = source.setdefault("inputs", {})
    size["width"], size["height"] = SIZES["large"]
    try:
        detected = detect_workflow_io(out)
        positive, negative = detected.positive_prompt_nodes, detected.negative_prompts
    except WorkflowGraphError as exc:
        logger.warning("A text sheet copy's prompts could not be read: %s", exc)
        positive, negative = (), ()
    if not positive:
        raise SheetConversionRefused(
            "PixlStash does not find one prompt in this workflow. Only a "
            "workflow with one prompt can be made into a sheet workflow."
        )
    # Every encoder of the one prompt takes the layout, so none of them draws
    # a sheet the others do not.
    for node_id in positive:
        if node_id in negative:
            raise SheetConversionRefused(
                "Its prompt and negative prompt are one node, so the negative "
                "would be drawn as the sheet's layout too."
            )
        encoder = out[node_id]
        if encoder["class_type"] == SHEET_LAYOUT_ENCODER:
            raise SheetConversionRefused("This workflow already draws sheets.")
        # ponytail: CLIPTextEncode only, the encoder of nearly every text-to-image
        # template. One with fields of its own (SDXL's text_g / text_l, Flux 1's
        # clip_l / t5xxl) needs a layout encoder that takes them.
        if encoder["class_type"] != "CLIPTextEncode":
            raise SheetConversionRefused(
                f"Its prompt is encoded by {encoder['class_type']}. Only a "
                "workflow whose prompt goes through CLIP Text Encode can be made "
                "into a sheet workflow."
            )
    for node_id in positive:
        encoder = out[node_id]
        encoder["class_type"] = SHEET_LAYOUT_ENCODER
        encoder.setdefault("inputs", {})["region_strength"] = 1.0
    # As the edit path's copy: a sheet PixlStash cannot collect is lost.
    _save_with_save_image(nodes)
    return out


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
        SheetConversionRefused: There is not exactly one sampler, the canvas
            already has a size of its own, the sampler starts from something
            this cannot replace, or the picture does not reach a reference
            input. A graph that reads no picture is text to image, made over
            by :func:`_text_to_sheet` instead and refused as it refuses.
    """
    out = copy.deepcopy(graph)
    nodes = {
        node_id: node
        for node_id, node in out.items()
        if isinstance(node, dict) and isinstance(node.get("class_type"), str)
    }
    classes = [node["class_type"] for node in nodes.values()]
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
    if not any(is_picture_loader(cls) for cls in classes):
        return _text_to_sheet(out, nodes, inputs)
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


def _seams(plane: np.ndarray, lo: int, hi: int, across: tuple[int, int]) -> np.ndarray:
    """Which lines *lo*..*hi* of *plane*, read over *across*, look like the
    edge between two photos.

    A point of a line is evidence when the line holds the largest step within
    :data:`SEAM_REACH` lines (or one of the two lines either side does: a
    seam's step is blurred over a few), or when texture stops there: the
    :data:`TEXTURE_LINES` lines on one side are busy (a sweater's knit, hair)
    and those on the other flat, as where one photo's sweater meets the next
    one's backdrop in much the same grey. A seam is a line with evidence on
    :data:`SEAM_SHARE` of *across*, and :data:`SEAM_PROMINENCE` times the
    lines around it: texture with a grain of its own puts evidence on every
    line of the grain alike.
    """
    size = plane.shape[1]
    reach = 2 * SEAM_REACH
    start, end = max(1, lo - reach), min(size - 1, hi + reach)
    first, last = max(1, start - SEAM_REACH - 2), min(size - 1, end + SEAM_REACH + 2)
    strip = plane[across[0] : across[1], first - 1 : last + 1].astype(np.float32)
    step = np.abs(strip[:, 2:] - strip[:, :-2])
    ridge = (step >= SEAM_STEP) & (
        step == ndimage.maximum_filter1d(step, 2 * SEAM_REACH + 1, axis=1)
    )
    ridge = ndimage.maximum_filter1d(ridge.astype(np.uint8), 5, axis=1)
    # Busyness: the mean change between neighbouring lines, over the lines
    # either side of each line (activity[:, j] is between strip lines j, j+1).
    activity = np.abs(np.diff(strip, axis=1))
    total = np.concatenate(
        [np.zeros((len(strip), 1), np.float32), np.cumsum(activity, axis=1)], axis=1
    )
    at = np.arange(start, end) - first

    def busy(begin: np.ndarray) -> np.ndarray:
        begin = np.clip(begin, 0, activity.shape[1])
        stop = np.clip(begin + TEXTURE_LINES - 1, 0, activity.shape[1])
        return (total[:, stop] - total[:, begin]) / (TEXTURE_LINES - 1)

    before, after = busy(at - TEXTURE_LINES + 1), busy(at + 2)
    loud, quiet = np.maximum(before, after), np.minimum(before, after)
    texture = (loud >= TEXTURE_STEP) & (loud >= TEXTURE_RATIO * quiet)
    share = (ridge[:, start - first : end - first].astype(bool) | texture).mean(axis=0)
    around = ndimage.median_filter(share, size=2 * reach + 1, mode="nearest")
    seam = (share >= SEAM_SHARE) & (share >= SEAM_PROMINENCE * around)
    return seam[lo - start : hi - start]


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
    to its edges plus a soft seam's width); down the middle of the nearest
    seam (:func:`_seams`) between two photos drawn touching (not off
    white, which is the frame's edge), a soft seam's width trimmed each side;
    at *nominal* moved into the nearest wider or step-less band (white frame,
    or background, nothing trimmed); or, with none, at *nominal* moved off
    the faces. Only a gutter, a seam or white frame is ``sure``: wherever two
    photos meet there is one of them, so background with no line in it is
    one photo, or two the cut cannot tell apart.
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
    gutter = max(1, round(MAX_GUTTER * scale))
    bleed = max(1, round(2 * scale))
    margin = gutter // 4

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

    def into(run: tuple[int, int]) -> tuple[int, int, bool]:
        # *nominal* moved into *run*, nothing trimmed; sure unless the run is
        # pressed against the window's edge, away from the grid line, where
        # it most likely goes on past it (a sheet drawn at another scale).
        start, end = run
        line = min(max(nominal, start + margin), end - 1 - margin)
        if line < start:
            line = (start + end) // 2
        sure = start <= nominal < end or lo + margin <= line < hi - margin
        return line, line, sure

    # A narrow band with a step at its edges is a gutter or a drawn line,
    # trimmed to its edges.
    dividers = [r for r in runs if r[1] - r[0] <= gutter and stepped(*r)]
    if dividers:
        start, end = min(dividers, key=distance)
        return start - bleed, end + bleed, True

    def white_beside(start: int, end: int) -> bool:
        return all(
            0 <= edge < size and level(edge) > FRAME_LEVEL
            for edge in (start - 1 - bleed, end + bleed)
        )

    # A band of white is frame (or a colour gutter, painted white), however
    # wide, and so is a band across the layout's own line with white either
    # side: FLUX.2 Klein frames white-backed passport photos in a wide grey
    # border, and the cut belongs in the grey. Frame is trimmed to its edges
    # when the window holds both; the other stays to the frame trim.
    frames = [r for r in runs if level((r[0] + r[1] - 1) // 2) > FRAME_LEVEL]
    bordered = [r for r in runs if r[0] <= nominal < r[1] and white_beside(*r)]
    if frames or bordered:
        start, end = min(frames + bordered, key=distance)
        if (start, end) in frames and lo < start and end < hi:
            return start - bleed, end + bleed, True
        return into((start, end))
    # Photos drawn touching, each on its own backdrop (MiniMax H3), meet in a
    # seam with no band between them: a backdrop lit top to bottom is no
    # band, and a lit arm can match it in grey. A step off white is the
    # frame's own edge, which the frame trim finds: white is read a margin
    # out, past the edge's blur.
    span = plane[across[0] : across[1]]

    def side(offset: int) -> np.ndarray:
        return span[:, np.clip(lines + offset, 0, size - 1)]

    white = np.maximum(
        np.median(side(margin), axis=0), np.median(side(-1 - margin), axis=0)
    )
    seam = _seams(plane, lo, hi, across) & (white <= FRAME_LEVEL) & ~fenced
    if seam.any():
        # The nearest seam, cut down its middle: every line within a soft
        # seam's width of the step reads it.
        first = last = int(np.argmin(np.where(seam, np.abs(lines - nominal), np.inf)))
        while first > 0 and seam[first - 1]:
            first -= 1
        while last < len(seam) - 1 and seam[last + 1]:
            last += 1
        line = lo + (first + last) // 2
        return line - bleed, line + bleed, True
    if runs:
        # Background with no line in it: where the photos meet, if they are
        # two, but nothing says they are.
        line, _same, _sure = into(min(runs, key=distance))
        return line, line, False
    if fenced.all():
        # Faces across the whole window: the line furthest from them.
        line = lo + int(np.argmax(clearance))
    else:
        line = lo + int(np.argmin(np.where(fenced, np.inf, np.abs(lines - nominal))))
    return line, line, False


def _content(gray: np.ndarray) -> Box:
    """*gray* past every whole line of white frame round its edges."""
    height, width = gray.shape
    white = gray > FRAME_LEVEL
    rows = np.flatnonzero(white.mean(axis=1) <= 0.95)
    cols = np.flatnonzero(white.mean(axis=0) <= 0.95)
    if not len(rows) or not len(cols):
        return 0, 0, width, height
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def _gutter_mask(rgb: np.ndarray) -> np.ndarray | None:
    """Which pixels are colour gutter, or ``None`` when the sheet's edge is
    not one colour of the magenta family (:data:`GUTTER_HUES`)."""
    hsv = np.asarray(Image.fromarray(rgb).convert("HSV"), dtype=np.int16)
    hue = hsv[..., 0]
    coloured = (hsv[..., 1] > GUTTER_SATURATION) & (hsv[..., 2] > GUTTER_VALUE)
    ring = np.ones(hue.shape, dtype=bool)
    ring[GUTTER_RING:-GUTTER_RING, GUTTER_RING:-GUTTER_RING] = False
    edge = ring & coloured
    if edge.sum() < ring.sum() / 2:
        return None
    frame = int(np.median(hue[edge]))
    if not GUTTER_HUES[0] <= frame <= GUTTER_HUES[1]:
        return None
    off = np.abs(hue - frame)
    return coloured & (np.minimum(off, 256 - off) < GUTTER_HUE)


def _off_gutter(cell: Box, gutter: np.ndarray, bleed: int) -> Box:
    """*cell* moved in past the colour gutter along its edges.

    An edge line is gutter while more than :data:`EDGE_GUTTER` of it is
    gutter running in from its ends: a photo drawn askew or with rounded
    corners leaves gutter at an edge's ends, while a pink sleeve in the
    middle of the edge is the photo's own. *bleed* more go from every edge
    with gutter outside it, for the colour's soft edge.
    """
    # ponytail: a pink garment reaching a panel's corner reads as gutter and
    # costs that edge its rows; compare texture if a model draws one.

    def gutter_share(line: np.ndarray) -> float:
        if line.all():
            return 1.0
        return (int(np.argmin(line)) + int(np.argmin(line[::-1]))) / len(line)

    box = list(cell)
    while box[2] - box[0] > 2 * bleed + 2 and box[3] - box[1] > 2 * bleed + 2:
        left, top, right, bottom = box
        shares = [
            gutter_share(gutter[top:bottom, left]),
            gutter_share(gutter[top, left:right]),
            gutter_share(gutter[top:bottom, right - 1]),
            gutter_share(gutter[bottom - 1, left:right]),
        ]
        # The most gutter goes first: a column of gutter beside the photo
        # also puts gutter at the ends of its top and bottom rows.
        edge = int(np.argmax(shares))
        if shares[edge] <= EDGE_GUTTER:
            break
        box[edge] += 1 if edge < 2 else -1
    # Every edge with gutter just outside it, trimmed or not, loses *bleed*.
    left, top, right, bottom = box
    height, width = gutter.shape
    outside = [
        left > 0 and gutter_share(gutter[top:bottom, left - 1]) > EDGE_GUTTER,
        top > 0 and gutter_share(gutter[top - 1, left:right]) > EDGE_GUTTER,
        right < width and gutter_share(gutter[top:bottom, right]) > EDGE_GUTTER,
        bottom < height and gutter_share(gutter[bottom, left:right]) > EDGE_GUTTER,
    ]
    return (
        left + bleed * outside[0],
        top + bleed * outside[1],
        right - bleed * outside[2],
        bottom - bleed * outside[3],
    )


def _in_layout_order(boxes: list[Box]) -> list[Box]:
    """*boxes* as the layout numbers them: the two tallest are the bodies,
    left to right, after the other four, top row first, each row left to
    right."""
    by_height = sorted(boxes, key=lambda b: b[3] - b[1])
    passports = sorted(by_height[:-2], key=lambda b: b[1])
    rows = sorted(passports[:2]) + sorted(passports[2:])
    return rows + sorted(by_height[-2:])


def _laid_out(cells: list[Box], width: int, height: int) -> bool:
    """Whether *cells* are the sheet's layout: four passport photos of one
    size, two by two, left of two body photos of another, each clearly taller
    and over most of the sheet's height, none overlapping."""
    if len(cells) != PANEL_COUNT:
        return False
    if any(
        not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height)
        for x0, y0, x1, y1 in cells
    ):
        return False
    if any(
        a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
        for a, b in combinations(cells, 2)
    ):
        return False

    def alike(boxes: list[Box], kind: str) -> bool:
        return all(
            max(sizes) <= min(sizes) * (1 + SAME_SIZE[kind])
            for sizes in (
                [box[2] - box[0] for box in boxes],
                [box[3] - box[1] for box in boxes],
            )
        )

    p1, p2, p3, p4, b5, b6 = cells
    body_height = min(b5[3] - b5[1], b6[3] - b6[1])
    return (
        alike([p1, p2, p3, p4], "passport")
        and alike([b5, b6], "body")
        and p1[2] <= p2[0]
        and p3[2] <= p4[0]
        and p1[3] <= p3[1]
        and p2[3] <= p4[1]
        and max(p2[2], p4[2]) <= b5[0]
        and b5[2] <= b6[0]
        and body_height >= 1.3 * (p1[3] - p1[1])
        and body_height >= 0.7 * height
    )


def _colour_cells(gutter: np.ndarray) -> list[Box]:
    """Step 1: the six largest regions between the colour gutters, in layout
    order, each moved in past the gutter round it."""
    photos = ndimage.binary_opening(~gutter, iterations=3)
    labels, count = ndimage.label(photos)
    if count < PANEL_COUNT:
        return []
    sizes = ndimage.sum_labels(photos, labels, range(1, count + 1))
    slices = ndimage.find_objects(labels)
    bleed = max(1, round(2 * gutter.shape[1] / SHEET_WIDTH))
    boxes = [
        _off_gutter(
            (
                slices[index][1].start,
                slices[index][0].start,
                slices[index][1].stop,
                slices[index][0].stop,
            ),
            gutter,
            bleed,
        )
        for index in np.argsort(sizes)[::-1][:PANEL_COUNT]
    ]
    return _in_layout_order(boxes)


def _seam_cells(
    gray: np.ndarray, centres: list[tuple[float, float, float]]
) -> tuple[list[Box], list[tuple[int, int, bool]]]:
    """Step 2: the cells cut along gutters and seams, and the cuts.

    The passport block's edge is searched where the layout puts it (1200 of
    2560; FLUX.2 Klein draws it as near as 920, MiniMax H3 at 1290), over
    the sheet past its white frame. Every other cut then starts from the middle of the block it
    splits, since the photos in a block are drawn one size, and searches a
    quarter of the block either side. The passport row is cut once per
    passport column, each over its own span, and the passport column then
    once per row, over that row: a model that draws the two rows with their
    gutters apart (MiniMax H3 Image does) would otherwise leave a strip of the
    next photo on one row's cut.
    """
    height, width = gray.shape
    sx, sy = width / SHEET_WIDTH, height / SHEET_HEIGHT
    left, top, right, bottom = _content(gray)
    across_x = [(x, y, reach) for x, y, reach in centres]
    across_y = [(y, x, reach) for x, y, reach in centres]

    def column_cut(
        start: int, end: int, span: tuple[int, int]
    ) -> tuple[int, int, bool]:
        middle, reach = (start + end) // 2, (end - start) // 4
        window = (middle - reach, middle + reach)
        return _find_cut(gray, middle, window, span, True, across_x, sx)

    def row_cut(start: int, end: int, span: tuple[int, int]) -> tuple[int, int, bool]:
        middle, reach = (start + end) // 2, (end - start) // 4
        window = (middle - reach, middle + reach)
        return _find_cut(gray, middle, window, span, False, across_y, sy)

    wide = (right - left) / SHEET_WIDTH
    half = _find_cut(
        gray,
        left + round(1200 * wide),
        (left + round(768 * wide), left + round(1540 * wide)),
        (top, bottom),
        True,
        across_x,
        sx,
    )
    bodies = column_cut(half[1], right, (top, bottom))
    _l, block_top, _r, block_bottom = _content(gray[:, left : half[0]])
    column = column_cut(left, half[0], (block_top, block_bottom))
    rows = [
        row_cut(block_top, block_bottom, span)
        for span in ((left, column[0]), (column[1], half[0]))
    ]
    top_row = column_cut(left, half[0], (block_top, min(rows[0][0], rows[1][0])))
    bottom_row = column_cut(left, half[0], (max(rows[0][1], rows[1][1]), block_bottom))
    cells = [
        (left, block_top, top_row[0], rows[0][0]),
        (top_row[1], block_top, half[0], rows[1][0]),
        (left, rows[0][1], bottom_row[0], block_bottom),
        (bottom_row[1], rows[1][1], half[0], block_bottom),
        (half[1], top, bodies[0], bottom),
        (bodies[1], top, right, bottom),
    ]
    return cells, [half, bodies, top_row, bottom_row, *rows]


def _sized_cells(gray: np.ndarray, half: tuple[int, int, bool]) -> list[Box]:
    """Step 3: the layout's own cells, sized to the sheet past its white
    frame and to the passport block's edge when step 2 was sure of it: four
    equal passport cells two by two, two equal body cells beside them."""
    left, top, right, bottom = _content(gray)
    end, start, sure = half
    if not sure:
        end = start = left + round(1200 * (right - left) / SHEET_WIDTH)
    middle, row = (left + end) // 2, (top + bottom) // 2
    split = (start + right) // 2
    return [
        (left, top, middle, row),
        (middle, top, end, row),
        (left, row, middle, bottom),
        (middle, row, end, bottom),
        (start, top, split, bottom),
        (split, top, right, bottom),
    ]


def sheet_cells(
    image: Image.Image, faces: list[list[int]] | None = None
) -> tuple[list[Box], bool]:
    """The six panels' boxes on *image*, in order, and whether the cut is clean.

    Three steps, each tried only when the one before did not find the
    sheet's layout (:func:`_laid_out`):

    1. Colour gutters: on a sheet drawn on magenta, the six largest regions
       between the gutters (:func:`_colour_cells`).
    2. Seams: the cuts searched along gutters, seams and white frame
       (:func:`_seam_cells`, :func:`_find_cut`). A colour gutter counts as
       white frame here, so a sheet whose gutters ran together in places
       (Qwen-Image draws them round pairs of photos) is cut along the seams
       inside them.
    3. Size: the layout's own cells (:func:`_sized_cells`), never clean.

    Whatever is left of the white frame round each photo is then trimmed,
    and any colour gutter.

    *faces* are the sheet's stored face boxes. A cut never runs through a
    face's centre, and the cut is clean only when every cut found a gutter,
    a seam or frame and no panel holds two faces' centres. A cut that is not
    clean still cuts: it is for the person to judge, from the cells drawn on
    the sheet.
    """
    width, height = image.size
    sx = width / SHEET_WIDTH
    rgb = np.asarray(image.convert("RGB"))
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
    gutter = _gutter_mask(rgb)
    limit, bleed = round(64 * sx), max(1, round(2 * sx))

    def trimmed(cells: list[Box]) -> list[Box]:
        cells = [_inside_frame(gray, cell, limit, bleed) for cell in cells]
        if gutter is not None:
            cells = [_off_gutter(cell, gutter, bleed) for cell in cells]
        return cells

    step, sure = "colour gutters", True
    # The white frame some models draw round each photo goes too, as in the
    # other two steps.
    cells = trimmed(_colour_cells(gutter)) if gutter is not None else []
    if not _laid_out(cells, width, height):
        if gutter is not None:
            gray = np.where(gutter, 255, gray).astype(np.int16)
        # Seams are read at the layout's own size: a larger sheet is drawn
        # from a larger latent, its seams blurred over as many more pixels.
        factor = max(1.0, width / SHEET_WIDTH)
        small = gray
        if factor > 1:
            small = np.asarray(
                Image.fromarray(gray.astype(np.uint8)).resize(
                    (round(width / factor), round(height / factor)), Image.BOX
                ),
                dtype=np.int16,
            )
        found, cuts = _seam_cells(
            small, [(x / factor, y / factor, r / factor) for x, y, r in centres]
        )
        step, sure = "seams", all(cut[2] for cut in cuts)
        cells = trimmed(
            [
                (
                    min(round(left * factor), width),
                    min(round(top * factor), height),
                    min(round(right * factor), width),
                    min(round(bottom * factor), height),
                )
                for left, top, right, bottom in found
            ]
        )
        if not _laid_out(cells, width, height):
            step, sure = "size", False
            end, start, half_sure = cuts[0]
            half = (round(end * factor), round(start * factor), half_sure)
            cells = trimmed(_sized_cells(gray, half))
    clean = sure and all(
        sum(left <= x < right and top <= y < bottom for x, y, _ in centres) <= 1
        for left, top, right, bottom in cells
    )
    (logger.warning if step == "size" else logger.info)(
        "Sheet %dx%d cut by %s (clean=%s).", width, height, step, clean
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
    its corners, when that is short of a quarter of *limit*. *bleed* more go
    with any trim, for the soft seam.
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
    # A rounded corner's white runs a few lines in along the diagonal; white
    # that runs a quarter of *limit* or more is the photo's own (FLUX.2
    # Klein lights its passport backdrops near white at a corner).
    reach = max(1, limit // 4)

    def rounded(flags: np.ndarray) -> int:
        n = run(flags[:reach])
        return 0 if n == reach else n

    corner = {
        (ys, xs): rounded(np.diagonal(white[::ys, ::xs]))
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


def _grid_alike(cells: list[Box], width: int, height: int) -> bool:
    """Whether *cells* are one size each (within the passports' share), on
    the sheet and none overlapping: an item sheet's layout."""
    if any(
        not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height)
        for x0, y0, x1, y1 in cells
    ):
        return False
    if any(
        a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
        for a, b in combinations(cells, 2)
    ):
        return False
    return all(
        max(sizes) <= min(sizes) * (1 + SAME_SIZE["passport"])
        for sizes in (
            [box[2] - box[0] for box in cells],
            [box[3] - box[1] for box in cells],
        )
    )


def item_sheet_cells(image: Image.Image, kind: ItemKind) -> tuple[list[Box], bool]:
    """The four panels' boxes on an item sheet, row by row, and whether the
    cut is clean.

    The cuts are searched as a character sheet's are (:func:`_find_cut`:
    gutters, white frame, seams), each from the grid line a quarter of the
    span it splits either side: the columns first over the whole height, then
    each column's rows over that column, so a model that draws the rows'
    borders apart is followed in each. With no faces nothing fences a cut. A
    cut that does not land on the grid's layout (:func:`_grid_alike`) falls
    back to the grid itself, never clean. White frame and colour gutter are
    trimmed as on a character sheet.
    """
    rows, cols = ITEM_GRIDS[kind]
    width, height = image.size
    sx = width / SHEET_WIDTH
    rgb = np.asarray(image.convert("RGB"))
    gray = np.asarray(image.convert("L"), dtype=np.int16)
    gutter = _gutter_mask(rgb)
    if gutter is not None:
        gray = np.where(gutter, 255, gray).astype(np.int16)
    limit, bleed = round(64 * sx), max(1, round(2 * sx))
    # Read at the layout's own size, as sheet_cells reads seams.
    factor = max(1.0, width / SHEET_WIDTH)
    small = gray
    if factor > 1:
        small = np.asarray(
            Image.fromarray(gray.astype(np.uint8)).resize(
                (round(width / factor), round(height / factor)), Image.BOX
            ),
            dtype=np.int16,
        )
    s_height, s_width = small.shape
    left, top, right, bottom = _content(small)

    def splits(start: int, end: int, n: int, span, vertical: bool):
        cuts, scale = (
            [],
            (s_width / SHEET_WIDTH if vertical else s_height / SHEET_HEIGHT),
        )
        reach = (end - start) // (4 * n)
        for i in range(1, n):
            nominal = start + (end - start) * i // n
            cuts.append(
                _find_cut(
                    small,
                    nominal,
                    (nominal - reach, nominal + reach),
                    span,
                    vertical,
                    [],
                    scale,
                )
            )
        return cuts

    column_cuts = splits(left, right, cols, (top, bottom), True)
    edges = [(left, left), *[(c[1], c[0]) for c in column_cuts], (right, right)]
    columns = [(edges[i][0], edges[i + 1][1]) for i in range(cols)]
    found: list[list[Box]] = [[] for _ in range(rows)]
    cuts = list(column_cuts)
    for x0, x1 in columns:
        row_cuts = splits(top, bottom, rows, (x0, x1), False)
        cuts.extend(row_cuts)
        lines = [(top, top), *[(c[1], c[0]) for c in row_cuts], (bottom, bottom)]
        for r in range(rows):
            found[r].append((x0, lines[r][0], x1, lines[r + 1][1]))
    cells = [
        (
            min(round(x0 * factor), width),
            min(round(y0 * factor), height),
            min(round(x1 * factor), width),
            min(round(y1 * factor), height),
        )
        for row in found
        for x0, y0, x1, y1 in row
    ]
    sure = all(cut[2] for cut in cuts)
    if not _grid_alike(cells, width, height):
        sure = False
        x0, y0, x1, y1 = (round(v * factor) for v in (left, top, right, bottom))
        cells = [
            (
                x0 + (x1 - x0) * c // cols,
                y0 + (y1 - y0) * row // rows,
                x0 + (x1 - x0) * (c + 1) // cols,
                y0 + (y1 - y0) * (row + 1) // rows,
            )
            for row in range(rows)
            for c in range(cols)
        ]
    cells = [_inside_frame(gray, cell, limit, bleed) for cell in cells]
    if gutter is not None:
        cells = [_off_gutter(cell, gutter, bleed) for cell in cells]
    (logger.info if sure else logger.warning)(
        "%s sheet %dx%d cut (clean=%s).", kind.capitalize(), width, height, sure
    )
    return cells, sure


# A panel agrees with the others when their colours overlap this much
# (histogram intersection, 0..1). ponytail: no outfit, location or prop sheet
# has been drawn yet to read it off; probe MiniMax H3 Image sheets and tune.
ITEM_AGREEMENT = 0.5
# Colour histogram bins per channel: coarse, so the light turning with the
# view moves few pixels across a bin.
_COLOUR_BINS = 4
# A panel's edge is its backdrop when every channel of it varies less than
# this; pixels within _BACKDROP_REACH grey levels of its colour are left out.
_BACKDROP_SPREAD = 12
_BACKDROP_REACH = 24


def _colours(panel: Image.Image) -> np.ndarray:
    """*panel*'s colour histogram, its backdrop left out, summing to 1."""
    small = panel.convert("RGB")
    small.thumbnail((96, 96))
    pixels = np.asarray(small, dtype=np.int16)
    edge = np.concatenate([pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]], axis=0)
    flat = pixels.reshape(-1, 3)
    if (edge.std(axis=0) < _BACKDROP_SPREAD).all():
        backdrop = np.median(edge, axis=0)
        subject = np.abs(flat - backdrop).max(axis=1) > _BACKDROP_REACH
        # A panel that is nearly all backdrop is judged on all of it.
        if subject.mean() >= 0.05:
            flat = flat[subject]
    bins = (flat * _COLOUR_BINS // 256).clip(0, _COLOUR_BINS - 1)
    index = (bins[:, 0] * _COLOUR_BINS + bins[:, 1]) * _COLOUR_BINS + bins[:, 2]
    counts = np.bincount(index, minlength=_COLOUR_BINS**3).astype(np.float64)
    return counts / max(1.0, counts.sum())


def colour_agreement(panels: list[Image.Image]) -> list[float]:
    """Each panel's mean colour overlap with the other panels (0..1).

    Item sheets have no face to rank by; within one sheet the same coat or
    street keeps its colours from every angle, and a panel that drifts (a back
    drawn in another colour) overlaps the others less.
    """
    hists = [_colours(panel) for panel in panels]
    if len(hists) < 2:
        return [1.0] * len(hists)
    return [
        float(
            np.mean(
                [np.minimum(h, other).sum() for j, other in enumerate(hists) if j != i]
            )
        )
        for i, h in enumerate(hists)
    ]


def item_agreement_words(kind: ItemKind, agreements: list[float]) -> str:
    """An item sheet's agreement as the dialog says it."""
    views = "views" if kind == "location" else "angles"
    off = [
        ITEM_ANGLES[kind][i].lower()
        for i, value in enumerate(agreements)
        if value < ITEM_AGREEMENT
    ]
    if not off:
        return f"the four {views} agree"
    if len(off) == 1:
        return f"the {off[0]} drifts in colour"
    return f"{len(off)} {views} drift in colour"


def _unit(vectors: list[np.ndarray] | np.ndarray) -> np.ndarray:
    """*vectors* as rows of unit length; a 2-D array is taken as that already."""
    if isinstance(vectors, np.ndarray) and vectors.ndim == 2:
        return vectors
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
    embeddings: list[np.ndarray], references: list[np.ndarray], best: bool = False
) -> float | None:
    """How close a sheet's faces come to the references, averaged over its faces.

    Each face counts its mean cosine similarity to the references, or with
    *best* its highest: against a cluster, a face need only match one of them.
    """
    if not len(embeddings) or not len(references):
        return None
    similarity = _unit(embeddings) @ _unit(references).T
    return float(np.mean(similarity.max(axis=1) if best else similarity))


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
    best: bool = False,
) -> list[dict]:
    """Each panel's face scored on its own, in panel order.

    *faces* are a sheet's ``(embedding, box)`` pairs, *width* and *height* its
    picture's. A panel's face is its largest; a panel with none is left out.
    Its ``agreement`` is its mean similarity to the other panels' faces, its
    ``closeness`` to *references* (with *best*, its best match among them), and its ``box`` the face's box as fractions
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
        near = closeness([vector], references, best)
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


class SheetBoxesInvalid(ValueError):
    """The photo boxes a person cut by hand are not six photos on the sheet."""


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
    faces are not read yet. The references are every face of the character's
    reference cluster (without one, its reference faces), or, given
    *reference_picture_ids*, the faces of those
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
        cluster = reference_cluster_faces(
            session, character_id, CLUSTER_FACES
        ) or select_reference_faces_for_character(session, character_id)
        references = [face.features for face in cluster if face.picture_id not in faces]
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
    ignored). Against the character's, a face's closeness is its best match
    among them, so a sheet like any one look of the person is close; against
    pictures, its mean similarity to their faces. Returns one dict per sheet,
    in the order asked, and how many reference faces closeness was measured
    against. Raises :class:`SheetNotFound` for an unknown character.
    """
    ids = list(dict.fromkeys(picture_ids))
    found = vault.db.run_immediate_read_task(
        sheet_faces_in_session, character_id, ids, reference_picture_ids
    )
    if found is None:
        raise SheetNotFound(f"No character {character_id}.")
    faces, reference_rows, pictures = found
    references = _vectors(reference_rows)
    # Normalised once: a cluster's thousands of faces meet every sheet and panel.
    references = _unit(references) if references else []
    best = reference_picture_ids is None
    vectors_of = {pid: _vectors([row[0] for row in faces[pid]]) for pid in ids}
    out = []
    for picture_id in ids:
        rows = faces[picture_id]
        vectors = vectors_of[picture_id]
        score = agreement(vectors)
        near = closeness(vectors, references, best)
        format_, width, height, file_path = pictures.get(picture_id, (None,) * 4)
        boxed = [
            (
                np.frombuffer(features, dtype=np.float32),
                json.loads(bbox) if bbox else None,
            )
            for features, bbox in rows
            if features
        ]
        # The cut needs no faces: they only fence it. It is shown from the
        # moment the sheet exists, and the face-aware one replaces it once the
        # faces are read. Cached per file and face boxes, so polling re-reads
        # nothing.
        cut = (
            _sheet_cut(
                vault.image_root,
                picture_id,
                file_path,
                _face_boxes([bbox for _, bbox in rows]),
            )
            if file_path
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
                "panels": panel_scores(boxed, width, height, references, best),
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


def _hand_cut_panels(
    image: Image.Image, boxes: list[list[float]], count: int = PANEL_COUNT
) -> list[Image.Image]:
    """The photos a person cut by hand, each cropped exactly as drawn.

    Each box is `[left, top, right, bottom]` as fractions of the sheet, in
    panel order. Raises :class:`SheetBoxesInvalid` for anything that is not
    *count* boxes (six on a character sheet, four on an item sheet) inside the
    sheet, inverted, or under :data:`MIN_EDITED_PX` a side.
    """
    width, height = image.size
    if len(boxes) != count:
        raise SheetBoxesInvalid(
            f"A sheet is cut into {count} photos, not {len(boxes)} boxes."
        )
    panels = []
    for box in boxes:
        if len(box) != 4:
            raise SheetBoxesInvalid(f"A photo box is four numbers, not {box}.")
        left, top, right, bottom = box
        # A NaN fails every comparison, so it is refused here with the rest.
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            raise SheetBoxesInvalid(
                f"A photo box must lie inside the sheet with left < right and "
                f"top < bottom, not {box}."
            )
        pixels = (
            round(left * width),
            round(top * height),
            round(right * width),
            round(bottom * height),
        )
        if (
            pixels[2] - pixels[0] < MIN_EDITED_PX
            or pixels[3] - pixels[1] < MIN_EDITED_PX
        ):
            raise SheetBoxesInvalid(
                f"A photo box must be at least {MIN_EDITED_PX} px a side on the "
                f"sheet, not {pixels[2] - pixels[0]} by {pixels[3] - pixels[1]} px."
            )
        panels.append(image.crop(pixels))
    return panels


def _cut(
    server,
    sheet: Picture,
    faces: list[list[int]],
    boxes: list[list[float]] | None = None,
    kind: Kind = "character",
) -> list[tuple[bytes, str]]:
    """The sheet's panels as PNG bytes, ready to import.

    A character sheet's six, cut round *faces*; an item sheet's four
    (:func:`item_sheet_cells`). Either cut where a person drew them, at
    *boxes*, instead.
    """
    path = ImageUtils.resolve_picture_path(server.vault.image_root, sheet.file_path)
    if not path or not os.path.isfile(path):
        raise SheetUnreadable(
            f"Picture {sheet.id}'s file is missing, so it can't be cut."
        )
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            if boxes is not None:
                count = PANEL_COUNT if kind == "character" else len(ITEM_ANGLES[kind])
                panels = _hand_cut_panels(rgb, boxes, count)
            elif kind == "character":
                panels = split_sheet(rgb, faces)
            else:
                panels = [rgb.crop(box) for box in item_sheet_cells(rgb, kind)[0]]
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
    boxes: dict[int, list[list[float]]] | None = None,
) -> list[int]:
    """Cut each sheet into its six panels and file them on the character.

    Passport photos first, sheet by sheet, each cut round its stored faces
    as :func:`rank_sheets` cuts it (faces read since the last rank count
    too); one that does not cut cleanly is cut all the same. A sheet in
    *boxes* (picture id to six `[left, top, right, bottom]` fractions, as a
    person drew them) is cut at those instead, exactly. Every sheet is read
    and every box checked before anything is imported, so a bad one imports
    nothing. Returns the panels' picture ids. Raises :class:`SheetNotFound`,
    :class:`SheetUnreadable` and :class:`SheetBoxesInvalid` (a box for a
    picture not among *picture_ids* included). The sheets themselves stay in
    the Scrapheap.
    """
    ids = list(dict.fromkeys(picture_ids))
    sheets = server.vault.db.run_immediate_read_task(
        sheets_in_session, character_id, ids
    )
    if sheets is None:
        raise SheetNotFound(f"No character {character_id} or no picture among {ids}.")
    boxes = boxes or {}
    stray = sorted(set(boxes) - set(ids))
    if stray:
        raise SheetBoxesInvalid(f"Boxes given for pictures {stray}, not among {ids}.")
    faces = server.vault.db.run_immediate_read_task(sheet_face_boxes_in_session, ids)
    cuts = [
        (sheet, _cut(server, sheet, faces.get(sheet.id, []), boxes.get(sheet.id)))
        for sheet in sheets
    ]
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


def pictures_in_session(
    session: Session, picture_ids: list[int]
) -> list[Picture] | None:
    """The pictures, in the order asked; ``None`` when any does not exist. A
    held sheet (soft-deleted on import) counts."""
    pictures = [session.get(Picture, pid) for pid in picture_ids]
    return None if any(picture is None for picture in pictures) else pictures


@lru_cache(maxsize=64)
def _item_rank_of_file(
    path: str, stamp: int, kind: str
) -> tuple[tuple[tuple[float, ...], ...], bool, tuple[float, ...]]:
    """:func:`item_sheet_cells` of the file at *path* as fractions, whether it
    cuts cleanly, and each panel's :func:`colour_agreement`. Keyed by *stamp*
    (its mtime) as :func:`_cut_of_file` is."""
    with Image.open(path) as image:
        image = image.convert("RGB")
    cells, clean = item_sheet_cells(image, kind)
    width, height = image.size
    fractions = tuple(
        (left / width, top / height, right / width, bottom / height)
        for left, top, right, bottom in cells
    )
    agreements = tuple(colour_agreement([image.crop(cell) for cell in cells]))
    return fractions, clean, agreements


def rank_item_sheets(vault, kind: ItemKind, picture_ids: list[int]) -> list[dict]:
    """Score each outfit, location or prop sheet by how its panels agree.

    There are no faces to wait for: a sheet is ranked from its file as soon
    as it is imported. Each panel scores its :func:`colour_agreement` with the
    other three; the sheet, their mean. One dict per sheet, in the order
    asked. A sheet whose file cannot be read comes back unscored. Raises
    :class:`SheetNotFound` when a picture does not exist.
    """
    ids = list(dict.fromkeys(picture_ids))
    pictures = vault.db.run_immediate_read_task(pictures_in_session, ids)
    if pictures is None:
        raise SheetNotFound(f"No picture among {ids}.")
    out = []
    for picture in pictures:
        path = ImageUtils.resolve_picture_path(vault.image_root, picture.file_path)
        ranked = None
        try:
            if not path:
                raise FileNotFoundError(picture.file_path)
            ranked = _item_rank_of_file(path, os.stat(path).st_mtime_ns, kind)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            logger.warning(
                "Couldn't read %s sheet %s at %s to rank it: %s",
                kind,
                picture.id,
                path,
                exc,
            )
        cells, clean, agreements = ranked or ((), None, ())
        out.append(
            {
                "picture_id": picture.id,
                "format": (picture.format or "").lower() or None,
                "readable": ranked is not None,
                "agreement": float(np.mean(agreements)) if agreements else None,
                "agreement_words": (
                    item_agreement_words(kind, list(agreements))
                    if agreements
                    else "can't be read"
                ),
                "cuts_cleanly": clean,
                "cells": [list(cell) for cell in cells],
                "panels": [
                    {
                        "panel": index,
                        "box": list(cell),
                        "agreement": value,
                        "agrees": value >= ITEM_AGREEMENT,
                    }
                    for index, (cell, value) in enumerate(zip(cells, agreements))
                ],
            }
        )
    return out


def unlink_from_sheet_in_session(session: Session, picture_ids: list[int]) -> None:
    """Drop each panel's link to its sheet: the sheet stays in the Scrapheap,
    and a picture naming it as source would fail its purge on the foreign key."""
    for picture in session.exec(
        select(Picture).where(Picture.id.in_(picture_ids))
    ).all():
        picture.source_picture_id = None
        session.add(picture)
    session.commit()


def use_item_sheets(
    server,
    kind: ItemKind,
    picture_ids: list[int],
    boxes: dict[int, list[list[float]]] | None = None,
) -> list[int]:
    """Cut each outfit, location or prop sheet into its four panels.

    As :func:`use_sheets`, without the character: the panels are imported as
    pictures made from their sheet, in panel order sheet by sheet, and filed on
    nobody (the dialog puts them in a set of their own). A sheet in *boxes* is
    cut at its four boxes, exactly. Every sheet is read and every box checked
    before anything is imported. Raises :class:`SheetNotFound`,
    :class:`SheetUnreadable` and :class:`SheetBoxesInvalid`.
    """
    ids = list(dict.fromkeys(picture_ids))
    sheets = server.vault.db.run_immediate_read_task(pictures_in_session, ids)
    if sheets is None:
        raise SheetNotFound(f"No picture among {ids}.")
    boxes = boxes or {}
    stray = sorted(set(boxes) - set(ids))
    if stray:
        raise SheetBoxesInvalid(f"Boxes given for pictures {stray}, not among {ids}.")
    cuts = [
        (sheet, _cut(server, sheet, [], boxes.get(sheet.id), kind)) for sheet in sheets
    ]
    panel_ids: list[int] = []
    for sheet, entries in cuts:
        _new, _duplicates, made = import_derived_outputs(
            server, [sheet] * len(entries), entries, stack=False
        )
        server.vault.db.run_task(unlink_from_sheet_in_session, made)
        panel_ids.extend(made)
    logger.info("%s sheets %s cut into pictures %s", kind.capitalize(), ids, panel_ids)
    return panel_ids
