from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class Snapshot(SQLModel, table=True):
    """A full SQLite snapshot of the vault database, retained under GFS policy.

    Attributes:
        id: Auto-increment primary key.
        kind: Retention tier: 'DAILY', 'WEEKLY', 'MONTHLY', 'MANUAL', or
            'OPPORTUNISTIC'.
        created_at: UTC timestamp when the snapshot was taken.
        relative_path: Path to the snapshot .sqlite file relative to the
            vault root (e.g. 'snapshots/2026/01/15/<uuid>.sqlite').
        manifest_relative_path: Path to the JSON sidecar relative to the
            vault root.
        byte_size: Size of the snapshot file in bytes.
        picture_count: Number of Picture rows at snapshot time.
        schema_version: Alembic head revision at snapshot time.
        label: Optional user-supplied label for MANUAL snapshots.
        identity_scrubbed_at: UTC timestamp when the one-time portable-identity
            scrub rewrote this archive, or None if it has not been scrubbed.
            Written per archive so an interrupted migration resumes rather than
            restarting; see
            ``pixlstash.services.portable_identity.sanitize_historical_snapshots``.
            Stays None on snapshots created after the migration, which never
            carried vault-side owner identity and are never scrubbed.
    """

    __tablename__ = "snapshot"

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
    identity_scrubbed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column("identity_scrubbed_at", DateTime, nullable=True),
    )
