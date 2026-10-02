"""Add the manual workflow a picture was made by (manual workflows, PR 2c).

``picture.run_workflow_id`` names the manual workflow (``manual:<uuid>``, a hub
row) whose run made the picture. NULL for every existing picture, which is
right: none was made by a manual workflow, since they did not exist.

Revision ID: 0126_add_picture_run_workflow_id
Revises: 0125_refile_recipes_off_hand_made_workflows
Create Date: 2026-09-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0126_add_picture_run_workflow_id"
down_revision: Union[str, None] = "0125_refile_recipes_off_hand_made_workflows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_INDEX = "ix_picture_run_workflow_id"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "run_workflow_id" not in existing_cols:
        op.add_column(
            "picture", sa.Column("run_workflow_id", sa.String(), nullable=True)
        )
    # Declared on the model too, so a fresh database already has it from the
    # baseline. Guarded by name so both paths converge on the same index.
    if _INDEX not in {idx["name"] for idx in inspector.get_indexes("picture")}:
        op.create_index(_INDEX, "picture", ["run_workflow_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        return
    if _INDEX in {idx["name"] for idx in inspector.get_indexes("picture")}:
        op.drop_index(_INDEX, table_name="picture")
    if "run_workflow_id" in {col["name"] for col in inspector.get_columns("picture")}:
        op.drop_column("picture", "run_workflow_id")
