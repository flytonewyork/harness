#!/usr/bin/env bash
# Zero-friction local dev auto-sync for Mac + phone LAN testing:
# Polls origin/feat/live-demo-convergence every 5s; on any new push,
# auto-pulls, rebuilds frontend/dist if frontend/ changed, and restarts Uvicorn.
#
# Usage (from harness repo root):
#   bash scripts/dev_autosync.sh
set -euo pipefail

BRANCH="${DEV_SYNC_BRANCH:-feat/live-demo-convergence}"
PORT="${PORT:-8080}"
UVICORN_PID=""

cleanup() {
  if [[ -n "${UVICORN_PID}" ]] && kill -0 "${UVICORN_PID}" 2>/dev/null; then
    kill "${UVICORN_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

find_uvicorn() {
  for cand in "${UVICORN_BIN:-}" ".venv/bin/uvicorn" "venv/bin/uvicorn" "../.venv/bin/uvicorn" "$HOME/.venv/bin/uvicorn"; do
    if [[ -n "${cand}" && -x "${cand}" ]]; then
      echo "${cand}"
      return 0
    fi
  done
  command -v uvicorn
}

start_server() {
  cleanup
  local UV_BIN
  UV_BIN="$(find_uvicorn)"
  echo "==> [dev_autosync] Starting ${UV_BIN} on 0.0.0.0:${PORT} (loads .env & gcloud ADC automatically)..."
  HARNESS_ENV=staging \
  HARNESS_RUNTIME_MODE=demo \
  DEMO_MODE=true \
  DEMO_SHOW_OTP=true \
  DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://postgres@127.0.0.1:5432/clinic_harness}" \
  DATABASE_ADMIN_URL="${DATABASE_ADMIN_URL:-postgresql+psycopg://postgres@127.0.0.1:5432/clinic_harness}" \
  "${UV_BIN}" backend.acute.app:app_from_env --factory --host 0.0.0.0 --port "${PORT}" --timeout-keep-alive 75 &
  UVICORN_PID=$!
}

# Initial build & start
git fetch origin "${BRANCH}" --quiet || true
npm --prefix frontend run build
start_server

LAST_SHA="$(git rev-parse HEAD)"
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo '127.0.0.1')"
echo ""
echo "========================================================================"
echo " LOCAL AUTO-SYNC LIVE (branch: ${BRANCH} @ ${LAST_SHA:0:7})"
echo "   Laptop Workspace (QR): http://${LAN_IP}:${PORT}/workspace"
echo "   Sim EMR:               http://${LAN_IP}:${PORT}/emr?mode=live"
echo "   Health & LLM warmup:   http://${LAN_IP}:${PORT}/api/v1/health"
echo "   Manual LLM re-warm:    curl -s -X POST http://127.0.0.1:${PORT}/api/v1/demo/live/warmup | jq ."
echo " Watching origin/${BRANCH} every 5s — zero manual rebuilds needed!"
echo "========================================================================"

while true; do
  sleep 5
  git fetch origin "${BRANCH}" --quiet || continue
  REMOTE_SHA="$(git rev-parse "origin/${BRANCH}" 2>/dev/null || echo "${LAST_SHA}")"
  if [[ "${REMOTE_SHA}" != "${LAST_SHA}" ]]; then
    echo ""
    echo "==> [dev_autosync] New commit detected (${LAST_SHA:0:7} -> ${REMOTE_SHA:0:7})! Syncing..."
    CHANGED_FRONTEND="$(git diff --name-only "${LAST_SHA}" "${REMOTE_SHA}" -- frontend/ || true)"
    git pull --ff-only origin "${BRANCH}"
    LAST_SHA="$(git rev-parse HEAD)"
    if [[ -n "${CHANGED_FRONTEND}" ]]; then
      echo "==> [dev_autosync] Frontend changed -> rebuilding dist (~2s)..."
      npm --prefix frontend run build
    fi
    start_server
    echo "==> [dev_autosync] Live at ${LAST_SHA:0:7}!"
  fi
done
