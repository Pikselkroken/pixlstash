"""Reads and writes for the ``training_run`` table.

A run is the hub's record of one ai-toolkit LoRA training: what it was trained
on, with what config, and how it ended. ``model.training_run_id`` points here.
Every read and write is scoped by ``library_uuid``, because the set and character
ids a run carries are vault ids and mean nothing in another library.

Rows come back as plain dicts with the column names, timestamps as the ISO
strings the hub stores. Callers that need JSON columns parse them.
"""

from __future__ import annotations

from typing import Optional

from pixlstash.hub.db import HubDatabase, now_iso

# The columns the worker may change after a run is created. Identity and
# ownership (``id``, ``uuid``, ``library_uuid``, ``created_at``) never change.
_MUTABLE_COLUMNS = frozenset(
    {
        "status",
        "base_model",
        "trigger_word",
        "rendered_config",
        "dataset_snapshot_path",
        "dataset_picture_ids",
        "ai_toolkit_commit",
        "training_folder",
        "pid",
        "step",
        "total_steps",
        "loss_curve",
        "sample_image_paths",
        "log_tail",
        "report",
        "error",
        "started_at",
        "completed_at",
    }
)

# Statuses a run holds while the trainer may still be alive.
UNFINISHED_STATUSES = ("running", "stopping")

# Statuses a run ends in: its trainer is gone and nothing more will write to it.
FINISHED_STATUSES = ("completed", "stopped", "error")


def create_run(
    hub: HubDatabase,
    *,
    library_uuid: str,
    uuid: str,
    name: str,
    set_id: Optional[int],
    character_id: Optional[int],
    base_model: Optional[str],
    trigger_word: Optional[str],
    preset: Optional[str] = None,
    resolved_profile: Optional[str] = None,
    advanced_pins: Optional[str] = None,
) -> int:
    """Insert a queued run and return its id.

    Args:
        hub: The open hub.
        library_uuid: The library the set or character belongs to.
        uuid: The run's stable external identifier.
        name: Human-readable name.
        set_id: Picture set trained on (a vault id), or None.
        character_id: Character trained on (a vault id), or None.
        base_model: Base checkpoint the LoRA trains against.
        trigger_word: Caption trigger word.
        preset: The recipe as ``id@version``.
        resolved_profile: The resolver's result, as JSON.
        advanced_pins: The owner's Advanced values, as JSON.

    Returns:
        The new ``training_run.id``.
    """
    with hub.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO training_run (uuid, library_uuid, name, status, set_id, "
            "character_id, base_model, trigger_word, preset, resolved_profile, "
            "advanced_pins, created_at) "
            "VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid,
                library_uuid,
                name,
                set_id,
                character_id,
                base_model,
                trigger_word,
                preset,
                resolved_profile,
                advanced_pins,
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def get_run(hub: HubDatabase, library_uuid: str, run_id: int) -> Optional[dict]:
    """One run of the library, or None when it is absent or in another library."""
    row = hub.fetchone(
        "SELECT * FROM training_run WHERE id = ? AND library_uuid = ?",
        (run_id, library_uuid),
    )
    return dict(row) if row is not None else None


def get_run_by_uuid(hub: HubDatabase, run_uuid: str) -> Optional[dict]:
    """One run by its uuid, whichever library holds it, or None.

    For the worker, which already holds the uuid of a run it owns. Routes take
    the integer id through :func:`get_run`, which is library-scoped.
    """
    row = hub.fetchone("SELECT * FROM training_run WHERE uuid = ?", (run_uuid,))
    return dict(row) if row is not None else None


def list_runs(
    hub: HubDatabase,
    library_uuid: str,
    *,
    set_id: Optional[int] = None,
    character_id: Optional[int] = None,
) -> list[dict]:
    """The library's runs, newest first, optionally for one set or character."""
    clauses = ["library_uuid = ?"]
    params: list = [library_uuid]
    if set_id is not None:
        clauses.append("set_id = ?")
        params.append(set_id)
    if character_id is not None:
        clauses.append("character_id = ?")
        params.append(character_id)
    rows = hub.fetchall(
        "SELECT * FROM training_run WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at DESC, id DESC",
        tuple(params),
    )
    return [dict(row) for row in rows]


def next_queued_run(hub: HubDatabase, library_uuid: str) -> Optional[dict]:
    """The oldest queued run of the library (FIFO), or None."""
    row = hub.fetchone(
        "SELECT * FROM training_run WHERE library_uuid = ? AND status = 'queued' "
        "ORDER BY created_at, id LIMIT 1",
        (library_uuid,),
    )
    return dict(row) if row is not None else None


def unfinished_runs(hub: HubDatabase, library_uuid: str) -> list[dict]:
    """The library's runs left running or stopping, for the startup reconcile."""
    marks = ", ".join("?" for _ in UNFINISHED_STATUSES)
    rows = hub.fetchall(
        "SELECT * FROM training_run WHERE library_uuid = ? "
        f"AND status IN ({marks}) ORDER BY created_at, id",
        (library_uuid, *UNFINISHED_STATUSES),
    )
    return [dict(row) for row in rows]


def update_run(hub: HubDatabase, run_uuid: str, **fields) -> bool:
    """Set mutable columns on one run, by uuid.

    Args:
        hub: The open hub.
        run_uuid: The run's uuid.
        **fields: Column names to set. Names outside the mutable set raise.

    Returns:
        True when a row was updated, False when no run has that uuid.

    Raises:
        ValueError: A field is not a mutable column.
    """
    unknown = set(fields) - _MUTABLE_COLUMNS
    if unknown:
        raise ValueError(f"Not a mutable training_run column: {sorted(unknown)}")
    if not fields:
        return False
    assignments = ", ".join(f"{column} = ?" for column in fields)
    with hub.transaction() as conn:
        cursor = conn.execute(
            f"UPDATE training_run SET {assignments} WHERE uuid = ?",
            (*fields.values(), run_uuid),
        )
        return cursor.rowcount > 0


def delete_run(
    hub: HubDatabase, library_uuid: str, run_id: int, *, only_finished: bool = False
) -> Optional[str]:
    """Delete one run of the library. Returns its uuid, or None when absent.

    With *only_finished*, the status test is part of the DELETE itself, so a
    run that is queued, running or stopping (its trainer may still write into
    its folder) is left alone and None is returned. Checking the status in a
    separate read would leave a window in which the worker can start the run.

    Models the run put on the shelf are not touched: they keep their
    ``training_run_id``, which then names nothing, the same as after any other
    deleted run.
    """
    with hub.transaction() as conn:
        row = conn.execute(
            "SELECT uuid FROM training_run WHERE id = ? AND library_uuid = ?",
            (run_id, library_uuid),
        ).fetchone()
        if row is None:
            return None
        if only_finished:
            marks = ", ".join("?" for _ in FINISHED_STATUSES)
            deleted = conn.execute(
                f"DELETE FROM training_run WHERE id = ? AND status IN ({marks})",
                (run_id, *FINISHED_STATUSES),
            )
            return str(row[0]) if deleted.rowcount else None
        conn.execute("DELETE FROM training_run WHERE id = ?", (run_id,))
        return str(row[0])


__all__ = [
    "FINISHED_STATUSES",
    "UNFINISHED_STATUSES",
    "create_run",
    "delete_run",
    "get_run",
    "get_run_by_uuid",
    "list_runs",
    "next_queued_run",
    "unfinished_runs",
    "update_run",
]
