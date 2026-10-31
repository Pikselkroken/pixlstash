"""Convert to Inpainting workflow: the Crop & Stitch graph transform (#2446)."""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from pixlstash.services.inpaint_conversion import (
    InpaintConversionRefused,
    convert_to_inpaint,
)

BUILT_IN = (
    Path(__file__).resolve().parent.parent
    / "pixlstash"
    / "data"
    / "comfyui-workflows"
    / "built-in"
)

# The built-ins load through PixlStash's digest loaders; an edit graph names
# its files. The same node to the sampler.
_SAME_LOADER = {"PixlStashCLIPLoader": "CLIPLoader", "PixlStashVAELoader": "VAELoader"}


def _built_in(name: str) -> dict:
    return json.loads((BUILT_IN / name).read_text())


def _edges(graph: dict) -> Counter:
    """Every edge as ``(source class, slot, target class, input)``, ids dropped."""

    def cls(node_id):
        name = graph[node_id]["class_type"]
        return _SAME_LOADER.get(name, name)

    return Counter(
        (cls(value[0]), value[1], cls(node_id), name)
        for node_id, node in graph.items()
        for name, value in node["inputs"].items()
        if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)
    )


def _of(graph: dict, class_type: str) -> list[str]:
    return [i for i, n in graph.items() if n["class_type"] == class_type]


def _one(graph: dict, class_type: str) -> str:
    [node_id] = _of(graph, class_type)
    return node_id


def test_the_klein_edit_graph_converts_to_the_klein_inpaint_built_in():
    """Golden: the edit graph becomes the inpaint built-in's structure.

    Three differences are the edit graph's own and stay, because nothing else
    in the graph changes: its negative goes through a second
    ``ReferenceLatent``, its scheduler is sized by ``GetImageSize`` (now of the
    crop) rather than a literal, and ``DifferentialDiffusion`` sits on the
    model, which the built-in omits.
    """
    converted = _edges(convert_to_inpaint(_built_in("Flux2-Klein-Image-Edit.json")))
    expected = _edges(_built_in("Inpaint-Flux2-Klein-9B.json"))
    expected -= Counter(
        {
            ("UNETLoader", 0, "CFGGuider", "model"): 1,
            ("ConditioningZeroOut", 0, "CFGGuider", "negative"): 1,
        }
    )
    expected += Counter(
        {
            ("UNETLoader", 0, "DifferentialDiffusion", "model"): 1,
            ("DifferentialDiffusion", 0, "CFGGuider", "model"): 1,
            ("ConditioningZeroOut", 0, "ReferenceLatent", "conditioning"): 1,
            ("VAEEncode", 0, "ReferenceLatent", "latent"): 1,
            ("ReferenceLatent", 0, "CFGGuider", "negative"): 1,
            ("InpaintCropImproved", 1, "GetImageSize", "image"): 1,
            ("GetImageSize", 0, "Flux2Scheduler", "width"): 1,
            ("GetImageSize", 1, "Flux2Scheduler", "height"): 1,
        }
    )
    assert converted == expected


def test_the_klein_crop_node_takes_the_built_ins_settings():
    converted = convert_to_inpaint(_built_in("Flux2-Klein-Image-Edit.json"))
    reference = _built_in("Inpaint-Flux2-Klein-9B.json")
    crop = converted[_one(converted, "InpaintCropImproved")]["inputs"]
    want = reference[_one(reference, "InpaintCropImproved")]["inputs"]
    assert {k: v for k, v in crop.items() if k not in ("image", "mask")} == {
        k: v for k, v in want.items() if k not in ("image", "mask")
    }
    # The empty latent is gone and the edit graph's own encode is reused.
    assert not _of(converted, "EmptyFlux2LatentImage")
    assert len(_of(converted, "VAEEncode")) == 1
    assert converted["9"]["inputs"]["filename_prefix"] == "Inpaint-Flux2-Klein"


def _moss_edit() -> dict:
    """MOSS-Image edit with an edit LoRA and skip-layer guidance."""
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "moss.safetensors", "weight_dtype": "default"},
        },
        "2": {
            "class_type": "PixlStashAdapterLoader",
            "inputs": {"model": ["1", 0], "lora_sha256": "0" * 64, "strength": 1.0},
        },
        "3": {
            "class_type": "SkipLayerGuidanceDiTSimple",
            "inputs": {"model": ["2", 0], "double_layers": "7", "scale": 3.0},
        },
        "4": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": "te.safetensors", "type": "moss"},
        },
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": "vae.safetensors"}},
        "6": {"class_type": "LoadImage", "inputs": {"image": "subject.png"}},
        "7": {
            "class_type": "ImageScaleToTotalPixels",
            "inputs": {
                "image": ["6", 0],
                "upscale_method": "lanczos",
                "megapixels": 1.0,
                "resolution_steps": 16,
            },
        },
        "8": {
            "class_type": "TextEncodeMOSSImageEdit",
            "inputs": {
                "clip": ["4", 0],
                "vae": ["5", 0],
                "prompt": "make the jacket red",
                "images.image_0": ["7", 0],
            },
        },
        "9": {
            "class_type": "TextEncodeMOSSImageEdit",
            "inputs": {
                "clip": ["4", 0],
                "vae": ["5", 0],
                "prompt": "",
                "images.image_0": ["7", 0],
            },
        },
        "10": {"class_type": "GetImageSize", "inputs": {"image": ["7", 0]}},
        "11": {
            "class_type": "EmptyFlux2LatentImage",
            "inputs": {"width": ["10", 0], "height": ["10", 1], "batch_size": 1},
        },
        "12": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["3", 0],
                "positive": ["8", 0],
                "negative": ["9", 0],
                "latent_image": ["11", 0],
                "seed": 7,
                "steps": 20,
                "cfg": 4.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 0.9,
            },
        },
        "13": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["12", 0], "vae": ["5", 0]},
        },
        "14": {
            "class_type": "SaveImage",
            "inputs": {"images": ["13", 0], "filename_prefix": "MOSS"},
        },
    }


def test_a_moss_edit_graph_reads_the_crop_and_starts_from_its_latent():
    converted = convert_to_inpaint(_moss_edit())
    crop = _one(converted, "InpaintCropImproved")
    assert converted[crop]["inputs"]["image"] == ["6", 0]
    assert converted[crop]["inputs"]["mask"] == ["6", 1]
    # √1 MP x 1024 on a 16 grid.
    assert converted[crop]["inputs"]["output_target_width"] == 1024
    assert converted[crop]["inputs"]["output_target_height"] == 1024
    for encoder in ("8", "9"):
        assert converted[encoder]["inputs"]["images.image_0"] == [crop, 1]
    for gone in ("7", "10", "11"):
        assert gone not in converted, f"{gone} should have been removed"

    sampler = converted["12"]["inputs"]
    masked = converted[sampler["latent_image"][0]]
    assert masked["class_type"] == "SetLatentNoiseMask"
    assert masked["inputs"]["mask"] == [crop, 2]
    encode = converted[masked["inputs"]["samples"][0]]
    assert encode == {
        "class_type": "VAEEncode",
        "inputs": {"pixels": [crop, 1], "vae": ["5", 0]},
        "_meta": {"title": "VAE Encode"},
    }
    assert sampler["denoise"] == 1.0

    differential = converted[sampler["model"][0]]
    assert differential["class_type"] == "DifferentialDiffusion"
    assert differential["inputs"]["model"] == ["3", 0]

    save = converted["14"]["inputs"]
    stitch = converted[save["images"][0]]
    assert stitch["class_type"] == "InpaintStitchImproved"
    assert stitch["inputs"] == {"stitcher": [crop, 0], "inpainted_image": ["13", 0]}
    assert save["filename_prefix"] == "Inpaint-MOSS"
    # Nothing else changed: prompts, seed, the LoRA and the guidance patch.
    assert converted["8"]["inputs"]["prompt"] == "make the jacket red"
    assert sampler["seed"] == 7
    assert converted["2"] == _moss_edit()["2"]
    assert converted["3"] == _moss_edit()["3"]
    # New ids go above the largest numeric one.
    assert min(int(i) for i in set(converted) - set(_moss_edit())) == 15


def _sdxl_img2img() -> dict:
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sdxl.safetensors"},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": "a"},
        },
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": ""}},
        "4": {"class_type": "LoadImage", "inputs": {"image": "subject.png"}},
        "5": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["4", 0], "vae": ["1", 2]},
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["5", 0],
                "seed": 1,
                "steps": 25,
                "cfg": 6.0,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 0.6,
            },
        },
        "7": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["6", 0], "vae": ["1", 2]},
        },
        "8": {
            "class_type": "SaveImage",
            "inputs": {"images": ["7", 0], "filename_prefix": "Inpaint-sdxl"},
        },
    }


def test_an_sdxl_img2img_graph_keeps_its_denoise_and_only_masks_the_latent():
    converted = convert_to_inpaint(_sdxl_img2img())
    crop = _one(converted, "InpaintCropImproved")
    sampler = converted["6"]["inputs"]
    assert sampler["denoise"] == 0.6
    masked = converted[sampler["latent_image"][0]]
    assert masked["class_type"] == "SetLatentNoiseMask"
    assert masked["inputs"]["samples"] == ["5", 0]
    assert converted["5"]["inputs"]["pixels"] == [crop, 1]
    assert len(_of(converted, "VAEEncode")) == 1
    # No resolution in the graph: the default target.
    assert converted[crop]["inputs"]["output_target_width"] == 1024
    # Already prefixed: not prefixed twice.
    assert converted["8"]["inputs"]["filename_prefix"] == "Inpaint-sdxl"
    added = Counter(
        n["class_type"] for i, n in converted.items() if i not in _sdxl_img2img()
    )
    assert added == Counter(
        [
            "InpaintCropImproved",
            "SetLatentNoiseMask",
            "DifferentialDiffusion",
            "InpaintStitchImproved",
        ]
    )


def test_an_empty_latents_own_size_sets_the_target_when_nothing_scales():
    graph = _moss_edit()
    graph["8"]["inputs"]["images.image_0"] = ["6", 0]
    graph["9"]["inputs"]["images.image_0"] = ["6", 0]
    del graph["7"], graph["10"]
    graph["11"]["inputs"].update(width=832, height=1216)
    converted = convert_to_inpaint(graph)
    crop = converted[_one(converted, "InpaintCropImproved")]["inputs"]
    assert (crop["output_target_width"], crop["output_target_height"]) == (832, 1216)


def test_an_image_scales_own_size_sets_the_target():
    graph = _sdxl_img2img()
    graph["9"] = {
        "class_type": "ImageScale",
        "inputs": {
            "image": ["4", 0],
            "upscale_method": "lanczos",
            "width": 512,
            "height": 768,
            "crop": "disabled",
        },
    }
    graph["5"]["inputs"]["pixels"] = ["9", 0]
    converted = convert_to_inpaint(graph)
    crop = _one(converted, "InpaintCropImproved")
    assert "9" not in converted
    assert converted["5"]["inputs"]["pixels"] == [crop, 1]
    inputs = converted[crop]["inputs"]
    assert (inputs["output_target_width"], inputs["output_target_height"]) == (512, 768)


def test_the_input_is_untouched_and_the_output_is_the_same_every_time():
    graph = _built_in("Flux2-Klein-Image-Edit.json")
    before = copy.deepcopy(graph)
    first = convert_to_inpaint(graph)
    assert graph == before
    assert json.dumps(convert_to_inpaint(graph)) == json.dumps(first)


def test_keys_that_are_not_nodes_are_carried_over():
    graph = {**_sdxl_img2img(), "pixlstash_bindings": [{"node": "4"}]}
    assert convert_to_inpaint(graph)["pixlstash_bindings"] == [{"node": "4"}]


def _no_picture() -> dict:
    graph = _sdxl_img2img()
    del graph["4"], graph["5"]
    graph["9"] = {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
    }
    graph["6"]["inputs"]["latent_image"] = ["9", 0]
    return graph


def _two_pictures() -> dict:
    graph = _sdxl_img2img()
    graph["9"] = {"class_type": "LoadImage", "inputs": {"image": "reference.png"}}
    graph["6"]["inputs"]["reference"] = ["9", 0]
    return graph


def _two_passes() -> dict:
    graph = _sdxl_img2img()
    graph["9"] = copy.deepcopy(graph["6"])
    graph["9"]["inputs"]["latent_image"] = ["6", 0]
    graph["7"]["inputs"]["samples"] = ["9", 0]
    return graph


def _encodes_something_else() -> dict:
    graph = _sdxl_img2img()
    graph["9"] = {
        "class_type": "EmptyImage",
        "inputs": {"width": 512, "height": 512, "batch_size": 1, "color": 0},
    }
    graph["5"]["inputs"]["pixels"] = ["9", 0]
    graph["10"] = {"class_type": "PreviewImage", "inputs": {"images": ["4", 0]}}
    return graph


def _mask_loader() -> dict:
    graph = _sdxl_img2img()
    graph["4"]["class_type"] = "LoadImageMask"
    return graph


def _already_inpaint() -> dict:
    return _built_in("Inpaint-Flux2-Klein-9B.json")


def _outpaint() -> dict:
    graph = _sdxl_img2img()
    graph["9"] = {
        "class_type": "ImagePadForOutpaint",
        "inputs": {"image": ["4", 0], "left": 64, "top": 0, "right": 0, "bottom": 0},
    }
    graph["5"]["inputs"]["pixels"] = ["9", 0]
    return graph


def _video() -> dict:
    graph = _sdxl_img2img()
    graph["8"]["class_type"] = "SaveAnimatedWEBP"
    return graph


def _upscale() -> dict:
    return _built_in("Upscale-2x-RealESRGAN.json")


def _no_save() -> dict:
    graph = _sdxl_img2img()
    graph["8"]["class_type"] = "ImageInvert"
    return graph


@pytest.mark.parametrize(
    "graph, says",
    [
        (_no_picture, "does not start from a picture"),
        (_two_pictures, "loads 2 pictures"),
        (_two_passes, "2 passes"),
        (_already_inpaint, "already inpaints"),
        (_outpaint, "outpaints"),
        (_video, "video"),
        (_upscale, "upscale"),
        (_no_save, "not decoded into a saved picture"),
        (_encodes_something_else, "something other than the loaded picture"),
        (_mask_loader, "LoadImageMask"),
    ],
    ids=lambda v: getattr(v, "__name__", v),
)
def test_each_shape_it_cannot_convert_is_refused_with_a_reason(graph, says):
    source = graph()
    before = copy.deepcopy(source)
    with pytest.raises(InpaintConversionRefused) as refused:
        convert_to_inpaint(source)
    assert says in refused.value.reason
    assert source == before
