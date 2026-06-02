# Playwright end-to-end tests

These tests drive the **real SPA against a real PixlStash backend** in a
headless browser. They complement the Vitest unit tests (pure utils) and the
pytest API suite (backend) by covering full user journeys through the UI.

## How it works

```
playwright.config.js
  └─ webServer:  npm run build  &&  python e2e/serve_e2e_backend.py
                 (builds the SPA, then boots a backend that serves it
                  + the API on one origin → cookie auth, no CORS)
  └─ globalSetup: e2e/global-setup.js
                 (registers an admin with a per-run random password,
                  mints an ALL-scope token, suppresses first-run dialogs,
                  saves the owner session to e2e/.auth/state.json)
  └─ specs:      e2e/specs/*.spec.js  (run with the saved session)
```

### The fixture (`test-data/`)

The backend boots against the repo's `test-data/` fixture — a seeded vault
(`test-data/images/vault.db` + image files) in the same shape as `demo-data/`.

`serve_e2e_backend.py` **never modifies `test-data/`**. On each run it:

1. copies the fixture images + vault into a throwaway temp dir
   (`<tmp>/pixlstash-e2e`, recreated every launch, backups excluded);
2. **strips the `user` / `usertoken` rows** so the server boots in *first-run*
   state — this is why no credential is ever committed;
3. writes a lean `server-config.json` (background workers + startup-thumbnail
   generation disabled, CPU only, password auth on);
4. execs `python -m pixlstash.app` against that temp copy.

Because the user table is stripped per run, `global-setup.js` registers a fresh
admin (random password) and mints an `ALL` token — credentials live only for
the duration of the run. **Do not commit `test-data/token.txt`**; it is unused.

## Running locally

```bash
# from the repo root, with the pixlstash venv active so `python` resolves:
pip install -e .                     # IMPORTANT: see note below
cd frontend
npm ci
npx playwright install chromium      # one-time
npm run test:e2e                     # or: npm run test:e2e:ui
```

> **Install pixlstash editable.** The backend is launched as `python -m
> pixlstash.app` from the `frontend/` working directory, so Python resolves
> `pixlstash` from site-packages — *not* your working tree. A non-editable
> install (`pip install .`) bakes a stale copy that ignores your local edits
> (e.g. you'll see the old rate-limit behaviour). `pip install -e .` makes the
> repo the live source. CI is unaffected: it `pip install`s the checked-out
> commit, so the installed code already matches.

The e2e backend sets `disable_rate_limit: true` (the suite reloads the SPA many
times in quick succession; the global limiter of 120 public requests / 60s
would otherwise 429 later navigations).

Useful env vars:

| Var | Default | Purpose |
|-----|---------|---------|
| `PIXLSTASH_PYTHON` | `python` | Interpreter for the backend (set to your venv's python if `python` isn't it). |
| `PIXLSTASH_E2E_PORT` | `9600` | Port the test backend binds. |

Each run boots a fresh backend (`reuseExistingServer: false`), so there is a
one-time SPA build (~20 s) + server boot (~5 s) per invocation.

## CI

The `e2e` job in `.github/workflows/ci.yml` runs these tests, but **skips
cleanly when `test-data/` is not committed** — so CI stays green until the
pruned fixture lands, then activates automatically.

## Layout

```
e2e/
  fixtures/test.js   extended `test` — apiContext (ALL token), credentials,
                     grid/overlay/settings page-object fixtures, and
                     loginToFreshSession() for the isolated logout spec
  pages/             thin page objects (locators + actions, no assertions):
                     GridPage, ImageOverlay, SettingsDialog
  specs/             one file per release-test-plan section:
                     grid.spec.js (smoke), auth (§1), grid-browse (§3),
                     overlay (§4), faces (§10), stacks (§11)
```

Each spec maps to a section of `docs/release-test-plan.md` so the manual
checklist shrinks as coverage grows.

## Writing specs

- Specs run with an authenticated **owner** session (full read-write), so they
  may exercise mutations (scoring, tagging, editing), not just browsing.
- Assert "at least one" / relative deltas rather than exact counts so fixture
  pruning doesn't break tests (see `specs/grid.spec.js`).
- Pictures are identified by their thumbnail `<img>` src
  (`.../thumbnails/<id>.webp`) — the only per-card picture id the grid renders.
- Selectors are CSS/ARIA-first; prefer stable classes and `title`/`aria-label`.
- The backend boots ONE throwaway DB per `playwright test` invocation (shared
  across specs in a run), so keep mutations additive/idempotent. The logout
  spec mints an isolated session (`loginToFreshSession`) because the backend
  tracks sessions in-memory and logout would otherwise pop the shared session.
- Stable selectors in use: `.thumbnail-card` (grid cell), `.image-overlay` /
  `.overlay-close` (lightbox), `.search-overlay` (search), `.mdi-magnify`
  (toolbar search button).
```
