"""Where a pulled workflow came from, and whether the owner sent it away (#1440).

A pull from ComfyUI's saved workflows files each document through the same
store an import uses, which matches by **content**. This module remembers the
other identity, the **path** over there, and what was read from it:

* **new** - a path with no row;
* **changed** - a row whose path now holds different content;
* **gone from ComfyUI** - a row whose path the listing no longer holds. The row
  is pruned; the local file is never touched;
* **deleted here** - the owner deleted the file a pull stored. The rows naming
  it are marked ``dismissed`` and keep the content they held, and a later pull
  skips any document with that content **at any path and any origin**: a
  rename in ComfyUI, another spelling of its URL, or a listing that came back
  empty for a while is still the workflow the owner deleted. A dismissed row
  is never pruned, since it is the only record of the decision.

``dismissed`` covers deletes made through PixlStash only. Nothing watches the
stored-workflow folder, so a file removed from it by hand comes back on the
next pull.

**Callers hold ``workflow_inbox.INBOX_LOCK``** around a check and the write it
decides, as the delete does around its dismissal: a check made before a delete
and a store made after it would otherwise put the deleted file straight back.

Which stored FILES a pull wrote is kept apart, in ``workflow_pulled_file``, so
it survives whatever happens to the path: the one-off test reads it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from pixlstash.hub.db import HubDatabase


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_dismissed(
    hub: HubDatabase, origin: str, remote_path: str, content_hash: Optional[str]
) -> bool:
    """Whether the owner deleted this path, or this content anywhere.

    Args:
        content_hash: ``workflow_inbox.content_hash`` of the document just
            read, or ``None`` when it could not be computed (then only the
            path is checked).
    """
    if content_hash is not None:
        if hub.fetchone(
            "SELECT 1 FROM workflow_origin WHERE content_hash = ? AND dismissed = 1 "
            "LIMIT 1",
            (content_hash,),
        ):
            return True
    return (
        hub.fetchone(
            "SELECT 1 FROM workflow_origin WHERE origin = ? AND remote_path = ? "
            "AND dismissed = 1",
            (origin, remote_path),
        )
        is not None
    )


def last_content(hub: HubDatabase, origin: str, remote_path: str) -> Optional[str]:
    """The content hash last read from this path, or ``None`` for a new one."""
    row = hub.fetchone(
        "SELECT content_hash FROM workflow_origin WHERE origin = ? AND remote_path = ?",
        (origin, remote_path),
    )
    return row["content_hash"] if row else None


def record_pulled(
    hub: HubDatabase,
    origin: str,
    remote_path: str,
    workflow_name: str,
    remote_modified: Optional[int],
    content_hash: Optional[str],
    *,
    wrote_file: bool,
) -> None:
    """Remember that *remote_path* at *origin* is stored as *workflow_name*.

    An upsert that keeps ``first_pulled_at`` and never clears ``dismissed``.
    *wrote_file* adds the file to the pull-written set; a pull that only
    matched a stored file leaves it as it was, so the owner's own file stays
    theirs.
    """
    now = _now()
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_origin (origin, remote_path, workflow_name, "
            "remote_modified, first_pulled_at, last_seen_at, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (origin, remote_path) DO UPDATE SET "
            "workflow_name = excluded.workflow_name, "
            "remote_modified = excluded.remote_modified, "
            "last_seen_at = excluded.last_seen_at, "
            "content_hash = excluded.content_hash",
            (
                origin,
                remote_path,
                workflow_name,
                remote_modified,
                now,
                now,
                content_hash,
            ),
        )
        if wrote_file:
            conn.execute(
                "INSERT OR IGNORE INTO workflow_pulled_file (workflow_name) VALUES (?)",
                (workflow_name,),
            )


def prune_gone(hub: HubDatabase, origin: str, listed: Iterable[str]) -> int:
    """Forget the rows for paths *origin* no longer lists. Returns how many.

    **Dismissed rows are kept**: they are the record of a delete, and a path
    that is gone today can be listed again tomorrow. **An empty listing prunes
    nothing**: an install that suddenly has no saved workflows (another user
    directory, a proxy answering 404, another ComfyUI on the port) is far more
    likely than one whose every workflow was deleted, and pruning on it would
    forget every path at once. The stored files are never touched.
    """
    listed = set(listed)
    if not listed:
        return 0
    gone = {
        row["remote_path"]
        for row in hub.fetchall(
            "SELECT remote_path FROM workflow_origin WHERE origin = ? "
            "AND dismissed = 0",
            (origin,),
        )
    } - listed
    if not gone:
        return 0
    with hub.transaction() as conn:
        conn.executemany(
            "DELETE FROM workflow_origin WHERE origin = ? AND remote_path = ? "
            "AND dismissed = 0",
            [(origin, path) for path in sorted(gone)],
        )
    return len(gone)


def dismiss_file(hub: HubDatabase, workflow_name: str) -> int:
    """The owner deleted *workflow_name*: mark every path naming it dismissed.

    **Every** row, at every origin: content matching makes the name
    many-to-one, and dismissing only the first would let its twin restore the
    file. The rows keep their ``content_hash``, which is what keeps a renamed
    or re-addressed copy out too. The file is gone, so it leaves the
    pull-written set as well. Returns how many origin rows it marked; ``0``
    for a file that was never pulled.
    """
    with hub.transaction() as conn:
        conn.execute(
            "DELETE FROM workflow_pulled_file WHERE workflow_name = ?",
            (workflow_name,),
        )
        return (
            conn.execute(
                "UPDATE workflow_origin SET dismissed = 1 WHERE workflow_name = ?",
                (workflow_name,),
            ).rowcount
            or 0
        )


def claim_file(hub: HubDatabase, workflow_name: str) -> int:
    """Record that the owner handed *workflow_name* over themselves.

    Called by both hand-over paths, the import route and the watched inbox,
    whenever they store or match a file: a file the owner gave PixlStash is
    theirs whether a pull wrote it first or not, so it leaves the pull-written
    set and can no longer be folded into the hidden one-offs. Returns ``1``
    when it was pull-written, else ``0``.
    """
    with hub.transaction() as conn:
        return (
            conn.execute(
                "DELETE FROM workflow_pulled_file WHERE workflow_name = ?",
                (workflow_name,),
            ).rowcount
            or 0
        )
