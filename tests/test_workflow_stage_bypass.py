"""Switching an upscale or FaceDetailer stage off for one run (#1621).

``tests/comfyui_object_info_stages.json`` is a real ComfyUI's ``object_info``
(core, Impact Pack, UltimateSDUpscale) for the classes below, trimmed to what
the bypass reads: each input's declared type, each class's output types and
``output_node``. Combo option lists are emptied, since they list models.
"""

import copy
import json
from pathlib import Path

import pytest

from pixlstash.services.comfyui_recipe_service import bypass_stage
from pixlstash.services.workflow_identity import FACE_DETAILER, SEED_VARIANCE, UPSCALE
from pixlstash.services.workflow_run_service import (
    STAGE_NOT_SKIPPABLE,
    skip_requested_stages,
)

OBJECT_INFO = json.loads(
    (Path(__file__).parent / "comfyui_object_info_stages.json").read_text()
)


def _base() -> dict:
    """A plain txt2img graph: checkpoint, prompts, sampler, decode, save."""
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "a"}},
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "p", "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "n", "clip": ["4", 1]},
        },
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512}},
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 1,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "out", "images": ["8", 0]},
        },
    }


def _actions(changes: list[dict]) -> dict[str, str]:
    return {change["node_id"]: change["action"] for change in changes}


def test_hires_fix_goes_with_its_latent_upscale():
    graph = _base()
    graph["10"] = {
        "class_type": "LatentUpscaleBy",
        "inputs": {"samples": ["3", 0], "scale_by": 1.5},
    }
    # A prompt only the hires pass reads: live before, orphaned after.
    graph["12"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "h", "clip": ["4", 1]},
    }
    graph["11"] = {
        "class_type": "KSampler",
        "inputs": {
            "model": ["4", 0],
            "positive": ["12", 0],
            "negative": ["7", 0],
            "latent_image": ["10", 0],
        },
    }
    graph["8"]["inputs"]["samples"] = ["11", 0]

    changes = bypass_stage(graph, UPSCALE, OBJECT_INFO)

    assert _actions(changes) == {"11": "bypassed", "10": "bypassed", "12": "pruned"}
    assert graph["8"]["inputs"]["samples"] == ["3", 0]
    assert graph == _base()


def test_model_upscale_prunes_its_loader():
    graph = _base()
    graph["20"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "x"}}
    graph["21"] = {
        "class_type": "ImageUpscaleWithModel",
        "inputs": {"upscale_model": ["20", 0], "image": ["8", 0]},
    }
    graph["9"]["inputs"]["images"] = ["21", 0]

    changes = bypass_stage(graph, UPSCALE, OBJECT_INFO)

    assert _actions(changes) == {"21": "bypassed", "20": "pruned"}
    assert graph == _base()


def test_seed_variance_comes_off_the_conditioning_it_feeds_the_sampler():
    """A pre-sampler stage: feeding the sampler is what it does, not a refusal."""
    graph = _base()
    graph["30"] = {
        "class_type": "SeedVarianceEnhancer",
        "inputs": {"conditioning": ["6", 0], "strength": 0.5, "seed": 1},
    }
    graph["3"]["inputs"]["positive"] = ["30", 0]
    info = dict(
        OBJECT_INFO,
        SeedVarianceEnhancer={
            "input": {"required": {"conditioning": ["CONDITIONING"]}},
            "output": ["CONDITIONING"],
            "output_node": False,
        },
    )

    assert _actions(bypass_stage(graph, SEED_VARIANCE, info)) == {"30": "bypassed"}
    assert graph == _base()


def test_ultimate_sd_upscale():
    graph = _base()
    graph["20"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "x"}}
    graph["30"] = {
        "class_type": "UltimateSDUpscale",
        "inputs": {
            "image": ["8", 0],
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "vae": ["4", 2],
            "upscale_model": ["20", 0],
            "upscale_by": 2,
        },
    }
    graph["9"]["inputs"]["images"] = ["30", 0]

    changes = bypass_stage(graph, UPSCALE, OBJECT_INFO)

    assert _actions(changes) == {"30": "bypassed", "20": "pruned"}
    assert graph == _base()


def test_a_loader_that_already_fed_nothing_is_not_pruned():
    """Pruned means orphaned BY the bypass; a node that was dead stays."""
    graph = _base()
    graph["20"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "x"}}
    graph["21"] = {
        "class_type": "ImageScaleBy",
        "inputs": {"image": ["8", 0], "scale_by": 2},
    }
    graph["9"]["inputs"]["images"] = ["21", 0]
    graph["40"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "d", "clip": ["4", 1]},
    }

    changes = bypass_stage(graph, UPSCALE, OBJECT_INFO)

    assert _actions(changes) == {"21": "bypassed"}
    assert "20" in graph and "40" in graph


def _face_detailer(graph: dict) -> dict:
    graph["41"] = {
        "class_type": "UltralyticsDetectorProvider",
        "inputs": {"model_name": "x"},
    }
    graph["42"] = {"class_type": "SAMLoader", "inputs": {"model_name": "y"}}
    graph["40"] = {
        "class_type": "FaceDetailer",
        "inputs": {
            "image": ["8", 0],
            "model": ["4", 0],
            "clip": ["4", 1],
            "vae": ["4", 2],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "bbox_detector": ["41", 0],
            "sam_model_opt": ["42", 0],
        },
    }
    graph["9"]["inputs"]["images"] = ["40", 0]
    return graph


def test_face_detailer_read_only_for_its_image():
    graph = _face_detailer(_base())

    changes = bypass_stage(graph, FACE_DETAILER, OBJECT_INFO)

    assert _actions(changes) == {"40": "bypassed", "41": "pruned", "42": "pruned"}
    assert graph == _base()


def test_face_detailer_whose_mask_is_read_refuses_and_changes_nothing():
    graph = _face_detailer(_base())
    graph["43"] = {"class_type": "MaskToImage", "inputs": {"mask": ["40", 3]}}
    graph["44"] = {"class_type": "PreviewImage", "inputs": {"images": ["43", 0]}}
    before = copy.deepcopy(graph)

    with pytest.raises(LookupError, match=r"node 40 \(FaceDetailer\)"):
        bypass_stage(graph, FACE_DETAILER, OBJECT_INFO)
    assert graph == before


def test_a_save_before_and_after_the_upscale_saves_once():
    graph = _base()
    graph["21"] = {
        "class_type": "ImageScaleBy",
        "inputs": {"image": ["8", 0], "scale_by": 2},
    }
    graph["9"]["inputs"]["images"] = ["21", 0]
    graph["50"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "before", "images": ["8", 0]},
    }

    changes = bypass_stage(graph, UPSCALE, OBJECT_INFO)

    # The rewired save goes; the one that always read the decode stays.
    assert _actions(changes) == {"21": "bypassed", "9": "duplicate_save"}
    assert graph["50"]["inputs"] == {"filename_prefix": "before", "images": ["8", 0]}
    assert "9" not in graph


def test_two_saves_of_different_images_are_both_kept():
    """The positive control for the duplicate-save drop."""
    graph = _base()
    graph["21"] = {
        "class_type": "ImageScaleBy",
        "inputs": {"image": ["8", 0], "scale_by": 2},
    }
    graph["9"]["inputs"]["images"] = ["21", 0]
    graph["51"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["5", 0], "vae": ["4", 2]},
    }
    graph["50"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "other", "images": ["51", 0]},
    }

    bypass_stage(graph, UPSCALE, OBJECT_INFO)

    assert "9" in graph and "50" in graph


def test_saves_that_were_already_alike_are_not_the_bypasses_to_drop():
    """Only a save the bypass rewired is a duplicate; the owner's own pair stays."""
    graph = _base()
    graph["50"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "copy", "images": ["8", 0]},
    }
    graph["21"] = {
        "class_type": "ImageScaleBy",
        "inputs": {"image": ["8", 0], "scale_by": 2},
    }
    graph["52"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "big", "images": ["21", 0]},
    }

    changes = bypass_stage(graph, UPSCALE, OBJECT_INFO)

    assert _actions(changes) == {"21": "bypassed", "52": "duplicate_save"}
    assert "9" in graph and "50" in graph


def test_a_face_detailer_chained_by_its_pipe_goes_consumer_first():
    """The pipe reader goes first; the other way round the pipe has no stand-in."""
    graph = _face_detailer(_base())
    graph["45"] = {
        "class_type": "FaceDetailerPipe",
        "inputs": {"image": ["40", 0], "detailer_pipe": ["40", 4]},
    }
    graph["9"]["inputs"]["images"] = ["45", 0]

    changes = bypass_stage(graph, FACE_DETAILER, OBJECT_INFO)

    assert [c["node_id"] for c in changes if c["action"] == "bypassed"] == ["45", "40"]
    assert graph == _base()


def test_a_dead_face_detailer_does_not_refuse_the_run():
    """Only what an output reads is the stage; a dead branch is not asked about."""
    graph = _base()
    graph["40"] = {
        "class_type": "FaceDetailer",
        "inputs": {"image": ["8", 0], "model": ["4", 0]},
    }
    graph["43"] = {"class_type": "MaskToImage", "inputs": {"mask": ["40", 3]}}

    assert bypass_stage(graph, FACE_DETAILER, OBJECT_INFO) == []
    assert "40" in graph


def _img2img(graph: dict) -> dict:
    graph["60"] = {"class_type": "LoadImage", "inputs": {"image": "in.png"}}
    graph["61"] = {
        "class_type": "ImageScale",
        "inputs": {"image": ["60", 0], "width": 1024, "height": 1024},
    }
    graph["62"] = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["61", 0], "vae": ["4", 2]},
    }
    graph["3"]["inputs"]["latent_image"] = ["62", 0]
    del graph["5"]
    return graph


def test_an_input_resize_is_not_the_upscale_stage():
    graph = _img2img(_base())
    graph["21"] = {
        "class_type": "ImageScaleBy",
        "inputs": {"image": ["8", 0], "scale_by": 2},
    }
    graph["9"]["inputs"]["images"] = ["21", 0]

    changes = bypass_stage(graph, UPSCALE, OBJECT_INFO)

    assert _actions(changes) == {"21": "bypassed"}
    assert graph == _img2img(_base())


def test_a_hires_fix_in_pixel_space_refuses():
    """Its re-encode and second sampler are not the group's; half off is refused."""
    graph = _base()
    graph["20"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "x"}}
    graph["21"] = {
        "class_type": "ImageUpscaleWithModel",
        "inputs": {"upscale_model": ["20", 0], "image": ["8", 0]},
    }
    graph["22"] = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["21", 0], "vae": ["4", 2]},
    }
    graph["23"] = {
        "class_type": "KSampler",
        "inputs": {
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["22", 0],
        },
    }
    graph["24"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["23", 0], "vae": ["4", 2]},
    }
    graph["9"]["inputs"]["images"] = ["24", 0]
    before = copy.deepcopy(graph)

    with pytest.raises(LookupError, match=r"node 21 .*node 23"):
        bypass_stage(graph, UPSCALE, OBJECT_INFO)
    assert graph == before


def test_a_graph_that_loops_is_refused_not_crashed():
    graph = _base()
    graph["21"] = {
        "class_type": "ImageScaleBy",
        "inputs": {"image": ["22", 0], "scale_by": 2},
    }
    graph["22"] = {"class_type": "ImageScaleBy", "inputs": {"image": ["21", 0]}}
    graph["9"]["inputs"]["images"] = ["21", 0]

    reasons = skip_requested_stages(graph, [UPSCALE], OBJECT_INFO)

    assert [r.code for r in reasons] == [STAGE_NOT_SKIPPABLE]
    assert "loops back" in reasons[0].detail["message"]


def test_a_graph_without_the_stage_is_untouched():
    graph = _base()
    assert bypass_stage(graph, FACE_DETAILER, OBJECT_INFO) == []
    assert graph == _base()


def test_a_refused_stage_is_a_reason_never_a_full_run():
    graph = _face_detailer(_base())
    graph["43"] = {"class_type": "MaskToImage", "inputs": {"mask": ["40", 3]}}
    graph["44"] = {"class_type": "PreviewImage", "inputs": {"images": ["43", 0]}}

    reasons = skip_requested_stages(graph, [FACE_DETAILER], OBJECT_INFO)

    assert [(r.code, r.detail["stage"]) for r in reasons] == [
        (STAGE_NOT_SKIPPABLE, FACE_DETAILER)
    ]


def test_without_comfyui_only_a_graph_with_the_stage_is_refused():
    assert skip_requested_stages(_base(), [UPSCALE], None) == []
    reasons = skip_requested_stages(_face_detailer(_base()), [FACE_DETAILER], None)
    assert [r.code for r in reasons] == [STAGE_NOT_SKIPPABLE]
    assert "could not reach ComfyUI" in reasons[0].detail["message"]
