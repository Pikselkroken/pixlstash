"""Text read out of pictures (#1197): matching, search, the text routes, the task."""

import json
import tempfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select, text

from pixlstash.database import (
    OCR_TEXT_MATCH_WEIGHT,
    ocr_query_terms,
    ocr_text_match,
    ocr_word_matches,
)
from pixlstash.db_models import Picture, Tag
from pixlstash.server import Server
from pixlstash.tagger_plugins.florence2 import _words_from_ocr_regions
from pixlstash.tasks.base_task import TaskPriority
from pixlstash.tasks.ocr_task import OCR_MIN_TEXT_SCORE, OcrTask

RECEIPT = (
    "BAKERY No.4\nHARBOUR ST. 12\n14/09/2026 10:42\nSourdough 6.50\n"
    "Croissant x2 5.00\nC0FFEE 3.80\nFlat white 4.20\nRhubarb tart 4.80\n"
    "TOTAL 24.30\nCARD 24.30\nTHANK YOU!\nSEE YOU SOON"
)
MENU = "MENU\nEspresso 3.00\nCoffee 3.50\nTea 3.00\nCake 4.50"
SCREENSHOT = (
    "Settings\nNotifications\nAllow notifications from this application\n"
    "Show previews Always\nSound Default\nBadges On\nLock screen Show"
)

# Queries that must NOT match any of the known text-heavy pictures above. A
# regression that lets a page of words answer searches it has nothing to do
# with shows up here first: near misses of words they do carry ("cart" against
# CARD, "cafe" against CAKE), short words, and ordinary photo searches.
MUST_NOT_MATCH = [
    "cat",
    "cart",
    "cafe",
    "rake",
    "moon",
    "shank",
    "sound effects",
    "portrait of a woman",
    "red car",
    "coffee shop",
    "notification bell",
    "tea party",
]


def _lines(*rows):
    return [
        [
            {"text": word, "box": [0.1 * i, 0.1 * r, 0.05, 0.02]}
            for i, word in enumerate(row.split())
        ]
        for r, row in enumerate(rows)
    ]


# ── matching ─────────────────────────────────────────────────────────────────


def test_misread_word_matches_but_short_words_must_be_exact():
    assert ocr_word_matches("coffee", "C0FFEE")
    assert ocr_word_matches("thank", "THANK")
    assert ocr_word_matches("you", "YOU!")
    assert not ocr_word_matches("cart", "CARD")
    assert not ocr_word_matches("cafe", "Cake")
    assert not ocr_word_matches("coffee", "toffees")
    # One edit is allowed from five letters, but never on the first letter.
    assert ocr_word_matches("loose", "L0OSE")
    assert not ocr_word_matches("loose", "goose")
    assert not ocr_word_matches("shank", "THANK")


def test_query_terms_drop_stopwords_unless_nothing_is_left():
    assert ocr_query_terms("receipt from the bakery") == ("receipt", "bakery")
    assert ocr_query_terms("of") == ("of",)


def test_every_query_word_must_appear():
    assert ocr_text_match(RECEIPT, "coffee") == 1.0
    assert ocr_text_match(RECEIPT, "flat white") == 1.0
    assert ocr_text_match(RECEIPT, "flat espresso") == 0.0
    assert ocr_text_match(MENU, "espresso coffee") == 1.0
    assert ocr_text_match(None, "coffee") == 0.0
    assert ocr_text_match("", "coffee") == 0.0


@pytest.mark.parametrize("query", MUST_NOT_MATCH)
def test_known_text_heavy_pictures_do_not_answer_unrelated_queries(query):
    for page in (RECEIPT, MENU, SCREENSHOT):
        assert ocr_text_match(page, query) == 0.0, (query, page.splitlines()[0])


# ── word boxes from Florence line regions ────────────────────────────────────


def test_line_regions_split_into_word_boxes_by_character_share():
    lines = _words_from_ocr_regions(
        quad_boxes=[
            [40, 246, 278, 246, 278, 282, 40, 282],
            [40, 47, 282, 47, 282, 81, 40, 81],
            [0, 0, 0, 0, 0, 0, 0, 0],
            [10, 10, 50, 10, 50, 20, 10, 20],
        ],
        labels=["  COFFEE 3.80 ", "</s>BAKERY No.4", "degenerate", "   "],
        image_size=(800, 600),
    )
    assert [[w["text"] for w in line] for line in lines] == [
        ["BAKERY", "No.4"],
        ["COFFEE", "3.80"],
    ]
    bakery, no4 = lines[0]
    # "BAKERY No.4" is 11 characters over 242 px: 22 px a character.
    assert bakery["box"] == [
        0.05,
        round(47 / 600, 4),
        round(132 / 800, 4),
        round(34 / 600, 4),
    ]
    assert no4["box"][0] == round((40 + 7 * 22) / 800, 4)
    assert no4["box"][2] == round(4 * 22 / 800, 4)
    # Padding around a label takes no width: "COFFEE 3.80" is 11 characters
    # over 238 px, starting at the box's left edge.
    coffee, _ = lines[1]
    assert coffee["box"][0] == 0.05
    assert coffee["box"][2] == round(6 * 238 / 11 / 800, 4)


# ── the server: search, the text routes, the task ────────────────────────────


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


@pytest.fixture(autouse=True)
def pictures(server, monkeypatch):
    """Four pictures with no file, so no background finder picks them up.

    No embeddings either: search scores come from tags and text alone.
    """
    monkeypatch.setattr(server.vault, "generate_text_embedding", lambda q: None)
    monkeypatch.setattr(server.vault, "generate_clip_text_embedding", lambda q: None)

    def create(session: Session):
        rows = {
            "receipt": Picture(
                ocr_text=RECEIPT,
                ocr_words=json.dumps(_lines(*RECEIPT.splitlines())),
                text_score=0.8,
            ),
            "menu": Picture(
                ocr_text=MENU,
                ocr_words=json.dumps(_lines(*MENU.splitlines())),
                text_score=0.6,
            ),
            "photo": Picture(text_score=0.0),
            "unread": Picture(text_score=OCR_MIN_TEXT_SCORE),
        }
        for pic in rows.values():
            session.add(pic)
        session.commit()
        ids = {name: pic.id for name, pic in rows.items()}
        session.add(Tag(picture_id=ids["photo"], tag="coffee"))
        session.commit()
        return ids

    ids = server.vault.db.run_task(create)
    yield ids

    def remove(session: Session):
        session.exec(delete(Tag).where(Tag.picture_id.in_(ids.values())))
        session.exec(delete(Picture).where(Picture.id.in_(ids.values())))
        session.commit()

    server.vault.db.run_task(remove)


def _search(client, query):
    response = client.get(f"/pictures/search?query={query}&threshold=0.01")
    assert response.status_code == 200, response.text
    return {row["id"]: row for row in response.json()}


def test_search_flags_text_matches_and_counts_them_in_the_score(
    server, client, pictures
):
    rows = _search(client, "coffee")

    assert rows[pictures["receipt"]]["text_match"] is True
    assert rows[pictures["menu"]]["text_match"] is True
    assert rows[pictures["photo"]]["text_match"] is False
    # With no tags or embeddings, the receipt's whole score is the text weight.
    assert rows[pictures["receipt"]]["likeness_score"] == pytest.approx(
        OCR_TEXT_MATCH_WEIGHT
    )


@pytest.mark.parametrize("query", ["sunset beach", "cart", "notification bell"])
def test_search_does_not_return_text_heavy_pictures_for_unrelated_queries(
    server, client, pictures, query
):
    rows = _search(client, query)
    assert pictures["receipt"] not in rows
    assert pictures["menu"] not in rows
    assert not any(row["text_match"] for row in rows.values())


def test_search_and_metadata_rows_do_not_carry_the_text(server, client, pictures):
    row = _search(client, "coffee")[pictures["receipt"]]
    assert "ocr_text" not in row and "ocr_words" not in row
    meta = client.get(f"/pictures/{pictures['receipt']}/metadata").json()
    assert "ocr_text" not in meta and "ocr_words" not in meta


def test_text_route_states_and_matched_words(server, client, pictures):

    read = client.get(f"/pictures/{pictures['receipt']}/text?query=coffee").json()
    assert read["state"] == "read"
    words = [word for line in read["lines"] for word in line]
    assert [w["text"] for w in words if w["matched"]] == ["C0FFEE"]
    assert words[0] == {
        "text": "BAKERY",
        "box": [0.0, 0.0, 0.05, 0.02],
        "matched": False,
    }

    unmarked = client.get(f"/pictures/{pictures['receipt']}/text").json()
    assert not any(w["matched"] for line in unmarked["lines"] for w in line)

    assert client.get(f"/pictures/{pictures['unread']}/text").json() == {
        "state": "pending",
        "lines": [],
    }
    assert client.get(f"/pictures/{pictures['photo']}/text").json() == {
        "state": "none",
        "lines": [],
    }
    assert client.get("/pictures/999999999/text").status_code == 404


def test_read_again_keeps_the_text_and_queues_an_urgent_read(
    server, client, pictures, monkeypatch
):
    submitted = []
    monkeypatch.setattr(server.vault, "_engine", object(), raising=False)
    monkeypatch.setattr(
        server.vault, "submit_task", lambda task: submitted.append(task) or "t1"
    )
    _set_files(server, {pictures["receipt"]: "/home/me/receipt.png"})

    response = client.post(f"/pictures/{pictures['receipt']}/text/read")

    assert response.status_code == 200, response.text
    assert response.json() == {"state": "pending", "lines": []}
    assert [task.params["picture_ids"] for task in submitted] == [[pictures["receipt"]]]
    assert submitted[0].priority == TaskPriority.URGENT
    # Kept until the new read succeeds, so a failed read loses nothing.
    assert client.get(f"/pictures/{pictures['receipt']}/text").json()["state"] == "read"
    # No file, or no such picture: nothing to read.
    assert client.post(f"/pictures/{pictures['menu']}/text/read").status_code == 404


def test_task_stores_text_and_marks_unreadable_pictures_read(server, client, pictures):
    lines = _lines("OPEN 8-18")

    class FakeEngine:
        def read_text(self, paths):
            return {path: lines for path in paths}

    def load(session: Session):
        pics = session.exec(
            select(Picture).where(
                Picture.id.in_([pictures["photo"], pictures["unread"]])
            )
        ).all()
        for pic in pics:
            pic.file_path = (
                "/home/me/shop.png"
                if pic.id == pictures["photo"]
                else "/home/me/clip.mp4"
            )
            session.add(pic)
        session.commit()
        return session.exec(
            select(Picture).where(
                Picture.id.in_([pictures["photo"], pictures["unread"]])
            )
        ).all()

    pics = server.vault.db.run_task(load)
    result = OcrTask(server.vault.db, FakeEngine(), list(pics))._run_task()

    assert result["changed_count"] == 2

    def fetch(session: Session):
        return {
            pic.id: (pic.ocr_text, pic.ocr_words)
            for pic in session.exec(
                select(Picture).where(
                    Picture.id.in_([pictures["photo"], pictures["unread"]])
                )
            ).all()
        }

    stored = server.vault.db.run_immediate_read_task(fetch)
    assert stored[pictures["photo"]] == ("OPEN 8-18", json.dumps(lines))
    assert stored[pictures["unread"]] == ("", None)


def test_a_picture_the_reader_skipped_in_a_good_batch_is_stored_empty(server, pictures):
    lines = _lines("OPEN 8-18")

    class PartialEngine:
        def read_text(self, paths):
            return {"/home/me/shop.png": lines}

    pics = _set_files(
        server,
        {
            pictures["photo"]: "/home/me/shop.png",
            pictures["unread"]: "/home/me/bad.png",
        },
    )
    OcrTask(server.vault.db, PartialEngine(), list(pics))._run_task()

    def fetch(session: Session):
        return {
            pid: session.get(Picture, pid).ocr_text
            for pid in (pictures["photo"], pictures["unread"])
        }

    assert server.vault.db.run_immediate_read_task(fetch) == {
        pictures["photo"]: "OPEN 8-18",
        pictures["unread"]: "",
    }


def _set_files(server, ids_to_paths):
    def update(session: Session):
        for pid, path in ids_to_paths.items():
            pic = session.get(Picture, pid)
            pic.file_path = path
            session.add(pic)
        session.commit()
        return session.exec(select(Picture).where(Picture.id.in_(ids_to_paths))).all()

    return server.vault.db.run_task(update)


def test_words_are_marked_only_when_every_query_word_matched(client, pictures):
    partial = client.get(
        f"/pictures/{pictures['receipt']}/text?query=flat espresso"
    ).json()
    assert partial["state"] == "read"
    assert not any(w["matched"] for line in partial["lines"] for w in line)
    full = client.get(f"/pictures/{pictures['receipt']}/text?query=flat white").json()
    assert [w["text"] for line in full["lines"] for w in line if w["matched"]] == [
        "Flat",
        "white",
    ]


@pytest.mark.parametrize(
    "stored",
    [
        "not json",
        "[1]",
        '[[{"box": [0, 0, 1, 1]}]]',
        '[[{"text": "SALE", "box": [0, 0, 1]}]]',
    ],
)
def test_malformed_word_boxes_read_as_no_text(server, client, pictures, stored):
    def corrupt(session: Session):
        pic = session.get(Picture, pictures["menu"])
        pic.ocr_words = stored
        session.add(pic)
        session.commit()

    server.vault.db.run_task(corrupt)
    response = client.get(f"/pictures/{pictures['menu']}/text")
    assert response.status_code == 200
    assert response.json() == {"state": "none", "lines": []}


def test_a_deleted_picture_is_never_pending(server, client, pictures):
    def delete_unread(session: Session):
        pic = session.get(Picture, pictures["unread"])
        pic.deleted = True
        session.add(pic)
        session.commit()

    assert (
        client.get(f"/pictures/{pictures['unread']}/text").json()["state"] == "pending"
    )
    server.vault.db.run_task(delete_unread)
    assert client.get(f"/pictures/{pictures['unread']}/text").json()["state"] == "none"


def test_finder_selects_only_unread_pictures_that_look_like_text(server, pictures):
    _set_files(
        server,
        {
            pictures["unread"]: "/home/me/unread.png",
            pictures["photo"]: "/home/me/photo.png",
            pictures["receipt"]: "/home/me/receipt.png",
        },
    )
    found = server.vault.db.run_immediate_read_task(OcrTask.find_unread_pictures, 50)
    # photo scores 0, receipt is already read, menu has no file.
    assert {pic.id for pic in found} & set(pictures.values()) == {pictures["unread"]}


def test_a_batch_the_reader_returns_nothing_for_stays_unread(server, pictures):
    class BrokenEngine:
        def read_text(self, paths):
            return {}

    pics = _set_files(server, {pictures["unread"]: "/home/me/unread.png"})
    with pytest.raises(RuntimeError):
        OcrTask(server.vault.db, BrokenEngine(), list(pics))._run_task()

    def fetch(session: Session):
        return session.get(Picture, pictures["unread"]).ocr_text

    assert server.vault.db.run_immediate_read_task(fetch) is None


def _token(client, scope, picture_id):
    response = client.post(
        "/users/me/token",
        json={
            "description": "test-ocr",
            "scope": scope,
            "resource_type": "picture",
            "resource_id": picture_id,
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_text_routes_follow_the_token_scope(server, client, pictures):
    anon = TestClient(server.api)
    read = _token(client, "READ", pictures["receipt"])

    # In scope: served. Out of scope: refused.
    assert (
        anon.get(f"/pictures/{pictures['receipt']}/text", headers=read).status_code
        == 200
    )
    assert (
        anon.get(f"/pictures/{pictures['menu']}/text", headers=read).status_code == 403
    )
    # A READ token cannot start a read, even on its own picture.
    assert (
        anon.post(
            f"/pictures/{pictures['receipt']}/text/read", headers=read
        ).status_code
        == 403
    )


def test_a_failed_batch_is_deferred_but_a_cancelled_one_is_not(server, pictures):
    from pixlstash.task_runner import TaskCancelledError
    from pixlstash.tasks.missing_ocr_finder import MissingOcrFinder

    _set_files(server, {pictures["unread"]: "/home/me/unread.png"})
    finder = MissingOcrFinder(server.vault.db, engine_getter=lambda: object())
    task = SimpleNamespace(params={"picture_ids": [pictures["unread"]]})

    def candidate_ids():
        found = server.vault.db.run_immediate_read_task(finder._fetch_candidates, 50)
        return {pic.id for pic in found}

    finder.on_task_complete(task, TaskCancelledError("stopped"))
    assert pictures["unread"] in candidate_ids()
    finder.on_task_complete(task, RuntimeError("reader returned nothing"))
    assert pictures["unread"] not in candidate_ids()


def test_text_match_flags_are_looked_up_only_for_the_ids_given(server, pictures):
    def lookup(session: Session, ids):
        return Picture.ids_matching_text(session, "coffee", ids)

    run = server.vault.db.run_immediate_read_task
    assert run(lookup, [pictures["receipt"], pictures["photo"]]) == {
        pictures["receipt"]
    }
    assert run(lookup, [pictures["photo"]]) == set()


def test_the_finder_probe_reads_the_partial_index(server):
    def plan(session: Session):
        statement = OcrTask._unread_query(select(Picture.id)).order_by(
            Picture.text_score.desc()
        )
        compiled = statement.compile(
            session.get_bind(), compile_kwargs={"literal_binds": True}
        )
        return session.exec(text(f"EXPLAIN QUERY PLAN {compiled}")).all()

    details = " ".join(
        str(row[-1]) for row in server.vault.db.run_immediate_read_task(plan)
    )
    assert "ix_picture_ocr_unread" in details, details
    assert "TEMP B-TREE" not in details, details


def test_reading_waits_for_scoring_only_and_runs_with_captioning_off(server, pictures):
    """Reading is neither queued behind captioning nor switched off with it.

    Both together stalled the sweep for good: the planner holds a finder until
    every finder it depends on reports no work left, so `DESCRIPTION` blocked
    reading while any picture was uncaptioned, and switching captioning off to
    clear that closed the guard instead.
    """
    from pixlstash.tasks.missing_ocr_finder import MissingOcrFinder
    from pixlstash.tasks.task_type import TaskType

    assert (
        TaskType.DESCRIPTION
        not in MissingOcrFinder(
            server.vault.db, engine_getter=lambda: None
        ).depends_on()
    )

    _set_files(server, {pictures["unread"]: "/home/me/unread.png"})
    captioning_off = SimpleNamespace(tagger_settings={"active_description_plugin": ""})
    finder = MissingOcrFinder(server.vault.db, engine_getter=lambda: captioning_off)

    task = finder.find_task()
    assert task is not None
    assert task.params["picture_ids"] == [pictures["unread"]]

    # No engine is the one thing that does stop it: the task needs one to read.
    assert (
        MissingOcrFinder(server.vault.db, engine_getter=lambda: None).find_task()
        is None
    )


def test_the_bar_only_has_to_clear_zero():
    """Measured over a library of 12k pictures, not chosen by taste.

    98.1% of them score exactly 0.0: the scorer's hard gates, not this
    constant, are what select a picture for reading. Dense phone screenshots
    scored 0.16-0.36 (a screenful of chat text: 0.23), so at 0.25 two of 81
    screen-sized pictures qualified and a random sample of 1200 produced none.
    A bar just above zero admits about 1.9% of a library.
    """
    assert 0 < OCR_MIN_TEXT_SCORE <= 0.05
