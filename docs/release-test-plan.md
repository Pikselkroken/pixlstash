# PixlStash Release Test Plan

This document contains **only what needs hands, eyes, or real hardware**.
Everything else runs automatically and must be green before this plan even
starts:

- **Backend API/integration** — pytest, run per-file in `ci.yml` on every
  push/PR (plus a widened blocking gate on release-prep branches/tags and an
  OS-sensitive subset on Windows runners).
- **Browser journeys** — the Playwright e2e suite (`frontend/e2e/specs/`,
  the `e2e` job in `ci.yml`), catalogued in `docs/regular-tests.md`.
- **Electron logic** — 40 `node --test` unit tests in `electron/test/`
  (overlay install args, CPU fallback state machine, backends location,
  forced-backend parsing, hardware-detector injection). Run them locally with
  `cd electron && npm test` — they are **not** wired into any workflow yet.
- **ROCm GPU overlay install** — `rocm-overlay-install.yml` (weekly) installs
  the ROCm torch overlay with the app's pip arguments and asserts a real HIP
  build imports. The cu128 overlay has no CI equivalent — it is covered by the
  Desktop section below.

**Automated-coverage map** (sections removed from previous editions of this
plan; legacy § numbers are still cited in spec titles):

| Legacy section | Now automated by |
|---|---|
| §1 Authentication (login/logout, tokens, session persistence) | `auth.spec.js` |
| §2 Import — drag overlay affordance | `import.spec.js` (drop→import stays manual, see Import section) |
| §3.1–3.3 Grid display, sort, search | `grid.spec.js`, `grid-browse.spec.js` |
| §3.5 Context menu opens/lists actions | `context-menu.spec.js` (Selection ▾ parity stays manual, #403) |
| §4 ImageOverlay open/navigate/close | `overlay.spec.js` |
| §5 Tags add/remove | `tags.spec.js` |
| §6 Star rating — overlay persist + grid stars + grid↔overlay sync | `rating.spec.js` |
| §7/§8/§9 Set/project/character navigation & filtering | `entities.spec.js`, `set-operations.spec.js`, `set-locking.spec.js` |
| §10 Faces crops + bounding boxes | `faces.spec.js` |
| §11 Stack expand/collapse | `stacks.spec.js` (badge click + visual stacking order stay manual) |
| §14 Predictions — delete drops to prediction chip, Confirm restores | `tag-predictions.spec.js` (generation after import stays manual) |
| §15 Export — selection ZIP contents + set ZIP count | `export.spec.js` |
| §19.1/19.4 Live-update own-change silence, pill choice, flood coalescing, overlay deferral | `grid-own-change-no-pill.spec.js`, `grid-external-change-pill.spec.js`, `grid-injection.spec.js`, `grid-overlay-deferral.spec.js` |
| §20.1 Tag-health board; §20.3/20.4 review create + binary decide + Escape | `review-board.spec.js`, `review-session.spec.js` |
| §0.5 From source (Linux) | CI `e2e` job (pip install, `npm run build`, boots server, logs in, renders grid) |

---

## How to Use

1. Work through each section below in order.
2. Mark each item ✅ Pass / ❌ Fail / ⏭ Skip (with reason).
3. A release is only signed off when all non-skipped items pass.

---

## 1. Installation & Packaging

Perform on a **clean machine/VM** (no prior PixlStash install, no existing
`vault.db`). CI builds all of these artifacts but never runs them — this
section is the only place they are executed.

### 1.1 pip + venv (PyPI)

Requires Python 3.10+.

```
python -m venv venv
# Linux/macOS:  source venv/bin/activate
# Windows:      venv\Scripts\activate
pip install pixlstash
pixlstash-server
```

| Check | macOS | Ubuntu 24.04 | Windows |
|-------|-------|--------------|---------|
| `pip install pixlstash` completes with exit code 0 | | | |
| `pixlstash-server` starts; logs show model download then "Uvicorn running" | | | |
| `http://localhost:9537` shows the login page; log in, import one `.jpg` — it appears in the grid | | | |
| Ctrl-C — server exits cleanly, no traceback, no lock files left | | | |

### 1.2 Docker — CPU image

```bash
docker run --rm -e PIXLSTASH_HOST=0.0.0.0 -p 9537:9537 \
  ghcr.io/pikselkroken/pixlstash:latest
```

| Check | Any host OS |
|-------|-------------|
| Container pulls and logs "Uvicorn running" | |
| Login page loads; log in, import one `.jpg` — it appears in the grid | |
| Ctrl-C — container stops cleanly (nothing dangling in `docker ps -a`) | |

### 1.3 Docker — GPU image (Ubuntu, NVIDIA)

Requires NVIDIA Container Toolkit. Verify GPU access first:
`docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi`

```bash
docker run -d --runtime nvidia --user $(id -u):$(id -g) \
  -e HOME=/home/pixlstash -e NVIDIA_VISIBLE_DEVICES=all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility -e PIXLSTASH_HOST=0.0.0.0 \
  -p 9537:9537 -v ~/Pictures/pixlstash:/home/pixlstash \
  --name pixlstash ghcr.io/pikselkroken/pixlstash:latest-gpu
```

> If the container exits with a permission error, a previous run may have left
> a root-owned `~/Pictures/pixlstash/.config`; `sudo chown -R $(id -u):$(id -g)
> ~/Pictures/pixlstash` and re-run.

| Check | Ubuntu 24.04 |
|-------|--------------|
| `docker logs pixlstash` shows "Uvicorn running", CUDA inference messages, and **no** "CPU fallback" warning | |
| Log in, import one `.jpg`, wait ~30 s — background tagging completes on GPU and tags appear | |

### 1.4 From source (macOS / Windows)

Linux is covered by the CI `e2e` job (same steps, real boot + login). Run on
the other two:

```bash
git clone https://github.com/pikselkroken/pixlstash.git && cd pixlstash
python -m venv venv && venv activation
pip install --upgrade pip && pip install -e .
cd frontend && npm ci && npm run build && cd ..
pixlstash-server
```

| Check | macOS | Windows |
|-------|-------|---------|
| `pip install -e .` and `npm run build` both exit 0 | | |
| Server starts; log in, import one `.jpg` — it appears in the grid | | |

### 1.5 Windows server installer (Inno Setup `.exe`)

The standalone server installer built by `windows-installer.yml` (distinct
from the Electron desktop installer, covered in section 3).

| Check | Windows |
|-------|---------|
| Download the `.exe` from the release page; wizard completes as a normal user with no error dialog | |
| If SmartScreen appears: **More info** → **Run anyway** proceeds normally | |
| Start Menu → **PixlStash Server** — console opens with startup logs; login page loads; import one `.jpg` | |
| Close the console — no `pixlstash`/`python` processes remain in Task Manager | |

---

## 2. Upgrade from the previous minor release

Run against a **copy** of a real `vault.db` created by the previous minor
version (e.g. testing 1.7.x → use a 1.6.x vault). Do not wipe the database —
the point is the migration chain. (Individual migration logic is covered by
the pytest migration/schema suites; this checks the end-to-end chain on real
data.)

```bash
cp vault.db vault.db.bak
alembic upgrade head
```

| Check |  |
|-------|--|
| `alembic upgrade head` completes with exit code 0, no traceback | |
| Server starts against the upgraded vault with no schema errors in logs | |
| Existing pictures, sets, characters, projects, tags, and scores are intact (spot-check counts and a few known pictures) | |
| Columns reset to `NULL` by migrations are picked up for reprocessing by the background finders (watch the task indicator) | |
| Start with the **previous version's `server-config.json`** (missing any newly added keys) — server starts, new keys take their documented defaults, no hard failure | |
| Windows: an existing watch folder with a `C:\...` path still imports; a new file dropped into it is picked up automatically | |

---

## 3. Desktop (Electron) app — Windows / Linux / macOS

The packaged desktop app. CI builds the installers (`electron.yml`) but
**never launches them**, and the `electron/test` unit suite covers only the
overlay/fallback logic — everything below needs a real machine per OS.
Context: the overlay fallback, per-accel ORT pin, and backend-list dedup all
shipped after live incidents verified **on Linux only**; Windows and macOS
have never been manually exercised and are the priority columns.

Overlay expectations per OS: **Windows/Linux** offer the cu128 (CUDA) overlay
from Settings (ROCm where applicable on Linux); **macOS has no overlay UI at
all** — Metal is bundled in the runtime.

### 3.1 Install & first run

| Check | Windows | Linux | macOS |
|-------|---------|-------|-------|
| Install: Windows NSIS `.exe` / Linux AppImage (and `.deb`) / macOS `.dmg` or `.zip` — completes without error | | | |
| Windows: slow-extraction messaging is visible during install (no silent multi-minute hang); SmartScreen **More info → Run anyway** path works | | n/a | n/a |
| First-run wizard appears: pick a library folder and a compute choice; both are honoured after the app starts | | | |
| App boots to the grid; importing one `.jpg` works end-to-end | | | |

### 3.2 GPU overlay install & activation (Windows/Linux)

| Check | Windows (cu128) | Linux (cu128) |
|-------|-----------------|---------------|
| Settings → compute backends: install the GPU overlay — download/install completes and the backend activates | | |
| After restart on the GPU backend, JoyCaption **nf4** produces a caption for a newly imported picture **on the GPU** (check logs: no CPU fallback) | | |
| ONNX runtime loads on the overlay (no `libcudart`/DLL import errors in logs — regression: per-accel ORT pin) | | |

### 3.3 Broken-overlay resilience (Windows/Linux)

Make the active overlay unloadable (e.g. rename a core torch library file
inside the overlay directory), then launch:

| Check | Windows | Linux |
|-------|---------|-------|
| The app shows the "GPU acceleration unavailable" dialog — **no fatal error screen** | | |
| The app boots and works on CPU | | |
| Settings shows **CPU as the active backend** — no phantom-active GPU entry | | |
| The GPU backend can be re-activated from Settings (overlay directory was not deleted) | | |

### 3.4 Compute-backend list

| Check | Windows | Linux | macOS |
|-------|---------|-------|-------|
| Every backend appears **exactly once** (no duplicate row for the active runtime) | | | |
| The built-in CPU (Win/Linux) / Metal (macOS) row is always present and shows **Use** when it is not active | | | |
| macOS: **no** overlay install UI is offered anywhere | n/a | n/a | |

### 3.5 Upgrade in place (Linux AppImage)

| Check | Linux |
|-------|-------|
| Replace the old AppImage binary with the new one; existing vault opens and works | |
| A previously installed GPU overlay is still detected and still works after the upgrade | |

### 3.6 Tray & shutdown

| Check | Windows | Linux | macOS |
|-------|---------|-------|-------|
| Closing the window hides to tray (when the pref is on); the tray menu restores it | | | |
| Full quit from the tray/menu **actually stops the backend** — no orphaned server process afterwards | | | |
| Windows: clean uninstall removes the app; the library folder and vault are left intact | | n/a | n/a |

---

## 4. Grid & browsing — manual remainder

- [ ] **Selection ▾ / context-menu parity:** select 2+ pictures, compare the Selection ▾ dropdown with the right-click context menu — both must list the same selection-scoped actions.
  - ⚠️ **Known bug — currently FAILS:** the context menu shows **Restore from snapshot** and **Reverse image search** (and, for a single selection, **Share image** / **Find similar faces**) which Selection ▾ lacks. Tracked in [#403](https://github.com/Pikselkroken/pixlstash/issues/403). Mark ❌ until reconciled.
- [ ] **Stack badge:** the top-left badge on a stack leader shows the member count; clicking it expands the members **in the correct stacking order**, clicking again collapses (the View-menu expand/collapse path is automated; the badge click and visual order are not)

---

## 5. Import & background processing

The e2e harness runs with `disable_background_workers: true` and the import
endpoint requires the face worker — so everything here needs a real server
run with workers ON.

- [ ] Drag two or more image files from the OS file manager onto the grid and drop — import starts immediately, a progress indicator appears, and the pictures appear in the grid without a reload (the drag overlay itself is automated; the drop → import path is not)
- [ ] After importing a batch of 5+ pictures: the task indicator shows live progress; faces, a generated caption (Description section), and predicted tags appear on the new pictures once processing completes
- [ ] Deleting all tags on a processed picture and requeueing (Reset and regenerate) produces fresh predictions

---

## 6. ComfyUI Integration

Requires a live ComfyUI server.

- [ ] Settings → **Workflows**: enter the ComfyUI server URL → Save → the host appears in the dialog
- [ ] Toolbar ComfyUI menu: run a text-to-image workflow with a prompt — progress shows; the output picture appears in the grid
- [ ] Run an image-to-image workflow from a source picture — output appears and is linked to the source
- [ ] **Abort** during a run — execution stops and the progress indicator disappears

---

## 7. Image Plugins

Visual-judgement checks (apply via the ImageOverlay):

- [ ] **Blur/Sharpen**: blur level 5 — result is visibly blurrier
- [ ] **Brightness/Contrast**: +2 brightness — result is visibly brighter
- [ ] **Colour Filter**: apply a tint — result shows the colour shift

---

## 8. Performance & Stability

Needs a real library at scale (the e2e fixture is ~110 pictures and renders in
one page).

- [ ] Import 200+ pictures; scroll the full grid at normal speed — pictures keep loading in as you scroll, no thumbnails remain broken, the browser does not freeze
- [ ] While face extraction is processing 20+ images: scroll, sort, and open the overlay — the UI stays responsive with no full-page freeze

---

## 9. Folder Management (Reference + Import)

Use two test folders: `reference_test/` (≥2 pre-existing images) and
`import_test/` (empty, add 1 image mid-test).

### 9.1 pip / native install

- [ ] Sidebar **Folders** → Add folder → **Reference folder** → `reference_test/` — created with no error, no pending-restart badge, scanning starts automatically
- [ ] Clicking the reference folder shows its pictures in the grid
- [ ] Add an **Import folder** → `import_test/`; copy a new image into it while running — it appears in the grid automatically
- [ ] Remove the reference folder — the entry disappears and its indexed pictures leave the grid

### 9.2 Docker install

- [ ] In Docker mode, folder browsing is unavailable and manual path entry is required
- [ ] Saving a new reference/import folder prompts for a container restart with an updated mount; the folder shows pending-restart status until then
- [ ] After restart the folder transitions to active (or mount-error if invalid); a new file in the mounted `import_test/` is picked up automatically

---

## 10. Grid Live-Update — manual remainder

The deterministic core is automated (own vs external, pill choice, flood
coalescing, overlay deferral — see `docs/regular-tests.md`). These need what
the harness can't do: a real second client, workers ON, a network drop, and a
storage-denied browser.

### 10.1 External change from a real second client

Two tabs or devices as the same owner (A observes, B mutates):

- [ ] B imports a picture → A shows the **"New pictures"** pill; clicking loads it
- [ ] B changes a rating/tag affecting A's sort → A shows **"View changed externally"**; clicking reflects it

### 10.2 Quiet during bulk work (workers ON)

- [ ] Import a 20+ batch (or reset tags/quality on a large selection) — the grid does **not** churn per picture; updates land in coalesced batches and the UI stays usable throughout

### 10.3 Reconnect after network drop

- [ ] DevTools → Offline, wait, → Online — the WebSocket reconnects and the grid recovers
- [ ] ⚠️ **Known gap:** events during the offline window are lost (no replay). Mark ✅ if reconnect itself recovers; note the gap.

### 10.4 Storage-denied clientId

- [ ] In a private window (or with `sessionStorage` blocked): make an own change, reload immediately — a pill may wrongly appear for the in-flight echo. ⚠️ Documented narrow limitation (#501), not a regression.

---

## 11. Review Sessions — manual remainder

Board + create + binary decide + Escape-close are automated
(`review-board.spec.js`, `review-session.spec.js`; four further cases are
`fixme`-guarded pending behaviour reconciliation). The rest needs a real vault
or human judgement.

### 11.1 Board signals not exercisable on the committed fixture

- [ ] **Est. wrong / Est. missing** non-zero on a vault with disagreeing predictions
- [ ] **"no model signal"** row for a tag outside the tagger vocabulary (kNN review still offered)
- [ ] **Model-disputes** banner when a human label contradicts a confident current-model prediction
- [ ] **Build-progress bar** visibly fills while the cache rebuilds on a large vault

### 11.2 Pair cards

- [ ] Same-stack / dhash-near pairs show a pair card with **Both / Neither / Left only / Right only** (`B/N/L/R`); Right-only performs the swap; LEFT = tagged side, RIGHT = untagged side

### 11.3 Skip / refresh / staleness

- [ ] Skip removes the item with **no** decision written; rail shows "N skipped"; completion offers "Reopen N skipped"
- [ ] After a vault change, the staleness hint appears; **Refresh appends** new suspects with a **NEW — from refresh** badge and never resurrects decided cards

### 11.4 Abort / archive

- [ ] Abort with decisions made offers **Keep N changes / Undo N changes / Cancel** and each behaves as labelled
- [ ] Completing the queue reaches an explicit completion state; **Archive** produces the receipt ("N reviewed — N removed, N added, N kept, N skipped")

### 11.5 Manual tag / evidence region / gamification

- [ ] **Apply tags** button + `T` opens the tag panel from a card
- [ ] `H` toggles the Grad-CAM heatmap/boxes on cards with a region; the preference persists
- [ ] Gamification opt-in: XP counters climb on decisions, stickers persist in the capped shelf, celebrations fire on decisions and never on undo, rewards are never clawed back

### 11.6 Acceptance criteria (release gate)

- [ ] Board automated specs green + §11.1 spot-checked on a real vault
- [ ] Binary + pair decision mapping verified against the backend receipt (accept/dismiss/swap/skip; skip writes nothing)
- [ ] One-open-per-tag (409), abort keep-vs-undo, and archive receipt verified
- [ ] No orphaned `TagSuggestion` rows; per-item accept/dismiss/reopen still work
