"""Add guest_session and guest_score tables.

Revision ID: 0041_guest_scores
Revises: 0040_move_text_score_to_picture
Create Date: 2026-05-07 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0041_guest_scores"
down_revision: Union[str, None] = "0040_move_text_score_to_picture"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "guest_session" not in existing_tables:
        op.create_table(
            "guest_session",
            sa.Column("session_id", sa.String(64), primary_key=True, nullable=False),
            sa.Column(
                "token_id",
                sa.Integer,
                sa.ForeignKey("usertoken.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("last_active_at", sa.DateTime, nullable=False),
        )

    if "guest_score" not in existing_tables:
        op.create_table(
            "guest_score",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "session_id",
                sa.String(64),
                sa.ForeignKey("guest_session.session_id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "token_id",
                sa.Integer,
                sa.ForeignKey("usertoken.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "picture_id",
                sa.Integer,
                sa.ForeignKey("picture.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("score", sa.Integer, nullable=False),
            sa.Column("scored_at", sa.DateTime, nullable=False),
        )
        op.create_index(
            "uq_guest_score_session_picture",
            "guest_score",
            ["session_id", "picture_id"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "guest_score" in existing_tables:
        op.drop_table("guest_score")
    if "guest_session" in existing_tables:
        op.drop_table("guest_session")
