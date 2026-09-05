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
CURRENT_DATA_VERSION = 1

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
    -- 'present' | 'missing' | 'unreachable' | 'not_downloaded'. The first three
    -- are the scanner's (see model_folder_scanner); the fourth belongs to the
    -- roots PixlStash declares rather than scans, where an absent file is one we
    -- have not fetched yet and never one that wandered off.
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
)

_V2_MODEL_SHELF_TABLES = (
    # adapter_stack first: `model.stack_id` references it.
    _V2_ADAPTER_STACK,
    _V2_MODEL_FOLDER,
    _V2_MODEL,
    _V2_MODEL_FILE,
    # After `model`: it references `model(id)`.
    _V2_MODEL_CAPABILITY,
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
# The instance tier is deliberately absent HERE, and that is not an oversight
# now that ingest computes an instance hash. The hash is a value on a picture
# (``picture.workflow_instance_hash``): two pictures share an instance exactly
# when they share it, which is the whole of what v1.11 asks of the tier. A
# hub-side ``recipe_instance`` table is AI-toolkit Phase 2 and moved to v1.12
# with the rest of it, so nothing in this release stores an instance ROW
# anywhere. Its location is not in question - §4 puts the whole family here.

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

_V2_WORKFLOW_INDEXES = (
    # "Which recipes are variants of this workflow" - the library view's expand
    # interaction, and the only query here that is not a primary-key lookup.
    "CREATE INDEX IF NOT EXISTS ix_workflow_recipe_topology "
    "ON workflow_recipe(topology_hash)",
    # "Which recipes use this model" - the model-companions plan's Workflow
    # sets, and the lookup a shelf row does to say what it is used by.
    "CREATE INDEX IF NOT EXISTS ix_workflow_recipe_asset_filename "
    "ON workflow_recipe_asset(normalized_filename)",
)

_V2_WORKFLOW_TABLES = (
    # Ordered by reference: recipe points at topology, graph and asset at recipe.
    _V2_WORKFLOW_TOPOLOGY,
    _V2_WORKFLOW_RECIPE,
    _V2_WORKFLOW_RECIPE_GRAPH,
    _V2_WORKFLOW_RECIPE_ASSET,
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
    for statement in _V2_WORKFLOW_TABLES:
        conn.execute(statement)

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
