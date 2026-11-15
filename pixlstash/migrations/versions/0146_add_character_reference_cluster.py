"""Add ``character_reference_member``: a person's frozen reference cluster.

The pictures the owner picked as who a person is, in *Faces and reference
cluster…*. Empty for everyone until they pick, which keeps today's choice of
reference faces.

Schema-only and additive.

Revision ID: 0146_add_character_reference_cluster
Revises: 0145_add_tag_suggestion_control_state
Create Date: 2026-11-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0146_add_character_reference_cluster"
down_revision: Union[str, None] = "0145_add_tag_suggestion_control_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

TABLE = "character_reference_member"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column(
            "character_id",
            sa.Integer(),
            sa.ForeignKey("character.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("picture_id", sa.Integer(), primary_key=True, nullable=False),
    )
    op.create_index(
        "ix_character_reference_member_character_id", TABLE, ["character_id"]
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        op.drop_table(TABLE)
