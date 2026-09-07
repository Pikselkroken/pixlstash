"""Remove the PixlStash Views settings and the unused project cover image.

Product-scope decision: PixlStash Views is withdrawn. Its settings section was
never wired into the app, so ``library_settings.views_root`` /
``library_settings.views_kinds`` were only ever writable through the
``PATCH /server-config/views`` API that shipped in v1.11. Dropping them
discards nothing a user typed into the product.

``project.cover_image_path`` arrived with 0005_add_projects and no frontend or
Electron code has ever read or written it, so every row holds NULL.

Both drops are conditional: 0001_baseline builds a fresh database with
``SQLModel.metadata.create_all()`` from the *current* models, which no longer
declare these columns, so a blind ``ALTER TABLE ... DROP COLUMN`` would fail
there. Same inspector-guarded shape as the drops in
0071_remove_tag_review_scoring_subsystem, which is the precedent for it - not
this revision's predecessor, which is 0113 (see Revises below).

Revision ID: 0114_remove_views_and_project_cover
Revises: 0113_reset_size_bin_without_likeness_parameters
Create Date: 2026-09-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0114_remove_views_and_project_cover"
down_revision: Union[str, None] = "0113_reset_size_bin_without_likeness_parameters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

#: table -> columns this migration removes.
_DROPPED = {
    "library_settings": ("views_root", "views_kinds"),
    "project": ("cover_image_path",),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for table, columns in _DROPPED.items():
        if table not in tables:
            continue
        existing = {col["name"] for col in inspector.get_columns(table)}
        for column in columns:
            if column in existing:
                op.drop_column(table, column)


def downgrade() -> None:
    """Recreate the columns, nullable and empty.

    Every one was nullable with no server default when it was added
    (0005_add_projects, 0107_add_library_views_settings), so a fresh
    0113 database and a downgraded one land on the same schema. The values
    are not restored: views settings are a withdrawn feature's configuration
    and the cover path was never populated.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for table, columns in _DROPPED.items():
        if table not in tables:
            continue
        existing = {col["name"] for col in inspector.get_columns(table)}
        for column in columns:
            if column not in existing:
                op.add_column(table, sa.Column(column, sa.String(), nullable=True))
