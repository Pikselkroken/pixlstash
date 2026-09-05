"""Which PixlStash feature a cached model powers, and when to admit we do not know.

The shelf labels a model by **the feature it powers**, not by its file format and
not by its ML task. That is the one of the three a person can answer questions
about: nobody who switched captioning on thinks they have an ``image-to-text``
model, and ``checkpoints/`` is a directory rather than a capability. Machine
identity stays underneath for interop - the same split the shelf already makes
between filename and display name, and between sha256 and a friendly name.

**Three sources of truth, then an honest shrug.** Measured against a real 26-repo
cache, the sources answer 5 outright and the file inspection most of the rest;
about a quarter of a working cache is not a feature-model at all and gets
``other``:

1. **Repos our own downloaders name.** A fact, not a guess.
2. **The shipped known-base-models table.** 43 curated entries, already used to
   fold a base model out of a filename, and a repo id in it is a base model.
3. **The files in the snapshot.** ``model_index.json`` means a diffusers
   pipeline; ``config.json`` names the architecture class. Local, already on
   disk, and it describes what the thing *is* rather than what its name suggests.
4. **Otherwise ``other``**, and this is the part that matters. A VAE, a T5 text
   encoder and a BERT are components of somebody else's pipeline, not models
   that power a PixlStash feature, and the label set has no honest row for them.
   Forcing one would put a confident wrong word in the column a reader uses to
   decide what is safe to delete. "We know our own manifest; we do not know what
   else you put here" is the same epistemics as ``unclaimed_files``.

**A model can serve several features, and says so.** Florence-2 both captions
and detects; the CLIP the embedder loads is both the search encoder and the
aesthetic scorer's backbone. Filing either under one heading answers "what
breaks if I delete this" wrongly, which is the question the column exists for,
so :func:`features_for_repo` returns the whole set and the shelf lists the model
under each. The set lives in the ``model_capability`` join table.

``model.kind`` still holds the **first** of them, and only that. It is the
adapter-algorithm column, it carries a CHECK that says so, and every existing
reader - the Kind column, the curation verbs - was written against one string.
The first entry is therefore the one a reader sees when they see only one, which
is why these tuples are ordered by what the model is *primarily* for.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Optional

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.known_base_models import KNOWN_BASE_MODELS

logger = get_logger(__name__)

# The vocabulary. Machine words here; the screen's labels ("Captioning",
# "Tagging") are the frontend's, because a label is a thing a designer changes
# and a stored value is not.
FEATURE_CAPTIONER = "captioner"
FEATURE_TAGGER = "tagger"
FEATURE_FACE = "face"
FEATURE_SEARCH = "search"
FEATURE_SCORER = "scorer"
# `DetectionTask` / `InferenceEngine.detect_objects`, which is Florence-2's
# `<OD>` and grounding heads - the same weights that caption, which is the
# worked example this module's multi-capability set exists for.
FEATURE_DETECTOR = "detector"
FEATURE_CHECKPOINT = "checkpoint"
FEATURE_OTHER = "other"

# Repo ids PixlStash's own code fetches, and what each is for. Restated rather
# than imported for the reason `builtin_models` restates filenames: joycaption,
# florence2 and wd14 import torch and onnxruntime at module level, and this runs
# at start-up. `tests/test_builtin_models.py` imports the real constants, where
# the cost is free, and asserts the two agree.
OUR_REPOS: dict[str, tuple[str, ...]] = {
    # `joycaption._MODEL_NAME`
    "fancyfeast/llama-joycaption-beta-one-hf-llava": (FEATURE_CAPTIONER,),
    # `florence2.FLORENCE_MODEL_VARIANTS[*]["model"]`. ONE setting and one set of
    # weights drive two features: `FlorenceService.get_captions` and
    # `.detect_objects`, the latter being what `DetectionTask` runs. Deleting
    # this repo takes both with it, so the row has to say both.
    "florence-community/Florence-2-base": (FEATURE_CAPTIONER, FEATURE_DETECTOR),
    "florence-community/Florence-2-large-ft": (FEATURE_CAPTIONER, FEATURE_DETECTOR),
    # `wd14.WD14_HF_REPO`
    "SmilingWolf/wd-convnext-tagger-v3": (FEATURE_TAGGER,),
    # `pixlstash_tagger.PIXLSTASH_TAGGER_HF_REPO`
    "PersonalJeebus/pixlvault-anomaly-tagger": (FEATURE_TAGGER,),
    # `insightface_model_utils._AURAFACE_REPO`
    "fal/AuraFace-v1": (FEATURE_FACE,),
    # `sbert.SBERT_MODEL_NAME`, which resolves under this org.
    "sentence-transformers/all-MiniLM-L6-v2": (FEATURE_SEARCH,),
    # What open_clip fetches for `clip_service.CLIP_MODEL_NAME` /
    # `CLIP_MODEL_WEIGHTS` ("ViT-B-32" / "laion2b_s34b_b79k"), and the second
    # worked example. `ImageEmbeddingTask` runs ONE forward pass through these
    # weights and uses the result twice: it is written as the search embedding
    # AND fed to the aesthetic predictor. `BUILTIN_ENGINES` already declares the
    # predictor itself as a `scorer`, but that is a 4 MB linear head - the
    # ~600 MB a reader is actually deciding about is this repo, and deleting it
    # stops search and quality scores together while leaving a scorer on the
    # shelf that has nothing left to score.
    #
    # Named here and not inferred from the CLIP architecture hint below, which
    # stays `search` alone: some *other* cached CLIP is somebody else's encoder
    # and is not this scorer's backbone. `tests/test_builtin_models.py` pins the
    # id against those two constants so a model switch cannot leave it stale.
    "laion/CLIP-ViT-B-32-laion2B-s34B-b79K": (FEATURE_SEARCH, FEATURE_SCORER),
}

# Architecture class -> feature, read out of `config.json`. Substring matched
# because the classes are versioned (`Qwen2_5_VLForConditionalGeneration`) and
# pinning exact names would go stale on every model release.
_ARCHITECTURE_HINTS: tuple[tuple[str, str], ...] = (
    ("visionencoderdecoder", FEATURE_CAPTIONER),
    ("blip", FEATURE_CAPTIONER),
    # The full class name, never the bare "git": that substring also matches
    # "digit" and "logit" and would label an image classifier a captioner.
    ("gitforcausallm", FEATURE_CAPTIONER),
    # CLIP and friends embed; that is what the search index is built from.
    ("clipmodel", FEATURE_SEARCH),
    ("clipvision", FEATURE_SEARCH),
    ("siglip", FEATURE_SEARCH),
    # A bare image classifier with no generation head is a tagger.
    ("forimageclassification", FEATURE_TAGGER),
)

# `…ForConditionalGeneration` is the trap. It is the class of every
# vision-language captioner AND of `T5ForConditionalGeneration`, so matching it
# alone labelled `google/flan-t5-base` and `google/t5-v1_1-xxl` "Captioning" -
# a text encoder that captions nothing, stated confidently, in the column a
# reader uses to decide what is safe to delete. It only counts when the config
# also describes a vision tower.
_GENERATION_HINT = "forconditionalgeneration"

# Last resort, and weaker evidence than a config on purpose: these repos ship no
# `config.json` at all. The open_clip ones carry only `open_clip_*.safetensors`
# and `openai/clip-vit-large-patch14` here holds just its tokenizer, so nothing
# in the snapshot can identify them. The family name is distinctive enough that
# "this is an embedder" is a safer claim than `other`.
_NAME_HINTS: tuple[tuple[str, str], ...] = (
    ("clip", FEATURE_SEARCH),
    ("tagger", FEATURE_TAGGER),
    ("joytag", FEATURE_TAGGER),
)

# Files that identify a repo without opening a model. Both are small JSON.
_PIPELINE_MARKER = "model_index.json"
_CONFIG_MARKER = "config.json"


@lru_cache(maxsize=1)
def _base_model_aliases() -> frozenset[str]:
    """Every HuggingFace repo id the known-base-models table already names.

    Cached: `KNOWN_BASE_MODELS` is a shipped constant, and the caller runs once
    per cached repo at start-up, so rebuilding the set per repo is 43 entries
    walked for nothing on every one of them.
    """
    aliases = set()
    for meta in KNOWN_BASE_MODELS.values():
        for alias in meta.get("aliases", ()):  # type: ignore[union-attr]
            if "/" in alias:
                aliases.add(alias.lower())
    return frozenset(aliases)


def _snapshot_dirs(repo) -> list[str]:
    """Every readable snapshot directory for the repo.

    **All of them, sorted, not the first one.** ``repo.revisions`` is a
    *frozenset*, so "the first directory" is whatever order the set iterated in
    that run - and a repo can hold a complete revision beside a half-downloaded
    one that has the tokenizer but no ``config.json``. Picking one at random
    made ``Salesforce/blip-image-captioning-base`` classify as ``other`` on one
    run and ``captioner`` on the next, off the same disk.
    """
    paths = []
    try:
        for revision in repo.revisions:
            path = str(revision.snapshot_path)
            if os.path.isdir(path):
                paths.append(path)
    except (AttributeError, OSError) as exc:
        logger.debug(
            "No readable snapshot for %s (%s); it will be classified by name only.",
            getattr(repo, "repo_id", "?"),
            exc,
        )
    return sorted(paths)


def _feature_from_files(snapshot: str) -> Optional[str]:
    """What the snapshot's own metadata says this is, or None.

    Reads at most two small JSON files and never a weight. A repo that is
    mid-download, or whose config is not JSON, simply does not answer - which is
    a shrug and not an error, because ``other`` is a real state here.
    """
    if os.path.isfile(os.path.join(snapshot, _PIPELINE_MARKER)):
        # `model_index.json` is what `DiffusionPipeline.from_pretrained` reads,
        # so its presence means a runnable image pipeline rather than a part.
        return FEATURE_CHECKPOINT

    config = os.path.join(snapshot, _CONFIG_MARKER)
    if not os.path.isfile(config):
        return None
    try:
        with open(config, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        logger.info(
            "Could not read %s (%s); this snapshot answers nothing, so the repo "
            "falls through to its other revisions and then to its name, and is "
            "labelled `other` if neither answers rather than guessed at.",
            config,
            exc,
        )
        return None

    names = " ".join(str(a) for a in data.get("architectures") or ()).lower()
    names += " " + str(data.get("model_type") or "").lower()
    for needle, feature in _ARCHITECTURE_HINTS:
        if needle in names:
            return feature
    if _GENERATION_HINT in names and data.get("vision_config"):
        return FEATURE_CAPTIONER
    return None


def features_for_repo(repo) -> tuple[str, ...]:
    """Every PixlStash feature a cached HuggingFace repo powers.

    The four sources answer in order and the **first one that answers wins
    outright**: they are ranked by how much they know, so a repo our own
    downloader named is not then also guessed at from its architecture. Only
    ``OUR_REPOS`` returns more than one capability today, because it is the only
    source that knows what PixlStash actually does with the weights - a
    ``config.json`` describes an architecture, and an architecture cannot say
    whether this machine's copy backs one feature or two.

    Args:
        repo: A ``CachedRepoInfo`` from ``scan_cache_dir()``.

    Returns:
        Ordered, non-empty, primary first. ``(other,)`` when nothing answers,
        which is a truthful state and not a failure.
    """
    repo_id = str(getattr(repo, "repo_id", "") or "")
    ours = OUR_REPOS.get(repo_id)
    if ours:
        return ours

    if repo_id.lower() in _base_model_aliases():
        return (FEATURE_CHECKPOINT,)

    for snapshot in _snapshot_dirs(repo):
        found = _feature_from_files(snapshot)
        if found:
            return (found,)

    lowered = repo_id.lower()
    for needle, feature in _NAME_HINTS:
        if needle in lowered:
            return (feature,)
    return (FEATURE_OTHER,)


def feature_for_repo(repo) -> str:
    """The one feature to show when there is room for one word.

    The first of :func:`features_for_repo`, which is what lands in
    ``model.kind``. Kept as its own function because "the primary label" is a
    distinct question from "everything this serves", and the callers that want
    one string should not each be reaching into a tuple for element zero.
    """
    return features_for_repo(repo)[0]
