from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlmodel import Field, SQLModel


class ChangeLog(SQLModel, table=True):
    """Records a single INSERT, UPDATE, or DELETE performed by the writer session.

    Attributes:
        id: Auto-increment primary key.
        txn_id: UUID shared by all rows produced in one writer-session task.
        seq_in_txn: 0-based ordering of entries within a single txn_id.
        table_name: SQLite table that was mutated.
        row_pk_json: JSON-encoded dict of the primary key column(s).
        op: 'INSERT', 'UPDATE', or 'DELETE'.
        before_json: JSON snapshot of column values before the change (NULL for
            INSERT and for excluded-table rows).
        after_json: JSON snapshot of column values after the change (NULL for
            DELETE and for excluded-table rows).
        created_at: UTC timestamp of the flush.
        actor_user_id: User who triggered the write (NULL when SET NULL on
            user deletion, or when no user context is present).
        reason: Human-readable label set via the write_reason() context manager.
    """

    __tablename__ = "changelog"

    id: Optional[int] = Field(default=None, primary_key=True)
    txn_id: str = Field(sa_column=Column(String, index=True, nullable=False))
    seq_in_txn: int = Field(nullable=False)
    table_name: str = Field(sa_column=Column(String, index=True, nullable=False))
    row_pk_json: str = Field(nullable=False)
    op: str = Field(nullable=False)
    before_json: Optional[str] = Field(default=None)
    after_json: Optional[str] = Field(default=None)
    created_at: datetime = Field(
        sa_column=Column("created_at", DateTime, index=True, nullable=False)
    )
    actor_user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    reason: Optional[str] = Field(default=None)
