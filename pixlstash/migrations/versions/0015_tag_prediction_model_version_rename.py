"""Rename tag_prediction model_version from epoch-N to vN format

Revision ID: 0015_tag_prediction_model_version_rename
Revises: 0014_tag_prediction_composite_index
Create Date: 2026-03-29 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015_tag_prediction_model_version_rename"
down_revision: Union[str, None] = "0014_tag_prediction_composite_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    # Rename any model_version values matching the old "epoch-N" format to "vN".
    op.execute(
        sa.text(
            "UPDATE tag_prediction"
            " SET model_version = 'v' || SUBSTR(model_version, 7)"
            " WHERE model_version LIKE 'epoch-%'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE tag_prediction"
            " SET model_version = 'epoch-' || SUBSTR(model_version, 2)"
            " WHERE model_version LIKE 'v%'"
        )
    )
