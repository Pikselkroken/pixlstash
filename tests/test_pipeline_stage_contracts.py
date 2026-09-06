"""Reviewer checklist (throughput plan §8) and stage-boundary regressions.

Mechanical checks that the row-granular pipeline kept its invariants: one GPU
worker, every CUDA ORT session bounded by the budget, the two maintenance
finders still gated on a global barrier - plus what happens at a stage boundary
when the upstream task raises or its rows have not landed yet. No Server, no
GPU, no model: these run on a bare ``Vault`` and on the source tree.
"""

from __future__ import annotations

import io
import re
import tokenize
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import Face, Picture, Tag
from pixlstash.db_models.tag import make_tag_sentinel
from pixlstash.tasks.face_extraction_task import FaceExtractionTask
from pixlstash.tasks.missing_face_extraction_finder import MissingFaceExtractionFinder
from pixlstash.tasks.missing_face_model_refresh_finder import (
    MissingFaceModelRefreshFinder,
)
from pixlstash.tasks.missing_tag_finder import MissingTagFinder
from pixlstash.tasks.missing_tag_prediction_finder import MissingTagPredictionFinder
from pixlstash.tasks.task_type import TaskType
from pixlstash.vault import Vault

PACKAGE = Path(__file__).resolve().parent.parent / "pixlstash"


def _code_lines(path: Path):
    """``(line_number, text)`` for every line of *path* with comments and
    string literals (docstrings included) blanked out."""
    source = path.read_text()
    masked = list(source)
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        (start_row, start_col), (end_row, end_col) = token.start, token.end
        lines = source.splitlines(keepends=True)
        offset = sum(len(ln) for ln in lines[: start_row - 1]) + start_col
        end = sum(len(ln) for ln in lines[: end_row - 1]) + end_col
        for index in range(offset, end):
            if masked[index] != "\n":
                masked[index] = " "
    return list(enumerate("".join(masked).splitlines(), 1))


# ── §8: the single GPU worker ───────────────────────────────────────────────


def test_task_runner_starts_exactly_one_gpu_worker(tmp_path):
    with Vault(image_root=str(tmp_path)) as vault:
        runner = vault._task_runner
        runner.start()
        try:
            gpu_threads = [t for t in runner._threads if t.name.endswith("-gpu")]
            assert len(gpu_threads) == 1, [t.name for t in runner._threads]
            # A second start() must not add another.
            runner.start()
            assert [
                t for t in runner._threads if t.name.endswith("-gpu")
            ] == gpu_threads
        finally:
            runner.stop()


def test_no_task_module_reaches_cuda_outside_the_insightface_init():
    """Only the InsightFace initialiser - documented to run on the GPU worker -
    names CUDA in ``pixlstash/tasks``. Every other task reaches the device
    through the engine's workflows, which the single GPU worker serialises."""
    hits = {}
    for path in sorted((PACKAGE / "tasks").glob("*.py")):
        for number, line in _code_lines(path):
            if re.search(r"torch\.cuda\.|\.cuda\(\)|CUDAExecutionProvider", line):
                hits.setdefault(path.name, []).append(number)
    assert set(hits) == {"face_extraction_task.py"}, hits
    source = (PACKAGE / "tasks" / "face_extraction_task.py").read_text()
    init_start = source.index("def get_or_init_insightface")
    init_end = source.index("def _init_insightface_app")
    for number in hits["face_extraction_task.py"]:
        offset = sum(len(ln) + 1 for ln in source.splitlines()[: number - 1])
        assert init_start <= offset < init_end, (
            f"face_extraction_task.py:{number} touches CUDA outside get_or_init_insightface"
        )


# ── §8: every ORT CUDA session is budget-bounded ────────────────────────────


def test_every_cuda_ort_session_site_is_built_from_the_budget():
    """Lists the creation sites rather than trusting a grep: two CPU-only sites
    need no cap, the OpenVINO site has no CUDA arena, and the two CUDA sites
    must take their options from ``VramBudget.ort_cuda_provider_options``
    (which `test_vram_batch_budget` proves carries ``gpu_mem_limit`` whenever
    a budget is set)."""
    sites = []
    for path in sorted(PACKAGE.rglob("*.py")):
        lines = path.read_text().splitlines()
        for number, line in _code_lines(path):
            if re.search(r"\b(InferenceSession|FaceAnalysis)\(", line):
                window = "\n".join(lines[number - 1 : number + 5])
                sites.append((path.relative_to(PACKAGE).as_posix(), number, window))
    by_file = {}
    for rel, number, window in sites:
        by_file.setdefault(rel, []).append((number, window))
    assert set(by_file) == {
        "tagger_plugins/wd14.py",
        "tasks/face_extraction_task.py",
    }, sorted(by_file)

    def classify(window: str) -> str:
        if 'providers=["CPUExecutionProvider"]' in window:
            return "cpu"
        if "ort_cuda_provider_options" in window or "cuda_options" in window:
            return "cuda-budgeted"
        if "provider_options=provider_options" in window:
            return "cuda-budgeted"
        if "OpenVINO" in window:
            return "openvino"
        return "UNBOUNDED"

    kinds = {
        rel: sorted(classify(w) for _n, w in entries)
        for rel, entries in by_file.items()
    }
    assert kinds["tagger_plugins/wd14.py"] == ["cpu", "cuda-budgeted", "openvino"], (
        kinds
    )
    assert kinds["tasks/face_extraction_task.py"] == ["cpu", "cuda-budgeted"], kinds
    # And the CUDA FaceAnalysis site's options really come from the budget.
    face_source = (PACKAGE / "tasks" / "face_extraction_task.py").read_text()
    assert "engine.vram_budget.ort_cuda_provider_options(" in face_source


# ── §8: the maintenance finders keep their global gate ──────────────────────


def test_the_two_maintenance_finders_still_declare_depends_on():
    refresh = MissingFaceModelRefreshFinder(database=None, engine_getter=lambda: None)
    backfill = MissingTagPredictionFinder(database=None, engine_getter=lambda: None)
    assert refresh.depends_on() == [TaskType.FACE_EXTRACTION]
    assert backfill.depends_on() == [TaskType.FACE_EXTRACTION, TaskType.TAGGER]


# ── stage boundaries ────────────────────────────────────────────────────────


def _seed_pending(vault, tmp_path, names: list[str]) -> list[int]:
    """Pictures with a pending-tag sentinel and no Face row: fresh imports."""

    def seed(session: Session):
        ids = []
        for name in names:
            Image.fromarray(np.zeros((30, 40, 3), dtype=np.uint8), "RGB").save(
                tmp_path / name
            )
            picture = Picture(file_path=name, format="png", width=40, height=30)
            session.add(picture)
            session.flush()
            session.add(Tag(picture_id=picture.id, tag=make_tag_sentinel()))
            ids.append(picture.id)
        session.commit()
        return ids

    return vault.db.run_task(seed)


def _tag_candidates(vault) -> set[int]:
    rows = vault.db.run_immediate_read_task(
        lambda s: MissingTagFinder._fetch_missing_tags(s, 64)
    )
    return {p.id for p in rows}


def _face_rows(vault, picture_id: int) -> list[Face]:
    return vault.db.run_immediate_read_task(
        lambda s: s.exec(select(Face).where(Face.picture_id == picture_id)).all()
    )


class _Engine:
    keep_models_in_memory = True


def test_a_face_task_that_raises_leaves_its_pictures_to_the_face_stage(
    tmp_path, monkeypatch
):
    """The tag stage keys on a Face row - real or the ``face_index=-1``
    sentinel - because that row is the only thing that means "face extraction
    has run". A task that raises writes neither, so its pictures are not tagged
    on an unknown face state; they are re-offered to the face stage (the claims
    come back on the failure path), and - the part the old global barrier got
    wrong - a sibling whose faces are known is tagged meanwhile."""
    with Vault(image_root=str(tmp_path)) as vault:
        broken_a, broken_b, sibling = _seed_pending(
            vault, tmp_path, ["a.png", "b.png", "c.png"]
        )
        finder = MissingFaceExtractionFinder(vault.db, lambda: _Engine())

        def boom(self):
            raise RuntimeError("model pack download failed")

        monkeypatch.setattr(FaceExtractionTask, "_run_task", boom)
        task = finder.find_task()
        assert task is not None
        assert set(task.params["picture_ids"]) == {broken_a, broken_b, sibling}
        with pytest.raises(RuntimeError):
            task.run()
        finder.on_task_complete(task, RuntimeError("model pack download failed"))

        for pid in (broken_a, broken_b, sibling):
            assert _face_rows(vault, pid) == [], "a raise writes no sentinel"
        assert _tag_candidates(vault) == set(), "unknown faces are not tagged"

        # The face stage owns the retry: the same pictures come straight back.
        retry = finder.find_task()
        assert retry is not None
        assert set(retry.params["picture_ids"]) == {broken_a, broken_b, sibling}
        finder.on_task_complete(retry, RuntimeError("still failing"))

        # A sibling whose faces ARE known is not held hostage by the failure.
        vault.db.run_task(
            lambda s: (s.add(Face(picture_id=sibling, face_index=-1)), s.commit())
        )
        assert _tag_candidates(vault) == {sibling}


def test_a_finished_face_batch_is_not_re_selected_while_its_rows_are_in_flight(
    tmp_path, monkeypatch
):
    with Vault(image_root=str(tmp_path)) as vault:
        ids = _seed_pending(vault, tmp_path, ["a.png", "b.png"])
        finder = MissingFaceExtractionFinder(vault.db, lambda: _Engine())
        task = finder.find_task()
        assert task is not None and set(task.params["picture_ids"]) == set(ids)

        # Skip InsightFace: hand back one sentinel row per picture, the same
        # shape the real extractor produces for a faceless image.
        def fake_extract(self, pics, **_kwargs):
            faces = [Face(picture_id=p.id, face_index=-1) for p in pics]
            return [(Picture, p.id, "faces", None) for p in pics], faces, []

        monkeypatch.setattr(FaceExtractionTask, "_extract_features", fake_extract)

        # Occupy the single writer thread with a LOW-priority write that is
        # already running, so the task's HIGH-priority flush has to queue.
        started = threading.Event()
        release = threading.Event()

        def slow_write(_session):
            started.set()
            release.wait(10)

        blocker = vault.db.submit_task(slow_write, priority=DBPriority.LOW)
        assert started.wait(5)
        try:
            task.run()
            finder.on_task_complete(task, None)  # what TaskRunner does next
            resubmitted = finder.find_task()
            re_selected = (
                set(resubmitted.params["picture_ids"]) if resubmitted else set()
            )
        finally:
            release.set()
            blocker.result(timeout=10)

        if resubmitted is not None:
            finder.on_task_complete(resubmitted, None)
        # And once the rows have landed the claims are let go - the deferred
        # release must not turn into a permanent one.
        deadline = time.monotonic() + 5
        while finder._claimed_picture_ids and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not finder._claimed_picture_ids, "claims held after the write landed"
        assert re_selected & set(ids) == set(), (
            f"pictures {sorted(re_selected & set(ids))} were offered to a second "
            "face task before their rows had landed"
        )


def test_a_full_card_is_never_recorded_as_no_faces():
    """An ORT allocation failure must propagate to the runner's OOM retry.

    Swallowed, it returned "no faces" for the whole batch, and inside
    FaceExtractionTask that is a sentinel row per picture - permanent, because
    sentinels are never re-scanned. Seen 2026-08-27 with another process
    holding the card: 70 pictures in four batches, all reported faceless.
    """
    import numpy as np

    from pixlstash.tasks.face_extraction_task import FaceExtractionTask

    class _FullCardApp:
        pass

    class _Runner:
        def __init__(self, _app):
            pass

        def run_batch(self, _images):
            raise RuntimeError(
                "[ONNXRuntimeError] : 1 : FAIL : Failed to allocate memory for "
                "requested buffer of size 64241920"
            )

    import pixlstash.tasks.face_extraction_task as module

    original = module.BatchedFaceRunner
    module.BatchedFaceRunner = _Runner
    try:
        image = np.zeros((256, 256, 3), dtype=np.uint8)
        with pytest.raises(RuntimeError, match="Failed to allocate memory"):
            FaceExtractionTask.detect_faces_in_images(_FullCardApp(), [image, image])
    finally:
        module.BatchedFaceRunner = original


def test_a_non_memory_detector_failure_still_degrades_to_no_faces():
    """The swallow stays for what it was written for: a genuinely bad image."""
    import numpy as np

    from pixlstash.tasks.face_extraction_task import FaceExtractionTask
    import pixlstash.tasks.face_extraction_task as module

    class _Runner:
        def __init__(self, _app):
            pass

        def run_batch(self, _images):
            raise ValueError("cv2 could not resize a degenerate frame")

    original = module.BatchedFaceRunner
    module.BatchedFaceRunner = _Runner
    try:
        image = np.zeros((256, 256, 3), dtype=np.uint8)
        assert FaceExtractionTask.detect_faces_in_images(object(), [image]) == [[]]
    finally:
        module.BatchedFaceRunner = original


# ── the video path is the still path ────────────────────────────────────────


class _RecordingDetector:
    """Stands in for RetinaFace and records every frame handed to it."""

    def __init__(self):
        self.seen = []

    def detect(self, img):
        self.seen.append(img.shape)
        return np.empty((0, 5), dtype=np.float32), None


def _clip_task(tmp_path, frames, detector=None):
    """A FaceExtractionTask wired to run one preloaded clip and nothing else.

    Built by hand rather than through a Server: the assertion is about which
    detection entry point ``_extract_features`` reaches for a video, which
    needs neither a database nor a real file. ``_preloaded_images`` is keyed by
    resolved path and holds ``(frames, inv_scale)`` for a clip, so the frames
    go straight in and ``_read_video_frames`` never runs.
    """
    task = FaceExtractionTask.__new__(FaceExtractionTask)
    task._db = SimpleNamespace(
        image_root=str(tmp_path),
        # What decides ``need_faces`` when the relationship is not loaded.
        run_immediate_read_task=lambda _fetch: False,
    )
    task._engine = SimpleNamespace(insightface_model_pack="buffalo_l")
    task._stop_event = threading.Event()
    task._insightface_app = SimpleNamespace(det_model=detector, models={})
    task._init_insightface_app = lambda: None
    task._preloaded_images = {str(tmp_path / "clip.mp4"): (frames, 1.0)}
    return task


def test_a_video_frame_below_the_minimum_dimension_never_reaches_the_detector(
    tmp_path,
):
    """The min-dimension guard is the still path's, and a clip needs it too.

    A 1x512 frame makes RetinaFace compute ``new_width = int(det_size /
    aspect_ratio) == 0`` and its cv2.resize raises - proved against the real
    models in ``test_face_detection_extreme_aspect_ratio.py``. The video branch
    called ``BatchedFaceRunner.run_batch`` directly, so the guard in
    ``detect_faces_in_images`` never ran and the raise escaped the whole chunk:
    every picture batched with the clip loses its Face rows and the finder
    re-offers exactly the same batch on the next sweep, for ever.
    """
    detector = _RecordingDetector()
    task = _clip_task(
        tmp_path, [(0, np.zeros((512, 1, 3), dtype=np.uint8))], detector=detector
    )
    pic = SimpleNamespace(id=1, file_path="clip.mp4", description="tiny clip")

    _updates, bulk_faces, _crops = task._extract_features([pic])

    assert detector.seen == [], "an undetectable frame was handed to RetinaFace"
    # A sentinel row, exactly as a 1x512 still produces: undetectable, done.
    assert [(f.picture_id, f.face_index, f.bbox) for f in bulk_faces] == [(1, -1, None)]


def test_a_normal_video_frame_still_reaches_the_detector(tmp_path):
    """The positive control: the guard must not swallow a usable frame."""
    detector = _RecordingDetector()
    task = _clip_task(
        tmp_path, [(0, np.zeros((64, 64, 3), dtype=np.uint8))], detector=detector
    )
    pic = SimpleNamespace(id=3, file_path="clip.mp4", description="ordinary clip")

    task._extract_features([pic])

    assert detector.seen == [(64, 64, 3)]


def _clip_with_failing_runner(tmp_path, monkeypatch, exc, picture_id):
    """One clip whose detector raises *exc*; returns ``(task, picture)``."""

    class _FailingRunner:
        def __init__(self, _app):
            pass

        def run_batch(self, _images):
            raise exc

    monkeypatch.setattr(
        "pixlstash.tasks.face_extraction_task.BatchedFaceRunner", _FailingRunner
    )
    task = _clip_task(tmp_path, [(0, np.zeros((64, 64, 3), dtype=np.uint8))])
    pic = SimpleNamespace(id=picture_id, file_path="clip.mp4", description="clip")
    return task, pic


def test_a_non_memory_detector_failure_on_a_video_degrades_to_no_faces(
    tmp_path, monkeypatch
):
    """The OOM classifier is the still path's, and a clip needs it too.

    ``detect_faces_in_images`` splits detector failures in two: a VRAM OOM is
    re-raised so the runner retries it, anything else is a bad frame and
    degrades to "no faces". The video branch had neither half, so a decoder
    error on ONE clip escaped ``_extract_features`` and cost the whole chunk -
    up to 100 pictures - their Face rows, on every sweep, for ever.
    """
    task, pic = _clip_with_failing_runner(
        tmp_path, monkeypatch, ValueError("cv2 could not resize a degenerate frame"), 2
    )

    _updates, bulk_faces, _crops = task._extract_features([pic])

    assert [(f.picture_id, f.face_index, f.bbox) for f in bulk_faces] == [(2, -1, None)]


def test_a_full_card_on_a_video_is_never_recorded_as_no_faces(tmp_path, monkeypatch):
    """The other half of the same split, and the reason it is a split.

    Written as a sentinel a full card would mark the clip permanently faceless;
    the runner's OOM retry only sees it if it propagates.
    """
    task, pic = _clip_with_failing_runner(
        tmp_path,
        monkeypatch,
        RuntimeError("[ONNXRuntimeError] : 1 : FAIL : CUDA failure: out of memory"),
        4,
    )

    with pytest.raises(RuntimeError, match="out of memory"):
        task._extract_features([pic])


# ── the tag window ──────────────────────────────────────────────────────────


class _TagEngine:
    """Enough of an engine for MissingTagFinder: one picture per task."""

    tagger_settings = {"active_tag_plugin": "wd14"}

    class tagging_workflow:  # noqa: N801 - stands in for an attribute
        @staticmethod
        def suggested_task_size():
            return 1


def test_undecodable_pictures_do_not_crowd_the_tag_candidate_window(tmp_path):
    """Tagging must not starve behind a run of corrupt files.

    A picture TagTask cannot decode is marked unprocessable and keeps its
    pending-tag sentinel for ever - nothing writes tags for it, so nothing
    clears the sentinel. ``_filter_and_claim`` refuses to claim it, but the
    candidate window is ordered by ``Picture.id`` and bounded, so once enough
    of them sit below the pictures that still need tagging every sweep reads
    only corrupt rows, claims none and returns None. The planner reads that as
    "no work" and backs off. The face, thumbnail and embedding finders all
    exclude the suppressed set at the query; this one did not.
    """
    with Vault(image_root=str(tmp_path)) as vault:
        names = [f"c{index}.png" for index in range(6)]
        ids = _seed_pending(vault, tmp_path, names)

        def add_face_rows(session: Session):
            for pid in ids:
                session.add(Face(picture_id=pid, face_index=-1))
            session.commit()

        vault.db.run_task(add_face_rows)

        # Every picture but the last is undecodable. The window is
        # suggested_task_size * (TAGGER_MAX_INFLIGHT + 1) = 4 rows, so the
        # five suppressed ones fill it completely.
        for pid, name in zip(ids[:-1], names[:-1]):
            assert vault.db.unprocessable_images.mark_unprocessable(
                pid, str(tmp_path / name), reason="test-corrupt fixture"
            )
        good = ids[-1]

        finder = MissingTagFinder(vault.db, lambda: _TagEngine())
        task = finder.find_task()

        assert task is not None, (
            "tagging returned no work while a taggable picture was waiting "
            "behind the suppressed rows"
        )
        assert task.params["picture_ids"] == [good]
