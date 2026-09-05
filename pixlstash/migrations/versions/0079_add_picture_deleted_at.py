"""Add ``picture.deleted_at`` - the scrapheap retention clock.

v1.8.0 adds an automatic scrapheap retention window: an UNPROTECTED (managed)
soft-deleted picture is permanently purged once it has sat in the scrapheap for
``scrapheap_retention_days``. That needs a per-picture "when did this enter the
scrapheap" timestamp, which the schema did not carry - ``deleted`` was a bare
boolean.

``deleted_at`` is stamped on the ``deleted`` False -> True transition and cleared
on restore, so it always describes the picture's CURRENT stay in the scrapheap.
A soft-deleted row with ``deleted_at IS NULL`` is never auto-purged (fail-closed).

**Backfill:** every row that is already ``deleted=True`` gets ``deleted_at`` set
to the migration time, not to its (unknown) original deletion time. That is
deliberate: it gives everything already in the scrapheap a FULL retention window
starting at upgrade, so the first run after deploying v1.8.0 can never surprise a
user by destroying a long-standing scrapheap item immediately.

Protected reference-folder originals (``ReferenceFolder.allow_delete_file=False``)
are exempt from the timer entirely and are only ever destroyed by the manual,
consent-gated ``include_protected=true`` delete-forever; the backfill is harmless
for them.

Revision ID: 0079_add_picture_deleted_at
Revises: 0078_add_reference_folder_pending_reimport
Create Date: 2026-07-22 00:00:00.000000

"""

from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0079_add_picture_deleted_at"
down_revision: Union[str, None] = "0078_add_reference_folder_pending_reimport"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture" not in inspector.get_table_names():
        # Fresh install - the baseline migration creates the table with all
        # current columns via SQLModel.metadata.create_all(); nothing to do.
        return

    existing_cols = {col["name"] for col in inspector.get_columns("picture")}

    if "deleted_at" not in existing_cols:
        op.add_column(
            "picture",
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
        )

    existing_indexes = {ix["name"] for ix in inspector.get_indexes("picture")}
    if "ix_picture_deleted_at" not in existing_indexes:
        op.create_index("ix_picture_deleted_at", "picture", ["deleted_at"])

    # Give everything already in the scrapheap a full retention window measured
    # from the upgrade, so no pre-existing item is purged on the first sweep.
    op.execute(
        sa.text(
            "UPDATE picture SET deleted_at = :now "
            "WHERE deleted = 1 AND deleted_at IS NULL"
        ).bindparams(now=datetime.now(timezone.utc).replace(tzinfo=None))
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture" not in inspector.get_table_names():
        return

    existing_indexes = {ix["name"] for ix in inspector.get_indexes("picture")}
    if "ix_picture_deleted_at" in existing_indexes:
        op.drop_index("ix_picture_deleted_at", table_name="picture")

    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "deleted_at" in existing_cols:
        op.drop_column("picture", "deleted_at")
