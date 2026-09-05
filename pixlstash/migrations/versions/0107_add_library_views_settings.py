"""Add the PixlStash Views settings to ``library_settings``.

Two columns: where this library publishes its views tree, and which kinds it
publishes. They belong to the library rather than to the person because the
folder holds *this* library's people and sets - two libraries publishing into
the same folder would overwrite each other's tree.

NULL in both means views are off, which is what every existing library wants:
the tree is opt-in and nothing is written until the owner names a folder.

Revision ID: 0107_add_library_views_settings
Revises: 0106_add_picture_workflow_hashes
Create Date: 2026-08-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0107_add_library_views_settings"
down_revision: Union[str, None] = "0106_add_picture_workflow_hashes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COLUMNS = ("views_root", "views_kinds")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("library_settings")}
    for column in _COLUMNS:
        if column not in existing_cols:
            op.add_column(
                "library_settings", sa.Column(column, sa.String(), nullable=True)
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("library_settings")}
    for column in _COLUMNS:
        if column in existing_cols:
            op.drop_column("library_settings", column)
