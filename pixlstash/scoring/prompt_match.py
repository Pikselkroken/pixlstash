"""Prompt match: does a picture look like what its prompt asked for, at all.

A sanity check for total failures (noise, a blank frame, mush from a broken
LoRA, a picture with nothing to do with its prompt). It is NOT a quality or
aesthetic score: a picture that passes is "recognisably about its prompt", never
"good", and nothing should present it as the latter.

**Method: the rank of the picture's own prompt among fixed distractors.** Raw
CLIP cosine (the quantity CLIPScore rescales, Hessel et al. 2021,
arXiv:2104.08718) is not comparable across prompts or across images: a long
detailed prompt sits at a different cosine from a short one, and some images
are near every text. So the score is the fraction of :data:`DISTRACTOR_PROMPTS`
that the picture's CLIP image embedding is LESS similar to than to its own
prompt. Both biases cancel, because every comparison shares the image and the
bank is one fixed set of texts. The bank includes texts describing the failures
themselves ("random noise", "a solid black image"), so a failed frame ranks
those above its prompt and lands near 0. Ties count half.

CLIP is a bag-of-words matcher on composition, counting and relations
(arXiv:2210.01936; VQAScore, arXiv:2404.01291), which is why the check is
deliberately coarse: it asks whether the prompt beats unrelated texts, not
whether every clause was honoured.
"""

from __future__ import annotations

import re
from typing import Optional

import numpy as np

#: The verdict's floor on :func:`prompt_match_score`. Set on the lenient side
#: because a false "does not look like its prompt" on a fine picture costs more
#: trust than a missed failure. Evidence, CLIP ViT-B-32 laion2b_s34b_b79k on a
#: local GPU over the 151 fixture images in ``pictures/`` and ``test-data/``
#: (2026-10-07): at 0.7, 0 of 459 matching pairs (6 real embedded prompts plus
#: Florence-2 captions as short, A1111-styled and long prompts) were flagged,
#: and 6.1% of 1031 failure pairs passed (noise, black, white, flat colour,
#: colour blobs, a prompt from an unrelated picture or about something else).
#: Not caught: failures that keep the subject visible (a heavy blur still passed
#: 68%, a fried/posterised copy 90%). Thin on real prompts; recalibrate against
#: the owner's ratings before reading much into the boundary.
PROMPT_MATCH_THRESHOLD = 0.7

#: Stored when a prompt exists but could not be scored (no text embedding, an
#: image embedding of the wrong size). Outside [0, 1], so it reads as "failed",
#: and the verdict for it is None, not False.
PROMPT_MATCH_FAILED = -1.0

#: Short, concrete, mutually unrelated texts spanning what generated pictures
#: are of, plus descriptions of the failure modes themselves. Fixed: changing
#: it changes every score, so a change here needs a migration that NULLs
#: ``picture.prompt_match`` (see 0128).
DISTRACTOR_PROMPTS: tuple[str, ...] = (
    # Failure modes. A failed frame matches these better than its prompt.
    "random colorful noise",
    "static noise pattern",
    "a solid black image",
    "a blank white image",
    "a flat grey image",
    "a blurry out of focus image",
    "a corrupted glitched image",
    "abstract smeared colors",
    # People.
    "a portrait of an old man",
    "a young woman smiling",
    "a child playing",
    "a group of people at a party",
    "a soldier in uniform",
    "a man in a business suit",
    "a woman in a red dress",
    "a baby sleeping",
    "an athlete running on a track",
    "a crowd at a concert",
    # Animals.
    "a cat sitting on a sofa",
    "a dog running on a beach",
    "a horse in a field",
    "a bird on a branch",
    "a fish in an aquarium",
    "an elephant in the savanna",
    "a butterfly on a flower",
    "a snake on a rock",
    "a herd of cows",
    "a penguin on ice",
    # Places and scenes.
    "a mountain landscape",
    "a city skyline at night",
    "a tropical beach",
    "a dense forest",
    "a desert with sand dunes",
    "a snowy village",
    "a busy street market",
    "an empty office",
    "a kitchen interior",
    "a library full of books",
    "an underwater coral reef",
    "outer space with stars and a planet",
    "a farm with a red barn",
    "a waterfall in a jungle",
    # Objects and vehicles.
    "a red sports car",
    "an airplane in the sky",
    "a sailing boat on the sea",
    "a steam locomotive",
    "a bicycle against a wall",
    "a bowl of fruit",
    "a plate of food",
    "a cup of coffee",
    "a laptop on a desk",
    "a vase of flowers",
    "a pair of shoes",
    "a wooden chair",
    "a clock on a wall",
    "a guitar",
    "a pile of coins",
    "a bottle of wine",
    # Kinds of picture.
    "a page of printed text",
    "a screenshot of a website",
    "a bar chart",
    "a map",
    "a cartoon character",
    "an anime girl",
    "a pencil sketch",
    "an oil painting of a landscape",
    "a logo on a white background",
    "a 3d render of a robot",
    "a pixel art game scene",
    "a medieval castle",
    "a futuristic spaceship",
    "a dragon",
    "a house in the suburbs",
    "a bridge over a river",
    "a church interior",
    "fireworks at night",
    "a stormy sky with lightning",
    "a close-up of a human eye",
)

# A1111 / Forge extra-network tags: <lora:name:0.8>, <lyco:...>, <hypernet:...>.
_EXTRA_NETWORK_RE = re.compile(r"<[^<>]*>")
# ComfyUI textual-inversion references: embedding:name, embedding:name:1.2.
_EMBEDDING_RE = re.compile(r"\bembedding:[^\s,()\[\]]+", re.IGNORECASE)
# The ":1.2" of an attention weight, ``(word:1.2)``, ``[word:0.5]``.
_WEIGHT_RE = re.compile(r":\s*-?(?:\d+\.?\d*|\.\d+)\s*(?=[)\]])")
# A1111 BREAK / composable-diffusion AND: chunk separators, not words.
_SEPARATOR_RE = re.compile(r"\b(?:BREAK|AND)\b")
_BRACKET_RE = re.compile(r"[()\[\]{}|\\]")
_COMMA_RUN_RE = re.compile(r"\s*,[\s,]*")
_SPACE_RE = re.compile(r"\s+")


def clean_prompt(prompt: Optional[str]) -> str:
    """The words a prompt asks for, without the syntax that steers the sampler.

    Drops LoRA / extra-network tags and ``embedding:`` references (file names,
    not words), attention weights and their brackets, and ``BREAK`` / ``AND``.
    Keeps every word otherwise, LoRA trigger words included: they are what the
    picture is meant to show. Returns "" when nothing is left.
    """
    if not prompt:
        return ""
    text = _EXTRA_NETWORK_RE.sub(" ", prompt)
    text = _EMBEDDING_RE.sub(" ", text)
    text = _WEIGHT_RE.sub("", text)
    text = _SEPARATOR_RE.sub(",", text)
    text = _BRACKET_RE.sub(" ", text)
    text = _COMMA_RUN_RE.sub(", ", text)
    text = _SPACE_RE.sub(" ", text)
    return text.strip(" ,")


def prompt_match_score(
    image_embedding: np.ndarray,
    prompt_embedding: np.ndarray,
    distractor_embeddings: np.ndarray,
) -> float:
    """Fraction of distractors the image is less similar to than its prompt.

    All three are CLIP embeddings from one model, passed as stored. The texts
    are normalised here; the image need not be, since scaling it scales every
    similarity alike and leaves the rank alone. Returns a value in [0, 1].
    """
    texts = np.vstack([prompt_embedding.ravel(), distractor_embeddings])
    texts = texts.astype(np.float32) / np.linalg.norm(texts, axis=1, keepdims=True)
    sims = texts @ image_embedding.astype(np.float32).ravel()
    prompt_sim, sims = sims[0], sims[1:]
    below = float(np.sum(sims < prompt_sim)) + 0.5 * float(np.sum(sims == prompt_sim))
    return below / len(sims)


def looks_like_prompt(score: Optional[float]) -> Optional[bool]:
    """The verdict for a stored score: None when there is no usable score."""
    if score is None or score < 0:
        return None
    return score >= PROMPT_MATCH_THRESHOLD
