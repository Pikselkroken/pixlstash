"""Colour shares (#2151): the bucketing, the stored column, the colour filter and the metadata."""

import io
import json
import os
import re
import tempfile

import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, delete, select

from pixlstash.db_models import Picture, PictureStack
from pixlstash.server import Server
from pixlstash.services.smart_sets import SmartFilterError, normalise_filter
from pixlstash.tasks.colour_shares_task import ColourSharesTask
from pixlstash.tasks.missing_colour_shares_finder import MissingColourSharesFinder
from pixlstash.tasks.task_type import TaskType
from pixlstash.utils.image_processing.colour_shares import (
    SWATCHES,
    SWATCH_NAMES,
    shares_from_bytes,
    shares_from_column,
    shares_from_rgb,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.query.predicate_filter import PredicateFilter, parse_colour_entry

BLUE = "#3c66ae"


def _shares_row(**named: float) -> str:
    """A stored value: the given shares by name, the rest zero."""
    return json.dumps([float(named.get(name, 0.0)) for name in SWATCH_NAMES])


# ── the bucketing ────────────────────────────────────────────────────────────


def test_pure_red_is_all_red():
    shares = shares_from_rgb(np.full((8, 8, 3), (255, 0, 0), np.uint8))
    assert shares[SWATCH_NAMES.index("red")] == 100.0
    assert sum(shares) == pytest.approx(100.0)


def test_half_white_half_black_is_split_evenly():
    rgb = np.zeros((10, 10, 3), np.uint8)
    rgb[:5] = 255
    shares = shares_from_rgb(rgb)
    assert shares[SWATCH_NAMES.index("white")] == 50.0
    assert shares[SWATCH_NAMES.index("black")] == 50.0
    assert sum(shares) == pytest.approx(100.0)


def test_noise_still_sums_to_exactly_one_hundred():
    rng = np.random.default_rng(7)
    shares = shares_from_rgb(rng.integers(0, 256, size=(37, 41, 3), dtype=np.uint8))
    assert len(shares) == 12
    assert sum(shares) == pytest.approx(100.0, abs=1e-9)
    # Every value is a tenth of a percent.
    assert all(round(v * 10) == pytest.approx(v * 10) for v in shares)


def test_a_blue_patch_counts_as_blue():
    rgb = np.full(
        (6, 6, 3), tuple(int(BLUE[i : i + 2], 16) for i in (1, 3, 5)), np.uint8
    )
    shares = shares_from_rgb(rgb)
    assert shares[SWATCH_NAMES.index("blue")] == 100.0


def test_transparent_pixels_read_as_white_not_black():
    buffer = io.BytesIO()
    Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(buffer, format="PNG")
    shares = shares_from_bytes(buffer.getvalue())
    assert shares[SWATCH_NAMES.index("white")] == 100.0


def test_column_reader_treats_sentinel_and_null_as_no_summary():
    assert shares_from_column(None) is None
    assert shares_from_column("[]") is None
    assert shares_from_column(_shares_row(blue=14)) == [
        0.0 if n != "blue" else 14.0 for n in SWATCH_NAMES
    ]


# ── the query parameter ──────────────────────────────────────────────────────


def test_a_well_formed_entry_parses_to_its_index_and_bound():
    assert parse_colour_entry("blue:10") == (SWATCH_NAMES.index("blue"), 10.0)
    assert parse_colour_entry("Grey:0") == (SWATCH_NAMES.index("grey"), 0.0)


@pytest.mark.parametrize(
    "entry",
    ["mauve:10", "blue", "blue:", "blue:lots", "blue:101", "blue:-1", "blue:nan"],
)
def test_a_malformed_entry_is_a_422(entry):
    with pytest.raises(HTTPException) as caught:
        parse_colour_entry(entry)
    assert caught.value.status_code == 422


# ── the server: finder, task, filter, metadata ───────────────────────────────


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as temp_dir:
        with Server(f"{temp_dir}/server-config.json") as srv:
            # The real sweep would fill these rows under the test.
            srv.vault._work_planner.detach_finders([TaskType.COLOUR_SHARES])
            yield srv


@pytest.fixture(scope="module")
def client(server):
    client = TestClient(server.api)
    response = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert response.status_code == 200, response.text
    return client


def _write_thumbnail(server, file_path: str, image: Image.Image) -> None:
    thumb = ImageUtils.get_thumbnail_path(server.vault.image_root, file_path)
    os.makedirs(os.path.dirname(thumb), exist_ok=True)
    image.save(thumb, format="WEBP")


@pytest.fixture
def pictures(server):
    red = Image.new("RGB", (16, 16), (255, 0, 0))
    _write_thumbnail(server, "colour/red.webp", red)
    with open(
        ImageUtils.get_thumbnail_path(server.vault.image_root, "colour/garbage.webp"),
        "wb",
    ) as handle:
        handle.write(b"not an image")
    # A source with no thumbnail: the failure is pinned to it.
    source = ImageUtils.resolve_picture_path(
        server.vault.image_root, "colour/missing.webp"
    )
    os.makedirs(os.path.dirname(source), exist_ok=True)
    red.save(source, format="WEBP")
    rows = {
        "blue14": dict(colour_shares=_shares_row(blue=14.0, grey=86.0)),
        "blue5_grey20": dict(colour_shares=_shares_row(blue=5.0, grey=20.0)),
        "blue12_grey12": dict(colour_shares=_shares_row(blue=12.0, grey=12.0)),
        "null": dict(),
        "sentinel": dict(colour_shares="[]"),
        "deleted": dict(colour_shares=_shares_row(blue=90.0), deleted=True),
        "todo_red": dict(
            file_path="colour/red.webp", thumbnail_width=16, thumbnail_height=16
        ),
        "todo_garbage": dict(
            file_path="colour/garbage.webp", thumbnail_width=16, thumbnail_height=16
        ),
        "todo_missing": dict(
            file_path="colour/missing.webp", thumbnail_width=16, thumbnail_height=16
        ),
    }

    def create(session: Session):
        made = {name: Picture(**values) for name, values in rows.items()}
        session.add_all(made.values())
        session.commit()
        return {name: pic.id for name, pic in made.items()}

    ids = server.vault.db.run_task(create)
    yield ids

    def remove(session: Session):
        session.exec(delete(Picture).where(Picture.id.in_(ids.values())))
        session.commit()

    server.vault.db.run_task(remove)
    # SQLite reuses ids after the delete: no suppression may outlive the rows.
    for pid in ids.values():
        server.vault.db.unprocessable_images.discard(pid)


def _matching(server, ids, *entries):
    def query(session: Session):
        stmt = PredicateFilter(colour_filter=list(entries)).apply(select(Picture.id))
        return set(session.exec(stmt).all())

    found = server.vault.db.run_immediate_read_task(query)
    return {name for name, pid in ids.items() if pid in found}


def test_a_colour_entry_compares_the_stored_share(server, pictures):
    assert _matching(server, pictures, "blue:10") == {"blue14", "blue12_grey12"}
    assert _matching(server, pictures, "blue:20") == set()


def test_entries_are_anded(server, pictures):
    assert _matching(server, pictures, "blue:10", "grey:10") == {
        "blue14",
        "blue12_grey12",
    }
    assert _matching(server, pictures, "blue:10", "grey:50") == {"blue14"}


def test_null_and_sentinel_never_match(server, pictures):
    matched = _matching(server, pictures, "blue:0")
    assert "null" not in matched
    assert "sentinel" not in matched


def test_the_route_rejects_a_bad_swatch_with_422(client):
    response = client.get("/api/v1/pictures", params={"colour": "mauve:10"})
    assert response.status_code == 422, response.text


def test_finder_and_task_fill_the_column_and_stop_selecting(server, pictures):
    finder = MissingColourSharesFinder(server.vault.db)

    def candidates(session: Session):
        return {p.id for p in finder._fetch_candidates(session, 1000)}

    before = server.vault.db.run_immediate_read_task(candidates)
    assert {
        pictures["todo_red"],
        pictures["todo_garbage"],
        pictures["todo_missing"],
    } <= before
    # No thumbnail yet, so the finder leaves it for MissingThumbnailFinder.
    assert pictures["null"] not in before
    assert pictures["sentinel"] not in before

    todo = [
        Picture(id=pictures[name], file_path=file_path)
        for name, file_path in (
            ("todo_red", "colour/red.webp"),
            ("todo_garbage", "colour/garbage.webp"),
            ("todo_missing", "colour/missing.webp"),
        )
    ]
    ColourSharesTask(server.vault.db, todo)._run_task()

    def read(session: Session):
        return {
            p.id: p.colour_shares
            for p in session.exec(
                select(Picture).where(Picture.id.in_(pictures.values()))
            ).all()
        }

    stored = server.vault.db.run_immediate_read_task(read)
    red = shares_from_column(stored[pictures["todo_red"]])
    assert red[SWATCH_NAMES.index("red")] == 100.0
    # A failure writes nothing durable: the picture stays NULL and is skipped
    # until its file changes (or the server restarts), never stamped empty.
    assert stored[pictures["todo_garbage"]] is None
    assert stored[pictures["todo_missing"]] is None

    after = server.vault.db.run_immediate_read_task(candidates)
    assert pictures["todo_red"] not in after
    assert pictures["todo_garbage"] not in after
    assert pictures["todo_missing"] not in after


def test_metadata_serves_the_list_and_null(client, pictures):
    response = client.get(f"/api/v1/pictures/{pictures['blue14']}/metadata")
    assert response.status_code == 200, response.text
    shares = response.json()["colour_shares"]
    assert isinstance(shares, list) and len(shares) == 12
    assert shares[SWATCH_NAMES.index("blue")] == 14.0

    for name in ("null", "sentinel"):
        body = client.get(f"/api/v1/pictures/{pictures[name]}/metadata").json()
        assert body["colour_shares"] is None


def test_the_task_never_overwrites_a_value_already_stored(server, pictures):
    # blue14 has a value; handed a readable red thumbnail, the task would write
    # all-red over it if it did not check that the column is still NULL.
    ColourSharesTask(
        server.vault.db,
        [Picture(id=pictures["blue14"], file_path="colour/red.webp")],
    )._run_task()

    def read(session: Session):
        return session.get(Picture, pictures["blue14"]).colour_shares

    assert server.vault.db.run_immediate_read_task(read) == _shares_row(
        blue=14.0, grey=86.0
    )


def test_a_stack_shows_by_its_member_holding_the_colour(server):
    # The cover has no blue; a variant does. The stack must still show, by it.
    def create(session: Session):
        stack = PictureStack()
        session.add(stack)
        session.flush()
        cover = Picture(
            stack_id=stack.id,
            stack_position=0,
            colour_shares=_shares_row(grey=100.0),
        )
        variant = Picture(
            stack_id=stack.id,
            stack_position=1,
            colour_shares=_shares_row(blue=30.0, grey=70.0),
        )
        session.add_all([cover, variant])
        session.commit()
        return stack.id, cover.id, variant.id

    stack_id, cover_id, variant_id = server.vault.db.run_task(create)
    try:

        def leaders(session: Session):
            return {
                p.id
                for p in Picture.find(
                    session,
                    stack_leaders_only=True,
                    colour_filter=["blue:10"],
                    id=[cover_id, variant_id],
                )
            }

        assert server.vault.db.run_immediate_read_task(leaders) == {variant_id}
    finally:

        def remove(session: Session):
            session.exec(delete(Picture).where(Picture.stack_id == stack_id))
            session.exec(delete(PictureStack).where(PictureStack.id == stack_id))
            session.commit()

        server.vault.db.run_task(remove)


def test_the_frontend_draws_the_same_swatches_in_the_same_order():
    # The grid's swatches are these buckets' fills: tuning one side alone
    # would show a colour the filter does not search for.
    path = os.path.join(
        os.path.dirname(__file__), "..", "frontend", "src", "utils", "colourSwatches.js"
    )
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    drawn = re.findall(r'id: "(\w+)", label: "\w+", fill: "(#[0-9a-f]{6})"', source)
    assert drawn == [(name, hex_) for name, hex_ in SWATCHES]


def test_the_tasks_tab_counts_what_is_left(server, pictures):
    count = server.vault._count_missing_colour_shares
    left = server.vault.db.run_immediate_read_task(count)
    # The three thumbnails waiting to be read; "null" has no thumbnail yet.
    assert left >= 3
    ColourSharesTask(
        server.vault.db,
        [Picture(id=pictures["todo_red"], file_path="colour/red.webp")],
    )._run_task()
    assert server.vault.db.run_immediate_read_task(count) == left - 1


def test_a_smart_set_keeps_a_colour_rule_and_refuses_a_bad_one():
    assert normalise_filter("colour=blue%3A10&sort=DATE") == "colour=blue%3A10"
    with pytest.raises(SmartFilterError):
        normalise_filter("colour=mauve%3A10")
