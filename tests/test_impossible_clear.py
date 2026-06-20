"""Tests for the bulk impossible-tags clear/restore service.

In-memory DB. Verifies that clearing removes exactly the filter-implied tags, records a
human NEG per removed tag (durable training signal), is no-face-gated, and that restore
(undo) re-adds the tags and resets the ledger.
"""

import pixlstash.db_models  # noqa: F401  (register all models for create_all)
from pixlstash.db_models import Face, Picture, Tag
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.services.impossible_tag_clear_service import (
    clear_in_session,
    restore_in_session,
)
from sqlmodel import Session, SQLModel, create_engine, select


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _add(session: Session, path: str, *, real_face: bool, tags: list[str]) -> int:
    pic = Picture(file_path=path, pixel_sha=path, format="JPEG")
    session.add(pic)
    session.commit()
    session.refresh(pic)
    session.add(Face(picture_id=pic.id, face_index=0 if real_face else -1))
    for t in tags:
        session.add(Tag(picture_id=pic.id, tag=t))
    session.commit()
    return pic.id


def _tags(session: Session, pid: int) -> set[str]:
    return set(session.exec(select(Tag.tag).where(Tag.picture_id == pid)).all())


def _ledger(session: Session, pid: int, tag: str):
    return session.exec(
        select(TagPrediction).where(
            TagPrediction.picture_id == pid, TagPrediction.tag == tag
        )
    ).first()


def test_clear_records_neg_and_is_no_face_gated_and_undo():
    with _session() as s:
        # p1: no face + "no humans" + person tags → no_humans strips ALL person tags.
        p1 = _add(
            s, "/1.jpg", real_face=False, tags=["no humans", "brown hair", "face"]
        )
        # p2: a REAL face → no-face-gated, nothing removed.
        p2 = _add(s, "/2.jpg", real_face=True, tags=["no humans", "face"])
        # p3: no face but no meta-tag and no face-requiring tag → nothing fires.
        p3 = _add(s, "/3.jpg", real_face=False, tags=["brown hair"])

        removed = clear_in_session(s, [p1, p2, p3], ["no_face", "no_humans"])
        removed_set = set(removed)

        # p1: no_humans wins → both person tags gone; meta-tag itself kept.
        assert removed_set == {(p1, "brown hair"), (p1, "face")}
        assert _tags(s, p1) == {"no humans"}
        # p2 untouched (real face); p3 untouched (nothing implied).
        assert _tags(s, p2) == {"no humans", "face"}
        assert _tags(s, p3) == {"brown hair"}

        # A human NEG was recorded for each removed tag.
        for tag in ("brown hair", "face"):
            led = _ledger(s, p1, tag)
            assert led is not None and led.label_state == "NEG"
            assert led.label_source == "human"

        # Undo restores the tags and resets the ledger to UNKNOWN.
        restore_in_session(s, removed)
        assert _tags(s, p1) == {"no humans", "brown hair", "face"}
        for tag in ("brown hair", "face"):
            led = _ledger(s, p1, tag)
            assert led.label_state == "UNKNOWN"
            assert led.label_source is None


def test_no_face_filter_keeps_hair():
    with _session() as s:
        p = _add(s, "/x.jpg", real_face=False, tags=["brown hair", "face", "nose"])
        removed = clear_in_session(s, [p], ["no_face"])
        # only the face-requiring tags go; hair stays
        assert set(removed) == {(p, "face"), (p, "nose")}
        assert _tags(s, p) == {"brown hair"}
