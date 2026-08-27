"""Add the external-move reconciliation queue.

v1.11 Phase 5, reconciling moves made outside PixlStash
(``docs/plans/v1.11.0-existing-library.md`` §4). The mirror of Phase 4b's move
engine: ``external_move_review`` holds one row per file the reference-folder
scan found moved that the move journal (``picture_move``, 0108) did not claim
as PixlStash's own. Empty on every existing database - nothing is reconciled
until the next scan runs.

Revision ID: 0109_add_external_move_review
Revises: 0108_add_library_layout_and_move_journal
Create Date: 2026-08-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0109_add_external_move_review"
down_revision: Union[str, None] = "0108_add_library_layout_and_move_journal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "external_move_review" not in inspector.get_table_names():
        op.create_table(
            "external_move_review",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("picture_id", sa.Integer(), nullable=False),
            sa.Column("old_path", sa.String(), nullable=False),
            sa.Column("new_path", sa.String(), nullable=False),
            sa.Column("detected_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_external_move_review_picture_id",
            "external_move_review",
            ["picture_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "external_move_review" in inspector.get_table_names():
        op.drop_table("external_move_review")
