"""Where a run puts the picture and the caption, without rewriting the workflow.

A workflow is stored exactly as it was imported. What a run fills comes from one
of two places:

* **Bindings**, a ``pixlstash_bindings`` list in the file. Only a workflow
  stored before import kept files as-is has them: the migration below records
  the spot each ``{{image_path}}`` / ``{{caption}}`` token sat on and puts a
  neutral value back, and gives a file with no token an empty list, so every
  such workflow runs exactly as it did. The old dialog let a workflow opt out of
  an input, and detection must not opt it back in.
* **Detection** (:mod:`pixlstash.services.workflow_io`) for everything else: the
  one picture input and the positive prompt the graph itself names.

A binding is a JSON path into the document, so it works for any layout the old
import dialog wrote tokens into. ``_submit_comfyui_prompt`` strips every
``pixlstash_`` key before a graph reaches ComfyUI.
"""

from __future__ import annotations

import json
import os
import tempfile
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

# Taken in the user folder before the start-up migration runs, so a file
# imported as-is afterwards is never mistaken for one the old dialog wrote. It
# holds the names of any file the pass has to try again.
MIGRATION_MARKER = ".placeholder-bindings-migrated"
# The file as it was before the migration rewrote it. Not ``.json``, so the
# workflow list never shows it.
BACKUP_SUFFIX = ".pre-bindings"

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
    value. A token inside a longer string (``"photo of {{caption}}, sharp"``)
    is bound with that string as its ``template``, so a run still fills the
    token inside it; the spot keeps the text with the token cut out, and the
    binding is marked ``recovered: false`` because what the string said before
    it became a template is gone. Only graph values are read: a token in a
    ``_meta`` title is not an input.

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
                if key == "_meta":
                    continue
                value[key] = walk(child, path + [key])
            return value
        if isinstance(value, list):
            for index, child in enumerate(value):
                value[index] = walk(child, path + [index])
            return value
        if not isinstance(value, str):
            return value
        whole = value.strip()
        roles = [role for token, role in _TOKENS.items() if token in value]
        for role in roles:
            binding = {"role": role, "node": _node_id(document, path), "path": path}
            if whole in _TOKENS:
                binding["recovered"] = True
            else:
                binding["template"] = value
                binding["recovered"] = False
            bindings.append(binding)
        if not roles:
            return value
        if whole in _TOKENS:
            return _NEUTRAL[_TOKENS[whole]]
        for token in _TOKENS:
            value = value.replace(token, "")
        return value

    walk(migrated, [])
    if not bindings:
        return document, False
    migrated[BINDINGS_KEY] = bindings
    return migrated, True


def migrate_workflow_folder(folder: str) -> int:
    """Give every workflow the old import dialog stored its bindings, once.

    A file with tokens gets them as bindings; a file without gets an empty
    list, because the dialog let a workflow take no picture or no caption and
    detection would otherwise start filling those. Each rewritten file keeps
    its original beside it as ``<name>.json.pre-bindings``.

    **The marker is taken before the pass, not after it.** It is created
    exclusively, and the folder with it when a fresh install has none yet, so
    the pass runs once per folder however start-up goes: a workflow imported
    as-is afterwards is never given empty bindings, and a second process
    starting at the same moment finds the marker and leaves the files alone. A
    file that could not be read or written for a reason that may pass (locked,
    disk full) is named in the marker, and only those are tried again at the
    next start. A file that is not valid JSON is logged and left: it cannot run
    either way.

    Returns:
        How many files were rewritten.
    """
    marker = os.path.join(folder, MIGRATION_MARKER)
    try:
        os.makedirs(folder, exist_ok=True)
        handle = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        entries = _pending_retries(marker)
    except OSError as exc:
        logger.error(
            "Could not take the placeholder migration marker in %s; it will be "
            "tried again next start: %s",
            folder,
            exc,
        )
        return 0
    else:
        os.close(handle)
        try:
            entries = sorted(os.listdir(folder))
        except OSError as exc:
            logger.error(
                "Could not list the workflow folder %s for the placeholder "
                "migration; it will be tried again next start: %s",
                folder,
                exc,
            )
            _release_marker(marker)
            return 0
    if not entries:
        return 0

    rewritten, retry = 0, []
    for entry in entries:
        if not entry.lower().endswith(".json"):
            continue
        outcome = _migrate_file(os.path.join(folder, entry))
        if outcome == _REWRITTEN:
            rewritten += 1
        elif outcome == _RETRY:
            retry.append(entry)
    try:
        with open(marker, "w", encoding="utf-8") as out:
            json.dump({"retry": retry}, out)
    except OSError as exc:
        logger.error(
            "Could not record the placeholder migration's retries in %s; these "
            "workflows keep their tokens until they are imported again: %s (%s)",
            marker,
            ", ".join(retry) or "none",
            exc,
        )
    return rewritten


def _pending_retries(marker: str) -> list[str]:
    """The files an earlier pass could not finish, from its marker."""
    try:
        with open(marker, "r", encoding="utf-8") as handle:
            recorded = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning(
            "Could not read the placeholder migration marker %s; treating the "
            "migration as done: %s",
            marker,
            exc,
        )
        return []
    retry = recorded.get("retry") if isinstance(recorded, dict) else None
    return [name for name in retry or () if isinstance(name, str)]


def _release_marker(marker: str) -> None:
    try:
        os.remove(marker)
    except OSError as exc:
        logger.error(
            "Could not remove the placeholder migration marker %s after the "
            "folder could not be listed; the migration will not run until it "
            "is removed: %s",
            marker,
            exc,
        )


_REWRITTEN, _SKIPPED, _RETRY = "rewritten", "skipped", "retry"


def _migrate_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return _SKIPPED
    except OSError as exc:
        logger.warning(
            "Could not read workflow %s for the placeholder migration; it is "
            "tried again next start: %s",
            path,
            exc,
        )
        return _RETRY
    try:
        document = json.loads(raw)
        if not isinstance(document, dict) or BINDINGS_KEY in document:
            return _SKIPPED
        migrated, changed = migrate_placeholders(document)
    except (ValueError, RecursionError) as exc:
        logger.warning(
            "Skipping placeholder migration of workflow %s, it is not a readable "
            "workflow: %s",
            path,
            exc,
        )
        return _SKIPPED
    if not changed:
        migrated = {**document, BINDINGS_KEY: []}
    backup_path = f"{path}{BACKUP_SUFFIX}"
    folder = os.path.dirname(path)
    temp_path = None
    try:
        if not os.path.exists(backup_path):
            with open(backup_path, "w", encoding="utf-8") as handle:
                handle.write(raw)
        # A temp name of its own, never ``.json``: two processes must not share
        # one, and the workflow list must never show it.
        descriptor, temp_path = tempfile.mkstemp(
            dir=folder, prefix=".migrating-", suffix=".tmp"
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(migrated, handle, indent=2, ensure_ascii=True)
        os.replace(temp_path, path)
    except OSError as exc:
        logger.error(
            "Could not write the migrated workflow %s; it keeps its placeholder "
            "tokens for now and is tried again next start: %s",
            path,
            exc,
        )
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError as cleanup_exc:
                logger.warning(
                    "Could not remove the migration temp file %s: %s",
                    temp_path,
                    cleanup_exc,
                )
        return _RETRY
    roles = ", ".join(b["role"] for b in migrated[BINDINGS_KEY]) or "no inputs"
    if is_flagged(migrated):
        logger.warning(
            "Migrated workflow %s to bindings (%s). A token sat inside a longer "
            "value, which is kept as a template; the value before it is gone.",
            path,
            roles,
        )
    else:
        logger.info("Migrated workflow %s to bindings (%s).", path, roles)
    return _REWRITTEN


def is_flagged(document: dict) -> bool:
    """Whether a migration could not put back what a token replaced."""
    bindings = document.get(BINDINGS_KEY) if isinstance(document, dict) else None
    return any(
        isinstance(b, dict) and not b.get("recovered", True) for b in bindings or ()
    )


def run_targets(document: dict, detected=None) -> dict[str, list[dict]]:
    """What a run fills, by role: ``{"path": [...], "template": str | None}``.

    Bindings win when the file has them, even an empty list. Otherwise the
    detected inputs of an API-format graph: exactly one picture input, and
    exactly one positive prompt's text, followed one link upstream when a
    primitive node feeds it. Two of either is ambiguous and fills nothing, so a
    fixed style prompt beside a subject prompt is never overwritten. A UI-format
    file fills nothing, because the run routes can only submit API format
    (#1307).

    Args:
        document: The stored workflow.
        detected: ``detect_workflow_io(document)`` when the caller already has
            it, so the graph is not reduced twice.
    """
    targets: dict[str, list[dict]] = {IMAGE: [], CAPTION: []}
    if not isinstance(document, dict):
        return targets
    bindings = document.get(BINDINGS_KEY)
    if isinstance(bindings, list):
        for binding in bindings:
            if isinstance(binding, dict) and binding.get("role") in targets:
                targets[binding["role"]].append(
                    {"path": binding.get("path"), "template": binding.get("template")}
                )
        return targets
    if isinstance(document.get("nodes"), list):
        return targets

    prefix, graph = [], document
    if isinstance(document.get("prompt"), dict):
        prefix, graph = ["prompt"], document["prompt"]
    try:
        found = detected or detect_workflow_io(document)
    except WorkflowGraphError as exc:
        logger.info("No run inputs detected, the graph cannot be read: %s", exc)
        return targets

    if len(found.picture_inputs) == 1:
        node_id = found.picture_inputs[0]
        node = graph.get(node_id) or {}
        inputs = node.get("inputs") or {}
        for field in INPUT_IMAGE_FIELDS.get(node.get("class_type"), ("image",)):
            if isinstance(inputs.get(field), str):
                targets[IMAGE].append(
                    {"path": prefix + [node_id, "inputs", field], "template": None}
                )
                break
    if len(found.positive_prompts) == 1:
        path = _text_path(graph, found.positive_prompts[0])
        if path:
            targets[CAPTION].append({"path": prefix + path, "template": None})
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


def fill(document: dict, targets: dict[str, list[dict]], values: dict) -> None:
    """Put each role's value at its targets, in place.

    *values* maps a role to its value, or to ``None`` to leave that role's
    targets as the workflow has them. A templated target is written once from
    every role it holds, so a string carrying both tokens gets both values.

    Raises:
        BindingError: A path does not resolve, so the run would silently ignore
            what it was given.
    """
    by_path: dict[str, tuple[list, str | None, dict]] = {}
    for role, found in targets.items():
        for target in found:
            path = target.get("path")
            if not isinstance(path, list) or not path:
                raise BindingError(f"binding path {path!r} is not a JSON path")
            key = json.dumps(path)
            _, template, roles = by_path.setdefault(
                key, (path, target.get("template"), {})
            )
            roles[role] = values.get(role)
    for path, template, roles in by_path.values():
        if all(value is None for value in roles.values()):
            continue
        if isinstance(template, str):
            filled = template
            for token, role in _TOKENS.items():
                filled = filled.replace(token, roles.get(role) or "")
        else:
            filled = next(value for value in roles.values() if value is not None)
        parent = document
        try:
            for step in path[:-1]:
                parent = parent[step]
            if isinstance(parent, list) and not 0 <= path[-1] < len(parent):
                raise IndexError(path[-1])
            if isinstance(parent, dict) and path[-1] not in parent:
                raise KeyError(path[-1])
            parent[path[-1]] = filled
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
