"""Normalize existing stack positions to a contiguous 0-based ordering.

The grid's stack-leader filter selects the leader purely in SQL with
``deleted = 0 AND (stack_id IS NULL OR stack_position = 0)`` (see migration
0047 and ``Picture.find``). That is only correct if every stack has a
*non-deleted* member at ``stack_position = 0``.

Historically several write paths could leave a stack without a position-0
member: ``assign_picture_to_stack`` appended at ``max(position) + 1`` (and left
NULL positions untouched), and soft-deleting the leader left a deleted picture
sitting at position 0. Such stacks silently disappear from the grid.

The write paths are now fixed to call ``stacking.normalize_stack_positions``,
but existing rows still need a one-time correction. This migration renumbers
each stack's members to contiguous 0-based positions using the same ordering as
``normalize_stack_positions``: non-deleted members first, then by explicit
position (NULLs last), then by id. It is pure data normalization (no schema
change) and is idempotent.

Revision ID: 0048_normalize_stack_positions
Revises: 0047_add_stack_leader_grid_indexes
Create Date: 2026-05-27 00:01:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0048_normalize_stack_positions"
down_revision: Union[str, None] = "0047_add_stack_leader_grid_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_NORMALIZE_SQL = """
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY stack_id
               ORDER BY deleted ASC,
                        (stack_position IS NULL) ASC,
                        stack_position ASC,
                        id ASC
           ) - 1 AS new_position
    FROM picture
    WHERE stack_id IS NOT NULL
)
UPDATE picture
SET stack_position = (
    SELECT new_position FROM ranked WHERE ranked.id = picture.id
)
WHERE stack_id IS NOT NULL
"""


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture" not in inspector.get_table_names():
        # Fresh install — baseline migration creates the table with no rows.
        return

    op.execute(_NORMALIZE_SQL)


def downgrade() -> None:
    # Positions are renumbered in place; the previous (arbitrary, possibly
    # gapped or NULL) values are not recoverable, so there is nothing to undo.
    pass
