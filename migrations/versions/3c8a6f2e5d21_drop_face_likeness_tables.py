"""drop face likeness tables

Revision ID: 3c8a6f2e5d21
Revises: 0beaf9bc3c44
Create Date: 2026-01-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "3c8a6f2e5d21"
down_revision: Union[str, None] = "0beaf9bc3c44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("facelikenessfrontier", schema=None) as batch_op:
        batch_op.drop_index("ix_face_frontier_a")

    op.drop_table("facelikenessfrontier")

    with op.batch_alter_table("facelikeness", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_facelikeness_likeness"))
        batch_op.drop_index("ix_face_likeness_b")
        batch_op.drop_index("ix_face_likeness_a")

    op.drop_table("facelikeness")


def downgrade() -> None:
    op.create_table(
        "facelikeness",
        sa.Column("face_id_a", sa.Integer(), nullable=False),
        sa.Column("face_id_b", sa.Integer(), nullable=False),
        sa.Column("likeness", sa.Float(), nullable=False),
        sa.Column("metric", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.CheckConstraint("face_id_a < face_id_b", name="ck_face_pair_order"),
        sa.ForeignKeyConstraint(["face_id_a"], ["face.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["face_id_b"], ["face.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("face_id_a", "face_id_b"),
    )
    with op.batch_alter_table("facelikeness", schema=None) as batch_op:
        batch_op.create_index("ix_face_likeness_a", ["face_id_a"], unique=False)
        batch_op.create_index("ix_face_likeness_b", ["face_id_b"], unique=False)
        batch_op.create_index(batch_op.f("ix_facelikeness_likeness"), ["likeness"], unique=False)

    op.create_table(
        "facelikenessfrontier",
        sa.Column("face_id_a", sa.Integer(), nullable=False),
        sa.Column("j_max", sa.Integer(), nullable=False),
        sa.CheckConstraint("j_max >= face_id_a", name="ck_frontier_order"),
        sa.ForeignKeyConstraint(["face_id_a"], ["face.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("face_id_a"),
    )
    with op.batch_alter_table("facelikenessfrontier", schema=None) as batch_op:
        batch_op.create_index("ix_face_frontier_a", ["face_id_a"], unique=False)
