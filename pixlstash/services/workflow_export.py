"""What a workflow file may still say once it leaves this machine (v1.12 B8).

**Export workflow** hands somebody else a ComfyUI file. The graph behind it was
not authored for that: the run path resolves a card's source from the linked
file, from a kept picture's embedded metadata or from a stored instance
document (``services/workflow_run_service.py``), and the last two are *real
runs* - they carry the prompt that was typed, the seed that was rolled, the
character LoRA that was loaded and the name of the picture that went in.

So the file that leaves is the graph minus everything that is about a run
rather than about the workflow (implementation plan §5.7):

* the prompt and caption targets, blank;
* seeds, nulled;
* every LoRA slot the owner has **not** marked *structural*, emptied - a
  lightning or turbo LoRA is part of the workflow, a character LoRA is the
  recipe's business;
* ``_meta`` titles, stripped, because a person names nodes after what they are
  making;
* picture loader filenames, blanked;
* output paths, reset - ``filename_prefix`` is where a file lands on the
  owner's disk, and a person names that folder after what is in it;
* and any model name this machine cannot vouch for
  (:func:`pixlstash.hub.workflows.unvouched_model_values`).

That last one is the one with teeth. Forgetting a model's name deletes its
``workflow_recipe_asset`` rows and rewrites no graph, so a picture's embedded
metadata still names the forgotten LoRA in full - and resolving a source from
that picture would carry the name straight back out in the exported file. The
shelf is what the export is checked against, so a name nobody on this machine
holds is never published.

**A graph that will not reduce is refused rather than exported.** The prompt
pass needs the reduction to know which nodes carry prose; exporting without it
would publish the prompt.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Callable, Iterable

from pixlstash.pixl_logging import get_logger
from pixlstash.services.comfyui_recipe_service import LORA_FILENAME_FIELD_RE
from pixlstash.services.workflow_hash import (
    OUTPUT_PATH_RE,
    OUTPUT_PREFIX_FIELD,
    SEED_FIELD_RE,
)
from pixlstash.services.workflow_io import (
    detect_workflow_io,
    is_picture_loader,
    picture_fields,
)
from pixlstash.services.workflow_run_service import FORGOTTEN_MODEL

logger = get_logger(__name__)

# What a blanked widget holds. The empty string rather than ``null``: ComfyUI
# types its widgets, and a null in a required STRING or combo widget is a load
# error in the editor, while an empty one is simply unset.
BLANK = ""

# What a nulled seed holds, for the same reason: a seed widget is an INT.
NULLED_SEED = 0

# What an output path is reset to. Not blank: ComfyUI writes to its output root
# with no prefix at all, which is a file nobody can find rather than a refusal,
# and the reducer already treats this widget as volatile
# (:data:`~pixlstash.services.workflow_hash.OUTPUT_PREFIX_FIELD`).
DEFAULT_OUTPUT_PREFIX = "PixlStash"

# The widget names a text encoder can carry its prose in, as
# ``workflow_bindings`` already reads them.
_PROMPT_FIELDS = ("text", "prompt", "value")

# The categories :func:`scrub_for_export` reports. Named rather than spelled at
# each site so the route, the tests and the UI copy agree on the vocabulary -
# and so nothing here is ever a VALUE. What was taken out of a file is the
# answer; what it said is exactly what must not be repeated.
PROMPTS = "prompts"
SEEDS = "seeds"
LORA_SLOTS = "LoRA slots that are part of the look"
NODE_TITLES = "node titles"
PICTURE_NAMES = "picture file names"
OUTPUT_PATHS = "where the pictures were saved"
MODEL_NAMES = "model names this machine does not hold"


def scrub_for_export(
    graph: dict,
    *,
    structural_lora_slots: Iterable[tuple[str, str]] = (),
    unvouched: Callable[[str, str], bool] = lambda _widget, _value: False,
) -> tuple[dict, list[str]]:
    """A copy of *graph* safe to give away, and what was taken out of it.

    Args:
        graph: An API-format graph, already sanitised of non-node entries.
        structural_lora_slots: ``(node id, widget name)`` of every LoRA slot
            the owner marked *structural*. A **slot**, not a node: a stacker
            carries three of them on one node and they are marked
            independently. Every other LoRA filename is emptied — **marked or
            not**, so a topology whose marks have not been frozen yet, and a
            loader the label map did not reach, both fall on the safe side.
        unvouched: ``(widget_name, value) -> bool``, true when the value names
            a model this machine cannot vouch for. The default vouches for
            nothing being wrong, which is only right for a caller that has
            already decided the graph carries no model names.

    Returns:
        ``(document, removed)`` — the scrubbed copy, and the sorted categories
        that were changed. ``removed`` holds categories and never values.

    Raises:
        WorkflowGraphError: The graph will not reduce, so which nodes carry the
            prompt is unknown and the export is refused rather than guessed.
    """
    exported = deepcopy(graph)
    detected = detect_workflow_io(exported)
    prompt_nodes = {*detected.positive_prompts, *detected.negative_prompts}
    keep_lora = {
        (str(node_id), str(widget)) for node_id, widget in structural_lora_slots
    }
    removed: set[str] = set()

    for node_id, node in exported.items():
        if not isinstance(node, dict):
            continue
        if node.pop("_meta", None) is not None:
            removed.add(NODE_TITLES)
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        class_type = str(node.get("class_type") or "")
        pictures = picture_fields(class_type) if is_picture_loader(class_type) else ()
        for widget, value in inputs.items():
            # A wired input carries no value of its own; overwriting one would
            # drop the link and leave a graph that no longer runs anywhere.
            if isinstance(value, list):
                continue
            name = str(widget)
            if SEED_FIELD_RE.search(name) and isinstance(value, (int, float)):
                if not isinstance(value, bool) and value != NULLED_SEED:
                    inputs[widget] = NULLED_SEED
                    removed.add(SEEDS)
                continue
            if not isinstance(value, str) or not value:
                continue
            if str(node_id) in prompt_nodes and name in _PROMPT_FIELDS:
                inputs[widget] = BLANK
                removed.add(PROMPTS)
            elif name in pictures:
                inputs[widget] = BLANK
                removed.add(PICTURE_NAMES)
            elif name == OUTPUT_PREFIX_FIELD or OUTPUT_PATH_RE.match(name):
                # "portraits/<a person's name>" is a perfectly ordinary prefix,
                # and it says where on the owner's disk the run landed. The
                # reducer calls this class volatile for the same reason.
                if value != DEFAULT_OUTPUT_PREFIX:
                    inputs[widget] = DEFAULT_OUTPUT_PREFIX
                    removed.add(OUTPUT_PATHS)
            elif (
                LORA_FILENAME_FIELD_RE.match(name)
                and (str(node_id), name) not in keep_lora
            ):
                inputs[widget] = BLANK
                removed.add(LORA_SLOTS)
            elif value == FORGOTTEN_MODEL or unvouched(name, value):
                # The sentinel included: a source resolved from a stored
                # instance whose asset row is gone carries it in place of the
                # name, and it names nothing, but a file that says a model went
                # here and will not say which is worse than an empty widget.
                inputs[widget] = BLANK
                removed.add(MODEL_NAMES)

    logger.info(
        "A %d-node workflow was scrubbed for export; what came out: %s.",
        len(exported),
        ", ".join(sorted(removed)) or "nothing to take out",
    )
    return exported, sorted(removed)
