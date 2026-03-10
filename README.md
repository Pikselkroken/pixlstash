# PixlVault
<p align="center">
  <img src="website/assets/ScreenshotGrid.jpg" alt="PixlVault Screenshot" width="800"/>
</p>

PixlVault is a local picture library server for organizing, filtering, and reviewing large image collections.

It provides:

- A browser-based interface
- Fast metadata and tag filtering
- Smart score sorting
- Character and set organization
- Local storage of your library data
- Simple keyboard shortcuts for scoring, selection, deletion and navigation.

PixlVault runs on your machine and serves the UI at a local web address.

## Install PixlVault

Choose one installation method.

### Option 1: Windows installer

Use this if you want the easiest setup on Windows.

1. Go to the GitHub Releases page for this repository.
2. Download the latest Windows installer `.exe`.
3. Run the installer.
4. Start PixlVault Server from the Start Menu shortcut.
5. Open your browser to `http://localhost:9537`.

> **Windows SmartScreen warning:** Because the installer is not yet signed with a paid code-signing certificate, Windows SmartScreen may show a red "Windows protected your PC" dialog when you run it. This is expected. Click **More info** and then **Run anyway** to proceed with the installation.
>
> <img src="website/assets/SmartScreen.png" alt="Windows SmartScreen – click More info then Run anyway" width="420"/>

## Option 2: Install from PyPI

Use this if you already have Python and want a pip install.

NOTE: You really, really should do this in a virtual environment:

```bash
python -m venv venv
. venv/bin/activate
```

Requirements:

- Python 3.10 or newer

Install:

```bash
pip install pixlvault
```

Run:

```bash
pixlvault-server
```

Then open:

```text
http://localhost:9537
```

## Option 3: Clone and run manually

Use this if you want to run from source.

Requirements:

- Python 3.10 or newer
- Node.js 20 or newer
- npm

Steps:

```bash
git clone https://github.com/Pixelurgy/pixlvault.git
cd pixlvault

python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install --upgrade pip
pip install -e .

cd frontend
npm ci
npm run build
cd ..

pixlvault-server
```

Then open:

```text
http://localhost:9537
```

## Option 4: Docker (GPU — Linux / WSL2 on Windows)

Use this if you want a fully self-contained container with CUDA support. A pre-built image is published to the GitHub Container Registry on every release — no clone required.

### Prerequisites

#### On Linux (native Docker)

1. Install [Docker Engine](https://docs.docker.com/engine/install/).
2. Install the NVIDIA Container Toolkit:

```bash
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -sL "https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list" \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

3. Verify GPU access:

```bash
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

#### On Windows (WSL2)

1. Install [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) with Ubuntu 24.04.
2. Install an NVIDIA Windows driver ≥ 525 from [nvidia.com/drivers](https://www.nvidia.com/drivers) — **do not** install a driver inside WSL2 itself; the Windows driver is shared automatically.
3. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) with the **WSL2 backend** enabled, or install Docker Engine directly inside WSL2 as per the Linux steps above.
4. Install the NVIDIA Container Toolkit inside your WSL2 distro (same commands as the Linux section above).
5. Verify GPU access from WSL2:

```bash
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

### Run (pre-built image — recommended)

Pull and start the container:

```bash
docker run -d \
  --runtime nvidia \
  -e NVIDIA_VISIBLE_DEVICES=all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
  -e PIXLVAULT_HOST=0.0.0.0 \
  -p 9537:9537 \
  -v pixlvault-home:/home/pixlvault \
  --name pixlvault \
  ghcr.io/pixelurgy/pixlvault:latest
```

Open `http://localhost:9537` in your browser.

All data (images, database, config, downloaded models) is stored in the `pixlvault-home` named volume and persists across restarts.

To update to the latest release:

```bash
docker pull ghcr.io/pixelurgy/pixlvault:latest
docker rm -f pixlvault
# re-run the docker run command above
```

To pin to a specific release, replace `latest` with a version tag, e.g. `ghcr.io/pixelurgy/pixlvault:0.9.1`.

#### Optional: docker-compose.yml

If you prefer Compose for easier management:

```yaml
services:
  pixlvault:
    image: ghcr.io/pixelurgy/pixlvault:latest
    runtime: nvidia
    ports:
      - "9537:9537"
    volumes:
      - pixlvault-home:/home/pixlvault
    environment:
      PIXLVAULT_HOST: "0.0.0.0"
      PIXLVAULT_PORT: "9537"
      NVIDIA_VISIBLE_DEVICES: all
      NVIDIA_DRIVER_CAPABILITIES: compute,utility
    restart: unless-stopped

volumes:
  pixlvault-home:
```

```bash
docker compose up -d
# To update:
docker compose pull && docker compose up -d
```

### Run (build from source)

Use this if you need a custom build or want to run unreleased changes.

```bash
git clone https://github.com/Pixelurgy/pixlvault.git
cd pixlvault

docker compose up --build
```

Then open `http://localhost:9537`.




## First run and data location

On first run, PixlVault creates a user config directory and stores:

- Server config
- Database
- Imported media files

> **Model downloads:** On first startup, PixlVault automatically downloads the AI models required for tagging, captioning, and quality scoring. This includes several hundred MB of model weights. Downloads happen in the background and are stored in the platform user data directory:
>
> | OS | Path |
> |----|------|
> | **Linux** | `~/.local/share/pixlvault/downloaded_models/` |
> | **macOS** | `~/Library/Application Support/pixlvault/downloaded_models/` |
> | **Windows** | `%LOCALAPPDATA%\pixlvault\downloaded_models\` |
>
> An internet connection is required the first time the server starts. Subsequent starts use the cached models.

If you need to use a custom config path:

```bash
python -m pixlvault.app --server-config "C:\path\to\server-config.json"
```

## Server configuration

On first run, PixlVault generates a `server-config.json` file in the user config directory:

- **Linux / macOS:** `~/.config/pixlvault/server-config.json`
- **Windows:** `%LOCALAPPDATA%\pixlvault\server-config.json`

You can also supply a custom path with `--server-config <path>`.

Edit the file and restart the server to apply changes.

### Network and port

| Key            | Default       | Description                                                                                                                                               |
| -------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `host`         | `"localhost"` | Address the server binds to. Change to `"0.0.0.0"` to expose the server on the local network.                                                             |
| `port`         | `9537`        | TCP port the server listens on.                                                                                                                           |
| `cors_origins` | `[]`          | Extra origins allowed to make credentialed cross-origin requests. `localhost`, `127.0.0.1`, and the server's own LAN IP are always permitted on any port. |

At startup the server detects its own LAN IP and automatically allows it on any port. This means the Vite dev server works over LAN (`http://192.168.1.5:5173` → `http://192.168.1.5:9537`) without any extra configuration, as long as network access is enabled via `host`.

Use `cors_origins` only if you need to allow origins on a different machine entirely.

### SSL / HTTPS

| Key               | Default                     | Description                                                                   |
| ----------------- | --------------------------- | ----------------------------------------------------------------------------- |
| `require_ssl`     | `false`                     | Enable HTTPS. When `true`, the server will use the key and certificate below. |
| `ssl_keyfile`     | `<config_dir>/ssl/key.pem`  | Path to the SSL private key file.                                             |
| `ssl_certfile`    | `<config_dir>/ssl/cert.pem` | Path to the SSL certificate file.                                             |
| `cookie_samesite` | `"Lax"`                     | `SameSite` attribute for session cookies (`"Lax"`, `"Strict"`, or `"None"`).  |
| `cookie_secure`   | `false`                     | Set the `Secure` flag on session cookies. Enable when serving over HTTPS.     |

### Storage

| Key             | Default               | Description                                                                                                                  |
| --------------- | --------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `image_root`    | `<config_dir>/images` | Directory where imported media files are stored.                                                                             |
| `watch_folders` | `[]`                  | List of folder entries to watch for new images and automatically import them. Each entry is an object with the fields below. |

Each entry in `watch_folders` has the following fields:

| Field                 | Type    | Default | Description                                                                            |
| --------------------- | ------- | ------- | -------------------------------------------------------------------------------------- |
| `folder`              | string  | —       | Absolute path to the directory to monitor (recursively).                               |
| `delete_after_import` | boolean | `false` | When `true`, source files are deleted from the watch folder after a successful import. |

Example:

```json
"watch_folders": [
  { "folder": "/home/user/downloads/photos", "delete_after_import": false },
  { "folder": "/mnt/camera", "delete_after_import": true }
]
```

### Processing

| Key                              | Default | Description                                          |
| -------------------------------- | ------- | ---------------------------------------------------- |
| `default_device`                 | `"cpu"` | Device used for AI processing (`"cpu"` or `"cuda"`). |
| `generate_thumbnails_on_startup` | `true`  | Generate missing thumbnails when the server starts.  |

### Logging

| Key         | Default                   | Description                                                  |
| ----------- | ------------------------- | ------------------------------------------------------------ |
| `log_level` | `"info"`                  | Log verbosity (`"debug"`, `"info"`, `"warning"`, `"error"`). |
| `log_file`  | `<config_dir>/server.log` | Path to the log file.                                        |

### Example config

```json
{
  "host": "localhost",
  "port": 9537,
  "log_level": "info",
  "require_ssl": false,
  "image_root": "/home/user/.config/pixlvault/images",
  "watch_folders": [
    { "folder": "/path/to/photos", "delete_after_import": false }
  ],
  "default_device": "cpu",
  "generate_thumbnails_on_startup": true
}
```
## Installing CUDA 12.8 for GPU Acceleration (Windows & Linux)

PixlVault can run fully on CPU, but GPU acceleration requires **CUDA 12.8** plus the corresponding CUDA-enabled PyTorch and ONNX Runtime packages.

1. Install or update your NVIDIA driver (must support CUDA 12.x).
2. Install the CUDA Toolkit for your distribution from NVIDIA’s CUDA downloads page. [1](https://developer.nvidia.com/cuda-toolkit-archive)
3. Verify installation:  
   ```bash
   nvcc --version
   nvidia-smi
   ```
4. Install PyTorch with CUDA 12.8 (from the pixlvault install folder):
   ```bash
   venv\Scripts\Activate
   pip install torch torchvision --force-reinstall --index-url https://download.pytorch.org/whl/cu128
   ```  
   
6. Install ONNX Runtime GPU:  
   ```bash
   pip uninstall -y onnxruntime
   pip install onnxruntime-gpu
   ```

### Verify GPU availability

#### Linux

```bash
python - <<EOF
import torch
print("CUDA available:", torch.cuda.is_available())
EOF
```

#### Windows
```powershell
py -c "import torch; print('CUDA available:', torch.cuda.is_available()); \
print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"
```


## Updating PixlVault

### PyPI install

```bash
pip install --upgrade pixlvault
```

### Source install

Pull latest changes, rebuild frontend, and reinstall:

```bash
git pull
cd frontend
npm ci
npm run build
cd ..
pip install -e .
```

## Troubleshooting

- If the page does not load, confirm the server process is running.
- If port `9537` is in use, set a different port in your server config file.
- If frontend assets are missing, rebuild frontend with `npm run build` and restart the server.

### GPU startup fails (`CUDAExecutionProvider` unavailable)

If startup reports that ONNX `CUDAExecutionProvider` is unavailable, you likely have CPU-only ONNX Runtime installed.

Fix your environment:

```bash
pip uninstall -y onnxruntime
pip install onnxruntime-gpu
```

Verify providers:

```bash
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

Expected output should include `CUDAExecutionProvider`.

If you prefer CPU mode, set `"default_device": "cpu"` in `server-config.json`.

## Installing plugins

PixlVault supports built-in plugins and user-created plugins.

### User plugin directory

Place your `.py` plugin files in the platform-specific user data directory. PixlVault logs the exact path on startup.

| OS | Path |
|----|------|
| **Linux** | `~/.local/share/pixlvault/image-plugins/user/` |
| **macOS** | `~/Library/Application Support/pixlvault/image-plugins/user/` |
| **Windows** | `%LOCALAPPDATA%\pixlvault\image-plugins\user\` |

### Writing a plugin

Use the template from `pixlvault/image_plugins/built-in/plugin_template.py` in the source repository as a starting point:

1. Create a new `.py` file in your user plugin directory.
2. Subclass `ImagePlugin`, set a unique `name` and `plugin_id`, and implement `run()`.
3. Restart PixlVault Server — plugins are loaded at startup.

`plugin_template.py` is ignored by plugin discovery and will not be loaded as a plugin.
