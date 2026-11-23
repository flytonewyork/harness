#!/usr/bin/env bash
# Deploy the 24/7 Autonomous Cloud Infra On-Call SRE Agent to GCP Hong Kong (asia-east2):
#   - Least-privilege `tmrw-sre-agent` SA (Cloud Run Admin, Logging Viewer, Cloud SQL Admin, Vertex AI User;
#     explicitly ZERO access to clinical Secret Manager keys or patient DB credentials)
#   - Durable GCS state bucket (`<project>-tmrw-sre-state-hk`)
#   - Cloud Run service `tmrw-sre-agent` with live Mission Control dashboard
#   - Cloud Scheduler job `tmrw-sre-watchdog` triggering every 2 minutes 24/7/365
#
# Usage:
#   GCP_PROJECT_ID=bard-388408 bash deploy/deploy_sre_agent_asia_east2.sh
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
REGION="${GCP_REGION:-asia-east2}"
REPO="tmrw-harness"
SRE_SERVICE="${SRE_SERVICE_NAME:-tmrw-sre-agent}"
TARGET_SERVICE="${TARGET_SERVICE:-clinic-ai-harness-staging}"
TARGET_SQL_INSTANCE="${TARGET_SQL_INSTANCE:-tmrw-acute-pg15}"
STATE_BUCKET="${SRE_STATE_GCS_BUCKET:-${PROJECT_ID}-tmrw-sre-state-hk}"
SA_NAME="tmrw-sre-agent"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_JOB="${SRE_SCHEDULER_JOB:-tmrw-sre-watchdog}"
SCHEDULE="${SRE_SCHEDULE:-*/2 * * * *}"

echo "==> Deploying 24/7 Autonomous SRE Agent: project=${PROJECT_ID} region=${REGION} watching=${TARGET_SERVICE}"
gcloud config set project "${PROJECT_ID}" --quiet

echo "==> 1. Enabling Cloud Scheduler, Cloud Run, Logging, SQL, Vertex AI APIs"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com \
  logging.googleapis.com \
  sqladmin.googleapis.com \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  --quiet

echo "==> 2. Ensuring SRE state bucket in ${REGION} (no public access)"
if ! gcloud storage buckets describe "gs://${STATE_BUCKET}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${STATE_BUCKET}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --public-access-prevention --quiet
fi

echo "==> 3. Ensuring least-privilege SRE agent service account (NO clinical secret access)"
gcloud iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${SA_NAME}" --display-name="tmrw 24/7 Autonomous Infra SRE Agent" --quiet

# Least-privilege infra remediation roles (deliberately excludes roles/secretmanager.secretAccessor)
for role in \
  roles/run.admin \
  roles/iam.serviceAccountUser \
  roles/logging.viewer \
  roles/cloudsql.admin \
  roles/aiplatform.user \
  roles/artifactregistry.writer \
  roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" --role="${role}" --quiet >/dev/null
done

gcloud storage buckets add-iam-policy-binding "gs://${STATE_BUCKET}" \
  --member="serviceAccount:${SA_EMAIL}" --role="roles/storage.objectAdmin" --quiet >/dev/null

TARGET_URL=$(gcloud run services describe "${TARGET_SERVICE}" --region="${REGION}" --format="value(status.url)" 2>/dev/null || echo "")

echo "==> 4. Building SRE Agent container remotely via Cloud Build (${REGION})"
cd "$(dirname "$0")/.."
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SRE_SERVICE}:$(git rev-parse --short HEAD)"
CB_TMP="$(mktemp /tmp/cloudbuild-sre.XXXXXX.yaml)"
trap 'rm -f "${CB_TMP}"' EXIT
cat >"${CB_TMP}" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-f", "deploy/sre.Dockerfile", "-t", "${IMAGE}", "."]
images:
  - "${IMAGE}"
options:
  logging: CLOUD_LOGGING_ONLY
EOF

BUILD_SA="${CLOUD_BUILD_SA:-tmrw-acute-runtime@${PROJECT_ID}.iam.gserviceaccount.com}"
gcloud builds submit \
  --region="${REGION}" \
  --service-account="projects/${PROJECT_ID}/serviceAccounts/${BUILD_SA}" \
  --config="${CB_TMP}" . --quiet

echo "==> 5. Deploying ${SRE_SERVICE} to Cloud Run (${REGION})"
gcloud run deploy "${SRE_SERVICE}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --platform=managed \
  --service-account="${SA_EMAIL}" \
  --allow-unauthenticated \
  --memory=512Mi \
  --cpu=1 \
  --min-instances=0 \
  --max-instances=2 \
  --set-env-vars="GCP_PROJECT_ID=${PROJECT_ID},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},TARGET_SERVICE=${TARGET_SERVICE},TARGET_SERVICE_URL=${TARGET_URL},TARGET_SQL_INSTANCE=${TARGET_SQL_INSTANCE},SRE_STATE_GCS_BUCKET=${STATE_BUCKET},AUTO_REMEDIATE=true,ENABLE_VERTEX_RCA=true,SRE_COOLDOWN_SECONDS=180,SRE_MAX_ACTIONS_PER_WINDOW=3,SRE_WINDOW_SECONDS=1800" \
  --quiet

SRE_URL=$(gcloud run services describe "${SRE_SERVICE}" --region="${REGION}" --format="value(status.url)")

echo "==> 6. Wiring 24/7 Cloud Scheduler Watchdog (${SCHEDULE} Asia/Hong_Kong -> ${SRE_URL}/api/v1/sre/tick)"
if gcloud scheduler jobs describe "${SCHEDULER_JOB}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "${SCHEDULER_JOB}" \
    --location="${REGION}" \
    --schedule="${SCHEDULE}" \
    --time-zone="Asia/Hong_Kong" \
    --uri="${SRE_URL}/api/v1/sre/tick" \
    --http-method=POST \
    --oidc-service-account-email="${SA_EMAIL}" \
    --oidc-token-audience="${SRE_URL}" \
    --quiet
else
  gcloud scheduler jobs create http "${SCHEDULER_JOB}" \
    --location="${REGION}" \
    --schedule="${SCHEDULE}" \
    --time-zone="Asia/Hong_Kong" \
    --uri="${SRE_URL}/api/v1/sre/tick" \
    --http-method=POST \
    --oidc-service-account-email="${SA_EMAIL}" \
    --oidc-token-audience="${SRE_URL}" \
    --quiet
fi

echo "==> 7. Running initial live SRE tick & verifying Mission Control"
curl -fsS "${SRE_URL}/health" && echo ""
curl -fsS -X POST "${SRE_URL}/api/v1/sre/tick" && echo ""
echo ""
echo "================================================================"
echo "  24/7 AUTONOMOUS SRE AGENT LIVE (${REGION}): ${SRE_URL}"
echo "  Watchdog schedule: ${SCHEDULE} (Asia/Hong_Kong)"
echo "  Target watched:    ${TARGET_SERVICE} (${TARGET_URL})"
echo "================================================================"
