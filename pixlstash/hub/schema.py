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

import secrets
import sqlite3

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


# The schema version this build expects. A hub file carrying a *higher* version
# was written by a newer PixlStash: refuse it rather than migrate it downward
# (see :func:`apply_migrations`).
CURRENT_SCHEMA_VERSION = 2


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
# The five columns at the end were the §5 "library-scoped" candidates. Decided
# individually 2026-08-02, after establishing what each one actually is:
#
# * ``similarity_character`` MOVED to the vault's ``library_settings``. It is a
#   row id in one vault's character table, so a per-user copy silently names a
#   different person after a switch. The column stays here, unused and NULL,
#   only because the shared ``User`` model declares it.
# * ``hidden_tags``, ``apply_tag_filter``, ``smart_score_penalised_tags`` stay
#   here by decision: they name library vocabulary but are the owner's own
#   working preferences, and the same person wants the same defects penalised
#   and the same clutter hidden wherever they are. They are also personal
#   information, which is a second reason to keep them out of a folder designed
#   to be copied and shared (see ``settings_salt`` below).
# * ``stack_strictness`` remains a hub-scoped owner preference. The frontend
#   consumes it as the similarity threshold for stack ordering; it does not
#   identify a vault row and therefore remains meaningful across libraries.
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

# Mirrors the vault's ``UserToken`` plus ``library_uuid``.
#
# **Every token belongs to exactly one library** (decided 2026-08-01). An
# unpinned token would change what it grants the moment the owner switched
# library: a share link would start serving somebody else's pictures, and an
# automation holding an ALL token (the ComfyUI node, MCP, a script) would
# silently start writing into a different library. NOT NULL, so there is no
# "unpinned" state to interpret; routes that legitimately need no library are
# marked library-independent at the gate instead.
#
# Referenced by uuid, not by ``library.id``, and deliberately without
# ON DELETE CASCADE: detaching a library must not destroy its share links (see
# the note on _V1_LIBRARY). A token whose library is detached is inert, because
# nothing unregistered can be the active library, and it works again if that
# library is re-attached.
#
# Named ``usertoken``, not ``user_token``: SQLModel derives the table name from
# the class, so this is what ``UserToken`` maps to (see the note on _V1_USER).
_V1_USER_TOKEN = """
CREATE TABLE IF NOT EXISTS usertoken (
    id                  INTEGER PRIMARY KEY,
    public_id           TEXT UNIQUE,
    user_id             INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
    library_uuid        TEXT NOT NULL REFERENCES library(uuid),
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
    "CREATE INDEX IF NOT EXISTS ix_usertoken_library_uuid ON usertoken(library_uuid)",
    "CREATE INDEX IF NOT EXISTS ix_usertoken_token_hash ON usertoken(token_hash)",
    "CREATE INDEX IF NOT EXISTS ix_usertoken_token_prefix ON usertoken(token_prefix)",
)

# The library registry. ``path`` is the resolved (symlinks followed) absolute
# path of the library *folder*, not of its vault.db.
#
# ``uuid`` is the library's stable identity and the only value anything outside
# the hub may reference. The integer ``id`` must never be used for that: SQLite
# hands the lowest free ``INTEGER PRIMARY KEY`` to the next insert, so detaching
# a library and registering another gives the new one the old one's id, and any
# token, URL or open browser tab still holding it would then name a *different*
# library. That is the same hazard ``UserToken.public_id`` was introduced for
# (see ``pixlstash/db_models/user_token.py``), applied to a longer-lived object.
#
# Minted by the hub, never read from a vault: a library folder copied in from
# elsewhere must not be able to claim an identity that tokens on this machine
# are already stamped with.
#
# ``attached`` is what makes ``detach`` non-destructive. Detaching clears the
# flag instead of deleting the row, so the uuid and the tokens stamped with it
# survive and come back when the same folder is attached again.
# ``settings_salt`` keys the settings fingerprint the vault stores (see
# ``library_settings`` in the vault). The fingerprint answers "have the owner's
# score-affecting settings changed since this library was last opened?" without
# the library holding any of those settings. The salt is what makes that safe:
# penalised tags and hidden tags are personal information (they say what someone
# collects and what they hide), a tag vocabulary is small and guessable, so an
# unsalted hash of them sitting in a *portable* library folder would be
# recoverable by dictionary attack. Keyed by a per-library random value that
# never leaves the hub, the fingerprint is an opaque blob to anyone holding only
# the library.
#
# ``vault_uuid`` is the fingerprint observed inside the library itself (the
# vault's ``library_settings`` row). It is *not* an identity: it is never
# referenced by a token and never trusted for authorization, because a library
# folder can arrive from anyone. Its only job is to answer "is the folder now at
# this path the same library I registered here before?", which decides whether
# re-attaching revives the old row and its share links. NULL for a library that
# predates the fingerprint, which falls back to matching on path alone.
_V1_LIBRARY = """
CREATE TABLE IF NOT EXISTS library (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid           TEXT NOT NULL UNIQUE,
    vault_uuid     TEXT,
    name        TEXT NOT NULL,
    path        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    attached_at TEXT NOT NULL,
    detached_at TEXT,
    attached    INTEGER NOT NULL DEFAULT 1,
    is_active   INTEGER NOT NULL DEFAULT 0,
    notes       TEXT
)
"""

# Append-only ledger of every library uuid this hub has ever issued.
#
# **A library uuid is never reused** (decided 2026-08-01). Tokens are stamped
# with it, so a reissued uuid would silently hand a stale token access to a
# library it was never minted for — the recycled-identifier hazard the uuid
# exists to eliminate, reintroduced at a different layer. Uniqueness on
# ``library.uuid`` only constrains rows that currently exist; this ledger keeps
# the constraint after a row is gone, so no future verb (a ``forget``, a partial
# hub restore, a hand-edited registry) can re-issue one.
#
# The second guard is the foreign key: ``usertoken.library_uuid`` references
# ``library(uuid)`` with no ON DELETE action, so SQLite refuses to delete a
# library row while any token still points at it.
_V1_LIBRARY_UUID_LEDGER = """
CREATE TABLE IF NOT EXISTS library_uuid_issued (
    uuid       TEXT PRIMARY KEY,
    issued_at  TEXT NOT NULL,
    first_path TEXT
)
"""

# Three invariants enforced by the database rather than by application code, so
# a concurrent CLI and server cannot race their way past them:
#   * a path is registered at most once;
#   * a uuid is unique (declared inline above);
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
            _V1_LIBRARY_UUID_LEDGER,
            *_V1_LIBRARY_INDEXES,
            _V1_USER,
            _V1_USER_USERNAME_INDEX,
            _V1_USER_TOKEN,
            *_V1_USER_TOKEN_INDEXES,
        ),
    ),
    # Version 2 is applied by ``_apply_v2`` below. SQLite does not support
    # ``ADD COLUMN IF NOT EXISTS``; the explicit column inspection is what
    # makes this retryable for developers who opened an earlier lane commit
    # whose nominal v1 already contained ``settings_salt``.
    (2, ()),
)


def _apply_v2(conn: sqlite3.Connection) -> None:
    """Add v2 library bootstrap state without rewriting an existing hub."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(library)").fetchall()}
    if "settings_salt" not in columns:
        conn.execute("ALTER TABLE library ADD COLUMN settings_salt TEXT")
    if "identity_migration_state" not in columns:
        conn.execute(
            "ALTER TABLE library ADD COLUMN identity_migration_state TEXT "
            "NOT NULL DEFAULT 'not_required'"
        )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS identity_migration_operation ("
        "library_uuid TEXT PRIMARY KEY REFERENCES library(uuid), "
        "source_path TEXT NOT NULL, payload_digest TEXT NOT NULL, "
        "state TEXT NOT NULL CHECK(state IN ('pending','copied','complete')))"
    )
    # Rows from the earliest feature-lane v1 may predate the column entirely.
    # Backfill in Python so every library gets a distinct cryptographic value;
    # SQLite has no suitable random-hex default for ALTER TABLE.
    missing_salts = conn.execute(
        "SELECT id FROM library WHERE settings_salt IS NULL OR settings_salt = ''"
    ).fetchall()
    for (library_id,) in missing_salts:
        conn.execute(
            "UPDATE library SET settings_salt = ? WHERE id = ?",
            (secrets.token_hex(16), library_id),
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
                if target_version == 2:
                    _apply_v2(conn)
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

    # Version 2 is still unreleased and existed in earlier feature-lane builds.
    # Re-run its guarded shape reconciliation so those developer hubs receive
    # newly added v2 tables without pretending a released v3 exists.
    if version >= 2:
        with conn:
            _apply_v2(conn)

    return version
