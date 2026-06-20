"""Person-tag taxonomy and the keyword classifier for impossible-tag review.

A picture with no detectable face that still carries person-tags ("brown hair",
"face", "nose") is a suspect. But no-face is only a prior, not a verdict: a hand, a
shoulder, a back-of-head are people with no visible face. So the *description*
decides, and the strip is category-aware:

  * Face-requiring tags (face, nose, lips, eyes…) literally need a face, so they are
    wrong on *any* no-face picture and are stripped regardless of the description.
  * The broader person/body tags (hair, hand, shoulder…) are stripped only when the
    description confirms a true non-person object ("wooden wardrobe", "motorcycle").

The vocabulary here is a STARTER set, deliberately conservative — clothing and
accessory tags are excluded, because a product photo of a dress legitimately carries
"dress" with no person in frame. Refine ``PERSON_TAGS`` from real co-occurrence on
no-face pictures with ``scripts/report_impossible_tags.py`` before trusting the
auto-strip tiers. The classifier is keyword-first by design (deterministic, auditable);
an optional LLM judge can replace :func:`classify_description` for the ambiguous bucket
later without changing this interface.
"""

import re

from pixlstash.db_models.tag import is_description_sentinel

# Tags that literally require a visible face — wrong on any no-face picture.
FACE_REQUIRING_TAGS = frozenset(
    {
        "face",
        "facial",
        "lips",
        "parted lips",
        "nose",
        "mouth",
        "open mouth",
        "closed mouth",
        "teeth",
        "tongue",
        "tongue out",
        "eyes",
        "eye",
        "closed eyes",
        "one eye closed",
        "eyebrows",
        "eyebrow",
        "eyelashes",
        "freckles",
        "cheeks",
        "cheek",
        "chin",
        "smile",
        "grin",
        "blush",
        "lipstick",
        "makeup",
        "eyeshadow",
        "mascara",
        "blue eyes",
        "green eyes",
        "brown eyes",
        "red eyes",
        "grey eyes",
        "gray eyes",
        "hazel eyes",
        "yellow eyes",
        "purple eyes",
        "pink eyes",
        "black eyes",
        "heterochromia",
    }
)

# Hair tags — a person's hair. Strip only when the description confirms an object.
_HAIR_TAGS = frozenset(
    {
        "hair",
        "long hair",
        "short hair",
        "medium hair",
        "very long hair",
        "brown hair",
        "black hair",
        "blonde hair",
        "blond hair",
        "red hair",
        "white hair",
        "grey hair",
        "gray hair",
        "pink hair",
        "blue hair",
        "purple hair",
        "green hair",
        "orange hair",
        "silver hair",
        "bald",
        "ponytail",
        "twintails",
        "braid",
        "bangs",
        "curly hair",
        "wavy hair",
        "straight hair",
        "messy hair",
        "bob cut",
        "hair over one eye",
    }
)

# Body-part tags — only meaningful with a body present.
_BODY_TAGS = frozenset(
    {
        "hand",
        "hands",
        "arm",
        "arms",
        "shoulder",
        "shoulders",
        "leg",
        "legs",
        "foot",
        "feet",
        "finger",
        "fingers",
        "fingernails",
        "thigh",
        "thighs",
        "knee",
        "knees",
        "neck",
        "back",
        "bare back",
        "stomach",
        "navel",
        "chest",
        "breasts",
        "nipples",
        "skin",
        "collarbone",
        "midriff",
        "hip",
        "hips",
        "torso",
        "toes",
        "elbow",
        "wrist",
        "palm",
    }
)

# The full person vocabulary. Face-requiring is a subset of this.
PERSON_TAGS = FACE_REQUIRING_TAGS | _HAIR_TAGS | _BODY_TAGS

# "There is no person here" meta-tags. A no-face picture that the tagger marked with
# one of these *and* still gave person-tags contradicts itself — the person-tags are the
# suspects (we never remove the meta-tag itself; on a faced picture it's the meta-tag
# that's wrong, which this tool deliberately leaves alone by staying no-face-gated).
OBJECT_META_TAGS = frozenset({"no humans", "scenery", "no people", "nobody"})

# Words whose presence in a caption means a person/body is in frame. Kept GENEROUS on
# purpose: a missed person-word would wrongly mark a real person-shot as an "object"
# and strip its hair/body tags, so we err toward "not an object" (keep, don't strip).
PERSON_DESCRIPTION_WORDS = frozenset(
    {
        "person",
        "people",
        "man",
        "men",
        "woman",
        "women",
        "girl",
        "girls",
        "boy",
        "boys",
        "child",
        "children",
        "kid",
        "lady",
        "ladies",
        "guy",
        "human",
        "figure",
        "model",
        "face",
        "portrait",
        "selfie",
        "head",
        "hair",
        "hand",
        "hands",
        "arm",
        "arms",
        "shoulder",
        "shoulders",
        "body",
        "torso",
        "leg",
        "legs",
        "foot",
        "feet",
        "finger",
        "skin",
        "back",
        "chest",
        "breast",
        "breasts",
        "nude",
        "naked",
        "wearing",
        "she",
        "he",
        "her",
        "his",
        "him",
        "they",
        "their",
    }
)

# Body-part words that signal a partial body (no full face/person), so we keep
# hair/body but still strip face-requiring tags.
_BODY_PART_WORDS = frozenset(
    {
        "hand",
        "hands",
        "arm",
        "arms",
        "shoulder",
        "shoulders",
        "leg",
        "legs",
        "foot",
        "feet",
        "finger",
        "fingers",
        "back",
        "torso",
        "knee",
        "thigh",
    }
)

# Full-person / face words: their presence means a whole person or face may be shown,
# so we must NOT downgrade to a bodypart crop — keep it ambiguous (strip only the
# face-requiring tags, never the hair/body ones).
_FULL_PERSON_WORDS = frozenset(
    {
        "person",
        "people",
        "man",
        "men",
        "woman",
        "women",
        "girl",
        "boy",
        "lady",
        "human",
        "face",
        "portrait",
        "selfie",
        "model",
        "figure",
    }
)

# Each impossibility signal is its own suggestion ``source`` so the grid can offer a
# named filter per signal (and "Any" = all of them). The score is the signal's
# reliability, surfaced on the suggestion for ranking / a future global threshold.
SOURCE_NO_HUMANS = "impossible:no_humans"  # tagged "no humans" + person-tags
SOURCE_NO_FACE = "impossible:no_face"  # face-requiring tag, no detected face
SOURCE_OBJECT = "impossible:object"  # caption describes a non-person object
IMPOSSIBLE_SOURCES = (SOURCE_NO_HUMANS, SOURCE_NO_FACE, SOURCE_OBJECT)

TIER_NO_HUMANS_SCORE = 0.95  # meta-tag evidence — strongest
TIER_NO_FACE_SCORE = 0.85  # face tag with no face — almost always wrong
TIER_OBJECT_SCORE = 0.70  # caption-based — caption-miss risk, lowest

_WORD_RE = re.compile(r"[a-z]+")


def _norm(tag: str | None) -> str:
    return (tag or "").strip().lower()


def is_face_requiring(tag: str | None) -> bool:
    """True if *tag* literally needs a face (so it is wrong on a no-face picture)."""
    return _norm(tag) in FACE_REQUIRING_TAGS


def is_person_tag(tag: str | None) -> bool:
    """True if *tag* describes a person/body (face-requiring tags included)."""
    return _norm(tag) in PERSON_TAGS


def classify_description(description: str | None) -> str:
    """Classify a caption as ``'object'`` | ``'bodypart'`` | ``'ambiguous'``.

    Keyword-first and deterministic:

      * empty / whitespace / pending-description sentinel  → ``'ambiguous'``
      * no person/body word at all                        → ``'object'``
      * a body-part word but no full-person/face word      → ``'bodypart'``
      * otherwise (mentions a person/face)                 → ``'ambiguous'``

    ``'bodypart'`` and ``'ambiguous'`` produce the same strip plan (face-requiring
    tags only); they are kept distinct for reporting and for the future LLM judge.
    """
    if (
        not description
        or is_description_sentinel(description)
        or not description.strip()
    ):
        return "ambiguous"
    words = set(_WORD_RE.findall(description.lower()))
    if not words & PERSON_DESCRIPTION_WORDS:
        return "object"
    has_full_person = bool(words & _FULL_PERSON_WORDS)
    has_bodypart = bool(words & _BODY_PART_WORDS)
    if has_bodypart and not has_full_person:
        return "bodypart"
    return "ambiguous"


def plan_strips(description: str | None, tags: list[str] | set[str]) -> dict:
    """Decide which signal fires for a picture, which tags to flag, and the score.

    Each picture gets exactly one signal (one suggestion ``source``); all its flagged
    tags carry that source. Precedence is by evidence strength — the broader, more
    certain object signals win over the face-only one:

      1. **no_humans** — an ``OBJECT_META_TAGS`` tag present → strip every person-tag.
      2. **object**    — caption describes a non-person object → strip every person-tag.
      3. **no_face**   — a face-requiring tag present → strip just the face-requiring
         tags (keep hair/body, which can be legit on a no-face body-part shot).

    Args:
        description: The picture's caption (may be ``None`` / a sentinel).
        tags: The tags currently on the picture (any iterable of tag strings). The
            ``OBJECT_META_TAGS`` evidence is read from here too; the meta-tag itself is
            never flagged — only person-tags are.

    Returns:
        ``{"source", "verdict", "flag", "score", "person_present", "face_present"}``.
        ``source`` is ``None`` and ``flag`` empty when nothing fires (e.g. a no-face
        body-part shot with only hair tags and no object evidence — left untouched).
    """
    person_present = {t for t in tags if is_person_tag(t)}
    face_present = {t for t in tags if is_face_requiring(t)}
    meta_present = {t for t in tags if _norm(t) in OBJECT_META_TAGS}

    if meta_present:
        source, flag, score, verdict = (
            SOURCE_NO_HUMANS,
            set(person_present),
            TIER_NO_HUMANS_SCORE,
            "no_humans",
        )
    elif classify_description(description) == "object":
        source, flag, score, verdict = (
            SOURCE_OBJECT,
            set(person_present),
            TIER_OBJECT_SCORE,
            "object",
        )
    elif face_present:
        source, flag, score, verdict = (
            SOURCE_NO_FACE,
            set(face_present),
            TIER_NO_FACE_SCORE,
            "no_face",
        )
    else:
        source, flag, score, verdict = None, set(), 0.0, "none"

    return {
        "source": source,
        "verdict": verdict,
        "flag": flag,
        "score": score,
        "person_present": person_present,
        "face_present": face_present,
    }
