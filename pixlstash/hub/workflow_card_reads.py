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


@dataclass
class Card:
    """One card as the hub knows it, before any picture has been counted.

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
    variants: list[str] = field(default_factory=list)


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
        "c.workflow_type AS workflow_type, a.name AS name, a.notes AS notes, "
        "a.hidden AS hidden, f.workflow_key IS NOT NULL AS imported "
        "FROM workflow_variant v "
        "LEFT JOIN workflow_topology_core c ON c.topology_hash = v.topology_hash "
        "AND c.core_version = ? "
        "LEFT JOIN workflow_attr a ON a.workflow_key = v.workflow_key "
        "LEFT JOIN (SELECT DISTINCT workflow_key FROM workflow_file) f "
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
                name=row["name"],
                notes=row["notes"],
                hidden=bool(row["hidden"]),
                imported=bool(row["imported"]),
            )
        card.variants.append(row["structural_hash"])
    return list(cards.values())


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
