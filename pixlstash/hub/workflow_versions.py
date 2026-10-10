"""The versions of a manual workflow's document.

A graph the owner saves over a workflow (a LoRA chain edit, say) is a new
**version** of it, not a new card, so its pictures, recipes, defaults and pins
stay where they are.

**An automatic workflow has versions too, once it is saved over.** It has no
``workflow_document`` row, so nothing here copies onto one: its version 1 is
the graph its pictures held (:func:`append_version`'s *first*), and its
current graph is read from its highest version (:func:`edited_document`). Version 1 is the document the card was
made with; the card's current graph is its highest version.

**``workflow_document`` holds a copy of the highest version** (``document`` and
``api_document``), written in the same transaction as the version row. Every
reader of a manual workflow's graph reads that row, as it always did, and an
older build sharing the hub still reads the current graph. Only this module
writes a later version, so the copy cannot drift from it.

A card an older build made has no version rows. Its first later version writes
version 1 from its document first (:func:`append_version`), and hub data step
12 (:func:`backfill_first_versions`) gives every card present then its version 1.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_inbox import content_hash as document_content_hash
from pixlstash.utils.workflow_ids import AUTO_PREFIX, stamp_workflow_id

logger = get_logger(__name__)

# The most versions one workflow keeps: version 1 and the newest
# ``MAX_VERSIONS - 1``, so history is bounded.
MAX_VERSIONS = 50

# ``source`` of an automatic workflow's version 1: the graph its pictures held
# when the owner first saved over it.
FROM_PICTURES = "pictures"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_of(workflow_id: str, document: str) -> Optional[str]:
    """``workflow_inbox.content_hash`` of a stored document, or ``None`` (logged)."""
    try:
        return document_content_hash(json.loads(document))
    except (ValueError, TypeError, AttributeError, RecursionError) as exc:
        # AttributeError: valid JSON that is not an object.
        logger.warning(
            "Manual workflow %s: its document will not hash, so its version "
            "records no content hash: %s",
            workflow_id,
            exc,
        )
        return None


def insert_version(
    conn: sqlite3.Connection,
    workflow_id: str,
    version: int,
    document: str,
    api_document: Optional[str],
    source: str,
    *,
    content_hash: Optional[str] = None,
    topology_hash: Optional[str] = None,
    remote_modified: Optional[int] = None,
    created_at: Optional[str] = None,
) -> None:
    """Write one version row, inside the caller's transaction.

    *document* and *api_document* are the serialised JSON as stored.
    """
    conn.execute(
        "INSERT INTO workflow_version (workflow_id, version, document, "
        "api_document, content_hash, topology_hash, remote_modified, source, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            workflow_id,
            version,
            document,
            api_document,
            content_hash
            if content_hash is not None
            else _hash_of(workflow_id, document),
            topology_hash,
            remote_modified,
            source,
            created_at or _now(),
        ),
    )


def _first_from_document(conn: sqlite3.Connection, workflow_id: str) -> bool:
    """Write version 1 from *workflow_id*'s stored document; ``False`` for no row."""
    row = conn.execute(
        "SELECT document, api_document, origin, created_at FROM workflow_document "
        "WHERE workflow_id = ?",
        (workflow_id,),
    ).fetchone()
    if row is None:
        return False
    insert_version(
        conn,
        workflow_id,
        1,
        row[0],
        row[1],
        row[2],
        created_at=row[3],
    )
    return True


def backfill_first_versions(conn: sqlite3.Connection) -> int:
    """Give every manual workflow without a version its version 1. Returns how many.

    Hub data step 12, inside its transaction. Idempotent: a workflow that has
    a version is left alone.
    """
    missing = [
        row[0]
        for row in conn.execute(
            "SELECT d.workflow_id FROM workflow_document d WHERE NOT EXISTS "
            "(SELECT 1 FROM workflow_version v WHERE v.workflow_id = d.workflow_id) "
            "ORDER BY d.workflow_id"
        ).fetchall()
    ]
    for workflow_id in missing:
        _first_from_document(conn, workflow_id)
    if missing:
        logger.info("Recorded version 1 of %d manual workflow(s).", len(missing))
    return len(missing)


def append_version(
    conn: sqlite3.Connection,
    workflow_id: str,
    document: dict,
    *,
    source: str,
    first: Optional[dict] = None,
) -> int:
    """Make *document* the next version of *workflow_id*, inside the caller's
    transaction, and return its number.

    *source* is how the version arrived: ``chain`` for a LoRA chain edit the
    owner saved over the workflow. (``pull`` on a stored row is a ComfyUI file
    the removed pull read.)

    *first* is version 1 of a workflow that has no document row to take it
    from: an automatic workflow's graph as read off its pictures, stored the
    first time the owner saves over it (``source`` :data:`FROM_PICTURES`), so
    what the edit replaced is kept like any other earlier version.

    Stamped with the workflow's id like every stored document, and copied onto
    ``workflow_document`` with its conversion cleared: the conversion was of the
    version before. Past :data:`MAX_VERSIONS`, the oldest versions after
    version 1 are deleted in the same transaction.

    Raises:
        LookupError: No manual workflow *workflow_id*.
    """
    latest = conn.execute(
        "SELECT MAX(version) FROM workflow_version WHERE workflow_id = ?",
        (workflow_id,),
    ).fetchone()[0]
    if latest is None:
        # Made by a build before versions: its document is version 1.
        if not _first_from_document(conn, workflow_id):
            if first is None:
                raise LookupError(f"No manual workflow {workflow_id}")
            insert_version(conn, workflow_id, 1, json.dumps(first), None, FROM_PICTURES)
        latest = 1
    stored = json.dumps(stamp_workflow_id(document, workflow_id))
    version = latest + 1
    insert_version(
        conn,
        workflow_id,
        version,
        stored,
        None,
        source,
    )
    conn.execute(
        "UPDATE workflow_document SET document = ?, api_document = NULL "
        "WHERE workflow_id = ?",
        (stored, workflow_id),
    )
    pruned = conn.execute(
        "DELETE FROM workflow_version WHERE workflow_id = ? AND version > 1 "
        "AND version <= ?",
        (workflow_id, version - (MAX_VERSIONS - 1)),
    ).rowcount
    logger.info(
        "Manual workflow %s is now at version %d (%s)%s.",
        workflow_id,
        version,
        source,
        f"; {pruned} older version(s) pruned" if pruned else "",
    )
    return version


def current_content_hash(hub: HubDatabase, workflow_id: str) -> Optional[str]:
    """The content hash of *workflow_id*'s current version.

    Hashed from its document for a workflow an older build made, which has
    no version row; ``None`` for no such workflow.
    """
    row = hub.fetchone(
        "SELECT content_hash FROM workflow_version WHERE workflow_id = ? "
        "ORDER BY version DESC LIMIT 1",
        (workflow_id,),
    )
    if row is not None and row[0] is not None:
        return row[0]
    document = hub.fetchone(
        "SELECT document FROM workflow_document WHERE workflow_id = ?",
        (workflow_id,),
    )
    return _hash_of(workflow_id, document[0]) if document is not None else None


def version_with_content(
    hub: HubDatabase, workflow_id: str, content_hash: str
) -> Optional[int]:
    """The version of *workflow_id* whose content is *content_hash*, or ``None``.

    What a picture made in ComfyUI records as the version it ran: an exact
    match only, never the nearest. A graph with edits no version holds, or one
    whose version was pruned, is ``None``. Two versions holding one content
    (an edit undone) answer the newer. A workflow an older build made has no
    version row; its document is version 1.
    """
    row = hub.fetchone(
        "SELECT MAX(version) FROM workflow_version WHERE workflow_id = ? "
        "AND content_hash = ?",
        (workflow_id, content_hash),
    )
    if row is not None and row[0] is not None:
        return row[0]
    if hub.fetchone(
        "SELECT 1 FROM workflow_version WHERE workflow_id = ? LIMIT 1", (workflow_id,)
    ):
        return None
    return 1 if current_content_hash(hub, workflow_id) == content_hash else None


def edited_document(
    hub: HubDatabase, workflow_id: str
) -> tuple[Optional[dict], Optional[int]]:
    """An automatic workflow's current graph and its version, or ``(None, None)``.

    The highest version the owner's overwrites made. ``(None, None)`` for a
    workflow never saved over, which is nearly every one. A row that will not
    parse is logged and answers ``(None, version)``: the caller decides what a
    workflow whose stored graph cannot be read runs.
    """
    row = hub.fetchone(
        "SELECT version, document FROM workflow_version WHERE workflow_id = ? "
        "ORDER BY version DESC LIMIT 1",
        (workflow_id,),
    )
    if row is None:
        return None, None
    try:
        document = json.loads(row["document"])
    except (ValueError, TypeError, RecursionError) as exc:
        logger.warning(
            "Workflow %s: version %d of its graph will not read: %s",
            workflow_id,
            row["version"],
            exc,
        )
        return None, row["version"]
    return (document if isinstance(document, dict) else None), row["version"]


def automatic_version_facts(hub: HubDatabase) -> dict[str, tuple[int, int, str]]:
    """``{workflow id: (versions kept, current version, stored at)}`` per
    automatic workflow the owner has saved over.

    What the grid shows as *Version N*, read in one statement. A manual
    workflow's are read with its document (``workflow_card_reads``).
    """
    return {
        row["workflow_id"]: (row["versions"], row["version"], row["created_at"])
        for row in hub.fetchall(
            "SELECT v.workflow_id, COUNT(*) AS versions, MAX(v.version) AS version, "
            "(SELECT l.created_at FROM workflow_version l "
            "WHERE l.workflow_id = v.workflow_id ORDER BY l.version DESC LIMIT 1) "
            "AS created_at FROM workflow_version v "
            "WHERE v.workflow_id LIKE ? GROUP BY v.workflow_id",
            (f"{AUTO_PREFIX}%",),
        )
    }
