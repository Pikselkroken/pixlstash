"""Re-read every picture that carries a workflow, for #1375.

A model named inside a ControlNet compound value used to stay inside that value
(``services/a1111_recipe.py``), so it was written into the instance document,
which forgetting a model never rewrites. Lifting it out changes what the
reduction produces, and **nothing re-keys a filed picture in place**: the
extraction finder selects on ``workflow_hash_version IS NULL``. So the scanned
marker is cleared on every picture that carries a workflow, the pattern 0118
used for the same reason, and the ones with A1111 ControlNet data come back with
the model as an asset instead of as text.

Broad on purpose: no column here says a picture's recipe came from A1111, and
missing one would leave the name this migration exists to remove. The revisit
keeps the keys it finds when a file cannot be read
(``tasks/comfyui_extraction_task.py``), so an unplugged drive loses nothing, and
a picture whose recipe does not change is re-filed onto the same rows.

Revision ID: 0122_rescan_pictures_for_controlnet_model_names
Revises: 0121_add_saved_recipe
Create Date: 2026-09-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0122_rescan_pictures_for_controlnet_model_names"
down_revision: Union[str, None] = "0121_add_saved_recipe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    if "picture" not in sa.inspect(op.get_bind()).get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    op.execute(
        "UPDATE picture SET workflow_hash_version = NULL "
        "WHERE workflow_instance_hash IS NOT NULL"
    )


def downgrade() -> None:
    # Re-stamp what upgrade cleared, as 0118 does: an older build's re-read
    # nulls the keys of a file it cannot read, and a picture left unstamped
    # would be re-read forever.
    if "picture" in sa.inspect(op.get_bind()).get_table_names():
        op.execute(
            "UPDATE picture SET workflow_hash_version = 'v1' "
            "WHERE workflow_hash_version IS NULL "
            "AND workflow_instance_hash IS NOT NULL"
        )
