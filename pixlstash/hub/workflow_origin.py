"""Where a pulled workflow came from, and whether the owner sent it away (#1440).

A pull from ComfyUI's saved workflows files each document through the same
store as an import, which matches by **content**. This module remembers the
other identity, the **path** over there, so that a re-pull can tell three
things apart that content alone cannot:

* **new** - a path with no row;
* **gone from ComfyUI** - a row whose path the listing no longer holds. The row
  is pruned; the local file is never touched;
* **deleted here** - a row the owner dismissed by deleting the file it was
  stored as. Later pulls skip it, so a delete is not undone by the next pull.

Content and path deliberately disagree: a rename in ComfyUI is one path gone and
one path new, both resolving to the file already stored, and a delete here must
dismiss **every** path naming that file, not the first one.

``dismissed`` covers deletes made through PixlStash only. Nothing watches the
stored-workflow folder, so a file removed from it by hand comes back on the next
pull.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from pixlstash.hub.db import HubDatabase


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dismissed_paths(hub: HubDatabase, origin: str) -> set[str]:
    """The paths at *origin* the owner deleted here, which a pull skips."""
    return {
        row["remote_path"]
        for row in hub.fetchall(
            "SELECT remote_path FROM workflow_origin "
            "WHERE origin = ? AND dismissed = 1",
            (origin,),
        )
    }


def known_paths(hub: HubDatabase, origin: str) -> set[str]:
    """Every path at *origin* a previous pull has a row for."""
    return {
        row["remote_path"]
        for row in hub.fetchall(
            "SELECT remote_path FROM workflow_origin WHERE origin = ?", (origin,)
        )
    }


def record_pulled(
    hub: HubDatabase,
    origin: str,
    remote_path: str,
    workflow_name: str,
    remote_modified: Optional[int],
    stored: bool = False,
) -> None:
    """Remember that *remote_path* at *origin* is stored as *workflow_name*.

    An upsert that keeps ``first_pulled_at`` and never clears ``dismissed``:
    the caller skips a dismissed path before it gets here, and a row written
    anyway must not quietly un-dismiss it.

    *stored* is true when this pull wrote the file rather than matching one
    already there. It is sticky per row - a later pull that matches the file
    it wrote itself does not make it the owner's - but it only ever describes
    this path, so a file the owner imported first stays hand-imported.
    """
    now = _now()
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_origin (origin, remote_path, workflow_name, "
            "remote_modified, first_pulled_at, last_seen_at, stored_by_pull) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (origin, remote_path) DO UPDATE SET "
            "stored_by_pull = CASE WHEN workflow_name = excluded.workflow_name "
            "THEN MAX(stored_by_pull, excluded.stored_by_pull) "
            "ELSE excluded.stored_by_pull END, "
            "workflow_name = excluded.workflow_name, "
            "remote_modified = excluded.remote_modified, "
            "last_seen_at = excluded.last_seen_at",
            (
                origin,
                remote_path,
                workflow_name,
                remote_modified,
                now,
                now,
                int(stored),
            ),
        )


def prune_gone(hub: HubDatabase, origin: str, listed: Iterable[str]) -> int:
    """Forget the rows for paths *origin* no longer lists. Returns how many.

    Dismissed rows go too: a path that no longer exists there cannot be pulled
    back, so there is nothing left for the dismissal to prevent. The stored
    file is never touched - it is the owner's now.
    """
    gone = known_paths(hub, origin) - set(listed)
    if not gone:
        return 0
    with hub.transaction() as conn:
        conn.executemany(
            "DELETE FROM workflow_origin WHERE origin = ? AND remote_path = ?",
            [(origin, path) for path in sorted(gone)],
        )
    return len(gone)


def dismiss_file(hub: HubDatabase, workflow_name: str) -> int:
    """Mark every pulled path stored as *workflow_name* dismissed.

    Called when the owner deletes that file, so the next pull does not bring
    it back. **Every** row, at every origin: content matching makes the name
    many-to-one, and dismissing only the first would let its twin restore the
    file. Returns how many rows it marked; ``0`` for a file that was never
    pulled.
    """
    with hub.transaction() as conn:
        return (
            conn.execute(
                "UPDATE workflow_origin SET dismissed = 1 WHERE workflow_name = ?",
                (workflow_name,),
            ).rowcount
            or 0
        )


def claim_file(hub: HubDatabase, workflow_name: str) -> int:
    """Record that the owner put *workflow_name* here themselves.

    Called by the import route whenever it stores or matches a file: a file
    the owner handed over is theirs, whether a pull wrote it first or not, so
    it must stop counting as pull-written (``stored_by_pull``) and can no
    longer be folded into the hidden one-offs. Returns how many rows changed.
    """
    with hub.transaction() as conn:
        return (
            conn.execute(
                "UPDATE workflow_origin SET stored_by_pull = 0 "
                "WHERE workflow_name = ? AND stored_by_pull = 1",
                (workflow_name,),
            ).rowcount
            or 0
        )
