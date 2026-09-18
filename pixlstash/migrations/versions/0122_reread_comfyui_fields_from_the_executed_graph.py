"""Re-read the ComfyUI fields of every picture that carries a workflow.

``extract_comfy_workflow_info`` took its facts from whichever graph
``find_comfy_workflow`` returned, and that prefers the UI ``workflow`` chunk -
the editor's view, recovered by mapping named inputs onto positional
``widgets_values`` and, failing that, by taking the longest string in a node. A
graph whose text encoder is fed from a prompt-builder node therefore reported
that node's template instead of the prompt, and could report no models and no
seed at all. The ``prompt`` chunk, the graph the ComfyUI server actually
executed, was sitting in the same file.

The read now prefers it, so ``comfyui_positive_prompt``, ``comfyui_models`` and
``comfyui_loras`` were written wrong for any picture whose two chunks disagree.
Those columns feed search, the text embedding and the workflow cards' credit
matching, so they are regenerated rather than left.

**Both stamps are cleared, and that is the point.**
``ComfyUIExtractionTask`` sets ``write_comfyui = pic.comfyui_models is None``,
so clearing ``workflow_hash_version`` alone re-runs the workflow scan and leaves
the ComfyUI columns exactly as they are - the very columns this fixes. Clearing
``comfyui_models`` is what re-queues the extraction with the hub absent, and
``workflow_hash_version`` is what re-queues it with the hub attached
(``MissingComfyUIExtractionFinder``), so a library is covered either way.

Narrowed to pictures the scan filed a graph for. A picture with no workflow has
nothing to re-derive, and this is one metadata read each for the ones that do.

Revision ID: 0122_reread_comfyui_fields
Revises: 0121_add_saved_recipe
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0122_reread_comfyui_fields"
down_revision: Union[str, None] = "0121_add_saved_recipe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    columns = {col["name"] for col in inspector.get_columns("picture")}
    if not {"comfyui_models", "workflow_hash_version", "workflow_topology_hash"} <= (
        columns
    ):
        return
    op.execute(
        "UPDATE picture SET comfyui_models = NULL, workflow_hash_version = NULL "
        "WHERE workflow_topology_hash IS NOT NULL"
    )


def downgrade() -> None:
    # Nothing to restore: an older build re-reads the cleared pictures and
    # stamps them again, with the answer its own extractor gives. Re-stamping
    # here would also stamp pictures that were never scanned.
    pass
