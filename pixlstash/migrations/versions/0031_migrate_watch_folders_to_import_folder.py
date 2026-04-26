"""Migrate legacy watch_folders config entries into import_folder rows.

Revision ID: 0031_migrate_watch_folders_to_import_folder
Revises: 0030_add_import_folders
Create Date: 2026-04-26 00:00:00.000000

"""

from __future__ import annotations

import json
import logging
import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from platformdirs import user_config_dir

# revision identifiers, used by Alembic.
revision: str = "0031_migrate_watch_folders_to_import_folder"
down_revision: Union[str, None] = "0030_add_import_folders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

logger = logging.getLogger(__name__)


def _resolve_server_config_path() -> str:
    env_path = os.environ.get("PIXLSTASH_SERVER_CONFIG")
    if env_path:
        return os.path.abspath(os.path.expanduser(env_path))
    return os.path.join(user_config_dir("pixlstash"), "server-config.json")


def _read_config(config_path: str) -> dict:
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def _write_config(config_path: str, config: dict) -> None:
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
    except Exception as exc:
        logger.warning(
            "!!!!!!!!!! FAILED TO WRITE SERVER CONFIG DURING MIGRATION !!!!!!!!!!"
        )
        logger.warning(
            "Could not persist migrated watch_folders cleanup to %s: %s",
            config_path,
            exc,
        )
        logger.warning(
            "Manual cleanup may be required: remove the legacy 'watch_folders' key from server-config.json."
        )


def _normalize_watch_folders(raw_entries: object) -> list[dict]:
    normalized: list[dict] = []
    seen: set[str] = set()

    for entry in raw_entries or []:
        folder: str | None = None
        delete_after_import = False
        last_checked = None

        if isinstance(entry, str):
            folder = entry.strip() or None
        elif isinstance(entry, dict):
            value = entry.get("folder")
            if value:
                folder = str(value).strip() or None
            delete_after_import = bool(entry.get("delete_after_import", False))
            raw_last_checked = entry.get("last_checked")
            if raw_last_checked is not None:
                try:
                    last_checked = float(raw_last_checked)
                except (TypeError, ValueError):
                    last_checked = None

        if not folder:
            continue

        folder = os.path.normpath(os.path.abspath(os.path.expanduser(folder)))
        if folder in seen:
            continue
        seen.add(folder)

        normalized.append(
            {
                "folder": folder,
                "label": os.path.basename(folder),
                "delete_after_import": delete_after_import,
                "last_checked": last_checked,
            }
        )

    return normalized


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "import_folder" not in inspector.get_table_names():
        return

    config_path = _resolve_server_config_path()
    config = _read_config(config_path)
    watch_folders = _normalize_watch_folders(config.get("watch_folders") or [])

    if watch_folders:
        existing_rows = bind.execute(sa.text("SELECT folder FROM import_folder"))
        existing = {str(row[0]) for row in existing_rows if row and row[0]}

        insert_stmt = sa.text(
            """
            INSERT INTO import_folder (folder, label, delete_after_import, last_checked)
            VALUES (:folder, :label, :delete_after_import, :last_checked)
            """
        )

        for entry in watch_folders:
            if entry["folder"] in existing:
                continue
            bind.execute(insert_stmt, entry)

    if "watch_folders" in config:
        config.pop("watch_folders", None)
        _write_config(config_path, config)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "import_folder" not in inspector.get_table_names():
        return

    rows = bind.execute(
        sa.text(
            """
            SELECT folder, delete_after_import, last_checked
            FROM import_folder
            ORDER BY id ASC
            """
        )
    )

    watch_folders = []
    for row in rows:
        folder = str(row[0] or "").strip()
        if not folder:
            continue
        entry = {
            "folder": folder,
            "delete_after_import": bool(row[1]),
        }
        if row[2] is not None:
            entry["last_checked"] = float(row[2])
        watch_folders.append(entry)

    config_path = _resolve_server_config_path()
    config = _read_config(config_path)
    config["watch_folders"] = watch_folders
    _write_config(config_path, config)
