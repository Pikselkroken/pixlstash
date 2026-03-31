"""Fix anomaly_tag_uncertainty to use model-vs-human disagreement formula

The previous formula used max(min(c, 1-c)) which is symmetric and peaks at 0.5
(maximum fence-sitting), regardless of whether the tag was confirmed or rejected.
This meant it could not tell the difference between a model that was unsure *and
agreed with the human* vs a model that was unsure *and disagreed with the human*.

The new formula measures how much the model disagrees with the human decision:
  - Rejected / PENDING tag:  score = confidence        (model says defect is there)
  - Confirmed tag:            score = 1 - confidence    (model was not confident in it)
  - Confirmed tag absent from model output: score = 1.0

Revision ID: 0018_anomaly_tag_uncertainty_disagreement
Revises: 0017_anomaly_tag_uncertainty
Create Date: 2026-03-31 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from pixlstash.db_models.tag import DEFAULT_SMART_SCORE_PENALIZED_TAGS

# revision identifiers, used by Alembic.
revision: str = "0018_anomaly_tag_uncertainty_disagreement"
down_revision: Union[str, None] = "0017_anomaly_tag_uncertainty"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "picture" not in existing_tables or "tag_prediction" not in existing_tables:
        return

    anomaly_tags = [t.strip().lower() for t in DEFAULT_SMART_SCORE_PENALIZED_TAGS]
    if not anomaly_tags:
        return

    placeholders = ", ".join(f"'{t}'" for t in anomaly_tags)

    # Recompute anomaly_tag_uncertainty using the disagreement formula:
    #   CONFIRMED tag  → 1 - confidence  (model unsure about a confirmed defect)
    #   REJECTED/PENDING tag → confidence (model confident a defect exists, was rejected)
    op.execute(
        sa.text(
            f"""
            UPDATE picture SET anomaly_tag_uncertainty = (
                SELECT MAX(
                    CASE WHEN tp.status = 'CONFIRMED' THEN 1.0 - tp.confidence
                         ELSE tp.confidence
                    END
                )
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

    if "picture" not in existing_tables or "tag_prediction" not in existing_tables:
        return

    anomaly_tags = [t.strip().lower() for t in DEFAULT_SMART_SCORE_PENALIZED_TAGS]
    if not anomaly_tags:
        return

    placeholders = ", ".join(f"'{t}'" for t in anomaly_tags)

    # Restore original symmetric formula: max(min(c, 1-c))
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
