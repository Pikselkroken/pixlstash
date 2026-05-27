"""Add partial composite indexes for stack-leader grid queries.

The standard grid endpoint always adds a stack_leaders_only filter:
  WHERE deleted=0 AND import_excluded=0
    AND (stack_id IS NULL OR stack_position = 0)

The existing ix_picture_grid_* composite indexes cover (deleted, import_excluded,
sort_key, id) but do NOT include the stack-leader condition.  SQLite therefore
scans every qualifying non-deleted/non-excluded row and filters out non-leaders
at read time, which on large libraries means reading many more rows than needed
for each paginated page.

These new partial indexes embed the stack-leader condition in the index itself so
that SQLite only visits rows that would pass all three filters, eliminating the
extra scan work.

Revision ID: 0049_add_stack_leader_grid_indexes
Revises: 0048_add_checkpoint
Create Date: 2026-05-27 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0049_add_stack_leader_grid_indexes"
down_revision: Union[str, None] = "0048_add_checkpoint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

# Each tuple is (index_name, column_list, partial_where_sql)
_PARTIAL_INDEXES = [
    (
        "ix_picture_grid_leaders_id",
        ["deleted", "import_excluded", "id"],
        "deleted = 0 AND import_excluded = 0 AND (stack_id IS NULL OR stack_position = 0)",
    ),
    (
        "ix_picture_grid_leaders_score",
        ["deleted", "import_excluded", "score", "id"],
        "deleted = 0 AND import_excluded = 0 AND (stack_id IS NULL OR stack_position = 0)",
    ),
    (
        "ix_picture_grid_leaders_smart_score",
        ["deleted", "import_excluded", "smart_score", "id"],
        "deleted = 0 AND import_excluded = 0 AND (stack_id IS NULL OR stack_position = 0)",
    ),
    (
        "ix_picture_grid_leaders_imported_at",
        ["deleted", "import_excluded", "imported_at", "id"],
        "deleted = 0 AND import_excluded = 0 AND (stack_id IS NULL OR stack_position = 0)",
    ),
    (
        "ix_picture_grid_leaders_created_at",
        ["deleted", "import_excluded", "created_at", "id"],
        "deleted = 0 AND import_excluded = 0 AND (stack_id IS NULL OR stack_position = 0)",
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture" not in inspector.get_table_names():
        # Fresh install — baseline migration creates the table.  Nothing to do.
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("picture")}

    for index_name, columns, where_clause in _PARTIAL_INDEXES:
        if index_name not in existing_indexes:
            col_sql = ", ".join(columns)
            op.execute(
                f"CREATE INDEX {index_name} ON picture ({col_sql})"
                f" WHERE {where_clause}"
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture" not in inspector.get_table_names():
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("picture")}

    for index_name, _, _ in _PARTIAL_INDEXES:
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name="picture")
