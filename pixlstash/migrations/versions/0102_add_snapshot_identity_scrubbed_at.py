"""Record per-archive progress for the one-time portable-identity scrub.

The scrub rewrites every historical snapshot so a library carries no owner
identity. It ran as one all-or-nothing loop: the "done" marker only advanced
after the last archive, so an interruption threw away every completed archive
and started again from the first. On a library with a real snapshot history
(22 archives / 5.7 GB measured) that is minutes of blocking startup, and a user
who interrupts it can never finish it.

This column is the resume point. It is written and committed per archive, so a
restart skips what is already scrubbed.

NULL means "not scrubbed". That is correct for both directions: existing rows
have not been scrubbed yet, and snapshots created after the migration never
carried vault-side owner identity, so they are never scrubbed and keep NULL.

Revision ID: 0102_add_snapshot_identity_scrubbed_at
Revises: 0101_add_settings_fingerprint
Create Date: 2026-08-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0102_add_snapshot_identity_scrubbed_at"
down_revision: Union[str, None] = "0101_add_settings_fingerprint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "snapshot" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("snapshot")}
    if "identity_scrubbed_at" not in existing_cols:
        op.add_column(
            "snapshot",
            sa.Column("identity_scrubbed_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "snapshot" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("snapshot")}
    if "identity_scrubbed_at" in existing_cols:
        op.drop_column("snapshot", "identity_scrubbed_at")
