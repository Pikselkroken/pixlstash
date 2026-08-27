"""Add the append-only operation log (DAM 1.2: undo/redo + the future audit log).

Creates the ``operation`` table. One row records one user-visible change: its
verb, the target ids, the *before* and *after* metadata state of those targets,
the actor, and the WebSocket-envelope origin (``source`` / ``origin_client_id``)
the change arrived with. Undo writes ``before_state`` back; redo writes
``after_state`` back.

``batch_id`` is present from this first migration on purpose. A bulk action (the
v1.9 near-duplicate sweep, any later batch job) must be one undoable unit, and
retrofitting a grouping key onto a log that already holds rows is exactly the
migration pain the additive-only rule exists to avoid.

Additive only: no existing table, column or constraint is touched, and no data
is reset. Table creation is conditional on the table being absent, because the
0001 baseline runs ``SQLModel.metadata.create_all()`` and therefore already
creates it on a fresh database.

Re-pointed onto ``0086_reissue_api_tokens`` when v1.8.1 was merged into the 1.9
line. Both migrations were written against ``0085`` on separate branches, which
left the chain with two heads. ``0086_reissue_api_tokens`` shipped in v1.8.1 and
released databases are stamped with exactly that id, so it must keep its
identifier and its parent; this migration is 1.9-only and unreleased, so moving
it is the safe side to change. A database already stamped here simply continues
forward.

Revision ID: 0086_add_operation_log
Revises: 0086_reissue_api_tokens
Create Date: 2026-07-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0086_add_operation_log"
down_revision: Union[str, None] = "0086_reissue_api_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


_INDEXES = (
    ("ix_operation_batch_id", ["batch_id"]),
    ("ix_operation_created_at", ["created_at"]),
    ("ix_operation_actor", ["actor"]),
    ("ix_operation_op_type", ["op_type"]),
    ("ix_operation_undoable", ["undoable"]),
    ("ix_operation_status", ["status"]),
    # The undo/redo stacks are "newest row in this status".
    ("ix_operation_status_id", ["status", "id"]),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "operation" not in inspector.get_table_names():
        op.create_table(
            "operation",
            sa.Column("id", sa.Integer(), nullable=False),
            # Groups the rows of one bulk action so it is undone as a unit.
            sa.Column("batch_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("actor", sa.String(), nullable=True),
            sa.Column("op_type", sa.String(), nullable=False),
            sa.Column(
                "target_type",
                sa.String(),
                nullable=False,
                server_default="picture",
            ),
            # JSON list[int] of affected object ids.
            sa.Column("target_ids", sa.Text(), nullable=False),
            sa.Column("target_count", sa.Integer(), nullable=False, server_default="0"),
            # JSON {"<target_id>": {facet: value}} - only the changed facets.
            sa.Column("before_state", sa.Text(), nullable=True),
            sa.Column("after_state", sa.Text(), nullable=True),
            # WS-envelope provenance, carried explicitly from the request.
            sa.Column("source", sa.String(), nullable=False, server_default="external"),
            sa.Column("origin_client_id", sa.String(), nullable=True),
            # False for changes recorded for audit only (file-mutating ops until
            # copy-on-write versions exist).
            sa.Column("undoable", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(), nullable=False, server_default="applied"),
            sa.Column("undone_at", sa.DateTime(), nullable=True),
            sa.Column("summary", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    # Re-inspect: the Inspector above cached the pre-create_table reflection.
    existing_indexes = {
        ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes("operation")
    }
    for index_name, columns in _INDEXES:
        if index_name not in existing_indexes:
            op.create_index(index_name, "operation", columns)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "operation" not in inspector.get_table_names():
        return

    existing_indexes = {ix["name"] for ix in inspector.get_indexes("operation")}
    for index_name, _columns in _INDEXES:
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name="operation")
    op.drop_table("operation")
