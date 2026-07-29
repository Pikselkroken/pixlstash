"""Unit tests for the tiered duplicate detection service.

Covers, per tier and per rule:

* **tier 1 (exact)** — groups on the indexed ``pixel_sha``, refuses to group two
  files that share a digest but differ in ``size_bytes`` (the sampled-digest
  guard), and stays blind to the scrapheap;
* **tier 2 (bucketed near)** — candidate buckets come from the precomputed
  columns, comparison happens only inside a bucket, and the group's confidence is
  its weakest link;
* **tier 3 (embedding)** — reuses the shipped likeness edge table;
* **the tier policy** — exact is always on, each looser tier requires the tier
  above it, and the 0.65 floor is a hard error rather than a silent clamp;
* **cover selection** — the design's ``px*4 + tags*3 + score*2 + RAW`` formula
  and the oldest-capture tie-break;
* **evidence** — matching pills and evidence-against pills, both directions;
* **the queue** — paged by confidence descending, verdict-resolved groups never
  re-offered, and scope-narrowed counts.
"""

import gc
import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, PictureSet, PictureSetMember
from pixlstash.db_models.dedup import (
    VERDICT_KEEP_SEPARATE,
    DedupGroup,
    DedupVerdict,
)
from pixlstash.db_models.picture_likeness import PictureLikeness
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
        srv.vault.close()
        temp_dir.cleanup()
        gc.collect()


def _run(server, fn, *args):
    """Run *fn(session, \\*args)* on the DB worker and return its result."""
    return server.vault.db.run_task(fn, *args, priority=DBPriority.IMMEDIATE)


def _seed(server, specs):
    """Insert one picture per spec; return the ids in order.

    Recognised keys: ``pixel_sha``, ``perceptual_hash``, ``size_bytes``,
    ``width``, ``height``, ``score``, ``format``, ``file_path``, ``created_at``
    (an offset in seconds from ``_BASE_TIME``), ``deleted``, ``tags``,
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


# ── cover selection ───────────────────────────────────────────────────────────


def test_cover_score_is_the_designs_formula():
    # 12 MP -> 12*4 = 48; 2 tags -> 6; score 3 -> 6; not RAW -> 0.
    member = _member(width=4000, height=3000, tag_count=2, score=3)
    assert member.megapixels == pytest.approx(12.0)
    assert member.cover_score == pytest.approx(48.0 + 6.0 + 6.0)


def test_raw_earns_the_cover_bonus_by_format_or_extension():
    assert _member(format="ARW").is_raw
    assert _member(format="jpeg", file_path="/shoots/A7R0912.arw").is_raw
    assert not _member(format="jpeg", file_path="/shoots/x.jpg").is_raw
    raw = _member(id=1, format="arw", width=1000, height=1000)
    jpeg = _member(id=2, format="jpeg", width=1000, height=1000)
    assert raw.cover_score - jpeg.cover_score == pytest.approx(tiers.COVER_RAW_BONUS)


def test_cover_prefers_the_highest_score_then_the_oldest_capture():
    big = _member(id=1, width=6000, height=4000)
    small = _member(id=2, width=1000, height=1000, tag_count=1)
    assert tiers.select_cover([small, big]) == 1

    # Identical formula scores: the oldest capture time wins, not the newest.
    old = _member(id=10, created_at=_BASE_TIME)
    new = _member(id=11, created_at=_BASE_TIME + timedelta(hours=5))
    assert old.cover_score == pytest.approx(new.cover_score)
    assert tiers.select_cover([new, old]) == 10


def test_tags_can_outweigh_a_slightly_larger_picture():
    # 1 MP + 5 tags = 4 + 15 = 19 beats 4 MP with nothing = 16.
    tagged = _member(id=1, width=1000, height=1000, tag_count=5)
    bigger = _member(id=2, width=2000, height=2000)
    assert tiers.select_cover([bigger, tagged]) == 1


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
    assert texts["96% visual match"] is False
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
    # The higher human score wins the cover under the formula.
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
    page, total = _run(server, tiers.page_queue_in_session, policy, None, 0, 2)
    assert total == 3
    assert [round(group["confidence"], 4) for group in page] == [
        1.0,
        round(63 / 64, 4),
    ]
    assert page[0]["tier"] == "exact"
    second, _total = _run(server, tiers.page_queue_in_session, policy, None, 2, 2)
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
    page, _total = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    group = page[0]
    assert group["cover_picture_id"] == ids[0]
    assert any(pill["text"] == "Identical file hash" for pill in group["why"])
    cover = next(c for c in group["candidates"] if c["picture_id"] == ids[0])
    assert cover["tag_count"] == 2
    assert any(p["text"] == "Preselected as cover" for p in cover["why"])
    assert any(p["text"].startswith("Most metadata") for p in cover["why"])


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
    page, _total = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
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
    page, total = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
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
    page, _total = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
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


def test_requesting_a_scan_creates_one_row_per_scope_and_reuses_it(server):
    first = _run(server, tiers.request_scan_in_session, TierPolicy(), None)
    assert first["status"] == "pending"
    assert first["scope_key"] == "global"
    second = _run(
        server, tiers.request_scan_in_session, TierPolicy(near_enabled=True), None
    )
    assert second["scan_id"] == first["scan_id"]
    assert second["tiers"] == ["exact", "near"]


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
    page, total = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    assert total == 2
    _run(server, verdicts.apply_keep_separate_in_session, page[0]["signature"], None)
    # The other group is still waiting for its own decision.
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1
    remaining, _total = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
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
