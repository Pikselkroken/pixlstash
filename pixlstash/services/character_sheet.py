"""Character sheets: one MOSS-Image picture of one person, drawn six times.

The "Create a training set…" dialog can draw a character instead of picking
from pictures that exist. A sheet is a 2560×1536 grid: four passport photos on
the left half (front neutral, three-quarter smiling, profile neutral, other
three-quarter laughing) and two tall body photos on the right (front, three
quarter) at the framing the person picked. Text pins down a type, not a person,
so each sheet is a slightly different person; within one sheet the six faces
agree, which is why the person picks one sheet and its six panels become the
character's pictures.

Three pure pieces live here, the routes in ``routes/character_sheets.py``:

* :func:`sheet_prompt` builds the MOSS layout prompt (the JSON
  ``MOSSImageLayoutPrompt`` emits). The boxes and captions are the ones that
  held their layout on 16 of 16 probe sheets; change them only with new probes.
* :func:`split_sheet` cuts a drawn sheet into its six panels.
* :func:`agreement` / :func:`closeness` score a sheet's faces, against each
  other and against the character's reference faces.

And two that touch the library, each a ``*_in_session`` function behind a thin
vault wrapper (backend architecture §10.1): :func:`rank_sheets` and
:func:`use_sheet`.
"""

from __future__ import annotations

import io
import json
import os
from itertools import combinations
from typing import Literal

import numpy as np
from PIL import Image
from sqlmodel import Session, select

from pixlstash.db_models import Character, Face, Picture
from pixlstash.image_plugins.service import import_derived_outputs
from pixlstash.pixl_logging import get_logger
from pixlstash.scoring import select_reference_faces_for_character
from pixlstash.services import comfyui_service, scrapheap_service
from pixlstash.services.move_reconciliation_service import add_person
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
) -> str:
    """The MOSS layout prompt for one grid sheet of the character described.

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
    who = f"{lead}{_phrase(description) or f'an adult {subject}'}, {body}"
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
    return json.dumps(
        {
            "summary": {"caption": summary},
            "items": items,
            "bbox_coord_space": "abs_centralized",
            "bbox_format": "yxyx",
        }
    )


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


def _vectors(rows: list) -> list[np.ndarray]:
    return [np.frombuffer(features, dtype=np.float32) for features in rows if features]


def sheet_faces_in_session(
    session: Session, character_id: int, picture_ids: list[int]
) -> tuple[dict[int, list], list, dict[int, str]] | None:
    """Each sheet's face embeddings, the character's reference embeddings, each sheet's format.

    ``None`` when the character does not exist. A sheet's list holds a ``None``
    for the row face extraction leaves on a picture with no face, so an empty
    list means its faces are not read yet. The sheets' own faces are never
    their own references. The format is what the picture's file is served as.
    """
    if session.get(Character, character_id) is None:
        return None
    faces: dict[int, list] = {pid: [] for pid in picture_ids}
    for picture_id, features in session.exec(
        select(Face.picture_id, Face.features).where(Face.picture_id.in_(picture_ids))
    ).all():
        faces[picture_id].append(features)
    references = [
        face.features
        for face in select_reference_faces_for_character(session, character_id)
        if face.picture_id not in faces
    ]
    formats = dict(
        session.exec(
            select(Picture.id, Picture.format).where(Picture.id.in_(picture_ids))
        ).all()
    )
    return faces, references, formats


def rank_sheets(
    vault, character_id: int, picture_ids: list[int]
) -> tuple[list[dict], int]:
    """Score each sheet by its own faces and by the character's reference faces.

    Returns one dict per sheet, in the order asked, and how many reference
    faces closeness was measured against. Raises :class:`SheetNotFound` for an
    unknown character.
    """
    ids = list(dict.fromkeys(picture_ids))
    found = vault.db.run_immediate_read_task(sheet_faces_in_session, character_id, ids)
    if found is None:
        raise SheetNotFound(f"No character {character_id}.")
    faces, reference_rows, formats = found
    references = _vectors(reference_rows)
    out = []
    for picture_id in ids:
        rows = faces[picture_id]
        vectors = _vectors(rows)
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
    """File each picture on the character: its largest face, or once one is read."""
    for picture in session.exec(
        select(Picture).where(Picture.id.in_(picture_ids))
    ).all():
        add_person(session, picture, character_id)
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


def use_sheets(server, character_id: int, picture_ids: list[int]) -> list[int]:
    """Cut each sheet into its six panels and file them on the character.

    Each panel is imported as a picture made from its sheet (its
    ``source_picture_id``), passport photos first, sheet by sheet. Every sheet
    is read before anything is imported, so an unreadable one imports nothing.
    Returns the panels' picture ids. Raises :class:`SheetNotFound` and
    :class:`SheetUnreadable`. The sheets themselves are left as they are.
    """
    ids = list(dict.fromkeys(picture_ids))
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
        panel_ids.extend(made)
    server.vault.db.run_task(file_on_character_in_session, panel_ids, character_id)
    logger.info(
        "Character sheets %s cut into pictures %s, filed on character %s",
        ids,
        panel_ids,
        character_id,
    )
    return panel_ids


def _unlink_sources_in_session(session: Session, picture_ids: list[int]) -> None:
    """Forget *picture_ids* as any picture's source, so they can be purged."""
    for picture in session.exec(
        select(Picture).where(Picture.source_picture_id.in_(picture_ids))
    ).all():
        picture.source_picture_id = None
        session.add(picture)
    session.commit()


def discard_sheets(server, picture_ids: list[int]) -> list[int]:
    """Destroy the held sheets among *picture_ids*: rows and files, for good.

    Only pictures a run imported held (``comfyui_service.held_picture_ids``)
    are touched, so nothing else in the Scrapheap can be destroyed through
    here; a sheet that is not held any more (kept, discarded, or held before a
    restart) is left alone. Panels cut from a sheet keep their pictures and
    lose only the link to it. Returns the ids destroyed.
    """
    held = sorted(comfyui_service.held_picture_ids(picture_ids))
    if not held:
        return []
    server.vault.db.run_task(_unlink_sources_in_session, held)
    outcome = scrapheap_service.purge_scrapheap_pictures(
        server.vault, held, include_protected=False
    )
    comfyui_service.release_held(held)
    logger.info("Discarded held character sheets %s", list(outcome.purged_ids))
    return list(outcome.purged_ids)
