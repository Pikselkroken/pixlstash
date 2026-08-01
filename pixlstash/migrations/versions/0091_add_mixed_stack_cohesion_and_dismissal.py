"""Add the mixed-stack cohesion cache and the ``Keep`` dismissal.

Two new tables behind the Mixed stacks page
(``docs/design/mixed-stacks-and-stack-units.md``, D5 and B5):

``stackcohesion``: the **cohesion cache**. A mixed stack is a live stack whose
members do not form one connected cluster at the queue's similarity threshold,
measured with the same 64-bit dHash Hamming distance tier 2 already uses. The
expensive half of that measurement (reading every member's ``perceptual_hash``
and comparing the stack's upper triangle) is *threshold-independent*, so this
table caches the resulting near-pair edge list and the page folds components out
of it at whatever threshold it was asked for. Keyed on ``content_fingerprint``,
a digest of the stack's ``(member id, perceptual hash)`` pairs, i.e. *every*
input the edges are derived from, so any change to either invalidates the row
by construction rather than by remembering to invalidate it, and so a recycled
SQLite stack id can never read a dead stack's edges. The hashes are deliberately
part of the key: one can move without membership moving (the embedding worker
filling a ``NULL``, a reference-folder file replaced under an unchanged picture
row), and a membership-only key would then freeze a member as "stranded"
forever. Pure derived data: dropping the table costs one recomputation and no
information.

``mixedstackdismissal``: the **Keep** dismissal. "This stack is fine, stop
listing it", durable and server-side. Keyed on stack id **plus** a
``membership_fingerprint``: the member ids only, *not* the cohesion cache's
content key, because D5 is explicit that adding a member is what re-raises a
kept stack and a re-hash must not silently retract a Keep. Adding a member later
produces a fingerprint no row matches and the stack is raised again; the user
approved *these* pictures together, not the stack forever. Unique on the pair,
so pressing Keep twice is
idempotent, and rows are kept per fingerprint so undoing a membership change
restores a dismissal the user already made instead of asking twice.

Both tables carry an ``ON DELETE CASCADE`` foreign key to ``picturestack``: a
dissolved stack has neither cohesion to cache nor a listing to suppress, and a
row outliving its stack would be a slow leak in both.

Nothing is cleared and no reprocessing is triggered; there is no existing
column whose meaning changes. The cache starts empty and
``MissingStackCohesionFinder`` fills it on the next work sweep; until then the
page computes what it needs inline, so an unmigrated-cache database is slower
for one request, never wrong.

Revision ID: 0091_add_mixed_stack_cohesion_and_dismissal
Revises: 0090_add_usertoken_public_id
Create Date: 2026-08-01 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0091_add_mixed_stack_cohesion_and_dismissal"
down_revision: Union[str, None] = "0090_add_usertoken_public_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COHESION_TABLE = "stackcohesion"
_DISMISSAL_TABLE = "mixedstackdismissal"
_COHESION_FINGERPRINT_INDEX = "ix_stackcohesion_content_fingerprint"
_DISMISSAL_STACK_INDEX = "ix_mixedstackdismissal_stack_id"
_DISMISSAL_FINGERPRINT_INDEX = "ix_mixedstackdismissal_fingerprint"


def _index_names(inspector, existing_tables: set, table: str) -> set:
    """Index names already on *table*, or an empty set if it was just created.

    The inspector is built before the ``create_table`` calls, so asking it about
    a table this migration is creating would raise. A table this migration
    created carries no indexes yet by construction, which is exactly the empty
    set, and a table that predates it (a fresh database, where the baseline's
    ``create_all`` made both tables and their indexes from the models) reports
    the ones it already has, so neither branch double-creates.
    """
    if table not in existing_tables:
        return set()
    return {ix["name"] for ix in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # A fresh database already has both tables from the baseline's
    # ``SQLModel.metadata.create_all()``; only an older one needs the CREATE.
    if _COHESION_TABLE not in existing_tables:
        op.create_table(
            _COHESION_TABLE,
            sa.Column(
                "stack_id",
                sa.Integer(),
                sa.ForeignKey("picturestack.id", ondelete="CASCADE"),
                primary_key=True,
                nullable=False,
            ),
            sa.Column("content_fingerprint", sa.String(), nullable=False),
            sa.Column("member_count", sa.Integer(), nullable=False),
            sa.Column("member_ids", sa.String(), nullable=False),
            sa.Column("unhashed_picture_ids", sa.String(), nullable=False),
            sa.Column("edges", sa.String(), nullable=False),
            sa.Column("computed_at", sa.DateTime(), nullable=False),
        )
    if _COHESION_FINGERPRINT_INDEX not in _index_names(
        inspector, existing_tables, _COHESION_TABLE
    ):
        op.create_index(
            _COHESION_FINGERPRINT_INDEX,
            _COHESION_TABLE,
            ["content_fingerprint"],
        )

    if _DISMISSAL_TABLE not in existing_tables:
        op.create_table(
            _DISMISSAL_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column(
                "stack_id",
                sa.Integer(),
                sa.ForeignKey("picturestack.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("membership_fingerprint", sa.String(), nullable=False),
            sa.Column("member_count", sa.Integer(), nullable=False),
            sa.Column("dismissed_at", sa.DateTime(), nullable=False),
            sa.Column("actor", sa.String(), nullable=True),
            sa.UniqueConstraint(
                "stack_id",
                "membership_fingerprint",
                name="uq_mixedstackdismissal_stack_fingerprint",
            ),
        )
    dismissal_indexes = _index_names(inspector, existing_tables, _DISMISSAL_TABLE)
    if _DISMISSAL_STACK_INDEX not in dismissal_indexes:
        op.create_index(_DISMISSAL_STACK_INDEX, _DISMISSAL_TABLE, ["stack_id"])
    if _DISMISSAL_FINGERPRINT_INDEX not in dismissal_indexes:
        op.create_index(
            _DISMISSAL_FINGERPRINT_INDEX,
            _DISMISSAL_TABLE,
            ["membership_fingerprint"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if _DISMISSAL_TABLE in existing_tables:
        op.drop_table(_DISMISSAL_TABLE)
    if _COHESION_TABLE in existing_tables:
        op.drop_table(_COHESION_TABLE)
