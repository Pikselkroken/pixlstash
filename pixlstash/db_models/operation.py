"""The append-only operation log - DAM 1.2's undo/redo substrate and audit log.

One :class:`Operation` row records one user-visible change: what changed, on
which targets, the *before* and *after* metadata state of those targets, who did
it, and where it came from (the WebSocket envelope's ``source`` /
``origin_client_id`` discipline, §15). Undo restores ``before_state``; redo
re-applies ``after_state``.

**Append-only.** Recorded content (``op_type`` / ``target_ids`` /
``before_state`` / ``after_state`` / ``actor`` / ``source`` / ``created_at``) is
written once and never rewritten - that is what makes this table usable as the
audit log and, later, the Studio activity feed (DAM roadmap §4.3). The only
mutable columns are the lifecycle markers ``status`` / ``undone_at``, which
*append* the fact that the operation was later reverted rather than erasing it.

**Batch id from day one.** ``batch_id`` groups several rows into one
user-visible action, so a bulk job (the v1.9 near-duplicate sweep, a future
dehydration pass) is undone as a single unit through one endpoint. It is present
in the schema from the first migration deliberately: retrofitting a grouping key
onto a log that already has rows is exactly the migration pain the additive-only
rule exists to avoid.
"""

from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

# Lifecycle of a recorded operation. ``APPLIED`` is live; ``UNDONE`` sits on the
# redo stack; ``SUPERSEDED`` is an undone operation whose redo was invalidated by
# a newer operation (the classic linear undo history). Only the marker moves -
# the recorded content never changes.
STATUS_APPLIED = "applied"
STATUS_UNDONE = "undone"
STATUS_SUPERSEDED = "superseded"
VALID_STATUSES = (STATUS_APPLIED, STATUS_UNDONE, STATUS_SUPERSEDED)

# The only target type today. Declared as a column (not assumed) because the
# audit log will later record set-, project- and model-shelf-level operations.
TARGET_PICTURE = "picture"


class Operation(SQLModel, table=True):
    """One recorded, potentially reversible change to library metadata.

    Attributes:
        id: Surrogate key; also the log's total order (monotonic per DB).
        batch_id: Opaque group id shared by every operation of one bulk action.
            ``None`` for a plain single-shot operation. Undoing any member of a
            batch undoes the whole batch.
        created_at: UTC timestamp the operation was recorded.
        actor: Who performed it - the user id as a string, or a service name for
            a background actor. ``None`` when unauthenticated context is absent.
        op_type: Dotted verb naming the change, e.g. ``"pictures.tags"``. Free
            text on purpose: the undo applier is driven by the recorded state,
            not by the verb, so a new op type needs no applier change.
        target_type: Kind of object the ids in ``target_ids`` refer to
            (:data:`TARGET_PICTURE` today).
        target_ids: JSON list of affected object ids.
        target_count: ``len(target_ids)``, denormalised so the activity feed can
            aggregate without parsing every payload.
        before_state: JSON ``{"<target_id>": {facet: value}}`` holding ONLY the
            facets this operation changed, as they were *before* it ran.
        after_state: The same shape, as the facets are *after* it ran.
        source: WS-envelope source (``"ui"`` / ``"external"``), carried
            explicitly from the request - never read from a contextvar.
        origin_client_id: WS-envelope per-tab origin, same discipline.
        undoable: ``True`` only for the metadata scope DAM 1.2 defines. A
            file-mutating operation is *recorded* (audit) but not reversible
            until copy-on-write versions exist, and is stored with ``False``.
        status: One of :data:`VALID_STATUSES`.
        undone_at: UTC timestamp of the undo, when it happened.
        summary: Short human sentence for the activity feed / undo toast.
    """

    __tablename__ = "operation"

    id: Optional[int] = Field(default=None, primary_key=True)

    batch_id: Optional[str] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    actor: Optional[str] = Field(default=None, index=True)

    op_type: str = Field(index=True)
    target_type: str = Field(default=TARGET_PICTURE)
    target_ids: str = Field(sa_column=sa.Column(sa.Text(), nullable=False))
    target_count: int = Field(default=0)

    before_state: Optional[str] = Field(
        default=None, sa_column=sa.Column(sa.Text(), nullable=True)
    )
    after_state: Optional[str] = Field(
        default=None, sa_column=sa.Column(sa.Text(), nullable=True)
    )

    source: str = Field(default="external")
    origin_client_id: Optional[str] = Field(default=None)

    undoable: bool = Field(default=False, index=True)
    status: str = Field(default=STATUS_APPLIED, index=True)
    undone_at: Optional[datetime] = Field(default=None)

    summary: Optional[str] = Field(default=None)

    __table_args__ = (
        # The undo/redo stacks are "newest row in this status, newest first".
        sa.Index("ix_operation_status_id", "status", "id"),
    )
