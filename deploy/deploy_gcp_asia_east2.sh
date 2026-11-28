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
DB_APP_USER="clinic_app"
DB_ADMIN_USER="${CLOUDSQL_ADMIN_USER:-${DB_APP_USER}}"
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
  cloudkms.googleapis.com \
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
for role in roles/cloudsql.client roles/storage.objectAdmin roles/aiplatform.user roles/secretmanager.secretAccessor roles/artifactregistry.writer roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" --role="${role}" --quiet >/dev/null
done

ensure_secret() {
  local name="$1"
  local gen_cmd="$2"
  if ! gcloud secrets describe "${name}" >/dev/null 2>&1; then
    gcloud secrets create "${name}" --replication-policy="user-managed" --locations="${REGION}" --quiet
    eval "${gen_cmd}" | gcloud secrets versions add "${name}" --data-file=- --quiet
  fi
}

upsert_secret_value() {
  local name="$1"
  local value="$2"
  if ! gcloud secrets describe "${name}" >/dev/null 2>&1; then
    gcloud secrets create "${name}" --replication-policy="user-managed" --locations="${REGION}" --quiet
  fi
  printf '%s' "${value}" | gcloud secrets versions add "${name}" --data-file=- --quiet
}

echo "==> 5. Ensuring persistent secrets in GCP Secret Manager (${REGION})"
ensure_secret "clinic-harness-db-admin-pass" "python3 -c 'import secrets; print(secrets.token_urlsafe(24), end=\"\")'"
ensure_secret "clinic-harness-db-pass" "python3 -c 'import secrets; print(secrets.token_urlsafe(24), end=\"\")'"
ensure_secret "clinic-harness-jwt-secret" "python3 -c 'import secrets; print(secrets.token_hex(32), end=\"\")'"
ensure_secret "clinic-harness-vault-kek" "python3 -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode(), end=\"\")'"
ensure_secret "clinic-harness-vault-signing" "python3 -c 'import secrets; print(secrets.token_hex(32), end=\"\")'"

DB_APP_PASS="${CLOUDSQL_APP_PASSWORD:-$(gcloud secrets versions access latest --secret=clinic-harness-db-pass)}"
if [ "${DB_ADMIN_USER}" = "${DB_APP_USER}" ]; then
  DB_ADMIN_PASS="${DB_APP_PASS}"
else
  DB_ADMIN_PASS="${CLOUDSQL_ADMIN_PASSWORD:-$(gcloud secrets versions access latest --secret=clinic-harness-db-admin-pass)}"
fi

echo "==> 6. Ensuring Cloud SQL PostgreSQL 15 instance in ${REGION}"
if ! gcloud sql instances describe "${SQL_INSTANCE}" >/dev/null 2>&1; then
  gcloud sql instances create "${SQL_INSTANCE}" \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region="${REGION}" \
    --storage-auto-increase \
    --backup-start-time=19:00 --quiet
fi

gcloud sql databases describe "${DB_NAME}" --instance="${SQL_INSTANCE}" >/dev/null 2>&1 || \
  gcloud sql databases create "${DB_NAME}" --instance="${SQL_INSTANCE}" --quiet

ensure_sql_user() {
  local u="$1"
  local p="$2"
  if gcloud sql users list --instance="${SQL_INSTANCE}" --format="value(name)" | grep -qx "${u}"; then
    gcloud sql users set-password "${u}" --instance="${SQL_INSTANCE}" --password="${p}" --quiet
  else
    gcloud sql users create "${u}" --instance="${SQL_INSTANCE}" --password="${p}" --quiet
  fi
}

ensure_sql_user "${DB_APP_USER}" "${DB_APP_PASS}"
if [ "${DB_ADMIN_USER}" != "${DB_APP_USER}" ]; then
  ensure_sql_user "${DB_ADMIN_USER}" "${DB_ADMIN_PASS}"
fi

CONN_NAME=$(gcloud sql instances describe "${SQL_INSTANCE}" --format="value(connectionName)")
DATABASE_ADMIN_URL="postgresql+psycopg://${DB_ADMIN_USER}:${DB_ADMIN_PASS}@/${DB_NAME}?host=/cloudsql/${CONN_NAME}"
DATABASE_APP_URL="postgresql+psycopg://${DB_APP_USER}:${DB_APP_PASS}@/${DB_NAME}?host=/cloudsql/${CONN_NAME}"

# Store DB connection strings in Secret Manager so credentials never appear in plaintext Cloud Run env vars
upsert_secret_value "clinic-harness-db-admin-url" "${DATABASE_ADMIN_URL}"
upsert_secret_value "clinic-harness-db-url" "${DATABASE_APP_URL}"

echo "==> 7. Building container remotely via Cloud Build & deploying to Cloud Run (${REGION})"
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
options:
  logging: CLOUD_LOGGING_ONLY
EOF
gcloud builds submit \
  --region="${REGION}" \
  --service-account="projects/${PROJECT_ID}/serviceAccounts/${SA_EMAIL}" \
  --config="${CB_TMP}" . --quiet

gcloud run deploy "${SERVICE}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --platform=managed \
  --service-account="${SA_EMAIL}" \
  --allow-unauthenticated \
  --memory=1Gi \
  --cpu=1 \
  --min-instances=1 \
  --max-instances=1 \
  --timeout=3600 \
  --cpu-boost \
  --add-cloudsql-instances="${CONN_NAME}" \
  --set-env-vars="HARNESS_ENV=staging,HARNESS_REGION=${REGION},GCP_PROJECT_ID=${PROJECT_ID},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},VERTEX_REGION=${REGION},VERTEX_MODEL=gemini-4-neon,VAULT_GCS_BUCKET=${VAULT_BUCKET},DEMO_MODE=true,DEMO_SHOW_OTP=true,OUTBOX_WORKER_ENABLED=true" \
  --set-secrets="DATABASE_URL=clinic-harness-db-url:latest,DATABASE_ADMIN_URL=clinic-harness-db-admin-url:latest,JWT_SECRET=clinic-harness-jwt-secret:latest,VAULT_KEK_B64=clinic-harness-vault-kek:latest,VAULT_SIGNING_SECRET=clinic-harness-vault-signing:latest" \
  --quiet

URL=$(gcloud run services describe "${SERVICE}" --region="${REGION}" --format="value(status.url)")
echo ""
echo "================================================================"
echo "  DEPLOYED TO HONG KONG (${REGION}): ${URL}"
echo "  Health check: ${URL}/api/v1/health"
echo "================================================================"
curl -fsS "${URL}/api/v1/health" && echo ""
