"""What the owner writes about a workflow CARD (v1.12 B4).

:mod:`pixlstash.hub.workflow_cards` derives a card from a stored document and
:mod:`pixlstash.hub.workflow_card_reads` reads the rows back; this module is
the other half - the owner's own decisions about a card, and the only place
that writes them.

**Nothing here decides anything about identity.** A key is
``services/workflow_identity.workflow_key`` and a stack is the automatic
grouping resolved in ``workflow_cards.effective_stack_keys``; this module takes
the keys its caller resolved and writes rows.

**One transaction per logical write**, so a crash can never leave attributes on
a key no variant is on, or a stack row with no members. The mark flip is the
one that needs it: it re-keys every variant of a topology AND carries the
owner's attributes across in the same breath, and either half alone is a card
somebody has named, pinned and stacked losing its name.

**Every user-attribute table is keyed on ``workflow_key`` and that is what
makes the carry-over one loop** (:data:`_KEYED_TABLES`). A table added to the
card's vocabulary belongs in that tuple, or a mark flip silently drops it -
which is the failure this module is shaped to make impossible to forget.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflow_card_reads import AUTO_STACK_PREFIX
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_hash import WorkflowGraphError
from pixlstash.services.workflow_identity import (
    STRUCTURAL,
    WORKFLOW_KEY_VERSION,
    slots,
    workflow_key,
)

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


class _Unset:
    """Sentinel: this attribute was not in the request, so it stands."""


UNSET = _Unset()


def set_attributes(
    hub: HubDatabase,
    key: str,
    name=UNSET,
    notes=UNSET,
    hidden=UNSET,
) -> None:
    """Write the attributes the request carried; the rest stand.

    ``None`` for *name* or *notes* clears it, which is the column's own
    default (no name of the owner's own, no notes) and not the same as leaving
    the field out.
    """
    fields = {"name": name, "notes": notes, "hidden": hidden}
    given = {
        column: (int(bool(value)) if column == "hidden" else value)
        for column, value in fields.items()
        if not isinstance(value, _Unset)
    }
    if not given:
        return
    columns = ", ".join(given)
    placeholders = ", ".join("?" for _ in given)
    updates = ", ".join(f"{column} = excluded.{column}" for column in given)
    with hub.transaction() as conn:
        conn.execute(
            f"INSERT INTO workflow_attr (workflow_key, {columns}) "
            f"VALUES (?, {placeholders}) "
            f"ON CONFLICT(workflow_key) DO UPDATE SET {updates}",
            (key, *given.values()),
        )


def replace_defaults(
    hub: HubDatabase, key: str, defaults: list[tuple[str, str, str]]
) -> None:
    """Set this card's whole override set: ``[(slot_label, input_name, value)]``.

    Whole rather than per address, because the form the owner edits shows every
    featured parameter at once and an empty list is somebody clearing them all.
    """
    with hub.transaction() as conn:
        conn.execute(
            "DELETE FROM workflow_default_override WHERE workflow_key = ?", (key,)
        )
        conn.executemany(
            "INSERT INTO workflow_default_override "
            "(workflow_key, slot_label, input_name, value) VALUES (?, ?, ?, ?)",
            [
                (key, slot_label, input_name, value)
                for slot_label, input_name, value in defaults
            ],
        )


def replace_pins(
    hub: HubDatabase, key: str, pins: Optional[list[tuple[str, str]]]
) -> None:
    """Store this card's pins whole, or forget them with ``None``.

    A row holding ``[]`` is somebody who unpinned everything, which the schema
    distinguishes from never having pinned - so an empty list writes a row and
    ``None`` deletes it.
    """
    with hub.transaction() as conn:
        if pins is None:
            conn.execute("DELETE FROM workflow_key_pins WHERE workflow_key = ?", (key,))
            return
        conn.execute(
            "INSERT INTO workflow_key_pins (workflow_key, pins) VALUES (?, ?) "
            "ON CONFLICT(workflow_key) DO UPDATE SET pins = excluded.pins",
            (key, json.dumps([list(pin) for pin in pins])),
        )


def replace_picture_inputs(
    hub: HubDatabase,
    library_uuid: str,
    key: str,
    inputs: list[tuple[str, str, str, Optional[str]]],
) -> None:
    """Set how this card's picture inputs are filled, in ONE library.

    ``inputs`` is ``[(slot_label, input_name, mode, pixel_sha)]``. Scoped to
    *library_uuid* because a picture is a picture in one vault: a second
    library gets its own setup rather than a picture it does not hold.
    """
    with hub.transaction() as conn:
        conn.execute(
            "DELETE FROM workflow_key_picture_input "
            "WHERE library_uuid = ? AND workflow_key = ?",
            (library_uuid, key),
        )
        conn.executemany(
            "INSERT INTO workflow_key_picture_input (library_uuid, workflow_key, "
            "slot_label, input_name, mode, pixel_sha) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (library_uuid, key, slot_label, input_name, mode, pixel_sha)
                for slot_label, input_name, mode, pixel_sha in inputs
            ],
        )


def flip_slot_marks(
    hub: HubDatabase,
    topology_hash: str,
    marks: dict[str, str],
    variant_pictures: dict[str, int],
) -> dict[str, list[str]]:
    """Re-mark LoRA slots of one topology and re-key its cards, carrying over.

    A mark decides whether a LoRA slot reaches the card key
    (``workflow_identity.workflow_key``), so flipping one re-keys every variant
    of the topology. **That re-keying and the carry-over are one transaction**:
    a card the owner has named, pinned and stacked must arrive on its new key
    with all of it, or the flip is a destructive operation dressed as a
    correction.

    Which way the cards move decides what "carrying over" means, and both
    directions are here rather than in the caller:

    * a **split** (``recipe`` → ``structural``: the LoRA now reaches the key)
      turns one card into several, and every new key inherits the attributes;
    * a **merge** (``structural`` → ``recipe``) folds several into one, and the
      member with the most pictures wins - ties broken on the key, because an
      arbitrary winner is a card whose name depends on row order.

    Args:
        topology_hash: The topology whose slots are being marked.
        marks: ``{slot_label: "structural" | "recipe"}``. A slot not named
            keeps the mark it has; a label this topology does not have is
            written and ignored by every reader, so callers validate first.
        variant_pictures: ``{structural_hash: kept pictures}``, read from the
            vault by the caller. A variant missing from it counts zero, which
            is what an unscanned library looks like.

    Returns:
        ``{old key: [new key, ...]}`` for every card that moved, biggest
        successor first, and empty when the flip changed nothing. A list
        because a split has several: a caller following the card somebody was
        looking at takes the first, which is the one with most of its
        pictures. A variant whose stored document will not reduce is logged
        and left on the key it has.
    """
    documents = {}
    for structural_hash, raw in hub.fetchall(
        "SELECT r.structural_hash, g.document FROM workflow_recipe r "
        "JOIN workflow_recipe_graph g ON g.structural_hash = r.structural_hash "
        "WHERE r.topology_hash = ?",
        (topology_hash,),
    ):
        try:
            documents[structural_hash] = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(
                "Stored document of variant %s will not parse, so the mark "
                "flip leaves it on the card it is on: %s",
                structural_hash,
                exc,
            )

    with hub.transaction() as conn:
        conn.executemany(
            "INSERT INTO workflow_slot_mark (topology_hash, slot_label, mark) "
            "VALUES (?, ?, ?) ON CONFLICT(topology_hash, slot_label) "
            "DO UPDATE SET mark = excluded.mark",
            [(topology_hash, label, mark) for label, mark in marks.items()],
        )
        structural_labels = {
            label
            for label, mark in conn.execute(
                "SELECT slot_label, mark FROM workflow_slot_mark "
                "WHERE topology_hash = ?",
                (topology_hash,),
            ).fetchall()
            if mark == STRUCTURAL
        }
        moved = _rekey_variants(
            conn, topology_hash, documents, structural_labels, variant_pictures
        )
    if moved:
        logger.info(
            "A slot-mark flip on topology %s moved %d card(s) to new keys.",
            topology_hash,
            len(moved),
        )
    return moved


def _rekey_variants(
    conn: sqlite3.Connection,
    topology_hash: str,
    documents: dict[str, dict],
    structural_labels: set[str],
    variant_pictures: dict[str, int],
) -> dict[str, list[str]]:
    """Move every variant of one topology onto the key the marks now give it."""
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
                topology_hash, slots(document), structural_labels
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
    pictures_per_new = _pictures_per_key(new_keys, variant_pictures)

    # Smallest successor first, which only matters for a split: each copy of a
    # stack membership goes just behind the source and pushes the previous copy
    # back, so copying in this order leaves the biggest half nearest the front
    # - the same half `SlotMarkResult.key` sends the caller to, rather than
    # whichever digest happens to sort first.
    for new_key, olds in sorted(
        contributors.items(),
        key=lambda item: (pictures_per_new.get(item[0], 0), item[0]),
    ):
        winner = min(olds, key=lambda key: (-pictures_per_old.get(key, 0), key))
        if winner == new_key:
            continue
        for table in _KEYED_TABLES:
            # The target first: a key being written onto can only hold rows a
            # previous flip left there, and the winner's are the ones the
            # owner has been editing since.
            conn.execute(f"DELETE FROM {table} WHERE workflow_key = ?", (new_key,))
            _copy_rows(conn, table, winner, new_key)

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

    moved = {
        old_key: sorted(news, key=lambda key: (-pictures_per_new.get(key, 0), key))
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


def stack_together(hub: HubDatabase, keys: list[str]) -> str:
    """Put these cards in one manual stack, in this order; return its id.

    *keys* is the resolved member list in the order the owner selected them,
    so ``keys[0]`` is the cover - which is what "merging keeps the first
    selected stack's name and cover" comes to, a stack having no name and no
    cover of its own beyond its first member's.

    Each card leaves whatever stack it was in, and its ``workflow_unstacked``
    row goes with it: stacking a card by hand is the owner reversing the
    decision to keep it out of a group, so leaving the row would take it back
    out the moment this stack dissolved.
    """
    stack_id = uuid.uuid4().hex
    with hub.transaction() as conn:
        conn.executemany(
            "DELETE FROM workflow_stack_member WHERE workflow_key = ?",
            [(key,) for key in keys],
        )
        conn.executemany(
            "DELETE FROM workflow_unstacked WHERE workflow_key = ?",
            [(key,) for key in keys],
        )
        conn.execute(
            "INSERT INTO workflow_stack (stack_id, kind, core_hash) "
            "VALUES (?, 'manual', NULL)",
            (stack_id,),
        )
        conn.executemany(
            "INSERT INTO workflow_stack_member (stack_id, workflow_key, position) "
            "VALUES (?, ?, ?)",
            [(stack_id, key, position) for position, key in enumerate(keys)],
        )
        _drop_thin_stacks(conn)
    return stack_id


def set_stack_order(hub: HubDatabase, stack_id: str, keys: list[str]) -> None:
    """Set one stack's member order, ``keys[0]`` the cover.

    An automatic grouping is not a row until somebody orders it, so this is
    also what materialises one: the id carries its core hash
    (:data:`AUTO_STACK_PREFIX`) and the row is written on first use. A manual
    stack's row already exists and keeps its kind.
    """
    is_auto = stack_id.startswith(AUTO_STACK_PREFIX)
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_stack (stack_id, kind, core_hash) VALUES (?, ?, ?) "
            "ON CONFLICT(stack_id) DO NOTHING",
            (
                stack_id,
                "auto" if is_auto else "manual",
                stack_id[len(AUTO_STACK_PREFIX) :] if is_auto else None,
            ),
        )
        conn.execute(
            "DELETE FROM workflow_stack_member WHERE stack_id = ?", (stack_id,)
        )
        # By key as well as by stack, exactly as `stack_together` does. The
        # primary key `(stack_id, workflow_key)` does not stop a card holding
        # two memberships, and `effective_stack_keys` orders by `stack_id` to
        # make that state *reproducible* rather than correct - so a card
        # ordered into this stack has to leave the one it was in, or the two
        # answers disagree about which stack it is in.
        conn.executemany(
            "DELETE FROM workflow_stack_member WHERE workflow_key = ?",
            [(key,) for key in keys],
        )
        conn.executemany(
            "INSERT INTO workflow_stack_member (stack_id, workflow_key, position) "
            "VALUES (?, ?, ?)",
            [(stack_id, key, position) for position, key in enumerate(keys)],
        )
        _drop_thin_stacks(conn)


def unstack_stack(hub: HubDatabase, stack_id: str, keys: list[str]) -> None:
    """Dissolve one whole stack: every member stands on its own afterwards.

    *keys* is the effective membership the caller resolved, which for an
    automatic grouping is not stored anywhere - hence the
    ``workflow_unstacked`` rows: without them the group re-forms on the next
    read and the gesture reads as having done nothing.
    """
    with hub.transaction() as conn:
        conn.execute(
            "DELETE FROM workflow_stack_member WHERE stack_id = ?", (stack_id,)
        )
        conn.execute("DELETE FROM workflow_stack WHERE stack_id = ?", (stack_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO workflow_unstacked (workflow_key) VALUES (?)",
            [(key,) for key in keys],
        )


def unstack_card(hub: HubDatabase, key: str) -> None:
    """Take one card out of its stack, leaving the rest of the stack standing.

    The remaining members keep their stack unless one is left alone, in which
    case it dissolves - and that card returns to its automatic group, because
    it was stacked by hand and never taken out of one.
    """
    with hub.transaction() as conn:
        conn.execute("DELETE FROM workflow_stack_member WHERE workflow_key = ?", (key,))
        conn.execute(
            "INSERT OR IGNORE INTO workflow_unstacked (workflow_key) VALUES (?)",
            (key,),
        )
        _drop_thin_stacks(conn)
