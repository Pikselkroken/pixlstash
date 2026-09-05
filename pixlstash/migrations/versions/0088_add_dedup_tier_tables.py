"""Add the tiered duplicate-detection tables (v1.9 Dedup -> Stacks).

Four tables backing the Duplicates queue:

* ``dedupgroup`` / ``dedupgroupmember`` - the found-groups cache. Detection
  writes groups as it finds them so the queue can be paged from the database by
  confidence descending instead of being materialised whole.
* ``dedupverdict`` - verdict memory keyed on a group signature (the sorted
  member content hashes), so rescans and re-imports never re-ask.
* ``dedupscan`` - per-scope scan progress for the "scanned N of M" banner.

**No reprocessing reset is needed on `picture`.** Tier 1 reuses the existing
indexed ``picture.pixel_sha`` column and tier 2 the existing
``picture.perceptual_hash`` / ``picture.size_bin_index`` columns; nothing
computed by an earlier release becomes invalid, so no column is NULLed here.
Pictures whose ``pixel_sha`` was never computed are backfilled by
``MissingPixelShaFinder`` at runtime, which already selects on
``pixel_sha IS NULL`` - a reset would be a no-op.

**Stale signatures are purged.** The group signature was changed during review to
include the ``size_bytes`` co-key (``<pixel_sha>:<size_bytes>``), because the
digest alone is sampled above 128 KiB and did not identify a file - two distinct
exact groups could collide on one signature. Rows written before that change are
keyed on the old format: a stale ``dedupverdict`` would silently never match
again (a remembered decision quietly forgotten) and a stale ``dedupgroup`` would
linger forever, matching no future detection while still inflating the sidebar
badge. Neither self-heals, so this migration empties the four tables when they
already exist. That can only affect a developer machine that ran this feature
branch before the fix - the tables ship for the first time here - and rebuilding
them costs one rescan. Amending the migration rather than stacking a second one
follows the feature-branch rule in CLAUDE.md.

Revision ID: 0088_add_dedup_tier_tables
Revises: 0087_add_entity_project_membership
Create Date: 2026-07-29 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0088_add_dedup_tier_tables"
down_revision: Union[str, None] = "0087_add_entity_project_membership"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "dedupgroup" not in existing_tables:
        op.create_table(
            "dedupgroup",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("signature", sa.String(), nullable=False),
            sa.Column("tier", sa.String(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("member_count", sa.Integer(), nullable=True),
            sa.Column("cover_picture_id", sa.Integer(), nullable=True),
            sa.Column("evidence", sa.String(), nullable=True),
            sa.Column("resolved", sa.Boolean(), nullable=True),
            sa.Column("scan_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_dedupgroup_signature", "dedupgroup", ["signature"], unique=True
        )
        op.create_index("ix_dedupgroup_tier", "dedupgroup", ["tier"])
        op.create_index("ix_dedupgroup_confidence", "dedupgroup", ["confidence"])
        op.create_index("ix_dedupgroup_resolved", "dedupgroup", ["resolved"])
        op.create_index("ix_dedupgroup_scan_id", "dedupgroup", ["scan_id"])
        op.create_index("ix_dedupgroup_queue", "dedupgroup", ["resolved", "confidence"])

    if "dedupgroupmember" not in existing_tables:
        op.create_table(
            "dedupgroupmember",
            sa.Column("group_id", sa.Integer(), nullable=False),
            sa.Column("picture_id", sa.Integer(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(
                ["group_id"], ["dedupgroup.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["picture_id"], ["picture.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("group_id", "picture_id"),
        )
        op.create_index(
            "ix_dedupgroupmember_group_id", "dedupgroupmember", ["group_id"]
        )
        op.create_index(
            "ix_dedupgroupmember_picture_id", "dedupgroupmember", ["picture_id"]
        )

    if "dedupverdict" not in existing_tables:
        op.create_table(
            "dedupverdict",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("signature", sa.String(), nullable=False),
            sa.Column("verdict", sa.String(), nullable=False),
            sa.Column("picture_ids", sa.String(), nullable=False),
            sa.Column("excluded_picture_ids", sa.String(), nullable=False),
            sa.Column("cover_picture_id", sa.Integer(), nullable=True),
            sa.Column("stack_id", sa.Integer(), nullable=True),
            sa.Column("batch_id", sa.String(), nullable=True),
            sa.Column("decided_at", sa.DateTime(), nullable=False),
            sa.Column("reopened_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_dedupverdict_signature", "dedupverdict", ["signature"], unique=True
        )
        op.create_index("ix_dedupverdict_verdict", "dedupverdict", ["verdict"])
        op.create_index("ix_dedupverdict_stack_id", "dedupverdict", ["stack_id"])
        op.create_index("ix_dedupverdict_batch_id", "dedupverdict", ["batch_id"])

    if "dedupscan" not in existing_tables:
        op.create_table(
            "dedupscan",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("scope_key", sa.String(), nullable=False),
            sa.Column("scope_type", sa.String(), nullable=False),
            sa.Column("scope_id", sa.String(), nullable=True),
            sa.Column("tiers", sa.String(), nullable=False),
            sa.Column("threshold", sa.Float(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("total_pictures", sa.Integer(), nullable=True),
            sa.Column("scanned_pictures", sa.Integer(), nullable=True),
            sa.Column("total_buckets", sa.Integer(), nullable=True),
            sa.Column("scanned_buckets", sa.Integer(), nullable=True),
            sa.Column("groups_found", sa.Integer(), nullable=True),
            sa.Column("error", sa.String(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_dedupscan_scope_key", "dedupscan", ["scope_key"], unique=True
        )
        op.create_index("ix_dedupscan_status", "dedupscan", ["status"])

    # Purge rows written under the pre-review signature format (see the module
    # docstring). Only a table that already existed can hold them; a table just
    # created above is empty, so the DELETE is a no-op there. Children first so
    # the foreign key holds on databases that enforce it.
    for table in ("dedupgroupmember", "dedupgroup", "dedupverdict", "dedupscan"):
        if table in existing_tables:
            op.execute(sa.text(f"DELETE FROM {table}"))  # noqa: S608 - fixed literals


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    for table in ("dedupscan", "dedupverdict", "dedupgroupmember", "dedupgroup"):
        if table in existing_tables:
            op.drop_table(table)
