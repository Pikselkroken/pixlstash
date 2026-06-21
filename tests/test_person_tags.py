"""Unit tests for the impossible-tag classifier (pixlstash.utils.service.person_tags).

Pure-Python, no DB: exercises the description verdict and the per-signal strip plan that
the impossible-tag scan relies on. The load-bearing guarantees are:

  * a "no humans" meta-tag (no face) strips ALL person-tags (the strongest signal);
  * an object caption strips ALL person-tags (lower-confidence, caption-miss risk);
  * a face-requiring tag with no face strips ONLY the face-requiring tags and KEEPS
    hair/body (the hand/shoulder-with-hair safety net);
  * each signal carries its own ``source`` (the named grid filter) and score.
"""

from pixlstash.db_models.tag import make_description_sentinel
from pixlstash.utils.service.person_tags import (
    SOURCE_NO_FACE,
    SOURCE_NO_HUMANS,
    SOURCE_OBJECT,
    TIER_NO_FACE_SCORE,
    TIER_NO_HUMANS_SCORE,
    TIER_OBJECT_SCORE,
    classify_description,
    is_face_requiring,
    is_person_tag,
    plan_strips,
    tags_to_clear,
)


def test_membership_is_case_insensitive_and_categorised():
    assert is_face_requiring("Nose")
    assert is_face_requiring("brown eyes")
    assert not is_face_requiring("brown hair")  # hair is person, not face-requiring
    assert is_person_tag("brown hair")
    assert is_person_tag("hand")
    assert is_person_tag("nose")  # face-requiring is a subset of person tags
    assert not is_person_tag("wooden wardrobe")
    assert not is_person_tag("no humans")  # a meta-tag, never a person tag


def test_object_caption_has_no_person_words():
    assert classify_description("a wooden wardrobe with a mirror") == "object"
    assert classify_description("a red motorcycle parked on the street") == "object"
    assert classify_description("a computer monitor on a desk") == "object"


def test_bodypart_caption():
    assert classify_description("a close-up of a hand") == "bodypart"
    assert classify_description("bare shoulder against a wall") == "bodypart"


def test_person_caption_is_ambiguous_not_object():
    assert classify_description("a woman with long brown hair") == "ambiguous"
    assert classify_description("a portrait of a man") == "ambiguous"


def test_empty_and_sentinel_descriptions_are_ambiguous():
    assert classify_description(None) == "ambiguous"
    assert classify_description("") == "ambiguous"
    assert classify_description("   ") == "ambiguous"
    assert classify_description(make_description_sentinel("joycaption")) == "ambiguous"


def test_no_humans_meta_tag_strips_all_person_tags_strongest():
    # The meta-tag wins over everything; the meta-tag itself is never flagged.
    plan = plan_strips(
        "a wooden wardrobe",
        ["no humans", "brown hair", "face", "hand"],
    )
    assert plan["source"] == SOURCE_NO_HUMANS
    assert plan["verdict"] == "no_humans"
    assert plan["flag"] == {"brown hair", "face", "hand"}
    assert "no humans" not in plan["flag"]
    assert plan["score"] == TIER_NO_HUMANS_SCORE


def test_object_caption_strips_all_person_tags():
    plan = plan_strips(
        "a wooden wardrobe with a mirror",
        ["brown hair", "face", "nose", "hand", "wooden"],
    )
    assert plan["source"] == SOURCE_OBJECT
    assert plan["verdict"] == "object"
    # every person-tag flagged; the non-person "wooden" tag is left alone
    assert plan["flag"] == {"brown hair", "face", "nose", "hand"}
    assert plan["score"] == TIER_OBJECT_SCORE


def test_no_face_caption_strips_only_face_requiring_keeps_hair():
    # the hand/shoulder-with-hair safety net: keep "brown hair", strip "nose"/"lips"
    plan = plan_strips("a close-up of a hand", ["brown hair", "nose", "lips"])
    assert plan["source"] == SOURCE_NO_FACE
    assert plan["verdict"] == "no_face"
    assert plan["flag"] == {"nose", "lips"}
    assert "brown hair" not in plan["flag"]
    assert plan["score"] == TIER_NO_FACE_SCORE


def test_real_person_caption_never_strips_hair():
    plan = plan_strips("a woman with long brown hair", ["brown hair", "face"])
    assert plan["source"] == SOURCE_NO_FACE
    # face is face-requiring → stripped; hair kept because it's not an object
    assert plan["flag"] == {"face"}
    assert "brown hair" not in plan["flag"]


def test_ambiguous_with_only_hair_flags_nothing():
    # no face-requiring tag, no object evidence → nothing fires (left untouched)
    plan = plan_strips(None, ["brown hair", "long hair"])
    assert plan["source"] is None
    assert plan["flag"] == set()
    assert plan["score"] == 0.0


def test_meta_tag_outranks_object_caption_and_face():
    # precedence: a meta-tag present wins even when the caption mentions a person
    plan = plan_strips("a woman with brown hair", ["no humans", "brown hair", "lips"])
    assert plan["source"] == SOURCE_NO_HUMANS
    assert plan["flag"] == {"brown hair", "lips"}


def test_to_clear_object_strips_all_person_tags():
    # description-driven "object" filter: no face + object caption → every person-tag goes
    out = tags_to_clear(
        {"object"},
        ["brown hair", "face", "hand", "wooden"],
        has_real_face=False,
        description="a wooden wardrobe with a mirror",
    )
    assert out == {"brown hair", "face", "hand"}  # the non-person "wooden" is kept


def test_to_clear_object_needs_object_caption():
    # a person caption is not "object" → the object filter strips nothing
    out = tags_to_clear(
        {"object"},
        ["brown hair", "face"],
        has_real_face=False,
        description="a woman with long brown hair",
    )
    assert out == set()


def test_to_clear_object_is_no_face_gated():
    # a real detected face short-circuits every filter, the object filter included
    out = tags_to_clear(
        {"object"},
        ["brown hair", "face"],
        has_real_face=True,
        description="a wooden wardrobe",
    )
    assert out == set()
