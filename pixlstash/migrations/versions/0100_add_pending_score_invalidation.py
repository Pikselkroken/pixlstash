"""Add ``pending_score_invalidation``, the durable "scores are owed a recompute" record.

Changing a penalised tag's weight used to save the setting and NULL the affected
smart scores in one transaction. Since the hub/vault split the setting lives in
the hub and the pictures in the vault, and SQLite cannot span a transaction
across two databases, so that guarantee is no longer available directly.

This table restores it indirectly: the record is written to the vault before the
setting is committed to the hub, and it is consumed together with the NULLing in
a single vault transaction. Both crash points then fail safe. See the model's
docstring for the full argument.

Revision ID: 0100_add_pending_score_invalidation
Revises: 0099_guest_tables_reference_token_public_id
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0100_add_pending_score_invalidation"
down_revision: Union[str, None] = "0099_guest_tables_reference_token_public_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "pending_score_invalidation" in inspector.get_table_names():
        return

    op.create_table(
        "pending_score_invalidation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tags", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "pending_score_invalidation" in inspector.get_table_names():
        op.drop_table("pending_score_invalidation")
