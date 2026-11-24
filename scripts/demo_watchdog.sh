#!/usr/bin/env bash
# Simple, zero-dependency on-call watchdog for the Clinic AI Harness live demo.
#
# Monitors and auto-heals:
#   1. PostgreSQL container (harness-postgres)
#   2. FastAPI demo server (127.0.0.1:8080 in tmux session 'demo')
#   3. Cloudflare public tunnel (tmux session 'tunnel') — ONLY restarted if local
#      app is healthy AND the tunnel is genuinely down/failing, so the QR URL stays stable.
#   4. Log file sizes (caps at 15 MB to prevent disk fill).
#
# Usage:
#   bash scripts/demo_watchdog.sh            # Run continuous on-call loop (15s interval)
#   bash scripts/demo_watchdog.sh --once     # Run a single check + heal cycle
#   bash scripts/demo_watchdog.sh --status   # Print live JSON + summary status

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${DEMO_PORT:-8080}"
PG_CONTAINER="${PG_CONTAINER:-harness-postgres}"
INTERVAL="${WATCHDOG_INTERVAL:-15}"
APP_LOG="${APP_LOG:-/tmp/harness-demo.log}"
TUNNEL_LOG="${TUNNEL_LOG:-/tmp/cloudflared.log}"
WATCHDOG_LOG="${WATCHDOG_LOG:-/tmp/demo-watchdog.log}"
STATUS_FILE="${STATUS_FILE:-/tmp/demo-watchdog-status.json}"
URL_FILE="${URL_FILE:-/tmp/demo-public-url.txt}"
QR_OUT_PATH="${QR_OUT_PATH:-}"
CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-$HOME/bin/cloudflared}"
DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://postgres@127.0.0.1:5432/clinic_harness}"
DATABASE_ADMIN_URL="${DATABASE_ADMIN_URL:-$DATABASE_URL}"

APP_FAILS=0
TUNNEL_FAILS=0
PG_RESTARTS=0
APP_RESTARTS=0
TUNNEL_RESTARTS=0
LAST_HEAL="none"

log() {
  local line
  line="$(date -u '+%Y-%m-%dT%H:%M:%SZ') [watchdog] $*"
  echo "$line" | tee -a "$WATCHDOG_LOG" >/dev/null
}

trim_log() {
  local f="$1"
  local max_bytes=$((15 * 1024 * 1024))
  if [ -f "$f" ]; then
    local sz
    sz="$(wc -c < "$f" 2>/dev/null || echo 0)"
    if [ "$sz" -gt "$max_bytes" ]; then
      tail -n 2000 "$f" > "${f}.trim" && mv "${f}.trim" "$f"
      log "Trimmed oversized log $f (was ${sz} bytes)"
    fi
  fi
}

get_tunnel_url() {
  if [ -f "$TUNNEL_LOG" ]; then
    grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" | tail -n 1
  elif [ -f "$URL_FILE" ]; then
    cat "$URL_FILE"
  fi
}

check_pg() {
  docker exec "$PG_CONTAINER" pg_isready -U postgres -d clinic_harness >/dev/null 2>&1
}

heal_pg() {
  log "HEAL: PostgreSQL container '$PG_CONTAINER' unhealthy — starting container"
  docker update --restart unless-stopped "$PG_CONTAINER" >/dev/null 2>&1 || true
  docker start "$PG_CONTAINER" >/dev/null 2>&1 || true
  local i
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if check_pg; then
      PG_RESTARTS=$((PG_RESTARTS + 1))
      LAST_HEAL="$(date -u '+%Y-%m-%dT%H:%M:%SZ') restarted_postgres"
      log "HEAL OK: PostgreSQL ready after ${i}s"
      return 0
    fi
    sleep 1
  done
  log "ALERT: PostgreSQL failed to recover within 15s"
  return 1
}

check_app() {
  local code1 code2
  code1="$(curl -sS -m 4 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/api/v1/health" 2>/dev/null || echo 000)"
  [ "$code1" = "200" ] || return 1
  code2="$(curl -sS -m 4 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/api/v1/demo/live/journeys" 2>/dev/null || echo 000)"
  [ "$code2" = "200" ] || return 1
  return 0
}

heal_app() {
  log "HEAL: FastAPI demo server on :${PORT} unhealthy — restarting tmux session 'demo'"
  tmux kill-session -t demo 2>/dev/null || true
  pkill -f "[u]vicorn backend.acute.app:app_from_env" 2>/dev/null || true
  sleep 1
  tmux new-session -d -s demo -c "$REPO_DIR" \
    "HARNESS_ENV=staging HARNESS_RUNTIME_MODE=demo DEMO_MODE=true DEMO_SHOW_OTP=true DATABASE_URL='${DATABASE_URL}' DATABASE_ADMIN_URL='${DATABASE_ADMIN_URL}' uvicorn backend.acute.app:app_from_env --factory --host 0.0.0.0 --port ${PORT} --timeout-keep-alive 75 --proxy-headers --forwarded-allow-ips='*' 2>&1 | tee -a '${APP_LOG}'"
  local i
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if check_app; then
      APP_RESTARTS=$((APP_RESTARTS + 1))
      APP_FAILS=0
      LAST_HEAL="$(date -u '+%Y-%m-%dT%H:%M:%SZ') restarted_uvicorn"
      log "HEAL OK: FastAPI demo server recovered after ${i}s"
      return 0
    fi
    sleep 1
  done
  log "ALERT: FastAPI demo server failed to recover within 15s"
  return 1
}

regenerate_qr() {
  local url="$1"
  [ -n "$QR_OUT_PATH" ] || return 0
  if [ -d "${REPO_DIR}/frontend/node_modules/qrcode" ] && command -v node >/dev/null 2>&1; then
    (cd "${REPO_DIR}/frontend" && node -e "require('qrcode').toFile(process.argv[1], process.argv[2], {width:480,margin:2,color:{dark:'#2b2622',light:'#fbf8f1'}}, err => { if (err) process.exit(1); })" "$QR_OUT_PATH" "${url}/p/chat") 2>/dev/null || true
  fi
}

heal_tunnel() {
  if [ ! -x "$CLOUDFLARED_BIN" ]; then
    log "SKIP: cloudflared binary not found at $CLOUDFLARED_BIN"
    return 1
  fi
  log "HEAL: Cloudflare tunnel unhealthy — restarting tmux session 'tunnel'"
  tmux kill-session -t tunnel 2>/dev/null || true
  pkill -f "[c]loudflared tunnel" 2>/dev/null || true
  : > "$TUNNEL_LOG"
  sleep 1
  tmux new-session -d -s tunnel \
    "'${CLOUDFLARED_BIN}' tunnel --no-autoupdate --retries 10 --url http://127.0.0.1:${PORT} 2>&1 | tee -a '${TUNNEL_LOG}'"
  local i new_url="" registered="false"
  # Phase 1: Wait up to 20s for cloudflared to mint the URL & register the QUIC/H2 connection
  for i in 1 2 3 4 5 6 7 8 9 10; do
    new_url="$(get_tunnel_url)"
    if [ -n "$new_url" ] && grep -q "Registered tunnel connection" "$TUNNEL_LOG" 2>/dev/null; then
      registered="true"
      break
    fi
    sleep 2
  done
  if [ -z "$new_url" ]; then
    log "ALERT: Cloudflare tunnel did not emit a URL within 20s"
    return 1
  fi
  echo "$new_url" > "$URL_FILE"
  regenerate_qr "$new_url"
  TUNNEL_RESTARTS=$((TUNNEL_RESTARTS + 1))
  TUNNEL_FAILS=0
  LAST_HEAL="$(date -u '+%Y-%m-%dT%H:%M:%SZ') rotated_tunnel:${new_url}"
  # Phase 2: Let global DNS propagate (10s) before first curl so we don't poison negative DNS cache
  sleep 10
  for i in 1 2 3 4 5 6 7 8; do
    local pub_code
    pub_code="$(curl -sS -m 5 -o /dev/null -w '%{http_code}' "${new_url}/api/v1/health" 2>/dev/null || echo 000)"
    if [ "$pub_code" = "200" ]; then
      log "ALERT: Tunnel recovered with NEW URL -> ${new_url} (HTTP 200)"
      return 0
    fi
    sleep 5
  done
  log "WARN: Tunnel registered=${registered} (${new_url}); still waiting for DNS propagation"
  return 0
}

run_cycle() {
  local now pg_ok="false" app_ok="false" tunnel_ok="false" local_ms="0" pub_url="" pub_code="000"
  now="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

  # 1. PostgreSQL
  if check_pg; then
    pg_ok="true"
  else
    heal_pg && pg_ok="true"
  fi

  # 2. FastAPI app (debounced: 2 consecutive failures -> restart)
  local t_start t_end
  t_start="$(date +%s%3N)"
  if check_app; then
    t_end="$(date +%s%3N)"
    local_ms=$((t_end - t_start))
    app_ok="true"
    APP_FAILS=0
  else
    APP_FAILS=$((APP_FAILS + 1))
    log "WARN: App health check failed (${APP_FAILS}/2)"
    if [ "$APP_FAILS" -ge 2 ]; then
      heal_app && app_ok="true"
    fi
  fi

  # 3. Public tunnel (ONLY touch if local app is healthy!)
  pub_url="$(get_tunnel_url)"
  if [ -n "$pub_url" ]; then
    echo "$pub_url" > "$URL_FILE"
  fi
  if [ "$app_ok" = "true" ] && [ -x "$CLOUDFLARED_BIN" ]; then
    if pgrep -f "[c]loudflared tunnel" >/dev/null 2>&1 && [ -n "$pub_url" ]; then
      pub_code="$(curl -sS -m 5 -o /dev/null -w '%{http_code}' "${pub_url}/api/v1/health" 2>/dev/null || echo 000)"
      if [ "$pub_code" = "200" ]; then
        tunnel_ok="true"
        TUNNEL_FAILS=0
      else
        TUNNEL_FAILS=$((TUNNEL_FAILS + 1))
        log "WARN: Public tunnel probe returned HTTP ${pub_code} (${TUNNEL_FAILS}/3)"
      fi
    else
      TUNNEL_FAILS=$((TUNNEL_FAILS + 1))
      log "WARN: cloudflared process missing or URL absent (${TUNNEL_FAILS}/3)"
    fi

    if [ "$TUNNEL_FAILS" -ge 3 ]; then
      heal_tunnel && { tunnel_ok="true"; pub_url="$(get_tunnel_url)"; pub_code="200"; }
    fi
  fi

  # 4. Log size caps
  trim_log "$APP_LOG"
  trim_log "$TUNNEL_LOG"
  trim_log "$WATCHDOG_LOG"

  # 5. Write atomic status heartbeat
  local mem_avail disk_pct overall="healthy"
  mem_avail="$(free -m | awk '/^Mem:/ {print $7}')"
  disk_pct="$(df -P / | awk 'NR==2 {print $5}')"
  if [ "$pg_ok" != "true" ] || [ "$app_ok" != "true" ]; then
    overall="degraded"
  elif [ -x "$CLOUDFLARED_BIN" ] && [ "$tunnel_ok" != "true" ]; then
    overall="degraded"
  fi

  cat > "${STATUS_FILE}.tmp" <<EOF
{
  "checked_at": "${now}",
  "overall": "${overall}",
  "postgres_ok": ${pg_ok},
  "app_ok": ${app_ok},
  "app_latency_ms": ${local_ms},
  "tunnel_ok": ${tunnel_ok},
  "tunnel_http_code": "${pub_code}",
  "public_url": "${pub_url}",
  "workspace_url": "${pub_url:+${pub_url}/workspace}",
  "phone_chat_url": "${pub_url:+${pub_url}/p/chat}",
  "restarts": {
    "postgres": ${PG_RESTARTS},
    "app": ${APP_RESTARTS},
    "tunnel": ${TUNNEL_RESTARTS}
  },
  "last_heal": "${LAST_HEAL}",
  "mem_available_mb": ${mem_avail:-0},
  "disk_used_pct": "${disk_pct:-unknown}"
}
EOF
  mv "${STATUS_FILE}.tmp" "$STATUS_FILE"
}

case "${1:-}" in
  --status)
    if [ -f "$STATUS_FILE" ]; then
      cat "$STATUS_FILE"
    else
      run_cycle
      cat "$STATUS_FILE"
    fi
    exit 0
    ;;
  --once)
    run_cycle
    cat "$STATUS_FILE"
    exit 0
    ;;
esac

log "Started demo on-call watchdog (interval=${INTERVAL}s, port=${PORT}, pg=${PG_CONTAINER})"
while true; do
  run_cycle
  sleep "$INTERVAL"
done
