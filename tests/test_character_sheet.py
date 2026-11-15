"""Character sheets: the layout prompt, the cut into panels, and the routes."""

import io
import json
import pathlib
import tempfile
import time

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
from pixlstash.services.workflow_hash import reduce_api_graph
from pixlstash.services.workflow_identity import reference_input_count
from pixlstash.utils.insightface_model_utils import DEFAULT_MODEL_PACK
from pixlstash.services.character_sheet import (
    SHEET_HEIGHT,
    SHEET_WIDTH,
    agreement,
    agreement_words,
    closeness,
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


@pytest.mark.parametrize("framing", ["waist_up", "mid_thigh", "knees_up", "full_body"])
def test_every_box_lands_inside_its_own_panel(framing):
    layout = json.loads(sheet_prompt(description="an adult woman", framing=framing))
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


@pytest.mark.parametrize("size", ["small", "medium"])
def test_a_smaller_sheet_scales_the_layout_with_its_canvas(size):
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
            i["bbox"] for i in items if i["caption"].startswith("Panel 5: Her head")
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
    assert "Her body shape: curvy." in given
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
            "the same woman as in the reference picture, auburn hair, slim"
            in item["caption"]
        )
    assert "reference picture" not in json.dumps(plain)
    # No description: the picture alone says who.
    bare = json.loads(sheet_prompt(description="", reference=True))
    assert (
        "the same woman as in the reference picture, wearing"
        in (bare["items"][0]["caption"])
    )


def test_a_prose_prompt_is_the_summary_sentence_not_the_layout():
    layout = json.loads(sheet_prompt(description="x", reference=True))
    prose = sheet_prompt(description="x", reference=True, layout="prose")
    assert prose == layout["summary"]["caption"]
    assert prose.startswith("Character reference sheet of one and the same woman")


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
    ("name", "references"),
    [
        ("Sheet-Flux2-Klein-9B.json", 0),
        ("Sheet-Flux2-Klein-9B-Reference.json", 1),
        ("Sheet-Qwen-Image-2.1.json", 0),
        ("Sheet-Qwen-Image-2.1-Reference.json", 1),
    ],
)
def test_the_shipped_sheet_workflows_draw_at_the_sheet_size(name, references):
    """The step lists a workflow from a reference only with a reference input,
    and draws only one with a size to set: every size is the sheet's (#2700)."""
    graph = json.loads((pathlib.Path(WORKFLOW_BUILTIN_DIR) / name).read_text())
    assert reference_input_count(reduce_api_graph(graph)) == references
    sizes = {
        (node["inputs"]["width"], node["inputs"]["height"])
        for node in graph.values()
        if "width" in node["inputs"]
    }
    assert sizes == {(SHEET_WIDTH, SHEET_HEIGHT)}


@pytest.mark.parametrize(
    ("graph", "words"),
    [
        (_edit_graph("literal"), "already draws at a size of its own"),
        (
            {k: v for k, v in _edit_graph("literal").items() if k != "1"},
            "reads no picture",
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


def test_a_seamless_sheet_is_cut_on_the_grid():
    assert sheet_cells(_sheet()) == [
        (0, 0, 592, 760),
        (608, 0, 1192, 760),
        (0, 776, 592, 1536),
        (608, 776, 1192, 1536),
        (1208, 0, 1872, 1536),
        (1888, 0, 2560, 1536),
    ]


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
    cells = sheet_cells(image)
    assert abs(cells[0][2] - (630 - 8)) <= 4
    assert abs(cells[1][0] - (630 + 8)) <= 4
    assert abs(cells[0][3] - (790 - 8)) <= 4
    assert abs(cells[1][2] - (1180 - 8)) <= 4
    assert abs(cells[4][0] - (1180 + 8)) <= 4
    assert abs(cells[4][2] - (1900 - 8)) <= 4
    assert abs(cells[5][0] - (1900 + 8)) <= 4
    # No panel keeps a sliver of a divider.
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
    cells = sheet_cells(image)
    assert abs(cells[0][2] - (523 - 8)) <= 4
    assert abs(cells[1][0] - (523 + 8)) <= 4
    assert abs(cells[0][3] - (700 - 8)) <= 4
    assert abs(cells[1][2] - (1064 - 8)) <= 4
    assert abs(cells[4][0] - (1064 + 8)) <= 4
    assert abs(cells[4][2] - (1689 - 8)) <= 4
    assert abs(cells[5][0] - (1689 + 8)) <= 4


def test_the_nearest_divider_wins_over_a_stronger_one_further_off():
    """A sheet that broke its layout can show a second seam in a cut's
    window; the gutter nearest the grid line is the one the cut belongs in."""
    image = _sheet(
        [
            ("x", 1180, 0, 1300),  # the gutter, over 85% of the height
            ("x", 1350, 0, 1536),  # a full-height seam 150 px off
        ]
    )
    assert abs(sheet_cells(image)[4][0] - (1180 + 8)) <= 4


def test_a_cell_that_is_all_frame_is_kept_rather_than_inverted():
    """Two dividers ~200 px apart leave a passport cell narrower than the
    frame trim; on white it must still cut, not crash the crop."""
    image = Image.new("RGB", (SHEET_WIDTH, SHEET_HEIGHT), (252, 252, 252))
    draw = ImageDraw.Draw(image)
    for at in (800, 1000):
        draw.rectangle([at, 0, at + 3, SHEET_HEIGHT - 1], fill=(40, 40, 40))
    for left, top, right, bottom in sheet_cells(image):
        assert left < right and top < bottom
    assert len(character_sheet.split_sheet(image)) == 6


def test_a_person_s_outline_is_not_a_divider():
    """An edge that does not run the cut's whole length leaves the cut where it is."""
    image = _sheet([("x", 1220, 300, 900)])
    assert sheet_cells(image)[4][0] == 1200 + 8


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
    cells = sheet_cells(image)
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
    cells = sheet_cells(_sheet(size=(1280, 768)))
    assert cells[4] == (604, 0, 936, 768)
    assert len(character_sheet.split_sheet(_sheet(size=(1280, 768)))) == 6


def test_agreement_and_closeness():
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    assert agreement([a]) is None
    assert agreement([a, a, a]) == pytest.approx(1.0)
    assert agreement([a, b]) == pytest.approx(0.0)
    assert closeness([a, a], [a]) == pytest.approx(1.0)
    assert closeness([a], []) is None
    assert agreement_words(None) == "faces don't hold"
    assert agreement_words(0.3) == "faces don't hold"
    assert agreement_words(0.5) == "faces agree somewhat"
    assert agreement_words(0.7) == "faces agree strongly"


def test_ranking_measures_closeness_against_references_never_the_sheet_itself():
    """A reference face on the sheet itself is left out; close follows the threshold."""
    person = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    other = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    seen = {}

    class Db:
        def run_immediate_read_task(self, fn, *args):
            seen["args"] = args
            return (
                {5: [person.tobytes()] * 6, 6: [other.tobytes()] * 6, 7: []},
                [person.tobytes()],
                {5: "PNG", 6: "WEBP"},
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


def test_ranking_names_no_difference_between_sheets_of_one_person():
    """The positive control: sheets alike across sheets are not flagged."""
    person = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    near_person = np.array([0.9, 0.3, 0.0], dtype=np.float32)

    class Db:
        def run_immediate_read_task(self, fn, *args):
            return ({5: [person.tobytes()] * 6, 6: [near_person.tobytes()] * 6}, [], {})

    vault = type("Vault", (), {"db": Db()})()
    sheets, _ = character_sheet.rank_sheets(vault, 1, [5, 6])
    assert [s["different_from"] for s in sheets] == [[], []]


def test_a_sheet_that_fails_to_import_leaves_the_earlier_panels_filed(monkeypatch):
    """Panels already imported are filed before the next sheet is tried."""
    filed = []

    class Db:
        def run_immediate_read_task(self, fn, *args):
            return [Picture(id=1), Picture(id=2)]

        def run_task(self, fn, picture_ids, character_id):
            filed.append((list(picture_ids), character_id))

    def import_panels(server, sources, entries, stack):
        if sources[0].id == 2:
            raise OSError("disk full")
        return [], [], [10, 11]

    monkeypatch.setattr(character_sheet, "_cut", lambda server, sheet: [b""] * 2)
    monkeypatch.setattr(character_sheet, "import_derived_outputs", import_panels)
    server = type("Server", (), {"vault": type("Vault", (), {"db": Db()})()})()
    with pytest.raises(OSError):
        character_sheet.use_sheets(server, 3, [1, 2])
    assert filed == [([10, 11], 3)]


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
    prose = client.post(
        f"{API_PREFIX}/character-sheets/prompt",
        json={"description": "x", "reference": True, "layout": "prose"},
    ).json()["prompt"]
    assert "the same woman as in the reference picture, x" in prose
    assert not prose.startswith("{")


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
    # The body | body divider drawn at 1900 moved that cut 20 px right.
    assert sizes[:4] == [(592, 760), (584, 760), (592, 760), (584, 760)]
    assert sizes[4:] == [(684, 1536), (652, 1536)]
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
    assert not any(c["is_reference"] for c in body["clusters"])

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
