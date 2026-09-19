"""The looks the owner keeps: ``saved_recipe`` (v1.12 Workflows & Recipes, B6).

A saved recipe is authored rather than derived, so it lives in the vault and
travels with a snapshot; the workflow it belongs to lives in the hub and is
named by ``workflow_key`` as text, across the database boundary
(``pixlstash/db_models/saved_recipe.py``).

Conditional, because the baseline's ``create_all()`` has already built the table
from the model on a fresh database and a blind ``CREATE TABLE`` would fail
there. No data step: nothing existing becomes a saved recipe.

Revision ID: 0121_add_saved_recipe
Revises: 0120_add_picture_ocr_text
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0121_add_saved_recipe"
down_revision: Union[str, None] = "0120_add_picture_ocr_text"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_WORKFLOW_KEY_INDEX = "ix_saved_recipe_workflow_key"
_SOURCE_PICTURE_INDEX = "ix_saved_recipe_source_picture_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "picture" not in inspector.get_table_names():
        # Partial databases used by migration tests carry no picture table, and
        # the source-picture foreign key points at it.
        return

    if not inspector.has_table("saved_recipe"):
        op.create_table(
            "saved_recipe",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("workflow_key", sa.String(), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("negative", sa.Text(), nullable=True),
            sa.Column("loras", sa.Text(), nullable=False),
            sa.Column("overrides", sa.Text(), nullable=False),
            # Text for the reason ``generation.seed`` is text: ComfyUI draws
            # seeds up to 2**64 - 1 and SQLite's INTEGER stops at 2**63 - 1.
            sa.Column("seed", sa.String(), nullable=True),
            sa.Column("keep_seed", sa.Boolean(), nullable=False),
            sa.Column("source_picture_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["source_picture_id"], ["picture.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    # Re-inspected: the inspector above cached the table list before the CREATE.
    # Both indexes are declared on the model too, so a fresh database already
    # has them from the baseline; guarded by name so both paths converge.
    inspector = sa.inspect(bind)
    existing = {idx["name"] for idx in inspector.get_indexes("saved_recipe")}
    if _WORKFLOW_KEY_INDEX not in existing:
        op.create_index(_WORKFLOW_KEY_INDEX, "saved_recipe", ["workflow_key"])
    if _SOURCE_PICTURE_INDEX not in existing:
        op.create_index(_SOURCE_PICTURE_INDEX, "saved_recipe", ["source_picture_id"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("saved_recipe"):
        op.drop_table("saved_recipe")
