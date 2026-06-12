# PixlStash Desktop (Electron)

A native desktop shell (Windows/macOS/Linux) around the local PixlStash server.
Following the **LM Studio model** for the shell (a local server + a hardware-matched
runtime manager), but with one key difference: the heavy ML stack is **not** an
artifact we host. Instead:

- The installer **bundles a fully-working runtime** — standalone CPython + the
  `pixlstash` wheel + all deps + **CPU** torch on Windows/Linux, or **Metal** torch
  on macOS (the default PyPI macOS wheel includes MPS). The app runs offline out of
  the box.
- **GPU acceleration is added on demand.** If a discrete GPU is detected, the app
  pip-installs the heavy wheels (`torch+cu128`/`torch+rocm`, `onnxruntime-gpu`)
  straight from PyPI / PyTorch into a writable overlay — **we host nothing**.

## How it works

1. **HardwareDetector** picks the accelerators available on this machine
   (NVIDIA/CUDA, Apple/Metal, AMD/ROCm, or CPU).
2. **ServerProcess** launches the bundled interpreter (`resources/python`) and
   spawns `python -m pixlstash.app` on a free `127.0.0.1` port with a one-time
   `PIXLSTASH_DESKTOP_SESSION` token, polling `/version` until healthy.
3. The main window injects that token as the `session_id` cookie and loads the
   server URL — opening **straight into the library, no login prompt**. This is
   instant: no download on first run (Macs are GPU-accelerated immediately).
4. **BackendManager** (the Backends window, Cmd/Ctrl+Shift+R) offers any GPU
   accelerator the bundled env doesn't already cover. Installing one runs
   `pip install --target userData/backends/<accel> torch… onnxruntime-gpu…`
   (pinned to the bundled versions via a constraints file), then relaunches the
   server with that overlay prepended to `PYTHONPATH` so the GPU torch shadows the
   bundled CPU build. The bundled env stays immutable; everything mutable lives in
   `userData/`.

AI **model weights** keep auto-downloading to the user data dir on first server
run, exactly as the pip/Docker installs do.

## Develop

```bash
npm install
# Run against a local interpreter (the repo .venv) — no bundled runtime needed:
npm run dev
```

`npm run dev` sets `PIXLSTASH_DESKTOP_DEV=1`, which makes `ServerProcess` launch
the repo's `../.venv` interpreter (override with `PIXLSTASH_DEV_BACKEND=/path/to/python`)
and skips the bundled runtime entirely.

To exercise the **bundled** path locally (what `npm start` / installers use), first
build the runtime into `electron/resources/` once, then run/package as usual:

```bash
python ../scripts/build_desktop_runtime.py \
  --wheel ../dist/pixlstash-*.whl \
  --os linux --arch x64 --accel cpu \
  --output-dir resources
npm start            # bundled env, or `npm run dist` to package
```

Useful env vars:

| Var | Purpose |
| --- | --- |
| `PIXLSTASH_DESKTOP_DEV=1` | Dev passthrough to a local interpreter (repo `.venv`). |
| `PIXLSTASH_DEV_BACKEND` | Explicit interpreter path for dev. |
| `PIXLSTASH_PIP_INDEX_URL` | Override the pip index for GPU installs (corporate mirror/proxy). |

## Build installers

```bash
npm run dist          # electron-builder for the current OS
```

CI (`.github/workflows/electron.yml`) builds the wheel once, then per OS builds the
bundled runtime with `scripts/build_desktop_runtime.py` and embeds it via
electron-builder `extraResources` before packaging. The version is mirrored from the
repo `pyproject.toml` (PEP 440 → semver) by `scripts/sync-version.mjs` during
`npm run build`.

[python-build-standalone]: https://github.com/astral-sh/python-build-standalone
