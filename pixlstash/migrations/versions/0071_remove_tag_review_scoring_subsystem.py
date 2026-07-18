"""Remove the accuracy-measurement subsystem (frozen eval slices, AP/F1
scoring, train/eval split assignment).

Product-scope decision: PixlStash no longer measures tagger accuracy itself
-- PixlTagger's own tooling stays responsible for that. What survives is the
Tag Health board's Priority ranking and the review-suggestion/correction flow
(the "find and fix probably-wrong tags" loop); nothing in this migration
touches those tables/columns.

Drops the ``picture_split`` table (Wave B, 0068_add_picture_split),
``tag_eval_slice`` / ``tag_eval_slice_item`` (Wave C, 0069_add_tag_eval_slice),
and every ``eval_*`` column on ``tag_health`` added by 0069 and
0070_add_tag_health_eval_candidate_n_pos. This is a real product decision,
not a temporary rollback-friendly change: it runs against a populated vault
(confirmed 36,318 picture_split rows, 13 tag_eval_slice rows, ~6,000
tag_eval_slice_item rows in production) and is expected to discard that data
-- the tables/columns are wholly derived (a leakage-guard split assignment
and a scoring cache), never a primary record of anything a user entered.

Revision ID: 0071_remove_tag_review_scoring_subsystem
Revises: 0070_add_tag_health_eval_candidate_n_pos
Create Date: 2026-07-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0071_remove_tag_review_scoring_subsystem"
down_revision: Union[str, None] = "0070_add_tag_health_eval_candidate_n_pos"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

# The eval_* TagHealth columns added across 0069 (everything but
# eval_candidate_n_pos) and 0070 (eval_candidate_n_pos), by SQL type.
_TAG_HEALTH_EVAL_FLOAT_COLS = [
    "eval_precision",
    "eval_recall",
    "eval_f1",
    "eval_ap",
    "eval_ap_ci_low",
    "eval_ap_ci_high",
]
_TAG_HEALTH_EVAL_INT_COLS = ["eval_n", "eval_n_pos", "eval_candidate_n_pos"]
_TAG_HEALTH_EVAL_STRING_COLS = ["eval_metric_kind", "eval_threshold_source"]
_TAG_HEALTH_EVAL_DATETIME_COLS = ["eval_slice_frozen_at"]
_ALL_TAG_HEALTH_EVAL_COLS = (
    _TAG_HEALTH_EVAL_FLOAT_COLS
    + _TAG_HEALTH_EVAL_INT_COLS
    + _TAG_HEALTH_EVAL_STRING_COLS
    + _TAG_HEALTH_EVAL_DATETIME_COLS
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "tag_health" in tables:
        existing_cols = {col["name"] for col in inspector.get_columns("tag_health")}
        for col_name in _ALL_TAG_HEALTH_EVAL_COLS:
            if col_name in existing_cols:
                op.drop_column("tag_health", col_name)

    if "tag_eval_slice_item" in tables:
        op.drop_table("tag_eval_slice_item")
    if "tag_eval_slice" in tables:
        op.drop_table("tag_eval_slice")
    if "picture_split" in tables:
        op.drop_table("picture_split")


def downgrade() -> None:
    """Recreate the dropped schema; no attempt to restore discarded data.

    Table shapes are reproduced verbatim from the migrations that created
    them (0068_add_picture_split, 0069_add_tag_eval_slice) so a downgrade
    lands on the exact same schema a fresh 0070 database would have had.
    The tag_health eval_* columns are added back nullable, matching how
    0069/0070 originally added them (additive, no server_default) -- this
    is a one-way product decision, not a rollback-friendly change, so no
    attempt is made to repopulate them; they read NULL until a rebuild
    would have populated them under the old code, which no longer exists.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "picture_split" not in tables:
        op.create_table(
            "picture_split",
            sa.Column("picture_id", sa.Integer(), nullable=False),
            sa.Column("split", sa.String(), nullable=False, server_default="NEITHER"),
            sa.Column("component_key", sa.Integer(), nullable=False),
            sa.Column("assigned_at", sa.DateTime(), nullable=True),
            sa.Column(
                "conflict", sa.Boolean(), nullable=False, server_default=sa.text("0")
            ),
            sa.Column("conflict_detail", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["picture_id"], ["picture.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("picture_id"),
        )
        op.create_index("ix_picture_split_split", "picture_split", ["split"])
        op.create_index(
            "ix_picture_split_component_key", "picture_split", ["component_key"]
        )
        op.create_index("ix_picture_split_conflict", "picture_split", ["conflict"])

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

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tag_health" in inspector.get_table_names():
        existing_cols = {col["name"] for col in inspector.get_columns("tag_health")}
        for col_name in _TAG_HEALTH_EVAL_FLOAT_COLS:
            if col_name not in existing_cols:
                op.add_column(
                    "tag_health", sa.Column(col_name, sa.Float(), nullable=True)
                )
        for col_name in _TAG_HEALTH_EVAL_INT_COLS:
            if col_name not in existing_cols:
                op.add_column(
                    "tag_health", sa.Column(col_name, sa.Integer(), nullable=True)
                )
        for col_name in _TAG_HEALTH_EVAL_STRING_COLS:
            if col_name not in existing_cols:
                op.add_column(
                    "tag_health", sa.Column(col_name, sa.String(), nullable=True)
                )
        for col_name in _TAG_HEALTH_EVAL_DATETIME_COLS:
            if col_name not in existing_cols:
                op.add_column(
                    "tag_health", sa.Column(col_name, sa.DateTime(), nullable=True)
                )
