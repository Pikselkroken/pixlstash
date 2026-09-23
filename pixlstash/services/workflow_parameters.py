"""A workflow file's settings, read as the controls of a form (#1306).

Every widget value in the graph is a parameter, except:

- a **connected** input, whose value comes from another node (a width wired
  from a primitive is set on the primitive, which is a parameter of its own);
- a **bound** input, which a run fills: every detected picture input's picture
  field, and the prompt that
  :func:`pixlstash.services.workflow_bindings.run_targets` names;
- a credential-named field (``api_key``, ``secret_key``, ``hfToken``...);
- a value that is not a number, a string or a boolean (a list or an object),
  which no form control edits.

**Types come from ComfyUI's ``object_info``**, so a number has the node's real
``min`` / ``max`` / ``step``, a combo has the options that ComfyUI actually
has installed, and a seed is the input ComfyUI itself re-rolls. Without
``object_info`` (ComfyUI unreachable) the recorded values are still described,
with ``typed`` False, no ranges or options, and a kind guessed from the JSON
value. The guess is only a hint for display: a value for an untyped parameter is
checked by its broad type alone, so a ``cfg`` stored as ``1`` still takes
``1.5``.

Only API-format graphs are read. A UI-format file stores its widget values as an
unnamed list whose layout depends on the node version, and the run routes can
only submit API format anyway.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Optional

from pixlstash.services import workflow_bindings
from pixlstash.services.comfyui_recipe_service import (
    model_filename_fields,
    SEED_PASSTHROUGH_CLASSES,
    find_input_spec,
)
from pixlstash.services.workflow_hash import MODEL_EXTENSIONS, SEED_FIELD_RE, is_link
from pixlstash.services.workflow_inputs import node_title
from pixlstash.services.workflow_io import (
    WorkflowIO,
    api_graph,
    detect_workflow_io,
    picture_fields,
)

# Matched anywhere in the name, case-blind, so ``secret_key`` and ``hfToken``
# are caught. The exceptions are settings that only contain the word: a token
# count, a tokenizer, an author.
_CREDENTIAL_RE = re.compile(
    r"api_?key|access_?key|private_?key|secret|passw(or)?d|token|auth", re.I
)
_NOT_CREDENTIAL_RE = re.compile(
    r"(^|_)(max_|num_)?tokens($|_)|token_normalization|tokenizer|(^|_)author($|_)",
    re.I,
)

INT = "int"
FLOAT = "float"
SEED = "seed"
BOOLEAN = "boolean"
STRING = "string"
CHOICE = "choice"
MODEL = "model"

# The settings shown before "All N parameters", besides every model and seed. A
# primitive counts when it drives one of these (Flux2-Klein sets its size so).
# ponytail: a name list; per-class rules if custom packs name these differently.
FEATURED_NAMES = frozenset(
    {
        "steps",
        "cfg",
        "guidance",
        "sampler_name",
        "scheduler",
        "denoise",
        "width",
        "height",
    }
)


@dataclass(frozen=True)
class Parameter:
    """One settable widget value of a workflow.

    ``typed`` is True when ComfyUI described the input. ``minimum``,
    ``maximum``, ``step`` and ``options`` are ``None`` when it did not say.
    ``drives`` names the inputs a passthrough primitive's value is wired into.
    """

    node_id: str
    node_title: str
    class_type: str
    name: str
    kind: str
    value: Any
    typed: bool = False
    minimum: Optional[int | float] = None
    maximum: Optional[int | float] = None
    step: Optional[int | float] = None
    options: Optional[tuple[Any, ...]] = None
    multiline: bool = False
    drives: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        """``(node_id, name)``, which names a parameter in pins and values."""
        return self.node_id, self.name


def describe_parameters(
    document: dict,
    object_info: Optional[dict] = None,
    detected: Optional[WorkflowIO] = None,
) -> list[Parameter]:
    """Every parameter of *document*, in node-id then input order.

    Args:
        document: A stored workflow file. A UI-format one has no parameters
            here; check :func:`pixlstash.services.workflow_io.api_graph` to tell
            that apart from a graph with nothing to set.
        object_info: ComfyUI's ``/object_info`` map, or ``None`` when ComfyUI
            could not be reached.
        detected: ``detect_workflow_io(document)`` when the caller already has
            it, so the graph is not reduced again.

    Raises:
        WorkflowGraphError: The graph cannot be read.
    """
    graph = api_graph(document)
    if graph is None:
        return []
    detected = detected or detect_workflow_io(document)
    bound = _bound_inputs(document, graph, detected)
    feeds = _passthrough_feeds(graph)
    info = object_info or {}
    parameters = []
    for node_id in sorted(graph, key=_node_order):
        node = graph[node_id]
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        node_id = str(node_id)
        class_type = str(node.get("class_type") or "")
        title = node_title(document, node_id, class_type)
        consumers = feeds.get(node_id, ())
        is_seed = None
        if class_type in SEED_PASSTHROUGH_CLASSES:
            is_seed = any(_feeds_seed(info, *consumer) for consumer in consumers)
        for name, value in node["inputs"].items():
            if is_link(value) or (node_id, name) in bound:
                continue
            if _CREDENTIAL_RE.search(name) and not _NOT_CREDENTIAL_RE.search(name):
                continue
            if not isinstance(value, (bool, int, float, str)):
                continue
            parameters.append(
                _typed(
                    Parameter(
                        node_id=node_id,
                        node_title=title,
                        class_type=class_type,
                        name=name,
                        kind=STRING,
                        value=value,
                        drives=tuple(input_name for _, input_name in consumers),
                    ),
                    info.get(class_type),
                    is_seed,
                )
            )
    return parameters


def default_pins(parameters: list[Parameter]) -> list[tuple[str, str]]:
    """The few parameters that matter: models, seeds and the sampler settings."""
    return [
        p.key
        for p in parameters
        if p.kind in (MODEL, SEED)
        or p.name in FEATURED_NAMES
        or FEATURED_NAMES.intersection(p.drives)
    ]


def validate_pins(
    parameters: list[Parameter], requested: object
) -> list[tuple[str, str]]:
    """Check pins sent by a client, returning them as ``(node_id, name)``.

    Raises:
        ValueError: The pins are malformed, repeat one, or name something that
            is not a parameter of this workflow.
    """
    if not isinstance(requested, list):
        raise ValueError("pins must be a list")
    known = {p.key for p in parameters}
    pins = []
    for item in requested:
        key = _key_of(item)
        if key not in known:
            raise ValueError(f"{key[0]}.{key[1]} is not a parameter of this workflow")
        if key in pins:
            raise ValueError(f"{key[0]}.{key[1]} is pinned twice")
        pins.append(key)
    return pins


def apply_values(document: dict, parameters: list[Parameter], values: object) -> dict:
    """A copy of *document* with *values* written into its graph.

    Args:
        document: The stored workflow, API format.
        parameters: :func:`describe_parameters` of the same document; a value
            is checked against the type and range described there.
        values: ``[{"node_id", "name", "value"}]``.

    Raises:
        ValueError: A value names no parameter, or does not fit it.
    """
    if not isinstance(values, list):
        raise ValueError("values must be a list")
    by_key = {p.key: p for p in parameters}
    updated = deepcopy(document)
    graph = api_graph(updated)
    if graph is None:
        raise ValueError("only an API-format workflow takes parameter values")
    for item in values:
        key = _key_of(item)
        parameter = by_key.get(key)
        if parameter is None:
            raise ValueError(f"{key[0]}.{key[1]} is not a parameter of this workflow")
        value = item.get("value")
        _check_value(parameter, value)
        graph[key[0]]["inputs"][key[1]] = value
    return updated


def _node_order(node_id: str) -> tuple:
    """Numeric ids in number order, so node 10 does not sort before node 9."""
    return tuple(
        (0, int(part), "") if part.isdecimal() else (1, 0, part)
        for part in str(node_id).split(":")
    )


def _bound_inputs(
    document: dict, graph: dict, detected: WorkflowIO
) -> set[tuple[str, str]]:
    """``(node_id, field)`` of every input a run fills."""
    bound = set()
    for targets in workflow_bindings.run_targets(document, detected).values():
        for target in targets:
            path = target.get("path")
            if isinstance(path, list) and len(path) >= 3 and path[-2] == "inputs":
                bound.add((str(path[-3]), str(path[-1])))
    # Every picture input, not only the one a run fills today: #1305 gives each
    # one a mode, and none of them is a setting.
    for node_id, class_type in zip(
        detected.picture_inputs, detected.picture_input_classes
    ):
        for field in picture_fields(class_type):
            bound.add((node_id, field))
    return bound


def _passthrough_feeds(graph: dict) -> dict[str, list[tuple[str, str]]]:
    """``(consumer class, input name)`` each passthrough primitive is wired into."""
    feeds: dict[str, list[tuple[str, str]]] = {}
    for node in graph.values():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        for name, value in node["inputs"].items():
            if not is_link(value):
                continue
            source = graph.get(value[0])
            if isinstance(source, dict) and (
                source.get("class_type") in SEED_PASSTHROUGH_CLASSES
            ):
                feeds.setdefault(value[0], []).append(
                    (str(node.get("class_type") or ""), str(name))
                )
    return feeds


def _feeds_seed(object_info: dict, consumer_class: str, input_name: str) -> bool:
    """Whether the input a primitive drives is one a run re-rolls.

    Decided the way ``detect_seed_targets`` decides it: by the consumer's
    ``control_after_generate``, when ComfyUI described the consumer, and by the
    seed name rule otherwise.
    """
    found = find_input_spec(object_info.get(consumer_class), input_name)
    if found is not None:
        type_field, opts = found
        return type_field == "INT" and bool(opts.get("control_after_generate"))
    return bool(SEED_FIELD_RE.search(input_name))


def _typed(parameter: Parameter, spec: Any, is_seed: Optional[bool]) -> Parameter:
    """Type one value, from *spec* when ComfyUI described it.

    *is_seed* decides for a passthrough primitive (``PrimitiveInt``), which
    ComfyUI flags as re-rollable even when it drives a width: it is a seed only
    when it feeds a seed input. ``None`` leaves the decision to the spec.
    """
    name, value = parameter.name, parameter.value
    is_model_field = name in model_filename_fields(parameter.class_type) or (
        isinstance(value, str) and value.lower().endswith(MODEL_EXTENSIONS)
    )
    found = find_input_spec(spec, name) if spec else None
    if found is None:
        return _replace(
            parameter, kind=_kind_of_value(name, value, is_model_field, is_seed)
        )

    type_field, opts = found
    if isinstance(type_field, (list, tuple)) or type_field == "COMBO":
        raw = (
            type_field if isinstance(type_field, (list, tuple)) else opts.get("options")
        )
        # A remote combo's list is filled at run time, so what is here proves
        # nothing; an empty one is lazily filled too. Numbers are kept: a custom
        # node can offer [1, 2, 4, 8].
        options = None
        if isinstance(raw, (list, tuple)) and not opts.get("remote"):
            options = (
                tuple(
                    o
                    for o in raw
                    if isinstance(o, (str, int, float)) and not isinstance(o, bool)
                )
                or None
            )
        return _replace(
            parameter,
            typed=True,
            kind=MODEL if is_model_field else CHOICE,
            options=options,
        )
    if type_field in ("INT", "FLOAT"):
        rerolled = bool(opts.get("control_after_generate"))
        if type_field == "INT" and (rerolled if is_seed is None else is_seed):
            kind = SEED
        else:
            kind = INT if type_field == "INT" else FLOAT
        return _replace(
            parameter,
            typed=True,
            kind=kind,
            minimum=_number(opts.get("min")),
            maximum=_number(opts.get("max")),
            step=_number(opts.get("step")),
        )
    if type_field == "BOOLEAN":
        return _replace(parameter, typed=True, kind=BOOLEAN)
    if type_field == "STRING":
        return _replace(
            parameter, typed=True, kind=STRING, multiline=bool(opts.get("multiline"))
        )
    # A custom widget type ComfyUI describes but this form has no control for.
    return _replace(
        parameter, kind=_kind_of_value(name, value, is_model_field, is_seed)
    )


def _replace(parameter: Parameter, **changes) -> Parameter:
    return Parameter(**{**parameter.__dict__, **changes})


def _kind_of_value(
    name: str, value: Any, is_model_field: bool, is_seed: Optional[bool]
) -> str:
    if isinstance(value, bool):
        return BOOLEAN
    if isinstance(value, int):
        seed = SEED_FIELD_RE.search(name) if is_seed is None else is_seed
        return SEED if seed else INT
    if isinstance(value, float):
        return FLOAT
    if is_model_field:
        return MODEL
    return STRING


def _number(value: Any) -> Optional[int | float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _key_of(item: object) -> tuple[str, str]:
    if not isinstance(item, dict):
        raise ValueError("each entry must be an object with node_id and name")
    node_id, name = item.get("node_id"), item.get("name")
    if not isinstance(node_id, str) or not isinstance(name, str):
        raise ValueError("each entry needs a string node_id and name")
    return node_id, name


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _check_value(parameter: Parameter, value: Any) -> None:
    label = f"{parameter.node_id}.{parameter.name}"
    kind = parameter.kind
    if not parameter.typed:
        # The kind was guessed from the stored value, so only the broad type
        # is held to: a cfg stored as 1 takes 1.5, ComfyUI judges the rest.
        stored = parameter.value
        if isinstance(stored, bool):
            fits = isinstance(value, bool)
        elif isinstance(stored, (int, float)):
            fits = _is_number(value)
        else:
            fits = isinstance(value, str)
        if not fits:
            raise ValueError(
                f"{label} must be a {type(stored).__name__} like its value"
            )
        return
    if kind in (INT, SEED):
        if not _is_number(value) or not isinstance(value, int):
            raise ValueError(f"{label} must be a whole number")
    elif kind == FLOAT:
        if not _is_number(value):
            raise ValueError(f"{label} must be a finite number")
    elif kind == BOOLEAN:
        if not isinstance(value, bool):
            raise ValueError(f"{label} must be true or false")
    elif parameter.options is not None:
        if isinstance(value, bool) or value not in parameter.options:
            raise ValueError(f"{label} is not one of the options ComfyUI offers")
        return
    elif not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    if parameter.minimum is not None and value < parameter.minimum:
        raise ValueError(f"{label} must be at least {parameter.minimum}")
    if parameter.maximum is not None and value > parameter.maximum:
        raise ValueError(f"{label} must be at most {parameter.maximum}")
