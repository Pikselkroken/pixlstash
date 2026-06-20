"""Tests for the tag-prediction back-fill finder and task.

These cover the pure DB logic (no tagger model needed): which pictures the
finder selects, the matching count, and that the task writes correct prediction
rows while never touching the Tag table.
"""

from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session, create_engine, select

from pixlstash.db_models import TAG_PENDING_SENTINEL
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.tag import Tag
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.tasks.missing_tag_prediction_finder import MissingTagPredictionFinder
from pixlstash.tasks.tag_prediction_backfill_task import TagPredictionBackfillTask
from pixlstash.vault import Vault


def _make_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'backfill.db'}", echo=False, poolclass=NullPool
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    return engine


def _add_picture(session, file_path, tags=(), predictions=(), deleted=False):
    pic = Picture(file_path=file_path, deleted=deleted)
    session.add(pic)
    session.commit()
    session.refresh(pic)
    for tag in tags:
        session.add(Tag(picture_id=pic.id, tag=tag))
    for tag, conf in predictions:
        session.add(
            TagPrediction(
                picture_id=pic.id, tag=tag, confidence=conf, model_version="v1"
            )
        )
    session.commit()
    return pic.id


def test_finder_selects_only_tagged_pictures_without_predictions(tmp_path):
    engine = _make_engine(tmp_path)
    with Session(engine) as session:
        # Target: real tags, no predictions.
        target = _add_picture(session, "a.jpg", tags=["dog", "park"])
        # Has predictions already.
        _add_picture(session, "b.jpg", tags=["cat"], predictions=[("cat", 0.9)])
        # Only the pending sentinel (awaiting first tagging).
        _add_picture(session, "c.jpg", tags=[TAG_PENDING_SENTINEL])
        # No tags at all (tagger found nothing).
        _add_picture(session, "d.jpg", tags=[])
        # Deleted picture.
        _add_picture(session, "e.jpg", tags=["bird"], deleted=True)

        found = MissingTagPredictionFinder._fetch_missing_predictions(session, 100)
        assert [p.id for p in found] == [target]

        count = Vault._count_missing_tag_predictions(session)
        assert count == 1


def test_backfill_writes_predictions_without_touching_tags(tmp_path):
    engine = _make_engine(tmp_path)
    with Session(engine) as session:
        pic_id = _add_picture(session, "a.jpg", tags=["dog", "park"])
        tags_before = set(
            session.exec(select(Tag.tag).where(Tag.picture_id == pic_id)).all()
        )

        # Model scored "dog" high (applied) and "cat" (not applied).
        label_scores = {pic_id: {"dog": 0.92, "cat": 0.10}}
        written = TagPredictionBackfillTask._backfill_predictions(
            session, label_scores, [pic_id], "v7"
        )
        assert written >= 2

        preds = {
            p.tag: p
            for p in session.exec(
                select(TagPrediction).where(TagPrediction.picture_id == pic_id)
            ).all()
        }
        # Applied tag -> CONFIRMED; scored-but-not-applied -> REJECTED.
        assert preds["dog"].status == "CONFIRMED"
        assert preds["cat"].status == "REJECTED"
        # "park" was applied but unscored: gets a confidence-0.0 CONFIRMED row.
        assert preds["park"].status == "CONFIRMED"
        assert preds["park"].confidence == 0.0
        assert preds["dog"].model_version == "v7"

        # The Tag table must be byte-for-byte unchanged.
        tags_after = set(
            session.exec(select(Tag.tag).where(Tag.picture_id == pic_id)).all()
        )
        assert tags_after == tags_before


def test_backfill_marks_unscored_pictures_so_they_drop_out(tmp_path):
    """A picture the model returns no scores for (decode failure) still gets a
    zero-confidence row per tag, so the finder won't reprocess it forever."""
    engine = _make_engine(tmp_path)
    with Session(engine) as session:
        pic_id = _add_picture(session, "broken.jpg", tags=["dog"])

        # No scores for this picture at all.
        written = TagPredictionBackfillTask._backfill_predictions(
            session, {}, [pic_id], "v7"
        )
        assert written == 1

        preds = session.exec(
            select(TagPrediction).where(TagPrediction.picture_id == pic_id)
        ).all()
        assert len(preds) == 1
        assert preds[0].tag == "dog"
        assert preds[0].confidence == 0.0

        # It now has a prediction, so the finder no longer selects it.
        found = MissingTagPredictionFinder._fetch_missing_predictions(session, 100)
        assert pic_id not in [p.id for p in found]
        assert Vault._count_missing_tag_predictions(session) == 0
