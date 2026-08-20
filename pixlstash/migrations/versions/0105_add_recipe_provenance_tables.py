"""Generation provenance: the vault half of the recipe tables (v1.11 Phase 2).

Two new vault tables and nothing else: no column is added to an existing table,
no data is rewritten, and no ingest behaviour changes in this revision. They sit
empty until the canonicalizer and the ingest hook land on top of them.

**The other half is hub-side.** ``recipe``, ``recipe_asset`` and
``recipe_instance`` are created by ``pixlstash/hub/schema.py`` (``_V2_RECIPE_TABLES``),
because a workflow is a fact about the machine rather than about one library:
measured on real libraries, 70% of recipes appear in more than one, and a
two-library comparison found complete containment. Two libraries sharing a
workflow is the norm, which is the multi-library placement test answered
directly, and it is what lets a workflow outlive every picture that used it.

What lands here is the half that names a ``picture.id`` and therefore cannot be
anything else. The two halves are joined by ``generation.instance_hash``, a TEXT
column and deliberately not an integer id: no foreign key spans the hub and a
vault, and SQLite reissues a deleted row's id to the next insert, so an integer
would silently come to name a different instance. Same rule as revision 0103.

**Nothing here dies with a picture.** Both tables null the pointer rather than
cascade, and both keep a sha256 as the identity that survives it:

* ``generation.image_id`` holds the seed, which is a third of what recreating a
  deleted image takes (recipe + instance + seed). Cascading it would mean
  deleting an image destroyed the recreation while leaving the workflow
  standing and apparently intact. What remains is a **ghost**, and permanently
  forgetting one is a delete of this row: the recipe is a graph of node types
  and is not personal data, while a prompt and a thumbnail can be.
* ``generation_input.image_id`` is the resolution lock, the record of which
  images a run consumed. Deleting one of those images does not unmake the run.

Revision ID: 0105_add_recipe_provenance_tables
Revises: 0104_add_picture_orientation
Create Date: 2026-08-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0105_add_recipe_provenance_tables"
down_revision: Union[str, None] = "0104_add_picture_orientation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


# (index name, table, columns, unique). Declared once and created outside the
# create_table branch, because a fresh database never enters it: the baseline's
# SQLModel.metadata.create_all() has already built these tables from the models
# added alongside this revision. An index created only inside that branch would
# exist on migrated databases and nowhere else, which is the gap revision 0103
# had to fix after the fact. The names are the ones SQLModel derives from the
# model declarations, so both paths converge on exactly this set.
_INDEXES = [
    ("ix_generation_image_id", "generation", ["image_id"], False),
    ("ix_generation_image_sha256", "generation", ["image_sha256"], False),
    # The join to the hub's recipe_instance, and the query behind "everything
    # made with this workflow". Non-unique: one instance has many generations.
    ("ix_generation_instance_hash", "generation", ["instance_hash"], False),
    (
        "ix_generation_input_image_sha256",
        "generation_input",
        ["image_sha256"],
        False,
    ),
    ("ix_generation_input_image_id", "generation_input", ["image_id"], False),
]

# Dropped in reverse dependency order so a downgrade never leaves a foreign key
# pointing at a table that is already gone.
_TABLES_IN_DROP_ORDER = ["generation_input", "generation"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "generation" not in existing_tables:
        op.create_table(
            "generation",
            sa.Column("id", sa.Integer(), nullable=False),
            # Nullable, and nulled rather than cascaded on delete: this row
            # holds the seed, so it is a third of what recreating the image
            # takes. It has to outlive the picture. image_sha256 is what it
            # keeps once the pointer is gone.
            sa.Column("image_id", sa.Integer(), nullable=True),
            sa.Column("image_sha256", sa.String(), nullable=True),
            # The hub's recipe_instance.instance_hash. Not a foreign key: the
            # referenced table is in another database file.
            sa.Column("instance_hash", sa.String(), nullable=False),
            sa.Column("seed", sa.Integer(), nullable=True),
            sa.Column("overrides", sa.String(), nullable=True),
            # No foreign key: remote_job is Phase 5 and does not exist yet.
            sa.Column("remote_job_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["image_id"], ["picture.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "generation_input" not in existing_tables:
        op.create_table(
            "generation_input",
            sa.Column("generation_id", sa.Integer(), nullable=False),
            sa.Column("node_ref", sa.String(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("image_sha256", sa.String(), nullable=False),
            sa.Column("image_id", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(
                ["generation_id"], ["generation.id"], ondelete="CASCADE"
            ),
            # SET NULL, not CASCADE: the lock records what a run consumed and
            # must survive the deletion of an image it names.
            sa.ForeignKeyConstraint(["image_id"], ["picture.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("generation_id", "node_ref", "position"),
        )

    # Re-inspect: the inspector above was built before the CREATE TABLEs and
    # caches what it saw, so it does not know the indexes of a table this
    # revision has just created.
    inspector = sa.inspect(bind)
    for name, table, columns, unique in _INDEXES:
        existing = {idx["name"] for idx in inspector.get_indexes(table)}
        if name not in existing:
            op.create_index(name, table, columns, unique=unique)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for table in _TABLES_IN_DROP_ORDER:
        if table in existing_tables:
            op.drop_table(table)
