"""Add a ``locked`` boolean to the pictureset table.

A locked picture set is a hard, whole-set freeze of label data: while locked the
set's own fields cannot be edited, its membership cannot change, and the tag /
description / score / delete / review-decision data of any member picture is
read-only. Existing sets default to unlocked (``server_default=false``).

Revision ID: 0073_add_pictureset_locked
Revises: 0072_add_review_receipt_snapshot_and_suggestion_prior_decision
Create Date: 2026-07-18 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0073_add_pictureset_locked"
down_revision: Union[str, None] = (
    "0072_add_review_receipt_snapshot_and_suggestion_prior_decision"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "pictureset" not in inspector.get_table_names():
        # Fresh install - the baseline migration creates the table with all
        # current columns via SQLModel.metadata.create_all(); nothing to do.
        return

    existing_cols = {col["name"] for col in inspector.get_columns("pictureset")}

    if "locked" not in existing_cols:
        op.add_column(
            "pictureset",
            sa.Column(
                "locked",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "pictureset" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("pictureset")}

    if "locked" in existing_cols:
        op.drop_column("pictureset", "locked")
