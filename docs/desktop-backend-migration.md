# Desktop backend migration: bundled CPU runtime + on-demand GPU wheels

## Context & goal
Today the Electron app ships a thin shell and, on first run, downloads a full
relocatable "compute backend" (standalone CPython + the `pixlstash` wheel + a
hardware-matched torch) as a multi-GB `.tar.zst` we **build and host** per
`(os, arch, accel)`, indexed by a catalog at `pixlstash.dev/backends/<version>.json`.

We don't need to host anything. The only genuinely heavy, hardware-specific
artifacts — `torch`/`torchvision` and `onnxruntime`/`onnxruntime-gpu` — already
live on PyPI and `download.pytorch.org` with their own CDNs and hashes. So:

- **Bundle a fully-working CPU (and, on macOS, Metal) runtime *inside the installer*** —
  the app runs offline out of the box.
- **Download only the GPU wheels on demand** (NVIDIA/CUDA, AMD/ROCm), straight from
  PyTorch's index, into a user-writable overlay.
- **Host nothing.** Delete the bundle-building matrix, the GitHub release assets,
  the website catalog, and the catalog version/404 problem along with them.

This is option **C** from the design discussion.

## What ships vs. what's fetched (per platform)

| Runner / platform | Bundled in the installer | Fetched on demand (from PyPI/PyTorch) |
| --- | --- | --- |
| Linux x64 | CPython + all deps + **CPU** torch + onnxruntime | NVIDIA → `torch+cu128`, `onnxruntime-gpu`; AMD → `torch+rocm` (ORT stays CPU — ROCm ORT isn't on PyPI) |
| Windows x64 | CPython + all deps + **CPU** torch + onnxruntime | NVIDIA → `torch+cu128`, `onnxruntime-gpu` |
| macOS arm64 | CPython + all deps + **Metal** torch (default PyPI macOS wheel) + onnxruntime | nothing — MPS is already in the bundled torch |

Notes:
- macOS arm64 is GPU-accelerated *in the bundled env* (default macOS torch includes MPS),
  so Mac users never download anything.
- Intel macOS (`x64`) isn't in the current matrix; out of scope (unchanged).
- AI **model weights** (CLIP, etc.) keep auto-downloading to `userData` at runtime,
  exactly as today — unchanged.

## The one hard constraint: the shipped runtime is read-only
You can't `pip install` into the bundled interpreter — the installed app is
read-only/immutable on every target: AppImage is a read-only squashfs mount, a
notarized macOS `.app` must not be modified, and Windows Program Files needs
elevation. So GPU wheels must land in a **writable overlay under `userData/`**.

**Mechanism (preferred): `pip install --target` + `PYTHONPATH` overlay.**
```
<bundled python> -m pip install --no-cache-dir \
    --target <userData>/backends/<accel> \
    torch==<bundled-ver> torchvision==<bundled-ver> --index-url <accel index> \
    onnxruntime-gpu==<pinned>
# launch: PYTHONPATH=<userData>/backends/<accel> prepended so the GPU torch
# shadows the bundled CPU torch (PYTHONPATH precedes base site-packages).
```
`--target` is chosen over `python -m venv --system-site-packages` because it has
**no hard-coded path to the base interpreter**, so it survives app updates/moves
without re-downloading multi-GB torch. (venv is the fallback if `--target` shadowing
proves fragile for torch/onnxruntime — to be validated during implementation.)

**Version pinning:** the build writes `resources/runtime.json` recording the exact
`torch`/`torchvision`/`onnxruntime` versions baked into the bundled env. The GPU
installer pins to those same versions (just swapping the `+cpu` build for `+cu128`/
`+rocm`), guaranteeing torch/torchvision ABI compatibility.

## Overlay install location (issue #472)

The GPU overlay is a multi-GB download, so *where* it lands matters. Originally it
was hardcoded to `userData/backends/<accel>` — always on the system drive under
AppData/`.config`, even when the user installed the app to another drive. Now the
overlay root is configurable (`config.ts` `backendsRoot()`), with a per-platform
default (`computeDefaultBackendsRoot`):

- **Windows (packaged):** `<installDir>/backends` — i.e. *inside the folder the user
  picked in the installer* (`dirname(process.resourcesPath)`). The NSIS install is
  per-user (`perMachine:false`) and user-writable, so the heavy wheels follow the
  install location and stay off C: when the user chose another drive. Because
  `--target` installs carry no hard-coded base path, the overlay also survives app
  moves.
- **Linux / macOS:** `userData/backends`, unchanged — the AppImage is a read-only
  squashfs mount and a notarized `.app` must not be modified, so there's no writable
  install folder to use.

Users can override the default anywhere: the first-run wizard shows an install-location
picker when GPU is selected, and Settings → Compute → *Install location* changes it later.
Changing it after an overlay is installed **moves** the existing overlay (rename, falling
back to copy+delete across drives) so the multi-GB torch isn't re-downloaded — this also
replaces the "move it manually + symlink" workaround users were doing. The chosen path is
persisted to `userData/backends-location.json`; when it equals the computed default the
override is cleared, so the default keeps tracking the install dir across updates.

> Caveat: on Windows an in-place app update runs the NSIS uninstaller, which can remove
> files under `<installDir>`. If that wipes an overlay the marker disappears and the user
> re-installs it (no data loss — model weights and the library live elsewhere). Users who
> want update-proof overlays can point the location at a folder outside the install dir.

## Code changes

### Electron app (`electron/src/`)
- **`config.ts`** — drop `catalogBaseUrl`/`BackendCatalog`/`BackendEntry` catalog types.
  Add path helpers: `bundledRuntimeDir()` (= `process.resourcesPath/python`, dev fallback),
  `overlayDir(accel)` (= `backendsRoot()/<accel>`; the root is configurable — see
  *Overlay install location* above), and an `accel → (torch index
  URL, onnxruntime package)` map (mirrors `TORCH_INDEX`/`ONNX_PACKAGE` in the build script).
  Keep `PIXLSTASH_BACKEND_*` env override, repurposed to override the pip index URL
  (corporate mirrors/proxies).
- **`HardwareDetector.ts`** — keep detection as-is. Replace `recommendBackend`/
  `compatibleBackends` (which took a catalog) with `availableAccelerators(hw)` and a
  flag for which need an on-demand install vs. are covered by the bundled env.
- **`BackendManager.ts`** — replace catalog fetch / download / sha-verify / extract with:
  - `activeRuntime()` → bundled env, or a GPU overlay if one is installed+selected.
  - `installGpu(accel, onProgress)` → `pip install --target` the heavy wheels, parsing
    pip stdout for progress (phase + current line/bytes; pip's own bar is TTY-only).
  - `listInstalled()` / `setActive()` simplified to "which accel overlay is active".
  - `remove(accel)` → `rm -rf` the overlay dir.
- **`ServerProcess.ts`** — `interpreterPath()` resolves the bundled interpreter; when a
  GPU overlay is active, launch the *bundled* python with `PYTHONPATH=<overlay>` injected.
  `devInterpreter()` (repo `.venv`) unchanged.
- **`main.ts`** — `boot()` no longer fetches a catalog. Detect hardware, then **launch the
  bundled CPU/Metal env immediately** (instant, offline). If a GPU is present and no
  overlay is installed, surface a non-blocking "Enable NVIDIA/AMD GPU acceleration"
  action (in the existing Backends window) that runs `installGpu` with progress and
  relaunches the server on the overlay.
- **`preload.ts` / `renderer/`** — first-run UI changes from "choose & download a backend"
  to "running on CPU — [Install GPU acceleration]" with a pip progress view.

### `electron/package.json` (electron-builder config)
- Add `extraResources` to copy the prebuilt runtime dir into the app:
  `{ "from": "resources/python", "to": "python" }` (so it lands at `resourcesPath/python`).
- Gitignore `electron/resources/python` (build output, produced per-platform in CI / locally).

### Build scripts (`scripts/`)
- **Repurpose `build_backend_bundle.py` → `build_desktop_runtime.py`**: keep
  `fetch_standalone_python` + `populate_env` (accel = `cpu` on win/linux, `metal` on mac);
  **drop** `archive_zst` / sha / `.meta.json`. Output the `python/` dir into
  `electron/resources/`, plus `resources/runtime.json` with the pinned torch/onnx versions.
- **Delete** `build_backend_catalog.py`.

### CI (`.github/workflows/electron.yml`)
- **Delete** the `build-backends` matrix job and the `publish-catalog` job.
- **`build-electron`**: before `npm run dist`, run `build_desktop_runtime.py` on the native
  runner (CPU on win/linux, Metal on mac) to populate `electron/resources/python`, then
  electron-builder embeds it. Keep `build-wheel` (the wheel still publishes to PyPI via
  `publish-pypi.yml`).
- The semver/PEP-440 catalog-naming fix discussed earlier becomes **unnecessary** (no catalog).

### Website
- **Delete** `website/backends/` (catalog dir + README). No `pixlstash.dev/backends/*`
  endpoint needed.

## Desktop config: separate file, shared-able library
The desktop app keeps its **own** `server-config.json` under Electron's `userData`
and launches the backend with `--server-config <userData>/server-config.json`. It is
never the same file as a standalone pip/Docker install's config, so the two can't
overwrite each other's host/SSL/device settings. The backend stays config-agnostic;
two ephemeral signals come from the launcher:
- `PIXLSTASH_HOST`/`PIXLSTASH_PORT` — bind a free loopback port (existing).
- `PIXLSTASH_DEFAULT_DEVICE` — the device the *active runtime* provides (CPU bundle vs
  GPU overlay), so the config's `default_device` can't request a device this runtime
  lacks. (The earlier shared-config + force-SSL-off approach is dropped.)

Dev (`PIXLSTASH_DESKTOP_DEV=1`) keeps using the developer's default config/library.

## First-run setup (UX)
On first launch (no desktop config yet) the main window shows a **setup screen**
(`renderer/setup.html`) before starting the backend:
1. **Import** — if a standalone pip/Docker config is found (located via the backend's
   own platformdirs, `setup:probe`), offer to copy its values as defaults — most
   usefully the existing `image_root`, so an existing library is picked up automatically.
2. **Library folder** — a folder picker (defaults to the imported `image_root`, else
   `~/Pictures/PixlStash`).
3. **CPU vs GPU** — shown only when a discrete GPU is detected; "GPU" routes through the
   existing on-demand overlay install (`~2.5 GB`, progress UI). Mac = Metal (no choice),
   no GPU = CPU (no choice).
4. `setup:commit` writes the desktop config, installs/activates the chosen overlay, then
   boots the backend and navigates the window into the library.

Subsequent launches see the config exists → skip the wizard → straight into the library.

## Phase 2: LAN exposure + SSL (decided, not yet built)
The desktop window **always** talks plain HTTP over a private, ephemeral loopback port —
that never changes, regardless of SSL. LAN exposure adds a *second* uvicorn listener for the
same FastAPI app on the one event loop (`asyncio.gather` over two `uvicorn.Server`s), bound to
`0.0.0.0` on the user's chosen (shareable) port, optionally with SSL using the self-signed
cert we already generate in `_ensure_ssl_certificates`. Rationale (see design discussion):

- SSL is meaningless on loopback, so the window stays on HTTP — **no Electron cert-trust
  code**, and "the app opens" is decoupled from cert validity (an expired self-signed cert
  can't break the window).
- Single-port HTTPS + Electron cert pinning was considered and rejected for that coupling.
- Self-signed ⇒ LAN clients (phones/browsers) see a cert warning — accepted; standard for
  local-hosting users. Warning-free would need a trusted cert (mkcert local CA / real domain);
  out of scope.
- **Ports:** the loopback listener stays an **ephemeral free port** (hidden, conflict-proof,
  as today); the LAN listener defaults to **9537** — the standard, shareable PixlStash port,
  bound to `0.0.0.0`. A fixed port can collide (e.g. a pip/Docker PixlStash already on 9537),
  so a bind failure on the LAN listener must surface as a clear "port in use" message and must
  **not** take down the loopback window — the app still opens, only LAN sharing is unavailable.

Backend change is swapping the blocking `uvicorn.run(...)` for the programmatic
`Server`/`gather` form. Wizard gains a Network step (Local only / Local network → SSL
sub-option). Also deferred: wiring automatic update checks.

## Tradeoffs & risks (going in eyes-open)
- **Installer size** grows to ~300–500 MB (CPython + deps + CPU/Metal torch). Acceptable for
  a desktop AI app; comparable to peers.
- **GPU install needs network + working pip** — corporate TLS-interception/proxies can break
  it. Expose an index-URL/proxy override (env + setting). CPU/Metal works fully offline.
- **Less hermetic** than extracting one prebuilt, sha-pinned tarball: we run pip on the user's
  machine against PyTorch's index. Mitigate by pinning exact versions (from `runtime.json`);
  pip still verifies index hashes over TLS.
- **`--target` shadowing** for torch/onnxruntime must be validated (fallback: `venv
  --system-site-packages`, accepting base-path-coupling and recreate-on-update).
- **AMD ROCm**: torch from the rocm index, but `onnxruntime` stays CPU (no PyPI ROCm ORT) —
  same limitation as today.

## Verification
- **Local CPU**: `build_desktop_runtime.py --accel cpu` → `npm start` → app opens into the
  library with no network; server runs from the bundled env.
- **Local GPU** (on an NVIDIA box): trigger `installGpu('cu128')` → confirm overlay created,
  `torch.cuda.is_available()` true in the launched server, model inference uses the GPU.
- **Offline**: disconnect network → CPU/Metal launch still works; GPU action fails gracefully
  back to CPU.
- **Packaged**: `npm run dist` on each OS → install → first run offline (CPU/Metal) → GPU
  install online → relaunch on overlay.
- **dev loop** (`npm run dev`, repo `.venv`) unchanged.

## Rollout (phased)
1. `build_desktop_runtime.py` + `extraResources` + `ServerProcess` launches the bundled env
   (no GPU path yet). App works offline on CPU/Metal.
2. `BackendManager.installGpu` (+ overlay launch) + Backends-window GPU action + progress UI.
3. Delete `build_backend_catalog.py`, `website/backends/`, the `build-backends`/
   `publish-catalog` CI jobs; trim `config.ts`/`HardwareDetector` catalog code.
4. Docs: update `electron/README.md` and the backend section of the main `README.md`.
