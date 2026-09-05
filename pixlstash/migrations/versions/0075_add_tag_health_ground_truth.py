"""Add the ground_truth count column to tag_health.

``ground_truth`` is the number of distinct non-deleted, in-scope pictures that
carry the (DEFAULT_TAG_MERGES-folded) tag. The board needs it to tell the user,
*before* they click "Start review", that a review would provably yield nothing:
at zero ground truth ``tag_scan_service.scan_tag`` takes its confidence-only
fallback branch, whose candidate query mirrors the board's ``est_missing``
aggregate, so ``ground_truth == 0 and est_missing == 0`` proves an empty scan.

Additive column on a derived cache - ``tag_health`` rows are wholesale replaced
by every rebuild (``tag_health_service.rebuild_tag_health`` DELETEs the table
before reinserting), so no backfill is needed. ``computed_at`` is reset to the
epoch so ``is_stale`` reports the cache as stale and the auto-rebuild finder
refreshes it with real counts.

The column is **nullable with no server default**, deliberately. A ``0`` here
would be a placeholder that is indistinguishable from a measured zero, and the
board's zero-yield gate (``tagHealthBoardLogic.zeroYieldReason``) treats
``ground_truth === 0 && est_missing === 0`` as proof that a review would yield
nothing and disables "Start review". Backfilling ``0`` would therefore disable
the button vault-wide for every pre-existing row until the next rebuild landed.
``NULL`` serializes as ``null``/``undefined``, which the gate already treats as
"no measurement yet" and leaves the button enabled - absence of evidence is not
evidence of emptiness.

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
            sa.Column("ground_truth", sa.Integer(), nullable=True),
        )
        # Existing rows now hold NULL ("not measured yet"), distinguishable from
        # a real 0; mark the cache stale so the next rebuild fills in real counts.
        op.execute(sa.text("UPDATE tag_health SET computed_at = '1970-01-01 00:00:00'"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tag_health" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("tag_health")}
    if "ground_truth" in existing_cols:
        op.drop_column("tag_health", "ground_truth")
