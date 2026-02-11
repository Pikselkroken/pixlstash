"""add picture likeness queue

Revision ID: f5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-02-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f5b6c7d8e9f0"
down_revision: Union[str, None] = "f4a5b6c7d8e9"
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
    if "picturelikenessqueue" not in tables:
        op.create_table(
            "picturelikenessqueue",
            sa.Column(
                "picture_id",
                sa.Integer(),
                sa.ForeignKey("picture.id", ondelete="CASCADE"),
                primary_key=True,
                nullable=False,
            ),
            sa.Column("queued_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_picture_likeness_queue_queued_at",
            "picturelikenessqueue",
            ["queued_at"],
            unique=False,
        )


def downgrade() -> None:
    conn = op.get_bind()
    tables = {
        row[0]
        for row in conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    }
    if "picturelikenessqueue" in tables:
        op.drop_index(
            "ix_picture_likeness_queue_queued_at", table_name="picturelikenessqueue"
        )
        op.drop_table("picturelikenessqueue")
