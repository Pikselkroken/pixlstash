"""Reset picture.metadata_hash so it is recomputed with membership folded in.

``database._compute_picture_metadata_hash`` now also covers picture-set and
project membership, so a picture moved between sets/projects is correctly
detected as changed by the restore preview and snapshot identical-state
detection. Existing hashes were computed with the previous algorithm (columns +
tags + faces only); reset them to NULL so they are recomputed with the current
algorithm. The hash is repopulated lazily — by the ``after_flush`` hook on the
next write and by ``RestoreService.compare_hashes`` on demand — so no
application logic belongs here. Data-only; no schema change.

Revision ID: 0050_reset_metadata_hash_membership
Revises: 0049_snapshots
Create Date: 2026-05-31 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0050_reset_metadata_hash_membership"
down_revision: Union[str, None] = "0049_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    # Force a recompute with the membership-inclusive algorithm. No-op on a
    # fresh DB where the column is already NULL.
    op.execute("UPDATE picture SET metadata_hash = NULL")


def downgrade() -> None:
    # Data-only reset; the prior hash values cannot be restored, and the older
    # code recomputes its own hashes on demand, so this is a no-op.
    pass
