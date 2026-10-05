"""Which of a workflow's settings are featured, and which is a picture batch.

What survives of the #1306 parameter form: the featured setting names a
workflow card lists (``workflow_card_service``) and the rule for the
``batch_size`` a run pins to 1 (``workflow_run_service``).
"""

from __future__ import annotations

from typing import Optional

# Re-exported: ``routes/comfyui.py`` reads it as ``workflow_parameters.api_graph``.
from pixlstash.services.workflow_io import api_graph as api_graph

# The featured settings a default recipe lists, besides every model and seed. A
# primitive counts when it drives one of these (Flux2-Klein sets its size so).
# ponytail: a name list; per-class rules if custom packs name these differently.
# The tuple is also the order a workflow's default recipe lists them in, so its
# defaults read the same way whichever of its nodes happens to hold the size.
FEATURED_ORDER = (
    "steps",
    "cfg",
    "guidance",
    "sampler_name",
    "scheduler",
    "denoise",
    "width",
    "height",
)
FEATURED_NAMES = frozenset(FEATURED_ORDER)

# Nodes whose ``batch_size`` is not how many pictures come out: a chunk size, or
# the number of views a multi-view model renders on purpose.
_NOT_PICTURE_BATCH_CLASSES = frozenset(
    {
        "RebatchLatents",
        "StableZero123_Conditioning_Batched",
        "StableZero123_BatchSchedule",
        "SV3D_BatchSchedule",
    }
)
# Loaders that carry their own empty latent (Efficiency Nodes), for the name
# rule; with ComfyUI's answer they are found by their LATENT output.
_LATENT_LOADER_CLASSES = frozenset({"Efficient Loader", "Eff. Loader SDXL"})


def is_picture_batch(
    class_type: str, name: str, object_info: Optional[dict] = None
) -> bool:
    """Whether *name* on *class_type* is how many pictures one submission makes.

    A run pins it to 1 (``workflow_run_service.pin_batch_size``), because the
    Run popup's count is the number of pictures.

    With ComfyUI's ``object_info`` it is any ``batch_size`` on a node that
    outputs a ``LATENT`` (every empty latent, core or pack, and the
    image-to-video latents). Without it, a name rule stands in.
    """
    if name != "batch_size" or class_type in _NOT_PICTURE_BATCH_CLASSES:
        return False
    spec = (object_info or {}).get(class_type)
    if isinstance(spec, dict) and isinstance(spec.get("output"), list):
        return "LATENT" in spec["output"]
    # ponytail: a name rule offline; object_info decides whenever ComfyUI answers.
    return (
        "Empty" in class_type and "Latent" in class_type
    ) or class_type in _LATENT_LOADER_CLASSES
