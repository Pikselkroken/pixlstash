#!/usr/bin/env bash
#
# Build the PixlStash desktop app end-to-end for the current platform:
#   frontend  ->  pixlstash wheel  ->  bundled runtime  ->  electron-builder installers
#
# Produces electron/release/*.AppImage + *.deb on Linux, *.dmg + *.zip on macOS.
#
# Usage:
#   scripts/build_desktop.sh [--skip-frontend] [--skip-wheel] [--skip-runtime]
#                            [-- <electron-builder args>]
#
# Examples:
#   scripts/build_desktop.sh                       # full build for this OS
#   scripts/build_desktop.sh --linux AppImage      # only the AppImage
#   scripts/build_desktop.sh --skip-runtime        # reuse electron/resources/python
#
# Env:
#   PYTHON   Python interpreter to drive the build (default: python3). Point this
#            at a venv so `python -m build` doesn't touch your system environment.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
SKIP_FRONTEND=0
SKIP_WHEEL=0
SKIP_RUNTIME=0
DIST_ARGS=()

usage() { sed -n '3,18p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-frontend) SKIP_FRONTEND=1 ;;
    --skip-wheel)    SKIP_WHEEL=1 ;;
    --skip-runtime)  SKIP_RUNTIME=1 ;;
    -h|--help)       usage 0 ;;
    --)              shift; DIST_ARGS+=("$@"); break ;;
    *)               DIST_ARGS+=("$1") ;;
  esac
  shift
done

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

# --- detect platform -> os/arch/accel for the bundled runtime ---
case "$(uname -s)" in
  Linux)  OS=linux; ACCEL=cpu ;;
  Darwin) OS=mac;   ACCEL=metal ;;
  *) die "unsupported OS '$(uname -s)' — build on Linux/macOS, or use CI for Windows." ;;
esac
case "$(uname -m)" in
  x86_64|amd64)  ARCH=x64 ;;
  arm64|aarch64) ARCH=arm64 ;;
  *) die "unsupported arch '$(uname -m)'." ;;
esac
log "Target: $OS/$ARCH (bundled accelerator: $ACCEL)"

# --- 0. frontend (baked into the wheel as package data) ---
if [ "$SKIP_FRONTEND" -eq 0 ]; then
  log "Building frontend"
  (cd frontend && npm ci && npm run build)
else
  log "Skipping frontend build"
fi

# --- 1. pixlstash wheel (installed into the bundled env) ---
if [ "$SKIP_WHEEL" -eq 0 ]; then
  log "Building pixlstash wheel"
  "$PYTHON" -m build --version >/dev/null 2>&1 || "$PYTHON" -m pip install --upgrade build
  "$PYTHON" -m build --wheel
fi
WHEEL="$(ls -t dist/pixlstash-*.whl 2>/dev/null | head -n1 || true)"
[ -n "$WHEEL" ] || die "no wheel in dist/ — run without --skip-wheel."
log "Using wheel: $WHEEL"

# --- 2. bundled runtime -> electron/resources/{python,runtime.json} ---
if [ "$SKIP_RUNTIME" -eq 0 ]; then
  log "Building bundled runtime (downloads CPython + torch — this is slow)"
  "$PYTHON" scripts/build_desktop_runtime.py \
    --wheel "$WHEEL" \
    --os "$OS" --arch "$ARCH" --accel "$ACCEL" \
    --output-dir electron/resources
else
  log "Skipping runtime build (reusing electron/resources/python)"
  [ -d electron/resources/python ] || die "electron/resources/python missing — run without --skip-runtime."
fi

# --- 3. electron-builder installers ---
log "Building Electron installers"
if [ "${#DIST_ARGS[@]}" -gt 0 ]; then
  (cd electron && npm install && npm run dist -- "${DIST_ARGS[@]}")
else
  (cd electron && npm install && npm run dist)
fi

log "Done. Artifacts in electron/release/:"
ls -1 electron/release/ 2>/dev/null | grep -Ei '\.(AppImage|deb|dmg|zip|exe)$' || echo "(none found)"
