"""Known base models - the folding table behind base-model tab-completion.

``model.base_model`` is **free text** and stays that way (see ``hub/schema.py``:
an enum would reject every model that ships after this release). This module
never constrains what can be stored. It does two jobs:

* **folds** an arriving string to a canonical label, so a caller that wants
  ``sdxl_base_v1-0``, ``SDXL``, ``sdxl base`` and ``stable diffusion xl`` to
  group as one rather than four can ask for that;
* **seeds tab-completion** with models we already know about, so the field is
  useful on a fresh install with an empty library.

**Nothing folds what is STORED.** Two callers read this module today and neither
writes through it: ``GET /adapters`` and ``GET /checkpoints`` carry
:func:`fold`'s answer beside the raw column as ``base_model_folded``, which is
where the shelf's grouping and faceting get their buckets, and
``GET /models/base-models`` serves :func:`completions` to the *Set base model*
field. The stored column stays free text either way, the shelf's *Base model*
sort is still ``m.base_model COLLATE NOCASE`` and its filter is still an exact
match on the raw column. Folding those two needs either a SQLite function
registered per hub connection or a canonical column the scanner maintains, and
that is still open work. Do not read this docstring as a description of the
whole shelf.

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

**Only an exact normalised match folds automatically.** Containment and
``difflib`` results are *offered* by :func:`suggest` and never applied, because
``flux`` is a substring of ``flux2`` and silent containment would file every
FLUX.2 adapter under FLUX.1. That split is the fold-and-ask guard; stdlib
``difflib`` is enough and no fuzzy-matching dependency is added.

**``family`` is architecture, the canonical name is compatibility.** A Pony V6
adapter *loads* on SDXL and produces mush; Pony V7 moved to AuraFlow, so a V6
adapter will not load on V7 at all despite the shared name. Grouping by name
gets both cases wrong, which is why family is stored rather than derived.
``modality`` keeps video bases out of an image-adapter filter.

This is code and not a table, on the same ruling made for the built-in tagger
models (``tagger_plugins/registry.py``): a declaration maintained beside the
parser that consumes it, which a database copy could only fall out of sync with.
Expect to append to it every few months.
"""

from __future__ import annotations

import difflib
import re
from typing import Iterable, Optional

# Canonical label -> {family, modality, aliases}.
#
# Aliases are matched normalised (see _norm), so DO NOT add spacing or casing
# variants - only strings that differ in their letters and digits.
KNOWN_BASE_MODELS: dict[str, dict] = {
    # --- Stable Diffusion line ---------------------------------------------
    "SD 1.5": {
        "family": "sd15",
        "modality": "image",
        "aliases": [
            "sd15",
            "sd v1-5",
            "stable diffusion 1.5",
            "runwayml/stable-diffusion-v1-5",
            "v1-5-pruned",
        ],
    },
    "SD 2.1": {
        "family": "sd21",
        "modality": "image",
        "aliases": [
            "sd21",
            "sd v2-1",
            "stable diffusion 2.1",
            "stabilityai/stable-diffusion-2-1",
        ],
    },
    "SDXL 1.0": {
        "family": "sdxl",
        "modality": "image",
        "aliases": [
            "sdxl",
            "sdxl base",
            "sdxl_base_v1-0",
            "sd_xl",
            "stable diffusion xl",
            "stabilityai/stable-diffusion-xl-base-1.0",
        ],
    },
    "SD 3.5": {
        "family": "sd35",
        "modality": "image",
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
        "aliases": [
            "flux2",
            "flux 2 dev",
            "flux.2 dev",
            "black-forest-labs/FLUX.2-dev",
        ],
    },
    "Chroma1 HD": {
        "family": "chroma",
        "modality": "image",
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
        "aliases": ["chroma1base", "lodestones/Chroma1-Base"],
    },
    "Chroma1 Radiance": {
        "family": "chroma",
        "modality": "image",
        "aliases": ["chroma radiance", "lodestones/Chroma1-Radiance"],
    },
    # --- Alibaba ------------------------------------------------------------
    "Z-Image Turbo": {
        "family": "zimage",
        "modality": "image",
        "aliases": ["zimageturbo", "Tongyi-MAI/Z-Image-Turbo"],
    },
    # Bare "zimage" resolves here, not to Turbo: the unqualified string most
    # often means the family, and Base is the fine-tuning target. Turbo has to
    # be asked for by name.
    "Z-Image Base": {
        "family": "zimage",
        "modality": "image",
        "aliases": ["zimagebase", "zimage", "Tongyi-MAI/Z-Image-Base"],
    },
    "Z-Image Edit": {
        "family": "zimage",
        "modality": "image",
        "aliases": ["zimageedit", "Tongyi-MAI/Z-Image-Edit"],
    },
    "Qwen-Image": {
        "family": "qwen",
        "modality": "image",
        "aliases": ["qwen", "qwenimage", "Qwen/Qwen-Image"],
    },
    "Qwen-Image-Edit": {
        "family": "qwen",
        "modality": "image",
        "aliases": ["qwenimageedit", "Qwen/Qwen-Image-Edit"],
    },
    "Wan 2.2": {
        "family": "wan",
        "modality": "video",
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
        "aliases": ["wan27", "wan2.7"],
    },
    # --- Krea ----------------------------------------------------------------
    "Krea 2 Raw": {
        "family": "krea2",
        "modality": "image",
        "aliases": ["krea2raw", "krea raw"],
    },
    "Krea 2 Turbo": {
        "family": "krea2",
        "modality": "image",
        "aliases": ["krea2turbo", "krea turbo"],
    },
    "Krea 2": {
        "family": "krea2",
        "modality": "image",
        "aliases": ["krea2", "krea"],
    },
    # --- Tencent --------------------------------------------------------------
    "HunyuanImage 3.0": {
        "family": "hunyuan_image",
        "modality": "image",
        "aliases": ["hunyuanimage", "hunyuanimage3"],
    },
    "HunyuanVideo 1.5": {
        "family": "hunyuan_video",
        "modality": "video",
        "aliases": ["hunyuanvideo", "hunyuan", "tencent/HunyuanVideo"],
    },
    # --- Lightricks -----------------------------------------------------------
    "LTX-2.3": {
        "family": "ltx2",
        "modality": "video",
        "aliases": ["ltx23", "ltxv2", "ltx2"],
    },
    "LTXV 13B": {
        "family": "ltxv",
        "modality": "video",
        "aliases": ["ltxv", "ltx video", "ltx", "Lightricks/LTX-Video"],
    },
    # --- SDXL-architecture community bases ------------------------------------
    # family='sdxl' is the load-compatibility fact; the canonical name is the
    # works-properly fact. Both are needed and neither implies the other.
    "Pony Diffusion V6 XL": {
        "family": "sdxl",
        "modality": "image",
        "aliases": ["pony", "ponyv6", "pony xl", "ponydiffusionv6xl"],
    },
    "Pony V7": {
        "family": "auraflow",
        "modality": "image",
        "aliases": ["ponyv7"],
    },
    "Illustrious XL": {
        "family": "sdxl",
        "modality": "image",
        "aliases": ["illustrious", "illustriousxl", "ilxl"],
    },
    "NoobAI-XL": {
        "family": "sdxl",
        "modality": "image",
        "aliases": ["noobai", "noobaixl"],
    },
    "Animagine XL": {
        "family": "sdxl",
        "modality": "image",
        "aliases": ["animagine", "animaginexl"],
    },
    # --- Other open bases ------------------------------------------------------
    "AuraFlow": {
        "family": "auraflow",
        "modality": "image",
        "aliases": ["auraflow", "fal/AuraFlow"],
    },
    "Sana": {
        "family": "sana",
        "modality": "image",
        "aliases": ["sana", "nvidia sana"],
    },
    "HiDream-I1": {
        "family": "hidream",
        "modality": "image",
        "aliases": ["hidream", "hidreami1"],
    },
    "Lumina-Image 2.0": {
        "family": "lumina",
        "modality": "image",
        "aliases": ["lumina", "luminaimage"],
    },
    "Kolors": {
        "family": "kolors",
        "modality": "image",
        "aliases": ["kolors", "kwai kolors"],
    },
    "PixArt-Sigma": {
        "family": "pixart",
        "modality": "image",
        "aliases": ["pixart", "pixartsigma"],
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


def suggest(raw: Optional[str], limit: int = 5) -> list[str]:
    """Canonical labels *raw* might mean. **Offer these; never apply them.**

    Containment first (``sdxl`` inside ``mymodel_sdxl_v3``), longest alias
    winning so ``flux2`` beats ``flux``, then a ``difflib`` pass for typos.
    Returns ``[]`` when :func:`fold` already had an exact answer - there is
    nothing to ask about.
    """
    if not raw:
        return []
    key = _norm(raw)
    if key in _ALIAS_INDEX:
        return []

    hits: list[str] = []
    # Longest alias first: 'flux2' must win over 'flux' for 'myflux2lora'.
    for alias in sorted(_ALIAS_INDEX, key=len, reverse=True):
        if alias and alias in key:
            canonical = _ALIAS_INDEX[alias]
            if canonical not in hits:
                hits.append(canonical)
        if len(hits) >= limit:
            return hits

    for alias in difflib.get_close_matches(key, _ALIAS_INDEX, n=limit, cutoff=0.8):
        canonical = _ALIAS_INDEX[alias]
        if canonical not in hits:
            hits.append(canonical)
    return hits[:limit]


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
