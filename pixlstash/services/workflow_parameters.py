"""A workflow file's settings, read as the controls of a form (#1306).

Every widget value in the graph is a parameter, except:

- a **connected** input, whose value comes from another node (a width wired
  from a primitive is set on the primitive, which is a parameter of its own);
- a **bound** input, which a run fills: the picture inputs and the prompt that
  :func:`pixlstash.services.workflow_bindings.run_targets` names;
- a credential-named field, which the workflow library never keeps either;
- a value that is not a number, a string or a boolean (a list or an object),
  which no form control edits.

**Types come from ComfyUI's ``object_info``**, so a number has the node's real
``min`` / ``max`` / ``step``, a combo has the options that ComfyUI actually
has installed, and a seed is the input ComfyUI itself re-rolls. Without
``object_info`` (ComfyUI unreachable) the recorded values are still described,
typed from the JSON value alone and with no ranges or options: a form can show
what the file holds, but not what else ComfyUI would accept.

Only API-format graphs are read. A UI-format file stores its widget values as an
unnamed list whose layout depends on the node version, and the run routes can
only submit API format anyway.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Optional

from pixlstash.services import workflow_bindings
from pixlstash.services.comfyui_recipe_service import (
    INPUT_IMAGE_FIELDS,
    MODEL_FILENAME_FIELDS,
    SEED_PASSTHROUGH_CLASSES,
    combo_options,
    find_input_spec,
)
from pixlstash.services.workflow_hash import (
    MODEL_EXTENSIONS,
    SECRET_FIELD_RE,
    SEED_FIELD_RE,
)
from pixlstash.services.workflow_inputs import node_title
from pixlstash.services.workflow_io import detect_workflow_io

INT = "int"
FLOAT = "float"
SEED = "seed"
BOOLEAN = "boolean"
STRING = "string"
CHOICE = "choice"
MODEL = "model"

# The settings shown before "All N parameters", besides every model and seed.
# ponytail: a name list; per-class rules if custom packs name these differently.
_FEATURED_NAMES = frozenset(
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

    ``minimum``, ``maximum``, ``step`` and ``options`` are ``None`` when
    ComfyUI did not describe them, which is every one of them offline.
    """

    node_id: str
    node_title: str
    class_type: str
    name: str
    kind: str
    value: Any
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    options: Optional[tuple[str, ...]] = None
    multiline: bool = False

    @property
    def key(self) -> tuple[str, str]:
        """``(node_id, name)``, which names a parameter in pins and values."""
        return self.node_id, self.name


def api_graph(document: dict) -> Optional[dict]:
    """The API-format graph in *document*, or ``None`` for a UI-format file."""
    if not isinstance(document, dict) or isinstance(document.get("nodes"), list):
        return None
    if isinstance(document.get("prompt"), dict):
        return document["prompt"]
    return document


def describe_parameters(
    document: dict, object_info: Optional[dict] = None
) -> list[Parameter]:
    """Every parameter of *document*, in node-id then input order.

    Args:
        document: A stored workflow file. A UI-format one has no parameters
            here; check :func:`api_graph` to tell that apart from a graph with
            nothing to set.
        object_info: ComfyUI's ``/object_info`` map, or ``None`` when ComfyUI
            could not be reached.

    Raises:
        WorkflowGraphError: The graph cannot be read.
    """
    graph = api_graph(document)
    if graph is None:
        return []
    bound = _bound_inputs(document)
    seed_feeders = {
        str(value[0])
        for node in graph.values()
        if isinstance(node, dict) and isinstance(node.get("inputs"), dict)
        for name, value in node["inputs"].items()
        if _is_link(value) and SEED_FIELD_RE.search(name)
    }
    parameters = []
    for node_id in sorted(graph, key=_node_order):
        node = graph[node_id]
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        class_type = str(node.get("class_type") or "")
        spec = (object_info or {}).get(class_type)
        title = node_title(document, str(node_id), class_type)
        for name, value in node["inputs"].items():
            if _is_link(value) or (str(node_id), name) in bound:
                continue
            if SECRET_FIELD_RE.search(name):
                continue
            if not isinstance(value, (bool, int, float, str)):
                continue
            is_seed = (
                str(node_id) in seed_feeders
                if class_type in SEED_PASSTHROUGH_CLASSES
                else None
            )
            parameters.append(
                _typed(str(node_id), title, class_type, name, value, spec, is_seed)
            )
    return parameters


def default_pins(parameters: list[Parameter]) -> list[tuple[str, str]]:
    """The few parameters that matter: models, seeds and the sampler settings."""
    return [
        p.key
        for p in parameters
        if p.kind in (MODEL, SEED) or p.name in _FEATURED_NAMES
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
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in str(node_id).split(":")
    )


def _is_link(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
    )


def _bound_inputs(document: dict) -> set[tuple[str, str]]:
    """``(node_id, field)`` of every input a run fills."""
    bound = set()
    for targets in workflow_bindings.run_targets(document).values():
        for target in targets:
            path = target.get("path")
            if isinstance(path, list) and len(path) >= 3 and path[-2] == "inputs":
                bound.add((str(path[-3]), str(path[-1])))
    # Every picture input, not only the one a run fills today: #1305 gives each
    # one a mode, and none of them is a setting.
    graph = api_graph(document) or {}
    for node_id in detect_workflow_io(document).picture_inputs:
        class_type = (graph.get(node_id) or {}).get("class_type")
        for field in INPUT_IMAGE_FIELDS.get(class_type, ("image",)):
            bound.add((node_id, field))
    return bound


def _typed(
    node_id: str,
    title: str,
    class_type: str,
    name: str,
    value: Any,
    spec: Any,
    is_seed: Optional[bool],
) -> Parameter:
    """Type one value, from *spec* when ComfyUI described it.

    *is_seed* decides for a passthrough primitive (``PrimitiveInt``), which
    ComfyUI flags as re-rollable even when it drives a width: it is a seed only
    when it feeds a seed input. ``None`` leaves the decision to the spec.
    """
    base = {
        "node_id": node_id,
        "node_title": title,
        "class_type": class_type,
        "name": name,
        "value": value,
    }
    is_model_field = name in MODEL_FILENAME_FIELDS.get(class_type, ()) or (
        isinstance(value, str) and value.lower().endswith(MODEL_EXTENSIONS)
    )
    found = find_input_spec(spec, name) if spec else None
    if found is None:
        return Parameter(
            kind=_kind_of_value(name, value, is_model_field, is_seed), **base
        )

    type_field, opts = found
    options = combo_options(spec, name)
    if isinstance(type_field, (list, tuple)) or type_field == "COMBO":
        return Parameter(
            kind=MODEL if is_model_field else CHOICE,
            options=tuple(options) if options else None,
            **base,
        )
    if type_field in ("INT", "FLOAT"):
        rerolled = bool(opts.get("control_after_generate"))
        if type_field == "INT" and (rerolled if is_seed is None else is_seed):
            kind = SEED
        else:
            kind = INT if type_field == "INT" else FLOAT
        return Parameter(
            kind=kind,
            minimum=_number(opts.get("min")),
            maximum=_number(opts.get("max")),
            step=_number(opts.get("step")),
            **base,
        )
    if type_field == "BOOLEAN":
        return Parameter(kind=BOOLEAN, **base)
    if type_field == "STRING":
        return Parameter(kind=STRING, multiline=bool(opts.get("multiline")), **base)
    # A custom widget type ComfyUI describes but this form has no control for.
    return Parameter(kind=_kind_of_value(name, value, is_model_field, is_seed), **base)


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


def _number(value: Any) -> Optional[float]:
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


def _check_value(parameter: Parameter, value: Any) -> None:
    label = f"{parameter.node_id}.{parameter.name}"
    kind = parameter.kind
    if kind in (INT, SEED):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{label} must be a whole number")
    elif kind == FLOAT:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{label} must be a number")
    elif kind == BOOLEAN:
        if not isinstance(value, bool):
            raise ValueError(f"{label} must be true or false")
    elif not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    if parameter.minimum is not None and value < parameter.minimum:
        raise ValueError(f"{label} must be at least {parameter.minimum}")
    if parameter.maximum is not None and value > parameter.maximum:
        raise ValueError(f"{label} must be at most {parameter.maximum}")
    if parameter.options is not None and value not in parameter.options:
        raise ValueError(f"{label} is not one of the options ComfyUI offers")
