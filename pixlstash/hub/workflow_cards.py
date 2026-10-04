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
from pixlstash.services.model_shelf_service import (
    base_model_family,
    models_for_digest,
    recipe_asset_index,
)
from pixlstash.services.workflow_hash import (
    _digest,
    asset_reference,
    is_link,
    normalized_filename,
)
from pixlstash.services.workflow_identity import (
    ASSET_REFERENCE_PREFIX,
    CORE_VERSION,
    RECIPE,
    STRUCTURAL,
    WORKFLOW_KEY_VERSION,
    LoaderSwap,
    Slot,
    base_model_kind,
    core_hash,
    core_node_ids,
    graph_traits,
    guess_mark,
    lora_assets,
    slots,
    special_groups,
    unswapped,
    workflow_key,
    workflow_type,
)
from pixlstash.utils.known_base_models import family_of, identify
from pixlstash.utils.sql_chunking import chunked
from pixlstash.utils.workflow_ids import AUTO_PREFIX

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
    "LEFT JOIN workflow_variant_family vf ON vf.structural_hash = r.structural_hash "
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
    "(v.structural_hash IS NULL OR c.topology_hash IS NULL OR c.specials IS NULL "
    "OR c.traits IS NULL OR vf.structural_hash IS NULL)"
)


def auto_workflow_id(core: str, families: str) -> str:
    """An automatic workflow's id: its core and its base-model families.

    Workflows whose base models are of different families never combine, so
    the family set (:func:`variant_families`) is part of the id.
    """
    return f"{AUTO_PREFIX}{_digest([core, families])}"


# A base model named by digest rather than by file: A1111's `Model hash`
# (`a1111_recipe._CHECKPOINT_HASHES`). Beside `base_model_kind`'s widgets,
# which include the shelf loader's `checkpoint_id`.
_BASE_DIGEST_WIDGETS = frozenset({"ckpt_sha256", "unet_sha256"})

# What a base-model loader names when neither its document nor any stored run
# says which model: a family of its own, never "no base model".
UNRESOLVED_FAMILY = "unresolved"


def variant_families(
    hub,
    structural_hash: str,
    document: dict,
    shelf: Optional[list] = None,
    core: Optional[str] = None,
) -> str:
    """The base-model families a variant loads, sorted and comma-joined.

    One family per base-model loader node (``base_model_kind``'s widgets and
    a base model's digest widget), from whichever of its values resolves: the
    file the stored reference names (``workflow_recipe_asset``, by digest,
    never by slot order), a shelf id or a digest through the shelf. The
    shelf's family where its candidate rows agree on one
    (``base_model_family``), else the family the filename identifies. A
    document that nulled the value (an older hash version) is read through a
    stored run of the variant. A model of no known family is a family of its
    own, spelled by its asset reference so no filename lands here; a loader
    naming no model at all is :data:`UNRESOLVED_FAMILY`. Both split rather
    than combine, logged. "No base model" (the empty string) is a graph with
    no base-model loader. Only loaders the core keeps count: an orphan loader
    the core rule pruned loads nothing into the workflow. Frozen by the caller
    on first sight, as a slot mark
    is; :func:`~pixlstash.hub.workflow_group_convert.reidentify_families`
    moves an unknown one once the shelf learns it. *shelf* is a cache of the
    shelf index a caller deriving many variants passes to every call.

    **A loader naming no model takes its core's one known family set**
    (:func:`_sibling_families`), when the caller says which *core* the
    variant is on, every other variant of that core whose families are
    known agrees on one set, that set holds every family this variant's own
    loaders did resolve, and none of them loads a model of unknown family. A shelf loader filed before its id was kept,
    or left blank, is the same graph as its siblings with the value
    missing, and a workflow of its own named "Text to Image" was the cost
    of reading it as a family nobody else has.

    ponytail: the sibling set is read when the variant is derived, so the
    answer depends on what was filed before it: a variant adopted while its
    core had one known set keeps it after a second set arrives. Two models of
    different families whose loaders name no model anywhere, on a core with
    no single known set, share :data:`UNRESOLVED_FAMILY` and combine; a stored
    run arriving later does not re-trigger the family pass.
    """
    names = {
        asset_reference(name): name
        for (name,) in hub.fetchall(
            "SELECT normalized_filename FROM workflow_recipe_asset "
            "WHERE structural_hash = ?",
            (structural_hash,),
        )
    }
    shelf = shelf if shelf is not None else []
    runs: Optional[list[dict]] = None
    families = set()
    kept = core_node_ids(document, strip_loras=STRIP_LORAS_FOR_STACKS)
    for node_id, node in sorted(document.items()):
        if str(node_id) not in kept:
            continue
        inputs = node.get("inputs") if isinstance(node, dict) else None
        widgets = [
            widget
            for widget in (inputs or {})
            if base_model_kind(widget) or widget in _BASE_DIGEST_WIDGETS
        ]
        if not widgets:
            continue
        stored = [inputs[w] for w in widgets if isinstance(inputs[w], str)]
        if not stored:
            if runs is None:
                runs = _stored_runs(hub, structural_hash)
            for run in runs:
                for widget in widgets:
                    value = ((run.get(node_id) or {}).get("inputs") or {}).get(widget)
                    if value in (None, "") or is_link(value):
                        continue
                    # A run stores a reference as the document does, or (an
                    # older one) the raw value.
                    value = str(value)
                    if not value.startswith(ASSET_REFERENCE_PREFIX):
                        names[asset_reference(normalized_filename(value))] = (
                            normalized_filename(value)
                        )
                        value = asset_reference(normalized_filename(value))
                    stored.append(value)
        candidates = [names[ref] for ref in stored if ref in names]
        family = next(
            filter(None, (_family_of_name(hub, name, shelf) for name in candidates)),
            None,
        )
        if family is None:
            family = stored[0] if stored else UNRESOLVED_FAMILY
            logger.info(
                "Variant %s: the base model of node %s has no known family (%s), "
                "so its workflow combines with no other until the shelf learns it.",
                structural_hash,
                node_id,
                family,
            )
        families.add(family)
    if UNRESOLVED_FAMILY in families and core is not None:
        sibling = _sibling_families(hub, core, structural_hash)
        known = {f for f in families if not _has_unknown(f)}
        # An unidentified model (`asset:`) is a model this loader does load;
        # adopting would drop it, so only `unresolved` may be the unknown.
        if (
            sibling is not None
            and known | {UNRESOLVED_FAMILY} == families
            and known <= set(sibling.split(","))
        ):
            logger.info(
                "Variant %s: a base-model loader names no model, so it takes "
                "the families %r every other variant of its core has.",
                structural_hash,
                sibling,
            )
            return sibling
    return ",".join(sorted(families))


def _sibling_families(hub, core: str, structural_hash: str) -> Optional[str]:
    """The one known family set the other variants of *core* share, else ``None``.

    Known means no unresolved loader and no unidentified model in it; two
    known sets, or none, is not an answer.
    """
    found = {
        families
        for (families,) in hub.fetchall(
            "SELECT DISTINCT vf.families FROM workflow_variant_family vf "
            "JOIN workflow_variant v ON v.structural_hash = vf.structural_hash "
            "AND v.key_version = ? "
            "JOIN workflow_topology_core c ON c.topology_hash = v.topology_hash "
            "AND c.core_version = ? "
            "WHERE c.core_hash = ? AND vf.structural_hash != ?",
            (WORKFLOW_KEY_VERSION, CORE_RULE_VERSION, core, structural_hash),
        )
        if not _has_unknown(families)
    }
    return found.pop() if len(found) == 1 else None


def _has_unknown(families: str) -> bool:
    return any(
        family.startswith(ASSET_REFERENCE_PREFIX) or family == UNRESOLVED_FAMILY
        for family in families.split(",")
    )


def shelf_family_signature(hub) -> str:
    """A digest of what a family pass can learn from.

    The shelf's base models, and the known family sets of each core that
    holds an unresolved variant: a variant
    derived after both last changed already has every family they give it,
    so only a change here can identify an unknown one (a loader naming no
    model takes its core's one known set, :func:`_sibling_families`, and a
    sibling filed after it is such a change). The rule version is in it too,
    so a build that derives families differently passes again over an
    unchanged hub.
    """
    return _digest(
        [CORE_RULE_VERSION, "siblings"]
        + [
            list(row)
            for row in hub.fetchall(
                "SELECT id, base_model, base_model_canonical FROM model ORDER BY id"
            )
        ]
        + [
            list(row)
            for row in hub.fetchall(
                "SELECT DISTINCT c.core_hash, vf.families "
                "FROM workflow_variant_family vf "
                "JOIN workflow_variant v ON v.structural_hash = vf.structural_hash "
                "AND v.key_version = ? "
                "JOIN workflow_topology_core c ON c.topology_hash = v.topology_hash "
                "AND c.core_version = ? "
                "WHERE vf.families NOT LIKE '%asset:%' "
                "AND vf.families NOT LIKE '%unresolved%' "
                # Only cores holding an unresolved variant: a new graph shape
                # anywhere else has nothing to teach a pass.
                "AND c.core_hash IN (SELECT c2.core_hash "
                "FROM workflow_variant_family vf2 "
                "JOIN workflow_variant v2 ON v2.structural_hash = vf2.structural_hash "
                "AND v2.key_version = ? "
                "JOIN workflow_topology_core c2 "
                "ON c2.topology_hash = v2.topology_hash AND c2.core_version = ? "
                "WHERE vf2.families LIKE '%unresolved%') "
                "ORDER BY c.core_hash, vf.families",
                (WORKFLOW_KEY_VERSION, CORE_RULE_VERSION) * 2,
            )
        ]
    )


def _stored_runs(hub, structural_hash: str) -> list[dict]:
    """The variant's stored runs (``workflow_recipe_instance``), unreadable ones left out."""
    runs = []
    for (raw,) in hub.fetchall(
        "SELECT document FROM workflow_recipe_instance WHERE structural_hash = ? "
        "ORDER BY library_uuid, instance_hash",
        (structural_hash,),
    ):
        try:
            run = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "A stored run of %s will not parse: %s", structural_hash, exc
            )
            continue
        if isinstance(run, dict):
            runs.append(run)
    return runs


def _family_of_name(hub, name: str, shelf: list) -> Optional[str]:
    """The family of one base-model value: filename, shelf id or digest.

    *shelf* caches the shelf index across calls (empty until first needed).
    """
    if not shelf:
        by_name, by_digest, _, _ = recipe_asset_index(hub)
        shelf.extend((by_name, by_digest, sorted(by_digest)))
    ids = set(shelf[0].get(name, ())) | models_for_digest(name, shelf[1], shelf[2])
    if name.isdigit():
        ids.add(int(name))
    rows = hub.fetchall(
        "SELECT base_model, base_model_canonical, filename FROM model "
        f"WHERE id IN ({','.join(str(int(i)) for i in ids) or 'NULL'})"
    )
    for found in (
        {base_model_family(row) for row in rows},
        # A row the shelf has not identified: its own base-model text and
        # filename, as the shelf's identification would read them.
        {
            family_of(identify([row["base_model"]], [row["filename"]])[0])
            for row in rows
        },
    ):
        found.discard(None)
        if len(found) == 1:
            return found.pop()
    return family_of(identify([], [name])[0])


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
        "c.core_hash AS core_hash, "
        "c.specials AS core_specials, c.traits AS core_traits, "
        "vf.families AS families "
        f"{_VARIANT_JOIN} WHERE r.structural_hash = ?",
        (*_VARIANT_VERSIONS, structural_hash),
    )
    if row is None:
        return None
    # The same conditions `_VARIANT_PENDING` selects on, spelled here as
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
    # `traits` (#1722) is the same kind of gap and is filled by the same UPDATE.
    specials_missing = row["core_specials"] is None or row["core_traits"] is None
    if (
        row["workflow_key"] is not None
        and not core_missing
        and not specials_missing
        and row["families"] is not None
    ):
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
    families = (
        variant_families(hub, structural_hash, document, core=core or row["core_hash"])
        if row["families"] is None
        else None
    )
    # The specials-only path's CPU, outside the lock for the same reason.
    cached = (
        None
        if core is not None or not specials_missing
        else (
            ",".join(special_groups(document)),
            ",".join(graph_traits(document, strip_loras=STRIP_LORAS_FOR_STACKS)),
        )
    )
    with hub.transaction() as conn:
        marks = _freeze_marks(conn, topology_hash, structural_hash, document_slots)
        if core is not None:
            _cache_topology(conn, topology_hash, document, document_slots, core)
        elif cached is not None:
            # The whole cost of the upgrade for an already-cached topology: a
            # reduction and a strip, no refinement, and the row's stack key, type and slots
            # are left exactly where the grid is already reading them.
            # `core_version` is in the WHERE so a row re-stamped under another
            # rule between the read and here is not written by this branch.
            conn.execute(
                "UPDATE workflow_topology_core SET specials = ?, traits = ? "
                "WHERE topology_hash = ? AND core_version = ?",
                (
                    *cached,
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
        if families is not None:
            # IGNORE is the freeze: what the shelf says later never moves it.
            conn.execute(
                "INSERT OR IGNORE INTO workflow_variant_family "
                "(structural_hash, families) VALUES (?, ?)",
                (structural_hash, families),
            )
        revive_workflows(conn, [structural_hash])
    return key


def revive_workflows(conn: sqlite3.Connection, structural_hashes: list[str]) -> None:
    """Take the workflows these variants are in off the retired list.

    A retired id (``workflow_id_successor``) is a digest of a core and a family
    set, so a variant can bring it back: a checkpoint renamed back to a name
    the shelf does not know derives the unknown family it was retired from.
    Left on the list, the vault's conversion would re-file every recipe on
    the living workflow, on every sweep.
    """
    for structural_hash in structural_hashes:
        row = conn.execute(
            "SELECT c.core_hash, vf.families FROM workflow_variant v "
            "JOIN workflow_topology_core c ON c.topology_hash = v.topology_hash "
            "AND c.core_version = ? "
            "JOIN workflow_variant_family vf ON vf.structural_hash = v.structural_hash "
            "WHERE v.structural_hash = ?",
            (CORE_RULE_VERSION, structural_hash),
        ).fetchone()
        if row is None:
            continue
        live = auto_workflow_id(row[0], row[1])
        if conn.execute(
            "DELETE FROM workflow_id_successor WHERE workflow_id = ?", (live,)
        ).rowcount:
            logger.info(
                "Workflow %s is live again (variant %s), so it is no longer retired.",
                live,
                structural_hash,
            )


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
        "(topology_hash, core_hash, core_version, workflow_type, slots, specials, "
        "traits) VALUES (?, ?, ?, ?, ?, ?, ?)",
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
            ",".join(graph_traits(document, strip_loras=STRIP_LORAS_FOR_STACKS)),
        ),
    )


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
