"""The sample prompts a training run previews with, written from its dataset.

:func:`generate` turns the subject choice and the training pictures' captions
and tags into two scenes, each written twice: with the trigger word and without
it. The prompt without the word shows whether the LoRA changes pictures it was
not asked for.

- Scenes come from a fixed list per subject, and a scene whose words occur in
  the captions or tags is passed over, so the samples test flexibility rather
  than recall.
- For a person or a thing the prompt without the word names the subject
  plainly ("an elderly man"), taken from the captions, else the tags, else the
  subject's generic noun, so the pair differs only by the trigger. For a style
  the ``<trigger> style`` suffix is dropped.

The trigger word is written as the ``{{TRIGGER_WORD}}`` placeholder, which the
config render fills in, so the prompts follow an edited trigger word. Nothing
here is random: the same captions and subject give the same prompts.
"""

from __future__ import annotations

import re
from collections import Counter

TRIGGER = "{{TRIGGER_WORD}}"
SCENE_COUNT = 2

# Per subject: (scene, words that mean the dataset already shows it). "{s}"
# is the trigger word or the plain description; a style scene has none.
_SCENES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "person": (
        (
            "photo of {s} smiling, outdoors in soft daylight",
            ("outdoor", "daylight", "sunlight", "park", "garden"),
        ),
        (
            "{s} reading a newspaper in an armchair, warm lamp light",
            ("armchair", "newspaper", "lamp", "sofa", "couch"),
        ),
        (
            "{s} sitting at a cafe table, holding a coffee cup",
            ("cafe", "coffee", "restaurant", "cup"),
        ),
        ("{s} walking along a beach at sunset", ("beach", "sunset", "sea", "ocean")),
        ("{s} on a snowy mountain trail in winter", ("snow", "mountain", "winter")),
        ("{s} cooking in a bright kitchen", ("kitchen", "cooking", "stove")),
    ),
    "style": (
        ("a harbour at dusk", ("harbour", "harbor", "boat", "dusk")),
        (
            "two friends laughing at a kitchen table",
            ("kitchen", "friends", "laughing"),
        ),
        ("a lighthouse on a rocky coast", ("lighthouse", "coast", "cliff")),
        ("portrait of an old fisherman", ("fisherman", "portrait", "old man")),
        ("a fox in a snowy forest", ("fox", "snow", "forest")),
        ("a city street in the rain at night", ("city", "street", "rain", "night")),
    ),
    "thing": (
        (
            "photo of {s} on a wooden table, studio lighting",
            ("wooden", "table", "studio"),
        ),
        ("{s} in a busy city street", ("city", "street", "crowd")),
        ("{s} on a sandy beach in bright sunlight", ("beach", "sand", "sunlight")),
        ("{s} on a shelf in a cosy living room", ("shelf", "living room")),
        ("{s} in a snowy forest", ("snow", "forest")),
    ),
}
_GENERIC = {"person": "a person", "thing": "an object"}

_PERSON_NOUNS = {
    "man",
    "woman",
    "boy",
    "girl",
    "person",
    "child",
    "kid",
    "lady",
    "gentleman",
    "baby",
    "teenager",
    "guy",
}
# What a caption calls the picture itself, never its subject: "a close-up of".
_FRAMING = {
    "close-up",
    "closeup",
    "picture",
    "photo",
    "photograph",
    "image",
    "view",
    "shot",
    "painting",
    "drawing",
    "illustration",
    "render",
    "portrait",
}
# Words that end a noun phrase: "a red bicycle leaning on" stops at "bicycle".
_STOP = {
    "a",
    "an",
    "the",
    "of",
    "with",
    "and",
    "or",
    "in",
    "on",
    "at",
    "to",
    "by",
    "for",
    "from",
    "is",
    "are",
    "who",
    "that",
    "which",
    "wearing",
    "holding",
    "sitting",
    "standing",
    "leaning",
    "lying",
    "looking",
    "smiling",
    "walking",
    "posing",
    "hanging",
    "resting",
    "placed",
    "made",
}
_WORD = re.compile(r"[a-z][a-z'-]*")
_MAX_MODIFIERS = 2


def _article(phrase: str) -> str:
    return f"{'an' if phrase[0] in 'aeiou' else 'a'} {phrase}"


def _phrases(caption: str, nouns: set[str] | None) -> list[str]:
    """Every ``a/an <up to three words>`` noun phrase in *caption*, in order.

    With *nouns* the phrase must end on one of them ("an elderly man"). Without
    it the phrase runs to the first stop word ("a red bicycle" of "a red bicycle
    leaning on"), and one ending on a framing word ("a close-up") or a person
    ("a woman" wearing the dress) is not one.
    """
    words = _WORD.findall(caption.lower())
    found = []
    for i, word in enumerate(words):
        if word not in ("a", "an"):
            continue
        tail = []
        for nxt in words[i + 1 : i + 2 + _MAX_MODIFIERS]:
            if nxt in _STOP:
                break
            tail.append(nxt)
            if nouns is not None and nxt in nouns:
                break
        if not tail:
            continue
        if nouns is not None:
            keep = tail[-1] in nouns
        else:
            keep = tail[-1] not in _FRAMING and tail[-1] not in _PERSON_NOUNS
        if keep:
            found.append(f"{word} {' '.join(tail)}")
    return found


def describe(subject: str, captions: list[str], tags: list[list[str]]):
    """A plain description of the subject, and where it came from.

    Args:
        subject: ``person`` or ``thing``.
        captions: One caption per picture (empty strings allowed).
        tags: One tag list per picture.

    Returns:
        ``(description, source)``, source being ``captions``, ``tags`` or
        ``generic``. The most common phrase wins; a tie goes to the one met
        first, so the answer follows the pictures' order.
    """
    nouns = _PERSON_NOUNS if subject == "person" else None
    counts: Counter = Counter()
    for caption in captions:
        phrases = _phrases(caption or "", nouns)
        if nouns is None:
            # A thing is what the caption opens with; later phrases are its
            # surroundings ("a red bicycle ... against a wall").
            phrases = phrases[:1]
        # A picture counts once for each phrase it holds.
        counts.update(list(dict.fromkeys(phrases)))
    if counts:
        return counts.most_common(1)[0][0], "captions"

    tag_counts: Counter = Counter()
    for picture_tags in tags:
        names = {t.strip().lower() for t in picture_tags if t and t.strip()}
        if nouns is not None:
            names = {t for t in names if t.split()[-1] in nouns}
        tag_counts.update(sorted(names))
    if tag_counts:
        # ponytail: for a thing the most common tag is a guess at the subject
        # ("bicycle" or "outdoors"); a caption beats it whenever there is one.
        best = max(tag_counts.values())
        # Of the equally common, the longest names the subject best ("old
        # man" over "man"); then alphabetical, so the pick is stable.
        tag = min(
            (t for t, n in tag_counts.items() if n == best),
            key=lambda t: (-len(t.split()), t),
        )
        return _article(tag), "tags"
    return _GENERIC[subject], "generic"


def _seen(text: str, words: tuple[str, ...]) -> int:
    """How many of *words* start a word somewhere in *text*."""
    return sum(1 for w in words if re.search(rf"\b{re.escape(w)}", text))


def generate(subject: str, captions: list[str], tags: list[list[str]]) -> dict:
    """The default sample prompts for a run: two scenes, with and without.

    Args:
        subject: ``person`` / ``style`` / ``thing``.
        captions: One caption per training picture, in a stable order.
        tags: One tag list per training picture, in the same order.

    Returns:
        ``{"prompts", "description", "description_source"}``. ``prompts`` is
        four ``{"prompt", "with_trigger", "without_trigger"}`` in pairs, the
        one with the word first; ``prompt`` is the form the run uses, and the
        other is what clicking the word in the dialog turns it into.
        ``description`` and its source are None for a style.

    Raises:
        ValueError: An unknown subject.
    """
    if subject not in _SCENES:
        raise ValueError(f"subject must be one of {', '.join(_SCENES)}.")
    text = " ".join(
        [c.lower() for c in captions if c]
        + [t.lower() for picture_tags in tags for t in picture_tags if t]
    )
    # The scenes the dataset shows least, the list's order breaking ties: a
    # set that shows every scene still gets two.
    ranked = sorted(
        range(len(_SCENES[subject])),
        key=lambda i: (_seen(text, _SCENES[subject][i][1]), i),
    )
    scenes = [_SCENES[subject][i][0] for i in sorted(ranked[:SCENE_COUNT])]

    description = source = None
    if subject != "style":
        description, source = describe(subject, captions, tags)
    prompts = []
    for scene in scenes:
        if subject == "style":
            with_trigger, without = f"{scene}, {TRIGGER} style", scene
        else:
            with_trigger = scene.format(s=TRIGGER)
            without = scene.format(s=description)
        for prompt in (with_trigger, without):
            prompts.append(
                {
                    "prompt": prompt,
                    "with_trigger": with_trigger,
                    "without_trigger": without,
                }
            )
    return {
        "prompts": prompts,
        "description": description,
        "description_source": source,
    }


def prompt_texts(generated: dict) -> list[str]:
    """The prompt strings of a :func:`generate` result, as a run stores them."""
    return [item["prompt"] for item in generated["prompts"]]
