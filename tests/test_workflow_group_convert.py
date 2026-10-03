"""The cut-over's state conversion (#1623): cards onto workflows.

Two halves. The hub step (``hub/workflow_group_convert.py``, data version 5)
is asserted row by row on a hub holding every kind of card state the issue
names, and run twice. The vault half (``MissingSavedRecipeWorkflowFinder``)
is asserted end to end: a recipe saved on a checkpoint-B card still runs on B.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError
from sqlmodel import delete, select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, SavedRecipe
from pixlstash.hub.db import HubDatabase
from pixlstash.hub.schema import CURRENT_DATA_VERSION, _has_old_cores
from pixlstash.hub.workflow_card_reads import (
    card_index,
    manual_document,
    workflow_index,
    workflow_of_variant,
)
from pixlstash.hub import workflow_origin
from pixlstash.hub.workflow_group_writes import (
    create_manual_workflow,
    delete_manual_workflow,
)
import pixlstash.routes.comfyui as comfyui_routes
import pixlstash.hub.workflow_group_convert as convert
from pixlstash.hub.workflow_group_convert import (
    _CORE_RULE_V1,
    _CORE_RULE_V2,
    _GROUP_NAMESPACE,
    _carry_group_state,
    _core_strip_v1,
    _core_strip_v2,
    convert_card_state,
    dissolve_manual_groups,
    rederive_cores,
    rederive_cores_v3,
)
from pixlstash.hub import workflow_cards
from pixlstash.hub.workflow_cards import auto_workflow_id
from pixlstash.hub.workflows import get_document, record_api_graph, record_ui_graph
from pixlstash.server import Server
import pixlstash.routes.workflows as workflows_routes
from pixlstash.services import workflow_inbox
from pixlstash.services.workflow_hash import graph_key, node_labels
from pixlstash.services.workflow_identity import (
    core_node_labels,
    topology_node_labels,
    WORKFLOW_KEY_VERSION,
)
from pixlstash.tasks.missing_saved_recipe_workflow_finder import (
    MissingSavedRecipeWorkflowFinder,
)
from pixlstash.tasks.saved_recipe_convert_task import _core_successor_map
from pixlstash.tasks.workflow_card_backfill_finder import WorkflowCardBackfillFinder
from pixlstash.tasks.workflow_card_backfill_task import FamilyReidentifyTask
from pixlstash.utils.workflow_ids import WORKFLOW_TAG_KEY
from tests.test_workflow_identity import CORE_V2_TWINS, CORE_V3_TWINS, _graph

API = "/api/v1"
LIB = "test-library"
SPEED_LORA = "test-lightning-8step.safetensors"
SPEED_SHA = "5" * 64
CHARACTER_LORA = "test-character.safetensors"
OTHER_CHARACTER_LORA = "test-other-character.safetensors"
SDXL = "sdxl_base.safetensors"
MANUAL = "a" * 32
MANUAL_TWO = "b" * 32

GROUP_TABLES = (
    "workflow_group",
    "workflow_group_member",
    "workflow_group_attr",
    "workflow_group_default",
    "workflow_group_pins",
    "workflow_group_picture_input",
    "workflow_key_successor",
)


def _card(hub, keys) -> str:
    return hub.fetchone(
        "SELECT workflow_key FROM workflow_variant WHERE structural_hash = ?",
        (keys.structural_hash,),
    )["workflow_key"]


def _labels(hub, keys, node_id):
    """``(slot label, core label or None)`` of one node of a filed graph."""
    document = get_document(hub, keys.structural_hash)
    return (
        topology_node_labels(document)[node_id],
        core_node_labels(document).get(node_id),
    )


def _rows(hub) -> dict:
    return {
        table: sorted(tuple(row) for row in hub.fetchall(f"SELECT * FROM {table}"))
        for table in GROUP_TABLES
    }


def _convert(hub) -> None:
    with hub.transaction() as conn:
        convert_card_state(conn)


@pytest.fixture
def world(tmp_path):
    """A hub with every kind of card state the issue's table names.

    Core C (txt2img): the speed-LoRA cover S (named, noted, position 0 of an
    ordered automatic stack), A1 and B1 on one topology with only B1 unstacked
    (a partial unstack), a hidden and named detailer D (the base topology), and
    P, the only card of its topology and unstacked (split out). Core C2
    (img2img): a manual stack [IU, I] and a second one [Ic, X], where I and Ic
    share a topology, I holding two variants to Ic's one.
    """
    hub = HubDatabase(str(tmp_path / "hub.db"))
    w = SimpleNamespace(hub=hub)

    def file(graph):
        return record_api_graph(hub, graph, library_uuid=LIB)

    # One base-model family throughout, so only the core decides the grouping.
    speed = _graph(ckpt=SDXL, loras=(SPEED_LORA,))
    speed["L0"]["inputs"]["strength_model"] = 0.5
    # Filed first, so the slot's mark freezes `structural` on the speed LoRA.
    w.s = file(speed)
    w.a1 = file(_graph(ckpt="sdxl_a.safetensors"))
    w.b1 = file(_graph(ckpt="sdxl_b.safetensors"))
    w.d = file(_graph(ckpt=SDXL, face_detailer=True))
    w.p = file(_graph(ckpt=SDXL, preview=True))
    w.i = file(_graph(img2img=True, loras=(CHARACTER_LORA,)))
    w.i_second = file(_graph(img2img=True, loras=(OTHER_CHARACTER_LORA,)))
    w.ic = file(
        _graph(img2img=True, loras=(CHARACTER_LORA,), ckpt="sdxl_c.safetensors")
    )
    w.iu = file(_graph(img2img=True, upscale=True))
    w.x = file(_graph(img2img=True, preview=True))
    for name in ("s", "a1", "b1", "d", "p", "i", "ic", "iu", "x"):
        setattr(w, f"{name}_key", _card(hub, getattr(w, name)))
    assert _card(hub, w.i_second) == w.i_key, "a character LoRA forked the card"
    w.auto = workflow_of_variant(hub, w.s.structural_hash)
    w.core = hub.fetchone(
        "SELECT core_hash FROM workflow_topology_core WHERE topology_hash = ?",
        (w.s.topology_hash,),
    )[0]
    w.split = uuid.uuid5(_GROUP_NAMESPACE, w.p.topology_hash).hex

    w.sampler_s, w.core_sampler = _labels(hub, w.s, "5")
    w.sampler_a1, _ = _labels(hub, w.a1, "5")
    w.detailer, detailer_core = _labels(hub, w.d, "31")
    assert detailer_core is None, "the face detailer is a stage, not core"
    w.loader_iu, w.core_loader = _labels(hub, w.iu, "10")
    w.loader_i, _ = _labels(hub, w.i, "10")

    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, kind, filename, sha256, provenance) "
            "VALUES ('adapter', 'unknown', ?, ?, 'scanned')",
            (SPEED_LORA, SPEED_SHA),
        )
        conn.executemany(
            "INSERT INTO workflow_attr (workflow_key, name, notes, hidden) "
            "VALUES (?, ?, ?, ?)",
            [
                (w.s_key, "Fast portrait", "Cover notes.", 0),
                (w.d_key, "Detailer", "Detailer notes.", 1),
                (w.p_key, "Preview", None, 1),
                (w.iu_key, "Img2img cover", None, 0),
                (w.i_key, "Img2img", "Character runs.", 0),
            ],
        )
        conn.execute(
            "INSERT INTO workflow_stack (stack_id, kind, core_hash) "
            "VALUES (?, 'auto', ?)",
            (w.auto, w.auto[len("auto:") :]),
        )
        conn.executemany(
            "INSERT INTO workflow_stack (stack_id, kind, core_hash) "
            "VALUES (?, 'manual', NULL)",
            [(MANUAL,), (MANUAL_TWO,)],
        )
        conn.executemany(
            "INSERT INTO workflow_stack_member (stack_id, workflow_key, position) "
            "VALUES (?, ?, ?)",
            [
                (w.auto, w.s_key, 0),
                (w.auto, w.a1_key, 1),
                (MANUAL, w.iu_key, 0),
                (MANUAL, w.i_key, 1),
                (MANUAL_TWO, w.ic_key, 0),
                (MANUAL_TWO, w.x_key, 1),
            ],
        )
        conn.executemany(
            "INSERT INTO workflow_unstacked (workflow_key) VALUES (?)",
            [(w.b1_key,), (w.p_key,)],
        )
        conn.executemany(
            "INSERT INTO workflow_default_override "
            "(workflow_key, slot_label, input_name, value) VALUES (?, ?, ?, ?)",
            [
                (w.s_key, w.sampler_s, "steps", "8"),
                (w.a1_key, w.sampler_a1, "steps", "30"),
                (w.a1_key, w.sampler_a1, "cfg", "5"),
                (w.a1_key, "no-such-label", "denoise", "0.4"),
                (w.d_key, w.detailer, "guide_size", "512"),
            ],
        )
        conn.executemany(
            "INSERT INTO workflow_key_pins (workflow_key, pins) VALUES (?, ?)",
            [
                (w.s_key, json.dumps([[w.sampler_s, "steps"]])),
                (
                    w.a1_key,
                    json.dumps([[w.sampler_a1, "cfg"], [w.sampler_a1, "steps"]]),
                ),
            ],
        )
        conn.executemany(
            "INSERT INTO workflow_key_picture_input (library_uuid, workflow_key, "
            "slot_label, input_name, mode, pixel_sha) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (LIB, w.iu_key, w.loader_iu, "image", "fixed", "sha-iu"),
                (LIB, w.i_key, w.loader_i, "image", "fixed", "sha-i"),
            ],
        )
        conn.execute(
            "INSERT INTO workflow_cover (library_uuid, workflow_key, pixel_sha) "
            "VALUES (?, ?, 'cover-sha')",
            (LIB, w.s_key),
        )
    try:
        yield w
    finally:
        hub.close()


def _expected(w) -> dict:
    core_steps = f"core:{w.core_sampler}/steps"
    core_cfg = f"core:{w.core_sampler}/cfg"
    return {
        "workflow_group": sorted(
            [
                (MANUAL, "manual", None),
                (MANUAL_TWO, "manual", None),
                (w.split, "manual", None),
                # Owner state is stored against it, so the automatic one is a
                # row, holding its core (#1692).
                (w.auto, "auto", w.core),
            ]
        ),
        "workflow_group_member": sorted(
            [
                # I held two variants of the topology to Ic's one.
                (w.i.topology_hash, MANUAL),
                (w.iu.topology_hash, MANUAL),
                (w.x.topology_hash, MANUAL_TWO),
                # Every card of it was unstacked.
                (w.p.topology_hash, w.split),
                # Not A1/B1's topology: only B1 was unstacked.
            ]
        ),
        "workflow_group_attr": sorted(
            [
                (
                    w.auto,
                    "Fast portrait",
                    "Fast portrait:\nCover notes.\n\nDetailer:\nDetailer notes."
                    "\n\nAlso named: Detailer",
                    # D was hidden, S and A1 were not.
                    0,
                ),
                (w.split, "Preview", None, 1),
                (
                    MANUAL,
                    "Img2img cover",
                    "Img2img:\nCharacter runs.\n\nAlso named: Img2img",
                    0,
                ),
            ]
        ),
        "workflow_group_default": sorted(
            [
                # The cover's 8 over A1's 30.
                (w.auto, core_steps, "8"),
                (w.auto, core_cfg, "5"),
                # A stage node, kept by slot label: D is on the base topology.
                (w.auto, f"{w.detailer}/guide_size", "512"),
                # The cover's structural speed LoRA, at the strength it ran at.
                (w.auto, f"lora:{SPEED_SHA}", "0.5"),
            ]
        ),
        "workflow_group_pins": [(w.auto, json.dumps([core_steps, core_cfg]))],
        "workflow_group_picture_input": [
            # The cover IU's picture over I's.
            (LIB, MANUAL, f"core:{w.core_loader}/image", "fixed", "sha-iu")
        ],
        "workflow_key_successor": sorted(
            [
                (w.s_key, w.auto),
                (w.a1_key, w.auto),
                (w.b1_key, w.auto),
                (w.d_key, w.auto),
                (w.p_key, w.split),
                (w.iu_key, MANUAL),
                (w.i_key, MANUAL),
                (w.ic_key, MANUAL),
                (w.x_key, MANUAL_TWO),
            ]
        ),
    }


def test_every_card_state_lands_on_its_workflow_and_a_rerun_changes_nothing(
    world, caplog
):
    caplog.set_level("INFO")
    _convert(world.hub)
    first = _rows(world.hub)
    expected = _expected(world)
    for table in GROUP_TABLES:
        assert first[table] == expected[table], table
    # The auto stack's cover choice (workflow_cover) is dropped: its picture is
    # nowhere in the new tables.
    assert "cover-sha" not in json.dumps(first)
    # The untranslatable default and the partial unstack are named in the log.
    assert "'0.4'" in caplog.text and "no-such-label" in caplog.text
    assert f"cards ['{world.b1_key}'] were unstacked" in caplog.text

    _convert(world.hub)
    assert _rows(world.hub) == first


def test_a_workflow_that_fails_to_convert_leaves_no_half_written_rows(
    world, monkeypatch
):
    """One workflow raising part-way leaves none of its rows; the rest convert."""
    real = convert._structural_loras

    def failing(hub, workflow_id, cover):
        # Called after the workflow's attributes were written.
        if workflow_id == world.auto:
            raise KeyError("malformed stored run")
        return real(hub, workflow_id, cover)

    monkeypatch.setattr(convert, "_structural_loras", failing)
    _convert(world.hub)

    rows = _rows(world.hub)
    assert not [row for row in rows["workflow_group_attr"] if row[0] == world.auto]
    assert not [row for row in rows["workflow_group_default"] if row[0] == world.auto]
    assert [row[0] for row in rows["workflow_group_attr"] if row[0] == MANUAL] == [
        MANUAL
    ]


def test_an_unnamed_cover_takes_the_next_name_it_holds(world):
    """A cover with no name has nothing to win with: the workflow keeps the
    first name in cover order, and it is not repeated as "Also named"."""
    hub = world.hub
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_attr SET name = NULL WHERE workflow_key = ?",
            (world.s_key,),
        )

    _convert(hub)

    row = hub.fetchone(
        "SELECT name, notes FROM workflow_group_attr WHERE workflow_id = ?",
        (world.auto,),
    )
    assert row["name"] == "Detailer"
    assert "Also named" not in row["notes"]


def test_a_file_only_card_keeps_what_the_owner_typed(tmp_path):
    """A workflow file with no recipe (#1466) is its own workflow, and converts.

    It has no core hash and no stored graph, so it is ``auto:<topology hash>``
    as ``workflow_index`` files it; its name, notes, hidden flag and successor
    row must not be dropped for want of a core.
    """
    hub = HubDatabase(str(tmp_path / "hub.db"))
    ui = {
        "nodes": [
            {
                "id": 1,
                "type": "LoadImage",
                "inputs": [],
                "outputs": [{"name": "IMAGE", "links": [1]}],
                "widgets_values": ["in.png", "image"],
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
    key = workflow_cards.topology_only_key(topology)
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_file (workflow_name, topology_hash, "
            "structural_hash, workflow_key) VALUES ('ui.json', ?, NULL, ?)",
            (topology, key),
        )
        conn.execute(
            "INSERT INTO workflow_attr (workflow_key, name, notes, hidden) "
            "VALUES (?, ?, ?, 1)",
            (key, "Upscale only", "Run at night."),
        )

    _convert(hub)

    workflow_id = f"auto:{topology}"
    assert _rows(hub)["workflow_key_successor"] == [(key, workflow_id)]
    assert _rows(hub)["workflow_group_attr"] == [
        (workflow_id, "Upscale only", "Run at night.", 1)
    ]


_EDITOR = {
    "nodes": [
        {
            "id": 1,
            "type": "LoadImage",
            "inputs": [],
            "outputs": [{"name": "IMAGE", "links": [1]}],
            "widgets_values": ["in.png", "image"],
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
_EDITOR_AS_API = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "in.png"}},
    "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
}


def _step_7_world(tmp_path):
    """A hub as data step 6 left it, and the user folder its file rows name.

    ``api.json`` is a file of a card its pictures made; ``editor.json`` a
    file-only card (#1466) whose owner state steps 5 and 6 carried to
    ``auto:<topology>``, converted by ComfyUI (#1530); ``pulled.json`` one a
    pull wrote; ``gone.json`` a row whose file is no longer there.
    """
    hub = HubDatabase(str(tmp_path / "hub.db"))
    folder = tmp_path / "user"
    folder.mkdir()
    api = record_api_graph(hub, _graph(), library_uuid=LIB)
    topology = record_ui_graph(hub, _EDITOR)
    for name, document in (
        ("api.json", _graph()),
        ("editor.json", _EDITOR),
        ("pulled.json", _graph(ckpt="pulled.safetensors")),
    ):
        (folder / name).write_text(json.dumps(document), encoding="utf-8")
    (folder / "editor.json.api").write_text(
        json.dumps(
            {
                "converted_from": comfyui_routes._editor_digest(_EDITOR),
                "prompt": _EDITOR_AS_API,
            }
        ),
        encoding="utf-8",
    )
    with hub.transaction() as conn:
        conn.executemany(
            "INSERT INTO workflow_file (workflow_name, topology_hash, "
            "structural_hash, workflow_key) VALUES (?, ?, ?, ?)",
            [
                ("api.json", api.topology_hash, api.structural_hash, "k1"),
                ("editor.json", topology, None, "k2"),
                ("pulled.json", api.topology_hash, api.structural_hash, "k3"),
                ("gone.json", api.topology_hash, api.structural_hash, "k4"),
            ],
        )
        conn.execute("INSERT INTO workflow_pulled_file VALUES ('pulled.json')")
        conn.execute(
            "INSERT INTO workflow_origin (origin, remote_path, workflow_name, "
            "first_pulled_at, last_seen_at, content_hash) VALUES "
            "('http://comfy.test', 'a/pulled.json', 'pulled.json', 't', 't', 'h')"
        )
        conn.execute(
            "INSERT INTO workflow_group_attr (workflow_id, name, notes, hidden) "
            "VALUES (?, 'Upscale only', 'Run at night.', 1)",
            (f"auto:{topology}",),
        )
    return hub, folder, topology


def test_step_7_makes_every_stored_file_a_manual_workflow_once(tmp_path):
    """Nothing the owner imported disappears across the upgrade, and a second
    run writes identical rows."""
    hub, folder, topology = _step_7_world(tmp_path)
    tables = ("workflow_document", "workflow_group_attr", "workflow_origin")

    def snapshot():
        return {
            table: sorted(tuple(row) for row in hub.fetchall(f"SELECT * FROM {table}"))
            for table in tables
        }

    with hub.transaction() as conn:
        assert convert.adopt_workflow_files(conn, str(folder)) == 3
    rows = {
        row["name"]: row
        for row in hub.fetchall(
            "SELECT d.workflow_id, d.origin, d.api_document, a.name, a.notes, "
            "a.hidden FROM workflow_document d JOIN workflow_group_attr a "
            "ON a.workflow_id = d.workflow_id"
        )
    }
    assert set(rows) == {"api", "Upscale only", "pulled"}
    assert {name: row["origin"] for name, row in rows.items()} == {
        "api": "import",
        "Upscale only": "import",
        "pulled": "pull",
    }
    editor = rows["Upscale only"]
    # Tagged with its id, so a ComfyUI run of it files its pictures on it.
    document = hub.fetchone(
        "SELECT document FROM workflow_document WHERE workflow_id = ?",
        (editor["workflow_id"],),
    )[0]
    assert json.loads(document)["extra"][WORKFLOW_TAG_KEY] == editor["workflow_id"]
    # The file-only card's owner state came with it, off `auto:<topology>`.
    assert (editor["notes"], editor["hidden"]) == ("Run at night.", 1)
    assert (
        hub.fetchone(
            "SELECT 1 FROM workflow_group_attr WHERE workflow_id = ?",
            (f"auto:{topology}",),
        )
        is None
    )
    # Runs from the conversion stored with it, and the file is never read again.
    (folder / "editor.json.api").unlink()
    assert manual_document(hub, editor["workflow_id"]) == _EDITOR_AS_API
    # The pull that wrote a file now names its workflow.
    assert (
        hub.fetchone(
            "SELECT workflow_name FROM workflow_origin WHERE origin != 'file'"
        )[0]
        == (rows["pulled"]["workflow_id"])
    )
    # Each adopted file is on record, so deleting its workflow trashes it.
    adopted = {
        row[0]: row[1]
        for row in hub.fetchall(
            "SELECT remote_path, workflow_name FROM workflow_origin "
            "WHERE origin = 'file'"
        )
    }
    assert adopted == {
        "api.json": rows["api"]["workflow_id"],
        "editor.json": editor["workflow_id"],
        "pulled.json": rows["pulled"]["workflow_id"],
    }
    assert {c.workflow_key for c in card_index(hub) if c.manual} == {
        row["workflow_id"] for row in rows.values()
    }
    first = snapshot()

    with hub.transaction() as conn:
        assert convert.adopt_workflow_files(conn, str(folder)) == 0
    assert snapshot() == first
    hub.close()


def test_the_hub_open_runs_step_7_on_the_user_folder(tmp_path, monkeypatch):
    hub, folder, _topology = _step_7_world(tmp_path)
    monkeypatch.setattr(
        "pixlstash.services.workflow_inbox.workflow_user_dir", lambda: str(folder)
    )
    with hub.transaction() as conn:
        conn.execute("PRAGMA user_version = 6")
    path = hub.path
    hub.close()
    reopened = HubDatabase(path)
    try:
        assert reopened.fetchone("PRAGMA user_version")[0] == CURRENT_DATA_VERSION
        assert reopened.fetchone("SELECT COUNT(*) FROM workflow_document")[0] == 3
    finally:
        reopened.close()


def test_the_hub_open_hashes_built_in_origin_rows_stored_without_one(tmp_path):
    """Data step 9: a built-in adopted before its row carried the hash.

    The inbox then stores the same content a second time; after the step it
    matches the built-in's workflow. Once only: a second run hashes nothing.
    """
    hub = HubDatabase(str(tmp_path / "hub.db"))
    builtin = create_manual_workflow(
        hub,
        "flow",
        _EDITOR,
        "builtin",
        record=(workflow_origin.BUILTIN_ORIGIN, "flow.json", None, None),
    )
    # Left alone: another origin's hashless row, a built-in whose workflow
    # is gone, and one whose stored document will not parse.
    create_manual_workflow(
        hub, "other", _EDITOR, "import", record=("file", "other.json", None, None)
    )
    with hub.transaction() as conn:
        workflow_origin.upsert(
            conn, workflow_origin.BUILTIN_ORIGIN, "gone.json", "manual:0", None, None
        )
        conn.execute(
            "INSERT INTO workflow_document (workflow_id, document, origin, "
            "created_at) VALUES ('manual:1', '{', 'builtin', '')"
        )
        workflow_origin.upsert(
            conn, workflow_origin.BUILTIN_ORIGIN, "bad.json", "manual:1", None, None
        )
    digest = workflow_inbox.content_hash(_EDITOR)
    assert workflow_origin.stored_as(hub, digest) is None
    with hub.transaction() as conn:
        conn.execute("PRAGMA user_version = 8")
    path = hub.path
    hub.close()
    reopened = HubDatabase(path)
    try:
        assert reopened.fetchone("PRAGMA user_version")[0] == CURRENT_DATA_VERSION
        assert workflow_origin.stored_as(reopened, digest) == builtin
        assert {
            (row["origin"], row["remote_path"])
            for row in reopened.fetchall(
                "SELECT origin, remote_path FROM workflow_origin "
                "WHERE content_hash IS NULL"
            )
        } == {
            ("file", "other.json"),
            (workflow_origin.BUILTIN_ORIGIN, "gone.json"),
            (workflow_origin.BUILTIN_ORIGIN, "bad.json"),
        }
        with reopened.transaction() as conn:
            assert convert.hash_builtin_origins(conn) == 0
    finally:
        reopened.close()


def _auto_of(hub, keys) -> str:
    return workflow_of_variant(hub, keys.structural_hash)


def _successor(hub, key) -> str:
    return hub.fetchone(
        "SELECT workflow_id FROM workflow_key_successor WHERE workflow_key = ?",
        (key,),
    )["workflow_id"]


def test_step_six_puts_every_hand_made_group_back_in_its_automatic_workflow(world):
    """Workflows are automatic: the cut-over's groups go, their state carried.

    MANUAL (IU and I, one img2img core) hands its name, notes and picture
    input to that core's automatic workflow, which has none of its own. The
    split-out P hands its name to its automatic workflow, which keeps its own
    and gains P's as a note.
    """
    hub = world.hub
    _convert(hub)
    with hub.transaction() as conn:
        assert dissolve_manual_groups(conn) == 3
    img2img = _auto_of(hub, world.i)  # placements gone: its automatic one
    assert hub.fetchone("SELECT 1 FROM workflow_group_member") is None
    assert hub.fetchone("SELECT 1 FROM workflow_group WHERE kind = 'manual'") is None
    for name in ("s", "a1", "b1", "d", "p", "iu", "i", "ic", "x"):
        assert _successor(hub, getattr(world, f"{name}_key")) == _auto_of(
            hub, getattr(world, name)
        ), name
    for gone in (MANUAL, MANUAL_TWO, world.split):
        for table in GROUP_TABLES[2:6]:
            assert (
                hub.fetchone(f"SELECT 1 FROM {table} WHERE workflow_id = ?", (gone,))
                is None
            ), (gone, table)
    row = hub.fetchone(
        "SELECT name, notes, hidden FROM workflow_group_attr WHERE workflow_id = ?",
        (img2img,),
    )
    assert tuple(row) == (
        "Img2img cover",
        "Img2img:\nCharacter runs.\n\nAlso named: Img2img",
        0,
    )
    assert [
        tuple(r)
        for r in hub.fetchall(
            "SELECT address, pixel_sha FROM workflow_group_picture_input "
            "WHERE workflow_id = ?",
            (img2img,),
        )
    ] == [(f"core:{world.core_loader}/image", "sha-iu")]
    # P's workflow is S's: its own name stands, P's is kept as a note.
    assert _auto_of(hub, world.p) == world.auto
    row = hub.fetchone(
        "SELECT name, notes FROM workflow_group_attr WHERE workflow_id = ?",
        (world.auto,),
    )
    assert row["name"] == "Fast portrait"
    assert row["notes"].endswith("\n\nAlso named: Preview")
    with hub.transaction() as conn:
        assert dissolve_manual_groups(conn) == 0


def test_a_group_with_no_automatic_heir_keeps_its_successor_rows(world):
    """No heir and no topology of its own: the NOT NULL row is left, not nulled."""
    hub = world.hub
    _convert(hub)
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_group (workflow_id, kind) VALUES ('orphan', 'manual')"
        )
        conn.execute(
            "INSERT INTO workflow_group_member (topology_hash, workflow_id) "
            "VALUES ('no-such-topology', 'orphan')"
        )
        conn.execute(
            "INSERT INTO workflow_key_successor (workflow_key, workflow_id) "
            "VALUES ('no-such-card', 'orphan')"
        )
    with hub.transaction() as conn:
        dissolve_manual_groups(conn)
    assert _successor(hub, "no-such-card") == "orphan"


def test_the_hub_open_runs_the_conversion_once(world, tmp_path):
    """Data steps 5 and 6: a hub on data version 4 is converted, then the
    hand-made groups the conversion made are put back in automatic ones."""
    path = world.hub.path
    with world.hub.transaction() as conn:
        conn.execute("PRAGMA user_version = 4")
    world.hub.close()
    reopened = HubDatabase(path)
    try:
        assert reopened.fetchone("PRAGMA user_version")[0] == CURRENT_DATA_VERSION
        assert reopened.fetchone("SELECT 1 FROM workflow_group_member") is None
        assert (
            reopened.fetchone(
                "SELECT workflow_id FROM workflow_key_successor WHERE workflow_key = ?",
                (world.s_key,),
            )["workflow_id"]
            == world.auto
        )
    finally:
        reopened.close()
        world.hub = HubDatabase(path)


# ── data step 8: core rule v2 ───────────────────────────────────────────────

STEP_8_TABLES = (
    "workflow_topology_core",
    "workflow_variant_family",
    "workflow_core_successor",
    "workflow_group",
    "workflow_group_attr",
    "workflow_group_default",
    "workflow_group_pins",
    "workflow_group_picture_input",
    "workflow_key_successor",
    "workflow_id_successor",
    "workflow_document",
)
MANUAL_ID = "manual:" + "c" * 32


def _back_to_v1(hub, *filed) -> dict:
    """Put *filed*'s topologies back on core rule v1, as a v7 hub holds them.

    Returns ``{name: (v1 workflow id, {node id: v1 core label})}``.
    """
    found = {}
    for keys in filed:
        v1 = _core_strip_v1(get_document(hub, keys.structural_hash))
        found[keys.topology_hash] = (
            f"auto:{graph_key(v1)}",
            node_labels(v1, rounds=None),
        )
    with hub.transaction() as conn:
        for topology_hash, (old_id, _) in found.items():
            conn.execute(
                "UPDATE workflow_topology_core SET core_version = ?, core_hash = ? "
                "WHERE topology_hash = ?",
                (_CORE_RULE_V1, old_id[len("auto:") :], topology_hash),
            )
            # A v7 hub has no family rows: step 8 derives them.
            conn.execute(
                "DELETE FROM workflow_variant_family WHERE structural_hash IN "
                "(SELECT structural_hash FROM workflow_variant WHERE topology_hash = ?)",
                (topology_hash,),
            )
    return found


def _step_8_rows(hub) -> dict:
    return {
        table: sorted(tuple(row) for row in hub.fetchall(f"SELECT * FROM {table}"))
        for table in STEP_8_TABLES
    }


FLUX = "flux1-krea-dev.safetensors"
QWEN = "qwen_image_fp8.safetensors"


@pytest.fixture
def step_8(tmp_path):
    """v1 workflows v2 combines by core and splits by base-model family.

    Flux runs: plain (and with a LoRA, the same v1 workflow), an orphan
    encoder, seed variance; all one v2 workflow. A Qwen run on the plain
    topology shares the plain v1 workflow and splits from it. Each v1 id
    carries owner state addressed on its v1 core; a manual workflow names the
    orphan one as where it came from.
    """
    hub = HubDatabase(str(tmp_path / "hub.db"))
    w = SimpleNamespace(hub=hub)
    w.plain = record_api_graph(hub, _graph(ckpt=FLUX), library_uuid=LIB)
    w.lora = record_api_graph(
        hub, _graph(ckpt=FLUX, loras=("x.safetensors",)), library_uuid=LIB
    )
    w.qwen = record_api_graph(hub, _graph(ckpt=QWEN), library_uuid=LIB)
    w.orphan = record_api_graph(
        hub,
        _graph(ckpt=FLUX, extra=CORE_V2_TWINS["orphan-encoder"][1]),
        library_uuid=LIB,
    )
    w.sve = record_api_graph(
        hub,
        _graph(ckpt=FLUX, extra=CORE_V2_TWINS["seed-variance"][1]),
        library_uuid=LIB,
    )
    assert w.qwen.topology_hash == w.plain.topology_hash
    v1 = _back_to_v1(hub, w.plain, w.lora, w.orphan, w.sve)
    (w.plain_id, plain_labels) = v1[w.plain.topology_hash]
    assert v1[w.lora.topology_hash][0] == w.plain_id
    (w.orphan_id, orphan_labels) = v1[w.orphan.topology_hash]
    (w.sve_id, sve_labels) = v1[w.sve.topology_hash]
    assert len({w.plain_id, w.orphan_id, w.sve_id}) == 3
    w.new_sampler = core_node_labels(get_document(hub, w.plain.structural_hash))["5"]
    w.sve_slot = topology_node_labels(get_document(hub, w.sve.structural_hash))["94"]
    w.orphan_key = _card(hub, w.orphan)
    w.qwen_key = _card(hub, w.qwen)
    with hub.transaction() as conn:
        conn.executemany(
            "INSERT INTO workflow_group (workflow_id, kind, core_hash) "
            "VALUES (?, 'auto', ?)",
            [(i, i[len("auto:") :]) for i in (w.plain_id, w.orphan_id)],
        )
        conn.executemany(
            "INSERT INTO workflow_group_attr (workflow_id, name, notes, hidden) "
            "VALUES (?, ?, ?, 0)",
            [(w.plain_id, "Plain", None), (w.orphan_id, "Orphan", "Orphan notes.")],
        )
        conn.executemany(
            "INSERT INTO workflow_group_default (workflow_id, address, value) "
            "VALUES (?, ?, ?)",
            [
                (w.plain_id, f"core:{plain_labels['5']}/steps", "8"),
                (w.orphan_id, f"core:{orphan_labels['5']}/steps", "30"),
                (w.orphan_id, f"core:{orphan_labels['5']}/cfg", "5"),
                # The orphan is pruned and its topology is not the base: dropped.
                (w.orphan_id, f"core:{orphan_labels['97']}/text", "left"),
                # Seed variance is a stage now, on the base topology.
                (w.sve_id, f"core:{sve_labels['94']}/strength", "0.5"),
                (w.orphan_id, "lora:" + "5" * 64, "0.7"),
                (MANUAL_ID, "some-slot/steps", "4"),
            ],
        )
        conn.executemany(
            "INSERT INTO workflow_group_pins (workflow_id, pins) VALUES (?, ?)",
            [
                (w.plain_id, json.dumps([f"core:{plain_labels['5']}/steps"])),
                (w.orphan_id, json.dumps([f"core:{orphan_labels['5']}/cfg"])),
            ],
        )
        conn.executemany(
            "INSERT INTO workflow_key_successor (workflow_key, workflow_id) "
            "VALUES (?, ?)",
            [(w.orphan_key, w.orphan_id), (w.qwen_key, w.plain_id)],
        )
        conn.execute(
            "INSERT INTO workflow_document (workflow_id, document, origin, "
            "from_workflow_id, from_name, created_at) "
            "VALUES (?, '{}', 'duplicate', ?, 'Orphan', 't')",
            (MANUAL_ID, w.orphan_id),
        )
    try:
        yield w
    finally:
        hub.close()


def test_step_8_moves_every_v1_workflow_and_its_state_onto_v2(step_8, caplog):
    caplog.set_level("INFO")
    w = step_8
    with w.hub.transaction() as conn:
        assert rederive_cores(conn) == 4
    rows = _step_8_rows(w.hub)
    flux = workflow_of_variant(w.hub, w.plain.structural_hash)
    qwen = workflow_of_variant(w.hub, w.qwen.structural_hash)
    steps, cfg = f"core:{w.new_sampler}/steps", f"core:{w.new_sampler}/cfg"

    # Families, frozen per variant: Qwen splits from the Flux runs.
    assert flux != qwen
    assert {
        workflow_of_variant(w.hub, keys.structural_hash)
        for keys in (w.lora, w.orphan, w.sve)
    } == {flux}
    assert dict((r[0], r[1]) for r in rows["workflow_variant_family"]) == {
        w.plain.structural_hash: "flux1",
        w.lora.structural_hash: "flux1",
        w.orphan.structural_hash: "flux1",
        w.sve.structural_hash: "flux1",
        w.qwen.structural_hash: "qwen",
    }
    # The v2 rows are written, so no card is pending and the grid never blanks.
    assert {r[2] for r in rows["workflow_topology_core"]} == {
        workflow_cards.CORE_RULE_VERSION
    }
    assert workflow_cards.unidentified_variants(w.hub, 10) == []
    assert {r[1:3] for r in rows["workflow_core_successor"]} == {
        (w.plain_id, flux),
        (w.plain_id, qwen),
        (w.orphan_id, flux),
        (w.sve_id, flux),
    }
    orphan_map = next(
        r[3] for r in rows["workflow_core_successor"] if r[0] == w.orphan.topology_hash
    )
    assert None in json.loads(orphan_map).values()

    assert rows["workflow_group_default"] == sorted(
        [
            (flux, steps, "8"),  # the heir's own wins over the orphan's 30
            (flux, cfg, "5"),
            (flux, f"{w.sve_slot}/strength", "0.5"),
            (flux, "lora:" + "5" * 64, "0.7"),
            # The split: plain's own state is copied to its Qwen successor.
            (qwen, steps, "8"),
            (MANUAL_ID, "some-slot/steps", "4"),  # manual: untouched
        ]
    )
    assert "'30'" in caplog.text and "'left'" in caplog.text
    assert rows["workflow_group_pins"] == sorted(
        [(flux, json.dumps([steps, cfg])), (qwen, json.dumps([steps]))]
    )
    attrs = {r[0]: r for r in rows["workflow_group_attr"]}
    assert set(attrs) == {flux, qwen}
    assert attrs[flux][1] == "Plain" and "Orphan notes." in attrs[flux][2]
    assert attrs[qwen][1:3] == ("Plain", None)
    # The v2 core both heirs are built on, not the id's digest (#1692).
    core = w.hub.fetchone(
        "SELECT core_hash FROM workflow_topology_core WHERE topology_hash = ?",
        (w.plain.topology_hash,),
    )[0]
    assert rows["workflow_group"] == sorted([(i, "auto", core) for i in (flux, qwen)])
    # A card follows its own variants; the retired ids follow the primary.
    assert rows["workflow_key_successor"] == sorted(
        [(w.orphan_key, flux), (w.qwen_key, qwen)]
    )
    assert rows["workflow_id_successor"] == sorted(
        [(w.plain_id, flux), (w.orphan_id, flux), (w.sve_id, flux)]
    )
    assert rows["workflow_document"][0][4] == flux

    with w.hub.transaction() as conn:
        assert rederive_cores(conn) == 0
    assert _step_8_rows(w.hub) == rows


def test_step_8_re_derives_the_type_so_a_wan_video_reads_video(tmp_path):
    """No data step of its own for the ``video`` type: step 8 re-derives it.

    ``rederive_cores`` rewrites every v1 ``workflow_topology_core`` row
    through ``_cache_topology``, which calls ``workflow_type`` afresh, so a
    Wan 2.2 text-to-video graph a v1 hub cached as ``txt2img`` (its
    ``EmptyHunyuanLatentVideo`` met the ``^Empty.*Latent`` rule) is ``video``
    under the current ``CORE_RULE_VERSION`` as soon as the hub opens.
    """
    graph = json.loads(
        (
            Path(__file__).parent
            / "comfyui_workflows/paired/multigpu/api/wan2_2 distorch2 double_unet no_cpu.json"
        ).read_text("utf-8")
    )
    hub = HubDatabase(str(tmp_path / "hub.db"))
    try:
        wan = record_api_graph(hub, graph, library_uuid=LIB)
        _back_to_v1(hub, wan)
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE workflow_topology_core SET workflow_type = 'txt2img' "
                "WHERE topology_hash = ?",
                (wan.topology_hash,),
            )
            assert rederive_cores(conn) == 1
        row = hub.fetchone(
            "SELECT core_version, workflow_type FROM workflow_topology_core "
            "WHERE topology_hash = ?",
            (wan.topology_hash,),
        )
        assert tuple(row) == (workflow_cards.CORE_RULE_VERSION, "video")
    finally:
        hub.close()


def test_a_label_one_topology_pruned_still_carries_from_another(step_8, monkeypatch):
    """The plain workflow's sampler default survives one topology mapping it to None."""
    w = step_8
    # Pruned on whichever of the two topologies is merged first.
    first = min((w.plain, w.qwen, w.lora), key=lambda keys: keys.topology_hash)
    pruned = [
        get_document(w.hub, keys.structural_hash)
        for keys in (w.plain, w.qwen, w.lora)
        if keys.topology_hash == first.topology_hash
    ]
    real = convert.core_label_maps

    def pruned_first(document):
        labels, stages = real(document)
        if document in pruned:
            labels = dict.fromkeys(labels)
        return labels, stages

    monkeypatch.setattr(convert, "core_label_maps", pruned_first)
    with w.hub.transaction() as conn:
        rederive_cores(conn)
    flux = workflow_of_variant(w.hub, w.plain.structural_hash)
    assert (flux, f"core:{w.new_sampler}/steps", "8") in _step_8_rows(w.hub)[
        "workflow_group_default"
    ]


def test_a_recipes_label_map_prefers_a_kept_label_over_a_prune(tmp_path):
    """The vault's conversion merges topology maps as data step 8 does."""
    hub = HubDatabase(str(tmp_path / "hub.db"))
    try:
        with hub.transaction() as conn:
            conn.executemany(
                "INSERT INTO workflow_core_successor (topology_hash, "
                "old_workflow_id, new_workflow_id, label_map) VALUES (?, ?, ?, ?)",
                [
                    ("t1", "auto:old", "auto:new", json.dumps({"s": None})),
                    ("t2", "auto:old", "auto:new", json.dumps({"s": "kept"})),
                ],
            )
        assert _core_successor_map(hub, "auto:old") == {"s": "kept"}
    finally:
        hub.close()


def test_the_hub_open_runs_step_8_once(step_8):
    w = step_8
    path = w.hub.path
    with w.hub.transaction() as conn:
        conn.execute("PRAGMA user_version = 7")
    w.hub.close()
    w.hub = HubDatabase(path)
    assert w.hub.fetchone("PRAGMA user_version")[0] == CURRENT_DATA_VERSION
    rows = _step_8_rows(w.hub)
    flux = workflow_of_variant(w.hub, w.plain.structural_hash)
    qwen = workflow_of_variant(w.hub, w.qwen.structural_hash)
    assert {r[:3] for r in rows["workflow_core_successor"]} == {
        (w.plain.topology_hash, w.plain_id, flux),
        (w.plain.topology_hash, w.plain_id, qwen),
        (w.lora.topology_hash, w.plain_id, flux),
        (w.orphan.topology_hash, w.orphan_id, flux),
        (w.sve.topology_hash, w.sve_id, flux),
    }
    assert {r[0]: r[1] for r in rows["workflow_id_successor"]} == {
        w.plain_id: flux,
        w.orphan_id: flux,
        w.sve_id: flux,
    }
    w.hub.close()
    w.hub = HubDatabase(path)
    assert _step_8_rows(w.hub) == rows


def test_a_v1_row_an_older_build_writes_is_moved_on_the_next_open(step_8):
    """A second checkout on an older build shares this hub: the v1 rows it
    writes, and the state its owner edits land on, move on the next open."""
    w = step_8
    with w.hub.transaction() as conn:
        rederive_cores(conn)
    flux = workflow_of_variant(w.hub, w.orphan.structural_hash)
    # The older build re-caches the orphan topology on v1; the owner names
    # that v1 workflow there.
    (old_id, _labels) = _back_to_v1(w.hub, w.orphan)[w.orphan.topology_hash]
    with w.hub.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_group_attr (workflow_id, name, notes, "
            "hidden) VALUES (?, 'Named on the old build', NULL, 0)",
            (old_id,),
        )
    path = w.hub.path
    w.hub.close()
    w.hub = HubDatabase(path)
    assert workflow_of_variant(w.hub, w.orphan.structural_hash) == flux
    notes = w.hub.fetchone(
        "SELECT notes FROM workflow_group_attr WHERE workflow_id = ?", (flux,)
    )["notes"]
    assert "Named on the old build" in notes
    assert (
        w.hub.fetchone(
            "SELECT 1 FROM workflow_group_attr WHERE workflow_id = ?", (old_id,)
        )
        is None
    )


def test_a_card_with_no_cache_row_is_moved_and_a_stranded_id_is_named(step_8, caplog):
    """Steps 5 and 6 file a card with no cache row on its document's v1 core;
    step 8 moves it. State on an id nothing leads to is logged."""
    caplog.set_level("WARNING")
    w = step_8
    with w.hub.transaction() as conn:
        conn.execute(
            "DELETE FROM workflow_topology_core WHERE topology_hash = ?",
            (w.sve.topology_hash,),
        )
        conn.execute(
            "INSERT INTO workflow_group_attr (workflow_id, name, notes, hidden) "
            "VALUES (?, 'Nowhere', NULL, 0)",
            ("auto:" + "9" * 64,),
        )
        rederive_cores(conn)
    flux = workflow_of_variant(w.hub, w.sve.structural_hash)
    assert (
        w.hub.fetchone(
            "SELECT successor_id FROM workflow_id_successor WHERE workflow_id = ?",
            (w.sve_id,),
        )[0]
        == flux
    )
    assert w.hub.fetchone(
        "SELECT 1 FROM workflow_group_default WHERE workflow_id = ? AND address = ?",
        (flux, f"{w.sve_slot}/strength"),
    )
    assert "auto:" + "9" * 64 in caplog.text


def test_a_card_none_of_whose_variants_reduces_gets_no_family(step_8, caplog):
    """Never another card's family: it stays pending for the backfill."""
    caplog.set_level("WARNING")
    w = step_8
    with w.hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_recipe_graph SET document = '{\"1\": 7}' "
            "WHERE structural_hash = ?",
            (w.qwen.structural_hash,),
        )
        rederive_cores(conn)
    assert (
        w.hub.fetchone(
            "SELECT 1 FROM workflow_variant_family WHERE structural_hash = ?",
            (w.qwen.structural_hash,),
        )
        is None
    )
    assert workflow_of_variant(w.hub, w.qwen.structural_hash) is None
    assert f"Card {w.qwen_key}: no variant of it reduces" in caplog.text


def _core_version(hub, keys) -> str:
    return hub.fetchone(
        "SELECT core_version FROM workflow_topology_core WHERE topology_hash = ?",
        (keys.topology_hash,),
    )[0]


def test_a_topology_whose_move_raises_is_rolled_back_and_the_rest_move(
    step_8, monkeypatch, caplog
):
    """#1696: a non-sqlite error must not escape step 8 and refuse the hub."""
    w = step_8
    real = convert.variant_families

    def raising(hub, structural_hash, document, shelf=None):
        # After the topology's v2 row is written: the savepoint must undo it.
        if structural_hash == w.sve.structural_hash:
            raise KeyError("a malformed node")
        return real(hub, structural_hash, document, shelf)

    monkeypatch.setattr(convert, "variant_families", raising)
    with w.hub.transaction() as conn:
        assert rederive_cores(conn) == 3
        assert not _has_old_cores(conn), "the step would re-run on every open"
    assert _core_version(w.hub, w.sve) == convert._CORE_RULE_V1_UNMOVED
    assert _core_version(w.hub, w.orphan) == workflow_cards.CORE_RULE_VERSION
    assert f"Topology {w.sve.topology_hash} failed to move" in caplog.text
    # Its own rows were rolled back, not half-written.
    assert not w.hub.fetchone(
        "SELECT 1 FROM workflow_core_successor WHERE topology_hash = ?",
        (w.sve.topology_hash,),
    )


def test_a_v1_topology_that_will_not_reduce_is_tried_once(step_8):
    """#1696: left on the v1 stamp, `_has_old_cores` re-ran step 8 forever."""
    w = step_8
    with w.hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_recipe_graph SET document = '{\"1\": 7}' "
            "WHERE structural_hash = ?",
            (w.orphan.structural_hash,),
        )
        rederive_cores(conn)
        assert not _has_old_cores(conn)
    assert _core_version(w.hub, w.orphan) == convert._CORE_RULE_V1_UNMOVED


# ── data step 10: core rule v2 onto v3 (#1719) ────────────────────────────


@pytest.fixture
def step_10(tmp_path):
    """v2 workflows v3 merges: a plain Flux run, and its Seed and AuraFlow twins.

    Each twin's v2 workflow is named and carries a sampler default on its v2
    core; the plain one's id is unchanged by v3, so it lives on and takes the
    twins' state. A two-pass graph is a workflow of its own under both rules.
    """
    hub = HubDatabase(str(tmp_path / "hub.db"))
    w = SimpleNamespace(hub=hub)
    w.plain = record_api_graph(hub, _graph(ckpt=FLUX), library_uuid=LIB)
    w.seed = record_api_graph(
        hub, _graph(ckpt=FLUX, extra=CORE_V3_TWINS["seed"][1]), library_uuid=LIB
    )
    w.aura = record_api_graph(
        hub, _graph(ckpt=FLUX, extra=CORE_V3_TWINS["aura-flow"][1]), library_uuid=LIB
    )
    second = _graph()["5"]
    second["inputs"]["latent_image"] = ["5", 0]
    w.two_pass = record_api_graph(
        hub,
        _graph(
            ckpt=FLUX,
            extra={
                "8": second,
                "6": {
                    "class_type": "VAEDecode",
                    "inputs": {"samples": ["8", 0], "vae": ["1", 2]},
                },
            },
        ),
        library_uuid=LIB,
    )
    filed = (w.plain, w.seed, w.aura, w.two_pass)
    w.v2_id, w.v2_sampler, w.v2_labels = {}, {}, {}
    with hub.transaction() as conn:
        for keys in filed:
            v2 = _core_strip_v2(get_document(hub, keys.structural_hash))
            families = hub.fetchone(
                "SELECT families FROM workflow_variant_family WHERE structural_hash = ?",
                (keys.structural_hash,),
            )[0]
            w.v2_id[keys] = auto_workflow_id(graph_key(v2), families)
            w.v2_labels[keys] = node_labels(v2, rounds=None)
            w.v2_sampler[keys] = w.v2_labels[keys]["5"]
            conn.execute(
                "UPDATE workflow_topology_core SET core_version = ?, core_hash = ? "
                "WHERE topology_hash = ?",
                (_CORE_RULE_V2, graph_key(v2), keys.topology_hash),
            )
        conn.executemany(
            "INSERT INTO workflow_group_attr (workflow_id, name, notes, hidden) "
            "VALUES (?, ?, ?, 0)",
            [
                (w.v2_id[w.plain], "Plain", None),
                (w.v2_id[w.seed], "Seeded", "Seed notes."),
                (w.v2_id[w.aura], "Aura", None),
                (w.v2_id[w.two_pass], "Two pass", None),
            ],
        )
        conn.executemany(
            "INSERT INTO workflow_group_default (workflow_id, address, value) "
            "VALUES (?, ?, ?)",
            [
                (w.v2_id[w.seed], f"core:{w.v2_sampler[w.seed]}/steps", "8"),
                (w.v2_id[w.aura], f"core:{w.v2_sampler[w.aura]}/cfg", "3"),
                # On the patch v3 strips: kept, though the heir's base lacks it.
                (w.v2_id[w.aura], f"core:{w.v2_labels[w.aura]['90']}/shift", "5.0"),
            ],
        )
    assert len({w.v2_id[keys] for keys in filed}) == 4
    w.v3_sampler = core_node_labels(get_document(hub, w.plain.structural_hash))["5"]
    w.aura_slot = topology_node_labels(get_document(hub, w.aura.structural_hash))["90"]
    try:
        yield w
    finally:
        hub.close()


def test_step_10_merges_v2_workflows_and_carries_their_state(step_10):
    w = step_10
    with w.hub.transaction() as conn:
        assert rederive_cores_v3(conn) == 4
        assert not _has_old_cores(conn, "v2")
    merged = workflow_of_variant(w.hub, w.plain.structural_hash)
    assert merged == w.v2_id[w.plain], "v3 leaves the plain core alone"
    assert {
        workflow_of_variant(w.hub, keys.structural_hash) for keys in (w.seed, w.aura)
    } == {merged}
    # The negative: two passes are another workflow still.
    assert workflow_of_variant(w.hub, w.two_pass.structural_hash) == w.v2_id[w.two_pass]

    rows = _step_8_rows(w.hub)
    attrs = {r[0]: r for r in rows["workflow_group_attr"]}
    assert set(attrs) == {merged, w.v2_id[w.two_pass]}
    assert attrs[merged][1] == "Plain"
    for carried in ("Seeded", "Seed notes.", "Aura"):
        assert carried in attrs[merged][2]
    assert sorted(r[1:] for r in rows["workflow_group_default"]) == sorted(
        [
            (f"core:{w.v3_sampler}/cfg", "3"),
            (f"core:{w.v3_sampler}/steps", "8"),
            (f"{w.aura_slot}/shift", "5.0"),
        ]
    )
    assert {r[0] for r in rows["workflow_group_default"]} == {merged}
    assert dict(rows["workflow_id_successor"]) == {
        w.v2_id[w.seed]: merged,
        w.v2_id[w.aura]: merged,
    }
    assert {r[1:3] for r in rows["workflow_core_successor"]} == {
        (w.v2_id[w.seed], merged),
        (w.v2_id[w.aura], merged),
    }

    with w.hub.transaction() as conn:
        assert rederive_cores_v3(conn) == 0
    assert _step_8_rows(w.hub) == rows


def test_step_10_composes_a_v1_recipes_map_onto_v3(step_10):
    """A recipe still on a v1 id reads step 8's v1 -> v2 map: made v1 -> v3."""
    w = step_10
    v2 = w.v2_labels[w.aura]
    with w.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_core_successor (topology_hash, old_workflow_id, "
            "new_workflow_id, label_map) VALUES (?, 'auto:v1', ?, ?)",
            (
                w.aura.topology_hash,
                w.v2_id[w.aura],
                json.dumps(
                    {
                        "s1": v2["5"],
                        "p1": v2["90"],
                        "x": None,
                        "stale": "on-no-core",
                    }
                ),
            ),
        )
        rederive_cores_v3(conn)
    assert _core_successor_map(w.hub, "auto:v1") == {
        "s1": w.v3_sampler,
        "p1": None,  # stripped: the recipe falls back to its stage slot
        "x": None,
        "stale": None,
    }


def test_step_10_a_card_split_by_family_follows_each_old_workflow(step_10):
    """Copilot on #1728: a card's successor row went to its last variant's heir."""
    w = step_10
    runs = [
        record_api_graph(
            w.hub,
            _graph(ckpt=FLUX, loras=(lora,), extra=CORE_V3_TWINS["seed"][1]),
            library_uuid=LIB,
        )
        for lora in ("a.safetensors", "b.safetensors")
    ]
    card = _card(w.hub, runs[0])
    assert _card(w.hub, runs[1]) == card, "a character LoRA does not fork the card"
    first, last = sorted(keys.structural_hash for keys in runs)
    core = graph_key(_core_strip_v2(get_document(w.hub, first)))
    with w.hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_topology_core SET core_version = ?, core_hash = ? "
            "WHERE topology_hash = ?",
            (_CORE_RULE_V2, core, runs[0].topology_hash),
        )
        # The last variant is of another family: in another workflow.
        conn.execute(
            "UPDATE workflow_variant_family SET families = 'qwen' "
            "WHERE structural_hash = ?",
            (last,),
        )
        families = conn.execute(
            "SELECT families FROM workflow_variant_family WHERE structural_hash = ?",
            (first,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO workflow_key_successor (workflow_key, workflow_id) "
            "VALUES (?, ?)",
            (card, auto_workflow_id(core, families)),
        )
        rederive_cores_v3(conn)
    assert workflow_of_variant(w.hub, first) != workflow_of_variant(w.hub, last)
    assert _successor(w.hub, card) == workflow_of_variant(w.hub, first)


def test_step_10_keeps_a_map_an_earlier_run_already_composed(step_10):
    """A re-run (an older build re-cached the topology) must not null v3 labels.

    The advanced sampler is relabelled by v3, so its v3 label is no v2 key.
    """
    w = step_10
    advanced = record_api_graph(
        w.hub,
        _graph(ckpt=FLUX, extra=CORE_V3_TWINS["ksampler-advanced"][1]),
        library_uuid=LIB,
    )
    document = get_document(w.hub, advanced.structural_hash)
    done = core_node_labels(document)["5"]
    assert done not in convert.core_label_maps(document, _core_strip_v2)[0]
    with w.hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_topology_core SET core_version = ?, core_hash = ? "
            "WHERE topology_hash = ?",
            (
                _CORE_RULE_V2,
                graph_key(_core_strip_v2(document)),
                advanced.topology_hash,
            ),
        )
        conn.execute(
            "INSERT INTO workflow_core_successor (topology_hash, old_workflow_id, "
            "new_workflow_id, label_map) VALUES (?, 'auto:v1', 'auto:v3', ?)",
            (advanced.topology_hash, json.dumps({"s1": done})),
        )
        rederive_cores_v3(conn)
    assert _core_successor_map(w.hub, "auto:v1") == {"s1": done}


def test_a_v2_row_an_older_build_writes_is_moved_on_the_next_open(step_10):
    w = step_10
    path = w.hub.path
    with w.hub.transaction() as conn:
        rederive_cores_v3(conn)
        # The older build re-caches the Seed topology on v2.
        conn.execute(
            "UPDATE workflow_topology_core SET core_version = ?, core_hash = ? "
            "WHERE topology_hash = ?",
            (
                _CORE_RULE_V2,
                graph_key(_core_strip_v2(get_document(w.hub, w.seed.structural_hash))),
                w.seed.topology_hash,
            ),
        )
        assert _has_old_cores(conn, "v2")
    w.hub.close()
    w.hub = HubDatabase(path)
    assert workflow_of_variant(w.hub, w.seed.structural_hash) == w.v2_id[w.plain]
    with w.hub.transaction() as conn:
        assert not _has_old_cores(conn, "v2")


def test_the_hub_open_runs_step_10_once(step_10):
    w = step_10
    path = w.hub.path
    with w.hub.transaction() as conn:
        conn.execute("PRAGMA user_version = 9")
    w.hub.close()
    w.hub = HubDatabase(path)
    assert w.hub.fetchone("PRAGMA user_version")[0] == CURRENT_DATA_VERSION
    merged = w.v2_id[w.plain]
    assert workflow_of_variant(w.hub, w.seed.structural_hash) == merged
    rows = _step_8_rows(w.hub)
    w.hub.close()
    w.hub = HubDatabase(path)
    assert _step_8_rows(w.hub) == rows


def test_carrying_one_workflow_twice_onto_an_heir_writes_its_notes_once(tmp_path):
    """#1696: a repeated carry (`keep=True`) appended the same notes again."""
    hub = HubDatabase(str(tmp_path / "hub.db"))
    try:
        with hub.transaction() as conn:
            conn.executemany(
                "INSERT INTO workflow_group_attr (workflow_id, name, notes, hidden) "
                "VALUES (?, ?, ?, 0)",
                [("auto:old", "Old", "Old notes."), ("auto:heir", "Heir", None)],
            )
            for _ in range(2):
                _carry_group_state(conn, "auto:old", "auto:heir", keep=True)
        notes = hub.fetchone(
            "SELECT notes FROM workflow_group_attr WHERE workflow_id = 'auto:heir'"
        )[0]
        assert notes == "Old:\nOld notes."
    finally:
        hub.close()


def test_a_carry_contained_in_longer_notes_is_still_written(tmp_path):
    """Only an exact repeat is dropped: "Also named: Foo" is not "...: Foo bar"."""
    hub = HubDatabase(str(tmp_path / "hub.db"))
    try:
        with hub.transaction() as conn:
            conn.executemany(
                "INSERT INTO workflow_group_attr (workflow_id, name, notes, hidden) "
                "VALUES (?, ?, ?, 0)",
                [
                    ("auto:old", "Foo", None),
                    ("auto:heir", "Heir", "Also named: Foo bar"),
                ],
            )
            _carry_group_state(conn, "auto:old", "auto:heir", keep=True)
        notes = hub.fetchone(
            "SELECT notes FROM workflow_group_attr WHERE workflow_id = 'auto:heir'"
        )[0]
        assert notes == "Also named: Foo bar\n\nAlso named: Foo"
    finally:
        hub.close()


# ── the vault half: a saved recipe keeps its checkpoint ────────────────────


def _quiesce(server) -> None:
    """Take every work finder out of the planner and let the pipeline settle."""
    task_types = list(server.vault._planner_work_finders)
    for task_type in task_types:
        server.vault._planner_work_finders.pop(task_type)
    server.vault._work_planner.detach_finders(task_types)
    runner = server.vault._task_runner
    runner.cancel_pending_tasks()
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        with runner._active_task_lock:
            if not runner._active_tasks:
                return
        time.sleep(0.05)
    raise AssertionError("background work did not settle within 60s")


@pytest.fixture(scope="module")
def run_env():
    tmp = tempfile.TemporaryDirectory()
    config_path = f"{tmp.name}/server-config.json"
    with open(config_path, "w") as handle:
        json.dump({"port": 8000}, handle)
    server = Server(config_path)
    server.__enter__()
    try:
        owner = TestClient(server.api, raise_server_exceptions=True)
        r = owner.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-ownerpass1"},
        )
        assert r.status_code == 200, r.text
        _quiesce(server)
        yield SimpleNamespace(server=server, owner=owner)
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


_OBJECT_INFO = {
    "CheckpointLoaderSimple": {
        "input": {
            "required": {
                "ckpt_name": [["sdxl_a.safetensors", "sdxl_b.safetensors"], {}]
            }
        }
    },
    "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}]}}},
    "EmptyLatentImage": {
        "input": {
            "required": {
                "width": ["INT", {}],
                "height": ["INT", {}],
                "batch_size": ["INT", {}],
            }
        }
    },
    "KSampler": {
        "input": {"required": {"seed": ["INT", {}], "steps": ["INT", {"default": 20}]}}
    },
    "VAEDecode": {"input": {"required": {}}},
    "SaveImage": {"input": {"required": {"filename_prefix": ["STRING", {}]}}},
}


def test_a_recipe_saved_on_a_checkpoint_b_card_still_runs_on_b(run_env, monkeypatch):
    server = run_env.server
    library = server.vault.library_uuid
    second_a = _graph(ckpt="sdxl_a.safetensors")
    second_a["5"]["inputs"]["steps"] = 21
    # Two distinct runs of A to B's one: A is the default recipe's checkpoint.
    a_runs = [
        record_api_graph(server.hub, _graph(ckpt="sdxl_a.safetensors"), library),
        record_api_graph(server.hub, second_a, library),
    ]
    b_run = record_api_graph(server.hub, _graph(ckpt="sdxl_b.safetensors"), library)
    b_key = _card(server.hub, b_run)
    assert b_key != _card(server.hub, a_runs[0])
    sampler, core_sampler = _labels(server.hub, b_run, "5")
    with server.hub.transaction() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO model (file_kind, filename, sha256, provenance) "
            "VALUES ('checkpoint', ?, ?, 'scanned')",
            [("sdxl_a.safetensors", "a" * 64), ("sdxl_b.safetensors", "b" * 64)],
        )
        convert_card_state(conn)

    def seed(session):
        session.exec(delete(SavedRecipe))
        session.exec(delete(Picture))
        # Card A has the pictures, so it is the workflow's base card and its
        # checkpoint the default recipe's.
        for index, keys in enumerate([*a_runs, a_runs[0], b_run]):
            session.add(
                Picture(
                    file_path=f"run_{index}.png",
                    deleted=False,
                    created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                    score=5,
                    workflow_topology_hash=keys.topology_hash,
                    workflow_structural_hash=keys.structural_hash,
                    workflow_instance_hash=keys.instance_hash,
                    workflow_hash_version="v1",
                )
            )
        recipe = SavedRecipe(
            name="on b",
            workflow_key=b_key,
            prompt="a cat",
            overrides=json.dumps({f"{sampler}/steps": 12}),
        )
        session.add(recipe)
        session.commit()
        return recipe.id

    recipe_id = server.vault.db.run_task(seed, priority=DBPriority.IMMEDIATE)

    finder = MissingSavedRecipeWorkflowFinder(vault=server.vault)
    task = finder.find_task()
    task.result = task._run_task()
    finder.on_task_complete(task, None)
    assert finder.find_task() is None, "a converted recipe was handed out again"

    stored = server.vault.db.run_immediate_read_task(
        lambda session: session.exec(
            select(SavedRecipe).where(SavedRecipe.id == recipe_id)
        ).one()
    )
    core = _labels(server.hub, b_run, "1")[1]
    assert stored.workflow_id.startswith("auto:")
    assert json.loads(stored.overrides) == {f"core:{core_sampler}/steps": 12}
    assert json.loads(stored.models) == [
        {"address": f"core:{core}/ckpt_name", "filename": "sdxl_b.safetensors"}
    ]

    submitted = []
    monkeypatch.setattr(
        workflows_routes,
        "_submit_comfyui_prompt",
        lambda base_url, graph, client_id=None: (
            submitted.append(graph) or {"prompt_id": "p1"}
        ),
    )
    monkeypatch.setattr(
        workflows_routes, "_process_comfyui_outputs", lambda *a, **k: None
    )
    monkeypatch.setattr(
        workflows_routes,
        "_read_object_info",
        lambda url: (json.loads(json.dumps(_OBJECT_INFO)), None),
    )
    r = run_env.owner.post(f"{API}/workflows/run", json={"saved_recipe_id": recipe_id})
    assert r.status_code == 200, r.text
    (group,) = r.json()["groups"]
    assert group["workflow_id"] == stored.workflow_id
    (graph,) = submitted
    assert graph["1"]["inputs"]["ckpt_name"] == "sdxl_b.safetensors"
    assert graph["5"]["inputs"]["steps"] == 12


def test_a_saved_recipe_runs_the_graph_it_was_saved_on(run_env, monkeypatch):
    """Not its workflow's base graph: the one with the face detailer is the
    base (most stages), and a recipe saved on the plain one runs plain.

    The plain card has no successor row, as a card filed after the cut-over
    has none, so the conversion files its recipe by the card's own topology.
    """
    server = run_env.server
    library = server.vault.library_uuid
    detailed = record_api_graph(
        server.hub, _graph(ckpt="sdxl_a.safetensors", face_detailer=True), library
    )
    with server.hub.transaction() as conn:
        convert_card_state(conn)
    plain = record_api_graph(server.hub, _graph(ckpt="sdxl_a.safetensors"), library)
    plain_key = _card(server.hub, plain)
    with server.hub.transaction() as conn:
        conn.execute(
            "DELETE FROM workflow_key_successor WHERE workflow_key = ?", (plain_key,)
        )
    sampler, _core = _labels(server.hub, plain, "5")

    def seed(session):
        session.exec(delete(SavedRecipe))
        session.exec(delete(Picture))
        for index, keys in enumerate([detailed, detailed, plain]):
            session.add(
                Picture(
                    file_path=f"own_{index}.png",
                    deleted=False,
                    created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                    score=5,
                    workflow_topology_hash=keys.topology_hash,
                    workflow_structural_hash=keys.structural_hash,
                    workflow_instance_hash=keys.instance_hash,
                    workflow_hash_version="v1",
                )
            )
        recipe = SavedRecipe(
            name="plain",
            workflow_key=plain_key,
            prompt="a cat",
            overrides=json.dumps({f"{sampler}/steps": 13}),
        )
        session.add(recipe)
        session.commit()
        return recipe.id

    recipe_id = server.vault.db.run_task(seed, priority=DBPriority.IMMEDIATE)
    finder = MissingSavedRecipeWorkflowFinder(vault=server.vault)
    task = finder.find_task()
    task.result = task._run_task()
    finder.on_task_complete(task, None)
    assert task.result["deferred"] == []
    stored = server.vault.db.run_immediate_read_task(
        lambda session: session.exec(
            select(SavedRecipe).where(SavedRecipe.id == recipe_id)
        ).one()
    )
    assert stored.workflow_id == _auto_of(server.hub, plain)
    assert _auto_of(server.hub, plain) == _auto_of(server.hub, detailed)

    submitted = []
    monkeypatch.setattr(
        workflows_routes,
        "_submit_comfyui_prompt",
        lambda base_url, graph, client_id=None: (
            submitted.append(graph) or {"prompt_id": "p1"}
        ),
    )
    monkeypatch.setattr(
        workflows_routes, "_process_comfyui_outputs", lambda *a, **k: None
    )
    monkeypatch.setattr(
        workflows_routes,
        "_read_object_info",
        lambda url: (json.loads(json.dumps(_OBJECT_INFO)), None),
    )
    r = run_env.owner.post(f"{API}/workflows/run", json={"saved_recipe_id": recipe_id})
    assert r.status_code == 200, r.text
    (graph,) = submitted
    assert "31" not in graph, "the recipe ran on the base graph's face detailer"
    assert graph["5"]["inputs"]["steps"] == 13


def test_a_recipe_whose_card_became_no_workflow_is_left_and_deferred(run_env):
    server = run_env.server

    def seed(session):
        session.exec(delete(SavedRecipe))
        recipe = SavedRecipe(
            name="orphan", workflow_key="0" * 64, prompt="x", overrides='{"a/b": 1}'
        )
        session.add(recipe)
        session.commit()
        return recipe.id

    recipe_id = server.vault.db.run_task(seed, priority=DBPriority.IMMEDIATE)
    finder = MissingSavedRecipeWorkflowFinder(vault=server.vault)
    task = finder.find_task()
    task.result = task._run_task()
    finder.on_task_complete(task, None)
    assert task.result["deferred"] == [recipe_id]
    assert finder.find_task() is None, "an orphan is handed out every sweep"
    stored = server.vault.db.run_immediate_read_task(
        lambda session: session.exec(
            select(SavedRecipe).where(SavedRecipe.id == recipe_id)
        ).one()
    )
    assert (stored.workflow_id, stored.overrides, stored.models) == (
        None,
        '{"a/b": 1}',
        None,
    )


def test_a_busy_vault_leaves_its_recipes_eligible(run_env):
    """A lock surfacing through SQLAlchemy is transient, like a busy hub's
    sqlite3 one: the batch is handed out again rather than deferred for the
    session."""
    server = run_env.server

    def seed(session):
        session.exec(delete(SavedRecipe))
        recipe = SavedRecipe(name="busy", workflow_key="0" * 64, prompt="x")
        session.add(recipe)
        session.commit()
        return recipe.id

    recipe_id = server.vault.db.run_task(seed, priority=DBPriority.IMMEDIATE)
    finder = MissingSavedRecipeWorkflowFinder(vault=server.vault)
    task = finder.find_task()
    finder.on_task_complete(
        task,
        SQLAlchemyOperationalError(
            "SELECT", {}, sqlite3.OperationalError("database is locked")
        ),
    )
    again = finder.find_task()
    assert again is not None and again.params["recipe_ids"] == [recipe_id]


def test_a_recipe_on_a_file_only_card_follows_its_file_into_step_7(
    run_env, tmp_path, monkeypatch
):
    """Nothing the owner saved changes where it lists across the upgrade.

    A recipe on a file-only card (#1466) named `auto:<topology hash>` once
    step 5 converted it, or still names only the card; step 7 makes the file a
    manual workflow and retires that id, and the conversion re-files both
    recipes there: they list on the manual workflow and run its document.
    """
    server = run_env.server
    hub = server.hub
    folder = tmp_path / "user"
    folder.mkdir()
    (folder / "editor.json").write_text(json.dumps(_EDITOR), encoding="utf-8")
    (folder / "editor.json.api").write_text(
        json.dumps(
            {
                "converted_from": comfyui_routes._editor_digest(_EDITOR),
                "prompt": _EDITOR_AS_API,
            }
        ),
        encoding="utf-8",
    )
    topology = record_ui_graph(hub, _EDITOR)
    key = workflow_cards.topology_only_key(topology)
    retired = f"auto:{topology}"
    with hub.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_file (workflow_name, topology_hash, "
            "structural_hash, workflow_key) VALUES ('editor.json', ?, NULL, ?)",
            (topology, key),
        )
        convert_card_state(conn)
        assert convert.adopt_workflow_files(conn, str(folder)) == 1
    manual = hub.fetchone(
        "SELECT successor_id FROM workflow_id_successor WHERE workflow_id = ?",
        (retired,),
    )[0]
    assert manual.startswith("manual:")

    def seed(session):
        session.exec(delete(SavedRecipe))
        converted = SavedRecipe(
            name="converted", workflow_key=key, workflow_id=retired, prompt="x"
        )
        pending = SavedRecipe(name="pending", workflow_key=key, prompt="x")
        session.add(converted)
        session.add(pending)
        session.commit()
        return converted.id, pending.id

    ids = server.vault.db.run_task(seed, priority=DBPriority.IMMEDIATE)
    finder = MissingSavedRecipeWorkflowFinder(vault=server.vault)
    task = finder.find_task()
    assert sorted(task.params["recipe_ids"]) == sorted(ids)
    task.result = task._run_task()
    finder.on_task_complete(task, None)
    assert finder.find_task() is None, "a re-filed recipe was handed out again"
    stored = server.vault.db.run_immediate_read_task(
        lambda session: session.exec(select(SavedRecipe)).all()
    )
    # A manual workflow is its own card, as `POST /recipes` stores one.
    assert {(row.workflow_id, row.workflow_key) for row in stored} == {(manual, manual)}

    listed = run_env.owner.get(f"{API}/recipes", params={"workflow_id": manual})
    assert sorted(row["id"] for row in listed.json()) == sorted(ids)
    unfiled = run_env.owner.get(f"{API}/recipes", params={"unfiled": "true"})
    assert not set(ids) & {row["id"] for row in unfiled.json()}

    monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "refused")
    )
    for recipe_id in ids:
        r = run_env.owner.post(
            f"{API}/workflows/run/preflight", json={"saved_recipe_id": recipe_id}
        )
        assert r.status_code == 200, r.text
        (group,) = r.json()["groups"]
        assert (group["workflow_id"], group["source"]) == (manual, "file"), group

    delete_manual_workflow(hub, manual)
    server.vault.db.run_task(
        lambda session: (session.exec(delete(SavedRecipe)), session.commit()),
        priority=DBPriority.IMMEDIATE,
    )


def test_a_recipe_on_a_v1_workflow_is_refiled_onto_v2_with_its_addresses(run_env):
    """Data step 8 retires `auto:<v1>`; the conversion moves a recipe naming it,
    and rewrites its `core:` overrides and model pins through the label map.

    The v1 workflow splits by family (a Qwen card on the same topology), and
    each recipe follows its own card, whichever successor is the primary.
    """
    server = run_env.server
    hub = server.hub
    library = server.vault.library_uuid
    plain = record_api_graph(hub, _graph(hires=True), library)
    orphan_graph = _graph(hires=True, extra=CORE_V2_TWINS["orphan-encoder"][1])
    orphan = record_api_graph(hub, orphan_graph, library)
    qwen = record_api_graph(
        hub,
        _graph(hires=True, ckpt=QWEN, extra=CORE_V2_TWINS["orphan-encoder"][1]),
        library,
    )
    assert qwen.topology_hash == orphan.topology_hash
    v1 = _back_to_v1(hub, plain, orphan)
    old_id, old_labels = v1[orphan.topology_hash]
    with hub.transaction() as conn:
        rederive_cores(conn)
    new_of = {
        keys.structural_hash: workflow_of_variant(hub, keys.structural_hash)
        for keys in (orphan, qwen)
    }
    assert old_id not in new_of.values()
    assert len(set(new_of.values())) == 2, "the families did not split"
    new_labels = core_node_labels(get_document(hub, orphan.structural_hash))
    old_steps = f"core:{old_labels['5']}/steps"
    old_ckpt = f"core:{old_labels['1']}/ckpt_name"

    def seed(session):
        session.exec(delete(SavedRecipe))
        ids = {}
        for keys in (orphan, qwen):
            recipe = SavedRecipe(
                name="v1",
                workflow_key=_card(hub, keys),
                workflow_id=old_id,
                prompt="x",
                overrides=json.dumps({old_steps: 12, "lora:" + "5" * 64: 0.7}),
                models=json.dumps([{"address": old_ckpt, "filename": "b.safetensors"}]),
            )
            session.add(recipe)
            session.flush()
            ids[recipe.id] = keys.structural_hash
        session.commit()
        return ids

    ids = server.vault.db.run_task(seed, priority=DBPriority.IMMEDIATE)
    finder = MissingSavedRecipeWorkflowFinder(vault=server.vault)
    task = finder.find_task()
    assert sorted(task.params["recipe_ids"]) == sorted(ids)
    task.result = task._run_task()
    finder.on_task_complete(task, None)
    assert finder.find_task() is None, "a re-filed recipe was handed out again"
    stored = server.vault.db.run_immediate_read_task(
        lambda session: session.exec(select(SavedRecipe)).all()
    )
    assert {row.id: row.workflow_id for row in stored} == {
        recipe_id: new_of[structural] for recipe_id, structural in ids.items()
    }
    for row in stored:
        assert json.loads(row.overrides) == {
            f"core:{new_labels['5']}/steps": 12,
            "lora:" + "5" * 64: 0.7,
        }
        assert json.loads(row.models) == [
            {
                "address": f"core:{new_labels['1']}/ckpt_name",
                "filename": "b.safetensors",
            }
        ]
    server.vault.db.run_task(
        lambda session: (session.exec(delete(SavedRecipe)), session.commit()),
        priority=DBPriority.IMMEDIATE,
    )


def test_a_refiled_recipe_keeps_a_stage_address_on_this_librarys_base(run_env):
    """#1738: the conversion reads the base this library runs, not the hub's.

    The twin with a LoRA loader is the base without counts; it
    has no picture here, so with them the orphan-encoder graph is. Only on
    that base does an address on the node v2 took off the core survive as a
    stage slot rather than being kept as the dead `core:` address it was.
    """
    server = run_env.server
    hub = server.hub
    library = server.vault.library_uuid
    extra = CORE_V2_TWINS["orphan-encoder"][1]
    pictured = record_api_graph(hub, _graph(hires=True, extra=extra), library)
    richer = record_api_graph(
        hub, _graph(hires=True, loras=("x.safetensors",), extra=extra), library
    )
    v1 = _back_to_v1(hub, pictured, richer)
    old_id, old_labels = v1[pictured.topology_hash]
    with hub.transaction() as conn:
        rederive_cores(conn)
    workflow_id = workflow_of_variant(hub, pictured.structural_hash)
    assert workflow_of_variant(hub, richer.structural_hash) == workflow_id
    hub_base = next(
        w.base_topology for w in workflow_index(hub) if w.workflow_id == workflow_id
    )
    assert hub_base == richer.topology_hash
    old_text = f"core:{old_labels['97']}/text"
    stage_slot, core_label = _labels(hub, pictured, "97")
    assert core_label is None, "v2 kept the orphan encoder on the core"

    def seed(session):
        session.exec(delete(SavedRecipe))
        session.exec(delete(Picture))
        session.add(
            Picture(
                file_path="pictured.png",
                deleted=False,
                created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                score=5,
                workflow_topology_hash=pictured.topology_hash,
                workflow_structural_hash=pictured.structural_hash,
                workflow_instance_hash=pictured.instance_hash,
                workflow_hash_version="v1",
            )
        )
        recipe = SavedRecipe(
            name="v1 stage",
            workflow_key=_card(hub, pictured),
            workflow_id=old_id,
            prompt="x",
            overrides=json.dumps({old_text: "right"}),
        )
        session.add(recipe)
        session.commit()
        return recipe.id

    recipe_id = server.vault.db.run_task(seed, priority=DBPriority.IMMEDIATE)
    finder = MissingSavedRecipeWorkflowFinder(vault=server.vault)
    task = finder.find_task()
    task.result = task._run_task()
    finder.on_task_complete(task, None)
    stored = server.vault.db.run_immediate_read_task(
        lambda session: session.exec(
            select(SavedRecipe).where(SavedRecipe.id == recipe_id)
        ).one()
    )
    assert stored.workflow_id == workflow_id
    assert json.loads(stored.overrides) == {f"{stage_slot}/text": "right"}
    server.vault.db.run_task(
        lambda session: (
            session.exec(delete(SavedRecipe)),
            session.exec(delete(Picture)),
            session.commit(),
        ),
        priority=DBPriority.IMMEDIATE,
    )


def test_an_unknown_family_the_shelf_learns_moves_with_its_state(run_env):
    """Unknown family -> the owner sets the base model on the shelf -> the
    variants join the known family's workflow, carrying name, defaults and
    pins, and a recipe saved on the old workflow follows them."""
    server = run_env.server
    hub = server.hub
    library = server.vault.library_uuid
    known = record_api_graph(hub, _graph(ckpt=QWEN, upscale=True), library)
    unknown = record_api_graph(
        hub, _graph(ckpt="house-finetune-v7.safetensors", upscale=True), library
    )
    qwen_id = workflow_of_variant(hub, known.structural_hash)
    unknown_id = workflow_of_variant(hub, unknown.structural_hash)
    assert unknown_id != qwen_id
    steps = f"core:{_labels(hub, unknown, '5')[1]}/steps"
    core = hub.fetchone(
        "SELECT core_hash FROM workflow_topology_core WHERE topology_hash = ?",
        (unknown.topology_hash,),
    )[0]
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_group (workflow_id, kind, core_hash) "
            "VALUES (?, 'auto', ?)",
            (unknown_id, core),
        )
        conn.executemany(
            "INSERT INTO workflow_group_attr (workflow_id, name, notes, hidden) "
            "VALUES (?, ?, NULL, 0)",
            [(qwen_id, "Qwen upscale"), (unknown_id, "House finetune")],
        )
        conn.execute(
            "INSERT INTO workflow_group_default (workflow_id, address, value) "
            "VALUES (?, ?, '9')",
            (unknown_id, steps),
        )
        conn.execute(
            "INSERT INTO workflow_group_pins (workflow_id, pins) VALUES (?, ?)",
            (unknown_id, json.dumps([steps])),
        )
        # A stale retired row for the heir: it is live, so it must go.
        conn.execute(
            "INSERT INTO workflow_id_successor (workflow_id, successor_id) "
            "VALUES (?, ?)",
            (qwen_id, "auto:" + "e" * 64),
        )

    def seed(session):
        session.exec(delete(SavedRecipe))
        recipe = SavedRecipe(
            name="finetune",
            workflow_key=_card(hub, unknown),
            workflow_id=unknown_id,
            prompt="x",
            overrides=json.dumps({steps: 11}),
        )
        session.add(recipe)
        session.commit()
        return recipe.id

    recipe_id = server.vault.db.run_task(seed, priority=DBPriority.IMMEDIATE)
    backfill = WorkflowCardBackfillFinder(hub=hub)
    # One pass at start-up, for a shelf change a previous session never passed
    # over; here there is none, so it moves nothing.
    started = backfill.find_task()
    assert isinstance(started, FamilyReidentifyTask)
    assert started._run_task()["moved"] == 0
    assert backfill.find_task() is None, "nothing on the shelf changed yet"
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, filename, sha256, provenance, base_model, "
            "base_model_canonical, base_model_source) VALUES ('checkpoint', "
            "'house-finetune-v7.safetensors', ?, 'scanned', 'Qwen-Image', "
            "'Qwen-Image', 'user')",
            ("7" * 64,),
        )
    task = backfill.find_task()
    assert isinstance(task, FamilyReidentifyTask)
    task.result = task._run_task()
    assert task.result["moved"] == 1
    assert backfill.find_task() is None

    assert workflow_of_variant(hub, unknown.structural_hash) == qwen_id
    assert (
        hub.fetchone(
            "SELECT 1 FROM workflow_id_successor WHERE workflow_id = ?", (qwen_id,)
        )
        is None
    )
    attr = hub.fetchone(
        "SELECT name, notes FROM workflow_group_attr WHERE workflow_id = ?", (qwen_id,)
    )
    assert attr["name"] == "Qwen upscale" and "House finetune" in attr["notes"]
    # The heir's row names its core, not its id's digest (#1692).
    assert [tuple(r) for r in hub.fetchall("SELECT * FROM workflow_group")] == [
        (qwen_id, "auto", core)
    ]
    assert (
        hub.fetchone(
            "SELECT value FROM workflow_group_default WHERE workflow_id = ? AND address = ?",
            (qwen_id, steps),
        )[0]
        == "9"
    )
    assert json.loads(
        hub.fetchone(
            "SELECT pins FROM workflow_group_pins WHERE workflow_id = ?", (qwen_id,)
        )[0]
    ) == [steps]
    assert (
        hub.fetchone(
            "SELECT 1 FROM workflow_group_attr WHERE workflow_id = ?", (unknown_id,)
        )
        is None
    )

    finder = MissingSavedRecipeWorkflowFinder(vault=server.vault)
    convert_task = finder.find_task()
    assert convert_task.params["recipe_ids"] == [recipe_id]
    convert_task.result = convert_task._run_task()
    finder.on_task_complete(convert_task, None)
    (stored,) = server.vault.db.run_immediate_read_task(
        lambda session: session.exec(select(SavedRecipe)).all()
    )
    assert stored.workflow_id == qwen_id
    assert json.loads(stored.overrides) == {steps: 11}
    server.vault.db.run_task(
        lambda session: (session.exec(delete(SavedRecipe)), session.commit()),
        priority=DBPriority.IMMEDIATE,
    )


def _share_an_unknown_workflow(hub, library, moving_ckpt, staying_ckpt):
    """Two variants of one topology filed under one unknown family's workflow.

    The staying one's frozen family is the moving one's, so they share a
    workflow; its own checkpoint is a different unknown, so a shelf that
    learns *moving_ckpt* moves one variant and leaves the other.
    """
    moving = record_api_graph(hub, _graph(ckpt=moving_ckpt, upscale=True), library)
    staying = record_api_graph(hub, _graph(ckpt=staying_ckpt, upscale=True), library)
    assert moving.topology_hash == staying.topology_hash
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_variant_family SET families = (SELECT families FROM "
            "workflow_variant_family WHERE structural_hash = ?) "
            "WHERE structural_hash = ?",
            (moving.structural_hash, staying.structural_hash),
        )
    old_id = workflow_of_variant(hub, moving.structural_hash)
    assert workflow_of_variant(hub, staying.structural_hash) == old_id
    return moving, staying, old_id


def _identify(hub, filename, sha):
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, filename, sha256, provenance, base_model, "
            "base_model_canonical, base_model_source) VALUES ('checkpoint', ?, ?, "
            "'scanned', 'Qwen-Image', 'Qwen-Image', 'user')",
            (filename, sha),
        )


def test_a_recipe_follows_its_card_out_of_a_workflow_that_lives_on(run_env):
    """#1689: a family pass that leaves some variants unknown keeps the old
    workflow; a recipe on the card that moved follows it, and a recipe on the
    card that stayed is left where it is."""
    server = run_env.server
    hub = server.hub
    moving, staying, old_id = _share_an_unknown_workflow(
        hub,
        server.vault.library_uuid,
        "house-finetune-v11.safetensors",
        "house-finetune-v12.safetensors",
    )
    _identify(hub, "house-finetune-v11.safetensors", "1689" + "d" * 60)
    result = convert.reidentify_families(hub)
    new_id = workflow_of_variant(hub, moving.structural_hash)
    assert new_id != old_id
    assert workflow_of_variant(hub, staying.structural_hash) == old_id
    assert old_id in result["keys"] and old_id not in result["renamed"]
    assert {
        tuple(row)
        for row in hub.fetchall(
            "SELECT workflow_key, old_workflow_id, new_workflow_id "
            "FROM workflow_card_move WHERE old_workflow_id = ?",
            (old_id,),
        )
    } == {(_card(hub, moving), old_id, new_id)}, "only the card that moved"
    # `workflow_core_successor` is keyed by (topology, new workflow), so an
    # earlier family of this topology that moved to the same workflow keeps
    # this move off it: the move row alone must carry the recipe.
    with hub.transaction() as conn:
        conn.execute(
            "DELETE FROM workflow_core_successor WHERE old_workflow_id = ?", (old_id,)
        )

    def seed(session):
        session.exec(delete(SavedRecipe))
        ids = []
        for keys in (moving, staying):
            recipe = SavedRecipe(
                name="partial",
                workflow_key=_card(hub, keys),
                workflow_id=old_id,
                prompt="x",
            )
            session.add(recipe)
            session.flush()
            ids.append(recipe.id)
        session.commit()
        return ids

    moved_id, stayed_id = server.vault.db.run_task(seed, priority=DBPriority.IMMEDIATE)
    try:
        finder = MissingSavedRecipeWorkflowFinder(vault=server.vault)
        task = finder.find_task()
        # The recipe whose card stayed names a live workflow it is in: not
        # handed out at all.
        assert task.params["recipe_ids"] == [moved_id]
        task.result = task._run_task()
        assert task.result == {"converted": 1, "deferred": []}
        finder.on_task_complete(task, None)
        assert finder.find_task() is None, "handed out again"
        stored = server.vault.db.run_immediate_read_task(
            lambda session: session.exec(select(SavedRecipe)).all()
        )
        assert {row.id: row.workflow_id for row in stored} == {
            moved_id: new_id,
            stayed_id: old_id,
        }
    finally:
        server.vault.db.run_task(
            lambda session: (session.exec(delete(SavedRecipe)), session.commit()),
            priority=DBPriority.IMMEDIATE,
        )


def test_a_moved_recipe_goes_where_the_move_says_not_where_a_variant_is(
    run_env,
):
    """#1689: a card's variants may sit in several workflows, so the
    conversion re-files on the recorded target, never on its first variant's
    workflow (which here is the very workflow the recipe names)."""
    server = run_env.server
    hub = server.hub
    keys = record_api_graph(
        hub,
        _graph(ckpt="house-finetune-v15.safetensors", upscale=True),
        server.vault.library_uuid,
    )
    here = workflow_of_variant(hub, keys.structural_hash)
    target = "auto:" + "9" * 64
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_card_move (workflow_key, old_workflow_id, "
            "new_workflow_id) VALUES (?, ?, ?)",
            (_card(hub, keys), here, target),
        )

    def seed(session):
        session.exec(delete(SavedRecipe))
        recipe = SavedRecipe(
            name="target", workflow_key=_card(hub, keys), workflow_id=here, prompt="x"
        )
        session.add(recipe)
        session.commit()
        return recipe.id

    recipe_id = server.vault.db.run_task(seed, priority=DBPriority.IMMEDIATE)
    try:
        finder = MissingSavedRecipeWorkflowFinder(vault=server.vault)
        task = finder.find_task()
        assert task.params["recipe_ids"] == [recipe_id]
        task.result = task._run_task()
        assert task.result == {"converted": 1, "deferred": []}
        (stored,) = server.vault.db.run_immediate_read_task(
            lambda session: session.exec(select(SavedRecipe)).all()
        )
        assert stored.workflow_id == target
    finally:
        with hub.transaction() as conn:
            conn.execute(
                "DELETE FROM workflow_card_move WHERE old_workflow_id = ?", (here,)
            )
        server.vault.db.run_task(
            lambda session: (session.exec(delete(SavedRecipe)), session.commit()),
            priority=DBPriority.IMMEDIATE,
        )


def test_a_stale_key_version_variant_does_not_keep_a_workflow_alive(run_env):
    """#1689: a variant row on an old key version is no variant the index
    lists, so the family pass retires the workflow it alone was left in."""
    server = run_env.server
    hub = server.hub
    moving, staying, old_id = _share_an_unknown_workflow(
        hub,
        server.vault.library_uuid,
        "house-finetune-v13.safetensors",
        "house-finetune-v14.safetensors",
    )
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_variant SET key_version = 'stale' "
            "WHERE structural_hash = ?",
            (staying.structural_hash,),
        )
    try:
        _identify(hub, "house-finetune-v13.safetensors", "1689" + "e" * 60)
        result = convert.reidentify_families(hub)
        assert result["renamed"].get(old_id) == workflow_of_variant(
            hub, moving.structural_hash
        )
    finally:
        # A stale row is a variant the backfill re-keys: put it back, or the
        # module's later finders find work that is not theirs.
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE workflow_variant SET key_version = ? WHERE structural_hash = ?",
                (WORKFLOW_KEY_VERSION, staying.structural_hash),
            )


def test_a_failed_family_pass_waits_for_the_shelf_to_change(run_env):
    """A crash must not hand the same pass out on every sweep forever.

    A cancelled or locked-out pass learned nothing and is asked again; any
    other failure keeps the signature it was handed out under, so the finder
    goes quiet until the shelf's base models change.
    """
    hub = run_env.server.hub
    backfill = WorkflowCardBackfillFinder(hub=hub)
    # The module's earlier tests leave unknown families filed, so a start-up
    # pass may be due; it runs and is reported first.
    started = backfill.find_task()
    if started is not None:
        started.result = started._run_task()
        backfill.on_task_complete(started, None)
    assert backfill.find_task() is None
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, filename, sha256, provenance, base_model, "
            "base_model_canonical, base_model_source) VALUES ('checkpoint', "
            "'quiet-after-crash.safetensors', ?, 'scanned', 'SDXL', 'SDXL', 'user')",
            ("8" * 64,),
        )
    task = backfill.find_task()
    assert isinstance(task, FamilyReidentifyTask)
    backfill.on_task_complete(task, RuntimeError("the worker died"))
    assert backfill.find_task() is None, "a failed pass was handed out again"

    with hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET base_model = 'SD 1.5', base_model_canonical = 'SD 1.5' "
            "WHERE sha256 = ?",
            ("8" * 64,),
        )
    task = backfill.find_task()
    assert isinstance(task, FamilyReidentifyTask), "a shelf change re-arms it"
    backfill.on_task_complete(task, sqlite3.OperationalError("database is locked"))
    assert isinstance(backfill.find_task(), FamilyReidentifyTask), (
        "a locked-out pass is asked again"
    )
    backfill.on_task_complete(backfill.find_task() or task, None)


def test_a_recipe_on_a_workflow_live_again_is_left_where_it_is(run_env):
    """The vault's conversion does not move a recipe onto the id it names."""
    server = run_env.server
    hub = server.hub
    keys = record_api_graph(
        hub,
        _graph(ckpt="house-finetune-v9.safetensors", hires=True),
        server.vault.library_uuid,
    )
    live = workflow_of_variant(hub, keys.structural_hash)
    with hub.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_id_successor (workflow_id, successor_id) "
            "VALUES (?, ?)",
            (live, "auto:" + "f" * 64),
        )
        conn.execute(
            "INSERT OR IGNORE INTO workflow_core_successor (topology_hash, "
            "old_workflow_id, new_workflow_id, label_map) VALUES (?, ?, ?, '{}')",
            (keys.topology_hash, live, "auto:" + "f" * 64),
        )

    def seed(session):
        session.exec(delete(SavedRecipe))
        recipe = SavedRecipe(
            name="live", workflow_key=_card(hub, keys), workflow_id=live, prompt="x"
        )
        session.add(recipe)
        session.commit()
        return recipe.id

    recipe_id = server.vault.db.run_task(seed, priority=DBPriority.IMMEDIATE)
    try:
        finder = MissingSavedRecipeWorkflowFinder(vault=server.vault)
        task = finder.find_task()
        assert task.params["recipe_ids"] == [recipe_id]
        task.result = task._run_task()
        assert task.result == {"converted": 0, "deferred": [recipe_id]}
        finder.on_task_complete(task, None)
        assert finder.find_task() is None, "handed out again"
        (stored,) = server.vault.db.run_immediate_read_task(
            lambda session: session.exec(select(SavedRecipe)).all()
        )
        assert stored.workflow_id == live
    finally:
        with hub.transaction() as conn:
            conn.execute(
                "DELETE FROM workflow_id_successor WHERE workflow_id = ?", (live,)
            )
        server.vault.db.run_task(
            lambda session: (session.exec(delete(SavedRecipe)), session.commit()),
            priority=DBPriority.IMMEDIATE,
        )


def _drop_twin_recipe(session) -> None:
    session.exec(delete(SavedRecipe).where(SavedRecipe.name == "twin-kept"))
    session.commit()


def test_an_adopted_file_with_no_pictures_lists_once_as_its_manual_workflow(
    run_env, tmp_path
):
    """Step 7 leaves an adopted file's rows, so its card still reads as an
    automatic workflow (#1720). With no picture that is the manual workflow's
    twin and the grid leaves it out; with pictures it is a real automatic
    workflow beside the manual one, and both list."""
    server = run_env.server
    hub = server.hub
    library = server.vault.library_uuid
    folder = tmp_path / "user"
    folder.mkdir()
    empty = _graph(ckpt="twin-empty.safetensors", upscale=True, preview=True)
    used = _graph(ckpt="twin-used.safetensors", face_detailer=True, preview=True)
    keys = {}
    for name, graph in (("twin-empty.json", empty), ("twin-used.json", used)):
        (folder / name).write_text(json.dumps(graph), encoding="utf-8")
        keys[name] = record_api_graph(hub, graph, library)
    with hub.transaction() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO workflow_file (workflow_name, topology_hash, "
            "structural_hash, workflow_key) VALUES (?, ?, ?, ?)",
            [
                (name, k.topology_hash, k.structural_hash, _card(hub, k))
                for name, k in keys.items()
            ],
        )
        assert convert.adopt_workflow_files(conn, str(folder)) == 2
    manual = {
        row[0]: row[1]
        for row in hub.fetchall(
            "SELECT remote_path, workflow_name FROM workflow_origin "
            "WHERE origin = 'file' AND remote_path LIKE 'twin-%'"
        )
    }
    auto = {
        name: workflow_of_variant(hub, k.structural_hash) for name, k in keys.items()
    }
    assert all(a.startswith("auto:") for a in auto.values())
    used_keys = keys["twin-used.json"]
    # A variant of the twin's card in another family is another automatic
    # workflow, and no file of it was adopted: it has no twin and lists.
    sibling = record_api_graph(
        hub, _graph(ckpt="twin-sibling.safetensors", upscale=True, preview=True)
    )
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_variant SET workflow_key = ? WHERE structural_hash = ?",
            (_card(hub, keys["twin-empty.json"]), sibling.structural_hash),
        )
        conn.execute(
            "INSERT OR REPLACE INTO workflow_variant_family (structural_hash, "
            "families) VALUES (?, 'test-other-family')",
            (sibling.structural_hash,),
        )
    other_family = workflow_of_variant(hub, sibling.structural_hash)
    assert other_family.startswith("auto:")
    assert other_family != auto["twin-empty.json"]

    def seed(session):
        session.add(
            Picture(
                file_path="twin_used.png",
                deleted=False,
                created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                workflow_topology_hash=used_keys.topology_hash,
                workflow_structural_hash=used_keys.structural_hash,
                workflow_instance_hash=used_keys.instance_hash,
                workflow_hash_version="v1",
            )
        )
        session.commit()

    server.vault.db.run_task(seed, priority=DBPriority.IMMEDIATE)
    try:
        r = run_env.owner.get(
            f"{API}/workflows",
            params={"include_hidden": "true", "include_one_offs": "true"},
        )
        assert r.status_code == 200, r.text
        listed = {card["id"] for card in r.json()["cards"]}
        assert manual["twin-empty.json"] in listed
        assert auto["twin-empty.json"] not in listed, "the picture-less twin lists"
        assert {manual["twin-used.json"], auto["twin-used.json"]} <= listed
        assert other_family in listed, "a sibling family's workflow was hidden"
        # A saved recipe on the twin keeps it listed, so the recipe is reachable.
        twin = auto["twin-empty.json"]

        def save_recipe(session):
            session.add(
                SavedRecipe(
                    name="twin-kept",
                    workflow_key=_card(hub, keys["twin-empty.json"]),
                    workflow_id=twin,
                    prompt="x",
                )
            )
            session.commit()

        server.vault.db.run_task(save_recipe, priority=DBPriority.IMMEDIATE)
        r = run_env.owner.get(
            f"{API}/workflows",
            params={"include_hidden": "true", "include_one_offs": "true"},
        )
        assert twin in {card["id"] for card in r.json()["cards"]}
        server.vault.db.run_task(_drop_twin_recipe, priority=DBPriority.IMMEDIATE)
        # Left off the grid, not gone: it still opens by its id.
        r = run_env.owner.get(f"{API}/workflows/{auto['twin-empty.json']}")
        assert r.status_code == 200, r.text
        # Hidden, it is not counted in `hidden` either; dismiss its file's
        # origin row and the file is no longer adopted: listed and counted.
        with hub.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO workflow_group_attr (workflow_id, hidden) "
                "VALUES (?, 1)",
                (twin,),
            )
        before = run_env.owner.get(f"{API}/workflows").json()["hidden"]
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE workflow_origin SET dismissed = 1 "
                "WHERE origin = 'file' AND remote_path = 'twin-empty.json'"
            )
        r = run_env.owner.get(f"{API}/workflows", params={"include_hidden": "true"})
        assert r.json()["hidden"] == before + 1
        assert twin in {c["id"] for c in r.json()["cards"]}
    finally:
        server.vault.db.run_task(
            lambda session: (
                session.exec(
                    delete(Picture).where(Picture.file_path == "twin_used.png")
                ),
                session.commit(),
            ),
            priority=DBPriority.IMMEDIATE,
        )
        server.vault.db.run_task(_drop_twin_recipe, priority=DBPriority.IMMEDIATE)
        for workflow_id in manual.values():
            delete_manual_workflow(hub, workflow_id)
        with hub.transaction() as conn:
            conn.execute("DELETE FROM workflow_file WHERE workflow_name LIKE 'twin-%'")
            conn.execute(
                "DELETE FROM workflow_group_attr WHERE workflow_id = ?",
                (auto["twin-empty.json"],),
            )
