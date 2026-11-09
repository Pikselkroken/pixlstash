"""Character sheets: the layout prompt, the cut into panels, and the routes."""

import io
import json
import tempfile
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlmodel import select

from pixlstash.db_models import Face, Picture
from pixlstash.server import Server
from pixlstash.services import character_sheet
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


@pytest.mark.parametrize("framing", ["mid_thigh", "knees_up", "full_body"])
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
            )

    vault = type("Vault", (), {"db": Db()})()
    sheets, references = character_sheet.rank_sheets(vault, 1, [5, 6, 7, 5])
    assert seen["args"] == (1, [5, 6, 7])
    assert references == 1
    near, far, unread = sheets
    assert (near["close"], far["close"]) == (True, False)
    assert near["closeness"] == pytest.approx(1.0)
    assert unread["faces_read"] is False and unread["agreement"] is None


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


@pytest.fixture(scope="module")
def sheet_id(client):
    buffer = io.BytesIO()
    _sheet([("x", 1900, 0, 1536)]).save(buffer, format="PNG")
    status = upload_pictures_and_wait(
        client, [("file", ("sheet.png", buffer.getvalue(), "image/png"))]
    )
    assert status["status"] == "completed", status
    return status["results"][0]["picture_id"]


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


def test_using_a_sheet_files_six_panels_on_the_character(client, sheet_id):
    character_id = _new_character(client, "Sheet Mira")
    response = client.post(
        f"{API_PREFIX}/characters/{character_id}/sheets/{sheet_id}/use"
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
        assert picture.source_picture_id == sheet_id
        # Filed on the character: its face now, or its largest face once read.
        assert (
            picture_id in with_face or picture.pending_character_id == character_id
        ), picture_id


def test_using_a_sheet_refuses_unknown_ids(client, sheet_id):
    character_id = _new_character(client, "Sheet Kai")
    missing_character = client.post(
        f"{API_PREFIX}/characters/999999/sheets/{sheet_id}/use"
    )
    assert missing_character.status_code == 404
    missing_picture = client.post(
        f"{API_PREFIX}/characters/{character_id}/sheets/999999/use"
    )
    assert missing_picture.status_code == 404


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

    def add_faces(session):
        for index in range(7):
            vector = person + rng.normal(scale=0.3, size=512).astype(np.float32)
            session.add(
                Face(
                    picture_id=sheet_id,
                    face_index=100 + index,
                    features=vector.astype(np.float32).tobytes(),
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
