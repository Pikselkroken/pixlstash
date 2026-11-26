"""Add the Studio's tables (#2819): scenes, takes, items and favourites.

``scene`` is a cast in a location (its cast, props, setting and the shoots
still drawing as JSON), ``scene_take`` the pictures its shoots made,
``studio_item`` which picture sets are outfits, locations and props, and
``studio_favourite`` the starred ones. See ``db_models/studio.py``.

Schema-only and additive.

Revision ID: 0151_add_studio
Revises: 0150_add_video_copy
Create Date: 2026-11-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0151_add_studio"
down_revision: Union[str, None] = "0150_add_video_copy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if not {"picture", "pictureset", "project"} <= tables:
        # Partial databases used by migration tests carry none of these.
        return
    if "scene" not in tables:
        op.create_table(
            "scene",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column(
                "project_id",
                sa.Integer(),
                sa.ForeignKey("project.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "location_set_id",
                sa.Integer(),
                sa.ForeignKey("pictureset.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("camera", sa.JSON(), nullable=True),
            sa.Column("setting", sa.JSON(), nullable=True),
            sa.Column("cast", sa.JSON(), nullable=True),
            sa.Column("props", sa.JSON(), nullable=True),
            sa.Column("runs", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_scene_project_id", "scene", ["project_id"])
    if "scene_take" not in tables:
        op.create_table(
            "scene_take",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "scene_id",
                sa.Integer(),
                sa.ForeignKey("scene.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "picture_id",
                sa.Integer(),
                sa.ForeignKey("picture.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("kind", sa.String(), nullable=False, server_default="still"),
            sa.Column("from_picture_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_scene_take_scene_id", "scene_take", ["scene_id"])
        op.create_index("ix_scene_take_picture_id", "scene_take", ["picture_id"])
    if "studio_item" not in tables:
        op.create_table(
            "studio_item",
            sa.Column(
                "set_id",
                sa.Integer(),
                sa.ForeignKey("pictureset.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_studio_item_kind", "studio_item", ["kind"])
    if "studio_favourite" not in tables:
        op.create_table(
            "studio_favourite",
            sa.Column("kind", sa.String(), primary_key=True),
            sa.Column("item_id", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("studio_favourite", "studio_item", "scene_take", "scene"):
        if table in tables:
            op.drop_table(table)
