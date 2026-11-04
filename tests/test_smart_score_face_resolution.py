"""The smart score's resolution term reads the main face, not the file (#2570).

A face crop and the picture it was cut from share the face's pixels, so they must
get the same resolution credit; a change to a picture's face boxes must clear its
stored score; and a score computed before a face change must not be written over it.
"""

import json
import os
import tempfile

import numpy as np
import pytest
from sqlmodel import select

from pixlstash.db_models import Face, Picture
from pixlstash.scoring import fetch_main_face_mpx
from pixlstash.scoring.smart_score import _STORED_FACE_BOX_SCALE
from pixlstash.server import Server
from pixlstash.tasks.face_extraction_task import CROP_EXPAND_SCALE
from pixlstash.tasks.smart_score_task import SmartScoreTask
from pixlstash.utils.quality.smart_score_utils import SmartScoreUtils


def _candidate(pid, **overrides):
    emb = np.ones(512, dtype=np.float32) / np.sqrt(512)
    base = {
        "id": pid,
        "embedding": emb,
        "aesthetic_score": 6.0,
        "width": 1920,
        "height": 1280,
        "sharpness": 0.8,
        "edge_density": 0.1,
        "luminance_entropy": 0.7,
        "anomaly_probs": {},
        "anomaly_human": frozenset(),
    }
    base.update(overrides)
    return base


def _scores(*candidates):
    return SmartScoreUtils.calculate_smart_score_batch_numpy(list(candidates), [], [])


def test_a_face_crop_scores_like_its_source():
    """Same face, different file sizes: the resolution term must not tell them apart."""
    source = _candidate(1, width=1920, height=1280, face_mpx=0.06)
    crop = _candidate(2, width=456, height=489, face_mpx=0.06)
    source_score, crop_score = _scores(source, crop)
    assert abs(source_score - crop_score) < 1e-9

    # Control: on file megapixels the same pair is far apart (the reported bias).
    source_old, crop_old = _scores(
        {**source, "face_mpx": None}, {**crop, "face_mpx": None}
    )
    assert source_old - crop_old > 0.3


def test_a_small_face_gets_little_credit_in_a_big_file():
    big_face, small_face, no_face = _scores(
        _candidate(1, width=2048, height=2048, face_mpx=0.15),
        _candidate(2, width=2048, height=2048, face_mpx=0.005),
        _candidate(3, width=2048, height=2048),
    )
    # A ~390 px face earns full credit, as a 4 MP file does.
    assert big_face == pytest.approx(no_face)
    assert no_face - small_face > 0.3


def test_a_picture_without_a_face_keeps_the_file_resolution():
    small, large = _scores(
        _candidate(1, width=512, height=512), _candidate(2, width=2048, height=2048)
    )
    assert large > small


def test_stored_box_scale_matches_face_extraction():
    assert _STORED_FACE_BOX_SCALE == CROP_EXPAND_SCALE


@pytest.fixture(scope="module")
def server():
    temp_dir = tempfile.TemporaryDirectory()
    config_path = os.path.join(temp_dir.name, "server-config.json")
    os.makedirs(os.path.join(temp_dir.name, "images"), exist_ok=True)
    with open(config_path, "w") as f:
        f.write(json.dumps({"port": 0}))
    srv = Server(config_path)
    # Nothing here wants background work: the scorer would race the assertions.
    srv.vault._work_planner.stop()
    try:
        yield srv
    finally:
        srv.close()
        temp_dir.cleanup()


def _make_picture(server, name, score=3.5, orientation=None):
    def insert(session):
        pic = Picture(
            file_path=name,
            width=1920,
            height=1280,
            orientation=orientation,
            image_embedding=np.random.rand(512).astype(np.float32).tobytes(),
            smart_score=score,
        )
        session.add(pic)
        session.commit()
        return pic.id

    return server.vault.db.run_task(insert)


def _smart_score(server, pic_id):
    return server.vault.db.run_task(lambda s: s.get(Picture, pic_id).smart_score)


def _set_score(server, pic_id, score=3.5):
    def apply(session):
        session.get(Picture, pic_id).smart_score = score
        session.commit()

    server.vault.db.run_task(apply)


def _add_face(server, pic_id, bbox, face_index=0):
    def insert(session):
        session.add(
            Face(picture_id=pic_id, frame_index=0, face_index=face_index, bbox=bbox)
        )
        session.commit()

    server.vault.db.run_task(insert)


def test_main_face_is_the_largest_box_shrunk_back(server):
    pic_id = _make_picture(server, "largest.png")
    _add_face(server, pic_id, [100, 100, 200, 200], face_index=0)
    _add_face(server, pic_id, [100, 100, 600, 500], face_index=1)
    faces = server.vault.db.run_task(lambda s: fetch_main_face_mpx(s, [pic_id]))
    assert faces[pic_id] == pytest.approx(500 * 400 / 1.25**2 / 1e6)


def test_a_box_clamped_at_an_edge_is_taken_as_stored(server):
    """Extraction clamps a box at the edge instead of growing it there."""
    left = _make_picture(server, "edge-left.png")
    _add_face(server, left, [0, 100, 300, 400])
    # 1280 wide as shown once a quarter turn (EXIF 6) swaps the 1920x1280 file.
    turned = _make_picture(server, "edge-turned.png", orientation=6)
    _add_face(server, turned, [980, 100, 1280, 400])
    upright = _make_picture(server, "inside.png")
    _add_face(server, upright, [980, 100, 1280, 400])
    faces = server.vault.db.run_task(
        lambda s: fetch_main_face_mpx(s, [left, turned, upright])
    )
    assert faces[left] == pytest.approx(0.09)
    assert faces[turned] == pytest.approx(0.09)
    assert faces[upright] == pytest.approx(0.09 / 1.25**2)


def test_face_box_changes_clear_the_stored_score(server):
    pic_id = _make_picture(server, "hook.png")

    # A sentinel (no box) says only "no faces": the score stands.
    def add_sentinel(session):
        session.add(Face(picture_id=pic_id, frame_index=0, face_index=-1, bbox=None))
        session.commit()

    server.vault.db.run_task(add_sentinel)
    assert _smart_score(server, pic_id) == 3.5

    _add_face(server, pic_id, [10, 10, 300, 300])
    assert _smart_score(server, pic_id) is None

    def first_face(session):
        return session.exec(
            select(Face).where(Face.picture_id == pic_id, Face.bbox_.is_not(None))
        ).one()

    def move(session):
        first_face(session).bbox = [10, 10, 600, 600]
        session.commit()

    _set_score(server, pic_id)
    server.vault.db.run_task(move)
    assert _smart_score(server, pic_id) is None

    def unrelated_edit(session):
        first_face(session).character_id = None
        first_face(session).model_pack = "buffalo_l"
        session.commit()

    _set_score(server, pic_id)
    server.vault.db.run_task(unrelated_edit)
    assert _smart_score(server, pic_id) == 3.5

    def delete(session):
        session.delete(first_face(session))
        session.commit()

    server.vault.db.run_task(delete)
    assert _smart_score(server, pic_id) is None


def test_a_score_computed_before_a_face_change_is_not_written(server):
    pic_id = _make_picture(server, "cas.png", score=None)
    before = server.vault.db.run_task(
        lambda s: SmartScoreTask._input_signature(s, [pic_id])
    )
    _add_face(server, pic_id, [0, 0, 400, 400])
    persisted = server.vault.db.run_task(
        SmartScoreTask._persist_scores, {pic_id: 4.0}, before
    )
    assert persisted == []
    assert _smart_score(server, pic_id) is None

    # Control: with no change in between, the same write goes through.
    before = server.vault.db.run_task(
        lambda s: SmartScoreTask._input_signature(s, [pic_id])
    )
    persisted = server.vault.db.run_task(
        SmartScoreTask._persist_scores, {pic_id: 4.0}, before
    )
    assert persisted == [pic_id]
    assert _smart_score(server, pic_id) == 4.0


def test_a_picture_loaded_in_the_session_sees_the_cleared_score(server):
    """The reset is a Core UPDATE; the hook must expire the in-session copy."""
    pic_id = _make_picture(server, "loaded.png")

    def add_face_to_loaded_picture(session):
        pic = session.get(Picture, pic_id)
        assert pic.smart_score == 3.5
        session.add(
            Face(picture_id=pic_id, frame_index=0, face_index=0, bbox=[1, 1, 99, 99])
        )
        session.flush()
        score = pic.smart_score
        session.commit()
        return score

    assert server.vault.db.run_task(add_face_to_loaded_picture) is None
