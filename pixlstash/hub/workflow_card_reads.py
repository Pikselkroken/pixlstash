"""What the hub answers about cards and the workflows they make (B3, #1623).

:mod:`pixlstash.hub.workflow_cards` derives a card and writes its rows; this
module reads them back and groups them into WORKFLOWS (``workflow_index``),
which is what the Workflows grid and every route show. The card-table reads
kept below the workflow ones (``stack_rows``, ``key_pins``,
``default_overrides``, ``slot_marks``) are the cut-over conversion's; no route
reads them.

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
from pixlstash.hub.workflow_cards import (
    CORE_RULE_VERSION,
    STRIP_LORAS_FOR_STACKS,
    auto_workflow_id,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_hash import (
    WorkflowGraphError,
    asset_reference,
    normalized_filename,
    reduce_api_graph,
    reduce_ui_graph,
)
from pixlstash.services.workflow_identity import (
    NEGATIVE_PROMPT,
    WORKFLOW_KEY_VERSION,
    model_fix_kind,
    reduced_traits,
    reduced_workflow_type,
    slots,
)
from pixlstash.services import workflow_bindings
from pixlstash.services.workflow_io import api_graph, with_converted_graph
from pixlstash.utils.sql_chunking import chunked
from pixlstash.utils.workflow_ids import AUTO_PREFIX

logger = get_logger(__name__)

# The prefix an automatic workflow's id carries. An automatic grouping is not a
# row - it IS the set of topologies sharing a ``core_hash`` - so ``auto:`` and
# that hash are the only thing that names one. A manual workflow is a row,
# ``workflow_document``, named ``manual:<uuid hex>``.
AUTO_STACK_PREFIX = AUTO_PREFIX


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

    ``specials`` is the same shape of answer one column over, and ``None`` is
    NOT the empty tuple: ``None`` means the pass has not said yet, ``()`` means
    it said "none". Only the second lets a name claim the workflow is plain.
    """

    workflow_key: str
    topology_hash: str
    core_hash: Optional[str] = None
    workflow_type: Optional[str] = None
    specials: Optional[tuple[str, ...]] = None
    # What the core does beyond its type (``workflow_identity.graph_traits``),
    # with the same ``None`` rule as ``specials``.
    traits: Optional[tuple[str, ...]] = None
    name: Optional[str] = None
    notes: Optional[str] = None
    hidden: bool = False
    imported: bool = False
    file_name: Optional[str] = None
    variants: list[str] = field(default_factory=list)
    # Each variant's base-model families (``workflow_variant_family``), None
    # while the backfill has not derived them: part of its workflow's id.
    families: dict[str, Optional[str]] = field(default_factory=dict)
    slots: list[dict] = field(default_factory=list)
    # A manual workflow's card (``workflow_document``): its own record, with
    # the id as both key and topology, and the workflow it was made from.
    manual: bool = False
    from_name: Optional[str] = None
    # How a manual workflow arrived (``workflow_document.origin``: ``import``,
    # ``duplicate``, ..., and ``pull`` for one the removed pull of ComfyUI's
    # saved workflows made); ``None`` for an automatic one.
    origin: Optional[str] = None
    # A manual workflow's versions (``workflow_version``): how many are kept,
    # the current one's number (past the 50 kept, more than ``versions``), and
    # when it was made. A workflow made before versions has its one.
    versions: int = 1
    version: int = 1
    version_at: Optional[str] = None
    # When a manual workflow was stored (``workflow_document.created_at``).
    created_at: Optional[str] = None


@dataclass(frozen=True)
class StackRows:
    """The owner's stack decisions, exactly as stored.

    ``members`` is ``{stack_id: [(position, workflow_key)]}`` and is NOT the
    effective grouping: a row for a card that has since left its automatic
    group is still here. Read once, by the cut-over's conversion.
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

    Then one more small query for the manual workflows, each its own card
    with no variant (:func:`_manual_cards`).
    """
    rows = hub.fetchall(
        "SELECT v.workflow_key AS workflow_key, v.topology_hash AS topology_hash, "
        "v.structural_hash AS structural_hash, c.core_hash AS core_hash, "
        "c.workflow_type AS workflow_type, c.slots AS slots, "
        "c.specials AS specials, c.traits AS traits, "
        "a.name AS name, a.notes AS notes, "
        "a.hidden AS hidden, f.workflow_key IS NOT NULL AS imported, "
        "f.workflow_name AS file_name, vf.families AS families "
        "FROM workflow_variant v "
        "LEFT JOIN workflow_topology_core c ON c.topology_hash = v.topology_hash "
        "AND c.core_version = ? "
        "LEFT JOIN workflow_variant_family vf "
        "ON vf.structural_hash = v.structural_hash "
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
                specials=_specials(row["specials"]),
                traits=_specials(row["traits"]),
                slots=_slots(row["slots"], row["workflow_key"]),
                name=row["name"],
                notes=row["notes"],
                hidden=bool(row["hidden"]),
                imported=bool(row["imported"]),
                file_name=row["file_name"],
            )
        card.variants.append(row["structural_hash"])
        card.families[row["structural_hash"]] = row["families"]
    return list(cards.values()) + _manual_cards(hub)


def _manual_cards(hub: HubDatabase) -> list[Card]:
    """One card per manual workflow: its own ``workflow_document`` row.

    **Never on a topology.** ``topology_hash`` is the workflow's own id, so
    nothing keyed per topology (a model fix, a slot mark, a core hash) can
    reach it from an automatic workflow or reach an automatic one from it. No
    variants: the document is the whole of it. Always ``imported``, so a
    stored workflow is never folded into the one-offs.
    """
    rows = hub.fetchall(
        "SELECT d.workflow_id, d.origin, d.from_name, d.document, d.created_at, "
        "MAX(1, (SELECT COUNT(*) FROM workflow_version v "
        "WHERE v.workflow_id = d.workflow_id)) AS versions, "
        "COALESCE((SELECT v.created_at FROM workflow_version v "
        "WHERE v.workflow_id = d.workflow_id ORDER BY v.version DESC LIMIT 1), "
        "d.created_at) AS version_at, "
        "(SELECT MAX(v.version) FROM workflow_version v "
        "WHERE v.workflow_id = d.workflow_id) AS current_version "
        "FROM workflow_document d ORDER BY d.workflow_id"
    )
    # A deleted workflow's description goes with it.
    for gone in set(_MANUAL_FACTS) - {row["workflow_id"] for row in rows}:
        _MANUAL_FACTS.pop(gone, None)
    return [
        Card(
            workflow_key=row["workflow_id"],
            topology_hash=row["workflow_id"],
            workflow_type=facts[0],
            traits=facts[1],
            imported=True,
            manual=True,
            from_name=row["from_name"],
            origin=row["origin"],
            versions=row["versions"],
            version=row["current_version"] or 1,
            version_at=row["version_at"],
            created_at=row["created_at"],
        )
        for row in rows
        for facts in (
            _manual_facts_of(
                row["workflow_id"],
                row["document"],
                (row["current_version"], row["version_at"]),
            ),
        )
    ]


# ``{workflow id: (version, (workflow_type, traits))}``: one entry per manual
# workflow, replaced when a save makes a new version (`hub/workflow_versions.py`)
# and dropped when the workflow is (`_manual_cards`), so no document is held
# and an old version is never described.
_MANUAL_FACTS: dict[str, tuple[object, tuple]] = {}


def _manual_facts_of(
    workflow_id: str, document: str, version: object = None
) -> tuple[Optional[str], Optional[tuple[str, ...]]]:
    """``(workflow_type, traits)`` of a manual workflow, parsed once per version.

    *version* names the current version: ``(number, stored at)`` from the
    grid, the time because a workflow made from a file reuses its id
    (`hub/workflow_group_convert.py`), so a deleted one's version 1 and its
    successor's differ only in when they were stored. Both ``None`` for a
    graph that will not reduce, as for an automatic card the backfill has not
    reached.
    """
    cached = _MANUAL_FACTS.get(workflow_id)
    if cached is not None and cached[0] == version:
        return cached[1]
    nodes = _manual_reduction(workflow_id, document)
    facts = (None, None) if nodes is None else _manual_facts(workflow_id, nodes)
    _MANUAL_FACTS[workflow_id] = (version, facts)
    return facts


def _manual_facts(
    workflow_id: str, nodes: dict
) -> tuple[Optional[str], Optional[tuple[str, ...]]]:
    """Type and traits of a reduced manual graph, read apart: either one is
    ``None``, logged, if a degenerate graph trips it, so one failed read
    neither blanks the other nor takes the grid down."""
    try:
        workflow_type = reduced_workflow_type(nodes)
    except (WorkflowGraphError, ValueError, TypeError, KeyError) as exc:
        logger.warning(
            "Manual workflow %s: its graph could not be read for its type, so "
            "it shows none: %s",
            workflow_id,
            exc,
        )
        workflow_type = None
    try:
        traits = reduced_traits(nodes, strip_loras=STRIP_LORAS_FOR_STACKS)
    except (WorkflowGraphError, ValueError, TypeError, KeyError) as exc:
        logger.warning(
            "Manual workflow %s: its core strip failed, so its name says no traits: %s",
            workflow_id,
            exc,
        )
        traits = None
    return workflow_type, traits


def _manual_reduction(workflow_id: str, document: str) -> Optional[dict]:
    """A manual workflow's own graph, reduced; ``None`` (logged) if it won't.

    Either serialisation: an editor file reduces as the UI graph it is.
    """
    try:
        parsed = json.loads(document)
        graph = api_graph(parsed)
        return reduce_api_graph(graph) if graph is not None else reduce_ui_graph(parsed)
    except (
        ValueError,
        TypeError,
        WorkflowGraphError,
        RecursionError,
        AttributeError,
    ) as exc:
        logger.warning(
            "Manual workflow %s: its graph will not reduce, so it shows no type: %s",
            workflow_id,
            exc,
        )
        return None


def workflow_id_successors(hub: HubDatabase) -> dict[str, str]:
    """``{retired workflow id: the workflow that took its place}``."""
    return {
        row[0]: row[1]
        for row in hub.fetchall(
            "SELECT workflow_id, successor_id FROM workflow_id_successor"
        )
    }


def moved_card_workflows(hub: HubDatabase) -> dict[str, str]:
    """``{"<workflow key> <old workflow id>": new workflow id}`` per card a
    family pass moved out of a workflow that lives on (``workflow_card_move``).
    """
    return {
        f"{row[0]} {row[1]}": row[2]
        for row in hub.fetchall(
            "SELECT workflow_key, old_workflow_id, new_workflow_id "
            "FROM workflow_card_move"
        )
    }


def manual_workflow_ids(hub: HubDatabase) -> list[str]:
    """Every live manual workflow's id, sorted: what the vault reads file under."""
    return [
        row[0]
        for row in hub.fetchall(
            "SELECT workflow_id FROM workflow_document ORDER BY workflow_id"
        )
    ]


def adopted_file_variants(hub: HubDatabase) -> set[str]:
    """Every variant whose files were ALL made live manual workflows (#1720).

    Hub data step 7 leaves the ``workflow_file`` rows it adopts in place, so
    such a variant still reads as an automatic workflow beside the manual one
    of the same name. Per variant and not per card: one card's variants can
    sit in automatic workflows of several families, and only the one the file
    is has a manual twin. A file is adopted while a ``file`` origin row names
    it and the manual workflow that row points at still exists. A UI-format
    file (no ``structural_hash``) has no variant, so no twin.
    """
    return {
        row[0]
        for row in hub.fetchall(
            "SELECT f.structural_hash FROM workflow_file f "
            "LEFT JOIN workflow_origin o ON o.origin = 'file' "
            "AND o.remote_path = f.workflow_name AND o.dismissed = 0 "
            "AND o.workflow_name IN (SELECT workflow_id FROM workflow_document) "
            "WHERE f.structural_hash IS NOT NULL "
            "GROUP BY f.structural_hash HAVING MIN(o.workflow_name IS NOT NULL) = 1"
        )
    }


def is_manual_workflow(hub: HubDatabase, workflow_id: str) -> bool:
    """Whether this hub holds the manual workflow *workflow_id*."""
    return (
        hub.fetchone(
            "SELECT 1 FROM workflow_document WHERE workflow_id = ?", (workflow_id,)
        )
        is not None
    )


def manual_documents_holding(hub: HubDatabase, canonical: str) -> list[str]:
    """Every manual workflow whose document is *canonical*, sorted by id.

    ``canonical`` is ``workflow_bindings.canonical`` of a placeholder-migrated
    document, which is how every row was stored.
    """
    # ponytail: parses every manual document per call; a stored content hash
    # column if conversions (the one caller) ever meet a large library.
    found = []
    for row in hub.fetchall(
        "SELECT workflow_id, document FROM workflow_document ORDER BY workflow_id"
    ):
        try:
            if workflow_bindings.canonical(json.loads(row["document"])) == canonical:
                found.append(row["workflow_id"])
        except (ValueError, RecursionError, AttributeError) as exc:
            # AttributeError: valid JSON that is not an object.
            logger.warning(
                "Manual workflow %s will not read to compare: %s",
                row["workflow_id"],
                exc,
            )
    return found


def manual_document(hub: HubDatabase, workflow_id: str) -> Optional[dict]:
    """A manual workflow's runnable document, or ``None`` for no such row.

    The stored document, or the API graph ComfyUI converted it into, exactly
    as ``routes/comfyui.runnable_document`` reads a file and its sidecar. A
    row that will not parse is logged and answers ``None``: the caller then
    reports no runnable source rather than raising.
    """
    return manual_document_and_version(hub, workflow_id)[0]


def manual_document_and_version(
    hub: HubDatabase, workflow_id: str
) -> tuple[Optional[dict], Optional[int]]:
    """:func:`manual_document`, and the number of the version it is.

    Read in one statement, so the number is the version of the document
    returned even while a save appends another. A workflow an older build made
    has no version row: its document is version 1. ``(None, None)`` for no
    such workflow.
    """
    row = hub.fetchone(
        "SELECT d.document, d.api_document, "
        "COALESCE((SELECT MAX(v.version) FROM workflow_version v "
        "WHERE v.workflow_id = d.workflow_id), 1) AS version "
        "FROM workflow_document d WHERE d.workflow_id = ?",
        (workflow_id,),
    )
    if row is None:
        return None, None
    return _runnable(workflow_id, row), row["version"]


def _runnable(workflow_id: str, row) -> Optional[dict]:
    """The runnable document of one ``workflow_document`` row (:func:`manual_document`)."""
    try:
        document = json.loads(row["document"])
    except json.JSONDecodeError as exc:
        logger.error(
            "Stored document of manual workflow %s is not valid JSON, so it has "
            "no graph to run: %s",
            workflow_id,
            exc,
        )
        return None
    converted = None
    if row["api_document"]:
        try:
            converted = json.loads(row["api_document"])
        except json.JSONDecodeError as exc:
            # The conversion only; the stored document still runs.
            logger.error(
                "Stored API document of manual workflow %s is not valid JSON; its "
                "stored document runs instead: %s",
                workflow_id,
                exc,
            )
    if not isinstance(document, dict):
        logger.error("Manual workflow %s holds a non-object document.", workflow_id)
        return None
    if converted is not None and not isinstance(converted, dict):
        logger.error(
            "Manual workflow %s holds a non-object API document; its stored "
            "document runs instead.",
            workflow_id,
        )
        converted = None
    return with_converted_graph(document, converted)


def _specials(raw: Optional[str]) -> Optional[tuple[str, ...]]:
    """The cached post-processing list, or ``None`` where the pass has not run.

    The empty string is a real answer ("this graph has none") and comes back as
    an empty tuple, which is why this cannot be ``raw.split(",") if raw else
    None``: that spelling loses the difference the column exists to keep.
    """
    if raw is None:
        return None
    return tuple(part for part in raw.split(",") if part)


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
    names for one widget and cannot say which loader each sat on. A caller
    that needs the loader matches by reference instead: the stored document
    carries each file as ``asset_reference(filename)`` in the node it was wired
    into. The grid does so only for a card with a model widget on two loaders
    (``workflow_card_service._wired_names``, #1691), since reducing every
    card's document would roughly double its cost. Nothing pairs a *recipe*
    LoRA to its slot: the card summarises every variant's recipe LoRAs as one
    list.
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

    **An unreadable row answers ``None``, never ``[]``.** The two mean
    different things to every caller, and reporting corruption as "the owner
    unpinned everything" would put a card's whole parameter list behind a
    collapsed disclosure and look deliberate. ``None`` is the state that
    degrades to the client's defaults, which is what a card nobody has touched
    already does. Everything rejected is logged with the key, because a silent
    drop here is a pin the owner set and cannot see.
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
            "made no choice and its default pins apply: %s",
            workflow_key,
            exc,
        )
        return None
    # Type-checked outside the try, because a stored `null`, number or object
    # parses perfectly well and then is not a pin list: iterating it raised a
    # TypeError out of the detail route, which is a 500 on the panel this
    # function exists to keep openable.
    if not isinstance(stored, list):
        logger.warning(
            "The pins of card %s parsed as %s rather than a list, so the card "
            "reads as having made no choice and its default pins apply.",
            workflow_key,
            type(stored).__name__,
        )
        return None
    pins = []
    for pin in stored:
        if isinstance(pin, (list, tuple)) and len(pin) == 2:
            pins.append((str(pin[0]), str(pin[1])))
        else:
            logger.warning(
                "A pin of card %s is %r rather than a (slot label, input name) "
                "pair, so it is left out; that parameter will not be pinned.",
                workflow_key,
                pin,
            )
    return pins


# ---------------------------------------------------------------------------
# Workflows as the owner sees them (#1622): variant -> topology -> workflow.
#
# What every route reads since the cut-over (#1623). An AUTOMATIC workflow is
# a group of VARIANTS sharing a core hash under the rule this build applies
# and a set of base-model families (``workflow_cards.auto_workflow_id``), so
# one topology can be in several (a ``workflow_group_member`` placement first,
# which data step 6 emptied). A variant with no core hash or no families - the
# backfill has not reached it, or it was cached under a superseded rule - is
# in no workflow yet, for the reason
# ``Card.core_hash`` gives: a NULL bucket would read as one enormous workflow.
# A MANUAL workflow is its own ``workflow_document`` row, ``manual:<uuid>``,
# on no topology: it never joins an automatic one and none absorbs it.
# ---------------------------------------------------------------------------


@dataclass
class Workflow:
    """One workflow as the hub knows it, before any picture has been counted.

    ``base_topology`` is the graph a run starts from and an export writes: a
    topology with kept pictures in this library before one without (#1738),
    then the most stage groups, then the most LoRA loaders, then the most kept
    pictures (#1620 D3). Automatic, with no owner control. Picture counts
    belong to the vault, so the base is per library, and without them both
    picture keys are skipped and the topology hash decides, which keeps a
    hub-only answer stable.

    ``base_card`` is the card on the base topology with the most kept pictures,
    whose source a run resolves (``workflow_run_service.resolve_source`` takes a
    card).
    """

    workflow_id: str
    topologies: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)
    cards: list[str] = field(default_factory=list)
    variant_topology: dict[str, str] = field(default_factory=dict)
    variant_card: dict[str, str] = field(default_factory=dict)
    # None where the specials pass has not read the topology yet.
    specials: dict[str, Optional[tuple[str, ...]]] = field(default_factory=dict)
    base_topology: Optional[str] = None
    base_card: Optional[str] = None
    name: Optional[str] = None
    notes: Optional[str] = None
    hidden: bool = False


# Every current variant with its automatic workflow: the topology's core under
# this build's rule and the variant's own base-model families.
_AUTO_VARIANTS = (
    "SELECT v.structural_hash AS structural_hash, v.topology_hash AS topology_hash, "
    "c.core_hash AS core_hash, vf.families AS families "
    "FROM workflow_variant v "
    "JOIN workflow_topology_core c ON c.topology_hash = v.topology_hash "
    "AND c.core_version = ? "
    "JOIN workflow_variant_family vf ON vf.structural_hash = v.structural_hash "
    "WHERE v.key_version = ? AND NOT EXISTS (SELECT 1 FROM workflow_group_member m "
    "WHERE m.topology_hash = v.topology_hash)"
)


def workflow_of_variant(hub: HubDatabase, structural_hash: str) -> Optional[str]:
    """The workflow one variant is in, or ``None`` while it is in none.

    Per variant and not per topology: one topology holds workflows of
    several base-model families.
    """
    row = hub.fetchone(
        "SELECT m.workflow_id FROM workflow_group_member m "
        "JOIN workflow_variant v ON v.topology_hash = m.topology_hash "
        "WHERE v.structural_hash = ?",
        (structural_hash,),
    )
    if row is not None:
        return row["workflow_id"]
    row = hub.fetchone(
        f"{_AUTO_VARIANTS} AND v.structural_hash = ?",
        (CORE_RULE_VERSION, WORKFLOW_KEY_VERSION, structural_hash),
    )
    return auto_workflow_id(row["core_hash"], row["families"]) if row else None


def variant_workflows(hub: HubDatabase) -> dict[str, str]:
    """:func:`workflow_of_variant` for every variant at once.

    The same precedence: a ``workflow_group_member`` placement wins, and
    ``_AUTO_VARIANTS`` already skips the topologies such a row places, so the
    overwrite below only ever fills what that query left out.
    ``{structural_hash: workflow_id}``; a variant in no workflow yet is absent.
    """
    found = {
        row["structural_hash"]: auto_workflow_id(row["core_hash"], row["families"])
        for row in hub.fetchall(
            _AUTO_VARIANTS, (CORE_RULE_VERSION, WORKFLOW_KEY_VERSION)
        )
    }
    for row in hub.fetchall(
        "SELECT v.structural_hash AS structural_hash, m.workflow_id AS workflow_id "
        "FROM workflow_group_member m "
        "JOIN workflow_variant v ON v.topology_hash = m.topology_hash"
    ):
        found[row["structural_hash"]] = row["workflow_id"]
    return found


def topologies_in_workflow(hub: HubDatabase, workflow_id: str) -> list[str]:
    """Every topology one workflow holds, sorted; empty for an unknown id."""
    if not workflow_id.startswith(AUTO_STACK_PREFIX):
        return [
            topology_hash
            for (topology_hash,) in hub.fetchall(
                "SELECT topology_hash FROM workflow_group_member "
                "WHERE workflow_id = ? ORDER BY topology_hash",
                (workflow_id,),
            )
        ]
    return sorted({t for _, t in _auto_variants_of(hub, workflow_id)})


def variants_in_workflow(hub: HubDatabase, workflow_id: str) -> list[str]:
    """Every variant filed under one workflow, sorted."""
    if workflow_id.startswith(AUTO_STACK_PREFIX):
        return sorted(s for s, _ in _auto_variants_of(hub, workflow_id))
    found: list[str] = []
    for batch in chunked(topologies_in_workflow(hub, workflow_id)):
        placeholders = ",".join("?" * len(batch))
        found += [
            structural_hash
            for (structural_hash,) in hub.fetchall(
                "SELECT structural_hash FROM workflow_variant "
                f"WHERE key_version = ? AND topology_hash IN ({placeholders})",
                (WORKFLOW_KEY_VERSION, *batch),
            )
        ]
    return sorted(found)


def variants_in_workflows(hub: HubDatabase, workflow_ids: list[str]) -> set[str]:
    """Every variant filed under any of several workflows.

    One scan for all the automatic ones, however many: their ids are digests,
    so :func:`variants_in_workflow` reads every variant per id.
    """
    auto = {wid for wid in workflow_ids if wid.startswith(AUTO_STACK_PREFIX)}
    found = (
        {
            row["structural_hash"]
            for row in hub.fetchall(
                _AUTO_VARIANTS, (CORE_RULE_VERSION, WORKFLOW_KEY_VERSION)
            )
            if auto_workflow_id(row["core_hash"], row["families"]) in auto
        }
        if auto
        else set()
    )
    for workflow_id in set(workflow_ids) - auto:
        found.update(variants_in_workflow(hub, workflow_id))
    return found


def _auto_variants_of(hub: HubDatabase, workflow_id: str) -> list[tuple[str, str]]:
    """``[(structural_hash, topology_hash)]`` of one automatic workflow.

    The id is a digest, so this reads every current variant and keeps the
    ones whose id it is. # ponytail: a scan per call, an id column if a
    library's variant count makes it slow.
    """
    return [
        (row["structural_hash"], row["topology_hash"])
        for row in hub.fetchall(
            _AUTO_VARIANTS, (CORE_RULE_VERSION, WORKFLOW_KEY_VERSION)
        )
        if auto_workflow_id(row["core_hash"], row["families"]) == workflow_id
    ]


def workflow_index(
    hub: HubDatabase,
    picture_counts: Optional[dict[str, int]] = None,
    cards: Optional[list[Card]] = None,
) -> list[Workflow]:
    """Every workflow this hub holds, built from :func:`card_index`.

    A card whose topology is in no workflow (the backfill has not reached it)
    is left out. A manual card is a workflow of its own, its id its base card.

    Args:
        hub: The hub.
        picture_counts: ``{structural_hash: kept pictures}`` from the vault,
            for the base topology's first key and its last tie-break. ``None``
            skips both.
        cards: :func:`card_index`'s answer when the caller already read it.
    """
    placed = {
        topology_hash: workflow_id
        for topology_hash, workflow_id in hub.fetchall(
            "SELECT topology_hash, workflow_id FROM workflow_group_member"
        )
    }
    workflows: dict[str, Workflow] = {}
    loras: dict[str, int] = {}
    # The topologies whose graph has a negative prompt to type.
    negative: set[str] = set()
    cards = cards if cards is not None else card_index(hub)
    # A card of a topology with no variant under the current key (a legacy
    # file row) reads no core hash of its own, so it joins whatever workflow
    # another card of its topology is in.
    core_of = {card.topology_hash: card.core_hash for card in cards if card.core_hash}
    manual: list[Workflow] = []
    topology_of: dict[str, str] = {}
    for card in cards:
        if card.manual:
            manual.append(
                Workflow(
                    card.workflow_key,
                    cards=[card.workflow_key],
                    base_card=card.workflow_key,
                )
            )
            continue
        core = core_of.get(card.topology_hash)
        for structural_hash in card.variants:
            families = card.families.get(structural_hash)
            workflow_id = placed.get(card.topology_hash) or (
                auto_workflow_id(core, families)
                if core and families is not None
                else None
            )
            if workflow_id is None:
                continue
            entry = workflows.setdefault(workflow_id, Workflow(workflow_id))
            if card.workflow_key not in entry.cards:
                entry.cards.append(card.workflow_key)
            topology_of[card.workflow_key] = card.topology_hash
            if card.topology_hash not in entry.topologies:
                entry.topologies.append(card.topology_hash)
            # Per topology, so every card on it agrees. The fullest answer
            # wins, never the last card read.
            known = entry.specials.get(card.topology_hash)
            if known is None or len(card.specials or ()) > len(known):
                entry.specials[card.topology_hash] = card.specials
            loras[card.topology_hash] = max(
                loras.get(card.topology_hash, 0),
                sum(1 for s in card.slots if s.get("is_lora")),
            )
            if NEGATIVE_PROMPT in (card.traits or ()):
                negative.add(card.topology_hash)
            entry.variants.append(structural_hash)
            entry.variant_topology[structural_hash] = card.topology_hash
            entry.variant_card[structural_hash] = card.workflow_key
    workflows.update((entry.workflow_id, entry) for entry in manual)
    attrs = {
        row["workflow_id"]: row
        for row in hub.fetchall(
            "SELECT workflow_id, name, notes, hidden FROM workflow_group_attr"
        )
    }
    counts = picture_counts or {}
    for entry in workflows.values():
        attr = attrs.get(entry.workflow_id)
        if attr is not None:
            entry.name, entry.notes = attr["name"], attr["notes"]
            entry.hidden = bool(attr["hidden"])
        if not entry.topologies:
            # A manual workflow: no topology to choose a base from.
            continue
        entry.topologies.sort()
        entry.variants.sort()
        entry.cards.sort()
        pictures: dict[str, int] = {}
        for structural_hash, topology_hash in entry.variant_topology.items():
            pictures[topology_hash] = pictures.get(topology_hash, 0) + counts.get(
                structural_hash, 0
            )
        # A topology this library has kept pictures of first, so a workflow
        # with any picture here has a graph to run (#1738); then one with a
        # negative prompt to type, which core rule v5 groups with graphs that
        # zero theirs and which can do what they do (a run cannot add the box);
        # then most of each, and on a full tie the smallest hash, so two reads
        # of an unchanged hub name the same base. A workflow file is no key:
        # it may be UI-format, which does not run, and the hub-only base must
        # not move.
        entry.base_topology = min(
            entry.topologies,
            key=lambda t: (
                not pictures.get(t),
                t not in negative,
                -len(entry.specials.get(t) or ()),
                -loras.get(t, 0),
                -pictures.get(t, 0),
                t,
            ),
        )
        on_base: dict[str, int] = {}
        for structural_hash, key in entry.variant_card.items():
            if entry.variant_topology[structural_hash] == entry.base_topology:
                on_base[key] = on_base.get(key, 0) + counts.get(structural_hash, 0)
        # A base topology no variant is filed on is a legacy file row's card.
        entry.base_card = (
            min(on_base, key=lambda key: (-on_base[key], key))
            if on_base
            else min(
                (k for k in entry.cards if topology_of[k] == entry.base_topology),
                default=None,
            )
        )
    return sorted(workflows.values(), key=lambda entry: entry.workflow_id)


def find_workflow(
    hub: HubDatabase,
    workflow_id: str,
    picture_counts: Optional[dict[str, int]] = None,
    cards: Optional[list[Card]] = None,
) -> Optional[Workflow]:
    """One workflow by its id, or ``None`` when this hub has no such workflow."""
    return next(
        (
            w
            for w in workflow_index(hub, picture_counts, cards)
            if w.workflow_id == workflow_id
        ),
        None,
    )


def group_pins(hub: HubDatabase, workflow_id: str) -> Optional[list[tuple[str, str]]]:
    """The parameters the owner pinned on a workflow, or ``None`` for no choice.

    The read side of ``PUT /workflows/{id}/pins``, which stores addresses
    (``<slot label>/<input name>``). A ``[slot label, input name]`` pair is
    read too, the shape ``workflow_key_pins`` holds. As :func:`key_pins`: an
    unreadable row answers ``None``, never ``[]``, and a pin that is neither
    shape is logged and left out.
    """
    row = hub.fetchone(
        "SELECT pins FROM workflow_group_pins WHERE workflow_id = ?", (workflow_id,)
    )
    if row is None:
        return None
    try:
        stored = json.loads(row["pins"])
    except (TypeError, ValueError) as exc:
        logger.warning(
            "The pins of workflow %s will not parse, so it reads as having made "
            "no choice and its default pins apply: %s",
            workflow_id,
            exc,
        )
        return None
    if not isinstance(stored, list):
        logger.warning(
            "The pins of workflow %s parsed as %s rather than a list, so it reads "
            "as having made no choice and its default pins apply.",
            workflow_id,
            type(stored).__name__,
        )
        return None
    pins = []
    for pin in stored:
        if isinstance(pin, str) and "/" in pin.strip("/"):
            slot_label, _, input_name = pin.rpartition("/")
            pins.append((slot_label, input_name))
        elif isinstance(pin, (list, tuple)) and len(pin) == 2:
            pins.append((str(pin[0]), str(pin[1])))
        else:
            logger.warning(
                "A pin of workflow %s is %r rather than an address, so it is left "
                "out; that parameter will not be pinned.",
                workflow_id,
                pin,
            )
    return pins


def group_picture_inputs(
    hub: HubDatabase, library_uuid: str, workflow_id: str
) -> list[dict]:
    """How each picture input of a workflow is filled here (``PUT …/inputs``).

    Returns:
        ``[{slot_label, input_name, mode, pixel_sha}]``, the address split at
        its last ``/``, ordered so two reads of an unchanged hub agree.
    """
    found = []
    for address, mode, pixel_sha in hub.fetchall(
        "SELECT address, mode, pixel_sha FROM workflow_group_picture_input "
        "WHERE library_uuid = ? AND workflow_id = ? ORDER BY address",
        (library_uuid, workflow_id),
    ):
        slot_label, _, input_name = address.rpartition("/")
        found.append(
            {
                "slot_label": slot_label,
                "input_name": input_name,
                "mode": mode,
                "pixel_sha": pixel_sha,
            }
        )
    return found


def workflow_group_defaults(hub: HubDatabase, workflow_id: str) -> dict[str, str]:
    """``{address: value}``: the owner's edits to a workflow's default recipe."""
    return {
        address: value
        for address, value in hub.fetchall(
            "SELECT address, value FROM workflow_group_default WHERE workflow_id = ?",
            (workflow_id,),
        )
    }


def model_fix_labels(
    hub: HubDatabase, topology_hash: str, was: str, kind: str
) -> list[str]:
    """The slots of a topology taking a *kind* model that load *was*, in any variant.

    Every variant and not one card's: a fix is keyed per topology, so a
    sibling card loading *was* in a slot this card's variants do not use would
    otherwise keep its missing file. Slots of that kind only
    (``model_fix_kind``): the replacement is a checkpoint, a VAE or a text
    encoder, and a slot of another kind holding a file of the same name is no
    place for it.
    """
    wanted = asset_reference(normalized_filename(was))
    hashes = [
        row["structural_hash"]
        for row in hub.fetchall(
            "SELECT structural_hash FROM workflow_recipe WHERE topology_hash = ?",
            (topology_hash,),
        )
    ]
    return sorted(
        {
            slot.label
            for document in variant_documents(hub, hashes).values()
            for slot in slots(document)
            if slot.asset == wanted
            and model_fix_kind(slot.class_type, slot.widget) == kind
        }
    )


def model_fixes(
    hub: HubDatabase, topology_hash: str
) -> list[tuple[str, str, str, str]]:
    """``[(slot_label, was_name, now_name, slot_kind)]``: a topology's replaced models.

    The read side of ``PUT /workflows/{key}/model-fix``. Per topology, because
    that is what a fix is keyed on (``workflow_cards.fixed_slots``).
    """
    return [
        (slot_label, was_name, now_name, slot_kind)
        for slot_label, was_name, now_name, slot_kind in hub.fetchall(
            "SELECT slot_label, was_name, now_name, slot_kind FROM workflow_model_fix "
            "WHERE topology_hash = ? ORDER BY slot_label, was_norm",
            (topology_hash,),
        )
    ]
