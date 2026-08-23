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
    assets_from_reduction,
    document_from_reduction,
    drop_widgets,
    graph_key,
    promote_instance_widgets,
    reduce_api_graph,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class WorkflowKeys:
    """What a vault records to point at a hub-side workflow.

    ``topology_hash`` and ``structural_hash`` are the two HUB-side content
    addresses. A vault stores them as plain text and resolves them against
    whatever hub is attached, so a library that moves machines still finds its
    recipes if that machine has them, and reports them as unknown if it does
    not.

    ``instance_hash`` is the third tier and is **vault-only**. It is returned
    from here because it falls out of the same reduction and re-walking the
    graph to get it would be the one cost this module exists to avoid, but
    nothing hub-side stores it: an instance carries the prompt and every
    parameter, and a hub-side ``recipe_instance`` table is Phase 2 work that
    moved to v1.12. Two pictures share an instance exactly when they share this
    string, which is all v1.11 asks.

    The document's own digest is deliberately absent: it is an implementation
    detail of the store (the same workflow rebuilt from scratch has different
    node ids and so a different document, with the same identity), and nothing
    outside this module should key on it.
    """

    topology_hash: str
    structural_hash: str
    instance_hash: str
    node_count: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_document(document: dict) -> str:
    """Render a structural document the one way, so its digest is stable."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def record_api_graph(hub: HubDatabase, api_graph: dict) -> WorkflowKeys:
    """File one API-format graph, returning the keys a vault should store.

    All three tiers are computed from the same reduction, so the topology row
    and the recipe row can never disagree about which graph they describe, and
    the instance key the caller stores in the vault describes that same graph.
    Only the first two are written here; see :class:`WorkflowKeys`.

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
        instance_hash=graph_key(promote_instance_widgets(nodes)),
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
        # The readable asset names, which the document above deliberately does
        # NOT carry. INSERT OR IGNORE like the rest, so re-filing one graph is
        # a no-op -- and note that a row deleted to forget a model name is not
        # resurrected by re-filing a DIFFERENT recipe, only by re-filing this
        # one, because the key includes the structural hash.
        conn.executemany(
            "INSERT OR IGNORE INTO workflow_recipe_asset "
            "(structural_hash, widget_name, normalized_filename) VALUES (?, ?, ?)",
            [
                (keys.structural_hash, widget_name, filename)
                for widget_name, filename in assets_from_reduction(nodes)
            ],
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


def assets_for_recipe(hub: HubDatabase, structural_hash: str) -> list[sqlite3.Row]:
    """The readable asset names for one recipe, or empty if they were forgotten.

    Empty is a legitimate answer, not a missing row: forgetting a model name is
    a delete here, and the stored document keeps working with its references
    unresolved.
    """
    return hub.fetchall(
        "SELECT widget_name, normalized_filename FROM workflow_recipe_asset "
        "WHERE structural_hash = ? ORDER BY widget_name, normalized_filename",
        (structural_hash,),
    )


def forget_asset_names(hub: HubDatabase, normalized_filename: str) -> int:
    """Destroy one model's readable name everywhere it is recorded.

    Returns the number of rows removed. **No stored graph is rewritten and no
    ``document_sha256`` is invalidated** -- the documents refer to the asset by
    an opaque reference, so what is lost is exactly the ability to say which
    model it was, which is what the caller asked for.
    """
    with hub.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM workflow_recipe_asset WHERE normalized_filename = ?",
            (normalized_filename,),
        )
        removed = cursor.rowcount or 0
    logger.info(
        "Forgot the readable name of a workflow asset from %s recipe row(s).",
        removed,
    )
    return removed


def recipes_for_topology(hub: HubDatabase, topology_hash: str) -> list[sqlite3.Row]:
    """Every recipe filed under one topology — the library view's expand."""
    return hub.fetchall(
        "SELECT structural_hash, hash_version, node_count, first_seen_at "
        "FROM workflow_recipe WHERE topology_hash = ? ORDER BY first_seen_at",
        (topology_hash,),
    )


def topology_index(hub: HubDatabase) -> list[sqlite3.Row]:
    """Every topology with the number of recipes filed under it.

    The Workflows view's whole list in one query. A LEFT JOIN rather than a
    subquery per row: 192 topologies over 617 recipes is small, and a topology
    with no recipe is impossible today but reads as zero rather than vanishing
    if one ever appears.
    """
    return hub.fetchall(
        "SELECT t.topology_hash, t.hash_version, t.node_count, t.first_seen_at, "
        "COUNT(r.structural_hash) AS variant_count "
        "FROM workflow_topology t "
        "LEFT JOIN workflow_recipe r ON r.topology_hash = t.topology_hash "
        "GROUP BY t.topology_hash"
    )


def assets_by_topology(hub: HubDatabase) -> dict[str, list[sqlite3.Row]]:
    """The readable asset names of every recipe, keyed by its topology.

    One query for the whole list rather than one per row, for the same reason
    :func:`topology_index` is one query. Names deleted by
    :func:`forget_asset_names` are simply absent, which is what lets the view
    say a workflow's models are no longer named rather than showing a blank.

    **DISTINCT, and it is load-bearing rather than tidy.** The asset table is
    keyed per RECIPE, so a topology's 159 variants naming the same checkpoint
    contribute 159 identical rows -- which the list would render as one row's
    Models cell and a caller counting them would read as 159 adapters. What a
    topology names is the SET of files its variants reach for, so that is what
    this returns.
    """
    rows = hub.fetchall(
        "SELECT DISTINCT r.topology_hash, a.widget_name, a.normalized_filename "
        "FROM workflow_recipe_asset a "
        "JOIN workflow_recipe r ON r.structural_hash = a.structural_hash "
        "ORDER BY a.widget_name, a.normalized_filename"
    )
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["topology_hash"], []).append(row)
    return grouped


def adapter_slots_by_topology(hub: HubDatabase) -> dict[str, int]:
    """How many adapters ONE run of each topology loads.

    The set of filenames a topology names cannot answer this: a family of 159
    character LoRAs is 159 names and one slot, and a row built from the set
    would describe itself as loading all of them at once.

    **The max over a topology's recipes is exact, not an estimate.** A topology
    is the graph alone -- node classes and named-input edges -- so every recipe
    filed under one has the same number of ``lora_name`` inputs by construction.
    The max is taken rather than any single recipe's count only because a recipe
    whose names were forgotten contributes zero rows and must not drag the
    answer down with it.

    Returns:
        ``{topology_hash: slots}``, with topologies whose recipes name no
        adapter absent entirely rather than present with a zero.
    """
    rows = hub.fetchall(
        "SELECT topology_hash, MAX(slots) AS slots FROM ("
        "  SELECT r.topology_hash AS topology_hash, "
        "         COUNT(DISTINCT a.normalized_filename) AS slots "
        "  FROM workflow_recipe_asset a "
        "  JOIN workflow_recipe r ON r.structural_hash = a.structural_hash "
        "  WHERE a.widget_name LIKE '%lora%' "
        "  GROUP BY r.topology_hash, a.structural_hash"
        ") GROUP BY topology_hash"
    )
    return {row["topology_hash"]: row["slots"] for row in rows}


def assets_for_topology_recipes(
    hub: HubDatabase, topology_hash: str
) -> dict[str, list[sqlite3.Row]]:
    """Every recipe's asset names under one topology, keyed by recipe.

    One query, not one per recipe. The row this whole shape exists for holds
    159 variants, and calling :func:`assets_for_recipe` in a loop over it is the
    161-query expand that :func:`assets_by_topology` was written to avoid on the
    list beside it.
    """
    rows = hub.fetchall(
        "SELECT a.structural_hash, a.widget_name, a.normalized_filename "
        "FROM workflow_recipe_asset a "
        "JOIN workflow_recipe r ON r.structural_hash = a.structural_hash "
        "WHERE r.topology_hash = ? "
        "ORDER BY a.widget_name, a.normalized_filename",
        (topology_hash,),
    )
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["structural_hash"], []).append(row)
    return grouped


def topology_exists(hub: HubDatabase, topology_hash: str) -> bool:
    """Whether this hub has heard of a topology at all."""
    return (
        hub.fetchone(
            "SELECT 1 FROM workflow_topology WHERE topology_hash = ?",
            (topology_hash,),
        )
        is not None
    )


def recipe_exists(hub: HubDatabase, structural_hash: str) -> bool:
    """Whether this hub has heard of a recipe at all.

    Asked separately from :func:`get_document` so an unreadable stored document
    is not reported as an unknown workflow: the first is a fault worth a 500 and
    a log line, the second is the ordinary answer for a hash from another
    machine.
    """
    return (
        hub.fetchone(
            "SELECT 1 FROM workflow_recipe WHERE structural_hash = ?",
            (structural_hash,),
        )
        is not None
    )
