"""Add set_icon and set_color columns to pictureset table.

Existing sets keep set_icon as NULL (meaning the default card-stack thumbnail).
set_color is assigned in a round-robin from a palette of 20 distinct colors,
ordered by the alphabetical (case-insensitive) sort of the set name so the
assignment is deterministic and stable.

Revision ID: 0043_pictureset_icon_color
Revises: 0042_guest_session_cookie_token
Create Date: 2026-05-13 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0043_pictureset_icon_color"
down_revision: Union[str, None] = "0042_guest_session_cookie_token"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

# 20 maximally-distinct colors — hue-interleaved so consecutive entries are ~180° apart
_PALETTE = [
    "#e53935",  # red          (~0°)
    "#00acc1",  # cyan         (~185°)
    "#f4511e",  # burnt orange (~12°)
    "#039be5",  # light blue   (~200°)
    "#ff7043",  # deep orange  (~15°)
    "#546e7a",  # blue-grey    (~205°)
    "#fb8c00",  # orange       (~30°)
    "#1e88e5",  # blue         (~215°)
    "#fdd835",  # yellow       (~60°)
    "#3949ab",  # indigo       (~230°)
    "#c0ca33",  # lime         (~75°)
    "#9c27b0",  # deep purple  (~270°)
    "#7cb342",  # light green  (~90°)
    "#8e24aa",  # purple       (~285°)
    "#43a047",  # green        (~125°)
    "#d81b60",  # magenta      (~330°)
    "#00897b",  # teal         (~165°)
    "#f06292",  # pink         (~345°)
    "#00bfa5",  # teal accent  (~170°)
    "#6d4c41",  # brown        (dark)
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "pictureset" not in inspector.get_table_names():
        # Fresh install — baseline migration creates the table with all
        # columns via SQLModel.metadata.create_all(); nothing to do here.
        return

    existing_cols = {col["name"] for col in inspector.get_columns("pictureset")}

    if "set_icon" not in existing_cols:
        op.add_column("pictureset", sa.Column("set_icon", sa.String(), nullable=True))

    if "set_color" not in existing_cols:
        op.add_column("pictureset", sa.Column("set_color", sa.String(), nullable=True))

        # Assign colors in round-robin, ordered alphabetically by name.
        result = bind.execute(sa.text("SELECT id FROM pictureset ORDER BY LOWER(name)"))
        rows = result.fetchall()
        for idx, row in enumerate(rows):
            color = _PALETTE[idx % len(_PALETTE)]
            bind.execute(
                sa.text("UPDATE pictureset SET set_color = :color WHERE id = :id"),
                {"color": color, "id": row[0]},
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "pictureset" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("pictureset")}

    if "set_color" in existing_cols:
        op.drop_column("pictureset", "set_color")

    if "set_icon" in existing_cols:
        op.drop_column("pictureset", "set_icon")
