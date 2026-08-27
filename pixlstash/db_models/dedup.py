"""Persistent state for the tiered duplicate detection queue (v1.9).

Three concerns, three tables:

* :class:`DedupGroup` / :class:`DedupGroupMember` - the **found groups cache**.
  Detection writes groups here as it finds them, so the queue is paged from the
  database by confidence descending and never materialised whole in memory (the
  design's "10 groups and 10,000 perform identically" rule). Without this cache a
  "page 40 of the queue" request would have to redo the whole scan.
* :class:`DedupVerdict` - the **verdict memory**. Keyed on a group *signature*
  (see :func:`pixlstash.services.dedup_tier_service.group_signature`), not on
  group or picture ids, so a rescan or a re-import that produces the same set of
  files finds the same signature and never re-asks. "Keep separate" is permanent
  until the user reopens it.
* :class:`DedupScan` - the **scan progress** behind the "scanned N of M" banner
  and the scoped-scan entry points, one row per scope key.

Nothing here stores pixels, and no row in this module ever causes a delete: a
verdict is either a stack (additive) or a "keep separate" note.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlmodel import Field, SQLModel

# ``DedupGroup.tier`` / scan tier values. Ordered loosest-last: tier 1 is exact
# and always on, each looser tier is an opt-in that requires the tier above it.
TIER_EXACT = "exact"
TIER_NEAR = "near"
TIER_EMBEDDING = "embedding"

# ``DedupVerdict.verdict`` values. The only two verdicts in 1.9 - there is no
# deletion verdict anywhere in this release.
VERDICT_STACKED = "stacked"
VERDICT_KEEP_SEPARATE = "keep_separate"

# ``DedupScan.status`` values.
SCAN_PENDING = "pending"
SCAN_RUNNING = "running"
SCAN_COMPLETE = "complete"
SCAN_PARTIAL = "partial"
SCAN_FAILED = "failed"


class DedupGroup(SQLModel, table=True):
    """One detected duplicate group awaiting (or carrying) a verdict.

    A group is identified by its :attr:`signature` rather than its id, so the
    same set of files always maps to the same row across rescans. Detection
    upserts on the signature; the queue pages on ``(resolved, confidence DESC)``.

    Attributes:
        signature: Stable hash of the sorted member content keys. Unique.
        tier: Which tier found it (:data:`TIER_EXACT` / :data:`TIER_NEAR` /
            :data:`TIER_EMBEDDING`). Exact is always shown; the looser tiers are
            filtered by the caller's :class:`~pixlstash.services.dedup_tier_service.TierPolicy`.
        confidence: 1.0 for an exact match, otherwise the group's weakest
            pairwise similarity - the value the queue sorts by, descending.
        member_count: Number of members, denormalised so the queue page does not
            need a join to render "Stack 3".
        cover_picture_id: The server's cover preselection (never silent - the
            client shows it and the user may override with 1-9).
        evidence: JSON ``[[text, against_bool], ...]`` - the why-pills. Stored
            with the group because it describes *this* grouping, and recomputing
            it would need the member metadata the queue page is trying to avoid
            loading for every group.
        resolved: True once a verdict covers this signature. The sidebar badge
            counts ``resolved IS FALSE``.
        scan_id: The :class:`DedupScan` that produced it, or NULL for a group
            found outside a tracked scan.
    """

    __tablename__ = "dedupgroup"

    id: Optional[int] = Field(default=None, primary_key=True)
    signature: str = Field(
        sa_column=Column("signature", String, nullable=False, unique=True, index=True)
    )
    tier: str = Field(sa_column=Column("tier", String, nullable=False, index=True))
    confidence: float = Field(default=0.0, index=True)
    member_count: int = Field(default=0)
    cover_picture_id: Optional[int] = Field(default=None)
    evidence: Optional[str] = Field(
        default=None, sa_column=Column("evidence", String, nullable=True)
    )
    resolved: bool = Field(default=False, index=True)
    scan_id: Optional[int] = Field(default=None, index=True)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column("created_at", DateTime, nullable=False),
    )

    __table_args__ = (
        # The queue's only hot query: unresolved groups, confidence descending.
        Index("ix_dedupgroup_queue", "resolved", "confidence"),
    )


class DedupGroupMember(SQLModel, table=True):
    """Membership of a picture in a :class:`DedupGroup`.

    ``picture_id`` is indexed because the scoped counts (project / set /
    character / folder context menus) and the invalidation path both start from
    a picture id set and ask which groups it touches.
    """

    __tablename__ = "dedupgroupmember"

    group_id: int = Field(
        sa_column=Column(
            "group_id",
            Integer,
            ForeignKey("dedupgroup.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        )
    )
    picture_id: int = Field(
        sa_column=Column(
            "picture_id",
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        )
    )
    position: int = Field(default=0)


class DedupVerdict(SQLModel, table=True):
    """A remembered decision about one group signature.

    This is what makes the sidebar count trustworthy: a rescan re-derives the
    same signature and resolves the group immediately instead of asking again.
    A "keep separate" verdict is permanent until :attr:`reopened_at` is stamped
    from the Stacks view.

    Attributes:
        signature: The group signature this verdict covers. Unique.
        verdict: :data:`VERDICT_STACKED` or :data:`VERDICT_KEEP_SEPARATE`.
        picture_ids: JSON list of the member ids the verdict was made on, for
            the audit trail and for the reopen path.
        excluded_picture_ids: JSON list of members the user left out of the
            stack (the design's X key). Empty for keep-separate.
        cover_picture_id: The cover the user confirmed or chose.
        stack_id: The stack the members landed in, for a ``stacked`` verdict.
        batch_id: The operation-log batch this verdict was recorded under; bulk
            auto-stack shares one batch id across every group so N stacks
            reverse with a single undo.
        reopened_at: When the user reopened the decision. A reopened verdict no
            longer resolves its group, so the group returns to the queue.
        reopen_batch_id: The operation-log batch of the most recent
            picture-touching **clear** of this verdict (a clear of a `stacked`
            verdict dissolves the verdict's stack and records one
            `dedup.reopen` operation). This is the correlation the
            undo-of-clear post-restore hook needs to re-mark the verdict
            decided; :attr:`batch_id` keeps pointing at the verdict's own
            operation, so undoing the original stack still finds its verdict.
            NULL until a clear has touched pictures.
    """

    __tablename__ = "dedupverdict"

    id: Optional[int] = Field(default=None, primary_key=True)
    signature: str = Field(
        sa_column=Column("signature", String, nullable=False, unique=True, index=True)
    )
    verdict: str = Field(
        sa_column=Column("verdict", String, nullable=False, index=True)
    )
    picture_ids: str = Field(
        default="[]", sa_column=Column("picture_ids", String, nullable=False)
    )
    excluded_picture_ids: str = Field(
        default="[]", sa_column=Column("excluded_picture_ids", String, nullable=False)
    )
    cover_picture_id: Optional[int] = Field(default=None)
    stack_id: Optional[int] = Field(default=None, index=True)
    batch_id: Optional[str] = Field(
        default=None, sa_column=Column("batch_id", String, nullable=True, index=True)
    )
    decided_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column("decided_at", DateTime, nullable=False),
    )
    reopened_at: Optional[datetime] = Field(
        default=None, sa_column=Column("reopened_at", DateTime, nullable=True)
    )
    reopen_batch_id: Optional[str] = Field(
        default=None,
        sa_column=Column("reopen_batch_id", String, nullable=True, index=True),
    )


class DedupScan(SQLModel, table=True):
    """Progress of one scan, keyed on its scope.

    One row per scope key ("global", "project:3", "set:9", ...), reused across
    rescans of that scope so the banner has a stable place to read from and a
    scoped scan can report "reused cached hashes" without inventing a new row
    every time.

    Attributes:
        scope_key: Canonical scope identifier. Unique.
        scanned_pictures / total_pictures: The "scanned N of M" banner.
        scanned_buckets / total_buckets: Tier-2 bucket progress; a bucket's
            groups become visible in the queue the moment its bucket finishes.
        tiers: JSON list of the tiers this scan was asked to run.
    """

    __tablename__ = "dedupscan"

    id: Optional[int] = Field(default=None, primary_key=True)
    scope_key: str = Field(
        sa_column=Column("scope_key", String, nullable=False, unique=True, index=True)
    )
    scope_type: str = Field(
        default="global", sa_column=Column("scope_type", String, nullable=False)
    )
    scope_id: Optional[str] = Field(
        default=None, sa_column=Column("scope_id", String, nullable=True)
    )
    tiers: str = Field(default="[]", sa_column=Column("tiers", String, nullable=False))
    threshold: float = Field(default=0.9)
    status: str = Field(
        default=SCAN_PENDING,
        sa_column=Column("status", String, nullable=False, index=True),
    )
    total_pictures: int = Field(default=0)
    scanned_pictures: int = Field(default=0)
    total_buckets: int = Field(default=0)
    scanned_buckets: int = Field(default=0)
    groups_found: int = Field(default=0)
    error: Optional[str] = Field(
        default=None, sa_column=Column("error", String, nullable=True)
    )
    started_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column("started_at", DateTime, nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column("updated_at", DateTime, nullable=False),
    )
    finished_at: Optional[datetime] = Field(
        default=None, sa_column=Column("finished_at", DateTime, nullable=True)
    )


__all__ = [
    "DedupGroup",
    "DedupGroupMember",
    "DedupScan",
    "DedupVerdict",
    "SCAN_COMPLETE",
    "SCAN_FAILED",
    "SCAN_PARTIAL",
    "SCAN_PENDING",
    "SCAN_RUNNING",
    "TIER_EMBEDDING",
    "TIER_EXACT",
    "TIER_NEAR",
    "VERDICT_KEEP_SEPARATE",
    "VERDICT_STACKED",
]
