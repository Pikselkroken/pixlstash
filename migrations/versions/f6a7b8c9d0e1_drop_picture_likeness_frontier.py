"""drop picture likeness frontier

Revision ID: f6a7b8c9d0e1
Revises: f5b6c7d8e9f0
Create Date: 2026-02-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "f5b6c7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = {
        row[0]
        for row in conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    }
    if "picturelikenessfrontier" in tables:
        op.drop_index("ix_picture_frontier_a", table_name="picturelikenessfrontier")
        op.drop_table("picturelikenessfrontier")


def downgrade() -> None:
    conn = op.get_bind()
    tables = {
        row[0]
        for row in conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    }
    if "picturelikenessfrontier" not in tables:
        op.create_table(
            "picturelikenessfrontier",
            sa.Column(
                "picture_id_a",
                sa.Integer(),
                sa.ForeignKey("picture.id", ondelete="CASCADE"),
                primary_key=True,
                nullable=False,
            ),
            sa.Column("j_max", sa.Integer(), nullable=False),
        )
        op.create_index(
            "ix_picture_frontier_a",
            "picturelikenessfrontier",
            ["picture_id_a"],
            unique=False,
        )
