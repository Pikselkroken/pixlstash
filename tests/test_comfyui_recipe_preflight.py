"""Tests for the recipe pre-flight against ComfyUI's /object_info (Remix v1.9).

The governing property is honesty in both directions: a check we cannot make is
reported as *unchecked*, never as passing and never as missing, because a
spurious "missing model" blocks a run that would have worked.
"""

import pytest

from pixlstash.services.comfyui_recipe_service import (
    MAX_SEED_64,
    MODEL_FILENAME_FIELDS,
    apply_seeds,
    detect_seed_targets,
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
                "note": "not available on this ComfyUI",
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


class TestComboSerialisationShapes:
    """ComfyUI serialises a combo two ways and both are live in one install."""

    def test_v3_combo_options_are_read(self):
        # UpscaleModelLoader already ships this shape; reading only the V1 form
        # would silently stop checking every V3-migrated loader.
        info = {
            "UpscaleModelLoader": {
                "input": {
                    "required": {
                        "model_name": ["COMBO", {"options": ["4x-UltraSharp.pth"]}]
                    }
                }
            }
        }
        ok = {
            "1": {
                "class_type": "UpscaleModelLoader",
                "inputs": {"model_name": "4x-UltraSharp.pth"},
            }
        }
        bad = {
            "1": {
                "class_type": "UpscaleModelLoader",
                "inputs": {"model_name": "gone.pth"},
            }
        }
        assert preflight_prompt(ok, info)["ok"] is True
        assert preflight_prompt(bad, info)["ok"] is False

    def test_a_remote_combo_is_never_checked(self):
        # Its options are fetched by the frontend at runtime, so the embedded
        # list proves nothing and a miss against it would be a false positive.
        info = {
            "LoraLoader": {
                "input": {
                    "required": {
                        "lora_name": [
                            "COMBO",
                            {"options": [], "remote": {"route": "/x"}},
                        ]
                    }
                }
            }
        }
        graph = {
            "1": {"class_type": "LoraLoader", "inputs": {"lora_name": "anything.st"}}
        }
        result = preflight_prompt(graph, info)
        assert result["ok"] is True
        assert result["unchecked_fields"] == 1


class TestFalsePositiveTraps:
    def test_a_windows_authored_path_matches_a_posix_listing(self):
        info = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["SDXL/base.safetensors"], {}]}}
            }
        }
        graph = {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "SDXL\\base.safetensors"},
            }
        }
        assert preflight_prompt(graph, info)["ok"] is True

    def test_a_case_only_mismatch_says_so_rather_than_missing(self):
        info = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["Base.safetensors"], {}]}}
            }
        }
        graph = {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "base.safetensors"},
            }
        }
        result = preflight_prompt(graph, info)
        assert result["ok"] is False
        assert "different case" in result["missing_models"][0]["note"]
        assert "Base.safetensors" in result["missing_models"][0]["note"]

    def test_a_load_image_miss_is_its_own_bucket_not_a_missing_model(self):
        # ComfyUI validates LoadImage by file existence, and the fix is a
        # re-upload, not a download. Calling it a missing model sends the user
        # hunting for something to install.
        info = {"LoadImage": {"input": {"required": {"image": [["present.png"], {}]}}}}
        graph = {"1": {"class_type": "LoadImage", "inputs": {"image": "gone.png"}}}
        result = preflight_prompt(graph, info)
        assert result["ok"] is False
        assert result["missing_models"] == []
        assert result["missing_input_images"][0]["value"] == "gone.png"


class TestSaveImageDetection:
    def test_reports_a_graph_that_writes_an_image(self):
        graph = {"9": {"class_type": "SaveImage", "inputs": {}}}
        assert preflight_prompt(graph, {"SaveImage": {}})["has_save_image"] is True

    def test_reports_a_graph_that_writes_nothing_importable(self):
        graph = {"9": {"class_type": "SaveAnimatedWEBP", "inputs": {}}}
        info = {"SaveAnimatedWEBP": {}}
        assert preflight_prompt(graph, info)["has_save_image"] is False


class TestDetectSeedTargets:
    OBJECT_INFO = {
        "KSampler": {
            "input": {
                "required": {
                    "seed": ["INT", {"control_after_generate": True, "max": 2**64 - 1}],
                    "steps": ["INT", {"max": 100}],
                }
            }
        },
        "RandomNoise": {
            "input": {
                "required": {
                    "noise_seed": [
                        "INT",
                        {"control_after_generate": "randomize", "max": 2**64 - 1},
                    ]
                }
            }
        },
        "PrimitiveInt": {
            "input": {
                "required": {
                    "value": ["INT", {"control_after_generate": "fixed", "max": 2**63}]
                }
            }
        },
        "EmptyLatentImage": {
            "input": {"required": {"width": ["INT", {}], "height": ["INT", {}]}}
        },
    }

    def test_finds_a_seed_by_control_after_generate_not_by_class_name(self):
        graph = {"3": {"class_type": "KSampler", "inputs": {"seed": 7, "steps": 20}}}
        targets = detect_seed_targets(graph, self.OBJECT_INFO)
        assert [(t["node_id"], t["field"]) for t in targets] == [("3", "seed")]

    def test_a_plain_int_input_is_not_a_seed(self):
        graph = {"3": {"class_type": "KSampler", "inputs": {"steps": 20}}}
        assert detect_seed_targets(graph, self.OBJECT_INFO) == []

    def test_the_string_form_of_the_flag_counts(self):
        graph = {"7": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42}}}
        assert len(detect_seed_targets(graph, self.OBJECT_INFO)) == 1

    def test_a_width_primitive_is_never_scanned_directly(self):
        # PrimitiveInt carries control_after_generate unconditionally, so
        # scanning it directly would randomize the image dimensions. The
        # shipped Flux2-Klein-t2i template has exactly this wiring.
        graph = {
            "68": {"class_type": "PrimitiveInt", "inputs": {"value": 1024}},
            "66": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": ["68", 0], "height": ["68", 0]},
            },
        }
        assert detect_seed_targets(graph, self.OBJECT_INFO) == []

    def test_a_seed_primitive_reached_through_a_link_is_patchable(self):
        graph = {
            "3": {"class_type": "KSampler", "inputs": {"seed": ["10", 0]}},
            "10": {"class_type": "PrimitiveInt", "inputs": {"value": 5}},
        }
        targets = detect_seed_targets(graph, self.OBJECT_INFO)
        assert [(t["node_id"], t["field"]) for t in targets] == [("10", "value")]

    def test_unknown_class_yields_nothing(self):
        graph = {"3": {"class_type": "SomeCustomSampler", "inputs": {"seed": 1}}}
        assert detect_seed_targets(graph, self.OBJECT_INFO) == []

    def test_tolerates_junk(self):
        assert detect_seed_targets({}, self.OBJECT_INFO) == []
        assert detect_seed_targets(None, self.OBJECT_INFO) == []
        assert detect_seed_targets({"3": "junk"}, self.OBJECT_INFO) == []


class TestApplySeeds:
    def test_pins_a_fixed_seed_everywhere(self):
        graph = {
            "3": {"class_type": "KSampler", "inputs": {"seed": 1}},
            "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": 2}},
        }
        targets = [
            {"node_id": "3", "field": "seed", "max": MAX_SEED_64},
            {"node_id": "7", "field": "noise_seed", "max": MAX_SEED_64},
        ]
        assert apply_seeds(graph, targets, 12345) == 2
        assert graph["3"]["inputs"]["seed"] == 12345
        assert graph["7"]["inputs"]["noise_seed"] == 12345

    def test_a_random_seed_respects_the_declared_ceiling(self):
        graph = {"3": {"class_type": "KSampler", "inputs": {"seed": 1}}}
        targets = [{"node_id": "3", "field": "seed", "max": 10}]
        for _ in range(20):
            apply_seeds(graph, targets, None)
            assert 0 <= graph["3"]["inputs"]["seed"] <= 10

    def test_a_fixed_seed_above_the_ceiling_is_clamped(self):
        graph = {"3": {"class_type": "KSampler", "inputs": {"seed": 1}}}
        apply_seeds(graph, [{"node_id": "3", "field": "seed", "max": 100}], 5000)
        assert graph["3"]["inputs"]["seed"] == 100

    def test_a_stale_target_is_skipped_not_fatal(self):
        graph = {"3": {"class_type": "KSampler", "inputs": {"seed": 1}}}
        assert apply_seeds(graph, [{"node_id": "99", "field": "seed"}], 5) == 0
        assert apply_seeds(graph, None, 5) == 0
