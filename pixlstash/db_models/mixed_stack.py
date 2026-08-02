"""Mixed stacks: the cohesion cache and the ``Keep`` dismissal (v1.9, D5/B5).

A **mixed stack** is a live stack whose members do not form one connected
cluster at the queue's similarity threshold, measured with the same 64-bit dHash
Hamming distance and the same connected-components test tier 2 already uses
(``docs/design/mixed-stacks-and-stack-units.md``, D5). Two tables carry it:

* :class:`StackCohesion`: the **cohesion cache**. Cohesion is *computed*, never
  a column on ``picture``; what is cached is the threshold-independent half of
  the computation (the near-pair edge list), so a threshold change re-folds
  cheap components instead of re-reading and re-comparing every hash. The cache
  is keyed on the stack's **membership fingerprint**, so a member joining or
  leaving invalidates it by construction rather than by remembering to.
* :class:`MixedStackDismissal`: the **Keep** dismissal. "This stack is fine,
  stop listing it", durable and server-side, keyed on stack id **plus** the
  membership fingerprint so adding a member later re-raises the stack.

Neither table stores pixels and neither ever causes a delete: the cache is
derived data that can be dropped and recomputed at any time, and a dismissal
only hides a row from one list.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel


class StackCohesion(SQLModel, table=True):
    """Cached near-pair edges of one live stack, for the mixed-stack score.

    One row per stack. The row stores the pairs whose Hamming distance is small
    enough to be an edge at *any* threshold the API accepts, which makes the
    row threshold-independent: the list endpoint folds components out of these
    edges at whatever threshold it was asked for, without touching ``picture``.

    **Validity is event-driven, not timestamp-driven.** Database triggers delete
    the row whenever membership, deletion state, or a perceptual hash changes.
    A present row is therefore exact however old it is, and a cache hit need not
    reread ``picture`` merely to prove that nothing moved. The content
    fingerprint remains an audit key for what the writer derived.

    **Its key is deliberately NOT the dismissal's key.** A ``Keep`` is keyed on
    *membership* (D5: adding a member re-raises the stack, and only that), but
    the edges depend on the member ids **and their perceptual hashes**. A hash
    can move without membership moving: the embedding worker filling a NULL, or
    a reference-folder file being replaced, and a membership-keyed cache would
    then serve edges derived from hashes that no longer exist, freezing a member
    as "stranded" forever. :attr:`content_fingerprint` therefore covers both,
    which is the only honest key for derived data.

    Attributes:
        stack_id: The stack this describes. Primary key, and a cascade FK, so a
            dissolved stack takes its cache with it.
        content_fingerprint: Digest of the stack's ``(member id, perceptual
            hash)`` pairs: every input the edge list is derived from. See
            ``mixed_stack_service.content_fingerprint``.
        member_count: Live member count at the time of the computation,
            denormalised so a staleness check needs no join.
        member_ids: JSON list of the live member ids, canonical stack order
            (leader first): the same order the deck renders in.
        unhashed_picture_ids: JSON list of members with no usable
            ``perceptual_hash``. They can carry no edge at all, so they would
            otherwise look identical to a genuinely stranded member; recording
            them lets the list say "not comparable yet" instead of "does not
            belong".
        edges: JSON ``[[picture_id_a, picture_id_b, hamming], ...]`` with
            ``a < b``, every pair at or below the widest admissible distance.
        nearest_edges: JSON ``[[picture_id, closest_picture_id, hamming], ...]``,
            one entry per member that has at least one comparable sibling: that
            member's **closest** sibling, at whatever distance it really is.
            Deliberately **not** filtered by the edge floor :attr:`edges` is
            pruned at. A pair further apart than that floor cannot be an edge at
            any admissible threshold, which is why dropping it from
            :attr:`edges` loses nothing; but it is still the honest answer to
            "how close does this member get?", and pruning it is what made a
            stranded member's closeness unreportable. One row per member, so the
            storage is O(n) beside an O(n^2) edge list.
        computed_at: When the edges were last derived. Diagnostics only, the
            fingerprint decides staleness.
    """

    __tablename__ = "stackcohesion"

    stack_id: int = Field(
        sa_column=Column(
            "stack_id",
            Integer,
            ForeignKey("picturestack.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    content_fingerprint: str = Field(
        sa_column=Column("content_fingerprint", String, nullable=False, index=True)
    )
    member_count: int = Field(default=0)
    member_ids: str = Field(
        default="[]", sa_column=Column("member_ids", String, nullable=False)
    )
    unhashed_picture_ids: str = Field(
        default="[]", sa_column=Column("unhashed_picture_ids", String, nullable=False)
    )
    edges: str = Field(default="[]", sa_column=Column("edges", String, nullable=False))
    nearest_edges: str = Field(
        default="[]",
        sa_column=Column("nearest_edges", String, nullable=False, server_default="[]"),
    )
    computed_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column("computed_at", DateTime, nullable=False),
    )


class MixedStackDismissal(SQLModel, table=True):
    """A ``Keep`` on one stack at one exact membership.

    Without this the legitimate-but-odd stacks (a burst where one frame panned
    off, a deliberate before/after pair) sit in the Mixed stacks list forever
    and the list becomes ignorable: which is the failure mode D5 names.

    **Keyed on membership, not just the stack.** Adding a member later produces
    a different fingerprint, no row matches, and the stack is raised again: the
    user approved *these* pictures together, not every future version of the
    stack. Rows are kept per fingerprint rather than overwritten per stack, so
    undoing the membership change restores the dismissal the user already made
    instead of asking a second time about a stack they have already judged.

    Attributes:
        stack_id: The stack that was kept. Cascade FK: dissolving the stack
            drops its dismissals, and a dissolved stack cannot be listed anyway.
        membership_fingerprint: The membership the user approved.
        member_count: Members at dismissal time, for the audit trail.
        dismissed_at: When ``Keep`` was pressed.
        actor: Who pressed it, from ``operation_log_service.request_context``.
    """

    __tablename__ = "mixedstackdismissal"

    id: Optional[int] = Field(default=None, primary_key=True)
    stack_id: int = Field(
        sa_column=Column(
            "stack_id",
            Integer,
            ForeignKey("picturestack.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    membership_fingerprint: str = Field(
        sa_column=Column("membership_fingerprint", String, nullable=False)
    )
    member_count: int = Field(default=0)
    dismissed_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column("dismissed_at", DateTime, nullable=False),
    )
    actor: Optional[str] = Field(
        default=None, sa_column=Column("actor", String, nullable=True)
    )

    __table_args__ = (
        # One dismissal per (stack, membership). Re-pressing Keep is idempotent
        # rather than a second row, and the list's "is this kept?" question is a
        # single indexed lookup.
        UniqueConstraint(
            "stack_id",
            "membership_fingerprint",
            name="uq_mixedstackdismissal_stack_fingerprint",
        ),
        Index(
            "ix_mixedstackdismissal_fingerprint",
            "membership_fingerprint",
        ),
    )


__all__ = ["MixedStackDismissal", "StackCohesion"]
