"""Add the tag_health cache table for the tag health board.

One row per tag with the board's aggregate signals (est_wrong, est_missing,
mismatch, verified_pct, boundary_pct, overturn_rate, model_disputes,
has_model), rebuilt on demand by the tag-health service. Pure cache - rows are
wholesale replaced on rebuild.

Revision ID: 0066_add_tag_health
Revises: 0065_add_review_sessions
Create Date: 2026-07-15 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0066_add_tag_health"
down_revision: Union[str, None] = "0065_add_review_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tag_health" in inspector.get_table_names():
        return

    op.create_table(
        "tag_health",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tag", sa.String(), nullable=False),
        sa.Column("est_wrong", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("est_missing", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mismatch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("boundary_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("overturn_rate", sa.Float(), nullable=True),
        sa.Column("model_disputes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "has_model", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("last_reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("computed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tag_health_tag", "tag_health", ["tag"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tag_health" in inspector.get_table_names():
        op.drop_table("tag_health")
