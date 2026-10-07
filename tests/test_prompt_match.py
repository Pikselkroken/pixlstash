"""Prompt match: the method, the finder, the task, and where the verdict is served."""

import tempfile
import zlib

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select, text

from pixlstash.db_models import Picture
from pixlstash.scoring.prompt_match import (
    DISTRACTOR_PROMPTS,
    PROMPT_MATCH_FAILED,
    PROMPT_MATCH_THRESHOLD,
    clean_prompt,
    looks_like_prompt,
    prompt_match_score,
)
from pixlstash.server import Server
from pixlstash.tasks.image_embedding_task import ImageEmbeddingTask
from pixlstash.tasks.missing_prompt_match_finder import MissingPromptMatchFinder
from pixlstash.tasks.prompt_match_task import PromptMatchTask
from pixlstash.tasks.task_type import TaskType

DIM = 16
FOX = np.eye(DIM, dtype=np.float32)[0]


# ── the method ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "prompt, expected",
    [
        ("(a red fox:1.2), snow", "a red fox, snow"),
        ("((red hair)), [blue eyes:0.8], (x:.5)", "red hair, blue eyes, x"),
        ("a castle <lora:detail_tweaker:0.6>, night", "a castle, night"),
        ("embedding:easynegative, embedding:foo:1.2 sunset", "sunset"),
        ("a cat BREAK sitting AND a dog", "a cat, sitting, a dog"),
        # Lower-case "and" is a word, a trigger word is a word.
        ("cats and dogs, zxc_style", "cats and dogs, zxc_style"),
        ("artist \\(style\\)", "artist style"),
        ("<lora:only:1>", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_clean_prompt_keeps_the_words_and_drops_the_steering(prompt, expected):
    assert clean_prompt(prompt) == expected


def _unit(vector):
    return vector / np.linalg.norm(vector)


def test_score_is_the_share_of_distractors_the_prompt_beats():
    bank = np.eye(4, DIM, k=1, dtype=np.float32)  # four texts, none along FOX
    assert prompt_match_score(FOX, FOX, bank) == 1.0
    # Image along the first distractor: the prompt beats the other three.
    image = _unit(FOX * 0.1 + bank[0])
    assert prompt_match_score(image, FOX, bank) == 0.75
    # Unrelated image: every distractor is as close as the prompt (0), ties half.
    assert prompt_match_score(np.eye(DIM)[10], FOX, bank) == 0.5
    # Unnormalised inputs, as stored, give the same answer.
    assert prompt_match_score(FOX * 3, FOX * 0.2, bank * 5) == 1.0


def test_verdict_has_three_states():
    assert looks_like_prompt(None) is None
    assert looks_like_prompt(PROMPT_MATCH_FAILED) is None
    assert looks_like_prompt(PROMPT_MATCH_THRESHOLD) is True
    assert looks_like_prompt(PROMPT_MATCH_THRESHOLD - 0.01) is False
    assert looks_like_prompt(1.0) is True
    assert looks_like_prompt(0.0) is False


# ── the server: finder, task, metadata ───────────────────────────────────────


class FakeClip:
    """CLIP text side: the fox prompts map to FOX, anything else to noise."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def encode_texts(self, texts):
        self.calls.append(list(texts))
        rows = []
        for text_ in texts:
            if text_ in ("masterpiece, a red fox in snow", "a red fox in snow"):
                rows.append(FOX)
            else:
                rng = np.random.default_rng(zlib.crc32(text_.encode()))
                rows.append(_unit(rng.normal(size=DIM)).astype(np.float32))
        return np.stack(rows)


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as temp_dir:
        with Server(f"{temp_dir}/server-config.json") as srv:
            # The real sweeps would score (or re-embed) these file-less rows
            # under the test.
            srv.vault._work_planner.detach_finders(
                [TaskType.PROMPT_MATCH, TaskType.IMAGE_EMBEDDING]
            )
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
def pictures(server, monkeypatch):
    monkeypatch.setattr(PromptMatchTask, "_bank", None)
    monkeypatch.setattr(PromptMatchTask, "_text_cache", {})
    fox = FOX.tobytes()
    rows = {
        "fox": dict(
            comfyui_positive_prompt=(
                "masterpiece, (a red fox in snow:1.2), <lora:fox_v2:0.8>"
            ),
            image_embedding=fox,
        ),
        "off": dict(
            comfyui_positive_prompt="a red fox in snow",
            image_embedding=(-FOX).tobytes(),
        ),
        "syntax_only": dict(
            comfyui_positive_prompt="<lora:only:1>", image_embedding=fox
        ),
        "no_prompt": dict(image_embedding=fox),
        "no_embedding": dict(comfyui_positive_prompt="a red fox in snow"),
        "deleted": dict(
            comfyui_positive_prompt="a red fox in snow",
            image_embedding=fox,
            deleted=True,
        ),
        "scored": dict(
            comfyui_positive_prompt="a red fox in snow",
            image_embedding=fox,
            prompt_match=0.5,
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


def _candidates(server, ids):
    finder = MissingPromptMatchFinder(server.vault.db, engine_getter=lambda: None)
    found = server.vault.db.run_immediate_read_task(finder._fetch_candidates, 1000)
    return {pic.id for pic in found} & set(ids.values())


def _scores(server, ids):
    def fetch(session: Session):
        return {
            pic.id: pic.prompt_match
            for pic in session.exec(
                select(Picture).where(Picture.id.in_(ids.values()))
            ).all()
        }

    stored = server.vault.db.run_immediate_read_task(fetch)
    return {name: stored[pid] for name, pid in ids.items()}


def test_finder_picks_unscored_pictures_with_a_prompt_and_an_embedding(
    server, pictures
):
    assert _candidates(server, pictures) == {
        pictures["fox"],
        pictures["off"],
        pictures["syntax_only"],
    }


def test_the_finder_probe_reads_the_partial_index(server):
    def plan(session: Session):
        compiled = MissingPromptMatchFinder.candidate_query(10).compile(
            session.get_bind(), compile_kwargs={"literal_binds": True}
        )
        return session.exec(text(f"EXPLAIN QUERY PLAN {compiled}")).all()

    details = " ".join(
        str(row[-1]) for row in server.vault.db.run_immediate_read_task(plan)
    )
    assert "ix_picture_prompt_match_missing" in details, details
    assert "TEMP B-TREE" not in details, details


def test_task_scores_the_batch_and_the_metadata_serves_the_verdict(
    server, client, pictures
):
    clip = FakeClip()
    due = [Picture(id=pictures[name]) for name in ("fox", "off", "syntax_only")]
    result = PromptMatchTask(server.vault.db, clip, due)._run_task()

    assert result["changed_count"] == 3
    scores = _scores(server, pictures)
    assert scores["fox"] == 1.0
    assert scores["off"] == 0.0
    assert scores["syntax_only"] == PROMPT_MATCH_FAILED
    assert scores["no_prompt"] is None
    assert scores["scored"] == 0.5
    assert _candidates(server, pictures) == set()
    # The bank once, then the distinct cleaned prompts once.
    assert clip.calls == [
        list(DISTRACTOR_PROMPTS),
        ["a red fox in snow", "masterpiece, a red fox in snow"],
    ]

    def verdict(name):
        response = client.get(f"/pictures/{pictures[name]}/metadata")
        assert response.status_code == 200, response.text
        body = response.json()
        return body["prompt_match"], body["looks_like_prompt"]

    assert verdict("fox") == (1.0, True)
    assert verdict("off") == (0.0, False)
    assert verdict("syntax_only") == (PROMPT_MATCH_FAILED, None)
    assert verdict("no_prompt") == (None, None)


def test_cached_prompts_are_not_encoded_again(server, pictures):
    clip = FakeClip()
    PromptMatchTask(server.vault.db, clip, [Picture(id=pictures["fox"])])._run_task()
    PromptMatchTask(server.vault.db, clip, [Picture(id=pictures["fox"])])._run_task()
    assert clip.calls == [list(DISTRACTOR_PROMPTS), ["masterpiece, a red fox in snow"]]


def test_a_new_image_embedding_queues_the_picture_again(server, pictures):
    server.vault.db.run_task(
        ImageEmbeddingTask._save_results,
        [(pictures["scored"], FOX.tobytes(), None, None)],
    )
    assert _scores(server, pictures)["scored"] is None
    assert pictures["scored"] in _candidates(server, pictures)
