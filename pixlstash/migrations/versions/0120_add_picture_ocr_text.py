"""Add the text read out of a picture (#1197).

``ocr_text`` holds the words, ``ocr_words`` the same words with their boxes.
Both start NULL, so ``MissingOcrFinder`` reads every picture whose text score
qualifies, found through the partial index ``ix_picture_ocr_unread``.

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

_UNREAD_INDEX = "ix_picture_ocr_unread"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    for column in ("ocr_text", "ocr_words"):
        if column not in existing_cols:
            op.add_column("picture", sa.Column(column, sa.String(), nullable=True))

    # Declared on the model too, so a fresh database already has it from the
    # baseline. Guarded by name so both paths converge on the same index.
    if _UNREAD_INDEX not in {idx["name"] for idx in inspector.get_indexes("picture")}:
        op.create_index(
            _UNREAD_INDEX,
            "picture",
            ["ocr_text", "deleted", "text_score"],
            sqlite_where=sa.text("ocr_text IS NULL"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        return
    if _UNREAD_INDEX in {idx["name"] for idx in inspector.get_indexes("picture")}:
        op.drop_index(_UNREAD_INDEX, table_name="picture")
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    for column in ("ocr_words", "ocr_text"):
        if column in existing_cols:
            op.drop_column("picture", column)
