"""The character training-set picker, on synthetic candidates (no server)."""

import numpy as np

from pixlstash.training import dataset_picker as dp
from pixlstash.training.dataset_picker import Candidate, rank


def _emb(seed):
    rng = np.random.default_rng(seed)
    return rng.standard_normal(16).astype(np.float32)


def _cand(pid, **kw):
    kw.setdefault("rating", 4)
    kw.setdefault("smart_score", 3.0)
    kw.setdefault("likeness", 0.8)
    kw.setdefault("tags", ("portrait",))
    return Candidate(picture_id=pid, **kw)


def _ids(ranking):
    return [p.picture_id for p in ranking.picks]


def _library(n=40):
    framings = ("close-up", "portrait", "upper body", "full body")
    faces = ("smile", "open mouth", "closed eyes", "")
    return [
        _cand(
            i,
            rating=(i * 7) % 6,
            smart_score=(i * 13) % 50 / 10,
            likeness=0.5 + (i * 11) % 40 / 100,
            tags=(framings[i % 4], faces[(i // 4) % 4]),
            embedding=_emb(i),
        )
        for i in range(1, n + 1)
    ]


def test_a_shorter_ranking_is_a_prefix_of_a_longer_one(monkeypatch):
    lib = _library()
    monkeypatch.setattr(dp, "MAX_PICKS", 20)
    top20 = _ids(rank(lib)["stills"])
    monkeypatch.setattr(dp, "MAX_PICKS", 15)
    top15 = _ids(rank(lib)["stills"])
    assert len(top20) == 20 and len(top15) == 15
    assert top15 == top20[:15]


def test_an_uncovered_framing_beats_a_slightly_better_repeat():
    same = _emb(1)
    lib = [
        _cand(1, rating=5, tags=("portrait",), embedding=same),
        _cand(2, rating=5, likeness=0.79, tags=("portrait",), embedding=same),
        _cand(3, rating=5, likeness=0.70, tags=("full body",), embedding=same),
    ]
    assert _ids(rank(lib)["stills"])[:2] == [1, 3]


def test_each_exclusion_sets_aside_and_its_toggle_lets_back_in():
    lib = [
        _cand(1, rating=5),
        _cand(2, face_count_max=2),
        _cand(3, rating=2),
        _cand(4, rating=0, smart_score=dp.SMART_SCORE_FLOOR - 0.5),
        _cand(5, likeness=dp.LIKENESS_FLOOR - 0.05),
        _cand(6, likeness=None),
        _cand(7, rating=4),
        _cand(8, rating=3),
    ]
    pairs = [(7, 8)]
    out = rank(lib, pairs)["stills"]
    assert sorted(_ids(out)) == [1, 7]
    assert out.excluded == {
        "other_people": 1,
        "near_duplicates": 1,
        "low_quality": 2,
        "low_likeness": 2,
    }
    assert out.would_add == {
        "other_people": 1,
        "near_duplicates": 1,
        "low_quality": 2,
        "low_likeness": 2,
    }
    for reason, back in (
        ("other_people", {2}),
        ("near_duplicates", {8}),
        ("low_quality", {3, 4}),
        ("low_likeness", {5, 6}),
    ):
        wider = rank(lib, pairs, include={reason: True})["stills"]
        assert set(_ids(wider)) == {1, 7} | back, reason
        assert wider.would_add[reason] == 0


def test_would_add_counts_only_what_that_toggle_alone_lets_in():
    # Picture 2 fails two filters, so neither toggle alone lets it in.
    lib = [_cand(1), _cand(2, face_count_max=3, rating=1), _cand(3, rating=1)]
    out = rank(lib)["stills"]
    assert out.excluded["other_people"] == 1 and out.excluded["low_quality"] == 2
    assert out.would_add["other_people"] == 0
    assert out.would_add["low_quality"] == 1


def test_a_near_duplicate_group_keeps_its_best_member():
    lib = [_cand(1, rating=3), _cand(2, rating=5), _cand(3, rating=4)]
    out = rank(lib, [(1, 2), (2, 3)])["stills"]
    assert _ids(out) == [2]


def test_references_take_five_stars_then_four_then_smart_score():
    vec = _emb(0).tobytes()
    faces = [
        (10, 1, 0, 9.0, vec),
        (11, 2, 4, 1.0, vec),
        (12, 3, 5, 0.5, vec),
        (13, 4, 0, 4.0, vec),
        (14, 3, 5, 0.5, vec),  # a second face on picture 3
        (15, 5, 5, 3.0, None),  # no features
    ]
    refs = dp.pick_references(faces)
    assert [r[0] for r in refs] == [12, 11, 10, 13]


def test_a_reference_face_does_not_score_itself():
    axes = np.eye(3, dtype=np.float32)
    refs = [(i + 1, axes[i].tobytes()) for i in range(3)]
    scores = dp.face_likeness(refs, refs + [(4, axes[0].tobytes())])
    # Against itself face 1 would score as face 4 does; against the others, 0.
    assert scores[4] > 0.9
    assert scores[1] < 0.1


def test_tags_are_case_folded():
    c = Candidate(picture_id=1, tags=("Full Body", " SMILE "))
    assert c.framing == "full body"
    assert c.expressions == ("smile",)


def test_a_picture_with_no_framing_tag_is_framed_by_its_face():
    assert Candidate(picture_id=1, face_area_ratio=0.3).framing == "close-up"
    assert Candidate(picture_id=1, face_area_ratio=0.001).framing == "full body"
    assert Candidate(picture_id=1).expressions == ("neutral",)


def test_stills_and_clips_rank_separately():
    lib = [_cand(1), _cand(2, is_clip=True), _cand(3, is_clip=True)]
    out = rank(lib, [(1, 2)])
    assert _ids(out["stills"]) == [1]
    assert sorted(_ids(out["clips"])) == [2, 3]
    assert out["clips"].excluded["near_duplicates"] == 0


def test_available_counts_the_passing_pool_by_label():
    lib = [
        _cand(1, tags=("close-up", "smile")),
        _cand(2, tags=("close-up",)),
        _cand(3, tags=("full body",), face_count_max=2),
    ]
    out = rank(lib)["stills"]
    assert out.available["framing"]["close-up"] == 2
    assert out.available["framing"]["full body"] == 0
    assert out.available["expression"] == {
        "smile": 1,
        "neutral": 1,
        "open mouth": 0,
        "closed eyes": 0,
    }


def test_targets_scale_with_the_set_and_never_drop_below_one():
    assert dp.target("framing", "close-up", 15) == 3
    assert dp.target("framing", "full body", 15) == 2
    assert dp.target("expression", "closed eyes", 15) == 1
    assert dp.target("expression", "closed eyes", 5) == 1
    assert dp.target("framing", "close-up", 30) == 6


def test_a_stack_is_set_aside_when_any_member_is():
    # A set takes a whole stack, so the clean member cannot pass alone.
    lib = [
        _cand(1, rating=5, stack_id=9),
        _cand(2, face_count_max=2, stack_id=9),
        _cand(3, rating=4),
    ]
    out = rank(lib, [(1, 2)])["stills"]
    assert _ids(out) == [3]
    assert out.excluded["other_people"] == 2
    assert out.would_add["other_people"] == 1


def test_a_lone_reference_keeps_a_likeness():
    vec = _emb(1).tobytes()
    assert dp.face_likeness([(1, vec)], [(1, vec)])[1] > 0.9


def test_variety_still_counts_when_the_best_pick_has_no_embedding():
    near, far = _emb(1), -_emb(1)
    lib = [
        _cand(1, rating=5, likeness=0.9, embedding=None),
        _cand(2, embedding=near),
        _cand(3, embedding=near),
        _cand(4, likeness=0.75, embedding=far),
    ]
    # 2 is picked second (lowest id among equals); 4, far from it, beats 3.
    assert _ids(rank(lib)["stills"])[:3] == [1, 2, 4]


def test_embeddings_of_another_size_are_skipped_not_fatal():
    lib = [
        _cand(1, rating=5, embedding=_emb(1)),
        _cand(2, embedding=np.ones(8, dtype=np.float32)),
    ]
    assert _ids(rank(lib)["stills"]) == [1, 2]


def test_a_stack_reason_reaches_across_stills_and_clips():
    # The clip drags the still in when the set is filled, and the reverse.
    lib = [
        _cand(1, rating=5, stack_id=9),
        _cand(2, is_clip=True, face_count_max=2, stack_id=9),
        _cand(3, rating=4),
    ]
    out = rank(lib)
    assert _ids(out["stills"]) == [3]
    assert out["stills"].excluded["other_people"] == 1
    assert _ids(out["clips"]) == []
