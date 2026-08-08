"""Attach model-shelf adapters to this library's characters and sets.

``adapter_attachment`` is the only model-shelf table that lives in a vault. The
adapters, the folders holding them and the checkpoints beside them are hub
tables, because those describe the machine. Which character a LoRA belongs to
describes *this library*, so it belongs here and travels with the library.

The link is ``adapter_sha256``, a TEXT column, and deliberately not an integer
adapter id. No foreign key can span the hub and a vault, so an integer would be
an unenforceable reference; worse, SQLite hands a deleted row's id to the next
insert, so an integer link would silently re-point at a different adapter after
a delete plus insert while still looking valid.

``character_color`` rides along because it is a one-column addition to an
existing table and splitting it into its own revision buys nothing. It mirrors
``PictureSet.set_color``: a hex seeded by position in the shared 48-colour list.
Only the column lands here; the assignment logic is #761.

Revision ID: 0103_add_adapter_attachment
Revises: 0102_add_snapshot_identity_scrubbed_at
Create Date: 2026-08-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0103_add_adapter_attachment"
down_revision: Union[str, None] = "0102_add_snapshot_identity_scrubbed_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Conditional because the baseline runs SQLModel.metadata.create_all(),
    # which already builds this table on a fresh database from the model added
    # alongside this revision. An unconditional CREATE would fail there.
    if "adapter_attachment" not in inspector.get_table_names():
        op.create_table(
            "adapter_attachment",
            sa.Column("adapter_sha256", sa.String(), nullable=False),
            sa.Column("entity_type", sa.String(), nullable=False),
            sa.Column("entity_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("adapter_sha256", "entity_type", "entity_id"),
        )
        op.create_index(
            "ix_adapter_attachment_adapter_sha256",
            "adapter_attachment",
            ["adapter_sha256"],
        )
        # The shelf's other direction: "what does this character use".
        op.create_index(
            "ix_adapter_attachment_entity",
            "adapter_attachment",
            ["entity_type", "entity_id"],
        )

    if "character" in inspector.get_table_names():
        existing_cols = {col["name"] for col in inspector.get_columns("character")}
        if "character_color" not in existing_cols:
            op.add_column(
                "character",
                sa.Column("character_color", sa.String(), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "character" in inspector.get_table_names():
        existing_cols = {col["name"] for col in inspector.get_columns("character")}
        if "character_color" in existing_cols:
            op.drop_column("character", "character_color")

    if "adapter_attachment" in inspector.get_table_names():
        op.drop_table("adapter_attachment")
