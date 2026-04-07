"""add url column to projectattachment

Revision ID: 0006_add_attachment_url
Revises: 0005_add_projects
Create Date: 2026-03-22 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_add_attachment_url"
down_revision: Union[str, None] = "0005_add_projects"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_columns = {
        col["name"] for col in inspector.get_columns("projectattachment")
    }
    if "url" not in existing_columns:
        with op.batch_alter_table("projectattachment") as batch_op:
            batch_op.add_column(sa.Column("url", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projectattachment") as batch_op:
        batch_op.drop_column("url")
