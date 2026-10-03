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
import os
import re
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timezone
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
from pixlstash.hub.workflow_cards import (
    CORE_RULE_VERSION,
    STRIP_LORAS_FOR_STACKS,
    UNRESOLVED_FAMILY,
    _cache_topology,
    auto_workflow_id,
    variant_families,
    loader_swaps_of,
    topology_only_key,
)
from pixlstash.hub.workflow_origin import BUILTIN_ORIGIN
from pixlstash.pixl_logging import get_logger
from pixlstash.services.comfyui_recipe_service import LORA_DIGEST_FIELD_RE
from pixlstash.services.model_shelf_service import adapter_digest_index
from pixlstash.services.workflow_card_service import LORA_ADDRESS_PREFIX
from pixlstash.services.workflow_hash import (
    ReducedNode,
    WorkflowGraphError,
    asset_reference,
    graph_key,
    node_labels,
    normalized_filename,
)
from pixlstash.services.workflow_identity import (
    CORE_ADDRESS_PREFIX,
    STRUCTURAL,
    core_hash,
    core_node_labels,
    model_fix_kind,
    WORKFLOW_KEY_VERSION,
    _core_strip,
    _reduce,
    _strip,
    slots,
    topology_node_labels,
    unswapped,
)
from pixlstash.services.workflow_bindings import migrate_placeholders
from pixlstash.services.workflow_inbox import content_hash
from pixlstash.utils.path_utils import resolve_path_within
from pixlstash.utils.sql_chunking import chunked
from pixlstash.utils.workflow_ids import MANUAL_PREFIX, stamp_workflow_id

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


def _cards_as_filed(hub) -> list[Card]:
    """The cards as data steps 5 and 6 were written against: files included.

    Until manual workflows (data step 7) a workflow FILE with no recipe was a
    card of its own (#1466, ``auto:<topology hash>``), and these steps carry
    the owner's state onto that id; step 7 then carries it on to the file's
    manual workflow. Frozen here, so a hub upgraded from before step 5 still
    converts what its owner typed on such a card. No manual card: none
    exists before step 7.
    """
    cards = [card for card in card_index(hub) if not card.manual]
    return cards + _file_only_cards(hub, {card.workflow_key for card in cards})


def _file_only_cards(hub, keyed: set[str]) -> list[Card]:
    """The cards whose whole content is a stored workflow file (#1466).

    The key is derived (``topology_only_key``) rather than read, since a
    stored ``workflow_file.workflow_key`` can be stale. NOT EXISTS rather
    than NOT IN, which one NULL would make NULL for every row.
    """
    found = {}
    for row in hub.fetchall(
        "SELECT f.topology_hash AS topology_hash, "
        "MIN(f.workflow_name) AS workflow_name "
        "FROM workflow_file f "
        "WHERE NOT EXISTS (SELECT 1 FROM workflow_variant v "
        "WHERE v.structural_hash = f.structural_hash AND v.key_version = ?) "
        "GROUP BY f.topology_hash ORDER BY f.topology_hash",
        (WORKFLOW_KEY_VERSION,),
    ):
        key = topology_only_key(row["topology_hash"])
        if key not in keyed:
            found[key] = (row["topology_hash"], row["workflow_name"])
    attrs = {}
    for batch in chunked(sorted(found)):
        placeholders = ",".join("?" * len(batch))
        for row in hub.fetchall(
            "SELECT workflow_key, name, notes, hidden FROM workflow_attr "
            f"WHERE workflow_key IN ({placeholders})",
            tuple(batch),
        ):
            attrs[row["workflow_key"]] = row
    return [
        Card(
            workflow_key=key,
            topology_hash=topology_hash,
            name=attrs[key]["name"] if key in attrs else None,
            notes=attrs[key]["notes"] if key in attrs else None,
            hidden=bool(attrs[key]["hidden"]) if key in attrs else False,
            imported=True,
            file_name=file_name,
        )
        for key, (topology_hash, file_name) in sorted(found.items())
    ]


def convert_card_state(conn: sqlite3.Connection) -> int:
    """Write every card's state onto its workflow; return how many cards moved.

    Args:
        conn: An open hub connection, inside the caller's transaction.
    """
    hub = _Reader(conn)
    cards = _cards_as_filed(hub)
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
        # The v1 id this step writes is no id `workflow_index` reads any more,
        # so its base is chosen here by the same rule: most stages, then most
        # LoRA loaders, then the topology hash.
        base = (
            bases.get(workflow_id)
            or min(
                group,
                key=lambda card: (
                    -len(card.specials or ()),
                    -sum(1 for slot in card.slots if slot.get("is_lora")),
                    card.topology_hash,
                ),
            ).topology_hash
        )
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


def dissolve_manual_groups(conn: sqlite3.Connection) -> int:
    """Put every hand-made group's topologies back in their automatic workflows.

    Hub data step 6: workflows are automatic, and nothing moves a topology
    between them any more, so the groups merge, split and the cut-over's
    stacks made are undone. Returns how many groups were dissolved.

    **Merge, never drop**, as the cut-over: a group's name, notes, defaults,
    pins and picture inputs go to the **heir**, the automatic workflow holding
    most of its variants. Where the heir already has its own, the heir's win
    address by address, and the group's name and notes are appended to the
    heir's notes rather than lost. A card's successor row follows its own
    topology, so the vault's saved-recipe conversion re-files each recipe on
    the workflow its card is in.

    Args:
        conn: An open hub connection, inside the caller's transaction.
    """
    hub = _Reader(conn)
    groups = [
        row[0]
        for row in conn.execute(
            "SELECT workflow_id FROM workflow_group WHERE kind = 'manual' "
            "ORDER BY workflow_id"
        )
    ]
    if not groups:
        return 0
    cards = _cards_as_filed(hub)
    core_of = {card.topology_hash: card.core_hash for card in cards if card.core_hash}
    # Per card, not per topology: one topology's cards can be in workflows
    # of different base-model families.
    auto_of: dict[str, str] = {}
    cards_on: dict[str, list[Card]] = {}
    for card in cards:
        auto_id = _auto_id(hub, card, core_of.get(card.topology_hash))
        if auto_id is not None:
            auto_of[card.workflow_key] = auto_id
        cards_on.setdefault(card.topology_hash, []).append(card)

    for group in groups:
        topologies = [
            row[0]
            for row in conn.execute(
                "SELECT topology_hash FROM workflow_group_member "
                "WHERE workflow_id = ? ORDER BY topology_hash",
                (group,),
            )
        ]
        votes: Counter = Counter()
        for topology_hash in topologies:
            for card in cards_on.get(topology_hash, []):
                if card.workflow_key in auto_of:
                    votes[auto_of[card.workflow_key]] += max(1, len(card.variants))
        heir = min(votes, key=lambda a: (-votes[a], a)) if votes else None
        conn.execute(
            "DELETE FROM workflow_group_member WHERE workflow_id = ?", (group,)
        )
        if heir is None:
            logger.warning(
                "Hand-made workflow %s holds no topology with an automatic "
                "workflow (%s); its name, notes and settings have nowhere to go "
                "and stay in the hub under its old id.",
                group,
                topologies,
            )
        else:
            _carry_group_state(conn, group, heir)
        for (workflow_key,) in conn.execute(
            "SELECT workflow_key FROM workflow_key_successor WHERE workflow_id = ?",
            (group,),
        ).fetchall():
            target = auto_of.get(workflow_key, heir)
            if target is None:
                # The column is NOT NULL; the row keeps naming the old id.
                logger.warning(
                    "Card %s of hand-made workflow %s has no automatic workflow "
                    "to follow; its successor row keeps the old id.",
                    workflow_key,
                    group,
                )
                continue
            conn.execute(
                "UPDATE workflow_key_successor SET workflow_id = ? "
                "WHERE workflow_key = ?",
                (target, workflow_key),
            )
        conn.execute(
            "DELETE FROM workflow_group WHERE workflow_id = ? AND NOT EXISTS "
            "(SELECT 1 FROM workflow_group_attr WHERE workflow_id = ?)",
            (group, group),
        )
        logger.info(
            "Dissolved hand-made workflow %s: %d topologies back in their "
            "automatic workflows, its state carried to %s.",
            group,
            len(topologies),
            heir,
        )
    return len(groups)


# The namespace a stored file's manual workflow id is derived in (data step
# 7), so a second run lands on the same id and writes nothing new.
_FILE_NAMESPACE = uuid.UUID("2a9e3c1d-7b64-4f0e-8d25-91c4b3a6e0f7")


def adopt_workflow_files(conn: sqlite3.Connection, folder: str) -> int:
    """Make every stored workflow file a manual workflow; return how many.

    Hub data step 7. Every ``workflow_file`` row whose file is in the user
    folder *folder* becomes a ``workflow_document`` row holding the file (and
    the API graph a conversion stored beside it), named after its stem, with
    origin ``pull`` where a pull wrote it and ``import`` otherwise; the pull
    rows naming the file then name the workflow. **Idempotent**: the id is
    derived from the file name and every write is an insert that keeps what
    is there, so a second run writes nothing. Files and ``workflow_file`` rows
    are left as they are; the stored document is tagged with its new id, as
    ``create_manual_workflow`` tags one.

    A file that was a card of its own (#1466, no variant) had its owner state
    carried to ``auto:<topology hash>`` by steps 5 and 6; it is carried on
    here, so nothing its owner typed is lost. A file that will not read is
    logged and skipped: it could not run either.

    Args:
        conn: An open hub connection, inside the caller's transaction.
        folder: The user workflow folder (``workflow_inbox.workflow_user_dir``).
    """
    files = conn.execute(
        "SELECT workflow_name, topology_hash FROM workflow_file ORDER BY workflow_name"
    ).fetchall()
    if not files:
        return 0
    # Here, not at the top: the route module is heavy and this runs once per hub.
    from pixlstash.routes.comfyui import _load_workflow_json, converted_graph

    pulled = {
        row[0] for row in conn.execute("SELECT workflow_name FROM workflow_pulled_file")
    }
    keyed = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT topology_hash FROM workflow_variant WHERE key_version = ?",
            (WORKFLOW_KEY_VERSION,),
        )
    }
    adopted = 0
    for name, topology_hash in files:
        try:
            path = resolve_path_within(folder, name)
            if not os.path.isfile(path):
                logger.info(
                    "Workflow file %s is not in the user folder (a built-in, or "
                    "gone), so it is not made a manual workflow.",
                    name,
                )
                continue
            stored = _load_workflow_json(path)
            if not isinstance(stored, dict):
                raise ValueError("not a JSON object")
            converted = converted_graph(path, stored)
            document, _migrated = migrate_placeholders(stored)
            created = datetime.fromtimestamp(
                os.path.getmtime(path), timezone.utc
            ).isoformat()
        except (OSError, ValueError, RecursionError) as exc:
            logger.warning(
                "Workflow file %s will not read, so it is not made a manual "
                "workflow: %s",
                name,
                exc,
            )
            continue
        workflow_id = f"{MANUAL_PREFIX}{uuid.uuid5(_FILE_NAMESPACE, name).hex}"
        wrote = conn.execute(
            "INSERT OR IGNORE INTO workflow_document (workflow_id, document, "
            "api_document, origin, from_workflow_id, from_name, created_at) "
            "VALUES (?, ?, ?, ?, NULL, NULL, ?)",
            (
                workflow_id,
                json.dumps(stamp_workflow_id(document, workflow_id)),
                json.dumps(converted) if converted is not None else None,
                "pull" if name in pulled else "import",
                created,
            ),
        ).rowcount
        if topology_hash not in keyed:
            # Once: the carry moves the rows, so a second file of the same
            # topology, or a second run, finds nothing left to carry.
            _carry_group_state(conn, f"{AUTO_STACK_PREFIX}{topology_hash}", workflow_id)
            # And its saved recipes: the vault's conversion re-files a recipe
            # naming the retired id on this workflow.
            conn.execute(
                "INSERT OR IGNORE INTO workflow_id_successor "
                "(workflow_id, successor_id) VALUES (?, ?)",
                (f"{AUTO_STACK_PREFIX}{topology_hash}", workflow_id),
            )
        stem = name[: -len(".json")] if name.lower().endswith(".json") else name
        conn.execute(
            "INSERT INTO workflow_group_attr (workflow_id, name) VALUES (?, ?) "
            "ON CONFLICT(workflow_id) DO UPDATE SET "
            "name = COALESCE(workflow_group_attr.name, excluded.name)",
            (workflow_id, stem),
        )
        conn.execute(
            "UPDATE workflow_origin SET workflow_name = ? WHERE workflow_name = ?",
            (workflow_id, name),
        )
        # Which file it was, so deleting the workflow trashes the file too
        # (`DELETE /workflows/{id}`); a file left behind would still list.
        conn.execute(
            "INSERT OR IGNORE INTO workflow_origin (origin, remote_path, "
            "workflow_name, first_pulled_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
            ("file", name, workflow_id, created, created),
        )
        adopted += wrote
    logger.info("Made %d stored workflow file(s) manual workflows.", adopted)
    return adopted


def hash_builtin_origins(conn: sqlite3.Connection) -> int:
    """Give each hashless built-in origin row its content hash; return how many.

    Hub data step 9. A built-in made a manual workflow before the hash was
    written with it has a ``builtin`` row with ``content_hash`` NULL, which
    ``workflow_origin.stored_as`` cannot match, so an inbox drop of the same
    content stored a second copy (a pull matched it as a built-in instead). The hash is the stored document's
    (``workflow_inbox.content_hash`` ignores the workflow's own tag). A row
    whose workflow is gone is left: there is nothing to hash. A document that
    will not hash is logged and left.
    """
    rows = conn.execute(
        "SELECT o.remote_path, d.document FROM workflow_origin o "
        "JOIN workflow_document d ON d.workflow_id = o.workflow_name "
        "WHERE o.origin = ? AND o.content_hash IS NULL",
        (BUILTIN_ORIGIN,),
    ).fetchall()
    hashed = 0
    for remote_path, document in rows:
        try:
            digest = content_hash(json.loads(document))
        except (ValueError, TypeError, AttributeError, RecursionError) as exc:
            logger.warning(
                "Built-in %s's stored workflow will not hash, so its origin "
                "row stays without one: %s",
                remote_path,
                exc,
            )
            continue
        conn.execute(
            "UPDATE workflow_origin SET content_hash = ? "
            "WHERE origin = ? AND remote_path = ?",
            (digest, BUILTIN_ORIGIN, remote_path),
        )
        hashed += 1
    logger.info("Hashed %d built-in workflow origin row(s).", hashed)
    return hashed


# The stamp core rule v1 wrote, which data step 8 re-derives from.
_CORE_RULE_V1 = f"v1-loras-{'stripped' if STRIP_LORAS_FOR_STACKS else 'kept'}"
# A v1 row step 8 tried and could not move: neither v1 (so `_has_v1_cores`
# stops re-running the step) nor current (so the card backfill still tries).
_CORE_RULE_V1_UNMOVED = "unmoved-v1"


def _core_strip_v1(document: dict) -> dict[str, ReducedNode]:
    """Core rule v1's graph, for data step 8's label maps. Not the live rule."""
    return _strip(_reduce(document), _core_strip(STRIP_LORAS_FOR_STACKS))


def core_label_maps(
    document: dict,
) -> tuple[dict[str, Optional[str]], dict[str, str]]:
    """``({v1 core label: v2 core label or None}, {v1 core label: slot label})``.

    The second map holds the nodes v2 took off the core (a stage now, or dead),
    by their slot label on *document*'s own topology: where an address on one
    of them can still point when this topology is its workflow's base. Matched
    by node id, so a stage v2 keeps in a graph with no sampler is simply new
    to the core.
    """
    v1 = _core_strip_v1(document)
    old = node_labels(v1, rounds=None)
    new = core_node_labels(document, strip_loras=STRIP_LORAS_FOR_STACKS)
    slot = topology_node_labels(document)
    return (
        {old[n]: new.get(n) for n in v1},
        {old[n]: slot[n] for n in v1 if n not in new},
    )


def rewritten_address(
    address: str,
    labels: dict[str, Optional[str]],
    stage_slots: dict[str, str],
) -> Optional[str]:
    """*address* on the v2 core, or ``None`` when it names nothing any more.

    Anything but a ``core:`` address, or one naming a label the map does not
    know, is returned as it is: data step 8 moves only what it can read.
    """
    if not address.startswith(CORE_ADDRESS_PREFIX):
        return address
    label, _, input_name = address[len(CORE_ADDRESS_PREFIX) :].rpartition("/")
    if label not in labels:
        return address
    if labels[label]:
        return f"{CORE_ADDRESS_PREFIX}{labels[label]}/{input_name}"
    if label in stage_slots:
        return f"{stage_slots[label]}/{input_name}"
    return None


def rederive_cores(conn: sqlite3.Connection, include_uncached: bool = True) -> int:
    """Put every topology on core rule v2 and its workflow's state with it.

    Hub data step 8. For each ``workflow_topology_core`` row at the v1 stamp,
    the v2 row is written here, in the hub-open transaction, with every
    variant's base-model families (``workflow_variant_family``), so no card is
    ever pending and the grid never blanks. A v2 workflow is
    ``auto_workflow_id(v2 core, families)``, so a v1 workflow mostly combines
    with others, and **splits** where its variants load different base-model
    families or its graph has no sampler of its own (whose stages stay core).
    ``workflow_core_successor`` records each (topology, new workflow) with the
    label map.

    Each retired ``auto:`` id's owner rows are rewritten through the label
    map, **copied** to every successor but the primary (the one holding most
    of its variants), and carried to the primary with
    :func:`_carry_group_state` (merge, never drop; the olds in sorted order).
    A card's successor row follows its own variants; the retired-id and
    ``from`` rows follow the primary. Manual workflows are not touched: their
    addresses are their own slot labels.

    **Idempotent**: a second run finds no v1 row and writes nothing. A
    topology whose documents will not reduce, or whose move raises, is rolled
    back to its savepoint, logged and restamped ``unmoved-v1`` (so the step
    is not re-run on every open); the card backfill re-derives it, without a
    successor row. A retirement that raises is rolled back the same way. *include_uncached*
    also moves the cards with no cache row at all (the upgrade's run); a later
    run, for v1 rows an older build wrote into a shared hub, leaves those to
    the backfill. Every ``auto:`` id the owner's rows still name that is
    neither live nor retired is logged afterwards (:func:`stranded_workflow_ids`).

    Returns:
        How many topologies moved to v2.
    """
    hub = _Reader(conn)
    v1_rows = dict(
        conn.execute(
            "SELECT topology_hash, core_hash FROM workflow_topology_core "
            "WHERE core_version = ?",
            (_CORE_RULE_V1,),
        ).fetchall()
    )
    cards_of: dict[str, list[Card]] = {}
    for card in card_index(hub):
        if not card.manual:
            cards_of.setdefault(card.topology_hash, []).append(card)
    todo = set(v1_rows)
    if include_uncached:
        # A card with no cache row at all: steps 5 and 6 filed its state on the
        # v1 core computed from its document (`_auto_id`), so it moves too.
        todo |= set(cards_of) - {
            row[0]
            for row in conn.execute(
                "SELECT topology_hash FROM workflow_topology_core "
                "WHERE core_version = ?",
                (CORE_RULE_VERSION,),
            )
        }
    if not todo:
        return 0
    heirs_of: dict[str, Counter] = {}  # old id -> {new id: variants}
    topologies_of: dict[str, set[str]] = {}
    new_of_card: dict[str, str] = {}
    stage_slots_of: dict[str, dict[str, str]] = {}
    labels_of: dict[str, dict[str, Optional[str]]] = {}
    shelf: list = []  # one shelf index for the whole step
    for topology_hash in sorted(todo):
        # A savepoint per topology: this runs at hub open inside the
        # data-version transaction, so an uncaught error would refuse the hub
        # on every start (`convert_card_state` guards the same way).
        conn.execute("SAVEPOINT rederive_topology")
        try:
            moved = _rederive_topology(
                conn,
                hub,
                topology_hash,
                cards_of.get(topology_hash, []),
                v1_rows,
                shelf,
            )
            conn.execute("RELEASE rederive_topology")
        except sqlite3.Error:
            # The hub itself (disk full, I/O): the whole step rolls back and
            # retries on the next open, rather than restamping the topology.
            raise
        except Exception as exc:
            conn.execute("ROLLBACK TO rederive_topology")
            conn.execute("RELEASE rederive_topology")
            logger.error(
                "Topology %s failed to move to core rule v2 (%s: %s); it stays on "
                "v1 until the card backfill re-derives it, and owner state on its "
                "v1 workflow is not carried from it.",
                topology_hash,
                type(exc).__name__,
                exc,
            )
            moved = None
        if moved is None:
            # Tried once: off the v1 stamp, or `_has_v1_cores` re-runs this
            # step on every open forever. Not current either, so the card
            # backfill still picks it up.
            conn.execute(
                "UPDATE workflow_topology_core SET core_version = ? "
                "WHERE topology_hash = ? AND core_version = ?",
                (_CORE_RULE_V1_UNMOVED, topology_hash, _CORE_RULE_V1),
            )
            continue
        old_id, labels, stage_slots, card_heirs = moved
        stage_slots_of[topology_hash] = stage_slots
        labels_of[topology_hash] = labels
        topologies_of.setdefault(old_id, set()).add(topology_hash)
        heirs = heirs_of.setdefault(old_id, Counter())
        for workflow_key, new_id, variants in card_heirs:
            new_of_card[workflow_key] = new_id
            heirs[new_id] += variants

    bases = {w.workflow_id: w.base_topology for w in workflow_index(hub)}
    for old_id, heirs in sorted(heirs_of.items()):
        if not heirs:
            continue
        primary = _primary(heirs)
        if len(heirs) > 1:
            logger.info(
                "Workflow %s splits under core rule v2 (base-model families, or "
                "a graph with no sampler): %s. Its owner state is copied to each.",
                old_id,
                dict(heirs),
            )
        labels: dict[str, Optional[str]] = {}
        for topology_hash in sorted(topologies_of[old_id]):
            for old, new in labels_of[topology_hash].items():
                # A label one topology pruned (None) yields to one another
                # topology of the same workflow keeps, or its state is dropped.
                if labels.get(old) is None:
                    labels[old] = new
        base = bases.get(primary)
        # ponytail: one stage-slot resolution, the primary's base; a split
        # successor on another base topology gets the same slot labels.
        conn.execute("SAVEPOINT retire_workflow")
        try:
            _retire_workflow(
                conn,
                old_id,
                heirs,
                new_of_card,
                labels,
                stage_slots_of[base] if base in topologies_of[old_id] else {},
            )
            conn.execute("RELEASE retire_workflow")
        except sqlite3.Error:
            raise
        except Exception as exc:
            conn.execute("ROLLBACK TO retire_workflow")
            conn.execute("RELEASE retire_workflow")
            # Its topologies are on v2 already, so its owner rows stay on the
            # retired id, where the stranded-id report below names them.
            logger.error(
                "Workflow %s: its owner state failed to carry to %s (%s: %s); "
                "it stays on the old id.",
                old_id,
                dict(heirs),
                type(exc).__name__,
                exc,
            )
    stranded = stranded_workflow_ids(
        hub,
        {w.workflow_id for w in workflow_index(hub)},
        {
            row[0]
            for row in conn.execute("SELECT workflow_id FROM workflow_id_successor")
        },
    )
    if stranded:
        logger.warning(
            "Core rule v2: %d automatic workflow id(s) the owner's rows name are "
            "neither live nor retired, so what is stored on them shows nowhere: "
            "%s",
            len(stranded),
            stranded,
        )
    moved = sum(len(t) for t in topologies_of.values())
    logger.info(
        "Core rule v2: %d topologies re-derived; %d workflows become %d.",
        moved,
        len(heirs_of),
        len({h for heirs in heirs_of.values() for h in heirs}),
    )
    return moved


def _rederive_topology(
    conn: sqlite3.Connection,
    hub,
    topology_hash: str,
    cards: list[Card],
    v1_rows: dict[str, str],
    shelf: list,
) -> Optional[tuple[str, dict, dict, list[tuple[str, str, int]]]]:
    """Write one topology's v2 rows; ``None`` when no stored graph reduces.

    Returns ``(old workflow id, label map, stage slots, [(card, new id,
    variants)])`` for :func:`rederive_cores` to retire the old id with.
    """
    documents = {c.workflow_key: card_document(hub, c) for c in cards}
    found = next(filter(None, documents.values()), None)
    if found is None:
        logger.warning(
            "Topology %s has no stored graph that reduces, so it stays on "
            "core rule v1 until the card backfill re-derives it; owner state "
            "on its v1 workflow is not carried from it.",
            topology_hash,
        )
        return None
    document = found[1]
    derived = graph_key(_core_strip_v1(document))
    old_core = v1_rows.get(topology_hash, derived)
    if derived != old_core:
        logger.warning(
            "Topology %s: its stored v1 core %s is not what v1 derives now; "
            "its label map may name the wrong nodes.",
            topology_hash,
            old_core,
        )
    new_core = core_hash(document, strip_loras=STRIP_LORAS_FOR_STACKS)
    labels, stage_slots = core_label_maps(document)
    _cache_topology(conn, topology_hash, document, slots(document), new_core)
    old_id = f"{AUTO_STACK_PREFIX}{old_core}"
    card_heirs = []
    for card in cards:
        # One family set per card: its key holds its base models. A card
        # none of whose own variants reduces gets none, never another
        # card's: it stays pending for the backfill, and its state goes
        # with its workflow's primary.
        if documents[card.workflow_key] is None:
            logger.warning(
                "Card %s: no variant of it reduces, so it gets no base-model "
                "family and stays pending.",
                card.workflow_key,
            )
            continue
        structural_hash, card_doc = documents[card.workflow_key]
        families = card.families.get(structural_hash)
        if families is None:
            families = variant_families(hub, structural_hash, card_doc, shelf)
        if UNRESOLVED_FAMILY in families.split(","):
            logger.warning(
                "Card %s (variants %s): a base-model loader names no model in "
                "its document or any stored run; it shares the '%s' family "
                "with every other such card of its core.",
                card.workflow_key,
                card.variants,
                UNRESOLVED_FAMILY,
            )
        conn.executemany(
            "INSERT OR IGNORE INTO workflow_variant_family "
            "(structural_hash, families) VALUES (?, ?)",
            [(variant, families) for variant in card.variants],
        )
        new_id = auto_workflow_id(new_core, families)
        card_heirs.append((card.workflow_key, new_id, len(card.variants)))
        conn.execute(
            "INSERT OR IGNORE INTO workflow_core_successor "
            "(topology_hash, old_workflow_id, new_workflow_id, label_map) "
            "VALUES (?, ?, ?, ?)",
            (topology_hash, old_id, new_id, json.dumps(labels, sort_keys=True)),
        )
    return old_id, labels, stage_slots, card_heirs


def _retire_workflow(
    conn: sqlite3.Connection,
    old_id: str,
    heirs: Counter,
    new_of_card: dict[str, str],
    labels: dict[str, Optional[str]],
    stage_slots: dict[str, str],
) -> None:
    """Hand a retired automatic workflow's owner state to its successors.

    *heirs* is ``{new id: variants}``. The owner rows are rewritten through
    *labels*, **copied** to every heir but the primary (the most variants),
    and merged onto the primary, never dropped (:func:`_carry_group_state`).
    A card's successor row follows its own variants (*new_of_card*); the
    retired-id and ``from`` rows follow the primary, and the retired id is
    recorded so the vault's conversion re-files a recipe naming it.
    """
    primary = _primary(heirs)
    # An heir is live by definition: never on the retired list it may have
    # been on before (a family the shelf once knew, then forgot).
    conn.executemany(
        "DELETE FROM workflow_id_successor WHERE workflow_id = ?",
        [(heir,) for heir in heirs],
    )
    _rewrite_addresses(conn, old_id, labels, stage_slots)
    had_row = conn.execute(
        "DELETE FROM workflow_group WHERE workflow_id = ?", (old_id,)
    ).rowcount
    for heir in [h for h in sorted(heirs) if h != primary] + [primary]:
        _carry_group_state(conn, old_id, heir, keep=heir != primary)
        if had_row:
            conn.execute(
                "INSERT OR IGNORE INTO workflow_group "
                "(workflow_id, kind, core_hash) VALUES (?, 'auto', ?)",
                (heir, heir[len(AUTO_STACK_PREFIX) :]),
            )
    for (workflow_key,) in conn.execute(
        "SELECT workflow_key FROM workflow_key_successor WHERE workflow_id = ?",
        (old_id,),
    ).fetchall():
        conn.execute(
            "UPDATE workflow_key_successor SET workflow_id = ? WHERE workflow_key = ?",
            (new_of_card.get(workflow_key, primary), workflow_key),
        )
    for sql in (
        "UPDATE workflow_id_successor SET successor_id = ? WHERE successor_id = ?",
        "UPDATE workflow_document SET from_workflow_id = ? WHERE from_workflow_id = ?",
    ):
        conn.execute(sql, (primary, old_id))
    # Puts a recipe naming it in front of the vault's conversion, which
    # re-files it on its own card's workflow (the primary only as a fallback).
    conn.execute(
        "INSERT OR IGNORE INTO workflow_id_successor (workflow_id, successor_id) "
        "VALUES (?, ?)",
        (old_id, primary),
    )


# Where an owner's rows name a workflow id: (table, column).
_WORKFLOW_ID_COLUMNS = (
    ("workflow_group", "workflow_id"),
    ("workflow_group_attr", "workflow_id"),
    ("workflow_group_default", "workflow_id"),
    ("workflow_group_pins", "workflow_id"),
    ("workflow_group_picture_input", "workflow_id"),
    ("workflow_key_successor", "workflow_id"),
    ("workflow_id_successor", "successor_id"),
    ("workflow_document", "from_workflow_id"),
)


def stranded_workflow_ids(hub, live: set[str], retired: set[str]) -> list[str]:
    """Every ``auto:`` id the owner's rows name that is neither *live* nor *retired*.

    What would show nowhere: state on a workflow no variant is in and no
    successor row leads away from. A table this hub does not have yet is
    skipped (the dry run reads a hub before the upgrade).
    """
    tables = {row[0] for row in hub.fetchall("SELECT name FROM sqlite_master")}
    named = set()
    for table, column in _WORKFLOW_ID_COLUMNS:
        if table in tables:
            named.update(
                row[0]
                for row in hub.fetchall(
                    f"SELECT DISTINCT {column} FROM {table} WHERE {column} LIKE ?",
                    (f"{AUTO_STACK_PREFIX}%",),
                )
            )
    return sorted(named - live - retired)


def reidentify_families(hub) -> dict:
    """Move variants whose unknown base model the shelf has since identified.

    A base model of no known family is a family of its own
    (``workflow_cards.variant_families``), frozen with the variant. Once the
    shelf knows its family (a scan, or the owner setting the base model),
    every variant whose family set carries such a key and now derives a set
    with fewer unknowns is moved: its family row rewritten, and its retiring
    workflow's owner state carried with :func:`_retire_workflow`, as data step
    8 carries it. The core does not change, so ``workflow_core_successor``
    gets an empty label map: the vault's conversion re-files a recipe on its
    own card's workflow and rewrites nothing.

    Returns:
        ``{"moved": variants moved, "keys": every old and new id involved,
        "renamed": {retired id: the heir holding most of its variants}}``.
        A retired id is one no variant is left in; an old id that lives on
        is in ``keys`` and not in ``renamed``.
    """
    rows = hub.fetchall(
        "SELECT vf.structural_hash AS structural_hash, vf.families AS families, "
        "v.topology_hash AS topology_hash, v.workflow_key AS workflow_key, "
        "c.core_hash AS core_hash FROM workflow_variant_family vf "
        "JOIN workflow_variant v ON v.structural_hash = vf.structural_hash "
        "AND v.key_version = ? "
        "JOIN workflow_topology_core c ON c.topology_hash = v.topology_hash "
        "AND c.core_version = ? "
        "WHERE vf.families LIKE '%asset:%' OR vf.families LIKE '%unresolved%' "
        "ORDER BY vf.structural_hash",
        (WORKFLOW_KEY_VERSION, CORE_RULE_VERSION),
    )
    moves = []
    shelf: list = []
    for row in rows:
        card = Card(
            workflow_key=row["workflow_key"],
            topology_hash=row["topology_hash"],
            variants=[row["structural_hash"]],
        )
        found = card_document(hub, card)
        if found is None:
            continue
        families = variant_families(hub, row["structural_hash"], found[1], shelf)
        if _unknowns(families) < _unknowns(row["families"]):
            moves.append((row, families))
    if not moves:
        return {"moved": 0, "keys": [], "renamed": {}}
    renamed: dict[str, str] = {}
    with hub.transaction() as conn:
        heirs_of: dict[str, Counter] = {}
        # Per old workflow, per card: where its moved variants went.
        cards_of: dict[str, dict[str, Counter]] = {}
        new_of_card: dict[str, str] = {}
        parts: dict[str, tuple[str, str]] = {}
        for row, families in moves:
            old_id = auto_workflow_id(row["core_hash"], row["families"])
            parts[old_id] = (row["families"], row["core_hash"])
            new_id = auto_workflow_id(row["core_hash"], families)
            conn.execute(
                "UPDATE workflow_variant_family SET families = ? "
                "WHERE structural_hash = ?",
                (families, row["structural_hash"]),
            )
            conn.execute(
                "INSERT OR IGNORE INTO workflow_core_successor (topology_hash, "
                "old_workflow_id, new_workflow_id, label_map) VALUES (?, ?, ?, '{}')",
                (row["topology_hash"], old_id, new_id),
            )
            heirs_of.setdefault(old_id, Counter())[new_id] += 1
            cards_of.setdefault(old_id, {}).setdefault(row["workflow_key"], Counter())[
                new_id
            ] += 1
            new_of_card[row["workflow_key"]] = new_id
        # A variant still in the old workflow, for the workflow or one card.
        still_in = (
            "SELECT 1 FROM workflow_variant v JOIN workflow_variant_family vf "
            "ON vf.structural_hash = v.structural_hash "
            "JOIN workflow_topology_core c ON c.topology_hash = v.topology_hash "
            "AND c.core_version = ? WHERE v.key_version = ? "
            "AND vf.families = ? AND c.core_hash = ?"
        )
        for old_id, heirs in sorted(heirs_of.items()):
            if not hub.fetchone(
                still_in, (CORE_RULE_VERSION, WORKFLOW_KEY_VERSION, *parts[old_id])
            ):
                _retire_workflow(conn, old_id, heirs, new_of_card, {}, {})
                renamed[old_id] = _primary(heirs)
            else:
                # The workflow lives on, so the retired-id row cannot carry a
                # recipe on a moved card: this row does, for the vault's
                # conversion to re-file it on its card's workflow. Only a card
                # none of whose variants stayed: a recipe does not say which
                # variant it was saved from.
                conn.executemany(
                    "INSERT OR REPLACE INTO workflow_card_move "
                    "(workflow_key, old_workflow_id, new_workflow_id) "
                    "VALUES (?, ?, ?)",
                    [
                        (key, old_id, _primary(went))
                        for key, went in sorted(cards_of[old_id].items())
                        if not hub.fetchone(
                            f"{still_in} AND v.workflow_key = ?",
                            (
                                CORE_RULE_VERSION,
                                WORKFLOW_KEY_VERSION,
                                *parts[old_id],
                                key,
                            ),
                        )
                    ],
                )
                logger.info(
                    "Workflow %s keeps variants the shelf has not identified; "
                    "its owner state stays and is copied to %s.",
                    old_id,
                    sorted(heirs),
                )
                for heir in sorted(heirs):
                    _carry_group_state(conn, old_id, heir, keep=True)
    logger.info(
        "Base-model families: %d variants moved to the workflow of the family "
        "the shelf now knows.",
        len(moves),
    )
    return {
        "moved": len(moves),
        "keys": sorted(
            set(heirs_of) | {heir for heirs in heirs_of.values() for heir in heirs}
        ),
        "renamed": renamed,
    }


def _primary(heirs: Counter) -> str:
    """The heir holding most of a retired workflow's variants, ties by id."""
    return min(heirs, key=lambda h: (-heirs[h], h))


def _unknowns(families: str) -> int:
    return sum(
        1
        for family in families.split(",")
        if family.startswith("asset:") or family == UNRESOLVED_FAMILY
    )


def _rewrite_addresses(
    conn: sqlite3.Connection,
    workflow_id: str,
    labels: dict[str, Optional[str]],
    stage_slots: dict[str, str],
) -> None:
    """Rewrite *workflow_id*'s ``core:`` addresses onto the v2 core, in place."""
    for table in ("workflow_group_default", "workflow_group_picture_input"):
        cursor = conn.execute(
            f"SELECT * FROM {table} WHERE workflow_id = ?", (workflow_id,)
        )
        columns = [column[0] for column in cursor.description]
        at = columns.index("address")
        found = cursor.fetchall()
        conn.execute(f"DELETE FROM {table} WHERE workflow_id = ?", (workflow_id,))
        for row in found:
            address = rewritten_address(row[at], labels, stage_slots)
            if address is None:
                logger.warning(
                    "Workflow %s: %s row %s names a node core rule v2 removed and "
                    "no stage of the new base holds, so it is dropped.",
                    workflow_id,
                    table,
                    tuple(row),
                )
                continue
            values = list(row)
            values[at] = address
            if not conn.execute(
                f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' * len(columns))})",
                values,
            ).rowcount:
                logger.warning(
                    "Workflow %s: %s row %s lands on an address another row "
                    "already holds; the first is kept.",
                    workflow_id,
                    table,
                    tuple(row),
                )
    row = conn.execute(
        "SELECT pins FROM workflow_group_pins WHERE workflow_id = ?", (workflow_id,)
    ).fetchone()
    if row is None:
        return
    pins = []
    for pin in json.loads(row[0]):
        address = rewritten_address(pin, labels, stage_slots)
        if address is None:
            logger.warning(
                "Workflow %s: pin %s names a node core rule v2 removed; it is dropped.",
                workflow_id,
                pin,
            )
        elif address not in pins:
            pins.append(address)
    conn.execute(
        "UPDATE workflow_group_pins SET pins = ? WHERE workflow_id = ?",
        (json.dumps(pins), workflow_id),
    )


def _carry_group_state(
    conn: sqlite3.Connection, group: str, heir: str, *, keep: bool = False
) -> None:
    """Move *group*'s owner rows onto *heir*, the heir's own winning.

    *keep* copies instead: *group*'s rows stay, for the next heir of a split.
    """
    attr = conn.execute(
        "SELECT name, notes, hidden FROM workflow_group_attr WHERE workflow_id = ?",
        (group,),
    ).fetchone()
    if attr is not None:
        mine = conn.execute(
            "SELECT name, notes FROM workflow_group_attr WHERE workflow_id = ?",
            (heir,),
        ).fetchone()
        if mine is None:
            conn.execute(
                "INSERT INTO workflow_group_attr (workflow_id, name, notes, hidden) "
                "VALUES (?, ?, ?, ?)",
                (heir, *attr),
            )
        elif (attr[0] and attr[0] != mine[0]) or attr[1]:
            heading = attr[0] or "From a merged workflow"
            carried = f"{heading}:\n{attr[1]}" if attr[1] else f"Also named: {heading}"
            # A second carry to the same heir (a re-run, or a split's copy
            # meeting its primary) must not say the same thing twice. Matched
            # as whole paragraphs, so "Also named: Foo" is not taken for a
            # repeat of "Also named: Foo bar"; a carry may itself hold blank
            # lines, so this is a bounded substring rather than a split.
            if f"\n\n{carried}\n\n" not in f"\n\n{mine[1] or ''}\n\n":
                conn.execute(
                    "UPDATE workflow_group_attr SET notes = ? WHERE workflow_id = ?",
                    ("\n\n".join(filter(None, [mine[1], carried])), heir),
                )
    pins = [
        conn.execute(
            "SELECT pins FROM workflow_group_pins WHERE workflow_id = ?", (wid,)
        ).fetchone()
        for wid in (heir, group)
    ]
    both_pinned = pins[0] is not None and pins[1] is not None
    if both_pinned:
        # Both pinned: the heir's pins first, then the group's it lacks.
        mine = json.loads(pins[0][0])
        merged = mine + [pin for pin in json.loads(pins[1][0]) if pin not in mine]
        conn.execute(
            "UPDATE workflow_group_pins SET pins = ? WHERE workflow_id = ?",
            (json.dumps(merged), heir),
        )
    for table, key in (
        ("workflow_group_default", "address"),
        ("workflow_group_pins", None),
        ("workflow_group_picture_input", "address"),
    ):
        if table == "workflow_group_pins" and both_pinned:
            if not keep:
                conn.execute(f"DELETE FROM {table} WHERE workflow_id = ?", (group,))
            continue
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        selected = ", ".join("?" if c == "workflow_id" else c for c in columns)
        moved = conn.execute(
            f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) "
            f"SELECT {selected} FROM {table} WHERE workflow_id = ?",
            (heir, group),
        ).rowcount
        left = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE workflow_id = ?", (group,)
        ).fetchone()[0]
        if left > moved:
            # Named with their values: the heir's win, so these are what lost.
            logger.warning(
                "Workflow %s: %d %s row(s) not carried to %s, which has its own "
                "for the same %s; the rows it held were %s",
                group,
                left - moved,
                table,
                heir,
                key or "workflow",
                [
                    tuple(row)
                    for row in conn.execute(
                        f"SELECT * FROM {table} WHERE workflow_id = ?", (group,)
                    )
                ],
            )
        if not keep:
            conn.execute(f"DELETE FROM {table} WHERE workflow_id = ?", (group,))
    if not keep:
        conn.execute("DELETE FROM workflow_group_attr WHERE workflow_id = ?", (group,))


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
    """The card's automatic workflow id, from the cache or a stored graph.

    A topology cached under the current rule gives the current id (core and
    the card's base-model families). One that is not (a hub upgrading through
    steps 5 and 6) gives the v1 id, ``auto:<v1 core>``, which data step 8 then
    moves with every other v1 id. A file-only card (#1466: a workflow file
    with no recipe) is its own workflow, ``auto:<topology hash>``, unless
    another card of its topology has a core hash.
    """
    if topology_core:
        families = next((f for f in card.families.values() if f is not None), None)
        if families is None:
            found = card_document(hub, card)
            if found is None:
                return None
            families = variant_families(hub, found[0], found[1])
        return auto_workflow_id(topology_core, families)
    if not card.variants:
        return f"{AUTO_STACK_PREFIX}{card.topology_hash}"
    found = card_document(hub, card)
    if found is None:
        return None
    try:
        # The v1 id, as steps 5 and 6 were written against: data step 8 moves
        # it on, with the cached ones, to the workflow the current rule makes.
        return f"{AUTO_STACK_PREFIX}{graph_key(_core_strip_v1(found[1]))}"
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
