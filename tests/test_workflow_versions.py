"""A manual workflow's versions and its origin rows, against a bare hub.

No server: each test drives the hub modules and the store every way in uses,
against a tmp hub and tmp workflow folders.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from pixlstash.hub import workflow_card_reads, workflow_versions
from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflow_card_reads import manual_document
from pixlstash.hub.workflow_group_writes import (
    delete_manual_workflow,
    set_manual_api_document,
)
from pixlstash.routes import comfyui as comfyui_module
from pixlstash.routes import workflows as workflows_routes
from pixlstash.services import workflow_bindings, workflow_inbox
from pixlstash.utils.workflow_ids import tagged_workflow_id, untagged

FIXTURES = Path(__file__).parent / "comfyui_workflows"

# Two editor-format workflows; the second names a UNET the first does not.
PLAIN = json.loads((FIXTURES / "image_z_image_turbo.json").read_text("utf-8"))
NEEDS_PACK = json.loads((FIXTURES / "image_flux2_klein_t2i.json").read_text("utf-8"))
ABSENT_MODEL = "flux-2-klein-9b-fp8.safetensors"


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """Point every workflow folder at tmp, and fake the trash."""
    user = tmp_path / "user"
    builtin = tmp_path / "built-in"
    inbox = tmp_path / "inbox"
    for folder in (user, builtin, inbox):
        folder.mkdir()

    def fake_trash(path):
        os.remove(path)

    monkeypatch.setattr(
        comfyui_module,
        "_workflow_dirs",
        lambda: [("user", str(user)), ("built-in", str(builtin))],
    )
    monkeypatch.setattr(comfyui_module, "workflow_user_dir", lambda: str(user))
    monkeypatch.setattr(workflow_inbox, "workflow_inbox_dir", lambda: str(inbox))
    monkeypatch.setattr(workflow_inbox, "send2trash", fake_trash)
    monkeypatch.setattr(comfyui_module, "send2trash", fake_trash)
    return user, builtin


@pytest.fixture
def hub(tmp_path):
    database = HubDatabase(str(tmp_path / "hub.db"))
    try:
        yield database
    finally:
        database.close()


def _versions(hub, workflow_id: str) -> list[tuple[int, str]]:
    return [
        (row["version"], row["source"])
        for row in hub.fetchall(
            "SELECT version, source FROM workflow_version WHERE workflow_id = ? "
            "ORDER BY version",
            (workflow_id,),
        )
    ]


def _save_over(hub, workflow_id: str, document: dict) -> int:
    with hub.transaction() as conn:
        return workflow_versions.append_version(
            conn, workflow_id, document, source="chain"
        )


# ── versions ────────────────────────────────────────────────────────────────


def test_every_way_in_stores_version_1_and_a_delete_takes_the_versions(folders, hub):
    workflow_id = comfyui_module.store_manual_workflow(hub, "mine", PLAIN, "import")
    assert _versions(hub, workflow_id) == [(1, "import")]
    converted = {"1": {"class_type": "KSampler", "inputs": {}}}
    set_manual_api_document(hub, [workflow_id], converted)
    # The conversion belongs to the version it converts, and the card reads it.
    row = hub.fetchone(
        "SELECT api_document FROM workflow_version WHERE workflow_id = ?",
        (workflow_id,),
    )
    assert json.loads(row[0]) == converted
    delete_manual_workflow(hub, workflow_id)
    assert _versions(hub, workflow_id) == []


def test_a_new_version_of_a_workflow_an_older_build_made_keeps_its_first(folders, hub):
    workflow_id = comfyui_module.store_manual_workflow(hub, "old", PLAIN, "import")
    converted = {"1": {"class_type": "KSampler", "inputs": {}}}
    set_manual_api_document(hub, [workflow_id], converted)
    with hub.transaction() as conn:
        conn.execute("DELETE FROM workflow_version")
    edited = copy.deepcopy(PLAIN)
    edited["extra"] = {"edited": True}
    assert _save_over(hub, workflow_id, edited) == 2
    assert _versions(hub, workflow_id) == [(1, "import"), (2, "chain")]
    first = hub.fetchone(
        "SELECT document, api_document FROM workflow_version "
        "WHERE workflow_id = ? AND version = 1",
        (workflow_id,),
    )
    assert untagged(json.loads(first["document"])) == PLAIN
    assert json.loads(first["api_document"]) == converted
    # The card now reads version 2, with no conversion: that was of version 1.
    current = hub.fetchone(
        "SELECT document, api_document FROM workflow_document WHERE workflow_id = ?",
        (workflow_id,),
    )
    assert json.loads(current["document"])["extra"]["edited"] is True
    assert current["api_document"] is None
    assert tagged_workflow_id(manual_document(hub, workflow_id)) == workflow_id


def test_data_step_12_gives_every_manual_workflow_its_version_1(folders, hub):
    workflow_id = comfyui_module.store_manual_workflow(hub, "mine", PLAIN, "inbox")
    with hub.transaction() as conn:
        conn.execute("DELETE FROM workflow_version")
        conn.execute("PRAGMA user_version = 11")
    path = hub.path
    hub.close()
    reopened = HubDatabase(path)
    try:
        assert _versions(reopened, workflow_id) == [(1, "inbox")]
        row = reopened.fetchone(
            "SELECT content_hash, document FROM workflow_version WHERE workflow_id = ?",
            (workflow_id,),
        )
        assert row["content_hash"] == workflow_inbox.content_hash(PLAIN)
        assert json.loads(row["document"]) == manual_document(reopened, workflow_id)
    finally:
        reopened.close()


def test_a_workflow_keeps_its_first_version_and_the_newest_49(folders, hub):
    workflow_id = comfyui_module.store_manual_workflow(hub, "w", PLAIN, "import")
    for n in range(60):
        edited = copy.deepcopy(PLAIN)
        edited["extra"] = {"n": n}
        _save_over(hub, workflow_id, edited)
    kept = [version for version, _ in _versions(hub, workflow_id)]
    assert len(kept) == workflow_versions.MAX_VERSIONS == 50
    assert kept == [1, *range(13, 62)]
    assert manual_document(hub, workflow_id)["extra"]["n"] == 59
    # The card says which version is current, not how many are kept.
    (card,) = [
        card
        for card in workflow_card_reads._manual_cards(hub)
        if card.workflow_key == workflow_id
    ]
    assert (card.versions, card.version) == (50, 61)


def test_a_conversion_is_not_stored_on_a_version_made_since_it_matched(folders, hub):
    workflow_id = comfyui_module.store_manual_workflow(hub, "w", PLAIN, "import")
    canonical = workflow_bindings.canonical(
        workflow_bindings.migrate_placeholders(PLAIN)[0]
    )
    edited = copy.deepcopy(PLAIN)
    edited["extra"] = {"edited": True}
    _save_over(hub, workflow_id, edited)
    converted = {"1": {"class_type": "KSampler", "inputs": {}}}
    assert set_manual_api_document(hub, [workflow_id], converted, canonical) == []
    assert (
        hub.fetchone(
            "SELECT api_document FROM workflow_document WHERE workflow_id = ?",
            (workflow_id,),
        )[0]
        is None
    )
    # Matching the current version, it is stored.
    assert set_manual_api_document(
        hub,
        [workflow_id],
        converted,
        workflow_bindings.canonical(workflow_bindings.migrate_placeholders(edited)[0]),
    ) == [workflow_id]


def test_a_cached_description_follows_the_version(folders, hub):
    workflow_id = comfyui_module.store_manual_workflow(hub, "w", PLAIN, "import")
    assert workflows_routes._manual_model_widgets(hub, workflow_id) == (
        workflows_routes._model_widgets_of(workflow_id, json.dumps(PLAIN))
    )
    _save_over(hub, workflow_id, NEEDS_PACK)
    names = {
        name for _w, name in workflows_routes._manual_model_widgets(hub, workflow_id)
    }
    assert ABSENT_MODEL in names
    # Keyed on the version: the same id with another version is read afresh,
    # and no document is held.
    first = workflow_card_reads._manual_facts_of(workflow_id, json.dumps(PLAIN), 1)
    assert workflow_card_reads._manual_facts_of(workflow_id, "{", 1) == first
    assert workflow_card_reads._manual_facts_of(workflow_id, "{", 2) == (None, None)
    assert workflow_card_reads._MANUAL_FACTS[workflow_id] == (2, (None, None))


def test_a_cached_description_is_not_handed_to_a_workflow_reusing_the_id(folders, hub):
    # A workflow made from a file reuses its id after a delete: the same id at
    # the same version number, stored at another time, is read afresh.
    workflow_id = comfyui_module.store_manual_workflow(hub, "w", PLAIN, "import")
    first = workflows_routes._manual_model_widgets(hub, workflow_id)
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_document SET document = ?, created_at = ? "
            "WHERE workflow_id = ?",
            (json.dumps(NEEDS_PACK), "2099-01-01T00:00:00+00:00", workflow_id),
        )
        conn.execute(
            "UPDATE workflow_version SET created_at = ? WHERE workflow_id = ?",
            ("2099-01-01T00:00:00+00:00", workflow_id),
        )
    again = workflows_routes._manual_model_widgets(hub, workflow_id)
    assert again != first
    assert ABSENT_MODEL in {name for _w, name in again}

    # The grid's description of a workflow is dropped once it is gone.
    workflow_card_reads._manual_cards(hub)
    assert workflow_id in workflow_card_reads._MANUAL_FACTS
    delete_manual_workflow(hub, workflow_id)
    workflow_card_reads._manual_cards(hub)
    assert workflow_id not in workflow_card_reads._MANUAL_FACTS


# ── origin rows ─────────────────────────────────────────────────────────────


def test_a_hub_made_before_content_hash_gains_the_column(tmp_path):
    """A development hub that ran an earlier commit of the pull (#1440)."""
    path = str(tmp_path / "older.db")
    database = HubDatabase(path)
    with database.transaction() as conn:
        conn.execute("DROP INDEX ix_workflow_origin_content")
        conn.execute("ALTER TABLE workflow_origin DROP COLUMN content_hash")
    database.close()

    reopened = HubDatabase(path)
    try:
        columns = {
            row[1] for row in reopened.fetchall("PRAGMA table_info(workflow_origin)")
        }
        assert "content_hash" in columns
    finally:
        reopened.close()


def test_the_inbox_matches_a_card_the_pull_made_until_it_is_deleted(folders, hub):
    """#1854: the pull of ComfyUI's saved workflows is gone and its origin rows
    stay. One still says its content is stored, so the same file dropped in
    the watched folder is not stored twice. Deleting the card dismisses the
    row (an older build sharing the hub must not pull the card back), and a
    dismissed row is read past, so the file is stored again when it is handed
    over again."""
    digest = workflow_inbox.content_hash(NEEDS_PACK)
    pulled = comfyui_module.store_manual_workflow(
        hub,
        "Needs pack",
        NEEDS_PACK,
        "pull",
        record=("http://comfy.test:8188", "Sub/Needs pack.json", 1000, digest),
    )
    with workflow_inbox.INBOX_LOCK:
        result = comfyui_module.store_inbox_workflow(hub, "dropped", NEEDS_PACK)
    assert (result["matched"], result["workflow_id"]) == (True, pulled)
    assert hub.fetchone("SELECT COUNT(*) FROM workflow_document")[0] == 1

    delete_manual_workflow(hub, pulled)
    assert [
        tuple(row)
        for row in hub.fetchall("SELECT origin, dismissed FROM workflow_origin")
    ] == [("http://comfy.test:8188", 1)]
    with workflow_inbox.INBOX_LOCK:
        result = comfyui_module.store_inbox_workflow(hub, "dropped", NEEDS_PACK)
    assert result["matched"] is False
    assert result["workflow_id"] != pulled
