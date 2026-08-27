"""Add review sessions: the review table + review_id/neighbors on tag_suggestion.

A review is one tag + an optional frozen scope + one scan's results (see
docs/reviews/2026-07-review-sessions-redesign-draft.md). Suggestions gain a
nullable review_id FK (ON DELETE SET NULL - decisions outlive their session)
and a neighbors TEXT column holding the k-nearest-neighbour evidence JSON
captured at scan time. A partial unique index enforces one OPEN review per tag.

Additive only: no existing column or constraint changes; in particular
UNIQUE(picture_id, tag, source) on tag_suggestion is untouched.

Revision ID: 0065_add_review_sessions
Revises: 0064_add_sidebar_pinned
Create Date: 2026-07-15 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0065_add_review_sessions"
down_revision: Union[str, None] = "0064_add_sidebar_pinned"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "review" not in tables:
        op.create_table(
            "review",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tag", sa.String(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=True),
            sa.Column("set_id", sa.Integer(), nullable=True),
            # String: may hold the literal "UNASSIGNED" besides numeric ids.
            sa.Column("character_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="OPEN"),
            sa.Column("scanned", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("found", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "prev_reviewed", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("refreshed_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_review_tag", "review", ["tag"])
        op.create_index("ix_review_status", "review", ["status"])
        # One OPEN review per tag (SQLite supports partial indexes).
        op.create_index(
            "uq_review_open_tag",
            "review",
            ["tag"],
            unique=True,
            sqlite_where=sa.text("status = 'OPEN'"),
        )
    else:
        # Fresh DBs get the table from the 0001 baseline create_all(); the
        # partial unique index is part of the model metadata, but guard it in
        # case an older create_all predates it.
        existing_indexes = {ix["name"] for ix in inspector.get_indexes("review")}
        if "uq_review_open_tag" not in existing_indexes:
            op.create_index(
                "uq_review_open_tag",
                "review",
                ["tag"],
                unique=True,
                sqlite_where=sa.text("status = 'OPEN'"),
            )

    if "tag_suggestion" not in tables:
        # A partial/synthetic DB (e.g. hand-built migration-test schemas):
        # nothing to extend.
        return

    # Plain ADD COLUMN (no batch mode): batch_alter_table reflects the whole
    # table incl. its FKs, which breaks on partial/synthetic DBs (migration
    # tests) where the picture table is absent. SQLite supports an inline
    # REFERENCES clause on ADD COLUMN, but Alembic's add_column emits the FK
    # as a separate (unsupported) ALTER - so use SQLite's native DDL directly
    # (the vault DB is SQLite by project invariant).
    existing_columns = {c["name"] for c in inspector.get_columns("tag_suggestion")}
    if "review_id" not in existing_columns:
        op.execute(
            sa.text(
                "ALTER TABLE tag_suggestion ADD COLUMN review_id INTEGER "
                "REFERENCES review(id) ON DELETE SET NULL"
            )
        )
    if "neighbors" not in existing_columns:
        op.add_column(
            "tag_suggestion", sa.Column("neighbors", sa.Text(), nullable=True)
        )

    existing_indexes = {ix["name"] for ix in inspector.get_indexes("tag_suggestion")}
    if "ix_tag_suggestion_review_id" not in existing_indexes:
        op.create_index("ix_tag_suggestion_review_id", "tag_suggestion", ["review_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "tag_suggestion" in inspector.get_table_names():
        existing_indexes = {
            ix["name"] for ix in inspector.get_indexes("tag_suggestion")
        }
        if "ix_tag_suggestion_review_id" in existing_indexes:
            op.drop_index("ix_tag_suggestion_review_id", table_name="tag_suggestion")
        existing_columns = {c["name"] for c in inspector.get_columns("tag_suggestion")}
        if "neighbors" in existing_columns:
            op.drop_column("tag_suggestion", "neighbors")
        if "review_id" in existing_columns:
            op.drop_column("tag_suggestion", "review_id")

    if "review" in inspector.get_table_names():
        op.drop_table("review")
