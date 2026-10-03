"""The Workflows view's read API (implementation plan §F1/§F2), both authz directions.

Environment sharing
-------------------
One ``Server`` per module, built once, because the boot is the expensive part
and everything this suite asserts is a row. The hub rows are written with plain
SQL and the vault's pictures through one queued task; the autouse fixture wipes
and re-seeds both before every test and re-mints the credentials, so no
assertion can inherit another test's state and no refusal can pass because the
token was dead rather than because the scope was refused.

The seeded library is shaped around the three states the list has to survive
(design ``States.dc.html``), so each is an assertion rather than a judgement:

* a topology with **two variants** and kept pictures — the ordinary row, and the
  one whose expansion has to add up;
* a topology whose every picture is **soft-deleted**, which must read as *none
  kept* rather than vanishing or reading as live;
* a recipe whose **asset names were forgotten**, which must still list, still
  group and still expand, and simply stop saying which models it used.

Both directions on every route, per §16.1: the owner 200s (over-blocking is its
own regression) and every scoped share token is 403'd by the gate's
``OWNER_ONLY`` declaration, with an in-scope positive control proving the
refused credential is live.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import logging
import os
import sqlite3
import tempfile
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import delete, select, update

from pixlstash import auth, mcp_server
from pixlstash.authz.policy import AccessPolicy
from pixlstash.authz.registry import ROUTE_POLICIES
from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, Project, ReferenceFolder, UserToken
from pixlstash.db_models.saved_recipe import SavedRecipe
from pixlstash.event_types import EventType
from pixlstash.hub.workflow_card_reads import (
    AUTO_STACK_PREFIX,
    Card,
    Workflow,
    group_picture_inputs,
    instance_documents,
    variant_documents,
)
from pixlstash.hub.workflow_card_reads import _manual_facts_of, manual_document
from pixlstash.hub.workflow_group_writes import (
    create_manual_workflow,
    delete_manual_workflow,
)
from pixlstash.hub.workflow_cards import CORE_RULE_VERSION, auto_workflow_id
from pixlstash.hub.workflows import (
    PictureGhost,
    get_document,
    record_picture_ghosts,
)
from pixlstash.services.workflow_hash import (
    WorkflowGraphError,
    asset_reference,
    structural_document,
    topology_hash,
)
from pixlstash.services import workflow_card_service
from pixlstash.services.workflow_run_service import (
    MISSING_MODELS,
    MISSING_NODES,
    Reason,
    bypass_missing_loras,
    place_recipe_loras,
    repair,
    replace_missing_seed_nodes,
    prompt_text_target,
    replace_missing_text_nodes,
    skip_requested_loras,
)
from pixlstash.utils.known_base_models import family_of, fold
import pixlstash.routes.workflows as workflows_routes
from pixlstash.routes.comfyui import MAX_RUNS_PER_REQUEST
from pixlstash.routes.workflows import RunRequest, UNNAMED_CARD
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.services.workflow_run_service import FORGOTTEN_MODEL
from pixlstash.services import workflow_run_service as run_service
from pixlstash.services.workflow_card_service import (
    SlotModel,
    WorkflowFigures,
    model_marks,
)
from pixlstash.services.workflow_identity import (
    CORE_ADDRESS_PREFIX,
    FACE_DETAILER,
    UPSCALE,
    WORKFLOW_KEY_VERSION,
    core_node_labels,
    graph_traits,
    guess_mark,
    slots,
    special_groups,
    topology_node_labels,
    workflow_key,
)
from pixlstash.services.workflow_export import (
    download_name,
    download_stem,
    scrub_for_export,
)
from pixlstash.services.workflow_inputs import card_input_modes
from pixlstash.utils.workflow_ids import stamp_workflow_id
from pixlstash.services.workflow_io import detect_workflow_io
import pixlstash.routes.comfyui as comfyui_module
from pixlstash.services import (
    comfyui_service,
    saved_recipe_service,
    workflow_bindings,
    workflow_inbox,
)
from pixlstash.server import Server
from pixlstash.stacking import parse_workflow_tag_from_filename
from pixlstash.tasks.ghost_cascade_task import GhostCascadeTask
from pixlstash.tasks.task_type import TaskType
from pixlstash.utils.sql_chunking import SQLITE_ID_CHUNK
from tests.authz_guard import assert_real_route, no_spa_fallback  # noqa: F401

API = "/api/v1"

# The SPA catch-all answers an unmatched GET with 200, which would make every
# positive assertion below vacuous if a path were misspelled.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")

_WORKFLOW_ROUTES = (
    ("GET", "/api/v1/workflows"),
    ("GET", "/api/v1/workflows/{workflow_id}"),
    ("GET", "/api/v1/workflows/{workflow_id}/pictures"),
    ("GET", "/api/v1/workflows/recipes/{structural_hash}/graph"),
    # The ghost routes. Pinned here as well as refused in the authz test below:
    # every token that test can mint is READ, which the middleware refuses on a
    # DELETE before the gate reads the declaration, so a loosened entry would
    # leave that test green.
    ("GET", "/api/v1/server-config/ghost-retention"),
    ("PATCH", "/api/v1/server-config/ghost-retention"),
    ("DELETE", "/api/v1/server-config/ghost-retention/ghosts"),
    ("DELETE", "/api/v1/server-config/ghost-retention/model-ghosts"),
    # Where a LoRA loader would go (#1376): it reaches the owner's ComfyUI, and
    # its refusal is measured with the GET belts emptied in the test below.
    ("GET", "/api/v1/comfyui/workflows/{workflow_name}/lora-insertion"),
    # Export (v1.12 B8): the sharpest read here, because it hands back a whole
    # graph rather than a count of one.
    ("GET", "/api/v1/workflows/{workflow_id}/export"),
    # The LoRA chain editor's read (#1478): the whole-library graph, the shelf
    # LoRA each loader loads, and the owner's ComfyUI behind it.
    ("GET", "/api/v1/workflows/{workflow_id}/lora-chain"),
    ("GET", "/api/v1/workflows/{workflow_id}/lora-summary"),
    # Open in ComfyUI: the same graph unscrubbed, so owner-only for the same
    # reason and with even more to lose.
    ("GET", "/api/v1/workflows/{workflow_id}/graph"),
    # Clone with new models: the card's files, the whole shelf and the recipes.
    ("GET", "/api/v1/workflows/{workflow_id}/model-swap"),
)

# The workflow writes (#1623), pinned in their own tuple: the reads above are
# owner-only over a disclosure judgement, these are owner-only because they
# are the owner rearranging their own library and no narrower scope could
# describe one.
_WORKFLOW_WRITE_ROUTES = (
    ("PATCH", "/api/v1/workflows/{workflow_id}"),
    ("PUT", "/api/v1/workflows/{workflow_id}/model-fix"),
    ("PUT", "/api/v1/workflows/{workflow_id}/defaults"),
    ("PUT", "/api/v1/workflows/{workflow_id}/default-lora"),
    ("PUT", "/api/v1/workflows/{workflow_id}/pins"),
    ("PUT", "/api/v1/workflows/{workflow_id}/inputs"),
    # The run route and its dry run (v1.12 B7). OWNER_ONLY: it resolves a card
    # from the whole library rather than replaying one named picture's own
    # graph, which is what the picture-scoped run routes #1410 retired did.
    ("POST", "/api/v1/workflows/run"),
    ("POST", "/api/v1/workflows/run/preflight"),
    # The file gestures (v1.12 B8). Each resolves the card's graph out of the
    # whole library the way the run route does, and two of them write a file.
    ("POST", "/api/v1/workflows/{workflow_id}/duplicate"),
    ("POST", "/api/v1/workflows/{workflow_id}/insert-lora-loader"),
    ("POST", "/api/v1/workflows/{workflow_id}/fixed-copy"),
    ("PUT", "/api/v1/workflows/{workflow_id}/lora-chain"),
    ("POST", "/api/v1/workflows/{workflow_id}/clone-with-models"),
    # Its plans read the graph and the whole shelf; POST, so no GET belt.
    ("POST", "/api/v1/workflows/{workflow_id}/set-clone-plans"),
    ("DELETE", "/api/v1/workflows/{workflow_id}"),
    # ComfyUI's conversion of an editor file (#1530): writes stored files.
    ("POST", "/api/v1/comfyui/workflows/convert"),
    # Edit with ComfyUI: files a stored workflow on its card in the hub.
    ("POST", "/api/v1/comfyui/workflows/{workflow_name}/card"),
)


def _h(name: str) -> str:
    """A stable stand-in for one graph key.

    Digested rather than spelled out, because the routes check the shape: a key
    is 64 hex characters, and a readable stand-in padded to that length is
    refused as malformed by exactly the guard this suite also asserts.
    """
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


BUSY_TOPOLOGY = _h("busytopology")
BUSY_RECIPE_A = _h("busyrecipea")
BUSY_RECIPE_B = _h("busyrecipeb")
BINNED_TOPOLOGY = _h("binnedtopology")
BINNED_RECIPE = _h("binnedrecipe")
FORGOTTEN_TOPOLOGY = _h("forgottentopology")
FORGOTTEN_RECIPE = _h("forgottenrecipe")
HIDDEN_TOPOLOGY = _h("hiddentopology")
HIDDEN_RECIPE = _h("hiddenrecipe")

# The cards (v1.12 B3). Hand-written rather than derived, so a change to the
# key rule cannot silently re-shape the fixture underneath these assertions.
# BUSY and FORGOTTEN share a core hash and therefore stack; BINNED and HIDDEN
# are the two cards the grid must leave out, for two different reasons.
BUSY_CARD = _h("busycard")
FORGOTTEN_CARD = _h("forgottencard")
BINNED_CARD = _h("binnedcard")
HIDDEN_CARD = _h("hiddencard")
SHARED_CORE = _h("sharedcore")
FORGOTTEN_CORE = _h("forgottencore")
LONE_CORE = _h("lonecore")
HIDDEN_CORE = _h("hiddencore")

# The workflows (#1623): one per core hash here, so each card above is the one
# card of its own workflow and the base card every one-graph route acts on.
# The tests that need a workflow of several topologies put one together.
BUSY_WF = auto_workflow_id(SHARED_CORE, "")
FORGOTTEN_WF = auto_workflow_id(FORGOTTEN_CORE, "")
BINNED_WF = auto_workflow_id(LONE_CORE, "")
HIDDEN_WF = auto_workflow_id(HIDDEN_CORE, "")

# (structural_hash, topology_hash, workflow_key)
_SEED_VARIANTS = (
    (BUSY_RECIPE_A, BUSY_TOPOLOGY, BUSY_CARD),
    (BUSY_RECIPE_B, BUSY_TOPOLOGY, BUSY_CARD),
    (BINNED_RECIPE, BINNED_TOPOLOGY, BINNED_CARD),
    (FORGOTTEN_RECIPE, FORGOTTEN_TOPOLOGY, FORGOTTEN_CARD),
    (HIDDEN_RECIPE, HIDDEN_TOPOLOGY, HIDDEN_CARD),
)

# (topology_hash, core_hash, workflow_type, the variant its slot list is
# derived from). The slot list is DERIVED rather than written out, so a change
# to what a slot is cannot leave this fixture describing the old shape while
# the code reads the new one.
_SEED_CORES = (
    (BUSY_TOPOLOGY, SHARED_CORE, "txt2img", BUSY_RECIPE_A),
    (FORGOTTEN_TOPOLOGY, FORGOTTEN_CORE, "img2img", FORGOTTEN_RECIPE),
    (BINNED_TOPOLOGY, LONE_CORE, None, BINNED_RECIPE),
    (HIDDEN_TOPOLOGY, HIDDEN_CORE, "upscale", HIDDEN_RECIPE),
)

# Instance documents, the tier a default is read off. Node ids match the
# variant's stored document, because that is what carries the slot labels.
BUSY_INSTANCE_ONE = _h("busyinstanceone")
BUSY_INSTANCE_TWO = _h("busyinstancetwo")
FORGOTTEN_INSTANCE = _h("forgotteninstance")
# Three more runs of the same card, so the newest-N cap has something to
# cut. The two middle ones repeat the oldest one's settings and the newest
# one disagrees with all of them, which is what makes the cap, its limit
# and its ORDER BY each observable on their own.
FORGOTTEN_INSTANCE_B = _h("forgotteninstanceb")
FORGOTTEN_INSTANCE_C = _h("forgotteninstancec")
FORGOTTEN_INSTANCE_NEWEST = _h("forgotteninstancenewest")
# The two instances only a SOFT-DELETED picture ran. Nothing may ever read
# them: they exist so that dropping a `deleted` filter changes an answer.
BINNED_INSTANCE = _h("binnedinstance")
FORGOTTEN_BINNED_INSTANCE = _h("forgottenbinnedinstance")

# (structural_hash, topology_hash, node_count, first_seen_at)
_SEED_RECIPES = (
    (BUSY_RECIPE_A, BUSY_TOPOLOGY, 47, "2026-08-01T00:00:00Z"),
    (BUSY_RECIPE_B, BUSY_TOPOLOGY, 47, "2026-08-02T00:00:00Z"),
    (BINNED_RECIPE, BINNED_TOPOLOGY, 12, "2026-08-03T00:00:00Z"),
    (FORGOTTEN_RECIPE, FORGOTTEN_TOPOLOGY, 38, "2026-08-04T00:00:00Z"),
    (HIDDEN_RECIPE, HIDDEN_TOPOLOGY, 9, "2026-08-05T00:00:00Z"),
)

# (structural_hash, widget_name, normalized_filename). The forgotten recipe has
# none, which is the state itself and not a missing row.
_SEED_ASSETS = (
    (BUSY_RECIPE_A, "ckpt_name", "realvisxl.safetensors"),
    (BUSY_RECIPE_A, "lora_name", "add_detail.safetensors"),
    (BUSY_RECIPE_B, "ckpt_name", "realvisxl.safetensors"),
)

# The stored shape: every asset an ``asset_reference``. The forgotten recipe's
# three references have no asset row behind them, which is what "names
# forgotten" is read from.
_DOCUMENTS = {
    BUSY_RECIPE_A: {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": asset_reference("realvisxl.safetensors")},
        },
        "2": {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": asset_reference("add_detail.safetensors"),
                "strength_model": None,
                "model": ["1", 0],
            },
        },
        # The parameters are nulled in a STORED document; the instance
        # documents below are where the values live, which is the whole
        # difference the defaults read depends on.
        "3": {
            "class_type": "KSampler",
            "inputs": {"steps": None, "cfg": None, "model": ["2", 0]},
        },
        # A refiner pass: a SECOND slot offering `steps`, which is what makes
        # the ⓘ list's label have to be unique within a card.
        "4": {
            "class_type": "KSamplerAdvanced",
            "inputs": {"steps": None, "cfg": None, "model": ["3", 0]},
        },
    },
    BUSY_RECIPE_B: {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": asset_reference("realvisxl.safetensors")},
        },
    },
    HIDDEN_RECIPE: {"1": {"class_type": "SaveImage", "inputs": {}}},
    BINNED_RECIPE: {"1": {"class_type": "SaveImage", "inputs": {}}},
    FORGOTTEN_RECIPE: {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": asset_reference("gone_base.safetensors")},
        },
        "2": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": asset_reference("gone_one.safetensors")},
        },
        "3": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": asset_reference("gone_two.safetensors")},
        },
        "4": {
            "class_type": "KSampler",
            "inputs": {"steps": None, "cfg": None, "model": ["3", 0]},
        },
    },
}

# (library-scoped) instance documents: the stored graph with its parameters
# filled in. ``busy_one`` and ``busy_two`` are the pictures rated 4 and up, so
# the card's defaults are the mode over these two and never over the third.
_INSTANCE_DOCUMENTS = {
    BUSY_INSTANCE_ONE: (BUSY_RECIPE_A, {"steps": 30, "cfg": 7.0}),
    BUSY_INSTANCE_TWO: (BUSY_RECIPE_A, {"steps": 30, "cfg": 8.0}),
    FORGOTTEN_INSTANCE: (FORGOTTEN_RECIPE, {"steps": 20, "cfg": 5.0}),
    FORGOTTEN_INSTANCE_B: (FORGOTTEN_RECIPE, {"steps": 20, "cfg": 5.0}),
    FORGOTTEN_INSTANCE_C: (FORGOTTEN_RECIPE, {"steps": 20, "cfg": 5.0}),
    FORGOTTEN_INSTANCE_NEWEST: (FORGOTTEN_RECIPE, {"steps": 77, "cfg": 7.7}),
    BINNED_INSTANCE: (BUSY_RECIPE_A, {"steps": 44, "cfg": 44.0}),
    FORGOTTEN_BINNED_INSTANCE: (FORGOTTEN_RECIPE, {"steps": 99, "cfg": 9.0}),
}


# Every readable filename the fixture names, by the reference a stored
# document holds it under - the same lookup `workflow_cards._freeze_marks`
# makes when it guesses a slot's mark.
_ASSET_NAMES = {asset_reference(filename): filename for _, _, filename in _SEED_ASSETS}


def _slots_of(structural_hash: str) -> list:
    """The model slots of one seeded document, by the rule B1 owns."""
    return slots(_DOCUMENTS[structural_hash])


def _slot_list(structural_hash: str) -> list[dict]:
    """``workflow_topology_core.slots`` exactly as the B2 backfill writes it."""
    return [
        {
            "label": slot.label,
            "class_type": slot.class_type,
            "widget": slot.widget,
            "is_lora": slot.is_lora,
        }
        for slot in _slots_of(structural_hash)
    ]


def _instance_document(structural_hash: str, values: dict) -> dict:
    """The variant's document with the sampler's parameters filled in."""
    document = json.loads(json.dumps(_DOCUMENTS[structural_hash]))
    for node in document.values():
        if "KSampler" in node["class_type"]:
            node["inputs"].update(values)
    return document


# The one model on the shelf: BUSY's checkpoint. Its LoRA is not, which makes
# ``add_detail.safetensors`` a model ghost.
_SHELF_FILENAME = "realvisxl.safetensors"
# A shelf checkpoint no seeded graph loads: what a missing one is replaced by.
_REPLACEMENT_FILENAME = "test-realvisxl-bf16.safetensors"
_SECOND_REPLACEMENT = "test-realvisxl-fp16.safetensors"
# What the shelf calls it, which is not how the file is spelled - the whole
# point of the join (#1454). A card naming this model says ``Krea 2``, never
# ``realvisxl``, and a card naming any model the shelf has not got still says
# the stem.
_SHELF_TITLE = "Krea 2"
# The same file as a card NAMES it: no folder, no extension, no quant postfix.
# A slot's ``name`` is derived rather than raw, so a card whose model the shelf
# has not got still reads as a name rather than as a path somebody pasted.
_SHELF_DERIVED = "realvisxl"

# (file_path, topology, structural, deleted, created_at, score, instance)
#
# ``score`` is the star rating. NULL is unrated and **0 is a rating somebody
# cleared**, which is not the same thing: the cover rank counts only stars that
# were put there, so a 0 must not drag a card's mean down and must still sort
# above a picture nobody has looked at.
#
# **Two pictures here are soft-deleted on purpose and are not spare.** Every
# read in this feature filters ``deleted``, and with the binned pictures all on
# cards the grid drops for other reasons those filters could each be removed
# with the suite still green. These two sit on cards that ARE drawn, are rated
# higher than anything kept beside them, and ran instances nothing else ran, so
# a dropped filter changes a count, a cover, a rating or a default.
_SEED_PICTURES = (
    (
        "busy_one.png",
        BUSY_TOPOLOGY,
        BUSY_RECIPE_A,
        False,
        "2026-08-10T00:00:00Z",
        5,
        BUSY_INSTANCE_ONE,
    ),
    (
        "busy_two.png",
        BUSY_TOPOLOGY,
        BUSY_RECIPE_A,
        False,
        "2026-08-11T00:00:00Z",
        4,
        BUSY_INSTANCE_TWO,
    ),
    (
        "busy_three.png",
        BUSY_TOPOLOGY,
        BUSY_RECIPE_B,
        False,
        "2026-08-12T00:00:00Z",
        0,
        None,
    ),
    (
        "busy_four.png",
        BUSY_TOPOLOGY,
        BUSY_RECIPE_B,
        False,
        "2026-08-13T00:00:00Z",
        0,
        None,
    ),
    # Unrated and quality-scored: the commonest picture in a real library, and
    # the only one that can tell `score or 0` from "NULL sorts last". Read as a
    # zero it ties the two cleared zeros, wins on its smart score and takes the
    # third cover slot, which is the in-memory strip disagreeing with the SQL
    # window that chose the candidates.
    (
        "busy_unrated.png",
        BUSY_TOPOLOGY,
        BUSY_RECIPE_A,
        False,
        "2026-08-09T00:00:00Z",
        None,
        None,
    ),
    # Soft-deleted, rated above everything kept on its card, and newest: it
    # would take the top of the cover strip and the top of the picture list.
    (
        "busy_binned.png",
        BUSY_TOPOLOGY,
        BUSY_RECIPE_A,
        True,
        "2026-08-20T00:00:00Z",
        5,
        BINNED_INSTANCE,
    ),
    (
        "binned.png",
        BINNED_TOPOLOGY,
        BINNED_RECIPE,
        True,
        "2026-08-13T00:00:00Z",
        None,
        None,
    ),
    # Rated, but below "your best pictures", so its card's defaults fall back
    # to every picture it has. A card nobody rated 4 still has to offer one.
    (
        "forgotten.png",
        FORGOTTEN_TOPOLOGY,
        FORGOTTEN_RECIPE,
        False,
        "2026-08-14T00:00:00Z",
        2,
        FORGOTTEN_INSTANCE,
    ),
    # Three more runs of the forgotten card, unrated, newer than the first.
    # Two repeat its settings and the newest disagrees, so the mode over ALL of
    # them is 20 and the mode over the newest ONE is 77 -- which is what makes
    # `DEFAULT_SAMPLE`, its `LIMIT` and its `ORDER BY` each visible.
    (
        "forgotten_b.png",
        FORGOTTEN_TOPOLOGY,
        FORGOTTEN_RECIPE,
        False,
        "2026-08-15T00:00:00Z",
        None,
        FORGOTTEN_INSTANCE_B,
    ),
    (
        "forgotten_c.png",
        FORGOTTEN_TOPOLOGY,
        FORGOTTEN_RECIPE,
        False,
        "2026-08-16T00:00:00Z",
        None,
        FORGOTTEN_INSTANCE_C,
    ),
    (
        "forgotten_newest.png",
        FORGOTTEN_TOPOLOGY,
        FORGOTTEN_RECIPE,
        False,
        "2026-08-17T00:00:00Z",
        None,
        FORGOTTEN_INSTANCE_NEWEST,
    ),
    # Soft-deleted and rated 5: the only thing on its card that would qualify
    # as a "best picture", so a dropped filter flips that card's provenance.
    (
        "forgotten_binned.png",
        FORGOTTEN_TOPOLOGY,
        FORGOTTEN_RECIPE,
        True,
        "2026-08-21T00:00:00Z",
        5,
        FORGOTTEN_BINNED_INSTANCE,
    ),
    # Read for a workflow and found to carry none: it counts towards `scanned`
    # and belongs to no topology. Every real library has these.
    ("photograph.jpg", None, None, False, "2026-08-15T00:00:00Z", None, None),
)

# Smart scores, and both entries are load-bearing.
#
# ``busy_three`` carries **-1.0**, this repo's "the calculation failed" sentinel
# (CLAUDE.md: "always set metrics to -1.0 if calculation fails"), and
# ``busy_four`` carries none. Both are rated 0, so the smart score is what
# separates them -- and a failed metric has to sort BELOW nothing at all, the
# way ``NULLS LAST`` sorts it in the window that picks the cover. Reading a
# missing score as 0.0 flips them.
#
# ``busy_unrated`` is the same argument one column to the left: it is the only
# kept picture on a drawn card whose **star rating** is NULL, so it is what
# tells ``-inf`` from ``score or 0`` on that half of the comparator. Its smart
# score is high enough that reading its NULL rating as a zero would carry it
# past the two cleared zeros and into the cover strip.
_SMART_SCORES = {"busy_three.png": -1.0, "busy_unrated.png": 0.9}

# The one picture the pass has NOT reached. Seeded separately because it is the
# only row with a NULL `workflow_hash_version`, which is the whole difference
# between "we have read everything" and "we are still reading" — and a fixture
# where every picture is scanned makes that field's test pass against a count of
# any column at all.
_UNSCANNED_PICTURE = ("not_read_yet.png", "2026-08-16T00:00:00Z")


def _stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _seed_hub(server) -> None:
    """Write the four workflow tables from scratch."""
    with server.hub.transaction() as conn:
        # Children before parents: the hub enforces foreign keys, so a leftover
        # row aborts the wipe rather than lingering.
        conn.execute("DELETE FROM workflow_recipe_asset")
        conn.execute("DELETE FROM workflow_recipe_graph")
        conn.execute("DELETE FROM workflow_variant")
        conn.execute("DELETE FROM workflow_slot_mark")
        conn.execute("DELETE FROM workflow_lora_promotion")
        conn.execute("DELETE FROM workflow_file")
        conn.execute("DELETE FROM workflow_origin")
        conn.execute("DELETE FROM workflow_pulled_file")
        conn.execute("DELETE FROM workflow_recipe")
        conn.execute("DELETE FROM workflow_topology_core")
        conn.execute("DELETE FROM workflow_topology")
        conn.execute("DELETE FROM workflow_picture_ghost")
        conn.execute("DELETE FROM workflow_picture_input")
        conn.execute("DELETE FROM workflow_parameter_pins")
        conn.execute("DELETE FROM workflow_attr")
        conn.execute("DELETE FROM workflow_default_override")
        conn.execute("DELETE FROM workflow_key_pins")
        conn.execute("DELETE FROM workflow_key_picture_input")
        conn.execute("DELETE FROM workflow_cover")
        conn.execute("DELETE FROM workflow_stack_member")
        conn.execute("DELETE FROM workflow_stack")
        conn.execute("DELETE FROM workflow_unstacked")
        conn.execute("DELETE FROM workflow_recipe_instance")
        conn.execute("DELETE FROM workflow_model_fix")
        conn.execute("DELETE FROM workflow_group_default")
        conn.execute("DELETE FROM workflow_group_attr")
        conn.execute("DELETE FROM workflow_group_pins")
        conn.execute("DELETE FROM workflow_group_picture_input")
        conn.execute("DELETE FROM workflow_group_member")
        conn.execute("DELETE FROM workflow_group")
        conn.execute("DELETE FROM workflow_document")
        conn.execute(
            "DELETE FROM model WHERE filename IN (?, ?, ?, ?)",
            (
                _SHELF_FILENAME,
                "add_detail.safetensors",
                _REPLACEMENT_FILENAME,
                _SECOND_REPLACEMENT,
            ),
        )
        # Hashed, like a checkpoint the finder has already read: an unhashed
        # one holds back every digest judgement (see the digest tests below).
        conn.execute(
            "INSERT INTO model (file_kind, filename, sha256, display_name, "
            "provenance) VALUES ('checkpoint', ?, ?, ?, 'scanned')",
            (_SHELF_FILENAME, _h("realvisxl-digest"), _SHELF_TITLE),
        )
        for topology, node_count, first_seen in (
            (BUSY_TOPOLOGY, 47, "2026-08-01T00:00:00Z"),
            (BINNED_TOPOLOGY, 12, "2026-08-03T00:00:00Z"),
            (FORGOTTEN_TOPOLOGY, 38, "2026-08-04T00:00:00Z"),
            (HIDDEN_TOPOLOGY, 9, "2026-08-05T00:00:00Z"),
        ):
            conn.execute(
                "INSERT INTO workflow_topology "
                "(topology_hash, hash_version, node_count, first_seen_at) "
                "VALUES (?, 'v1', ?, ?)",
                (topology, node_count, first_seen),
            )
        conn.executemany(
            "INSERT INTO workflow_recipe "
            "(structural_hash, topology_hash, hash_version, node_count, first_seen_at) "
            "VALUES (?, ?, 'v1', ?, ?)",
            _SEED_RECIPES,
        )
        conn.executemany(
            "INSERT INTO workflow_recipe_asset "
            "(structural_hash, widget_name, normalized_filename) VALUES (?, ?, ?)",
            _SEED_ASSETS,
        )
        conn.executemany(
            "INSERT INTO workflow_recipe_graph "
            "(structural_hash, document_sha256, document, created_at) "
            "VALUES (?, 'x', ?, '2026-08-01T00:00:00Z')",
            [(key, json.dumps(doc)) for key, doc in _DOCUMENTS.items()],
        )
        conn.executemany(
            "INSERT INTO workflow_variant "
            "(structural_hash, topology_hash, workflow_key, key_version) "
            "VALUES (?, ?, ?, ?)",
            [(*row, WORKFLOW_KEY_VERSION) for row in _SEED_VARIANTS],
        )
        # Hand-written cards name no base model: the empty family set.
        conn.execute(
            "INSERT OR IGNORE INTO workflow_variant_family (structural_hash, families) "
            "SELECT structural_hash, '' FROM workflow_variant"
        )
        conn.executemany(
            "INSERT INTO workflow_topology_core "
            "(topology_hash, core_hash, core_version, workflow_type, slots, "
            "specials, traits) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    topology,
                    core,
                    CORE_RULE_VERSION,
                    kind,
                    json.dumps(_slot_list(structural)),
                    # Derived rather than written out, for the same reason the
                    # slot list is: a fixture that spelled it would go on
                    # describing the old rule after the rule changed.
                    ",".join(special_groups(_DOCUMENTS[structural])),
                    ",".join(graph_traits(_DOCUMENTS[structural])),
                )
                for topology, core, kind, structural in _SEED_CORES
            ],
        )
        # The LoRA marks B2 freezes on first sight. `add_detail.safetensors`
        # holds no speed-LoRA word, so the guess is `recipe` - which is what
        # makes it an anonymous dashed slot rather than a named file.
        conn.executemany(
            "INSERT INTO workflow_slot_mark (topology_hash, slot_label, mark) "
            "VALUES (?, ?, ?)",
            [
                (topology, slot.label, guess_mark(_ASSET_NAMES[slot.asset]))
                for topology, _, _, structural in _SEED_CORES
                for slot in _slots_of(structural)
                if slot.is_lora and slot.asset in _ASSET_NAMES
            ],
        )
        # The one workflow the owner has hidden. Written as a row rather than
        # inferred, because "hidden" is a decision and nothing derives it.
        conn.execute(
            "INSERT INTO workflow_group_attr (workflow_id, name, hidden) "
            "VALUES (?, 'A workflow I hid', 1)",
            (HIDDEN_WF,),
        )
        # It also has a workflow file, and that is load-bearing rather than
        # decoration: with no file and no pictures it would be a one-off too,
        # and the test that says hiding removes it from the grid would pass
        # because the one-off clause removed it instead.
        conn.execute(
            "INSERT INTO workflow_file "
            "(workflow_name, topology_hash, structural_hash, workflow_key) "
            "VALUES ('hidden.json', ?, ?, ?)",
            (HIDDEN_TOPOLOGY, HIDDEN_RECIPE, HIDDEN_CARD),
        )
        conn.executemany(
            "INSERT INTO workflow_recipe_instance "
            "(library_uuid, instance_hash, structural_hash, hash_version, "
            "document, first_seen_at) VALUES (?, ?, ?, 'v1', ?, '2026-08-10T00:00:00Z')",
            [
                (
                    server.vault.library_uuid,
                    instance_hash,
                    structural_hash,
                    json.dumps(_instance_document(structural_hash, values)),
                )
                for instance_hash, (
                    structural_hash,
                    values,
                ) in _INSTANCE_DOCUMENTS.items()
            ],
        )


def _seed_pictures(server) -> None:
    """Replace the vault's pictures with the seeded set, in one queued task."""

    def write(session):
        # The saved recipes go with them. They are keyed by `workflow_key`, not
        # by picture, so nothing else in this wipe reaches them - and a recipe
        # left behind changes the next test's one-off count and its cards'
        # `saved_recipe_count`, which is the shared-module leak CLAUDE.md warns
        # about: reset every global the module touches, not only the obvious one.
        session.exec(delete(SavedRecipe))
        session.exec(delete(Picture))
        for (
            path,
            topology,
            structural,
            deleted,
            created,
            score,
            instance,
        ) in _SEED_PICTURES:
            session.add(
                Picture(
                    file_path=path,
                    deleted=deleted,
                    created_at=_stamp(created),
                    score=score,
                    smart_score=_SMART_SCORES.get(path),
                    workflow_topology_hash=topology,
                    workflow_structural_hash=structural,
                    workflow_instance_hash=instance,
                    workflow_hash_version="v1",
                )
            )
        path, created = _UNSCANNED_PICTURE
        session.add(Picture(file_path=path, deleted=False, created_at=_stamp(created)))
        session.commit()

    server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


def _quiesce_background_work(server):
    """Take every work finder out of the planner and let the pipeline settle.

    A shared server is WARM, so its sweeps land inside the tests rather than
    sitting in the long backoff a freshly-built one is in — and this module's
    fixtures are hand-placed rows that those sweeps rewrite. The one that
    matters here is ``MissingComfyUIExtractionFinder``: it looks for exactly the
    NULL ``workflow_hash_version`` this suite seeds to prove the difference
    between "read everything" and "still reading", reads the (nonexistent) file
    and stamps the column, and the scan assertion then measured whichever ran
    first. Every finder goes, not a curated subset: nothing here needs derived
    data, every assertion is a status code or a count over rows this file
    wrote.

    The planner thread and the task runner keep running, so a route that submits
    work directly is unaffected. Returns the removed names so the per-test
    fixture can re-check that they are still gone.
    """
    planner = server.vault._work_planner
    task_types = list(server.vault._planner_work_finders)
    for task_type in task_types:
        server.vault._planner_work_finders.pop(task_type)
    removed = planner.detach_finders(task_types)

    # Work already queued when the finders went is still ours to wait for: it
    # would otherwise write into the first test's freshly seeded library.
    runner = server.vault._task_runner
    runner.cancel_pending_tasks()
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        with runner._active_task_lock:
            active = list(runner._active_tasks.values())
        if not active:
            return removed
        time.sleep(0.05)
    raise AssertionError(
        f"background work did not settle within 60s; still running: {active}"
    )


@pytest.fixture(scope="module")
def workflow_env():
    """One Server and one owner login, for every test in the module."""
    tmp = tempfile.TemporaryDirectory()
    config_path = f"{tmp.name}/server-config.json"
    with open(config_path, "w") as handle:
        json.dump({"port": 8000}, handle)
    server = Server(config_path)
    server.__enter__()
    try:
        owner = TestClient(server.api, raise_server_exceptions=True)
        # `example-` marks the value as invented, per CLAUDE.md's stand-in
        # table. The rest of the suite writes `ownerpass1`, which predates the
        # rule and is not this file's to change; a new line follows it.
        r = owner.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-ownerpass1"},
        )
        assert r.status_code == 200, r.text

        r = owner.post(f"{API}/characters", json={"name": "Workflow Character"})
        assert r.status_code in {200, 201}, r.text
        character_id = r.json().get("id") or r.json()["character"]["id"]

        extraction_finder = server.vault._planner_work_finders[
            TaskType.COMFYUI_EXTRACTION
        ]
        detached = _quiesce_background_work(server)

        yield SimpleNamespace(
            server=server,
            owner=owner,
            character_id=character_id,
            detached=detached,
            extraction_finder=extraction_finder,
        )
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


@pytest.fixture(autouse=True)
def fresh_library(workflow_env):
    """Re-seed the hub and the vault before every test.

    Identity, not counts, for the shared-environment reason: every assertion
    below names the workflow it expects, so state left by another test cannot
    make one pass for the wrong reason.
    """
    # Re-checked every test rather than trusted from module setup: a finder that
    # came back would rewrite the seeded rows and the failure would look like a
    # bug in the route.
    assert not workflow_env.server.vault._planner_work_finders, (
        "a work finder is back in the planner; the seeded rows are no longer "
        "the only thing writing to this vault"
    )
    _seed_hub(workflow_env.server)
    _seed_pictures(workflow_env.server)
    # The owner session is what every positive control runs on; prove it is live
    # before any refusal is measured against it.
    r = workflow_env.owner.get(f"{API}/workflows")
    assert r.status_code == 200, (
        f"the shared owner session cannot read the library ({r.status_code}: "
        f"{r.text}) — every refusal below would prove nothing"
    )
    yield workflow_env


def _by_hash(payload) -> dict:
    return {row["topology_hash"]: row for row in payload["workflows"]}


def _mint(owner_client, description: str, **restriction) -> str:
    r = owner_client.post(
        f"{API}/users/me/token",
        json={"description": description, "scope": "READ", **restriction},
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(server, token: str) -> TestClient:
    client = TestClient(server.api)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ===========================================================================
# Declarations — the registry entry is the route's only authorization
# ===========================================================================


def _titles(hub, names):
    """``model_marks`` narrowed to the title.

    Was ``model_titles`` in the service until #1483: a public function with no
    production caller, kept alive by these tests alone. The narrowing it did
    is one line and belongs to the one caller that wants it - here - while the
    rule itself stays stated once, in ``model_marks``.
    """
    return {
        name: mark.title for name, mark in model_marks(hub, names).items() if mark.title
    }


def test_the_workflow_scan_records_instances_for_the_open_library(workflow_env):
    """Without the library, the scan files recipes and silently no instance."""
    library_uuid = workflow_env.server.vault.library_uuid
    assert library_uuid
    assert workflow_env.extraction_finder._library_uuid == library_uuid


def test_every_workflow_route_is_declared_owner_only():
    """§16.1: the declaration IS the enforcement, so pin every cell.

    OWNER_ONLY is a decision here rather than a default: the counts are read
    across every non-deleted picture in the vault, so a scoped token holding
    them would learn the size of the whole library one workflow at a time.
    """
    for key in _WORKFLOW_ROUTES:
        assert key in ROUTE_POLICIES, f"{key} has no ROUTE_POLICIES entry"
        assert ROUTE_POLICIES[key].policy is AccessPolicy.OWNER_ONLY, (
            f"{key} declares {ROUTE_POLICIES[key].policy}, not OWNER_ONLY"
        )


def test_no_scoped_token_can_read_the_workflow_library(workflow_env):
    """Every route refuses a live resource-scoped share token.

    ``assert_real_route`` is load-bearing: the middleware answers before
    routing, so a renamed route would 403 identically and the assertion would
    dissolve into a test of nothing.
    """
    token = _mint(
        workflow_env.owner,
        "workflow scope probe",
        resource_type="character",
        resource_id=workflow_env.character_id,
    )
    client = _bearer(workflow_env.server, token)
    assert client.get(f"{API}/pictures").status_code == 200, (
        "the scoped token is dead; the refusals below would prove nothing"
    )
    # Each path is named with the template that must answer it. Since #1410
    # moved the cards onto `/workflows/{workflow_key}`, a bare `assert_real_route`
    # passes for ANY string in that segment, so it would no longer notice the
    # handler being renamed away - which is the vacuity it exists to refuse.
    paths = (
        (f"{API}/workflows", f"{API}/workflows"),
        (f"{API}/workflows/{BUSY_WF}", API + "/workflows/{workflow_id}"),
        (
            f"{API}/workflows/{BUSY_WF}/pictures",
            API + "/workflows/{workflow_id}/pictures",
        ),
        (
            f"{API}/workflows/recipes/{BUSY_RECIPE_A}/graph",
            API + "/workflows/recipes/{structural_hash}/graph",
        ),
        (
            f"{API}/workflows/{BUSY_WF}/export",
            API + "/workflows/{workflow_id}/export",
        ),
        (
            f"{API}/workflows/{BUSY_WF}/lora-chain",
            API + "/workflows/{workflow_id}/lora-chain",
        ),
        (
            f"{API}/workflows/{BUSY_WF}/graph",
            API + "/workflows/{workflow_id}/graph",
        ),
        (
            f"{API}/workflows/{BUSY_WF}/model-swap",
            API + "/workflows/{workflow_id}/model-swap",
        ),
    )
    for path, template in paths:
        assert_real_route(workflow_env.server.api, "GET", path, template)
        r = client.get(path)
        assert r.status_code == 403, f"GET {path}: {r.status_code} {r.text}"


# ===========================================================================
# The stored graph
# ===========================================================================


def test_a_recipe_serves_its_stored_graph_and_says_it_will_not_run(workflow_env):
    """The stored document is prompt-free and parameter-free by construction, so
    it describes the workflow and cannot be handed back to ComfyUI. The payload
    has to say so; a caller discovering it by feeding this to ComfyUI is the
    defect §B5 exists to close."""
    r = workflow_env.owner.get(f"{API}/workflows/recipes/{BUSY_RECIPE_A}/graph")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["document"] == _DOCUMENTS[BUSY_RECIPE_A]
    assert body["runnable"] is False


def test_an_unknown_recipe_is_a_404(workflow_env):
    r = workflow_env.owner.get(f"{API}/workflows/recipes/{_h('nosuchrecipe')}/graph")
    assert r.status_code == 404, r.text


# ===========================================================================
# Hardening (#1293): the rollback belt, transport, and the ghost routes
# ===========================================================================

# `(path, the route template that must answer it)` - see
# `test_no_scoped_token_can_read_the_workflow_library` for why the template is
# named rather than left to "some route matched".
_TEMPLATED_PATHS = (
    (f"{API}/workflows/{BUSY_WF}", API + "/workflows/{workflow_id}"),
    (
        f"{API}/workflows/{BUSY_WF}/pictures",
        API + "/workflows/{workflow_id}/pictures",
    ),
    (
        f"{API}/workflows/recipes/{BUSY_RECIPE_A}/graph",
        API + "/workflows/recipes/{structural_hash}/graph",
    ),
    # The export (v1.12 B8) hands back a whole graph, so it is the one here
    # with most to lose from the rollback.
    (f"{API}/workflows/{BUSY_WF}/export", API + "/workflows/{workflow_id}/export"),
    (f"{API}/workflows/{BUSY_WF}/graph", API + "/workflows/{workflow_id}/graph"),
)


def test_the_templated_reads_stay_closed_with_the_gate_rolled_back(workflow_env):
    """``AUTHZ_GATE_ENFORCING = False`` is a documented rollback, and the belt
    that survives it used to match literal paths only, so these three answered
    a share token. ``READ_BLOCKED_GET_PREFIXES`` is what refuses them now."""
    server = workflow_env.server
    unscoped = _bearer(server, _mint(workflow_env.owner, "rollback unscoped"))
    scoped = _bearer(
        server,
        _mint(
            workflow_env.owner,
            "rollback scoped",
            resource_type="character",
            resource_id=workflow_env.character_id,
        ),
    )
    previously_enforcing = server.authz._enforcing
    server.authz._enforcing = False
    try:
        for client in (unscoped, scoped):
            assert client.get(f"{API}/pictures").status_code == 200, (
                "the token is dead; the refusals below would prove nothing"
            )
            for path, template in _TEMPLATED_PATHS:
                assert_real_route(server.api, "GET", path, template)
                r = client.get(path)
                assert r.status_code == 403, f"GET {path}: {r.status_code} {r.text}"
        for path, _template in _TEMPLATED_PATHS:
            r = workflow_env.owner.get(path)
            assert r.status_code == 200, f"owner GET {path}: {r.status_code} {r.text}"
    finally:
        server.authz._enforcing = previously_enforcing


def test_the_workflow_reads_refuse_remote_plaintext_under_require_ssl(
    workflow_env, monkeypatch
):
    """The same transport rule as the model-shelf reads naming the same files."""
    server = workflow_env.server
    monkeypatch.setitem(server.auth._server_config, "require_ssl", True)
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "8.8.8.8")
    for path in (f"{API}/workflows", *(p for p, _t in _TEMPLATED_PATHS)):
        r = workflow_env.owner.get(path)
        assert r.status_code == 403 and "HTTPS is required" in r.text, (
            f"GET {path}: {r.status_code} {r.text}"
        )


def _ghost(server, pixel_sha: str, instance_hash: str) -> PictureGhost:
    return PictureGhost(
        library_uuid=server.vault.library_uuid,
        pixel_sha=pixel_sha,
        instance_hash=instance_hash,
        thumbnail=b"thumbnail-bytes",
    )


def _ghost_shas(server) -> set[str]:
    return {
        row["pixel_sha"]
        for row in server.hub.fetchall("SELECT pixel_sha FROM workflow_picture_ghost")
    }


def test_the_comfyui_pull_routes_are_the_owners_alone(workflow_env, monkeypatch):
    """#1440: both directions on the pull and its summary, measured at the gate.

    The GET belts are emptied so a refusal is the gate's (the rollback case the
    belt exists for is covered by it being in ``READ_BLOCKED_GET_PATHS``); the
    POST is refused to a READ token before routing either way. The runner is
    stubbed so the owner's pull is queued and never reaches a ComfyUI.
    """
    server = workflow_env.server
    monkeypatch.setattr(auth, "READ_BLOCKED_GET_PATHS", frozenset())
    monkeypatch.setattr(auth, "READ_BLOCKED_GET_PREFIXES", ())
    queued = []
    monkeypatch.setattr(
        server.vault, "submit_task", lambda task: queued.append(task) or task.id
    )
    path = f"{API}/comfyui/workflows/pull"
    assert_real_route(server.api, "GET", path)
    assert_real_route(server.api, "POST", path)
    tokens = {
        "unscoped": _mint(workflow_env.owner, "pull unscoped"),
        "scoped": _mint(
            workflow_env.owner,
            "pull scoped",
            resource_type="character",
            resource_id=workflow_env.character_id,
        ),
    }
    previously_enforcing = server.authz._enforcing
    server.authz._enforcing = True
    try:
        for label, token in tokens.items():
            client = _bearer(server, token)
            assert client.get(f"{API}/pictures").status_code == 200, label
            r = client.get(path)
            assert r.status_code == 403, f"{label} GET: {r.status_code} {r.text}"
            assert "Owner-level" in r.text, f"{label} GET not refused by the gate"
            r = client.post(path)
            assert r.status_code == 403, f"{label} POST: {r.status_code} {r.text}"
        assert queued == []

        r = workflow_env.owner.get(path)
        assert r.status_code == 200 and r.json()["status"] == "idle", r.text
        r = workflow_env.owner.post(path)
        assert r.status_code == 202 and r.json()["status"] == "started", r.text
        assert len(queued) == 1
        r = workflow_env.owner.get(path)
        assert r.status_code == 200 and r.json()["status"] == "pending", r.text
    finally:
        server.authz._enforcing = previously_enforcing


def test_the_ghost_routes_are_the_owners_alone(workflow_env, monkeypatch):
    """Both directions on GET, PATCH and the erase, with the gate enforcing.

    The GET belts are emptied so the refusal is measured at the gate rather
    than at the middleware in front of it; PATCH and DELETE are refused to a
    READ token before routing either way.
    """
    server = workflow_env.server
    monkeypatch.setattr(auth, "READ_BLOCKED_GET_PATHS", frozenset())
    monkeypatch.setattr(auth, "READ_BLOCKED_GET_PREFIXES", ())
    record_picture_ghosts(server.hub, [_ghost(server, "sha-authz", _h("authz"))])
    tokens = {
        "unscoped": _mint(workflow_env.owner, "ghost unscoped"),
        "scoped": _mint(
            workflow_env.owner,
            "ghost scoped",
            resource_type="character",
            resource_id=workflow_env.character_id,
        ),
    }
    record_picture_ghosts(
        server.hub,
        [
            PictureGhost(
                library_uuid=_h("another-library"),
                pixel_sha="sha-other-library",
                instance_hash=_h("authz"),
                thumbnail=b"thumbnail-bytes",
            )
        ],
    )
    base = f"{API}/server-config/ghost-retention"
    previously_enforcing = server.authz._enforcing
    server.authz._enforcing = True
    try:
        for label, token in tokens.items():
            client = _bearer(server, token)
            assert client.get(f"{API}/pictures").status_code == 200, label
            r = client.get(base)
            assert r.status_code == 403, f"{label} GET: {r.status_code} {r.text}"
            assert "Owner-level" in r.text, f"{label} GET not refused by the gate"
            r = client.patch(base, json={"workflow_ghost_retention": "on"})
            assert r.status_code == 403, f"{label} PATCH: {r.status_code} {r.text}"
            r = client.delete(f"{base}/ghosts")
            assert r.status_code == 403, f"{label} DELETE: {r.status_code} {r.text}"
            assert_real_route(server.api, "DELETE", f"{base}/model-ghosts")
            r = client.delete(f"{base}/model-ghosts")
            assert r.status_code == 403, f"{label} forget: {r.status_code} {r.text}"
        assert _ghost_shas(server) == {"sha-authz", "sha-other-library"}
        assert server.hub.fetchone(
            "SELECT 1 FROM workflow_recipe_asset "
            "WHERE normalized_filename = 'add_detail.safetensors'"
        )
        assert server.vault.ghost_retention == "covered"

        owner = workflow_env.owner
        assert owner.get(base).status_code == 200
        r = owner.patch(base, json={"workflow_ghost_retention": "covered"})
        assert r.status_code == 200, r.text
        r = owner.delete(f"{base}/ghosts")
        assert r.status_code == 200, r.text
        assert r.json()["ghosts_erased"] == 1
        # The erase is the active library's: another library's ghost stays.
        assert _ghost_shas(server) == {"sha-other-library"}
        r = owner.delete(f"{base}/model-ghosts")
        assert r.status_code == 200, r.text
        assert r.json()["names_forgotten"] == 1
    finally:
        server.authz._enforcing = previously_enforcing


def test_removing_a_reference_folder_cascades_its_uncovered_ghosts(workflow_env):
    """The reported repro: a folder removal left an uncovered ghost behind.

    Two ghosts, two instance hashes. The folder held the only picture carrying
    one of them and one of two carrying the other, so exactly one ghost loses
    its cover. The other staying is the positive control: a cascade that fired
    on every hash the folder touched would pass the first assertion alone.
    """
    server = workflow_env.server
    lost, kept = _h("folder-lost-instance"), _h("folder-kept-instance")
    with tempfile.TemporaryDirectory() as folder_dir:

        def insert(session):
            folder = ReferenceFolder(folder=folder_dir, label="refs", status="active")
            session.add(folder)
            session.commit()
            session.refresh(folder)
            for name, instance in (("lost.png", lost), ("kept.png", kept)):
                session.add(
                    Picture(
                        file_path=f"{folder_dir}/{name}",
                        reference_folder_id=folder.id,
                        workflow_instance_hash=instance,
                    )
                )
            session.add(Picture(file_path="cover.png", workflow_instance_hash=kept))
            session.commit()
            return folder.id

        folder_id = server.vault.db.run_task(insert)
        record_picture_ghosts(
            server.hub,
            [_ghost(server, "sha-lost", lost), _ghost(server, "sha-kept", kept)],
        )

        r = workflow_env.owner.delete(f"{API}/reference-folders/{folder_id}")
        assert r.status_code == 200, r.text

    result = GhostCascadeTask(vault=server.vault)._run_task()
    assert result["destroyed"] == 1, result
    assert _ghost_shas(server) == {"sha-kept"}


def test_a_hub_attached_vault_registers_the_ghost_cascade(workflow_env):
    """Every cascade test above drives the task directly; this is what makes the
    planner run it at all. The module detached the finders, so they are read
    back from the planner's record of what it detached."""
    assert "GhostCascadeFinder" in workflow_env.detached


# ===========================================================================
# Workflow files on disk — the shared isolation every file gesture needs
# ===========================================================================


def _isolate_workflow_folders(tmp_path, monkeypatch) -> None:
    """Point every workflow folder a route touches at *tmp_path*, and fake the trash.

    Deleting a workflow writes it back to the inbox and sends it to the system
    trash. Both are shared by every checkout on the machine, and a PixlStash
    running against the same data folder would import what lands in the inbox.
    """
    inbox = tmp_path / "inbox"
    # exist_ok: two workflow fixtures can share one test's tmp_path, which is
    # how a test gets a workflow with a LoRA loader and one without at once.
    inbox.mkdir(exist_ok=True)

    def fake_trash(path):
        os.remove(path)

    monkeypatch.setattr(comfyui_module, "_workflow_dirs", lambda: [("user", tmp_path)])
    monkeypatch.setattr(comfyui_module, "workflow_user_dir", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_inbox, "workflow_inbox_dir", lambda: str(inbox))
    monkeypatch.setattr(workflow_inbox, "send2trash", fake_trash)
    monkeypatch.setattr(comfyui_module, "send2trash", fake_trash)
    comfyui_module._describe_workflow.cache_clear()


# ===========================================================================
# A LoRA from the shelf: the slots a file carries, and where one would go (#1310)
# ===========================================================================

_SHELF_LORA_SHA = _h("shelf-lora-digest")
_SHELF_LORA_FILENAME = "example-subject-v2.safetensors"
# What the shelf calls the copy it scanned, and what ComfyUI calls the file it
# has. They are deliberately different paths of the same basename, because that
# is the ordinary case: the two sides count from different folders.
_SHELF_LORA_RELPATH = f"sd15/{_SHELF_LORA_FILENAME}"
_COMFY_LORA_NAME = f"characters/{_SHELF_LORA_FILENAME}"


@pytest.fixture
def lora_workflow(tmp_path, monkeypatch, workflow_env):
    """One workflow carrying both kinds of LoRA slot, and one adapter on the shelf.

    The shelf rows are written here and removed again, because the module's own
    re-seed only knows about the two models it seeds itself; a leftover adapter
    would be a second file for the next test's basename to match.

    ``comfy.info`` is what ComfyUI answers for ``object_info``: set it to a map,
    or to an exception for a ComfyUI that cannot be reached.
    """
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "{{image_path}}"}},
        "2": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "whatever-is-there.safetensors", "model": ["1", 0]},
        },
        "3": {
            "class_type": "PixlStashAdapterLoader",
            "inputs": {"adapter_sha256": "0" * 64, "model": ["2", 0]},
        },
        "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
    }
    bound, _changed = workflow_bindings.migrate_placeholders(graph)
    (tmp_path / "lora.json").write_text(json.dumps(bound), encoding="utf-8")
    _isolate_workflow_folders(tmp_path, monkeypatch)

    comfy = SimpleNamespace(
        info={
            "LoraLoader": {
                "input": {"required": {"lora_name": [[_COMFY_LORA_NAME], {}]}}
            }
        }
    )

    def fake_object_info(_url):
        if isinstance(comfy.info, Exception):
            raise comfy.info
        return comfy.info

    monkeypatch.setattr(comfyui_module, "fetch_object_info", fake_object_info)

    hub = workflow_env.server.hub
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, kind, filename, sha256, provenance) "
            "VALUES ('adapter', 'lora', ?, ?, 'scanned')",
            (_SHELF_LORA_FILENAME, _SHELF_LORA_SHA),
        )
        model_id = conn.execute(
            "SELECT id FROM model WHERE sha256 = ?", (_SHELF_LORA_SHA,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO model_folder (path, kind, movable) "
            "VALUES ('/home/me/loras', 'reference', 'fixed')"
        )
        folder_id = conn.execute(
            "SELECT id FROM model_folder WHERE path = '/home/me/loras'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state) "
            "VALUES (?, ?, ?, 'present')",
            (model_id, folder_id, _SHELF_LORA_RELPATH),
        )
    comfy.model_id = model_id
    try:
        yield comfy
    finally:
        with hub.transaction() as conn:
            conn.execute("DELETE FROM model_file WHERE model_id = ?", (model_id,))
            conn.execute("DELETE FROM model_folder WHERE id = ?", (folder_id,))
            conn.execute("DELETE FROM model WHERE id = ?", (model_id,))


@pytest.fixture
def loaderless_workflow(tmp_path, lora_workflow):
    """A checkpoint workflow with no LoRA loader, and a ComfyUI that types it (#1376)."""
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "{{image_path}}"}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1]}},
        "3": {
            "class_type": "KSampler",
            "inputs": {"model": ["4", 0], "positive": ["6", 0]},
        },
        "9": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }
    bound, _changed = workflow_bindings.migrate_placeholders(graph)
    (tmp_path / "plain.json").write_text(json.dumps(bound), encoding="utf-8")
    lora_workflow.info = {
        "LoadImage": {"output": ["IMAGE", "MASK"]},
        "CheckpointLoaderSimple": {"output": ["MODEL", "CLIP", "VAE"]},
        "CLIPTextEncode": {"output": ["CONDITIONING"]},
        "LoraLoader": {
            "input": {
                "required": {
                    "model": ["MODEL", {}],
                    "clip": ["CLIP", {}],
                    "lora_name": [[_COMFY_LORA_NAME], {}],
                    "strength_model": ["FLOAT", {"default": 1.0}],
                }
            },
            "output": ["MODEL", "CLIP"],
        },
    }
    return lora_workflow


def test_a_workflow_with_no_lora_loader_shows_where_one_would_go(
    workflow_env, loaderless_workflow
):
    owner = workflow_env.owner
    r = owner.get(f"{API}/comfyui/workflows/plain.json/lora-insertion")
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert r.json()["has_lora_loader"] is False and r.json()["reason"] is None
    assert plan["model"]["node_id"] == "4" and plan["clip"]["output"] == 1
    assert [(w["node_id"], w["field"]) for w in plan["rewires"]] == [
        ("6", "clip"),
        ("3", "model"),
    ]

    # The control: a workflow that has a loader needs no plan.
    r = owner.get(f"{API}/comfyui/workflows/lora.json/lora-insertion")
    assert r.status_code == 200 and r.json()["has_lora_loader"] is True, r.text
    assert r.json()["plan"] is None

    # A ComfyUI that cannot be asked says so rather than guessing a splice.
    loaderless_workflow.info = RuntimeError("Could not reach ComfyUI at example")
    r = owner.get(f"{API}/comfyui/workflows/plain.json/lora-insertion")
    assert r.status_code == 200 and r.json()["plan"] is None, r.text
    assert "could not ask ComfyUI" in r.json()["reason"]


def test_the_insertion_preview_is_owner_only(
    workflow_env, loaderless_workflow, monkeypatch
):
    """Measured at the gate, on the route under test, in both directions.

    The GET belts are emptied first: ``/api/v1/comfyui/workflows/`` is a
    READ-blocked prefix, so the middleware would answer 403 before routing and
    the declaration this test is named after could be loosened to ANY_TOKEN
    with the test still green. ``assert_real_route`` is the other half - a
    renamed or unmounted path 403s identically.
    """
    monkeypatch.setattr(auth, "READ_BLOCKED_GET_PATHS", frozenset())
    monkeypatch.setattr(auth, "READ_BLOCKED_GET_PREFIXES", ())
    path = f"{API}/comfyui/workflows/plain.json/lora-insertion"
    assert_real_route(workflow_env.server.api, "GET", path)
    token = _mint(
        workflow_env.owner,
        "lora insertion probe",
        resource_type="character",
        resource_id=workflow_env.character_id,
    )
    client = _bearer(workflow_env.server, token)
    assert client.get(f"{API}/pictures").status_code == 200, (
        "the scoped token is dead; the refusal below would prove nothing"
    )
    r = client.get(path)
    assert r.status_code == 403, r.text
    # The positive control: the owner still reads it, with the belts down.
    assert workflow_env.owner.get(path).status_code == 200


def _extensions_answer(monkeypatch, answer):
    """Stand ComfyUI's ``GET /extensions`` in for the node check."""

    def fake_get(url, timeout):
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: answer)

    monkeypatch.setattr(
        comfyui_service,
        "requests",
        SimpleNamespace(get=fake_get, RequestException=requests.RequestException),
    )


def test_the_node_check_is_owner_only(workflow_env, monkeypatch):
    """Both directions at the gate, with the GET belt emptied."""
    monkeypatch.setattr(auth, "READ_BLOCKED_GET_PATHS", frozenset())
    _extensions_answer(monkeypatch, [])
    path = f"{API}/comfyui/pixlstash-node"
    assert_real_route(workflow_env.server.api, "GET", path)
    token = _mint(
        workflow_env.owner,
        "node check probe",
        resource_type="character",
        resource_id=workflow_env.character_id,
    )
    client = _bearer(workflow_env.server, token)
    assert client.get(f"{API}/pictures").status_code == 200, (
        "the scoped token is dead; the refusal below would prove nothing"
    )
    assert client.get(path).status_code == 403
    assert workflow_env.owner.get(path).status_code == 200


@pytest.mark.parametrize(
    "answer, expected",
    [
        (["/extensions/ComfyUI-PixlStash/open_workflow.js"], True),
        (["/extensions/comfyui-pixlstash/js/open_workflow.js"], True),
        # The pack from before Open in ComfyUI, and another pack's same name.
        (["/extensions/ComfyUI-PixlStash/picker.js"], False),
        (["/extensions/other-pack/open_workflow.js"], False),
        (requests.ConnectionError("refused"), None),
        ({"not": "a list"}, None),
    ],
)
def test_the_node_check_reads_comfyuis_extensions(
    workflow_env, monkeypatch, answer, expected
):
    """Only the node's own open_workflow.js counts; unreachable is ``null``."""
    _extensions_answer(monkeypatch, answer)
    r = workflow_env.owner.get(f"{API}/comfyui/pixlstash-node")
    assert r.status_code == 200, r.text
    assert r.json() == {"can_open_workflows": expected}


def test_the_workflow_list_says_which_files_have_a_lora_loader(
    workflow_env, loaderless_workflow
):
    """The menus that run a workflow read the list, not a request per file."""
    r = workflow_env.owner.get(f"{API}/comfyui/workflows")
    assert r.status_code == 200, r.text
    slots = {w["name"]: w.get("lora_slots") for w in r.json()["workflows"]}
    assert [s["node_id"] for s in slots["lora.json"]] == ["2", "3"]
    # The control: a file with no loader says so with an empty list rather
    # than by leaving the field out.
    assert slots["plain.json"] == []


def test_a_share_link_sees_no_lora_filenames_on_the_workflow_list(
    workflow_env, lora_workflow
):
    """The list is open to share tokens, and a slot's value is the owner's inventory.

    /models/ and /adapters/ keep the model inventory from those tokens, so the
    list must not hand the same filenames and digests out through its slots.
    The scoped token still reads the list, because over-blocking is its own
    regression.
    """
    server = workflow_env.server
    token = _mint(
        workflow_env.owner,
        "lora slot probe",
        resource_type="character",
        resource_id=workflow_env.character_id,
    )
    client = _bearer(server, token)
    r = client.get(f"{API}/comfyui/workflows")
    assert r.status_code == 200, r.text
    row = next(w for w in r.json()["workflows"] if w["name"] == "lora.json")
    assert [s["node_id"] for s in row["lora_slots"]] == ["2", "3"]
    assert all("value" not in slot for slot in row["lora_slots"])
    assert "whatever-is-there.safetensors" not in r.text
    assert "0" * 64 not in r.text


# ===========================================================================
# The cards (v1.12 B3)
# ===========================================================================
#
# **The payload shape is not this file's to choose.** `GET /workflows` is
# consumed by `frontend/src/utils/workflowCard.js`, which is already merged and
# documents the card at the top of the file, and `docs/frontend_architecture.md`
# guarantees it needs no mapping layer. The first test below pins the field
# names against that document; the rest pin the arithmetic behind them.
#
# Every figure is stated as a number rather than as a relation, because a
# relation ("the busy card ranks higher") stays true when the formula is wrong.
# The library's ratings are 5, 4, 0 and 2, of which three are stars somebody
# put there, so the library mean is 11/3 - deliberately NOT a round number and
# deliberately not equal to any single card's mean, so a constant standing in
# for it cannot pass.

_LIBRARY_MEAN = (5 + 4 + 2) / 3
_BUSY_RANK = (5 * _LIBRARY_MEAN + 9) / (5 + 2)
_FORGOTTEN_RANK = (5 * _LIBRARY_MEAN + 2) / (5 + 1)

# What `workflowCard.js` reads off a card. Held here as data so a field that
# quietly stops being served fails one named assertion rather than whichever
# test happened to touch it.
_CONTRACT_FIELDS = {
    "id",
    "name",
    "type",
    "imported",
    "models",
    "loras",
    "picture_count",
    "rating",
    "covers",
    "saved_recipe_count",
    "defaults",
    # #1623: every topology, and the graph a run starts from.
    "base_topology",
    "topologies",
    "recipe_values",
    "default_recipe",
}

# What the cut-over (#1623) took off the card: the stack a card sat in, the
# per-slot mark, and the recipe-LoRA pile that `recipe_values` replaces. Named
# so that one of them quietly coming back fails an assertion.
_REMOVED_FIELDS = {
    "key",
    "member_keys",
    "members",
    "stack_id",
    "stack_size",
    "differs_by",
    "differs_by_detail",
    "recipe_loras",
    "topology_hash",
}


def _cards(owner, query: str = "") -> dict:
    payload = owner.get(f"{API}/workflows{query}")
    assert payload.status_code == 200, payload.text
    return payload.json()


def _by_key(payload) -> dict:
    return {card["id"]: card for card in payload["cards"]}


def _detail(owner, workflow_id) -> dict:
    r = owner.get(f"{API}/workflows/{workflow_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _slot_label(structural_hash: str, node_id: str) -> str:
    """One node's slot label, as an override addresses it.

    Derived rather than written down: the label is a Weisfeiler-Leman digest
    over the whole graph, so adding a node to a fixture document changes every
    one of them and a literal would pin the wrong slot silently.
    """
    return topology_node_labels(_DOCUMENTS[structural_hash])[node_id]


def _picture_ids_by_path(server) -> dict[str, int]:
    def read(session):
        return {
            picture.file_path: picture.id
            for picture in session.exec(select(Picture)).all()
        }

    return server.vault.db.run_task(read, priority=DBPriority.IMMEDIATE)


def _cover_urls(card) -> list[str]:
    """The strip's URLs alone, which is what the ordering tests are about.

    A cover is an object since #1465 -- the URL plus the crop rectangle a
    client frames it by -- and the crop has its own test below.
    """
    return [cover["url"] for cover in card["covers"]]


def test_a_card_is_served_in_the_shape_the_frontend_already_reads(workflow_env):
    """The merged contract, field by field.

    `utils/workflowCard.js` is shipped and `frontend_architecture.md` promises
    it needs no mapping layer, so a renamed field here is a broken screen
    rather than a caller to update. One entry per workflow (#1623): the stack,
    the per-slot mark and the recipe-LoRA pile are gone, and a client reading
    one of them would be reading something no longer true.
    """
    card = _by_key(_cards(workflow_env.owner))[BUSY_WF]
    assert _CONTRACT_FIELDS <= set(card), _CONTRACT_FIELDS - set(card)
    assert not _REMOVED_FIELDS & set(card), _REMOVED_FIELDS & set(card)
    assert card["id"] == BUSY_WF
    assert card["base_topology"] == BUSY_TOPOLOGY
    assert card["topologies"] == [BUSY_TOPOLOGY]
    # Too dear per grid entry, so it is served on the detail route only.
    assert card["default_recipe"] is None
    assert card["type"] == "txt2img"
    assert card["saved_recipe_count"] == 0
    assert [model["kind"] for model in card["models"]] == ["checkpoint"]
    # The DERIVED name, which is what the field promises: the extension and any
    # quant postfix are off it, and `quant` carries the precision instead.
    assert card["models"][0]["name"] == _SHELF_DERIVED
    assert card["models"][0]["quant"] is None
    # One LoRA slot, an anonymous slot rather than a named file: which LoRA
    # fills it is the recipe's business, and `recipe_values` says which did.
    lora_label = next(
        slot.label for slot in slots(_DOCUMENTS[BUSY_RECIPE_A]) if slot.is_lora
    )
    #
    # `title` is null here and that is the state, not an omission: the LoRA is
    # not on the shelf. It is served on every slot all the same, because the
    # card's name row is built from it and a client showing `name` instead
    # would describe one model twice (#1416).
    # `icon` and `base_model` are the shelf's picture and the colour a generated
    # mark takes from it (#1466): null on this slot for the same reason `title`
    # is, and served on every slot so a card that has to draw itself out of its
    # models rather than its pictures needs no second request to do it.
    assert card["loras"] == [
        {
            "name": None,
            "title": None,
            "icon": None,
            "base_model": None,
            "base_model_folded": None,
            # No file, so no shelf row to name or to read a family off.
            "sha256": None,
            "base_model_family": None,
            "kind": "lora",
            # A recipe slot names no file at all, so there is nothing to read a
            # precision off either - and nothing is what it serves.
            "quant": None,
            "slot_label": lora_label,
            "filename": None,
        }
    ]


def test_a_card_says_when_it_was_last_used_so_the_grid_can_sort_by_it(
    workflow_env,
):
    """`last_used`, in the same ISO spelling `/workflows` already serves.

    The Workflows grid (F1a) offers *Recently used* beside *Your ratings* and
    *Picture count*, and nothing else on the card carries it: `rank` is a
    smoothed rating and `covers` is an order, not a date. A card whose pictures
    are all binned has no last use and must read null rather than an epoch,
    which would sort it as the oldest workflow in the library instead of one
    with nothing to date.
    """
    cards = _by_key(_cards(workflow_env.owner))
    assert cards[BUSY_WF]["last_used"].startswith("2026-08-13")
    # BINNED is dropped from the grid (its pictures are all in the scrapheap),
    # so its null is asserted where it is still served: its own detail route.
    assert _detail(workflow_env.owner, BINNED_WF)["card"]["last_used"] is None


def test_a_card_is_never_nameless(workflow_env):
    """`name` may not be null, and most cards have no name of their own.

    It is written only on an explicit rename, so null is the dominant case
    rather than an edge -- and it is the card's only identifying text row,
    while the ⓘ panel puts it straight into an ``aria-label``. Served as null
    the row renders empty and the label reads "About null".

    The fallback is the workflow file that runs the card, without its
    extension; then a description built from what the card loads; and only a
    card with none of those gets the stand-in.

    **The built one exists because the stand-in used to be the common case.**
    A name is written only on an explicit rename and most cards come from
    pictures rather than a dropped file, so a whole grid read "Untitled
    workflow" and the one identifying row identified nothing.
    """
    cards = _by_key(_cards(workflow_env.owner))
    # BUSY has no name and no file, so it is named for what it loads - and by
    # the name the SHELF has for that model rather than by the file's spelling
    # (#1454). The stem would read `realvisxl`; the row says `Krea 2`, and the
    # two are in the same database, so this is a join and not a derivation.
    assert cards[BUSY_WF]["name"] == f"{_SHELF_TITLE}: Text to Image"
    assert _SHELF_FILENAME.startswith("realvisxl")  # ... and the file is not it
    # The seeded documents carry no post-processing, and `[]` says so. It is
    # not `null`, which would mean nothing had looked.
    assert cards[BUSY_WF]["specials"] == []

    # **`type_label` is SERVED, not mirrored.** The card shows its type twice -
    # in a generated name and in its own chip - and a second copy of these
    # labels on the client is the drift `CHECKPOINT_WIDGETS` was written to
    # end. One map, on the wire, so the two strings are equal by construction.
    busy = cards[BUSY_WF]
    assert busy["type"] == "txt2img"
    assert busy["type_label"] == "Text to Image"
    assert busy["type_label"] in busy["name"]

    # The stand-in is still the floor, for a card with nothing to be named
    # after: no name, no file, and every model name forgotten. Asserted on the
    # helper, because the fixture has no such card and inventing one to prove a
    # two-line branch costs more than it tells anybody.
    #
    # The REAL `Card` and the real `SlotModel`, not a stub with the three
    # attributes this branch happens to read: a duck that grows an attribute
    # only when a test remembers to add it is how a name built from a field
    # nothing supplies still passes.
    def _nameless(workflow_type=None, specials=None):
        return Card(
            workflow_key="k",
            topology_hash="t",
            workflow_type=workflow_type,
            specials=specials,
        )

    assert workflows_routes._display_name(_nameless(), []) == UNNAMED_CARD
    # Nothing to name it after but what it does: its type, and its extras.
    assert (
        workflows_routes._display_name(_nameless("upscale", ("face_detailer",)), [])
        == "Upscale + FaceDetailer"
    )

    # A graph whose names survive but which loads no checkpoint still gets a
    # name: the first slot it does load.
    unet_only = _nameless("txt2img")

    only = [SlotModel(name="flux1-dev.safetensors", kind="unet")]
    assert workflows_routes._display_name(unet_only, only) == "flux1-dev: Text to Image"

    # **The base model names the card, and nothing else may.** A Flux or SD3
    # graph has no `checkpoint` kind at all - only `unet` - and slot order is
    # document order, so a fallback of "the first slot with a name" named such
    # a card after its VAE or one of its text encoders. Nobody calls a
    # workflow by its VAE.
    # Built the way `_describe_slots` now builds them: `name` is derived, so
    # these are the strings `_display_name` actually receives.
    flux = [
        SlotModel(name="ae", kind="vae"),
        SlotModel(name="t5xxl", kind="clip"),
        SlotModel(name="flux1 dev", kind="unet"),
    ]
    assert workflows_routes._display_name(unet_only, flux) == "flux1 dev: Text to Image"

    # A graph that loads a VAE and an upscaler but no base model at all is
    # named for what it does rather than after either.
    accessories = [
        SlotModel(name="ae.safetensors", kind="vae"),
        SlotModel(name="4x-UltraSharp.pth", kind="upscale"),
    ]
    assert (
        workflows_routes._display_name(_nameless("upscale"), accessories) == "Upscale"
    )

    # **An empty stem is as nameless as a null one.** These are graph widget
    # values - third-party strings out of whatever workflow was imported - so
    # a name that is nothing but an extension, or that ends in a separator,
    # reaches here and would render the row blank and read "About null".
    for hostile in (".safetensors", "SDXL/", "loras\\"):
        hostile_slots = [SlotModel(name=hostile, kind="checkpoint")]
        assert (
            workflows_routes._display_name(unet_only, hostile_slots) == "Text to Image"
        )

    # A checkpoint outranks a unet where a graph carries both.
    both = [
        SlotModel(name="flux1-dev.safetensors", kind="unet"),
        SlotModel(name="juggernautXL.safetensors", kind="checkpoint"),
    ]
    assert (
        workflows_routes._display_name(unet_only, both) == "juggernautXL: Text to Image"
    )

    # **Two loaders of one kind are both the model** (a Wan 2.2 high + low
    # noise pair), shelf title first, and two loaders of ONE file read once.
    # Never which is high and which low: the set is right, the slot order not.
    wan = [
        SlotModel(name="wan2.2_t2v_high_noise_14B", kind="unet", title="Wan 2.2 High"),
        SlotModel(name="umt5_xxl", kind="clip"),
        SlotModel(name="wan2.2_t2v_low_noise_14B", kind="unet"),
        SlotModel(name="wan2.2_t2v_high_noise_14B", kind="unet", title="Wan 2.2 High"),
    ]
    assert (
        workflows_routes._display_name(unet_only, wan)
        == "Wan 2.2 High + wan2.2_t2v_low_noise_14B: Text to Image"
    )
    # A graph that saves a video is a Video workflow, and says so.
    assert (
        workflows_routes._display_name(_nameless("video"), wan)
        == "Wan 2.2 High + wan2.2_t2v_low_noise_14B: Video"
    )

    # **The shelf's name for the model beats the file's spelling** (#1454).
    # `realvisxl` is a filename stem; `Krea 2` is what the trainer wrote in the
    # header or what the owner typed, and it is in the same database as the
    # card, so this is a join rather than a derivation.
    titled = [
        SlotModel(name="realvisxl_v50.safetensors", kind="checkpoint", title="Krea 2")
    ]
    assert workflows_routes._display_name(unet_only, titled) == "Krea 2: Text to Image"
    # Free text off a safetensors header: whitespace is not a name, and taking
    # it would render the row blank exactly as an empty stem would.
    blank = [
        SlotModel(name="realvisxl_v50.safetensors", kind="checkpoint", title="   ")
    ]
    assert (
        workflows_routes._display_name(unet_only, blank)
        == "realvisxl_v50: Text to Image"
    )

    # **The post-processing half** (#1454). `[]` and `None` both produce no
    # suffix, but they are different facts and only `[]` is a claim.
    assert (
        workflows_routes._display_name(_nameless("txt2img", (FACE_DETAILER,)), titled)
        == "Krea 2: Text to Image + FaceDetailer"
    )
    assert (
        workflows_routes._display_name(
            _nameless("txt2img", (UPSCALE, FACE_DETAILER)), titled
        )
        == "Krea 2: Text to Image + Upscale + FaceDetailer"
    )
    for no_suffix in ((), None):
        assert (
            workflows_routes._display_name(_nameless("txt2img", no_suffix), titled)
            == "Krea 2: Text to Image"
        )
    # A group that IS the type is left off rather than said twice.
    assert (
        workflows_routes._display_name(_nameless("upscale", (UPSCALE,)), titled)
        == "Krea 2: Upscale"
    )
    # A value a newer build wrote is dropped rather than printed raw: the name
    # row is the card's only identifying text.
    assert (
        workflows_routes._display_name(_nameless("txt2img", ("teleportation",)), titled)
        == "Krea 2: Text to Image"
    )
    # **A card with no recognised type still takes the suffix**, on the bare
    # stem: `workflow_type` is null for any graph matching none of its five
    # shapes, and "Krea 2 + Upscale" is the honest name for one - the model,
    # and what it adds.
    assert (
        workflows_routes._display_name(_nameless(None, (UPSCALE,)), titled)
        == "Krea 2 + Upscale"
    )

    # The suffix is on the GENERATED name only. A card named after its workflow
    # file keeps the owner's spelling untouched.
    filed = Card(
        workflow_key="k",
        topology_hash="t",
        workflow_type="txt2img",
        specials=(FACE_DETAILER,),
        file_name="Flux2 portrait.json",
    )
    assert workflows_routes._display_name(filed, titled) == "Flux2 portrait"
    # The hidden card has both a name and a file, and the owner's name wins.
    assert _detail(workflow_env.owner, HIDDEN_WF)["card"]["name"] == (
        "A workflow I hid"
    )
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_file "
            "(workflow_name, topology_hash, structural_hash, workflow_key) "
            "VALUES ('Flux2 portrait.json', ?, ?, ?)",
            (BUSY_TOPOLOGY, BUSY_RECIPE_A, BUSY_CARD),
        )
    named = _by_key(_cards(workflow_env.owner))[BUSY_WF]
    assert named["name"] == "Flux2 portrait"


def test_a_card_never_calls_one_model_two_different_things(workflow_env):
    """The name row and every chip under it read the same name for one model.

    The generated name is built from the SHELF's name for the base model, so a
    payload carrying only the filename would have the card read `Krea 2: Text
    to Image` over a chip reading `realvisxl.safetensors` - one model described
    twice, on one card, and spoken that way to a screen reader. That is exactly
    the pair that drifted in #1416, which is why the shelf's name is SERVED on
    the slot rather than left for a client to look up.
    """
    card = _by_key(_cards(workflow_env.owner))[BUSY_WF]
    checkpoint = next(slot for slot in card["models"] if slot["kind"] == "checkpoint")
    assert checkpoint["name"] == _SHELF_DERIVED
    assert checkpoint["title"] == _SHELF_TITLE
    # The name row was built from the title, so the title is what a client has
    # to be able to show beside it.
    assert checkpoint["title"] in card["name"]
    assert checkpoint["name"] not in card["name"]

    # A model the shelf does not know carries no title at all, and the client
    # falls back to the filename - which is also what the name row does.
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET display_name = NULL WHERE filename = ?",
            (_SHELF_FILENAME,),
        )
    plain = _by_key(_cards(workflow_env.owner))[BUSY_WF]
    plain_ckpt = next(s for s in plain["models"] if s["kind"] == "checkpoint")
    assert plain_ckpt["title"] is None
    assert plain["name"] == "realvisxl: Text to Image"


def test_a_card_names_itself_without_the_precision(workflow_env):
    """The name row is built from the slot, so it loses the postfix too.

    BUSY has no stored file, so `_display_name` really does reach its base
    model - which is what makes this an assertion rather than the tautology it
    would be on a card named after `something.json`. Two things have to move
    for the derived name to be what answers: the RECIPE's asset value, which
    is the string the slot carries, and the shelf's own title, which would
    otherwise answer first.
    """
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_recipe_asset SET normalized_filename = ? "
            "WHERE normalized_filename = ?",
            ("realvisxl_fp8_e4m3fn.safetensors", _SHELF_FILENAME),
        )
        conn.execute(
            "UPDATE model SET display_name = NULL WHERE filename = ?",
            (_SHELF_FILENAME,),
        )

    card = _by_key(_cards(workflow_env.owner))[BUSY_WF]
    # The postfix is gone from both the chip and the row built out of it.
    assert card["models"][0]["name"] == _SHELF_DERIVED
    assert card["models"][0]["quant"] == "fp8_e4m3"
    assert card["name"] == "realvisxl: Text to Image"


def test_a_shelf_loader_slot_is_named_by_its_file_not_its_id(workflow_env):
    """A `checkpoint_id` holds a shelf row id, never a name (#1721).

    The slot serves the shelf file's derived name beside the shelf title, and
    an id the shelf no longer holds says so rather than reading as a model
    called "75": its name is null, the forgotten-model state.
    """
    hub = workflow_env.server.hub
    model_id = hub.fetchone(
        "SELECT id FROM model WHERE filename = ?", (_SHELF_FILENAME,)
    )["id"]
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_recipe_asset SET widget_name = 'checkpoint_id', "
            "normalized_filename = ? WHERE normalized_filename = ?",
            (str(model_id), _SHELF_FILENAME),
        )
        conn.execute(
            "UPDATE workflow_topology_core SET slots = replace(slots, "
            '\'"widget": "ckpt_name"\', \'"widget": "checkpoint_id"\') '
            "WHERE topology_hash = ?",
            (BUSY_TOPOLOGY,),
        )

    checkpoint = _by_key(_cards(workflow_env.owner))[BUSY_WF]["models"][0]
    assert (checkpoint["kind"], checkpoint["name"], checkpoint["title"]) == (
        "checkpoint",
        _SHELF_DERIVED,
        _SHELF_TITLE,
    )
    # The slot keeps the value its recipe recorded, which is what the default
    # recipe's `filename` matches it by.
    assert checkpoint["filename"] == str(model_id)
    # The variant keeps the id it recorded, and says which file it names.
    assets = [
        asset
        for variant in _detail(workflow_env.owner, BUSY_WF)["variants"]
        for asset in variant["assets"]
        if asset["widget"] == "checkpoint_id"
    ]
    assert assets
    assert {(a["name"], a["shelf_filename"]) for a in assets} == {
        (str(model_id), _SHELF_FILENAME)
    }

    with hub.transaction() as conn:
        conn.execute("DELETE FROM model WHERE id = ?", (model_id,))
    gone = _by_key(_cards(workflow_env.owner))[BUSY_WF]["models"][0]
    assert gone["name"] is None
    assert gone["title"] is None


def test_a_card_says_the_post_processing_it_carries_and_when_it_cannot(
    workflow_env,
):
    """`specials` on the wire, and the two answers null and `[]` keep apart.

    A card whose document was cached by a build predating the column has NOT
    said "no post-processing"; it has said nothing. Serving `[]` there would
    have the grid assert a fact about every workflow in a library that has not
    finished its backfill, and the name would drop a `+ FaceDetailer` that is
    really there.
    """
    hub = workflow_env.server.hub
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_topology_core SET specials = ? WHERE topology_hash = ?",
            (FACE_DETAILER, BUSY_TOPOLOGY),
        )
    card = _by_key(_cards(workflow_env.owner))[BUSY_WF]
    assert card["specials"] == [FACE_DETAILER]
    assert card["name"] == f"{_SHELF_TITLE}: Text to Image + FaceDetailer"

    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_topology_core SET specials = NULL WHERE topology_hash = ?",
            (BUSY_TOPOLOGY,),
        )
    unknown = _by_key(_cards(workflow_env.owner))[BUSY_WF]
    assert unknown["specials"] is None
    # No suffix, because a name has nowhere to say "not known yet" - but the
    # payload does, and it did.
    assert unknown["name"] == f"{_SHELF_TITLE}: Text to Image"


def test_the_shelf_is_asked_for_a_model_s_name_by_all_three_things_a_card_holds(
    workflow_env,
):
    """the title rule over filename, digest and shelf id, and the ambiguity.

    A card's slot name is whichever of those three `structural_widget_value`
    kept, and the card cannot say which - so a lookup that guessed one column
    would silently miss every graph using the other two.
    """
    hub = workflow_env.server.hub
    row = hub.fetchone(
        "SELECT id, sha256 FROM model WHERE filename = ?", (_SHELF_FILENAME,)
    )
    # All three name the same model, so all three resolve to the same title.
    found = _titles(hub, [_SHELF_FILENAME, row["sha256"], str(row["id"])])
    assert found == {
        _SHELF_FILENAME: _SHELF_TITLE,
        row["sha256"]: _SHELF_TITLE,
        str(row["id"]): _SHELF_TITLE,
    }
    # **And the SHORT hash A1111 writes**, which is a prefix of that digest
    # rather than the digest. `structural_widget_value` keeps 10 and 12 hex
    # characters as readily as 64, so a lookup matching only the long form
    # leaves every A1111-sourced card wearing a raw hex blob for a name.
    assert _titles(hub, [row["sha256"][:10]]) == {row["sha256"][:10]: _SHELF_TITLE}
    assert _titles(hub, [row["sha256"][:12]]) == {row["sha256"][:12]: _SHELF_TITLE}
    # A model the shelf has never scanned has no entry, which is how a card
    # keeps its filename stem rather than being renamed after something else.
    assert _titles(hub, ["add_detail.safetensors"]) == {}
    assert _titles(hub, []) == {}
    # A digest names only the model that carries it, never the row beside it.
    assert _titles(hub, [_h("nothing-on-this-shelf")]) == {}

    # The rest mutates the shelf, which this module shares: the autouse reset
    # deletes the seeded row BY FILENAME, so a renamed one survives it and the
    # next insert of the same digest fails the UNIQUE. Undone in `finally`
    # rather than left to the reset.
    second = _h("a-second-realvisxl")
    try:
        # The stored name is a lowercased basename; the shelf keeps the file's
        # own spelling. Matching has to ignore the case or every capitalised
        # model on the shelf is invisible to a card.
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE model SET filename = 'RealVisXL.safetensors' WHERE id = ?",
                (row["id"],),
            )
        assert _titles(hub, [_SHELF_FILENAME]) == {_SHELF_FILENAME: _SHELF_TITLE}

        # **A SECOND COPY under a different spelling resolves too.**
        # `model.filename` is frozen at first sight while `model_file` holds a
        # row per copy, so the same checkpoint sitting in an archive under a
        # longer name is a name only the location table knows.
        with hub.transaction() as conn:
            folder = conn.execute(
                "INSERT INTO model_folder (path, kind, movable) "
                "VALUES ('/home/me/models', 'checkpoint', 'no')"
            ).lastrowid
            conn.execute(
                "INSERT INTO model_file (model_id, model_folder_id, relpath, state) "
                "VALUES (?, ?, 'SDXL/RealVisXL_v5.0.safetensors', 'present')",
                (row["id"], folder),
            )
        assert _titles(hub, ["realvisxl_v5.0.safetensors"]) == {
            "realvisxl_v5.0.safetensors": _SHELF_TITLE
        }

        # **Two rows claiming one filename with different titles is dropped,
        # not resolved.** Naming a card after the wrong model is worse than
        # naming it after its file, and nothing here could tell the two apart.
        with hub.transaction() as conn:
            conn.execute(
                "INSERT INTO model (file_kind, filename, sha256, display_name, "
                "provenance) VALUES ('checkpoint', 'RealVisXL.safetensors', ?, "
                "'Something Else', 'scanned')",
                (second,),
            )
        assert _titles(hub, [_SHELF_FILENAME]) == {}
        # **An UNNAMED rival is an ambiguity too, and this is the one a filter
        # on `display_name` in SQL hides**: the rival never reaches the check,
        # so the named row wins by default and a card using the unnamed file is
        # titled after a model it did not use.
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE model SET display_name = NULL WHERE sha256 = ?", (second,)
            )
        assert _titles(hub, [_SHELF_FILENAME]) == {}
        # ... while two rows agreeing are not an ambiguity at all.
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE model SET display_name = ? WHERE sha256 = ?",
                (_SHELF_TITLE, second),
            )
        assert _titles(hub, [_SHELF_FILENAME]) == {_SHELF_FILENAME: _SHELF_TITLE}
    finally:
        with hub.transaction() as conn:
            conn.execute("DELETE FROM model_file WHERE model_id = ?", (row["id"],))
            conn.execute("DELETE FROM model_folder WHERE path = '/home/me/models'")
            conn.execute("DELETE FROM model WHERE sha256 = ?", (second,))
            conn.execute(
                "UPDATE model SET filename = ? WHERE id = ?",
                (_SHELF_FILENAME, row["id"]),
            )


def test_generated_names_that_collide_are_numbered():
    """No two workflows print one generated name; an owner's or a file's is theirs.

    Numbered in id order, so a workflow keeps its number from one read to the
    next whatever order the grid ranks them in.
    """
    plain = SlotModel(name="realvisxl", kind="checkpoint")

    def figure(key, **card):
        return WorkflowFigures(
            card=Card(
                workflow_key=key, topology_hash=key, workflow_type="txt2img", **card
            ),
            workflow=Workflow(key, topologies=[key], base_topology=key),
            models=[plain],
        )

    figures = [
        figure("d" * 64),
        figure("a" * 64),
        figure("c" * 64, name="Portrait"),
        figure("b" * 64, name="Portrait"),
        figure("e" * 64, file_name="realvisxl: Text to Image.json"),
        figure("f" * 64),
    ]
    names = workflows_routes._display_names(figures)
    assert names == {
        "a" * 64: "realvisxl: Text to Image",
        "d" * 64: "realvisxl: Text to Image (2)",
        "f" * 64: "realvisxl: Text to Image (3)",
        "b" * 64: "Portrait",
        "c" * 64: "Portrait",
        "e" * 64: "realvisxl: Text to Image",
    }
    # The entry reads its name from the same map.
    card = workflows_routes._entry(figures[0], names=names)
    assert card.name == "realvisxl: Text to Image (2)"


def test_colliding_generated_names_say_how_they_differ_before_numbering():
    """#1722: a generated name that collides says what its core does that the
    others do not, and only what still collides is numbered.

    A trait every colliding workflow shares tells none of them apart and is
    left off; a workflow with no traits read yet claims none.
    """
    plain = SlotModel(name="realvisxl", kind="checkpoint")

    def figure(key, traits):
        return WorkflowFigures(
            card=Card(
                workflow_key=key,
                topology_hash=key,
                workflow_type="txt2img",
                traits=traits,
            ),
            workflow=Workflow(key, topologies=[key], base_topology=key),
            models=[plain],
        )

    figures = [
        figure("a" * 64, ("negative_prompt",)),
        figure("b" * 64, ("two_pass", "model_per_pass", "negative_prompt")),
        figure("c" * 64, ("refine", "likeness_gate", "negative_prompt")),
        figure("d" * 64, ("refine", "negative_prompt")),
        figure("e" * 64, ("negative_prompt", "references:2")),
        figure("f" * 64, ("negative_prompt",)),
        figure("g" * 64, None),
        # A trait a newer build wrote is left out rather than printed raw.
        figure("h" * 64, ("teleportation", "negative_prompt")),
    ]
    base = "realvisxl: Text to Image"
    assert workflows_routes._display_names(figures) == {
        "a" * 64: base,
        "b" * 64: f"{base} + Two-Pass + Model per Pass",
        "c" * 64: f"{base} + Refine + Likeness Gate",
        "d" * 64: f"{base} + Refine",
        "e" * 64: f"{base} + 2 References",
        "f" * 64: f"{base} (2)",
        "g" * 64: f"{base} (3)",
        "h" * 64: f"{base} (4)",
    }

    # A workflow whose name does not collide is never lengthened.
    alone = [figure("a" * 64, ("two_pass", "negative_prompt"))]
    assert workflows_routes._display_names(alone) == {"a" * 64: base}

    # A shared trait is news once one of them lacks it: the Klein T2I with a
    # typed negative beside the one that zeroes its prompt.
    pair = [figure("a" * 64, ()), figure("b" * 64, ("negative_prompt",))]
    assert workflows_routes._display_names(pair) == {
        "a" * 64: base,
        "b" * 64: f"{base} + Negative Prompt",
    }


def test_the_grid_is_one_entry_per_workflow_not_one_per_variant(workflow_env):
    """A workflow is the unit, and BUSY's two variants are one entry, not two.

    The hidden workflow and the one-off are the grid's two exclusions and are
    asserted by name rather than by a count, so the test cannot pass because
    something else went missing.
    """
    payload = _cards(workflow_env.owner)
    cards = _by_key(payload)
    assert set(cards) == {BUSY_WF, FORGOTTEN_WF}
    assert cards[BUSY_WF]["variant_count"] == 2
    assert cards[BUSY_WF]["base_topology"] == BUSY_TOPOLOGY
    assert cards[FORGOTTEN_WF]["variant_count"] == 1


# An **editor-format** workflow (#1466): the format ComfyUI saves by default,
# which names its widget values by POSITION and so is filed as a topology with
# no recipe at all. `_SHELF_FILENAME` is the one model the seed puts on the
# shelf and `add_detail.safetensors` is deliberately NOT on it (it is the
# module's model ghost), so one name here resolves to a shelf row and the
# other cannot - which is what makes a null `title` mean something.
_EDITOR_UNRESOLVED = "add_detail.safetensors"
_EDITOR_WORKFLOW = {
    "nodes": [
        {
            "id": 1,
            "type": "UNETLoader",
            "inputs": [],
            "outputs": [{"name": "MODEL", "links": [1]}],
            "widgets_values": [_SHELF_FILENAME],
        },
        {
            "id": 2,
            "type": "LoraLoader",
            "inputs": [{"name": "model", "link": 1}],
            "outputs": [{"name": "MODEL", "links": [2]}],
            "widgets_values": [_EDITOR_UNRESOLVED, 1.0, 1.0],
        },
        {
            "id": 3,
            "type": "SaveImage",
            "inputs": [{"name": "images", "link": 2}],
            "outputs": [],
            "widgets_values": ["out"],
        },
    ],
    "links": [[1, 1, 0, 2, 0, "*"], [2, 2, 0, 3, 0, "*"]],
}
_EDITOR_ICON = _h("editor-card-icon")
# A base model spelled the way a safetensors header spells it, so `fold` has a
# real fold to do rather than passing a canonical label through untouched.
_EDITOR_BASE_MODEL = "flux.1-dev"
_EDITOR_BASE_MODEL_FOLDED = fold(_EDITOR_BASE_MODEL)
_EDITOR_FAMILY = family_of(_EDITOR_BASE_MODEL)


def _file_a_workflow(server, tmp_path, monkeypatch, name, workflow) -> str:
    """Import *workflow* as the import route does: a manual workflow. Its id.

    ``tmp_path`` and ``monkeypatch`` isolate the workflow folders, which no
    manual workflow reads: a test that finds a file there has found a bug.
    """
    monkeypatch.setattr(
        comfyui_module, "_workflow_dirs", lambda: [("user", str(tmp_path))]
    )
    workflows_routes._manual_model_widgets.cache_clear()
    return comfyui_module.store_manual_workflow(
        server.hub, name.removesuffix(".json"), workflow, "import"
    )


def _give_the_shelf_model_a_picture(server) -> None:
    """Put a chosen picture and a base model on the seeded shelf row.

    Both re-seeded before the next test. The base model is a string
    `known_base_models` recognises, so `fold` has something to fold: an
    unrecognised one folds to null and would leave the whole wiring saying
    nothing.
    """
    with server.hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET icon_sha256 = ?, base_model = ? WHERE filename = ?",
            (_EDITOR_ICON, _EDITOR_BASE_MODEL, _SHELF_FILENAME),
        )


def test_an_editor_format_file_is_a_card_of_its_own(
    workflow_env, tmp_path, monkeypatch
):
    """#1466, now a manual workflow: an imported editor document is on the
    grid as a workflow of its own, with no topology and no recipe."""
    key = _file_a_workflow(
        workflow_env.server, tmp_path, monkeypatch, "editor.json", _EDITOR_WORKFLOW
    )

    cards = _by_key(_cards(workflow_env.owner))
    assert set(cards) == {BUSY_WF, FORGOTTEN_WF, key}
    card = cards[key]
    assert key.startswith("manual:")
    assert (card["base_topology"], card["topologies"]) == (None, [])
    assert (card["variant_count"], card["picture_count"]) == (0, 0)
    assert (card["imported"], card["manual"], card["name"]) == (True, True, "editor")
    # And it opens on its own route, which is what every write answers with.
    assert _detail(workflow_env.owner, key)["card"]["id"] == key


def test_an_editor_format_cards_models_are_read_off_its_own_file(
    workflow_env, tmp_path, monkeypatch
):
    """The models of a card that has no recipe to read them from (#1466).

    Recovered from the file's own widget values, each resolved against the
    model shelf. The two names are chosen to run both sides of that: the UNET
    is the seeded shelf model and carries its title and its picture, the LoRA
    is this module's model ghost and carries neither.
    """
    _give_the_shelf_model_a_picture(workflow_env.server)
    key = _file_a_workflow(
        workflow_env.server, tmp_path, monkeypatch, "editor.json", _EDITOR_WORKFLOW
    )

    card = _by_key(_cards(workflow_env.owner))[key]
    # `unet`, not `checkpoint`: the recovery keeps the widget each name came
    # off, and a Flux or Z-Image graph carries no checkpoint at all.
    # `base_model` is the shelf's raw column and `base_model_folded` its
    # canonical label: a client hashes a generated mark's colour out of
    # `folded or raw`, so the same model has to arrive here spelled the way it
    # arrives on the shelf or one file gets two colours in two places.
    assert _EDITOR_BASE_MODEL_FOLDED not in (None, _EDITOR_BASE_MODEL)
    assert _EDITOR_FAMILY is not None
    assert card["models"] == [
        {
            "name": _SHELF_DERIVED,
            "title": _SHELF_TITLE,
            "icon": _EDITOR_ICON,
            "base_model": _EDITOR_BASE_MODEL,
            "base_model_folded": _EDITOR_BASE_MODEL_FOLDED,
            # Which shelf file it is, and the family the shelf serves for it,
            # so a client can check a character's LoRA against this workflow
            # without a second read.
            "sha256": _h("realvisxl-digest"),
            "base_model_family": _EDITOR_FAMILY,
            "kind": "unet",
            # Null and not an empty string: neither the shelf's column nor the
            # filename records a precision for this file.
            "quant": None,
            # No label: a slot label is an address inside a stored topology,
            # and this card has none to address.
            "slot_label": None,
            "filename": _SHELF_FILENAME,
        }
    ]
    # The family is the one the model shelf serves for the same file: a client
    # compares a LoRA's shelf `base_model_family` against this, so the two
    # must agree.
    shelf = workflow_env.owner.get(f"{API}/checkpoints")
    assert shelf.status_code == 200, shelf.text
    row = next(
        entry
        for entry in shelf.json()["checkpoints"]
        if entry["sha256"] == _h("realvisxl-digest")
    )
    assert row["base_model_family"] == card["models"][0]["base_model_family"]
    # Named, because a LoRA named in the file is one the workflow loads and
    # there is no recipe to fill it. Null title and null icon are the state:
    # the shelf does not hold this file.
    assert card["loras"] == [
        {
            "name": "add detail",
            "title": None,
            "icon": None,
            "base_model": None,
            "base_model_folded": None,
            "sha256": None,
            "base_model_family": None,
            "kind": "lora",
            "quant": None,
            "slot_label": None,
            "filename": "add_detail.safetensors",
        }
    ]


def test_a_slots_quant_comes_from_the_shelf_first_and_the_filename_after(
    workflow_env, tmp_path, monkeypatch
):
    """The precision a card shows, from whichever source can answer.

    Two sources fold into one vocabulary, and the card has to prefer the right
    one: the shelf's column is read from the safetensors header, which is the
    only thing that knows what a file called `nvfp4_awq` is actually stored at,
    while the filename is the only source a `.gguf` or an unscanned model has.
    Serving the header's raw `f8_e4m3` would have one card read `f8_e4m3`
    where the next reads `fp8_e4m3` for one precision.
    """
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET quant = 'f8_e4m3' WHERE filename = ?",
            (_SHELF_FILENAME,),
        )
    document = json.loads(json.dumps(_EDITOR_WORKFLOW))
    # A model this shelf has never scanned, whose name is its only source.
    document["nodes"][1]["widgets_values"] = ["Flux1-Dev-Q4_K_M.gguf", 1.0, 1.0]
    key = _file_a_workflow(
        workflow_env.server, tmp_path, monkeypatch, "quantised.json", document
    )

    card = _by_key(_cards(workflow_env.owner))[key]
    # The shelf row's own column, FOLDED - the header spells it `f8_e4m3`.
    assert (card["models"][0]["name"], card["models"][0]["quant"]) == (
        _SHELF_DERIVED,
        "fp8_e4m3",
    )
    # The filename, for the file the shelf does not hold. The level is kept
    # whole because `Q4_K_M` is the name a person recognises.
    assert (card["loras"][0]["name"], card["loras"][0]["quant"]) == (
        "Flux1 Dev",
        "q4_k_m",
    )
    # The card's own name row is NOT asserted here: this card was filed from
    # `quantised.json`, so `_display_name` returns the file stem and never
    # reaches a model slot. The name row's own strip is pinned in
    # `test_a_card_names_itself_without_the_precision`, on a card that has no
    # file to be named after.


def test_an_editor_format_card_answers_the_same_on_its_own_route(
    workflow_env, tmp_path, monkeypatch
):
    """The detail route reads the file too, and every write answers with it.

    `_read_detail` is the write path's seam: PATCH, the defaults, the pins and
    the slot marks all answer with it, so a card that had its models on the
    grid and lost them the moment somebody renamed it would be the bug nobody
    reported.
    """
    _give_the_shelf_model_a_picture(workflow_env.server)
    key = _file_a_workflow(
        workflow_env.server, tmp_path, monkeypatch, "editor.json", _EDITOR_WORKFLOW
    )

    on_the_grid = _by_key(_cards(workflow_env.owner))[key]
    opened = _detail(workflow_env.owner, key)["card"]
    assert opened["models"] == on_the_grid["models"]
    assert opened["loras"] == on_the_grid["loras"]

    # ...and after a write, which answers with that same read.
    r = workflow_env.owner.patch(
        f"{API}/workflows/{key}", json={"name": "Editor workflow"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["card"]["models"] == on_the_grid["models"]


# What ComfyUI's own graphToPrompt makes of _EDITOR_WORKFLOW (#1530).
_EDITOR_CONVERTED = {
    "1": {
        "class_type": "UNETLoader",
        "inputs": {"unet_name": _SHELF_FILENAME, "weight_dtype": "default"},
    },
    "2": {
        "class_type": "LoraLoader",
        "inputs": {
            "model": ["1", 0],
            "lora_name": _EDITOR_UNRESOLVED,
            "strength_model": 1.0,
            "strength_clip": 1.0,
        },
    },
    "3": {
        "class_type": "SaveImage",
        "inputs": {"images": ["2", 0], "filename_prefix": "out"},
    },
}


def _convert(owner, workflow=_EDITOR_WORKFLOW, output=_EDITOR_CONVERTED):
    return owner.post(
        f"{API}/comfyui/workflows/convert",
        json={"name": "editor.json", "workflow": workflow, "output": output},
    )


def _listed(owner) -> dict:
    r = owner.get(f"{API}/comfyui/workflows")
    assert r.status_code == 200, r.text
    return {item["name"]: item for item in r.json()["workflows"]}


@pytest.fixture
def converting(workflow_env, tmp_path, monkeypatch):
    """An imported editor-format workflow, no ComfyUI."""
    _isolate_workflow_folders(tmp_path, monkeypatch)
    monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "refused")
    )
    manual = _file_a_workflow(
        workflow_env.server, tmp_path, monkeypatch, "editor.json", _EDITOR_WORKFLOW
    )
    return SimpleNamespace(folder=tmp_path, manual=manual)


def _api_document(hub, workflow_id):
    row = hub.fetchone(
        "SELECT api_document FROM workflow_document WHERE workflow_id = ?",
        (workflow_id,),
    )
    return json.loads(row[0]) if row[0] else None


def test_a_converted_editor_file_runs_from_the_graph_stored_beside_it(
    workflow_env, converting
):
    """#1530: ComfyUI's conversion makes an imported editor workflow runnable."""
    owner = workflow_env.owner
    assert owner.get(f"{API}/workflows/{converting.manual}/graph").status_code == 409

    r = _convert(owner)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"name": "editor", "matched": True, "workflow_id": converting.manual}
    # Stored on the workflow's own row, and nothing written to any folder.
    assert _api_document(workflow_env.server.hub, converting.manual) == (
        _EDITOR_CONVERTED
    )
    assert list(converting.folder.glob("*.json*")) == []
    r = owner.get(f"{API}/workflows/{converting.manual}/graph")
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "file"
    assert r.json()["workflow"]["2"]["inputs"]["lora_name"] == _EDITOR_UNRESOLVED


def test_a_conversion_of_another_editor_document_is_not_run(workflow_env, converting):
    """Matched on the whole document: another version is another workflow."""
    owner = workflow_env.owner
    changed = json.loads(json.dumps(_EDITOR_WORKFLOW))
    changed["nodes"][2]["widgets_values"] = ["elsewhere"]
    r = _convert(owner, workflow=changed)
    assert r.status_code == 200, r.text
    assert r.json()["matched"] is False
    assert r.json()["workflow_id"] != converting.manual
    assert _api_document(workflow_env.server.hub, converting.manual) is None
    assert owner.get(f"{API}/workflows/{converting.manual}/graph").status_code == 409


def test_a_converted_file_runs_without_its_editor_bindings(workflow_env, converting):
    """Bindings address the editor structure; the API graph is detected afresh."""
    bound = {**_EDITOR_WORKFLOW, "pixlstash_bindings": []}
    r = _convert(workflow_env.owner, workflow=bound)
    assert r.status_code == 200, r.text
    assert r.json()["workflow_id"] == converting.manual
    document = manual_document(workflow_env.server.hub, converting.manual)
    assert "pixlstash_bindings" not in document
    assert document["3"]["class_type"] == "SaveImage"


def test_an_enveloped_conversion_is_stored_unwrapped(workflow_env, converting):
    r = _convert(workflow_env.owner, output={"prompt": _EDITOR_CONVERTED})
    assert r.status_code == 200, r.text
    assert _api_document(workflow_env.server.hub, converting.manual) == (
        _EDITOR_CONVERTED
    )


def test_a_built_in_workflow_is_put_on_a_card_that_runs_its_file(
    workflow_env, tmp_path, monkeypatch
):
    """Edit with ComfyUI opens the Run popup on the built-in edit workflow's card.

    A built-in is never imported, so nothing files it until this route does.
    """
    builtin = comfyui_module._workflow_builtin_dir()
    _isolate_workflow_folders(tmp_path, monkeypatch)
    monkeypatch.setattr(
        comfyui_module,
        "_workflow_dirs",
        lambda: [("user", str(tmp_path)), ("built-in", builtin)],
    )
    owner = workflow_env.owner
    path = f"{API}/comfyui/workflows/Flux2-Klein-Image-Edit.json/card"

    r = owner.post(path)
    assert r.status_code == 200, r.text
    key = r.json()["workflow_id"]
    assert key.startswith("manual:")
    assert r.json()["name"] == "Flux2-Klein-Image-Edit.json"
    # Idempotent: the second ask answers the same workflow.
    assert owner.post(path).json()["workflow_id"] == key
    card = _by_key(_cards(owner))[key]
    assert (card["manual"], card["name"]) == (True, "Flux2-Klein-Image-Edit")

    r = owner.get(f"{API}/workflows/{key}/graph")
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "file"
    assert r.json()["workflow"]["76"]["class_type"] == "LoadImage"

    assert owner.post(f"{API}/comfyui/workflows/nope.json/card").status_code == 404


def test_deleting_a_converted_workflow_takes_its_conversion_with_it(
    workflow_env, converting
):
    owner = workflow_env.owner
    key = _convert(owner).json()["workflow_id"]
    r = owner.delete(f"{API}/workflows/{key}")
    assert r.status_code == 200, r.text
    assert manual_document(workflow_env.server.hub, key) is None


def test_a_workflow_not_stored_yet_is_stored_by_its_conversion(
    workflow_env, converting
):
    other = json.loads(json.dumps(_EDITOR_WORKFLOW))
    other["nodes"][2]["widgets_values"] = ["another"]
    r = _convert(workflow_env.owner, workflow=other)
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["matched"]) == ("editor", False)
    hub = workflow_env.server.hub
    assert _api_document(hub, r.json()["workflow_id"]) == _EDITOR_CONVERTED
    assert json.loads(
        hub.fetchone(
            "SELECT document FROM workflow_document WHERE workflow_id = ?",
            (r.json()["workflow_id"],),
        )[0]
    ) == stamp_workflow_id(other, r.json()["workflow_id"])


@pytest.mark.parametrize(
    "workflow, output",
    [
        (_EDITOR_CONVERTED, _EDITOR_CONVERTED),
        (_EDITOR_WORKFLOW, _EDITOR_WORKFLOW),
        (_EDITOR_WORKFLOW, {}),
        (_EDITOR_WORKFLOW, {"1": "x"}),
        (_EDITOR_WORKFLOW, {"a": 1}),
    ],
    ids=["api-as-editor", "editor-as-api", "empty-output", "not-nodes", "scalar"],
)
def test_a_conversion_is_an_editor_workflow_and_its_api_graph(
    workflow_env, converting, workflow, output
):
    r = _convert(workflow_env.owner, workflow=workflow, output=output)
    assert r.status_code == 400, r.text
    assert _api_document(workflow_env.server.hub, converting.manual) is None


def test_two_imports_of_one_graph_are_two_workflows(
    workflow_env, tmp_path, monkeypatch
):
    """A manual workflow is a record, not a content address: identical copies
    are allowed and never fold into one."""
    first = _file_a_workflow(
        workflow_env.server, tmp_path, monkeypatch, "editor.json", _EDITOR_WORKFLOW
    )
    same = json.loads(json.dumps(_EDITOR_WORKFLOW))
    second = _file_a_workflow(
        workflow_env.server, tmp_path, monkeypatch, "editor-copy.json", same
    )
    cards = _by_key(_cards(workflow_env.owner))
    assert set(cards) == {BUSY_WF, FORGOTTEN_WF, first, second}
    assert (cards[first]["name"], cards[second]["name"]) == ("editor", "editor-copy")


def test_a_loader_this_build_does_not_know_leaves_the_row_silent(
    workflow_env, tmp_path, monkeypatch
):
    """No list of loader classes is every loader there is (#1466 review).

    The recovery finds a LoRA it knows and misses the base model beside it, so
    `models` is empty while `loras` is not. The row must still decline to say
    "No checkpoint" — `variant_count: 0` is what a client branches on, and the
    payload has to leave it that choice rather than implying an answer.
    """
    graph = json.loads(json.dumps(_EDITOR_WORKFLOW))
    graph["nodes"][0]["type"] = "SomeThirdPartyCheckpointLoader"
    key = _file_a_workflow(
        workflow_env.server, tmp_path, monkeypatch, "custom.json", graph
    )

    card = _by_key(_cards(workflow_env.owner))[key]
    assert card["models"] == []
    # The DERIVED name: a slot's `name` is what the card calls the file, not
    # the raw widget value it came off.
    assert [lora["name"] for lora in card["loras"]] == ["add detail"]
    # Non-empty models is NOT what tells a client the card was read.
    assert card["variant_count"] == 0


def test_a_card_with_a_recipe_never_reads_its_models_off_the_file(
    workflow_env, tmp_path, monkeypatch
):
    """The guard on the recovery, exercised from the side that could be wrong.

    BUSY has a recipe, so its models come from the cached slot list. Giving it
    a FILE that names something else must change nothing: reading a card's
    models off a file it happens to own would overwrite what its pictures
    actually ran with, and would put a filesystem read on every card of the
    grid besides.
    """
    other = json.loads(json.dumps(_EDITOR_WORKFLOW))
    other["nodes"][0]["widgets_values"] = ["not-the-recipes-model.safetensors"]
    (tmp_path / "busy.json").write_text(json.dumps(other), encoding="utf-8")
    monkeypatch.setattr(
        comfyui_module, "_workflow_dirs", lambda: [("user", str(tmp_path))]
    )
    # A legacy file row, as an import wrote one before manual workflows.
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_file (workflow_name, topology_hash, "
            "structural_hash, workflow_key) VALUES ('busy.json', ?, ?, ?)",
            (BUSY_TOPOLOGY, BUSY_RECIPE_A, BUSY_CARD),
        )

    card = _by_key(_cards(workflow_env.owner))[BUSY_WF]
    assert [model["name"] for model in card["models"]] == [_SHELF_DERIVED]
    # The LoRA slot stays anonymous rather than taking the file's name.
    assert [lora["name"] for lora in card["loras"]] == [None]


def test_a_file_that_says_nothing_about_its_models_leaves_them_unread(
    workflow_env, tmp_path, monkeypatch
):
    """A template-style export, whose loaders were never filled in (#1466).

    The card still exists - that is the whole point - and its models are
    EMPTY, which a client reads beside `variant_count: 0` as "nobody has read
    this workflow's models" rather than as "it has none".
    """
    empty = json.loads(json.dumps(_EDITOR_WORKFLOW))
    for node in empty["nodes"]:
        node["widgets_values"] = []
    key = _file_a_workflow(
        workflow_env.server, tmp_path, monkeypatch, "template.json", empty
    )

    card = _by_key(_cards(workflow_env.owner))[key]
    assert (card["models"], card["loras"]) == ([], [])
    assert card["variant_count"] == 0
    # A document that will not read answers the same way rather than raising.
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_document SET document = '{' WHERE workflow_id = ?", (key,)
        )
    workflows_routes._manual_model_widgets.cache_clear()
    assert _by_key(_cards(workflow_env.owner))[key]["models"] == []


def test_an_imported_wan_video_workflow_is_a_video_workflow(
    workflow_env, tmp_path, monkeypatch
):
    """A manual workflow's type is read off its own graph (#1696), as an
    automatic card's is off its topology: a Wan 2.2 video import says Video."""
    wan = json.loads(
        (
            Path(__file__).parent
            / "comfyui_workflows/paired/multigpu/api/wan2_2 distorch2 double_unet no_cpu.json"
        ).read_text("utf-8")
    )
    key = _file_a_workflow(workflow_env.server, tmp_path, monkeypatch, "wan.json", wan)
    card = _by_key(_cards(workflow_env.owner))[key]
    assert (card["type"], card["type_label"]) == ("video", "Video")
    editor = _file_a_workflow(
        workflow_env.server, tmp_path, monkeypatch, "editor.json", _EDITOR_WORKFLOW
    )
    assert _by_key(_cards(workflow_env.owner))[editor]["type"] is None


def test_a_manual_workflow_whose_links_are_malformed_has_no_type():
    """A link slot that is not a number raised TypeError out of the reduction
    and took the whole grid with it; it is a card with no type instead."""
    graph = {
        "nodes": [
            {"id": 1, "type": "KSampler", "inputs": [], "outputs": []},
        ],
        "links": [[1, 1, {"slot": 0}, 2, 0, "MODEL"]],
    }
    assert _manual_facts_of("manual:" + "0" * 32, json.dumps(graph)) == (None, None)


def test_a_manual_workflows_base_models_read_as_an_automatic_cards_do(
    workflow_env, tmp_path, monkeypatch
):
    """Sorted on the lowercased name and a case variant read once (#1696), so a
    Wan pair is "High + Low" on both kinds of card, never "Low + High"."""
    graph = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "wan_t2v_low.safetensors"},
        },
        "2": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "Wan_T2V_High.safetensors"},
        },
        "3": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "WAN_T2V_LOW.safetensors"},
        },
        "4": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }
    key = _file_a_workflow(
        workflow_env.server, tmp_path, monkeypatch, "pair.json", graph
    )
    models = _by_key(_cards(workflow_env.owner))[key]["models"]
    assert [m["name"].lower() for m in models] == ["wan t2v high", "wan t2v low"]


def test_the_shelfs_picture_needs_one_candidate_where_its_name_needs_agreement(
    workflow_env,
):
    """`model_marks`' rule beside the narrowed title view, which it is not.

    Two shelf rows answering to one basename are two files. They can still
    agree on a name - and then the card may show it - but a thumbnail is a
    picture *of one of them*, so an ambiguous name takes none.
    """
    hub = workflow_env.server.hub
    _give_the_shelf_model_a_picture(workflow_env.server)
    mark = workflow_card_service.model_marks(hub, [_SHELF_FILENAME])[_SHELF_FILENAME]
    assert (mark.title, mark.icon) == (_SHELF_TITLE, _EDITOR_ICON)

    with hub.transaction() as conn:
        # A second file of the same basename, named the same and with a
        # picture of its own. Deleted with its twin by the next re-seed.
        conn.execute(
            "INSERT INTO model (file_kind, filename, display_name, icon_sha256, "
            "provenance) VALUES ('checkpoint', ?, ?, ?, 'scanned')",
            (_SHELF_FILENAME, _SHELF_TITLE, _h("the-other-copys-icon")),
        )
    mark = workflow_card_service.model_marks(hub, [_SHELF_FILENAME])[_SHELF_FILENAME]
    assert mark.title == _SHELF_TITLE, "two rows agreeing on a name still name it"
    assert mark.icon is None, "but neither one's picture is the model's"
    # A name this shelf has never seen is absent, not present-and-empty.
    assert workflow_card_service.model_marks(hub, ["nothing-here.safetensors"]) == {}


def test_two_names_for_one_shelf_model_both_keep_its_picture(workflow_env):
    """One model answers to several names, and each of them is a slot value.

    Its own filename, every copy's basename and its digest all resolve to the
    same row, so one card can name it twice - and an index keyed by MODEL
    rather than by name silently drops one of them, leaving a model whose
    picture the shelf holds drawing initials.
    """
    hub = workflow_env.server.hub
    _give_the_shelf_model_a_picture(workflow_env.server)
    digest = hub.fetchone(
        "SELECT sha256 FROM model WHERE filename = ?", (_SHELF_FILENAME,)
    )["sha256"]

    marks = workflow_card_service.model_marks(hub, [_SHELF_FILENAME, digest])
    assert set(marks) == {_SHELF_FILENAME, digest}
    assert [mark.icon for mark in marks.values()] == [_EDITOR_ICON, _EDITOR_ICON]


def _set_picture_models(server, values: dict[str, tuple[list, list]]) -> None:
    """Give seeded pictures the `comfyui_models` / `comfyui_loras` they ran.

    The extraction pass writes these columns; the module's finders are
    detached, so a test writes them itself, raw widget values and all.
    """

    def write(session):
        for picture in session.exec(select(Picture)).all():
            if picture.file_path in values:
                models, loras = values[picture.file_path]
                # A str is stored as it is: a malformed value, as a test wants.
                picture.comfyui_models = (
                    models if isinstance(models, str) else json.dumps(models)
                )
                picture.comfyui_loras = (
                    loras if isinstance(loras, str) else json.dumps(loras)
                )
                session.add(picture)
        session.commit()

    server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


def test_a_workflow_lists_the_values_its_kept_pictures_used(workflow_env):
    """`recipe_values`: each checkpoint and LoRA, with how many kept pictures.

    Spelled as the picture filters take them - the raw widget value, folder
    and all - so a value filters to the pictures it counted. The soft-deleted
    picture is given a value nothing kept used: counting it would list it.
    """
    server, owner = workflow_env.server, workflow_env.owner
    _set_picture_models(
        server,
        {
            "busy_one.png": (
                ["SDXL/RealVisXL.safetensors"],
                ["add_detail.safetensors"],
            ),
            "busy_two.png": (["SDXL/RealVisXL.safetensors"], []),
            "busy_three.png": (["other.safetensors"], ["add_detail.safetensors"]),
            "busy_binned.png": (["binned-only.safetensors"], ["binned-only-lora.st"]),
        },
    )
    values = _by_key(_cards(owner))[BUSY_WF]["recipe_values"]
    assert values == {
        "checkpoints": [
            {"name": "SDXL/RealVisXL.safetensors", "pictures": 2},
            {"name": "other.safetensors", "pictures": 1},
        ],
        "loras": [{"name": "add_detail.safetensors", "pictures": 2}],
    }
    # Another workflow's pictures are its own.
    assert _by_key(_cards(owner))[FORGOTTEN_WF]["recipe_values"] == {
        "checkpoints": [],
        "loras": [],
    }
    # ...and the value filters to exactly the pictures it counted.
    ids = _picture_ids_by_path(server)
    r = owner.get(
        f"{API}/pictures",
        params={"workflow": BUSY_WF, "comfyui_model": "SDXL/RealVisXL.safetensors"},
    )
    assert r.status_code == 200, r.text
    assert {picture["id"] for picture in r.json()} == {
        ids["busy_one.png"],
        ids["busy_two.png"],
    }


def test_one_malformed_value_does_not_fail_the_grid(workflow_env):
    """A picture whose stored LoRA list is not JSON counts nothing, and the
    grid still reads: ``json_each`` is handed ``[]`` in its place."""
    server, owner = workflow_env.server, workflow_env.owner
    _set_picture_models(
        server,
        {
            "busy_one.png": (["SDXL/RealVisXL.safetensors"], "[not json"),
            "busy_two.png": (
                ["SDXL/RealVisXL.safetensors"],
                ["add_detail.safetensors"],
            ),
        },
    )
    try:
        r = owner.get(f"{API}/workflows")
        assert r.status_code == 200, r.text
        values = _by_key(r.json())[BUSY_WF]["recipe_values"]
        assert {"name": "add_detail.safetensors", "pictures": 1} in values["loras"]
    finally:
        # The module shares its vault: leave no malformed row to later tests.
        _set_picture_models(
            server, {"busy_one.png": ([], []), "busy_two.png": ([], [])}
        )


def test_a_card_adds_up_every_variants_kept_pictures_and_ratings(workflow_env):
    """Four kept pictures across two variants; two of them carry a star.

    ``busy_three`` is rated **0**, which is a rating somebody cleared and not a
    rating: counting it would drag the card's mean from 4.5 to 3.0. The two
    soft-deleted pictures belong to these cards and are rated 5; counting them
    would move every figure on this line.
    """
    card = _by_key(_cards(workflow_env.owner))[BUSY_WF]
    assert card["picture_count"] == 5
    assert card["rating"] == pytest.approx(4.5)
    forgotten = _detail(workflow_env.owner, FORGOTTEN_WF)["card"]
    assert forgotten["picture_count"] == 4
    assert forgotten["rating"] == pytest.approx(2.0)


def test_an_unrated_card_reports_no_rating_rather_than_the_prior(workflow_env):
    """``rating`` is the plain mean, never the rank.

    The rank is smoothed towards the library's average so cards can be ordered
    against each other; showing it would tell somebody their never-rated
    workflow is rated 3.7.
    """
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_file "
            "(workflow_name, topology_hash, structural_hash, workflow_key) "
            "VALUES ('binned.json', ?, ?, ?)",
            (BINNED_TOPOLOGY, BINNED_RECIPE, BINNED_CARD),
        )
    card = _by_key(_cards(workflow_env.owner))[BINNED_WF]
    assert card["picture_count"] == 0
    assert card["rating"] is None


def test_the_cover_rank_orders_the_grid_by_the_bayesian_mean(workflow_env):
    """The exact number, not an ordering, and the prior is the LIBRARY's mean.

    A plain mean would put FORGOTTEN (one 2) and BUSY (a 5 and a 4) at 2.0 and
    4.5; the prior pulls both towards 11/3 in proportion to how little each has
    been rated. The library mean is not a round number and equals no card's own
    mean, so a constant cannot stand in for it.
    """
    payload = _cards(workflow_env.owner)
    assert _by_key(payload)[BUSY_WF]["rank"] == pytest.approx(_BUSY_RANK)
    detail = _detail(workflow_env.owner, FORGOTTEN_WF)["card"]
    assert detail["rank"] == pytest.approx(_FORGOTTEN_RANK)


def test_the_cover_strip_is_the_cards_best_three_across_its_variants(workflow_env):
    """Best first, both variants, exactly three, and as thumbnail URLs.

    One behaviour, so one test. The order pins both halves of the comparator's
    NULL rule, and the fixture carries a picture for each:

    * ``busy_unrated`` has **no star rating** and a good smart score. NULL sorts
      last, so it stays out of the strip; read as a zero it would tie the two
      cleared zeros, win on its smart score and take the third slot.
    * ``busy_three`` has a cleared **0** and the ``-1.0`` failed-metric
      sentinel, and it takes that third slot ahead of ``busy_four``, which has
      the same 0 and no smart score at all.

    The soft-deleted 5 would head this list, and the strip stops at three
    however many the card has.
    """
    card = _by_key(_cards(workflow_env.owner))[BUSY_WF]
    ids = _picture_ids_by_path(workflow_env.server)
    assert _cover_urls(card) == [
        f"/pictures/thumbnails/{ids['busy_one.png']}.webp?v=0",
        f"/pictures/thumbnails/{ids['busy_two.png']}.webp?v=0",
        f"/pictures/thumbnails/{ids['busy_three.png']}.webp?v=0",
    ]


def test_a_cover_names_the_picture_it_draws(workflow_env):
    """Every cover carries `picture_id`, and it is the one in its own URL.

    The Workflows grid opens the picture a cover draws (#1455), and the only
    other way to that id is to parse it back out of the thumbnail path - which
    is a path shape, not an interface.

    **It is a field ON the cover, not a `cover_ids` list beside `covers`**,
    which is what it was until #1465 gave each cover an object of its own. Two
    lists paired by position are two lists that can come apart, and a strip
    that dropped an entry without its id would open picture 2 from the tile
    showing picture 3. One object cannot do that - so what is left to assert
    is that the id and the URL name the same picture, which is checked on
    every cover of every card the grid serves.
    """
    cards = _cards(workflow_env.owner)["cards"]
    assert cards, "the grid served no cards - the comparison below is vacuous"
    drawn = 0
    for card in cards:
        for cover in card["covers"]:
            in_url = int(cover["url"].split("/")[-1].split(".webp")[0])
            assert cover["picture_id"] == in_url, card["id"]
            drawn += 1
    assert drawn, "no card in the fixture has a cover - nothing was compared"


def test_a_cover_carries_the_stored_crop_rectangle_or_nothing(workflow_env):
    """The face-weighted rectangle travels with the cover, per picture (#1465).

    Both halves in one test, because the fallback is the half that decides
    whether this is a regression: a cover whose rectangle has not been computed
    must send NULLs, so the client can keep the top-anchored ``cover`` crop it
    has always drawn rather than inventing a framing from a zero.

    The card's other two pictures carry no rectangle and are exactly that case,
    so the negative is asserted against a sibling in the same strip rather than
    against an empty library.
    """
    ids = _picture_ids_by_path(workflow_env.server)
    # A portrait bitmap whose stored square sits BELOW the top edge, which is
    # the picture the issue is about: a blind top anchor would cut through it.
    rectangle = {
        "thumbnail_width": 384,
        "thumbnail_height": 561,
        "square_crop_x": 0,
        "square_crop_y": 120,
        "square_crop_side": 384,
    }

    def write(session, values):
        picture = session.get(Picture, ids["busy_one.png"])
        for field, value in values.items():
            setattr(picture, field, value)
        session.add(picture)
        session.commit()

    workflow_env.server.vault.db.run_task(
        lambda session: write(session, rectangle), priority=DBPriority.IMMEDIATE
    )
    try:
        covers = _by_key(_cards(workflow_env.owner))[BUSY_WF]["covers"]
        assert covers[0] == {
            "url": (
                f"/pictures/thumbnails/{ids['busy_one.png']}.webp"
                f"?v={ImageUtils.thumbnail_cache_version(384, 561, None)}"
            ),
            # The picture the cover draws, so a client can open it (#1455).
            "picture_id": ids["busy_one.png"],
            **rectangle,
            "superseded": False,
        }
        for cover in covers[1:]:
            assert cover["square_crop_x"] is None
            assert cover["square_crop_y"] is None
            assert cover["square_crop_side"] is None
    finally:
        # Put the picture back: the module shares one vault, and the bitmap
        # dimensions are the cache-buster every other cover assertion reads.
        workflow_env.server.vault.db.run_task(
            lambda session: write(session, dict.fromkeys(rectangle, None)),
            priority=DBPriority.IMMEDIATE,
        )


def test_a_hidden_card_is_counted_never_listed_and_still_opens(workflow_env):
    """Hiding is a decision about the grid, not a deletion.

    The detail route is the half that gets forgotten: a card nobody can reach
    once it is hidden cannot be un-hidden. The hidden card carries a workflow
    file so it is not ALSO a one-off -- without that this passes whichever
    clause removed it.
    """
    payload = _cards(workflow_env.owner)
    assert payload["hidden"] == 1
    assert HIDDEN_WF not in _by_key(payload)
    body = _detail(workflow_env.owner, HIDDEN_WF)
    assert body["hidden"] is True
    assert body["card"]["name"] == "A workflow I hid"
    # And the card's own `hidden` is its state here, with NO flag involved:
    # the detail route opens a hidden card by design, because that is the only
    # way one can be unhidden. The grid's rule - true only for a card
    # `include_hidden` let in - is the grid's, and the two are documented
    # apart because one contract for both would be wrong about this route.
    assert body["card"]["hidden"] is True


def test_a_one_off_is_counted_and_an_imported_file_takes_it_out_of_the_count(
    workflow_env,
):
    """Every clause of the one-off predicate, by moving one of them.

    BINNED has no kept picture, no rating and no file, so it is folded into the
    count. Filing a workflow file against it is the owner saying they run this
    on purpose, and it must come back into the grid.
    """
    assert _cards(workflow_env.owner)["one_offs"] == 1
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_file "
            "(workflow_name, topology_hash, structural_hash, workflow_key) "
            "VALUES ('binned.json', ?, ?, ?)",
            (BINNED_TOPOLOGY, BINNED_RECIPE, BINNED_CARD),
        )
    payload = _cards(workflow_env.owner)
    assert payload["one_offs"] == 0
    assert _by_key(payload)[BINNED_WF]["imported"] is True


@pytest.mark.parametrize(
    ("pull_wrote_it", "one_offs"),
    [(True, 1), (False, 0)],
    ids=["written-by-a-pull", "matched-by-a-pull"],
)
def test_a_pulled_file_does_not_take_a_card_out_of_the_one_offs(
    workflow_env, pull_wrote_it, one_offs
):
    """#1440: a file a pull WROTE is not the owner's statement; one it matched is.

    The same file row as the test above. Written by a pull
    (``workflow_pulled_file``), BINNED stays a one-off - eighty pulled
    workflows must not all come out of the count. Matched by the pull, the
    file was already the owner's, and it keeps the card in the grid.
    """
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_file "
            "(workflow_name, topology_hash, structural_hash, workflow_key) "
            "VALUES ('binned.json', ?, ?, ?)",
            (BINNED_TOPOLOGY, BINNED_RECIPE, BINNED_CARD),
        )
        if pull_wrote_it:
            conn.execute(
                "INSERT INTO workflow_pulled_file (workflow_name) "
                "VALUES ('binned.json')"
            )
    payload = _cards(workflow_env.owner, "?include_one_offs=true")
    assert payload["one_offs"] == one_offs
    assert _by_key(payload)[BINNED_WF]["imported"] is True


def test_the_filters_panel_can_ask_for_the_one_offs_and_for_the_hidden(
    workflow_env,
):
    """F7's two checkboxes, and the counts that label them.

    Both counts are taken over the same set whatever the flags say, so the
    panel can go on writing "Hide one-offs (1)" while it is showing that one:
    a count that moved when its own checkbox was ticked would read as the
    number of cards still being held back, which is zero.
    """
    owner = workflow_env.owner
    drawn = _cards(owner)
    assert BINNED_WF not in _by_key(drawn)
    assert HIDDEN_WF not in _by_key(drawn)

    with_one_offs = _cards(owner, "?include_one_offs=true")
    assert BINNED_WF in _by_key(with_one_offs)
    assert HIDDEN_WF not in _by_key(with_one_offs)
    assert (with_one_offs["one_offs"], with_one_offs["hidden"]) == (1, 1)

    with_hidden = _cards(owner, "?include_hidden=true")
    # And it says which one it is. A card let back in unmarked is
    # indistinguishable from one that was never hidden, in the one grid it was
    # deliberately kept out of; `frontend/src/utils/workflowCard.js` draws the
    # chip off this field.
    assert _by_key(with_hidden)[HIDDEN_WF]["hidden"] is True
    assert _by_key(with_hidden)[BUSY_WF]["hidden"] is False
    assert HIDDEN_WF in _by_key(with_hidden)
    assert BINNED_WF not in _by_key(with_hidden)
    assert (with_hidden["one_offs"], with_hidden["hidden"]) == (1, 1)

    both = _cards(owner, "?include_hidden=true&include_one_offs=true")
    assert {BINNED_WF, HIDDEN_WF} <= set(_by_key(both))
    assert (both["one_offs"], both["hidden"]) == (1, 1)

    # The overlap, which is where a count taken over the widened set gives
    # itself away: HIDDEN is only spared the one-off rule by its workflow
    # file, so without it the card is both. Letting the hidden ones in must
    # not make the one-off count climb - the checkbox beside that number is
    # the thing that let them in, and its own label would move as it was
    # ticked.
    with workflow_env.server.hub.transaction() as conn:
        conn.execute("DELETE FROM workflow_file WHERE workflow_key = ?", (HIDDEN_CARD,))
    assert _cards(owner)["one_offs"] == 1
    assert _cards(owner, "?include_hidden=true")["one_offs"] == 1


def test_a_workflow_counts_the_ghosts_its_own_variants_keep(workflow_env):
    """The Filters panel's Ghosts row: "keeps something deleted", per WORKFLOW.

    Over every variant of every card of the workflow, and no other workflow's.
    """
    server = workflow_env.server
    record_picture_ghosts(
        server.hub,
        [
            PictureGhost(
                library_uuid=server.vault.library_uuid,
                pixel_sha="sha-busy-ghost",
                instance_hash=_h("busy-ghost-instance"),
                structural_hash=BUSY_RECIPE_A,
                thumbnail=b"thumbnail-bytes",
            ),
            # Another library's ghost, which this library must not count.
            PictureGhost(
                library_uuid=_h("another-library"),
                pixel_sha="sha-elsewhere",
                instance_hash=_h("elsewhere-instance"),
                structural_hash=FORGOTTEN_RECIPE,
                thumbnail=b"thumbnail-bytes",
            ),
        ],
    )
    # A second missing model, on the OTHER variant. This is what makes the
    # count a workflow-wide answer: `_describe_slots` resolves names for the
    # base card's first variant alone (and leaves a LoRA slot anonymous), so
    # an implementation reading the ghosts off `models`/`loras` sees exactly
    # one of these two whatever the variant order is.
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_recipe_asset "
            "(structural_hash, widget_name, normalized_filename) "
            "VALUES (?, 'lora_name', 'test-gone-from-the-shelf.safetensors')",
            (BUSY_RECIPE_B,),
        )

    cards = _by_key(_cards(workflow_env.owner))
    assert cards[BUSY_WF]["ghosts"] == 1
    # One missing LoRA per variant: the fixture's own, and the one just filed.
    assert cards[BUSY_WF]["model_ghosts"] == 2

    # And another library's ghost is nobody's here.
    other = _detail(workflow_env.owner, FORGOTTEN_WF)["card"]
    assert (other["ghosts"], other["model_ghosts"]) == (0, 0)


# The card a saved recipe written by `_save_recipe` is filed under: internal
# storage, NOT NULL, and nothing these tests read.
_RECIPE_CARD = {
    BUSY_WF: BUSY_CARD,
    FORGOTTEN_WF: FORGOTTEN_CARD,
    BINNED_WF: BINNED_CARD,
    HIDDEN_WF: HIDDEN_CARD,
}


def _save_recipe(server, workflow_id, name="A look I kept"):
    """Put one saved recipe on a workflow, the way `POST /recipes` would."""

    def write(session):
        session.add(
            SavedRecipe(
                name=name,
                workflow_key=_RECIPE_CARD.get(workflow_id, "0" * 64),
                workflow_id=workflow_id,
                prompt="a cat",
            )
        )
        session.commit()

    server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


def test_a_card_counts_the_recipes_saved_on_it(workflow_env):
    """``saved_recipe_count`` is the real number, not a placeholder.

    ⓘ always draws the "Saved recipes" row, so a hardcoded zero does not read
    as "not implemented yet" -- it reads as "you have saved none", which is a
    different and wrong statement once B6's table exists.
    """
    assert _by_key(_cards(workflow_env.owner))[BUSY_WF]["saved_recipe_count"] == 0
    _save_recipe(workflow_env.server, BUSY_WF)
    _save_recipe(workflow_env.server, BUSY_WF, name="And another")
    cards = _by_key(_cards(workflow_env.owner))
    assert cards[BUSY_WF]["saved_recipe_count"] == 2
    # Keyed by workflow, so a recipe on one does not leak onto another.
    assert cards[FORGOTTEN_WF]["saved_recipe_count"] == 0


def test_a_saved_recipe_takes_a_card_out_of_the_one_off_count(workflow_env):
    """The fourth clause of the one-off rule, which B6's table made reachable.

    BINNED has no kept picture, no rating and no file, so the grid folds it
    into a count. Saving a look on it is the plainest statement that somebody
    means to run it again, so it has to come back into the grid -- otherwise
    the owner's own recipe sits on a card they can no longer see.
    """
    assert _cards(workflow_env.owner)["one_offs"] == 1
    _save_recipe(workflow_env.server, BINNED_WF)
    payload = _cards(workflow_env.owner)
    assert payload["one_offs"] == 0
    assert _by_key(payload)[BINNED_WF]["saved_recipe_count"] == 1


def _share_busy_core(server, topology=FORGOTTEN_TOPOLOGY) -> None:
    """Put *topology* in BUSY's automatic workflow, as a shared core hash does."""
    with server.hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_topology_core SET core_hash = ? WHERE topology_hash = ?",
            (SHARED_CORE, topology),
        )


def test_topologies_sharing_a_core_hash_are_one_workflow(workflow_env):
    """One entry for the group, with every figure summed over its topologies.

    FORGOTTEN joins BUSY's core hash, so the grid has one entry where it had
    two: its pictures, ratings, variants and covers are both cards', and its
    base topology is FORGOTTEN's, which has two LoRA loaders to BUSY's one
    (#1620 D3). Its models are therefore the base card's.
    """
    owner = workflow_env.owner
    _share_busy_core(workflow_env.server)
    cards = _by_key(_cards(owner))
    assert set(cards) == {BUSY_WF}
    merged = cards[BUSY_WF]
    assert merged["topologies"] == sorted([BUSY_TOPOLOGY, FORGOTTEN_TOPOLOGY])
    assert merged["base_topology"] == FORGOTTEN_TOPOLOGY
    assert merged["variant_count"] == 3
    # BUSY's five kept pictures and FORGOTTEN's four; the stars 5, 4 and 2.
    assert merged["picture_count"] == 9
    assert merged["rating"] == pytest.approx(11 / 3)
    # The base card's models: FORGOTTEN's, whose names were forgotten.
    assert [model["name"] for model in merged["models"]] == [None]
    assert len(merged["loras"]) == 2
    # The workflow's own id opens it; the one FORGOTTEN had is gone.
    assert owner.get(f"{API}/workflows/{FORGOTTEN_WF}").status_code == 404


def _defaults(owner, workflow_id) -> dict:
    """The workflow's defaults keyed by their address, which is what names a slot.

    By address and not by input name, because the busy card has two samplers:
    keying on the name alone would silently assert against whichever of them
    happened to sort first.
    """
    return {
        (row["slot_label"], row["input_name"]): row
        for row in _detail(owner, workflow_id)["card"]["defaults"]
    }


def _core(structural_hash: str, node_id: str) -> str:
    """One node's core address label (#1622), as the default recipe names it."""
    return (
        CORE_ADDRESS_PREFIX
        + core_node_labels(_DOCUMENTS[structural_hash], strip_loras=True)[node_id]
    )


def test_defaults_are_the_mode_over_the_cards_best_pictures(workflow_env):
    """30 steps twice beats nothing else; the two cfg values tie and resolve
    the same way on every read, which is what stops a card's defaults moving
    when nothing has changed. The binned 5-star run used 44 of each."""
    defaults = _defaults(workflow_env.owner, BUSY_WF)
    sampler = _core(BUSY_RECIPE_A, "3")
    assert defaults[(sampler, "steps")]["value"] == 30
    assert defaults[(sampler, "steps")]["provenance"] == "best"
    assert defaults[(sampler, "cfg")]["value"] == 8.0
    # Two slots offer `steps` and two offer `cfg`, and ⓘ keys its list on the
    # label: two rows called "steps" is a duplicate `v-for` key, and one of
    # them silently replaces the other on screen.
    rows = _detail(workflow_env.owner, BUSY_WF)["card"]["defaults"]
    labels = [row["label"] for row in rows]
    assert len(labels) == 4, labels
    assert sorted(labels) == ["cfg", "cfg 2", "steps", "steps 2"]


def test_defaults_fall_back_to_every_picture_when_none_is_rated_four(
    workflow_env,
):
    """A card whose only kept picture is a 2 still has to offer a default, and
    has to say the value did not come from anybody's best work.

    Its one 5-star picture is in the Scrapheap, so reading "best pictures"
    without excluding the Scrapheap would answer 99 and call it ``best``.
    """
    defaults = _defaults(workflow_env.owner, FORGOTTEN_WF)
    address = (_core(FORGOTTEN_RECIPE, "4"), "steps")
    assert defaults[address]["value"] == 20
    assert defaults[address]["provenance"] == "all"


def test_defaults_read_the_newest_runs_and_stop_at_the_cap(workflow_env, monkeypatch):
    """The cap, its size and its direction, each observable on its own.

    An instance keys on every parameter value including the seed, so a card
    somebody runs daily has one per picture and the read parses one stored
    graph apiece. The cap is what bounds that, and it has to cut from the
    NEWEST end: a card's defaults are meant to converge on what its owner does
    now, and a cap taken off the oldest runs drifts backwards forever.

    The forgotten card ran 20 steps three times and then 77 once, most
    recently. Over every run the mode is 20; over the newest run alone it is
    77. So a cap that does not cut, a limit that is not applied, and an order
    that is reversed each answer 20 where this asserts 77 -- and the sibling
    test above, which reads the same card uncapped, asserts the 20.
    """
    monkeypatch.setattr(workflow_card_service, "DEFAULT_SAMPLE", 1)
    defaults = _defaults(workflow_env.owner, FORGOTTEN_WF)
    address = (_core(FORGOTTEN_RECIPE, "4"), "steps")
    assert defaults[address]["value"] == 77
    assert defaults[address]["provenance"] == "all"


def test_an_owner_override_replaces_a_default_and_says_it_was_edited(
    workflow_env,
):
    """And it is addressed by core address, never by node id."""
    sampler = _core(BUSY_RECIPE_A, "3")
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_group_default (workflow_id, address, value) "
            "VALUES (?, ?, '8')",
            (BUSY_WF, f"{sampler}/steps"),
        )
    defaults = _defaults(workflow_env.owner, BUSY_WF)
    # The column is TEXT; a graph takes the number.
    assert defaults[(sampler, "steps")]["value"] == 8
    assert defaults[(sampler, "steps")]["provenance"] == "edited"
    # Only that one slot: the refiner's own `steps` and every other parameter
    # are still read off the pictures.
    refiner = _core(BUSY_RECIPE_A, "4")
    assert defaults[(refiner, "steps")]["value"] == 30
    assert defaults[(refiner, "steps")]["provenance"] == "best"
    assert defaults[(sampler, "cfg")]["provenance"] == "best"


def test_the_card_detail_lists_its_own_variants_and_not_its_topologys(
    workflow_env,
):
    """The variants filed under the workflow's topologies, with what each made."""
    body = _detail(workflow_env.owner, BUSY_WF)
    by_hash = {variant["structural_hash"]: variant for variant in body["variants"]}
    assert set(by_hash) == {BUSY_RECIPE_A, BUSY_RECIPE_B}
    assert by_hash[BUSY_RECIPE_A]["pictures"] == 3
    assert {asset["name"] for asset in by_hash[BUSY_RECIPE_A]["assets"]} == {
        "realvisxl.safetensors",
        "add_detail.safetensors",
    }


def test_card_pictures_are_every_variants_newest_kept_pictures(workflow_env):
    """Newest first, both variants, and never the soft-deleted one -- which is
    the newest picture on this card and would otherwise head the list."""
    ids = _picture_ids_by_path(workflow_env.server)
    r = workflow_env.owner.get(f"{API}/workflows/{BUSY_WF}/pictures")
    assert r.status_code == 200, r.text
    assert r.json() == [
        ids["busy_four.png"],
        ids["busy_three.png"],
        ids["busy_two.png"],
        ids["busy_one.png"],
        ids["busy_unrated.png"],
    ]
    limited = workflow_env.owner.get(f"{API}/workflows/{BUSY_WF}/pictures?limit=1")
    assert limited.json() == [ids["busy_four.png"]]


def test_a_manual_runs_pictures_count_on_it_and_fall_back_when_it_goes(
    workflow_env,
):
    """Filing is exclusive: a picture a manual workflow's run made counts on
    the manual card and not on the automatic one its graph is in - in the
    grid's counts, covers and values, its picture strip, the picture filter
    and a picture's own workflow. Deleting the manual workflow puts it back,
    with no write to the vault."""
    server, owner = workflow_env.server, workflow_env.owner
    _set_picture_models(
        server,
        {
            "busy_one.png": (["SDXL/RealVisXL.safetensors"], "[]"),
            "busy_two.png": (["SDXL/RealVisXL.safetensors"], "[]"),
        },
    )
    before = _by_key(_cards(owner))[BUSY_WF]
    manual = create_manual_workflow(
        server.hub, "Mine", {"1": {"class_type": "SaveImage", "inputs": {}}}, "import"
    )
    ids = _picture_ids_by_path(server)
    pid = ids["busy_one.png"]
    comfyui_service._set_run_workflow_id(server, manual, [pid])

    def pictures(workflow_id):
        r = owner.get(f"{API}/workflows/{workflow_id}/pictures?limit=60")
        assert r.status_code == 200, r.text
        return r.json()

    def filtered(workflow_id):
        r = owner.get(f"{API}/pictures", params={"workflow": workflow_id})
        assert r.status_code == 200, r.text
        return {picture["id"] for picture in r.json()}

    cards = _by_key(_cards(owner))
    mine, busy = cards[manual], cards[BUSY_WF]
    assert (mine["picture_count"], mine["rating"]) == (1, 5)
    assert [cover["picture_id"] for cover in mine["covers"]] == [pid]
    assert mine["recipe_values"]["checkpoints"] == [
        {"name": "SDXL/RealVisXL.safetensors", "pictures": 1}
    ]
    assert busy["picture_count"] == before["picture_count"] - 1
    assert pid not in [cover["picture_id"] for cover in busy["covers"]]
    assert busy["recipe_values"]["checkpoints"] == [
        {"name": "SDXL/RealVisXL.safetensors", "pictures": 1}
    ]
    assert pictures(manual) == [pid]
    assert pid not in pictures(BUSY_WF)
    assert filtered(manual) == {pid}
    assert pid not in filtered(BUSY_WF) and ids["busy_two.png"] in filtered(BUSY_WF)
    assert comfyui_module._picture_workflow_id(server, pid) == manual

    delete_manual_workflow(server.hub, manual)

    assert _by_key(_cards(owner))[BUSY_WF]["picture_count"] == before["picture_count"]
    assert pid in pictures(BUSY_WF) and pid in filtered(BUSY_WF)
    assert comfyui_module._picture_workflow_id(server, pid) == BUSY_WF

    def run_workflow_id(session):
        return session.get(Picture, pid).run_workflow_id

    # No vault write: the picture still names the workflow that made it.
    assert server.vault.db.run_task(run_workflow_id) == manual
    # And it is written once: a later run reporting it does not re-file it.
    comfyui_service._set_run_workflow_id(server, "manual:" + "b" * 32, [pid])
    assert server.vault.db.run_task(run_workflow_id) == manual


def test_an_unknown_workflow_is_a_404_and_a_malformed_id_a_422(workflow_env):
    """Both read routes: an empty 200 would read as "this workflow has nothing"
    rather than "this machine has no such workflow". A card key is no longer
    an id at all, so it is malformed rather than unknown.
    """
    unknown = AUTO_STACK_PREFIX + _h("nosuchcore")
    for path, template in (
        (f"{API}/workflows/{unknown}", API + "/workflows/{workflow_id}"),
        (
            f"{API}/workflows/{unknown}/pictures",
            API + "/workflows/{workflow_id}/pictures",
        ),
    ):
        assert_real_route(workflow_env.server.api, "GET", path, template)
        assert workflow_env.owner.get(path).status_code == 404, path
    for path in (
        f"{API}/workflows/not-a-digest",
        f"{API}/workflows/not-a-digest/pictures",
        f"{API}/workflows/{BUSY_CARD}",
        # A real id with a trailing newline: `$` alone would have let it by.
        f"{API}/workflows/{unknown}%0A",
        f"{API}/workflows/{unknown}%0A/pictures",
    ):
        assert workflow_env.owner.get(path).status_code == 422, path


def test_the_hub_card_reads_survive_a_list_longer_than_sqlites_cap(workflow_env):
    """A card the owner runs every day has one instance per run, and that list
    is sized by them rather than by the code.

    **The limit is pinned to 999 for the duration**, because this build's
    SQLite allows 250,000 bound parameters: without the pin an un-chunked read
    passes here and fails on the older builds the chunker exists for, which is
    an assertion that cannot go red on the machine that wrote it. Restored in a
    ``finally`` -- the hub connection outlives this test.

    The hash that must come back goes in the FIRST chunk and the filler behind
    it, so a read that returns only its last batch fails too.
    """
    filler = [_h(f"absent-{n}") for n in range(2 * SQLITE_ID_CHUNK)]
    hub = workflow_env.server.hub
    previous = hub.connection.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    hub.connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
    try:
        found = instance_documents(
            hub, workflow_env.server.vault.library_uuid, [BUSY_INSTANCE_ONE, *filler]
        )
        assert [structural for structural, _ in found] == [BUSY_RECIPE_A]
        assert set(variant_documents(hub, [BUSY_RECIPE_B, *filler])) == {BUSY_RECIPE_B}
    finally:
        hub.connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous)


# ===========================================================================
# The writes (v1.12 B4)
#
# Same shared environment, and the autouse fixture wipes every card table
# before each test, so a write here cannot reach the next one. The flip
# fixture below is seeded by the tests that need it rather than by the module,
# because it is a fifth topology with pictures of its own: seeded module-wide
# it would change the row count, the scan totals and the library's mean rating
# that every assertion above is written against.
# ===========================================================================

FLIP_TOPOLOGY = _h("fliptopology")
FLIP_RECIPE_A = _h("fliprecipea")
FLIP_RECIPE_B = _h("fliprecipeb")
FLIP_CORE = _h("flipcore")
FLIP_WF = auto_workflow_id(FLIP_CORE, "")

# Two variants of ONE graph that differ in nothing but which character LoRA
# sits in the slot. That is the whole point of the fixture: with the slot
# marked `recipe` they are one card, and marking it `structural` is what
# splits them - so the split and the merge are the same two rows read twice.
_FLIP_DOCUMENTS = {
    FLIP_RECIPE_A: {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": asset_reference("realvisxl.safetensors")},
        },
        "2": {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": asset_reference("character_ada.safetensors"),
                "model": ["1", 0],
            },
        },
        "3": {"class_type": "KSampler", "inputs": {"steps": None, "model": ["2", 0]}},
    },
    FLIP_RECIPE_B: {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": asset_reference("realvisxl.safetensors")},
        },
        "2": {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": asset_reference("character_bo.safetensors"),
                "model": ["1", 0],
            },
        },
        "3": {"class_type": "KSampler", "inputs": {"steps": None, "model": ["2", 0]}},
    },
}

_FLIP_ASSETS = (
    (FLIP_RECIPE_A, "ckpt_name", "realvisxl.safetensors"),
    (FLIP_RECIPE_A, "lora_name", "character_ada.safetensors"),
    (FLIP_RECIPE_B, "ckpt_name", "realvisxl.safetensors"),
    (FLIP_RECIPE_B, "lora_name", "character_bo.safetensors"),
)

# Three pictures on A and one on B, so a merge has a winner that is not the
# lexicographically first key by luck: the assertion is about the count.
_FLIP_PICTURES = (
    ("flip_a_one.png", FLIP_RECIPE_A),
    ("flip_a_two.png", FLIP_RECIPE_A),
    ("flip_a_three.png", FLIP_RECIPE_A),
    ("flip_b_one.png", FLIP_RECIPE_B),
)


def _flip_slot_label(structural_hash: str) -> str:
    """The LoRA slot both flip variants share, by B1's own rule."""
    return next(
        slot.label for slot in slots(_FLIP_DOCUMENTS[structural_hash]) if slot.is_lora
    )


def _flip_key(structural_hash: str, structural_labels=()) -> str:
    """The card key one flip variant lands on under these marks.

    **Derived and not written out**, unlike the module's other keys: the flip
    recomputes a key from the stored document, so a hand-written fixture key
    would make "the marks are already in force" look like a re-key.
    """
    return workflow_key(
        FLIP_TOPOLOGY, slots(_FLIP_DOCUMENTS[structural_hash]), structural_labels
    )


def _seed_flip_fixture(server) -> str:
    """Add the flip topology, its two variants and their pictures; return the
    one card both variants are on while the LoRA slot is marked ``recipe``."""
    label = _flip_slot_label(FLIP_RECIPE_A)
    assert label == _flip_slot_label(FLIP_RECIPE_B), (
        "the two flip variants must share a slot label, or they are not two "
        "variants of one topology and nothing below tests a flip"
    )
    merged = _flip_key(FLIP_RECIPE_A)
    assert merged == _flip_key(FLIP_RECIPE_B), (
        "with the LoRA slot marked recipe the two variants must share a key"
    )
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_topology "
            "(topology_hash, hash_version, node_count, first_seen_at) "
            "VALUES (?, 'v1', 3, '2026-08-06T00:00:00Z')",
            (FLIP_TOPOLOGY,),
        )
        conn.executemany(
            "INSERT INTO workflow_recipe (structural_hash, topology_hash, "
            "hash_version, node_count, first_seen_at) "
            "VALUES (?, ?, 'v1', 3, '2026-08-06T00:00:00Z')",
            [(FLIP_RECIPE_A, FLIP_TOPOLOGY), (FLIP_RECIPE_B, FLIP_TOPOLOGY)],
        )
        conn.executemany(
            "INSERT INTO workflow_recipe_graph "
            "(structural_hash, document_sha256, document, created_at) "
            "VALUES (?, 'x', ?, '2026-08-06T00:00:00Z')",
            [(key, json.dumps(doc)) for key, doc in _FLIP_DOCUMENTS.items()],
        )
        conn.executemany(
            "INSERT INTO workflow_recipe_asset "
            "(structural_hash, widget_name, normalized_filename) VALUES (?, ?, ?)",
            _FLIP_ASSETS,
        )
        conn.executemany(
            "INSERT INTO workflow_variant (structural_hash, topology_hash, "
            "workflow_key, key_version) VALUES (?, ?, ?, ?)",
            [
                (FLIP_RECIPE_A, FLIP_TOPOLOGY, merged, WORKFLOW_KEY_VERSION),
                (FLIP_RECIPE_B, FLIP_TOPOLOGY, merged, WORKFLOW_KEY_VERSION),
            ],
        )
        # Hand-written cards name no base model: the empty family set.
        conn.execute(
            "INSERT OR IGNORE INTO workflow_variant_family (structural_hash, families) "
            "SELECT structural_hash, '' FROM workflow_variant"
        )
        conn.execute(
            "INSERT INTO workflow_topology_core (topology_hash, core_hash, "
            "core_version, workflow_type, slots, specials, traits) "
            "VALUES (?, ?, ?, 'txt2img', ?, '', '')",
            (
                FLIP_TOPOLOGY,
                FLIP_CORE,
                CORE_RULE_VERSION,
                json.dumps(
                    [
                        {
                            "label": slot.label,
                            "class_type": slot.class_type,
                            "widget": slot.widget,
                            "is_lora": slot.is_lora,
                        }
                        for slot in slots(_FLIP_DOCUMENTS[FLIP_RECIPE_A])
                    ]
                ),
            ),
        )
        # The mark as B2 froze it: neither filename holds a speed-LoRA word,
        # so the guess is `recipe` and the two variants share one card.
        conn.execute(
            "INSERT INTO workflow_slot_mark (topology_hash, slot_label, mark) "
            "VALUES (?, ?, ?)",
            (FLIP_TOPOLOGY, label, guess_mark("character_ada.safetensors")),
        )

    def write(session):
        for path, structural in _FLIP_PICTURES:
            session.add(
                Picture(
                    file_path=path,
                    deleted=False,
                    created_at=_stamp("2026-08-18T00:00:00Z"),
                    workflow_topology_hash=FLIP_TOPOLOGY,
                    workflow_structural_hash=structural,
                    workflow_hash_version="v1",
                )
            )
        session.commit()

    server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)
    return merged


def _add_flip_pictures(server, structural_hash: str, count: int) -> None:
    """Give one flip variant more pictures, so which half is bigger can be set
    per test rather than only by the fixture's own counts."""

    def write(session):
        for n in range(count):
            session.add(
                Picture(
                    file_path=f"flip_extra_{structural_hash[:6]}_{n}.png",
                    deleted=False,
                    created_at=_stamp("2026-08-19T00:00:00Z"),
                    workflow_topology_hash=FLIP_TOPOLOGY,
                    workflow_structural_hash=structural_hash,
                    workflow_hash_version="v1",
                )
            )
        session.commit()

    server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


def _attr_row(server, workflow_id: str):
    return server.hub.fetchone(
        "SELECT name, notes, hidden FROM workflow_group_attr WHERE workflow_id = ?",
        (workflow_id,),
    )


def _events(server):
    """Collect every CHANGED_WORKFLOWS envelope raised while the block runs."""
    seen = []

    def listener(event_type, data=None):
        if event_type is EventType.CHANGED_WORKFLOWS:
            seen.append(data)

    server.vault.add_event_listener(listener)

    def stop():
        # The vault has no remover: this is a shared server, so a listener
        # left behind would collect the next test's events into a dead list.
        with server.vault._event_listeners_lock:
            server.vault._event_listeners.remove(listener)

    return seen, stop


def test_every_workflow_write_route_is_declared_owner_only():
    """§16.1 again, for the writes: the declaration IS the enforcement.

    Pinned separately from the reads because the reasons differ. A read is
    owner-only over a disclosure judgement; a write is owner-only because it
    is the owner rearranging their own library and no scope could describe it.
    """
    for key in _WORKFLOW_WRITE_ROUTES:
        assert key in ROUTE_POLICIES, f"{key} has no ROUTE_POLICIES entry"
        assert ROUTE_POLICIES[key].policy is AccessPolicy.OWNER_ONLY, (
            f"{key} declares {ROUTE_POLICIES[key].policy}, not OWNER_ONLY"
        )


def test_no_scoped_token_can_write_a_workflow_card(workflow_env):
    """Every write refuses a live resource-scoped share token.

    **What this measures is the verb belt, not the gate**, and saying so is
    the point: every token this suite can mint is READ, and the middleware
    refuses PATCH/PUT/POST before the gate ever reads a declaration. The gate's
    half is pinned by the declaration test above, which is why both exist.
    """
    token = _mint(
        workflow_env.owner,
        "workflow write probe",
        resource_type="character",
        resource_id=workflow_env.character_id,
    )
    client = _bearer(workflow_env.server, token)
    assert client.get(f"{API}/pictures").status_code == 200, (
        "the scoped token is dead; the refusals below would prove nothing"
    )
    for method, path, body in (
        ("PATCH", f"{API}/workflows/{BUSY_WF}", {"name": "nope"}),
        (
            "PUT",
            f"{API}/workflows/{BUSY_WF}/model-fix",
            {"was": _SHELF_FILENAME, "now": None},
        ),
        ("PUT", f"{API}/workflows/{BUSY_WF}/defaults", {"defaults": []}),
        ("PUT", f"{API}/workflows/{BUSY_WF}/pins", {"pins": []}),
        ("PUT", f"{API}/workflows/{BUSY_WF}/inputs", {"inputs": []}),
        ("POST", f"{API}/workflows/run", {"workflow_id": BUSY_WF}),
        ("POST", f"{API}/workflows/run/preflight", {"workflow_id": BUSY_WF}),
        (
            "POST",
            f"{API}/workflows/run",
            {"picture_ids": [1], "target": BUSY_WF},
        ),
        ("POST", f"{API}/workflows/{BUSY_WF}/duplicate", None),
        ("POST", f"{API}/workflows/{BUSY_WF}/insert-lora-loader", None),
        ("POST", f"{API}/workflows/{BUSY_WF}/fixed-copy", None),
        ("PUT", f"{API}/workflows/{BUSY_WF}/lora-chain", {"entries": []}),
        (
            "POST",
            f"{API}/workflows/{BUSY_WF}/clone-with-models",
            {"name": "nope", "swaps": {"a.safetensors": "b.safetensors"}},
        ),
        ("POST", f"{API}/workflows/{BUSY_WF}/set-clone-plans", {"sets": []}),
        ("DELETE", f"{API}/workflows/{BUSY_WF}", None),
        (
            "POST",
            f"{API}/comfyui/workflows/convert",
            {"workflow": _EDITOR_WORKFLOW, "output": _EDITOR_CONVERTED},
        ),
    ):
        assert_real_route(workflow_env.server.api, method, path)
        r = client.request(method, path, json=body)
        assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"
    # The positive control, and it is what makes the nine refusals above mean
    # something: the same gesture from the owner lands.
    assert (
        workflow_env.owner.patch(
            f"{API}/workflows/{BUSY_WF}", json={"name": "Owner can"}
        ).status_code
        == 200
    )


# Every route `routes/workflows.py` serves, with a request that reaches its
# handler: `(method, template, path, body)`. The completeness check in the
# test below compares this against ROUTE_POLICIES, so a route added without a
# row here fails rather than going unmeasured.
_EVERY_WORKFLOW_ROUTE = (
    ("GET", "/workflows", "/workflows", None),
    (
        "GET",
        "/workflows/recipes/{structural_hash}/graph",
        f"/workflows/recipes/{BUSY_RECIPE_A}/graph",
        None,
    ),
    ("GET", "/workflows/{workflow_id}", f"/workflows/{BUSY_WF}", None),
    (
        "GET",
        "/workflows/{workflow_id}/pictures",
        f"/workflows/{BUSY_WF}/pictures",
        None,
    ),
    ("GET", "/workflows/{workflow_id}/export", f"/workflows/{BUSY_WF}/export", None),
    ("GET", "/workflows/{workflow_id}/graph", f"/workflows/{BUSY_WF}/graph", None),
    (
        "GET",
        "/workflows/{workflow_id}/lora-chain",
        f"/workflows/{BUSY_WF}/lora-chain",
        None,
    ),
    (
        "GET",
        "/workflows/{workflow_id}/lora-summary",
        f"/workflows/{BUSY_WF}/lora-summary",
        None,
    ),
    (
        "GET",
        "/workflows/{workflow_id}/model-swap",
        f"/workflows/{BUSY_WF}/model-swap",
        None,
    ),
    ("PATCH", "/workflows/{workflow_id}", f"/workflows/{BUSY_WF}", {"notes": "n"}),
    (
        "PUT",
        "/workflows/{workflow_id}/defaults",
        f"/workflows/{BUSY_WF}/defaults",
        {"defaults": []},
    ),
    (
        "PUT",
        "/workflows/{workflow_id}/default-lora",
        f"/workflows/{BUSY_WF}/default-lora",
        {"asset": "asset:" + "0" * 64, "include": None},
    ),
    (
        "PUT",
        "/workflows/{workflow_id}/pins",
        f"/workflows/{BUSY_WF}/pins",
        {"pins": []},
    ),
    (
        "PUT",
        "/workflows/{workflow_id}/inputs",
        f"/workflows/{BUSY_WF}/inputs",
        {"inputs": []},
    ),
    (
        "PUT",
        "/workflows/{workflow_id}/model-fix",
        f"/workflows/{BUSY_WF}/model-fix",
        {"was": _SHELF_FILENAME, "now": None},
    ),
    (
        "PUT",
        "/workflows/{workflow_id}/lora-chain",
        f"/workflows/{BUSY_WF}/lora-chain",
        {"entries": []},
    ),
    (
        "POST",
        "/workflows/{workflow_id}/duplicate",
        f"/workflows/{BUSY_WF}/duplicate",
        None,
    ),
    (
        "POST",
        "/workflows/{workflow_id}/insert-lora-loader",
        f"/workflows/{BUSY_WF}/insert-lora-loader",
        None,
    ),
    (
        "POST",
        "/workflows/{workflow_id}/fixed-copy",
        f"/workflows/{BUSY_WF}/fixed-copy",
        None,
    ),
    (
        "POST",
        "/workflows/{workflow_id}/clone-with-models",
        f"/workflows/{BUSY_WF}/clone-with-models",
        {"name": "c", "swaps": {"a.safetensors": "b.safetensors"}},
    ),
    (
        "POST",
        "/workflows/{workflow_id}/set-clone-plans",
        f"/workflows/{BUSY_WF}/set-clone-plans",
        {"sets": []},
    ),
    (
        "POST",
        "/workflows/run/preflight",
        "/workflows/run/preflight",
        {"workflow_id": BUSY_WF},
    ),
    ("POST", "/workflows/run", "/workflows/run", {"workflow_id": BUSY_WF}),
    ("DELETE", "/workflows/{workflow_id}", f"/workflows/{BUSY_WF}", None),
)


def _write_enable(server, token: str) -> str:
    """Rescope a minted share token to ``WRITE``, and prove the row took it.

    `create_token` mints only READ for a scoped token, and the READ-token
    middleware refuses every non-GET before the gate reads a declaration - so
    a READ token measures the verb belt, never the declaration. A WRITE grant
    passes the belt and is refused by the gate alone.
    """

    def rescope(session):
        session.exec(
            update(UserToken)
            .where(UserToken.token_prefix == token[:8])
            .values(scope="WRITE")
        )
        session.commit()
        return [
            row.scope
            for row in session.exec(
                select(UserToken).where(UserToken.token_prefix == token[:8])
            ).all()
        ]

    assert server.hub_engine.run_task(rescope) == ["WRITE"], "the rescope missed"
    server.auth._flush_token_cache()
    return token


def test_every_workflow_route_refuses_a_scoped_grant_at_the_gate_and_answers_the_owner(
    workflow_env, tmp_path, monkeypatch
):
    """Both directions on every route of the module, measured at the gate (#1623).

    The GET belts are emptied and the grant is write-enabled, so a 403 here is
    the gate's OWNER_ONLY declaration and nothing in front of it; the owner
    reaching every handler (anything but 401/403) is the positive control that
    keeps the refusal from being a dead path.
    """
    server = workflow_env.server
    _isolate_workflow_folders(tmp_path, monkeypatch)
    monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url, **_: (None, "refused")
    )
    monkeypatch.setattr(auth, "READ_BLOCKED_GET_PATHS", frozenset())
    monkeypatch.setattr(auth, "READ_BLOCKED_GET_PREFIXES", ())
    declared = {
        (method, path[len("/api/v1") :])
        for method, path in ROUTE_POLICIES
        if path.startswith("/api/v1/workflows")
    }
    assert declared == {(m, t) for m, t, _p, _b in _EVERY_WORKFLOW_ROUTE}, (
        "a workflow route has no row in _EVERY_WORKFLOW_ROUTE (or a row names a "
        "route that is gone)"
    )
    token = _write_enable(
        server,
        _mint(
            workflow_env.owner,
            "workflow gate probe",
            resource_type="character",
            resource_id=workflow_env.character_id,
        ),
    )
    scoped = _bearer(server, token)
    assert scoped.get(f"{API}/pictures").status_code == 200, "the grant is dead"
    previously_enforcing = server.authz._enforcing
    server.authz._enforcing = True
    try:
        for method, template, path, body in _EVERY_WORKFLOW_ROUTE:
            assert_real_route(server.api, method, API + path, API + template)
            r = scoped.request(method, API + path, json=body)
            assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"
            assert "Owner-level" in r.text, f"{method} {path} not refused by the gate"
        for method, _template, path, body in _EVERY_WORKFLOW_ROUTE:
            r = workflow_env.owner.request(method, API + path, json=body)
            assert r.status_code not in (401, 403), (
                f"owner {method} {path}: {r.status_code} {r.text}"
            )
    finally:
        server.authz._enforcing = previously_enforcing


def test_naming_a_card_shows_on_the_grid_and_clearing_it_goes_back(workflow_env):
    """PATCH writes the fields sent and leaves the rest; null clears."""
    owner = workflow_env.owner
    r = owner.patch(
        f"{API}/workflows/{BUSY_WF}",
        json={"name": "My portrait workflow", "notes": "cfg 7, always"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["card"]["name"] == "My portrait workflow"
    assert r.json()["notes"] == "cfg 7, always"

    grid = owner.get(f"{API}/workflows").json()
    named = {card["id"]: card["name"] for card in grid["cards"]}
    assert named[BUSY_WF] == "My portrait workflow"
    # Written on the workflow, never on the card it happens to be made of.
    assert _attr_row(workflow_env.server, BUSY_WF)["name"] == "My portrait workflow"
    assert (
        workflow_env.server.hub.fetchone(
            "SELECT 1 FROM workflow_attr WHERE workflow_key = ?", (BUSY_CARD,)
        )
        is None
    )

    # Notes alone: the name must stand rather than be cleared by omission.
    r = owner.patch(f"{API}/workflows/{BUSY_WF}", json={"notes": "cfg 8 now"})
    assert r.json()["card"]["name"] == "My portrait workflow"
    assert r.json()["notes"] == "cfg 8 now"

    r = owner.patch(f"{API}/workflows/{BUSY_WF}", json={"name": None})
    assert r.status_code == 200, r.text
    # Back to the fallback - whatever it is - rather than to null or to the
    # name that was just cleared. What the fallback SAYS is pinned by
    # `test_a_card_is_never_nameless`; this asserts the clearing round-trips.
    cleared = r.json()["card"]["name"]
    assert cleared and cleared != "My portrait workflow"
    grid = _by_key(_cards(owner))
    assert grid[BUSY_WF]["name"] == cleared


def test_hiding_a_card_takes_it_off_the_grid_and_it_still_opens(workflow_env):
    """Hiding is a decision about the grid, never a deletion."""
    owner = workflow_env.owner
    before = owner.get(f"{API}/workflows").json()
    assert BUSY_WF in {card["id"] for card in before["cards"]}

    assert (
        owner.patch(f"{API}/workflows/{BUSY_WF}", json={"hidden": True}).status_code
        == 200
    )
    after = owner.get(f"{API}/workflows").json()
    assert BUSY_WF not in {card["id"] for card in after["cards"]}
    assert after["hidden"] == before["hidden"] + 1

    detail = owner.get(f"{API}/workflows/{BUSY_WF}")
    assert detail.status_code == 200
    assert detail.json()["hidden"] is True


def _group_defaults(hub, workflow_id) -> dict[str, str]:
    return {
        row["address"]: row["value"]
        for row in hub.fetchall(
            "SELECT address, value FROM workflow_group_default WHERE workflow_id = ?",
            (workflow_id,),
        )
    }


def _group_pins(hub, workflow_id):
    row = hub.fetchone(
        "SELECT pins FROM workflow_group_pins WHERE workflow_id = ?", (workflow_id,)
    )
    return None if row is None else json.loads(row["pins"])


def test_a_workflows_defaults_pins_and_inputs_are_written_whole(workflow_env):
    """The three whole-set writes, on the workflow tables, each read back."""
    owner, hub = workflow_env.owner, workflow_env.server.hub
    # The default recipe's model and LoRA rows are the conversion's, and a
    # parameter write must leave them standing.
    model_address = f"{_core(BUSY_RECIPE_A, '1')}/ckpt_name"
    with hub.transaction() as conn:
        conn.executemany(
            "INSERT INTO workflow_group_default (workflow_id, address, value) "
            "VALUES (?, ?, ?)",
            [
                (BUSY_WF, model_address, _SHELF_FILENAME),
                (BUSY_WF, "lora:" + _h("speed-lora"), "0.8"),
            ],
        )
    r = owner.put(
        f"{API}/workflows/{BUSY_WF}/defaults",
        json={
            "defaults": [
                {"slot_label": "slot-a", "input_name": "steps", "value": 42},
                {"slot_label": "slot-a", "input_name": "keep", "value": True},
            ]
        },
    )
    assert r.status_code == 200, r.text
    edited = {
        (default["slot_label"], default["input_name"]): default
        for default in r.json()["card"]["defaults"]
    }
    # TEXT in the table, the number and the boolean a graph takes on the wire.
    assert edited[("slot-a", "steps")]["value"] == 42
    assert edited[("slot-a", "steps")]["provenance"] == "edited"
    assert edited[("slot-a", "keep")]["value"] is True
    # A bool is stored the way a graph writes one, not as Python's `True`.
    assert _group_defaults(hub, BUSY_WF)["slot-a/keep"] == "true"
    # And the write answers with the default recipe, which a grid entry never
    # carries.
    assert r.json()["card"]["default_recipe"]["values"]
    # Nothing on the card tables.
    assert hub.fetchone("SELECT 1 FROM workflow_default_override") is None

    # Whole, so a second write with one entry leaves one parameter entry - and
    # the model and LoRA rows beside them.
    owner.put(
        f"{API}/workflows/{BUSY_WF}/defaults",
        json={
            "defaults": [{"slot_label": "slot-a", "input_name": "cfg", "value": 7.5}]
        },
    )
    assert _group_defaults(hub, BUSY_WF) == {
        "slot-a/cfg": "7.5",
        model_address: _SHELF_FILENAME,
        "lora:" + _h("speed-lora"): "0.8",
    }
    # An address naming a model or a LoRA is not a parameter, and is refused
    # rather than written over the default recipe's own rows.
    core_ckpt = _core(BUSY_RECIPE_A, "1")
    for slot_label, input_name in ((core_ckpt, "ckpt_name"), ("lora:abc", "x")):
        r = owner.put(
            f"{API}/workflows/{BUSY_WF}/defaults",
            json={
                "defaults": [
                    {"slot_label": slot_label, "input_name": input_name, "value": 1}
                ]
            },
        )
        assert r.status_code == 422, (slot_label, r.text)
    assert model_address in _group_defaults(hub, BUSY_WF)

    assert (
        owner.put(
            f"{API}/workflows/{BUSY_WF}/pins",
            json={"pins": [{"slot_label": "slot-a", "input_name": "steps"}]},
        ).status_code
        == 200
    )
    assert _group_pins(hub, BUSY_WF) == ["slot-a/steps"]
    # And READ BACK on the detail route, which is the only thing that makes
    # the pin a control rather than a write into the dark.
    assert owner.get(f"{API}/workflows/{BUSY_WF}").json()["pins"] == [
        {"slot_label": "slot-a", "input_name": "steps"}
    ]
    # `[]` is somebody who unpinned everything; null forgets the choice.
    owner.put(f"{API}/workflows/{BUSY_WF}/pins", json={"pins": []})
    assert owner.get(f"{API}/workflows/{BUSY_WF}").json()["pins"] == []
    assert _group_pins(hub, BUSY_WF) == []
    owner.put(f"{API}/workflows/{BUSY_WF}/pins", json={"pins": None})
    assert _group_pins(hub, BUSY_WF) is None
    assert owner.get(f"{API}/workflows/{BUSY_WF}").json()["pins"] is None

    # A row that is not a pin list reads as NO CHOICE, never as `[]`: a
    # corrupt row saying "the owner unpinned everything" would look
    # deliberate. A `[slot label, input name]` pair, the card tables' shape,
    # still reads.
    for corrupt, expected in (
        ("null", None),
        ("5", None),
        ('{"a": 1}', None),
        ("not json", None),
        ('[["slot-a", "steps"]]', [{"slot_label": "slot-a", "input_name": "steps"}]),
    ):
        with hub.transaction() as conn:
            conn.execute(
                "INSERT INTO workflow_group_pins (workflow_id, pins) VALUES (?, ?) "
                "ON CONFLICT(workflow_id) DO UPDATE SET pins = excluded.pins",
                (BUSY_WF, corrupt),
            )
        r = owner.get(f"{API}/workflows/{BUSY_WF}")
        assert r.status_code == 200, f"{corrupt!r}: {r.text}"
        assert r.json()["pins"] == expected, corrupt

    assert (
        owner.put(
            f"{API}/workflows/{BUSY_WF}/inputs",
            json={
                "inputs": [
                    {
                        "slot_label": "slot-a",
                        "input_name": "image",
                        "mode": "fixed",
                        "pixel_sha": _h("apicture"),
                    }
                ]
            },
        ).status_code
        == 200
    )
    library = workflow_env.server.vault.library_uuid
    assert group_picture_inputs(hub, library, BUSY_WF) == [
        {
            "slot_label": "slot-a",
            "input_name": "image",
            "mode": "fixed",
            "pixel_sha": _h("apicture"),
        }
    ]
    assert hub.fetchone("SELECT 1 FROM workflow_key_picture_input") is None
    # A fixed input with no picture is refused rather than stored as a row no
    # run could satisfy.
    assert (
        owner.put(
            f"{API}/workflows/{BUSY_WF}/inputs",
            json={
                "inputs": [
                    {
                        "slot_label": "slot-a",
                        "input_name": "image",
                        "mode": "fixed",
                        "pixel_sha": None,
                    }
                ]
            },
        ).status_code
        == 422
    )


_ADA = asset_reference("character_ada.safetensors")
_BO = asset_reference("character_bo.safetensors")


def _variant_card(server, structural_hash: str) -> str:
    """The card a variant is on - internal storage, read to see it did not move."""
    return server.hub.fetchone(
        "SELECT workflow_key FROM workflow_variant WHERE structural_hash = ?",
        (structural_hash,),
    )["workflow_key"]


def _flip_picture_ids(server, structural_hash: str) -> set[int]:
    def read(session):
        return set(
            session.exec(
                select(Picture.id).where(
                    Picture.workflow_structural_hash == structural_hash
                )
            ).all()
        )

    return server.vault.db.run_immediate_read_task(read)


def test_a_lora_summary_says_which_loras_change_and_how_often(workflow_env):
    """Both character LoRAs change, most pictures first, with their pictures.

    Nothing is shared: no LoRA is in all four pictures. Each row names the
    file, its count and a strip of that file's own pictures, never the other
    file's.
    """
    owner, server = workflow_env.owner, workflow_env.server
    _seed_flip_fixture(server)
    a_ids = _flip_picture_ids(server, FLIP_RECIPE_A)

    r = owner.get(
        f"{API}/workflows/{FLIP_WF}/lora-summary", params={"cover": min(a_ids)}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pictures"] == 4
    assert body["shared"] == []
    assert body["without"] is None
    assert [(use["asset"], use["pictures"]) for use in body["varying"]] == [
        (_ADA, 3),
        (_BO, 1),
    ]
    ada = body["varying"][0]
    assert ada["filename"] == "character_ada.safetensors"
    assert ada["name"] == "character ada"
    # A card key and a promotion are gone from the pile with the stack (#1623).
    assert "members" not in ada and "promoted" not in ada
    assert set(ada["picture_ids"]) == a_ids
    assert set(body["varying"][1]["picture_ids"]) == _flip_picture_ids(
        server, FLIP_RECIPE_B
    )
    assert body["cover_asset"] == _ADA
    # The other direction: the cover names B's file when it is B's picture.
    b_cover = min(_flip_picture_ids(server, FLIP_RECIPE_B))
    assert (
        owner.get(
            f"{API}/workflows/{FLIP_WF}/lora-summary", params={"cover": b_cover}
        ).json()["cover_asset"]
        == _BO
    )


_BO_DIGEST = _h("character-bo-digest")


def test_a_lora_from_the_pile_goes_into_the_default_recipe_and_back(workflow_env):
    """*Add to default* (#1653): one LoRA of the pile in, then the edit dropped.

    The route takes the pile's `asset:` reference (a hash of the NAME), stores
    the shelf's digest, and the detail names the LoRA by that same asset, so a
    client can join the default list to the pile. Every other default stands.
    """
    owner, server = workflow_env.owner, workflow_env.server
    _seed_flip_fixture(server)
    route = f"{API}/workflows/{FLIP_WF}/default-lora"
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, kind, filename, sha256, provenance) "
            "VALUES ('adapter', 'unknown', 'character_bo.safetensors', ?, 'scanned')",
            (_BO_DIGEST,),
        )
        conn.execute(
            "INSERT INTO workflow_group_default (workflow_id, address, value) "
            "VALUES (?, 'core:x/steps', '30')",
            (FLIP_WF,),
        )
    try:
        # The pile says which LoRAs can go in: the one it can name by digest.
        pile = owner.get(f"{API}/workflows/{FLIP_WF}/lora-summary").json()
        assert {use["asset"]: use["sha256"] for use in pile["varying"]} == {
            _ADA: None,
            _BO: _BO_DIGEST,
        }
        r = owner.put(route, json={"asset": _BO, "include": True})
        assert r.status_code == 200, r.text
        loras = r.json()["card"]["default_recipe"]["loras"]
        (bo,) = [lora for lora in loras if lora["sha256"] == _BO_DIGEST]
        assert bo["asset"] == _BO
        assert bo["filename"] == "character_bo.safetensors"
        assert bo["provenance"] == "edited"
        # No run of it records a strength, so it goes in at 1.
        assert bo["strength"] == 1.0
        rows = dict(
            server.hub.fetchall(
                "SELECT address, value FROM workflow_group_default "
                "WHERE workflow_id = ?",
                (FLIP_WF,),
            )
        )
        assert rows == {"core:x/steps": "30", "lora:" + _BO_DIGEST: "1.0"}

        r = owner.put(route, json={"asset": _BO, "include": None})
        assert r.status_code == 200, r.text
        assert _BO_DIGEST not in {
            lora["sha256"] for lora in r.json()["card"]["default_recipe"]["loras"]
        }
        assert [
            tuple(row)
            for row in server.hub.fetchall(
                "SELECT address FROM workflow_group_default WHERE workflow_id = ?",
                (FLIP_WF,),
            )
        ] == [("core:x/steps",)]

        # Not on the shelf: nothing can name it, so nothing is written.
        assert (
            owner.put(route, json={"asset": _ADA, "include": True}).status_code == 409
        )
        # Not one of this workflow's LoRAs at all.
        other = asset_reference("somebody_else.safetensors")
        assert (
            owner.put(route, json={"asset": other, "include": True}).status_code == 404
        )
        assert (
            owner.put(
                route, json={"asset": "character_bo", "include": True}
            ).status_code
            == 422
        )
        # An explicit strength is stored as given; `false` keeps it out.
        r = owner.put(route, json={"asset": _BO, "include": True, "strength": 0.4})
        assert [
            lora["strength"]
            for lora in r.json()["card"]["default_recipe"]["loras"]
            if lora["asset"] == _BO
        ] == [0.4]
        r = owner.put(route, json={"asset": _BO, "include": False})
        assert r.status_code == 200, r.text
        assert _BO not in {
            lora["asset"] for lora in r.json()["card"]["default_recipe"]["loras"]
        }
        assert dict(
            server.hub.fetchall(
                "SELECT address, value FROM workflow_group_default "
                "WHERE workflow_id = ? AND address LIKE 'lora:%'",
                (FLIP_WF,),
            )
        ) == {"lora:" + _BO_DIGEST: "off"}
        owner.put(route, json={"asset": _BO, "include": None})

        # An edit stays removable after its file leaves the shelf, by the
        # digest the default recipe names it with.
        owner.put(route, json={"asset": _BO, "include": True})
        with server.hub.transaction() as conn:
            conn.execute("DELETE FROM model WHERE sha256 = ?", (_BO_DIGEST,))
        assert owner.put(route, json={"asset": _BO, "include": None}).status_code == 404
        r = owner.put(route, json={"sha256": _BO_DIGEST, "include": None})
        assert r.status_code == 200, r.text
        assert not server.hub.fetchall(
            "SELECT 1 FROM workflow_group_default WHERE workflow_id = ? "
            "AND address LIKE 'lora:%'",
            (FLIP_WF,),
        )
        # Adding names the LoRA by its asset; clearing needs one of the two.
        assert (
            owner.put(route, json={"sha256": _BO_DIGEST, "include": True}).status_code
            == 422
        )
        assert owner.put(route, json={"include": None}).status_code == 422
        with server.hub.transaction() as conn:
            conn.execute(
                "INSERT INTO model (file_kind, kind, filename, sha256, provenance) "
                "VALUES ('adapter', 'unknown', 'character_bo.safetensors', ?, "
                "'scanned')",
                (_BO_DIGEST,),
            )

        # A LoRA edit whose digest the shelf holds only as a checkpoint is not
        # named after the checkpoint: the default recipe names LoRA files.
        ckpt = _h("bo-as-checkpoint")
        with server.hub.transaction() as conn:
            conn.execute(
                "INSERT INTO model (file_kind, filename, sha256, provenance) "
                "VALUES ('checkpoint', 'not_a_lora.safetensors', ?, 'scanned')",
                (ckpt,),
            )
            conn.execute(
                "INSERT INTO workflow_group_default (workflow_id, address, value) "
                "VALUES (?, ?, '1.0')",
                (FLIP_WF, "lora:" + ckpt),
            )
        (odd,) = [
            lora
            for lora in owner.get(f"{API}/workflows/{FLIP_WF}").json()["card"][
                "default_recipe"
            ]["loras"]
            if lora["sha256"] == ckpt
        ]
        assert (odd["asset"], odd["filename"]) == ("", None)
        with server.hub.transaction() as conn:
            conn.execute("DELETE FROM model WHERE sha256 = ?", (ckpt,))
            conn.execute(
                "DELETE FROM workflow_group_default WHERE address = ?",
                ("lora:" + ckpt,),
            )

        # Two shelf files by that name: which one is a guess, so it is refused.
        with server.hub.transaction() as conn:
            conn.execute(
                "INSERT INTO model (file_kind, kind, filename, sha256, provenance) "
                "VALUES ('adapter', 'unknown', 'character_bo.safetensors', ?, "
                "'scanned')",
                (_h("character-bo-other"),),
            )
        assert owner.put(route, json={"asset": _BO, "include": True}).status_code == 409
    finally:
        with server.hub.transaction() as conn:
            conn.execute(
                "DELETE FROM model WHERE sha256 IN (?, ?)",
                (_BO_DIGEST, _h("character-bo-other")),
            )


def test_an_added_lora_takes_the_strength_its_own_runs_used(workflow_env):
    """Read off the runs that loaded it, not the default recipe's sample.

    B's one picture ran it at 0.65; nothing else did, so 0.65 it is.
    """
    owner, server = workflow_env.owner, workflow_env.server
    _seed_flip_fixture(server)
    instance = _h("flip-b-instance")
    document = json.loads(json.dumps(_FLIP_DOCUMENTS[FLIP_RECIPE_B]))
    document["2"]["inputs"]["strength_model"] = 0.65
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, kind, filename, sha256, provenance) "
            "VALUES ('adapter', 'unknown', 'character_bo.safetensors', ?, 'scanned')",
            (_BO_DIGEST,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO workflow_recipe_instance "
            "(library_uuid, instance_hash, structural_hash, hash_version, "
            "document, first_seen_at) VALUES (?, ?, ?, 'v1', ?, "
            "'2026-09-01T00:00:00Z')",
            (server.vault.library_uuid, instance, FLIP_RECIPE_B, json.dumps(document)),
        )

    def run_b(session):
        session.exec(
            update(Picture)
            .where(Picture.workflow_structural_hash == FLIP_RECIPE_B)
            .values(workflow_instance_hash=instance)
        )
        session.commit()

    server.vault.db.run_task(run_b, priority=DBPriority.IMMEDIATE)
    try:
        r = owner.put(
            f"{API}/workflows/{FLIP_WF}/default-lora",
            json={"asset": _BO, "include": True},
        )
        assert r.status_code == 200, r.text
        assert [
            lora["strength"]
            for lora in r.json()["card"]["default_recipe"]["loras"]
            if lora["asset"] == _BO
        ] == [0.65]
    finally:
        with server.hub.transaction() as conn:
            conn.execute("DELETE FROM model WHERE sha256 = ?", (_BO_DIGEST,))


def test_a_lora_in_every_picture_is_shared_not_piled(workflow_env):
    """The positive control for "shared": one variant only, so its LoRA is in
    every picture and nothing changes."""
    owner, server = workflow_env.owner, workflow_env.server
    _seed_flip_fixture(server)

    def bin_b(session):
        for picture in session.exec(
            select(Picture).where(Picture.workflow_structural_hash == FLIP_RECIPE_B)
        ).all():
            picture.deleted = True
        session.commit()

    server.vault.db.run_task(bin_b, priority=DBPriority.IMMEDIATE)
    body = owner.get(f"{API}/workflows/{FLIP_WF}/lora-summary").json()
    assert body["pictures"] == 3
    assert [use["asset"] for use in body["shared"]] == [_ADA]
    assert body["varying"] == []
    assert body["without"] is None


def test_the_picture_grid_narrows_a_workflow_to_one_lora(workflow_env):
    """*Show N*: the workflow's pictures that loaded one file, and no others."""
    owner, server = workflow_env.owner, workflow_env.server
    _seed_flip_fixture(server)

    def ids(**params):
        r = owner.get(f"{API}/pictures", params=params)
        assert r.status_code == 200, r.text
        return {picture["id"] for picture in r.json()}

    a_ids = _flip_picture_ids(server, FLIP_RECIPE_A)
    b_ids = _flip_picture_ids(server, FLIP_RECIPE_B)
    # The control: the workflow alone is both halves.
    assert ids(workflow=FLIP_WF) == a_ids | b_ids
    assert ids(workflow=FLIP_WF, workflow_lora=_ADA) == a_ids
    assert ids(workflow=FLIP_WF, workflow_lora=_BO) == b_ids
    # Only ever a narrowing: alone, or malformed, it matches nothing rather
    # than parsing every stored graph for whoever asked.
    assert ids(workflow_lora=_ADA) == set()
    assert ids(workflow=FLIP_WF, workflow_lora="character_ada") == set()
    # And the summary names the workflow it counted, which is what Show N sends.
    assert (
        owner.get(f"{API}/workflows/{FLIP_WF}/lora-summary").json()["workflow_id"]
        == FLIP_WF
    )

    # A manual workflow's own runs narrow the same way, and only its own.
    manual = create_manual_workflow(
        server.hub, "Mine", {"1": {"class_type": "SaveImage", "inputs": {}}}, "import"
    )
    mine_a, mine_b = min(a_ids), min(b_ids)
    try:
        comfyui_service._set_run_workflow_id(server, manual, [mine_a, mine_b])
        assert ids(workflow=manual) == {mine_a, mine_b}
        assert ids(workflow=manual, workflow_lora=_ADA) == {mine_a}
        assert ids(workflow=manual, workflow_lora=_BO) == {mine_b}
        assert ids(workflow=FLIP_WF, workflow_lora=_ADA) == a_ids - {mine_a}
    finally:
        delete_manual_workflow(server.hub, manual)


def test_replacing_a_missing_model_keeps_the_card_and_flags_its_old_pictures(
    workflow_env, monkeypatch
):
    """A fixed checkpoint: same card, its pictures flagged as the old model's.

    Both flip variants load `realvisxl`, so after the fix every cover the card
    has is a picture of the original model, and the card is still at its key.
    """
    owner, server = workflow_env.owner, workflow_env.server
    merged = _seed_flip_fixture(server)
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, filename, sha256, provenance) "
            "VALUES ('checkpoint', ?, ?, 'scanned')",
            (_REPLACEMENT_FILENAME, _h("bf16-digest")),
        )
        conn.execute(
            "INSERT INTO model (file_kind, filename, sha256, provenance) "
            "VALUES ('vae', 'test-vae-bf16.safetensors', ?, 'scanned')",
            (_h("vae-digest"),),
        )
    route = f"{API}/workflows/{FLIP_WF}/model-fix"

    assert (
        owner.put(
            route, json={"was": _SHELF_FILENAME, "now": "realvisxl.safetensors"}
        ).status_code
        == 422
    )
    assert (
        owner.put(
            route, json={"was": _SHELF_FILENAME, "now": "test-not-on-shelf.safetensors"}
        ).status_code
        == 404
    )
    assert (
        owner.put(
            route, json={"was": "test-other.safetensors", "now": _REPLACEMENT_FILENAME}
        ).status_code
        == 409
    )
    assert (
        owner.put(route, json={"was": _SHELF_FILENAME, "now": None}).status_code == 409
    )
    # #1596: the replacement's kind is the slot's. The workflow loads its
    # checkpoint in no VAE slot, and a checkpoint is no VAE.
    r = owner.put(
        route, json={"was": _SHELF_FILENAME, "now": "test-vae-bf16.safetensors"}
    )
    assert r.status_code == 409 and "as a VAE" in r.json()["detail"], r.text
    r = owner.put(
        route,
        json={
            "was": _SHELF_FILENAME,
            "now": _REPLACEMENT_FILENAME,
            "slot_kind": "vae",
        },
    )
    assert r.status_code == 422 and "not a VAE" in r.json()["detail"], r.text
    # Every kind the shelf holds the file under, never just the first.
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, filename, sha256, provenance) "
            "VALUES ('text_encoder', ?, ?, 'scanned')",
            (_REPLACEMENT_FILENAME, _h("te-digest")),
        )
    r = owner.put(
        route,
        json={
            "was": _SHELF_FILENAME,
            "now": _REPLACEMENT_FILENAME,
            "slot_kind": "vae",
        },
    )
    assert r.json()["detail"] == (
        "That is a checkpoint or a text encoder, not a VAE."
    ), r.text
    # The workflow loading `was` in slots of both of those kinds, and the
    # caller not saying which: refused, never one fixed and one left missing.
    real_labels = workflows_routes.model_fix_labels
    monkeypatch.setattr(
        workflows_routes,
        "model_fix_labels",
        lambda hub, topology, was, kind: [f"test-slot/{kind}"],
    )
    r = owner.put(route, json={"was": _SHELF_FILENAME, "now": _REPLACEMENT_FILENAME})
    assert r.status_code == 409, r.text
    assert "as a checkpoint and a text encoder" in r.json()["detail"], r.text
    monkeypatch.setattr(workflows_routes, "model_fix_labels", real_labels)
    # Shelf kinds the workflow loads `was` as none of: said, never a guess.
    with server.hub.transaction() as conn:
        conn.executemany(
            "INSERT INTO model (file_kind, filename, sha256, provenance) "
            "VALUES (?, 'test-support-pair.safetensors', ?, 'scanned')",
            [("vae", _h("pair-vae")), ("text_encoder", _h("pair-te"))],
        )
    r = owner.put(
        route, json={"was": _SHELF_FILENAME, "now": "test-support-pair.safetensors"}
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "This workflow does not load that model as a text encoder or a VAE."
    ), r.text
    with server.hub.transaction() as conn:
        conn.execute(
            "DELETE FROM model WHERE sha256 IN (?, ?)",
            (_h("pair-vae"), _h("pair-te")),
        )
    with server.hub.transaction() as conn:
        conn.execute("DELETE FROM model WHERE sha256 = ?", (_h("te-digest"),))

    r = owner.put(route, json={"was": _SHELF_FILENAME, "now": _REPLACEMENT_FILENAME})
    assert r.status_code == 200, r.text
    detail = r.json()
    # The workflow keeps its id: the fix re-keys cards, which are internal.
    assert detail["card"]["id"] == FLIP_WF
    assert _variant_card(server, FLIP_RECIPE_A) == merged
    (fix,) = detail["model_fixes"]
    assert (fix["was"], fix["now"], fix["slot_kind"]) == (
        _SHELF_FILENAME,
        _REPLACEMENT_FILENAME,
        "checkpoint",
    )
    covers = detail["card"]["covers"]
    assert covers and all(cover["superseded"] for cover in covers)

    # The replacement goes missing too: the next pick replaces the ORIGINAL,
    # never the middle link, so the card is keyed and run on one step.
    r = owner.put(
        route, json={"was": _REPLACEMENT_FILENAME, "now": "add_detail.safetensors"}
    )
    assert r.status_code == 404, "a LoRA is no shelf checkpoint"
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, filename, sha256, provenance) "
            "VALUES ('checkpoint', ?, ?, 'scanned')",
            (_SECOND_REPLACEMENT, _h("fp16-digest")),
        )
    r = owner.put(
        route,
        json={"was": _REPLACEMENT_FILENAME, "now": _SECOND_REPLACEMENT.upper()},
    )
    assert r.status_code == 200, r.text
    assert [(f["was"], f["now"]) for f in r.json()["model_fixes"]] == [
        (_SHELF_FILENAME, _SECOND_REPLACEMENT)
    ], "the chain was stored, or the client's spelling rather than the shelf's"

    r = owner.put(route, json={"was": _SECOND_REPLACEMENT, "now": None})
    assert r.status_code == 200, r.text
    assert r.json()["model_fixes"] == []
    assert not any(cover["superseded"] for cover in r.json()["card"]["covers"])

    # Two originals replaced by one file in two slots: naming that file is
    # ambiguous, and fixing one original would leave the other missing.
    with server.hub.transaction() as conn:
        conn.executemany(
            "INSERT INTO workflow_model_fix (topology_hash, slot_label, was_norm, "
            "now_norm, was_name, now_name) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    FLIP_TOPOLOGY,
                    label,
                    was,
                    _REPLACEMENT_FILENAME,
                    was,
                    _REPLACEMENT_FILENAME,
                )
                for label, was in (
                    ("test-slot-a/ckpt_name", "test-a.safetensors"),
                    ("test-slot-b/ckpt_name", "test-b.safetensors"),
                )
            ],
        )
    r = owner.put(
        route, json={"was": _REPLACEMENT_FILENAME, "now": _SECOND_REPLACEMENT}
    )
    assert r.status_code == 409, r.text
    assert "test-a.safetensors" in r.json()["detail"]
    # Undoing is not ambiguous: every slot goes back to its own original.
    # An undo of one kind leaves a fix of another kind alone.
    r = owner.put(
        route,
        json={"was": _REPLACEMENT_FILENAME, "now": None, "slot_kind": "vae"},
    )
    assert r.status_code == 409, r.text
    r = owner.put(route, json={"was": _REPLACEMENT_FILENAME, "now": None})
    assert r.status_code == 200, r.text
    assert r.json()["model_fixes"] == []


def test_a_missing_replacement_is_replaced_from_the_original_whatever_the_shelf(
    workflow_env,
):
    """The chain again, with the new file on the shelf under two kinds.

    No variant was made with the first replacement, so no stored graph names
    it: the kind it is loaded as is the one its fix recorded, and the second
    pick replaces the ORIGINAL rather than being refused.
    """
    owner, server = workflow_env.owner, workflow_env.server
    _seed_flip_fixture(server)
    with server.hub.transaction() as conn:
        conn.executemany(
            "INSERT INTO model (file_kind, filename, sha256, provenance) "
            "VALUES (?, ?, ?, 'scanned')",
            [
                ("checkpoint", _REPLACEMENT_FILENAME, _h("chain-bf16")),
                ("checkpoint", "test-chain-pair.safetensors", _h("chain-ckpt")),
                ("vae", "test-chain-pair.safetensors", _h("chain-vae")),
            ],
        )
    route = f"{API}/workflows/{FLIP_WF}/model-fix"
    try:
        r = owner.put(
            route, json={"was": _SHELF_FILENAME, "now": _REPLACEMENT_FILENAME}
        )
        assert r.status_code == 200, r.text
        assert r.json()["card"]["id"] == FLIP_WF
        r = owner.put(
            route,
            json={"was": _REPLACEMENT_FILENAME, "now": "test-chain-pair.safetensors"},
        )
        assert r.status_code == 200, r.text
        assert [
            (f["was"], f["now"], f["slot_kind"]) for f in r.json()["model_fixes"]
        ] == [(_SHELF_FILENAME, "test-chain-pair.safetensors", "checkpoint")]
    finally:
        with server.hub.transaction() as conn:
            conn.execute("DELETE FROM workflow_model_fix")
            conn.execute(
                "DELETE FROM model WHERE sha256 IN (?, ?, ?)",
                (_h("chain-bf16"), _h("chain-ckpt"), _h("chain-vae")),
            )


def test_every_write_says_which_workflows_to_look_at_again(workflow_env):
    """`CHANGED_WORKFLOWS` on each write, naming WORKFLOW ids (#1623).

    A "look again" signal, so what is asserted is that it is raised at all,
    that it names the workflow the gesture was about - never a card key, which
    a client can no longer address - and where it came from.
    """
    owner, server = workflow_env.owner, workflow_env.server
    seen, stop = _events(server)
    try:
        owner.patch(
            f"{API}/workflows/{BUSY_WF}",
            json={"name": "Announced"},
            headers={"X-Client-Id": "example-tab"},
        )
        owner.put(f"{API}/workflows/{BUSY_WF}/pins", json={"pins": []})
        owner.put(f"{API}/workflows/{BUSY_WF}/defaults", json={"defaults": []})
    finally:
        stop()
    assert [event["reason"] for event in seen] == ["changed", "changed", "changed"]
    assert [event["keys"] for event in seen] == [[BUSY_WF]] * 3
    assert seen[0]["origin_client_id"] == "example-tab"
    assert seen[0]["source"] == "ui"


def test_an_unknown_workflow_cannot_be_written_to(workflow_env):
    """A 404 rather than a row against a workflow this hub never had."""
    owner = workflow_env.owner
    unknown = AUTO_STACK_PREFIX + _h("nosuchcore")
    for method, path, body in (
        ("PATCH", f"{API}/workflows/{unknown}", {"name": "x"}),
        ("PUT", f"{API}/workflows/{unknown}/defaults", {"defaults": []}),
        ("PUT", f"{API}/workflows/{unknown}/pins", {"pins": []}),
        ("PUT", f"{API}/workflows/{unknown}/inputs", {"inputs": []}),
        (
            "PUT",
            f"{API}/workflows/{unknown}/model-fix",
            {"was": _SHELF_FILENAME, "now": None},
        ),
    ):
        assert_real_route(workflow_env.server.api, method, path)
        r = owner.request(method, path, json=body)
        assert r.status_code == 404, f"{method} {path}: {r.status_code} {r.text}"
    assert _attr_row(workflow_env.server, unknown) is None
    for path in (f"{API}/workflows/not-a-digest", f"{API}/workflows/{BUSY_CARD}"):
        assert owner.patch(path, json={"name": "x"}).status_code == 422, path


def test_the_card_routes_the_cut_over_removed_are_gone(workflow_env):
    """#1623: the mark flip, the stacks and the promotion have no route left.

    Each answers as a path nothing serves - never a 200 that writes a card
    table no reader looks at any more.
    """
    owner = workflow_env.owner
    stack_id = BUSY_WF
    for method, path, body in (
        ("PUT", f"{API}/workflows/{BUSY_WF}/slots", {"marks": {}}),
        ("POST", f"{API}/workflows/{BUSY_WF}/unstack", None),
        (
            "PUT",
            f"{API}/workflows/{BUSY_WF}/lora-promotion",
            {"asset": "asset:" + "a" * 64, "promoted": True},
        ),
        ("POST", f"{API}/workflows/stacks", {"keys": [BUSY_CARD, FORGOTTEN_CARD]}),
        ("PUT", f"{API}/workflows/stacks/{stack_id}/order", {"keys": [BUSY_CARD]}),
        ("POST", f"{API}/workflows/stacks/{stack_id}/unstack", None),
    ):
        r = owner.request(method, path, json=body)
        assert r.status_code in (404, 405), f"{method} {path}: {r.status_code}"
        assert (method, "/api/v1" + path[len(API) :]) not in ROUTE_POLICIES
    for method, template in (
        ("PUT", "/api/v1/workflows/{workflow_key}/slots"),
        ("POST", "/api/v1/workflows/stacks"),
        ("PUT", "/api/v1/workflows/{workflow_key}/lora-promotion"),
    ):
        assert (method, template) not in ROUTE_POLICIES
    assert workflow_env.server.hub.fetchone("SELECT 1 FROM workflow_stack") is None


def test_naming_one_parameter_twice_is_refused_rather_than_a_500(workflow_env):
    """Both whole-set writes, both keyed on `(slot_label, input_name)`.

    The sibling constraint on the same table (a `fixed` input with no picture)
    is pre-validated; this one reached the database as an uncaught
    IntegrityError, which is a 500 for what is a bad request.
    """
    owner = workflow_env.owner
    assert (
        owner.put(
            f"{API}/workflows/{BUSY_WF}/defaults",
            json={
                "defaults": [
                    {"slot_label": "s", "input_name": "steps", "value": 1},
                    {"slot_label": "s", "input_name": "steps", "value": 2},
                ]
            },
        ).status_code
        == 422
    )
    assert (
        owner.put(
            f"{API}/workflows/{BUSY_WF}/inputs",
            json={
                "inputs": [
                    {"slot_label": "s", "input_name": "image", "mode": "picker"},
                    {"slot_label": "s", "input_name": "image", "mode": "selection"},
                ]
            },
        ).status_code
        == 422
    )
    # The same address on two different slots is not a duplicate.
    assert (
        owner.put(
            f"{API}/workflows/{BUSY_WF}/defaults",
            json={
                "defaults": [
                    {"slot_label": "a", "input_name": "steps", "value": 1},
                    {"slot_label": "b", "input_name": "steps", "value": 2},
                ]
            },
        ).status_code
        == 200
    )


def _shelve_replacement(server) -> None:
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, filename, sha256, provenance) "
            "VALUES ('checkpoint', ?, ?, 'scanned')",
            (_REPLACEMENT_FILENAME, _h("bf16-digest")),
        )


def test_a_model_fix_that_cannot_move_the_recipes_says_which_keys_to_repair(
    workflow_env, monkeypatch, caplog
):
    """The hub commits first, so the second write's failure needs a record.

    A model fix re-keys the cards of its topology internally and then moves
    the vault's saved recipes after them; the log has to carry the map, not
    just a count, or the only trace is recipes on keys no variant carries.
    """
    owner, server = workflow_env.owner, workflow_env.server
    _seed_flip_fixture(server)
    _shelve_replacement(server)

    def _fails(vault, moved):
        raise RuntimeError("the vault went away mid-fix")

    monkeypatch.setattr(saved_recipe_service, "rekey_recipes", _fails)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError):
            owner.put(
                f"{API}/workflows/{FLIP_WF}/model-fix",
                json={"was": _SHELF_FILENAME, "now": _REPLACEMENT_FILENAME},
            )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "could not move the saved recipes" in logged
    assert FLIP_TOPOLOGY in logged, "the log has to name what a repair would need"
    # And the hub half did land, which is what makes the record necessary.
    assert server.hub.fetchone(
        "SELECT 1 FROM workflow_model_fix WHERE topology_hash = ?", (FLIP_TOPOLOGY,)
    )


def test_a_model_fix_needs_an_open_library_and_says_so(workflow_env, monkeypatch):
    """503 rather than an arbitrary merge winner and orphaned recipes."""
    owner, server = workflow_env.owner, workflow_env.server
    _seed_flip_fixture(server)
    _shelve_replacement(server)
    # The property's own backing attribute, because that is what "no library
    # open" is: `Vault.library_uuid` reads `_library_uuid` and has no setter.
    monkeypatch.setattr(server.vault, "_library_uuid", None)
    r = owner.put(
        f"{API}/workflows/{FLIP_WF}/model-fix",
        json={"was": _SHELF_FILENAME, "now": _REPLACEMENT_FILENAME},
    )
    assert r.status_code == 503, r.text
    # Nothing was written.
    assert server.hub.fetchone("SELECT 1 FROM workflow_model_fix") is None


def test_a_picture_input_needs_an_open_library_and_says_so(workflow_env, monkeypatch):
    """The 503 `PUT /inputs` publishes in its own `responses=`.

    The setup is stored per library because a `fixed` input names a picture by
    content, so with none open there is no library column to write and the
    route says that rather than inventing one.
    """
    owner, server = workflow_env.owner, workflow_env.server
    # The property's own backing attribute, because that is what "no library
    # open" is: `Vault.library_uuid` reads `_library_uuid` and has no setter,
    # so patching the name would only prove that a fake can be attached.
    monkeypatch.setattr(server.vault, "_library_uuid", None)

    assert (
        owner.put(
            f"{API}/workflows/{BUSY_WF}/inputs",
            json={
                "inputs": [
                    {"slot_label": "s", "input_name": "image", "mode": "selection"}
                ]
            },
        ).status_code
        == 503
    )


# ===========================================================================
# Running a card (v1.12 B7) — POST /workflows/run and its pre-flight
# ===========================================================================

# A card that CAN run, seeded per test beside the fixture's own. The seeded
# cards deliberately have no save node, which is a reason code rather than a
# runnable workflow, so one card here carries the whole happy path and every
# refusal below is measured as a change from it.
RUN_TOPOLOGY = _h("runtopology")
RUN_RECIPE = _h("runrecipe")
RUN_CARD = _h("runcard")
RUN_CORE = _h("runcore")
RUN_WF = auto_workflow_id(RUN_CORE, "")
RUN_INSTANCE = _h("runinstance")

RUN_DOCUMENT = {
    "1": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": asset_reference("realvisxl.safetensors")},
    },
    "2": {
        "class_type": "LoraLoader",
        "inputs": {
            "lora_name": asset_reference("add_detail.safetensors"),
            "strength_model": None,
            "strength_clip": None,
            "model": ["1", 0],
        },
    },
    "3": {
        "class_type": "KSampler",
        "inputs": {"steps": None, "cfg": None, "seed": None, "model": ["2", 0]},
    },
    "4": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": None, "images": ["3", 0]},
    },
}

# What a healthy ComfyUI would answer for RUN_DOCUMENT. Every refusal test
# below takes this and removes exactly one thing, so the reason it asserts is
# the only difference from a graph that runs.
RUN_OBJECT_INFO = {
    "KSampler": {
        "input": {
            "required": {
                "seed": ["INT", {"default": 0}],
                "steps": ["INT", {"default": 20}],
                "cfg": ["FLOAT", {"default": 7.0}],
            }
        }
    },
    "CheckpointLoaderSimple": {
        "input": {"required": {"ckpt_name": [["realvisxl.safetensors"], {}]}}
    },
    "LoraLoader": {
        # Its wiring is declared as a real ComfyUI declares it, because #1463's
        # bypass reads exactly that: which of this node's inputs stands in for
        # each of its outputs once the node is gone.
        "input": {
            "required": {
                "model": ["MODEL", {}],
                "clip": ["CLIP", {}],
                "lora_name": [["add_detail.safetensors", "other.safetensors"], {}],
                "strength_model": ["FLOAT", {"default": 1.0}],
                "strength_clip": ["FLOAT", {"default": 1.0}],
            }
        },
        "output": ["MODEL", "CLIP"],
    },
    "SaveImage": {"input": {"required": {"filename_prefix": ["STRING", {}]}}},
}


# The LoRA on the shelf for the run tests, named so it resolves to the SECOND
# of the loader's two options: matching the one the graph already holds would
# pass whether or not the adapter was ever placed.
RUN_ADAPTER_DIGEST = _h("add-detail-digest")
RUN_ADAPTER_FILENAME = "other.safetensors"

# The runnable card's picture has CONTENT, unlike the module's other fixtures.
# A `fixed` picture input names its picture by `pixel_sha`, so without one the
# "is it still here" read can only ever be asked about a picture that is not,
# which is the half of that branch that needs no code to pass.
RUN_PIXEL_SHA = _h("run-one-pixels")


def _seed_runnable_card(server) -> int:
    """Add RUN_CARD, its variant, its instance and one picture on it.

    Written per test rather than into ``_seed_hub``, because a card with a save
    node changes the grid, the covers and the defaults every other test in this
    module asserts. Returns that picture's id.
    """
    with server.hub.transaction() as conn:
        conn.execute("DELETE FROM model WHERE sha256 = ?", (RUN_ADAPTER_DIGEST,))
        conn.execute(
            # `kind` is NOT NULL for an adapter by CHECK constraint: every
            # producer supplies an algorithm, 'unknown' included.
            "INSERT INTO model (file_kind, kind, filename, sha256, provenance) "
            "VALUES ('adapter', 'unknown', ?, ?, 'scanned')",
            (RUN_ADAPTER_FILENAME, RUN_ADAPTER_DIGEST),
        )
        conn.execute(
            "INSERT OR REPLACE INTO workflow_topology "
            "(topology_hash, hash_version, node_count, first_seen_at) "
            "VALUES (?, 'v1', ?, ?)",
            (RUN_TOPOLOGY, 4, "2026-09-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO workflow_topology_core "
            "(topology_hash, core_hash, core_version, workflow_type, slots, "
            "specials, traits) VALUES (?, ?, ?, ?, ?, '', '')",
            (
                RUN_TOPOLOGY,
                RUN_CORE,
                CORE_RULE_VERSION,
                "txt2img",
                json.dumps(
                    [
                        {
                            "label": slot.label,
                            "class_type": slot.class_type,
                            "widget": slot.widget,
                            "is_lora": slot.is_lora,
                        }
                        for slot in slots(RUN_DOCUMENT)
                    ]
                ),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO workflow_recipe "
            "(structural_hash, topology_hash, hash_version, node_count, first_seen_at) "
            "VALUES (?, ?, 'v1', ?, ?)",
            (RUN_RECIPE, RUN_TOPOLOGY, 4, "2026-09-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO workflow_recipe_graph "
            "(structural_hash, document_sha256, document, created_at) "
            "VALUES (?, ?, ?, '2026-09-01T00:00:00Z')",
            (
                RUN_RECIPE,
                hashlib.sha256(json.dumps(RUN_DOCUMENT).encode()).hexdigest(),
                json.dumps(RUN_DOCUMENT),
            ),
        )
        for widget, filename in (
            ("ckpt_name", "realvisxl.safetensors"),
            ("lora_name", "add_detail.safetensors"),
        ):
            conn.execute(
                "INSERT OR REPLACE INTO workflow_recipe_asset "
                "(structural_hash, widget_name, normalized_filename) VALUES (?, ?, ?)",
                (RUN_RECIPE, widget, filename),
            )
        conn.execute(
            "INSERT OR REPLACE INTO workflow_variant "
            "(structural_hash, topology_hash, workflow_key, key_version) "
            "VALUES (?, ?, ?, ?)",
            (RUN_RECIPE, RUN_TOPOLOGY, RUN_CARD, WORKFLOW_KEY_VERSION),
        )
        # Hand-written cards name no base model: the empty family set.
        conn.execute(
            "INSERT OR IGNORE INTO workflow_variant_family (structural_hash, families) "
            "SELECT structural_hash, '' FROM workflow_variant"
        )
        instance = json.loads(json.dumps(RUN_DOCUMENT))
        instance["3"]["inputs"].update({"steps": 24, "cfg": 6.5})
        conn.execute(
            "INSERT OR REPLACE INTO workflow_recipe_instance "
            "(library_uuid, instance_hash, structural_hash, hash_version, "
            "document, first_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                server.vault.library_uuid,
                RUN_INSTANCE,
                RUN_RECIPE,
                "v1",
                json.dumps(instance),
                "2026-09-01T00:00:00Z",
            ),
        )

    def write(session):
        picture = Picture(
            file_path="run_one.png",
            deleted=False,
            created_at=_stamp("2026-09-02T00:00:00Z"),
            score=5,
            pixel_sha=RUN_PIXEL_SHA,
            workflow_topology_hash=RUN_TOPOLOGY,
            workflow_structural_hash=RUN_RECIPE,
            workflow_instance_hash=RUN_INSTANCE,
            workflow_hash_version="v1",
        )
        session.add(picture)
        session.commit()
        return picture.id

    return server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


# A SECOND runnable card, for the one question a single card cannot ask: the
# run cap counts every group, and one card can never exceed it because `count`
# alone is capped by the field.
RUN_RECIPE_TWO = _h("runrecipetwo")
RUN_CARD_TWO = _h("runcardtwo")
RUN_INSTANCE_TWO = _h("runinstancetwo")


def _seed_second_runnable_card(server) -> int:
    """The same graph on a second card, so a selection makes two groups."""
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_recipe "
            "(structural_hash, topology_hash, hash_version, node_count, first_seen_at) "
            "VALUES (?, ?, 'v1', ?, ?)",
            (RUN_RECIPE_TWO, RUN_TOPOLOGY, 4, "2026-09-03T00:00:00Z"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO workflow_recipe_graph "
            "(structural_hash, document_sha256, document, created_at) "
            "VALUES (?, ?, ?, '2026-09-03T00:00:00Z')",
            (
                RUN_RECIPE_TWO,
                hashlib.sha256(json.dumps(RUN_DOCUMENT).encode()).hexdigest(),
                json.dumps(RUN_DOCUMENT),
            ),
        )
        for widget, filename in (
            ("ckpt_name", "realvisxl.safetensors"),
            ("lora_name", "add_detail.safetensors"),
        ):
            conn.execute(
                "INSERT OR REPLACE INTO workflow_recipe_asset "
                "(structural_hash, widget_name, normalized_filename) VALUES (?, ?, ?)",
                (RUN_RECIPE_TWO, widget, filename),
            )
        conn.execute(
            "INSERT OR REPLACE INTO workflow_variant "
            "(structural_hash, topology_hash, workflow_key, key_version) "
            "VALUES (?, ?, ?, ?)",
            (RUN_RECIPE_TWO, RUN_TOPOLOGY, RUN_CARD_TWO, WORKFLOW_KEY_VERSION),
        )
        # Hand-written cards name no base model: the empty family set.
        conn.execute(
            "INSERT OR IGNORE INTO workflow_variant_family (structural_hash, families) "
            "SELECT structural_hash, '' FROM workflow_variant"
        )
        instance = json.loads(json.dumps(RUN_DOCUMENT))
        instance["3"]["inputs"].update({"steps": 24, "cfg": 6.5})
        conn.execute(
            "INSERT OR REPLACE INTO workflow_recipe_instance "
            "(library_uuid, instance_hash, structural_hash, hash_version, "
            "document, first_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                server.vault.library_uuid,
                RUN_INSTANCE_TWO,
                RUN_RECIPE_TWO,
                "v1",
                json.dumps(instance),
                "2026-09-03T00:00:00Z",
            ),
        )

    def write(session):
        picture = Picture(
            file_path="run_two.png",
            deleted=False,
            created_at=_stamp("2026-09-04T00:00:00Z"),
            score=5,
            workflow_topology_hash=RUN_TOPOLOGY,
            workflow_structural_hash=RUN_RECIPE_TWO,
            workflow_instance_hash=RUN_INSTANCE_TWO,
            workflow_hash_version="v1",
        )
        session.add(picture)
        session.commit()
        return picture.id

    return server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


@pytest.fixture
def runnable(workflow_env, monkeypatch):
    """RUN_CARD seeded, ComfyUI answering, and nothing actually submitted.

    ``_read_object_info`` is patched rather than the HTTP layer because it is
    the one seam both routes read ComfyUI through, and every reason code below
    is "this same install, minus one thing".
    """
    picture_id = _seed_runnable_card(workflow_env.server)
    submitted: list[dict] = []

    def fake_submit(base_url, workflow_instance, client_id=None):
        submitted.append(
            {"graph": workflow_instance, "client_id": client_id, "url": base_url}
        )
        return {"prompt_id": f"prompt-{len(submitted)}"}

    monkeypatch.setattr(workflows_routes, "_submit_comfyui_prompt", fake_submit)
    # The import worker polls a ComfyUI that is not there; it is a daemon
    # thread and its failure is not this suite's subject, so it never starts.
    monkeypatch.setattr(
        workflows_routes, "_process_comfyui_outputs", lambda *a, **k: None
    )
    monkeypatch.setattr(
        workflows_routes,
        "_read_object_info",
        lambda url: (json.loads(json.dumps(RUN_OBJECT_INFO)), None),
    )
    return SimpleNamespace(
        env=workflow_env,
        owner=workflow_env.owner,
        server=workflow_env.server,
        picture_id=picture_id,
        submitted=submitted,
        monkeypatch=monkeypatch,
    )


def _reasons(payload) -> set[str]:
    """Every reason code a plan came back with, across all its groups."""
    return {
        reason["code"] for group in payload["groups"] for reason in group["reasons"]
    }


def _preflight(client, **body) -> dict:
    r = client.post(f"{API}/workflows/run/preflight", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# --- the sources -----------------------------------------------------------


def test_the_linked_imported_file_is_the_first_source_tried(runnable, monkeypatch):
    """Tier 1 wins over a picture and over a stored instance.

    It is the only tier that is the workflow AS AUTHORED — the other two are
    reconstructions — so a card holding one must run that and nothing else.
    """
    from_file = json.loads(json.dumps(RUN_DOCUMENT))
    from_file["3"]["inputs"].update({"steps": 11, "cfg": 1.5, "seed": 5})
    from_file["1"]["inputs"]["ckpt_name"] = "realvisxl.safetensors"
    from_file["2"]["inputs"]["lora_name"] = "add_detail.safetensors"
    monkeypatch.setattr(
        workflows_routes,
        "_resolve_workflow_path",
        lambda name: ("/authored.json", "user"),
    )
    monkeypatch.setattr(workflows_routes, "_load_workflow_json", lambda path: from_file)
    with runnable.server.hub.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_file "
            "(workflow_name, workflow_key, topology_hash, structural_hash) "
            "VALUES (?, ?, ?, ?)",
            ("authored.json", RUN_CARD, RUN_TOPOLOGY, RUN_RECIPE),
        )
    # Run by its picture, which applies no default recipe over the graph, so
    # the values that arrive are the source's own.
    r = runnable.owner.post(
        f"{API}/workflows/run", json={"picture_ids": [runnable.picture_id]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["groups"][0]["source"] == "file", r.json()
    # The file's own values, not the instance document's 24/6.5.
    assert runnable.submitted[0]["graph"]["3"]["inputs"]["steps"] == 11


def test_a_manual_workflow_runs_its_own_document_and_nothing_else(runnable):
    """A manual workflow is its document: no file, picture or instance tier.

    It is on the grid as a workflow of its own, badged manual, and its default
    recipe is its own graph addressed by its own slot labels, never `core:`.
    """
    document = json.loads(json.dumps(RUN_DOCUMENT))
    document["3"]["inputs"].update({"steps": 13, "cfg": 1.5, "seed": 5})
    document["1"]["inputs"]["ckpt_name"] = "realvisxl.safetensors"
    document["2"]["inputs"]["lora_name"] = "add_detail.safetensors"
    # Imported from an earlier manual run's output, so it inherits that tag.
    document["4"]["inputs"]["filename_prefix"] = f"out__wf_{'c' * 32}"
    manual = create_manual_workflow(runnable.server.hub, "Mine", document, "import")

    card = _by_key(_cards(runnable.owner))[manual]
    assert (card["manual"], card["from_name"], card["imported"]) == (True, None, True)
    assert (card["name"], card["picture_count"], card["topologies"]) == (
        "Mine",
        0,
        [],
    )
    values = _detail(runnable.owner, manual)["card"]["default_recipe"]["values"]
    assert values and not any(v["slot_label"].startswith("core:") for v in values)

    importing: list[dict] = []
    runnable.monkeypatch.setattr(
        workflows_routes,
        "_process_comfyui_outputs",
        lambda *args, **kwargs: importing.append(kwargs),
    )
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": manual})
    assert r.status_code == 200, r.text
    (group,) = r.json()["groups"]
    assert (group["source"], group["workflow_id"]) == ("file", manual), group
    assert runnable.submitted[0]["graph"]["3"]["inputs"]["steps"] == 13
    # What it makes is filed on it; a run of an automatic workflow files none.
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WF})
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 5
    while len(importing) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)  # the import runs on a thread of its own
    assert [kwargs["run_workflow_id"] for kwargs in importing] == [manual, None]
    # Its save node's name carries it too, so a watch-folder import that beats
    # the poll still files it (#1688); ComfyUI appends its counter after it.
    manual_run, auto_run = (
        s["graph"]["4"]["inputs"]["filename_prefix"] for s in runnable.submitted[-2:]
    )
    # The inherited tag is replaced, never kept ahead of this run's.
    assert manual_run == f"out__wf_{manual.removeprefix('manual:')}"
    assert parse_workflow_tag_from_filename(f"/out/{manual_run}_00001_.png") == manual
    assert parse_workflow_tag_from_filename(f"{manual_run}__stack_3__src_9.png") == (
        manual
    )
    assert parse_workflow_tag_from_filename(f"{auto_run}_00001_.png") is None


def test_a_manual_workflow_opens_and_exports_the_document_run_submits(runnable):
    """One graph per workflow (#1682): for a manual one, its stored document."""
    document = json.loads(json.dumps(RUN_DOCUMENT))
    document["3"]["inputs"].update({"steps": 13, "cfg": 1.5})
    document["1"]["inputs"]["ckpt_name"] = "realvisxl.safetensors"
    document["2"]["inputs"]["lora_name"] = "add_detail.safetensors"
    manual = create_manual_workflow(runnable.server.hub, "Mine", document, "import")
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": manual})
    assert r.status_code == 200, r.text
    ran = runnable.submitted[0]["graph"]
    r = runnable.owner.get(f"{API}/workflows/{manual}/graph")
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "file"
    opened = r.json()["workflow"]
    assert opened["3"]["inputs"]["steps"] == 13
    # The run's own adjustments: its seed, and the save node's tag (#1688).
    assert ran["4"]["inputs"]["filename_prefix"].startswith("wf_")
    for graph in (ran, opened):
        graph["3"]["inputs"].pop("seed")
        graph["4"]["inputs"].pop("filename_prefix")
    assert opened == ran
    r = runnable.owner.get(f"{API}/workflows/{manual}/export")
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "file"
    assert r.json()["workflow"]["3"]["inputs"]["steps"] == 13


def _saved_on_run_card(runnable, **fields) -> int:
    body = {"workflow_id": RUN_WF, "name": "A look to keep", **fields}
    r = runnable.owner.post(f"{API}/recipes", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _set_recipe_workflow(runnable, recipe_id, workflow_id, workflow_key=None):
    def write(session):
        row = session.get(SavedRecipe, recipe_id)
        row.workflow_id = workflow_id
        if workflow_key is not None:
            row.workflow_key = workflow_key
        session.add(row)
        session.commit()

    runnable.server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


def test_extracting_a_recipe_stores_its_run_graph_with_comfyui_down(
    runnable, monkeypatch
):
    """The run planner's own graph, recipe applied, seeds left unfilled, and
    ComfyUI never asked: a manual workflow made from the recipe."""

    def no_comfyui(url):
        raise AssertionError("extraction asked ComfyUI")

    monkeypatch.setattr(workflows_routes, "_read_object_info", no_comfyui)
    steps = f"{topology_node_labels(RUN_DOCUMENT)['3']}/steps"
    recipe_id = _saved_on_run_card(runnable, overrides={steps: 17})

    r = runnable.owner.post(f"{API}/recipes/{recipe_id}/extract-workflow")
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"workflow_id", "name"}
    assert body["name"] == "A look to keep"
    manual = body["workflow_id"]
    assert manual.startswith("manual:")
    document = manual_document(runnable.server.hub, manual)
    assert document["3"]["inputs"]["steps"] == 17
    # Built from a stored run, which keeps no seed (its placeholder is 0):
    # the seed pass belongs to a run, and a run of this workflow draws one.
    assert document["3"]["inputs"]["seed"] == 0
    row = runnable.server.hub.fetchone(
        "SELECT origin, from_workflow_id, from_name FROM workflow_document "
        "WHERE workflow_id = ?",
        (manual,),
    )
    assert tuple(row) == ("recipe", RUN_WF, "A look to keep")
    card = _by_key(_cards(runnable.owner))[manual]
    assert (card["manual"], card["from_name"]) == (True, "A look to keep")
    assert runnable.submitted == []

    assert (
        runnable.owner.post(f"{API}/recipes/999999/extract-workflow").status_code == 404
    )


def test_a_recipe_on_a_gone_workflow_is_unfiled_and_still_extracts(runnable):
    """Its workflow gone, a recipe runs - and extracts - on the card it was
    saved from; with that gone too there is nothing to build on (409)."""
    kept = _saved_on_run_card(runnable, name="On a gone workflow")
    stranded = _saved_on_run_card(runnable, name="On nothing at all")
    filed = _saved_on_run_card(runnable, name="Still filed")
    _set_recipe_workflow(runnable, kept, AUTO_STACK_PREFIX + _h("gone"))
    _set_recipe_workflow(
        runnable,
        stranded,
        "manual:" + "d" * 32,
        workflow_key="manual:" + "d" * 32,
    )

    r = runnable.owner.get(f"{API}/recipes", params={"unfiled": "true"})
    assert r.status_code == 200, r.text
    unfiled = {row["id"]: row for row in r.json()}
    assert set(unfiled) == {kept, stranded}
    assert filed not in unfiled
    assert {row["pictures"] for row in unfiled.values()} == {0}
    # The listing says which rows the Extract below can succeed on (#1687).
    assert unfiled[kept]["extractable"] is True
    assert unfiled[stranded]["extractable"] is False
    r = runnable.owner.get(f"{API}/recipes", params={"workflow_id": RUN_WF})
    assert {row["extractable"] for row in r.json()} == {None}
    both = runnable.owner.get(
        f"{API}/recipes", params={"unfiled": "true", "workflow_id": RUN_WF}
    )
    assert both.status_code == 400, both.text

    r = runnable.owner.post(f"{API}/recipes/{kept}/extract-workflow")
    assert r.status_code == 201, r.text
    assert manual_document(runnable.server.hub, r.json()["workflow_id"])["3"]
    r = runnable.owner.post(f"{API}/recipes/{stranded}/extract-workflow")
    assert r.status_code == 409, r.text
    assert "no_runnable_source" in r.json()["detail"]


def test_a_kept_pictures_embedded_graph_is_the_second_source(runnable, monkeypatch):
    """Tier 2: a real run of the card, with real filenames, off the best picture."""
    embedded = json.loads(json.dumps(RUN_DOCUMENT))
    embedded["3"]["inputs"].update({"steps": 33, "cfg": 3.5, "seed": 7})
    embedded["1"]["inputs"]["ckpt_name"] = "realvisxl.safetensors"
    embedded["2"]["inputs"]["lora_name"] = "add_detail.safetensors"
    read: list[int] = []

    def fake_embedded(server, picture_id, object_info=None):
        read.append(picture_id)
        return embedded, []

    monkeypatch.setattr(workflows_routes, "_load_embedded_api_prompt", fake_embedded)
    r = runnable.owner.post(
        f"{API}/workflows/run", json={"picture_ids": [runnable.picture_id]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["groups"][0]["source"] == "picture", r.json()
    # It names WHICH picture answered, and that is the card's best.
    assert r.json()["groups"][0]["source_picture_id"] == read[0]
    assert runnable.submitted[0]["graph"]["3"]["inputs"]["steps"] == 33


def test_count_decides_the_pictures_not_the_graphs_batch_size(runnable, monkeypatch):
    """Every latent a submission creates is a batch of one, however it is set.

    Wrong if a submitted latent still says 4 or still reads the primitive: a
    count of 2 would then come back as eight pictures. ComfyUI's answer
    decides which nodes create latents, so a Wan image-to-video node counts and
    ``RebatchLatents``, whose ``batch_size`` is a chunk size, does not.
    """
    embedded = json.loads(json.dumps(RUN_DOCUMENT))
    embedded["1"]["inputs"]["ckpt_name"] = "realvisxl.safetensors"
    embedded["2"]["inputs"]["lora_name"] = "add_detail.safetensors"
    embedded["5"] = {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 512, "height": 512, "batch_size": 4},
    }
    embedded["6"] = {"class_type": "PrimitiveInt", "inputs": {"value": 4}}
    embedded["7"] = {
        "class_type": "EmptySD3LatentImage",
        "inputs": {"width": ["6", 0], "height": 512, "batch_size": ["6", 0]},
    }
    embedded["8"] = {
        "class_type": "WanImageToVideo",
        "inputs": {"width": 512, "length": 33, "batch_size": 2.0},
    }
    embedded["9"] = {
        "class_type": "RebatchLatents",
        "inputs": {"latents": ["5", 0], "batch_size": 8},
    }
    object_info = json.loads(json.dumps(RUN_OBJECT_INFO))
    outputs = {
        "EmptyLatentImage": ["LATENT"],
        "EmptySD3LatentImage": ["LATENT"],
        "WanImageToVideo": ["CONDITIONING", "CONDITIONING", "LATENT"],
        "RebatchLatents": ["LATENT"],
        "PrimitiveInt": ["INT"],
    }
    for class_type, output in outputs.items():
        object_info[class_type] = {"input": {"required": {}}, "output": output}
    monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (object_info, None)
    )
    monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, picture_id, object_info=None: (embedded, []),
    )
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={"picture_ids": [runnable.picture_id], "count": 2},
    )
    assert r.status_code == 200, r.text
    assert len(runnable.submitted) == 2
    for sent in runnable.submitted:
        graph = sent["graph"]
        assert graph["5"]["inputs"]["batch_size"] == 1
        assert graph["7"]["inputs"]["batch_size"] == 1
        # Only the batch link is cut: the primitive still sets the width.
        assert graph["7"]["inputs"]["width"] == ["6", 0]
        assert graph["8"]["inputs"]["batch_size"] == 1
        assert graph["9"]["inputs"]["batch_size"] == 8


def test_a_picture_whose_file_has_gone_falls_through_instead_of_erroring(
    runnable, monkeypatch
):
    """The resolver is walking candidates; an unreadable one is not an error.

    Wrong if the request 404s: the best picture of a card being off the disk
    is exactly when the next tier is wanted.
    """

    def gone(server, picture_id, object_info=None):
        raise HTTPException(status_code=404, detail="Picture file missing")

    monkeypatch.setattr(workflows_routes, "_load_embedded_api_prompt", gone)
    payload = _preflight(runnable.owner, workflow_id=RUN_WF)
    assert payload["groups"][0]["source"] == "instance", payload
    assert payload["groups"][0]["reasons"] == [], payload


@pytest.mark.parametrize("source", ["workflow", "picture", "saved_recipe"])
def test_a_card_runs_from_any_of_its_three_sources(runnable, source):
    """The same card, named three ways, resolves to the same runnable graph.

    Three ways of NAMING one card, which is not the same axis as the three
    source tiers — those are covered by the three tests above. All three land
    on tier 3 here because this fixture's card has no file and its picture has
    none on disk.
    """
    body = {"workflow_id": RUN_WF}
    if source == "picture":
        body = {"picture_ids": [runnable.picture_id]}
    elif source == "saved_recipe":
        r = runnable.owner.post(
            f"{API}/recipes",
            json={"name": "cold light", "workflow_id": RUN_WF, "prompt": "a cat"},
        )
        assert r.status_code in {200, 201}, r.text
        body = {"saved_recipe_id": r.json()["id"]}

    payload = _preflight(runnable.owner, **body)
    assert payload["groups"], payload
    assert payload["groups"][0]["workflow_id"] == RUN_WF
    assert payload["groups"][0]["reasons"] == [], payload
    assert payload["ok"] is True
    # Tier 3: the card has no imported file and its picture has no file on
    # disk, so the stored instance document is what answered.
    assert payload["groups"][0]["source"] == "instance"


def test_exactly_one_source_may_be_named(runnable):
    for body in (
        {},
        {"workflow_id": RUN_WF, "picture_ids": [runnable.picture_id]},
        {"workflow_id": RUN_WF, "saved_recipe_id": 1},
        # A card key is no longer a source (#1623): named alone it names none.
        {"workflow_key": RUN_CARD},
    ):
        r = runnable.owner.post(f"{API}/workflows/run/preflight", json=body)
        assert r.status_code == 400, f"{body}: {r.status_code} {r.text}"


def test_several_pictures_with_no_target_group_by_their_recipe(runnable):
    """One selection spanning two cards is two groups, not one run of the first."""
    busy = runnable.server.vault.db.run_immediate_read_task(
        lambda session: [
            p.id
            for p in session.exec(
                select(Picture).where(Picture.workflow_structural_hash == BUSY_RECIPE_A)
            ).all()
            if not p.deleted
        ]
    )
    payload = _preflight(runnable.owner, picture_ids=[runnable.picture_id, *busy[:2]])
    keys = {group["workflow_id"] for group in payload["groups"]}
    assert keys == {RUN_WF, BUSY_WF}, payload
    # Each group carries the pictures that chose it and nobody else's.
    by_key = {group["workflow_id"]: group for group in payload["groups"]}
    assert by_key[RUN_WF]["picture_ids"] == [runnable.picture_id]
    assert sorted(by_key[BUSY_WF]["picture_ids"]) == sorted(busy[:2])


def test_a_picture_selected_twice_is_run_once(runnable):
    """The de-duplication `_int_list` did for the retired `run_i2i` (#1410).

    A selection can hand the same id in twice, and grouping whatever arrives
    put it in the group twice - never a second submission, because `count`
    governs those, but a group REPORTING it covers a picture twice is a wrong
    answer to "what would this run". Asserted on the pre-flight and the run
    together, since `RunRequest` is one body for both.
    """
    twice = [runnable.picture_id, runnable.picture_id]
    payload = _preflight(runnable.owner, picture_ids=twice)
    (group,) = payload["groups"]
    assert group["picture_ids"] == [runnable.picture_id], payload
    assert group["runs"] == 1, payload

    r = runnable.owner.post(f"{API}/workflows/run", json={"picture_ids": twice})
    assert r.status_code == 200, r.text
    assert r.json()["runs"] == 1, r.json()
    assert len(runnable.submitted) == 1, "the repeat submitted a second run"


def test_a_target_runs_that_workflow_instead_of_the_ones_selected(runnable):
    """`target` names the workflow that runs over the pictures selected."""
    payload = _preflight(
        runnable.owner, picture_ids=[runnable.picture_id], target=BUSY_WF
    )
    assert [group["workflow_id"] for group in payload["groups"]] == [BUSY_WF]


# --- every reason code -----------------------------------------------------


def test_a_workflow_source_reports_no_save_node(runnable):
    """BUSY's stored graph has nothing that writes an image."""
    payload = _preflight(runnable.owner, workflow_id=BUSY_WF)
    assert "no_save_node" in _reasons(payload), payload


def test_a_forgotten_model_name_surfaces_as_a_missing_model(runnable):
    """FORGOTTEN's references have no asset row, so nothing names its models.

    This is the tier-3 resolution rule showing through: the stored document
    still says a model went there and the hub can no longer say which, so the
    run reports a missing model rather than pretending the slot is empty.
    """
    payload = _preflight(runnable.owner, workflow_id=FORGOTTEN_WF)
    assert "missing_models" in _reasons(payload), payload
    missing = [
        model
        for group in payload["groups"]
        for reason in group["reasons"]
        if reason["code"] == "missing_models"
        for model in reason["models"]
    ]
    assert any(model["file"] == FORGOTTEN_MODEL for model in missing), missing
    # The folder is named, because it is where the owner has to put the file.
    assert {"loras", "checkpoints"} <= {model["folder"] for model in missing}, missing


def test_a_workflow_this_hub_does_not_hold_is_a_404_not_a_reason(runnable):
    """A request naming nothing is a request error, not a group that refuses."""
    r = runnable.owner.post(
        f"{API}/workflows/run/preflight",
        json={"workflow_id": AUTO_STACK_PREFIX + _h("nosuchcore")},
    )
    assert r.status_code == 404, r.text
    r = runnable.owner.post(
        f"{API}/workflows/run/preflight",
        json={
            "picture_ids": [runnable.picture_id],
            "target": AUTO_STACK_PREFIX + _h("nosuchcore"),
        },
    )
    assert r.status_code == 404, r.text


def test_a_picture_on_no_card_reports_a1111_or_nothing_to_run(runnable, monkeypatch):
    """The two honest "nothing to run" states are told apart, not merged."""
    unscanned = runnable.server.vault.db.run_immediate_read_task(
        lambda session: session.exec(
            select(Picture).where(Picture.workflow_structural_hash.is_(None))
        ).first()
    )
    assert unscanned is not None, "the fixture's unscanned picture is gone"

    payload = _preflight(runnable.owner, picture_ids=[unscanned.id])
    assert _reasons(payload) == {"no_runnable_source"}, payload

    # The same picture, with A1111 infotext behind it.
    monkeypatch.setattr(
        workflows_routes, "_read_embedded_metadata", lambda server, pid: {}
    )
    monkeypatch.setattr(workflows_routes, "reduce_a1111", lambda metadata: object())
    payload = _preflight(runnable.owner, picture_ids=[unscanned.id])
    assert _reasons(payload) == {"a1111"}, payload


# The editor serialisation of RUN_DOCUMENT's loader, sampler and writer, with
# the widget values positional the way ComfyUI's editor writes them. Convertible
# against RUN_OBJECT_INFO, which is what makes it a source.
RUN_EDITOR_GRAPH = {
    "last_node_id": 4,
    "last_link_id": 0,
    "links": [],
    "nodes": [
        {
            "id": 1,
            "type": "CheckpointLoaderSimple",
            "mode": 0,
            "inputs": [],
            "outputs": [],
            "widgets_values": ["realvisxl.safetensors"],
        },
        {
            "id": 3,
            "type": "KSampler",
            "mode": 0,
            "inputs": [],
            "outputs": [],
            "widgets_values": [123, 20, 7.0],
        },
        {
            "id": 4,
            "type": "SaveImage",
            "mode": 0,
            "inputs": [],
            "outputs": [],
            "widgets_values": ["ComfyUI"],
        },
    ],
}


def _only_an_editor_graph(monkeypatch, graph=None):
    """Every picture answers with an editor `workflow` chunk and no API one.

    Patched on `comfyui_module`, not on `workflows_routes`: the reader lives in
    `routes/comfyui.py` and `_load_embedded_api_prompt` resolves it in ITS
    namespace, so patching the name this module imported would leave the real
    file read in place and the test would pass or fail for the wrong reason.
    """
    payload = {"png": {"workflow": json.dumps(graph or RUN_EDITOR_GRAPH)}}
    monkeypatch.setattr(
        comfyui_module,
        "_read_embedded_metadata",
        lambda server, pid: json.loads(json.dumps(payload)),
    )


def test_a_picture_whose_only_graph_is_the_editors_is_still_a_source(runnable):
    """The card run rebuilds an editor graph, like the recipe replay does.

    **This is the path that outlives `POST /comfyui/run_recipe`.** The rebuild
    lives in `_load_embedded_api_prompt`, which both callers come through, so
    retiring that route cannot take the capability with it - and this test is
    in the run route's own file so the coverage does not go either.
    """
    _only_an_editor_graph(runnable.monkeypatch)
    r = runnable.owner.post(
        f"{API}/workflows/run", json={"picture_ids": [runnable.picture_id]}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["groups"][0]["source"] == "picture", body
    assert body["groups"][0]["reasons"] == [], body
    assert len(runnable.submitted) == 1, runnable.submitted
    graph = runnable.submitted[0]["graph"]
    # The rebuild, read off `/object_info` rather than guessed: the widget
    # array `[123, 20, 7.0]` lands on seed, steps and cfg in that order.
    assert set(graph) == {"1", "3", "4"}, graph
    assert graph["3"]["class_type"] == "KSampler"
    assert graph["3"]["inputs"]["steps"] == 20
    assert graph["3"]["inputs"]["cfg"] == 7.0
    assert graph["1"]["inputs"]["ckpt_name"] == "realvisxl.safetensors"
    assert graph["4"]["inputs"]["filename_prefix"] == "ComfyUI"


def test_an_editor_graph_that_will_not_rebuild_is_not_a_source(runnable):
    """A refusal makes the resolver move on; it must not fail the run.

    The other half of the contract `_load_embedded_api_prompt` returns problems
    for: this caller is walking candidates, so "this one cannot be rebuilt"
    means take the next tier, while the recipe replay - which is replaying that
    one picture - answers 400 and names what stopped it.
    """
    broken = json.loads(json.dumps(RUN_EDITOR_GRAPH))
    broken["nodes"][1]["widgets_values"] = [123, 20, 7.0, "one too many"]
    _only_an_editor_graph(runnable.monkeypatch, broken)
    payload = _preflight(runnable.owner, workflow_id=RUN_WF)
    # The instance tier answers instead, and nothing raised on the way past.
    assert payload["groups"][0]["source"] != "picture", payload


def test_the_run_reads_object_info_itself_rather_than_a_cached_map(runnable):
    """A run decides what executes, so it asks ComfyUI now.

    The recipe READ may serve a map up to a minute old - that is what makes
    stepping the filmstrip affordable - and a run that reused it could submit
    a graph against node definitions that have since changed. Ported here from
    the recipe route's own file when #1410 retired `POST /comfyui/run_recipe`;
    the property belongs to whichever route runs things.
    """
    asked = []

    def spy(url, **kwargs):
        asked.append(kwargs)
        return json.loads(json.dumps(RUN_OBJECT_INFO)), None

    runnable.monkeypatch.setattr(workflows_routes, "_read_object_info", spy)
    _only_an_editor_graph(runnable.monkeypatch)
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WF})
    assert r.status_code == 200, r.text
    assert asked, "the run must read /object_info"
    assert all(not call.get("cached") for call in asked), asked


def test_a_missing_node_pack_is_reported_by_name(runnable):
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info.pop("LoraLoader")
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    payload = _preflight(runnable.owner, workflow_id=RUN_WF)
    nodes = [
        node
        for group in payload["groups"]
        for reason in group["reasons"]
        if reason["code"] == "missing_nodes"
        for node in reason["nodes"]
    ]
    assert nodes == ["LoraLoader"], payload


def test_a_model_this_comfyui_does_not_have_names_the_file_and_the_folder(runnable):
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = [
        ["something_else.safetensors"],
        {},
    ]
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    payload = _preflight(runnable.owner, workflow_id=RUN_WF)
    models = [
        model
        for group in payload["groups"]
        for reason in group["reasons"]
        if reason["code"] == "missing_models"
        for model in reason["models"]
    ]
    assert models == [{"file": "realvisxl.safetensors", "folder": "checkpoints"}], (
        payload
    )


@pytest.fixture
def merged_checkpoint(runnable):
    """The shelf after a duplicate merge: one model, one name gone, one kept.

    ``realvisxl.safetensors`` is the copy that was removed - its ``model_file``
    row survives at ``state = 'removed'``, which is the whole point - and the
    keeper is the copy that is still on the disk. One ``model`` row, because the
    hub is content-addressed: same bytes, one row, two paths.

    A fixture rather than a helper because the rows it writes outlive the test in
    this module's shared server, and `_seed_hub` deletes that `model` row by
    filename - which a surviving `model_file` child turns into a foreign-key
    failure in the NEXT test's setup.
    """
    folders: list[int] = []

    def seed(*, keeper: str) -> None:
        folders.append(_seed_merged_checkpoint(runnable.server, keeper=keeper))

    try:
        yield seed
    finally:
        with runnable.server.hub.transaction() as conn:
            for folder_id in folders:
                conn.execute(
                    "DELETE FROM model_file WHERE model_folder_id = ?", (folder_id,)
                )
                conn.execute("DELETE FROM model_folder WHERE id = ?", (folder_id,))


def _seed_merged_checkpoint(server, *, keeper: str) -> int:
    """Write the merged shelf rows; returns the folder id they live under."""
    with server.hub.transaction() as conn:
        # The module's own shelf row for that filename, not a second one: two
        # models of one name is the ambiguity `model_name_aliases` refuses to
        # resolve, which would make this test pass for the wrong reason.
        model_id = int(
            conn.execute(
                "SELECT id FROM model WHERE filename = ?", (_SHELF_FILENAME,)
            ).fetchone()[0]
        )
        conn.execute(
            "INSERT INTO model_folder (path, kind, movable) "
            "VALUES ('/models/checkpoints', 'user', 'per_item')"
        )
        folder_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state) "
            "VALUES (?, ?, 'realvisxl.safetensors', 'removed')",
            (model_id, folder_id),
        )
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state) "
            "VALUES (?, ?, ?, 'present')",
            (model_id, folder_id, keeper),
        )
    return folder_id


def test_a_run_loads_the_copy_that_is_left_and_says_it_did(runnable, merged_checkpoint):
    """The submit half of "merge duplicates, resolve at use" (#1439).

    The graph names the copy a merge removed; the same bytes are still on the
    shelf under another name, and this ComfyUI advertises THAT one. So the run
    goes ahead on the copy that is there instead of refusing with a missing
    model - and it says so, on the plan and in the submitted graph, because a
    run that quietly loaded a different file makes its own lineage a lie.
    """
    merged_checkpoint(keeper="kept.safetensors")
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    # This install lists only the copy that survived.
    info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = [
        ["kept.safetensors"],
        {},
    ]
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )

    payload = _preflight(runnable.owner, workflow_id=RUN_WF)
    assert _reasons(payload) == set(), payload
    assert payload["groups"][0]["substitutions"] == [
        {
            "node_id": "1",
            "class_type": "CheckpointLoaderSimple",
            "field": "ckpt_name",
            "was": "realvisxl.safetensors",
            "now": "kept.safetensors",
        }
    ], payload

    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WF})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success", r.json()
    assert len(runnable.submitted) == 1, "the run was refused over a copy that is there"
    assert (
        runnable.submitted[0]["graph"]["1"]["inputs"]["ckpt_name"] == "kept.safetensors"
    )
    assert r.json()["groups"][0]["substitutions"][0]["now"] == "kept.safetensors"


def test_a_run_loads_the_model_the_owner_replaced_the_missing_one_with(runnable):
    """The run half of a fixed workflow: the replacement is loaded, and said."""
    with runnable.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, filename, sha256, provenance) "
            "VALUES ('checkpoint', ?, ?, 'scanned')",
            (_REPLACEMENT_FILENAME, _h("bf16-digest")),
        )
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = [
        [f"sdxl/{_REPLACEMENT_FILENAME}"],
        {},
    ]
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    assert "missing_models" in _reasons(_preflight(runnable.owner, workflow_id=RUN_WF))

    r = runnable.owner.put(
        f"{API}/workflows/{RUN_WF}/model-fix",
        json={"was": _SHELF_FILENAME, "now": _REPLACEMENT_FILENAME},
    )
    assert r.status_code == 200, r.text
    # The fixture's card key is hand-written, so the re-key moves the base
    # card to the real one; the workflow keeps its id.
    assert r.json()["card"]["id"] == RUN_WF

    payload = _preflight(runnable.owner, workflow_id=RUN_WF)
    assert _reasons(payload) == set(), payload
    (swap,) = payload["groups"][0]["substitutions"]
    assert (swap["was"], swap["now"]) == (
        _SHELF_FILENAME,
        f"sdxl/{_REPLACEMENT_FILENAME}",
    )
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WF})
    assert r.json()["status"] == "success", r.json()
    assert runnable.submitted[0]["graph"]["1"]["inputs"]["ckpt_name"] == (
        f"sdxl/{_REPLACEMENT_FILENAME}"
    )


def test_a_vae_fix_never_rewrites_a_checkpoint_of_the_same_name(runnable):
    """#1596: a fix is its slot kind's; the run rewrite asks each field's kind."""
    label = next(
        slot.label for slot in slots(RUN_DOCUMENT) if slot.widget == "ckpt_name"
    )
    with runnable.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_model_fix (topology_hash, slot_label, was_norm, "
            "now_norm, was_name, now_name, slot_kind) VALUES (?, ?, ?, ?, ?, ?, 'vae')",
            (
                RUN_TOPOLOGY,
                label,
                _SHELF_FILENAME,
                "test-vae.safetensors",
                _SHELF_FILENAME,
                "test-vae.safetensors",
            ),
        )
    # ComfyUI lists the VAE in the checkpoint field too, so only the kind
    # filter can keep the rewrite out of it.
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = [
        [_SHELF_FILENAME, "test-vae.safetensors"],
        {},
    ]
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    try:
        payload = _preflight(runnable.owner, workflow_id=RUN_WF)
        assert payload["groups"][0]["substitutions"] == [], payload
    finally:
        with runnable.server.hub.transaction() as conn:
            conn.execute("DELETE FROM workflow_model_fix")


def _shelve_with_copy(conn, kind, filename, digest, state="present"):
    """A shelf model with one copy in *state*: a PixlStash loader fetches it."""
    conn.execute(
        "INSERT INTO model (file_kind, filename, sha256, provenance) "
        "VALUES (?, ?, ?, 'scanned')",
        (kind, filename, digest),
    )
    model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT OR IGNORE INTO model_folder (path, kind, movable) "
        "VALUES ('/models/test-swap', 'user', 'per_item')"
    )
    folder_id = conn.execute(
        "SELECT id FROM model_folder WHERE path = '/models/test-swap'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO model_file (model_id, model_folder_id, relpath, state) "
        "VALUES (?, ?, ?, ?)",
        (model_id, folder_id, filename, state),
    )


def _unshelve(conn, digests):
    conn.executemany(
        "DELETE FROM model_file WHERE model_id IN "
        "(SELECT id FROM model WHERE sha256 = ?)",
        [(digest,) for digest in digests],
    )
    conn.executemany(
        "DELETE FROM model WHERE sha256 = ?", [(digest,) for digest in digests]
    )
    conn.execute(
        "DELETE FROM model_folder WHERE path = '/models/test-swap' "
        "AND id NOT IN (SELECT model_folder_id FROM model_file)"
    )


def test_a_fix_the_loader_cannot_load_runs_through_a_pixlstash_loader(
    runnable, monkeypatch
):
    """#1605: ComfyUI does not list the replacement, so our node loads it.

    The rename cannot work (the VAE is on the shelf, not in ComfyUI's folders),
    so the run swaps the loader node for ``PixlStashVAELoader`` with the
    replacement's digest, keeps every link, and records the swap so the
    pictures card as this workflow's. Without the pack the fix stays missed.
    """
    missing, replacement = "test-vae-fp8.safetensors", "test-vae-swap-bf16.safetensors"
    digest = _h("vae-bf16-digest")
    embedded = json.loads(json.dumps(RUN_DOCUMENT))
    embedded["1"]["inputs"]["ckpt_name"] = "realvisxl.safetensors"
    embedded["2"]["inputs"]["lora_name"] = "add_detail.safetensors"
    embedded["3"]["inputs"].update({"steps": 20, "cfg": 7.0, "seed": 1})
    embedded["8"] = {"class_type": "VAELoader", "inputs": {"vae_name": missing}}
    embedded["6"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["3", 0], "vae": ["8", 0]},
    }
    embedded["4"]["inputs"]["images"] = ["6", 0]
    monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, picture_id, object_info=None: (
            json.loads(json.dumps(embedded)),
            [],
        ),
    )
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["VAELoader"] = {"input": {"required": {"vae_name": [[missing + "x"], {}]}}}
    info["VAEDecode"] = {"input": {"required": {}}}
    with runnable.server.hub.transaction() as conn:
        _shelve_with_copy(conn, "vae", replacement, digest)
        conn.execute(
            "INSERT INTO workflow_model_fix (topology_hash, slot_label, was_norm, "
            "now_norm, was_name, now_name, slot_kind) VALUES (?, ?, ?, ?, ?, ?, 'vae')",
            (RUN_TOPOLOGY, "l", missing, replacement, missing, replacement),
        )
    try:
        runnable.monkeypatch.setattr(
            workflows_routes, "_read_object_info", lambda url: (info, None)
        )
        payload = _preflight(runnable.owner, workflow_id=RUN_WF)
        assert payload["groups"][0]["substitutions"] == [], payload
        assert "missing_models" in _reasons(payload), payload

        info["PixlStashVAELoader"] = {
            "input": {"required": {"vae_sha256": ["STRING", {}]}}
        }
        payload = _preflight(runnable.owner, workflow_id=RUN_WF)
        assert _reasons(payload) == set(), payload
        assert payload["groups"][0]["substitutions"] == [
            {
                "node_id": "8",
                "class_type": "PixlStashVAELoader",
                "field": "vae_sha256",
                "was": missing,
                "now": replacement,
                "verified": True,
            }
        ], payload
        # A preflight submits nothing, so it records nothing.
        assert not runnable.server.hub.fetchall("SELECT 1 FROM workflow_loader_swap")
        r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WF})
        assert r.json()["status"] == "success", r.json()
        submitted = runnable.submitted[0]["graph"]
        assert submitted["8"] == {
            "class_type": "PixlStashVAELoader",
            "inputs": {"vae_sha256": digest},
        }
        assert submitted["6"]["inputs"]["vae"] == ["8", 0]
        (swap,) = runnable.server.hub.fetchall(
            "SELECT swapped_topology_hash, topology_hash, class_type, swap_class "
            "FROM workflow_loader_swap"
        )
        assert tuple(swap) == (
            topology_hash(submitted),
            topology_hash(embedded),
            "VAELoader",
            "PixlStashVAELoader",
        )
    finally:
        with runnable.server.hub.transaction() as conn:
            conn.execute("DELETE FROM workflow_model_fix")
            conn.execute("DELETE FROM workflow_loader_swap")
            _unshelve(conn, [digest])


def test_a_loader_left_unswapped_keeps_its_file_missing(runnable, monkeypatch, caplog):
    """Two loaders name the missing VAE and only one can be swapped.

    The other still names a file ComfyUI does not have: the pre-flight says
    so, and the miss is logged rather than dropped because the file name was
    loaded elsewhere.
    """
    missing, replacement = (
        "test-vae-two-fp8.safetensors",
        "test-vae-two-bf16.safetensors",
    )
    digest = _h("vae-two-bf16")
    embedded = json.loads(json.dumps(RUN_DOCUMENT))
    embedded["1"]["inputs"]["ckpt_name"] = "realvisxl.safetensors"
    embedded["2"]["inputs"]["lora_name"] = "add_detail.safetensors"
    embedded["3"]["inputs"].update({"steps": 20, "cfg": 7.0, "seed": 1})
    embedded["8"] = {"class_type": "VAELoader", "inputs": {"vae_name": missing}}
    # A widget our loader has no place for: this one is refused.
    embedded["7"] = {
        "class_type": "VAELoader",
        "inputs": {"vae_name": missing, "device": "cpu"},
    }
    embedded["6"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["3", 0], "vae": ["8", 0]},
    }
    embedded["5"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["3", 0], "vae": ["7", 0]},
    }
    embedded["4"]["inputs"]["images"] = ["6", 0]
    monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, picture_id, object_info=None: (
            json.loads(json.dumps(embedded)),
            [],
        ),
    )
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["VAELoader"] = {
        "input": {"required": {"vae_name": [["other.safetensors"], {}]}}
    }
    info["VAEDecode"] = {"input": {"required": {}}}
    info["PixlStashVAELoader"] = {"input": {"required": {"vae_sha256": ["STRING", {}]}}}
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    with runnable.server.hub.transaction() as conn:
        _shelve_with_copy(conn, "vae", replacement, digest)
        conn.execute(
            "INSERT INTO workflow_model_fix (topology_hash, slot_label, was_norm, "
            "now_norm, was_name, now_name, slot_kind) VALUES (?, ?, ?, ?, ?, ?, 'vae')",
            (RUN_TOPOLOGY, "l", missing, replacement, missing, replacement),
        )
    try:
        with caplog.at_level(logging.WARNING, logger="pixlstash.routes.workflows"):
            payload = _preflight(runnable.owner, workflow_id=RUN_WF)
        assert "missing_models" in _reasons(payload), payload
        assert [
            (sub["node_id"], sub["class_type"])
            for sub in payload["groups"][0]["substitutions"]
        ] == [("8", "PixlStashVAELoader")], payload
        assert any(
            "was not loaded" in record.getMessage() and missing in record.getMessage()
            for record in caplog.records
        ), caplog.text
    finally:
        with runnable.server.hub.transaction() as conn:
            conn.execute("DELETE FROM workflow_model_fix")
            _unshelve(conn, [digest])


def test_a_rename_on_a_loader_swapped_afterwards_is_still_reported(
    runnable, monkeypatch
):
    """A Dual loader: one file renamed, the other swapped. Both are reported."""
    old_l, new_l = "test-l-fp8.safetensors", "test-l-bf16.safetensors"
    old_t5, new_t5 = "test-t5-fp8.safetensors", "test-t5-swap.safetensors"
    digests = [_h("dual-l"), _h("dual-t5")]
    embedded = json.loads(json.dumps(RUN_DOCUMENT))
    embedded["1"]["inputs"]["ckpt_name"] = "realvisxl.safetensors"
    embedded["2"]["inputs"]["lora_name"] = "add_detail.safetensors"
    embedded["3"]["inputs"].update({"steps": 20, "cfg": 7.0, "seed": 1})
    embedded["8"] = {
        "class_type": "DualCLIPLoader",
        "inputs": {"clip_name1": old_l, "clip_name2": old_t5, "type": "flux"},
    }
    embedded["9"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["8", 0]}}
    monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, picture_id, object_info=None: (
            json.loads(json.dumps(embedded)),
            [],
        ),
    )
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    listed = [[new_l], {}]
    info["DualCLIPLoader"] = {
        "input": {"required": {"clip_name1": listed, "clip_name2": listed}}
    }
    info["CLIPTextEncode"] = {"input": {"required": {}}}
    info["PixlStashCLIPLoader"] = {
        "input": {"required": {"clip_sha256": ["STRING", {}], "type": [["flux"], {}]}}
    }
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    with runnable.server.hub.transaction() as conn:
        _shelve_with_copy(conn, "text_encoder", new_l, digests[0])
        _shelve_with_copy(conn, "text_encoder", new_t5, digests[1])
        conn.executemany(
            "INSERT INTO workflow_model_fix (topology_hash, slot_label, was_norm, "
            "now_norm, was_name, now_name, slot_kind) "
            "VALUES (?, ?, ?, ?, ?, ?, 'text_encoder')",
            [
                (RUN_TOPOLOGY, "l1", old_l, new_l, old_l, new_l),
                (RUN_TOPOLOGY, "l2", old_t5, new_t5, old_t5, new_t5),
            ],
        )
    try:
        payload = _preflight(runnable.owner, workflow_id=RUN_WF)
        assert _reasons(payload) == set(), payload
        reported = {
            (sub["class_type"], sub["field"], sub["was"], sub["now"])
            for sub in payload["groups"][0]["substitutions"]
        }
        assert reported == {
            ("PixlStashCLIPLoader", "clip_sha256", old_l, new_l),
            ("PixlStashCLIPLoader", "clip_sha256_2", old_t5, new_t5),
        }, payload
    finally:
        with runnable.server.hub.transaction() as conn:
            conn.execute("DELETE FROM workflow_model_fix")
            _unshelve(conn, digests)


def test_a_pixlstash_loader_stands_in_only_where_it_does_what_the_original_did():
    """#1605's refusals, pure: the swapped node must be the original's equal."""
    digests = {"test-l.safetensors": "11" * 32, "test-t5.safetensors": "22" * 32}
    info = {
        "PixlStashCLIPLoader": {"input": {"required": {"type": [["flux", "sd3"], {}]}}}
    }

    def plan(node, *consumers, swaps=None):
        graph = {"9": node}
        graph.update({f"c{i}": c for i, c in enumerate(consumers)})
        return run_service.plan_pixlstash_swap(
            graph,
            "9",
            "text_encoder",
            swaps or {"test-t5-fp8.safetensors": "test-t5.safetensors"},
            info,
            digests.get,
        )

    dual = {
        "class_type": "DualCLIPLoader",
        "inputs": {
            "clip_name1": "test-l.safetensors",
            "clip_name2": "test-t5-fp8.safetensors",
            "type": "flux",
        },
    }
    made, refusal = plan(dual, {"class_type": "X", "inputs": {"clip": ["9", 0]}})
    assert refusal is None
    graph = {"9": json.loads(json.dumps(dual))}
    run_service.apply_pixlstash_swap(graph, "9", made)
    assert graph["9"] == {
        "class_type": "PixlStashCLIPLoader",
        "inputs": {
            "clip_sha256": "11" * 32,
            "clip_sha256_2": "22" * 32,
            "type": "flux",
        },
    }
    assert plan(dual, {"class_type": "X", "inputs": {"a": ["9", 1]}}) == (
        None,
        run_service.SWAP_OUTPUTS_DIFFER,
    )
    triple = json.loads(json.dumps(dual))
    triple["class_type"] = "TripleCLIPLoader"
    triple["inputs"]["clip_name3"] = "test-g.safetensors"
    assert plan(triple) == (None, run_service.SWAP_TOO_MANY_FILES)
    kept_device = json.loads(json.dumps(dual))
    kept_device["inputs"]["device"] = "default"
    assert plan(kept_device)[1] is None
    kept_device["inputs"]["device"] = "cpu"
    assert plan(kept_device) == (None, run_service.SWAP_UNSUPPORTED)
    assert plan(
        dual, swaps={"test-t5-fp8.safetensors": "test-unknown.safetensors"}
    ) == (
        None,
        run_service.SWAP_NO_SHELF_COPY,
    )
    # A loader returning something other than a CLIP is not ours to stand in.
    info["DualCLIPLoader"] = {"output": ["TEST_VIDEO_CLIP"]}
    assert plan(dual) == (None, run_service.SWAP_OUTPUTS_DIFFER)
    info["DualCLIPLoader"] = {"output": ["CLIP"]}
    assert plan(dual)[1] is None
    # A declaration that is not a list is read as none, never indexed.
    info["DualCLIPLoader"] = {"output": {"clip": "CLIP"}}
    assert plan(dual)[1] is None


def test_a_model_no_copy_of_which_is_left_is_still_a_missing_model(
    runnable, merged_checkpoint
):
    """The negative half, and what keeps the swap honest.

    Nothing on this shelf offers a name this ComfyUI advertises, so there is
    nothing to substitute and the run refuses exactly as it did before - rather
    than swapping to a file PixlStash believes in and ComfyUI does not list,
    which would only move the failure to the queue.
    """
    merged_checkpoint(keeper="also-not-there.safetensors")
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = [
        ["something-else.safetensors"],
        {},
    ]
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )

    payload = _preflight(runnable.owner, workflow_id=RUN_WF)
    assert "missing_models" in _reasons(payload), payload
    assert payload["groups"][0]["substitutions"] == []

    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WF})
    assert r.json()["status"] == "refused", r.json()
    assert runnable.submitted == []


def test_an_unreachable_comfyui_is_reported_and_a_configured_one_says_so(runnable):
    """The two ComfyUI codes are different questions with different fixes.

    Both halves are asserted. `comfyui_not_configured` is the address being
    PixlStash's own guess, so the fix is "set your ComfyUI address";
    `comfyui_unreachable` is an address the owner chose that did not answer,
    so the fix is "start ComfyUI".
    """
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "connection refused")
    )
    assert "comfyui_not_configured" in _reasons(
        _preflight(runnable.owner, workflow_id=RUN_WF)
    )

    # `.test` is RFC 2606's reserved name; nothing resolves it and the fetch is
    # patched out anyway. What matters is only that the user HAS an address.
    r = runnable.owner.patch(
        f"{API}/users/me/config", json={"comfyui_url": "http://comfyui.test:8188"}
    )
    assert r.status_code == 200, r.text
    try:
        assert "comfyui_unreachable" in _reasons(
            _preflight(runnable.owner, workflow_id=RUN_WF)
        )
        # ...and it is the ONLY one of the two: reporting both would leave a
        # panel deciding which sentence to show.
        assert "comfyui_not_configured" not in _reasons(
            _preflight(runnable.owner, workflow_id=RUN_WF)
        )
    finally:
        # The user row outlives the test; `fresh_library` re-seeds the hub and
        # the vault and would not undo this.
        runnable.owner.patch(f"{API}/users/me/config", json={"comfyui_url": ""})


def test_an_uninspectable_comfyui_runs_only_on_the_owners_acknowledgement(runnable):
    """The `allow_unchecked` consent rule, both directions.

    Without it nothing at all is known about the graph, so the request fails
    closed. With it the run goes ahead AND the reason is still reported: the
    fact stays true, and a panel that hid it would be hiding what was consented
    to.
    """
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "connection refused")
    )
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WF})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "refused", r.json()
    assert runnable.submitted == [], "an uninspected graph ran without consent"

    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={"workflow_id": RUN_WF, "allow_unchecked": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success", r.json()
    assert len(runnable.submitted) == 1, "consent did not let the run through"
    # The reason survives the consent rather than being cleared by it.
    assert r.json()["groups"][0]["reasons"][0]["code"] == "comfyui_not_configured"


def test_only_the_literal_true_is_consent(runnable):
    """R3b, carried over from the route #1410 retired: consent is JSON `true`.

    The string `"false"` is truthy in Python, and `"true"` / `1` / `"yes"` /
    `"on"` are the sibling spellings a lenient cast would also let through -
    which is what a plain Pydantic `bool` does. `RunRequest.allow_unchecked`
    is `StrictBool` so every one of them is a 422, and the run is refused
    rather than submitted on a word the owner never typed.

    The positive control is next door in
    `test_an_uninspectable_comfyui_runs_only_on_the_owners_acknowledgement`: a
    real `true` still runs, because over-blocking is its own regression.
    """
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "connection refused")
    )
    for value in ("false", "true", 1, "yes", "on", [True], {"v": True}):
        r = runnable.owner.post(
            f"{API}/workflows/run",
            json={"workflow_id": RUN_WF, "allow_unchecked": value},
        )
        assert r.status_code == 422, f"{value!r} was accepted: {r.status_code} {r.text}"
    assert runnable.submitted == [], "an uninspected graph ran on a cast consent"


def test_consent_does_not_reach_a_missing_model(runnable):
    """`allow_unchecked` answers "nothing could be checked", nothing else.

    A missing model is a fact that WAS established, so there is nothing there
    to consent to. Asserted on a MIXED batch and on the healthy card in it,
    because that is the only thing that isolates the batch rule: a one-card
    request would refuse from the per-card rule whatever the batch rule said.
    """
    forgotten = runnable.server.vault.db.run_immediate_read_task(
        lambda session: [
            p.id
            for p in session.exec(
                select(Picture).where(
                    Picture.workflow_structural_hash == FORGOTTEN_RECIPE
                )
            ).all()
            if not p.deleted
        ]
    )
    assert forgotten, "the fixture's forgotten-card pictures are gone"
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={
            "picture_ids": [runnable.picture_id, forgotten[0]],
            "allow_unchecked": True,
        },
    )
    assert r.json()["status"] == "refused", r.json()
    assert runnable.submitted == []
    # The healthy card has no reason of its own and still does not run: that is
    # the batch rule, and consent did not switch it off.
    healthy = {g["workflow_id"]: g for g in r.json()["groups"]}[RUN_WF]
    assert healthy["reasons"] == [], healthy
    assert healthy["runs"] == 0, healthy


def _pack_refusals(payload) -> dict[str, str]:
    """``{node_id: why}`` from every group's ``pixlstash_nodes`` reason."""
    return {
        node["node_id"]: node["why"]
        for group in payload["groups"]
        for reason in group["reasons"]
        if reason["code"] == "pixlstash_nodes"
        for node in reason["nodes"]
    }


def test_a_pack_checkpoint_loader_is_refused_outside_a_stored_file(runnable):
    """It names a shelf row id, which is per-hub (#1521).

    The card here runs from its stored INSTANCE, which a picture of any hub
    may have written; the same loader from a stored file is allowed in
    `test_a_pack_graph_runs_from_a_file_with_the_picture_fed_by_id`. A pack
    node with no policy entry is refused beside it, whatever the source.
    """
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["PixlStashCheckpointLoader"] = {"input": {"required": {}}}
    info["PixlStashSomethingNew"] = {"input": {"required": {}}}
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    with runnable.server.hub.transaction() as conn:
        instance = json.loads(json.dumps(RUN_DOCUMENT))
        instance["3"]["inputs"].update({"steps": 24, "cfg": 6.5})
        instance["5"] = {
            "class_type": "PixlStashCheckpointLoader",
            "inputs": {"checkpoint_id": "12"},
        }
        instance["6"] = {"class_type": "PixlStashSomethingNew", "inputs": {}}
        conn.execute(
            "UPDATE workflow_recipe_instance SET document = ? WHERE instance_hash = ?",
            (json.dumps(instance), RUN_INSTANCE),
        )
    payload = _preflight(runnable.owner, workflow_id=RUN_WF)
    assert _pack_refusals(payload) == {
        "5": "per_hub_checkpoint",
        "6": "no_policy",
    }, payload


def _pack_graph(project: str | None = None) -> dict:
    """`_i2i_graph` built on the pack: its picture loader and its saver."""
    graph = _i2i_graph()
    graph["5"] = {
        "class_type": "PixlStashPictureLoader",
        "inputs": {"picture_ids": "7,8"},
    }
    graph["4"] = {
        "class_type": "PixlStashPictureSaver",
        "inputs": {"images": ["3", 0], "filename_prefix": "out", "save_workflow": True},
    }
    graph["9"] = {
        "class_type": "PixlStashCheckpointLoader",
        "inputs": {"checkpoint_id": "12"},
    }
    if project is not None:
        graph["10"] = {
            "class_type": "PixlStashProjectLoader",
            "inputs": {"pixlstash_project": project},
        }
        graph["5"]["inputs"]["pixlstash_project"] = ["10", 0]
    return graph


@pytest.fixture
def pack(i2i):
    info = json.loads(json.dumps(I2I_OBJECT_INFO))
    for cls in (
        "PixlStashPictureLoader",
        "PixlStashCheckpointLoader",
        "PixlStashProjectLoader",
    ):
        info[cls] = {"input": {"required": {}}}
    i2i.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    return i2i


def test_a_pack_graph_runs_from_a_file_with_the_picture_fed_by_id(pack):
    """Picture loader fed now, saver swapped, checkpoint loader from a file.

    The loader is handed the selected picture's ID, never its baked `7,8`, and
    nothing is uploaded, because it fetches the picture itself. The saver is
    submitted as `SaveImage`, so this run's import is the only one.
    """
    pack.graph = _pack_graph()
    subject = _add_picture(pack.server, pack.tmp_path, "pack-subject.png")
    r = pack.owner.post(
        f"{API}/workflows/run", json={"picture_ids": [subject], "target": RUN_WF}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success", r.json()
    [submitted] = pack.submitted
    assert submitted["graph"]["5"]["inputs"]["picture_ids"] == str(subject)
    assert submitted["graph"]["4"]["class_type"] == "SaveImage"
    assert submitted["graph"]["4"]["inputs"] == {
        "images": ["3", 0],
        "filename_prefix": "out",
    }
    assert pack.uploads == []


def test_a_pack_picture_loader_nothing_feeds_does_not_run(pack):
    """No selection: its baked ids are never used, and it says so."""
    pack.graph = _pack_graph()
    payload = _preflight(pack.owner, workflow_id=RUN_WF)
    assert "picture_input_unfilled" in _reasons(payload), payload
    assert _pack_refusals(payload) == {}, payload

    # Opted out of by the file's bindings, so no input can fill it either.
    pack.graph["pixlstash_bindings"] = []
    payload = _preflight(pack.owner, workflow_id=RUN_WF)
    assert _pack_refusals(payload) == {"5": "picks_its_own_picture"}, payload


def test_a_pack_project_loader_runs_only_on_a_project_this_library_has(pack):
    subject = _add_picture(pack.server, pack.tmp_path, "pack-project.png")

    def write(session):
        project = Project(name="Pack project 1521")
        session.add(project)
        session.commit()
        return project.id

    project_id = pack.server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)
    body = {"picture_ids": [subject], "target": RUN_WF}

    pack.graph = _pack_graph(f"Pack project 1521 #{project_id}")
    payload = _preflight(pack.owner, **body)
    assert _reasons(payload) == set(), payload

    pack.graph = _pack_graph(f"Somewhere else #{project_id + 1000}")
    payload = _preflight(pack.owner, **body)
    [reason] = [
        reason
        for reason in payload["groups"][0]["reasons"]
        if reason["code"] == "pixlstash_nodes"
    ]
    assert reason["nodes"] == [
        {
            "node_id": "10",
            "class_type": "PixlStashProjectLoader",
            "title": "PixlStashProjectLoader",
            "why": "not_in_library",
            "kind": "project",
            "id": project_id + 1000,
        }
    ], reason


def test_a_lora_asked_for_where_there_is_no_loader_says_so(runnable):
    """`no_lora_loader` is the code for a graph that has no slot at all.

    Asked only when a LoRA is actually being placed: a workflow with no loader
    is perfectly runnable on its own, and reporting this for one would make
    every plain card look broken.
    """
    # The card as it stands HAS a slot, so the code must not appear.
    assert "no_lora_loader" not in _reasons(
        _preflight(runnable.owner, workflow_id=RUN_WF)
    )

    runnable.monkeypatch.setattr(
        workflows_routes, "detect_lora_targets", lambda graph: []
    )
    payload = _preflight(
        runnable.owner,
        workflow_id=RUN_WF,
        loras=[{"node_id": "2", "sha256": RUN_ADAPTER_DIGEST}],
    )
    assert "no_lora_loader" in _reasons(payload), payload


def test_a_lora_addressed_to_a_slot_the_graph_does_not_have_is_refused(runnable):
    """A graph that HAS slots, asked for one it does not, is a 400 by name.

    Different from the code above on purpose: "this workflow takes no LoRA"
    and "this workflow has no slot called that" send the caller two places.
    """
    r = runnable.owner.post(
        f"{API}/workflows/run/preflight",
        json={
            "workflow_id": RUN_WF,
            "loras": [{"node_id": "99", "sha256": RUN_ADAPTER_DIGEST}],
        },
    )
    assert r.status_code == 400, r.text
    assert "no LoRA slot" in r.json()["detail"]


def test_a_lora_is_placed_in_its_own_slot_with_its_own_strengths(runnable):
    """One slot is a node AND a field (#1377), and the strengths are this run's."""
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={
            "workflow_id": RUN_WF,
            "loras": [
                {
                    "node_id": "2",
                    "field": "lora_name",
                    "sha256": RUN_ADAPTER_DIGEST,
                    "strength_model": 0.4,
                    "strength_clip": 0.2,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    inputs = runnable.submitted[0]["graph"]["2"]["inputs"]
    assert inputs["lora_name"] == "other.safetensors", inputs
    assert inputs["strength_model"] == 0.4
    assert inputs["strength_clip"] == 0.2


# --- picture inputs (#1457) ------------------------------------------------
#
# A card whose graph loads pictures, served from the linked-file tier so each
# test can shape the graph. The pictures have real files and real content: a
# pin names its picture by `pixel_sha` and an upload reads the file, and a
# fixture without either can only exercise the branch where nothing is found.

I2I_OBJECT_INFO = {
    **RUN_OBJECT_INFO,
    "LoadImage": {
        "input": {"required": {"image": [["subject.png", "reference.png"], {}]}}
    },
}


def _i2i_graph(references: int = 0) -> dict:
    """A one-input i2i graph, plus *references* further loaders.

    Each reference is wired into its own named input, which is what gives it a
    slot label of its own: loaders the topology cannot tell apart share one.
    """
    graph = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "realvisxl.safetensors"},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "steps": 20,
                "cfg": 7.0,
                "seed": 1,
                "model": ["1", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "out", "images": ["3", 0]},
        },
        "5": {"class_type": "LoadImage", "inputs": {"image": "subject.png"}},
    }
    for index in range(references):
        node_id = str(6 + index)
        graph[node_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": "reference.png"},
        }
        graph["3"]["inputs"][f"reference_{index}"] = [node_id, 0]
    return graph


def _label_of(graph: dict, node_id: str) -> str:
    """The slot label a card addresses this picture input by."""
    [item] = [item for item in card_input_modes(graph, []) if node_id in item.node_ids]
    return item.slot_label


def _add_picture(server, tmp_path, name: str, pixel_sha: str | None = None) -> int:
    """A kept picture with a file on disk and, by default, its own content."""
    path = tmp_path / name
    path.write_bytes(b"\x89PNG not really " + name.encode())
    pixel_sha = pixel_sha or _h(f"pixels-{name}")

    def write(session):
        picture = Picture(
            file_path=str(path),
            deleted=False,
            created_at=_stamp("2026-09-05T00:00:00Z"),
            pixel_sha=pixel_sha,
        )
        session.add(picture)
        session.commit()
        return picture.id

    return server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


def _bin_picture(server, picture_id: int) -> None:
    def write(session):
        picture = session.get(Picture, picture_id)
        picture.deleted = True
        session.add(picture)
        session.commit()

    server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


@pytest.fixture
def i2i(runnable, monkeypatch, tmp_path):
    """RUN_CARD served from a file whose graph loads pictures.

    ``uploads`` records every upload with how many prompts had been submitted
    when it happened, which is what the ordering assertions read: an upload
    recorded against a refused request is the failure this feature must never
    have.
    """
    state = SimpleNamespace(
        graph=_i2i_graph(),
        uploads=[],
        outputs=[],
        tmp_path=tmp_path,
        **vars(runnable),
    )
    monkeypatch.setattr(
        workflows_routes,
        "_resolve_workflow_path",
        lambda name: ("/i2i.json", "user"),
    )
    monkeypatch.setattr(
        workflows_routes,
        "_load_workflow_json",
        lambda path: json.loads(json.dumps(state.graph)),
    )
    monkeypatch.setattr(
        workflows_routes,
        "_read_object_info",
        lambda url: (json.loads(json.dumps(I2I_OBJECT_INFO)), None),
    )

    def fake_upload(base_url, path, upload_name=None):
        state.uploads.append(
            {"path": path, "name": upload_name, "submitted": len(runnable.submitted)}
        )
        return upload_name

    monkeypatch.setattr(workflows_routes, "_upload_image_to_comfyui", fake_upload)
    monkeypatch.setattr(
        workflows_routes,
        "_process_comfyui_outputs",
        lambda *args, **kwargs: state.outputs.append(args),
    )
    with runnable.server.hub.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_file "
            "(workflow_name, workflow_key, topology_hash, structural_hash) "
            "VALUES (?, ?, ?, ?)",
            ("i2i.json", RUN_CARD, RUN_TOPOLOGY, RUN_RECIPE),
        )
    return state


def _upload_name(server, picture_id: int, content: str) -> str:
    """The name a picture is uploaded to ComfyUI under: library, id, content."""
    library = "".join(c for c in server.vault.library_uuid if c.isalnum())[:8]
    return f"pixlstash-{library}-{picture_id}-{content}.png"


def _uploaded_id(name: str) -> str:
    return name.split("-")[2]


def _set_inputs(owner, inputs: list[dict]):
    r = owner.put(f"{API}/workflows/{RUN_WF}/inputs", json={"inputs": inputs})
    assert r.status_code == 200, r.text
    return r.json()


def _inputs_of(payload) -> dict[str, dict]:
    """The first group's picture inputs by slot label."""
    return {item["slot_label"]: item for item in payload["groups"][0]["picture_inputs"]}


def test_a_card_nobody_set_up_still_lists_its_picture_inputs(i2i):
    """Enumerated from the graph, not the stored rows, with the defaults on.

    A card with no stored setup has no rows at all, so a read of the table
    alone would hand the popup nothing to draw for the commonest case.
    """
    i2i.graph = _i2i_graph(references=1)
    payload = _preflight(i2i.owner, workflow_id=RUN_WF)
    inputs = payload["groups"][0]["picture_inputs"]
    assert len(inputs) == 2, inputs
    assert {item["input_name"] for item in inputs} == {"image"}
    assert len({item["slot_label"] for item in inputs}) == 2, inputs
    # One Selection, the rest Picker - the rule `resolve_input_modes` states.
    assert sorted(item["mode"] for item in inputs) == ["picker", "selection"]
    assert all(item["title"] == "LoadImage" for item in inputs)


def test_a_pin_by_picture_id_is_stored_as_its_content(i2i):
    """The picker hands back a row with no `pixel_sha`, so the id is enough."""
    picture = _add_picture(i2i.server, i2i.tmp_path, "pinned.png")
    label = _label_of(i2i.graph, "5")
    stored = _set_inputs(
        i2i.owner,
        [
            {
                "slot_label": label,
                "input_name": "image",
                "mode": "fixed",
                "picture_id": picture,
            }
        ],
    )
    assert stored["inputs"][0]["pixel_sha"] == _h("pixels-pinned.png"), stored
    payload = _preflight(i2i.owner, workflow_id=RUN_WF)
    pinned = _inputs_of(payload)[label]
    assert pinned["picture_id"] == picture, pinned
    assert pinned["picture_missing"] is False
    assert pinned["fill"] == "fixed"
    assert payload["groups"][0]["reasons"] == [], payload


def test_a_pin_stored_by_core_address_fills_the_run_graphs_own_input(i2i):
    """The conversion writes picture inputs by CORE address (#1623).

    Every topology of a workflow shares its core labels, and the run graph
    reads slot labels, so a `core:` row has to be put on the graph's own
    label before the fill reads it - or the owner's pinned picture would read
    as an input the graph has lost.
    """
    picture = _add_picture(i2i.server, i2i.tmp_path, "pinned.png")
    core = core_node_labels(structural_document(i2i.graph), strip_loras=True)["5"]
    with i2i.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_group_picture_input "
            "(library_uuid, workflow_id, address, mode, pixel_sha) "
            "VALUES (?, ?, ?, 'fixed', ?)",
            (
                i2i.server.vault.library_uuid,
                RUN_WF,
                f"{CORE_ADDRESS_PREFIX}{core}/image",
                _h("pixels-pinned.png"),
            ),
        )
    payload = _preflight(i2i.owner, workflow_id=RUN_WF)
    pinned = _inputs_of(payload)[_label_of(i2i.graph, "5")]
    assert (pinned["mode"], pinned["fill"]) == ("fixed", "fixed"), pinned
    assert pinned["picture_id"] == picture, pinned
    assert payload["groups"][0]["reasons"] == [], payload


def test_a_pin_naming_a_picture_this_library_does_not_keep_is_refused(i2i):
    """Stored as null it would be a pin that silently names nothing."""
    binned = _add_picture(i2i.server, i2i.tmp_path, "binned.png")
    _bin_picture(i2i.server, binned)
    for picture_id in (binned, 987654):
        r = i2i.owner.put(
            f"{API}/workflows/{RUN_WF}/inputs",
            json={
                "inputs": [
                    {
                        "slot_label": _label_of(i2i.graph, "5"),
                        "input_name": "image",
                        "mode": "fixed",
                        "picture_id": picture_id,
                    }
                ]
            },
        )
        assert r.status_code == 400, r.text
    assert (
        group_picture_inputs(i2i.server.hub, i2i.server.vault.library_uuid, RUN_WF)
        == []
    )


def test_a_pin_whose_picture_has_gone_is_an_empty_slot_that_refuses(i2i):
    """Decision 7: an empty slot saying so, and `picture_input_unfilled`.

    Not `fixed_input_deleted`, which is retired: the popup now offers the fix.
    """
    i2i.graph = _i2i_graph(references=1)
    reference = _label_of(i2i.graph, "6")
    _set_inputs(
        i2i.owner,
        [
            {
                "slot_label": reference,
                "input_name": "image",
                "mode": "fixed",
                "pixel_sha": _h("a picture that left"),
            }
        ],
    )
    # The file the graph names is gone from this ComfyUI too, so nothing
    # answers the input.
    i2i.graph["6"]["inputs"]["image"] = "long-gone.png"
    subject = _add_picture(i2i.server, i2i.tmp_path, "subject-1.png")
    payload = _preflight(i2i.owner, picture_ids=[subject], target=RUN_WF)
    gone = _inputs_of(payload)[reference]
    assert gone["picture_missing"] is True, gone
    assert gone["picture_id"] is None
    assert gone["fill"] is None
    reasons = payload["groups"][0]["reasons"]
    assert [r["code"] for r in reasons] == ["picture_input_unfilled"], reasons
    assert reasons[0]["inputs"] == [
        {"slot_label": reference, "input_name": "image", "title": "LoadImage"}
    ]
    assert "fixed_input_deleted" not in _reasons(payload)


def test_a_dead_pin_is_empty_even_when_the_graphs_own_file_is_live(i2i):
    """The authored file being on ComfyUI must not stand in for a gone pin.

    Otherwise the run quietly uses the file the graph was written with, and the
    dead pin also keeps the selection from filling the other input, so neither
    input reads what the owner chose (#1501 review).
    """
    i2i.graph = _i2i_graph(references=1)
    reference = _label_of(i2i.graph, "6")
    _set_inputs(
        i2i.owner,
        [
            {
                "slot_label": reference,
                "input_name": "image",
                "mode": "fixed",
                "pixel_sha": _h("a picture that left"),
            }
        ],
    )
    subject = _add_picture(i2i.server, i2i.tmp_path, "subject-1.png")
    r = i2i.owner.post(
        f"{API}/workflows/run", json={"picture_ids": [subject], "target": RUN_WF}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "refused", r.json()
    gone = _inputs_of(r.json())[reference]
    assert gone["fill"] is None and gone["picture_missing"] is True, gone
    assert "picture_input_unfilled" in _reasons(r.json())
    assert i2i.uploads == [] and i2i.submitted == []


def test_a_duplicate_import_does_not_move_what_a_pin_resolves_to(i2i):
    """The OLDEST kept copy, so a pin feeds the same picture every run."""
    first = _add_picture(i2i.server, i2i.tmp_path, "a.png", _h("shared pixels"))
    label = _label_of(i2i.graph, "5")
    _set_inputs(
        i2i.owner,
        [
            {
                "slot_label": label,
                "input_name": "image",
                "mode": "fixed",
                "pixel_sha": _h("shared pixels"),
            }
        ],
    )
    before = _inputs_of(_preflight(i2i.owner, workflow_id=RUN_WF))[label]
    second = _add_picture(i2i.server, i2i.tmp_path, "b.png", _h("shared pixels"))
    after = _inputs_of(_preflight(i2i.owner, workflow_id=RUN_WF))[label]
    assert second > first
    assert before["picture_id"] == after["picture_id"] == first, (before, after)


def test_a_graph_that_will_not_reduce_is_a_400_only_when_an_input_is_named(
    i2i, monkeypatch
):
    def broken(graph, stored):
        raise WorkflowGraphError("no usable node")

    monkeypatch.setattr(workflows_routes, "card_input_modes", broken)
    assert (
        _preflight(i2i.owner, workflow_id=RUN_WF)["groups"][0]["picture_inputs"] == []
    )
    r = i2i.owner.post(
        f"{API}/workflows/run/preflight",
        json={
            "workflow_id": RUN_WF,
            "inputs": [{"slot_label": "x", "input_name": "image", "picture_id": 1}],
        },
    )
    assert r.status_code == 400, r.text
    assert "will not reduce" in r.json()["detail"]


def test_a_lone_picture_input_takes_the_selection_with_nothing_in_the_body(i2i):
    """The zero-click case: one input, a selection, no `inputs` at all."""
    subject = _add_picture(i2i.server, i2i.tmp_path, "cat.png")
    r = i2i.owner.post(
        f"{API}/workflows/run", json={"picture_ids": [subject], "target": RUN_WF}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success", r.json()
    [upload] = i2i.uploads
    assert upload["name"] == _upload_name(i2i.server, subject, _h("pixels-cat.png"))
    assert upload["path"] == str(i2i.tmp_path / "cat.png")
    assert i2i.submitted[0]["graph"]["5"]["inputs"]["image"] == upload["name"]
    assert _inputs_of(r.json())[_label_of(i2i.graph, "5")]["fill"] == "selection"


def test_a_pinned_reference_leaves_one_input_for_the_selection(i2i):
    """Two inputs, one pinned: one is unresolved, so the selection fills it.

    The regression the whole-card counting rule exists for. Read per input -
    "is this the only picture input?" - it refuses this, the ordinary shape of
    a reference-to-image workflow.
    """
    i2i.graph = _i2i_graph(references=1)
    reference = _add_picture(i2i.server, i2i.tmp_path, "ref.png")
    subjects = [
        _add_picture(i2i.server, i2i.tmp_path, f"subject-{n}.png") for n in range(3)
    ]
    # Stored as the popup would after a pin: the reference fixed, the subject
    # left a picker, so no stored Selection points the selection anywhere.
    _set_inputs(
        i2i.owner,
        [
            {
                "slot_label": _label_of(i2i.graph, "6"),
                "input_name": "image",
                "mode": "fixed",
                "picture_id": reference,
            },
            {
                "slot_label": _label_of(i2i.graph, "5"),
                "input_name": "image",
                "mode": "picker",
            },
        ],
    )
    r = i2i.owner.post(
        f"{API}/workflows/run", json={"picture_ids": subjects, "target": RUN_WF}
    )
    assert r.status_code == 200, r.text
    assert r.json()["runs"] == 3, r.json()
    names = {_uploaded_id(upload["name"]): upload["name"] for upload in i2i.uploads}
    assert [g["graph"]["5"]["inputs"]["image"] for g in i2i.submitted] == [
        names[str(s)] for s in subjects
    ]
    assert {g["graph"]["6"]["inputs"]["image"] for g in i2i.submitted} == {
        names[str(reference)]
    }


def test_two_open_inputs_are_not_guessed_between(i2i):
    """Neither pinned, both pickers: which one the selection meant is unknown."""
    i2i.graph = _i2i_graph(references=1)
    _set_inputs(
        i2i.owner,
        [
            {"slot_label": _label_of(i2i.graph, n), "input_name": "image", "mode": m}
            for n, m in (("5", "picker"), ("6", "picker"))
        ],
    )
    # Neither file the graph names is on this ComfyUI.
    for node_id in ("5", "6"):
        i2i.graph[node_id]["inputs"]["image"] = f"gone-{node_id}.png"
    subject = _add_picture(i2i.server, i2i.tmp_path, "s.png")
    r = i2i.owner.post(
        f"{API}/workflows/run", json={"picture_ids": [subject], "target": RUN_WF}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "refused", r.json()
    [reason] = r.json()["groups"][0]["reasons"]
    assert reason["code"] == "picture_input_unfilled"
    assert len(reason["inputs"]) == 2, reason
    assert i2i.submitted == [] and i2i.uploads == []


def test_a_dead_pin_does_not_count_as_resolving_its_input(i2i):
    """A pinned-and-gone reference leaves two open, so the rule does not fire."""
    i2i.graph = _i2i_graph(references=1)
    reference = _add_picture(i2i.server, i2i.tmp_path, "ref.png")
    _set_inputs(
        i2i.owner,
        [
            {
                "slot_label": _label_of(i2i.graph, "6"),
                "input_name": "image",
                "mode": "fixed",
                "picture_id": reference,
            },
            {
                "slot_label": _label_of(i2i.graph, "5"),
                "input_name": "image",
                "mode": "picker",
            },
        ],
    )
    _bin_picture(i2i.server, reference)
    i2i.graph["6"]["inputs"]["image"] = "gone.png"
    subject = _add_picture(i2i.server, i2i.tmp_path, "s.png")
    payload = _preflight(i2i.owner, picture_ids=[subject], target=RUN_WF)
    assert "picture_input_unfilled" in _reasons(payload), payload
    assert payload["ok"] is False


def test_the_request_beats_the_pin_and_names_the_picture_it_sends(i2i):
    pinned = _add_picture(i2i.server, i2i.tmp_path, "pinned.png")
    picked = _add_picture(i2i.server, i2i.tmp_path, "picked.png")
    label = _label_of(i2i.graph, "5")
    _set_inputs(
        i2i.owner,
        [
            {
                "slot_label": label,
                "input_name": "image",
                "mode": "fixed",
                "picture_id": pinned,
            }
        ],
    )
    r = i2i.owner.post(
        f"{API}/workflows/run",
        json={
            "workflow_id": RUN_WF,
            "inputs": [
                {"slot_label": label, "input_name": "image", "picture_id": picked}
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert [_uploaded_id(u["name"]) for u in i2i.uploads] == [str(picked)]
    assert _inputs_of(r.json())[label]["fill"] == "request"


def test_an_open_input_whose_file_is_on_this_comfyui_still_runs(i2i):
    """The positive control: a mask kept in ComfyUI's input folder is not a gap.

    `judge` never read image widgets, so this card ran before #1457. Refusing
    it now would be a regression dressed as a feature.
    """
    i2i.graph = _i2i_graph(references=1)
    subject = _label_of(i2i.graph, "5")
    reference = _label_of(i2i.graph, "6")
    _set_inputs(
        i2i.owner,
        [
            {"slot_label": subject, "input_name": "image", "mode": "selection"},
            {"slot_label": reference, "input_name": "image", "mode": "picker"},
        ],
    )
    picture = _add_picture(i2i.server, i2i.tmp_path, "s.png")
    r = i2i.owner.post(
        f"{API}/workflows/run", json={"picture_ids": [picture], "target": RUN_WF}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success", r.json()
    assert _inputs_of(r.json())[reference]["fill"] == "graph"
    assert i2i.submitted[0]["graph"]["6"]["inputs"]["image"] == "reference.png"


def test_nothing_is_uploaded_when_anything_refuses(i2i):
    """Every refusal is decided before the first upload.

    Asserted on the UPLOAD log, not the submit log: a refused request must not
    leave files in the owner's ComfyUI input folder for a run that never runs.
    """
    picture = _add_picture(i2i.server, i2i.tmp_path, "s.png")
    info = json.loads(json.dumps(I2I_OBJECT_INFO))
    del info["CheckpointLoaderSimple"]
    i2i.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    r = i2i.owner.post(
        f"{API}/workflows/run", json={"picture_ids": [picture], "target": RUN_WF}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "refused", r.json()
    assert i2i.uploads == [] and i2i.submitted == []


def test_the_pre_flight_uploads_nothing(i2i):
    picture = _add_picture(i2i.server, i2i.tmp_path, "s.png")
    payload = _preflight(i2i.owner, picture_ids=[picture], target=RUN_WF)
    assert payload["ok"] is True, payload
    assert i2i.uploads == [] and i2i.submitted == []


def test_every_upload_happens_before_the_first_submission(i2i):
    pictures = [_add_picture(i2i.server, i2i.tmp_path, f"{n}.png") for n in range(3)]
    r = i2i.owner.post(
        f"{API}/workflows/run", json={"picture_ids": pictures, "target": RUN_WF}
    )
    assert r.status_code == 200, r.text
    assert len(i2i.uploads) == 3
    assert {upload["submitted"] for upload in i2i.uploads} == {0}


def test_two_pictures_with_one_file_name_upload_under_two_names(i2i, tmp_path):
    """ComfyUI's upload overwrites by name; the id and content keep them apart."""
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    first = _add_picture(i2i.server, tmp_path / "one", "image.png", _h("first"))
    second = _add_picture(i2i.server, tmp_path / "two", "image.png", _h("second"))
    r = i2i.owner.post(
        f"{API}/workflows/run",
        json={"picture_ids": [first, second], "target": RUN_WF},
    )
    assert r.status_code == 200, r.text
    names = [upload["name"] for upload in i2i.uploads]
    assert len(set(names)) == 2, names
    assert [g["graph"]["5"]["inputs"]["image"] for g in i2i.submitted] == names


def test_a_pinned_picture_is_uploaded_once_for_a_whole_selection(i2i):
    i2i.graph = _i2i_graph(references=1)
    reference = _add_picture(i2i.server, i2i.tmp_path, "ref.png")
    _set_inputs(
        i2i.owner,
        [
            {
                "slot_label": _label_of(i2i.graph, "6"),
                "input_name": "image",
                "mode": "fixed",
                "picture_id": reference,
            }
        ],
    )
    subjects = [_add_picture(i2i.server, i2i.tmp_path, f"s{n}.png") for n in range(40)]
    r = i2i.owner.post(
        f"{API}/workflows/run", json={"picture_ids": subjects, "target": RUN_WF}
    )
    assert r.status_code == 200, r.text
    assert r.json()["runs"] == 40
    uploaded = [_uploaded_id(upload["name"]) for upload in i2i.uploads]
    assert uploaded.count(str(reference)) == 1
    assert len(uploaded) == 41


def test_a_selection_times_count_is_what_the_run_cap_counts(i2i):
    """40 pictures at count 5 is 200 runs, not 5; one more picture is refused."""
    pictures = [_add_picture(i2i.server, i2i.tmp_path, f"p{n}.png") for n in range(41)]
    payload = _preflight(i2i.owner, picture_ids=pictures[:40], target=RUN_WF, count=5)
    assert payload["runs"] == MAX_RUNS_PER_REQUEST == 200, payload["runs"]
    r = i2i.owner.post(
        f"{API}/workflows/run/preflight",
        json={"picture_ids": pictures, "target": RUN_WF, "count": 5},
    )
    assert r.status_code == 400, r.text
    assert i2i.uploads == []


def test_a_selection_run_with_stack_makes_one_stack_per_picture(i2i):
    """Each selected picture is its own source, so its outputs join its stack."""
    pictures = [_add_picture(i2i.server, i2i.tmp_path, f"{n}.png") for n in range(3)]
    r = i2i.owner.post(
        f"{API}/workflows/run",
        json={"picture_ids": pictures, "target": RUN_WF, "stack": True},
    )
    assert r.status_code == 200, r.text
    # args: server, url, prompt id, output nodes, stack id, source picture id
    stacks = [args[4] for args in i2i.outputs]
    sources = [args[5] for args in i2i.outputs]
    assert sources == pictures, sources
    assert len(set(stacks)) == 3 and None not in stacks, stacks
    # The save node carries the same answer, for an output a watched folder
    # imports before the run's own import sees it.
    assert [g["graph"]["4"]["inputs"]["filename_prefix"] for g in i2i.submitted] == [
        f"out__stack_{stack}__src_{source}" for stack, source in zip(stacks, sources)
    ]


def test_without_stack_no_output_is_stacked_with_its_source(i2i):
    picture = _add_picture(i2i.server, i2i.tmp_path, "s.png")
    r = i2i.owner.post(
        f"{API}/workflows/run", json={"picture_ids": [picture], "target": RUN_WF}
    )
    assert r.status_code == 200, r.text
    assert [(args[4], args[5]) for args in i2i.outputs] == [(None, None)]
    assert i2i.submitted[0]["graph"]["4"]["inputs"]["filename_prefix"] == "out"


def test_a_picture_input_the_card_does_not_have_is_a_400(i2i):
    picture = _add_picture(i2i.server, i2i.tmp_path, "s.png")
    r = i2i.owner.post(
        f"{API}/workflows/run/preflight",
        json={
            "workflow_id": RUN_WF,
            "inputs": [
                {"slot_label": "nope", "input_name": "image", "picture_id": picture}
            ],
        },
    )
    assert r.status_code == 400, r.text
    assert "nope/image" in r.json()["detail"]


def test_a_binned_picture_cannot_fill_an_input(i2i):
    picture = _add_picture(i2i.server, i2i.tmp_path, "s.png")
    _bin_picture(i2i.server, picture)
    r = i2i.owner.post(
        f"{API}/workflows/run",
        json={
            "workflow_id": RUN_WF,
            "inputs": [
                {
                    "slot_label": _label_of(i2i.graph, "5"),
                    "input_name": "image",
                    "picture_id": picture,
                }
            ],
        },
    )
    assert r.status_code == 404, r.text
    assert i2i.uploads == [] and i2i.submitted == []


def test_the_selection_can_be_sent_to_one_input_by_name(i2i):
    """`picture_id: null` is "my selection goes here", for two open inputs."""
    i2i.graph = _i2i_graph(references=1)
    reference = _label_of(i2i.graph, "6")
    picture = _add_picture(i2i.server, i2i.tmp_path, "s.png")
    r = i2i.owner.post(
        f"{API}/workflows/run",
        json={
            "picture_ids": [picture],
            "target": RUN_WF,
            "inputs": [
                {"slot_label": reference, "input_name": "image", "picture_id": None}
            ],
        },
    )
    assert r.status_code == 200, r.text
    graph = i2i.submitted[0]["graph"]
    assert _uploaded_id(graph["6"]["inputs"]["image"]) == str(picture)
    assert graph["5"]["inputs"]["image"] == "subject.png"
    r = i2i.owner.post(
        f"{API}/workflows/run/preflight",
        json={
            "picture_ids": [picture],
            "target": RUN_WF,
            "inputs": [
                {"slot_label": label, "input_name": "image", "picture_id": None}
                for label in (reference, _label_of(i2i.graph, "5"))
            ],
        },
    )
    assert r.status_code == 422, r.text


def test_a_picture_not_yet_hashed_uploads_under_its_file_not_its_id(i2i):
    """No `pixel_sha` yet: the name still changes when the bytes do.

    An id alone is reused after a delete, and ComfyUI's upload overwrites by
    name, so a queued run would read whichever picture was uploaded last.
    """
    picture = _add_picture(i2i.server, i2i.tmp_path, "fresh.png")

    def unhash(session):
        row = session.get(Picture, picture)
        row.pixel_sha = None
        session.add(row)
        session.commit()

    i2i.server.vault.db.run_task(unhash, priority=DBPriority.IMMEDIATE)
    stat = os.stat(i2i.tmp_path / "fresh.png")
    r = i2i.owner.post(
        f"{API}/workflows/run", json={"picture_ids": [picture], "target": RUN_WF}
    )
    assert r.status_code == 200, r.text
    [upload] = i2i.uploads
    assert upload["name"] == _upload_name(
        i2i.server, picture, f"{stat.st_mtime_ns:x}-{stat.st_size:x}"
    )


def test_each_save_node_keeps_its_own_prefix_under_the_stack_tag(i2i):
    i2i.graph["9"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "preview", "images": ["3", 0]},
    }
    picture = _add_picture(i2i.server, i2i.tmp_path, "s.png")
    r = i2i.owner.post(
        f"{API}/workflows/run",
        json={"picture_ids": [picture], "target": RUN_WF, "stack": True},
    )
    assert r.status_code == 200, r.text
    graph = i2i.submitted[0]["graph"]
    stack = i2i.outputs[0][4]
    assert graph["4"]["inputs"]["filename_prefix"] == (
        f"out__stack_{stack}__src_{picture}"
    )
    assert graph["9"]["inputs"]["filename_prefix"] == (
        f"preview__stack_{stack}__src_{picture}"
    )


@pytest.mark.parametrize("stack", [False, True])
def test_an_automatic_run_drops_a_workflow_tag_its_graph_inherited(i2i, stack):
    """A graph replayed from a manual run's output keeps that run's tagged
    prefix; a watch folder would file this run's outputs on it (#1688)."""
    i2i.graph["4"]["inputs"]["filename_prefix"] = f"out__wf_{'c' * 32}"
    picture = _add_picture(i2i.server, i2i.tmp_path, "w.png")
    r = i2i.owner.post(
        f"{API}/workflows/run",
        json={"picture_ids": [picture], "target": RUN_WF, "stack": stack},
    )
    assert r.status_code == 200, r.text
    expected = f"out__stack_{i2i.outputs[0][4]}__src_{picture}" if stack else "out"
    assert i2i.submitted[0]["graph"]["4"]["inputs"]["filename_prefix"] == expected


def test_the_selection_sent_to_an_input_of_a_run_without_one_is_a_400(i2i):
    r = i2i.owner.post(
        f"{API}/workflows/run/preflight",
        json={
            "workflow_id": RUN_WF,
            "inputs": [
                {
                    "slot_label": _label_of(i2i.graph, "5"),
                    "input_name": "image",
                    "picture_id": None,
                }
            ],
        },
    )
    assert r.status_code == 400, r.text
    assert "no selection" in r.json()["detail"]


def test_an_id_no_sqlite_row_could_hold_is_a_422_not_a_500(i2i):
    too_big = 2**70
    label = _label_of(i2i.graph, "5")
    r = i2i.owner.post(
        f"{API}/workflows/run/preflight",
        json={
            "workflow_id": RUN_WF,
            "inputs": [
                {"slot_label": label, "input_name": "image", "picture_id": too_big}
            ],
        },
    )
    assert r.status_code == 422, r.text
    r = i2i.owner.post(
        f"{API}/workflows/run/preflight",
        json={"picture_ids": [too_big], "target": RUN_WF},
    )
    assert r.status_code == 422, r.text
    r = i2i.owner.put(
        f"{API}/workflows/{RUN_WF}/inputs",
        json={
            "inputs": [
                {
                    "slot_label": label,
                    "input_name": "image",
                    "mode": "fixed",
                    "picture_id": too_big,
                }
            ]
        },
    )
    assert r.status_code == 422, r.text


def test_a_file_whose_bindings_opted_out_of_its_picture_is_not_filled(i2i):
    """`pixlstash_bindings: []` is a file the old dialog stored taking no picture.

    Detection must not opt that input back in, so the selection is not fed to
    it and nothing is uploaded: the file runs as it was authored.
    """
    i2i.graph["pixlstash_bindings"] = []
    picture = _add_picture(i2i.server, i2i.tmp_path, "s.png")
    r = i2i.owner.post(
        f"{API}/workflows/run", json={"picture_ids": [picture], "target": RUN_WF}
    )
    assert r.status_code == 200, r.text
    assert r.json()["groups"][0]["picture_inputs"] == []
    assert i2i.uploads == []
    assert i2i.submitted[0]["graph"]["5"]["inputs"]["image"] == "subject.png"
    # And one that bound its image is filled as before.
    i2i.graph["pixlstash_bindings"] = [
        {"role": "image", "path": ["5", "inputs", "image"], "recovered": True}
    ]
    r = i2i.owner.post(
        f"{API}/workflows/run", json={"picture_ids": [picture], "target": RUN_WF}
    )
    assert r.status_code == 200, r.text
    assert _uploaded_id(i2i.submitted[-1]["graph"]["5"]["inputs"]["image"]) == str(
        picture
    )


def test_a_ui_format_file_is_not_a_runnable_source(runnable, monkeypatch):
    """A card whose only source is a UI export says which format it is in."""
    monkeypatch.setattr(
        workflows_routes,
        "_resolve_workflow_path",
        lambda name: ("/nowhere.json", "user"),
    )
    monkeypatch.setattr(
        workflows_routes,
        "_load_workflow_json",
        lambda path: {"nodes": [{"id": 1, "type": "KSampler"}], "links": []},
    )
    with runnable.server.hub.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_file "
            "(workflow_name, workflow_key, topology_hash, structural_hash) "
            "VALUES (?, ?, ?, ?)",
            ("ui_only.json", HIDDEN_CARD, HIDDEN_TOPOLOGY, HIDDEN_RECIPE),
        )
    # HIDDEN has no instances in this library, so the file is its only tier.
    payload = _preflight(runnable.owner, workflow_id=HIDDEN_WF)
    assert "ui_format" in _reasons(payload), payload


# --- what a run actually does ----------------------------------------------


def test_count_produces_that_many_submissions(runnable):
    r = runnable.owner.post(
        f"{API}/workflows/run", json={"workflow_id": RUN_WF, "count": 3}
    )
    assert r.status_code == 200, r.text
    assert r.json()["runs"] == 3, r.json()
    assert len(runnable.submitted) == 3
    # Each is its own graph with its own seed, not three references to one.
    seeds = {entry["graph"]["3"]["inputs"]["seed"] for entry in runnable.submitted}
    assert len(seeds) == 3, seeds


def test_a_new_run_is_not_stacked_with_the_picture_it_came_from(runnable):
    """The default is unstacked, which is where this differs from the retired
    per-picture replay route (#1410)."""
    stacked: list = []
    runnable.monkeypatch.setattr(
        workflows_routes,
        "stack_for_picture",
        lambda vault, picture_id: stacked.append(picture_id) or 7,
    )
    r = runnable.owner.post(
        f"{API}/workflows/run", json={"picture_ids": [runnable.picture_id]}
    )
    assert r.status_code == 200, r.text
    assert stacked == [], "a run was stacked with its source without being asked"

    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={"picture_ids": [runnable.picture_id], "stack": True},
    )
    assert r.status_code == 200, r.text
    assert stacked == [runnable.picture_id], "stack: true did not reach the stack"


def test_a_missing_model_blocks_the_whole_mixed_batch(runnable):
    """One card short of a file stops the cards beside it that were fine.

    Installing a model is a trip away from the keyboard, so queueing the rest
    would leave the owner repeating the gesture to catch what was skipped.
    """
    busy = runnable.server.vault.db.run_immediate_read_task(
        lambda session: [
            p.id
            for p in session.exec(
                select(Picture).where(
                    Picture.workflow_structural_hash == FORGOTTEN_RECIPE
                )
            ).all()
            if not p.deleted
        ]
    )
    assert busy, "the fixture's forgotten-card pictures are gone"
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={"picture_ids": [runnable.picture_id, busy[0]]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "refused", r.json()
    assert runnable.submitted == [], "a run was queued behind a missing model"
    # The runnable card is reported as run-able-but-blocked rather than silently
    # dropped: its own reasons are empty and its run count is zero.
    by_key = {group["workflow_id"]: group for group in r.json()["groups"]}
    assert by_key[RUN_WF]["reasons"] == []
    assert by_key[RUN_WF]["runs"] == 0


def test_values_are_applied_at_run_time_and_never_written_back(runnable):
    """An edited default is an override on the submitted graph, nothing more."""
    stored = get_document(runnable.server.hub, RUN_RECIPE)
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={
            "workflow_id": RUN_WF,
            "values": [{"slot_label": "steps", "input_name": "steps", "value": 99}],
        },
    )
    assert r.status_code == 200, r.text
    # The stored graph is content-addressed: rewriting it would change the very
    # identity of the card being run.
    assert get_document(runnable.server.hub, RUN_RECIPE) == stored


RUN_WORKFLOW = auto_workflow_id(RUN_CORE, "")


def _core_address(node_id: str, input_name: str) -> str:
    return f"core:{core_node_labels(RUN_DOCUMENT)[node_id]}/{input_name}"


def test_a_workflow_run_applies_its_default_recipe_on_the_server(runnable):
    """The request sends no values, and the default recipe still lands (#1622).

    The owner's edit to the default recipe is the one value nothing else could
    have put there: the card's stored instance says 24 steps, and a card run
    (the control) keeps it.
    """
    with runnable.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_group_default (workflow_id, address, value) "
            "VALUES (?, ?, '31')",
            (RUN_WORKFLOW, _core_address("3", "steps")),
        )
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WORKFLOW})
    assert r.status_code == 200, r.text
    (group,) = r.json()["groups"]
    assert group["workflow_id"] == RUN_WORKFLOW
    assert "workflow_key" not in group
    submitted = runnable.submitted[0]["graph"]["3"]["inputs"]
    assert (submitted["steps"], submitted["cfg"]) == (31, 6.5)
    # The default LoRA is off the shelf (no digest) but named: its loader keeps
    # it rather than being bypassed as a slot the recipe left empty.
    assert runnable.submitted[0]["graph"]["2"]["inputs"]["lora_name"] == (
        "add_detail.safetensors"
    )
    assert group["bypassed_loras"] == [] and group["unplaced_loras"] == []
    # A request value still wins over the default recipe.
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={
            "workflow_id": RUN_WORKFLOW,
            "values": [
                {
                    "slot_label": _core_address("3", "steps").rpartition("/")[0],
                    "input_name": "steps",
                    "value": 12,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert runnable.submitted[1]["graph"]["3"]["inputs"]["steps"] == 12
    # The control: a run of a picture's own graph reads no workflow default.
    r = runnable.owner.post(
        f"{API}/workflows/run", json={"picture_ids": [runnable.picture_id]}
    )
    assert r.status_code == 200, r.text
    assert runnable.submitted[2]["graph"]["3"]["inputs"]["steps"] == 24


def test_a_request_value_by_slot_label_wins_over_a_default_by_core_address(
    runnable,
):
    """One input, two addresses: the request's still lands last."""
    with runnable.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_group_default (workflow_id, address, value) "
            "VALUES (?, ?, '31')",
            (RUN_WORKFLOW, _core_address("3", "steps")),
        )
    slot_label = topology_node_labels(RUN_DOCUMENT)["3"]
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={
            "workflow_id": RUN_WORKFLOW,
            "values": [{"slot_label": slot_label, "input_name": "steps", "value": 12}],
        },
    )
    assert r.status_code == 200, r.text
    assert runnable.submitted[0]["graph"]["3"]["inputs"]["steps"] == 12


def test_one_input_named_two_ways_takes_the_later_value(runnable):
    """A saved recipe's core-addressed override must not beat a request's slot label."""
    core = {"slot_label": _core_address("3", "steps").rpartition("/")[0]}
    slot = {"slot_label": topology_node_labels(RUN_DOCUMENT)["3"]}
    for first, second, expected in ((core, slot, 12), (slot, core, 12)):
        r = runnable.owner.post(
            f"{API}/workflows/run",
            json={
                "workflow_id": RUN_WF,
                "values": [
                    {
                        **first,
                        "input_name": "steps",
                        "value": 31,
                    },
                    {**second, "input_name": "steps", "value": expected},
                ],
            },
        )
        assert r.status_code == 200, r.text
        assert runnable.submitted[-1]["graph"]["3"]["inputs"]["steps"] == expected


def test_a_recipe_read_off_no_picture_bypasses_no_lora(runnable):
    """No sample is no evidence: the base graph's LoRA stays, unreported."""
    runnable.monkeypatch.setattr(
        workflow_card_service, "read_instance_hashes", lambda *args: []
    )
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WORKFLOW})
    assert r.status_code == 200, r.text
    (group,) = r.json()["groups"]
    assert group["bypassed_loras"] == [] and group["unplaced_loras"] == []
    assert "2" in runnable.submitted[0]["graph"]


def test_a_model_pinned_by_a_digest_the_shelf_lost_is_flagged_not_refused(runnable):
    body = {
        "workflow_id": RUN_WORKFLOW,
        "models": [{"address": _core_address("1", "ckpt_name"), "sha256": "0" * 64}],
    }
    r = runnable.owner.post(f"{API}/workflows/run", json=body)
    assert r.status_code == 200, r.text
    (group,) = r.json()["groups"]
    assert [(f["code"], f["reason"]) for f in group["flags"]] == [
        ("model_not_applied", "not_on_shelf")
    ]
    loaded = runnable.submitted[0]["graph"]["1"]["inputs"]["ckpt_name"]
    assert loaded == "realvisxl.safetensors"


def test_an_empty_core_label_names_no_loader(runnable):
    """``core:`` with no label must not match every node the core stripped."""
    (group,) = _preflight(
        runnable.owner,
        workflow_id=RUN_WORKFLOW,
        models=[{"address": "core:/lora_name", "filename": "other.safetensors"}],
    )["groups"]
    assert [(f["code"], f["reason"]) for f in group["flags"]] == [
        ("model_not_applied", "no_loader")
    ]


def test_a_model_address_no_loader_has_is_flagged(runnable):
    body = {
        "workflow_id": RUN_WORKFLOW,
        "models": [
            {"address": "core:no-such-label/ckpt_name", "filename": "x.safetensors"}
        ],
    }
    (group,) = _preflight(runnable.owner, **body)["groups"]
    assert [(f["code"], f["reason"]) for f in group["flags"]] == [
        ("model_not_applied", "no_loader")
    ]


def test_an_unknown_workflow_is_a_404_and_a_malformed_one_a_422(runnable):
    r = runnable.owner.post(
        f"{API}/workflows/run/preflight",
        json={"workflow_id": f"{AUTO_STACK_PREFIX}{_h('no-such-core')}"},
    )
    assert r.status_code == 404, r.text
    r = runnable.owner.post(
        f"{API}/workflows/run/preflight", json={"workflow_id": "auto:nope"}
    )
    assert r.status_code == 422, r.text


_OTHER_FAMILY_CHECKPOINT = "test-flux-dev.safetensors"


def test_a_pinned_checkpoint_of_another_family_is_flagged_and_still_runs(runnable):
    """Flagged, never blocked (#1620 Q3), and the pinned file is what loads."""
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0].append(
        _OTHER_FAMILY_CHECKPOINT
    )
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    hub = runnable.server.hub
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET base_model = 'SDXL 1.0' WHERE filename = ?",
            (_SHELF_FILENAME,),
        )
        conn.execute(
            "INSERT INTO model (file_kind, filename, sha256, base_model, "
            "provenance) VALUES ('checkpoint', ?, ?, 'Flux.1 D', 'scanned')",
            (_OTHER_FAMILY_CHECKPOINT, _h("other-family-digest")),
        )
    try:
        body = {
            "workflow_id": RUN_WORKFLOW,
            "models": [
                {
                    "address": _core_address("1", "ckpt_name"),
                    "filename": _OTHER_FAMILY_CHECKPOINT,
                }
            ],
        }
        payload = _preflight(runnable.owner, **body)
        (group,) = payload["groups"]
        assert group["reasons"] == [], group
        assert [flag["code"] for flag in group["flags"]] == ["family_mismatch"]
        assert group["flags"][0]["now"] == _OTHER_FAMILY_CHECKPOINT
        r = runnable.owner.post(f"{API}/workflows/run", json=body)
        assert r.status_code == 200, r.text
        loaded = runnable.submitted[0]["graph"]["1"]["inputs"]["ckpt_name"]
        assert loaded == _OTHER_FAMILY_CHECKPOINT
        # The control: the same family is no flag.
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE model SET base_model = 'SDXL 1.0' WHERE filename = ?",
                (_OTHER_FAMILY_CHECKPOINT,),
            )
        (group,) = _preflight(runnable.owner, **body)["groups"]
        assert group["flags"] == []
    finally:
        with hub.transaction() as conn:
            conn.execute(
                "DELETE FROM model WHERE filename = ?", (_OTHER_FAMILY_CHECKPOINT,)
            )


def test_the_prompt_lands_in_the_graph_and_not_in_the_stored_document(runnable):
    document = json.loads(json.dumps(RUN_DOCUMENT))
    document["5"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": None, "clip": ["1", 1]},
    }
    document["3"]["inputs"]["positive"] = ["5", 0]
    runnable.monkeypatch.setattr(
        workflows_routes,
        "detect_workflow_io",
        lambda graph: SimpleNamespace(positive_prompts=("5",), negative_prompts=()),
    )
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["CLIPTextEncode"] = {"input": {"required": {"text": ["STRING", {}]}}}
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    with runnable.server.hub.transaction() as conn:
        instance = json.loads(json.dumps(document))
        instance["3"]["inputs"].update({"steps": 24, "cfg": 6.5})
        instance["5"]["inputs"]["text"] = "the saved prompt"
        conn.execute(
            "UPDATE workflow_recipe_instance SET document = ? WHERE instance_hash = ?",
            (json.dumps(instance), RUN_INSTANCE),
        )
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={"workflow_id": RUN_WF, "prompt": "a lighthouse at dusk"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success", r.json()
    assert (
        runnable.submitted[0]["graph"]["5"]["inputs"]["text"] == "a lighthouse at dusk"
    )


def test_more_runs_than_one_request_starts_are_refused(runnable):
    """`count` alone is Pydantic's; count TIMES groups is the route's."""
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={"workflow_id": RUN_WF, "count": MAX_RUNS_PER_REQUEST + 1},
    )
    assert r.status_code == 422, r.text


def test_the_cap_counts_every_group_not_every_card(runnable):
    """Two cards at half the cap each is over it, and Pydantic cannot see that.

    The aggregate is what reaches ComfyUI's queue, so the aggregate is what is
    capped — a per-card ceiling would let a wide selection multiply straight
    through it.
    """
    second = _seed_second_runnable_card(runnable.server)
    # Both cards run, so both groups carry `count`. Half the cap each is over
    # it, and no field validator can see that.
    half = MAX_RUNS_PER_REQUEST // 2 + 1
    r = runnable.owner.post(
        f"{API}/workflows/run/preflight",
        json={"picture_ids": [runnable.picture_id, second], "count": half},
    )
    assert r.status_code == 400, r.text
    assert "more than one request starts" in r.json()["detail"]
    # The control: one under the cap between them goes through, so the refusal
    # above is the total and not the second card merely existing.
    ok = _preflight(
        runnable.owner,
        picture_ids=[runnable.picture_id, second],
        count=MAX_RUNS_PER_REQUEST // 2,
    )
    assert ok["runs"] == MAX_RUNS_PER_REQUEST, ok


def test_an_unchecked_graph_needs_the_owners_acknowledgement(runnable):
    """Fail closed when nothing could be learned about the graph."""
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "connection refused")
    )
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WF})
    # An unreachable ComfyUI blocks the batch before the acknowledgement is
    # even reached, which is the stronger of the two refusals.
    assert r.json()["status"] == "refused", r.json()
    assert runnable.submitted == []


def test_keeping_a_seed_a_stored_recipe_does_not_have_is_refused(runnable):
    """`seed_mode: "keep"` on a tier-3 source has nothing to keep.

    A stored instance document nulls its seeds by design, so keeping one would
    submit zero for every run — `count: 3` silently producing three identical
    images. Refused rather than quietly re-read as "new", which answers a
    different question than the one asked.
    """
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={"workflow_id": RUN_WF, "seed_mode": "keep", "count": 3},
    )
    assert r.status_code == 400, r.text
    assert "keeps no seed" in r.json()["detail"]
    assert runnable.submitted == []

    # A source that DOES carry a seed keeps it, which is what makes the
    # refusal above about the source rather than about the mode.
    embedded = json.loads(json.dumps(RUN_DOCUMENT))
    embedded["3"]["inputs"].update({"steps": 33, "cfg": 3.5, "seed": 4242})
    embedded["1"]["inputs"]["ckpt_name"] = "realvisxl.safetensors"
    embedded["2"]["inputs"]["lora_name"] = "add_detail.safetensors"
    runnable.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (embedded, []),
    )
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={"workflow_id": RUN_WF, "seed_mode": "keep", "count": 2},
    )
    assert r.status_code == 200, r.text
    assert [e["graph"]["3"]["inputs"]["seed"] for e in runnable.submitted] == [
        4242,
        4242,
    ]


def test_the_dry_run_and_the_run_agree_about_a_missing_seed(runnable):
    """A body the run rejects must never pre-flight as `ok`.

    The whole value of a dry run is that it answers the same question; a
    validation living inside the submission loop would let the panel say "this
    will run" about a body that then 400s.
    """
    body = {"workflow_id": RUN_WF, "seed_mode": "fixed"}
    dry = runnable.owner.post(f"{API}/workflows/run/preflight", json=body)
    wet = runnable.owner.post(f"{API}/workflows/run", json=body)
    assert dry.status_code == 400, dry.text
    assert wet.status_code == 400, wet.text
    assert dry.json()["detail"] == wet.json()["detail"]
    assert runnable.submitted == []


def test_a_lora_not_on_this_comfyui_is_a_reason_not_a_hard_error(runnable):
    """A dry run that 400s instead of reporting a reason is not a dry run.

    The adapter is on the shelf and not on this ComfyUI, which is the same
    fact as any other model the graph names and must be the same kind of
    answer.
    """
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["LoraLoader"]["input"]["required"]["lora_name"] = [
        ["add_detail.safetensors"],
        {},
    ]
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    payload = _preflight(
        runnable.owner,
        workflow_id=RUN_WF,
        loras=[{"node_id": "2", "sha256": RUN_ADAPTER_DIGEST}],
    )
    models = [
        model
        for group in payload["groups"]
        for reason in group["reasons"]
        if reason["code"] == "missing_models"
        for model in reason["models"]
    ]
    assert models == [{"file": RUN_ADAPTER_FILENAME, "folder": "loras"}], payload


def _without_lora(names=()):
    """RUN_OBJECT_INFO with the loader offering *names* and nothing else."""
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["LoraLoader"]["input"]["required"]["lora_name"] = [list(names), {}]
    return info


def test_a_missing_lora_is_bypassed_and_the_run_still_happens(runnable):
    """#1463: a LoRA is optional, so its loader leaves the chain.

    The card's graph names `add_detail.safetensors` and this ComfyUI does not
    have it. Refusing would be the answer for a checkpoint; here the run goes
    ahead with the adapter simply not applied.
    """
    runnable.monkeypatch.setattr(
        workflows_routes,
        "_read_object_info",
        lambda url: (_without_lora(["something-else.safetensors"]), None),
    )
    payload = _preflight(runnable.owner, workflow_id=RUN_WF)
    assert _reasons(payload) == set(), payload
    group = payload["groups"][0]
    assert group["runs"] == 1, payload
    # Said before the run, not after: a picture made without the LoRA the owner
    # expected, with nothing said, is worse than a refusal.
    assert [(gone["file"], gone["folder"]) for gone in group["bypassed_loras"]] == [
        ("add_detail.safetensors", "loras")
    ], payload

    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WF})
    assert r.status_code == 200, r.text
    assert r.json()["groups"][0]["bypassed_loras"], r.json()
    graph = runnable.submitted[0]["graph"]
    # The loader is gone and the sampler reads the checkpoint directly, which
    # is the half that relaxing the refusal alone would not have done: a graph
    # still naming the absent file is one ComfyUI refuses.
    assert "2" not in graph, graph
    assert graph["3"]["inputs"]["model"] == ["1", 0], graph


def test_a_missing_checkpoint_still_refuses_and_nothing_is_bypassed(runnable):
    """The distinction #1463 turns on, asserted from the other side.

    Installing a checkpoint is a trip away from the keyboard and the graph
    cannot run without one, so it blocks exactly as it did - and a run that
    quietly bypassed a checkpoint loader would produce nothing at all.
    """
    info = _without_lora(["something-else.safetensors"])
    info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = [
        ["not-this-one.safetensors"],
        {},
    ]
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    payload = _preflight(runnable.owner, workflow_id=RUN_WF)
    assert "missing_models" in _reasons(payload), payload
    models = [
        model
        for group in payload["groups"]
        for reason in group["reasons"]
        if reason["code"] == "missing_models"
        for model in reason["models"]
    ]
    # Only the checkpoint: the LoRA left the graph, so it is no longer missing
    # from it, and the owner is not sent looking for a file they do not need.
    assert models == [{"file": "realvisxl.safetensors", "folder": "checkpoints"}], (
        payload
    )
    assert payload["groups"][0]["runs"] == 0, payload
    # And nothing claims a run went ahead without the LoRA. The loader DID
    # leave the graph - it had to, before `judge` could report honestly - but
    # the graph is not being submitted, so saying so would describe a run
    # nobody made.
    assert payload["groups"][0]["bypassed_loras"] == [], payload

    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WF})
    assert r.json()["status"] == "refused", r.json()
    assert runnable.submitted == []


def test_a_lora_the_request_asked_for_is_a_refusal_and_not_a_bypass(runnable):
    """A run that is not happening must not report one that went ahead without.

    The owner asked for a LoRA this ComfyUI cannot load, so the run is refused.
    The card's own LoRA is absent too, but saying it was bypassed would describe
    a run nobody made - and the bypass exists for the graph's own optional
    adapter, never for the one the request put there.
    """
    runnable.monkeypatch.setattr(
        workflows_routes,
        "_read_object_info",
        # A real list that names neither: an EMPTY one is "not enumerable" to
        # the pre-flight, so the graph's own LoRA would not be missing at all
        # and this would pass without the rule it is measuring being there.
        lambda url: (_without_lora(["something-else.safetensors"]), None),
    )
    payload = _preflight(
        runnable.owner,
        workflow_id=RUN_WF,
        loras=[{"node_id": "2", "sha256": RUN_ADAPTER_DIGEST}],
    )
    group = payload["groups"][0]
    assert group["bypassed_loras"] == [], payload
    assert "missing_models" in _reasons(payload), payload
    assert group["runs"] == 0, payload


def test_a_missing_lora_no_longer_holds_back_the_rest_of_a_mixed_batch(runnable):
    """The reported symptom: one absent LoRA refused a whole selection.

    Both cards run the same graph, so both carry the absent LoRA; what is
    being measured is that `blocks_batch` no longer zeroes the request.
    """
    second = _seed_second_runnable_card(runnable.server)
    runnable.monkeypatch.setattr(
        workflows_routes,
        "_read_object_info",
        lambda url: (_without_lora(["something-else.safetensors"]), None),
    )
    payload = _preflight(
        runnable.owner, picture_ids=[runnable.picture_id, second], count=1
    )
    assert payload["runs"] == 2, payload
    assert all(group["bypassed_loras"] for group in payload["groups"]), payload


# --- which loaders `bypass_missing_loras` will and will not take out -------
#
# Asked of the function rather than through the route, because the three cases
# that matter are shapes a stored card does not have: a stacker, and a node
# ComfyUI describes too thinly to rewire around.

STACKER_INFO = {
    "CheckpointLoaderSimple": {
        "input": {"required": {"ckpt_name": [["base.safetensors"], {}]}},
        "output": ["MODEL", "CLIP"],
    },
    "LoraLoader": {
        "input": {
            "required": {
                "model": ["MODEL", {}],
                "lora_name": [["here.safetensors"], {}],
                "lora_name_2": [["here.safetensors"], {}],
            }
        },
        "output": ["MODEL"],
    },
    "KSampler": {"input": {"required": {}}, "output": ["LATENT"]},
}


def _stacker_graph(second="here.safetensors"):
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "base.safetensors"},
        },
        "2": {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["1", 0],
                "lora_name": "gone.safetensors",
                "lora_name_2": second,
            },
        },
        "3": {"class_type": "KSampler", "inputs": {"model": ["2", 0]}},
    }


def test_a_group_refused_for_any_other_reason_claims_no_bypass(runnable):
    """The ordinary case, and the one the batch rule does not reach.

    `missing_nodes` stops this card without stopping the batch, so nothing
    downstream clears the claim: if the bypass were reported where it is
    carried out, the popup would say this run is going ahead without the LoRA
    beside a red notice saying it cannot run at all. The loader still has to
    leave the graph first - `judge` has not been called yet, so most of the
    refusals are unknown at that point.
    """
    info = _without_lora(["something-else.safetensors"])
    del info["KSampler"]
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    payload = _preflight(runnable.owner, workflow_id=RUN_WF)
    group = payload["groups"][0]

    assert "missing_nodes" in _reasons(payload), payload
    # Not a batch blocker, so the zeroing loop never runs over this group.
    assert "missing_models" not in _reasons(payload), payload
    assert group["runs"] == 0, payload
    assert group["bypassed_loras"] == [], payload


def test_another_cards_missing_model_unclaims_this_ones_bypass(runnable):
    """The batch rule has to reach the notice as well as the count.

    The forgotten card is missing a model nothing can supply, which zeroes
    EVERY group including the one that was going to run - so the card that
    would have run without its LoRA is not running either, and must stop
    saying that it is. Measured against the same selection with that card left
    out, which is the positive control: there, it does run and does say so.
    """
    forgotten = runnable.server.vault.db.run_immediate_read_task(
        lambda session: [
            p.id
            for p in session.exec(
                select(Picture).where(
                    Picture.workflow_structural_hash == FORGOTTEN_RECIPE
                )
            ).all()
            if not p.deleted
        ]
    )
    assert forgotten, "the fixture's forgotten-card pictures are gone"
    runnable.monkeypatch.setattr(
        workflows_routes,
        "_read_object_info",
        lambda url: (_without_lora(["something-else.safetensors"]), None),
    )

    alone = _preflight(runnable.owner, picture_ids=[runnable.picture_id])
    assert alone["runs"] == 1, alone
    assert alone["groups"][0]["bypassed_loras"], alone

    mixed = _preflight(runnable.owner, picture_ids=[runnable.picture_id, forgotten[0]])
    assert mixed["runs"] == 0, mixed
    assert {group["workflow_id"] for group in mixed["groups"]} == {
        RUN_WF,
        FORGOTTEN_WF,
    }, mixed
    assert all(group["bypassed_loras"] == [] for group in mixed["groups"]), mixed


def test_a_stacker_still_holding_a_lora_that_is_here_is_not_bypassed():
    """Taking the node out would drop the adapter that IS installed.

    One missing file out of two is not worth losing the other over, so the node
    stays and the run keeps its refusal - the honest answer, and the one the
    owner can act on by installing the one file.
    """
    graph = _stacker_graph()
    assert bypass_missing_loras(graph, STACKER_INFO) == []
    assert graph["2"]["inputs"]["lora_name_2"] == "here.safetensors"
    assert graph["3"]["inputs"]["model"] == ["2", 0]


def test_a_stacker_whose_other_slot_is_empty_is_bypassed():
    """The positive control beside it: one named LoRA, and it is gone."""
    graph = _stacker_graph(second="")
    assert [gone["file"] for gone in bypass_missing_loras(graph, STACKER_INFO)] == [
        "gone.safetensors"
    ]
    assert "2" not in graph
    assert graph["3"]["inputs"]["model"] == ["1", 0]


def test_a_stacker_whose_numbered_slots_are_all_missing_is_bypassed():
    """Each numbered LoRA is pre-flighted, then the empty stack leaves once."""
    graph = _stacker_graph(second="also-gone.safetensors")

    bypassed = bypass_missing_loras(graph, STACKER_INFO)

    assert [gone["file"] for gone in bypassed] == [
        "gone.safetensors",
        "also-gone.safetensors",
    ]
    assert "2" not in graph
    assert graph["3"]["inputs"]["model"] == ["1", 0]


def test_only_loras_are_bypassed_even_where_another_loader_could_be():
    """Only `loras`. Nothing else in a graph is optional in this way.

    Measured against a `HypernetworkLoader`, which is the one other missing-file
    case shaped exactly like a LoRA loader - MODEL in, MODEL out, sitting in
    the chain - so it *could* be rewired around and the only thing declining to
    is the folder the field resolves to. A checkpoint would prove nothing here:
    a loader with no inputs cannot be bypassed whatever the rule says.
    """
    info = json.loads(json.dumps(STACKER_INFO))
    info["HypernetworkLoader"] = {
        "input": {
            "required": {
                "model": ["MODEL", {}],
                "hypernetwork_name": [["installed.pt"], {}],
            }
        },
        "output": ["MODEL"],
    }
    graph = _stacker_graph(second="")
    graph["5"] = {
        "class_type": "HypernetworkLoader",
        "inputs": {"model": ["2", 0], "hypernetwork_name": "absent.pt"},
    }
    graph["3"]["inputs"]["model"] = ["5", 0]

    bypassed = bypass_missing_loras(graph, info)

    assert [gone["file"] for gone in bypassed] == ["gone.safetensors"]
    assert "5" in graph, "a hypernetwork is not a LoRA and must keep its refusal"
    # The LoRA left and the hypernetwork took its place in the chain, which is
    # what makes the assertion above about the RULE and not about the traversal.
    assert graph["5"]["inputs"]["model"] == ["1", 0]


def test_a_stacker_whose_other_slot_is_WIRED_is_not_bypassed():
    """The guard counts a link as a filled slot, not as an empty one.

    Converting `lora_name` to an input is an ordinary ComfyUI gesture, and the
    pre-flight skips a link because it is computed at run time. Counting only
    literal strings therefore read a live second adapter as an empty slot and
    dropped it - the exact loss the guard beside it exists to prevent.
    """
    info = json.loads(json.dumps(STACKER_INFO))
    info["PrimitiveString"] = {"input": {"required": {}}, "output": ["STRING"]}
    graph = _stacker_graph(second="")
    graph["2"]["inputs"]["lora_name_2"] = ["7", 0]
    graph["7"] = {"class_type": "PrimitiveString", "inputs": {"value": "here.st"}}

    assert bypass_missing_loras(graph, info) == []
    assert "2" in graph
    assert graph["7"]["inputs"]["value"] == "here.st", "the wired LoRA is still fed"


def test_a_lora_this_hub_can_no_longer_name_is_not_bypassed():
    """`FORGOTTEN_MODEL` means the NAME is lost, not that the file is.

    `resolve_references` writes the token in so the pre-flight surfaces it.
    Bypassing would trade that for a run quietly made without an adapter the
    owner may well have installed - and then tell them to go and install a file
    called "(forgotten model)".
    """
    graph = _stacker_graph(second="")
    graph["2"]["inputs"]["lora_name"] = FORGOTTEN_MODEL

    assert bypass_missing_loras(graph, STACKER_INFO) == []
    assert "2" in graph
    assert graph["3"]["inputs"]["model"] == ["2", 0]


def test_a_loader_that_cannot_be_rewired_around_is_left_in_place():
    """An un-bypassable node keeps its refusal rather than half-leaving.

    This ComfyUI says the loader hands on CLIP, and the graph's text encoder
    reads it, but the loader takes no CLIP of its own - so there is nothing to
    put in its place and the run is still refused.
    """
    info = json.loads(json.dumps(STACKER_INFO))
    info["LoraLoader"]["output"] = ["MODEL", "CLIP"]
    info["CLIPTextEncode"] = {"input": {"required": {}}, "output": ["CONDITIONING"]}
    graph = _stacker_graph(second="")
    graph["4"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 1]}}
    assert bypass_missing_loras(graph, info) == []
    assert "2" in graph
    assert graph["4"]["inputs"]["clip"] == ["2", 1]


# --- a custom seed node this ComfyUI lacks (#1463, case B) -----------------


def _with_seed_node(seed_value=4242, field="seed"):
    """RUN_DOCUMENT with real names, its sampler's *field* fed by rgthree's Seed."""
    graph = json.loads(json.dumps(RUN_DOCUMENT))
    graph["1"]["inputs"]["ckpt_name"] = "realvisxl.safetensors"
    graph["2"]["inputs"]["lora_name"] = "add_detail.safetensors"
    graph["3"]["inputs"].update({"steps": 20, "cfg": 7.0, "seed": 1})
    graph["3"]["inputs"][field] = ["9", 0]
    graph["4"]["inputs"]["filename_prefix"] = "PixlStash"
    graph["9"] = {"class_type": "Seed (rgthree)", "inputs": {"seed": seed_value}}
    return graph


def _embed(monkeypatch, graph):
    monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, picture_id, object_info=None: (
            json.loads(json.dumps(graph)),
            [],
        ),
    )


def test_a_missing_seed_node_is_replaced_and_the_run_still_happens(runnable):
    """#1463 case B: rgthree's Seed is not here, and the run needs no pack.

    The node only hands a number to the sampler's seed, which the run's own
    seed pass writes. Said before the run, exactly as a bypassed LoRA is.
    """
    _embed(runnable.monkeypatch, _with_seed_node())
    payload = _preflight(runnable.owner, workflow_id=RUN_WF)
    assert _reasons(payload) == set(), payload
    group = payload["groups"][0]
    assert group["runs"] == 1, payload
    assert [(n["node_id"], n["class_type"]) for n in group["replaced_nodes"]] == [
        ("9", "Seed (rgthree)")
    ], payload
    assert group["replaced_nodes"][0]["consumers"] == [
        {"node_id": "3", "field": "seed"}
    ]

    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={"workflow_id": RUN_WF, "seed_mode": "fixed", "seed": 77},
    )
    assert r.status_code == 200, r.text
    assert r.json()["groups"][0]["replaced_nodes"], r.json()
    graph = runnable.submitted[0]["graph"]
    assert "9" not in graph, graph
    # The overwrite that makes the replacement safe: the run's seed, not the
    # literal it was inlined with.
    assert graph["3"]["inputs"]["seed"] == 77, graph


def test_keeping_the_seed_keeps_the_one_the_seed_node_handed_on(runnable):
    """Under `keep` nothing overwrites the literal, so it must be the real seed.

    A picture's embedded graph carries the value the node actually produced;
    inlining anything else would re-run a different picture under "keep".
    """
    _embed(runnable.monkeypatch, _with_seed_node(seed_value=4242))
    r = runnable.owner.post(
        f"{API}/workflows/run", json={"workflow_id": RUN_WF, "seed_mode": "keep"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success", r.json()
    assert runnable.submitted[0]["graph"]["3"]["inputs"]["seed"] == 4242


def test_a_seed_node_feeding_something_else_keeps_its_refusal(runnable):
    """The allow-list is not the whole test: what it feeds must be a seed.

    Wired into `steps`, which the seed pass never writes, an inlined literal
    would silently be what the node computed - the "nearly right" replacement
    that changes the picture - so the run is refused as before.
    """
    _embed(runnable.monkeypatch, _with_seed_node(field="steps"))
    payload = _preflight(runnable.owner, workflow_id=RUN_WF)
    assert "missing_nodes" in _reasons(payload), payload
    group = payload["groups"][0]
    assert group["runs"] == 0, payload
    assert group["replaced_nodes"] == [], payload


def test_a_group_refused_for_another_reason_claims_no_replacement(runnable):
    """Replaced and then refused is a run nobody made, so nothing is claimed."""
    graph = _with_seed_node()
    graph["4"]["class_type"] = "PreviewImage"
    _embed(runnable.monkeypatch, graph)
    payload = _preflight(runnable.owner, workflow_id=RUN_WF)
    assert "missing_nodes" in _reasons(payload), payload
    assert all(
        reason.get("nodes") == ["PreviewImage"]
        for reason in payload["groups"][0]["reasons"]
        if reason["code"] == "missing_nodes"
    ), payload
    assert payload["groups"][0]["replaced_nodes"] == [], payload


# Asked of the function directly: an object_info that declares its seed the
# way a real ComfyUI does, so `detect_seed_targets` is the finder in play.
SEED_INFO = {
    "KSampler": {
        "input": {
            "required": {
                "seed": ["INT", {"default": 0, "control_after_generate": True}],
                "steps": ["INT", {"default": 20}],
            }
        },
        "output": ["LATENT"],
    },
    "PrimitiveInt": {
        "input": {"required": {"value": ["INT", {}]}},
        "output": ["INT"],
    },
}


def _seed_graph(class_type="Seed (rgthree)", value=4242, field="seed"):
    """A sampler whose *field* is fed by a seed node of *class_type*."""
    sampler = {"seed": 1, "steps": 20}
    sampler[field] = ["9", 0]
    return {
        "3": {"class_type": "KSampler", "inputs": sampler},
        "9": {"class_type": class_type, "inputs": {"seed": value}},
    }


def test_a_seed_node_is_replaced_by_its_own_value():
    graph = _seed_graph()
    replaced = replace_missing_seed_nodes(graph, SEED_INFO)
    assert [n["class_type"] for n in replaced] == ["Seed (rgthree)"]
    assert "9" not in graph
    assert graph["3"]["inputs"]["seed"] == 4242


def test_a_seed_node_into_a_widget_the_seed_pass_skips_is_left_alone():
    graph = _seed_graph(field="steps")
    assert replace_missing_seed_nodes(graph, SEED_INFO) == []
    assert graph["9"]["class_type"] == "Seed (rgthree)"
    assert graph["3"]["inputs"]["steps"] == ["9", 0]


def test_an_installed_seed_node_is_not_replaced():
    info = dict(SEED_INFO, **{"Seed (rgthree)": {"input": {}, "output": ["INT"]}})
    graph = _seed_graph()
    assert replace_missing_seed_nodes(graph, info) == []
    assert "9" in graph


def test_a_node_outside_the_allow_list_is_never_replaced():
    """Anything that samples, conditions or loads has no standard equivalent."""
    graph = _seed_graph(class_type="Noise Injector (some pack)")
    assert replace_missing_seed_nodes(graph, SEED_INFO) == []
    assert "9" in graph


def test_a_placeholder_seed_keeps_its_refusal():
    """rgthree's -1 means "random" and is no seed to keep.

    Refused whatever the seed mode: accepting it for "new" only would make the
    pre-flight's answer depend on a control the popups do not re-ask on, so
    the owner could be shown "goes ahead" and then refused on Run.
    """
    graph = _seed_graph(value=-1)
    assert replace_missing_seed_nodes(graph, SEED_INFO) == []
    assert "9" in graph


def test_a_seed_node_feeding_two_samplers_keeps_its_refusal():
    """A shared seed would become two: the seed pass rolls each target alone."""
    graph = _seed_graph()
    graph["5"] = {"class_type": "KSampler", "inputs": {"seed": ["9", 0], "steps": 8}}
    assert replace_missing_seed_nodes(graph, SEED_INFO) == []
    assert graph["3"]["inputs"]["seed"] == ["9", 0]
    assert graph["5"]["inputs"]["seed"] == ["9", 0]


def test_a_later_replacement_cannot_strand_an_earlier_ones_seed():
    """Checked on the final graph, not one node at a time.

    KSampler here declares no `control_after_generate` (an older ComfyUI), so
    its seed is only found by the fallback finder - which stops being used the
    moment the second replacement gives `detect_seed_targets` a target of its
    own. Checked alone, each looks safe; together the sampler's seed would
    never be written.
    """
    info = json.loads(json.dumps(SEED_INFO))
    info["KSampler"]["input"]["required"]["seed"] = ["INT", {"default": 0}]
    info["Custom Sampler"] = {
        "input": {
            "required": {
                "seed": ["INT", {"default": 0, "control_after_generate": True}]
            }
        },
        "output": ["LATENT"],
    }
    graph = _seed_graph()
    graph["5"] = {"class_type": "Custom Sampler", "inputs": {"seed": ["10", 0]}}
    graph["10"] = {"class_type": "CR Seed", "inputs": {"seed": 11}}
    before = json.loads(json.dumps(graph))

    assert replace_missing_seed_nodes(graph, info) == []
    assert graph == before, "the graph is put back whole, not half replaced"


def test_a_seed_node_whose_own_seed_is_wired_is_left_alone():
    graph = _seed_graph()
    graph["9"]["inputs"]["seed"] = ["7", 0]
    graph["7"] = {"class_type": "PrimitiveInt", "inputs": {"value": 5}}
    assert replace_missing_seed_nodes(graph, SEED_INFO) == []
    assert "9" in graph


def _text_graph(class_type="Text Multiline", text="a platypus in a toga"):
    """Two encoders both fed by one text node of *class_type*."""
    return {
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ["103", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": ["103", 0]}},
        "103": {"class_type": class_type, "inputs": {"text": text}},
    }


def test_a_text_node_is_replaced_by_its_own_string_in_every_consumer():
    graph = _text_graph(text="# a note\na platypus\n  # another\nin a toga")
    replaced = replace_missing_text_nodes(graph, SEED_INFO)
    assert [n["replacement"] for n in replaced] == ["text"]
    assert "103" not in graph
    assert graph["6"]["inputs"]["text"] == "a platypus\nin a toga"
    assert graph["7"]["inputs"]["text"] == "a platypus\nin a toga"


def test_an_installed_text_node_is_not_replaced():
    info = dict(SEED_INFO, **{"Text Multiline": {"input": {}, "output": ["STRING"]}})
    graph = _text_graph()
    assert replace_missing_text_nodes(graph, info) == []
    assert "103" in graph


def test_a_text_node_outside_the_allow_list_is_never_replaced():
    graph = _text_graph(class_type="Prompt Styler (some pack)")
    assert replace_missing_text_nodes(graph, SEED_INFO) == []
    assert "103" in graph


def test_a_was_text_with_a_token_keeps_its_refusal():
    """`[time]` is expanded by the node; a literal would send it verbatim."""
    graph = _text_graph(text="a platypus at [time]")
    assert replace_missing_text_nodes(graph, SEED_INFO) == []
    assert graph["6"]["inputs"]["text"] == ["103", 0]


def test_a_text_node_whose_text_is_wired_keeps_its_refusal():
    graph = _text_graph(text=["5", 0])
    assert replace_missing_text_nodes(graph, SEED_INFO) == []
    assert "103" in graph


def test_a_text_node_read_on_another_output_keeps_its_refusal():
    """`CR Text`'s second output is its help text, not the prompt."""
    graph = _text_graph(class_type="CR Text")
    graph["7"]["inputs"]["text"] = ["103", 1]
    assert replace_missing_text_nodes(graph, SEED_INFO) == []
    assert "103" in graph


def test_cr_text_keeps_its_comment_lines_and_brackets():
    """Only WAS's node drops `#` lines and expands tokens."""
    graph = _text_graph(class_type="CR Text", text="# kept\na [red] fox")
    assert replace_missing_text_nodes(graph, SEED_INFO)
    assert graph["6"]["inputs"]["text"] == "# kept\na [red] fox"


def test_a_was_time_format_token_keeps_its_refusal():
    graph = _text_graph(text="made on [time(%Y-%m-%d)]")
    assert replace_missing_text_nodes(graph, SEED_INFO) == []


def test_the_run_prompt_lands_in_the_text_node_an_encoder_reads():
    """Else the repair would inline the stored prompt, not the typed one."""
    graph = _text_graph()
    del graph["7"]
    assert prompt_text_target(graph, "6", "typed") == ("103", "text")
    graph["6"]["inputs"]["text"] = "literal"
    assert prompt_text_target(graph, "6", "typed") == ("6", "text")


def test_a_textbox_with_passthrough_set_hands_the_run_prompt_to_the_encoder():
    """The node would ignore a prompt written into its `text`."""
    graph = _text_graph(class_type="Textbox")
    del graph["7"]
    assert prompt_text_target(graph, "6", "typed") == ("103", "text")
    graph["103"]["inputs"]["passthrough"] = "overrides the text"
    assert prompt_text_target(graph, "6", "typed") == ("6", "text")


def test_a_text_node_shared_by_two_encoders_hands_each_its_own_prompt():
    """Positive then negative written into one node would leave both negative."""
    graph = _text_graph()
    assert prompt_text_target(graph, "6", "typed") == ("6", "text")
    assert prompt_text_target(graph, "7", "typed") == ("7", "text")


def test_a_run_prompt_replaces_a_prompt_builder_wired_into_the_encoder():
    """A generated prompt on the wire silently won over the typed one."""
    graph = {
        "68": {"class_type": "LoRACharacterPromptBuilder", "inputs": {"seed": 1}},
        "91": {"class_type": "CLIPTextEncode", "inputs": {"text": ["68", 0]}},
    }
    assert prompt_text_target(graph, "91", "a red bicycle") == ("91", "text")
    assert prompt_text_target(graph, "91", "") == ("91", "text")
    assert prompt_text_target(graph, "91", "   ") == ("91", "text")
    del graph["91"]["inputs"]["text"]
    assert prompt_text_target(graph, "91", "a red bicycle") is None


def test_an_untouched_run_keeps_the_prompt_builder_wired():
    """The recipe read the builder's template, which is no prompt to run."""
    graph = {
        "68": {
            "class_type": "LoRACharacterPromptBuilder",
            "inputs": {"seed": 1, "template": "You are a photographer."},
        },
        "91": {"class_type": "CLIPTextEncode", "inputs": {"text": ["68", 0]}},
    }
    assert prompt_text_target(graph, "91", " You are a photographer.\n") is None
    assert prompt_text_target(graph, "91", "a red bicycle") == ("91", "text")
    # Only the string the recipe read, not any other input of the builder.
    graph["68"]["inputs"]["name"] = "clem"
    assert prompt_text_target(graph, "91", "clem") == ("91", "text")
    # The API reader takes `value` before `text`, the UI reader the reverse.
    graph["68"]["inputs"].update(text="a ui prompt", value="an api prompt")
    assert prompt_text_target(graph, "91", "a ui prompt") is None
    assert prompt_text_target(graph, "91", "an api prompt") is None


def test_a_primitive_string_multiline_is_replaced_from_its_value():
    graph = _text_graph(class_type="PrimitiveStringMultiline")
    graph["103"]["inputs"] = {"value": "# kept\na [red] fox"}
    assert replace_missing_text_nodes(graph, SEED_INFO)
    assert graph["6"]["inputs"]["text"] == "# kept\na [red] fox"


def test_a_textbox_is_replaced_unless_its_passthrough_is_set():
    graph = _text_graph(class_type="Textbox")
    graph["103"]["inputs"]["passthrough"] = ""
    assert replace_missing_text_nodes(graph, SEED_INFO)
    assert graph["6"]["inputs"]["text"] == "a platypus in a toga"

    for passthrough in ("overrides the text", ["5", 0]):
        graph = _text_graph(class_type="Textbox")
        graph["103"]["inputs"]["passthrough"] = passthrough
        assert replace_missing_text_nodes(graph, SEED_INFO) == []
        assert "103" in graph


def test_the_registry_reports_text_and_seed_replacements_together():
    graph = dict(_seed_graph(), **_text_graph())
    done = repair(graph, SEED_INFO, [Reason(MISSING_NODES)])
    assert sorted(n["node_id"] for n in done["replaced_nodes"]) == ["103", "9"]


def test_the_registry_repairs_only_what_judge_reported():
    """Keyed on reason code: a repair runs only against its own refusal."""
    graph = _seed_graph()
    assert repair(graph, SEED_INFO, [Reason(MISSING_MODELS)]) == {
        "bypassed_loras": [],
        "replaced_nodes": [],
    }
    assert "9" in graph

    done = repair(graph, SEED_INFO, [Reason(MISSING_NODES)])
    assert [n["node_id"] for n in done["replaced_nodes"]] == ["9"]
    assert "9" not in graph


def test_a_saved_recipes_own_loras_are_placed_in_the_graphs_slots(runnable):
    """ "Run this saved look" that drops the look's LoRAs is the wrong result."""
    r = runnable.owner.post(
        f"{API}/recipes",
        json={
            "name": "with its lora",
            "workflow_id": RUN_WF,
            "prompt": "a cat",
            "loras": [
                {
                    "filename": RUN_ADAPTER_FILENAME,
                    "sha256": RUN_ADAPTER_DIGEST,
                    "strength": 0.6,
                }
            ],
        },
    )
    assert r.status_code in {200, 201}, r.text
    run = runnable.owner.post(
        f"{API}/workflows/run", json={"saved_recipe_id": r.json()["id"]}
    )
    assert run.status_code == 200, run.text
    inputs = runnable.submitted[0]["graph"]["2"]["inputs"]
    assert inputs["lora_name"] == RUN_ADAPTER_FILENAME, inputs
    assert inputs["strength_model"] == 0.6, inputs


def test_a_saved_recipes_pinned_model_is_loaded(runnable):
    """``saved_recipe.models`` reaches the run as typed models (#1622)."""
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0].append(
        _OTHER_FAMILY_CHECKPOINT
    )
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    r = runnable.owner.post(
        f"{API}/recipes",
        json={"name": "pinned model", "workflow_id": RUN_WF, "prompt": "a cat"},
    )
    assert r.status_code in {200, 201}, r.text
    recipe_id = r.json()["id"]
    models = [
        {
            "address": _core_address("1", "ckpt_name"),
            "filename": _OTHER_FAMILY_CHECKPOINT,
        }
    ]

    def pin(session):
        recipe = session.get(SavedRecipe, recipe_id)
        recipe.models = json.dumps(models)
        session.commit()

    runnable.server.vault.db.run_task(pin, priority=DBPriority.IMMEDIATE)
    run = runnable.owner.post(
        f"{API}/workflows/run", json={"saved_recipe_id": recipe_id}
    )
    assert run.status_code == 200, run.text
    loaded = runnable.submitted[0]["graph"]["1"]["inputs"]["ckpt_name"]
    assert loaded == _OTHER_FAMILY_CHECKPOINT


def test_a_saved_seed_never_overrides_a_seed_mode_the_caller_sent(runnable):
    """The request wins over the row, which is what the merge promises."""
    r = runnable.owner.post(
        f"{API}/recipes",
        json={
            "name": "pinned seed",
            "workflow_id": RUN_WF,
            "prompt": "a cat",
            "seed": "4242",
            "keep_seed": True,
        },
    )
    assert r.status_code in {200, 201}, r.text
    recipe_id = r.json()["id"]

    # Left open: the recipe's kept seed applies.
    run = runnable.owner.post(
        f"{API}/workflows/run", json={"saved_recipe_id": recipe_id}
    )
    assert run.status_code == 200, run.text
    assert runnable.submitted[0]["graph"]["3"]["inputs"]["seed"] == 4242

    # Asked for explicitly: the caller's choice stands and a fresh seed is drawn.
    runnable.submitted.clear()
    run = runnable.owner.post(
        f"{API}/workflows/run",
        json={"saved_recipe_id": recipe_id, "seed_mode": "new", "count": 2},
    )
    assert run.status_code == 200, run.text
    seeds = [e["graph"]["3"]["inputs"]["seed"] for e in runnable.submitted]
    assert 4242 not in seeds, seeds
    assert len(set(seeds)) == 2, seeds


def test_a_failure_part_way_through_still_reports_what_was_queued(runnable):
    """A prompt id nobody was told about is a generation nobody can find.

    Wrong if the request raises: two runs are already in ComfyUI's queue and
    importing, and a 500 loses every id.
    """
    calls = {"n": 0}

    def flaky(base_url, workflow_instance, client_id=None):
        calls["n"] += 1
        if calls["n"] == 3:
            raise HTTPException(status_code=502, detail="ComfyUI prompt request failed")
        return {"prompt_id": f"prompt-{calls['n']}"}

    runnable.monkeypatch.setattr(workflows_routes, "_submit_comfyui_prompt", flaky)
    r = runnable.owner.post(
        f"{API}/workflows/run", json={"workflow_id": RUN_WF, "count": 5}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "partial", r.json()
    assert [p["prompt_id"] for p in r.json()["prompts"]] == ["prompt-1", "prompt-2"]
    assert r.json()["runs"] == 2


def test_the_body_takes_an_inputs_field_and_the_schema_says_so(runnable):
    """`inputs` is a real field now (#1457), in the model and in OpenAPI.

    It replaces a test asserting the opposite, written while nothing filled a
    picture input: a field accepted and ignored would have been a run that
    never read the picture it was sent.
    """
    assert "inputs" in RunRequest.model_fields
    schema = runnable.server.api.openapi()["components"]["schemas"]["RunRequest"]
    assert "inputs" in schema["properties"], schema["properties"].keys()


def test_consent_never_silently_swaps_the_lora_that_was_asked_for(runnable):
    """`allow_unchecked` consents to running uninspected, not to another LoRA.

    A filename slot is resolved against what THIS ComfyUI lists, and with no
    `object_info` there is no list — so the run cannot honour the request and
    must say so rather than quietly keep the stored graph's LoRA.
    """
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "connection refused")
    )
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={
            "workflow_id": RUN_WF,
            "allow_unchecked": True,
            "loras": [{"node_id": "2", "sha256": RUN_ADAPTER_DIGEST}],
        },
    )
    assert r.status_code == 400, r.text
    assert "which file to write into LoRA slot" in r.json()["detail"]
    assert runnable.submitted == []

    # The control: the same consent with no LoRA asked for still runs, so the
    # refusal above is about the LoRA and not about the consent.
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={"workflow_id": RUN_WF, "allow_unchecked": True},
    )
    assert r.status_code == 200 and r.json()["status"] == "success", r.text
    assert len(runnable.submitted) == 1
    # ...and the graph still names what it always named, untouched.
    assert (
        runnable.submitted[0]["graph"]["2"]["inputs"]["lora_name"]
        == "add_detail.safetensors"
    )


def test_a_saved_recipe_on_a_card_this_hub_lacks_carries_a_reason(runnable):
    """`runs: 0` with an empty `reasons` would contradict the one response rule."""

    # Written as a row: `POST /recipes` refuses a workflow this hub lacks, and
    # a recipe the vault conversion has not reached yet is still on a card.
    def write(session):
        recipe = SavedRecipe(
            name="orphan", workflow_key=_h("nosuchcard"), prompt="a cat"
        )
        session.add(recipe)
        session.commit()
        return recipe.id

    recipe_id = runnable.server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)
    payload = _preflight(runnable.owner, saved_recipe_id=recipe_id)
    assert _reasons(payload) == {"no_runnable_source"}, payload
    assert payload["ok"] is False


def test_a_stored_value_a_run_cannot_take_is_named_rather_than_assigned(runnable):
    """The merge re-validates: a row must not walk past the body's ceilings.

    `model_copy(update=…)` assigns without validating, so a seed stored above
    the 64-bit ceiling would reach ComfyUI as a number no sampler can take.
    """
    r = runnable.owner.post(
        f"{API}/recipes",
        json={
            "name": "impossible seed",
            "workflow_id": RUN_WF,
            "prompt": "a cat",
            "seed": str(2**64),
            "keep_seed": True,
        },
    )
    assert r.status_code in {200, 201}, r.text
    run = runnable.owner.post(
        f"{API}/workflows/run", json={"saved_recipe_id": r.json()["id"]}
    )
    assert run.status_code == 422, run.text
    assert "cannot take" in run.json()["detail"]
    assert runnable.submitted == []


def test_one_stack_holds_every_run_of_a_group(runnable):
    """`stack: true` with `count: 3` is one stack, asked for once."""
    stacked: list[int] = []
    runnable.monkeypatch.setattr(
        workflows_routes,
        "stack_for_picture",
        lambda vault, picture_id: stacked.append(picture_id) or 7,
    )
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={"picture_ids": [runnable.picture_id], "stack": True, "count": 3},
    )
    assert r.status_code == 200, r.text
    assert len(runnable.submitted) == 3
    # One write task, not one per run: it is idempotent, so a per-run call was
    # two wasted writes rather than a wrong answer - but it was still two.
    assert stacked == [runnable.picture_id], stacked


# ===========================================================================
# The file gestures (v1.12 B8) — export, duplicate, insert loader, delete
# ===========================================================================

# What a run of RUN_CARD looks like as a PICTURE's embedded graph: the tier
# that carries real filenames, a real prompt and a real seed, and therefore
# the tier the export exists for. `gone_one.safetensors` is the fixture's
# FORGOTTEN name — its `workflow_recipe_asset` rows were never written, so
# `model_ghost_names` cannot see it and only the shelf can say it is unknown,
# which is exactly the acceptance case (§5.7).
EXPORT_PROMPT = "a portrait of someone the owner knows"
FORGOTTEN_LORA = "gone_one.safetensors"


def _embedded_export_graph(lora: str = FORGOTTEN_LORA) -> dict:
    """RUN_DOCUMENT as a real run: prompts, a seed, a title and a picture."""
    graph = json.loads(json.dumps(RUN_DOCUMENT))
    graph["1"]["inputs"]["ckpt_name"] = _SHELF_FILENAME
    graph["2"]["inputs"].update({"lora_name": lora, "clip": ["1", 1]})
    graph["3"]["inputs"].update(
        {
            "steps": 33,
            "cfg": 3.5,
            "seed": 4242,
            "positive": ["5", 0],
            "negative": ["6", 0],
        }
    )
    graph["4"]["inputs"]["filename_prefix"] = "portraits/someone/2026-09"
    graph["5"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": EXPORT_PROMPT, "clip": ["2", 1]},
        "_meta": {"title": "the subject's name"},
    }
    graph["6"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "blurry, watermark", "clip": ["2", 1]},
    }
    graph["7"] = {"class_type": "LoadImage", "inputs": {"image": "a-private-photo.png"}}
    return graph


@pytest.fixture
def exportable(runnable):
    """RUN_CARD whose only source is a picture's embedded graph, as a real run.

    The picture tier and not the file tier on purpose: a stored file is the
    workflow as authored and carries none of this, so exporting one would pass
    with the whole scrub deleted.
    """
    graph = _embedded_export_graph()
    runnable.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (graph, []),
    )
    return SimpleNamespace(graph=graph, **vars(runnable))


def test_an_export_carries_no_prompt_no_seed_no_title_and_no_picture_name(exportable):
    """§5.7: everything that is about a RUN rather than about the workflow goes."""
    r = exportable.owner.get(f"{API}/workflows/{RUN_WF}/export")
    assert r.status_code == 200, r.text
    payload = r.json()
    graph = payload["workflow"]
    assert payload["source"] == "picture"
    assert graph["5"]["inputs"]["text"] == ""
    assert graph["6"]["inputs"]["text"] == ""
    assert graph["3"]["inputs"]["seed"] == 0
    assert "_meta" not in graph["5"]
    assert graph["7"]["inputs"]["image"] == ""
    # Where the run landed on the owner's disk: a folder a person names after
    # what is in it, so it is reset rather than carried out of the house.
    assert graph["4"]["inputs"]["filename_prefix"] == "PixlStash"
    # The workflow itself survives: the checkpoint is on the shelf and the
    # parameters are the DEFAULT RECIPE's (#1623), not this one run's.
    # Over-blanking is its own regression - an export nobody can run is not a
    # safer export.
    assert graph["1"]["inputs"]["ckpt_name"] == _SHELF_FILENAME
    assert (graph["3"]["inputs"]["steps"], graph["3"]["inputs"]["cfg"]) == (24, 6.5)
    # The run's LoRA is not the recipe's, so its loader is taken out and the
    # sampler reads the checkpoint directly.
    assert "2" not in graph
    assert graph["3"]["inputs"]["model"] == ["1", 0]
    # Whatever is in the source, no exported string may be the prompt.
    assert EXPORT_PROMPT not in json.dumps(payload)


def test_an_export_leaves_out_a_forgotten_lora_a_picture_still_names(exportable):
    """The acceptance case: the name is in the picture and not in the file."""
    payload = exportable.owner.get(f"{API}/workflows/{RUN_WF}/export").json()
    assert exportable.graph["2"]["inputs"]["lora_name"] == FORGOTTEN_LORA
    assert "2" not in payload["workflow"]
    assert FORGOTTEN_LORA not in json.dumps(payload)


def test_without_comfyui_a_lora_outside_the_recipe_is_emptied_not_kept(
    exportable, monkeypatch
):
    """No `object_info`, no bypass: the scrub is the fallback, and it empties.

    The forgotten name is then reported as a MODEL NAME, not as "a LoRA that
    is part of the look" - the shelf check runs first, so the category says
    what actually happened.
    """
    monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "refused")
    )
    payload = exportable.owner.get(f"{API}/workflows/{RUN_WF}/export").json()
    assert payload["workflow"]["2"]["inputs"]["lora_name"] == ""
    assert FORGOTTEN_LORA not in json.dumps(payload)
    assert "model names this machine does not hold" in payload["removed"]
    assert "LoRA slots that are part of the look" not in payload["removed"]


def _export_with_lora(runnable, monkeypatch, lora: str) -> dict:
    graph = _embedded_export_graph(lora=lora)
    monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (graph, []),
    )
    r = runnable.owner.get(f"{API}/workflows/{RUN_WF}/export")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_default_recipe_lora_the_shelf_does_not_hold_keeps_its_slot_not_its_name(
    runnable, monkeypatch
):
    """The default recipe keeps the slot; the shelf still decides the name.

    `add_detail` is in every run of the workflow, so it is its default recipe's
    LoRA and its loader stays - but the shelf does not hold it, so the name
    goes. Without `unvouched_model_values` this goes red and the test above
    stays green, which is why both exist.
    """
    payload = _export_with_lora(runnable, monkeypatch, "add_detail.safetensors")
    assert payload["workflow"]["2"]["inputs"]["lora_name"] == ""
    assert "add_detail" not in json.dumps(payload["workflow"])


def test_a_default_recipe_lora_on_the_shelf_travels_with_the_workflow(
    runnable, monkeypatch
):
    """The positive control, and its negative: only the recipe's LoRA travels.

    `other.safetensors` is on the shelf. While the default recipe does not
    name it, its loader is taken out as the look it is; once the owner puts
    it in the default recipe (`lora:<sha256>`), it is kept, name and all.
    """
    payload = _export_with_lora(runnable, monkeypatch, RUN_ADAPTER_FILENAME)
    assert "2" not in payload["workflow"]
    assert "LoRA slots that are part of the look" in payload["removed"]

    with runnable.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_group_default (workflow_id, address, value) "
            "VALUES (?, ?, '0.9')",
            (RUN_WF, "lora:" + RUN_ADAPTER_DIGEST),
        )
    payload = _export_with_lora(runnable, monkeypatch, RUN_ADAPTER_FILENAME)
    assert payload["workflow"]["2"]["inputs"]["lora_name"] == RUN_ADAPTER_FILENAME
    assert "LoRA slots that are part of the look" not in payload["removed"]


def test_an_export_switches_off_the_stages_its_default_recipe_runs_without(
    exportable, monkeypatch
):
    """A stage most of the workflow's pictures ran without is off in the file.

    The bypass itself is `bypass_stage`'s and tested there; this pins that the
    export asks for exactly the recipe's off stages, and nothing when there
    are none.
    """
    asked: list[list[str]] = []

    def record(graph, stages, object_info):
        asked.append(list(stages))
        return []

    monkeypatch.setattr(run_service, "skip_requested_stages", record)
    assert exportable.owner.get(f"{API}/workflows/{RUN_WF}/export").status_code == 200
    assert asked == [[]]

    real = workflow_card_service.workflow_defaults

    def with_upscale_off(hub, vault, workflow_id):
        recipe = real(hub, vault, workflow_id)
        recipe.stages = {"upscale": False, "face_detailer": True}
        return recipe

    monkeypatch.setattr(workflows_routes, "workflow_defaults", with_upscale_off)
    assert exportable.owner.get(f"{API}/workflows/{RUN_WF}/export").status_code == 200
    assert asked[-1] == ["upscale"]


def test_an_export_names_the_categories_it_removed_and_never_the_values(exportable):
    """`removed` is what a client renders; a value in it would be the leak itself."""
    payload = exportable.owner.get(f"{API}/workflows/{RUN_WF}/export").json()
    assert set(payload["removed"]) == {
        # The run's LoRA, taken out: it is not the default recipe's.
        "LoRA slots that are part of the look",
        "node titles",
        "picture file names",
        "prompts",
        "seeds",
        "where the pictures were saved",
    }, payload["removed"]


def test_an_export_refuses_a_graph_it_cannot_read_rather_than_publishing_it(
    runnable, monkeypatch
):
    """Which nodes carry prose comes out of the reduction: no reduction, no export."""
    monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        # Node-shaped enough to survive `sanitize_prompt_graph` and refused by
        # the reducer: `inputs` is not a mapping.
        lambda server, pid, object_info=None: (
            {"1": {"class_type": "KSampler", "inputs": ["nope"]}},
            [],
        ),
    )
    r = runnable.owner.get(f"{API}/workflows/{RUN_WF}/export")
    assert r.status_code == 409, r.text


def test_exporting_a_card_with_no_graph_at_all_says_so(workflow_env):
    """A card whose three tiers all answer nothing is a 409, not an empty file.

    BINNED_CARD is the one: no file, its only picture soft-deleted (so no
    embedded graph is reachable) and no instance document of its own.
    """
    r = workflow_env.owner.get(f"{API}/workflows/{BINNED_WF}/export")
    assert r.status_code == 409, r.text
    assert "no graph" in r.json()["detail"].lower()


def test_exporting_an_unknown_card_is_a_404(workflow_env):
    assert (
        workflow_env.owner.get(
            f"{API}/workflows/{AUTO_STACK_PREFIX}{_h('nope')}/export"
        ).status_code
        == 404
    )


def test_the_runnable_graph_is_the_run_unscrubbed(exportable):
    """Open in ComfyUI hands the owner's own ComfyUI what ran, not the export.

    And it answers although Run would refuse this graph: ComfyUI is where the
    missing nodes are fixed.
    """
    assert not _preflight(exportable.owner, workflow_id=RUN_WF)["ok"]
    r = exportable.owner.get(f"{API}/workflows/{RUN_WF}/graph")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["source"] == "picture"
    assert payload["name"]
    assert payload["workflow"]["5"]["inputs"]["text"] == EXPORT_PROMPT
    assert payload["workflow"]["3"]["inputs"]["seed"] == 4242


def test_the_runnable_graph_is_the_graph_run_submits(runnable):
    """Open in ComfyUI opens what Run runs, default recipe and all (#1623).

    The default recipe's steps are the value only Run's plan puts there. The
    seed is left out: Run rolls a new one per submission.
    """
    with runnable.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_group_default (workflow_id, address, value) "
            "VALUES (?, ?, '31')",
            (RUN_WORKFLOW, _core_address("3", "steps")),
        )
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WF})
    assert r.status_code == 200, r.text
    ran = runnable.submitted[0]["graph"]
    opened = runnable.owner.get(f"{API}/workflows/{RUN_WF}/graph").json()["workflow"]
    assert opened["3"]["inputs"]["steps"] == 31
    for graph in (ran, opened):
        graph["3"]["inputs"].pop("seed")
    assert opened == ran


def test_the_runnable_graph_keeps_a_pixlstash_saver(exportable):
    """Run swaps it for SaveImage and imports itself; ComfyUI by hand has no import."""
    exportable.graph["4"]["class_type"] = "PixlStashPictureSaver"
    graph = exportable.owner.get(f"{API}/workflows/{RUN_WF}/graph").json()["workflow"]
    assert graph["4"]["class_type"] == "PixlStashPictureSaver"


def test_the_runnable_graph_keeps_a_loader_whose_lora_comfyui_lacks(runnable):
    """Run bypasses it; opened, it stays for the owner to fix in ComfyUI."""
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["LoraLoader"]["input"]["required"]["lora_name"] = [["other.safetensors"], {}]
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    ran = _preflight(runnable.owner, workflow_id=RUN_WF)["groups"][0]
    assert [b["node_id"] for b in ran["bypassed_loras"]] == ["2"]
    graph = runnable.owner.get(f"{API}/workflows/{RUN_WF}/graph").json()["workflow"]
    assert graph["2"]["class_type"] == "LoraLoader"


def test_the_runnable_graph_of_a_card_without_one_is_a_409_and_unknown_a_404(
    workflow_env, monkeypatch
):
    monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "refused")
    )
    r = workflow_env.owner.get(f"{API}/workflows/{BINNED_WF}/graph")
    assert r.status_code == 409
    # The reason code, as Export's 409 names it.
    assert "no graph" in r.json()["detail"].lower()
    assert f"({run_service.NO_RUNNABLE_SOURCE})" in r.json()["detail"]
    assert (
        workflow_env.owner.get(
            f"{API}/workflows/{AUTO_STACK_PREFIX}{_h('nope')}/graph"
        ).status_code
        == 404
    )


def test_a_runnable_graph_nested_too_deeply_is_a_409_like_export(runnable, monkeypatch):
    def too_deep(server, picture_id, object_info=None):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(workflows_routes, "_load_embedded_api_prompt", too_deep)
    for route in ("graph", "export"):
        r = runnable.owner.get(f"{API}/workflows/{RUN_WF}/{route}")
        assert r.status_code == 409, (route, r.text)
        assert "nested too deeply" in r.json()["detail"]


def test_the_runnable_graph_keeps_the_batch_size(exportable):
    """Run pins the batch to 1 for its count; opened, the owner's 4 stays."""
    exportable.graph["9"] = {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 512, "height": 512, "batch_size": 4},
    }
    graph = exportable.owner.get(f"{API}/workflows/{RUN_WF}/graph").json()["workflow"]
    assert graph["9"]["inputs"]["batch_size"] == 4


def test_the_runnable_graph_blanks_a_credential_widget(exportable):
    """It crosses the network into ComfyUI's page, so a key does not."""
    exportable.graph["8"] = {
        "class_type": "SomeApiNode",
        "inputs": {"api_key": "example-key", "model": "keep-me"},
    }
    graph = exportable.owner.get(f"{API}/workflows/{RUN_WF}/graph").json()["workflow"]
    assert graph["8"]["inputs"] == {"api_key": "", "model": "keep-me"}


def test_the_runnable_graph_loads_the_copy_run_would(runnable, merged_checkpoint):
    """Resolved against this ComfyUI like Run… is, so a merged copy loads (#1439)."""
    merged_checkpoint(keeper="kept.safetensors")
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = [
        ["kept.safetensors"],
        {},
    ]
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    graph = runnable.owner.get(f"{API}/workflows/{RUN_WF}/graph").json()["workflow"]
    assert graph["1"]["inputs"]["ckpt_name"] == "kept.safetensors"


def test_a_runnable_graph_from_a_stored_recipe_gets_a_seed(runnable, monkeypatch):
    """The instance tier nulls seeds by design, so Open writes a fresh one."""

    def gone(server, picture_id, object_info=None):
        raise HTTPException(status_code=404, detail="Picture file missing")

    monkeypatch.setattr(workflows_routes, "_load_embedded_api_prompt", gone)
    payload = runnable.owner.get(f"{API}/workflows/{RUN_WF}/graph").json()
    assert payload["source"] == "instance", payload
    assert payload["seedless"] is False
    # Not the input's declared default of 0, which is what a nulled seed reads
    # as without the seed pass: every queue of it would make the same picture.
    assert payload["workflow"]["3"]["inputs"]["seed"] != 0


def test_a_runnable_graph_from_a_stored_recipe_gets_a_seed_without_comfyui(
    runnable, monkeypatch
):
    """`run_seed_targets` falls back to the graph's own seed inputs offline."""

    def gone(server, picture_id, object_info=None):
        raise HTTPException(status_code=404, detail="Picture file missing")

    monkeypatch.setattr(workflows_routes, "_load_embedded_api_prompt", gone)
    monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "refused")
    )
    payload = runnable.owner.get(f"{API}/workflows/{RUN_WF}/graph").json()
    assert payload["source"] == "instance", payload
    assert payload["workflow"]["3"]["inputs"]["seed"] != 0


def test_a_runnable_graph_takes_the_default_recipe_seed_over_a_fresh_one(
    runnable, monkeypatch
):
    """The seed is a parameter: the inspector's default is what opens."""

    def gone(server, picture_id, object_info=None):
        raise HTTPException(status_code=404, detail="Picture file missing")

    monkeypatch.setattr(workflows_routes, "_load_embedded_api_prompt", gone)
    with runnable.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_group_default (workflow_id, address, value) "
            "VALUES (?, ?, '777')",
            (RUN_WORKFLOW, _core_address("3", "seed")),
        )
    payload = runnable.owner.get(f"{API}/workflows/{RUN_WF}/graph").json()
    assert payload["source"] == "instance", payload
    assert payload["workflow"]["3"]["inputs"]["seed"] == 777


def test_duplicating_writes_a_runnable_file_the_original_does_not_lose(
    exportable, tmp_path
):
    """Duplicate is for the owner's own machine, so it is NOT scrubbed.

    A copy with its prompt and its models blanked would not run, and running it
    in ComfyUI is the entire reason the gesture exists.
    """
    _isolate_workflow_folders(tmp_path, exportable.monkeypatch)
    r = exportable.owner.post(f"{API}/workflows/{RUN_WF}/duplicate")
    assert r.status_code == 201, r.text
    body = r.json()
    # A manual workflow of its own, made from the original and saying so.
    assert body["workflow_id"].startswith("manual:")
    card = _by_key(_cards(exportable.owner, "?include_one_offs=true"))[
        body["workflow_id"]
    ]
    assert (card["manual"], card["name"]) == (True, body["name"])
    assert (
        card["from_name"]
        == _by_key(_cards(exportable.owner, "?include_one_offs=true"))[RUN_WF]["name"]
    )
    assert list(tmp_path.glob("*.json")) == []
    written = _written(exportable, body)
    assert written["5"]["inputs"]["text"] == EXPORT_PROMPT
    assert written["2"]["inputs"]["lora_name"] == FORGOTTEN_LORA
    assert written["3"]["inputs"]["seed"] == 4242


def test_duplicating_twice_puts_a_second_file_beside_the_first(exportable, tmp_path):
    """Identical copies are two workflows: a duplicate never replaces one."""
    _isolate_workflow_folders(tmp_path, exportable.monkeypatch)
    first = exportable.owner.post(f"{API}/workflows/{RUN_WF}/duplicate").json()
    second = exportable.owner.post(f"{API}/workflows/{RUN_WF}/duplicate").json()
    assert first["workflow_id"] != second["workflow_id"], "one copy replaced another"
    assert _written(exportable, first) == _written(exportable, second)


# ---------------------------------------------------------------------------
# The MCP round trip (#1436): export a graph, edit it, store it, preflight it
# ---------------------------------------------------------------------------


def _mcp_fetch(client: TestClient) -> mcp_server.Fetch:
    """``pixlstash-mcp``'s transport, over the owner's TestClient session."""

    def fetch(path, params, method="GET", body=None):
        r = client.request(method, f"{API}{path}", params=params, json=body)
        return r.status_code, r.headers.get("content-type", ""), r.content

    return fetch


def _mcp_json(fetch, tool: str, **arguments) -> dict:
    content = mcp_server.call_tool(fetch, tool, arguments, allow_write=True)
    return json.loads(content[0]["text"])


def test_the_mcp_round_trip_stores_an_edit_as_a_manual_workflow(exportable, tmp_path):
    """Export → edit → import → preflight, through the tools an agent calls."""
    (tmp_path / "store").mkdir()
    _isolate_workflow_folders(tmp_path / "store", exportable.monkeypatch)
    fetch = _mcp_fetch(exportable.owner)
    out = tmp_path / "agent" / "graph.json"

    exported = _mcp_json(
        fetch, "export_workflow_graph", workflow_id=RUN_WF, out_path=str(out)
    )
    assert exported["path"] == str(out)
    graph = json.loads(out.read_text())
    assert exported["nodes"] == len(graph)
    # The runnable graph, not the scrubbed export: ComfyUI can validate it.
    assert graph["5"]["inputs"]["text"] == EXPORT_PROMPT
    assert graph["3"]["inputs"]["seed"] == 4242

    graph["3"]["inputs"]["steps"] = 41
    out.write_text(json.dumps(graph))
    stored = _mcp_json(
        fetch, "import_workflow_graph", name="mcp-edited.json", path=str(out)
    )
    assert stored["matched"] is False, stored
    new_key = stored["workflow_id"]
    assert new_key.startswith("manual:")
    assert exportable.owner.get(f"{API}/workflows/{new_key}").status_code == 200

    # The same file again is a second manual workflow: identical copies are
    # allowed, and nothing is written to the folder either way.
    again = _mcp_json(
        fetch, "import_workflow_graph", name="mcp-edited-again.json", path=str(out)
    )
    assert (again["matched"], again["name"]) == (False, "mcp-edited-again")
    assert again["workflow_id"] != new_key
    assert list((tmp_path / "store").glob("*.json")) == []

    # Preflight reads the stored file: this ComfyUI lacks two of its nodes, and
    # saying so is the agent's feedback. Nothing is submitted.
    preflight = _mcp_json(fetch, "preflight_workflow", workflow_id=new_key)
    assert preflight["ok"] is False
    assert [g["workflow_id"] for g in preflight["groups"]] == [new_key]
    assert _reasons(preflight) == {"missing_nodes"}
    assert exportable.submitted == []


# ---------------------------------------------------------------------------
# Clone with new models
# ---------------------------------------------------------------------------

CLONE_CHECKPOINT = "krea2.safetensors"


@pytest.fixture
def cloneable(exportable, tmp_path):
    """RUN_CARD's picture graph, a second checkpoint on the shelf, and a folder."""
    _isolate_workflow_folders(tmp_path, exportable.monkeypatch)
    hub = exportable.server.hub
    with hub.transaction() as conn:
        checkpoint_id = conn.execute(
            "INSERT INTO model (file_kind, filename, provenance, base_model) "
            "VALUES ('checkpoint', ?, 'scanned', 'FLUX.1 dev')",
            (CLONE_CHECKPOINT,),
        ).lastrowid
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0].append(
        f"flux/{CLONE_CHECKPOINT}"
    )
    exportable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    yield SimpleNamespace(
        folder=tmp_path, checkpoint_id=checkpoint_id, **vars(exportable)
    )
    with hub.transaction() as conn:
        conn.execute("DELETE FROM model WHERE id = ?", (checkpoint_id,))


def _written(env, body) -> dict:
    """The document a copy stored as its manual workflow."""
    return manual_document(env.server.hub, body["workflow_id"])


def _manual_ids(env) -> list[str]:
    return [
        row[0]
        for row in env.server.hub.fetchall("SELECT workflow_id FROM workflow_document")
    ]


def _clone(env, swaps, name="Portrait on Krea"):
    return env.owner.post(
        f"{API}/workflows/{RUN_WF}/clone-with-models",
        json={"name": name, "swaps": swaps},
    )


def test_cloning_writes_a_file_in_comfyuis_spelling_in_the_same_workflow(cloneable):
    r = _clone(cloneable, {_SHELF_FILENAME: CLONE_CHECKPOINT})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["verified"] is True
    written = _written(cloneable, body)
    # The option ComfyUI lists, never the shelf's bare name.
    assert written["1"]["inputs"]["ckpt_name"] == f"flux/{CLONE_CHECKPOINT}"
    # The rest of the run travels, as a Duplicate's does.
    assert written["5"]["inputs"]["text"] == EXPORT_PROMPT
    # A manual workflow of its own, remembering what it was cloned from.
    assert body["workflow_id"].startswith("manual:")
    assert "workflow_key" not in body
    row = cloneable.server.hub.fetchone(
        "SELECT origin, from_workflow_id FROM workflow_document WHERE workflow_id = ?",
        (body["workflow_id"],),
    )
    assert tuple(row) == ("clone", RUN_WF)
    # The original file is untouched.
    assert cloneable.graph["1"]["inputs"]["ckpt_name"] == _SHELF_FILENAME


def test_cloning_with_comfyui_down_writes_the_names_unchecked(cloneable):
    cloneable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "unreachable")
    )
    r = _clone(cloneable, {_SHELF_FILENAME: CLONE_CHECKPOINT})
    assert r.status_code == 201, r.text
    assert r.json()["verified"] is False
    written = _written(cloneable, r.json())
    assert written["1"]["inputs"]["ckpt_name"] == CLONE_CHECKPOINT


def test_a_clone_where_nothing_could_be_swapped_is_refused(cloneable):
    r = _clone(cloneable, {_SHELF_FILENAME: "not-on-comfyui.safetensors"})
    assert r.status_code == 409, r.text
    assert "not_on_comfyui" in r.json()["detail"]
    assert _manual_ids(cloneable) == []


def test_a_clone_that_cannot_take_every_model_is_not_written(cloneable):
    """The checkpoint would land and the LoRA would not: nothing is written."""
    r = _clone(
        cloneable,
        {
            _SHELF_FILENAME: CLONE_CHECKPOINT,
            FORGOTTEN_LORA: "not-on-comfyui.safetensors",
        },
    )
    assert r.status_code == 409, r.text
    assert _manual_ids(cloneable) == []


def test_a_clone_onto_the_files_it_already_loads_says_so(cloneable):
    r = _clone(cloneable, {_SHELF_FILENAME: _SHELF_FILENAME})
    assert r.status_code == 409, r.text
    assert "already loads" in r.json()["detail"]


@pytest.mark.parametrize("verb", ["duplicate", "clone-with-models"])
def test_a_copy_keeps_the_picture_inputs_its_file_opted_out_of(cloneable, verb):
    """`Source.graph` is sanitised; the file's bindings have to be put back."""
    source = run_service.Source(
        graph=json.loads(json.dumps(cloneable.graph)),
        origin=run_service.FROM_FILE,
        bindings=[],
    )
    cloneable.monkeypatch.setattr(
        run_service, "resolve_source", lambda *args, **kwargs: (source, None)
    )
    r = (
        _clone(cloneable, {_SHELF_FILENAME: CLONE_CHECKPOINT})
        if verb == "clone-with-models"
        else cloneable.owner.post(f"{API}/workflows/{RUN_WF}/duplicate")
    )
    assert r.status_code == 201, r.text
    written = _written(cloneable, r.json())
    assert written["pixlstash_bindings"] == []


def test_the_base_slot_is_offered_only_what_its_loader_could_load(cloneable):
    hub = cloneable.server.hub
    with hub.transaction() as conn:
        added = [
            conn.execute(
                "INSERT INTO model (file_kind, filename, provenance) "
                "VALUES ('checkpoint', ?, 'scanned')",
                (filename,),
            ).lastrowid
            for filename in ("krea2-q8.gguf", "unlisted.safetensors")
        ]
    try:
        body = cloneable.owner.get(f"{API}/workflows/{RUN_WF}/model-swap").json()
        offered = {m["filename"] for m in body["checkpoints"]}
        # Listed by the CheckpointLoaderSimple ComfyUI answers for.
        assert CLONE_CHECKPOINT in offered
        # A GGUF file for a .safetensors loader, and a file ComfyUI does not list.
        assert "krea2-q8.gguf" not in offered
        assert "unlisted.safetensors" not in offered

        cloneable.monkeypatch.setattr(
            workflows_routes, "_read_object_info", lambda url: (None, "down")
        )
        down = cloneable.owner.get(f"{API}/workflows/{RUN_WF}/model-swap").json()
        offered = {m["filename"] for m in down["checkpoints"]}
        # Unchecked, so every file of the loader's type; never the GGUF one.
        assert "unlisted.safetensors" in offered
        assert "krea2-q8.gguf" not in offered
    finally:
        with hub.transaction() as conn:
            conn.executemany("DELETE FROM model WHERE id = ?", [(i,) for i in added])


def test_cloning_a_card_with_no_graph_is_a_409(workflow_env):
    r = workflow_env.owner.post(
        f"{API}/workflows/{BINNED_WF}/clone-with-models",
        json={"name": "x", "swaps": {"a.safetensors": "b.safetensors"}},
    )
    assert r.status_code == 409, r.text


def test_planning_clones_of_a_card_with_no_graph_is_a_409(workflow_env):
    r = workflow_env.owner.post(
        f"{API}/workflows/{BINNED_WF}/set-clone-plans", json={"sets": []}
    )
    assert r.status_code == 409, r.text


def test_a_replacement_that_is_not_a_model_file_is_a_422(cloneable):
    assert _clone(cloneable, {_SHELF_FILENAME: "krea2"}).status_code == 422


def _plans(env, **sets):
    r = env.owner.post(
        f"{API}/workflows/{RUN_WF}/set-clone-plans",
        # The first id is the set's checkpoint, the rest its other models.
        json={
            "sets": [
                {"key": k, "checkpoint_ids": ids[:1], "model_ids": ids[1:]}
                for k, ids in sets.items()
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    return body, {plan["key"]: plan for plan in body["plans"]}


def test_a_set_plan_names_the_swap_and_the_loader_before_and_after(cloneable):
    body, plans = _plans(cloneable, krea=[cloneable.checkpoint_id])
    assert body["base_filename"] == _SHELF_FILENAME
    krea = plans["krea"]
    assert krea["swaps"] == {_SHELF_FILENAME: CLONE_CHECKPOINT}
    # The workflow's checkpoint has no known base model: not the same one, so
    # its LoRAs would not come along, and nothing guessed it did.
    assert (krea["fit"], krea["keeps_loras"], krea["reason"]) == ("other", False, None)
    assert krea["loaders"] == [
        {
            "node_id": "1",
            "kind": "checkpoint",
            "was_class": "CheckpointLoaderSimple",
            "now_class": "CheckpointLoaderSimple",
            "was": [_SHELF_FILENAME],
            "now": [f"flux/{CLONE_CHECKPOINT}"],
            "pack": None,
            "installed": None,
        }
    ]
    # A plan writes nothing.
    assert _manual_ids(cloneable) == []


def test_a_workflow_with_no_checkpoint_says_so_for_every_set(cloneable):
    """Its own reason, not the set's: none of them can be cloned onto."""
    graph = _embedded_export_graph()
    del graph["1"]
    cloneable.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (graph, []),
    )
    hub = cloneable.server.hub
    with hub.transaction() as conn:
        vae_id = conn.execute(
            "INSERT INTO model (file_kind, filename, provenance) "
            "VALUES ('vae', 'ae.safetensors', 'scanned')"
        ).lastrowid
    try:
        _body, plans = _plans(cloneable, vae_only=[vae_id])
    finally:
        with hub.transaction() as conn:
            conn.execute("DELETE FROM model WHERE id = ?", (vae_id,))
    assert plans["vae_only"]["fit"] == "wont_load"
    assert plans["vae_only"]["reason"] == "This workflow loads no checkpoint"


def test_a_family_read_off_the_graphs_loras_makes_a_set_of_that_family(cloneable):
    """The checkpoint has no known base model; its LoRA says FLUX.1 schnell."""
    hub = cloneable.server.hub
    with hub.transaction() as conn:
        lora_id = conn.execute(
            "INSERT INTO model "
            "(file_kind, kind, filename, sha256, base_model, provenance) "
            "VALUES ('adapter', 'lora', ?, ?, 'FLUX.1 schnell', 'scanned')",
            (FORGOTTEN_LORA, _h("schnell-lora")),
        ).lastrowid
    try:
        _body, plans = _plans(cloneable, krea=[cloneable.checkpoint_id])
    finally:
        with hub.transaction() as conn:
            conn.execute("DELETE FROM model WHERE id = ?", (lora_id,))
    # FLUX.1 dev is another base model of the same family: LoRAs go, but the
    # set is offered with the family, as `keeps` reads the same source.
    assert plans["krea"]["fit"] == "same_family"
    assert plans["krea"]["keeps_loras"] is False


def test_a_set_whose_model_left_the_shelf_is_refused_on_its_own(cloneable):
    """A stale id refuses that set, not the read: the other set still plans."""
    _body, plans = _plans(cloneable, stale=[987654321], krea=[cloneable.checkpoint_id])
    assert plans["stale"]["fit"] == "wont_load"
    assert plans["stale"]["reason"] == "A model of this set is no longer on the shelf"
    assert plans["krea"]["fit"] != "wont_load"


def test_two_sets_asked_under_one_key_are_a_422(cloneable):
    ask = {
        "key": "same",
        "checkpoint_ids": [cloneable.checkpoint_id],
        "model_ids": [],
    }
    r = cloneable.owner.post(
        f"{API}/workflows/{RUN_WF}/set-clone-plans", json={"sets": [ask, ask]}
    )
    assert r.status_code == 422, r.text


def test_a_set_plan_says_which_sets_will_not_load_and_why(cloneable):
    hub = cloneable.server.hub
    with hub.transaction() as conn:
        vae_id = conn.execute(
            "INSERT INTO model (file_kind, filename, provenance) "
            "VALUES ('vae', 'ae.safetensors', 'scanned')"
        ).lastrowid
        unlisted_id = conn.execute(
            "INSERT INTO model (file_kind, filename, provenance) "
            "VALUES ('checkpoint', 'unlisted.safetensors', 'scanned')"
        ).lastrowid
    try:
        _body, plans = _plans(cloneable, vae_only=[vae_id], unlisted=[unlisted_id])
    finally:
        with hub.transaction() as conn:
            conn.executemany(
                "DELETE FROM model WHERE id = ?", [(vae_id,), (unlisted_id,)]
            )
    assert plans["vae_only"]["fit"] == "wont_load"
    assert plans["vae_only"]["reason"] == "Has no checkpoint"
    assert plans["unlisted"]["fit"] == "wont_load"
    assert plans["unlisted"]["reason"] == "ComfyUI does not list unlisted.safetensors"


def test_a_gguf_set_on_a_whole_checkpoint_loader_names_the_loader_it_needs(
    cloneable,
):
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["UnetLoaderGGUF"] = {
        "input": {"required": {"unet_name": [["flux1-dev-Q8_0.gguf"], {}]}}
    }
    cloneable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    hub = cloneable.server.hub
    with hub.transaction() as conn:
        gguf_id = conn.execute(
            "INSERT INTO model (file_kind, filename, provenance) "
            "VALUES ('unknown', 'flux1-dev-Q8_0.gguf', 'scanned')"
        ).lastrowid
    try:
        _body, plans = _plans(cloneable, gguf=[gguf_id])
    finally:
        with hub.transaction() as conn:
            conn.execute("DELETE FROM model WHERE id = ?", (gguf_id,))
    # A whole-checkpoint loader has no GGUF twin: refused with the reason.
    assert plans["gguf"]["fit"] == "wont_load"
    assert plans["gguf"]["reason"] == "Needs a UnetLoaderGGUF"


def test_a_two_model_graph_takes_each_set_checkpoint_by_name(cloneable):
    """#1690: a Wan 2.2 high/low graph cloned onto a set holding a pair swaps
    each expert for its own, whatever order the set lists them in; a set
    holding one expert replaces that one and leaves the other, and a graph
    loading one expert takes the set's matching one."""
    graph = _embedded_export_graph()
    graph["1"] = {
        "class_type": "UNETLoader",
        "inputs": {"unet_name": "wan_high_noise.safetensors"},
    }
    graph["10"] = {
        "class_type": "UNETLoader",
        "inputs": {"unet_name": "wan_low_noise.safetensors"},
    }
    cloneable.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (graph, []),
    )
    cloneable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "down")
    )
    hub = cloneable.server.hub
    with hub.transaction() as conn:
        low, high, own_low = (
            conn.execute(
                "INSERT INTO model (file_kind, filename, provenance) "
                "VALUES ('checkpoint', ?, 'scanned')",
                (filename,),
            ).lastrowid
            for filename in (
                "wan_v2_low_noise.safetensors",
                "wan_v2_high_noise.safetensors",
                "wan_low_noise.safetensors",
            )
        )
    try:
        r = cloneable.owner.post(
            f"{API}/workflows/{RUN_WF}/set-clone-plans",
            json={
                "sets": [
                    {"key": "pair", "checkpoint_ids": [low, high], "model_ids": []},
                    {"key": "low", "checkpoint_ids": [low], "model_ids": []},
                    # The very file the graph loads: paired, though unchanged.
                    {"key": "own", "checkpoint_ids": [own_low], "model_ids": []},
                ]
            },
        )
        # A graph loading one expert takes the set's matching one, not the
        # first the set lists.
        graph.pop("10")
        one = cloneable.owner.post(
            f"{API}/workflows/{RUN_WF}/set-clone-plans",
            json={
                "sets": [
                    {"key": "pair", "checkpoint_ids": [low, high], "model_ids": []}
                ]
            },
        )
    finally:
        with hub.transaction() as conn:
            conn.executemany(
                "DELETE FROM model WHERE id = ?", [(low,), (high,), (own_low,)]
            )
    assert r.status_code == 200, r.text
    plans = {plan["key"]: plan for plan in r.json()["plans"]}
    assert plans["pair"]["swaps"] == {
        "wan_high_noise.safetensors": "wan_v2_high_noise.safetensors",
        "wan_low_noise.safetensors": "wan_v2_low_noise.safetensors",
    }
    assert plans["low"]["swaps"] == {
        "wan_low_noise.safetensors": "wan_v2_low_noise.safetensors"
    }
    # What the dialog warns about is the pairing, not the unchanged rows.
    assert plans["pair"]["unpaired_bases"] == []
    assert plans["low"]["unpaired_bases"] == ["wan_high_noise.safetensors"]
    assert plans["own"]["swaps"] == {}
    assert plans["own"]["unpaired_bases"] == ["wan_high_noise.safetensors"]
    assert one.status_code == 200, one.text
    assert one.json()["plans"][0]["swaps"] == {
        "wan_high_noise.safetensors": "wan_v2_high_noise.safetensors"
    }


def test_a_set_vae_replaces_only_the_graph_vae_of_its_layout(cloneable):
    """Two VAE loaders are two different files: one set VAE is not both."""
    graph = _embedded_export_graph()
    graph["8"] = {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}}
    graph["9"] = {
        "class_type": "VAELoader",
        "inputs": {"vae_name": "wan-video-vae.safetensors"},
    }
    cloneable.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (graph, []),
    )
    hub = cloneable.server.hub
    with hub.transaction() as conn:
        ids = [
            conn.execute(
                "INSERT INTO model (file_kind, filename, family, provenance) "
                "VALUES ('vae', ?, ?, 'scanned')",
                (filename, family),
            ).lastrowid
            for filename, family in (
                ("ae.safetensors", "vae_16ch"),
                ("wan-video-vae.safetensors", "wan_vae"),
                ("flux2-ae.safetensors", "vae_16ch"),
            )
        ]
    try:
        _body, plans = _plans(cloneable, flux=[cloneable.checkpoint_id, ids[2]])
    finally:
        with hub.transaction() as conn:
            conn.executemany("DELETE FROM model WHERE id = ?", [(i,) for i in ids])
    assert plans["flux"]["swaps"] == {
        _SHELF_FILENAME: CLONE_CHECKPOINT,
        "ae.safetensors": "flux2-ae.safetensors",
    }


def test_a_set_vae_of_another_known_layout_is_not_swapped_in(cloneable):
    """One slot, one set VAE, two known layouts that differ: no fallback."""
    graph = _embedded_export_graph()
    graph["8"] = {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}}
    cloneable.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (graph, []),
    )
    hub = cloneable.server.hub
    with hub.transaction() as conn:
        ids = [
            conn.execute(
                "INSERT INTO model (file_kind, filename, family, provenance) "
                "VALUES ('vae', ?, ?, 'scanned')",
                (filename, family),
            ).lastrowid
            for filename, family in (
                ("ae.safetensors", "vae_16ch"),
                ("wan-video-vae.safetensors", "wan_vae"),
            )
        ]
    try:
        _body, plans = _plans(cloneable, wan=[cloneable.checkpoint_id, ids[1]])
    finally:
        with hub.transaction() as conn:
            conn.executemany("DELETE FROM model WHERE id = ?", [(i,) for i in ids])
    assert plans["wan"]["swaps"] == {_SHELF_FILENAME: CLONE_CHECKPOINT}


def test_two_unknown_base_models_are_not_the_same_one(cloneable):
    """Neither checkpoint's base model is known: the LoRAs are not kept."""
    cloneable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "down")
    )
    hub = cloneable.server.hub
    with hub.transaction() as conn:
        unknown_id = conn.execute(
            "INSERT INTO model (file_kind, filename, provenance) "
            "VALUES ('checkpoint', 'no-base.safetensors', 'scanned')"
        ).lastrowid
    try:
        _body, plans = _plans(cloneable, unknown=[unknown_id])
    finally:
        with hub.transaction() as conn:
            conn.execute("DELETE FROM model WHERE id = ?", (unknown_id,))
    assert plans["unknown"]["fit"] == "other"
    assert plans["unknown"]["keeps_loras"] is False


def test_a_set_of_the_same_base_model_keeps_the_loras(cloneable):
    hub = cloneable.server.hub
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET base_model = 'FLUX.1 dev' WHERE filename = ?",
            (_SHELF_FILENAME,),
        )
    try:
        _body, plans = _plans(cloneable, krea=[cloneable.checkpoint_id])
    finally:
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE model SET base_model = NULL WHERE filename = ?",
                (_SHELF_FILENAME,),
            )
    assert plans["krea"]["fit"] == "same_base_model"
    assert plans["krea"]["keeps_loras"] is True


def test_a_clone_can_carry_a_chain_with_its_loras_removed(cloneable):
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0].append(
        f"flux/{CLONE_CHECKPOINT}"
    )
    info["CheckpointLoaderSimple"]["output"] = ["MODEL", "CLIP", "VAE"]
    info["KSampler"]["input"]["required"].update(
        {
            "model": ["MODEL", {}],
            "positive": ["CONDITIONING", {}],
            "negative": ["CONDITIONING", {}],
        }
    )
    info["KSampler"]["output"] = ["LATENT"]
    info["SaveImage"]["input"]["required"]["images"] = ["IMAGE", {}]
    info["CLIPTextEncode"] = {
        "input": {"required": {"text": ["STRING", {}], "clip": ["CLIP", {}]}},
        "output": ["CONDITIONING"],
    }
    info["LoadImage"] = {
        "input": {"required": {"image": [["a-private-photo.png"], {}]}},
        "output": ["IMAGE", "MASK"],
    }
    cloneable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    r = cloneable.owner.post(
        f"{API}/workflows/{RUN_WF}/clone-with-models",
        json={
            "name": "Portrait on Krea",
            "swaps": {_SHELF_FILENAME: CLONE_CHECKPOINT},
            "loras": {"entries": []},
        },
    )
    assert r.status_code == 201, r.text
    written = _written(cloneable, r.json())
    assert written["1"]["inputs"]["ckpt_name"] == f"flux/{CLONE_CHECKPOINT}"
    # The loader is gone and the wires are joined past it.
    assert "2" not in written
    assert written["3"]["inputs"]["model"] == ["1", 0]
    assert r.json()["loaders"] == []

    # The same chain edit onto the files it already loads is still a change.
    again = cloneable.owner.post(
        f"{API}/workflows/{RUN_WF}/clone-with-models",
        json={
            "name": "Portrait without LoRAs",
            "swaps": {_SHELF_FILENAME: _SHELF_FILENAME},
            "loras": {"entries": []},
        },
    )
    assert again.status_code == 201, again.text
    kept = _written(cloneable, again.json())
    assert kept["1"]["inputs"]["ckpt_name"] == _SHELF_FILENAME
    assert "2" not in kept


def test_a_clone_carrying_a_chain_needs_comfyui(cloneable):
    cloneable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "unreachable")
    )
    r = cloneable.owner.post(
        f"{API}/workflows/{RUN_WF}/clone-with-models",
        json={
            "name": "x",
            "swaps": {_SHELF_FILENAME: CLONE_CHECKPOINT},
            "loras": {"entries": []},
        },
    )
    assert r.status_code == 503, r.text
    assert _manual_ids(cloneable) == []


def test_the_swap_options_name_the_graphs_files_and_the_shelf(cloneable):
    r = cloneable.owner.get(f"{API}/workflows/{RUN_WF}/model-swap")
    assert r.status_code == 200, r.text
    body = r.json()
    slots = {slot["filename"]: slot for slot in body["slots"]}
    assert slots[_SHELF_FILENAME]["kind"] == "checkpoint"
    assert slots[_SHELF_FILENAME]["model"]["filename"] == _SHELF_FILENAME
    assert slots[FORGOTTEN_LORA] == {
        "filename": FORGOTTEN_LORA,
        "kind": "lora",
        "model": None,
    }
    assert CLONE_CHECKPOINT in {m["filename"] for m in body["checkpoints"]}
    assert body["proposals"] == {}

    chosen = cloneable.owner.get(
        f"{API}/workflows/{RUN_WF}/model-swap",
        params={"checkpoint_id": cloneable.checkpoint_id},
    ).json()
    assert chosen["checkpoint_family"] == "flux1"
    assert set(chosen["proposals"]) == {"vae", "text_encoder"}


def test_the_swap_options_carry_the_companion_proposals(cloneable):
    asked = []

    def proposals(hub, checkpoint_id, index=None):
        asked.append(checkpoint_id)
        return {
            "vae": [
                {
                    "id": 7,
                    "filename": "flux-vae.safetensors",
                    "display_name": None,
                    "via": "family",
                    "recipes": 2,
                }
            ],
            "text_encoder": [],
        }

    cloneable.monkeypatch.setattr(workflows_routes, "propose_companions", proposals)
    body = cloneable.owner.get(
        f"{API}/workflows/{RUN_WF}/model-swap",
        params={"checkpoint_id": cloneable.checkpoint_id},
    ).json()
    assert asked == [cloneable.checkpoint_id]
    assert body["proposals"]["vae"][0]["filename"] == "flux-vae.safetensors"
    assert body["proposals"]["vae"][0]["via"] == "family"


def test_a_lora_trained_on_another_family_is_flagged_never_dropped(cloneable):
    graph = _embedded_export_graph(lora=RUN_ADAPTER_FILENAME)
    cloneable.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (graph, []),
    )
    hub = cloneable.server.hub
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET base_model = 'SDXL 1.0' WHERE sha256 = ?",
            (RUN_ADAPTER_DIGEST,),
        )
    body = cloneable.owner.get(
        f"{API}/workflows/{RUN_WF}/model-swap",
        params={"checkpoint_id": cloneable.checkpoint_id},
    ).json()
    assert body["flags"] == [
        {
            "filename": RUN_ADAPTER_FILENAME,
            "kind": "lora",
            "base_model": "SDXL 1.0",
            "family": "sdxl",
            "modality": "image",
        }
    ]
    assert body["checkpoint_modality"] == "image"
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET base_model = 'Wan 2.2' WHERE sha256 = ?",
            (RUN_ADAPTER_DIGEST,),
        )
    video = cloneable.owner.get(
        f"{API}/workflows/{RUN_WF}/model-swap",
        params={"checkpoint_id": cloneable.checkpoint_id},
    ).json()
    assert [(f["family"], f["modality"]) for f in video["flags"]] == [("wan", "video")]
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET base_model = 'FLUX.1 schnell' WHERE sha256 = ?",
            (RUN_ADAPTER_DIGEST,),
        )
    same_family = cloneable.owner.get(
        f"{API}/workflows/{RUN_WF}/model-swap",
        params={"checkpoint_id": cloneable.checkpoint_id},
    ).json()
    assert same_family["flags"] == []


def _with_support_loaders(graph: dict) -> dict:
    """The clone graph plus a VAE, a pair of text encoders and a GGUF encoder."""
    graph = json.loads(json.dumps(graph))
    graph["20"] = {
        "class_type": "VAELoader",
        "inputs": {"vae_name": "test-vae-fp8.safetensors"},
    }
    graph["21"] = {
        "class_type": "DualCLIPLoader",
        "inputs": {
            "clip_name1": "test-clip-l.safetensors",
            "clip_name2": "test-t5-fp16.safetensors",
            "type": "flux",
        },
    }
    graph["22"] = {
        "class_type": "CLIPLoaderGGUF",
        "inputs": {"clip_name": "test-umt5-q8.gguf", "type": "wan"},
    }
    return graph


def test_the_replacements_go_with_the_checkpoint_and_load_in_the_loader(cloneable):
    """#1596: Replace with… offers what works together AND what the loader lists.

    Evidence first (the checkpoint's workflow sets and co-occurrence, here
    stubbed), then each loader's own list: a core loader never lists GGUF, a
    GGUF loader lists safetensors too.
    """
    graph = _with_support_loaders(cloneable.graph)
    cloneable.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (graph, []),
    )
    asked = []

    def proposals(hub, checkpoint_id, index=None):
        asked.append(checkpoint_id)

        def entry(model_id, filename, via):
            return {
                "id": model_id,
                "filename": filename,
                "display_name": None,
                "via": via,
            }

        return {
            "vae": [
                entry(1, "test-vae-bf16.safetensors", "grouped"),
                entry(2, "test-vae-unlisted.safetensors", "checkpoint"),
            ],
            "text_encoder": [
                entry(3, "test-t5-bf16.safetensors", "checkpoint"),
                entry(4, "test-t5-q8.gguf", "family"),
            ],
        }

    cloneable.monkeypatch.setattr(workflows_routes, "propose_companions", proposals)
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0].append(
        f"flux/{CLONE_CHECKPOINT}"
    )
    info["VAELoader"] = {
        "input": {"required": {"vae_name": [["test-vae-bf16.safetensors"], {}]}}
    }
    both = [["test-clip-l.safetensors", "test-t5-bf16.safetensors"], {}]
    info["DualCLIPLoader"] = {
        "input": {"required": {"clip_name1": both, "clip_name2": both}}
    }
    info["CLIPLoaderGGUF"] = {
        "input": {
            "required": {
                "clip_name": [["test-t5-bf16.safetensors", "test-t5-q8.gguf"], {}]
            }
        }
    }
    cloneable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )

    def offered(replacing, **params):
        r = cloneable.owner.get(
            f"{API}/workflows/{RUN_WF}/model-swap",
            params={"replacing": replacing, **params},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        return [c["filename"] for c in body["replacements"]], body[
            "replacements_reason"
        ]

    shelf_id = cloneable.server.hub.fetchone(
        "SELECT id FROM model WHERE filename = ?", (_SHELF_FILENAME,)
    )["id"]
    assert offered("test-vae-fp8.safetensors") == (["test-vae-bf16.safetensors"], None)
    assert asked == [shelf_id], "evidence asked about another checkpoint"
    assert offered("test-t5-fp16.safetensors") == (["test-t5-bf16.safetensors"], None)
    assert offered("test-umt5-q8.gguf") == (
        ["test-t5-bf16.safetensors", "test-t5-q8.gguf"],
        None,
    )
    # A checkpoint is offered what its loader lists, with no evidence asked.
    names, _ = offered(_SHELF_FILENAME)
    assert CLONE_CHECKPOINT in names and _SHELF_FILENAME not in names
    # ComfyUI down: the file type decides, so a core loader gets no GGUF.
    cloneable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "unreachable")
    )
    assert offered("test-t5-fp16.safetensors") == (["test-t5-bf16.safetensors"], None)
    assert offered("test-vae-fp8.safetensors") == (
        ["test-vae-bf16.safetensors", "test-vae-unlisted.safetensors"],
        None,
    )
    # Nothing goes with it, or nothing that does can load here.
    cloneable.monkeypatch.setattr(
        workflows_routes,
        "propose_companions",
        lambda hub, checkpoint_id, index=None: {"vae": [], "text_encoder": []},
    )
    assert offered("test-vae-fp8.safetensors") == ([], "none_go_with_it")
    cloneable.monkeypatch.setattr(workflows_routes, "propose_companions", proposals)
    graph["20"]["inputs"]["vae_name"] = "test-vae-fp8.pt"
    assert offered("test-vae-fp8.pt") == ([], "none_loadable")
    # One file in slots of two kinds: which to replace is the caller's to say.
    graph["22"]["inputs"]["clip_name"] = "test-vae-fp8.pt"
    r = cloneable.owner.get(
        f"{API}/workflows/{RUN_WF}/model-swap",
        params={"replacing": "test-vae-fp8.pt"},
    )
    assert r.status_code == 409, r.text
    assert "as a text encoder and a VAE" in r.json()["detail"], r.text
    assert offered("test-vae-fp8.pt", slot_kind="vae") == ([], "none_loadable")
    graph["22"]["inputs"]["clip_name"] = "test-umt5-q8.gguf"
    # A kind the graph does not load the file as, or a file it does not load.
    r = cloneable.owner.get(
        f"{API}/workflows/{RUN_WF}/model-swap",
        params={"replacing": "test-vae-fp8.pt", "slot_kind": "text_encoder"},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "This workflow loads that model as a VAE, not a text encoder."
    ), r.text
    r = cloneable.owner.get(
        f"{API}/workflows/{RUN_WF}/model-swap",
        params={"replacing": "test-not-in-graph.safetensors"},
    )
    assert r.status_code == 409, r.text


def test_a_replacement_the_loader_cannot_load_is_offered_through_our_loader(
    cloneable,
):
    """#1605: what the loader does not list is offered through a PixlStash one.

    Only a file the shelf has a digest for, never a GGUF (our loaders read
    through core ComfyUI), and only with ComfyUI-PixlStash installed: without
    it the row says that is what is missing.
    """
    graph = _with_support_loaders(cloneable.graph)
    cloneable.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (graph, []),
    )

    def entry(model_id, filename):
        return {"id": model_id, "filename": filename, "display_name": None}

    cloneable.monkeypatch.setattr(
        workflows_routes,
        "propose_companions",
        lambda hub, checkpoint_id, index=None: {
            "vae": [
                {**entry(1, "test-vae-unlisted.safetensors"), "via": "grouped"},
                {**entry(2, "test-vae-unhashed.safetensors"), "via": "grouped"},
            ],
            "text_encoder": [
                {**entry(3, "test-t5-unlisted.safetensors"), "via": "checkpoint"},
                {**entry(4, "test-t5-q8.gguf"), "via": "family"},
            ],
        },
    )
    digests = {
        "test-vae-unlisted.safetensors": ("vae", _h("vae-unlisted")),
        "test-vae-unhashed.safetensors": ("vae", _h("vae-copy-removed")),
        "test-t5-unlisted.safetensors": ("text_encoder", _h("t5-unlisted")),
        "test-t5-q8.gguf": ("text_encoder", _h("t5-q8")),
        "test-clip-l.safetensors": ("text_encoder", _h("clip-l")),
    }
    with cloneable.server.hub.transaction() as conn:
        for name, (kind, digest) in digests.items():
            # Its only copy merged away: nothing left for our loader to fetch.
            state = "removed" if name == "test-vae-unhashed.safetensors" else "present"
            _shelve_with_copy(conn, kind, name, digest, state)
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["VAELoader"] = {
        "input": {"required": {"vae_name": [["test-vae-other.safetensors"], {}]}}
    }
    listed = [["test-clip-l.safetensors"], {}]
    info["DualCLIPLoader"] = {
        "input": {"required": {"clip_name1": listed, "clip_name2": listed}}
    }
    cloneable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )

    def offered(replacing):
        r = cloneable.owner.get(
            f"{API}/workflows/{RUN_WF}/model-swap",
            params={"replacing": replacing},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        return [(c["filename"], c["loader"]) for c in body["replacements"]], body[
            "replacements_reason"
        ]

    try:
        assert offered("test-vae-fp8.safetensors") == ([], "needs_pixlstash_nodes")
        # A second loader of the file that our loader could never stand in
        # for: installing the pack would not help, so it is not what is said.
        graph["24"] = {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "test-vae-fp8.safetensors", "device": "cpu"},
        }
        assert offered("test-vae-fp8.safetensors") == ([], "none_loadable")
        del graph["24"]
        info["PixlStashVAELoader"] = {
            "input": {"required": {"vae_sha256": ["STRING", {}]}}
        }
        info["PixlStashCLIPLoader"] = {
            "input": {
                "required": {
                    "clip_sha256": ["STRING", {}],
                    "type": [["stable_diffusion", "flux"], {}],
                }
            }
        }
        assert offered("test-vae-fp8.safetensors") == (
            [("test-vae-unlisted.safetensors", "PixlStashVAELoader")],
            None,
        )
        assert offered("test-t5-fp16.safetensors") == (
            [("test-t5-unlisted.safetensors", "PixlStashCLIPLoader")],
            None,
        )
        # A CLIP family our loader does not list is not one it can stand in for.
        info["PixlStashCLIPLoader"]["input"]["required"]["type"] = [["sd3"], {}]
        assert offered("test-t5-fp16.safetensors") == ([], "none_loadable")
    finally:
        with cloneable.server.hub.transaction() as conn:
            _unshelve(conn, [digest for _kind, digest in digests.values()])


def test_a_missing_checkpoint_is_offered_only_its_own_base_model(cloneable):
    """A replacement of another base model would not match the LoRAs around it.

    The missing file's shelf base model decides; without one, the one base
    model the graph's LoRAs agree on; with neither, nothing is narrowed.
    """
    hub = cloneable.server.hub
    with hub.transaction() as conn:
        sdxl_id = conn.execute(
            "INSERT INTO model (file_kind, filename, provenance, base_model) "
            "VALUES ('checkpoint', ?, 'scanned', 'sdxl')",
            (_REPLACEMENT_FILENAME,),
        ).lastrowid
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0] += [
        CLONE_CHECKPOINT,
        _REPLACEMENT_FILENAME,
    ]
    cloneable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )

    def offered():
        body = cloneable.owner.get(
            f"{API}/workflows/{RUN_WF}/model-swap",
            params={"replacing": _SHELF_FILENAME},
        ).json()
        return [c["filename"] for c in body["replacements"]], body[
            "replacements_reason"
        ]

    def set_base(where, value, *args):
        with hub.transaction() as conn:
            conn.execute(
                f"UPDATE model SET base_model = ? WHERE {where}", (value, *args)
            )

    try:
        # Nothing says what the missing one was: every checkpoint.
        every = offered()
        assert {CLONE_CHECKPOINT, _REPLACEMENT_FILENAME} <= set(every[0])
        # Its shelf row does, spelled differently from the candidate's.
        set_base("filename = ?", "SDXL 1.0", _SHELF_FILENAME)
        assert offered() == ([_REPLACEMENT_FILENAME], None)
        # Without it, the graph's LoRA does.
        set_base("filename = ?", None, _SHELF_FILENAME)
        cloneable.graph["2"]["inputs"]["lora_name"] = RUN_ADAPTER_FILENAME
        set_base("sha256 = ?", "FLUX.1 dev", RUN_ADAPTER_DIGEST)
        assert offered() == ([CLONE_CHECKPOINT], None)
        # A base model nothing on the shelf has is said as such.
        set_base("sha256 = ?", "SD 1.5", RUN_ADAPTER_DIGEST)
        assert offered() == ([], "none_same_base_model")
        # LoRAs that disagree say nothing.
        cloneable.graph["8"] = {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": _REPLACEMENT_FILENAME},
        }
        assert offered() == every
        # Nor do they beside a second base model they may not feed.
        cloneable.graph["8"] = {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": CLONE_CHECKPOINT},
        }
        assert offered() == every
    finally:
        set_base("sha256 = ?", None, RUN_ADAPTER_DIGEST)
        set_base("filename = ?", None, _SHELF_FILENAME)
        with hub.transaction() as conn:
            conn.execute("DELETE FROM model WHERE id = ?", (sdxl_id,))


def test_an_empty_checkpoints_folder_says_the_checkpoint_is_missing(cloneable):
    """ComfyUI listing no checkpoints at all is not "cannot tell".

    The pre-flight flags the file, and nothing on the shelf is offered in its
    place, since that loader can load none of it.
    """
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = [[], {}]
    cloneable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    plan = _preflight(cloneable.owner, workflow_id=RUN_WF, values=[])
    missing = [
        model
        for group in plan["groups"]
        for reason in group["reasons"]
        if reason["code"] == "missing_models"
        for model in reason["models"]
    ]
    assert {"file": _SHELF_FILENAME, "folder": "checkpoints"} in missing, plan
    body = cloneable.owner.get(
        f"{API}/workflows/{RUN_WF}/model-swap",
        params={"replacing": _SHELF_FILENAME},
    ).json()
    assert (body["replacements"], body["replacements_reason"]) == (
        [],
        "none_loadable",
    )


def test_no_replacement_is_offered_without_a_checkpoint_to_go_with(cloneable):
    graph = _with_support_loaders(cloneable.graph)
    graph["1"]["inputs"]["ckpt_name"] = "test-not-on-shelf.safetensors"
    cloneable.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (graph, []),
    )
    body = cloneable.owner.get(
        f"{API}/workflows/{RUN_WF}/model-swap",
        params={"replacing": "test-vae-fp8.safetensors"},
    ).json()
    assert (body["replacements"], body["replacements_reason"]) == (
        [],
        "no_checkpoint",
    )


def test_the_swap_options_refuse_a_checkpoint_id_that_is_not_one(cloneable):
    lora_id = cloneable.server.hub.fetchall(
        "SELECT id FROM model WHERE file_kind = 'adapter' LIMIT 1"
    )[0]["id"]
    r = cloneable.owner.get(
        f"{API}/workflows/{RUN_WF}/model-swap", params={"checkpoint_id": lora_id}
    )
    assert r.status_code == 404, r.text


def test_deleting_a_card_the_library_knows_from_its_pictures_is_refused(workflow_env):
    """Found workflows are hide-only, and the refusal says which gesture to use."""
    r = workflow_env.owner.delete(f"{API}/workflows/{BUSY_WF}")
    assert r.status_code == 409, r.text
    assert "hide" in r.json()["detail"].lower()
    # Nothing went: the card is still on the grid.
    assert workflow_env.owner.get(f"{API}/workflows/{BUSY_WF}").status_code == 200


def test_deleting_a_manual_workflow_leaves_the_automatic_one_and_its_file(
    runnable, tmp_path
):
    """A manual workflow is deletable; an automatic one is not, legacy file or no.

    The copy goes to the trash by way of the inbox and its rows go; the
    workflow it was made from, and a legacy file row on it, stay.
    """
    _isolate_workflow_folders(tmp_path, runnable.monkeypatch)
    (tmp_path / "imported.json").write_text(json.dumps(RUN_DOCUMENT))
    with runnable.server.hub.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_file "
            "(workflow_name, workflow_key, topology_hash, structural_hash) "
            "VALUES ('imported.json', ?, ?, ?)",
            (RUN_CARD, RUN_TOPOLOGY, RUN_RECIPE),
        )
    r = runnable.owner.delete(f"{API}/workflows/{RUN_WF}")
    assert r.status_code == 409, r.text
    assert (tmp_path / "imported.json").exists()

    copy = runnable.owner.post(f"{API}/workflows/{RUN_WF}/duplicate").json()
    manual = copy["workflow_id"]
    runnable.owner.patch(f"{API}/workflows/{manual}", json={"notes": "mine"})
    r = runnable.owner.delete(f"{API}/workflows/{manual}")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": copy["name"], "workflow_id": manual}
    assert runnable.owner.get(f"{API}/workflows/{manual}").status_code == 404
    assert runnable.owner.delete(f"{API}/workflows/{manual}").status_code == 404
    hub = runnable.server.hub
    for table in ("workflow_document", "workflow_group_attr"):
        assert (
            hub.fetchone(f"SELECT 1 FROM {table} WHERE workflow_id = ?", (manual,))
            is None
        ), table
    assert runnable.owner.get(f"{API}/workflows/{RUN_WF}").status_code == 200
    assert (tmp_path / "imported.json").exists()


# A graph with no LoRA loader at all: the state `no_lora_loader` names and the
# only one a loader can be spliced into. RUN_DOCUMENT already has one, and
# `plan_lora_insertion` refuses to stack a second.
LOADERLESS_DOCUMENT = {
    "1": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "realvisxl.safetensors"},
    },
    "2": {
        "class_type": "KSampler",
        "inputs": {"steps": 20, "cfg": 7.0, "seed": 1, "model": ["1", 0]},
    },
    "3": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "P", "images": ["2", 0]},
    },
}

LOADERLESS_OBJECT_INFO = {
    "CheckpointLoaderSimple": {
        "input": {"required": {"ckpt_name": [["realvisxl.safetensors"], {}]}},
        "output": ["MODEL", "CLIP", "VAE"],
    },
    "KSampler": {
        "input": {"required": {"seed": ["INT", {"default": 0}]}},
        "output": ["LATENT"],
    },
    "SaveImage": {
        "input": {"required": {"filename_prefix": ["STRING", {}]}},
        "output": [],
    },
    "LoraLoaderModelOnly": {
        "input": {
            "required": {
                "model": ["MODEL", {}],
                "lora_name": [["add_detail.safetensors"], {}],
                "strength_model": ["FLOAT", {"default": 1.0}],
            }
        },
        "output": ["MODEL"],
    },
}


@pytest.fixture
def loaderless(runnable, tmp_path):
    """RUN_CARD sourced from a graph with nowhere to put a LoRA, on this ComfyUI."""
    _isolate_workflow_folders(tmp_path, runnable.monkeypatch)
    runnable.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (
            json.loads(json.dumps(LOADERLESS_DOCUMENT)),
            [],
        ),
    )
    runnable.monkeypatch.setattr(
        workflows_routes,
        "_read_object_info",
        lambda url: (json.loads(json.dumps(LOADERLESS_OBJECT_INFO)), None),
    )
    return SimpleNamespace(tmp_path=tmp_path, **vars(runnable))


def test_inserting_a_lora_loader_writes_a_copy_with_a_slot_to_swap_into(loaderless):
    """The #1376 splice, kept: a new file whose LoRA slot is there to be filled."""
    r = loaderless.owner.post(f"{API}/workflows/{RUN_WF}/insert-lora-loader")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["class_type"] == "LoraLoaderModelOnly"
    written = _written(loaderless, body)
    loader = written[body["node_id"]]
    # ComfyUI's own widget default, the way dropping the node there would
    # leave it. The gesture adds the slot; which LoRA goes in it is a later
    # gesture, which is why the route says so and the client must too.
    assert loader["inputs"]["lora_name"] == "add_detail.safetensors"
    assert loader["inputs"]["strength_model"] == 1.0
    assert loader["inputs"]["model"] == ["1", 0]
    # The sampler now reads the loader rather than the checkpoint, or the run
    # would go through without the LoRA and say nothing.
    assert written["2"]["inputs"]["model"] == [body["node_id"], 0]


def _serve_the_adapter_on(fixture, object_info: dict) -> None:
    """Make *object_info*'s model-only loader list the run tests' shelf LoRA."""
    info = json.loads(json.dumps(object_info))
    options = info["LoraLoaderModelOnly"]["input"]["required"]["lora_name"][0]
    options.append(RUN_ADAPTER_FILENAME)
    fixture.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )


def test_a_run_adds_a_lora_in_a_loader_of_its_own(loaderless):
    """`add_loras` splices a loader in on the run's copy (Create with LoRA…).

    The workflow has no LoRA loader at all, which is the case a slot-addressed
    `loras` entry cannot serve; the stored file is not touched.
    """
    _serve_the_adapter_on(loaderless, LOADERLESS_OBJECT_INFO)
    r = loaderless.owner.post(
        f"{API}/workflows/run",
        json={
            "workflow_id": RUN_WF,
            "add_loras": [{"sha256": RUN_ADAPTER_DIGEST, "strength_model": 0.6}],
        },
    )
    assert r.status_code == 200, r.text
    graph = loaderless.submitted[0]["graph"]
    added = [
        node_id
        for node_id, node in graph.items()
        if node["class_type"] == "LoraLoaderModelOnly"
    ]
    assert len(added) == 1, graph
    inputs = graph[added[0]]["inputs"]
    assert inputs["lora_name"] == RUN_ADAPTER_FILENAME
    assert inputs["strength_model"] == 0.6
    assert inputs["model"] == ["1", 0]
    # The sampler reads the LoRA, or the run would go through without it.
    assert graph["2"]["inputs"]["model"] == [added[0], 0]


def test_an_added_lora_replaces_none_of_the_workflows_own(runnable):
    """The graph's own loader keeps its LoRA; the added one goes in beside it."""
    info = json.loads(json.dumps({**RUN_OBJECT_INFO, **LOADERLESS_OBJECT_INFO}))
    info["LoraLoader"] = json.loads(json.dumps(RUN_OBJECT_INFO["LoraLoader"]))
    info["CheckpointLoaderSimple"]["output"] = ["MODEL", "CLIP", "VAE"]
    info["KSampler"]["output"] = ["LATENT"]
    info["SaveImage"]["output"] = []
    # The graph's CLIP never reaches a text encoder, so the splice is a
    # model-only loader.
    info["LoraLoaderModelOnly"]["input"]["required"]["lora_name"] = [
        ["add_detail.safetensors", RUN_ADAPTER_FILENAME],
        {},
    ]
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={"workflow_id": RUN_WF, "add_loras": [{"sha256": RUN_ADAPTER_DIGEST}]},
    )
    assert r.status_code == 200, r.text
    assert runnable.submitted, [g["reasons"] for g in r.json()["groups"]]
    graph = runnable.submitted[0]["graph"]
    assert graph["2"]["inputs"]["lora_name"] == "add_detail.safetensors"
    loaders = [
        node["inputs"]["lora_name"]
        for node in graph.values()
        if node["class_type"] in ("LoraLoader", "LoraLoaderModelOnly")
    ]
    assert sorted(loaders) == ["add_detail.safetensors", RUN_ADAPTER_FILENAME]


def test_an_added_lora_the_graph_already_loads_is_not_added_twice(runnable):
    """Its strength is set on the loader that has it instead of a second copy."""
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={
            "workflow_id": RUN_WF,
            "loras": [{"node_id": "2", "sha256": RUN_ADAPTER_DIGEST}],
            "add_loras": [{"sha256": RUN_ADAPTER_DIGEST, "strength_model": 0.3}],
        },
    )
    assert r.status_code == 200, r.text
    graph = runnable.submitted[0]["graph"]
    assert [n for n in graph.values() if n["class_type"] == "LoraLoader"] == [
        graph["2"]
    ]
    assert graph["2"]["inputs"]["lora_name"] == RUN_ADAPTER_FILENAME
    assert graph["2"]["inputs"]["strength_model"] == 0.3


def test_a_lora_that_cannot_be_added_is_a_reason_with_the_splices_sentence(
    loaderless,
):
    """Not on this ComfyUI, and no ComfyUI-PixlStash to fetch it: said, not run."""
    payload = _preflight(
        loaderless.owner,
        workflow_id=RUN_WF,
        add_loras=[{"sha256": RUN_ADAPTER_DIGEST}],
    )
    reasons = [r for group in payload["groups"] for r in group["reasons"]]
    refused = [r for r in reasons if r["code"] == "lora_not_insertable"]
    assert refused, reasons
    assert "ComfyUI-PixlStash" in refused[0]["detail"]


def test_every_added_lora_that_cannot_be_added_says_so(loaderless):
    """One refusal per LoRA, not only the first: the rest are not hidden behind it."""
    payload = _preflight(
        loaderless.owner,
        workflow_id=RUN_WF,
        add_loras=[{"sha256": RUN_ADAPTER_DIGEST}, {"sha256": RUN_ADAPTER_DIGEST}],
    )
    refused = [
        reason
        for group in payload["groups"]
        for reason in group["reasons"]
        if reason["code"] == "lora_not_insertable"
    ]
    assert len(refused) == 2, payload


def test_an_added_lora_without_comfyui_is_the_unreachable_reason(loaderless):
    """Not a 400: the pre-flight's own refusal, which the popup offers a Retry for."""
    loaderless.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "refused")
    )
    payload = _preflight(
        loaderless.owner,
        workflow_id=RUN_WF,
        add_loras=[{"sha256": RUN_ADAPTER_DIGEST}],
    )
    # Refused by the pre-flight's own ComfyUI reason (this owner has no
    # address set), never by a 400 the popup would read as a refusal of the body.
    assert _reasons(payload) & {"comfyui_unreachable", "comfyui_not_configured"}
    r = loaderless.owner.post(
        f"{API}/workflows/run",
        json={
            "workflow_id": RUN_WF,
            "add_loras": [{"sha256": RUN_ADAPTER_DIGEST}],
            "allow_unchecked": True,
        },
    )
    # Consent to run unchecked is not consent to run without the LoRA.
    assert r.status_code == 400, r.text
    assert loaderless.submitted == []


# The ComfyUI-PixlStash LoRA loader as its node pack declares it: resolved by
# digest, so it loads a shelf LoRA this ComfyUI has no file of.
PIXLSTASH_ADAPTER_LOADER_INFO = {
    "input": {
        "required": {
            "model": ["MODEL", {}],
            "adapter_kind": [["— Any —"], {}],
            "base_model": [["— Any —"], {}],
            "adapter_sha256": ["STRING", {"default": ""}],
        },
        "optional": {
            "clip": ["CLIP", {}],
            "strength_model": ["FLOAT", {"default": 1.0}],
            "strength_clip": ["FLOAT", {"default": 1.0}],
        },
    },
    "output": ["MODEL", "CLIP", "STRING"],
}


def _graph_loads_a_shelf_lora_comfyui_lacks(runnable, with_pixlstash=True):
    """RUN_CARD's loader names the shelf LoRA's file, which ComfyUI does not list.

    The shelf row is renamed to the file the graph's loader names, so the
    shelf can name that slot's digest; ComfyUI's LoraLoader lists only the
    other file, so without a swap the #1463 bypass would take the loader out.
    """
    with runnable.server.hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET filename = 'add_detail.safetensors' WHERE sha256 = ?",
            (RUN_ADAPTER_DIGEST,),
        )
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["LoraLoader"]["input"]["required"]["lora_name"] = [
        ["unrelated.safetensors"],
        {},
    ]
    if with_pixlstash:
        info["PixlStashAdapterLoader"] = json.loads(
            json.dumps(PIXLSTASH_ADAPTER_LOADER_INFO)
        )
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )


def test_a_shelf_lora_comfyui_lacks_loads_through_the_pixlstash_loader(runnable):
    """The workflow's own recipe LoRA, on the shelf but not on this ComfyUI.

    Swapped to the digest loader rather than refused or bypassed: the LoRA is
    loaded, fetched by its hash.
    """
    _graph_loads_a_shelf_lora_comfyui_lacks(runnable)
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WF})
    assert r.status_code == 200, r.text
    group = r.json()["groups"][0]
    assert group["bypassed_loras"] == [], group["reasons"]
    assert runnable.submitted, group["reasons"]
    # Reported, so the popup can offer to save the fixed workflow.
    # The workflow's own recipe LoRA, so not `requested`: a saved copy keeps it.
    assert [
        (e["node_id"], e["sha256"], e["requested"]) for e in group["swapped_loaders"]
    ] == [("2", RUN_ADAPTER_DIGEST, False)]
    node = runnable.submitted[0]["graph"]["2"]
    assert node["class_type"] == "PixlStashAdapterLoader"
    assert node["inputs"]["adapter_sha256"] == RUN_ADAPTER_DIGEST
    # Wired where the core loader was, so the sampler still reads the LoRA.
    assert node["inputs"]["model"] == ["1", 0]
    assert runnable.submitted[0]["graph"]["3"]["inputs"]["model"] == ["2", 0]


def test_an_added_lora_in_a_loader_comfyui_cannot_fill_is_swapped_not_dropped(
    runnable,
):
    """Create with LoRA on a workflow that already names the person's LoRA.

    The loader holding it names a file this ComfyUI lacks; the added LoRA is
    loaded there through the digest loader, at the asked strength, and not a
    second time in a loader of its own.
    """
    _graph_loads_a_shelf_lora_comfyui_lacks(runnable)
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={
            "workflow_id": RUN_WF,
            "add_loras": [{"sha256": RUN_ADAPTER_DIGEST, "strength_model": 0.7}],
        },
    )
    assert r.status_code == 200, r.text
    graph = runnable.submitted[0]["graph"]
    loaders = [
        node_id
        for node_id, node in graph.items()
        if node["class_type"] in ("LoraLoader", "PixlStashAdapterLoader")
    ]
    assert loaders == ["2"], graph
    assert graph["2"]["class_type"] == "PixlStashAdapterLoader"
    assert graph["2"]["inputs"]["adapter_sha256"] == RUN_ADAPTER_DIGEST
    assert graph["2"]["inputs"]["strength_model"] == 0.7
    group = r.json()["groups"][0]
    assert group["bypassed_loras"] == []
    # The workflow's own recipe names this LoRA in this loader, so the swap
    # is the recipe's (`requested: false`) and the added LoRA only re-weights it.
    assert [e["requested"] for e in group["swapped_loaders"]] == [False]


def test_an_added_lora_is_reported_even_behind_another_refusal(runnable):
    """`add_loras` answers for itself when a `loras` entry has already refused.

    Here neither can be placed (ComfyUI lists neither file, and there is no
    ComfyUI-PixlStash or model-only loader to add one in): both say so, rather
    than the added LoRA going unmentioned behind the first refusal.
    """
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["LoraLoader"]["input"]["required"]["lora_name"] = [
        ["unrelated.safetensors"],
        {},
    ]
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    payload = _preflight(
        runnable.owner,
        workflow_id=RUN_WF,
        loras=[{"node_id": "2", "sha256": RUN_ADAPTER_DIGEST}],
        add_loras=[{"sha256": RUN_ADAPTER_DIGEST}],
    )
    assert {"missing_models", "lora_not_insertable"} <= _reasons(payload), payload


def test_a_swap_for_a_lora_the_request_named_is_marked_requested(runnable):
    """`loras` naming the slot is the form's choice, which a saved copy never keeps."""
    _graph_loads_a_shelf_lora_comfyui_lacks(runnable)
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={
            "workflow_id": RUN_WF,
            "loras": [{"node_id": "2", "sha256": RUN_ADAPTER_DIGEST}],
        },
    )
    assert r.status_code == 200, r.text
    group = r.json()["groups"][0]
    assert [(e["node_id"], e["requested"]) for e in group["swapped_loaders"]] == [
        ("2", True)
    ]


def test_without_the_pixlstash_loader_nothing_is_swapped(runnable):
    """No node pack to fetch it by hash: the recipe's LoRA is refused as before.

    `missing_models`, naming the file, and nothing submitted: the swap is only
    ever to a loader this ComfyUI actually has.
    """
    _graph_loads_a_shelf_lora_comfyui_lacks(runnable, with_pixlstash=False)
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WF})
    assert r.status_code == 200, r.text
    group = r.json()["groups"][0]
    assert {reason["code"] for reason in group["reasons"]} == {"missing_models"}
    assert runnable.submitted == []


def test_the_fixed_workflow_is_saved_with_the_loader_swapped(runnable, tmp_path):
    """Save fixed workflow: this ComfyUI's repair written into a NEW file.

    The LoRA this ComfyUI lacks loads through the digest loader in the copy,
    wired where the core loader was; the original is not the file written.
    """
    _isolate_workflow_folders(tmp_path, runnable.monkeypatch)
    _graph_loads_a_shelf_lora_comfyui_lacks(runnable)
    r = runnable.owner.post(f"{API}/workflows/{RUN_WF}/fixed-copy")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"].endswith("(fixed)"), body
    assert len(body["changes"]) == 1 and "ComfyUI-PixlStash" in body["changes"][0]
    written = _written(runnable, body)
    assert written["2"]["class_type"] == "PixlStashAdapterLoader"
    assert written["2"]["inputs"]["adapter_sha256"] == RUN_ADAPTER_DIGEST
    assert written["3"]["inputs"]["model"] == ["2", 0]


def test_a_workflow_with_nothing_to_fix_is_not_copied(runnable, tmp_path):
    """Every file here is on this ComfyUI: 409, and no file written."""
    _isolate_workflow_folders(tmp_path, runnable.monkeypatch)
    r = runnable.owner.post(f"{API}/workflows/{RUN_WF}/fixed-copy")
    assert r.status_code == 409, r.text
    assert _manual_ids(runnable) == []


def test_a_fixed_copy_needs_comfyui(runnable, tmp_path):
    """What needs fixing is what this ComfyUI lacks: no ComfyUI, no answer."""
    _isolate_workflow_folders(tmp_path, runnable.monkeypatch)
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "refused")
    )
    r = runnable.owner.post(f"{API}/workflows/{RUN_WF}/fixed-copy")
    assert r.status_code == 503, r.text


def test_inserting_a_loader_leaves_the_original_workflow_alone(loaderless):
    """The stored file is never rewritten: the loader goes into a NEW file.

    Asserts what it can observe — the original's bytes, and that the new file
    is a different file with one more node in it. The dedupe bypass this route
    also depends on is guarded where it IS observable, by
    ``test_duplicating_twice_puts_a_second_file_beside_the_first``, where the
    two documents are identical; here the spliced graph would not match the
    original anyway, so asserting it would prove nothing.
    """
    original = loaderless.tmp_path / "original.json"
    original.write_text(json.dumps(LOADERLESS_DOCUMENT))
    body = loaderless.owner.post(f"{API}/workflows/{RUN_WF}/insert-lora-loader").json()
    assert json.loads(original.read_text()) == LOADERLESS_DOCUMENT
    written = _written(loaderless, body)
    assert written != LOADERLESS_DOCUMENT
    assert len(written) == len(LOADERLESS_DOCUMENT) + 1


def test_inserting_a_loader_into_a_workflow_that_has_one_adds_it_after_the_source(
    chained,
):
    """A loader always goes in the MODEL path, whatever loaders are there already.

    Spliced right after the checkpoint, so the existing chain reads it. #1376
    refused this; the owner's rule since is that the MODEL path from the
    model source to the sampler always takes another LoRA.
    """
    r = chained.owner.post(f"{API}/workflows/{RUN_WF}/insert-lora-loader")
    assert r.status_code == 201, r.text
    body = r.json()
    written = _written(chained, body)
    new = body["node_id"]
    assert written[new]["inputs"]["model"] == ["1", 0]
    assert written["2"]["inputs"]["model"] == [new, 0]


def test_inserting_a_loader_without_comfyui_is_a_503_not_a_guess(runnable, tmp_path):
    """An API link carries no type, so with no `object_info` a reader could be missed."""
    _isolate_workflow_folders(tmp_path, runnable.monkeypatch)
    runnable.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (
            json.loads(json.dumps(LOADERLESS_DOCUMENT)),
            [],
        ),
    )
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "connection refused")
    )
    r = runnable.owner.post(f"{API}/workflows/{RUN_WF}/insert-lora-loader")
    assert r.status_code == 503, r.text
    assert _manual_ids(runnable) == [], "a workflow was stored anyway"


# --- the LoRA chain editor's routes (#1478) ---------------------------------
#
# Two loaders in a row between the checkpoint and both of its readers, typed
# the way a real ComfyUI types them. Node 2 loads the shelf LoRA the run tests
# already seed (`RUN_ADAPTER_FILENAME`); node 5 loads a file the shelf does not
# know, which is the row the editor has to flag rather than hide.
CHAIN_DOCUMENT = {
    "1": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "realvisxl.safetensors"},
    },
    "2": {
        "class_type": "LoraLoader",
        "inputs": {
            "lora_name": RUN_ADAPTER_FILENAME,
            "strength_model": 0.8,
            "strength_clip": 0.8,
            "model": ["1", 0],
            "clip": ["1", 1],
        },
    },
    "5": {
        "class_type": "LoraLoader",
        "inputs": {
            "lora_name": "Mystery_Style.safetensors",
            "strength_model": 0.5,
            "strength_clip": 0.5,
            "model": ["2", 0],
            "clip": ["2", 1],
        },
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "a cat", "clip": ["5", 1]},
    },
    "3": {
        "class_type": "KSampler",
        "inputs": {"seed": 1, "model": ["5", 0], "positive": ["6", 0]},
    },
    "4": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "P", "images": ["3", 0]},
    },
}

CHAIN_OBJECT_INFO = {
    "CheckpointLoaderSimple": {
        "input": {"required": {"ckpt_name": [["realvisxl.safetensors"], {}]}},
        "output": ["MODEL", "CLIP", "VAE"],
    },
    "LoraLoader": {
        "input": {
            "required": {
                "model": ["MODEL", {}],
                "clip": ["CLIP", {}],
                "lora_name": [
                    [RUN_ADAPTER_FILENAME, "Mystery_Style.safetensors"],
                    {},
                ],
                "strength_model": ["FLOAT", {"default": 1.0}],
                "strength_clip": ["FLOAT", {"default": 1.0}],
            }
        },
        "output": ["MODEL", "CLIP"],
    },
    "CLIPTextEncode": {
        "input": {"required": {"text": ["STRING", {}], "clip": ["CLIP", {}]}},
        "output": ["CONDITIONING"],
    },
    "KSampler": {
        "input": {
            "required": {
                "seed": ["INT", {"default": 0}],
                "model": ["MODEL", {}],
                "positive": ["CONDITIONING", {}],
            }
        },
        "output": ["LATENT"],
    },
    "SaveImage": {
        "input": {"required": {"filename_prefix": ["STRING", {}]}},
        "output": [],
    },
}


@pytest.fixture
def chained(runnable, tmp_path):
    """RUN_CARD sourced from CHAIN_DOCUMENT, on a ComfyUI that types every link."""
    _isolate_workflow_folders(tmp_path, runnable.monkeypatch)
    runnable.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (
            json.loads(json.dumps(CHAIN_DOCUMENT)),
            [],
        ),
    )
    runnable.monkeypatch.setattr(
        workflows_routes,
        "_read_object_info",
        lambda url, **_: (json.loads(json.dumps(CHAIN_OBJECT_INFO)), None),
    )
    return SimpleNamespace(tmp_path=tmp_path, **vars(runnable))


def _chain_edit(client, *entries, **extra):
    return client.put(
        f"{API}/workflows/{RUN_WF}/lora-chain",
        json={"entries": list(entries), **extra},
    )


def test_the_chain_is_read_in_apply_order_with_the_unknown_loader_flagged(chained):
    """The inspector's list: source to sink, and the one the shelf cannot name kept.

    Wrong if node 5 is missing (hidden) or listed before node 2 (the order a
    run applies them is the order the editor edits).
    """
    r = chained.owner.get(f"{API}/workflows/{RUN_WF}/lora-chain")
    assert r.status_code == 200, r.text
    chain = r.json()
    assert chain["editable"] is True, chain
    assert chain["refusal"] is None
    assert chain["source"]["node_id"] == "1"
    assert [loader["node_id"] for loader in chain["loaders"]] == ["2", "5"]
    known, unknown = chain["loaders"]
    assert (known["sha256"], known["on_shelf"]) == (RUN_ADAPTER_DIGEST, True)
    assert (unknown["sha256"], unknown["on_shelf"]) == (None, False)
    assert unknown["strength"] == 0.5
    # A CLIP source is there, so an Add would put in the loader that patches it.
    assert chain["added_loader_class"] == "LoraLoader"


def test_the_chain_is_still_shown_when_comfyui_is_down(chained):
    """Read-only, with the reason, rather than a 503 the inspector cannot draw."""
    chained.monkeypatch.setattr(
        workflows_routes,
        "_read_object_info",
        lambda url, **_: (None, "connection refused"),
    )
    r = chained.owner.get(f"{API}/workflows/{RUN_WF}/lora-chain")
    assert r.status_code == 200, r.text
    chain = r.json()
    assert chain["editable"] is False
    assert "ComfyUI" in chain["refusal"]
    assert [loader["node_id"] for loader in chain["loaders"]] == ["2", "5"]
    # Nothing typed the links, so nothing is named as reading the chain.
    assert chain["sink"]["summary"] is None


def _chain_document_with(**nodes):
    document = json.loads(json.dumps(CHAIN_DOCUMENT))
    document.update(nodes)
    return document


def _serve_chain(chained, document, info=None):
    chained.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (json.loads(json.dumps(document)), []),
    )
    if info is not None:
        chained.monkeypatch.setattr(
            workflows_routes,
            "_read_object_info",
            lambda url, **_: (json.loads(json.dumps(info)), None),
        )


def test_a_chain_refused_for_its_shape_still_names_both_ends(chained):
    """ComfyUI answered, so the read-only view says what the chain runs between.

    Refused because a second checkpoint feeds two more samplers, so there is
    no single chain for it. Wrong if the sink summary is None: that is the
    dialog's empty bottom node.
    """
    _serve_chain(
        chained,
        _chain_document_with(
            **{
                "8": {
                    "class_type": "CheckpointLoaderSimple",
                    "inputs": {"ckpt_name": "realvisxl.safetensors"},
                },
                "7": {
                    "class_type": "KSampler",
                    "inputs": {"seed": 1, "model": ["8", 0], "positive": ["6", 0]},
                },
                "9": {
                    "class_type": "KSampler",
                    "inputs": {"seed": 1, "model": ["8", 0], "positive": ["6", 0]},
                },
            }
        ),
    )
    r = chained.owner.get(f"{API}/workflows/{RUN_WF}/lora-chain")
    assert r.status_code == 200, r.text
    chain = r.json()
    assert chain["editable"] is False
    assert "loads 2 models" in chain["refusal"]
    assert chain["source"]["node_id"] == "1"
    assert chain["sink"]["summary"] == (
        "KSampler #3 reads model · CLIPTextEncode #6 reads clip"
    )


def _two_pass_chain(chained):
    """A second sampler pass, #7 "Hires pass", reads loader #2 before #5."""
    _serve_chain(
        chained,
        _chain_document_with(
            **{
                "7": {
                    "class_type": "KSampler",
                    "inputs": {"seed": 1, "model": ["2", 0], "positive": ["6", 0]},
                    "_meta": {"title": "Hires pass"},
                }
            }
        ),
    )


def test_a_fork_is_read_as_a_trunk_and_one_lane_per_pass(chained):
    """#2 is the trunk; #3 gets #5 on its own lane, #7 an empty one.

    Wrong if `editable` is false (a fork used to stop the chain at #2 and
    leave #5 to ComfyUI), or a lane is missing its sampler.
    """
    _two_pass_chain(chained)
    r = chained.owner.get(f"{API}/workflows/{RUN_WF}/lora-chain")
    assert r.status_code == 200, r.text
    chain = r.json()
    assert chain["editable"] is True, chain["refusal"]
    assert [loader["node_id"] for loader in chain["loaders"]] == ["2"]
    assert chain["branch_note"] is None
    lanes = chain["lanes"]
    assert [lane["sampler"] for lane in lanes] == [
        {"node_id": "3", "class_type": "KSampler", "title": None},
        {"node_id": "7", "class_type": "KSampler", "title": "Hires pass"},
    ]
    assert [[x["node_id"] for x in lane["loaders"]] for lane in lanes] == [["5"], []]
    # #5's CLIP feeds the prompt, so #3's lane adds a CLIP-carrying loader;
    # nothing on #7's side reads a CLIP.
    assert [lane["added_loader_class"] for lane in lanes] == ["LoraLoader", None]


def test_a_loader_moved_across_the_fork_is_written_to_both_passes(chained):
    _two_pass_chain(chained)
    r = _chain_edit(
        chained.owner,
        {"node_id": "2"},
        {"node_id": "5"},
        lanes=[[], []],
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["changes"][0]["text"] == (
        "#5 Mystery_Style moved before the fork: both passes get it"
    )
    written = _written(chained, body)
    assert written["3"]["inputs"]["model"] == ["5", 0]
    assert written["7"]["inputs"]["model"] == ["5", 0]
    assert written["6"]["inputs"]["clip"] == ["5", 1]


def test_the_loader_cap_counts_every_lane(chained):
    """32 loaders in all, not 32 per list: each one may cost a shelf lookup."""
    _two_pass_chain(chained)
    lane = [{"node_id": "5"}] * 17
    r = _chain_edit(chained.owner, {"node_id": "2"}, lanes=[lane, lane], dry_run=True)
    assert r.status_code == 422, r.text


def test_lanes_that_do_not_match_the_fork_are_a_409(chained):
    _two_pass_chain(chained)
    r = _chain_edit(chained.owner, {"node_id": "2"}, lanes=[[{"node_id": "5"}]])
    assert r.status_code == 409, r.text
    assert "goes 2 ways" in r.json()["detail"]


def test_a_character_prompt_builder_does_not_stop_a_lora_being_added(chained):
    """A node loading a LoRA its own way is an ordinary node, not a refusal.

    The MODEL path from the checkpoint to the sampler always takes another
    loader. Wrong if `editable` is false, or the new loader is not between
    the last loader and the sampler.
    """
    info = json.loads(json.dumps(CHAIN_OBJECT_INFO))
    info["LoRACharacterPromptBuilder"] = {
        "input": {"required": {"clip": ["CLIP", {}]}},
        "output": ["STRING"],
    }
    _serve_chain(
        chained,
        _chain_document_with(
            **{
                "68": {
                    "class_type": "LoRACharacterPromptBuilder",
                    "inputs": {"lora_name": "hero.safetensors", "clip": ["5", 1]},
                }
            }
        ),
        info,
    )
    r = chained.owner.get(f"{API}/workflows/{RUN_WF}/lora-chain")
    assert r.status_code == 200, r.text
    chain = r.json()
    assert chain["editable"] is True, chain["refusal"]
    assert [loader["node_id"] for loader in chain["loaders"]] == ["2", "5"]

    r = _chain_edit(
        chained.owner,
        {"node_id": "2", "strength": 0.8},
        {"node_id": "5", "strength": 0.5},
        {"sha256": RUN_ADAPTER_DIGEST, "strength": 1.0},
    )
    assert r.status_code == 201, r.text
    written = _written(chained, r.json())
    added = [
        node_id
        for node_id, node in written.items()
        if node_id not in CHAIN_DOCUMENT and node_id != "68"
    ]
    assert len(added) == 1, written
    assert written[added[0]]["inputs"]["model"] == ["5", 0]
    assert written["3"]["inputs"]["model"] == [added[0], 0]
    # The builder is left as it was, reading the chain's CLIP end.
    assert written["68"]["inputs"]["lora_name"] == "hero.safetensors"


def test_a_dry_run_lists_the_changes_and_writes_nothing(chained):
    r = _chain_edit(chained.owner, {"node_id": "2", "strength": 0.8}, dry_run=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["workflow_id"] is None
    assert ("deleted", "5") in {(c["kind"], c["node_id"]) for c in body["changes"]}
    assert _manual_ids(chained) == [], "a dry run wrote a file"


def test_deleting_a_loader_writes_a_new_file_whose_readers_skip_it(chained):
    """The whole point of the write: every MODEL and CLIP reader closes over the gap.

    The original is written to disk first so its bytes can be compared after.
    """
    original = chained.tmp_path / "original.json"
    original.write_text(json.dumps(CHAIN_DOCUMENT))
    r = _chain_edit(chained.owner, {"node_id": "2", "strength": 0.8})
    assert r.status_code == 201, r.text
    body = r.json()
    # A manual workflow of its own, never folded into the automatic one its
    # source is in.
    assert body["workflow_id"].startswith("manual:")
    assert "workflow_key" not in body
    written = _written(chained, body)
    assert "5" not in written, written
    assert written["3"]["inputs"]["model"] == ["2", 0]
    assert written["6"]["inputs"]["clip"] == ["2", 1]
    assert json.loads(original.read_text()) == CHAIN_DOCUMENT


def test_reordering_two_loaders_rewires_every_link_and_keeps_their_ids(chained):
    r = _chain_edit(
        chained.owner,
        {"node_id": "5", "strength": 0.5},
        {"node_id": "2", "strength": 0.8},
    )
    assert r.status_code == 201, r.text
    written = _written(chained, r.json())
    assert written["5"]["inputs"]["model"] == ["1", 0]
    assert written["5"]["inputs"]["clip"] == ["1", 1]
    assert written["2"]["inputs"]["model"] == ["5", 0]
    assert written["2"]["inputs"]["clip"] == ["5", 1]
    assert written["3"]["inputs"]["model"] == ["2", 0]
    assert written["6"]["inputs"]["clip"] == ["2", 1]


@pytest.mark.parametrize(
    "entries",
    [
        # Nothing changed.
        [{"node_id": "2", "strength": 0.8}, {"node_id": "5", "strength": 0.5}],
        # A loader this graph does not have.
        [{"node_id": "99", "strength": 1.0}],
        # A LoRA the shelf does not hold.
        [{"sha256": _h("not-on-the-shelf"), "strength": 1.0}],
    ],
    ids=["unchanged", "unknown-node", "not-on-shelf"],
)
def test_an_edit_that_cannot_be_made_is_a_409_and_writes_nothing(chained, entries):
    r = _chain_edit(chained.owner, *entries)
    assert r.status_code == 409, r.text
    assert _manual_ids(chained) == [], "a refused edit wrote a file"


def test_an_edit_without_comfyui_is_a_503_not_a_guess(chained):
    chained.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "connection refused")
    )
    r = _chain_edit(chained.owner, {"node_id": "2", "strength": 0.8})
    assert r.status_code == 503, r.text
    assert _manual_ids(chained) == []


def test_an_edit_never_changes_the_linked_workflow_file(chained):
    """A card with a FILE behind it: the edit writes beside it, never into it.

    The file differs from the picture's graph (seed 42), so the written copy
    proves which one the edit started from, and the original's bytes are
    compared after.
    """
    original = chained.tmp_path / "original.json"
    authored = json.loads(json.dumps(CHAIN_DOCUMENT))
    authored["3"]["inputs"]["seed"] = 42
    original.write_text(json.dumps(authored))
    before = original.read_bytes()
    chained.monkeypatch.setattr(
        workflows_routes,
        "_resolve_workflow_path",
        lambda name: (str(original), "user"),
    )
    with chained.server.hub.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_file "
            "(workflow_name, workflow_key, topology_hash, structural_hash) "
            "VALUES (?, ?, ?, ?)",
            ("original.json", RUN_CARD, RUN_TOPOLOGY, RUN_RECIPE),
        )
    try:
        r = _chain_edit(chained.owner, {"node_id": "2", "strength": 0.8})
        assert r.status_code == 201, r.text
        written = _written(chained, r.json())
        assert written["3"]["inputs"]["seed"] == 42, "not edited from the file"
        assert "5" not in written
        assert original.read_bytes() == before
    finally:
        with chained.server.hub.transaction() as conn:
            conn.execute(
                "DELETE FROM workflow_file WHERE workflow_name = 'original.json'"
            )


# --- skipping a LoRA for one run (#1478) ------------------------------------


def test_a_skipped_lora_leaves_this_runs_graph_and_nothing_else(runnable):
    """The Run popup's Skip: the loader is bypassed on the submitted copy only.

    The LoRA IS on this ComfyUI, so nothing but the request takes it out.
    """
    body = {
        "workflow_id": RUN_WF,
        "skip_loras": [{"node_id": "2", "field": "lora_name"}],
    }
    payload = _preflight(runnable.owner, **body)
    assert _reasons(payload) == set(), payload
    skipped = payload["groups"][0]["bypassed_loras"]
    assert [(s["node_id"], s["requested"]) for s in skipped] == [("2", True)]

    r = runnable.owner.post(f"{API}/workflows/run", json=body)
    assert r.status_code == 200, r.text
    graph = runnable.submitted[0]["graph"]
    assert "2" not in graph, graph
    assert graph["3"]["inputs"]["model"] == ["1", 0], graph


def test_the_same_run_without_a_skip_keeps_the_loader(runnable):
    """The positive control for the skip above: the loader is there by default."""
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WF})
    assert r.status_code == 200, r.text
    assert "2" in runnable.submitted[0]["graph"]
    assert r.json()["groups"][0]["bypassed_loras"] == []


def test_skipping_a_slot_no_graph_has_is_a_400(runnable):
    r = runnable.owner.post(
        f"{API}/workflows/run/preflight",
        json={"workflow_id": RUN_WF, "skip_loras": [{"node_id": "99"}]},
    )
    assert r.status_code == 400, r.text
    assert "99" in r.json()["detail"]


def test_a_slot_both_filled_and_skipped_is_refused(runnable):
    r = runnable.owner.post(
        f"{API}/workflows/run/preflight",
        json={
            "workflow_id": RUN_WF,
            "loras": [{"node_id": "2", "sha256": RUN_ADAPTER_DIGEST}],
            "skip_loras": [{"node_id": "2"}],
        },
    )
    assert r.status_code == 422, r.text


# --- switching a stage off for one run (#1621) ------------------------------


def _upscaled_run(runnable, monkeypatch, object_info) -> None:
    """RUN_CARD's graph with an ImageScaleBy between the sampler and the save."""
    embedded = json.loads(json.dumps(RUN_DOCUMENT))
    embedded["1"]["inputs"]["ckpt_name"] = "realvisxl.safetensors"
    embedded["2"]["inputs"]["lora_name"] = "add_detail.safetensors"
    embedded["5"] = {
        "class_type": "ImageScaleBy",
        "inputs": {"image": ["3", 0], "scale_by": 2},
    }
    embedded["4"]["inputs"]["images"] = ["5", 0]
    monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, picture_id, object_info=None: (
            json.loads(json.dumps(embedded)),
            [],
        ),
    )
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["ImageScaleBy"] = {
        "input": {"required": {"image": ["IMAGE"], "scale_by": ["FLOAT", {}]}},
        "output": ["IMAGE"],
    }
    monkeypatch.setattr(
        workflows_routes,
        "_read_object_info",
        lambda url: (info, None) if object_info else (None, "unreachable"),
    )


def test_a_skipped_upscale_leaves_this_runs_graph(runnable, monkeypatch):
    _upscaled_run(runnable, monkeypatch, object_info=True)
    body = {"workflow_id": RUN_WF, "skip_stages": ["upscale"]}
    r = runnable.owner.post(f"{API}/workflows/run", json=body)
    assert r.status_code == 200, r.text
    graph = runnable.submitted[0]["graph"]
    assert "5" not in graph, graph
    assert graph["4"]["inputs"]["images"] == ["3", 0]


def test_the_same_run_without_skip_stages_keeps_the_upscale(runnable, monkeypatch):
    _upscaled_run(runnable, monkeypatch, object_info=True)
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_id": RUN_WF})
    assert r.status_code == 200, r.text
    assert runnable.submitted[0]["graph"]["4"]["inputs"]["images"] == ["5", 0]


def test_a_stage_that_cannot_be_skipped_never_runs_whole(runnable, monkeypatch):
    """Consent to an unchecked ComfyUI does not reach a refused stage."""
    _upscaled_run(runnable, monkeypatch, object_info=False)
    body = {
        "workflow_id": RUN_WF,
        "skip_stages": ["upscale"],
        "allow_unchecked": True,
    }
    r = runnable.owner.post(f"{API}/workflows/run", json=body)
    assert r.status_code == 200, r.text
    assert "stage_not_skippable" in _reasons(r.json()), r.json()
    assert runnable.submitted == []


def test_an_unknown_stage_is_a_422(runnable):
    r = runnable.owner.post(
        f"{API}/workflows/run/preflight",
        json={"workflow_id": RUN_WF, "skip_stages": ["lora"]},
    )
    assert r.status_code == 422, r.text


def test_a_forgotten_loras_loader_is_skipped_when_asked():
    """The owner's request is the consent the automatic bypass does without."""
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
        "2": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": FORGOTTEN_MODEL, "model": ["1", 0]},
        },
        "3": {"class_type": "KSampler", "inputs": {"model": ["2", 0]}},
    }
    skipped, reasons, found = skip_requested_loras(
        graph, [("2", "lora_name")], json.loads(json.dumps(RUN_OBJECT_INFO))
    )
    assert reasons == []
    assert found == {("2", "lora_name")}
    assert [s["requested"] for s in skipped] == [True]
    assert "2" not in graph
    assert graph["3"]["inputs"]["model"] == ["1", 0]


def test_a_stacker_slot_whose_neighbour_is_live_is_not_skipped():
    """Skipping the node would skip the LoRA the owner did not name."""
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
        "2": {
            "class_type": "LoraStack",
            "inputs": {
                "lora_name_1": "a.safetensors",
                "lora_name_2": "b.safetensors",
                "model": ["1", 0],
            },
        },
    }
    skipped, reasons, _ = skip_requested_loras(
        graph, [("2", "lora_name_1")], json.loads(json.dumps(RUN_OBJECT_INFO))
    )
    assert skipped == []
    assert [r.code for r in reasons] == ["lora_not_skippable"]
    assert "b.safetensors" in reasons[0].as_dict()["message"]
    assert "2" in graph, "the stacker was taken out anyway"


def test_a_skip_is_not_undone_by_the_saved_recipes_loras(chained):
    """The recipe LoRA a skipped loader held does not move on to the next one.

    Placed after the skip, `other` (on the shelf, and in skipped node 2) fell
    through to the positional fill and replaced node 5's LoRA, which the owner
    had not named, while the notice still said node 2 was skipped.
    """
    r = chained.owner.post(
        f"{API}/recipes",
        json={
            "name": "skip keeps its word",
            "workflow_id": RUN_WF,
            "prompt": "a cat",
            "loras": [
                {
                    "filename": RUN_ADAPTER_FILENAME,
                    "sha256": RUN_ADAPTER_DIGEST,
                    "strength": 0.7,
                }
            ],
        },
    )
    assert r.status_code in {200, 201}, r.text
    run = chained.owner.post(
        f"{API}/workflows/run",
        json={"saved_recipe_id": r.json()["id"], "skip_loras": [{"node_id": "2"}]},
    )
    assert run.status_code == 200, run.text
    graph = chained.submitted[0]["graph"]
    assert "2" not in graph, graph
    # The recipe's own LoRA went with the skipped loader and nowhere else: no
    # loader the owner did not name picked it up.
    assert all(
        node.get("inputs", {}).get("lora_name") != RUN_ADAPTER_FILENAME
        for node in graph.values()
    ), graph


def test_a_skip_on_a_run_of_several_workflows_is_refused(runnable):
    """A node id means one loader on one graph; across cards it could be any."""
    second = _seed_second_runnable_card(runnable.server)
    r = runnable.owner.post(
        f"{API}/workflows/run/preflight",
        json={
            "picture_ids": [runnable.picture_id, second],
            "skip_loras": [{"node_id": "2"}],
        },
    )
    assert r.status_code == 400, r.text
    assert "several" in r.json()["detail"]
    assert runnable.submitted == []


# --- a saved recipe's LoRAs, placed by what they are (#1478) ----------------


def _slot(node_id, value):
    return {"node_id": node_id, "field": "lora_name", "value": value, "by": "filename"}


def test_a_recipe_stored_in_the_other_order_still_reaches_its_own_loaders():
    """The positional zip put these on each other's loaders.

    The graph and the recipe spell the files differently, so only the digest
    can tell which loader is which: a basename match or the positional fill
    would pass here by luck if the names agreed.
    """
    slots = [_slot("2", "sub/A-v2.safetensors"), _slot("5", "sub/B-v2.safetensors")]
    recipe = [
        {"filename": "b_renamed.safetensors", "sha256": _h("b"), "strength": 0.4},
        {"filename": "a_renamed.safetensors", "sha256": _h("a"), "strength": 0.9},
    ]
    digests = {("2", "lora_name"): _h("a"), ("5", "lora_name"): _h("b")}
    placements, unplaced = place_recipe_loras(slots, recipe, digests)
    assert unplaced == []
    assert {slot["node_id"]: saved["sha256"] for slot, saved in placements} == {
        "2": _h("a"),
        "5": _h("b"),
    }


def test_a_recipe_with_more_loras_than_loaders_reports_the_one_left_over():
    slots = [_slot("2", "a.safetensors"), _slot("5", "b.safetensors")]
    recipe = [
        {"filename": "a.safetensors", "sha256": _h("a"), "strength": 1.0},
        {"filename": "b.safetensors", "sha256": _h("b"), "strength": 1.0},
        {"filename": "c.safetensors", "sha256": _h("c"), "strength": 1.0},
    ]
    placements, unplaced = place_recipe_loras(slots, recipe, {})
    assert len(placements) == 2
    assert [u["filename"] for u in unplaced] == ["c.safetensors"]
    assert "2 LoRA loaders" in unplaced[0]["reason"]


def test_a_recipe_lora_the_shelf_cannot_name_is_reported_not_filtered():
    slots = [_slot("2", "Mystery_Style.safetensors")]
    recipe = [{"filename": "Mystery_Style.safetensors", "sha256": "", "strength": 1.0}]
    placements, unplaced = place_recipe_loras(slots, recipe, {})
    assert placements == []
    assert [(u["filename"], u["sha256"]) for u in unplaced] == [
        ("Mystery_Style.safetensors", None)
    ]
    assert "cannot identify" in unplaced[0]["reason"]


# --- the shapes one hand-written graph never asks about ---------------------
#
# Every test above runs against a single-sampler graph whose prompts sit in a
# plain `CLIPTextEncode`. An adversarial pass found four leaks that shape
# cannot see, each of which published the owner's own writing. They are
# asserted here against `scrub_for_export` directly rather than through the
# route: the route adds a card, a source tier and a shelf, none of which is
# what these are about, and the leak is in the scrub.

LEAKED = "a portrait of SOMEONE-REAL"


def _sdxl_graph() -> dict:
    """An SDXL graph: its prompt widgets are `text_g` and `text_l`."""
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "base.safetensors"},
        },
        "2": {
            "class_type": "CLIPTextEncodeSDXL",
            "inputs": {"text_g": LEAKED, "text_l": LEAKED, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "blurry", "clip": ["1", 1]},
        },
        "4": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 7,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
            },
        },
        "5": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "x", "images": ["4", 0]},
        },
    }


def test_the_sdxl_encoders_own_prompt_widgets_are_blanked():
    """`text_g` / `text_l`, which no run-time binding names.

    The bug this replaces reported `"prompts"` in `removed` — because the
    plain negative encoder beside it WAS blanked — while writing the positive
    prompt into the file. A scrub that says it removed the prompt and did not
    is worse than one that never claimed to.
    """
    exported, removed = scrub_for_export(_sdxl_graph())
    assert exported["2"]["inputs"]["text_g"] == ""
    assert exported["2"]["inputs"]["text_l"] == ""
    assert LEAKED not in json.dumps(exported)
    assert "prompts" in removed


def test_two_samplers_reading_different_prompts_are_still_blanked():
    """`detect_workflow_io` reports NO prompt node here, which is the trap.

    A hires-fix graph — two samplers, two positive prompts — makes the detector
    return an ambiguity and an empty prompt set. Blanking by widget name is
    what makes that case identical to the simple one.
    """
    graph = _sdxl_graph()
    graph["2"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": LEAKED, "clip": ["1", 1]},
    }
    graph["6"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": f"second pass, {LEAKED}", "clip": ["1", 1]},
    }
    graph["7"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": 9,
            "model": ["1", 0],
            "positive": ["6", 0],
            "negative": ["3", 0],
        },
    }
    assert detect_workflow_io(graph).positive_prompts == (), (
        "the detector must still find nothing here, or this test has stopped "
        "asking its question"
    )
    exported, removed = scrub_for_export(graph)
    assert LEAKED not in json.dumps(exported)
    assert "prompts" in removed


def test_a_lora_named_by_digest_is_a_lora_slot_too():
    """The ComfyUI-PixlStash loaders name their adapter in `lora_sha256`.

    `unvouched` says nothing about it — the owner HAS that LoRA, so the shelf
    vouches for the digest — and a digest identifies a model on a public
    registry as surely as a filename does. Only the slot rule can take it out,
    and it only can if it knows both spellings (#1416's lesson).
    """
    digest = "a" * 64
    graph = _sdxl_graph()
    graph["8"] = {
        "class_type": "PixlStashAdapterLoader",
        "inputs": {"lora_sha256": digest, "lora_sha256_2": digest, "model": ["1", 0]},
    }
    exported, removed = scrub_for_export(graph)
    assert exported["8"]["inputs"]["lora_sha256"] == ""
    assert exported["8"]["inputs"]["lora_sha256_2"] == ""
    assert "LoRA slots that are part of the look" in removed


def test_a_model_the_shelf_vouches_for_travels_without_its_folder():
    """ComfyUI files models in folders, and a person names a folder.

    The shelf check reads the basename, so `characters/<a person>/base.safetensors`
    is vouched for on the strength of `base.safetensors` alone. Emitting what
    was judged is the only honest answer; the recipient's ComfyUI has its own
    layout regardless.
    """
    graph = _sdxl_graph()
    graph["1"]["inputs"]["ckpt_name"] = "characters/someone/Base.safetensors"
    exported, removed = scrub_for_export(graph)
    assert exported["1"]["inputs"]["ckpt_name"] == "Base.safetensors"
    assert "the folders your models are filed in" in removed


def test_a_seed_kept_as_a_string_is_nulled_like_any_other():
    """The reducer judges a seed on the widget's name; so does this."""
    graph = _sdxl_graph()
    graph["4"]["inputs"]["seed"] = "987654321"
    exported, removed = scrub_for_export(graph)
    assert exported["4"]["inputs"]["seed"] == 0
    assert "seeds" in removed


def test_prose_nested_in_a_list_or_a_dict_is_blanked_and_a_link_is_not():
    """A list is not always a wire, and `is_link` is what tells them apart."""
    graph = _sdxl_graph()
    graph["2"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": [LEAKED, {"prompt": LEAKED}], "clip": ["1", 1]},
    }
    exported, removed = scrub_for_export(graph)
    assert LEAKED not in json.dumps(exported)
    assert "prompts" in removed
    # The wire is untouched, or the graph no longer runs anywhere.
    assert exported["2"]["inputs"]["clip"] == ["1", 1]
    assert exported["4"]["inputs"]["model"] == ["1", 0]


def test_a_shelf_row_id_does_not_leave_the_machine():
    """`checkpoint_id` names a row in this machine's database and nothing else."""
    graph = _sdxl_graph()
    graph["1"]["inputs"]["checkpoint_id"] = "412"
    exported, removed = scrub_for_export(graph)
    assert exported["1"]["inputs"]["checkpoint_id"] == ""
    assert "model names this machine does not hold" in removed


def test_the_forgotten_model_sentinel_never_reaches_the_file():
    """A source resolved from a stored instance carries it where a name was."""
    graph = _sdxl_graph()
    graph["1"]["inputs"]["ckpt_name"] = FORGOTTEN_MODEL
    exported, removed = scrub_for_export(graph)
    assert exported["1"]["inputs"]["ckpt_name"] == ""
    assert "model names this machine does not hold" in removed


def test_an_export_download_name_is_cleaned_for_the_client_that_writes_it():
    """The client writes the file, so the cleaning must hold on ITS platform.

    `os.path.basename` on Linux leaves a Windows separator alone, which is the
    one traversal it would have been reached for.
    """
    assert download_name("..\\..\\evil") == "evil.json"
    assert download_name("../../evil") == "evil.json"
    assert download_name(None) == "recipe.json"
    assert download_name("a\tb") == "a b.json"
    # Bounded in BYTES, with room for the ".json" — see
    # `test_a_download_name_is_bounded_in_bytes_not_characters` for why the
    # unit matters and for the non-Latin case this ASCII one cannot show.
    assert len(download_name("x" * 400).encode("utf-8")) <= 205


# --- what the backend review found the scrub still published ----------------


def test_a_prompt_wired_in_from_a_string_primitive_is_blanked():
    """`PrimitiveStringMultiline` hands its prompt on in a `value` widget.

    `carries_prose` cannot be widened to cover it: that rule is the REDUCER's
    too, and a `value` widget feeding a LoadImage its filename is a topology
    asset there, so calling it prose would re-key every workflow built that
    way. The export carries the extra rule instead.
    """
    graph = _sdxl_graph()
    graph["9"] = {"class_type": "PrimitiveStringMultiline", "inputs": {"value": LEAKED}}
    graph["10"] = {"class_type": "String Literal", "inputs": {"string": "catgirl"}}
    graph["2"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": ["9", 0], "clip": ["1", 1]},
    }
    exported, removed = scrub_for_export(graph)
    assert exported["9"]["inputs"]["value"] == ""
    # A ONE-WORD prompt too: the length and whitespace backstops both miss it,
    # so this is the assertion that needs the class list.
    assert exported["10"]["inputs"]["string"] == ""
    assert LEAKED not in json.dumps(exported)
    assert "prompts" in removed


def test_a_sentence_in_a_widget_no_rule_names_is_still_blanked():
    """The reducer's own backstop: that is not a filename, so a person wrote it."""
    graph = _sdxl_graph()
    graph["9"] = {
        "class_type": "SomeCustomNode",
        "inputs": {"notes": LEAKED, "long_one": "x" * 300},
    }
    exported, removed = scrub_for_export(graph)
    assert exported["9"]["inputs"]["notes"] == ""
    assert exported["9"]["inputs"]["long_one"] == ""
    assert "prompts" in removed


def test_the_enum_tokens_that_make_a_file_loadable_are_left_alone():
    """The positive control for the rule above: over-blanking is a regression.

    A sampler name, a scheduler and an upscale method are what ComfyUI matches
    against its own combo lists. Blank them and the export opens to a graph
    nobody can run, which is not a safer export.
    """
    graph = _sdxl_graph()
    graph["4"]["inputs"].update(
        {"sampler_name": "dpmpp_2m_sde_gpu", "scheduler": "karras"}
    )
    graph["9"] = {
        "class_type": "UpscaleModelLoader",
        # A model filename WITH SPACES, which is ordinary — 5,066 real ones in
        # this library have them — so the filename test has to answer first.
        "inputs": {
            "model_name": "4x Ultra Sharp.pth",
            "upscale_method": "nearest-exact",
        },
    }
    exported, _removed = scrub_for_export(graph)
    assert exported["4"]["inputs"]["sampler_name"] == "dpmpp_2m_sde_gpu"
    assert exported["4"]["inputs"]["scheduler"] == "karras"
    assert exported["9"]["inputs"]["upscale_method"] == "nearest-exact"
    assert exported["9"]["inputs"]["model_name"] == "4x Ultra Sharp.pth"


def test_a_credential_in_a_widget_never_leaves_the_machine():
    """`SECRET_FIELD_RE` drops these from a stored document; a file given away
    is the stronger case, and the two tiers this route resolves from most
    often are raw ComfyUI output the reducer never touched."""
    graph = _sdxl_graph()
    graph["9"] = {
        "class_type": "SomeUploader",
        "inputs": {
            "api_key": "example-not-a-real-key",
            "auth_token": "example-token",
            "password": "placeholder-pw",
        },
    }
    exported, removed = scrub_for_export(graph)
    assert list(exported["9"]["inputs"].values()) == ["", "", ""]
    assert "values in fields named like a key or a password" in removed


def test_a_dict_widget_is_scrubbed_by_its_own_key_not_its_parents():
    """What `workflow_hash`'s nested-asset walk does, and for the same reason.

    A list is positional and inherits the widget's meaning; a dict key is a
    name and may mean something else entirely.
    """
    graph = _sdxl_graph()
    # A ONE-WORD prompt, deliberately: with whitespace in it the backstop
    # blanks it whatever name the recursion carried, and this test would pass
    # while saying nothing about the key.
    graph["9"] = {
        "class_type": "SomeCustomNode",
        "inputs": {"config": {"positive_prompt": "catgirl", "steps": 30}},
    }
    exported, removed = scrub_for_export(graph)
    assert exported["9"]["inputs"]["config"]["positive_prompt"] == ""
    assert exported["9"]["inputs"]["config"]["steps"] == 30
    assert "prompts" in removed
    # A list is positional and DOES inherit its widget's meaning, which is the
    # other half of the same rule.
    graph["10"] = {"class_type": "CLIPTextEncode", "inputs": {"text": ["catgirl"]}}
    exported, _removed = scrub_for_export(graph)
    assert exported["10"]["inputs"]["text"] == [""]


def test_comfyuis_own_default_node_title_is_not_reported_as_something_removed():
    """`removed` is the only list the dialog renders, so it must mean something.

    ComfyUI writes `_meta: {"title": "<class name>"}` on almost every node, so
    reporting those would put "node titles" on nearly every export and tell the
    owner nothing. The `_meta` block still goes either way.
    """
    graph = _sdxl_graph()
    graph["4"]["_meta"] = {"title": "KSampler"}
    exported, removed = scrub_for_export(graph)
    assert "_meta" not in exported["4"]
    assert "node titles" not in removed
    # A title a person actually wrote is reported.
    graph["4"]["_meta"] = {"title": "her second pass"}
    _exported, removed = scrub_for_export(graph)
    assert "node titles" in removed


def test_a_download_name_is_bounded_in_bytes_not_characters():
    """A filesystem counts bytes: 100 CJK characters are 300 of them.

    Past ext4's 255-byte component limit, `store_workflow_copy` raises OSError
    and `POST /duplicate` can only answer 500 — for that card, for good.
    """
    stem = download_stem("人" * 200)
    assert len(stem.encode("utf-8")) <= 200
    assert stem, "a long non-Latin name must still produce a usable stem"
    # Not truncated mid-character.
    stem.encode("utf-8").decode("utf-8")


def test_a_graph_too_deeply_nested_to_walk_is_refused_not_a_500(runnable, monkeypatch):
    """An embedded graph arrived from outside, so its depth is not ours to trust.

    `deepcopy` and the scrub's own recursion both raise `RecursionError` on
    one, and the honest answer is the same 409 an unreadable graph gets —
    `_store_workflow` and `_trash_stored_workflow` already name this class too.
    """
    nested: list = []
    cursor = nested
    for _ in range(6000):
        deeper: list = []
        cursor.append(deeper)
        cursor = deeper
    monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid, object_info=None: (
            {"1": {"class_type": "KSampler", "inputs": {"whatever": nested}}},
            [],
        ),
    )
    r = runnable.owner.get(f"{API}/workflows/{RUN_WF}/export")
    assert r.status_code == 409, r.text


def test_a_comfyui_with_no_lora_files_refuses_the_insert_rather_than_writing_one(
    loaderless,
):
    """`_widget_defaults` yields no `lora_name` when the combo is empty.

    The file would be written, answered 201 and then refused by ComfyUI on a
    missing required input — after the owner was told it was ready to pick a
    LoRA in. The adapter path has always made this check; the empty-slot path
    did not.
    """
    empty = json.loads(json.dumps(LOADERLESS_OBJECT_INFO))
    empty["LoraLoaderModelOnly"]["input"]["required"]["lora_name"] = [[], {}]
    loaderless.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (empty, None)
    )
    r = loaderless.owner.post(f"{API}/workflows/{RUN_WF}/insert-lora-loader")
    assert r.status_code == 409, r.text
    assert "which LoRA files" in r.json()["detail"]
    assert _manual_ids(loaderless) == [], "a file was written anyway"
