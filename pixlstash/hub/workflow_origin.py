"""Where a stored workflow came from, by origin and path (#1440).

Every way a workflow arrives that must not arrive twice records a row here,
and deduplicates on this table alone (:func:`stored_as`, :func:`stored_at`),
never on a folder:

* the watched inbox (``INBOX_ORIGIN``), keyed by content hash;
* a user-folder file data step 7 made a manual workflow (``FILE_ORIGIN``),
  keyed by its file name;
* a built-in made a manual workflow (``BUILTIN_ORIGIN``), keyed by file name.

``workflow_name`` names the manual workflow (``manual:<uuid>``) a document is
stored as.

**Rows under a ComfyUI address are left over from the pull** of ComfyUI's
saved workflows, which is gone (#1854). Nothing writes one or follows one to a
file any more. They still say that a content is already stored as a live
workflow, so an inbox drop of the same content matches it. Deleting the
workflow marks them ``dismissed`` rather than removing them
(``workflow_group_writes.delete_manual_workflow``), which every reader here
passes over and which keeps an older build sharing the hub, one that still
pulls, from bringing the workflow back.

**Callers hold ``workflow_inbox.INBOX_LOCK``** around a check and the write it
decides.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pixlstash.hub.db import HubDatabase

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert(
    conn,
    origin: str,
    remote_path: str,
    workflow_name: Optional[str],
    remote_modified: Optional[int],
    content_hash: Optional[str],
) -> None:
    """Remember that *remote_path* at *origin* is stored as *workflow_name*.

    Inside the caller's transaction. An upsert that keeps ``first_pulled_at``.
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
        "content_hash = excluded.content_hash",
        (origin, remote_path, workflow_name, remote_modified, now, now, content_hash),
    )


def stored_as(hub: HubDatabase, content_hash: Optional[str]) -> Optional[str]:
    """The live manual workflow some origin already stored this content as.

    Any origin, and never a dismissed row. The oldest wins, so two reads
    agree. ``None`` for no hash.
    """
    if content_hash is None:
        return None
    row = hub.fetchone(
        "SELECT o.workflow_name FROM workflow_origin o "
        "JOIN workflow_document d ON d.workflow_id = o.workflow_name "
        "WHERE o.content_hash = ? AND o.dismissed = 0 "
        "ORDER BY o.first_pulled_at, o.origin, o.remote_path LIMIT 1",
        (content_hash,),
    )
    return row["workflow_name"] if row else None


def stored_at(hub: HubDatabase, origin: str, remote_path: str) -> Optional[str]:
    """The live manual workflow one origin's path is stored as, or ``None``."""
    row = hub.fetchone(
        "SELECT o.workflow_name FROM workflow_origin o "
        "JOIN workflow_document d ON d.workflow_id = o.workflow_name "
        "WHERE o.origin = ? AND o.remote_path = ?",
        (origin, remote_path),
    )
    return row["workflow_name"] if row else None
