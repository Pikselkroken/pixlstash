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
cd frontend
npm ci
npx playwright install chromium      # one-time
npm run test:e2e                     # or: npm run test:e2e:ui
```

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

## Writing specs

- Specs run with an authenticated **owner** session (full read-write), so they
  may exercise mutations (scoring, tagging, editing), not just browsing.
- Assert "at least one" rather than exact counts so fixture pruning doesn't
  break tests (see `specs/grid.spec.js`).
- Stable selectors in use: `.thumbnail-card` (grid cell), `.image-overlay` /
  `.overlay-close` (lightbox), `.search-overlay` (search), `.mdi-magnify`
  (toolbar search button).
```
