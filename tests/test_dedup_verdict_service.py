"""Unit tests for applying and remembering duplicate verdicts.

Covers:

* **stack** - members land in one stack led by the chosen cover, excluded members
  are untouched, and the metadata union runs (tags, sets, score);
* **keep separate** - no picture row changes, and the decision survives a rescan;
* **reopen** - the group comes back and the decision history is kept;
* **bulk auto-stack** - exact tier only, one batch id across the whole run, and
  the dry run writes nothing;
* **the operation log (§21)** - one verdict is exactly one row (no double-record
  through `routes/stacks.py`), undo reverses the stacking *and* the metadata
  union, the snapshot covers stack siblings the group never named, and a whole
  bulk run reverses with a single batch undo;
* **the non-destructive invariant** - no verdict deletes a picture, ever;
* **locked sets** - the metadata union is refused rather than half-applied.
"""

import gc
import json
import os
import sqlite3
import tempfile

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Picture,
    PictureSet,
    PictureSetMember,
    PictureStack,
)
from pixlstash.db_models.dedup import (
    DedupGroup,
    VERDICT_KEEP_SEPARATE,
    VERDICT_STACKED,
    DedupVerdict,
)
from pixlstash.db_models.operation import Operation
from pixlstash.db_models.tag import Tag
from pixlstash.server import Server
from pixlstash.services import dedup_tier_service as tiers
from pixlstash.services import dedup_verdict_service as verdicts
from pixlstash.services import operation_log_service
from pixlstash.services import set_lock_service as set_locks
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
        srv.close()
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
                smart_score=spec.get("smart_score"),
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
    page, _total, _cursor = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    assert page, "expected at least one unresolved group"
    return page[0]["signature"]


def _only_signature(server) -> str:
    """The one group's signature, read straight from the table.

    ``_one_signature`` goes through the queue page, which withholds a group with
    fewer than two stackable members. A withheld group is still a real row with a
    real signature, and a client holding a stale page can still POST it, so the
    refusal paths below have to be reachable in a test without the queue's help.
    """
    rows = _run(
        server,
        lambda session: [
            str(sig) for sig in session.exec(select(DedupGroup.signature)).all()
        ],
    )
    assert len(rows) == 1, f"expected exactly one group, got {rows}"
    return rows[0]


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
    # Equal quality and size tiers (no smart scores, same pixels): the star
    # tier of the ranking makes the 5-star picture the cover.
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
            _only_signature(server),
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


def test_reopening_a_stack_verdict_dissolves_the_verdicts_stack(server):
    """Owner override, 2026-07-30: "Clear decision" must return the group.

    The old pin here asserted the opposite (reopen left the stack standing,
    "unstacking is the Stacks view's own action"), which is exactly what made
    a cleared group vanish forever: the queue's live filter requires two stack
    units. See the dedicated clear-decision section at the end of this file
    for the full behaviour matrix.
    """
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
    assert _picture(server, ids[0]).stack_id is None
    assert _picture(server, ids[1]).stack_id is None


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
    """Only tier 1 is bulk-eligible; a near group always goes through the queue.

    The near group holds two pictures of ITS OWN (no overlap with the exact
    pair): a near group sharing the exact pair's members would stop posing a
    decision the moment auto-stack stacked them - the pending-decision filter's
    stack-units rule - which is correct but proves nothing about the near tier.
    """
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "bbb", "size_bytes": 200},
            {"pixel_sha": "ccc", "size_bytes": 300},
        ],
    )

    def add_near_group(session):
        pictures = session.exec(
            select(Picture).where(Picture.pixel_sha.in_(["bbb", "ccc"]))
        ).all()
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
    # And its members are untouched: no stack, no verdict, still unresolved.
    stacked = _run(
        server,
        lambda session: [
            pic.stack_id
            for pic in session.exec(
                select(Picture).where(Picture.pixel_sha.in_(["bbb", "ccc"]))
            ).all()
        ],
    )
    assert stacked == [None, None]


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


# ── operation log integration (§21) ───────────────────────────────────────────


def _operations(server) -> list:
    return _run(
        server,
        lambda session: list(
            session.exec(select(Operation).order_by(Operation.id)).all()
        ),
    )


def test_a_stack_verdict_records_exactly_one_operation(server):
    """One verdict, one row. The verdict path must not double-record.

    ``routes/stacks.py`` wraps itself in ``run_recorded_metadata_task``; this
    module deliberately stacks in-session instead and records once around the
    whole verdict, so a second row here would mean two Ctrl+Z presses to undo
    one decision.
    """
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    batch_id = verdicts.new_batch_id()
    _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        _one_signature(server),
        None,
        [],
        batch_id,
    )
    rows = _operations(server)
    assert len(rows) == 1
    assert rows[0].op_type == verdicts.OP_TYPE_STACK
    assert rows[0].batch_id == batch_id
    assert rows[0].undoable is True
    assert "Stacked 2 duplicates" in (rows[0].summary or "")


def test_record_failure_rolls_back_the_whole_dedup_verdict(server, monkeypatch):
    """Stack pointers, verdict state and receipt share one transaction."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    signature = _one_signature(server)
    monkeypatch.setattr(
        operation_log_service,
        "record_operation_in_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("dedup receipt failed")
        ),
    )

    with pytest.raises(RuntimeError, match="dedup receipt failed"):
        _run(
            server,
            verdicts.apply_stack_verdict_in_session,
            signature,
            None,
            [],
            None,
        )

    assert all(_picture(server, picture_id).stack_id is None for picture_id in ids)
    verdict_rows = _run(
        server,
        lambda session: list(
            session.exec(
                select(DedupVerdict).where(DedupVerdict.signature == signature)
            ).all()
        ),
    )
    assert verdict_rows == []
    assert _operations(server) == []


def test_undoing_a_stack_verdict_reverses_the_stacking(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
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
    assert _picture(server, ids[0]).stack_id is not None

    _run(server, operation_log_service.undo_in_session, None)
    # The recorded `stack` facet is written back, so both pictures leave the
    # stack neither of them was in before the verdict.
    assert _picture(server, ids[0]).stack_id is None
    assert _picture(server, ids[1]).stack_id is None
    rows = _operations(server)
    assert len(rows) == 1 and rows[0].status == "undone"


def test_undoing_a_stack_verdict_reverses_the_metadata_union(server):
    """The union happens inside the snapshot, so undo restores tags and scores."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100, "score": 5, "tags": ["portrait"]},
            {"pixel_sha": "aaa", "size_bytes": 100},
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
    assert _tags(server, ids[1]) == {"portrait"}
    assert _picture(server, ids[1]).score == 5

    _run(server, operation_log_service.undo_in_session, None)
    assert _tags(server, ids[1]) == set()
    assert _picture(server, ids[1]).score is None
    # The picture that already carried them keeps them.
    assert _tags(server, ids[0]) == {"portrait"}
    assert _picture(server, ids[0]).score == 5


def test_the_snapshot_covers_stack_siblings_the_group_never_named(server):
    """Folding a second stack in must be fully reversible (§21 ``expand_stacks``).

    The duplicate pair is 0 and 1. Picture 0 sits in stack A with sibling 2;
    picture 1 sits in stack B with sibling 3. The verdict's cover is 0, so stack
    B is **folded into A** and sibling 3 - which the group never named - is
    reparented. An undo that snapshotted only the group's own members would
    leave 3 stranded in A with B gone.

    Verified non-vacuous: with the snapshot narrowed back to ``included``, this
    test fails on the sibling assertion.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "yyy", "size_bytes": 800},
            {"pixel_sha": "zzz", "size_bytes": 900},
        ],
    )

    def pre_stack(session):
        created = []
        for name, members in (("A", [ids[0], ids[2]]), ("B", [ids[1], ids[3]])):
            stack = PictureStack(name=name)
            session.add(stack)
            session.commit()
            session.refresh(stack)
            for position, picture_id in enumerate(members):
                pic = session.get(Picture, picture_id)
                pic.stack_id = int(stack.id)
                pic.stack_position = position
                session.add(pic)
            created.append(int(stack.id))
        session.commit()
        return created

    stack_a, stack_b = _run(server, pre_stack)
    _scan(server)
    _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        _one_signature(server),
        ids[0],
        [],
        None,
    )
    # Stack B was folded into A: all four pictures, sibling 3 included, now
    # share one stack, and B is gone.
    assert {_picture(server, pid).stack_id for pid in ids} == {stack_a}
    assert _run(server, lambda session: session.get(PictureStack, stack_b)) is None

    _run(server, operation_log_service.undo_in_session, None)
    assert _picture(server, ids[0]).stack_id == stack_a
    assert _picture(server, ids[2]).stack_id == stack_a
    # The sibling the group never named is returned to its own stack, which the
    # recorded `stack` facet recreates by name.
    assert _picture(server, ids[1]).stack_id == _picture(server, ids[3]).stack_id
    assert _picture(server, ids[1]).stack_id != stack_a
    assert _picture(server, ids[1]).stack_id is not None


def test_bulk_auto_stack_reverses_with_one_batch_undo(server):
    ids = _seed_two_exact_groups(server)
    _scan(server)
    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)
    batch_id = report["batch_id"]
    assert report["groups"] == 2
    rows = _operations(server)
    # Two groups, two rows, ONE batch id.
    assert len(rows) == 2
    assert {row.batch_id for row in rows} == {batch_id}
    assert all(_picture(server, pid).stack_id is not None for pid in ids)

    _run(server, operation_log_service.undo_batch_in_session, batch_id)
    # A single call reversed every stack in the run.
    assert all(_picture(server, pid).stack_id is None for pid in ids)
    assert {row.status for row in _operations(server)} == {"undone"}


def test_undoing_any_member_of_the_batch_reverts_the_whole_run(server):
    """Batch semantics: a partially-undone bulk action cannot exist."""
    ids = _seed_two_exact_groups(server)
    _scan(server)
    _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)
    first_id = _operations(server)[0].id

    _run(server, operation_log_service.undo_in_session, first_id)
    assert all(_picture(server, pid).stack_id is None for pid in ids)
    assert {row.status for row in _operations(server)} == {"undone"}


def test_keep_separate_records_exactly_one_operation(server):
    """Keep-separate is op-logged since 2026-07-30 (owner override of #644 CSO).

    It changes no picture facet, so the row goes through the empty-diff path:
    empty before/after payloads, the member ids as targets, and the batch id
    stored on the verdict row - the correlation the post-restore hook needs.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    signature = _one_signature(server)
    result = _run(server, verdicts.apply_keep_separate_in_session, signature, None)
    rows = _operations(server)
    assert len(rows) == 1
    assert rows[0].op_type == verdicts.OP_TYPE_KEEP_SEPARATE
    assert rows[0].undoable is True
    assert rows[0].batch_id == result.batch_id
    assert result.batch_id, "a batch id is minted when the caller supplies none"
    assert json.loads(rows[0].target_ids) == sorted(ids)
    assert json.loads(rows[0].before_state) == {}
    assert json.loads(rows[0].after_state) == {}
    assert "Kept 2 pictures separate" in (rows[0].summary or "")
    verdict_row = _run(
        server,
        lambda session: session.exec(
            select(DedupVerdict).where(DedupVerdict.signature == signature)
        ).first(),
    )
    assert verdict_row.batch_id == result.batch_id


def test_reopen_records_no_operation_when_it_touches_no_pictures(server):
    """A clear that mutates nothing stays out of the log.

    Keep-separate never touched a picture, so its clear is pure verdict
    memory: recording it would make undo-of-clear a second, confusing way to
    re-decide the group while there is nothing to restore. (A clear that DOES
    unstack records one operation - pinned in the clear-decision section.)
    """
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
    before = len(_operations(server))
    _run(server, verdicts.reopen_verdict_in_session, signature)
    assert len(_operations(server)) == before


def test_undoing_a_keep_separate_reopens_the_group_and_redo_re_resolves(server):
    """Both directions: undo returns the group to the queue, redo re-decides it.

    The pictures are untouched in every direction - keep-separate never had a
    picture facet to restore; the verdict row and the group's resolved flag are
    the whole reversible state, carried by the post-restore hook.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100, "score": 3},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    signature = _one_signature(server)
    _run(server, verdicts.apply_keep_separate_in_session, signature, None)
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0

    _run(server, operation_log_service.undo_in_session, None)
    # The group is queue-visible again and the verdict row is kept, reopened.
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1
    row = _run(
        server,
        lambda session: session.exec(
            select(DedupVerdict).where(DedupVerdict.signature == signature)
        ).first(),
    )
    assert row is not None and row.reopened_at is not None
    assert all(_picture(server, pid).stack_id is None for pid in ids)
    assert _picture(server, ids[0]).score == 3

    _run(server, operation_log_service.redo_in_session)
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0
    row = _run(
        server,
        lambda session: session.exec(
            select(DedupVerdict).where(DedupVerdict.signature == signature)
        ).first(),
    )
    assert row.reopened_at is None
    assert all(_picture(server, pid).stack_id is None for pid in ids)


def test_batch_undo_restores_a_mixed_stack_and_keep_separate_gesture(server):
    """One gesture batch spanning both verdict kinds reverses as one undo.

    Each hook is scoped to its own verdict kind, so the stack hook restores the
    stacked group and the keep-separate hook restores the kept-separate one -
    both explicitly, through their own operations, in a single batch undo.
    """
    ids = _seed_two_exact_groups(server)
    _scan(server)
    page, _total, _cursor = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    signatures = sorted(group["signature"] for group in page)
    assert len(signatures) == 2
    gesture = verdicts.new_batch_id()
    _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        signatures[0],
        None,
        [],
        gesture,
    )
    _run(server, verdicts.apply_keep_separate_in_session, signatures[1], gesture)
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0
    rows = _operations(server)
    assert {row.op_type for row in rows} == {
        verdicts.OP_TYPE_STACK,
        verdicts.OP_TYPE_KEEP_SEPARATE,
    }
    assert {row.batch_id for row in rows} == {gesture}

    _run(server, operation_log_service.undo_batch_in_session, gesture)
    # Both kinds restored: the stack reversed, both groups back in the queue.
    assert all(_picture(server, pid).stack_id is None for pid in ids)
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 2
    reopened = _run(
        server,
        lambda session: [
            row.reopened_at is not None
            for row in session.exec(select(DedupVerdict)).all()
        ],
    )
    assert reopened == [True, True]


# ── R2: the bulk path must never lose its undo handle ─────────────────────────


def _lock_picture_in_a_set(server, picture_id: int, name: str = "Frozen") -> None:
    def add_locked_set(session):
        picture_set = PictureSet(name=name, locked=True)
        session.add(picture_set)
        session.commit()
        session.refresh(picture_set)
        session.add(PictureSetMember(set_id=int(picture_set.id), picture_id=picture_id))
        session.commit()

    _run(server, add_locked_set)


def _seed_three_exact_groups(server):
    return _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "bbb", "size_bytes": 200},
            {"pixel_sha": "bbb", "size_bytes": 200},
            {"pixel_sha": "ccc", "size_bytes": 300},
            {"pixel_sha": "ccc", "size_bytes": 300},
        ],
    )


def test_a_locked_group_is_never_planned_into_a_bulk_run(server):
    """The run no longer plans work it would only refuse.

    Was the CSO's B2 fixture, when a locked group reached the loop and 423'd out
    of it. Since the queue withholds a group with fewer than two stackable
    members (owner call, 2026-07-30), auto-stack applies the same filter and the
    group never enters the run at all. The B2 invariant itself - an
    ``HTTPException`` mid-run must not abort - is pinned directly by
    :func:`test_an_http_exception_mid_run_does_not_abort_the_bulk_run`, which does
    not depend on a lock to raise one.
    """
    ids = _seed_three_exact_groups(server)
    _lock_picture_in_a_set(server, ids[2])  # a member of the middle group
    _scan(server)

    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)

    assert report["batch_id"]
    assert report["groups"] == 2
    assert report["blocked"] == 0, "the group was filtered out, not refused"
    assert report["failed"] == 0
    assert report["failures"] == []

    # The locked group is untouched, and it is withheld from the queue too, so
    # the badge and the run agree about what is left to do.
    assert _picture(server, ids[2]).stack_id is None
    assert _picture(server, ids[3]).stack_id is None
    assert _picture(server, ids[0]).stack_id is not None
    assert _picture(server, ids[4]).stack_id is not None
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0


def test_an_http_exception_mid_run_does_not_abort_the_bulk_run(server, monkeypatch):
    """Regression for the CSO's B2, pinned on the mechanism rather than a lock.

    The locked-set guards raise ``HTTPException(423)``, not
    ``DedupVerdictError``. Catching only the latter meant a refusal in the middle
    of a bulk run propagated out **after** earlier groups had already committed:
    a partially applied bulk mutation whose server-minted batch id the caller
    never received, i.e. work that happened with no undo handle in the response.

    The refusal is injected here so the test keeps pinning that ``except`` clause
    however the lock filters change upstream.
    """
    ids = _seed_three_exact_groups(server)
    _scan(server)

    real = verdicts.apply_stack_verdict_in_session
    seen: list[str] = []

    def refuse_the_second(session, signature, *args, **kwargs):
        seen.append(signature)
        if len(seen) == 2:
            raise HTTPException(
                status_code=423,
                detail={"code": "set_locked", "action": "stack duplicates together"},
            )
        return real(session, signature, *args, **kwargs)

    monkeypatch.setattr(verdicts, "apply_stack_verdict_in_session", refuse_the_second)
    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)

    # The run completed rather than raising, and handed back its undo handle.
    assert report["batch_id"]
    assert report["dry_run"] is False
    assert report["groups"] == 2
    assert report["blocked"] == 1
    assert report["failed"] == 0
    assert {r["outcome"] for r in report["results"]} == {verdicts.BULK_REASON_APPLIED}
    assert len(report["failures"]) == 1
    failure = report["failures"][0]
    assert failure["outcome"] == verdicts.BULK_REASON_BLOCKED
    assert failure["status_code"] == 423
    assert failure["error"]["code"] == "set_locked"
    # Every group was considered; the refused one committed nothing.
    assert len(seen) == 3
    assert sum(1 for pid in ids if _picture(server, pid).stack_id is not None) == 4


def test_the_returned_batch_id_reverses_exactly_the_applied_groups(server):
    """The undo handle from a partial run must work, and must not over-reach."""
    ids = _seed_three_exact_groups(server)
    _lock_picture_in_a_set(server, ids[2])
    _scan(server)
    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)
    batch_id = report["batch_id"]

    rows = _operations(server)
    assert len(rows) == 2
    assert {row.batch_id for row in rows} == {batch_id}

    _run(server, operation_log_service.undo_batch_in_session, batch_id)
    assert all(_picture(server, pid).stack_id is None for pid in ids)
    assert {row.status for row in _operations(server)} == {"undone"}


def test_a_blocked_group_leaves_no_partial_write_of_its_own(server):
    """The skipped iteration is rolled back, not carried into the next commit."""
    ids = _seed_three_exact_groups(server)
    _lock_picture_in_a_set(server, ids[2])
    _scan(server)
    _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)

    # No stack row was left behind for the blocked group (_stack_members flushes
    # a PictureStack before the lock guard runs), and no verdict was recorded.
    stack_ids = {
        _picture(server, pid).stack_id for pid in ids if _picture(server, pid).stack_id
    }
    assert len(stack_ids) == 2
    stacks = _run(server, lambda session: session.exec(select(PictureStack)).all())
    assert len(stacks) == 2
    signatures = _run(
        server,
        lambda session: [
            row.signature for row in session.exec(select(DedupVerdict)).all()
        ],
    )
    assert len(signatures) == 2


# ── R3: §21 origin discipline on the recording routes ─────────────────────────


def test_a_stack_verdict_records_the_actor_and_origin(server):
    """The service must carry through what the handler read from the request.

    §21 is explicit that actor / source / origin_client_id come from the request,
    in the handler, and are passed down - the contextvar is dead on the DB worker
    thread. Before this, every dedup operation recorded `actor=None,
    source="external"`, degrading the audit trail for the most far-reaching
    mutation on the surface.
    """
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
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
        "42",
        "ui",
        "tab-abc",
    )
    row = _operations(server)[0]
    assert row.actor == "42"
    assert row.source == "ui"
    assert row.origin_client_id == "tab-abc"


def test_bulk_auto_stack_attributes_every_row_in_the_batch(server):
    _seed_two_exact_groups(server)
    _scan(server)
    _run(
        server,
        verdicts.bulk_auto_stack_in_session,
        None,
        None,
        False,
        None,
        "42",
        "ui",
        "tab-abc",
    )
    rows = _operations(server)
    assert len(rows) == 2
    assert {row.actor for row in rows} == {"42"}
    assert {row.source for row in rows} == {"ui"}
    assert {row.origin_client_id for row in rows} == {"tab-abc"}


# ── R7: the scrapheaped-sibling snapshot flag is load-bearing ─────────────────


def test_undo_restores_a_scrapheaped_stack_siblings_position(server):
    """Regression for the CSO's C1 - pins ``include_deleted=True``.

    ``normalize_stack_positions`` renumbers **every** member of an affected
    stack, soft-deleted ones included (§21.1). If the undo snapshot expanded the
    stack without ``include_deleted=True``, the scrapheaped sibling's renumbered
    position would be an unrecorded change that undo could not reverse - and the
    whole suite stayed green without it.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "zzz", "size_bytes": 900},
        ],
    )

    def pre_stack(session):
        stack = PictureStack(name="with-a-scrapheaped-member")
        session.add(stack)
        session.commit()
        session.refresh(stack)
        live = session.get(Picture, ids[1])
        live.stack_id = int(stack.id)
        live.stack_position = 0
        session.add(live)
        buried = session.get(Picture, ids[2])
        buried.stack_id = int(stack.id)
        buried.stack_position = 5
        buried.deleted = True
        session.add(buried)
        session.commit()

    _run(server, pre_stack)
    _scan(server)
    _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        _one_signature(server),
        None,
        [],
        None,
    )
    # The verdict renumbered the scrapheaped sibling along with everyone else.
    assert _picture(server, ids[2]).stack_position != 5

    _run(server, operation_log_service.undo_in_session, None)
    buried = _picture(server, ids[2])
    assert buried.stack_position == 5
    assert buried.deleted is True


# ── addendum: the auto-stack dry-run consent aggregates ───────────────────────


def test_the_dry_run_summary_counts_covers_that_gain_metadata(server):
    """The design's consent dialog promises a "covers gaining metadata" row.

    Derived from the planned verdicts in the dry run's own snapshot - the union
    is never executed, and nothing is written.
    """
    ids = _seed(
        server,
        [
            # Group 1: the cover (highest score) gains a tag from its twin.
            {"pixel_sha": "aaa", "size_bytes": 100, "score": 5},
            {"pixel_sha": "aaa", "size_bytes": 100, "tags": ["portrait"]},
            # Group 2: the cover already has everything, so it gains nothing.
            {"pixel_sha": "bbb", "size_bytes": 200, "score": 5, "tags": ["sunset"]},
            {"pixel_sha": "bbb", "size_bytes": 200},
        ],
    )
    _scan(server)
    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, True, None)
    summary = report["dry_run_summary"]
    assert summary["groups"] == 2
    assert summary["groups_by_tier"] == {"exact": 2, "near": 0, "embedding": 0}
    assert summary["pictures"] == 4
    assert summary["covers_gaining_tags"] == 1
    assert summary["covers_gaining_metadata"] == 1
    # Aggregates agree with the top-level counts from the same snapshot.
    assert summary["groups"] == report["groups"]
    assert summary["pictures"] == report["pictures"]
    # Still a dry run: nothing written, nothing tagged.
    assert all(_picture(server, pid).stack_id is None for pid in ids)
    assert _tags(server, ids[0]) == set()


def test_the_dry_run_summary_counts_a_score_lift(server):
    _seed(
        server,
        [
            # The cover wins on smart score (the ranking's dominant tier), but
            # a twin outranks it on human stars, so the union would lift the
            # cover's score.
            {"pixel_sha": "aaa", "size_bytes": 100, "smart_score": 4.5},
            {"pixel_sha": "aaa", "size_bytes": 100, "score": 5, "smart_score": 2.0},
        ],
    )
    _scan(server)
    summary = _run(server, verdicts.bulk_auto_stack_in_session, None, None, True, None)[
        "dry_run_summary"
    ]
    assert summary["covers_gaining_score"] == 1
    assert summary["covers_gaining_metadata"] == 1


def test_an_applied_run_carries_no_dry_run_summary(server):
    _seed_two_exact_groups(server)
    _scan(server)
    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)
    assert "dry_run_summary" not in report


def test_a_locked_co_member_of_a_folded_stack_is_refused(server):
    """The lock guard must run BEFORE the fold, and expand through it.

    ``apply_metadata_union_in_session`` only checks the group's own members, so
    the co-members that ``_stack_members`` drags in when it folds another stack
    are covered solely by ``enforce_stack_membership_not_locked`` running first
    and expanding through ``expand_picture_ids_to_stacks``. That ordering is
    load-bearing: move the lock check after the fold and a locked picture gets
    silently reparented. Pins the CSO's B6b probe.
    """
    from fastapi import HTTPException

    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "zzz", "size_bytes": 900},
        ],
    )

    def pre_stack_and_lock(session):
        # Picture 2 (a group member) shares a stack with picture 3, which is NOT
        # in the group and is the one frozen by the locked set.
        stack = PictureStack(name="folds-in")
        session.add(stack)
        session.commit()
        session.refresh(stack)
        for position, picture_id in enumerate([ids[1], ids[2]]):
            pic = session.get(Picture, picture_id)
            pic.stack_id = int(stack.id)
            pic.stack_position = position
            session.add(pic)
        picture_set = PictureSet(name="Frozen", locked=True)
        session.add(picture_set)
        session.commit()
        session.refresh(picture_set)
        session.add(PictureSetMember(set_id=int(picture_set.id), picture_id=ids[2]))
        session.commit()
        return int(stack.id)

    original_stack = _run(server, pre_stack_and_lock)
    _scan(server)
    with pytest.raises(HTTPException) as excinfo:
        _run(
            server,
            verdicts.apply_stack_verdict_in_session,
            _only_signature(server),
            ids[0],
            [],
            None,
        )
    assert excinfo.value.status_code == 423
    # Nothing moved: the group member outside the stack is still unstacked and
    # the folded stack is intact.
    assert _picture(server, ids[0]).stack_id is None
    assert _picture(server, ids[1]).stack_id == original_stack
    assert _picture(server, ids[2]).stack_id == original_stack
    assert _operations(server) == []


# ── partial success across a locked-set boundary ──────────────────────────────
#
# A locked set freezes its membership and a stack reconciles to the union of its
# members' sets, so a group straddling a locked-set boundary has no legal
# whole-group stack. The dedup queue answers that with partial success (stack the
# side that can be stacked, report the rest) rather than the whole-group refusal
# the manual /stacks routes still use, because a triage queue that dead-ends on
# one frozen member costs the user the decision about all the others.


def _set_member_ids(server, set_id: int) -> set[int]:
    return _run(
        server,
        lambda session: {
            int(pid)
            for pid in session.exec(
                select(PictureSetMember.picture_id).where(
                    PictureSetMember.set_id == set_id
                )
            ).all()
        },
    )


def _lock_set(server, name: str, picture_ids) -> int:
    """Create a locked set holding *picture_ids* and return its id."""

    def create(session):
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

    return _run(server, create)


def test_a_group_straddling_a_locked_set_stacks_the_unlocked_side(server):
    """The two loose members stack; the frozen one is skipped, not fatal."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    set_id = _lock_set(server, "Evaluation Set", [ids[2]])
    _scan(server)

    result = _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        _one_signature(server),
        ids[0],
        [],
        None,
    )

    assert result.picture_ids == [ids[0], ids[1]]
    assert result.skipped == [
        {
            "picture_id": ids[2],
            "reason": "set_locked",
            "sets": [{"id": set_id, "name": "Evaluation Set"}],
        }
    ]
    # Recorded as an exclusion, so a rescan does not read the group as undecided.
    assert result.excluded_picture_ids == [ids[2]]
    stack_id = _picture(server, ids[0]).stack_id
    assert stack_id is not None
    assert _picture(server, ids[1]).stack_id == stack_id
    # The frozen member is untouched, and the locked set did not gain a member.
    assert _picture(server, ids[2]).stack_id is None
    assert _set_member_ids(server, set_id) == {ids[2]}


def test_a_group_wholly_inside_one_locked_set_cannot_stack(server):
    """Two gates, and the dedup path has to satisfy the tighter one.

    ``enforce_stack_membership_not_locked`` alone would allow this: the set
    already contains every resulting member, so it gains no row. But the stack
    verdict also runs the metadata union, which writes tags and lifts scores and
    therefore refuses *any* frozen member. So the honest answer for a wholly
    frozen group is "no legal stack", and the partition has to say so up front
    rather than letting the union refuse halfway through.
    """
    from fastapi import HTTPException

    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    set_id = _lock_set(server, "All Frozen", ids)
    _scan(server)

    with pytest.raises(HTTPException) as excinfo:
        _run(
            server,
            verdicts.apply_stack_verdict_in_session,
            _only_signature(server),
            ids[0],
            [],
            None,
        )

    assert excinfo.value.status_code == 423
    assert excinfo.value.detail["code"] == "set_locked"
    assert excinfo.value.detail["picture_ids"] == sorted(ids)
    # Refused up front: no stack row, no partial union, no verdict.
    assert all(_picture(server, pid).stack_id is None for pid in ids)
    assert _set_member_ids(server, set_id) == set(ids)
    assert _operations(server) == []


def test_a_pair_split_by_a_locked_set_is_refused_and_names_the_pictures(server):
    """Nothing legal is left, so there is no partial success to report.

    The 423 carries `picture_ids` as well as `sets`: without it the client can
    name the set but cannot mark the thumbnail it belongs to.
    """
    from fastapi import HTTPException

    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    set_id = _lock_set(server, "Evaluation Set", [ids[1]])
    _scan(server)

    with pytest.raises(HTTPException) as excinfo:
        _run(
            server,
            verdicts.apply_stack_verdict_in_session,
            _only_signature(server),
            ids[0],
            [],
            None,
        )

    assert excinfo.value.status_code == 423
    detail = excinfo.value.detail
    assert detail["code"] == "set_locked"
    assert detail["sets"] == [{"id": set_id, "name": "Evaluation Set"}]
    assert detail["picture_ids"] == [ids[1]]
    # Nothing was written, and no verdict was recorded.
    assert _picture(server, ids[0]).stack_id is None
    assert _picture(server, ids[1]).stack_id is None
    assert _operations(server) == []


def test_members_in_two_different_locked_sets_cannot_stack(server):
    """Each set would gain the other's member, so no pair is legal."""
    from fastapi import HTTPException

    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    left = _lock_set(server, "Left", [ids[0]])
    right = _lock_set(server, "Right", [ids[1]])
    _scan(server)

    with pytest.raises(HTTPException) as excinfo:
        _run(
            server,
            verdicts.apply_stack_verdict_in_session,
            _only_signature(server),
            ids[0],
            [],
            None,
        )

    assert excinfo.value.status_code == 423
    assert [s["id"] for s in excinfo.value.detail["sets"]] == sorted([left, right])
    assert _set_member_ids(server, left) == {ids[0]}
    assert _set_member_ids(server, right) == {ids[1]}


def test_the_cover_moves_off_a_skipped_member(server):
    """A cover that turns out to be frozen does not sink the whole verdict.

    The client normally moves the cover before Stack is ever pressed; this is the
    stale-client path, where the set was locked after the page was loaded.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _lock_set(server, "Evaluation Set", [ids[0]])
    _scan(server)

    result = _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        _one_signature(server),
        ids[0],
        [],
        None,
    )

    assert result.cover_picture_id in (ids[1], ids[2])
    assert sorted(result.picture_ids) == sorted([ids[1], ids[2]])
    assert [entry["picture_id"] for entry in result.skipped] == [ids[0]]
    # The chosen cover really did lead the stack.
    cover = _picture(server, result.cover_picture_id)
    assert cover.stack_position == 0


def test_the_queue_marks_a_locked_member_as_unstackable(server):
    """The listing hides nothing: the frozen member is still a row, marked."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    set_id = _lock_set(server, "Evaluation Set", [ids[2]])
    _scan(server)

    page, _total, _cursor = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    group = page[0]
    by_id = {c["picture_id"]: c for c in group["candidates"]}

    assert sorted(by_id) == sorted(ids), "no member is filtered out of the listing"
    assert by_id[ids[0]]["stackable"] is True
    assert by_id[ids[0]]["blocked_by_sets"] == []
    assert by_id[ids[2]]["stackable"] is False
    assert by_id[ids[2]]["blocked_by_sets"] == [
        {"id": set_id, "name": "Evaluation Set"}
    ]
    # The preselected cover is one the user can actually stack.
    assert group["cover_picture_id"] in (ids[0], ids[1])


def test_a_wholly_frozen_group_is_withheld_from_the_queue(server):
    """No stackable decision is left in it, so the queue does not offer it.

    Superseded the earlier "listed but every member marked" expectation when the
    owner asked for the group to be withheld outright (2026-07-30). The group row
    survives, so unlocking the set brings it straight back.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _lock_set(server, "All Frozen", ids)
    _scan(server)

    page, total, _cursor = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    assert page == []
    assert total == 0
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0


# ── adversarial probes (independent review of 613a08b3) ──────────────────────
#
# Written by the security reviewer, not by the author of the partition, per the
# CLAUDE.md rule that a security fix is not certified by whoever wrote it. Each
# one is a shape the author's own tests did not cover.


def test_probe_frozen_only_via_stack_sibling_is_not_laundered_into_the_stack(server):
    """The divergence case: a group member frozen ONLY through a stack sibling.

    The partition asks locked_sets_for_pictures (input-keyed, rolled up to the
    stack); the union's own gate asks _locked_sets_by_picture over the expansion.
    If those two ever disagree, THIS is the shape that finds it: pic B is not
    itself in the locked set, its stack sibling C is, and C is not in the group.
    Three members so the partition has a legal 2-picture stack to fall back to,
    which is exactly when it would be tempted to admit B.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},  # A, clean
            {"pixel_sha": "aaa", "size_bytes": 100},  # B, stacked with C
            {"pixel_sha": "aaa", "size_bytes": 100},  # D, clean
            {"pixel_sha": "zzz", "size_bytes": 900},  # C, the locked one
        ],
    )

    def pre(session):
        stack = PictureStack(name="sibling")
        session.add(stack)
        session.commit()
        session.refresh(stack)
        for position, pid in enumerate([ids[1], ids[3]]):
            pic = session.get(Picture, pid)
            pic.stack_id = int(stack.id)
            pic.stack_position = position
            session.add(pic)
        s = PictureSet(name="Frozen", locked=True)
        session.add(s)
        session.commit()
        session.refresh(s)
        session.add(PictureSetMember(set_id=int(s.id), picture_id=ids[3]))
        session.commit()
        return int(stack.id), int(s.id)

    original_stack, set_id = _run(server, pre)
    _scan(server)

    result = _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        _one_signature(server),
        ids[0],
        [],
        None,
    )

    # B must be skipped: stacking it would drag C into the new stack.
    assert [e["picture_id"] for e in result.skipped] == [ids[1]], result.skipped
    assert sorted(result.picture_ids) == sorted([ids[0], ids[2]])
    # C never moved and the locked set never grew.
    assert _picture(server, ids[3]).stack_id == original_stack
    assert _picture(server, ids[1]).stack_id == original_stack
    assert _set_member_ids(server, set_id) == {ids[3]}


def test_probe_repeated_stack_cannot_launder_a_skipped_member(server):
    """A second verdict on the same signature must not pick up the skipped one."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    set_id = _lock_set(server, "Frozen", [ids[2]])
    _scan(server)
    signature = _one_signature(server)

    _run(server, verdicts.apply_stack_verdict_in_session, signature, ids[0], [], None)
    # Replay the same verdict, this time naming the FROZEN picture as the cover.
    # It succeeds as another partial success (the cover is moved), which is the
    # documented behaviour; what matters is that the replay cannot be used to
    # walk the frozen member in through the cover argument.
    again = _run(
        server, verdicts.apply_stack_verdict_in_session, signature, ids[2], [], None
    )
    assert [e["picture_id"] for e in again.skipped] == [ids[2]]
    assert again.cover_picture_id != ids[2]
    assert ids[2] not in again.picture_ids
    assert _picture(server, ids[2]).stack_id is None
    assert _set_member_ids(server, set_id) == {ids[2]}


def test_probe_reopen_cannot_reparent_the_skipped_member(server):
    """Undoing a partial stack must not drag the frozen member into anything.

    The skipped ids are recorded as `excluded_picture_ids` on the verdict, and a
    reopen dissolves the verdict's stack from the recorded pre-verdict state. If
    the skipped member had leaked into the undo snapshot, this is where it would
    surface as a write to a frozen picture.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    set_id = _lock_set(server, "Frozen", [ids[2]])
    _scan(server)
    signature = _one_signature(server)

    _run(server, verdicts.apply_stack_verdict_in_session, signature, ids[0], [], None)
    _run(
        server,
        verdicts.reopen_verdict_in_session,
        signature,
        None,
        None,
        "external",
        None,
    )

    assert _picture(server, ids[2]).stack_id is None
    assert _set_member_ids(server, set_id) == {ids[2]}


def test_probe_bulk_auto_stack_never_grows_a_locked_set(server):
    """The most far-reaching mutation still cannot touch a locked set."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "bbb", "size_bytes": 200},
            {"pixel_sha": "bbb", "size_bytes": 200},
        ],
    )
    set_id = _lock_set(server, "Frozen", [ids[2], ids[3]])
    _scan(server)

    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)

    assert _set_member_ids(server, set_id) == {ids[2], ids[3]}
    assert _picture(server, ids[2]).stack_id is None
    assert _picture(server, ids[3]).stack_id is None
    # And the run reported what it held back rather than claiming a clean sweep.
    skipped = [s for r in report["results"] for s in r.get("skipped", [])]
    assert [s["picture_id"] for s in skipped] == [ids[2]], report


# ── clearing a decision (the Decided page's "Clear decision") ─────────────────


def _stacks(server) -> list:
    return _run(server, lambda session: session.exec(select(PictureStack)).all())


def _open_signatures(server) -> list[str]:
    page, _total, _cursor = _run(server, tiers.page_queue_in_session, None, None, 0, 50)
    return [group["signature"] for group in page]


def test_clearing_a_stacked_decision_returns_the_group_to_the_queue(server):
    """The user-reported bug (owner, 2026-07-30): clear must return the group.

    Reopen used to stamp the verdict and unresolve the group but leave the
    pictures stacked, and the live-groups filter requires the members to span
    two stack units - so the cleared group vanished from Decided AND never
    reappeared in the queue. Clearing a stacked decision must dissolve the
    stack the verdict created.
    """
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
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0

    cleared = _run(server, verdicts.reopen_verdict_in_session, signature)
    assert cleared["previous_verdict"] == VERDICT_STACKED
    assert cleared["group_returned_to_queue"] is True
    # Back in the count AND the open-queue listing, not just marked reopened.
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1
    assert _open_signatures(server) == [signature]
    # The verdict's stack is dissolved and no empty stack row is left behind.
    assert _picture(server, ids[0]).stack_id is None
    assert _picture(server, ids[1]).stack_id is None
    assert _stacks(server) == []


def test_clearing_a_stacked_decision_restores_a_folded_stack(server):
    """The true inverse: a pre-existing stack the verdict folded in comes back.

    The verdict's operation row recorded the pre-verdict stack state, so a
    clear restores the pair's own stack rather than flattening everything the
    verdict touched.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )

    def pre_stack(session):
        stack = PictureStack(name="pair")
        session.add(stack)
        session.commit()
        session.refresh(stack)
        for position, picture_id in enumerate(ids[:2]):
            pic = session.get(Picture, picture_id)
            pic.stack_id = int(stack.id)
            pic.stack_position = position
            session.add(pic)
        session.commit()
        return int(stack.id)

    pair_stack = _run(server, pre_stack)
    _scan(server)
    signature = _one_signature(server)
    # Cover is the loner, so the pair's stack is folded into a new/cover unit.
    _run(server, verdicts.apply_stack_verdict_in_session, signature, ids[2], [], None)
    assert len({_picture(server, pid).stack_id for pid in ids}) == 1

    _run(server, verdicts.reopen_verdict_in_session, signature)
    # The pair is back in ITS stack; the loner is a loner again.
    assert _picture(server, ids[0]).stack_id == pair_stack
    assert _picture(server, ids[1]).stack_id == pair_stack
    assert _picture(server, ids[2]).stack_id is None
    # Two stack units again, so the group genuinely poses a decision.
    assert _open_signatures(server) == [signature]
    # Exactly one stack row survives: the pair's. No orphaned empties.
    assert [int(row.id) for row in _stacks(server)] == [pair_stack]


def test_clearing_a_keep_separate_returns_the_group(server):
    """The keep-separate clear touches no pictures and already worked; pin it."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    signature = _one_signature(server)
    _run(server, verdicts.apply_keep_separate_in_session, signature, None)
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0

    cleared = _run(server, verdicts.reopen_verdict_in_session, signature)
    assert cleared["previous_verdict"] == VERDICT_KEEP_SEPARATE
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1
    assert _open_signatures(server) == [signature]
    assert all(_picture(server, pid).stack_id is None for pid in ids)


def test_clearing_a_stacked_decision_records_one_operation(server):
    """The unstack is a picture mutation, so it must be undoable like any other.

    A keep-separate clear still records nothing (no picture facet moves), so
    the log stays free of no-op rows.
    """
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    signature = _one_signature(server)
    _run(server, verdicts.apply_stack_verdict_in_session, signature, None, [], None)
    cleared = _run(server, verdicts.reopen_verdict_in_session, signature)
    rows = _operations(server)
    assert len(rows) == 2
    clear_row = rows[-1]
    assert clear_row.op_type == verdicts.OP_TYPE_REOPEN
    assert clear_row.undoable is True
    assert clear_row.batch_id == cleared["batch_id"]
    assert cleared["batch_id"] and cleared["batch_id"].startswith("srv-")
    # The correlation the undo-of-clear hook needs is stored on the verdict.
    row = _run(
        server,
        lambda session: session.exec(
            select(DedupVerdict).where(DedupVerdict.signature == signature)
        ).first(),
    )
    assert row.reopen_batch_id == cleared["batch_id"]
    # The verdict's own undo handle is untouched: undoing the ORIGINAL stack
    # operation must still find its verdict.
    assert row.batch_id == rows[0].batch_id


def test_undo_of_a_clear_restacks_and_re_decides_and_redo_clears_again(server):
    """Both directions of the clear operation itself.

    Undo-of-clear must restore the verdict's stack AND re-mark the decision
    live, or the pictures and the queue would disagree (the same half-restore
    class the verdict hooks exist for). Redo clears again.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    signature = _one_signature(server)
    stacked = _run(
        server, verdicts.apply_stack_verdict_in_session, signature, None, [], None
    )
    _run(server, verdicts.reopen_verdict_in_session, signature)
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1

    _run(server, operation_log_service.undo_in_session, None)
    # Restacked, decided again, out of the queue.
    assert _picture(server, ids[0]).stack_id == stacked.stack_id
    assert _picture(server, ids[1]).stack_id == stacked.stack_id
    row = _run(
        server,
        lambda session: session.exec(
            select(DedupVerdict).where(DedupVerdict.signature == signature)
        ).first(),
    )
    assert row.reopened_at is None
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0

    _run(server, operation_log_service.redo_in_session)
    # Cleared again: unstacked, reopened, back in the queue, no empty stacks.
    assert _picture(server, ids[0]).stack_id is None
    assert _picture(server, ids[1]).stack_id is None
    row = _run(
        server,
        lambda session: session.exec(
            select(DedupVerdict).where(DedupVerdict.signature == signature)
        ).first(),
    )
    assert row.reopened_at is not None
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1
    assert _stacks(server) == []


def test_clearing_one_group_of_a_bulk_batch_leaves_the_others_alone(server):
    """A bulk auto-stack coalesces many groups into ONE batch id.

    Clearing one group must revert only that group's stacking - never its batch
    siblings' - and must leave the sibling verdicts decided.
    """
    ids = _seed_two_exact_groups(server)
    _scan(server)
    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)
    assert report["groups"] == 2
    first, second = report["results"]

    _run(server, verdicts.reopen_verdict_in_session, first["signature"])
    # The cleared group is unstacked and back in the queue...
    assert _open_signatures(server) == [first["signature"]]
    for pid in first["picture_ids"]:
        assert _picture(server, pid).stack_id is None
    # ... while the sibling keeps its stack and its verdict.
    for pid in second["picture_ids"]:
        assert _picture(server, pid).stack_id == second["stack_id"]
    sibling = _run(
        server,
        lambda session: session.exec(
            select(DedupVerdict).where(DedupVerdict.signature == second["signature"])
        ).first(),
    )
    assert sibling.reopened_at is None
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1
    assert ids  # seeded four pictures; both assertions above cover all of them


def test_clear_after_a_manual_unstack_touches_no_pictures(server):
    """A verdict stack the user already dissolved needs no unstack.

    The members span two stack units again, so the group is queue-visible the
    moment the memory clears; mutating pictures (or recording an operation)
    would fight the user's own arrangement.
    """
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

    def unstack_by_hand(session):
        for picture_id in ids:
            pic = session.get(Picture, picture_id)
            pic.stack_id = None
            pic.stack_position = None
            session.add(pic)
        session.commit()

    _run(server, unstack_by_hand)
    before = len(_operations(server))
    cleared = _run(server, verdicts.reopen_verdict_in_session, signature)
    assert cleared["batch_id"] is None
    assert len(_operations(server)) == before
    assert _open_signatures(server) == [signature]


def test_a_clear_that_cannot_locate_its_stack_operation_is_refused(server):
    """No silent fallback: an uncorrelatable stacked verdict is an error.

    A verdict row without a batch id (pre-batching data) cannot name the
    operation that knows its pre-verdict stack state. The clear refuses with
    context instead of guessing; the way out is unstacking from the Stacks
    view, after which the clear needs no picture mutation and succeeds.
    """
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

    def strip_batch_id(session):
        row = session.exec(
            select(DedupVerdict).where(DedupVerdict.signature == signature)
        ).first()
        row.batch_id = None
        session.add(row)
        session.commit()

    _run(server, strip_batch_id)
    with pytest.raises(DedupVerdictError, match="cannot locate"):
        _run(server, verdicts.reopen_verdict_in_session, signature)
    # Nothing moved and the verdict still stands.
    assert _picture(server, ids[0]).stack_id is not None
    row = _run(
        server,
        lambda session: session.exec(
            select(DedupVerdict).where(DedupVerdict.signature == signature)
        ).first(),
    )
    assert row.reopened_at is None


def test_a_clear_may_not_reuse_the_verdicts_own_batch_id(server):
    """Grafting the clear into the verdict's own batch would make one undo
    apply the stack and its inverse in the same restore. Refused, not honoured.
    """
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)
    signature = _one_signature(server)
    gesture = "cli-gesture-clear-1"
    _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        signature,
        None,
        [],
        gesture,
    )
    with pytest.raises(DedupVerdictError, match="own batch"):
        _run(server, verdicts.reopen_verdict_in_session, signature, gesture)


# ── withholding a group with no stackable decision left ───────────────────────
#
# Owner call, 2026-07-30: a group left with fewer than two stackable members is
# withheld from the queue entirely rather than shown with Stack disabled. The
# rule has to be SQL inside the group filter, not a post-filter on the page, or
# the page shrinks under its own LIMIT and the badge disagrees with the list.


def test_a_group_with_one_stackable_member_is_withheld_from_the_queue(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _lock_set(server, "Frozen", [ids[1]])
    _scan(server)

    page, total, _cursor = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    assert page == []
    # `total` is the count the same filter produces, so the header cannot claim
    # a group the list does not show.
    assert total == 0


def test_the_badge_and_the_tier_split_withhold_it_too(server):
    """One rule, three surfaces. A count that disagrees with the list is the bug."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            # A second, wholly unfrozen group that must survive the filter.
            {"pixel_sha": "bbb", "size_bytes": 200},
            {"pixel_sha": "bbb", "size_bytes": 200},
        ],
    )
    _lock_set(server, "Frozen", [ids[1]])
    _scan(server)

    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1
    by_tier = _run(server, tiers.count_by_tier_in_session, None, None)
    assert by_tier["exact"] == 1
    page, total, _cursor = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    assert len(page) == 1
    assert total == 1
    assert sorted(c["picture_id"] for c in page[0]["candidates"]) == [ids[2], ids[3]]


def test_a_group_keeping_two_stackable_members_is_still_offered(server):
    """Over-block regression: the filter must not swallow a decidable group."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _lock_set(server, "Frozen", [ids[2]])
    _scan(server)

    page, total, _cursor = _run(server, tiers.page_queue_in_session, None, None, 0, 10)
    assert total == 1
    assert len(page[0]["candidates"]) == 3, "the frozen member is still listed"
    assert sum(1 for c in page[0]["candidates"] if c["stackable"]) == 2


def test_unlocking_the_set_returns_the_withheld_group(server):
    """The withholding is a live predicate, not a decision baked in at scan time."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    set_id = _lock_set(server, "Frozen", [ids[1]])
    _scan(server)
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0

    def unlock(session):
        picture_set = session.get(PictureSet, set_id)
        picture_set.locked = False
        session.add(picture_set)
        session.commit()

    _run(server, unlock)
    # No rescan: the group row never changed, only the lock did.
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1


def test_the_page_stays_full_when_a_withheld_group_sits_inside_it(server):
    """The filter runs in SQL, so LIMIT counts only groups that survive it.

    A post-filter applied after the LIMIT would serve a short page here and the
    cursor would skip work; this pins that it does not.
    """
    specs = []
    for i in range(5):
        specs.extend(
            [
                {"pixel_sha": f"g{i}", "size_bytes": 100 + i},
                {"pixel_sha": f"g{i}", "size_bytes": 100 + i},
            ]
        )
    ids = _seed(server, specs)
    # Freeze one member of the second and fourth groups: both become undecidable.
    _lock_set(server, "Frozen", [ids[3], ids[7]])
    _scan(server)

    page, total, _cursor = _run(server, tiers.page_queue_in_session, None, None, 0, 3)
    assert total == 3, "two of the five groups are withheld"
    assert len(page) == 3, "the page is filled from the groups that survive"


# ── the dry run counts what the run will actually do ──────────────────────────


def test_the_dry_run_excludes_frozen_members_from_its_counts(server):
    """The consent dialog must not promise pictures the run will skip."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _lock_set(server, "Frozen", [ids[2]])
    _scan(server)

    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, True, None)
    assert report["groups"] == 1
    assert report["pictures"] == 2, "the frozen member is not promised"
    assert report["dry_run_summary"]["pictures"] == 2
    # The top-level figure and the summary come from one computation.
    assert report["pictures"] == report["dry_run_summary"]["pictures"]


def test_the_dry_run_omits_a_group_with_no_stackable_decision(server):
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _lock_set(server, "Frozen", [ids[1]])
    _scan(server)

    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, True, None)
    assert report["groups"] == 0
    assert report["pictures"] == 0


def test_the_dry_run_still_counts_a_cover_that_gains_from_an_unfrozen_twin(server):
    """Over-block regression on the summary: a moved cover still counts."""
    ids = _seed(
        server,
        [
            # The stored preselection is the frozen one, so the preview has to
            # move the cover exactly as the real run does.
            {"pixel_sha": "aaa", "size_bytes": 100, "smart_score": 4.9},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100, "tags": ["sunset"]},
        ],
    )
    _lock_set(server, "Frozen", [ids[0]])
    _scan(server)

    summary = _run(server, verdicts.bulk_auto_stack_in_session, None, None, True, None)[
        "dry_run_summary"
    ]
    assert summary["pictures"] == 2
    assert summary["covers_gaining_tags"] == 1


# ── the batched lock lookup refuses to guess ──────────────────────────────────


def test_the_lock_lookup_raises_rather_than_calling_an_unknown_id_unfrozen(server):
    """A partial batch must fail loudly, not silently admit a frozen picture."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _lock_set(server, "Frozen", [ids[1]])

    def probe(session):
        # Built over the FIRST picture only, then asked about the second.
        lookup = set_locks.build_locked_set_lookup(session, [ids[0]])
        with pytest.raises(KeyError):
            lookup.sets_for(ids[1])
        with pytest.raises(KeyError):
            set_locks.partition_stackable_members(session, ids, lookup=lookup)
        # Built over both, it answers correctly.
        full = set_locks.build_locked_set_lookup(session, ids)
        partition = set_locks.partition_stackable_members(session, ids, lookup=full)
        assert partition.blocked_ids == [ids[1]]

    _run(server, probe)


# ── D1: auto-stack plans the population the queue shows ───────────────────────
#
# `bulk_auto_stack_in_session` used to filter on `stackable_groups_filter`, which
# says nothing about stack units, while the queue list and both counts filter on
# `_live_groups_filter`, which requires two. On a real library that meant 62
# planned groups behind a badge that said 3: the other 59 were already collapsed
# into one stack, so nothing was created, and 21 of them had their curated cover
# replaced, because the run passes no cover and `_stack_members` forces the
# group's preselection to position 0.


def _stack_pictures(server, picture_ids, name="hand-stacked"):
    """Put *picture_ids* into one stack, first id leading. Returns the stack id."""

    def build(session):
        stack = PictureStack(name=name)
        session.add(stack)
        session.commit()
        session.refresh(stack)
        for position, picture_id in enumerate(picture_ids):
            pic = session.get(Picture, picture_id)
            pic.stack_id = int(stack.id)
            pic.stack_position = position
            session.add(pic)
        session.commit()
        return int(stack.id)

    return _run(server, build)


def _preselected_cover(server, signature: str) -> int:
    return _run(
        server,
        lambda session: int(
            session.exec(
                select(DedupGroup.cover_picture_id).where(
                    DedupGroup.signature == signature
                )
            ).one()
        ),
    )


def test_auto_stack_never_plans_a_fully_collapsed_group(server):
    """D1 negative: a group whose live members already share ONE stack.

    It poses no decision, so the queue hides it, and the run must not plan it
    either. Verified non-vacuous: restoring `stackable_groups_filter` on the
    auto-stack query fails this test.
    """
    ids = _seed(
        server,
        [
            # Distinct smart-score buckets, so the preselection is deterministic
            # and is NOT the picture that leads the hand-made stack.
            {"pixel_sha": "aaa", "size_bytes": 100, "smart_score": 1.0},
            {"pixel_sha": "aaa", "size_bytes": 100, "smart_score": 2.0},
            {"pixel_sha": "aaa", "size_bytes": 100, "smart_score": 9.0},
        ],
    )
    _scan(server)
    signature = _only_signature(server)
    assert _preselected_cover(server, signature) == ids[2]
    # The user then stacks them by hand from the grid, leading with ids[0].
    stack_id = _stack_pictures(server, [ids[0], ids[1], ids[2]])

    # The queue already hides it; the run must agree.
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 0
    dry = _run(server, verdicts.bulk_auto_stack_in_session, None, None, True, None)
    assert dry["groups"] == 0
    assert dry["pictures"] == 0

    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)
    assert report["groups"] == 0
    assert report["results"] == []
    assert report["failures"] == []

    # And the curated stack is untouched: same stack, same leader, no verdict.
    assert {_picture(server, pid).stack_id for pid in ids} == {stack_id}
    assert _picture(server, ids[0]).stack_position == 0
    assert _run(server, lambda session: session.exec(select(DedupVerdict)).all()) == []


def test_auto_stack_still_plans_a_genuinely_unstacked_group(server):
    """D1 positive: over-filtering would be its own regression.

    One collapsed group and one loose group in the same run: exactly the loose
    one is planned.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "bbb", "size_bytes": 200},
            {"pixel_sha": "bbb", "size_bytes": 200},
        ],
    )
    _scan(server)
    _stack_pictures(server, [ids[0], ids[1]], name="collapsed")

    dry = _run(server, verdicts.bulk_auto_stack_in_session, None, None, True, None)
    assert dry["groups"] == 1
    assert dry["pictures"] == 2

    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)
    assert report["groups"] == 1
    assert report["failures"] == []
    stacked = {int(pid) for item in report["results"] for pid in item["picture_ids"]}
    assert stacked == {ids[2], ids[3]}
    assert _picture(server, ids[2]).stack_id == _picture(server, ids[3]).stack_id
    assert _picture(server, ids[2]).stack_id is not None


def test_auto_stack_still_plans_a_group_that_folds_a_stack_in(server):
    """D1 positive, the sharper case: a stack plus a loner still poses a decision.

    Two units, so `_live_groups_filter` keeps it and the run must still act,
    the filter tightening must not swallow the groups where a fold WOULD happen.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "zzz", "size_bytes": 900},
        ],
    )
    _scan(server)
    stack_id = _stack_pictures(server, [ids[1], ids[2]], name="pre-existing")

    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)
    assert report["groups"] == 1
    assert report["event_picture_ids"] == sorted(ids)
    assert {_picture(server, pid).stack_id for pid in ids} == {stack_id}


def test_bulk_wrapper_emits_aggregated_ids_and_keeps_them_out_of_response():
    notifications = []

    class FakeDatabase:
        def run_task(self, func, *args):
            assert func is verdicts.bulk_auto_stack_in_session
            return {
                "dry_run": False,
                "results": [{"picture_ids": [1, 2]}],
                "event_picture_ids": [1, 2, 3],
            }

    class FakeVault:
        db = FakeDatabase()

        def notify(self, event_type, payload):
            notifications.append((event_type, payload))

    report = verdicts.bulk_auto_stack(
        FakeVault(), source="test", origin_client_id="tab-1"
    )

    assert "event_picture_ids" not in report
    assert notifications[0][1] == {
        "picture_ids": [1, 2, 3],
        "origin_client_id": "tab-1",
        "change_kind": "updated",
        "source": "test",
    }


# ── B2: the cover may be a folded stack's leader ──────────────────────────────


def test_a_folded_stacks_leader_is_an_acceptable_cover(server):
    """B2: the deck's face is the leader, and the group names only one member.

    Requiring the cover to be a group member forced the MATCHED member to be
    promoted instead, silently re-covering a stack the user had already curated.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},  # 0: the loose duplicate
            {"pixel_sha": "aaa", "size_bytes": 100},  # 1: the matched deck member
            {"pixel_sha": "yyy", "size_bytes": 800},  # 2: the deck's LEADER
            {"pixel_sha": "zzz", "size_bytes": 900},  # 3: another deck member
        ],
    )
    # Leader first: ids[2] is at stack_position 0 and is not in the group.
    stack_id = _stack_pictures(server, [ids[2], ids[1], ids[3]], name="deck")
    _scan(server)

    result = _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        _one_signature(server),
        ids[2],
        [],
        None,
    )
    assert result.cover_picture_id == ids[2]
    assert result.stack_id == stack_id
    # Everything landed in the pre-existing stack and the leader never moved.
    assert {_picture(server, pid).stack_id for pid in ids} == {stack_id}
    assert _picture(server, ids[2]).stack_position == 0
    # The matched member was NOT promoted over it.
    assert _picture(server, ids[1]).stack_position != 0
    # Every member has a distinct position (the renumber ran over the whole deck).
    positions = sorted(_picture(server, pid).stack_position for pid in ids)
    assert positions == [0, 1, 2, 3]


def test_choosing_the_deck_as_cover_leaves_its_leader_where_it_was(server):
    """B2, stated as the invariant the queue promises: nothing gets re-covered.

    Scoped to **position 0**, which is the whole of the cover contract: a
    growing stack always renumbers its non-leaders through
    ``normalize_stack_positions``, so asserting a fixed position for anything
    below the leader would pin that ordering rule rather than this fix.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "yyy", "size_bytes": 800},
        ],
    )
    stack_id = _stack_pictures(server, [ids[2], ids[1]], name="deck")
    assert _picture(server, ids[2]).stack_position == 0
    _scan(server)

    result = _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        _one_signature(server),
        ids[2],
        [],
        None,
    )
    assert result.cover_picture_id == ids[2]
    # The pre-existing leader still leads, and the matched member did not take
    # its place: which is exactly what promoting the group member would do.
    assert _picture(server, ids[2]).stack_position == 0
    assert _picture(server, ids[1]).stack_position != 0
    assert _picture(server, ids[0]).stack_id == stack_id


def test_a_leader_of_an_untouched_stack_is_still_rejected_as_cover(server):
    """B2 negative: relaxed to the resulting stack, not to anything at all.

    The sharper sibling of `test_a_cover_outside_the_group_is_rejected`: the
    offered cover IS a stack leader, just not of a stack this group folds in.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "yyy", "size_bytes": 800},
            {"pixel_sha": "zzz", "size_bytes": 900},
        ],
    )
    # A stack of its own, sharing no picture with the duplicate group.
    _stack_pictures(server, [ids[2], ids[3]], name="elsewhere")
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
    # Nothing moved.
    assert _picture(server, ids[0]).stack_id is None
    assert _picture(server, ids[1]).stack_id is None
    assert _operations(server) == []


def test_an_excluded_members_stack_leader_is_not_a_legal_cover(server):
    """B2 negative: exclusions shrink the legal cover set with the fold."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},  # 0
            {"pixel_sha": "aaa", "size_bytes": 100},  # 1
            {"pixel_sha": "aaa", "size_bytes": 100},  # 2, deck member
            {"pixel_sha": "yyy", "size_bytes": 800},  # 3, the deck's leader
        ],
    )
    _stack_pictures(server, [ids[3], ids[2]], name="deck")
    _scan(server)

    # Excluding the deck's only group member takes its stack out of the fold, so
    # its leader is no longer part of the resulting stack.
    with pytest.raises(DedupVerdictError, match="not an included member"):
        _run(
            server,
            verdicts.apply_stack_verdict_in_session,
            _one_signature(server),
            ids[3],
            [ids[2]],
            None,
        )


# ── B4: the receipts count what actually moved ────────────────────────────────


def _stack_operation_summary(server) -> str:
    rows = [op for op in _operations(server) if op.op_type == verdicts.OP_TYPE_STACK]
    assert len(rows) == 1, f"expected one stack operation, got {rows}"
    return str(rows[0].summary)


def test_the_receipt_counts_the_folded_stacks_members_too(server):
    """B4: `len(included)` under-reported every verdict that folded a stack in."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},  # 0, the loose duplicate
            {"pixel_sha": "aaa", "size_bytes": 100},  # 1, in the deck
            {"pixel_sha": "yyy", "size_bytes": 800},  # 2, deck member, not matched
            {"pixel_sha": "zzz", "size_bytes": 900},  # 3, deck member, not matched
        ],
    )
    _stack_pictures(server, [ids[1], ids[2], ids[3]], name="deck")
    _scan(server)

    _run(
        server,
        verdicts.apply_stack_verdict_in_session,
        _one_signature(server),
        ids[0],
        [],
        None,
    )
    # Four pictures ended up in the stack, so the receipt says four.
    assert _stack_operation_summary(server) == "Stacked 4 duplicates"


def test_the_receipt_counts_a_plain_group_exactly(server):
    """B4 negative: no stack folded in, so nothing is inflated."""
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
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
    assert _stack_operation_summary(server) == "Stacked 2 duplicates"


def test_the_dry_run_counts_the_pictures_a_fold_would_move(server):
    """B4: the consent dialog promised fewer pictures than the run would touch."""
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},  # 0
            {"pixel_sha": "aaa", "size_bytes": 100},  # 1, in the deck
            {"pixel_sha": "yyy", "size_bytes": 800},  # 2, deck member
        ],
    )
    _stack_pictures(server, [ids[1], ids[2]], name="deck")
    _scan(server)

    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, True, None)
    assert report["groups"] == 1
    assert report["pictures"] == 3
    assert report["dry_run_summary"]["pictures"] == 3
    # The top-level figure and the summary still come from one computation.
    assert report["pictures"] == report["dry_run_summary"]["pictures"]
    # And the cover row is unchanged: the union runs over the group's members.
    assert report["dry_run_summary"]["covers_gaining_metadata"] == 0


def test_the_dry_run_does_not_inflate_a_group_with_no_stack(server):
    """B4 negative: loose groups still count exactly their own members."""
    _seed_two_exact_groups(server)
    _scan(server)
    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, True, None)
    assert report["groups"] == 2
    assert report["pictures"] == 4
    assert report["dry_run_summary"]["pictures"] == 4


def test_the_dry_run_counts_a_shared_stack_once(server):
    """B4, the other direction: honest also means not over-counting.

    Two exact groups each name a member of the SAME stack, so both would fold
    it in. Summing per group promised its members twice.
    """
    ids = _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},  # 0, in the shared stack
            {"pixel_sha": "aaa", "size_bytes": 100},  # 1, loose twin of 0
            {"pixel_sha": "bbb", "size_bytes": 200},  # 2, in the shared stack
            {"pixel_sha": "bbb", "size_bytes": 200},  # 3, loose twin of 2
        ],
    )
    _stack_pictures(server, [ids[0], ids[2]], name="shared")
    _scan(server)

    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, True, None)
    assert report["groups"] == 2
    # Four distinct pictures would move, not six.
    assert report["pictures"] == 4
    assert report["dry_run_summary"]["pictures"] == 4


def _force_variable_limit(server, limit=999):
    """Lower every new vault connection to SQLite's historical bind limit.

    Modern SQLite raises ``SQLITE_MAX_VARIABLE_NUMBER`` to 32766, so a query that
    binds one parameter per id passes here at any test-sized scale and only dies
    on a real library. Pinning the ceiling back to the pre-3.32 value is what
    makes the regression reproducible in a test that seeds a thousand rows
    instead of thirty thousand.
    """
    engine = server.vault.db._engine

    def set_limit(dbapi_conn, _record):
        dbapi_conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, limit)

    event.listen(engine, "connect", set_limit)
    engine.dispose()


def test_the_dry_run_survives_the_sqlite_variable_ceiling(server):
    """Regression for #751: the preview aggregates over the WHOLE candidate set.

    The reporter had 30,140 exact groups. Every id set in the summary was bound
    one parameter per id, so the group query, the picture query and the tag query
    each blew the ceiling and the consent dialog reported "the preview could not
    be read". Both scopes here are over 999: 1001 groups and 2002 pictures.
    """
    specs = []
    for index in range(1001):
        specs.extend(
            [
                {"pixel_sha": f"sha{index:05d}", "size_bytes": 100 + index},
                {"pixel_sha": f"sha{index:05d}", "size_bytes": 100 + index},
            ]
        )
    _seed(server, specs)
    _scan(server)
    _force_variable_limit(server)

    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, True, None)

    assert report["dry_run"] is True
    assert report["groups"] == 1001
    assert report["pictures"] == 2002
    assert report["dry_run_summary"]["groups"] == 1001
    assert report["dry_run_summary"]["pictures"] == 2002


def test_the_dry_run_does_not_occupy_the_writer_queue(server):
    """Regression for #751, second half: a read-only preview blocked every write.

    ``bulk_auto_stack`` sent the dry run to the serialised writer thread, so a
    slow preview held it and each stack verdict queued behind it until the run
    finished, so the queue answered "Could not stack that group" the whole time.
    The preview writes nothing, so it must not take the writer at all.
    """
    _seed(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "aaa", "size_bytes": 100},
        ],
    )
    _scan(server)

    real_run_task = server.vault.db.run_task
    writer_calls = []

    def spy(func, *args, **kwargs):
        writer_calls.append(getattr(func, "__name__", repr(func)))
        return real_run_task(func, *args, **kwargs)

    server.vault.db.run_task = spy
    try:
        report = verdicts.bulk_auto_stack(server.vault, None, None, True, None)
        assert report["dry_run"] is True
        assert report["groups"] == 1
        assert "bulk_auto_stack_in_session" not in writer_calls

        # The applied run is a real mutation and still belongs on the writer.
        writer_calls.clear()
        applied = verdicts.bulk_auto_stack(server.vault, None, None, False, None)
        assert applied["dry_run"] is False
        assert "bulk_auto_stack_in_session" in writer_calls
    finally:
        server.vault.db.run_task = real_run_task
