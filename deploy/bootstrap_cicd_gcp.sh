#!/usr/bin/env bash
# One-time 60-second bootstrap to:
#   1. Provision Cloud SQL PG15 + GCS Vault + Secret Manager + Vertex AI IAM + Cloud Run (asia-east2)
#   2. Create a least-privilege GitHub Actions deployer Service Account
#   3. Set GCP_PROJECT_ID and GCP_SA_KEY secrets on flytonewyork/harness via `gh secret set`
#
# After running this ONCE, every `git push` to `feat/live-demo-convergence` or `main`
# automatically builds & deploys frontend + backend + Vertex AI Gemini 4 in ~90s.
#
# Usage:
#   GCP_PROJECT_ID=<your-gcp-project-id> bash deploy/bootstrap_cicd_gcp.sh
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID (e.g. GCP_PROJECT_ID=my-gcp-project bash deploy/bootstrap_cicd_gcp.sh)}"
REGION="${GCP_REGION:-asia-east2}"
REPO_SLUG="${GITHUB_REPO:-flytonewyork/harness}"
DEPLOYER_SA="github-cloudrun-deployer"
DEPLOYER_EMAIL="${DEPLOYER_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
KEY_FILE="$(mktemp /tmp/gh-deployer-key.XXXXXX.json)"
trap 'rm -f "${KEY_FILE}"' EXIT

echo "==> Step 1/3: Provisioning GCP asia-east2 staging + Vertex AI IAM + initial Cloud Run deploy"
GCP_PROJECT_ID="${PROJECT_ID}" GCP_REGION="${REGION}" bash deploy/deploy_gcp_asia_east2.sh

echo "==> Step 2/3: Creating least-privilege GitHub Actions deployer service account (${DEPLOYER_EMAIL})"
gcloud iam service-accounts describe "${DEPLOYER_EMAIL}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${DEPLOYER_SA}" \
    --project="${PROJECT_ID}" \
    --display-name="GitHub Actions Cloud Run Staging Deployer"

for ROLE in \
  roles/cloudbuild.builds.editor \
  roles/run.admin \
  roles/iam.serviceAccountUser \
  roles/artifactregistry.writer \
  roles/storage.admin \
  roles/logging.viewer
do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${DEPLOYER_EMAIL}" \
    --role="${ROLE}" \
    --quiet >/dev/null
done

gcloud iam service-accounts keys create "${KEY_FILE}" \
  --iam-account="${DEPLOYER_EMAIL}" \
  --project="${PROJECT_ID}" \
  --quiet

echo "==> Step 3/3: Wiring GitHub Actions secrets on ${REPO_SLUG}"
gh secret set GCP_PROJECT_ID -R "${REPO_SLUG}" --body "${PROJECT_ID}"
gh secret set GCP_SA_KEY -R "${REPO_SLUG}" < "${KEY_FILE}"

SERVICE_URL="$(gcloud run services describe clinic-ai-harness-staging --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"
echo ""
echo "========================================================================"
echo " CI/CD LIVE! Every push to feat/live-demo-convergence auto-deploys here:"
echo "   Workspace (QR): ${SERVICE_URL}/workspace"
echo "   Phone Chat:     ${SERVICE_URL}/p/chat"
echo "   Sim EMR:        ${SERVICE_URL}/emr?mode=live"
echo "   Health:         ${SERVICE_URL}/api/v1/health"
echo "========================================================================"
