"""Reads and writes for ``dataset_snapshot`` and its items (#2123).

A snapshot freezes what one training run was given: each picture's file in the
dataset, the sha256 of those bytes and the caption as written. It is taken when
the dataset is built and never re-read from the vault, so it survives later
tag edits and deletes. ``training_run.dataset_snapshot_id`` names it, and it
goes when its run does.
"""

from __future__ import annotations

from typing import Optional

from pixlstash.hub.db import HubDatabase, now_iso


def record_snapshot(
    hub: HubDatabase,
    *,
    library_uuid: str,
    run_uuid: str,
    set_id: Optional[int],
    character_id: Optional[int],
    trigger_word: Optional[str],
    caption_mode: Optional[str],
    items: list[dict],
) -> int:
    """Store a run's snapshot and point the run at it, in one transaction.

    A run whose dataset is built again (it was still queued at a restart)
    replaces the snapshot it had, so a run names exactly one.

    Args:
        hub: The open hub.
        library_uuid: The library the run belongs to.
        run_uuid: The run the snapshot was taken for.
        set_id: Picture set the dataset came from (a vault id), or None.
        character_id: Character the dataset came from (a vault id), or None.
        trigger_word: The trigger word every caption leads with, or None.
        caption_mode: How the captions were written (``tags``, ...).
        items: One dict per dataset file, in dataset order, with
            ``picture_id``, ``file_name``, ``sha256`` and ``caption``.

    Returns:
        The new ``dataset_snapshot.id``.

    Raises:
        LookupError: No run of that library has *run_uuid*.
    """
    with hub.transaction() as conn:
        run = conn.execute(
            "SELECT dataset_snapshot_id FROM training_run "
            "WHERE uuid = ? AND library_uuid = ?",
            (run_uuid, library_uuid),
        ).fetchone()
        if run is None:
            raise LookupError(f"No training run {run_uuid} in this library.")
        if run[0] is not None:
            conn.execute("DELETE FROM dataset_snapshot WHERE id = ?", (run[0],))
        cursor = conn.execute(
            "INSERT INTO dataset_snapshot (library_uuid, set_id, character_id, "
            "trigger_word, caption_mode, image_count, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                library_uuid,
                set_id,
                character_id,
                trigger_word,
                caption_mode,
                len(items),
                now_iso(),
            ),
        )
        snapshot_id = int(cursor.lastrowid)
        conn.executemany(
            "INSERT INTO dataset_snapshot_item (snapshot_id, position, picture_id, "
            "file_name, sha256, caption) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    snapshot_id,
                    position,
                    item.get("picture_id"),
                    item["file_name"],
                    item["sha256"],
                    item.get("caption"),
                )
                for position, item in enumerate(items)
            ],
        )
        conn.execute(
            "UPDATE training_run SET dataset_snapshot_id = ? WHERE uuid = ?",
            (snapshot_id, run_uuid),
        )
        return snapshot_id


def get_snapshot(
    hub: HubDatabase, library_uuid: str, snapshot_id: int
) -> Optional[dict]:
    """One snapshot of the library with its ``items`` in dataset order, or None."""
    row = hub.fetchone(
        "SELECT * FROM dataset_snapshot WHERE id = ? AND library_uuid = ?",
        (snapshot_id, library_uuid),
    )
    if row is None:
        return None
    items = hub.fetchall(
        "SELECT position, picture_id, file_name, sha256, caption "
        "FROM dataset_snapshot_item WHERE snapshot_id = ? ORDER BY position",
        (snapshot_id,),
    )
    return {**dict(row), "items": [dict(item) for item in items]}


__all__ = ["get_snapshot", "record_snapshot"]
