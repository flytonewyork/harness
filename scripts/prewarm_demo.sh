#!/usr/bin/env bash
# Pre-warm GCP Cloud Run (asia-east2) or local Clinic AI Harness before a live demo.
# Uses ONLY existing frozen-main endpoints — zero new backend code required.
#
# Usage:
#   bash scripts/prewarm_demo.sh https://<your-cloud-run-url>
#   bash scripts/prewarm_demo.sh --ensure-warm-instance --reset https://<your-cloud-run-url>
set -euo pipefail

ENSURE_WARM_INSTANCE=false
RESET_DEMO=false
BASE_URL=""

for arg in "$@"; do
  case "${arg}" in
    --ensure-warm-instance) ENSURE_WARM_INSTANCE=true ;;
    --reset) RESET_DEMO=true ;;
    http://*|https://*) BASE_URL="${arg%/}" ;;
  esac
done

REGION="${GCP_REGION:-asia-east2}"
SERVICE="${CLOUD_RUN_SERVICE:-clinic-ai-harness-staging}"

if [ -z "${BASE_URL}" ]; then
  if command -v gcloud >/dev/null 2>&1 && [ -n "${GCP_PROJECT_ID:-}" ]; then
    BASE_URL=$(gcloud run services describe "${SERVICE}" --project="${GCP_PROJECT_ID}" --region="${REGION}" --format="value(status.url)")
  else
    BASE_URL="http://127.0.0.1:8000"
  fi
fi

echo "==> Pre-warming Clinic AI Harness at: ${BASE_URL}"

if [ "${ENSURE_WARM_INSTANCE}" = "true" ]; then
  if command -v gcloud >/dev/null 2>&1; then
    PROJECT_FLAG=""
    if [ -n "${GCP_PROJECT_ID:-}" ]; then
      PROJECT_FLAG="--project=${GCP_PROJECT_ID}"
    fi
    echo "==> Enforcing Cloud Run min-instances=1 + cpu-boost (${SERVICE} in ${REGION})..."
    gcloud run services update "${SERVICE}" ${PROJECT_FLAG} \
      --region="${REGION}" --min-instances=1 --cpu-boost --quiet
  else
    echo "WARN: gcloud not installed; skipping min-instances check."
  fi
fi

measure() {
  local label="$1"
  shift
  local t_ms
  t_ms=$(curl -sS -o /tmp/prewarm_body.json -w "%{time_total}" "$@")
  python3 -c "print(f'  {float(\"${t_ms}\")*1000:6.1f} ms  |  ${label}')"
}

echo ""
echo "==> 1. Readiness & Warm-Up Pass"
measure "GET  /api/v1/health/ready (1st pass)" -f "${BASE_URL}/api/v1/health/ready"
measure "GET  /api/v1/health/ready (2nd pass)" -f "${BASE_URL}/api/v1/health/ready"
measure "GET  /api/v1/health" -f "${BASE_URL}/api/v1/health"

if [ "${RESET_DEMO}" = "true" ]; then
  echo ""
  echo "==> 2. Resetting Synthetic Demo Data"
  measure "POST /api/v1/demo/reset" -f -X POST "${BASE_URL}/api/v1/demo/reset"
fi

echo ""
echo "==> 3. Warming Clinician Console (/c) Live Demo Path"
TOKEN=$(curl -fsS -X POST "${BASE_URL}/api/v1/demo/login-as" \
  -H "Content-Type: application/json" \
  -d '{"persona":"clinician:doc_dr_lam"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')

measure "GET  /api/v1/clinician/queue" -f -H "Authorization: Bearer ${TOKEN}" "${BASE_URL}/api/v1/clinician/queue"
measure "GET  /api/v1/clinician/proposals/prop_lam_gi_referral" -f -H "Authorization: Bearer ${TOKEN}" "${BASE_URL}/api/v1/clinician/proposals/prop_lam_gi_referral"
measure "POST /api/v1/clinician/rx-safety-check (DOAC+NSAID DDI)" -f -X POST \
  -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
  -d '{"patient_id":"pat_lam_gi_referral","diagnosis_icd10":"K30","items":[{"drug":"ibuprofen","dose_mg":400,"frequency":"TDS","days":5}]}' \
  "${BASE_URL}/api/v1/clinician/rx-safety-check"
measure "GET  /c (Frontend SPA bundle)" -f "${BASE_URL}/c"

echo ""
echo "==> READY FOR DEMO"
echo "    Clinician Console: ${BASE_URL}/c  (select LAM Wing-Keung in queue)"
echo "    After meeting, scale back to 0 to save cost:"
echo "      gcloud run services update ${SERVICE} --region=${REGION} --min-instances=0"
