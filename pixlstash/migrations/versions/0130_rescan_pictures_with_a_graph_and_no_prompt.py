"""Hand back every picture whose graph was read and gave no prompt.

The recipe's prompt reader only knew a ``CLIPTextEncode`` behind a sampler's
``positive`` input, so a graph that conditions any other way (a ``BasicGuider``,
an edit or video encoder holding its own ``prompt``) was filed with models, a
seed and no prompt, and a look with no prompt and no LoRA is listed nowhere.
The reader follows those now (``utils/comfyui_utilities.py``), and nothing
re-reads a scanned picture in place: the finder selects on
``workflow_hash_version IS NULL``, or on ``comfyui_models IS NULL`` where there
is no hub. So both markers are cleared, the pattern 0129 used.

Only pictures that carry a graph and have no prompt. The revisit rewrites
``comfyui_models`` and ``comfyui_loras`` from the same read that wrote them, and
keeps a picture's workflow keys when its file cannot be read
(``tasks/comfyui_extraction_task.py``), so an unplugged drive loses nothing.

Revision ID: 0130_rescan_pictures_with_a_graph_and_no_prompt
Revises: 0129_rescan_videos_for_embedded_workflows
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0130_rescan_pictures_with_a_graph_and_no_prompt"
down_revision: Union[str, None] = "0129_rescan_videos_for_embedded_workflows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    columns = {col["name"] for col in inspector.get_columns("picture")}
    needed = {
        "workflow_instance_hash",
        "workflow_hash_version",
        "comfyui_models",
        "comfyui_positive_prompt",
    }
    if not needed <= columns:
        return
    op.execute(
        "UPDATE picture SET workflow_hash_version = NULL, comfyui_models = NULL "
        "WHERE workflow_instance_hash IS NOT NULL "
        "AND comfyui_positive_prompt IS NULL"
    )


def downgrade() -> None:
    # Nothing to restore: an older build's extraction re-reads a cleared
    # picture and stamps it again, with the prompt it could already find.
    return
