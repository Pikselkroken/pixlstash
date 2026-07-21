# Grid Refresh — Test & Cleanup Project Plan

**Status:** Draft for sign-off
**Date:** 2026-06-21
**Scope:** The image-grid live-update system (WebSocket events + the small amount of
polling), the two "pill" notifications, and the echo-suppression that distinguishes a
user's own change from an external one.

---

## 1. Problem statement

Two recurring, user-visible defects remain in the grid refresh system:

1. **Pill-on-own-change** — a "New pictures" / "View changed externally" pill appears
   after a change *the current user initiated*, when it should have been silently
   reconciled in place.
2. **Continuous refresh** — the grid keeps refreshing / reshuffling when it should be
   quiet.

**Out of scope (explicitly):** the *opposite* failure — the grid failing to refresh when
it should — which today only happens in rare, isolated cases where a new feature was never
wired into the update broadcast. We are not hunting those here, though Phase 1's coverage
matrix will incidentally surface any that exist.

**Goal:** a reproducible test harness that pins both defect classes, confirmed
root-cause fixes, and a regression net (automated + manual) so they stay fixed.

---

## 2. How the system works today (verified map)

Grounded in a read of the frontend, backend, and test code. File references are starting
points for Phase 1, not yet-confirmed defect sites.

**Echo-suppression chain (the mechanism both bugs live inside):**

1. Each browser tab generates a per-tab `clientId` (persisted in `sessionStorage` as
   `pixlstash:clientId`) — `frontend/src/stores/useWsStore.js`.
2. `apiClient` attaches `X-Client-Id: <clientId>` **only on mutating requests**
   (POST/PUT/PATCH/DELETE) — `frontend/src/services/apiClient.js`.
3. Backend middleware captures it to `request.state.origin_client_id`
   (`pixlstash/utils/request_origin.py`); mutating handlers thread it into
   `vault.notify(..., {"origin_client_id": ...})`.
4. The WS broadcaster stamps every envelope with `source`, `origin_client_id`, and
   optional `change_kind` / `fields` — `pixlstash/server.py:1315-1395`.
5. The frontend decision engine `useGridRealtimeSync.js` compares
   `origin_client_id === myClientId`: **match → suppress / targeted reconcile**,
   **mismatch or null → treat as foreign → pill or refresh**.

**The two pills** (backed by `useWsStore`):
- "New pictures" — `pendingExternalImportIds`, raised on external/foreign `added`.
- "View changed externally — click to refresh" — `sortChangedExternalIds`, raised on
  external `updated` events that affect the current sort/filter.

**Debounce / flood control that already exists** (`pixlstash/vault.py`):
- `CHANGED_TAGS` coalesces over a 2s timer (one event per burst).
- `smart_score` defers until the batch is fully scored.
- `QUALITY_UPDATED` is emitted but **not** delivered to clients (filtered in
  `_should_send_ws_update`).

**No grid polling.** The grid is fully event-driven. The only polling is the Tasks
sidebar progress (`useTasksStore`, 2s/5s), which does not touch the grid.

---

## 3. Leading hypotheses (to confirm/refute in Phase 1)

These came out of the code map and are the reason both bugs are plausible. **Do not fix
on these — verify first** (per the repo's root-cause policy).

**For pill-on-own-change:**
- **H1 — emit sites that drop `origin_client_id`.** Several `notify()` calls broadcast
  `CHANGED_PICTURES` with no origin, so the originating tab can't recognise its own echo
  and falls through to the foreign path. Suspects from the map:
  `pixlstash/routes/picture_sets.py` (~1266/1427/1546/1614),
  `pixlstash/routes/pictures/_import.py:546` (the batch `CHANGED_PICTURES`; the
  per-picture `PICTURE_IMPORTED` *does* carry origin),
  `pixlstash/routes/characters.py:754`, `pixlstash/services/plugin_service.py:188`.
- **H2 — `clientId` not present/stable at mutation time.** `sessionStorage` failure
  (private mode / disabled storage), or a race where the first mutating request fires
  before `setRequestClientId` runs → backend echoes `origin_client_id: null`.
- **H3 — origin echoed but lost in a multi-event handler.** A handler that fires
  *both* `CHANGED_CHARACTERS` and `CHANGED_PICTURES` where only one carries origin →
  the un-tagged one trips a pill.

**For continuous refresh:**
- **H4 — un-debounced high-volume events.** `PICTURE_IMPORTED` is per-picture
  (~100 events for a 100-image import); `CHANGED_DESCRIPTIONS`, `CHANGED_CHARACTERS`,
  `CHANGED_FACES`, and non-tag `CHANGED_PICTURES` have no coalescing.
- **H5 — double-emit per action.** Set/character ops emit two event types that each
  drive an independent refresh path.
- **H6 — targeted-update fallthrough.** Batches over `MAX_TARGETED_UPDATE` (50) escalate
  to a full reload; repeated mid-size foreign updates → repeated full reloads.
- **H7 — reconcile re-entrancy.** A reconcile GET that (directly or via a watcher) marks
  another refresh pending.

---

## 4. Phases

### Phase 1 — Thorough review & root-cause (the foundation)

**Primary artifact: the Origin & Debounce Coverage Matrix.** One row per `vault.notify`
emit site (the map already enumerates ~30). Columns:

| emit site (file:line) | event type | wire type | `change_kind` | carries `origin_client_id`? | debounced/coalesced? | user-reachable path? | frontend handler branch | verdict |

This mirrors the repo's security coverage-matrix discipline: **completeness is arithmetic,
not judgement.** Every user-reachable mutating emit site that does *not* carry origin is a
pill-on-own-change candidate; every high-frequency site with no coalescing is a
continuous-refresh candidate. Empty/▲ cells are the bug list.

Tasks:
- Build the matrix (backend) and the mirror decision-table for `useGridRealtimeSync`
  (frontend) — every `(source, change_kind, origin?)` combination → branch → outcome.
- Confirm `clientId` lifecycle: generated before first mutation, persisted, attached to
  every mutating request, survives reload; behaviour when `sessionStorage` is unavailable.
- Classify each hypothesis H1–H7 as confirmed / refuted / needs-test, with evidence.

Output: a findings section appended to this doc + the filled matrix.
**Skills:** `senior-backend-developer` (emit sites, vault, server broadcast) +
`senior-frontend-developer` (decision engine, clientId, pills), run in parallel.

### Phase 2 — Playwright test harness

The existing harness (`frontend/e2e/`) boots a real backend against a fixed fixture with a
saved owner session, single worker, one origin — solid foundation. Three gaps must close:

1. **Simulating an *external* change deterministically.** The `apiContext` fixture sends
   requests with **no `X-Client-Id`** → the backend already treats those as external. This
   is the clean lever to assert "external change → pill appears" without a second browser.
2. **Simulating *own* changes.** Drive the mutation through the UI under test (which sends
   `X-Client-Id`) and assert **no pill** + in-place reconcile. A second browser context
   covers the foreign-tab (same-owner, different client) case.
3. **Background-worker floods.** The e2e backend sets `disable_background_workers: true`,
   so tag/quality/smart_score/face events never fire — exactly the events behind most
   continuous-refresh reports. **Decision needed (see §6):** add a guarded, e2e-only
   **test event-injection endpoint** that calls `vault.notify` with a controlled payload,
   so tests can fire any event (external add, 100-id tag flood, own-origin echo, double
   emit) deterministically and without real timing nondeterminism. Strongly recommended
   over enabling real workers (which is slow and flaky).

Plus: a **WebSocket sniffer fixture** (`page.on('websocket')` → assert frames carry
`origin_client_id` where expected, and count how many refresh-driving frames arrive per
action — the direct measurement for "continuous refresh").

New test surface:
- `data-testid` hooks on both pills and the grid container (currently class-only:
  `.pending-imports-pill`) — small frontend change, makes assertions robust.
- `GridPage` extensions: `pendingImportsPill()`, `sortChangedPill()`, pill-count helpers.
- Specs: own-change-no-pill (upload/tag/rate/delete via UI), external-change-raises-pill
  (via `apiContext`), flood-coalescing (via injection endpoint → assert ≤N refreshes),
  overlay-deferral (changes during overlay open → no pill until close), double-emit.

**Skills:** `qa-tester` (owns harness/specs/config) with `senior-frontend-developer`
(test-ids) and `senior-backend-developer` (injection endpoint).

### Phase 3 — Manual tests (complement)

Add a "Grid live-update" section to `docs/release-test-plan.md` and catalogue it in
`docs/regular-tests.md`, covering what's hard to automate deterministically:
- Real two-device / two-tab observation during a live import with background workers ON.
- Subjective "does the grid feel quiet?" during a bulk tag/score sweep.
- Reconnect behaviour after network drop (events lost during downtime — known gap).
- Private-window / storage-disabled `clientId` fallback (H2).
**Skill:** `qa-tester`.

### Phase 4 — Evaluation of the tests

A test that doesn't catch the bug is theatre. For each new automated test:
- **Bug-catching validation:** confirm it *fails* against the un-fixed code (or a
  deliberately reverted fix), then *passes* after the fix. This is the acceptance gate.
- **Flakiness:** run the new specs ≥20× (and under CI's single-worker config); zero
  intermittent failures before they're considered done.
- Record coverage delta in `docs/regular-tests.md` (which release-plan rows are now
  automated).
**Skill:** `qa-tester` + `ci-expert` (CI integration, repeat-run job).

### Phase 5 — Log issues

For every confirmed defect from Phase 1 (and any found while building tests): one GitHub
issue, tagged `bug`, with a minimal repro (ideally the failing Playwright spec), the
matrix cell it corresponds to, and the hypothesis it confirms. No fixes bundled into the
logging step — issues first, so scope is visible.
**Skill:** `qa-tester` (files via `gh`).

### Phase 6 — Fix issues

Fix in priority order (own-change pills first — most visible), each as a focused change
with its regression test going green:
- **Tactical:** thread `origin_client_id` into the un-tagged emit sites; add coalescing to
  the flood-prone events; collapse double-emits.
- **Strategic (flag for discussion, don't silently inline):** the recurring root cause is
  *per-handler opt-in* origin tagging — the same shape as the BOLA class the repo fought.
  Consider a single choke point that stamps `origin_client_id` onto every broadcast from
  request context automatically, so an emit site is correct *by omission*. Mirror of the
  centralised-authz direction in `docs/backend_architecture.md`.
**Skills:** `senior-backend-developer` + `senior-frontend-developer`, reconciled against
`docs/integration_architecture.md`. Any change touching WS auth/origin trust → gate
through `chief-security-officer` before merge.

---

## 5. Sequencing & deliverables

```
Phase 1 (review)  ─┬─►  Phase 2 (harness) ─►  Phase 4 (eval) ─►  Phase 5 (issues) ─►  Phase 6 (fix)
                   └─►  Phase 3 (manual)  ──────────────────────────────────────────►
```

Phase 1 gates everything (no fixing before root cause). Phases 2 and 3 can run in
parallel once the matrix exists. Phase 4 validates Phase 2. Phase 6 closes Phase 5's
issues, each behind a green test.

Deliverables: this doc with the filled matrix + findings; new Playwright specs + harness
additions (injection endpoint, WS sniffer, test-ids); expanded manual test docs; GitHub
issues; merged fixes with passing regression tests.

---

## 6. Decisions (signed off 2026-06-21)

1. **Background-flood simulation:** ✅ **Guarded e2e-only `vault.notify` injection
   endpoint** (deterministic, fast). Must be unreachable outside the e2e config.
2. **Phase 6 fix depth:** ✅ **Tactical patches now** (thread `origin_client_id` into the
   un-tagged emit sites; add coalescing; collapse double-emits) **plus a scoped design
   note** for the centralised origin-stamping choke point (build next, not inline).
3. **Scope:** "grid fails to refresh" stays out of scope beyond whatever the Phase 1
   matrix incidentally surfaces.

---

## 7. Phase 1 findings (2026-06-21) — root cause confirmed

Built from a full read of every `vault.notify`/`emit_event` site (backend) and the
complete `useGridRealtimeSync` decision table + `clientId` lifecycle (frontend). Both
defect classes are **confirmed and explained**. Headline: the frontend is *correct given a
correctly-stamped envelope* — it defaults an event to `source:"external"` whenever both
`origin_client_id` and `source:"ui"` are absent (`useGridRealtimeSync.js:38-46`, `:269`).
The bugs are backend emit sites that drop those fields, plus missing coalescing on both
sides.

### Correction to §2's assumptions
- **`CHANGED_TAGS` 2s coalescing applies to the *background* path only.** The coalescer
  (`vault.py:866`) is reached just from the tagger worker and ref-folder caption updates.
  **Every route-level `CHANGED_TAGS` emit (`tags.py`, `tag_suggestions.py`,
  `tag_predictions.py`) calls `notify()` directly and is NOT coalesced.**
- **`PICTURE_IMPORTED` is one event per import batch, not per-picture** — the "~100 events
  per 100-image import" hypothesis is *refuted*; there is no per-picture import loop.
- **Origin is read from the event `data` dict only**, never the contextvar (the broadcaster
  runs on a different thread — `server.py:1262-1288`). So a bare-enum / bare-list emit is
  *structurally* origin-less; threading origin means putting it in the dict.

### Confirmed root causes — pill-on-own-change (ranked, all user-reachable)
`origin_client_id` dropped at the emit site → own change classified external → pill:
1. **`plugin_service.py:188`** — bare `CHANGED_PICTURES, output_ids` paired with an
   origin-tagged `PICTURE_IMPORTED` for the *same* ids → guaranteed self-pill every plugin
   run (H1+H5, cleanest repro).
2. **`_import.py:546` / `:571`** — bare `CHANGED_PICTURES` on every import (origin sits
   unused in scope 10 lines above).
3. **`picture_sets.py:1266/1427/1546/1614`** (+ `:1429/1474` characters) — every set
   membership/project mutation self-pills; handlers never even read origin.
4. **`characters.py:752+754`** — update-character double-emit, *neither* event tagged
   (H1+H3).
5. **`import_folders.py:201/237/263`** (file reads origin 0 times) +
   **`reference_folders.py:621/787/1404`** — folder CRUD self-pills.
6. **`tag_suggestions.py:130/311` + `tag_predictions.py:244`** — tag-fix / reset-tags
   self-pill (uncoalesced `CHANGED_TAGS`, no origin).
7. **`vault.py:626`** — reset-description self-pills; needs an origin param added to
   `reset_description_interactive`.

**Reference (correct) pattern already in-repo:** `tags.py`, all of `_crud.py`, and
`characters.py` face assign/unassign (`:1444-1524`) thread origin correctly — copy them.

Secondary, environment-gated: **H2 storage-denial** — in a private window / disabled
`sessionStorage`, `clientId` regenerates on every reload (`useWsStore.js:27-35`), so
in-flight echoes for pre-reload mutations mismatch and pill. Real but narrow; covered by a
Phase 3 manual test. All other H2 sub-claims **refuted** (no mutating path bypasses
`X-Client-Id`; single axios client; no mutating raw `fetch`).

### Confirmed root causes — continuous-refresh (ranked)
1. **`tag_suggestions.py:130` (`_notify_changed`)** — loops and emits one *uncoalesced*
   `CHANGED_TAGS` **per picture id** on bulk accept/reopen → 100 pictures = 100 frames.
   Worst flood.
2. **Route-level `CHANGED_TAGS` bypasses the coalescer entirely** — interactive tag edits
   each fire immediately.
3. **No frontend coalescing of incoming WS picture events** (H4 confirmed) — N foreign
   events → N `insertGridImagesById` / per-id `applyTargetedUpdate` fetch+rebuild cycles.
   Only the tag path and sidebar refresh are throttled.
4. **`CHANGED_DESCRIPTIONS` / `CHANGED_CHARACTERS` / `CHANGED_FACES`** — no coalescing;
   background description/face sweeps + the post-face-extract pending-assignment pair
   (`vault.py:860+861`) churn the grid/sidebar.
5. **H6 confirmed (partially mitigated):** foreign batches >50 ids escalate to a full
   `fullGridReload`; a 1200ms `gridVersion` throttle (`ImageGrid.vue:3774`) collapses tight
   bursts, but batches spaced >1.2s apart cause repeated whole-grid refetches.

**H7 (reconcile re-entrancy loop) refuted** — grid GETs never re-arm a refresh watcher.

### Incidental "fails to refresh" flag (out of scope, noted)
`RESTORE_COMPLETED` serializes to a distinct `restore_*` wire type, not a `pictures_changed`
event — verify the grid actually refreshes after a restore. Logged for awareness only.

### Phase 2 input: test hooks
**Zero `data-testid` in `frontend/src/`.** Both pills share the class
`pending-imports-pill` (`ImageGrid.vue:366` and `:379`) — Playwright cannot distinguish
them without text matching. Phase 2 must add distinct testids to each pill and to the grid
container before specs can assert *which* pill appeared.

> The full ~59-row emit-site matrix lives in the Phase 1 backend agent transcript; the
> ranked causes above are its actionable distillation. Regenerate the table into this doc
> if a reviewer wants the per-row audit inline.

---

## 8. Phase 2 + initial Phase 4 results (2026-06-21)

**Harness built (working tree, not committed):**
- Guarded test endpoint `POST /api/v1/test-hooks/ws-event` (`pixlstash/routes/test_hooks.py`)
  — registered only when server-config `enable_test_hooks: true`; off (route absent, 404)
  by default; owner-only; the e2e launcher sets the flag. **Needs `chief-security-officer`
  sign-off before merge** (it emits arbitrary WS events).
- `data-testid`s: `pending-imports-pill`, `sort-changed-pill`, `image-grid` (ImageGrid.vue).
- WS sniffer + `readClientId` helpers in `e2e/fixtures/test.js`; pill helpers in
  `e2e/pages/GridPage.js`. (Sniffer must attach in `beforeEach` before `goto`, not as a
  fixture — a fixture-attached listener missed the socket on the 2nd+ test of a file.)
- 4 new specs: `grid-own-change-no-pill`, `grid-external-change-pill`, `grid-injection`,
  `grid-overlay-deferral`.

**Run (stable across 3 runs): 6 pass, 4 expected-fail — each fail = a confirmed bug:**

| Spec assertion | Result | Pins |
|---|---|---|
| `PATCH /pictures/{id}` own X-Client-Id → no pill (positive control) | ✅ pass | correct origin-threading reference |
| `PATCH /pictures/{id}` external → "changed externally" pill | ✅ pass | positive direction |
| `tag_predictions` delete external → pill | ✅ pass | positive direction |
| injection own-origin → suppressed | ✅ pass | frontend echo-suppression correct |
| injection external `added` → "New pictures" pill | ✅ pass | positive direction |
| overlay open + external update → no pill, deferred refetch on close | ✅ pass | deferral contract intact |
| `tag_predictions` delete own → frame had `origin_client_id: null` | ❌ EXPECTED | dropped-origin (`tag_predictions.py:216`) |
| `tag_predictions/{tag}/reject` own → `origin_client_id: null` | ❌ EXPECTED | dropped-origin (`tag_predictions.py:193`) |
| `picture_sets/{id}/members` add own → `origin_client_id: null` | ❌ EXPECTED | dropped-origin (`picture_sets.py:1546`) |
| 100× external flood → **100** `/pictures/metadata` refetches (bound 15) | ❌ EXPECTED | no frontend coalescing |

The four failures are real assertions (not `test.fixme`), each commented with the Phase 6
fix that flips it green. This is the Phase 4 pre-fix baseline.

**Env note for runners:** `.venv` had pixlstash installed non-editable; re-ran
`pip install -e . --no-deps`. Specs run with
`PIXLSTASH_PYTHON=/home/glindkvist/Projects/pixlstash/.venv/bin/python`.

---

## 9. Phase 6 fixes + final evaluation (2026-06-21)

**#499 (pill-on-own-change) — backend origin threading.** Threaded
`origin_client_id` into the event data dict at every confirmed user-reachable emit
site (adding `request: Request` where the handler lacked it): `plugin_service.py:188`,
`_import.py:546/571`, `picture_sets.py:1266/1427/1429/1474/1546/1614`,
`characters.py:752/754/806/1266`, `import_folders.py:201/237/263`,
`reference_folders.py:621/787/1404`, `tag_suggestions.py:130/311`,
`tag_predictions.py:193/216/244`, and `vault.py:626` (`reset_description_interactive`
gained an origin param threaded from its route). Background/worker emits left alone
(genuinely origin-less). `ruff` clean.

**#500 (continuous refresh) — coalescing on both sides.**
- Backend: `tag_suggestions._notify_changed` now emits **one** batched `CHANGED_TAGS`
  (all ids + origin) instead of one-per-id. Kept it a *direct* emit, **not** routed
  through the origin-less background coalescer (that would re-introduce the #499
  self-pill).
- Frontend: added a **200ms coalescing window** in `useGridRealtimeSync` that
  accumulates added/updated/removed ids and pill ids, flushing batched grid ops once
  per window. Decision logic (own-origin suppression, overlay deferral, pill choice,
  view-affecting checks, `MAX_TARGETED_UPDATE` escalation) is unchanged — only
  side-effect timing moved behind the window. 5 new unit tests; all 150 frontend unit
  tests pass.

**Design note** for the centralised origin-stamping choke point added to
`docs/backend_architecture.md` §15 ("Aspirational: centralised origin-stamping
chokepoint — NOT YET IMPLEMENTED"). Strategic direction, not implemented.

**Final evaluation (Phase 4):** the 4 previously-failing specs now pass; the 6 controls
hold. Grid specs stable across `--repeat-each=3` (30/30). Full e2e suite **36/36**, no
regressions. (One transient `tags.spec` flake on a mid-run pass was pre-existing —
passed in isolation and on re-run; unrelated to coalescing.) Stale "EXPECTED FAIL"
spec titles/messages renamed to regression-guard wording referencing #499/#500.

A determinism bug in the new `picture_sets` spec was fixed during evaluation: it now
queries `GET /picture_sets/{id}/members` (returns `{picture_ids:[...]}`) and adds only
non-members, so it's independent of shared-backend state and re-runs.

## 10. Open security follow-ups (before merge)

1. **`POST /api/v1/test-hooks/ws-event` — ✅ APPROVED by `chief-security-officer`**
   (`docs/reviews/grid-refresh-security-signoff.md`). Off by default (route absent unless
   `enable_test_hooks` is set; only the e2e launcher sets it), owner-only via
   `require_unscoped_owner`, no exfil surface, `repeat<=500` adequate. Non-blocking
   hardening suggestion: a boot-time guard refusing to register it under a production
   marker. Cleared for merge.
2. **Pre-existing BOLA-class gap — filed as #504** (`bug`+`security`). The review found
   **five** (not four) `/pictures/{id}/...` mutators in `tag_predictions.py` with no
   `enforce_picture_scope`: `confirm_tag_prediction:162`, `reject_tag_prediction:185`,
   `delete_tag_predictions:213`, `reset_picture_tags:244`, `reset_picture_description:278`.
   **Medium-latent, NOT currently exploitable** — scoped tokens are READ-only and the
   middleware 403s their writes before the handler, so no live BOLA today; the risk is
   structural (unscoped-by-omission). Mutate-only, no read-leak. Predates this work; not a
   release blocker on its own. Full trace in the signoff doc.
```
