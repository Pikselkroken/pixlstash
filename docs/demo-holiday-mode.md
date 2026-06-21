# Demo holiday mode

Swap the live `pixlstash-demo` Fly app for the static `website/demo-holiday.html`
while nobody's watching it, then restore. Holiday mode deploys an image containing
**only nginx + that one HTML file**, so the PixlStash app (upload/parse, API, the
whole kill chain) isn't running at all. It also closes the current
`auto_start_machines = true` behaviour, which today wakes the demo on any inbound
request.

CSO sign-off: recorded as a **security improvement**, not an accepted risk.
Owner: Gaute. Revisit: on return (flip back, re-verify exposure).

## One-time setup

**Token must be app-scoped, not org-wide.** An org token in CI is the thing that
ruins a holiday.

```bash
fly tokens create deploy -a pixlstash-demo   # store as FLY_API_TOKEN
```

## Files (commit these)

The served page is the existing **`website/demo-holiday.html`**. It pulls its logo
and fonts from absolute URLs (pixlstash.dev, Google Fonts), so the single file is
all the image needs. (It renders only while pixlstash.dev is up, which it is, since
that's a separate site from this demo.)

`holiday/nginx.conf` — serve the page for every path, no directory listing:

```nginx
server {
  listen 8080;            # matches the demo's internal_port
  root /usr/share/nginx/html;
  autoindex off;
  location / { try_files /index.html =404; }
}
```

`holiday/Dockerfile` — pinned base; `COPY` paths are relative to the build
context, which is the repo root (the `.` in the deploy command):

```dockerfile
FROM nginx:1.27-alpine
COPY holiday/nginx.conf /etc/nginx/conf.d/default.conf
COPY website/demo-holiday.html /usr/share/nginx/html/index.html
```

The repo-root `.dockerignore` excludes `website/`, so the build context will not
contain `website/demo-holiday.html` unless it is re-included. Add this line to
`.dockerignore` (after the `website/` exclude) or the build fails with
"`/website/demo-holiday.html`: not found":

```gitignore
!website/demo-holiday.html
```

`holiday/fly.holiday.toml` — single web process, no volume, no secrets, no wake:

```toml
app = 'pixlstash-demo'
primary_region = 'arn'

[build]
  # flyctl resolves this relative to THIS config file's dir (holiday/), not the
  # repo root. So it is just 'Dockerfile'. The build CONTEXT is still the repo
  # root (the `.` in the deploy command), so the COPY paths above resolve.
  dockerfile = 'Dockerfile'

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = false          # do NOT let a request wake anything
  min_machines_running = 1

[[vm]]
  memory = '256mb'
  cpu_kind = 'shared'
  cpus = 1
```

## scripts/holiday-mode.sh

```bash
#!/usr/bin/env bash
set -euo pipefail
APP=pixlstash-demo
# Run from repo root so the build context includes website/ and holiday/.
fly deploy . -a "$APP" -c holiday/fly.holiday.toml --yes
# Verify the app is genuinely gone: running image must be the nginx build,
# nothing else left up.
fly image show -a "$APP"
fly machine list -a "$APP"
echo "Confirm above: image is nginx:1.27-alpine, only one web machine running."
```

## scripts/back-to-work.sh

```bash
#!/usr/bin/env bash
set -euo pipefail
APP=pixlstash-demo
# Restore the real demo image (rebuild + push first if it changed; see fly.toml).
fly deploy -a "$APP" -c fly.toml --image registry.fly.io/pixlstash-demo:latest
fly status -a "$APP"
echo "Re-check before walking away: read-only on, exposure intended, deps audited."
```

## Block-on checklist (CSO)

- [ ] `FLY_API_TOKEN` is an **app-scoped deploy token**, not an org token.
- [ ] If wired to GitHub Actions: `workflow_dispatch` only, on a protected branch,
      the flyctl action pinned to a commit SHA, token in repo secrets (not YAML).
- [ ] After `holiday-mode`, every machine in `fly machine list` runs the nginx
      build (look for the `maintainer=NGINX` label / the 256MB size); no machine
      still runs the app image. Note: `pixlstash-demo` currently has **6 machines
      across regions** (arn/iad/sjc/bom), so a rolling deploy updates all 6 to
      nginx, not "one machine." That is fine: 6 copies of an app-less static page
      are exactly as safe as one. Getting to a literal single box (`fly scale
      count 1`) is a separate, destructive step, not part of holiday mode.
- [ ] Restore is by mutable `:latest`. The known-good demo image at the time of
      writing is `registry.fly.io/pixlstash-demo@sha256:465ff42ba3729290acbbc4cf1e6da161494f4f7c9af01397d02a918207d117fc`.
      Prefer restoring by digest so a later push cannot silently change what comes
      back.
- [ ] The baked-in demo data (vault.db + images in the real image) is **synthetic**,
      not real. It isn't present in the holiday image at all, which is the point.
