"""tag feature junctions + fk repair (squash)

Revision ID: a8b9c0d1e2f3
Revises: d1e2f3a4b5c6
Create Date: 2026-01-29 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
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

    def table_pk_columns(table_name: str) -> set[str]:
        if not table_exists(table_name):
            return set()
        return {
            row[1]
            for row in conn.execute(text(f"PRAGMA table_info('{table_name}')"))
            if row[5] == 1
        }

    def foreign_key_refs(table_name: str) -> list[tuple[str, str]]:
        if not table_exists(table_name):
            return []
        return [
            (row[2], row[4])
            for row in conn.execute(text(f"PRAGMA foreign_key_list('{table_name}')"))
        ]

    if not table_exists("tag"):
        op.create_table(
            "tag",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("picture_id", sa.Integer(), nullable=False),
            sa.Column("tag", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["picture_id"], ["picture.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("picture_id", "tag", name="uq_tag_picture_tag"),
        )
        op.create_index("ix_tag_picture_id", "tag", ["picture_id"], unique=False)
        op.create_index("ix_tag_tag", "tag", ["tag"], unique=False)

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

    tag_cols = table_columns("tag")
    tag_pk_cols = table_pk_columns("tag")
    tag_id_is_pk = "id" in tag_pk_cols
    tag_has_id = "id" in tag_cols

    needs_tag_rebuild = not (tag_has_id and tag_id_is_pk)

    if needs_tag_rebuild:
        op.create_table(
            "tag_new",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("picture_id", sa.Integer(), nullable=False),
            sa.Column("tag", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["picture_id"], ["picture.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("picture_id", "tag", name="uq_tag_picture_tag"),
        )
        if tag_has_id:
            op.execute(
                "INSERT INTO tag_new (id, picture_id, tag) SELECT id, picture_id, tag FROM tag"
            )
        else:
            op.execute(
                "INSERT INTO tag_new (picture_id, tag) SELECT picture_id, tag FROM tag"
            )
        op.drop_table("tag")
        op.rename_table("tag_new", "tag")
        existing_indexes = {
            row[1]
            for row in conn.execute(text("PRAGMA index_list('tag')"))
            if row and len(row) > 1
        }
        if "ix_tag_picture_id" not in existing_indexes:
            op.create_index("ix_tag_picture_id", "tag", ["picture_id"], unique=False)
        if "ix_tag_tag" not in existing_indexes:
            op.create_index("ix_tag_tag", "tag", ["tag"], unique=False)

    def rebuild_junction_table(name: str, left_col: str, left_ref: str) -> None:
        if not table_exists(name):
            return
        fk_refs = foreign_key_refs(name)
        expects = [(left_ref.split(".")[0], left_ref.split(".")[1]), ("tag", "id")]
        if all(ref in fk_refs for ref in expects):
            return
        op.rename_table(name, f"{name}_old")
        op.create_table(
            name,
            sa.Column(left_col, sa.Integer(), primary_key=True, nullable=False),
            sa.Column("tag_id", sa.Integer(), primary_key=True, nullable=False),
            sa.ForeignKeyConstraint([left_col], [left_ref], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], ondelete="CASCADE"),
        )
        op.execute(
            f"INSERT INTO {name} ({left_col}, tag_id) "
            f"SELECT {left_col}, tag_id FROM {name}_old "
            "WHERE tag_id IN (SELECT id FROM tag)"
        )
        op.drop_table(f"{name}_old")

    rebuild_junction_table("face_tag", "face_id", "face.id")
    rebuild_junction_table("hand_tag", "hand_id", "hand.id")


def downgrade() -> None:
    # No safe downgrade for data-repair migration.
    pass
