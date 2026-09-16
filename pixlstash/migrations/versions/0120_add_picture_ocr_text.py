"""Add the text read out of a picture (#1197).

``ocr_text`` holds the words, ``ocr_words`` the same words with their boxes.
Both start NULL, so ``MissingOcrFinder`` reads every picture whose text score
qualifies.

Revision ID: 0120_add_picture_ocr_text
Revises: 0119_rescan_pictures_for_a1111_recipes
Create Date: 2026-09-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0120_add_picture_ocr_text"
down_revision: Union[str, None] = "0119_rescan_pictures_for_a1111_recipes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    for column in ("ocr_text", "ocr_words"):
        if column not in existing_cols:
            op.add_column("picture", sa.Column(column, sa.String(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    for column in ("ocr_words", "ocr_text"):
        if column in existing_cols:
            op.drop_column("picture", column)
