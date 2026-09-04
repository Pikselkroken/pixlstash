"""Re-queue pictures whose likeness parameters are missing but look done.

Data-only reset, no schema change. ``LikenessParametersTask`` writes
``likeness_parameters`` and ``size_bin_index`` in one statement, and the
parameters finder treats ``size_bin_index IS NULL`` as the sole pending-work
marker (``LikenessParameterUtils.find_next_work``). A row with the index set
and the blob NULL is therefore invisible to that finder, while the pairs
batch (``LikenessUtils.get_next_work_batch``) requires the blob, so such a
picture sits in the likeness queue forever and the Likeness Pairs progress
stops short of its total. Restored snapshots have produced exactly that row
shape. Nulling ``size_bin_index`` on those rows hands them back to the
finder, which rewrites both columns and the pairs task drains them.

Revision ID: 0113_reset_size_bin_without_likeness_parameters
Revises: 0112_add_image_embedding_probe_indexes
Create Date: 2026-09-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0113_reset_size_bin_without_likeness_parameters"
down_revision: Union[str, None] = "0112_add_image_embedding_probe_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    if "picture" in sa.inspect(op.get_bind()).get_table_names():
        op.execute(
            "UPDATE picture SET size_bin_index = NULL "
            "WHERE likeness_parameters IS NULL AND size_bin_index IS NOT NULL"
        )


def downgrade() -> None:
    # The reset only asks for a recompute; there is nothing to restore.
    pass
