"""Reset picture.metadata_hash to NULL for recomputation with corrected algorithm.

The hash previously included derived scores (aesthetic_score, smart_score,
text_score) which are regenerable and not user-controlled metadata.  These
columns have now been excluded from the hash so that recalculating them does
not make a snapshot appear as changed.  Setting metadata_hash to NULL forces
recomputation on the next compare_hashes call.

Revision ID: 0050_reset_metadata_hash
Revises: 0049_add_picture_metadata_hash
Create Date: 2026-05-27 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0050_reset_metadata_hash"
down_revision: Union[str, None] = "0049_add_picture_metadata_hash"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    op.execute("UPDATE picture SET metadata_hash = NULL")


def downgrade() -> None:
    # Hashes cannot be reconstructed deterministically here; leave as-is.
    pass
