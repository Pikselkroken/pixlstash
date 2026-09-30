"""Re-file saved recipes that name a hand-made workflow.

Workflows are automatic now: hub data step 6 dissolves every group that merge,
split or the cut-over's stacks made, and points each card's successor row back
at its automatic workflow. A recipe still naming one of those groups would list
nowhere, so its ``workflow_id`` is reset to NULL and
``MissingSavedRecipeWorkflowFinder`` re-files it from its card.

A hand-made id is a 32-hex uuid; an automatic one starts ``auto:``.

Revision ID: 0125_refile_recipes_off_hand_made_workflows
Revises: 0124_saved_recipe_models_and_workflow_id
Create Date: 2026-09-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0125_refile_recipes_off_hand_made_workflows"
down_revision: Union[str, None] = "0124_saved_recipe_models_and_workflow_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("saved_recipe"):
        # Partial databases used by migration tests carry no saved_recipe.
        return
    op.execute(
        "UPDATE saved_recipe SET workflow_id = NULL "
        "WHERE workflow_id IS NOT NULL AND workflow_id NOT LIKE 'auto:%'"
    )


def downgrade() -> None:
    # The groups are gone from the hub; there is nothing to point back at.
    pass
