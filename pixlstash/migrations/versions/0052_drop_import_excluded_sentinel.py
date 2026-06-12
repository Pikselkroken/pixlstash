"""Drop the import_excluded sentinel column and convert its rows to the ledger.

``Picture.import_excluded`` used to be a "permanent-delete sentinel": when a
user emptied the scrapheap and the source file lived in a reference folder with
``allow_delete_file=False``, the row was kept with ``import_excluded=True`` so
the reference-folder scan would not re-import the still-on-disk file.  That
coupled file-protection to row-visibility: those rows were filtered out of every
``Picture.find()`` query, so they vanished from the scrapheap grid while still
being counted by the badge (the "72 deleted, empty grid" bug).

File-protection is now decoupled from the DB row.  Every permanently deleted
scrapheap picture loses its row and gains a ``deleted_file_log`` entry, which is
what stops re-import.  This migration retires the now-vestigial column.

The data conversion below is a one-time, bounded migration of existing sentinel
rows into the ledger.  It is REQUIRED for correctness: without it, the on-disk
files behind the old sentinel rows would be re-imported on the next reference
folder scan (the new scan path skips files only via ``deleted_file_log``, not
via ``import_excluded``).  CLAUDE.md prefers schema-only migrations, but this
sentinel->ledger conversion is necessary for the column drop to be safe, so it
lives here rather than in application code.

Several composite/partial grid indexes (added in 0044 and 0047) list
``import_excluded`` as a column, and SQLite refuses to drop a column an index
references.  They are dropped first and recreated without ``import_excluded``
(the grid queries no longer filter on it), so grid sorts stay index-covered.

Revision ID: 0052_drop_import_excluded_sentinel
Revises: 0051_deleted_file_log_path_sha
Create Date: 2026-06-08 00:00:00.000000

"""

import hashlib
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0052_drop_import_excluded_sentinel"
down_revision: Union[str, None] = "0051_deleted_file_log_path_sha"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

# Indexes that reference the import_excluded column and must be dropped before
# the column can be dropped on SQLite.
_INDEXES_REFERENCING_IMPORT_EXCLUDED = [
    "ix_picture_import_excluded",
    "ix_picture_grid_score",
    "ix_picture_grid_created_at",
    "ix_picture_grid_imported_at",
    "ix_picture_grid_leaders_id",
    "ix_picture_grid_leaders_score",
    "ix_picture_grid_leaders_smart_score",
    "ix_picture_grid_leaders_imported_at",
    "ix_picture_grid_leaders_created_at",
]

# Grid indexes recreated without import_excluded so grid sort queries stay
# covered after the column is gone. Each tuple is
# (index_name, column_list, partial_where_sql_or_None).
_GRID_INDEXES_WITHOUT_IMPORT_EXCLUDED = [
    ("ix_picture_grid_score", ["deleted", "score", "id"], None),
    ("ix_picture_grid_created_at", ["deleted", "created_at", "id"], None),
    ("ix_picture_grid_imported_at", ["deleted", "imported_at", "id"], None),
    (
        "ix_picture_grid_leaders_id",
        ["deleted", "id"],
        "deleted = 0 AND (stack_id IS NULL OR stack_position = 0)",
    ),
    (
        "ix_picture_grid_leaders_score",
        ["deleted", "score", "id"],
        "deleted = 0 AND (stack_id IS NULL OR stack_position = 0)",
    ),
    (
        "ix_picture_grid_leaders_smart_score",
        ["deleted", "smart_score", "id"],
        "deleted = 0 AND (stack_id IS NULL OR stack_position = 0)",
    ),
    (
        "ix_picture_grid_leaders_imported_at",
        ["deleted", "imported_at", "id"],
        "deleted = 0 AND (stack_id IS NULL OR stack_position = 0)",
    ),
    (
        "ix_picture_grid_leaders_created_at",
        ["deleted", "created_at", "id"],
        "deleted = 0 AND (stack_id IS NULL OR stack_position = 0)",
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture" not in inspector.get_table_names():
        # Fresh install — baseline create_all() builds the table from the
        # current model (no import_excluded). Nothing to convert or drop.
        return

    existing_cols = {c["name"] for c in inspector.get_columns("picture")}
    if "import_excluded" not in existing_cols:
        # Column already gone (e.g. a fresh DB where 0029 added it and this
        # migration already ran, or a partial re-run). Nothing to do.
        return

    # --- One-time sentinel -> ledger conversion ---
    # Every picture with deleted=1 AND import_excluded=1 was a kept-on-disk
    # sentinel. Move it into deleted_file_log (so the scan never re-imports the
    # file) and delete the picture row.
    picture = sa.table(
        "picture",
        sa.column("id", sa.Integer),
        sa.column("file_path", sa.String),
        sa.column("pixel_sha", sa.String),
        sa.column("deleted", sa.Boolean),
        sa.column("import_excluded", sa.Boolean),
    )
    log = sa.table(
        "deleted_file_log",
        sa.column("id", sa.Integer),
        sa.column("path_sha", sa.String),
        sa.column("pixel_sha", sa.String),
        sa.column("deleted_at", sa.DateTime),
    )

    sentinel_rows = bind.execute(
        sa.select(picture.c.id, picture.c.file_path, picture.c.pixel_sha).where(
            picture.c.deleted == 1,
            picture.c.import_excluded == 1,
        )
    ).fetchall()

    now = datetime.now(timezone.utc)
    converted_ids: list[int] = []
    for pic_id, file_path, pixel_sha in sentinel_rows:
        path_sha = hashlib.sha256((file_path or "").encode("utf-8")).hexdigest()
        already_logged = bind.execute(
            sa.select(log.c.id).where(log.c.path_sha == path_sha).limit(1)
        ).first()
        if already_logged is None:
            bind.execute(
                sa.insert(log).values(
                    path_sha=path_sha,
                    pixel_sha=pixel_sha,
                    deleted_at=now,
                )
            )
        if pic_id is not None:
            converted_ids.append(pic_id)

    if converted_ids:
        bind.execute(sa.delete(picture).where(picture.c.id.in_(converted_ids)))

    # --- Drop indexes referencing import_excluded, then the column ---
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("picture")}
    for index_name in _INDEXES_REFERENCING_IMPORT_EXCLUDED:
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name="picture")

    op.drop_column("picture", "import_excluded")

    # --- Recreate the grid indexes without import_excluded ---
    inspector = sa.inspect(bind)
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("picture")}
    for index_name, columns, where_clause in _GRID_INDEXES_WITHOUT_IMPORT_EXCLUDED:
        if index_name in existing_indexes:
            continue
        col_sql = ", ".join(columns)
        if where_clause is None:
            op.execute(f"CREATE INDEX {index_name} ON picture ({col_sql})")
        else:
            op.execute(
                f"CREATE INDEX {index_name} ON picture ({col_sql}) WHERE {where_clause}"
            )


def downgrade() -> None:
    # The sentinel -> ledger conversion is NOT reversible: the deleted picture
    # rows are gone and the ledger does not retain enough to rebuild them. This
    # downgrade only restores the schema (re-adds the column defaulting False
    # and the original index shapes) so an older app version can run again; it
    # does not recover the converted rows.
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture" not in inspector.get_table_names():
        return

    existing_cols = {c["name"] for c in inspector.get_columns("picture")}

    # Drop the rebuilt (import_excluded-free) grid indexes first.
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("picture")}
    for index_name, _, _ in _GRID_INDEXES_WITHOUT_IMPORT_EXCLUDED:
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name="picture")

    if "import_excluded" not in existing_cols:
        op.add_column(
            "picture",
            sa.Column(
                "import_excluded",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    # Recreate the original index shapes (matching 0029, 0044, 0047).
    inspector = sa.inspect(bind)
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("picture")}
    if "ix_picture_import_excluded" not in existing_indexes:
        op.create_index("ix_picture_import_excluded", "picture", ["import_excluded"])

    original_grid_indexes = [
        ("ix_picture_grid_score", ["deleted", "import_excluded", "score", "id"], None),
        (
            "ix_picture_grid_created_at",
            ["deleted", "import_excluded", "created_at", "id"],
            None,
        ),
        (
            "ix_picture_grid_imported_at",
            ["deleted", "import_excluded", "imported_at", "id"],
            None,
        ),
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
    for index_name, columns, where_clause in original_grid_indexes:
        if index_name in existing_indexes:
            continue
        col_sql = ", ".join(columns)
        if where_clause is None:
            op.execute(f"CREATE INDEX {index_name} ON picture ({col_sql})")
        else:
            op.execute(
                f"CREATE INDEX {index_name} ON picture ({col_sql}) WHERE {where_clause}"
            )
