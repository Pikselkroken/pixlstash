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
is no fallback to another model: an edit model is what failed.
"""

from __future__ import annotations

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

    *graph* is not modified, and nothing else of it is kept: whatever model it
    runs, the copy runs the Fill model.

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
