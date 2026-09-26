"""Hub database schema and its sequential versioning.

The hub is the app-level database that owns identity (the user, password hash,
API/share tokens), per-user preferences, machine/deployment settings, and the
registry of known libraries. It lives outside any library, so a library folder
can be copied or moved without carrying credentials with it.

**Why this is not Alembic.** The vault has an Alembic lineage of its own, and a
second lineage in the same process is a standing source of "which head am I
on?" confusion - the two databases have unrelated lifetimes and are opened by
unrelated code paths (the server opens both; the CLI opens only this one). The
hub's schema is small and append-only, so a single ``schema_version`` row plus
an ordered list of migration steps is the whole mechanism. Each step is applied
exactly once, in order, inside one transaction per step.

**Why this is not SQLModel.** ``SQLModel.metadata`` is process-global, and the
vault's baseline migration calls ``metadata.create_all()``: declaring hub tables
as SQLModel tables would create them inside every vault. The hub therefore uses
stdlib :mod:`sqlite3` and explicit DDL. It also keeps the CLI light - importing
:mod:`pixlstash.database` pulls in numpy and the image stack, which a
``libraries list`` invocation has no use for.

See ``docs/backend_architecture.md`` §17 and the multi-library plan §4.
"""

from __future__ import annotations

import secrets
import sqlite3

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.adapter_header import (
    FILE_CHECKPOINT,
    FILE_UNKNOWN,
    role_from_folder,
)
from pixlstash.utils.known_base_models import SOURCE_DECLARED, identify

logger = get_logger(__name__)


# The schema version this build expects. A hub file carrying a *higher* version
# was written by a newer PixlStash: refuse it rather than migrate it downward
# (see :func:`apply_migrations`).
CURRENT_SCHEMA_VERSION = 2

# How many one-shot DATA backfills have been applied, kept in SQLite's own
# ``PRAGMA user_version`` slot.
#
# This is deliberately a second counter and not a schema version. The two answer
# different questions and must not share one number:
#
# * ``schema_version`` is the SHAPE, and its steps are re-runnable by design -
#   ``_apply_v2`` is re-applied on every open so a developer hub picks up newly
#   added v2 tables. A step that rewrites a user-correctable *value* cannot live
#   there: re-running it would undo the correction, silently, on every restart.
# * this counter is applied strictly once per hub, which is what a backfill over
#   owner-editable data needs.
#
# It also cannot be a third schema version. A build shipped before this change
# has ``CURRENT_SCHEMA_VERSION = 2`` and would refuse a v3 hub outright with
# ``HubSchemaTooNewError``, locking that user out of a downgrade - the same
# reasoning the model-shelf tables were amended into v2 for. ``user_version`` is
# free (nothing in PixlStash has ever written it), costs no DDL, and an older
# build ignores it entirely.
CURRENT_DATA_VERSION = 3

# `model_file.state` for a copy the last scan actually looked at, spelled out
# rather than imported from `services.model_folder_scanner`. That module imports
# `hub.db`, which imports this one, so the import would be a cycle. Kept beside
# the version counters so the duplication is visible rather than buried at its
# one use in `_backfill_component_roles`.
_BACKFILL_LIVE_STATE = "present"


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
    telemetry_send_install_id  INTEGER,
    telemetry_send_feature_usage INTEGER,
    telemetry_send_error_reports INTEGER,
    telemetry_send_hardware_profile INTEGER,
    telemetry_consent_prompted INTEGER,
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
# library it was never minted for - the recycled-identifier hazard the uuid
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


# ---------------------------------------------------------------------------
# Model shelf (v1.10.0). These live in the HUB, not in a vault, because what
# they record is a fact about this machine rather than about a library: a folder
# of LoRAs is on this disk, and re-registering the same folder in every library
# would be absurd. The only vault-side table is ``adapter_attachment``, which
# says "this library's character uses that adapter" and is keyed by sha256
# because no foreign key can span the two databases.
#
# Two tables carry the shelf: ``model`` is what a file IS (identity, curation,
# provenance) and ``model_file`` is WHERE a copy of it sits. That split is what
# makes one file in two folders one row with two locations, and what makes
# removing a folder a tombstone rather than a deletion.
#
# Hand-written DDL against stdlib sqlite3, like everything else here. The hub is
# deliberately not SQLModel: a SQLModel table would be created inside every
# vault as well.
# ---------------------------------------------------------------------------

# AUTOINCREMENT, not a bare INTEGER PRIMARY KEY, for the same reason
# ``library.id`` uses it: SQLite hands a deleted row's id to the next insert, and
# a recycled folder id would silently re-point every ``model_file`` row at a
# different folder.
_V2_MODEL_FOLDER = """
CREATE TABLE IF NOT EXISTS model_folder (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    path                 TEXT NOT NULL UNIQUE,
    kind                 TEXT NOT NULL,
    owner                TEXT,
    movable              TEXT NOT NULL,
    host_path            TEXT,
    delete_after_import  INTEGER,
    last_checked         TEXT,
    created_at           TEXT
)
"""

# One content table for every ``.safetensors`` on the shelf, adapter or
# checkpoint (integration plan §3, "File location needs its own row", ruled
# 2026-08-08: *the same split applies to checkpoint*). Two content tables would
# mean two location tables, or a location table with a discriminator column, and
# every consumer branching on which one to read - for rows that differ in three
# columns.
#
# `base_model` is free text on purpose. It comes from whatever the trainer wrote
# and an enum would reject every model that ships after this release.
_V2_MODEL = """
CREATE TABLE IF NOT EXISTS model (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    -- What the file IS, from the header: 'adapter' | 'checkpoint' | 'unknown'
    -- (pixlstash.utils.adapter_header.FILE_*). Owner-correctable; 'unknown'
    -- is a first-class value here and is never promoted to checkpoint.
    file_kind             TEXT NOT NULL,
    -- Which adapter algorithm. Meaningful only when file_kind = 'adapter'.
    kind                  TEXT,
    -- Interop identity (Civitai, {sha256}/file, the ComfyUI node). NULL only
    -- while a checkpoint waits for MissingCheckpointHashFinder; the CHECK below
    -- keeps today's NOT NULL guarantee exactly where it was load-bearing.
    sha256                TEXT UNIQUE,
    display_name          TEXT,
    filename              TEXT,
    base_model            TEXT,
    trigger_words         TEXT,
    provenance            TEXT NOT NULL,
    training_run_id       INTEGER,
    lineage_id            INTEGER,
    training_step         INTEGER,
    safetensors_metadata  TEXT,
    param_count           INTEGER,
    file_size             INTEGER,
    hashed_at             TEXT,
    stack_id              INTEGER REFERENCES adapter_stack(id),
    stack_position        INTEGER,
    run_key               TEXT,
    -- The authored mark, content-addressed at <hub_dir>/icons/<sha256>.webp.
    -- A HASH, never a vault picture id: `model` is a hub table, no foreign key
    -- spans hub and vault, and SQLite recycles deleted ids - so an
    -- `icon_picture_id` would silently re-point at a different picture after a
    -- delete-plus-insert and break on every library switch. Picking a library
    -- picture therefore COPIES it into the icon store rather than referencing
    -- it. NULL means no icon, which the client draws as a generated mark rather
    -- than as a blank.
    icon_sha256           TEXT,
    created_at            TEXT,
    -- Three facts read off the safetensors header at scan
    -- (pixlstash.utils.adapter_header), none of them a group and none
    -- owner-editable. `family` is the architecture the tensors show for a
    -- support file (vae_16ch, clip_l, t5_xxl); a row's base_model, when it
    -- folds, is the family the shelf serves instead, computed on the way out
    -- so a corrected base model is never contradicted by a stale column.
    -- `quant` is the precision the file was stored at: the dtype holding
    -- most of the parameters (or 'mixed'), and for a file with no readable
    -- header the postfix in its own name. Stored in whichever source's own
    -- spelling; ModelResponse folds the two (`canonical_quant`) on the way
    -- out, so a row written before either source existed still reads right.
    -- `weights_id` hashes tensor names and shapes without dtypes: two clean
    -- casts of one model share it, a repack that adds scale tensors does not.
    -- NULL until a scan has read the header; the scanner re-reads it for rows
    -- registered before these columns existed.
    family                TEXT,
    quant                 TEXT,
    weights_id            TEXT,
    -- The known base model (pixlstash.utils.known_base_models) this row was
    -- identified as, and which evidence said so: 'user' | 'declared' |
    -- 'filename' | 'declared_fuzzy' | 'filename_fuzzy'. Unlike `family` this
    -- IS stored, because the shelf sorts and filters on it in SQL, so the
    -- staleness is closed at the write sites instead: a curated base_model
    -- recomputes both columns in the same UPDATE (source 'user'), and the
    -- scanner only replaces a source it outranks. `base_model` stays the
    -- trainer's own string. NULL on both means nothing matched yet, which the
    -- scanner takes as a reason to look again.
    base_model_canonical  TEXT,
    base_model_source     TEXT,
    CHECK (file_kind <> 'adapter' OR sha256 IS NOT NULL),
    -- Same shape one column over: every producer already supplies an algorithm
    -- for an adapter ('unknown' is a first-class value, never NULL), so an
    -- adapter with no kind at all is a state the code cannot reach.
    CHECK (file_kind <> 'adapter' OR kind IS NOT NULL)
)
"""

# One model, many paths. That is what a duplicate after an interrupted move is,
# and what the same file copied into two registered folders is - for a 24 GB
# checkpoint exactly as much as for an adapter.
#
# This table is also the tombstone. Removing a folder drops its ``model_file``
# rows and KEEPS the ``model`` row with its name, triggers and attachments, so
# re-adding the folder re-links with the user's curation intact. That is what
# lets folder removal skip a confirmation prompt: nothing a user typed is
# destroyed by it.
_V2_MODEL_FILE = """
CREATE TABLE IF NOT EXISTS model_file (
    -- Integer, not sha256: this link does not cross a database. The precedent
    -- is model_folder_id on the next line, which is already an integer FK, and
    -- model.id is AUTOINCREMENT so a deleted id is never reissued. A sha256 key
    -- could not name a checkpoint at all until something had read 24 GB of it.
    model_id         INTEGER NOT NULL REFERENCES model(id),
    model_folder_id  INTEGER NOT NULL REFERENCES model_folder(id),
    relpath          TEXT NOT NULL,
    -- 'present' | 'missing' | 'unreachable' | 'not_downloaded' | 'removed'. The
    -- first three are the scanner's (see model_folder_scanner); 'not_downloaded'
    -- belongs to the roots PixlStash declares rather than scans, where an absent
    -- file is one we have not fetched yet and never one that wandered off; and
    -- 'removed' is a copy the owner deleted to keep one of several (#1439,
    -- POST /model-files/merge): there the ROW is the point of the delete rather
    -- than a leftover - it is what keeps a recipe's filename resolving to the
    -- model after the file is gone - so every sweep that writes this column
    -- skips it, or the next walk of the folder would re-label it 'missing' and
    -- throw the distinction away. This column has never carried a CHECK, so the
    -- value needed no migration; the list above is the whole vocabulary.
    state            TEXT NOT NULL,
    seen_at          TEXT,
    -- st_mtime_ns of this copy at the last scan. Paired with model.file_size it
    -- is what lets a sweep skip re-hashing 1,800 unchanged adapters, without
    -- the size-only blind spot where a same-size in-place edit leaves
    -- model.sha256 naming bytes that are no longer there. Per-location, not
    -- per-model: two copies of one file have two mtimes.
    file_mtime       INTEGER,
    PRIMARY KEY (model_folder_id, relpath)
)
"""

# One model, many capabilities - the same idiom as ``model_file`` one table up,
# and for the same reason. A model that serves several features genuinely cannot
# be filed under one heading: Florence-2 both captions and detects, and the CLIP
# the embedder loads is both the search encoder and the aesthetic scorer's
# backbone. The shelf lists such a model under *each* feature it serves, which
# needs somewhere to hold the set.
#
# ``model.kind`` is not that place and is left alone: it is the adapter
# algorithm, it carries a CHECK that says so, and it holds the *primary* label
# for a declared engine so the Kind column and every existing reader keep
# working. This table is additive - a row with no capabilities declared simply
# has none, which is what every scanned adapter and checkpoint is.
#
# No index on ``capability``: the shelf facets and filters client-side over the
# rows it already fetched, so nothing queries "which models can X" in SQL.
_V2_MODEL_CAPABILITY = """
CREATE TABLE IF NOT EXISTS model_capability (
    model_id    INTEGER NOT NULL REFERENCES model(id),
    capability  TEXT NOT NULL,
    PRIMARY KEY (model_id, capability)
)
"""

# One subject, many runs, many steps per run. Mirrors PictureStack exactly
# (id, name, created_at, updated_at) so the shelf can reuse the picture-stack
# presentation rather than invent a second stacking idiom.
_V2_ADAPTER_STACK = """
CREATE TABLE IF NOT EXISTS adapter_stack (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    created_at  TEXT,
    updated_at  TEXT
)
"""

# A hand-made workflow set (#1520): shelf models the owner says work together.
# A menu, not a recipe - no order and no strengths, which is what separates it
# from `adapter_stack` above and from the recipe evidence in the workflow tables.
# In the hub for the shelf's own reason: which files go together is a fact about
# this machine's models, not about a library. AUTOINCREMENT so a deleted set's id
# is never reissued to a client still holding it for an undo.
_V2_MODEL_WORKFLOW_SET = """
CREATE TABLE IF NOT EXISTS model_workflow_set (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
)
"""

# Members are keyed by sha256, NOT by `model.id`, and carry no foreign key to
# `model`: forgetting or deleting a file drops its model row, and the set must
# keep the member (drawn as not on the shelf) so it reconnects when a file with
# the same bytes comes back. `label` is the name at add time, the only name such
# a member still has. A checkpoint still hashing has no sha256, so it cannot
# be a member until it has one.
_V2_MODEL_WORKFLOW_SET_MEMBER = """
CREATE TABLE IF NOT EXISTS model_workflow_set_member (
    set_id    INTEGER NOT NULL REFERENCES model_workflow_set(id),
    sha256    TEXT NOT NULL,
    slot      TEXT NOT NULL
              CHECK (slot IN ('checkpoint', 'text_encoder', 'vae', 'lora', 'other')),
    label     TEXT,
    added_at  TEXT NOT NULL,
    PRIMARY KEY (set_id, sha256)
)
"""

_V2_MODEL_SHELF_INDEXES = (
    # The scanner's hot path: "which files does this model have, and where".
    "CREATE INDEX IF NOT EXISTS ix_model_file_model ON model_file(model_id)",
    # Folder removal and rescan both work folder-at-a-time.
    "CREATE INDEX IF NOT EXISTS ix_model_file_folder ON model_file(model_folder_id)",
    # Expanding one stack in the shelf, in cover-first order.
    "CREATE INDEX IF NOT EXISTS ix_model_stack_member "
    "ON model(stack_id, stack_position)",
    # The hash finder's whole query, as a partial index. Mirrors 0095 in the
    # vault: the queue is a handful of rows in a table of thousands, so a full
    # index on sha256 would be almost entirely rows the finder never wants.
    "CREATE INDEX IF NOT EXISTS ix_model_hash_queue ON model(id) WHERE sha256 IS NULL",
    # At most one checkpoint per hand-made set, held by the database so two
    # concurrent adds cannot both land one.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_model_workflow_set_checkpoint "
    "ON model_workflow_set_member(set_id) WHERE slot = 'checkpoint'",
    # "Which sets hold this file" - the clone dialog's grouped proposals.
    "CREATE INDEX IF NOT EXISTS ix_model_workflow_set_member_sha "
    "ON model_workflow_set_member(sha256)",
)

_V2_MODEL_SHELF_TABLES = (
    # adapter_stack first: `model.stack_id` references it.
    _V2_ADAPTER_STACK,
    _V2_MODEL_FOLDER,
    _V2_MODEL,
    _V2_MODEL_FILE,
    # After `model`: it references `model(id)`.
    _V2_MODEL_CAPABILITY,
    _V2_MODEL_WORKFLOW_SET,
    # After the set: it references `model_workflow_set(id)`.
    _V2_MODEL_WORKFLOW_SET_MEMBER,
    *_V2_MODEL_SHELF_INDEXES,
)

# The pre-reshape shelf shape. ``CREATE TABLE IF NOT EXISTS`` silently skips a
# *reshape*, so a developer hub opened on an earlier develop would keep the old
# three tables and gain the new two, with the scanner writing to neither.
_V2_SUPERSEDED_SHELF_TABLES = ("adapter_file", "adapter", "checkpoint")


# ---------------------------------------------------------------------------
# The workflow library (v1.11, workflow library plan §4)
# ---------------------------------------------------------------------------
#
# **These live in the hub and not in a vault, and that is the irreversible
# decision in the release.** 70.2% of the owner's structural recipes appear in
# more than one of his libraries, so a per-vault store would hold the same
# workflow three times and could never answer "have I built this before" -
# which is the question the feature exists to answer. The backfill that fills
# these tables is a one-time pass over every picture in every library: writing
# the rows into a vault and moving them later means re-running it, and
# re-running it after pictures have been deleted cannot recover the rows for
# those pictures at all.
#
# **Identity is the content, so nothing crosses the database boundary.** A
# vault's generation refers to a recipe by its structural hash. That is not a
# foreign key, needs no coordinated migration, and still resolves after a
# library has been detached and reattached somewhere else.
#
# Two tiers, one row each, from ``services/workflow_hash.py``:
#
# * ``workflow_topology`` - the graph alone, node classes and named-input
#   edges. The only tier computable from *either* ComfyUI serialisation, which
#   is what lets a dropped ``workflow.json`` be filed with ComfyUI stopped.
# * ``workflow_recipe`` - that graph bound to specific models. Parameters and
#   seeds are nulled before hashing, so a recipe is **prompt-free by
#   construction** (library plan §5) and needs no purge to stay that way.
#
# ``workflow_recipe_graph`` holds the graph itself, in exactly that nulled form.
#
# ``workflow_recipe_instance`` is the third tier, and the first that holds what a
# person wrote. The vault half of a picture's recipe, its seed and its inputs, is
# ``generation`` / ``generation_input`` (``db_models/generation.py``).

_V2_WORKFLOW_TOPOLOGY = """
CREATE TABLE IF NOT EXISTS workflow_topology (
    -- Content address, so this is the primary key. No surrogate id: an integer
    -- would have to be resolved across the hub/vault boundary, which is the
    -- exact thing §4 chose content addressing to avoid.
    topology_hash  TEXT PRIMARY KEY,
    -- Which rule produced the hash. A later rule is a new value here rather
    -- than a silent reinterpretation of rows written under the old one.
    hash_version   TEXT NOT NULL,
    node_count     INTEGER NOT NULL,
    first_seen_at  TEXT NOT NULL
)
"""

_V2_WORKFLOW_RECIPE = """
CREATE TABLE IF NOT EXISTS workflow_recipe (
    structural_hash  TEXT PRIMARY KEY,
    -- Within one database, so an ordinary foreign key. Every recipe has
    -- exactly one topology: the coarser key is computed from the same graph.
    topology_hash    TEXT NOT NULL REFERENCES workflow_topology(topology_hash),
    hash_version     TEXT NOT NULL,
    node_count       INTEGER NOT NULL,
    first_seen_at    TEXT NOT NULL
)
"""

# The graph itself, split off the recipe row because it is the only large
# column here and the library view never lists it.
#
# **Named for what it holds.** This is the RECIPE's graph, not the file that
# was imported: parameter and volatile widget values are already nulled and any
# field named like a credential is dropped. That is what makes library plan §5's
# deletion boundary real - "forget the pictures" purges instances and ghosts and
# leaves the recipe standing, with no purge having to rewrite a stored graph -
# and it is also why this row cannot be handed back to ComfyUI as a runnable
# workflow. The verbatim import store (implementation plan §B5) is a different
# thing that belongs beside the workflow file, and the name `workflow_document`
# is deliberately left free for it.
#
# One row per recipe. The same workflow rebuilt from scratch has different node
# ids and so different document TEXT at the same identity, so a key on the
# document's own digest would let those accumulate; `document_sha256` is kept as
# a plain column because it is what a content-addressed export filename is made
# of (library plan §9.2).
_V2_WORKFLOW_RECIPE_GRAPH = """
CREATE TABLE IF NOT EXISTS workflow_recipe_graph (
    structural_hash  TEXT PRIMARY KEY REFERENCES workflow_recipe(structural_hash),
    document_sha256  TEXT NOT NULL,
    document         TEXT NOT NULL,
    created_at       TEXT NOT NULL
)
"""

# The readable half of a recipe's assets, split out of the document so it can
# be DESTROYED without touching the document.
#
# ``workflow_recipe_graph.document`` names every asset by an opaque
# ``asset_reference`` (``services/workflow_hash.py``), so this table is the only
# place a model filename is legible. That is what makes "forget this model's
# name" a row delete rather than a rewrite of every stored graph plus a
# ``document_sha256`` migration -- and a model filename is worth being able to
# destroy, because on a real shelf a character LoRA is named after its subject.
#
# A recipe can name the same widget twice with different files (two
# ``LoraLoader`` nodes, two ``lora_name`` values), so the key is the triple, not
# the pair. ``normalized_filename`` is exactly rule 5's form -- lowercase
# basename, extension kept -- which is also what the recipe hashed, so a lookup
# from a shelf entry needs no second normalisation.
#
# Deleting a row leaves the document intact and its reference unresolvable,
# which is the intended end state: the graph still says "a model went here",
# and no longer says which.
_V2_WORKFLOW_RECIPE_ASSET = """
CREATE TABLE IF NOT EXISTS workflow_recipe_asset (
    structural_hash     TEXT NOT NULL REFERENCES workflow_recipe(structural_hash),
    widget_name         TEXT NOT NULL,
    normalized_filename TEXT NOT NULL,
    PRIMARY KEY (structural_hash, widget_name, normalized_filename)
)
"""

# A recipe with one set of parameters: the prompt, the steps, the strengths,
# everything but the seed (``services/workflow_hash.py``, the instance tier).
#
# **Keyed by library, for the ghost table's reason, and unlike the recipe.** A
# recipe is prompt-free and safe to share; an instance is the prompt. What may be
# kept of it is the retention setting's call, made per library against that
# library's own pictures, so a row lives while a picture (Scrapheap included) or
# a ghost IN THAT LIBRARY carries its hash, and the covered-ghost cascade
# destroys it when neither does. A hub-global row could not be judged by any one vault.
#
# ``document`` is the instance as a graph: the recipe's document with each
# parameter's value where the recipe has a null. Model and image filenames are
# references, nested ones included, so no readable model name is kept here. ``structural_hash`` is a
# plain column, not a foreign key, so deleting a workflow is never blocked by
# the instances that ran it.
_V2_WORKFLOW_RECIPE_INSTANCE = """
CREATE TABLE IF NOT EXISTS workflow_recipe_instance (
    library_uuid     TEXT NOT NULL,
    instance_hash    TEXT NOT NULL,
    structural_hash  TEXT NOT NULL,
    hash_version     TEXT NOT NULL,
    document         TEXT NOT NULL,
    first_seen_at    TEXT NOT NULL,
    PRIMARY KEY (library_uuid, instance_hash)
)
"""

# A **picture ghost**: the thumbnail and the prompt of a picture that has been
# permanently destroyed, kept because something else finds it useful (library
# plan §5, implementation plan §B4).
#
# **A ghost is the thumbnail AND the prompt, never one without the other.** The
# two are equally sensitive, they are created together, retained together and
# destroyed together, and this table is shaped so no later optimisation can keep
# "just the prompt" on the grounds that it is only text.
#
# It lives in the hub because that is where a workflow outlives its pictures,
# and within a library it is keyed on ``pixel_sha``, the one identifier of a
# destroyed picture that survives the row: the vault id is reused by SQLite the
# moment the next import lands, and the path is not stable across libraries.
#
# **But the key is the PAIR, and that is not a detail.** Unlike every other
# table here a ghost is not content-addressed identity, it is retained content,
# and whether it may be retained is a fact about ONE library: the cover that
# justifies it is a picture in that library's vault, and only one vault is live
# at a time so no purge can see any other's. A hub-global key would let library
# A's purge cascade away a ghost library B still has cover for — silent loss in
# the other direction from the one this feature is careful about. So every ghost
# names its library and every query below is scoped to one.
#
# ``instance_hash`` is the column the **covered-ghost cascade** turns on. At the
# default retention setting a ghost is kept only while a SURVIVING picture
# carries the same value, so destroying the last such picture un-covers its
# dependants and the purge must destroy them in the same pass
# (``services/workflow_ghost_service.py``). Indexed with the library for exactly
# that lookup.
#
# ``structural_hash`` is a plain column and NOT a foreign key to
# ``workflow_recipe``: a ghost can outlive a recipe that was never filed (the
# hub was unwritable when its picture was ingested, or the graph was refused by
# the hash layer), and a reference that could abort the write would trade a
# privacy record for referential tidiness.
_V2_WORKFLOW_PICTURE_GHOST = """
CREATE TABLE IF NOT EXISTS workflow_picture_ghost (
    library_uuid     TEXT NOT NULL,
    pixel_sha        TEXT NOT NULL,
    instance_hash    TEXT NOT NULL,
    structural_hash  TEXT,
    -- Nullable ON PURPOSE, and it is the one asymmetry here worth explaining.
    -- The rule §5 states is that a ghost never keeps the PROMPT ALONE, because
    -- the prompt is the sensitive half and "it is only text" is the argument a
    -- later optimisation would use to keep it. It is not a rule that a prompt
    -- must exist: an upscale or img2img graph has no CLIPTextEncode at all, and
    -- it still hashes, still has a recipe and a seed, and is still worth being
    -- able to make again. NULL here means the picture never had a prompt, never
    -- that one was dropped -- nothing between the vault column and this row can
    -- lose it.
    positive_prompt  TEXT,
    seed             INTEGER,
    -- NOT NULL, because this is the half the rule is actually about. A row with
    -- no thumbnail would be a retained prompt on its own, which is the exact
    -- artefact §5 forbids, and it would also destroy the argument that makes
    -- `covered` a safe default -- that a covered ghost's only marginal exposure
    -- is a thumbnail of a near-duplicate. The writer refuses to build one; this
    -- is the same refusal at the storage layer, so a future caller cannot.
    --
    -- Tightened in place rather than by a rebuild, because `CREATE TABLE IF NOT
    -- EXISTS` cannot add a constraint to a table that already exists and this
    -- one exists nowhere: it was introduced on this same unmerged branch, so
    -- every install creates it with the constraint on its first open. A
    -- developer who ran an earlier commit of the branch keeps the nullable
    -- shape until the hub is recreated, and the writer's own refusal covers
    -- them. Once this is released the rule from CLAUDE.md applies and any
    -- further tightening is a rebuild, as `_rebuild_model_with_kind_check` is.
    thumbnail        BLOB NOT NULL,
    created_at       TEXT NOT NULL,
    PRIMARY KEY (library_uuid, pixel_sha)
)
"""

# How each picture input of a runnable workflow file is filled (implementation
# plan §F3): by the grid's selection, by a picker at run time, or by one picture
# fixed here. Stored BESIDE the workflow and keyed by node id, never written
# into the file (§1 rule 1).
#
# Keyed by library as well as by file, for the ghost table's reason: a Fixed
# picture is a picture in ONE vault, and a vault id means nothing in another.
# It is named by ``pixel_sha`` rather than by id because SQLite reuses a vault
# id the moment the next import lands. The modes travel with the library too,
# which costs a second library its own setup and never hands it a picture it
# does not hold.
#
# No row for a workflow means "never configured" and the defaults apply. Rows
# for nodes the file no longer has are ignored rather than pruned, so replacing
# a file with a version that still has the node keeps its setting.
#
# A Fixed row always has a picture. Any other row MAY keep the one it had, so
# stepping an input off Fixed (one arrow key) and back does not lose it.
_V2_WORKFLOW_PICTURE_INPUT_CHECK = "CHECK (mode <> 'fixed' OR pixel_sha IS NOT NULL)"
_V2_WORKFLOW_PICTURE_INPUT = """
CREATE TABLE IF NOT EXISTS workflow_picture_input (
    library_uuid   TEXT NOT NULL,
    workflow_name  TEXT NOT NULL,
    node_id        TEXT NOT NULL,
    mode           TEXT NOT NULL CHECK (mode IN ('selection', 'picker', 'fixed')),
    pixel_sha      TEXT,
    CHECK (mode <> 'fixed' OR pixel_sha IS NOT NULL),
    PRIMARY KEY (library_uuid, workflow_name, node_id)
)
"""

# Which of a workflow file's parameters its form shows before "All N
# parameters" (#1306), in order. One row per FILE, not per library: a pin is a
# choice about the workflow, and it names node ids and input names, never a
# picture. No row means "never pinned" and the defaults apply; a row holding
# ``[]`` is somebody who unpinned everything, which is not the same thing.
_V2_WORKFLOW_PARAMETER_PINS = """
CREATE TABLE IF NOT EXISTS workflow_parameter_pins (
    workflow_name  TEXT PRIMARY KEY,
    pins           TEXT NOT NULL
)
"""

# --------------------------------------------------------------------------
# v1.12 Workflows & Recipes, step B2: the tables that key a WORKFLOW CARD.
#
# **Glossary, because "recipe" means two things.** The tables above call the
# graph-bound-to-models tier ``workflow_recipe`` / ``structural_hash``; new code
# calls that a **variant**. A **saved recipe** is the look a person keeps. The
# tables above are append-only and keep their names, so the mapping from a
# variant to the card it belongs to is a new table rather than a column added to
# one of them.
#
# **Eight of these tables are created before anything writes them**, and that is
# deliberate rather than speculative: they are the owner's own decisions about a
# card (name, notes, pins, overrides, cover, stacks), the steps that write them
# are the rest of this release, and a hub table is append-only - adding them
# with their writers means a second guarded amendment of v2 per step for rows
# whose shape is already decided here.
#
# A card is ``workflow_key`` (``services/workflow_identity.py``): the topology,
# the non-LoRA models, and the LoRA slots marked *structural*. Everything the
# owner can say about a workflow - its name, its pins, its overrides, which
# stack it sits in - is keyed by that, so a new character LoRA does not hand
# them a fresh, empty card.
# --------------------------------------------------------------------------

# Which card each stored variant belongs to. The backfill's output, and the one
# table that has to be rewritten when a mark is flipped or a key rule changes -
# hence ``key_version``, which is what makes a stale row findable rather than
# silently wrong.
#
# **No timestamp column on any card table.** Deriving the same hub twice has to
# write byte-identical rows, or "the backfill runs twice with identical rows" is
# a claim no test can make; a wall-clock column would churn every row on every
# re-derivation and hide a real difference in the noise. Nothing reads "when was
# this derived" - and a step that wants it can add the column then.
_V2_WORKFLOW_VARIANT = """
CREATE TABLE IF NOT EXISTS workflow_variant (
    structural_hash  TEXT PRIMARY KEY REFERENCES workflow_recipe(structural_hash),
    topology_hash    TEXT NOT NULL,
    workflow_key     TEXT NOT NULL,
    key_version      TEXT NOT NULL
)
"""

# Everything derivable from a topology alone, cached so the grid does not
# re-reduce a document per card: the automatic stack key, the workflow type, and
# the slot list every mark and override addresses.
#
# Per TOPOLOGY and not per variant, because a slot label is a Weisfeiler-Leman
# label over the graph: it means the same thing for every variant of one
# topology and nothing at all outside it. ``core_version`` stamps the rule that
# produced ``core_hash`` so a changed rule re-groups visibly instead of mixing
# two rules' stacks.
#
# ``slots`` is JSON: ``[{"label", "class_type", "widget", "is_lora"}, ...]``.
# **No filename and no asset reference.** A model's readable name lives in
# ``workflow_recipe_asset`` and nowhere else, so forgetting it stays one delete.
#
# ``specials`` is the post-processing this topology carries
# (``workflow_identity.SPECIAL_GROUPS``), comma-joined. **NULL and the empty
# string are different answers**: NULL is "the pass has not reached this
# topology", the empty string is "it has, and the graph has none". A card's
# generated name says "+ FaceDetailer" only on the second, so a topology the
# backfill has not reached is not quietly described as plain.
_V2_WORKFLOW_TOPOLOGY_CORE = """
CREATE TABLE IF NOT EXISTS workflow_topology_core (
    topology_hash  TEXT PRIMARY KEY REFERENCES workflow_topology(topology_hash),
    core_hash      TEXT NOT NULL,
    core_version   TEXT NOT NULL,
    workflow_type  TEXT,
    slots          TEXT NOT NULL,
    specials       TEXT
)
"""

# Whether a LoRA slot is part of the workflow (``structural``) or part of the
# look (``recipe``).
#
# **Frozen the first time the slot is seen and never recomputed.**
# ``workflow_identity.guess_mark`` reads a filename, so a later re-guess over a
# differently-named LoRA in the same slot would silently re-key every card that
# slot is in - cards the owner has by then named, pinned and stacked. The guess
# is where a mark starts, not what it is.
#
# **Two consequences worth stating rather than discovering.** A card key is then
# a function of what arrived first, so two machines that imported the same
# pictures in a different order can put one variant on different cards; and a
# mark outlives ``forget_asset_names``, which deletes the readable filename but
# cannot delete a decision that keys the card. ``structural`` therefore still
# says the forgotten file looked like a speed LoRA. That is a classification of
# a name, not the name, and destroying it would silently re-key the card - but
# it is a residue, and the owner-facing way to correct a wrong mark is a flip
# (a later step), never a delete here.
_V2_WORKFLOW_SLOT_MARK = """
CREATE TABLE IF NOT EXISTS workflow_slot_mark (
    topology_hash  TEXT NOT NULL,
    slot_label     TEXT NOT NULL,
    mark           TEXT NOT NULL CHECK (mark IN ('structural', 'recipe')),
    PRIMARY KEY (topology_hash, slot_label)
)
"""

# A workflow FILE - imported, or dropped in the watched folder - on the card its
# pictures already made. ``structural_hash`` is NULL for a UI-format file: it
# names its widget values by position, so it has a topology and no models, and
# it becomes a card with no assets rather than no card at all.
_V2_WORKFLOW_FILE = """
CREATE TABLE IF NOT EXISTS workflow_file (
    workflow_name    TEXT PRIMARY KEY,
    topology_hash    TEXT NOT NULL,
    structural_hash  TEXT,
    workflow_key     TEXT NOT NULL
)
"""

# What the owner says about a card. No row is the default: no name of their
# own, no notes, not hidden.
_V2_WORKFLOW_ATTR = """
CREATE TABLE IF NOT EXISTS workflow_attr (
    workflow_key  TEXT PRIMARY KEY,
    name          TEXT,
    notes         TEXT,
    hidden        INTEGER NOT NULL DEFAULT 0
)
"""

# A parameter the card starts from, and the pins its form shows first.
#
# **Addressed by (slot label, input name), never by node id.** A node id is
# whatever the file that was serialised last happened to call it; the same
# workflow rebuilt from scratch renumbers every one of them, and an override
# keyed that way would follow the numbering rather than the slot.
_V2_WORKFLOW_DEFAULT_OVERRIDE = """
CREATE TABLE IF NOT EXISTS workflow_default_override (
    workflow_key  TEXT NOT NULL,
    slot_label    TEXT NOT NULL,
    input_name    TEXT NOT NULL,
    value         TEXT NOT NULL,
    PRIMARY KEY (workflow_key, slot_label, input_name)
)
"""

# Key-based pins, the card's answer to the file-keyed ``workflow_parameter_pins``
# above. A row holding ``[]`` is somebody who unpinned everything, which is not
# the same as never having pinned.
_V2_WORKFLOW_KEY_PINS = """
CREATE TABLE IF NOT EXISTS workflow_key_pins (
    workflow_key  TEXT PRIMARY KEY,
    pins          TEXT NOT NULL
)
"""

# How each picture input of a card is filled, and which picture covers it.
#
# Keyed by ``(library_uuid, workflow_key)`` for the reason the ghost table is
# keyed by library: a picture is a picture in ONE vault, and it is named by
# ``pixel_sha`` because SQLite reuses a vault id the moment the next import
# lands. A second library gets its own setup and is never handed a picture it
# does not hold.
_V2_WORKFLOW_KEY_PICTURE_INPUT = """
CREATE TABLE IF NOT EXISTS workflow_key_picture_input (
    library_uuid  TEXT NOT NULL,
    workflow_key  TEXT NOT NULL,
    slot_label    TEXT NOT NULL,
    input_name    TEXT NOT NULL,
    mode          TEXT NOT NULL CHECK (mode IN ('selection', 'picker', 'fixed')),
    pixel_sha     TEXT,
    CHECK (mode <> 'fixed' OR pixel_sha IS NOT NULL),
    PRIMARY KEY (library_uuid, workflow_key, slot_label, input_name)
)
"""

_V2_WORKFLOW_COVER = """
CREATE TABLE IF NOT EXISTS workflow_cover (
    library_uuid  TEXT NOT NULL,
    workflow_key  TEXT NOT NULL,
    pixel_sha     TEXT NOT NULL,
    PRIMARY KEY (library_uuid, workflow_key)
)
"""

# Stacks of cards. An ``auto`` stack is the automatic grouping by ``core_hash``
# (cards that differ only in plumbing, post-processing or a checkpoint); a
# ``manual`` one is the owner's own and names no core hash. ``position`` 0 is
# the cover.
#
# ``workflow_unstacked`` is the owner taking a card out of its automatic stack.
# A row here is a decision, so it survives a ``CORE_VERSION`` bump regrouping
# everything around it - which is also why stack membership is keyed on the card
# and not on the core hash.
_V2_WORKFLOW_STACK = """
CREATE TABLE IF NOT EXISTS workflow_stack (
    stack_id   TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('manual', 'auto')),
    core_hash  TEXT,
    CHECK (kind <> 'auto' OR core_hash IS NOT NULL)
)
"""

_V2_WORKFLOW_STACK_MEMBER = """
CREATE TABLE IF NOT EXISTS workflow_stack_member (
    stack_id      TEXT NOT NULL REFERENCES workflow_stack(stack_id),
    workflow_key  TEXT NOT NULL,
    position      INTEGER NOT NULL,
    PRIMARY KEY (stack_id, workflow_key)
)
"""

_V2_WORKFLOW_UNSTACKED = """
CREATE TABLE IF NOT EXISTS workflow_unstacked (
    workflow_key  TEXT PRIMARY KEY
)
"""

# Where a stored workflow FILE came from when it was pulled from a ComfyUI's
# saved workflows (#1440), one row per path over there. Its own table rather
# than columns on ``workflow_file``, because ``workflow_cards.forget_file``
# deletes that row when the file is deleted, and ``dismissed`` exists precisely
# to outlive that delete: a workflow the owner deleted here is not pulled back.
# ``workflow_name`` is many-to-one - a pull matches by content, so two paths
# holding one document both name the file it was stored as. ``remote_modified``
# is the remote machine's clock in milliseconds, a hint and never an identity.
# ``content_hash`` is ``workflow_inbox.content_hash`` of the document last read
# from that path. It is what a dismissal really keys on: the same workflow
# renamed in ComfyUI, reached through another spelling of its URL, or listed
# again after an empty listing is still the workflow the owner deleted.
_V2_WORKFLOW_ORIGIN = """
CREATE TABLE IF NOT EXISTS workflow_origin (
    origin           TEXT NOT NULL,
    remote_path      TEXT NOT NULL,
    workflow_name    TEXT,
    remote_modified  INTEGER,
    first_pulled_at  TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    dismissed        INTEGER NOT NULL DEFAULT 0,
    content_hash     TEXT,
    PRIMARY KEY (origin, remote_path)
)
"""

# The stored workflow files a pull WROTE (#1440), as opposed to ones the owner
# put there. Per FILE and not per path, so what a pull wrote stays pull-written
# when ComfyUI renames, edits or stops listing the path it came from - the
# one-off test reads it (``Card.hand_imported``). The owner handing a file over
# (the import route, the watched inbox) takes it off; deleting the file does
# too, since there is nothing left to describe.
_V2_WORKFLOW_PULLED_FILE = """
CREATE TABLE IF NOT EXISTS workflow_pulled_file (
    workflow_name  TEXT PRIMARY KEY
)
"""

# Which shelf models ran together in one ComfyUI run, read off ComfyUI's own
# ``GET /history`` by the workflow pull (#1518). Companion proposals count it
# beside ``workflow_recipe_asset``, so a checkpoint used in ComfyUI but never in
# a picture PixlStash filed still has evidence - kept here because ComfyUI
# forgets its history on restart and the shelf must not need it running.
#
# **Model ids, never names.** A run's asset names are resolved to shelf rows at
# pull time and only an unambiguous match is kept, so this table adds no place a
# model filename lives and "forget this model's name" has nothing new to reach.
# ``model_id`` is a plain column, not a foreign key: ``model.id`` is
# AUTOINCREMENT and never reused, a row whose model is gone is simply skipped,
# and a foreign key would make this a third child in the model-table rebuild.
_V2_COMFYUI_HISTORY_MODEL = """
CREATE TABLE IF NOT EXISTS comfyui_history_model (
    prompt_id  TEXT NOT NULL,
    model_id   INTEGER NOT NULL,
    PRIMARY KEY (prompt_id, model_id)
)
"""

# A model the owner replaced in a workflow because the original is gone (an
# FP8 checkpoint swapped for its BF16 build, say). **Keyed by topology and slot,
# not by card**: a picture made with the replacement in that slot is filed on
# the card the original made (``workflow_cards.fixed_slots``), so the card
# keeps its pictures, its name and its settings. The names are kept as the
# graph spelled them - ``was_name`` is what a run rewrites, and what the
# Workflow tab shows as the original - and the normalized forms are what the
# card key and the asset rows are matched on.
_V2_WORKFLOW_MODEL_FIX = """
CREATE TABLE IF NOT EXISTS workflow_model_fix (
    topology_hash  TEXT NOT NULL,
    slot_label     TEXT NOT NULL,
    was_norm       TEXT NOT NULL,
    now_norm       TEXT NOT NULL,
    was_name       TEXT NOT NULL,
    now_name       TEXT NOT NULL,
    PRIMARY KEY (topology_hash, slot_label, was_norm)
)
"""

_V2_WORKFLOW_INDEXES = (
    # "Which recipes are variants of this workflow" - the library view's expand
    # interaction, and the only query here that is not a primary-key lookup.
    "CREATE INDEX IF NOT EXISTS ix_workflow_recipe_topology "
    "ON workflow_recipe(topology_hash)",
    # "Which recipes use this model" - the model-companions plan's Workflow
    # sets, and the lookup a shelf row does to say what it is used by.
    "CREATE INDEX IF NOT EXISTS ix_workflow_recipe_asset_filename "
    "ON workflow_recipe_asset(normalized_filename)",
    # The covered-ghost cascade: "is any ghost still leaning on this instance
    # hash". Run once per purge per destroyed instance, on the one irreversible
    # path, so it is not a lookup that may degrade into a scan.
    "CREATE INDEX IF NOT EXISTS ix_workflow_picture_ghost_instance "
    "ON workflow_picture_ghost(library_uuid, instance_hash)",
    # "Which variants is this card made of" - every card read, since the
    # variants are where the pictures and the assets hang. The only index B2
    # adds: an index on a table nothing writes yet would be a write cost bought
    # for a query that does not exist, so the stack and file lookups get theirs
    # with the code that runs them.
    "CREATE INDEX IF NOT EXISTS ix_workflow_variant_key "
    "ON workflow_variant(workflow_key)",
    # "Which pulled paths name this file" - a delete's dismissal. The primary
    # key is by path, so without this it is a scan.
    "CREATE INDEX IF NOT EXISTS ix_workflow_origin_name "
    "ON workflow_origin(workflow_name)",
)

_V2_WORKFLOW_TABLES = (
    # Ordered by reference: recipe points at topology, graph and asset at recipe.
    _V2_WORKFLOW_TOPOLOGY,
    _V2_WORKFLOW_RECIPE,
    _V2_WORKFLOW_RECIPE_GRAPH,
    _V2_WORKFLOW_RECIPE_ASSET,
    _V2_WORKFLOW_RECIPE_INSTANCE,
    _V2_WORKFLOW_PICTURE_GHOST,
    _V2_WORKFLOW_PICTURE_INPUT,
    _V2_WORKFLOW_PARAMETER_PINS,
    # B2's card tables, in the same v2 and for the same reason: a build shipped
    # before this change has CURRENT_SCHEMA_VERSION = 2 and would refuse a v3
    # hub with HubSchemaTooNewError, locking the owner out of a downgrade.
    _V2_WORKFLOW_VARIANT,
    _V2_WORKFLOW_TOPOLOGY_CORE,
    _V2_WORKFLOW_SLOT_MARK,
    _V2_WORKFLOW_FILE,
    _V2_WORKFLOW_ATTR,
    _V2_WORKFLOW_DEFAULT_OVERRIDE,
    _V2_WORKFLOW_KEY_PINS,
    _V2_WORKFLOW_KEY_PICTURE_INPUT,
    _V2_WORKFLOW_COVER,
    _V2_WORKFLOW_STACK,
    _V2_WORKFLOW_STACK_MEMBER,
    _V2_WORKFLOW_UNSTACKED,
    _V2_WORKFLOW_ORIGIN,
    _V2_WORKFLOW_PULLED_FILE,
    _V2_COMFYUI_HISTORY_MODEL,
    _V2_WORKFLOW_MODEL_FIX,
    *_V2_WORKFLOW_INDEXES,
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


def _rebuild_model_with_kind_check(conn: sqlite3.Connection) -> None:
    """Add the adapter-kind CHECK to an existing ``model`` table, rows and all.

    SQLite has no ``ALTER TABLE ADD CONSTRAINT``, so the constraint can only
    arrive by rebuilding: create the new shape, copy every row into it, drop the
    old table, rename. The copy is what makes this cheap: ``model.id`` and
    ``sha256`` come across unchanged, so every ``model_file`` row still points
    at its content row and nothing has to be re-derived. Dropping the shelf
    instead would re-hash every adapter and re-queue every checkpoint through
    ``MissingCheckpointHashFinder`` at up to 24 GB each, to recover digests the
    hub already had.

    ``model_file`` is carried out and back in Python rather than left in place
    because foreign keys are on for the whole migration
    (``HubDatabase._configure``): ``DROP TABLE model`` runs an implicit DELETE
    whose FK violations are checked immediately, so a child row referencing it
    aborts the drop. ``PRAGMA defer_foreign_keys`` does not help, because
    re-creating the parent by rename never decrements the counter that delete
    increments, and ``PRAGMA foreign_keys=OFF`` is a silent no-op inside a
    transaction.

    One SAVEPOINT around the lot, because this runs both inside the migration's
    transaction and (via the tail re-run) outside one. That is the difference
    between a crash mid-rebuild and a hub whose ``model_file`` rows are gone.

    ``model_capability`` is the *other* child of ``model`` and is deliberately
    not carried here, because it cannot exist yet when this runs: its
    ``CREATE TABLE`` is in the statement loop that follows the caller's rebuild
    guard, and that guard is false forever after the rebuild. **A third child
    table would have to join the dance above** - the drop aborts otherwise.

    Raises:
        sqlite3.IntegrityError: A stored adapter row has no ``kind`` and the new
            CHECK rejects it. No producer emits that row, so it is a genuine
            surprise and is left to surface rather than worked around.
    """
    # Columns are named from the *stored* tables, not assumed to line up
    # positionally with the DDL above: a column this build does not know about
    # fails the copy loudly instead of being silently dropped.
    model_columns = ", ".join(
        row[1] for row in conn.execute("PRAGMA table_info(model)").fetchall()
    )
    file_columns = [
        row[1] for row in conn.execute("PRAGMA table_info(model_file)").fetchall()
    ]
    file_names = ", ".join(file_columns)
    saved_files = conn.execute(f"SELECT {file_names} FROM model_file").fetchall()

    offenders = conn.execute(
        "SELECT COUNT(*) FROM model WHERE file_kind = 'adapter' AND kind IS NULL"
    ).fetchone()[0]
    if offenders:
        logger.error(
            "%d adapter row(s) in this hub have no kind, which the new CHECK "
            "rejects, so the model table cannot be rebuilt. Nothing writes that "
            "row, so this is unexpected: inspect them with SELECT id, filename "
            "FROM model WHERE file_kind = 'adapter' AND kind IS NULL.",
            offenders,
        )

    logger.info(
        "Rebuilding the model table to add the adapter-kind CHECK, carrying "
        "%d model row(s) and %d location row(s) across.",
        conn.execute("SELECT COUNT(*) FROM model").fetchone()[0],
        len(saved_files),
    )
    conn.execute("SAVEPOINT model_kind_check")
    try:
        conn.execute("DROP TABLE model_file")
        conn.execute(
            _V2_MODEL.replace("IF NOT EXISTS model", "IF NOT EXISTS model_new")
        )
        conn.execute(
            f"INSERT INTO model_new ({model_columns}) SELECT {model_columns} FROM model"
        )
        conn.execute("DROP TABLE model")
        conn.execute("ALTER TABLE model_new RENAME TO model")
        conn.execute(_V2_MODEL_FILE)
        conn.executemany(
            f"INSERT INTO model_file ({file_names}) "
            f"VALUES ({', '.join('?' * len(file_columns))})",
            saved_files,
        )
    except sqlite3.Error:
        conn.execute("ROLLBACK TO model_kind_check")
        conn.execute("RELEASE model_kind_check")
        raise
    conn.execute("RELEASE model_kind_check")


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
    # When the first import into this folder started. While it is set, the
    # library's database is still under its temporary name and the folder holds
    # no vault.db: the row describes a library that does not exist yet, so the
    # listings hide it and the switch refuses it. Cleared by the promotion.
    # NULL for every library that already exists, which is the right answer for
    # every row an older hub carries.
    if "pending_import_at" not in columns:
        conn.execute("ALTER TABLE library ADD COLUMN pending_import_at TEXT")

    # Telemetry consent landed on develop while the multi-library feature lane
    # already had v2 developer hubs. Identity now lives in the hub, so mirror
    # develop's nullable opt-in columns here as part of the same unreleased,
    # guarded schema version. NULL is intentionally equivalent to the model's
    # False default for an existing owner who has never been prompted.
    user_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(user)").fetchall()
    }
    for column in (
        "telemetry_send_install_id",
        "telemetry_send_feature_usage",
        "telemetry_send_error_reports",
        "telemetry_send_hardware_profile",
        "telemetry_consent_prompted",
    ):
        if column not in user_columns:
            conn.execute(f"ALTER TABLE user ADD COLUMN {column} INTEGER")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS identity_migration_operation ("
        "library_uuid TEXT PRIMARY KEY REFERENCES library(uuid), "
        "source_path TEXT NOT NULL, payload_digest TEXT NOT NULL, "
        "state TEXT NOT NULL CHECK(state IN ('pending','copied','complete')))"
    )
    # Model shelf (v1.10.0). Amending v2 rather than adding a v3 is deliberate.
    # apply_migrations re-runs this function for any hub already at v2 (see the
    # tail of that function), so an existing developer or dev-build hub picks
    # these up on its next open. A v3 would be actively worse: a build shipped
    # before this change has CURRENT_SCHEMA_VERSION = 2, so it would refuse a v3
    # hub with HubSchemaTooNewError and lock that user out of a downgrade.
    # CREATE TABLE IF NOT EXISTS throughout, so re-running is a no-op.
    #
    # One-shot drop first, for the same reason: IF NOT EXISTS cannot reshape a
    # table, so a hub opened on an earlier unreleased develop would keep the
    # superseded `adapter`/`adapter_file`/`checkpoint` shape alongside the new
    # `model`/`model_file` one. Dropping rather than migrating is correct only
    # because nothing has ever written these tables - the scan that fills them
    # is unmerged, no route or UI reads them, and no released build shipped
    # them (v1.10.0-dev.1 was tagged 13 hours before they landed). `model_folder`
    # and `adapter_stack` are NOT dropped: a developer may have registered
    # folders, and neither table changes shape.
    existing = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table'"
        )
    }
    # `adapter` does not exist after the reshape, so this guard is false on a
    # fresh hub and false on every subsequent re-run.
    if "adapter" in existing:
        logger.info(
            "Replacing the superseded model-shelf tables %s with model/model_file.",
            ", ".join(_V2_SUPERSEDED_SHELF_TABLES),
        )
        # DROP TABLE takes each table's indexes with it, so the superseded
        # ix_adapter_* indexes need no separate statement.
        for table in _V2_SUPERSEDED_SHELF_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")

    # The `kind` CHECK was added after the reshape, and unlike the tables above
    # `model` holds rows worth keeping by then, so it is rebuilt rather than
    # dropped. Keyed on the stored DDL, so it is false on a fresh hub (the
    # CREATE below already carries the CHECK) and false on every re-run after
    # the rebuild. The indexes the drops take with them are recreated by the
    # statement loop that follows.
    model_ddl = existing.get("model")
    if model_ddl is not None and "kind IS NOT NULL" not in model_ddl:
        _rebuild_model_with_kind_check(conn)

    for statement in _V2_MODEL_SHELF_TABLES:
        conn.execute(statement)

    # The workflow library (v1.11), amended into v2 for the same reason the
    # model shelf was: a build shipped before this change has
    # CURRENT_SCHEMA_VERSION = 2 and would refuse a v3 hub outright, locking
    # that user out of a downgrade. CREATE TABLE IF NOT EXISTS throughout, so
    # re-running is a no-op and an existing developer hub picks these up on its
    # next open.
    # An unreleased branch briefly created workflow_picture_input with a CHECK
    # forbidding a picture on a non-Fixed row. IF NOT EXISTS cannot loosen it,
    # so a developer hub holding that shape is rebuilt with its rows.
    input_ddl = existing.get("workflow_picture_input")
    if input_ddl is not None and _V2_WORKFLOW_PICTURE_INPUT_CHECK not in input_ddl:
        rows = conn.execute("SELECT * FROM workflow_picture_input").fetchall()
        conn.execute("DROP TABLE workflow_picture_input")
        conn.execute(_V2_WORKFLOW_PICTURE_INPUT)
        conn.executemany(
            "INSERT INTO workflow_picture_input "
            "(library_uuid, workflow_name, node_id, mode, pixel_sha) "
            "VALUES (?, ?, ?, ?, ?)",
            [tuple(row) for row in rows],
        )

    for statement in _V2_WORKFLOW_TABLES:
        conn.execute(statement)

    # The card's post-processing groups (#1454), guarded on PRAGMA table_info
    # like every other amendment into v2: a no-op on a fresh hub, because the
    # CREATE above already carries the column, and a no-op on re-run. Nullable
    # by design - an existing row means "derived before this column existed",
    # which is what `workflow_cards._VARIANT_PENDING` re-queues on.
    core_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(workflow_topology_core)").fetchall()
    }
    if "specials" not in core_columns:
        conn.execute("ALTER TABLE workflow_topology_core ADD COLUMN specials TEXT")

    # The dismissal's content key (#1440), guarded the same way. No released
    # hub has this table; a development hub that ran an earlier commit of the
    # pull does, and would otherwise fail every pull on the missing column. A
    # NULL there means "not read since", which the next pull fills in.
    origin_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(workflow_origin)").fetchall()
    }
    if "content_hash" not in origin_columns:
        conn.execute("ALTER TABLE workflow_origin ADD COLUMN content_hash TEXT")
    # "Was this content dismissed, at any path or origin" - once per pulled
    # document. Here and not in `_V2_WORKFLOW_INDEXES`: those run before this
    # ALTER, and on a hub that needed it the column would not exist yet.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_origin_content "
        "ON workflow_origin(content_hash)"
    )

    # The icon column (shelf plan, the sixth verb) lands the same way the rest
    # of v2 does: amended in place rather than as a v3, because a build shipped
    # before this change has CURRENT_SCHEMA_VERSION = 2 and would refuse a v3
    # hub with HubSchemaTooNewError. Guarded on PRAGMA table_info, so it is a
    # no-op on a fresh hub (the CREATE above already carries it) and on re-run.
    model_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(model)").fetchall()
    }
    if "icon_sha256" not in model_columns:
        conn.execute("ALTER TABLE model ADD COLUMN icon_sha256 TEXT")
    # The header facts (#1314), the same way and for the same reason.
    for column in ("family", "quant", "weights_id"):
        if column not in model_columns:
            conn.execute(f"ALTER TABLE model ADD COLUMN {column} TEXT")
    # The identified base model, the same way and for the same reason. The
    # rows already on the shelf are filled by the one-shot data backfill
    # (`_backfill_base_model_canonical`), not here: this runs on every open.
    for column in ("base_model_canonical", "base_model_source"):
        if column not in model_columns:
            conn.execute(f"ALTER TABLE model ADD COLUMN {column} TEXT")

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


def _backfill_component_roles(conn: sqlite3.Connection) -> int:
    """Re-file VAEs and text encoders that were registered before they had kinds.

    Every row on an existing shelf was classified by tensor markers and a
    parameter count alone, and those two cannot see a support file: a VAE and a
    CLIP fall below ``_CHECKPOINT_MIN_PARAMS`` and were stored as ``unknown``,
    while a T5-class encoder clears it and was stored as ``checkpoint``. The
    directory each file sits in says which it is, and the directory is already
    in the hub - so this needs no rescan and reads no bytes.

    Only ``unknown`` and ``checkpoint`` rows are considered. An ``adapter`` was
    asserted from markers the file cannot strip, and an ``engine`` was declared
    by us rather than derived, so neither is a guess this can improve on.

    **A model with several copies must agree with itself.** Two locations that
    name different roles - one under ``vae/``, one loose in a mixed folder -
    are not evidence, so the row is left alone rather than resolved by picking
    a side.

    **``present`` copies decide it when there are any.** ``model_file`` is also
    the tombstone: a copy deleted months ago leaves its row behind with
    ``state = 'missing'``, and a dead path in a differently-named folder would
    otherwise manufacture a disagreement and veto a re-filing that every live
    copy agrees on. A model with *no* present copy - every location on a drive
    that is not plugged in - still falls back to the paths it has, because this
    runs once and skipping it there would mislabel that drive permanently.

    Runs exactly once per hub (see :data:`CURRENT_DATA_VERSION`), because
    ``file_kind`` is owner-correctable and a backfill that re-ran would undo the
    correction on the next restart.

    Args:
        conn: An open hub connection, inside the caller's transaction.

    Returns:
        How many rows were re-filed.
    """
    # Indexed positionally rather than by name: this runs from
    # `apply_migrations`, which a test may hand a bare `sqlite3.connect` with no
    # `row_factory` set.
    rows = conn.execute(
        "SELECT m.id, f.path, mf.relpath, mf.state "
        "FROM model m "
        "JOIN model_file mf ON mf.model_id = m.id "
        "JOIN model_folder f ON f.id = mf.model_folder_id "
        "WHERE m.file_kind IN (?, ?)",
        (FILE_UNKNOWN, FILE_CHECKPOINT),
    ).fetchall()

    # Gathered per state so the present copies can be preferred whole. Taking
    # the union and then dropping tombstones would be the same thing written
    # so that a later reader cannot see the rule.
    live_roles: dict[int, set] = {}
    any_roles: dict[int, set] = {}
    for model_id, folder_path, relpath, state in rows:
        role = role_from_folder(f"{folder_path}/{relpath}")
        any_roles.setdefault(model_id, set()).add(role)
        if state == _BACKFILL_LIVE_STATE:
            live_roles.setdefault(model_id, set()).add(role)

    roles_by_model = {
        model_id: live_roles.get(model_id) or roles
        for model_id, roles in any_roles.items()
    }

    refiled = 0
    for model_id, roles in roles_by_model.items():
        if len(roles) != 1:
            continue
        (role,) = roles
        if role is None:
            continue
        conn.execute("UPDATE model SET file_kind = ? WHERE id = ?", (role, model_id))
        refiled += 1
    if refiled:
        logger.info(
            "Re-filed %d model rows as VAEs or text encoders from the folder "
            "they sit in; they were registered before those kinds existed.",
            refiled,
        )
    return refiled


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


def _drop_blank_recipe_assets(conn: sqlite3.Connection) -> int:
    """Delete the asset rows that name no model at all (#1416).

    A shelf loader's widget is blank until its Browse button is clicked, and
    the PixlStash CLIP loader's second encoder is blank on every SD and SDXL
    graph. ``workflow_hash`` used to keep that blank as a topology asset, so
    each one filed a ``workflow_recipe_asset`` row with an empty
    ``normalized_filename``. It now keeps only a digest, but the rows already
    written are unreachable: ``_model_ghost_names`` routes a ``*_sha256``
    widget to the digest branch, where an empty value is not a digest and so is
    never a ghost, so "forget model names not on the shelf" cannot clear them.

    Deleting one loses nothing -- an empty name identifies nobody and resolves
    to no shelf row -- and no hash moves and no stored document is rewritten,
    exactly as forgetting a name does not. The ``asset:e3b0c442...`` reference
    in the document stays, which is the same shape a forgotten name leaves.

    Runs exactly once per hub (see :data:`CURRENT_DATA_VERSION`).

    Args:
        conn: An open hub connection, inside the caller's transaction.

    Returns:
        How many rows were deleted.
    """
    deleted = conn.execute(
        "DELETE FROM workflow_recipe_asset WHERE normalized_filename = ''"
    ).rowcount
    if deleted:
        logger.info(
            "Hub data v2: dropped %d recipe asset row(s) naming no model.", deleted
        )
    return deleted


def _backfill_base_model_canonical(conn: sqlite3.Connection) -> int:
    """Identify the rows already on the shelf whose stored base model folds.

    From the stored ``base_model`` alone, and **only an exact fold of it**
    (``declared``), so it reads no file and parses no header. Nothing weaker is
    written here, because a stored answer stops the scanner re-reading the
    header, and the header can carry better evidence than the columns do
    (``modelspec.architecture``, ``ss_sd_model_name``): a filename or fuzzy
    guess written now would never be checked against it. Every row this leaves
    NULL is identified from all of its evidence on its next scan, which re-reads
    the header - never the bytes - of each row whose source is still NULL.

    Only rows with no source are touched, so a hub that ran a scan first loses
    nothing to this. Runs exactly once per hub (see
    :data:`CURRENT_DATA_VERSION`).

    Args:
        conn: An open hub connection, inside the caller's transaction.

    Returns:
        How many rows were identified.
    """
    rows = conn.execute(
        "SELECT id, base_model FROM model "
        "WHERE base_model_source IS NULL AND base_model IS NOT NULL"
    ).fetchall()
    updates = []
    for model_id, base_model in rows:
        canonical, source = identify([base_model], [])
        if source == SOURCE_DECLARED:
            updates.append((canonical, source, model_id))
    conn.executemany(
        "UPDATE model SET base_model_canonical = ?, base_model_source = ? WHERE id = ?",
        updates,
    )
    if updates:
        logger.info(
            "Hub data v3: identified the base model of %d of %d shelf row(s).",
            len(updates),
            len(rows),
        )
    return len(updates)


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
                # Take the write lock before reading the schema. _apply_v2 asks
                # PRAGMA table_info what exists and then ALTERs what does not,
                # and under sqlite3's default DEFERRED transaction two processes
                # opening the same hub can both read "column absent" and both
                # try to add it. The loser fails with "duplicate column name"
                # and the hub stays on the old version.
                conn.execute("BEGIN IMMEDIATE")
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
    #
    # BEGIN IMMEDIATE for the same reason the versioned path takes it: this is
    # the same read-then-ALTER logic, so a server and a `pixlstash libraries`
    # CLI opening a pre-model-shelf v2 hub at once can both read "column
    # absent" and both try to add it. Without the lock the loser raises
    # OperationalError out of HubDatabase.__init__, uncaught.
    if version >= 2:
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                _apply_v2(conn)
        except sqlite3.Error as exc:
            logger.error(
                "Hub v2 shape reconciliation failed, hub stays on version %d: %s",
                version,
                exc,
            )
            raise

    # Data backfills, after the shape is settled and each applied exactly once.
    # BEGIN IMMEDIATE and the version write share the transaction for the same
    # reason the schema steps do: an interrupted backfill must leave the counter
    # where it was, so the next open retries it rather than skipping it.
    data_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if data_version < CURRENT_DATA_VERSION:
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                if data_version < 1:
                    _backfill_component_roles(conn)
                if data_version < 2:
                    _drop_blank_recipe_assets(conn)
                if data_version < 3:
                    _backfill_base_model_canonical(conn)
                # No placeholder: PRAGMA takes no parameters, and the value is
                # this module's own constant rather than anything from outside.
                conn.execute(f"PRAGMA user_version = {CURRENT_DATA_VERSION:d}")
        except sqlite3.Error as exc:
            logger.error(
                "Hub data backfill to version %d failed, hub stays on data "
                "version %d: %s",
                CURRENT_DATA_VERSION,
                data_version,
                exc,
            )
            raise

    return version
