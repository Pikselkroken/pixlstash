"""Merge the multi-library and telemetry migration branches.

Both features started from ``0090_add_usertoken_public_id`` and were developed
in parallel. Their revision identifiers have already been used by developer
databases, so preserve both histories and join them with an empty Alembic merge
revision instead of renumbering either branch.

Revision ID: 0096_merge_multi_library_telemetry_heads
Revises: 0095_add_settings_fingerprint, 0094_add_telemetry_consent
Create Date: 2026-08-03
"""

from typing import Sequence, Union


revision: str = "0096_merge_multi_library_telemetry_heads"
down_revision: Union[str, tuple[str, str], None] = (
    "0095_add_settings_fingerprint",
    "0094_add_telemetry_consent",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
