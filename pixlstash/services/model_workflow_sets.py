"""Hand-made workflow sets (#1520): shelf models the owner says work together.

A set is a menu, not a recipe: fixed slots (any number of checkpoints, since a
two-model workflow such as a Wan 2.2 high/low pair loads two, and of text
encoders, VAEs, LoRAs and others), no order and no strengths. It is a
hub fact, like the shelf itself, so it is the same in every library.

**Members are sha256s, not model ids.** A file that leaves the shelf takes its
``model`` row with it, and the set keeps the member (drawn as not on the shelf)
so it reconnects when a file with the same bytes comes back. That is also why a
checkpoint still waiting for its hash cannot be added yet: it has no identity a
set could hold.

**A set lives on its checkpoint (#1972).** What a set holds is not only its
member rows: it is its *base*, every model the pictures made with one of its
checkpoints ran with, plus the members the owner stored, minus the models the
owner removed (``model_workflow_set_decline``). So a LoRA new pictures start
using joins every set on that checkpoint by itself, and removing it keeps it
out of that set for good. The set reads *automatic* while the owner has added
and removed nothing, and *custom* otherwise. Every recipe combination headed by
one of its checkpoints is *covered* by it and counts toward it, so the
automatic card for a checkpoint is drawn only while no set is on it.

**Evidence is read, never written.** The combinations come from
:func:`~pixlstash.services.model_shelf_service.fetch_workflow_sets` unchanged,
and :func:`attach_hand_made` layers the sets over them. Nothing is stored for
the base: it is re-derived on every read.
"""

from __future__ import annotations

from typing import Optional

from pixlstash.hub.db import now_iso
from pixlstash.pixl_logging import get_logger
from pixlstash.services.builtin_models import HASH_PENDING_SQL
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
    return _collect(
        hub.fetchall(_SETS_SQL + " ORDER BY created_at DESC, id DESC"),
        hub.fetchall(_MEMBERS_SQL),
        hub.fetchall("SELECT set_id, sha256 FROM model_workflow_set_decline"),
    )


# The two reads a set is made of, shared by `fetch_sets` (every set, through
# the hub) and `delete_set` (one set, on the transaction that deletes it).
_SETS_SQL = (
    "SELECT id, name, created_at, updated_at, cloned, template FROM model_workflow_set"
)
# `model.sha256` is UNIQUE, so the LEFT JOIN yields one row per member.
_MEMBERS_SQL = (
    "SELECT m.set_id, m.sha256, m.slot, m.label, model.id, model.display_name, "
    "model.filename, model.file_kind, model.base_model, "
    "model.base_model_canonical, model.base_model_source, model.file_size "
    "FROM model_workflow_set_member AS m "
    "LEFT JOIN model ON model.sha256 = m.sha256"
)


def _collect(set_rows, member_rows, decline_rows=()) -> list[dict]:
    """Set rows and member rows as `fetch_sets` entries, in set-row order."""
    sets = [{**dict(row), "members": [], "declined": []} for row in set_rows]
    by_id = {entry["id"]: entry for entry in sets}
    for row in decline_rows:
        if row["set_id"] in by_id:
            by_id[row["set_id"]]["declined"].append(row["sha256"])
    for row in member_rows:
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
    """Layer the owner's sets over a :func:`fetch_workflow_sets` answer.

    Mutates *found* in place and returns it. Each set lives on its checkpoints
    (#1972): its *base* is every model recipes or ComfyUI runs used with one of
    them, and what it holds is that base plus its stored members, minus the
    models the owner removed (``declined``). Every combination headed by one of
    a set's checkpoints gains that set's id in ``covered_by`` and counts toward
    it, so the automatic card for a checkpoint leaves the grid while any set
    is on it; one whose base model is ``missing`` is covered by no set.
    ``no_set`` loses every on-shelf member, and ``hand_made`` is added, newest
    first, each set's ``covers`` cover candidates like a combination's.

    The base reads every library's recipes (``hub_combinations``) and the runs
    stored here: a set is a hub fact, so what it holds must be the same in
    every library. Its counts and covers are this library's.
    """
    rows = {int(row["id"]): row for row in hub.fetchall(_MODEL_ROWS_SQL)}
    seen: dict[int, set[str]] = {}
    for row in hub.fetchall("SELECT set_id, sha256 FROM model_workflow_set_seen"):
        seen.setdefault(row["set_id"], set()).add(row["sha256"])
    here = found["combinations"]
    every = {c["key"]: c for c in found.get("hub_combinations", ())}
    for combination in here:
        every.setdefault(combination["key"], combination)
    for combination in here:
        combination["covered_by"] = []

    hand_made = []
    for entry in fetch_sets(hub):
        shaped = _shape(entry)
        own = set(shaped["checkpoint_ids"])
        stored_ids = {m["id"] for m in shaped["members"] if m["on_shelf"]}
        base = _base(
            [c for c in every.values() if _on_checkpoint(c, own)], stored_ids, rows
        )
        on_here = [c for c in here if _on_checkpoint(c, own)]
        for combination in on_here:
            combination["covered_by"].append(shaped["id"])
            shaped["picture_count"] += combination["picture_count"]
            shaped["recipes"] += combination["recipes"]
            shaped["history_runs"] += combination["history_runs"]
            shaped["covers"].extend(combination["covers"])
        shaped["covers"] = sorted(shaped["covers"], key=cover_order, reverse=True)[
            :SET_COVER_DEPTH
        ]
        _live(shaped, base, on_here, rows, seen.get(shaped["id"], set()))
        hand_made.append(shaped)

    grouped = {
        m["id"] for entry in hand_made for m in entry["members"] if m["on_shelf"]
    }
    found["no_set"] = [
        model_id for model_id in found["no_set"] if model_id not in grouped
    ]
    found["hand_made"] = hand_made
    return found


# The shelf rows a set's base is made of, read once per evidence pass.
_MODEL_ROWS_SQL = (
    "SELECT id, sha256, display_name, filename, file_kind, base_model, "
    "base_model_canonical, base_model_source, file_size FROM model"
)


def _on_checkpoint(combination: dict, own: set[int]) -> bool:
    """Whether *combination* is headed by one of a set's checkpoints *own*.

    The head is the first member, as the grid names a card after it, and it
    must be a base model. A head that is a namesake of the set's checkpoint
    (one name reached both rows) is the set's own: which twin heads the
    combination is an accident of id order.
    """
    if not own or combination.get("missing") or not combination["models"]:
        return False
    head = combination["models"][0]
    if head["kind"] not in _CHECKPOINT_KINDS:
        return False
    return head["id"] in own or any(
        head["id"] in names and own & set(names)
        for names in combination.get("namesakes", ())
    )


def _base(combinations: list[dict], stored_ids: set[int], rows) -> set[int]:
    """The model ids the pictures on a set's checkpoints ran with.

    A recipe naming a file by basename reaches every shelf row answering to
    it, so its combination holds them all though it ran with one: each such
    group counts once, as the row the set already holds or else the first.
    ponytail: "the first" is the lowest id, since a recipe that named a file
    only by basename recorded no digest to choose by; the owner removes the
    wrong twin by hand. The groups are the resolver's own (``namesakes``),
    never re-derived from filenames. A file still hashing or an engine is left out, since a set
    keeps its members by digest and no workflow loads an engine.
    """
    ids: set[int] = set()
    groups: list[set[int]] = []
    for combination in combinations:
        ids.update(model["id"] for model in combination["models"])
        groups.extend(set(names) for names in combination.get("namesakes", ()))
    for group in groups:
        ids -= group - (group & stored_ids or {min(group)})
    return {
        model_id
        for model_id in ids
        if model_id in rows
        and rows[model_id]["sha256"]
        and rows[model_id]["file_kind"] != FILE_ENGINE
    }


def _live(shaped: dict, base: set[int], on_here, rows, seen: set[str]) -> None:
    """Turn a set's stored members into what it holds (#1972), in place.

    ``members`` becomes the base plus the stored members, minus ``declined``,
    each marked with its ``origin`` (``pictures`` when the base has it,
    ``added`` when only the owner put it there), whether a member row
    ``stored`` it, and ``new`` for a model the pictures brought in that the
    owner has not seen in this set yet. ``removed`` is the base models the
    owner took out. The set is ``automatic`` while it has a checkpoint and the
    owner has neither added nor removed anything: the badge is a fact about
    those two lists, so undoing the last change turns it back.
    """
    declined = set(shaped["declined"])
    stored = {m["sha256"]: m for m in shaped["members"]}
    base_by_sha = {rows[model_id]["sha256"]: model_id for model_id in base}
    evidence: dict[int, list[int]] = {}
    for combination in on_here:
        for model in combination["models"]:
            counts = evidence.setdefault(model["id"], [0, 0])
            counts[0] += combination["recipes"]
            counts[1] += combination["picture_count"]

    def counted(member: dict) -> dict:
        recipes, pictures = evidence.get(member["id"], (0, 0))
        return {**member, "recipes": recipes, "picture_count": pictures}

    members = [
        counted(
            {
                **member,
                "stored": True,
                # The set's own checkpoints are what it follows, never an
                # addition: a set on a checkpoint no picture used yet is
                # still exactly what its pictures ran with.
                "origin": (
                    "pictures"
                    if sha in base_by_sha or member["slot"] == SLOT_CHECKPOINT
                    else "added"
                ),
                "new": False,
            }
        )
        for sha, member in stored.items()
        if sha not in declined
    ]
    removed = []
    for sha, model_id in base_by_sha.items():
        row = rows[model_id]
        if sha in declined:
            slot = stored[sha]["slot"] if sha in stored else _base_slot(row)
            removed.append(
                counted(
                    _member_out(
                        {"sha256": sha, "slot": slot, "label": None, "model": row}
                    )
                )
            )
        elif sha not in stored:
            members.append(
                counted(
                    {
                        **_member_out(
                            {
                                "sha256": sha,
                                "slot": _base_slot(row),
                                "label": None,
                                "model": row,
                            }
                        ),
                        "stored": False,
                        "origin": "pictures",
                        "new": sha not in seen,
                    }
                )
            )
    # The set's own checkpoints lead their slot: the set is named after the
    # first one, and a checkpoint the pictures brought in must not take that.
    shaped["members"] = sorted(
        members,
        key=lambda m: (
            SLOTS.index(m["slot"]),
            m["slot"] == SLOT_CHECKPOINT and not m["stored"],
            m["name"].lower(),
            m["sha256"],
        ),
    )
    shaped["removed"] = sorted(
        removed, key=lambda m: (SLOTS.index(m["slot"]), m["name"].lower())
    )
    added = sum(1 for m in members if m["origin"] == "added")
    shaped["added"] = added
    shaped["automatic"] = not shaped["incomplete"] and not added and not removed


def _lacks(combination: dict, held: set[int]) -> set[int]:
    """The models of *combination* a set holding *held* does not have.

    A recipe that names a file only by its basename (or a short digest) reaches
    every shelf row answering to it, so its combination holds them all though
    it ran with one. Holding one of them is holding the file it ran with, so
    its namesakes are not missing. The groups are the resolver's own
    (``namesakes``), never re-derived from filenames: a row also answers to
    every copy's basename. Read by the template picker's coverage
    (``services.workflow_templates``).
    """
    groups = [set(names) for names in combination.get("namesakes", ())]
    return {
        model["id"]
        for model in combination["models"]
        if model["id"] not in held
        and not any(model["id"] in names and names & held for names in groups)
    }


def _base_slot(row) -> str:
    """Where a model the pictures brought in sits: by its kind, so a second
    checkpoint (Wan 2.2's other expert, a refiner) joins the checkpoints."""
    return _DEFAULT_SLOT.get(row["file_kind"], "other")


def _shape(entry: dict) -> dict:
    """One `fetch_sets` entry as the API serves a set, before any evidence.

    ``members`` are the stored ones here; :func:`_live` turns them into what
    the set holds.
    """
    members = sorted(
        (_member_out(member) for member in entry["members"]),
        key=lambda m: (SLOTS.index(m["slot"]), m["name"].lower(), m["sha256"]),
    )
    checkpoints = [m for m in members if m["slot"] == SLOT_CHECKPOINT]
    return {
        "id": entry["id"],
        "name": entry["name"],
        "created_at": entry["created_at"],
        "updated_at": entry["updated_at"],
        "cloned": bool(entry["cloned"]),
        "template": entry["template"],
        "incomplete": not checkpoints,
        "checkpoint_ids": [m["id"] for m in checkpoints if m["on_shelf"]],
        "picture_count": 0,
        "recipes": 0,
        "history_runs": 0,
        "covers": [],
        "members": members,
        "declined": sorted(entry.get("declined", ())),
        "removed": [],
        "added": 0,
        "automatic": False,
        # The template offer (#1999), filled by `workflow_templates`.
        "offer": None,
    }


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

    *member* is ``{"model_id", "slot"?}`` or ``{"sha256", "slot", "label"?}``.
    The second form is how an undo puts a member back, so it is restored as it
    was, in the slot it held: re-applying the kind rules there would refuse a
    member whose file was re-kinded since it went in, and roll back the whole
    undo with the set lost. The kind rules apply to the ``model_id`` form.
    """
    if member.get("model_id") is None:
        return member["sha256"].lower(), member["slot"], member.get("label")
    row = conn.execute(
        "SELECT m.id, m.sha256, m.file_kind, m.display_name, m.filename, "
        f"({HASH_PENDING_SQL}) AS hash_pending FROM model m WHERE m.id = ?",
        (member["model_id"],),
    ).fetchone()
    if row is None:
        raise WorkflowSetNotFoundError(
            f"No model with id {member['model_id']} on the shelf."
        )
    # Cut to what the undo path accepts back (`label` is capped at 500 on the
    # request), or a long display name would make its own undo a 422.
    label = _model_name(row)[:500]

    name = _model_name(row)
    if row["file_kind"] == FILE_ENGINE:
        raise WorkflowSetRefusedError(f"{name} isn't a model file a workflow loads.")
    if row["sha256"] is None:
        raise WorkflowSetRefusedError(
            f"{name} is still being hashed. Add it once the shelf has read it."
            if row["hash_pending"]
            else f"{name} has no file hash, so a set can't keep it."
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
        row["sha256"]
        for row in conn.execute(
            "SELECT sha256 FROM model_workflow_set_member WHERE set_id = ?",
            (set_id,),
        )
    }
    added: list[str] = []
    for member in members:
        sha256, slot, label = _resolve(conn, member)
        if sha256 in existing:
            continue
        conn.execute(
            "INSERT INTO model_workflow_set_member "
            "(set_id, sha256, slot, label, added_at) VALUES (?, ?, ?, ?, ?)",
            (set_id, sha256, slot, label, now),
        )
        existing.add(sha256)
        added.append(sha256)
    return added


def create_set(
    hub,
    name: Optional[str],
    members: list[dict],
    cloned: bool = False,
    template: Optional[str] = None,
    declined: Optional[list[str]] = None,
) -> int:
    """Create a set, empty or with *members*, in one transaction; return its id.

    *cloned* records that the owner made it from a set from pictures; it is
    only stored and served (#1972). *template* is the
    ComfyUI template it was made from (``services.workflow_templates``), which
    it then covers in the New workflow set picker. *declined* is its removed
    list, written in the same transaction, so a set put back by an undo never
    holds, even for a moment, what its owner had taken out.
    """
    now = now_iso()
    with hub.transaction() as conn:
        set_id = int(
            conn.execute(
                "INSERT INTO model_workflow_set "
                "(name, created_at, updated_at, cloned, template) "
                "VALUES (?, ?, ?, ?, ?)",
                (clean_name(name), now, now, int(cloned), template),
            ).lastrowid
        )
        added = _insert_members(conn, set_id, members, now)
        conn.executemany(
            "INSERT INTO model_workflow_set_decline (set_id, sha256, declined_at) "
            "VALUES (?, ?, ?)",
            [(set_id, sha, now) for sha in sorted({d.lower() for d in declined or ()})],
        )
    logger.info("Created workflow set %d with %d member(s).", set_id, len(added))
    return set_id


def rename_set(hub, set_id: int, name: Optional[str]) -> None:
    with hub.transaction() as conn:
        _require_set(conn, set_id)
        conn.execute(
            "UPDATE model_workflow_set SET name = ?, updated_at = ? WHERE id = ?",
            (clean_name(name), now_iso(), set_id),
        )


def delete_set(hub, set_id: int) -> dict:
    """Drop the set and its rows. Never touches a file or a model row.

    Returns the set as it was, read on the SAME transaction that deletes it:
    that snapshot is the undo, and read beforehand it could miss a member added
    between the read and the delete - which the undo would then lose. It
    carries ``declined`` and ``seen`` too, since what the owner removed and
    what they have seen are part of the set (#1972). Coverage (pictures,
    covers) is not computed; an undo needs none.
    """
    with hub.transaction() as conn:
        snapshot = _delete_in(conn, set_id)
    logger.info("Deleted workflow set %d.", set_id)
    return snapshot


def _delete_in(conn, set_id: int) -> dict:
    """Delete one set on *conn*; return it as it was, shaped, with ``seen``."""
    _require_set(conn, set_id)
    seen = [
        row["sha256"]
        for row in conn.execute(
            "SELECT sha256 FROM model_workflow_set_seen WHERE set_id = ? "
            "ORDER BY sha256",
            (set_id,),
        )
    ]
    (entry,) = _collect(
        conn.execute(_SETS_SQL + " WHERE id = ?", (set_id,)).fetchall(),
        conn.execute(_MEMBERS_SQL + " WHERE m.set_id = ?", (set_id,)).fetchall(),
        conn.execute(
            "SELECT set_id, sha256 FROM model_workflow_set_decline WHERE set_id = ?",
            (set_id,),
        ).fetchall(),
    )
    # Its child rows first: the hub enforces foreign keys.
    for table in (
        "model_workflow_set_member",
        "model_workflow_set_decline",
        "model_workflow_set_seen",
    ):
        conn.execute(f"DELETE FROM {table} WHERE set_id = ?", (set_id,))
    conn.execute("DELETE FROM model_workflow_set WHERE id = ?", (set_id,))
    # Every library's hide row for it, so the AUTOINCREMENT id it leaves
    # behind is not a hidden row for nothing.
    conn.execute(
        "DELETE FROM workflow_set_hidden WHERE set_key = ?", (f"hand:{set_id}",)
    )
    return {**_shape(entry), "seen": seen}


def add_members(hub, set_id: int, members: list[dict]) -> tuple[list[str], list[str]]:
    """Add *members*; one already in the set is a no-op.

    A model the owner had removed comes back: adding it is saying so, and a
    removal would otherwise win over the member row (#1972).

    Returns ``(added, undeclined)``: the sha256s inserted, and the ones taken
    off the removed list, which an undo puts back on it.
    """
    now = now_iso()
    with hub.transaction() as conn:
        _require_set(conn, set_id)
        added = _insert_members(conn, set_id, members, now)
        wanted = {_resolve(conn, member)[0] for member in members}
        undeclined = [
            row["sha256"]
            for row in conn.execute(
                "SELECT sha256 FROM model_workflow_set_decline WHERE set_id = ? "
                "ORDER BY sha256",
                (set_id,),
            )
            if row["sha256"] in wanted
        ]
        conn.executemany(
            "DELETE FROM model_workflow_set_decline WHERE set_id = ? AND sha256 = ?",
            [(set_id, sha) for sha in undeclined],
        )
        if added or undeclined:
            conn.execute(
                "UPDATE model_workflow_set SET updated_at = ? WHERE id = ?",
                (now, set_id),
            )
    return added, undeclined


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
                (now_iso(), set_id),
            )
    return removed


def set_declines(hub, set_id: int, sha256s: list[str]) -> list[str]:
    """Replace the models the owner removed from this set (#1972).

    A removed model stays out of the set however often the pictures on its
    checkpoint use it. A whole-list write, so Remove, Restore, Reset to
    automatic and each one's undo are the same call. Returns the list as it
    was, sorted: that is the undo.
    """
    wanted = sorted({sha.lower() for sha in sha256s})
    now = now_iso()
    with hub.transaction() as conn:
        _require_set(conn, set_id)
        previous = [
            row["sha256"]
            for row in conn.execute(
                "SELECT sha256 FROM model_workflow_set_decline WHERE set_id = ? "
                "ORDER BY sha256",
                (set_id,),
            )
        ]
        conn.execute(
            "DELETE FROM model_workflow_set_decline WHERE set_id = ?", (set_id,)
        )
        conn.executemany(
            "INSERT INTO model_workflow_set_decline (set_id, sha256, declined_at) "
            "VALUES (?, ?, ?)",
            [(set_id, sha, now) for sha in wanted],
        )
    logger.info(
        "Workflow set %d has %d model(s) removed (was %d).",
        set_id,
        len(wanted),
        len(previous),
    )
    return previous


def merge_sets(
    hub, library_uuid: Optional[str], set_id: int, other_ids: list[int]
) -> dict:
    """Merge *other_ids* into *set_id*, in one transaction (#1972).

    Only sets on the same checkpoint merge: every set's checkpoint members
    must be the same digests (the same pair, for a two-model workflow), or the
    merge is refused. The survivor keeps its name and gains every member any
    of the others stored; a model stays removed only if every set had removed
    it, so a merge never drops a model one of them held. It stays hidden in
    this library only if every set was. The others are deleted. No file is
    touched.

    Returns what an undo needs: ``added`` (the members inserted into the
    survivor), ``previous_declined`` (its removed list before), ``was_hidden``,
    and ``merged``, each deleted set's snapshot with its own ``hidden``.
    """
    others = sorted(set(other_ids) - {set_id})
    if not others:
        raise WorkflowSetRefusedError("Pick at least one other set to merge.")
    with hub.transaction() as conn:
        everyone = [set_id, *others]
        for one in everyone:
            _require_set(conn, one)
        pairs = {
            one: frozenset(
                row["sha256"]
                for row in conn.execute(
                    "SELECT sha256 FROM model_workflow_set_member "
                    "WHERE set_id = ? AND slot = ?",
                    (one, SLOT_CHECKPOINT),
                )
            )
            for one in everyone
        }
        if not pairs[set_id] or any(pairs[one] != pairs[set_id] for one in others):
            raise WorkflowSetRefusedError("These sets use different checkpoints.")
        hidden = {
            one: library_uuid is not None
            and conn.execute(
                "SELECT 1 FROM workflow_set_hidden "
                "WHERE library_uuid = ? AND set_key = ?",
                (library_uuid, f"hand:{one}"),
            ).fetchone()
            is not None
            for one in everyone
        }
        declines = {
            one: {
                row["sha256"]
                for row in conn.execute(
                    "SELECT sha256 FROM model_workflow_set_decline WHERE set_id = ?",
                    (one,),
                )
            }
            for one in everyone
        }
        now = now_iso()
        added = _insert_members(
            conn,
            set_id,
            [
                dict(row)
                for one in others
                for row in conn.execute(
                    "SELECT sha256, slot, label FROM model_workflow_set_member "
                    "WHERE set_id = ?",
                    (one,),
                )
                # A row its own set had removed is not a model that set held.
                if row["sha256"] not in declines[one]
            ],
            now,
        )
        merged = []
        for one in others:
            merged.append({**_delete_in(conn, one), "hidden": hidden[one]})
        kept_out = set.intersection(*declines.values())
        conn.execute(
            "DELETE FROM model_workflow_set_decline WHERE set_id = ?", (set_id,)
        )
        conn.executemany(
            "INSERT INTO model_workflow_set_decline (set_id, sha256, declined_at) "
            "VALUES (?, ?, ?)",
            [(set_id, sha, now) for sha in sorted(kept_out)],
        )
        if hidden[set_id] and not all(hidden.values()):
            conn.execute(
                "DELETE FROM workflow_set_hidden "
                "WHERE library_uuid = ? AND set_key = ?",
                (library_uuid, f"hand:{set_id}"),
            )
        conn.execute(
            "UPDATE model_workflow_set SET updated_at = ? WHERE id = ?",
            (now, set_id),
        )
    logger.info(
        "Merged workflow set(s) %s into %d: %d member(s) added, %d removed kept.",
        others,
        set_id,
        len(added),
        len(kept_out),
    )
    return {
        "added": added,
        "previous_declined": sorted(declines[set_id]),
        "was_hidden": hidden[set_id],
        "merged": merged,
    }


def mark_seen(hub, set_id: int, sha256s: list[str]) -> None:
    """Record that the owner has seen these models in this set's tray.

    A model the pictures bring into a set is marked new until then (#1972).
    Rows for a model that later leaves the base are harmless: they are only
    read against what the base holds.
    """
    wanted = sorted({sha.lower() for sha in sha256s})
    now = now_iso()
    with hub.transaction() as conn:
        _require_set(conn, set_id)
        conn.executemany(
            "INSERT OR IGNORE INTO model_workflow_set_seen (set_id, sha256, seen_at) "
            "VALUES (?, ?, ?)",
            [(set_id, sha, now) for sha in wanted],
        )


def hidden_set_keys(hub, library_uuid: Optional[str]) -> list[str]:
    """The set keys hidden in ONE library, sorted. ``[]`` with no library."""
    if not library_uuid:
        return []
    return [
        row["set_key"]
        for row in hub.fetchall(
            "SELECT set_key FROM workflow_set_hidden WHERE library_uuid = ? "
            "ORDER BY set_key",
            (library_uuid,),
        )
    ]


def set_sets_hidden(
    hub, library_uuid: Optional[str], keys: list[str], hidden: bool
) -> list[str]:
    """Hide or show sets in one library; answer with that library's hidden keys.

    A key is the shelf's own for a set - `model:<head id>` (or
    `missing:<filename>`), or ``hand:<id>`` for a hand-made set - so no key has
    to correspond to a row. A hub with no active library hides nothing (there
    is no library to hide it in).
    """
    if not library_uuid or not keys:
        return hidden_set_keys(hub, library_uuid)
    with hub.transaction() as conn:
        if hidden:
            conn.executemany(
                "INSERT OR IGNORE INTO workflow_set_hidden (library_uuid, set_key) "
                "VALUES (?, ?)",
                [(library_uuid, key) for key in keys],
            )
        else:
            conn.executemany(
                "DELETE FROM workflow_set_hidden "
                "WHERE library_uuid = ? AND set_key = ?",
                [(library_uuid, key) for key in keys],
            )
    return hidden_set_keys(hub, library_uuid)
