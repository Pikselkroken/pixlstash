"""add project_id to picture

Revision ID: 0007_add_picture_project_id
Revises: 0006_add_attachment_url
Create Date: 2026-03-24 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_add_picture_project_id"  # noqa: F841
down_revision: Union[str, None] = "0006_add_attachment_url"  # noqa: F841
branch_labels: Union[str, Sequence[str], None] = None  # noqa: F841
depends_on: Union[str, Sequence[str], None] = None  # noqa: F841


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_columns = {col["name"] for col in inspector.get_columns("picture")}
    if "project_id" not in existing_columns:
        with op.batch_alter_table("picture") as batch_op:
            batch_op.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
            batch_op.create_index("ix_picture_project_id", ["project_id"])

    # Backfill project_id from character assignment (via face records).
    # Only sets project_id where it is currently NULL, preferring the character's
    # project when multiple faces point to different characters on the same picture.
    bind.execute(
        sa.text(
            """
            UPDATE picture
            SET project_id = (
                SELECT c.project_id
                FROM face f
                JOIN character c ON c.id = f.character_id
                WHERE f.picture_id = picture.id
                  AND c.project_id IS NOT NULL
                LIMIT 1
            )
            WHERE picture.project_id IS NULL
              AND EXISTS (
                SELECT 1
                FROM face f
                JOIN character c ON c.id = f.character_id
                WHERE f.picture_id = picture.id
                  AND c.project_id IS NOT NULL
              )
            """
        )
    )

    # Backfill project_id from picture set membership.
    # Only updates pictures that still have no project_id after the character pass.
    bind.execute(
        sa.text(
            """
            UPDATE picture
            SET project_id = (
                SELECT ps.project_id
                FROM picturesetmember psm
                JOIN pictureset ps ON ps.id = psm.set_id
                WHERE psm.picture_id = picture.id
                  AND ps.project_id IS NOT NULL
                LIMIT 1
            )
            WHERE picture.project_id IS NULL
              AND EXISTS (
                SELECT 1
                FROM picturesetmember psm
                JOIN pictureset ps ON ps.id = psm.set_id
                WHERE psm.picture_id = picture.id
                  AND ps.project_id IS NOT NULL
              )
            """
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("picture") as batch_op:
        batch_op.drop_index("ix_picture_project_id")
        batch_op.drop_column("project_id")
