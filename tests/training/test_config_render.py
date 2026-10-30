"""Tests for the job-config renderer (placeholder substitution + deep merge)."""

from __future__ import annotations

import json
import re

from pixlstash.training import presets, sample_prompts
from pixlstash.training.config_render import (
    _substitute_placeholders,
    deep_merge,
    render_job_config,
)

# The shipped Z-Image Turbo recipe, resolved for 24 pictures on a 24 GB card
# with the prompts an uncaptioned set gets: what a real run renders over the
# template.
_PRESET = presets.get_preset("zimage-turbo")
_RESOLVED = presets.to_patch(
    presets.resolved_values(
        presets.resolve(
            _PRESET,
            subject="person",
            effort="standard",
            fit="auto",
            picture_count=24,
            vram_mb=22_000,
            ram_mb=60_000,
            prompts=sample_prompts.prompt_texts(
                sample_prompts.generate("person", [], [])
            ),
        )
    )
)


def _render(**overrides):
    """Render the recipe's template with stable test values.

    Args:
        **overrides: ``overrides`` is merged over the resolved values, as a
            run's tunable patch.

    Returns:
        The rendered config dict.
    """
    return render_job_config(
        presets.template_path(_PRESET),
        job_name="job-abc",
        training_folder="/work/output",
        sqlite_db_path="/work/job.db",
        trigger_word="ohwx_person",
        dataset_dir="/work/dataset",
        overrides=deep_merge(_RESOLVED, overrides.get("overrides") or {}),
        locked=presets.to_patch(_PRESET["locked"]),
    )


def test_placeholders_substituted_everywhere():
    """All five placeholders are replaced, including inside sample prompts."""
    config = _render()

    assert config["config"]["name"] == "job-abc"
    assert config["meta"]["name"] == "job-abc"

    process = config["config"]["process"][0]
    assert process["training_folder"] == "/work/output"
    assert process["sqlite_db_path"] == "/work/job.db"
    assert process["trigger_word"] == "ohwx_person"
    assert process["datasets"][0]["folder_path"] == "/work/dataset"

    # The written prompts arrive in the patch, after the template, and the
    # trigger word is still substituted inside them: half carry it.
    prompts = [s["prompt"] for s in process["sample"]["samples"]]
    assert len(prompts) == 4
    assert sum("ohwx_person" in prompt for prompt in prompts) == 2
    for prompt in prompts:
        assert "{{TRIGGER_WORD}}" not in prompt


def test_user_text_spelling_a_placeholder_is_never_expanded():
    """A job name or trigger word naming another placeholder stays literal.

    Substitution is a single pass, so text an earlier replacement inserted is
    never scanned for a later placeholder. A user value must not turn into a
    host path.
    """
    config = render_job_config(
        presets.template_path(_PRESET),
        job_name="{{SQLITE_DB_PATH}}",
        training_folder="/work/output",
        sqlite_db_path="/work/job.db",
        trigger_word="{{DATASET_FOLDER}}",
        dataset_dir="/work/dataset",
        overrides=_RESOLVED,
        locked=presets.to_patch(_PRESET["locked"]),
    )

    assert config["config"]["name"] == "{{SQLITE_DB_PATH}}"
    process = config["config"]["process"][0]
    assert process["trigger_word"] == "{{DATASET_FOLDER}}"
    assert process["sqlite_db_path"] == "/work/job.db"
    assert process["datasets"][0]["folder_path"] == "/work/dataset"
    prompts = [s["prompt"] for s in process["sample"]["samples"]]
    assert sum("{{DATASET_FOLDER}}" in prompt for prompt in prompts) == 2
    assert not any("/work/dataset" in prompt for prompt in prompts)


def test_sample_prompts_expand_only_the_trigger_word():
    """A host-path placeholder in a sample prompt stays literal.

    The live view shows sample prompts unredacted, so a prompt must never be
    the place a host path enters. Only the trigger word expands inside one.
    """
    prompts = [
        "ohwx_person in {{TRAINING_FOLDER}}",
        "{{DATASET_FOLDER}} then {{SQLITE_DB_PATH}}",
        "a photo of {{TRIGGER_WORD}}",
        "x {{OUTPUT_FOLDER}} y",
    ]
    config = render_job_config(
        presets.template_path(_PRESET),
        job_name="job-abc",
        training_folder="/work/output",
        sqlite_db_path="/work/job.db",
        trigger_word="ohwx_person",
        dataset_dir="/work/dataset",
        overrides=_RESOLVED,
        locked=deep_merge(
            presets.to_patch(_PRESET["locked"]),
            presets.to_patch({presets.PROMPTS_KEY: prompts}),
        ),
    )

    sample_prompts_out = [
        s["prompt"] for s in config["config"]["process"][0]["sample"]["samples"]
    ]
    assert sample_prompts_out == [
        "ohwx_person in {{TRAINING_FOLDER}}",
        "{{DATASET_FOLDER}} then {{SQLITE_DB_PATH}}",
        "a photo of ohwx_person",
        "x {{OUTPUT_FOLDER}} y",
    ]
    assert "/work" not in json.dumps(sample_prompts_out)


def test_only_a_sample_entrys_prompt_is_limited_to_the_trigger_word():
    """A ``prompt`` key outside ``sample.samples[]`` is an ordinary leaf and
    takes every placeholder, so a config that names one elsewhere still runs."""
    out = _substitute_placeholders(
        {
            "sample": {
                "samples": [
                    {
                        "prompt": "{{TRIGGER_WORD}} {{DATASET_FOLDER}}",
                        "extra": {"prompt": "{{TRAINING_FOLDER}}"},
                        "more": [{"prompt": "{{DATASET_FOLDER}}"}],
                    }
                ]
            },
            "datasets": [{"prompt": "{{DATASET_FOLDER}}"}],
            "prompt": "{{TRAINING_FOLDER}}",
        },
        {
            "{{TRIGGER_WORD}}": "ohwx",
            "{{DATASET_FOLDER}}": "/work/dataset",
            "{{TRAINING_FOLDER}}": "/work/output",
        },
        {"{{TRIGGER_WORD}}": "ohwx"},
    )
    entry = out["sample"]["samples"][0]
    assert entry["prompt"] == "ohwx {{DATASET_FOLDER}}"
    # Deeper inside a sample entry is still a sample prompt.
    assert entry["extra"]["prompt"] == "{{TRAINING_FOLDER}}"
    assert entry["more"][0]["prompt"] == "{{DATASET_FOLDER}}"
    assert out["datasets"][0]["prompt"] == "/work/dataset"
    assert out["prompt"] == "/work/output"


def test_a_padded_trigger_word_renders_as_the_stripped_one():
    """The trigger word is one normalised value everywhere it is rendered.

    A trigger word with surrounding spaces must give the same config as the
    stripped word: the trainer's trigger, the sample prompts, and the dataset
    captions all read one value.
    """

    def render(trigger):
        return render_job_config(
            presets.template_path(_PRESET),
            job_name="job-abc",
            training_folder="/work/output",
            sqlite_db_path="/work/job.db",
            trigger_word=trigger,
            dataset_dir="/work/dataset",
            overrides=_RESOLVED,
            locked=presets.to_patch(_PRESET["locked"]),
        )

    padded = render("  ohwx  ")
    stripped = render("ohwx")
    assert padded == stripped
    assert padded["config"]["process"][0]["trigger_word"] == "ohwx"
    prompts = [s["prompt"] for s in padded["config"]["process"][0]["sample"]["samples"]]
    assert not any("  " in prompt for prompt in prompts)


def test_no_placeholders_remain():
    """No ``{{NAME}}`` placeholder token survives rendering anywhere."""
    config = _render()
    # Re-serialise to JSON and scan for the placeholder pattern. (We can't scan
    # repr(dict) for "}}" because nested-dict closings produce that naturally.)
    serialised = json.dumps(config)
    assert re.search(r"\{\{[A-Z_]+\}\}", serialised) is None


def test_required_invariants_preserved():
    """The non-PixlStash-owned invariants survive rendering unchanged."""
    process = _render()["config"]["process"][0]
    assert process["type"] == "diffusion_trainer"
    assert process["logging"]["use_ui_logger"] is True


def test_every_prompt_samples_at_one_seed_from_step_zero():
    """A pair and a step column compare like with like: one fixed seed for
    every prompt, and a sample before the first step as the "before" column."""
    process = _render()["config"]["process"][0]
    assert isinstance(process["sample"]["seed"], int)
    assert process["sample"]["walk_seed"] is False
    assert process["train"]["skip_first_sample"] is False
    assert process["train"]["disable_sampling"] is False


def test_overrides_deep_merged():
    """A process[0] patch merges in place and preserves sibling keys.

    The ``process`` list is a list of dicts, so it is merged element-wise: the
    patched fields land while every untouched field of process[0] (datasets,
    network, sample, type, logging, …) survives.
    """
    config = _render(
        overrides={
            "config": {
                "process": [
                    {
                        "train": {"steps": 50},
                        "model": {"qtype": "qint8"},
                    }
                ]
            }
        }
    )
    process = config["config"]["process"][0]
    # Patched values landed.
    assert process["train"]["steps"] == 50
    assert process["model"]["qtype"] == "qint8"
    # Sibling keys of the patched nested dicts survive (not wiped).
    assert process["train"]["optimizer"] == "adamw8bit"
    assert process["model"]["arch"] == "zimage:turbo"
    assert process["model"]["assistant_lora_path"]
    # Sibling top-level process keys survive the in-place list merge.
    assert process["type"] == "diffusion_trainer"
    assert process["logging"]["use_ui_logger"] is True
    assert process["datasets"][0]["folder_path"] == "/work/dataset"
    assert len(process["sample"]["samples"]) == 4


def test_list_of_dicts_merged_elementwise():
    """A list whose paired elements are dicts merges element-wise, keeping tails."""
    base = {"items": [{"a": 1, "keep": True}, {"b": 2}]}
    patch = {"items": [{"a": 9}]}
    merged = deep_merge(base, patch)
    # Index 0 deep-merged (a patched, keep retained); index 1 base tail kept.
    assert merged["items"] == [{"a": 9, "keep": True}, {"b": 2}]


def test_deep_merge_does_not_mutate_inputs():
    """deep_merge returns a new dict and leaves both inputs untouched."""
    base = {"a": 1, "nested": {"x": 1, "y": 2}}
    patch = {"nested": {"y": 99, "z": 3}, "b": 2}

    merged = deep_merge(base, patch)

    assert merged == {"a": 1, "nested": {"x": 1, "y": 99, "z": 3}, "b": 2}
    # Inputs unchanged.
    assert base == {"a": 1, "nested": {"x": 1, "y": 2}}
    assert patch == {"nested": {"y": 99, "z": 3}, "b": 2}


def test_deep_merge_replaces_non_dict_values():
    """A non-dict patch value (e.g. a list) replaces the base value wholesale."""
    base = {"resolution": [512, 768], "scalar": 1}
    patch = {"resolution": [1024], "scalar": 2}

    merged = deep_merge(base, patch)

    assert merged["resolution"] == [1024]
    assert merged["scalar"] == 2


def test_no_overrides_leaves_the_resolved_steps():
    """Without overrides the recipe's steps hold: 100 per picture x 24."""
    config = _render()
    assert config["config"]["process"][0]["train"]["steps"] == 2400


def test_locked_keys_are_merged_last():
    """The recipe's locked keys reach the config even where the template has none."""
    process = render_job_config(
        presets.template_path(_PRESET),
        job_name="j",
        training_folder="",
        sqlite_db_path="",
        trigger_word="t",
        dataset_dir="",
        locked=presets.to_patch(_PRESET["locked"]),
    )["config"]["process"][0]
    assert process["model"]["arch"] == "zimage:turbo"
    assert process["model"]["extras_name_or_path"] == "Tongyi-MAI/Z-Image-Turbo"
    assert process["train"]["noise_scheduler"] == "flowmatch"
    template = json.load(open(presets.template_path(_PRESET), encoding="utf-8"))
    assert "arch" not in template["config"]["process"][0]["model"]


def test_training_folder_override_is_refused():
    """A path set through overrides never reaches the rendered config."""
    import pytest

    from pixlstash.training.config_render import TunableOverrideError

    with pytest.raises(TunableOverrideError, match="training_folder"):
        _render(
            overrides={
                "config": {"process": [{"training_folder": "/home/victim/library"}]}
            }
        )


def test_dataset_folder_path_override_is_refused():
    import pytest

    from pixlstash.training.config_render import TunableOverrideError

    with pytest.raises(TunableOverrideError, match="folder_path"):
        _render(
            overrides={
                "config": {
                    "process": [{"datasets": [{"folder_path": "/home/victim/pics"}]}]
                }
            }
        )


def test_allowed_tunable_override_renders():
    """The positive control: an allow-listed key still lands in the config."""
    config = _render(
        overrides={
            "config": {
                "process": [
                    {
                        "train": {"steps": 500, "optimizer": "adamw"},
                        "datasets": [{"resolution": [512, 768]}],
                    }
                ]
            }
        }
    )
    process = config["config"]["process"][0]
    assert process["train"]["steps"] == 500
    assert process["train"]["optimizer"] == "adamw"
    assert process["datasets"][0]["resolution"] == [512, 768]
    assert process["datasets"][0]["folder_path"] == "/work/dataset"


def test_tunable_value_out_of_range_or_wrong_type_is_refused():
    import pytest

    from pixlstash.training.config_render import TunableOverrideError, validate_tunables

    for bad, message in (
        ({"config": {"process": [{"train": {"lr": 5}}]}}, "train.lr"),
        ({"config": {"process": [{"train": {"optimizer": "sgd"}}]}}, "optimizer"),
        ({"config": {"process": [{"train": {"steps": True}}]}}, "train.steps"),
        ({"config": {"process": [{"model": {"qtype": "fp64"}}]}}, "qtype"),
        ({"config": {"process": [{"device": "cuda:1"}]}}, "device"),
        (
            {"config": {"process": [{"model": {"name_or_path": "/evil"}}]}},
            "name_or_path",
        ),
        ({"config": {"process": [{"train": {}}]}}, "train"),
        (
            {"config": {"process": [{"train": {"steps": 5}}, {"train": {"steps": 5}}]}},
            "process\\[1\\]",
        ),
    ):
        with pytest.raises(TunableOverrideError, match=message):
            validate_tunables(bad)
