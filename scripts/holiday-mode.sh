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
