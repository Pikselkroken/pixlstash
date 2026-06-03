"""Unit tests for :class:`PredicateFilter`.

These run against a throwaway SQLite database (no Server needed): the predicate
compiler only relies on built-in SQLite features (``json_each``, ``LOWER``), so a bare
``SQLModel.metadata.create_all`` engine is enough.  Each test asserts the matching id
set for a field, that :meth:`PredicateFilter.matches` agrees with the set query for
every picture, and covers the documented tricky cases.
"""

import json

import pytest
from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine, select

# Importing the models registers them on SQLModel.metadata.
from pixlstash.db_models import Face, Picture, Tag, TagPrediction  # noqa: F401
from pixlstash.utils.query.predicate_filter import PredicateFilter


@pytest.fixture
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'predicate.db'}",
        echo=False,
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _add_picture(session, **kwargs):
    kwargs.setdefault("file_path", f"pic_{id(kwargs)}.jpg")
    pic = Picture(**kwargs)
    session.add(pic)
    session.commit()
    session.refresh(pic)
    return pic


def _add_tag(session, picture_id, tag):
    session.add(Tag(picture_id=picture_id, tag=tag))
    session.commit()


def _add_prediction(session, picture_id, tag, confidence):
    session.add(
        TagPrediction(
            picture_id=picture_id,
            tag=tag,
            confidence=confidence,
            model_version="epoch-test",
        )
    )
    session.commit()


def _add_face(session, picture_id, face_index=0, character_id=None):
    session.add(
        Face(picture_id=picture_id, face_index=face_index, character_id=character_id)
    )
    session.commit()


def _matching_ids(session, flt: PredicateFilter) -> set[int]:
    stmt = flt.apply(select(Picture.id))
    return set(session.exec(stmt).all())


def _all_ids(session) -> list[int]:
    return list(session.exec(select(Picture.id)).all())


def _assert_matches_agrees(session, flt: PredicateFilter, expected: set[int]):
    """``matches()`` must agree with ``apply()`` for every picture in the DB."""
    via_set = _matching_ids(session, flt)
    assert via_set == expected
    for pid in _all_ids(session):
        assert flt.matches(session, pid) is (pid in expected)


# --------------------------------------------------------------------------- #
# Lifecycle flags
# --------------------------------------------------------------------------- #


def test_default_excludes_deleted_and_import_excluded(session):
    live = _add_picture(session, file_path="a.jpg")
    _add_picture(session, file_path="b.jpg", deleted=True)
    _add_picture(session, file_path="c.jpg", import_excluded=True)

    flt = PredicateFilter()
    _assert_matches_agrees(session, flt, {live.id})


def test_only_deleted(session):
    _add_picture(session, file_path="a.jpg")
    gone = _add_picture(session, file_path="b.jpg", deleted=True)

    flt = PredicateFilter(only_deleted=True, exclude_import_excluded=False)
    _assert_matches_agrees(session, flt, {gone.id})


def test_include_deleted(session):
    a = _add_picture(session, file_path="a.jpg")
    b = _add_picture(session, file_path="b.jpg", deleted=True)

    flt = PredicateFilter(include_deleted=True, exclude_import_excluded=False)
    _assert_matches_agrees(session, flt, {a.id, b.id})


def test_apply_deleted_filter_false_emits_no_deleted_clause(session):
    a = _add_picture(session, file_path="a.jpg")
    b = _add_picture(session, file_path="b.jpg", deleted=True)

    flt = PredicateFilter(apply_deleted_filter=False, exclude_import_excluded=False)
    _assert_matches_agrees(session, flt, {a.id, b.id})


def test_include_unimported_false_requires_imported_at(session):
    from datetime import datetime

    imported = _add_picture(
        session, file_path="a.jpg", imported_at=datetime(2020, 1, 1)
    )
    _add_picture(session, file_path="b.jpg", imported_at=None)

    flt = PredicateFilter(include_unimported=False)
    _assert_matches_agrees(session, flt, {imported.id})


# --------------------------------------------------------------------------- #
# Scalar / bucket predicates
# --------------------------------------------------------------------------- #


def test_format_filter(session):
    png = _add_picture(session, file_path="a.png", format="PNG")
    _add_picture(session, file_path="b.jpg", format="JPEG")

    flt = PredicateFilter(format=["PNG"])
    _assert_matches_agrees(session, flt, {png.id})


def test_score_range(session):
    _add_picture(session, file_path="a.jpg", score=1)
    mid = _add_picture(session, file_path="b.jpg", score=5)
    _add_picture(session, file_path="c.jpg", score=9)

    flt = PredicateFilter(min_score=3, max_score=7)
    _assert_matches_agrees(session, flt, {mid.id})


def test_smart_score_bucket_unscored(session):
    unscored = _add_picture(session, file_path="a.jpg", smart_score=None)
    _add_picture(session, file_path="b.jpg", smart_score=2.5)

    flt = PredicateFilter(smart_score_bucket="unscored")
    _assert_matches_agrees(session, flt, {unscored.id})


def test_smart_score_bucket_3_4_boundary(session):
    # 3-4 bucket is [3.0, 4.0): includes 3.0, excludes 4.0.
    low = _add_picture(session, file_path="lo.jpg", smart_score=2.99)
    at3 = _add_picture(session, file_path="at3.jpg", smart_score=3.0)
    mid = _add_picture(session, file_path="mid.jpg", smart_score=3.99)
    at4 = _add_picture(session, file_path="at4.jpg", smart_score=4.0)

    flt = PredicateFilter(smart_score_bucket="3-4")
    _assert_matches_agrees(session, flt, {at3.id, mid.id})
    assert low.id not in _matching_ids(session, flt)
    assert at4.id not in _matching_ids(session, flt)


def test_resolution_bucket_unknown(session):
    no_w = _add_picture(session, file_path="a.jpg", width=None, height=100)
    no_h = _add_picture(session, file_path="b.jpg", width=100, height=None)
    _add_picture(session, file_path="c.jpg", width=100, height=100)

    flt = PredicateFilter(resolution_bucket="unknown")
    _assert_matches_agrees(session, flt, {no_w.id, no_h.id})


def test_resolution_bucket_1_4mp(session):
    _add_picture(session, file_path="small.jpg", width=500, height=500)  # 0.25 MP
    one_mp = _add_picture(session, file_path="one.jpg", width=1000, height=1000)  # 1 MP
    _add_picture(session, file_path="big.jpg", width=3000, height=3000)  # 9 MP

    flt = PredicateFilter(resolution_bucket="1-4mp")
    _assert_matches_agrees(session, flt, {one_mp.id})


# --------------------------------------------------------------------------- #
# Tag / prediction predicates
# --------------------------------------------------------------------------- #


def test_tags_filter_requires_all_tags(session):
    a = _add_picture(session, file_path="a.jpg")
    b = _add_picture(session, file_path="b.jpg")
    _add_tag(session, a.id, "cat")
    _add_tag(session, a.id, "dog")
    _add_tag(session, b.id, "cat")

    flt = PredicateFilter(tags_filter=["cat", "dog"])
    _assert_matches_agrees(session, flt, {a.id})


def test_tags_rejected_filter(session):
    a = _add_picture(session, file_path="a.jpg")
    b = _add_picture(session, file_path="b.jpg")
    _add_tag(session, a.id, "cat")

    flt = PredicateFilter(tags_rejected_filter=["cat"])
    _assert_matches_agrees(session, flt, {b.id})


def test_hidden_tags_filter_is_case_insensitive(session):
    a = _add_picture(session, file_path="a.jpg")
    b = _add_picture(session, file_path="b.jpg")
    # Stored mixed-case; hidden filter passes lowercase and must still exclude.
    _add_tag(session, a.id, "NSFW")

    flt = PredicateFilter(hidden_tags_filter=["nsfw"])
    _assert_matches_agrees(session, flt, {b.id})


def test_confidence_above_standard(session):
    a = _add_picture(session, file_path="a.jpg")
    b = _add_picture(session, file_path="b.jpg")
    c = _add_picture(session, file_path="c.jpg")
    _add_prediction(session, a.id, "sunset", 0.9)  # predicted, not applied -> match
    _add_prediction(session, b.id, "sunset", 0.1)  # below threshold -> no match
    _add_prediction(session, c.id, "sunset", 0.9)
    _add_tag(session, c.id, "sunset")  # already applied -> excluded by NOT EXISTS tag

    flt = PredicateFilter(tags_confidence_above_filter=["sunset:0.5"])
    _assert_matches_agrees(session, flt, {a.id})


def test_confidence_above_zero_threshold_branch(session):
    """threshold <= 0.0 matches predicted-only OR applied-only (XOR-like)."""
    predicted_only = _add_picture(session, file_path="p.jpg")
    applied_only = _add_picture(session, file_path="ap.jpg")
    both = _add_picture(session, file_path="both.jpg")
    _add_picture(session, file_path="none.jpg")  # neither predicted nor applied

    _add_prediction(session, predicted_only.id, "sunset", 0.3)
    _add_tag(session, applied_only.id, "sunset")
    _add_prediction(session, both.id, "sunset", 0.3)
    _add_tag(session, both.id, "sunset")
    # neither: nothing

    flt = PredicateFilter(tags_confidence_above_filter=["sunset:0.0"])
    _assert_matches_agrees(session, flt, {predicted_only.id, applied_only.id})


def test_confidence_below(session):
    # below filter: a low-confidence prediction AND the tag is applied.
    a = _add_picture(session, file_path="a.jpg")
    b = _add_picture(session, file_path="b.jpg")
    _add_prediction(session, a.id, "sunset", 0.1)
    _add_tag(session, a.id, "sunset")
    _add_prediction(session, b.id, "sunset", 0.9)
    _add_tag(session, b.id, "sunset")

    flt = PredicateFilter(tags_confidence_below_filter=["sunset:0.5"])
    _assert_matches_agrees(session, flt, {a.id})


# --------------------------------------------------------------------------- #
# ComfyUI / face predicates
# --------------------------------------------------------------------------- #


def test_comfyui_models_filter_requires_all(session):
    a = _add_picture(
        session, file_path="a.jpg", comfyui_models=json.dumps(["m1", "m2"])
    )
    _add_picture(session, file_path="b.jpg", comfyui_models=json.dumps(["m1"]))

    flt = PredicateFilter(comfyui_models_filter=["m1", "m2"])
    _assert_matches_agrees(session, flt, {a.id})


def test_comfyui_loras_filter(session):
    a = _add_picture(session, file_path="a.jpg", comfyui_loras=json.dumps(["lora_x"]))
    _add_picture(session, file_path="b.jpg", comfyui_loras=json.dumps(["lora_y"]))

    flt = PredicateFilter(comfyui_loras_filter=["lora_x"])
    _assert_matches_agrees(session, flt, {a.id})


def test_face_filter(session):
    with_face = _add_picture(session, file_path="a.jpg")
    without_face = _add_picture(session, file_path="b.jpg")
    sentinel_only = _add_picture(session, file_path="c.jpg")
    _add_face(session, with_face.id, face_index=0)
    _add_face(session, sentinel_only.id, face_index=-1)  # -1 = "no face" sentinel

    with_flt = PredicateFilter(face_filter="with_face")
    _assert_matches_agrees(session, with_flt, {with_face.id})

    without_flt = PredicateFilter(face_filter="without_face")
    _assert_matches_agrees(
        session, without_flt, {without_face.id, sentinel_only.id}
    )


# --------------------------------------------------------------------------- #
# File path / import source
# --------------------------------------------------------------------------- #


def test_file_path_prefix_children_only(session):
    direct = _add_picture(session, file_path="/ref/photos/a.jpg")
    _add_picture(session, file_path="/ref/photos/sub/b.jpg")  # sub-dir excluded
    _add_picture(session, file_path="/ref/photos2/c.jpg")  # sibling prefix excluded

    flt = PredicateFilter(file_path_prefix="/ref/photos")
    _assert_matches_agrees(session, flt, {direct.id})


def test_file_path_prefix_subtree(session):
    direct = _add_picture(session, file_path="/ref/photos/a.jpg")
    nested = _add_picture(session, file_path="/ref/photos/sub/b.jpg")
    _add_picture(session, file_path="/other/c.jpg")

    flt = PredicateFilter(
        file_path_prefix="/ref/photos", file_path_prefix_children_only=False
    )
    _assert_matches_agrees(session, flt, {direct.id, nested.id})


def test_import_source_folder(session):
    a = _add_picture(session, file_path="a.jpg", import_source_folder="/inbox")
    _add_picture(session, file_path="b.jpg", import_source_folder="/elsewhere")

    flt = PredicateFilter(import_source_folder="/inbox")
    _assert_matches_agrees(session, flt, {a.id})


# --------------------------------------------------------------------------- #
# Combination
# --------------------------------------------------------------------------- #


def test_combined_predicates(session):
    match = _add_picture(session, file_path="m.png", format="PNG", score=5)
    _add_picture(session, file_path="wrong_format.jpg", format="JPEG", score=5)
    _add_picture(session, file_path="wrong_score.png", format="PNG", score=1)
    _add_tag(session, match.id, "keep")

    flt = PredicateFilter(format=["PNG"], min_score=3, tags_filter=["keep"])
    _assert_matches_agrees(session, flt, {match.id})


# --------------------------------------------------------------------------- #
# from_query_params parsing
# --------------------------------------------------------------------------- #


class _FakeRequest:
    def __init__(self, items):
        from starlette.datastructures import QueryParams

        self.query_params = QueryParams(items)


def test_from_query_params_full_vocabulary():
    req = _FakeRequest(
        [
            ("format", "PNG"),
            ("format", "JPEG"),
            ("min_score", "2"),
            ("max_score", "8"),
            ("smart_score_bucket", "3-4"),
            ("resolution_bucket", "1-4mp"),
            ("comfyui_model", "m1"),
            ("comfyui_lora", "l1"),
            ("tag", "cat"),
            ("tag", "dog"),
            ("rejected_tag", "blurry"),
            ("hidden_tag", "nsfw"),
            ("tag_confidence_above", "sunset:0.5"),
            ("tag_confidence_below", "noise:0.2"),
            ("face_filter", "with_face"),
            ("file_path_prefix", "/ref/photos"),
            ("import_source_folder", "/inbox"),
        ]
    )
    flt = PredicateFilter.from_query_params(req)

    assert flt.format == ["PNG", "JPEG"]
    assert flt.min_score == 2
    assert flt.max_score == 8
    assert flt.smart_score_bucket == "3-4"
    assert flt.resolution_bucket == "1-4mp"
    assert flt.comfyui_models_filter == ["m1"]
    assert flt.comfyui_loras_filter == ["l1"]
    assert flt.tags_filter == ["cat", "dog"]
    assert flt.tags_rejected_filter == ["blurry"]
    assert flt.hidden_tags_filter == ["nsfw"]
    assert flt.tags_confidence_above_filter == ["sunset:0.5"]
    assert flt.tags_confidence_below_filter == ["noise:0.2"]
    assert flt.face_filter == "with_face"
    assert flt.file_path_prefix == "/ref/photos"
    assert flt.import_source_folder == "/inbox"
    assert flt.file_path_prefix_children_only is True


def test_from_query_params_empty_leaves_defaults():
    flt = PredicateFilter.from_query_params(_FakeRequest([]))
    assert flt.format is None
    assert flt.min_score is None
    assert flt.tags_filter is None
    assert flt.face_filter is None
    # Defaults describe the "live pictures" predicate.
    assert flt.apply_deleted_filter is True
    assert flt.exclude_import_excluded is True


def test_from_query_params_children_only_override():
    flt = PredicateFilter.from_query_params(
        _FakeRequest([("file_path_prefix", "/x")]), children_only=False
    )
    assert flt.file_path_prefix == "/x"
    assert flt.file_path_prefix_children_only is False


def test_from_query_params_runs_against_db(session):
    """The parsed filter must compile and execute end-to-end."""
    a = _add_picture(session, file_path="a.png", format="PNG", score=5)
    _add_picture(session, file_path="b.jpg", format="JPEG", score=5)
    _add_tag(session, a.id, "keep")

    req = _FakeRequest([("format", "PNG"), ("min_score", "3"), ("tag", "keep")])
    flt = PredicateFilter.from_query_params(req)
    _assert_matches_agrees(session, flt, {a.id})
