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
            "stable-diffusion-v1",
            "sd_v1",
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
            "stable-diffusion-v2-512",
            "stable-diffusion-v2-768-v",
            "sd_v2",
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
            "stable-diffusion-xl-v1-base",
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


# Architecture family -> the support-file layouts it loads, per support kind.
#
# The one bridge between the two ``family`` vocabularies: the base-model
# families above and the tensor layouts `adapter_header.family_from_header`
# stores on a VAE or text encoder (``vae_4ch``, ``vae_16ch``, ``clip_l``,
# ``clip_h``, ``clip_g``, ``t5_xxl``, ``umt5_xxl``). It is a **declaration, not
# evidence**: a layout that fits says the file will load, not that it was made
# for this family (SD 1.5's and SDXL's VAEs share a layout and are not
# interchangeable), so `propose_companions` reaches for it only when no recipe
# answers and labels what it proposes as coming from here.
#
# Only what that vocabulary can say, and only what is certain. A family whose
# companion has no recognised layout (FLUX.2, Qwen-Image, Krea 2, the Qwen and
# Gemma encoders, the 3D video VAEs) declares that kind not at all rather than
# something close, and Chroma declares no VAE because Radiance needs none.
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
    "wan": {"text_encoder": frozenset({"umt5_xxl"})},
    "hidream": {
        "vae": frozenset({"vae_16ch"}),
        "text_encoder": frozenset({"clip_l", "clip_g", "t5_xxl"}),
    },
    "pixart": {"vae": frozenset({"vae_4ch"}), "text_encoder": frozenset({"t5_xxl"})},
    "auraflow": {"vae": frozenset({"vae_4ch"})},
    "kolors": {"vae": frozenset({"vae_4ch"})},
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


def _close(key: str, cutoff: float, limit: int) -> list[str]:
    """Canonical labels an alias of which is within edit distance of *key*."""
    return [canonical for _ratio, canonical in _scored(key, cutoff)[:limit]]


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
    for canonical in _close(key, _SUGGEST_CUTOFF, limit):
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
