# PixlStash Release Test Plan

This document contains **only what needs hands, eyes, or real hardware**.
Everything else runs automatically and must be green before this plan even
starts:

- **Backend API/integration** — pytest, run per-file in `ci.yml` on every
  push/PR (plus a widened blocking gate on release-prep branches/tags and an
  OS-sensitive subset on Windows runners).
- **Browser journeys** — the Playwright e2e suite (`frontend/e2e/specs/`,
  the `e2e` job in `ci.yml`), catalogued in `docs/regular-tests.md`.
- **Electron logic** — the `node --test` suite in `electron/test/` (overlay
  install and fallback, backends location, setup, IPC validation, MCP shim and
  more), run by `npm test` in both `ci.yml` and `electron.yml`.
- **ROCm GPU overlay install** — `rocm-overlay-install.yml` (weekly) installs
  the ROCm torch overlay with the app's pip arguments and asserts a real HIP
  build imports. The cu128 overlay has no CI equivalent — it is covered by the
  Desktop section below.

**Automated-coverage map** (sections removed from previous editions of this
plan; legacy § numbers are still cited in spec titles):

| Legacy section | Now automated by |
|---|---|
| §1 Authentication (login/logout, tokens, session persistence) | `auth.spec.js` |
| §2 Import — Import panel, grid drop, set/character/project targets, locked set, unsupported files, de-duplication, `.txt` sidecar tags, video | `import.spec.js` |
| §3.1–3.3 Grid display, sort, search | `grid.spec.js`, `grid-browse.spec.js` |
| §3.5 Context menu opens/lists actions | `context-menu.spec.js` |
| §4 Selection ▾ / context-menu parity (was #403) | `menu-parity.spec.js` |
| §4 Lightbox open/navigate/close | `lightbox.spec.js` |
| §5 Tags add/remove | `tags.spec.js` |
| §6 Star rating — overlay persist + grid stars + grid↔overlay sync | `rating.spec.js` |
| §7/§8/§9 Set/project/character navigation & filtering; create, edit, delete | `entities.spec.js`, `set-operations.spec.js`, `set-locking.spec.js`, `sidebar-entries.spec.js` |
| §10 Faces crops + bounding boxes | `faces.spec.js` |
| Delete to the Scrapheap (key, Selection ▾, context menu, lightbox), locked-set refusal, Restore / Restore all, Empty and Delete forever up to Cancel | `scrapheap.spec.js` (confirming a purge stays manual until #2353) |
| §11 Stack expand/collapse | `stacks.spec.js` (badge click + visual stacking order stay manual) |
| §14 Predictions — delete drops to prediction chip, Confirm restores | `tag-predictions.spec.js` (generation after import stays manual) |
| §15 Export — selection ZIP contents + set ZIP count | `export.spec.js` |
| §19.1/19.4 Live-update own-change silence, pill choice, flood coalescing, overlay deferral | `grid-own-change-no-pill.spec.js`, `grid-external-change-pill.spec.js`, `grid-injection.spec.js`, `grid-lightbox-deferral.spec.js` |
| §20.1 Tag-health board; §20.3/20.4 review create + binary decide + Escape | `review-board.spec.js`, `review-session.spec.js` |
| Filter menu facets, chips, Clear all | `filters.spec.js` |
| Deep links (`?overlay=` on entity routes, multi-entity URLs, `?review=`) | `deep-links.spec.js` |
| Grid and lightbox keyboard shortcuts, typing safety | `grid-keys.spec.js`, `lightbox-keys.spec.js` |
| §0.5 From source (Linux) | CI `e2e` job (pip install, `npm run build`, boots server, logs in, renders grid) |
| §9 Folder Management: reference and import folders (no sidebar row; pictures still listed; a new file is picked up) | `reference-folders.spec.js`, `import-folders.spec.js`, `tests/test_reference_folder_watcher.py` |
| Workflows: adding, cards, navigation, the Workflow tab editor and its versions, the Run popup, seeds, LoRA runs against a fake ComfyUI | `workflows-*.spec.js`, `workflow-*.spec.js`, `run-popup-save.spec.js`, `comfyui-fixture.spec.js` |
| Recipes tab | `workflow-recipes-tab.spec.js` |
| Models shelf: listing, names, base model edit, move, delete, removing a folder | `model-shelf-*.spec.js`, `model-tile-names.spec.js` |
| Training a LoRA end to end (finished, stopped, failed) against a fake ai-toolkit; Train longer | `training.spec.js`, `training-run-board-steady.spec.js`; `tests/training/test_training_service.py` |
| Lightbox editing (rotate, crop, description, naming a face); inpaint graph conversion | `lightbox-editing.spec.js`; `tests/test_inpaint_conversion.py` |
| Installing a third-party node pack through ComfyUI-Manager | `tests/test_node_pack_install.py` |
| MCP server: tools, token scope, write gating | `tests/test_mcp_server.py` |

---

## How to Use

1. Work through each section below in order.
2. Mark each item ✅ Pass / ❌ Fail / ⏭ Skip (with reason).
3. A release is only signed off when all non-skipped items pass.

**Before cutting the release, review the known base models.** The Models
shelf identifies each file's base model against the table in
`pixlstash/utils/known_base_models.py`, and that table only grows when a
release ships. Add any base model released since the last one that people are
training on: a label a person would recognise, its `family` (what it loads
alongside, which the name does not tell you), its `modality`, and the aliases
Civitai, kohya and HuggingFace use for it. Each addition gets a `changelog.d/`
fragment, because files already on a shelf pick it up on their next scan.

---

## 1. Installation & Packaging

Perform on a **clean machine/VM** (no prior PixlStash install, no existing
`vault.db`). CI builds all of these artifacts but never runs them — this
section is the only place they are executed.

**Which build:** always the newest release candidate, by its exact version.
One set of issues covers rc1 to rcN, so every block below looks the RC up
itself and prints it; write that version next to your results. Docker pulls
`latest-dev` / `latest-dev-gpu`, which move with every development release,
and prints the version inside the image. Bash blocks run on Linux and macOS (and
Git Bash on Windows); PowerShell blocks are for Windows.

**Mark the machine as a test machine** first, so the version-check data
excludes your installs from the real active-install count. Only the file's
existence is read:

```bash
# Linux
mkdir -p ~/.local/share/pixlstash && touch ~/.local/share/pixlstash/.pixlstash-dev-machine
# macOS
mkdir -p ~/Library/Application\ Support/pixlstash && touch ~/Library/Application\ Support/pixlstash/.pixlstash-dev-machine
```

```powershell
New-Item -ItemType File -Force "$env:LOCALAPPDATA\pixlstash\pixlstash\.pixlstash-dev-machine"
```

The doubled `pixlstash\pixlstash` on Windows is not a typo: the server
resolves the marker with `platformdirs.user_data_dir("pixlstash")`, which
supplies the app name as the author segment when none is given. The Docker
commands set `PIXLSTASH_TELEMETRY_DEV=1` instead.

Where a pip or source install keeps its state (server-config.json, hub.db, and
by default the library with its `vault.db` under `images/`):
`~/.config/pixlstash` on Linux, `~/Library/Application Support/pixlstash` on
macOS, `%LOCALAPPDATA%\pixlstash\pixlstash` on Windows.

### 1.1 pip + venv (PyPI)

Requires Python 3.11+.

```bash
RC=$(curl -s https://api.github.com/repos/Pikselkroken/pixlstash/releases | grep -o '"tag_name": *"v[0-9.]*rc[0-9]*"' | head -1 | grep -o 'v[^"]*'); echo "Testing ${RC:?no release candidate found}"
python3 -m venv venv
venv/bin/pip install "pixlstash==${RC#v}"
venv/bin/pixlstash-server
```

```powershell
$RC = ((Invoke-RestMethod https://api.github.com/repos/Pikselkroken/pixlstash/releases) | Where-Object tag_name -match '^v[\d.]+rc\d+$' | Select-Object -First 1).tag_name; "Testing $RC"
python -m venv venv
venv\Scripts\pip install "pixlstash==$($RC.TrimStart('v'))"
venv\Scripts\pixlstash-server
```

| Check | macOS | Ubuntu 24.04 | Windows |
|-------|-------|--------------|---------|
| `pip install` completes with exit code 0 and installs the version printed (not the last stable release) | | | |
| `pixlstash-server` starts; logs show model download then "Uvicorn running" | | | |
| `http://localhost:9537` shows the login page; log in, import one `.jpg` — it appears in the grid | | | |
| Ctrl-C — server exits cleanly, no traceback, no lock files left | | | |

### 1.2 Docker — CPU image

```bash
docker pull ghcr.io/pikselkroken/pixlstash:latest-dev
docker run --rm --entrypoint python ghcr.io/pikselkroken/pixlstash:latest-dev \
  -c "from importlib.metadata import version; print('Testing', version('pixlstash'))"
PW=$(openssl rand -base64 18); echo "Log in as: owner / $PW"
docker run --rm -e PIXLSTASH_HOST=0.0.0.0 -e PIXLSTASH_TELEMETRY_DEV=1 \
  -e PIXLSTASH_INITIAL_USERNAME=owner -e PIXLSTASH_INITIAL_PASSWORD="$PW" \
  -p 9537:9537 ghcr.io/pikselkroken/pixlstash:latest-dev
```

```powershell
docker pull ghcr.io/pikselkroken/pixlstash:latest-dev
docker run --rm --entrypoint python ghcr.io/pikselkroken/pixlstash:latest-dev -c "from importlib.metadata import version; print('Testing', version('pixlstash'))"
$PW = [Guid]::NewGuid().ToString('N'); "Log in as: owner / $PW"
docker run --rm -e PIXLSTASH_HOST=0.0.0.0 -e PIXLSTASH_TELEMETRY_DEV=1 -e PIXLSTASH_INITIAL_USERNAME=owner -e "PIXLSTASH_INITIAL_PASSWORD=$PW" -p 9537:9537 ghcr.io/pikselkroken/pixlstash:latest-dev
```

The browser cannot create the owner through Docker's network (the request is
not from the same machine), so the command creates it and prints the login.

| Check | Any host OS |
|-------|-------------|
| The printed version is the RC under test (the newest release); the log says it claimed the owner account, then "Uvicorn running" | |
| `http://localhost:9537` shows the login page; the printed login works; import one `.jpg` — it appears in the grid | |
| Ctrl-C — container stops cleanly (nothing dangling in `docker ps -a`) | |

### 1.3 Docker — GPU image (Ubuntu, NVIDIA)

Requires NVIDIA Container Toolkit. Verify GPU access first:
`docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi`

Then, in one terminal, this block and the next:

```bash
docker pull ghcr.io/pikselkroken/pixlstash:latest-dev-gpu
docker run --rm --entrypoint python ghcr.io/pikselkroken/pixlstash:latest-dev-gpu \
  -c "from importlib.metadata import version; print('Testing', version('pixlstash'))"
mkdir -p ~/Pictures/pixlstash
if [ -z "$(ls -A ~/Pictures/pixlstash)" ]; then PW=$(openssl rand -base64 18); echo "Log in as: owner / $PW"
else echo "~/Pictures/pixlstash holds an earlier run: log in with its password, or move it aside and paste this block again"; fi
```

```bash
docker run -d --runtime nvidia --user $(id -u):$(id -g) \
  -e HOME=/home/pixlstash -e NVIDIA_VISIBLE_DEVICES=all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility -e PIXLSTASH_HOST=0.0.0.0 \
  -p 9537:9537 -v ~/Pictures/pixlstash:/home/pixlstash \
  -e PIXLSTASH_TELEMETRY_DEV=1 -e PIXLSTASH_INITIAL_USERNAME=owner -e PIXLSTASH_INITIAL_PASSWORD="$PW" \
  --name pixlstash ghcr.io/pikselkroken/pixlstash:latest-dev-gpu
```

> If the container exits with a permission error, a previous run may have left
> a root-owned `~/Pictures/pixlstash/.config`; `sudo chown -R $(id -u):$(id -g)
> ~/Pictures/pixlstash` and re-run.

| Check | Ubuntu 24.04 |
|-------|--------------|
| `docker logs pixlstash` shows "Uvicorn running", CUDA inference messages, and **no** "CPU fallback" warning | |
| Log in with the printed login, import one `.jpg`, wait ~30 s — background tagging completes on GPU and tags appear | |

### 1.4 From source (macOS / Windows)

Linux is covered by the CI `e2e` job (same steps, real boot + login). Run on
the other two:

```bash
RC=$(curl -s https://api.github.com/repos/Pikselkroken/pixlstash/releases | grep -o '"tag_name": *"v[0-9.]*rc[0-9]*"' | head -1 | grep -o 'v[^"]*'); echo "Testing ${RC:?no release candidate found}"
git clone --branch "$RC" --depth 1 --recurse-submodules --shallow-submodules https://github.com/Pikselkroken/pixlstash.git "pixlstash-$RC" && cd "pixlstash-$RC"
python3 -m venv venv && venv/bin/python -m pip install --upgrade pip && venv/bin/pip install -e .
(cd frontend && npm ci && npm run build)
venv/bin/pixlstash-server
```

```powershell
$RC = ((Invoke-RestMethod https://api.github.com/repos/Pikselkroken/pixlstash/releases) | Where-Object tag_name -match '^v[\d.]+rc\d+$' | Select-Object -First 1).tag_name; "Testing $RC"
git clone --branch $RC --depth 1 --recurse-submodules --shallow-submodules https://github.com/Pikselkroken/pixlstash.git "pixlstash-$RC"; cd "pixlstash-$RC"
python -m venv venv; venv\Scripts\python -m pip install --upgrade pip; venv\Scripts\pip install -e .
cd frontend; npm.cmd ci; npm.cmd run build; cd ..
venv\Scripts\pixlstash-server
```

| Check | macOS | Windows |
|-------|-------|---------|
| `pip install -e .` and `npm run build` both exit 0 | | |
| Server starts; log in, import one `.jpg` — it appears in the grid | | |

### 1.5 Windows server installer (Inno Setup `.exe`)

The standalone server installer built by `windows-installer.yml` (distinct
from the Electron desktop installer, covered in section 3).

```powershell
$RC = ((Invoke-RestMethod https://api.github.com/repos/Pikselkroken/pixlstash/releases) | Where-Object tag_name -match '^v[\d.]+rc\d+$' | Select-Object -First 1).tag_name; "Testing $RC"
$a = (Invoke-RestMethod "https://api.github.com/repos/Pikselkroken/pixlstash/releases/tags/$RC").assets | Where-Object name -like 'pixlstash-server-*-windows-x64*.exe' | Select-Object -First 1
$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest $a.browser_download_url -OutFile $a.name; "Downloaded $($a.name)"
```

| Check | Windows |
|-------|---------|
| Run the downloaded `.exe`; wizard completes as a normal user with no error dialog | |
| Right-click the `.exe` → Properties → Digital Signatures: a valid SHA-256 Certum signature with timestamp (signed releases; a `-unsigned.exe` has no signature tab) | |
| SmartScreen: on a signed build it should show the publisher name if it appears at all (reputation accrues over time); on a `-unsigned` build **More info** → **Run anyway** proceeds normally | |
| Start Menu → **PixlStash Server** — console opens with startup logs; login page loads; import one `.jpg` | |
| Close the console — no `pixlstash`/`python` processes remain in Task Manager | |

---

## 2. Upgrade from the previous minor release

Run on a test machine where the previous minor release (e.g. testing 2.1.x →
2.0.x) was installed with §1.1's commands, in the same folder, and used for a
while. The server migrates the library when it opens it, so the check is
starting the new version on that state. Do not wipe anything: the point is the
migration chain. (Individual migration logic is covered by the pytest
migration/schema suites; this checks the end-to-end chain on real data.) Stop
the old server before pasting.

```bash
RC=$(curl -s https://api.github.com/repos/Pikselkroken/pixlstash/releases | grep -o '"tag_name": *"v[0-9.]*rc[0-9]*"' | head -1 | grep -o 'v[^"]*'); echo "Testing ${RC:?no release candidate found}"
CONF=~/.config/pixlstash; [ "$(uname)" = Darwin ] && CONF=~/Library/Application\ Support/pixlstash
grep '"image_root"' "$CONF/server-config.json"   # the library: its vault.db is in this folder
cp -a "$CONF" ~/pixlstash-config-before-upgrade
venv/bin/pip install "pixlstash==${RC#v}"
venv/bin/pixlstash-server
```

```powershell
$RC = ((Invoke-RestMethod https://api.github.com/repos/Pikselkroken/pixlstash/releases) | Where-Object tag_name -match '^v[\d.]+rc\d+$' | Select-Object -First 1).tag_name; "Testing $RC"
Select-String '"image_root"' "$env:LOCALAPPDATA\pixlstash\pixlstash\server-config.json"
Copy-Item -Recurse "$env:LOCALAPPDATA\pixlstash\pixlstash" "$HOME\pixlstash-config-before-upgrade"
venv\Scripts\pip install "pixlstash==$($RC.TrimStart('v'))"
venv\Scripts\pixlstash-server
```

The copy holds the config, `hub.db` and, when `image_root` is the default
`images/` folder inside it, the library too. If `image_root` points elsewhere,
copy that folder as well before starting.

| Check |  |
|-------|--|
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

If a release build failed for a CI reason (a retired runner image, a signing
outage), fix the workflow on `main` and rebuild the release without a new tag:
Actions → *Rebuild release* → **Run workflow** from `main` with `release_tag`
set to the tag. It dispatches the Windows installer, desktop app, Docker,
PyPI and install-smoke workflows with `main`'s current definitions; each
builds the tag's source, attaches to the tag's existing release, and skips
what is already published. Each of those workflows also takes `release_tag`
on its own, to rebuild just one part.

Overlay expectations per OS: **Windows/Linux** offer the cu128 (CUDA) overlay
from Settings (ROCm where applicable on Linux); **macOS has no overlay UI at
all** — Metal is bundled in the runtime.

### 3.1 Install & first run

The RC's installers (the line printing them is the one for your OS):

```bash
RC=$(curl -s https://api.github.com/repos/Pikselkroken/pixlstash/releases | grep -o '"tag_name": *"v[0-9.]*rc[0-9]*"' | head -1 | grep -o 'v[^"]*'); echo "Testing ${RC:?no release candidate found}"
curl -s "https://api.github.com/repos/Pikselkroken/pixlstash/releases/tags/$RC" | grep -o 'https://[^"]*/PixlStash-desktop-[^"]*'
```

```powershell
$RC = ((Invoke-RestMethod https://api.github.com/repos/Pikselkroken/pixlstash/releases) | Where-Object tag_name -match '^v[\d.]+rc\d+$' | Select-Object -First 1).tag_name; "Testing $RC"
$a = (Invoke-RestMethod "https://api.github.com/repos/Pikselkroken/pixlstash/releases/tags/$RC").assets | Where-Object name -like 'PixlStash-desktop-*-win-x64*.exe' | Select-Object -First 1
$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest $a.browser_download_url -OutFile $a.name; "Downloaded $($a.name)"
```

| Check | Windows | Linux | macOS |
|-------|---------|-------|-------|
| Install: Windows NSIS `.exe` / Linux AppImage (and `.deb`) / macOS `.dmg` or `.zip` — completes without error | | | |
| Windows: slow-extraction messaging is visible during install (no silent multi-minute hang); installer `.exe` carries a valid Certum digital signature (Properties → Digital Signatures; `-unsigned` builds instead need the SmartScreen **More info → Run anyway** path) | | n/a | n/a |
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

- [ ] **Stack badge:** the top-left badge on a stack leader shows the member count; clicking it expands the members **in the correct stacking order**, clicking again collapses (the View-menu expand/collapse path is automated; the badge click and visual order are not)

---

## 5. Import & background processing

The e2e harness runs submitted tasks and the folder sweeps but loads no models
and runs none of the tagging, embedding or scoring sweeps, so the import itself
is automated (`import.spec.js`) and what follows it is not: everything here
needs a real server run with workers ON.

- [ ] Drag two or more image files from the OS file manager onto the grid and drop — import starts immediately, a progress indicator appears, and the pictures appear in the grid without a reload (`import.spec.js` drops a synthetic DataTransfer; a drag from a real file manager is not automated)
- [ ] After importing a batch of 5+ pictures: the task indicator shows live progress; faces, a generated caption (Description section), and predicted tags appear on the new pictures once processing completes
- [ ] Deleting all tags on a processed picture and requeueing (Reset and regenerate) produces fresh predictions

---

## 6. ComfyUI Integration

Requires a live ComfyUI server.

- [ ] Settings → **ComfyUI**, no address saved, ComfyUI running locally: "Found ComfyUI on this computer" offers its address → **Connect** → Connected, with the node-pack line
- [ ] Same, ComfyUI stopped: the address form appears; a wrong port shows "Nothing answered at …" and saves nothing (reopen Settings: still not connected)
- [ ] Settings → **ComfyUI**, on a ComfyUI without the PixlStash nodes: the row's Nodes column says **Missing**; **Link** offers to install them, ComfyUI restarts, and the row comes back **Installed** and **Linked**
- [ ] **Workflows**: Run… a text-to-image workflow with a prompt — progress shows in the title bar's Tasks pill; the picture appears in the grid, and its recipe in the lightbox names that workflow
- [ ] **Workflows**: open a workflow and **Open in ComfyUI** — ComfyUI shows the same graph
- [ ] Lightbox → **Edit** tab: paint a mask on a picture and run an Inpaint workflow — the result joins the picture's stack, and only the painted area has changed (judgement)
- [ ] Stop a run from the **Tasks** pill while it draws — ComfyUI stops and the run ends as stopped

---

## 7. Image Plugins

Visual-judgement checks (apply via the Lightbox):

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

## 9. Models, training and the MCP server — manual remainder

The suites run these against three stub models, a fake ai-toolkit and a test
token (see the coverage map). What needs real files, a GPU or a real agent:

- [ ] **Models** shelf on a real model folder (hundreds of files): the scan finishes, and the base models it names look right for files you know
- [ ] Train a LoRA of a person on a real ai-toolkit and GPU: samples appear while it runs; when it finishes the LoRA is on the shelf, attached to the person
- [ ] **Train longer…** on that run: it carries on in the same run from a kept checkpoint, and the run's page says from which step
- [ ] Settings → **API Tokens** → **Connect AI agent**: paste the Claude Code command into a terminal, ask Claude "how many picture sets do I have?" — the answer matches the sidebar

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

### 10.4 Storage-denied clientId — not a release check

**Nothing to test here.** In a private window (or with `sessionStorage`
blocked) the per-tab client id cannot persist, so it regenerates on reload and
an in-flight echo of a pre-reload own change can raise a pill. This is
**accepted permanent behaviour**, not an open defect: [#501](https://github.com/Pikselkroken/pixlstash/issues/501)
was closed on that basis, because the only fixes available either break the
per-tab uniqueness the id exists for (`localStorage`) or invent a fingerprint.

Re-confirming it every release costs a tester several minutes to reproduce a
result that is known in advance and cannot change without a deliberate design
decision. It is recorded here so the behaviour is not mistaken for a
regression when someone stumbles into it — not so that anyone goes looking.

---

## 11. Review Sessions — manual remainder

Board + the session loop (create, binary decide, Escape-close, Undo, Skip,
keyboard, completion/archive) are automated (`review-board.spec.js`,
`review-session.spec.js`). The rest needs a real vault or human judgement.

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
