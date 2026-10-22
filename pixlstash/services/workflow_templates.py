"""ComfyUI workflow templates against the shelf: the New workflow set picker.

The list is ComfyUI's own templates, built in CI into a committed file
(``scripts/build_template_suggestions.py``, #1999) and never fetched at
runtime. Each template names the files its loaders load. This module answers,
per request, which of them are on the shelf, so the picker can lead with the
templates whose files have all arrived (#2001).

**Matching is by filename**, the template carries no hashes: a file is on the
shelf when a model row with a ``present`` copy answers to it by its stored
filename or that copy's own basename, case folded. A different quant of the same file counts too (``fp8`` where the
template lists ``bf16``), and the row says so.

**One row per head.** The head is the file a set is built around, the first
``checkpoints``, ``diffusion_models`` or ``unet`` file. Templates that share a head (text
to image, edit, an int8 cut) are variants of one row, so one download is offered
once. A template with no head is a row of its own.

**Covered** is the merge offer's rule (``_lacks``): a set holding every on-shelf
file of the template, a namesake counting as the file. A set made from the
template covers it whatever it holds since.

**Nothing is stored for the offer.** The only state is ``model.arrived_at``,
written by the Downloads intake, and ``model_workflow_set.template``. A template
is *new* while its head arrived in the last :data:`NEW_WINDOW`; that only drives
the New tile's line, and a complete template stays offered however old it is.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_workflow_sets import SLOT_CHECKPOINT, _lacks
from pixlstash.services.workflow_hash import normalized_filename
from pixlstash.utils.adapter_header import FILE_CHECKPOINT, FILE_UNKNOWN
from pixlstash.utils.model_utils import quantless_name as _quantless_name

logger = get_logger(__name__)

# The same few thousand shelf and template names on every read; the parse is
# most of what reading the shelf costs. Bounded, since a shelf only grows.
quantless_name = lru_cache(maxsize=16384)(_quantless_name)

NEW_WINDOW = timedelta(hours=24)

TEMPLATES_PATH = (
    Path(__file__).resolve().parent.parent / "data" / ("workflow_templates.json")
)

# A template file's ComfyUI folder, as the slot it fills in a set.
_SLOT_BY_DIRECTORY = {
    "checkpoints": SLOT_CHECKPOINT,
    "diffusion_models": SLOT_CHECKPOINT,
    "unet": SLOT_CHECKPOINT,
    "text_encoders": "text_encoder",
    "clip": "text_encoder",
    "vae": "vae",
    "loras": "lora",
}


@lru_cache(maxsize=1)
def load_templates() -> dict:
    """The committed list: ``{"source", "version", "templates": [...]}``."""
    try:
        return json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.error(
            "The workflow template list %s could not be read (%s); the New "
            "workflow set picker offers no templates.",
            TEMPLATES_PATH,
            exc,
        )
        return {"source": None, "version": None, "templates": []}


def _shelf(hub) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """The models with a present copy, by basename and by quantless name."""
    by_id: dict[int, dict] = {}
    by_name: dict[str, list[dict]] = {}
    for row in hub.fetchall(
        "SELECT m.id, m.filename, m.display_name, m.file_kind, m.file_size, "
        "m.arrived_at, f.relpath FROM model m "
        "JOIN model_file f ON f.model_id = m.id AND f.state = 'present'"
    ):
        model = by_id.setdefault(
            row["id"],
            {
                "id": row["id"],
                "filename": row["filename"] or normalized_filename(row["relpath"]),
                "kind": row["file_kind"],
                "file_size": row["file_size"],
                "arrived_at": row["arrived_at"],
            },
        )
        for name in {row["filename"], row["relpath"]} - {None}:
            entries = by_name.setdefault(normalized_filename(name), [])
            if model not in entries:
                entries.append(model)
    by_quantless: dict[str, list[dict]] = {}
    for name, models in by_name.items():
        entries = by_quantless.setdefault(quantless_name(name), [])
        entries.extend(m for m in models if m not in entries)
    return by_name, by_quantless


def _slot(directory: str, model: Optional[dict]) -> str:
    slot = _SLOT_BY_DIRECTORY.get(directory, "other")
    # The create route refuses a checkpoint slot for a file the shelf knows
    # is something else; such a file still joins, as "other".
    if (
        slot == SLOT_CHECKPOINT
        and model
        and model["kind"]
        not in (
            FILE_CHECKPOINT,
            FILE_UNKNOWN,
        )
    ):
        return "other"
    return slot


def _file(entry: dict, by_name, by_quantless) -> dict:
    name = normalized_filename(entry["name"])
    models = by_name.get(name) or []
    other_quant = None
    if not models:
        models = by_quantless.get(quantless_name(name)) or []
        if models:
            other_quant = models[0]["filename"]
    model = models[0] if models else None
    return {
        "name": entry["name"],
        "url": entry["url"],
        "directory": entry["directory"],
        "slot": _slot(entry["directory"], model),
        "on_shelf": model is not None,
        "model_id": model["id"] if model else None,
        "shelf_filename": model["filename"] if model else None,
        "other_quant": other_quant,
        "file_size": model["file_size"] if model else None,
        "_ids": [m["id"] for m in models],
        "_arrived_at": model["arrived_at"] if model else None,
    }


def _head(files: list[dict]) -> Optional[dict]:
    return next(
        (f for f in files if _SLOT_BY_DIRECTORY.get(f["directory"]) == SLOT_CHECKPOINT),
        None,
    )


def _covered_by(template: dict, files: list[dict], sets: list[dict]):
    """The first set covering *template*: ``{"id", "name"}`` or None."""
    shelf_files = [f for f in files if f["on_shelf"]]
    head = _head(files)
    combination = {
        "models": [{"id": f["_ids"][0]} for f in shelf_files],
        "namesakes": [f["_ids"] for f in shelf_files if len(f["_ids"]) > 1],
    }
    for entry, _held in sets:
        if entry.get("template") == template["name"]:
            return {"id": entry["id"], "name": entry["name"]}
    if not shelf_files or not head or not head["on_shelf"]:
        return None
    for entry, held in sets:
        # A set without the head (or a namesake of it) lacks it: the same
        # answer `_lacks` gives, without asking it for every set.
        if held.isdisjoint(head["_ids"]):
            continue
        if not _lacks(combination, held):
            return {"id": entry["id"], "name": entry["name"]}
    return None


def _is_new(arrived_at: Optional[str], now: datetime) -> bool:
    if not arrived_at:
        return False
    try:
        when = datetime.fromisoformat(arrived_at)
    except ValueError:
        logger.warning("model.arrived_at %r is not an ISO time; not new.", arrived_at)
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return now - when <= NEW_WINDOW


def _variant(template: dict, by_name, by_quantless, sets, now) -> dict:
    files = [_file(entry, by_name, by_quantless) for entry in template["files"]]
    head = _head(files)
    on_shelf = sum(f["on_shelf"] for f in files)
    covered = _covered_by(template, files, sets)
    complete = on_shelf == len(files)
    arrived_at = head["_arrived_at"] if head else None
    for f in files:
        del f["_ids"], f["_arrived_at"]
    return {
        "name": template["name"],
        "title": template["title"],
        "kind": template["kind"],
        "tags": template["tags"],
        "models": template["models"],
        "min_comfyui_version": template.get("min_comfyui_version"),
        "size": template.get("size") or 0,
        "usage": template.get("usage") or 0,
        "files": files,
        "on_shelf": on_shelf,
        "complete": complete,
        "covered_by": covered,
        "head_arrived_at": arrived_at,
        "new": complete and covered is None and _is_new(arrived_at, now),
    }


def _rank(variant: dict) -> tuple:
    """The variant a row shows first: covered, then complete, then closest.

    Closest is the most files on the shelf, then the fewest of them matched only
    as another quant: a shelf holding the bf16 file leads with the bf16
    template, not the more used int8 one it also completes.
    """
    return (
        variant["covered_by"] is not None,
        variant["complete"],
        variant["on_shelf"],
        -sum(bool(f["other_quant"]) for f in variant["files"]),
        variant["usage"],
    )


def template_rows(hub, sets: list[dict], now: Optional[datetime] = None) -> dict:
    """Every template against the shelf, one row per head file.

    Args:
        hub: the hub holding the shelf.
        sets: the hand-made sets as ``attach_hand_made`` serves them (``id``,
            ``name``, ``template``, ``members`` with ``id`` and ``on_shelf``).
        now: the clock, for tests.

    Returns:
        ``{"version", "rows", "new_ready"}``. Each row is ``{"key", "variants"}``
        with the default variant first.
    """
    now = now or datetime.now(timezone.utc)
    listing = load_templates()
    by_name, by_quantless = _shelf(hub)
    # Each set's on-shelf ids, once, rather than per template.
    sets = [
        (entry, {m["id"] for m in entry["members"] if m["on_shelf"]}) for entry in sets
    ]
    rows: dict[str, list[dict]] = {}
    for template in listing["templates"]:
        variant = _variant(template, by_name, by_quantless, sets, now)
        head = _head(variant["files"])
        key = (
            f"head:{quantless_name(normalized_filename(head['name']))}"
            if head
            else f"template:{template['name']}"
        )
        rows.setdefault(key, []).append(variant)
    out = [
        {"key": key, "variants": sorted(variants, key=_rank, reverse=True)}
        for key, variants in rows.items()
    ]
    return {
        "version": listing.get("version"),
        "rows": out,
        "new_ready": sum(row["variants"][0]["new"] for row in out),
    }
