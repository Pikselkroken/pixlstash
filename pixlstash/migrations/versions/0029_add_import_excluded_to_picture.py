"""Add import_excluded to picture.

Marks reference-folder pictures that have been permanently removed from the
scrapheap but whose source files could not be deleted (allow_delete_file=False
on the owning ReferenceFolder).  The record is kept so the scan task does not
re-import the file on the next pass.  These pictures are invisible to all
normal queries.

Revision ID: 0029_add_import_excluded_to_picture
Revises: 0028_add_smart_score
Create Date: 2026-04-21 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0029_add_import_excluded_to_picture"
down_revision: Union[str, None] = "0028_add_smart_score"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "import_excluded" not in existing_cols:
        op.add_column(
            "picture",
            sa.Column(
                "import_excluded",
                sa.Boolean,
                nullable=False,
                server_default="0",
            ),
        )
        op.create_index(
            "ix_picture_import_excluded", "picture", ["import_excluded"]
        )


def downgrade() -> None:
    op.drop_index("ix_picture_import_excluded", table_name="picture")
    op.drop_column("picture", "import_excluded")
