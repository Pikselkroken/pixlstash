"""The hub's workflow cards: the tables, the filing hook and the backfill (B2).

The graph fixture is the one the identity rules were written against
(``tests/test_workflow_identity.py``), so a card asserted here and a key
asserted there cannot drift apart.
"""

import json
import sqlite3

import pytest

from pixlstash.hub import workflow_cards
from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflows import (
    forget_asset_names,
    record_api_graph,
    record_ui_graph,
)
from pixlstash.services.workflow_identity import RECIPE, STRUCTURAL
from pixlstash.tasks.workflow_card_backfill_finder import WorkflowCardBackfillFinder
from pixlstash.tasks.workflow_card_backfill_task import WorkflowCardBackfillTask
from tests.test_workflow_identity import _graph

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
    assert cached["core_version"] == "v1"
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
    workflow_cards.record_file(
        hub, "portrait.json", keys.topology_hash, keys.structural_hash
    )

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

    The second pass is run with the guard REMOVED - the card rows are wiped and
    derived again from the same stored documents - because a re-run that returns
    early proves only that the early return exists. Byte-identical rows is the
    acceptance criterion, and it is why nothing here carries a timestamp.
    """
    filed = [
        record_api_graph(hub, _graph()),
        record_api_graph(hub, _graph(ckpt="dreamshaper.safetensors")),
        record_api_graph(hub, _graph(loras=(CHARACTER_LORA,), upscale=True)),
    ]
    wipe = "DELETE FROM workflow_variant", "DELETE FROM workflow_topology_core"
    with hub.transaction() as conn:
        for statement in (*wipe, "DELETE FROM workflow_slot_mark"):
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


def test_the_grouping_the_owner_gate_reads(hub):
    """Two checkpoints of one workflow stack; an unrelated workflow does not."""
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


def test_a_failed_batch_is_deferred_and_a_cancelled_one_is_not(hub):
    keys = record_api_graph(hub, _graph())
    with hub.transaction() as conn:
        conn.execute("DELETE FROM workflow_variant")
    finder = WorkflowCardBackfillFinder(hub=hub)

    from pixlstash.task_runner import TaskCancelledError

    task = finder.find_task()
    assert task.params["structural_hashes"] == [keys.structural_hash]
    finder.on_task_complete(task, TaskCancelledError("stopping"))
    assert finder.find_task() is not None

    finder.on_task_complete(task, sqlite3.OperationalError("database is locked"))
    assert finder.find_task() is None


def test_a_ui_file_lands_on_a_card_with_no_assets(hub):
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
    key = workflow_cards.record_file(hub, "ui.json", topology)

    row = hub.fetchone("SELECT * FROM workflow_file WHERE workflow_name = 'ui.json'")
    assert (row["topology_hash"], row["structural_hash"]) == (topology, None)
    assert row["workflow_key"] == key == workflow_cards.topology_only_key(topology)


def test_replacing_a_file_moves_it_to_the_new_card(hub):
    """A name is the owner's; the row says what is in the file now."""
    first = record_api_graph(hub, _graph())
    second = record_api_graph(hub, _graph(ckpt="dreamshaper.safetensors"))
    workflow_cards.record_file(
        hub, "flow.json", first.topology_hash, first.structural_hash
    )
    workflow_cards.record_file(
        hub, "flow.json", second.topology_hash, second.structural_hash
    )

    rows = hub.fetchall("SELECT structural_hash, workflow_key FROM workflow_file")
    assert len(rows) == 1
    assert rows[0]["structural_hash"] == second.structural_hash
    assert rows[0]["workflow_key"] == card_of(hub, second.structural_hash)
