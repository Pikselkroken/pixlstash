"""Character sheets: the layout prompt, the cut into panels, and the routes."""

import io
import json
import pathlib
import tempfile
import time
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlmodel import select

from pixlstash.db_models import CharacterReferenceMember, Face, Picture
from pixlstash.server import Server
from pixlstash.event_types import EventType
from pixlstash.routes.comfyui import WORKFLOW_BUILTIN_DIR
from pixlstash.services import character_sheet, comfyui_service, scrapheap_service
from pixlstash.routes import workflows as workflows_routes
from pixlstash.services import prompt_llm, workflow_run_service
from pixlstash.services.comfyui_recipe_service import plan_lora_insertion
from pixlstash.services.prompt_assist import assemble
from pixlstash.services.workflow_hash import reduce_api_graph
from pixlstash.services.workflow_identity import (
    SHEET_LAYOUT_ENCODER,
    draws_sheet_layout,
    reference_input_count,
)
from pixlstash.services.workflow_io import detect_workflow_io
from pixlstash.services.workflow_parameters import BLANK_PICTURE_CLASSES
from pixlstash.services.workflow_run_service import run_seed_targets
from pixlstash.utils.insightface_model_utils import DEFAULT_MODEL_PACK
from pixlstash.services.character_sheet import (
    ITEM_AGREEMENT,
    ITEM_PHOTO_LOOK,
    PHOTO_LOOK,
    SHEET_HEIGHT,
    SHEET_WIDTH,
    agreement,
    agreement_words,
    closeness,
    colour_agreement,
    item_agreement_words,
    item_canvas,
    item_sheet_cells,
    item_sheet_prompt,
    panel_of,
    panel_scores,
    sheet_cells,
    sheet_prompt,
)
from tests.utils import (
    API_PREFIX,
    bearer_client,
    mint_read_token,
    upload_pictures_and_wait,
)

# Panel 1-6 of the 2560×1536 grid in MOSS's centred y-x boxes.
CELLS = [
    (-768, -1280, 0, -680),
    (-768, -680, 0, -80),
    (0, -1280, 768, -680),
    (0, -680, 768, -80),
    (-768, -80, 768, 600),
    (-768, 600, 768, 1280),
]


def _panel_of(caption: str) -> int:
    return int(caption.split(":", 1)[0].removeprefix("Panel "))


@pytest.mark.parametrize("proportions", ["typical", "little_person", "cartoon"])
@pytest.mark.parametrize("framing", ["waist_up", "mid_thigh", "knees_up", "full_body"])
def test_every_box_lands_inside_its_own_panel(framing, proportions):
    layout = json.loads(
        sheet_prompt(
            description="an adult woman", framing=framing, proportions=proportions
        )
    )
    items = layout["items"]
    assert layout["bbox_format"] == "yxyx"
    assert {_panel_of(i["caption"]) for i in items} == set(range(1, 7))
    for item in items:
        y1, x1, y2, x2 = item["bbox"]
        cy1, cx1, cy2, cx2 = CELLS[_panel_of(item["caption"]) - 1]
        assert cy1 <= y1 < y2 <= cy2 and cx1 <= x1 < x2 <= cx2, item
    # One character item per panel, carrying the whole character.
    characters = [i for i in items if i["label"] == "character"]
    assert len(characters) == 6
    assert all("an adult woman" in i["caption"] for i in characters)


@pytest.mark.parametrize("size", ["small", "medium", "4k", "5k", "6k"])
def test_another_sheet_size_scales_the_layout_with_its_canvas(size):
    width, height = character_sheet.SIZES[size]
    assert width * 3 == height * 5 and width % 16 == 0 and height % 16 == 0
    large = json.loads(sheet_prompt(description="x"))
    scaled = json.loads(sheet_prompt(description="x", size=size))
    scale = width / SHEET_WIDTH
    for big, small in zip(large["items"], scaled["items"]):
        assert small["caption"] == big["caption"]
        assert small["bbox"] == [round(v * scale) for v in big["bbox"]]


def test_waist_up_draws_the_head_bigger_than_mid_thigh():
    """Waist-up is the framing for pixels: the body photos' heads are larger."""

    def head(framing):
        items = json.loads(sheet_prompt(description="x", framing=framing))["items"]
        y1, x1, y2, x2 = next(
            i["bbox"] for i in items if i["caption"].startswith("Panel 5: Their head")
        )
        return (y2 - y1) * (x2 - x1)

    assert head("waist_up") > 1.5 * head("mid_thigh")
    summary = json.loads(sheet_prompt(description="x", framing="waist_up"))["summary"]
    assert "cropped at the waist" in summary["caption"]


def test_the_mid_thigh_boxes_are_the_probed_ones():
    """The boxes that held their layout on the probe sheets, unchanged."""
    layout = json.loads(sheet_prompt(description="x", framing="mid_thigh"))
    assert [i["bbox"] for i in layout["items"]][12:] == [
        [-740, -70, 748, 590],
        [-725, 125, -410, 395],
        [-410, -75, 330, 595],
        [330, -55, 748, 575],
        [-740, 610, 748, 1270],
        [-725, 805, -410, 1075],
        [-410, 605, 330, 1275],
        [330, 625, 748, 1255],
    ]


@pytest.mark.parametrize("framing", ["waist_up", "mid_thigh", "knees_up", "full_body"])
def test_other_proportions_give_the_head_more_of_the_body_photo(framing):
    """A little person or a cartoon is drawn with a bigger head and shorter
    legs than an adult (#1079): the boxes say so as well as the words, and a
    typical figure keeps the probed boxes."""

    def shares(proportions):
        layout = json.loads(
            sheet_prompt(description="x", framing=framing, proportions=proportions)
        )
        body = [i for i in layout["items"] if _panel_of(i["caption"]) == 5]
        (top, _, bottom, _), (h1, _, h2, _) = body[0]["bbox"], body[1]["bbox"]
        return layout, (h2 - h1) / (bottom - top)

    plain = sheet_prompt(description="x", framing=framing)
    typical, head = shares("typical")
    assert json.dumps(typical) == plain
    little, little_head = shares("little_person")
    cartoon, cartoon_head = shares("cartoon")
    assert head < little_head < cartoon_head
    for layout, words in (
        (little, "a little person with dwarfism"),
        (cartoon, "about three heads tall"),
    ):
        captions = [i["caption"] for i in layout["items"] if i["label"] == "character"]
        assert len(captions) == 6 and all(words in c for c in captions)
        assert words in layout["summary"]["caption"]
    assert "three heads" not in plain and "little person" not in plain
    assert "a grown adult with an adult face" in json.dumps(cartoon)


def test_a_prose_prompt_names_the_proportions():
    prose = sheet_prompt(description="x", proportions="cartoon", layout="prose")
    assert "about three heads tall" in prose


def test_trousers_are_always_named_and_shoes_only_at_full_body():
    def text(framing):
        return sheet_prompt(
            description="x",
            trousers="red corduroys",
            shoes="white sneakers",
            framing=framing,
        )

    for framing in ("mid_thigh", "knees_up", "full_body"):
        assert "red corduroys" in text(framing)
    assert "white sneakers" not in text("mid_thigh")
    assert "white sneakers" not in text("knees_up")
    assert "white sneakers" in text("full_body")


@pytest.mark.parametrize("layout", ["moss", "prose"])
def test_a_background_replaces_the_studio_and_none_keeps_it(layout):
    def summary(**kwargs):
        text = sheet_prompt(description="x", layout=layout, **kwargs)
        return text if layout == "prose" else json.loads(text)["summary"]["caption"]

    studio = "same person on a plain light-grey studio background, soft even "
    assert studio in summary()
    assert studio in summary(background="  ")
    park = summary(background="A sunlit park with tall trees.")
    assert "same person with the background: a sunlit park with tall trees, ph" in park
    assert "studio" not in park


def test_build_trigger_and_subject_reach_every_panel():
    layout = json.loads(
        sheet_prompt(
            description="Adult man with a grey beard",
            body="broad and stocky",
            subject="man",
            trigger="kai",
        )
    )
    characters = [i["caption"] for i in layout["items"] if i["label"] == "character"]
    assert all(
        ": kai, adult man with a grey beard, broad and stocky" in c for c in characters
    )
    assert "same man" in layout["summary"]["caption"]
    assert " her " not in json.dumps(layout)


@pytest.mark.parametrize("framing", ["waist_up", "mid_thigh", "knees_up", "full_body"])
def test_no_build_given_says_none_and_a_given_one_reaches_the_torso(framing):
    """The build lives in the description: with no separate body nothing
    stands in for it, since "average build" would contradict "curvy"."""
    described = sheet_prompt(description="Adult woman, curvy", framing=framing)
    assert "average build" not in described
    assert "body shape:" not in described
    assert "{" not in "".join(i["caption"] for i in json.loads(described)["items"])
    given = sheet_prompt(description="Adult woman", body="curvy", framing=framing)
    assert "Their body shape: curvy." in given
    assert "adult woman, curvy" in given


def test_a_reference_prompt_names_the_picture_and_keeps_every_box():
    """#2679: the picture sets the face, so each photo names it; the layout
    the cut relies on does not move."""
    plain = json.loads(sheet_prompt(description="Auburn hair", body="slim"))
    drawn = json.loads(
        sheet_prompt(description="Auburn hair", body="slim", reference=True)
    )
    assert [i["bbox"] for i in drawn["items"]] == [i["bbox"] for i in plain["items"]]
    characters = [i for i in drawn["items"] if i["label"] == "character"]
    assert len(characters) == 6
    for item in characters:
        assert (
            "the same person as in the reference picture, auburn hair, slim"
            in item["caption"]
        )
    assert "reference picture" not in json.dumps(plain)
    # No description: the picture alone says who.
    bare = json.loads(sheet_prompt(description="", reference=True))
    assert (
        "the same person as in the reference picture, wearing"
        in (bare["items"][0]["caption"])
    )


def test_a_prose_prompt_is_the_summary_sentence_not_the_layout():
    layout = json.loads(sheet_prompt(description="x", reference=True))
    prose = sheet_prompt(
        description="x", reference=True, layout="prose", photo_look=False
    )
    # The same sentence, with the eye colour among what every photo shares.
    assert prose == layout["summary"]["caption"].replace(
        "Identical face, hair", "Identical face, eye colour, hair"
    )
    assert prose.startswith("Character reference sheet of one and the same person")


def test_the_photo_look_is_the_prose_default_and_leaves_the_layouts_alone():
    """Ordinary unedited photographs, not renders: on by default for prose,
    and the probed MOSS and regions layouts never see it."""
    look = sheet_prompt(description="x", layout="prose")
    plain = sheet_prompt(description="x", layout="prose", photo_look=False)
    assert look.endswith(f" {PHOTO_LOOK}")
    assert "photorealistic" not in look
    assert PHOTO_LOOK not in plain and "photorealistic photographs" in plain
    for layout in ("moss", "regions"):
        assert sheet_prompt(description="x", layout=layout) == sheet_prompt(
            description="x", layout=layout, photo_look=False
        )
        text = sheet_prompt(description="x", layout=layout)
        assert "digitals" not in text and "eye colour" not in text


def test_the_sheet_look_the_run_adds_is_the_photo_look_prompt_byte_for_byte():
    """`look_added_by_run` hands the look to the run's prompt assist; its sheet
    default then sends the model exactly what #2776's photo look did."""
    words = sheet_prompt(description="x", layout="prose", look_added_by_run=True)
    assert "photorealistic" not in words and PHOTO_LOOK not in words
    graph = json.loads(
        (
            pathlib.Path(character_sheet.__file__).parent.parent
            / "data/comfyui-workflows/built-in/MiniMax H3 Image T2I (PixlStash).json"
        ).read_text(encoding="utf-8")
    )
    stamp = assemble("minimax_h3_image", "sheet", {}, words)
    workflow_run_service.apply_prompts(graph, words, None, None, stamp)
    assert graph["6"]["inputs"]["prompt"] == sheet_prompt(
        description="x", layout="prose"
    )
    # Whatever photo_look says, and the layouts never see it.
    assert words == sheet_prompt(
        description="x", layout="prose", photo_look=False, look_added_by_run=True
    )
    for layout in ("moss", "regions"):
        assert sheet_prompt(
            description="x", layout=layout, look_added_by_run=True
        ) == sheet_prompt(description="x", layout=layout)


def _edit_graph(latent: str) -> dict:
    """A Qwen-style edit graph: the picture feeds the encoder's reference and,
    by *latent*, the canvas (``encode``), its size only (``sized``), or
    nothing (``literal``)."""
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
        "2": {
            "class_type": "TextEncodeQwenImageEditPlus",
            "inputs": {"image1": ["1", 0], "prompt": "x"},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "positive": ["2", 0],
                "latent_image": ["4", 0],
                "denoise": 0.8,
            },
        },
        "5": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0]}},
        "6": {"class_type": "SaveImage", "inputs": {"images": ["5", 0]}},
    }
    if latent == "encode":
        graph["4"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0]}}
    else:
        graph["7"] = {"class_type": "GetImageSize", "inputs": {"image": ["1", 0]}}
        sized = latent == "sized"
        graph["4"] = {
            "class_type": "EmptySD3LatentImage",
            "inputs": {
                "width": ["7", 0] if sized else 1024,
                "height": ["7", 1] if sized else 1024,
                "batch_size": 1,
            },
        }
    return graph


def _klein_graph() -> dict:
    """The reference is encoded for ``ReferenceLatent``; the canvas is empty."""
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
        "2": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
        "4": {
            "class_type": "ReferenceLatent",
            "inputs": {"conditioning": ["3", 0], "latent": ["2", 0]},
        },
        "5": {
            "class_type": "EmptyFlux2LatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {"positive": ["4", 0], "latent_image": ["5", 0]},
        },
    }


@pytest.mark.parametrize(
    ("graph", "count"),
    [
        # The canvas is empty and the picture is read: a reference.
        (_edit_graph("literal"), 1),
        # An empty latent sized from the picture still starts from nothing.
        (_edit_graph("sized"), 1),
        # Typed img2img for its VAEEncode, yet the sampler starts empty.
        (_klein_graph(), 1),
        # The sampler starts from the picture's own latent: not a reference.
        (_edit_graph("encode"), 0),
        # A picture read for something else (a ControlNet's pose) is no reference.
        (
            {
                **_klein_graph(),
                "4": {
                    "class_type": "ControlNetApplyAdvanced",
                    "inputs": {"positive": ["3", 0], "image": ["1", 0]},
                },
            },
            0,
        ),
        # Text to image reads no picture.
        (
            {
                "1": {"class_type": "EmptyLatentImage", "inputs": {}},
                "2": {"class_type": "KSampler", "inputs": {"latent_image": ["1", 0]}},
            },
            0,
        ),
    ],
)
def test_reference_inputs_are_pictures_read_while_the_canvas_starts_empty(graph, count):
    assert reference_input_count(reduce_api_graph(graph)) == count


def test_a_second_pass_starting_from_the_first_keeps_the_reference():
    """A hires pass's latent comes from the first sampler, whose conditioning
    holds the reference: only its own latent is followed."""
    graph = _klein_graph()
    graph["7"] = {
        "class_type": "KSampler",
        "inputs": {"positive": ["4", 0], "latent_image": ["6", 0]},
    }
    assert reference_input_count(reduce_api_graph(graph)) == 1


@pytest.mark.parametrize("latent", ["encode", "sized"])
def test_converting_to_a_sheet_workflow_gives_the_canvas_a_size_of_its_own(latent):
    graph = _edit_graph(latent)
    before = json.dumps(graph)
    sheet = character_sheet.convert_to_sheet(graph)
    assert json.dumps(graph) == before
    canvas = sheet[sheet["3"]["inputs"]["latent_image"][0]]
    assert canvas["class_type"] == "EmptySD3LatentImage"
    assert (canvas["inputs"]["width"], canvas["inputs"]["height"]) == (
        SHEET_WIDTH,
        SHEET_HEIGHT,
    )
    # The picture still reaches the reference input, and now nothing else.
    assert sheet["2"]["inputs"]["image1"] == ["1", 0]
    assert reference_input_count(reduce_api_graph(sheet)) == 1
    assert sheet["3"]["inputs"]["denoise"] == 1.0


def test_a_qwen_image_2_1_edit_takes_the_canvas_side_of_its_switch():
    """Qwen-Image 2.1's template switches between the picture's own latent
    and an empty canvas, and saves through ``SaveImageAdvanced``, which
    PixlStash collects nothing from (#2700)."""
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
        "2": {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {"prompt": "x", "images.image_1": ["1", 0]},
        },
        "3": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        },
        "4": {
            "class_type": "ComfySwitchNode",
            "inputs": {"switch": False, "on_false": ["2", 2], "on_true": ["3", 0]},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {"positive": ["2", 0], "latent_image": ["4", 0]},
        },
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0]}},
        "7": {
            "class_type": "SaveImageAdvanced",
            "inputs": {"images": ["6", 0], "filename_prefix": "q", "format": "exr"},
        },
        "8": {
            "class_type": "ImageCompare",
            "inputs": {"image_a": ["1", 0], "image_b": ["7", 0]},
        },
    }
    assert reference_input_count(reduce_api_graph(graph)) == 0
    sheet = character_sheet.convert_to_sheet(graph)
    assert sheet["5"]["inputs"]["latent_image"] == ["3", 0]
    assert sheet["3"]["inputs"]["width"] == SHEET_WIDTH
    assert sheet["3"]["inputs"]["height"] == SHEET_HEIGHT
    assert sheet["7"] == {
        "class_type": "SaveImage",
        "inputs": {"images": ["6", 0], "filename_prefix": "q"},
    }
    assert sheet["8"]["inputs"]["image_b"] == ["6", 0]
    assert reference_input_count(reduce_api_graph(sheet)) == 1


def test_a_klein_edit_sized_from_its_picture_sizes_its_scheduler_too():
    """FLUX.2 Klein's edit template sizes its canvas and its scheduler from
    the picture: both take the sheet's size (#2700)."""
    graph = _klein_graph()
    graph["7"] = {"class_type": "GetImageSize", "inputs": {"image": ["1", 0]}}
    graph["5"]["inputs"].update(width=["7", 0], height=["7", 1])
    graph["8"] = {
        "class_type": "Flux2Scheduler",
        "inputs": {"steps": 4, "width": ["7", 0], "height": ["7", 1]},
    }
    sheet = character_sheet.convert_to_sheet(graph)
    for node in ("5", "8"):
        assert sheet[node]["inputs"]["width"] == SHEET_WIDTH
        assert sheet[node]["inputs"]["height"] == SHEET_HEIGHT
    assert reference_input_count(reduce_api_graph(sheet)) == 1


@pytest.mark.parametrize(
    ("name", "references", "size"),
    [
        ("FLUX.2 Klein 9B Sheet.json", 0, "large"),
        ("FLUX.2 Klein 9B Sheet from Reference.json", 1, "large"),
        ("MiniMax H3 Image Sheet (PixlStash).json", 0, "4k"),
        ("MiniMax H3 Image Sheet from Reference (PixlStash).json", 1, "4k"),
        ("Qwen-Image 2.1 Sheet.json", 0, "large"),
        ("Qwen-Image 2.1 Sheet from Reference.json", 1, "large"),
    ],
)
def test_the_shipped_sheet_workflows_draw_at_the_sheet_size(name, references, size):
    """The step lists a workflow from a reference only with a reference input,
    and draws only one with a size to set: every size is one of the sheet's
    (#2700), and MiniMax H3 Image's is 4K, the least it draws faces well at."""
    graph = json.loads((pathlib.Path(WORKFLOW_BUILTIN_DIR) / name).read_text())
    assert reference_input_count(reduce_api_graph(graph)) == references
    sizes = {
        (node["inputs"]["width"], node["inputs"]["height"])
        for node in graph.values()
        if "width" in node["inputs"] and node["class_type"] not in BLANK_PICTURE_CLASSES
    }
    assert sizes == {character_sheet.SIZES[size]}


# The outputs of every class the H3 Image sheets use, as ComfyUI declares them:
# what the LoRA splice types its links by.
_H3_IMAGE_OUTPUTS = {
    "LoadImage": ["IMAGE", "MASK"],
    "UNETLoader": ["MODEL"],
    "PixlStashCLIPLoader": ["CLIP"],
    "PixlStashVAELoader": ["VAE"],
    "MiniMaxH3ImageConditioning": ["CONDITIONING", "LATENT"],
    "ConditioningZeroOut": ["CONDITIONING"],
    "EmptyLatentImage": ["LATENT"],
    "EmptyImage": ["IMAGE"],
    "PrimitiveStringMultiline": ["STRING"],
    "ConditioningSetTimestepRange": ["CONDITIONING"],
    "ConditioningCombine": ["CONDITIONING"],
    "RandomNoise": ["NOISE"],
    "CFGGuider": ["GUIDER"],
    "SamplerLCM": ["SAMPLER"],
    "BasicScheduler": ["SIGMAS"],
    "SamplerCustomAdvanced": ["LATENT", "LATENT"],
    "VAEDecode": ["IMAGE"],
    "SaveImage": [],
}


@pytest.mark.parametrize(
    "name",
    [
        "MiniMax H3 Image T2I (PixlStash).json",
        "MiniMax H3 Image Sheet (PixlStash).json",
        "MiniMax H3 Image Sheet from Reference (PixlStash).json",
    ],
)
def test_the_h3_image_sheets_and_t2i_draw_with_lcm_and_a_run_still_reaches_them(
    name,
):
    """euler at 6 steps draws H3 Image faces crunchy; SamplerLCM with its
    noise easing from 1.1 to 0.9 draws them clean, for a sheet and for a plain
    text to image alike. A run must still find the seed (now RandomNoise's)
    and splice a LoRA into both readers of the model."""
    graph = json.loads((pathlib.Path(WORKFLOW_BUILTIN_DIR) / name).read_text())
    by_class = {node["class_type"]: node["inputs"] for node in graph.values()}
    assert by_class["SamplerLCM"] == {
        "s_noise": 1.1,
        "s_noise_end": 0.9,
        "noise_clip_std": 0.0,
    }
    assert "KSampler" not in by_class
    assert by_class["CFGGuider"]["cfg"] == 1.0
    assert by_class["BasicScheduler"]["steps"] == 6
    assert [(t["class_type"], t["field"]) for t in run_seed_targets(graph, None)] == [
        ("RandomNoise", "noise_seed")
    ]
    info = {cls: {"output": out} for cls, out in _H3_IMAGE_OUTPUTS.items()}
    rewired = {
        (r["class_type"], r["field"])
        for r in plan_lora_insertion(graph, info)["rewires"]
    }
    assert {("CFGGuider", "model"), ("BasicScheduler", "model")} <= rewired


@pytest.mark.parametrize(
    "name",
    [
        "MiniMax H3 Image T2I (PixlStash).json",
        "MiniMax H3 Image Sheet (PixlStash).json",
    ],
)
def test_the_h3_image_text_workflows_condition_on_a_blank_picture_never_drawn_from(
    name,
):
    """A blank picture in image_1 puts the ``<Picture 1>`` label and vision
    tokens ahead of the prompt, which draws H3 Image cleaner from words. With
    no VAE on the conditioning it is not a reference latent, and PixlStash
    must still read the workflow as text to image with no picture to fill."""
    graph = json.loads((pathlib.Path(WORKFLOW_BUILTIN_DIR) / name).read_text())
    conditioning = graph["6"]["inputs"]
    blank = graph[conditioning["images.image_1"][0]]
    assert blank == {
        "inputs": {"width": 32, "height": 32, "batch_size": 1, "color": 0},
        "class_type": "EmptyImage",
    }
    assert "vae" not in conditioning
    assert reference_input_count(reduce_api_graph(graph)) == 0
    assert detect_workflow_io(graph).picture_inputs == ()


@pytest.mark.parametrize(
    ("graph", "words"),
    [
        (_edit_graph("literal"), "already draws at a size of its own"),
        # With no picture it is text to image, and the Qwen edit encoder is
        # no CLIP Text Encode.
        (
            {k: v for k, v in _edit_graph("literal").items() if k != "1"},
            "encoded by TextEncodeQwenImageEditPlus",
        ),
        (
            {
                **_edit_graph("sized"),
                "9": {"class_type": "KSampler", "inputs": {"latent_image": ["3", 0]}},
            },
            "samples in 2 passes",
        ),
        # The picture sizes the canvas but reaches no reference input.
        (
            {
                **_edit_graph("sized"),
                "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
            },
            "does not reach an edit model's reference",
        ),
        # A picture's own latent with no Qwen edit encoder: no canvas class
        # is known for it.
        (
            {
                **_edit_graph("encode"),
                "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
            },
            "starts from VAEEncode",
        ),
    ],
)
def test_a_graph_that_cannot_draw_a_sheet_is_refused_with_its_reason(graph, words):
    with pytest.raises(character_sheet.SheetConversionRefused) as refused:
        character_sheet.convert_to_sheet(graph)
    assert words in refused.value.reason


def _t2i_graph(sized: bool = False) -> dict:
    """Flux-style text to image: the prompt reaches the sampler through
    FluxGuidance, the negative is a second encoder."""
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "a"}},
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": "x"},
        },
        "3": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": ""}},
        "5": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["3", 0],
                "negative": ["4", 0],
                "latent_image": ["5", 0],
            },
        },
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0]}},
    }
    if sized:
        graph["9"] = {"class_type": "ResolutionSelector", "inputs": {}}
        graph["5"]["inputs"].update(width=["9", 0], height=["9", 1])
    return graph


@pytest.mark.parametrize("sized", [False, True])
def test_a_text_to_image_copy_reads_its_prompt_as_the_sheets_layout(sized):
    """The positive encoder becomes Layout Text Encode with its own wiring, so
    the run's prompt lands where it did; the negative is left alone."""
    graph = _t2i_graph(sized)
    before = json.dumps(graph)
    sheet = character_sheet.convert_to_sheet(graph)
    assert json.dumps(graph) == before
    assert sheet["2"] == {
        "class_type": SHEET_LAYOUT_ENCODER,
        "inputs": {"clip": ["1", 1], "text": "x", "region_strength": 1.0},
    }
    assert sheet["4"]["class_type"] == "CLIPTextEncode"
    canvas = sheet["5"]["inputs"]
    # The layout's 5:3, wired or square before: a run sets its own size.
    assert (canvas["width"], canvas["height"]) == (SHEET_WIDTH, SHEET_HEIGHT)
    detected = detect_workflow_io(sheet)
    assert (detected.positive_prompts, detected.negative_prompts) == (("2",), ("4",))
    assert draws_sheet_layout(reduce_api_graph(sheet))
    assert not draws_sheet_layout(reduce_api_graph(graph))


def test_a_text_to_image_copy_saves_with_save_image():
    """The advanced saver is not one PixlStash collects from: a sheet drawn
    through it would never reach the library."""
    graph = _t2i_graph()
    graph["8"] = {
        "class_type": "SaveImageAdvanced",
        "inputs": {"images": ["7", 0], "filename_prefix": "sheet", "format": "exr"},
    }
    sheet = character_sheet.convert_to_sheet(graph)
    assert sheet["8"] == {
        "class_type": "SaveImage",
        "inputs": {"images": ["7", 0], "filename_prefix": "sheet"},
    }


@pytest.mark.parametrize(
    ("change", "words"),
    [
        (
            lambda g: g["2"].update(class_type="CLIPTextEncodeSDXL"),
            "encoded by CLIPTextEncodeSDXL",
        ),
        (lambda g: g["2"].update(class_type=SHEET_LAYOUT_ENCODER), "already draws"),
        # One encoder for both sides: the negative would become regions too.
        (lambda g: g["6"]["inputs"].update(negative=["2", 0]), "are one node"),
        (
            lambda g: g["5"].update(class_type="LoadLatent", inputs={}),
            "starts from LoadLatent",
        ),
        # Two prompts merged into one conditioning: which is the sheet's?
        (
            lambda g: g["3"].update(
                class_type="ConditioningCombine",
                inputs={"conditioning_1": ["2", 0], "conditioning_2": ["4", 0]},
            ),
            "does not find one prompt",
        ),
    ],
)
def test_a_text_to_image_graph_that_cannot_draw_a_sheet_says_why(change, words):
    graph = _t2i_graph()
    change(graph)
    with pytest.raises(character_sheet.SheetConversionRefused) as refused:
        character_sheet.convert_to_sheet(graph)
    assert words in refused.value.reason


@pytest.mark.parametrize("size", ["small", "large"])
def test_a_regions_prompt_is_the_moss_layout_with_its_canvas_and_standalone_captions(
    size,
):
    """Each caption is encoded over its own box, so none says "Panel n" and
    every one names who it is of; the boxes are MOSS's own."""
    kwargs = dict(description="Adult woman, copper bob", trigger="anna", size=size)
    moss = json.loads(sheet_prompt(**kwargs))
    regions = json.loads(sheet_prompt(layout="regions", **kwargs))
    assert regions["canvas"] == list(character_sheet.SIZES[size])
    assert "canvas" not in moss
    assert [i["bbox"] for i in regions["items"]] == [i["bbox"] for i in moss["items"]]
    assert regions["summary"] == moss["summary"]
    for item in regions["items"]:
        assert not item["caption"].startswith("Panel")
        assert item["caption"].startswith("anna, adult woman, copper bob")


def _sheet(lines=(), size=(SHEET_WIDTH, SHEET_HEIGHT)) -> Image.Image:
    """A flat grey sheet with dark 4 px dividers: ("x", at, top, bottom) or ("y", at, left, right)."""
    image = Image.new("RGB", size, (200, 200, 200))
    draw = ImageDraw.Draw(image)
    for axis, at, start, end in lines:
        if axis == "x":
            draw.rectangle([at, start, at + 3, end - 1], fill=(40, 40, 40))
        else:
            draw.rectangle([start, at, end - 1, at + 3], fill=(40, 40, 40))
    return image


def test_a_seamless_sheet_is_cut_on_the_grid_and_says_it_is_not_sure():
    """Nothing on the sheet says where one photo ends: cut on the grid,
    nothing trimmed, and not clean, since wherever two photos meet there is
    a line, and background with none is one photo as likely as two."""
    assert sheet_cells(_sheet()) == (
        [
            (0, 0, 600, 768),
            (600, 0, 1200, 768),
            (0, 768, 600, 1536),
            (600, 768, 1200, 1536),
            (1200, 0, 1880, 1536),
            (1880, 0, 2560, 1536),
        ],
        False,
    )


def test_cuts_follow_dividers_drawn_off_the_grid():
    """MOSS draws its dividers up to ~50 px off the layout's cells."""
    image = _sheet(
        [
            ("x", 630, 0, 1536),  # passport column, 30 px right
            ("x", 1180, 0, 1536),  # passport | body, 20 px left
            ("x", 1900, 0, 1536),  # body | body, 20 px right
            ("y", 790, 0, 1180),  # passport row, 22 px down
        ]
    )
    cells, clean = sheet_cells(image)
    # Trimmed to the 4 px line's own edges and 2 px of seam, no more.
    assert (cells[0][2], cells[1][0]) == (630 - 2, 634 + 2)
    assert (cells[0][3], cells[2][1]) == (790 - 2, 794 + 2)
    assert (cells[1][3], cells[3][1]) == (790 - 2, 794 + 2)
    assert (cells[1][2], cells[4][0]) == (1180 - 2, 1184 + 2)
    assert (cells[4][2], cells[5][0]) == (1900 - 2, 1904 + 2)
    assert clean
    # No panel keeps a sliver of a divider.
    for left, top, right, bottom in cells:
        assert np.asarray(image.crop((left, top, right, bottom))).min() > 100


def test_each_passport_row_is_cut_on_its_own_column_gutter():
    """MiniMax H3 Image draws the two passport rows with their gutters apart
    (measured: 1232 against 1197 on a 5280 wide sheet). One cut down the whole
    height left a strip of the next photo on one row."""
    image = _sheet(
        [
            ("x", 630, 0, 768),  # top row's gutter, 30 px right
            ("x", 560, 772, 1536),  # bottom row's, 40 px left
            ("y", 768, 0, 1200),  # passport row
            ("x", 1200, 0, 1536),  # passport | body
            ("x", 1880, 0, 1536),  # body | body
        ]
    )
    cells, clean = sheet_cells(image)
    assert (cells[0][2], cells[1][0]) == (630 - 2, 634 + 2)
    assert (cells[2][2], cells[3][0]) == (560 - 2, 564 + 2)
    assert clean
    for left, top, right, bottom in cells:
        assert np.asarray(image.crop((left, top, right, bottom))).min() > 100


def test_cuts_follow_an_edit_model_s_dividers_further_off_the_grid():
    """Qwen-Image and FLUX.2 Klein, drawing from a reference picture, put
    their dividers up to ~190 px off the layout's cells (measured on real
    sheets): the cut must not fall back to the grid line through a photo."""
    image = _sheet(
        [
            ("x", 523, 0, 1536),  # passport column, 77 px left
            ("x", 1064, 0, 1536),  # passport | body, 136 px left
            ("x", 1689, 0, 1536),  # body | body, 191 px left
            ("y", 700, 0, 1064),  # passport row, 68 px up
        ]
    )
    cells, clean = sheet_cells(image)
    assert (cells[0][2], cells[1][0]) == (523 - 2, 527 + 2)
    assert (cells[0][3], cells[2][1]) == (700 - 2, 704 + 2)
    assert (cells[1][2], cells[4][0]) == (1064 - 2, 1068 + 2)
    assert (cells[4][2], cells[5][0]) == (1689 - 2, 1693 + 2)
    assert clean


def test_the_nearest_divider_wins_over_a_stronger_one_further_off():
    """A sheet that broke its layout can show a second seam in a cut's
    window; the gutter nearest the grid line is the one the cut belongs in."""
    image = _sheet(
        [
            ("x", 1180, 0, 1300),  # the gutter, over 85% of the height
            ("x", 1350, 0, 1536),  # a full-height seam 150 px off
        ]
    )
    assert sheet_cells(image)[0][4][0] == 1184 + 2


def test_a_cell_that_is_all_frame_is_kept_rather_than_inverted():
    """Two dividers ~200 px apart leave a passport cell narrower than the
    frame trim; on white it must still cut, not crash the crop."""
    image = Image.new("RGB", (SHEET_WIDTH, SHEET_HEIGHT), (252, 252, 252))
    draw = ImageDraw.Draw(image)
    for at in (800, 1000):
        draw.rectangle([at, 0, at + 3, SHEET_HEIGHT - 1], fill=(40, 40, 40))
    for left, top, right, bottom in sheet_cells(image)[0]:
        assert left < right and top < bottom
    assert len(character_sheet.split_sheet(image)) == 6


def test_a_person_s_outline_is_not_a_divider():
    """An edge that does not run the cut's whole length leaves the cut where it is."""
    image = _sheet([("x", 1220, 300, 900)])
    assert sheet_cells(image)[0][4][0] == 1200


def test_the_white_frame_and_rounded_corners_are_cut_away():
    """MOSS frames each photo in white, wider than the cut's gutter, and rounds
    its corners; an overcast sky at a photo's edge is not frame."""
    image = Image.new("RGB", (SHEET_WIDTH, SHEET_HEIGHT), (252, 252, 252))
    draw = ImageDraw.Draw(image)
    photos = [
        (16, 16, 584, 752),
        (616, 16, 1184, 752),
        (16, 784, 584, 1520),
        (616, 784, 1184, 1520),
        (1216, 16, 1864, 1520),
        (1896, 16, 2544, 1520),
    ]
    for left, top, right, bottom in photos:
        draw.rounded_rectangle([left, top, right - 1, bottom - 1], 40, (90, 90, 90))
    # Panel 4 (the beach) has an overcast sky along its top.
    draw.rectangle([640, 784, 1160, 900], fill=(237, 237, 237))
    cells, _clean = sheet_cells(image)
    gray = np.asarray(image.convert("L"))
    for (left, top, right, bottom), photo in zip(cells, photos):
        panel = gray[top:bottom, left:right]
        assert panel.max() < 244
        for edge in (panel[0], panel[-1], panel[:, 0], panel[:, -1]):
            assert (edge >= 244).sum() == 0
        # Not cut deep into the photo: past the frame and the corners, no more.
        assert all(abs(a - b) <= 20 for a, b in zip((left, top, right, bottom), photo))
    # The sky stays: panel 4 starts at its photo's top, past only the corners.
    assert cells[3][1] - photos[3][1] <= 20


def test_a_smaller_sheet_is_cut_in_proportion():
    cells, _clean = sheet_cells(_sheet(size=(1280, 768)))
    assert cells[4] == (600, 0, 940, 768)
    assert len(character_sheet.split_sheet(_sheet(size=(1280, 768)))) == 6


def _photos(bands=(), seed=0) -> Image.Image:
    """A sheet of "photos": smooth seeded texture, where no line is the same
    all along, with flat *bands* over it: ("x", start, end, level) a column
    band, ("y", start, end, level, left, right) a row band over left..right.

    The texture is turned 7 degrees: smoothed on a square grid, every row
    would change fastest on the same columns, a grain no photo has and the
    seam search rightly reads as seams."""
    rng = np.random.default_rng(seed)
    side = 3200
    coarse = rng.integers(40, 220, (side // 24, side // 24))
    texture = (
        Image.fromarray(coarse.astype(np.uint8))
        .resize((side, side), Image.BICUBIC)
        .rotate(7, resample=Image.BICUBIC)
    )
    left, top = (side - SHEET_WIDTH) // 2, (side - SHEET_HEIGHT) // 2
    texture = texture.crop((left, top, left + SHEET_WIDTH, top + SHEET_HEIGHT))
    pixels = np.asarray(texture.convert("RGB")).copy()
    for band in bands:
        if band[0] == "x":
            _, start, end, level = band
            pixels[:, start:end] = level
        else:
            _, start, end, level, left, right = band
            pixels[start:end, left:right] = level
    return Image.fromarray(pixels)


def _uneven(pixels: np.ndarray) -> bool:
    """A strip of the sheet is photo, not a flat band."""
    return np.abs(pixels - np.median(pixels)).mean() > 12


def test_a_straight_edge_inside_a_photo_is_not_the_gutter():
    """A door frame runs the full height of a body photo: a step across it,
    nearer the grid line than the gutter, but no line along it is even."""
    image = _photos([("x", 1260, 1290, 250)])
    pixels = np.asarray(image).copy()
    pixels[:, 1215:1260] = np.clip(pixels[:, 1215:1260].astype(int) + 90, 0, 255)
    cells, _clean = sheet_cells(Image.fromarray(pixels.astype(np.uint8)))
    assert (cells[1][2], cells[4][0]) == (1260 - 2, 1290 + 2)


def test_a_dark_gutter_off_the_grid_leaves_no_sliver_on_either_photo():
    """A 30 px gutter 150 px off the grid is trimmed to its own edges: a
    fixed trim leaves a strip of it on one photo, and only white is frame."""
    image = _photos([("x", 1050, 1080, 30)])
    cells, _clean = sheet_cells(image)
    gray = np.asarray(image.convert("L"), dtype=np.int16)
    passport, body = cells[1], cells[4]
    assert (passport[2], body[0]) == (1050 - 2, 1080 + 2)
    for left, top, right, bottom in (passport, body):
        for edge in (gray[top:bottom, left], gray[top:bottom, right - 1]):
            assert _uneven(edge)


def test_a_gutter_far_off_the_grid_is_still_found():
    """No model's drift is a constant: a gutter 260 px off the grid line is
    found anywhere up to halfway to the next one."""
    image = _photos([("x", 940, 960, 250)])
    cells, _clean = sheet_cells(image)
    assert (cells[1][2], cells[4][0]) == (940 - 2, 960 + 2)


def test_each_passport_column_is_cut_on_its_own_row_gutter():
    """A model can draw the two passport columns' rows 120 px apart; one row
    cut shared by both cuts one column through a head."""
    image = _photos(
        [
            ("x", 590, 610, 250),
            ("x", 1190, 1210, 250),
            ("x", 1870, 1890, 250),
            ("y", 700, 720, 250, 0, 590),
            ("y", 820, 840, 250, 610, 1190),
        ]
    )
    cells, clean = sheet_cells(image)
    assert (cells[0][3], cells[2][1]) == (700 - 2, 720 + 2)
    assert (cells[1][3], cells[3][1]) == (820 - 2, 840 + 2)
    assert clean


def _studio(people) -> Image.Image:
    """A seamless studio grey with a textured ellipse per person, (cx, rx)."""
    image = Image.new("RGB", (SHEET_WIDTH, SHEET_HEIGHT), (200, 200, 200))
    texture = _photos()
    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)
    for cx, rx in people:
        draw.ellipse([cx - rx, 150, cx + rx, 1450], fill=255)
    image.paste(texture, (0, 0), mask)
    return image


def test_on_a_seamless_sheet_the_cut_lands_in_the_background_between_people():
    """No gutter at all, and the grid line 1880 grazes the second person: the
    cut moves into the grey between them, and none of it is trimmed. Grey
    with no line in it is no evidence of two photos, so it is not clean."""
    cells, clean = sheet_cells(_studio([(1500, 200), (2100, 230)]))
    assert cells[4][2] == cells[5][0]
    assert 1700 < cells[4][2] < 2100 - 230
    assert clean is False


def test_photos_drawn_touching_are_cut_on_the_seam_not_through_an_arm():
    """MiniMax H3 draws its photos edge to edge, each on its own backdrop lit
    top to bottom, so no line between them is even all along; a lit arm as
    grey as the backdrop is. The cut belongs on the step between backdrops."""
    ramp = np.linspace(186, 150, SHEET_HEIGHT)[:, None]
    pixels = np.full((SHEET_HEIGHT, SHEET_WIDTH), 200.0)
    pixels[:, 1200:1840] = ramp  # the first body photo's backdrop
    pixels[:, 1840:] = ramp - 20  # the second's, a step darker
    pixels[230:, 1700:1725] = 170  # an arm, 155 px left of the seam
    pixels[:, 598:602] = pixels[766:770, :1200] = 40  # the passports' dividers
    cells, clean = sheet_cells(Image.fromarray(pixels.astype(np.uint8)))
    # Cut down the seam's middle (it lies between 1839 and 1840), 2 px of it
    # to each side.
    assert (cells[4][2], cells[5][0]) == (1839 - 2, 1839 + 2)
    assert clean


def test_a_white_gutter_wider_than_a_divider_is_cut_inside_not_on_its_edge():
    """The edge where a photo meets white is the frame's, not a seam: the cut
    stays inside the white, blurred edge and all, so the frame trim takes all
    of it and no panel keeps a strip of frame."""
    image = _photos([("x", 1150, 1230, 252), ("x", 1229, 1230, 244)])
    cells, _clean = sheet_cells(image)
    gray = np.asarray(image.convert("L"), dtype=np.int16)
    for left, top, right, bottom in (cells[1], cells[4]):
        for edge in (gray[top:bottom, left], gray[top:bottom, right - 1]):
            assert _uneven(edge)


def test_a_face_fences_the_cut_and_says_when_nothing_else_is_clear():
    """A dark line through a face looks like a divider; with the faces given
    it is never the cut, and with nothing else clear the sheet is not clean."""
    image = _photos([("x", 1180, 1184, 40)])
    assert sheet_cells(image)[0][4][0] == 1184 + 2
    face = [1080, 300, 1280, 500]
    cells, clean = sheet_cells(image, [face])
    assert abs(cells[4][0] - 1180) >= 0.2 * 200
    assert clean is False


def test_faces_across_a_whole_window_keep_the_guess_off_them():
    """Two faces fence every line the passport | body cut may take: the guess
    is the line furthest from both, not the window's first, and is unsure."""
    faces = [[x - 768, 200 - 768, x + 768, 200 + 768] for x in (900, 1400)]
    cells, clean = sheet_cells(_sheet(), faces)
    assert cells[1][2] == cells[4][0] == 1150
    assert clean is False


def test_a_row_cut_is_scaled_by_the_sheet_height():
    """A sheet of another shape: the row's seam is measured in its height."""
    image = _sheet([("y", 380, 0, 1200)], size=(2560, 768))
    cells, _clean = sheet_cells(image)
    assert (cells[0][3], cells[2][1]) == (380 - 1, 384 + 1)


def test_two_faces_in_one_panel_is_not_a_clean_cut():
    """The positive control holds, and a third person in a body photo fails it."""
    image = _sheet(
        [("x", 600, 0, 1536), ("x", 1200, 0, 1536), ("x", 1880, 0, 1536)]
        + [("y", 768, 0, 1200)]
    )
    heads = [[1450, 200, 1550, 300], [2050, 200, 2150, 300]]
    assert sheet_cells(image, heads)[1] is True
    assert sheet_cells(image, heads + [[2250, 200, 2300, 260]])[1] is False


def test_the_seam_between_a_photo_and_its_frame_is_not_a_gutter():
    """FLUX.2 Klein frames white-backed passport photos in a wide grey border:
    the soft line where white meets grey climbs from one to the other, and
    the cut belongs in the grey, not on that line."""
    image = _photos(
        [
            ("y", 590, 700, 254, 0, SHEET_WIDTH),
            ("y", 700, 701, 222, 0, SHEET_WIDTH),
            ("y", 701, 790, 200, 0, SHEET_WIDTH),
            ("y", 790, 791, 222, 0, SHEET_WIDTH),
            ("y", 791, 900, 254, 0, SHEET_WIDTH),
        ]
    )
    cells, _clean = sheet_cells(image)
    assert cells[0][3] == cells[2][1] == 768


def test_a_band_pressed_against_the_window_edge_is_not_a_sure_cut():
    """A sheet drawn at another scale puts its gutter past the window: the
    cut finds the band's near edge, and the body photos it leaves are not
    the layout's (one twice the other), so the sheet is cut by size and says
    it is not clean."""
    bands = [
        ("x", 590, 610, 250),
        ("x", 1870, 1890, 250),
        ("y", 760, 776, 250, 0, 1530),
    ]
    _cells, clean = sheet_cells(_photos(bands + [("x", 1190, 1210, 250)]))
    assert clean
    cells, clean = sheet_cells(_photos(bands + [("x", 1530, 1560, 120)]))
    assert 1520 <= cells[1][2] < 1540
    assert clean is False


# The photos on a magenta sheet, 40 px gutters: four passports, two bodies.
_ON_MAGENTA = [
    (40, 40, 620, 748),
    (660, 40, 1240, 748),
    (40, 788, 620, 1496),
    (660, 788, 1240, 1496),
    (1280, 40, 1900, 1496),
    (1940, 40, 2520, 1496),
]


def _magenta(photos=_ON_MAGENTA) -> np.ndarray:
    """A magenta sheet drifting from plum to hot pink left to right, with a
    textured photo in each of *photos*, each on its own grey."""
    ramp = np.linspace(0.0, 1.0, SHEET_WIDTH)[None, :, None]
    plum, pink = np.array([150, 0, 140]), np.array([255, 20, 150])
    pixels = np.broadcast_to(
        plum + (pink - plum) * ramp, (SHEET_HEIGHT, SHEET_WIDTH, 3)
    ).copy()
    texture = np.asarray(_photos(), dtype=np.float64)
    for index, (left, top, right, bottom) in enumerate(photos):
        grey = 0.5 * texture[top:bottom, left:right] + 60 + 12 * index
        pixels[top:bottom, left:right] = grey
    return pixels


def _gutter_left_in(image: Image.Image, cells) -> int:
    """How many gutter pixels the cut panels of *image* keep."""
    gutter = character_sheet._gutter_mask(np.asarray(image))
    return sum(int(gutter[y0:y1, x0:x1].sum()) for x0, y0, x1, y1 in cells)


def test_magenta_gutters_are_cut_away_whatever_their_shade():
    """Colour gutters drifting from plum to hot pink across the sheet: each
    panel is its photo, past the gutter and its soft edge, and no more."""
    image = Image.fromarray(_magenta().astype(np.uint8))
    cells, clean = sheet_cells(image)
    assert clean
    assert _gutter_left_in(image, cells) == 0
    for cell, photo in zip(cells, _ON_MAGENTA):
        assert all(abs(a - b) <= 3 for a, b in zip(cell, photo)), (cell, photo)


def test_a_white_frame_inside_a_magenta_gutter_is_trimmed_too():
    """A photo framed in white on a magenta sheet: the panel is the photo, not
    its frame, as the seam and size steps already cut it."""
    pixels = _magenta()
    frame = 24
    for left, top, right, bottom in _ON_MAGENTA:
        pixels[top : top + frame, left:right] = 250
        pixels[bottom - frame : bottom, left:right] = 250
        pixels[top:bottom, left : left + frame] = 250
        pixels[top:bottom, right - frame : right] = 250
    cells, clean = sheet_cells(Image.fromarray(pixels.astype(np.uint8)))
    assert clean
    for cell, (left, top, right, bottom) in zip(cells, _ON_MAGENTA):
        assert cell[0] >= left + frame and cell[1] >= top + frame, (cell, left, top)
        assert cell[2] <= right - frame and cell[3] <= bottom - frame, (
            cell,
            right,
            bottom,
        )


def test_a_pink_garment_at_a_panel_edge_stays_in_its_panel():
    """A sleeve in the gutter's own pink, reaching the photo's bottom edge:
    it is the photo's, so the panel loses no more than the gutter's edge."""
    pixels = _magenta()
    pixels[1380:1496, 200:450] = (230, 10, 150)
    cells, clean = sheet_cells(Image.fromarray(pixels.astype(np.uint8)))
    assert clean
    assert cells[2][3] >= 1496 - 3


def test_bodies_drawn_touching_on_magenta_are_cut_on_their_seam():
    """Qwen-Image draws magenta round groups of photos, not between them: the
    two bodies touch, one region between the gutters, and the cut goes down
    the seam between their backdrops."""
    photos = list(_ON_MAGENTA)
    photos[4], photos[5] = (1280, 40, 1920, 1496), (1920, 40, 2520, 1496)
    image = Image.fromarray(_magenta(photos).astype(np.uint8))
    cells, clean = sheet_cells(image)
    assert clean
    assert 1915 <= cells[4][2] < 1920 <= cells[5][0] <= 1925
    assert _gutter_left_in(image, cells) == 0


def test_one_body_photo_where_two_belong_is_split_by_size_and_not_clean():
    """FLUX.2 Klein can draw both body views as one photo, two figures on one
    backdrop (library picture 4046): no seam, so the body block is split in
    two equal halves, and the cut says it is not sure."""
    photos = list(_ON_MAGENTA)
    photos[4:] = [(1280, 40, 2520, 1496)]
    pixels = _magenta(photos)
    pixels[40:1496, 1280:2520] = 120 + 0.3 * pixels[40:1496, 1280:2520] - 36
    image = Image.fromarray(pixels.astype(np.uint8))
    cells, clean = sheet_cells(image)
    assert clean is False
    assert abs(cells[4][2] - 1900) <= 4
    assert _gutter_left_in(image, cells) == 0


def test_a_sheet_that_is_not_the_layout_is_cut_by_size_and_not_clean():
    """A sheet whose lines make no sense of the layout (the passport rows cut
    a third of the way down, one twice the other) falls to the layout's own
    cells, sized to what was found (the passport block's edge), and says the
    cut is not clean."""
    image = _sheet([("x", 1000, 0, 1536), ("y", 500, 0, 1000)])
    cells, clean = sheet_cells(image)
    assert clean is False
    assert cells == [
        (0, 0, 499, 768),
        (499, 0, 998, 768),
        (0, 768, 499, 1536),
        (499, 768, 998, 1536),
        (1006, 0, 1783, 1536),
        (1783, 0, 2560, 1536),
    ]


def test_photos_drawn_touching_are_cut_where_texture_stops():
    """MiniMax H3's passports meet in much the same grey: a knitted sweater
    at the bottom of one photo runs into the plain backdrop at the top of
    the next. Nothing steps in level, but the texture stops on one line."""
    rng = np.random.default_rng(3)
    pixels = np.full((SHEET_HEIGHT, SHEET_WIDTH), 200.0)
    knit = 200 + rng.normal(0, 12, (90, 1200))
    pixels[690:780, :1200] = knit  # each top passport's sweater, to the seam
    pixels[:, 598:602] = 40  # the passports' column divider
    pixels[:, 1198:1202] = pixels[:, 1878:1882] = 40  # the body dividers
    cells, clean = sheet_cells(Image.fromarray(pixels.clip(0, 255).astype(np.uint8)))
    assert clean
    assert 776 <= cells[0][3] < 780 <= cells[2][1] <= 784
    assert 776 <= cells[1][3] < 780 <= cells[3][1] <= 784


def test_agreement_and_closeness():
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    assert agreement([a]) is None
    assert agreement([a, a, a]) == pytest.approx(1.0)
    assert agreement([a, b]) == pytest.approx(0.0)
    assert closeness([a, a], [a]) == pytest.approx(1.0)
    assert closeness([a], []) is None
    # Against a cluster each face counts its best match, not the mean.
    assert closeness([a], [a, b]) == pytest.approx(0.5)
    assert closeness([a], [a, b], best=True) == pytest.approx(1.0)
    assert closeness([a, b], [a, b], best=True) == pytest.approx(1.0)
    assert agreement_words(None) == "faces don't hold"
    assert agreement_words(0.3) == "faces don't hold"
    assert agreement_words(0.5) == "faces agree somewhat"
    assert agreement_words(0.7) == "faces agree strongly"


# One face box per panel at 2560×1536, centred in its photo.
PANEL_BOXES = [
    [200, 250, 400, 500],
    [800, 250, 1000, 500],
    [200, 1000, 400, 1250],
    [800, 1000, 1000, 1250],
    [1440, 100, 1640, 350],
    [2120, 100, 2320, 350],
]


def test_each_face_box_lands_in_its_own_panel():
    assert [panel_of(box, SHEET_WIDTH, SHEET_HEIGHT) for box in PANEL_BOXES] == list(
        range(6)
    )
    # The same grid at another size.
    half = [[edge // 2 for edge in box] for box in PANEL_BOXES]
    assert [panel_of(box, 1280, 768) for box in half] == list(range(6))


def test_each_panel_is_scored_on_its_own():
    """The panel that drifts reads low on both counts; the rest read high."""
    person = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    stranger = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    faces = [(person, box) for box in PANEL_BOXES]
    faces[3] = (stranger, PANEL_BOXES[3])
    # A smaller second face in panel 0 is not the panel's face; a face with
    # no box is on no panel.
    faces.append((stranger, [250, 300, 300, 350]))
    faces.append((stranger, None))
    panels = panel_scores(faces, SHEET_WIDTH, SHEET_HEIGHT, [person])
    assert [p["panel"] for p in panels] == list(range(6))
    assert [p["close"] for p in panels] == [True, True, True, False, True, True]
    assert [p["agrees"] for p in panels] == [True, True, True, False, True, True]
    assert panels[0]["closeness"] == pytest.approx(1.0)
    assert panels[3]["closeness"] == pytest.approx(0.0)
    # Four of the five others are the same person.
    assert panels[0]["agreement"] == pytest.approx(0.8)
    assert panels[0]["box"] == pytest.approx(
        [200 / 2560, 250 / 1536, 400 / 2560, 500 / 1536]
    )
    # No references: agreement only. No size: nothing to place.
    alone = panel_scores(faces, SHEET_WIDTH, SHEET_HEIGHT, [])
    assert {p["closeness"] for p in alone} == {None}
    assert {p["close"] for p in alone} == {None}
    assert panel_scores(faces, None, None, [person]) == []
    # One face has no other panel to agree with.
    (only,) = panel_scores(faces[:1], SHEET_WIDTH, SHEET_HEIGHT, [])
    assert only["agreement"] is None and only["agrees"] is None


def test_ranking_measures_closeness_against_references_never_the_sheet_itself():
    """A reference face on the sheet itself is left out; close follows the threshold."""
    person = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    other = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    seen = {}

    class Db:
        def run_immediate_read_task(self, fn, *args):
            seen["args"] = args
            return (
                {
                    5: [(person.tobytes(), None)] * 6,
                    6: [(other.tobytes(), None)] * 6,
                    7: [],
                },
                [person.tobytes()],
                {5: ("PNG", None, None, None), 6: ("WEBP", None, None, None)},
            )

    vault = type("Vault", (), {"db": Db()})()
    sheets, references = character_sheet.rank_sheets(vault, 1, [5, 6, 7, 5])
    assert seen["args"] == (1, [5, 6, 7], None)
    assert references == 1
    near, far, unread = sheets
    assert (near["close"], far["close"]) == (True, False)
    assert near["closeness"] == pytest.approx(1.0)
    assert unread["faces_read"] is False and unread["agreement"] is None
    # The format the full-size picture is served as, for the zoom.
    assert (near["format"], far["format"], unread["format"]) == ("png", "webp", None)
    # Two people: each names the other; an unread sheet is compared with none.
    assert (near["different_from"], far["different_from"]) == ([6], [5])
    assert unread["different_from"] == []
    # No boxes and no size: no panel is placed.
    assert near["panels"] == [] and unread["panels"] == []
    # No file to read: the cut is not shown, rather than guessed.
    assert (near["cuts_cleanly"], near["cells"]) == (None, [])


def test_ranking_takes_the_best_match_in_the_cluster_and_the_mean_over_pictures():
    """The character's reference cluster holds two looks; a sheet like either
    is close. Against a picture, closeness stays the mean over its faces."""
    person = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    other_look = np.array([0.0, 1.0, 0.0], dtype=np.float32)

    class Db:
        def run_immediate_read_task(self, fn, *args):
            return (
                {5: [(person.tobytes(), json.dumps(PANEL_BOXES[0]))]},
                [person.tobytes(), other_look.tobytes()],
                {5: ("PNG", SHEET_WIDTH, SHEET_HEIGHT, None)},
            )

    vault = type("Vault", (), {"db": Db()})()
    (cluster,), _ = character_sheet.rank_sheets(vault, 1, [5])
    assert cluster["closeness"] == pytest.approx(1.0) and cluster["close"] is True
    assert cluster["panels"][0]["closeness"] == pytest.approx(1.0)
    (picture,), _ = character_sheet.rank_sheets(vault, 1, [5], [9])
    assert picture["closeness"] == pytest.approx(0.5)
    assert picture["panels"][0]["closeness"] == pytest.approx(0.5)


def test_ranking_names_no_difference_between_sheets_of_one_person():
    """The positive control: sheets alike across sheets are not flagged."""
    person = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    near_person = np.array([0.9, 0.3, 0.0], dtype=np.float32)

    class Db:
        def run_immediate_read_task(self, fn, *args):
            return (
                {
                    5: [(person.tobytes(), None)] * 6,
                    6: [(near_person.tobytes(), None)] * 6,
                },
                [],
                {},
            )

    vault = type("Vault", (), {"db": Db()})()
    sheets, _ = character_sheet.rank_sheets(vault, 1, [5, 6])
    assert [s["different_from"] for s in sheets] == [[], []]


def test_a_sheet_that_fails_to_import_leaves_the_earlier_panels_filed(monkeypatch):
    """Panels already imported are filed before the next sheet is tried."""
    filed = []

    class Db:
        def run_immediate_read_task(self, fn, *args):
            if fn is character_sheet.sheet_face_boxes_in_session:
                return {}
            return [Picture(id=1), Picture(id=2)]

        def run_task(self, fn, picture_ids, character_id):
            filed.append((list(picture_ids), character_id))

    def import_panels(server, sources, entries, stack):
        if sources[0].id == 2:
            raise OSError("disk full")
        return [], [], [10, 11]

    monkeypatch.setattr(
        character_sheet, "_cut", lambda server, sheet, faces, boxes=None: [b""] * 2
    )
    monkeypatch.setattr(character_sheet, "import_derived_outputs", import_panels)
    server = type("Server", (), {"vault": type("Vault", (), {"db": Db()})()})()
    with pytest.raises(OSError):
        character_sheet.use_sheets(server, 3, [1, 2])
    assert filed == [([10, 11], 3)]


def test_each_item_sheet_names_its_four_views_and_the_item_once():
    outfit = item_sheet_prompt(kind="outfit", description="A mustard raincoat.")
    assert "four photos of equal size, two by two" in outfit
    assert "(4) bottom right, back view" in outfit
    assert "The outfit is a mustard raincoat." in outfit
    # Nobody inside it, and nothing the description does not name: a jumper
    # gets no legs or shoes.
    assert "invisible mannequin" in outfit and "no head, neck, skin" in outfit
    assert "framed to the garments described" in outfit and "shoes" in outfit
    assert "soles of the shoes" not in outfit
    assert "worn by" not in outfit and "cropped at the chin" not in outfit
    location = item_sheet_prompt(kind="location", description="a pine trail")
    assert "two by two" in location and "(2) top right, the reverse view" in location
    assert "no people" in location and "Plain daylight" in location
    prop = item_sheet_prompt(kind="prop", description="a red umbrella")
    assert "a red umbrella, on its own" in prop and "no hands" in prop
    for prompt in (outfit, location, prop):
        assert prompt.endswith(ITEM_PHOTO_LOOK) and "photorealistic" not in prompt


def test_an_outfit_sheet_is_cut_two_by_two_on_its_4_5_canvas():
    width, height = item_canvas("outfit", "large")
    assert (width, height) == (1792, 2240) and width * 5 == height * 4
    image = _sheet(
        [("x", 900, 0, height), ("y", 1100, 0, 900), ("y", 1140, 900, width)],
        size=(width, height),
    )
    cells, clean = item_sheet_cells(image, "outfit")
    assert clean
    (a, b, c, d) = cells
    assert a[2] < 900 < b[0] and a[3] < 1100 < c[1] and b[3] < 1140 < d[1]
    assert (a[0], c[0], b[2], d[2], c[3], d[3]) == (0, 0, width, width, height, height)


def test_a_location_sheet_is_cut_two_by_two_each_column_on_its_own_row():
    image = _sheet(
        [
            ("x", 1300, 0, 1536),
            ("y", 740, 0, 1300),
            ("y", 800, 1300, 2560),
        ]
    )
    cells, clean = item_sheet_cells(image, "location")
    assert clean
    (a, b, c, d) = cells
    assert a[2] < 1300 < b[0] and a[3] < 740 < c[1] and b[3] < 800 < d[1]
    assert (a[0], c[0], b[2], d[2]) == (0, 0, 2560, 2560)


def test_a_seamless_item_sheet_is_cut_on_the_grid_and_not_clean():
    cells, clean = item_sheet_cells(_sheet(), "prop")
    assert not clean
    assert cells == [
        (0, 0, 1280, 768),
        (1280, 0, 2560, 768),
        (0, 768, 1280, 1536),
        (1280, 768, 2560, 1536),
    ]


def _panel(colour, backdrop=(220, 220, 220)) -> Image.Image:
    image = Image.new("RGB", (120, 200), backdrop)
    ImageDraw.Draw(image).rectangle([30, 30, 90, 170], fill=colour)
    return image


def test_a_panel_drawn_in_another_colour_drifts_from_the_other_three():
    mustard = (200, 160, 40)
    same = colour_agreement([_panel(mustard)] * 4)
    assert all(value > 0.99 for value in same)
    # The backdrop is left out: a grey studio shared by every panel does not
    # make a blue coat agree with a mustard one.
    drift = colour_agreement([_panel(mustard)] * 3 + [_panel((30, 60, 200))])
    assert drift[3] < ITEM_AGREEMENT <= min(drift[:3])
    assert item_agreement_words("outfit", drift) == "the back drifts in colour"
    assert item_agreement_words("location", same) == "the four views agree"


@pytest.fixture(scope="module")
def client():
    with tempfile.TemporaryDirectory() as temp_dir:
        with Server(f"{temp_dir}/server-config.json") as server:
            test_client = TestClient(server.api)
            response = test_client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200
            test_client.server = server
            yield test_client


def _upload_sheet(client, lines) -> int:
    buffer = io.BytesIO()
    _sheet(lines).save(buffer, format="PNG")
    status = upload_pictures_and_wait(
        client, [("file", ("sheet.png", buffer.getvalue(), "image/png"))]
    )
    assert status["status"] == "completed", status
    return status["results"][0]["picture_id"]


@pytest.fixture(scope="module")
def sheet_id(client):
    return _upload_sheet(client, [("x", 1900, 0, 1536)])


def _new_character(client, name):
    response = client.post(f"{API_PREFIX}/characters", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()["character"]["id"]


def test_prompt_route_takes_a_background(client):
    response = client.post(
        f"{API_PREFIX}/character-sheets/prompt",
        json={"description": "x", "background": "a brick wall", "layout": "prose"},
    )
    assert response.status_code == 200, response.text
    assert "with the background: a brick wall" in response.json()["prompt"]


def test_prompt_route_hands_out_the_canvas(client):
    response = client.post(
        f"{API_PREFIX}/character-sheets/prompt",
        json={"description": "an adult woman", "framing": "knees_up"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["width"], body["height"]) == (SHEET_WIDTH, SHEET_HEIGHT)
    assert (
        "cropped just above the knees"
        in json.loads(body["prompt"])["summary"]["caption"]
    )
    bad = client.post(f"{API_PREFIX}/character-sheets/prompt", json={"framing": "wide"})
    assert bad.status_code == 422
    small = client.post(
        f"{API_PREFIX}/character-sheets/prompt",
        json={"description": "x", "size": "small"},
    ).json()
    assert (small["width"], small["height"]) == (1280, 768)
    # MiniMax H3's sizes: the route takes them like any other.
    for size, canvas in (
        ("4k", (3840, 2304)),
        ("5k", (5280, 3168)),
        ("6k", (6240, 3744)),
    ):
        drawn = client.post(
            f"{API_PREFIX}/character-sheets/prompt",
            json={"description": "x", "size": size, "layout": "prose"},
        )
        assert drawn.status_code == 200, drawn.text
        assert (drawn.json()["width"], drawn.json()["height"]) == canvas
    # An outfit's sheet is 4:5, so its 2x2 panels are; a prop's is the 5:3.
    for kind, canvas in (("outfit", (2688, 3360)), ("prop", (3840, 2304))):
        drawn = client.post(
            f"{API_PREFIX}/character-sheets/prompt",
            json={"kind": kind, "description": "x", "size": "4k", "layout": "prose"},
        ).json()
        assert (drawn["width"], drawn["height"]) == canvas
    # A location's single wide picture, which More angles films the rest from.
    wide = client.post(
        f"{API_PREFIX}/character-sheets/prompt",
        json={"kind": "location", "description": "a walled garden", "single": True},
    ).json()
    assert (wide["width"], wide["height"]) == (2560, 1440)
    assert (
        "wide establishing photograph of one place, a walled garden" in wide["prompt"]
    )
    assert "four photos" not in wide["prompt"]
    for kind in ("prop", "character"):
        refused = client.post(
            f"{API_PREFIX}/character-sheets/prompt",
            json={"kind": kind, "description": "x", "single": True},
        )
        assert refused.status_code == 422, (kind, refused.text)
    prose = client.post(
        f"{API_PREFIX}/character-sheets/prompt",
        json={"description": "x", "reference": True, "layout": "prose"},
    ).json()["prompt"]
    assert "the same person as in the reference picture, x" in prose
    assert not prose.startswith("{")
    little = client.post(
        f"{API_PREFIX}/character-sheets/prompt",
        json={"description": "x", "proportions": "little_person", "layout": "prose"},
    ).json()["prompt"]
    assert "a little person with dwarfism" in little
    assert little.endswith(character_sheet.PHOTO_LOOK)
    plain = client.post(
        f"{API_PREFIX}/character-sheets/prompt",
        json={"description": "x", "layout": "prose", "photo_look": False},
    ).json()["prompt"]
    assert "digitals" not in plain
    by_run = client.post(
        f"{API_PREFIX}/character-sheets/prompt",
        json={"description": "x", "layout": "prose", "look_added_by_run": True},
    ).json()["prompt"]
    assert "digitals" not in by_run and "photorealistic" not in by_run
    giant = client.post(
        f"{API_PREFIX}/character-sheets/prompt",
        json={"description": "x", "proportions": "giant"},
    )
    assert giant.status_code == 422


def test_a_workflow_llm_rewrites_a_prompt_through_generate_text(client, monkeypatch):
    sent = {}

    def submit(base_url, graph, client_id=None):
        sent["graph"] = graph
        return {"prompt_id": "p1"}

    answers = iter(
        [
            {},  # still queued
            {
                "p1": {
                    "status": {"status_str": "success"},
                    "outputs": {"3": {"text": ["  A richer pub.  "]}},
                }
            },
        ]
    )
    monkeypatch.setattr(prompt_llm, "_submit_comfyui_prompt", submit)
    monkeypatch.setattr(prompt_llm, "_fetch_comfyui_history", lambda *_: next(answers))
    monkeypatch.setattr(prompt_llm, "POLL_SECONDS", 0)
    # The route reads ComfyUI's node map to load off the shelf as a run does.
    monkeypatch.setattr(
        workflows_routes, "_read_object_info", lambda *_a, **_k: ({}, None)
    )
    loader = {
        "class_type": "CLIPLoader",
        # A number as the node holds it is taken, and dropped with the rest.
        "inputs": {
            "clip_name": "qwen3vl.safetensors",
            "type": "minimax",
            "evil": "x",
            "strength": 1.0,
        },
    }
    resp = client.post(
        f"{API_PREFIX}/item-sheets/prompt/embellish",
        json={"loader": loader, "prompt": "a pub"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"prompt": "A richer pub."}
    graph = sent["graph"]
    # Only the loader's own inputs travel; the words go to Generate Text with
    # the rewrite instruction as its system prompt, read back from a preview.
    assert graph["1"] == {
        "class_type": "CLIPLoader",
        "inputs": {"clip_name": "qwen3vl.safetensors", "type": "minimax"},
    }
    assert graph["2"]["class_type"] == "TextGenerateV2"
    assert graph["2"]["inputs"]["clip"] == ["1", 0]
    assert graph["2"]["inputs"]["prompt"] == "a pub"
    assert graph["2"]["inputs"]["system_prompt"] == prompt_llm.EMBELLISH_INSTRUCTION
    assert graph["3"] == {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}}

    for bad in (
        {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x"}},
        {"class_type": "CLIPLoader", "inputs": {"type": "minimax"}},
    ):
        refused = client.post(
            f"{API_PREFIX}/item-sheets/prompt/embellish",
            json={"loader": bad, "prompt": "a pub"},
        )
        assert refused.status_code == 409, (bad, refused.text)


def test_embellish_prepares_the_graph_before_it_is_sent(monkeypatch):
    order = []

    def submit(_base_url, graph, client_id=None):
        order.append(("submit", graph["1"]["class_type"]))
        return {"prompt_id": "p2"}

    def prepare(graph):
        order.append(("prepare", graph["1"]["class_type"]))
        graph["1"]["class_type"] = "PixlStashClipLoader"

    monkeypatch.setattr(prompt_llm, "_submit_comfyui_prompt", submit)
    monkeypatch.setattr(
        prompt_llm,
        "_fetch_comfyui_history",
        lambda *_: {
            "p2": {
                "status": {"status_str": "success"},
                "outputs": {"3": {"text": ["ok"]}},
            }
        },
    )
    loader = {
        "class_type": "CLIPLoader",
        "inputs": {"clip_name": "q.safetensors", "type": "minimax"},
    }
    assert prompt_llm.embellish("http://x", loader, "a pub", prepare=prepare) == "ok"
    # The shelf's loader is what ComfyUI is sent.
    assert order == [("prepare", "CLIPLoader"), ("submit", "PixlStashClipLoader")]


def test_using_a_sheet_files_six_panels_on_the_character(client, sheet_id):
    character_id = _new_character(client, "Sheet Mira")
    response = client.post(
        f"{API_PREFIX}/characters/{character_id}/sheets/use",
        json={"picture_ids": [sheet_id]},
    )
    assert response.status_code == 200, response.text
    panel_ids = response.json()["picture_ids"]
    assert len(panel_ids) == 6 and sheet_id not in panel_ids

    def read(session):
        pictures = session.exec(select(Picture).where(Picture.id.in_(panel_ids))).all()
        faces = session.exec(
            select(Face.picture_id).where(
                Face.picture_id.in_(panel_ids), Face.character_id == character_id
            )
        ).all()
        return {p.id: p for p in pictures}, set(faces)

    pictures, with_face = client.server.vault.db.run_task(read)
    sizes = [(pictures[i].width, pictures[i].height) for i in panel_ids]
    # The body | body divider drawn at 1900 moved that cut 20 px right, and
    # only it was trimmed: the 4 px line and 2 px of seam either side.
    assert sizes[:4] == [(600, 768)] * 4
    assert sizes[4:] == [(698, 1536), (654, 1536)]
    for picture_id in panel_ids:
        picture = pictures[picture_id]
        # No link back to the sheet, which stays in the Scrapheap to be purged.
        assert picture.source_picture_id is None
        # Filed on the character: its face now, or its largest face once read.
        assert (
            picture_id in with_face or picture.pending_character_id == character_id
        ), picture_id


def test_using_a_sheet_refuses_unknown_ids(client, sheet_id):
    character_id = _new_character(client, "Sheet Kai")
    missing_character = client.post(
        f"{API_PREFIX}/characters/999999/sheets/use", json={"picture_ids": [sheet_id]}
    )
    assert missing_character.status_code == 404
    missing_picture = client.post(
        f"{API_PREFIX}/characters/{character_id}/sheets/use",
        json={"picture_ids": [sheet_id, 999999]},
    )
    assert missing_picture.status_code == 404


_HAND_BOXES = [
    [0, 0, 0.25, 0.5],
    [0.25, 0, 0.5, 0.5],
    [0, 0.5, 0.25, 1],
    [0.25, 0.5, 0.5, 1],
    [0.5, 0, 0.8, 1],
    [0.8, 0, 1, 1],
]
# The six photos a 2560x1536 sheet cut at _HAND_BOXES is, in pixels.
_HAND_SIZES = [(640, 768)] * 4 + [(768, 1536), (512, 1536)]
# The same sheet cut automatically: the 1900 divider trimmed, as the use test
# above asserts for it.
_AUTO_SIZES = [(600, 768)] * 4 + [(698, 1536), (654, 1536)]


def _panel_sizes(client, panel_ids):
    def read(session):
        return {
            p.id: p
            for p in session.exec(select(Picture).where(Picture.id.in_(panel_ids)))
        }

    pictures = client.server.vault.db.run_task(read)
    return [(pictures[i].width, pictures[i].height) for i in panel_ids]


def test_a_sheet_is_cut_before_its_faces_are_read(tmp_path, monkeypatch):
    """The cut needs no faces, so a sheet whose faces are unread already has
    its six cells, the same as its cut once read."""
    _sheet([("x", 1900, 0, 1536)]).save(tmp_path / "sheet.png", format="PNG")
    picture = ("png", SHEET_WIDTH, SHEET_HEIGHT, "sheet.png")
    monkeypatch.setattr(
        character_sheet,
        "sheet_faces_in_session",
        lambda character_id, ids, refs=None: (
            {pid: [] for pid in ids},
            [],
            {pid: picture for pid in ids},
        ),
    )
    vault = SimpleNamespace(
        image_root=str(tmp_path),
        db=SimpleNamespace(run_immediate_read_task=lambda fn, *args: fn(*args)),
    )
    [unread], _ = character_sheet.rank_sheets(vault, 1, [5])
    assert unread["faces_read"] is False
    expected, _ = sheet_cells(_sheet([("x", 1900, 0, 1536)]))
    assert len(unread["cells"]) == 6
    for got, box in zip(unread["cells"], expected):
        assert got == pytest.approx(
            [
                box[0] / SHEET_WIDTH,
                box[1] / SHEET_HEIGHT,
                box[2] / SHEET_WIDTH,
                box[3] / SHEET_HEIGHT,
            ]
        )
    assert unread["cuts_cleanly"] is False


def test_a_sheet_cut_by_hand_is_cut_at_its_boxes_exactly(client):
    sheet = _upload_sheet(client, [("x", 1900, 0, 1536)])
    character_id = _new_character(client, "Sheet Hand")
    response = client.post(
        f"{API_PREFIX}/characters/{character_id}/sheets/use",
        json={"picture_ids": [sheet], "boxes": {str(sheet): _HAND_BOXES}},
    )
    assert response.status_code == 200, response.text
    assert _panel_sizes(client, response.json()["picture_ids"]) == _HAND_SIZES


def test_a_sheet_without_boxes_is_still_cut_automatically(client, sheet_id):
    # Identical uploads are one picture, so the hand-cut one differs in content.
    edited = _upload_sheet(client, [("x", 1902, 0, 1536)])
    automatic = sheet_id
    character_id = _new_character(client, "Sheet Mixed")
    response = client.post(
        f"{API_PREFIX}/characters/{character_id}/sheets/use",
        json={
            "picture_ids": [edited, automatic],
            "boxes": {str(edited): _HAND_BOXES},
        },
    )
    assert response.status_code == 200, response.text
    panel_ids = response.json()["picture_ids"]
    assert len(panel_ids) == 12
    assert _panel_sizes(client, panel_ids) == _HAND_SIZES + _AUTO_SIZES


@pytest.mark.parametrize(
    "boxes",
    [
        pytest.param(lambda b: b[:5], id="five-boxes"),
        pytest.param(lambda b: [[0, 0, 1.2, 0.5]] + b[1:], id="past-the-sheet"),
        pytest.param(lambda b: [[0.5, 0, 0.25, 0.5]] + b[1:], id="inverted"),
        pytest.param(lambda b: [[0, 0, 0.005, 0.5]] + b[1:], id="under-32-px"),
        pytest.param(lambda b: [[0, 0, 0.5]] + b[1:], id="three-numbers"),
    ],
)
def test_hand_boxes_that_are_not_six_photos_on_the_sheet_are_refused(client, boxes):
    sheet = _upload_sheet(client, [("x", 1900, 0, 1536)])
    character_id = _new_character(client, "Sheet Refused")
    response = client.post(
        f"{API_PREFIX}/characters/{character_id}/sheets/use",
        json={"picture_ids": [sheet], "boxes": {str(sheet): boxes(_HAND_BOXES)}},
    )
    assert response.status_code == 422, response.text


def test_a_hand_box_that_is_not_a_number_is_refused(client):
    sheet = _upload_sheet(client, [("x", 1900, 0, 1536)])
    character_id = _new_character(client, "Sheet NaN")
    # The test client cannot encode NaN, so the body is written out by hand.
    rest = json.dumps(_HAND_BOXES[1:])[1:-1]
    body = '{"picture_ids": [%d], "boxes": {"%d": [[0, 0, NaN, 0.5], %s]}}' % (
        sheet,
        sheet,
        rest,
    )
    response = client.post(
        f"{API_PREFIX}/characters/{character_id}/sheets/use",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text


def test_hand_boxes_for_a_picture_not_among_the_sheets_are_refused(client):
    sheet = _upload_sheet(client, [("x", 1900, 0, 1536)])
    other = _upload_sheet(client, [("x", 1904, 0, 1536)])
    character_id = _new_character(client, "Sheet Foreign")
    response = client.post(
        f"{API_PREFIX}/characters/{character_id}/sheets/use",
        json={"picture_ids": [sheet], "boxes": {str(other): _HAND_BOXES}},
    )
    assert response.status_code == 422, response.text
    # Nothing was cut: the foreign key is refused before any panel is imported.
    assert "not among" in response.json()["detail"]


def _library_uuid(server):
    lease = server.library_coordinator.acquire_read()
    try:
        return lease.library_uuid
    finally:
        server.library_coordinator.release_read(lease)


def _watch_events(server, monkeypatch):
    events = []
    real_notify = server.vault.notify

    def notify(kind, payload=None):
        events.append((kind, payload))
        return real_notify(kind, payload)

    monkeypatch.setattr(server.vault, "notify", notify)
    return events


def _state(server, ids):
    def read(session):
        return {
            p.id: p for p in session.exec(select(Picture).where(Picture.id.in_(ids)))
        }

    return server.vault.db.run_task(read)


def test_a_held_run_never_holds_a_picture_it_did_not_import(client, monkeypatch):
    """A saver node reports ids of its own: they can be pictures the library
    already had, so a held run leaves them live."""
    server = client.server
    live = _upload_sheet(client, [("x", 1930, 0, 1536)])
    events = _watch_events(server, monkeypatch)
    monkeypatch.setattr(
        comfyui_service, "_wait_for_comfyui_outputs", lambda *a, **k: ([], [live])
    )
    comfyui_service._process_comfyui_outputs(
        server,
        "http://comfy",
        "held-saver-run",
        None,
        None,
        None,
        hold=True,
        origin_library_uuid=_library_uuid(server),
    )
    assert not _state(server, [live])[live].deleted
    # Left live, so announced like any run's picture.
    imported = [e for e in events if e[0] == EventType.PICTURE_IMPORTED]
    assert [e[1]["ids"] for e in imported] == [[live]]


def test_a_used_sheet_stays_in_the_scrapheap_and_purges_cleanly(client, monkeypatch):
    """A run drawn with ``hold`` soft-deletes what it imports and announces
    none; the sheet can still be cut, stays in the Scrapheap afterwards, and
    the Scrapheap's own purge removes it without touching its panels."""
    server = client.server
    drawn = io.BytesIO()
    _sheet([("x", 1910, 0, 1536)]).save(drawn, format="PNG")
    events = _watch_events(server, monkeypatch)
    monkeypatch.setattr(
        comfyui_service,
        "_wait_for_comfyui_outputs",
        lambda *a, **k: ([{"filename": "sheet.png"}], None),
    )
    monkeypatch.setattr(
        comfyui_service,
        "_download_comfyui_image",
        lambda *a, **k: (drawn.getvalue(), ".png"),
    )
    comfyui_service._process_comfyui_outputs(
        server,
        "http://comfy",
        "held-run",
        None,
        None,
        None,
        hold=True,
        origin_library_uuid=_library_uuid(server),
    )
    [held] = comfyui_service.run_outcome("held-run")["picture_ids"]
    assert not [e for e in events if e[0] == EventType.PICTURE_IMPORTED]
    assert _state(server, [held])[held].deleted

    character_id = _new_character(client, "Sheet Held")
    used = client.post(
        f"{API_PREFIX}/characters/{character_id}/sheets/use",
        json={"picture_ids": [held]},
    )
    assert used.status_code == 200, used.text
    panels = used.json()["picture_ids"]
    assert len(panels) == 6
    assert _state(server, [held])[held].deleted

    # Retention or Empty Scrapheap: a panel still naming its sheet as source
    # would fail the purge on the foreign key.
    purged = scrapheap_service.purge_scrapheap_pictures(
        server.vault, [held], include_protected=False
    )
    assert list(purged.purged_ids) == [held]
    left = _state(server, [held, *panels])
    assert held not in left
    assert all(not left[pid].deleted for pid in panels)


def test_ranking_scores_a_sheet_by_its_own_faces(client, sheet_id):
    character_id = _new_character(client, "Sheet Anna")
    url = f"{API_PREFIX}/characters/{character_id}/sheets/rank"
    # Face extraction leaves a row on every picture it read, a face or not.
    deadline = time.time() + 120
    while True:
        response = client.post(url, json={"picture_ids": [sheet_id]})
        assert response.status_code == 200, response.text
        if response.json()["sheets"][0]["faces_read"] or time.time() > deadline:
            break
        time.sleep(0.5)
    assert response.json()["sheets"][0]["faces_read"]
    # Read, the sheet shows the cut using it makes: the 1900 divider trimmed.
    # Only that cut found a line; the rest is grey both sides share, which
    # nothing says is two photos, so the sheet may not cut cleanly.
    read = response.json()["sheets"][0]
    assert read["cuts_cleanly"] is False
    expected = [
        (0, 0, 600, 768),
        (600, 0, 1200, 768),
        (0, 768, 600, 1536),
        (600, 768, 1200, 1536),
        (1200, 0, 1898, 1536),
        (1906, 0, 2560, 1536),
    ]
    assert read["cells"] == [
        pytest.approx([left / 2560, top / 1536, right / 2560, bottom / 1536])
        for left, top, right, bottom in expected
    ]

    rng = np.random.default_rng(7)
    person = rng.normal(size=512).astype(np.float32)

    # Stamped with the server's pack: a hand-written embedding without one
    # reads as stale, and the pack-refresh sweep re-detects the picture and
    # replaces these with what it really finds (nothing, on a drawn sheet).
    def add_faces(session):
        for index in range(7):
            vector = person + rng.normal(scale=0.3, size=512).astype(np.float32)
            session.add(
                Face(
                    picture_id=sheet_id,
                    face_index=100 + index,
                    features=vector.astype(np.float32).tobytes(),
                    model_pack=DEFAULT_MODEL_PACK,
                    bbox=PANEL_BOXES[index % 6],
                )
            )
        session.commit()

    client.server.vault.db.run_task(add_faces)
    response = client.post(url, json={"picture_ids": [sheet_id, sheet_id]})
    assert response.status_code == 200, response.text
    body = response.json()
    # The sheet's own faces are never its references.
    assert body["references"] == 0
    (sheet,) = body["sheets"]
    assert sheet["faces"] == 7
    assert sheet["agreement"] > 0.8
    assert sheet["agreement_words"] == "faces agree strongly"
    assert sheet["closeness"] is None and sheet["close"] is None
    # Seven faces on a six-panel sheet: one panel holds two people.
    assert sheet["broken"] is True
    # ...so its cut is not clean either, though it is still drawn.
    assert sheet["cuts_cleanly"] is False and len(sheet["cells"]) == 6
    # Each panel scored on its own, placed where its face is.
    assert [p["panel"] for p in sheet["panels"]] == list(range(6))
    assert all(p["agrees"] for p in sheet["panels"])
    assert sheet["panels"][4]["box"] == pytest.approx(
        [1440 / 2560, 100 / 1536, 1640 / 2560, 350 / 1536]
    )

    missing = client.post(
        f"{API_PREFIX}/characters/999999/sheets/rank", json={"picture_ids": [sheet_id]}
    )
    assert missing.status_code == 404

    # #2679: drawn from a picture, the sheet is measured against that picture's
    # faces, whoever's they are; an id with no faces counts for nothing.
    reference_id = _upload_sheet(client, [(600, 0, 600, 1536)])
    deadline = time.time() + 120
    while time.time() < deadline:
        read = client.post(url, json={"picture_ids": [reference_id]})
        if read.json()["sheets"][0]["faces_read"]:
            break
        time.sleep(0.5)

    def add_reference(session):
        session.add(
            Face(
                picture_id=reference_id,
                face_index=100,
                features=person.tobytes(),
                model_pack=DEFAULT_MODEL_PACK,
            )
        )
        session.commit()

    client.server.vault.db.run_task(add_reference)
    response = client.post(
        url,
        json={
            "picture_ids": [sheet_id],
            "reference_picture_ids": [reference_id, 999999],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["references"] == 1
    assert body["sheets"][0]["closeness"] > 0.8
    assert body["sheets"][0]["close"] is True
    assert all(p["close"] for p in body["sheets"][0]["panels"])
    # The sheet is never its own reference, named or not.
    own = client.post(
        url, json={"picture_ids": [sheet_id], "reference_picture_ids": [sheet_id]}
    )
    assert own.json()["references"] == 0
    too_many = client.post(
        url, json={"picture_ids": [sheet_id], "reference_picture_ids": [1, 2, 3, 4, 5]}
    )
    assert too_many.status_code == 422


def test_a_reference_cluster_chooses_the_reference_faces(client):
    """Face clusters and the reference cluster, end to end (§25.30)."""
    character_id = _new_character(client, "Cluster Ida")
    files = []
    for n in range(9):
        buffer = io.BytesIO()
        _sheet([("x", 40 + 37 * n, 0, 300)], size=(640, 384)).save(buffer, "PNG")
        files.append(("file", (f"face{n}.png", buffer.getvalue(), "image/png")))
    status = upload_pictures_and_wait(client, files)
    assert status["status"] == "completed", status
    ids = [r["picture_id"] for r in status["results"]]
    assert len(set(ids)) == 9
    # Let face extraction read them first, so it never overwrites these rows.
    url = f"{API_PREFIX}/characters/{character_id}/sheets/rank"
    deadline = time.time() + 120
    while time.time() < deadline:
        read = client.post(url, json={"picture_ids": ids}).json()["sheets"]
        if all(sheet["faces_read"] for sheet in read):
            break
        time.sleep(0.5)

    rng = np.random.default_rng(23)
    a, b = rng.normal(size=512), rng.normal(size=512)
    a_ids, b_ids, plain = ids[:4], ids[4:8], ids[8]
    b_centre = b_ids[2]

    def add_faces(session):
        for pid in ids:
            person = a if pid in a_ids else b
            vec = person if pid == b_centre else person + rng.normal(size=512) * 0.6
            if pid == plain:
                vec = rng.normal(size=512)
            session.add(
                Face(
                    picture_id=pid,
                    face_index=100,
                    character_id=character_id,
                    features=vec.astype(np.float32).tobytes(),
                    model_pack=DEFAULT_MODEL_PACK,
                )
            )
            # A is the rated identity: today's rule would pick it.
            session.get(Picture, pid).score = 5 if pid in a_ids else None
        session.commit()

    client.server.vault.db.run_task(add_faces)
    refs_url = f"{API_PREFIX}/characters/{character_id}/reference_pictures"
    assert set(client.get(refs_url).json()["reference_picture_ids"][:4]) == set(a_ids)

    clusters_url = f"{API_PREFIX}/face-clusters?character_id={character_id}"
    body = client.get(clusters_url).json()
    assert body["reference_cluster"] is None
    assert body["faces"] == 9
    assert sorted(
        sorted(m["picture_id"] for m in c["members"]) for c in body["clusters"]
    ) == sorted([sorted(a_ids), sorted(b_ids)])
    assert [u["picture_id"] for u in body["unclustered"]] == [plain]
    # The picker keeps videos out of "Matched no one" by this.
    plain_format = client.server.vault.db.run_task(
        lambda session: session.get(Picture, plain).format
    )
    assert plain_format and body["unclustered"][0]["format"] == plain_format
    assert not any(c["is_reference"] for c in body["clusters"])
    # Each member carries its shape, so the view can keep its aspect ratio.
    member = body["clusters"][0]["members"][0]
    assert (member["width"], member["height"]) == (640, 384)
    assert body["centred"] is None

    # Centred on one face of B: every face, ranked by likeness to it.
    centred = client.get(f"{clusters_url}&centre={b_centre}").json()["centred"]
    assert sorted(m["picture_id"] for m in centred) == sorted(ids)
    assert centred[0]["picture_id"] == b_centre and centred[0]["centrality"] == 1.0
    values = [m["centrality"] for m in centred]
    assert values == sorted(values, reverse=True)
    order = [m["picture_id"] for m in centred]
    assert max(order.index(pid) for pid in b_ids) < min(
        order.index(pid) for pid in a_ids
    )
    assert centred[0]["width"] == 640
    missing = client.get(f"{clusters_url}&centre=999999")
    assert missing.status_code == 422, missing.text

    # #2738: seeded from picked pictures. Two more pictures, neither hers: one
    # with an unassigned look-alike of B, one with another person's B-like face.
    files = []
    for n in range(2):
        buffer = io.BytesIO()
        _sheet([("x", 60 + 50 * n, 0, 300)], size=(640, 384)).save(buffer, "PNG")
        files.append(("file", (f"seedextra{n}.png", buffer.getvalue(), "image/png")))
    status = upload_pictures_and_wait(client, files)
    assert status["status"] == "completed", status
    look_alike, others = (r["picture_id"] for r in status["results"])
    deadline = time.time() + 120
    while time.time() < deadline:
        read = client.post(url, json={"picture_ids": [look_alike, others]}).json()
        if all(sheet["faces_read"] for sheet in read["sheets"]):
            break
        time.sleep(0.5)
    other_id = _new_character(client, "Cluster Ida's double")

    def add_look_alikes(session):
        for pid, character, owner in (
            (look_alike, None, look_alike),
            (others, other_id, others),
            # Beside her own face: already ranked, never "probably" too.
            (b_ids[0], None, b_ids[0]),
        ):
            session.add(
                Face(
                    picture_id=owner,
                    face_index=101,
                    character_id=character,
                    features=(b + rng.normal(size=512) * 0.3)
                    .astype(np.float32)
                    .tobytes(),
                    model_pack=DEFAULT_MODEL_PACK,
                )
            )
        session.commit()

    client.server.vault.db.run_task(add_look_alikes)
    seeds = [b_ids[0], b_ids[1], others]
    seeded_body = client.get(
        clusters_url + "".join(f"&seed={pid}" for pid in seeds)
    ).json()
    # The automatic clusters keep their order: "Cluster N" is still the filter's.
    assert seeded_body["clusters"] == client.get(clusters_url).json()["clusters"]
    assert seeded_body["seeds_ignored"] == [others]
    seeded = seeded_body["seeded"]
    assert seeded["is_reference"] is False and seeded["size"] == 9
    by_pid = {m["picture_id"]: m for m in seeded["members"]}
    assert {pid for pid, m in by_pid.items() if m["pinned"]} == set(b_ids[:2])
    order = [m["picture_id"] for m in seeded["members"]]
    assert max(order.index(pid) for pid in b_ids) < min(
        order.index(pid) for pid in a_ids
    )
    values = [m["centrality"] for m in seeded["members"]]
    assert values == sorted(values, reverse=True)
    probable = by_pid[look_alike]
    assert probable["probable"] and not probable["pinned"]
    face_id = client.server.vault.db.run_task(
        lambda session: session.exec(
            select(Face.id).where(Face.picture_id == look_alike, Face.face_index == 101)
        ).one()
    )
    assert probable["face_id"] == face_id
    assert others not in by_pid
    assert not by_pid[b_ids[0]]["probable"] and by_pid[b_ids[0]]["face_id"] is None
    assert all(
        m["probable"] is False for m in by_pid.values() if m["picture_id"] in ids
    )
    # A picked look-alike is left out, never also "probably" theirs.
    picked = client.get(f"{clusters_url}&seed={b_ids[0]}&seed={look_alike}").json()
    assert picked["seeds_ignored"] == [look_alike]
    assert look_alike not in {m["picture_id"] for m in picked["seeded"]["members"]}
    assert client.get(clusters_url).json()["seeded"] is None
    nobody = client.get(f"{clusters_url}&seed={look_alike}&seed=999999")
    assert nobody.status_code == 422, nobody.text
    too_many = client.get(clusters_url + "&seed=1" * 501)
    assert too_many.status_code == 422, too_many.text

    cluster_url = f"{API_PREFIX}/characters/{character_id}/reference-cluster"
    put = client.put(cluster_url, json={"picture_ids": b_ids})
    assert put.status_code == 200, put.text
    assert put.json()["picture_ids"] == sorted(b_ids)
    refs = client.get(refs_url).json()["reference_picture_ids"]
    assert set(refs) == set(b_ids) and refs[0] == b_centre

    body = client.get(clusters_url).json()
    assert body["reference_cluster"] == sorted(b_ids)
    first = body["clusters"][0]
    assert first["is_reference"] and first["reference_share"] == 1.0
    assert first["members"][0]["picture_id"] == b_centre
    assert body["clusters"][1]["similarity_to_reference"] < 0.45

    # A picture without the person's face is refused, and nothing is stored.
    bad = client.put(cluster_url, json={"picture_ids": [b_ids[0], 999999]})
    assert bad.status_code == 422
    assert bad.json()["detail"]["picture_ids"] == [999999]
    assert client.get(clusters_url).json()["reference_cluster"] == sorted(b_ids)

    # With every member gone, the person has no reference cluster again.
    def set_deleted(flag):
        def run(session):
            for pid in b_ids:
                session.get(Picture, pid).deleted = flag
            session.commit()

        client.server.vault.db.run_task(run)

    set_deleted(True)
    try:
        assert client.get(clusters_url).json()["reference_cluster"] is None
        # A picture on the Scrapheap is no centre: the same 422.
        scrapped = client.get(f"{clusters_url}&centre={b_centre}")
        assert scrapped.status_code == 422, scrapped.text
        refs = client.get(refs_url).json()["reference_picture_ids"]
        assert set(refs[:4]) == set(a_ids)
    finally:
        set_deleted(False)

    cleared = client.put(cluster_url, json={"picture_ids": []})
    assert cleared.json()["picture_ids"] == []
    assert set(client.get(refs_url).json()["reference_picture_ids"][:4]) == set(a_ids)
    assert (
        client.get(f"{API_PREFIX}/face-clusters?character_id=999999").status_code == 404
    )

    # Owner only: a READ token and one scoped to this very person are refused
    # both routes, which the owner reached above (so they resolve).
    for restriction in (
        {},
        {"resource_type": "character", "resource_id": character_id},
    ):
        token = mint_read_token(client, "face clusters", **restriction)
        guest = bearer_client(client.server, token)
        assert guest.get(clusters_url).status_code == 403, restriction
        refused = guest.put(cluster_url, json={"picture_ids": b_ids})
        assert refused.status_code == 403, restriction
    assert client.get(clusters_url).json()["reference_cluster"] is None

    # Purging a member deletes its row, so a picture that reuses the id
    # never joins the frozen cluster.
    client.put(cluster_url, json={"picture_ids": b_ids})
    gone = b_ids[0]

    def soft_delete(session):
        session.get(Picture, gone).deleted = True
        session.commit()

    client.server.vault.db.run_task(soft_delete)
    purged = scrapheap_service.purge_scrapheap_pictures(
        client.server.vault, [gone], include_protected=True
    )
    assert list(purged.purged_ids) == [gone]

    def stored(session):
        return sorted(
            session.exec(
                select(CharacterReferenceMember.picture_id).where(
                    CharacterReferenceMember.character_id == character_id
                )
            ).all()
        )

    assert client.server.vault.db.run_task(stored) == sorted(b_ids[1:])


def test_item_sheets_are_prompted_ranked_and_cut_into_four_pictures(client):
    prompt = client.post(
        f"{API_PREFIX}/character-sheets/prompt",
        json={"kind": "prop", "description": "a brass lantern", "size": "4k"},
    )
    assert prompt.status_code == 200, prompt.text
    body = prompt.json()
    assert (body["width"], body["height"]) == (3840, 2304)
    assert body["prompt"].startswith("Prop reference sheet")
    assert (
        client.post(
            f"{API_PREFIX}/character-sheets/prompt",
            json={"kind": "vehicle", "description": "x"},
        ).status_code
        == 422
    )
    sheet = _upload_sheet(client, [("x", 1300, 0, 1536), ("y", 760, 0, 2560)])
    ranked = client.post(
        f"{API_PREFIX}/item-sheets/rank",
        json={"kind": "location", "picture_ids": [sheet]},
    )
    assert ranked.status_code == 200, ranked.text
    (row,) = ranked.json()["sheets"]
    assert row["readable"] and row["cuts_cleanly"] is True
    assert len(row["cells"]) == 4 and [p["panel"] for p in row["panels"]] == [
        0,
        1,
        2,
        3,
    ]
    assert row["agreement_words"] == "the four views agree"
    # Owner only: a READ token, and one scoped to the sheet itself, are refused
    # both routes, which the owner reaches here (so they resolve). The refusal
    # is the READ write-belt's, ahead of the gate: no mintable token but the
    # owner's can POST, so the registry row is not what this exercises.
    for restriction in ({}, {"resource_type": "picture", "resource_id": sheet}):
        guest = bearer_client(
            client.server, mint_read_token(client, "item sheets", **restriction)
        )
        for route in ("rank", "use"):
            refused = guest.post(
                f"{API_PREFIX}/item-sheets/{route}",
                json={"kind": "location", "picture_ids": [sheet]},
            )
            assert refused.status_code == 403, (route, restriction)
    used = client.post(
        f"{API_PREFIX}/item-sheets/use",
        json={"kind": "location", "picture_ids": [sheet]},
    )
    assert used.status_code == 200, used.text
    panel_ids = used.json()["picture_ids"]
    assert len(panel_ids) == 4 and sheet not in panel_ids

    def read(session):
        return session.exec(select(Picture).where(Picture.id.in_(panel_ids))).all()

    pictures = client.server.vault.db.run_task(read)
    assert all(p.source_picture_id is None for p in pictures)
    six = client.post(
        f"{API_PREFIX}/item-sheets/use",
        json={
            "kind": "outfit",
            "picture_ids": [sheet],
            "boxes": {str(sheet): _HAND_BOXES},
        },
    )
    assert six.status_code == 422
    missing = client.post(
        f"{API_PREFIX}/item-sheets/rank",
        json={"kind": "prop", "picture_ids": [sheet, 999999]},
    )
    assert missing.status_code == 404
