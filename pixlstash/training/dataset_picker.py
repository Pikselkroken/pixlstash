"""Pick a character LoRA's training set from the character's pictures.

A pure ranking over plain rows: the route
(``GET /training/character_set_suggestion``) gathers the faces, tags,
embeddings and near pairs, and :func:`rank` orders the stills and the clips
separately with one greedy pass each. A slider in the dialog takes a prefix of
that order, so a bigger set always adds the next most useful picture and never
reshuffles the earlier ones.

The value of a picture is its quality percentile times its likeness. The
greedy pass multiplies that by what the picture adds: a framing or expression
label the picks hold fewer of than :data:`TARGET_SHARE` asks for, and its
CLIP distance from the nearest pick (outfit, setting, light).
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
"""Below this face similarity (0-1) a picture is set aside as a poor match."""

SMART_SCORE_FLOOR = 3.0
"""An unrated picture whose smart score is below this is set aside.

Smart scores run about 1-5; this drops roughly the weakest 5% of a library.
"""

LOW_RATING = 2
"""A picture the user rated 1-2 stars is set aside."""

W_NEW = 1.0
"""The gain of a picture bringing a framing or expression under its target."""

W_SEM = 1.5
"""The gain per unit of CLIP cosine distance from the nearest pick."""

REASONS = ("other_people", "near_duplicates", "low_quality", "low_likeness")
"""The four set-aside reasons, each with an ``include_<reason>`` toggle."""

_FRAMING_TAGS = (
    # The first label a picture's tags name wins: "full body" and "close-up"
    # are unambiguous, "portrait" is narrower than "upper body".
    ("full body", ("full body",)),
    ("close-up", ("close-up", "close-up view")),
    ("portrait", ("portrait",)),
    ("upper body", ("upper body", "cowboy shot")),
)

_FACE_AREA_FRAMING = (
    # Face box over frame area, for a picture with no framing tag.
    # ponytail: fixed cut-offs, measured on portrait and full-length stills;
    # tune if wide-angle libraries misfile.
    (0.12, "close-up"),
    (0.04, "portrait"),
    (0.01, "upper body"),
    (0.0, "full body"),
)

_EXPRESSION_TAGS = (
    ("smile", ("smile", "grin")),
    ("open mouth", ("open mouth",)),
    ("closed eyes", ("closed eyes",)),
)

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


def framing_label(tags: set[str], face_area_ratio: float | None) -> str | None:
    """The picture's framing, from its tags or else its face's size."""
    for label, names in _FRAMING_TAGS:
        if any(name in tags for name in names):
            return label
    if face_area_ratio is None:
        return None
    for floor, label in _FACE_AREA_FRAMING:
        if face_area_ratio >= floor:
            return label
    return None


def expression_labels(tags: set[str]) -> tuple[str, ...]:
    """The picture's expressions; ``neutral`` when its tags name none."""
    found = tuple(
        label for label, names in _EXPRESSION_TAGS if any(n in tags for n in names)
    )
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
    face_area_ratio: float | None = None
    embedding: np.ndarray | None = None
    stack_id: int | None = None
    framing: str | None = field(init=False, default=None)
    expressions: tuple[str, ...] = field(init=False, default=())

    def __post_init__(self):
        folded = {str(tag).strip().casefold() for tag in self.tags if tag}
        self.framing = framing_label(folded, self.face_area_ratio)
        self.expressions = expression_labels(folded)


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


def _intrinsic_reasons(c: Candidate) -> set[str]:
    reasons = set()
    if c.face_count_max > 1:
        reasons.add("other_people")
    rating = c.rating or 0
    if 0 < rating <= LOW_RATING or (
        rating == 0 and c.smart_score is not None and c.smart_score < SMART_SCORE_FLOOR
    ):
        reasons.add("low_quality")
    if c.likeness is None or c.likeness < LIKENESS_FLOOR:
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


def _passing(cands, value, pairs, include) -> tuple[list[Candidate], dict]:
    """The candidates that pass the filters, and how many each reason sets aside."""
    reasons = {c.picture_id: _intrinsic_reasons(c) for c in cands}
    # A set takes a whole stack, so a stack passes only if every member
    # showing the character does: a reason against one is against them all.
    by_stack: dict[int, set[str]] = {}
    for c in cands:
        if c.stack_id is not None:
            by_stack.setdefault(c.stack_id, set()).update(reasons[c.picture_id])
    for c in cands:
        if c.stack_id is not None:
            reasons[c.picture_id] = by_stack[c.stack_id]
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


def _greedy(pool: list[Candidate], value: dict[int, float]) -> list[Candidate]:
    """Order *pool* by greedy gain, up to :data:`MAX_PICKS`."""
    rest = sorted(pool, key=lambda c: c.picture_id)
    units = {c.picture_id: _unit(c.embedding) for c in rest}
    # None until a pick with an embedding has been measured against.
    min_dist: dict[int, float | None] = {c.picture_id: None for c in rest}
    counts = {name: {} for name, _ in AXES}
    picks: list[Candidate] = []
    while rest and len(picks) < MAX_PICKS:
        size = len(picks) + 1

        def gain(c):
            if not picks:
                return value[c.picture_id]
            short = 0
            for name, labels_of in AXES:
                if any(
                    counts[name].get(label, 0) < target(name, label, size)
                    for label in labels_of(c)
                ):
                    short += 1
            return value[c.picture_id] * (
                1 + W_NEW * short + W_SEM * (min_dist[c.picture_id] or 0.0)
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


def _rank_group(cands, pairs, include) -> Ranking:
    quality = _quality(cands)
    value = {
        c.picture_id: quality[c.picture_id] * max(0.0, min(1.0, c.likeness or 0.0))
        for c in cands
    }
    pool, excluded = _passing(cands, value, pairs, include)
    would_add = {}
    for reason in REASONS:
        if include[reason]:
            would_add[reason] = 0
            continue
        wider, _ = _passing(cands, value, pairs, {**include, reason: True})
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
        for i, c in enumerate(_greedy(pool, value))
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
) -> dict[str, Ranking]:
    """Rank the stills and the clips, each on its own.

    Args:
        candidates: Every picture and clip showing the character.
        near_pairs: ``(picture_id_a, picture_id_b)`` pairs that are near
            copies of each other; each group keeps its best member.
        include: Reason -> True to let the pictures it sets aside back in.

    Returns:
        ``{"stills": Ranking, "clips": Ranking}``.
    """
    include = {r: bool((include or {}).get(r)) for r in REASONS}
    pairs = [(int(p[0]), int(p[1])) for p in near_pairs]
    return {
        group: _rank_group(
            [c for c in candidates if c.is_clip == is_clip], pairs, include
        )
        for group, is_clip in (("stills", False), ("clips", True))
    }
