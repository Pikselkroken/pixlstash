"""Replace deleted_file_log.file_path with an opaque path_sha hash.

The deleted-file ledger only ever needs to *match* a path on restore, never
read it back. Storing the raw ``file_path`` leaked sensitive information for
reference-folder pictures (their real on-disk paths). This migration adds a
SHA-256 ``path_sha`` column, backfills it from the existing ``file_path``
values so already-logged deletions stay matchable, and drops ``file_path`` so
no cleartext path is retained.

Revision ID: 0051_deleted_file_log_path_sha
Revises: 0050_reset_metadata_hash_membership
Create Date: 2026-05-31 00:00:00.000000

"""

import hashlib
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0051_deleted_file_log_path_sha"
down_revision: Union[str, None] = "0050_reset_metadata_hash_membership"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "deleted_file_log" not in inspector.get_table_names():
        return
    existing_cols = {c["name"] for c in inspector.get_columns("deleted_file_log")}

    # A fresh DB is created from the current model (path_sha already present,
    # file_path absent) — both branches below no-op in that case.
    if "path_sha" not in existing_cols:
        op.add_column(
            "deleted_file_log", sa.Column("path_sha", sa.String(), nullable=True)
        )
        op.create_index(
            "ix_deleted_file_log_path_sha", "deleted_file_log", ["path_sha"]
        )

    if "file_path" in existing_cols:
        # Backfill path_sha from the soon-to-be-dropped file_path so existing
        # entries keep matching on restore. Pure SHA-256 of the path string;
        # the raw path never survives this migration.
        log = sa.table(
            "deleted_file_log",
            sa.column("id", sa.Integer),
            sa.column("file_path", sa.String),
            sa.column("path_sha", sa.String),
        )
        rows = bind.execute(
            sa.select(log.c.id, log.c.file_path).where(log.c.path_sha.is_(None))
        ).fetchall()
        for row_id, file_path in rows:
            digest = hashlib.sha256((file_path or "").encode("utf-8")).hexdigest()
            bind.execute(
                sa.update(log).where(log.c.id == row_id).values(path_sha=digest)
            )

        existing_indexes = {
            ix["name"] for ix in inspector.get_indexes("deleted_file_log")
        }
        if "ix_deleted_file_log_file_path" in existing_indexes:
            op.drop_index(
                "ix_deleted_file_log_file_path", table_name="deleted_file_log"
            )
        op.drop_column("deleted_file_log", "file_path")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "deleted_file_log" not in inspector.get_table_names():
        return
    existing_cols = {c["name"] for c in inspector.get_columns("deleted_file_log")}

    # The original paths were hashed away and cannot be recovered, so the
    # restored column is nullable (best-effort schema rollback only).
    if "file_path" not in existing_cols:
        op.add_column(
            "deleted_file_log", sa.Column("file_path", sa.String(), nullable=True)
        )
        op.create_index(
            "ix_deleted_file_log_file_path", "deleted_file_log", ["file_path"]
        )

    if "path_sha" in existing_cols:
        existing_indexes = {
            ix["name"] for ix in inspector.get_indexes("deleted_file_log")
        }
        if "ix_deleted_file_log_path_sha" in existing_indexes:
            op.drop_index("ix_deleted_file_log_path_sha", table_name="deleted_file_log")
        op.drop_column("deleted_file_log", "path_sha")
