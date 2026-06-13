"""Tests for the configurable InsightFace model pack feature.

These tests never hit the network or load real models: ``FaceAnalysis`` and
``snapshot_download`` are mocked throughout.
"""

import os
import time
import types
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import numpy as np
import pytest
from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session, create_engine, select

from pixlstash.db_models.character import Character
from pixlstash.db_models.face import Face
from pixlstash.db_models.picture import Picture
from pixlstash.tasks.face_extraction_task import FaceExtractionTask
from pixlstash.tasks.face_model_refresh_task import FaceModelRefreshTask, _bbox_iou
from pixlstash.tasks.missing_face_model_refresh_finder import (
    MissingFaceModelRefreshFinder,
)
import pixlstash.utils.insightface_model_utils as model_utils


@pytest.fixture(autouse=True)
def _clear_download_backoff():
    """Reset the per-process download-failure backoff so it cannot leak between tests."""
    model_utils._download_failures.clear()
    yield
    model_utils._download_failures.clear()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_engine(model_pack: str, force_cpu: bool = True):
    """Return a minimal stand-in engine exposing only what the face code reads."""
    return types.SimpleNamespace(
        insightface_model_pack=model_pack,
        force_cpu=force_cpu,
        keep_models_in_memory=True,
    )


class _FakeDetection:
    """Stand-in for insightface FaceResult: only bbox + embedding are read."""

    def __init__(self, bbox, embedding):
        self.bbox = np.asarray(bbox, dtype="float32")
        self.embedding = np.asarray(embedding, dtype="float32")


def _reset_face_globals():
    FaceExtractionTask._global_insightface_app = None
    FaceExtractionTask._global_cpu_insightface_app = None


# --------------------------------------------------------------------------- #
# §2 — get_or_init_insightface passes the configured name on both paths
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("pack", ["buffalo_l", "auraface"])
@pytest.mark.parametrize("cpu_spillover", [False, True])
def test_get_or_init_passes_model_pack_name(pack, cpu_spillover):
    _reset_face_globals()
    engine = _make_engine(pack)

    fake_app = mock.MagicMock(name="FaceAnalysisInstance")
    with (
        mock.patch.object(model_utils, "ensure_model_pack_available") as ensure,
        mock.patch(
            "pixlstash.tasks.face_extraction_task.ensure_model_pack_available"
        ) as ensure_in_task,
        mock.patch(
            "pixlstash.tasks.face_extraction_task.FaceAnalysis",
            return_value=fake_app,
        ) as fa,
        mock.patch("torch.cuda.is_available", return_value=False),
    ):
        FaceExtractionTask.get_or_init_insightface(engine, cpu_spillover=cpu_spillover)

    # The task imports ensure_model_pack_available by name, so that is the patch
    # that must have fired; the module-level patch is just belt-and-braces.
    assert ensure_in_task.called or ensure.called
    fa.assert_called_once()
    _, kwargs = fa.call_args
    assert kwargs.get("name") == pack
    _reset_face_globals()


# --------------------------------------------------------------------------- #
# §2 — unknown pack fails closed
# --------------------------------------------------------------------------- #


def test_unknown_pack_fails_closed(caplog):
    with pytest.raises(ValueError, match="Unknown InsightFace model pack"):
        model_utils.validate_model_pack("definitely_not_a_pack")
    assert any("Unknown InsightFace model pack" in r.message for r in caplog.records)


def test_get_or_init_unknown_pack_raises():
    _reset_face_globals()
    engine = _make_engine("bogus_pack")
    with (
        mock.patch("pixlstash.tasks.face_extraction_task.FaceAnalysis") as fa,
        mock.patch("torch.cuda.is_available", return_value=False),
    ):
        with pytest.raises(ValueError, match="Unknown InsightFace model pack"):
            FaceExtractionTask.get_or_init_insightface(engine)
    fa.assert_not_called()
    _reset_face_globals()


# --------------------------------------------------------------------------- #
# §3 — auto-download behaviour
# --------------------------------------------------------------------------- #


def test_auraface_downloads_when_absent(tmp_path, monkeypatch):
    # Point the insightface root at a temp dir so nothing exists yet.
    monkeypatch.setattr(model_utils, "_INSIGHTFACE_ROOT", str(tmp_path))

    # Fake snapshot dir containing the .onnx files at its root.
    snapshot_dir = tmp_path / "hf_snapshot"
    snapshot_dir.mkdir()
    for name in ("scrfd_10g_bnkps.onnx", "glintr100.onnx"):
        (snapshot_dir / name).write_bytes(b"onnx")

    fake_snapshot = mock.MagicMock(return_value=str(snapshot_dir))
    with mock.patch.dict(
        "sys.modules",
        {"huggingface_hub": types.SimpleNamespace(snapshot_download=fake_snapshot)},
    ):
        model_utils.ensure_model_pack_available("auraface")

    fake_snapshot.assert_called_once()
    _, kwargs = fake_snapshot.call_args
    assert kwargs.get("revision") == model_utils._AURAFACE_REVISION

    dest = tmp_path / "models" / "auraface"
    assert (dest / "scrfd_10g_bnkps.onnx").exists()
    assert (dest / "glintr100.onnx").exists()


def test_auraface_not_downloaded_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(model_utils, "_INSIGHTFACE_ROOT", str(tmp_path))
    dest = tmp_path / "models" / "auraface"
    dest.mkdir(parents=True)
    (dest / "glintr100.onnx").write_bytes(b"onnx")

    fake_snapshot = mock.MagicMock()
    with mock.patch.dict(
        "sys.modules",
        {"huggingface_hub": types.SimpleNamespace(snapshot_download=fake_snapshot)},
    ):
        model_utils.ensure_model_pack_available("auraface")

    fake_snapshot.assert_not_called()


def test_buffalo_l_never_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(model_utils, "_INSIGHTFACE_ROOT", str(tmp_path))
    fake_snapshot = mock.MagicMock()
    with mock.patch.dict(
        "sys.modules",
        {"huggingface_hub": types.SimpleNamespace(snapshot_download=fake_snapshot)},
    ):
        model_utils.ensure_model_pack_available("buffalo_l")
    fake_snapshot.assert_not_called()


def test_auraface_download_failure_raises_with_manual_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(model_utils, "_INSIGHTFACE_ROOT", str(tmp_path))

    def _boom(*a, **k):
        raise OSError("network down")

    with mock.patch.dict(
        "sys.modules",
        {"huggingface_hub": types.SimpleNamespace(snapshot_download=_boom)},
    ):
        with pytest.raises(RuntimeError, match="place the pack manually"):
            model_utils.ensure_model_pack_available("auraface")


def test_auraface_download_backoff_suppresses_repeated_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(model_utils, "_INSIGHTFACE_ROOT", str(tmp_path))

    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise OSError("network down")

    with mock.patch.dict(
        "sys.modules",
        {"huggingface_hub": types.SimpleNamespace(snapshot_download=_boom)},
    ):
        # First attempt reaches the network and fails (records the failure time).
        with pytest.raises(RuntimeError, match="place the pack manually"):
            model_utils.ensure_model_pack_available("auraface")
        assert calls["n"] == 1

        # A second attempt inside the backoff window is short-circuited: no extra
        # network call, and the error explains it is in backoff (not re-logged loud).
        with pytest.raises(RuntimeError, match="backoff"):
            model_utils.ensure_model_pack_available("auraface")
        assert calls["n"] == 1


def test_auraface_download_backoff_expires_then_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(model_utils, "_INSIGHTFACE_ROOT", str(tmp_path))
    # Simulate a failure that happened longer ago than the backoff window.
    model_utils._download_failures["auraface"] = (
        time.monotonic() - model_utils._DOWNLOAD_BACKOFF_SECONDS - 1.0
    )

    snapshot_dir = tmp_path / "hf_snapshot"
    snapshot_dir.mkdir()
    (snapshot_dir / "glintr100.onnx").write_bytes(b"onnx")
    fake_snapshot = mock.MagicMock(return_value=str(snapshot_dir))

    with mock.patch.dict(
        "sys.modules",
        {"huggingface_hub": types.SimpleNamespace(snapshot_download=fake_snapshot)},
    ):
        # Window elapsed → it retries, succeeds, and clears the failure record.
        model_utils.ensure_model_pack_available("auraface")

    fake_snapshot.assert_called_once()
    assert "auraface" not in model_utils._download_failures


# --------------------------------------------------------------------------- #
# §4 — migration backfill (helper SQL on a temp SQLite DB)
# --------------------------------------------------------------------------- #


def _make_sqlite(tmp_path, name="t.db"):
    db_path = tmp_path / name
    engine = create_engine(f"sqlite:///{db_path}", echo=False, poolclass=NullPool)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(engine)
    return engine


def test_migration_backfill_sets_buffalo_l(tmp_path):
    engine = _make_sqlite(tmp_path)
    with Session(engine) as session:
        pic = Picture(file_path="p.jpg")
        session.add(pic)
        session.commit()
        session.refresh(pic)
        # Simulate pre-migration rows: model_pack NULL.
        f1 = Face(picture_id=pic.id, frame_index=0, face_index=0, bbox=[0, 0, 4, 4])
        f1.model_pack = None
        f2 = Face(picture_id=pic.id, frame_index=0, face_index=1, bbox=[5, 5, 9, 9])
        f2.model_pack = None
        session.add(f1)
        session.add(f2)
        session.commit()

    # Apply the migration's backfill SQL (mirrors 0052 upgrade()).
    from sqlalchemy import String, column, table, update

    face_tbl = table("face", column("model_pack", String))
    with engine.begin() as conn:
        conn.execute(
            update(face_tbl)
            .where(face_tbl.c.model_pack.is_(None))
            .values(model_pack="buffalo_l")
        )

    with Session(engine) as session:
        packs = session.exec(select(Face.model_pack)).all()
    assert set(packs) == {"buffalo_l"}


# --------------------------------------------------------------------------- #
# §4 — model_pack recorded on newly-written faces (incl. sentinel)
# --------------------------------------------------------------------------- #


class _FakeDB:
    """Minimal DB shim: runs submitted writes synchronously on a real session."""

    def __init__(self, engine, image_root):
        self._engine = engine
        self.image_root = image_root

    def submit_task(self, fn, *args, priority=None, **kwargs):
        with Session(self._engine) as session:
            fn(session, *args, **kwargs)

    def run_immediate_read_task(self, fn, *args, **kwargs):
        with Session(self._engine) as session:
            return fn(session, *args, **kwargs)


def test_model_pack_set_on_written_faces_including_sentinel(tmp_path, monkeypatch):
    engine = _make_sqlite(tmp_path)
    img_root = tmp_path / "imgs"
    img_root.mkdir()

    # Two pictures: one with a detected face, one with none (sentinel path).
    with Session(engine) as session:
        pic_face = Picture(file_path="has_face.jpg")
        pic_none = Picture(file_path="no_face.jpg")
        session.add(pic_face)
        session.add(pic_none)
        session.commit()
        session.refresh(pic_face)
        session.refresh(pic_none)
        pic_face_id, pic_none_id = pic_face.id, pic_none.id

    db = _FakeDB(engine, str(img_root))
    inf_engine = _make_engine("auraface")

    with Session(engine) as session:
        pics = session.exec(select(Picture)).all()

    task = FaceExtractionTask(database=db, engine=inf_engine, pictures=list(pics))

    # Stub the InsightFace init and the per-image loader/detector so no models or
    # files are needed. The "has_face" picture returns one detection; the other
    # returns none → sentinel.
    monkeypatch.setattr(task, "_init_insightface_app", lambda: None)
    # _extract_features builds a BatchedFaceRunner(app) unconditionally (only used
    # for the video path); give the stub a det_model so construction succeeds.
    task._insightface_app = mock.MagicMock(name="FaceAnalysisStub")
    monkeypatch.setattr(
        FaceExtractionTask,
        "_has_faces",
        lambda self, pid: False,
    )

    fake_img = np.zeros((64, 64, 3), dtype="uint8")

    def _fake_load(path, max_side):
        return fake_img, 1.0

    monkeypatch.setattr(
        "pixlstash.tasks.face_extraction_task.ImageUtils.load_image_bgr_reduced",
        _fake_load,
    )

    # The task preloads images; emulate the preloaded dict so detection runs on
    # the chunk via batched_detections keyed by resolved path.
    def _resolve(image_root, file_path):
        return os.path.join(image_root, file_path)

    monkeypatch.setattr(
        "pixlstash.tasks.face_extraction_task.ImageUtils.resolve_picture_path",
        _resolve,
    )

    # Preload both images so the batched-detection branch is exercised.
    task._preloaded_images = {
        os.path.join(str(img_root), "has_face.jpg"): (fake_img, 1.0),
        os.path.join(str(img_root), "no_face.jpg"): (fake_img, 1.0),
    }

    def _detect_by_path(app, images):
        # has_face.jpg is added to the batch first (insertion order of dict).
        results = []
        for idx, _ in enumerate(images):
            if idx == 0:
                results.append([_FakeDetection([10, 10, 30, 30], np.ones(512))])
            else:
                results.append([])
        return results

    monkeypatch.setattr(
        FaceExtractionTask, "detect_faces_in_images", staticmethod(_detect_by_path)
    )
    # Avoid thumbnail file I/O.
    monkeypatch.setattr(
        "pixlstash.tasks.face_extraction_task.FaceUtils."
        "generate_face_weighted_thumbnail_with_crop",
        lambda *a, **k: (None, None),
    )

    changed, bulk_faces, _crops = task._extract_features(list(pics))
    # Persist via the fake DB.
    task._flush_to_db(bulk_faces, [])

    with Session(engine) as session:
        faces = session.exec(select(Face)).all()
        by_pic = {}
        for f in faces:
            by_pic.setdefault(f.picture_id, []).append(f)

    # Every written face (real or sentinel) carries the configured pack.
    assert faces, "expected faces to be written"
    assert all(f.model_pack == "auraface" for f in faces)
    # The no-face picture got a sentinel row (face_index == -1).
    assert any(f.face_index == -1 for f in by_pic.get(pic_none_id, []))
    _ = pic_face_id  # used implicitly above


# --------------------------------------------------------------------------- #
# §6 — stale-pack finder selection
# --------------------------------------------------------------------------- #


def test_finder_selects_only_stale_pack_pictures(tmp_path):
    engine = _make_sqlite(tmp_path)
    with Session(engine) as session:
        stale = Picture(file_path="stale.jpg")
        current = Picture(file_path="current.jpg")
        session.add(stale)
        session.add(current)
        session.commit()
        session.refresh(stale)
        session.refresh(current)
        stale_id, current_id = stale.id, current.id

        fa = Face(picture_id=stale_id, frame_index=0, face_index=0, bbox=[0, 0, 4, 4])
        fa.model_pack = "buffalo_l"
        fb = Face(picture_id=current_id, frame_index=0, face_index=0, bbox=[0, 0, 4, 4])
        fb.model_pack = "auraface"
        session.add(fa)
        session.add(fb)
        session.commit()

    db = _FakeDB(engine, str(tmp_path))
    finder = MissingFaceModelRefreshFinder(
        database=db, engine_getter=lambda: _make_engine("auraface")
    )

    with Session(engine) as session:
        selected = finder._fetch_stale_pack_pictures(session, "auraface")

    selected_ids = {p.id for p in selected}
    assert stale_id in selected_ids
    assert current_id not in selected_ids


def test_finder_yields_to_face_extraction():
    from pixlstash.tasks.task_type import TaskType

    finder = MissingFaceModelRefreshFinder(database=None, engine_getter=lambda: None)
    assert finder.depends_on() == [TaskType.FACE_EXTRACTION]


# --------------------------------------------------------------------------- #
# §5 — in-place refresh preserves character_id
# --------------------------------------------------------------------------- #


def test_refresh_updates_in_place_preserving_character(tmp_path):
    engine = _make_sqlite(tmp_path)
    with Session(engine) as session:
        char = Character(name="Alice")
        session.add(char)
        session.commit()
        session.refresh(char)

        pic = Picture(file_path="p.jpg")
        session.add(pic)
        session.commit()
        session.refresh(pic)
        pic_id, char_id = pic.id, char.id

        old = Face(
            picture_id=pic_id,
            frame_index=0,
            face_index=0,
            bbox=[10, 10, 30, 30],
            features=np.zeros(512, dtype="float32").tobytes(),
        )
        old.model_pack = "buffalo_l"
        old.character_id = char_id
        session.add(old)
        session.commit()
        old_face_id = old.id

    # New detection from the new pack at the same position with a new embedding.
    new_face = Face(
        picture_id=pic_id,
        frame_index=0,
        face_index=0,
        bbox=[10, 10, 30, 30],
        features=np.ones(512, dtype="float32").tobytes(),
        model_pack="auraface",
    )

    with Session(engine) as session:
        FaceModelRefreshTask._refresh_picture_faces(
            session, pic_id, [new_face], "auraface"
        )

    with Session(engine) as session:
        faces = session.exec(select(Face).where(Face.picture_id == pic_id)).all()

    assert len(faces) == 1
    refreshed = faces[0]
    # Same row (identity preserved), embedding + pack updated, character kept.
    assert refreshed.id == old_face_id
    assert refreshed.model_pack == "auraface"
    assert refreshed.character_id == char_id
    assert refreshed.features == np.ones(512, dtype="float32").tobytes()


def test_refresh_handles_new_detection_and_removal(tmp_path):
    engine = _make_sqlite(tmp_path)
    with Session(engine) as session:
        pic = Picture(file_path="p.jpg")
        session.add(pic)
        session.commit()
        session.refresh(pic)
        pic_id = pic.id

        # Two old faces; the second will disappear in the new run.
        keep = Face(
            picture_id=pic_id, frame_index=0, face_index=0, bbox=[10, 10, 30, 30]
        )
        keep.model_pack = "buffalo_l"
        gone = Face(
            picture_id=pic_id, frame_index=0, face_index=1, bbox=[100, 100, 130, 130]
        )
        gone.model_pack = "buffalo_l"
        session.add(keep)
        session.add(gone)
        session.commit()

    # New run: face 0 matches "keep"; a brand-new face appears at a new spot.
    new_faces = [
        Face(
            picture_id=pic_id,
            frame_index=0,
            face_index=0,
            bbox=[10, 10, 30, 30],
            features=np.ones(512, dtype="float32").tobytes(),
            model_pack="auraface",
        ),
        Face(
            picture_id=pic_id,
            frame_index=0,
            face_index=1,
            bbox=[200, 200, 230, 230],
            features=np.full(512, 2, dtype="float32").tobytes(),
            model_pack="auraface",
        ),
    ]

    with Session(engine) as session:
        FaceModelRefreshTask._refresh_picture_faces(
            session, pic_id, new_faces, "auraface"
        )

    with Session(engine) as session:
        faces = session.exec(select(Face).where(Face.picture_id == pic_id)).all()

    # The vanished face (at 100,100) is gone; two faces remain, both auraface.
    boxes = sorted(f.bbox[0] for f in faces if f.bbox)
    assert boxes == [10, 200]
    assert all(f.model_pack == "auraface" for f in faces)


def test_bbox_iou_basics():
    assert _bbox_iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert _bbox_iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    assert _bbox_iou(None, [0, 0, 1, 1]) == 0.0


# --------------------------------------------------------------------------- #
# §7 — claim-vs-commit timing and commit-failure handling (B2)
# --------------------------------------------------------------------------- #
#
# The synchronous _FakeDB above masks the async behaviour that caused B2: it
# commits inside submit_task before the call even returns. _AsyncFakeDB runs the
# write on a real background thread (with a deliberate delay) and returns a real
# Future, so a fire-and-forget _run_task would return BEFORE the commit landed —
# exactly the bug. _run_task must block on the write futures.


class _AsyncFakeDB:
    """DB shim that runs each submitted write on a background thread.

    submit_task returns a real ``concurrent.futures.Future``; the write does not
    commit until ``commit_delay_s`` has elapsed on the worker thread. This makes
    "claim released / run() returned before the write committed" observable.
    """

    def __init__(self, engine, image_root, commit_delay_s=0.2):
        self._engine = engine
        self.image_root = image_root
        self._commit_delay_s = commit_delay_s
        self._executor = ThreadPoolExecutor(max_workers=2)

    def submit_task(self, fn, *args, priority=None, **kwargs):
        def _work():
            time.sleep(self._commit_delay_s)
            with Session(self._engine) as session:
                return fn(session, *args, **kwargs)

        return self._executor.submit(_work)

    def run_immediate_read_task(self, fn, *args, **kwargs):
        with Session(self._engine) as session:
            return fn(session, *args, **kwargs)

    def shutdown(self):
        self._executor.shutdown(wait=True)


def _seed_stale_picture(engine, file_path="p.jpg"):
    with Session(engine) as session:
        pic = Picture(file_path=file_path)
        session.add(pic)
        session.commit()
        session.refresh(pic)
        pic_id = pic.id
        face = Face(
            picture_id=pic_id, frame_index=0, face_index=0, bbox=[10, 10, 30, 30]
        )
        face.model_pack = "buffalo_l"
        face.features = np.zeros(512, dtype="float32").tobytes()
        session.add(face)
        session.commit()
    return pic_id


def _patch_detection(monkeypatch, task):
    """Stub init + load + detect so the refresh runs without models or files."""
    monkeypatch.setattr(task, "_init_insightface_app", lambda: None)
    task._insightface_app = mock.MagicMock(name="FaceAnalysisStub")
    fake_img = np.zeros((64, 64, 3), dtype="uint8")
    monkeypatch.setattr(
        "pixlstash.tasks.face_model_refresh_task.ImageUtils.resolve_picture_path",
        lambda image_root, file_path: os.path.join(image_root, file_path),
    )
    monkeypatch.setattr(
        "pixlstash.tasks.face_model_refresh_task.ImageUtils.load_image_bgr_reduced",
        lambda path, max_side: (fake_img, 1.0),
    )
    monkeypatch.setattr(
        FaceExtractionTask,
        "detect_faces_in_images",
        staticmethod(
            lambda app, images: [
                [_FakeDetection([10, 10, 30, 30], np.ones(512))] for _ in images
            ]
        ),
    )


def test_run_task_blocks_until_writes_commit(tmp_path, monkeypatch):
    """C1: run() must not return before the model_pack writes have committed.

    With the old fire-and-forget code, _run_task returned while the write was
    still queued, so the still-stale row would be re-selected. The fix blocks on
    the submit futures, so by the time run() returns the new pack is persisted.
    """
    engine = _make_sqlite(tmp_path)
    img_root = tmp_path / "imgs"
    img_root.mkdir()
    pic_id = _seed_stale_picture(engine)

    db = _AsyncFakeDB(engine, str(img_root), commit_delay_s=0.3)
    inf_engine = _make_engine("auraface")

    with Session(engine) as session:
        pics = list(session.exec(select(Picture)).all())

    task = FaceModelRefreshTask(database=db, engine=inf_engine, pictures=pics)
    _patch_detection(monkeypatch, task)

    try:
        result = task.run()

        # The instant run() returns, the write MUST already be committed: the
        # finder's claim is released here, so a still-stale row would be
        # re-selected for redundant GPU detection.
        with Session(engine) as session:
            packs = session.exec(select(Face.model_pack)).all()
        assert set(packs) == {"auraface"}, (
            "run() returned before the model_pack write committed (claim-vs-commit "
            f"race): {packs}"
        )
        assert result["changed_count"] == 1
        assert result["picture_ids"] == [pic_id]
    finally:
        db.shutdown()


def test_commit_failure_surfaces_and_excludes_picture(tmp_path, monkeypatch):
    """C2: a persistent commit failure is logged and NOT counted as changed.

    The previous code swallowed the generic exception and ran dead no-op code,
    so the failure was invisible and the row stayed stale forever with no signal.
    Now _commit re-raises, the failure surfaces on the future, and _run_task
    excludes the picture from changed_ids (the row stays stale, so the finder
    re-selects it — a genuine retry, not silent masking).
    """
    engine = _make_sqlite(tmp_path)
    img_root = tmp_path / "imgs"
    img_root.mkdir()
    pic_id = _seed_stale_picture(engine)

    db = _AsyncFakeDB(engine, str(img_root), commit_delay_s=0.0)
    inf_engine = _make_engine("auraface")

    with Session(engine) as session:
        pics = list(session.exec(select(Picture)).all())

    task = FaceModelRefreshTask(database=db, engine=inf_engine, pictures=pics)
    _patch_detection(monkeypatch, task)

    # Force a persistent (non-Integrity) commit failure.
    def _boom(session, picture_id):
        raise RuntimeError("simulated persistent DB write failure")

    monkeypatch.setattr(FaceModelRefreshTask, "_commit", staticmethod(_boom))

    try:
        result = task.run()
        # The failing picture is not counted as changed.
        assert result["changed_count"] == 0, result
        assert result["picture_ids"] == [], result
        # The row stays stale (old pack), so the finder will re-select it.
        with Session(engine) as session:
            packs = session.exec(select(Face.model_pack)).all()
        assert "buffalo_l" in packs, packs
        _ = pic_id
    finally:
        db.shutdown()


def test_commit_reraises_on_unexpected_error(tmp_path):
    """C2 (unit): _commit re-raises a non-Integrity failure instead of swallowing it."""
    engine = _make_sqlite(tmp_path)
    pic_id = _seed_stale_picture(engine)

    class _ExplodingSession:
        def commit(self):
            raise RuntimeError("boom")

        def rollback(self):
            pass

    with pytest.raises(RuntimeError, match="boom"):
        FaceModelRefreshTask._commit(_ExplodingSession(), pic_id)
