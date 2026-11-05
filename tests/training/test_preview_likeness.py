"""Likeness of a training run's previews to its character (#2522)."""

import os
import time
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image

from pixlstash.services.training_service import handle_for_run
from pixlstash.tasks.face_extraction_task import FaceExtractionTask
from pixlstash.tasks.training_preview_faces_finder import TrainingPreviewFacesFinder
from pixlstash.tasks.training_preview_faces_task import TrainingPreviewFacesTask
from pixlstash.training import preview_likeness

# Three orthogonal unit vectors: the character, a stranger, and a third face.
ME, OTHER, THIRD = np.eye(3, 512, dtype=np.float32)


def _reference(vector):
    return SimpleNamespace(id=1, features=vector.tobytes())


def _cell(filename, state="sample"):
    return {"state": state, "filename": filename}


def _rows():
    """Two prompts with the word, one without; columns 0, 500, 1000."""
    return [
        {
            "with_trigger": True,
            "cells": [_cell("a0"), _cell("a1"), _cell("a2")],
        },
        {
            "with_trigger": True,
            "cells": [_cell("b0"), _cell("b1"), _cell(None, state="future")],
        },
        {
            "with_trigger": False,
            "cells": [_cell("c0"), _cell("c1"), _cell("c2")],
        },
    ]


def test_a_step_is_the_mean_of_its_previews_with_a_face():
    rows = _rows()
    faces = {
        "a0": [OTHER],
        "a1": [(ME + OTHER) / np.sqrt(2)],
        "a2": [ME],
        "b0": [OTHER],
        "b1": [],  # a back view: left out, not scored 0
        "c0": [OTHER],
        "c1": None,  # unreadable: says nothing, and is not pending
        "c2": [THIRD, ME],  # the best-matching face counts
    }
    result = preview_likeness.score(rows, [0, 500, 1000], faces, [_reference(ME)])

    assert [c["likeness"] for c in rows[0]["cells"]] == [0, 71, 100]
    assert [c["face"] for c in rows[1]["cells"]] == [True, False, None]
    assert rows[1]["cells"][1]["likeness"] is None
    assert [c["face"] for c in rows[2]["cells"]] == [True, None, True]
    assert result["with_trigger"] == [0, 71, 100]
    assert result["without_trigger"] == [0, None, 100]
    assert (result["best_step"], result["best"]) == (1000, 100)
    assert result["pending"] == 0
    assert result["references"] == 1


def test_step_zero_is_never_the_best_and_unseen_previews_are_pending():
    rows = _rows()
    faces = {"a0": [ME], "a1": [OTHER]}
    result = preview_likeness.score(rows, [0, 500, 1000], faces, [_reference(ME)])

    assert result["with_trigger"] == [100, 0, None]
    assert (result["best_step"], result["best"]) == (500, 0)
    # a2, b0, b1, c0, c1, c2; the future cell is not a preview yet.
    assert result["pending"] == 6
    assert rows[0]["cells"][2] == {
        "state": "sample",
        "filename": "a2",
        "face": None,
        "likeness": None,
    }


def test_without_reference_faces_a_face_is_found_but_not_scored():
    rows = _rows()
    result = preview_likeness.score(rows, [0, 500, 1000], {"a1": [ME]}, [])
    assert rows[0]["cells"][1]["face"] is True
    assert rows[0]["cells"][1]["likeness"] is None
    assert result["best_step"] is None and result["references"] == 0


def test_the_cache_round_trips_and_a_pack_change_reads_empty(tmp_path):
    work_dir = str(tmp_path)
    assert preview_likeness.read_faces(work_dir) == {}
    preview_likeness.write_faces(work_dir, "buffalo_l", {"a": [ME], "b": []})
    preview_likeness.write_faces(work_dir, "buffalo_l", {"c": None})

    faces = preview_likeness.read_faces(work_dir, "buffalo_l")
    assert sorted(faces) == ["a", "b", "c"]
    assert np.array_equal(faces["a"][0], ME)
    assert faces["b"] == [] and faces["c"] is None
    assert preview_likeness.read_faces(work_dir, "antelopev2") == {}
    # A deleted run is not recreated by a late write.
    assert not preview_likeness.write_faces(
        str(tmp_path / "gone"), "buffalo_l", {"a": [ME]}
    )
    assert not os.path.exists(tmp_path / "gone")


def test_an_unreadable_cache_is_embedded_again(tmp_path):
    with open(preview_likeness.faces_path(str(tmp_path)), "w") as fh:
        fh.write("{not json")
    assert preview_likeness.read_faces(str(tmp_path)) == {}


# ── the finder and the task ─────────────────────────────────────────────────


class _Hub:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self, sql, params):
        return [row for row in self.rows if row["library_uuid"] == params[0]]


def _run(tmp_path, **fields):
    run = {
        "uuid": "0123456789abcdef",
        "library_uuid": "lib",
        "name": "portrait",
        "pid": None,
        "training_folder": None,
        "character_id": 7,
        "status": "running",
        "completed_at": None,
    }
    run.update(fields)
    handle = handle_for_run(str(tmp_path / "training"), run)
    os.makedirs(os.path.join(handle.save_root, "samples"))
    return run, handle


def _sample(handle, step, index, age_s=10.0, image=True):
    path = os.path.join(
        handle.save_root, "samples", f"1700000000000__{step:09d}_{index}.png"
    )
    if image:
        Image.new("RGB", (64, 64), (120, 90, 60)).save(path)
    else:
        open(path, "wb").close()
    then = time.time() - age_s
    os.utime(path, (then, then))
    return os.path.basename(path)


def _finder(tmp_path, runs):
    engine = SimpleNamespace(insightface_model_pack="buffalo_l")
    finder = TrainingPreviewFacesFinder(
        hub=_Hub(runs),
        library_uuid="lib",
        image_root=str(tmp_path),
        engine_getter=lambda: engine,
    )
    finder._due = lambda interval: True
    return finder


def test_the_finder_hands_out_settled_previews_of_character_runs(tmp_path):
    run, handle = _run(tmp_path)
    settled = _sample(handle, 0, 0)
    _sample(handle, 0, 1, age_s=0.0)  # still being written
    finder = _finder(tmp_path, [run, {**run, "library_uuid": "another"}])

    task = finder.find_task()
    assert isinstance(task, TrainingPreviewFacesTask)
    assert task.params == {"run_uuid": run["uuid"], "previews": 1}
    assert [name for name, _ in task._previews] == [settled]
    assert finder.progress() == (2, 1)
    # Claimed while its task runs: not handed out twice.
    assert finder.find_task() is None
    finder.on_task_complete(task, None)
    assert finder.find_task() is not None


def test_a_clip_preview_is_scored_from_its_frames(tmp_path, monkeypatch):
    """A video model previews as MP4 clips: their sampled frames are read."""
    run, handle = _run(tmp_path, status="completed", completed_at="2026-11-02")
    clip = os.path.join(handle.save_root, "samples", "1700000000000__000000500_0.mp4")
    writer = cv2.VideoWriter(clip, cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (64, 64))
    for _ in range(9):
        writer.write(np.full((64, 64, 3), 100, np.uint8))
    writer.release()
    then = time.time() - 10.0
    os.utime(clip, (then, then))
    seen = []

    def detect(app, images):
        seen.append(len(images))
        # The face is in the last sampled frame only.
        return [[] for _ in images[:-1]] + [[SimpleNamespace(embedding=ME)]]

    monkeypatch.setattr(
        FaceExtractionTask,
        "get_or_init_insightface",
        classmethod(lambda cls, engine, cpu_spillover=False: "app"),
    )
    monkeypatch.setattr(
        FaceExtractionTask, "detect_faces_in_images", staticmethod(detect)
    )
    finder = _finder(tmp_path, [run])

    task = finder.find_task()
    assert [name for name, _ in task._previews] == [os.path.basename(clip)]
    assert task._run_task() == {"previews": 1}
    finder.on_task_complete(task, None)
    assert seen and seen[0] > 1
    faces = preview_likeness.read_faces(handle.work_dir, "buffalo_l")
    assert np.array_equal(faces[os.path.basename(clip)][0], ME)
    # Every preview embedded, so the ended run is no longer watched.
    assert finder.find_task() is None
    assert run["uuid"] in finder._settled


def test_the_task_writes_faces_and_an_ended_run_settles(tmp_path, monkeypatch):
    run, handle = _run(tmp_path, status="completed", completed_at="2026-11-02")
    with_face = _sample(handle, 500, 0)
    unreadable = _sample(handle, 500, 1, image=False)
    monkeypatch.setattr(
        FaceExtractionTask,
        "get_or_init_insightface",
        classmethod(lambda cls, engine, cpu_spillover=False: "app"),
    )
    monkeypatch.setattr(
        FaceExtractionTask,
        "detect_faces_in_images",
        staticmethod(lambda app, images: [[SimpleNamespace(embedding=ME)]]),
    )
    finder = _finder(tmp_path, [run])

    task = finder.find_task()
    assert task._run_task() == {"previews": 2}
    finder.on_task_complete(task, None)
    faces = preview_likeness.read_faces(handle.work_dir, "buffalo_l")
    assert np.array_equal(faces[with_face][0], ME)
    assert faces[unreadable] is None

    assert finder.find_task() is None
    assert run["uuid"] in finder._settled


def test_a_preview_that_breaks_the_detector_spares_the_rest(tmp_path, monkeypatch):
    run, handle = _run(tmp_path)
    first = _sample(handle, 0, 0)
    second = _sample(handle, 0, 1)
    calls = []

    def detect(app, images):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("the detector choked")
        return [[SimpleNamespace(embedding=ME)]]

    monkeypatch.setattr(
        FaceExtractionTask,
        "get_or_init_insightface",
        classmethod(lambda cls, engine, cpu_spillover=False: "app"),
    )
    monkeypatch.setattr(
        FaceExtractionTask, "detect_faces_in_images", staticmethod(detect)
    )
    task = _finder(tmp_path, [run]).find_task()
    assert task._run_task() == {"previews": 2}
    faces = preview_likeness.read_faces(handle.work_dir, "buffalo_l")
    assert faces[first] is None
    assert np.array_equal(faces[second][0], ME)


def test_step_zero_is_skipped_by_its_step_not_its_position():
    rows = [{"with_trigger": True, "cells": [_cell("a"), _cell("b")]}]
    faces = {"a": [ME], "b": [OTHER]}
    result = preview_likeness.score(rows, [500, 1000], faces, [_reference(ME)])
    assert (result["best_step"], result["best"]) == (500, 100)
