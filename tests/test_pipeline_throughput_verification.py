"""Independent verification of the row-granular pipeline on a real ``Server``.

One module-scoped server with the four GPU stages the throughput plan touched
left in the planner - faces, tags, CLIP, text embeddings - and every other
finder detached (CLAUDE.md, *Tests: reuse the environment*). Descriptions are
switched off in the tagger settings, so the text stage has to run on tags
alone. Each test imports its own pictures and asserts on those ids only: the
gate shards individual tests, so nothing here may depend on another test
having run first.
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from io import BytesIO

import cv2
import numpy as np
import pytest
from PIL import Image
from sqlmodel import select

from pixlstash.db_models import Face, Picture, Tag, is_tag_sentinel
from pixlstash.db_models.tag import make_tag_sentinel
from pixlstash.server import Server
from pixlstash.tasks import missing_face_extraction_finder
from pixlstash.tasks.face_extraction_task import FaceExtractionTask
from pixlstash.tasks.missing_face_extraction_finder import MissingFaceExtractionFinder
from pixlstash.tasks.task_type import TaskType
from tests.utils import upload_pictures_and_wait

API = "/api/v1"
FACE_FINDER = "MissingFaceExtractionFinder"
KEPT = {
    TaskType.FACE_EXTRACTION,
    TaskType.TAGGER,
    TaskType.IMAGE_EMBEDDING,
    TaskType.TEXT_EMBEDDING,
}
PICTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "pictures")
# Cold model loads (InsightFace, the tagger, CLIP, SBERT) all land inside the
# first test to run; a warm pass over a dozen pictures is a few seconds.
PASS_TIMEOUT_S = 300.0


@dataclass
class _Submit:
    type: str
    id: str
    at: float
    face_inflight: int
    picture_ids: tuple


@dataclass
class _Done:
    type: str
    id: str
    started: float
    completed: float
    error: object
    picture_ids: tuple


def _epoch(dt) -> float:
    """``BaseTask`` stamps naive UTC datetimes; the log records are epoch."""
    return calendar.timegm(dt.timetuple()) + dt.microsecond / 1e6


class _PassLog(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.records = []

    def emit(self, record):
        if "[PIPELINE_PASS]" in record.getMessage():
            self.records.append((record.created, record.getMessage()))

    def drains(self, finder: str, since: float) -> list[float]:
        return [
            at
            for at, msg in self.records
            if f"finder={finder} " in msg and "pictures=" in msg and at >= since
        ]


@pytest.fixture(scope="module")
def env():
    tmp = tempfile.TemporaryDirectory()
    cfg = os.path.join(tmp.name, "server-config.json")
    with open(cfg, "w") as fh:
        json.dump({"port": 8000, "trusted_proxies": ["testclient"]}, fh)
    server = Server(cfg)
    server.__enter__()
    planner_logger = logging.getLogger("pixlstash.work_planner")
    previous_level = planner_logger.level
    pass_log = _PassLog()
    vault = server.vault
    runner = vault._task_runner
    original_submit = runner.submit
    submits: list[_Submit] = []
    dones: list[_Done] = []
    lock = threading.Lock()
    try:
        from starlette.testclient import TestClient

        owner = TestClient(server.api, raise_server_exceptions=True)
        login = owner.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
        )
        assert login.status_code == 200, login.text

        # Descriptions off: the text stage must not wait for a caption.
        vault.ensure_ready()
        settings = dict(vault._tagger_settings)
        settings["active_description_plugin"] = None
        vault.set_tagger_settings(settings)
        assert not vault._engine.tagger_settings.get("active_description_plugin")
        assert vault._engine.tagger_settings.get("active_tag_plugin")

        detach = [t for t in vault._planner_work_finders if t not in KEPT]
        for task_type in detach:
            vault._planner_work_finders.pop(task_type)
        vault._work_planner.detach_finders(detach)
        assert vault._work_planner.registered_finder_names() == {
            FACE_FINDER,
            "MissingTagFinder",
            "MissingImageEmbeddingFinder",
            "MissingTextEmbeddingFinder",
        }

        planner = vault._work_planner

        def recording_submit(task):
            with lock:
                submits.append(
                    _Submit(
                        task.type,
                        task.id,
                        time.time(),
                        planner.inflight_count(FACE_FINDER),
                        tuple(task.params.get("picture_ids") or ()),
                    )
                )
            return original_submit(task)

        def on_done(task, error):
            with lock:
                dones.append(
                    _Done(
                        task.type,
                        task.id,
                        _epoch(task.started_at) if task.started_at else 0.0,
                        _epoch(task.completed_at) if task.completed_at else 0.0,
                        error,
                        tuple(task.params.get("picture_ids") or ()),
                    )
                )

        runner.submit = recording_submit
        runner.add_task_complete_callback(on_done)
        # NOTSET inherits the root's WARNING, which drops the INFO pass line
        # before any handler sees it.
        planner_logger.setLevel(logging.INFO)
        planner_logger.addHandler(pass_log)

        yield {
            "server": server,
            "vault": vault,
            "owner": owner,
            "submits": submits,
            "dones": dones,
            "lock": lock,
            "pass_log": pass_log,
        }
    finally:
        planner_logger.removeHandler(pass_log)
        planner_logger.setLevel(previous_level)
        runner.submit = original_submit
        server.__exit__(None, None, None)
        tmp.cleanup()


# ── helpers ─────────────────────────────────────────────────────────────────


def _png(color) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


def _tagger_test_files() -> list[tuple[str, bytes]]:
    names = sorted(
        f
        for f in os.listdir(PICTURES_DIR)
        if f.startswith("TaggerTest") and f.endswith(".png")
    )
    out = []
    for name in names:
        with open(os.path.join(PICTURES_DIR, name), "rb") as fh:
            out.append((name, fh.read()))
    return out


def _import(env, files: list[tuple[str, bytes]]) -> list[int]:
    """Twelve-ish real files through the import route; returns picture ids."""
    payload = [("file", (name, data, "image/png")) for name, data in files]
    status = upload_pictures_and_wait(env["owner"], payload, timeout_s=120)
    assert status["status"] == "completed", status
    ids = [r["picture_id"] for r in status["results"]]
    assert len(ids) == len(files), status
    return ids


def _state(vault, ids: list[int]) -> dict[int, dict]:
    def read(session):
        out = {}
        for pid in ids:
            pic = session.get(Picture, pid)
            faces = session.exec(select(Face).where(Face.picture_id == pid)).all()
            tags = session.exec(select(Tag.tag).where(Tag.picture_id == pid)).all()
            out[pid] = {
                "faces": [(f.frame_index, f.face_index, f.bbox) for f in faces],
                "tags": list(tags),
                "image_embedding": bool(pic.image_embedding),
                "text_embedding": bool(pic.text_embedding),
                "description": pic.description,
            }
        return out

    return vault.db.run_immediate_read_task(read)


def _finished(row: dict) -> bool:
    return (
        bool(row["faces"])
        and bool(row["tags"])
        and not any(is_tag_sentinel(t) for t in row["tags"])
        and row["image_embedding"]
        and row["text_embedding"]
    )


def _wait_for_pass(vault, ids: list[int], timeout_s: float = PASS_TIMEOUT_S) -> dict:
    deadline = time.monotonic() + timeout_s
    while True:
        state = _state(vault, ids)
        if all(_finished(row) for row in state.values()):
            return state
        if time.monotonic() > deadline:
            unfinished = {pid: row for pid, row in state.items() if not _finished(row)}
            pytest.fail(f"the pass never finished for {unfinished}")
        time.sleep(0.2)


def _assert_every_picture_finished(state: dict, faced: set[int], faceless: set[int]):
    """Identity, not counts: each id ends with the rows its content earns."""
    for pid in faced:
        real = [f for f in state[pid]["faces"] if f[1] >= 0 and f[2]]
        assert real, (pid, state[pid]["faces"])
    for pid in faceless:
        assert state[pid]["faces"] == [(0, -1, None)], (pid, state[pid]["faces"])
    for pid, row in state.items():
        assert row["tags"] and not any(is_tag_sentinel(t) for t in row["tags"]), (
            pid,
            row["tags"],
        )
        assert row["image_embedding"], pid
        # Descriptions are off for this server: the text stage ran on tags.
        assert row["description"] in (None, ""), (pid, row["description"])
        assert row["text_embedding"], pid


def _twelve(salt: int) -> tuple[list[tuple[str, bytes]], int]:
    """Ten real face pictures plus two solid greys; returns (files, grey count).

    *salt* recolours one corner pixel of every file: the import de-duplicates
    on pixel content, so two tests importing the same bytes would share one
    set of picture ids instead of each getting its own.
    """
    files = []
    for name, data in _tagger_test_files()[:10]:
        image = Image.open(BytesIO(data)).convert("RGB")
        image.putpixel((0, 0), (salt % 256, (salt * 7) % 256, (salt * 13) % 256))
        buf = BytesIO()
        image.save(buf, format="PNG")
        files.append((f"{salt}-{name}", buf.getvalue()))
    files += [
        (f"{salt}-grey-a.png", _png((128, 128, salt % 256))),
        (f"{salt}-grey-b.png", _png((90, salt % 256, 90))),
    ]
    return files, 2


# ── the pass ────────────────────────────────────────────────────────────────


def test_tag_and_clip_work_is_found_while_the_face_stage_is_in_flight(env, monkeypatch):
    """The removed barriers, end to end on real finders and real models.

    With the face stage cut into six two-picture tasks, the tag finder must
    offer a picture the moment ITS face row lands and the CLIP finder must not
    wait at all - both while the face finder still has tasks in flight, i.e.
    before its ``[PIPELINE_PASS]`` drain. The GPU queue then orders execution:
    HIGH face tasks run ahead of MEDIUM tag/CLIP tasks, so the overlap is that
    the next stage is already queued when the last face task ends, not that a
    tag task pre-empts a queued face task; a task found before the faces were
    (CLIP, in the opening sweep) does run ahead of them.
    """
    vault = env["vault"]
    monkeypatch.setattr(
        missing_face_extraction_finder, "FACE_EXTRACTION_BATCH_LIMIT", 2
    )
    files, greys = _twelve(salt=1)
    since = time.time()
    ids = _import(env, files)
    mine = set(ids)
    state = _wait_for_pass(vault, ids)
    _assert_every_picture_finished(state, set(ids[:-greys]), set(ids[-greys:]))

    with env["lock"]:
        submits = [s for s in env["submits"] if set(s.picture_ids) & mine]
        dones = [d for d in env["dones"] if set(d.picture_ids) & mine]
    face_tasks = [s for s in submits if s.type == "FaceExtractionTask"]
    # >= rather than ==: a finished batch is sometimes re-selected before its
    # rows land (test_pipeline_stage_contracts' xfail), which shows up here as
    # a seventh face task over already-done pictures.
    assert len(face_tasks) >= 6, [(s.type, s.picture_ids) for s in submits]
    assert {pid for s in face_tasks for pid in s.picture_ids} == mine
    assert not [d for d in dones if d.error is not None], dones

    face_drains = env["pass_log"].drains(FACE_FINDER, since)
    assert face_drains, env["pass_log"].records
    face_drain_at = face_drains[-1]

    early_tags = [s for s in submits if s.type == "TagTask" and s.face_inflight > 0]
    assert early_tags, (
        "no tag task was found while face tasks were in flight: "
        f"{[(s.type, s.face_inflight, round(s.at - since, 3)) for s in submits]}"
    )
    assert early_tags[0].at < face_drain_at
    # And each early tag task carried only pictures whose faces were known.
    for tag_submit in early_tags:
        face_done_before = {
            pid
            for d in dones
            if d.type == "FaceExtractionTask" and d.completed <= tag_submit.at
            for pid in d.picture_ids
        }
        assert set(tag_submit.picture_ids) <= face_done_before, tag_submit

    # CLIP needs nothing upstream: its task is found in the same sweeps as the
    # face tasks (the sweep order rotates, so it may even be found first).
    first_face_done = min(d.completed for d in dones if d.type == "FaceExtractionTask")
    clip = [s for s in submits if s.type == "ImageEmbeddingTask"]
    assert clip and clip[0].at < first_face_done < face_drain_at, (
        clip,
        first_face_done - since,
        face_drain_at - since,
    )

    # Whatever was queued first ran first: the CLIP task found in the opening
    # sweep can even run ahead of the face tasks submitted a moment later, and
    # a tag task only ever runs after the face tasks that fed it.
    for d in dones:
        if d.type == "TagTask":
            fed_by = max(
                f.completed
                for f in dones
                if f.type == "FaceExtractionTask"
                and set(f.picture_ids) & set(d.picture_ids)
            )
            assert d.started >= fed_by - 0.005, (d, fed_by)


def test_a_tag_task_starts_before_the_face_stage_drains_when_the_gpu_has_a_gap(
    env, monkeypatch
):
    """Execution-level overlap. With one face task in flight at a time the
    worker sees an empty HIGH queue between face batches and starts the queued
    tag task there - before the face finder has drained."""
    vault = env["vault"]
    monkeypatch.setattr(
        missing_face_extraction_finder, "FACE_EXTRACTION_BATCH_LIMIT", 2
    )
    monkeypatch.setattr(
        MissingFaceExtractionFinder, "max_inflight_tasks", lambda self: 1
    )
    files, greys = _twelve(salt=2)
    since = time.time()
    ids = _import(env, files)
    mine = set(ids)
    state = _wait_for_pass(vault, ids)
    _assert_every_picture_finished(state, set(ids[:-greys]), set(ids[-greys:]))

    with env["lock"]:
        dones = [d for d in env["dones"] if set(d.picture_ids) & mine]
    face_drains = env["pass_log"].drains(FACE_FINDER, since)
    assert face_drains
    face_drain_at = face_drains[-1]
    tag_starts = sorted(d.started for d in dones if d.type == "TagTask")
    assert tag_starts and tag_starts[0] < face_drain_at, (
        f"first tag start {tag_starts[0] - since:.3f}s, "
        f"face drain {face_drain_at - since:.3f}s"
    )
    last_face_done = max(d.completed for d in dones if d.type == "FaceExtractionTask")
    assert tag_starts[0] < last_face_done, "a tag task ran between face batches"


# ── keep_models_in_memory=False ─────────────────────────────────────────────


def _loaded_tagger(vault):
    engine = vault._engine
    active = engine.tagger_settings["active_tag_plugin"]
    service = {
        "pixlstash_tagger": engine.pixlstash_tagger_service,
        "wd14": engine.wd14_service,
    }[active]
    return active, service


def _pending_work(vault) -> dict:
    snapshot = vault._build_worker_progress_snapshot()
    return {
        name: row
        for name, row in snapshot.items()
        if row.get("running")
        and row.get("status") not in ("idle", "stopped", "uninitialized")
        and (row.get("remaining") or 0) > 0
    }


def test_the_idle_sweep_releases_the_models_only_when_progress_is_polled(env):
    """With ``keep_models_in_memory=False`` the tag finder no longer tears the
    tagger down on drain; the ONLY release path is ``Vault.get_worker_progress``
    (the ``/workers/progress`` poll). After a pass the tagger is still resident
    until something polls, and one poll past ``AGGRESSIVE_UNLOAD_INTERVAL``
    frees the tagger, CLIP and InsightFace together."""
    vault = env["vault"]
    vault.set_keep_models_in_memory(False)
    try:
        ids = _import(
            env,
            [("keep-a.png", _png((40, 60, 80))), ("keep-b.png", _png((200, 30, 30)))],
        )
        _wait_for_pass(vault, ids)
        deadline = time.monotonic() + 30
        while _pending_work(vault) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not _pending_work(vault), _pending_work(vault)

        active, tagger = _loaded_tagger(vault)
        assert tagger.is_loaded(), f"{active} was torn down on drain"
        assert vault._engine.clip_service.is_loaded()

        # Rate-limited from the last sweep, not timed from idleness: a poll
        # inside the interval is a no-op even though every worker is idle.
        vault._last_aggressive_unload_at = time.time()
        vault.get_worker_progress()
        assert tagger.is_loaded()

        vault._last_aggressive_unload_at = (
            time.time() - vault.AGGRESSIVE_UNLOAD_INTERVAL - 1
        )
        vault.get_worker_progress()
        assert not tagger.is_loaded(), f"{active} survived the idle sweep"
        assert not vault._engine.clip_service.is_loaded()
        assert FaceExtractionTask._global_insightface_app is None
    finally:
        vault.set_keep_models_in_memory(True)


# ── videos in the face preload pool ─────────────────────────────────────────


def _write_video(path, frames, size):
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10.0, size)
    if not writer.isOpened():
        pytest.skip("no OpenCV video encoder available in this environment")
    for frame in frames:
        writer.write(frame)
    writer.release()


def test_video_faces_land_in_source_pixels_alongside_stills(env):
    """One 1280x720 clip with a known face region and two stills in the same
    face batch: the clip's Face rows are in SOURCE pixels (past the 512 px
    reduced frame), the stills get theirs in the same task, and the clip then
    flows through the rest of the pipeline like any picture."""
    vault = env["vault"]
    image_root = vault.image_root
    face = cv2.imread(os.path.join(PICTURES_DIR, "TaggerTest.png"))
    size = (1280, 720)
    region = (800, 300, 1056, 500)  # x0, y0, x1, y1 in source pixels
    frame = np.full((size[1], size[0], 3), (128, 128, 128), dtype=np.uint8)
    frame[region[1] : region[3], region[0] : region[2]] = cv2.resize(
        face, (region[2] - region[0], region[3] - region[1])
    )
    _write_video(os.path.join(image_root, "wide-clip.mp4"), [frame] * 30, size)
    for name, colour in (
        ("clip-still-a.png", (10, 10, 10)),
        ("clip-still-b.png", (250, 250, 250)),
    ):
        Image.new("RGB", (64, 64), colour).save(os.path.join(image_root, name))

    def add(session):
        rows = [
            Picture(file_path="wide-clip.mp4", width=size[0], height=size[1]),
            Picture(file_path="clip-still-a.png", width=64, height=64),
            Picture(file_path="clip-still-b.png", width=64, height=64),
        ]
        session.add_all(rows)
        session.flush()
        for row in rows:
            session.add(Tag(picture_id=row.id, tag=make_tag_sentinel()))
        session.commit()
        return [row.id for row in rows]

    video_id, still_a, still_b = ids = vault.db.run_task(add)
    vault._work_planner.wake()
    state = _wait_for_pass(vault, ids)
    _assert_every_picture_finished(state, {video_id}, {still_a, still_b})

    with env["lock"]:
        face_tasks = [
            d
            for d in env["dones"]
            if d.type == "FaceExtractionTask" and set(d.picture_ids) & set(ids)
        ]
    assert len(face_tasks) == 1 and set(face_tasks[0].picture_ids) == set(ids), (
        "the clip and the stills were meant to share one batch",
        [t.picture_ids for t in face_tasks],
    )

    rows = state[video_id]["faces"]
    assert [r[0] for r in rows] == [0, 10, 20], rows
    for _frame, _index, bbox in rows:
        assert (
            0 <= bbox[0] < bbox[2] <= size[0] and 0 <= bbox[1] < bbox[3] <= size[1]
        ), bbox
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        assert region[0] < cx < region[2] and region[1] < cy < region[3], bbox
        # Past the reduced frame: proof the box was scaled back to the source.
        assert bbox[2] > FaceExtractionTask.INFERENCE_MAX_SIDE, bbox
