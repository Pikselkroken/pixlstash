"""Render the golden ai-toolkit job-config template into a concrete config.

PixlStash owns only a handful of fields in the trainer config (the
``{{PLACEHOLDER}}`` tokens in the template); everything else is passed through
verbatim. This module loads the template, deep-merges the run's tunable values
and the recipe's locked keys (:mod:`pixlstash.training.presets`) on top, then
substitutes those placeholders everywhere, except that a sample prompt
(``sample.samples[].prompt``) takes only the trigger word.

Caller overrides are restricted to :data:`TUNABLE_KEYS`: training
hyper-parameters only. A path, a process type, a device or a model source can
never be reached through an override, because :func:`validate_tunables` refuses
any key outside the allow-list before the merge happens.

No trainer-specific behaviour beyond the placeholder names lives here; the
adapter (:mod:`pixlstash.training.local_ai_toolkit`) is responsible for
injecting the resolved per-job paths via these arguments and for writing the
result to disk.
"""

from __future__ import annotations

import copy
import json
import math
import re
from typing import Any, NamedTuple

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


class _Rule(NamedTuple):
    """Type and range check for one tunable.

    Attributes:
        kind: ``int``, ``float``, ``bool``, ``enum``, ``str`` or ``int_list``.
        lo: Lower bound (inclusive) for numbers and list items.
        hi: Upper bound (inclusive) for numbers and list items, or max length
            for ``str``.
        count: Maximum list length for ``int_list``.
        choices: Allowed values for ``enum``.
    """

    kind: str
    lo: float = 0
    hi: float = 0
    count: int = 0
    choices: frozenset = frozenset()


def _ints(lo: int, hi: int) -> _Rule:
    return _Rule("int", lo, hi)


def _floats(lo: float, hi: float) -> _Rule:
    return _Rule("float", lo, hi)


def _flag() -> _Rule:
    return _Rule("bool")


def _one_of(*choices: str) -> _Rule:
    return _Rule("enum", choices=frozenset(choices))


def _text(max_len: int) -> _Rule:
    return _Rule("str", hi=max_len)


def _int_list(lo: int, hi: int, count: int) -> _Rule:
    return _Rule("int_list", lo, hi, count)


# The one allow-list. Keys are dotted paths relative to ``config.process[0]``;
# ``[*]`` matches any list index. Every caller-supplied override is checked
# against this table in :func:`validate_tunables`, which the render path runs.
TUNABLE_KEYS: dict[str, _Rule] = {
    "train.steps": _ints(1, 100_000),
    "train.lr": _floats(1e-7, 1e-1),
    "train.batch_size": _ints(1, 64),
    "train.gradient_accumulation": _ints(1, 64),
    "train.optimizer": _one_of("adamw", "adamw8bit", "adafactor", "prodigy", "lion"),
    "train.content_or_style": _one_of("balanced", "content", "style"),
    "train.timestep_type": _one_of("linear", "sigmoid", "shift", "weighted"),
    "train.train_text_encoder": _flag(),
    "train.cache_text_embeddings": _flag(),
    "train.disable_sampling": _flag(),
    "train.audio_loss_multiplier": _floats(0, 10),
    # Keeps the words that describe a person what the base model drew for them
    # (sample_prompts.preservation); the class word is that description.
    "train.diff_output_preservation": _flag(),
    "train.diff_output_preservation_class": _text(200),
    "network.linear": _ints(1, 512),
    "network.linear_alpha": _floats(0, 1024),
    "network.conv": _ints(0, 512),
    "network.conv_alpha": _floats(0, 1024),
    "datasets[*].resolution": _int_list(64, 4096, 8),
    "datasets[*].caption_dropout_rate": _floats(0, 1),
    "datasets[*].num_repeats": _ints(1, 1000),
    "datasets[*].flip_x": _flag(),
    "datasets[*].do_audio": _flag(),
    "save.save_every": _ints(1, 100_000),
    # Kept checkpoints are what fills the disk, whatever save_every is, so this
    # bounds what a recipe update from pixlstash.dev can ask for (#2295).
    "save.max_step_saves_to_keep": _ints(1, 24),
    "sample.sample_every": _ints(1, 100_000),
    "sample.samples[*].prompt": _text(2000),
    "sample.sample_steps": _ints(1, 200),
    "sample.guidance_scale": _floats(0, 30),
    "sample.width": _ints(64, 4096),
    "sample.height": _ints(64, 4096),
    "sample.seed": _ints(0, 2**32 - 1),
    "sample.num_frames": _ints(1, 1000),
    "sample.fps": _ints(1, 120),
    "model.quantize": _flag(),
    "model.qtype": _one_of("qfloat8", "qint8", "qint4", "convrot8"),
    "model.quantize_te": _flag(),
    "model.qtype_te": _one_of("qfloat8", "qint8", "qint4", "nvfp4"),
    "model.low_vram": _flag(),
    "model.layer_offloading": _flag(),
    "model.layer_offloading_text_encoder_percent": _floats(0, 1),
    "model.layer_offloading_transformer_percent": _floats(0, 1),
}

_PROCESS_PREFIX = ("config", "process", 0)
_ANY_INDEX = "*"


class TunableOverrideError(ValueError):
    """A config override names a key outside the tunables, or breaks its rule."""


def _parse_key(key: str) -> tuple:
    """Turn ``datasets[*].resolution`` into ``("datasets", "*", "resolution")``."""
    tokens: list = []
    for part in key.split("."):
        if part.endswith("[*]"):
            tokens.append(part[:-3])
            tokens.append(_ANY_INDEX)
        else:
            tokens.append(part)
    return tuple(tokens)


def _matches(pattern: tuple, rel: tuple) -> bool:
    """Whether a parsed allow-list pattern matches a relative override path."""
    if len(pattern) != len(rel):
        return False
    for want, got in zip(pattern, rel):
        if want == _ANY_INDEX:
            if isinstance(got, bool) or not isinstance(got, int):
                return False
        elif want != got:
            return False
    return True


def _leaves(node: Any, path: tuple = ()):
    """Yield ``(path, value)`` for every leaf of an override tree.

    Non-empty dicts and lists of non-empty dicts are walked; anything else
    (scalars, empty containers, lists of scalars) is a leaf, so a list such as
    ``resolution: [512, 768]`` is checked as one value.
    """
    if isinstance(node, dict) and node:
        for key, value in node.items():
            yield from _leaves(value, path + (key,))
    elif (
        isinstance(node, list) and node and all(isinstance(i, dict) and i for i in node)
    ):
        for index, item in enumerate(node):
            yield from _leaves(item, path + (index,))
    else:
        yield path, node


def _format_path(path: tuple) -> str:
    """Render an override path for an error message: ``a.b[0].c``."""
    text = ""
    for token in path:
        if isinstance(token, int):
            text += f"[{token}]"
        else:
            text += f".{token}" if text else str(token)
    return text


def check_value(rule: _Rule, name: str, value: Any) -> None:
    """Raise :class:`TunableOverrideError` if *value* breaks *rule*."""
    if rule.kind == "bool":
        ok = isinstance(value, bool)
        detail = "must be true or false"
    elif rule.kind == "int":
        ok = (
            isinstance(value, int)
            and not isinstance(value, bool)
            and rule.lo <= value <= rule.hi
        )
        detail = f"must be an integer from {rule.lo} to {rule.hi}"
    elif rule.kind == "float":
        ok = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and rule.lo <= value <= rule.hi
        )
        detail = f"must be a number from {rule.lo} to {rule.hi}"
    elif rule.kind == "enum":
        ok = isinstance(value, str) and value in rule.choices
        detail = f"must be one of {', '.join(sorted(rule.choices))}"
    elif rule.kind == "str":
        ok = isinstance(value, str) and len(value) <= rule.hi
        detail = f"must be a string of at most {int(rule.hi)} characters"
    else:  # int_list
        ok = (
            isinstance(value, list)
            and 1 <= len(value) <= rule.count
            and all(
                isinstance(v, int)
                and not isinstance(v, bool)
                and rule.lo <= v <= rule.hi
                for v in value
            )
        )
        detail = (
            f"must be a list of 1 to {rule.count} integers from {rule.lo} to {rule.hi}"
        )
    if not ok:
        raise TunableOverrideError(f"'{name}' {detail}.")


def validate_tunables(overrides: dict | None) -> dict:
    """Check caller overrides against :data:`TUNABLE_KEYS` and return a copy.

    The overrides use the same shape as the rendered config, so a patch of
    ``process[0].train.steps`` is ``{"config": {"process": [{"train":
    {"steps": 50}}]}}``. Every leaf must sit under ``config.process[0]`` and
    match an allow-list entry, and its value must pass that entry's type and
    range check. This has no route coupling and is safe to run on a preset
    file loaded from anywhere.

    Args:
        overrides: The caller's deep-merge patch, or ``None``.

    Returns:
        A deep copy of *overrides* (``{}`` when none were given).

    Raises:
        TunableOverrideError: If a key is not tunable (the message names it) or
            a value fails its check.
    """
    if not overrides:
        return {}
    if not isinstance(overrides, dict):
        raise TunableOverrideError("config_overrides must be an object.")
    for path, value in _leaves(overrides):
        name = _format_path(path)
        rel = path[3:] if path[:3] == _PROCESS_PREFIX else None
        rule = None
        if rel:
            for key, candidate in TUNABLE_KEYS.items():
                if _matches(_parse_key(key), rel):
                    rule = candidate
                    break
        if rule is None:
            raise TunableOverrideError(f"'{name}' is not a tunable training parameter.")
        check_value(rule, name, value)
    return copy.deepcopy(overrides)


def deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge *patch* into *base*, returning a new dict.

    Merge rules (neither input is mutated):

    - Nested dicts are merged key-by-key.
    - **Lists of dicts** are merged element-wise (paired by index, each pair
      deep-merged), so a patch like ``{"process": [{"train": {"steps": 50}}]}``
      patches ``process[0]`` *in place* and preserves its sibling keys
      (``model``, ``datasets``, …) instead of dropping them. Extra patch
      elements are appended; base elements beyond the patch length are kept.
    - Any other list (scalars or mixed, e.g. ``resolution: [512, 768, 1024]``)
      and any other value type are replaced by the patch value wholesale.

    This makes "full passthrough + override an arbitrary nested field" ergonomic
    without forcing the caller to re-supply a whole ``process`` element.

    Args:
        base: The base mapping.
        patch: The override mapping applied on top of *base*.

    Returns:
        A new dict with *patch* merged into *base*.
    """
    merged: dict = dict(base)
    for key, patch_value in patch.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(patch_value, dict):
            merged[key] = deep_merge(base_value, patch_value)
        elif isinstance(base_value, list) and isinstance(patch_value, list):
            merged[key] = _merge_lists(base_value, patch_value)
        else:
            merged[key] = patch_value
    return merged


def _merge_lists(base_list: list, patch_list: list) -> list:
    """Merge two lists, element-wise iff every paired element is a dict.

    Element-wise dict merging is what makes a single-element ``process`` list
    patchable in place. For scalar/mixed lists (or when any paired element is
    not a dict), the patch list replaces the base list wholesale, the right
    behaviour for value lists like ``resolution``.

    Args:
        base_list: The base list.
        patch_list: The override list.

    Returns:
        A new merged list.
    """
    pair_count = min(len(base_list), len(patch_list))
    all_pairs_dicts = pair_count > 0 and all(
        isinstance(base_list[i], dict) and isinstance(patch_list[i], dict)
        for i in range(pair_count)
    )
    if not all_pairs_dicts:
        return list(patch_list)
    merged = [deep_merge(base_list[i], patch_list[i]) for i in range(pair_count)]
    # Keep base elements beyond the patch; append patch elements beyond the base
    # (only one of these tails is non-empty since pair_count is the min length).
    merged.extend(base_list[pair_count:])
    merged.extend(patch_list[pair_count:])
    return merged


def normalise_trigger_word(word: str | None) -> str:
    """The trigger word as every consumer reads it: surrounding whitespace out.

    The rendered config, the sample prompts and the dataset captions all take
    this value, so none of them can disagree about the word.

    Args:
        word: The trigger word as given, or None.

    Returns:
        The word stripped, or ``""`` when there is none.
    """
    return (word or "").strip()


def _fill_text(text: str, replacements: dict[str, str]) -> str:
    """Replace each token in *text* in one pass over the original string.

    Sequential replace would re-scan text an earlier pass inserted, so a
    trigger word or job name naming a later token (e.g. ``{{DATASET_FOLDER}}``)
    would expand to a host path.
    """
    if not replacements:
        return text
    pattern = "|".join(re.escape(token) for token in replacements)
    return re.sub(pattern, lambda match: replacements[match.group(0)], text)


def _substitute_placeholders(
    value: Any,
    replacements: dict[str, str],
    prompt_replacements: dict[str, str],
    *,
    parent: str | None = None,
    sample_entry: bool = False,
) -> Any:
    """Recursively replace ``{{PLACEHOLDER}}`` tokens in any JSON value.

    Every string leaf takes *replacements*, except the ``prompt`` of a sample
    entry (an item of a ``sample.samples`` list), which takes
    *prompt_replacements* only, at any depth inside that entry. A sample prompt
    is shown unredacted in the live view, so it must not be where a host path
    enters. A ``prompt`` outside a sample entry is an ordinary leaf.

    Args:
        value: A JSON-decoded value (dict, list, str, or scalar).
        replacements: Mapping of placeholder token (``"{{NAME}}"``) to its
            replacement string.
        prompt_replacements: The subset of *replacements* a sample prompt takes.
        parent: The key *value* sits under, if any.
        sample_entry: Whether *value* is, or lies inside, an item of a
            ``sample.samples`` list.

    Returns:
        A new value with all placeholders substituted.
    """
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if sample_entry and key == "prompt" and isinstance(item, str):
                out[key] = _fill_text(item, prompt_replacements)
            elif parent == "sample" and key == "samples" and isinstance(item, list):
                out[key] = [
                    _substitute_placeholders(
                        entry, replacements, prompt_replacements, sample_entry=True
                    )
                    for entry in item
                ]
            else:
                out[key] = _substitute_placeholders(
                    item,
                    replacements,
                    prompt_replacements,
                    parent=key,
                    sample_entry=sample_entry,
                )
        return out
    if isinstance(value, list):
        return [
            _substitute_placeholders(
                item, replacements, prompt_replacements, sample_entry=sample_entry
            )
            for item in value
        ]
    if isinstance(value, str):
        return _fill_text(value, replacements)
    return value


def render_job_config(
    template_path: str,
    *,
    job_name: str,
    training_folder: str,
    sqlite_db_path: str,
    trigger_word: str,
    dataset_dir: str,
    overrides: dict | None = None,
    locked: dict | None = None,
) -> dict:
    """Render the golden config template into a concrete job-config dict.

    Loads the JSON template at *template_path*, deep-merges *overrides* and then
    *locked* on top, and substitutes the five PixlStash placeholders last, so a
    placeholder in an override is filled too. A sample prompt takes only
    ``{{TRIGGER_WORD}}``; the host-path placeholders stay literal in it.

    The substituted placeholders are:

    - ``{{JOB_NAME}}`` → *job_name* (``config.name`` and ``meta.name``)
    - ``{{TRAINING_FOLDER}}`` → *training_folder*
    - ``{{SQLITE_DB_PATH}}`` → *sqlite_db_path*
    - ``{{TRIGGER_WORD}}`` → *trigger_word* (trigger + sample prompts)
    - ``{{DATASET_FOLDER}}`` → *dataset_dir*

    Args:
        template_path: Path to the golden JSON template.
        job_name: Replaces ``{{JOB_NAME}}``.
        training_folder: Replaces ``{{TRAINING_FOLDER}}``.
        sqlite_db_path: Replaces ``{{SQLITE_DB_PATH}}``.
        trigger_word: Replaces ``{{TRIGGER_WORD}}``.
        dataset_dir: Replaces ``{{DATASET_FOLDER}}``.
        overrides: Optional deep-merge patch applied after substitution. It is
            checked by :func:`validate_tunables` first, so only allow-listed
            training parameters can change.
        locked: The recipe's locked keys as a patch (``to_patch`` shape),
            merged last and not checked against the tunables: it comes from a
            shipped preset, never from a caller.

    Returns:
        The rendered config as a plain dict, ready to serialise.

    Raises:
        OSError: If the template cannot be read.
        json.JSONDecodeError: If the template is not valid JSON.
        TunableOverrideError: If *overrides* names a non-tunable key or a value
            outside its range.
    """
    try:
        with open(template_path, encoding="utf-8") as handle:
            template = json.load(handle)
    except OSError as exc:
        logger.error("Failed to read job-config template %s: %s", template_path, exc)
        raise
    except json.JSONDecodeError as exc:
        logger.error("Job-config template %s is not valid JSON: %s", template_path, exc)
        raise

    trigger = normalise_trigger_word(trigger_word)
    replacements = {
        "{{JOB_NAME}}": job_name,
        "{{TRAINING_FOLDER}}": training_folder,
        "{{SQLITE_DB_PATH}}": sqlite_db_path,
        "{{TRIGGER_WORD}}": trigger,
        "{{DATASET_FOLDER}}": dataset_dir,
    }
    # A sample prompt takes the trigger word alone: the host paths stay literal
    # there, since the live view shows prompts unredacted.
    prompt_replacements = {"{{TRIGGER_WORD}}": trigger}

    if overrides:
        template = deep_merge(template, validate_tunables(overrides))
    if locked:
        template = deep_merge(template, locked)

    return _substitute_placeholders(template, replacements, prompt_replacements)
