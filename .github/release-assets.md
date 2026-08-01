## Which download do I want?

PixlStash ships as a **desktop app** — a window around a local server, with
everything bundled and nothing to set up afterwards. Pick the file for your
platform:

| File | Platform | Size | Notes |
|------|----------|------|-------|
| `PixlStash-desktop-*-win-x64.exe` | Windows | ~400 MB | Installer. Digitally signed — Windows shows **Open Source Developer Gaute Lindkvist** as the publisher |
| `PixlStash-desktop-*-linux-x86_64.AppImage` | Linux | ~690 MB | Portable — `chmod +x` and run, nothing to install |
| `PixlStash-desktop-*-linux-amd64.deb` | Debian / Ubuntu | ~500 MB | `sudo apt install ./PixlStash-desktop-*.deb` |
| `PixlStash-desktop-*-mac-arm64.dmg` | macOS (Apple Silicon) | ~430 MB | Signed and notarised |

They are large because each one bundles its own Python runtime and the AI
stack — that is the whole point: download, install, done.

### Running it headless instead

Prefer PixlStash as a service on a machine you reach from elsewhere? Windows
gets a dedicated server installer, because setting a Python service up by hand
is awkward there:

| File | Platform | Size | Notes |
|------|----------|------|-------|
| `pixlstash-server-*-windows-x64.exe` | Windows | ~9 MB | Needs Python 3.10+; installs into a managed virtualenv. Digitally signed |

On **Linux and macOS**, where that is already easy, there is no separate
download — use whichever fits your setup:

- **Docker:** `docker pull ghcr.io/pikselkroken/pixlstash` (CPU) or
  `…:X.Y.Z-gpu` (NVIDIA CUDA)
- **PyPI:** `pip install pixlstash`, then run `pixlstash`

### Other install options

- **PyPI + a native window:** `pip install pixlstash[desktop]`, then run
  `pixlstash-desktop` — opens the server in the OS's native webview, no Electron
  download. On Linux use `pixlstash[desktop-qt]` for a pip-only backend, or
  install system WebKitGTK.

> **About the Windows warning:** the installers are Authenticode-signed, so
> Windows names the publisher instead of saying "Unknown publisher". SmartScreen
> may still interrupt for a while — that reputation builds as more people
> download the signed installers. If a release carries `…-unsigned.exe` files,
> signing was unavailable for that build: they still work, but Windows cannot
> tell you who made them.
