"""Tests for the live "Impossible tags" grid filters (PredicateFilter.impossible_sources).

In-memory DB, exercising the real Picture.find path: the filters are computed live from a
picture's own tags/faces (no precomputed queue). The load-bearing guarantees:

  * ``no_face``   selects no-detected-face pictures that carry a face-requiring tag;
  * ``no_humans`` selects no-detected-face pictures tagged "no humans"/"scenery" that
    also carry a person-tag;
  * both are no-face-gated (a picture with a real face is never selected);
  * multiple kinds are OR'd; an unset filter changes nothing.
"""

import pixlstash.db_models  # noqa: F401  (register all models for create_all)
from pixlstash.db_models import Face, Picture, Tag
from sqlmodel import Session, SQLModel, create_engine


def _make_session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _add_picture(session: Session, path: str, *, real_face: bool, tags: list[str]) -> int:
    pic = Picture(file_path=path, pixel_sha=path, format="JPEG")
    session.add(pic)
    session.commit()
    session.refresh(pic)
    # real_face=False → a sentinel face row (face_index=-1), i.e. "no face detected".
    session.add(Face(picture_id=pic.id, face_index=0 if real_face else -1))
    for tag in tags:
        session.add(Tag(picture_id=pic.id, tag=tag))
    session.commit()
    return pic.id


def _find(session: Session, **kw) -> set[int]:
    return {p.id for p in Picture.find(session, select_fields=["id"], **kw)}


def test_live_impossible_filters():
    with _make_session() as s:
        # A: no face + "no humans" + a hair tag → only no_humans matches.
        a = _add_picture(s, "/a.jpg", real_face=False, tags=["no humans", "brown hair"])
        # B: no face + a face-requiring tag → only no_face matches.
        b = _add_picture(s, "/b.jpg", real_face=False, tags=["face", "brown hair"])
        # C: a REAL face, both kinds of impossible tags → neither filter matches.
        c = _add_picture(
            s, "/c.jpg", real_face=True, tags=["no humans", "face", "brown hair"]
        )
        # D: no face, only a hair tag (no meta-tag, no face-requiring) → neither matches.
        d = _add_picture(s, "/d.jpg", real_face=False, tags=["brown hair"])

        assert _find(s) == {a, b, c, d}  # unset filter: everyone
        assert _find(s, impossible_sources=["no_face"]) == {b}
        assert _find(s, impossible_sources=["no_humans"]) == {a}
        # OR of both kinds
        assert _find(s, impossible_sources=["no_face", "no_humans"]) == {a, b}
        # an unrecognised kind contributes no clause (no rows, not an error)
        assert _find(s, impossible_sources=["object"]) == {a, b, c, d}
