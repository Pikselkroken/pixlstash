"""baseline schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-03-06 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
from sqlmodel import SQLModel

# Import all models to register metadata.
import pixlstash.db_models  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = "0001_baseline"  # noqa: F841
down_revision: Union[str, None] = None  # noqa: F841
branch_labels: Union[str, Sequence[str], None] = None  # noqa: F841
depends_on: Union[str, Sequence[str], None] = None  # noqa: F841


def upgrade() -> None:
    SQLModel.metadata.create_all(op.get_bind())


def downgrade() -> None:
    SQLModel.metadata.drop_all(op.get_bind())
