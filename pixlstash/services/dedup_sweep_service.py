"""Vault-wide near-duplicate sweep - server-side group resolution + dry-run plan.

Promotes the manual grid maneuver (Likeness Groups sort → select → "Stack groups")
into a library-wide, policy-driven service. Three things move server-side:

1. **Group resolution is vault-wide, not selection-scoped.** The grid endpoint
   (``GET /pictures/likeness-groups``) loads every above-threshold
   :class:`~pixlstash.db_models.picture_likeness.PictureLikeness` row into an
   adjacency dict and BFSes it, then applies the *display* filters. This service
   instead **streams** the edge table in keyset-paginated pages and folds each
   edge into a union-find forest, so peak memory is two ints per *picture* rather
   than an adjacency set per picture, and the edge pages never all exist at once.
   Per-component min/max likeness is accumulated on the union-find root as the
   edges stream past, so the weakest link in a transitive chain is known without
   a second pass over the edges.
2. **The confidence policy is a parameter object** (:class:`SweepPolicy`), never
   hardcoded: a candidate threshold, a *higher* auto-resolve threshold, a
   smart-score margin, a group-size ceiling, and the cross-stack disposition. It
   splits every group into ``auto_collapse`` (act) and ``needs_review`` (propose),
   and every ``needs_review`` group carries machine-readable
   :class:`ReviewReason` codes - the sweep never rejects a group silently.
3. **Groups spanning several existing stacks are represented, not skipped.** The
   shipped client drops those groups with a single aggregated warning (and with no
   feedback at all when *every* selected group spans stacks). Here they are a
   first-class :attr:`SweepOutcome.MERGE_STACKS` row with the target stack and the
   stacks that would be folded into it; :class:`CrossStackPolicy` decides whether
   that lands in the auto lane or the review lane.

**Strictly non-destructive.** Resolution means *stacking*, never deleting. This
module is read-only by construction: it opens no write task, and nothing here
mutates a row. It produces a :class:`SweepReport` - the dry-run plan an execution
step (a later lane) would consume.

**Lane-B seam.** :func:`plan_near_duplicate_sweep` accepts an optional
``operation_batch_id``. It is inert for a dry run (nothing is written, so there is
nothing to log) and is echoed back in the report so a caller can correlate a plan
with the operation-log batch that later applies it. Nothing in this module imports
the operation log.

Keeper (stack-leader) selection reuses the shipped ordering - human ``score`` DESC,
then ``smart_score`` DESC, then ``created_at`` DESC (recency), then ``id`` ASC -
so a sweep-built stack has the same leader the grid would have picked. The one
deliberate divergence from ``routes/stacks.py::_stack_order_key`` is that this
service reads the **stored** ``Picture.smart_score`` column (maintained by
``MissingSmartScoreFinder``) instead of recomputing smart scores live: a vault-wide
sweep cannot afford a live batch recompute, and a picture whose smart score has not
been computed yet is reported as an ambiguous keeper rather than silently ranked at
zero.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterable, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import aliased
from sqlmodel import Session, select

from pixlstash.db_models import Picture
from pixlstash.db_models.picture_likeness import PictureLikeness
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.sql_chunking import SQLITE_ID_CHUNK as ID_CHUNK

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# Bounds on any likeness knob. Deliberately the same window the shipped grid
# slider clamps to (``getStackThreshold`` in frontend/src/utils/utils.js), so a
# policy the future UI can express is always a policy this service accepts.
MIN_LIKENESS = 0.5
MAX_LIKENESS = 0.99999

# Policy defaults. ``DEFAULT_LIKENESS_THRESHOLD`` matches the shipped grid default
# (0.9 - what counts as a candidate group at all); ``DEFAULT_AUTO_RESOLVE_LIKENESS``
# is the *higher* bar a group must clear to be acted on without a human look.
DEFAULT_LIKENESS_THRESHOLD = 0.9
DEFAULT_AUTO_RESOLVE_LIKENESS = 0.95
DEFAULT_SMART_SCORE_MARGIN = 0.05
DEFAULT_MIN_GROUP_SIZE = 2
DEFAULT_MAX_AUTO_GROUP_SIZE = 12
DEFAULT_MAX_GROUPS_LISTED = 500

# Edge rows pulled per keyset page. Large enough that a vault-sized sweep is a
# handful of round-trips, small enough that no page dominates memory.
EDGE_PAGE_SIZE = 20000


class CrossStackPolicy(str, Enum):
    """What to do with a group whose members already live in *several* stacks.

    The shipped client skips these groups. Both dispositions here keep them in the
    report; they differ only in which lane the group lands in.
    """

    REPORT = "report"
    """Propose the merge but always route it to the review lane (the default: a
    group spanning stacks means an earlier grouping decision is about to be
    overridden, which is exactly the case a human should see)."""

    MERGE = "merge"
    """Treat the merge as an ordinary outcome; the group is auto-resolved if it
    clears the remaining confidence gates."""


class SweepVerdict(str, Enum):
    """Which lane a group falls into."""

    AUTO_COLLAPSE = "auto_collapse"
    NEEDS_REVIEW = "needs_review"


class SweepOutcome(str, Enum):
    """The stacking action a group's resolution would perform.

    Every value is additive - a stack is created, grown, or merged. There is no
    destructive outcome, by design.
    """

    CREATE_STACK = "create_stack"
    ADD_TO_STACK = "add_to_stack"
    MERGE_STACKS = "merge_stacks"


class ReviewReason(str, Enum):
    """Why a group was routed to the review lane. Never empty on a review group."""

    SPANS_MULTIPLE_STACKS = "spans_multiple_stacks"
    WEAK_LIKENESS = "weak_likeness"
    OVERSIZED_GROUP = "oversized_group"
    AMBIGUOUS_KEEPER = "ambiguous_keeper"
    KEEPER_SMART_SCORE_MISSING = "keeper_smart_score_missing"


class _SkipReason(str, Enum):
    """Why a candidate component produced no group. Counted, never silent."""

    ALREADY_COLLAPSED = "already_collapsed"
    """Every remaining member already sits in one and the same stack."""

    ABSORBED = "absorbed"
    """The component's members were already claimed by an earlier group (they
    shared a stack with it), so it has nothing left of its own to resolve."""


@dataclass(frozen=True)
class SweepPolicy:
    """The confidence policy - the sweep's single tuning surface.

    Constructed per request; nothing here is read from module state, so two
    concurrent sweeps can run different policies. Validated in ``__post_init__``:
    an out-of-range knob raises :class:`ValueError` (the route turns that into a
    400) rather than being silently clamped, because a silently-retuned sweep is
    exactly the surprise this feature cannot afford.

    Attributes:
        likeness_threshold: Minimum pairwise likeness for an edge to join two
            pictures into a candidate group. The grid's group threshold.
        auto_resolve_likeness: The higher bar for acting without review. A group
            whose *weakest* observed edge falls below this is proposed, not acted
            on (:attr:`ReviewReason.WEAK_LIKENESS`). Must be >=
            ``likeness_threshold``; equal means the likeness axis never routes
            anything to review.
        smart_score_margin: How far the keeper must lead the runner-up on smart
            score, when the two tie on human ``score``, for the keeper to count as
            unambiguous.
        min_group_size: Smallest component that counts as a group at all.
        max_auto_group_size: Groups larger than this are proposed, not acted on
            (:attr:`ReviewReason.OVERSIZED_GROUP`) - a big transitively-chained
            blob is rarely one duplicate cluster.
        cross_stack: Disposition for groups spanning several existing stacks.
        max_groups_listed: Cap on the *listing* in the report. Counts and totals
            are always complete; only the per-group array is truncated.
    """

    likeness_threshold: float = DEFAULT_LIKENESS_THRESHOLD
    auto_resolve_likeness: float = DEFAULT_AUTO_RESOLVE_LIKENESS
    smart_score_margin: float = DEFAULT_SMART_SCORE_MARGIN
    min_group_size: int = DEFAULT_MIN_GROUP_SIZE
    max_auto_group_size: int = DEFAULT_MAX_AUTO_GROUP_SIZE
    cross_stack: CrossStackPolicy = CrossStackPolicy.REPORT
    max_groups_listed: int = DEFAULT_MAX_GROUPS_LISTED

    def __post_init__(self) -> None:
        for name in ("likeness_threshold", "auto_resolve_likeness"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not (
                MIN_LIKENESS <= float(value) <= MAX_LIKENESS
            ):
                raise ValueError(
                    f"{name} must be between {MIN_LIKENESS} and {MAX_LIKENESS}, "
                    f"got {value!r}"
                )
        if self.auto_resolve_likeness < self.likeness_threshold:
            raise ValueError(
                "auto_resolve_likeness must be >= likeness_threshold "
                f"({self.auto_resolve_likeness} < {self.likeness_threshold})"
            )
        if not 0.0 <= float(self.smart_score_margin) <= 1.0:
            raise ValueError(
                f"smart_score_margin must be between 0 and 1, "
                f"got {self.smart_score_margin!r}"
            )
        if int(self.min_group_size) < 2:
            raise ValueError(
                f"min_group_size must be at least 2, got {self.min_group_size!r}"
            )
        if int(self.max_auto_group_size) < int(self.min_group_size):
            raise ValueError(
                "max_auto_group_size must be >= min_group_size "
                f"({self.max_auto_group_size} < {self.min_group_size})"
            )
        if int(self.max_groups_listed) < 0:
            raise ValueError(
                f"max_groups_listed must not be negative, got {self.max_groups_listed!r}"
            )
        if not isinstance(self.cross_stack, CrossStackPolicy):
            object.__setattr__(
                self, "cross_stack", CrossStackPolicy(str(self.cross_stack))
            )

    def as_dict(self) -> dict[str, Any]:
        """Serialise the policy for echoing back in the report."""
        return {
            "likeness_threshold": float(self.likeness_threshold),
            "auto_resolve_likeness": float(self.auto_resolve_likeness),
            "smart_score_margin": float(self.smart_score_margin),
            "min_group_size": int(self.min_group_size),
            "max_auto_group_size": int(self.max_auto_group_size),
            "cross_stack": self.cross_stack.value,
            "max_groups_listed": int(self.max_groups_listed),
        }


@dataclass(frozen=True)
class SweepGroup:
    """One resolved near-duplicate group and the action it proposes.

    Attributes:
        index: Stable 0-based position in this report's group ordering (ascending
            lowest member id), so a UI can address a group without a DB id.
        picture_ids: Every member, keeper first, then the canonical stack order.
        keeper_id: The picture that would lead the resulting stack.
        verdict: Auto lane or review lane.
        reasons: Why it is in the review lane; empty for an auto group.
        outcome: The stacking action (always additive).
        target_stack_id: The stack that would receive the members, when one
            already exists. ``None`` for :attr:`SweepOutcome.CREATE_STACK`.
        merged_stack_ids: The other stacks that would be folded into
            ``target_stack_id`` - non-empty only for
            :attr:`SweepOutcome.MERGE_STACKS`.
        likeness_min: The group's weakest observed likeness edge (the chain's
            weak link). Members pulled in by stack expansion have no edge and do
            not lower it.
        likeness_max: The group's strongest observed likeness edge.
        keeper_margin: How far the keeper leads the runner-up on the deciding
            signal, or ``None`` when the signal could not be measured.
        keeper_margin_basis: Which signal decided - ``"score"``,
            ``"smart_score"``, or ``"none"`` when neither could separate them.
        held_bytes: Bytes of stored pixels held by the *non-keeper* members -
            the "these stacks hold N GB" figure, reported without promising the
            reclaim.
        linked_member_ids: Members that carry no likeness edge in this group and
            were pulled in because they share a stack with a member that does.
    """

    index: int
    picture_ids: list[int]
    keeper_id: int
    verdict: SweepVerdict
    reasons: list[ReviewReason]
    outcome: SweepOutcome
    target_stack_id: Optional[int]
    merged_stack_ids: list[int]
    likeness_min: float
    likeness_max: float
    keeper_margin: Optional[float]
    keeper_margin_basis: str
    held_bytes: int
    linked_member_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Serialise the group for the API response."""
        return {
            "index": self.index,
            "picture_ids": list(self.picture_ids),
            "keeper_id": self.keeper_id,
            "verdict": self.verdict.value,
            "reasons": [reason.value for reason in self.reasons],
            "outcome": self.outcome.value,
            "target_stack_id": self.target_stack_id,
            "merged_stack_ids": list(self.merged_stack_ids),
            "likeness_min": self.likeness_min,
            "likeness_max": self.likeness_max,
            "keeper_margin": self.keeper_margin,
            "keeper_margin_basis": self.keeper_margin_basis,
            "held_bytes": self.held_bytes,
            "linked_member_ids": list(self.linked_member_ids),
        }


@dataclass(frozen=True)
class SweepReport:
    """The dry-run plan: complete counts plus a (capped) group listing.

    The headline the future UI renders is
    ``"{auto_collapse_groups} groups auto-collapse, {needs_review_groups} need review"``.
    Counts always describe the whole vault; :attr:`listing_truncated` says whether
    :attr:`groups` shows all of them.
    """

    policy: dict[str, Any]
    operation_batch_id: Optional[str]
    generated_at: datetime
    scanned_edges: int
    candidate_groups: int
    already_collapsed_groups: int
    absorbed_groups: int
    groups_total: int
    auto_collapse_groups: int
    needs_review_groups: int
    auto_collapse_pictures: int
    needs_review_pictures: int
    outcome_counts: dict[str, int]
    reason_counts: dict[str, int]
    held_bytes_auto: int
    held_bytes_review: int
    groups: list[SweepGroup]
    listing_truncated: bool

    def as_dict(self) -> dict[str, Any]:
        """Serialise the report for the API response."""
        return {
            "policy": dict(self.policy),
            "operation_batch_id": self.operation_batch_id,
            "generated_at": self.generated_at,
            "scanned_edges": self.scanned_edges,
            "candidate_groups": self.candidate_groups,
            "already_collapsed_groups": self.already_collapsed_groups,
            "absorbed_groups": self.absorbed_groups,
            "groups_total": self.groups_total,
            "auto_collapse_groups": self.auto_collapse_groups,
            "needs_review_groups": self.needs_review_groups,
            "auto_collapse_pictures": self.auto_collapse_pictures,
            "needs_review_pictures": self.needs_review_pictures,
            "outcome_counts": dict(self.outcome_counts),
            "reason_counts": dict(self.reason_counts),
            "held_bytes_auto": self.held_bytes_auto,
            "held_bytes_review": self.held_bytes_review,
            "groups": [group.as_dict() for group in self.groups],
            "listing_truncated": self.listing_truncated,
        }


@dataclass
class SweepMember:
    """The per-picture columns the sweep ranks and measures on."""

    id: int
    stack_id: Optional[int]
    score: Optional[int]
    smart_score: Optional[float]
    created_at: Optional[datetime]
    size_bytes: Optional[int]


class _LikenessForest:
    """Union-find over streamed likeness edges, carrying per-component extremes.

    Only the *root* of a component holds its ``(min, max)`` likeness accumulator;
    a union merges the two accumulators, so the weakest link of a transitively
    chained component is known when the stream ends - no second pass over the
    edge table and no adjacency structure.
    """

    def __init__(self) -> None:
        self._parent: dict[int, int] = {}
        self._size: dict[int, int] = {}
        self._extremes: dict[int, tuple[float, float]] = {}
        self.edge_count = 0

    def add_edge(self, node_a: int, node_b: int, likeness: float) -> None:
        """Fold one likeness edge into the forest."""
        self.edge_count += 1
        root_a = self._find(self._ensure(node_a))
        root_b = self._find(self._ensure(node_b))
        if root_a != root_b:
            if self._size[root_a] < self._size[root_b]:
                root_a, root_b = root_b, root_a
            self._parent[root_b] = root_a
            self._size[root_a] += self._size[root_b]
            low_b, high_b = self._extremes.pop(root_b)
            low_a, high_a = self._extremes[root_a]
            self._extremes[root_a] = (min(low_a, low_b), max(high_a, high_b))
        root = root_a
        low, high = self._extremes[root]
        self._extremes[root] = (min(low, likeness), max(high, likeness))

    def components(self, min_size: int) -> list[tuple[list[int], float, float]]:
        """Return ``(member_ids, likeness_min, likeness_max)`` per component.

        Components smaller than *min_size* are dropped. Members are sorted by id
        and the components are ordered by their lowest member id, so a report is
        reproducible for an unchanged vault.
        """
        by_root: dict[int, list[int]] = defaultdict(list)
        for node in self._parent:
            by_root[self._find(node)].append(node)
        result: list[tuple[list[int], float, float]] = []
        for root, members in by_root.items():
            if len(members) < min_size:
                continue
            low, high = self._extremes.get(root, (0.0, 0.0))
            result.append((sorted(members), low, high))
        result.sort(key=lambda item: item[0][0])
        return result

    def _ensure(self, node: int) -> int:
        if node not in self._parent:
            self._parent[node] = node
            self._size[node] = 1
            self._extremes[node] = (float("inf"), float("-inf"))
        return node

    def _find(self, node: int) -> int:
        root = node
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[node] != root:
            self._parent[node], node = root, self._parent[node]
        return root


def member_order_key(meta: SweepMember) -> tuple[float, float, float, int]:
    """Canonical stack order: score DESC, smart score DESC, recency DESC, id ASC.

    Mirrors ``routes/stacks.py::_stack_order_key`` and the grid's
    ``compareStackOrder``, so a sweep-built stack leads with the same picture the
    grid would have led with. The first element of the sorted list is the keeper.
    """
    created_at = meta.created_at
    created_ts = created_at.timestamp() if isinstance(created_at, datetime) else 0.0
    return (
        -float(meta.score or 0),
        -float(meta.smart_score or 0.0),
        -created_ts,
        int(meta.id),
    )


def stream_likeness_edges(
    session: Session, likeness_threshold: float, page_size: int = EDGE_PAGE_SIZE
) -> Iterable[tuple[int, int, float]]:
    """Yield ``(picture_id_a, picture_id_b, likeness)`` above *likeness_threshold*.

    Keyset-paginated on the ``(picture_id_a, picture_id_b)`` primary key so pages
    never overlap or skip, and joined to :class:`~pixlstash.db_models.picture.Picture`
    on both endpoints so scrapheap (soft-deleted) pictures are excluded in SQL
    rather than by loading the whole live-id set into memory. The sweep is
    deliberately blind to the scrapheap: resolution stacks pictures, and stacking
    something the user already threw away is not a cleanup.
    """
    last_a = -1
    last_b = -1
    while True:
        rows = likeness_edge_page_in_session(
            session,
            likeness_threshold,
            after=(last_a, last_b),
            page_size=page_size,
        )
        if not rows:
            return
        for row in rows:
            yield row
        last_a, last_b = rows[-1][0], rows[-1][1]
        if len(rows) < page_size:
            return


def likeness_edge_page_in_session(
    session: Session,
    likeness_threshold: float,
    *,
    after: tuple[int, int] = (-1, -1),
    page_size: int = EDGE_PAGE_SIZE,
) -> list[tuple[int, int, float]]:
    """Return one keyset page of live likeness edges.

    The streaming scan task calls this once per queued database slice so a large
    edge table cannot monopolise the single writer thread.  The cursor and rows
    are plain Python values: no ORM object or open session crosses the slice
    boundary.
    """
    last_a, last_b = int(after[0]), int(after[1])
    pic_a = aliased(Picture)
    pic_b = aliased(Picture)
    statement = (
        select(
            PictureLikeness.picture_id_a,
            PictureLikeness.picture_id_b,
            PictureLikeness.likeness,
        )
        .join(pic_a, pic_a.id == PictureLikeness.picture_id_a)
        .join(pic_b, pic_b.id == PictureLikeness.picture_id_b)
        .where(
            PictureLikeness.likeness >= likeness_threshold,
            pic_a.deleted.is_(False),
            pic_b.deleted.is_(False),
            or_(
                PictureLikeness.picture_id_a > last_a,
                and_(
                    PictureLikeness.picture_id_a == last_a,
                    PictureLikeness.picture_id_b > last_b,
                ),
            ),
        )
        .order_by(PictureLikeness.picture_id_a, PictureLikeness.picture_id_b)
        .limit(max(1, int(page_size)))
    )
    return [
        (int(row[0]), int(row[1]), float(row[2] or 0.0))
        for row in session.exec(statement).all()
    ]


def plan_sweep_in_session(
    session: Session,
    policy: Optional[SweepPolicy] = None,
    operation_batch_id: Optional[str] = None,
) -> SweepReport:
    """Resolve every near-duplicate group in the vault and return the dry-run plan.

    Read-only: no row is written, created, or deleted. See the module docstring
    for the algorithm and :class:`SweepPolicy` for the knobs.

    Args:
        session: Pre-opened DB session (the ``*_in_session`` service contract).
        policy: Confidence policy; the defaults when omitted.
        operation_batch_id: Optional operation-log batch id to correlate this plan
            with. Inert for a dry run - echoed into the report, never written.

    Returns:
        The :class:`SweepReport` for the whole vault.
    """
    policy = policy or SweepPolicy()

    forest = _LikenessForest()
    for picture_id_a, picture_id_b, likeness in stream_likeness_edges(
        session, policy.likeness_threshold
    ):
        forest.add_edge(picture_id_a, picture_id_b, likeness)

    components = forest.components(policy.min_group_size)
    logger.info(
        "[dedup-sweep] %d edges >= %.5f produced %d candidate group(s)",
        forest.edge_count,
        policy.likeness_threshold,
        len(components),
    )

    meta_by_id = _load_member_meta(
        session, {pid for members, _, _ in components for pid in members}
    )
    meta_by_id.update(_load_stack_siblings(session, meta_by_id))
    members_by_stack: dict[int, set[int]] = defaultdict(set)
    for meta in meta_by_id.values():
        if meta.stack_id is not None:
            members_by_stack[int(meta.stack_id)].add(meta.id)

    groups: list[SweepGroup] = []
    outcome_counts: dict[str, int] = defaultdict(int)
    reason_counts: dict[str, int] = defaultdict(int)
    assigned_ids: set[int] = set()
    skip_counts: dict[_SkipReason, int] = defaultdict(int)

    for member_ids, likeness_min, likeness_max in components:
        group = _resolve_group(
            index=len(groups),
            member_ids=member_ids,
            likeness_min=likeness_min,
            likeness_max=likeness_max,
            policy=policy,
            meta_by_id=meta_by_id,
            members_by_stack=members_by_stack,
            assigned_ids=assigned_ids,
        )
        if isinstance(group, _SkipReason):
            skip_counts[group] += 1
            continue
        assigned_ids.update(group.picture_ids)
        groups.append(group)
        outcome_counts[group.outcome.value] += 1
        for reason in group.reasons:
            reason_counts[reason.value] += 1

    auto = [g for g in groups if g.verdict is SweepVerdict.AUTO_COLLAPSE]
    review = [g for g in groups if g.verdict is SweepVerdict.NEEDS_REVIEW]
    listed = groups[: policy.max_groups_listed]
    report = SweepReport(
        policy=policy.as_dict(),
        operation_batch_id=operation_batch_id,
        generated_at=datetime.utcnow(),
        scanned_edges=forest.edge_count,
        candidate_groups=len(components),
        already_collapsed_groups=skip_counts[_SkipReason.ALREADY_COLLAPSED],
        absorbed_groups=skip_counts[_SkipReason.ABSORBED],
        groups_total=len(groups),
        auto_collapse_groups=len(auto),
        needs_review_groups=len(review),
        auto_collapse_pictures=sum(len(g.picture_ids) for g in auto),
        needs_review_pictures=sum(len(g.picture_ids) for g in review),
        outcome_counts=dict(outcome_counts),
        reason_counts=dict(reason_counts),
        held_bytes_auto=sum(g.held_bytes for g in auto),
        held_bytes_review=sum(g.held_bytes for g in review),
        groups=listed,
        listing_truncated=len(listed) < len(groups),
    )
    logger.info(
        "[dedup-sweep] %d group(s) auto-collapse, %d need review "
        "(%d already collapsed, batch=%s)",
        report.auto_collapse_groups,
        report.needs_review_groups,
        report.already_collapsed_groups,
        operation_batch_id,
    )
    return report


def plan_near_duplicate_sweep(
    vault: "Vault",
    policy: Optional[SweepPolicy] = None,
    operation_batch_id: Optional[str] = None,
) -> SweepReport:
    """Vault wrapper around :func:`plan_sweep_in_session` (read-only).

    Args:
        vault: The vault owning the database.
        policy: Confidence policy; the defaults when omitted.
        operation_batch_id: The Lane-B operation-log batch id seam - echoed into
            the report, never written by a dry run.
    """
    return vault.db.run_immediate_read_task(
        plan_sweep_in_session, policy, operation_batch_id
    )


def _load_member_meta(
    session: Session, picture_ids: set[int]
) -> dict[int, SweepMember]:
    """Load the ranking columns for *picture_ids* in bound-variable-safe chunks."""
    if not picture_ids:
        return {}
    ordered = sorted(picture_ids)
    meta_by_id: dict[int, SweepMember] = {}
    for start in range(0, len(ordered), ID_CHUNK):
        chunk = ordered[start : start + ID_CHUNK]
        rows = session.exec(
            select(
                Picture.id,
                Picture.stack_id,
                Picture.score,
                Picture.smart_score,
                Picture.created_at,
                Picture.size_bytes,
            ).where(Picture.id.in_(chunk), Picture.deleted.is_(False))
        ).all()
        for row in rows:
            meta_by_id[int(row[0])] = SweepMember(
                id=int(row[0]),
                stack_id=int(row[1]) if row[1] is not None else None,
                score=row[2],
                smart_score=row[3],
                created_at=row[4],
                size_bytes=row[5],
            )
    return meta_by_id


def _load_stack_siblings(
    session: Session, meta_by_id: dict[int, SweepMember]
) -> dict[int, SweepMember]:
    """Load the *other* members of every stack a candidate already belongs to.

    Stacks move as a unit, so a group touching one member of a stack proposes an
    action on the whole stack. Loading the siblings up front is what lets the
    resolution report an honest member list and an honest ``held_bytes``.
    """
    stack_ids = sorted(
        {
            int(meta.stack_id)
            for meta in meta_by_id.values()
            if meta.stack_id is not None
        }
    )
    if not stack_ids:
        return {}
    siblings: dict[int, SweepMember] = {}
    for start in range(0, len(stack_ids), ID_CHUNK):
        chunk = stack_ids[start : start + ID_CHUNK]
        rows = session.exec(
            select(
                Picture.id,
                Picture.stack_id,
                Picture.score,
                Picture.smart_score,
                Picture.created_at,
                Picture.size_bytes,
            ).where(Picture.stack_id.in_(chunk), Picture.deleted.is_(False))
        ).all()
        for row in rows:
            picture_id = int(row[0])
            if picture_id in meta_by_id:
                continue
            siblings[picture_id] = SweepMember(
                id=picture_id,
                stack_id=int(row[1]) if row[1] is not None else None,
                score=row[2],
                smart_score=row[3],
                created_at=row[4],
                size_bytes=row[5],
            )
    return siblings


def _resolve_group(
    *,
    index: int,
    member_ids: list[int],
    likeness_min: float,
    likeness_max: float,
    policy: SweepPolicy,
    meta_by_id: dict[int, SweepMember],
    members_by_stack: dict[int, set[int]],
    assigned_ids: set[int],
) -> SweepGroup | _SkipReason:
    """Turn one connected component into a :class:`SweepGroup`, or say why not.

    A :class:`_SkipReason` return means the component produced no proposal. Both
    reasons are counted in the report - nothing is dropped silently, which is the
    whole point of promoting this out of the client.
    """
    component_ids = set(member_ids)
    linked_ids: set[int] = set()
    expanded: set[int] = set()
    for picture_id in member_ids:
        meta = meta_by_id.get(picture_id)
        if meta is None:
            # Soft-deleted between the edge scan and the metadata load, or an
            # orphaned likeness row. Skipping is correct; log it so a systematic
            # gap between the two queries is visible rather than silent.
            logger.debug(
                "[dedup-sweep] group %d: picture %d has no live metadata; skipping",
                index,
                picture_id,
            )
            continue
        expanded.add(picture_id)
        if meta.stack_id is not None:
            siblings = members_by_stack.get(int(meta.stack_id), set())
            linked_ids.update(siblings - component_ids)
            expanded.update(siblings)

    expanded -= assigned_ids
    linked_ids &= expanded
    members = [meta_by_id[pid] for pid in sorted(expanded) if pid in meta_by_id]
    if len(members) < policy.min_group_size:
        return _SkipReason.ABSORBED

    stack_ids = sorted({int(m.stack_id) for m in members if m.stack_id is not None})
    unstacked = [m for m in members if m.stack_id is None]
    if len(stack_ids) == 1 and not unstacked:
        return _SkipReason.ALREADY_COLLAPSED

    members.sort(key=member_order_key)
    keeper = members[0]
    runner_up = members[1]
    keeper_margin, keeper_margin_basis, keeper_reason = evaluate_keeper_margin(
        keeper, runner_up, policy
    )

    reasons: list[ReviewReason] = []
    if len(stack_ids) >= 2:
        outcome = SweepOutcome.MERGE_STACKS
        target_stack_id = (
            int(keeper.stack_id) if keeper.stack_id is not None else stack_ids[0]
        )
        merged_stack_ids = [sid for sid in stack_ids if sid != target_stack_id]
        if policy.cross_stack is CrossStackPolicy.REPORT:
            reasons.append(ReviewReason.SPANS_MULTIPLE_STACKS)
    elif len(stack_ids) == 1:
        outcome = SweepOutcome.ADD_TO_STACK
        target_stack_id = stack_ids[0]
        merged_stack_ids = []
    else:
        outcome = SweepOutcome.CREATE_STACK
        target_stack_id = None
        merged_stack_ids = []

    if len(members) > policy.max_auto_group_size:
        reasons.append(ReviewReason.OVERSIZED_GROUP)
    if likeness_min < policy.auto_resolve_likeness:
        reasons.append(ReviewReason.WEAK_LIKENESS)
    if keeper_reason is not None:
        reasons.append(keeper_reason)

    return SweepGroup(
        index=index,
        picture_ids=[m.id for m in members],
        keeper_id=keeper.id,
        verdict=SweepVerdict.NEEDS_REVIEW if reasons else SweepVerdict.AUTO_COLLAPSE,
        reasons=reasons,
        outcome=outcome,
        target_stack_id=target_stack_id,
        merged_stack_ids=merged_stack_ids,
        likeness_min=round(float(likeness_min), 6),
        likeness_max=round(float(likeness_max), 6),
        keeper_margin=keeper_margin,
        keeper_margin_basis=keeper_margin_basis,
        held_bytes=sum(int(m.size_bytes or 0) for m in members[1:]),
        linked_member_ids=sorted(linked_ids),
    )


def _keeper_margin(
    keeper: SweepMember, runner_up: SweepMember
) -> tuple[Optional[float], str, Optional[ReviewReason]]:
    """How clearly the keeper beats the runner-up, and whether that is enough.

    The human ``score`` is decisive when it separates the two - a picture the user
    rated higher is the keeper regardless of any model signal. Only on a score tie
    does the smart-score margin decide, and an unmeasurable margin (either picture
    has no stored smart score) is reported as such rather than treated as zero.

    Returns:
        ``(margin, basis, reason)`` where *reason* is ``None`` when the keeper is
        unambiguous. The caller compares *margin* against the policy.
    """
    keeper_score = keeper.score or 0
    runner_score = runner_up.score or 0
    if keeper_score != runner_score:
        return float(keeper_score - runner_score), "score", None
    if keeper.smart_score is None or runner_up.smart_score is None:
        return None, "none", ReviewReason.KEEPER_SMART_SCORE_MISSING
    margin = float(keeper.smart_score) - float(runner_up.smart_score)
    return margin, "smart_score", None


def evaluate_keeper_margin(
    keeper: SweepMember, runner_up: SweepMember, policy: SweepPolicy
) -> tuple[Optional[float], str, Optional[ReviewReason]]:
    """:func:`_keeper_margin` plus the policy's margin gate.

    Split out from :func:`_keeper_margin` so the "is it big enough" decision is
    testable independently of how the margin is measured. A
    ``smart_score_margin`` of 0 switches the whole smart-score axis off - an
    unmeasurable margin then stops being a review reason too, because the caller
    has said the signal is not a gate.
    """
    margin, basis, reason = _keeper_margin(keeper, runner_up)
    if policy.smart_score_margin <= 0:
        return margin, basis, None
    if reason is None and basis == "smart_score" and margin is not None:
        if margin < policy.smart_score_margin:
            reason = ReviewReason.AMBIGUOUS_KEEPER
    return margin, basis, reason


__all__ = [
    "CrossStackPolicy",
    "evaluate_keeper_margin",
    "ReviewReason",
    "SweepGroup",
    "SweepMember",
    "SweepOutcome",
    "SweepPolicy",
    "SweepReport",
    "SweepVerdict",
    "member_order_key",
    "plan_near_duplicate_sweep",
    "plan_sweep_in_session",
    "stream_likeness_edges",
]
