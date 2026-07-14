"""Rename the anomaly tag "pixelated" to "blocky".

The anomaly tagger's quality label was renamed from ``pixelated`` to ``blocky`` (a clearer
name for blocky/compression-style degradation). Existing ``tag`` and ``tag_prediction``
rows still carry the old value, so this migration renames them in place — preserving any
human POS/NEG supervision attached to the prediction rows — and NULL-resets the smart score
of affected pictures so it recomputes under the tag's (changed) penalty weight.

Data-only: no schema changes. The rename is collision-safe against the (picture_id, tag)
unique constraint in case a database already produced fresh ``blocky`` rows from a newer
tagger before this migration ran; human-labelled rows win such a collision.

Revision ID: 0063_rename_pixelated_tag_to_blocky
Revises: 0062_recompute_smart_score_calibrated_anomaly
Create Date: 2026-06-28 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0063_rename_pixelated_tag_to_blocky"
down_revision: Union[str, None] = "0062_recompute_smart_score_calibrated_anomaly"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_OLD = "pixelated"
_NEW = "blocky"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # --- tag_prediction: rename, preserving human supervision on collision ----------
    if "tag_prediction" in tables:
        pred_cols = {c["name"] for c in inspector.get_columns("tag_prediction")}
        has_labels = "label_source" in pred_cols
        if has_labels:
            # If a picture has a human-confirmed "pixelated" prediction AND a non-human
            # "blocky" one, drop the non-human "blocky" so the human decision survives the
            # rename instead of being blocked by the (picture_id, tag) unique constraint.
            bind.execute(
                sa.text(
                    """
                    DELETE FROM tag_prediction
                    WHERE tag = :new
                      AND (label_source IS NULL OR label_source <> 'human')
                      AND picture_id IN (
                          SELECT picture_id FROM tag_prediction
                          WHERE tag = :old AND label_source = 'human'
                      )
                    """
                ),
                {"old": _OLD, "new": _NEW},
            )
        # Drop any remaining old rows whose picture already has a "blocky" row (keeps the
        # existing one) so the rename below can't violate the unique constraint.
        bind.execute(
            sa.text(
                """
                DELETE FROM tag_prediction
                WHERE tag = :old
                  AND picture_id IN (
                      SELECT picture_id FROM tag_prediction WHERE tag = :new
                  )
                """
            ),
            {"old": _OLD, "new": _NEW},
        )
        bind.execute(
            sa.text("UPDATE tag_prediction SET tag = :new WHERE tag = :old"),
            {"old": _OLD, "new": _NEW},
        )

    # --- tag: rename, dropping duplicates that would collide -------------------------
    if "tag" in tables:
        bind.execute(
            sa.text(
                """
                DELETE FROM tag
                WHERE tag = :old
                  AND picture_id IN (SELECT picture_id FROM tag WHERE tag = :new)
                """
            ),
            {"old": _OLD, "new": _NEW},
        )
        bind.execute(
            sa.text("UPDATE tag SET tag = :new WHERE tag = :old"),
            {"old": _OLD, "new": _NEW},
        )

    # --- Recompute smart score for affected pictures ---------------------------------
    # The penalised-tag weight differs between the old and new name, so any stored score
    # on a picture now carrying "blocky" is stale; MissingSmartScoreFinder recomputes it.
    if "picture" in tables and "tag" in tables:
        pic_cols = {c["name"] for c in inspector.get_columns("picture")}
        if "smart_score" in pic_cols:
            bind.execute(
                sa.text(
                    """
                    UPDATE picture SET smart_score = NULL
                    WHERE id IN (SELECT picture_id FROM tag WHERE tag = :new)
                    """
                ),
                {"new": _NEW},
            )


def downgrade() -> None:
    # No-op: a blind "blocky" -> "pixelated" rename would also clobber genuinely-new
    # "blocky" rows produced by the current tagger, so the rename is not reversed.
    pass
