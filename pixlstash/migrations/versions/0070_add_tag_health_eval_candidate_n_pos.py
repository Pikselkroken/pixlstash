"""Add eval_candidate_n_pos to tag_health.

Pre-freeze eligibility signal (see the docstring on
pixlstash.db_models.tag_health.TagHealth): "if I froze this tag right now,
how many verified positives would be in the slice" -- computed for every
tag on every rebuild, not just already-frozen ones, via the shared
count_eval_slice_candidates_in_session helper in
pixlstash.services.tag_eval_slice_service (also used by the freeze action
itself, so the two counts can never silently diverge). Additive, nullable
column -- the cache is wholesale-replaced on the next rebuild
(rebuild_tag_health deletes and reinserts every row), so existing rows
simply carry NULL until the next POST /tag_health/rebuild; no targeted
NULL-reset is needed here.

Revision ID: 0070_add_tag_health_eval_candidate_n_pos
Revises: 0069_add_tag_eval_slice
Create Date: 2026-07-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0070_add_tag_health_eval_candidate_n_pos"
down_revision: Union[str, None] = "0069_add_tag_eval_slice"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("tag_health")}

    if "eval_candidate_n_pos" not in existing_cols:
        op.add_column(
            "tag_health", sa.Column("eval_candidate_n_pos", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("tag_health")}

    if "eval_candidate_n_pos" in existing_cols:
        op.drop_column("tag_health", "eval_candidate_n_pos")
