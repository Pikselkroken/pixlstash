"""Invalidate cached stack cohesion when any source picture changes.

The cohesion cache used to validate every hit by rereading every member's
perceptual hash. That made a hit save only the comparison and made each list
request rescan the library. These database triggers make cache-row presence an
exact validity signal: every column that changes live membership or an edge
deletes the affected stack's derived row in the same transaction.

Revision ID: 0093_invalidate_stackcohesion_inputs
Revises: 0092_add_stackcohesion_nearest_edges
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0093_invalidate_stackcohesion_inputs"
down_revision: Union[str, None] = "0092_add_stackcohesion_nearest_edges"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_TRIGGERS = {
    "trg_stackcohesion_picture_insert": """
        CREATE TRIGGER trg_stackcohesion_picture_insert
        AFTER INSERT ON picture
        WHEN NEW.stack_id IS NOT NULL
        BEGIN
            DELETE FROM stackcohesion WHERE stack_id = NEW.stack_id;
        END
    """,
    "trg_stackcohesion_picture_update": """
        CREATE TRIGGER trg_stackcohesion_picture_update
        AFTER UPDATE OF stack_id, deleted, perceptual_hash ON picture
        BEGIN
            DELETE FROM stackcohesion
            WHERE stack_id = OLD.stack_id OR stack_id = NEW.stack_id;
        END
    """,
    "trg_stackcohesion_picture_delete": """
        CREATE TRIGGER trg_stackcohesion_picture_delete
        AFTER DELETE ON picture
        WHEN OLD.stack_id IS NOT NULL
        BEGIN
            DELETE FROM stackcohesion WHERE stack_id = OLD.stack_id;
        END
    """,
}


def upgrade() -> None:
    # Rows written before invalidation existed cannot be trusted without the
    # old full rescan, so start cold once and let the finder refill them.
    op.execute("DELETE FROM stackcohesion")
    for statement in _TRIGGERS.values():
        op.execute(statement)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
