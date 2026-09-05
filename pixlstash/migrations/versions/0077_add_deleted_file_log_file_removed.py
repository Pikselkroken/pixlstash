"""Add ``file_removed`` to ``deleted_file_log`` to disambiguate the ledger.

``deleted_file_log`` was serving two different meanings through one table: (1) the
picture's content was permanently deleted and its file removed from disk - restore
must never resurrect it; and (2) the picture was removed from the library but its
file was deliberately kept on disk (a protected reference-folder picture,
``allow_delete_file=False``) - the row exists only so the reference-folder scanner
does not auto re-import that path. Conflating the two made restore drop alive,
file-present reference pictures.

``file_removed`` records which meaning a row carries: ``True`` = file was actually
removed (genuinely gone), ``False`` = file kept on disk. Restore's
permanent-deletion drop now only counts ``file_removed=True`` rows; the scanner
still consults every row. Existing rows predate the distinction and default to
``True`` (``server_default="1"``) so the "never resurrect permanently-deleted
content" guarantee is preserved for them.

Revision ID: 0077_add_deleted_file_log_file_removed
Revises: 0076_recompute_smart_score_after_stale_tag_edits
Create Date: 2026-07-22 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0077_add_deleted_file_log_file_removed"
down_revision: Union[str, None] = "0076_recompute_smart_score_after_stale_tag_edits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "deleted_file_log" not in inspector.get_table_names():
        # Fresh install - the baseline migration creates the table with all
        # current columns via SQLModel.metadata.create_all(); nothing to do.
        return

    existing_cols = {col["name"] for col in inspector.get_columns("deleted_file_log")}

    if "file_removed" not in existing_cols:
        op.add_column(
            "deleted_file_log",
            sa.Column(
                "file_removed",
                sa.Boolean(),
                nullable=False,
                server_default="1",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "deleted_file_log" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("deleted_file_log")}

    if "file_removed" in existing_cols:
        op.drop_column("deleted_file_log", "file_removed")
