"""Workflow cards and automatic stacks derived from stored graphs (v1.12 B1).

Every stacking case in the user plan §2 is a pair: two workflows, and whether
they share a card (``workflow_key``) and a stack (``core_hash``).
"""

import hashlib
import json
from pathlib import Path

import pytest

from pixlstash.hub.workflow_group_convert import _core_strip_v1, _core_strip_v2
from pixlstash.services.workflow_hash import (
    WorkflowGraphError,
    graph_key,
    asset_reference,
    structural_document,
    structural_hash,
    topology_hash,
)
from pixlstash.services.workflow_identity import (
    FACE_DETAILER,
    RECIPE,
    SEED_VARIANCE,
    STRUCTURAL,
    UPSCALE,
    Difference,
    core_hash,
    core_node_labels,
    differences_reduced,
    differs_by,
    guess_mark,
    reduce_stored_document,
    slots,
    special_groups,
    workflow_key,
    workflow_type,
)
from pixlstash.services.workflow_identity import _SINK_CLASS_RE


def _node(class_type: str, **inputs) -> dict:
    return {"class_type": class_type, "inputs": inputs}


def _graph(
    *,
    ckpt: str = "base.safetensors",
    loras: tuple[str, ...] = (),
    preview: bool = False,
    upscale: bool = False,
    face_detailer: bool = False,
    hires: bool = False,
    img2img: bool = False,
    input_picture: str = "in.png",
    extra: dict | None = None,
) -> dict:
    """A txt2img graph with optional plumbing, post-processing and LoRAs."""
    g = {"1": _node("CheckpointLoaderSimple", ckpt_name=ckpt)}
    model, clip = ["1", 0], ["1", 1]
    for i, lora in enumerate(loras):
        node_id = f"L{i}"
        g[node_id] = _node(
            "LoraLoader",
            lora_name=lora,
            strength_model=1.0,
            strength_clip=1.0,
            model=model,
            clip=clip,
        )
        model, clip = [node_id, 0], [node_id, 1]
    g["2"] = _node("CLIPTextEncode", text="a cat", clip=clip)
    g["3"] = _node("CLIPTextEncode", text="blurry", clip=clip)
    if img2img:
        g["10"] = _node("LoadImage", image=input_picture)
        g["11"] = _node("ImageScale", image=["10", 0], width=512, height=512)
        g["12"] = _node("VAEEncode", pixels=["11", 0], vae=["1", 2])
        latent = ["12", 0]
    else:
        g["4"] = _node("EmptyLatentImage", width=512, height=512, batch_size=1)
        latent = ["4", 0]
    g["5"] = _node(
        "KSampler",
        model=model,
        positive=["2", 0],
        negative=["3", 0],
        latent_image=latent,
        seed=1,
        steps=20,
    )
    samples = ["5", 0]
    if hires:
        g["20"] = _node("LatentUpscaleBy", samples=samples, scale_by=1.5)
        g["21"] = _node(
            "KSampler",
            model=model,
            positive=["2", 0],
            negative=["3", 0],
            latent_image=["20", 0],
            seed=1,
            steps=10,
        )
        samples = ["21", 0]
    g["6"] = _node("VAEDecode", samples=samples, vae=["1", 2])
    image = ["6", 0]
    if face_detailer:
        g["30"] = _node("UltralyticsDetectorProvider", model_name="face_yolo.pt")
        g["31"] = _node(
            "FaceDetailer",
            image=image,
            model=model,
            clip=clip,
            vae=["1", 2],
            positive=["2", 0],
            negative=["3", 0],
            bbox_detector=["30", 0],
        )
        image = ["31", 0]
    if upscale:
        g["40"] = _node("UpscaleModelLoader", model_name="4x_ultra.pth")
        g["41"] = _node("ImageUpscaleWithModel", upscale_model=["40", 0], image=image)
        image = ["41", 0]
    if preview:
        g["50"] = _node("PreviewImage", images=["6", 0])
    g["7"] = _node("SaveImage", images=image, filename_prefix="out")
    g.update(extra or {})
    return g


def _doc(graph: dict) -> dict:
    # Round-tripped through JSON, as the hub stores it.
    return json.loads(json.dumps(structural_document(graph)))


def _key(graph: dict) -> str:
    doc = _doc(graph)
    found = slots(doc)
    structural = set()
    for slot in found:
        if slot.is_lora and slot.node_id in _lora_files(graph):
            if guess_mark(_lora_files(graph)[slot.node_id]) == STRUCTURAL:
                structural.add(slot.label)
    return workflow_key(topology_hash(graph), found, structural)


def _lora_files(graph: dict) -> dict:
    return {
        node_id: node["inputs"]["lora_name"]
        for node_id, node in graph.items()
        if "lora_name" in node["inputs"]
    }


def _renumbered(graph: dict) -> dict:
    """The same graph with every node id changed and the keys reordered."""
    ids = {old: f"n{i}" for i, old in enumerate(reversed(list(graph)))}
    out = {}
    for old in reversed(list(graph)):
        node = graph[old]
        inputs = {
            name: [ids[v[0]], v[1]] if isinstance(v, list) and v[0] in ids else v
            for name, v in node["inputs"].items()
        }
        out[ids[old]] = {"class_type": node["class_type"], "inputs": inputs}
    return out


# ── cards: workflow_key ─────────────────────────────────────────────────────


def test_a_different_checkpoint_is_a_different_workflow_in_the_same_stack():
    a, b = _graph(ckpt="base.safetensors"), _graph(ckpt="other.safetensors")
    assert _key(a) != _key(b)
    assert core_hash(_doc(a)) == core_hash(_doc(b))
    assert differs_by(_doc(a), _doc(b)) == ["other checkpoint"]


def _with_companions(vae: str, clip: str) -> dict:
    """A graph whose VAE and text encoder load from their own loaders."""
    return _graph(
        extra={
            "8": _node("VAELoader", vae_name=vae),
            "9": _node("CLIPLoader", clip_name=clip, type="flux2"),
            "2": _node("CLIPTextEncode", text="a cat", clip=["9", 0]),
            "6": _node("VAEDecode", samples=["5", 0], vae=["8", 0]),
        }
    )


@pytest.mark.parametrize(
    "a, b",
    [
        (
            ("ae.safetensors", "t5.safetensors"),
            ("other_vae.safetensors", "t5.safetensors"),
        ),
        (("ae.safetensors", "t5.safetensors"), ("ae.safetensors", "qwen.safetensors")),
    ],
    ids=["vae", "text_encoder"],
)
def test_a_different_companion_file_is_a_different_workflow_in_the_same_stack(a, b):
    # Clone with new models re-keys on the VAE and text encoder too, not only
    # the checkpoint: otherwise the clone would land on the original's card.
    first, second = _with_companions(*a), _with_companions(*b)
    assert _key(first) != _key(second)
    assert core_hash(_doc(first)) == core_hash(_doc(second))


def test_a_character_lora_is_part_of_the_recipe_not_the_workflow():
    a = _graph(loras=("alice_character.safetensors",))
    b = _graph(loras=("bob_character.safetensors",))
    assert _key(a) == _key(b)


def test_a_speed_lora_is_part_of_the_workflow():
    a = _graph(loras=("sdxl_lightning_4step.safetensors",))
    b = _graph(loras=("hyper-sdxl-8steps.safetensors",))
    assert _key(a) != _key(b)


def test_flipping_a_mark_regroups_the_card():
    graph = _graph(loras=("anything.safetensors",))
    doc = _doc(graph)
    found = slots(doc)
    lora_labels = {s.label for s in found if s.is_lora}
    assert lora_labels
    as_recipe = workflow_key(topology_hash(graph), found, set())
    as_structural = workflow_key(topology_hash(graph), found, lora_labels)
    assert as_recipe != as_structural


def test_the_input_picture_is_not_part_of_the_workflow():
    a = _graph(img2img=True, input_picture="first.png")
    b = _graph(img2img=True, input_picture="second.png")
    assert _key(a) == _key(b)


def test_keys_ignore_node_ids_and_key_order():
    graph = _graph(loras=("sdxl_lightning_4step.safetensors",), upscale=True)
    other = _renumbered(graph)
    assert _key(graph) == _key(other)
    assert core_hash(_doc(graph)) == core_hash(_doc(other))
    assert [s.label for s in slots(_doc(graph))] == [
        s.label for s in slots(_doc(other))
    ]


def test_slots_name_models_by_reference_never_by_filename():
    doc = _doc(_graph(loras=("alice_character.safetensors",)))
    found = slots(doc)
    assert {s.widget for s in found} == {"ckpt_name", "lora_name"}
    assert all(s.asset.startswith("asset:") for s in found)
    assert "alice" not in json.dumps([s.__dict__ for s in found])


@pytest.mark.parametrize(
    "filename, mark",
    [
        ("sdxl_lightning_4step_lora.safetensors", STRUCTURAL),
        ("Hyper-SD15-8steps.safetensors", STRUCTURAL),
        ("lcm-lora-sdxl.safetensors", STRUCTURAL),
        ("z_image_turbo_distill.safetensors", STRUCTURAL),
        ("wan_4steps.safetensors", STRUCTURAL),
        ("wan2.1_lightx2v_cfg_step_distill.safetensors", STRUCTURAL),
        ("causvid_14b.safetensors", STRUCTURAL),
        ("sdxl_flash.safetensors", STRUCTURAL),
        ("alice_character_v2.safetensors", RECIPE),
        ("hyperrealism_style.safetensors", RECIPE),
        ("hyper_detailed_skin.safetensors", RECIPE),
        ("turbocharged_cars.safetensors", RECIPE),
        ("superturbo_style.safetensors", RECIPE),
        ("alice_3000steps.safetensors", RECIPE),
        ("alice-v2-1500-steps.safetensors", RECIPE),
    ],
)
def test_guess_mark(filename, mark):
    assert guess_mark(filename) == mark


# ── stacks: core_hash ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "variant",
    [
        {"preview": True},
        {"upscale": True},
        {"face_detailer": True},
        {"hires": True},
        {"loras": ("alice_character.safetensors",)},
    ],
    ids=["preview", "upscale", "face-detailer", "hires-fix", "lora"],
)
def test_plumbing_and_post_processing_stack_with_the_plain_workflow(variant):
    plain, member = _graph(), _graph(**variant)
    assert topology_hash(plain) != topology_hash(member)
    assert core_hash(_doc(plain)) == core_hash(_doc(member))


def _core_label(graph: dict, node_id: str) -> str:
    return core_node_labels(_doc(graph))[node_id]


@pytest.mark.parametrize(
    "variant",
    [
        {"face_detailer": True},
        {"loras": ("a.safetensors",)},
        {"loras": ("a.safetensors", "b.safetensors", "c.safetensors")},
        {"loras": ("a.safetensors",), "face_detailer": True, "upscale": True},
    ],
    ids=["detailer", "one-lora", "three-loras", "all-of-it"],
)
def test_core_labels_agree_across_the_members_of_one_workflow(variant):
    """The core address (#1622): one label per node across every member.

    A slot label changes the moment a node is added anywhere, so it cannot
    address "the sampler" across a workflow's topologies; the label on the
    stripped core graph can. Asserted against a one-LoRA member as well as the
    plain graph, because 1 vs 3 LoRA loaders is the case the issue names.
    """
    one_lora, member = _graph(loras=("a.safetensors",)), _graph(**variant)
    assert core_hash(_doc(one_lora)) == core_hash(_doc(member))
    for node_id in ("1", "4", "5"):  # checkpoint loader, latent, sampler
        assert _core_label(one_lora, node_id) == _core_label(member, node_id)
        assert _core_label(_graph(), node_id) == _core_label(member, node_id)


def test_core_labels_tell_the_nodes_of_one_graph_apart_and_skip_stripped_ones():
    """A shared label that named every node would pass the test above."""
    graph = _graph(loras=("a.safetensors",), face_detailer=True)
    labels = core_node_labels(_doc(graph))
    assert len({labels["1"], labels["4"], labels["5"]}) == 3
    # Stripped: LoRA loader, detailer and its detector have no core address.
    assert not {"L0", "30", "31"} & set(labels)


# ── core rule v2: what the second pass strips ─────────────────────────────


def _positive_through(node_id: str, node: dict) -> dict:
    """``extra`` putting *node* between the positive prompt and the sampler."""
    sampler = _graph()["5"]
    sampler["inputs"]["positive"] = [node_id, 0]
    return {node_id: node, "5": sampler}


# name: (the clean graph's extra, its twin's extra)
CORE_V2_TWINS = {
    "orphan-encoder": (
        None,
        {"97": _node("CLIPTextEncode", text="left", clip=["1", 1])},
    ),
    "orphan-chain": (
        None,
        {
            "96": _node("Textbox", text="left"),
            "97": _node("CLIPTextEncode", text=["96", 0], clip=["1", 1]),
        },
    ),
    "textbox": (
        None,
        {
            "96": _node("Textbox", text="a cat"),
            "2": _node("CLIPTextEncode", text=["96", 0], clip=["1", 1]),
        },
    ),
    "text-multiline": (
        None,
        {
            "96": _node("Text Multiline", text="a cat"),
            "2": _node("CLIPTextEncode", text=["96", 0], clip=["1", 1]),
        },
    ),
    # Not a sink by name: `Combine` matched it before #1696.
    "orphan-combine": (
        None,
        {
            "97": _node(
                "ConditioningCombine", conditioning_1=["2", 0], conditioning_2=["2", 0]
            )
        },
    ),
    "film-grain": (
        None,
        {
            "95": _node("PhotoFilmGrain", images=["6", 0], grain_intensity=0.1),
            "7": _node("SaveImage", images=["95", 0], filename_prefix="out"),
        },
    ),
    "gguf-unet": (
        {"1": _node("UNETLoader", unet_name="base.gguf")},
        {"1": _node("UnetLoaderGGUF", unet_name="base.gguf")},
    ),
    "multigpu-unet": (
        {"1": _node("UNETLoader", unet_name="base.gguf")},
        {"1": _node("UNETLoaderDisTorch2MultiGPU", unet_name="base.gguf")},
    ),
    "shelf-checkpoint": (
        None,
        {"1": _node("PixlStashCheckpointLoader", checkpoint_id="7")},
    ),
    "seed-variance": (
        None,
        _positive_through(
            "94", _node("SeedVarianceEnhancer", conditioning=["2", 0], strength=0.5)
        ),
    ),
}


@pytest.mark.parametrize("pair", CORE_V2_TWINS.values(), ids=CORE_V2_TWINS.keys())
def test_core_v2_stacks_a_graph_with_its_clean_twin(pair):
    """Left-over nodes, film grain, a loader variant or seed variance: one core."""
    clean, member = _graph(extra=pair[0]), _graph(extra=pair[1])
    assert topology_hash(clean) != topology_hash(member)
    assert core_hash(_doc(clean)) == core_hash(_doc(member))
    for node_id in ("1", "4", "5", "6", "7"):
        assert _core_label(clean, node_id) == _core_label(member, node_id)


def test_the_prune_is_refused_when_it_would_take_every_sampler():
    """A graph whose only output was a (stripped) preview keeps its sampler."""
    previewed = _graph(preview=True)
    # The only save left saves an input: the prune would keep that and nothing
    # else, which is what the guard is for.
    previewed["7"] = _node("SaveImage", images=["60", 0])
    previewed["60"] = _node("LoadImage", image="in.png")
    labels = core_node_labels(_doc(previewed))
    assert {"5", "6"} <= set(labels), "the only sampler was pruned"
    # Refused whole: the orphan encoder beside it is kept too.
    orphan = dict(previewed, **CORE_V2_TWINS["orphan-encoder"][1])
    assert "97" in core_node_labels(_doc(orphan))
    assert core_hash(_doc(orphan)) != core_hash(_doc(previewed))


def test_a_dead_node_that_is_not_the_last_sampler_is_pruned():
    extra = {
        "97": _node(
            "KSampler", model=["1", 0], positive=["2", 0], latent_image=["4", 0]
        )
    }
    assert "97" not in core_node_labels(_doc(_graph(extra=extra)))


def _detailer_only(flux: bool) -> dict:
    """A FaceDetailer over an input picture, with no sampler of its own."""
    g = {
        "1": _node("UNETLoader", unet_name="model.safetensors"),
        "3": _node("VAELoader", vae_name="ae.safetensors"),
        "6": _node("LoadImage", image="in.png"),
        "30": _node("UltralyticsDetectorProvider", model_name="face_yolo.pt"),
        "7": _node("SaveImage", images=["31", 0], filename_prefix="out"),
    }
    model = ["1", 0]
    if flux:
        g["2"] = _node("DualCLIPLoader", clip_name1="t5.safetensors", type="flux")
        g["4"] = _node("CLIPTextEncode", text="a face", clip=["2", 0])
        g["5"] = _node("FluxGuidance", conditioning=["4", 0], guidance=3.5)
        positive = ["5", 0]
    else:
        g["2"] = _node("CLIPLoader", clip_name="qwen_3_4b.safetensors", type="lumina2")
        g["4"] = _node("CLIPTextEncode", text="a face", clip=["2", 0])
        g["8"] = _node("ModelSamplingAuraFlow", model=["1", 0], shift=3)
        model, positive = ["8", 0], ["4", 0]
    g["31"] = _node(
        "FaceDetailer",
        image=["6", 0],
        model=model,
        clip=["2", 0],
        vae=["3", 0],
        positive=positive,
        negative=["4", 0],
        bbox_detector=["30", 0],
    )
    return g


UPSCALE_ONLY = {
    "6": _node("LoadImage", image="in.png"),
    "40": _node("UpscaleModelLoader", model_name="4x.pth"),
    "41": _node("ImageUpscaleWithModel", upscale_model=["40", 0], image=["6", 0]),
    "7": _node("SaveImage", images=["41", 0], filename_prefix="out"),
}


def _upscaled(graph: dict) -> dict:
    """*graph* with a model upscale between its last stage and the save."""
    graph = dict(graph)
    graph["40"] = _node("UpscaleModelLoader", model_name="4x.pth")
    graph["41"] = _node(
        "ImageUpscaleWithModel", upscale_model=["40", 0], image=["31", 0]
    )
    graph["7"] = _node("SaveImage", images=["41", 0], filename_prefix="out")
    return graph


def test_a_graph_with_no_sampler_keeps_what_it_does_as_its_core():
    """With a sampler every stage is optional; without one, a detailer is the
    core and an upscale after it optional; with neither, the upscale is.

    Stripped as optional additions, all three were a picture loader feeding a
    save, and their model chains dead nodes to prune.
    """
    cores = {
        core_hash(_doc(graph))
        for graph in (UPSCALE_ONLY, _detailer_only(True), _detailer_only(False))
    }
    assert len(cores) == 3
    flux_labels = core_node_labels(_doc(_detailer_only(True)))
    assert {"1", "2", "5", "31"} <= set(flux_labels), "the model chain was pruned"
    upscale_labels = core_node_labels(_doc(UPSCALE_ONLY))
    assert {"40", "41"} <= set(upscale_labels), "the upscale-only core is empty"
    # A detailer-only graph with an upscale after it is the same workflow.
    upscaled = _upscaled(_detailer_only(True))
    assert core_hash(_doc(upscaled)) == core_hash(_doc(_detailer_only(True)))
    assert not {"40", "41"} & set(core_node_labels(_doc(upscaled)))
    assert core_hash(_doc(upscaled)) != core_hash(_doc(UPSCALE_ONLY))
    # With a sampler, the detailer and the upscale are both optional stages.
    assert core_hash(_doc(_graph(face_detailer=True))) == core_hash(_doc(_graph()))
    assert core_hash(_doc(_graph(face_detailer=True, upscale=True))) == core_hash(
        _doc(_graph())
    )


def test_the_sink_rule_keeps_named_outputs_and_savers_but_not_conditioning():
    """Anchored (#1696): `ConditioningCombine` is not a sink, but a node named
    as an output or a saver still is, so its workflow keeps its last node."""
    for sink in ("SaveImage", "ImageSaver", "ImageOutput", "VHS_VideoCombine"):
        assert _SINK_CLASS_RE.search(sink), sink
    for middle in ("ConditioningCombine", "ImageCompositeMasked", "OutputSwitch"):
        assert not _SINK_CLASS_RE.search(middle), middle


def test_sampler_less_filter_workflows_keep_their_filter_as_their_core():
    """Without a sampler the filter is the workflow (#1696): a sharpen and a
    blur over an input picture are two workflows, never one."""

    def filtered(cls: str) -> dict:
        return {
            "6": _node("LoadImage", image="in.png"),
            "8": _node(cls, image=["6", 0]),
            "7": _node("SaveImage", images=["8", 0], filename_prefix="out"),
        }

    assert core_hash(_doc(filtered("ImageSharpen"))) != core_hash(
        _doc(filtered("ImageBlur"))
    )
    assert "8" in core_node_labels(_doc(filtered("ImageSharpen")))


def test_seed_variance_is_a_stage_the_card_has_and_chips():
    member = _doc(_graph(extra=CORE_V2_TWINS["seed-variance"][1]))
    assert special_groups(member) == (SEED_VARIANCE,)
    assert differs_by(_doc(_graph()), member) == ["+ seed variance"]


def test_core_v2_is_a_function_of_the_v1_core():
    """Many-to-one: graphs sharing a v1 core share a v2 core (data step 8)."""
    graphs = [
        _graph(**variant, extra=extra)
        for variant in ({}, {"preview": True}, {"upscale": True}, {"loras": ("a",)})
        for extra in [None, *(twin for pair in CORE_V2_TWINS.values() for twin in pair)]
    ]
    # A node read only by a preview (which v1 strips) and the same node read by
    # nothing have one v1 core: pruning before the strip would split them.
    shown = {"80": _node("LoadImage", image="x.png")}
    graphs += [
        _graph(extra=shown),
        _graph(extra=dict(shown, **{"81": _node("PreviewImage", images=["80", 0])})),
    ]
    v2_of_v1: dict[str, set[str]] = {}
    for graph in graphs:
        doc = _doc(graph)
        v2_of_v1.setdefault(graph_key(_core_strip_v1(doc)), set()).add(core_hash(doc))
    assert len(v2_of_v1) > 1
    assert all(len(v2) == 1 for v2 in v2_of_v1.values())


# ── core rule v3: primitives, previews and model patches (#1719) ──────────


def _sampler_fed(input_name: str, node_id: str, node: dict, sampler=None) -> dict:
    """``extra`` feeding the sampler's *input_name* from *node*."""
    sampler = sampler or _graph()["5"]
    sampler["inputs"][input_name] = [node_id, 0]
    return {node_id: node, "5": sampler}


def _advanced_sampler(**inputs) -> dict:
    """KSamplerAdvanced with its own widget names, wired as the KSampler is."""
    wired = {k: v for k, v in _graph()["5"]["inputs"].items() if isinstance(v, list)}
    widgets = dict(add_noise="enable", noise_seed=1, steps=20, start_at_step=0)
    return _node("KSamplerAdvanced", **{**wired, **widgets, **inputs})


_SAVER = _node("PixlStashPictureSaver", images=["6", 0])

# name: (the clean graph's extra, its twin's extra)
CORE_V3_TWINS = {
    "seed": (None, _sampler_fed("seed", "90", _node("Seed", seed=5))),
    "jw-integer": (None, _sampler_fed("steps", "90", _node("JWInteger", value=8))),
    "primitive-int": (
        None,
        _sampler_fed("steps", "90", _node("PrimitiveInt", value=8)),
    ),
    "preview-any": (None, {"90": _node("PreviewAny", source=["6", 0])}),
    "prompt-builder": (
        None,
        {
            "90": _node("LoRACharacterPromptBuilder", prompt="a cat"),
            "2": _node("CLIPTextEncode", text=["90", 0], clip=["1", 1]),
        },
    ),
    "project-loader": (
        {"7": _SAVER},
        {
            "90": _node("PixlStashProjectLoader", pixlstash_project="p"),
            "7": _node("PixlStashPictureSaver", images=["6", 0], project=["90", 0]),
        },
    ),
    "aura-flow": (
        None,
        _sampler_fed(
            "model", "90", _node("ModelSamplingAuraFlow", model=["1", 0], shift=3.0)
        ),
    ),
    "sage-attention": (
        None,
        _sampler_fed(
            "model",
            "90",
            _node("PathchSageAttentionKJ", model=["1", 0], sage_attention="auto"),
        ),
    ),
    "ksampler-advanced": (None, {"5": _advanced_sampler()}),
    "picture-saver": (None, {"7": _SAVER}),
}


@pytest.mark.parametrize("pair", CORE_V3_TWINS.values(), ids=CORE_V3_TWINS.keys())
def test_core_v3_stacks_a_graph_with_its_clean_twin(pair):
    clean, member = _graph(extra=pair[0]), _graph(extra=pair[1])
    assert topology_hash(clean) != topology_hash(member)
    assert core_hash(_doc(clean)) == core_hash(_doc(member))
    for node_id in ("1", "4", "5", "6", "7"):
        assert _core_label(clean, node_id) == _core_label(member, node_id)


def test_core_v3_keeps_a_second_pass_apart():
    """Two chained advanced samplers split by step are not the one-pass graph."""
    first = _advanced_sampler(start_at_step=0, end_at_step=10)
    second = _advanced_sampler(latent_image=["5", 0], start_at_step=10)
    one = _graph(extra={"5": _advanced_sampler()})
    two = _graph(
        extra={
            "5": first,
            "8": second,
            "6": _node("VAEDecode", samples=["8", 0], vae=["1", 2]),
        }
    )
    assert core_hash(_doc(one)) != core_hash(_doc(two))


def test_core_v3_keeps_one_and_two_reference_edits_apart():
    def edit(references: int) -> dict:
        extra, conditioning = {}, ["2", 0]
        for i in range(references):
            node_id = f"4{i}"
            extra[node_id] = _node(
                "ReferenceLatent", conditioning=conditioning, latent=["4", 0]
            )
            conditioning = [node_id, 0]
        sampler = _graph()["5"]
        sampler["inputs"]["positive"] = conditioning
        return _graph(extra=dict(extra, **{"5": sampler}))

    assert core_hash(_doc(edit(1))) != core_hash(_doc(edit(2)))


def test_core_v3_is_a_function_of_the_v2_core():
    """Many-to-one: graphs sharing a v2 core share a v3 core (data step 10)."""
    twins = [*CORE_V2_TWINS.values(), *CORE_V3_TWINS.values()]
    graphs = [
        _graph(**variant, extra=extra)
        for variant in ({}, {"preview": True}, {"upscale": True}, {"loras": ("a",)})
        for extra in [None, *(twin for pair in twins for twin in pair)]
    ]
    v3_of_v2: dict[str, set[str]] = {}
    for graph in graphs:
        doc = _doc(graph)
        v3_of_v2.setdefault(graph_key(_core_strip_v2(doc)), set()).add(core_hash(doc))
    assert len(v3_of_v2) > 1
    assert all(len(v3) == 1 for v3 in v3_of_v2.values())


def test_an_extra_lora_loader_splits_when_loras_are_not_stripped():
    plain, member = _doc(_graph()), _doc(_graph(loras=("a.safetensors",)))
    assert core_hash(plain, strip_loras=False) != core_hash(member, strip_loras=False)


def test_img2img_and_txt2img_do_not_stack():
    assert core_hash(_doc(_graph())) != core_hash(_doc(_graph(img2img=True)))


def test_a_real_step_added_does_not_stack():
    # Wired in: a loader nothing reads is a dead node, and the core prunes it.
    extra = {
        "98": _node("ControlNetLoader", control_net_name="canny.safetensors"),
        "99": _node("ControlNetApply", conditioning=["2", 0], control_net=["98", 0]),
    }
    extra["5"] = _graph()["5"]
    extra["5"]["inputs"]["positive"] = ["99", 0]
    assert core_hash(_doc(_graph())) != core_hash(_doc(_graph(extra=extra)))


# ── differs by ──────────────────────────────────────────────────────────────


def test_differs_by_names_post_processing_in_both_directions():
    plain, upscaled = _doc(_graph()), _doc(_graph(upscale=True))
    assert differs_by(plain, upscaled) == ["+ upscale"]
    assert differs_by(plain, upscaled, upscale_factor=2) == ["+ upscale 2×"]
    assert differs_by(upscaled, plain) == ["− upscale"]
    assert differs_by(plain, _doc(_graph(face_detailer=True))) == ["+ face detailer"]
    assert differs_by(plain, _doc(_graph(hires=True))) == ["+ upscale"]


def test_special_groups_says_what_one_graph_has_rather_than_how_two_differ():
    """The same taxonomy `differs_by` chips, asked of a card standing alone.

    A lone card has no cover to differ from, so `differs_by` never runs for it
    and nothing computes what it *has* - which is the half its generated name
    needs (#1454).

    The empty tuple is a real answer and not a failure to look: a plain txt2img
    graph genuinely carries no post-processing, which is what lets a name claim
    the workflow is plain.
    """
    assert special_groups(_doc(_graph())) == ()
    assert special_groups(_doc(_graph(face_detailer=True))) == (FACE_DETAILER,)
    assert special_groups(_doc(_graph(upscale=True))) == (UPSCALE,)
    # A hires fix is an upscale: the second sampler goes with the latent
    # upscale it refines, exactly as `core_hash` strips it.
    assert special_groups(_doc(_graph(hires=True))) == (UPSCALE,)
    # Plumbing is not post-processing, and a LoRA is not either.
    assert special_groups(_doc(_graph(preview=True))) == ()
    assert special_groups(_doc(_graph(loras=("alice.safetensors",)))) == ()
    # **Declaration order, never the graph's.** The cached value is a string
    # and two derivations of one topology have to compare byte for byte, so a
    # set's iteration order would make the cache churn at random.
    both = _doc(_graph(upscale=True, face_detailer=True))
    assert special_groups(both) == (UPSCALE, FACE_DETAILER)


def test_a_loader_on_its_own_is_not_the_graph_doing_the_thing():
    """Narrower than the strip, and deliberately so.

    `node_groups` puts a detector loader in the FACE_DETAILER group because it
    exists to remove a group whole - leaving the loader behind while removing
    the detailer it feeds would change the stack key for nothing. Printing is
    the opposite case: a `SAMLoader` with no detailer in front of it must not
    put "+ FaceDetailer" on the card's only identifying text, and an upscale
    model loaded and never applied must not claim an upscale.
    """
    for loader in (
        _node("SAMLoader", model_name="sam_vit_b.pth"),
        _node("UltralyticsDetectorProvider", model_name="face_yolo.pt"),
        _node("UpscaleModelLoader", model_name="4x_ultra.pth"),
    ):
        graph = _graph(extra={"99": loader})
        # The strip still sees it - that is the behaviour being narrowed, not
        # a bug - and the printed answer does not.
        assert loader["class_type"] in str(graph)
        assert special_groups(_doc(graph)) == ()

    # ... and the worker beside the loader is still counted, so the narrowing
    # did not simply switch the feature off.
    assert special_groups(_doc(_graph(face_detailer=True))) == (FACE_DETAILER,)
    assert special_groups(_doc(_graph(upscale=True))) == (UPSCALE,)


def test_special_groups_refuses_a_raw_graph_like_every_other_rule_here():
    """A raw graph names its models by value; every rule here needs the stored
    document. Reading one anyway would report no groups for a graph that has
    them."""
    with pytest.raises(WorkflowGraphError):
        special_groups(_graph(face_detailer=True))


def test_plumbing_only_when_only_plumbing_differs():
    assert differs_by(_doc(_graph()), _doc(_graph(preview=True))) == ["plumbing only"]


def test_identical_workflows_differ_by_nothing():
    assert differs_by(_doc(_graph()), _doc(_graph())) == []


def test_plumbing_only_is_never_claimed_when_a_model_differs():
    cover = _doc(_graph(loras=("alice.safetensors",)))
    member = _doc(_graph(loras=("bob.safetensors",), preview=True))
    chips = differs_by(cover, member)
    assert "plumbing only" not in chips
    assert chips == ["1 node differs"]

    other_ckpt = _doc(_graph(ckpt="other.safetensors", preview=True))
    assert differs_by(_doc(_graph()), other_ckpt) == [
        "other checkpoint",
        "1 node differs",
    ]


def test_plumbing_only_is_never_claimed_beside_an_unclassified_node():
    extra = {"98": _node("SomeCustomNode"), "99": _node("PreviewImage")}
    assert differs_by(_doc(_graph()), _doc(_graph(extra=extra))) == ["2 nodes differ"]


def _differences(cover: dict, member: dict) -> list[Difference]:
    return differences_reduced(
        reduce_stored_document(_doc(cover)), reduce_stored_document(_doc(member))
    )


def test_a_nodes_chip_names_the_classes_it_counted():
    """#1597: the detail says HOW, and names only what the chip counted."""
    extra = {
        "98": _node("SomeCustomNode"),
        "97": _node("SomeCustomNode"),
        "99": _node("PreviewImage"),
    }
    cover = _graph(loras=("alice.safetensors",))
    # The upscale nodes are "+ upscale"'s, so the nodes chip leaves them out.
    upscale, chip = _differences(cover, _graph(extra=extra, upscale=True))
    assert upscale.chip == "+ upscale"
    assert chip.chip == "4 nodes differ"
    assert chip.detail == "+ PreviewImage · + SomeCustomNode ×2 · − LoraLoader"
    # Reversed, the signs flip with it.
    (back,) = _differences(_graph(extra=extra), cover)
    assert back.detail == "+ LoraLoader · − PreviewImage · − SomeCustomNode ×2"


def test_a_nodes_chip_with_no_class_change_says_what_did_change():
    cover = _graph(loras=("alice.safetensors",))
    rewired = _graph(loras=("alice.safetensors",))
    rewired["5"]["inputs"]["model"] = ["1", 0]
    assert [(d.chip, d.detail) for d in _differences(cover, rewired)] == [
        ("1 node differs", "same nodes, wired differently")
    ]


def test_a_changed_node_says_whether_it_was_a_model_or_a_picture():
    cover = _graph(img2img=True, input_picture="a.png")
    picture = _graph(img2img=True, input_picture="b.png")
    assert [(d.chip, d.detail) for d in _differences(cover, picture)] == [
        ("1 node differs", "LoadImage: other picture")
    ]
    # A LoRA is not an "other models" chip, so a swapped one lands here.
    alice = _graph(loras=("alice.safetensors",))
    bob = _graph(loras=("bob.safetensors",))
    assert [d.detail for d in _differences(alice, bob)] == ["LoraLoader: other model"]


def test_a_model_chip_carries_both_sides_base_model_first():
    member = _graph(ckpt="other.safetensors", upscale=True)
    member["60"] = _node("VAELoader", vae_name="new_vae.safetensors")
    cover = _graph()
    cover["60"] = _node("VAELoader", vae_name="old_vae.safetensors")
    chips = {d.chip: d for d in _differences(cover, member)}
    # The upscaler's model belongs to "+ upscale", never to this chip.
    assert chips["other checkpoint"].cover_assets == (
        asset_reference("base.safetensors"),
        asset_reference("old_vae.safetensors"),
    )
    assert chips["other checkpoint"].member_assets == (
        asset_reference("other.safetensors"),
        asset_reference("new_vae.safetensors"),
    )
    assert chips["+ upscale"].cover_assets == ()
    assert chips["other checkpoint"].detail is None


# ── type ────────────────────────────────────────────────────────────────────


def test_workflow_type():
    assert workflow_type(_doc(_graph())) == "txt2img"
    assert workflow_type(_doc(_graph(img2img=True))) == "img2img"
    inpaint = _graph(img2img=True)
    inpaint["12"] = _node("VAEEncodeForInpaint", pixels=["11", 0], vae=["1", 2])
    assert workflow_type(_doc(inpaint)) == "inpaint"
    outpaint = dict(inpaint, **{"13": _node("ImagePadForOutpaint", image=["10", 0])})
    assert workflow_type(_doc(outpaint)) == "outpaint"
    upscale = {
        "1": _node("LoadImage", image="in.png"),
        "2": _node("UpscaleModelLoader", model_name="4x.pth"),
        "3": _node("ImageUpscaleWithModel", upscale_model=["2", 0], image=["1", 0]),
        "4": _node("SaveImage", images=["3", 0], filename_prefix="out"),
    }
    assert workflow_type(_doc(upscale)) == "upscale"
    assert workflow_type(_doc({"1": _node("SaveImage")})) is None


PAIRED_API = Path(__file__).parent / "comfyui_workflows" / "paired" / "multigpu" / "api"


@pytest.mark.parametrize(
    "name, expected",
    [
        # Saved as a video: VHS_VideoCombine, SaveAnimatedWEBP, SaveWEBM.
        ("ComfyUI-WanVideoWrapper wanvideo2_2 I2V A14B GGUF", "video"),
        ("ComfyUI-WanVideoWrapper wanvideo_T2V", "video"),
        ("ltxvideo checkpointloadersimple distorch2", "video"),
        ("mochi checkpointloaderadvanced distorch2", "video"),
        # Wan 2.2 text to video: its `EmptyHunyuanLatentVideo` used to make it
        # `txt2img` by the `^Empty.*Latent` rule.
        ("wan2_2 distorch2 double_unet no_cpu", "video"),
        ("wan2_2 t2v lightx2v lora distorch2", "video"),
        # The same video latent saving ONE picture is text to image.
        ("wan2_2 t2i lightx2v lora distorch2", "txt2img"),
        ("sdxl checkpoint loader advanced", "txt2img"),
    ],
)
def test_a_graph_that_saves_a_video_is_a_video_workflow(name, expected):
    graph = json.loads((PAIRED_API / f"{name}.json").read_text("utf-8"))
    assert workflow_type(_doc(graph)) == expected


def test_a_video_from_a_start_frame_is_video_not_img2img():
    graph = _graph(img2img=True)
    graph["7"] = _node("SaveVideo", video=["6", 0], filename_prefix="out")
    assert workflow_type(_doc(graph)) == "video"


def test_plumbing_only_is_never_claimed_when_the_wiring_differs():
    cover = _graph(loras=("alice.safetensors",))
    member = _graph(loras=("alice.safetensors",), preview=True)
    member["5"]["inputs"]["model"] = ["1", 0]  # the LoRA no longer reaches the sampler
    assert core_hash(_doc(cover)) == core_hash(_doc(member))
    assert "plumbing only" not in differs_by(_doc(cover), _doc(member))


def test_every_slot_in_a_long_lora_chain_has_its_own_label():
    loras = tuple(f"style_{i}.safetensors" for i in range(12))
    labels = [s.label for s in slots(_doc(_graph(loras=loras))) if s.is_lora]
    assert len(labels) == 12
    assert len(set(labels)) == 12


def test_a_picture_under_another_widget_name_is_not_a_model_slot():
    def custom(picture):
        return _graph(extra={"98": _node("CustomLoad", image_path=picture)})

    assert _key(custom("a.png")) == _key(custom("b.png"))


def test_post_processing_members_are_separate_cards_in_one_stack():
    plain = _graph()
    for variant in ({"upscale": True}, {"hires": True}, {"preview": True}):
        member = _graph(**variant)
        assert _key(plain) != _key(member)
        assert core_hash(_doc(plain)) == core_hash(_doc(member))


def test_a_picture_fed_encode_is_img2img_even_beside_an_empty_latent():
    graph = _graph(img2img=True)
    graph["4"] = _node("EmptyLatentImage", width=512, height=512, batch_size=1)
    assert workflow_type(_doc(graph)) == "img2img"


def _base_and_refiner(base: str, refiner: str, *, preview: bool = False) -> dict:
    g = {
        "1": _node("CheckpointLoaderSimple", ckpt_name=base),
        "2": _node("CheckpointLoaderSimple", ckpt_name=refiner),
        "3": _node("CLIPTextEncode", text="a cat", clip=["1", 1]),
        "4": _node("EmptyLatentImage", width=512, height=512, batch_size=1),
        "5": _node(
            "KSampler", model=["1", 0], positive=["3", 0], latent_image=["4", 0]
        ),
        "6": _node(
            "KSampler", model=["2", 0], positive=["3", 0], latent_image=["5", 0]
        ),
        "7": _node("VAEDecode", samples=["6", 0], vae=["2", 2]),
        "8": _node("SaveImage", images=["7", 0], filename_prefix="out"),
    }
    if preview:
        g["9"] = _node("PreviewImage", images=["7", 0])
    return g


def test_models_swapped_between_loaders_are_a_difference():
    cover = _doc(_base_and_refiner("base.safetensors", "refiner.safetensors"))
    swapped = _base_and_refiner("refiner.safetensors", "base.safetensors")
    assert differs_by(cover, _doc(swapped)) != []
    with_preview = _base_and_refiner(
        "refiner.safetensors", "base.safetensors", preview=True
    )
    assert "plumbing only" not in differs_by(cover, _doc(with_preview))


def test_a_raw_graph_is_refused_rather_than_read_as_having_no_models():
    with pytest.raises(WorkflowGraphError):
        slots(_graph())
    with pytest.raises(WorkflowGraphError):
        core_hash(_graph())


def test_the_pixlstash_picture_loader_and_a_noise_mask_are_picture_sources():
    graph = _graph(img2img=True)
    graph["10"] = _node("PixlStashPictureLoader", picture_ids="1,2")
    assert workflow_type(_doc(graph)) == "img2img"
    graph["13"] = _node("SetLatentNoiseMask", samples=["12", 0])
    assert workflow_type(_doc(graph)) == "inpaint"


# ── the PixlStash shelf loaders (#1416) ─────────────────────────────────────


def _shelf_graph(checkpoint_id="11") -> dict:
    """The same txt2img graph, loading its checkpoint off the model shelf.

    ``PixlStashCheckpointLoader`` is shaped after ``CheckpointLoaderSimple`` --
    the same three outputs in the same order -- so only node 1 changes. It
    names its model by the shelf's row id rather than by digest, because a
    checkpoint's ``sha256`` is NULL until the background hasher has read it.
    """
    graph = _graph()
    graph["1"] = _node("PixlStashCheckpointLoader", checkpoint_id=checkpoint_id)
    return graph


def test_the_shelf_checkpoint_is_part_of_the_workflow():
    a, b = _shelf_graph("11"), _shelf_graph("12")
    assert structural_hash(a) != structural_hash(b)
    assert _key(a) != _key(b)
    assert differs_by(_doc(a), _doc(b)) == ["other checkpoint"]
    # Same card rule as the house loader: a checkpoint swap stays one stack.
    assert core_hash(_doc(a)) == core_hash(_doc(b))


def test_the_shelf_checkpoint_is_the_graph_s_one_model_slot():
    found = slots(_doc(_shelf_graph("11")))
    assert [(s.class_type, s.widget, s.is_lora) for s in found] == [
        ("PixlStashCheckpointLoader", "checkpoint_id", False)
    ]
    assert found[0].asset.startswith("asset:")


def test_the_second_text_encoder_of_a_pair_is_a_model_not_a_parameter():
    """``clip_sha256_2`` is the T5 or Llama beside a clip-l, on one node."""

    def graph(second: str) -> dict:
        return _graph(
            extra={
                "60": _node(
                    "PixlStashCLIPLoader",
                    clip_sha256="aa" * 32,
                    type="flux",
                    clip_sha256_2=second,
                )
            }
        )

    a, b = graph("bb" * 32), graph("cc" * 32)
    assert structural_hash(a) != structural_hash(b)
    assert _key(a) != _key(b)


def test_a_graph_with_no_shelf_loader_keeps_the_keys_it_had():
    """The keys of a graph on ComfyUI's own loader, measured before #1416.

    A pin rather than a comparison: there is nothing to compare against once
    the rule has changed. It guards the blast radius -- the new rules are two
    widget NAMES, so this graph, which carries neither, must key exactly as it
    did. It cannot fail for a change confined to those names, and is not
    claimed to: the tests below are what hold the new rules up.
    """
    graph = _graph()
    assert (
        structural_hash(graph)
        == "dcfda8d6286e65d97f4a71e15a7d2ec710c600e1f521b36db857c17ac70ed5a3"
    )
    assert (
        topology_hash(graph)
        == "04b6983e6e0e88e757920f45dbc2fe2f8bf09e140bcb06e8c9d341bbaf150edd"
    )
    assert (
        _key(graph)
        == "a66cc0095a961c3944ce59f0b8e656ae667c18d765ec7c0c74f87b5e40cab2cf"
    )


def test_an_unpicked_shelf_widget_names_no_model():
    """Empty is the ordinary state, not the odd one.

    A shelf widget is empty until Browse has been clicked, and the CLIP
    loader's second encoder is empty on every SD and SDXL graph. Keeping it
    would file a junk asset row and write the digest of the empty string into
    the stored document as if a model had gone there.
    """
    empty = _shelf_graph("")
    assert slots(_doc(empty)) == []
    assert "asset:" not in json.dumps(_doc(empty))

    # The blank second encoder of an SD/SDXL pair, likewise: one asset on that
    # node, not two. (The node still HAS the widget, and a nulled widget keeps
    # its name by design, so this is about the value and not the shape.)
    def clip_loader(**widgets):
        return _graph(
            extra={"60": _node("PixlStashCLIPLoader", type="sdxl", **widgets)}
        )

    blank = clip_loader(clip_sha256="aa" * 32, clip_sha256_2="")
    assert {s.widget for s in slots(_doc(blank))} == {"ckpt_name", "clip_sha256"}
    # And a blank is not merely absent from the slots: it must not key the
    # graph either, or two SD/SDXL workflows differing in nothing fork.
    assert structural_hash(blank) != structural_hash(
        clip_loader(clip_sha256="aa" * 32, clip_sha256_2="bb" * 32)
    )
    assert "asset:" + _digest_of_empty() not in json.dumps(_doc(blank))


def _digest_of_empty() -> str:
    """The reference a blank widget used to be filed as."""
    return hashlib.sha256(b"").hexdigest()


def test_a_checkpoint_id_written_as_a_number_names_the_same_model():
    """A script-written prompt carries it as JSON number; the node runs it."""
    as_text, as_number = _shelf_graph("11"), _shelf_graph(11)
    assert structural_hash(as_text) == structural_hash(as_number)
    assert _key(as_text) == _key(as_number)
    # `True` is an `int` in Python and is not a shelf id.
    assert slots(_doc(_shelf_graph(True))) == []


def test_a_numbered_digest_slot_is_a_lora_slot_and_takes_a_mark():
    """`SHA256_FIELD_RE` keys it, so `is_lora_widget` has to claim it.

    Missed, it is a non-LoRA slot and reaches the card key unconditionally, so
    swapping a character LoRA in a stacker's second slot forks the workflow
    into a new card -- what marks exist to prevent.
    """

    def stacker(second: str) -> dict:
        return _graph(
            extra={
                "60": _node(
                    "PixlStashLoraStacker",
                    lora_sha256="aa" * 32,
                    lora_sha256_2=second,
                )
            }
        )

    a, b = stacker("bb" * 32), stacker("cc" * 32)
    assert [s.is_lora for s in slots(_doc(a)) if s.widget.startswith("lora_")] == [
        True,
        True,
    ]
    # Both are recipe slots by default, so the card does not fork on a swap.
    assert _key(a) == _key(b)
    # ...but the variant hash still moves, because the recipe did.
    assert structural_hash(a) != structural_hash(b)


def test_a_checkpoint_id_that_is_not_a_shelf_id_names_nothing():
    """The node refuses anything but digits, so the hash rule does too.

    Name-only would let any node with a widget of that name write whatever it
    holds into ``workflow_recipe_asset``, which is kept forever and shared.
    """
    for value in (
        "",
        "Not A Model",
        "a picture of a cat\nin two lines",
        # `^\d+$` accepts both of these -- `$` matches before a final newline,
        # and a character class says nothing about length. `str.isdigit()` and
        # the cap are what refuse them.
        "11\n",
        "1" * 400,
        "x" * 400,
    ):
        assert slots(_doc(_shelf_graph(value))) == [], value
    # A third-party node is judged by the same rule: digits are a model
    # whoever carries them, and anything else is a parameter.
    prose = _graph(extra={"60": _node("SomeOtherPack_Loader", checkpoint_id="latest")})
    assert [s.widget for s in slots(_doc(prose))] == ["ckpt_name"]
