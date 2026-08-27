from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class Review(SQLModel, table=True):
    """A review session: one tag + an optional frozen scope + one scan's results.

    The first-class noun of the tag-review workflow (see
    ``docs/reviews/2026-07-review-sessions-redesign-draft.md``). A review is
    created explicitly, runs the near-neighbour scan once, and its receipt
    (``scanned`` / ``found`` / ``prev_reviewed``) becomes the session's cover
    sheet. Suggestions produced by the scan carry this review's id
    (``TagSuggestion.review_id``); switching between reviews never rescans and
    never destroys rows.

    Scope (``project_id`` / ``set_id`` / ``character_id``) is frozen at
    creation - a different scope is a different review. ``character_id`` is a
    string because it may hold the literal ``"UNASSIGNED"`` besides numeric ids.

    Status lifecycle: ``OPEN`` → ``ARCHIVED`` (completed) or ``ABORTED``
    (discarded; per-item decisions already written through stand). A partial
    unique index enforces at most one OPEN review per tag.
    """

    __tablename__ = "review"

    id: Optional[int] = Field(default=None, primary_key=True)

    tag: str = Field(index=True)

    # Frozen scope, nullable - all None means "whole vault".
    project_id: Optional[int] = Field(default=None)
    set_id: Optional[int] = Field(default=None)
    character_id: Optional[str] = Field(default=None)  # numeric str or "UNASSIGNED"

    status: str = Field(default="OPEN", index=True)  # OPEN | ARCHIVED | ABORTED

    # Scan receipt ("Scanned 4,812 pictures · 23 suspects · 61 handled earlier").
    scanned: int = Field(default=0)
    found: int = Field(default=0)
    prev_reviewed: int = Field(default=0)

    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    refreshed_at: Optional[datetime] = Field(default=None)

    # Frozen ``{"receipt": {...}, "progress": {...}}`` JSON, written when the
    # review is closed (ARCHIVED/ABORTED). A closed review's receipt/progress
    # otherwise aggregate LIVE over its suggestion rows, so a later scan that
    # re-parents those rows into a new review would shrink this review's
    # historical cover sheet. Freezing on close makes it immutable. NULL for
    # OPEN reviews (served live) and for reviews closed before this column
    # existed (served live as a back-compat fallback).
    receipt_snapshot: Optional[str] = Field(
        default=None, sa_column=sa.Column(sa.Text(), nullable=True)
    )

    __table_args__ = (
        # One OPEN review per tag; archived/aborted history is unlimited.
        sa.Index(
            "uq_review_open_tag",
            "tag",
            unique=True,
            sqlite_where=sa.text("status = 'OPEN'"),
        ),
    )
