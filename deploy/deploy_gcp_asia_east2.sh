#!/usr/bin/env bash
# One-command idempotent staging deployment to GCP Hong Kong (asia-east2):
#   Cloud SQL Postgres 15 (db-f1-micro) + GCS AES-256-GCM Vault + Cloud Build + Cloud Run + Vertex AI
#
# Usage:
#   GCP_PROJECT_ID=my-staging-project bash deploy/deploy_gcp_asia_east2.sh
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
REGION="${GCP_REGION:-asia-east2}"
SERVICE="${CLOUD_RUN_SERVICE:-clinic-ai-harness-staging}"
REPO="tmrw-harness"
SQL_INSTANCE="${CLOUDSQL_INSTANCE_NAME:-tmrw-acute-pg15}"
DB_NAME="clinic_harness"
DB_USER="clinic_app"
VAULT_BUCKET="${VAULT_GCS_BUCKET:-${PROJECT_ID}-tmrw-vault-hk}"
SA_NAME="tmrw-acute-runtime"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "==> Target: project=${PROJECT_ID} region=${REGION} service=${SERVICE}"
gcloud config set project "${PROJECT_ID}" --quiet

echo "==> 1. Enabling required GCP APIs (Hong Kong residency stack)"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  sqladmin.googleapis.com \
  storage.googleapis.com \
  aiplatform.googleapis.com \
  secretmanager.googleapis.com \
  --quiet

echo "==> 2. Ensuring Artifact Registry repository in ${REGION}"
gcloud artifacts repositories describe "${REPO}" --location="${REGION}" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "${REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Clinic AI Harness containers (HK)" --quiet

echo "==> 3. Ensuring GCS Encrypted Vault bucket in ${REGION} (no public access)"
if ! gcloud storage buckets describe "gs://${VAULT_BUCKET}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${VAULT_BUCKET}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --public-access-prevention --quiet
fi

echo "==> 4. Ensuring least-privilege runtime service account"
gcloud iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${SA_NAME}" --display-name="Clinic AI Harness Runtime" --quiet
for role in roles/cloudsql.client roles/storage.objectAdmin roles/aiplatform.user roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" --role="${role}" --quiet >/dev/null
done

echo "==> 5. Ensuring Cloud SQL PostgreSQL 15 instance in ${REGION}"
if ! gcloud sql instances describe "${SQL_INSTANCE}" >/dev/null 2>&1; then
  gcloud sql instances create "${SQL_INSTANCE}" \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region="${REGION}" \
    --storage-auto-increase \
    --backup-start-time=19:00 --quiet
fi

DB_PASS="${CLOUDSQL_APP_PASSWORD:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')}"
gcloud sql databases describe "${DB_NAME}" --instance="${SQL_INSTANCE}" >/dev/null 2>&1 || \
  gcloud sql databases create "${DB_NAME}" --instance="${SQL_INSTANCE}" --quiet
gcloud sql users list --instance="${SQL_INSTANCE}" --format="value(name)" | grep -qx "${DB_USER}" || \
  gcloud sql users create "${DB_USER}" --instance="${SQL_INSTANCE}" --password="${DB_PASS}" --quiet

CONN_NAME=$(gcloud sql instances describe "${SQL_INSTANCE}" --format="value(connectionName)")
JWT_SECRET="${JWT_SECRET:-$(python3 -c 'import secrets; print(secrets.token_hex(32))')}"
VAULT_KEK_B64="${VAULT_KEK_B64:-$(python3 -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode())')}"
VAULT_SIGNING_SECRET="${VAULT_SIGNING_SECRET:-$(python3 -c 'import secrets; print(secrets.token_hex(32))')}"
DATABASE_URL="postgresql+psycopg://${DB_USER}:${DB_PASS}@/${DB_NAME}?host=/cloudsql/${CONN_NAME}"

echo "==> 6. Building container remotely via Cloud Build & deploying to Cloud Run (${REGION})"
cd "$(dirname "$0")/.."
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE}:$(git rev-parse --short HEAD)"
CB_TMP="$(mktemp /tmp/cloudbuild-acute.XXXXXX.yaml)"
trap 'rm -f "${CB_TMP}"' EXIT
cat >"${CB_TMP}" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-f", "deploy/acute.Dockerfile", "-t", "${IMAGE}", "."]
images:
  - "${IMAGE}"
EOF
gcloud builds submit --region="${REGION}" --config="${CB_TMP}" . --quiet

gcloud run deploy "${SERVICE}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --platform=managed \
  --service-account="${SA_EMAIL}" \
  --allow-unauthenticated \
  --memory=1Gi \
  --cpu=1 \
  --add-cloudsql-instances="${CONN_NAME}" \
  --set-env-vars="HARNESS_ENV=staging,HARNESS_REGION=${REGION},GCP_PROJECT_ID=${PROJECT_ID},VERTEX_REGION=${REGION},VERTEX_MODEL=gemini-2.5-flash,VAULT_GCS_BUCKET=${VAULT_BUCKET},DEMO_MODE=true,DEMO_SHOW_OTP=true,DATABASE_URL=${DATABASE_URL},JWT_SECRET=${JWT_SECRET},VAULT_KEK_B64=${VAULT_KEK_B64},VAULT_SIGNING_SECRET=${VAULT_SIGNING_SECRET}" \
  --quiet

URL=$(gcloud run services describe "${SERVICE}" --region="${REGION}" --format="value(status.url)")
echo ""
echo "================================================================"
echo "  DEPLOYED TO HONG KONG (${REGION}): ${URL}"
echo "  Health check: ${URL}/api/v1/health"
echo "================================================================"
curl -fsS "${URL}/api/v1/health" && echo ""
