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
from typing import Callable, Optional

from pixlstash.hub import workflow_cards
from pixlstash.hub.db import HubDatabase
from pixlstash.services.model_folder_scanner import MODEL_SUFFIX as SHELF_MODEL_SUFFIX
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.sql_chunking import chunked
from pixlstash.services.workflow_hash import (
    DIGEST_PREFIX_RE,
    HASH_VERSION,
    MODEL_EXTENSIONS,
    ReducedNode,
    SHA256_FIELD_RE,
    asset_reference,
    assets_from_reduction,
    digests_with_prefix,
    document_from_reduction,
    drop_widgets,
    graph_key,
    instance_document_from_reduction,
    normalized_filename,
    promote_instance_widgets,
    reduce_api_graph,
    reduce_ui_graph,
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

    ``instance_hash`` is the third tier. It falls out of the same reduction, so
    it is returned from here rather than re-walked for; its row is per library
    (``workflow_recipe_instance``), because an instance is the prompt.

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


def record_api_graph(
    hub: HubDatabase,
    api_graph: dict,
    library_uuid: Optional[str] = None,
    *,
    revisit: bool = False,
) -> WorkflowKeys:
    """File one API-format graph, returning the keys a vault should store.

    All three tiers are computed from the same reduction, so the topology row,
    the recipe row and the instance row can never disagree about which graph
    they describe.

    Args:
        library_uuid: The library the picture is in. The instance row is written
            only with one, because a row that cannot name its library can never
            be cascaded when its pictures go.
        revisit: The picture was filed before. Its recipe's asset names are then
            written only if the recipe itself is new to this hub: a name missing
            under a recipe the hub already holds was forgotten on purpose, and a
            backfill re-reading the library must not bring it back.

    Raises:
        pixlstash.services.workflow_hash.WorkflowGraphError: The graph holds
            nothing keyable. Callers ingesting arbitrary images are expected to
            catch this and skip the picture rather than fail the batch.
    """
    return record_reduction(
        hub, reduce_api_graph(api_graph), library_uuid, revisit=revisit
    )


def record_reduction(
    hub: HubDatabase,
    nodes: dict[str, ReducedNode],
    library_uuid: Optional[str] = None,
    *,
    revisit: bool = False,
) -> WorkflowKeys:
    """File an already-reduced graph; :func:`record_api_graph` for the rest.

    The entry for a recipe that was never a ComfyUI graph: A1111 generation
    data is classified into a reduction directly (``services/a1111_recipe.py``).
    """
    document = _canonical_document(document_from_reduction(nodes))
    instance_document = _canonical_document(instance_document_from_reduction(nodes))
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
        new_recipe = conn.execute(
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
        ).rowcount
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
        # one, because the key includes the structural hash -- and never by a
        # revisit (see ``revisit``).
        if new_recipe or not revisit:
            conn.executemany(
                "INSERT OR IGNORE INTO workflow_recipe_asset "
                "(structural_hash, widget_name, normalized_filename) "
                "VALUES (?, ?, ?)",
                [
                    (keys.structural_hash, widget_name, filename)
                    for widget_name, filename in assets_from_reduction(nodes)
                ],
            )
        if library_uuid:
            conn.execute(
                "INSERT OR IGNORE INTO workflow_recipe_instance "
                "(library_uuid, instance_hash, structural_hash, hash_version, "
                " document, first_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    library_uuid,
                    keys.instance_hash,
                    keys.structural_hash,
                    HASH_VERSION,
                    instance_document,
                    now,
                ),
            )
    # The card, from the rows just written. Here and not at the three callers -
    # the import route, the watched inbox and the extraction task - because
    # every one of them files a graph through this function, and a hook per
    # caller is a hook the next caller forgets. Outside the transaction above:
    # `hub.transaction` is not re-entrant, and this one is a whole transaction
    # of its own.
    #
    # **Only for a recipe this hub had not seen**, which is the difference
    # between a card derived once and a four-table join run once per PICTURE:
    # a library is thousands of pictures over a handful of recipes, and the
    # thousandth filing of one recipe has nothing to add. A recipe already here
    # whose card is missing (filed by a build before this one, or refused at the
    # time) is the backfill finder's, which sweeps for exactly that.
    #
    # Never fatal to the filing. A graph that will not reduce into a card is
    # still a filed recipe, and the backfill finder retries it, so the picture's
    # own ingest is not failed over a card it can do without.
    if new_recipe:
        try:
            workflow_cards.record_identity(hub, keys.structural_hash)
        except Exception as exc:
            # Deliberately everything, with the type logged. The derivation
            # indexes into a parsed document, so a malformed one raises
            # KeyError or TypeError rather than WorkflowGraphError, and this
            # runs inside the extraction task's ingest of a picture: a card is
            # secondary to the filing, exactly as it is on the import route
            # (`routes/comfyui.py._file_in_hub`), and the backfill finder is
            # what retries it.
            logger.warning(
                "Recipe %s is filed but has no workflow card yet, deriving it "
                "failed with %s: %s",
                keys.structural_hash,
                type(exc).__name__,
                exc,
            )
    return keys


def record_ui_graph(hub: HubDatabase, workflow: dict) -> str:
    """File a UI-format workflow's topology, returning its hash.

    Topology only: a UI file names its widget values by position, so it has no
    recipe until ``object_info`` can name them. The row is the same one an API
    graph of that workflow files, so a dropped ``workflow.json`` lands on the
    Workflows view row its pictures already made.

    Raises:
        pixlstash.services.workflow_hash.WorkflowGraphError: Nothing keyable.
    """
    nodes = reduce_ui_graph(workflow)
    topology = graph_key(nodes)
    with hub.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO workflow_topology "
            "(topology_hash, hash_version, node_count, first_seen_at) "
            "VALUES (?, ?, ?, ?)",
            (topology, HASH_VERSION, len(nodes), _now()),
        )
    return topology


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


def _shelf_model_names(fetchall: Callable[[str], list]) -> set[str]:
    """Every model filename the shelf holds, normalized (rule 5).

    Both the recorded filename and every copy's basename, because a copy
    renamed on disk is the same model. A tombstoned row counts: the shelf still
    lists it, and re-adding its folder re-links it.
    """
    names = {
        normalized_filename(row[0])
        for row in fetchall("SELECT filename FROM model WHERE filename IS NOT NULL")
    }
    names.update(
        normalized_filename(row[0])
        for row in fetchall("SELECT relpath FROM model_file")
    )
    return names


def _shelf_model_digests(fetchall: Callable[[str], list]) -> list[str]:
    """Every digest the shelf holds, lowercased and sorted for a prefix search."""
    return sorted(
        row[0].lower()
        for row in fetchall("SELECT sha256 FROM model WHERE sha256 IS NOT NULL")
    )


def shelf_model_names(hub: HubDatabase) -> set[str]:
    """Every model filename the shelf holds, normalized (rule 5).

    Public for the recipe export, which asks a narrower question than
    :func:`unvouched_model_values`: a saved recipe's ``filename`` is a model
    name by construction, so it needs no extension test to be judged - and the
    extension test is exactly what would let one saved without its suffix be
    exported with no warning beside it.
    """
    return _shelf_model_names(hub.fetchall)


def unvouched_model_values(hub: HubDatabase) -> Callable[[str, str], bool]:
    """Ask, of one widget's value, whether this machine can vouch for the model.

    The question an **export** has to ask before it writes a filename into a
    file somebody else will read. It is deliberately stricter than
    :func:`model_ghost_names`, and for the opposite reason: a ghost is judged
    in order to destroy a name forever, so an unjudgeable one has to be kept,
    while here a wrong "vouched for" publishes a name the owner asked PixlStash
    to forget and a wrong "unvouched" only leaves a widget blank in a file they
    are giving away.

    So the rule is *not on the shelf, not published*:

    * a value :func:`model_ghost_names` already calls a ghost;
    * any filename with a model extension that no shelf model is called -
      which is what catches a **forgotten** name. Forgetting deletes the
      ``workflow_recipe_asset`` rows, so the ghost set cannot see it any more,
      but a picture's embedded graph still says it in full;
    * a ``*_sha256`` widget holding a digest no shelf model's digest starts
      with. Unlike the ghost screen this does not wait for every shelf model to
      be hashed: a digest names a model on a public registry as surely as a
      filename does, and the cost of being early here is a blank widget.

    Returns:
        ``unvouched(widget_name, value) -> bool``, closed over three reads of
        the hub so a whole graph costs those three and no more.
    """
    ghosts = model_ghost_names(hub)
    shelf_names = _shelf_model_names(hub.fetchall)
    shelf_digests = _shelf_model_digests(hub.fetchall)

    def unvouched(widget_name: str, value: str) -> bool:
        if not isinstance(value, str) or not value:
            return False
        normalized = normalized_filename(value)
        if value in ghosts or normalized in ghosts:
            return True
        if SHA256_FIELD_RE.search(widget_name or ""):
            return bool(DIGEST_PREFIX_RE.match(value)) and not digests_with_prefix(
                value, shelf_digests
            )
        return normalized.endswith(MODEL_EXTENSIONS) and normalized not in shelf_names

    return unvouched


def _model_ghost_names(fetchall: Callable[[str], list]) -> set[str]:
    """See :func:`model_ghost_names`; ``fetchall`` runs one read and returns rows.

    A model is on the shelf while it has a row at all, tombstone included:
    removing a folder keeps the ``model`` row so re-adding the folder re-links
    it, and the shelf still lists it. Both the recorded filename and every
    copy's basename count, because a copy renamed on disk is the same model.

    **Digests are judged only when every shelf model has one.** A checkpoint
    waits for ``MissingCheckpointHashFinder`` with ``sha256`` NULL, and until it
    is read its loader digest matches nothing, so judging then would forget the
    digest of a model still on disk. ``engine`` rows never carry a digest and
    are left out of that wait. A value that is not a digest or an A1111 short
    hash of one (an unset loader, a download node's ``expected_sha256`` left
    blank) names no model and is never a ghost; a short hash is a ghost when no
    shelf digest starts with it.
    """
    shelf_names = _shelf_model_names(fetchall)
    shelf_digests = _shelf_model_digests(fetchall)
    judge_digests = not fetchall(
        "SELECT 1 FROM model WHERE sha256 IS NULL AND file_kind <> 'engine' LIMIT 1"
    )
    ghosts = set()
    for widget, value in fetchall(
        "SELECT DISTINCT widget_name, normalized_filename FROM workflow_recipe_asset"
    ):
        if SHA256_FIELD_RE.search(widget):
            if (
                judge_digests
                and DIGEST_PREFIX_RE.match(value)
                and not digests_with_prefix(value, shelf_digests)
            ):
                ghosts.add(value)
        elif value.endswith(SHELF_MODEL_SUFFIX) and value not in shelf_names:
            ghosts.add(value)
    return ghosts


def model_ghost_names(hub: HubDatabase) -> set[str]:
    """What recipes keep that names a model the shelf does not hold.

    These are **model ghosts**: a ``.safetensors`` filename matching no model on
    the shelf, or a ``*_sha256`` value no model's digest equals or, for an A1111
    short hash, starts with (a digest identifies a model on a public registry as
    surely as its name does).
    Only what the shelf can hold is judged: it scans ``.safetensors`` alone, so
    a ``.ckpt`` or ``.gguf`` is never on it and calling one a ghost would forget
    the name of a model still on disk. A model that was never added to the shelf
    counts — a picture made elsewhere names one — and the Privacy copy says so.
    Hub-wide, like the recipes: a model name is not a fact about one library.
    """
    return _model_ghost_names(hub.fetchall)


def forget_model_ghosts(
    hub: HubDatabase, expected: Optional[int] = None
) -> Optional[int]:
    """Forget every model ghost. Returns how many values were forgotten.

    The set is re-read inside the write. ``expected`` is the count the person
    confirmed: when the set has changed size since (an import filed new names,
    or a model came back to the shelf), nothing is forgotten and ``None`` is
    returned, so a confirm never destroys more than it showed. Each forget is a
    row delete: no document is rewritten and no hash moves, so the workflow
    still groups and reads as models whose names are forgotten.
    """
    with hub.transaction() as conn:
        names = sorted(_model_ghost_names(lambda sql: conn.execute(sql).fetchall()))
        if expected is not None and expected != len(names):
            logger.info(
                "Model-ghost forget refused: %d confirmed, %d now.",
                expected,
                len(names),
            )
            return None
        for batch in chunked(names):
            placeholders = ",".join("?" for _ in batch)
            conn.execute(
                "DELETE FROM workflow_recipe_asset "
                f"WHERE normalized_filename IN ({placeholders})",
                tuple(batch),
            )
    logger.info("Forgot %d model name(s) or digest(s) not on the shelf.", len(names))
    return len(names)


def forgotten_asset_counts(
    hub: HubDatabase, topology_hash: Optional[str] = None
) -> dict[str, dict[str, int]]:
    """How many of each recipe's assets no longer have a readable name.

    The stored document names every asset by :func:`asset_reference`, so a
    reference with no ``workflow_recipe_asset`` row behind it is exactly a name
    that was forgotten. That is what lets a row say "3 models, names forgotten"
    rather than going blank.

    Args:
        topology_hash: Read only this topology's documents (a row's expand)
            rather than the whole hub.

    Returns:
        ``{topology_hash: {structural_hash: count}}``, recipes with nothing
        forgotten absent.
    """
    # ponytail: the list re-reads every document per call (~600 on a large
    # library, one json_each pass in C); cache per hub write if it ever shows.
    known: dict[str, set[str]] = {}
    scope = "WHERE r.topology_hash = ?" if topology_hash else ""
    params = (topology_hash,) if topology_hash else ()
    for row in hub.fetchall(
        "SELECT a.structural_hash, a.normalized_filename FROM workflow_recipe_asset a "
        f"JOIN workflow_recipe r ON r.structural_hash = a.structural_hash {scope}",
        params,
    ):
        known.setdefault(row["structural_hash"], set()).add(
            asset_reference(row["normalized_filename"])
        )
    # json_valid first: one corrupt document must not take the whole list down.
    rows = hub.fetchall(
        "SELECT DISTINCT r.topology_hash, g.structural_hash, i.value AS reference "
        "FROM workflow_recipe_graph g "
        "JOIN workflow_recipe r ON r.structural_hash = g.structural_hash, "
        "     json_each(g.document) n, json_each(n.value, '$.inputs') i "
        "WHERE json_valid(g.document) AND n.type = 'object' "
        "  AND json_type(n.value, '$.inputs') = 'object' "
        "  AND i.type = 'text' AND i.value LIKE 'asset:%'"
        + (" AND r.topology_hash = ?" if topology_hash else ""),
        params,
    )
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        recipe = row["structural_hash"]
        if row["reference"] in known.get(recipe, ()):
            continue
        per_recipe = counts.setdefault(row["topology_hash"], {})
        per_recipe[recipe] = per_recipe.get(recipe, 0) + 1
    return counts


def filed_instance_hashes(
    hub: HubDatabase, library_uuid: str, instance_hashes: list[str]
) -> set[str]:
    """Which of these instance hashes this library has an instance row for.

    The row holds the parameters a remake needs, and its recipe is filed in the
    same transaction, so a hash without one cannot be made again from the hub.
    """
    filed: set[str] = set()
    for batch in chunked(sorted(set(instance_hashes))):
        placeholders = ",".join("?" for _ in batch)
        filed.update(
            row["instance_hash"]
            for row in hub.fetchall(
                "SELECT instance_hash FROM workflow_recipe_instance "
                f"WHERE library_uuid = ? AND instance_hash IN ({placeholders})",
                (library_uuid, *batch),
            )
        )
    return filed


def recipes_missing_a_model(hub: HubDatabase, structural_hashes: list[str]) -> set[str]:
    """Which of these recipes name a model the shelf cannot vouch for.

    A model ghost (:func:`model_ghost_names`) or a name that was forgotten
    (:func:`forgotten_asset_counts`) both mean the recipe cannot load what it
    needs, so neither can be made again as it is.

    **A model the shelf cannot judge counts as missing here, which is the
    opposite of what the ghost screen does, deliberately.** The shelf scans
    ``.safetensors`` alone, so :func:`model_ghost_names` will not call a
    ``.ckpt``, ``.gguf`` or ``.pt`` a ghost: forgetting the name of a model
    still on disk would be the damage there. The question here is whether to
    *delete a picture*, and an unjudgeable name answers "we do not know", which
    on a destructive action has to fail toward keeping the picture.

    One blind spot stays, and it is the ghost screen's: ``_model_ghost_names``
    suspends digest judgement entirely while any shelf model still has a NULL
    ``sha256`` (``judge_digests``), so on a library whose checkpoint hashing has
    not finished a recipe identified only by a digest is not flagged. It is a
    window rather than a rule, and it closes itself when the hashing does.
    """
    wanted = set(structural_hashes)
    if not wanted:
        return set()
    ghosts = model_ghost_names(hub)
    unjudgeable = tuple(ext for ext in MODEL_EXTENSIONS if ext != SHELF_MODEL_SUFFIX)
    missing: set[str] = set()
    for batch in chunked(sorted(wanted)):
        placeholders = ",".join("?" for _ in batch)
        missing.update(
            row["structural_hash"]
            for row in hub.fetchall(
                "SELECT structural_hash, normalized_filename FROM workflow_recipe_asset "
                f"WHERE structural_hash IN ({placeholders})",
                tuple(batch),
            )
            if row["normalized_filename"] in ghosts
            or row["normalized_filename"].endswith(unjudgeable)
        )
    for per_recipe in forgotten_asset_counts(hub).values():
        missing.update(wanted.intersection(per_recipe))
    return missing


def recipes_for_topology(hub: HubDatabase, topology_hash: str) -> list[sqlite3.Row]:
    """Every recipe filed under one topology - the library view's expand."""
    return hub.fetchall(
        "SELECT structural_hash, hash_version, node_count, first_seen_at "
        "FROM workflow_recipe WHERE topology_hash = ? ORDER BY first_seen_at",
        (topology_hash,),
    )


def topology_index(hub: HubDatabase) -> list[sqlite3.Row]:
    """Every topology with the number of recipes filed under it.

    The Workflows view's whole list in one query. A LEFT JOIN rather than a
    subquery per row: 192 topologies over 617 recipes is small, and a topology
    with no recipe - a UI-format workflow filed by import, which has no recipe
    until ``object_info`` names its widgets - reads as zero rather than
    vanishing.
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

    ``thumbnail`` is required and has no default, so the type itself refuses a
    ghost that would be a retained prompt on its own -- which is the artefact
    library plan §5 forbids, and the reason ``covered`` can be a safe default.

    ``positive_prompt`` is optional, and that is not the same rule relaxed. §5
    says a ghost never keeps the prompt ALONE; it does not say a prompt must
    exist. An upscale or img2img graph has no ``CLIPTextEncode``, so it has no
    prompt to keep, and it is still worth being able to make again. ``None``
    here means the picture never had one: it is copied straight off
    ``picture.comfyui_positive_prompt``, so nothing on the way in can drop it.
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


def ghost_instance_hashes(hub: HubDatabase, library_uuid: str) -> list[str]:
    """Every instance hash this library's ghosts lean on."""
    return [
        row["instance_hash"]
        for row in hub.fetchall(
            "SELECT DISTINCT instance_hash FROM workflow_picture_ghost "
            "WHERE library_uuid = ? ORDER BY instance_hash",
            (library_uuid,),
        )
    ]


def retained_instance_hashes(hub: HubDatabase, library_uuid: str) -> list[str]:
    """Every instance hash this library keeps a ghost or an instance row for.

    For a full re-check, when the vault changed under the hub without a trigger
    seeing it (a restore swapped the file) or a ghost that was covering an
    instance row has just been erased.
    """
    return [
        row["instance_hash"]
        for row in hub.fetchall(
            "SELECT instance_hash FROM workflow_picture_ghost WHERE library_uuid = ? "
            "UNION SELECT instance_hash FROM workflow_recipe_instance "
            "WHERE library_uuid = ? ORDER BY instance_hash",
            (library_uuid, library_uuid),
        )
    ]


def destroy_uncovered_instances(
    hub: HubDatabase, library_uuid: str, instance_hashes: list[str]
) -> int:
    """Destroy this library's instance rows for hashes no surviving picture carries.

    The caller has established that no surviving picture in THIS library holds
    these hashes. A row a ghost still leans on stays, whatever the retention
    position: the ghost is the consented survivor, and its instance is what
    makes it worth keeping. Returns how many rows went.
    """
    if not library_uuid or not instance_hashes:
        return 0
    removed = 0
    with hub.transaction() as conn:
        for batch in chunked(instance_hashes):
            placeholders = ",".join("?" for _ in batch)
            removed += (
                conn.execute(
                    "DELETE FROM workflow_recipe_instance WHERE library_uuid = ? "
                    f"AND instance_hash IN ({placeholders}) AND NOT EXISTS ("
                    "  SELECT 1 FROM workflow_picture_ghost g "
                    "  WHERE g.library_uuid = workflow_recipe_instance.library_uuid "
                    "  AND g.instance_hash = workflow_recipe_instance.instance_hash)",
                    (library_uuid, *batch),
                ).rowcount
                or 0
            )
    if removed:
        logger.info(
            "Destroyed %d workflow instance row(s) whose last picture and ghost "
            "are gone.",
            removed,
        )
    return removed


def picture_ghost_count(hub: HubDatabase, library_uuid: str) -> int:
    """How many picture ghosts one library holds — the number an erase destroys."""
    row = hub.fetchone(
        "SELECT COUNT(*) FROM workflow_picture_ghost WHERE library_uuid = ?",
        (library_uuid,),
    )
    return int(row[0]) if row else 0


def picture_ghosts_by_topology(hub: HubDatabase, library_uuid: str) -> dict[str, int]:
    """One library's picture ghosts, counted per topology, for the list's filter.

    A ghost whose recipe was never filed has no topology to be counted under,
    so it is in :func:`picture_ghost_count` and absent here.
    """
    rows = hub.fetchall(
        "SELECT r.topology_hash, COUNT(*) AS ghosts "
        "FROM workflow_picture_ghost g "
        "JOIN workflow_recipe r ON r.structural_hash = g.structural_hash "
        "WHERE g.library_uuid = ? GROUP BY r.topology_hash",
        (library_uuid,),
    )
    return {row["topology_hash"]: row["ghosts"] for row in rows}


def erase_picture_ghosts(hub: HubDatabase, library_uuid: str) -> int:
    """Destroy every ghost one library holds. Returns how many.

    One library, like every other query here: credentials are pinned to the
    library they were minted in, so an erase requested in one must not reach
    another's. A detached library's ghosts are erased by attaching it again.
    """
    with hub.transaction() as conn:
        removed = (
            conn.execute(
                "DELETE FROM workflow_picture_ghost WHERE library_uuid = ?",
                (library_uuid,),
            ).rowcount
            or 0
        )
    logger.info(
        "Erased %d picture ghost(s) of library %s on request.", removed, library_uuid
    )
    return removed


# ---------------------------------------------------------------------------
# How each picture input is filled (implementation plan §F3)
# ---------------------------------------------------------------------------


def input_modes_by_workflow(
    hub: HubDatabase, library_uuid: str
) -> dict[str, list[sqlite3.Row]]:
    """Every stored picture-input mode in one library, keyed by workflow file.

    One query for the whole workflow list, which asks every file whether it has
    a Selection input.
    """
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in hub.fetchall(
        "SELECT workflow_name, node_id, mode, pixel_sha FROM workflow_picture_input "
        "WHERE library_uuid = ? ORDER BY workflow_name, node_id",
        (library_uuid,),
    ):
        grouped.setdefault(row["workflow_name"], []).append(row)
    return grouped


def replace_input_modes(
    hub: HubDatabase,
    library_uuid: str,
    workflow_name: str,
    modes: list[tuple[str, str, Optional[str]]],
) -> None:
    """Store one workflow's modes whole, as ``(node_id, mode, pixel_sha)``.

    Replaced rather than merged: the caller sends every input, and a merge would
    keep a second Selection row the new set moved elsewhere.
    """
    with hub.transaction() as conn:
        conn.execute(
            "DELETE FROM workflow_picture_input "
            "WHERE library_uuid = ? AND workflow_name = ?",
            (library_uuid, workflow_name),
        )
        conn.executemany(
            "INSERT INTO workflow_picture_input "
            "(library_uuid, workflow_name, node_id, mode, pixel_sha) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (library_uuid, workflow_name, node_id, mode, pixel_sha)
                for node_id, mode, pixel_sha in modes
            ],
        )


def forget_input_modes(hub: HubDatabase, workflow_name: str) -> int:
    """Drop a deleted workflow file's modes in every library. Returns how many.

    Every library, unlike the reads: the file is one per machine, so once it is
    gone no library's setup for it describes anything.
    """
    with hub.transaction() as conn:
        return (
            conn.execute(
                "DELETE FROM workflow_picture_input WHERE workflow_name = ?",
                (workflow_name,),
            ).rowcount
            or 0
        )


# ---------------------------------------------------------------------------
# Which parameters a workflow's form shows first (#1306)
# ---------------------------------------------------------------------------


def parameter_pins(hub: HubDatabase, workflow_name: str) -> Optional[list[list[str]]]:
    """A workflow file's pins as ``[node_id, name]`` pairs, or ``None`` if unset.

    A stored value that does not read as pairs is logged and treated as unset,
    so the defaults apply rather than the form failing to open.
    """
    row = hub.fetchone(
        "SELECT pins FROM workflow_parameter_pins WHERE workflow_name = ?",
        (workflow_name,),
    )
    if row is None:
        return None
    try:
        pins = json.loads(row["pins"])
        if not all(
            isinstance(pin, list)
            and len(pin) == 2
            and all(isinstance(p, str) for p in pin)
            for pin in pins
        ):
            raise ValueError("not a list of [node_id, name] pairs")
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Stored pins of workflow %s are unreadable, using the defaults: %s",
            workflow_name,
            exc,
        )
        return None
    return pins


def replace_parameter_pins(
    hub: HubDatabase, workflow_name: str, pins: Optional[list[tuple[str, str]]]
) -> None:
    """Store a workflow file's pins whole, or forget them with ``None``."""
    with hub.transaction() as conn:
        if pins is None:
            conn.execute(
                "DELETE FROM workflow_parameter_pins WHERE workflow_name = ?",
                (workflow_name,),
            )
            return
        conn.execute(
            "INSERT INTO workflow_parameter_pins (workflow_name, pins) VALUES (?, ?) "
            "ON CONFLICT(workflow_name) DO UPDATE SET pins = excluded.pins",
            (workflow_name, json.dumps([list(pin) for pin in pins])),
        )
