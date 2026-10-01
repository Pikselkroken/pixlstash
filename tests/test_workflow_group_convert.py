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
from pixlstash.hub.schema import CURRENT_DATA_VERSION
from pixlstash.hub.workflow_card_reads import (
    card_index,
    manual_document,
    workflow_of_variant,
)
from pixlstash.hub.workflow_group_writes import delete_manual_workflow
import pixlstash.routes.comfyui as comfyui_routes
import pixlstash.hub.workflow_group_convert as convert
from pixlstash.hub.workflow_group_convert import (
    _CORE_RULE_V1,
    _GROUP_NAMESPACE,
    _core_strip_v1,
    convert_card_state,
    dissolve_manual_groups,
    rederive_cores,
)
from pixlstash.hub import workflow_cards
from pixlstash.hub.workflows import get_document, record_api_graph, record_ui_graph
from pixlstash.server import Server
import pixlstash.routes.workflows as workflows_routes
from pixlstash.services.workflow_hash import graph_key, node_labels
from pixlstash.services.workflow_identity import (
    core_node_labels,
    topology_node_labels,
)
from pixlstash.tasks.missing_saved_recipe_workflow_finder import (
    MissingSavedRecipeWorkflowFinder,
)
from pixlstash.tasks.workflow_card_backfill_finder import WorkflowCardBackfillFinder
from pixlstash.tasks.workflow_card_backfill_task import FamilyReidentifyTask
from pixlstash.utils.workflow_ids import WORKFLOW_TAG_KEY
from tests.test_workflow_identity import CORE_V2_TWINS, _graph

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
                # Owner state is stored against it, so the automatic one is a row.
                (w.auto, "auto", w.auto[len("auto:") :]),
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
        convert.adopt_workflow_files(conn, str(folder))
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
    assert rows["workflow_group"] == sorted(
        [(i, "auto", i[len("auto:") :]) for i in (flux, qwen)]
    )
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
    with hub.transaction() as conn:
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
    assert task._run_task() == {"moved": 1}
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
