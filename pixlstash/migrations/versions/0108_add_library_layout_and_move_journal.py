"""Add the library layout, the layout-check stamp and the move journal.

v1.11 Phase 4b, the move engine (``docs/plans/v1.11.0-existing-library.md`` §4).

Four things, all inert on an existing library:

* ``library_settings.layout`` / ``layout_unfiled`` and the same pair on
  ``reference_folder`` - how a root is laid out. NULL means no layout, and a
  root with no layout is never placed into and never moved within. Every
  existing library and every existing folder gets NULL, which is the whole
  reason "importing a real library moves zero files" is a fact rather than a
  promise.
* ``picture.layout_check_due_at`` - when the engine should next ask whether a
  picture's folder is still true. NULL everywhere to begin with, indexed
  because the finder's only query asks for the rows that are not.
* ``picture_move`` - the journal of moves PixlStash made itself, so its own
  writes are not read back through the reference-folder scan as owner intent.

**No data is reset.** Nothing here changes what a picture is, only where the
engine would put it, and the engine does nothing until a root has a layout.

Revision ID: 0108_add_library_layout_and_move_journal
Revises: 0107_add_library_views_settings
Create Date: 2026-08-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0108_add_library_layout_and_move_journal"
down_revision: Union[str, None] = "0107_add_library_views_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_LAYOUT_COLUMNS = ("layout", "layout_unfiled")
_LAYOUT_TABLES = ("library_settings", "reference_folder")
_DUE_COLUMN = "layout_check_due_at"
_DUE_INDEX = "ix_picture_layout_check_due_at"


def _columns(inspector, table: str) -> set:
    """Column names of *table*, or ``None`` when the table is not there yet.

    The legacy-upgrade fixtures in ``tests/test_migrations.py`` build a database
    at an old revision and walk it forward, so a table this migration touches
    can genuinely not exist at the point it runs. ``get_columns`` raises for
    that rather than returning empty, and a raise here would abort an upgrade
    that has nothing to do with the layout.
    """
    try:
        return {col["name"] for col in inspector.get_columns(table)}
    except sa.exc.NoSuchTableError:
        return None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table in _LAYOUT_TABLES:
        existing = _columns(inspector, table)
        if existing is None:
            continue
        for column in _LAYOUT_COLUMNS:
            if column not in existing:
                op.add_column(table, sa.Column(column, sa.String(), nullable=True))

    picture_columns = _columns(inspector, "picture")
    if picture_columns is not None:
        if _DUE_COLUMN not in picture_columns:
            op.add_column("picture", sa.Column(_DUE_COLUMN, sa.Float(), nullable=True))
        if _DUE_INDEX not in {ix["name"] for ix in inspector.get_indexes("picture")}:
            op.create_index(_DUE_INDEX, "picture", [_DUE_COLUMN])

    if "picture_move" not in inspector.get_table_names():
        op.create_table(
            "picture_move",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("picture_id", sa.Integer(), nullable=True),
            sa.Column("old_path", sa.String(), nullable=False),
            sa.Column("new_path", sa.String(), nullable=False),
            sa.Column("moved_at", sa.DateTime(), nullable=False),
            sa.Column("reason", sa.String(), nullable=False),
            sa.Column("consumed", sa.Boolean(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_picture_move_picture_id", "picture_move", ["picture_id"])
        op.create_index("ix_picture_move_old_path", "picture_move", ["old_path"])
        op.create_index("ix_picture_move_new_path", "picture_move", ["new_path"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture_move" in inspector.get_table_names():
        op.drop_table("picture_move")

    picture_columns = _columns(inspector, "picture")
    if picture_columns is not None:
        if _DUE_INDEX in {ix["name"] for ix in inspector.get_indexes("picture")}:
            op.drop_index(_DUE_INDEX, table_name="picture")
        if _DUE_COLUMN in picture_columns:
            op.drop_column("picture", _DUE_COLUMN)

    for table in _LAYOUT_TABLES:
        existing = _columns(inspector, table)
        if existing is None:
            continue
        for column in _LAYOUT_COLUMNS:
            if column in existing:
                op.drop_column(table, column)
