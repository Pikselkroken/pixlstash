"""Workflow identity: the two hash tiers, subgraph inlining, and the hub store.

The fixtures here are the contract from
``pixlstash-hash-field-classification.md``, and two groups of them exist
because a rule shipped without them was found broken by measurement:

* **§Node identity's six invariants.** The superseded canonicalization
  relabelled nodes by topological sort and tie-broke on ``(class_type, input
  signature)`` — which does not break the tie two twin ``CLIPTextEncode`` nodes
  produce, so the "canonical" id fell through to JSON serialisation order. 12
  of 40 real workflows changed their structural hash when only the key order
  moved. :func:`test_reshuffled_key_order_keys_the_same` is that case.
* **§Subgraphs' five.** The API format is already flat, so the structural hash
  needs nothing; the UI format is not, and 20% of the owner's images use
  subgraphs. :func:`test_ui_keys_differ_without_inlining` asserts the two keys
  **differ** when inlining is skipped, precisely so the step cannot be dropped
  and go unnoticed.
"""

import copy
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from sqlalchemy.exc import OperationalError
from sqlmodel import delete as sqlmodel_delete, select

from pixlstash.db_models import DeletedFileLog, Picture
from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflows import (
    PictureGhost,
    assets_for_recipe,
    forget_asset_names,
    get_document,
    record_api_graph,
    record_picture_ghosts,
    recipes_for_topology,
)
from pixlstash.services.workflow_hash import (
    HASH_VERSION,
    MissingSubgraphDefinitionError,
    asset_reference,
    assets_from_reduction,
    instance_hash,
    WorkflowGraphError,
    reduce_api_graph,
    reduce_ui_graph,
    structural_document,
    structural_hash,
    topology_hash,
    ui_topology_hash,
)
from pixlstash.services.scrapheap_service import purge_scrapheap_pictures
from pixlstash.services.workflow_ghost_service import (
    DEFAULT_GHOST_RETENTION,
    GHOST_RETENTION_COVERED,
    GHOST_RETENTION_KEY,
    GHOST_RETENTION_OFF,
    GHOST_RETENTION_ON,
    GhostPurgeContext,
    apply_purge_to_hub,
    collect_ghost_candidates_in_session,
    read_ghost_retention,
    surviving_instance_hashes_in_session,
)
from pixlstash.services.workflow_library_service import topology_picture_counts
from pixlstash.tasks.comfyui_extraction_task import ComfyUIExtractionTask
from pixlstash.tasks.missing_file_purge_task import MissingFilePurgeTask
from pixlstash.tasks.missing_comfyui_extraction_finder import (
    MissingComfyUIExtractionFinder,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils

# One ordinary txt2img graph, held once in a format-neutral shape so the API
# and UI builders below cannot drift apart. Nodes 2 and 3 are the twin
# CLIPTextEncode pair the automorphism case turns on.
#
# (node id, class, [(input name, source id, source slot)], {widget: value})
TXT2IMG = [
    (1, "CheckpointLoaderSimple", [], {"ckpt_name": "sd_xl_base_1.0.safetensors"}),
    (2, "CLIPTextEncode", [("clip", 1, 1)], {"text": "a lighthouse at dusk"}),
    (3, "CLIPTextEncode", [("clip", 1, 1)], {"text": "blurry, watermark"}),
    (4, "EmptyLatentImage", [], {"width": 1024, "height": 1024, "batch_size": 1}),
    (
        5,
        "KSampler",
        [
            ("model", 1, 0),
            ("positive", 2, 0),
            ("negative", 3, 0),
            ("latent_image", 4, 0),
        ],
        {
            "seed": 42,
            "steps": 20,
            "cfg": 7.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
        },
    ),
    (6, "VAEDecode", [("samples", 5, 0), ("vae", 1, 2)], {}),
    (7, "SaveImage", [("images", 6, 0)], {"filename_prefix": "ComfyUI"}),
]

# An upscale graph: a real workflow shape with no CLIPTextEncode anywhere, so it
# hashes and has a recipe but genuinely carries no prompt. The fixture for §5's
# "a ghost never keeps the prompt ALONE, but a prompt need not exist" rule.
UPSCALE = [
    (1, "LoadImage", [], {"image": "in.png"}),
    (2, "UpscaleModelLoader", [], {"model_name": "x4-upscaler.pth"}),
    (3, "ImageUpscaleWithModel", [("upscale_model", 2, 0), ("image", 1, 0)], {}),
    (4, "SaveImage", [("images", 3, 0)], {"filename_prefix": "upscaled"}),
]

SUBGRAPH_UUID = "7b34ab90-36f9-45ba-a665-71d418f0df18"
INNER_SUBGRAPH_UUID = "1f2e3d4c-5b6a-4978-8695-a4b3c2d1e0f9"


def api_graph(spec, *, prefix=""):
    """Render the neutral spec as ComfyUI API format.

    ``prefix`` gives every node a colon-path id, which is exactly what a
    subgraph produces in a real API graph. Nothing in the hash reads an id, so
    this must not change a single key.
    """
    graph = {}
    for node_id, class_type, links, widgets in spec:
        inputs = {name: [f"{prefix}{src}", slot] for name, src, slot in links}
        inputs.update(widgets)
        graph[f"{prefix}{node_id}"] = {"class_type": class_type, "inputs": inputs}
    return graph


def ui_node(node_id, class_type, input_names, *, mode=0, outputs=None):
    return {
        "id": node_id,
        "type": class_type,
        "mode": mode,
        "inputs": [{"name": name} for name in input_names],
        "outputs": [{"name": name} for name in (outputs or ())],
    }


def ui_workflow(spec):
    """Render the neutral spec as a flat ComfyUI UI-format workflow."""
    nodes, links = [], []
    for node_id, class_type, node_links, _widgets in spec:
        nodes.append(ui_node(node_id, class_type, [name for name, _, _ in node_links]))
        for slot, (_name, src, src_slot) in enumerate(node_links):
            links.append([len(links) + 1, src, src_slot, node_id, slot, "*"])
    return {"nodes": nodes, "links": links}


def _sub_links(pairs):
    """Subgraph definitions spell their links as objects, not arrays.

    Both spellings occur in one real file, so both are exercised: the top level
    of every workflow built here uses the array form.
    """
    return [
        {
            "id": index + 1,
            "origin_id": origin,
            "origin_slot": origin_slot,
            "target_id": target,
            "target_slot": target_slot,
            "type": "*",
        }
        for index, (origin, origin_slot, target, target_slot) in enumerate(pairs)
    ]


def subgraph_ui_workflow():
    """The same graph with nodes 4 and 5 moved inside one subgraph.

    The instance node lists only the three inputs it wires while the definition
    declares them in its own order, which is the shape measured in a real file
    (3 against 7) and the reason the boundary is mapped by name.
    """
    definition = {
        "id": SUBGRAPH_UUID,
        "name": "sampler",
        "inputNode": {"id": -10},
        "outputNode": {"id": -20},
        "inputs": [{"name": "model"}, {"name": "positive"}, {"name": "negative"}],
        "outputs": [{"name": "LATENT"}],
        "nodes": [
            ui_node(4, "EmptyLatentImage", [], outputs=["LATENT"]),
            ui_node(
                5,
                "KSampler",
                ["model", "positive", "negative", "latent_image"],
                outputs=["LATENT"],
            ),
        ],
        "links": _sub_links(
            [
                (-10, 0, 5, 0),
                (-10, 1, 5, 1),
                (-10, 2, 5, 2),
                (4, 0, 5, 3),
                (5, 0, -20, 0),
            ]
        ),
    }
    nodes = [
        ui_node(1, "CheckpointLoaderSimple", [], outputs=["MODEL", "CLIP", "VAE"]),
        ui_node(2, "CLIPTextEncode", ["clip"], outputs=["CONDITIONING"]),
        ui_node(3, "CLIPTextEncode", ["clip"], outputs=["CONDITIONING"]),
        ui_node(
            10, SUBGRAPH_UUID, ["model", "positive", "negative"], outputs=["LATENT"]
        ),
        ui_node(6, "VAEDecode", ["samples", "vae"], outputs=["IMAGE"]),
        ui_node(7, "SaveImage", ["images"]),
    ]
    edges = [
        (1, 1, 2, 0),
        (1, 1, 3, 0),
        (1, 0, 10, 0),
        (2, 0, 10, 1),
        (3, 0, 10, 2),
        (10, 0, 6, 0),
        (1, 2, 6, 1),
        (6, 0, 7, 0),
    ]
    links = [
        [index + 1, origin, origin_slot, target, target_slot, "*"]
        for index, (origin, origin_slot, target, target_slot) in enumerate(edges)
    ]
    return {"nodes": nodes, "links": links, "definitions": {"subgraphs": [definition]}}


def nested_subgraph_ui_workflow():
    """Two levels: EmptyLatentImage moves one subgraph deeper again.

    Real data carries ``a:b:c`` ids, so inlining has to recurse rather than
    expand one level and stop.
    """
    workflow = copy.deepcopy(subgraph_ui_workflow())
    outer = workflow["definitions"]["subgraphs"][0]
    inner = {
        "id": INNER_SUBGRAPH_UUID,
        "name": "latent",
        "inputNode": {"id": -10},
        "outputNode": {"id": -20},
        "inputs": [],
        "outputs": [{"name": "LATENT"}],
        "nodes": [ui_node(4, "EmptyLatentImage", [], outputs=["LATENT"])],
        "links": _sub_links([(4, 0, -20, 0)]),
    }
    outer["nodes"] = [
        ui_node(20, INNER_SUBGRAPH_UUID, [], outputs=["LATENT"]),
        ui_node(
            5,
            "KSampler",
            ["model", "positive", "negative", "latent_image"],
            outputs=["LATENT"],
        ),
    ]
    outer["links"] = _sub_links(
        [(-10, 0, 5, 0), (-10, 1, 5, 1), (-10, 2, 5, 2), (20, 0, 5, 3), (5, 0, -20, 0)]
    )
    workflow["definitions"]["subgraphs"].append(inner)
    return workflow


def edited(spec, node_id, **widgets):
    """Return the spec with one node's widgets overridden."""
    return [
        (nid, cls, links, {**values, **widgets} if nid == node_id else values)
        for nid, cls, links, values in spec
    ]


# ---------------------------------------------------------------------------
# §Node identity — the six invariants
# ---------------------------------------------------------------------------


def test_every_node_id_replaced_keys_the_same():
    assert structural_hash(api_graph(TXT2IMG)) == structural_hash(
        api_graph(TXT2IMG, prefix="900")
    )


def test_reshuffled_key_order_keys_the_same():
    """The fixture whose absence let the superseded rule ship broken."""
    graph = api_graph(TXT2IMG)
    reshuffled = dict(reversed(list(graph.items())))
    for node in reshuffled.values():
        node["inputs"] = dict(reversed(list(node["inputs"].items())))
    assert list(reshuffled) != list(graph)
    assert structural_hash(reshuffled) == structural_hash(graph)


def test_twin_text_encoders_key_the_same_in_either_order():
    """The automorphism case, stated directly rather than left to chance.

    Nodes 2 and 3 are identical on class, on widget names and on upstream
    structure. Their prompts are bucket P and nulled, so after nulling they are
    genuine twins and swapping which one feeds ``positive`` must not move the
    key.
    """
    graph = api_graph(TXT2IMG)
    swapped = copy.deepcopy(graph)
    swapped["5"]["inputs"]["positive"] = ["3", 0]
    swapped["5"]["inputs"]["negative"] = ["2", 0]
    assert structural_hash(swapped) == structural_hash(graph)


def test_seed_change_keys_the_same():
    assert structural_hash(
        api_graph(edited(TXT2IMG, 5, seed=99999))
    ) == structural_hash(api_graph(TXT2IMG))


def test_prompt_edit_keeps_the_same_recipe():
    """Bucket P is nulled, so the recipe survives an edited prompt.

    The instance tier is what forks here, and it is a later step's to write.
    """
    assert structural_hash(
        api_graph(edited(TXT2IMG, 2, text="a lighthouse at dawn"))
    ) == structural_hash(api_graph(TXT2IMG))


def test_model_swap_forks_the_recipe():
    swapped = api_graph(edited(TXT2IMG, 1, ckpt_name="dreamshaper_8.safetensors"))
    assert structural_hash(swapped) != structural_hash(api_graph(TXT2IMG))


def test_node_deleted_forks_the_recipe():
    trimmed = api_graph(TXT2IMG)
    del trimmed["7"]
    assert structural_hash(trimmed) != structural_hash(api_graph(TXT2IMG))


def test_rewiring_forks_the_key_with_the_nodes_left_alone():
    """The **edges** half of "node classes and named-input edges".

    Every other fork fixture forks by removing a node or changing a widget, so
    all of them would still pass with the edges deleted from the key entirely —
    at which point topology degenerates into the class multiset the library
    plan retired. This one holds the node multiset and every widget constant
    and moves one connection: VAEDecode takes the raw latent instead of the
    sampled one, which is a different workflow made of the same parts.
    """
    rewired = api_graph(TXT2IMG)
    rewired["6"]["inputs"]["samples"] = ["4", 0]
    assert structural_hash(rewired) != structural_hash(api_graph(TXT2IMG))
    assert topology_hash(rewired) != topology_hash(api_graph(TXT2IMG))


def test_refinement_separates_graphs_that_agree_one_hop_out():
    """Weisfeiler-Leman refinement, and the reason the module runs it at all.

    Both graphs hold the same nodes with the same widgets, and every node sees
    the same neighbour *classes*. They differ only in that one shares a loader
    between both text encoders while the other gives each its own. Nothing a
    single hop can see tells them apart, so without refinement they collide —
    which is the whole failure mode the corrected rule exists to answer, one
    tier down from the twin-node case.
    """
    shared = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "a.safetensors"},
        },
        "2": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "a.safetensors"},
        },
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1]}},
    }
    split = copy.deepcopy(shared)
    split["4"]["inputs"]["clip"] = ["2", 1]
    assert structural_hash(shared) != structural_hash(split)
    assert topology_hash(shared) != topology_hash(split)


def test_a_two_number_widget_is_not_read_as_a_connection():
    """A resolution pair is bucket P, not an edge.

    Reading ``[1024, 1024]`` as ``[node_id, slot]`` puts a parameter into the
    topology, which the spec calls the unrecoverable direction — it shatters
    grouping into near-duplicate recipes — and writes a link to a node that
    does not exist into the stored graph. All 501,128 links measured across the
    owner's libraries carry a string node id, so the class is closed on that.
    """
    spec = edited(TXT2IMG, 4, resolution=[1024, 1024])
    baseline = structural_hash(api_graph(spec))
    assert (
        structural_hash(api_graph(edited(spec, 4, resolution=[768, 768]))) == baseline
    )
    assert structural_document(api_graph(spec))["4"]["inputs"]["resolution"] is None


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------


def test_filename_prefix_change_keys_the_same():
    assert structural_hash(
        api_graph(edited(TXT2IMG, 7, filename_prefix="lighthouses/final"))
    ) == structural_hash(api_graph(TXT2IMG))


def test_pixlstash_loader_keys_on_the_digest_not_the_pointer():
    """``lora_sha256`` is the identity; ``pixlstash_lora_id`` is install-local.

    Named rather than extension-shaped, so without an explicit rule a LoRA swap
    on a PixlStash node would leave the recipe unchanged — the one direction
    the spec calls unrecoverable.
    """
    spec = TXT2IMG + [
        (
            8,
            "PixlStashLoraLoader",
            [("model", 1, 0)],
            {"lora_sha256": "a" * 64, "pixlstash_lora_id": 17, "strength_model": 0.8},
        )
    ]
    baseline = structural_hash(api_graph(spec))
    assert structural_hash(api_graph(edited(spec, 8, pixlstash_lora_id=41))) == baseline
    assert structural_hash(api_graph(edited(spec, 8, strength_model=0.6))) == baseline
    assert structural_hash(api_graph(edited(spec, 8, lora_sha256="b" * 64))) != baseline


def test_a_prompt_ending_in_an_image_extension_is_not_an_asset():
    """The extension rules are name-blind, and this is their one bad case.

    Read as a topology asset, a prompt both forks the recipe on a prompt-only
    edit and lands in the stored graph, which is exactly what library plan §5's
    prompt-free guarantee forbids. Prose-carrying inputs are therefore excluded
    before the extension test.
    """
    spec = edited(TXT2IMG, 2, text="a photorealistic cat.png")
    assert structural_hash(api_graph(spec)) == structural_hash(api_graph(TXT2IMG))
    assert structural_document(api_graph(spec))["2"]["inputs"]["text"] is None
    # A newline or an over-long value is prose whatever the field is called.
    assert structural_hash(
        api_graph(edited(TXT2IMG, 4, note="a cat\non a mat.png"))
    ) == structural_hash(api_graph(edited(TXT2IMG, 4, note="a dog\nin a bog.png")))


def test_an_asset_filename_containing_a_space_is_still_an_asset():
    """The guard above must not catch real files.

    Measured: 5,066 genuine `lora_name` and `image` values in the owner's
    libraries contain a space, so "has a space" would have been a wrong rule
    and this is the fixture that says so.
    """
    spaced = api_graph(edited(TXT2IMG, 1, ckpt_name="Some Model v2.safetensors"))
    assert structural_hash(spaced) != structural_hash(api_graph(TXT2IMG))
    # Kept as an asset -- but the DOCUMENT names it by reference, so what is
    # asserted here is that the normalized name reached the asset list.
    assert structural_document(spaced)["1"]["inputs"]["ckpt_name"] == asset_reference(
        "some model v2.safetensors"
    )
    assets = assets_from_reduction(reduce_api_graph(spaced))
    assert ("ckpt_name", "some model v2.safetensors") in assets


def test_a_malformed_node_is_refused_rather_than_keyed():
    """`str(None)` would otherwise become the perfectly ordinary class "None"."""
    with pytest.raises(WorkflowGraphError):
        structural_hash({"1": {"class_type": None, "inputs": {}}})
    with pytest.raises(WorkflowGraphError):
        structural_hash({"1": {"class_type": "", "inputs": {}}})
    with pytest.raises(WorkflowGraphError):
        structural_hash({"1": {"class_type": "SaveImage", "inputs": ["images"]}})
    # A sibling key that is not node-shaped at all is an envelope, not a defect.
    assert structural_hash({**api_graph(TXT2IMG), "extra_data": {}}) == structural_hash(
        api_graph(TXT2IMG)
    )


def test_structural_document_is_prompt_free_and_scrubs_credentials():
    spec = edited(TXT2IMG, 7, api_key="example-not-a-real-key")
    document = structural_document(api_graph(spec))
    rendered = json.dumps(document)
    assert "lighthouse" not in rendered
    assert "example-not-a-real-key" not in rendered
    assert "api_key" not in document["7"]["inputs"]
    # The graph itself survives intact: classes and edges.
    assert document["5"]["inputs"]["positive"] == ["2", 0]
    assert document["5"]["inputs"]["seed"] is None
    # The model is named by REFERENCE, never by name. A model filename is
    # bucket TA and survives the nulling, and on a real shelf it names a person
    # -- so the readable form lives only in `workflow_recipe_asset`.
    assert document["1"]["inputs"]["ckpt_name"] == asset_reference(
        "sd_xl_base_1.0.safetensors"
    )
    assert "sd_xl_base_1.0.safetensors" not in rendered


def test_an_unkeyable_graph_is_refused_rather_than_hashed():
    with pytest.raises(WorkflowGraphError):
        structural_hash({})
    with pytest.raises(WorkflowGraphError):
        structural_hash({"1": {"inputs": {}}})


# ---------------------------------------------------------------------------
# The topology tier
# ---------------------------------------------------------------------------


def test_topology_is_coarser_than_the_recipe():
    """A model swap forks the recipe and leaves the topology alone."""
    swapped = api_graph(edited(TXT2IMG, 1, ckpt_name="dreamshaper_8.safetensors"))
    assert topology_hash(swapped) == topology_hash(api_graph(TXT2IMG))
    assert structural_hash(swapped) != structural_hash(api_graph(TXT2IMG))


def test_topology_forks_when_the_graph_does():
    trimmed = api_graph(TXT2IMG)
    del trimmed["7"]
    assert topology_hash(trimmed) != topology_hash(api_graph(TXT2IMG))


def test_topology_agrees_across_the_two_serialisations():
    """The portability claim the drop target is built on.

    The same workflow, keyed from the executed API graph and from the UI
    document a user would drag in, has to land on one row.
    """
    assert ui_topology_hash(ui_workflow(TXT2IMG)) == topology_hash(api_graph(TXT2IMG))


def test_ui_only_and_bypassed_nodes_do_not_reach_the_key():
    workflow = ui_workflow(TXT2IMG)
    workflow["nodes"].append(ui_node(90, "Note", [], outputs=[]))
    workflow["nodes"].append(ui_node(91, "PreviewImage", ["images"], mode=4))
    workflow["links"].append([len(workflow["links"]) + 1, 6, 0, 91, 0, "IMAGE"])
    assert ui_topology_hash(workflow) == topology_hash(api_graph(TXT2IMG))


def test_a_reroute_is_stepped_through():
    """A Reroute exists only in the UI graph, so it must not key as a node."""
    workflow = ui_workflow(TXT2IMG)
    workflow["nodes"].append(ui_node(92, "Reroute", ["in"], outputs=["out"]))
    # VAEDecode's samples input now arrives via the reroute instead of directly.
    for link in workflow["links"]:
        if link[3] == 6 and link[4] == 0:
            link[3], link[4] = 92, 0
    workflow["links"].append([len(workflow["links"]) + 1, 92, 0, 6, 0, "LATENT"])
    assert ui_topology_hash(workflow) == topology_hash(api_graph(TXT2IMG))


# ---------------------------------------------------------------------------
# §Subgraphs — the five fixtures that section names
# ---------------------------------------------------------------------------


def test_api_structural_hash_ignores_subgraph_authoring():
    """Flat and nested API graphs key identically; a colon path is just an id."""
    nested = api_graph(TXT2IMG, prefix="75:")
    assert structural_hash(nested) == structural_hash(api_graph(TXT2IMG))
    assert topology_hash(nested) == topology_hash(api_graph(TXT2IMG))


def test_ui_keys_match_once_subgraphs_are_inlined():
    assert ui_topology_hash(subgraph_ui_workflow()) == ui_topology_hash(
        ui_workflow(TXT2IMG)
    )


def test_ui_keys_differ_without_inlining():
    """The step cannot be silently dropped, because this asserts it is missing.

    Without inlining the 7-node graph keys as 6 nodes, one of them typed by a
    UUID that is generated per definition and so differs between two people who
    built the same workflow.
    """
    assert ui_topology_hash(subgraph_ui_workflow(), inline=False) != ui_topology_hash(
        ui_workflow(TXT2IMG)
    )


def test_inlining_recurses_through_two_levels():
    assert ui_topology_hash(nested_subgraph_ui_workflow()) == ui_topology_hash(
        ui_workflow(TXT2IMG)
    )


def test_a_missing_subgraph_definition_is_reported():
    """Never treated as a leaf: an instance stands for its whole definition."""
    workflow = subgraph_ui_workflow()
    workflow["definitions"]["subgraphs"] = []
    with pytest.raises(MissingSubgraphDefinitionError):
        ui_topology_hash(workflow)


def test_a_bypassed_subgraph_takes_its_whole_contents_with_it():
    """ComfyUI does not execute a bypassed instance, so neither do we.

    Expanding it anyway leaves its inner nodes standing while every edge
    through it disappears — a key for a graph that has never run. A bypassed
    *ordinary* node already drops correctly, and this is the same rule applied
    one level down.
    """
    workflow = subgraph_ui_workflow()
    for node in workflow["nodes"]:
        if node["type"] == SUBGRAPH_UUID:
            node["mode"] = 4
    reduced = reduce_ui_graph(workflow)
    assert not any(":" in key for key in reduced)
    assert sorted(node.class_type for node in reduced.values()) == [
        "CLIPTextEncode",
        "CLIPTextEncode",
        "CheckpointLoaderSimple",
        "SaveImage",
        "VAEDecode",
    ]


def test_a_definition_that_serialises_its_own_io_nodes_keys_the_same():
    """The synthetic boundary nodes must survive the definition's node list.

    Installed before it, they would be overwritten by any definition that
    serialises ``inputNode``/``outputNode`` into ``nodes``, and both boundaries
    would key as ordinary graph nodes — nine emitted for a seven-node graph.
    """
    workflow = subgraph_ui_workflow()
    definition = workflow["definitions"]["subgraphs"][0]
    definition["nodes"] = definition["nodes"] + [
        ui_node(-10, "SubgraphInputNode", [], outputs=["model"]),
        ui_node(-20, "SubgraphOutputNode", ["LATENT"]),
    ]
    assert len(reduce_ui_graph(workflow)) == len(TXT2IMG)
    assert ui_topology_hash(workflow) == ui_topology_hash(ui_workflow(TXT2IMG))


def test_a_passthrough_cycle_is_refused_rather_than_keyed():
    """Refused, not quietly keyed over a graph missing an edge."""
    workflow = ui_workflow(TXT2IMG)
    workflow["nodes"].append(ui_node(93, "Reroute", ["in"], outputs=["out"]))
    workflow["nodes"].append(ui_node(94, "Reroute", ["in"], outputs=["out"]))
    workflow["links"].append([len(workflow["links"]) + 1, 94, 0, 93, 0, "*"])
    workflow["links"].append([len(workflow["links"]) + 1, 93, 0, 94, 0, "*"])
    for link in workflow["links"]:
        if link[3] == 7 and link[4] == 0:
            link[1] = 93
    with pytest.raises(WorkflowGraphError):
        ui_topology_hash(workflow)


def test_a_link_to_a_node_that_does_not_exist_is_refused():
    """Dropping it would key the same as the graph with that edge deleted.

    Worse, the API-side reduction keeps its dangling edges, so silently
    dropping this one lets the two serialisations of one workflow disagree —
    which is the portability claim the topology tier exists to make.
    """
    workflow = ui_workflow(TXT2IMG)
    workflow["links"].append([len(workflow["links"]) + 1, 404, 0, 7, 0, "*"])
    with pytest.raises(WorkflowGraphError):
        ui_topology_hash(workflow)


def test_the_ui_topology_of_a_subgraph_workflow_matches_its_api_graph():
    """The pairing the drop target actually performs, with subgraphs in play."""
    assert ui_topology_hash(subgraph_ui_workflow()) == topology_hash(
        api_graph(TXT2IMG, prefix="10:")
    )


# ---------------------------------------------------------------------------
# B1 — the hub tables
# ---------------------------------------------------------------------------

WORKFLOW_TABLES = ("workflow_topology", "workflow_recipe", "workflow_recipe_graph")

# Every workflow table including the asset child and the ghost store, deepest
# first. Separate from WORKFLOW_TABLES above because that one is asserted on
# one-row-per-recipe counts and a recipe names several assets, and because a
# ghost is not a recipe row at all.
WORKFLOW_WIPE_ORDER = (
    "workflow_recipe_asset",
    "workflow_recipe_graph",
    "workflow_recipe",
    "workflow_topology",
    "workflow_picture_ghost",
)


@pytest.fixture
def hub(tmp_path):
    database = HubDatabase(str(tmp_path / "hub.db"))
    try:
        yield database
    finally:
        database.close()


def test_the_workflow_tables_land_in_the_hub(hub):
    """The irreversible decision, asserted where it is made.

    70% of the owner's recipes appear in more than one library, so these rows
    are per-machine and not per-vault. A vault references them by hash, which
    is why nothing here is a cross-database foreign key.
    """
    present = {
        row[0]
        for row in hub.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert set(WORKFLOW_TABLES) <= present


def test_reopening_a_hub_is_a_no_op(tmp_path):
    path = str(tmp_path / "hub.db")
    first = HubDatabase(path)
    try:
        keys = record_api_graph(first, api_graph(TXT2IMG))
    finally:
        first.close()
    second = HubDatabase(path)
    try:
        assert get_document(second, keys.structural_hash) is not None
    finally:
        second.close()


def test_recording_the_same_graph_twice_writes_one_row(hub):
    """Idempotent, so the backfill can be re-run without a reconciliation pass.

    The second graph is the same workflow with every node id replaced, which is
    the "rebuilt from scratch" case: the identity is unchanged, the document
    text is not, and only one document survives per recipe.
    """
    first = record_api_graph(hub, api_graph(TXT2IMG))
    second = record_api_graph(hub, api_graph(TXT2IMG, prefix="500"))
    assert first == second
    for table in WORKFLOW_TABLES:
        assert hub.fetchone(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 1
    # First one filed wins, rather than the last write silently replacing it.
    assert get_document(hub, first.structural_hash) == structural_document(
        api_graph(TXT2IMG)
    )


def test_two_recipes_share_one_topology(hub):
    first = record_api_graph(hub, api_graph(TXT2IMG))
    second = record_api_graph(
        hub, api_graph(edited(TXT2IMG, 1, ckpt_name="dreamshaper_8.safetensors"))
    )
    assert first.topology_hash == second.topology_hash
    assert first.structural_hash != second.structural_hash
    assert hub.fetchone("SELECT COUNT(*) AS n FROM workflow_topology")["n"] == 1
    assert {
        row["structural_hash"] for row in recipes_for_topology(hub, first.topology_hash)
    } == {first.structural_hash, second.structural_hash}


def test_the_stored_row_records_which_rule_keyed_it(hub):
    keys = record_api_graph(hub, api_graph(TXT2IMG))
    row = hub.fetchone(
        "SELECT hash_version, node_count FROM workflow_recipe WHERE structural_hash = ?",
        (keys.structural_hash,),
    )
    # The literal the spec names, not the module's own constant: comparing a
    # written value against the thing that wrote it asserts nothing.
    assert row["hash_version"] == "v1"
    assert row["node_count"] == len(TXT2IMG)


def test_the_stored_document_holds_neither_prompt_nor_model_name(hub):
    """Library plan §5, and the half of it the first draft of this file missed.

    Prompt-free was never the whole guarantee: bucket TA survives the nulling
    by design, and TA is model filenames, which on a real shelf name people. So
    the document names its assets by reference and `workflow_recipe_asset` is
    the only home of the readable form.
    """
    keys = record_api_graph(hub, api_graph(TXT2IMG))
    stored = hub.fetchone(
        "SELECT document FROM workflow_recipe_graph WHERE structural_hash = ?",
        (keys.structural_hash,),
    )["document"]
    assert "lighthouse" not in stored
    assert "sd_xl_base_1.0.safetensors" not in stored
    assert asset_reference("sd_xl_base_1.0.safetensors") in stored
    assert get_document(hub, keys.structural_hash) == structural_document(
        api_graph(TXT2IMG)
    )
    # And the name is recoverable, from the one place that holds it.
    assert ("ckpt_name", "sd_xl_base_1.0.safetensors") in {
        (row["widget_name"], row["normalized_filename"])
        for row in assets_for_recipe(hub, keys.structural_hash)
    }


def test_forgetting_a_model_name_rewrites_no_stored_graph(hub):
    """The property the whole indirection exists for.

    Destroying a model's readable name is a row delete. The document is not
    touched, so its `document_sha256` stays valid and no migration is owed --
    which is what made this worth doing BEFORE any backfill populated the
    table rather than after.
    """
    keys = record_api_graph(hub, api_graph(TXT2IMG))
    before = hub.fetchone(
        "SELECT document, document_sha256 FROM workflow_recipe_graph "
        "WHERE structural_hash = ?",
        (keys.structural_hash,),
    )

    removed = forget_asset_names(hub, "sd_xl_base_1.0.safetensors")

    assert removed == 1
    assert assets_for_recipe(hub, keys.structural_hash) == []
    after = hub.fetchone(
        "SELECT document, document_sha256 FROM workflow_recipe_graph "
        "WHERE structural_hash = ?",
        (keys.structural_hash,),
    )
    assert after["document"] == before["document"]
    assert after["document_sha256"] == before["document_sha256"]
    # The graph still says a model went there, and no longer says which.
    assert asset_reference("sd_xl_base_1.0.safetensors") in after["document"]


def test_an_asset_row_is_written_per_distinct_file(hub):
    """Two LoRA loaders naming different files are two rows, not one.

    The key is the triple, so one widget name carrying two files does not
    collapse -- which is why it is not `(structural_hash, widget_name)`.
    """
    spec = TXT2IMG + [
        (20, "LoraLoader", [("model", 1, 0)], {"lora_name": "one.safetensors"}),
        (21, "LoraLoader", [("model", 20, 0)], {"lora_name": "two.safetensors"}),
    ]
    keys = record_api_graph(hub, api_graph(spec))
    names = {
        (row["widget_name"], row["normalized_filename"])
        for row in assets_for_recipe(hub, keys.structural_hash)
    }
    assert ("lora_name", "one.safetensors") in names
    assert ("lora_name", "two.safetensors") in names


def test_an_existing_v2_hub_gains_the_tables_on_its_next_open(tmp_path):
    """The compatibility path `_apply_v2` is re-run for, exercised directly.

    These tables were amended into schema v2 rather than shipped as a v3, which
    only works because `apply_migrations` re-runs `_apply_v2` for a hub already
    at 2. A hub created by this build has them from its first open, so nothing
    else in this file touches that path — and a regression that stopped
    creating them for an existing hub would leave the whole suite green.
    """
    path = str(tmp_path / "hub.db")
    first = HubDatabase(path)
    try:
        assert record_api_graph(first, api_graph(TXT2IMG)) is not None
    finally:
        first.close()

    # An older v2 hub: the shape as it was before this change, version intact.
    scratch = sqlite3.connect(path)
    try:
        for table in WORKFLOW_WIPE_ORDER:
            scratch.execute(f"DROP TABLE {table}")
        scratch.commit()
        assert scratch.execute("SELECT version FROM schema_version").fetchone()[0] == 2
    finally:
        scratch.close()

    reopened = HubDatabase(path)
    try:
        present = {
            row[0]
            for row in reopened.fetchall(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            )
        }
        assert set(WORKFLOW_WIPE_ORDER) <= present
        assert "ix_workflow_recipe_topology" in present
        assert "ix_workflow_picture_ghost_instance" in present
        # Re-running the shape reconciliation must not claim a new version.
        assert reopened.fetchone("SELECT version FROM schema_version")["version"] == 2
        # And the recreated tables are usable, not just present.
        assert record_api_graph(reopened, api_graph(TXT2IMG)) is not None
    finally:
        reopened.close()


def test_a_graph_cannot_name_a_recipe_that_does_not_exist(hub):
    """The recipe foreign key, exercised on its own rather than through another.

    Every other column here is content-addressed and crosses no boundary, so
    this reference is the one piece of referential integrity the schema has.
    """
    record_api_graph(hub, api_graph(TXT2IMG))
    with pytest.raises(sqlite3.IntegrityError):
        with hub.transaction() as conn:
            conn.execute(
                "INSERT INTO workflow_recipe_graph (structural_hash, "
                "document_sha256, document, created_at) "
                "VALUES ('nosuchrecipe', 'deadbeef', '{}', 'now')"
            )


def test_a_recipe_cannot_name_a_topology_that_does_not_exist(hub):
    with pytest.raises(sqlite3.IntegrityError):
        with hub.transaction() as conn:
            conn.execute(
                "INSERT INTO workflow_recipe (structural_hash, topology_hash, "
                "hash_version, node_count, first_seen_at) "
                "VALUES ('r', 'nosuchtopology', 'v1', 1, 'now')"
            )


# ---------------------------------------------------------------------------
# The instance tier — a picture column, never a hub table
# ---------------------------------------------------------------------------


def test_a_seed_change_keeps_the_instance():
    """A generation is an instance plus a seed, so the seed is bucket V.

    Keep the seed in this key and every picture is its own instance and the
    tier answers nothing at all.
    """
    assert instance_hash(api_graph(edited(TXT2IMG, 5, seed=999))) == instance_hash(
        api_graph(TXT2IMG)
    )


def test_a_prompt_edit_forks_the_instance_but_not_the_recipe():
    """The one thing that separates this tier from the one above it."""
    edit = api_graph(edited(TXT2IMG, 2, text="a lighthouse at dawn"))
    assert structural_hash(edit) == structural_hash(api_graph(TXT2IMG))
    assert instance_hash(edit) != instance_hash(api_graph(TXT2IMG))


def test_a_parameter_change_forks_the_instance_but_not_the_recipe():
    edit = api_graph(edited(TXT2IMG, 5, steps=35))
    assert structural_hash(edit) == structural_hash(api_graph(TXT2IMG))
    assert instance_hash(edit) != instance_hash(api_graph(TXT2IMG))


def test_a_model_swap_forks_the_instance_too():
    """The tiers nest: a different recipe cannot be the same instance."""
    edit = api_graph(edited(TXT2IMG, 1, ckpt_name="dreamshaper_8.safetensors"))
    assert instance_hash(edit) != instance_hash(api_graph(TXT2IMG))


def test_the_instance_key_is_order_invariant_like_the_two_above_it():
    """Why this is a graph_key rather than a JSON blob of parameters.

    The spec words the tier as "structural hash plus canonical JSON of all P
    values", and a JSON of parameters has to key them by node id. Node ids are
    the one thing this module refuses to read: keying on them is the superseded
    rule that re-keyed 12 of 40 real workflows when only the serialisation
    order moved. Renaming every node must not move this key either.
    """
    assert instance_hash(api_graph(TXT2IMG, prefix="900")) == instance_hash(
        api_graph(TXT2IMG)
    )
    reshuffled = dict(reversed(list(api_graph(TXT2IMG).items())))
    assert instance_hash(reshuffled) == instance_hash(api_graph(TXT2IMG))


def test_a_credential_widget_reaches_no_tier_including_the_instance():
    """Dropped before bucketing, so it is absent from all three keys."""
    dirty = api_graph(edited(TXT2IMG, 7, api_key="example-not-a-real-key"))
    assert instance_hash(dirty) == instance_hash(api_graph(TXT2IMG))


def test_no_hub_table_stores_an_instance(hub):
    """Phase 2 creep, guarded rather than remembered.

    "Add an instance hash" reads like an invitation to build `recipe_instance`,
    and that table moved to v1.12 with the rest of the AI-toolkit work. The
    hash is a value on a picture; v1.11 stores no instance ROW anywhere.
    """
    record_api_graph(hub, api_graph(TXT2IMG))
    tables = {
        row[0]
        for row in hub.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert not [name for name in tables if "instance" in name.lower()]


# ---------------------------------------------------------------------------
# B3 — ingest writes to the hub, and the rows outlive their pictures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    """One vault and one hub for the whole section.

    Module-scoped because standing a ``VaultDatabase`` up runs every migration,
    which costs far more than any assertion below. Each test wipes what it wrote
    (``reset``), so the shared environment cannot leak state between them.
    """
    from pixlstash.database import VaultDatabase

    root = tmp_path_factory.mktemp("workflow_b3")
    images = root / "images"
    images.mkdir()
    vault = VaultDatabase(str(root / "vault.db"))
    hub_db = HubDatabase(str(root / "hub.db"))
    try:
        yield SimpleNamespace(vault=vault, hub=hub_db, image_root=str(images))
    finally:
        hub_db.close()
        vault.close()


@pytest.fixture(autouse=True)
def reset(request):
    """Empty both databases between tests in this section only."""
    yield
    if "store" not in request.fixturenames:
        return
    shared = request.getfixturevalue("store")
    shared.vault.run_task(lambda session: _wipe_pictures(session))
    with shared.hub.transaction() as conn:
        # Children first: every table here references the one above it.
        for table in WORKFLOW_WIPE_ORDER:
            conn.execute(f"DELETE FROM {table}")


def _wipe_pictures(session):
    # The purge tests below write permanent-deletion ledger rows, which are
    # keyed by PATH rather than by picture: leaving them behind would let one
    # test's destroyed path change what the next test's purge owns.
    session.exec(sqlmodel_delete(DeletedFileLog))
    session.exec(sqlmodel_delete(Picture))
    session.commit()


def write_png(directory, name, api=None):
    """A real PNG, carrying an API graph in its ``prompt`` chunk when given one."""
    info = PngInfo()
    if api is not None:
        info.add_text("prompt", json.dumps(api))
    path = directory / name
    Image.new("RGB", (4, 4), "black").save(path, pnginfo=info)
    return name


def add_picture(store, file_path, *, deleted=False):
    def insert(session):
        picture = Picture(file_path=file_path, deleted=deleted)
        session.add(picture)
        session.commit()
        return picture.id

    return store.vault.run_task(insert)


def read_picture(store, picture_id):
    return store.vault.run_immediate_read_task(
        lambda session: session.get(Picture, picture_id)
    )


def run_extraction(store, picture_ids, *, hub=True, on_hub_failure=None):
    task = ComfyUIExtractionTask(
        database=store.vault,
        image_root=store.image_root,
        pictures=[SimpleNamespace(id=pid) for pid in picture_ids],
        hub=store.hub if hub else None,
        on_hub_failure=on_hub_failure,
    )
    return task._run_task()


def unwritable_hub():
    """A hub whose every write raises, the way a read-only file would."""
    return SimpleNamespace(
        transaction=lambda: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O"))
    )


def test_ingest_files_the_workflow_and_stamps_the_picture(store):
    """One file read does both jobs: the ComfyUI fields and the library keys."""
    name = write_png(Path(store.image_root), "keyed.png", api=api_graph(TXT2IMG))
    picture_id = add_picture(store, name)

    result = run_extraction(store, [picture_id])

    assert result["found_workflow"] == 1
    picture = read_picture(store, picture_id)
    assert picture.workflow_hash_version == HASH_VERSION
    assert picture.workflow_topology_hash == topology_hash(api_graph(TXT2IMG))
    assert picture.workflow_structural_hash == structural_hash(api_graph(TXT2IMG))
    assert picture.workflow_instance_hash == instance_hash(api_graph(TXT2IMG))
    # The hub holds the graph the vault is now pointing at.
    assert get_document(store.hub, picture.workflow_structural_hash) is not None


def test_every_return_path_reports_the_same_keys(store):
    """A caller reading `found_workflow` must not depend on which path ran.

    `_run_task` returns from three places -- no pictures, nothing to persist,
    and the ordinary end -- and an early exit that omits a key is a KeyError in
    whatever reads the result later.
    """
    name = write_png(Path(store.image_root), "keys.png", api=api_graph(TXT2IMG))
    picture_id = add_picture(store, name)

    populated = run_extraction(store, [picture_id])
    empty = run_extraction(store, [])

    assert (
        set(empty) == set(populated) == {"checked", "found_comfyui", "found_workflow"}
    )


def test_a_picture_with_no_graph_is_marked_scanned_rather_than_re_read(store):
    """Absence is the ordinary case, and must not cost a second read."""
    name = write_png(Path(store.image_root), "bare.png")
    picture_id = add_picture(store, name)

    run_extraction(store, [picture_id])

    picture = read_picture(store, picture_id)
    assert picture.workflow_hash_version == HASH_VERSION
    assert picture.workflow_topology_hash is None
    assert picture.workflow_structural_hash is None
    assert picture.workflow_instance_hash is None
    # Nothing was filed, because there was nothing to file.
    assert store.hub.fetchone("SELECT COUNT(*) AS n FROM workflow_recipe")["n"] == 0


def test_a_missing_file_is_marked_scanned(store):
    """Otherwise the widened predicate hands the same broken row back forever."""
    picture_id = add_picture(store, "gone.png")

    run_extraction(store, [picture_id])

    assert read_picture(store, picture_id).workflow_hash_version == HASH_VERSION


def test_without_a_hub_nothing_claims_the_picture_was_scanned(store):
    """A vault opened by the CLI must not mark a scan it could not perform."""
    name = write_png(Path(store.image_root), "nohub.png", api=api_graph(TXT2IMG))
    picture_id = add_picture(store, name)

    run_extraction(store, [picture_id], hub=False)

    picture = read_picture(store, picture_id)
    assert picture.comfyui_models is not None
    assert picture.workflow_hash_version is None


def test_a_hub_that_cannot_be_written_leaves_the_picture_unscanned(store):
    """A transient failure must not be recorded as "this picture has no graph"."""
    name = write_png(Path(store.image_root), "unwritable.png", api=api_graph(TXT2IMG))
    picture_id = add_picture(store, name)

    task = ComfyUIExtractionTask(
        database=store.vault,
        image_root=store.image_root,
        pictures=[SimpleNamespace(id=picture_id)],
        hub=unwritable_hub(),
    )
    task._run_task()

    assert read_picture(store, picture_id).workflow_hash_version is None
    # The ComfyUI half still landed, so the pre-B3 predicate drains normally.
    assert read_picture(store, picture_id).comfyui_models is not None


def new_finder(store, *, hub=True):
    return MissingComfyUIExtractionFinder(
        database=store.vault,
        image_root=store.image_root,
        hub=store.hub if hub else None,
    )


def test_the_finder_stops_handing_back_a_scanned_picture(store):
    """The whole point of the third state, asserted on the query that reads it.

    **A second finder, deliberately.** ``_filter_and_claim`` marks a picture as
    handed out and only ``on_task_complete`` releases it, so re-asking the SAME
    finder returns None whether or not the column was ever written -- the
    assertion would pass with the whole persist removed. A fresh finder has an
    empty claim set, so the only thing that can silence it is the row.
    """
    name = write_png(Path(store.image_root), "finder.png", api=api_graph(TXT2IMG))
    picture_id = add_picture(store, name)

    assert new_finder(store).find_task() is not None
    run_extraction(store, [picture_id])
    assert new_finder(store).find_task() is None


def test_an_unwritable_hub_stands_the_workflow_scan_down(store):
    """Bounded, rather than re-reading the whole library on every cycle.

    Left unmarked and still matched, these pictures would be re-opened,
    re-decoded and re-parsed on every planning sweep for as long as the hub
    stayed broken. The finder narrows back to its pre-B3 predicate instead,
    which the ComfyUI half has already satisfied, so the sweep drains and goes
    quiet exactly as it did before this task learned to hash.
    """
    name = write_png(Path(store.image_root), "standdown.png", api=api_graph(TXT2IMG))
    picture_id = add_picture(store, name)
    finder = new_finder(store)
    assert finder.find_task() is not None

    task = ComfyUIExtractionTask(
        database=store.vault,
        image_root=store.image_root,
        pictures=[SimpleNamespace(id=picture_id)],
        hub=unwritable_hub(),
        on_hub_failure=finder.stand_down,
    )
    task._run_task()

    assert read_picture(store, picture_id).workflow_hash_version is None
    # Same finder AND a fresh one: the claim set is not what is doing this.
    finder.on_task_complete(task, None)
    assert finder.find_task() is None


def test_a_revisit_never_rewrites_what_the_first_extraction_stored(store):
    """The upgrade path's data-loss case, and it is not hypothetical.

    Widening the predicate to `workflow_hash_version IS NULL` re-queues every
    picture a pre-B3 run already extracted. Re-reading the file to rewrite its
    ComfyUI columns is not a no-op: a file that has since moved, or been
    stripped of its metadata, would replace real stored models and LoRAs with
    the "[]" sentinel. The extraction happened once; the revisit adds keys and
    touches nothing else.
    """

    def insert(session):
        picture = Picture(
            file_path="moved-since/never-existed.png",
            comfyui_models=json.dumps(["flux1-dev.safetensors"]),
            comfyui_loras=json.dumps(["example-subject.safetensors"]),
            comfyui_positive_prompt="what somebody typed",
        )
        session.add(picture)
        session.commit()
        return picture.id

    picture_id = store.vault.run_task(insert)
    run_extraction(store, [picture_id])

    picture = read_picture(store, picture_id)
    assert json.loads(picture.comfyui_models) == ["flux1-dev.safetensors"]
    assert json.loads(picture.comfyui_loras) == ["example-subject.safetensors"]
    assert picture.comfyui_positive_prompt == "what somebody typed"
    # And it still came away scanned, so it is not handed back again.
    assert picture.workflow_hash_version == HASH_VERSION


def test_a_library_extracted_before_b3_is_re_queued_for_its_workflow(store):
    """The upgrade case: `comfyui_models` is already written, the keys are not."""
    name = write_png(Path(store.image_root), "legacy.png", api=api_graph(TXT2IMG))
    picture_id = add_picture(store, name)
    run_extraction(store, [picture_id], hub=False)
    assert read_picture(store, picture_id).comfyui_models is not None

    assert new_finder(store).find_task() is not None


def test_hub_rows_outlive_the_pictures_they_came_from(store):
    """The point of the whole feature, and the reason B3 exists at all.

    Without this, dehydrating a stack would delete the graph its own rehydrate
    promise depends on. The rows are content-addressed with no foreign key into
    the vault, so a hard delete of every picture must leave all three tables
    intact.
    """
    name = write_png(Path(store.image_root), "outlives.png", api=api_graph(TXT2IMG))
    picture_id = add_picture(store, name)
    run_extraction(store, [picture_id])
    structural = read_picture(store, picture_id).workflow_structural_hash

    def soft_then_hard_delete(session):
        picture = session.get(Picture, picture_id)
        # The Scrapheap first, which is what every user-facing delete does.
        picture.deleted = True
        session.commit()
        # Then the destruction `purge_scrapheap_pictures` ends at: the row goes.
        session.delete(session.get(Picture, picture_id))
        session.commit()

    store.vault.run_task(soft_then_hard_delete)

    assert read_picture(store, picture_id) is None
    for table in WORKFLOW_TABLES:
        assert store.hub.fetchone(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 1
    assert get_document(store.hub, structural) is not None


def test_two_pictures_share_an_instance_exactly_when_they_share_the_value(store):
    """What "Covered only" tests, asserted end to end through ingest.

    Same graph, different seed: one instance. Same graph, different prompt: two.
    """
    same = write_png(Path(store.image_root), "roll-a.png", api=api_graph(TXT2IMG))
    reroll = write_png(
        Path(store.image_root), "roll-b.png", api=api_graph(edited(TXT2IMG, 5, seed=7))
    )
    other = write_png(
        Path(store.image_root),
        "roll-c.png",
        api=api_graph(edited(TXT2IMG, 2, text="a lighthouse at dawn")),
    )
    ids = [add_picture(store, n) for n in (same, reroll, other)]
    run_extraction(store, ids)

    a, b, c = (read_picture(store, pid) for pid in ids)
    assert a.workflow_instance_hash == b.workflow_instance_hash
    assert a.workflow_instance_hash != c.workflow_instance_hash
    # All three are still one recipe, which is the tier boundary.
    assert (
        a.workflow_structural_hash
        == b.workflow_structural_hash
        == c.workflow_structural_hash
    )


def test_counts_exclude_soft_deleted_pictures(store):
    """A workflow whose every picture is in the Scrapheap reads as none kept."""
    trimmed = api_graph(TXT2IMG)
    del trimmed["7"]
    kept = write_png(Path(store.image_root), "kept.png", api=api_graph(TXT2IMG))
    binned = write_png(Path(store.image_root), "binned.png", api=api_graph(TXT2IMG))
    other = write_png(Path(store.image_root), "other.png", api=trimmed)
    ids = [add_picture(store, name) for name in (kept, binned, other)]
    run_extraction(store, ids)

    def soft_delete(session):
        session.get(Picture, ids[1]).deleted = True
        session.commit()

    store.vault.run_task(soft_delete)

    counts = store.vault.run_immediate_read_task(topology_picture_counts)
    kept_topology = topology_hash(api_graph(TXT2IMG))
    other_topology = topology_hash(trimmed)
    assert kept_topology != other_topology
    assert counts[kept_topology] == 1
    assert counts[other_topology] == 1

    def soft_delete_the_rest(session):
        session.get(Picture, ids[0]).deleted = True
        session.commit()

    store.vault.run_task(soft_delete_the_rest)

    # Absent entirely, not present with a zero.
    assert topology_hash(api_graph(TXT2IMG)) not in store.vault.run_immediate_read_task(
        topology_picture_counts
    )


# ---------------------------------------------------------------------------
# B4 — purge reaches the hub, and the covered-ghost cascade
# ---------------------------------------------------------------------------
#
# The same feature has to survive one deletion and not another, so both
# directions are asserted here rather than assumed:
#
# * a recipe and its topology outlive EVERY picture deletion, purge included —
#   that is B3's whole point and the thing dehydration's promise rests on;
# * a ghost is thumbnail-and-prompt of a destroyed picture, and a purge must
#   reach it, because leaving it behind is a privacy defect rather than a
#   missing feature.


def scrapheap(store, picture_id, *, pixel_sha, thumbnail=b"thumbnail-bytes"):
    """Put a picture in the Scrapheap with the sha the ghost will be keyed on.

    A thumbnail is written by default because a ghost is thumbnail-AND-prompt
    and one without a thumbnail is refused: a helper that quietly omitted it
    would make every retention assertion below pass for the wrong reason.
    ``thumbnail=None`` is the case that exercises the refusal.
    """
    if thumbnail is not None:
        write_thumbnail(store, read_picture(store, picture_id).file_path, thumbnail)

    def mark(session):
        picture = session.get(Picture, picture_id)
        picture.deleted = True
        picture.deleted_at = datetime(2026, 8, 23, tzinfo=timezone.utc)
        picture.pixel_sha = pixel_sha
        session.commit()

    store.vault.run_task(mark)


LIBRARY = "11111111-2222-4333-8444-555555555555"
OTHER_LIBRARY = "99999999-8888-4777-8666-555555555555"


def purge(store, picture_ids, *, retention, library_uuid=LIBRARY):
    """Run THE destruction path against the shared store.

    A ``SimpleNamespace`` rather than a real ``Vault`` for the same reason the
    rest of this section uses a bare ``VaultDatabase``: standing a Vault up
    loads an inference engine and a work planner, and the purge only ever
    reaches these five members.
    """
    vault = SimpleNamespace(
        db=store.vault,
        image_root=store.image_root,
        hub=store.hub,
        library_uuid=library_uuid,
        ghost_retention=retention,
        reference_folder_roots=lambda: (),
    )
    return purge_scrapheap_pictures(vault, picture_ids, include_protected=False)


def ghosts(store):
    return {
        row["pixel_sha"]: row
        for row in store.hub.fetchall(
            "SELECT * FROM workflow_picture_ghost ORDER BY pixel_sha"
        )
    }


def write_thumbnail(store, file_name, payload):
    thumb_path = ImageUtils.get_thumbnail_path(store.image_root, file_name)
    with open(thumb_path, "wb") as handle:
        handle.write(payload)
    return thumb_path


def test_the_default_position_is_covered_only():
    """Settled with the owner 2026-08-23, and the whole cascade hangs off it."""
    assert read_ghost_retention({}) == GHOST_RETENTION_COVERED
    assert DEFAULT_GHOST_RETENTION == GHOST_RETENTION_COVERED


def test_an_unreadable_position_keeps_no_ghosts():
    """The fail-safe direction, which is the OPPOSITE of the retention window's.

    There an unparseable config must not license a deletion. Here it must not
    license keeping a thumbnail and a prompt for a picture the user destroyed.
    Both fall to the position holding less of the user's data.
    """
    assert read_ghost_retention({GHOST_RETENTION_KEY: "sometimes"}) == (
        GHOST_RETENTION_OFF
    )
    assert read_ghost_retention({GHOST_RETENTION_KEY: None}) == GHOST_RETENTION_OFF
    # A valid position is honoured whatever case it was saved in.
    assert read_ghost_retention({GHOST_RETENTION_KEY: " ON "}) == GHOST_RETENTION_ON


def test_a_purge_destroys_the_picture_and_leaves_the_recipe_standing(store):
    """Direction one: the rows that must SURVIVE a purge.

    Ordinary deletion is already covered by
    ``test_hub_rows_outlive_the_pictures_they_came_from``; this is the harder
    case, because the purge is the one path that now reaches into the hub at
    all. A step that destroyed the recipe would take dehydration's rehydrate
    promise with it.
    """
    name = write_png(
        Path(store.image_root), "purged-recipe.png", api=api_graph(TXT2IMG)
    )
    picture_id = add_picture(store, name)
    run_extraction(store, [picture_id])
    structural = read_picture(store, picture_id).workflow_structural_hash
    scrapheap(store, picture_id, pixel_sha="sha-purged-recipe")

    outcome = purge(store, [picture_id], retention=GHOST_RETENTION_ON)

    assert outcome.deleted_count == 1
    assert read_picture(store, picture_id) is None
    assert not os.path.exists(os.path.join(store.image_root, name))
    for table in WORKFLOW_TABLES:
        assert store.hub.fetchone(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 1
    assert get_document(store.hub, structural) is not None
    assert assets_for_recipe(store.hub, structural)


def test_a_purge_with_ghosts_off_leaves_nothing_of_the_picture(store):
    """Direction two, at the strictest position: no trace at all."""
    name = write_png(Path(store.image_root), "no-ghost.png", api=api_graph(TXT2IMG))
    picture_id = add_picture(store, name)
    run_extraction(store, [picture_id])
    write_thumbnail(store, name, b"thumbnail-bytes")
    scrapheap(store, picture_id, pixel_sha="sha-no-ghost")

    outcome = purge(store, [picture_id], retention=GHOST_RETENTION_OFF)

    assert outcome.deleted_count == 1
    assert outcome.ghosts_kept == 0
    assert ghosts(store) == {}


def test_a_covered_picture_leaves_a_ghost_and_an_uncovered_one_does_not(store):
    """The default position, and both of its branches in one environment.

    ``covered`` is not "keep a ghost when the workflow survives" — it is "keep
    one when a SURVIVING PICTURE carries the same instance hash". The lone
    picture below shares the recipe with the other two and still gets no ghost,
    which is the distinction the structural hash cannot make.
    """
    covered = write_png(Path(store.image_root), "cov-a.png", api=api_graph(TXT2IMG))
    cover = write_png(
        Path(store.image_root), "cov-b.png", api=api_graph(edited(TXT2IMG, 5, seed=99))
    )
    lone = write_png(
        Path(store.image_root),
        "cov-c.png",
        api=api_graph(edited(TXT2IMG, 2, text="a different prompt entirely")),
    )
    ids = [add_picture(store, name) for name in (covered, cover, lone)]
    run_extraction(store, ids)
    same_instance = read_picture(store, ids[0]).workflow_instance_hash
    assert same_instance == read_picture(store, ids[1]).workflow_instance_hash
    assert same_instance != read_picture(store, ids[2]).workflow_instance_hash

    scrapheap(store, ids[0], pixel_sha="sha-covered")
    scrapheap(store, ids[2], pixel_sha="sha-lone")
    outcome = purge(store, [ids[0], ids[2]], retention=GHOST_RETENTION_COVERED)

    assert outcome.deleted_count == 2
    assert outcome.ghosts_kept == 1
    assert set(ghosts(store)) == {"sha-covered"}


def test_the_cascade_destroys_a_ghost_when_its_last_cover_is_purged(store):
    """The hole the safe class would otherwise leave, closed on the DEFAULT path.

    A covered ghost is safe *because* a covering picture survives. Destroy the
    cover later and it stops being covered, retroactively, with nothing on
    screen to say so. So the purge that destroys the last picture carrying an
    instance hash must destroy the ghosts leaning on it, in the same pass.
    """
    member = write_png(Path(store.image_root), "casc-a.png", api=api_graph(TXT2IMG))
    cover = write_png(
        Path(store.image_root), "casc-b.png", api=api_graph(edited(TXT2IMG, 5, seed=11))
    )
    member_id, cover_id = (add_picture(store, name) for name in (member, cover))
    run_extraction(store, [member_id, cover_id])

    scrapheap(store, member_id, pixel_sha="sha-member")
    assert purge(store, [member_id], retention=GHOST_RETENTION_COVERED).ghosts_kept == 1
    assert set(ghosts(store)) == {"sha-member"}

    scrapheap(store, cover_id, pixel_sha="sha-cover")
    outcome = purge(store, [cover_id], retention=GHOST_RETENTION_COVERED)

    # The cover was itself uncovered by then, so it leaves no ghost of its own
    # AND takes its dependant with it.
    assert outcome.ghosts_kept == 0
    assert outcome.ghosts_cascaded == 1
    assert ghosts(store) == {}


def test_on_keeps_every_ghost_and_runs_no_cascade(store):
    """The opposite setting, on the same shape, so the cascade is not universal.

    Asserted because a cascade that ran regardless of the position would make
    ``on`` mean ``covered`` and nobody would notice until a rehydrate failed.
    """
    member = write_png(Path(store.image_root), "keep-a.png", api=api_graph(TXT2IMG))
    cover = write_png(
        Path(store.image_root), "keep-b.png", api=api_graph(edited(TXT2IMG, 5, seed=13))
    )
    member_id, cover_id = (add_picture(store, name) for name in (member, cover))
    run_extraction(store, [member_id, cover_id])

    scrapheap(store, member_id, pixel_sha="sha-keep-member")
    purge(store, [member_id], retention=GHOST_RETENTION_ON)
    scrapheap(store, cover_id, pixel_sha="sha-keep-cover")
    outcome = purge(store, [cover_id], retention=GHOST_RETENTION_ON)

    assert outcome.ghosts_cascaded == 0
    assert set(ghosts(store)) == {"sha-keep-member", "sha-keep-cover"}


def test_a_soft_deleted_picture_is_not_cover(store):
    """A sibling still in the Scrapheap must not keep a ghost alive.

    It exists and can be restored, but it is sitting in the destruction queue,
    so reading it as cover would retain a prompt on the strength of a picture
    that is itself on its way out. Same rule as the workflow counts.
    """
    going = write_png(Path(store.image_root), "soft-a.png", api=api_graph(TXT2IMG))
    also_binned = write_png(
        Path(store.image_root), "soft-b.png", api=api_graph(edited(TXT2IMG, 5, seed=17))
    )
    going_id, binned_id = (add_picture(store, name) for name in (going, also_binned))
    run_extraction(store, [going_id, binned_id])
    assert (
        read_picture(store, going_id).workflow_instance_hash
        == read_picture(store, binned_id).workflow_instance_hash
    )

    # The sibling is soft-deleted but NOT purged: still a row, still restorable.
    scrapheap(store, binned_id, pixel_sha="sha-soft-sibling")
    scrapheap(store, going_id, pixel_sha="sha-soft-going")
    outcome = purge(store, [going_id], retention=GHOST_RETENTION_COVERED)

    assert read_picture(store, binned_id) is not None
    assert outcome.ghosts_kept == 0
    assert ghosts(store) == {}


def test_a_ghost_carries_the_thumbnail_and_the_prompt_together(store):
    """One word, two things, and it always means both (library plan §5).

    Written down as an assertion because the optimisation this guards against
    is a plausible one: keeping "just the prompt" because it is only text.
    """
    kept = write_png(Path(store.image_root), "both-a.png", api=api_graph(TXT2IMG))
    cover = write_png(
        Path(store.image_root), "both-b.png", api=api_graph(edited(TXT2IMG, 5, seed=23))
    )
    kept_id, cover_id = (add_picture(store, name) for name in (kept, cover))
    run_extraction(store, [kept_id, cover_id])
    scrapheap(store, kept_id, pixel_sha="sha-both", thumbnail=b"a real thumbnail")

    purge(store, [kept_id], retention=GHOST_RETENTION_COVERED)

    ghost = ghosts(store)["sha-both"]
    assert ghost["thumbnail"] == b"a real thumbnail"
    assert ghost["positive_prompt"] == "a lighthouse at dusk"
    # The seed is the ONLY thing distinguishing this ghost from its cover, and
    # it is not a picture column — it is re-read from the file the purge is
    # about to delete.
    assert ghost["seed"] == 42
    assert ghost["structural_hash"] == structural_hash(api_graph(TXT2IMG))
    assert ghost["instance_hash"] == instance_hash(api_graph(TXT2IMG))


def test_a_picture_with_no_workflow_leaves_no_ghost(store):
    """Nothing to key the cascade on and nothing that could be made again."""
    plain = write_png(Path(store.image_root), "plain.png")
    picture_id = add_picture(store, plain)
    run_extraction(store, [picture_id])
    assert read_picture(store, picture_id).workflow_instance_hash is None
    scrapheap(store, picture_id, pixel_sha="sha-plain")

    outcome = purge(store, [picture_id], retention=GHOST_RETENTION_ON)

    assert outcome.deleted_count == 1
    assert ghosts(store) == {}


def test_the_purge_leaves_no_derived_copy_of_the_prompt(store):
    """ "Forgetting reaches every derived copy" — an acceptance criterion.

    A prompt lives in the column AND, in vector form, in ``text_embedding``.
    Both are columns of the row the purge deletes, so this holds structurally;
    it is asserted rather than argued because a future "keep the embedding, it
    is only numbers" would pass every other test in this file.
    """
    name = write_png(Path(store.image_root), "derived.png", api=api_graph(TXT2IMG))
    picture_id = add_picture(store, name)
    run_extraction(store, [picture_id])

    def give_it_an_embedding(session):
        session.get(Picture, picture_id).text_embedding = b"pretend-vector"
        session.commit()

    store.vault.run_task(give_it_an_embedding)
    scrapheap(store, picture_id, pixel_sha="sha-derived")
    purge(store, [picture_id], retention=GHOST_RETENTION_OFF)

    assert read_picture(store, picture_id) is None
    remaining = store.vault.run_immediate_read_task(
        lambda session: session.exec(
            select(Picture).where(Picture.text_embedding.is_not(None))
        ).all()
    )
    assert remaining == []


def test_a_vault_with_no_hub_purges_without_reaching_for_one(store):
    """The CLI tools and most tests open a vault with no hub attached."""
    name = write_png(Path(store.image_root), "hubless.png", api=api_graph(TXT2IMG))
    picture_id = add_picture(store, name)
    run_extraction(store, [picture_id])
    scrapheap(store, picture_id, pixel_sha="sha-hubless")

    hubless = SimpleNamespace(
        db=store.vault,
        image_root=store.image_root,
        hub=None,
        library_uuid=None,
        ghost_retention=GHOST_RETENTION_ON,
        reference_folder_roots=lambda: (),
    )
    outcome = purge_scrapheap_pictures(hubless, [picture_id], include_protected=False)

    assert outcome.deleted_count == 1
    assert outcome.ghosts_kept == 0
    assert read_picture(store, picture_id) is None


def test_the_missing_file_purge_cascades_too(store):
    """The OTHER path that removes a picture row for good (§B4).

    ``MissingFilePurgeTask`` never writes a ghost — the file it purges is
    already gone from disk, so there is no thumbnail to keep — but it can
    destroy the last picture covering an instance hash. Leaving it out of the
    cascade would let the safe class decay into the unsafe one exactly where
    nobody is looking, which is the failure library plan §5 names.
    """
    member = write_png(Path(store.image_root), "mfp-a.png", api=api_graph(TXT2IMG))
    cover = write_png(
        Path(store.image_root), "mfp-b.png", api=api_graph(edited(TXT2IMG, 5, seed=31))
    )
    member_id, cover_id = (add_picture(store, name) for name in (member, cover))
    run_extraction(store, [member_id, cover_id])

    scrapheap(store, member_id, pixel_sha="sha-mfp-member")
    assert purge(store, [member_id], retention=GHOST_RETENTION_COVERED).ghosts_kept == 1

    # The cover's file vanishes from under the library — a sync agent, a moved
    # folder — which is what this task exists to reconcile.
    os.remove(os.path.join(store.image_root, cover))
    task = MissingFilePurgeTask(
        database=store.vault,
        pictures=[read_picture(store, cover_id)],
        hub=store.hub,
        library_uuid=LIBRARY,
        ghost_retention=GHOST_RETENTION_COVERED,
    )
    result = task._run_task()

    assert result["purged"] == 1
    assert result["ghosts_cascaded"] == 1
    assert ghosts(store) == {}


def test_the_missing_file_purge_leaves_a_still_covered_ghost_alone(store):
    """The positive control: over-destroying is its own regression.

    A cascade that fired on every purged picture rather than on the ones that
    were actually covering something would pass the test above and silently
    break the feature the setting exists to enable.
    """
    member = write_png(Path(store.image_root), "mfp-keep-a.png", api=api_graph(TXT2IMG))
    cover = write_png(
        Path(store.image_root),
        "mfp-keep-b.png",
        api=api_graph(edited(TXT2IMG, 5, seed=37)),
    )
    unrelated = write_png(
        Path(store.image_root),
        "mfp-keep-c.png",
        api=api_graph(edited(TXT2IMG, 2, text="something else entirely")),
    )
    member_id, cover_id, other_id = (
        add_picture(store, name) for name in (member, cover, unrelated)
    )
    run_extraction(store, [member_id, cover_id, other_id])

    scrapheap(store, member_id, pixel_sha="sha-mfp-keep")
    purge(store, [member_id], retention=GHOST_RETENTION_COVERED)
    assert set(ghosts(store)) == {"sha-mfp-keep"}

    # A DIFFERENT picture's file vanishes. The cover is untouched, so the ghost
    # is still covered and must survive.
    os.remove(os.path.join(store.image_root, unrelated))
    result = MissingFilePurgeTask(
        database=store.vault,
        pictures=[read_picture(store, other_id)],
        hub=store.hub,
        library_uuid=LIBRARY,
        ghost_retention=GHOST_RETENTION_COVERED,
    )._run_task()

    assert result["purged"] == 1
    assert result["ghosts_cascaded"] == 0
    assert set(ghosts(store)) == {"sha-mfp-keep"}


def test_a_picture_with_no_thumbnail_leaves_no_ghost_at_all(store):
    """A ghost is thumbnail AND prompt, so half a ghost is no ghost.

    Thumbnails are generated lazily by ``MissingThumbnailFinder``, so a picture
    destroyed before its thumbnail existed genuinely reaches this branch. Keeping
    the prompt alone would retain the more sensitive half on its own AND destroy
    the argument that makes ``covered`` a safe default — that the only marginal
    exposure is a thumbnail of a near-duplicate.
    """
    bare = write_png(Path(store.image_root), "nothumb-a.png", api=api_graph(TXT2IMG))
    cover = write_png(
        Path(store.image_root),
        "nothumb-b.png",
        api=api_graph(edited(TXT2IMG, 5, seed=41)),
    )
    bare_id, cover_id = (add_picture(store, name) for name in (bare, cover))
    run_extraction(store, [bare_id, cover_id])
    # Covered by its sibling, and would otherwise be retained.
    assert (
        read_picture(store, bare_id).workflow_instance_hash
        == read_picture(store, cover_id).workflow_instance_hash
    )
    scrapheap(store, bare_id, pixel_sha="sha-nothumb", thumbnail=None)

    outcome = purge(store, [bare_id], retention=GHOST_RETENTION_ON)

    assert outcome.deleted_count == 1
    assert outcome.ghosts_kept == 0
    assert ghosts(store) == {}


def test_a_picture_restored_mid_purge_leaves_no_ghost(store):
    """The ``destroyed_ids`` guard, exercised where the purge cannot reach it.

    ``purge_rows_in_session`` re-checks ``deleted`` at the point of deletion and
    reports what it spared, and a spared picture still EXISTS — so writing its
    thumbnail and prompt into the hub would retain a trace of a picture the user
    just rescued. Driven through ``apply_purge_to_hub`` directly because the
    interleaving it guards against cannot be produced from the endpoint.
    """
    kept = write_png(Path(store.image_root), "restored-a.png", api=api_graph(TXT2IMG))
    cover = write_png(
        Path(store.image_root),
        "restored-b.png",
        api=api_graph(edited(TXT2IMG, 5, seed=43)),
    )
    kept_id, cover_id = (add_picture(store, name) for name in (kept, cover))
    run_extraction(store, [kept_id, cover_id])
    scrapheap(store, kept_id, pixel_sha="sha-restored", thumbnail=b"still here")
    instance = read_picture(store, kept_id).workflow_instance_hash

    candidates = store.vault.run_immediate_read_task(
        collect_ghost_candidates_in_session, [kept_id]
    )
    assert len(candidates) == 1 and candidates[0].pixel_sha == "sha-restored"

    # The purge planned it, then the guarded DELETE spared it: destroyed_ids is
    # empty even though the candidate was gathered.
    written, cascaded = apply_purge_to_hub(
        store.hub,
        LIBRARY,
        store.image_root,
        GHOST_RETENTION_ON,
        GhostPurgeContext(candidates=candidates, surviving_instance_hashes={instance}),
        set(),
    )

    assert (written, cascaded) == (0, 0)
    assert ghosts(store) == {}
    # Positive control: the SAME call with the id actually destroyed does write
    # one, so the negative above is the guard and not a broken fixture.
    written, _ = apply_purge_to_hub(
        store.hub,
        LIBRARY,
        store.image_root,
        GHOST_RETENTION_ON,
        GhostPurgeContext(candidates=candidates, surviving_instance_hashes={instance}),
        {kept_id},
    )
    assert written == 1


def test_the_missing_file_purge_never_cascades_at_on(store):
    """``on`` keeps every ghost, on BOTH destruction paths.

    Covered separately from the scrapheap purge because the cascade helper is a
    different function with its own retention gate; a regression that dropped
    that gate would make ``on`` mean ``covered`` and only show up as a failed
    rehydrate months later.
    """
    member = write_png(Path(store.image_root), "on-mfp-a.png", api=api_graph(TXT2IMG))
    cover = write_png(
        Path(store.image_root),
        "on-mfp-b.png",
        api=api_graph(edited(TXT2IMG, 5, seed=47)),
    )
    member_id, cover_id = (add_picture(store, name) for name in (member, cover))
    run_extraction(store, [member_id, cover_id])

    scrapheap(store, member_id, pixel_sha="sha-on-mfp")
    purge(store, [member_id], retention=GHOST_RETENTION_ON)
    assert set(ghosts(store)) == {"sha-on-mfp"}

    os.remove(os.path.join(store.image_root, cover))
    result = MissingFilePurgeTask(
        database=store.vault,
        pictures=[read_picture(store, cover_id)],
        hub=store.hub,
        library_uuid=LIBRARY,
        ghost_retention=GHOST_RETENTION_ON,
    )._run_task()

    assert result["purged"] == 1
    assert result["ghosts_cascaded"] == 0
    assert set(ghosts(store)) == {"sha-on-mfp"}


def test_the_missing_file_purge_clears_ghosts_at_off(store):
    """``off`` means no ghosts, and it has to mean that on both paths.

    Without this the two hard-delete paths disagree: a user who has said "keep
    nothing" gets tidied up by one and not the other.
    """
    member = write_png(Path(store.image_root), "off-mfp-a.png", api=api_graph(TXT2IMG))
    cover = write_png(
        Path(store.image_root),
        "off-mfp-b.png",
        api=api_graph(edited(TXT2IMG, 5, seed=53)),
    )
    member_id, cover_id = (add_picture(store, name) for name in (member, cover))
    run_extraction(store, [member_id, cover_id])

    # Written while the setting was ON, then the user turns it off.
    scrapheap(store, member_id, pixel_sha="sha-off-mfp")
    purge(store, [member_id], retention=GHOST_RETENTION_ON)
    assert set(ghosts(store)) == {"sha-off-mfp"}

    os.remove(os.path.join(store.image_root, cover))
    result = MissingFilePurgeTask(
        database=store.vault,
        pictures=[read_picture(store, cover_id)],
        hub=store.hub,
        library_uuid=LIBRARY,
        ghost_retention=GHOST_RETENTION_OFF,
    )._run_task()

    assert result["ghosts_cascaded"] == 1
    assert ghosts(store) == {}


def test_a_purge_never_cascades_a_ghost_belonging_to_another_library(store):
    """The hub is shared; the cover that justifies a ghost is not.

    Only one vault is live at a time, so a purge cannot see whether ANOTHER
    library still holds a covering picture. A hub-global cascade would therefore
    destroy a ghost that library still has cover for — silent loss, in the
    opposite direction from the one this feature is careful about. Every hub
    query is scoped to one library so that cannot happen.
    """
    mine = write_png(Path(store.image_root), "xlib-a.png", api=api_graph(TXT2IMG))
    cover = write_png(
        Path(store.image_root), "xlib-b.png", api=api_graph(edited(TXT2IMG, 5, seed=59))
    )
    mine_id, cover_id = (add_picture(store, name) for name in (mine, cover))
    run_extraction(store, [mine_id, cover_id])
    instance = read_picture(store, mine_id).workflow_instance_hash

    # Another library holds a ghost on the same instance hash — the same
    # workflow used in two of the owner's libraries, which the measurement says
    # is the common case.
    record_picture_ghosts(
        store.hub,
        [
            PictureGhost(
                library_uuid=OTHER_LIBRARY,
                pixel_sha="sha-other-library",
                instance_hash=instance,
                thumbnail=b"other library thumbnail",
                positive_prompt="a lighthouse at dusk",
            )
        ],
    )

    # This library destroys both pictures carrying the hash, so its own ghost is
    # uncovered and cascades.
    scrapheap(store, mine_id, pixel_sha="sha-mine")
    purge(store, [mine_id], retention=GHOST_RETENTION_COVERED)
    scrapheap(store, cover_id, pixel_sha="sha-mine-cover")
    outcome = purge(store, [cover_id], retention=GHOST_RETENTION_COVERED)

    assert outcome.ghosts_cascaded == 1
    assert set(ghosts(store)) == {"sha-other-library"}


def test_the_write_rechecks_cover_at_the_last_moment(store):
    """The vault and the hub are separate databases, so coverage can go stale.

    ``apply_purge_to_hub`` runs off the DB worker thread — it reads thumbnails
    and re-parses embedded graphs — so an unknown amount of wall-clock separates
    the coverage read inside the purge's submission from the hub write. If the
    cover disappears in that window the ghost must not be written. Simulated by
    a recheck that reports the hash uncovered.
    """
    member = write_png(Path(store.image_root), "stale-a.png", api=api_graph(TXT2IMG))
    cover = write_png(
        Path(store.image_root),
        "stale-b.png",
        api=api_graph(edited(TXT2IMG, 5, seed=61)),
    )
    member_id, cover_id = (add_picture(store, name) for name in (member, cover))
    run_extraction(store, [member_id, cover_id])
    scrapheap(store, member_id, pixel_sha="sha-stale", thumbnail=b"about to be stale")
    instance = read_picture(store, member_id).workflow_instance_hash

    # An earlier purge already left a ghost on this instance, so the cascade has
    # something to destroy and its count means what it says.
    record_picture_ghosts(
        store.hub,
        [
            PictureGhost(
                library_uuid=LIBRARY,
                pixel_sha="sha-stale-earlier",
                instance_hash=instance,
                thumbnail=b"earlier ghost",
            )
        ],
    )

    candidates = store.vault.run_immediate_read_task(
        collect_ghost_candidates_in_session, [member_id]
    )
    written, cascaded = apply_purge_to_hub(
        store.hub,
        LIBRARY,
        store.image_root,
        GHOST_RETENTION_COVERED,
        # The early read said covered...
        GhostPurgeContext(candidates=candidates, surviving_instance_hashes={instance}),
        {member_id},
        # ...and by write time the cover was gone.
        recheck_surviving=lambda hashes: set(),
    )

    assert written == 0
    assert cascaded == 1
    assert ghosts(store) == {}


def test_the_ghost_reads_survive_the_bound_parameter_ceiling(store):
    """Emptying a whole scrapheap must not abort inside the purge (§B4).

    Both new reads run INSIDE ``plan_and_purge_in_session`` — the candidate read
    before the DELETE, the coverage read after it — so an ``OperationalError``
    from either takes the whole purge down and destroys nothing. The list they
    are handed is the entire purge plan, which for "delete forever, everything"
    is the entire scrapheap.

    Pinned to SQLite's historical 999-variable floor with ``setlimit``, the same
    build-independent proof ``tests/test_scope_table.py`` uses, at a size well
    under any modern default so the failure can only come from the ceiling.
    """
    size = 1500
    ids = list(range(1, size + 1))
    hashes = [f"instance-{n}" for n in range(size)]

    def probe(session):
        session.connection().connection.setlimit(
            sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999
        )
        # A plain ``.in_()`` at this size is what the ceiling refuses, so the
        # two calls below are not passing for want of a big enough list.
        with pytest.raises(OperationalError, match="too many SQL variables"):
            session.exec(select(Picture.id).where(Picture.id.in_(ids))).all()
        return (
            collect_ghost_candidates_in_session(session, ids),
            surviving_instance_hashes_in_session(session, hashes),
        )

    candidates, surviving = store.vault.run_immediate_read_task(probe)

    assert candidates == []
    assert surviving == set()


def test_a_workflow_with_no_prompt_still_leaves_a_ghost(store):
    """The prompt half is optional; the thumbnail half is not (library plan §5).

    §5's rule is that a ghost never keeps the prompt ALONE — the prompt is the
    sensitive half and "it is only text" is the argument that would keep it once
    the thumbnail had gone. It is NOT a rule that a prompt must exist. An upscale
    graph has no ``CLIPTextEncode`` at all, and it still hashes, still has a
    recipe and a seed, and is exactly as worth rehydrating as any other. Refusing
    it would delete the feature for a whole class of real workflows.
    """
    ids = [
        add_picture(
            store,
            write_png(Path(store.image_root), name, api=api_graph(UPSCALE)),
        )
        for name in ("noprompt-a.png", "noprompt-b.png")
    ]
    run_extraction(store, ids)
    kept_id, cover_id = ids
    kept = read_picture(store, kept_id)
    # The premise: hashed and coverable, but genuinely promptless.
    assert kept.workflow_instance_hash
    assert (
        kept.workflow_instance_hash
        == read_picture(store, cover_id).workflow_instance_hash
    )
    assert kept.comfyui_positive_prompt is None

    scrapheap(store, kept_id, pixel_sha="sha-noprompt", thumbnail=b"upscaled thumb")
    outcome = purge(store, [kept_id], retention=GHOST_RETENTION_COVERED)

    assert outcome.ghosts_kept == 1
    ghost = ghosts(store)["sha-noprompt"]
    assert ghost["thumbnail"] == b"upscaled thumb"
    assert ghost["positive_prompt"] is None
    assert ghost["structural_hash"] == structural_hash(api_graph(UPSCALE))


def test_a_prompt_the_picture_had_is_never_dropped_on_the_way_in(store):
    """NULL in the ghost means "no prompt", never "we lost it".

    The pair with the test above: together they say the nullable column is a
    faithful copy and not a place a half-ghost can appear. Without this one,
    ``positive_prompt IS NULL`` would be indistinguishable from a write that
    silently dropped the sensitive half.
    """
    kept = write_png(Path(store.image_root), "keptprompt-a.png", api=api_graph(TXT2IMG))
    cover = write_png(
        Path(store.image_root),
        "keptprompt-b.png",
        api=api_graph(edited(TXT2IMG, 5, seed=67)),
    )
    kept_id, cover_id = (add_picture(store, name) for name in (kept, cover))
    run_extraction(store, [kept_id, cover_id])
    stored = read_picture(store, kept_id).comfyui_positive_prompt
    assert stored == "a lighthouse at dusk"

    scrapheap(store, kept_id, pixel_sha="sha-keptprompt")
    purge(store, [kept_id], retention=GHOST_RETENTION_COVERED)

    assert ghosts(store)["sha-keptprompt"]["positive_prompt"] == stored


def test_the_store_itself_refuses_a_ghost_with_no_thumbnail(store):
    """The invariant at the storage layer, not only in the writer.

    ``_prepare_ghosts`` already refuses to build one, so this asserts the second
    line of defence: a FUTURE caller that bypassed the writer still cannot
    create a retained prompt with no thumbnail beside it.
    """
    with pytest.raises(sqlite3.IntegrityError):
        with store.hub.transaction() as conn:
            conn.execute(
                "INSERT INTO workflow_picture_ghost (library_uuid, pixel_sha, "
                "instance_hash, positive_prompt, thumbnail, created_at) "
                "VALUES (?, ?, ?, ?, NULL, 'now')",
                (LIBRARY, "sha-half-ghost", "some-instance", "a secret prompt"),
            )
    assert ghosts(store) == {}
