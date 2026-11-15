"""Add ``character_sheet_draw``, the character sheet draws the dialog keeps.

One row per draw of character sheets on a character: its settings, runs,
sheets, kept and used sheets, and whether the owner has seen it. Schema-only
and additive.

Revision ID: 0146_add_character_sheet_draw
Revises: 0145_add_tag_suggestion_control_state
Create Date: 2026-11-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0146_add_character_sheet_draw"
down_revision: Union[str, None] = "0145_add_tag_suggestion_control_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = inspector.get_table_names()
    if "character_sheet_draw" in tables or "character" not in tables:
        # Partial databases used by migration tests carry no character table.
        return
    op.create_table(
        "character_sheet_draw",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "character_id",
            sa.Integer(),
            sa.ForeignKey("character.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("settings", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("drawn_from", sa.Integer(), nullable=True),
        sa.Column("runs", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("kept", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("used", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("used_sheets", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("seen", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_character_sheet_draw_character_id",
        "character_sheet_draw",
        ["character_id"],
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "character_sheet_draw" in inspector.get_table_names():
        op.drop_table("character_sheet_draw")
