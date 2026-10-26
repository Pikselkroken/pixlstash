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
"""

from __future__ import annotations

import json
import math
import os
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
_MAX_PROMPTS = 16
EFFORT_STEP_KEYS = ("steps_per_picture", "min_steps", "max_steps")


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
    for field in (
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
    ):
        if field not in preset:
            raise PresetError(f"{pid}: missing '{field}'.")
    if "model.name_or_path" not in preset["locked"]:
        raise PresetError(f"{pid}: locked must name 'model.name_or_path'.")
    for key in preset["locked"]:
        if key in TUNABLE_KEYS:
            raise PresetError(f"{pid}: locked key '{key}' is on the tunable list.")
    _check_table(pid, "defaults", preset["defaults"])
    target = preset["inference_target"]
    if target not in preset["preview"]:
        raise PresetError(f"{pid}: no preview for inference target '{target}'.")
    _check_table(pid, f"preview.{target}", preset["preview"][target])
    if set(preset["subject"]) != set(SUBJECTS):
        raise PresetError(f"{pid}: subject must hold exactly {', '.join(SUBJECTS)}.")
    for name, row in preset["subject"].items():
        _check_table(pid, f"subject.{name}", row)
    if set(preset["effort"]) != set(EFFORTS):
        raise PresetError(f"{pid}: effort must hold exactly {', '.join(EFFORTS)}.")
    for name, row in preset["effort"].items():
        steps = [row.get(k) for k in EFFORT_STEP_KEYS]
        if not all(isinstance(v, (int, float)) and v > 0 for v in steps):
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
        for field in (
            "label",
            "vram_mb",
            "ram_mb",
            "max_bucket",
            "max_rank",
            "max_batch",
            "settings",
        ):
            if field not in profile:
                raise PresetError(f"{pid}: profile {name} is missing '{field}'.")
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


@lru_cache(maxsize=None)
def _load_all() -> dict[str, dict]:
    presets = {}
    for filename in sorted(os.listdir(PRESET_DIR)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(PRESET_DIR, filename)
        with open(path, encoding="utf-8") as handle:
            preset = validate_preset(json.load(handle))
        presets[preset["id"]] = preset
    return presets


def list_presets() -> list[dict]:
    """Every shipped preset, by id."""
    return [_load_all()[pid] for pid in sorted(_load_all())]


def get_preset(preset_id: str) -> dict | None:
    """The shipped preset *preset_id*, or None."""
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


def offered(preset: dict, architectures: list[str]) -> bool:
    """Whether a checkout with *architectures* can train *preset*."""
    return preset["requires_arch"] in architectures


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
        "previews_deferred", "estimate", "values"}``; ``steps_clamp`` is
        ``cap`` or ``floor`` when the effort's step range held the
        picture-scaled count, else None; ``probe`` is True when the profile's
        setup has never been measured here; ``fallbacks`` are the profiles a
        probe that runs out of memory steps to; ``estimate`` is ``{seconds,
        basis}``, basis ``measured`` or ``none``; ``values`` maps each key to
        ``{"value", "source"}``, the source being ``model``, ``subject``,
        ``effort``, ``pictures``, ``captions``, ``gpu`` or ``pinned``.

    Raises:
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
    picked = fitting[0]
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
    # previews do not trains without them, and its checkpoints are sampled
    # once training is done.
    deferred = (
        picked["preview_vram_mb"] is not None and picked["preview_vram_mb"] > vram_mb
    ) or (
        picked["preview_ram_mb"] is not None
        and ram_mb is not None
        and picked["preview_ram_mb"] > ram_mb
    )
    sampling = "train.disable_sampling"
    if deferred and sampling not in pins:
        values[sampling] = {"value": True, "source": "gpu"}
    elif values.get(sampling, {}).get("value"):
        deferred = True
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
        "room": {"vram_mb": vram_mb, "ram_mb": ram_mb},
        "steps_clamp": clamp,
        "fingerprint": picked["fingerprint"],
        "probe": lookup is not None and picked["measurement"] is None,
        # The profiles a probe that runs out of memory steps to, in the fit
        # choice's order: each offloads more than the one before.
        "fallbacks": [e["name"] for e in judgedall[judgedall.index(picked) + 1 :]],
        "previews_deferred": bool(deferred),
        "estimate": {
            "seconds": round(seconds) if seconds is not None else None,
            "basis": "measured" if seconds is not None else "none",
        },
        "values": values,
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
