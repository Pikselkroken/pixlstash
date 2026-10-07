"""Add which version of its manual workflow a picture was made with.

``picture.run_workflow_version`` is the version number (hub
``workflow_version.version``) of ``run_workflow_id``'s document that made the
picture: the one a PixlStash Run submitted, or, for a picture made in ComfyUI,
the version whose content its embedded editor workflow equals. NULL for every
existing picture, which reads as "not known": no backfill, because which
version an old run used was never recorded. A version can later be pruned
from the hub (it keeps version 1 and the newest 49); the number still records
which one it was.

Schema-only and additive.

Revision ID: 0127_add_picture_run_workflow_version
Revises: 0126_add_picture_run_workflow_id
Create Date: 2026-10-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0127_add_picture_run_workflow_version"
down_revision: Union[str, None] = "0126_add_picture_run_workflow_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "run_workflow_version" not in existing_cols:
        op.add_column(
            "picture",
            sa.Column("run_workflow_version", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "run_workflow_version" in existing_cols:
        op.drop_column("picture", "run_workflow_version")
