"""Queue the covered-ghost cascade from the database itself.

A picture ghost kept under ``covered`` is safe only while a surviving picture
carries the same ``workflow_instance_hash`` (workflow implementation plan §B4).
Six code paths hard-delete picture rows, with ORM deletes and core deletes alike,
and asking each to remember the cascade is how four of them forgot. So the
vault records the fact instead: a picture row that stops carrying an instance
hash, because it was deleted or because it was re-hashed under a new rule,
leaves that hash in ``pending_ghost_cascade``, and
``GhostCascadeFinder`` re-evaluates the hub's ghosts for it.

``seq`` is AUTOINCREMENT on purpose. The trigger re-queues an already-pending
hash with ``INSERT OR REPLACE``, which gives it a new ``seq``, and the drain
removes only the rows up to the ``seq`` it read. A hash that loses its last
cover while a drain is running is therefore re-queued rather than swallowed.
A plain rowid happens to grow the same way today; AUTOINCREMENT is what makes
"never reused" SQLite's documented guarantee rather than an observation.

Revision ID: 0117_add_pending_ghost_cascade
Revises: 0116_add_library_settings_caption_sync
Create Date: 2026-09-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0117_add_pending_ghost_cascade"
down_revision: Union[str, None] = "0116_add_library_settings_caption_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_TABLE = """
    CREATE TABLE IF NOT EXISTS pending_ghost_cascade (
        seq            INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_hash  TEXT NOT NULL UNIQUE
    )
"""

_TRIGGERS = {
    "trg_pending_ghost_cascade_picture_delete": """
        CREATE TRIGGER IF NOT EXISTS trg_pending_ghost_cascade_picture_delete
        AFTER DELETE ON picture
        WHEN OLD.workflow_instance_hash IS NOT NULL
        BEGIN
            INSERT OR REPLACE INTO pending_ghost_cascade (instance_hash)
            VALUES (OLD.workflow_instance_hash);
        END
    """,
    "trg_pending_ghost_cascade_picture_rehash": """
        CREATE TRIGGER IF NOT EXISTS trg_pending_ghost_cascade_picture_rehash
        AFTER UPDATE OF workflow_instance_hash ON picture
        WHEN OLD.workflow_instance_hash IS NOT NULL
            AND OLD.workflow_instance_hash IS NOT NEW.workflow_instance_hash
        BEGIN
            INSERT OR REPLACE INTO pending_ghost_cascade (instance_hash)
            VALUES (OLD.workflow_instance_hash);
        END
    """,
}


def upgrade() -> None:
    op.execute(_TABLE)
    if "picture" not in set(sa.inspect(op.get_bind()).get_table_names()):
        # Partial databases used by migration tests carry no picture table, and
        # SQLite cannot create a trigger on a table that is absent.
        return
    for statement in _TRIGGERS.values():
        op.execute(statement)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.execute("DROP TABLE IF EXISTS pending_ghost_cascade")
