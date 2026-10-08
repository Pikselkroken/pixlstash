"""The versions of a manual workflow's document.

A ComfyUI file whose content changed is a new **version** of the card its
``workflow_origin`` row names, not a new card, so its pictures, recipes,
defaults and pins stay where they are. Version 1 is the document the card was
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
from pixlstash.utils.workflow_ids import stamp_workflow_id

logger = get_logger(__name__)

# The most versions one workflow keeps: version 1 and the newest
# ``MAX_VERSIONS - 1``. A ComfyUI file is written by whoever reaches ComfyUI,
# and the poll takes every change, so history is bounded or the hub is not.
MAX_VERSIONS = 50


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
    content_hash: Optional[str] = None,
    topology_hash: Optional[str] = None,
    remote_modified: Optional[int] = None,
) -> int:
    """Make *document* the next version of *workflow_id*, inside the caller's
    transaction, and return its number.

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
            raise LookupError(f"No manual workflow {workflow_id}")
        latest = 1
    stored = json.dumps(stamp_workflow_id(document, workflow_id))
    version = latest + 1
    insert_version(
        conn,
        workflow_id,
        version,
        stored,
        None,
        "pull",
        content_hash=content_hash,
        topology_hash=topology_hash,
        remote_modified=remote_modified,
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
        "Manual workflow %s is now at version %d (pull)%s.",
        workflow_id,
        version,
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
