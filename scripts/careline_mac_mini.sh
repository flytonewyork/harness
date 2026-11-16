#!/bin/bash
# The WhatsApp care line on the Mac mini (spec 004). Tester mode: the real WhatsApp number, but only the numbers in
# CARELINE_TESTERS reach the agent; anyone else gets one fixed notice and nothing they write is kept.
#
#   scripts/careline_mac_mini.sh setup      # one time: asks for the Meta values, checks them, installs the service
#   scripts/careline_mac_mini.sh            # run in the foreground (tester mode)
#   scripts/careline_mac_mini.sh sim        # simulator only, nothing leaves the machine
#   scripts/careline_mac_mini.sh status     # is the service up, and what does Meta see
#   scripts/careline_mac_mini.sh stop       # stop and remove the background service
#   scripts/careline_mac_mini.sh logs       # follow the service log
#
# Secrets live in ~/.tmrw-careline/env (chmod 600), never in git. Live mode is not offered here on purpose.
set -euo pipefail
CMD="${1:-tester}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${CARELINE_DATA:-$HOME/.tmrw-careline}"
ENVF="$DATA/env"
LABEL="com.tmrw.careline"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
FEED="$HERE/../loopbench/results/product/hk_solo_apricot_vita_agentic-rules_s1.json"
GRAPH="https://graph.facebook.com/${WA_API_VERSION:-v21.0}"
mkdir -p "$DATA"; chmod 700 "$DATA"

load_env() { [ -f "$ENVF" ] && source "$ENVF" || true; }

pick_python() {  # the repo needs Python 3.12+; macOS's own python3 is older
  for p in python3.13 python3.12 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null && { echo "$p"; return; }
  done
  echo "Python 3.12 or newer is needed: brew install python@3.12" >&2; exit 1
}

prepare() {
  cd "$HERE"
  [ -x .venv/bin/python ] && .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 12))' || { rm -rf .venv; "$(pick_python)" -m venv .venv; }
  # shellcheck disable=SC1091
  source .venv/bin/activate
  if ! python -c 'import backend.careline.api, segno, httpx' 2>/dev/null; then
    pip install -q --upgrade pip >/dev/null
    pip install -q -e ".[careline]"
  fi
  if [ ! -f "$FEED" ]; then
    [ -d "$HERE/../loopbench" ] || git clone -q https://github.com/flytonewyork/loopbench "$HERE/../loopbench"
    echo "Building the simulated clinic (LoopBench seed 1, ~10 s)…"
    python -c 'import pandas, numpy, yaml, scipy' 2>/dev/null || pip install -q pandas numpy pyyaml scipy
    (cd "$HERE/../loopbench" && PYTHONPATH=src python -m loopbench.cli product-feed --clinic hk_solo --system apricot_vita --harness agentic --seeds 1)
  fi
}

ask() {  # ask VAR "prompt" [secret]
  local cur="${!1:-}" val
  if [ -n "${3:-}" ]; then
    read -r -s -p "$2${cur:+ [keep current]}: " val; echo
  else
    read -r -p "$2${cur:+ [$cur]}: " val
  fi
  printf -v "$1" '%s' "${val:-$cur}"
}

check_meta() {
  echo "Checking the token and phone number ID with Meta…"
  local out
  out="$(curl -sS "$GRAPH/$WA_PHONE_NUMBER_ID?fields=display_phone_number,verified_name,quality_rating" -H "Authorization: Bearer $WA_TOKEN")" || true
  if echo "$out" | grep -q display_phone_number; then
    echo "  OK: $out"
  else
    echo "  Meta said: $out"
    echo "  Fix WA_TOKEN or WA_PHONE_NUMBER_ID (run setup again)."; return 1
  fi
}

setup() {
  load_env
  echo "Values from developers.facebook.com → your app → WhatsApp → API setup, and App settings → Basic."
  ask WA_TOKEN "WhatsApp access token (permanent system-user token)" secret
  ask WA_PHONE_NUMBER_ID "Phone number ID (the long ID, not the phone number)"
  ask WA_APP_SECRET "App secret (App settings → Basic)" secret
  WA_VERIFY_TOKEN="${WA_VERIFY_TOKEN:-$(openssl rand -hex 16)}"
  ask WA_DISPLAY_NUMBER "The business number, digits only (e.g. 85291234567)"
  ask CARELINE_TESTERS "Tester numbers, digits only, comma-separated (your own phone first)"
  ask CARELINE_PORT "Local port (the port your tunnel forwards to)"
  CARELINE_PORT="${CARELINE_PORT:-8787}"
  ask CARELINE_INTENT "Intent model: rules, or claude-cli to use this Mac's logged-in claude"
  CARELINE_INTENT="${CARELINE_INTENT:-rules}"
  umask 077
  cat > "$ENVF" <<EOF
export WA_TOKEN='$WA_TOKEN'
export WA_PHONE_NUMBER_ID='$WA_PHONE_NUMBER_ID'
export WA_APP_SECRET='$WA_APP_SECRET'
export WA_VERIFY_TOKEN='$WA_VERIFY_TOKEN'
export WA_DISPLAY_NUMBER='${WA_DISPLAY_NUMBER//[^0-9]/}'
export CARELINE_TESTERS='${CARELINE_TESTERS// /}'
export CARELINE_PORT='$CARELINE_PORT'
export CARELINE_INTENT='$CARELINE_INTENT'
EOF
  chmod 600 "$ENVF"
  echo "Saved $ENVF"
  check_meta
  prepare
  install_service
  sleep 3
  status
  cat <<EOF

Last step, in Meta (developers.facebook.com → your app → WhatsApp → Configuration → Webhook → Edit):
  Callback URL:  https://<your tunnel's host>/webhook/whatsapp     (tunnel → http://127.0.0.1:$CARELINE_PORT)
  Verify token:  $WA_VERIFY_TOKEN
  Then "Verify and save", and subscribe the "messages" field.
  Meta sends an app's messages to one URL only, so this replaces your current webhook.

Then from a tester's phone: open http://127.0.0.1:$CARELINE_PORT/join on this Mac and scan the QR code,
or send "JOIN" to the business number. Join as a synthetic patient: CHAN Tai Man, 23/09/1964.
Dashboard: http://127.0.0.1:$CARELINE_PORT/careline
EOF
}

install_service() {
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$HERE/scripts/careline_mac_mini.sh</string><string>tester</string></array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DATA/careline.log</string>
  <key>StandardErrorPath</key><string>$DATA/careline.log</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
</dict></plist>
EOF
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "Service $LABEL installed: starts at login, restarts if it stops. Log: $DATA/careline.log"
}

status() {
  load_env
  local port="${CARELINE_PORT:-8787}"
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && echo "service: loaded" || echo "service: not installed"
  curl -sS "http://127.0.0.1:$port/api/careline/health" && echo || echo "server: not answering on $port (see $DATA/careline.log)"
  curl -sS "http://127.0.0.1:$port/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=${WA_VERIFY_TOKEN:-x}&hub.challenge=ok" | grep -qx ok \
    && echo "webhook verify: ok locally" || echo "webhook verify: failing locally"
}

run() {
  load_env
  local mode="$1"
  if [ "$mode" = tester ]; then
    for v in WA_TOKEN WA_PHONE_NUMBER_ID WA_APP_SECRET WA_VERIFY_TOKEN WA_DISPLAY_NUMBER CARELINE_TESTERS; do
      [ -n "${!v:-}" ] || { echo "$v is empty: run '$0 setup' first"; exit 1; }
    done
  fi
  prepare
  echo "Care line ($mode) on http://127.0.0.1:${CARELINE_PORT:-8787}/careline · /join · /webhook/whatsapp"
  exec python -m backend.careline --run "$FEED" --mode "$mode" --port "${CARELINE_PORT:-8787}" --data "$DATA" --intent "${CARELINE_INTENT:-rules}"
}

case "$CMD" in
  setup) setup ;;
  tester|sim) run "$CMD" ;;
  status) status ;;
  logs) tail -f "$DATA/careline.log" ;;
  stop) launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true; rm -f "$PLIST"; echo "stopped and removed" ;;
  *) echo "usage: $0 [setup|tester|sim|status|logs|stop]"; exit 1 ;;
esac
