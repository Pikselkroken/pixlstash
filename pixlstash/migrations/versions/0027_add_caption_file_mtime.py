"""Add caption_file_mtime to picture.

Stores the modification time (Unix float) of the sidecar caption file the last
time it was read into the database.  The reference-folder scan task compares
this against the file's current mtime to detect changed or newly-appeared
sidecars without reading file content on every scan.

Revision ID: 0027_add_caption_file_mtime
Revises: 0026_add_caption_file_and_sync_captions
Create Date: 2026-04-16 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0027_add_caption_file_mtime"
down_revision: Union[str, None] = "0026_add_caption_file_and_sync_captions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "caption_file_mtime" not in existing_cols:
        op.add_column(
            "picture",
            sa.Column("caption_file_mtime", sa.Float, nullable=True),
        )


def downgrade() -> None:
    op.drop_column("picture", "caption_file_mtime")
