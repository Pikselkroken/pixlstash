"""Rewrite an img2img, image-edit or txt2img API graph to inpaint or outpaint.

Inpainting is #2446; outpainting is the same transform with a pad in front of
the crop.

A pure, deterministic transform: API graph in, a new API graph out, read from
node classes and edges alone. The picture is cropped to the selection plus
context at the model's working resolution (``InpaintCropImproved``), every edge
that carried the picture carries the crop instead, the sampler starts from the
crop's latent under the crop's mask, and the decode is stitched back into the
untouched original (``InpaintStitchImproved``) before anything saves it.

There is no per-model table. "Every edge that carried the loader's picture" is
the whole rule, which is what makes an img2img ``VAEEncode`` and an edit
model's reference input (``ReferenceLatent``, ``TextEncodeQwenImageEdit*``,
``TextEncodeMOSSImageEdit``'s ``images.image_0``) the same case.

``DifferentialDiffusion`` goes on the sampler's model unconditionally. The
built-in inpaint workflows omit it, with nothing recorded to say that was
deliberate; the crop's mask is blended (``mask_blend_pixels``), and a soft mask
is what differential diffusion exists to honour.

A txt2img graph has no picture to start from, so a ``LoadImage`` is put in and
the sampler starts from an encode of the crop instead of the empty latent.

Outpainting pads the picture first (``ImagePadForOutpaint``, every side 0 and
a 40 px feather until a run writes the extension into it) and crops the padded
canvas to the pad's mask, so the stitch pastes into the extended canvas. The
crop's own ``extend_for_outpainting`` stays off: it extends after the mask is
blended, which leaves a hard seam. An edit model's reference encoders
(``ReferenceLatent``, ``*ImageEdit*``) keep the picture they read before, not
the padded crop: an edit model copies its reference, so a reference holding
the pad comes back with the pad in it (a green pad stayed green on Klein even
when told to fill it, and tinted the fill when only the canvas was green).
Only the sampler's latent works on the padded canvas.
"""

from __future__ import annotations

import copy
import math
import re

from pixlstash.services.comfyui_service import SAVE_NODE_CLASSES
from pixlstash.services.workflow_hash import (
    WorkflowGraphError,
    is_link,
    reduce_api_graph,
)
from pixlstash.services.workflow_identity import reduced_workflow_type
from pixlstash.services.workflow_io import is_picture_loader

CROP_CLASS = "InpaintCropImproved"
PAD_CLASS = "ImagePadForOutpaint"
STITCH_CLASS = "InpaintStitchImproved"

# The crop node's settings, copied from the built-in
# ``Inpaint-Flux2-Klein-9B.json``. The target size is the graph's own.
CROP_WIDGETS = {
    "downscale_algorithm": "bilinear",
    "upscale_algorithm": "bicubic",
    "preresize": False,
    "preresize_mode": "ensure minimum resolution",
    "preresize_min_width": 1024,
    "preresize_min_height": 1024,
    "preresize_max_width": 16384,
    "preresize_max_height": 16384,
    "mask_fill_holes": True,
    "mask_expand_pixels": 8,
    "mask_invert": False,
    "mask_blend_pixels": 32,
    "mask_hipass_filter": 0.1,
    "extend_for_outpainting": False,
    "extend_up_factor": 1.0,
    "extend_down_factor": 1.0,
    "extend_left_factor": 1.0,
    "extend_right_factor": 1.0,
    "context_from_mask_extend_factor": 1.6,
    "output_resize_to_target_size": True,
    "output_padding": "16",
    "device_mode": "gpu (much faster)",
}
DEFAULT_TARGET = 1024

_SAMPLERS = frozenset({"KSampler", "KSamplerAdvanced", "SamplerCustomAdvanced"})
_RESIZE_RE = re.compile(r"^ImageScale")
_EMPTY_LATENT_RE = re.compile(r"Empty.*Latent")
_SAVE_PREFIX = {"inpaint": "Inpaint-", "outpaint": "Outpaint-"}
_PAD_FEATHER = 40
# An edit model's reference encoders. A ControlNet or IP-adapter is not one:
# it takes the padded crop like any other edge that carried the picture.
_EDIT_ENCODER_RE = re.compile(r"^ReferenceLatent$|ImageEdit")
# Loaders known to put the picture on output 0 and its alpha as a MASK on 1,
# which is what the crop reads. ``is_picture_loader`` also matches loaders
# that do not (``LoadImageMask`` has a MASK alone).
_CROPPABLE_LOADERS = frozenset(
    {"LoadImage", "LoadImageOutput", "PixlStashPictureLoader"}
)

_TYPE_REFUSALS = {
    "inpaint": {
        "inpaint": "This workflow already inpaints.",
        "outpaint": (
            "This workflow outpaints, so it already works on part of a picture."
        ),
        "video": "A video workflow cannot be converted to inpainting.",
        "upscale": "An upscale workflow has no sampler to inpaint with.",
    },
    "outpaint": {
        "inpaint": (
            "This workflow already inpaints. Convert the image-to-image or "
            "edit workflow it came from instead."
        ),
        "outpaint": "This workflow already outpaints.",
        "video": "A video workflow cannot be converted to outpainting.",
        "upscale": "An upscale workflow has no sampler to outpaint with.",
    },
}


class InpaintConversionRefused(ValueError):
    """The graph is not one this transform can rewrite; ``reason`` says why."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def convert_to_inpaint(graph: dict) -> dict:
    """A copy of *graph* that inpaints the selection with Crop & Stitch.

    *graph* is not modified. Keys that are not nodes (``pixlstash_bindings``)
    are carried over as they are; new nodes take ids above the largest numeric
    one.

    Raises:
        InpaintConversionRefused: The graph is not a single-pass, one-picture
            img2img, edit or txt2img graph, or is already inpaint, outpaint,
            video or upscale.
    """
    return _convert(graph, "inpaint")


def convert_to_outpaint(graph: dict) -> dict:
    """A copy of *graph* that extends the picture and redraws only the new area.

    The pad sits between the loader and the crop with every side 0, so the
    copy pads nothing until a run writes an extension into it. Otherwise as
    :func:`convert_to_inpaint`.

    Raises:
        InpaintConversionRefused: As :func:`convert_to_inpaint`, worded for
            outpainting.
    """
    return _convert(graph, "outpaint")


def _convert(graph: dict, target: str) -> dict:
    try:
        kind = reduced_workflow_type(reduce_api_graph(graph))
    except WorkflowGraphError as exc:
        raise InpaintConversionRefused(
            f"PixlStash cannot read this workflow's graph: {exc}"
        ) from exc
    refusals = _TYPE_REFUSALS[target]
    if kind in refusals:
        raise InpaintConversionRefused(refusals[kind])

    out = copy.deepcopy(graph)
    nodes = {
        node_id: node
        for node_id, node in out.items()
        if isinstance(node, dict) and "class_type" in node
    }
    new_id = _id_counter(out)
    loader = _only_loader(nodes, txt2img=kind == "txt2img")
    if loader is None:
        loader = next(new_id)
        out[loader] = nodes[loader] = {
            "class_type": "LoadImage",
            "inputs": {"image": "example.png"},
            "_meta": {"title": "Load Image"},
        }
    sampler_id, model_holder = _only_sampler(nodes)
    sampler = nodes[sampler_id]
    decode_id = _saved_decode(nodes, sampler_id)
    vae = nodes[decode_id]["inputs"].get("vae")
    latent_link = sampler["inputs"]["latent_image"]
    latent_source = nodes.get(latent_link[0], {}).get("class_type", "")
    from_empty = bool(_EMPTY_LATENT_RE.search(latent_source))
    if latent_source != "VAEEncode" and not from_empty:
        raise InpaintConversionRefused(
            f"Its sampler starts from {latent_source or 'nothing'}, not from the "
            "picture or an empty latent, so PixlStash cannot convert it."
        )

    empty = latent_link[0] if from_empty else None
    resizers = _resizers_of(nodes, loader)
    width, height = _target_size(nodes, resizers, empty)
    references = (
        _reference_edges(nodes, {loader} | resizers) if target == "outpaint" else []
    )
    if references:
        # A reference read through a resize keeps the resize on the picture;
        # one nothing reads any more is pruned below as before.
        references += [
            (node_id, "image", list(nodes[node_id]["inputs"]["image"]), None)
            for node_id in resizers
        ]

    # The crop, and every edge that carried the picture now carries it.
    crop = next(new_id)
    crop_link = [crop, 1]
    for node in nodes.values():
        inputs = node.get("inputs", {})
        for name, value in inputs.items():
            if (
                is_link(value)
                and value[1] == 0
                and (value[0] == loader or value[0] in resizers)
            ):
                inputs[name] = list(crop_link)
    crop_image, crop_mask = [loader, 0], [loader, 1]
    if target == "outpaint":
        crop_image, crop_mask = _pad(out, nodes, loader, next(new_id))
    out[crop] = nodes[crop] = {
        "class_type": CROP_CLASS,
        "inputs": {
            "image": crop_image,
            "mask": crop_mask,
            **CROP_WIDGETS,
            "output_target_width": width,
            "output_target_height": height,
        },
        "_meta": {"title": "Crop to selection + context"},
    }
    if not from_empty and nodes[latent_link[0]]["inputs"].get("pixels") != crop_link:
        raise InpaintConversionRefused(
            "Its sampler starts from an encode of something other than the "
            "loaded picture, so PixlStash cannot convert it."
        )

    # The latent: the crop's, under the crop's mask.
    if from_empty:
        encode = next(
            (
                node_id
                for node_id, node in nodes.items()
                if node["class_type"] == "VAEEncode"
                and node["inputs"].get("pixels") == crop_link
                and node["inputs"].get("vae") == vae
            ),
            None,
        )
        if encode is None:
            encode = next(new_id)
            out[encode] = nodes[encode] = {
                "class_type": "VAEEncode",
                "inputs": {"pixels": list(crop_link), "vae": vae},
                "_meta": {"title": "VAE Encode"},
            }
        latent_link = [encode, 0]
        if "denoise" in sampler["inputs"]:
            sampler["inputs"]["denoise"] = 1.0
    masked = next(new_id)
    out[masked] = nodes[masked] = {
        "class_type": "SetLatentNoiseMask",
        "inputs": {"samples": latent_link, "mask": [crop, 2]},
        "_meta": {"title": "SetLatentNoiseMask"},
    }
    sampler["inputs"]["latent_image"] = [masked, 0]

    # The model, after every patch the sampler already reads.
    holder_inputs = nodes[model_holder]["inputs"]
    differential = next(new_id)
    out[differential] = nodes[differential] = {
        "class_type": "DifferentialDiffusion",
        "inputs": {"model": holder_inputs["model"]},
        "_meta": {"title": "Differential Diffusion"},
    }
    holder_inputs["model"] = [differential, 0]

    # The output: stitched back into the original before anything reads it.
    stitch = next(new_id)
    for node in nodes.values():
        inputs = node.get("inputs", {})
        for name, value in inputs.items():
            if is_link(value) and value == [decode_id, 0]:
                inputs[name] = [stitch, 0]
    out[stitch] = nodes[stitch] = {
        "class_type": STITCH_CLASS,
        "inputs": {"stitcher": [crop, 0], "inpainted_image": [decode_id, 0]},
        "_meta": {"title": "Stitch into the original"},
    }
    save_prefix = _SAVE_PREFIX[target]
    for node_id in _downstream(nodes, stitch):
        node = nodes[node_id]
        prefix = node["inputs"].get("filename_prefix")
        if (
            node["class_type"] in SAVE_NODE_CLASSES
            and isinstance(prefix, str)
            and not prefix.startswith(save_prefix)
        ):
            node["inputs"]["filename_prefix"] = save_prefix + prefix

    # Sizing nodes the crop made redundant.
    candidates = set(resizers) | {
        node_id
        for node_id, node in nodes.items()
        if node["class_type"] == "GetImageSize"
        and node["inputs"].get("image") == crop_link
    }
    if empty is not None:
        candidates.add(empty)
    _restore_references(out, nodes, new_id, references)
    _prune(out, nodes, candidates)
    return out


def _pad(graph: dict, nodes: dict, loader: str, pad: str) -> tuple[list, list]:
    """Pad the loaded picture; the crop's ``image`` and ``mask`` links.

    The pad reads the loader directly, which is what a run's extension looks
    for.
    """
    graph[pad] = nodes[pad] = {
        "class_type": PAD_CLASS,
        "inputs": {
            "image": [loader, 0],
            "left": 0,
            "top": 0,
            "right": 0,
            "bottom": 0,
            "feathering": _PAD_FEATHER,
        },
        "_meta": {"title": "Pad for outpainting"},
    }
    return [pad, 0], [pad, 1]


def _reference_edges(nodes: dict, picture: set[str]) -> list[tuple]:
    """Each edge into an edit encoder that carries the picture, before the
    crop takes it: ``(node, input, picture link, vae)``, *vae* set when the
    edge goes through a ``VAEEncode`` of the picture."""
    edges = []
    for node_id, node in nodes.items():
        if not _EDIT_ENCODER_RE.search(node["class_type"]):
            continue
        for name, value in node["inputs"].items():
            if not is_link(value):
                continue
            if value[1] == 0 and value[0] in picture:
                edges.append((node_id, name, list(value), None))
                continue
            source = nodes.get(value[0], {})
            pixels = source.get("inputs", {}).get("pixels")
            if (
                source.get("class_type") == "VAEEncode"
                and is_link(pixels)
                and pixels[1] == 0
                and pixels[0] in picture
            ):
                edges.append((node_id, name, list(pixels), source["inputs"]["vae"]))
    return edges


def _restore_references(graph: dict, nodes: dict, new_id, edges: list) -> None:
    """Point each of *edges* back at the picture it read, through a fresh
    ``VAEEncode`` (one per picture and VAE) where it went through one."""
    encodes: dict[str, str] = {}
    for node_id, name, picture, vae in edges:
        if vae is None:
            nodes[node_id]["inputs"][name] = picture
            continue
        key = repr((picture, vae))
        if key not in encodes:
            encodes[key] = next(new_id)
            graph[encodes[key]] = nodes[encodes[key]] = {
                "class_type": "VAEEncode",
                "inputs": {"pixels": picture, "vae": vae},
                "_meta": {"title": "Encode the reference"},
            }
        nodes[node_id]["inputs"][name] = [encodes[key], 0]


def _id_counter(graph: dict):
    """Fresh node ids above the largest numeric one, skipping any in use."""
    next_id = max((int(k) for k in graph if str(k).isdigit()), default=0) + 1
    while True:
        if str(next_id) not in graph:
            yield str(next_id)
        next_id += 1


def _consumers(nodes: dict, source: str) -> list[str]:
    return [
        node_id
        for node_id, node in nodes.items()
        if any(
            is_link(value) and value[0] == source
            for value in node.get("inputs", {}).values()
        )
    ]


def _downstream(nodes: dict, start: str) -> list[str]:
    """Every node fed, directly or not, by *start*."""
    seen: list[str] = []
    stack = [start]
    while stack:
        for node_id in _consumers(nodes, stack.pop()):
            if node_id not in seen:
                seen.append(node_id)
                stack.append(node_id)
    return seen


def _prune(graph: dict, nodes: dict, candidates: set[str]) -> None:
    """Delete each of *candidates* nothing reads, until none is left to delete."""
    removed = True
    while removed:
        removed = False
        for node_id in sorted(candidates):
            if node_id in nodes and not _consumers(nodes, node_id):
                del graph[node_id], nodes[node_id]
                candidates.discard(node_id)
                removed = True


def _only_loader(nodes: dict, txt2img: bool = False) -> str | None:
    """The one picture loader, or None for a txt2img graph that has none."""
    loaders = [i for i, n in nodes.items() if is_picture_loader(n["class_type"])]
    if not loaders and txt2img:
        return None
    if not loaders:
        raise InpaintConversionRefused(
            "This workflow does not start from a picture, so there is nothing to "
            "inpaint. Convert an image-to-image or image-edit workflow."
        )
    if len(loaders) > 1:
        raise InpaintConversionRefused(
            f"This workflow loads {len(loaders)} pictures. Only a workflow with "
            "one picture input can be converted."
        )
    if nodes[loaders[0]]["class_type"] not in _CROPPABLE_LOADERS:
        raise InpaintConversionRefused(
            f"Its picture loader, {nodes[loaders[0]]['class_type']}, does not "
            "hand out the picture and its selection, so PixlStash cannot crop "
            "to it. Load the picture with Load Image."
        )
    return loaders[0]


def _only_sampler(nodes: dict) -> tuple[str, str]:
    """The one sampler's id, and the id of the node holding its ``model``."""
    samplers = [
        i
        for i, n in nodes.items()
        if "Sampler" in n["class_type"] and is_link(n["inputs"].get("latent_image"))
    ]
    if not samplers:
        raise InpaintConversionRefused(
            "This workflow has no sampler PixlStash can convert."
        )
    if len(samplers) > 1:
        raise InpaintConversionRefused(
            f"This workflow samples in {len(samplers)} passes (a refiner or a "
            "hires fix). Only a single-pass workflow can be converted."
        )
    sampler_id = samplers[0]
    sampler = nodes[sampler_id]
    if sampler["class_type"] not in _SAMPLERS:
        raise InpaintConversionRefused(
            f"Its sampler, {sampler['class_type']}, is not one PixlStash can "
            "convert (KSampler, KSamplerAdvanced or SamplerCustomAdvanced)."
        )
    holder = sampler_id
    if sampler["class_type"] == "SamplerCustomAdvanced":
        guider = sampler["inputs"].get("guider")
        holder = guider[0] if is_link(guider) else None
    if holder not in nodes or not is_link(nodes[holder]["inputs"].get("model")):
        raise InpaintConversionRefused(
            "PixlStash cannot find the model its sampler reads."
        )
    return sampler_id, holder


def _saved_decode(nodes: dict, sampler_id: str) -> str:
    """The ``VAEDecode`` of the sampler's output that reaches a save."""
    for node_id, node in nodes.items():
        if (
            node["class_type"] == "VAEDecode"
            and node["inputs"].get("samples") == [sampler_id, 0]
            and any(
                nodes[d]["class_type"] in SAVE_NODE_CLASSES
                for d in _downstream(nodes, node_id)
            )
        ):
            return node_id
    raise InpaintConversionRefused(
        "Its sampler's output is not decoded into a saved picture."
    )


def _resizers_of(nodes: dict, loader: str) -> set[str]:
    """``ImageScale*`` nodes the picture reaches through resize nodes alone."""
    found: set[str] = set()
    grew = True
    while grew:
        grew = False
        for node_id, node in nodes.items():
            image = node["inputs"].get("image")
            if (
                node_id not in found
                and _RESIZE_RE.search(node["class_type"])
                and is_link(image)
                and image[1] == 0
                and (image[0] == loader or image[0] in found)
            ):
                found.add(node_id)
                grew = True
    return found


def _target_size(nodes: dict, resizers: set[str], empty: str | None) -> tuple[int, int]:
    """The working resolution the graph already asks for.

    A resize of the picture says it (``ImageScaleToTotalPixels``' megapixels,
    an ``ImageScale*``'s literal width and height), else the empty latent's
    literal size. A graph that says neither gets 1024 x 1024. In a chain of
    resizes the last one (no other resize reads it) is asked first, since it is
    the size the graph works at.
    """
    fed = {
        nodes[node_id]["inputs"]["image"][0]
        for node_id in resizers
        if is_link(nodes[node_id]["inputs"].get("image"))
    }
    for node_id in sorted(resizers, key=lambda node_id: (node_id in fed, node_id)):
        inputs = nodes[node_id]["inputs"]
        megapixels = inputs.get("megapixels")
        if nodes[node_id]["class_type"] == "ImageScaleToTotalPixels" and isinstance(
            megapixels, (int, float)
        ):
            steps = inputs.get("resolution_steps")
            steps = steps if isinstance(steps, int) and steps > 0 else 1
            side = max(steps, round(math.sqrt(megapixels) * 1024 / steps) * steps)
            return side, side
        width, height = inputs.get("width"), inputs.get("height")
        if isinstance(width, int) and isinstance(height, int) and width > 0 < height:
            return width, height
    if empty is not None:
        width = nodes[empty]["inputs"].get("width")
        height = nodes[empty]["inputs"].get("height")
        if isinstance(width, int) and isinstance(height, int):
            return width, height
    return DEFAULT_TARGET, DEFAULT_TARGET
