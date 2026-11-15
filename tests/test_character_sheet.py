"""Character sheets: the layout prompt, the cut into panels, and the routes."""

import io
import json
import tempfile
import time
import uuid

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlmodel import select

from pixlstash.db_models import CharacterSheetDraw, Face, Picture
from pixlstash.server import Server
from pixlstash.event_types import EventType
from pixlstash.services import character_sheet, comfyui_service, scrapheap_service
from pixlstash.services.run_log import LOST_MESSAGE
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
from tests.utils import API_PREFIX, upload_pictures_and_wait

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
        "the same woman as in the reference picture, average build"
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


# --- Character sheet draws: the dialog's state, kept on the character --------


def _key() -> str:
    return f"run-{uuid.uuid4().hex[:16]}"


def _running(key: str) -> dict:
    return {"status": "running", "message": None, "picture_ids": []}


def _fake_outcomes(monkeypatch, outcomes: dict) -> None:
    """Point the settle at *outcomes*: a key not in it has no recorded ending."""
    monkeypatch.setattr(comfyui_service, "run_outcome", lambda key: outcomes.get(key))


def _post_draw(client, character_id, run_keys, drawn_from=None):
    response = client.post(
        f"{API_PREFIX}/characters/{character_id}/sheets/draws",
        json={
            "settings": {"workflow_id": "wf-1", "lora": "kai", "signature": "sig"},
            "drawn_from": drawn_from,
            "run_keys": run_keys,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _list_draws(client, character_id):
    response = client.get(f"{API_PREFIX}/characters/{character_id}/sheets/draws")
    assert response.status_code == 200, response.text
    return response.json()["draws"]


def _stored_runs(server, draw_id):
    """The runs as the database holds them, read without settling anything."""

    def read(session):
        return json.loads(session.get(CharacterSheetDraw, draw_id).runs)

    return server.vault.db.run_task(read)


def test_draws_round_trip_newest_first(client, monkeypatch):
    outcomes: dict = {}
    _fake_outcomes(monkeypatch, outcomes)
    character_id = _new_character(client, "Draw Round Trip")
    keys_a = [_key(), _key()]
    keys_b = [_key()]
    first = _post_draw(client, character_id, keys_a, drawn_from=None)
    # Nothing has ended yet, so the new runs wait as queued.
    assert [run["status"] for run in first["runs"]] == ["queued", "queued"]
    assert first["runs"][0] == {
        "key": keys_a[0],
        "status": "queued",
        "message": "",
        "picture_ids": [],
    }
    second = _post_draw(client, character_id, keys_b, drawn_from=42)
    outcomes.update({key: _running(key) for key in keys_a + keys_b})

    draws = _list_draws(client, character_id)
    assert [draw["id"] for draw in draws] == [second["id"], first["id"]]
    newest = draws[0]
    assert newest["settings"] == {
        "workflow_id": "wf-1",
        "lora": "kai",
        "signature": "sig",
    }
    assert newest["drawn_from"] == 42
    assert newest["character_id"] == character_id
    assert newest["created_at"].endswith("+00:00")
    assert newest["kept"] == [] and newest["used"] == [] and newest["used_sheets"] == []
    assert newest["seen"] is False
    assert [run["status"] for run in newest["runs"]] == ["running"]
    assert [run["key"] for run in draws[1]["runs"]] == keys_a


def test_a_draw_settles_from_how_each_run_ended(client, monkeypatch):
    done, live, lost = _key(), _key(), _key()
    outcomes = {
        done: {"status": "completed", "message": "made", "picture_ids": [11, 12]},
        live: _running(live),
    }
    _fake_outcomes(monkeypatch, outcomes)
    character_id = _new_character(client, "Settle Each")
    draw = _post_draw(client, character_id, [done, live, lost])

    (settled,) = _list_draws(client, character_id)
    assert settled["id"] == draw["id"]
    runs = {run["key"]: run for run in settled["runs"]}
    assert runs[done] == {
        "key": done,
        "status": "completed",
        "message": "made",
        "picture_ids": [11, 12],
    }
    assert runs[live]["status"] == "running"
    # No recorded ending any more: a restart lost it, so it is failed, not queued.
    assert runs[lost]["status"] == "failed"
    assert (
        runs[lost]["message"]
        == LOST_MESSAGE
        == ("PixlStash restarted before this run ended, so what it made is not known.")
    )

    # Persisted: a run that ended stays as it ended, even when nothing records it now.
    outcomes.clear()
    (again,) = _list_draws(client, character_id)
    again_runs = {run["key"]: run for run in again["runs"]}
    assert again_runs[done]["status"] == "completed"
    assert again_runs[done]["picture_ids"] == [11, 12]
    assert _stored_runs(client.server, draw["id"])[1]["status"] == "failed"


def test_an_ending_recorded_with_nobody_looking_lands_in_its_draw(client, monkeypatch):
    _fake_outcomes(monkeypatch, {})
    character_id = _new_character(client, "Hook Lands")
    by_run_id, by_prompt_id, still_going = _key(), _key(), _key()
    first = _post_draw(client, character_id, [by_run_id])
    second = _post_draw(client, character_id, [by_prompt_id, still_going])

    # The run is keyed by its run id here; its prompt id is another name for it.
    character_sheet.settle_run(
        client.server.vault,
        {
            "run_id": by_run_id,
            "prompt_id": None,
            "status": "completed",
            "message": "",
            "picture_ids": [21],
        },
    )
    # Keyed by its prompt id: the run id is the other name.
    character_sheet.settle_run(
        client.server.vault,
        {
            "run_id": "run-not-in-any-draw",
            "prompt_id": by_prompt_id,
            "status": "failed",
            "message": "boom",
            "picture_ids": [],
        },
    )
    # A run still going writes nothing.
    character_sheet.settle_run(
        client.server.vault,
        {
            "run_id": still_going,
            "prompt_id": None,
            "status": "running",
            "message": "",
            "picture_ids": [],
        },
    )
    character_sheet.settle_run(None, {"run_id": by_run_id, "status": "completed"})

    (run,) = _stored_runs(client.server, first["id"])
    assert (run["status"], run["picture_ids"]) == ("completed", [21])
    prompted, going = _stored_runs(client.server, second["id"])
    assert (prompted["status"], prompted["message"]) == ("failed", "boom")
    assert going["status"] == "queued"


def test_patch_appends_runs_keeps_and_sees(client, monkeypatch):
    known, fresh = _key(), _key()
    _fake_outcomes(monkeypatch, {known: _running(known)})
    character_id = _new_character(client, "Patch Draw")
    draw = _post_draw(client, character_id, [known])
    url = f"{API_PREFIX}/characters/{character_id}/sheets/draws/{draw['id']}"

    response = client.patch(
        url, json={"add_run_keys": [known, fresh], "kept": [7, 8, 7], "seen": True}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # The known run is not added twice; the new one waits as queued.
    assert [(run["key"], run["status"]) for run in body["runs"]] == [
        (known, "running"),
        (fresh, "queued"),
    ]
    assert body["kept"] == [7, 8] and body["seen"] is True

    response = client.patch(url, json={"kept": [9]})
    assert response.json()["kept"] == [9]
    assert response.json()["seen"] is True

    missing = client.patch(
        f"{API_PREFIX}/characters/{character_id}/sheets/draws/999999",
        json={"seen": True},
    )
    assert missing.status_code == 404


def test_another_characters_draw_is_not_found(client, monkeypatch):
    _fake_outcomes(monkeypatch, {})
    owner = _new_character(client, "Draw Owner")
    other = _new_character(client, "Draw Stranger")
    key = _key()
    draw = _post_draw(client, owner, [key])
    url = f"{API_PREFIX}/characters/{other}/sheets/draws/{draw['id']}"

    assert client.patch(url, json={"seen": True}).status_code == 404
    assert client.delete(url).status_code == 404
    # Refused both ways: the owner's draw is untouched, and the stranger has none.
    (kept_draw,) = _list_draws(client, owner)
    assert kept_draw["seen"] is False and kept_draw["id"] == draw["id"]
    assert _list_draws(client, other) == []
    assert client.get(f"{API_PREFIX}/characters/999999/sheets/draws").status_code == 404
    assert (
        client.post(
            f"{API_PREFIX}/characters/999999/sheets/draws",
            json={"settings": {}, "run_keys": [key]},
        ).status_code
        == 404
    )


def test_summary_lists_each_character_with_draws_and_its_run_keys(client, monkeypatch):
    first, second, third = _key(), _key(), _key()
    _fake_outcomes(
        monkeypatch,
        {key: _running(key) for key in (first, second, third)},
    )
    character_id = _new_character(client, "Summary Draws")
    _new_character(client, "Summary Without Draws")
    older = _post_draw(client, character_id, [first])
    newer = _post_draw(client, character_id, [second, third])

    response = client.get(f"{API_PREFIX}/character-sheets/draws")
    assert response.status_code == 200, response.text
    entries = {c["character_id"]: c for c in response.json()["characters"]}
    entry = entries[character_id]
    assert entry["draws"] == 2
    assert entry["newest"]["id"] == newer["id"] != older["id"]
    assert entry["run_keys"] == [second, third, first]
    # Only characters with a draw are listed.
    assert all(c["draws"] >= 1 for c in entries.values())


def test_use_with_a_draw_records_the_panels_and_the_sheets_cut(
    client, sheet_id, monkeypatch
):
    _fake_outcomes(monkeypatch, {})
    character_id = _new_character(client, "Used Draw")
    draw = _post_draw(client, character_id, [_key()])
    response = client.post(
        f"{API_PREFIX}/characters/{character_id}/sheets/use",
        json={"picture_ids": [sheet_id, sheet_id], "draw_id": draw["id"]},
    )
    assert response.status_code == 200, response.text
    panel_ids = response.json()["picture_ids"]
    assert len(panel_ids) == 6

    (recorded,) = _list_draws(client, character_id)
    assert recorded["used"] == panel_ids
    # The sheet cut is listed once, however many times the request names it.
    assert recorded["used_sheets"] == [sheet_id]

    # Cut again through the same draw (a second tab, a repeated request): refused
    # before anything is filed, and the record is unchanged.
    again = client.post(
        f"{API_PREFIX}/characters/{character_id}/sheets/use",
        json={"picture_ids": [sheet_id], "draw_id": draw["id"]},
    )
    assert again.status_code == 409, again.text
    (unchanged,) = _list_draws(client, character_id)
    assert unchanged["used"] == panel_ids

    # Another character's draw is a 404, and nothing is cut for it.
    stranger = _new_character(client, "Used Stranger")
    refused = client.post(
        f"{API_PREFIX}/characters/{stranger}/sheets/use",
        json={"picture_ids": [sheet_id], "draw_id": draw["id"]},
    )
    assert refused.status_code == 404
    unknown = client.post(
        f"{API_PREFIX}/characters/{character_id}/sheets/use",
        json={"picture_ids": [sheet_id], "draw_id": 999999},
    )
    assert unknown.status_code == 404
    assert _list_draws(client, stranger) == []


def test_a_purged_sheet_drops_out_of_the_draw_shown(client, sheet_id, monkeypatch):
    key = _key()
    gone = 987_654_321
    _fake_outcomes(
        monkeypatch,
        {key: {"status": "completed", "message": "", "picture_ids": [sheet_id, gone]}},
    )
    character_id = _new_character(client, "Purged Sheet")
    draw = _post_draw(client, character_id, [key])
    response = client.patch(
        f"{API_PREFIX}/characters/{character_id}/sheets/draws/{draw['id']}",
        json={"kept": [sheet_id, gone]},
    )
    assert response.status_code == 200, response.text
    (shown,) = _list_draws(client, character_id)
    assert shown["runs"][0]["picture_ids"] == [sheet_id]
    assert shown["kept"] == [sheet_id]


def test_deleting_a_draw_keeps_nothing_of_it(client, monkeypatch):
    _fake_outcomes(monkeypatch, {})
    character_id = _new_character(client, "Delete Draw")
    draw = _post_draw(client, character_id, [_key()])
    url = f"{API_PREFIX}/characters/{character_id}/sheets/draws/{draw['id']}"
    response = client.delete(url)
    assert response.status_code == 200 and response.json() == {"deleted": True}
    assert _list_draws(client, character_id) == []
    assert client.delete(url).status_code == 404


def test_deleting_a_character_deletes_its_draws(client, monkeypatch):
    _fake_outcomes(monkeypatch, {})
    character_id = _new_character(client, "Delete With Draws")
    draw = _post_draw(client, character_id, [_key()])
    assert client.delete(f"{API_PREFIX}/characters/{character_id}").status_code == 200

    def remaining(session):
        return session.exec(
            select(CharacterSheetDraw).where(CharacterSheetDraw.id == draw["id"])
        ).all()

    assert client.server.vault.db.run_task(remaining) == []


def test_settings_past_the_size_cap_are_refused(client, monkeypatch):
    _fake_outcomes(monkeypatch, {})
    character_id = _new_character(client, "Settings Cap")
    url = f"{API_PREFIX}/characters/{character_id}/sheets/draws"
    fits = client.post(
        url, json={"settings": {"text": "a" * 19_000}, "run_keys": [_key()]}
    )
    assert fits.status_code == 200, fits.text
    too_big = client.post(
        url, json={"settings": {"text": "a" * 20_000}, "run_keys": [_key()]}
    )
    assert too_big.status_code == 422


def test_unreadable_stored_columns_are_read_as_empty(client, monkeypatch):
    _fake_outcomes(monkeypatch, {})
    character_id = _new_character(client, "Unreadable Draw")
    draw = _post_draw(client, character_id, [_key()])

    def corrupt(session):
        row = session.get(CharacterSheetDraw, draw["id"])
        row.runs = "not json"
        row.settings = "[1]"
        row.kept = '{"a": 1}'
        session.add(row)
        session.commit()

    client.server.vault.db.run_task(corrupt)
    (read,) = _list_draws(client, character_id)
    assert read["runs"] == [] and read["settings"] == {} and read["kept"] == []
