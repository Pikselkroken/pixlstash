"""Where a pulled workflow came from, and whether the owner sent it away (#1440).

A pull from ComfyUI's saved workflows files each document through the same
store an import uses, which matches by **content**. This module remembers the
other identity, the **path** over there, and what was read from it:

* **new** - a path with no row;
* **changed** - a row whose path now holds different content: a new version
  of the workflow the row names (``hub/workflow_versions.py``);
* **gone from ComfyUI** - a row whose path the listing no longer holds. The row
  is kept and marked ``gone_at``, so its workflow keeps its versions and Open
  can tell the file is gone; listed again, the mark clears;
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

``workflow_name`` names the manual workflow (``manual:<uuid>``) a document is
stored as; rows written before data step 7 named a file, and that step renamed
them. The watched inbox (``INBOX_ORIGIN``, keyed by content hash) and a
built-in made a workflow (``BUILTIN_ORIGIN``, keyed by file name) record rows
here too, so every way in deduplicates on this table alone
(:func:`stored_as`) and never on a folder. What a pull stored is the
workflow's own ``origin = 'pull'``: the one-off test reads it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.services.comfyui_userdata import is_safe_workflow_path

# The origin a watched-inbox file is recorded under, keyed by its content hash
# (``remote_path`` is the hash too): one row per content, so a restart that
# reads every inbox file again matches each.
INBOX_ORIGIN = "inbox"

# The origin a user-folder file data step 7 made a manual workflow is recorded
# under, keyed by its file name: deleting the workflow trashes the file too.
FILE_ORIGIN = "file"

# The origin a built-in stored as a manual workflow is recorded under, keyed by
# its file name, so ``POST /comfyui/workflows/{name}/card`` makes it once.
BUILTIN_ORIGIN = "builtin"


# The most live workflows a pull may have made from one ComfyUI address. The
# per-pull budget bounds one pull; this bounds them all, since ComfyUI is
# written by whoever reaches it and every new path there is a new card here.
# At the cap a pull makes no new card; existing ones still take versions.
MAX_PULL_CARDS_PER_ORIGIN = 2000


def live_pull_cards(hub: HubDatabase, origin: str) -> int:
    """How many live workflows a pull made from *origin* (deleted ones not)."""
    return hub.fetchone(
        "SELECT COUNT(DISTINCT o.workflow_name) FROM workflow_origin o "
        "JOIN workflow_document d ON d.workflow_id = o.workflow_name "
        "WHERE o.origin = ? AND d.origin = 'pull'",
        (origin,),
    )[0]


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


def origin_rows(hub: HubDatabase, origin: str) -> dict[str, dict]:
    """``{remote_path: row}`` of every row *origin* has, one read for a pull.

    Each row carries ``workflow_name``, ``remote_modified``, ``content_hash``,
    ``dismissed``, ``gone_at`` and ``live``: whether the workflow it names
    still exists (always false for a row naming none, a built-in's copy).
    """
    return {
        row["remote_path"]: dict(row)
        for row in hub.fetchall(
            "SELECT o.remote_path, o.workflow_name, o.remote_modified, "
            "o.content_hash, o.dismissed, o.gone_at, "
            "d.workflow_id IS NOT NULL AS live FROM workflow_origin o "
            "LEFT JOIN workflow_document d ON d.workflow_id = o.workflow_name "
            "WHERE o.origin = ?",
            (origin,),
        )
    }


def live_file(hub: HubDatabase, origin: str, workflow_id: str) -> Optional[dict]:
    """The ComfyUI file at *origin* the live workflow *workflow_id* is linked to.

    A row that is neither dismissed nor gone. Where several paths name the
    workflow (a pull links a copy of a stored content to it), the one holding
    its current version wins, then the most recently modified, then the path.
    ``None`` when there is none. The row has ``remote_path``,
    ``remote_modified`` and ``content_hash``.

    **Only a workflow a pull made** (``origin`` ``pull``) has a ComfyUI file:
    a path whose content matched a card the owner made never versions it, so
    Open must not hand that card the file either. A row naming an unsafe path
    (stored before listings were checked) is never answered.
    """
    rows = hub.fetchall(
        "SELECT o.remote_path, o.remote_modified, o.content_hash "
        "FROM workflow_origin o "
        "JOIN workflow_document d ON d.workflow_id = o.workflow_name "
        "LEFT JOIN workflow_version v ON v.workflow_id = o.workflow_name "
        "AND v.version = (SELECT MAX(version) FROM workflow_version "
        "WHERE workflow_id = o.workflow_name) "
        "WHERE o.origin = ? AND o.workflow_name = ? AND o.dismissed = 0 "
        "AND o.gone_at IS NULL AND d.origin = 'pull' "
        "ORDER BY (v.content_hash IS NOT NULL AND v.content_hash = o.content_hash) "
        "DESC, o.remote_modified DESC, o.remote_path",
        (origin, workflow_id),
    )
    return next(
        (dict(row) for row in rows if is_safe_workflow_path(row["remote_path"])),
        None,
    )


def record_pulled(
    hub: HubDatabase,
    origin: str,
    remote_path: str,
    workflow_name: str,
    remote_modified: Optional[int],
    content_hash: Optional[str],
) -> None:
    """Remember that *remote_path* at *origin* is stored as *workflow_name*.

    *workflow_name* is the manual workflow's id (``None`` for a copy of a
    built-in, which is not stored). An upsert that keeps ``first_pulled_at``
    and never clears ``dismissed``.
    """
    with hub.transaction() as conn:
        upsert(conn, origin, remote_path, workflow_name, remote_modified, content_hash)


def upsert(
    conn,
    origin: str,
    remote_path: str,
    workflow_name: Optional[str],
    remote_modified: Optional[int],
    content_hash: Optional[str],
) -> None:
    """:func:`record_pulled`'s write, inside the caller's transaction.

    A path read again is listed, so a ``gone_at`` mark clears.
    """
    now = _now()
    conn.execute(
        "INSERT INTO workflow_origin (origin, remote_path, workflow_name, "
        "remote_modified, first_pulled_at, last_seen_at, content_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (origin, remote_path) DO UPDATE SET "
        "workflow_name = excluded.workflow_name, "
        "remote_modified = excluded.remote_modified, "
        "last_seen_at = excluded.last_seen_at, "
        "content_hash = excluded.content_hash, "
        "gone_at = NULL",
        (origin, remote_path, workflow_name, remote_modified, now, now, content_hash),
    )


def mark_gone(hub: HubDatabase, origin: str, listed: Iterable[str]) -> int:
    """Mark the rows for paths *origin* no longer lists, and clear the mark on
    the ones it lists again. Returns how many were newly marked.

    **Nothing is deleted**: the workflow a gone file was pulled as keeps its
    versions, and its row says the link is gone. Dismissed rows are left as
    they are. **An empty listing marks nothing**: an install that suddenly has
    no saved workflows (another user directory, a proxy answering 404, another
    ComfyUI on the port) is far more likely than one whose every workflow was
    deleted.
    """
    listed = set(listed)
    if not listed:
        return 0
    rows = hub.fetchall(
        "SELECT remote_path, gone_at FROM workflow_origin WHERE origin = ? "
        "AND dismissed = 0",
        (origin,),
    )
    gone = sorted(
        row["remote_path"]
        for row in rows
        if row["gone_at"] is None and row["remote_path"] not in listed
    )
    back = sorted(
        row["remote_path"]
        for row in rows
        if row["gone_at"] is not None and row["remote_path"] in listed
    )
    if not gone and not back:
        return 0
    now = _now()
    with hub.transaction() as conn:
        conn.executemany(
            "UPDATE workflow_origin SET gone_at = ? WHERE origin = ? "
            "AND remote_path = ?",
            [(now, origin, path) for path in gone],
        )
        conn.executemany(
            "UPDATE workflow_origin SET gone_at = NULL, last_seen_at = ? "
            "WHERE origin = ? AND remote_path = ?",
            [(now, origin, path) for path in back],
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


def stored_as(
    hub: HubDatabase, content_hash: Optional[str], pulled_only: bool = False
) -> Optional[str]:
    """The live manual workflow some origin already stored this content as.

    Any origin - a pull, the inbox, a built-in - and never a dismissed row, so
    a content the owner deleted is stored again when they hand it over again.
    The oldest wins, so two reads agree. ``None`` for no hash.

    Args:
        pulled_only: Only a workflow a pull made (``workflow_document.origin``
            ``pull``): what a pull may link a path to. A path linked to a card
            the owner made would version it on the file's next change.
    """
    if content_hash is None:
        return None
    row = hub.fetchone(
        "SELECT o.workflow_name FROM workflow_origin o "
        "JOIN workflow_document d ON d.workflow_id = o.workflow_name "
        "WHERE o.content_hash = ? AND o.dismissed = 0 "
        + ("AND d.origin = 'pull' " if pulled_only else "")
        + "ORDER BY o.first_pulled_at, o.origin, o.remote_path LIMIT 1",
        (content_hash,),
    )
    return row["workflow_name"] if row else None


def stored_at(
    hub: HubDatabase, origin: str, remote_path: str, pulled_only: bool = False
) -> Optional[str]:
    """The live manual workflow one origin's path is stored as, or ``None``.

    *pulled_only* as :func:`stored_as`: a link an earlier build made to a card
    the owner made is not followed.
    """
    row = hub.fetchone(
        "SELECT o.workflow_name FROM workflow_origin o "
        "JOIN workflow_document d ON d.workflow_id = o.workflow_name "
        "WHERE o.origin = ? AND o.remote_path = ?"
        + (" AND d.origin = 'pull'" if pulled_only else ""),
        (origin, remote_path),
    )
    return row["workflow_name"] if row else None
