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
from pixlstash.utils.sql_chunking import chunked
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

# Ghost rows per INSERT batch. Each carries a thumbnail BLOB, so this bounds
# peak memory on a purge of an entire scrapheap rather than materialising every
# retained thumbnail at once. Membership tests use ``sql_chunking.chunked``'s
# own default, which is sized against SQLite's bound-parameter cap.
_GHOST_WRITE_CHUNK = 100


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


# ---------------------------------------------------------------------------
# Picture ghosts — the thumbnail and prompt a destroyed picture leaves behind
# ---------------------------------------------------------------------------
#
# Storage only, and **scoped to one library on every call**. Whether a ghost may
# exist at all is decided by ``services/workflow_ghost_service.py`` against the
# user's retention setting, and nothing here consults it: a store that quietly
# declined a write would put the consent decision in two places, and the one
# that is easy to forget is the one that keeps data.


@dataclass(frozen=True)
class PictureGhost:
    """One destroyed picture's retained trace, within one library.

    ``pixel_sha`` identifies the picture: the vault row is gone by the time this
    is written and SQLite reuses its id on the next import, so nothing else
    does. It is the key only *with* ``library_uuid`` — see the table comment in
    ``hub/schema.py`` for why a hub-global key would be wrong.

    ``thumbnail`` and ``positive_prompt`` are the ghost proper and are BOTH
    required: a ghost is the thumbnail and the prompt, never one without the
    other, so the caller refuses to build one rather than dropping a half.
    """

    library_uuid: str
    pixel_sha: str
    instance_hash: str
    thumbnail: bytes
    structural_hash: Optional[str] = None
    positive_prompt: Optional[str] = None
    seed: Optional[int] = None


def record_picture_ghosts(hub: HubDatabase, ghosts: list[PictureGhost]) -> int:
    """Write retained ghosts, replacing any earlier trace of the same picture.

    ``INSERT OR REPLACE`` rather than ``INSERT OR IGNORE``: the same
    ``(library_uuid, pixel_sha)`` recurring means the file was re-imported into
    that library and destroyed again, and the newer trace is the truthful one.

    Written in chunks so a purge of a whole scrapheap never holds every retained
    thumbnail in memory at once, and returns the number of ROWS written — the
    caller's list is de-duplicated on the key first, because two pictures with
    the same pixel_sha are the same bytes and collapse to one row.
    """
    unique = {(ghost.library_uuid, ghost.pixel_sha): ghost for ghost in ghosts}
    rows = list(unique.values())
    for batch in chunked(rows, _GHOST_WRITE_CHUNK):
        now = _now()
        with hub.transaction() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO workflow_picture_ghost "
                "(library_uuid, pixel_sha, instance_hash, structural_hash, "
                " positive_prompt, seed, thumbnail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        ghost.library_uuid,
                        ghost.pixel_sha,
                        ghost.instance_hash,
                        ghost.structural_hash,
                        ghost.positive_prompt,
                        ghost.seed,
                        ghost.thumbnail,
                        now,
                    )
                    for ghost in batch
                ],
            )
    return len(rows)


def destroy_ghosts_for_instances(
    hub: HubDatabase, library_uuid: str, instance_hashes: list[str]
) -> int:
    """Destroy this library's ghosts leaning on the named instance hashes.

    This is the **covered-ghost cascade**: the caller has established that no
    surviving picture in THIS library carries these hashes any more, so the
    ghosts that were kept because one did are no longer covered and must go.
    Another library's ghosts are never touched — its cover lives in a vault this
    process cannot see.
    """
    if not library_uuid or not instance_hashes:
        return 0
    removed = 0
    with hub.transaction() as conn:
        for batch in chunked(instance_hashes):
            placeholders = ",".join("?" for _ in batch)
            cursor = conn.execute(
                "DELETE FROM workflow_picture_ghost WHERE library_uuid = ? "
                f"AND instance_hash IN ({placeholders})",
                (library_uuid, *batch),
            )
            removed += cursor.rowcount or 0
    if removed:
        logger.info(
            "Covered-ghost cascade: destroyed %d ghost(s) whose last covering "
            "picture was purged.",
            removed,
        )
    return removed
