"""Render the golden ai-toolkit job-config template into a concrete config.

PixlStash owns only a handful of fields in the trainer config (the
``{{PLACEHOLDER}}`` tokens in the template); everything else is passed through
verbatim. This module loads the template, substitutes those placeholders
(including inside ``sample.samples[].prompt``), then deep-merges any caller
overrides on top for full passthrough of arbitrary config fields.

No trainer-specific behaviour beyond the placeholder names lives here; the
adapter (:mod:`pixlstash.training.local_ai_toolkit`) is responsible for
injecting the resolved per-job paths via these arguments and for writing the
result to disk.
"""

from __future__ import annotations

import json
from typing import Any

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


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


def _substitute_placeholders(value: Any, replacements: dict[str, str]) -> Any:
    """Recursively replace ``{{PLACEHOLDER}}`` tokens in any JSON value.

    Replacement is applied to every string leaf, so placeholders are honoured
    wherever they appear in the tree, including inside ``sample.samples[].prompt``.

    Args:
        value: A JSON-decoded value (dict, list, str, or scalar).
        replacements: Mapping of placeholder token (``"{{NAME}}"``) to its
            replacement string.

    Returns:
        A new value with all placeholders substituted.
    """
    if isinstance(value, dict):
        return {
            key: _substitute_placeholders(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_substitute_placeholders(item, replacements) for item in value]
    if isinstance(value, str):
        substituted = value
        for token, replacement in replacements.items():
            if token in substituted:
                substituted = substituted.replace(token, replacement)
        return substituted
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
) -> dict:
    """Render the golden config template into a concrete job-config dict.

    Loads the JSON template at *template_path*, substitutes the five PixlStash
    placeholders, then deep-merges *overrides* on top for full passthrough.

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
        overrides: Optional deep-merge patch applied after substitution.

    Returns:
        The rendered config as a plain dict, ready to serialise.

    Raises:
        OSError: If the template cannot be read.
        json.JSONDecodeError: If the template is not valid JSON.
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

    replacements = {
        "{{JOB_NAME}}": job_name,
        "{{TRAINING_FOLDER}}": training_folder,
        "{{SQLITE_DB_PATH}}": sqlite_db_path,
        "{{TRIGGER_WORD}}": trigger_word,
        "{{DATASET_FOLDER}}": dataset_dir,
    }

    rendered = _substitute_placeholders(template, replacements)

    if overrides:
        rendered = deep_merge(rendered, overrides)

    return rendered
