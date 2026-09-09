"""Record the owner's caption-file choices on the folder-mapping commit.

The read now reports the caption-file patterns beside the pictures and the
owner says which are tags, which are descriptions and which to ignore. A
commit resumed after a crash must honour that answer rather than probe the
conventions again and import a file the owner said to leave alone, so it is
recorded beside the assignments. ``"[]"`` on every existing row: no answer,
which is what every commit before this column meant.

Revision ID: 0115_add_folder_mapping_commit_captions
Revises: 0114_remove_views_and_project_cover
Create Date: 2026-09-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0115_add_folder_mapping_commit_captions"
down_revision: Union[str, None] = "0114_remove_views_and_project_cover"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def _columns(bind) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns("folder_mapping_commit")}


def upgrade() -> None:
    if "captions" not in _columns(op.get_bind()):
        op.add_column(
            "folder_mapping_commit",
            sa.Column("captions", sa.String(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    if "captions" in _columns(op.get_bind()):
        with op.batch_alter_table("folder_mapping_commit") as batch:
            batch.drop_column("captions")
