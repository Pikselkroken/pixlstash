"""Add anomaly_tag_uncertainty column to picture table

Revision ID: 0017_anomaly_tag_uncertainty
Revises: 0016_tag_prediction_confirm_applied
Create Date: 2026-03-29 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from pixlstash.db_models.tag import DEFAULT_SMART_SCORE_PENALIZED_TAGS

# revision identifiers, used by Alembic.
revision: str = "0017_anomaly_tag_uncertainty"
down_revision: Union[str, None] = "0016_tag_prediction_confirm_applied"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "picture" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("picture")}
        if "anomaly_tag_uncertainty" not in existing_cols:
            op.add_column(
                "picture",
                sa.Column("anomaly_tag_uncertainty", sa.Float(), nullable=True),
            )
            op.create_index(
                "ix_picture_anomaly_tag_uncertainty",
                "picture",
                ["anomaly_tag_uncertainty"],
            )

    # Backfill anomaly_tag_uncertainty from existing tag_prediction rows.
    # uncertainty = max(min(c, 1-c)) across anomaly tags for each picture.
    if "picture" in existing_tables and "tag_prediction" in existing_tables:
        anomaly_tags = [t.strip().lower() for t in DEFAULT_SMART_SCORE_PENALIZED_TAGS]
        if anomaly_tags:
            placeholders = ", ".join(f"'{t}'" for t in anomaly_tags)
            op.execute(
                sa.text(
                    f"""
                UPDATE picture SET anomaly_tag_uncertainty = (
                    SELECT MAX(MIN(tp.confidence, 1.0 - tp.confidence))
                    FROM tag_prediction tp
                    WHERE tp.picture_id = picture.id
                      AND LOWER(TRIM(tp.tag)) IN ({placeholders})
                )
                WHERE EXISTS (
                    SELECT 1 FROM tag_prediction tp
                    WHERE tp.picture_id = picture.id
                      AND LOWER(TRIM(tp.tag)) IN ({placeholders})
                )
                """
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "picture" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("picture")}
        if "anomaly_tag_uncertainty" in existing_cols:
            op.drop_index("ix_picture_anomaly_tag_uncertainty", table_name="picture")
            op.drop_column("picture", "anomaly_tag_uncertainty")
