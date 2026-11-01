"""Training recipes (presets) and the resolver that fits one to this machine.

A preset is a versioned JSON file in ``pixlstash/data/training-presets/``, one
per model family and use. It holds every value a run needs, so the owner never
reads an ai-toolkit config:

- ``locked``: compatibility keys (architecture, checkpoints, adapter, LoRA
  exclusions, scheduler). Never on :data:`TUNABLE_KEYS`, so neither a table nor
  the owner can change them.
- ``defaults`` and ``preview[inference_target]``: what the model trains and
  samples with.
- ``subject`` (person / style / thing), ``effort`` (gentle / standard /
  strong) and ``fit`` (auto / save memory / best quality): the dialog's three
  choices.
- ``hardware_profiles``: memory tiers, each with a VRAM and RAM need, the
  largest bucket, rank and batch the need holds for, the settings it writes and
  the evidence behind its figures. A tier with evidence ``none`` is never
  offered.

:func:`resolve` turns the choices, the picture count and the memory the card and
RAM can give a run into one value per tunable key, each with where it came from.
It picks the least offloading that fits within the fit choice and never lowers
resolution, rank or steps to make a run fit; when nothing fits it says which
budget is short. Keys are the dotted :data:`TUNABLE_KEYS` names, relative to
``config.process[0]``.

A newer version of a shipped recipe can be downloaded from pixlstash.dev
(:mod:`pixlstash.training.preset_updates`) into :func:`use_cache_dir`. It
replaces the shipped one only while it passes :func:`validate_update`: the
same family, the same ``locked`` table (model checkpoints, adapter and
compatibility keys), a higher version, and no key outside the schema.
"""

from __future__ import annotations

import json
import math
import os
import re
from functools import lru_cache
from typing import Callable

from pixlstash.pixl_logging import get_logger
from pixlstash.training import measurements
from pixlstash.training.config_render import (
    TUNABLE_KEYS,
    TunableOverrideError,
    check_value,
)

logger = get_logger(__name__)

PRESET_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "training-presets")
)
TEMPLATE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "ai-toolkit-jobs", "built-in")
)
DEFAULT_PRESET = "zimage-turbo"

SUBJECTS = ("person", "style", "thing")
EFFORTS = ("gentle", "standard", "strong")
FITS = ("auto", "save_memory", "best_quality")
EVIDENCE = ("measured", "reported", "derived", "none")

PROMPTS_KEY = "sample.samples[*].prompt"
#: Keeping the description generic (``sample_prompts.preservation``).
PRESERVATION_KEY = "train.diff_output_preservation"
PRESERVATION_CLASS_KEY = "train.diff_output_preservation_class"
_MAX_PROMPTS = 16
SAMPLING_KEY = "train.disable_sampling"
#: The preview size the Previews question offers (#2415), when drawing at it is
#: what lets the fastest setup keep its previews while training.
SMALLER_PREVIEW = 768
#: A choice is offered only when it finishes this much sooner, as a share of
#: the default's time and in seconds both: noise between two equal-speed
#: profiles is neither.
CHOICE_MIN_SHARE = 0.1
CHOICE_MIN_SECONDS = 300
#: Under this much VRAM left free, a card reads "Tight on this GPU".
TIGHT_MB = 512
#: Keys that only say where the model sits, not what the LoRA learns.
_PLACEMENT_KEYS = frozenset(
    {
        "model.low_vram",
        "model.layer_offloading",
        "model.layer_offloading_transformer_percent",
        "model.layer_offloading_text_encoder_percent",
        "train.cache_text_embeddings",
        SAMPLING_KEY,
    }
)
EFFORT_STEP_KEYS = ("steps_per_picture", "min_steps", "max_steps")
FIELDS = (
    "id",
    "version",
    "label",
    "requires_arch",
    "template",
    "divisibility",
    "locked",
    "defaults",
    "inference_target",
    "preview",
    "subject",
    "effort",
    "fit",
    "hardware_profiles",
    "sources",
)
# Keys a recipe may leave out. ``min_ai_toolkit``: the oldest ai-toolkit version
# (``version.py``) that can train it, when its model folder alone does not say.
OPTIONAL_FIELDS = ("min_ai_toolkit",)
PROFILE_FIELDS = (
    "label",
    "vram_mb",
    "ram_mb",
    "max_bucket",
    "max_rank",
    "max_batch",
    "evidence",
    "settings",
)
_PROFILE_NUMBERS = ("vram_mb", "ram_mb", "max_bucket", "max_rank", "max_batch")
# What a downloaded recipe may not change: the family and its model sources.
UPDATE_FIXED = ("id", "requires_arch", "template", "divisibility", "locked")

# Where downloaded recipes are kept (beside the hub); None reads shipped only.
_cache_dir: str | None = None


class PresetError(ValueError):
    """A preset file breaks the schema."""


class NoProfileFits(ValueError):
    """No hardware profile of the fit choice fits this machine.

    Attributes:
        budget: ``"vram"``, ``"ram"``, or ``"profile"`` when no profile with
            evidence covers the size, rank, batch or pinned memory settings.
        need_mb: What the closest profile needs.
        free_mb: What this machine can give it.
    """

    def __init__(self, message: str, budget: str, need_mb: int, free_mb: int):
        super().__init__(message)
        self.budget = budget
        self.need_mb = need_mb
        self.free_mb = free_mb


def check_tunable(key: str, value) -> None:
    """Raise :class:`TunableOverrideError` unless *key* may be set to *value*.

    The prompt list is the one flat key holding a list of tunable leaves: each
    prompt is checked against the ``sample.samples[*].prompt`` rule.
    """
    rule = TUNABLE_KEYS.get(key)
    if rule is None:
        raise TunableOverrideError(f"'{key}' is not a tunable training parameter.")
    if key == PROMPTS_KEY:
        if not isinstance(value, list) or not 1 <= len(value) <= _MAX_PROMPTS:
            raise TunableOverrideError(
                f"'{key}' must be a list of 1 to {_MAX_PROMPTS} prompts."
            )
        for prompt in value:
            check_value(rule, key, prompt)
        return
    check_value(rule, key, value)


def _check_table(preset_id: str, where: str, table: dict) -> None:
    for key, value in table.items():
        try:
            check_tunable(key, value)
        except TunableOverrideError as exc:
            raise PresetError(f"{preset_id}: {where}: {exc}") from exc


def validate_preset(preset: dict) -> dict:
    """Check a preset against the schema and return it.

    Raises:
        PresetError: Naming the first broken part.
    """
    pid = preset.get("id") or "?"
    for field in FIELDS:
        if field not in preset:
            raise PresetError(f"{pid}: missing '{field}'.")
    if not isinstance(preset["version"], int) or isinstance(preset["version"], bool):
        raise PresetError(f"{pid}: version must be a whole number.")
    if "min_ai_toolkit" in preset and _version_tuple(preset["min_ai_toolkit"]) is None:
        raise PresetError(f"{pid}: min_ai_toolkit must be a version like 0.13.28.")
    if "model.name_or_path" not in preset["locked"]:
        raise PresetError(f"{pid}: locked must name 'model.name_or_path'.")
    for key in preset["locked"]:
        if key in TUNABLE_KEYS:
            raise PresetError(f"{pid}: locked key '{key}' is on the tunable list.")
    _check_table(pid, "defaults", preset["defaults"])
    target = preset["inference_target"]
    if target not in preset["preview"]:
        raise PresetError(f"{pid}: no preview for inference target '{target}'.")
    for name, table in preset["preview"].items():
        _check_table(pid, f"preview.{name}", table)
    if set(preset["subject"]) != set(SUBJECTS):
        raise PresetError(f"{pid}: subject must hold exactly {', '.join(SUBJECTS)}.")
    for name, row in preset["subject"].items():
        _check_table(pid, f"subject.{name}", row)
    if set(preset["effort"]) != set(EFFORTS):
        raise PresetError(f"{pid}: effort must hold exactly {', '.join(EFFORTS)}.")
    for name, row in preset["effort"].items():
        steps = [row.get(k) for k in EFFORT_STEP_KEYS]
        if not all(
            isinstance(v, (int, float))
            and not isinstance(v, bool)
            and math.isfinite(v)
            and v > 0
            for v in steps
        ):
            raise PresetError(
                f"{pid}: effort.{name} needs {', '.join(EFFORT_STEP_KEYS)}."
            )
        if row["min_steps"] > row["max_steps"]:
            raise PresetError(f"{pid}: effort.{name} min_steps is above max_steps.")
        _check_table(
            pid,
            f"effort.{name}",
            {k: v for k, v in row.items() if k not in EFFORT_STEP_KEYS},
        )
    if set(preset["fit"]) != set(FITS):
        raise PresetError(f"{pid}: fit must hold exactly {', '.join(FITS)}.")
    profiles = preset["hardware_profiles"]
    if not profiles:
        raise PresetError(f"{pid}: hardware_profiles must hold at least one profile.")
    setting_keys = None
    for name, profile in profiles.items():
        if profile.get("evidence") not in EVIDENCE:
            raise PresetError(f"{pid}: profile {name} has no valid evidence level.")
        for field in PROFILE_FIELDS:
            if field not in profile:
                raise PresetError(f"{pid}: profile {name} is missing '{field}'.")
        for field in _PROFILE_NUMBERS:
            value = profile[field]
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise PresetError(f"{pid}: profile {name} {field} must be above 0.")
        _check_table(pid, f"hardware_profiles.{name}", profile["settings"])
        # Every profile writes the same keys, so the one picked fully decides
        # the memory settings instead of inheriting another tier's.
        keys = set(profile["settings"])
        if setting_keys is not None and keys != setting_keys:
            raise PresetError(f"{pid}: profile {name} sets different keys.")
        setting_keys = keys
    for choice, names in preset["fit"].items():
        if not names:
            raise PresetError(f"{pid}: fit.{choice} names no profile.")
        for name in names:
            if name not in profiles:
                raise PresetError(f"{pid}: fit.{choice} names unknown profile {name}.")
    for row in (preset["defaults"], *preset["subject"].values()):
        sizes = row.get("datasets[*].resolution") or []
        for size in sizes:
            if size % preset["divisibility"]:
                raise PresetError(
                    f"{pid}: resolution {size} is not a multiple of "
                    f"{preset['divisibility']}."
                )
    return preset


def validate_update(preset: dict, shipped: dict) -> dict:
    """Check a downloaded *preset* may replace *shipped*, and return it.

    It may change values and memory profiles, never the family or the
    ``locked`` table of model checkpoints, adapter and compatibility keys
    (:data:`UPDATE_FIXED`), and it holds no key outside the schema.

    Raises:
        PresetError: Naming the first thing it may not do.
    """
    pid = shipped["id"]
    if not isinstance(preset, dict):
        raise PresetError(f"{pid}: a recipe must be an object.")
    extra = set(preset) - set(FIELDS) - set(OPTIONAL_FIELDS)
    if extra:
        raise PresetError(f"{pid}: unknown keys {', '.join(sorted(extra))}.")
    try:
        validate_preset(preset)
    except (TypeError, AttributeError, KeyError) as exc:
        # A wrong type the shipped files never carry (a list for a table).
        raise PresetError(f"{pid}: malformed recipe: {exc!r}.") from exc
    for field in UPDATE_FIXED:
        if preset[field] != shipped[field]:
            raise PresetError(f"{pid}: an update may not change '{field}'.")
    if not isinstance(preset["label"], str):
        raise PresetError(f"{pid}: label must be text.")
    for name, profile in preset["hardware_profiles"].items():
        extra = set(profile) - set(PROFILE_FIELDS)
        if extra:
            raise PresetError(
                f"{pid}: profile {name} has unknown keys {', '.join(sorted(extra))}."
            )
    for choice, names in preset["fit"].items():
        if not isinstance(names, list):
            raise PresetError(f"{pid}: fit.{choice} must be a list.")
    sources = preset["sources"]
    if not isinstance(sources, dict) or not all(
        isinstance(v, str) for v in sources.values()
    ):
        raise PresetError(f"{pid}: sources must map each value to text.")
    return preset


def use_cache_dir(path: str | None) -> None:
    """Read downloaded recipes from *path* (None: shipped only)."""
    global _cache_dir
    _cache_dir = path
    reload()


def cache_dir() -> str | None:
    """Where downloaded recipes are kept, or None."""
    return _cache_dir


def reload() -> None:
    """Forget what was read, so the next read sees the cache as it is now."""
    _load_all.cache_clear()


@lru_cache(maxsize=None)
def shipped_presets() -> dict[str, dict]:
    """The recipes that ship in ``pixlstash/data/training-presets/``, by id."""
    presets = {}
    for filename in sorted(os.listdir(PRESET_DIR)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(PRESET_DIR, filename)
        with open(path, encoding="utf-8") as handle:
            preset = validate_preset(json.load(handle))
        presets[preset["id"]] = preset
    return presets


def _cached(shipped: dict) -> dict | None:
    """The downloaded version of *shipped*, when it may replace it."""
    if _cache_dir is None:
        return None
    path = os.path.join(_cache_dir, f"{shipped['id']}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            preset = validate_update(json.load(handle), shipped)
    except (OSError, ValueError, RecursionError) as exc:
        logger.warning(
            "Ignoring the downloaded recipe %s (%s); using the shipped version %s.",
            path,
            exc,
            shipped["version"],
        )
        return None
    if preset["version"] <= shipped["version"]:
        logger.info(
            "Ignoring the downloaded recipe %s: version %s is not above the "
            "shipped %s.",
            path,
            preset["version"],
            shipped["version"],
        )
        return None
    return preset


@lru_cache(maxsize=None)
def _load_all() -> dict[str, dict]:
    return {
        pid: _cached(shipped) or shipped for pid, shipped in shipped_presets().items()
    }


def list_presets() -> list[dict]:
    """Every preset, downloaded where newer: :data:`DEFAULT_PRESET` first, then by id.

    The Train dialog preselects the first one the checkout can run.
    """
    ids = sorted(_load_all(), key=lambda pid: (pid != DEFAULT_PRESET, pid))
    return [_load_all()[pid] for pid in ids]


def get_preset(preset_id: str) -> dict | None:
    """The preset *preset_id* (its downloaded version where newer), or None."""
    return _load_all().get(preset_id)


def preset_ref(preset: dict) -> str:
    """``id@version``, what a run records."""
    return f"{preset['id']}@{preset['version']}"


def template_path(preset: dict) -> str:
    """The passthrough job-config skeleton the preset renders over."""
    return os.path.join(TEMPLATE_DIR, f"{preset['template']}.json")


def checkout_architectures(repo_path: str | None) -> list[str]:
    """The model code a checkout ships: ``extensions_built_in/diffusion_models/<arch>/``."""
    if not repo_path:
        return []
    root = os.path.join(repo_path, "extensions_built_in", "diffusion_models")
    try:
        names = os.listdir(root)
    except OSError as exc:
        logger.info("No diffusion model code under %s: %s", root, exc)
        return []
    return sorted(
        name
        for name in names
        if not name.startswith(("_", ".")) and os.path.isdir(os.path.join(root, name))
    )


def _version_tuple(value) -> tuple[int, ...] | None:
    """``"0.13.28"`` as ``(0, 13, 28)``; None for anything that is not one."""
    if not isinstance(value, str) or not re.fullmatch(r"\d+(\.\d+)*", value.strip()):
        return None
    return tuple(int(part) for part in value.strip().split("."))


def not_offered_because(
    preset: dict, architectures: list[str], version: str | None = None
) -> str | None:
    """Why a checkout with *architectures* at *version* cannot train *preset*.

    Returns:
        One sentence naming what the checkout lacks, or None when it can train.
    """
    if preset["requires_arch"] not in architectures:
        return (
            f"This ai-toolkit checkout has no {preset['requires_arch']} model "
            f"code, so it cannot train {preset['label']}. Update the checkout."
        )
    needed = preset.get("min_ai_toolkit")
    if needed:
        have = _version_tuple(version)
        if have is None or have < _version_tuple(needed):
            return (
                f"{preset['label']} needs ai-toolkit {needed} or newer; this "
                f"checkout is {version or 'of an unknown version'}. Update the "
                "checkout."
            )
    return None


def offered(preset: dict, architectures: list[str], version: str | None = None) -> bool:
    """Whether a checkout with *architectures* at *version* can train *preset*."""
    return not_offered_because(preset, architectures, version) is None


def trains_clips(preset: dict) -> bool:
    """Whether *preset*'s dataset holds the set's video clips beside its stills."""
    return reads_video(to_patch(preset["locked"]))


def reads_video(patch: dict) -> bool:
    """Whether a job-config *patch* (``to_patch`` shape) has ai-toolkit read video.

    ai-toolkit reads a dataset as video when it pulls more than one frame
    (``num_frames`` above 1, or ``auto_frame_count``), and it then takes clips
    and stills alike; otherwise it reads stills only.
    """
    process = ((patch.get("config") or {}).get("process") or [{}])[0]
    dataset = (process.get("datasets") or [{}])[0]
    return bool(dataset.get("auto_frame_count")) or (
        int(dataset.get("num_frames") or 1) > 1
    )


def to_patch(values: dict) -> dict:
    """Turn flat ``{key: value}`` into the nested ``config.process[0]`` patch.

    A ``[*]`` segment writes the first list element (the job has one dataset);
    the prompt list becomes ``sample.samples``.
    """
    process: dict = {}
    for key, value in values.items():
        if key == PROMPTS_KEY:
            process.setdefault("sample", {})["samples"] = [
                {"prompt": prompt} for prompt in value
            ]
            continue
        node = process
        *parents, leaf = key.split(".")
        for part in parents:
            if part.endswith("[*]"):
                node = node.setdefault(part[:-3], [{}])[0]
            else:
                node = node.setdefault(part, {})
        node[leaf] = value
    return {"config": {"process": [process]}}


def validate_pins(preset: dict, pins: dict | None) -> dict:
    """Check the owner's Advanced values and return a copy.

    Raises:
        TunableOverrideError: A locked key (named as the preset's), a key that
            is not tunable, or a value outside its rule.
    """
    if pins is None:
        return {}
    if not isinstance(pins, dict):
        raise TunableOverrideError("pins must be an object of setting: value.")
    for key, value in pins.items():
        if key in preset["locked"]:
            raise TunableOverrideError(
                f"'{key}' is fixed by the {preset['label']} recipe and cannot be "
                "changed."
            )
        check_tunable(key, value)
    return dict(pins)


def _gb(mb: int) -> str:
    return f"{mb / 1024:.1f} GB"


def resolve(
    preset: dict,
    *,
    subject: str,
    effort: str,
    fit: str,
    picture_count: int,
    vram_mb: int,
    ram_mb: int | None,
    pins: dict | None = None,
    prompts: list[str] | None = None,
    preservation: dict | None = None,
    lookup: Callable[[dict], tuple[str, dict | None]] | None = None,
    profile: str | None = None,
) -> dict:
    """Every value a run of *preset* uses on this machine, and where it came from.

    Args:
        preset: A validated preset.
        subject: ``person`` / ``style`` / ``thing``.
        effort: ``gentle`` / ``standard`` / ``strong``.
        fit: ``auto`` / ``save_memory`` / ``best_quality``.
        picture_count: Pictures in the dataset; scales the step budget.
        vram_mb: GPU memory the run can be given on the leased card.
        ram_mb: Available physical RAM (swap never counts); None when unknown,
            which skips the RAM check.
        pins: The owner's Advanced values, already checked by
            :func:`validate_pins`. They win over every derived value. The
            profile is chosen for the size, rank and batch they ask for, and a
            pinned memory setting limits it to profiles with that setting.
        prompts: The sample prompts written from the dataset's captions
            (:func:`pixlstash.training.sample_prompts.generate`), checked
            like a pin; pinned prompts win over them.
        preservation: Whether to keep the person's description generic, and
            that description (:func:`pixlstash.training.sample_prompts.preservation`),
            source ``captions``; pins win over them. Left on beside a text
            encoder that trains it is turned off, as ai-toolkit refuses the
            two together; pinned on, that is an error.
        lookup: Answers ``(fingerprint, measurement or None)`` for a profile's
            full ``{key: value}`` (:func:`measurements.make_lookup`). A
            measured profile is admitted on its measured peaks instead of the
            seeds, and its previews apart. None skips measurements altogether
            (and never asks for a probe).
        profile: Consider this hardware profile alone, whatever the fit
            choice: the worker re-resolves with the profile a probe settled on.

    Returns:
        ``{"preset", "subject", "effort", "fit", "picture_count", "profile",
        "profile_label", "evidence", "vram_mb", "ram_mb", "preview_vram_mb",
        "room", "steps_clamp", "fingerprint", "probe", "fallbacks",
        "passed_over", "previews_deferred", "estimate", "values"}``;
        ``steps_clamp`` is
        ``cap`` or ``floor`` when the effort's step range held the
        picture-scaled count, else None; ``probe`` is True when the profile's
        setup has never been measured here; ``fallbacks`` are the profiles a
        probe that runs out of memory steps to; ``passed_over`` are the
        profiles before it that train but were measured with previews that do
        not fit, which the probe keeps to fall back on; ``estimate`` is ``{seconds,
        basis}``, basis ``measured`` or ``none``; ``values`` maps each key to
        ``{"value", "source"}``, the source being ``model``, ``subject``,
        ``effort``, ``pictures``, ``captions``, ``gpu`` or ``pinned``.

    Raises:
        TunableOverrideError: Preservation pinned on beside a text encoder
            that trains, or with no description to keep generic (a style or
            a thing).
        ValueError: An unknown choice.
        NoProfileFits: Nothing in the fit choice fits; says which budget.
    """
    for name, value, allowed in (
        ("subject", subject, SUBJECTS),
        ("effort", effort, EFFORTS),
        ("fit", fit, FITS),
    ):
        if value not in allowed:
            raise ValueError(f"{name} must be one of {', '.join(allowed)}.")
    pins = pins or {}
    values: dict = {}

    def put(source: str, table: dict) -> None:
        for key, value in table.items():
            values[key] = {"value": value, "source": source}

    put("model", preset["defaults"])
    put("model", preset["preview"][preset["inference_target"]])
    put("subject", preset["subject"][subject])
    if prompts:
        check_tunable(PROMPTS_KEY, list(prompts))
        put("captions", {PROMPTS_KEY: list(prompts)})
    if preservation:
        for key, value in preservation.items():
            check_tunable(key, value)
        put("captions", preservation)
    row = preset["effort"][effort]
    put("effort", {k: v for k, v in row.items() if k not in EFFORT_STEP_KEYS})
    steps = math.ceil(row["steps_per_picture"] * max(0, picture_count))
    values["train.steps"] = {
        "value": int(min(max(steps, row["min_steps"]), row["max_steps"])),
        "source": "pictures",
    }
    clamp = (
        "cap"
        if steps > row["max_steps"]
        else "floor"
        if steps < row["min_steps"]
        else None
    )
    put("pinned", pins)
    kept = values.get(PRESERVATION_KEY)
    if kept and kept["value"] and not values.get(PRESERVATION_CLASS_KEY):
        raise TunableOverrideError(
            "Keeping the description generic needs a person's description; "
            "this run has none."
        )
    if (
        kept
        and kept["value"]
        and values.get("train.train_text_encoder", {}).get("value")
    ):
        if kept["source"] == "pinned":
            raise TunableOverrideError(
                "Keeping the description generic cannot be combined with "
                "training the text encoder."
            )
        values[PRESERVATION_KEY] = {"value": False, "source": kept["source"]}
    if "network.linear_alpha" not in pins and "network.linear" in values:
        values["network.linear_alpha"] = dict(values["network.linear"])

    sizes = values.get("datasets[*].resolution", {}).get("value") or []
    bucket = max(sizes, default=0)
    for size in sizes:
        if size % preset["divisibility"]:
            raise TunableOverrideError(
                f"'datasets[*].resolution' {size} is not a multiple of "
                f"{preset['divisibility']}, which {preset['label']} needs."
            )
    rank = values.get("network.linear", {}).get("value") or 0
    batch = values.get("train.batch_size", {}).get("value") or 1
    profiles = preset["hardware_profiles"]
    # A pinned memory setting decides the profile instead of the fit choice:
    # only a profile with that setting has a need the lease can ask for.
    setting_keys = set(next(iter(profiles.values()))["settings"])
    pinned_settings = {k: v for k, v in pins.items() if k in setting_keys}
    names = list(preset["fit"][fit])
    if pinned_settings:
        names += [name for name in profiles if name not in names]
    if profile is not None:
        if profile not in profiles:
            raise ValueError(f"{preset['label']} has no memory profile {profile}.")
        names = [profile]
    candidates = [
        (name, profiles[name])
        for name in names
        if profiles[name]["evidence"] != "none"
        and all(profiles[name]["settings"][k] == v for k, v in pinned_settings.items())
    ]
    sized = [
        (n, p)
        for n, p in candidates
        if p["max_bucket"] >= bucket
        and p["max_rank"] >= rank
        and p["max_batch"] >= batch
    ]
    if not sized:
        raise NoProfileFits(
            f"No {preset['label']} memory profile is known for {bucket}px, rank "
            f"{rank} and batch size {batch}"
            + (" with the Advanced memory settings." if pinned_settings else "."),
            "profile",
            0,
            0,
        )
    flat = {key: entry["value"] for key, entry in values.items()}
    preview_size = {
        k: v
        for k, v in preset["preview"][preset["inference_target"]].items()
        if k in ("sample.width", "sample.height")
    }

    def judged(name: str, profile: dict) -> dict:
        """The profile's needs, measured on this machine where they were."""
        settings = {k: v for k, v in profile["settings"].items() if k not in pins}
        entry = {
            "name": name,
            "profile": profile,
            "vram_mb": profile["vram_mb"],
            "ram_mb": profile["ram_mb"],
            "evidence": profile["evidence"],
            "fingerprint": None,
            "measurement": None,
            "preview_vram_mb": None,
            "preview_ram_mb": None,
        }
        if lookup is None:
            return entry
        key, measurement = lookup({**flat, **settings})
        entry["fingerprint"] = key
        if measurement is None:
            # Previews pinned to another size are a setup of their own, but
            # training peaks the same whatever size it previews at (#2415):
            # the recipe's own size, measured, admits the training.
            full = {k: v for k, v in preview_size.items() if flat.get(k) != v}
            if not full:
                return entry
            _, sized_like = lookup({**flat, **settings, **full})
            needs = measurements.needs(sized_like) if sized_like else {}
            if needs.get("vram_mb") is not None:
                entry["vram_mb"] = needs["vram_mb"]
                entry["evidence"] = "measured"
            if needs.get("ram_mb") is not None:
                entry["ram_mb"] = needs["ram_mb"]
            return entry
        measured = measurements.needs(measurement)
        entry["measurement"] = measurement
        if measured["vram_mb"] is not None:
            entry["vram_mb"] = measured["vram_mb"]
            entry["evidence"] = "measured"
        if measured["ram_mb"] is not None:
            entry["ram_mb"] = measured["ram_mb"]
        entry["preview_vram_mb"] = measured["preview_vram_mb"]
        entry["preview_ram_mb"] = measured["preview_ram_mb"]
        return entry

    judgedall = [judged(n, p) for n, p in sized]
    fitting = [
        e
        for e in judgedall
        if e["vram_mb"] <= vram_mb and (ram_mb is None or e["ram_mb"] <= ram_mb)
    ]
    if not fitting:
        vram_ok = [e for e in judgedall if e["vram_mb"] <= vram_mb]
        if vram_ok:
            need = min(e["ram_mb"] for e in vram_ok)
            raise NoProfileFits(
                f"{preset['label']} needs {_gb(need)} of RAM to hold what does "
                f"not fit on the GPU; {_gb(ram_mb or 0)} is available.",
                "ram",
                need,
                ram_mb or 0,
            )
        need = min(e["vram_mb"] for e in judgedall)
        raise NoProfileFits(
            f"{preset['label']} needs {_gb(need)} of GPU memory at least; "
            f"{_gb(vram_mb)} can be given to it on this GPU.",
            "vram",
            need,
            vram_mb,
        )

    def previews_fit(entry: dict) -> bool:
        """Whether *entry*'s previews fit the room, as far as it was measured."""
        vram = entry["preview_vram_mb"]
        ram = entry["preview_ram_mb"]
        return not (
            (vram is not None and vram > vram_mb)
            or (ram is not None and ram_mb is not None and ram > ram_mb)
        )

    # Previews drawn while training are worth more offloading (#2411): the
    # first profile whose previews fit is picked over putting them off, unless
    # the owner pinned them off.
    sampling = SAMPLING_KEY
    picked = fitting[0]
    if not pins.get(sampling):
        picked = next((e for e in fitting if previews_fit(e)), picked)
    passed_over = fitting[: fitting.index(picked)]
    name, profile = picked["name"], picked["profile"]
    for key, value in profile["settings"].items():
        if key not in pins:
            values[key] = {"value": value, "source": "gpu"}
    # ai-toolkit drops no captions it has encoded once, so say what will run.
    dropout = "datasets[*].caption_dropout_rate"
    if values.get("train.cache_text_embeddings", {}).get("value") and (
        dropout in values and dropout not in pins
    ):
        values[dropout] = {"value": 0.0, "source": "gpu"}
    # Previews are admitted apart: a run whose training fits but whose
    # previews do not in any profile trains without them, and its checkpoints
    # are sampled once training is done.
    deferred = not previews_fit(picked)
    if sampling in pins:
        # The owner's word on previews stands, either way.
        deferred = bool(pins[sampling])
    elif deferred:
        values[sampling] = {"value": True, "source": "gpu"}
    seconds = None
    if picked["measurement"] is not None:
        seconds = measurements.estimate_seconds(
            picked["measurement"],
            steps=values["train.steps"]["value"],
            previews=_preview_images(values, deferred),
        )
        if deferred and seconds is not None:
            # Each checkpoint sampled after training loads the model again.
            seconds += _after_training_passes(values) * float(
                picked["measurement"].get("setup_seconds") or 0
            )
    return {
        "preset": preset_ref(preset),
        "subject": subject,
        "effort": effort,
        "fit": fit,
        "picture_count": picture_count,
        "profile": name,
        "profile_label": profile["label"],
        "evidence": picked["evidence"],
        "vram_mb": picked["vram_mb"],
        "ram_mb": picked["ram_mb"],
        "preview_vram_mb": picked["preview_vram_mb"],
        "preview_ram_mb": picked["preview_ram_mb"],
        "room": {"vram_mb": vram_mb, "ram_mb": ram_mb},
        "steps_clamp": clamp,
        "fingerprint": picked["fingerprint"],
        "probe": lookup is not None and picked["measurement"] is None,
        # The profiles a probe that runs out of memory steps to, in the fit
        # choice's order: each offloads more than the one before.
        "fallbacks": [e["name"] for e in judgedall[judgedall.index(picked) + 1 :]],
        "passed_over": [e["name"] for e in passed_over],
        "previews_deferred": bool(deferred),
        "estimate": {
            "seconds": round(seconds) if seconds is not None else None,
            "basis": "measured" if seconds is not None else "none",
        },
        "values": values,
    }


def preview_choice(preset: dict, *, pins: dict | None = None, **choices) -> dict | None:
    """The Train dialog's Previews question (#2415), or None when there is none.

    It exists when the fastest profile that trains was measured with previews
    that do not fit, so :func:`resolve` moved to a slower one to draw them
    while training, and a route that keeps the fast profile finishes sooner by
    :data:`CHOICE_MIN_SHARE` and :data:`CHOICE_MIN_SECONDS`. The routes:
    ``while`` (the default, no pin), ``smaller`` (``sample.width`` and
    ``sample.height`` pinned to :data:`SMALLER_PREVIEW`) and ``after``
    (``train.disable_sampling`` pinned on). An unmeasured default has no time
    to compare, so a first run asks nothing.

    Args:
        preset: A validated preset.
        pins: The owner's pins; the ones a route writes are set aside to price
            every route from the same start.
        **choices: :func:`resolve`'s other keyword arguments, ``lookup``
            included.

    Returns:
        None, or ``{"preset", "selected", "full_size", "smaller_size",
        "trains_same", "differs", "options"}``, ``options`` starting with
        ``while`` and holding only the routes that save time (and the
        selected one). Each option is ``{"id", "profile", "profile_label",
        "layers_in_ram", "seconds", "peak_vram_mb", "free_mb", "tight",
        "basis"}``: ``basis`` is ``measured``, or ``estimated`` for a 768²
        peak scaled from the full-size one by pixel count. ``differs`` names
        the values other than placement that a route changes.
    """
    pins = dict(pins or {})
    size_keys = ("sample.width", "sample.height")
    smaller_pinned = all(pins.get(k) == SMALLER_PREVIEW for k in size_keys)
    selected = (
        "after" if pins.get(SAMPLING_KEY) else "smaller" if smaller_pinned else "while"
    )
    base = {
        k: v
        for k, v in pins.items()
        if k != SAMPLING_KEY and not (smaller_pinned and k in size_keys)
    }
    try:
        default = resolve(preset, pins=base, **choices)
    except NoProfileFits:
        return None
    if not default["passed_over"] or default["estimate"]["seconds"] is None:
        return None
    fast = default["passed_over"][0]
    vram_room = default["room"]["vram_mb"]
    ram_room = default["room"]["ram_mb"]

    def option(route: str, res: dict, peak: int, basis: str, seconds) -> dict:
        values = resolved_values(res)
        in_ram = values.get("model.layer_offloading_transformer_percent")
        return {
            "id": route,
            "profile": res["profile"],
            "profile_label": res["profile_label"],
            "layers_in_ram": (
                round(float(in_ram) * 100)
                if values.get("model.layer_offloading") and in_ram is not None
                else 0
            ),
            "seconds": seconds,
            "peak_vram_mb": peak,
            "free_mb": vram_room - peak,
            "tight": vram_room - peak < TIGHT_MB,
            "basis": basis,
            "values": values,
        }

    while_ = option(
        "while",
        default,
        max(default["vram_mb"], default["preview_vram_mb"] or 0),
        "measured",
        default["estimate"]["seconds"],
    )
    after_res = resolve(
        preset, pins={**base, SAMPLING_KEY: True}, profile=fast, **choices
    )
    after = None
    if after_res["estimate"]["seconds"] is not None:
        after = option(
            "after",
            after_res,
            after_res["vram_mb"],
            "measured",
            after_res["estimate"]["seconds"],
        )

    smaller = None
    values = default["values"]
    width = (values.get("sample.width") or {}).get("value") or 0
    height = (values.get("sample.height") or {}).get("value") or 0
    if (
        not any(k in base for k in size_keys)
        and width * height > SMALLER_PREVIEW**2
        and after_res["preview_vram_mb"] is not None
        # A smaller picture is no answer to previews that need more RAM.
        and not (
            ram_room is not None
            and after_res["preview_ram_mb"] is not None
            and after_res["preview_ram_mb"] > ram_room
        )
    ):
        small_pins = {**base, **{k: SMALLER_PREVIEW for k in size_keys}}
        try:
            small = resolve(preset, pins=small_pins, **choices)
        except NoProfileFits:
            small = None
        if small is not None and small["profile"] == fast:
            if small["probe"] or small["preview_vram_mb"] is None:
                # Not drawn at 768² here yet: the weights stay as measured,
                # and only what a full-size preview adds on top of them is
                # scaled by pixel count, which errs high for a VAE decode.
                train_mb = after_res["vram_mb"]
                extra = max(0, after_res["preview_vram_mb"] - train_mb)
                peak = train_mb + math.ceil(
                    extra * SMALLER_PREVIEW**2 / (width * height)
                )
                # The full-size preview time on the fast profile: a little
                # long for a smaller picture, never short.
                timed = resolve(
                    preset, pins={**base, SAMPLING_KEY: False}, profile=fast, **choices
                )
                basis, seconds = "estimated", timed["estimate"]["seconds"]
            else:
                peak = max(small["vram_mb"], small["preview_vram_mb"])
                basis, seconds = "measured", small["estimate"]["seconds"]
            if peak <= vram_room and seconds is not None:
                smaller = option("smaller", small, peak, basis, seconds)

    def saves(o: dict) -> bool:
        gain = while_["seconds"] - o["seconds"]
        return gain > CHOICE_MIN_SECONDS and gain > CHOICE_MIN_SHARE * while_["seconds"]

    others = [o for o in (smaller, after) if o is not None]
    if not any(saves(o) for o in others):
        return None
    options = [while_] + [o for o in others if saves(o) or o["id"] == selected]
    trained = {
        k: v
        for k, v in while_["values"].items()
        if k not in _PLACEMENT_KEYS and not k.startswith("sample.")
    }
    differs = sorted(
        {
            k
            for o in options[1:]
            for k in set(trained) | set(o["values"])
            if k not in _PLACEMENT_KEYS
            and not k.startswith("sample.")
            and trained.get(k) != o["values"].get(k)
        }
    )
    for o in options:
        del o["values"]
    return {
        "preset": preset["id"],
        "selected": selected,
        "full_size": [width, height],
        "smaller_size": SMALLER_PREVIEW,
        "trains_same": not differs,
        "differs": differs,
        "options": options,
    }


def _preview_images(values: dict, deferred: bool) -> int:
    """How many preview pictures a run draws with these values."""
    prompts = len((values.get(PROMPTS_KEY) or {}).get("value") or []) or 1
    steps = int(values["train.steps"]["value"])
    if deferred:
        return prompts * _after_training_passes(values)
    every = int((values.get("sample.sample_every") or {}).get("value") or 0)
    if every <= 0:
        return prompts * 2
    # The "before" round at step 0, one every sample_every, and the last.
    rounds = steps // every + 1 + (1 if steps % every else 0)
    return prompts * rounds


def _after_training_passes(values: dict) -> int:
    """How many checkpoints a run with deferred previews samples afterwards."""
    steps = int(values["train.steps"]["value"])
    every = int((values.get("save.save_every") or {}).get("value") or 0)
    keep = int((values.get("save.max_step_saves_to_keep") or {}).get("value") or 4)
    saved = steps // every if every > 0 else 0
    return min(saved, keep) + 1


def resolved_values(resolution: dict) -> dict:
    """The flat ``{key: value}`` of a :func:`resolve` result."""
    return {key: entry["value"] for key, entry in resolution["values"].items()}
