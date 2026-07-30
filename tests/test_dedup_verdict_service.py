"""Unit tests for applying and remembering duplicate verdicts.

Covers:

* **stack** — members land in one stack led by the chosen cover, excluded members
  are untouched, and the metadata union runs (tags, sets, score);
* **keep separate** — no picture row changes, and the decision survives a rescan;
* **reopen** — the group comes back and the decision history is kept;
* **bulk auto-stack** — exact tier only, one batch id across the whole run, and
  the dry run writes nothing;
* **the operation log (§21)** — one verdict is exactly one row (no double-record
  through `routes/stacks.py`), undo reverses the stacking *and* the metadata
  union, the snapshot covers stack siblings the group never named, and a whole
  bulk run reverses with a single batch undo;
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
from pixlstash.db_models import (
    Picture,
    PictureSet,
    PictureSetMember,
    PictureStack,
)
from pixlstash.db_models.dedup import (
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
    """Only tier 1 is bulk-eligible; a near group always goes through the queue.

    The near group holds two pictures of ITS OWN (no overlap with the exact
    pair): a near group sharing the exact pair's members would stop posing a
    decision the moment auto-stack stacked them — the pending-decision filter's
    stack-units rule — which is correct but proves nothing about the near tier.
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
    B is **folded into A** and sibling 3 — which the group never named — is
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
    stored on the verdict row — the correlation the post-restore hook needs.
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


def test_reopen_records_no_operation(server):
    """Reopen IS the explicit inverse action, so it stays out of the log."""
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

    The pictures are untouched in every direction — keep-separate never had a
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
    stacked group and the keep-separate hook restores the kept-separate one —
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


def test_a_locked_group_mid_run_does_not_abort_the_bulk_run(server):
    """Regression for the CSO's B2.

    The locked-set guards raise ``HTTPException(423)``, not
    ``DedupVerdictError``. Catching only the latter meant a locked group in the
    middle of a bulk run propagated out **after** earlier groups had already
    committed — a partially applied bulk mutation whose server-minted batch id
    the caller never received, i.e. work that happened with no undo handle in the
    response. The run must instead skip the group, report it, and still return
    the batch id.
    """
    ids = _seed_three_exact_groups(server)
    _lock_picture_in_a_set(server, ids[2])  # a member of the middle group
    _scan(server)

    report = _run(server, verdicts.bulk_auto_stack_in_session, None, None, False, None)

    # The run completed rather than raising.
    assert report["batch_id"]
    assert report["dry_run"] is False
    assert report["groups"] == 2
    assert report["blocked"] == 1
    assert report["failed"] == 0

    # Every group is accounted for under exactly one outcome.
    assert {r["outcome"] for r in report["results"]} == {verdicts.BULK_REASON_APPLIED}
    assert len(report["failures"]) == 1
    failure = report["failures"][0]
    assert failure["outcome"] == verdicts.BULK_REASON_BLOCKED
    assert failure["status_code"] == 423
    assert failure["error"]["code"] == "set_locked"

    # The locked group is untouched; the other two are stacked.
    assert _picture(server, ids[2]).stack_id is None
    assert _picture(server, ids[3]).stack_id is None
    assert _picture(server, ids[0]).stack_id is not None
    assert _picture(server, ids[4]).stack_id is not None
    # ...and the locked group is still in the queue, awaiting its own decision.
    assert _run(server, tiers.count_unresolved_in_session, None, None) == 1


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
    in the handler, and are passed down — the contextvar is dead on the DB worker
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
    """Regression for the CSO's C1 — pins ``include_deleted=True``.

    ``normalize_stack_positions`` renumbers **every** member of an affected
    stack, soft-deleted ones included (§21.1). If the undo snapshot expanded the
    stack without ``include_deleted=True``, the scrapheaped sibling's renumbered
    position would be an unrecorded change that undo could not reverse — and the
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

    Derived from the planned verdicts in the dry run's own snapshot — the union
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
            _one_signature(server),
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
