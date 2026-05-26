from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class Checkpoint(SQLModel, table=True):
    """A full SQLite snapshot of the vault database, retained under GFS policy.

    Attributes:
        id: Auto-increment primary key.
        kind: Retention tier: 'DAILY', 'WEEKLY', 'MONTHLY', 'MANUAL', or
            'OPPORTUNISTIC'.
        created_at: UTC timestamp when the snapshot was taken.
        relative_path: Path to the snapshot .sqlite file relative to the
            vault root (e.g. 'checkpoints/2026/01/15/<uuid>.sqlite').
        manifest_relative_path: Path to the JSON sidecar relative to the
            vault root.
        byte_size: Size of the snapshot file in bytes.
        picture_count: Number of Picture rows at snapshot time.
        schema_version: Alembic head revision at snapshot time.
        label: Optional user-supplied label for MANUAL checkpoints.
    """

    __tablename__ = "checkpoint"

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(nullable=False, index=True)
    created_at: datetime = Field(
        sa_column=Column("created_at", DateTime, index=True, nullable=False)
    )
    relative_path: str = Field(nullable=False)
    manifest_relative_path: str = Field(nullable=False)
    byte_size: int = Field(nullable=False)
    picture_count: int = Field(nullable=False)
    schema_version: str = Field(nullable=False)
    label: Optional[str] = Field(default=None)
