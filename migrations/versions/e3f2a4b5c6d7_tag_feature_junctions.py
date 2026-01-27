"""tag feature junctions

Revision ID: e3f2a4b5c6d7
Revises: d4a1b2c3d4e5
Create Date: 2026-01-29 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "e3f2a4b5c6d7"
down_revision: Union[str, None] = "d4a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    def table_exists(table_name: str) -> bool:
        return bool(
            conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=:name"
                ),
                {"name": table_name},
            ).fetchone()
        )

    def table_columns(table_name: str) -> set[str]:
        if not table_exists(table_name):
            return set()
        return {
            row[1] for row in conn.execute(text(f"PRAGMA table_info('{table_name}')"))
        }

    has_tag = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='tag'")
    ).fetchone()
    if not has_tag:
        op.create_table(
            "tag",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("picture_id", sa.Integer(), nullable=False),
            sa.Column("tag", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["picture_id"], ["picture.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("picture_id", "tag", name="uq_tag_picture_tag"),
        )
        existing_indexes = {
            row[1]
            for row in conn.execute(text("PRAGMA index_list('tag')"))
            if row and len(row) > 1
        }
        if "ix_tag_picture_id" not in existing_indexes:
            op.create_index("ix_tag_picture_id", "tag", ["picture_id"], unique=False)
        if "ix_tag_tag" not in existing_indexes:
            op.create_index("ix_tag_tag", "tag", ["tag"], unique=False)

        op.create_table(
            "face_tag",
            sa.Column("face_id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("tag_id", sa.Integer(), primary_key=True, nullable=False),
            sa.ForeignKeyConstraint(["face_id"], ["face.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], ondelete="CASCADE"),
        )
        op.create_table(
            "hand_tag",
            sa.Column("hand_id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("tag_id", sa.Integer(), primary_key=True, nullable=False),
            sa.ForeignKeyConstraint(["hand_id"], ["hand.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], ondelete="CASCADE"),
        )
        return

    has_tag_old = table_exists("tag_old")
    has_face_tag = table_exists("face_tag")
    has_hand_tag = table_exists("hand_tag")

    tag_columns = table_columns("tag")
    tag_old_columns = table_columns("tag_old")
    tag_has_old_columns = "face_id" in tag_columns or "hand_id" in tag_columns
    tag_old_has_old_columns = (
        "face_id" in tag_old_columns or "hand_id" in tag_old_columns
    )

    if tag_has_old_columns:
        if has_tag_old:
            op.drop_table("tag_old")
            has_tag_old = False
            tag_old_columns = set()
            tag_old_has_old_columns = False

        op.rename_table("tag", "tag_old")
        op.create_table(
            "tag_new",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("picture_id", sa.Integer(), nullable=False),
            sa.Column("tag", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["picture_id"], ["picture.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("picture_id", "tag", name="uq_tag_picture_tag"),
        )
        op.execute(
            "INSERT INTO tag_new (picture_id, tag) "
            "SELECT DISTINCT picture_id, tag FROM tag_old"
        )
        op.rename_table("tag_new", "tag")
        has_tag_old = True
        tag_old_columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info('tag_old')"))
        }
        tag_old_has_old_columns = (
            "face_id" in tag_old_columns or "hand_id" in tag_old_columns
        )

    if not has_face_tag:
        op.create_table(
            "face_tag",
            sa.Column("face_id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("tag_id", sa.Integer(), primary_key=True, nullable=False),
            sa.ForeignKeyConstraint(["face_id"], ["face.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], ondelete="CASCADE"),
        )
        has_face_tag = True

    if not has_hand_tag:
        op.create_table(
            "hand_tag",
            sa.Column("hand_id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("tag_id", sa.Integer(), primary_key=True, nullable=False),
            sa.ForeignKeyConstraint(["hand_id"], ["hand.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], ondelete="CASCADE"),
        )
        has_hand_tag = True

    if has_tag_old and tag_old_has_old_columns:
        if "face_id" in tag_old_columns:
            op.execute(
                "INSERT INTO face_tag (face_id, tag_id) "
                "SELECT t.face_id, tn.id "
                "FROM tag_old t "
                "JOIN tag tn ON tn.picture_id = t.picture_id AND tn.tag = t.tag "
                "WHERE t.face_id IS NOT NULL"
            )
        if "hand_id" in tag_old_columns:
            op.execute(
                "INSERT INTO hand_tag (hand_id, tag_id) "
                "SELECT t.hand_id, tn.id "
                "FROM tag_old t "
                "JOIN tag tn ON tn.picture_id = t.picture_id AND tn.tag = t.tag "
                "WHERE t.hand_id IS NOT NULL"
            )

        op.drop_table("tag_old")
    elif has_tag_old and not tag_old_has_old_columns:
        op.drop_table("tag_old")

    existing_indexes = {
        row[1]
        for row in conn.execute(text("PRAGMA index_list('tag')"))
        if row and len(row) > 1
    }
    if "ix_tag_picture_id" not in existing_indexes:
        op.create_index("ix_tag_picture_id", "tag", ["picture_id"], unique=False)
    if "ix_tag_tag" not in existing_indexes:
        op.create_index("ix_tag_tag", "tag", ["tag"], unique=False)


def downgrade() -> None:
    op.create_table(
        "tag_old",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("picture_id", sa.Integer(), nullable=False),
        sa.Column("face_id", sa.Integer(), nullable=True),
        sa.Column("hand_id", sa.Integer(), nullable=True),
        sa.Column("tag", sa.String(), nullable=False),
        sa.CheckConstraint(
            "NOT (face_id IS NOT NULL AND hand_id IS NOT NULL)",
            name="ck_tag_single_feature",
        ),
        sa.ForeignKeyConstraint(["picture_id"], ["picture.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["face_id"], ["face.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["hand_id"], ["hand.id"], ondelete="CASCADE"),
    )

    op.execute("INSERT INTO tag_old (picture_id, tag) SELECT picture_id, tag FROM tag")
    op.execute(
        "INSERT INTO tag_old (picture_id, face_id, tag) "
        "SELECT t.picture_id, ft.face_id, t.tag "
        "FROM face_tag ft "
        "JOIN tag t ON t.id = ft.tag_id"
    )
    op.execute(
        "INSERT INTO tag_old (picture_id, hand_id, tag) "
        "SELECT t.picture_id, ht.hand_id, t.tag "
        "FROM hand_tag ht "
        "JOIN tag t ON t.id = ht.tag_id"
    )

    op.drop_table("face_tag")
    op.drop_table("hand_tag")

    op.drop_table("tag")
    op.rename_table("tag_old", "tag")

    op.create_index("ix_tag_picture_id", "tag", ["picture_id"], unique=False)
    op.create_index("ix_tag_tag", "tag", ["tag"], unique=False)
    op.create_index("ix_tag_face_id", "tag", ["face_id"], unique=False)
    op.create_index("ix_tag_hand_id", "tag", ["hand_id"], unique=False)
