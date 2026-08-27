"""Add ``picture.orientation`` - the mirrored EXIF orientation tag.

In-place rotate (#950) turns a photo by rewriting one EXIF field and copying
every pixel byte through. For that to be undoable, the operation log has to
record the orientation as an ordinary reversible facet, which means
``capture_state_in_session`` has to be able to read it. That capture runs for
**every** recorded operation over **every** affected picture, so reading the tag
off disk there would make a 2,700-row tag edit do 5,400 file opens on the single
DB writer thread. The column is the mirror that makes the capture a column read.

The file stays the source of truth: ``operation_log_service.apply_orientation``
re-reads the tag before it writes, so an externally rotated file converges rather
than being overwritten from a stale mirror.

Additive and NULL-defaulted; nothing is reset. NULL means "not read yet", and
``MissingOrientationFinder`` backfills it from ``read_orientation``. The partial
index matches ``0095_add_finder_partial_indexes``'s shape and exists for the same
reason: the planner probes ``orientation IS NULL AND deleted IS 0`` several times
a second, and without it SQLite walks every non-deleted row to prove there is no
work.

Revision ID: 0104_add_picture_orientation
Revises: 0103_add_adapter_attachment
Create Date: 2026-08-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0104_add_picture_orientation"
down_revision: Union[str, None] = "0103_add_adapter_attachment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_ORIENTATION_INDEX = "ix_picture_orientation_missing"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "picture" not in set(inspector.get_table_names()):
        return

    # Conditional because the baseline runs SQLModel.metadata.create_all(), which
    # builds `picture` with every current model column; an unconditional
    # ALTER TABLE ... ADD COLUMN would fail on a fresh database.
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "orientation" not in existing_cols:
        op.add_column("picture", sa.Column("orientation", sa.Integer(), nullable=True))

    # Declared on the model too, so a fresh database already has it from the
    # baseline. Guarded by name so both paths converge on the same index.
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("picture")}
    if _ORIENTATION_INDEX not in existing_indexes:
        op.create_index(
            _ORIENTATION_INDEX,
            "picture",
            ["orientation", "deleted", "id"],
            sqlite_where=sa.text("orientation IS NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "picture" not in set(inspector.get_table_names()):
        return
    if _ORIENTATION_INDEX in {idx["name"] for idx in inspector.get_indexes("picture")}:
        op.drop_index(_ORIENTATION_INDEX, table_name="picture")
    if "orientation" in {col["name"] for col in inspector.get_columns("picture")}:
        op.drop_column("picture", "orientation")
