"""Add the prompt-match score and its finder's probe index.

``picture.prompt_match`` is whether the picture looks like its prompt at all
(``pixlstash/scoring/prompt_match.py``). NULL for every existing picture, which
``MissingPromptMatchFinder`` reads as "not scored yet" for the pictures that
have a prompt; the rest stay NULL. ``ix_picture_prompt_match_missing`` serves
that finder's probe, partial on the prompt as well so the pictures without one
are not in it. Declared on the model too, so the create is guarded on the name.

Schema-only and additive.

Revision ID: 0128_add_picture_prompt_match
Revises: 0127_add_picture_run_workflow_version
Create Date: 2026-10-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0128_add_picture_prompt_match"
down_revision: Union[str, None] = "0127_add_picture_run_workflow_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_INDEX = "ix_picture_prompt_match_missing"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "prompt_match" not in existing_cols:
        op.add_column("picture", sa.Column("prompt_match", sa.Float(), nullable=True))
    if _INDEX not in {ix["name"] for ix in inspector.get_indexes("picture")}:
        op.create_index(
            _INDEX,
            "picture",
            ["prompt_match", "deleted", "id"],
            sqlite_where=sa.text(
                "prompt_match IS NULL AND comfyui_positive_prompt IS NOT NULL"
            ),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        return
    if _INDEX in {ix["name"] for ix in inspector.get_indexes("picture")}:
        op.drop_index(_INDEX, table_name="picture")
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "prompt_match" in existing_cols:
        op.drop_column("picture", "prompt_match")
