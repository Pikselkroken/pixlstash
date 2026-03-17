# PixlStash
<p align="center">
  <img src="website/assets/ScreenshotGrid.jpg" alt="PixlStash Screenshot" width="800"/>
</p>

PixlStash is a local picture library server for organizing, filtering, and reviewing large image collections. It was just renamed from PixlVault due to name conflicts.

It provides:

- A browser-based interface
- Fast metadata and tag filtering
- Smart score sorting
- Character and set organization
- Local storage of your library data
- Simple keyboard shortcuts for scoring, selection, deletion and navigation.
- Integration with ComfyUI for running workflows on selected images within PixlStash.
- Plugin system for defining new filter operations that can be performed on a set of images.

PixlStash runs on your machine and serves the UI at a local web address.

## Install PixlStash

<p align="center">
  <a href="https://pixlstash.dev/install.html">
    <img src="website/assets/install-banner.svg" alt="Install PixlStash" width="320"/>
  </a>
</p>

Detailed installation instructions on <a href="http://pixlstash.dev/install.html">pixlstash.dev</a>.


## First run and data location

On first run, PixlStash creates a user config directory and stores:

- Server config
- Database
- Imported media files

> **Model downloads:** On first startup, PixlStash automatically downloads the AI models required for tagging, captioning, and quality scoring. This includes several hundred MB of model weights. PixlStash-managed models are stored in the platform user data directory:
>
> | OS | Path |
> |----|------|
> | **Linux** | `~/.local/share/pixlstash/downloaded_models/` |
> | **macOS** | `~/Library/Application Support/pixlstash/downloaded_models/` |
> | **Windows** | `%LOCALAPPDATA%\pixlstash\downloaded_models\` |
>
> Third-party models (Florence-2, CLIP, sentence-transformers, InsightFace) are cached by their respective libraries in the standard library cache directories (`~/.cache/huggingface/`, `~/.cache/torch/hub/`, `~/.insightface/`).
>
> An internet connection is required the first time the server starts. Subsequent starts use the cached models.

If you need to use a custom config path:

```bash
python -m pixlstash.app --server-config "C:\path\to\server-config.json"
```

## Server configuration

On first run, PixlStash generates a `server-config.json` file in the user config directory:

- **Linux / macOS:** `~/.config/pixlstash/server-config.json`
- **Windows:** `%LOCALAPPDATA%\pixlstash\server-config.json`

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

### Model locations

Different models use different automatic cache locations by default. PixlStash-managed models go into the platform user data directory, while third-party libraries (HuggingFace, open_clip, InsightFace) use their own caches. **If you don't care about this, this section of the README is not for you.**

If you DO care, the optional `model_locations` key lets you redirect individual models to a directory you control. Add `model_locations` to finetune the location of the models.

Each entry follows this schema:

```json
"<model-key>": {
  "path": "/absolute/path/to/directory",
  "download": true | false
}
```

- **`path`** — An absolute directory path, or `"auto"` (the default).
- **`download`** — Only meaningful when `path` is not `"auto"`.
  - `true` (default): download model weights into `path` the first time they are needed; the directory is created automatically.
  - `false`: require the model files to be **already present** at `path`; startup fails with a clear error if they are missing.

Any model key absent from `model_locations` defaults to `{"path": "auto"}`.

| Key                    | Default location (`"auto"`)                                      | Required files / layout when `download: false`                                                 |
| ---------------------- | ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `wd14_tagger`          | `<user_data_dir>/pixlstash/downloaded_models/`                   | `model.onnx`, `selected_tags.csv`                                                              |
| `custom_tagger`        | `<user_data_dir>/pixlstash/downloaded_models/`                   | `pixlstash-anomaly-tagger.safetensors`, `pixlstash-anomaly-tagger_meta.json`                   |
| `aesthetic_predictor`  | `<user_data_dir>/pixlstash/downloaded_models/`                   | `sa_0_4_vit_b_32_linear.pth` (for the default ViT-B-32 CLIP model)                            |
| `florence_captioner`   | HuggingFace transformers cache (`~/.cache/huggingface/`)         | `config.json` + model weights (standard HuggingFace snapshot)                                  |
| `sentence_transformer` | HuggingFace / sentence-transformers cache (`~/.cache/huggingface/`) | `config.json` + model weights (standard HuggingFace snapshot)                              |
| `clip`                 | open_clip / torch hub cache (`~/.cache/torch/hub/`)              | `open_clip_pytorch_model.bin` (see note below)                                                 |
| `face_detector`        | InsightFace default (`~/.insightface/`)                          | `models/buffalo_l/` subdirectory (see note below)                                              |

`<user_data_dir>` is platform-specific:

| OS | Path |
|----|------|
| **Linux** | `~/.local/share` |
| **macOS** | `~/Library/Application Support` |
| **Windows** | `%LOCALAPPDATA%` |

Example — all models redirected to a central read-only share:

```json
"model_locations": {
  "wd14_tagger":          { "path": "/opt/models/wd14",          "download": false },
  "florence_captioner":   { "path": "/opt/models/florence",      "download": false },
  "clip":                 { "path": "/opt/models/clip",          "download": false },
  "custom_tagger":        { "path": "/opt/models/custom_tagger", "download": false },
  "aesthetic_predictor":  { "path": "/opt/models/aesthetic",     "download": false },
  "sentence_transformer": { "path": "/opt/models/sbert",        "download": false },
  "face_detector":        { "path": "/opt/models/insightface",  "download": false }
}
```

Startup validates every `download: false` entry and fails with an explicit error if any required file is missing.

#### CLIP directory layout

Download `open_clip_pytorch_model.bin` from the HuggingFace repository [`laion/CLIP-ViT-B-32-laion2B-s34B-b79K`](https://huggingface.co/laion/CLIP-ViT-B-32-laion2B-s34B-b79K/tree/main) and place it directly inside the configured `path`. The directory must look like:

```
<path>/
  open_clip_pytorch_model.bin
```

#### InsightFace directory layout

InsightFace expects its model pack under a fixed subdirectory structure inside the configured `path`:

```
<path>/
  models/
    buffalo_l/
      det_10g.onnx
      genderage.onnx
      w600k_r50.onnx
```

To stage this offline, run the following on an internet-connected machine first, then copy the `<path>/models/buffalo_l/` directory to your target host:

```python
from insightface.app import FaceAnalysis
app = FaceAnalysis(root="<path>")
app.prepare(ctx_id=-1)  # downloads buffalo_l on first call
```

### Example config

```json
{
  "host": "localhost",
  "port": 9537,
  "log_level": "info",
  "require_ssl": false,
  "image_root": "/home/user/.config/pixlstash/images",
  "watch_folders": [
    { "folder": "/path/to/photos", "delete_after_import": false }
  ],
  "default_device": "cpu",
  "generate_thumbnails_on_startup": true,
  "model_locations": {}
}
```

## Upgrade PixlStash

<p align="center">
  <a href="https://pixlstash.dev/upgrade.html">
    <img src="website/assets/upgrade-banner.svg" alt="Upgrade PixlStash" width="320"/>
  </a>
</p>

Detailed installation instructions on <a href="http://pixlstash.dev/upgrade.html">pixlstash.dev</a>.

## Installing plugins

PixlStash supports built-in plugins and user-created plugins.

### User plugin directory

Place your `.py` plugin files in the platform-specific user data directory. PixlStash logs the exact path on startup.

| OS | Path |
|----|------|
| **Linux** | `~/.local/share/pixlstash/image-plugins/user/` |
| **macOS** | `~/Library/Application Support/pixlstash/image-plugins/user/` |
| **Windows** | `%LOCALAPPDATA%\pixlstash\image-plugins\user\` |

### Writing a plugin

Use the template from `pixlstash/image_plugins/built-in/plugin_template.py` in the source repository as a starting point:

1. Create a new `.py` file in your user plugin directory.
2. Subclass `ImagePlugin`, set a unique `name` and `plugin_id`, and implement `run()`.
3. Restart PixlStash Server — plugins are loaded at startup.

`plugin_template.py` is ignored by plugin discovery and will not be loaded as a plugin.


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
It some cases you may have to uninstall onnxruntime-gpu and reinstall it.

Verify providers:

```bash
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

Expected output should include `CUDAExecutionProvider`.

If you prefer CPU mode, set `"default_device": "cpu"` in `server-config.json`.

