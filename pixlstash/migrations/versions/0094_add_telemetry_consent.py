"""Add the opt-in telemetry consent flags to user.

Five additive boolean columns: one per telemetry category, plus a marker
recording that the consent question has been asked. All are nullable, so every
existing row reads NULL and falls back to the model default of False, so an
upgrade stays fully off and ``telemetry_consent_prompted`` reads
false, so the question is put to existing users exactly once.

Schema-only and additive. No reprocessing reset is needed: no derived data
depends on these columns.

Revision ID: 0094_add_telemetry_consent
Revises: 0093_invalidate_stackcohesion_inputs
Create Date: 2026-08-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0094_add_telemetry_consent"
down_revision: Union[str, None] = "0093_invalidate_stackcohesion_inputs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COLUMNS = (
    "telemetry_send_install_id",
    "telemetry_send_feature_usage",
    "telemetry_send_error_reports",
    "telemetry_send_hardware_profile",
    "telemetry_consent_prompted",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # Guard a missing user table (a partial/synthetic DB, e.g. the migration
    # tests that hand-build a minimal schema) before inspecting its columns.
    if "user" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("user")}
    for column in _COLUMNS:
        if column not in existing_cols:
            op.add_column("user", sa.Column(column, sa.Boolean(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user" not in inspector.get_table_names():
        return

    for column in _COLUMNS:
        op.drop_column("user", column)
