"""Hand-made workflow sets (#1520): shelf models the owner says work together.

A set is a menu, not a recipe: fixed slots (one checkpoint at most, any number
of text encoders, VAEs, LoRAs and others), no order and no strengths. It is a
hub fact, like the shelf itself, so it is the same in every library.

**Members are sha256s, not model ids.** A file that leaves the shelf takes its
``model`` row with it, and the set keeps the member (drawn as not on the shelf)
so it reconnects when a file with the same bytes comes back. That is also why a
checkpoint still waiting for its hash cannot be added yet: it has no identity a
set could hold.

**Evidence is read, never written.** A recipe combination is *covered* by a set
when every model in it is an on-shelf member of the set; the set then carries
that combination's recipe and picture counts and its covers. The combinations
come from :func:`~pixlstash.services.model_shelf_service.fetch_workflow_sets`
unchanged, and :func:`attach_hand_made` layers the sets over them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_shelf_service import SET_COVER_DEPTH, known_base_model
from pixlstash.services.workflow_library_service import cover_order
from pixlstash.utils.adapter_header import (
    FILE_ADAPTER,
    FILE_CHECKPOINT,
    FILE_ENGINE,
    FILE_TEXT_ENCODER,
    FILE_UNKNOWN,
    FILE_VAE,
)

logger = get_logger(__name__)

SLOT_CHECKPOINT = "checkpoint"
# The member order a set is drawn in, and the whole slot vocabulary (the hub's
# CHECK on `model_workflow_set_member.slot` spells the same five).
SLOTS = (SLOT_CHECKPOINT, "text_encoder", "vae", "lora", "other")

MAX_SET_NAME_LENGTH = 200

# Where a member lands when the caller names no slot.
_DEFAULT_SLOT = {
    FILE_CHECKPOINT: SLOT_CHECKPOINT,
    FILE_TEXT_ENCODER: "text_encoder",
    FILE_VAE: "vae",
    FILE_ADAPTER: "lora",
}

# The kinds that may fill the checkpoint slot. `unknown` because a Flux or Wan
# diffusion file is often stored as one, and it is the model the set is about.
_CHECKPOINT_KINDS = (FILE_CHECKPOINT, FILE_UNKNOWN)


class WorkflowSetNotFoundError(LookupError):
    """A set or a model the request named does not exist. Mapped to 404."""


class WorkflowSetRefusedError(ValueError):
    """The request would break a set rule. Mapped to 409."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_name(name: Optional[str]) -> Optional[str]:
    """Trim a set name; blank means no name."""
    if name is None:
        return None
    return name.strip() or None


def _model_name(row) -> str:
    return row["display_name"] or row["filename"] or f"model {row['id']}"


def fetch_sets(hub) -> list[dict]:
    """Every hand-made set with its members, newest first. Two hub reads.

    Each member is ``{"sha256", "slot", "label", "model"}``, where ``model`` is
    the shelf row holding those bytes or ``None`` when no row does.
    """
    sets = [
        {**dict(row), "members": []}
        for row in hub.fetchall(
            "SELECT id, name, created_at, updated_at FROM model_workflow_set "
            "ORDER BY created_at DESC, id DESC"
        )
    ]
    by_id = {entry["id"]: entry for entry in sets}
    # `model.sha256` is UNIQUE, so the LEFT JOIN yields one row per member.
    for row in hub.fetchall(
        "SELECT m.set_id, m.sha256, m.slot, m.label, model.id, model.display_name, "
        "model.filename, model.file_kind, model.base_model, "
        "model.base_model_canonical, model.base_model_source, model.file_size "
        "FROM model_workflow_set_member AS m "
        "LEFT JOIN model ON model.sha256 = m.sha256"
    ):
        entry = by_id.get(row["set_id"])
        if entry is None:
            continue
        entry["members"].append(
            {
                "sha256": row["sha256"],
                "slot": row["slot"],
                "label": row["label"],
                "model": row if row["id"] is not None else None,
            }
        )
    return sets


def _member_out(member: dict) -> dict:
    row = member["model"]
    if row is None:
        return {
            "sha256": member["sha256"],
            "slot": member["slot"],
            "label": member["label"],
            "on_shelf": False,
            "id": None,
            "name": member["label"] or member["sha256"][:12],
            "filename": None,
            "kind": None,
            "base_model": None,
            "file_size": None,
        }
    return {
        "sha256": member["sha256"],
        "slot": member["slot"],
        "label": member["label"],
        "on_shelf": True,
        "id": int(row["id"]),
        "name": row["display_name"] or row["filename"] or member["label"] or "",
        "filename": row["filename"],
        "kind": row["file_kind"],
        "base_model": known_base_model(row),
        "file_size": row["file_size"],
    }


def attach_hand_made(hub, found: dict) -> dict:
    """Layer the hand-made sets over a :func:`fetch_workflow_sets` answer.

    Mutates *found* in place and returns it: every combination gains
    ``covered_by`` (the ids of the sets covering it; the combination itself is
    kept, since the client's *Works with* reads all of them), ``no_set`` loses
    the on-shelf members of any set, and ``hand_made`` is added, newest first.
    Each set's ``covers`` are cover candidates, like a combination's.
    """
    hand_made = []
    for entry in fetch_sets(hub):
        members = sorted(
            (_member_out(member) for member in entry["members"]),
            key=lambda m: (SLOTS.index(m["slot"]), m["name"].lower(), m["sha256"]),
        )
        checkpoint = next((m for m in members if m["slot"] == SLOT_CHECKPOINT), None)
        hand_made.append(
            {
                "id": entry["id"],
                "name": entry["name"],
                "created_at": entry["created_at"],
                "updated_at": entry["updated_at"],
                "incomplete": checkpoint is None,
                "checkpoint_id": checkpoint["id"] if checkpoint else None,
                "picture_count": 0,
                "recipes": 0,
                "covers": [],
                "members": members,
                "_on_shelf": {m["id"] for m in members if m["on_shelf"]},
            }
        )

    for combination in found["combinations"]:
        ids = {model["id"] for model in combination["models"]}
        combination["covered_by"] = []
        for entry in hand_made:
            # Every model of the combination on the set; the set may hold more.
            # `ids` is never empty, so an empty set covers nothing.
            if ids <= entry["_on_shelf"]:
                combination["covered_by"].append(entry["id"])
                entry["picture_count"] += combination["picture_count"]
                entry["recipes"] += combination["recipes"]
                entry["covers"].extend(combination["covers"])

    grouped: set[int] = set()
    for entry in hand_made:
        grouped |= entry.pop("_on_shelf")
        entry["covers"] = sorted(entry["covers"], key=cover_order, reverse=True)[
            :SET_COVER_DEPTH
        ]
    found["no_set"] = [
        model_id for model_id in found["no_set"] if model_id not in grouped
    ]
    found["hand_made"] = hand_made
    return found


def _require_set(conn, set_id: int) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM model_workflow_set WHERE id = ?", (set_id,)
        ).fetchone()
        is None
    ):
        raise WorkflowSetNotFoundError(f"No workflow set with id {set_id}.")


def _resolve(conn, member: dict) -> tuple[str, str, Optional[str]]:
    """One requested member as ``(sha256, slot, label)``, or a refusal.

    *member* is ``{"model_id", "slot"?}`` or ``{"sha256", "slot", "label"?}``;
    the second form is how an undo puts back a member no longer on the shelf.
    The kind rules apply whenever a shelf row holds the bytes.
    """
    if member.get("model_id") is not None:
        row = conn.execute(
            "SELECT id, sha256, file_kind, display_name, filename FROM model "
            "WHERE id = ?",
            (member["model_id"],),
        ).fetchone()
        if row is None:
            raise WorkflowSetNotFoundError(
                f"No model with id {member['model_id']} on the shelf."
            )
        label = _model_name(row)
    else:
        sha256 = member["sha256"].lower()
        row = conn.execute(
            "SELECT id, sha256, file_kind, display_name, filename FROM model "
            "WHERE sha256 = ?",
            (sha256,),
        ).fetchone()
        label = member.get("label")
        if row is None:
            return sha256, member["slot"], label

    name = _model_name(row)
    if row["file_kind"] == FILE_ENGINE:
        raise WorkflowSetRefusedError(f"{name} isn't a model file a workflow loads.")
    if row["sha256"] is None:
        raise WorkflowSetRefusedError(
            f"{name} is still being hashed. Add it once the shelf has read it."
        )
    slot = member.get("slot") or _DEFAULT_SLOT.get(row["file_kind"], "other")
    if slot == SLOT_CHECKPOINT and row["file_kind"] not in _CHECKPOINT_KINDS:
        raise WorkflowSetRefusedError(
            f"{name} is a {row['file_kind']} file, so it can't be the checkpoint."
        )
    return row["sha256"], slot, label


def _insert_members(conn, set_id: int, members: list[dict], now: str) -> list[str]:
    """Insert what is not in the set yet; return the sha256s actually added."""
    existing = {
        row["sha256"]: row["slot"]
        for row in conn.execute(
            "SELECT sha256, slot FROM model_workflow_set_member WHERE set_id = ?",
            (set_id,),
        )
    }
    added: list[str] = []
    for member in members:
        sha256, slot, label = _resolve(conn, member)
        if sha256 in existing:
            continue
        if slot == SLOT_CHECKPOINT and SLOT_CHECKPOINT in existing.values():
            raise WorkflowSetRefusedError(
                "This set already has a checkpoint. Remove it first."
            )
        conn.execute(
            "INSERT INTO model_workflow_set_member "
            "(set_id, sha256, slot, label, added_at) VALUES (?, ?, ?, ?, ?)",
            (set_id, sha256, slot, label, now),
        )
        existing[sha256] = slot
        added.append(sha256)
    return added


def create_set(hub, name: Optional[str], members: list[dict]) -> int:
    """Create a set, empty or with *members*, in one transaction; return its id."""
    now = _now()
    with hub.transaction() as conn:
        set_id = int(
            conn.execute(
                "INSERT INTO model_workflow_set (name, created_at, updated_at) "
                "VALUES (?, ?, ?)",
                (clean_name(name), now, now),
            ).lastrowid
        )
        added = _insert_members(conn, set_id, members, now)
    logger.info("Created workflow set %d with %d member(s).", set_id, len(added))
    return set_id


def rename_set(hub, set_id: int, name: Optional[str]) -> None:
    with hub.transaction() as conn:
        _require_set(conn, set_id)
        conn.execute(
            "UPDATE model_workflow_set SET name = ?, updated_at = ? WHERE id = ?",
            (clean_name(name), _now(), set_id),
        )


def delete_set(hub, set_id: int) -> None:
    """Drop the set and its member rows. Never touches a file or a model row."""
    with hub.transaction() as conn:
        _require_set(conn, set_id)
        # Members first: the hub enforces foreign keys.
        conn.execute(
            "DELETE FROM model_workflow_set_member WHERE set_id = ?", (set_id,)
        )
        conn.execute("DELETE FROM model_workflow_set WHERE id = ?", (set_id,))
    logger.info("Deleted workflow set %d.", set_id)


def add_members(hub, set_id: int, members: list[dict]) -> list[str]:
    """Add *members*; one already in the set is a no-op. Returns what was added."""
    now = _now()
    with hub.transaction() as conn:
        _require_set(conn, set_id)
        added = _insert_members(conn, set_id, members, now)
        if added:
            conn.execute(
                "UPDATE model_workflow_set SET updated_at = ? WHERE id = ?",
                (now, set_id),
            )
    return added


def remove_members(hub, set_id: int, sha256s: list[str]) -> list[dict]:
    """Take members out by sha256. An emptied set stays: empty sets are allowed.

    Returns ``[{"sha256", "slot", "label"}]`` for the rows actually removed, so
    the caller can put them back.
    """
    wanted = sorted({sha.lower() for sha in sha256s})
    with hub.transaction() as conn:
        _require_set(conn, set_id)
        removed = [
            dict(row)
            for sha in wanted
            for row in conn.execute(
                "SELECT sha256, slot, label FROM model_workflow_set_member "
                "WHERE set_id = ? AND sha256 = ?",
                (set_id, sha),
            )
        ]
        if removed:
            conn.executemany(
                "DELETE FROM model_workflow_set_member WHERE set_id = ? AND sha256 = ?",
                [(set_id, row["sha256"]) for row in removed],
            )
            conn.execute(
                "UPDATE model_workflow_set SET updated_at = ? WHERE id = ?",
                (_now(), set_id),
            )
    return removed
