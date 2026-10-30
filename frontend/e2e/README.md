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
3. writes a lean `server-config.json` (startup-thumbnail generation disabled,
   CPU only, password auth on). The task runner runs, so an import or a folder
   scan a spec starts completes, but `e2e_planner_finders` restricts the work
   planner to the reference-folder scan, the import-folder pickup and the face
   finder the import routes require. No inference engine is built, so the face
   finder queues nothing, and the sweeps that would rewrite the fixture (tags,
   embeddings, scores, thumbnails, dedup) never run;
4. lays out a small model folder (`<work dir>/model-shelf`: stub LoRAs and a
   checkpoint in nested subfolders, an empty one, a preview `.png`) and names it
   in the config as `e2e_model_folder`. With `enable_test_hooks` on, the server
   registers it and scans it synchronously at start-up, so the Models shelf has
   rows although the model-folder scan is off (`model-shelf-fixture.spec.js`);
5. lays out a reference folder (`<work dir>/reference-folder/Inbox`: three
   flat images) and names it as `e2e_reference_folder`. The server registers it
   with the default layout, scans it, moves `Inbox/fern.png` into a folder named
   after the fixture's first project and scans again, so `/moves` has one
   pending review (`media-fixture.spec.js`). A spec that registers a folder of
   its own uses a `mkdtemp` folder: Delete forever with *include protected*
   removes reference-folder originals from disk;
6. names `<work dir>/user-data` in the config as `e2e_data_dir`. With
   `enable_test_hooks` on, the workflow inbox, the user workflow folder and the
   trash move there, so a run neither imports the machine's own workflows nor
   writes into or trashes from the folders the owner's server uses;
7. writes a workflow seed (`<work dir>/workflow-seed`: `seed.json` and PNGs
   carrying a ComfyUI `prompt` graph) and names it as `e2e_workflow_seed`.
   With `enable_test_hooks` on, the server imports it at start-up through the
   import, picture-import and recipe paths, and runs the ComfyUI extraction and
   card backfill synchronously. `GET /workflows` then lists *Seeded LoRA
   portrait* (a checkpoint and two LoRAs, all on the seeded shelf), *Seeded
   plain txt2img*, and a workflow known from three pictures; *Seeded hidden
   draft* is hidden and an img2img picture is a one-off. `GET /recipes` lists
   two recipes on the LoRA workflow and one unfiled
   (`workflows-seed.spec.js`). The pictures carry the fixture's median date,
   so they sit mid-grid in either date order and the specs that open, rate or
   tag the first or last few pictures still get fixture ones;
8. execs `python -m pixlstash.app` against that temp copy.

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
                     grid/lightbox/settings page-object fixtures, and
                     loginToFreshSession() for the isolated logout spec
  pages/             thin page objects (locators + actions, no assertions):
                     GridPage, Lightbox, SettingsDialog
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
- A route fake is a copy of a server answer that nothing keeps current. The
  Workflows run fakes (`fixtures/workflowRunFakes.json`, read by
  `specs/workflow-run.spec.js`) are held against the server's own models and
  producers by `tests/test_e2e_workflow_run_fakes.py`; change a fake's values
  in a spec, never its keys.
- Selectors are CSS/ARIA-first; prefer stable classes and `aria-label` (a native `title` survives only to reveal clipped text).
- The backend boots ONE throwaway DB per `playwright test` invocation (shared
  across specs in a run), so keep mutations additive/idempotent. The one
  exception is `snapshots.spec.js`: its full restore signs out every session
  and revokes every token, so it signs in again and rewrites `.auth/` for the
  specs after it. The logout
  spec mints an isolated session (`loginToFreshSession`) because the backend
  tracks sessions in-memory and logout would otherwise pop the shared session.
- Models shelf specs that move or delete files act only on stubs they stage
  themselves (`stageStub` in `pages/ModelShelfPage.js`, through
  `POST /model-files` into the seeded folder) and delete them in `afterEach`.
  Real removals are the permanent kind; a Trash path is checked at the request
  and answered by the spec, because the Trash is that of whoever runs the suite.
- A spec that registers a reference or import folder makes it with
  `scratchFolder` (`fixtures/folders.js`), calls `expectScratch` on it before
  any remove or relocate, and writes its pictures with `writePicture`. After
  changing a reference folder's files on disk it calls `markFolderChanged`,
  which does what the filesystem watcher would (`POST
  /api/v1/test-hooks/reference-folders/{id}/rescan`): the watcher needs an
  inotify instance a busy machine may not have, and without it the next scan
  is five minutes away. Import folders poll and need neither.
- Stable selectors in use: `.thumbnail-card` (grid cell), `.lightbox` /
  `.lightbox-back` (lightbox), `.search-overlay` (search), `.mdi-magnify`
  (toolbar search button).
```
