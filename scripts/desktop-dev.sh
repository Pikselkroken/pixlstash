#!/usr/bin/env bash
# Build and run the desktop app from whichever checkout this script is in,
# against a local Python env. Meant for worktrees (e.g. Piecework's), which
# have no .venv, no node_modules and no built frontend of their own.
#
#   scripts/desktop-dev.sh build   install missing node deps, build frontend + electron
#   scripts/desktop-dev.sh run     launch Electron on this checkout's backend code
#   scripts/desktop-dev.sh         both
#
# `run` never opens the real data: it refreshes a scratch copy of the desktop
# app's hub and libraries (scripts/desktop_dev_scratch.py) and runs on that.
# `run --keep-data` reuses the last copy, to restart without losing a test's
# state.
#
# The interpreter is PIXLSTASH_DEV_BACKEND if set, otherwise the first .venv
# in this checkout, in the main checkout, or beside the main checkout (where a
# venv shared by several checkouts lives).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

find_venv_python() {
  local main dir
  main="$(cd "$ROOT" && cd "$(git rev-parse --git-common-dir)/.." && pwd)"
  for dir in "$ROOT" "$main" "$(dirname "$main")"; do
    if [[ -x "$dir/.venv/bin/python" ]]; then
      echo "$dir/.venv/bin/python"
      return
    fi
  done
  echo "desktop-dev: no .venv in $ROOT, $main or beside it; set PIXLSTASH_DEV_BACKEND" >&2
  return 1
}

build() {
  local dir
  for dir in frontend electron; do
    if [[ ! -d "$ROOT/$dir/node_modules" ]]; then
      (cd "$ROOT/$dir" && npm ci)
    fi
  done
  # Electron no longer fetches its binary on install; it ships a bin for it.
  if [[ ! -f "$ROOT/electron/node_modules/electron/path.txt" ]]; then
    (cd "$ROOT/electron" && npx --no-install install-electron)
  fi
  (cd "$ROOT/frontend" && npm run build)
  (cd "$ROOT/electron" && npm run check-electron && npm run build)
}

run() {
  local python scratch keep=""
  python="${PIXLSTASH_DEV_BACKEND:-$(find_venv_python)}"
  # PYTHONPATH makes this checkout's pixlstash win over an editable install
  # of another checkout in the shared venv.
  export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
  if [[ "${1:-}" == --keep-data ]]; then
    keep=--keep
    shift
  fi
  # A fresh copy of the real data every run: this branch's migrations upgrade
  # only the copy, so the installed app never meets a revision it cannot read.
  scratch="$("$python" "$ROOT/scripts/desktop_dev_scratch.py" ${keep:+"$keep"})"
  echo "desktop-dev: backend $python on $ROOT, data copied to $scratch"
  cd "$ROOT/electron"
  PIXLSTASH_DESKTOP_DEV=1 PIXLSTASH_DEV_BACKEND="$python" \
    PIXLSTASH_DESKTOP_USER_DATA="$scratch" \
    exec node scripts/dev-run.mjs "$@"
}

case "${1:-all}" in
  build) build ;;
  run) shift; run "$@" ;;
  all) build && run ;;
  *) echo "usage: $0 [build|run]" >&2; exit 64 ;;
esac
