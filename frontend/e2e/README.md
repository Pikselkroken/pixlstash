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
  └─ webServer:  python e2e/fake_comfyui.py
                 (a fake ComfyUI on the backend's port + 200; the backend's
                  ComfyUI address points at it)
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
8. lays out a fake ai-toolkit checkout (`<work dir>/ai-toolkit`) whose `run.py`
   links to the backend suite's CPU stand-in, `tests/training/fake_run.py`, and
   whose `.venv/bin/python` is the launcher's own interpreter. It sets
   `FAKE_RUN_STEP_DELAY` (0.05 s) so a short run is seen in progress, and names
   a pretend card as `e2e_training_card`: with `enable_test_hooks` on, training
   sizes runs against it and is granted a lease at once, so a run needs no GPU
   and never leases the machine's own. The checkout is connected on the owner's
   row, which does not exist at launch, so a spec connects it with
   `PUT /training/ai-toolkit/config` and disconnects it after
   (`training-fixture.spec.js`). Datasets, runs, the job database and the
   imported LoRA all land in the work dir. Each checkpoint the stand-in writes
   carries its own file name, so no two runs' LoRAs share a hash, and the run
   folder holds the job's `config.yaml`, as ai-toolkit's does, so the imported
   LoRA records the recipe's base model. A run made to
   fail connects a second interpreter in the same `.venv`, a wrapper that sets
   `FAKE_RUN_FAIL` (`training.spec.js`);
9. names the fake ComfyUI as `e2e_comfyui_url`. With `enable_test_hooks` on,
   it replaces the `127.0.0.1:8188` an owner with no ComfyUI address of their
   own reaches, so no run, read or abort reaches a real ComfyUI on the machine;
10. execs `python -m pixlstash.app` against that temp copy.

### The fake ComfyUI (`fake_comfyui.py`)

Playwright starts it beside the backend, on the backend's port + 200
(`PIXLSTASH_E2E_COMFYUI_PORT` pins it; it refuses 8188), bound to 127.0.0.1.
It answers what PixlStash calls: `system_stats`, `object_info` (the stock
loaders and samplers with the seeded model folder's files, the pack's Adapter
Loader, which loads a shelf LoRA by its digest wherever it lies, and the Picture
Saver), `extensions` (the ComfyUI-PixlStash scripts, so the pack reads as
installed), `queue`, `interrupt`, `prompt`, `history` and `view`. A prompt
"runs" as it is queued: each `SaveImage` makes one small random PNG with the
graph in its `prompt` chunk, and the backend imports it from `/view` exactly as
it would from ComfyUI. It never receives a `PixlStashPictureSaver`: PixlStash
swaps each for a `SaveImage` before it submits and files the pictures itself.
Anything it does not answer is a 404 and a `[fake-comfyui] not faked:` line in
the output (the progress socket, `/ws`, is one: a run then shows no step
progress, and nothing else changes).

For specs (`specs/comfyui-fixture.spec.js` is the worked example), at
`process.env.PIXLSTASH_E2E_COMFYUI_URL`:

- `GET /__fake/prompts`: every graph it was sent, as `{prompt_id, client_id,
  prompt}`. Find yours by the `prompt_id` the run answered with.
- `PUT /__fake/fail` with `{"mode": "missing_models"}` (no model files in
  `object_info`, so the pre-flight refuses), `{"mode": "node_error"}` (`POST
  /prompt` answers ComfyUI's validation failure) or `{"mode": null}`. Turn it
  off again in `afterEach`.

The owner's own `comfyui_url` stays unset, so the SPA still reads ComfyUI as
not configured; a run through the API reaches the fake regardless. A run that
names one person files its picture as theirs pending face extraction
(`pending_character_id`); the e2e backend loads no face model, so it stays
pending.

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
  fixtures/workflowsApi.js
                     the real workflow API for the Workflows specs that run
                     without fakes: store/duplicate a workflow, clean up
  fixtures/workflowStubs.js, workflowTabStubs.js
                     the Run popup's and the Workflow tab's fake servers
  pages/             thin page objects (locators + actions, no assertions):
                     GridPage, Lightbox, SettingsDialog, WorkflowsPage
  specs/             one file per release-test-plan section:
                     grid.spec.js (smoke), auth (§1), grid-browse (§3),
                     overlay (§4), faces (§10), stacks (§11);
                     workflows-card-lifecycle and workflows-change-bar run
                     the card verbs and the change bar on the real API, and
                     workflow-fakes-contract holds the two fakes against it
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
  in a spec, never its keys. The popup's and the Workflow tab's fakes
  (`fixtures/workflowStubs.js`, `fixtures/workflowTabStubs.js`) are held
  against the real API by `specs/workflow-fakes-contract.spec.js`: a key a fake
  serves must be one the server sends, with the same JSON type. A fake for a
  new route gets a call there too.
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
