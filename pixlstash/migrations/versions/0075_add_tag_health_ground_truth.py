"""Add the ground_truth count column to tag_health.

``ground_truth`` is the number of distinct non-deleted, in-scope pictures that
carry the (DEFAULT_TAG_MERGES-folded) tag. The board needs it to tell the user,
*before* they click "Start review", that a review would provably yield nothing:
at zero ground truth ``tag_scan_service.scan_tag`` takes its confidence-only
fallback branch, whose candidate query mirrors the board's ``est_missing``
aggregate, so ``ground_truth == 0 and est_missing == 0`` proves an empty scan.

Additive column on a derived cache — ``tag_health`` rows are wholesale replaced
by every rebuild (``tag_health_service.rebuild_tag_health`` DELETEs the table
before reinserting), so no backfill or NULL-reset is needed; existing rows carry
the ``0`` server default until the next rebuild. ``computed_at`` is reset to the
epoch so ``is_stale`` reports the cache as stale and the auto-rebuild finder
refreshes it with real counts.

Revision ID: 0075_add_tag_health_ground_truth
Revises: 0074_recompute_tag_health_exclude_human_decisions
Create Date: 2026-07-19 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0075_add_tag_health_ground_truth"
down_revision: Union[str, None] = "0074_recompute_tag_health_exclude_human_decisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tag_health" not in inspector.get_table_names():
        return

    # Conditional: 0001_baseline creates tables from the *current* model
    # metadata, so on a fresh database this column already exists.
    existing_cols = {col["name"] for col in inspector.get_columns("tag_health")}
    if "ground_truth" not in existing_cols:
        op.add_column(
            "tag_health",
            sa.Column("ground_truth", sa.Integer(), nullable=False, server_default="0"),
        )
        # Existing rows now hold a placeholder 0 rather than a real count; mark
        # the cache stale so the next rebuild recomputes it.
        op.execute(sa.text("UPDATE tag_health SET computed_at = '1970-01-01 00:00:00'"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tag_health" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("tag_health")}
    if "ground_truth" in existing_cols:
        op.drop_column("tag_health", "ground_truth")
