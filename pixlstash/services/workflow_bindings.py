"""Where a run puts the picture and the caption, without rewriting the workflow.

A workflow is stored exactly as it was imported. What a run fills comes from one
of two places:

* **Bindings**, a ``pixlstash_bindings`` list in the file. Only a workflow that
  was stored with ``{{image_path}}`` / ``{{caption}}`` tokens has them: the
  migration below records the spot each token sat on and puts a neutral value
  back, so that workflow runs exactly as it did.
* **Detection** (:mod:`pixlstash.services.workflow_io`) for everything else: the
  one picture input and the positive prompt the graph itself names.

A binding is a JSON path into the document, so it works for any layout the old
import dialog wrote tokens into. ``_submit_comfyui_prompt`` strips every
``pixlstash_`` key before a graph reaches ComfyUI.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy

from pixlstash.pixl_logging import get_logger
from pixlstash.services.comfyui_recipe_service import INPUT_IMAGE_FIELDS
from pixlstash.services.workflow_hash import WorkflowGraphError
from pixlstash.services.workflow_io import detect_workflow_io

logger = get_logger(__name__)

BINDINGS_KEY = "pixlstash_bindings"
IMAGE = "image"
CAPTION = "caption"

_TOKENS = {"{{image_path}}": IMAGE, "{{caption}}": CAPTION}

# What a whole-value token is replaced with. ComfyUI ships `example.png` in its
# input folder as LoadImage's own default; an empty prompt is the neutral text.
_NEUTRAL = {IMAGE: "example.png", CAPTION: ""}

_PROMPT_FIELDS = ("text", "prompt", "value")


class BindingError(ValueError):
    """A binding names a spot the document no longer has."""


def _node_id(document: dict, path: list) -> str | None:
    """The graph node a JSON path sits inside, in the graph's own ids."""
    if path[:1] == ["prompt"] and len(path) > 1:
        return str(path[1])
    if path[:1] == ["nodes"] and len(path) > 1 and isinstance(path[1], int):
        node = document["nodes"][path[1]]
        return str(node.get("id")) if isinstance(node, dict) else None
    if path and isinstance(document.get(path[0]), dict):
        return str(path[0])
    return None


def migrate_placeholders(document: dict) -> tuple[dict, bool]:
    """Turn stored placeholder tokens into bindings.

    A token that was the whole value is bound and its spot gets a neutral
    value. A token inside a longer string is bound too, but the text around it
    stays with the token cut out and the binding is marked ``recovered: false``:
    a run replaces the whole value, so what the string meant before cannot be
    restored.

    Args:
        document: A stored workflow, any format.

    Returns:
        ``(migrated, changed)``. *migrated* is a new document; the input is not
        modified. *changed* is False when there was no token to migrate.
    """
    bindings: list[dict] = []
    migrated = deepcopy(document)

    def walk(value, path):
        if isinstance(value, dict):
            for key, child in value.items():
                if not path and str(key).startswith("pixlstash_"):
                    continue
                value[key] = walk(child, path + [key])
            return value
        if isinstance(value, list):
            for index, child in enumerate(value):
                value[index] = walk(child, path + [index])
            return value
        if not isinstance(value, str):
            return value
        for token, role in _TOKENS.items():
            if token not in value:
                continue
            whole = value.strip() == token
            value = _NEUTRAL[role] if whole else value.replace(token, "")
            bindings.append(
                {
                    "role": role,
                    "node": _node_id(document, path),
                    "path": path,
                    "recovered": whole,
                }
            )
        return value

    walk(migrated, [])
    if not bindings:
        return document, False
    migrated[BINDINGS_KEY] = bindings
    return migrated, True


def migrate_workflow_folder(folder: str) -> int:
    """Migrate every tokened workflow file in *folder*, in place.

    Idempotent: a migrated file carries no token, so a second pass finds
    nothing. Each file is replaced atomically, and one unreadable file is logged
    and skipped rather than stopping the rest.

    Returns:
        How many files were rewritten.
    """
    if not os.path.isdir(folder):
        return 0
    rewritten = 0
    for entry in sorted(os.listdir(folder)):
        if not entry.lower().endswith(".json"):
            continue
        path = os.path.join(folder, entry)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning(
                "Skipping placeholder migration of workflow %s, it cannot be read: %s",
                path,
                exc,
            )
            continue
        if not isinstance(document, dict):
            continue
        migrated, changed = migrate_placeholders(document)
        if not changed:
            continue
        temp_path = f"{path}.migrating"
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(migrated, handle, indent=2, ensure_ascii=True)
            os.replace(temp_path, path)
        except OSError as exc:
            logger.error(
                "Could not write the migrated workflow %s; it keeps its "
                "placeholder tokens and will not run until it is migrated: %s",
                path,
                exc,
            )
            continue
        rewritten += 1
        roles = ", ".join(b["role"] for b in migrated[BINDINGS_KEY])
        if is_flagged(migrated):
            logger.warning(
                "Migrated workflow %s to bindings (%s). A token sat inside a "
                "longer value, which cannot be restored.",
                path,
                roles,
            )
        else:
            logger.info("Migrated workflow %s to bindings (%s).", path, roles)
    return rewritten


def is_flagged(document: dict) -> bool:
    """Whether a migration could not put back what a token replaced."""
    bindings = document.get(BINDINGS_KEY) if isinstance(document, dict) else None
    return any(
        isinstance(b, dict) and not b.get("recovered", True) for b in bindings or ()
    )


def run_targets(document: dict) -> dict[str, list[list]]:
    """The JSON paths a run fills, by role.

    Bindings win when the file has them, even an empty list. Otherwise the
    detected inputs of an API-format graph: exactly one picture input (two is
    ambiguous and fills nothing), and the positive prompt's text, followed one
    link upstream when a primitive node feeds it. A UI-format file fills nothing,
    because the run routes can only submit API format (#1307).
    """
    targets: dict[str, list[list]] = {IMAGE: [], CAPTION: []}
    if not isinstance(document, dict):
        return targets
    bindings = document.get(BINDINGS_KEY)
    if isinstance(bindings, list):
        for binding in bindings:
            if isinstance(binding, dict) and binding.get("role") in targets:
                targets[binding["role"]].append(list(binding.get("path") or ()))
        return targets
    if isinstance(document.get("nodes"), list):
        return targets

    prefix, graph = [], document
    if isinstance(document.get("prompt"), dict):
        prefix, graph = ["prompt"], document["prompt"]
    try:
        found = detect_workflow_io(document)
    except WorkflowGraphError as exc:
        logger.info("No run inputs detected, the graph cannot be read: %s", exc)
        return targets

    if len(found.picture_inputs) == 1:
        node_id = found.picture_inputs[0]
        node = graph.get(node_id) or {}
        inputs = node.get("inputs") or {}
        for field in INPUT_IMAGE_FIELDS.get(node.get("class_type"), ("image",)):
            if isinstance(inputs.get(field), str):
                targets[IMAGE].append(prefix + [node_id, "inputs", field])
                break
    for node_id in found.positive_prompts:
        path = _text_path(graph, node_id)
        if path:
            targets[CAPTION].append(prefix + path)
    return targets


def _text_path(graph: dict, node_id: str, hops: int = 1) -> list | None:
    inputs = (graph.get(node_id) or {}).get("inputs") or {}
    for field in _PROMPT_FIELDS:
        value = inputs.get(field)
        if isinstance(value, str):
            return [node_id, "inputs", field]
        if isinstance(value, list) and len(value) == 2 and hops > 0:
            return _text_path(graph, str(value[0]), hops - 1)
    return None


def fill(document: dict, targets: list[list], value: str) -> None:
    """Set *value* at every path in *targets*, in place.

    Raises:
        BindingError: A path does not resolve, so the run would silently ignore
            what it was given.
    """
    for path in targets:
        if not path:
            raise BindingError("empty binding path")
        parent = document
        try:
            for step in path[:-1]:
                parent = parent[step]
            if isinstance(parent, list) and not 0 <= path[-1] < len(parent):
                raise IndexError(path[-1])
            if isinstance(parent, dict) and path[-1] not in parent:
                raise KeyError(path[-1])
            parent[path[-1]] = value
        except (KeyError, IndexError, TypeError) as exc:
            raise BindingError(
                f"binding {json.dumps(path)} does not resolve: {exc!r}"
            ) from exc


def canonical(document: dict) -> str:
    """The document as one string, ignoring PixlStash's own keys.

    Two files are copies of one workflow when these match, whatever their
    whitespace or key order.
    """
    body = {
        key: value
        for key, value in document.items()
        if not str(key).startswith("pixlstash_")
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"))
