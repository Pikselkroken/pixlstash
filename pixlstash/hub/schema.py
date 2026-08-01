"""Hub database schema and its sequential versioning.

The hub is the app-level database that owns identity (the user, password hash,
API/share tokens), per-user preferences, machine/deployment settings, and the
registry of known libraries. It lives outside any library, so a library folder
can be copied or moved without carrying credentials with it.

**Why this is not Alembic.** The vault has an Alembic lineage of its own, and a
second lineage in the same process is a standing source of "which head am I
on?" confusion — the two databases have unrelated lifetimes and are opened by
unrelated code paths (the server opens both; the CLI opens only this one). The
hub's schema is small and append-only, so a single ``schema_version`` row plus
an ordered list of migration steps is the whole mechanism. Each step is applied
exactly once, in order, inside one transaction per step.

**Why this is not SQLModel.** ``SQLModel.metadata`` is process-global, and the
vault's baseline migration calls ``metadata.create_all()``: declaring hub tables
as SQLModel tables would create them inside every vault. The hub therefore uses
stdlib :mod:`sqlite3` and explicit DDL. It also keeps the CLI light — importing
:mod:`pixlstash.database` pulls in numpy and the image stack, which a
``libraries list`` invocation has no use for.

See ``docs/backend_architecture.md`` §17 and the multi-library plan §4.
"""

from __future__ import annotations

import sqlite3

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


# The schema version this build expects. A hub file carrying a *higher* version
# was written by a newer PixlStash: refuse it rather than migrate it downward
# (see :func:`apply_migrations`).
CURRENT_SCHEMA_VERSION = 1


# Identity plus the per-user preference and machine/deployment columns that move
# out of the vault's ``user`` table (multi-library plan §5).
#
# **The table name and column set match the vault's ``User`` SQLModel exactly.**
# That is load-bearing, not cosmetic: :class:`pixlstash.hub.engine.HubEngine`
# hands :class:`~pixlstash.auth.AuthService` SQLModel sessions bound to this
# file, so identity moves to the hub by re-pointing one constructor argument
# rather than by rewriting 23 call sites against a second data access style.
# A column the model declares but this table lacks would fail every
# ``SELECT user.*``.
#
# The five library-scoped columns at the end (similarity_character,
# stack_strictness, smart_score_penalised_tags, hidden_tags, apply_tag_filter)
# belong to the vault's ``library_settings`` table by §5 and are *not* read from
# here. They exist only so the shared model maps cleanly, and they come out in a
# later hub schema version once ``library_settings`` is the live source.
_V1_USER = """
CREATE TABLE IF NOT EXISTS user (
    id                          INTEGER PRIMARY KEY,
    username                    TEXT,
    password_hash               TEXT,
    is_admin                    INTEGER NOT NULL DEFAULT 0,
    description                 TEXT,

    -- per-user view preferences
    theme_mode                  TEXT,
    date_format                 TEXT,
    sort                        TEXT,
    descending                  INTEGER,
    columns                     INTEGER,
    thumbnail_mode              TEXT,
    thumbnail_size_level        INTEGER,
    sidebar_thumbnail_size      INTEGER,
    sidebar_width               INTEGER,
    sidebar_docked              INTEGER,
    sidebar_pinned              INTEGER,
    compact_mode                INTEGER,
    show_stars                  INTEGER,
    show_face_bboxes            INTEGER,
    show_hand_bboxes            INTEGER,
    show_format                 INTEGER,
    show_resolution             INTEGER,
    show_problem_icon           INTEGER,
    show_stacks                 INTEGER,
    show_keyboard_hint          INTEGER,
    hide_purge_snapshot_warning INTEGER,

    -- machine / deployment settings (owner-level, not library-level)
    comfyui_url                 TEXT,
    public_url                  TEXT,
    keep_models_in_memory       INTEGER,
    max_vram_gb                 REAL,
    tagger_settings             TEXT,
    check_for_updates           INTEGER,
    embed_watermark             INTEGER,
    watermark_image             BLOB,

    -- Library-scoped by §5; present only so the shared ``User`` model maps.
    -- Never read from the hub; the vault's library_settings row owns them.
    similarity_character        INTEGER,
    stack_strictness            REAL,
    smart_score_penalised_tags  TEXT,
    hidden_tags                 TEXT,
    apply_tag_filter            INTEGER
)
"""

_V1_USER_USERNAME_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_user_username ON user(username)"
)

# Mirrors the vault's ``UserToken`` plus ``library_id``. NULL library_id means
# an owner/ALL-scope token, valid regardless of which library is active; a set
# library_id means a resource-scoped share link, whose resource ids only mean
# something inside that one library.
#
# Named ``usertoken``, not ``user_token``: SQLModel derives the table name from
# the class, so this is what ``UserToken`` maps to (see the note on _V1_USER).
_V1_USER_TOKEN = """
CREATE TABLE IF NOT EXISTS usertoken (
    id                  INTEGER PRIMARY KEY,
    public_id           TEXT UNIQUE,
    user_id             INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
    library_id          INTEGER REFERENCES library(id) ON DELETE CASCADE,
    token_hash          TEXT NOT NULL,
    token_prefix        TEXT,
    description         TEXT,
    scope               TEXT NOT NULL DEFAULT 'ALL',
    resource_type       TEXT,
    resource_id         INTEGER,
    created_at          TEXT NOT NULL,
    last_used_at        TEXT,
    expires_at          TEXT,
    include_attachments INTEGER NOT NULL DEFAULT 0,
    watermark           INTEGER NOT NULL DEFAULT 1
)
"""

_V1_USER_TOKEN_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_usertoken_user_id ON usertoken(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_usertoken_library_id ON usertoken(library_id)",
    "CREATE INDEX IF NOT EXISTS ix_usertoken_token_hash ON usertoken(token_hash)",
    "CREATE INDEX IF NOT EXISTS ix_usertoken_token_prefix ON usertoken(token_prefix)",
)

# The library registry. ``path`` is the resolved (symlinks followed) absolute
# path of the library *folder*, not of its vault.db.
_V1_LIBRARY = """
CREATE TABLE IF NOT EXISTS library (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    path        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    attached_at TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 0,
    notes       TEXT
)
"""

# Two invariants enforced by the database rather than by application code, so a
# concurrent CLI and server cannot race their way past them:
#   * a path is registered at most once;
#   * at most one library is active (a partial index over the active rows only).
_V1_LIBRARY_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_library_path ON library(path)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_library_single_active "
    "ON library(is_active) WHERE is_active = 1",
)


# Ordered schema steps. Append only: a released version's statement list is
# never edited, exactly as for an applied Alembic migration. ``library`` is
# created before ``user_token`` because the latter references it.
SCHEMA_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            _V1_LIBRARY,
            *_V1_LIBRARY_INDEXES,
            _V1_USER,
            _V1_USER_USERNAME_INDEX,
            _V1_USER_TOKEN,
            *_V1_USER_TOKEN_INDEXES,
        ),
    ),
)


class HubSchemaTooNewError(RuntimeError):
    """The hub file was written by a newer PixlStash than this build.

    Raised instead of migrating downward or opening it anyway: a newer hub may
    hold columns and rows this build would silently drop on write.
    """


def read_schema_version(conn: sqlite3.Connection) -> int:
    """Return the hub's schema version, or 0 for a hub that has no tables yet."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return int(row[0]) if row else 0


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Bring *conn* up to :data:`CURRENT_SCHEMA_VERSION` and return that version.

    Each step runs in its own transaction together with the ``schema_version``
    write, so an interrupted upgrade leaves the hub on the last fully-applied
    version rather than half-way through one.

    Raises:
        HubSchemaTooNewError: The file is newer than this build understands.
    """
    version = read_schema_version(conn)
    if version > CURRENT_SCHEMA_VERSION:
        raise HubSchemaTooNewError(
            f"Hub schema version {version} is newer than this PixlStash build "
            f"understands (version {CURRENT_SCHEMA_VERSION}). Upgrade PixlStash, "
            "or point it at a different hub file."
        )

    for target_version, statements in SCHEMA_MIGRATIONS:
        if target_version <= version:
            continue
        logger.info("Upgrading hub schema to version %d", target_version)
        try:
            with conn:
                for statement in statements:
                    conn.execute(statement)
                conn.execute("DELETE FROM schema_version")
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (target_version,),
                )
        except sqlite3.Error as exc:
            logger.error(
                "Hub schema upgrade to version %d failed, hub stays on version %d: %s",
                target_version,
                version,
                exc,
            )
            raise
        version = target_version

    return version
