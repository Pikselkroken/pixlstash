"""Startup: open the hub, resolve the active library, move identity across.

One entry point, :func:`bootstrap_hub`, called before the server opens a vault.
It answers "which library am I opening?" and guarantees that identity lives in
the hub by the time anything authenticates.

**Ordering is the whole design.** The vault's Alembic migrations run when the
file is opened, so anything that destroys vault-side credentials must happen
*after* this module has copied them. The sequence is therefore: open the hub,
register the configured vault as library #1 if the registry is empty, copy the
user and tokens across if the hub has no user yet, stamp the library's
fingerprint into the vault, and only then blank the vault's credentials.

**Losing the hub is survivable.** A missing hub is recreated empty and the owner
re-registers on loopback exactly as on a fresh install; the libraries are
re-attached and every picture, tag, score and snapshot is untouched, because all
of that lives in the library. What is lost is the password, the tokens (share
links included, permanently) and the preferences. That is a deliberate trade:
the hub is the cheap half, and the data worth protecting is in the library.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Optional

from pixlstash.hub.db import HubDatabase, default_hub_path
from pixlstash.hub.engine import HubEngine
from pixlstash.hub.registry import Library, LibraryRegistry, VAULT_FILENAME
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# Columns copied verbatim from the vault's ``user`` row into the hub's. Identity
# and the per-user/machine settings of plan §5; the library-scoped ones are not
# here because migration 0092 moved them into the vault's ``library_settings``.
_USER_COLUMNS_TO_COPY = (
    "username",
    "password_hash",
    "description",
    "theme_mode",
    "date_format",
    "sort",
    "descending",
    "columns",
    "thumbnail_mode",
    "thumbnail_size_level",
    "sidebar_thumbnail_size",
    "sidebar_width",
    "sidebar_docked",
    "sidebar_pinned",
    "compact_mode",
    "show_stars",
    "show_face_bboxes",
    "show_hand_bboxes",
    "show_format",
    "show_resolution",
    "show_problem_icon",
    "show_stacks",
    "show_keyboard_hint",
    "hide_purge_snapshot_warning",
    "comfyui_url",
    "public_url",
    "keep_models_in_memory",
    "max_vram_gb",
    "tagger_settings",
    "check_for_updates",
    "embed_watermark",
    "watermark_image",
)

_TOKEN_COLUMNS_TO_COPY = (
    "public_id",
    "token_hash",
    "token_prefix",
    "description",
    "scope",
    "resource_type",
    "resource_id",
    "created_at",
    "last_used_at",
    "expires_at",
    "include_attachments",
    "watermark",
)


@dataclass
class HubBootstrap:
    """What startup needs after the hub has been resolved."""

    hub: HubDatabase
    engine: HubEngine
    library: Library
    migrated: bool = False

    @property
    def image_root(self) -> str:
        """The active library's folder, which is the vault's image root."""
        return self.library.path


def bootstrap_hub(
    configured_image_root: str, hub_path: Optional[str] = None
) -> HubBootstrap:
    """Open the hub and return the library the server should open.

    Args:
        configured_image_root: The image root from server config. On a first run
            this is the vault that becomes library #1, which is what makes an
            upgrade invisible: the same folder, now named and registered.
        hub_path: Hub file, defaulting to :func:`default_hub_path`.

    Returns:
        A :class:`HubBootstrap` whose ``library`` is the active one.
    """
    hub = HubDatabase(hub_path or default_hub_path())
    registry = LibraryRegistry(hub)

    library = registry.active_library()
    if library is None:
        library = _register_first_library(registry, configured_image_root)

    engine = HubEngine(hub.path)
    migrated = _migrate_identity_if_needed(hub, engine, library)
    return HubBootstrap(hub=hub, engine=engine, library=library, migrated=migrated)


def _register_first_library(registry: LibraryRegistry, image_root: str) -> Library:
    """Register the configured vault as library #1 and make it active.

    Covers three cases with one path: a fresh install (the folder has no vault
    yet and the server is about to create one), an upgrade (the folder holds the
    only vault there has ever been), and a hub that was lost and recreated.
    """
    os.makedirs(image_root, exist_ok=True)
    vault_path = os.path.join(image_root, VAULT_FILENAME)

    if os.path.isfile(vault_path):
        logger.info("Registering the existing vault at %s as library 1", image_root)
        return registry.attach(image_root, "Library 1")

    # No vault yet: the server creates it moments from now, so register the
    # folder without asking the registry to validate a vault that does not exist.
    logger.info("Registering %s as library 1 (a new vault will be created)", image_root)
    return registry.register_pending(image_root, "Library 1")


def _migrate_identity_if_needed(
    hub: HubDatabase, engine: HubEngine, library: Library
) -> bool:
    """Copy the user and tokens out of the library's vault, once.

    A no-op when the hub already has a user, which is every run after the first.
    Returns True when a migration actually happened.
    """
    already = hub.connection.execute("SELECT COUNT(*) FROM user").fetchone()[0]
    if already:
        return False

    vault_path = library.vault_path
    if not os.path.isfile(vault_path):
        # A fresh install: there is no vault to migrate from and no identity to
        # preserve. The owner registers as they always have.
        return False

    vault = sqlite3.connect(vault_path)
    vault.row_factory = sqlite3.Row
    try:
        user_row = _read_user(vault)
        if user_row is None:
            return False
        token_rows = _read_tokens(vault, user_row["id"])
    finally:
        vault.close()

    _write_identity(hub, user_row, token_rows, library.uuid)
    logger.info(
        "Migrated the owner and %d token(s) from %s into the hub; tokens are "
        "stamped for library %s",
        len(token_rows),
        vault_path,
        library.uuid,
    )

    _stamp_vault_fingerprint(vault_path, library.uuid)
    _blank_vault_credentials(vault_path)
    return True


def _read_user(vault: sqlite3.Connection) -> Optional[sqlite3.Row]:
    """Return the vault's user row, or None when there is nothing to migrate."""
    try:
        return vault.execute("SELECT * FROM user LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        logger.warning("Could not read the user row from the vault: %s", exc)
        return None


def _read_tokens(vault: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    """Return the vault's token rows for *user_id*."""
    try:
        return vault.execute(
            "SELECT * FROM usertoken WHERE user_id = ?", (user_id,)
        ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("Could not read tokens from the vault: %s", exc)
        return []


def _write_identity(
    hub: HubDatabase,
    user_row: sqlite3.Row,
    token_rows: list[sqlite3.Row],
    library_uuid: str,
) -> None:
    """Insert the user and its tokens into the hub in one transaction.

    All or nothing: a partial copy would leave an owner who can log in but whose
    share links have vanished, or the reverse.
    """
    available = set(user_row.keys())
    columns = [name for name in _USER_COLUMNS_TO_COPY if name in available]
    token_columns = (
        [name for name in _TOKEN_COLUMNS_TO_COPY if name in set(token_rows[0].keys())]
        if token_rows
        else []
    )

    with hub.transaction() as conn:
        placeholders = ", ".join("?" for _ in columns)
        cursor = conn.execute(
            f"INSERT INTO user ({', '.join(columns)}, is_admin) "
            f"VALUES ({placeholders}, 1)",
            [user_row[name] for name in columns],
        )
        user_id = int(cursor.lastrowid)

        for token in token_rows:
            token_placeholders = ", ".join("?" for _ in token_columns)
            conn.execute(
                f"INSERT INTO usertoken (user_id, library_uuid, "
                f"{', '.join(token_columns)}) VALUES (?, ?, {token_placeholders})",
                [user_id, library_uuid] + [token[name] for name in token_columns],
            )


def _stamp_vault_fingerprint(vault_path: str, library_uuid: str) -> None:
    """Record the library's identity inside its own vault.

    Written only for a library this installation owns and is migrating, never
    into a vault attached from elsewhere. It is a fingerprint, not a credential:
    :func:`pixlstash.hub.registry.read_vault_uuid` uses it to decide whether a
    re-attached path is the same library, and nothing authorises on it.
    """
    try:
        conn = sqlite3.connect(vault_path)
        try:
            with conn:
                conn.execute(
                    "UPDATE library_settings SET library_uuid = ? "
                    "WHERE library_uuid IS NULL",
                    (library_uuid,),
                )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # Not fatal: without a fingerprint the registry falls back to matching on
        # path alone, which is the pre-fingerprint behaviour.
        logger.warning(
            "Could not stamp the library fingerprint into %s: %s. Re-attaching "
            "this library after a detach will match on path alone.",
            vault_path,
            exc,
        )


def _blank_vault_credentials(vault_path: str) -> None:
    """Clear the credentials left behind in the vault.

    This is what makes a library safe to copy, move or hand to someone else: the
    hub now holds the only live credentials, and the rows left here authenticate
    nothing. The tables themselves stay until a post-1.12 cleanup, because
    dropping them is a rewrite and blanking them is a targeted UPDATE.

    Runs only after :func:`_write_identity` has committed.
    """
    try:
        conn = sqlite3.connect(vault_path)
        try:
            with conn:
                conn.execute("UPDATE user SET password_hash = NULL")
                conn.execute("DELETE FROM usertoken")
        finally:
            conn.close()
        logger.info("Blanked the credentials left in %s", vault_path)
    except sqlite3.Error as exc:
        logger.error(
            "Could not blank the credentials in %s: %s. The hub now holds the "
            "live copy, but this library still carries a password hash and must "
            "not be shared until that is cleared.",
            vault_path,
            exc,
        )
