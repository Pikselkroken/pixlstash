"""Unit tests for the vault-wide near-duplicate sweep planner.

Covers the four things Lane E promotes server-side:

* **vault-wide grouping** - union-find over the streamed likeness edge table
  produces the same connected components the grid's BFS would, including the
  transitive chain A~B~C with no A~C edge, and excludes scrapheap pictures;
* **the confidence policy** - each axis (auto-resolve likeness, group size,
  smart-score margin, missing smart score) independently routes a group to the
  review lane with its own reason code, and every axis is a policy parameter;
* **merge-or-report for groups spanning existing stacks** - represented as a
  ``merge_stacks`` outcome under both dispositions, never skipped;
* **the report's arithmetic** - counts, held bytes, and the listing cap.

Plus the non-destructive invariant: planning a sweep changes no row.
"""

import gc
import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, PictureStack
from pixlstash.db_models.picture_likeness import PictureLikeness
from pixlstash.server import Server
from pixlstash.services.dedup_sweep_service import (
    CrossStackPolicy,
    ReviewReason,
    SweepMember,
    SweepOutcome,
    SweepPolicy,
    SweepVerdict,
    evaluate_keeper_margin,
    member_order_key,
    plan_near_duplicate_sweep,
    stream_likeness_edges,
)

_BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


@pytest.fixture
def server():
    temp_dir = tempfile.TemporaryDirectory()
    config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(config_path, "w") as fh:
        fh.write(json.dumps({"port": 8000}))
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


def _seed(server, pictures, edges, stacks=None):
    """Create *pictures*, *edges* and *stacks*; return the id of each picture.

    ``pictures`` is a list of dicts with optional ``score``/``smart_score``/
    ``size_bytes``/``deleted``/``stack`` (a stack label). ``edges`` is a list of
    ``(index_a, index_b, likeness)`` over the pictures list. Returns the picture
    ids in the same order.
    """

    def create(session):
        stack_ids = {}
        for label in sorted(stacks or []):
            stack = PictureStack(name=label)
            session.add(stack)
            session.commit()
            session.refresh(stack)
            stack_ids[label] = stack.id

        picture_ids = []
        for index, spec in enumerate(pictures):
            pic = Picture(
                file_path=f"/vault/pic_{index}.png",
                format="png",
                width=64,
                height=64,
                size_bytes=spec.get("size_bytes", 1000),
                created_at=_BASE_TIME + timedelta(minutes=index),
                score=spec.get("score"),
                smart_score=spec.get("smart_score"),
                deleted=spec.get("deleted", False),
                stack_id=stack_ids.get(spec.get("stack")),
            )
            session.add(pic)
            session.commit()
            session.refresh(pic)
            picture_ids.append(pic.id)

        for index_a, index_b, likeness in edges:
            first, second = PictureLikeness.canon_pair(
                picture_ids[index_a], picture_ids[index_b]
            )
            session.add(
                PictureLikeness(
                    picture_id_a=first,
                    picture_id_b=second,
                    likeness=likeness,
                    metric="test",
                )
            )
        session.commit()
        return picture_ids

    return _run(server, create)


def _member(picture_id, *, score=None, smart_score=None, minute=0):
    return SweepMember(
        id=picture_id,
        stack_id=None,
        score=score,
        smart_score=smart_score,
        created_at=_BASE_TIME + timedelta(minutes=minute),
        size_bytes=0,
    )


# ── grouping ──────────────────────────────────────────────────────────────────


def test_transitive_chain_is_one_group_with_the_weak_link_reported(server):
    """A~B~C with no A~C edge is one group; likeness_min is the weakest edge."""
    ids = _seed(
        server,
        [{"score": 5}, {"score": 3}, {"score": 1}],
        [(0, 1, 0.97), (1, 2, 0.92)],
    )
    report = plan_near_duplicate_sweep(
        server.vault, SweepPolicy(auto_resolve_likeness=0.9)
    )

    assert report.groups_total == 1
    group = report.groups[0]
    assert sorted(group.picture_ids) == sorted(ids)
    assert group.likeness_min == pytest.approx(0.92)
    assert group.likeness_max == pytest.approx(0.97)
    assert group.outcome is SweepOutcome.CREATE_STACK
    assert group.target_stack_id is None
    assert group.keeper_id == ids[0]  # highest human score wins
    assert group.verdict is SweepVerdict.AUTO_COLLAPSE


def test_edge_streaming_pages_without_skipping_or_repeating(server):
    """Keyset pagination is the sweep's only edge reader; a gap silently loses groups."""
    ids = _seed(
        server,
        [{"score": 9 - i} for i in range(4)],
        [(0, 1, 0.99), (0, 2, 0.98), (1, 2, 0.97), (2, 3, 0.96)],
    )
    expected = {
        (min(ids[a], ids[b]), max(ids[a], ids[b]))
        for a, b in [(0, 1), (0, 2), (1, 2), (2, 3)]
    }

    def read(session, page_size):
        return list(stream_likeness_edges(session, 0.9, page_size=page_size))

    for page_size in (1, 2, 3, 4, 100):
        edges = _run(server, read, page_size)
        assert len(edges) == 4, f"page_size={page_size} returned {edges}"
        assert {(a, b) for a, b, _ in edges} == expected


def test_edges_below_the_candidate_threshold_do_not_group(server):
    _seed(server, [{"score": 5}, {"score": 3}], [(0, 1, 0.80)])
    report = plan_near_duplicate_sweep(server.vault, SweepPolicy())
    assert report.scanned_edges == 0
    assert report.groups_total == 0


def test_scrapheap_pictures_are_never_swept(server):
    """A soft-deleted endpoint drops the edge: the sweep never re-stacks trash."""
    _seed(
        server,
        [{"score": 5}, {"score": 3, "deleted": True}],
        [(0, 1, 0.99)],
    )
    report = plan_near_duplicate_sweep(server.vault, SweepPolicy())
    assert report.scanned_edges == 0
    assert report.groups_total == 0


def test_min_group_size_is_a_policy_parameter(server):
    _seed(
        server,
        [{"score": 5}, {"score": 3}, {"score": 1}],
        [(0, 1, 0.99), (1, 2, 0.99)],
    )
    assert plan_near_duplicate_sweep(server.vault, SweepPolicy()).groups_total == 1
    assert (
        plan_near_duplicate_sweep(
            server.vault, SweepPolicy(min_group_size=4)
        ).groups_total
        == 0
    )


def test_planning_mutates_nothing(server):
    """Strictly non-destructive: a dry run leaves every row exactly as it was."""
    ids = _seed(
        server,
        [{"score": 5}, {"score": 3}],
        [(0, 1, 0.99)],
    )

    def snapshot(session):
        rows = session.exec(
            select(Picture.id, Picture.stack_id, Picture.deleted).where(
                Picture.id.in_(ids)
            )
        ).all()
        stacks = session.exec(select(PictureStack.id)).all()
        return [tuple(row) for row in rows], list(stacks)

    before = _run(server, snapshot)
    report = plan_near_duplicate_sweep(server.vault, SweepPolicy())
    assert report.groups_total == 1
    assert _run(server, snapshot) == before


# ── confidence policy ─────────────────────────────────────────────────────────


def test_weak_link_below_auto_resolve_likeness_needs_review(server):
    _seed(
        server,
        [{"score": 5}, {"score": 3}, {"score": 1}],
        [(0, 1, 0.99), (1, 2, 0.91)],
    )
    report = plan_near_duplicate_sweep(
        server.vault, SweepPolicy(likeness_threshold=0.9, auto_resolve_likeness=0.95)
    )
    group = report.groups[0]
    assert group.verdict is SweepVerdict.NEEDS_REVIEW
    assert group.reasons == [ReviewReason.WEAK_LIKENESS]
    assert report.reason_counts == {ReviewReason.WEAK_LIKENESS.value: 1}
    assert report.needs_review_groups == 1
    assert report.auto_collapse_groups == 0


def test_oversized_group_needs_review(server):
    specs = [{"score": 10 - i} for i in range(5)]
    edges = [(i, i + 1, 0.99) for i in range(4)]
    _seed(server, specs, edges)
    report = plan_near_duplicate_sweep(server.vault, SweepPolicy(max_auto_group_size=4))
    group = report.groups[0]
    assert group.verdict is SweepVerdict.NEEDS_REVIEW
    assert ReviewReason.OVERSIZED_GROUP in group.reasons

    # Same vault, looser ceiling -> the same group auto-collapses.
    relaxed = plan_near_duplicate_sweep(
        server.vault, SweepPolicy(max_auto_group_size=5)
    )
    assert relaxed.groups[0].verdict is SweepVerdict.AUTO_COLLAPSE


def test_tied_scores_fall_back_to_the_smart_score_margin(server):
    _seed(
        server,
        [
            {"score": 4, "smart_score": 0.80},
            {"score": 4, "smart_score": 0.79},
        ],
        [(0, 1, 0.99)],
    )
    tight = plan_near_duplicate_sweep(
        server.vault, SweepPolicy(smart_score_margin=0.05)
    ).groups[0]
    assert tight.verdict is SweepVerdict.NEEDS_REVIEW
    assert tight.reasons == [ReviewReason.AMBIGUOUS_KEEPER]
    assert tight.keeper_margin == pytest.approx(0.01)
    assert tight.keeper_margin_basis == "smart_score"

    loose = plan_near_duplicate_sweep(
        server.vault, SweepPolicy(smart_score_margin=0.005)
    ).groups[0]
    assert loose.verdict is SweepVerdict.AUTO_COLLAPSE


def test_missing_smart_score_on_a_score_tie_needs_review(server):
    _seed(
        server,
        [{"score": 4, "smart_score": 0.9}, {"score": 4, "smart_score": None}],
        [(0, 1, 0.99)],
    )
    group = plan_near_duplicate_sweep(server.vault, SweepPolicy()).groups[0]
    assert group.verdict is SweepVerdict.NEEDS_REVIEW
    assert group.reasons == [ReviewReason.KEEPER_SMART_SCORE_MISSING]
    assert group.keeper_margin is None
    assert group.keeper_margin_basis == "none"


def test_human_score_outranks_the_smart_score_axis(server):
    """A decisive human score keeps the group in the auto lane, margin or not."""
    _seed(
        server,
        [
            {"score": 5, "smart_score": 0.10},
            {"score": 1, "smart_score": 0.99},
        ],
        [(0, 1, 0.99)],
    )
    group = plan_near_duplicate_sweep(server.vault, SweepPolicy()).groups[0]
    assert group.verdict is SweepVerdict.AUTO_COLLAPSE
    assert group.keeper_margin_basis == "score"
    assert group.keeper_margin == pytest.approx(4.0)


def test_zero_margin_switches_the_smart_score_axis_off():
    keeper = _member(1, score=4, smart_score=None)
    runner_up = _member(2, score=4, smart_score=None)
    _, _, reason = evaluate_keeper_margin(
        keeper, runner_up, SweepPolicy(smart_score_margin=0.0)
    )
    assert reason is None
    _, _, gated = evaluate_keeper_margin(
        keeper, runner_up, SweepPolicy(smart_score_margin=0.05)
    )
    assert gated is ReviewReason.KEEPER_SMART_SCORE_MISSING


def test_policy_rejects_an_auto_bar_below_the_candidate_threshold():
    with pytest.raises(ValueError, match="auto_resolve_likeness"):
        SweepPolicy(likeness_threshold=0.95, auto_resolve_likeness=0.9)


def test_policy_rejects_out_of_range_thresholds():
    with pytest.raises(ValueError, match="likeness_threshold"):
        SweepPolicy(likeness_threshold=0.1)
    with pytest.raises(ValueError, match="likeness_threshold"):
        SweepPolicy(likeness_threshold=1.5)
    with pytest.raises(ValueError, match="min_group_size"):
        SweepPolicy(min_group_size=1)


def test_member_order_key_matches_the_shipped_stack_order():
    """score DESC, smart score DESC, recency DESC, id ASC - the grid's order."""
    high_score = _member(3, score=5, smart_score=0.1, minute=0)
    high_smart = _member(1, score=5, smart_score=0.9, minute=0)
    newer = _member(2, score=5, smart_score=0.9, minute=10)
    ordered = sorted([high_score, high_smart, newer], key=member_order_key)
    assert [m.id for m in ordered] == [2, 1, 3]


# ── merge-or-report across existing stacks ────────────────────────────────────


def test_group_spanning_two_stacks_is_reported_not_skipped(server):
    """The behaviour Lane E item 7 changes: the client skips these outright."""
    ids = _seed(
        server,
        [
            {"score": 9, "stack": "left"},
            {"score": 8, "stack": "left"},
            {"score": 7, "stack": "right"},
            {"score": 6, "stack": "right"},
        ],
        [(0, 2, 0.99)],
        stacks=["left", "right"],
    )
    report = plan_near_duplicate_sweep(server.vault, SweepPolicy())

    assert report.groups_total == 1
    group = report.groups[0]
    assert group.outcome is SweepOutcome.MERGE_STACKS
    # Both stacks move as a unit, so all four pictures are in the proposal.
    assert sorted(group.picture_ids) == sorted(ids)
    assert sorted(group.linked_member_ids) == sorted([ids[1], ids[3]])
    assert group.target_stack_id is not None
    assert len(group.merged_stack_ids) == 1
    assert group.target_stack_id not in group.merged_stack_ids
    # Default disposition: propose, don't act.
    assert group.verdict is SweepVerdict.NEEDS_REVIEW
    assert ReviewReason.SPANS_MULTIPLE_STACKS in group.reasons
    assert report.outcome_counts == {SweepOutcome.MERGE_STACKS.value: 1}


def test_cross_stack_merge_disposition_moves_it_to_the_auto_lane(server):
    _seed(
        server,
        [
            {"score": 9, "stack": "left"},
            {"score": 8, "stack": "left"},
            {"score": 7, "stack": "right"},
            {"score": 6, "stack": "right"},
        ],
        [(0, 2, 0.99)],
        stacks=["left", "right"],
    )
    report = plan_near_duplicate_sweep(
        server.vault, SweepPolicy(cross_stack=CrossStackPolicy.MERGE)
    )
    group = report.groups[0]
    assert group.outcome is SweepOutcome.MERGE_STACKS
    assert group.verdict is SweepVerdict.AUTO_COLLAPSE
    assert group.reasons == []


def test_unstacked_member_joins_an_existing_stack(server):
    ids = _seed(
        server,
        [{"score": 9, "stack": "left"}, {"score": 8, "stack": "left"}, {"score": 7}],
        [(0, 2, 0.99)],
        stacks=["left"],
    )
    group = plan_near_duplicate_sweep(server.vault, SweepPolicy()).groups[0]
    assert group.outcome is SweepOutcome.ADD_TO_STACK
    assert group.target_stack_id is not None
    assert group.merged_stack_ids == []
    assert sorted(group.picture_ids) == sorted(ids)
    assert group.linked_member_ids == [ids[1]]
    assert group.verdict is SweepVerdict.AUTO_COLLAPSE


def test_group_already_fully_collapsed_is_counted_not_listed(server):
    _seed(
        server,
        [{"score": 9, "stack": "left"}, {"score": 8, "stack": "left"}],
        [(0, 1, 0.99)],
        stacks=["left"],
    )
    report = plan_near_duplicate_sweep(server.vault, SweepPolicy())
    assert report.groups_total == 0
    assert report.candidate_groups == 1
    assert report.already_collapsed_groups == 1
    assert report.absorbed_groups == 0


# ── report arithmetic ─────────────────────────────────────────────────────────


def test_report_counts_and_held_bytes_split_by_lane(server):
    # Group 1 (auto): two pictures, decisive score, strong likeness.
    # Group 2 (review): two pictures, weak link.
    _seed(
        server,
        [
            {"score": 9, "size_bytes": 100},
            {"score": 1, "size_bytes": 250},
            {"score": 9, "size_bytes": 100},
            {"score": 1, "size_bytes": 400},
        ],
        [(0, 1, 0.99), (2, 3, 0.91)],
    )
    report = plan_near_duplicate_sweep(
        server.vault, SweepPolicy(likeness_threshold=0.9, auto_resolve_likeness=0.95)
    )

    assert report.groups_total == 2
    assert report.auto_collapse_groups == 1
    assert report.needs_review_groups == 1
    assert report.auto_collapse_pictures == 2
    assert report.needs_review_pictures == 2
    # held_bytes counts the non-keeper members only.
    assert report.held_bytes_auto == 250
    assert report.held_bytes_review == 400
    assert report.scanned_edges == 2


def test_listing_cap_truncates_the_array_but_never_the_counts(server):
    _seed(
        server,
        [{"score": 9}, {"score": 1}, {"score": 9}, {"score": 1}],
        [(0, 1, 0.99), (2, 3, 0.99)],
    )
    report = plan_near_duplicate_sweep(server.vault, SweepPolicy(max_groups_listed=1))
    assert report.groups_total == 2
    assert report.auto_collapse_groups == 2
    assert len(report.groups) == 1
    assert report.listing_truncated is True


def test_operation_batch_id_is_echoed_and_writes_nothing(server):
    """The Lane-B seam: accepted, echoed, inert for a dry run."""
    _seed(server, [{"score": 9}, {"score": 1}], [(0, 1, 0.99)])
    report = plan_near_duplicate_sweep(
        server.vault, SweepPolicy(), operation_batch_id="batch-42"
    )
    assert report.operation_batch_id == "batch-42"
    assert report.as_dict()["operation_batch_id"] == "batch-42"


def test_report_serialises_to_json_safe_primitives(server):
    _seed(server, [{"score": 9}, {"score": 1}], [(0, 1, 0.99)])
    payload = plan_near_duplicate_sweep(server.vault, SweepPolicy()).as_dict()
    group = payload["groups"][0]
    assert isinstance(group["verdict"], str)
    assert isinstance(group["outcome"], str)
    assert all(isinstance(reason, str) for reason in group["reasons"])
    assert payload["policy"]["cross_stack"] == "report"
