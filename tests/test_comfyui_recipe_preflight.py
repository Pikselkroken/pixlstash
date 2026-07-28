"""Tests for the recipe pre-flight against ComfyUI's /object_info (Remix v1.9).

The governing property is honesty in both directions: a check we cannot make is
reported as *unchecked*, never as passing and never as missing, because a
spurious "missing model" blocks a run that would have worked.
"""

import pytest

from pixlstash.services.comfyui_recipe_service import (
    MODEL_FILENAME_FIELDS,
    format_prompt_rejection,
    preflight_prompt,
    sanitize_prompt_graph,
    unchecked_preflight,
)

GRAPH = {
    "3": {"class_type": "KSampler", "inputs": {"seed": 1, "model": ["4", 0]}},
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    },
    "5": {"class_type": "LoraLoader", "inputs": {"lora_name": "detail.safetensors"}},
}

OBJECT_INFO = {
    "KSampler": {"input": {"required": {"seed": ["INT", {"default": 0}]}}},
    "CheckpointLoaderSimple": {
        "input": {"required": {"ckpt_name": [["sd_xl_base_1.0.safetensors"], {}]}}
    },
    "LoraLoader": {
        "input": {"required": {"lora_name": [["detail.safetensors", "other.pt"], {}]}}
    },
}


class TestPreflightPasses:
    def test_clean_graph(self):
        result = preflight_prompt(GRAPH, OBJECT_INFO)
        assert result["ok"] is True
        assert result["checked"] is True
        assert result["missing_node_classes"] == []
        assert result["missing_models"] == []

    def test_a_linked_loader_input_is_not_treated_as_a_filename(self):
        graph = {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": ["9", 0]},
            }
        }
        result = preflight_prompt(graph, OBJECT_INFO)
        assert result["ok"] is True
        assert result["missing_models"] == []


class TestPreflightFinds:
    def test_missing_node_class(self):
        graph = dict(GRAPH)
        graph["7"] = {"class_type": "UltimateSDUpscale", "inputs": {}}
        result = preflight_prompt(graph, OBJECT_INFO)
        assert result["ok"] is False
        assert result["missing_node_classes"] == ["UltimateSDUpscale"]

    def test_a_missing_class_is_reported_once_not_per_node(self):
        graph = {
            "1": {"class_type": "Nope", "inputs": {}},
            "2": {"class_type": "Nope", "inputs": {}},
        }
        assert preflight_prompt(graph, OBJECT_INFO)["missing_node_classes"] == ["Nope"]

    def test_missing_model_filename(self):
        graph = {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "deleted_model.safetensors"},
            }
        }
        result = preflight_prompt(graph, OBJECT_INFO)
        assert result["ok"] is False
        assert result["missing_models"] == [
            {
                "node_id": "4",
                "class_type": "CheckpointLoaderSimple",
                "field": "ckpt_name",
                "value": "deleted_model.safetensors",
            }
        ]

    def test_filename_comparison_is_exact_not_basename(self):
        # ComfyUI's combo values are subfolder-qualified; a bare basename is a
        # genuine mismatch and must be reported, not silently accepted.
        info = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["SDXL/base.safetensors"], {}]}}
            }
        }
        graph = {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "base.safetensors"},
            }
        }
        assert preflight_prompt(graph, info)["ok"] is False

    def test_a_missing_class_does_not_also_report_its_filenames(self):
        # Without a spec there is nothing to compare against; claiming the model
        # is missing too would be a guess.
        graph = {
            "4": {
                "class_type": "SomeCustomLoader",
                "inputs": {"ckpt_name": "x.safetensors"},
            }
        }
        result = preflight_prompt(graph, {})
        assert result["missing_node_classes"] == ["SomeCustomLoader"]
        assert result["missing_models"] == []


class TestPreflightDoesNotGuess:
    def test_an_unenumerated_field_is_counted_unchecked_not_missing(self):
        info = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": ["STRING", {}]}}
            }
        }
        graph = {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "anything.safetensors"},
            }
        }
        result = preflight_prompt(graph, info)
        assert result["ok"] is True
        assert result["missing_models"] == []
        assert result["unchecked_fields"] == 1

    def test_an_empty_combo_list_is_unchecked_not_everything_missing(self):
        info = {
            "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [[], {}]}}}
        }
        graph = {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "x.safetensors"},
            }
        }
        result = preflight_prompt(graph, info)
        assert result["ok"] is True
        assert result["unchecked_fields"] == 1

    def test_a_node_class_not_in_the_loader_map_is_never_filename_checked(self):
        assert "SaveImage" not in MODEL_FILENAME_FIELDS
        graph = {
            "9": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "not_a_model"},
            }
        }
        result = preflight_prompt(graph, {"SaveImage": {}})
        assert result["ok"] is True
        assert result["unchecked_fields"] == 0

    def test_an_optional_group_field_is_read_too(self):
        info = {
            "VAELoader": {
                "input": {"optional": {"vae_name": [["good.safetensors"], {}]}}
            }
        }
        graph = {
            "6": {"class_type": "VAELoader", "inputs": {"vae_name": "gone.safetensors"}}
        }
        assert preflight_prompt(graph, info)["ok"] is False

    def test_tolerates_a_junk_graph(self):
        assert preflight_prompt({}, OBJECT_INFO)["ok"] is True
        assert preflight_prompt({"3": "junk"}, OBJECT_INFO)["ok"] is True
        assert preflight_prompt(None, OBJECT_INFO)["ok"] is True


class TestUncheckedPreflight:
    def test_unreachable_comfyui_is_not_a_failure(self):
        result = unchecked_preflight("Could not reach ComfyUI at http://x")
        assert result["checked"] is False
        # ok stays True: the only thing we know is the check did not run.
        assert result["ok"] is True
        assert "Could not reach" in result["error"]


class TestSanitizePromptGraph:
    def test_drops_non_node_entries(self):
        graph = {
            "3": {"class_type": "KSampler", "inputs": {}},
            "extra_pnginfo": {"anything": 1},
            "pixlstash_output_nodes": ["9"],
        }
        assert set(sanitize_prompt_graph(graph)) == {"3"}

    def test_returns_a_copy_not_the_original_nodes(self):
        graph = {"3": {"class_type": "KSampler", "inputs": {"seed": 1}}}
        clean = sanitize_prompt_graph(graph)
        clean["3"]["inputs"]["seed"] = 999
        assert graph["3"]["inputs"]["seed"] == 1

    def test_tolerates_junk(self):
        assert sanitize_prompt_graph({}) == {}
        assert sanitize_prompt_graph(None) == {}


class TestFormatPromptRejection:
    def test_renders_node_errors(self):
        body = {
            "error": {
                "type": "prompt_outputs_failed_validation",
                "message": "Prompt outputs failed validation",
                "details": "",
            },
            "node_errors": {
                "4": {
                    "class_type": "CheckpointLoaderSimple",
                    "errors": [
                        {
                            "type": "value_not_in_list",
                            "message": "Value not in list",
                            "details": "ckpt_name: 'gone.safetensors' not in [...]",
                        }
                    ],
                }
            },
        }
        text = format_prompt_rejection(body)
        assert "Prompt outputs failed validation" in text
        assert "CheckpointLoaderSimple (node 4)" in text
        assert "gone.safetensors" in text

    def test_error_only_body(self):
        body = {"error": {"message": "Bad prompt", "details": "node 3"}}
        assert format_prompt_rejection(body) == "Bad prompt (node 3)"

    @pytest.mark.parametrize("body", [None, "text", {}, {"node_errors": {}}, []])
    def test_unrecognised_body_degrades_to_none(self, body):
        assert format_prompt_rejection(body) is None

    def test_tolerates_partial_node_error_shapes(self):
        body = {"node_errors": {"4": {"errors": [{"type": "custom"}]}}}
        assert format_prompt_rejection(body) == "node (node 4): custom"
