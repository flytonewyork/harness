#!/bin/bash
# Run the WhatsApp care line on the Mac mini (spec 004).
#   scripts/careline_mac_mini.sh            # tester mode: real WhatsApp, only numbers in CARELINE_TESTERS
#   scripts/careline_mac_mini.sh sim        # simulator only, nothing leaves the machine
# Secrets live in ~/.tmrw-careline/env (chmod 600), never in git. Live mode is not offered here on purpose.
set -euo pipefail
MODE="${1:-tester}"
case "$MODE" in sim|tester) ;; *) echo "mode must be sim or tester (live needs the spec 003 sign-off)"; exit 1;; esac
HERE="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${CARELINE_DATA:-$HOME/.tmrw-careline}"
ENVF="$DATA/env"
mkdir -p "$DATA"; chmod 700 "$DATA"

if [ ! -f "$ENVF" ]; then
  cat > "$ENVF" <<'TEMPLATE'
# WhatsApp Cloud API (Meta app -> WhatsApp -> API setup). Keep this file private.
export WA_TOKEN=""               # permanent system-user token with whatsapp_business_messaging
export WA_PHONE_NUMBER_ID=""     # the business number's Phone number ID (not the number itself)
export WA_APP_SECRET=""          # Meta app -> Settings -> Basic -> App secret (checks webhook signatures)
export WA_VERIFY_TOKEN=""        # any long random string; paste the same into the webhook settings
export WA_DISPLAY_NUMBER=""      # the business number, digits only, e.g. 85291234567 (for the QR code)
export CARELINE_TESTERS=""       # comma-separated tester numbers, digits only, e.g. 85291111111,85292222222
export CARELINE_INTENT="rules"   # rules | claude-cli (uses this Mac's logged-in claude) | claude-api
export CARELINE_PORT="8787"
TEMPLATE
  chmod 600 "$ENVF"
  echo "Wrote $ENVF. Fill it in, then run this again."; exit 1
fi
# shellcheck disable=SC1090
source "$ENVF"
PORT="${CARELINE_PORT:-8787}"

cd "$HERE"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -e ".[careline]"

FEED="$HERE/../loopbench/results/product/hk_solo_apricot_vita_agentic-rules_s1.json"
if [ ! -f "$FEED" ]; then
  echo "Building the simulated clinic's feed (LoopBench, seed 1, ~10 s)…"
  (cd "$HERE/../loopbench" && PYTHONPATH=src python -m loopbench.cli product-feed --clinic hk_solo --system apricot_vita --harness agentic --seeds 1)
fi

if [ "$MODE" = tester ]; then
  for v in WA_TOKEN WA_PHONE_NUMBER_ID WA_APP_SECRET WA_VERIFY_TOKEN WA_DISPLAY_NUMBER CARELINE_TESTERS; do
    [ -n "${!v:-}" ] || { echo "$v is empty in $ENVF"; exit 1; }
  done
fi

echo
echo "Care line ($MODE) on http://127.0.0.1:$PORT/careline   join page: /join   webhook: /webhook/whatsapp"
echo "Point your tunnel at http://127.0.0.1:$PORT, and Meta's webhook Callback URL at https://<tunnel>/webhook/whatsapp"
echo "(verify token = WA_VERIFY_TOKEN; subscribe to 'messages'). Meta sends each app's messages to one URL only."
echo
exec python -m backend.careline --run "$FEED" --mode "$MODE" --port "$PORT" --data "$DATA" --intent "${CARELINE_INTENT:-rules}"
