"""Cards: which workflow a stored variant belongs to (v1.12 B2).

:mod:`pixlstash.hub.workflows` files a graph's identity; this module derives the
**card** from what is already filed and stores it.
:mod:`pixlstash.services.workflow_identity` owns every rule, so nothing here
decides anything - it reads the stored document, freezes the marks that have to
be frozen, and writes rows.

**Hub documents only.** The whole derivation runs off
``workflow_recipe_graph.document``, whose assets are opaque references, which is
what lets the backfill be a hub pass with no picture rescan and what keeps a
forgotten model name forgotten: a LoRA whose name has been forgotten cannot be
guessed at and falls to ``recipe``, rather than resolving to something readable.

**Idempotent, and no row carries a timestamp.** Deriving the same hub twice
writes byte-identical rows, which is what lets the backfill be re-run with no
reconciliation pass. A ``computed_at`` would have made every re-derivation churn
every row and turned "runs twice the same" into a claim no test could make.

**One thing here is NOT content alone: a LoRA slot's mark.** It is frozen the
first time the slot is seen (``workflow_slot_mark``), so two machines that
imported the same pictures in a different order can put the same variant on
different cards. That is the design and not an oversight - re-guessing per
filing would re-key cards the owner has by then named, pinned and stacked - but
it is the reason nothing else here is allowed to depend on when a row was
written.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_hash import WorkflowGraphError, asset_reference
from pixlstash.services.workflow_identity import (
    CORE_VERSION,
    RECIPE,
    STRUCTURAL,
    WORKFLOW_KEY_VERSION,
    LoaderSwap,
    Slot,
    core_hash,
    guess_mark,
    lora_assets,
    slots,
    special_groups,
    unswapped,
    workflow_key,
    workflow_type,
)
from pixlstash.utils.sql_chunking import chunked

logger = get_logger(__name__)

# Whether the automatic stack key ignores LoRA loaders. True groups "the same
# workflow plus a character LoRA" into one stack, which is the point of the
# grouping; whether it OVER-groups is what the owner gate on the dogfood copy
# answers, and this is the single line that flips it.
STRIP_LORAS_FOR_STACKS = True

# What is stamped on a cached core hash, and it is NOT `CORE_VERSION` alone.
# `core_hash` does not carry the flag above inside its digest the way
# `workflow_key` carries `WORKFLOW_KEY_VERSION` inside its own, so flipping the
# flag changes every core hash while leaving `CORE_VERSION` at "v1": the finder
# would not re-queue, and the hub would hold two rules' stacks at once with no
# way to tell them apart. Naming the flag in the stamp makes a flip a
# re-derivation, which is what the owner gate needs it to be.
CORE_RULE_VERSION = (
    f"{CORE_VERSION}-loras-{'stripped' if STRIP_LORAS_FOR_STACKS else 'kept'}"
)

# Every variant with a stored document, left-joined to the card rules THIS build
# writes. A row whose join came back empty needs the pass: it has no card, or it
# has one from a superseded rule. One fragment, so the finder's query, its
# progress count and the derivation itself cannot disagree about what is
# outstanding - a variant reported as done but still handed out is a finder that
# never settles.
_VARIANT_JOIN = (
    "FROM workflow_recipe r "
    "JOIN workflow_recipe_graph g ON g.structural_hash = r.structural_hash "
    "LEFT JOIN workflow_variant v ON v.structural_hash = r.structural_hash "
    "AND v.key_version = ? "
    # The variant's own topology once it has a card: a graph with a swapped-in
    # loader is carded under the topology it was swapped from (#1605), and that
    # is the one whose cache its card reads.
    "LEFT JOIN workflow_topology_core c "
    "ON c.topology_hash = COALESCE(v.topology_hash, r.topology_hash) "
    "AND c.core_version = ? "
)
_VARIANT_VERSIONS = (WORKFLOW_KEY_VERSION, CORE_RULE_VERSION)
# `c.specials IS NULL` is the third case and it is not redundant: a topology
# cached before that column existed joins on the current rule and is still
# missing a value this build's cards read. Re-queuing on the column itself,
# rather than bumping CORE_VERSION, is what keeps the re-derivation invisible -
# the row keeps its stack key, its type and its slots the whole time, so no
# grid goes blank while the pass runs. The finder is the once-only-ness here
# (see WorkflowCardBackfillFinder): work exists exactly while a stored document
# has no current row, so nothing has to remember to run anything.
_VARIANT_PENDING = (
    "(v.structural_hash IS NULL OR c.topology_hash IS NULL OR c.specials IS NULL)"
)


def topology_only_key(topology_hash: str) -> str:
    """The card key for a graph whose models are not known.

    A UI-format file names its widget values by position, so it has a topology
    and no assets. It gets a card all the same - the same card an API graph of
    that topology naming no models at all would get, which is the right answer:
    neither of them says which model it uses.
    """
    return workflow_key(topology_hash, [], [])


def record_identity(hub: HubDatabase, structural_hash: str) -> Optional[str]:
    """Derive and store the card a filed variant belongs to; return its key.

    Returns ``None`` when the variant is not filed here or its document has
    gone: an identity is derived from a stored document, never guessed.

    One transaction, so a crash cannot leave a card keyed on marks that were not
    written. It holds no file and waits on nobody, which is what the hub's short
    write transactions ask.

    Raises:
        pixlstash.services.workflow_hash.WorkflowGraphError: The stored document
            cannot be reduced, or nothing survives the strip. Callers filing
            arbitrary pictures catch this and skip the variant.
    """
    row = hub.fetchone(
        "SELECT r.topology_hash AS topology_hash, g.document AS document, "
        "v.workflow_key AS workflow_key, c.topology_hash AS core_cached, "
        "c.specials AS core_specials "
        f"{_VARIANT_JOIN} WHERE r.structural_hash = ?",
        (*_VARIANT_VERSIONS, structural_hash),
    )
    if row is None:
        return None
    # The same three conditions `_VARIANT_PENDING` selects on, spelled here as
    # the early return. Both halves, and on the same rule the finder selects by:
    # returning early on a current card while the topology cache is stale would
    # leave the finder handing this variant out on every sweep, for a pass that
    # does nothing.
    #
    # **The two cache gaps are kept apart** rather than folded into one "is it
    # current". A row missing only `specials` already holds a `core_hash` under
    # the current rule, and re-deriving that would run the Weisfeiler-Leman
    # refinement over every topology in the hub on upgrade to fill in at most
    # two words. That row is UPDATEd in place instead.
    core_missing = row["core_cached"] is None
    specials_missing = row["core_specials"] is None
    if row["workflow_key"] is not None and not core_missing and not specials_missing:
        return row["workflow_key"]
    try:
        document = json.loads(row["document"])
    except json.JSONDecodeError as exc:
        logger.error(
            "Stored workflow document for variant %s is not valid JSON, so it "
            "gets no card: %s",
            structural_hash,
            exc,
        )
        return None

    # A graph a model fix swapped a loader in (#1605) is carded as the graph it
    # was swapped from: that topology, its marks, its cache and its key.
    swapped_from, document = unswapped(
        document, loader_swaps_of(hub.fetchall, row["topology_hash"])
    )
    topology_hash = swapped_from or row["topology_hash"]
    if swapped_from:
        # The cache the join read was the swapped topology's; the card reads
        # the original's, which a card made from a workflow file alone lacks.
        core_missing = True
    document_slots = slots(document)
    # Computed before the transaction opens: this is the CPU of the pass (a
    # Weisfeiler-Leman refinement and a strip), and the write lock is shared
    # with a second process. Only when the cache is missing or stale, because a
    # topology with 200 variants would otherwise recompute and rewrite one row
    # 200 times in a single pass.
    core = (
        core_hash(document, strip_loras=STRIP_LORAS_FOR_STACKS)
        if core_missing
        else None
    )
    with hub.transaction() as conn:
        marks = _freeze_marks(conn, topology_hash, structural_hash, document_slots)
        if core is not None:
            _cache_topology(conn, topology_hash, document, document_slots, core)
        elif specials_missing:
            # The whole cost of the upgrade for an already-cached topology: one
            # reduction, no refinement, and the row's stack key, type and slots
            # are left exactly where the grid is already reading them.
            # `core_version` is in the WHERE so a row re-stamped under another
            # rule between the read and here is not written by this branch.
            conn.execute(
                "UPDATE workflow_topology_core SET specials = ? "
                "WHERE topology_hash = ? AND core_version = ?",
                (
                    ",".join(special_groups(document)),
                    topology_hash,
                    CORE_RULE_VERSION,
                ),
            )
        key = workflow_key(
            topology_hash,
            fixed_slots(conn, topology_hash, document_slots),
            [label for label, mark in marks.items() if mark == STRUCTURAL],
            promoted_pairs(conn, topology_hash),
        )
        # REPLACE and not IGNORE: a re-keyed variant (a flipped mark, a new
        # WORKFLOW_KEY_VERSION) has to land on its new card, and this row is the
        # only place the old key is recorded.
        conn.execute(
            "INSERT OR REPLACE INTO workflow_variant "
            "(structural_hash, topology_hash, workflow_key, key_version) "
            "VALUES (?, ?, ?, ?)",
            (structural_hash, topology_hash, key, WORKFLOW_KEY_VERSION),
        )
    return key


def loader_swaps_of(fetchall, swapped_topology_hash: str) -> list[LoaderSwap]:
    """The loader swaps recorded for graphs of this topology (#1605).

    *fetchall* is ``hub.fetchall`` or a connection's equivalent, so the read
    can sit inside a caller's transaction.
    """
    return [
        LoaderSwap(
            topology_hash=row[0],
            node_label=row[1],
            class_type=row[2],
            swap_class=row[3],
            fields=tuple(tuple(field) for field in json.loads(row[4])),
        )
        for row in fetchall(
            "SELECT topology_hash, node_label, class_type, swap_class, fields "
            "FROM workflow_loader_swap WHERE swapped_topology_hash = ?",
            (swapped_topology_hash,),
        )
    ]


def fixed_slots(
    conn: sqlite3.Connection, topology_hash: str, document_slots: list[Slot]
) -> list[Slot]:
    """*document_slots* with each replacement model read as the one it replaced.

    What files a picture made with a fixed workflow (``workflow_model_fix``) on
    the card the original model made: a slot holding the replacement counts as
    holding the original, so the card key comes out the same. Every writer of
    ``workflow_variant.workflow_key`` computes the key over this, or a re-key
    would move those pictures back off the card.
    """
    fixes = {
        (label, asset_reference(now_norm)): asset_reference(was_norm)
        for label, was_norm, now_norm in conn.execute(
            "SELECT slot_label, was_norm, now_norm FROM workflow_model_fix "
            "WHERE topology_hash = ?",
            (topology_hash,),
        ).fetchall()
    }
    if not fixes:
        return document_slots
    return [
        replace(slot, asset=fixes.get((slot.label, slot.asset), slot.asset))
        for slot in document_slots
    ]


def promoted_pairs(
    conn: sqlite3.Connection, topology_hash: str
) -> set[tuple[str, str]]:
    """``(slot label, asset)`` of every LoRA file promoted in this topology.

    What :func:`~pixlstash.services.workflow_identity.workflow_key` takes as
    ``promoted``. Every writer of ``workflow_variant.workflow_key`` passes it,
    or a re-key would fold a promoted LoRA's pictures back onto the card they
    were split from.
    """
    return {
        (label, asset)
        for label, asset in conn.execute(
            "SELECT slot_label, asset FROM workflow_lora_promotion "
            "WHERE topology_hash = ?",
            (topology_hash,),
        ).fetchall()
    }


def _freeze_marks(
    conn: sqlite3.Connection,
    topology_hash: str,
    structural_hash: str,
    document_slots: list[Slot],
) -> dict[str, str]:
    """Mark every unmarked LoRA slot of this topology, and read them all back.

    The guess needs the filename, which the document deliberately does not
    carry: it names assets by reference, and the readable name lives in
    ``workflow_recipe_asset``. A reference resolving to nothing is a name that
    was forgotten or never filed, and falls to ``recipe`` - the guess errs that
    way anyway, because a wrong ``structural`` pulls a character LoRA into the
    card key and splits the card per LoRA.
    """
    names = {
        asset_reference(name): name
        for (name,) in conn.execute(
            "SELECT normalized_filename FROM workflow_recipe_asset "
            "WHERE structural_hash = ?",
            (structural_hash,),
        ).fetchall()
    }
    # A label is shared by loaders Weisfeiler-Leman cannot separate (genuine
    # twins), and `slots` returns them sorted by `(label, asset)` - so where a
    # topology has twins the mark is frozen from the lexicographically smaller
    # `asset:<digest>`, a stable choice made on something meaningless. Same
    # freeze as the order dependence in the module docstring, same answer: the
    # correction is a flip, never a re-guess.
    # IGNORE is the freeze: the first sighting of a slot decides it, and every
    # later one - a different LoRA in that slot, a re-run of the backfill - is a
    # no-op.
    conn.executemany(
        "INSERT OR IGNORE INTO workflow_slot_mark "
        "(topology_hash, slot_label, mark) VALUES (?, ?, ?)",
        [
            (
                topology_hash,
                slot.label,
                guess_mark(names[slot.asset]) if slot.asset in names else RECIPE,
            )
            for slot in document_slots
            if slot.is_lora
        ],
    )
    return {
        label: mark
        for label, mark in conn.execute(
            "SELECT slot_label, mark FROM workflow_slot_mark WHERE topology_hash = ?",
            (topology_hash,),
        ).fetchall()
    }


def _cache_topology(
    conn: sqlite3.Connection,
    topology_hash: str,
    document: dict,
    document_slots: list[Slot],
    core: str,
) -> None:
    """Cache this topology's stack key, type and slot list.

    ``slots`` is JSON and holds no filename and no asset reference: a model's
    readable name lives in ``workflow_recipe_asset`` and nowhere else, so
    forgetting it stays one delete.

    ``specials`` is written on every pass and is never left NULL here: NULL is
    reserved for a row this pass has not touched, and a graph with no
    post-processing writes the empty string.
    """
    conn.execute(
        "INSERT OR REPLACE INTO workflow_topology_core "
        "(topology_hash, core_hash, core_version, workflow_type, slots, specials) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            topology_hash,
            core,
            CORE_RULE_VERSION,
            workflow_type(document),
            json.dumps(
                [
                    {
                        "label": slot.label,
                        "class_type": slot.class_type,
                        "widget": slot.widget,
                        "is_lora": slot.is_lora,
                    }
                    for slot in document_slots
                ],
                separators=(",", ":"),
            ),
            ",".join(special_groups(document)),
        ),
    )


def record_file(
    hub: HubDatabase,
    name: str,
    topology_hash: str,
    structural_hash: Optional[str] = None,
) -> Optional[str]:
    """Put a stored workflow file on its card; return the card's key.

    Called where a file is filed - the import route, and so the watched inbox
    too - so a file lands on the card its pictures already made rather than
    starting one of its own.

    REPLACE, because a file name is the owner's and can be overwritten with a
    different workflow; the row describes what is in the file now.
    """
    key = None
    if structural_hash is not None:
        try:
            key = record_identity(hub, structural_hash)
        except WorkflowGraphError as exc:
            logger.info(
                "Workflow file %s gets a card with no assets, its stored graph "
                "cannot be keyed: %s",
                name,
                exc,
            )
    if key is None:
        # Either a UI-format file, or a document that would not reduce. Both are
        # a card with no assets rather than no card: a file the owner can see in
        # the folder and not on the Workflows view is the worse answer.
        key = topology_only_key(topology_hash)
    with hub.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_file "
            "(workflow_name, topology_hash, structural_hash, workflow_key) "
            "VALUES (?, ?, ?, ?)",
            (name, topology_hash, structural_hash, key),
        )
    return key


def forget_file(hub: HubDatabase, name: str) -> int:
    """Take a deleted workflow file off its card. Returns how many rows went.

    The card itself stays: it is made by the pictures, and the file was only one
    way to run it.
    """
    with hub.transaction() as conn:
        return (
            conn.execute(
                "DELETE FROM workflow_file WHERE workflow_name = ?", (name,)
            ).rowcount
            or 0
        )


def key_of_variant(hub: HubDatabase, structural_hash: str) -> Optional[str]:
    """The card a filed variant is on, or ``None`` if it has none yet.

    A read, never a derivation: a variant whose card has not been derived (or
    was keyed by a superseded rule) has no card to report, and inventing one
    here would hand out a key the hub does not hold.
    """
    row = hub.fetchone(
        "SELECT workflow_key FROM workflow_variant "
        "WHERE structural_hash = ? AND key_version = ?",
        (structural_hash, WORKFLOW_KEY_VERSION),
    )
    return row["workflow_key"] if row else None


def variants_loading(
    hub: HubDatabase, asset: str, among: Optional[list[str]] = None
) -> list[str]:
    """The variants whose stored graph loads the LoRA *asset*, sorted.

    For the picture filter's ``workflow_lora``: *among* is what the other
    workflow filters already resolved to, and ``None`` means every filed
    variant (the picture listing never passes ``None``: it is open to scoped
    tokens, and that would parse every stored graph per request). A document that will not parse is logged and matches nothing,
    so an unreadable row narrows the grid rather than widening it.
    """
    if among is None:
        rows = hub.fetchall(
            "SELECT structural_hash, document FROM workflow_recipe_graph "
            "ORDER BY structural_hash"
        )
    else:
        rows = []
        for batch in chunked(sorted(set(among))):
            placeholders = ",".join("?" * len(batch))
            rows += hub.fetchall(
                "SELECT structural_hash, document FROM workflow_recipe_graph "
                f"WHERE structural_hash IN ({placeholders})",
                tuple(batch),
            )
    found = []
    for structural_hash, raw in rows:
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(
                "Stored document of variant %s will not parse, so the LoRA "
                "filter leaves its pictures out: %s",
                structural_hash,
                exc,
            )
            continue
        if isinstance(document, dict) and asset in lora_assets(document):
            found.append(structural_hash)
    return sorted(found)


def unidentified_variants(hub: HubDatabase, limit: int) -> list[str]:
    """Filed variants with no card, or with one keyed by a superseded rule."""
    return [
        structural_hash
        for (structural_hash,) in hub.fetchall(
            f"SELECT r.structural_hash {_VARIANT_JOIN} WHERE {_VARIANT_PENDING} "
            "ORDER BY r.structural_hash LIMIT ?",
            (*_VARIANT_VERSIONS, limit),
        )
    ]


def variant_counts(hub: HubDatabase) -> tuple[int, int]:
    """``(variants with a stored document, how many still need the pass)``."""
    row = hub.fetchone(
        f"SELECT COUNT(*) AS total, SUM({_VARIANT_PENDING}) AS pending {_VARIANT_JOIN}",
        _VARIANT_VERSIONS,
    )
    if row is None:
        return 0, 0
    return int(row["total"] or 0), int(row["pending"] or 0)


def card_grouping(hub: HubDatabase) -> dict:
    """What the cards group into: the owner gate's report.

    A ``core_hash`` shared by two or more cards is one automatic stack; a
    ``core_hash`` with one card is a one-off. Nothing is written - the stack
    tables are for the owner's own decisions, and the automatic grouping IS this
    query - so a regrouping costs nothing and destroys nothing.

    **Only rows stamped with the rule this build applies are grouped.** Read
    mid-re-derivation the cache legitimately holds a superseded ``core_hash``,
    and mixing the two would report a grouping no build ever produces. A card
    whose cache row has not caught up is counted in ``cards`` and reported as
    ``ungrouped`` rather than folded into a NULL bucket, which would otherwise
    read as one enormous stack.

    One grouped scan rather than a count per figure: five separate aggregates
    over the same table is five scans for numbers that have to agree anyway.

    **This is the AUTOMATIC grouping, before the owner's decisions, and that is
    deliberate**: the gate it reports for asks whether ``STRIP_LORAS_FOR_STACKS``
    groups well, which is a question about the rule and not about what anybody
    has since taken out of it. ``workflow_card_reads.workflow_index`` answers
    the other question - what the owner's workflows actually are - and puts a
    topology the owner placed by hand (``workflow_group_member``) where they
    put it. The two legitimately disagree once the owner has moved one.
    """
    rows = hub.fetchall(
        "SELECT c.core_hash AS core_hash, v.topology_hash AS topology_hash, "
        "v.workflow_key AS workflow_key, COUNT(*) AS variants "
        "FROM workflow_variant v "
        "LEFT JOIN workflow_topology_core c ON c.topology_hash = v.topology_hash "
        "AND c.core_version = ? "
        "GROUP BY c.core_hash, v.topology_hash, v.workflow_key",
        (CORE_RULE_VERSION,),
    )
    cards_per_core: dict[str, set] = {}
    ungrouped: set = set()
    for row in rows:
        if row["core_hash"] is None:
            ungrouped.add(row["workflow_key"])
            continue
        cards_per_core.setdefault(row["core_hash"], set()).add(row["workflow_key"])
    sizes = [len(cards) for cards in cards_per_core.values()]
    # A card with two variants can have one of them cached and the other not,
    # so a card that is grouped at all is not also counted as ungrouped.
    grouped = set().union(*cards_per_core.values()) if cards_per_core else set()
    return {
        "variants": sum(int(row["variants"]) for row in rows),
        "topologies": len({row["topology_hash"] for row in rows}),
        "cards": len({row["workflow_key"] for row in rows}),
        "stacks": sum(1 for size in sizes if size > 1),
        "stacked_cards": sum(size for size in sizes if size > 1),
        "one_offs": sum(1 for size in sizes if size == 1),
        # Cards whose topology cache is missing or stamped with a superseded
        # rule. Zero once the backfill has drained.
        "ungrouped": len(ungrouped - grouped),
    }
