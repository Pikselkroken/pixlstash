"""Which PixlStash feature a cached model powers, and when to admit we do not know.

The shelf labels a model by **the feature it powers**, not by its file format and
not by its ML task. That is the one of the three a person can answer questions
about: nobody who switched captioning on thinks they have an ``image-to-text``
model, and ``checkpoints/`` is a directory rather than a capability. Machine
identity stays underneath for interop — the same split the shelf already makes
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

**One label per row, for now.** A model can genuinely serve several features —
the laion CLIP repos are both the search embedder and the aesthetic scorer's
backbone — and the design calls for such a model to appear under each. That
needs somewhere to put a list, which ``model.kind`` is not.

ponytail: single label per row, keyed on `model.kind`; a `model_capability`
join table is the upgrade when the shelf starts listing capabilities per row.
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
FEATURE_CHECKPOINT = "checkpoint"
FEATURE_OTHER = "other"

# Repo ids PixlStash's own code fetches, and what each is for. Restated rather
# than imported for the reason `builtin_models` restates filenames: joycaption,
# florence2 and wd14 import torch and onnxruntime at module level, and this runs
# at start-up. `tests/test_builtin_models.py` imports the real constants, where
# the cost is free, and asserts the two agree.
OUR_REPOS: dict[str, str] = {
    # `joycaption._MODEL_NAME`
    "fancyfeast/llama-joycaption-beta-one-hf-llava": FEATURE_CAPTIONER,
    # `florence2.FLORENCE_MODEL_VARIANTS[*]["model"]`. One setting drives both
    # captioning and Segment, so the row says captioner and the multi-capability
    # listing is the deferred piece above.
    "florence-community/Florence-2-base": FEATURE_CAPTIONER,
    "florence-community/Florence-2-large-ft": FEATURE_CAPTIONER,
    # `wd14.WD14_HF_REPO`
    "SmilingWolf/wd-convnext-tagger-v3": FEATURE_TAGGER,
    # `pixlstash_tagger.PIXLSTASH_TAGGER_HF_REPO`
    "PersonalJeebus/pixlvault-anomaly-tagger": FEATURE_TAGGER,
    # `insightface_model_utils._AURAFACE_REPO`
    "fal/AuraFace-v1": FEATURE_FACE,
    # `sbert.SBERT_MODEL_NAME`, which resolves under this org.
    "sentence-transformers/all-MiniLM-L6-v2": FEATURE_SEARCH,
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
# alone labelled `google/flan-t5-base` and `google/t5-v1_1-xxl` "Captioning" —
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
    that run — and a repo can hold a complete revision beside a half-downloaded
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
    mid-download, or whose config is not JSON, simply does not answer — which is
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


def feature_for_repo(repo) -> str:
    """The PixlStash feature a cached HuggingFace repo powers.

    Args:
        repo: A ``CachedRepoInfo`` from ``scan_cache_dir()``.

    Returns:
        One of the ``FEATURE_*`` values. ``other`` when nothing above answers,
        which is a truthful state and not a failure.
    """
    repo_id = str(getattr(repo, "repo_id", "") or "")
    ours = OUR_REPOS.get(repo_id)
    if ours:
        return ours

    if repo_id.lower() in _base_model_aliases():
        return FEATURE_CHECKPOINT

    for snapshot in _snapshot_dirs(repo):
        found = _feature_from_files(snapshot)
        if found:
            return found

    lowered = repo_id.lower()
    for needle, feature in _NAME_HINTS:
        if needle in lowered:
            return feature
    return FEATURE_OTHER
