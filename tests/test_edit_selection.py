"""A selection as JSON and the mask it renders to (the Edit tab's Paint tools)."""

import io
import tempfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from sqlmodel import Session, delete

from pixlstash.db_models import Picture
from pixlstash.image_plugins.registry import get_image_plugin_manager
from pixlstash.server import Server
from pixlstash.services.edit_selection import Selection, render_mask

# Left half red, right half blue, 40 x 20.
PICTURE = np.zeros((20, 40, 3), dtype=np.uint8)
PICTURE[:, :20] = (220, 30, 30)
PICTURE[:, 20:] = (30, 30, 220)


def _mask(steps, **refine):
    return render_mask(Selection(steps=steps, refine=refine), PICTURE)


def test_modes_combine_shapes_in_order():
    """New replaces, add unions, subtract cuts, intersect keeps the overlap."""
    left = {"tool": "rect", "box": [0, 0, 0.5, 1]}
    top = {"tool": "rect", "box": [0, 0, 1, 0.5]}
    assert (_mask([{**left, "mode": "new"}])[:, :20] == 255).all()
    assert (_mask([{**left, "mode": "new"}])[:, 20:] == 0).all()
    added = _mask([{**left, "mode": "new"}, {**top, "mode": "add"}])
    assert added[0, 39] == 255 and added[19, 39] == 0
    cut = _mask([{**left, "mode": "new"}, {**top, "mode": "subtract"}])
    assert cut[0, 0] == 0 and cut[19, 0] == 255
    both = _mask([{**left, "mode": "new"}, {**top, "mode": "intersect"}])
    assert both[0, 0] == 255 and both[19, 0] == 0 and both[0, 39] == 0
    replaced = _mask([{**left, "mode": "new"}, {**top, "mode": "new"}])
    assert replaced[19, 0] == 0


def test_invert_lasso_ellipse_and_brush():
    inverted = _mask(
        [{"tool": "rect", "mode": "new", "box": [0, 0, 0.5, 1]}, {"tool": "invert"}]
    )
    assert inverted[0, 0] == 0 and inverted[0, 39] == 255
    lasso = _mask(
        [{"tool": "lasso", "mode": "new", "points": [[0, 0], [1, 0], [0, 1]]}]
    )
    assert lasso[1, 1] == 255 and lasso[19, 39] == 0
    ellipse = _mask([{"tool": "ellipse", "mode": "new", "box": [0, 0, 1, 1]}])
    assert ellipse[10, 20] == 255 and ellipse[0, 0] == 0
    brush = _mask(
        [
            {
                "tool": "brush",
                "mode": "new",
                "radius": 0.1,
                "points": [[0.1, 0.5], [0.9, 0.5]],
            }
        ]
    )
    assert brush[10, 20] == 255 and brush[0, 20] == 0


def test_the_wand_floods_one_colour_and_stops_at_its_edge():
    wand = _mask(
        [{"tool": "wand", "mode": "new", "point": [0.1, 0.5], "tolerance": 10}]
    )
    assert (wand[:, :20] == 255).all() and (wand[:, 20:] == 0).all()


def test_refine_fills_holes_grows_and_feathers():
    ring = [
        {"tool": "rect", "mode": "new", "box": [0.1, 0.1, 0.8, 0.8]},
        {"tool": "rect", "mode": "subtract", "box": [0.45, 0.45, 0.1, 0.1]},
    ]
    assert _mask(ring)[10, 20] == 0
    assert _mask(ring, fill_holes=8)[10, 20] == 255
    box = [{"tool": "rect", "mode": "new", "box": [0.25, 0.25, 0.5, 0.5]}]
    assert _mask(box)[3, 20] == 0 and _mask(box, grow=3)[3, 20] == 255
    assert _mask(box, grow=-3)[5, 20] == 0
    feathered = _mask(box, feather=4)
    assert 0 < feathered[5, 20] < 255


def test_refine_treats_the_picture_edge_as_neither_selected_nor_not():
    """Shrinking keeps a selection that runs to the edge, growing nothing
    stays nothing, and growing reaches out by a disc, not a square."""
    left = [{"tool": "rect", "mode": "new", "box": [0, 0, 0.5, 1]}]
    shrunk = _mask(left, grow=-3)
    assert shrunk[0, 0] == 255 and shrunk[10, 16] == 255 and shrunk[10, 17] == 0
    assert not _mask([{"tool": "invert"}, {"tool": "invert"}], grow=5).any()
    assert (_mask([{"tool": "invert"}], grow=-5) == 255).all()
    dot = [{"tool": "rect", "mode": "new", "box": [0.5, 0.5, 0.025, 0.05]}]
    grown = _mask(dot, grow=5)
    assert grown[10, 15] == 255 and grown[5, 15] == 0


def test_a_step_without_its_geometry_is_refused():
    with pytest.raises(ValidationError):
        Selection(steps=[{"tool": "rect"}])
    with pytest.raises(ValidationError):
        Selection(steps=[{"tool": "brush", "points": [[0, 0]]}])
    with pytest.raises(ValidationError):
        Selection(steps=[{"tool": "lasso", "points": [[0, 0], [1, 1]]}])


def test_a_coordinate_far_off_the_picture_is_refused():
    for far in (1e30, float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            Selection(steps=[{"tool": "rect", "box": [0, 0, far, 0.5]}])
        with pytest.raises(ValidationError):
            Selection(steps=[{"tool": "wand", "point": [far, 0.5]}])
    Selection(steps=[{"tool": "lasso", "points": [[-0.1, 0], [1.1, 0], [1, 1]]}])


def test_the_crop_plugin_cuts_the_box_given_as_fractions():
    plugin = get_image_plugin_manager().get_plugin("crop")
    assert plugin is not None
    [out] = plugin.run(
        [Image.fromarray(PICTURE)],
        {"left": 0.5, "top": 0.25, "width": 0.5, "height": 0.5},
    )
    assert out.size == (20, 10)
    assert np.array(out)[0, 0].tolist() == [30, 30, 220]
    # Out of range and nonsense clamp to the picture rather than failing.
    [clamped] = plugin.run([Image.fromarray(PICTURE)], {"left": -1, "width": 9})
    assert clamped.size[0] == 40


# ── the route: POST /pictures/{id}/mask ──────────────────────────────────────


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as temp_dir:
        with Server(f"{temp_dir}/server-config.json") as srv:
            yield srv


@pytest.fixture(scope="module")
def client(server):
    client = TestClient(server.api)
    response = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def pictures(server, tmp_path):
    """Two pictures, one with a real file the mask is drawn on."""
    path = tmp_path / "masked.png"
    Image.fromarray(PICTURE).save(path)

    def create(session: Session):
        rows = [Picture(file_path=str(path)), Picture(file_path=str(path))]
        session.add_all(rows)
        session.commit()
        return [row.id for row in rows]

    ids = server.vault.db.run_task(create)
    yield ids

    def remove(session: Session):
        session.exec(delete(Picture).where(Picture.id.in_(ids)))
        session.commit()

    server.vault.db.run_task(remove)


LEFT_HALF = {"steps": [{"tool": "rect", "mode": "new", "box": [0, 0, 0.5, 1]}]}


def _token(client, scope, picture_id=None):
    body = {"description": "test-mask", "scope": scope}
    if picture_id is not None:
        body.update(resource_type="picture", resource_id=picture_id)
    response = client.post("/users/me/token", json=body)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_the_route_answers_the_mask_a_run_would_get(client, pictures):
    response = client.post(f"/pictures/{pictures[0]}/mask", json=LEFT_HALF)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(response.content)) as image:
        mask = np.array(image)
    assert mask.shape == (20, 40)
    assert (mask[:, :20] == 255).all() and (mask[:, 20:] == 0).all()


def test_the_route_follows_the_token_scope(server, client, pictures):
    """A resource-scoped token is READ-only, and a READ token renders nothing,
    even on its own picture; an unscoped token is the positive control that
    the route resolves under a bearer token at all."""
    mine, _other = pictures
    anon = TestClient(server.api)
    path = f"/pictures/{mine}/mask"
    owner = _token(client, "ALL")
    assert anon.post(path, json=LEFT_HALF, headers=owner).status_code == 200
    read = _token(client, "READ", mine)
    assert anon.post(path, json=LEFT_HALF, headers=read).status_code == 403
