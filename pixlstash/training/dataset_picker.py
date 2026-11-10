"""Pick a character LoRA's training set from the character's pictures.

A pure ranking over plain rows: the route
(``GET /training/character_set_suggestion``) gathers the faces, tags,
embeddings and near pairs, and :func:`rank` orders the stills and the clips
separately with one greedy pass each. A slider in the dialog takes a prefix of
that order, so a bigger set always adds the next most useful picture and never
reshuffles the earlier ones.

The value of a picture is its quality percentile times its likeness. The
greedy pass multiplies that by what the picture adds: a framing or expression
label the picks hold fewer of than :data:`TARGET_SHARE` asks for, as far as
the caller says that kind of variety matters (:data:`VARIETY`), and its CLIP
distance from the nearest pick (outfit, setting, light).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np

from pixlstash.scoring import compute_character_likeness_for_faces

MAX_PICKS = 50
"""The longest ranking a group returns: the dialog's slider stops here."""

MAX_REFERENCES = 10
"""How many of the character's faces the likeness is measured against."""

LIKENESS_FLOOR = 0.30
"""The default minimum likeness: a face similarity (0-1) below it is set aside."""

SCORE_FLOOR = 3.0
"""The default minimum score: a picture scoring below it is set aside.

A picture's score is the user's rating where they gave one and its smart score
where they did not. Smart scores run about 1-5; 3.0 drops 1-2 star pictures
and roughly the weakest 5% of the unrated ones.
"""

VARIETY = ("unimportant", "somewhat", "important")
"""How much a kind of variety (:data:`AXES`) matters to the ranking.

``important``: a picture filling a gap beats any repeat. ``somewhat``: its
value counts :data:`SOMEWHAT_GAIN` more. ``unimportant``: the gap is ignored.
"""

SOMEWHAT_GAIN = 0.6
"""At ``somewhat`` a gap-filler beats a repeat up to this much better.

Per axis: a picture filling a gap on two such axes counts twice this.
"""

W_SEM = 1.5
"""The gain per unit of CLIP cosine distance from the nearest pick."""

REASONS = ("other_people", "near_duplicates", "low_quality", "low_likeness")
"""The four set-aside reasons; ``include`` lets one's pictures back in."""

_FRAMING_TAGS = (
    # For a picture with no face box. The first label its tags name wins:
    # "full body" and "close-up" are unambiguous, "portrait" is narrower than
    # "upper body".
    ("full body", ("full body",)),
    ("close-up", ("close-up", "close-up view")),
    ("portrait", ("portrait",)),
    ("upper body", ("upper body", "cowboy shot")),
)

_FACE_ROOM_FRAMING = (
    # :func:`face_room` on a stored face box, which face extraction widens by
    # a quarter and rounds up to 64 px at its 512 px working size, so a box
    # reads about 0.75 of the room a tight face box would. Read off the demo
    # library sorted by room: chest-up under 2.1, waist-up and seated about
    # 2.2-2.9, thigh-up mirror shots about 3.1-3.4, whole figures above.
    # ponytail: fixed cut-offs on the largest face; a seated or lying figure
    # reads tighter than it is. A pose model would settle it.
    (1.5, "close-up"),
    (2.1, "portrait"),
    (3.5, "upper body"),
)

_EXPRESSION_TAGS = (
    ("smile", ("smile", "grin")),
    ("open mouth", ("open mouth",)),
    ("closed eyes", ("closed eyes",)),
)

_SMILE_WORDS = ("smil", "grin", "laugh")
"""A caption naming one of these agrees with a smile tag.

The whole-picture tagger calls a small face smiling with high confidence when
it is not (0.92 on a frowning face); a caption reads the face the way a person
does, so one that never mentions smiling vetoes the tag.
"""

TARGET_SHARE = {
    "framing": {
        "close-up": 1 / 5,
        "portrait": 1 / 5,
        "upper body": 1 / 5,
        "full body": 2 / 15,
    },
    "expression": {
        "smile": 2 / 15,
        "neutral": 2 / 15,
        "open mouth": 1 / 15,
        "closed eyes": 1 / 15,
    },
}
"""The share of a set each label should hold, never fewer than one picture.

At 15 pictures: three each of close-up, portrait and upper body, two full
body, two smiling, two neutral, one open mouth and one closed eyes.
"""


def target(axis: str, label: str, size: int) -> int:
    """How many pictures of *label* a set of *size* should hold."""
    return max(1, int(TARGET_SHARE[axis][label] * size + 0.5))


def face_room(
    box: tuple[float, float, float, float],
    width: int | None,
    height: int | None,
    orientation: int | None = None,
) -> float | None:
    """Face heights from the top of *box* to the bottom of the picture.

    How much of the body the frame has room for, whatever its shape. *box* is
    a stored face box, in the picture as shown; *width* and *height* are the
    file's raw size, so an EXIF orientation of 5-8 (a quarter turn) swaps
    them. ``None`` for a box or size that cannot be measured.
    """
    _x1, y1, _x2, y2 = box
    shown_height = width if orientation in (5, 6, 7, 8) else height
    if not shown_height or y2 <= y1:
        return None
    return (shown_height - max(0.0, y1)) / (y2 - y1)


def framing_label(tags: set[str], face_room: float | None) -> str | None:
    """The picture's framing, from its face's place in the frame, else tags.

    The face box is measured; a framing tag is a whole-picture guess that
    calls a waist-up shot a close-up, so it decides only with no face box.
    """
    if face_room is None:
        for label, names in _FRAMING_TAGS:
            if any(name in tags for name in names):
                return label
        return None
    for ceiling, label in _FACE_ROOM_FRAMING:
        if face_room < ceiling:
            return label
    return "full body"


def expression_labels(
    tags: set[str], description: str | None = None
) -> tuple[str, ...]:
    """The picture's expressions; ``neutral`` when its tags name none.

    A smile tag holds only if the picture has no caption or its caption
    mentions smiling (:data:`_SMILE_WORDS`).
    """
    found = tuple(
        label for label, names in _EXPRESSION_TAGS if any(n in tags for n in names)
    )
    caption = (description or "").casefold()
    if caption.strip() and not any(word in caption for word in _SMILE_WORDS):
        found = tuple(label for label in found if label != "smile")
    return found or ("neutral",)


AXES = (
    ("framing", lambda c: (c.framing,) if c.framing else ()),
    ("expression", lambda c: c.expressions),
)
"""The kinds of variety a pick is rewarded for: ``(name, labels_of)``."""


@dataclass
class Candidate:
    """One picture or clip showing the character, as the picker reads it."""

    picture_id: int
    is_clip: bool = False
    rating: int | None = None
    smart_score: float | None = None
    likeness: float | None = None
    face_count_max: int = 1
    tags: tuple[str, ...] = ()
    face_room: float | None = None  # face heights below the face's top
    description: str | None = None
    embedding: np.ndarray | None = None
    stack_id: int | None = None
    framing: str | None = field(init=False, default=None)
    expressions: tuple[str, ...] = field(init=False, default=())

    def __post_init__(self):
        folded = {str(tag).strip().casefold() for tag in self.tags if tag}
        self.framing = framing_label(folded, self.face_room)
        self.expressions = expression_labels(folded, self.description)


def pick_references(faces: list) -> list:
    """The reference faces for likeness, best evidence first.

    *faces* are ``(face_id, picture_id, rating, smart_score, features)``
    tuples. 5-star pictures first, then 4-star, then by smart score; one face
    per picture, at most :data:`MAX_REFERENCES`.
    """

    def order(row):
        _face_id, picture_id, rating, smart, _features = row
        stars = rating or 0
        tier = 0 if stars >= 5 else 1 if stars >= 4 else 2
        return (tier, -(smart if smart is not None else float("-inf")), picture_id)

    seen = set()
    refs = []
    for row in sorted((f for f in faces if f[4] is not None), key=order):
        if row[1] in seen:
            continue
        seen.add(row[1])
        refs.append(row)
        if len(refs) == MAX_REFERENCES:
            break
    return refs


def face_likeness(references: list, faces: list) -> dict[int, float]:
    """Each face's similarity to the references, a reference leaving itself out.

    A reference compared against a set that holds it scores near 1, which
    would inflate the pictures already rated best; it is scored against the
    other references instead. A lone reference has nothing else to be
    measured against, so it keeps its own score (1). *references* and *faces*
    are ``(face_id, features)`` pairs. Returns ``face_id -> similarity``.
    """

    def ns(rows):
        return [SimpleNamespace(id=fid, features=feat) for fid, feat in rows]

    ref_ids = {fid for fid, _ in references}
    out = compute_character_likeness_for_faces(
        ns(references), ns([f for f in faces if f[0] not in ref_ids])
    )
    for fid, feat in faces:
        if fid in ref_ids:
            others = [r for r in references if r[0] != fid] or references
            out.update(
                compute_character_likeness_for_faces(ns(others), ns([(fid, feat)]))
            )
    return out


@dataclass
class Pick:
    """One ranked picture: its 1-based rank, likeness 0-100 and labels."""

    picture_id: int
    rank: int
    likeness: int | None
    buckets: dict[str, list[str]]


@dataclass
class Ranking:
    """What :func:`rank` returns for one group (stills or clips)."""

    picks: list[Pick]
    passing: int
    excluded: dict[str, int]
    would_add: dict[str, int]
    available: dict[str, dict[str, int]]


def _quality(cands: list[Candidate]) -> dict[int, float]:
    """Each candidate's quality percentile in (0, 1]: real scores first.

    Sorted by the user's rating, then smart score; a rated picture outranks
    every unrated one. Ties share the better position.
    """

    def key(c):
        smart = c.smart_score if c.smart_score is not None else float("-inf")
        return (-(c.rating or 0), -smart)

    ordered = sorted(cands, key=key)
    n = len(ordered)
    out, prev, pos = {}, None, 0
    for i, c in enumerate(ordered):
        if key(c) != prev:
            prev, pos = key(c), i
        out[c.picture_id] = 1.0 - pos / n
    return out


def _intrinsic_reasons(c: Candidate, floors: tuple[float, float]) -> set[str]:
    """The reasons against *c* at ``(min_likeness 0-1, min_score)``.

    A picture with no likeness counts as 0, and one with neither a rating nor
    a smart score has no score to fail.
    """
    min_likeness, min_score = floors
    reasons = set()
    if c.face_count_max > 1:
        reasons.add("other_people")
    score = c.rating or c.smart_score
    if score is not None and score < min_score:
        reasons.add("low_quality")
    if (c.likeness or 0.0) < min_likeness:
        reasons.add("low_likeness")
    return reasons


def _components(ids: list[int], pairs) -> list[list[int]]:
    """Connected components of the near pairs, restricted to *ids*."""
    parent = {i: i for i in ids}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in pairs:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _stack_reasons(candidates: list[Candidate], floors) -> dict[int, set[str]]:
    """The reasons against each stack: the union over its members.

    A set takes a whole stack, so a stack passes only if every member showing
    the character does, stills and clips alike.
    """
    by_stack: dict[int, set[str]] = {}
    for c in candidates:
        if c.stack_id is not None:
            by_stack.setdefault(c.stack_id, set()).update(_intrinsic_reasons(c, floors))
    return by_stack


def _passing(
    cands, value, pairs, include, by_stack, floors
) -> tuple[list[Candidate], dict]:
    """The candidates that pass the filters, and how many each reason sets aside."""
    reasons = {
        c.picture_id: (
            by_stack[c.stack_id]
            if c.stack_id is not None
            else _intrinsic_reasons(c, floors)
        )
        for c in cands
    }
    excluded = {r: 0 for r in REASONS}
    for rs in reasons.values():
        for r in rs:
            excluded[r] += 1
    survivors = [
        c for c in cands if not any(not include[r] for r in reasons[c.picture_id])
    ]
    dropped = set()
    for group in _components([c.picture_id for c in survivors], pairs):
        if len(group) < 2:
            continue
        best = max(group, key=lambda pid: (value[pid], -pid))
        dropped.update(pid for pid in group if pid != best)
    excluded["near_duplicates"] = len(dropped)
    if include["near_duplicates"]:
        dropped = set()
    return [c for c in survivors if c.picture_id not in dropped], excluded


def _unit(embedding) -> np.ndarray | None:
    if embedding is None:
        return None
    vec = np.asarray(embedding, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 1e-8 else None


def _greedy(
    pool: list[Candidate], value: dict[int, float], variety: dict[str, str]
) -> list[Candidate]:
    """Order *pool* by greedy gain, up to :data:`MAX_PICKS`."""
    rest = sorted(pool, key=lambda c: c.picture_id)
    units = {c.picture_id: _unit(c.embedding) for c in rest}
    # None until a pick with an embedding has been measured against.
    min_dist: dict[int, float | None] = {c.picture_id: None for c in rest}
    counts = {name: {} for name, _ in AXES}
    picks: list[Candidate] = []
    while rest and len(picks) < MAX_PICKS:
        size = len(picks) + 1

        # On an important axis filling a gap comes first: the filters already
        # decided what is good enough, so a weaker picture of a missing
        # framing beats a better repeat. On a somewhat important one the gap
        # is a bonus on the value. Quality and look order the rest.
        def gain(c):
            if not picks:
                return (0, value[c.picture_id])
            must, bonus = 0, 0.0
            for name, labels_of in AXES:
                if variety[name] == "unimportant" or not any(
                    counts[name].get(label, 0) < target(name, label, size)
                    for label in labels_of(c)
                ):
                    continue
                if variety[name] == "important":
                    must += 1
                else:
                    bonus += SOMEWHAT_GAIN
            return (
                must,
                value[c.picture_id]
                * (1 + bonus)
                * (1 + W_SEM * (min_dist[c.picture_id] or 0.0)),
            )

        # max() keeps the first of equal gains: rest is in picture_id order.
        best = max(rest, key=gain)
        rest.remove(best)
        picks.append(best)
        for name, labels_of in AXES:
            for label in labels_of(best):
                counts[name][label] = counts[name].get(label, 0) + 1
        chosen = units[best.picture_id]
        for c in rest:
            vec = units[c.picture_id]
            if vec is None or chosen is None or vec.shape != chosen.shape:
                continue
            dist = max(0.0, 1.0 - float(vec @ chosen))
            old = min_dist[c.picture_id]
            min_dist[c.picture_id] = dist if old is None else min(old, dist)
    return picks


def _rank_group(cands, pairs, include, by_stack, floors, variety) -> Ranking:
    quality = _quality(cands)
    value = {
        c.picture_id: quality[c.picture_id] * max(0.0, min(1.0, c.likeness or 0.0))
        for c in cands
    }
    pool, excluded = _passing(cands, value, pairs, include, by_stack, floors)
    would_add = {}
    for reason in REASONS:
        if include[reason]:
            would_add[reason] = 0
            continue
        wider, _ = _passing(
            cands, value, pairs, {**include, reason: True}, by_stack, floors
        )
        would_add[reason] = len(wider) - len(pool)
    available = {name: {label: 0 for label in TARGET_SHARE[name]} for name, _ in AXES}
    for c in pool:
        for name, labels_of in AXES:
            for label in labels_of(c):
                available[name][label] += 1
    picks = [
        Pick(
            picture_id=c.picture_id,
            rank=i + 1,
            likeness=(
                None
                if c.likeness is None
                else round(max(0.0, min(1.0, c.likeness)) * 100)
            ),
            buckets={name: list(labels_of(c)) for name, labels_of in AXES},
        )
        for i, c in enumerate(_greedy(pool, value, variety))
    ]
    return Ranking(
        picks=picks,
        passing=len(pool),
        excluded=excluded,
        would_add=would_add,
        available=available,
    )


def rank(
    candidates: list[Candidate],
    near_pairs=(),
    *,
    include: dict[str, bool] | None = None,
    min_likeness: float = LIKENESS_FLOOR,
    min_score: float = SCORE_FLOOR,
    variety: dict[str, str] | None = None,
) -> dict[str, Ranking]:
    """Rank the stills and the clips, each on its own.

    Args:
        candidates: Every picture and clip showing the character.
        near_pairs: ``(picture_id_a, picture_id_b)`` pairs that are near
            copies of each other; each group keeps its best member.
        include: Reason -> True to let the pictures it sets aside back in.
        min_likeness: The likeness (0-1) below which a picture is set aside.
        min_score: The score (rating, else smart score) below which one is.
        variety: Axis name -> one of :data:`VARIETY`; ``important`` when
            the axis is not named.

    Returns:
        ``{"stills": Ranking, "clips": Ranking}``.
    """
    include = {r: bool((include or {}).get(r)) for r in REASONS}
    pairs = [(int(p[0]), int(p[1])) for p in near_pairs]
    floors = (min_likeness, min_score)
    variety = {name: (variety or {}).get(name, "important") for name, _ in AXES}
    by_stack = _stack_reasons(candidates, floors)
    return {
        group: _rank_group(
            [c for c in candidates if c.is_clip == is_clip],
            pairs,
            include,
            by_stack,
            floors,
            variety,
        )
        for group, is_clip in (("stills", False), ("clips", True))
    }
