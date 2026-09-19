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
import logging
import os
import sqlite3
import tempfile
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import delete, select

from pixlstash import auth
from pixlstash.authz.policy import AccessPolicy
from pixlstash.authz.registry import ROUTE_POLICIES
from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, ReferenceFolder
from pixlstash.db_models.saved_recipe import SavedRecipe
from pixlstash.event_types import EventType
from pixlstash.hub.workflow_card_reads import (
    AUTO_STACK_PREFIX,
    default_overrides,
    instance_documents,
    variant_documents,
)
from pixlstash.hub.workflow_card_writes import set_stack_order
from pixlstash.hub.workflow_cards import CORE_RULE_VERSION, effective_stack_keys
from pixlstash.hub.workflows import PictureGhost, get_document, record_picture_ghosts
from pixlstash.services.workflow_hash import (
    WorkflowGraphError,
    asset_reference,
    structural_document,
)
from pixlstash.services import workflow_card_service
import pixlstash.routes.workflows as workflows_routes
from pixlstash.routes.comfyui import MAX_RUNS_PER_REQUEST
from pixlstash.routes.workflows import RunRequest, UNNAMED_CARD
from pixlstash.services.workflow_run_service import FORGOTTEN_MODEL
from pixlstash.services.workflow_identity import (
    WORKFLOW_KEY_VERSION,
    guess_mark,
    slots,
    topology_node_labels,
    workflow_key,
)
from pixlstash.services.workflow_export import (
    download_name,
    scrub_for_export,
)
from pixlstash.services.workflow_io import detect_workflow_io
import pixlstash.routes.comfyui as comfyui_module
from pixlstash.services import saved_recipe_service, workflow_bindings, workflow_inbox
from pixlstash.server import Server
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
    ("GET", "/api/v1/workflows/{topology_hash}/variants"),
    ("GET", "/api/v1/workflows/{topology_hash}/pictures"),
    ("GET", "/api/v1/workflows/recipes/{structural_hash}/graph"),
    ("GET", "/api/v1/workflows/cards"),
    ("GET", "/api/v1/workflows/cards/{workflow_key}"),
    ("GET", "/api/v1/workflows/cards/{workflow_key}/pictures"),
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
    ("GET", "/api/v1/workflows/{workflow_key}/export"),
)

# The card and stack writes (v1.12 B4), pinned in their own tuple: the reads
# above are owner-only over a disclosure judgement, these are owner-only
# because they are the owner rearranging their own library and no narrower
# scope could describe one.
_WORKFLOW_WRITE_ROUTES = (
    ("PATCH", "/api/v1/workflows/{workflow_key}"),
    ("PUT", "/api/v1/workflows/{workflow_key}/slots"),
    ("PUT", "/api/v1/workflows/{workflow_key}/defaults"),
    ("PUT", "/api/v1/workflows/{workflow_key}/pins"),
    ("PUT", "/api/v1/workflows/{workflow_key}/inputs"),
    ("POST", "/api/v1/workflows/{workflow_key}/unstack"),
    ("POST", "/api/v1/workflows/stacks"),
    ("PUT", "/api/v1/workflows/stacks/{stack_id}/order"),
    ("POST", "/api/v1/workflows/stacks/{stack_id}/unstack"),
    # The run route and its dry run (v1.12 B7). OWNER_ONLY, which NARROWS the
    # picture-scoped run routes in comfyui.py: those replay one named
    # picture's own graph, this resolves one from the whole library.
    ("POST", "/api/v1/workflows/run"),
    ("POST", "/api/v1/workflows/run/preflight"),
    # The file gestures (v1.12 B8). Each resolves the card's graph out of the
    # whole library the way the run route does, and two of them write a file.
    ("POST", "/api/v1/workflows/{workflow_key}/duplicate"),
    ("POST", "/api/v1/workflows/{workflow_key}/insert-lora-loader"),
    ("DELETE", "/api/v1/workflows/{workflow_key}"),
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
LONE_CORE = _h("lonecore")
HIDDEN_CORE = _h("hiddencore")

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
    (FORGOTTEN_TOPOLOGY, SHARED_CORE, "img2img", FORGOTTEN_RECIPE),
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
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _seed_hub(server) -> None:
    """Write the four workflow tables from scratch."""
    with server.hub.transaction() as conn:
        # Children before parents: the hub enforces foreign keys, so a leftover
        # row aborts the wipe rather than lingering.
        conn.execute("DELETE FROM workflow_recipe_asset")
        conn.execute("DELETE FROM workflow_recipe_graph")
        conn.execute("DELETE FROM workflow_variant")
        conn.execute("DELETE FROM workflow_slot_mark")
        conn.execute("DELETE FROM workflow_file")
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
        conn.execute(
            "DELETE FROM model WHERE filename IN (?, ?)",
            (_SHELF_FILENAME, "add_detail.safetensors"),
        )
        # Hashed, like a checkpoint the finder has already read: an unhashed
        # one holds back every digest judgement (see the digest tests below).
        conn.execute(
            "INSERT INTO model (file_kind, filename, sha256, provenance) "
            "VALUES ('checkpoint', ?, ?, 'scanned')",
            (_SHELF_FILENAME, _h("realvisxl-digest")),
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
        conn.executemany(
            "INSERT INTO workflow_topology_core "
            "(topology_hash, core_hash, core_version, workflow_type, slots) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    topology,
                    core,
                    CORE_RULE_VERSION,
                    kind,
                    json.dumps(_slot_list(structural)),
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
        # The one card the owner has hidden. Written as a row rather than
        # inferred, because "hidden" is a decision and nothing derives it.
        conn.execute(
            "INSERT INTO workflow_attr (workflow_key, name, hidden) "
            "VALUES (?, 'A workflow I hid', 1)",
            (HIDDEN_CARD,),
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
    paths = (
        f"{API}/workflows",
        f"{API}/workflows/{BUSY_TOPOLOGY}/variants",
        f"{API}/workflows/{BUSY_TOPOLOGY}/pictures",
        f"{API}/workflows/recipes/{BUSY_RECIPE_A}/graph",
        f"{API}/workflows/cards",
        f"{API}/workflows/cards/{BUSY_CARD}",
        f"{API}/workflows/cards/{BUSY_CARD}/pictures",
        f"{API}/workflows/{BUSY_CARD}/export",
    )
    for path in paths:
        assert_real_route(workflow_env.server.api, "GET", path)
        r = client.get(path)
        assert r.status_code == 403, f"GET {path}: {r.status_code} {r.text}"


# ===========================================================================
# The list opens at topology level
# ===========================================================================


def test_the_list_is_one_row_per_topology_not_per_recipe(workflow_env):
    """§F1's whole shape: five recipes, four rows, variants counted not listed."""
    payload = workflow_env.owner.get(f"{API}/workflows").json()
    rows = _by_hash(payload)
    assert set(rows) == {
        BUSY_TOPOLOGY,
        BINNED_TOPOLOGY,
        FORGOTTEN_TOPOLOGY,
        HIDDEN_TOPOLOGY,
    }
    assert rows[BUSY_TOPOLOGY]["variants"] == 2
    assert rows[BINNED_TOPOLOGY]["variants"] == 1


def test_a_row_counts_the_kept_pictures_and_names_when_they_were_made(workflow_env):
    rows = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())
    assert rows[BUSY_TOPOLOGY]["pictures"] == 5
    assert rows[BUSY_TOPOLOGY]["last_used"].startswith("2026-08-13")


def test_a_workflow_whose_pictures_are_all_binned_reads_as_none_kept(workflow_env):
    """It must still list — the graph outliving its pictures is the point of the
    hub — and it must read as zero rather than as live."""
    rows = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())
    assert BINNED_TOPOLOGY in rows
    assert rows[BINNED_TOPOLOGY]["pictures"] == 0
    assert rows[BINNED_TOPOLOGY]["last_used"] is None


def test_forgotten_model_names_leave_the_row_intact_and_the_assets_empty(
    workflow_env,
):
    """ "Forget this model's name" is a row delete, so the workflow keeps
    listing, keeps its node count and simply stops saying what it used."""
    rows = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())
    row = rows[FORGOTTEN_TOPOLOGY]
    assert row["assets"] == []
    assert row["node_count"] == 38
    assert row["pictures"] == 4


def test_forgotten_names_are_counted_so_the_row_can_say_how_many(workflow_env):
    """The hash does not move when a name goes, so the row still groups; the
    document's unresolved references are what say three models were there."""
    rows = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())
    assert rows[FORGOTTEN_TOPOLOGY]["forgotten_models"] == 3
    assert rows[BUSY_TOPOLOGY]["forgotten_models"] == 0
    (variant,) = workflow_env.owner.get(
        f"{API}/workflows/{FORGOTTEN_TOPOLOGY}/variants"
    ).json()
    assert variant["forgotten_models"] == 3


def test_a_row_carries_its_ghosts_for_the_filter(workflow_env):
    """Picture ghosts are this library's; model ghosts are names for models
    not on the shelf. Each positive sits beside a row that must read zero."""
    server = workflow_env.server
    record_picture_ghosts(
        server.hub,
        [
            PictureGhost(
                library_uuid=server.vault.library_uuid,
                pixel_sha="sha-binned-ghost",
                instance_hash=_h("binned-instance"),
                structural_hash=BINNED_RECIPE,
                thumbnail=b"thumbnail-bytes",
            ),
            PictureGhost(
                library_uuid=_h("another-library"),
                pixel_sha="sha-elsewhere",
                instance_hash=_h("binned-instance"),
                structural_hash=BUSY_RECIPE_A,
                thumbnail=b"thumbnail-bytes",
            ),
        ],
    )
    rows = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())
    assert rows[BINNED_TOPOLOGY]["ghosts"] == 1
    assert rows[BUSY_TOPOLOGY]["ghosts"] == 0
    assert rows[BUSY_TOPOLOGY]["model_ghosts"] == 1
    assert rows[FORGOTTEN_TOPOLOGY]["model_ghosts"] == 0

    settings = workflow_env.owner.get(f"{API}/server-config/ghost-retention").json()
    assert settings["picture_ghosts"] == 1
    assert settings["model_ghosts"] == 1


def test_forgetting_model_ghosts_keeps_the_workflow_and_the_shelfs_names(
    workflow_env,
):
    """The purge forgets the LoRA the shelf no longer has, keeps the checkpoint
    it does, and the workflow stays one row of two variants."""
    owner = workflow_env.owner
    r = owner.delete(f"{API}/server-config/ghost-retention/model-ghosts")
    assert r.status_code == 200, r.text
    assert r.json()["names_forgotten"] == 1

    row = _by_hash(owner.get(f"{API}/workflows").json())[BUSY_TOPOLOGY]
    assert [a["name"] for a in row["assets"]] == [_SHELF_FILENAME]
    assert row["variants"] == 2
    assert row["model_ghosts"] == 0
    assert row["forgotten_models"] == 1
    assert owner.get(f"{API}/server-config/ghost-retention").json()["model_ghosts"] == 0


def test_model_ghosts_judge_only_what_the_shelf_can_hold(workflow_env):
    """The shelf scans ``.safetensors`` alone, so a ``.pth`` is never on it and
    must never be forgotten as a ghost. A loader digest is judged against the
    shelf's digests: the unknown one goes, the one on the shelf stays."""
    server = workflow_env.server
    on_shelf, unknown = _h("digest-on-shelf"), _h("digest-unknown")
    with server.hub.transaction() as conn:
        conn.executemany(
            "INSERT INTO workflow_recipe_asset "
            "(structural_hash, widget_name, normalized_filename) VALUES (?, ?, ?)",
            [
                (BUSY_RECIPE_A, "model_name", "4x_ultrasharp.pth"),
                (BUSY_RECIPE_A, "lora_sha256", unknown),
                (BUSY_RECIPE_B, "lora_sha256", on_shelf),
                # An unset loader and a blank download digest name no model.
                (BUSY_RECIPE_B, "checkpoint_sha256", ""),
                (BUSY_RECIPE_B, "expected_sha256", "not-a-digest"),
            ],
        )
        conn.execute("DELETE FROM model WHERE sha256 = ?", (on_shelf,))
        conn.execute(
            "INSERT INTO model (file_kind, kind, sha256, provenance) "
            "VALUES ('adapter', 'lora', ?, 'scanned')",
            (on_shelf,),
        )
    try:
        owner = workflow_env.owner
        base = f"{API}/server-config/ghost-retention"
        assert owner.get(base).json()["model_ghosts"] == 2
        # A confirm for a count that is no longer true destroys nothing.
        r = owner.delete(f"{base}/model-ghosts", params={"expected": 1})
        assert r.status_code == 409, r.text
        assert owner.get(base).json()["model_ghosts"] == 2

        r = owner.delete(f"{base}/model-ghosts", params={"expected": 2})
        assert r.status_code == 200, r.text
        assert r.json()["names_forgotten"] == 2
        left = {
            row["normalized_filename"]
            for row in server.hub.fetchall(
                "SELECT normalized_filename FROM workflow_recipe_asset"
            )
        }
        assert {
            "4x_ultrasharp.pth",
            on_shelf,
            _SHELF_FILENAME,
            "",
            "not-a-digest",
        } <= left
        assert not {"add_detail.safetensors", unknown} & left
    finally:
        with server.hub.transaction() as conn:
            conn.execute("DELETE FROM model WHERE sha256 = ?", (on_shelf,))


def test_digests_wait_while_a_shelf_checkpoint_is_unhashed(workflow_env):
    """Until the hash finder reads it, a checkpoint's loader digest matches
    nothing on the shelf, so judging it then would forget a model on disk."""
    server = workflow_env.server
    digest = _h("digest-of-unhashed-checkpoint")
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_recipe_asset "
            "(structural_hash, widget_name, normalized_filename) VALUES (?, ?, ?)",
            (BUSY_RECIPE_B, "checkpoint_sha256", digest),
        )
        conn.execute("DELETE FROM model WHERE filename = 'unhashed.safetensors'")
        conn.execute(
            "INSERT INTO model (file_kind, filename, provenance) "
            "VALUES ('checkpoint', 'unhashed.safetensors', 'scanned')"
        )
    base = f"{API}/server-config/ghost-retention"
    try:
        # Only the seeded LoRA name; the digest is not judged yet.
        assert workflow_env.owner.get(base).json()["model_ghosts"] == 1
    finally:
        with server.hub.transaction() as conn:
            conn.execute("DELETE FROM model WHERE filename = 'unhashed.safetensors'")
    # Positive control: with nothing waiting, the same digest is a ghost.
    assert workflow_env.owner.get(base).json()["model_ghosts"] == 2


def test_a_model_back_on_the_shelf_is_not_a_ghost(workflow_env):
    """A copy's basename counts as much as the recorded filename."""
    server = workflow_env.server
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model (file_kind, filename, provenance) "
            "VALUES ('checkpoint', NULL, 'scanned')"
        )
        model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        folder_id = conn.execute("SELECT id FROM model_folder LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state) "
            "VALUES (?, ?, 'loras/Add_Detail.safetensors', 'present')",
            (model_id, folder_id),
        )
    try:
        r = workflow_env.owner.delete(
            f"{API}/server-config/ghost-retention/model-ghosts"
        )
        assert r.json()["names_forgotten"] == 0
    finally:
        with server.hub.transaction() as conn:
            conn.execute("DELETE FROM model_file WHERE model_id = ?", (model_id,))
            conn.execute("DELETE FROM model WHERE id = ?", (model_id,))


def test_a_row_carries_each_asset_its_variants_name_exactly_once(workflow_env):
    """A LIST comparison, not a set, and that is the point of the test.

    The asset table is keyed per recipe, so two variants naming the same
    checkpoint are two rows. Compared as a set that duplication is invisible —
    and it is not cosmetic: it is what turns the 159-variant family's Models
    cell into 159 copies of one filename and its descriptor into a claim that
    the graph loads 159 adapters at once.
    """
    rows = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())
    assets = rows[BUSY_TOPOLOGY]["assets"]
    assert [(a["widget"], a["name"]) for a in assets] == [
        ("ckpt_name", "realvisxl.safetensors"),
        ("lora_name", "add_detail.safetensors"),
    ]


def test_adapter_slots_count_one_run_not_the_names_across_variants(workflow_env):
    """What one run loads, which the set of names cannot answer.

    Both of BUSY's recipes name the same checkpoint; only one names an adapter.
    A topology is the graph alone, so its adapter slots are a property every
    recipe under it shares — one here, however many files the family has been
    bound to over its life.
    """
    rows = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())
    assert rows[BUSY_TOPOLOGY]["adapter_slots"] == 1
    assert rows[FORGOTTEN_TOPOLOGY]["adapter_slots"] == 0


def test_the_scan_block_says_which_empty_state_the_list_is_in(workflow_env):
    """The list cannot tell "not looked yet" from "looked, and nothing" on its
    own, and three of the four states a new user meets are exactly that.

    The two figures must **differ** here, or the assertion says nothing: with
    every picture scanned, counting any column at all gives the same answer and
    the one distinction this block exists to draw goes untested.
    """
    scan = workflow_env.owner.get(f"{API}/workflows").json()["scan"]
    # Six kept pictures, one of them not yet read. The binned picture is in
    # neither figure: it is not kept.
    assert scan["pictures"] == 11
    assert scan["scanned"] == 10


# ===========================================================================
# The variants are the row's expansion
# ===========================================================================


def test_variants_add_up_to_the_row_above_them(workflow_env):
    variants = workflow_env.owner.get(
        f"{API}/workflows/{BUSY_TOPOLOGY}/variants"
    ).json()
    by_hash = {row["structural_hash"]: row for row in variants}
    assert set(by_hash) == {BUSY_RECIPE_A, BUSY_RECIPE_B}
    assert by_hash[BUSY_RECIPE_A]["pictures"] == 3
    assert by_hash[BUSY_RECIPE_B]["pictures"] == 2
    row = _by_hash(workflow_env.owner.get(f"{API}/workflows").json())[BUSY_TOPOLOGY]
    assert sum(v["pictures"] for v in variants) == row["pictures"]


def test_a_variant_carries_only_its_own_assets(workflow_env):
    """The LoRA belongs to one of the two recipes; the expansion is where that
    difference becomes visible, and it is the reason variants exist at all."""
    variants = workflow_env.owner.get(
        f"{API}/workflows/{BUSY_TOPOLOGY}/variants"
    ).json()
    by_hash = {row["structural_hash"]: row for row in variants}
    assert {a["name"] for a in by_hash[BUSY_RECIPE_A]["assets"]} == {
        "realvisxl.safetensors",
        "add_detail.safetensors",
    }
    assert {a["name"] for a in by_hash[BUSY_RECIPE_B]["assets"]} == {
        "realvisxl.safetensors"
    }


def test_an_unknown_topology_is_a_404_not_an_empty_list(workflow_env):
    """A hash from another machine is "this machine does not have it", which an
    empty 200 would render as "this workflow has no variants"."""
    r = workflow_env.owner.get(f"{API}/workflows/{_h('nosuchtopology')}/variants")
    assert r.status_code == 404, r.text


def test_a_malformed_hash_is_refused_by_name(workflow_env):
    r = workflow_env.owner.get(f"{API}/workflows/not-a-hash/variants")
    assert r.status_code == 422, r.text
    assert "topology_hash" in r.text


# ===========================================================================
# The rail's tiles, and the graph
# ===========================================================================


def test_picture_ids_are_newest_first_and_exclude_the_scrapheap(workflow_env):
    """Named for the order, so the order is what is asserted.

    The rail draws six tiles out of a workflow that may have a thousand
    pictures, so which six is the whole of the choice; a test that only counted
    them would pass with the sort reversed.
    """
    ids = workflow_env.owner.get(f"{API}/workflows/{BUSY_TOPOLOGY}/pictures").json()
    assert len(ids) == 5
    dated = {
        row["file_path"]: row["id"]
        for row in workflow_env.owner.get(f"{API}/pictures", params={"id": ids}).json()
    }
    # busy_four (2026-08-13) is the newest of the five, busy_unrated the oldest.
    assert ids[0] == dated["busy_four.png"]
    assert ids[-1] == dated["busy_unrated.png"]

    binned = workflow_env.owner.get(
        f"{API}/workflows/{BINNED_TOPOLOGY}/pictures"
    ).json()
    assert binned == []


def test_the_tile_limit_is_the_routes_to_set_not_the_callers(workflow_env):
    """A tile strip must not be turnable into a library dump by editing a URL."""
    r = workflow_env.owner.get(
        f"{API}/workflows/{BUSY_TOPOLOGY}/pictures", params={"limit": 100000}
    )
    assert r.status_code == 422, r.text


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

_TEMPLATED_PATHS = (
    f"{API}/workflows/{BUSY_TOPOLOGY}/variants",
    f"{API}/workflows/{BUSY_TOPOLOGY}/pictures",
    f"{API}/workflows/recipes/{BUSY_RECIPE_A}/graph",
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
            for path in _TEMPLATED_PATHS:
                assert_real_route(server.api, "GET", path)
                r = client.get(path)
                assert r.status_code == 403, f"GET {path}: {r.status_code} {r.text}"
        for path in _TEMPLATED_PATHS:
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
    for path in (f"{API}/workflows", *_TEMPLATED_PATHS):
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
# How each picture input is filled (#1305)
# ===========================================================================

_INPUT_ROUTES = (
    ("GET", "/api/v1/comfyui/workflows/{workflow_name}/inputs"),
    ("PUT", "/api/v1/comfyui/workflows/{workflow_name}/inputs"),
)


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


@pytest.fixture
def edit_workflow(tmp_path, monkeypatch):
    """One two-input workflow file in a user folder of its own.

    The real user folder is shared by every checkout on the machine, so the
    routes are pointed at a temporary one rather than written into it.
    """
    # Stored the way the placeholder migration leaves a dialog-bound file: the
    # old token sat on the SECOND input, so a default that just took the lowest
    # node id would show here.
    graph, _changed = workflow_bindings.migrate_placeholders(
        {
            "1": {"class_type": "LoadImage", "inputs": {"image": "Logo.png"}},
            "2": {
                "class_type": "LoadImage",
                "inputs": {"image": "{{image_path}}"},
                "_meta": {"title": "Reference"},
            },
            "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        }
    )
    (tmp_path / "edit.json").write_text(json.dumps(graph), encoding="utf-8")
    _isolate_workflow_folders(tmp_path, monkeypatch)
    return f"{API}/comfyui/workflows/edit.json/inputs"


def _picture_with_sha(server, file_path: str, pixel_sha: str) -> int:
    def write(session):
        picture = session.exec(
            select(Picture).where(Picture.file_path == file_path)
        ).one()
        picture.pixel_sha = pixel_sha
        session.add(picture)
        session.commit()
        return picture.id

    return server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


def _by_node(body) -> dict:
    return {item["node_id"]: item for item in body["inputs"]}


def test_the_input_routes_are_declared_owner_only():
    for key in _INPUT_ROUTES:
        assert ROUTE_POLICIES[key].policy is AccessPolicy.OWNER_ONLY, key


def test_no_scoped_token_can_read_or_set_a_workflows_inputs(
    workflow_env, edit_workflow
):
    """The setup names Fixed pictures by id, so a share token gets neither half.

    The belts are emptied, and the token's scope is let through the non-GET
    refusal, so both halves are refused by the gate's declaration rather than
    by the middleware in front of it: "Owner-level" is the gate's own string.
    """
    server = workflow_env.server
    token = _mint(
        workflow_env.owner,
        "inputs scope probe",
        resource_type="character",
        resource_id=workflow_env.character_id,
    )
    client = _bearer(server, token)
    assert client.get(f"{API}/pictures").status_code == 200, "the token is dead"
    assert_real_route(server.api, "GET", edit_workflow)
    assert_real_route(server.api, "PUT", edit_workflow)
    body = {
        "inputs": [
            {"node_id": "1", "mode": "picker"},
            {"node_id": "2", "mode": "picker"},
        ]
    }
    previously_enforcing = server.authz._enforcing
    server.authz._enforcing = True
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(auth, "READ_BLOCKED_GET_PATHS", frozenset())
            patch.setattr(auth, "READ_BLOCKED_GET_PREFIXES", ())
            patch.setattr(auth, "WRITE_ENABLED_SCOPES", frozenset({"READ", "WRITE"}))
            r = client.get(edit_workflow)
            assert r.status_code == 403 and "Owner-level" in r.text, r.text
            r = client.put(edit_workflow, json=body)
            assert r.status_code == 403 and "Owner-level" in r.text, r.text
        assert client.put(edit_workflow, json=body).status_code == 403
        # The refusal wrote nothing: the owner still reads the defaults.
        owner_view = _by_node(workflow_env.owner.get(edit_workflow).json())
        assert owner_view["2"]["mode"] == "selection"
    finally:
        server.authz._enforcing = previously_enforcing


def test_an_unconfigured_workflow_reads_its_defaults_with_titles(
    workflow_env, edit_workflow
):
    r = workflow_env.owner.get(edit_workflow)
    assert r.status_code == 200, r.text
    assert r.json()["inputs"] == [
        {
            "node_id": "1",
            "title": "LoadImage",
            "mode": "picker",
            "picture_id": None,
            "picture_missing": False,
        },
        {
            "node_id": "2",
            "title": "Reference",
            "mode": "selection",
            "picture_id": None,
            "picture_missing": False,
        },
    ]


def test_a_fixed_picture_is_kept_by_content_and_leaves_the_selection_pill(
    workflow_env, edit_workflow
):
    server, owner = workflow_env.server, workflow_env.owner
    picture_id = _picture_with_sha(server, "busy_one.png", "sha-fixed-reference")
    r = owner.put(
        edit_workflow,
        json={
            "inputs": [
                {"node_id": "1", "mode": "picker"},
                {"node_id": "2", "mode": "fixed", "picture_id": picture_id},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _by_node(r.json())["2"]["picture_id"] == picture_id
    assert (
        server.hub.fetchone(
            "SELECT pixel_sha FROM workflow_picture_input WHERE node_id = '2'"
        )["pixel_sha"]
        == "sha-fixed-reference"
    )
    listed = {
        item["name"]: item
        for item in owner.get(f"{API}/comfyui/workflows").json()["workflows"]
    }
    assert listed["edit.json"]["has_selection_input"] is False

    # Moving the picture to the Scrapheap leaves the input saying so.
    def bin_it(session):
        picture = session.get(Picture, picture_id)
        picture.deleted = True
        session.add(picture)
        session.commit()

    server.vault.db.run_task(bin_it, priority=DBPriority.IMMEDIATE)
    fixed = _by_node(owner.get(edit_workflow).json())["2"]
    assert (fixed["picture_id"], fixed["picture_missing"]) == (None, True)

    # Changing the other input keeps the Fixed one's picture, missing or not.
    r = owner.put(
        edit_workflow,
        json={
            "inputs": [
                {"node_id": "1", "mode": "selection"},
                {"node_id": "2", "mode": "fixed"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert (
        server.hub.fetchone(
            "SELECT pixel_sha FROM workflow_picture_input WHERE node_id = '2'"
        )["pixel_sha"]
        == "sha-fixed-reference"
    )


def test_leaving_fixed_keeps_the_picture_for_coming_back(workflow_env, edit_workflow):
    """One arrow key steps an input off Fixed; it must not cost the picture."""
    server, owner = workflow_env.server, workflow_env.owner
    picture_id = _picture_with_sha(server, "busy_two.png", "sha-kept-reference")

    def put(mode_2, **extra):
        r = owner.put(
            edit_workflow,
            json={
                "inputs": [
                    {"node_id": "1", "mode": "selection"},
                    {"node_id": "2", "mode": mode_2, **extra},
                ]
            },
        )
        assert r.status_code == 200, r.text
        return _by_node(r.json())["2"]

    put("fixed", picture_id=picture_id)
    stepped_off = put("picker")
    assert (stepped_off["mode"], stepped_off["picture_id"]) == ("picker", picture_id)
    back = put("fixed")
    assert (back["mode"], back["picture_id"], back["picture_missing"]) == (
        "fixed",
        picture_id,
        False,
    )


def test_an_unhashed_picture_is_a_409_and_a_duplicate_resolves_to_its_oldest_copy(
    workflow_env, edit_workflow
):
    server, owner = workflow_env.server, workflow_env.owner

    def ids_by_path(session):
        return {p.file_path: p.id for p in session.exec(select(Picture)).all()}

    ids = server.vault.db.run_immediate_read_task(ids_by_path)
    body = {
        "inputs": [
            {"node_id": "1", "mode": "selection"},
            {"node_id": "2", "mode": "fixed", "picture_id": ids["busy_one.png"]},
        ]
    }
    # Seeded pictures carry no pixel_sha: there is nothing to name one by.
    r = owner.put(edit_workflow, json=body)
    assert r.status_code == 409, r.text

    older = _picture_with_sha(server, "busy_one.png", "sha-duplicate")
    newer = _picture_with_sha(server, "busy_three.png", "sha-duplicate")
    assert older < newer
    body["inputs"][1]["picture_id"] = newer
    r = owner.put(edit_workflow, json=body)
    assert r.status_code == 200, r.text
    assert _by_node(r.json())["2"]["picture_id"] == older


def test_a_bad_setup_writes_nothing(workflow_env, edit_workflow):
    owner = workflow_env.owner
    two_selections = {
        "inputs": [
            {"node_id": "1", "mode": "selection"},
            {"node_id": "2", "mode": "selection"},
        ]
    }
    assert owner.put(edit_workflow, json=two_selections).status_code == 400
    unknown_picture = {
        "inputs": [
            {"node_id": "1", "mode": "selection"},
            {"node_id": "2", "mode": "fixed", "picture_id": 987654},
        ]
    }
    assert owner.put(edit_workflow, json=unknown_picture).status_code == 404
    # A Fixed input with no picture of its own to keep.
    no_picture = {
        "inputs": [
            {"node_id": "1", "mode": "selection"},
            {"node_id": "2", "mode": "fixed"},
        ]
    }
    assert owner.put(edit_workflow, json=no_picture).status_code == 400
    assert (
        workflow_env.server.hub.fetchone(
            "SELECT COUNT(*) AS n FROM workflow_picture_input"
        )["n"]
        == 0
    )
    missing = f"{API}/comfyui/workflows/nosuch.json/inputs"
    assert owner.get(missing).status_code == 404


def test_deleting_a_workflow_forgets_its_setup(workflow_env, edit_workflow):
    owner = workflow_env.owner
    r = owner.put(
        edit_workflow,
        json={
            "inputs": [
                {"node_id": "1", "mode": "picker"},
                {"node_id": "2", "mode": "selection"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    r = owner.delete(f"{API}/comfyui/workflows/edit.json")
    assert r.status_code == 200, r.text
    assert (
        workflow_env.server.hub.fetchone(
            "SELECT COUNT(*) AS n FROM workflow_picture_input"
        )["n"]
        == 0
    )


# ===========================================================================
# A workflow's parameters and pins (#1306)
# ===========================================================================

_PARAMETER_ROUTES = (
    ("GET", "/api/v1/comfyui/workflows/{workflow_name}/parameters"),
    ("PUT", "/api/v1/comfyui/workflows/{workflow_name}/pins"),
)

_SAMPLER_INFO = {
    "KSampler": {
        "input": {
            "required": {
                "seed": [
                    "INT",
                    {"min": 0, "max": 2**64 - 1, "control_after_generate": True},
                ],
                "steps": ["INT", {"min": 1, "max": 150}],
                "sampler_name": [["euler", "dpmpp_2m"], {}],
            }
        }
    },
}


@pytest.fixture
def sampler_workflow(tmp_path, monkeypatch):
    """One API-format workflow with a prompt a run fills, in its own user folder.

    ComfyUI is replaced by ``object_info``: set ``.info`` to a map, or to an
    exception for an unreachable ComfyUI.
    """
    graph = {
        "1": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 2**64 - 2,
                "steps": 20,
                "sampler_name": "euler",
                "positive": ["2", 0],
            },
        },
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }
    (tmp_path / "sampler.json").write_text(json.dumps(graph), encoding="utf-8")
    (tmp_path / "canvas.json").write_text(
        json.dumps({"nodes": [{"id": 1, "type": "KSampler"}], "links": []}),
        encoding="utf-8",
    )
    _isolate_workflow_folders(tmp_path, monkeypatch)
    comfy = SimpleNamespace(info=_SAMPLER_INFO, asked=0)

    def fake_object_info(_url):
        comfy.asked += 1
        if isinstance(comfy.info, Exception):
            raise comfy.info
        return comfy.info

    monkeypatch.setattr(comfyui_module, "fetch_object_info", fake_object_info)
    comfy.parameters = f"{API}/comfyui/workflows/sampler.json/parameters"
    comfy.pins = f"{API}/comfyui/workflows/sampler.json/pins"
    return comfy


def _parameter(body, node_id: str, name: str) -> dict:
    return next(
        p for p in body["parameters"] if (p["node_id"], p["name"]) == (node_id, name)
    )


def _pinned(body) -> list:
    return [(p["node_id"], p["name"]) for p in body["parameters"] if p["pinned"]]


def test_the_parameter_routes_are_declared_owner_only():
    for key in _PARAMETER_ROUTES:
        assert ROUTE_POLICIES[key].policy is AccessPolicy.OWNER_ONLY, key


def test_no_scoped_token_can_read_parameters_or_set_pins(
    workflow_env, sampler_workflow
):
    """Refused by the gate's declaration, not the middleware in front of it."""
    server = workflow_env.server
    token = _mint(
        workflow_env.owner,
        "parameters scope probe",
        resource_type="character",
        resource_id=workflow_env.character_id,
    )
    client = _bearer(server, token)
    assert client.get(f"{API}/pictures").status_code == 200, "the token is dead"
    assert_real_route(server.api, "GET", sampler_workflow.parameters)
    assert_real_route(server.api, "PUT", sampler_workflow.pins)
    body = {"pins": [{"node_id": "1", "name": "steps"}]}
    previously_enforcing = server.authz._enforcing
    server.authz._enforcing = True
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(auth, "READ_BLOCKED_GET_PATHS", frozenset())
            patch.setattr(auth, "READ_BLOCKED_GET_PREFIXES", ())
            patch.setattr(auth, "WRITE_ENABLED_SCOPES", frozenset({"READ", "WRITE"}))
            r = client.get(sampler_workflow.parameters)
            assert r.status_code == 403 and "Owner-level" in r.text, r.text
            r = client.put(sampler_workflow.pins, json=body)
            assert r.status_code == 403 and "Owner-level" in r.text, r.text
        # Refused before ComfyUI was asked, and nothing was stored.
        assert sampler_workflow.asked == 0
        owner_view = workflow_env.owner.get(sampler_workflow.parameters).json()
        assert owner_view["pins_saved"] is False
    finally:
        server.authz._enforcing = previously_enforcing


def test_parameters_are_typed_from_comfyui_and_leave_the_prompt_out(
    workflow_env, sampler_workflow
):
    r = workflow_env.owner.get(sampler_workflow.parameters)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["readable"], body["typed"], body["comfyui_error"]) == (
        True,
        True,
        None,
    )
    steps = _parameter(body, "1", "steps")
    assert (steps["kind"], steps["value"], steps["min"], steps["max"]) == (
        "int",
        20,
        1,
        150,
    )
    assert _parameter(body, "1", "sampler_name")["options"] == ["euler", "dpmpp_2m"]
    assert _parameter(body, "1", "seed")["kind"] == "seed"
    names = {(p["node_id"], p["name"]) for p in body["parameters"]}
    assert ("2", "text") not in names and ("1", "positive") not in names
    assert body["pins_saved"] is False
    assert _pinned(body) == [("1", "seed"), ("1", "steps"), ("1", "sampler_name")]


def test_an_unreachable_comfyui_still_shows_the_recorded_values(
    workflow_env, sampler_workflow
):
    """The values survive the outage, and the exception's own text does not.

    ``comfyui_error`` is composed by the route, not taken from the exception:
    whatever the fetch failed on - a chained error, an internal address, a
    token echoed back by a proxy - belongs in the log, not in a response.
    """
    sampler_workflow.info = RuntimeError(
        "Could not reach ComfyUI at http://127.0.0.1:8188 (example-secret)"
    )
    r = workflow_env.owner.get(sampler_workflow.parameters)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["typed"] is False
    assert body["comfyui_error"].startswith("Could not read ComfyUI's node types at")
    assert "example-secret" not in body["comfyui_error"]
    steps = _parameter(body, "1", "steps")
    assert (steps["value"], steps["min"], steps["max"]) == (20, None, None)
    assert _parameter(body, "1", "sampler_name")["options"] is None


def test_a_ui_format_file_is_unreadable_without_asking_comfyui(
    workflow_env, sampler_workflow
):
    r = workflow_env.owner.get(f"{API}/comfyui/workflows/canvas.json/parameters")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["readable"], body["parameters"]) == (False, [])
    # Nothing was asked, so nothing is reported offline.
    assert (body["typed"], body["comfyui_error"]) == (False, None)
    assert sampler_workflow.asked == 0


def test_a_ui_file_the_reduction_refuses_is_still_just_unreadable(
    workflow_env, sampler_workflow, tmp_path
):
    """A subgraph instance with no definition makes detection raise; the form
    does not need detection for a file it cannot read anyway."""
    canvas = {
        "nodes": [{"id": 1, "type": "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"}],
        "links": [],
        "definitions": {"subgraphs": []},
    }
    with pytest.raises(WorkflowGraphError):
        detect_workflow_io(canvas)
    (tmp_path / "orphan.json").write_text(json.dumps(canvas), encoding="utf-8")
    r = workflow_env.owner.get(f"{API}/comfyui/workflows/orphan.json/parameters")
    assert r.status_code == 200, r.text
    assert r.json()["readable"] is False


def test_a_workflow_with_nothing_to_set_does_not_ask_comfyui(
    workflow_env, sampler_workflow, tmp_path
):
    graph = {"1": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}}}
    (tmp_path / "bare.json").write_text(json.dumps(graph), encoding="utf-8")
    r = workflow_env.owner.get(f"{API}/comfyui/workflows/bare.json/parameters")
    assert r.status_code == 200, r.text
    assert (r.json()["parameters"], r.json()["typed"]) == ([], False)
    assert sampler_workflow.asked == 0


def test_pins_are_kept_in_order_and_null_restores_the_defaults(
    workflow_env, sampler_workflow
):
    owner = workflow_env.owner
    order = [
        {"node_id": "1", "name": "sampler_name"},
        {"node_id": "1", "name": "steps"},
    ]
    r = owner.put(sampler_workflow.pins, json={"pins": order})
    assert r.status_code == 200, r.text
    assert r.json() == {"workflow": "sampler.json", "pins_saved": True, "pins": order}
    # Saving does not wait on ComfyUI.
    assert sampler_workflow.asked == 0
    body = owner.get(sampler_workflow.parameters).json()
    assert (body["pins_saved"], body["pins"]) == (True, order)
    assert _pinned(body) == [("1", "steps"), ("1", "sampler_name")]

    r = owner.put(sampler_workflow.pins, json={"pins": []})
    assert r.json()["pins"] == [] and r.json()["pins_saved"] is True
    body = owner.get(sampler_workflow.parameters).json()
    assert (body["pins"], _pinned(body)) == ([], [])

    r = owner.put(sampler_workflow.pins, json={"pins": None})
    assert (r.json()["pins_saved"], r.json()["pins"]) == (False, None)
    body = owner.get(sampler_workflow.parameters).json()
    assert [(p["node_id"], p["name"]) for p in body["pins"]] == [
        ("1", "seed"),
        ("1", "steps"),
        ("1", "sampler_name"),
    ]


def test_a_seed_beyond_float_precision_is_returned_exactly(
    workflow_env, sampler_workflow
):
    body = workflow_env.owner.get(sampler_workflow.parameters).json()
    seed = _parameter(body, "1", "seed")
    assert (seed["value"], seed["max"]) == (2**64 - 2, 2**64 - 1)


def test_a_bad_pin_writes_nothing(workflow_env, sampler_workflow):
    owner = workflow_env.owner
    for body in (
        {"pins": [{"node_id": "2", "name": "text"}]},
        {"pins": [{"node_id": "1"}]},
        {"pins": "steps"},
        {},
    ):
        assert owner.put(sampler_workflow.pins, json=body).status_code == 400, body
    assert (
        workflow_env.server.hub.fetchone(
            "SELECT COUNT(*) AS n FROM workflow_parameter_pins"
        )["n"]
        == 0
    )
    missing = f"{API}/comfyui/workflows/nosuch.json/parameters"
    assert owner.get(missing).status_code == 404


def test_deleting_a_workflow_forgets_its_pins(workflow_env, sampler_workflow):
    owner = workflow_env.owner
    r = owner.put(
        sampler_workflow.pins, json={"pins": [{"node_id": "1", "name": "steps"}]}
    )
    assert r.status_code == 200, r.text
    r = owner.delete(f"{API}/comfyui/workflows/sampler.json")
    assert r.status_code == 200, r.text
    assert (
        workflow_env.server.hub.fetchone(
            "SELECT COUNT(*) AS n FROM workflow_parameter_pins"
        )["n"]
        == 0
    )


# ===========================================================================
# Running a workflow (#1307)
# ===========================================================================

_RUN_ROUTE = ("POST", "/api/v1/comfyui/workflows/{workflow_name}/run")


@pytest.fixture
def fake_comfyui(tmp_path, monkeypatch):
    """ComfyUI replaced by recorders, and every picture given a file to upload.

    ``uploads`` holds ``(file, upload_name)``, ``submitted`` each graph sent and
    ``collected`` the arguments each output import started with.
    """
    comfy = SimpleNamespace(uploads=[], submitted=[], collected=[])
    files = tmp_path / "pictures"
    files.mkdir()

    def resolve(_root, file_path):
        path = files / file_path
        path.write_bytes(b"not really a png")
        return str(path)

    def upload(_url, file_path, upload_name=None):
        comfy.uploads.append((file_path.rsplit("/", 1)[-1], upload_name))
        return upload_name

    def submit(_url, workflow, _client_id=None):
        comfy.submitted.append(json.loads(json.dumps(workflow)))
        return {"prompt_id": f"prompt-{len(comfy.submitted)}"}

    def collect(*args, **kwargs):
        comfy.collected.append((args[3:], kwargs.get("view_context")))

    monkeypatch.setattr(
        comfyui_module.ImageUtils, "resolve_picture_path", staticmethod(resolve)
    )
    monkeypatch.setattr(comfyui_module, "_upload_image_to_comfyui", upload)
    monkeypatch.setattr(comfyui_module, "_submit_comfyui_prompt", submit)
    monkeypatch.setattr(comfyui_module, "_process_comfyui_outputs", collect)
    return comfy


def _picture_ids(server) -> dict:
    def ids_by_path(session):
        return {p.file_path: p.id for p in session.exec(select(Picture)).all()}

    return server.vault.db.run_immediate_read_task(ids_by_path)


def _run(owner, name: str, **body):
    return owner.post(f"{API}/comfyui/workflows/{name}/run", json=body)


def _wait_for_collection(comfy, count: int) -> None:
    deadline = time.monotonic() + 5.0
    while len(comfy.collected) < count and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(comfy.collected) == count, comfy.collected


def test_the_run_route_is_declared_owner_only_and_refuses_a_scoped_token(
    workflow_env, edit_workflow, fake_comfyui
):
    assert ROUTE_POLICIES[_RUN_ROUTE].policy is AccessPolicy.OWNER_ONLY
    server = workflow_env.server
    token = _mint(
        workflow_env.owner,
        "run scope probe",
        resource_type="character",
        resource_id=workflow_env.character_id,
    )
    client = _bearer(server, token)
    assert client.get(f"{API}/pictures").status_code == 200, "the token is dead"
    url = f"{API}/comfyui/workflows/edit.json/run"
    assert_real_route(server.api, "POST", url)
    ids = _picture_ids(server)
    body = {
        "picture_ids": [ids["busy_one.png"]],
        "pictures": [{"node_id": "1", "picture_id": ids["busy_two.png"]}],
        "stack": False,
    }
    previously_enforcing = server.authz._enforcing
    server.authz._enforcing = True
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(auth, "WRITE_ENABLED_SCOPES", frozenset({"READ", "WRITE"}))
            r = client.post(url, json=body)
            assert r.status_code == 403 and "Owner-level" in r.text, r.text
        assert fake_comfyui.submitted == []
        # The in-scope positive control: the owner runs the same body.
        r = workflow_env.owner.post(url, json=body)
        assert r.status_code == 200, r.text
    finally:
        server.authz._enforcing = previously_enforcing


def test_a_selection_runs_once_per_picture_with_the_picker_in_every_run(
    workflow_env, edit_workflow, fake_comfyui
):
    ids = _picture_ids(workflow_env.server)
    selected = [ids["busy_one.png"], ids["busy_three.png"]]
    picked = ids["busy_two.png"]
    r = _run(
        workflow_env.owner,
        "edit.json",
        picture_ids=selected,
        pictures=[{"node_id": "1", "picture_id": picked}],
        stack=False,
    )
    assert r.status_code == 200, r.text
    assert [p["picture_id"] for p in r.json()["prompts"]] == selected
    # Node 2 holds the old binding, so it is the Selection input by default.
    assert [g["2"]["inputs"]["image"] for g in fake_comfyui.submitted] == [
        f"pixlstash-{selected[0]}-unhashed.png",
        f"pixlstash-{selected[1]}-unhashed.png",
    ]
    assert {g["1"]["inputs"]["image"] for g in fake_comfyui.submitted} == {
        f"pixlstash-{picked}-unhashed.png"
    }
    # The picker's picture is uploaded once, not once per run.
    assert [name for name, _ in fake_comfyui.uploads].count("busy_two.png") == 1
    _wait_for_collection(fake_comfyui, 2)
    assert sorted(args for args, _ in fake_comfyui.collected) == sorted(
        (["3"], None, pic_id) for pic_id in selected
    )


def test_a_fixed_input_is_filled_from_the_setup_and_a_lost_one_runs_nothing(
    workflow_env, edit_workflow, fake_comfyui
):
    server, owner = workflow_env.server, workflow_env.owner
    fixed = _picture_with_sha(server, "busy_two.png", "sha-run-fixed")
    r = owner.put(
        edit_workflow,
        json={
            "inputs": [
                {"node_id": "1", "mode": "fixed", "picture_id": fixed},
                {"node_id": "2", "mode": "selection"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    selected = _picture_ids(server)["busy_one.png"]
    r = _run(owner, "edit.json", picture_ids=[selected], stack=False)
    assert r.status_code == 200, r.text
    assert fake_comfyui.submitted[0]["1"]["inputs"]["image"] == (
        f"pixlstash-{fixed}-sha-run-fixed.png"
    )

    def bin_it(session):
        picture = session.get(Picture, fixed)
        picture.deleted = True
        session.add(picture)
        session.commit()

    server.vault.db.run_task(bin_it, priority=DBPriority.IMMEDIATE)
    r = _run(owner, "edit.json", picture_ids=[selected], stack=False)
    assert r.status_code == 409, r.text
    assert len(fake_comfyui.submitted) == 1


def test_a_run_that_cannot_be_filled_submits_nothing(
    workflow_env, edit_workflow, fake_comfyui
):
    owner = workflow_env.owner
    ids = _picture_ids(workflow_env.server)
    one, two = ids["busy_one.png"], ids["busy_two.png"]
    picker = [{"node_id": "1", "picture_id": two}]
    for name, body in (
        # The picker input was not chosen.
        ("edit.json", {"picture_ids": [one]}),
        # A Selection input with nothing selected.
        ("edit.json", {"pictures": picker}),
        # A node that is not a picker.
        (
            "edit.json",
            {"picture_ids": [one], "pictures": [*picker, {"node_id": "2"}]},
        ),
        (
            "edit.json",
            {
                "picture_ids": [one],
                "pictures": picker,
                "seed_mode": "fixed",
                "seed": "x",
            },
        ),
        ("edit.json", {"picture_ids": ["1"], "pictures": picker}),
        ("edit.json", {"picture_ids": [one], "pictures": picker, "seed_mode": "fixd"}),
        ("edit.json", {"picture_ids": [one], "pictures": picker, "stack": "false"}),
    ):
        r = _run(owner, name, **body)
        assert r.status_code == 400, (body, r.text)
    r = _run(owner, "edit.json", picture_ids=[987654], pictures=picker)
    assert r.status_code == 404, r.text

    # A picture in the Scrapheap is not one a run may read.
    def bin_it(session):
        picture = session.get(Picture, one)
        picture.deleted = True
        session.add(picture)
        session.commit()

    workflow_env.server.vault.db.run_task(bin_it, priority=DBPriority.IMMEDIATE)
    r = _run(owner, "edit.json", picture_ids=[one], pictures=picker)
    assert r.status_code == 404, r.text
    assert fake_comfyui.submitted == [] and fake_comfyui.uploads == []


def test_a_workflow_without_a_selection_input_runs_once_and_takes_no_selection(
    workflow_env, sampler_workflow, fake_comfyui
):
    owner = workflow_env.owner
    one = _picture_ids(workflow_env.server)["busy_one.png"]
    r = _run(owner, "sampler.json", picture_ids=[one])
    assert r.status_code == 400, r.text
    r = _run(owner, "canvas.json")
    assert r.status_code == 400 and "UI format" in r.text, r.text
    assert fake_comfyui.submitted == []

    r = _run(
        owner,
        "sampler.json",
        caption="a dog",
        values=[{"node_id": "1", "name": "steps", "value": 31}],
        seed_mode="keep",
        character_id=workflow_env.character_id,
    )
    assert r.status_code == 200, r.text
    (prompt,) = r.json()["prompts"]
    assert (prompt["picture_id"], prompt["prompt_id"]) == (None, "prompt-1")
    (graph,) = fake_comfyui.submitted
    assert graph["1"]["inputs"]["steps"] == 31
    assert graph["1"]["inputs"]["seed"] == 2**64 - 2
    assert graph["2"]["inputs"]["text"] == "a dog"
    _wait_for_collection(fake_comfyui, 1)
    assert fake_comfyui.collected == [
        ((["3"], None, None), {"character_id": workflow_env.character_id})
    ]

    r = _run(owner, "sampler.json", values=[{"node_id": "2", "name": "text"}])
    assert r.status_code == 400, r.text
    r = _run(owner, "sampler.json")
    assert r.status_code == 200, r.text
    assert fake_comfyui.submitted[-1]["1"]["inputs"]["seed"] != 2**64 - 2


def test_the_list_says_which_workflows_can_run(workflow_env, sampler_workflow):
    listed = {
        item["name"]: item
        for item in workflow_env.owner.get(f"{API}/comfyui/workflows").json()[
            "workflows"
        ]
    }
    assert listed["sampler.json"]["runnable"] is True
    assert listed["canvas.json"]["runnable"] is False


def test_a_selection_run_stacks_each_output_with_its_picture(
    workflow_env, edit_workflow, fake_comfyui
):
    ids = _picture_ids(workflow_env.server)
    one, two = ids["busy_one.png"], ids["busy_two.png"]
    r = _run(
        workflow_env.owner,
        "edit.json",
        picture_ids=[one],
        pictures=[{"node_id": "1", "picture_id": two}],
    )
    assert r.status_code == 200, r.text
    _wait_for_collection(fake_comfyui, 1)
    ((output_nodes, stack_id, source), _context) = fake_comfyui.collected[0]
    assert (output_nodes, source) == (["3"], one)
    assert stack_id is not None
    assert str(stack_id) in fake_comfyui.submitted[0]["3"]["inputs"]["filename_prefix"]


def test_a_template_holding_the_picture_and_the_caption_gets_both(
    workflow_env, tmp_path, edit_workflow, fake_comfyui
):
    # A loader detection does not recognise, bound by the old dialog with both
    # tokens in one string: filled a role at a time, one would wipe the other.
    graph, _changed = workflow_bindings.migrate_placeholders(
        {
            "7": {
                "class_type": "MyPictureSource",
                "inputs": {"image": "{{image_path}} | {{caption}}"},
            },
            "3": {"class_type": "SaveImage", "inputs": {"images": ["7", 0]}},
        }
    )
    (tmp_path / "joint.json").write_text(json.dumps(graph), encoding="utf-8")
    comfyui_module._describe_workflow.cache_clear()
    one = _picture_ids(workflow_env.server)["busy_one.png"]
    r = _run(
        workflow_env.owner, "joint.json", picture_ids=[one], caption="dog", stack=False
    )
    assert r.status_code == 200, r.text
    assert fake_comfyui.submitted[0]["7"]["inputs"]["image"] == (
        f"pixlstash-{one}-unhashed.png | dog"
    )


def test_a_batch_that_fails_partway_returns_the_runs_it_started(
    workflow_env, edit_workflow, fake_comfyui, monkeypatch
):
    def submit_once(_url, workflow, _client_id=None):
        if fake_comfyui.submitted:
            raise HTTPException(status_code=502, detail="ComfyUI prompt failed")
        fake_comfyui.submitted.append(workflow)
        return {"prompt_id": "prompt-1"}

    monkeypatch.setattr(comfyui_module, "_submit_comfyui_prompt", submit_once)
    ids = _picture_ids(workflow_env.server)
    r = _run(
        workflow_env.owner,
        "edit.json",
        picture_ids=[ids["busy_one.png"], ids["busy_three.png"]],
        pictures=[{"node_id": "1", "picture_id": ids["busy_two.png"]}],
        stack=False,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "partial"
    assert [p["prompt_id"] for p in body["prompts"]] == ["prompt-1"]
    assert "ComfyUI prompt failed" in body["error"]


def test_a_run_names_the_missing_pictures_and_caps_the_batch(
    workflow_env, edit_workflow, fake_comfyui, monkeypatch
):
    owner = workflow_env.owner
    ids = _picture_ids(workflow_env.server)
    one, two, three = ids["busy_one.png"], ids["busy_two.png"], ids["busy_three.png"]
    picker = [{"node_id": "1", "picture_id": two}]
    r = _run(owner, "edit.json", picture_ids=[one, 987654], pictures=picker)
    assert r.status_code == 404 and "987654" in r.json()["detail"], r.text

    monkeypatch.setattr(comfyui_module, "MAX_RUNS_PER_REQUEST", 1)
    r = _run(owner, "edit.json", picture_ids=[one, three], pictures=picker)
    assert r.status_code == 400 and "at most 1" in r.json()["detail"], r.text
    assert fake_comfyui.submitted == []
    r = _run(owner, "edit.json", picture_ids=[one], pictures=picker, stack=False)
    assert r.status_code == 200, r.text


def test_setup_and_run_resolve_a_duplicated_fixed_picture_to_the_same_copy(
    workflow_env, edit_workflow, fake_comfyui
):
    server, owner = workflow_env.server, workflow_env.owner
    older = _picture_with_sha(server, "busy_two.png", "sha-run-duplicate")
    newer = _picture_with_sha(server, "busy_three.png", "sha-run-duplicate")
    r = owner.put(
        edit_workflow,
        json={
            "inputs": [
                {"node_id": "1", "mode": "fixed", "picture_id": newer},
                {"node_id": "2", "mode": "selection"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _by_node(r.json())["1"]["picture_id"] == older
    selected = _picture_ids(server)["busy_one.png"]
    r = _run(owner, "edit.json", picture_ids=[selected], stack=False)
    assert r.status_code == 200, r.text
    assert fake_comfyui.submitted[0]["1"]["inputs"]["image"] == (
        f"pixlstash-{older}-sha-run-duplicate.png"
    )


# ===========================================================================
# A LoRA from the shelf, put into the run (#1310)
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


def _lora_run(workflow_env, **body):
    ids = _picture_ids(workflow_env.server)
    return _run(
        workflow_env.owner,
        "lora.json",
        picture_ids=[ids["busy_one.png"]],
        stack=False,
        **body,
    )


def test_a_workflow_says_which_lora_slots_a_run_can_swap(
    workflow_env, lora_workflow, edit_workflow
):
    owner = workflow_env.owner
    r = owner.get(f"{API}/comfyui/workflows/lora.json/inputs")
    assert r.status_code == 200, r.text
    assert [(s["node_id"], s["by"], s["value"]) for s in r.json()["lora_slots"]] == [
        ("2", "filename", "whatever-is-there.safetensors"),
        ("3", "digest", "0" * 64),
    ]
    # The control: the workflow beside it has no loader and says so with an
    # empty list rather than by leaving the field out.
    r = owner.get(f"{API}/comfyui/workflows/edit.json/inputs")
    assert r.status_code == 200 and r.json()["lora_slots"] == [], r.text


def test_a_shelf_lora_goes_into_the_named_slot_and_no_other(
    workflow_env, lora_workflow, fake_comfyui
):
    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="2")
    assert r.status_code == 200, r.text
    submitted = fake_comfyui.submitted[0]
    # The name this ComfyUI lists, matched on the basename the shelf knows.
    assert submitted["2"]["inputs"]["lora_name"] == _COMFY_LORA_NAME
    # The other loader keeps the LoRA the workflow chose: swapping both would
    # load one file twice and lose the other.
    assert submitted["3"]["inputs"]["adapter_sha256"] == "0" * 64


def test_the_pixlstash_loader_takes_the_digest_without_asking_comfyui(
    workflow_env, lora_workflow, fake_comfyui
):
    # Nothing to resolve, so an unreachable ComfyUI is no obstacle either.
    lora_workflow.info = RuntimeError("Could not reach ComfyUI at http://127.0.0.1:1")
    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="3")
    assert r.status_code == 200, r.text
    submitted = fake_comfyui.submitted[0]
    assert submitted["3"]["inputs"]["adapter_sha256"] == _SHELF_LORA_SHA
    assert submitted["2"]["inputs"]["lora_name"] == "whatever-is-there.safetensors"


def test_the_path_this_shelf_scanned_wins_over_another_file_of_that_name(
    workflow_env, lora_workflow, fake_comfyui
):
    lora_workflow.info = {
        "LoraLoader": {
            "input": {
                "required": {"lora_name": [[_COMFY_LORA_NAME, _SHELF_LORA_RELPATH], {}]}
            }
        }
    }
    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="2")
    assert r.status_code == 200, r.text
    assert fake_comfyui.submitted[0]["2"]["inputs"]["lora_name"] == _SHELF_LORA_RELPATH


def test_a_workflow_with_two_loaders_will_not_guess_which_one(
    workflow_env, lora_workflow, fake_comfyui
):
    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA)
    assert r.status_code == 400, r.text
    assert "2 LoRA slots" in r.json()["detail"]
    assert "2 lora_name, 3 adapter_sha256" in r.json()["detail"]

    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="4")
    assert r.status_code == 400 and "not a LoRA loader" in r.text
    assert fake_comfyui.submitted == [] and fake_comfyui.uploads == []


def test_the_chosen_lora_wins_over_one_set_in_the_parameter_form(
    workflow_env, lora_workflow, fake_comfyui
):
    r = _lora_run(
        workflow_env,
        adapter_sha256=_SHELF_LORA_SHA,
        lora_node_id="2",
        values=[{"node_id": "2", "name": "lora_name", "value": "from-the-form.st"}],
    )
    assert r.status_code == 200, r.text
    assert fake_comfyui.submitted[0]["2"]["inputs"]["lora_name"] == _COMFY_LORA_NAME


def test_a_run_without_a_lora_leaves_the_workflows_own_choice(
    workflow_env, lora_workflow, fake_comfyui
):
    r = _lora_run(workflow_env)
    assert r.status_code == 200, r.text
    submitted = fake_comfyui.submitted[0]
    assert submitted["2"]["inputs"]["lora_name"] == "whatever-is-there.safetensors"
    assert submitted["3"]["inputs"]["adapter_sha256"] == "0" * 64


def test_a_workflow_with_no_lora_loader_is_refused_by_name(
    workflow_env, lora_workflow, edit_workflow, fake_comfyui
):
    ids = _picture_ids(workflow_env.server)
    r = _run(
        workflow_env.owner,
        "edit.json",
        picture_ids=[ids["busy_one.png"]],
        pictures=[{"node_id": "1", "picture_id": ids["busy_two.png"]}],
        adapter_sha256=_SHELF_LORA_SHA,
        stack=False,
    )
    assert r.status_code == 400, r.text
    assert "no LoRA loader" in r.json()["detail"]
    assert fake_comfyui.submitted == [] and fake_comfyui.uploads == []


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


def test_a_lora_goes_into_a_loader_added_only_when_asked(
    workflow_env, loaderless_workflow, fake_comfyui
):
    ids = _picture_ids(workflow_env.server)

    def run(**body):
        return _run(
            workflow_env.owner,
            "plain.json",
            picture_ids=[ids["busy_one.png"]],
            stack=False,
            adapter_sha256=_SHELF_LORA_SHA,
            **body,
        )

    # A bare adapter_sha256 never adds a node to the owner's graph.
    r = run()
    assert r.status_code == 400 and "insert_lora_loader" in r.text, r.text
    assert fake_comfyui.submitted == []

    r = run(insert_lora_loader=True)
    assert r.status_code == 200, r.text
    submitted = fake_comfyui.submitted[0]
    assert submitted["10"]["class_type"] == "LoraLoader"
    assert submitted["10"]["inputs"]["lora_name"] == _COMFY_LORA_NAME
    assert submitted["10"]["inputs"]["model"] == ["4", 0]
    assert submitted["3"]["inputs"]["model"] == ["10", 0]
    assert submitted["6"]["inputs"]["clip"] == ["10", 1]
    # The stored file is untouched: the loader is the run's, not the workflow's.
    r = workflow_env.owner.get(f"{API}/comfyui/workflows/plain.json/lora-insertion")
    assert r.json()["plan"] is not None

    # No loader, so no slot to name.
    r = run(insert_lora_loader=True, lora_node_id="4")
    assert r.status_code == 400 and "no slot to name" in r.text


def test_a_lora_the_shelf_or_comfyui_does_not_have_stops_before_any_upload(
    workflow_env, lora_workflow, fake_comfyui
):
    r = _lora_run(workflow_env, adapter_sha256=_h("nothing"), lora_node_id="2")
    assert r.status_code == 404 and "not on this PixlStash's shelf" in r.text

    # On the shelf, absent from the ComfyUI this would run on: that ComfyUI
    # lists LoRAs, just not this one under any name the shelf knows it by.
    lora_workflow.info = {
        "LoraLoader": {"input": {"required": {"lora_name": [["other.st"], {}]}}}
    }
    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="2")
    assert r.status_code == 400 and "not on the ComfyUI" in r.text

    # A loader whose file list ComfyUI does not enumerate (an empty combo reads
    # as "not listed", never as "nothing installed").
    lora_workflow.info = {
        "LoraLoader": {"input": {"required": {"lora_name": [[], {}]}}}
    }
    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="2")
    assert r.status_code == 400 and "will not guess" in r.text

    # A ComfyUI that cannot be asked at all is a 502, not a guess.
    lora_workflow.info = RuntimeError("Could not reach ComfyUI at http://127.0.0.1:1")
    r = _lora_run(workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="2")
    assert r.status_code == 502 and "could not ask ComfyUI" in r.text

    # Nothing reached ComfyUI for any of the three.
    assert fake_comfyui.submitted == [] and fake_comfyui.uploads == []


def test_only_a_file_a_lora_loader_can_load_is_accepted(
    workflow_env, lora_workflow, fake_comfyui
):
    """A checkpoint, a VAE or an engine is refused by name, an unknown is not.

    ``file_kind`` is an allow-list here: the shelf holds five other kinds and
    writing any of them into ``lora_name`` is a run that fails further in.
    """
    hub = workflow_env.server.hub
    unknown_sha, vae_sha = _h("unclassified-file"), _h("a-vae")
    with hub.transaction() as conn:
        conn.executemany(
            "INSERT INTO model (file_kind, filename, sha256, provenance) "
            "VALUES (?, ?, ?, 'scanned')",
            [
                ("unknown", "mystery.safetensors", unknown_sha),
                ("vae", "example-vae.safetensors", vae_sha),
            ],
        )
    try:
        r = _lora_run(
            workflow_env, adapter_sha256=_h("realvisxl-digest"), lora_node_id="2"
        )
        assert r.status_code == 400 and "is a checkpoint" in r.text

        r = _lora_run(workflow_env, adapter_sha256=vae_sha, lora_node_id="2")
        assert r.status_code == 400 and "is a vae" in r.text

        # Unclassified is first-class on this shelf and usually an adapter the
        # header reader could not place, so it is offered, not refused: it gets
        # as far as the name check.
        r = _lora_run(workflow_env, adapter_sha256=unknown_sha, lora_node_id="2")
        assert r.status_code == 400 and "not on the ComfyUI" in r.text
    finally:
        with hub.transaction() as conn:
            conn.execute(
                "DELETE FROM model WHERE sha256 IN (?, ?)", (unknown_sha, vae_sha)
            )
    assert fake_comfyui.submitted == [] and fake_comfyui.uploads == []


def test_edit_with_comfyui_swaps_the_same_way_the_run_panel_does(
    workflow_env, lora_workflow, fake_comfyui
):
    """run_i2i is the overlay's "Edit with ComfyUI", and it runs a saved file too.

    Same body, same refusals: the swap belongs to every route that runs a
    workflow, not only to the run panel's own (#1310).
    """
    ids = _picture_ids(workflow_env.server)
    r = workflow_env.owner.post(
        f"{API}/comfyui/run_i2i",
        json={
            "picture_ids": [ids["busy_one.png"]],
            "workflow_name": "lora.json",
            "stack": False,
            "adapter_sha256": _SHELF_LORA_SHA,
            "lora_node_id": "2",
        },
    )
    assert r.status_code == 200, r.text
    submitted = fake_comfyui.submitted[0]
    assert submitted["2"]["inputs"]["lora_name"] == _COMFY_LORA_NAME
    assert submitted["3"]["inputs"]["adapter_sha256"] == "0" * 64

    # And the same refusal, before anything is uploaded for a second run.
    fake_comfyui.submitted.clear()
    fake_comfyui.uploads.clear()
    r = workflow_env.owner.post(
        f"{API}/comfyui/run_i2i",
        json={
            "picture_ids": [ids["busy_one.png"]],
            "workflow_name": "lora.json",
            "stack": False,
            "adapter_sha256": _SHELF_LORA_SHA,
        },
    )
    assert r.status_code == 400 and "2 LoRA slots" in r.text
    assert fake_comfyui.submitted == [] and fake_comfyui.uploads == []


def test_the_workflow_list_says_which_files_have_a_lora_loader(
    workflow_env, lora_workflow, edit_workflow
):
    """The menus that run a workflow read the list, not a request per file."""
    r = workflow_env.owner.get(f"{API}/comfyui/workflows")
    assert r.status_code == 200, r.text
    slots = {w["name"]: w.get("lora_slots") for w in r.json()["workflows"]}
    assert [s["node_id"] for s in slots["lora.json"]] == ["2", "3"]
    assert slots["edit.json"] == []


def test_a_stacker_swaps_the_slot_named_and_leaves_its_other_loras(
    workflow_env, lora_workflow, fake_comfyui, tmp_path
):
    """A slot is a node AND a field: one stacker carries several LoRAs.

    Naming the node alone used to keep every field on it and write the chosen
    LoRA into all of them - the style and the character both gone, a disabled
    slot switched on, one file loaded three times.
    """
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "{{image_path}}"}},
        "7": {
            "class_type": "CR LoRA Stack",
            "inputs": {
                "lora_name_1": "example-style.safetensors",
                "lora_name_2": "example-character.safetensors",
                "lora_name_3": "None",
            },
        },
        "9": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }
    bound, _changed = workflow_bindings.migrate_placeholders(graph)
    (tmp_path / "stack.json").write_text(json.dumps(bound), encoding="utf-8")
    lora_workflow.info = {
        "CR LoRA Stack": {
            "input": {
                "required": {
                    f"lora_name_{n}": [["None", _COMFY_LORA_NAME], {}]
                    for n in (1, 2, 3)
                }
            }
        }
    }
    ids = _picture_ids(workflow_env.server)

    def run(**body):
        return _run(
            workflow_env.owner,
            "stack.json",
            picture_ids=[ids["busy_one.png"]],
            stack=False,
            adapter_sha256=_SHELF_LORA_SHA,
            **body,
        )

    # The node alone is still three slots, and says which.
    r = run(lora_node_id="7")
    assert r.status_code == 400, r.text
    assert "3 LoRA slots" in r.json()["detail"]
    assert "7 lora_name_1, 7 lora_name_2, 7 lora_name_3" in r.json()["detail"]
    assert fake_comfyui.submitted == []

    r = run(lora_node_id="7", lora_field="lora_name_2")
    assert r.status_code == 200, r.text
    inputs = fake_comfyui.submitted[0]["7"]["inputs"]
    assert inputs["lora_name_2"] == _COMFY_LORA_NAME
    assert inputs["lora_name_1"] == "example-style.safetensors"
    assert inputs["lora_name_3"] == "None"

    r = run(lora_node_id="7", lora_field="lora_name_9")
    assert r.status_code == 400 and "not a LoRA slot" in r.text


def test_an_empty_slot_name_counts_as_not_sent(
    workflow_env, lora_workflow, fake_comfyui
):
    """A client with nothing chosen yet sends "", which must not name node "".

    Two slots and an empty name is still an unnamed choice between two - the
    refusal that lists them, not "Node  is not a LoRA loader".
    """
    r = _lora_run(
        workflow_env, adapter_sha256=_SHELF_LORA_SHA, lora_node_id="", lora_field=""
    )
    assert r.status_code == 400, r.text
    assert "2 LoRA slots" in r.json()["detail"]
    assert "is not a LoRA loader" not in r.json()["detail"]


def test_a_share_link_sees_no_lora_filenames_on_the_workflow_list(
    workflow_env, lora_workflow
):
    """The list is open to share tokens, and a slot's value is the owner's inventory.

    /models/ and /adapters/ keep the model inventory from those tokens, so the
    list must not hand the same filenames and digests out through its slots.
    Both directions: the scoped token still reads the list (over-blocking is
    its own regression) and the owner still gets the values from /inputs.
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

    # The owner's own read of the file's setup keeps them.
    r = workflow_env.owner.get(f"{API}/comfyui/workflows/lora.json/inputs")
    assert r.status_code == 200, r.text
    assert "whatever-is-there.safetensors" in {
        s["value"] for s in r.json()["lora_slots"]
    }
    # And the scoped token cannot reach that read at all.
    r = client.get(f"{API}/comfyui/workflows/lora.json/inputs")
    assert r.status_code == 403, r.text


def test_a_swap_reaches_a_document_stored_with_its_graph_one_level_down():
    """The import dialog stores {"prompt": graph}; detection reads inside it, so must the write."""
    graph = {"5": {"class_type": "LoraLoader", "inputs": {"lora_name": "old.st"}}}
    swap = {
        "adapter": {"sha256": "b" * 64, "filenames": ["new.st"]},
        "targets": [
            {
                "node_id": "5",
                "class_type": "LoraLoader",
                "field": "lora_name",
                "by": "filename",
            }
        ],
        "object_info": {
            "LoraLoader": {"input": {"required": {"lora_name": [["new.st"], {}]}}}
        },
    }
    wrapped = {"prompt": graph}
    comfyui_module._apply_lora_swap(wrapped, swap, "test")
    assert wrapped["prompt"]["5"]["inputs"]["lora_name"] == "new.st"

    # A slot the instance does not have is refused, never run with its own LoRA.
    with pytest.raises(HTTPException) as refused:
        comfyui_module._apply_lora_swap({"prompt": {}}, swap, "test")
    assert refused.value.status_code == 500


def test_a_ui_format_file_is_refused_as_what_it_is_before_the_shelf_is_asked():
    """No hub here at all: the refusal must come before the lookup, and name the format."""
    with pytest.raises(HTTPException) as refused:
        comfyui_module._resolve_lora_swap(
            None, {"adapter_sha256": "b" * 64}, None, "test", "http://127.0.0.1:1"
        )
    assert refused.value.status_code == 400
    assert "UI format" in refused.value.detail


# ===========================================================================
# The cards (v1.12 B3)
# ===========================================================================
#
# **The payload shape is not this file's to choose.** `GET /workflows/cards` is
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
    "key",
    "name",
    "type",
    "imported",
    "models",
    "loras",
    "differs_by",
    "picture_count",
    "rating",
    "covers",
    "stack_size",
    "saved_recipe_count",
    "defaults",
}


def _cards(owner) -> dict:
    payload = owner.get(f"{API}/workflows/cards")
    assert payload.status_code == 200, payload.text
    return payload.json()


def _by_key(payload) -> dict:
    return {card["key"]: card for card in payload["cards"]}


def _detail(owner, workflow_key) -> dict:
    r = owner.get(f"{API}/workflows/cards/{workflow_key}")
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


def test_a_card_is_served_in_the_shape_the_frontend_already_reads(workflow_env):
    """The merged contract, field by field.

    `utils/workflowCard.js` is shipped and `frontend_architecture.md` promises
    it needs no mapping layer, so a renamed field here is a broken screen
    rather than a caller to update. `models` and `loras` carry B1's own
    vocabulary (`structural` | `recipe`) for the same reason: translating it is
    how the solid/dashed meaning gets inverted.
    """
    card = _by_key(_cards(workflow_env.owner))[BUSY_CARD]
    assert _CONTRACT_FIELDS <= set(card), _CONTRACT_FIELDS - set(card)
    assert card["key"] == BUSY_CARD
    assert card["type"] == "txt2img"
    assert card["saved_recipe_count"] == 0
    assert [model["kind"] for model in card["models"]] == ["checkpoint"]
    assert card["models"][0]["name"] == "realvisxl.safetensors"
    # One LoRA slot, guessed `recipe` from its filename, so it is an anonymous
    # slot rather than a named file: a character LoRA is the recipe's business.
    assert card["loras"] == [{"name": None, "kind": "lora", "mark": "recipe"}]


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
    assert cards[BUSY_CARD]["last_used"].startswith("2026-08-13")
    # BINNED is dropped from the grid (its pictures are all in the scrapheap),
    # so its null is asserted where it is still served: its own detail route.
    assert _detail(workflow_env.owner, BINNED_CARD)["card"]["last_used"] is None


def test_a_card_is_never_nameless(workflow_env):
    """`name` may not be null, and most cards have no name of their own.

    It is written only on an explicit rename, so null is the dominant case
    rather than an edge -- and it is the card's only identifying text row,
    while the ⓘ panel puts it straight into an ``aria-label``. Served as null
    the row renders empty and the label reads "About null".

    The fallback is the workflow file that runs the card, without its
    extension; a card with neither a name nor a file gets a stand-in.
    """
    cards = _by_key(_cards(workflow_env.owner))
    # BUSY has no name and no file: the stand-in, not an empty string.
    assert cards[BUSY_CARD]["name"] == "Untitled workflow"
    # The hidden card has both a name and a file, and the owner's name wins.
    assert _detail(workflow_env.owner, HIDDEN_CARD)["card"]["name"] == (
        "A workflow I hid"
    )
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_file "
            "(workflow_name, topology_hash, structural_hash, workflow_key) "
            "VALUES ('Flux2 portrait.json', ?, ?, ?)",
            (BUSY_TOPOLOGY, BUSY_RECIPE_A, BUSY_CARD),
        )
    named = _by_key(_cards(workflow_env.owner))[BUSY_CARD]
    assert named["name"] == "Flux2 portrait"


def test_a_stack_member_opened_alone_still_says_it_is_in_a_stack(workflow_env):
    """Its difference chips only mean something beside the size that explains them.

    `factChips` branches on `stack_size`: at 1 it drops the "differs by" label
    and renders the chips as plain facts, so "other checkpoint" arrives as a
    statement about a cover the payload never names. The member therefore
    carries the stack it is in, not the stack it covers.
    """
    member = _detail(workflow_env.owner, FORGOTTEN_CARD)["card"]
    assert member["differs_by"], "the member has nothing to explain"
    assert member["stack_size"] == 2
    assert member["member_keys"] == [BUSY_CARD]
    # And the cover names it back, so the two agree about the same stack.
    cover = _by_key(_cards(workflow_env.owner))[BUSY_CARD]
    assert cover["stack_size"] == 2
    assert cover["member_keys"] == [FORGOTTEN_CARD]


def test_a_card_outside_a_stack_carries_no_difference_chips(workflow_env):
    """The other direction: chips and size never disagree.

    Unstacking FORGOTTEN leaves both cards standing alone, and a lone card has
    nothing to differ from -- so neither may keep chips earned against a cover
    that is no longer beside it.
    """
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_unstacked (workflow_key) VALUES (?)",
            (FORGOTTEN_CARD,),
        )
    for key in (BUSY_CARD, FORGOTTEN_CARD):
        card = _detail(workflow_env.owner, key)["card"]
        assert card["stack_size"] == 1, key
        assert card["differs_by"] == [], key


def test_the_grid_is_one_card_per_key_not_one_per_variant(workflow_env):
    """A card is the unit, and BUSY's two variants are one card, not two.

    The hidden card and the one-off are the grid's two exclusions and are
    asserted by name rather than by a count, so the test cannot pass because
    something else went missing. FORGOTTEN is absent because it is stacked
    under BUSY, not because it was dropped -- the next test holds that.
    """
    payload = _cards(workflow_env.owner)
    cards = _by_key(payload)
    assert set(cards) == {BUSY_CARD}
    assert cards[BUSY_CARD]["variant_count"] == 2
    assert cards[BUSY_CARD]["topology_hash"] == BUSY_TOPOLOGY
    assert _detail(workflow_env.owner, FORGOTTEN_CARD)["card"]["variant_count"] == 1


def test_a_card_adds_up_every_variants_kept_pictures_and_ratings(workflow_env):
    """Four kept pictures across two variants; two of them carry a star.

    ``busy_three`` is rated **0**, which is a rating somebody cleared and not a
    rating: counting it would drag the card's mean from 4.5 to 3.0. The two
    soft-deleted pictures belong to these cards and are rated 5; counting them
    would move every figure on this line.
    """
    card = _by_key(_cards(workflow_env.owner))[BUSY_CARD]
    assert card["picture_count"] == 5
    assert card["rating"] == pytest.approx(4.5)
    forgotten = _detail(workflow_env.owner, FORGOTTEN_CARD)["card"]
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
    card = _by_key(_cards(workflow_env.owner))[BINNED_CARD]
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
    assert _by_key(payload)[BUSY_CARD]["rank"] == pytest.approx(_BUSY_RANK)
    detail = _detail(workflow_env.owner, FORGOTTEN_CARD)["card"]
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
    card = _by_key(_cards(workflow_env.owner))[BUSY_CARD]
    ids = _picture_ids_by_path(workflow_env.server)
    assert card["covers"] == [
        f"/pictures/thumbnails/{ids['busy_one.png']}.webp?v=0",
        f"/pictures/thumbnails/{ids['busy_two.png']}.webp?v=0",
        f"/pictures/thumbnails/{ids['busy_three.png']}.webp?v=0",
    ]


def test_an_owner_chosen_cover_leads_the_strip(workflow_env):
    """The cover the owner picked, which is stored by content and not by id.

    A picture id is reused by SQLite the moment the next import lands, so the
    row names a ``pixel_sha``; resolving it is the one extra query this feature
    costs, and only on a library where somebody has actually chosen one.
    """
    ids = _picture_ids_by_path(workflow_env.server)
    chosen = _h("chosen-cover-pixels")

    def stamp(session):
        picture = session.get(Picture, ids["busy_four.png"])
        picture.pixel_sha = chosen
        session.add(picture)
        session.commit()

    workflow_env.server.vault.db.run_task(stamp, priority=DBPriority.IMMEDIATE)
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_cover (library_uuid, workflow_key, pixel_sha) "
            "VALUES (?, ?, ?)",
            (workflow_env.server.vault.library_uuid, BUSY_CARD, chosen),
        )
    card = _by_key(_cards(workflow_env.owner))[BUSY_CARD]
    assert card["covers"][0] == (
        f"/pictures/thumbnails/{ids['busy_four.png']}.webp?v=0"
    )
    # Still three, and the rest keep their order behind it.
    assert card["covers"][1:] == [
        f"/pictures/thumbnails/{ids['busy_one.png']}.webp?v=0",
        f"/pictures/thumbnails/{ids['busy_two.png']}.webp?v=0",
    ]

    # Bin the chosen cover and the card falls back to its computed strip rather
    # than showing a hole or pointing at a picture in the Scrapheap. The
    # `workflow_cover` row is deliberately left alone: it is the owner's
    # choice, and a restore has to bring it back.
    def bin_it(session):
        picture = session.get(Picture, ids["busy_four.png"])
        picture.deleted = True
        session.add(picture)
        session.commit()

    workflow_env.server.vault.db.run_task(bin_it, priority=DBPriority.IMMEDIATE)
    card = _by_key(_cards(workflow_env.owner))[BUSY_CARD]
    assert card["covers"] == [
        f"/pictures/thumbnails/{ids['busy_one.png']}.webp?v=0",
        f"/pictures/thumbnails/{ids['busy_two.png']}.webp?v=0",
        f"/pictures/thumbnails/{ids['busy_three.png']}.webp?v=0",
    ]


def test_an_empty_chosen_cover_covers_nothing_rather_than_everything(
    workflow_env,
):
    """A blank ``pixel_sha`` must name no picture, not every card's picture.

    ``workflow_cover.pixel_sha`` is ``NOT NULL`` but an empty string satisfies
    that, and looking a card up with ``chosen.get(key, "")`` would give every
    card WITHOUT a chosen cover the same empty key -- so one blank row plus one
    picture stored with a blank sha would hand that picture to the whole grid
    as its cover. Nothing writes these rows yet, which is exactly why it is
    worth pinning before something does.
    """
    ids = _picture_ids_by_path(workflow_env.server)

    def stamp(session):
        picture = session.get(Picture, ids["forgotten.png"])
        picture.pixel_sha = ""
        session.add(picture)
        session.commit()

    workflow_env.server.vault.db.run_task(stamp, priority=DBPriority.IMMEDIATE)
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_cover (library_uuid, workflow_key, pixel_sha) "
            "VALUES (?, ?, '')",
            (workflow_env.server.vault.library_uuid, HIDDEN_CARD),
        )
    card = _by_key(_cards(workflow_env.owner))[BUSY_CARD]
    assert card["covers"] == [
        f"/pictures/thumbnails/{ids['busy_one.png']}.webp?v=0",
        f"/pictures/thumbnails/{ids['busy_two.png']}.webp?v=0",
        f"/pictures/thumbnails/{ids['busy_three.png']}.webp?v=0",
    ]


def test_a_hidden_card_is_counted_never_listed_and_still_opens(workflow_env):
    """Hiding is a decision about the grid, not a deletion.

    The detail route is the half that gets forgotten: a card nobody can reach
    once it is hidden cannot be un-hidden. The hidden card carries a workflow
    file so it is not ALSO a one-off -- without that this passes whichever
    clause removed it.
    """
    payload = _cards(workflow_env.owner)
    assert payload["hidden"] == 1
    assert HIDDEN_CARD not in _by_key(payload)
    body = _detail(workflow_env.owner, HIDDEN_CARD)
    assert body["hidden"] is True
    assert body["card"]["name"] == "A workflow I hid"


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
    assert _by_key(payload)[BINNED_CARD]["imported"] is True


def _save_recipe(server, workflow_key, name="A look I kept"):
    """Put one saved recipe on a card, the way `POST /recipes` would."""

    def write(session):
        session.add(SavedRecipe(name=name, workflow_key=workflow_key, prompt="a cat"))
        session.commit()

    server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


def test_a_card_counts_the_recipes_saved_on_it(workflow_env):
    """``saved_recipe_count`` is the real number, not a placeholder.

    ⓘ always draws the "Saved recipes" row, so a hardcoded zero does not read
    as "not implemented yet" -- it reads as "you have saved none", which is a
    different and wrong statement once B6's table exists.
    """
    assert _by_key(_cards(workflow_env.owner))[BUSY_CARD]["saved_recipe_count"] == 0
    _save_recipe(workflow_env.server, BUSY_CARD)
    _save_recipe(workflow_env.server, BUSY_CARD, name="And another")
    cards = _by_key(_cards(workflow_env.owner))
    assert cards[BUSY_CARD]["saved_recipe_count"] == 2
    # Keyed by card, so a recipe on one does not leak onto its stack partner.
    assert (
        _detail(workflow_env.owner, FORGOTTEN_CARD)["card"]["saved_recipe_count"] == 0
    )


def test_a_saved_recipe_takes_a_card_out_of_the_one_off_count(workflow_env):
    """The fourth clause of the one-off rule, which B6's table made reachable.

    BINNED has no kept picture, no rating and no file, so the grid folds it
    into a count. Saving a look on it is the plainest statement that somebody
    means to run it again, so it has to come back into the grid -- otherwise
    the owner's own recipe sits on a card they can no longer see.
    """
    assert _cards(workflow_env.owner)["one_offs"] == 1
    _save_recipe(workflow_env.server, BINNED_CARD)
    payload = _cards(workflow_env.owner)
    assert payload["one_offs"] == 0
    assert _by_key(payload)[BINNED_CARD]["saved_recipe_count"] == 1


def _stack_row(server, stack_id, kind, core_hash, members):
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_stack (stack_id, kind, core_hash) VALUES (?, ?, ?)",
            (stack_id, kind, core_hash),
        )
        conn.executemany(
            "INSERT INTO workflow_stack_member (stack_id, workflow_key, position) "
            "VALUES (?, ?, ?)",
            [(stack_id, key, position) for position, key in enumerate(members)],
        )


def test_cards_sharing_a_core_hash_stack_behind_the_higher_ranked(workflow_env):
    """The automatic group exists with no stack row behind it at all.

    The grid draws ONE card per stack -- the cover -- so the member is absent
    from `cards` and named on the cover instead. `stack_size` of 2 is what
    makes it a stack to its reader.
    """
    cards = _by_key(_cards(workflow_env.owner))
    assert cards[BUSY_CARD]["stack_size"] == 2
    assert cards[BUSY_CARD]["member_keys"] == [FORGOTTEN_CARD]
    assert FORGOTTEN_CARD not in cards


def test_a_stack_shows_the_union_of_its_members_difference_chips(workflow_env):
    """The chips belong to the drawn card, because the cover is what is drawn.

    The cover has none of its own: it is what the others are compared against.
    """
    cards = _by_key(_cards(workflow_env.owner))
    assert cards[BUSY_CARD]["differs_by"], "a stack with no difference chips"
    member = _detail(workflow_env.owner, FORGOTTEN_CARD)["card"]
    assert set(member["differs_by"]) <= set(cards[BUSY_CARD]["differs_by"])


def test_unstacking_a_card_takes_it_out_of_the_automatic_group(workflow_env):
    """And a group of one is not a stack, so both cards are drawn on their own."""
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_unstacked (workflow_key) VALUES (?)",
            (FORGOTTEN_CARD,),
        )
    cards = _by_key(_cards(workflow_env.owner))
    assert set(cards) == {BUSY_CARD, FORGOTTEN_CARD}
    assert cards[BUSY_CARD]["stack_size"] == 1
    assert cards[BUSY_CARD]["member_keys"] == []


def test_a_member_row_for_a_card_that_left_the_group_is_ignored(workflow_env):
    """A stored position must never re-admit a card whose core hash moved.

    The row is written against LONE_CORE while both cards carry SHARED_CORE, so
    both of its positions describe a group neither card is in any more.
    Honouring them would hand the cover to FORGOTTEN, which ranks second -- so
    the grid falling back to cover rank is the whole assertion.
    """
    _stack_row(
        workflow_env.server, "s-left", "auto", LONE_CORE, [FORGOTTEN_CARD, BUSY_CARD]
    )
    cards = _by_key(_cards(workflow_env.owner))
    assert set(cards) == {BUSY_CARD}
    assert cards[BUSY_CARD]["member_keys"] == [FORGOTTEN_CARD]


def test_a_card_that_joined_a_group_since_is_appended_not_prepended(workflow_env):
    """FORGOTTEN has a stored position and BUSY does not.

    BUSY ranks higher, so ordering by rank alone would make it the cover; the
    owner's order has to win for the cards it names, and the newcomer goes
    after them rather than in front. The cover is therefore FORGOTTEN, which is
    the card the grid draws.
    """
    _stack_row(workflow_env.server, "s-auto", "auto", SHARED_CORE, [FORGOTTEN_CARD])
    cards = _by_key(_cards(workflow_env.owner))
    assert set(cards) == {FORGOTTEN_CARD}
    assert cards[FORGOTTEN_CARD]["member_keys"] == [BUSY_CARD]


def test_a_manual_assignment_beats_the_automatic_group(workflow_env):
    """A manual stack is the owner's, so it wins over the core hash."""
    _stack_row(
        workflow_env.server, "s-manual", "manual", None, [FORGOTTEN_CARD, BUSY_CARD]
    )
    cards = _by_key(_cards(workflow_env.owner))
    assert set(cards) == {FORGOTTEN_CARD}
    assert cards[FORGOTTEN_CARD]["stack_size"] == 2
    assert cards[FORGOTTEN_CARD]["member_keys"] == [BUSY_CARD]


def _defaults(owner, workflow_key) -> dict:
    """The card's defaults keyed by their address, which is what names a slot.

    By address and not by input name, because the busy card has two samplers:
    keying on the name alone would silently assert against whichever of them
    happened to sort first.
    """
    return {
        (row["slot_label"], row["input_name"]): row
        for row in _detail(owner, workflow_key)["card"]["defaults"]
    }


def test_defaults_are_the_mode_over_the_cards_best_pictures(workflow_env):
    """30 steps twice beats nothing else; the two cfg values tie and resolve
    the same way on every read, which is what stops a card's defaults moving
    when nothing has changed. The binned 5-star run used 44 of each."""
    defaults = _defaults(workflow_env.owner, BUSY_CARD)
    sampler = _slot_label(BUSY_RECIPE_A, "3")
    assert defaults[(sampler, "steps")]["value"] == 30
    assert defaults[(sampler, "steps")]["provenance"] == "best"
    assert defaults[(sampler, "cfg")]["value"] == 8.0
    # Two slots offer `steps` and two offer `cfg`, and ⓘ keys its list on the
    # label: two rows called "steps" is a duplicate `v-for` key, and one of
    # them silently replaces the other on screen.
    rows = _detail(workflow_env.owner, BUSY_CARD)["card"]["defaults"]
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
    defaults = _defaults(workflow_env.owner, FORGOTTEN_CARD)
    address = (_slot_label(FORGOTTEN_RECIPE, "4"), "steps")
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
    defaults = _defaults(workflow_env.owner, FORGOTTEN_CARD)
    address = (_slot_label(FORGOTTEN_RECIPE, "4"), "steps")
    assert defaults[address]["value"] == 77
    assert defaults[address]["provenance"] == "all"


def test_an_owner_override_replaces_a_default_and_says_it_was_edited(
    workflow_env,
):
    """And it is addressed by slot label, never by node id."""
    with workflow_env.server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_default_override "
            "(workflow_key, slot_label, input_name, value) VALUES (?, ?, 'steps', '8')",
            (BUSY_CARD, _slot_label(BUSY_RECIPE_A, "3")),
        )
    defaults = _defaults(workflow_env.owner, BUSY_CARD)
    sampler = _slot_label(BUSY_RECIPE_A, "3")
    assert defaults[(sampler, "steps")]["value"] == "8"
    assert defaults[(sampler, "steps")]["provenance"] == "edited"
    # Only that one slot: the refiner's own `steps` and every other parameter
    # are still read off the pictures.
    refiner = _slot_label(BUSY_RECIPE_A, "4")
    assert defaults[(refiner, "steps")]["value"] == 30
    assert defaults[(refiner, "steps")]["provenance"] == "best"
    assert defaults[(sampler, "cfg")]["provenance"] == "best"


def test_the_card_detail_lists_its_own_variants_and_not_its_topologys(
    workflow_env,
):
    """A topology can carry several cards, so the variant list is filtered by
    the card and not by the graph it is a binding of."""
    body = _detail(workflow_env.owner, BUSY_CARD)
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
    r = workflow_env.owner.get(f"{API}/workflows/cards/{BUSY_CARD}/pictures")
    assert r.status_code == 200, r.text
    assert r.json() == [
        ids["busy_four.png"],
        ids["busy_three.png"],
        ids["busy_two.png"],
        ids["busy_one.png"],
        ids["busy_unrated.png"],
    ]
    limited = workflow_env.owner.get(
        f"{API}/workflows/cards/{BUSY_CARD}/pictures?limit=1"
    )
    assert limited.json() == [ids["busy_four.png"]]


def test_an_unknown_card_is_a_404_and_a_malformed_key_a_422(workflow_env):
    """Both card routes, because a key is a content address: an empty 200 would
    read as "this card has nothing" rather than "this machine has no such card".
    """
    unknown = _h("nosuchcard")
    for path in (
        f"{API}/workflows/cards/{unknown}",
        f"{API}/workflows/cards/{unknown}/pictures",
    ):
        assert_real_route(workflow_env.server.api, "GET", path)
        assert workflow_env.owner.get(path).status_code == 404, path
    for path in (
        f"{API}/workflows/cards/not-a-digest",
        f"{API}/workflows/cards/not-a-digest/pictures",
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
        conn.execute(
            "INSERT INTO workflow_topology_core (topology_hash, core_hash, "
            "core_version, workflow_type, slots) VALUES (?, ?, ?, 'txt2img', ?)",
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


def _attr_row(server, key: str):
    return server.hub.fetchone(
        "SELECT name, notes, hidden FROM workflow_attr WHERE workflow_key = ?", (key,)
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
    stack_id = f"{AUTO_STACK_PREFIX}{SHARED_CORE}"
    for method, path, body in (
        ("PATCH", f"{API}/workflows/{BUSY_CARD}", {"name": "nope"}),
        ("PUT", f"{API}/workflows/{BUSY_CARD}/slots", {"marks": {}}),
        ("PUT", f"{API}/workflows/{BUSY_CARD}/defaults", {"defaults": []}),
        ("PUT", f"{API}/workflows/{BUSY_CARD}/pins", {"pins": []}),
        ("PUT", f"{API}/workflows/{BUSY_CARD}/inputs", {"inputs": []}),
        ("POST", f"{API}/workflows/{BUSY_CARD}/unstack", None),
        ("POST", f"{API}/workflows/stacks", {"keys": [BUSY_CARD, FORGOTTEN_CARD]}),
        ("PUT", f"{API}/workflows/stacks/{stack_id}/order", {"keys": [BUSY_CARD]}),
        ("POST", f"{API}/workflows/stacks/{stack_id}/unstack", None),
        ("POST", f"{API}/workflows/run", {"workflow_key": BUSY_CARD}),
        ("POST", f"{API}/workflows/run/preflight", {"workflow_key": BUSY_CARD}),
        ("POST", f"{API}/workflows/{BUSY_CARD}/duplicate", None),
        ("POST", f"{API}/workflows/{BUSY_CARD}/insert-lora-loader", None),
        ("DELETE", f"{API}/workflows/{BUSY_CARD}", None),
    ):
        assert_real_route(workflow_env.server.api, method, path)
        r = client.request(method, path, json=body)
        assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"
    # The positive control, and it is what makes the nine refusals above mean
    # something: the same gesture from the owner lands.
    assert (
        workflow_env.owner.patch(
            f"{API}/workflows/{BUSY_CARD}", json={"name": "Owner can"}
        ).status_code
        == 200
    )


def test_naming_a_card_shows_on_the_grid_and_clearing_it_goes_back(workflow_env):
    """PATCH writes the fields sent and leaves the rest; null clears."""
    owner = workflow_env.owner
    r = owner.patch(
        f"{API}/workflows/{BUSY_CARD}",
        json={"name": "My portrait workflow", "notes": "cfg 7, always"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["card"]["name"] == "My portrait workflow"
    assert r.json()["notes"] == "cfg 7, always"

    grid = owner.get(f"{API}/workflows/cards").json()
    named = {card["key"]: card["name"] for card in grid["cards"]}
    assert named[BUSY_CARD] == "My portrait workflow"

    # Notes alone: the name must stand rather than be cleared by omission.
    r = owner.patch(f"{API}/workflows/{BUSY_CARD}", json={"notes": "cfg 8 now"})
    assert r.json()["card"]["name"] == "My portrait workflow"
    assert r.json()["notes"] == "cfg 8 now"

    r = owner.patch(f"{API}/workflows/{BUSY_CARD}", json={"name": None})
    assert r.status_code == 200, r.text
    # Back to the fallback, which is the file that runs it or the stand-in.
    assert r.json()["card"]["name"] == UNNAMED_CARD


def test_hiding_a_card_takes_it_off_the_grid_and_it_still_opens(workflow_env):
    """Hiding is a decision about the grid, never a deletion."""
    owner = workflow_env.owner
    before = owner.get(f"{API}/workflows/cards").json()
    assert BUSY_CARD in {card["key"] for card in before["cards"]}

    assert (
        owner.patch(f"{API}/workflows/{BUSY_CARD}", json={"hidden": True}).status_code
        == 200
    )
    after = owner.get(f"{API}/workflows/cards").json()
    assert BUSY_CARD not in {card["key"] for card in after["cards"]}
    assert after["hidden"] == before["hidden"] + 1

    detail = owner.get(f"{API}/workflows/cards/{BUSY_CARD}")
    assert detail.status_code == 200
    assert detail.json()["hidden"] is True


def test_a_stack_whose_members_are_all_hidden_is_hidden(workflow_env):
    """BUSY and FORGOTTEN share a core hash, so they are one stack.

    Hiding both must leave the grid drawing neither, and counting both: a
    stack is hidden when its members are, because the grid draws the cover and
    a hidden cover is not drawn.
    """
    owner = workflow_env.owner
    cover = owner.get(f"{API}/workflows/cards").json()["cards"]
    assert any(card["stack_size"] == 2 for card in cover), (
        "the fixture's stack is gone; this test would pass on nothing"
    )
    for key in (BUSY_CARD, FORGOTTEN_CARD):
        assert (
            owner.patch(f"{API}/workflows/{key}", json={"hidden": True}).status_code
            == 200
        )
    grid = owner.get(f"{API}/workflows/cards").json()
    assert not {BUSY_CARD, FORGOTTEN_CARD} & {card["key"] for card in grid["cards"]}


def test_a_cards_overrides_pins_and_inputs_are_written_whole(workflow_env):
    """The three whole-set writes, each read back where something reads it."""
    owner, hub = workflow_env.owner, workflow_env.server.hub
    r = owner.put(
        f"{API}/workflows/{BUSY_CARD}/defaults",
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
    assert edited[("slot-a", "steps")]["value"] == "42"
    assert edited[("slot-a", "steps")]["provenance"] == "edited"
    # A bool is stored the way a graph writes one, not as Python's `True`.
    assert edited[("slot-a", "keep")]["value"] == "true"
    assert default_overrides(hub, BUSY_CARD)[("slot-a", "keep")] == "true"

    # Whole, so a second write with one entry leaves one entry.
    owner.put(
        f"{API}/workflows/{BUSY_CARD}/defaults",
        json={
            "defaults": [{"slot_label": "slot-a", "input_name": "cfg", "value": 7.5}]
        },
    )
    assert set(default_overrides(hub, BUSY_CARD)) == {("slot-a", "cfg")}

    assert (
        owner.put(
            f"{API}/workflows/{BUSY_CARD}/pins",
            json={"pins": [{"slot_label": "slot-a", "input_name": "steps"}]},
        ).status_code
        == 200
    )
    assert json.loads(
        hub.fetchone(
            "SELECT pins FROM workflow_key_pins WHERE workflow_key = ?", (BUSY_CARD,)
        )["pins"]
    ) == [["slot-a", "steps"]]
    # `[]` is somebody who unpinned everything; null forgets the choice.
    owner.put(f"{API}/workflows/{BUSY_CARD}/pins", json={"pins": []})
    assert (
        json.loads(
            hub.fetchone(
                "SELECT pins FROM workflow_key_pins WHERE workflow_key = ?",
                (BUSY_CARD,),
            )["pins"]
        )
        == []
    )
    owner.put(f"{API}/workflows/{BUSY_CARD}/pins", json={"pins": None})
    assert (
        hub.fetchone(
            "SELECT pins FROM workflow_key_pins WHERE workflow_key = ?", (BUSY_CARD,)
        )
        is None
    )

    assert (
        owner.put(
            f"{API}/workflows/{BUSY_CARD}/inputs",
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
    row = hub.fetchone(
        "SELECT library_uuid, mode, pixel_sha FROM workflow_key_picture_input "
        "WHERE workflow_key = ?",
        (BUSY_CARD,),
    )
    assert row["mode"] == "fixed"
    assert row["library_uuid"] == workflow_env.server.vault.library_uuid
    # A fixed input with no picture is refused rather than stored as a row no
    # run could satisfy -- the CHECK constraint says the same thing, and a 500
    # out of the database is not how a bad request is answered.
    assert (
        owner.put(
            f"{API}/workflows/{BUSY_CARD}/inputs",
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


def test_marking_a_lora_slot_structural_splits_the_card_and_carries_it_over(
    workflow_env,
):
    """The split half of the acceptance.

    Two variants of one graph differing only in which character LoRA they
    load: marked `recipe` they are one card, and marking the slot `structural`
    pulls the LoRA into the key and makes two. **Every new key inherits the
    owner's attributes** -- name, notes, pins, overrides -- because the
    alternative is a correction that silently empties a card somebody named.
    """
    owner, server = workflow_env.owner, workflow_env.server
    merged = _seed_flip_fixture(server)
    label = _flip_slot_label(FLIP_RECIPE_A)

    assert (
        owner.patch(
            f"{API}/workflows/{merged}",
            json={"name": "Character sheet", "notes": "two characters"},
        ).status_code
        == 200
    )
    owner.put(
        f"{API}/workflows/{merged}/defaults",
        json={"defaults": [{"slot_label": "s", "input_name": "steps", "value": 28}]},
    )
    owner.put(
        f"{API}/workflows/{merged}/pins",
        json={"pins": [{"slot_label": "s", "input_name": "steps"}]},
    )

    r = owner.put(
        f"{API}/workflows/{merged}/slots", json={"marks": {label: "structural"}}
    )
    assert r.status_code == 200, r.text
    split = {_flip_key(FLIP_RECIPE_A, [label]), _flip_key(FLIP_RECIPE_B, [label])}
    assert len(split) == 2, "the marks did not separate the two variants"
    assert r.json()["key"] in split
    assert set(r.json()["moved"][merged]) == split

    for key in split:
        assert owner.get(f"{API}/workflows/cards/{key}").status_code == 200
        row = _attr_row(server, key)
        assert row is not None, f"{key} lost the owner's attributes in the split"
        assert row["name"] == "Character sheet"
        assert row["notes"] == "two characters"
        assert default_overrides(server.hub, key) == {("s", "steps"): "28"}
        assert (
            server.hub.fetchone(
                "SELECT pins FROM workflow_key_pins WHERE workflow_key = ?", (key,)
            )
            is not None
        )
    # And the card they came from is gone rather than left behind empty.
    assert owner.get(f"{API}/workflows/cards/{merged}").status_code == 404


def test_merging_two_cards_keeps_the_name_of_the_one_with_most_pictures(workflow_env):
    """The merge half: flipping back folds the two cards into one again.

    Three pictures on one and one on the other, so the winner is decided by
    the count and not by which key happens to sort first -- the assertion
    names the loser's name as the one that must NOT survive.
    """
    owner, server = workflow_env.owner, workflow_env.server
    merged = _seed_flip_fixture(server)
    label = _flip_slot_label(FLIP_RECIPE_A)
    assert (
        owner.put(
            f"{API}/workflows/{merged}/slots", json={"marks": {label: "structural"}}
        ).status_code
        == 200
    )

    busy_key = _flip_key(FLIP_RECIPE_A, [label])
    quiet_key = _flip_key(FLIP_RECIPE_B, [label])
    owner.patch(f"{API}/workflows/{busy_key}", json={"name": "The busy one"})
    owner.patch(f"{API}/workflows/{quiet_key}", json={"name": "The quiet one"})
    assert (
        owner.get(f"{API}/workflows/cards/{busy_key}").json()["card"]["picture_count"]
        == 3
    )

    r = owner.put(
        f"{API}/workflows/{busy_key}/slots", json={"marks": {label: "recipe"}}
    )
    assert r.status_code == 200, r.text
    assert r.json()["key"] == merged
    assert r.json()["moved"][busy_key] == [merged]
    row = _attr_row(server, merged)
    assert row["name"] == "The busy one", (
        "the merged card took the name of the card with fewer pictures"
    )
    assert _attr_row(server, quiet_key) is None


def test_a_variant_that_will_not_reduce_keeps_its_card_and_its_attributes(
    workflow_env,
):
    """The one way the flip can destroy work, pinned.

    A variant whose stored document will not parse keeps the key it is on -
    both branches log and carry on rather than taking the flip down. Its
    SIBLING moves, so the card they shared is no longer among the new keys,
    and a carry-over that cleared "every key no new key replaced" would empty
    a card a variant is still sitting on.
    """
    owner, server = workflow_env.owner, workflow_env.server
    merged = _seed_flip_fixture(server)
    with server.hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_recipe_graph SET document = '{oops' "
            "WHERE structural_hash = ?",
            (FLIP_RECIPE_B,),
        )
    assert (
        owner.patch(
            f"{API}/workflows/{merged}", json={"name": "Half of me stays"}
        ).status_code
        == 200
    )

    label = _flip_slot_label(FLIP_RECIPE_A)
    r = owner.put(
        f"{API}/workflows/{merged}/slots", json={"marks": {label: "structural"}}
    )
    assert r.status_code == 200, r.text
    # B could not be re-keyed, so it is still on the card they shared - and
    # that card must still know its name.
    assert (
        server.hub.fetchone(
            "SELECT workflow_key FROM workflow_variant WHERE structural_hash = ?",
            (FLIP_RECIPE_B,),
        )["workflow_key"]
        == merged
    )
    row = _attr_row(server, merged)
    assert row is not None and row["name"] == "Half of me stays", (
        "the flip emptied a card a variant is still on"
    )
    assert _attr_row(server, _flip_key(FLIP_RECIPE_A, [label]))["name"] == (
        "Half of me stays"
    )


def test_a_mark_flip_refuses_a_slot_the_workflow_does_not_have(workflow_env):
    """Named rather than ignored: a mark on a label no reader will ever look
    up would answer 200 to a flip that cannot have happened."""
    owner, server = workflow_env.owner, workflow_env.server
    merged = _seed_flip_fixture(server)
    r = owner.put(
        f"{API}/workflows/{merged}/slots", json={"marks": {"not-a-slot": "structural"}}
    )
    assert r.status_code == 422, r.text
    r = owner.put(
        f"{API}/workflows/{merged}/slots",
        json={"marks": {_flip_slot_label(FLIP_RECIPE_A): "maybe"}},
    )
    assert r.status_code == 422, r.text


def test_stacking_two_stacks_merges_them_and_keeps_the_first_ones_cover(workflow_env):
    """Selection order is the stack's order, so the first selection covers it."""
    owner, server = workflow_env.owner, workflow_env.server
    # BUSY and FORGOTTEN already share a core hash. Stacking BINNED onto the
    # first of them must bring the whole automatic group with it.
    r = owner.post(f"{API}/workflows/stacks", json={"keys": [BINNED_CARD, BUSY_CARD]})
    assert r.status_code == 201, r.text
    assert r.json()["keys"][0] == BINNED_CARD
    assert set(r.json()["keys"]) == {BINNED_CARD, BUSY_CARD, FORGOTTEN_CARD}
    stack_id = r.json()["stack_id"]
    assert effective_stack_keys(server.hub, BUSY_CARD)[0] == BINNED_CARD

    r = owner.put(
        f"{API}/workflows/stacks/{stack_id}/order",
        json={"keys": [BUSY_CARD, FORGOTTEN_CARD, BINNED_CARD]},
    )
    assert r.status_code == 200, r.text
    assert effective_stack_keys(server.hub, BINNED_CARD) == [
        BUSY_CARD,
        FORGOTTEN_CARD,
        BINNED_CARD,
    ]


def test_unstacking_one_card_leaves_the_rest_and_dissolves_a_stack_of_one(
    workflow_env,
):
    """A stack left with one member is not a stack, and the row goes with it."""
    owner, server = workflow_env.owner, workflow_env.server
    assert set(effective_stack_keys(server.hub, BUSY_CARD)) == {
        BUSY_CARD,
        FORGOTTEN_CARD,
    }
    r = owner.post(f"{API}/workflows/{BUSY_CARD}/unstack")
    assert r.status_code == 200, r.text
    assert effective_stack_keys(server.hub, BUSY_CARD) == [BUSY_CARD]
    assert effective_stack_keys(server.hub, FORGOTTEN_CARD) == [FORGOTTEN_CARD]

    # A manual stack of three, one taken out, leaves a stack of two standing.
    stack_id = owner.post(
        f"{API}/workflows/stacks",
        json={"keys": [BUSY_CARD, FORGOTTEN_CARD, BINNED_CARD]},
    ).json()["stack_id"]
    owner.post(f"{API}/workflows/{BINNED_CARD}/unstack")
    assert effective_stack_keys(server.hub, BUSY_CARD) == [BUSY_CARD, FORGOTTEN_CARD]
    # Down to one, and the row is gone rather than left naming a lone card.
    owner.post(f"{API}/workflows/{FORGOTTEN_CARD}/unstack")
    assert effective_stack_keys(server.hub, BUSY_CARD) == [BUSY_CARD]
    assert (
        server.hub.fetchone(
            "SELECT 1 FROM workflow_stack WHERE stack_id = ?", (stack_id,)
        )
        is None
    )


def test_dissolving_a_whole_stack_keeps_its_members_apart(workflow_env):
    """The automatic grouping must not re-form on the next read.

    An `auto:` id names a grouping that is not a row, so taking it apart is
    only visible if every member is recorded as having left it.
    """
    owner, server = workflow_env.owner, workflow_env.server
    stack_id = f"{AUTO_STACK_PREFIX}{SHARED_CORE}"
    r = owner.post(f"{API}/workflows/stacks/{stack_id}/unstack")
    assert r.status_code == 200, r.text
    assert set(r.json()["keys"]) == {BUSY_CARD, FORGOTTEN_CARD}
    assert effective_stack_keys(server.hub, BUSY_CARD) == [BUSY_CARD]
    assert effective_stack_keys(server.hub, FORGOTTEN_CARD) == [FORGOTTEN_CARD]
    # A stack this hub does not hold is a 404, never a silent success.
    assert (
        owner.post(
            f"{API}/workflows/stacks/{AUTO_STACK_PREFIX}{_h('nosuchcore')}/unstack"
        ).status_code
        == 404
    )
    assert owner.post(f"{API}/workflows/stacks/not-a-stack/unstack").status_code == 422


def test_stack_decisions_and_attributes_survive_a_regrouping(workflow_env):
    """The acceptance: a `CORE_VERSION` bump must not undo what the owner did.

    A bump re-derives every `core_hash`, which is what the automatic grouping
    IS -- so the test regroups the fixture by rewriting those hashes. Both
    kinds of decision are keyed on the CARD and must come through: the name,
    and the two stack decisions (a card taken out of its group, and a manual
    stack of cards that never shared one).
    """
    owner, server = workflow_env.owner, workflow_env.server
    assert (
        owner.patch(
            f"{API}/workflows/{BUSY_CARD}", json={"name": "Survivor"}
        ).status_code
        == 200
    )
    assert owner.post(f"{API}/workflows/{BUSY_CARD}/unstack").status_code == 200
    manual = owner.post(
        f"{API}/workflows/stacks", json={"keys": [BINNED_CARD, HIDDEN_CARD]}
    ).json()["stack_id"]

    # The regrouping. Every topology lands on a new core hash, so nothing the
    # grid would group by is what it was before this line.
    with server.hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_topology_core SET core_hash = ? || core_hash",
            (_h("bumped")[:8],),
        )

    assert _attr_row(server, BUSY_CARD)["name"] == "Survivor"
    assert effective_stack_keys(server.hub, BUSY_CARD) == [BUSY_CARD], (
        "the regrouping put a card back in a group it was taken out of"
    )
    assert set(effective_stack_keys(server.hub, BINNED_CARD)) == {
        BINNED_CARD,
        HIDDEN_CARD,
    }
    assert (
        server.hub.fetchone(
            "SELECT 1 FROM workflow_stack WHERE stack_id = ?", (manual,)
        )
        is not None
    )


def test_every_write_says_which_cards_to_look_at_again(workflow_env):
    """`CHANGED_WORKFLOWS` on each write, with its keys and its reason.

    A "look again" signal, so what is asserted is that it is raised at all,
    that it names the card the gesture was about, and that the reason
    separates an edit from a restack -- which is what a client uses to decide
    whether to re-read one card or the whole grid.
    """
    owner, server = workflow_env.owner, workflow_env.server
    seen, stop = _events(server)
    try:
        owner.patch(
            f"{API}/workflows/{BUSY_CARD}",
            json={"name": "Announced"},
            headers={"X-Client-Id": "example-tab"},
        )
        owner.put(f"{API}/workflows/{BUSY_CARD}/pins", json={"pins": []})
        owner.post(f"{API}/workflows/{BUSY_CARD}/unstack")
    finally:
        stop()
    assert [event["reason"] for event in seen] == ["changed", "changed", "stacks"]
    assert seen[0]["keys"] == [BUSY_CARD]
    assert seen[0]["origin_client_id"] == "example-tab"
    assert seen[0]["source"] == "ui"
    # The unstack names the stack it took the card out of, not the card alone:
    # every member's tile changes when one leaves.
    assert set(seen[2]["keys"]) == {BUSY_CARD, FORGOTTEN_CARD}


def test_an_unknown_card_cannot_be_written_to(workflow_env):
    """A 404 rather than an attribute row against a card this hub never had."""
    owner = workflow_env.owner
    unknown = _h("nosuchcard")
    for method, path, body in (
        ("PATCH", f"{API}/workflows/{unknown}", {"name": "x"}),
        ("PUT", f"{API}/workflows/{unknown}/slots", {"marks": {}}),
        ("PUT", f"{API}/workflows/{unknown}/defaults", {"defaults": []}),
        ("PUT", f"{API}/workflows/{unknown}/pins", {"pins": []}),
        ("PUT", f"{API}/workflows/{unknown}/inputs", {"inputs": []}),
        ("POST", f"{API}/workflows/{unknown}/unstack", None),
        ("POST", f"{API}/workflows/stacks", {"keys": [BUSY_CARD, unknown]}),
    ):
        assert_real_route(workflow_env.server.api, method, path)
        r = owner.request(method, path, json=body)
        assert r.status_code == 404, f"{method} {path}: {r.status_code} {r.text}"
    assert (
        owner.patch(f"{API}/workflows/not-a-digest", json={"name": "x"}).status_code
        == 422
    )
    assert (
        owner.post(f"{API}/workflows/stacks", json={"keys": [BUSY_CARD]}).status_code
        == 400
    )


def test_a_split_carries_everything_the_owner_said_and_the_file_with_it(workflow_env):
    """Every table in `_KEYED_TABLES`, not just the ones with a route.

    The cover and the "keep this out of its group" row have no write of their
    own in this step, so nothing else would notice them being dropped from the
    carry-over -- and the coverage matrix claims the cover comes across. The
    workflow FILE is here for the same reason: its key is derived rather than
    the owner's, but a file left on a dead key is a card that stops saying a
    workflow on this machine runs it.
    """
    owner, server = workflow_env.owner, workflow_env.server
    merged = _seed_flip_fixture(server)
    label = _flip_slot_label(FLIP_RECIPE_A)
    pixel_sha = _h("acover")
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_cover (library_uuid, workflow_key, pixel_sha) "
            "VALUES (?, ?, ?)",
            (server.vault.library_uuid, merged, pixel_sha),
        )
        conn.execute(
            "INSERT INTO workflow_unstacked (workflow_key) VALUES (?)", (merged,)
        )
        conn.execute(
            "INSERT INTO workflow_file "
            "(workflow_name, topology_hash, structural_hash, workflow_key) "
            "VALUES ('flip_a.json', ?, ?, ?)",
            (FLIP_TOPOLOGY, FLIP_RECIPE_A, merged),
        )

    assert (
        owner.put(
            f"{API}/workflows/{merged}/slots", json={"marks": {label: "structural"}}
        ).status_code
        == 200
    )
    split = {_flip_key(FLIP_RECIPE_A, [label]), _flip_key(FLIP_RECIPE_B, [label])}
    for key in split:
        assert (
            server.hub.fetchone(
                "SELECT pixel_sha FROM workflow_cover WHERE workflow_key = ?", (key,)
            )["pixel_sha"]
            == pixel_sha
        ), f"{key} lost the cover the owner chose"
        assert (
            server.hub.fetchone(
                "SELECT 1 FROM workflow_unstacked WHERE workflow_key = ?", (key,)
            )
            is not None
        ), f"{key} lost the decision to keep it out of its group"
    # The file follows the variant it holds, not the card it used to be on.
    assert server.hub.fetchone(
        "SELECT workflow_key FROM workflow_file WHERE workflow_name = 'flip_a.json'"
    )["workflow_key"] == _flip_key(FLIP_RECIPE_A, [label])
    assert (
        owner.get(f"{API}/workflows/cards/{_flip_key(FLIP_RECIPE_A, [label])}").json()[
            "card"
        ]["imported"]
        is True
    )


def test_a_split_sends_the_caller_to_the_half_with_the_pictures(workflow_env):
    """`SlotMarkResult.key` is the BIGGEST successor, and `moved` says so first.

    The card the caller was looking at has no single successor after a split,
    so the route promises the one holding most of its pictures -- three
    against one in this fixture. Asserted by name rather than by membership:
    `in split` passes for either half and is what makes the promise unpinned.
    """
    owner, server = workflow_env.owner, workflow_env.server
    merged = _seed_flip_fixture(server)
    label = _flip_slot_label(FLIP_RECIPE_A)
    r = owner.put(
        f"{API}/workflows/{merged}/slots", json={"marks": {label: "structural"}}
    )
    assert r.status_code == 200, r.text
    biggest = _flip_key(FLIP_RECIPE_A, [label])
    smallest = _flip_key(FLIP_RECIPE_B, [label])
    assert (
        owner.get(f"{API}/workflows/cards/{biggest}").json()["card"]["picture_count"]
        == 3
    )
    assert (
        owner.get(f"{API}/workflows/cards/{smallest}").json()["card"]["picture_count"]
        == 1
    )
    assert r.json()["key"] == biggest
    assert r.json()["moved"][merged] == [biggest, smallest]


def test_a_split_of_a_stacked_card_keeps_the_stacks_cover(workflow_env):
    """`workflow_stack_member.position` is not part of the primary key.

    Copied verbatim, both halves land on the position the card had, and
    `effective_stack_keys` then breaks the tie on the KEY -- handing the cover
    to whichever digest sorts first. The fixture is built so that is the
    WRONG half: the small one's key sorts first.
    """
    owner, server = workflow_env.owner, workflow_env.server
    merged = _seed_flip_fixture(server)
    label = _flip_slot_label(FLIP_RECIPE_A)
    # B is given the pictures here, because the tie-break is only WRONG when
    # the small half's key sorts first, and with the module's counts (A big)
    # A's key happens to sort first anyway -- which would make this pass
    # against the very bug it is for.
    _add_flip_pictures(server, FLIP_RECIPE_B, 4)
    biggest = _flip_key(FLIP_RECIPE_B, [label])
    smallest = _flip_key(FLIP_RECIPE_A, [label])
    assert smallest < biggest, (
        "this fixture only tests the tie-break while the small half's key "
        "sorts first; the flip fixture's documents decide that"
    )
    # BINNED rather than BUSY: BUSY shares a core hash with FORGOTTEN, so
    # stacking it would pull that card in too and the stack under test would
    # not be the pair this is about.
    stack_id = owner.post(
        f"{API}/workflows/stacks", json={"keys": [merged, BINNED_CARD]}
    ).json()["stack_id"]

    assert (
        owner.put(
            f"{API}/workflows/{merged}/slots", json={"marks": {label: "structural"}}
        ).status_code
        == 200
    )
    assert effective_stack_keys(server.hub, BINNED_CARD) == [
        biggest,
        smallest,
        BINNED_CARD,
    ], "the split moved the stack's cover to the half with fewer pictures"
    assert [
        row["position"]
        for row in server.hub.fetchall(
            "SELECT position FROM workflow_stack_member WHERE stack_id = ? "
            "ORDER BY position",
            (stack_id,),
        )
    ] == [0, 1, 2], "two members share a position"


def test_a_re_keying_takes_the_saved_recipes_with_it(workflow_env):
    """The migration `db_models/saved_recipe.py` says this change owes it.

    A saved recipe is a VAULT row keyed on the card key, and it is authored:
    unlike a hub row it cannot be rebuilt from anything. Left on the old key
    it is addressed by a key no variant carries, its workflow's tab stops
    listing it, and nothing says where it went.
    """
    owner, server = workflow_env.owner, workflow_env.server
    merged = _seed_flip_fixture(server)
    label = _flip_slot_label(FLIP_RECIPE_A)
    r = owner.post(
        f"{API}/recipes",
        json={"workflow_key": merged, "name": "Kept look", "prompt": "a portrait"},
    )
    assert r.status_code == 201, r.text
    recipe_id = r.json()["id"]

    assert (
        owner.put(
            f"{API}/workflows/{merged}/slots", json={"marks": {label: "structural"}}
        ).status_code
        == 200
    )
    biggest = _flip_key(FLIP_RECIPE_A, [label])
    listed = owner.get(f"{API}/recipes?workflow_key={biggest}").json()
    assert [row["id"] for row in listed] == [recipe_id], (
        "the saved recipe was orphaned on a key no variant carries"
    )
    assert listed[0]["workflow_key"] == biggest
    # And it is not left behind on a card that no longer exists.
    assert owner.get(f"{API}/recipes?workflow_key={merged}").json() == []


def test_reordering_a_stack_cannot_leave_a_card_in_two_of_them(workflow_env):
    """A complete ordered member list, checked against what the stack holds.

    `effective_stack_keys` orders by `stack_id` to make a double membership
    *reproducible*, never correct, so this route must not be able to create
    one -- and a key left out of the list would be deleted from the stack with
    nothing recording that it left.
    """
    owner, server = workflow_env.owner, workflow_env.server
    stack_id = owner.post(
        f"{API}/workflows/stacks", json={"keys": [BUSY_CARD, FORGOTTEN_CARD]}
    ).json()["stack_id"]

    # A stack this hub does not hold is a 404, not a stack minted out of thin
    # air -- the same answer its `unstack` sibling gives.
    assert (
        owner.put(
            f"{API}/workflows/stacks/{'f' * 32}/order",
            json={"keys": [BUSY_CARD, BINNED_CARD]},
        ).status_code
        == 404
    )
    assert (
        owner.put(
            f"{API}/workflows/stacks/{AUTO_STACK_PREFIX}{_h('nosuchcore')}/order",
            json={"keys": [BUSY_CARD, BINNED_CARD]},
        ).status_code
        == 404
    )
    # A list that is not this stack's membership is refused whole.
    assert (
        owner.put(
            f"{API}/workflows/stacks/{stack_id}/order",
            json={"keys": [BUSY_CARD, BINNED_CARD]},
        ).status_code
        == 400
    )
    assert [
        row["stack_id"]
        for row in server.hub.fetchall(
            "SELECT stack_id FROM workflow_stack_member WHERE workflow_key = ?",
            (BUSY_CARD,),
        )
    ] == [stack_id], "a card ended up in two stacks at once"
    # The gesture it does take.
    assert (
        owner.put(
            f"{API}/workflows/stacks/{stack_id}/order",
            json={"keys": [FORGOTTEN_CARD, BUSY_CARD]},
        ).status_code
        == 200
    )
    assert effective_stack_keys(server.hub, BUSY_CARD) == [FORGOTTEN_CARD, BUSY_CARD]

    # And the hub function keeps the guarantee on its own. The route refuses
    # the shape above before the hub ever sees it, so without this the hub
    # would be correct only because of its one caller -- and it is the
    # module's public entry point, which the next caller will reach for.
    set_stack_order(server.hub, "e" * 32, [BUSY_CARD, BINNED_CARD])
    assert [
        row["stack_id"]
        for row in server.hub.fetchall(
            "SELECT stack_id FROM workflow_stack_member WHERE workflow_key = ?",
            (BUSY_CARD,),
        )
    ] == ["e" * 32], "the card kept its old membership as well as the new one"


def test_naming_one_parameter_twice_is_refused_rather_than_a_500(workflow_env):
    """Both whole-set writes, both keyed on `(slot_label, input_name)`.

    The sibling constraint on the same table (a `fixed` input with no picture)
    is pre-validated; this one reached the database as an uncaught
    IntegrityError, which is a 500 for what is a bad request.
    """
    owner = workflow_env.owner
    assert (
        owner.put(
            f"{API}/workflows/{BUSY_CARD}/defaults",
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
            f"{API}/workflows/{BUSY_CARD}/inputs",
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
            f"{API}/workflows/{BUSY_CARD}/defaults",
            json={
                "defaults": [
                    {"slot_label": "a", "input_name": "steps", "value": 1},
                    {"slot_label": "b", "input_name": "steps", "value": 2},
                ]
            },
        ).status_code
        == 200
    )


def test_a_surviving_card_keeps_its_saved_recipes(workflow_env):
    """A card a variant is still on must not have its recipes taken off it.

    The sibling of `..._takes_the_saved_recipes_with_it`, and the direction
    that was wrong. `rekey_in_session` skipped on `successors[0] == old_key`,
    which is not the question "did this card go away": a variant whose
    document will not parse keeps the key it is on, so when a sibling moves,
    that key is in `moved` with a successor that is not itself - while the
    card is still open at its own URL and still holds its name (the test above
    asserts that half). Every recipe on it was moved to the sibling's new key.

    The Unstack is what makes it visible rather than what causes it: stacked,
    the two halves share a core hash and `GET /recipes` expands across the
    stack, so the recipe answers on both keys and nothing looks wrong. One
    Unstack - a gesture this step ships the route for - and the tab is empty.
    """
    owner, server = workflow_env.owner, workflow_env.server
    merged = _seed_flip_fixture(server)
    with server.hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_recipe_graph SET document = '{oops' "
            "WHERE structural_hash = ?",
            (FLIP_RECIPE_B,),
        )
    assert owner.post(f"{API}/workflows/{merged}/unstack").status_code == 200
    r = owner.post(
        f"{API}/recipes",
        json={"workflow_key": merged, "name": "Stays put", "prompt": "a portrait"},
    )
    assert r.status_code == 201, r.text
    recipe_id = r.json()["id"]

    label = _flip_slot_label(FLIP_RECIPE_A)
    r = owner.put(
        f"{API}/workflows/{merged}/slots", json={"marks": {label: "structural"}}
    )
    assert r.status_code == 200, r.text
    # The card is still there: B could not be re-keyed onto anything.
    assert owner.get(f"{API}/workflows/cards/{merged}").status_code == 200
    listed = owner.get(f"{API}/recipes?workflow_key={merged}").json()
    assert [row["id"] for row in listed] == [recipe_id], (
        "the saved recipe left a card that is still there"
    )
    assert listed[0]["workflow_key"] == merged
    # And it did not follow the half that moved.
    moved_to = _flip_key(FLIP_RECIPE_A, [label])
    assert owner.get(f"{API}/recipes?workflow_key={moved_to}").json() == []
    # The response says so too: a card that both moved and did not is listed
    # among its own successors rather than only among the keys it went to.
    assert merged in r.json()["moved"][merged]


def test_a_re_keying_that_cannot_move_the_recipes_says_which_keys_to_repair(
    workflow_env, monkeypatch, caplog
):
    """The hub commits first, so the second write's failure needs a record.

    Re-running the flip cannot repair it - the marks asked for are now the
    marks in force, so a second PUT re-keys nothing and answers an empty
    `moved` - which is why the log has to carry the map and not just a count.
    Without it the only trace is recipes on keys no variant carries, with
    nothing saying where they belong.
    """
    owner, server = workflow_env.owner, workflow_env.server
    merged = _seed_flip_fixture(server)

    def _fails(vault, moved):
        raise RuntimeError("the vault went away mid-flip")

    monkeypatch.setattr(saved_recipe_service, "rekey_recipes", _fails)
    label = _flip_slot_label(FLIP_RECIPE_A)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError):
            owner.put(
                f"{API}/workflows/{merged}/slots", json={"marks": {label: "structural"}}
            )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "could not move the saved recipes" in logged
    assert merged in logged, "the log has to name the keys a repair would need"
    # And the hub half did land, which is what makes the record necessary.
    assert (
        server.hub.fetchone(
            "SELECT workflow_key FROM workflow_variant WHERE structural_hash = ?",
            (FLIP_RECIPE_A,),
        )["workflow_key"]
        != merged
    )


def test_a_re_key_needs_an_open_library_and_says_so(workflow_env, monkeypatch):
    """503 rather than an arbitrary merge winner and orphaned recipes.

    The order matters as much as the code: a mark this workflow has no slot
    for is a 422 whether or not a library is open, so the label check stays
    ahead of this one. A caller who got told "no library" about a typo would
    go looking in the wrong place.
    """
    owner, server = workflow_env.owner, workflow_env.server
    label = _flip_slot_label(FLIP_RECIPE_A)
    merged = _seed_flip_fixture(server)
    # The property's own backing attribute, because that is what "no library
    # open" is: `Vault.library_uuid` reads `_library_uuid` and has no setter,
    # so patching the name would only prove that a fake can be attached.
    monkeypatch.setattr(server.vault, "_library_uuid", None)

    assert (
        owner.put(
            f"{API}/workflows/{merged}/slots", json={"marks": {label: "structural"}}
        ).status_code
        == 503
    )
    assert (
        owner.put(
            f"{API}/workflows/{merged}/slots",
            json={"marks": {"no such slot": "recipe"}},
        ).status_code
        == 422
    ), "a bad slot label must be named even when no library is open"
    # Nothing was written: the card is still on the key it was on.
    assert owner.get(f"{API}/workflows/cards/{merged}").status_code == 200


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
            f"{API}/workflows/{BUSY_CARD}/inputs",
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
        "input": {
            "required": {
                "lora_name": [["add_detail.safetensors", "other.safetensors"], {}],
                "strength_model": ["FLOAT", {"default": 1.0}],
                "strength_clip": ["FLOAT", {"default": 1.0}],
            }
        }
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
            "(topology_hash, core_hash, core_version, workflow_type, slots) "
            "VALUES (?, ?, ?, ?, ?)",
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
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_key": RUN_CARD})
    assert r.status_code == 200, r.text
    assert r.json()["groups"][0]["source"] == "file", r.json()
    # The file's own values, not the instance document's 24/6.5.
    assert runnable.submitted[0]["graph"]["3"]["inputs"]["steps"] == 11


def test_a_kept_pictures_embedded_graph_is_the_second_source(runnable, monkeypatch):
    """Tier 2: a real run of the card, with real filenames, off the best picture."""
    embedded = json.loads(json.dumps(RUN_DOCUMENT))
    embedded["3"]["inputs"].update({"steps": 33, "cfg": 3.5, "seed": 7})
    embedded["1"]["inputs"]["ckpt_name"] = "realvisxl.safetensors"
    embedded["2"]["inputs"]["lora_name"] = "add_detail.safetensors"
    read: list[int] = []

    def fake_embedded(server, picture_id):
        read.append(picture_id)
        return embedded

    monkeypatch.setattr(workflows_routes, "_load_embedded_api_prompt", fake_embedded)
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_key": RUN_CARD})
    assert r.status_code == 200, r.text
    assert r.json()["groups"][0]["source"] == "picture", r.json()
    # It names WHICH picture answered, and that is the card's best.
    assert r.json()["groups"][0]["source_picture_id"] == read[0]
    assert runnable.submitted[0]["graph"]["3"]["inputs"]["steps"] == 33


def test_a_picture_whose_file_has_gone_falls_through_instead_of_erroring(
    runnable, monkeypatch
):
    """The resolver is walking candidates; an unreadable one is not an error.

    Wrong if the request 404s: the best picture of a card being off the disk
    is exactly when the next tier is wanted.
    """

    def gone(server, picture_id):
        raise HTTPException(status_code=404, detail="Picture file missing")

    monkeypatch.setattr(workflows_routes, "_load_embedded_api_prompt", gone)
    payload = _preflight(runnable.owner, workflow_key=RUN_CARD)
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
    body = {"workflow_key": RUN_CARD}
    if source == "picture":
        body = {"picture_ids": [runnable.picture_id]}
    elif source == "saved_recipe":
        r = runnable.owner.post(
            f"{API}/recipes",
            json={"name": "cold light", "workflow_key": RUN_CARD, "prompt": "a cat"},
        )
        assert r.status_code in {200, 201}, r.text
        body = {"saved_recipe_id": r.json()["id"]}

    payload = _preflight(runnable.owner, **body)
    assert payload["groups"], payload
    assert payload["groups"][0]["workflow_key"] == RUN_CARD
    assert payload["groups"][0]["reasons"] == [], payload
    assert payload["ok"] is True
    # Tier 3: the card has no imported file and its picture has no file on
    # disk, so the stored instance document is what answered.
    assert payload["groups"][0]["source"] == "instance"


def test_exactly_one_source_may_be_named(runnable):
    for body in (
        {},
        {"workflow_key": RUN_CARD, "picture_ids": [runnable.picture_id]},
        {"workflow_key": RUN_CARD, "saved_recipe_id": 1},
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
    keys = {group["workflow_key"] for group in payload["groups"]}
    assert keys == {RUN_CARD, BUSY_CARD}, payload
    # Each group carries the pictures that chose it and nobody else's.
    by_key = {group["workflow_key"]: group for group in payload["groups"]}
    assert by_key[RUN_CARD]["picture_ids"] == [runnable.picture_id]
    assert sorted(by_key[BUSY_CARD]["picture_ids"]) == sorted(busy[:2])


def test_a_target_runs_that_card_instead_of_the_ones_selected(runnable):
    """`target` is how a stack's other member is chosen."""
    payload = _preflight(
        runnable.owner, picture_ids=[runnable.picture_id], target=BUSY_CARD
    )
    assert [group["workflow_key"] for group in payload["groups"]] == [BUSY_CARD]


# --- every reason code -----------------------------------------------------


def test_a_workflow_source_reports_no_save_node(runnable):
    """BUSY's stored graph has nothing that writes an image."""
    payload = _preflight(runnable.owner, workflow_key=BUSY_CARD)
    assert "no_save_node" in _reasons(payload), payload


def test_a_forgotten_model_name_surfaces_as_a_missing_model(runnable):
    """FORGOTTEN's references have no asset row, so nothing names its models.

    This is the tier-3 resolution rule showing through: the stored document
    still says a model went there and the hub can no longer say which, so the
    run reports a missing model rather than pretending the slot is empty.
    """
    payload = _preflight(runnable.owner, workflow_key=FORGOTTEN_CARD)
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


def test_a_card_this_hub_does_not_hold_has_no_runnable_source(runnable):
    payload = _preflight(runnable.owner, workflow_key=_h("nosuchcard"))
    assert _reasons(payload) == {"no_runnable_source"}, payload


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


def test_a_missing_node_pack_is_reported_by_name(runnable):
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info.pop("LoraLoader")
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    payload = _preflight(runnable.owner, workflow_key=RUN_CARD)
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
    payload = _preflight(runnable.owner, workflow_key=RUN_CARD)
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
        _preflight(runnable.owner, workflow_key=RUN_CARD)
    )

    # `.test` is RFC 2606's reserved name; nothing resolves it and the fetch is
    # patched out anyway. What matters is only that the user HAS an address.
    r = runnable.owner.patch(
        f"{API}/users/me/config", json={"comfyui_url": "http://comfyui.test:8188"}
    )
    assert r.status_code == 200, r.text
    try:
        assert "comfyui_unreachable" in _reasons(
            _preflight(runnable.owner, workflow_key=RUN_CARD)
        )
        # ...and it is the ONLY one of the two: reporting both would leave a
        # panel deciding which sentence to show.
        assert "comfyui_not_configured" not in _reasons(
            _preflight(runnable.owner, workflow_key=RUN_CARD)
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
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_key": RUN_CARD})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "refused", r.json()
    assert runnable.submitted == [], "an uninspected graph ran without consent"

    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={"workflow_key": RUN_CARD, "allow_unchecked": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success", r.json()
    assert len(runnable.submitted) == 1, "consent did not let the run through"
    # The reason survives the consent rather than being cleared by it.
    assert r.json()["groups"][0]["reasons"][0]["code"] == "comfyui_not_configured"


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
    healthy = {g["workflow_key"]: g for g in r.json()["groups"]}[RUN_CARD]
    assert healthy["reasons"] == [], healthy
    assert healthy["runs"] == 0, healthy


def test_a_graph_with_pixlstash_nodes_is_refused(runnable):
    """It would read and write the library while PixlStash is running it.

    The node is put in the stored graph and DETECTED, rather than the detector
    being replaced with `lambda: True`: patching it would guard the plumbing
    and say nothing about whether such a graph is recognised.
    """
    info = json.loads(json.dumps(RUN_OBJECT_INFO))
    info["PixlStashPictureLoader"] = {"input": {"required": {}}}
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (info, None)
    )
    with runnable.server.hub.transaction() as conn:
        instance = json.loads(json.dumps(RUN_DOCUMENT))
        instance["3"]["inputs"].update({"steps": 24, "cfg": 6.5})
        instance["5"] = {"class_type": "PixlStashPictureLoader", "inputs": {}}
        conn.execute(
            "UPDATE workflow_recipe_instance SET document = ? WHERE instance_hash = ?",
            (json.dumps(instance), RUN_INSTANCE),
        )
    payload = _preflight(runnable.owner, workflow_key=RUN_CARD)
    assert "pixlstash_nodes" in _reasons(payload), payload


def test_a_lora_asked_for_where_there_is_no_loader_says_so(runnable):
    """`no_lora_loader` is the code for a graph that has no slot at all.

    Asked only when a LoRA is actually being placed: a workflow with no loader
    is perfectly runnable on its own, and reporting this for one would make
    every plain card look broken.
    """
    # The card as it stands HAS a slot, so the code must not appear.
    assert "no_lora_loader" not in _reasons(
        _preflight(runnable.owner, workflow_key=RUN_CARD)
    )

    runnable.monkeypatch.setattr(
        workflows_routes, "detect_lora_targets", lambda graph: []
    )
    payload = _preflight(
        runnable.owner,
        workflow_key=RUN_CARD,
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
            "workflow_key": RUN_CARD,
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
            "workflow_key": RUN_CARD,
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


def test_a_fixed_picture_input_whose_picture_is_still_here_is_not_reported(
    runnable,
):
    """The positive half, and it is the half that needs the code to be right.

    A `fixed` input names its picture by content, so "is it still here" is a
    lookup that has to come back with the sha. Asserting only the absent case
    passes on a read that cannot return anything at all.
    """
    r = runnable.owner.put(
        f"{API}/workflows/{RUN_CARD}/inputs",
        json={
            "inputs": [
                {
                    "slot_label": "load",
                    "input_name": "image",
                    "mode": "fixed",
                    "pixel_sha": RUN_PIXEL_SHA,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    payload = _preflight(runnable.owner, workflow_key=RUN_CARD)
    assert "fixed_input_deleted" not in _reasons(payload), payload
    assert payload["groups"][0]["reasons"] == [], payload


def test_a_fixed_picture_input_that_has_been_deleted_is_reported(runnable):
    """The card's fixed input names a picture this library no longer holds."""
    r = runnable.owner.put(
        f"{API}/workflows/{RUN_CARD}/inputs",
        json={
            "inputs": [
                {
                    "slot_label": "load",
                    "input_name": "image",
                    "mode": "fixed",
                    "pixel_sha": _h("a picture that left"),
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    payload = _preflight(runnable.owner, workflow_key=RUN_CARD)
    assert "fixed_input_deleted" in _reasons(payload), payload


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
    payload = _preflight(runnable.owner, workflow_key=HIDDEN_CARD)
    assert "ui_format" in _reasons(payload), payload


# --- what a run actually does ----------------------------------------------


def test_count_produces_that_many_submissions(runnable):
    r = runnable.owner.post(
        f"{API}/workflows/run", json={"workflow_key": RUN_CARD, "count": 3}
    )
    assert r.status_code == 200, r.text
    assert r.json()["runs"] == 3, r.json()
    assert len(runnable.submitted) == 3
    # Each is its own graph with its own seed, not three references to one.
    seeds = {entry["graph"]["3"]["inputs"]["seed"] for entry in runnable.submitted}
    assert len(seeds) == 3, seeds


def test_a_new_run_is_not_stacked_with_the_picture_it_came_from(runnable):
    """The default is unstacked, which is where this differs from run_recipe."""
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
    by_key = {group["workflow_key"]: group for group in r.json()["groups"]}
    assert by_key[RUN_CARD]["reasons"] == []
    assert by_key[RUN_CARD]["runs"] == 0


def test_values_are_applied_at_run_time_and_never_written_back(runnable):
    """An edited default is an override on the submitted graph, nothing more."""
    stored = get_document(runnable.server.hub, RUN_RECIPE)
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={
            "workflow_key": RUN_CARD,
            "values": [{"slot_label": "steps", "input_name": "steps", "value": 99}],
        },
    )
    assert r.status_code == 200, r.text
    # The stored graph is content-addressed: rewriting it would change the very
    # identity of the card being run.
    assert get_document(runnable.server.hub, RUN_RECIPE) == stored


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
        json={"workflow_key": RUN_CARD, "prompt": "a lighthouse at dusk"},
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
        json={"workflow_key": RUN_CARD, "count": MAX_RUNS_PER_REQUEST + 1},
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
    r = runnable.owner.post(f"{API}/workflows/run", json={"workflow_key": RUN_CARD})
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
        json={"workflow_key": RUN_CARD, "seed_mode": "keep", "count": 3},
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
        workflows_routes, "_load_embedded_api_prompt", lambda server, pid: embedded
    )
    r = runnable.owner.post(
        f"{API}/workflows/run",
        json={"workflow_key": RUN_CARD, "seed_mode": "keep", "count": 2},
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
    body = {"workflow_key": RUN_CARD, "seed_mode": "fixed"}
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
        workflow_key=RUN_CARD,
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


def test_a_saved_recipes_own_loras_are_placed_in_the_graphs_slots(runnable):
    """ "Run this saved look" that drops the look's LoRAs is the wrong result."""
    r = runnable.owner.post(
        f"{API}/recipes",
        json={
            "name": "with its lora",
            "workflow_key": RUN_CARD,
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


def test_a_saved_seed_never_overrides_a_seed_mode_the_caller_sent(runnable):
    """The request wins over the row, which is what the merge promises."""
    r = runnable.owner.post(
        f"{API}/recipes",
        json={
            "name": "pinned seed",
            "workflow_key": RUN_CARD,
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
        f"{API}/workflows/run", json={"workflow_key": RUN_CARD, "count": 5}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "partial", r.json()
    assert [p["prompt_id"] for p in r.json()["prompts"]] == ["prompt-1", "prompt-2"]
    assert r.json()["runs"] == 2


def test_the_body_takes_no_inputs_field(runnable):
    """Nothing FILLS a card's picture inputs yet, so nothing offers to.

    A field accepted and ignored is worse than one that is absent: a caller
    would send a picture and get a run that never read it. `fixed_input_deleted`
    still reads the setup — reading it and filling it are different jobs.
    """
    assert "inputs" not in RunRequest.model_fields
    r = runnable.owner.post(
        f"{API}/workflows/run/preflight",
        json={"workflow_key": RUN_CARD, "inputs": {"2": 1}},
    )
    # Pydantic ignores an unknown key rather than failing; what matters is that
    # the OpenAPI schema never promised it.
    assert r.status_code == 200, r.text
    schema = runnable.server.api.openapi()["components"]["schemas"]["RunRequest"]
    assert "inputs" not in schema["properties"], schema["properties"].keys()


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
            "workflow_key": RUN_CARD,
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
        json={"workflow_key": RUN_CARD, "allow_unchecked": True},
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
    r = runnable.owner.post(
        f"{API}/recipes",
        json={"name": "orphan", "workflow_key": _h("nosuchcard"), "prompt": "a cat"},
    )
    assert r.status_code in {200, 201}, r.text
    payload = _preflight(runnable.owner, saved_recipe_id=r.json()["id"])
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
            "workflow_key": RUN_CARD,
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


def _lora_slot_label(graph: dict) -> str:
    """The `<node label>/<widget>` a slot mark is keyed by, for this graph."""
    labels = topology_node_labels(structural_document(graph))
    return f"{labels['2']}/lora_name"


def _mark_slot(server, label: str, mark: str) -> None:
    with server.hub.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_slot_mark "
            "(topology_hash, slot_label, mark) VALUES (?, ?, ?)",
            (RUN_TOPOLOGY, label, mark),
        )


@pytest.fixture
def exportable(runnable):
    """RUN_CARD whose only source is a picture's embedded graph, as a real run.

    The picture tier and not the file tier on purpose: a stored file is the
    workflow as authored and carries none of this, so exporting one would pass
    with the whole scrub deleted.
    """
    graph = _embedded_export_graph()
    runnable.monkeypatch.setattr(
        workflows_routes, "_load_embedded_api_prompt", lambda server, pid: graph
    )
    return SimpleNamespace(graph=graph, **vars(runnable))


def test_an_export_carries_no_prompt_no_seed_no_title_and_no_picture_name(exportable):
    """§5.7: everything that is about a RUN rather than about the workflow goes."""
    r = exportable.owner.get(f"{API}/workflows/{RUN_CARD}/export")
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
    # The workflow itself survives: the checkpoint is on the shelf, the wiring
    # and the parameters are untouched. Over-blanking is its own regression —
    # an export nobody can run is not a safer export.
    assert graph["1"]["inputs"]["ckpt_name"] == _SHELF_FILENAME
    assert graph["3"]["inputs"]["steps"] == 33
    assert graph["3"]["inputs"]["model"] == ["2", 0]
    # Whatever is in the source, no exported string may be the prompt.
    assert EXPORT_PROMPT not in json.dumps(payload)


def test_an_export_leaves_out_a_forgotten_lora_a_picture_still_names(exportable):
    """The acceptance case: the name is in the picture and not in the file."""
    payload = exportable.owner.get(f"{API}/workflows/{RUN_CARD}/export").json()
    assert exportable.graph["2"]["inputs"]["lora_name"] == FORGOTTEN_LORA
    assert payload["workflow"]["2"]["inputs"]["lora_name"] == ""
    assert FORGOTTEN_LORA not in json.dumps(payload)


def test_a_structural_lora_the_shelf_does_not_hold_is_still_left_out(exportable):
    """Marking the slot structural keeps the slot, never an unknown name.

    This is the assertion the LoRA rule alone cannot make. A structural mark
    says "this LoRA is part of the workflow", so the slot is not emptied for
    being a look — and the name still goes, because the shelf cannot vouch for
    it. Without `unvouched_model_values` this test goes red and the one above
    stays green, which is why both exist.
    """
    _mark_slot(exportable.server, _lora_slot_label(exportable.graph), "structural")
    payload = exportable.owner.get(f"{API}/workflows/{RUN_CARD}/export").json()
    assert payload["workflow"]["2"]["inputs"]["lora_name"] == ""
    assert FORGOTTEN_LORA not in json.dumps(payload)


def test_a_structural_lora_that_is_on_the_shelf_travels_with_the_workflow(
    runnable, monkeypatch
):
    """The positive control: a lightning LoRA IS the workflow, so it is kept."""
    graph = _embedded_export_graph(lora=RUN_ADAPTER_FILENAME)
    monkeypatch.setattr(
        workflows_routes, "_load_embedded_api_prompt", lambda server, pid: graph
    )
    _mark_slot(runnable.server, _lora_slot_label(graph), "structural")
    payload = runnable.owner.get(f"{API}/workflows/{RUN_CARD}/export").json()
    assert payload["workflow"]["2"]["inputs"]["lora_name"] == RUN_ADAPTER_FILENAME
    assert "LoRA slots that are part of the look" not in payload["removed"]


def test_an_export_names_the_categories_it_removed_and_never_the_values(exportable):
    """`removed` is what a client renders; a value in it would be the leak itself."""
    payload = exportable.owner.get(f"{API}/workflows/{RUN_CARD}/export").json()
    assert set(payload["removed"]) == {
        "model names this machine does not hold",
        "node titles",
        "picture file names",
        "prompts",
        "seeds",
        "where the pictures were saved",
    }, payload["removed"]
    # The forgotten LoRA is reported as a MODEL NAME and not as "a LoRA that is
    # part of the look", which is the opposite fact. Both blank it; only one of
    # them tells the owner what actually happened in the case this feature
    # leads with.
    assert "LoRA slots that are part of the look" not in payload["removed"]


def test_an_export_refuses_a_graph_it_cannot_read_rather_than_publishing_it(
    runnable, monkeypatch
):
    """Which nodes carry prose comes out of the reduction: no reduction, no export."""
    monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        # Node-shaped enough to survive `sanitize_prompt_graph` and refused by
        # the reducer: `inputs` is not a mapping.
        lambda server, pid: {"1": {"class_type": "KSampler", "inputs": ["nope"]}},
    )
    r = runnable.owner.get(f"{API}/workflows/{RUN_CARD}/export")
    assert r.status_code == 409, r.text


def test_exporting_a_card_with_no_graph_at_all_says_so(workflow_env):
    """A card whose three tiers all answer nothing is a 409, not an empty file.

    BINNED_CARD is the one: no file, its only picture soft-deleted (so no
    embedded graph is reachable) and no instance document of its own.
    """
    r = workflow_env.owner.get(f"{API}/workflows/{BINNED_CARD}/export")
    assert r.status_code == 409, r.text
    assert "no graph" in r.json()["detail"].lower()


def test_exporting_an_unknown_card_is_a_404(workflow_env):
    assert (
        workflow_env.owner.get(f"{API}/workflows/{_h('nope')}/export").status_code
        == 404
    )


def test_duplicating_writes_a_runnable_file_the_original_does_not_lose(
    exportable, tmp_path
):
    """Duplicate is for the owner's own machine, so it is NOT scrubbed.

    A copy with its prompt and its models blanked would not run, and running it
    in ComfyUI is the entire reason the gesture exists.
    """
    _isolate_workflow_folders(tmp_path, exportable.monkeypatch)
    r = exportable.owner.post(f"{API}/workflows/{RUN_CARD}/duplicate")
    assert r.status_code == 201, r.text
    name = r.json()["name"]
    written = json.loads((tmp_path / name).read_text())
    assert written["5"]["inputs"]["text"] == EXPORT_PROMPT
    assert written["2"]["inputs"]["lora_name"] == FORGOTTEN_LORA
    assert written["3"]["inputs"]["seed"] == 4242


def test_duplicating_twice_puts_a_second_file_beside_the_first(exportable, tmp_path):
    """The `(2)` counter, so a duplicate never overwrites the one before it."""
    _isolate_workflow_folders(tmp_path, exportable.monkeypatch)
    first = exportable.owner.post(f"{API}/workflows/{RUN_CARD}/duplicate").json()[
        "name"
    ]
    second = exportable.owner.post(f"{API}/workflows/{RUN_CARD}/duplicate").json()[
        "name"
    ]
    assert first != second, "the second duplicate overwrote the first"
    assert (tmp_path / first).is_file() and (tmp_path / second).is_file()


def test_deleting_a_card_the_library_knows_from_its_pictures_is_refused(workflow_env):
    """Found workflows are hide-only, and the refusal says which gesture to use."""
    r = workflow_env.owner.delete(f"{API}/workflows/{BUSY_CARD}")
    assert r.status_code == 409, r.text
    assert "hide" in r.json()["detail"].lower()
    # Nothing went: the card is still on the grid.
    assert (
        workflow_env.owner.get(f"{API}/workflows/cards/{BUSY_CARD}").status_code == 200
    )


def test_deleting_an_imported_workflow_trashes_it_and_takes_it_off_the_card(
    runnable, tmp_path
):
    """The file goes, the card and its variants stay: they are made by pictures."""
    _isolate_workflow_folders(tmp_path, runnable.monkeypatch)
    (tmp_path / "imported.json").write_text(json.dumps(RUN_DOCUMENT))
    with runnable.server.hub.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_file "
            "(workflow_name, workflow_key, topology_hash, structural_hash) "
            "VALUES ('imported.json', ?, ?, ?)",
            (RUN_CARD, RUN_TOPOLOGY, RUN_RECIPE),
        )
    r = runnable.owner.delete(f"{API}/workflows/{RUN_CARD}")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": "imported.json", "workflow_key": RUN_CARD}
    assert not (tmp_path / "imported.json").exists()
    assert (
        runnable.server.hub.fetchall(
            "SELECT 1 FROM workflow_file WHERE workflow_name = 'imported.json'"
        )
        == []
    )
    assert runnable.owner.get(f"{API}/workflows/cards/{RUN_CARD}").status_code == 200


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
        lambda server, pid: json.loads(json.dumps(LOADERLESS_DOCUMENT)),
    )
    runnable.monkeypatch.setattr(
        workflows_routes,
        "_read_object_info",
        lambda url: (json.loads(json.dumps(LOADERLESS_OBJECT_INFO)), None),
    )
    return SimpleNamespace(tmp_path=tmp_path, **vars(runnable))


def test_inserting_a_lora_loader_writes_a_copy_with_a_slot_to_swap_into(loaderless):
    """The #1376 splice, kept: a new file whose LoRA slot is there to be filled."""
    r = loaderless.owner.post(f"{API}/workflows/{RUN_CARD}/insert-lora-loader")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["class_type"] == "LoraLoaderModelOnly"
    written = json.loads((loaderless.tmp_path / body["name"]).read_text())
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
    body = loaderless.owner.post(
        f"{API}/workflows/{RUN_CARD}/insert-lora-loader"
    ).json()
    assert json.loads(original.read_text()) == LOADERLESS_DOCUMENT
    written = json.loads((loaderless.tmp_path / body["name"]).read_text())
    assert written != LOADERLESS_DOCUMENT
    assert len(written) == len(LOADERLESS_DOCUMENT) + 1


def test_inserting_a_loader_into_a_workflow_that_has_one_is_refused_with_the_reason(
    runnable, tmp_path
):
    """Stacking a second adapter silently is the failure #1376 refuses."""
    _isolate_workflow_folders(tmp_path, runnable.monkeypatch)
    runnable.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid: _embedded_export_graph(),
    )
    r = runnable.owner.post(f"{API}/workflows/{RUN_CARD}/insert-lora-loader")
    assert r.status_code == 409, r.text
    assert "lora" in r.json()["detail"].lower()


def test_inserting_a_loader_without_comfyui_is_a_503_not_a_guess(runnable, tmp_path):
    """An API link carries no type, so with no `object_info` a reader could be missed."""
    _isolate_workflow_folders(tmp_path, runnable.monkeypatch)
    runnable.monkeypatch.setattr(
        workflows_routes,
        "_load_embedded_api_prompt",
        lambda server, pid: json.loads(json.dumps(LOADERLESS_DOCUMENT)),
    )
    runnable.monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda url: (None, "connection refused")
    )
    r = runnable.owner.post(f"{API}/workflows/{RUN_CARD}/insert-lora-loader")
    assert r.status_code == 503, r.text
    assert list(tmp_path.glob("*.json")) == [], "a file was written anyway"


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
    assert len(download_name("x" * 400)) <= 105
