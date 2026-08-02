"""Add ``stackcohesion.nearest_edges``: every member's closest sibling.

The Mixed stacks page could not say *how* unlike the rest a stranded member was,
because the only per-member number it had (``strongest_edge``) is defined as the
best edge that **survives at the row's threshold** and is therefore ``None`` for
a stranded member by construction. The UI printed an en dash and the row said
the picture matched nothing, about members whose closest sibling was 89%
similar (7 bits of 64, where the 0.90 cut is 6).

Compounding it, the cached edge list is pruned at ``MAX_CACHED_HAMMING`` (22
bits, the widest distance that can be an edge at *any* admissible threshold), so
for a member whose closest sibling is further away than that the number was
never stored at all. That prune is still right for ``edges``: dropping those
pairs loses nothing when the question is "is this an edge". It is wrong when the
question is "how close is this member to anything", which is why the answer gets
its own column instead of widening the edge list, which would be O(n^2) storage
for a fact that is O(n).

``nearest_edges`` is JSON ``[[picture_id, closest_picture_id, hamming], ...]``,
one entry per member with at least one comparable sibling, never thresholded and
never pruned. It is folded out of the same upper-triangle pass that already
computes ``edges``, so it costs no extra query and no extra scan.

**No data is cleared here and none needs to be.** The cache's staleness test is
``content_fingerprint``, and that digest now carries a cache-format version, so
every row written before this migration mismatches and is recomputed:
``MissingStackCohesionFinder`` re-queues them, and until it catches up the read
path recomputes inline (it already does that on any cache miss). An
unmigrated-cache database is therefore slower for one request, never wrong.

Revision ID: 0092_add_stackcohesion_nearest_edges
Revises: 0091_add_mixed_stack_cohesion_and_dismissal
Create Date: 2026-08-02 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0092_add_stackcohesion_nearest_edges"
down_revision: Union[str, None] = "0091_add_mixed_stack_cohesion_and_dismissal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_TABLE = "stackcohesion"
_COLUMN = "nearest_edges"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        # The table itself arrives with 0091 (or with the baseline's
        # create_all on a fresh database); nothing to widen if it is absent.
        return
    existing_cols = {col["name"] for col in inspector.get_columns(_TABLE)}
    if _COLUMN not in existing_cols:
        # A fresh database already has the column from the baseline's
        # ``SQLModel.metadata.create_all()``; only an older one needs the ALTER.
        # ``server_default`` so the NOT NULL is satisfiable on existing rows,
        # which are stale by fingerprint anyway and will be rewritten.
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.String(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    existing_cols = {col["name"] for col in inspector.get_columns(_TABLE)}
    if _COLUMN in existing_cols:
        op.drop_column(_TABLE, _COLUMN)
