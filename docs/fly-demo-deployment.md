# Fly.io Demo Site Deployment

The demo site runs at **pixlstash-demo.fly.dev**. It uses a pre-built image with the vault database and all images baked in — no persistent volume is used, so the data is static and read-only.

## Prerequisites

### 1. Install flyctl

```bash
curl -L https://fly.io/install.sh | sh
flyctl auth login
```

### 2. Prepare demo data

Create a `demo-data/` directory alongside the repo root before building:

```
demo-data/
  server-config.json   # image_root must point to "/home/pixlstash/images"
  images/              # vault.db + all picture files
```

The `demo-data/images/` directory must contain:
- `vault.db` — the fully processed SQLite database
- All picture files referenced by the database

The `Dockerfile.demo` will automatically rewrite the following fields in `server-config.json` at build time, so you don't need to set them manually:
- `image_root` → `/home/pixlstash/images`
- `host` → `0.0.0.0`
- `port` → `8080`
- `disable_password_auth` → `true` (nobody can log in via password on the demo)
- `disable_background_workers` → `true` (no models are loaded, no background tasks run)

## Preparing the Demo Database

The demo image bakes in a fully processed `vault.db` and all picture files. Follow these steps to create or update the demo dataset using a local pixlstash instance.

### Step 1 — Create a local server config for the demo data

Create `demo-data/server-config.json` pointing at the demo images directory:

```json
{
  "host": "localhost",
  "port": 9537,
  "image_root": "/absolute/path/to/demo-data/images",
  "default_device": "cpu",
  "require_ssl": false,
  "log_level": "info"
}
```

Replace `/absolute/path/to/demo-data/images` with the real absolute path on your machine. pixlstash stores `vault.db` directly inside `image_root`, so this is where both the database and all picture files will live.

### Step 2 — Run pixlstash locally against the demo data

```bash
python -m pixlstash.app --server-config demo-data/server-config.json
```

On first run the server will create `demo-data/images/vault.db` and prompt you to set a username and password (used only for this local session).

### Step 3 — Import and process images

Open `http://localhost:9537` in your browser and:

1. Use the **Import** panel to import the pictures you want in the demo.
2. Wait for all background tasks to complete — tagging, quality scoring, and embedding generation must all finish before you build the image. The task progress indicator in the toolbar shows when processing is done.
3. Curate the library: set scores, add tags, organise sets, etc.
4. Stop the server (`Ctrl+C`) once everything is processed.

The fully processed `vault.db` is now at `demo-data/images/vault.db`.

### Step 4 — Verify the data layout

Before building, confirm the layout looks like:

```
demo-data/
  server-config.json       ← local config (image_root may be any local path — Dockerfile.demo rewrites it)
  images/
    vault.db               ← fully processed database
    <picture files ...>    ← all imported image files
```

The `Dockerfile.demo` will rewrite `image_root` (and other fields) at build time, so the path in your local `server-config.json` does not need to match the in-container path.

---

## Build, Push and Deploy

### Step 1 — Build the image

```bash
docker build -f Dockerfile.demo -t registry.fly.io/pixlstash-demo:latest .
```

This builds a multi-stage image: the Vue frontend is compiled first, then the Python runtime image is assembled with the demo data baked in. Expect the first build to take ~10–15 minutes due to PyTorch and model downloads; subsequent builds are faster thanks to Docker layer caching.

### Step 2 — Push to the Fly.io registry

```bash
flyctl auth docker && docker push registry.fly.io/pixlstash-demo:latest
```

`flyctl auth docker` configures Docker to authenticate against `registry.fly.io` using your Fly.io credentials. Only needs to be run once per session (or after token expiry).

### Step 3 — Test the image locally

Run the image locally and verify it starts correctly before pushing to production:

```bash
docker run --rm -p 8080:8080 registry.fly.io/pixlstash-demo:latest
```

Then in a second terminal:

```bash
# Check the app responds (expects HTTP 200)
curl -sf http://localhost:8080/ -o /dev/null && echo "OK" || echo "FAILED"
```

Once satisfied, stop the container with `Ctrl+C`.

### Step 4 — Deploy

```bash
flyctl deploy --config fly.toml --image registry.fly.io/pixlstash-demo:latest
```

Fly.io will pull the image from its own registry and roll it out. Because `auto_stop_machines = 'suspend'` and `min_machines_running = 0`, the machine will suspend when idle and resume in under a second on the next request.

## Configuration (`fly.toml`)

| Setting | Value | Notes |
|---|---|---|
| `app` | `pixlstash-demo` | Fly app name |
| `primary_region` | `arn` | Stockholm |
| `PIXLSTASH_PORT` | `8080` | Must match `internal_port` |
| `internal_port` | `8080` | Port the app listens on inside the container |
| `memory` | `2gb` | Needed for model loading (even with workers disabled, image serving uses memory) |
| `auto_stop_machines` | `suspend` | Machine suspends when idle; resumes in <1s on next request |
| `min_machines_running` | `0` | No always-on machines |

## Updating the Demo Data

To refresh the demo with a new database or new images:

1. Replace the contents of `demo-data/images/` with the updated `vault.db` and picture files.
2. Rebuild and redeploy using the steps above.

Because the data is baked into the image, there is no migration step — the new `vault.db` is already fully processed.

## Troubleshooting

**Check live logs:**
```bash
flyctl logs --app pixlstash-demo
```

**SSH into a running machine:**
```bash
flyctl ssh console --app pixlstash-demo
```

**List machines:**
```bash
flyctl machine list --app pixlstash-demo
```
