"""Which of a workflow's settings are featured, and which is a picture batch.

What survives of the #1306 parameter form: the featured setting names a
workflow card lists (``workflow_card_service``) and the rule for the
``batch_size`` a run pins to 1 (``workflow_run_service``).
"""

from __future__ import annotations

import math
from typing import Any, Optional

from pixlstash.services.workflow_hash import is_link

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


# ComfyUI's own `ResolutionSelector` table (`comfy_extras/nodes_resolution.py`):
# what each `aspect_ratio` choice multiplies out to.
_ASPECT_RATIOS = {
    "1:1 (Square)": (1, 1),
    "2:3 (Portrait Photo)": (2, 3),
    "3:2 (Photo)": (3, 2),
    "3:4 (Portrait Standard)": (3, 4),
    "4:3 (Standard)": (4, 3),
    "9:16 (Portrait Widescreen)": (9, 16),
    "16:9 (Widescreen)": (16, 9),
    "21:9 (Ultrawide)": (21, 9),
}
SIZE_NAMES = ("width", "height")


def is_latent_size(class_type: Any, name: str) -> bool:
    """Whether *name* on *class_type* is the size of the picture a run makes.

    The empty latent's ``width`` / ``height``: the one place a size can be set
    whatever drives it, so a run that wants another size writes it there.
    """
    # ponytail: a name rule, like `is_picture_batch` offline; object_info's
    # LATENT output if a pack names its empty latent otherwise.
    # Efficiency loaders name theirs `empty_latent_width`, so are not matched.
    cls = str(class_type or "")
    return name in SIZE_NAMES and "Empty" in cls and "Latent" in cls


def linked_size(graph: dict, link: Any) -> Optional[int]:
    """The number a wired ``width`` / ``height`` carries, when it can be read.

    A ``ResolutionSelector`` is worked out the way ComfyUI works it out (its
    output 0 is the width, 1 the height), and a ``PrimitiveInt`` is its
    ``value``. ``None`` for anything else, or a selector whose own inputs are
    wired: then there is no number to offer.
    """
    if not is_link(link):
        return None
    node = graph.get(str(link[0]))
    inputs = node.get("inputs") if isinstance(node, dict) else None
    if not isinstance(inputs, dict):
        return None
    if node.get("class_type") == "ResolutionSelector":
        aspect = inputs.get("aspect_ratio")
        ratio = _ASPECT_RATIOS.get(aspect) if isinstance(aspect, str) else None
        megapixels = inputs.get("megapixels")
        multiple = inputs.get("multiple", 8)
        if (
            ratio is None
            or link[1] not in (0, 1)
            or not isinstance(megapixels, (int, float))
            or not isinstance(multiple, int)
            or isinstance(multiple, bool)
            or multiple <= 0
        ):
            return None
        scale = math.sqrt(megapixels * 1024 * 1024 / (ratio[0] * ratio[1]))
        # Python's round, as ComfyUI's is: halves go to even there too.
        return round(ratio[link[1]] * scale / multiple) * multiple
    if node.get("class_type") != "PrimitiveInt" or link[1] != 0:
        return None
    value = inputs.get("value")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def set_latent_size(graph: dict, node: dict, name: str, value: Any) -> None:
    """Write a run's *value* into a latent size input, cutting a wire if needed.

    A wired size whose source already says *value* is left wired, so a run
    that did not change the size sends the graph as authored. Otherwise the
    number replaces the link in every input *of the same name* reading that
    source output, so the latent and anything else sized from the same
    selector stay in step, while a primitive that also feeds a ``height`` or
    a seed keeps driving it.
    """
    current = node["inputs"][name]
    if not is_link(current):
        node["inputs"][name] = value
        return
    if linked_size(graph, current) == value:
        return
    source = list(current)
    for other in graph.values():
        inputs = other.get("inputs") if isinstance(other, dict) else None
        if not isinstance(inputs, dict):
            continue
        wired = inputs.get(name)
        if is_link(wired) and list(wired) == source:
            inputs[name] = value
