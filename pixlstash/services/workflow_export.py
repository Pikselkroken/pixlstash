"""What a workflow file may still say once it leaves this machine (v1.12 B8).

**Export workflow** hands somebody else a ComfyUI file. The graph behind it was
not authored for that: the run path resolves a card's source from the linked
file, from a kept picture's embedded metadata or from a stored instance
document (``services/workflow_run_service.py``), and the last two are *real
runs* - they carry the prompt that was typed, the seed that was rolled, the
character LoRA that was loaded and the name of the picture that went in.

So the file that leaves is the graph minus everything that is about a run
rather than about the workflow (implementation plan §5.7):

* every widget that carries prose, blanked;
* seeds, nulled;
* every LoRA slot the owner has **not** marked *structural*, emptied - a
  lightning or turbo LoRA is part of the workflow, a character LoRA is the
  recipe's business - by filename **and by digest**;
* ``_meta`` titles, stripped, because a person names nodes after what they are
  making;
* picture loader filenames, blanked;
* output paths, reset - ``filename_prefix`` is where a file lands on the
  owner's disk, and a person names that folder after what is in it;
* any model name this machine cannot vouch for
  (:func:`pixlstash.hub.workflows.unvouched_model_values`), and the *folder* of
  the ones it can, since the check only ever looked at the last component;
* ``checkpoint_id``, which is a row id in this machine's own database.

Two rules decide most of it, and **both are imported rather than restated**.
Whether a widget carries prose is
:func:`pixlstash.services.workflow_hash.carries_prose`, the same question the
reducer asks to keep prose out of a stored document; whether a value is a link
rather than a value is :func:`pixlstash.services.workflow_hash.is_link`. A
second spelling of either is a second rule that can drift, and drift here is a
prompt in somebody else's hands.

**Prose is found by widget name across the whole graph, never by asking which
node the sampler reads.** ``detect_workflow_io`` answers "where would a run
write its prompt", which is a different question with the opposite failure
direction: it reports *nothing* for a graph whose two samplers read different
prompts (an ordinary hires-fix layout), and it classifies
``CLIPTextEncodeSDXL`` as a prompt node whose widgets - ``text_g``, ``text_l``
- no run-time binding names. Either would have exported the prompt in full, and
the second would have done it while reporting ``prompts`` in ``removed``.

**A graph that will not reduce is refused rather than exported.** Nothing in
the scrub needs the reduction any more, so this is a belt rather than the
trousers - but a graph PixlStash cannot read is one it can promise nothing
about, and the export is a promise.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Iterable

from pixlstash.pixl_logging import get_logger
from pixlstash.services.comfyui_recipe_service import (
    LORA_DIGEST_FIELD_RE,
    LORA_FILENAME_FIELD_RE,
)
from pixlstash.services.workflow_hash import (
    IMAGE_EXTENSIONS,
    MAX_FILENAME_LENGTH,
    MODEL_EXTENSIONS,
    OUTPUT_PATH_RE,
    OUTPUT_PREFIX_FIELD,
    SECRET_FIELD_RE,
    SEED_FIELD_RE,
    SHELF_ID_FIELD,
    carries_prose,
    is_link,
    normalized_filename,
    structural_document,
)
from pixlstash.services.workflow_io import is_picture_loader, picture_fields
from pixlstash.services.workflow_run_service import FORGOTTEN_MODEL
from pixlstash.utils.comfyui_utilities import PRIMITIVE_STRING_CLASSES

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

# How much of a name the owner typed a download or a copy may carry, in BYTES.
# 255 is the component limit on every filesystem PixlStash runs on, and the
# rest is room for the longest suffix `store_workflow_copy` appends,
# `` (copy) (2).json``.
_MAX_STEM_BYTES = 200

# What a node of :data:`PRIMITIVE_STRING_CLASSES` calls the string it hands on.
# `carries_prose` cannot be widened to these: it is the reducer's rule too, and
# a `value` widget feeding a LoadImage its filename is a topology asset there —
# calling it prose would re-key every workflow built that way. So the export
# adds them, and only the export.
_PRIMITIVE_STRING_WIDGETS = frozenset({"value", "string"})

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
MODEL_FOLDERS = "the folders your models are filed in"
CREDENTIALS = "values in fields named like a key or a password"


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
            independently. Every other LoRA slot is emptied — **marked or
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
        WorkflowGraphError: The graph will not reduce, so PixlStash cannot read
            it well enough to promise anything about what leaves in it.
    """
    exported = deepcopy(graph)
    # The refusal, and nothing else: `structural_document` raises on a graph
    # PixlStash cannot read, which is the whole reason to reduce here. It used
    # to be `detect_workflow_io`, a second full reduction whose only other
    # product was a log line about an ambiguity the scrub does not consult.
    structural_document(exported)
    keep_lora = {
        (str(node_id), str(widget)) for node_id, widget in structural_lora_slots
    }
    removed: set[str] = set()

    for node_id, node in exported.items():
        if not isinstance(node, dict):
            continue
        meta = node.pop("_meta", None)
        if isinstance(meta, dict) and str(meta.get("title") or "") not in (
            "",
            str(node.get("class_type") or ""),
        ):
            # Reported only when the title is not ComfyUI's own default, which
            # is the node's class name. `removed` is the only thing the dialog
            # can render, and a category present on almost every export is
            # noise in the one list the owner actually reads.
            removed.add(NODE_TITLES)
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        class_type = str(node.get("class_type") or "")
        pictures = picture_fields(class_type) if is_picture_loader(class_type) else ()
        raw_string = class_type in PRIMITIVE_STRING_CLASSES
        for widget in list(inputs):
            name = str(widget)
            # A LoRA slot is one BY BOTH SPELLINGS: the ComfyUI-PixlStash
            # loaders name their adapter by digest, and `workflow_identity`
            # already records that missing the numbered digest form is how a
            # character LoRA slips past a slot rule (#1416).
            is_lora = bool(
                LORA_FILENAME_FIELD_RE.match(name) or LORA_DIGEST_FIELD_RE.match(name)
            )
            inputs[widget] = _scrub(
                name,
                inputs[widget],
                removed,
                is_picture=name in pictures,
                is_lora_slot=is_lora and (str(node_id), name) not in keep_lora,
                primitive_string=raw_string and name in _PRIMITIVE_STRING_WIDGETS,
                unvouched=unvouched,
            )

    logger.info(
        "A %d-node workflow was scrubbed for export; what came out: %s.",
        len(exported),
        ", ".join(sorted(removed)) or "nothing to take out",
    )
    return exported, sorted(removed)


def _names_a_file(value: str) -> bool:
    """True when this string is shaped like a model or picture filename.

    The line between "a name ComfyUI will look up" and "something a person
    typed". Everything past it in :func:`_scrub` is judged as prose, which is
    only safe because this returns first for every value the shelf check has
    already had its say about.
    """
    lowered = normalized_filename(value)
    return lowered.endswith(MODEL_EXTENSIONS) or lowered.endswith(IMAGE_EXTENSIONS)


def _is_prose(value: str, *, primitive_string: bool) -> bool:
    """True when a leftover string is something a person wrote.

    Three rules, none of them invented here:

    * the node is one of :data:`PRIMITIVE_STRING_CLASSES` handing on its raw
      string, which is where a prompt lives when it is wired into an encoder
      rather than typed into one;
    * the reducer's own backstop — a newline, or longer than
      :data:`MAX_FILENAME_LENGTH` — which it applies for exactly this reason,
      that such a value cannot be a filename;
    * whitespace. A combo token ComfyUI would offer (``dpmpp_2m``, ``normal``,
      ``nearest-exact``, ``randomize``) has none; a sentence does. Filenames do
      contain spaces — 5,066 real ones in this library — which is why
      :func:`_names_a_file` gets to answer first.

    The residual is a single word, on an unknown node, in a widget no rule
    names. It is the narrowest shape left, and widening past it would start
    blanking the enum tokens that make an exported file loadable.
    """
    if primitive_string:
        return True
    if "\n" in value or len(value.encode("utf-8", "ignore")) > MAX_FILENAME_LENGTH:
        return True
    return any(character.isspace() for character in value)


def _scrub(
    name: str,
    value: Any,
    removed: set[str],
    *,
    is_picture: bool,
    is_lora_slot: bool,
    primitive_string: bool,
    unvouched: Callable[[str, str], bool],
) -> Any:
    """One widget's value, scrubbed, recording what it cost in *removed*.

    Recursive, because a widget's value is not always a scalar. A link is a
    two-element list and is left alone, but a custom node can hold a list of
    prompts or a dict of assets, and ``workflow_hash``'s own nested-asset walk
    exists because that population is real. **A dict recurses on its KEY and a
    list on its parent's name**, which is what that walk does: a list is
    positional and inherits the widget's meaning, while a dict key is a name
    and may mean something else entirely.
    """
    if is_link(value):
        # A wired input carries no value of its own; overwriting one would drop
        # the link and leave a graph that no longer runs anywhere.
        return value

    def recurse(inner_name: str, item: Any) -> Any:
        return _scrub(
            inner_name,
            item,
            removed,
            is_picture=is_picture,
            is_lora_slot=is_lora_slot,
            primitive_string=primitive_string,
            unvouched=unvouched,
        )

    if isinstance(value, dict):
        return {key: recurse(str(key), item) for key, item in value.items()}
    if isinstance(value, list):
        return [recurse(name, item) for item in value]

    # Seeds are judged on the widget's NAME alone, the way the reducer judges
    # them: a seed stored as a string is still the seed that made this picture,
    # and two rules for "this is a seed" is one rule too many.
    if SEED_FIELD_RE.search(name):
        if isinstance(value, bool) or value is None or value == NULLED_SEED:
            return value
        removed.add(SEEDS)
        return NULLED_SEED
    if not isinstance(value, str) or not value:
        return value
    # First, and before anything that could decide to keep it. The reducer
    # drops these outright from a stored document because it "is kept forever
    # and shared"; an exported file is handed to somebody on purpose, and the
    # two tiers this route resolves from most often — a stored file and a
    # picture's embedded metadata — are raw ComfyUI output the reducer never
    # touched.
    if SECRET_FIELD_RE.search(name):
        removed.add(CREDENTIALS)
        return BLANK
    if carries_prose(name):
        removed.add(PROMPTS)
        return BLANK
    if is_picture:
        removed.add(PICTURE_NAMES)
        return BLANK
    if name == OUTPUT_PREFIX_FIELD or OUTPUT_PATH_RE.match(name):
        # "portraits/<a person's name>" is a perfectly ordinary prefix, and it
        # says where on the owner's disk the run landed. The reducer calls this
        # class volatile for the same reason.
        if value == DEFAULT_OUTPUT_PREFIX:
            return value
        removed.add(OUTPUT_PATHS)
        return DEFAULT_OUTPUT_PREFIX
    if name == SHELF_ID_FIELD:
        # A row id in this machine's own database. It names nothing anywhere
        # else, and it makes the file unloadable for whoever opens it.
        removed.add(MODEL_NAMES)
        return BLANK
    # The shelf check runs BEFORE the LoRA-slot rule so the reported category
    # is the true one: a forgotten model name sitting in a `lora_name` widget
    # is a forgotten model name, and calling it "a LoRA that is part of the
    # look" tells the owner the opposite of what happened in the very case the
    # feature leads with. Both blank it either way.
    if value == FORGOTTEN_MODEL or unvouched(name, value):
        # The sentinel included: a source resolved from a stored instance whose
        # asset row is gone carries it in place of the name, and it names
        # nothing, but a file that says a model went here and will not say
        # which is worse than an empty widget.
        removed.add(MODEL_NAMES)
        return BLANK
    if is_lora_slot:
        removed.add(LORA_SLOTS)
        return BLANK
    if _names_a_file(value):
        # Vouched for, so the name travels — but as the basename it was JUDGED
        # by. ComfyUI files models in subfolders and a person names a folder
        # after what is in it, so "characters/<a person>/base.safetensors"
        # would otherwise go out whole on the strength of a check that only
        # ever read the last component. The recipient's ComfyUI has its own
        # layout regardless. Split rather than normalized: the latter also
        # lowercases, and a filesystem may care.
        basename = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if basename != value:
            removed.add(MODEL_FOLDERS)
            return basename
        return value
    if _is_prose(value, primitive_string=primitive_string):
        removed.add(PROMPTS)
        return BLANK
    return value


def download_stem(name: str | None) -> str:
    """One file-name stem built from the owner's own text, without the suffix.

    The client writes this file, so the sanitising has to hold on ITS platform
    rather than on the server's: ``os.path.basename`` on Linux leaves a Windows
    separator alone, which is the whole traversal it would be reached for. Both
    separators go, so do the relative-path components and the control
    characters a shell or a file dialog would act on, and what is left is
    bounded — a name the owner typed has no length limit and a file name does.
    **Bounded in BYTES**, because that is the unit a filesystem counts in: 100
    CJK characters are 300 of them, past ext4's 255-byte component limit, and
    `store_workflow_copy` would then fail with an ``OSError`` the duplicate
    route can only answer 500 to — for that card, permanently.

    Here rather than in either route because both name a download this
    way, and a second spelling is a second rule to get wrong.
    """
    cleaned = "".join(
        " " if character < " " else character for character in (name or "")
    )
    cleaned = cleaned.replace("/", " ").replace("\\", " ").strip(" .")
    return cleaned.encode("utf-8")[:_MAX_STEM_BYTES].decode("utf-8", "ignore").strip()


def download_name(name: str | None) -> str:
    """:func:`download_stem` with the ``.json`` an export is saved under."""
    return f"{download_stem(name) or 'recipe'}.json"
