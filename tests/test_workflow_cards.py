"""The hub's workflow cards: the tables, the filing hook and the backfill (B2).

The graph fixture is the one the identity rules were written against
(``tests/test_workflow_identity.py``), so a card asserted here and a key
asserted there cannot drift apart.
"""

import json
import logging
import re
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from pixlstash.hub import workflow_cards
from pixlstash.services.workflow_hash import structural_document, topology_hash
from pixlstash.hub.workflow_cards import record_identity
from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflows import (
    forget_asset_names,
    forget_model_ghosts,
    record_api_graph,
    record_ui_graph,
)
from pixlstash.hub.workflow_card_reads import (
    Card,
    card_index,
    manual_document,
    manual_documents_holding,
    model_fix_labels,
    model_fixes,
    Workflow,
    topologies_in_workflow,
    variants_in_workflow,
    workflow_index,
    workflow_of_topology,
)
from pixlstash.services import workflow_card_service
from pixlstash.services.workflow_run_service import saved_recipe_body
from pixlstash.hub.workflow_card_writes import (
    record_loader_swaps,
    set_model_fix,
)
from pixlstash.hub.workflow_group_writes import create_manual_workflow
from pixlstash.services.workflow_card_service import _figures, _superseded_variants
from pixlstash.services.workflow_library_service import CoverCandidate
from pixlstash.services.workflow_identity import (
    CORE_VERSION,
    FACE_DETAILER,
    RECIPE,
    STRUCTURAL,
    UPSCALE,
    WORKFLOW_KEY_VERSION,
    loader_swaps,
    unswapped,
)
from pixlstash.task_runner import TaskCancelledError
from pixlstash.tasks.workflow_card_backfill_finder import WorkflowCardBackfillFinder
from pixlstash.tasks.workflow_card_backfill_task import WorkflowCardBackfillTask
from tests.test_workflow_identity import _graph, _node

CARD_TABLES = (
    "workflow_variant",
    "workflow_topology_core",
    "workflow_slot_mark",
    "workflow_file",
    "workflow_attr",
    "workflow_default_override",
    "workflow_key_pins",
    "workflow_key_picture_input",
    "workflow_cover",
    "workflow_stack",
    "workflow_stack_member",
    "workflow_unstacked",
    "workflow_model_fix",
    "workflow_loader_swap",
)

SPEED_LORA = "test-lightning-8step.safetensors"
CHARACTER_LORA = "test-character.safetensors"
OTHER_CHARACTER_LORA = "test-other-character.safetensors"


@pytest.fixture
def hub(tmp_path):
    database = HubDatabase(str(tmp_path / "hub.db"))
    try:
        yield database
    finally:
        database.close()


def card_of(hub, structural_hash):
    row = hub.fetchone(
        "SELECT workflow_key FROM workflow_variant WHERE structural_hash = ?",
        (structural_hash,),
    )
    return None if row is None else row["workflow_key"]


def all_card_rows(hub):
    return {
        table: sorted(tuple(row) for row in hub.fetchall(f"SELECT * FROM {table}"))
        for table in CARD_TABLES
    }


def _legacy_file(hub, name, topology_hash, structural_hash=None):
    """A ``workflow_file`` row as imports wrote them before manual workflows.

    Nothing writes one any more; hubs upgraded from then still hold them.
    """
    key = (
        workflow_cards.record_identity(hub, structural_hash)
        if structural_hash
        else workflow_cards.topology_only_key(topology_hash)
    )
    with hub.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_file "
            "(workflow_name, topology_hash, structural_hash, workflow_key) "
            "VALUES (?, ?, ?, ?)",
            (name, topology_hash, structural_hash, key),
        )
    return key


def test_the_card_tables_land_in_the_hub(hub):
    """Amended into v2, never a v3: a released build refuses a newer hub."""
    present = {
        row[0]
        for row in hub.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert set(CARD_TABLES) <= present
    assert hub.fetchone("SELECT version FROM schema_version")["version"] == 2


def test_filing_a_graph_gives_it_a_card(hub):
    """The hook, where every caller files: no card is a caller's own omission."""
    keys = record_api_graph(hub, _graph())

    assert card_of(hub, keys.structural_hash) is not None
    cached = hub.fetchone(
        "SELECT * FROM workflow_topology_core WHERE topology_hash = ?",
        (keys.topology_hash,),
    )
    assert cached["workflow_type"] == "txt2img"
    assert cached["core_version"] == workflow_cards.CORE_RULE_VERSION
    assert json.loads(cached["slots"]) == [
        {
            "label": json.loads(cached["slots"])[0]["label"],
            "class_type": "CheckpointLoaderSimple",
            "widget": "ckpt_name",
            "is_lora": False,
        }
    ]


def test_a_character_lora_does_not_fork_the_card(hub):
    """The whole point of a card: one workflow, not one per LoRA."""
    plain = record_api_graph(hub, _graph())
    with_lora = record_api_graph(hub, _graph(loras=(CHARACTER_LORA,)))
    second_lora = record_api_graph(hub, _graph(loras=(OTHER_CHARACTER_LORA,)))

    assert with_lora.structural_hash != second_lora.structural_hash
    assert card_of(hub, with_lora.structural_hash) == card_of(
        hub, second_lora.structural_hash
    )
    # ... and a card the plain graph does not share: it has no LoRA loader at
    # all, which is a different topology.
    assert card_of(hub, plain.structural_hash) != card_of(
        hub, with_lora.structural_hash
    )


def test_a_different_checkpoint_is_a_different_card(hub):
    first = record_api_graph(hub, _graph())
    second = record_api_graph(hub, _graph(ckpt="dreamshaper.safetensors"))

    assert first.topology_hash == second.topology_hash
    assert card_of(hub, first.structural_hash) != card_of(hub, second.structural_hash)


def test_a_speed_lora_forks_the_card(hub):
    """A lightning LoRA changes how the workflow samples, so it is the workflow.

    Pair this with :func:`test_a_mark_is_frozen_on_first_sight`, which files the
    same two graphs the other way round and gets ONE card. That is not a
    contradiction to be reconciled, it is the freeze: a slot's mark is decided
    by the first LoRA seen in it and never recomputed, so ingest order can
    decide which card a later variant joins. The alternative - re-guessing on
    every filing - silently re-keys cards the owner has named, pinned and
    stacked, which is the worse failure.
    """
    speed = record_api_graph(hub, _graph(loras=(SPEED_LORA,)))
    character = record_api_graph(hub, _graph(loras=(CHARACTER_LORA,)))

    assert card_of(hub, speed.structural_hash) != card_of(
        hub, character.structural_hash
    )
    marks = dict(
        tuple(row)
        for row in hub.fetchall("SELECT slot_label, mark FROM workflow_slot_mark")
    )
    assert sorted(marks.values()) == [STRUCTURAL]


def test_a_mark_is_frozen_on_first_sight(hub):
    """The slot was seen holding a character LoRA, so it stays the look.

    The same two graphs as the test above, filed the other way round: the speed
    LoRA now joins the character card rather than forking one. See that test for
    why the freeze is worth the order dependence.
    """
    character = record_api_graph(hub, _graph(loras=(CHARACTER_LORA,)))
    speed = record_api_graph(hub, _graph(loras=(SPEED_LORA,)))

    marks = [tuple(row) for row in hub.fetchall("SELECT mark FROM workflow_slot_mark")]
    assert marks == [(RECIPE,)]
    assert card_of(hub, character.structural_hash) == card_of(
        hub, speed.structural_hash
    )


def test_a_forgotten_model_name_leaves_no_readable_copy(hub):
    """The acceptance criterion, asserted over every row the cards add.

    The mark is the one thing that survives, and it is asserted here rather than
    left to be discovered: it is a decision about the slot that keys the card,
    not a copy of the name, and deleting it would re-key a card the owner may
    have named and stacked. It still says the forgotten file did not look like a
    speed LoRA, which is a classification and not the filename.
    """
    keys = record_api_graph(
        hub, _graph(ckpt="test-private-name.safetensors", loras=(CHARACTER_LORA,))
    )
    _legacy_file(hub, "portrait.json", keys.topology_hash, keys.structural_hash)

    assert forget_asset_names(hub, CHARACTER_LORA) == 1
    assert forget_asset_names(hub, "test-private-name.safetensors") == 1

    written = json.dumps(all_card_rows(hub))
    assert CHARACTER_LORA not in written
    assert "test-private-name" not in written
    assert [
        tuple(row) for row in hub.fetchall("SELECT mark FROM workflow_slot_mark")
    ] == [(RECIPE,)]


def test_a_slot_whose_name_was_forgotten_is_not_guessed_at(hub):
    """A hub upgraded to this build after a name was already forgotten.

    Pre-B2 there are no card rows and no marks at all, which is what the deletes
    below stand for; the recipe and its document are what the older build left.
    The positive control is
    :func:`test_a_speed_lora_forks_the_card`, where the same filename IS
    resolvable and the slot comes out ``structural`` - so this pair fails if the
    name stops being read as well as if an unresolvable one starts being
    guessed at. Precision beats recall: a wrong ``structural`` splits the card
    per LoRA, which is the failure the card exists to prevent.
    """
    keys = record_api_graph(hub, _graph(loras=(SPEED_LORA,)))
    forget_asset_names(hub, SPEED_LORA)
    with hub.transaction() as conn:
        conn.execute("DELETE FROM workflow_slot_mark")
        conn.execute("DELETE FROM workflow_variant")

    workflow_cards.record_identity(hub, keys.structural_hash)

    assert [
        tuple(row) for row in hub.fetchall("SELECT mark FROM workflow_slot_mark")
    ] == [(RECIPE,)]


def test_the_backfill_keys_what_was_filed_before_it_and_runs_twice_the_same(hub):
    """A hub filed by an older build, then the pass, then the pass again.

    The second pass is run with the guard REMOVED - every derived row is wiped
    and derived again from the same stored documents - because a re-run that
    returns early proves only that the early return exists. Byte-identical rows
    is the acceptance criterion, and it is why no card table carries a
    timestamp. ``workflow_file`` is compared as well and is empty here: it is
    written by an import rather than derived, and
    :func:`test_replacing_a_file_moves_it_to_the_new_card` owns it.
    """
    filed = [
        record_api_graph(hub, _graph()),
        record_api_graph(hub, _graph(ckpt="dreamshaper.safetensors")),
        record_api_graph(hub, _graph(loras=(CHARACTER_LORA,), upscale=True)),
    ]
    wipe = (
        "DELETE FROM workflow_variant",
        "DELETE FROM workflow_topology_core",
        # The marks go too, or the second derivation reads back the first one's
        # and the comparison is of a table with itself.
        "DELETE FROM workflow_slot_mark",
    )
    with hub.transaction() as conn:
        for statement in wipe:
            conn.execute(statement)
    finder = WorkflowCardBackfillFinder(hub=hub)
    assert finder.progress() == (3, 3)

    first = finder.find_task()._run_task()
    assert (first["identified"], first["deferred"]) == (3, [])
    after_one_pass = all_card_rows(hub)
    assert finder.progress() == (3, 0)

    # Nothing is outstanding, so the finder hands nothing out ...
    assert finder.find_task() is None
    # ... and a derivation forced over the same documents writes the same rows.
    with hub.transaction() as conn:
        for statement in wipe:
            conn.execute(statement)
    assert (
        WorkflowCardBackfillTask(
            hub=hub, structural_hashes=[keys.structural_hash for keys in filed]
        )._run_task()["identified"]
        == 3
    )
    assert all_card_rows(hub) == after_one_pass


def test_a_superseded_key_rule_re_queues_the_variants_it_keyed(hub):
    """The bump is this PR's answer to a data-version counter, so it is tested.

    Stamping an old ``key_version`` on the rows is what a bumped
    ``WORKFLOW_KEY_VERSION`` looks like to every query here: the finder has to
    hand them back, and the derivation has to rewrite them rather than read the
    stale card and return it.
    """
    keys = record_api_graph(hub, _graph())
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_variant SET workflow_key = 'stale', key_version = 'v0'"
        )
    finder = WorkflowCardBackfillFinder(hub=hub)

    assert finder.progress() == (1, 1)
    assert finder.find_task()._run_task()["identified"] == 1

    row = hub.fetchone("SELECT workflow_key, key_version FROM workflow_variant")
    assert row["key_version"] == WORKFLOW_KEY_VERSION
    assert row["workflow_key"] != "stale"
    assert row["workflow_key"] == card_of(hub, keys.structural_hash)


def test_a_superseded_core_rule_re_queues_the_topologies_it_cached(hub):
    """The other half, and the one a stale early return would swallow.

    A variant whose card is current but whose topology cache is not is still
    outstanding: returning the card and leaving the stale core hash would have
    the finder hand it back on every sweep forever, and would report a grouping
    under a rule this build does not apply.
    """
    record_api_graph(hub, _graph())
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_topology_core SET core_hash = 'stale', core_version = 'v0'"
        )
    finder = WorkflowCardBackfillFinder(hub=hub)

    assert finder.progress() == (1, 1)
    # Nothing is grouped while the only cache row is stamped with the old rule.
    assert workflow_cards.card_grouping(hub)["ungrouped"] == 1

    assert finder.find_task()._run_task()["identified"] == 1

    row = hub.fetchone("SELECT core_hash, core_version FROM workflow_topology_core")
    assert row["core_version"] == workflow_cards.CORE_RULE_VERSION
    assert row["core_hash"] != "stale"
    assert finder.progress() == (1, 0)
    assert workflow_cards.card_grouping(hub)["ungrouped"] == 0


def test_the_cache_records_what_post_processing_a_topology_carries(hub):
    """The card's `+ FaceDetailer` half, cached rather than derived per read.

    Doing it live in `read_grid` means walking every card's stored document on
    every grid read; `workflow_type` and `core_hash` are cached on this row for
    exactly that reason, so this belongs beside them.
    """
    plain = record_api_graph(hub, _graph())
    fancy = record_api_graph(hub, _graph(upscale=True, face_detailer=True))

    def specials_of(topology_hash):
        return hub.fetchone(
            "SELECT specials FROM workflow_topology_core WHERE topology_hash = ?",
            (topology_hash,),
        )["specials"]

    # **The empty string, never NULL.** NULL is reserved for a row this pass
    # has not touched, and a graph with no post-processing has to be able to
    # say so - a name may only claim a workflow is plain on the second.
    assert specials_of(plain.topology_hash) == ""
    assert specials_of(fancy.topology_hash) == f"{UPSCALE},{FACE_DETAILER}"

    # And the card read gives the same two answers apart, as a tuple and never
    # as None.
    by_topology = {card.topology_hash: card.specials for card in card_index(hub)}
    assert by_topology[plain.topology_hash] == ()
    assert by_topology[fancy.topology_hash] == (UPSCALE, FACE_DETAILER)


def test_a_topology_cached_before_the_specials_column_is_re_derived(hub):
    """A row written by an older build reads "not known yet", then fills.

    NULLing the column is what such a row looks like to every query here: the
    ALTER adds it nullable, so every topology an existing hub already cached
    arrives this way. It must be re-queued **without** the row losing its stack
    key, its type or its slots in the meantime - that is the whole reason this
    re-derives on the column rather than on a bumped `CORE_VERSION`, which
    would blank the grid while the pass ran.
    """
    keys = record_api_graph(hub, _graph(face_detailer=True))
    with hub.transaction() as conn:
        conn.execute("UPDATE workflow_topology_core SET specials = NULL")
    before = hub.fetchone("SELECT * FROM workflow_topology_core")
    assert before["specials"] is None
    # Not known yet, and that is not the same answer as "has none".
    assert card_index(hub)[0].specials is None

    finder = WorkflowCardBackfillFinder(hub=hub)
    assert finder.progress() == (1, 1)
    # The card key is current, so only a specials-aware early return hands this
    # variant back at all. A stale one would return the key and leave the
    # column NULL forever, with the finder re-offering it on every sweep.
    assert finder.find_task()._run_task()["identified"] == 1

    after = hub.fetchone("SELECT * FROM workflow_topology_core")
    assert after["specials"] == FACE_DETAILER
    # Nothing else moved: the grid saw the same stack key, type and slots
    # throughout. The core hash is not merely EQUAL, it was never recomputed -
    # see the next test, which is what asserts that.
    assert (after["core_hash"], after["workflow_type"], after["slots"]) == (
        before["core_hash"],
        before["workflow_type"],
        before["slots"],
    )
    assert card_of(hub, keys.structural_hash) is not None
    assert finder.progress() == (1, 0)
    assert finder.find_task() is None


def test_filling_specials_alone_does_not_rerun_the_refinement(hub, monkeypatch):
    """The upgrade may not pay the hub's most expensive pass for two words.

    `core_hash` is a Weisfeiler-Leman refinement and a strip; `special_groups`
    is one reduction. A topology that already holds a current `core_hash` and
    only wants `specials` therefore gets an UPDATE, not a re-derivation - which
    on an existing library is the difference between one reduction per topology
    and a refinement per topology.

    Asserted by making `core_hash` fail: if the specials-only path calls it at
    all, this test raises rather than quietly costing more.
    """
    record_api_graph(hub, _graph(face_detailer=True))
    with hub.transaction() as conn:
        conn.execute("UPDATE workflow_topology_core SET specials = NULL")

    def explode(*args, **kwargs):
        raise AssertionError("core_hash was re-run to fill specials alone")

    monkeypatch.setattr(workflow_cards, "core_hash", explode)
    assert (
        WorkflowCardBackfillFinder(hub=hub).find_task()._run_task()["identified"] == 1
    )
    assert (
        hub.fetchone("SELECT specials FROM workflow_topology_core")["specials"]
        == FACE_DETAILER
    )


def test_a_core_row_restamped_under_another_rule_is_not_updated_by_this_branch(hub):
    """The specials-only UPDATE names the rule it read, so it cannot cross one.

    Two cases, and only the second needs arranging.

    A row already stamped under another rule when the pass starts never reaches
    the UPDATE at all: it does not join, so the variant takes the core-missing
    path and a fresh row is written under the current stamp.

    The one the ``core_version`` in the WHERE actually guards is the RACE - the
    row is re-stamped by a second process *between* the read that found
    ``specials`` NULL and this write. Driven here by re-stamping inside the
    pass's own transaction, from `_freeze_marks`, which runs on that connection
    immediately before the UPDATE. Without the guard the UPDATE lands, and this
    build's taxonomy is filed as the other rule's answer.
    """
    keys = record_api_graph(hub, _graph(face_detailer=True))
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_topology_core SET specials = NULL, core_version = 'v0'"
        )
    record_identity(hub, keys.structural_hash)
    row = hub.fetchone("SELECT core_version, specials FROM workflow_topology_core")
    assert (row["core_version"], row["specials"]) == (
        workflow_cards.CORE_RULE_VERSION,
        FACE_DETAILER,
    )

    # Now the race. `specials` is NULL under the CURRENT rule when the pass
    # reads, so it takes the UPDATE branch - and the stamp moves under it.
    with hub.transaction() as conn:
        conn.execute("UPDATE workflow_topology_core SET specials = NULL")
    real_freeze = workflow_cards._freeze_marks

    def restamp(conn, topology_hash, structural_hash, document_slots):
        conn.execute("UPDATE workflow_topology_core SET core_version = 'v9'")
        return real_freeze(conn, topology_hash, structural_hash, document_slots)

    workflow_cards._freeze_marks = restamp
    try:
        record_identity(hub, keys.structural_hash)
    finally:
        workflow_cards._freeze_marks = real_freeze
    raced = hub.fetchone("SELECT core_version, specials FROM workflow_topology_core")
    # Left alone: the row belongs to 'v9' now, and this pass has nothing true
    # to say about a rule it did not run.
    assert (raced["core_version"], raced["specials"]) == ("v9", None)


def test_flipping_the_lora_strip_is_a_new_core_rule(hub):
    """The owner gate's one-line flip must re-key the stacks, not mix two rules.

    ``core_hash`` does not carry the flag inside its digest, so the stamp has to
    name it; otherwise flipping the default changes every core hash while the
    stored ``core_version`` still reads current and nothing is re-derived.
    """
    assert workflow_cards.STRIP_LORAS_FOR_STACKS is True
    assert "stripped" in workflow_cards.CORE_RULE_VERSION

    record_api_graph(hub, _graph(loras=(CHARACTER_LORA,)))
    stamped = hub.fetchone("SELECT core_version FROM workflow_topology_core")
    assert stamped["core_version"] == workflow_cards.CORE_RULE_VERSION
    assert stamped["core_version"] != CORE_VERSION


def test_the_grouping_the_owner_gate_reads(hub, caplog):
    """Two checkpoints of one workflow stack; an unrelated workflow does not.

    Reported through the finder's own drain callback, which is the caller the
    owner gate actually reads - once per drain rather than once per batch.
    """
    record_api_graph(hub, _graph())
    record_api_graph(hub, _graph(ckpt="dreamshaper.safetensors"))
    record_api_graph(hub, _graph(preview=True))
    record_api_graph(hub, _graph(img2img=True))

    grouping = workflow_cards.card_grouping(hub)
    assert grouping["variants"] == 4
    assert grouping["cards"] == 4
    # The three txt2img cards differ only in a checkpoint or in plumbing, so
    # they are one automatic stack; the img2img graph is a one-off.
    assert (grouping["stacks"], grouping["stacked_cards"], grouping["one_offs"]) == (
        1,
        3,
        1,
    )

    with caplog.at_level(logging.INFO, logger="pixlstash.tasks"):
        WorkflowCardBackfillFinder(hub=hub).on_all_tasks_complete()
    assert "4 variants over 3 topologies make 4 cards" in caplog.text
    assert "1 automatic stacks holding 3 of them, 1 one-offs" in caplog.text


def test_an_unkeyable_document_is_deferred_rather_than_retried(hub):
    """A stored document does not change by itself, so a retry loop is forever."""
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_topology "
            "(topology_hash, hash_version, node_count, first_seen_at) "
            "VALUES ('t', 'v1', 1, 'now')"
        )
        conn.execute(
            "INSERT INTO workflow_recipe (structural_hash, topology_hash, "
            "hash_version, node_count, first_seen_at) VALUES ('s', 't', 'v1', 1, 'now')"
        )
        conn.execute(
            "INSERT INTO workflow_recipe_graph (structural_hash, document_sha256, "
            "document, created_at) VALUES ('s', 'd', ?, 'now')",
            # A raw graph: it names its model by value, which is the one thing a
            # stored document never does.
            (
                json.dumps(
                    {
                        "1": {
                            "class_type": "CheckpointLoaderSimple",
                            "inputs": {"ckpt_name": "base.safetensors"},
                        }
                    }
                ),
            ),
        )
    finder = WorkflowCardBackfillFinder(hub=hub)

    task = finder.find_task()
    result = task._run_task()
    task.result = result
    finder.on_task_complete(task, None)

    assert result["deferred"] == ["s"]
    assert finder.find_task() is None
    # Still outstanding, though: a refusal is not a completion.
    assert finder.progress() == (1, 1)


def test_only_an_error_that_will_not_pass_retires_a_batch(hub):
    """Cancelled and locked stay eligible; anything else is deferred.

    The hub is shared with a second process under a busy timeout, and deriving
    a card is cheap to retry, so a momentary lock must not retire fifty
    variants until the next restart - which is what this finder's model, the
    checkpoint hasher, does, because ITS retry is 24 GB of reading.
    """
    keys = record_api_graph(hub, _graph())
    with hub.transaction() as conn:
        conn.execute("DELETE FROM workflow_variant")
    finder = WorkflowCardBackfillFinder(hub=hub)

    task = finder.find_task()
    assert task.params["structural_hashes"] == [keys.structural_hash]
    finder.on_task_complete(task, TaskCancelledError("stopping"))
    assert finder.find_task() is not None

    finder.on_task_complete(task, sqlite3.OperationalError("database is locked"))
    assert finder.find_task() is not None

    finder.on_task_complete(task, RuntimeError("the worker died"))
    assert finder.find_task() is None


def test_a_legacy_ui_file_row_is_no_card(hub):
    """A UI-format file names its widgets by position, so it has no models."""
    ui = {
        "nodes": [
            {
                "id": 1,
                "type": "CheckpointLoaderSimple",
                "inputs": [],
                "outputs": [{"name": "MODEL", "links": [1]}],
                "widgets_values": ["base.safetensors"],
            },
            {
                "id": 2,
                "type": "SaveImage",
                "inputs": [{"name": "images", "link": 1}],
                "outputs": [],
                "widgets_values": ["out"],
            },
        ],
        "links": [[1, 1, 0, 2, 0, "*"]],
    }
    topology = record_ui_graph(hub, ui)
    key = _legacy_file(hub, "ui.json", topology)

    row = hub.fetchone("SELECT * FROM workflow_file WHERE workflow_name = 'ui.json'")
    assert (row["topology_hash"], row["structural_hash"]) == (topology, None)
    assert row["workflow_key"] == key == workflow_cards.topology_only_key(topology)

    # A variant-less file row is no card: files are not workflows. What the
    # owner imports is a manual workflow of its own (`workflow_document`).
    assert card_index(hub) == []


def test_a_file_never_forks_a_card_a_variant_already_holds(hub):
    """The dedup guard: one key, one Card, and it keeps its variants.

    A graph that names NO model keys on ``topology_only_key`` - that is what
    that key means - so a topology can have a variant on the very key the file
    pass derives. Yielding it twice would put two Cards under one key on the
    grid, and the second, built from the file alone, carries no variants: the
    card would lose its pictures, its defaults and its stack depending on
    which of the two a reader reached first.
    """
    modelless = {
        "1": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 512, "height": 512, "batch_size": 1},
        },
        "2": {
            "class_type": "SaveImage",
            "inputs": {"images": ["1", 0], "filename_prefix": "x"},
        },
    }
    keys = record_api_graph(hub, modelless)
    assert card_of(hub, keys.structural_hash) == workflow_cards.topology_only_key(
        keys.topology_hash
    )
    # A file of the same topology whose own variant is missing - an
    # editor-format export of that graph, which files no recipe.
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_file "
            "(workflow_name, topology_hash, structural_hash, workflow_key) "
            "VALUES (?, ?, NULL, ?)",
            ("modelless.json", keys.topology_hash, card_of(hub, keys.structural_hash)),
        )

    cards = card_index(hub)
    assert len(cards) == 1
    assert cards[0].variants == [keys.structural_hash]
    assert cards[0].file_name == "modelless.json"


def test_a_workflow_file_past_the_cap_is_refused_by_the_loader(tmp_path, monkeypatch):
    """The size cap lives on the loader, so every caller gets it (#1483).

    It guarded one of ten `_load_workflow_json` call sites when it was added -
    the grid's per-card read - while the sibling that reads the same watched
    folder on every workflow-list and menu open had none. Asserted on the
    loader rather than on a route so it holds for all ten.

    The cap is monkeypatched rather than written to: a real 32 MB file would
    be a 32 MB write on every run of this suite for one branch.
    """
    from pixlstash.routes import comfyui as comfyui_routes

    path = tmp_path / "big.json"
    path.write_text(json.dumps({"nodes": []}), encoding="utf-8")

    # Reads fine as it is.
    assert comfyui_routes._load_workflow_json(str(path)) == {"nodes": []}

    monkeypatch.setattr(comfyui_routes, "MAX_WORKFLOW_FILE_BYTES", 4)
    with pytest.raises(comfyui_routes.WorkflowFileTooLarge):
        comfyui_routes._load_workflow_json(str(path))


def test_a_card_whose_only_file_a_pull_wrote_is_not_hand_imported(hub):
    """#1440: ``hand_imported`` off a legacy file row on an automatic card.

    A file a pull wrote is not hand-imported; a second, hand-dropped file on
    the same card makes it so.
    """
    keys = record_api_graph(hub, _graph())
    workflow_cards.record_identity(hub, keys.structural_hash)
    api_key = _legacy_file(hub, "api.json", keys.topology_hash, keys.structural_hash)

    def pulled(name):
        with hub.transaction() as conn:
            conn.execute(
                "INSERT INTO workflow_pulled_file (workflow_name) VALUES (?)", (name,)
            )

    def hand():
        return {card.workflow_key: card.hand_imported for card in card_index(hub)}

    assert hand() == {api_key: True}
    pulled("api.json")
    assert hand()[api_key] is False
    _legacy_file(hub, "api copy.json", keys.topology_hash, keys.structural_hash)
    assert hand()[api_key] is True


def _base_slot_label(hub, topology_hash, widget="ckpt_name"):
    slots = json.loads(
        hub.fetchone(
            "SELECT slots FROM workflow_topology_core WHERE topology_hash = ?",
            (topology_hash,),
        )["slots"]
    )
    return next(slot["label"] for slot in slots if slot["widget"] == widget)


def test_a_replaced_model_keeps_the_card_and_its_pictures(hub):
    """A missing checkpoint replaced: runs with the new one land on the old card.

    The pictures already made with the replacement join it when the fix is set,
    one filed afterwards lands there on its own, and undoing the fix sends both
    back to a card of their own - the original's pictures never move.
    """
    old = record_api_graph(hub, _graph(ckpt="test-model-fp8.safetensors"))
    early = record_api_graph(hub, _graph(ckpt="test-model-bf16.safetensors"))
    card = card_of(hub, old.structural_hash)
    apart = card_of(hub, early.structural_hash)
    assert apart != card
    label = _base_slot_label(hub, old.topology_hash)

    moved = set_model_fix(
        hub,
        old.topology_hash,
        [label],
        "sdxl/test-model-FP8.safetensors",
        "test-model-bf16.safetensors",
        {},
    )

    assert card_of(hub, old.structural_hash) == card
    assert card_of(hub, early.structural_hash) == card
    assert moved == {apart: [card]}
    assert model_fixes(hub, old.topology_hash) == [
        (
            label,
            "sdxl/test-model-FP8.safetensors",
            "test-model-bf16.safetensors",
            "checkpoint",
        )
    ]
    later = record_api_graph(
        hub, _graph(ckpt="test-model-bf16.safetensors", preview=False, extra=None)
    )
    assert later.structural_hash == early.structural_hash
    # A second run of the replacement with a different graph value spelling
    # still files on the card: the key reads the file, not its folder.
    folder = record_api_graph(hub, _graph(ckpt="sdxl/test-model-bf16.safetensors"))
    assert card_of(hub, folder.structural_hash) == card

    set_model_fix(
        hub,
        old.topology_hash,
        [label],
        "sdxl/test-model-FP8.safetensors",
        None,
        {},
    )

    assert card_of(hub, old.structural_hash) == card
    assert card_of(hub, early.structural_hash) == apart
    assert model_fixes(hub, old.topology_hash) == []


def test_a_fix_targets_checkpoint_slots_across_the_whole_topology(hub):
    """Every card of the graph, and never a VAE slot naming the same file.

    Only the sibling card loads the missing file, so its slot is found from
    the topology rather than from the card being fixed. It also has a VAE
    slot holding a file of that name, which is no place for a checkpoint.
    """
    missing = "test-model-fp8.safetensors"

    def with_vae(ckpt, vae):
        return _graph(
            ckpt=ckpt,
            extra={
                "8": _node("VAELoader", vae_name=vae),
                "6": _node("VAEDecode", samples=["5", 0], vae=["8", 0]),
            },
        )

    mine = record_api_graph(
        hub, with_vae("test-other.safetensors", "test-vae-a.safetensors")
    )
    sibling = record_api_graph(hub, with_vae(missing, missing))
    assert card_of(hub, mine.structural_hash) != card_of(hub, sibling.structural_hash)

    assert model_fix_labels(hub, mine.topology_hash, missing, "checkpoint") == [
        _base_slot_label(hub, mine.topology_hash)
    ]
    assert model_fix_labels(hub, mine.topology_hash, missing, "vae") == [
        _base_slot_label(hub, mine.topology_hash, "vae_name")
    ]


def test_a_vae_naming_the_replaced_file_does_not_flag_its_pictures(hub):
    """A fix is a checkpoint's: a VAE of that name is still what loads there."""
    replaced = "test-model-fp8.safetensors"
    fixed = record_api_graph(
        hub,
        _graph(
            ckpt=replaced,
            extra={
                "8": _node("VAELoader", vae_name="test-vae-a.safetensors"),
                "6": _node("VAEDecode", samples=["5", 0], vae=["8", 0]),
            },
        ),
    )
    vae_only = record_api_graph(
        hub,
        _graph(
            ckpt="test-other.safetensors",
            extra={
                "8": _node("VAELoader", vae_name=replaced),
                "6": _node("VAEDecode", samples=["5", 0], vae=["8", 0]),
            },
        ),
    )
    set_model_fix(
        hub,
        fixed.topology_hash,
        model_fix_labels(hub, fixed.topology_hash, replaced, "checkpoint"),
        replaced,
        "test-model-bf16.safetensors",
        {},
    )
    cards = [
        Card(
            workflow_key=card_of(hub, keys.structural_hash),
            topology_hash=keys.topology_hash,
            variants=[keys.structural_hash],
        )
        for keys in (fixed, vae_only)
    ]

    assert _superseded_variants(hub, cards) == {fixed.structural_hash}


def _with_vae(ckpt, vae):
    return _graph(
        ckpt=ckpt,
        extra={
            "8": _node("VAELoader", vae_name=vae),
            "6": _node("VAEDecode", samples=["5", 0], vae=["8", 0]),
        },
    )


def test_a_replaced_vae_keeps_the_card_and_leaves_a_checkpoint_of_that_name(hub):
    """#1596: a missing VAE fixed as #1587 fixes a checkpoint, in its own slot.

    The sibling card loads the missing file as its CHECKPOINT: a VAE fix must
    neither name that slot nor flag that card's pictures.
    """
    missing = "test-vae-fp8.safetensors"
    old = record_api_graph(hub, _with_vae("test-base.safetensors", missing))
    new = record_api_graph(
        hub, _with_vae("test-base.safetensors", "test-vae-bf16.safetensors")
    )
    as_checkpoint = record_api_graph(hub, _with_vae(missing, "test-vae-a.safetensors"))
    card = card_of(hub, old.structural_hash)
    other = card_of(hub, as_checkpoint.structural_hash)
    assert card_of(hub, new.structural_hash) != card
    vae_label = _base_slot_label(hub, old.topology_hash, "vae_name")

    labels = model_fix_labels(hub, old.topology_hash, missing, "vae")
    assert labels == [vae_label]
    set_model_fix(
        hub,
        old.topology_hash,
        labels,
        missing,
        "test-vae-bf16.safetensors",
        {},
        kind="vae",
    )

    assert card_of(hub, new.structural_hash) == card
    assert card_of(hub, as_checkpoint.structural_hash) == other
    assert model_fixes(hub, old.topology_hash) == [
        (vae_label, missing, "test-vae-bf16.safetensors", "vae")
    ]
    cards = [
        Card(
            workflow_key=card_of(hub, keys.structural_hash),
            topology_hash=keys.topology_hash,
            variants=[keys.structural_hash],
        )
        for keys in (old, new, as_checkpoint)
    ]
    assert _superseded_variants(hub, cards) == {old.structural_hash}


def test_a_swapped_in_pixlstash_loader_keeps_the_card(hub):
    """#1605: a fix run through a PixlStash loader files on the original card.

    The swapped node changes the topology, and with it every slot label: the
    speed LoRA's structural slot included, which is in the key. The recorded
    swap reads the graph back as the original, so the key comes out the same;
    undoing the fix sends it to the replacement's card, as a renamed file's
    pictures go, and a PixlStash loader holding any other file stays apart.
    """
    missing, now = "test-vae-fp8.safetensors", "test-vae-bf16.safetensors"
    digest = "ab" * 32

    def with_vae(vae_node):
        return _graph(
            loras=(SPEED_LORA,),
            extra={
                "8": vae_node,
                "6": _node("VAEDecode", samples=["5", 0], vae=["8", 0]),
            },
        )

    original = with_vae(_node("VAELoader", vae_name=missing))
    old = record_api_graph(hub, original)
    plain = record_api_graph(hub, with_vae(_node("VAELoader", vae_name=now)))
    card = card_of(hub, old.structural_hash)
    set_model_fix(
        hub,
        old.topology_hash,
        model_fix_labels(hub, old.topology_hash, missing, "vae"),
        missing,
        now,
        {},
        kind="vae",
    )
    assert card_of(hub, plain.structural_hash) == card

    swapped = with_vae(_node("PixlStashVAELoader", vae_sha256=digest))
    swapped_topology, swaps = loader_swaps(
        original, swapped, {"8": {"vae_sha256": ("vae_name", now)}}
    )
    assert swapped_topology != old.topology_hash
    record_loader_swaps(hub, swapped_topology, swaps)
    ran = record_api_graph(hub, swapped)
    # Carded under the original topology, whose cache it then reads, so the
    # backfill counts it done. Asked before anything else of the swapped
    # topology is filed, whose cache would hide a lookup there.
    assert ran.structural_hash not in workflow_cards.unidentified_variants(hub, 100)
    by_hand = record_api_graph(
        hub, with_vae(_node("PixlStashVAELoader", vae_sha256="cd" * 32))
    )

    assert card_of(hub, ran.structural_hash) == card
    assert card_of(hub, by_hand.structural_hash) != card
    # Carded under the topology it was swapped from, which is what a re-key of
    # that topology selects its variants by.
    assert (
        hub.fetchone(
            "SELECT topology_hash FROM workflow_variant WHERE structural_hash = ?",
            (ran.structural_hash,),
        )["topology_hash"]
        == old.topology_hash
    )

    set_model_fix(
        hub,
        old.topology_hash,
        model_fix_labels(hub, old.topology_hash, missing, "vae"),
        missing,
        None,
        {},
        kind="vae",
    )
    assert card_of(hub, old.structural_hash) == card
    assert card_of(hub, ran.structural_hash) == card_of(hub, plain.structural_hash)
    assert card_of(hub, ran.structural_hash) != card


def test_an_unswap_matches_exactly_the_files_the_swap_recorded():
    """#1605: a second encoder, or a second original, keys the graph as itself."""
    x, z = "11" * 32, "22" * 32

    def with_clip(node):
        return _graph(extra={"8": node, "9": _node("CLIPTextEncode", clip=["8", 0])})

    original = with_clip(
        _node("CLIPLoader", clip_name="test-t5-fp8.safetensors", type="flux")
    )
    one = with_clip(_node("PixlStashCLIPLoader", clip_sha256=x, type="flux"))
    topology, swaps = loader_swaps(
        original, one, {"8": {"clip_sha256": ("clip_name", "test-t5.safetensors")}}
    )
    swapped_from, restored = unswapped(structural_document(one), swaps)
    assert swapped_from == topology_hash(original)
    assert restored["8"]["class_type"] == "CLIPLoader"

    two = with_clip(
        _node("PixlStashCLIPLoader", clip_sha256=x, clip_sha256_2=z, type="flux")
    )
    assert unswapped(structural_document(two), swaps)[0] is None
    # The same swap recorded from another loader class: which one it was is a
    # guess.
    other = replace(swaps[0], topology_hash="0" * 64, class_type="CLIPLoaderGGUF")
    assert unswapped(structural_document(one), [*swaps, other])[0] is None


def test_an_unswap_puts_back_all_of_a_runs_swapped_loaders_or_none():
    """Two loaders swapped in one run: a graph matching only one keys as itself."""
    x, y = "11" * 32, "22" * 32

    def graph(vae, clip):
        return _graph(
            extra={
                "7": clip,
                "9": _node("CLIPTextEncode", clip=["7", 0]),
                "8": vae,
                "6": _node("VAEDecode", samples=["5", 0], vae=["8", 0]),
            }
        )

    original = graph(
        _node("VAELoader", vae_name="test-vae-fp8.safetensors"),
        _node("CLIPLoader", clip_name="test-t5-fp8.safetensors", type="flux"),
    )
    both = graph(
        _node("PixlStashVAELoader", vae_sha256=x),
        _node("PixlStashCLIPLoader", clip_sha256=y, type="flux"),
    )
    _topology, swaps = loader_swaps(
        original,
        both,
        {
            "8": {"vae_sha256": ("vae_name", "test-vae.safetensors")},
            "7": {"clip_sha256": ("clip_name", "test-t5.safetensors")},
        },
    )
    assert unswapped(structural_document(both), swaps)[0] == topology_hash(original)
    one = graph(
        _node("PixlStashVAELoader", vae_sha256=x),
        _node("PixlStashCLIPLoader", clip_sha256="33" * 32, type="flux"),
    )
    assert unswapped(structural_document(one), swaps) == (
        None,
        structural_document(one),
    )


def test_a_text_encoder_fix_names_encoder_slots_and_never_a_vision_one(hub):
    """``clip_name`` on a CLIP vision loader is an image encoder, not a text one."""
    missing = "test-t5-fp8.safetensors"
    keys = record_api_graph(
        hub,
        _graph(
            extra={
                "8": _node(
                    "DualCLIPLoader",
                    clip_name1="test-clip-l.safetensors",
                    clip_name2=missing,
                ),
                "9": _node("CLIPVisionLoader", clip_name=missing),
            }
        ),
    )
    label = {
        (slot["class_type"], slot["widget"]): slot["label"]
        for slot in json.loads(
            hub.fetchone(
                "SELECT slots FROM workflow_topology_core WHERE topology_hash = ?",
                (keys.topology_hash,),
            )["slots"]
        )
    }

    assert model_fix_labels(hub, keys.topology_hash, missing, "text_encoder") == [
        label[("DualCLIPLoader", "clip_name2")]
    ]
    assert model_fix_labels(hub, keys.topology_hash, missing, "checkpoint") == []


def test_a_vision_encoder_of_a_replaced_text_encoders_name_is_not_flagged(hub):
    """The asset rows say `clip_name`, not which loader: the document decides."""
    missing = "test-t5-fp8.safetensors"

    def encoders(text, vision):
        return _graph(
            extra={
                "8": _node(
                    "DualCLIPLoader",
                    clip_name1="test-clip-l.safetensors",
                    clip_name2=text,
                ),
                "9": _node("CLIPVisionLoader", clip_name=vision),
            }
        )

    as_text = record_api_graph(hub, encoders(missing, "test-vision.safetensors"))
    as_vision = record_api_graph(hub, encoders("test-t5-bf16.safetensors", missing))
    assert as_text.topology_hash == as_vision.topology_hash
    set_model_fix(
        hub,
        as_text.topology_hash,
        model_fix_labels(hub, as_text.topology_hash, missing, "text_encoder"),
        missing,
        "test-t5-bf16.safetensors",
        {},
        kind="text_encoder",
    )
    cards = [
        Card(
            workflow_key=card_of(hub, keys.structural_hash),
            topology_hash=keys.topology_hash,
            variants=[keys.structural_hash],
        )
        for keys in (as_text, as_vision)
    ]

    assert _superseded_variants(hub, cards) == {as_text.structural_hash}


def test_a_hub_made_before_the_slot_kind_reads_its_fixes_as_checkpoints(tmp_path):
    """A development hub that ran #1587: its fixes were all checkpoints."""
    path = str(tmp_path / "older.db")
    database = HubDatabase(path)
    with database.transaction() as conn:
        conn.execute("ALTER TABLE workflow_model_fix DROP COLUMN slot_kind")
        conn.execute(
            "INSERT INTO workflow_model_fix (topology_hash, slot_label, was_norm, "
            "now_norm, was_name, now_name) VALUES ('t', 'l', 'a', 'b', 'a', 'b')"
        )
    database.close()

    reopened = HubDatabase(path)
    try:
        assert model_fixes(reopened, "t") == [("l", "a", "b", "checkpoint")]
    finally:
        reopened.close()


def test_the_fixed_card_keeps_its_own_name_whoever_has_more_pictures(hub):
    """The owner is fixing THIS card, not folding it into the replacement's."""
    old = record_api_graph(hub, _graph(ckpt="test-model-fp8.safetensors"))
    new = record_api_graph(hub, _graph(ckpt="test-model-bf16.safetensors"))
    card = card_of(hub, old.structural_hash)
    other = card_of(hub, new.structural_hash)
    # The card tables have no route writer since #1623; a model fix still
    # carries them across a re-key, which is what is asserted.
    with hub.transaction() as conn:
        conn.executemany(
            "INSERT INTO workflow_attr (workflow_key, name) VALUES (?, ?)",
            [(card, "The one being fixed"), (other, "The busier card")],
        )

    set_model_fix(
        hub,
        old.topology_hash,
        [_base_slot_label(hub, old.topology_hash)],
        "test-model-fp8.safetensors",
        "test-model-bf16.safetensors",
        {old.structural_hash: 1, new.structural_hash: 30},
        keep_key=card,
    )

    assert card_of(hub, new.structural_hash) == card
    assert (
        hub.fetchone("SELECT name FROM workflow_attr WHERE workflow_key = ?", (card,))[
            "name"
        ]
        == "The one being fixed"
    )


def test_forgetting_a_replaced_model_forgets_the_replacement(hub):
    keys = record_api_graph(hub, _graph(ckpt="test-private-name.safetensors"))
    set_model_fix(
        hub,
        keys.topology_hash,
        [_base_slot_label(hub, keys.topology_hash)],
        "test-private-name.safetensors",
        "test-model-bf16.safetensors",
        {},
    )

    forget_asset_names(hub, "test-private-name.safetensors")

    assert "test-private-name" not in json.dumps(all_card_rows(hub))


def test_forgetting_model_ghosts_forgets_their_replacements(hub):
    """Settings › Privacy's purge: a replaced model is a ghost by definition."""
    keys = record_api_graph(hub, _graph(ckpt="test-private-name.safetensors"))
    set_model_fix(
        hub,
        keys.topology_hash,
        [_base_slot_label(hub, keys.topology_hash)],
        "test-private-name.safetensors",
        "test-model-bf16.safetensors",
        {},
    )

    assert forget_model_ghosts(hub)

    assert "test-private-name" not in json.dumps(all_card_rows(hub))


def test_covers_made_with_the_replaced_model_are_flagged_and_go_last(hub):
    old = record_api_graph(hub, _graph(ckpt="test-model-fp8.safetensors"))
    new = record_api_graph(hub, _graph(ckpt="test-model-bf16.safetensors"))
    set_model_fix(
        hub,
        old.topology_hash,
        [_base_slot_label(hub, old.topology_hash)],
        "test-model-fp8.safetensors",
        "test-model-bf16.safetensors",
        {},
    )
    key = card_of(hub, old.structural_hash)
    card = Card(
        workflow_key=key,
        topology_hash=old.topology_hash,
        variants=[old.structural_hash, new.structural_hash],
    )
    superseded = _superseded_variants(hub, [card])
    assert superseded == {old.structural_hash}

    candidates = [
        # The old model's picture is rated higher, and still goes last.
        CoverCandidate(old.structural_hash, 1, 5, 0.9, None),
        CoverCandidate(new.structural_hash, 2, 2, 0.1, None),
    ]
    workflow = Workflow(
        "auto:" + "c" * 64,
        topologies=[old.topology_hash],
        variants=list(card.variants),
        cards=[key],
        base_card=key,
    )
    (figure,) = _figures([workflow], [card], {}, candidates, {}, superseded)

    assert [(c.picture_id, c.superseded) for c in figure.covers] == [
        (2, False),
        (1, True),
    ]


# ── workflows and their default recipe (#1622) ─────────────────────────────


def _file_run(hub, *, strength=None, steps=20, **graph_options):
    """File one run of ``_graph(...)`` into a library; return its keys."""
    graph = _graph(**graph_options)
    graph["5"]["inputs"]["steps"] = steps
    if strength is not None:
        graph["L0"]["inputs"]["strength_model"] = strength
    return record_api_graph(hub, graph, library_uuid="test-library")


def _defaults(hub, monkeypatch, runs):
    """``workflow_defaults`` with the vault's two reads answered by *runs*."""
    monkeypatch.setattr(
        workflow_card_service,
        "read_instance_hashes",
        lambda vault, variants, score, limit: sorted(
            {keys.instance_hash for keys in runs if keys.structural_hash in variants}
        ),
    )
    monkeypatch.setattr(
        workflow_card_service,
        "read_variant_picture_counts",
        lambda vault: {keys.structural_hash: 1 for keys in runs},
    )
    workflow_id = workflow_of_topology(hub, runs[0].topology_hash)
    vault = SimpleNamespace(library_uuid="test-library")
    return workflow_id, workflow_card_service.workflow_defaults(hub, vault, workflow_id)


def _four_runs(hub):
    return [
        _file_run(hub, ckpt="a.safetensors", loras=("x.safetensors",), strength=0.8),
        _file_run(
            hub,
            ckpt="a.safetensors",
            loras=("x.safetensors",),
            strength=0.8,
            face_detailer=True,
        ),
        _file_run(
            hub,
            ckpt="b.safetensors",
            loras=("x.safetensors", "y.safetensors"),
            strength=0.6,
            steps=30,
        ),
        _file_run(hub, ckpt="a.safetensors"),
    ]


def test_one_workflow_spans_the_topologies_its_core_hash_groups(hub):
    runs = _four_runs(hub)
    ids = {workflow_of_topology(hub, keys.topology_hash) for keys in runs}
    assert len(ids) == 1
    (workflow_id,) = ids
    assert workflow_id.startswith("auto:")
    assert set(topologies_in_workflow(hub, workflow_id)) == {
        keys.topology_hash for keys in runs
    }
    assert set(variants_in_workflow(hub, workflow_id)) == {
        keys.structural_hash for keys in runs
    }
    (entry,) = workflow_index(hub)
    # A variant is on one card (it keys workflow_variant), so none repeats.
    assert sorted(entry.variants) == sorted({keys.structural_hash for keys in runs})
    # The base graph has the most stage groups: the detailer run's.
    assert entry.base_topology == runs[1].topology_hash
    assert entry.base_card == card_of(hub, runs[1].structural_hash)


def test_a_topology_placed_by_hand_leaves_its_automatic_workflow(hub):
    runs = _four_runs(hub)
    workflow_id = workflow_of_topology(hub, runs[0].topology_hash)
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_group (workflow_id, kind) VALUES ('0' || ?, 'manual')",
            ("1" * 31,),
        )
        conn.execute(
            "INSERT INTO workflow_group_member (topology_hash, workflow_id) "
            "VALUES (?, ?)",
            (runs[3].topology_hash, "0" + "1" * 31),
        )
    assert workflow_of_topology(hub, runs[3].topology_hash) == "0" + "1" * 31
    assert runs[3].topology_hash not in topologies_in_workflow(hub, workflow_id)
    assert topologies_in_workflow(hub, "0" + "1" * 31) == [runs[3].topology_hash]


def test_the_default_recipe_is_the_modal_checkpoint_and_the_majority_loras(
    hub, monkeypatch
):
    runs = _four_runs(hub)
    workflow_id, recipe = _defaults(hub, monkeypatch, runs)

    assert recipe.workflow_id == workflow_id
    # a.safetensors in three runs of four, b in one.
    (checkpoint,) = [m for m in recipe.models if m.address.endswith("/ckpt_name")]
    assert checkpoint.address.startswith("core:")
    assert (checkpoint.filename, checkpoint.kind) == ("a.safetensors", "checkpoint")
    # x in three runs of four (more than half) at its modal strength; y in one.
    assert [(lora.filename, lora.strength) for lora in recipe.loras] == [
        ("x.safetensors", 0.8)
    ]
    # Three runs at 20 steps, one at 30.
    steps = {d.input_name: d.value for d in recipe.values}
    assert steps["steps"] == 20
    assert [d.input_name for d in recipe.values] == ["steps", "width", "height"]
    assert recipe.sampled == 4
    # The base graph has a face detailer, and three runs of four went without.
    assert recipe.stages == {"face_detailer": False}


def test_exactly_half_is_not_a_majority(hub, monkeypatch):
    runs = [
        _file_run(hub, loras=("x.safetensors",)),
        _file_run(hub),
    ]
    _, recipe = _defaults(hub, monkeypatch, runs)
    assert recipe.loras == []


def test_the_owner_s_edits_replace_what_they_name(hub, monkeypatch):
    runs = _four_runs(hub)
    workflow_id = workflow_of_topology(hub, runs[0].topology_hash)
    _, computed = _defaults(hub, monkeypatch, runs)
    steps = next(d for d in computed.values if d.input_name == "steps")
    checkpoint = next(m for m in computed.models if m.address.endswith("/ckpt_name"))
    with hub.transaction() as conn:
        conn.executemany(
            "INSERT INTO workflow_group_default (workflow_id, address, value) "
            "VALUES (?, ?, ?)",
            [
                (workflow_id, f"{steps.slot_label}/steps", "25"),
                (workflow_id, checkpoint.address, "c.safetensors"),
                (workflow_id, "lora:" + "d" * 64, "0.5"),
            ],
        )
    _, recipe = _defaults(hub, monkeypatch, runs)
    edited = next(d for d in recipe.values if d.input_name == "steps")
    assert (edited.value, edited.provenance) == (25, "edited")
    model = next(m for m in recipe.models if m.address == checkpoint.address)
    assert (model.filename, model.provenance) == ("c.safetensors", "edited")
    assert ("d" * 64, 0.5) in [(lora.sha256, lora.strength) for lora in recipe.loras]


def test_the_stage_vote_counts_only_topologies_whose_stages_are_known(hub, monkeypatch):
    """An unread topology neither votes for a stage nor pads the electorate."""
    runs = [
        _file_run(hub, face_detailer=True),
        _file_run(hub, steps=21),
        _file_run(hub, steps=22),
        *(_file_run(hub, preview=True, steps=s) for s in (23, 24, 25)),
    ]
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_topology_core SET specials = NULL WHERE topology_hash = ?",
            (runs[3].topology_hash,),
        )
    _, recipe = _defaults(hub, monkeypatch, runs)
    # Two of the three runs whose stages are known went without the detailer.
    assert recipe.stages == {"face_detailer": False}


def test_a_saved_recipe_with_malformed_models_inherits_the_default():
    recipe = SimpleNamespace(
        id=7,
        workflow_key="k",
        workflow_id=None,
        prompt="",
        negative=None,
        loras="[]",
        overrides="{}",
        seed=None,
        keep_seed=False,
    )
    valid = [{"address": "core:a/ckpt_name", "filename": "x.safetensors"}]
    for stored, expected in (
        (json.dumps(valid), valid),
        ("[1, 2]", None),
        ('["x"]', None),
        ('[{"address": "core:a/ckpt_name"}]', None),
        ('[{"address": 5, "filename": "x.safetensors"}]', None),
        ('[{"address": "core:a/ckpt_name", "filename": 123}]', None),
        (
            '[{"address": "core:a/ckpt_name", "filename": "x", "sha256": "y"}]',
            None,
        ),
        (None, None),
    ):
        body = saved_recipe_body(SimpleNamespace(**vars(recipe), models=stored))
        assert body["models"] == expected, stored


def test_a_manual_workflow_is_its_own_record_and_never_an_automatic_ones(hub):
    """The same graph an automatic workflow runs, kept by hand, stays apart.

    Its card sits on no topology (its id is its topology), so no core hash
    can fold it into the automatic workflow and the automatic one can never
    absorb it; a row that will not read is a workflow with no graph, not a
    500.
    """
    runs = _four_runs(hub)
    auto = workflow_of_topology(hub, runs[0].topology_hash)
    manual = create_manual_workflow(hub, "Mine", _graph(ckpt="a.safetensors"), "import")
    assert re.fullmatch(r"manual:[0-9a-f]{32}", manual)

    by_id = {entry.workflow_id: entry for entry in workflow_index(hub)}
    assert set(by_id) == {auto, manual}
    mine = by_id[manual]
    assert (mine.topologies, mine.variants, mine.cards) == ([], [], [manual])
    assert (mine.base_card, mine.base_topology, mine.name) == (manual, None, "Mine")
    assert manual not in by_id[auto].cards
    assert set(by_id[auto].topologies) == {keys.topology_hash for keys in runs}
    assert topologies_in_workflow(hub, manual) == []
    (card,) = [card for card in card_index(hub) if card.manual]
    assert (card.workflow_key, card.topology_hash) == (manual, manual)
    assert (card.imported, card.hand_imported, card.variants) == (True, True, [])
    assert manual_document(hub, manual) == _graph(ckpt="a.safetensors")

    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_document SET document = '{' WHERE workflow_id = ?",
            (manual,),
        )
    assert manual_document(hub, manual) is None

    # Valid JSON that is not an object: skipped or ignored, never a crash.
    editor = {"nodes": [], "links": []}
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_document SET document = ?, api_document = '[1]' "
            "WHERE workflow_id = ?",
            (json.dumps(editor), manual),
        )
    assert manual_document(hub, manual) == editor
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_document SET document = '[1]' WHERE workflow_id = ?",
            (manual,),
        )
    assert manual_documents_holding(hub, "{}") == []


def test_a_pulled_manual_workflow_is_not_hand_imported(hub):
    """The one-off rule keeps its #1440 reading: a pull is not a statement."""
    pulled = create_manual_workflow(hub, "p", _graph(), "pull")
    dropped = create_manual_workflow(hub, "d", _graph(), "inbox")
    hand = {c.workflow_key: c.hand_imported for c in card_index(hub) if c.manual}
    assert hand == {pulled: False, dropped: True}


def test_a_manual_default_recipe_is_its_own_graph_by_its_own_slot_labels(
    hub, monkeypatch
):
    """Never a `core:` address: nothing keyed on the core can reach it."""
    _four_runs(hub)
    graph = _graph(ckpt="a.safetensors", loras=("x.safetensors",))
    graph["5"]["inputs"]["steps"] = 33
    manual = create_manual_workflow(hub, "Mine", graph, "import")
    monkeypatch.setattr(
        workflow_card_service, "read_variant_picture_counts", lambda vault: {}
    )
    recipe = workflow_card_service.workflow_defaults(
        hub, SimpleNamespace(library_uuid="test-library"), manual
    )

    assert (recipe.base_card, recipe.base_topology, recipe.sampled) == (
        manual,
        None,
        1,
    )
    assert recipe.values and not any(
        d.slot_label.startswith("core:") for d in recipe.values
    )
    assert {d.input_name: d.value for d in recipe.values}["steps"] == 33
    assert recipe.models == []
    assert [(lora.filename, lora.strength) for lora in recipe.loras] == [
        ("x.safetensors", 1.0)
    ]


def test_a_lora_split_with_no_majority_decides_nothing(hub, monkeypatch):
    """50/50 between two LoRAs is no consensus, not "run without LoRAs"."""
    split = [
        _file_run(hub, loras=("x.safetensors",)),
        _file_run(hub, loras=("y.safetensors",)),
    ]
    _, recipe = _defaults(hub, monkeypatch, split)
    assert (recipe.loras, recipe.loras_decided) == ([], False)


def test_most_runs_without_a_lora_decide_none(hub, monkeypatch):
    runs = [
        _file_run(hub, loras=("x.safetensors",)),
        _file_run(hub, steps=21),
        _file_run(hub, steps=22),
    ]
    _, recipe = _defaults(hub, monkeypatch, runs)
    assert (recipe.loras, recipe.loras_decided) == ([], True)


def test_a_swapped_run_votes_for_its_original_loader_s_file(hub, monkeypatch):
    """#1605 meets #1622: a run through a PixlStash loader is read unswapped."""
    missing, now = "test-vae-fp8.safetensors", "test-vae-bf16.safetensors"

    def with_vae(vae_node, steps=20):
        graph = _graph(
            extra={
                "8": vae_node,
                "6": _node("VAEDecode", samples=["5", 0], vae=["8", 0]),
            },
        )
        graph["5"]["inputs"]["steps"] = steps
        return graph

    original = with_vae(_node("VAELoader", vae_name=missing))
    old = record_api_graph(hub, original, library_uuid="test-library")
    plain = record_api_graph(
        hub, with_vae(_node("VAELoader", vae_name=now)), library_uuid="test-library"
    )
    swapped_topology, swaps = loader_swaps(
        original,
        with_vae(_node("PixlStashVAELoader", vae_sha256="ab" * 32)),
        {"8": {"vae_sha256": ("vae_name", now)}},
    )
    record_loader_swaps(hub, swapped_topology, swaps)
    ran = [
        record_api_graph(
            hub,
            with_vae(_node("PixlStashVAELoader", vae_sha256="ab" * 32), steps),
            library_uuid="test-library",
        )
        for steps in (21, 22)
    ]
    runs = [old, plain, *ran]
    _, recipe = _defaults(hub, monkeypatch, runs)
    (vae,) = [m for m in recipe.models if m.address.endswith("/vae_name")]
    # Three runs loaded the bf16 file, two of them through the swapped loader.
    assert vae.filename == now


def test_the_defaults_list_the_sampler_before_the_size_whatever_the_slot_labels():
    """Slot labels are digests, so they must not decide the order."""
    latent, sampler = "core:" + "a" * 64, "core:" + "f" * 64
    addresses = [
        (latent, "height"),
        (latent, "width"),
        (sampler, "steps"),
        (sampler, "cfg"),
        (sampler, "seed_extra"),
    ]
    ordered = sorted(addresses, key=workflow_card_service._address_order)
    assert [name for _, name in ordered] == [
        "steps",
        "cfg",
        "width",
        "height",
        "seed_extra",
    ]
