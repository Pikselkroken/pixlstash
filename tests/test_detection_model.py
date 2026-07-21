"""Fast unit tests for the Detection model and the COCO-subset export sidecar.

No inference engine or server is needed here — these exercise the bbox-as-JSON
round-trip, the FK cascade from Picture, and the export sidecar writer.
"""

import io
import json
import zipfile
from types import SimpleNamespace

import sqlalchemy as sa
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from pixlstash.db_models import Detection, Picture
from pixlstash.utils.service.export_utils import ExportUtils


def _make_engine():
    """In-memory SQLite engine with FK enforcement on (as in production)."""
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_con, _record):  # noqa: ANN001
        dbapi_con.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    return engine


def test_detection_table_columns():
    engine = _make_engine()
    cols = {c["name"] for c in sa.inspect(engine).get_columns("detection")}
    assert cols == {
        "id",
        "picture_id",
        "frame_index",
        "detection_index",
        "label",
        "bbox",
        "score",
        "source",
        "attributes",
    }


def test_detection_bbox_round_trips():
    engine = _make_engine()
    with Session(engine) as session:
        pic = Picture(file_path="x.jpg", format="jpg", width=100, height=80)
        session.add(pic)
        session.commit()
        session.refresh(pic)

        det = Detection(
            picture_id=pic.id,
            detection_index=0,
            label="dog",
            bbox=[1, 2, 30, 40],
            score=None,
            source="florence2:od",
        )
        session.add(det)
        session.commit()

        got = Detection.find(session, picture_id=pic.id)[0]
        assert got.bbox == [1, 2, 30, 40]
        assert got.label == "dog"
        assert got.score is None
        assert got.source == "florence2:od"
        # Stored as JSON text under the "bbox" column.
        assert json.loads(got.bbox_) == [1, 2, 30, 40]


def test_detection_bbox_none_when_unset():
    engine = _make_engine()
    with Session(engine) as session:
        pic = Picture(file_path="x.jpg", format="jpg")
        session.add(pic)
        session.commit()
        session.refresh(pic)
        det = Detection(picture_id=pic.id, label="thing")
        session.add(det)
        session.commit()
        assert Detection.find(session, picture_id=pic.id)[0].bbox is None


def test_detection_cascade_deletes_with_picture():
    engine = _make_engine()
    with Session(engine) as session:
        pic = Picture(file_path="x.jpg", format="jpg")
        session.add(pic)
        session.commit()
        session.refresh(pic)
        pic_id = pic.id
        session.add(Detection(picture_id=pic_id, label="dog", bbox=[1, 2, 3, 4]))
        session.commit()

        session.delete(session.get(Picture, pic_id))
        session.commit()
        assert Detection.find(session, picture_id=pic_id) == []


def _read_zip_json(buf, name):
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        return json.loads(zf.read(name))


def test_export_sidecar_schema_and_scores():
    buf = io.BytesIO()
    pic = SimpleNamespace(width=1920, height=1080)
    dets = [
        SimpleNamespace(bbox=[10, 20, 110, 220], label="dog", score=None),
        SimpleNamespace(bbox=[0, 0, 50, 50], label="cat", score=0.9),
    ]
    with zipfile.ZipFile(buf, "w") as zf:
        ExportUtils._write_detection_sidecar(
            zf, "IMG_0001", "IMG_0001.jpg", pic, dets, 1.0
        )

    sidecar = _read_zip_json(buf, "IMG_0001.json")
    assert sidecar["image"] == "IMG_0001.jpg"
    assert sidecar["width"] == 1920 and sidecar["height"] == 1080
    assert sidecar["schema"] == "pixlstash.detections/v1"
    assert sidecar["bbox_format"] == "xyxy_px"
    # Nullable score serialises to 0.0 in the COCO-subset sidecar.
    assert sidecar["objects"][0] == {
        "label": "dog",
        "bbox": [10, 20, 110, 220],
        "score": 0.0,
    }
    assert sidecar["objects"][1]["score"] == 0.9


def test_export_sidecar_scales_with_resolution():
    """Downscaled exports scale both the dimensions and the box coordinates."""
    buf = io.BytesIO()
    pic = SimpleNamespace(width=1920, height=1080)
    dets = [SimpleNamespace(bbox=[10, 20, 110, 220], label="dog", score=None)]
    with zipfile.ZipFile(buf, "w") as zf:
        ExportUtils._write_detection_sidecar(
            zf, "IMG_0002", "IMG_0002.jpg", pic, dets, 0.5
        )

    sidecar = _read_zip_json(buf, "IMG_0002.json")
    assert sidecar["width"] == 960 and sidecar["height"] == 540
    assert sidecar["objects"][0]["bbox"] == [5, 10, 55, 110]


def test_ideogram_sidecar_schema_and_normalized_yxyx():
    """Ideogram-4 caption: normalized [ymin,xmin,ymax,xmax] 0-1000, fixed key order."""
    buf = io.BytesIO()
    pic = SimpleNamespace(width=2000, height=1000)
    dets = [
        SimpleNamespace(bbox=[100, 200, 1100, 800], label="dog", score=None),
        SimpleNamespace(bbox=[0, 0, 2000, 1000], label="cat", score=0.9),
    ]
    with zipfile.ZipFile(buf, "w") as zf:
        ExportUtils._write_ideogram_sidecar(
            zf, "IMG_0001", pic, dets, "a dog and a cat"
        )

    cap = _read_zip_json(buf, "IMG_0001.json")
    # Top-level key order matters for Ideogram-4.
    assert list(cap.keys()) == [
        "high_level_description",
        "compositional_deconstruction",
    ]
    assert cap["high_level_description"] == "a dog and a cat"
    cd = cap["compositional_deconstruction"]
    assert list(cd.keys()) == ["background", "elements"]
    elem = cd["elements"][0]
    assert list(elem.keys()) == ["type", "bbox", "desc"]
    assert elem["type"] == "obj" and elem["desc"] == "dog"
    # x1=100/2000*1000=50, y1=200/1000*1000=200, x2=1100/2000*1000=550, y2=800
    # → [ymin, xmin, ymax, xmax] = [200, 50, 800, 550]
    assert elem["bbox"] == [200, 50, 800, 550]
    assert cd["elements"][1]["bbox"] == [0, 0, 1000, 1000]
    assert "score" not in elem  # Ideogram elements carry no score


def test_ideogram_sidecar_handles_missing_caption_and_dims():
    buf = io.BytesIO()
    dets = [SimpleNamespace(bbox=[1, 2, 3, 4], label="x", score=None)]
    with zipfile.ZipFile(buf, "w") as zf:
        ExportUtils._write_ideogram_sidecar(
            zf, "IMG_0009", SimpleNamespace(width=0, height=0), dets, None
        )
    cap = _read_zip_json(buf, "IMG_0009.json")
    # No caption → no high_level_description; zero dims → no elements.
    assert "high_level_description" not in cap
    assert cap["compositional_deconstruction"]["elements"] == []
