"""Add caption_file to picture and sync_captions to reference_folder.

caption_file stores the absolute path of the sidecar .txt/.caption file that
was present when a reference-folder picture was first indexed, so that
write-back does not need to guess the extension.

sync_captions enables automatic write-back of tag changes to that sidecar.

Revision ID: 0026_add_caption_file_and_sync_captions
Revises: 0025_add_reference_folders
Create Date: 2026-04-16 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0026_add_caption_file_and_sync_captions"
down_revision: Union[str, None] = "0025_add_reference_folders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_picture_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "caption_file" not in existing_picture_cols:
        op.add_column(
            "picture",
            sa.Column("caption_file", sa.String(), nullable=True),
        )

    existing_rf_cols = {
        col["name"] for col in inspector.get_columns("reference_folder")
    }
    if "sync_captions" not in existing_rf_cols:
        op.add_column(
            "reference_folder",
            sa.Column(
                "sync_captions", sa.Boolean(), nullable=False, server_default="0"
            ),
        )


def downgrade() -> None:
    op.drop_column("picture", "caption_file")
    op.drop_column("reference_folder", "sync_captions")
