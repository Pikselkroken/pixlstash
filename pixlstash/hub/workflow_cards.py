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
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_hash import WorkflowGraphError, asset_reference
from pixlstash.services.workflow_identity import (
    CORE_VERSION,
    RECIPE,
    STRUCTURAL,
    WORKFLOW_KEY_VERSION,
    Slot,
    core_hash,
    guess_mark,
    slots,
    special_groups,
    workflow_key,
    workflow_type,
)

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
    "LEFT JOIN workflow_topology_core c ON c.topology_hash = r.topology_hash "
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
    core_current = row["core_cached"] is not None and row["core_specials"] is not None
    if row["workflow_key"] is not None and core_current:
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

    topology_hash = row["topology_hash"]
    document_slots = slots(document)
    # Computed before the transaction opens: this is the CPU of the pass (a
    # Weisfeiler-Leman refinement and a strip), and the write lock is shared
    # with a second process. Only when the cache is missing or stale, because a
    # topology with 200 variants would otherwise recompute and rewrite one row
    # 200 times in a single pass.
    core = (
        None
        if core_current
        else core_hash(document, strip_loras=STRIP_LORAS_FOR_STACKS)
    )
    with hub.transaction() as conn:
        marks = _freeze_marks(conn, topology_hash, structural_hash, document_slots)
        if core is not None:
            _cache_topology(conn, topology_hash, document, document_slots, core)
        key = workflow_key(
            topology_hash,
            document_slots,
            [label for label, mark in marks.items() if mark == STRUCTURAL],
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


def variants_on_key(hub: HubDatabase, key: str) -> list[str]:
    """Every variant on one card, for the picture filter's ``IN``.

    An empty list is a real answer - a card with no filed variant - and the
    caller must keep it as a filter matching nothing rather than as no filter.
    """
    return [
        row["structural_hash"]
        for row in hub.fetchall(
            "SELECT structural_hash FROM workflow_variant "
            "WHERE workflow_key = ? AND key_version = ? ORDER BY structural_hash",
            (key, WORKFLOW_KEY_VERSION),
        )
    ]


def variants_in_stack(hub: HubDatabase, stack_id: str) -> list[str]:
    """Every variant on every card in one stack.

    *stack_id* is read two ways, in one query rather than one-then-the-other,
    because a stack can be named either way:

    - a **stored** stack (``workflow_stack``, manual or auto) has a row per
      card in ``workflow_stack_member``;
    - an automatic grouping that **has not been stored** is not a row at all -
      it IS the set of cards sharing a ``core_hash`` (:func:`card_grouping`
      computes it and writes nothing), so until it is materialised the only
      thing that names it is that hash.

    Nothing writes the stack tables yet, so today every answer comes from the
    ``core_hash`` half; the membership half is here because the schema already
    says a stack has its own id, and a filter that ignored it would answer a
    stored stack with an empty grid the day one is written.

    **Not consulted: ``workflow_unstacked``**, the owner taking a card out of
    its automatic grouping. Nothing writes that table either, and the step that
    does owns making every reader agree with it.

    A value that names neither matches nothing.
    """
    return [
        row["structural_hash"]
        for row in hub.fetchall(
            "SELECT DISTINCT v.structural_hash AS structural_hash "
            "FROM workflow_variant v "
            "LEFT JOIN workflow_topology_core c ON c.topology_hash = v.topology_hash "
            "AND c.core_version = ? "
            "WHERE v.key_version = ? AND (c.core_hash = ? OR v.workflow_key IN ("
            "SELECT workflow_key FROM workflow_stack_member WHERE stack_id = ?)) "
            "ORDER BY v.structural_hash",
            (CORE_RULE_VERSION, WORKFLOW_KEY_VERSION, stack_id, stack_id),
        )
    ]


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
    has since taken out of it. :func:`effective_stack_keys` answers the other
    question - what one card's stack actually IS - and subtracts
    ``workflow_stack_member`` and ``workflow_unstacked`` for that reason. The two
    legitimately disagree once the owner has moved a card, and a reader comparing
    them should expect it.
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


def effective_stack_keys(hub: HubDatabase, key: str) -> list[str]:
    """The cards one card shares a stack with, itself included.

    Resolved in the order the plan sets (§5.4) and never written down: an
    explicit ``workflow_stack_member`` row first, then the owner having taken
    this card OUT of its automatic group, and only then the automatic group
    itself - every card whose topology shares this one's ``core_hash`` under the
    rule THIS build applies. Cards that have left the group by either of the
    first two routes are excluded from it, which is what makes an Unstack stick
    through a ``CORE_VERSION`` bump.

    **Only rows keyed by the rule THIS build applies** (``key_version``), like
    every other reader but ``card_grouping``. A row from a superseded rule names
    a card computed under different marks, and grouping by it would stack cards
    this build does not believe in. The cost is a transient: mid-re-derivation a
    card's stack shrinks to itself until the pass catches up, which reads as a
    smaller stack rather than as a wrong one.

    **A membership row wins whatever its stack's ``kind``.** An ``auto`` stack
    only has rows once somebody has ordered or edited it, and at that point it
    is as much the owner's arrangement as a ``manual`` one; reading the kind
    here would put a card back in a grouping it was explicitly placed out of.
    The lookup is ordered, because the table's primary key
    ``(stack_id, workflow_key)`` does not stop a card holding two memberships
    and an arbitrary answer would be an unreproducible stack.

    A card with no cache row yet, or one stamped with a superseded rule, is its
    own stack rather than joining a NULL bucket that would read as one enormous
    stack holding every unfiled card.

    Returns:
        The keys, ``key`` always among them even when the hub has never heard
        of it - a saved recipe still runs on the workflow it was saved from.
    """
    member = hub.fetchone(
        "SELECT stack_id FROM workflow_stack_member WHERE workflow_key = ? "
        "ORDER BY stack_id",
        (key,),
    )
    if member is not None:
        rows = hub.fetchall(
            "SELECT workflow_key FROM workflow_stack_member WHERE stack_id = ? "
            "ORDER BY position, workflow_key",
            (member["stack_id"],),
        )
        return [row["workflow_key"] for row in rows]

    unstacked = hub.fetchone(
        "SELECT 1 FROM workflow_unstacked WHERE workflow_key = ?", (key,)
    )
    if unstacked is not None:
        return [key]

    rows = hub.fetchall(
        "SELECT DISTINCT v.workflow_key AS workflow_key "
        "FROM workflow_variant v "
        "JOIN workflow_topology_core c ON c.topology_hash = v.topology_hash "
        "AND c.core_version = ? "
        "WHERE v.key_version = ? AND c.core_hash IN ("
        "  SELECT c2.core_hash FROM workflow_variant v2 "
        "  JOIN workflow_topology_core c2 ON c2.topology_hash = v2.topology_hash "
        "  AND c2.core_version = ? "
        "  WHERE v2.workflow_key = ? AND v2.key_version = ?"
        ") "
        "AND v.workflow_key NOT IN (SELECT workflow_key FROM workflow_stack_member) "
        "AND v.workflow_key NOT IN (SELECT workflow_key FROM workflow_unstacked) "
        "ORDER BY v.workflow_key",
        (
            CORE_RULE_VERSION,
            WORKFLOW_KEY_VERSION,
            CORE_RULE_VERSION,
            key,
            WORKFLOW_KEY_VERSION,
        ),
    )
    keys = [row["workflow_key"] for row in rows]
    return keys if key in keys else [key, *keys]


def variant_hashes_for_keys(hub: HubDatabase, keys: list[str]) -> list[str]:
    """Every variant filed under these cards.

    The structural hashes are what a vault read joins on: a picture carries one
    (``picture.workflow_structural_hash``) and the hub says which card it is
    part of, so "the pictures this stack made" is one ``IN`` over an indexed
    column rather than a join across two databases.

    Filtered on ``key_version`` for the reason
    :func:`effective_stack_keys` is: a variant filed under a superseded rule
    belongs to a card this build would not compute, and crediting its pictures
    to this one would be crediting somebody else's.
    """
    if not keys:
        return []
    placeholders = ",".join("?" for _ in keys)
    rows = hub.fetchall(
        "SELECT structural_hash FROM workflow_variant "
        f"WHERE key_version = ? AND workflow_key IN ({placeholders}) "
        "ORDER BY structural_hash",
        (WORKFLOW_KEY_VERSION, *keys),
    )
    return [row["structural_hash"] for row in rows]
