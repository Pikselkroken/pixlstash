"""Unit tests for applying and remembering duplicate verdicts.

Covers:

* **stack** — members land in one stack led by the chosen cover, excluded members
  are untouched, and the metadata union runs (tags, sets, score);
* **keep separate** — no picture row changes, and the decision survives a rescan;
* **reopen** — the group comes back and the decision history is kept;
* **bulk auto-stack** — exact tier only, one batch id across the whole run, and
  the dry run writes nothing;
* **the non-destructive invariant** — no verdict deletes a picture, ever;
* **locked sets** — the metadata union is refused rather than half-applied.
"""

import gc
import json
import os
import tempfile

import pytest
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, PictureSet, PictureSetMember
from pixlstash.db_models.dedup import (
    VERDICT_KEEP_SEPARATE,
    VERDICT_STACKED,
    DedupVerdict,
)
from pixlstash.db_models.tag import Tag
from pixlstash.server import Server
from pixlstash.services import dedup_tier_service as tiers
from pixlstash.services import dedup_verdict_service as verdicts
from pixlstash.services.dedup_tier_service import TierPolicy
from pixlstash.services.dedup_verdict_service import DedupVerdictError


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
    return server.vault.db.run_task(fn, *args, priority=DBPriority.IMMEDIATE)


def _seed(server, specs):
    def insert(session):
        picture_ids = []
        for index, spec in enumerate(specs):
            pic = Picture(
                file_path=spec.get("file_path", f"/vault/pic_{index}.png"),
                format="png",
                width=spec.get("width", 4000),
                height=spec.get("height", 3000),
                size_bytes=spec.get("size_bytes", 1000),
                score=spec.get("score"),
                pixel_sha=spec.get("pixel_sha"),
            )
            session.add(pic)
            session.flush()
            for tag in spec.get("tags", []):
                session.add(Tag(picture_id=int(pic.id), tag=tag))
            picture_ids.append(int(pic.id))
        session.commit()
        return picture_ids

    return _run(server, insert)


def _scan(server, policy=None):
    return _run(server, tiers.run_scan_now_in_session, policy or TierPolicy(), None)


def _one_signature(server) -> str:
    page, _total = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    assert page, "expected at least one unresolved group"
    return page[0]["signature"]


def _picture(server, picture_id: int) -> Picture:
    return _run(server, lambda session: session.get(Picture, picture_id))


def _tags(server, picture_id: int) -> set[str]:
    return _run(
        server,
        lambda session: {
            str(row)
            for row in session.exec(
                select(Tag.tag).where(Tag.picture_id == picture_id)
            ).all()
        },
    )


# ── stack ─────────────────────────────────────────────────────────────────────


def test_stacking_puts_the_chosen_cover_at_position_zero(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100, "score": 1},
            {"pixel_sha": "aaa", "size_bytes": 100, "score": 5},
        ],
    )
    _scan(server)
    signature = _one_signature(server)
    # Override the preselection: the user picks the *lower*-scored picture.
    result = _run(
        server, verdicts.apply_stack_verdict_in_session, signature, ids[0], [], None
    )
    assert result.verdict == VERDICT_STACKED
    assert result.cover_picture_id == ids[0]
    cover = _picture(server, ids[0])
    other = _picture(server, ids[1])
    assert cover.stack_id == other.stack_id == result.stack_id
    assert cover.stack_position == 0
    assert other.stack_position == 1


def test_stacking_defaults_to_the_server_preselection(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100, "score": 1},
            {"pixel_sha": "aaa", "size_bytes": 100, "score": 5},
        ],
    )
    _scan(server)
    result = _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        _one_signature(server),
        None,
        [],
        None,
    )
    # The formula's score term makes the 5-star picture the cover.
    assert result.cover_picture_id == ids[1]


def test_excluded_members_stay_out_and_are_recorded(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    signature = _one_signature(server)
    result = _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        signature,
        None,
        [ids[2]],
        None,
    )
    assert result.excluded_picture_ids == [ids[2]]
    assert _picture(server, ids[2]).stack_id is None
    row = _run(
        server,
        lambda session: session.exec(
            select(DedupVerdict).where(DedupVerdict.signature == signature)
        ).first(),
    )
    assert json.loads(row.excluded_picture_ids) == [ids[2]]


def test_excluding_down_to_one_member_is_rejected(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    with pytest.raises(DedupVerdictError, match="at least two"):
        _run(
            server,
            verdicts.apply_stack_verdict_in_session,
            _one_signature(server),
            None,
            [ids[1]],
            None,
        )


def test_a_cover_outside_the_group_is_rejected(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "bbb", "size_bytes": 100},
        ],
    )
    _scan(server)
    with pytest.raises(DedupVerdictError, match="not an included member"):
        _run(
            server,
            verdicts.apply_stack_verdict_in_session,
            _one_signature(server),
            ids[2],
            [],
            None,
        )


def test_an_unknown_signature_is_rejected(server):
    with pytest.raises(DedupVerdictError, match="No duplicate group"):
        _run(server, verdicts.apply_stack_verdict_in_session, "0" * 64, None, [], None)


# ── the metadata union ────────────────────────────────────────────────────────


def test_stacking_unions_tags_onto_every_member(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100, "tags": ["portrait", "outdoor"]},
            {"pixel_sha": "aaa", "size_bytes": 100, "tags": ["sunset"]},
        ],
    )
    _scan(server)
    result = _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        _one_signature(server),
        None,
        [],
        None,
    )
    assert result.metadata_union["tags_added"] == 3
    expected = {"portrait", "outdoor", "sunset"}
    assert _tags(server, ids[0]) == expected
    assert _tags(server, ids[1]) == expected


def test_the_tag_union_skips_pipeline_sentinels(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100, "tags": ["portrait"]},
            {"pixel_sha": "aaa", "size_bytes": 100, "tags": ["__tag:wd14"]},
        ],
    )
    _scan(server)
    _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        _one_signature(server),
        None,
        [],
        None,
    )
    # The sentinel is a "needs retagging" marker, not user metadata: copying it
    # would re-queue an already-tagged picture for no reason.
    assert _tags(server, ids[0]) == {"portrait"}
    assert _tags(server, ids[1]) == {"__tag:wd14", "portrait"}


def test_stacking_lifts_every_member_to_the_highest_score(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100, "score": 5},
            {"pixel_sha": "aaa", "size_bytes": 100, "score": 1},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    result = _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        _one_signature(server),
        None,
        [],
        None,
    )
    assert result.metadata_union["scores_lifted"] == 2
    assert [_picture(server, pid).score for pid in ids] == [5, 5, 5]


def test_stacking_unions_set_membership(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )

    def add_set(session):
        picture_set = PictureSet(name="Celebrities")
        session.add(picture_set)
        session.commit()
        session.refresh(picture_set)
        session.add(PictureSetMember(set_id=int(picture_set.id), picture_id=ids[0]))
        session.commit()
        return int(picture_set.id)

    set_id = _run(server, add_set)
    _scan(server)
    _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        _one_signature(server),
        None,
        [],
        None,
    )
    members = _run(
        server,
        lambda session: {
            int(row)
            for row in session.exec(
                select(PictureSetMember.picture_id).where(
                    PictureSetMember.set_id == set_id
                )
            ).all()
        },
    )
    # A union can never break an album: the set gained a member, lost none.
    assert members == set(ids)


def test_the_union_is_refused_on_a_locked_set_rather_than_half_applied(server):
    from fastapi import HTTPException

    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100, "tags": ["portrait"]},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )

    def add_locked_set(session):
        picture_set = PictureSet(name="Frozen", locked=True)
        session.add(picture_set)
        session.commit()
        session.refresh(picture_set)
        session.add(PictureSetMember(set_id=int(picture_set.id), picture_id=ids[0]))
        session.commit()

    _run(server, add_locked_set)
    _scan(server)
    with pytest.raises(HTTPException) as excinfo:
        _run(
            server,
            verdicts.apply_stack_verdict_in_session,
            _one_signature(server),
            None,
            [],
            None,
        )
    assert excinfo.value.status_code == 423
    assert _tags(server, ids[1]) == set()


# ── keep separate, and the memory ─────────────────────────────────────────────


def test_keep_separate_changes_no_picture_row(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100, "score": 3},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    result = _run(
        server, verdicts.apply_keep_separate_in_session, _one_signature(server), None
    )
    assert result.verdict == VERDICT_KEEP_SEPARATE
    assert result.stack_id is None
    assert all(_picture(server, pid).stack_id is None for pid in ids)
    assert _picture(server, ids[1]).score is None


def test_a_verdict_survives_a_rescan(server):
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    _run(server, verdicts.apply_keep_separate_in_session, _one_signature(server), None)
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0
    _scan(server)
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0


def test_reopening_returns_the_group_and_keeps_the_history(server):
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    signature = _one_signature(server)
    _run(server, verdicts.apply_keep_separate_in_session, signature, None)
    reopened = _run(server, verdicts.reopen_verdict_in_session, signature)
    assert reopened["previous_verdict"] == VERDICT_KEEP_SEPARATE
    assert reopened["group_returned_to_queue"] is True
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1
    # The row is kept and marked, not deleted: the decision history survives.
    row = _run(
        server,
        lambda session: session.exec(
            select(DedupVerdict).where(DedupVerdict.signature == signature)
        ).first(),
    )
    assert row is not None and row.reopened_at is not None
    with pytest.raises(DedupVerdictError, match="already reopened"):
        _run(server, verdicts.reopen_verdict_in_session, signature)


def test_reopening_a_stack_verdict_does_not_unstack_anything(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    signature = _one_signature(server)
    _run(server, verdicts.apply_stack_verdict_in_session, signature, None, [], None)
    _run(server, verdicts.reopen_verdict_in_session, signature)
    assert _picture(server, ids[0]).stack_id is not None
    assert _picture(server, ids[1]).stack_id is not None


def test_reopening_an_unknown_signature_is_rejected(server):
    with pytest.raises(DedupVerdictError, match="No verdict recorded"):
        _run(server, verdicts.reopen_verdict_in_session, "0" * 64)


# ── bulk auto-stack ───────────────────────────────────────────────────────────


def _seed_two_exact_groups(server):
    return _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "bbb", "size_bytes": 200},
            {"pixel_sha": "bbb", "size_bytes": 200},
        ],
    )


def test_the_dry_run_counts_and_writes_nothing(server):
    ids = _seed_two_exact_groups(server)
    _scan(server)
    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, True, None)
    assert report["dry_run"] is True
    assert report["groups"] == 2
    assert report["pictures"] == 4
    assert report["results"] == []
    assert all(_picture(server, pid).stack_id is None for pid in ids)
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 2


def test_auto_stack_shares_one_batch_id_across_every_group(server):
    ids = _seed_two_exact_groups(server)
    _scan(server)
    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)
    assert report["dry_run"] is False
    assert report["groups"] == 2
    batch_id = report["batch_id"]
    assert batch_id
    assert {item["batch_id"] for item in report["results"]} == {batch_id}
    rows = _run(server, lambda session: session.exec(select(DedupVerdict)).all())
    assert {row.batch_id for row in rows} == {batch_id}
    # Two groups, two stacks, every picture stacked and none deleted.
    stacks = {_picture(server, pid).stack_id for pid in ids}
    assert len(stacks) == 2 and None not in stacks
    assert all(_picture(server, pid).deleted is False for pid in ids)
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0


def test_auto_stack_never_touches_the_near_tier(server):
    """Only tier 1 is bulk-eligible; a near group always goes through the queue."""
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )

    def add_near_group(session):
        pictures = session.exec(select(Picture)).all()
        members = [
            tiers.CandidateMember(id=int(pic.id), width=10, height=10)
            for pic in pictures
        ]
        group = tiers.assemble_group(tiers.DedupTier.NEAR, 0.95, members)
        # A distinct signature so it is a second, near-tier group.
        group.signature = "n" * 64
        tiers.persist_groups_in_session(session, [group])

    _scan(server)
    _run(server, add_near_group)
    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)
    assert report["groups"] == 1
    near_policy = TierPolicy(near_enabled=True)
    assert _run(server, tiers.count_unresolved_in_session, near_policy, None) == 1


def test_auto_stack_respects_a_limit(server):
    _seed_two_exact_groups(server)
    _scan(server)
    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, 1)
    assert report["groups"] == 1
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1


def test_no_verdict_ever_deletes_a_picture(server):
    ids = _seed_two_exact_groups(server)
    _scan(server)
    _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)
    live = _run(
        server,
        lambda session: [int(row) for row in session.exec(select(Picture.id)).all()],
    )
    assert sorted(live) == sorted(ids)
    assert all(_picture(server, pid).deleted is False for pid in ids)
