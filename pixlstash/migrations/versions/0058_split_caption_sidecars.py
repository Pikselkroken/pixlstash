"""Split the single caption sidecar into independent tags and description sidecars.

Replaces ``reference_folder.sync_captions`` with two independent toggles
(``sync_tags`` / ``sync_descriptions``) plus a configurable filename suffix for
each (``tags_suffix`` / ``description_suffix``).

Replaces the single ``picture.caption_file`` / ``caption_file_mtime`` pair with
two pairs — one per sidecar type — so tags and descriptions can live in separate
files and have their external changes tracked independently.

Existing data is preserved: the old ``caption_file`` is split by extension
(``.txt`` → tags, ``.caption`` → description) and the old ``sync_captions`` value
is copied onto both new toggles.

Revision ID: 0058_split_caption_sidecars
Revises: 0057_add_tag_suggestion
Create Date: 2026-06-16 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0058_split_caption_sidecars"
down_revision: Union[str, None] = "0057_add_tag_suggestion"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    picture_cols = (
        {col["name"] for col in inspector.get_columns("picture")}
        if "picture" in tables
        else set()
    )
    rf_cols = (
        {col["name"] for col in inspector.get_columns("reference_folder")}
        if "reference_folder" in tables
        else set()
    )

    # --- picture: add the two new sidecar pairs ---
    if "picture" in tables:
        _upgrade_picture(picture_cols)

    # --- reference_folder: add toggles + suffixes ---
    if "reference_folder" in tables:
        _upgrade_reference_folder(rf_cols)


def _upgrade_picture(picture_cols: set) -> None:
    if "tags_file" not in picture_cols:
        op.add_column("picture", sa.Column("tags_file", sa.String(), nullable=True))
    if "tags_file_mtime" not in picture_cols:
        op.add_column(
            "picture", sa.Column("tags_file_mtime", sa.Float(), nullable=True)
        )
    if "description_file" not in picture_cols:
        op.add_column(
            "picture", sa.Column("description_file", sa.String(), nullable=True)
        )
    if "description_file_mtime" not in picture_cols:
        op.add_column(
            "picture", sa.Column("description_file_mtime", sa.Float(), nullable=True)
        )

    # --- picture: split the old single caption_file by extension, then drop it ---
    if "caption_file" in picture_cols:
        op.execute(
            sa.text(
                "UPDATE picture SET tags_file = caption_file, "
                "tags_file_mtime = caption_file_mtime "
                "WHERE caption_file IS NOT NULL AND lower(caption_file) LIKE '%.txt'"
            )
        )
        op.execute(
            sa.text(
                "UPDATE picture SET description_file = caption_file, "
                "description_file_mtime = caption_file_mtime "
                "WHERE caption_file IS NOT NULL AND lower(caption_file) LIKE '%.caption'"
            )
        )
        op.drop_column("picture", "caption_file_mtime")
        op.drop_column("picture", "caption_file")


def _upgrade_reference_folder(rf_cols: set) -> None:
    # --- reference_folder: add the two new toggles + suffix columns ---
    if "sync_descriptions" not in rf_cols:
        op.add_column(
            "reference_folder",
            sa.Column(
                "sync_descriptions", sa.Boolean(), nullable=False, server_default="0"
            ),
        )
    if "sync_tags" not in rf_cols:
        op.add_column(
            "reference_folder",
            sa.Column("sync_tags", sa.Boolean(), nullable=False, server_default="0"),
        )
    if "description_suffix" not in rf_cols:
        op.add_column(
            "reference_folder",
            sa.Column("description_suffix", sa.String(), nullable=True),
        )
    if "tags_suffix" not in rf_cols:
        op.add_column(
            "reference_folder", sa.Column("tags_suffix", sa.String(), nullable=True)
        )

    # --- reference_folder: copy old sync_captions onto both toggles, then drop ---
    if "sync_captions" in rf_cols:
        op.execute(
            sa.text(
                "UPDATE reference_folder SET sync_tags = sync_captions, "
                "sync_descriptions = sync_captions"
            )
        )
        op.drop_column("reference_folder", "sync_captions")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "picture" in tables:
        _downgrade_picture({col["name"] for col in inspector.get_columns("picture")})
    if "reference_folder" in tables:
        _downgrade_reference_folder(
            {col["name"] for col in inspector.get_columns("reference_folder")}
        )


def _downgrade_picture(picture_cols: set) -> None:
    # --- picture: re-add the single caption pair and recombine ---
    if "caption_file" not in picture_cols:
        op.add_column("picture", sa.Column("caption_file", sa.String(), nullable=True))
    if "caption_file_mtime" not in picture_cols:
        op.add_column(
            "picture", sa.Column("caption_file_mtime", sa.Float(), nullable=True)
        )
    op.execute(
        sa.text(
            "UPDATE picture SET caption_file = COALESCE(tags_file, description_file), "
            "caption_file_mtime = COALESCE(tags_file_mtime, description_file_mtime)"
        )
    )
    for col in (
        "tags_file",
        "tags_file_mtime",
        "description_file",
        "description_file_mtime",
    ):
        if col in picture_cols:
            op.drop_column("picture", col)


def _downgrade_reference_folder(rf_cols: set) -> None:
    # --- reference_folder: re-add sync_captions as the OR of the two toggles ---
    if "sync_captions" not in rf_cols:
        op.add_column(
            "reference_folder",
            sa.Column(
                "sync_captions", sa.Boolean(), nullable=False, server_default="0"
            ),
        )
    op.execute(
        sa.text(
            "UPDATE reference_folder SET sync_captions = "
            "CASE WHEN sync_tags = 1 OR sync_descriptions = 1 THEN 1 ELSE 0 END"
        )
    )
    for col in ("sync_tags", "sync_descriptions", "tags_suffix", "description_suffix"):
        if col in rf_cols:
            op.drop_column("reference_folder", col)
