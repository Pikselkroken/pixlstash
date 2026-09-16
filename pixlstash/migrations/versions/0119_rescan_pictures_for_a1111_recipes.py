"""Re-read pictures with no workflow for A1111 generation data.

The workflow scan used to file only a ComfyUI graph, so a picture from A1111 or
one of its forks was stamped scanned with no keys. Clearing the stamp on every
picture that has none hands it back to the scan, which now reads that data
(``services/a1111_recipe.py``). The formats it can be read from only: a PNG
chunk or a JPEG's or WebP's EXIF. A picture carrying nothing is stamped again on
the read, so this costs one pass over those files and nothing after it.

Revision ID: 0119_rescan_pictures_for_a1111_recipes
Revises: 0118_add_generation_tables
Create Date: 2026-09-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0119_rescan_pictures_for_a1111_recipes"
down_revision: Union[str, None] = "0118_add_generation_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    if "picture" not in sa.inspect(op.get_bind()).get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    extensions = " OR ".join(
        f"lower(file_path) LIKE '%.{suffix}'"
        for suffix in ("png", "jpg", "jpeg", "webp")
    )
    op.execute(
        "UPDATE picture SET workflow_hash_version = NULL "
        "WHERE workflow_instance_hash IS NULL "
        "AND workflow_hash_version IS NOT NULL "
        f"AND ({extensions})"
    )


def downgrade() -> None:
    # Nothing to restore: an older build re-reads the cleared pictures, finds no
    # ComfyUI graph and stamps them again. Re-stamping here instead would also
    # stamp pictures that were never scanned.
    pass
