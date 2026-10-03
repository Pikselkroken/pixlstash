"""The card writes that remain after the cut-over (#1623): a model fix.

:mod:`pixlstash.hub.workflow_cards` derives a card from a stored document and
:mod:`pixlstash.hub.workflow_card_reads` reads the rows back. The owner's
decisions about a WORKFLOW are written by
:mod:`pixlstash.hub.workflow_group_writes`; what is left here re-keys cards
internally - a model fix, which reads a replacement as the original it
replaced - and records a swapped-in loader.

**One transaction per logical write**, so a crash can never leave attributes on
a key no variant is on. A re-key moves every variant of a topology AND carries
the card tables across in the same breath (:data:`_KEYED_TABLES`), which the
cut-over's conversion reads once; a table added there belongs in that tuple.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflow_cards import (
    fixed_slots,
    loader_swaps_of,
    promoted_pairs,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_hash import WorkflowGraphError, normalized_filename
from pixlstash.services.workflow_identity import (
    STRUCTURAL,
    WORKFLOW_KEY_VERSION,
    LoaderSwap,
    slots,
    unswapped,
    workflow_key,
)
from pixlstash.utils.adapter_header import FILE_CHECKPOINT

logger = get_logger(__name__)

# Every table the owner's decisions about a card live in, all keyed on
# ``workflow_key``. Ordered children-last is not needed - none of them
# references another - but the tuple IS the carry-over's definition of "the
# user attributes", so it is the one place a new card table has to be listed.
_KEYED_TABLES = (
    "workflow_attr",
    "workflow_default_override",
    "workflow_key_pins",
    "workflow_key_picture_input",
    "workflow_cover",
    "workflow_stack_member",
    "workflow_unstacked",
)


def set_model_fix(
    hub: HubDatabase,
    topology_hash: str,
    slot_labels: list[str],
    was: str,
    now: Optional[str],
    variant_pictures: dict[str, int],
    keep_key: Optional[str] = None,
    kind: str = FILE_CHECKPOINT,
) -> dict[str, list[str]]:
    """Replace model *was* with *now* in these slots of one topology, or undo it.

    ``now=None`` takes the replacement back out. *kind* is the shelf
    ``file_kind`` the slots take (``workflow_identity.model_fix_kind``), kept
    so the Workflow tab can say which row a fix belongs to. Either way every variant of
    the topology is re-keyed in the same transaction:
    setting a fix folds the pictures already made with *now* onto the card the
    original made, and undoing it moves them back onto a card of their own.

    *keep_key* is the card being fixed: where cards merge onto it, its own
    name, pins and defaults win whatever the picture counts say, because the
    owner is fixing THIS card rather than folding it into another.

    Returns:
        ``{old key: [new key, ...]}``, biggest successor first; the old key is
        among its own successors when a variant is still on it.
    """
    was_norm = normalized_filename(was)
    documents = _topology_documents(hub, topology_hash)
    with hub.transaction() as conn:
        conn.executemany(
            "DELETE FROM workflow_model_fix "
            "WHERE topology_hash = ? AND slot_label = ? AND was_norm = ?",
            [(topology_hash, label, was_norm) for label in slot_labels],
        )
        if now is not None:
            conn.executemany(
                "INSERT INTO workflow_model_fix (topology_hash, slot_label, "
                "was_norm, now_norm, was_name, now_name, slot_kind) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        topology_hash,
                        label,
                        was_norm,
                        normalized_filename(now),
                        was,
                        now,
                        kind,
                    )
                    for label in slot_labels
                ],
            )
        moved = _rekey_variants(
            conn, topology_hash, documents, variant_pictures, keep_key
        )
    logger.info(
        "Model %r on topology %s %s; %d card(s) moved.",
        was,
        topology_hash,
        f"replaced by {now!r}" if now is not None else "restored",
        len(moved),
    )
    return moved


def _topology_documents(hub: HubDatabase, topology_hash: str) -> dict[str, dict]:
    """Every stored document of one topology, by variant, that parses.

    A variant carded under this topology through a swapped-in loader (#1605)
    is one of them, its document read with the original loader put back.
    """
    documents = {}
    for structural_hash, recipe_topology, raw in hub.fetchall(
        "SELECT r.structural_hash, r.topology_hash, g.document "
        "FROM workflow_recipe r "
        "JOIN workflow_recipe_graph g ON g.structural_hash = r.structural_hash "
        "WHERE r.topology_hash = ? OR r.structural_hash IN "
        "(SELECT structural_hash FROM workflow_variant WHERE topology_hash = ?)",
        (topology_hash, topology_hash),
    ):
        try:
            document = json.loads(raw)
            if recipe_topology != topology_hash:
                swapped_from, document = unswapped(
                    document, loader_swaps_of(hub.fetchall, recipe_topology)
                )
                if swapped_from != topology_hash:
                    continue
            documents[structural_hash] = document
        except (json.JSONDecodeError, WorkflowGraphError) as exc:
            logger.error(
                "Stored document of variant %s will not parse, so a re-key "
                "leaves it on the card it is on: %s",
                structural_hash,
                exc,
            )
    return documents


def record_loader_swaps(
    hub: HubDatabase, swapped_topology_hash: str, swaps: list[LoaderSwap]
) -> None:
    """Remember that graphs of this topology are another's with loaders swapped.

    What a run that swapped a PixlStash loader in for a workflow's own (#1605)
    writes, so the pictures it makes are carded as the original graph's
    (``workflow_cards.record_identity``). A fact about two graphs, not about a
    fix: it is kept when the fix is undone, and those pictures then card as the
    replacement's, as a rewritten name's do.
    """
    with hub.transaction() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO workflow_loader_swap (swapped_topology_hash, "
            "node_label, fields, topology_hash, class_type, swap_class) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    swapped_topology_hash,
                    swap.node_label,
                    json.dumps(swap.fields, separators=(",", ":")),
                    swap.topology_hash,
                    swap.class_type,
                    swap.swap_class,
                )
                for swap in swaps
            ],
        )


def _rekey_variants(
    conn: sqlite3.Connection,
    topology_hash: str,
    documents: dict[str, dict],
    variant_pictures: dict[str, int],
    keep_key: Optional[str] = None,
) -> dict[str, list[str]]:
    """Move every variant of one topology onto the key its marks and fixes give it.

    Where several cards merge, the one with most pictures keeps its attributes,
    unless *keep_key* is among them.
    """
    structural_labels = {
        label
        for label, mark in conn.execute(
            "SELECT slot_label, mark FROM workflow_slot_mark WHERE topology_hash = ?",
            (topology_hash,),
        ).fetchall()
        if mark == STRUCTURAL
    }
    promoted = promoted_pairs(conn, topology_hash)
    old_keys = {
        structural_hash: key
        for structural_hash, key in conn.execute(
            "SELECT structural_hash, workflow_key FROM workflow_variant "
            "WHERE topology_hash = ? AND key_version = ?",
            (topology_hash, WORKFLOW_KEY_VERSION),
        ).fetchall()
    }
    new_keys = {}
    for structural_hash, document in documents.items():
        if structural_hash not in old_keys:
            continue
        try:
            new_keys[structural_hash] = workflow_key(
                topology_hash,
                fixed_slots(conn, topology_hash, slots(document)),
                structural_labels,
                promoted,
            )
        except WorkflowGraphError as exc:
            logger.error(
                "Variant %s will not reduce, so the mark flip leaves it on the "
                "card it is on: %s",
                structural_hash,
                exc,
            )

    # Which old keys land on each new one, and which new keys each old one
    # fans out to. Both directions, because the two failure modes are
    # opposite: a merge needs a winner among several olds, and a split needs
    # every new key to end up with a COPY rather than the one that happened
    # to be written last.
    contributors: dict[str, set[str]] = {}
    successors: dict[str, set[str]] = {}
    for structural_hash, new_key in new_keys.items():
        contributors.setdefault(new_key, set()).add(old_keys[structural_hash])
        successors.setdefault(old_keys[structural_hash], set()).add(new_key)

    # Kept pictures per key, on both sides. Summed over the key's variants
    # rather than read per variant: a card is the unit the owner named, and
    # the biggest single variant of a small card is not the card that should
    # keep the name.
    pictures_per_old = _pictures_per_key(old_keys, variant_pictures)
    # Where every variant sits AFTER the flip, which is what "the biggest half"
    # has to be counted over. `new_keys` alone would answer 0 for a key that
    # only a variant which could not be re-keyed is still on, and then send the
    # caller to the smaller half of its own split.
    pictures_per_new = _pictures_per_key(
        {
            structural_hash: new_keys.get(structural_hash, key)
            for structural_hash, key in old_keys.items()
        },
        variant_pictures,
    )

    # Smallest successor first, which only matters for a split: each copy of a
    # stack membership goes just behind the source and pushes the previous copy
    # back, so copying in this order leaves the biggest half nearest the front
    # - the same half `SlotMarkResult.key` sends the caller to, rather than
    # whichever digest happens to sort first.
    for new_key, olds in sorted(
        contributors.items(),
        key=lambda item: (pictures_per_new.get(item[0], 0), item[0]),
    ):
        winner = (
            keep_key
            if keep_key in olds
            else min(olds, key=lambda key: (-pictures_per_old.get(key, 0), key))
        )
        if winner == new_key:
            continue
        for table in _KEYED_TABLES:
            # The target first: a key being written onto can only hold rows a
            # previous flip left there, and the winner's are the ones the
            # owner has been editing since.
            conn.execute(f"DELETE FROM {table} WHERE workflow_key = ?", (new_key,))
            _copy_rows(conn, table, winner, new_key)

    _carry_card_moves(conn, contributors)

    # Only now, and only for keys no variant is on any more: a split copies
    # one card's attributes to several, so deleting as it went would empty the
    # source before the second copy read it.
    #
    # **A key a variant is STILL on survives**, which is not the same set as
    # the new keys: a variant whose document would not parse or would not
    # reduce keeps the key it has (both branches log and carry on), and if a
    # sibling variant of the same card moved, that key is not among the new
    # ones. Deleting by the new keys alone would empty a card that is still
    # there, which is the one way this pass can destroy the owner's work.
    surviving = set(new_keys.values()) | {
        key
        for structural_hash, key in old_keys.items()
        if structural_hash not in new_keys
    }
    for old_key in sorted(set(old_keys.values()) - surviving):
        for table in _KEYED_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE workflow_key = ?", (old_key,))

    # A key a variant is still on is one of its own successors, whatever its
    # siblings did. Without that, `surviving` and `moved` disagree about the
    # same card: this pass correctly KEEPS the old key's attribute rows and
    # COPIES them to the sibling's new key, while a reader of `moved` alone
    # sees an old key whose successors are all elsewhere and concludes the card
    # went away. `saved_recipe_service.rekey_in_session` read it that way and
    # moved an authored recipe off a card that was still there.
    moved = {
        old_key: sorted(
            news | ({old_key} if old_key in surviving else set()),
            key=lambda key: (-pictures_per_new.get(key, 0), key),
        )
        for old_key, news in successors.items()
        if news != {old_key}
    }

    for structural_hash, new_key in new_keys.items():
        if new_key == old_keys[structural_hash]:
            continue
        conn.execute(
            "UPDATE workflow_variant SET workflow_key = ? WHERE structural_hash = ?",
            (new_key, structural_hash),
        )
        # The file rows too. ``workflow_file.workflow_key`` is derived rather
        # than the owner's, but a file left on a dead key is a card that stops
        # saying a workflow on this machine runs it until the next import.
        conn.execute(
            "UPDATE workflow_file SET workflow_key = ? WHERE structural_hash = ?",
            (new_key, structural_hash),
        )
    _renumber_stacks(conn)
    _drop_thin_stacks(conn)
    return moved


def _carry_card_moves(
    conn: sqlite3.Connection, contributors: dict[str, set[str]]
) -> None:
    """Put a family pass's move rows (#1689) on the keys recipes move to.

    A move row is matched against the key a saved recipe stores, and
    ``saved_recipe_service.rekey_in_session`` moves the recipes of EVERY old
    key onto the new one, merged loser or not. So a new key holds a move out
    of a workflow only when **every** card merging into it moved out of it:
    one card that stayed means its recipes would be re-filed by a row that
    was never about them, so the new key gets none, and every recipe there
    stays where it is (still running). The old keys' rows stay.
    """
    rows = conn.execute(
        "SELECT workflow_key, old_workflow_id, new_workflow_id FROM workflow_card_move"
    ).fetchall()
    for new_key, olds in sorted(contributors.items()):
        moves: dict[str, dict[str, str]] = {}
        for key, old_workflow_id, new_workflow_id in rows:
            if key in olds:
                moves.setdefault(old_workflow_id, {})[key] = new_workflow_id
        for old_workflow_id, by_key in sorted(moves.items()):
            if set(by_key) == olds:
                conn.execute(
                    "INSERT OR IGNORE INTO workflow_card_move (workflow_key, "
                    "old_workflow_id, new_workflow_id) VALUES (?, ?, ?)",
                    (
                        new_key,
                        old_workflow_id,
                        by_key.get(new_key, by_key[min(by_key)]),
                    ),
                )
            else:
                conn.execute(
                    "DELETE FROM workflow_card_move WHERE workflow_key = ? "
                    "AND old_workflow_id = ?",
                    (new_key, old_workflow_id),
                )


def _pictures_per_key(
    keys: dict[str, str], variant_pictures: dict[str, int]
) -> dict[str, int]:
    """``{card key: kept pictures}`` from ``{structural_hash: card key}``."""
    totals: dict[str, int] = {}
    for structural_hash, key in keys.items():
        totals[key] = totals.get(key, 0) + variant_pictures.get(structural_hash, 0)
    return totals


def _copy_rows(conn: sqlite3.Connection, table: str, source: str, target: str) -> None:
    """Copy every row of *table* keyed on *source* onto *target*.

    A copy and not an UPDATE, because a split sends one card's attributes to
    several new keys and a move would give them to whichever was written last.
    The column list is read from the table rather than written down here, so a
    column added to a card table is carried without this module hearing about
    it - which is the same reason :data:`_KEYED_TABLES` names tables and not
    columns.
    """
    if table == "workflow_stack_member":
        # `position` is not part of the primary key, so a verbatim copy puts
        # two cards on the same one - and `effective_stack_keys` then breaks
        # the tie on the KEY, which hands the stack's cover to whichever
        # digest sorts first rather than to the card with the pictures. The
        # successor goes just behind the source and everything after it
        # shifts, so a split lands its halves adjacent and in the order they
        # were copied in (`_rekey_variants` copies smallest successor first).
        for stack_id, position in conn.execute(
            "SELECT stack_id, position FROM workflow_stack_member "
            "WHERE workflow_key = ?",
            (source,),
        ).fetchall():
            conn.execute(
                "UPDATE workflow_stack_member SET position = position + 1 "
                "WHERE stack_id = ? AND position > ?",
                (stack_id, position),
            )
            conn.execute(
                "INSERT INTO workflow_stack_member "
                "(stack_id, workflow_key, position) VALUES (?, ?, ?)",
                (stack_id, target, position + 1),
            )
        return
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    selected = ", ".join(
        "?" if column == "workflow_key" else column for column in columns
    )
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"SELECT {selected} FROM {table} WHERE workflow_key = ?",
        (target, source),
    )


def _renumber_stacks(conn: sqlite3.Connection) -> None:
    """Close the gaps a split's inserts left, keeping every stack's order.

    ``workflow_stack_member.position`` is documented as "0 is the cover"
    (``hub/schema.py``), and a split inserts its successors behind the source
    and then deletes the source - which keeps the order but leaves the first
    member at 1.

    **Read whole, then written**, and that is not a style choice. The obvious
    one statement - ``SET position = (SELECT COUNT(*) FROM the same table
    WHERE it sorts before this row)`` - has the subquery reading rows this
    same statement has already updated, so the second row is counted against
    the first row's NEW position: two members land on one position and
    ``effective_stack_keys``' ``ORDER BY position, workflow_key`` then hands
    the cover to whichever digest sorts first. That is the bug this function
    exists to prevent, arrived at from the other direction.
    """
    rows = conn.execute(
        "SELECT stack_id, workflow_key, position FROM workflow_stack_member "
        "ORDER BY stack_id, position, workflow_key"
    ).fetchall()
    moved, current_stack, index = [], None, 0
    for stack_id, key, position in rows:
        if stack_id != current_stack:
            current_stack, index = stack_id, 0
        if position != index:
            moved.append((index, stack_id, key))
        index += 1
    conn.executemany(
        "UPDATE workflow_stack_member SET position = ? "
        "WHERE stack_id = ? AND workflow_key = ?",
        moved,
    )


def _drop_thin_stacks(conn: sqlite3.Connection) -> None:
    """Dissolve every stored stack left with fewer than two members.

    A stack of one is not a stack - the card stands on its own - and the read
    side already draws it that way, so a row saying otherwise is a row that
    will be believed by the next thing to read it (the recipe tab's
    ``effective_stack_keys`` reads membership before it reads the group).
    Members first: ``workflow_stack_member`` references ``workflow_stack``.
    """
    conn.execute(
        "DELETE FROM workflow_stack_member WHERE stack_id IN ("
        "SELECT stack_id FROM workflow_stack_member GROUP BY stack_id "
        "HAVING COUNT(*) < 2)"
    )
    conn.execute(
        "DELETE FROM workflow_stack WHERE stack_id NOT IN "
        "(SELECT stack_id FROM workflow_stack_member)"
    )
