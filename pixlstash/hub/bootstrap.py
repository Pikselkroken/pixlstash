"""Crash-safe startup for the hub/vault identity split.

Bootstrap deliberately has two phases.  Before the vault is opened it may copy
legacy identity into the hub, but it never writes the vault.  After
``VaultDatabase`` has run Alembic, :func:`finalize_opened_library` stamps and
validates the library fingerprint and only then blanks the legacy credentials.
The hub records every phase so a crash resumes work instead of guessing from
the contents of either database.
"""

from __future__ import annotations

import os
import sqlite3
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pixlstash.hub.db import HubDatabase, default_hub_path
from pixlstash.hub.engine import HubEngine
from pixlstash.hub.registry import (
    Library,
    LibraryError,
    LibraryRegistry,
    VAULT_FILENAME,
    validate_vault_folder,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.trusted_sqlite import TrustedSQLiteLocation
from pixlstash.services.portable_identity import sanitize_vault_connection

logger = get_logger(__name__)


class HubBootstrapError(RuntimeError):
    """Startup cannot safely establish a consistent hub/vault pair."""


class RegisteredVaultPath(str):
    """A path carrying the hub registration that authorises a vault open."""

    def __new__(
        cls,
        path: str,
        hub: HubDatabase,
        library: Library,
        bootstrap: "HubBootstrap | None" = None,
    ):
        value = str.__new__(cls, path)
        value.hub = hub
        value.library = library
        value.bootstrap = bootstrap
        return value


def registered_vault_path(
    hub: HubDatabase,
    library: Library,
    bootstrap: "HubBootstrap | None" = None,
) -> RegisteredVaultPath:
    return RegisteredVaultPath(library.path, hub, library, bootstrap)


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
    "stack_strictness",
    "smart_score_penalised_tags",
    "hidden_tags",
    "apply_tag_filter",
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
    hub: HubDatabase
    engine: HubEngine
    library: Library
    migrated: bool = False

    @property
    def image_root(self) -> str:
        return self.library.path


def bootstrap_hub(
    configured_image_root: str,
    hub_path: Optional[str] = None,
) -> HubBootstrap:
    """Open the hub and perform only the pre-vault-open part of migration.

    The safe default is ``NEW_INSTALL``: an arbitrary vault found at
    ``image_root`` is registered but its identity rows remain inert.  The
    startup never turns config shape into import authority. A legacy copy runs
    only when :func:`prepare_legacy_identity` already recorded explicit intent.
    """
    hub = HubDatabase(hub_path or default_hub_path())
    registry = LibraryRegistry(hub)
    library = registry.active_library()
    if library is None:
        library = _register_first_library(registry, configured_image_root)

    engine = HubEngine(hub.path)
    migrated = _copy_legacy_identity_if_needed(hub, library)
    library = registry.by_uuid(library.uuid) or library
    return HubBootstrap(hub=hub, engine=engine, library=library, migrated=migrated)


def _register_first_library(
    registry: LibraryRegistry,
    image_root: str,
) -> Library:
    os.makedirs(image_root, mode=0o700, exist_ok=True)
    vault_path = os.path.join(image_root, VAULT_FILENAME)
    exists = os.path.isfile(vault_path)

    if exists:
        logger.info(
            "Registering an existing vault at %s without importing identity; "
            "only an explicit durable preparation operation can authorize import",
            image_root,
        )
        return registry.attach(image_root, "Library 1")

    # This process is creating the SQLite namespace, so establish the trust
    # boundary now instead of inheriting a permissive umask-created directory.
    if os.name != "nt":
        os.chmod(image_root, 0o700)
    return registry.register_pending(image_root, "Library 1")


def _identity_payload(
    vault: sqlite3.Connection,
) -> tuple[sqlite3.Row | None, list[sqlite3.Row], str]:
    vault.row_factory = sqlite3.Row
    user = _read_user(vault)
    tokens = _read_tokens(vault, user["id"]) if user is not None else []
    user_keys = set(user.keys()) if user is not None else set()
    serializable = {
        # Hash exactly the values the copy operation is authorised to move.
        # Ignore schema-only columns (notably 0091's token library_uuid), so
        # Alembic running between copy and finalization cannot invalidate the
        # approved payload without any identity value having changed.
        "user": (
            {name: user[name] for name in _USER_COLUMNS_TO_COPY if name in user_keys}
            if user is not None
            else None
        ),
        "tokens": [
            {
                name: row[name]
                for name in _TOKEN_COLUMNS_TO_COPY
                if name in set(row.keys())
            }
            for row in tokens
        ],
    }
    encoded = json.dumps(
        serializable, sort_keys=True, separators=(",", ":"), default=str
    )
    return user, tokens, hashlib.sha256(encoded.encode()).hexdigest()


def prepare_legacy_identity(hub: HubDatabase, folder: str) -> Library:
    """Record explicit, path- and payload-bound one-shot migration intent."""
    resolved = os.path.realpath(os.path.abspath(os.path.expanduser(folder)))
    vault_path = validate_vault_folder(resolved)
    registry = LibraryRegistry(hub)
    existing = hub.fetchone("SELECT uuid FROM library WHERE path = ?", (resolved,))
    if existing is not None:
        operation = hub.fetchone(
            "SELECT state FROM identity_migration_operation WHERE library_uuid = ?",
            (existing[0],),
        )
        if operation is not None and operation[0] != "pending":
            raise HubBootstrapError(
                f"Legacy migration is already {operation[0]}; refusing to "
                "reauthorize it."
            )
    try:
        vault = sqlite3.connect(f"file:{vault_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise HubBootstrapError(
            f"Could not open legacy vault {vault_path}: {exc}"
        ) from exc
    try:
        user, _tokens, digest = _identity_payload(vault)
    finally:
        vault.close()
    if user is None:
        raise HubBootstrapError("The selected vault has no legacy owner to migrate.")
    try:
        return registry.record_legacy_preparation(resolved, digest)
    except LibraryError as exc:
        raise HubBootstrapError(str(exc)) from exc


def _copy_legacy_identity_if_needed(hub: HubDatabase, library: Library) -> bool:
    state = library.identity_migration_state
    if state != "pending":
        return False
    operation = hub.fetchone(
        "SELECT source_path, payload_digest, state FROM identity_migration_operation "
        "WHERE library_uuid = ?",
        (library.uuid,),
    )
    if operation is None or operation[2] != "pending" or operation[0] != library.path:
        raise HubBootstrapError(
            "Legacy migration is pending without matching explicit intent."
        )
    vault_path = library.vault_path
    if not os.path.isfile(vault_path):
        raise HubBootstrapError(
            f"Approved legacy vault {vault_path} is missing; migration remains pending."
        )

    try:
        vault = sqlite3.connect(f"file:{vault_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise HubBootstrapError(
            f"Could not open legacy identity database {vault_path}: {exc}"
        ) from exc
    vault.row_factory = sqlite3.Row
    try:
        user_row, token_rows, digest = _identity_payload(vault)
        if digest != operation[1]:
            raise HubBootstrapError(
                "Legacy identity payload changed after approval; nothing was copied."
            )
        if user_row is None:
            raise HubBootstrapError("Approved legacy owner disappeared before copy.")
    finally:
        vault.close()

    _write_identity_and_mark_copied(hub, library, user_row, token_rows)
    logger.info(
        "Copied the owner and %d token(s) from %s into the hub; vault cleanup "
        "will run only after Alembic and fingerprint validation",
        len(token_rows),
        vault_path,
    )
    return True


def _read_user(vault: sqlite3.Connection) -> Optional[sqlite3.Row]:
    try:
        return vault.execute("SELECT * FROM user LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        raise HubBootstrapError(f"Could not read the legacy user row: {exc}") from exc


def _read_tokens(vault: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    try:
        return vault.execute(
            "SELECT * FROM usertoken WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
    except sqlite3.Error as exc:
        raise HubBootstrapError(f"Could not read legacy tokens: {exc}") from exc


def prevalidate_library_fingerprint(library: Library) -> None:
    """Fail before Alembic when a hub-stamped library no longer matches.

    A recorded ``vault_uuid`` means this installation has already opened and
    stamped the library. From then on absence, unreadability, malformed schema,
    and a different value are all decisive failures: treating a read error as
    an unstamped legacy vault would let startup migrate a replacement database
    before discovering it is foreign.
    """
    expected = library.vault_uuid
    if expected is None:
        return
    try:
        conn = sqlite3.connect(
            f"file:{library.vault_path}?mode=ro", uri=True, timeout=5
        )
        try:
            row = conn.execute(
                "SELECT library_uuid FROM library_settings LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise HubBootstrapError(
            f"Could not verify the registered library fingerprint at "
            f"{library.vault_path} before migration: {exc}"
        ) from exc
    observed = row[0] if row and row[0] else None
    if observed != expected:
        raise HubBootstrapError(
            f"Library fingerprint conflict at {library.vault_path}: the vault "
            f"contains {observed or 'no fingerprint'}, but the hub expects "
            f"{expected}. The vault was not opened or migrated."
        )


def _opened_execute(connection, sql: str, params: tuple = ()):
    if hasattr(connection, "exec_driver_sql"):
        return connection.exec_driver_sql(sql, params)
    return connection.execute(sql, params)


def _opened_first(connection, sql: str, params: tuple = ()):
    result = _opened_execute(connection, sql, params)
    if hasattr(result, "mappings"):
        row = result.mappings().first()
        return dict(row) if row is not None else None
    row = result.fetchone()
    return dict(row) if isinstance(row, sqlite3.Row) else row


def _opened_revision_rows(connection) -> list[str]:
    try:
        exists = _opened_first(
            connection,
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='alembic_version'",
        )
    except Exception as exc:
        raise HubBootstrapError(
            f"Could not inspect the vault schema lineage: {exc}"
        ) from exc
    if exists is None:
        # A genuinely new or pre-Alembic vault has no lineage to reject.
        return []
    try:
        result = _opened_execute(connection, "SELECT version_num FROM alembic_version")
        return [row[0] for row in result.fetchall() if row[0]]
    except Exception as exc:
        raise HubBootstrapError(
            f"Could not read the existing Alembic revision: {exc}"
        ) from exc


def prevalidate_opened_library(connection, library: Library) -> None:
    """Validate fingerprint and schema on the exact connection Alembic will use."""
    try:
        fingerprint_table = _opened_first(
            connection,
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='library_settings'",
        )
    except Exception as exc:
        raise HubBootstrapError(
            f"Could not inspect the registered library fingerprint schema at "
            f"{library.vault_path} before migration: {exc}"
        ) from exc
    if fingerprint_table is None:
        if library.vault_uuid is not None:
            raise HubBootstrapError(
                f"Could not verify the registered library fingerprint at "
                f"{library.vault_path} before migration: library_settings is missing."
            )
        row = None
    else:
        try:
            row = _opened_first(
                connection,
                "SELECT library_uuid FROM library_settings LIMIT 1",
            )
        except Exception as exc:
            raise HubBootstrapError(
                f"Could not verify the registered library fingerprint at "
                f"{library.vault_path} before migration: {exc}"
            ) from exc
    observed = None
    if isinstance(row, dict):
        observed = row.get("library_uuid")
    elif row:
        observed = row[0]
    expected = library.vault_uuid
    if expected is not None and observed != expected:
        raise HubBootstrapError(
            f"Library fingerprint conflict at {library.vault_path}: the vault "
            f"contains {observed or 'no fingerprint'}, but the hub expects "
            f"{expected}. The vault was not opened or migrated."
        )
    if expected is None and observed not in (None, library.uuid):
        raise HubBootstrapError(
            f"Library fingerprint conflict at {library.vault_path}: the vault "
            f"contains {observed}, but this registration expects {library.uuid}."
        )

    known = set()
    revision_pattern = re.compile(
        r'^revision(?:\s*:\s*[^=]+)?\s*=\s*["\']([^"\']+)', re.M
    )
    versions = Path(__file__).resolve().parent.parent / "migrations" / "versions"
    for path in versions.glob("*.py"):
        match = revision_pattern.search(path.read_text(encoding="utf-8"))
        if match:
            known.add(match.group(1))
    unknown = [rev for rev in _opened_revision_rows(connection) if rev not in known]
    if unknown:
        raise HubBootstrapError(
            "This library was last opened by a newer version of PixlStash "
            f"(database revision {', '.join(unknown)}). Update PixlStash before "
            "opening it. Nothing has been changed."
        )


def _write_identity_and_mark_copied(
    hub: HubDatabase,
    library: Library,
    user_row: sqlite3.Row,
    token_rows: list[sqlite3.Row],
) -> None:
    """Commit the complete copy and its durable state marker atomically."""
    available = set(user_row.keys())
    columns = [name for name in _USER_COLUMNS_TO_COPY if name in available]
    token_columns = (
        [name for name in _TOKEN_COLUMNS_TO_COPY if name in set(token_rows[0].keys())]
        if token_rows
        else []
    )
    with hub.transaction() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM user").fetchone()[0]
        if existing:
            raise HubBootstrapError(
                "Legacy identity migration is pending but the hub already has an owner. "
                "Refusing to guess which identity should win."
            )
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
                [user_id, library.uuid] + [token[name] for name in token_columns],
            )
        conn.execute(
            "UPDATE library SET identity_migration_state = 'copied' WHERE id = ?",
            (library.id,),
        )
        conn.execute(
            "UPDATE identity_migration_operation SET state='copied' WHERE library_uuid=?",
            (library.uuid,),
        )


def finalize_opened_library(result: HubBootstrap, vault_db=None) -> None:
    """Stamp/validate an opened vault, then finish legacy credential cleanup.

    Compatibility wrapper for CLI/tests. Server startup and switching use
    :func:`finalize_library_connection` on VaultDatabase's bound connection.
    """
    # This compatibility path owns an independent sqlite3 connection. Ensure a
    # supplied test/CLI VaultDatabase is not retaining pooled WAL connections
    # while the sanitizer switches the file to DELETE mode. Production uses
    # finalize_library_connection directly as VaultDatabase's migration hook.
    if vault_db is not None:
        vault_db.close()
    guard = TrustedSQLiteLocation.open(result.library.vault_path)
    try:
        conn = sqlite3.connect(guard.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            guard.verify_after_open()
            prevalidate_opened_library(conn, result.library)
            finalize_library_connection(result.hub, result.library, conn)
        finally:
            conn.close()
    finally:
        guard.close()
    result.library = (
        LibraryRegistry(result.hub).by_uuid(result.library.uuid) or result.library
    )


def _opened_identity_payload(connection) -> tuple[dict | None, list[dict], str]:
    if isinstance(connection, sqlite3.Connection):
        connection.row_factory = sqlite3.Row
    user = _opened_first(connection, "SELECT * FROM user LIMIT 1")
    if user is None:
        tokens = []
    else:
        user_id = user.get("id") if isinstance(user, dict) else user[0]
        result = _opened_execute(
            connection,
            "SELECT * FROM usertoken WHERE user_id = ? ORDER BY id",
            (user_id,),
        )
        if hasattr(result, "mappings"):
            tokens = [dict(row) for row in result.mappings().all()]
        else:
            tokens = [dict(row) for row in result.fetchall()]
    user_map = user if isinstance(user, dict) else (dict(user) if user else None)
    serializable = {
        "user": (
            {name: user_map[name] for name in _USER_COLUMNS_TO_COPY if name in user_map}
            if user_map is not None
            else None
        ),
        "tokens": [
            {name: row[name] for name in _TOKEN_COLUMNS_TO_COPY if name in row}
            for row in tokens
        ],
    }
    encoded = json.dumps(
        serializable, sort_keys=True, separators=(",", ":"), default=str
    )
    return user_map, tokens, hashlib.sha256(encoded.encode()).hexdigest()


def _stamp_opened_connection(connection, expected: str) -> str:
    row = _opened_first(
        connection, "SELECT id, library_uuid FROM library_settings LIMIT 1"
    )
    if row is None:
        _opened_execute(
            connection,
            "INSERT INTO library_settings (id, library_uuid) VALUES (1, ?)",
            (expected,),
        )
        return expected
    row_id = row.get("id") if isinstance(row, dict) else row[0]
    observed = row.get("library_uuid") if isinstance(row, dict) else row[1]
    if observed is None:
        _opened_execute(
            connection,
            "UPDATE library_settings SET library_uuid = ? WHERE id = ? "
            "AND library_uuid IS NULL",
            (expected, row_id),
        )
        return expected
    return observed


def finalize_library_connection(hub: HubDatabase, library: Library, connection) -> None:
    """Revalidate, stamp, and blank on the exact Alembic connection."""
    current = LibraryRegistry(hub).by_uuid(library.uuid)
    if current is None:
        raise HubBootstrapError("The opened library disappeared from the hub.")

    copied = current.identity_migration_state == "copied"
    if current.identity_migration_state == "pending":
        raise HubBootstrapError(
            "Legacy identity migration is still pending and must be copied before "
            "the vault can be opened."
        )
    needs_scrub = current.identity_migration_state != "complete"
    operation = None
    if copied:
        operation = hub.fetchone(
            "SELECT source_path, payload_digest, state "
            "FROM identity_migration_operation WHERE library_uuid=?",
            (current.uuid,),
        )
        if (
            operation is None
            or operation[2] != "copied"
            or operation[0] != current.path
        ):
            raise HubBootstrapError(
                "Copied legacy migration has no matching explicit source operation."
            )

    in_transaction = getattr(connection, "in_transaction", False)
    if callable(in_transaction) and in_transaction():
        connection.commit()

    def vault_changes() -> str:
        if copied:
            user, tokens, digest = _opened_identity_payload(connection)
            expected = current.vault_uuid or current.uuid
            fingerprint_row = _opened_first(
                connection,
                "SELECT library_uuid FROM library_settings LIMIT 1",
            )
            existing_fingerprint = (
                fingerprint_row.get("library_uuid")
                if isinstance(fingerprint_row, dict)
                else (fingerprint_row[0] if fingerprint_row else None)
            )
            already_blanked = (
                (
                    user is None
                    or (
                        user.get("username") is None
                        and user.get("password_hash") is None
                    )
                )
                and not tokens
                and existing_fingerprint == expected
            )
            if digest != operation[1] and not already_blanked:
                raise HubBootstrapError(
                    "Approved legacy identity changed after it was copied; the "
                    "vault was not stamped or blanked."
                )
        expected = current.vault_uuid or current.uuid
        observed = _stamp_opened_connection(connection, expected)
        if observed != expected:
            raise HubBootstrapError(
                f"Library fingerprint conflict at {current.vault_path}: the vault "
                f"contains {observed}, but the hub expects {expected}. Neither "
                "value was overwritten."
            )
        return observed

    if hasattr(connection, "begin") and not isinstance(connection, sqlite3.Connection):
        with connection.begin():
            observed = vault_changes()
    else:
        with connection:
            observed = vault_changes()

    # Alembic has completed and the fingerprint is durable. Every existing or
    # attached legacy library has its LIVE vault scrubbed exactly once here,
    # whether or not its owner identity was the explicitly approved one copied
    # into this hub. This part stays synchronous: it is the database about to be
    # served, it is one file, and it is fast.
    #
    # Historical snapshot archives are deliberately NOT scrubbed here. Rewriting
    # them is minutes of blocking work ahead of the listening socket (22
    # archives / 5.7 GB measured at 5 min 47 s), and serving does not depend on
    # it: every restore and preview path scrubs the scratch database it
    # materializes before anything opens it. MissingSnapshotIdentityScrubFinder
    # drains them in the background, one archive at a time, and create_backup
    # finishes any outstanding ones before it can package them.
    if needs_scrub:
        logger.info(
            "Library %s needs the one-time portable-identity migration. The "
            "vault is scrubbed now; its historical snapshot archives are "
            "scrubbed in the background afterwards and do not hold up startup.",
            current.name,
        )
        sanitize_vault_connection(connection, current.vault_path, restore_wal=True)

    with hub.transaction() as hub_conn:
        row = hub_conn.execute(
            "SELECT vault_uuid FROM library WHERE id = ?", (current.id,)
        ).fetchone()
        recorded = row[0] if row else None
        if recorded not in (None, observed):
            raise HubBootstrapError(
                f"Hub fingerprint conflict for library {current.uuid}: recorded "
                f"{recorded}, observed {observed}."
            )
        hub_conn.execute(
            "UPDATE library SET vault_uuid = ? WHERE id = ?",
            (observed, current.id),
        )
        if needs_scrub:
            hub_conn.execute(
                "UPDATE library SET identity_migration_state='complete' WHERE id=?",
                (current.id,),
            )
        if copied:
            hub_conn.execute(
                "UPDATE identity_migration_operation SET state='complete' "
                "WHERE library_uuid=?",
                (current.uuid,),
            )
