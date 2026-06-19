"""Add the human-label ledger to tag_prediction.

Records an explicit human POS/NEG decision per (picture, tag) — so a reviewed negative
(a human removing/declining a tag) is durable supervision instead of being lost to an
absent Tag row. ``status`` stays as review-UI state; ``label_state``/``label_source`` are
the supervision signal training reads.

Backfill is deliberately conservative: ``status`` is auto-flipped by the background
TagTask from the applied tags, so it is *not* a reliable record of who decided. The only
unambiguous historical human signal in tag_prediction is the synthetic ``manual`` rows
that ``reject_tag_prediction`` writes — those become human NEGs. Everything else stays
UNKNOWN and is captured going forward by record_human_label.

Revision ID: 0060_tag_prediction_label_ledger
Revises: 0059_add_tagger_run
Create Date: 2026-06-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0060_tag_prediction_label_ledger"
down_revision: Union[str, None] = "0059_add_tagger_run"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COLUMNS = {
    "label_state": sa.Column(
        "label_state", sa.String(), nullable=False, server_default="UNKNOWN"
    ),
    "label_source": sa.Column("label_source", sa.String(), nullable=True),
    "labeled_at": sa.Column("labeled_at", sa.DateTime(), nullable=True),
    "label_model_version": sa.Column(
        "label_model_version", sa.String(), nullable=True
    ),
    "label_confidence": sa.Column("label_confidence", sa.Float(), nullable=True),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tag_prediction" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("tag_prediction")}

    for name, column in _COLUMNS.items():
        if name not in existing:
            op.add_column("tag_prediction", column)

    existing_indexes = {ix["name"] for ix in inspector.get_indexes("tag_prediction")}
    if "ix_tag_prediction_label_state" not in existing_indexes:
        op.create_index(
            "ix_tag_prediction_label_state", "tag_prediction", ["label_state"]
        )
    if "ix_tag_prediction_label_source" not in existing_indexes:
        op.create_index(
            "ix_tag_prediction_label_source", "tag_prediction", ["label_source"]
        )

    # Conservative backfill: the synthetic 'manual' REJECTED rows are genuine human
    # rejections (reject_tag_prediction creates them). Make them ledger NEGs.
    op.execute(
        sa.text(
            "UPDATE tag_prediction "
            "SET label_state = 'NEG', label_source = 'human', "
            "    labeled_at = predicted_at "
            "WHERE model_version = 'manual' AND status = 'REJECTED' "
            "  AND (label_source IS NULL OR label_source != 'human')"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tag_prediction" not in inspector.get_table_names():
        return
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("tag_prediction")}
    if "ix_tag_prediction_label_source" in existing_indexes:
        op.drop_index("ix_tag_prediction_label_source", table_name="tag_prediction")
    if "ix_tag_prediction_label_state" in existing_indexes:
        op.drop_index("ix_tag_prediction_label_state", table_name="tag_prediction")
    existing = {c["name"] for c in inspector.get_columns("tag_prediction")}
    for name in reversed(list(_COLUMNS)):
        if name in existing:
            op.drop_column("tag_prediction", name)
