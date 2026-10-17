"""Known base models - the folding table behind base-model tab-completion.

``model.base_model`` is **free text** and stays that way (see ``hub/schema.py``:
an enum would reject every model that ships after this release). This module
never constrains what can be stored. It does two jobs:

* **folds** an arriving string to a canonical label, so a caller that wants
  ``sdxl_base_v1-0``, ``SDXL``, ``sdxl base`` and ``stable diffusion xl`` to
  group as one rather than four can ask for that;
* **seeds tab-completion** with models we already know about, so the field is
  useful on a fresh install with an empty library.

**The shelf stores what this module identified, beside the raw column.**
:func:`identify` matches a file's evidence - the base model its metadata
declares, and its filename - against this table, and the scanner writes the
answer to ``model.base_model_canonical`` with where it came from in
``model.base_model_source``. The shelf's *Base model* sort and filter run on
``COALESCE(base_model_canonical, base_model)``. ``base_model`` itself is never
rewritten: it is the trainer's own string and the evidence a better matcher in
a later release would want to re-read. ``GET /adapters`` and ``GET
/checkpoints`` still carry :func:`fold`'s answer as ``base_model_folded`` for a
row nothing has identified yet, and ``GET /models/base-models`` serves
:func:`completions` to the *Set base model* field.

An unrecognised string is not an error. It is stored verbatim, displayed
verbatim, and - the moment it lands on a ``model`` row - becomes a completion
target like any other. That is why there is no user-base-model table: the user's
own list is ``SELECT DISTINCT base_model FROM model``, which is already written
by the time it matters. Callers pass it to :func:`completions` as *extra*.

**Spacing and case are normalised away, never enumerated.** :func:`_norm` maps
``Z-Image Turbo``, ``z image turbo``, ``Z_Image_Turbo``, ``ZIMAGETURBO`` and
``z.image.turbo`` onto one key, so the alias lists below carry only genuinely
different strings: Civitai ``baseModel`` labels, kohya ``ss_base_model_version``
values, HuggingFace repo ids and vendor codenames. Adding a spacing or case
variant to an alias list means the normaliser was bypassed somewhere.

**Evidence is ranked by quality, not by where it came from.** An exact
normalised match from any source beats a fuzzy one from any source; within a
tier the file's declared metadata beats its filename (:data:`SOURCE_RANK`). A
person's value (``user``) outranks every scan, so nothing here clobbers it.

**Containment for filenames, edit distance for declared values, never the
reverse.** A filename is a soup of tokens (``ilxl_example_v3_fp16-000012``) where
only containment means anything; a declared value is one person's attempt at
one name, where a typo is plausible. Containment is safe to apply only under
two guards: the **longest alias wins**, because ``flux`` is a substring of
``flux2`` and the shorter would file every FLUX.2 adapter under FLUX.1, and an
alias shorter than :data:`_MIN_CONTAINED_ALIAS` never matches by containment
at all, so ``mj`` and ``sd3`` do not fire off an incidental substring, and nor
does an alias that is an ordinary word (:data:`_NOT_CONTAINED`: ``pony`` in
``ponytail``), which only counts as a whole filename token. A fuzzy
answer is still an answer the shelf marks as guessed. Stdlib ``difflib`` is
enough at this table's size; ``rapidfuzz`` would buy speed nobody waits on.

**``family`` is architecture, the canonical name is compatibility.** A Pony V6
adapter *loads* on SDXL and produces mush; Pony V7 moved to AuraFlow, so a V6
adapter will not load on V7 at all despite the shared name. Grouping by name
gets both cases wrong, which is why family is stored rather than derived.
``modality`` keeps a clone's companion proposals from crossing from image to
video (or back), and lets the clone dialog say a LoRA was trained for the
other kind of model.

This is code and not a table, on the same ruling made for the built-in tagger
models (``tagger_plugins/registry.py``): a declaration maintained beside the
parser that consumes it, which a database copy could only fall out of sync with.

**Maintaining it.** The table grows with PixlStash releases, never from what
sits on one machine's disk: review it before cutting a release
(``docs/release-test-plan.md``) and add what has shipped since. A new entry
carries a canonical label a person would recognise, its ``family`` (load
compatibility, which is not derivable from the name - see Pony V6 vs V7 above),
its ``modality``, and only aliases that differ in letters or digits. Every new
entry or alias is user-visible, because files already on a shelf are identified
by it on their next scan, so it gets a ``changelog.d/`` fragment. An alias two
entries claim stops the server booting (:func:`_build_index`), by design.
"""

from __future__ import annotations

import difflib
import functools
import re
from typing import Iterable, Optional

from pixlstash.utils.model_utils import derive_model_name

# Canonical label -> {family, modality, aliases} and the tile it draws.
#
# Aliases are matched normalised (see _norm), so DO NOT add spacing or casing
# variants - only strings that differ in their letters and digits.
#
# The tile a model with no icon wears on a workflow card (:func:`model_tile`):
# ``mark`` is the short name people write for the model, on one or two lines
# read together (SD over 3.5 reads SD3.5), acronyms in capitals and words in
# title case; ``role`` is the tab that tells same-named releases apart (BASE,
# REFI, EDIT, RAW, HD); ``few_step`` puts a bolt on the tab instead;
# ``file_version`` takes the second line from the filename (Qwen 2511);
# ``maker`` picks the tile colour from :data:`MAKERS` and ``icon`` a family
# icon from :data:`FAMILY_ICONS`. Every ``mark`` line must fit the card's 30px
# tile's 26px line at 8.25px, which ``frontend/e2e/specs/model-tile-names.spec.js``
# measures in Chromium; a fine-tune's own second line is not measured.
# Closed models carry none: they never exist as files.
KNOWN_BASE_MODELS: dict[str, dict] = {
    # --- Stable Diffusion line ---------------------------------------------
    "SD 1.5": {
        "family": "sd15",
        "modality": "image",
        "mark": ["SD", "1.5"],
        "maker": "Stability AI",
        "aliases": [
            "sd15",
            "sd v1-5",
            "stable diffusion 1.5",
            "runwayml/stable-diffusion-v1-5",
            "v1-5-pruned",
            "stable-diffusion-v1",
            "sd_v1",
        ],
    },
    "SD 2.1": {
        "family": "sd21",
        "modality": "image",
        "mark": ["SD", "2.1"],
        "maker": "Stability AI",
        "aliases": [
            "sd21",
            "sd v2-1",
            "stable diffusion 2.1",
            "stabilityai/stable-diffusion-2-1",
            "stable-diffusion-v2-512",
            "stable-diffusion-v2-768-v",
            "sd_v2",
        ],
    },
    "SDXL 1.0": {
        "family": "sdxl",
        "modality": "image",
        "mark": ["SDXL", "1.0"],
        "maker": "Stability AI",
        "aliases": [
            "sdxl",
            "sdxl base",
            "sdxl_base_v1-0",
            "sd_xl",
            "stable diffusion xl",
            "stabilityai/stable-diffusion-xl-base-1.0",
            "stable-diffusion-xl-v1-base",
        ],
    },
    "SD 3.5": {
        "family": "sd35",
        "modality": "image",
        "mark": ["SD", "3.5"],
        "maker": "Stability AI",
        "aliases": [
            "sd35",
            "sd3.5",
            "sd3",
            "stable diffusion 3.5",
            "stabilityai/stable-diffusion-3.5-large",
        ],
    },
    # --- FLUX and descendants ----------------------------------------------
    "FLUX.1 dev": {
        "family": "flux1",
        "modality": "image",
        "mark": ["F1D"],
        "maker": "Black Forest Labs",
        "aliases": [
            "flux1d",
            "flux.1 d",
            "flux dev",
            "flux1",
            "black-forest-labs/FLUX.1-dev",
        ],
    },
    "FLUX.1 schnell": {
        "family": "flux1",
        "modality": "image",
        "mark": ["F1S"],
        "few_step": True,
        "maker": "Black Forest Labs",
        "aliases": [
            "flux1s",
            "flux.1 s",
            "flux schnell",
            "black-forest-labs/FLUX.1-schnell",
        ],
    },
    "FLUX.2": {
        "family": "flux2",
        "modality": "image",
        "mark": ["Flux", "2"],
        "maker": "Black Forest Labs",
        "aliases": [
            "flux2",
            "flux 2 dev",
            "flux.2 dev",
            "black-forest-labs/FLUX.2-dev",
        ],
    },
    # The small FLUX.2s: a 4-step distilled build and an undistilled base, at
    # 4B and 9B. Named apart for the tile, but in FLUX.2's family: a family
    # is part of a workflow's identity, and Klein runs have always been flux2.
    "FLUX.2 Klein": {
        "family": "flux2",
        "modality": "image",
        "mark": ["Flux", "Klein"],
        "few_step": True,
        "maker": "Black Forest Labs",
        "aliases": [
            "flux2klein",
            "black-forest-labs/FLUX.2-klein-4B",
            "black-forest-labs/FLUX.2-klein-9B",
        ],
    },
    "Chroma1 HD": {
        "family": "chroma",
        "modality": "image",
        "mark": ["CHRO"],
        "role": "HD",
        "icon": "chroma",
        "aliases": [
            "chroma",
            "chroma1",
            "chroma hd",
            "chroma unlocked",
            "lodestones/Chroma1-HD",
        ],
    },
    "Chroma1 Base": {
        "family": "chroma",
        "modality": "image",
        "mark": ["CHRO"],
        "role": "BASE",
        "icon": "chroma",
        "aliases": ["chroma1base", "lodestones/Chroma1-Base"],
    },
    "Chroma1 Radiance": {
        "family": "chroma",
        "modality": "image",
        "mark": ["CHRO"],
        "icon": "chroma",
        "aliases": ["chroma radiance", "lodestones/Chroma1-Radiance"],
    },
    # --- Alibaba ------------------------------------------------------------
    "Z-Image Turbo": {
        "family": "zimage",
        "modality": "image",
        "mark": ["ZIT"],
        "few_step": True,
        "maker": "Alibaba",
        # `zit`: the community's short name, matched only as a whole filename
        # token (shorter than `_MIN_CONTAINED_ALIAS`), as in `clementine-zit`.
        "aliases": ["zimageturbo", "zit", "Tongyi-MAI/Z-Image-Turbo"],
    },
    # Bare "zimage" resolves here, not to Turbo: the unqualified string most
    # often means the family, and Base is the fine-tuning target. Turbo has to
    # be asked for by name.
    "Z-Image Base": {
        "family": "zimage",
        "modality": "image",
        "mark": ["ZIB"],
        "maker": "Alibaba",
        "aliases": ["zimagebase", "zimage", "zib", "Tongyi-MAI/Z-Image-Base"],
    },
    "Z-Image Edit": {
        "family": "zimage",
        "modality": "image",
        "mark": ["ZI"],
        "role": "EDIT",
        "maker": "Alibaba",
        "aliases": ["zimageedit", "Tongyi-MAI/Z-Image-Edit"],
    },
    "Qwen-Image": {
        "family": "qwen",
        "modality": "image",
        "mark": ["Qwen"],
        "file_version": True,
        "maker": "Qwen",
        "aliases": ["qwen", "qwenimage", "Qwen/Qwen-Image"],
    },
    "Qwen-Image-Edit": {
        "family": "qwen",
        "modality": "image",
        "mark": ["Qwen"],
        "role": "EDIT",
        "file_version": True,
        "maker": "Qwen",
        "aliases": ["qwenimageedit", "Qwen/Qwen-Image-Edit"],
    },
    "Wan 2.2": {
        "family": "wan",
        "modality": "video",
        "mark": ["Wan", "2.2"],
        "maker": "Alibaba",
        "aliases": [
            "wan22",
            "wan2.2",
            "wan video",
            "wan video 14b",
            "Wan-AI/Wan2.2-T2V-A14B",
        ],
    },
    "Wan 2.7": {
        "family": "wan",
        "modality": "video",
        "mark": ["Wan", "2.7"],
        "maker": "Alibaba",
        "aliases": ["wan27", "wan2.7"],
    },
    # --- Krea ----------------------------------------------------------------
    "Krea 2 Raw": {
        "family": "krea2",
        "modality": "image",
        "mark": ["Krea", "2"],
        "role": "RAW",
        "maker": "Krea",
        "aliases": ["krea2raw", "krea raw"],
    },
    "Krea 2 Turbo": {
        "family": "krea2",
        "modality": "image",
        "mark": ["Krea", "2"],
        "few_step": True,
        "maker": "Krea",
        "aliases": ["krea2turbo", "krea turbo"],
    },
    "Krea 2": {
        "family": "krea2",
        "modality": "image",
        "mark": ["Krea", "2"],
        "maker": "Krea",
        "aliases": ["krea2", "krea"],
    },
    # --- Tencent --------------------------------------------------------------
    "HunyuanImage 3.0": {
        "family": "hunyuan_image",
        "modality": "image",
        "mark": ["HUNY", "3.0"],
        "maker": "Tencent Hunyuan",
        "aliases": ["hunyuanimage", "hunyuanimage3"],
    },
    "HunyuanVideo 1.5": {
        "family": "hunyuan_video",
        "modality": "video",
        "mark": ["HUNY", "1.5"],
        "maker": "Tencent Hunyuan",
        "aliases": ["hunyuanvideo", "hunyuan", "tencent/HunyuanVideo"],
    },
    # --- Lightricks -----------------------------------------------------------
    "LTX-2.3": {
        "family": "ltx2",
        "modality": "video",
        "mark": ["LTXV", "2.3"],
        "maker": "Lightricks",
        "aliases": ["ltx23", "ltxv2", "ltx2"],
    },
    "LTXV 13B": {
        "family": "ltxv",
        "modality": "video",
        "mark": ["LTXV", "13B"],
        "maker": "Lightricks",
        "aliases": ["ltxv", "ltx video", "ltx", "Lightricks/LTX-Video"],
    },
    # --- MiniMax --------------------------------------------------------------
    "MiniMax H3": {
        "family": "minimax_h3",
        "modality": "video",
        "mark": ["MINI", "H3"],
        "aliases": [],
    },
    # --- SDXL-architecture community bases ------------------------------------
    # family='sdxl' is the load-compatibility fact; the canonical name is the
    # works-properly fact. Both are needed and neither implies the other.
    "Pony Diffusion V6 XL": {
        "family": "sdxl",
        "modality": "image",
        "mark": ["PDXL"],
        "icon": "pony",
        "aliases": ["pony", "ponyv6", "pony xl", "ponydiffusionv6xl"],
    },
    "Pony V7": {
        "family": "auraflow",
        "modality": "image",
        "mark": ["Pony"],
        "role": "V7",
        "icon": "pony",
        "aliases": ["ponyv7"],
    },
    "Illustrious XL": {
        "family": "sdxl",
        "modality": "image",
        "mark": ["ILXL"],
        "icon": "illustrious",
        "aliases": ["illustrious", "illustriousxl", "ilxl"],
    },
    "NoobAI-XL": {
        "family": "sdxl",
        "modality": "image",
        "mark": ["Noob"],
        "aliases": ["noobai", "noobaixl"],
    },
    "Animagine XL": {
        "family": "sdxl",
        "modality": "image",
        "mark": ["ANIM"],
        "aliases": ["animagine", "animaginexl"],
    },
    # --- Other open bases ------------------------------------------------------
    "AuraFlow": {
        "family": "auraflow",
        "modality": "image",
        "mark": ["Aura"],
        "maker": "fal",
        "aliases": ["auraflow", "fal/AuraFlow"],
    },
    "Sana": {
        "family": "sana",
        "modality": "image",
        "mark": ["Sana"],
        "maker": "NVIDIA",
        "aliases": ["sana", "nvidia sana"],
    },
    "HiDream-I1": {
        "family": "hidream",
        "modality": "image",
        "mark": ["HIDR", "I1"],
        "aliases": ["hidream", "hidreami1"],
    },
    "Lumina-Image 2.0": {
        "family": "lumina",
        "modality": "image",
        "mark": ["LUMI", "2.0"],
        "aliases": ["lumina", "luminaimage"],
    },
    "Kolors": {
        "family": "kolors",
        "modality": "image",
        "mark": ["KOLO"],
        "maker": "Kuaishou Kolors",
        "aliases": ["kolors", "kwai kolors"],
    },
    "PixArt-Sigma": {
        "family": "pixart",
        "modality": "image",
        "mark": ["PIXA"],
        "aliases": ["pixart", "pixartsigma"],
    },
    # ByteDance's video and image restoration model, loaded by its own nodes
    # rather than a checkpoint loader.
    "SeedVR2": {
        "family": "seedvr2",
        "modality": "video",
        "mark": ["Seed", "VR2"],
        "aliases": [
            "seedvr2",
            "ByteDance-Seed/SeedVR2-3B",
            "ByteDance-Seed/SeedVR2-7B",
        ],
    },
    # Ideogram's one open-weights release (its own fp8 and nf4 files, a
    # Comfy-Org repackage, LoRAs). Ideogram 3.0 below is cloud-only.
    "Ideogram 4": {
        "family": "ideogram",
        "modality": "image",
        "mark": ["IDEO", "4"],
        "maker": "Ideogram",
        "aliases": ["ideogram4", "ideogram v4"],
    },
    # --- Closed / API-only -----------------------------------------------------
    # Recorded so an image can be tagged with its origin. family='closed' is the
    # marker for "never trained against locally": nothing attaches an adapter to
    # one of these, and UI that implies local loading must filter them out.
    "GPT Image 2": {
        "family": "closed",
        "modality": "image",
        "aliases": ["gptimage2", "gpt-image"],
    },
    "Nano Banana Pro": {
        "family": "closed",
        "modality": "image",
        "aliases": ["nanobananapro", "nano banana", "nanobanana2"],
    },
    "Midjourney V8.1": {
        "family": "closed",
        "modality": "image",
        "aliases": ["midjourney", "mj", "mjv8"],
    },
    "Ideogram 3.0": {
        "family": "closed",
        "modality": "image",
        "aliases": ["ideogram", "ideogram3"],
    },
    "Imagen 4": {
        "family": "closed",
        "modality": "image",
        "aliases": ["imagen", "imagen4"],
    },
    "Seedream 4.5": {
        "family": "closed",
        "modality": "image",
        "aliases": ["seedream", "seedream45"],
    },
    "MAI-Image 2.5": {
        "family": "closed",
        "modality": "image",
        "aliases": ["maiimage", "mai image"],
    },
}


# Architecture family -> the support-file layouts it loads, per support kind.
#
# The one bridge between the two ``family`` vocabularies: the base-model
# families above and the tensor layouts `adapter_header.family_from_header`
# stores on a VAE or text encoder (``vae_4ch``, ``vae_16ch``, ``clip_l``,
# ``clip_h``, ``clip_g``, ``t5_xxl``, ``umt5_xxl``). It is a **declaration, not
# evidence**: a layout that fits says the file will load, not that it was made
# for this family (SD 1.5's and SDXL's VAEs share ``vae_4ch``, FLUX's and SD
# 3.5's share ``vae_16ch``, and neither pair is interchangeable), so `propose_companions` reaches for it only when no recipe
# answers and labels what it proposes as coming from here.
#
# Only what that vocabulary can say, and only what is certain. A family whose
# companion has no recognised layout (FLUX.2, Qwen-Image, Krea 2, the Qwen and
# Gemma encoders, the 3D video VAEs) declares that kind not at all rather than
# something close, Chroma declares no VAE because Radiance needs none, and Wan
# is left out until every release in its family is known to load UMT5-XXL.
COMPANION_LAYOUTS: dict[str, dict[str, frozenset[str]]] = {
    "sd15": {"vae": frozenset({"vae_4ch"}), "text_encoder": frozenset({"clip_l"})},
    "sd21": {"vae": frozenset({"vae_4ch"}), "text_encoder": frozenset({"clip_h"})},
    "sdxl": {
        "vae": frozenset({"vae_4ch"}),
        "text_encoder": frozenset({"clip_l", "clip_g"}),
    },
    "sd35": {
        "vae": frozenset({"vae_16ch"}),
        "text_encoder": frozenset({"clip_l", "clip_g", "t5_xxl"}),
    },
    "flux1": {
        "vae": frozenset({"vae_16ch"}),
        "text_encoder": frozenset({"clip_l", "t5_xxl"}),
    },
    "chroma": {"text_encoder": frozenset({"t5_xxl"})},
    "zimage": {"vae": frozenset({"vae_16ch"})},
    "hidream": {
        "vae": frozenset({"vae_16ch"}),
        "text_encoder": frozenset({"clip_l", "clip_g", "t5_xxl"}),
    },
    "pixart": {"vae": frozenset({"vae_4ch"}), "text_encoder": frozenset({"t5_xxl"})},
    "auraflow": {"vae": frozenset({"vae_4ch"})},
    "kolors": {"vae": frozenset({"vae_4ch"})},
}

# Family -> what a person calls it. What a list narrowed by family shows, so
# "Krea 2" stands for Krea 2, Krea 2 Raw and Krea 2 Turbo alike.
FAMILY_LABELS: dict[str, str] = {
    "sd15": "SD 1.5",
    "sd21": "SD 2.1",
    "sdxl": "SDXL",
    "sd35": "SD 3.5",
    "flux1": "FLUX.1",
    "flux2": "FLUX.2",
    "chroma": "Chroma",
    "zimage": "Z-Image",
    "qwen": "Qwen-Image",
    "wan": "Wan",
    "krea2": "Krea 2",
    "hunyuan_image": "HunyuanImage",
    "hunyuan_video": "HunyuanVideo",
    "ltx2": "LTX-2",
    "ltxv": "LTXV",
    "auraflow": "AuraFlow",
    "sana": "Sana",
    "hidream": "HiDream",
    "lumina": "Lumina",
    "kolors": "Kolors",
    "pixart": "PixArt",
    "ideogram": "Ideogram 4",
    "seedvr2": "SeedVR2",
    "minimax_h3": "MiniMax H3",
    "closed": "Closed models",
}


def _norm(value: str) -> str:
    """Fold case, spacing and punctuation away. The whole spacing axis dies here."""
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _build_index() -> dict[str, str]:
    """normalised alias -> canonical label.

    A collision is the one way this module can silently corrupt the shelf - two
    entries claiming one alias would make folding depend on dict order - so it
    raises at import rather than picking a winner.
    """
    index: dict[str, str] = {}
    for canonical, info in KNOWN_BASE_MODELS.items():
        for candidate in (canonical, *info["aliases"]):
            key = _norm(candidate)
            existing = index.get(key)
            if existing is not None and existing != canonical:
                raise ValueError(
                    f"alias collision: {candidate!r} claimed by both "
                    f"{existing!r} and {canonical!r}"
                )
            index[key] = canonical
    return index


_ALIAS_INDEX = _build_index()


def fold(raw: Optional[str]) -> Optional[str]:
    """Return the canonical label for *raw*, or ``None`` if we do not know it.

    Exact-normalised only, and therefore safe to apply without asking. Anything
    less certain belongs in :func:`suggest`.
    """
    if not raw:
        return None
    return _ALIAS_INDEX.get(_norm(raw))


def family_of(raw: Optional[str]) -> Optional[str]:
    """Return the architecture family *raw* folds to, or ``None``.

    Exact-normalised through :func:`fold`, so it is as safe to apply as a fold.
    """
    label = fold(raw)
    return KNOWN_BASE_MODELS[label]["family"] if label else None


def modality_of(raw: Optional[str]) -> Optional[str]:
    """Return ``"image"`` or ``"video"`` for what *raw* folds to, or ``None``."""
    label = fold(raw)
    return KNOWN_BASE_MODELS[label]["modality"] if label else None


def family_label(family: Optional[str]) -> Optional[str]:
    """What a person calls *family* (``krea2`` -> ``Krea 2``), or ``None``."""
    return FAMILY_LABELS.get(family) if family else None


_FAMILY_BY_LABEL = {_norm(label): family for family, label in FAMILY_LABELS.items()}


def family_named(value: Optional[str]) -> Optional[str]:
    """The family *value* names: a family, its label, or any base model in it.

    So a filter given ``Krea 2``, ``krea2`` or ``Krea 2 Turbo`` narrows to the
    same family. ``None`` when it names none.
    """
    if not value:
        return None
    if value in FAMILY_LABELS:
        return value
    return _FAMILY_BY_LABEL.get(_norm(value)) or family_of(value)


# Where a stored canonical label came from, highest first. The scanner writes a
# new answer only when its source outranks the stored one, within one scan and
# across scans alike, so a rescan can upgrade a filename guess to a declared
# match and nothing it finds can replace what a person typed.
SOURCE_USER = "user"
SOURCE_DECLARED = "declared"
SOURCE_FILENAME = "filename"
SOURCE_DECLARED_FUZZY = "declared_fuzzy"
SOURCE_FILENAME_FUZZY = "filename_fuzzy"

SOURCE_RANK = {
    SOURCE_USER: 5,
    SOURCE_DECLARED: 4,
    SOURCE_FILENAME: 3,
    SOURCE_DECLARED_FUZZY: 2,
    SOURCE_FILENAME_FUZZY: 1,
}

# An alias shorter than this never matches by containment: `mj` and `sd3` are
# incidental substrings of too many filenames to mean anything inside one.
_MIN_CONTAINED_ALIAS = 4

# Aliases long enough for that floor that are still ordinary words or word
# pieces (`ponytail`, `sanae`, `illumination`), so they never match INSIDE a
# filename word. They still match as a whole filename token, and as declared
# metadata. Normalised keys. Distinctive names such as `qwen` and `hunyuan`
# are deliberately not here.
_NOT_CONTAINED = frozenset({"pony", "sana", "lumina", "krea", "chroma"})

# How close a declared value has to be to an alias to be read as a typo of it.
# Applied, not offered, so it is stricter than :func:`suggest`'s cutoff.
_DECLARED_FUZZY_CUTOFF = 0.88
_SUGGEST_CUTOFF = 0.8

# A declared value can carry the file's own type after a slash
# (`stable-diffusion-xl-v1-base/lora`); only the part before it names a model.
_ARCHITECTURE_SUFFIX_RE = re.compile(
    r"/(?:lora|lycoris|lokr|loha|dora|adapter|textual-inversion|ti|"
    r"controlnet|control)\s*$",
    re.IGNORECASE,
)

# What a filename is split on before its tokens are folded one by one.
_FILENAME_SEPARATORS_RE = re.compile(r"[\s_.\-]+")

# Alias keys, longest first, so containment tries `flux2` before anything it
# contains. Built once: the table is fixed for the life of the process.
_ALIASES_LONGEST_FIRST = sorted(_ALIAS_INDEX, key=len, reverse=True)


def rank(source: Optional[str]) -> int:
    """How much a stored source is worth; ``0`` for none or an unknown one."""
    return SOURCE_RANK.get(source or "", 0)


def _contained(
    key: str, min_alias: int, skip: frozenset[str] = frozenset()
) -> list[str]:
    """Canonical labels whose alias occurs inside *key*, longest alias first.

    Aliases in *skip* are not tried.
    """
    hits: list[str] = []
    for alias in _ALIASES_LONGEST_FIRST:
        if len(alias) < min_alias:
            # Sorted longest first, so every alias after this one is shorter.
            break
        if alias in key and alias not in skip:
            canonical = _ALIAS_INDEX[alias]
            if canonical not in hits:
                hits.append(canonical)
    return hits


def _scored(key: str, cutoff: float) -> list[tuple[float, str]]:
    """``(similarity, canonical label)`` at or above *cutoff*, closest first.

    One entry per label, at its closest alias. Ties keep table order, so the
    caller can see a tie rather than have ``difflib`` break it silently.
    """
    best: dict[str, float] = {}
    for alias, canonical in _ALIAS_INDEX.items():
        ratio = difflib.SequenceMatcher(None, key, alias).ratio()
        if ratio >= cutoff and ratio > best.get(canonical, 0.0):
            best[canonical] = ratio
    return sorted(((r, c) for c, r in best.items()), key=lambda hit: -hit[0])


def _stem(filename: str) -> str:
    """A filename without its directory and extension."""
    base = re.split(r"[\\/]", filename)[-1]
    return base.rsplit(".", 1)[0] if "." in base else base


def suggest(raw: Optional[str], limit: int = 5) -> list[str]:
    """Canonical labels *raw* might mean, for a person to choose between.

    Containment first (``sdxl`` inside ``mymodel_sdxl_v3``), longest alias
    winning so ``flux2`` beats ``flux``, then a ``difflib`` pass for typos. The
    same two operators :func:`identify` applies, looser: any alias length and a
    lower cutoff, because a person reads these before anything is kept.
    Returns ``[]`` when :func:`fold` already had an exact answer - there is
    nothing to ask about.
    """
    if not raw:
        return []
    key = _norm(raw)
    if key in _ALIAS_INDEX:
        return []
    hits = _contained(key, 1)[:limit]
    # Then labels an alias of which is within edit distance of *key*.
    for _ratio, canonical in _scored(key, _SUGGEST_CUTOFF)[:limit]:
        if canonical not in hits:
            hits.append(canonical)
    return hits[:limit]


def identify(
    declared: Iterable[Optional[str]], filenames: Iterable[Optional[str]]
) -> tuple[Optional[str], Optional[str]]:
    """The base model a file's evidence names, and which evidence named it.

    *declared* is what the file's metadata says it was trained against
    (``ss_base_model_version``, ``modelspec.architecture``), best first.
    *filenames* is the file's own name and any filename its metadata records
    (``ss_sd_model_name``). Tried in :data:`SOURCE_RANK` order, so an exact
    match on a filename token beats a fuzzy match on a declared value:

    1. a declared value folds exactly - ``declared``;
    2. the whole stem, or one token of it, folds exactly - ``filename``;
    3. a declared value is within edit distance of an alias -
       ``declared_fuzzy``;
    4. an alias of at least :data:`_MIN_CONTAINED_ALIAS` characters occurs in a
       filename, longest alias first - ``filename_fuzzy``.

    A ``closed`` base (Midjourney, Imagen) is never an answer: nothing is
    trained against one locally, so a filename token ``mj`` is somebody's
    abbreviation rather than a base model.

    Returns:
        ``(canonical label, source)``, or ``(None, None)`` when nothing
        matched. No answer is written as nothing, so the next release's table
        reaches the file by itself.
    """
    values = [_ARCHITECTURE_SUFFIX_RE.sub("", str(v)).strip() for v in declared if v]
    values = [v for v in values if v]
    stems = [_stem(str(f)) for f in filenames if f]
    stems = [s for s in stems if s]

    for value in values:
        label = fold(value)
        if _local(label):
            return label, SOURCE_DECLARED
    for stem in stems:
        if _local(fold(stem)):
            return fold(stem), SOURCE_FILENAME
        # The longest token that names a base model, not the first: in
        # `sdxl_illustrious_char` the specific base is the one that says more.
        tokens = [t for t in _FILENAME_SEPARATORS_RE.split(stem) if _local(fold(t))]
        if tokens:
            return fold(max(tokens, key=lambda t: len(_norm(t)))), SOURCE_FILENAME
    for value in values:
        key = _norm(value)
        hits = [h for h in _scored(key, _DECLARED_FUZZY_CUTOFF) if _local(h[1])]
        # Two different bases equally close is not a typo of either: a
        # declared `flux` is as near `flux1` as `flux2`, and picking one would
        # be a coin toss the shelf then presents as an answer.
        if key and hits and (len(hits) == 1 or hits[1][0] < hits[0][0]):
            return hits[0][1], SOURCE_DECLARED_FUZZY
    for stem in stems:
        hits = [
            h
            for h in _contained(_norm(stem), _MIN_CONTAINED_ALIAS, _NOT_CONTAINED)
            if _local(h)
        ]
        if hits:
            return hits[0], SOURCE_FILENAME_FUZZY
    return None, None


def _local(label: Optional[str]) -> bool:
    """Whether *label* is a base a local file can have been made against."""
    return bool(label) and KNOWN_BASE_MODELS[label]["family"] != "closed"


def completions(prefix: str = "", extra: Iterable[str] = ()) -> list[str]:
    """Tab-completion targets for the base-model field.

    *extra* is the user's own vocabulary - pass ``SELECT DISTINCT base_model
    FROM model``. Values that fold to something we already know are dropped
    rather than shown twice, so a user who typed ``sdxl`` sees ``SDXL 1.0``
    once; anything that folds to nothing is theirs and is offered verbatim from
    the moment it was saved.

    Prefix matches sort ahead of substring matches, each group alphabetically,
    so typing narrows predictably instead of reshuffling.
    """
    targets = list(KNOWN_BASE_MODELS)
    seen = {_norm(t) for t in targets}
    for value in extra:
        if not value or not value.strip():
            continue
        if fold(value) is not None:
            continue
        key = _norm(value)
        if key and key not in seen:
            seen.add(key)
            targets.append(value.strip())

    key = _norm(prefix)
    if not key:
        return sorted(targets, key=str.casefold)

    starts = sorted((t for t in targets if _norm(t).startswith(key)), key=str.casefold)
    contains = sorted(
        (t for t in targets if key in _norm(t) and not _norm(t).startswith(key)),
        key=str.casefold,
    )
    return starts + contains


# --- Tiles -------------------------------------------------------------------

# Maker -> the tile's colour stops: each brand colour from @lobehub/icons
# 6.0.0, kept at its hue and saturation and darkened only until white text on
# it clears 4:1 (a gradient's stops one by one). Makers whose marks are black
# and white share one graphite; the name tells them apart. A family with no
# maker here keeps the hashed hue every other mark takes.
_GRAPHITE = "#484c53"
MAKERS: dict[str, list[str]] = {
    "Stability AI": ["#9d39ff", "#e80000"],
    "Black Forest Labs": [_GRAPHITE],
    "Qwen": ["#615ced"],
    "Alibaba": ["#dc5100"],
    "Tencent Hunyuan": ["#0053e0"],
    "Lightricks": [_GRAPHITE],
    "Krea": [_GRAPHITE],
    "Kuaishou Kolors": ["#6c8700", "#418e07"],
    "NVIDIA": ["#598d15"],
    "fal": ["#ec0648"],
    "Ideogram": [_GRAPHITE],
}

# The owner's family icons (``frontend/src/assets/model-families/``) -> the
# field they are drawn on. Only the base checkpoint wears the icon; its LoRAs
# and fine-tunes are text tiles in this colour, or every Chroma LoRA would
# draw one identical eye. Illustrious's is a step lighter than its picture's
# near-black field, so a LoRA tile keeps its edge on the dark theme.
FAMILY_ICONS: dict[str, str] = {
    "chroma": "#7a3b12",
    "pony": "#4b2373",
    "illustrious": "#0c183e",
}

# Fine-tunes with a mark of their own, by how their name starts (normalised)
# -> the tile. Juggernaut XL's logo is a J on this purple.
KNOWN_FINE_TUNES: dict[str, dict] = {
    "juggernaut": {"mark": ["J"], "colors": ["#6b46c1"]},
}

# Filename words that say which of several same-named releases a checkpoint
# is. REFI, not REF, which reads as "reference". A role word nobody listed
# shows no tab.
ROLE_WORDS = {"base": "BASE", "refiner": "REFI"}

# What the tab of a few-step model or speed LoRA shows, followed by the step
# count a speed LoRA is named for (``⚡4``).
FEW_STEP_TAB = "⚡"
_SPEED_WORDS = frozenset({"lightning", "hyper", "lcm", "dmd2", "turbo"})
_STEPS_RE = re.compile(r"(?<![a-z0-9])(\d+)[\s_-]?steps?(?![a-z])", re.IGNORECASE)

# Words a release's filename carries besides its name, role and version.
_PACKAGING_WORDS = frozenset(
    {
        *("pruned", "emaonly", "ema", "noema", "nonema", "vae", "bakedvae"),
        *("fixed", "full", "dev", "t2v", "i2v", "ti2v", "high", "low", "noise"),
        *("convrot", "mixed", "scaled", "fast", "awq", "gptq"),
    }
)
# A precision anywhere in a name (`seedvr2_7b_int8_convrot`), where the shelf's
# own quant parser only strips one at the end.
_QUANT_WORD_RE = re.compile(
    r"(?:bf|fp|int|nvfp|mxfp|nf|e\dm|i?q)\d\w*|[fi](?:4|8|16|32)", re.IGNORECASE
)

# Words that say what kind of file it is rather than which, so never a
# LoRA's own initials (`my_style_lora` is MS, not ML).
_KIND_WORDS = frozenset({"lora", "lycoris", "locon", "loha", "lokr", "dora"})

# Words that join two others in a name, so "World of Wordcraft" is WW. The
# shelf's own list (``MARK_FILLER_WORDS`` in ``frontend/src/utils/modelShelf.js``).
_FILLER_WORDS = frozenset(
    {"of", "in", "on", "at", "by", "for", "from", "to", "with", "the", "and"}
)


@functools.cache
def _vocabulary(canonical: str) -> frozenset[str]:
    """Every name of the base model's family, and each word and number in one.

    The family's and not the model's alone, so ``zimage`` is a base word in a
    Z-Image Turbo LoRA's name although only Z-Image Base claims it.
    """
    family = KNOWN_BASE_MODELS[canonical]["family"]
    names = [
        name
        for label, info in KNOWN_BASE_MODELS.items()
        if info["family"] == family
        for name in (label, *info["aliases"])
    ]
    return frozenset(
        word for name in names for word in re.findall(r"[a-z]+|\d+", name.casefold())
    ) | {_norm(name) for name in names}


@functools.cache
def _vocabulary_pattern(vocabulary: frozenset[str]) -> re.Pattern:
    """Any run of *vocabulary* words and digits, longest word tried first."""
    words = sorted(vocabulary, key=len, reverse=True)
    return re.compile("(?:" + "|".join(map(re.escape, words)) + r"|\d+)*")


def _leftover_is(text: str, vocabulary: frozenset[str]) -> bool:
    """Whether *text* (normalised) is nothing but *vocabulary* words and digits."""
    return _vocabulary_pattern(vocabulary).fullmatch(text) is not None


def _is_release(canonical: str, tokens: list[str], vocabulary: frozenset[str]) -> bool:
    """Whether a checkpoint's name *tokens* are the base model's own release.

    The name must open with one of the base model's aliases, and everything
    after it be a role, a speed word, a version or packaging:
    ``sd xl refiner 1.0`` and ``flux1 dev`` are releases, ``sdxlUnstable
    Diffusers v11`` and ``juggernautXL v9`` are fine-tunes. A token opening
    with ``v`` and a digit or a capital is a version and its label
    (``v6StartWithThisOne``).
    """
    # ponytail: a fine-tune named exactly "<base alias> <version>" reads as the
    # release; a per-file list of real releases if that ever bites.
    normed = [_norm(token) for token in tokens]
    keys = {
        _norm(name) for name in (canonical, *KNOWN_BASE_MODELS[canonical]["aliases"])
    }
    for alias in sorted(keys, key=len, reverse=True):
        joined = ""
        for i, token in enumerate(normed):
            joined += token
            if len(joined) < len(alias):
                continue
            if not joined.startswith(alias):
                break
            if not _leftover_is(joined[len(alias) :], vocabulary):
                break
            rest = tokens[i + 1 :]
            if all(
                re.match(r"\d|v(?:\d|[A-Z])", token)
                or _norm(token) in vocabulary
                or _norm(token) in ROLE_WORDS
                or _norm(token) in _SPEED_WORDS
                or _norm(token) in _PACKAGING_WORDS
                or _QUANT_WORD_RE.fullmatch(token)
                for token in rest
            ):
                return True
            break
    return False


def _initials(words: list[str]) -> str:
    """Two letters for a name, as the shelf's ``initialsOf`` takes them.

    The first letters of the first two words that open with a letter, or the
    first two letters of the only one; a ``v1.0`` is a version, not a word.
    ``""`` when no word has a letter.
    """
    significant = [
        word
        for index, word in enumerate(words)
        if index == 0 or word.casefold() not in _FILLER_WORDS
    ]
    words = significant if len(significant) > 1 else words
    lettered = [
        word
        for word in words
        if word[:1].isalpha() and not re.match(r"v\d", word, re.I)
    ]
    if len(lettered) > 1:
        return (lettered[0][0] + lettered[1][0]).upper()
    if lettered:
        return "".join(ch for ch in lettered[0] if ch.isalpha())[:2].upper()
    return ""


def _own_line(name: str, vocabulary: frozenset[str]) -> Optional[str]:
    """A fine-tune's or LoRA's second line: its own initials and version.

    Words of the base model's own names are not its own (``Juggernaut XL v9``
    is JU9, ``Qwen-Image-Edit-2511-Lightning-4steps`` is LI), nor is a role,
    packaging or file-kind word (``lora``), nor a long number (a base's
    ``2511``). The version is a ``v<n>``'s
    major, left off at 1 because a first release says nothing.
    """
    words = re.split(r"[\s_-]+", name)
    # A name run together with its base's (`sdxlUnstableDiffusers`) is its own
    # from where the base's ends.
    if words and words[0]:
        base = max(
            (key for key in vocabulary if words[0].casefold().startswith(key)),
            key=len,
            default="",
        )
        if len(base) > 2 and words[0][len(base) :].isalpha():
            words[0] = words[0][len(base) :]
    words = [
        word
        for word in words
        if word
        and _norm(word) not in vocabulary
        and _norm(word) not in ROLE_WORDS
        and _norm(word) not in _PACKAGING_WORDS
        and not _QUANT_WORD_RE.fullmatch(word)
        and _norm(word) not in _KIND_WORDS
        and not re.fullmatch(r"\d{3,}", word)
    ]
    letters = _initials(words)
    if not letters:
        return None
    version = next(
        (
            m.group(1)
            for w in words
            if (m := re.fullmatch(r"v(\d+)(?:\.\d+)?", w, re.I))
        ),
        None,
    )
    return letters + (version[:2] if version and version != "1" else "")


def model_tile(
    canonical: Optional[str],
    filename: Optional[str],
    name: Optional[str] = None,
    checkpoint: bool = False,
) -> Optional[dict]:
    """The tile a workflow card draws a model with, or ``None`` for the generated one.

    *canonical* is the known base model the row was identified as, *name* the
    shelf's own name for it (else its filename's), and *checkpoint* whether it
    is one; a LoRA never wears its base model's role or icon.

    Returns ``{"mark", "mark_tab", "mark_colors", "mark_icon"}``: the one or
    two lines of the name; the tab (a role, or :data:`FEW_STEP_TAB` with a
    speed LoRA's step count), or ``None``; the colour stops, or ``None`` for
    the client's hashed hue; and the family icon a base checkpoint wears.

    * **The base model's own release** shows its ``mark`` (SDXL over 1.0),
      the role word in its filename or else its entry's ``role`` on the tab.
    * **A fine-tune or LoRA** shows the base's first line over its own
      initials and version (SDXL over JU9), or its own mark where
      :data:`KNOWN_FINE_TUNES` has one. It is none of the base's releases, so
      its tab is only a role word of its own name.
    """
    tokens = derive_model_name(filename or "").split()
    own_name = name or " ".join(tokens)
    words = [_norm(word) for word in re.split(r"[\s_-]+", own_name) if word]
    # Whatever base model it was identified as, or none: a fine-tune with a
    # mark of its own is known by its name.
    fine_tune = next(
        (
            tile
            for start, tile in KNOWN_FINE_TUNES.items()
            if checkpoint and words and words[0].startswith(start)
        ),
        None,
    )
    entry = KNOWN_BASE_MODELS.get(canonical or "", {})
    if "mark" not in entry and not fine_tune:
        return None
    vocabulary = _vocabulary(canonical) if "mark" in entry else frozenset()
    release = bool(
        checkpoint and not fine_tune and _is_release(canonical, tokens, vocabulary)
    )
    icon = entry.get("icon")
    colors = [FAMILY_ICONS[icon]] if icon else MAKERS.get(entry.get("maker", ""))
    if fine_tune:
        mark = list(fine_tune["mark"])
        colors = fine_tune["colors"]
    elif release:
        mark = list(entry["mark"])
        if entry.get("file_version"):
            version = next(
                (t for t in tokens if re.fullmatch(r"\d{4}|\d+\.\d+", t)), None
            )
            mark += [version] if version else []
    else:
        own = _own_line(own_name, vocabulary)
        mark = [entry["mark"][0]] + ([own] if own else [])

    tab = None
    own_words = {_norm(t) for t in tokens} | set(words)
    if checkpoint:
        label_words = set(re.findall(r"[a-z]+", (canonical or "").casefold()))
        # The entry's role tells the base's own releases apart, so only a
        # release wears it; a role word in the file's own name is its own.
        tab = (entry.get("role") if release else None) or next(
            (
                ROLE_WORDS[word]
                for word in own_words
                if word in ROLE_WORDS and word not in label_words
            ),
            None,
        )
    if tab is None:
        speed = (checkpoint and entry.get("few_step")) or any(
            word in _SPEED_WORDS for word in own_words - vocabulary
        )
        if speed:
            steps = _STEPS_RE.search(own_name)
            tab = FEW_STEP_TAB + (steps.group(1) if steps else "")
    return {
        "mark": mark,
        "mark_tab": tab,
        "mark_colors": list(colors) if colors else None,
        "mark_icon": icon if release else None,
    }
