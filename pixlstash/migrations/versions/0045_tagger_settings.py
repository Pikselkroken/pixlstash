"""Add tagger_settings JSON column; drop legacy per-tagger flag columns.

Adds a single ``tagger_settings`` TEXT column that stores the full tagger
configuration as a JSON string.  The four legacy boolean/numeric columns
(``wd14_tagger_enabled``, ``wd14_threshold``, ``custom_tagger_enabled``,
``custom_tagger_threshold_offset``) are populated into the new JSON on the
way out, then dropped.

SQLite does not support ``ALTER TABLE DROP COLUMN`` via Alembic's default
path, so both add and drop operations use ``batch_alter_table`` which
reconstructs the table.  All operations are guarded by column-existence
checks so the migration is safe on fresh databases (where the baseline
migration already creates the table from the current SQLModel schema, which
no longer includes the four legacy columns).

Revision ID: 0045_tagger_settings
Revises: 0044_add_grid_sort_indexes
Create Date: 2026-05-23 00:00:00.000000

"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0045_tagger_settings"
down_revision: Union[str, None] = "0044_add_grid_sort_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_LEGACY_COLS = {
    "wd14_tagger_enabled",
    "wd14_threshold",
    "custom_tagger_enabled",
    "custom_tagger_threshold_offset",
}

_DEFAULT_WD14_THRESHOLD = 0.85
_DEFAULT_THRESHOLD_OFFSET = 0.0


def _build_tagger_settings(
    wd14_enabled: bool,
    wd14_threshold: float | None,
    custom_enabled: bool,
    custom_offset: float | None,
    has_descriptions: bool,
) -> str:
    """Build the initial ``tagger_settings`` JSON from legacy column values."""
    plugins: dict = {
        "wd14": {
            "enabled": bool(wd14_enabled),
            "params": {
                "threshold": float(wd14_threshold)
                if wd14_threshold is not None
                else _DEFAULT_WD14_THRESHOLD
            },
        },
        "pixlstash_tagger": {
            "enabled": bool(custom_enabled),
            "params": {
                "threshold_offset": float(custom_offset)
                if custom_offset is not None
                else _DEFAULT_THRESHOLD_OFFSET
            },
        },
        "florence2": {
            "params": {
                "max_new_tokens": 120,
                "fast_mode": False,
            }
        },
    }
    # Pre-select Florence-2 as the active description plugin when the vault
    # already contains descriptions, otherwise leave it selected by default so
    # new installs start generating descriptions immediately.
    active_description_plugin = "florence2"
    return json.dumps(
        {
            "active_description_plugin": active_description_plugin,
            "plugins": plugins,
        }
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = inspector.get_table_names()
    if "user" not in tables:
        # Fresh install — baseline migration creates the table via
        # SQLModel.metadata.create_all() using the current model, which
        # already has tagger_settings and lacks the four legacy columns.
        return

    existing_cols = {col["name"] for col in inspector.get_columns("user")}

    # ------------------------------------------------------------------
    # 1. Add tagger_settings column (conditional).
    # ------------------------------------------------------------------
    if "tagger_settings" not in existing_cols:
        with op.batch_alter_table("user") as batch_op:
            batch_op.add_column(sa.Column("tagger_settings", sa.Text(), nullable=True))

    # ------------------------------------------------------------------
    # 2. Populate tagger_settings from legacy columns where they exist.
    # ------------------------------------------------------------------
    legacy_present = _LEGACY_COLS & existing_cols
    if legacy_present and "tagger_settings" not in existing_cols:
        # Re-fetch after the batch_alter above — some backends may report stale state.
        pass

    conn = bind
    user_table = sa.table(
        "user",
        sa.column("id", sa.Integer),
        sa.column("tagger_settings", sa.Text),
        *(
            sa.column(c, sa.Text)
            for c in (
                "wd14_tagger_enabled",
                "wd14_threshold",
                "custom_tagger_enabled",
                "custom_tagger_threshold_offset",
            )
            if c in existing_cols
        ),
    )

    rows = conn.execute(sa.select(user_table)).fetchall()

    # Detect whether any pictures have descriptions (for sensible default).
    has_descriptions = False
    if "picture" in tables:
        picture_desc_count = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM picture WHERE description IS NOT NULL AND description != ''"
            )
        ).scalar()
        has_descriptions = bool(picture_desc_count and picture_desc_count > 0)

    for row in rows:
        row_dict = dict(row._mapping)
        existing_settings = row_dict.get("tagger_settings")
        if existing_settings:
            # Already migrated — skip.
            continue

        wd14_en = bool(row_dict.get("wd14_tagger_enabled", False))
        wd14_thr = row_dict.get("wd14_threshold")
        custom_en = bool(row_dict.get("custom_tagger_enabled", True))
        custom_off = row_dict.get("custom_tagger_threshold_offset")

        settings_json = _build_tagger_settings(
            wd14_enabled=wd14_en,
            wd14_threshold=float(wd14_thr) if wd14_thr is not None else None,
            custom_enabled=custom_en,
            custom_offset=float(custom_off) if custom_off is not None else None,
            has_descriptions=has_descriptions,
        )
        conn.execute(
            sa.update(user_table)
            .where(user_table.c.id == row_dict["id"])
            .values(tagger_settings=settings_json)
        )

    # ------------------------------------------------------------------
    # 3. Drop the four legacy columns via batch_alter_table (SQLite safe).
    #    Each drop is conditional on the column still being present.
    # ------------------------------------------------------------------
    cols_to_drop = _LEGACY_COLS & existing_cols
    if cols_to_drop:
        with op.batch_alter_table("user") as batch_op:
            for col in sorted(cols_to_drop):
                batch_op.drop_column(col)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "user" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("user")}

    # Re-add legacy columns if absent.
    with op.batch_alter_table("user") as batch_op:
        if "wd14_tagger_enabled" not in existing_cols:
            batch_op.add_column(
                sa.Column(
                    "wd14_tagger_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default="0",
                )
            )
        if "wd14_threshold" not in existing_cols:
            batch_op.add_column(sa.Column("wd14_threshold", sa.Float(), nullable=True))
        if "custom_tagger_enabled" not in existing_cols:
            batch_op.add_column(
                sa.Column(
                    "custom_tagger_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default="1",
                )
            )
        if "custom_tagger_threshold_offset" not in existing_cols:
            batch_op.add_column(
                sa.Column("custom_tagger_threshold_offset", sa.Float(), nullable=True)
            )

    # Restore values from tagger_settings JSON where possible.
    conn = bind
    user_table = sa.table(
        "user",
        sa.column("id", sa.Integer),
        sa.column("tagger_settings", sa.Text),
        sa.column("wd14_tagger_enabled", sa.Boolean),
        sa.column("wd14_threshold", sa.Float),
        sa.column("custom_tagger_enabled", sa.Boolean),
        sa.column("custom_tagger_threshold_offset", sa.Float),
    )
    rows = conn.execute(sa.select(user_table)).fetchall()
    for row in rows:
        row_dict = dict(row._mapping)
        settings_str = row_dict.get("tagger_settings")
        if not settings_str:
            continue
        try:
            settings = json.loads(settings_str)
        except (json.JSONDecodeError, TypeError):
            continue
        plugins = settings.get("plugins", {})
        wd14 = plugins.get("wd14", {})
        pixl = plugins.get("pixlstash_tagger", {})
        conn.execute(
            sa.update(user_table)
            .where(user_table.c.id == row_dict["id"])
            .values(
                wd14_tagger_enabled=bool(wd14.get("enabled", False)),
                wd14_threshold=wd14.get("params", {}).get("threshold"),
                custom_tagger_enabled=bool(pixl.get("enabled", True)),
                custom_tagger_threshold_offset=pixl.get("params", {}).get(
                    "threshold_offset"
                ),
            )
        )

    # Drop tagger_settings column.
    existing_cols = {col["name"] for col in inspector.get_columns("user")}
    if "tagger_settings" in existing_cols:
        with op.batch_alter_table("user") as batch_op:
            batch_op.drop_column("tagger_settings")
