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
