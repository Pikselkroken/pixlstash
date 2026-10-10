"""Hand every video back to the ComfyUI extraction.

A video's container carries the graph that made it, as a PNG's text chunks do
(``VideoUtils.extract_embedded_metadata``), and the extraction used to mark
every video scanned without opening it. Nothing re-reads a scanned picture in
place: the finder selects on ``workflow_hash_version IS NULL``, or on
``comfyui_models IS NULL`` where there is no hub. So both markers are cleared on
the videos, the pattern 0118 used, and the ones that carry a workflow come back
with a prompt, models and a recipe.

Nothing is lost by it: a video's ``comfyui_models`` was only ever the ``"[]"``
sentinel, and no video carried workflow keys.

Revision ID: 0129_rescan_videos_for_embedded_workflows
Revises: 0128_add_picture_prompt_match
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0129_rescan_videos_for_embedded_workflows"
down_revision: Union[str, None] = "0128_add_picture_prompt_match"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    columns = {col["name"] for col in inspector.get_columns("picture")}
    if not {"is_video", "workflow_hash_version", "comfyui_models"} <= columns:
        return
    op.execute(
        "UPDATE picture SET workflow_hash_version = NULL, comfyui_models = NULL "
        "WHERE is_video = 1"
    )


def downgrade() -> None:
    # Nothing to restore: an older build's extraction marks a video scanned
    # again the first time it is offered one.
    return
