"""The workflow library's hub-side store: topologies, recipes and documents.

Thin by design. Everything interesting about a workflow's identity happens in
:mod:`pixlstash.services.workflow_hash`; this module only puts the answer
somewhere it outlives the pictures it came from, which is the whole point of
the library plan (§2: today the executable graph lives in exactly one place,
the image file, so every cleanup feature destroys workflow knowledge as a side
effect of reclaiming space).

Writes are **idempotent and content-addressed**. The same graph seen in a
thousand images inserts three rows once and then does nothing, which is what
lets the backfill be re-run without a reconciliation pass, and what lets one
recipe be shared by three libraries without any of them owning it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_hash import (
    HASH_VERSION,
    document_from_reduction,
    drop_widgets,
    graph_key,
    reduce_api_graph,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class WorkflowKeys:
    """What a vault records to point at a hub-side workflow.

    Both hashes are content addresses. A vault stores them as plain text and
    resolves them against whatever hub is attached, so a library that moves
    machines still finds its recipes if that machine has them, and reports them
    as unknown if it does not.

    The document's own digest is deliberately absent: it is an implementation
    detail of the store (the same workflow rebuilt from scratch has different
    node ids and so a different document, with the same identity), and nothing
    outside this module should key on it.
    """

    topology_hash: str
    structural_hash: str
    node_count: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_document(document: dict) -> str:
    """Render a structural document the one way, so its digest is stable."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def record_api_graph(hub: HubDatabase, api_graph: dict) -> WorkflowKeys:
    """File one API-format graph, returning the keys a vault should store.

    Both tiers are computed from the same reduction, so the topology row and
    the recipe row can never disagree about which graph they describe.

    Raises:
        pixlstash.services.workflow_hash.WorkflowGraphError: The graph holds
            nothing keyable. Callers ingesting arbitrary images are expected to
            catch this and skip the picture rather than fail the batch.
    """
    nodes = reduce_api_graph(api_graph)
    document = _canonical_document(document_from_reduction(nodes))
    keys = WorkflowKeys(
        topology_hash=graph_key(drop_widgets(nodes)),
        structural_hash=graph_key(nodes),
        node_count=len(nodes),
    )

    now = _now()
    with hub.transaction() as conn:
        # INSERT OR IGNORE rather than check-then-write: the row is keyed by
        # its own content, so a concurrent writer racing us is writing the
        # identical row and the loser has nothing to correct.
        conn.execute(
            "INSERT OR IGNORE INTO workflow_topology "
            "(topology_hash, hash_version, node_count, first_seen_at) "
            "VALUES (?, ?, ?, ?)",
            (keys.topology_hash, HASH_VERSION, keys.node_count, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO workflow_recipe "
            "(structural_hash, topology_hash, hash_version, node_count, first_seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                keys.structural_hash,
                keys.topology_hash,
                HASH_VERSION,
                keys.node_count,
                now,
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO workflow_recipe_graph "
            "(structural_hash, document_sha256, document, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                keys.structural_hash,
                hashlib.sha256(document.encode("utf-8")).hexdigest(),
                document,
                now,
            ),
        )
    return keys


def get_document(hub: HubDatabase, structural_hash: str) -> Optional[dict]:
    """Return the stored structural graph for a recipe, or None if unknown."""
    row = hub.fetchone(
        "SELECT document FROM workflow_recipe_graph WHERE structural_hash = ?",
        (structural_hash,),
    )
    if row is None:
        return None
    try:
        return json.loads(row["document"])
    except json.JSONDecodeError as exc:
        logger.error(
            "Stored workflow document for recipe %s is not valid JSON: %s",
            structural_hash,
            exc,
        )
        return None


def recipes_for_topology(hub: HubDatabase, topology_hash: str) -> list[sqlite3.Row]:
    """Every recipe filed under one topology — the library view's expand."""
    return hub.fetchall(
        "SELECT structural_hash, hash_version, node_count, first_seen_at "
        "FROM workflow_recipe WHERE topology_hash = ? ORDER BY first_seen_at",
        (topology_hash,),
    )
