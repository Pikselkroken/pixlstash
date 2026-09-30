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
from pixlstash.hub.workflow_card_reads import card_index, manual_document
from pixlstash.hub.workflow_group_writes import delete_manual_workflow
import pixlstash.routes.comfyui as comfyui_routes
import pixlstash.hub.workflow_group_convert as convert
from pixlstash.hub.workflow_group_convert import (
    _GROUP_NAMESPACE,
    convert_card_state,
    dissolve_manual_groups,
)
from pixlstash.hub import workflow_cards
from pixlstash.hub.workflows import get_document, record_api_graph, record_ui_graph
from pixlstash.server import Server
import pixlstash.routes.workflows as workflows_routes
from pixlstash.services.workflow_identity import (
    core_node_labels,
    topology_node_labels,
)
from pixlstash.tasks.missing_saved_recipe_workflow_finder import (
    MissingSavedRecipeWorkflowFinder,
)
from pixlstash.utils.workflow_ids import WORKFLOW_TAG_KEY
from tests.test_workflow_identity import _graph

API = "/api/v1"
LIB = "test-library"
SPEED_LORA = "test-lightning-8step.safetensors"
SPEED_SHA = "5" * 64
CHARACTER_LORA = "test-character.safetensors"
OTHER_CHARACTER_LORA = "test-other-character.safetensors"
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

    speed = _graph(loras=(SPEED_LORA,))
    speed["L0"]["inputs"]["strength_model"] = 0.5
    # Filed first, so the slot's mark freezes `structural` on the speed LoRA.
    w.s = file(speed)
    w.a1 = file(_graph(ckpt="a.safetensors"))
    w.b1 = file(_graph(ckpt="b.safetensors"))
    w.d = file(_graph(face_detailer=True))
    w.p = file(_graph(preview=True))
    w.i = file(_graph(img2img=True, loras=(CHARACTER_LORA,)))
    w.i_second = file(_graph(img2img=True, loras=(OTHER_CHARACTER_LORA,)))
    w.ic = file(_graph(img2img=True, loras=(CHARACTER_LORA,), ckpt="c.safetensors"))
    w.iu = file(_graph(img2img=True, upscale=True))
    w.x = file(_graph(img2img=True, preview=True))
    for name in ("s", "a1", "b1", "d", "p", "i", "ic", "iu", "x"):
        setattr(w, f"{name}_key", _card(hub, getattr(w, name)))
    assert _card(hub, w.i_second) == w.i_key, "a character LoRA forked the card"
    core = hub.fetchone(
        "SELECT core_hash FROM workflow_topology_core WHERE topology_hash = ?",
        (w.s.topology_hash,),
    )["core_hash"]
    w.auto = f"auto:{core}"
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
            (w.auto, core),
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
        assert reopened.fetchone("PRAGMA user_version")[0] == 7
        assert reopened.fetchone("SELECT COUNT(*) FROM workflow_document")[0] == 3
    finally:
        reopened.close()


def _auto_of(hub, keys) -> str:
    return (
        "auto:"
        + hub.fetchone(
            "SELECT core_hash FROM workflow_topology_core WHERE topology_hash = ?",
            (keys.topology_hash,),
        )["core_hash"]
    )


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
    img2img = _auto_of(hub, world.i)
    with hub.transaction() as conn:
        assert dissolve_manual_groups(conn) == 3
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
        "input": {"required": {"ckpt_name": [["a.safetensors", "b.safetensors"], {}]}}
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
    second_a = _graph(ckpt="a.safetensors")
    second_a["5"]["inputs"]["steps"] = 21
    # Two distinct runs of A to B's one: A is the default recipe's checkpoint.
    a_runs = [
        record_api_graph(server.hub, _graph(ckpt="a.safetensors"), library),
        record_api_graph(server.hub, second_a, library),
    ]
    b_run = record_api_graph(server.hub, _graph(ckpt="b.safetensors"), library)
    b_key = _card(server.hub, b_run)
    assert b_key != _card(server.hub, a_runs[0])
    sampler, core_sampler = _labels(server.hub, b_run, "5")
    with server.hub.transaction() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO model (file_kind, filename, sha256, provenance) "
            "VALUES ('checkpoint', ?, ?, 'scanned')",
            [("a.safetensors", "a" * 64), ("b.safetensors", "b" * 64)],
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
        {"address": f"core:{core}/ckpt_name", "filename": "b.safetensors"}
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
    assert graph["1"]["inputs"]["ckpt_name"] == "b.safetensors"
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
        server.hub, _graph(ckpt="a.safetensors", face_detailer=True), library
    )
    with server.hub.transaction() as conn:
        convert_card_state(conn)
    plain = record_api_graph(server.hub, _graph(ckpt="a.safetensors"), library)
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
