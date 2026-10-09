#!/usr/bin/env bash
# Local replay of the "Connect SimplySign" step in action.yml: one attempt,
# same Xvfb/fluxbox/config/clicks/sleeps. Differences from CI: the OTP is
# typed by the operator from the phone app, and a screenshot is written to
# /out at every step. Nothing leaves this machine. Keep the clicks in sync
# with action.yml. Usage (in a terminal, it prompts):
#   docker build -t certum-signer-local .github/docker/certum-signer
#   mkdir -p /tmp/ss-replay && cp .github/actions/certum-sign/local-replay.sh /tmp/ss-replay/
#   docker run --rm -it -v /tmp/ss-replay:/out certum-signer-local bash /out/local-replay.sh
set -uo pipefail
shot() { import -window root "/out/$1.png" && echo "  [shot] $1.png" || echo "  [shot FAILED] $1"; }

read -rp "Certum login email: " CERTUM_USER_ID

# --- Start headless display (as the action) ---
Xvfb :99 -screen 0 1280x1024x24 >/out/xvfb.log 2>&1 &
sleep 3
mkdir -p "$HOME/.fluxbox"
printf 'session.screen0.rootCommand: /bin/true\n' > "$HOME/.fluxbox/init"
DISPLAY=:99 fluxbox >/out/fluxbox.log 2>&1 &
sleep 2
export DISPLAY=:99
xdpyinfo >/dev/null && echo "X display :99 up"

# --- Launch SimplySign (as the action) ---
SS_DIST=/opt/SimplySignDesktop
SS_START="$(find "$SS_DIST/" -name 'SimplySignDesktop_start' -type f | head -1)"
SS_PKCS11="$(find "$SS_DIST/" -name 'SimplySignPKCS*.so' | head -1)"
export USER="${USER:-root}"
cp "$SS_DIST/SimplySignDesktop.xml" "$HOME/" 2>/dev/null || true
mkdir -p "$HOME/.config"
{ echo "[General]"; echo "CacheUserIdAtLogon=Yes"; echo "ShowLogonDialogAfterApplicationStartup=Yes"; echo "ShowLogonDialogWhenAnyAppRequestsAccess=Yes"; } > "$HOME/.config/Unknown Organization.conf"
export LD_LIBRARY_PATH="$SS_DIST:${LD_LIBRARY_PATH:-}"
"$SS_START" >/out/simplysign.log 2>&1 &
echo "launched SimplySign, waiting 20s"
sleep 20

find_ss_window() {
  best=0; WID=""; BX=0; BY=0; BW=0; BH=0
  for w in $(xdotool search --name '' 2>/dev/null); do
    nm="$(xdotool getwindowname "$w" 2>/dev/null || true)"
    case "$nm" in *implySign*) ;; *) continue ;; esac
    eval "$(xdotool getwindowgeometry --shell "$w" 2>/dev/null)"
    area=$(( ${WIDTH:-0} * ${HEIGHT:-0} ))
    if [ "$area" -gt "$best" ]; then best=$area; WID=$w; BX=${X:-0}; BY=${Y:-0}; BW=${WIDTH:-0}; BH=${HEIGHT:-0}; fi
  done
}

for i in $(seq 1 30); do
  find_ss_window
  [ "${BW:-0}" -ge 400 ] && [ "${BH:-0}" -ge 300 ] && break
  WID=""; sleep 2
done
[ -n "$WID" ] || { echo "login window did not appear"; shot no-window; exit 1; }
echo "login window $WID ${BW}x${BH} at ${BX},${BY}"
shot 1-launched

pkill -x xmessage && echo "closed fbsetbg xmessage" || true
xdotool windowactivate --sync "$WID" || xdotool windowactivate "$WID" || true
xdotool windowraise "$WID" || true
sleep 1
xdotool mousemove $((BX + BW/2)) $((BY + BH*45/100)) click 1; sleep 1
shot 2-clicked-email-field
xdotool type --clearmodifiers --delay 50 "$CERTUM_USER_ID"
xdotool mousemove $((BX + BW/2)) $((BY + BH*55/100)) click 1; sleep 1
shot 3-email-typed

read -rsp "Open the phone app, then enter the CURRENT code and press Enter: " OTP; echo
xdotool type --clearmodifiers --delay 50 "$OTP"
unset OTP
shot 4-filled
xdotool key Return
sleep 8
shot 5-after-login

find_ss_window
for w in $(xdotool search --name '' 2>/dev/null); do nm="$(xdotool getwindowname "$w" 2>/dev/null)"; [ -n "$nm" ] && echo "  window $w '$nm' $(xdotool getwindowgeometry "$w" | tail -1)"; done
if [ -n "$WID" ]; then
  echo "post-login window $WID ${BW}x${BH} at ${BX},${BY}"
  xdotool windowactivate --sync "$WID" || true
  xdotool mousemove $((BX + BW/2)) $((BY + BH*94/100)) click 1
  sleep 3
fi
shot 6-after-close

if timeout 60 pkcs11-tool --module "$SS_PKCS11" -L 2>/dev/null | grep -q "token label"; then
  echo "RESULT: PKCS#11 token active (automated login WORKS)"
else
  echo "RESULT: token NOT active (automated login fails here too)"
fi
pkill -f SimplySignDesktop; pkill Xvfb
