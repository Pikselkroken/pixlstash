"""Unit tests for the calibrated anomaly penalty and the smart-score wiring.

Covers the penalty math (per-tag severity, confidence/precision weighting, noisy-OR over
merge-alias duplicates, rank-decayed accumulation across distinct defects, precision floor,
human override, objective corroboration), the defect-count response curve and its
acceptance bands, the soft score floor that keeps heavily penalised pictures ordered, the
per-tag precision reader, and the CLIP-IQA term in the scorer.
"""

import numpy as np
from sqlmodel import SQLModel, Session, create_engine

import pixlstash.db_models  # noqa: F401  (register tables for create_all)
from pixlstash.db_models.tagger_run import TaggerRun
from pixlstash.services.tagger_run_service import get_latest_tag_precisions
from pixlstash.utils.quality.anomaly_penalty import (
    DEFAULT_PENALTY_CAP,
    FAMILY_SEVERITY,
    PRECISION_FLOOR,
    RANK_DECAY,
    SEVERITY_BASE,
    SEVERITY_GAIN,
    SEVERITY_WEIGHT_SPAN,
    anomaly_penalty,
)
from pixlstash.utils.quality.smart_score_utils import (
    SCORE_FLOOR_BAND,
    SmartScoreUtils,
    _load_clipiqa_prompts,
    compress_raw_score,
)

# Raw score of an "otherwise good" picture, measured as the median raw (pre-penalty)
# score of the 466 demo-vault pictures that carry no anomaly predictions at all.
# The count-response criteria below are defined relative to this base.
GOOD_BASE_RAW = 0.65
# Weight the scorer applies to the anomaly penalty (cfg["w_penalised_tag"]).
W_PENALISED_TAG = 0.50


def _final_score(base_raw, penalty):
    """Replicate the scorer's raw -> [1, 5] mapping for a single picture."""
    return float(1.0 + compress_raw_score(base_raw - W_PENALISED_TAG * penalty) * 4.0)


def _score_with_defects(tags):
    """Final [1, 5] score of an otherwise-good picture carrying ``tags`` at full confidence."""
    return _final_score(GOOD_BASE_RAW, anomaly_penalty({t: 1.0 for t in tags}))


def _unit(vec):
    vec = np.asarray(vec, dtype=np.float32)
    return vec / np.linalg.norm(vec)


# --------------------------------------------------------------------------- penalty


def test_penalty_empty_is_zero():
    assert anomaly_penalty({}) == 0.0
    assert anomaly_penalty({"not an anomaly tag": 0.9}) == 0.0


def test_penalty_monotonic_in_probability():
    lo = anomaly_penalty({"watermark": 0.3})
    hi = anomaly_penalty({"watermark": 0.9})
    assert 0.0 < lo < hi


def test_penalty_monotonic_in_precision():
    low_prec = anomaly_penalty({"watermark": 0.9}, tag_precisions={"watermark": 0.75})
    high_prec = anomaly_penalty({"watermark": 0.9}, tag_precisions={"watermark": 0.95})
    assert low_prec < high_prec


def test_precision_floor_gates_out_unreliable_tags():
    below = anomaly_penalty(
        {"noise": 0.95}, tag_precisions={"noise": PRECISION_FLOOR - 0.05}
    )
    assert below == 0.0


def test_human_verified_bypasses_precision_floor():
    # A human said the tag is present: it must penalise even if model precision is low.
    penalty = anomaly_penalty(
        {"noise": 0.95},
        tag_precisions={"noise": 0.2},
        human_tags={"noise"},
    )
    assert penalty > 0.0


def test_distinct_defects_in_a_family_accumulate_with_diminishing_returns():
    # Distinct anatomy defects must keep *adding* (defect count has to matter), but with
    # diminishing returns, bounded by the geometric RANK_DECAY series. The old noisy-OR
    # capped the family at a single tag's severity, which is what crushed the bottom of
    # the scale — three defects scored barely worse than one.
    one = anomaly_penalty({"bad anatomy": 0.9})
    three = anomaly_penalty(
        {"bad anatomy": 0.9, "malformed hand": 0.9, "malformed foot": 0.9}
    )
    assert three > one
    # Strictly more than the old family ceiling, strictly less than naive triple-counting.
    assert three > FAMILY_SEVERITY["anatomy"]
    assert three < 3.0 * one
    # Bounded by the infinite decay series.
    assert three <= one / (1.0 - RANK_DECAY) + 1e-9


def test_independent_families_add_up():
    watermark = anomaly_penalty({"watermark": 0.9})
    both = anomaly_penalty({"watermark": 0.9, "bad anatomy": 0.9})
    assert both > watermark


def test_penalty_respects_cap():
    huge = anomaly_penalty(
        {
            "bad anatomy": 1.0,
            "watermark": 1.0,
            "noise": 1.0,
            "blocky": 1.0,
            "waxy skin": 1.0,
        },
        metrics={"noise_level": 1.0, "colorfulness": 1.0, "sharpness": 0.0},
        cap=DEFAULT_PENALTY_CAP,
    )
    assert huge <= DEFAULT_PENALTY_CAP


def test_corroboration_noise_disambiguated_by_sharpness():
    # High noise_level on a sharp image is detail, not noise → weaker corroboration.
    sharp = anomaly_penalty(
        {"noise": 0.9}, metrics={"noise_level": 0.2, "sharpness": 0.95}
    )
    soft = anomaly_penalty(
        {"noise": 0.9}, metrics={"noise_level": 0.2, "sharpness": 0.05}
    )
    assert sharp < soft


def test_merge_child_is_noisy_ored_into_its_parent_not_counted_twice():
    # "extra digit" IS "malformed hand" — the same defect under two names. Noisy-OR is
    # reserved for exactly this duplicate-detection job, so the pair must stay within one
    # canonical tag's severity rather than escalating like two distinct defects would.
    hand_only = anomaly_penalty({"malformed hand": 0.9})
    child = anomaly_penalty({"extra digit": 0.9})
    assert 0.0 < child <= FAMILY_SEVERITY["anatomy"] + 1e-9
    combined = anomaly_penalty({"extra digit": 0.9, "malformed hand": 0.9})
    assert combined <= FAMILY_SEVERITY["anatomy"] + 1e-9
    # Two *distinct* defects of the same weight escalate; a duplicate barely moves.
    two_distinct = anomaly_penalty({"malformed hand": 0.9, "malformed foot": 0.9})
    assert combined < two_distinct
    assert combined < 1.3 * hand_only


def test_severity_is_per_tag_not_family_max():
    # Regression: _family_severity used to take the family's MAX member weight, so every
    # anatomy tag inherited the ceiling set by "bad anatomy" and "incorrect reflection"
    # (weight 3) was punished exactly as hard as "bad anatomy" (weight 5).
    catastrophic = anomaly_penalty({"bad anatomy": 0.9})  # weight 5
    moderate = anomaly_penalty({"malformed hand": 0.9})  # weight 4
    mild = anomaly_penalty({"incorrect reflection": 0.9})  # weight 3
    assert catastrophic > moderate > mild > 0.0


def test_family_severity_follows_the_affine_weight_map():
    def expected(weight):
        return SEVERITY_GAIN * (SEVERITY_BASE + SEVERITY_WEIGHT_SPAN * (weight / 5.0))

    assert FAMILY_SEVERITY["anatomy"] == expected(5.0)
    assert FAMILY_SEVERITY["watermark"] == expected(4.0)
    assert FAMILY_SEVERITY["anatomy"] > FAMILY_SEVERITY["noise"]


def test_zero_weight_tag_is_not_penalised():
    # "silicone breasts" is registered with weight 0 — deliberately de-penalised.
    assert anomaly_penalty({"silicone breasts": 1.0}) == 0.0


def test_penalty_super_linear_in_confidence():
    # CONF_POWER > 1: doubling confidence more than doubles the penalty, so a near-certain
    # defect is punished disproportionately harder than a borderline one.
    high = anomaly_penalty({"bad anatomy": 0.8})
    low = anomaly_penalty({"bad anatomy": 0.4})
    assert high > 2.0 * low


def test_lower_precision_punishes_less():
    # Precision is a separate axis from severity: "malformed hand" is both slightly less
    # severe (weight 4 vs 5) and harder to predict, and both effects push the same way.
    reliable = anomaly_penalty(
        {"bad anatomy": 0.9}, tag_precisions={"bad anatomy": 0.92}
    )
    flaky = anomaly_penalty(
        {"malformed hand": 0.9}, tag_precisions={"malformed hand": 0.74}
    )
    assert reliable > flaky > 0.0


def test_confident_defects_drive_a_top_picture_down_by_count():
    # A single confident "bad anatomy" costs a top-scoring picture most of the scale, but
    # it no longer *floors* it: flooring on the first defect is exactly what collapsed a
    # fifth of the library onto 1.0 and left defect count unable to move the score.
    # Stacking distinct defects is what reaches the floor.
    rng = np.random.default_rng(7)
    emb = _unit(rng.standard_normal(512))
    clean = _candidate(1, emb)
    one = _candidate(2, emb, anomaly_probs={"bad anatomy": 0.95})
    three = _candidate(
        3,
        emb,
        anomaly_probs={
            "bad anatomy": 0.95,
            "malformed hand": 0.95,
            "malformed foot": 0.95,
        },
    )
    scores = SmartScoreUtils.calculate_smart_score_batch_numpy(
        [clean, one, three], [], []
    )
    # This synthetic candidate is *exceptionally* good (raw base ~0.84 vs the
    # GOOD_BASE_RAW = 0.65 the count bands are calibrated for), so it legitimately
    # survives further down the count curve than the band tests above require.
    assert scores[0] >= 4.0  # clean stays high
    assert scores[1] <= scores[0] - 1.5  # one confident disaster costs it dearly
    assert scores[2] <= scores[1] - 0.5  # ... and three cost substantially more
    assert scores[2] < 2.0


# ------------------------------------------------- count-response acceptance criteria
#
# On the [1, 5] smart-score scale, for an otherwise-good picture (raw base GOOD_BASE_RAW):
#   1 anatomy defect  -> 1.5 - 2.2
#   2 anatomy defects -> 1.0 - 1.5
#   3+ anatomy defects-> the floor band (<= 1.0 + 4 * SCORE_FLOOR_BAND)
# These are the acceptance criteria the aggregation constants are calibrated against.

# Distinct anatomy tags ordered so any prefix of length k is a valid k-defect set,
# spanning the full registered weight range (3, 4, 4, 5).
_ANATOMY_BY_WEIGHT = {
    3: ("incorrect reflection",),
    4: ("malformed hand", "malformed teeth", "malformed foot", "malformed nipples"),
    5: ("bad anatomy", "missing nipples"),
}
_FLOOR_BAND_TOP = 1.0 + 4.0 * SCORE_FLOOR_BAND


def _stack_of(weight, k):
    """``k`` distinct anatomy tags, as far as possible all of the given weight."""
    same = list(_ANATOMY_BY_WEIGHT[weight])
    if len(same) >= k:
        return same[:k]
    others = [t for w, tags in _ANATOMY_BY_WEIGHT.items() if w != weight for t in tags]
    return same + others[: k - len(same)]


def test_one_anatomy_defect_lands_in_the_1_5_to_2_2_band():
    for weight in (3, 4, 5):
        for tag in _ANATOMY_BY_WEIGHT[weight]:
            score = _score_with_defects([tag])
            assert 1.5 <= score <= 2.2, f"{tag} (weight {weight}) scored {score:.3f}"


def test_two_anatomy_defects_land_in_the_1_0_to_1_5_band():
    for weight in (3, 4, 5):
        tags = _stack_of(weight, 2)
        score = _score_with_defects(tags)
        assert 1.0 <= score <= 1.5, f"{tags} scored {score:.3f}"


def test_three_or_more_anatomy_defects_sit_in_the_floor_band():
    for weight in (3, 4, 5):
        for k in (3, 4, 5):
            tags = _stack_of(weight, k)
            score = _score_with_defects(tags)
            assert score <= _FLOOR_BAND_TOP, (
                f"{k} defects of weight {weight} scored {score:.3f}, "
                f"above the floor band top {_FLOOR_BAND_TOP:.3f}"
            )


def test_penalty_is_strictly_monotonic_in_defect_count():
    # The core regression: the old noisy-OR saturated, so 1/2/3 defects were nearly
    # indistinguishable. Each additional distinct defect must strictly increase the
    # penalty and strictly decrease the final score.
    for weight in (3, 4, 5):
        penalties = [
            anomaly_penalty({t: 1.0 for t in _stack_of(weight, k)}) for k in range(1, 6)
        ]
        assert penalties == sorted(penalties), penalties
        assert all(b > a for a, b in zip(penalties, penalties[1:])), penalties
        scores = [_score_with_defects(_stack_of(weight, k)) for k in range(1, 6)]
        assert all(b < a for a, b in zip(scores, scores[1:])), scores


def test_defect_count_separates_meaningfully_not_marginally():
    # Under the old aggregation, going from 1 to 3 defects moved the final score by only
    # ~0.3 (the noisy-OR ran 0.900 / 0.990 / 0.999) while the first defect alone cost more
    # than a good picture's entire score. Require real separation across the counts that
    # are still on the open part of the scale. Once a picture is inside the floor band the
    # remaining separation is intentionally tiny — that is what the band is for — so
    # ordering there is asserted by test_scores_below_the_old_clamp_floor_stay_ordered.
    one, two, three = (_score_with_defects(_stack_of(4, k)) for k in (1, 2, 3))
    assert one - two >= 0.4
    assert one - three >= 0.6
    assert two > three


# ------------------------------------------------------ ordering below the old floor


def test_scores_below_the_old_clamp_floor_stay_ordered():
    # Previously np.clip(raw, 0, 1) mapped every negative raw score to exactly 1.0, so
    # a mildly bad and a catastrophic picture tied and sorting fell back to picture id.
    mild = _final_score(
        GOOD_BASE_RAW, anomaly_penalty({t: 1.0 for t in _stack_of(4, 3)})
    )
    catastrophic = _final_score(
        GOOD_BASE_RAW, anomaly_penalty({t: 1.0 for t in _stack_of(5, 6)})
    )
    assert mild > catastrophic, (mild, catastrophic)
    # Both still read as "1.0" to a user, and both honour the [1, 5] contract.
    assert 1.0 < catastrophic < mild <= _FLOOR_BAND_TOP


def test_compress_raw_score_is_monotonic_and_bounded():
    raw = np.linspace(-5.0, 2.0, 4001)
    out = compress_raw_score(raw)
    assert np.all(out >= 0.0) and np.all(out <= 1.0)
    # Strictly increasing everywhere below the top clamp.
    below_top = raw < 1.0
    assert np.all(np.diff(out[below_top]) > 0.0)
    # Continuous at the seam.
    assert compress_raw_score(0.0) == SCORE_FLOOR_BAND
    assert abs(float(compress_raw_score(-1e-12)) - SCORE_FLOOR_BAND) < 1e-9
    # Never reaches exactly 0, so no ties at the floor.
    assert float(compress_raw_score(-1000.0)) > 0.0


def test_compress_raw_score_preserves_the_output_range_contract():
    for raw in (-1e6, -1.0, 0.0, 0.5, 1.0, 1e6):
        final = 1.0 + float(compress_raw_score(raw)) * 4.0
        assert 1.0 <= final <= 5.0


# ---------------------------------------------------- no collateral drift when clean


def test_clean_pictures_do_not_drift():
    # The floor band compresses the positive range slightly; the shift must stay under
    # 4 * SCORE_FLOOR_BAND and vanish at the top of the scale.
    for raw in (0.30, 0.50, 0.65, 0.75, 0.90):
        old = 1.0 + float(np.clip(raw, 0.0, 1.0)) * 4.0
        new = 1.0 + float(compress_raw_score(raw)) * 4.0
        assert 0.0 <= new - old <= 4.0 * SCORE_FLOOR_BAND
    assert compress_raw_score(1.0) == 1.0  # top of the scale is exact


def test_unpenalised_picture_keeps_a_high_score():
    rng = np.random.default_rng(11)
    emb = _unit(rng.standard_normal(512))
    clean = _candidate(1, emb)
    (score,) = SmartScoreUtils.calculate_smart_score_batch_numpy([clean], [], [])
    assert score >= 4.0
    assert score <= 5.0


# --------------------------------------------------------------- precision reader


def _memory_session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_get_latest_tag_precisions_empty_when_no_runs():
    with _memory_session() as session:
        assert get_latest_tag_precisions(session) == {}


def test_get_latest_tag_precisions_reads_per_tag():
    with _memory_session() as session:
        session.add(
            TaggerRun(
                run="run-1",
                report={
                    "payload": {
                        "run": "run-1",
                        "per_tag": [
                            {"tag": "Watermark", "precision": 0.91, "f1": 0.8},
                            {"tag": "noise", "precision": 0.82},
                            {"tag": "missing", "f1": 0.5},  # no precision → skipped
                        ],
                    }
                },
            )
        )
        session.commit()
        precisions = get_latest_tag_precisions(session)
        assert precisions["watermark"] == 0.91
        assert precisions["noise"] == 0.82
        assert "missing" not in precisions


def test_get_latest_tag_precisions_falls_back_to_prior_run():
    with _memory_session() as session:
        # Older run has precision; newest run omits per_tag entirely.
        session.add(
            TaggerRun(
                run="run-old",
                report={"payload": {"per_tag": [{"tag": "noise", "precision": 0.77}]}},
            )
        )
        session.commit()
        session.add(TaggerRun(run="run-new", report={"payload": {}}))
        session.commit()
        precisions = get_latest_tag_precisions(session)
        assert precisions.get("noise") == 0.77


# ------------------------------------------------------------------ scorer wiring


def _candidate(pid, emb, **overrides):
    base = {
        "id": pid,
        "embedding": emb,
        "aesthetic_score": 6.0,
        "width": 2000,
        "height": 2000,
        "sharpness": 0.8,
        "edge_density": 0.1,
        "luminance_entropy": 0.7,
        "noise_level": 0.03,
        "colorfulness": 0.5,
        "text_score": 0.0,
        "anomaly_probs": {},
        "anomaly_human": frozenset(),
    }
    base.update(overrides)
    return base


def test_scorer_penalises_defects_and_stays_in_range():
    rng = np.random.default_rng(0)
    emb = _unit(rng.standard_normal(512))
    clean = _candidate(1, emb)
    defect = _candidate(2, emb, anomaly_probs={"watermark": 0.95, "bad anatomy": 0.9})
    scores = SmartScoreUtils.calculate_smart_score_batch_numpy([clean, defect], [], [])
    assert scores[1] < scores[0]
    assert np.all(scores >= 1.0) and np.all(scores <= 5.0)


def test_scorer_ignores_low_precision_tag():
    rng = np.random.default_rng(1)
    emb = _unit(rng.standard_normal(512))
    clean = _candidate(1, emb)
    flaky = _candidate(2, emb, anomaly_probs={"noise": 0.95})
    config = {"tag_precisions": {"noise": 0.4}}  # below the floor → no down-score
    scores = SmartScoreUtils.calculate_smart_score_batch_numpy(
        [clean, flaky], [], [], config=config
    )
    assert abs(float(scores[0]) - float(scores[1])) < 1e-6


def test_clipiqa_term_rewards_quality_aligned_embedding():
    good_vec, bad_vec = _load_clipiqa_prompts()
    assert good_vec is not None and good_vec.shape == (512,)
    good_like = _candidate(1, good_vec.copy())
    bad_like = _candidate(2, bad_vec.copy())
    scores = SmartScoreUtils.calculate_smart_score_batch_numpy(
        [good_like, bad_like], [], []
    )
    assert scores[0] > scores[1]
