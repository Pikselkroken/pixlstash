"""Track which InsightFace model pack produced each face embedding.

Adds a nullable ``model_pack`` column to the ``face`` table so the face pipeline
can record which InsightFace pack (e.g. ``buffalo_l`` or ``auraface``) produced
a given embedding, and so a pack change can be detected and refreshed in place.

All faces that exist before this migration were produced by ``buffalo_l`` (the
only pack PixlStash supported until now), so existing rows are backfilled to
``buffalo_l``. We do NOT delete faces or force a full re-extraction: the
FACE_MODEL_REFRESH finder will refresh embeddings in place only when the
configured pack differs from a face's recorded ``model_pack``.

Revision ID: 0052_add_face_model_pack
Revises: 0051_deleted_file_log_path_sha
Create Date: 2026-06-09 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0052_add_face_model_pack"
down_revision: Union[str, None] = "0051_deleted_file_log_path_sha"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "face" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("face")}

    # On a fresh DB the baseline create_all already added model_pack, so guard
    # the add. The backfill below still runs and harmlessly no-ops (no rows).
    if "model_pack" not in existing_cols:
        op.add_column("face", sa.Column("model_pack", sa.String(), nullable=True))

    # Backfill: every pre-existing face was produced by buffalo_l. Stamp them so
    # the refresh finder treats them as buffalo_l, not as "unknown / needs work".
    face = sa.table(
        "face",
        sa.column("model_pack", sa.String),
    )
    op.execute(
        face.update().where(face.c.model_pack.is_(None)).values(model_pack="buffalo_l")
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "face" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("face")}
    if "model_pack" in existing_cols:
        op.drop_column("face", "model_pack")
