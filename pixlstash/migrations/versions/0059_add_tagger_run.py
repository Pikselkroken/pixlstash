"""Add tagger_run table — evaluation reports pushed from PixlTagger.

Stores every run PixlTagger pushes (including rejected ones) with its full report
payload, so PixlStash owns the tagger's stats/trend history. The first brick of making
PixlStash the system of record for the tagger.

Revision ID: 0059_add_tagger_run
Revises: 0058_add_tag_suggestion
Create Date: 2026-06-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0059_add_tagger_run"
down_revision: Union[str, None] = "0058_add_tag_suggestion"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tagger_run" in inspector.get_table_names():
        return

    op.create_table(
        "tagger_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run", sa.String(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=True),
        sa.Column("verdict", sa.String(), nullable=True),
        sa.Column("recommend", sa.String(), nullable=True),
        sa.Column("accepted", sa.String(), nullable=True),
        sa.Column("anomaly_macro_f1", sa.Float(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run"),
    )
    op.create_index("ix_tagger_run_run", "tagger_run", ["run"])
    op.create_index("ix_tagger_run_model_version", "tagger_run", ["model_version"])
    op.create_index("ix_tagger_run_created_at", "tagger_run", ["created_at"])


def downgrade() -> None:
    op.drop_table("tagger_run")
