"""Add precision-discounted columns to tag_health.

Wave A of the tag-review-takeover design (docs/reviews/tag-review-tagger-takeover-design.md
§3): est_wrong_adj / est_missing_adj discount est_wrong/est_missing by the tag's
measured precision from the latest TaggerRun report, so an unreliable tag doesn't
dominate the board's "estimated fixes" ranking. Additive, nullable columns - the cache
is wholesale-replaced on the next rebuild, so no NULL-reset/backfill is needed here;
existing rows simply carry NULL for the new columns until the next
``POST /tag_health/rebuild``.

Revision ID: 0067_add_tag_health_precision_adj
Revises: 0066_add_tag_health
Create Date: 2026-07-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0067_add_tag_health_precision_adj"
down_revision: Union[str, None] = "0066_add_tag_health"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("tag_health")}

    if "est_wrong_adj" not in existing_cols:
        op.add_column(
            "tag_health", sa.Column("est_wrong_adj", sa.Float(), nullable=True)
        )
    if "est_missing_adj" not in existing_cols:
        op.add_column(
            "tag_health", sa.Column("est_missing_adj", sa.Float(), nullable=True)
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("tag_health")}

    if "est_missing_adj" in existing_cols:
        op.drop_column("tag_health", "est_missing_adj")
    if "est_wrong_adj" in existing_cols:
        op.drop_column("tag_health", "est_wrong_adj")
