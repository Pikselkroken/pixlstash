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
blended, which leaves a hard seam.

An edit graph (the picture reaches an edit encoder: ``ReferenceLatent``,
``*ImageEdit*``) is not cropped. Crop & Stitch under a noise mask came back as
a grey wash on Klein, and an edit model needs its reference lined up with what
it draws: a reference of the unpadded picture put the original in the middle
of a blank card. So it outpaints the way the ComfyUI-PixlStash outpaint
example does: every edge that read the picture reads it padded, with the pad
painted green from core nodes, the sampler starts from noise at that size, and
the model redraws the whole canvas, filling the green from the prompt. The
decode is scaled back to the padded size. The original is not pasted back: the
model re-frames, drawing the subject at its own scale, so the original laid
over the redraw put a large head on a small body.
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
# ComfyUI's own ``SaveImageAdvanced`` saves a picture too. Run does not collect
# it yet (``SAVE_NODE_CLASSES``), so a graph saving with it converts and stays
# exactly as runnable as it was.
_SAVES = SAVE_NODE_CLASSES | {"SaveImageAdvanced"}
_SWITCH_CLASS = "ComfySwitchNode"

_SAMPLERS = frozenset({"KSampler", "KSamplerAdvanced", "SamplerCustomAdvanced"})
_RESIZE_RE = re.compile(r"^ImageScale")
_EMPTY_LATENT_RE = re.compile(r"Empty.*Latent")
_SAVE_PREFIX = {"inpaint": "Inpaint-", "outpaint": "Outpaint-"}
_PAD_FEATHER = 40
_GREEN = 0x00FF00
# An edit model's reference encoders. A ControlNet or IP-adapter is not one:
# a graph with only those takes the padded crop like an img2img graph.
# Qwen Image 2.1's encoder edits too, though its name does not say so.
_EDIT_ENCODER_RE = re.compile(r"^ReferenceLatent$|ImageEdit|^TextEncodeQwenImage21$")
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
        InpaintConversionRefused: The graph is not a single-pass img2img,
            edit or txt2img graph with one picture to edit, or is already
            inpaint, outpaint, video or upscale.
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
    latent_link, switches = _through_switches(nodes, sampler["inputs"]["latent_image"])
    latent_source = nodes.get(latent_link[0], {}).get("class_type", "")
    from_empty = bool(_EMPTY_LATENT_RE.search(latent_source))
    # An edit encoder that hands out its own latent of the picture
    # (``TextEncodeQwenImage21``): started from a ``VAEEncode`` of the crop,
    # as the built-in inpaint workflows do, so the latent is the crop's size.
    from_picture = (
        latent_source != "VAEEncode"
        and not from_empty
        and latent_link[0] in nodes
        and _reads_picture(nodes, latent_link[0], loader)
    )
    replaced = [latent_link[0]] if from_picture else []
    if latent_source != "VAEEncode" and not from_empty and not from_picture:
        raise InpaintConversionRefused(
            f"Its sampler starts from {latent_source or 'nothing'}, not from the "
            "picture or an empty latent, so PixlStash cannot convert it."
        )

    empty = latent_link[0] if from_empty else None
    resizers = _resizers_of(nodes, loader)
    width, height = _target_size(nodes, resizers, empty)
    if target == "outpaint" and _feeds_edit_encoder(nodes, {loader} | resizers):
        if (
            not from_empty
            and not from_picture
            and not _encodes(nodes, latent_link, {loader} | resizers)
        ):
            raise InpaintConversionRefused(
                "Its sampler starts from an encode of something other than the "
                "loaded picture, so PixlStash cannot convert it."
            )
        _outpaint_edit(out, nodes, new_id, loader, resizers, decode_id, empty)
        if "denoise" in sampler["inputs"]:
            sampler["inputs"]["denoise"] = 1.0
        return out

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
    if (
        latent_source == "VAEEncode"
        and nodes[latent_link[0]]["inputs"].get("pixels") != crop_link
    ):
        raise InpaintConversionRefused(
            "Its sampler starts from an encode of something other than the "
            "loaded picture, so PixlStash cannot convert it."
        )

    # The latent: the crop's, under the crop's mask.
    if from_empty or from_picture:
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
        if from_empty and "denoise" in sampler["inputs"]:
            sampler["inputs"]["denoise"] = 1.0
    masked = next(new_id)
    out[masked] = nodes[masked] = {
        "class_type": "SetLatentNoiseMask",
        "inputs": {"samples": latent_link, "mask": [crop, 2]},
        "_meta": {"title": "SetLatentNoiseMask"},
    }
    sampler["inputs"]["latent_image"] = [masked, 0]
    if target == "outpaint" and "denoise" in sampler["inputs"]:
        # From noise: under an img2img graph's partial denoise the pad stays grey.
        sampler["inputs"]["denoise"] = 1.0

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
    _prefix_saves(nodes, stitch, _SAVE_PREFIX[target])

    # Sizing nodes the crop made redundant.
    candidates = (
        set(resizers)
        | _switched_off(nodes, switches)
        | set(replaced)
        | {
            node_id
            for node_id, node in nodes.items()
            if node["class_type"] == "GetImageSize"
            and node["inputs"].get("image") == crop_link
        }
    )
    if empty is not None:
        candidates.add(empty)
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


def _feeds_edit_encoder(nodes: dict, picture: set[str]) -> bool:
    """Whether an edit encoder reads the picture, directly or through a
    ``VAEEncode`` of it."""
    for node in nodes.values():
        if not _EDIT_ENCODER_RE.search(node["class_type"]):
            continue
        for value in node["inputs"].values():
            if is_link(value) and (
                (value[1] == 0 and value[0] in picture)
                or _encodes(nodes, value, picture)
            ):
                return True
    return False


def _encodes(nodes: dict, link: list, picture: set[str]) -> bool:
    """Whether *link* is a ``VAEEncode`` of the picture."""
    source = nodes.get(link[0], {})
    pixels = source.get("inputs", {}).get("pixels")
    return (
        source.get("class_type") == "VAEEncode"
        and is_link(pixels)
        and pixels[1] == 0
        and pixels[0] in picture
    )


def _outpaint_edit(
    graph: dict,
    nodes: dict,
    new_id,
    loader: str,
    resizers: set[str],
    decode_id: str,
    empty: str | None,
) -> None:
    """Outpaint an edit graph: the whole canvas redrawn from the green-padded
    picture, scaled to the padded size."""
    pad, size, green, threshold, painted = (next(new_id) for _ in range(5))
    for node in nodes.values():
        inputs = node.get("inputs", {})
        for name, value in inputs.items():
            if is_link(value) and value == [loader, 0]:
                inputs[name] = [painted, 0]
    _pad(graph, nodes, loader, pad)
    added = {
        size: ("GetImageSize", {"image": [pad, 0]}, "Get Image Size"),
        green: (
            "EmptyImage",
            {"width": [size, 0], "height": [size, 1], "batch_size": 1, "color": _GREEN},
            "Green",
        ),
        # The pad itself, not the feathered rim inside the picture.
        threshold: (
            "ThresholdMask",
            {"mask": [pad, 1], "value": 0.99},
            "The pad without its feather",
        ),
        painted: (
            "ImageCompositeMasked",
            {
                "destination": [pad, 0],
                "source": [green, 0],
                "x": 0,
                "y": 0,
                "resize_source": False,
                "mask": [threshold, 0],
            },
            "Paint the pad green",
        ),
    }
    # An empty latent of a literal size would draw the canvas at another
    # aspect: it takes the size the encoders see (the last resize, if any).
    if empty is not None:
        latent = nodes[empty]["inputs"]
        if not (is_link(latent.get("width")) and is_link(latent.get("height"))):
            fed = {nodes[r]["inputs"]["image"][0] for r in resizers}
            last = next((r for r in sorted(resizers) if r not in fed), painted)
            sized = next(new_id)
            added[sized] = ("GetImageSize", {"image": [last, 0]}, "Get Image Size")
            latent.update(width=[sized, 0], height=[sized, 1])
    # No paste-back: the model re-frames, drawing the face smaller than the
    # reference's, so the original laid over it is a big head on a small body.
    scale = next(new_id)
    for node in nodes.values():
        inputs = node.get("inputs", {})
        for name, value in inputs.items():
            if is_link(value) and value == [decode_id, 0]:
                inputs[name] = [scale, 0]
    added[scale] = (
        "ImageScale",
        {
            "image": [decode_id, 0],
            "upscale_method": "lanczos",
            "width": [size, 0],
            "height": [size, 1],
            "crop": "disabled",
        },
        "Back to the padded size",
    )
    for node_id, (class_type, inputs, title) in added.items():
        graph[node_id] = nodes[node_id] = {
            "class_type": class_type,
            "inputs": inputs,
            "_meta": {"title": title},
        }
    _prefix_saves(nodes, scale, _SAVE_PREFIX["outpaint"])


def _prefix_saves(nodes: dict, start: str, save_prefix: str) -> None:
    """Prefix every save *start* reaches with *save_prefix*, once."""
    for node_id in _downstream(nodes, start):
        node = nodes[node_id]
        prefix = node["inputs"].get("filename_prefix")
        if (
            node["class_type"] in _SAVES
            and isinstance(prefix, str)
            and not prefix.startswith(save_prefix)
        ):
            node["inputs"]["filename_prefix"] = save_prefix + prefix


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
        loaders = [_edited_picture(nodes, sorted(loaders))]
    if nodes[loaders[0]]["class_type"] not in _CROPPABLE_LOADERS:
        raise InpaintConversionRefused(
            f"Its picture loader, {nodes[loaders[0]]['class_type']}, does not "
            "hand out the picture and its selection, so PixlStash cannot crop "
            "to it. Load the picture with Load Image."
        )
    return loaders[0]


def _upstream(nodes: dict, start: str) -> set[str]:
    """Every node *start* reads, directly or not."""
    seen: set[str] = set()
    stack = [start]
    while stack:
        for value in nodes.get(stack.pop(), {}).get("inputs", {}).values():
            if is_link(value) and value[0] in nodes and value[0] not in seen:
                seen.add(value[0])
                stack.append(value[0])
    return seen


def _through_switches(nodes: dict, link: list) -> tuple[list, list[str]]:
    """*link* followed through each ``ComfySwitchNode`` set by a literal.

    Returns where it ends up and the switches it went through. A switch wired
    to something else, whose chosen side is not wired, or that is met again
    (a loop), is where it stops: what it hands on is not in the graph to read.
    """
    switches: list[str] = []
    while nodes.get(link[0], {}).get("class_type") == _SWITCH_CLASS:
        inputs = nodes[link[0]]["inputs"]
        chosen = inputs.get("on_true" if inputs.get("switch") else "on_false")
        if (
            not isinstance(inputs.get("switch"), bool)
            or not is_link(chosen)
            or link[0] in switches
        ):
            break
        switches.append(link[0])
        link = chosen
    return link, switches


def _switched_off(nodes: dict, switches: list[str]) -> set[str]:
    """*switches*, and what they chose between: once the sampler starts from
    the crop, nothing may read them."""
    return set(switches) | {
        value[0]
        for switch in switches
        for value in nodes[switch]["inputs"].values()
        if is_link(value)
    }


def _reads_picture(nodes: dict, node_id: str, loader: str) -> bool:
    """True when *node_id* takes the loader's picture, or a resize of it."""
    picture = {loader} | _resizers_of(nodes, loader)
    return any(
        is_link(value) and value[1] == 0 and value[0] in picture
        for value in nodes[node_id]["inputs"].values()
    )


def _natural(name: str) -> list:
    """``image_2`` before ``image_10``: digit runs compare as numbers."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def _edited_picture(nodes: dict, loaders: list[str]) -> str:
    """Which of several picture *loaders* the sampler edits.

    The one the sampler's starting latent is made from (an img2img encode
    beside a reference picture). When that latent is made from more than one,
    or from none, the one every node feeding the sampler several of them takes
    on its first input (``image_1`` before ``image_2``), which is the picture
    an edit model edits.
    """
    sampler_id, _ = _only_sampler(nodes)
    start, _ = _through_switches(nodes, nodes[sampler_id]["inputs"]["latent_image"])
    behind = _upstream(nodes, start[0]) | {start[0]}
    reached = [loader for loader in loaders if loader in behind]
    if len(reached) == 1:
        return reached[0]
    # A resize of a picture stands for it.
    picture_of = {}
    for loader in reached or loaders:
        picture_of[loader] = loader
        picture_of.update(dict.fromkeys(_resizers_of(nodes, loader), loader))
    # ponytail: input-name order is the whole tie-break; a graph whose nodes
    # disagree, or that never takes two pictures in one node, is refused.
    firsts = set()
    for node_id in _upstream(nodes, sampler_id):
        fed = sorted(
            (_natural(name), picture_of[value[0]])
            for name, value in nodes[node_id].get("inputs", {}).items()
            if is_link(value) and value[1] == 0 and value[0] in picture_of
        )
        if len({picture for _, picture in fed}) > 1:
            firsts.add(fed[0][1])
    if len(firsts) == 1:
        return firsts.pop()
    raise InpaintConversionRefused(
        f"This workflow loads {len(loaders)} pictures, and PixlStash cannot "
        "tell which one it edits."
    )


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
                nodes[d]["class_type"] in _SAVES for d in _downstream(nodes, node_id)
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
