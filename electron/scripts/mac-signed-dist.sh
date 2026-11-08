#!/usr/bin/env bash
# Signed + notarised macOS build that survives transient faults AFTER notarisation.
#
# Run from electron/ by the "Build signed macOS Electron installer" step of
# .github/workflows/electron.yml, with the Apple signing and notarisation
# credentials in the environment. Extra arguments go to electron-builder.
#
# electron-builder signs, notarises and staples the .app in one go, then builds
# the zip and the DMG from it. Two runner/Apple-side faults have failed that
# whole chain after notarisation had already succeeded (issue #2634):
#   * the staple's ticket lookup losing its connection to Apple's CDN
#     (@electron/notarize retries it only for ~7 s);
#   * dmgbuild failing to detach its scratch image ("Resource busy").
# Re-running the job re-pays 30-60 min of notarisation. Instead, when the first
# build fails this script:
#   1. staples the .app electron-builder left behind, with a long backoff. A
#      staple only succeeds when Apple holds a notarisation ticket for that
#      app's code signature; an app that never got that far fails here, as the
#      build did before. The signature and Gatekeeper's verdict are then
#      checked explicitly before anything is built from the app;
#   2. rebuilds only the zip and DMG from that app with --prepackaged, which
#      skips packing, signing and notarisation, force-detaching any scratch
#      disk image a failed dmgbuild left attached between attempts.
# A failure that is not transient (bad signature, notarisation rejected) still
# fails the job, after the staple backoff (about five minutes).
set -uo pipefail

# Base delay of the backoff (doubles each retry), and the attempt counts.
# Overridable so the test suite can run the script without sleeping.
BASE_DELAY="${MAC_DIST_RETRY_BASE_DELAY:-20}"
STAPLE_ATTEMPTS="${MAC_DIST_STAPLE_ATTEMPTS:-5}"
TARGET_ATTEMPTS="${MAC_DIST_TARGET_ATTEMPTS:-3}"

# retry <attempts> <base delay> <command...>: exponential backoff.
retry() {
  local attempts="$1" delay="$2" n=1
  shift 2
  while true; do
    if "$@"; then
      return 0
    fi
    if [ "$n" -ge "$attempts" ]; then
      return 1
    fi
    echo "Attempt $n/$attempts of '$*' failed; retrying in ${delay}s." >&2
    sleep "$delay"
    n=$((n + 1))
    delay=$((delay * 2))
  done
}

# Whole-disk devices of attached images that live in a temp directory, which
# is where dmgbuild puts its scratch image. Images elsewhere (the runner's
# simulator runtimes, for one) are left alone.
scratch_image_devices() {
  hdiutil info 2>/dev/null | awk '
    /^image-path/ { sub(/^image-path[ \t]*:[ \t]*/, ""); path = $0; want = 1; next }
    want && /^\/dev\/disk[0-9]+[ \t]/ {
      if (path ~ /^(\/private)?\/var\/folders\// || path ~ /^(\/private)?\/tmp\//) print $1
      want = 0
    }'
}

# Detach whatever a failed dmgbuild left attached, and delete partial outputs.
clean_targets() {
  local device
  for device in $(scratch_image_devices); do
    echo "Force-detaching leftover scratch image: $device"
    hdiutil detach -force "$device" || true
  done
  rm -f release/*.dmg release/*.zip release/*.blockmap
}

build_targets_from_app() {
  clean_targets
  npx --no-install electron-builder --mac "$ARCH_FLAG" --publish never --prepackaged "$APP" "$@"
}

if npm run dist -- --publish never "$@"; then
  exit 0
fi
echo "::warning title=macOS build failed, attempting recovery::electron-builder failed. Checking whether the app was already notarised so the zip/DMG can be rebuilt without notarising again."

PRODUCT_NAME="$(node -p "require('./package.json').build.productName")" || exit 1
shopt -s nullglob
apps=(release/mac*/"$PRODUCT_NAME".app)
shopt -u nullglob
if [ "${#apps[@]}" -ne 1 ]; then
  echo "::error title=macOS build failed::No single packaged $PRODUCT_NAME.app under release/ (found ${#apps[@]}): the build failed before the app was packaged, so there is nothing to recover."
  exit 1
fi
APP="${apps[0]}"
# Rebuild for the arch the first build packaged, so artifact names match.
case "$(basename "$(dirname "$APP")")" in
  mac-arm64) ARCH_FLAG=--arm64 ;;
  mac-universal) ARCH_FLAG=--universal ;;
  mac) ARCH_FLAG=--x64 ;;
  *)
    echo "::error title=macOS build failed::Unrecognised app output directory for $APP."
    exit 1
    ;;
esac

if ! retry "$STAPLE_ATTEMPTS" "$BASE_DELAY" xcrun stapler staple -v "$APP"; then
  echo "::error title=macOS build failed::Could not staple $APP after $STAPLE_ATTEMPTS attempts: either it was never notarised (see the electron-builder error above) or Apple's ticket service stayed unreachable."
  exit 1
fi
if ! xcrun stapler validate "$APP"; then
  echo "::error title=macOS build failed::$APP was stapled but its ticket does not validate."
  exit 1
fi
if ! codesign --verify --deep --strict --verbose=2 "$APP"; then
  echo "::error title=macOS build failed::$APP is stapled but its code signature does not verify."
  exit 1
fi
if ! spctl --assess --type execute --verbose=2 "$APP"; then
  echo "::error title=macOS build failed::$APP is stapled but Gatekeeper rejects it."
  exit 1
fi

if ! retry "$TARGET_ATTEMPTS" "$BASE_DELAY" build_targets_from_app "$@"; then
  echo "::error title=macOS build failed::The app is notarised and stapled, but building the zip/DMG from it failed $TARGET_ATTEMPTS times."
  exit 1
fi
echo "::warning title=macOS build recovered::The first build failed after notarisation; the notarised app was stapled and the zip/DMG rebuilt from it without notarising again."
