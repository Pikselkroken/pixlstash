"""add indexes for cascade deletes

Revision ID: b2c4d6e8f0a1
Revises: a4c5d6e7f8b9
Create Date: 2026-02-08 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "b2c4d6e8f0a1"
down_revision: Union[str, None] = "a4c5d6e7f8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    def index_names(table_name: str) -> set[str]:
        return {
            row[1]
            for row in conn.execute(text(f"PRAGMA index_list('{table_name}')"))
            if row and len(row) > 1
        }

    face_indexes = index_names("face")
    if "ix_face_picture_id" not in face_indexes:
        op.create_index("ix_face_picture_id", "face", ["picture_id"], unique=False)

    hand_indexes = index_names("hand")
    if "ix_hand_picture_id" not in hand_indexes:
        op.create_index("ix_hand_picture_id", "hand", ["picture_id"], unique=False)

    face_tag_indexes = index_names("face_tag")
    if "ix_face_tag_tag_id" not in face_tag_indexes:
        op.create_index("ix_face_tag_tag_id", "face_tag", ["tag_id"], unique=False)

    hand_tag_indexes = index_names("hand_tag")
    if "ix_hand_tag_tag_id" not in hand_tag_indexes:
        op.create_index("ix_hand_tag_tag_id", "hand_tag", ["tag_id"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()

    def index_names(table_name: str) -> set[str]:
        return {
            row[1]
            for row in conn.execute(text(f"PRAGMA index_list('{table_name}')"))
            if row and len(row) > 1
        }

    if "ix_hand_tag_tag_id" in index_names("hand_tag"):
        op.drop_index("ix_hand_tag_tag_id", table_name="hand_tag")

    if "ix_face_tag_tag_id" in index_names("face_tag"):
        op.drop_index("ix_face_tag_tag_id", table_name="face_tag")

    if "ix_hand_picture_id" in index_names("hand"):
        op.drop_index("ix_hand_picture_id", table_name="hand")

    if "ix_face_picture_id" in index_names("face"):
        op.drop_index("ix_face_picture_id", table_name="face")
