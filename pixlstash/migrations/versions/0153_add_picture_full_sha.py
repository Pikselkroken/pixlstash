"""Add ``picture.full_sha`` and ``picture.full_sha_stat``.

The import duplicate check's cache of each library file's full-file SHA-256,
with the stat signature it was taken under. Both nullable and filled lazily by
the import paths: NULL means "read the file", which is what every import did
before, so there is no backfill and no ``NULL`` reset.

Schema-only and additive.

Revision ID: 0153_add_picture_full_sha
Revises: 0152_add_guide_welcome_seen
Create Date: 2026-11-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0153_add_picture_full_sha"
down_revision: Union[str, None] = "0152_add_guide_welcome_seen"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COLUMNS = ("full_sha", "full_sha_stat")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    for name in _COLUMNS:
        if name not in existing_cols:
            op.add_column("picture", sa.Column(name, sa.String(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    for name in _COLUMNS:
        if name in existing_cols:
            op.drop_column("picture", name)
