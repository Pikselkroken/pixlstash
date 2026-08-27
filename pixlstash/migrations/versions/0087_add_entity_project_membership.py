"""Add characterprojectmember / picturesetprojectmember join tables (issue #125).

A character or picture set could previously belong to exactly one project, via the
scalar ``character.project_id`` / ``pictureset.project_id`` foreign keys. Issue
#125 makes both many-to-many, mirroring the shape ``pictureprojectmember``
(migration ``0009``) already gives pictures.

The change is deliberately **additive and non-destructive**: both scalar FK columns
stay, and stay populated - they now hold the entity's *primary* project (the lowest
member project id, or ``NULL``). Reads move to the join tables; the FKs are retired
by a later cleanup release once nothing reads them. Nothing is dropped here, so the
migration is safe to run against a deployed database and reversible without data
loss (``downgrade`` drops only the new tables - every entity keeps its original
single-project assignment in the untouched FK).

Backfill inserts one join row per non-``NULL`` scalar FK, so an existing library
comes up with exactly the membership it had before, expressed in the new shape.

Revision ID: 0087_add_entity_project_membership
Revises: 0086_add_operation_log
Create Date: 2026-07-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0087_add_entity_project_membership"
down_revision: Union[str, None] = "0086_add_operation_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "project" not in existing_tables:
        # Nothing to link: no project table means no project assignments exist.
        # A fresh database builds both join tables from the models in
        # ``0001_baseline``; every real deployed database has had ``project``
        # since ``0005_add_projects``. This branch only fires for a partially
        # hand-built database (the migration test fixtures).
        return

    has_character = "character" in existing_tables
    has_pictureset = "pictureset" in existing_tables

    if has_character and "characterprojectmember" not in existing_tables:
        op.create_table(
            "characterprojectmember",
            sa.Column("character_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(
                ["character_id"], ["character.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("character_id", "project_id"),
        )
        op.create_index(
            "ix_characterprojectmember_character_id",
            "characterprojectmember",
            ["character_id"],
            unique=False,
        )
        op.create_index(
            "ix_characterprojectmember_project_id",
            "characterprojectmember",
            ["project_id"],
            unique=False,
        )

    if has_pictureset and "picturesetprojectmember" not in existing_tables:
        op.create_table(
            "picturesetprojectmember",
            sa.Column("set_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["set_id"], ["pictureset.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("set_id", "project_id"),
        )
        op.create_index(
            "ix_picturesetprojectmember_set_id",
            "picturesetprojectmember",
            ["set_id"],
            unique=False,
        )
        op.create_index(
            "ix_picturesetprojectmember_project_id",
            "picturesetprojectmember",
            ["project_id"],
            unique=False,
        )

    # Backfill memberships from the legacy single-project foreign keys. The join
    # is against ``project`` so a dangling FK (a project row deleted without the
    # pointer being cleared) does not violate the new FK constraint.
    if has_character:
        bind.execute(
            sa.text(
                """
                INSERT OR IGNORE INTO characterprojectmember (character_id, project_id)
                SELECT c.id, c.project_id
                FROM character AS c
                JOIN project AS p ON p.id = c.project_id
                WHERE c.project_id IS NOT NULL
                """
            )
        )
    if has_pictureset:
        bind.execute(
            sa.text(
                """
                INSERT OR IGNORE INTO picturesetprojectmember (set_id, project_id)
                SELECT s.id, s.project_id
                FROM pictureset AS s
                JOIN project AS p ON p.id = s.project_id
                WHERE s.project_id IS NOT NULL
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "picturesetprojectmember" in existing_tables:
        op.drop_index(
            "ix_picturesetprojectmember_project_id",
            table_name="picturesetprojectmember",
        )
        op.drop_index(
            "ix_picturesetprojectmember_set_id", table_name="picturesetprojectmember"
        )
        op.drop_table("picturesetprojectmember")

    if "characterprojectmember" in existing_tables:
        op.drop_index(
            "ix_characterprojectmember_project_id", table_name="characterprojectmember"
        )
        op.drop_index(
            "ix_characterprojectmember_character_id",
            table_name="characterprojectmember",
        )
        op.drop_table("characterprojectmember")
