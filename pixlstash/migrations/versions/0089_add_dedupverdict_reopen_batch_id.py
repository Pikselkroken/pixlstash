"""Add ``dedupverdict.reopen_batch_id`` - the undo-of-clear correlation key.

Clearing a ``stacked`` duplicate decision now dissolves the stack the verdict
created (restoring the recorded pre-verdict stack state) and records that
mutation as one ``dedup.reopen`` operation, so it is undoable like every other
stack mutation. The undo-of-clear post-restore hook needs a durable way to find
the verdict a clear operation reopened; ``batch_id`` cannot carry it, because it
must keep pointing at the verdict's *own* operation (or undoing the original
stack would no longer find its verdict). Hence a second, additive correlation
column: the batch id of the most recent picture-touching clear.

Schema-only and additive; no reprocessing reset is needed. The column is NULL
for every existing row, which is correct - no clear has touched their pictures.

Revision ID: 0089_add_dedupverdict_reopen_batch_id
Revises: 0088_add_dedup_tier_tables
Create Date: 2026-07-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0089_add_dedupverdict_reopen_batch_id"
down_revision: Union[str, None] = "0088_add_dedup_tier_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "dedupverdict" not in set(inspector.get_table_names()):
        # 0088 creates the table; nothing to alter on a database that somehow
        # reaches this revision without it (the baseline creates it with the
        # column already present via the model metadata).
        return
    existing_cols = {col["name"] for col in inspector.get_columns("dedupverdict")}
    if "reopen_batch_id" not in existing_cols:
        # A fresh database created the column (and its index) from the model in
        # the baseline's ``create_all``; only a table created before this change
        # needs the ALTER.
        op.add_column(
            "dedupverdict", sa.Column("reopen_batch_id", sa.String(), nullable=True)
        )
        op.create_index(
            "ix_dedupverdict_reopen_batch_id", "dedupverdict", ["reopen_batch_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "dedupverdict" not in set(inspector.get_table_names()):
        return
    existing_cols = {col["name"] for col in inspector.get_columns("dedupverdict")}
    if "reopen_batch_id" in existing_cols:
        op.drop_index("ix_dedupverdict_reopen_batch_id", table_name="dedupverdict")
        op.drop_column("dedupverdict", "reopen_batch_id")
