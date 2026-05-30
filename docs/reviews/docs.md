# Code Review — `docs` branch (since v1.4.1)

**Scope:** ~9,800 insertions across 81 files. Snapshot → restore → undo system
on top of the existing `ChangeLog` audit trail, plus a big API-docs overhaul
(Scalar/OpenAPI, response_models, screenshots, website rebuild, Scalar CDN).

**Posture:** Grumpy senior engineer. I re-verified every previous-review finding
against the current `HEAD` of `docs`; most of the headline issues from earlier
passes are **already fixed** and have been removed from this review. What's
below is what's actually still open.

---

## What's already been addressed (don't re-litigate)

These prior findings are stale and have been confirmed fixed in current `HEAD`:

| Prior | Status | Where it now lives |
|---|---|---|
| C1 — scope leak on snapshot GET endpoints | ✅ FIXED | every route in [routes/snapshots.py](../../pixlstash/routes/snapshots.py) calls `auth.require_unscoped_owner` |
| C2 — no mutex on concurrent restores | ✅ FIXED | `_restore_lock` non-blocking acquire in `restore_full`/`restore_resource`/`restore_batch` (~lines 222, 436, 732), raises `RestoreInProgressError` |
| C3 — engine dispose inside open Session | ✅ FIXED | `_do_swap` is submitted via `db.run_control_task`; worker skips opening a Session for control tasks ([database.py:1093-1104](../../pixlstash/database.py#L1093-L1104)) |
| H3 — `merge` on faces / set members risks PK collision + leaves live-only rows | ✅ FIXED | `_upsert_rows` bulk-deletes children by `picture_id` then inserts fresh ([restore_service.py:1508-1540](../../pixlstash/services/restore_service.py#L1508-L1540)) |
| H4 — cleanup relies on FK cascade | ✅ FIXED | `_post_restore_cleanup` explicitly bulk-deletes Tag/Face/PSM/PPM by `picture_id.in_(missing_ids)` before deleting pictures |
| M1 — ChangeLog INSERT failure swallowed | ✅ FIXED | `_after_flush_handler` now re-raises; worker rolls back the user write |
| M2 — in-place mutated JSON columns | ✅ N/A | no JSON/MutableList/MutableDict columns exist; comfyui fields are String-of-JSON, reassignment-only |
| M3 — snapshot-file backfill races on disk | ✅ FIXED | per-path `_snapshot_file_lock` used by all callers |
| M4 — `hash_compare` only catches `ValueError` | ✅ FIXED | now catches `Exception` with `exc_info=True` |
| M5 — `fetchSnapshots()` on mount for read-only | ✅ FIXED | gated on `!isReadOnly` in App.vue |
| `wal_snapshot(TRUNCATE)` typo | ✅ FIXED | uses `wal_checkpoint(TRUNCATE)` |
| `_compute_picture_metadata_hash` uses Pydantic-v1 `__fields__` | ✅ PARTLY | now `type(pic).model_fields` — but see 🟠 below, it still iterates relationships |
| Frontend stale-closure hash-compare race | ✅ FIXED | run-token guard in [ImageGridContextMenu.vue:425-457](../../frontend/src/components/widgets/ImageGridContextMenu.vue#L425-L457) |
| Frontend "blindly appends Z" | ✅ N/A | `formatSnapshotDate` already checks for `Z`/offset before appending |
| `humanBytes` caps at GB | ✅ N/A | caps at EB |
| Dual-list guardrail test missing | ✅ FIXED | `test_change_log_dual_list_covers_all_tables` exists at [tests/test_architecture_guardrails.py:259-318](../../tests/test_architecture_guardrails.py#L259-L318) |
| Per-resource restore of pictures-with-children untested | ✅ FIXED | [test_restore.py:246-349, 356-389, 397-421](../../tests/test_restore.py#L246-L349) |
| GFS-retention untested | ✅ N/A | three GFS tests at test_snapshots.py:206-371 |

Good clean-up between the previous review and now. The remaining issues below
are genuinely still open in `HEAD`.

---

## ✅ Resolved during review

**~~/server-config/* endpoints were unscoped~~** — fixed. All five
`/server-config/*` routes (`watch-folders` GET, `filesystem-roots` GET,
`snapshots` GET+PATCH, `open` POST) now call `require_unscoped_owner`. The
side-effectful `open` POST was the most obviously wrong (any authenticated
scoped token could trigger an OS file-browser action on the host).
`/workers/progress` still uses `require_user_id` — defensible as read-only
telemetry but worth a second look.

## 🟣 Context: undo is not shipped in 1.5.0

The `POST /undo` HTTP route was removed ([commit f18548e3](https://github.com),
"Make sure undo is gone for this release — not robust enough yet"). The
`UndoService` and the `after_flush` ChangeLog hook are still wired up — the
ChangeLog table is populated on every write — but nothing in 1.5.0 reads it.
This downgrades several findings: anything that's only reachable via undo is a
"fix before re-enabling," not a release blocker. Marked below as 🟣 to signal
"hold for next-release work, but don't lose the trail."

## 🔴 Critical

**C1 — Missing migration-from-real-DB test**
[tests/test_migrations.py:25-50](../../tests/test_migrations.py#L25-L50)

`test_migrations` runs `alembic upgrade head` only on a fresh sqlite. The
baseline migration uses `SQLModel.metadata.create_all()`, which creates tables
with all current model columns; conditional `add_column` then no-ops. **Nothing
exercises stacked migrations against an actually-pre-existing v1.4.1 schema.** A
migration that breaks pre-existing rows would land green. This is the one test
gap that bites in 1.5.0 regardless of the undo deferral — every user who
upgrades runs these migrations on a real, not-fresh DB.

## 🟣 Deferred — only matters when undo is re-enabled

**D1 — UPDATE-undo silently keeps new BLOBs in place**
[undo_service.py:543-579](../../pixlstash/services/undo_service.py#L543-L579)

`_coerce_serialized_value` returns `(False, None)` for `"sha256:…"` BLOB
markers (Face.features, Picture.text_embedding, Picture.image_embedding). The
DELETE-undo branch at least appends a warning to `report.errors` and re-merges
with NULL. The UPDATE-undo branch silently omits the column from kwargs — the
new (post-change) embedding stays in place and undo reports success. Fix both
branches to surface in `UndoReport.errors` and ideally enqueue reprocessing.
This is the "not robust enough yet" the commit message acknowledges.

**D2 — No ChangeLog atomicity test**
[tests/test_change_log.py](../../tests/test_change_log.py)

`_after_flush_handler` re-raises (so a failed audit row rolls back the user
write) but no test pins that contract. ChangeLog is now write-only in 1.5.0,
so polluted rows aren't user-visible — they only become a problem the moment
undo is re-enabled and tries to reverse history that includes orphaned or
duplicate rows. Pair with re-enabling undo.

**D3 — Plus everything else in `undo_service.py`** flagged below (H8, M
undo-conflict tests, the thin column-type matrix, the `one_or_none()`
defensiveness). All deferred to the same milestone.

---

## 🟠 High

**H1 — Restore can leave the planner stopped forever**
[restore_service.py:309-378](../../pixlstash/services/restore_service.py#L309-L378)

`restore_full` calls `planner.stop()` and only restarts it on the success path.
If `_do_swap` or the cleanup task raises, there is no `try/finally` to restart
the planner. Result: background work (daily snapshots, missing-file detection,
embedding generation, …) silently halts after any restore failure, until the
server restarts. Wrap steps 4–6 in `try/finally: planner.start()`.

**H2 — `_post_restore_cleanup` is fire-and-forget**
[restore_service.py:373-377](../../pixlstash/services/restore_service.py#L373-L377)

The cleanup task is `db.run_task(...)` but the return is discarded and the
planner is restarted on the next line. The cleanup may still be queued behind a
newly-started planner task; the post-restore "missing-file pictures dropped"
guarantee is timing-dependent. Either `result_or_throw()` before restarting
the planner, or wait on the future.

**H3 — `write_reason` is called without `actor_user_id` in restore paths**
[restore_service.py:759, 1390](../../pixlstash/services/restore_service.py#L759)

Every restore-driven ChangeLog row gets `actor_user_id=NULL`. The router knows
the user; the service signature doesn't. Three public entry points
(`restore_full`/`restore_resource`/`restore_batch`) all need to accept and
forward `actor_user_id`. For an audit trail this is a real hole — the moment
two restores happen back-to-back, attribution is gone.

**H4 — `_swap_database` doesn't fsync before `os.replace`**
[restore_service.py:1271](../../pixlstash/services/restore_service.py#L1271)

`shutil.copy2(staged, snapshot)` → `os.replace(snapshot, live_path)`. The
`os.replace` is atomic for the rename, but neither the staged file's fd nor
the containing directory is fsync'd before the swap. A power loss between
copy and the next implicit fsync can leave the live DB pointing at a file
whose pages aren't yet on disk. For SQLite + a feature called "restore from
snapshot," durability ought to be the floor.

**H5 — `_compute_picture_metadata_hash` lazy-loads relationships into the hash**
[database.py:371](../../pixlstash/database.py#L371)

`for field_name in type(pic).model_fields:` iterates **all** SQLModel fields,
including relationship fields (`tags`, `faces`, `project`, …). `getattr` on a
relationship during `after_flush` triggers a lazy load. The returned ORM object
isn't JSON-serialisable, so `json.dumps(..., default=str)` digests Python
`repr()` — which contains object memory addresses. **The hash is
non-deterministic across reloads.** That defeats the whole "Disable
checkpoints that are identical to the current one" feature: a no-op snapshot
will look different from the previous one and won't be skipped. Fix: iterate
`sa_inspect(Picture).column_attrs` only (the same pattern `_column_by_attr`
already uses).

**H6 — `_post_restore_cleanup` materialises the whole Picture table in Python**
[restore_service.py:339-371](../../pixlstash/services/restore_service.py#L339-L371)

`select(Picture)` → Python loop → mutate. On a 100k-row vault that's 100k ORM
objects through identity map. Use a bulk `update(Picture).values(...)` with a
filter, or chunk.

**H7 — `restore_service.py` is 1,949 lines in one module**

Three preview methods, three restore methods, the swap path, the upgrade path,
the hash-compare path, the cleanup path, and the locking — all in one file.
Splitting into `restore_service/{public, swap, upgrade, preview, hash}.py` would
make every one of the findings in this review easier to verify, and a future
maintainer (you, in six months) less hostile.

**H8 — Scalar API docs load from a CDN with no SRI / version pin**
[server.py: `render_scalar_html`](../../pixlstash/server.py)

`/scalar` fetches `https://cdn.jsdelivr.net/npm/@scalar/api-reference` live —
no integrity hash, no version pin. A self-hosted product whose docs page is
reachable inside the user's LAN now has a cross-origin trust path that didn't
exist before. Pin a version + `integrity` attribute, or vendor the bundle
into `data/scalar-assets/` (which you've already set up for screenshots).


**H9 — App.vue WS handlers don't gate on `!isReadOnly`**
[App.vue ~266-283](../../frontend/src/App.vue)

The snapshot/restore WS push handlers call `snapshotsStore.fetchSnapshots()`
unconditionally. Read-only / share sessions will see a stream of 403s every
time the owner creates or restores a snapshot. Same fix as the mount-time
guard (which is in place).

**H10 — `test_concurrent_restore_rejected_with_409` is not a concurrency test**
[test_restore.py:475-493](../../tests/test_restore.py#L475-L493)

It reaches into `svc._restore_lock` (private attribute) and `.acquire()`s it
on the test thread, then calls `restore_full` from the same thread. No second
writer, no race. The real production race — two HTTP requests landing
concurrently — is unverified. Rename to `test_lock_short_circuits` and add a
real two-thread test that proves the second request gets a 409 while the first
is still running.

---

## 🟡 Medium

**Backend**

- **GFS "oldest" picks `min(created_at)`, not `min(max_changelog_id)`** ([snapshot_service.py:451](../../pixlstash/services/snapshot_service.py#L451)). NTP jumps / restore-from-backup can leave a snapshot whose `created_at` is recent but whose `max_changelog_id` is the lowest — truncation would delete rows still referenced by another, "newer"-by-clock snapshot.
- **Snapshot identity is a UUID, not a content hash** ([snapshot_service.py:88](../../pixlstash/services/snapshot_service.py#L88)). The "Disable checkpoints that are identical to the current one" feature lives outside this layer (likely keyed on `Picture.metadata_hash` rollup) — fine, but rename the variable to `snapshot_uuid` so the next reader doesn't assume content-hash semantics.
- **`server-config.json` write is non-atomic** ([routes/config.py:642-647](../../pixlstash/routes/config.py#L642-L647)). `open(path, "w")` truncates on open; a crash mid-write zeroes the file. Write to `path.tmp` + `os.fsync` + `os.replace`. Also: `vault.set_daily_snapshots_enabled(...)` is called **before** the file write, so a write failure leaves runtime and config out of sync. Persist first, then mutate runtime.
- **`import json as _json` inline inside `create_router`** ([routes/config.py:600](../../pixlstash/routes/config.py#L600)). Per `pixlstash/CLAUDE.md` imports section, this should be at module top — there's nothing optional about `json`.
- **`_install_custom_openapi` monkey-patches `app.openapi`** ([server.py:1593-1670](../../pixlstash/server.py#L1593-L1670)) with no guard against an early `app.openapi()` caching `openapi_schema` before all routers are mounted. Currently safe because `_setup_routes()` precedes the install; defensive fix is `self.api.openapi_schema = None` at the top of `custom_openapi`.
- **`_strip_query_param_defaults` / `_inject_response_examples` / `_inject_path_param_examples` post-processors are untested**. If a future FastAPI version moves `default` into `anyOf` differently, they silently no-op. Add one test per post-processor that asserts a representative shape change.
- **`response_model` with `model_config = ConfigDict(extra="allow")`**. You added `response_model` to lots of routes, then weakened them with `extra="allow"`. That defeats the contract — a backend dict that loses a key serialises fine and the consumer-facing schema lies. Either drop `extra="allow"` or drop `response_model`; the middle ground is the worst of both.
- **GFS pruning re-enters the writer thread inside `snapshot_if_due`** ([snapshot_service.py:317](../../pixlstash/services/snapshot_service.py#L317)). If the caller is itself holding a writer txn, the manifest's `max_changelog_id` may be from a different session — could miss in-flight rows. Worth a comment documenting which contexts may call this, even if today's callers are all safe.
- **`_vacuum_into` runs while a `session.connection()` is open** ([snapshot_service.py:339-344](../../pixlstash/services/snapshot_service.py#L339-L344)). SQLite VACUUM requires no open txn. Works today because the writer thread's session isn't in a transaction at the call site, but a future `before_flush` hook could trip this. Run on a dedicated raw connection.
- **`_before_flush_handler` materializes the full pre-flush row into `before_json` for every UPDATE** ([database.py: `_cl_before_state_from_history`](../../pixlstash/database.py)), not just the changed columns. ChangeLog will be several times larger than necessary; for tables with text_embedding bytes-as-sha256 that's already trimmed, but for everything else this scales poorly.
- **Migration 0049 unconditionally `UPDATE picture SET metadata_hash = NULL`** even when the column was just added (all rows are already NULL). Harmless but pointless full-table write on every migrate. Gate inside the `if not in existing_cols` branch.
- **`_find_missing_file_ids` leaks engine on exception path** ([restore_service.py:1213-1231](../../pixlstash/services/restore_service.py#L1213-L1231)). `engine.dispose()` only fires on success. `try/finally`.
- **Alembic upgrade is not cached** ([restore_service.py:1141-1197](../../pixlstash/services/restore_service.py#L1141-L1197)). Every preview / restore / compare runs full `alembic upgrade head` against a freshly-copied temp file. Repeat preview clicks pay the full migration cost each time. Cache by `(snapshot_path, mtime)`.
- **Dead `"project"` branch in `_collect_rows_for_upsert`** while `_SUPPORTED_RESOURCE_TYPES` excludes project ([restore_service.py:77, 1339, 1454](../../pixlstash/services/restore_service.py#L77)). Delete the branch or add the type — currently it's a trap for the next reader.
- **`_swap_database` always `os.remove` + `rmtree` in `finally`** ([restore_service.py:1291](../../pixlstash/services/restore_service.py#L1291)). If the swap raised mid-copy, the staged temp may be the only copy of the just-upgraded snapshot. Original snapshot still exists so this isn't catastrophic — but a debuggable failure becomes an undebuggable one.
- **`one_or_none()` on scalar aggregate** in undo guard ([undo_service.py:264-268](../../pixlstash/services/undo_service.py#L264-L268)) — depending on SQLModel version, this can return `(None,)` instead of `None`. `int > int + 1` then raises `TypeError`, swallowed nowhere. Use `.scalar()` or normalise.

**Frontend**

- **`useSnapshotsStore` mixes error patterns** — `createSnapshot`/`renameSnapshot`/`deleteSnapshot` throw; `fetchStatus`/`fetchSnapshotSettings` swallow into `console.warn`. Pick one. Document the convention in `docs/frontend_architecture.md` (currently silent on store-error-handling).
- **`onSnapshotCreated(payload)` ignores its payload** ([useSnapshotsStore.js:117](../../frontend/src/stores/useSnapshotsStore.js#L117)) and re-fetches the whole list. The WS push presumably carries the new snapshot row — push to head locally instead of round-tripping.
- **`onRestoreStarted` synthesises `activeJob` client-side** ([useSnapshotsStore.js:129](../../frontend/src/stores/useSnapshotsStore.js#L129)) — if two clients restore simultaneously, this client's view is wrong. Trust the server payload or call `fetchStatus`.
- **`ImageGridContextMenu` hash-compare runs even when selected pictures aren't in any snapshot** ([ImageGridContextMenu.vue:427](../../frontend/src/components/widgets/ImageGridContextMenu.vue#L427)). On every submenu hover, one POST per recent snapshot. Gate on "selection intersects any snapshot's picture set" — or at minimum cache.
- **`identicalSnapshotIds` is not cleared when the menu closes** — stale highlight persists across invocations. Watch `props.visible` and reset.
- **`RestoreConfirmDialog` uses `@click:outside="dialogOpen = false"`** ([RestoreConfirmDialog.vue:172](../../frontend/src/components/widgets/RestoreConfirmDialog.vue#L172)) on a destructive-action dialog. A misclick dismisses the preview. Use `persistent`.
- **A11y on `RestoreConfirmDialog`** — no `aria-label` on the X button, destructive Restore button has no `aria-describedby` pointing at the preview summary, focus doesn't land on Cancel (conventional for destructive dialogs).
- **`SnapshotsSection.vue` uses `window.confirm()` for delete** — inconsistent with the rest of the snapshot UI, hostile on mobile, strips theming.
- **`SnapshotsSection.vue` label save fires on both `@keydown.enter` and `@blur`** — the bail-out at line 103 is defensive coding around a structural double-fire. Use `@change`.
- **`humanBytes` and `summaryLabel` re-implemented inline** in both `SnapshotsSection` and `RestoreConfirmDialog` despite `utils/snapshots.js` existing precisely for shared formatters. Centralise.

**Tests**

- **No undo-conflict test** — same row modified by an unrelated txn after snapshot, then `undo_to_snapshot`. Behavior is unspecified.
- **Undo column-type matrix is thin** — only `description` (TEXT) and BLOB "reset to NULL" warning. No enum, no FK, no nullable→non-nullable. Guardrail 8 *prevents* mutable JSON; it doesn't prove undo on other types works.
- **`test_picture_scoped_all_token_rejected_on_snapshot_routes` only checks `GET /snapshots`** ([test_snapshots_auth.py](../../tests/test_snapshots_auth.py)). Prior review flagged four endpoints — only one is covered. Parametrize.
- **`test_openapi_response_schemas.py` smoke-checks, doesn't validate response shape per route**. A `response_model` swap from `SnapshotResponse` to `dict` would pass.
- **Daily-snapshot background task is untested** — no test that it runs, that it's idempotent within a day, or that it isolates failures.
- **`_upgrade_snapshot_schema` test is `0048→0049` with one column** ([test_restore.py:501-553](../../tests/test_restore.py#L501-L553)). Multi-step migrations and data-shape upgrades unverified — this is the most fragile part of the feature.
- **All four feature test modules run with `disable_background_workers=True`** ([conftest module fixtures]). The real production codepath — background workers writing during a restore — has zero coverage. Tests are written *around* the workers rather than against them.
- **`test_undo_to_snapshot_noop_when_no_changes` asserts almost nothing** ([test_undo.py:225-239](../../tests/test_undo.py#L225-L239)). Docstring claims background writes are "correctly reversed"; nothing actually asserts that. False confidence.

---

## 🪶 Low / cosmetic

- **"Snapshot snapshot" docstring typos**, throughout `restore_service.py` (lines 1, 154, 198, 824, 829) and `routes/snapshots.py` (~ 226, 442, 583). Sed rename from "Checkpoint snapshot" not proofread.
- **`restore_snapshot` returns `dry_run` in the body, `restore_batch` does not** ([routes/snapshots.py](../../pixlstash/routes/snapshots.py)) — same response model, different payload. Pick one.
- **`auth.py:50` adds `/scalar` to `AUTH_EXCLUDED_PATHS`** — because `is_auth_excluded_path` also matches the stripped form, `/api/v1/scalar` is also auth-bypassed. No route at that URL today; permanent landmine if someone adds one. Use a tighter matcher.
- **`generate_openapi_docs.py` is not hermetic** — `from pixlstash.server import Server` triggers `pillow_heif.register_heif_opener()` and the full vault/tagger import graph. Works today; the next torch/heif import-time side effect breaks CI docs generation.
- **"Hide lots of endpoints from API docs" (37ff30c0)** bulk-applied `include_in_schema=False` to entire routers (config, comfyui, taggers, tag_predictions, guest_scores, snapshots, filesystem, import_folders, reference_folders). Snapshots fine; `comfyui` and `tag_predictions` are user-facing — was that deliberate?

---

## Grumpy observations

- The previous review's findings landing rate is high — most C/H items were addressed. Credit where due; this is rare for a feature this size.
- That said, the prior review was generated against an older commit and several of its frontend findings were already wrong-on-arrival (the "blindly appends Z" claim, the URL-prefix claim, the `previewResourceRestore` claim, etc.). If you're regenerating reviews mid-branch, re-verify rather than carrying forward.
- The shape of the feature is right (audit trail + file swap + per-resource upsert, three layers). The execution gaps are concentrated in (a) error-path resilience (planner restart, fsync, await), (b) auth surface area on the config sibling endpoints, and (c) test coverage of the parts that actually risk data loss (migrations against real DBs, undo on non-trivial column types, real concurrent restores).
- `restore_service.py` at 1,949 lines is the kind of file where the next bug fix will silently introduce the next bug. Split it before the next major change.
- The doc work is real and well-done. The Scalar migration, the screenshots, the response_model annotations — that's a lot of work and the API page is much better for it. Just lock down the CDN reference and stop hard-coding the demo token.

---

## Recommendation

**Must-fix before 1.5.0:** C1 (real migration test from a v1.4.1 dump —
upgrades run on every user's existing DB regardless of the undo deferral).

**Pair with re-enabling undo (post-1.5.0):** D1 (UPDATE-undo silent BLOB
loss), D2 (ChangeLog atomicity test), and the other undo-only items in 🟠/🟡.

**Should-fix before merge:** H1 (planner-not-restarted), H2 (cleanup not
awaited), H3 (audit attribution), H5 (metadata_hash non-determinism — defeats
"identical snapshot" detection), H8 + H9 (Scalar CDN pin + demo token from
env).

**Everything else** is solid follow-up work. The feature is close.
