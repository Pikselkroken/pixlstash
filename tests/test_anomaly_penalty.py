"""Unit tests for the calibrated anomaly penalty and the smart-score wiring.

Covers the penalty math (confidence/precision weighting, noisy-OR within families,
precision floor, human override, objective corroboration), the per-tag precision reader,
and the CLIP-IQA term in the scorer.
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
    SEVERITY_GAIN,
    anomaly_penalty,
)
from pixlstash.utils.quality.smart_score_utils import (
    SmartScoreUtils,
    _load_clipiqa_prompts,
)


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


def test_noisy_or_within_family_does_not_triple_count():
    # Three correlated anatomy tags must not sum past the family severity.
    three = anomaly_penalty(
        {"bad anatomy": 0.9, "malformed hand": 0.9, "malformed foot": 0.9}
    )
    one = anomaly_penalty({"bad anatomy": 0.9})
    assert one < three <= FAMILY_SEVERITY["anatomy"] + 1e-9


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
            "oversaturation": 1.0,
            "pixelated": 1.0,
            "waxy skin": 1.0,
        },
        metrics={"noise_level": 1.0, "colorfulness": 1.0, "sharpness": 0.0},
        cap=DEFAULT_PENALTY_CAP,
    )
    assert huge <= DEFAULT_PENALTY_CAP


def test_corroboration_oversaturation_tracks_colorfulness():
    low = anomaly_penalty({"oversaturation": 0.9}, metrics={"colorfulness": 0.1})
    high = anomaly_penalty({"oversaturation": 0.9}, metrics={"colorfulness": 0.95})
    assert 0.0 < low < high


def test_corroboration_noise_disambiguated_by_sharpness():
    # High noise_level on a sharp image is detail, not noise → weaker corroboration.
    sharp = anomaly_penalty(
        {"noise": 0.9}, metrics={"noise_level": 0.2, "sharpness": 0.95}
    )
    soft = anomaly_penalty(
        {"noise": 0.9}, metrics={"noise_level": 0.2, "sharpness": 0.05}
    )
    assert sharp < soft


def test_merge_child_groups_with_parent_family():
    # "extra digit" merges into the anatomy family, so it should not add on top of
    # "malformed hand" beyond the family severity.
    anatomy = FAMILY_SEVERITY["anatomy"]
    child = anomaly_penalty({"extra digit": 0.9})
    assert 0.0 < child <= anatomy + 1e-9
    combined = anomaly_penalty({"extra digit": 0.9, "malformed hand": 0.9})
    assert combined <= anatomy + 1e-9


def test_severity_derived_from_tag_weights_times_gain():
    # Severity is derived from DEFAULT_SMART_SCORE_PENALIZED_TAGS (weight/5) × gain, not
    # a second hand-maintained table. "bad anatomy" (weight 5) is the most severe.
    assert FAMILY_SEVERITY["anatomy"] == (5.0 / 5.0) * SEVERITY_GAIN
    assert FAMILY_SEVERITY["watermark"] == (4.0 / 5.0) * SEVERITY_GAIN
    assert FAMILY_SEVERITY["anatomy"] > FAMILY_SEVERITY["noise"]


def test_penalty_super_linear_in_confidence():
    # CONF_POWER > 1: doubling confidence more than doubles the penalty, so a near-certain
    # defect is punished disproportionately harder than a borderline one.
    high = anomaly_penalty({"bad anatomy": 0.8})
    low = anomaly_penalty({"bad anatomy": 0.4})
    assert high > 2.0 * low


def test_equal_severity_lower_precision_punishes_less():
    # "bad anatomy" and "malformed hand" are equally severe (same family) but a human
    # finds malformed hands harder to predict — its lower precision punishes less.
    reliable = anomaly_penalty(
        {"bad anatomy": 0.9}, tag_precisions={"bad anatomy": 0.92}
    )
    flaky = anomaly_penalty(
        {"malformed hand": 0.9}, tag_precisions={"malformed hand": 0.74}
    )
    assert reliable > flaky > 0.0


def test_confident_disaster_floors_the_score():
    # A confident "bad anatomy" must drop a good picture to the bottom of the scale.
    rng = np.random.default_rng(7)
    emb = _unit(rng.standard_normal(512))
    clean = _candidate(1, emb)
    disaster = _candidate(2, emb, anomaly_probs={"bad anatomy": 0.95})
    scores = SmartScoreUtils.calculate_smart_score_batch_numpy(
        [clean, disaster], [], []
    )
    assert scores[0] >= 4.0  # clean stays high
    assert scores[1] <= 2.0  # confident disaster is floored


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
