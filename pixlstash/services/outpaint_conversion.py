"""Build the FLUX.1 Fill outpaint graph a workflow converts to (#2812).

An edit model (Klein, Kontext, Qwen Edit) redraws the whole canvas at its own
framing, so it cannot outpaint: with the original pasted back the head came
out too big for the body, and without it the original was redrawn (#2551). A
Fill model extends the picture instead, drawing the new area at the scale of
what is already there, so pasting the original back is correct.

So Convert to Outpainting does not rewrite the source graph. It builds one
graph, the one the #2812 smoke test ran, carrying over only the source's
positive prompt:

``LoadImage`` → ``ImagePadForOutpaint`` (at the picture's own size; every side
0 and a 40 px feather until a run writes the extension into it) →
``ImageScaleToTotalPixels`` (the padded canvas to :data:`FILL_MEGAPIXELS`, the
range Flux is comfortable at) → ``InpaintModelConditioning`` (noise mask on;
it scales the pad's mask to the pixels itself) with FLUX.1 Fill [dev] under
``DifferentialDiffusion``, ``FluxGuidance`` 30, cfg 1, euler/normal → the
decode scaled back to the padded size → ``ImageCompositeMasked`` over the
padded original through the pad's mask, so the original pixels are kept at
their own resolution → ``SaveImage``.

The model files are those of ComfyUI's own Flux Fill templates, so a ComfyUI
without them is refused with ``missing_models`` and each file offers its
download (``services/model_downloads.py``; the Fill model comes from
Comfy-Org's ungated copy, not the gated Black Forest Labs repository). There
is no fallback to another model.

A Qwen Image 2.1 or FLUX.2 workflow converts to an outpainter on its own
family's edit model instead (:func:`edit_outpaint_graph`, issue #2812's probes):
the pad is filled white, the whole padded canvas is the edit's only reference
and is sampled at exactly its own size, so the model keeps the original's
framing, and the decode is pasted back as the Fill graph's is. Their models are
the built-in Inpaint workflows' (``FLUX.2 Klein 9B Inpaint.json``,
``Qwen-Image 2.1 Inpaint.json``), and their prompt is an instruction
(:data:`EDIT_PROMPT`), not the source's.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

from pixlstash.services.inpaint_conversion import InpaintConversionRefused
from pixlstash.services.workflow_hash import WorkflowGraphError, reduce_api_graph
from pixlstash.services.workflow_identity import reduced_workflow_type
from pixlstash.services.workflow_io import detect_workflow_io, prompt_field
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

PAD_CLASS = "ImagePadForOutpaint"
FILL_MODEL = "flux1-fill-dev.safetensors"
CLIP_L = "clip_l.safetensors"
T5XXL = "t5xxl_fp16.safetensors"
FLUX_VAE = "ae.safetensors"
# The padded canvas is sampled at about this size and the result scaled back
# up. The smoke test sampled 2.1 MP in about 40 s on one consumer GPU.
FILL_MEGAPIXELS = 2.0
PAD_FEATHER = 40
SAVE_PREFIX = "Outpaint-"
QWEN_IMAGE_21 = "qwen_image_21"
KLEIN = "flux2_klein"
# What the probes prompted with. "White rectangles" is the pad; red bled a cast
# into the skin.
EDIT_PROMPT = "Outpaint the image. Remove the white rectangles. Same picture style."
_BUILT_IN_DIR = os.path.join(
    os.path.dirname(__file__), "..", "data", "comfyui-workflows", "built-in"
)
# The built-in whose model loaders each family's outpainter loads.
_FAMILY_SOURCE = {
    KLEIN: "FLUX.2 Klein 9B Inpaint.json",
    QWEN_IMAGE_21: "Qwen-Image 2.1 Inpaint.json",
}
_LOADER_CLASSES = ("UNETLoader", "PixlStashCLIPLoader", "PixlStashVAELoader")

_TYPE_REFUSALS = {
    "inpaint": (
        "This workflow already inpaints. Convert the image-to-image or "
        "edit workflow it came from instead."
    ),
    "outpaint": "This workflow already outpaints.",
    "video": "A video workflow cannot be converted to outpainting.",
    "upscale": "An upscale workflow has no sampler to outpaint with.",
}


def convert_to_outpaint(graph: dict) -> dict:
    """The FLUX.1 Fill outpaint graph, prompted as *graph* is.

    *graph* is not modified, and nothing else of it is kept: a Qwen Image 2.1
    or FLUX.2 graph's copy runs that family's edit model
    (:func:`edit_outpaint_graph`), any other's the Fill model.

    Raises:
        InpaintConversionRefused: *graph* cannot be read, or is already
            inpaint, outpaint, video or upscale.
    """
    try:
        kind = reduced_workflow_type(reduce_api_graph(graph))
        io = detect_workflow_io(graph)
    except WorkflowGraphError as exc:
        raise InpaintConversionRefused(
            f"PixlStash cannot read this workflow's graph: {exc}"
        ) from exc
    if kind in _TYPE_REFUSALS:
        raise InpaintConversionRefused(_TYPE_REFUSALS[kind])
    family = edit_family(graph)
    if family:
        return edit_outpaint_graph(family)
    return fill_outpaint_graph(_positive_prompt(graph, io.positive_prompt_nodes))


def fill_outpaint_graph(prompt: str = "") -> dict:
    """The FLUX.1 Fill outpaint graph, with every side 0 and *prompt*."""

    def node(class_type: str, title: str, **inputs) -> dict:
        return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}

    return {
        "1": node("LoadImage", "Load Image", image="example.png"),
        "2": node(
            PAD_CLASS,
            "Pad for outpainting",
            image=["1", 0],
            left=0,
            top=0,
            right=0,
            bottom=0,
            feathering=PAD_FEATHER,
        ),
        "3": node(
            "ImageScaleToTotalPixels",
            "Canvas to the Fill model's size",
            image=["2", 0],
            upscale_method="lanczos",
            megapixels=FILL_MEGAPIXELS,
            # Flux packs 2x2 latent patches of an 8x VAE: a multiple of 16
            # is never cropped.
            resolution_steps=16,
        ),
        "4": node(
            "UNETLoader",
            "Load FLUX.1 Fill",
            unet_name=FILL_MODEL,
            weight_dtype="default",
        ),
        "5": node(
            "DualCLIPLoader",
            "Load CLIP",
            clip_name1=CLIP_L,
            clip_name2=T5XXL,
            type="flux",
        ),
        "6": node("VAELoader", "Load VAE", vae_name=FLUX_VAE),
        "7": node("CLIPTextEncode", "Prompt", clip=["5", 0], text=prompt),
        "8": node("FluxGuidance", "FluxGuidance", conditioning=["7", 0], guidance=30.0),
        "9": node("ConditioningZeroOut", "Negative", conditioning=["7", 0]),
        "10": node(
            "InpaintModelConditioning",
            "InpaintModelConditioning",
            positive=["8", 0],
            negative=["9", 0],
            vae=["6", 0],
            pixels=["3", 0],
            mask=["2", 1],
            noise_mask=True,
        ),
        "11": node("DifferentialDiffusion", "Differential Diffusion", model=["4", 0]),
        "12": node(
            "KSampler",
            "KSampler",
            model=["11", 0],
            positive=["10", 0],
            negative=["10", 1],
            latent_image=["10", 2],
            seed=0,
            steps=24,
            cfg=1.0,
            sampler_name="euler",
            scheduler="normal",
            denoise=1.0,
        ),
        "13": node("VAEDecode", "VAE Decode", samples=["12", 0], vae=["6", 0]),
        "14": node("GetImageSize", "Padded size", image=["2", 0]),
        "15": node(
            "ImageScale",
            "Back to the padded size",
            image=["13", 0],
            upscale_method="lanczos",
            width=["14", 0],
            height=["14", 1],
            crop="disabled",
        ),
        "16": node(
            "ImageCompositeMasked",
            "Keep the original",
            destination=["2", 0],
            source=["15", 0],
            x=0,
            y=0,
            resize_source=False,
            mask=["2", 1],
        ),
        "17": node(
            "SaveImage", "Save Image", images=["16", 0], filename_prefix=SAVE_PREFIX
        ),
    }


def edit_family(graph: dict) -> str | None:
    """:data:`QWEN_IMAGE_21` or :data:`KLEIN` for a graph of that family, else ``None``.

    Read off node classes and the text encoder's type, never the latent's:
    MOSS-Image samples on FLUX.2's empty latent too. Any FLUX.2 graph counts
    as Klein: the copy runs Klein 9B whatever FLUX.2 model the source loads.
    """
    nodes = [node for node in graph.values() if isinstance(node, dict)]
    classes = {str(node.get("class_type", "")) for node in nodes}
    if classes & {"TextEncodeQwenImage21", "QwenImage21Cache"}:
        return QWEN_IMAGE_21
    if any(
        "CLIPLoader" in str(node.get("class_type", ""))
        and (node.get("inputs") or {}).get("type") == "flux2"
        for node in nodes
    ):
        return KLEIN
    return None


@lru_cache(maxsize=None)
def _family_loaders(family: str) -> dict[str, dict]:
    """The model loader nodes of *family*'s built-in Inpaint workflow, by class."""
    path = os.path.join(_BUILT_IN_DIR, _FAMILY_SOURCE[family])
    with open(path, encoding="utf-8") as handle:
        graph = json.load(handle)
    return {
        node["class_type"]: node["inputs"]
        for node in graph.values()
        if isinstance(node, dict) and node.get("class_type") in _LOADER_CLASSES
    }


def edit_outpaint_graph(family: str, prompt: str = EDIT_PROMPT) -> dict:
    """*family*'s edit-model outpaint graph, with every side 0 and *prompt*.

    The probes cropped and stitched with Inpaint Crop & Stitch; this keeps to
    core nodes. The crop spanned the whole canvas there (every side was
    padded), and with one side padded it would have cut the original out of
    the reference, so the whole canvas is sampled and the decode is pasted
    back through the pad's feathered mask as in :func:`fill_outpaint_graph`.
    """
    graph: dict[str, dict] = {}

    def add(class_type: str, title: str, **inputs) -> str:
        node_id = str(len(graph) + 1)
        graph[node_id] = {
            "class_type": class_type,
            "inputs": inputs,
            "_meta": {"title": title},
        }
        return node_id

    loaders = _family_loaders(family)
    load = add("LoadImage", "Load Image", image="example.png")
    pad = add(
        PAD_CLASS,
        "Pad for outpainting",
        image=[load, 0],
        left=0,
        top=0,
        right=0,
        bottom=0,
        feathering=PAD_FEATHER,
    )
    # The pad is grey and its mask feathers into the original; the new area
    # alone (mask 1) is painted white.
    new_area = add("ThresholdMask", "New area", mask=[pad, 1], value=0.99)
    white = add("MaskToImage", "White", mask=[new_area, 0])
    filled = add(
        "ImageCompositeMasked",
        "White pad",
        destination=[pad, 0],
        source=[white, 0],
        x=0,
        y=0,
        resize_source=False,
        mask=[new_area, 0],
    )
    canvas = add(
        "ImageScaleToTotalPixels",
        "Canvas to the model's size",
        image=[filled, 0],
        upscale_method="lanczos",
        megapixels=FILL_MEGAPIXELS,
        # Qwen Image 2.1 rounds its reference to 32; FLUX.2 needs 16.
        resolution_steps=32,
    )
    unet = add("UNETLoader", "Load model", **loaders["UNETLoader"])
    clip = add("PixlStashCLIPLoader", "Load CLIP", **loaders["PixlStashCLIPLoader"])
    vae = add("PixlStashVAELoader", "Load VAE", **loaders["PixlStashVAELoader"])
    if family == QWEN_IMAGE_21:
        model = add(
            "QwenImage21Cache",
            "Qwen Image 2.1 Cache",
            model=[unet, 0],
            device="auto",
            dtype="default",
        )
        encode = add(
            "TextEncodeQwenImage21",
            "Prompt",
            clip=[clip, 0],
            prompt=prompt,
            negative_prompt="",
            vae=[vae, 0],
            # 0: the reference at its own size, so its latent is the canvas's.
            resolution=0,
            **{"images.image_1": [canvas, 0]},
        )
        sampled = add(
            "KSampler",
            "KSampler",
            model=[model, 0],
            positive=[encode, 0],
            negative=[encode, 1],
            latent_image=[encode, 2],
            seed=0,
            steps=25,
            cfg=1.0,
            sampler_name="euler",
            scheduler="simple",
            denoise=1.0,
        )
        decoded = add("VAEDecode", "VAE Decode", samples=[sampled, 0], vae=[vae, 0])
        # Its VAE decodes RGBA; the paste-back needs RGB.
        decoded = add("SplitImageWithAlpha", "Drop alpha", image=[decoded, 0])
    else:
        text = add("CLIPTextEncode", "Prompt", clip=[clip, 0], text=prompt)
        latent = add("VAEEncode", "Encode canvas", pixels=[canvas, 0], vae=[vae, 0])
        positive = add(
            "ReferenceLatent",
            "ReferenceLatent",
            conditioning=[text, 0],
            latent=[latent, 0],
        )
        negative = add("ConditioningZeroOut", "Negative", conditioning=[text, 0])
        guider = add(
            "CFGGuider",
            "CFGGuider",
            model=[unet, 0],
            positive=[positive, 0],
            negative=[negative, 0],
            cfg=1.0,
        )
        sampler = add("KSamplerSelect", "KSamplerSelect", sampler_name="euler")
        size = add("GetImageSize", "Canvas size", image=[canvas, 0])
        sigmas = add(
            "Flux2Scheduler",
            "Flux2Scheduler",
            steps=4,
            width=[size, 0],
            height=[size, 1],
        )
        noise = add("RandomNoise", "RandomNoise", noise_seed=0)
        # The canvas's own latent at full denoise: no noise mask, which left a
        # rectangle and a tone step at the seam in four steps.
        sampled = add(
            "SamplerCustomAdvanced",
            "SamplerCustomAdvanced",
            noise=[noise, 0],
            guider=[guider, 0],
            sampler=[sampler, 0],
            sigmas=[sigmas, 0],
            latent_image=[latent, 0],
        )
        decoded = add("VAEDecode", "VAE Decode", samples=[sampled, 0], vae=[vae, 0])
    padded = add("GetImageSize", "Padded size", image=[pad, 0])
    back = add(
        "ImageScale",
        "Back to the padded size",
        image=[decoded, 0],
        upscale_method="lanczos",
        width=[padded, 0],
        height=[padded, 1],
        crop="disabled",
    )
    # The edit redraws the whole canvas; the original's pixels go back on top.
    kept = add(
        "ImageCompositeMasked",
        "Keep the original",
        destination=[pad, 0],
        source=[back, 0],
        x=0,
        y=0,
        resize_source=False,
        mask=[pad, 1],
    )
    add("SaveImage", "Save Image", images=[kept, 0], filename_prefix=SAVE_PREFIX)
    return graph


def _positive_prompt(graph: dict, encoders: tuple[str, ...]) -> str:
    """The text of the one positive prompt, or ``""`` when it is not a literal."""
    for node_id in encoders:
        inputs = (graph.get(node_id) or {}).get("inputs") or {}
        text = inputs.get(prompt_field(inputs) or "")
        if isinstance(text, str):
            return text
    logger.info(
        "Outpaint conversion: the source's positive prompt (%s) is not one "
        "literal text, so the copy starts with an empty prompt.",
        ", ".join(encoders) or "none found",
    )
    return ""
