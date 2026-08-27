"""Unit tests for the tiered duplicate detection service.

Covers, per tier and per rule:

* **tier 1 (exact)** - groups on the indexed ``pixel_sha``, refuses to group two
  files that share a digest but differ in ``size_bytes`` (the sampled-digest
  guard), and stays blind to the scrapheap;
* **tier 2 (bucketed near)** - candidate buckets come from the precomputed
  columns, comparison happens only inside a bucket, and the group's confidence is
  its weakest link;
* **tier 3 (embedding)** - reuses the shipped likeness edge table;
* **the tier policy** - exact is always on, each looser tier requires the tier
  above it, and the 0.65 floor is a hard error rather than a silent clamp;
* **cover selection** - the 2026-07-30 lexicographic ranking: smart score in
  quarter-star buckets (unknown ranks neutral, never zero), then pixel count,
  then sharpness, then stars/tags/RAW/bytes, ties to the oldest capture;
* **evidence** - matching pills and evidence-against pills, both directions;
* **the queue** - paged by confidence descending, verdict-resolved groups never
  re-offered, and scope-narrowed counts.
"""

import gc
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta

import pytest
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, PictureSet, PictureSetMember, PictureStack
from pixlstash.db_models.dedup import (
    SCAN_PENDING,
    SCAN_RUNNING,
    VERDICT_KEEP_SEPARATE,
    DedupGroup,
    DedupScan,
    DedupVerdict,
)
from pixlstash.db_models.picture_likeness import PictureLikeness
from pixlstash.db_models.quality import Quality
from pixlstash.db_models.tag import Tag
from pixlstash.server import Server
from pixlstash.services import dedup_tier_service as tiers
from pixlstash.services import dedup_verdict_service as verdicts
from pixlstash.services.dedup_tier_service import (
    CandidateMember,
    DedupScope,
    DedupTier,
    ScopeType,
    TierPolicy,
)
from pixlstash.tasks import dedup_scan_task as dedup_scan_task_module
from pixlstash.tasks.dedup_scan_task import DedupScanTask
from pixlstash.tasks.dedup_scan_finder import DedupScanFinder
from pixlstash.tasks.task_type import TaskType
from pixlstash.task_runner import TaskRunner

_BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)

# A 64-bit dHash is 16 hex chars. These differ from ZERO by a controlled number
# of set bits, so the Hamming distance (and therefore the similarity) is exact.
PHASH_ZERO = "0000000000000000"
PHASH_ONE_BIT = "0000000000000001"  # 1 bit  -> similarity 63/64 = 0.984375
PHASH_FOUR_BITS = "000000000000000f"  # 4 bits -> similarity 60/64 = 0.9375
PHASH_FAR = "ffffffffffffffff"  # 64 bits -> similarity 0.0


@pytest.fixture
def server():
    temp_dir = tempfile.TemporaryDirectory()
    config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(config_path, "w") as fh:
        fh.write(json.dumps({"port": 8000, "disable_background_workers": True}))
    Server.DEFAULT_FORCE_CPU = True
    srv = Server(config_path)
    try:
        yield srv
    finally:
        srv.close()
        temp_dir.cleanup()
        gc.collect()


def _run(server, fn, *args):
    """Run *fn(session, \\*args)* on the DB worker and return its result."""
    return server.vault.db.run_task(fn, *args, priority=DBPriority.IMMEDIATE)


def _seed(server, specs):
    """Insert one picture per spec; return the ids in order.

    Recognised keys: ``pixel_sha``, ``perceptual_hash``, ``size_bytes``,
    ``width``, ``height``, ``score``, ``smart_score``, ``sharpness`` (writes a
    ``Quality`` row), ``format``, ``file_path``, ``created_at`` (an offset in
    seconds from ``_BASE_TIME``), ``deleted``, ``tags``,
    ``import_source_folder``, ``reference_folder_id``.
    """

    def insert(session):
        picture_ids = []
        for index, spec in enumerate(specs):
            width = spec.get("width", 4000)
            height = spec.get("height", 3000)
            created_offset = spec.get("created_at")
            pic = Picture(
                file_path=spec.get("file_path", f"/vault/pic_{index}.png"),
                format=spec.get("format", "png"),
                width=width,
                height=height,
                size_bin_index=(width << 32) + height,
                size_bytes=spec.get("size_bytes", 1000),
                score=spec.get("score"),
                smart_score=spec.get("smart_score"),
                pixel_sha=spec.get("pixel_sha"),
                perceptual_hash=spec.get("perceptual_hash"),
                import_source_folder=spec.get("import_source_folder"),
                reference_folder_id=spec.get("reference_folder_id"),
                deleted=bool(spec.get("deleted", False)),
                created_at=(
                    _BASE_TIME + timedelta(seconds=created_offset)
                    if created_offset is not None
                    else None
                ),
            )
            session.add(pic)
            session.flush()
            for tag in spec.get("tags", []):
                session.add(Tag(picture_id=int(pic.id), tag=tag))
            if "sharpness" in spec:
                session.add(
                    Quality(picture_id=int(pic.id), sharpness=spec["sharpness"])
                )
            picture_ids.append(int(pic.id))
        session.commit()
        return picture_ids

    return _run(server, insert)


def _member(**kwargs) -> CandidateMember:
    """A bare :class:`CandidateMember` for the pure-function tests."""
    defaults = {
        "id": 1,
        "width": 4000,
        "height": 3000,
        "format": "jpeg",
        "score": 0,
        "tag_count": 0,
    }
    defaults.update(kwargs)
    return CandidateMember(**defaults)


# ── the tier policy ───────────────────────────────────────────────────────────


def test_exact_tier_is_always_on_and_cannot_be_switched_off():
    assert TierPolicy().tiers == (DedupTier.EXACT,)
    assert TierPolicy().includes(DedupTier.EXACT)
    assert not TierPolicy().includes(DedupTier.NEAR)


def test_each_looser_tier_requires_the_tier_above_it():
    assert TierPolicy(near_enabled=True).tiers == (DedupTier.EXACT, DedupTier.NEAR)
    assert TierPolicy(near_enabled=True, embedding_enabled=True).tiers == (
        DedupTier.EXACT,
        DedupTier.NEAR,
        DedupTier.EMBEDDING,
    )
    with pytest.raises(ValueError, match="requires near_enabled"):
        TierPolicy(embedding_enabled=True)


def test_threshold_default_is_090_and_the_floor_is_a_hard_error():
    assert TierPolicy().threshold == pytest.approx(0.90)
    # Below the floor is a 400-worthy error, never a silent clamp: a low
    # threshold produces confident-looking garbage and destroys the count.
    with pytest.raises(ValueError, match="0.65"):
        TierPolicy(threshold=0.5)
    assert TierPolicy(threshold=tiers.MIN_THRESHOLD).threshold == pytest.approx(0.65)


def test_group_size_bounds_are_validated():
    with pytest.raises(ValueError, match="min_group_size"):
        TierPolicy(min_group_size=1)
    with pytest.raises(ValueError, match="max_group_size"):
        TierPolicy(min_group_size=4, max_group_size=3)


# ── cover selection (the 2026-07-30 lexicographic ranking) ────────────────────


def test_smart_score_dominates_every_size_advantage():
    """Tier 1 beats tier 2: a 40 MP blurry scan must not outrank a sharp
    original the scorer rated higher - the exact failure of the old weighted
    sum, where pixels alone could buy the cover."""
    sharp_original = _member(id=1, width=4000, height=3000, smart_score=4.5)
    blurry_scan = _member(
        id=2, width=8000, height=5000, smart_score=2.0, tag_count=9, score=5
    )
    assert tiers.select_cover([blurry_scan, sharp_original]) == 1


def test_smart_scores_inside_one_bucket_fall_through_to_size():
    """A lead smaller than the 0.25 bucket is scoring noise, not a decision:
    both land in the same bucket and the bigger picture wins."""
    slightly_better = _member(id=1, width=1000, height=1000, smart_score=4.30)
    bigger = _member(id=2, width=4000, height=3000, smart_score=4.26)
    assert tiers.select_cover([slightly_better, bigger]) == 2
    # A genuine bucket lead decides, regardless of size.
    clearly_better = _member(id=3, width=1000, height=1000, smart_score=4.55)
    assert tiers.select_cover([bigger, clearly_better]) == 3


def test_an_unknown_smart_score_ranks_neutral_never_zero():
    """NULL (not yet computed) and -1.0 (the failed-metric sentinel) both read
    as unknown and rank at the neutral midpoint: an unscored copy still loses
    to a known-good one, still beats a known-bad one, and two unknowns fall
    through to size - never buried below a scored-terrible sibling."""
    unknown = _member(id=1, width=1000, height=1000)
    known_bad = _member(id=2, width=8000, height=8000, smart_score=2.0)
    known_good = _member(id=3, width=1000, height=1000, smart_score=4.0)
    assert tiers.select_cover([known_bad, unknown]) == 1
    assert tiers.select_cover([unknown, known_good]) == 3
    failed = _member(id=4, width=4000, height=3000, smart_score=-1.0)
    # Failed (-1.0) is neutral too: it ties the NULL member and wins on size.
    assert tiers.select_cover([unknown, failed]) == 4


def test_size_beats_sharpness_at_equal_smart_bucket():
    bigger_softer = _member(
        id=1, width=4000, height=3000, smart_score=4.0, sharpness=0.10
    )
    smaller_sharper = _member(
        id=2, width=2000, height=1500, smart_score=4.0, sharpness=0.45
    )
    assert tiers.select_cover([smaller_sharper, bigger_softer]) == 1


def test_sharpness_decides_at_equal_smart_score_and_pixels():
    soft = _member(id=1, sharpness=0.15, score=5, tag_count=9)
    sharp = _member(id=2, sharpness=0.40)
    assert tiers.select_cover([soft, sharp]) == 2
    # Unknown sharpness (missing row or the -1.0 sentinel) is neutral (0.25):
    # it loses to a known-sharper copy and beats a known-softer one.
    unknown = _member(id=3)
    failed = _member(id=4, sharpness=-1.0)
    assert tiers.select_cover([unknown, sharp]) == 2
    assert tiers.select_cover([soft, failed]) == 4


def test_lower_order_signals_break_full_quality_ties():
    """Stars, then tags, then RAW, then bytes - in that order, only after the
    quality and size tiers tie."""
    starred = _member(id=1, score=4)
    tagged = _member(id=2, tag_count=7)
    assert tiers.select_cover([tagged, starred]) == 1
    raw = _member(id=3, format="arw")
    assert tiers.select_cover([tagged, raw]) == 2  # tags outrank RAW
    heavy = _member(id=4, size_bytes=9_000_000)
    light = _member(id=5, size_bytes=1_000)
    assert tiers.select_cover([light, heavy]) == 4


def test_ties_break_to_the_oldest_capture_then_the_lowest_id():
    old = _member(id=10, created_at=_BASE_TIME)
    new = _member(id=11, created_at=_BASE_TIME + timedelta(hours=5))
    assert tiers.select_cover([new, old]) == 10
    # No timestamps at all: deterministic on the lowest id.
    a = _member(id=21)
    b = _member(id=22)
    assert tiers.select_cover([b, a]) == 21


def test_raw_is_detected_by_format_or_extension():
    assert _member(format="ARW").is_raw
    assert _member(format="jpeg", file_path="/shoots/A7R0912.arw").is_raw
    assert not _member(format="jpeg", file_path="/shoots/x.jpg").is_raw


def test_the_legacy_cover_score_field_is_unchanged():
    """`cover_score` is deprecated wire-compat, not the selection rule - but
    while it ships it must keep its documented value."""
    member = _member(width=4000, height=3000, tag_count=2, score=3)
    assert member.megapixels == pytest.approx(12.0)
    assert member.cover_score == pytest.approx(48.0 + 6.0 + 6.0)
    raw = _member(id=1, format="arw", width=1000, height=1000)
    jpeg = _member(id=2, format="jpeg", width=1000, height=1000)
    assert raw.cover_score - jpeg.cover_score == pytest.approx(tiers.COVER_RAW_BONUS)


def test_serialization_carries_the_ranking_signals_null_safe():
    scored = _member(smart_score=4.256, sharpness=0.312)
    assert scored.as_dict()["smart_score"] == pytest.approx(4.256)
    assert scored.as_dict()["sharpness"] == pytest.approx(0.312)
    # NULL and the -1.0 failed sentinel both serialize as null, never a fake
    # number the Compare view would display as real.
    blank = _member()
    assert blank.as_dict()["smart_score"] is None
    assert blank.as_dict()["sharpness"] is None
    failed = _member(smart_score=-1.0, sharpness=-1.0)
    assert failed.as_dict()["smart_score"] is None
    assert failed.as_dict()["sharpness"] is None


# ── signature ─────────────────────────────────────────────────────────────────


def test_signature_is_order_independent_and_content_derived():
    assert tiers.group_signature(["b", "a"]) == tiers.group_signature(["a", "b"])
    assert tiers.group_signature(["a", "b"]) != tiers.group_signature(["a", "c"])


def test_signature_falls_back_to_the_picture_id_when_no_hash_exists():
    hashed = _member(id=7, pixel_sha="deadbeef", size_bytes=100)
    unhashed = _member(id=7)
    # The hash is never the identity on its own: it is sampled above 128 KiB, so
    # the size travels with it (see test_content_key_carries_the_size_co_key).
    assert hashed.content_key == "deadbeef:100"
    assert unhashed.content_key == "id:7"


# ── evidence ──────────────────────────────────────────────────────────────────


def test_group_evidence_reports_both_directions():
    members = [
        _member(id=1, width=6000, height=4000, created_at=_BASE_TIME, format="jpeg"),
        _member(id=2, width=1920, height=1440, created_at=_BASE_TIME, format="webp"),
    ]
    pills = tiers.build_group_evidence(DedupTier.NEAR, 0.96, members)
    texts = {pill["text"]: pill["against"] for pill in pills}
    assert "96% visual match" not in texts
    assert texts["Different resolution"] is True
    assert texts["Different aspect ratio"] is True
    assert texts["Different file format"] is True
    assert texts["Same capture second"] is False


def test_exact_evidence_leads_with_the_hash_and_same_dimensions():
    members = [_member(id=1), _member(id=2)]
    pills = tiers.build_group_evidence(DedupTier.EXACT, 1.0, members)
    texts = [pill["text"] for pill in pills]
    assert texts[0] == "Identical file hash"
    assert "Same dimensions" in texts
    assert not any(pill["against"] for pill in pills)


def test_candidate_evidence_explains_the_preselection_both_ways():
    best = _member(id=1, width=6000, height=4000, tag_count=6, score=5)
    worst = _member(id=2, width=1080, height=1080)
    members = [best, worst]
    best_pills = tiers.build_candidate_evidence(best, members, cover_id=1)
    worst_pills = tiers.build_candidate_evidence(worst, members, cover_id=1)
    assert any(p["text"] == "Highest resolution" for p in best_pills)
    assert any(p["text"] == "Preselected as cover" for p in best_pills)
    assert any(p["against"] and "fewer pixels" in p["text"] for p in worst_pills)
    assert any(p["against"] and "Fewer tags" in p["text"] for p in worst_pills)
    # Nobody here has a smart score or sharpness: no pill invents one.
    for pills in (best_pills, worst_pills):
        assert not any("smart score" in p["text"].lower() for p in pills)
        assert not any("Sharpest" in p["text"] for p in pills)


def test_candidate_evidence_explains_the_smart_score_and_sharpness_tiers():
    """The pills must explain the NEW ranking, in its priority order."""
    best = _member(id=1, smart_score=4.3, sharpness=0.4)
    mid = _member(id=2, smart_score=4.26, sharpness=0.1)
    worse = _member(id=3, smart_score=3.1)
    unknown = _member(id=4)
    members = [best, mid, worse, unknown]

    best_pills = tiers.build_candidate_evidence(best, members, cover_id=1)
    assert best_pills[0]["text"] == "Best smart score (4.3)"
    assert best_pills[0]["against"] is False
    assert any(p["text"] == "Sharpest copy" for p in best_pills)

    # 4.26 sits in the same quarter-star bucket as 4.3: an effective tie both
    # read as best - the pill mirrors the decision unit, not float noise.
    mid_pills = tiers.build_candidate_evidence(mid, members, cover_id=1)
    assert mid_pills[0]["text"] == "Best smart score (4.3)"
    assert not any(p["text"] == "Sharpest copy" for p in mid_pills)

    worse_pills = tiers.build_candidate_evidence(worse, members, cover_id=1)
    assert worse_pills[0]["text"] == "Lower smart score (3.1 vs 4.3)"
    assert worse_pills[0]["against"] is True

    # An unknown score gets no pill either way: the null field is the honest
    # display, and a red pill would blame the picture for a pending task.
    unknown_pills = tiers.build_candidate_evidence(unknown, members, cover_id=1)
    assert not any("smart score" in p["text"].lower() for p in unknown_pills)


def test_reference_folder_pictures_expose_their_path_and_others_do_not():
    managed = _member(id=1, file_path="/vault/a.png")
    referenced = _member(id=2, file_path="/photos/a.png", reference_folder_id=3)
    assert managed.as_dict()["file_path"] is None
    assert referenced.as_dict()["file_path"] == "/photos/a.png"


# ── tier 1: exact ─────────────────────────────────────────────────────────────


def test_exact_tier_groups_on_the_indexed_hash(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100, "score": 4},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "bbb", "size_bytes": 100},
        ],
    )
    groups = _run(server, tiers.find_exact_groups_in_session, None)
    assert len(groups) == 1
    group = groups[0]
    assert sorted(group.picture_ids) == sorted(ids[:2])
    assert group.confidence == pytest.approx(1.0)
    assert group.tier is DedupTier.EXACT
    # Equal quality/size tiers: the star tier picks the higher human score.
    assert group.cover_picture_id == ids[0]


def test_exact_tier_refuses_to_group_on_a_digest_alone(server):
    """The sampled-digest guard: same hash, different size is not a match.

    ``pixel_sha`` samples large files rather than hashing every byte, so equal
    file size is a required co-key. Dropping it would let the queue claim an
    identity the digest does not actually prove.
    """
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 999},
        ],
    )
    assert _run(server, tiers.find_exact_groups_in_session, None) == []


def test_exact_tier_ignores_the_scrapheap_and_unhashed_rows(server):
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100, "deleted": True},
            {"pixel_sha": None, "size_bytes": 100},
            {"pixel_sha": None, "size_bytes": 100},
        ],
    )
    assert _run(server, tiers.find_exact_groups_in_session, None) == []


def test_exact_tier_narrows_to_a_scope(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "ccc", "size_bytes": 100},
            {"pixel_sha": "ccc", "size_bytes": 100},
        ],
    )

    def add_set(session):
        picture_set = PictureSet(name="Scope")
        session.add(picture_set)
        session.commit()
        session.refresh(picture_set)
        for picture_id in ids[:2]:
            session.add(
                PictureSetMember(set_id=int(picture_set.id), picture_id=picture_id)
            )
        session.commit()
        return int(picture_set.id)

    set_id = _run(server, add_set)
    scope = DedupScope(scope_type=ScopeType.SET, scope_id=str(set_id))
    scoped = _run(server, tiers.find_exact_groups_in_session, scope)
    assert len(scoped) == 1
    assert sorted(scoped[0].picture_ids) == sorted(ids[:2])
    assert len(_run(server, tiers.find_exact_groups_in_session, None)) == 2


# ── tier 2: bucketed near ─────────────────────────────────────────────────────


def test_buckets_come_from_the_precomputed_columns(server):
    _seed(
        server,
        [
            {"perceptual_hash": PHASH_ZERO, "width": 100, "height": 100},
            {"perceptual_hash": PHASH_ONE_BIT, "width": 100, "height": 100},
            {"perceptual_hash": PHASH_FAR, "width": 200, "height": 200},
        ],
    )
    buckets = _run(server, tiers.build_near_buckets, None)
    kinds = {bucket.kind for bucket in buckets}
    assert "size_bin" in kinds
    # The 200x200 picture is alone in its size bin, so that bucket is dropped:
    # a singleton bucket is not work and must not inflate the progress total.
    size_bins = [b for b in buckets if b.kind == "size_bin"]
    assert len(size_bins) == 1
    assert len(size_bins[0].picture_ids) == 2


def test_near_pairs_are_only_compared_inside_a_bucket(server):
    """A picture that shares no bucket is never compared, however similar it is.

    The third picture has a byte-identical perceptual hash but different
    dimensions *and* a different folder, so it shares no candidate bucket with
    the pair. Library-wide comparison would have pulled it in; bucketed
    comparison does not.
    """
    ids = _seed(
        server,
        [
            # Same dimensions and folder -> same bucket, 1 bit apart.
            {
                "perceptual_hash": PHASH_ZERO,
                "width": 100,
                "height": 100,
                "file_path": "/vault/a/one.png",
            },
            {
                "perceptual_hash": PHASH_ONE_BIT,
                "width": 100,
                "height": 100,
                "file_path": "/vault/a/two.png",
            },
            {
                "perceptual_hash": PHASH_ZERO,
                "width": 300,
                "height": 300,
                "file_path": "/vault/b/three.png",
            },
        ],
    )
    groups = _run(
        server, tiers.find_near_groups_in_session, TierPolicy(near_enabled=True), None
    )
    assert len(groups) == 1
    assert sorted(groups[0].picture_ids) == sorted(ids[:2])
    assert groups[0].confidence == pytest.approx(63 / 64)
    assert groups[0].tier is DedupTier.NEAR


def test_capture_minute_buckets_catch_a_resize(server):
    """Different dimensions, same capture minute: still one bucket, still found."""
    ids = _seed(
        server,
        [
            {
                "perceptual_hash": PHASH_ZERO,
                "width": 4000,
                "height": 3000,
                "created_at": 0,
            },
            {
                "perceptual_hash": PHASH_ONE_BIT,
                "width": 2000,
                "height": 1500,
                "created_at": 5,
            },
        ],
    )
    groups = _run(
        server, tiers.find_near_groups_in_session, TierPolicy(near_enabled=True), None
    )
    assert len(groups) == 1
    assert sorted(groups[0].picture_ids) == sorted(ids)
    assert any(
        pill["against"] and pill["text"] == "Different resolution"
        for pill in groups[0].evidence
    )


def test_the_threshold_excludes_a_weaker_near_pair(server):
    _seed(
        server,
        [
            {"perceptual_hash": PHASH_ZERO, "width": 100, "height": 100},
            {"perceptual_hash": PHASH_FOUR_BITS, "width": 100, "height": 100},
        ],
    )
    # 4 bits apart is 0.9375: above the 0.90 default, below a 0.99 threshold.
    loose = _run(
        server, tiers.find_near_groups_in_session, TierPolicy(near_enabled=True), None
    )
    assert len(loose) == 1
    tight = _run(
        server,
        tiers.find_near_groups_in_session,
        TierPolicy(near_enabled=True, threshold=0.99),
        None,
    )
    assert tight == []


def test_a_group_is_judged_by_its_weakest_link(server):
    """A~B at 1 bit and B~C at 4 bits is one group whose confidence is the 4-bit edge."""
    ids = _seed(
        server,
        [
            {"perceptual_hash": PHASH_ZERO, "width": 100, "height": 100},
            {"perceptual_hash": PHASH_ONE_BIT, "width": 100, "height": 100},
            {"perceptual_hash": PHASH_FOUR_BITS, "width": 100, "height": 100},
        ],
    )
    groups = _run(
        server, tiers.find_near_groups_in_session, TierPolicy(near_enabled=True), None
    )
    assert len(groups) == 1
    assert sorted(groups[0].picture_ids) == sorted(ids)
    assert groups[0].confidence == pytest.approx(60 / 64)


def test_unparseable_perceptual_hashes_are_excluded_not_crashed(server):
    _seed(
        server,
        [
            {"perceptual_hash": PHASH_ZERO, "width": 100, "height": 100},
            {"perceptual_hash": "not-a-hash-xx", "width": 100, "height": 100},
            {"perceptual_hash": "abc", "width": 100, "height": 100},
        ],
    )
    assert (
        _run(
            server,
            tiers.find_near_groups_in_session,
            TierPolicy(near_enabled=True),
            None,
        )
        == []
    )


# ── tier 3: embedding ─────────────────────────────────────────────────────────


def test_embedding_tier_reuses_the_shipped_likeness_table(server):
    ids = _seed(server, [{}, {}, {}])

    def link(session):
        first, second = PictureLikeness.canon_pair(ids[0], ids[1])
        session.add(
            PictureLikeness(
                picture_id_a=first, picture_id_b=second, likeness=0.97, metric="test"
            )
        )
        session.commit()

    _run(server, link)
    policy = TierPolicy(near_enabled=True, embedding_enabled=True)
    groups = _run(server, tiers.find_embedding_groups_in_session, policy, None)
    assert len(groups) == 1
    assert sorted(groups[0].picture_ids) == sorted(ids[:2])
    assert groups[0].tier is DedupTier.EMBEDDING
    assert groups[0].confidence == pytest.approx(0.97)


# ── persistence, the queue and the counts ─────────────────────────────────────


def _scan(server, policy=None, scope=None):
    return _run(server, tiers.run_scan_now_in_session, policy or TierPolicy(), scope)


def test_a_rescan_refreshes_groups_instead_of_duplicating_them(server):
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    assert _scan(server)["unresolved_groups"] == 1
    _scan(server)
    rows = _run(server, lambda session: session.exec(select(DedupGroup)).all())
    assert len(rows) == 1


def test_the_queue_pages_by_confidence_descending(server):
    _seed(
        server,
        [
            # An exact pair (confidence 1.0). Separate folders so it does not
            # also land in a near bucket with the pairs below.
            {
                "pixel_sha": "aaa",
                "size_bytes": 100,
                "width": 10,
                "height": 10,
                "file_path": "/vault/x/1.png",
            },
            {
                "pixel_sha": "aaa",
                "size_bytes": 100,
                "width": 10,
                "height": 10,
                "file_path": "/vault/x/2.png",
            },
            # A 1-bit near pair (0.984).
            {
                "perceptual_hash": PHASH_ZERO,
                "width": 20,
                "height": 20,
                "file_path": "/vault/y/1.png",
            },
            {
                "perceptual_hash": PHASH_ONE_BIT,
                "width": 20,
                "height": 20,
                "file_path": "/vault/y/2.png",
            },
            # A 4-bit near pair (0.9375), in its own folder so it stays its own
            # group rather than chaining onto the pair above.
            {
                "perceptual_hash": PHASH_ZERO,
                "width": 30,
                "height": 30,
                "file_path": "/vault/z/1.png",
            },
            {
                "perceptual_hash": PHASH_FOUR_BITS,
                "width": 30,
                "height": 30,
                "file_path": "/vault/z/2.png",
            },
        ],
    )
    policy = TierPolicy(near_enabled=True)
    _scan(server, policy)
    page, total, _cursor = _run(server, tiers.page_queue_in_session, policy, None, 0, 2)
    assert total == 3
    assert [round(group["confidence"], 4) for group in page] == [
        1.0,
        round(63 / 64, 4),
    ]
    assert page[0]["tier"] == "exact"
    second, _total, _cursor = _run(
        server, tiers.page_queue_in_session, policy, None, 2, 2
    )
    assert len(second) == 1
    assert second[0]["confidence"] == pytest.approx(60 / 64)


def test_the_queue_carries_the_cover_and_both_evidence_layers(server):
    ids = _seed(
        server,
        [
            {
                "pixel_sha": "aaa",
                "size_bytes": 100,
                "score": 5,
                "tags": ["portrait", "outdoor"],
            },
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    page, _total, _cursor = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    group = page[0]
    assert group["cover_picture_id"] == ids[0]
    assert any(pill["text"] == "Identical file hash" for pill in group["why"])
    cover = next(c for c in group["candidates"] if c["picture_id"] == ids[0])
    assert cover["tag_count"] == 2
    assert any(p["text"] == "Preselected as cover" for p in cover["why"])
    assert any(p["text"].startswith("Most metadata") for p in cover["why"])


def test_the_scan_ranks_the_cover_on_smart_score_end_to_end(server):
    """The stored smart score drives the baked cover and member order, and the
    queue serves the ranking signals null-safe (the -1.0 failed sentinel and a
    missing Quality row both read as null on the wire)."""
    ids = _seed(
        server,
        [
            # Bigger, heavily tagged and starred - but the scorer rates it low.
            {
                "pixel_sha": "aaa",
                "size_bytes": 100,
                "width": 8000,
                "height": 6000,
                "score": 5,
                "smart_score": 2.0,
                "sharpness": -1.0,
                "tags": ["portrait", "outdoor"],
            },
            # Smaller but rated clearly better: the cover under the new rule.
            {
                "pixel_sha": "aaa",
                "size_bytes": 100,
                "width": 4000,
                "height": 3000,
                "smart_score": 4.5,
                "sharpness": 0.4,
            },
        ],
    )
    _scan(server)
    page, _total, _cursor = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    group = page[0]
    assert group["cover_picture_id"] == ids[1]
    # Members serialize in ranking order, cover first.
    assert [c["picture_id"] for c in group["candidates"]] == [ids[1], ids[0]]
    cover, other = group["candidates"]
    assert cover["smart_score"] == pytest.approx(4.5)
    assert cover["sharpness"] == pytest.approx(0.4)
    assert any(p["text"] == "Best smart score (4.5)" for p in cover["why"])
    assert any(p["text"] == "Sharpest copy" for p in cover["why"])
    assert any(p["text"] == "Preselected as cover" for p in cover["why"])
    assert other["smart_score"] == pytest.approx(2.0)
    assert other["sharpness"] is None, "-1.0 failed sentinel must serve null"
    assert any(
        p["against"] and p["text"] == "Lower smart score (2.0 vs 4.5)"
        for p in other["why"]
    )


def test_the_near_tier_is_hidden_until_it_is_switched_on(server):
    _seed(
        server,
        [
            {"perceptual_hash": PHASH_ZERO, "width": 100, "height": 100},
            {"perceptual_hash": PHASH_ONE_BIT, "width": 100, "height": 100},
        ],
    )
    near_policy = TierPolicy(near_enabled=True)
    _scan(server, near_policy)
    # Detected and stored, but the default policy shows only tier 1.
    assert _run(server, tiers.count_unresolved_in_session, TierPolicy(), None) == 0
    assert _run(server, tiers.count_unresolved_in_session, near_policy, None) == 1
    # The per-tier counts report the switched-off tier anyway, so the user can
    # see what enabling it would add.
    by_tier = _run(server, tiers.count_by_tier_in_session, TierPolicy(), None)
    assert by_tier["near"] == 1
    assert by_tier["exact"] == 0


def test_a_recorded_verdict_resolves_the_group_on_the_next_scan(server):
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    page, _total, _cursor = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    signature = page[0]["signature"]

    def record(session):
        session.add(
            DedupVerdict(
                signature=signature,
                verdict=VERDICT_KEEP_SEPARATE,
                picture_ids="[]",
                excluded_picture_ids="[]",
            )
        )
        session.commit()

    _run(server, record)
    # A rescan re-derives the same signature and never re-asks.
    _scan(server)
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0
    page, total, _cursor = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    assert page == [] and total == 0


def test_a_reopened_verdict_puts_the_group_back_in_the_queue(server):
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    page, _total, _cursor = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    signature = page[0]["signature"]

    def record_and_reopen(session):
        session.add(
            DedupVerdict(
                signature=signature,
                verdict=VERDICT_KEEP_SEPARATE,
                picture_ids="[]",
                excluded_picture_ids="[]",
                reopened_at=_BASE_TIME,
            )
        )
        session.commit()

    _run(server, record_and_reopen)
    _scan(server)
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1


def test_stale_groups_are_pruned_when_their_members_go_to_the_scrapheap(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1

    def soft_delete(session):
        pic = session.get(Picture, ids[1])
        pic.deleted = True
        session.add(pic)
        session.commit()

    _run(server, soft_delete)
    assert _run(server, tiers.prune_stale_groups_in_session) == 1
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0


def _soft_delete(server, picture_id):
    """Move one picture to the scrapheap, the way ``DELETE /pictures/{id}`` does."""

    def go(session):
        picture = session.get(Picture, picture_id)
        picture.deleted = True
        session.add(picture)
        session.commit()

    _run(server, go)


def test_a_group_thinned_below_two_leaves_the_queue_at_once(server):
    """Not "on the next verdict or scan": immediately, with no prune in between.

    The owner's report: scrapheap one of a pair and the queue kept the row,
    drawn as a lone picture beside an empty slot.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    page, total, _cursor = _run(server, tiers.page_queue_in_session)
    assert len(page) == 1 and total == 1

    _soft_delete(server, ids[1])

    page, total, _cursor = _run(server, tiers.page_queue_in_session)
    assert page == []
    assert total == 0
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0
    assert _run(server, tiers.count_by_tier_in_session, None, None)["exact"] == 0
    # The row itself is still there, unpruned: it is the READ that filters, so
    # restoring the picture must bring the group straight back.
    assert (
        len(_run(server, lambda session: session.exec(select(DedupGroup)).all())) == 1
    )


def test_a_restored_member_brings_its_group_back_without_a_rescan(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    signature = _run(server, tiers.page_queue_in_session)[0][0]["signature"]
    _soft_delete(server, ids[1])
    assert _run(server, tiers.page_queue_in_session)[0] == []

    def restore(session):
        picture = session.get(Picture, ids[1])
        picture.deleted = False
        session.add(picture)
        session.commit()

    _run(server, restore)
    page, total, _cursor = _run(server, tiers.page_queue_in_session)
    assert [group["signature"] for group in page] == [signature]
    assert total == 1
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1


def test_a_group_with_two_live_members_left_keeps_its_place_in_the_queue(server):
    """Over-filtering is its own regression: three minus one is still a decision.

    The row stays, and every number on it counts the LIVE members only, the
    stored ``member_count`` still says three until the next prune.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "bbb", "size_bytes": 100},
            {"pixel_sha": "bbb", "size_bytes": 100},
            {"pixel_sha": "bbb", "size_bytes": 100},
        ],
    )
    _scan(server)
    _soft_delete(server, ids[2])

    page, total, _cursor = _run(server, tiers.page_queue_in_session)
    assert len(page) == 1
    assert total == 1
    group = page[0]
    assert group["member_count"] == 2
    assert sorted(c["picture_id"] for c in group["candidates"]) == sorted(ids[:2])
    assert group["cover_picture_id"] in ids[:2]
    stored = _run(
        server,
        lambda session: session.exec(select(DedupGroup)).first().member_count,
    )
    assert stored == 3, "the payload's count is live even while the stored one lags"


def test_large_group_evidence_tracks_the_live_member_count(server):
    """The warning and header must never report two different group sizes.

    The evidence is stored at scan time, but the queue filters scrapheaped
    members live. A group that shrinks back under the policy limit must lose
    its stale size warning rather than saying both "2 pictures" and "3
    pictures" in the same row.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "large", "size_bytes": 100},
            {"pixel_sha": "large", "size_bytes": 100},
            {"pixel_sha": "large", "size_bytes": 100},
        ],
    )
    policy = TierPolicy(max_group_size=2)
    _scan(server, policy)

    page, _total, _cursor = _run(server, tiers.page_queue_in_session, policy)
    assert page[0]["member_count"] == 3
    assert any("(3 pictures)" in pill["text"] for pill in page[0]["why"])

    _soft_delete(server, ids[2])
    page, _total, _cursor = _run(server, tiers.page_queue_in_session, policy)
    assert page[0]["member_count"] == 2
    assert not any(
        pill["text"].startswith("Unusually large group") for pill in page[0]["why"]
    )


def test_a_thinned_group_still_lists_on_the_decided_page(server):
    """The way back must survive the scrapheap.

    The open queue drops a group that no longer poses a decision, but the
    decided page keeps it: the verdict already happened and "clear this
    decision" is the only route back to it.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "ccc", "size_bytes": 100},
            {"pixel_sha": "ccc", "size_bytes": 100},
        ],
    )
    _scan(server)
    signature = _run(server, tiers.page_queue_in_session)[0][0]["signature"]
    _run(server, verdicts.apply_keep_separate_in_session, signature, None)
    _soft_delete(server, ids[1])

    page, total, _cursor = _run(
        server, tiers.page_queue_in_session, None, None, 0, 20, None, True
    )
    assert [group["signature"] for group in page] == [signature]
    assert total == 1
    assert page[0]["member_count"] == 1, "live members only, even here"


def test_a_decks_depth_in_the_queue_follows_a_scrapheaped_stack_member(server):
    """A deleted member must not leave a hole in the deck's depth.

    The deck's depth is the STACK's live member count, so scrapheaping a member
    the group never even names still has to shrink it, otherwise the row
    promises to move a picture that is in the Scrapheap.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "stack-leader", "size_bytes": 10},
            {"pixel_sha": "stack-b", "size_bytes": 11},
            {"pixel_sha": "shared", "size_bytes": 100},
            {"pixel_sha": "shared", "size_bytes": 100},
        ],
    )
    stack_id = _stack(server, ids[:3])
    group = _only_group(server)
    assert group["stacks"][str(stack_id)]["member_count"] == 3

    # A sibling the group never names: it is not a candidate, but it IS depth.
    _soft_delete(server, ids[1])
    page, _total, _cursor = _run(server, tiers.page_queue_in_session)
    assert len(page) == 1
    deck = page[0]["stacks"][str(stack_id)]
    assert deck["member_count"] == 2
    assert deck["leader_picture_id"] == ids[0]
    assert deck["matched_picture_ids"] == [ids[2]]


def test_a_deck_whose_leader_is_scrapheaped_reports_the_next_live_leader(server):
    """The face has to move: a deck drawn from a scrapheaped leader is the
    404-thumbnail the queue's empty placeholder was made of."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "stack-leader", "size_bytes": 10},
            {"pixel_sha": "stack-b", "size_bytes": 11},
            {"pixel_sha": "shared", "size_bytes": 100},
            {"pixel_sha": "shared", "size_bytes": 100},
        ],
    )
    stack_id = _stack(server, ids[:3])
    assert _only_group(server)["stacks"][str(stack_id)]["leader_picture_id"] == ids[0]

    _soft_delete(server, ids[0])
    page, _total, _cursor = _run(server, tiers.page_queue_in_session)
    deck = page[0]["stacks"][str(stack_id)]
    assert deck["member_count"] == 2
    assert deck["leader_picture_id"] == ids[1]


def test_scoped_counts_are_reported_per_scope(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "ccc", "size_bytes": 100},
            {"pixel_sha": "ccc", "size_bytes": 100},
        ],
    )
    _scan(server)

    def add_set(session):
        picture_set = PictureSet(name="Scope")
        session.add(picture_set)
        session.commit()
        session.refresh(picture_set)
        for picture_id in ids[:2]:
            session.add(
                PictureSetMember(set_id=int(picture_set.id), picture_id=picture_id)
            )
        session.commit()
        return int(picture_set.id)

    set_id = _run(server, add_set)
    scopes = [
        DedupScope(),
        DedupScope(scope_type=ScopeType.SET, scope_id=str(set_id)),
    ]
    counts = _run(server, tiers.scope_counts_in_session, scopes, None)
    assert counts[0]["key"] == "global"
    assert counts[0]["unresolved_groups"] == 2
    assert counts[1]["key"] == f"set:{set_id}"
    assert counts[1]["unresolved_groups"] == 1


def test_a_scope_id_is_required_for_a_non_global_scope():
    with pytest.raises(ValueError, match="scope_id is required"):
        DedupScope(scope_type=ScopeType.PROJECT)
    assert DedupScope(scope_type=ScopeType.GLOBAL, scope_id="ignored").key == "global"


# ── scan progress ─────────────────────────────────────────────────────────────


def test_an_equivalent_active_scan_request_is_coalesced_without_restarting(server):
    first = _run(server, tiers.request_scan_in_session, TierPolicy(), None)
    assert first["status"] == "pending"
    assert first["scope_key"] == "global"
    second = _run(server, tiers.request_scan_in_session, TierPolicy(), None)
    assert second["scan_id"] == first["scan_id"]
    assert second["started_at"] == first["started_at"]
    assert second["updated_at"] == first["updated_at"]


def test_a_different_policy_cannot_overwrite_an_active_scan(server):
    first = _run(server, tiers.request_scan_in_session, TierPolicy(), None)
    with pytest.raises(tiers.DedupScanBusyError) as raised:
        _run(
            server,
            tiers.request_scan_in_session,
            TierPolicy(near_enabled=True),
            None,
        )
    assert raised.value.active_scan["scan_id"] == first["scan_id"]
    assert raised.value.active_scan["tiers"] == ["exact"]
    persisted = _run(server, tiers.scan_progress_in_session, None)
    assert persisted["tiers"] == ["exact"]
    assert persisted["status"] == "pending"


def test_scan_slices_yield_the_writer_between_near_buckets(server, monkeypatch):
    """An interactive callback queued after bucket 1 runs before bucket 2."""
    _seed(
        server,
        [
            {"perceptual_hash": PHASH_ZERO},
            {"perceptual_hash": PHASH_ONE_BIT},
        ],
    )
    progress = _run(
        server,
        tiers.request_scan_in_session,
        TierPolicy(near_enabled=True),
        None,
    )
    task = DedupScanTask(server.vault.db, progress["scan_id"])
    order: list[str] = []
    queued_interactive = threading.Event()
    original_bucket_slice = DedupScanTask._run_near_bucket_slice

    def marked_bucket_slice(session, *args):
        order.append(f"bucket-{args[3]}")
        return original_bucket_slice(session, *args)

    monkeypatch.setattr(
        DedupScanTask, "_run_near_bucket_slice", staticmethod(marked_bucket_slice)
    )
    original_run_low_slice = task._run_low_slice

    def run_low_slice(func, *args):
        result = original_run_low_slice(func, *args)
        if func is marked_bucket_slice and not queued_interactive.is_set():
            queued_interactive.set()
            server.vault.db.submit_task(
                lambda _session: order.append("interactive"),
                priority=DBPriority.IMMEDIATE,
            )
        return result

    monkeypatch.setattr(task, "_run_low_slice", run_low_slice)
    task._run_task()
    bucket_positions = [i for i, item in enumerate(order) if item.startswith("bucket-")]
    assert len(bucket_positions) >= 2, order
    assert bucket_positions[0] < order.index("interactive") < bucket_positions[1]


def test_sliced_scan_preserves_cross_bucket_chaining_and_restart(server):
    ids = _seed(
        server,
        [
            {
                "perceptual_hash": PHASH_ZERO,
                "width": 100,
                "height": 100,
                "file_path": "/a/one.png",
            },
            {
                "perceptual_hash": PHASH_ONE_BIT,
                "width": 100,
                "height": 100,
                "file_path": "/b/two.png",
            },
            {
                "perceptual_hash": PHASH_FOUR_BITS,
                "width": 200,
                "height": 200,
                "file_path": "/b/three.png",
            },
        ],
    )
    progress = _run(
        server,
        tiers.request_scan_in_session,
        TierPolicy(near_enabled=True),
        None,
    )

    def mark_interrupted(session, scan_id):
        scan = session.get(DedupScan, scan_id)
        scan.status = SCAN_RUNNING
        session.add(scan)
        session.commit()

    _run(server, mark_interrupted, progress["scan_id"])
    _run(
        server,
        DedupScanTask._mark_pending_after_cancel,
        progress["scan_id"],
    )
    pending = _run(server, DedupScanTask.find_pending_scan)
    assert pending.id == progress["scan_id"]
    assert pending.status == SCAN_PENDING
    resumed_task = DedupScanFinder(server.vault.db).find_task()
    assert resumed_task is not None
    assert resumed_task.params["scan_id"] == progress["scan_id"]
    DedupScanTask(server.vault.db, progress["scan_id"])._run_task()
    rows = _run(server, lambda session: session.exec(select(DedupGroup)).all())
    matching = [row for row in rows if row.tier == "near" and row.member_count == 3]
    assert len(matching) == 1
    members = _run(
        server,
        lambda session: session.exec(
            select(tiers.DedupGroupMember.picture_id).where(
                tiers.DedupGroupMember.group_id == matching[0].id
            )
        ).all(),
    )
    assert sorted(members) == sorted(ids)


def test_task_runner_shutdown_stops_scan_after_current_slice():
    """Shutdown lets one active callback finish but submits no later slice."""
    slice_started = threading.Event()
    release_slice = threading.Event()
    calls: list[str] = []

    class FakeDatabase:
        def run_task(self, func, *args, priority):
            calls.append(func.__name__)
            if func.__name__ == "_start_scan_slice":
                slice_started.set()
                assert release_slice.wait(timeout=2)
                return {
                    "policy": TierPolicy(near_enabled=True).as_dict(),
                    "scope_type": "global",
                    "scope_id": None,
                    "total_pictures": 100,
                    "groups_found": 1,
                }
            if func.__name__ == "_mark_pending_after_cancel":
                assert priority == DBPriority.IMMEDIATE
                return None
            raise AssertionError(f"shutdown submitted later slice {func.__name__}")

    task = DedupScanTask(FakeDatabase(), scan_id=7)
    runner = TaskRunner(name="dedup-shutdown-test", num_workers=1)
    runner.start()
    runner.submit(task)
    assert slice_started.wait(timeout=1)

    shutdown = threading.Thread(target=runner.stop)
    shutdown.start()
    # stop() must wait for the callback that currently owns the DB session.
    shutdown.join(timeout=0.05)
    assert shutdown.is_alive()

    release_slice.set()
    shutdown.join(timeout=1)
    assert not shutdown.is_alive()
    assert calls == ["_start_scan_slice", "_mark_pending_after_cancel"]
    assert task.result == {
        "scan_id": 7,
        "status": SCAN_PENDING,
        "cancelled": True,
    }


def test_legacy_exact_plus_embedding_scan_has_defined_phase_progress(monkeypatch):
    """A persisted pre-validator tier combination must not unbind total_phases."""

    class LegacyPolicy:
        def __init__(self, **values):
            self.near_enabled = bool(values.get("near_enabled"))
            self.embedding_enabled = bool(values.get("embedding_enabled"))
            self.threshold = float(values.get("threshold", 0.9))
            self.min_group_size = int(values.get("min_group_size", 2))
            self.max_group_size = int(values.get("max_group_size", 24))

    monkeypatch.setattr(dedup_scan_task_module, "TierPolicy", LegacyPolicy)

    class FakeDatabase:
        def run_task(self, func, *args, priority):
            if func.__name__ == "_start_scan_slice":
                return {
                    "policy": {
                        "near_enabled": False,
                        "embedding_enabled": True,
                        "threshold": 0.9,
                        "min_group_size": 2,
                        "max_group_size": 24,
                    },
                    "scope_type": "global",
                    "scope_id": None,
                    "total_pictures": 1,
                    "groups_found": 0,
                }
            if func.__name__ == "_embedding_scope_slice":
                return None
            if func.__name__ == "_embedding_edge_page_slice":
                return {"edges": [], "cursor": (-1, -1), "done": True}
            if func.__name__ == "_finish_scan_slice":
                assert task.task_progress() == (2, 3)
                return {
                    "scan_id": 1,
                    "scope": "global",
                    "total_pictures": 1,
                    "groups_found": 0,
                }
            raise AssertionError(f"unexpected slice {func.__name__}")

    task = DedupScanTask(FakeDatabase(), scan_id=1)
    assert task._run_task()["scan_id"] == 1
    assert task.task_progress() == (3, 3)


def test_near_scan_with_no_buckets_keeps_finalisation_remaining():
    class FakeDatabase:
        def run_task(self, func, *args, priority):
            if func.__name__ == "_start_scan_slice":
                return {
                    "policy": TierPolicy(near_enabled=True).as_dict(),
                    "scope_type": "global",
                    "scope_id": None,
                    "total_pictures": 0,
                    "groups_found": 0,
                }
            if func.__name__ == "_prepare_near_slice":
                return []
            if func.__name__ == "_finish_scan_slice":
                assert task.task_progress() == (2, 3)
                return {
                    "scan_id": 1,
                    "scope": "global",
                    "total_pictures": 0,
                    "groups_found": 0,
                }
            raise AssertionError(f"unexpected slice {func.__name__}")

    task = DedupScanTask(FakeDatabase(), scan_id=1)
    task._run_task()
    assert task.task_progress() == (3, 3)


def test_scan_progress_for_an_unscanned_scope_is_idle_not_an_error(server):
    progress = _run(server, tiers.scan_progress_in_session, None)
    assert progress["status"] == "idle"
    assert progress["total_pictures"] == 0


# ── R1: the group signature must be injective over groups ─────────────────────


def test_two_groups_sharing_a_digest_but_not_a_size_stay_distinct(server):
    """Regression for the CSO's E1: the signature needs the ``size_bytes`` co-key.

    ``pixel_sha`` is a *sampled* digest above 128 KiB, which is exactly why tier 1
    detects on ``(pixel_sha, size_bytes)``. Identity that dropped the size made
    two distinct exact groups collapse onto one signature, and all three
    consequences were silent: one group vanished from the queue via the
    upsert-on-signature, a keep-separate on the survivor resolved both file sets,
    and a stack verdict's write target depended on scan order rather than on what
    the user saw.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 999},
            {"pixel_sha": "aaa", "size_bytes": 999},
        ],
    )
    groups = _run(server, tiers.find_exact_groups_in_session, None)
    assert len(groups) == 2
    assert sorted(sorted(g.picture_ids) for g in groups) == [
        sorted(ids[:2]),
        sorted(ids[2:]),
    ]
    # The whole point: two groups, two signatures.
    assert len({g.signature for g in groups}) == 2

    # ...and therefore two persisted rows, not one silently overwriting the other.
    _scan(server)
    rows = _run(
        server,
        lambda session: sorted(
            (row.signature, row.member_count)
            for row in session.exec(select(DedupGroup)).all()
        ),
    )
    assert len(rows) == 2
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 2


def test_a_verdict_on_one_group_does_not_resolve_its_same_digest_twin(server):
    """The second half of E1: consent must not leak across file sets."""
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 999},
            {"pixel_sha": "aaa", "size_bytes": 999},
        ],
    )
    _scan(server)
    page, total, _cursor = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    assert total == 2
    _run(server, verdicts.apply_keep_separate_in_session, page[0]["signature"], None)
    # The other group is still waiting for its own decision.
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1
    remaining, _total, _cursor = _run(
        server, tiers.page_queue_in_session, None, None, 0, 10
    )
    assert len(remaining) == 1
    assert remaining[0]["signature"] == page[1]["signature"]

    # And a rescan does not resurrect the decided one or silence the open one.
    _scan(server)
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1


def test_content_key_carries_the_size_co_key():
    """Unit-level pin on the identity format itself."""
    member = tiers.CandidateMember(id=1, pixel_sha="aaa", size_bytes=100)
    twin = tiers.CandidateMember(id=2, pixel_sha="aaa", size_bytes=999)
    assert member.content_key == "aaa:100"
    assert member.content_key != twin.content_key
    assert tiers.group_signature([member.content_key]) != tiers.group_signature(
        [twin.content_key]
    )
    # An unhashed picture still falls back to its id.
    assert tiers.CandidateMember(id=7).content_key == "id:7"


# ── tier precedence on upsert ────────────────────────────────────────────────


def _group_row(server, signature):
    return _run(
        server,
        lambda session: session.exec(
            select(DedupGroup).where(DedupGroup.signature == signature)
        ).first(),
    )


def test_a_near_scan_does_not_downgrade_an_exact_group(server):
    """QA blocker 2, the two-scan repro.

    A byte-identical pair is also perceptually identical, so every near-enabled
    scan rediscovers it as a ``near`` group with the same signature. The upsert
    wrote ``row.tier`` unconditionally, so the pair was demoted to ``near`` - and
    a ``near`` group is invisible in the exact-only default queue *and*
    ineligible for ``POST /dedup/auto-stack``, which only ever acts on ``exact``.
    """
    _seed(
        server,
        [
            {
                "pixel_sha": "same",
                "size_bytes": 100,
                "perceptual_hash": PHASH_ZERO,
            },
            {
                "pixel_sha": "same",
                "size_bytes": 100,
                "perceptual_hash": PHASH_ZERO,
            },
        ],
    )
    # Scan 1: exact only.
    _run(server, tiers.run_scan_now_in_session, TierPolicy(), None)
    rows = _run(server, lambda session: session.exec(select(DedupGroup)).all())
    assert len(rows) == 1
    signature = rows[0].signature
    assert rows[0].tier == DedupTier.EXACT.value
    exact_confidence = rows[0].confidence

    # Scan 2: the user turns tier 2 on.
    _run(
        server,
        tiers.run_scan_now_in_session,
        TierPolicy(near_enabled=True),
        None,
    )
    row = _group_row(server, signature)
    assert row.tier == DedupTier.EXACT.value, "exact must never be downgraded"
    assert row.confidence == exact_confidence

    # And the pair is still where the exact-only default queue and auto-stack
    # both look for it.
    page, _total, _cursor = _run(
        server, tiers.page_queue_in_session, TierPolicy(), None, 0, 10
    )
    assert [group["tier"] for group in page] == [DedupTier.EXACT.value]


def test_the_upsert_takes_the_stronger_tier_in_either_arrival_order(server):
    """Precedence is a *maximum*, not a freeze on whatever landed first.

    Driven through :func:`persist_groups_in_session` directly, because a real
    scan cannot present the same signature under two tiers in the weak-then-
    strong order: the signature hashes the members' ``<pixel_sha>:<size_bytes>``
    content keys, so anything tier 1 can group tier 2 also sees, and the reverse
    change (two files becoming byte-identical) changes the content keys and
    therefore the signature. The precedence rule still has to hold both ways, or
    it is an ordering accident rather than a rule.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "same", "size_bytes": 100},
            {"pixel_sha": "same", "size_bytes": 100},
        ],
    )

    def upsert(session, first, second):
        members = tiers.load_candidates(session, ids)
        ordered = [members[pid] for pid in ids]
        for tier, confidence in (first, second):
            tiers.persist_groups_in_session(
                session, [tiers.assemble_group(tier, confidence, ordered)]
            )
        return session.exec(select(DedupGroup)).all()

    strong = (DedupTier.EXACT, 1.0)
    weak = (DedupTier.NEAR, 0.93)

    rows = _run(server, upsert, strong, weak)
    assert [row.tier for row in rows] == [DedupTier.EXACT.value]
    assert rows[0].confidence == 1.0

    rows = _run(server, upsert, weak, strong)
    assert [row.tier for row in rows] == [DedupTier.EXACT.value]
    assert rows[0].confidence == 1.0


# ── keyset cursor ────────────────────────────────────────────────────────────


def test_a_cursor_round_trips_its_position_exactly():
    """Float equality drives the tie-break branch, so the encoding must be exact."""
    for confidence in (1.0, 0.9, 0.65, 0.123456789012345, 0.0):
        cursor = tiers.encode_queue_cursor(confidence, 4242)
        assert tiers.decode_queue_cursor(cursor) == (confidence, 4242)


def test_a_tampered_cursor_is_refused_rather_than_reinterpreted():
    for bad in ("", "AAAA", "not base64!", tiers.encode_queue_cursor(1.0, 1)[:-4]):
        with pytest.raises(tiers.DedupCursorError):
            tiers.decode_queue_cursor(bad)
    # A cursor from a future encoding version is refused, not misread.
    import base64

    forged = base64.urlsafe_b64encode(b"9|1.0|1").decode().rstrip("=")
    with pytest.raises(tiers.DedupCursorError):
        tiers.decode_queue_cursor(forged)


def test_the_cursor_resumes_a_tied_confidence_run_without_gap_or_repeat(server):
    """Every exact group ties at the same confidence; the id breaks the tie."""
    for index in range(5):
        _seed(
            server,
            [
                {"pixel_sha": f"tie-{index}", "size_bytes": 100 + index},
                {"pixel_sha": f"tie-{index}", "size_bytes": 100 + index},
            ],
        )
    _run(server, tiers.run_scan_now_in_session, TierPolicy(), None)

    walked = []
    cursor = None
    for _ in range(10):
        page, total, cursor = _run(
            server, tiers.page_queue_in_session, None, None, 0, 2, cursor
        )
        assert total == 5
        walked.extend(group["signature"] for group in page)
        if cursor is None:
            break
    assert len(walked) == 5, walked
    assert len(set(walked)) == 5, "a tie must not be delivered twice"


# ── folder scope normalisation ───────────────────────────────────────────────


def test_a_folder_scope_that_normalises_to_empty_is_refused():
    """CSO W2: these all rstripped to "" and became a LIKE pattern of "%"."""
    for bad in ("/", "\\", "///", "\\\\", "/\\/"):
        with pytest.raises(ValueError):
            DedupScope(scope_type=ScopeType.FOLDER, scope_id=bad)


def test_a_folder_scope_is_normalised_to_one_scope_key():
    plain = DedupScope(scope_type=ScopeType.FOLDER, scope_id="/photos/2026")
    trailing = DedupScope(scope_type=ScopeType.FOLDER, scope_id="/photos/2026/")
    assert plain.key == trailing.key == "folder:/photos/2026"
    assert trailing.scope_id == "/photos/2026"


# ── stack units: the deck's depth is the STACK's, not the group's ─────────────


def _stack(server, picture_ids, thumbnails=None):
    """Put *picture_ids* in one stack, in order, and return the stack id.

    ``picture_ids[0]`` becomes the leader (``stack_position`` 0). *thumbnails*
    optionally maps a picture id to a ``(width, height)`` pair so the leader's
    cache-buster token is a real value rather than the unprocessed ``"0"``.
    """

    def build(session):
        stack = PictureStack(name=None)
        session.add(stack)
        session.commit()
        session.refresh(stack)
        for position, picture_id in enumerate(picture_ids):
            picture = session.get(Picture, int(picture_id))
            picture.stack_id = int(stack.id)
            picture.stack_position = position
            size = (thumbnails or {}).get(int(picture_id))
            if size is not None:
                picture.thumbnail_width, picture.thumbnail_height = size
            session.add(picture)
        session.commit()
        return int(stack.id)

    return _run(server, build)


def _lock_set(server, name, picture_ids):
    """Create a LOCKED picture set containing *picture_ids*; return its id."""

    def build(session):
        picture_set = PictureSet(name=name, locked=True)
        session.add(picture_set)
        session.commit()
        session.refresh(picture_set)
        for picture_id in picture_ids:
            session.add(
                PictureSetMember(set_id=int(picture_set.id), picture_id=int(picture_id))
            )
        session.commit()
        return int(picture_set.id)

    return _run(server, build)


def _only_group(server):
    """Scan, page the queue, and return the single group it serves."""
    _run(server, tiers.run_scan_now_in_session, TierPolicy(), None)
    page, _total, _cursor = _run(server, tiers.page_queue_in_session)
    assert len(page) == 1, page
    return page[0]


def test_a_group_naming_one_member_of_a_four_stack_reports_the_stacks_depth(server):
    """The measured majority case: 36 of 116 stack-touching groups name ONE
    member of a stack. The deck must still stand for the whole stack, or the
    row draws a 4-deep stack as one picture and then silently moves four."""
    ids = _seed(
        server,
        [
            # The stack: leader, two siblings the group never names, and the
            # member that is a byte-identical duplicate of the loose picture.
            {"pixel_sha": "stack-leader", "size_bytes": 10},
            {"pixel_sha": "stack-b", "size_bytes": 11},
            {"pixel_sha": "stack-c", "size_bytes": 12},
            {"pixel_sha": "shared", "size_bytes": 100},
            # The loose picture it duplicates.
            {"pixel_sha": "shared", "size_bytes": 100},
        ],
    )
    stack_id = _stack(server, ids[:4], thumbnails={ids[0]: (1024, 768)})

    group = _only_group(server)
    assert sorted(c["picture_id"] for c in group["candidates"]) == sorted(ids[3:])

    stacks = group["stacks"]
    assert list(stacks) == [str(stack_id)], "keyed by stack id, as a string"
    deck = stacks[str(stack_id)]
    assert deck["stack_id"] == stack_id
    # The stack's REAL depth, not the one member of it that is in the group.
    assert deck["member_count"] == 4
    assert deck["matched_picture_ids"] == [ids[3]]
    # The face is the stack's leader, which is NOT the matched member.
    assert deck["leader_picture_id"] == ids[0]
    assert deck["leader_picture_id"] not in deck["matched_picture_ids"]
    assert deck["leader_thumbnail_version"] == "1024x768"
    assert deck["stackable"] is True
    assert deck["blocked_by_sets"] == []
    # Eager count and leader, LAZY members: the members are never inlined.
    assert "members" not in deck
    assert "member_ids" not in deck


def test_a_group_naming_a_whole_two_stack_reports_two_and_two(server):
    """When the group does name every member, depth and matched agree."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "shared", "size_bytes": 100},
            {"pixel_sha": "shared", "size_bytes": 100},
            {"pixel_sha": "shared", "size_bytes": 100},
        ],
    )
    stack_id = _stack(server, ids[:2])

    group = _only_group(server)
    deck = group["stacks"][str(stack_id)]
    assert deck["member_count"] == 2
    assert deck["matched_picture_ids"] == sorted(ids[:2])
    assert deck["leader_picture_id"] == ids[0]
    # The loose third picture is its own unit and contributes no stacks entry.
    assert len(group["stacks"]) == 1


def test_a_deck_with_one_locked_member_is_unstackable_at_unit_level(server):
    """A stack cannot be partially stacked, so ONE frozen member freezes the
    deck: including a member the group never names, because a locked set
    freezes a whole stack.

    Three units keep the group in the queue at all: a frozen deck plus two loose
    pictures still leaves two stackable units, which is what
    ``_live_groups_filter`` requires.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "stack-leader", "size_bytes": 10},
            {"pixel_sha": "shared", "size_bytes": 100},
            {"pixel_sha": "shared", "size_bytes": 100},
            {"pixel_sha": "shared", "size_bytes": 100},
        ],
    )
    stack_id = _stack(server, ids[:2])
    # The LEADER is locked, and the leader is not in the group at all.
    set_id = _lock_set(server, "Frozen", [ids[0]])

    group = _only_group(server)
    deck = group["stacks"][str(stack_id)]
    assert deck["matched_picture_ids"] == [ids[1]], "only the sibling is in the group"
    assert deck["member_count"] == 2
    assert deck["stackable"] is False
    assert deck["blocked_by_sets"] == [{"id": set_id, "name": "Frozen"}]
    # The per-candidate value it rolls up says the same thing, and the two loose
    # units are untouched: over-blocking would be its own regression.
    by_id = {c["picture_id"]: c for c in group["candidates"]}
    assert by_id[ids[1]]["stackable"] is False
    assert by_id[ids[2]]["stackable"] is True
    assert by_id[ids[3]]["stackable"] is True


def test_the_deck_rollup_costs_one_query_for_the_whole_page(server):
    """The page resolves stacks once, not once per group (no N+1).

    Five groups, each touching its own stack. ``load_stack_facts`` must be
    called exactly once for the page, with every stack id in it.
    """
    stack_ids = []
    for index in range(5):
        ids = _seed(
            server,
            [
                {"pixel_sha": f"lead-{index}", "size_bytes": 10 + index},
                {"pixel_sha": f"shared-{index}", "size_bytes": 100 + index},
                {"pixel_sha": f"shared-{index}", "size_bytes": 100 + index},
            ],
        )
        stack_ids.append(_stack(server, ids[:2]))
    _run(server, tiers.run_scan_now_in_session, TierPolicy(), None)

    calls = []
    original = tiers.load_stack_facts

    def counting(session, ids):
        materialised = sorted({int(i) for i in ids})
        calls.append(materialised)
        return original(session, materialised)

    tiers.load_stack_facts = counting
    try:
        page, _total, _cursor = _run(server, tiers.page_queue_in_session)
    finally:
        tiers.load_stack_facts = original

    assert len(page) == 5
    assert len(calls) == 1, f"one batched resolve per page, got {len(calls)}"
    assert calls[0] == sorted(stack_ids)


def test_the_leader_ranking_matches_the_grids(server):
    """An unpositioned stack still names a leader, by the same rule the grid
    uses: position (NULLs last), then score, then newest capture, then id."""
    ids = _seed(
        server,
        [
            {"score": 1, "created_at": 0},
            {"score": 5, "created_at": 10},
            {"score": 5, "created_at": 20},
        ],
    )

    def unposition(session):
        stack = PictureStack(name=None)
        session.add(stack)
        session.commit()
        session.refresh(stack)
        for picture_id in ids:
            picture = session.get(Picture, picture_id)
            picture.stack_id = int(stack.id)
            picture.stack_position = None
            session.add(picture)
        session.commit()
        return int(stack.id)

    stack_id = _run(server, unposition)
    facts = _run(server, tiers.load_stack_facts, [stack_id])[stack_id]
    # Highest score wins; the newer capture breaks the tie between the two 5s.
    assert facts.leader_picture_id == ids[2]
    assert facts.member_ids == (ids[2], ids[1], ids[0])
    assert facts.member_count == 3


def test_a_scrapheaped_member_is_not_part_of_the_stacks_depth(server):
    """``member_count`` is the LIVE member count: a deck must not promise to
    move a picture that is already in the scrapheap."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "a", "size_bytes": 10},
            {"pixel_sha": "b", "size_bytes": 11},
            {"pixel_sha": "c", "size_bytes": 12, "deleted": True},
        ],
    )
    stack_id = _stack(server, ids)
    facts = _run(server, tiers.load_stack_facts, [stack_id])[stack_id]
    assert facts.member_count == 2
    assert facts.member_ids == (ids[0], ids[1])


# ── the lazy half: one stack's members, paged ────────────────────────────────


def test_stack_members_pages_in_canonical_order(server):
    ids = _seed(server, [{"pixel_sha": f"m{index}"} for index in range(5)])
    stack_id = _stack(server, ids, thumbnails={ids[0]: (640, 480)})

    first = _run(server, tiers.stack_members_in_session, stack_id, 0, 2)
    assert first["stack_id"] == stack_id
    assert first["member_count"] == 5
    assert first["leader_picture_id"] == ids[0]
    assert first["leader_thumbnail_version"] == "640x480"
    assert [m["picture_id"] for m in first["members"]] == ids[:2]
    assert [m["position"] for m in first["members"]] == [0, 1]
    assert [m["is_leader"] for m in first["members"]] == [True, False]
    assert first["next_offset"] == 2
    assert first["stackable"] is True
    assert first["blocked_by_sets"] == []
    # The tile fields are the queue candidate's, unchanged, so the expansion
    # strip reuses the row's tile.
    assert "thumbnail_version" in first["members"][0]
    assert "smart_score" in first["members"][0]

    last = _run(server, tiers.stack_members_in_session, stack_id, 4, 2)
    assert [m["picture_id"] for m in last["members"]] == [ids[4]]
    assert [m["position"] for m in last["members"]] == [4]
    assert last["next_offset"] is None


def test_stack_members_clamps_the_page_size(server):
    ids = _seed(server, [{"pixel_sha": "one"}, {"pixel_sha": "two"}])
    stack_id = _stack(server, ids)
    page = _run(
        server,
        tiers.stack_members_in_session,
        stack_id,
        0,
        tiers.MAX_STACK_MEMBER_PAGE_SIZE * 10,
    )
    assert page["limit"] == tiers.MAX_STACK_MEMBER_PAGE_SIZE


def test_stack_members_rolls_the_lock_up_over_the_whole_stack(server):
    """Page 2 must not report a different stackability from page 1, so the unit
    rollup is taken over every member, not over the page."""
    ids = _seed(server, [{"pixel_sha": f"m{index}"} for index in range(4)])
    stack_id = _stack(server, ids)
    set_id = _lock_set(server, "Frozen", [ids[3]])

    for offset in (0, 2):
        page = _run(server, tiers.stack_members_in_session, stack_id, offset, 2)
        assert page["stackable"] is False, offset
        assert page["blocked_by_sets"] == [{"id": set_id, "name": "Frozen"}]
        # A locked set freezes the whole stack, so every member is out.
        assert all(member["stackable"] is False for member in page["members"])


def test_stack_members_is_none_for_a_stack_with_no_live_members(server):
    """The route turns this into a 404 rather than an empty stack that looks
    like it exists."""
    assert _run(server, tiers.stack_members_in_session, 987654, 0, 10) is None
    ids = _seed(server, [{"pixel_sha": "gone", "deleted": True}])
    stack_id = _stack(server, ids)
    assert _run(server, tiers.stack_members_in_session, stack_id, 0, 10) is None


# ── release-candidate scan generation and bounded-work regressions ───────────


def _run_requested_scan(server, policy=None, scope=None):
    progress = _run(
        server,
        tiers.request_scan_in_session,
        policy or TierPolicy(),
        scope,
    )
    return DedupScanTask(server.vault.db, progress["scan_id"])._run_task()


def test_completed_rescan_retires_obsolete_exact_evidence(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "same", "size_bytes": 100},
            {"pixel_sha": "same", "size_bytes": 100},
        ],
    )
    first = _run_requested_scan(server)
    assert first["status"] == "complete"
    assert len(_run(server, lambda s: s.exec(select(DedupGroup)).all())) == 1

    def make_distinct(session):
        picture = session.get(Picture, ids[1])
        picture.pixel_sha = "different"
        session.add(picture)
        session.commit()

    _run(server, make_distinct)
    second = _run_requested_scan(server)

    assert second["status"] == "complete"
    assert second["retired_groups"] == 1
    assert _run(server, lambda s: s.exec(select(DedupGroup)).all()) == []


def test_failed_and_cancelled_rescans_preserve_prior_complete_evidence(
    server, monkeypatch
):
    ids = _seed(
        server,
        [
            {"pixel_sha": "same", "size_bytes": 100},
            {"pixel_sha": "same", "size_bytes": 100},
        ],
    )
    _run_requested_scan(server)

    def make_distinct(session):
        picture = session.get(Picture, ids[1])
        picture.pixel_sha = "different"
        session.add(picture)
        session.commit()

    _run(server, make_distinct)
    cancelled = _run(server, tiers.request_scan_in_session, TierPolicy(), None)
    _run(server, DedupScanTask._start_scan_slice, cancelled["scan_id"])
    _run(server, DedupScanTask._mark_pending_after_cancel, cancelled["scan_id"])
    assert len(_run(server, lambda s: s.exec(select(DedupGroup)).all())) == 1

    # Allow a fresh request, then fail before a successful finalisation. Neither
    # path is allowed to erase the last complete generation's queue evidence.
    def make_complete(session):
        scan = session.get(DedupScan, cancelled["scan_id"])
        scan.status = "complete"
        session.add(scan)
        session.commit()

    _run(server, make_complete)
    failed = _run(server, tiers.request_scan_in_session, TierPolicy(), None)

    def fail_exact(_session, _scope):
        raise RuntimeError("forced exact scan failure")

    monkeypatch.setattr(tiers, "find_exact_groups_in_session", fail_exact)
    with pytest.raises(RuntimeError, match="forced exact scan failure"):
        DedupScanTask(server.vault.db, failed["scan_id"])._run_task()
    progress = _run(server, tiers.scan_progress_in_session, None)
    assert progress["status"] == "failed"
    assert len(_run(server, lambda s: s.exec(select(DedupGroup)).all())) == 1


def test_scan_retirement_covers_all_complete_tiers_but_not_sibling_evidence(server):
    def seed_generations(session):
        first = DedupScan(scope_key="set:1", scope_type="set", scope_id="1")
        sibling = DedupScan(scope_key="set:2", scope_type="set", scope_id="2")
        session.add(first)
        session.add(sibling)
        session.flush()
        owned = [
            DedupGroup(
                signature=f"owned-stale-{tier}",
                tier=tier,
                confidence=1.0,
                member_count=2,
                scan_id=int(first.id),
            )
            for tier in ("exact", "near", "embedding")
        ]
        protected = DedupGroup(
            signature="sibling-current",
            tier="exact",
            confidence=1.0,
            member_count=2,
            scan_id=int(sibling.id),
        )
        session.add_all(owned)
        session.add(protected)
        session.commit()
        return int(first.id)

    scan_id = _run(server, seed_generations)

    def retire_and_commit(session):
        removed = tiers.retire_obsolete_scan_groups_in_session(
            session,
            scan_id,
            {"exact": set(), "near": set(), "embedding": set()},
            {"exact", "near", "embedding"},
        )
        session.commit()
        return removed

    removed = _run(server, retire_and_commit)
    remaining = _run(
        server, lambda s: [row.signature for row in s.exec(select(DedupGroup)).all()]
    )
    assert removed == 3
    assert remaining == ["sibling-current"]


def test_incomplete_tier_is_omitted_from_generation_retirement(server):
    def seed_partial_generation(session):
        scan = DedupScan(scope_key="global", scope_type="global")
        session.add(scan)
        session.flush()
        session.add(
            DedupGroup(
                signature="prior-complete-near",
                tier="near",
                confidence=0.95,
                member_count=2,
                scan_id=int(scan.id),
            )
        )
        session.commit()
        return int(scan.id)

    scan_id = _run(server, seed_partial_generation)

    def retire_complete_tiers(session):
        removed = tiers.retire_obsolete_scan_groups_in_session(
            session,
            scan_id,
            {"exact": set(), "near": set()},
            {"exact"},
        )
        session.commit()
        return removed

    assert _run(server, retire_complete_tiers) == 0
    assert _run(
        server, lambda s: [row.signature for row in s.exec(select(DedupGroup)).all()]
    ) == ["prior-complete-near"]


def test_exact_only_scan_does_not_depend_on_embedding_work(server):
    _run(server, tiers.request_scan_in_session, TierPolicy(), None)
    finder = DedupScanFinder(server.vault.db)
    assert finder.depends_on() == [TaskType.PIXEL_SHA]


def test_4001_member_bucket_keeps_the_boundary_member(monkeypatch):
    rows = [(picture_id, 1, None, None, None) for picture_id in range(1, 4002)]
    monkeypatch.setattr(tiers, "_bucket_rows", lambda _session, _scope: rows)

    buckets = tiers.build_near_buckets(object(), DedupScope())

    assert [len(bucket.picture_ids) for bucket in buckets] == [4000, 2]
    assert buckets[0].picture_ids[-1] == buckets[1].picture_ids[0] == 4000
    assert set().union(*(set(bucket.picture_ids) for bucket in buckets)) == set(
        range(1, 4002)
    )
    assert all(bucket.oversized for bucket in buckets)


def test_pair_cap_marks_scan_partial_and_preserves_near_evidence(server, monkeypatch):
    _seed(
        server,
        [
            {"perceptual_hash": PHASH_ZERO},
            {"perceptual_hash": PHASH_ZERO},
            {"perceptual_hash": PHASH_ZERO},
        ],
    )
    monkeypatch.setattr(tiers, "MAX_PAIRS_PER_BUCKET", 1)

    summary = _run_requested_scan(server, TierPolicy(near_enabled=True))
    progress = _run(server, tiers.scan_progress_in_session, None)

    assert summary["status"] == "partial"
    assert progress["status"] == "partial"
    assert "pair cap" in progress["error"]
    assert any(
        row.tier == "near"
        for row in _run(server, lambda s: s.exec(select(DedupGroup)).all())
    )
