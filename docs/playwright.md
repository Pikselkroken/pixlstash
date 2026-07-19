# PixlStash Playwright End-to-End Tests

> **Document purpose:** Reference for the Playwright e2e suite — what it covers,
> how it is wired, how to run it, and how to add new specs. The canonical
> README lives at [`frontend/e2e/README.md`](../frontend/e2e/README.md); this
> document is the higher-level companion. Keep both in sync when the harness
> changes.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Coverage](#2-coverage)
3. [Architecture](#3-architecture)
4. [Running Locally](#4-running-locally)
5. [CI](#5-ci)
6. [Adding New Tests](#6-adding-new-tests)
7. [Conventions](#7-conventions)

---

## 1. Overview

The Playwright suite drives the **real SPA against a real PixlStash backend** in
a headless browser, covering full user journeys through the UI. It complements
the other two test layers:

| Layer | Tool | Scope |
|-------|------|-------|
| Backend API | pytest | `pixlstash/` server + DB |
| Frontend units | Vitest | pure utils / pure logic |
| **End-to-end** | **Playwright** | **the built SPA + a live backend, one origin** |

There are **21 specs** today, all green locally. Each spec maps to a section of
[`docs/release-test-plan.md`](release-test-plan.md), so the manual release
checklist shrinks as e2e coverage grows.

## 2. Coverage

| Release-plan section | Spec | What it checks |
|----------------------|------|----------------|
| Smoke | [`grid.spec.js`](../frontend/e2e/specs/grid.spec.js) | Reference spec: render, overlay open/close, search toggle |
| §1 Authentication | [`auth.spec.js`](../frontend/e2e/specs/auth.spec.js) | Session persists across reload, logged-out redirect, token generation, logout |
| §3 Grid & browsing | [`grid-browse.spec.js`](../frontend/e2e/specs/grid-browse.spec.js) | Render, sort + direction flip, column reflow, search → history → reset |
| §3.5 Context menu | [`context-menu.spec.js`](../frontend/e2e/specs/context-menu.spec.js) | Right-click a card opens the context menu listing Tag / Reverse image search / Share image; Escape dismisses |
| §4 Picture detail | [`overlay.spec.js`](../frontend/e2e/specs/overlay.spec.js) | Overlay open/close, arrow-key next/prev, Escape close |
| §4 Picture-set locking | [`set-locking.spec.js`](../frontend/e2e/specs/set-locking.spec.js) | Lock a set → grid lock badge + Tag action disabled; unlock restores tagging |
| §5 Tags | [`tags.spec.js`](../frontend/e2e/specs/tags.spec.js) | Add a tag via the inline input, remove it via its ✕ chip |
| §6 Star rating | [`rating.spec.js`](../frontend/e2e/specs/rating.spec.js) | Set a star rating in the overlay; it round-trips and survives a reload |
| §7/§8/§9 Sets / Projects / Characters | [`entities.spec.js`](../frontend/e2e/specs/entities.spec.js) | Sidebar set/character rows filter the grid; Projects tab opens a project |
| §10 Faces | [`faces.spec.js`](../frontend/e2e/specs/faces.spec.js) | Face crops in the side panel + bounding-box overlay toggle |
| §11 Stacks | [`stacks.spec.js`](../frontend/e2e/specs/stacks.spec.js) | Expand-all / collapse-all from the View menu |
| Statistics sidebar | [`stats.spec.js`](../frontend/e2e/specs/stats.spec.js) | Toolbar toggle shows/hides the stats sidebar with its Tags/Pictures/Tasks tabs |
| Boolean set operations | [`set-operations.spec.js`](../frontend/e2e/specs/set-operations.spec.js) | Multi-select sets reveals Union / Overlap / Difference / Unique; clearing dismisses |
| Sharing | [`sharing.spec.js`](../frontend/e2e/specs/sharing.spec.js) | Context menu → Share image → Create Link mints a read-only URL |
| Snapshots | [`snapshots.spec.js`](../frontend/e2e/specs/snapshots.spec.js) | Settings → Snapshots lists ≥1 restore point with a Restore action (list-only) |
| §19 Grid live-update | [`grid-own-change-no-pill.spec.js`](../frontend/e2e/specs/grid-own-change-no-pill.spec.js), [`grid-external-change-pill.spec.js`](../frontend/e2e/specs/grid-external-change-pill.spec.js), [`grid-injection.spec.js`](../frontend/e2e/specs/grid-injection.spec.js), [`grid-overlay-deferral.spec.js`](../frontend/e2e/specs/grid-overlay-deferral.spec.js) | WebSocket refresh: own change raises no pill, external raises the right pill, injection origin-suppression, flood coalescing, overlay deferral |
| §20 Review Sessions | [`review-board.spec.js`](../frontend/e2e/specs/review-board.spec.js) | Tag-health board (9 cases): open, render columns, rebuild control, Why column, filter, sort, anomalies toggle, Start-review row (drives the real `tag_health` cache) |
| §20 Review Sessions | [`review-session.spec.js`](../frontend/e2e/specs/review-session.spec.js) | Session loop (7 cases): create, binary Yes/No mapping + tally + backend receipt, and Escape-close run green (**BUG-RS-1 render crash resolved**); four further cases — Undo, Skip, keyboard, queue completion/archive — stay `test.fixme`'d for reasons unrelated to BUG-RS-1 (see docs/regular-tests.md §Review Sessions) |

Still manual (candidates for future specs): §14 Tag predictions, §15 Export,
and the Selection ▾ ↔ context-menu parity comparison (#403).

## 3. Architecture

```
playwright.config.js
  └─ webServer:   npm run build  &&  python e2e/serve_e2e_backend.py
                  (builds the SPA, then boots a backend that serves both the
                   SPA and the API on one origin → cookie auth, no CORS)
  └─ globalSetup: e2e/global-setup.js
                  (registers an admin with a per-run random password, mints an
                   ALL-scope token, suppresses first-run dialogs, saves the
                   owner session to e2e/.auth/state.json)
  └─ specs:       e2e/specs/*.spec.js  (run with the saved session)
```

### The fixture

The backend boots against the repo's committed `test-data/` fixture — a
**sanitized** seed vault (`test-data/images/vault.db` + image files): 111
pictures, 115 faces, 4 characters, 6 sets, 3 projects, 10 stacks, ~2483 tag
predictions. User/token rows are stripped from git history, so no credentials
are ever committed.

[`serve_e2e_backend.py`](../frontend/e2e/serve_e2e_backend.py) **never modifies
`test-data/`**. On each run it:

1. copies the fixture images + vault into a throwaway temp dir
   (`<tmp>/pixlstash-e2e`, recreated every launch);
2. **strips the `user` / `usertoken` rows** so the server boots in *first-run*
   state — this is why no credential is ever committed;
3. writes a lean `server-config.json` (background workers + startup-thumbnail
   generation disabled, CPU only, password auth on, **rate limiter disabled**);
4. execs `python -m pixlstash.app` against that temp copy.

Because the user table is stripped per run, `global-setup.js` registers a fresh
admin (random password) and mints an `ALL` token — credentials live only in the
gitignored `.auth/` dir for the duration of the run.

> The rate limiter is disabled on the e2e backend because the suite reloads the
> SPA many times in quick succession; the global limiter (120 public requests /
> 60s) would otherwise 429 later navigations. This is driven by the
> configurable limiter in `pixlstash/utils/rate_limiter.py`
> (`disable_rate_limit` / `rate_limit_max_requests` / `rate_limit_window_seconds`),
> whose defaults are unchanged for production.

### Layout

```
e2e/
  fixtures/test.js   extended `test` — apiContext (ALL token), credentials,
                     grid/overlay/settings page-object fixtures, and
                     loginToFreshSession() for the isolated logout spec
  pages/             thin page objects (locators + actions, no assertions):
                     GridPage, ImageOverlay, SettingsDialog
  specs/             one file per release-test-plan section
  global-setup.js    registers admin, mints token, saves session state
  serve_e2e_backend.py  the throwaway-backend launcher
```

## 4. Running Locally

From the repo root, **with the pixlstash venv active** so `python` resolves:

```bash
pip install -e .                     # IMPORTANT: editable — see note below
cd frontend
npm ci
npx playwright install chromium      # one-time
npm run test:e2e                     # headless — or:
npm run test:e2e:ui                  # interactive Playwright UI mode
```

> **Install pixlstash editable.** The backend is launched as
> `python -m pixlstash.app` from `frontend/`, so Python resolves `pixlstash`
> from site-packages — *not* your working tree. A non-editable install
> (`pip install .`) bakes a stale copy that ignores local edits (e.g. you'll see
> the old rate-limit behaviour). `pip install -e .` makes the repo the live
> source. CI is unaffected: it installs the checked-out commit, so the installed
> code already matches.

Each run boots a fresh backend (`reuseExistingServer: false`), so expect a
one-time SPA build (~20 s) + server boot (~5 s) per invocation. The suite runs
single-worker (`workers: 1`) against one shared, mutable backend — tests must
not race.

Useful env vars:

| Var | Default | Purpose |
|-----|---------|---------|
| `PIXLSTASH_PYTHON` | `python` | Interpreter for the backend (set to your venv's python if `python` isn't it). |
| `PIXLSTASH_E2E_PORT` | `9600` | Port the test backend binds. |

## 5. CI

The `e2e` job in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs
the suite on Ubuntu. It detects the fixture and **skips cleanly when
`test-data/images/vault.db` is not committed**, so CI stays green if the fixture
is ever removed. The Playwright HTML report is uploaded as an artifact
(`playwright-report`, 7-day retention) on every run, pass or fail.

## 6. Adding New Tests

The pattern is consistent. To cover a new release-plan section:

1. **Create** `frontend/e2e/specs/<section>.spec.js`, importing the extended
   fixture (not raw `@playwright/test`):

   ```js
   import { test, expect } from '../fixtures/test.js'

   test.describe('tags (§5)', () => {
     test.beforeEach(async ({ grid }) => {
       await grid.goto()
     })

     test('adds a tag to a picture', async ({ overlay, apiContext }) => {
       await overlay.openFromGrid()
       // ...drive the UI, then optionally assert backend truth via apiContext
     })
   })
   ```

2. **Add selectors to a page object** under `e2e/pages/` rather than inlining
   them, so a future Vue refactor touches only one file. For a new surface, add
   a new page object and wire it into [`fixtures/test.js`](../frontend/e2e/fixtures/test.js)
   as a fixture (like `grid` / `overlay` / `settings`).

Fixtures available to every spec (from
[`fixtures/test.js`](../frontend/e2e/fixtures/test.js)):

| Fixture | Use |
|---------|-----|
| `grid`, `overlay`, `settings` | Page objects bound to the current page |
| `apiContext` | ALL-scope API request context — read entity IDs, assert backend truth after a UI mutation |
| `credentials` | `{ token, username, password }` minted this run |
| `loginToFreshSession(browser, baseURL)` | Mints an isolated session for specs that invalidate their own (e.g. logout) |

## 7. Conventions

- **Authenticated owner.** Specs run as a full read-write owner, so they may
  exercise mutations (scoring, tagging, editing), not just browsing.
- **Relative assertions.** Assert "at least one" / relative deltas rather than
  exact counts, so fixture pruning doesn't break tests.
- **Identify pictures by thumbnail src.** Pictures are identified by their
  thumbnail `<img>` src (`.../thumbnails/<id>.webp`) — the only per-card picture
  id the grid renders to the DOM.
- **CSS/ARIA-first selectors.** Prefer stable classes and `title` / `aria-label`.
  Stable selectors already in use: `.thumbnail-card` (grid cell),
  `.image-overlay` / `.overlay-close` (lightbox), `.search-overlay` (search),
  `.mdi-magnify` (toolbar search button).
- **One shared backend per run.** The backend boots ONE throwaway DB per
  `playwright test` invocation (shared across specs), so keep mutations
  additive/idempotent. Use `loginToFreshSession()` for anything that invalidates
  its own session — the backend tracks sessions in-memory and logout would
  otherwise pop the shared session and break other specs.
