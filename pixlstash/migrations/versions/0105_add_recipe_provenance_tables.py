"""Recipe, instance and generation provenance tables (v1.11 AI-toolkit Phase 2).

Five new vault tables and nothing else: no column is added to an existing table,
no data is rewritten, and no ingest behaviour changes in this revision. They sit
empty until the canonicalizer and the ingest hook land on top of them.

They are vault tables because they reference pictures, and a picture belongs to
one library. The model shelf they resolve against is hub-side, which is why
``recipe_asset.resolved_adapter_sha256`` and ``resolved_checkpoint_sha256`` hold
a hash rather than an integer model id: no foreign key can span the hub and a
vault, and SQLite reissues a deleted row's id to the next insert, so an integer
would silently re-point at a different model. Same rule as revision 0103.

**Nothing here dies with a picture.** Both tables that point at ``picture`` null
the pointer rather than cascade, and both keep a sha256 as the identity that
survives it:

* ``generation.image_id`` holds the seed, which is a third of what recreating a
  deleted image takes (recipe + instance + seed). Cascading it would mean
  deleting an image destroyed the recreation while leaving the workflow
  standing and apparently intact. What remains is a **ghost**, and permanently
  forgetting one is a delete of this row: the recipe is a graph of node types
  and is not personal data, while a prompt and a thumbnail can be.
* ``generation_input.image_id`` is the resolution lock, the record of which
  images a run consumed. Deleting one of those images does not unmake the run.

``recipe`` carries three hashes rather than one, all exact, nested loosest-last:
``structural_hash`` is the identity and is never merged, ``topology_hash`` drops
asset filenames so one graph run against two checkpoints is one entry, and
``role_hash`` groups by what each node *feeds into* rather than what it is
called, which is what makes the Workflows view browsable and merges node-pack
variants without a synonym table to maintain.

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
    ("ix_recipe_engine", "recipe", ["engine"], False),
    ("ix_recipe_structural_hash", "recipe", ["structural_hash"], False),
    # The Workflows view pages on role_hash; topology_hash is the model-variant
    # rollup inside a group. Both are non-unique by design: many recipes share
    # one, which is the entire point of them.
    ("ix_recipe_topology_hash", "recipe", ["topology_hash"], False),
    ("ix_recipe_role_hash", "recipe", ["role_hash"], False),
    (
        "ix_recipe_structural_identity",
        "recipe",
        ["structural_hash", "hash_version"],
        True,
    ),
    ("ix_recipe_asset_recipe_id", "recipe_asset", ["recipe_id"], False),
    ("ix_recipe_asset_asset_type", "recipe_asset", ["asset_type"], False),
    ("ix_recipe_asset_asset_sha256", "recipe_asset", ["asset_sha256"], False),
    ("ix_recipe_asset_asset_filename", "recipe_asset", ["asset_filename"], False),
    (
        "ix_recipe_asset_resolved_adapter_sha256",
        "recipe_asset",
        ["resolved_adapter_sha256"],
        False,
    ),
    (
        "ix_recipe_asset_resolved_checkpoint_sha256",
        "recipe_asset",
        ["resolved_checkpoint_sha256"],
        False,
    ),
    ("ix_recipe_instance_recipe_id", "recipe_instance", ["recipe_id"], False),
    (
        "ix_recipe_instance_instance_hash",
        "recipe_instance",
        ["instance_hash"],
        True,
    ),
    ("ix_generation_image_id", "generation", ["image_id"], False),
    ("ix_generation_image_sha256", "generation", ["image_sha256"], False),
    ("ix_generation_instance_id", "generation", ["instance_id"], False),
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
_TABLES_IN_DROP_ORDER = [
    "generation_input",
    "generation",
    "recipe_instance",
    "recipe_asset",
    "recipe",
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "recipe" not in existing_tables:
        op.create_table(
            "recipe",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("engine", sa.String(), nullable=False),
            sa.Column("engine_version", sa.String(), nullable=True),
            sa.Column("document", sa.String(), nullable=False),
            sa.Column("document_ui", sa.String(), nullable=True),
            sa.Column("structural_hash", sa.String(), nullable=False),
            # Nullable: they describe a node graph, and an ai-toolkit training
            # config is a recipe with no graph to roll up.
            sa.Column("topology_hash", sa.String(), nullable=True),
            sa.Column("role_hash", sa.String(), nullable=True),
            sa.Column("hash_version", sa.String(), nullable=False, server_default="v1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if "recipe_asset" not in existing_tables:
        op.create_table(
            "recipe_asset",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("recipe_id", sa.Integer(), nullable=False),
            sa.Column("asset_type", sa.String(), nullable=False),
            sa.Column("asset_sha256", sa.String(), nullable=True),
            sa.Column("asset_filename", sa.String(), nullable=True),
            sa.Column("resolved_adapter_sha256", sa.String(), nullable=True),
            sa.Column("resolved_checkpoint_sha256", sa.String(), nullable=True),
            sa.Column("role", sa.String(), nullable=True),
            sa.Column("strength", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(["recipe_id"], ["recipe.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "recipe_instance" not in existing_tables:
        op.create_table(
            "recipe_instance",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("recipe_id", sa.Integer(), nullable=False),
            sa.Column("instance_hash", sa.String(), nullable=False),
            sa.Column("prompt_positive", sa.String(), nullable=True),
            sa.Column("prompt_negative", sa.String(), nullable=True),
            sa.Column("params", sa.String(), nullable=True),
            sa.Column("hash_version", sa.String(), nullable=False, server_default="v1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["recipe_id"], ["recipe.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

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
            sa.Column("instance_id", sa.Integer(), nullable=False),
            sa.Column("seed", sa.Integer(), nullable=True),
            sa.Column("overrides", sa.String(), nullable=True),
            # No foreign key: remote_job is Phase 5 and does not exist yet.
            sa.Column("remote_job_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["image_id"], ["picture.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(
                ["instance_id"], ["recipe_instance.id"], ondelete="CASCADE"
            ),
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
