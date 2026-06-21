#!/usr/bin/env bash
set -euo pipefail
APP=pixlstash-demo
# Restore the real demo image (rebuild + push first if it changed; see fly.toml).
fly deploy -a "$APP" -c fly.toml --image registry.fly.io/pixlstash-demo:latest
fly status -a "$APP"
echo "Re-check before walking away: read-only on, exposure intended, deps audited."
