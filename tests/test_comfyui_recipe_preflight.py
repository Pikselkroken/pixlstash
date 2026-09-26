"""Tests for the recipe pre-flight against ComfyUI's /object_info (Remix v1.9).

The governing property is honesty in both directions: a check we cannot make is
reported as *unchecked*, never as passing and never as missing, because a
spurious "missing model" blocks a run that would have worked.
"""

import json

import pytest

from pixlstash.services.comfyui_recipe_service import (
    MAX_SEED_64,
    MODEL_FILENAME_FIELDS,
    advertised_model_names,
    apply_adapter,
    apply_filename_swap,
    apply_lora_chain,
    apply_model_swap,
    apply_seeds,
    collect_node_classes,
    detect_lora_targets,
    detect_model_targets,
    detect_seed_targets,
    format_prompt_rejection,
    bypass_node,
    insert_adapter,
    model_filename_fields,
    pass_label,
    plan_lora_chain,
    plan_lora_insertion,
    preflight_prompt,
    read_lora_chain,
    read_lora_chain_untyped,
    sanitize_prompt_graph,
    unchecked_preflight,
)
from pixlstash.utils.comfyui_utilities import extract_recipe_extras

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


class TestCollectNodeClasses:
    """The consent disclosure (R3): what the owner is actually approving.

    A node *count* is not an answer to "what will this run"; the class list is.
    """

    def test_lists_each_class_once_sorted(self):
        graph = {
            "9": {"class_type": "SaveImage", "inputs": {}},
            "3": {"class_type": "KSampler", "inputs": {}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {}},
        }
        assert collect_node_classes(graph) == [
            "CLIPTextEncode",
            "KSampler",
            "SaveImage",
        ]

    def test_sorts_case_insensitively_rather_than_by_byte(self):
        # ASCII order would put every capitalised class before "reroute"; a
        # reader scanning for an unfamiliar node needs one alphabet, not two.
        graph = {
            "1": {"class_type": "reroute", "inputs": {}},
            "2": {"class_type": "SaveImage", "inputs": {}},
        }
        assert collect_node_classes(graph) == ["reroute", "SaveImage"]

    def test_ignores_entries_that_are_not_nodes(self):
        graph = {
            "3": {"class_type": "KSampler", "inputs": {}},
            "extra_pnginfo": {"anything": 1},
            "4": {"inputs": {}},
            "5": {"class_type": "", "inputs": {}},
            "6": "junk",
        }
        assert collect_node_classes(graph) == ["KSampler"]

    def test_tolerates_junk(self):
        assert collect_node_classes({}) == []
        assert collect_node_classes(None) == []


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

    def test_reports_a_graph_saved_by_the_pixlstash_node(self):
        # The PixlStash saver uploads into the vault itself instead of writing
        # a file. Refusing it as "produces nothing importable" blocked variants
        # on every workflow built around the ComfyUI-PixlStash pack.
        graph = {"9": {"class_type": "PixlStashPictureSaver", "inputs": {}}}
        info = {"PixlStashPictureSaver": {}}
        assert preflight_prompt(graph, info)["has_save_image"] is True

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


class TestDetectLoraTargets:
    """#1310: a slot is found by its field name, whatever class carries it."""

    def test_every_core_lora_loader_spelling_is_a_slot(self):
        graph = {
            "1": {"class_type": "LoraLoader", "inputs": {"lora_name": "a.safetensors"}},
            "2": {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {"lora_name": "b.safetensors"},
            },
            # A pack this module has never been told about, which is the point.
            "3": {
                "class_type": "LoraLoader|somepack",
                "inputs": {"lora_name": "c.safetensors"},
            },
        }
        targets = detect_lora_targets(graph)
        assert [t["node_id"] for t in targets] == ["1", "2", "3"]
        assert {t["by"] for t in targets} == {"filename"}

    def test_a_pixlstash_loader_is_a_digest_slot(self):
        graph = {
            "1": {
                "class_type": "PixlStashAdapterLoader",
                "inputs": {"adapter_sha256": "a" * 64, "strength_model": 0.8},
            }
        }
        assert detect_lora_targets(graph) == [
            {
                "node_id": "1",
                "class_type": "PixlStashAdapterLoader",
                "field": "adapter_sha256",
                "value": "a" * 64,
                "by": "digest",
                "strengths": {"model": 0.8},
            }
        ]

    def test_a_graph_with_no_lora_loader_has_no_slot(self):
        # The control: the same reader does find the one GRAPH carries.
        assert [t["node_id"] for t in detect_lora_targets(GRAPH)] == ["5"]
        assert detect_lora_targets({"4": GRAPH["4"]}) == []
        assert detect_lora_targets(None) == []

    def test_a_stackers_numbered_slots_are_each_their_own(self):
        graph = {
            "1": {
                "class_type": "CR LoRA Stack",
                "inputs": {
                    "lora_name_1": "a.safetensors",
                    "lora_name_2": "b.safetensors",
                },
            }
        }
        assert [t["field"] for t in detect_lora_targets(graph)] == [
            "lora_name_1",
            "lora_name_2",
        ]

    def test_each_numbered_slot_reports_its_own_strengths(self):
        """The numbers have to line up, or a slot is reported at another's weight."""
        graph = {
            "1": {
                "class_type": "SomeLoRAStacker",
                "inputs": {
                    "lora_name_1": "a.safetensors",
                    "strength_model_1": 0.25,
                    "lora_name_2": "b.safetensors",
                    "strength_model_2": 0.75,
                },
            }
        }
        assert [t["strengths"] for t in detect_lora_targets(graph)] == [
            {"model": 0.25},
            {"model": 0.75},
        ]

    def test_a_model_only_loader_reports_its_single_strength(self):
        """``strength`` is the one-widget spelling, so it fills ``model``."""
        graph = {
            "1": {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {"lora_name": "a.safetensors", "strength": 0.5},
            }
        }
        assert [t["strengths"] for t in detect_lora_targets(graph)] == [{"model": 0.5}]

    def test_a_wired_strength_is_left_out_rather_than_reported_as_a_link(self):
        graph = {
            "1": {
                "class_type": "LoraLoader",
                "inputs": {"lora_name": "a.safetensors", "strength_model": ["9", 0]},
            }
        }
        assert [t["strengths"] for t in detect_lora_targets(graph)] == [{}]

    def test_a_non_finite_strength_is_refused_rather_than_rendered(self):
        """`json.loads` takes `NaN`; the response renders with allow_nan=False.

        So a crafted file carrying one would 500 every route that hands these
        dicts back - to a share-token holder, on a read they can just make.
        """
        graph = json.loads(
            '{"1": {"class_type": "LoraLoader", "inputs": '
            '{"lora_name": "a.safetensors", "strength_model": NaN, '
            '"strength_clip": Infinity}}}'
        )
        strengths = detect_lora_targets(graph)[0]["strengths"]
        assert strengths == {}
        json.dumps(strengths, allow_nan=False)  # would raise if one got through


class TestRecipeExtras:
    """The negative prompt and the settings, off an API graph."""

    def test_the_settings_come_from_any_node_that_names_one(self):
        """A split-sampler graph, the shape the shipped Flux2 templates use.

        The step count is on the scheduler, the sampler name on its own
        selector and the CFG on the guider. Reading the sampler alone answers
        with one field out of five for PixlStash's own workflows.
        """
        graph = {
            "61": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "62": {
                "class_type": "Flux2Scheduler",
                "inputs": {"steps": 20, "width": 1024, "height": 1024},
            },
            "63": {
                "class_type": "CFGGuider",
                "inputs": {"cfg": 3.5, "negative": ["7", 0]},
            },
            "64": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {"sampler": ["61", 0], "guider": ["63", 0]},
            },
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry"}},
        }
        extras = extract_recipe_extras(graph)
        # `width` and `height` were already in this fixture and went unread
        # until the Recipe tab needed a Size row (#1313); they come off the
        # scheduler here, which is exactly the point the test is making.
        assert extras["settings"] == {
            "steps": 20,
            "cfg": 3.5,
            "sampler_name": "euler",
            "width": 1024,
            "height": 1024,
        }
        # The guider is a sampler class, so the negative walk still starts.
        assert extras["negative_prompt"] == "blurry"

    def test_a_wired_or_non_finite_setting_is_left_out(self):
        graph = json.loads(
            '{"3": {"class_type": "KSampler", "inputs": '
            '{"cfg": NaN, "steps": Infinity, "denoise": ["9", 0], '
            '"sampler_name": "euler", "scheduler": true}}}'
        )
        settings = extract_recipe_extras(graph)["settings"]
        assert settings == {"sampler_name": "euler"}
        json.dumps(settings, allow_nan=False)  # would raise if one got through

    def test_a_graph_with_nothing_to_say_says_nothing(self):
        assert extract_recipe_extras({}) == {"negative_prompt": None, "settings": {}}
        assert extract_recipe_extras({"1": "not a node"}) == {
            "negative_prompt": None,
            "settings": {},
        }

    def test_each_field_is_taken_at_its_own_type(self):
        """The value's kind is not the field's type.

        A graph is attacker-authorable, so a number field written as text and a
        text field written as a number both arrive looking plausible. Neither
        is a setting, and passing them on puts the graph's author in charge of
        what a client's formatter receives.
        """
        graph = {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "steps": "twenty",
                    "sampler_name": 12345,
                    "scheduler": ["9", 0],
                    "cfg": 7,
                    "denoise": 1,
                },
            }
        }
        # Only the two number fields, and both as numbers.
        assert extract_recipe_extras(graph)["settings"] == {"cfg": 7.0, "denoise": 1.0}

    def test_an_integer_too_large_for_a_float_costs_one_field_not_the_block(self):
        """`math.isfinite` OVERFLOWS on such an int, so the guard must not call it.

        Raised out of the loop it would have cost every other setting as well.
        """
        graph = {
            "3": {
                "class_type": "KSampler",
                "inputs": {"steps": 10**400, "cfg": 7.5, "denoise": 10**400},
            }
        }
        settings = extract_recipe_extras(graph)["settings"]
        assert settings["cfg"] == 7.5
        assert "denoise" not in settings, "a float field cannot hold it"
        assert settings["steps"] == 10**400, "an int field can, and renders"
        json.dumps(settings, allow_nan=False)

    def test_the_walk_does_not_cross_sides_at_a_node_carrying_both(self):
        """A node with a generic `conditioning` AND a named side input.

        Trying the generic key first sends the negative chain to the positive
        prompt - the exact failure the side parameter exists to prevent, and
        one that a graph shaped like a ControlNet applier produces.
        """
        graph = {
            "1": {
                "class_type": "KSampler",
                "inputs": {"positive": ["2", 0], "negative": ["X", 0]},
            },
            "X": {
                "class_type": "ControlNetApply",
                "inputs": {"conditioning": ["2", 0], "negative": ["3", 0]},
            },
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "POSITIVE"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "NEGATIVE"}},
        }
        assert extract_recipe_extras(graph)["negative_prompt"] == "NEGATIVE"

    def test_a_node_spelling_its_digest_twice_is_still_one_slot(self):
        graph = {
            "1": {
                "class_type": "PixlStashAdapterLoader",
                "inputs": {"adapter_sha256": "a" * 64, "lora_sha256": "a" * 64},
            }
        }
        assert len(detect_lora_targets(graph)) == 1

    def test_a_wired_slot_is_left_alone(self):
        graph = {
            "1": {"class_type": "LoraLoader", "inputs": {"lora_name": ["9", 0]}},
        }
        assert detect_lora_targets(graph) == []


class TestApplyAdapter:
    """#1310: each slot written the way its own loader reads it, or refused."""

    INFO = {
        "LoraLoader": {
            "input": {
                "required": {
                    "lora_name": [["style/subject-v2.safetensors", "other.pt"], {}]
                }
            }
        }
    }
    ADAPTER = {"sha256": "b" * 64, "filenames": ["subject-v2.safetensors"]}

    def _graph(self):
        return {
            "1": {"class_type": "LoraLoader", "inputs": {"lora_name": "old.st"}},
            "2": {
                "class_type": "PixlStashAdapterLoader",
                "inputs": {"adapter_sha256": "a" * 64},
            },
        }

    def test_a_filename_slot_takes_the_name_this_comfyui_lists(self):
        graph = self._graph()
        targets = detect_lora_targets(graph)
        assert apply_adapter(graph, targets, self.ADAPTER, self.INFO) == 2
        # Matched on the basename: the shelf counts from its folder, ComfyUI
        # from its own, and the file is the same file.
        assert graph["1"]["inputs"]["lora_name"] == "style/subject-v2.safetensors"
        assert graph["2"]["inputs"]["adapter_sha256"] == "b" * 64

    def test_a_windows_listing_matches_a_posix_shelf_path(self):
        graph = {"1": self._graph()["1"]}
        info = {
            "LoraLoader": {
                "input": {
                    "required": {"lora_name": [["style\\subject-v2.safetensors"], {}]}
                }
            }
        }
        adapter = {"sha256": "b" * 64, "filenames": ["style/subject-v2.safetensors"]}
        assert apply_adapter(graph, detect_lora_targets(graph), adapter, info) == 1
        assert graph["1"]["inputs"]["lora_name"] == "style\\subject-v2.safetensors"

    def test_a_lora_this_comfyui_does_not_have_is_refused(self):
        graph = {"1": self._graph()["1"]}
        adapter = {"sha256": "b" * 64, "filenames": ["elsewhere.safetensors"]}
        with pytest.raises(LookupError, match="not on the ComfyUI"):
            apply_adapter(graph, detect_lora_targets(graph), adapter, self.INFO)
        assert graph["1"]["inputs"]["lora_name"] == "old.st"

    def test_two_files_of_that_name_are_refused_rather_than_picked_between(self):
        graph = {"1": self._graph()["1"]}
        info = {
            "LoraLoader": {
                "input": {
                    "required": {
                        "lora_name": [
                            ["a/subject-v2.safetensors", "b/subject-v2.safetensors"],
                            {},
                        ]
                    }
                }
            }
        }
        with pytest.raises(LookupError, match="2 different files"):
            apply_adapter(graph, detect_lora_targets(graph), self.ADAPTER, info)

    def test_an_unenumerable_loader_is_refused_not_guessed_at(self):
        graph = {"1": self._graph()["1"]}
        # The node is there; its file list is not enumerated.
        info = {"LoraLoader": {"input": {"required": {"lora_name": ["STRING", {}]}}}}
        with pytest.raises(LookupError, match="will not guess"):
            apply_adapter(graph, detect_lora_targets(graph), self.ADAPTER, info)

    def test_a_loader_this_comfyui_lacks_is_named_as_a_missing_node(self):
        """Not "does not say which files": the node pack itself is what is missing."""
        graph = {"1": self._graph()["1"]}
        with pytest.raises(LookupError, match="has no LoraLoader node"):
            apply_adapter(graph, detect_lora_targets(graph), self.ADAPTER, {})

    def test_a_digest_slot_needs_nothing_from_comfyui(self):
        graph = {"2": self._graph()["2"]}
        assert apply_adapter(graph, detect_lora_targets(graph), self.ADAPTER, {}) == 1
        assert graph["2"]["inputs"]["adapter_sha256"] == "b" * 64

    def test_a_stale_target_is_skipped_not_fatal(self):
        graph = {"2": self._graph()["2"]}
        stale = [{"node_id": "99", "field": "adapter_sha256", "by": "digest"}]
        assert apply_adapter(graph, stale, self.ADAPTER, {}) == 0
        assert apply_adapter(graph, None, self.ADAPTER, {}) == 0


def _loader_spec(outputs, lora_names, clip=True):
    required = {
        "model": ["MODEL", {}],
        "lora_name": [lora_names, {}],
        "strength_model": ["FLOAT", {"default": 1.0}],
    }
    if clip:
        required["clip"] = ["CLIP", {}]
        required["strength_clip"] = ["FLOAT", {"default": 1.0}]
    return {"input": {"required": required}, "output": outputs}


class TestLoraInsertion:
    """#1376: a loader spliced in after the model source, typed by object_info."""

    ADAPTER = {"sha256": "b" * 64, "filenames": ["subject-v2.safetensors"]}
    INFO = {
        "CheckpointLoaderSimple": {"output": ["MODEL", "CLIP", "VAE"]},
        "UnetLoaderGGUF": {"output": ["MODEL"]},
        "DualCLIPLoader": {"output": ["CLIP"]},
        "ModelSamplingFlux": {"output": ["MODEL"]},
        "CLIPTextEncode": {"output": ["CONDITIONING"]},
        "KSampler": {"output": ["LATENT"]},
        "BasicGuider": {"output": ["GUIDER"]},
        "LoraLoader": _loader_spec(["MODEL", "CLIP"], ["subject-v2.safetensors"]),
        "LoraLoaderModelOnly": _loader_spec(
            ["MODEL"], ["subject-v2.safetensors"], clip=False
        ),
    }

    def _checkpoint_graph(self):
        return {
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x"}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1]}},
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                },
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
            },
        }

    def test_a_checkpoint_graph_rewires_every_model_and_clip_reader(self):
        graph = self._checkpoint_graph()
        info = {**self.INFO, "VAEDecode": {"output": ["IMAGE"]}}
        plan = plan_lora_insertion(graph, info)
        assert plan["model"] == {
            "node_id": "4",
            "class_type": "CheckpointLoaderSimple",
            "output": 0,
        }
        assert plan["clip"]["output"] == 1
        # Both text encoders, not only the first: the negative prompt without
        # the LoRA's CLIP is a different run.
        assert [(r["node_id"], r["field"]) for r in plan["rewires"]] == [
            ("6", "clip"),
            ("7", "clip"),
            ("3", "model"),
        ]

        added = insert_adapter(graph, plan, self.ADAPTER, info)
        assert added == {"node_id": "9", "class_type": "LoraLoader"}
        loader = graph["9"]["inputs"]
        assert loader["model"] == ["4", 0] and loader["clip"] == ["4", 1]
        assert loader["lora_name"] == "subject-v2.safetensors"
        assert loader["strength_model"] == 1.0 and loader["strength_clip"] == 1.0
        assert graph["3"]["inputs"]["model"] == ["9", 0]
        assert graph["6"]["inputs"]["clip"] == ["9", 1]
        assert graph["7"]["inputs"]["clip"] == ["9", 1]
        # The VAE is not a LoRA's business and stays on the checkpoint.
        assert graph["8"]["inputs"]["vae"] == ["4", 2]

    def test_a_gguf_chain_with_its_own_clip_loader_splices_after_the_unet(self):
        graph = {
            "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "f.gguf"}},
            "2": {"class_type": "DualCLIPLoader", "inputs": {}},
            "3": {"class_type": "ModelSamplingFlux", "inputs": {"model": ["1", 0]}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0]}},
            "5": {"class_type": "BasicGuider", "inputs": {"model": ["3", 0]}},
        }
        plan = plan_lora_insertion(graph, self.INFO)
        # Right after the loader, not after the model patch: the patch reads
        # the LoRA'd model, and the guider still reads the patch.
        assert plan["model"]["node_id"] == "1" and plan["clip"]["node_id"] == "2"
        insert_adapter(graph, plan, self.ADAPTER, self.INFO)
        assert graph["3"]["inputs"]["model"] == ["6", 0]
        assert graph["5"]["inputs"]["model"] == ["3", 0]
        assert graph["4"]["inputs"]["clip"] == ["6", 1]
        assert graph["6"]["inputs"]["clip"] == ["2", 0]

    def test_a_graph_reading_no_clip_gets_a_model_only_loader(self):
        graph = {
            "1": {"class_type": "UnetLoaderGGUF", "inputs": {}},
            "2": {"class_type": "KSampler", "inputs": {"model": ["1", 0]}},
            "3": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0]}},
        }
        plan = plan_lora_insertion(graph, self.INFO)
        assert plan["clip"] is None
        added = insert_adapter(graph, plan, self.ADAPTER, self.INFO)
        assert added["class_type"] == "LoraLoaderModelOnly"
        assert "clip" not in graph[added["node_id"]]["inputs"]
        # Several readers of one MODEL output, all moved.
        assert graph["2"]["inputs"]["model"] == graph["3"]["inputs"]["model"]
        assert graph["2"]["inputs"]["model"] == [added["node_id"], 0]

    def test_two_model_sources_are_refused_rather_than_picked_between(self):
        graph = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
            "2": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
            "3": {"class_type": "KSampler", "inputs": {"model": ["1", 0]}},
            "4": {"class_type": "KSampler", "inputs": {"model": ["2", 0]}},
        }
        with pytest.raises(LookupError, match="loads 2 models"):
            plan_lora_insertion(graph, self.INFO)
        # The control: one sampler fewer and there is exactly one source.
        del graph["4"]
        assert plan_lora_insertion(graph, self.INFO)["model"]["node_id"] == "1"

    def test_a_node_this_comfyui_lacks_is_refused_not_read_as_untyped(self):
        graph = self._checkpoint_graph()
        info = {k: v for k, v in self.INFO.items() if k != "CheckpointLoaderSimple"}
        with pytest.raises(LookupError, match="no CheckpointLoaderSimple node"):
            plan_lora_insertion(graph, info)

    def test_a_graph_with_no_model_has_nowhere_to_put_one(self):
        graph = {"1": {"class_type": "KSampler", "inputs": {"latent": ["2", 0]}}}
        graph["2"] = {"class_type": "CLIPTextEncode", "inputs": {}}
        with pytest.raises(LookupError, match="could not find the model"):
            plan_lora_insertion(graph, self.INFO)

    def test_the_pixlstash_loader_goes_in_only_where_comfyui_lacks_the_file(self):
        info = {
            **self.INFO,
            "PixlStashAdapterLoader": {
                "input": {
                    "required": {
                        "model": ["MODEL", {}],
                        "adapter_kind": [["— Any —", "lora"], {}],
                        "adapter_sha256": ["STRING", {"default": ""}],
                    },
                    "optional": {
                        # Optional on the real node, where ComfyUI's own loader
                        # requires it: one class covers both shapes.
                        "clip": ["CLIP", {}],
                        "strength_model": ["FLOAT", {"default": 1.0}],
                    },
                },
                "output": ["MODEL", "CLIP", "STRING"],
            },
        }
        # The file is on this ComfyUI: its own loader, which needs no pack.
        graph = self._checkpoint_graph()
        plan = plan_lora_insertion(graph, info)
        assert insert_adapter(graph, plan, self.ADAPTER, info)["class_type"] == (
            "LoraLoader"
        )

        graph = self._checkpoint_graph()
        elsewhere = {"sha256": "c" * 64, "filenames": ["elsewhere.safetensors"]}
        added = insert_adapter(graph, plan, elsewhere, info)
        assert added["class_type"] == "PixlStashAdapterLoader"
        inputs = graph[added["node_id"]]["inputs"]
        assert inputs["adapter_sha256"] == "c" * 64
        assert inputs["adapter_kind"] == "— Any —"
        assert inputs["clip"] == ["4", 1]

        # Neither: refused, saying why the core loader could not.
        graph = self._checkpoint_graph()
        with pytest.raises(LookupError, match="not on this ComfyUI.*ComfyUI-PixlStash"):
            insert_adapter(graph, plan, elsewhere, self.INFO)
        assert "9" not in graph

    def test_a_graph_that_no_longer_reads_the_plan_is_refused_whole(self):
        graph = self._checkpoint_graph()
        plan = plan_lora_insertion(graph, self.INFO)
        graph["7"]["inputs"]["clip"] = ["99", 0]
        with pytest.raises(LookupError, match="Node 7 no longer reads its CLIP"):
            insert_adapter(graph, plan, self.ADAPTER, self.INFO)
        # Nothing half done: no loader, the first reader untouched.
        assert "9" not in graph and graph["6"]["inputs"]["clip"] == ["4", 1]

    @pytest.mark.parametrize(
        "loader",
        [
            # A loader whose file is wired in reads as no slot at all.
            {"class_type": "LoraLoader", "inputs": {"lora_name": ["8", 0]}},
            # rgthree's stacker holds its slots as dicts.
            {
                "class_type": "Power Lora Loader (rgthree)",
                "inputs": {"lora_1": {"on": True, "lora": "a.st", "strength": 1}},
            },
            # easy-loraStack numbers the name itself, which no lora_name rule
            # matches.
            {"class_type": "easy loraStack", "inputs": {"lora_1_name": "a.st"}},
            # A prompt-tag loader: nothing about its inputs says "lora" at all.
            {
                "class_type": "ImpactWildcardEncode",
                "inputs": {"text": "a cat <lora:styleA:0.8>"},
            },
            # A slot holding None reads as no slot, and the node is still one.
            {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": None}},
        ],
    )
    def test_a_lora_loaded_where_no_slot_is_seen_does_not_stop_an_insert(self, loader):
        """A loader always goes in the MODEL path, whatever else loads a LoRA.

        It applies alongside that node, never instead of it, so there is no
        reason to refuse; #1376 used to, and nearly nothing could take one.
        """
        graph = self._checkpoint_graph()
        graph["5"] = loader
        # VAEDecode: the wired `lora_name` reads node 8, so its output is typed.
        info = {**self.INFO, "VAEDecode": {"output": ["IMAGE"]}}
        plan = plan_lora_insertion(graph, info)
        assert plan["model"]["node_id"] == "4"
        assert ("3", "model") in {(r["node_id"], r["field"]) for r in plan["rewires"]}

    def _sampler_spec(self):
        return {
            "input": {
                "required": {
                    "model": ["MODEL", {}],
                    "seed": ["INT", {"default": 0}],
                    "positive": ["CONDITIONING", {}],
                    "negative": ["CONDITIONING", {}],
                }
            },
            "output": ["LATENT"],
        }

    def test_a_seed_node_this_comfyui_lacks_does_not_stop_an_insert(self):
        """The sampler says its seed is an INT, so that link is no model.

        The single MODEL path from the checkpoint to the sampler is still there
        to follow; refusing here was "cannot tell where a LoRA would go" over a
        number.
        """
        graph = self._checkpoint_graph()
        graph["20"] = {"class_type": "Seed (rgthree)", "inputs": {"seed": 42}}
        graph["3"]["inputs"]["seed"] = ["20", 0]
        info = {**self.INFO, "KSampler": self._sampler_spec()}
        plan = plan_lora_insertion(graph, info)
        assert plan["model"]["node_id"] == "4"
        assert ("3", "model") in {(r["node_id"], r["field"]) for r in plan["rewires"]}

    def test_a_model_loader_this_comfyui_lacks_is_typed_by_its_reader(self):
        """The sampler's `model` input says MODEL, so the unknown node is the source."""
        graph = self._checkpoint_graph()
        graph["20"] = {"class_type": "SomePackUnetLoader", "inputs": {"name": "x"}}
        graph["3"]["inputs"]["model"] = ["20", 0]
        info = {**self.INFO, "KSampler": self._sampler_spec()}
        plan = plan_lora_insertion(graph, info)
        assert plan["model"]["node_id"] == "20"

    def test_a_link_neither_end_can_type_is_still_refused(self):
        """An unknown node read by an input nobody declares may be the model."""
        graph = self._checkpoint_graph()
        graph["20"] = {"class_type": "MysteryNode", "inputs": {}}
        graph["21"] = {"class_type": "OtherMystery", "inputs": {"thing": ["20", 0]}}
        with pytest.raises(
            LookupError, match="no MysteryNode node.*hands on the model"
        ):
            plan_lora_insertion(graph, self.INFO)

    def test_a_stacker_in_the_model_path_reads_the_inserted_loader(self):
        """It sits after the model source, so it is one of the readers rewired."""
        graph = self._checkpoint_graph()
        graph["5"] = {
            "class_type": "Power Lora Loader (rgthree)",
            "inputs": {
                "model": ["4", 0],
                "clip": ["4", 1],
                "lora_1": {"on": True, "lora": "a.st", "strength": 1},
            },
        }
        graph["3"]["inputs"]["model"] = ["5", 0]
        info = {
            **self.INFO,
            "Power Lora Loader (rgthree)": {"output": ["MODEL", "CLIP"]},
        }
        plan = plan_lora_insertion(graph, info)
        assert plan["model"]["node_id"] == "4"
        rewired = {(r["node_id"], r["field"]) for r in plan["rewires"]}
        assert {("5", "model"), ("5", "clip")} <= rewired
        assert ("3", "model") not in rewired

    @pytest.mark.parametrize(
        "loader_class, kind, reader_class, field",
        [
            # The upscale pass after decoding: a model, and nothing to do with
            # the MODEL path a LoRA goes on.
            (
                "UpscaleModelLoader",
                "UPSCALE_MODEL",
                "ImageUpscaleWithModel",
                "upscale_model",
            ),
            ("WanVideoModelLoader", "WANVIDEOMODEL", "WanVideoSampler", "model"),
        ],
    )
    def test_another_kind_of_model_does_not_stop_an_insert(
        self, loader_class, kind, reader_class, field
    ):
        """Only MODEL is patched, so only the MODEL path matters."""
        graph = self._checkpoint_graph()
        graph["20"] = {"class_type": loader_class, "inputs": {}}
        graph["21"] = {"class_type": reader_class, "inputs": {field: ["20", 0]}}
        info = {
            **self.INFO,
            loader_class: {"output": [kind]},
            reader_class: {"output": ["IMAGE"]},
            "VAEDecode": {"output": ["IMAGE"]},
        }
        plan = plan_lora_insertion(graph, info)
        assert plan["model"]["node_id"] == "4"
        assert "21" not in {r["node_id"] for r in plan["rewires"]}

    def test_a_branch_no_output_reads_is_not_a_second_model(self):
        """A leftover UNET loader wired into nothing never runs in ComfyUI."""
        graph = self._checkpoint_graph()
        graph["9"] = {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}}
        graph["20"] = {"class_type": "UnetLoaderGGUF", "inputs": {}}
        graph["21"] = {
            "class_type": "ModelSamplingFlux",
            "inputs": {"model": ["20", 0]},
        }
        info = {
            **self.INFO,
            "VAEDecode": {"output": ["IMAGE"]},
            "SaveImage": {"output": [], "output_node": True},
        }
        assert plan_lora_insertion(graph, info)["model"]["node_id"] == "4"
        # The control: without `output_node` nothing says #20 is dead.
        info["SaveImage"] = {"output": []}
        with pytest.raises(LookupError, match="loads 2 models"):
            plan_lora_insertion(graph, info)

    def test_a_spec_that_does_not_say_what_a_node_hands_on_is_refused(self):
        """An output list too short hides exactly the chain the refusals look for."""
        graph = self._checkpoint_graph()
        info = {**self.INFO, "VAEDecode": {"output": ["IMAGE"]}}
        info["CheckpointLoaderSimple"] = {"output": ["MODEL"]}
        with pytest.raises(
            LookupError, match="does not say what CheckpointLoaderSimple"
        ):
            plan_lora_insertion(graph, info)

    def test_a_clip_source_that_reads_the_model_will_not_take_a_loader(self):
        """Splicing in front of both would wire the loader into its own input."""
        graph = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
            "2": {"class_type": "ClipFromModel", "inputs": {"model": ["1", 0]}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0]}},
            "4": {"class_type": "KSampler", "inputs": {"model": ["1", 0]}},
        }
        info = {**self.INFO, "ClipFromModel": {"output": ["CLIP"]}}
        with pytest.raises(LookupError, match="hands out this workflow's CLIP"):
            plan_lora_insertion(graph, info)

    def test_a_loader_this_comfyui_spells_differently_is_not_wired_blind(self):
        """Its outputs were checked; its inputs are the other half of the wiring."""
        info = {
            **self.INFO,
            "LoraLoader": {
                "input": {"required": {"lora_name": [["subject-v2.safetensors"], {}]}},
                "output": ["MODEL", "CLIP"],
            },
        }
        graph = self._checkpoint_graph()
        plan = plan_lora_insertion(graph, info)
        with pytest.raises(LookupError, match="takes no model input"):
            insert_adapter(graph, plan, self.ADAPTER, info)
        assert "9" not in graph

    def test_the_sentence_reads_in_node_order_not_string_order(self):
        graph = self._checkpoint_graph()
        graph["10"] = {"class_type": "KSampler", "inputs": {"model": ["4", 0]}}
        info = {**self.INFO, "VAEDecode": {"output": ["IMAGE"]}}
        plan = plan_lora_insertion(graph, info)
        assert [r["node_id"] for r in plan["rewires"] if r["type"] == "MODEL"] == [
            "3",
            "10",
        ]


class TestBypassNode:
    """#1463: a loader taken back out again, which is the insertion's inverse."""

    INFO = {
        "CheckpointLoaderSimple": {"output": ["MODEL", "CLIP", "VAE"]},
        "CLIPTextEncode": {"output": ["CONDITIONING"]},
        "KSampler": {"output": ["LATENT"]},
        "LoraLoader": _loader_spec(["MODEL", "CLIP"], ["subject-v2.safetensors"]),
    }

    def _graph(self, clip_wired=True):
        loader_inputs = {
            "lora_name": "gone.safetensors",
            "strength_model": 1.0,
            "model": ["4", 0],
        }
        if clip_wired:
            loader_inputs["clip"] = ["4", 1]
        return {
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x"}},
            "5": {"class_type": "LoraLoader", "inputs": loader_inputs},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["5", 1]}},
            "3": {
                "class_type": "KSampler",
                "inputs": {"model": ["5", 0], "positive": ["6", 0]},
            },
        }

    def test_every_consumer_reads_what_the_loader_read(self):
        """The whole point: the chain closes over the gap the node leaves."""
        graph = self._graph()
        bypass_node(graph, "5", self.INFO)
        assert "5" not in graph
        # MODEL and CLIP each answered by the loader's own input of that type,
        # so the sampler and the text encoder both read the checkpoint.
        assert graph["3"]["inputs"]["model"] == ["4", 0]
        assert graph["6"]["inputs"]["clip"] == ["4", 1]
        # Nothing else moved.
        assert graph["3"]["inputs"]["positive"] == ["6", 0]

    def test_an_output_with_nothing_to_take_its_place_refuses_whole(self):
        """A CLIP reader over a loader whose own CLIP is not wired.

        Rewiring what it could and dropping the node would leave the text
        encoder connected to nothing, which ComfyUI refuses after the run is
        queued - so the graph is left exactly as it was and the run keeps its
        refusal.
        """
        graph = self._graph(clip_wired=False)
        before = json.loads(json.dumps(graph))
        with pytest.raises(LookupError, match="nothing to put in its place"):
            bypass_node(graph, "5", self.INFO)
        assert graph == before

    def test_a_class_this_comfyui_does_not_describe_refuses(self):
        """Unknown outputs means unknown wiring; guessing would be the bug."""
        graph = self._graph()
        info = {key: spec for key, spec in self.INFO.items() if key != "LoraLoader"}
        with pytest.raises(LookupError, match="does not say what"):
            bypass_node(graph, "5", info)
        assert "5" in graph

    def test_a_node_that_is_not_there_refuses(self):
        with pytest.raises(LookupError, match="not in this graph"):
            bypass_node(self._graph(), "99", self.INFO)


class TestLoraChain:
    """#1478: the loaders between the model and the sampler, read and rewired whole.

    Every link here is typed by ``object_info``. The sampler reads its model
    on ``model1`` and ``model2`` and the text encoders on ``conditioner``, so
    an implementation guessing consumers from input names cannot pass.
    """

    INFO = {
        "CheckpointLoaderSimple": {"output": ["MODEL", "CLIP", "VAE"]},
        "UnetLoaderGGUF": {"output": ["MODEL"]},
        "PromptEncoder": {"output": ["CONDITIONING"]},
        "TwinSampler": {"output": ["LATENT"]},
        "LoraLoader": _loader_spec(
            ["MODEL", "CLIP"],
            [
                "a.safetensors",
                "b.safetensors",
                "c.safetensors",
                "styles/Skin-Detail-XL.safetensors",
            ],
        ),
        "LoraLoaderModelOnly": _loader_spec(
            ["MODEL"],
            ["a.safetensors", "styles/Skin-Detail-XL.safetensors"],
            clip=False,
        ),
    }
    NEW = {"sha256": "d" * 64, "filenames": ["skin-detail-xl.safetensors"]}

    @staticmethod
    def _loader(name, strength, model, clip):
        return {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": name,
                "strength_model": strength,
                "strength_clip": strength,
                "model": model,
                "clip": clip,
            },
        }

    def _graph(self, loaders=("a", "b", "c")):
        """Checkpoint #4, then loaders #10, #11, #12 …, then the readers."""
        graph = {
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x"}}
        }
        model, clip = ["4", 0], ["4", 1]
        for index, name in enumerate(loaders):
            node_id = str(10 + index)
            graph[node_id] = self._loader(
                f"{name}.safetensors", 0.5 + index / 10, model, clip
            )
            model, clip = [node_id, 0], [node_id, 1]
        graph["6"] = {"class_type": "PromptEncoder", "inputs": {"conditioner": clip}}
        graph["8"] = {"class_type": "PromptEncoder", "inputs": {"conditioner": clip}}
        graph["7"] = {
            "class_type": "TwinSampler",
            "inputs": {
                "model1": model,
                "model2": model,
                "positive": ["6", 0],
                "negative": ["8", 0],
            },
        }
        return graph

    def _edit(self, graph, entries, info=None):
        info = info or self.INFO
        chain = read_lora_chain(graph, info)
        plan = plan_lora_chain(graph, chain, entries, info)
        apply_lora_chain(graph, plan, info)
        return plan

    def test_the_chain_is_read_in_the_order_it_applies(self):
        chain = read_lora_chain(self._graph(), self.INFO)
        assert [loader["node_id"] for loader in chain["loaders"]] == ["10", "11", "12"]
        assert chain["model_source"]["node_id"] == "4"
        assert {(s["node_id"], s["field"]) for s in chain["sinks"]} == {
            ("7", "model1"),
            ("7", "model2"),
            ("6", "conditioner"),
            ("8", "conditioner"),
        }
        assert chain["sink_summary"] == (
            "TwinSampler #7 reads model · 2 text encoders read clip"
        )

    def test_deleting_the_middle_loader_closes_the_chain_over_it(self):
        graph = self._graph()
        plan = self._edit(graph, [{"node_id": "10"}, {"node_id": "12"}])
        assert "11" not in graph
        # The survivor after the gap reads the one before it, on both chains.
        assert graph["12"]["inputs"]["model"] == ["10", 0]
        assert graph["12"]["inputs"]["clip"] == ["10", 1]
        # The sampler and both encoders read the last survivor.
        assert graph["7"]["inputs"]["model1"] == ["12", 0]
        assert graph["7"]["inputs"]["model2"] == ["12", 0]
        assert graph["6"]["inputs"]["conditioner"] == ["12", 1]
        assert graph["8"]["inputs"]["conditioner"] == ["12", 1]
        assert [c["text"] for c in plan["changes"]] == ["Loader #11 deleted: b"]

    def test_deleting_the_last_loader_moves_every_reader_to_the_survivor(self):
        graph = self._graph()
        plan = self._edit(graph, [{"node_id": "10"}, {"node_id": "11"}])
        assert "12" not in graph
        assert graph["7"]["inputs"]["model1"] == ["11", 0]
        assert graph["7"]["inputs"]["model2"] == ["11", 0]
        assert graph["6"]["inputs"]["conditioner"] == ["11", 1]
        assert graph["8"]["inputs"]["conditioner"] == ["11", 1]
        assert plan["changes"][-1] == {
            "kind": "rewired",
            "node_id": "7",
            "text": "#7 TwinSampler and 2 text encoders rewired",
        }

    def test_reordering_two_loaders_rewires_every_link_and_keeps_their_ids(self):
        graph = self._graph()
        widgets = {n: dict(graph[n]["inputs"]) for n in ("10", "11", "12")}
        plan = self._edit(
            graph, [{"node_id": "10"}, {"node_id": "12"}, {"node_id": "11"}]
        )
        links = {
            (node_id, field): value
            for node_id, node in graph.items()
            for field, value in node["inputs"].items()
            if isinstance(value, list)
        }
        assert links == {
            ("10", "model"): ["4", 0],
            ("10", "clip"): ["4", 1],
            ("12", "model"): ["10", 0],
            ("12", "clip"): ["10", 1],
            ("11", "model"): ["12", 0],
            ("11", "clip"): ["12", 1],
            ("6", "conditioner"): ["11", 1],
            ("8", "conditioner"): ["11", 1],
            ("7", "model1"): ["11", 0],
            ("7", "model2"): ["11", 0],
            ("7", "positive"): ["6", 0],
            ("7", "negative"): ["8", 0],
        }
        # A move is a rewire and nothing else: each loader keeps its own file
        # and strengths under its own id.
        for node_id, before in widgets.items():
            for field in ("lora_name", "strength_model", "strength_clip"):
                assert graph[node_id]["inputs"][field] == before[field]
        assert plan["changes"][0]["text"] == "#11 and #12 swapped: c now applies first"

    def test_adding_to_a_graph_with_no_clip_source_uses_the_model_only_loader(self):
        graph = {
            "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "f.gguf"}},
            "7": {
                "class_type": "TwinSampler",
                "inputs": {"model1": ["1", 0], "model2": ["1", 0]},
            },
        }
        chain = read_lora_chain(graph, self.INFO)
        assert chain["loaders"] == [] and chain["clip_source"] is None
        plan = self._edit(
            graph, [{"node_id": None, "adapter": self.NEW, "strength": 0.3}]
        )
        assert graph["8"]["class_type"] == "LoraLoaderModelOnly"
        assert "clip" not in graph["8"]["inputs"]
        # Matched case-folded on the basename and written in ComfyUI's spelling.
        assert graph["8"]["inputs"]["lora_name"] == "styles/Skin-Detail-XL.safetensors"
        assert graph["8"]["inputs"]["strength_model"] == 0.3
        assert graph["8"]["inputs"]["model"] == ["1", 0]
        assert graph["7"]["inputs"]["model1"] == ["8", 0]
        assert graph["7"]["inputs"]["model2"] == ["8", 0]
        assert [c["kind"] for c in plan["changes"]] == ["added", "rewired"]
        assert plan["changes"][0]["text"] == (
            "A loader added for skin-detail-xl at 0.30"
        )

    def test_add_delete_and_reorder_back_is_the_original_graph(self):
        original = self._graph(loaders=("a", "b"))
        graph = json.loads(json.dumps(original))
        self._edit(
            graph,
            [
                {"node_id": "11"},
                {"node_id": "10"},
                {"node_id": None, "adapter": self.NEW, "strength": 0.3},
            ],
        )
        assert graph != original
        assert graph["12"]["inputs"]["model"] == ["10", 0]
        self._edit(graph, [{"node_id": "10"}, {"node_id": "11"}])
        assert graph == original

    def test_a_strength_moves_the_clip_strength_only_when_they_were_equal(self):
        graph = self._graph(loaders=("a", "b"))
        graph["11"]["inputs"]["strength_clip"] = 0.2
        plan = self._edit(
            graph,
            [{"node_id": "10", "strength": 0.9}, {"node_id": "11", "strength": 0.4}],
        )
        assert graph["10"]["inputs"]["strength_model"] == 0.9
        assert graph["10"]["inputs"]["strength_clip"] == 0.9
        assert graph["11"]["inputs"]["strength_model"] == 0.4
        assert graph["11"]["inputs"]["strength_clip"] == 0.2
        assert [c["text"] for c in plan["changes"]] == [
            "#10 a from 0.50 to 0.90",
            "#11 b from 0.60 to 0.40",
        ]

    def test_nothing_changed_plans_no_change(self):
        graph = self._graph()
        chain = read_lora_chain(graph, self.INFO)
        entries = [{"node_id": n, "strength": None} for n in ("10", "11", "12")]
        assert plan_lora_chain(graph, chain, entries, self.INFO)["changes"] == []

    @pytest.mark.parametrize(
        "entries, message",
        [
            ([{"node_id": "99"}], "no LoRA loader #99"),
            ([{"node_id": "10"}, {"node_id": "10"}], "listed twice"),
        ],
    )
    def test_an_unknown_or_repeated_loader_is_refused(self, entries, message):
        graph = self._graph()
        chain = read_lora_chain(graph, self.INFO)
        with pytest.raises(LookupError, match=message):
            plan_lora_chain(graph, chain, entries, self.INFO)

    def test_a_refused_apply_leaves_the_graph_as_it_was(self):
        graph = self._graph()
        chain = read_lora_chain(graph, self.INFO)
        plan = plan_lora_chain(graph, chain, [{"node_id": "10"}], self.INFO)
        before = json.loads(json.dumps(graph))
        info = {k: v for k, v in self.INFO.items() if k != "LoraLoader"}
        with pytest.raises(LookupError):
            apply_lora_chain(graph, plan, info)
        assert graph == before

    def test_a_stacker_is_an_ordinary_node_the_chain_goes_in_front_of(self):
        """Not edited, not refused: the chain runs from the source to it."""
        graph = self._graph(loaders=("a",))
        graph["10"]["inputs"]["lora_name_2"] = "b.safetensors"
        chain = read_lora_chain(graph, self.INFO)
        assert chain["loaders"] == []
        assert chain["model_source"]["node_id"] == "4"
        assert {s["node_id"] for s in chain["sinks"]} == {"10"}

    def test_a_prompt_tag_node_does_not_stop_the_chain_being_edited(self):
        graph = self._graph(loaders=("a",))
        graph["6"]["inputs"]["text"] = "a cat <lora:style:0.8>"
        chain = read_lora_chain(graph, self.INFO)
        assert [loader["node_id"] for loader in chain["loaders"]] == ["10"]

    def test_a_lora_node_this_comfyui_lacks_is_left_out_of_the_chain(self):
        """Its wiring cannot be read, so it is not moved; the chain still reads."""
        graph = self._graph(loaders=("a",))
        graph["68"] = {
            "class_type": "UninstalledLoraThing",
            "inputs": {"lora_name": "hero.safetensors", "clip": ["10", 1]},
        }
        chain = read_lora_chain(graph, self.INFO)
        assert [loader["node_id"] for loader in chain["loaders"]] == ["10"]

    def test_a_lora_node_that_takes_no_model_is_left_out_of_the_chain(self):
        """A character prompt builder: a LoRA slot, a CLIP in, no model."""
        graph = self._graph(loaders=("a",))
        graph["68"] = {
            "class_type": "LoRACharacterPromptBuilder",
            "inputs": {"lora_name": "hero.safetensors", "clip": ["10", 1]},
        }
        info = {
            **self.INFO,
            "LoRACharacterPromptBuilder": {
                "input": {"required": {"clip": ["CLIP", {}]}},
                "output": ["STRING"],
            },
        }
        chain = read_lora_chain(graph, info)
        assert [loader["node_id"] for loader in chain["loaders"]] == ["10"]
        # A CLIP reader of the chain's end like any other.
        assert "68" in {s["node_id"] for s in chain["sinks"]}

    def _two_pass(self):
        """Loaders #10, #11 for both passes, then #12 on the hires pass only.

        Base pass #3 reads #11 straight; hires pass #15 reads #12. The encoders
        read #11's CLIP, and #12's CLIP output is read by nothing, the way a
        LoraLoader on a hires branch is usually wired.
        """
        graph = self._graph(loaders=("a", "b"))
        del graph["7"]
        graph["12"] = self._loader("c.safetensors", 0.7, ["11", 0], ["11", 1])
        for node_id, title, model in (
            ("3", "Base pass", ["11", 0]),
            ("15", "Hires pass", ["12", 0]),
        ):
            graph[node_id] = {
                "class_type": "TwinSampler",
                "inputs": {"model1": model, "positive": ["6", 0]},
                "_meta": {"title": title},
            }
        return graph

    def _edit_lanes(self, graph, entries, lanes):
        chain = read_lora_chain(graph, self.INFO)
        plan = plan_lora_chain(graph, chain, entries, self.INFO, lanes)
        apply_lora_chain(graph, plan, self.INFO)
        return plan

    def test_a_fork_reads_as_a_trunk_and_one_lane_per_pass(self):
        chain = read_lora_chain(self._two_pass(), self.INFO)
        assert [loader["node_id"] for loader in chain["loaders"]] == ["10", "11"]
        # Every reader of the trunk's model is a lane; the trunk keeps its CLIP.
        assert {(s["node_id"], s["type"]) for s in chain["sinks"]} == {
            ("6", "CLIP"),
            ("8", "CLIP"),
        }
        lanes = chain["lanes"]
        assert [lane["pass"] for lane in lanes] == [
            {"node_id": "3", "class_type": "TwinSampler", "title": "Base pass"},
            {"node_id": "15", "class_type": "TwinSampler", "title": "Hires pass"},
        ]
        assert [[x["node_id"] for x in lane["loaders"]] for lane in lanes] == [
            [],
            ["12"],
        ]
        assert [(s["node_id"], s["field"]) for s in lanes[0]["sinks"]] == [
            ("3", "model1")
        ]
        assert [(s["node_id"], s["field"]) for s in lanes[1]["sinks"]] == [
            ("15", "model1")
        ]
        assert chain["branch_note"] is None

    def test_a_straight_chain_has_no_lanes(self):
        assert read_lora_chain(self._graph(), self.INFO)["lanes"] == []

    def test_one_edit_per_pass_rewires_each_lane_off_the_trunk(self):
        graph = self._two_pass()
        plan = self._edit_lanes(
            graph,
            [{"node_id": "10", "strength": 0.9}],
            [
                [{"node_id": None, "adapter": self.NEW, "strength": 0.3}],
                [{"node_id": "11"}],
            ],
        )
        new = str(max(int(n) for n in graph))
        assert "12" not in graph
        # The base lane has nothing reading a CLIP, so its new loader has none.
        assert graph[new]["class_type"] == "LoraLoaderModelOnly"
        assert graph[new]["inputs"]["model"] == ["10", 0]
        assert graph["3"]["inputs"]["model1"] == [new, 0]
        # #11 crossed the fork: only the hires pass reads it now.
        assert graph["11"]["inputs"]["model"] == ["10", 0]
        assert graph["11"]["inputs"]["clip"] == ["10", 1]
        assert graph["15"]["inputs"]["model1"] == ["11", 0]
        # The encoders follow the trunk's CLIP end, which is #10 now.
        assert graph["6"]["inputs"]["conditioner"] == ["10", 1]
        assert graph["8"]["inputs"]["conditioner"] == ["10", 1]
        assert [c["text"] for c in plan["changes"][:4]] == [
            "Loader #12 deleted: c",
            "A loader added for skin-detail-xl at 0.30, on Base pass only, as "
            "LoraLoaderModelOnly: nothing on that side reads a CLIP, so it does "
            "not change the prompt",
            "#11 b moved to Hires pass only; nothing there reads its CLIP, so it "
            "no longer changes the prompt",
            "#10 a from 0.50 to 0.90",
        ]

    def test_a_loader_moved_into_the_trunk_reaches_both_passes_and_back(self):
        original = self._two_pass()
        graph = json.loads(json.dumps(original))
        plan = self._edit_lanes(
            graph,
            [{"node_id": "10"}, {"node_id": "11"}, {"node_id": "12"}],
            [[], []],
        )
        assert graph["3"]["inputs"]["model1"] == ["12", 0]
        assert graph["15"]["inputs"]["model1"] == ["12", 0]
        assert graph["6"]["inputs"]["conditioner"] == ["12", 1]
        assert plan["changes"][0]["text"] == (
            "#12 c moved before the fork: both passes get it"
        )
        # Read again, #12 is the trunk's end and both lanes are empty; moving it
        # back to the hires pass is the workflow it came from.
        chain = read_lora_chain(graph, self.INFO)
        assert [loader["node_id"] for loader in chain["loaders"]] == ["10", "11", "12"]
        self._edit_lanes(
            graph, [{"node_id": "10"}, {"node_id": "11"}], [[], [{"node_id": "12"}]]
        )
        assert graph == original

    def test_lanes_left_out_keep_every_pass_as_read(self):
        graph = self._two_pass()
        chain = read_lora_chain(graph, self.INFO)
        plan = plan_lora_chain(graph, chain, [{"node_id": "10"}], self.INFO)
        assert [c["text"] for c in plan["changes"]][0] == "Loader #11 deleted: b"
        assert plan["lanes"][1]["order"] == ["12"]

    def test_lanes_that_do_not_match_the_chain_are_refused(self):
        graph = self._two_pass()
        chain = read_lora_chain(graph, self.INFO)
        with pytest.raises(LookupError, match="goes 2 ways"):
            plan_lora_chain(graph, chain, [], self.INFO, [[]])

    def test_a_fork_further_down_a_lane_ends_that_lane_and_says_why(self):
        """#12's model goes to the hires sampler AND to loader #13."""
        graph = self._two_pass()
        graph["13"] = self._loader("a.safetensors", 1.0, ["12", 0], ["12", 1])
        graph["16"] = {"class_type": "TwinSampler", "inputs": {"model1": ["13", 0]}}
        chain = read_lora_chain(graph, self.INFO)
        assert [x["node_id"] for x in chain["lanes"][1]["loaders"]] == ["12"]
        note = chain["branch_note"]
        assert note.startswith("The chain stops at #12 LoraLoader, because its model")
        assert "goes 2 ways" in note and "#13 LoraLoader is past the branch" in note

    def test_a_lane_add_says_where_its_clip_goes(self):
        """#12's CLIP feeds encoder #9, so a new hires loader carries one too."""
        graph = self._two_pass()
        graph["9"] = {
            "class_type": "PromptEncoder",
            "inputs": {"conditioner": ["12", 1]},
        }
        plan = self._edit_lanes(
            graph,
            [{"node_id": "10"}, {"node_id": "11"}],
            [[], [{"node_id": "12"}, {"node_id": None, "adapter": self.NEW}]],
        )
        new = str(max(int(n) for n in graph))
        assert graph[new]["class_type"] == "LoraLoader"
        assert graph[new]["inputs"]["clip"] == ["12", 1]
        assert graph["9"]["inputs"]["conditioner"] == [new, 1]
        assert plan["changes"][0]["text"] == (
            "A loader added for skin-detail-xl at 1.00, its model on Hires pass "
            "only; its CLIP goes to #9 PromptEncoder"
        )

    def test_a_guider_and_a_scheduler_on_one_model_are_one_pass(self):
        """SamplerCustomAdvanced's shape: a straight chain, not a fork."""
        info = {
            **self.INFO,
            "BasicGuider": {"output": ["GUIDER"]},
            "BasicScheduler": {"output": ["SIGMAS"]},
            "SamplerCustomAdvanced": {"output": ["LATENT"]},
        }
        graph = {
            "1": {"class_type": "UnetLoaderGGUF", "inputs": {}},
            "10": {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {
                    "lora_name": "a.safetensors",
                    "strength_model": 1.0,
                    "model": ["1", 0],
                },
            },
            "22": {"class_type": "BasicGuider", "inputs": {"model": ["10", 0]}},
            "17": {"class_type": "BasicScheduler", "inputs": {"model": ["10", 0]}},
            "30": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {"guider": ["22", 0], "sigmas": ["17", 0]},
            },
        }
        chain = read_lora_chain(graph, info)
        assert chain["lanes"] == []
        assert [s["node_id"] for s in chain["sinks"]] == ["17", "22"]

    def test_a_nested_fork_names_the_lane_by_its_own_sampler(self):
        graph = self._two_pass()
        graph["13"] = self._loader("a.safetensors", 1.0, ["12", 0], ["12", 1])
        graph["16"] = {
            "class_type": "TwinSampler",
            "inputs": {"model1": ["13", 0]},
            "_meta": {"title": "Upscale pass"},
        }
        lanes = read_lora_chain(graph, self.INFO)["lanes"]
        assert [pass_label(lane) for lane in lanes] == ["Base pass", "Hires pass"]

    def test_branches_that_merge_again_are_refused(self):
        graph = self._graph(loaders=())
        del graph["7"]
        info = {**self.INFO, "ModelMergeSimple": {"output": ["MODEL"]}}
        graph["10"] = self._loader("a.safetensors", 1.0, ["4", 0], ["4", 1])
        graph["11"] = self._loader("b.safetensors", 1.0, ["4", 0], ["4", 1])
        graph["20"] = {
            "class_type": "ModelMergeSimple",
            "inputs": {"model1": ["10", 0], "model2": ["11", 0]},
        }
        graph["7"] = {"class_type": "TwinSampler", "inputs": {"model1": ["20", 0]}}
        with pytest.raises(LookupError, match="meet again at #7 TwinSampler"):
            read_lora_chain(graph, info)

    def test_two_models_into_one_sampler_are_refused(self):
        graph = self._graph(loaders=("a",))
        graph["5"] = {"class_type": "UnetLoaderGGUF", "inputs": {}}
        graph["7"]["inputs"]["model2"] = ["5", 0]
        with pytest.raises(LookupError, match="meet again at #7"):
            read_lora_chain(graph, self.INFO)

    def test_a_lane_taking_its_clip_from_elsewhere_is_refused(self):
        """#12 reads the checkpoint's CLIP, not the trunk's end at #11."""
        graph = self._two_pass()
        graph["12"]["inputs"]["clip"] = ["4", 1]
        with pytest.raises(LookupError, match="take their CLIP from somewhere"):
            read_lora_chain(graph, self.INFO)

    def _two_models(self):
        """Wan 2.2's shape: high noise #1 → #10 → #3, low noise #2 → #15."""
        return {
            "1": {"class_type": "UnetLoaderGGUF", "inputs": {}},
            "2": {"class_type": "UnetLoaderGGUF", "inputs": {}},
            "10": {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {
                    "lora_name": "a.safetensors",
                    "strength_model": 1.0,
                    "model": ["1", 0],
                },
            },
            "3": {"class_type": "TwinSampler", "inputs": {"model1": ["10", 0]}},
            "15": {"class_type": "TwinSampler", "inputs": {"model1": ["2", 0]}},
        }

    def test_two_models_read_as_one_lane_each_with_no_trunk(self):
        chain = read_lora_chain(self._two_models(), self.INFO)
        assert chain["model_source"] is None and chain["loaders"] == []
        assert [lane["source"]["node_id"] for lane in chain["lanes"]] == ["1", "2"]
        assert [lane["pass"]["title"] for lane in chain["lanes"]] == [None, None]
        assert pass_label(chain["lanes"][1]) == "TwinSampler #15"

    def test_a_loader_moved_between_two_models_changes_which_model_it_patches(self):
        graph = self._two_models()
        plan = self._edit_lanes(graph, [], [[], [{"node_id": "10"}]])
        assert graph["10"]["inputs"]["model"] == ["2", 0]
        assert graph["15"]["inputs"]["model1"] == ["10", 0]
        assert graph["3"]["inputs"]["model1"] == ["1", 0]
        assert plan["changes"][0]["text"] == "#10 a moved to TwinSampler #15 only"

    def test_two_models_have_no_trunk_to_add_to(self):
        graph = self._two_models()
        chain = read_lora_chain(graph, self.INFO)
        with pytest.raises(LookupError, match="no LoRA can go before them all"):
            plan_lora_chain(
                graph,
                chain,
                [{"node_id": None, "adapter": self.NEW, "strength": 1.0}],
                self.INFO,
                [[{"node_id": "10"}], []],
            )

    def test_a_straight_chain_has_no_branch_note(self):
        assert read_lora_chain(self._graph(), self.INFO)["branch_note"] is None

    def test_a_dead_branch_is_neither_a_second_model_nor_a_loader(self):
        """A second UNET and a LoRA wired into nothing: ComfyUI never runs them."""
        graph = self._graph(loaders=("a",))
        graph["9"] = {"class_type": "SaveImage", "inputs": {"images": ["7", 0]}}
        graph["20"] = {"class_type": "UnetLoaderGGUF", "inputs": {}}
        graph["21"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "lora_name": "a.safetensors",
                "strength_model": 1.0,
                "model": ["20", 0],
            },
        }
        info = {**self.INFO, "SaveImage": {"output": [], "output_node": True}}
        chain = read_lora_chain(graph, info)
        assert chain["model_source"]["node_id"] == "4"
        assert [loader["node_id"] for loader in chain["loaders"]] == ["10"]

    def test_a_second_model_that_goes_several_ways_is_refused(self):
        graph = self._graph(loaders=("a",))
        graph["5"] = {"class_type": "UnetLoaderGGUF", "inputs": {}}
        graph["7"]["inputs"]["model2"] = ["5", 0]
        graph["9"] = {"class_type": "TwinSampler", "inputs": {"model1": ["5", 0]}}
        with pytest.raises(LookupError, match="loads 2 models, and the one from #5"):
            read_lora_chain(graph, self.INFO)

    def test_the_untyped_reading_follows_the_model_links_and_lists_every_slot(self):
        graph = self._graph()
        # Reversed ids, so node order is not the chain's order.
        graph["10"]["inputs"]["model"] = ["12", 0]
        graph["12"]["inputs"]["model"] = ["11", 0]
        graph["11"]["inputs"]["model"] = ["4", 0]
        graph["12"]["inputs"]["lora_name_2"] = "d.safetensors"
        chain = read_lora_chain_untyped(graph)
        assert [(s["node_id"], s["field"]) for s in chain["loaders"]] == [
            ("11", "lora_name"),
            ("12", "lora_name"),
            ("12", "lora_name_2"),
            ("10", "lora_name"),
        ]
        assert chain["model_source"]["node_id"] == "4"

    def test_a_refused_chain_still_names_its_two_ends_when_comfyui_answered(self):
        # Refused for its shape: the read-only view still says what the chain
        # runs between, and nothing untyped does.
        graph = self._graph()
        # A second model source for the sampler's other input, which also
        # goes to a second sampler.
        graph["5"] = {"class_type": "UnetLoaderGGUF", "inputs": {}}
        graph["7"]["inputs"]["model2"] = ["5", 0]
        graph["9"] = {"class_type": "TwinSampler", "inputs": {"model1": ["5", 0]}}
        with pytest.raises(LookupError, match="loads 2 models"):
            read_lora_chain(graph, self.INFO)
        chain = read_lora_chain_untyped(graph, self.INFO)
        assert chain["model_source"]["node_id"] == "4"
        # #7 reads the chain's end on model1; model2 is the other model.
        assert [(s["node_id"], s["type"]) for s in chain["sinks"]] == [
            ("7", "MODEL"),
            ("6", "CLIP"),
            ("8", "CLIP"),
        ]
        assert chain["sink_summary"] == (
            "TwinSampler #7 reads model · 2 text encoders read clip"
        )
        # Without ComfyUI the source is still followed by name; the readers
        # need the types, so none are named.
        untyped = read_lora_chain_untyped(graph)
        assert untyped["model_source"]["node_id"] == "4"
        assert untyped["sinks"] == [] and untyped["sink_summary"] is None


class TestAdvertisedModelNames:
    """What a ComfyUI says it can load, which is the only honest answer to it.

    PixlStash holds a URL and no path into that install's ``models/`` tree, so
    the combo lists are the source for both the merge's warning and the
    submit-time swap's verification.
    """

    def test_every_loader_field_is_collected_with_its_basename(self):
        names = advertised_model_names(
            {
                "CheckpointLoaderSimple": {
                    "input": {"required": {"ckpt_name": [["SDXL/base.safetensors"]]}}
                },
                "LoraLoader": {
                    "input": {"required": {"lora_name": [["detail.safetensors"]]}}
                },
            }
        )
        # The entry as listed AND its basename: a combo entry is relative to one
        # of ComfyUI's model folders, and nothing here knows which prefix it puts
        # in front of a registered folder's relpath.
        assert names == {
            "SDXL/base.safetensors",
            "base.safetensors",
            "detail.safetensors",
        }

    def test_a_windows_listing_is_the_same_file_as_a_posix_one(self):
        names = advertised_model_names(
            {
                "LoraLoader": {
                    "input": {"required": {"lora_name": [["sdxl\\a.safetensors"]]}}
                }
            }
        )
        assert names == {"sdxl/a.safetensors", "a.safetensors"}

    def test_a_field_with_no_enumerable_options_advertises_nothing(self):
        # A `remote` combo's embedded list proves nothing, and a plain type name
        # is not a filename. Reporting either as "advertised" would warn the owner
        # about a file this ComfyUI may not read at all.
        assert (
            advertised_model_names(
                {
                    "LoraLoader": {
                        "input": {
                            "required": {
                                "lora_name": ["COMBO", {"remote": {"route": "/x"}}]
                            }
                        }
                    }
                }
            )
            == set()
        )
        assert advertised_model_names({}) == set()


class TestModelSwap:
    """Resolve a model reference at submit, verified against ComfyUI (#1439).

    The graph names a copy that was removed to keep one of several; another copy
    of the *same bytes* is still on the shelf under another name. The swap points
    the loader at it, and the thing that decides whether it may is ComfyUI's own
    combo list.
    """

    GRAPH = {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "gone.safetensors"},
        }
    }
    OBJECT_INFO = {
        "CheckpointLoaderSimple": {
            "input": {"required": {"ckpt_name": [["kept/kept.safetensors"], {}]}}
        }
    }
    ALIASES = {"gone.safetensors": ["kept/kept.safetensors", "kept.safetensors"]}

    def test_it_aims_at_exactly_what_the_preflight_would_report(self):
        targets = detect_model_targets(self.GRAPH, self.OBJECT_INFO)
        assert (
            targets == preflight_prompt(self.GRAPH, self.OBJECT_INFO)["missing_models"]
        )
        assert [t["value"] for t in targets] == ["gone.safetensors"]

    def test_a_verified_candidate_is_written_and_reported(self):
        graph = json.loads(json.dumps(self.GRAPH))
        swapped = apply_model_swap(
            graph,
            detect_model_targets(graph, self.OBJECT_INFO),
            self.ALIASES,
            self.OBJECT_INFO,
        )
        assert graph["4"]["inputs"]["ckpt_name"] == "kept/kept.safetensors"
        # Reported, never silent: a run that quietly loaded another file makes
        # its own lineage a lie.
        assert swapped == [
            {
                "node_id": "4",
                "class_type": "CheckpointLoaderSimple",
                "field": "ckpt_name",
                "was": "gone.safetensors",
                "now": "kept/kept.safetensors",
            }
        ]
        # And the graph now passes the pre-flight it was failing.
        assert preflight_prompt(graph, self.OBJECT_INFO)["missing_models"] == []

    def test_a_candidate_this_comfyui_does_not_advertise_is_not_written(self):
        """**The rule the whole design turns on.** A swap PixlStash believes in
        and ComfyUI does not list only moves the failure to the queue."""
        graph = json.loads(json.dumps(self.GRAPH))
        swapped = apply_model_swap(
            graph,
            detect_model_targets(graph, self.OBJECT_INFO),
            {"gone.safetensors": ["somewhere-else.safetensors"]},
            self.OBJECT_INFO,
        )
        assert swapped == []
        assert graph["4"]["inputs"]["ckpt_name"] == "gone.safetensors", (
            "an unverified name was written into the graph"
        )

    def test_a_mixed_case_filename_resolves(self):
        """**The case that made the whole feature a no-op.**

        `model_name_aliases` keys its map with `normalized_filename`, which
        LOWERCASES; a lookup that merely unified separators found nothing for any
        name with a capital in it, which is most real model filenames. The
        candidates are the shelf's own spelling, because ComfyUI compares exactly.
        """
        graph = {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "RealVisXL.safetensors"},
            }
        }
        object_info = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["kept/Kept.safetensors"], {}]}}
            }
        }
        swapped = apply_model_swap(
            graph,
            detect_model_targets(graph, object_info),
            # As `model_name_aliases` builds it: a folded key, unfolded names.
            {"realvisxl.safetensors": ["kept/Kept.safetensors", "Kept.safetensors"]},
            object_info,
        )
        assert [s["now"] for s in swapped] == ["kept/Kept.safetensors"]
        assert preflight_prompt(graph, object_info)["missing_models"] == []

    def test_a_candidate_is_never_lowercased_on_the_way_in(self):
        """A folded candidate is a filename ComfyUI would refuse.

        `_match_option` reports a case-only difference as a miss, on purpose - it
        is a real failure on a case-sensitive host - so writing the lowercased
        form would be a substitution that fails at the queue instead of here.
        """
        graph = {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "gone.safetensors"},
            }
        }
        object_info = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["Kept.safetensors"], {}]}}
            }
        }
        assert (
            apply_model_swap(
                graph,
                detect_model_targets(graph, object_info),
                {"gone.safetensors": ["kept.safetensors"]},
                object_info,
            )
            == []
        )
        assert graph["4"]["inputs"]["ckpt_name"] == "gone.safetensors"

    def test_it_writes_the_spelling_this_comfyui_advertises(self):
        """The candidate is verified with separators normalised; the value written
        has to be the option that matched.

        ComfyUI compares exactly, so writing a Windows-spelled candidate onto an
        install that lists the POSIX form would be a substitution that fails at
        the queue - the one thing this function's contract says cannot happen.
        """
        graph = {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "gone.safetensors"},
            }
        }
        object_info = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["sub/kept.safetensors"], {}]}}
            }
        }
        swapped = apply_model_swap(
            graph,
            detect_model_targets(graph, object_info),
            # As a Windows host recorded the relpath.
            {"gone.safetensors": ["sub\\kept.safetensors"]},
            object_info,
        )
        assert [s["now"] for s in swapped] == ["sub/kept.safetensors"]
        assert graph["4"]["inputs"]["ckpt_name"] == "sub/kept.safetensors"
        # And the graph really does pass now, which is the property at stake.
        assert preflight_prompt(graph, object_info)["missing_models"] == []

    def test_a_model_with_no_other_name_is_left_alone(self):
        graph = json.loads(json.dumps(self.GRAPH))
        assert (
            apply_model_swap(
                graph,
                detect_model_targets(graph, self.OBJECT_INFO),
                {},
                self.OBJECT_INFO,
            )
            == []
        )
        assert graph["4"]["inputs"]["ckpt_name"] == "gone.safetensors"

    def test_the_same_file_under_a_path_this_comfyui_uses_is_a_real_swap(self):
        """Not a no-op, and worth having: ComfyUI enumerates a model by its path
        under its own folder, so an install that files this one in a
        subdirectory advertises a name the recipe never said. The pre-flight
        calls that missing today; the swap is the fix, and it is verified the
        same way as any other."""
        graph = json.loads(json.dumps(self.GRAPH))
        object_info = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["sub/gone.safetensors"], {}]}}
            }
        }
        swapped = apply_model_swap(
            graph,
            detect_model_targets(graph, object_info),
            {"gone.safetensors": ["sub/gone.safetensors"]},
            object_info,
        )
        assert [s["now"] for s in swapped] == ["sub/gone.safetensors"]
        assert preflight_prompt(graph, object_info)["missing_models"] == []

    def test_a_loaded_model_is_never_touched(self):
        graph = {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "kept/kept.safetensors"},
            }
        }
        assert (
            apply_model_swap(
                graph,
                detect_model_targets(graph, self.OBJECT_INFO),
                {"kept.safetensors": ["something.safetensors"]},
                self.OBJECT_INFO,
            )
            == []
        )
        assert graph["4"]["inputs"]["ckpt_name"] == "kept/kept.safetensors"


class TestFilenameSwap:
    """Clone with new models: replace a file that loads with a different one."""

    def _graph(self):
        return {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "zimage/zimage-turbo.safetensors"},
            },
            "5": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
            "6": {
                "class_type": "DualCLIPLoader",
                "inputs": {
                    "clip_name1": "qwen_3_4b.safetensors",
                    "clip_name2": "ae.safetensors",
                },
            },
        }

    def test_every_field_naming_the_file_is_rewritten(self):
        graph = self._graph()
        swapped, unswapped = apply_filename_swap(
            graph, {"ae.safetensors": "flux2-vae.safetensors"}
        )
        assert graph["5"]["inputs"]["vae_name"] == "flux2-vae.safetensors"
        assert graph["6"]["inputs"]["clip_name2"] == "flux2-vae.safetensors"
        assert {(s["node_id"], s["field"]) for s in swapped} == {
            ("5", "vae_name"),
            ("6", "clip_name2"),
        }
        assert unswapped == []

    def test_a_key_matches_the_whole_name_whatever_its_case_or_separators(self):
        graph = self._graph()
        swapped, _ = apply_filename_swap(
            graph, {"ZIMAGE\\Zimage-Turbo.safetensors": "krea2.safetensors"}
        )
        assert graph["4"]["inputs"]["ckpt_name"] == "krea2.safetensors"
        assert swapped[0]["was"] == "zimage/zimage-turbo.safetensors"

    def test_a_bare_key_never_rewrites_a_file_of_that_name_in_a_folder(self):
        graph = self._graph()
        graph["7"] = {
            "class_type": "ControlNetLoader",
            "inputs": {"control_net_name": "canny/ae.safetensors"},
        }
        apply_filename_swap(graph, {"ae.safetensors": "flux2-vae.safetensors"})
        assert graph["5"]["inputs"]["vae_name"] == "flux2-vae.safetensors"
        assert graph["7"]["inputs"]["control_net_name"] == "canny/ae.safetensors"

    def test_a_key_with_a_folder_never_matches_another_folders_file(self):
        graph = self._graph()
        graph["7"] = {
            "class_type": "CLIPVisionLoader",
            "inputs": {"clip_name": "clip_vision/qwen_3_4b.safetensors"},
        }
        graph["6"]["inputs"]["clip_name1"] = "text_encoders/qwen_3_4b.safetensors"
        apply_filename_swap(
            graph, {"text_encoders/qwen_3_4b.safetensors": "mistral.safetensors"}
        )
        assert graph["6"]["inputs"]["clip_name1"] == "mistral.safetensors"
        assert graph["7"]["inputs"]["clip_name"] == "clip_vision/qwen_3_4b.safetensors"

    def test_a_basename_two_keys_share_is_left_alone(self):
        graph = self._graph()
        swapped, unswapped = apply_filename_swap(
            graph,
            {
                "a/zimage-turbo.safetensors": "one.safetensors",
                "b/zimage-turbo.safetensors": "two.safetensors",
            },
        )
        assert swapped == []
        assert graph["4"]["inputs"]["ckpt_name"] == "zimage/zimage-turbo.safetensors"
        assert {u["reason"] for u in unswapped} == {"not_in_graph"}

    def test_without_object_info_the_name_is_written_unverified(self):
        graph = self._graph()
        swapped, _ = apply_filename_swap(
            graph, {"zimage/zimage-turbo.safetensors": "krea2.safetensors"}
        )
        assert swapped == [
            {
                "node_id": "4",
                "class_type": "CheckpointLoaderSimple",
                "field": "ckpt_name",
                "was": "zimage/zimage-turbo.safetensors",
                "now": "krea2.safetensors",
                "verified": False,
            }
        ]

    def test_with_object_info_the_options_own_spelling_is_written(self):
        info = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["krea/krea2.safetensors"], {}]}}
            }
        }
        graph = self._graph()
        swapped, _ = apply_filename_swap(
            graph,
            {"zimage/zimage-turbo.safetensors": "krea\\krea2.safetensors"},
            info,
        )
        assert graph["4"]["inputs"]["ckpt_name"] == "krea/krea2.safetensors"
        assert swapped[0]["verified"] is True

    def test_a_shelf_basename_finds_the_subfolder_comfyui_lists_it_under(self):
        info = {
            "CheckpointLoaderSimple": {
                "input": {
                    "required": {
                        "ckpt_name": [
                            ["other.safetensors", "krea/krea2.safetensors"],
                            {},
                        ]
                    }
                }
            }
        }
        graph = self._graph()
        apply_filename_swap(
            graph, {"zimage/zimage-turbo.safetensors": "krea2.safetensors"}, info
        )
        assert graph["4"]["inputs"]["ckpt_name"] == "krea/krea2.safetensors"

    def test_a_replacement_comfyui_does_not_list_is_reported_not_written(self):
        info = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["other.safetensors"], {}]}}
            }
        }
        graph = self._graph()
        swapped, unswapped = apply_filename_swap(
            graph, {"zimage/zimage-turbo.safetensors": "krea2.safetensors"}, info
        )
        assert swapped == []
        assert graph["4"]["inputs"]["ckpt_name"] == "zimage/zimage-turbo.safetensors"
        assert unswapped == [
            {
                "was": "zimage/zimage-turbo.safetensors",
                "now": "krea2.safetensors",
                "reason": "not_on_comfyui",
            }
        ]

    def test_a_name_comfyui_lists_in_two_folders_is_refused_as_ambiguous(self):
        info = {
            "VAELoader": {
                "input": {
                    "required": {
                        "vae_name": [
                            ["flux/new.safetensors", "sd3/new.safetensors"],
                            {},
                        ]
                    }
                }
            }
        }
        graph = self._graph()
        swapped, unswapped = apply_filename_swap(
            graph, {"ae.safetensors": "new.safetensors"}, info
        )
        assert graph["5"]["inputs"]["vae_name"] == "ae.safetensors"
        assert {u["reason"] for u in unswapped} == {"several_on_comfyui"}

    def test_a_swap_onto_the_same_file_changes_nothing(self):
        graph = self._graph()
        swapped, unswapped = apply_filename_swap(
            graph, {"ae.safetensors": "ae.safetensors"}
        )
        assert swapped == [] and unswapped == []
        assert graph == self._graph()


class TestWrappedLoaders:
    """ComfyUI-MultiGPU wrappers and the GGUF CLIP loader are checked (#1440).

    A hand-written slice of ``object_info`` in the shape a live install
    advertises: each wrapper keeps the field names of the loader it wraps and
    adds placement inputs of its own.
    """

    WRAPPED = {
        "UNETLoaderDisTorch2MultiGPU": "UNETLoader",
        "VAELoaderDisTorch2MultiGPU": "VAELoader",
        "CLIPLoaderDisTorch2MultiGPU": "CLIPLoader",
        "DualCLIPLoaderMultiGPU": "DualCLIPLoader",
        "CheckpointLoaderSimpleMultiGPU": "CheckpointLoaderSimple",
        "UnetLoaderGGUFDisTorchMultiGPU": "UnetLoaderGGUF",
        "UnetLoaderGGUFDisTorch2MultiGPU": "UnetLoaderGGUF",
        "LoraLoaderMultiGPU": "LoraLoader",
    }

    @pytest.mark.parametrize(("wrapper", "base"), sorted(WRAPPED.items()))
    def test_a_wrapper_reads_the_fields_of_the_loader_it_wraps(self, wrapper, base):
        assert model_filename_fields(wrapper) == MODEL_FILENAME_FIELDS[base]

    @pytest.mark.parametrize(
        "class_type",
        ["WanVideoModelLoaderMultiGPU", "MultiGPU", "DisTorch2MultiGPU", "KSampler"],
    )
    def test_a_suffix_on_an_unknown_base_invents_no_field(self, class_type):
        assert model_filename_fields(class_type) == ()

    def test_a_wrapped_loader_naming_an_absent_file_is_missing(self):
        info = {
            "UNETLoaderDisTorch2MultiGPU": {
                "input": {
                    "required": {
                        "unet_name": [["wan2.2_t2v_14B.safetensors"], {}],
                        "device": [["cuda:0", "cpu"], {}],
                    }
                }
            }
        }
        present = {
            "1": {
                "class_type": "UNETLoaderDisTorch2MultiGPU",
                "inputs": {"unet_name": "wan2.2_t2v_14B.safetensors"},
            }
        }
        absent = {
            "1": {
                "class_type": "UNETLoaderDisTorch2MultiGPU",
                "inputs": {"unet_name": "gone.safetensors"},
            }
        }
        assert preflight_prompt(present, info)["ok"] is True
        result = preflight_prompt(absent, info)
        assert result["ok"] is False
        assert [m["value"] for m in result["missing_models"]] == ["gone.safetensors"]
        assert result["unchecked_models"] == 0

    def test_the_gguf_clip_loader_is_checked(self):
        info = {
            "CLIPLoaderGGUF": {"input": {"required": {"clip_name": [["t5.gguf"], {}]}}}
        }
        graph = {
            "1": {"class_type": "CLIPLoaderGGUF", "inputs": {"clip_name": "umt5.gguf"}}
        }
        result = preflight_prompt(graph, info)
        assert [m["value"] for m in result["missing_models"]] == ["umt5.gguf"]

    def test_a_model_on_a_loader_nobody_reads_is_counted_unchecked(self):
        info = {"WanVideoModelLoader": {"input": {"required": {}}}, **OBJECT_INFO}
        graph = {
            **GRAPH,
            "7": {
                "class_type": "WanVideoModelLoader",
                "inputs": {"model": "wan.safetensors", "precision": "bf16"},
            },
        }
        result = preflight_prompt(graph, info)
        assert result["ok"] is True
        assert result["missing_models"] == []
        # Only the unread one: the checkpoint and the LoRA were checked.
        assert result["unchecked_models"] == 1

    def test_a_wrapper_advertises_what_its_comfyui_can_load(self):
        info = {
            "VAELoaderDisTorch2MultiGPU": {
                "input": {
                    "required": {"vae_name": [["wan/wan_2.1_vae.safetensors"], {}]}
                }
            }
        }
        assert advertised_model_names(info) >= {
            "wan/wan_2.1_vae.safetensors",
            "wan_2.1_vae.safetensors",
        }
