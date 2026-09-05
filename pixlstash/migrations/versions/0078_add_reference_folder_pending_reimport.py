"""Add ``pending_reimport`` to ``reference_folder`` for the explicit re-import signal.

The reference-folder scanner overrides the permanent-deletion ledger (re-importing
removed-but-kept files present on disk and clearing their ``deleted_file_log`` rows
so restore can resurface them) only on a *deliberate* folder (re-)add - never on a
routine sync-toggle, rename, relocate, mount-recovery, watcher, or periodic
re-scan. ``pending_reimport`` is that dedicated one-shot signal: it is set to True
only by the reference-folder create endpoint and cleared by the next scan that
completes, so no routine path can trigger the ledger override. It replaces the
earlier ``last_scanned IS NULL`` + no-pictures heuristic, which the watcher (it
resets ``last_scanned``) could spoof.

Existing folders predate the signal and default to ``False`` (``server_default="0"``)
so they are inert - a routine re-scan of an already-added folder never overrides
the ledger.

Revision ID: 0078_add_reference_folder_pending_reimport
Revises: 0077_add_deleted_file_log_file_removed
Create Date: 2026-07-22 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0078_add_reference_folder_pending_reimport"
down_revision: Union[str, None] = "0077_add_deleted_file_log_file_removed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "reference_folder" not in inspector.get_table_names():
        # Fresh install - the baseline migration creates the table with all
        # current columns via SQLModel.metadata.create_all(); nothing to do.
        return

    existing_cols = {col["name"] for col in inspector.get_columns("reference_folder")}

    if "pending_reimport" not in existing_cols:
        op.add_column(
            "reference_folder",
            sa.Column(
                "pending_reimport",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "reference_folder" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("reference_folder")}

    if "pending_reimport" in existing_cols:
        op.drop_column("reference_folder", "pending_reimport")
