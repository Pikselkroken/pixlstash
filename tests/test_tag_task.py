import pytest

from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session, create_engine, select

from pixlstash.db_models.picture import Picture
from pixlstash.db_models.tag import Tag
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.tagger_plugins.pixlstash_tagger import (
    CENTRE_CROP_TAG_WHITELIST,
    FACE_QUALITY_CROP_TAGS,
    QUALITY_CROP_TAG_WHITELIST,
)
from pixlstash.tasks.tag_task import TagTask


def test_centre_crop_whitelist_excludes_face_tags():
    """The faceless centre-crop fallback must not own face-specific anomaly tags.

    A centre crop has no face, so 'malformed teeth' / 'flux chin' judged from it
    would be meaningless; those stay owned by the face crop only.
    """
    # Face tags are a real subset of the full whitelist.
    assert FACE_QUALITY_CROP_TAGS
    assert FACE_QUALITY_CROP_TAGS <= QUALITY_CROP_TAG_WHITELIST
    # The centre whitelist is exactly the non-face high-res quality tags.
    assert (
        CENTRE_CROP_TAG_WHITELIST == QUALITY_CROP_TAG_WHITELIST - FACE_QUALITY_CROP_TAGS
    )
    assert {"malformed teeth", "flux chin"} <= FACE_QUALITY_CROP_TAGS
    assert not (FACE_QUALITY_CROP_TAGS & CENTRE_CROP_TAG_WHITELIST)
    # The general image-quality tags survive in the centre whitelist.
    assert "blocky" in CENTRE_CROP_TAG_WHITELIST


def _make_engine(tmp_path):
    db_path = tmp_path / "tag-task.db"
    engine = create_engine(f"sqlite:///{db_path}", echo=False, poolclass=NullPool)

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    return engine


def test_add_tags_bulk_skips_missing_picture_ids(tmp_path):
    engine = _make_engine(tmp_path)

    with Session(engine) as session:
        picture = Picture(file_path="existing-picture.jpg")
        session.add(picture)
        session.commit()
        session.refresh(picture)

        updates = [
            {"pic_id": picture.id, "tags": ["jewelry"]},
            {"pic_id": picture.id + 9999, "tags": ["face"]},
        ]

        updated_ids = TagTask._add_tags_bulk(session, updates)

        assert updated_ids == [picture.id]

        saved_tags = session.exec(
            select(Tag.tag).where(Tag.picture_id == picture.id)
        ).all()
        assert set(saved_tags) == {"jewelry"}

        all_tag_rows = session.exec(select(Tag)).all()
        assert len(all_tag_rows) == 1


def test_add_tags_bulk_honours_human_labels(tmp_path):
    """The tagger must not drop a human-confirmed tag nor re-apply a rejected one.

    Regression for the ImageOverlay re-tag bug: a manually-confirmed 'watermark'
    (POS, outside the model vocabulary) vanished after a regenerate because the
    fresh tagger pass rewrote the Tag table from model output alone.
    """
    engine = _make_engine(tmp_path)

    with Session(engine) as session:
        picture = Picture(file_path="p.jpg")
        session.add(picture)
        session.commit()
        session.refresh(picture)

        # Durable human supervision: 'watermark' accepted, 'blurry' rejected.
        session.add(
            TagPrediction(
                picture_id=picture.id,
                tag="watermark",
                confidence=1.0,
                model_version="manual",
                status="CONFIRMED",
                label_state="POS",
                label_source="human",
            )
        )
        session.add(
            TagPrediction(
                picture_id=picture.id,
                tag="blurry",
                confidence=0.0,
                model_version="manual",
                status="REJECTED",
                label_state="NEG",
                label_source="human",
            )
        )
        session.commit()

        # The fresh model pass emits neither 'watermark' nor honours the rejection.
        updated_ids = TagTask._add_tags_bulk(
            session, [{"pic_id": picture.id, "tags": ["woman", "blurry"]}]
        )

        assert updated_ids == [picture.id]
        saved_tags = set(
            session.exec(select(Tag.tag).where(Tag.picture_id == picture.id)).all()
        )
        # 'watermark' is kept (human POS), 'blurry' is dropped (human NEG).
        assert saved_tags == {"woman", "watermark"}


# --- Unprocessable-registry gating (PR #750 review blockers) -----------------


class _FakeDb:
    """Minimal stand-in for the vault database `_load_pic` and marking need."""

    def __init__(self, image_root, registry=None):
        self.image_root = image_root
        if registry is not None:
            self.unprocessable_images = registry


class _RecordingRegistry:
    def __init__(self):
        self.marked = []

    def mark_unprocessable(self, picture_id, file_path, *, reason=""):
        self.marked.append((picture_id, file_path, reason))
        return True


def _task_for(db):
    task = TagTask.__new__(TagTask)
    task._db = db
    return task


def test_a_corrupt_file_is_marked_unprocessable(tmp_path):
    """The positive direction: bytes readable, no decoder can make sense of them."""
    bad = tmp_path / "corrupt.png"
    bad.write_bytes(b"this is definitely not a PNG")
    registry = _RecordingRegistry()
    task = _task_for(_FakeDb(str(tmp_path), registry))
    pic = Picture(id=1, file_path=str(bad))

    file_path, img, undecodable = task._load_pic(pic)

    assert img is None
    assert undecodable is True, "a readable but undecodable file must be markable"
    task._mark_unprocessable(pic, file_path)
    assert registry.marked == [
        (1, str(bad), "tag source could not be decoded"),
    ]


def test_a_transient_load_error_is_never_marked(tmp_path, monkeypatch):
    """The negative direction, and the one that matters.

    A mark suppresses the picture for EVERY batch finder until the file changes,
    so an EMFILE while the preload pool holds handles must not disable a good
    picture for the rest of the server session.
    """
    import errno

    from PIL import Image as PILImage

    good = tmp_path / "good.png"
    PILImage.new("RGB", (8, 8), "red").save(good)

    def _emfile(*_args, **_kwargs):
        raise OSError(errno.EMFILE, "Too many open files")

    monkeypatch.setattr(PILImage, "open", _emfile)
    # The shared fallback loader would hit the same wall; it must not be reached.
    monkeypatch.setattr(
        "pixlstash.tasks.tag_task.ImageUtils.load_image_or_video",
        lambda *_a, **_k: pytest.fail("transient error must not reach the fallback"),
    )
    task = _task_for(_FakeDb(str(tmp_path), _RecordingRegistry()))

    _file_path, img, undecodable = task._load_pic(Picture(id=2, file_path=str(good)))

    assert img is None
    assert undecodable is False, "EMFILE is the machine failing, not the file"


def test_a_mislabelled_video_is_loaded_not_suppressed(tmp_path, monkeypatch):
    """A real video named `.png` is decodable by every other pipeline (#750 B3).

    The tag path must not be stricter than the pipelines its mark suppresses, so
    a PIL failure falls back to the shared loader before concluding anything.
    """
    import numpy as np

    lying = tmp_path / "clip.png"
    lying.write_bytes(b"\x00\x00\x00\x18ftypmp42 not really a png")
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    monkeypatch.setattr(
        "pixlstash.tasks.tag_task.ImageUtils.load_image_or_video",
        lambda *_a, **_k: frame,
    )
    registry = _RecordingRegistry()
    task = _task_for(_FakeDb(str(tmp_path), registry))

    _file_path, img, undecodable = task._load_pic(Picture(id=3, file_path=str(lying)))

    assert img is not None, "the shared loader decoded it, so tagging must use it"
    assert img.size == (4, 4)
    assert undecodable is False
    assert registry.marked == []


def test_transient_error_classifier():
    """`OSError.errno` is the discriminator; PIL's decode failures carry none."""
    import errno

    from PIL import Image as PILImage

    from pixlstash.tasks.tag_task import _is_transient_load_error

    assert _is_transient_load_error(OSError(errno.EMFILE, "Too many open files"))
    assert _is_transient_load_error(OSError(errno.EIO, "I/O error"))
    assert _is_transient_load_error(MemoryError())
    # PIL raises both of these with errno unset: the file, not the machine.
    assert not _is_transient_load_error(PILImage.UnidentifiedImageError("nope"))
    assert not _is_transient_load_error(OSError("image file is truncated"))
    assert not _is_transient_load_error(PILImage.DecompressionBombError("huge"))


# --- The per-picture crop guard (PR #750 review blocker B2) ------------------


class _FakeFace:
    def __init__(self, face_id, bbox, face_index=0):
        self.id = face_id
        self.bbox = bbox
        self.face_index = face_index


def _png(tmp_path, name, size=(64, 48)):
    from PIL import Image as PILImage

    path = tmp_path / name
    PILImage.new("RGB", size, "blue").save(path)
    return path


def test_a_malformed_bbox_costs_only_its_own_picture(tmp_path):
    """The blocker: one bad face row must not cost the batch its crops.

    `Face.bbox` is json.loads of a free-text column and is never length-checked,
    so a row that is not [x1,y1,x2,y2] raises inside the crop build. Inline in
    the caller's loop that escaped to the pass-level handler and every remaining
    picture in the task silently lost its quality crop.
    """
    task = _task_for(_FakeDb(str(tmp_path)))
    pics = [
        Picture(id=1, file_path=str(_png(tmp_path, "first.png"))),
        Picture(id=2, file_path=str(_png(tmp_path, "second.png"))),
        Picture(id=3, file_path=str(_png(tmp_path, "third.png"))),
    ]
    faces_by_pic = {
        1: [_FakeFace(11, [1, 2, 40, 30])],
        2: [_FakeFace(22, [1, 2])],  # truncated: IndexError in the max() key
        3: [_FakeFace(33, [3, 4, 44, 34])],
    }
    preloaded = {}

    built = [
        task._build_quality_crop(pic, faces_by_pic[pic.id], 32, preloaded)
        for pic in pics
    ]

    assert built[1] is None, "the malformed row yields no crop"
    assert built[0] is not None and built[2] is not None, (
        "pictures either side of the bad row must still get their crops"
    )
    assert [b[0] for b in built if b] == [
        f"{pics[0].file_path}#face11",
        f"{pics[2].file_path}#face33",
    ]


def test_a_bbox_whose_json_is_corrupt_is_survived(tmp_path):
    """Malformed bbox *JSON* raises on attribute access, before any indexing.

    This one was unguarded even before the PR: the valid_faces comprehension sat
    outside the old try, so json.loads blew up the whole pass.
    """

    class _CorruptBboxFace:
        id = 44

        @property
        def bbox(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    task = _task_for(_FakeDb(str(tmp_path)))
    pic = Picture(id=4, file_path=str(_png(tmp_path, "corrupt-bbox.png")))

    assert task._build_quality_crop(pic, [_CorruptBboxFace()], 32, {}) is None


def test_no_faces_falls_back_to_a_centre_crop(tmp_path):
    """The faceless path still contributes, flagged for the reduced whitelist."""
    task = _task_for(_FakeDb(str(tmp_path)))
    pic = Picture(id=5, file_path=str(_png(tmp_path, "faceless.png")))

    key, crop, file_path, is_centre_crop = task._build_quality_crop(pic, [], 32, {})

    assert is_centre_crop is True
    assert key == f"{file_path}#centre"
    assert crop.size == (32, 32)


def test_a_negative_face_index_is_not_a_face(tmp_path):
    """face_index < 0 is filtered out, so the picture takes the centre crop."""
    task = _task_for(_FakeDb(str(tmp_path)))
    pic = Picture(id=6, file_path=str(_png(tmp_path, "negative-index.png")))

    _key, _crop, _path, is_centre_crop = task._build_quality_crop(
        pic, [_FakeFace(66, [1, 2, 40, 30], face_index=-1)], 32, {}
    )

    assert is_centre_crop is True


def test_an_undecodable_picture_is_marked_once_from_the_crop_pass(tmp_path):
    """The registry call still happens through the extracted seam."""
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"nope")
    registry = _RecordingRegistry()
    task = _task_for(_FakeDb(str(tmp_path), registry))
    pic = Picture(id=7, file_path=str(bad))

    assert task._build_quality_crop(pic, [], 32, {}) is None
    assert registry.marked == [(7, str(bad), "tag source could not be decoded")]


def _rotated_jpeg(tmp_path, name, size=(4608, 2592), orientation=6):
    """A landscape JPEG that EXIF says is portrait - an ordinary phone photo."""
    from PIL import Image as PILImage

    path = tmp_path / name
    image = PILImage.new("RGB", size, "blue")
    exif = image.getexif()
    exif[274] = orientation
    image.save(path, exif=exif)
    return path


def test_a_rotated_photo_is_loaded_the_way_its_faces_were_measured(tmp_path):
    """Orientation 6 means 4608x2592 on disk and 2592x4608 to everyone else.

    Face bboxes are recorded against the transposed frame, because
    `load_image_bgr_reduced` transposes. This loader did not, so every rotated
    photo reached the tagger sideways and its recorded faces pointed off the
    edge of the frame it produced.
    """
    path = _rotated_jpeg(tmp_path, "phone.jpg")
    task = _task_for(_FakeDb(str(tmp_path)))

    _file_path, img, _undecodable = task._load_pic(Picture(id=1, file_path=str(path)))

    assert img is not None
    assert img.size == (2592, 4608), (
        "the tagger must see the picture the way a person does, and the way the "
        f"face pass measured it; got {img.size}"
    )


def test_a_face_low_in_a_rotated_photo_still_yields_a_crop(tmp_path):
    """The exact failure from a real library, reproduced.

    Picture 4401: orientation 6, 4608x2592 stored, face bbox
    [432, 1971, 1584, 3699]. Against the untransposed frame the box's centre
    sits below the image, `expand_bbox_to_square` clamps the bottom edge and
    not the top, and PIL refuses: "Coordinate 'lower' is less than 'upper'".
    """
    path = _rotated_jpeg(tmp_path, "low-face.jpg")
    task = _task_for(_FakeDb(str(tmp_path)))
    pic = Picture(id=4401, file_path=str(path))
    faces = [_FakeFace(1, [432.0, 1971.0, 1584.0, 3699.0])]

    built = task._build_quality_crop(pic, faces, 448, {})

    assert built is not None, "the crop must be buildable, not warned away"
    key, crop, _source, is_centre = built
    assert key.endswith("#face1"), "and it must be the FACE crop, not the fallback"
    assert is_centre is False
    assert crop.size == (448, 448), f"a square target-sized crop; got {crop.size}"
