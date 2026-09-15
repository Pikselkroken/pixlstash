"""How each picture was made: ``generation`` and ``generation_input``.

The vault half of a recipe (``pixlstash/db_models/generation.py``); the
instance it points at is a hub table. Both new tables die with their picture.

**And one reset, which is what fills them for pictures already in the library.**
Their seed and parameters exist only in the files, so every picture that already
carries a workflow is handed back to the ComfyUI extraction by clearing its
scanned marker, the pattern this project uses for any backfill. The revisit
keeps the keys it finds when a file cannot be read, so an unplugged drive loses
nothing (``tasks/comfyui_extraction_task.py``).

Revision ID: 0118_add_generation_tables
Revises: 0117_add_pending_ghost_cascade
Create Date: 2026-09-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0118_add_generation_tables"
down_revision: Union[str, None] = "0117_add_pending_ghost_cascade"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

# (name, table, columns). Created outside the create_table branches because a
# fresh database never enters them: the baseline's create_all() has already
# built both tables from the models. The names are the ones SQLModel derives.
_INDEXES = (
    ("ix_generation_input_pixel_sha", "generation_input", ["pixel_sha"]),
    ("ix_generation_input_input_picture_id", "generation_input", ["input_picture_id"]),
)


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "picture" not in tables:
        # Partial databases used by migration tests carry no picture table, and
        # both foreign keys point at it.
        return

    if "generation" not in tables:
        op.create_table(
            "generation",
            sa.Column("picture_id", sa.Integer(), nullable=False),
            sa.Column("seed", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(["picture_id"], ["picture.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("picture_id"),
        )
    if "generation_input" not in tables:
        op.create_table(
            "generation_input",
            sa.Column("picture_id", sa.Integer(), nullable=False),
            sa.Column("node_ref", sa.String(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("pixel_sha", sa.String(), nullable=False),
            sa.Column("input_picture_id", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(
                ["picture_id"], ["generation.picture_id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["input_picture_id"], ["picture.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("picture_id", "node_ref", "position"),
        )

    # Re-inspected: the inspector above cached the table list before the CREATEs.
    inspector = sa.inspect(bind)
    for name, table, columns in _INDEXES:
        if name not in {idx["name"] for idx in inspector.get_indexes(table)}:
            op.create_index(name, table, columns)

    op.execute(
        "UPDATE picture SET workflow_hash_version = NULL "
        "WHERE workflow_instance_hash IS NOT NULL"
    )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("generation_input", "generation"):
        if table in tables:
            op.drop_table(table)
