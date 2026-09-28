"""Convert the owner's card state onto workflows (#1623, the cut-over).

Cards (``workflow_key``) carried the owner's names, notes, defaults, pins,
picture inputs and stacks; a **workflow** (``workflow_id``, a group of
topologies) carries them now. This is the one-shot step that moves them: hub
data step 5 (:func:`pixlstash.hub.schema.apply_migrations`), inside the
transaction that writes the data version, so an interrupted conversion is
retried on the next open rather than half-applied.

**Merge, never drop.** The owner did not ask for this migration, so nothing
they typed may vanish in it. Where several cards become one workflow the
cover's value wins and every other one survives somewhere readable - a second
name in the notes, a second note under its card's name - or is logged with its
value. ``workflow_card_writes._rekey_variants``' winner-takes-all is not
reused, deliberately.

**Idempotent on its own.** Every row is computed from the card tables alone,
never from what an earlier run wrote, and written with ``INSERT OR REPLACE``,
so a second run leaves byte-identical rows. The card tables are left in place:
the hub is append-only.

The same address translation serves the vault's saved-recipe conversion
(``tasks/saved_recipe_convert_task.py``), which holds card keys and
``<slot label>/<input>`` overrides.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import Counter
from typing import Optional

from pixlstash.hub.workflow_card_reads import (
    AUTO_STACK_PREFIX,
    Card,
    asset_names,
    card_index,
    default_overrides,
    key_pins,
    model_fixes,
    slot_marks,
    stack_rows,
    variant_documents,
    workflow_index,
)
from pixlstash.hub.workflow_cards import STRIP_LORAS_FOR_STACKS, loader_swaps_of
from pixlstash.pixl_logging import get_logger
from pixlstash.services.comfyui_recipe_service import LORA_DIGEST_FIELD_RE
from pixlstash.services.model_shelf_service import adapter_digest_index
from pixlstash.services.workflow_card_service import LORA_ADDRESS_PREFIX
from pixlstash.services.workflow_hash import (
    WorkflowGraphError,
    asset_reference,
    normalized_filename,
)
from pixlstash.services.workflow_identity import (
    CORE_ADDRESS_PREFIX,
    STRUCTURAL,
    core_hash,
    core_node_labels,
    model_fix_kind,
    slots,
    topology_node_labels,
    unswapped,
)

logger = get_logger(__name__)

# A manual group id is a uuid hex. A manual stack's id already is one
# (``stack_together`` writes ``uuid4().hex``), so it is reused and a rerun
# lands on the same row; anything else is hashed into one.
_GROUP_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_GROUP_NAMESPACE = uuid.UUID("5f0c1b7e-2a4d-4c3e-9f1a-6b8d0e2c7a31")

# What a converted speed LoRA holds when no stored run says how strong it ran:
# not a number, so ``workflow_card_service._float_or_none`` reads it as "the
# graph's own strength" (and says so in the log).
_GRAPH_STRENGTH = "on"

_UNNAMED = "Unnamed card"


class _Reader:
    """``hub.fetchone``/``hub.fetchall`` over a bare connection.

    The card readers take a hub; the conversion runs inside
    ``apply_migrations``' transaction on a connection that may have no
    ``row_factory``. A cursor of its own gets ``sqlite3.Row`` without changing
    the connection's.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def _cursor(self, sql: str, params: tuple) -> sqlite3.Cursor:
        cursor = self._conn.cursor()
        cursor.row_factory = sqlite3.Row
        return cursor.execute(sql, params)

    def fetchone(self, sql: str, params: tuple = ()):
        return self._cursor(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()):
        return self._cursor(sql, params).fetchall()


def convert_card_state(conn: sqlite3.Connection) -> int:
    """Write every card's state onto its workflow; return how many cards moved.

    Args:
        conn: An open hub connection, inside the caller's transaction.
    """
    hub = _Reader(conn)
    cards = card_index(hub)
    if not cards:
        return 0
    stacks = stack_rows(hub)
    placement, stack_of_group = _placements(cards, stacks)

    # A file-only card has no core hash of its own and joins its topology's
    # workflow, as ``workflow_index`` reads it.
    core_of = {card.topology_hash: card.core_hash for card in cards if card.core_hash}
    workflow_of: dict[str, str] = {}
    for card in cards:
        workflow_id = placement.get(card.topology_hash) or _auto_id(
            hub, card, core_of.get(card.topology_hash)
        )
        if workflow_id is None:
            logger.warning(
                "Card %s (%s) has no core hash and no stored graph to compute "
                "one from, so it gets no workflow and no successor row.",
                card.workflow_key,
                card.name or "unnamed",
            )
            continue
        workflow_of[card.workflow_key] = workflow_id

    for workflow_id in sorted(set(placement.values())):
        conn.execute(
            "INSERT OR REPLACE INTO workflow_group (workflow_id, kind, core_hash) "
            "VALUES (?, 'manual', NULL)",
            (workflow_id,),
        )
    conn.executemany(
        "INSERT OR REPLACE INTO workflow_group_member (topology_hash, workflow_id) "
        "VALUES (?, ?)",
        sorted(placement.items()),
    )
    conn.executemany(
        "INSERT OR REPLACE INTO workflow_key_successor (workflow_key, workflow_id) "
        "VALUES (?, ?)",
        sorted(workflow_of.items()),
    )

    # Read after the member rows are written, through the same connection, so
    # the base topology is the one every later read will choose. No picture
    # counts at hub open: the topology hash settles the last tie.
    bases = {w.workflow_id: w.base_topology for w in workflow_index(hub, cards=cards)}
    positions = {
        (stack_id, key): position
        for stack_id, members in stacks.members.items()
        for position, key in members
    }
    by_key = {card.workflow_key: card for card in cards}
    members: dict[str, list[Card]] = {}
    for key, workflow_id in workflow_of.items():
        members.setdefault(workflow_id, []).append(by_key[key])
    labels = _LabelCache(hub)
    for workflow_id, group in sorted(members.items()):
        # The cover first: stack position 0, else most variants, then the key.
        stack_id = stack_of_group.get(workflow_id, workflow_id)
        group.sort(
            key=lambda card: (
                positions.get((stack_id, card.workflow_key), float("inf")),
                -len(card.variants),
                card.workflow_key,
            )
        )
        base = bases.get(workflow_id) or min(card.topology_hash for card in group)
        # A savepoint per workflow, so one that fails leaves no half-written
        # rows behind while the others still convert.
        conn.execute("SAVEPOINT convert_workflow")
        try:
            _convert_workflow(conn, hub, workflow_id, group, base, labels)
            conn.execute("RELEASE convert_workflow")
        except Exception as exc:
            conn.execute("ROLLBACK TO convert_workflow")
            conn.execute("RELEASE convert_workflow")
            # This runs at hub open inside the data-version transaction: an
            # uncaught error would roll it back and refuse the hub on every
            # start. The card tables are left as they were, so what this
            # workflow's cards held is still on disk to recover by hand. Its
            # successor rows stay: the workflow exists whether or not its
            # owner state converted, and its saved recipes still belong there.
            logger.error(
                "Workflow %s: converting cards %s failed, so their names, notes, "
                "defaults, pins and inputs are not carried over: %s",
                workflow_id,
                [card.workflow_key for card in group],
                exc,
                exc_info=True,
            )
    _log_dropped_promotions(hub)
    return len(workflow_of)


def _run_strength(run, node_id: str):
    """A stored run's ``strength_model`` on *node_id*, or ``None``."""
    node = run.get(node_id) if isinstance(run, dict) else None
    inputs = node.get("inputs") if isinstance(node, dict) else None
    return inputs.get("strength_model") if isinstance(inputs, dict) else None


def _log_dropped_promotions(hub) -> None:
    """Name every LoRA promotion, which the default recipe has no place for.

    A promotion keyed one LoRA file into a card; with LoRAs recipe values now,
    the rule "a LoRA in most of the best pictures is a default" (#1620 D2)
    takes its place. The rows stay in the hub; this says which they were.
    """
    for topology_hash, slot_label, asset in hub.fetchall(
        "SELECT topology_hash, slot_label, asset FROM workflow_lora_promotion "
        "ORDER BY topology_hash, slot_label, asset"
    ):
        logger.warning(
            "The LoRA promotion of %s at slot %s of topology %s is not carried "
            "over: a workflow's default LoRAs are those in most of its best "
            "pictures.",
            asset,
            slot_label,
            topology_hash,
        )


def _placements(cards: list[Card], stacks) -> tuple[dict[str, str], dict[str, str]]:
    """``({topology: manual workflow id}, {manual workflow id: its stack id})``.

    A manual stack becomes a manual group of its cards' topologies; a topology
    whose cards sat in two stacks goes to the one holding more of its variants
    (then the smaller stack id). ``auto`` stack rows only ordered a group and
    place nothing. A topology in no manual stack is split out of its automatic
    group only when **every** card of it was unstacked.
    """
    by_key = {card.workflow_key: card for card in cards}
    votes: dict[str, Counter] = {}
    for stack_id in sorted(stacks.members):
        if stacks.kinds.get(stack_id) != "manual":
            continue
        for _position, key in stacks.members[stack_id]:
            card = by_key.get(key)
            if card is None:
                logger.info(
                    "Manual stack %s names card %s, which this hub no longer "
                    "holds; it places nothing.",
                    stack_id,
                    key,
                )
                continue
            votes.setdefault(card.topology_hash, Counter())[stack_id] += len(
                card.variants
            )
    placement, stack_of_group = {}, {}
    for topology_hash, counted in votes.items():
        stack_id = min(counted, key=lambda s: (-counted[s], s))
        if len(counted) > 1:
            logger.info(
                "Topology %s had cards in stacks %s; it goes to %s, which held "
                "most of its variants.",
                topology_hash,
                dict(counted),
                stack_id,
            )
        group_id = (
            stack_id
            if _GROUP_ID_RE.match(stack_id)
            else uuid.uuid5(_GROUP_NAMESPACE, stack_id).hex
        )
        placement[topology_hash] = group_id
        stack_of_group[group_id] = stack_id

    per_topology: dict[str, list[str]] = {}
    for card in cards:
        per_topology.setdefault(card.topology_hash, []).append(card.workflow_key)
    for topology_hash, keys in sorted(per_topology.items()):
        if topology_hash in placement:
            continue
        out = [key for key in keys if key in stacks.unstacked]
        if out and len(out) == len(keys):
            placement[topology_hash] = uuid.uuid5(_GROUP_NAMESPACE, topology_hash).hex
        elif out:
            logger.info(
                "Topology %s stays in its automatic workflow: cards %s were "
                "unstacked but %s were not.",
                topology_hash,
                sorted(out),
                sorted(set(keys) - set(out)),
            )
    return placement, stack_of_group


def _auto_id(hub, card: Card, topology_core: Optional[str]) -> Optional[str]:
    """``auto:<core hash>``, computed from a stored graph when the cache lacks it.

    A file-only card (#1466: a workflow file with no recipe) is its own
    workflow, ``auto:<topology hash>``, unless another card of its topology
    has a core hash, exactly as ``workflow_index`` files it.
    """
    if topology_core:
        return f"{AUTO_STACK_PREFIX}{topology_core}"
    if not card.variants:
        return f"{AUTO_STACK_PREFIX}{card.topology_hash}"
    found = card_document(hub, card)
    if found is None:
        return None
    try:
        core = core_hash(found[1], strip_loras=STRIP_LORAS_FOR_STACKS)
        return f"{AUTO_STACK_PREFIX}{core}"
    except WorkflowGraphError as exc:
        logger.warning(
            "Card %s's stored graph has no core hash: %s", card.workflow_key, exc
        )
        return None


def card_document(hub, card: Card) -> Optional[tuple[str, dict]]:
    """``(structural_hash, document)`` of the card's first variant that reduces.

    Read as the card's own topology: a graph a model fix swapped a loader into
    (#1605) gets its original loader back, as ``record_identity`` reads it.
    """
    documents = variant_documents(hub, card.variants)
    for structural_hash in sorted(documents):
        row = hub.fetchone(
            "SELECT topology_hash FROM workflow_recipe WHERE structural_hash = ?",
            (structural_hash,),
        )
        document = documents[structural_hash]
        if row is not None and row["topology_hash"] != card.topology_hash:
            _, document = unswapped(
                document, loader_swaps_of(hub.fetchall, row["topology_hash"])
            )
        try:
            # Both reductions the callers make, so neither raises for them.
            slots(document)
            core_node_labels(document, strip_loras=STRIP_LORAS_FOR_STACKS)
        except Exception as exc:
            # Not only WorkflowGraphError: a malformed stored document raises
            # KeyError from the reduction (see ``record_reduction``), and this
            # runs at hub open, where anything uncaught would refuse the hub
            # on every start. The card converts without this variant.
            logger.warning(
                "Variant %s of card %s will not reduce, so the conversion reads "
                "the card without it: %s",
                structural_hash,
                card.workflow_key,
                exc,
            )
            continue
        return structural_hash, document
    return None


class _LabelCache:
    """Per topology, ``{slot label: core label or None}`` from one stored graph."""

    def __init__(self, hub) -> None:
        self._hub = hub
        self._maps: dict[str, Optional[dict[str, Optional[str]]]] = {}

    def of(self, card: Card) -> Optional[dict[str, Optional[str]]]:
        if card.topology_hash not in self._maps:
            found = card_document(self._hub, card)
            self._maps[card.topology_hash] = label_map(found[1]) if found else None
        return self._maps[card.topology_hash]


def label_map(document: dict) -> dict[str, Optional[str]]:
    """Each slot label of *document*'s topology, to its core label (``None``: a stage)."""
    core = core_node_labels(document, strip_loras=STRIP_LORAS_FOR_STACKS)
    return {
        label: core.get(node_id)
        for node_id, label in topology_node_labels(document).items()
    }


def translate(
    labels: Optional[dict[str, Optional[str]]],
    topology_hash: str,
    base_topology: Optional[str],
    slot_label: str,
    input_name: str,
) -> Optional[str]:
    """A card's ``(slot label, input)`` as a workflow address, or ``None``.

    ``core:<label>/<input>`` for a node on the core graph; a stage node keeps
    its slot label only on the workflow's base topology, the one graph that
    label means anything on.
    """
    if labels is None or slot_label not in labels:
        return None
    core = labels[slot_label]
    if core:
        return f"{CORE_ADDRESS_PREFIX}{core}/{input_name}"
    if topology_hash == base_topology:
        return f"{slot_label}/{input_name}"
    return None


def model_pins(hub, card: Card) -> list[dict]:
    """The card's checkpoint, VAE and text encoders as ``[{address, filename}]``.

    What pins a saved recipe to the models of the card it was saved on. The
    filename is the one the hub holds (``workflow_recipe_asset``), or the
    replacement where a model fix replaced it. A loader with no core label (a
    stage's own) or whose name was forgotten pins nothing.
    """
    found = card_document(hub, card)
    if found is None:
        return []
    structural_hash, document = found
    core = core_node_labels(document, strip_loras=STRIP_LORAS_FOR_STACKS)
    names = {
        asset_reference(filename): filename
        for _widget, filename in asset_names(hub, [structural_hash]).get(
            structural_hash, []
        )
    }
    fixed = {
        (slot_label, normalized_filename(was)): now
        for slot_label, was, now, _kind in model_fixes(hub, card.topology_hash)
    }
    pins = []
    for slot in slots(document):
        label = core.get(slot.node_id)
        filename = names.get(slot.asset)
        if (
            slot.is_lora
            or not label
            or not model_fix_kind(slot.class_type, slot.widget)
        ):
            continue
        if filename is None:
            logger.info(
                "Card %s's %s has no readable name, so it is not pinned.",
                card.workflow_key,
                slot.widget,
            )
            continue
        pins.append(
            {
                "address": f"{CORE_ADDRESS_PREFIX}{label}/{slot.widget}",
                "filename": fixed.get(
                    (slot.label, normalized_filename(filename)), filename
                ),
            }
        )
    return sorted(pins, key=lambda pin: pin["address"])


def _convert_workflow(
    conn: sqlite3.Connection,
    hub,
    workflow_id: str,
    group: list[Card],
    base_topology: str,
    labels: _LabelCache,
) -> None:
    """Write one workflow's attributes, defaults, pins and inputs. Cover first."""
    cover = group[0]
    keys = [card.workflow_key for card in group]
    attrs = {
        row["workflow_key"]: row
        for row in hub.fetchall(
            "SELECT workflow_key, name, notes, hidden FROM workflow_attr "
            f"WHERE workflow_key IN ({','.join('?' * len(keys))})",
            tuple(keys),
        )
    }

    def attr(card: Card, column: str):
        row = attrs.get(card.workflow_key)
        return row[column] if row is not None else None

    # The cover's name, else the first name in cover order: an unnamed cover
    # has nothing to win with, and the owner did name this workflow.
    name = attr(cover, "name") or next(
        (attr(card, "name") for card in group[1:] if attr(card, "name")), None
    )
    also = [
        other
        for other in dict.fromkeys(attr(card, "name") for card in group[1:])
        if other and other != name
    ]
    blocks = [(attr(card, "name"), attr(card, "notes")) for card in group]
    blocks = [(heading, text) for heading, text in blocks if text]
    # Only the cover's notes, when they are the only notes, stand as written.
    # Once blocks are put together every one is headed with its card's name,
    # the cover's included (the issue's rule), so nobody has to guess whose
    # is whose; a lone non-cover block is headed too.
    notes = "\n\n".join(
        text
        if len(blocks) == 1 and attr(cover, "notes") == text
        else f"{heading or _UNNAMED}:\n{text}"
        for heading, text in blocks
    )
    if also:
        notes = "\n\n".join(filter(None, [notes, "Also named: " + ", ".join(also)]))
    hidden = all(attr(card, "hidden") for card in group)
    wrote = []
    if name or notes or hidden:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_group_attr "
            "(workflow_id, name, notes, hidden) VALUES (?, ?, ?, ?)",
            (workflow_id, name, notes or None, int(hidden)),
        )
        wrote.append("attributes")

    def address(card: Card, slot_label: str, input_name: str, what: str, value):
        found = translate(
            labels.of(card), card.topology_hash, base_topology, slot_label, input_name
        )
        if found is None:
            logger.warning(
                "Workflow %s: card %s's %s at %s/%s (value %r) names no node of "
                "the workflow, so it is not carried over.",
                workflow_id,
                card.workflow_key,
                what,
                slot_label,
                input_name,
                value,
            )
        return found

    defaults: dict[str, tuple[str, str]] = {}
    pins: Optional[list[str]] = None
    for card in group:
        for (slot_label, input_name), value in sorted(
            default_overrides(hub, card.workflow_key).items()
        ):
            found = address(card, slot_label, input_name, "default", value)
            if found is None:
                continue
            if found in defaults and defaults[found][0] != value:
                logger.info(
                    "Workflow %s: default %s is %r on card %s and %r on card %s; "
                    "the first (the cover's, where it has one) is kept.",
                    workflow_id,
                    found,
                    defaults[found][0],
                    defaults[found][1],
                    value,
                    card.workflow_key,
                )
                continue
            defaults[found] = (value, card.workflow_key)
        card_pins = key_pins(hub, card.workflow_key)
        if card_pins is None:
            continue
        pins = pins if pins is not None else []
        for slot_label, input_name in card_pins:
            found = address(card, slot_label, input_name, "pin", None)
            if found is not None and found not in pins:
                pins.append(found)
    defaults.update(
        (address_, (value, cover.workflow_key))
        for address_, value in _structural_loras(hub, workflow_id, cover).items()
    )
    conn.executemany(
        "INSERT OR REPLACE INTO workflow_group_default (workflow_id, address, value) "
        "VALUES (?, ?, ?)",
        [(workflow_id, address_, value) for address_, (value, _) in defaults.items()],
    )
    if defaults:
        wrote.append(f"{len(defaults)} default(s)")
    if pins is not None:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_group_pins (workflow_id, pins) "
            "VALUES (?, ?)",
            (workflow_id, json.dumps(pins)),
        )
        wrote.append(f"{len(pins)} pin(s)")

    inputs: dict[tuple[str, str], tuple[str, Optional[str]]] = {}
    for card in group:
        for library_uuid, slot_label, input_name, mode, pixel_sha in hub.fetchall(
            "SELECT library_uuid, slot_label, input_name, mode, pixel_sha "
            "FROM workflow_key_picture_input WHERE workflow_key = ? "
            "ORDER BY library_uuid, slot_label, input_name",
            (card.workflow_key,),
        ):
            found = address(card, slot_label, input_name, "picture input", mode)
            if found is None:
                continue
            if (library_uuid, found) in inputs:
                logger.info(
                    "Workflow %s: picture input %s in library %s is set on more "
                    "than one card; the cover's (or the first) is kept.",
                    workflow_id,
                    found,
                    library_uuid,
                )
                continue
            inputs[(library_uuid, found)] = (mode, pixel_sha)
    conn.executemany(
        "INSERT OR REPLACE INTO workflow_group_picture_input "
        "(library_uuid, workflow_id, address, mode, pixel_sha) VALUES (?, ?, ?, ?, ?)",
        [
            (library_uuid, workflow_id, found, mode, pixel_sha)
            for (library_uuid, found), (mode, pixel_sha) in sorted(inputs.items())
        ],
    )
    if inputs:
        wrote.append(f"{len(inputs)} picture input(s)")

    if wrote and workflow_id.startswith(AUTO_STACK_PREFIX):
        # An automatic workflow is a row once something is stored against it.
        conn.execute(
            "INSERT OR IGNORE INTO workflow_group (workflow_id, kind, core_hash) "
            "VALUES (?, 'auto', ?)",
            (workflow_id, workflow_id[len(AUTO_STACK_PREFIX) :]),
        )
    logger.info(
        "Converted %d card(s) over %d topologies to workflow %s (cover %s, %s): %s.",
        len(group),
        len({card.topology_hash for card in group}),
        workflow_id,
        cover.workflow_key,
        name or "unnamed",
        ", ".join(wrote) or "no owner state",
    )


def _structural_loras(hub, workflow_id: str, cover: Card) -> dict[str, str]:
    """``{lora:<sha256>: strength}`` for each LoRA slot marked ``structural`` on the cover.

    The last read of ``workflow_slot_mark`` as an owner's mark: a structural
    LoRA keyed the cover card, so the workflow's default recipe runs it. The
    strength is its modal one over the stored runs of that variant, else
    :data:`_GRAPH_STRENGTH`.
    """
    others = sorted(
        (topology_hash, label)
        for (topology_hash, label), mark in slot_marks(
            hub,
            sorted(
                {
                    row["topology_hash"]
                    for row in hub.fetchall(
                        "SELECT DISTINCT topology_hash FROM workflow_variant "
                        "WHERE workflow_key IN (SELECT workflow_key FROM "
                        "workflow_key_successor WHERE workflow_id = ?)",
                        (workflow_id,),
                    )
                }
                - {cover.topology_hash}
            ),
        ).items()
        if mark == STRUCTURAL
    )
    for topology_hash, label in others:
        logger.warning(
            "Workflow %s: the structural LoRA slot %s of topology %s is not on "
            "the cover card, so it is not made a default-recipe LoRA.",
            workflow_id,
            label,
            topology_hash,
        )
    marks = slot_marks(hub, [cover.topology_hash])
    if STRUCTURAL not in marks.values():
        return {}
    found = card_document(hub, cover)
    if found is None:
        return {}
    structural_hash, document = found
    names = {
        asset_reference(filename): filename
        for _widget, filename in asset_names(hub, [structural_hash]).get(
            structural_hash, []
        )
    }
    by_name, _ = adapter_digest_index(hub)
    runs = []
    for (raw,) in hub.fetchall(
        "SELECT document FROM workflow_recipe_instance WHERE structural_hash = ? "
        "ORDER BY library_uuid, instance_hash",
        (structural_hash,),
    ):
        try:
            runs.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            logger.warning(
                "A stored run of variant %s will not parse, so it says nothing "
                "about its LoRA strengths: %s",
                structural_hash,
                exc,
            )
    loras = {}
    for slot in slots(document):
        if (
            not slot.is_lora
            or marks.get((cover.topology_hash, slot.label)) != STRUCTURAL
        ):
            continue
        filename = names.get(slot.asset)
        if (
            filename
            and LORA_DIGEST_FIELD_RE.match(slot.widget)
            and re.fullmatch(r"[0-9a-f]{64}", filename.lower())
        ):
            sha256 = filename.lower()
        else:
            shelf = by_name.get(normalized_filename(filename or ""), set())
            sha256 = next(iter(shelf)) if len(shelf) == 1 else None
        if sha256 is None:
            logger.warning(
                "Workflow %s: the structural LoRA %s of cover card %s is not one "
                "file on the shelf, so it is not made a default-recipe LoRA.",
                workflow_id,
                filename or slot.asset,
                cover.workflow_key,
            )
            continue
        strengths = Counter(
            float(value)
            for run in runs
            for value in [_run_strength(run, slot.node_id)]
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        )
        loras[f"{LORA_ADDRESS_PREFIX}{sha256}"] = (
            str(max(strengths.items(), key=lambda item: (item[1], item[0]))[0])
            if strengths
            else _GRAPH_STRENGTH
        )
    return loras
