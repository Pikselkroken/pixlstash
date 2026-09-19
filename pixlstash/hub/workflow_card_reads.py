"""What the hub answers about a workflow CARD (v1.12 B3).

:mod:`pixlstash.hub.workflow_cards` derives a card and writes its rows; this
module only reads them back, for the Workflows grid and for one card's detail.

**No aggregate table, and no join to the vault.** Everything that counts
pictures is computed per request in the vault
(:mod:`pixlstash.services.workflow_card_service`) and joined to these rows in
memory on a ``structural_hash``. That is the boundary ``routes/workflows.py``
already keeps for the topology list and it is kept for the same reason: the
rows here are content-addressed and hub-global, the counts belong to whichever
vault is attached, and a hash one side has never heard of is a workflow this
machine does not have rather than an error.

**A card spans exactly one topology.** ``workflow_key`` is digested over the
topology hash (``services/workflow_identity.workflow_key``), so every variant
of a card shares its topology - which is what lets one cached
``workflow_topology_core`` row describe the whole card, and what makes a slot
label mean the same thing for every variant of it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflow_cards import CORE_RULE_VERSION
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_identity import WORKFLOW_KEY_VERSION
from pixlstash.utils.sql_chunking import chunked

logger = get_logger(__name__)

# The prefix an automatic stack's id carries. An automatic grouping is not a
# row - it IS the set of cards sharing a ``core_hash`` - so ``auto:`` and that
# hash are the only thing that names one, and storing an ordered grouping
# under the same id is what makes ordering it idempotent. Defined here because
# three modules need the same literal:
# :mod:`pixlstash.services.workflow_card_service` produces it,
# :func:`keys_in_stack` below resolves it, and
# :mod:`pixlstash.hub.workflow_card_writes` writes it.
AUTO_STACK_PREFIX = "auto:"


@dataclass
class Card:
    """One card as the hub knows it, before any picture has been counted.

    ``file_name`` is the workflow file on this machine that runs this card,
    the alphabetically first where there are several, so a card names itself
    the same way on two reads of an unchanged hub.

    ``core_hash`` is ``None`` while the backfill has not reached this card's
    topology, or has reached it under a superseded rule. Such a card stacks
    with nothing rather than falling into a NULL bucket that would read as one
    enormous stack - the same choice ``workflow_cards.card_grouping`` makes.
    """

    workflow_key: str
    topology_hash: str
    core_hash: Optional[str] = None
    workflow_type: Optional[str] = None
    name: Optional[str] = None
    notes: Optional[str] = None
    hidden: bool = False
    imported: bool = False
    file_name: Optional[str] = None
    variants: list[str] = field(default_factory=list)
    slots: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class StackRows:
    """The owner's stack decisions, exactly as stored.

    ``members`` is ``{stack_id: [(position, workflow_key)]}`` and is NOT the
    effective grouping: a row for a card that has since left its automatic
    group is still here, and resolving that is
    :func:`pixlstash.services.workflow_card_service.effective_stacks`.
    """

    kinds: dict[str, str]
    core_hashes: dict[str, Optional[str]]
    members: dict[str, list[tuple[int, str]]]
    unstacked: frozenset[str]


def card_index(hub: HubDatabase) -> list[Card]:
    """Every card this hub holds, with its variants and what the owner said.

    One query over the variant table, left-joined to the topology cache and to
    the owner's attributes, grouped in memory. Variants are returned sorted so
    two reads of an unchanged hub answer identically.
    """
    rows = hub.fetchall(
        "SELECT v.workflow_key AS workflow_key, v.topology_hash AS topology_hash, "
        "v.structural_hash AS structural_hash, c.core_hash AS core_hash, "
        "c.workflow_type AS workflow_type, c.slots AS slots, "
        "a.name AS name, a.notes AS notes, "
        "a.hidden AS hidden, f.workflow_key IS NOT NULL AS imported, "
        "f.workflow_name AS file_name "
        "FROM workflow_variant v "
        "LEFT JOIN workflow_topology_core c ON c.topology_hash = v.topology_hash "
        "AND c.core_version = ? "
        "LEFT JOIN workflow_attr a ON a.workflow_key = v.workflow_key "
        "LEFT JOIN (SELECT workflow_key, MIN(workflow_name) AS workflow_name "
        "FROM workflow_file GROUP BY workflow_key) f "
        "ON f.workflow_key = v.workflow_key "
        "WHERE v.key_version = ? ORDER BY v.workflow_key, v.structural_hash",
        (CORE_RULE_VERSION, WORKFLOW_KEY_VERSION),
    )
    cards: dict[str, Card] = {}
    for row in rows:
        card = cards.get(row["workflow_key"])
        if card is None:
            card = cards[row["workflow_key"]] = Card(
                workflow_key=row["workflow_key"],
                topology_hash=row["topology_hash"],
                core_hash=row["core_hash"],
                workflow_type=row["workflow_type"],
                slots=_slots(row["slots"], row["workflow_key"]),
                name=row["name"],
                notes=row["notes"],
                hidden=bool(row["hidden"]),
                imported=bool(row["imported"]),
                file_name=row["file_name"],
            )
        card.variants.append(row["structural_hash"])
    return list(cards.values())


def _slots(raw: Optional[str], workflow_key: str) -> list[dict]:
    """The topology's cached slot list, or an empty one with a reason logged.

    ``workflow_topology_core.slots`` is written by the B2 backfill, so it is
    absent for a card the backfill has not reached and JSON by construction
    once it has. A card whose slots will not parse is described without its
    models rather than taking the grid down.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(
            "Cached slot list for card %s is not valid JSON, so it is "
            "described with no models: %s",
            workflow_key,
            exc,
        )
        return []
    return parsed if isinstance(parsed, list) else []


def slot_marks(
    hub: HubDatabase, topology_hashes: list[str]
) -> dict[tuple[str, str], str]:
    """``{(topology_hash, slot_label): mark}`` for every LoRA slot named.

    A slot with no row is one the backfill has not frozen yet; the caller reads
    that as ``recipe``, which is the direction the guess itself errs in.
    """
    marks = {}
    for batch in chunked(sorted(set(topology_hashes))):
        placeholders = ",".join("?" * len(batch))
        for topology_hash, slot_label, mark in hub.fetchall(
            "SELECT topology_hash, slot_label, mark FROM workflow_slot_mark "
            f"WHERE topology_hash IN ({placeholders})",
            tuple(batch),
        ):
            marks[(topology_hash, slot_label)] = mark
    return marks


def asset_names(
    hub: HubDatabase, structural_hashes: list[str]
) -> dict[str, list[tuple[str, str]]]:
    """``{structural_hash: [(widget_name, normalized_filename)]}``, sorted.

    **Keyed by widget and not by slot**, because that is how the hub stores a
    readable name: ``workflow_recipe_asset`` names the widget a file was given
    to, never the node. A topology naming the same widget on two loaders (two
    ``LoraLoader`` nodes, two ``lora_name`` values) therefore hands back two
    names for one widget and cannot say which loader each sat on; the caller
    pairs them in this sorted order, so the answer is deterministic and, where
    a widget appears once, exact.

    Resolving that properly means reducing the stored document per card, which
    is a Weisfeiler-Leman refinement apiece and would roughly double the grid's
    cost. It is worth doing when something depends on it: a *recipe* LoRA is
    drawn as an anonymous slot rather than by name, so today nothing does.
    """
    names: dict[str, list[tuple[str, str]]] = {}
    for batch in chunked(sorted(set(structural_hashes))):
        placeholders = ",".join("?" * len(batch))
        for structural_hash, widget, filename in hub.fetchall(
            "SELECT structural_hash, widget_name, normalized_filename "
            "FROM workflow_recipe_asset "
            f"WHERE structural_hash IN ({placeholders}) "
            "ORDER BY widget_name, normalized_filename",
            tuple(batch),
        ):
            names.setdefault(structural_hash, []).append((widget, filename))
    return names


def find_card(hub: HubDatabase, workflow_key: str) -> Optional[Card]:
    """One card by its key, or ``None`` when this hub has no such card."""
    return next(
        (card for card in card_index(hub) if card.workflow_key == workflow_key), None
    )


def stack_rows(hub: HubDatabase) -> StackRows:
    """Every stack, its members and every card taken out of its group."""
    stacks = hub.fetchall("SELECT stack_id, kind, core_hash FROM workflow_stack")
    members: dict[str, list[tuple[int, str]]] = {}
    for stack_id, position, key in hub.fetchall(
        "SELECT stack_id, position, workflow_key FROM workflow_stack_member "
        "ORDER BY stack_id, position"
    ):
        members.setdefault(stack_id, []).append((position, key))
    return StackRows(
        kinds={row["stack_id"]: row["kind"] for row in stacks},
        core_hashes={row["stack_id"]: row["core_hash"] for row in stacks},
        members=members,
        unstacked=frozenset(
            key
            for (key,) in hub.fetchall("SELECT workflow_key FROM workflow_unstacked")
        ),
    )


def chosen_covers(hub: HubDatabase, library_uuid: str) -> dict[str, str]:
    """``{workflow_key: pixel_sha}`` for the covers the owner picked here.

    By ``pixel_sha`` and not by picture id, because SQLite reuses a vault id the
    moment the next import lands, and by library because a picture is a picture
    in one vault.
    """
    return {
        key: pixel_sha
        for key, pixel_sha in hub.fetchall(
            "SELECT workflow_key, pixel_sha FROM workflow_cover WHERE library_uuid = ?",
            (library_uuid,),
        )
    }


def variant_documents(
    hub: HubDatabase, structural_hashes: list[str]
) -> dict[str, dict]:
    """The stored graph of each named variant, skipping any that will not parse.

    A row whose document is corrupt is logged and left out rather than raising:
    the grid it feeds describes every other card correctly, and the alternative
    is one unreadable row taking the whole view down.

    Chunked, because the caller sizes the list: a library with more variants
    than SQLite's bound-parameter cap would otherwise fail at execution time
    rather than answer slowly.
    """
    documents = {}
    for batch in chunked(sorted(set(structural_hashes))):
        placeholders = ",".join("?" * len(batch))
        for structural_hash, document in hub.fetchall(
            "SELECT structural_hash, document FROM workflow_recipe_graph "
            f"WHERE structural_hash IN ({placeholders})",
            tuple(batch),
        ):
            try:
                documents[structural_hash] = json.loads(document)
            except json.JSONDecodeError as exc:
                logger.error(
                    "Stored workflow document for variant %s is not valid JSON, "
                    "so its card is described without it: %s",
                    structural_hash,
                    exc,
                )
    return documents


def instance_documents(
    hub: HubDatabase, library_uuid: str, instance_hashes: list[str]
) -> list[tuple[str, dict]]:
    """``(structural_hash, document)`` for each named instance of this library.

    The instance document is the variant's graph with each parameter's value
    filled in, so it is where a default is read from - and it carries the
    variant it ran, because a slot label is a label within one topology and the
    node ids differ between variants of a card.

    Chunked for :func:`variant_documents`' reason, and more pressingly: this
    list is one entry per distinct run of a card, which on a card somebody uses
    every day is the largest caller-sized list in the whole read.
    """
    found = []
    for batch in chunked(sorted(set(instance_hashes))):
        placeholders = ",".join("?" * len(batch))
        for structural_hash, document in hub.fetchall(
            "SELECT structural_hash, document FROM workflow_recipe_instance "
            f"WHERE library_uuid = ? AND instance_hash IN ({placeholders})",
            (library_uuid, *batch),
        ):
            try:
                found.append((structural_hash, json.loads(document)))
            except json.JSONDecodeError as exc:
                logger.error(
                    "Stored instance document of variant %s is not valid JSON, "
                    "so it does not contribute a default: %s",
                    structural_hash,
                    exc,
                )
    return found


def default_overrides(
    hub: HubDatabase, workflow_key: str
) -> dict[tuple[str, str], str]:
    """``{(slot_label, input_name): value}`` the owner has set on this card."""
    return {
        (slot_label, input_name): value
        for slot_label, input_name, value in hub.fetchall(
            "SELECT slot_label, input_name, value FROM workflow_default_override "
            "WHERE workflow_key = ?",
            (workflow_key,),
        )
    }


def key_pins(hub: HubDatabase, workflow_key: str) -> Optional[list[tuple[str, str]]]:
    """The parameters the owner pinned on this card, or ``None`` for no choice.

    The read side of ``PUT /workflows/{key}/pins``. ``None`` and ``[]`` are
    different answers and the table keeps them apart: ``None`` is a card
    nobody has pinned on, so a client applies its own default pins, and ``[]``
    is somebody who unpinned everything.

    A row whose JSON will not parse is reported as no choice rather than
    raising: the pins decide which of a card's defaults show first, and a
    panel that will not open is a worse answer than one showing the default
    set. The hash is logged so the row can be found.
    """
    row = hub.fetchone(
        "SELECT pins FROM workflow_key_pins WHERE workflow_key = ?", (workflow_key,)
    )
    if row is None:
        return None
    try:
        stored = json.loads(row["pins"])
    except (TypeError, ValueError) as exc:
        logger.warning(
            "The pins of card %s will not parse, so the card reads as having "
            "none and its default pins apply: %s",
            workflow_key,
            exc,
        )
        return None
    return [
        (str(pin[0]), str(pin[1]))
        for pin in stored
        if isinstance(pin, (list, tuple)) and len(pin) == 2
    ]


def keys_in_stack(hub: HubDatabase, stack_id: str) -> list[str]:
    """Every card one stack holds, in order; empty when it holds none.

    Both kinds of stack answer here, because both can be addressed by a write
    and only one of them is a row:

    * a **stored** stack (manual, or an automatic group somebody has ordered)
      has a ``workflow_stack_member`` row per card;
    * an automatic grouping that nobody has ordered is not a row at all - it
      IS the set of cards sharing a ``core_hash``, and ``auto:<core_hash>`` is
      the only thing that names it
      (:data:`pixlstash.hub.workflow_card_writes.AUTO_STACK_PREFIX`).

    The automatic half subtracts the cards that have left the group, the way
    :func:`pixlstash.hub.workflow_cards.effective_stack_keys` does: a stored
    membership elsewhere or a ``workflow_unstacked`` row both mean this group
    is no longer where that card sits, and dissolving a group would otherwise
    write an ``unstacked`` row for a card that is not in it.
    """
    stored = [
        key
        for (key,) in hub.fetchall(
            "SELECT workflow_key FROM workflow_stack_member WHERE stack_id = ? "
            "ORDER BY position, workflow_key",
            (stack_id,),
        )
    ]
    if stored or not stack_id.startswith(AUTO_STACK_PREFIX):
        return stored
    return [
        key
        for (key,) in hub.fetchall(
            "SELECT DISTINCT v.workflow_key FROM workflow_variant v "
            "JOIN workflow_topology_core c ON c.topology_hash = v.topology_hash "
            "AND c.core_version = ? "
            "WHERE v.key_version = ? AND c.core_hash = ? "
            "AND v.workflow_key NOT IN (SELECT workflow_key FROM workflow_stack_member) "
            "AND v.workflow_key NOT IN (SELECT workflow_key FROM workflow_unstacked) "
            "ORDER BY v.workflow_key",
            (
                CORE_RULE_VERSION,
                WORKFLOW_KEY_VERSION,
                stack_id[len(AUTO_STACK_PREFIX) :],
            ),
        )
    ]


def picture_inputs(
    hub: HubDatabase, library_uuid: str, workflow_key: str
) -> list[dict]:
    """How each picture input of a card is filled, as the owner set it (B4).

    The read side of ``PUT /workflows/{key}/inputs``. Scoped to one library for
    the reason the table is keyed that way: a ``fixed`` row names a picture by
    ``pixel_sha`` in ONE vault, and handing another library's setup to a run
    would have it look for a picture that library does not hold.

    Returns:
        ``[{slot_label, input_name, mode, pixel_sha}]``, ordered so two reads
        of an unchanged hub answer identically.
    """
    return [
        {
            "slot_label": slot_label,
            "input_name": input_name,
            "mode": mode,
            "pixel_sha": pixel_sha,
        }
        for slot_label, input_name, mode, pixel_sha in hub.fetchall(
            "SELECT slot_label, input_name, mode, pixel_sha "
            "FROM workflow_key_picture_input "
            "WHERE library_uuid = ? AND workflow_key = ? "
            "ORDER BY slot_label, input_name",
            (library_uuid, workflow_key),
        )
    ]
