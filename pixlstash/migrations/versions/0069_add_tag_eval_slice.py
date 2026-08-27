"""Add tag_eval_slice/tag_eval_slice_item tables + TagHealth eval columns.

Wave C of the tag-review takeover design
(docs/reviews/tag-review-tagger-takeover-design.md §1): a frozen,
leak-free per-tag ground-truth membership (TagEvalSlice/TagEvalSliceItem,
one ACTIVE slice per tag enforced by a partial unique index, mirroring
Review's OPEN-per-tag pattern) plus the TagHealth board columns that surface
its computed AP/F1 metrics. Additive, no data migration: TagHealth is a
wholesale-replaced cache (rebuild_tag_health deletes and reinserts every row),
so existing rows simply carry NULL for the new eval_* columns until the next
POST /tag_health/rebuild - no targeted NULL-reset is needed here.

Revision ID: 0069_add_tag_eval_slice
Revises: 0068_add_picture_split
Create Date: 2026-07-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0069_add_tag_eval_slice"
down_revision: Union[str, None] = "0068_add_picture_split"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "tag_eval_slice" not in tables:
        op.create_table(
            "tag_eval_slice",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tag", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="ACTIVE"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_tag_eval_slice_tag", "tag_eval_slice", ["tag"])
        op.create_index("ix_tag_eval_slice_status", "tag_eval_slice", ["status"])
        # One ACTIVE slice per tag (mirrors uq_review_open_tag).
        op.create_index(
            "uq_tag_eval_slice_active_tag",
            "tag_eval_slice",
            ["tag"],
            unique=True,
            sqlite_where=sa.text("status = 'ACTIVE'"),
        )
    else:
        existing_indexes = {
            ix["name"] for ix in inspector.get_indexes("tag_eval_slice")
        }
        if "uq_tag_eval_slice_active_tag" not in existing_indexes:
            op.create_index(
                "uq_tag_eval_slice_active_tag",
                "tag_eval_slice",
                ["tag"],
                unique=True,
                sqlite_where=sa.text("status = 'ACTIVE'"),
            )

    if "tag_eval_slice_item" not in tables:
        op.create_table(
            "tag_eval_slice_item",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("eval_slice_id", sa.Integer(), nullable=False),
            sa.Column("picture_id", sa.Integer(), nullable=False),
            sa.Column("label_state", sa.String(), nullable=False),
            sa.Column("frozen_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["eval_slice_id"], ["tag_eval_slice.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["picture_id"], ["picture.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "eval_slice_id", "picture_id", name="uq_tag_eval_slice_item"
            ),
        )
        op.create_index(
            "ix_tag_eval_slice_item_eval_slice_id",
            "tag_eval_slice_item",
            ["eval_slice_id"],
        )
        op.create_index(
            "ix_tag_eval_slice_item_picture_id", "tag_eval_slice_item", ["picture_id"]
        )
        op.create_index(
            "ix_tag_eval_slice_item_label_state", "tag_eval_slice_item", ["label_state"]
        )

    if "tag_health" in tables:
        existing_cols = {col["name"] for col in inspector.get_columns("tag_health")}
        float_cols = [
            "eval_precision",
            "eval_recall",
            "eval_f1",
            "eval_ap",
            "eval_ap_ci_low",
            "eval_ap_ci_high",
        ]
        for col_name in float_cols:
            if col_name not in existing_cols:
                op.add_column(
                    "tag_health", sa.Column(col_name, sa.Float(), nullable=True)
                )
        int_cols = ["eval_n", "eval_n_pos"]
        for col_name in int_cols:
            if col_name not in existing_cols:
                op.add_column(
                    "tag_health", sa.Column(col_name, sa.Integer(), nullable=True)
                )
        if "eval_slice_frozen_at" not in existing_cols:
            op.add_column(
                "tag_health",
                sa.Column("eval_slice_frozen_at", sa.DateTime(), nullable=True),
            )
        if "eval_metric_kind" not in existing_cols:
            op.add_column(
                "tag_health", sa.Column("eval_metric_kind", sa.String(), nullable=True)
            )
        if "eval_threshold_source" not in existing_cols:
            op.add_column(
                "tag_health",
                sa.Column("eval_threshold_source", sa.String(), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "tag_health" in tables:
        existing_cols = {col["name"] for col in inspector.get_columns("tag_health")}
        for col_name in (
            "eval_precision",
            "eval_recall",
            "eval_f1",
            "eval_ap",
            "eval_ap_ci_low",
            "eval_ap_ci_high",
            "eval_n",
            "eval_n_pos",
            "eval_slice_frozen_at",
            "eval_metric_kind",
            "eval_threshold_source",
        ):
            if col_name in existing_cols:
                op.drop_column("tag_health", col_name)

    if "tag_eval_slice_item" in tables:
        op.drop_table("tag_eval_slice_item")
    if "tag_eval_slice" in tables:
        op.drop_table("tag_eval_slice")
