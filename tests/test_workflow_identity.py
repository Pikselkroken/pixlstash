"""Workflow cards and automatic stacks derived from stored graphs (v1.12 B1).

Every stacking case in the user plan §2 is a pair: two workflows, and whether
they share a card (``workflow_key``) and a stack (``core_hash``).
"""

import json

import pytest

from pixlstash.services.workflow_hash import (
    WorkflowGraphError,
    structural_document,
    topology_hash,
)
from pixlstash.services.workflow_identity import (
    RECIPE,
    STRUCTURAL,
    core_hash,
    differs_by,
    guess_mark,
    slots,
    workflow_key,
    workflow_type,
)


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


def test_an_extra_lora_loader_splits_when_loras_are_not_stripped():
    plain, member = _doc(_graph()), _doc(_graph(loras=("a.safetensors",)))
    assert core_hash(plain, strip_loras=False) != core_hash(member, strip_loras=False)


def test_img2img_and_txt2img_do_not_stack():
    assert core_hash(_doc(_graph())) != core_hash(_doc(_graph(img2img=True)))


def test_a_real_step_added_does_not_stack():
    extra = {"98": _node("ControlNetLoader", control_net_name="canny.safetensors")}
    assert core_hash(_doc(_graph())) != core_hash(_doc(_graph(extra=extra)))


# ── differs by ──────────────────────────────────────────────────────────────


def test_differs_by_names_post_processing_in_both_directions():
    plain, upscaled = _doc(_graph()), _doc(_graph(upscale=True))
    assert differs_by(plain, upscaled) == ["+ upscale"]
    assert differs_by(plain, upscaled, upscale_factor=2) == ["+ upscale 2×"]
    assert differs_by(upscaled, plain) == ["− upscale"]
    assert differs_by(plain, _doc(_graph(face_detailer=True))) == ["+ face detailer"]
    assert differs_by(plain, _doc(_graph(hires=True))) == ["+ upscale"]


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
