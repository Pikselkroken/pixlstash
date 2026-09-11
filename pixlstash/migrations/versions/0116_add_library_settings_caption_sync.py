"""Caption-file sync for the library's own picture root.

The four fields a reference folder carries for its sidecars, a toggle and a
filename suffix per kind, now live on ``library_settings`` too, so pictures
imported in place get the same two-way sync. Off on every existing library.

Revision ID: 0116_add_library_settings_caption_sync
Revises: 0115_add_folder_mapping_commit_captions
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0116_add_library_settings_caption_sync"
down_revision: Union[str, None] = "0115_add_folder_mapping_commit_captions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COLUMNS = (
    sa.Column("sync_tags", sa.Boolean(), nullable=False, server_default="0"),
    sa.Column("sync_descriptions", sa.Boolean(), nullable=False, server_default="0"),
    sa.Column("tags_suffix", sa.String(), nullable=True),
    sa.Column("description_suffix", sa.String(), nullable=True),
)


def _existing(bind) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns("library_settings")}


def upgrade() -> None:
    existing = _existing(op.get_bind())
    for column in _COLUMNS:
        if column.name not in existing:
            op.add_column("library_settings", column)


def downgrade() -> None:
    existing = _existing(op.get_bind())
    with op.batch_alter_table("library_settings") as batch:
        for column in _COLUMNS:
            if column.name in existing:
                batch.drop_column(column.name)
