"""Rename tag sentinel from empty string to __tag; insert sentinel for untagged pictures.

Changes:
1. Converts any existing empty-string sentinel tag (``tag.tag = ''``) to the
   new ``'__tag'`` pending-retag sentinel.
2. Inserts a ``'__tag'`` sentinel for every non-deleted, non-orphaned picture
   that currently has zero tag rows, so MissingTagFinder picks them up after
   the upgrade.

Revision ID: 0046_tag_sentinel_rename
Revises: 0045_tagger_settings
Create Date: 2026-05-25 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0046_tag_sentinel_rename"
down_revision: Union[str, None] = "0045_tagger_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    # 1. Convert the old empty-string sentinel to the new __tag value.
    op.execute("UPDATE tag SET tag = '__tag' WHERE tag = ''")

    # 2. Insert __tag sentinel for pictures that have zero tag rows.
    #    These may be pre-sentinel pictures or pictures whose sentinel was
    #    previously removed.  After the upgrade MissingTagFinder will pick
    #    them up and tag them automatically.
    op.execute(
        "INSERT INTO tag (picture_id, tag) "
        "SELECT p.id, '__tag' "
        "FROM picture p "
        "WHERE NOT EXISTS (SELECT 1 FROM tag t WHERE t.picture_id = p.id) "
        "  AND p.deleted = 0 "
        "  AND p.file_path IS NOT NULL"
    )


def downgrade() -> None:
    # Remove all pending-retag sentinel rows (covers both '__tag' and
    # engine-specific variants like '__tag:joycaption').
    op.execute("DELETE FROM tag WHERE tag GLOB '__tag*'")
    # Restore the old empty-string sentinel for every picture that now has no
    # tags so the legacy MissingTagFinder can still find them.
    op.execute(
        "INSERT OR IGNORE INTO tag (picture_id, tag) "
        "SELECT id, '' FROM picture "
        "WHERE NOT EXISTS (SELECT 1 FROM tag t WHERE t.picture_id = picture.id)"
    )
