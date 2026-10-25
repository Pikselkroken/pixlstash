"""Tests for the job-config renderer (placeholder substitution + deep merge)."""

from __future__ import annotations

import json
import os
import re

from pixlstash.training.config_render import deep_merge, render_job_config

# The golden template shipped with PixlStash.
_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "pixlstash",
    "data",
    "ai-toolkit-jobs",
    "built-in",
    "zimage-turbo-lora.json",
)


def _render(**overrides):
    """Render the golden template with stable test values.

    Args:
        **overrides: Passed straight through as the ``overrides`` argument.

    Returns:
        The rendered config dict.
    """
    return render_job_config(
        _TEMPLATE_PATH,
        job_name="job-abc",
        training_folder="/work/output",
        sqlite_db_path="/work/job.db",
        trigger_word="ohwx_person",
        dataset_dir="/work/dataset",
        overrides=overrides.get("overrides"),
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

    # Trigger word must be substituted inside each sample prompt.
    prompts = [s["prompt"] for s in process["sample"]["samples"]]
    assert len(prompts) == 3
    for prompt in prompts:
        assert "ohwx_person" in prompt
        assert "{{TRIGGER_WORD}}" not in prompt


def test_user_text_spelling_a_placeholder_is_never_expanded():
    """A job name or trigger word naming another placeholder stays literal.

    Substitution is a single pass, so text an earlier replacement inserted is
    never scanned for a later placeholder. A user value must not turn into a
    host path.
    """
    config = render_job_config(
        _TEMPLATE_PATH,
        job_name="{{SQLITE_DB_PATH}}",
        training_folder="/work/output",
        sqlite_db_path="/work/job.db",
        trigger_word="{{DATASET_FOLDER}}",
        dataset_dir="/work/dataset",
    )

    assert config["config"]["name"] == "{{SQLITE_DB_PATH}}"
    process = config["config"]["process"][0]
    assert process["trigger_word"] == "{{DATASET_FOLDER}}"
    assert process["sqlite_db_path"] == "/work/job.db"
    assert process["datasets"][0]["folder_path"] == "/work/dataset"
    for sample in process["sample"]["samples"]:
        assert sample["prompt"].startswith("{{DATASET_FOLDER}}, ")


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
    assert len(process["sample"]["samples"]) == 3


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


def test_no_overrides_leaves_template_steps():
    """Without overrides, the template's own train.steps is preserved."""
    config = _render()
    assert config["config"]["process"][0]["train"]["steps"] == 3000


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
