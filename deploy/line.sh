#!/usr/bin/env bash
# The doctor's own line (spec 005) on Google Cloud, Hong Kong region. Run it in Google Cloud Shell
# (console.cloud.google.com → the >_ button), from a clone of this repo:
#
#   git clone https://github.com/flytonewyork/harness && cd harness
#   deploy/line.sh setup staging      # once per environment: APIs, Firestore, bucket, service account, secrets
#   deploy/line.sh deploy staging     # build and deploy (again after every merge you want live)
#   deploy/line.sh scheduler staging  # once: the every-minute tick for check-ins, reminders and nudges
#   deploy/line.sh doctor staging     # add or update a doctor (asks for the details)
#   deploy/line.sh urls staging       # the webhook URLs to paste into Meta and Stripe
#   deploy/line.sh secret staging STRIPE_WEBHOOK_SECRET   # set one secret
#
# Environments: staging (Meta test number, Stripe test keys, role-play patients only, Firestore prefix stg_) and
# live (the real number, live keys, real patients, prefix line_; refuses to start without the doctor's sign-off).
# Secrets go into Secret Manager by prompt, never into this repo or the shell history.
set -euo pipefail
CMD="${1:-help}"; ENVN="${2:-staging}"
REGION=asia-east2
PROJECT="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
[ -n "$PROJECT" ] || { echo "Set a project first: gcloud config set project <your-project-id>"; exit 1; }
case "$ENVN" in staging) SVC=care-line-staging; PREFIX=stg_; SUF=-staging ;; live) SVC=care-line; PREFIX=line_; SUF= ;; *) echo "staging or live"; exit 1 ;; esac
SA="care-line@${PROJECT}.iam.gserviceaccount.com"
BUCKET="gs://${PROJECT}-care-line-media${SUF}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/tmrw/line:latest"
SECRETS=(WA_TOKEN WA_APP_SECRET WA_VERIFY_TOKEN STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET LINE_CRON_SECRET LINE_SESSION_SECRET LINE_ADMIN_TOKEN)

secret_set() {  # name: asks for the value, or generates one for the line's own secrets
  local name="$1${SUF//-/_}" v
  name="${name^^}"
  if [[ "$1" =~ ^(LINE_CRON_SECRET|LINE_SESSION_SECRET|LINE_ADMIN_TOKEN|WA_VERIFY_TOKEN)$ ]]; then
    if gcloud secrets describe "$name" >/dev/null 2>&1; then echo "  $name: kept"; return 0; fi
    v="$(openssl rand -hex 24)"; echo "  $name: generated"
  else
    read -rsp "  $name (paste, hidden; Enter to skip): " v; echo
  fi
  [ -n "$v" ] || return 0
  gcloud secrets describe "$name" >/dev/null 2>&1 || gcloud secrets create "$name" --replication-policy=user-managed --locations="$REGION" >/dev/null
  printf %s "$v" | gcloud secrets versions add "$name" --data-file=- >/dev/null
  gcloud secrets add-iam-policy-binding "$name" --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor >/dev/null
}

case "$CMD" in
setup)
  echo "Project $PROJECT, region $REGION, environment $ENVN"
  gcloud services enable run.googleapis.com firestore.googleapis.com aiplatform.googleapis.com secretmanager.googleapis.com \
    cloudscheduler.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com storage.googleapis.com
  gcloud firestore databases describe --database='(default)' >/dev/null 2>&1 || gcloud firestore databases create --location="$REGION" --type=firestore-native
  gcloud artifacts repositories describe tmrw --location="$REGION" >/dev/null 2>&1 || gcloud artifacts repositories create tmrw --repository-format=docker --location="$REGION"
  gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || gcloud iam service-accounts create care-line --display-name="tmrw care line"
  for role in roles/datastore.user roles/aiplatform.user roles/logging.logWriter; do
    gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" --role="$role" --condition=None >/dev/null
  done
  gcloud storage buckets describe "$BUCKET" >/dev/null 2>&1 || gcloud storage buckets create "$BUCKET" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
  printf '{"rule":[{"action":{"type":"Delete"},"condition":{"age":30}}]}' > /tmp/lifecycle.json
  gcloud storage buckets update "$BUCKET" --lifecycle-file=/tmp/lifecycle.json
  gcloud storage buckets add-iam-policy-binding "$BUCKET" --member="serviceAccount:$SA" --role=roles/storage.objectAdmin >/dev/null
  echo "Secrets for $ENVN (WhatsApp from Meta's app dashboard; Stripe from the Stripe dashboard, test keys for staging):"
  for s in "${SECRETS[@]}"; do secret_set "$s"; done
  echo "Done. Next: deploy/line.sh deploy $ENVN"
  ;;
deploy)
  read -rp "The shared WhatsApp number for $ENVN, digits only (e.g. 85291234567): " WA_NUMBER
  read -rp "Its phone number ID (Meta → WhatsApp → API setup): " WA_PNID
  read -rp "Comma-separated tester numbers (staging only; Enter for none): " TESTERS
  read -rp "Gemini model offered in $REGION (Vertex AI → Model Garden; Enter for gemini-2.5-flash): " VMODEL
  VMODEL="${VMODEL:-gemini-2.5-flash}"
  MODE="$ENVN"; SIGN=""
  if [ "$ENVN" = live ]; then
    read -rp "Live mode sends to real patients. As the doctor, do you sign off (type yes)? " ok
    [ "$ok" = yes ] || { echo "Not deployed."; exit 1; }
    SIGN="@CARELINE_I_HAVE_SIGN_OFF=yes"
  fi
  gcloud builds submit --config deploy/cloudbuild.line.yaml .
  sec=""; for s in "${SECRETS[@]}"; do n="${s}${SUF//-/_}"; n="${n^^}"; gcloud secrets describe "$n" >/dev/null 2>&1 && sec+="${s}=${n}:latest,"; done
  URL="$(gcloud run services describe "$SVC" --region="$REGION" --format='value(status.url)' 2>/dev/null || true)"
  PAY=off; [[ "$sec" == *STRIPE_SECRET_KEY=* && "$sec" == *STRIPE_WEBHOOK_SECRET=* ]] && PAY=on
  [ "$PAY" = on ] || echo "Payments are OFF until both Stripe secrets are set (deploy/line.sh secret $ENVN STRIPE_SECRET_KEY, then STRIPE_WEBHOOK_SECRET)."
  # '@' separates the settings, because the tester list has commas in it
  gcloud run deploy "$SVC" --image="$IMAGE" --region="$REGION" --service-account="$SA" --allow-unauthenticated \
    --min-instances=1 --max-instances=1 --no-cpu-throttling --memory=1Gi --timeout=120 \
    --set-env-vars="^@^LINE_MODE=$MODE@LINE_STORE=firestore@LINE_STORE_PREFIX=$PREFIX@LINE_MODEL=vertex@VERTEX_PROJECT=$PROJECT@VERTEX_REGION=$REGION@VERTEX_MODEL=$VMODEL@LINE_MEDIA_BUCKET=$BUCKET@LINE_PAYMENTS=$PAY@WA_NUMBER=$WA_NUMBER@WA_PHONE_NUMBER_ID=$WA_PNID@LINE_TESTERS=${TESTERS// /}@LINE_BASE_URL=${URL:-https://placeholder}${SIGN}" \
    --set-secrets="${sec%,}"
  URL="$(gcloud run services describe "$SVC" --region="$REGION" --format='value(status.url)')"
  gcloud run services update "$SVC" --region="$REGION" --update-env-vars="LINE_BASE_URL=$URL" >/dev/null
  echo "Deployed: $URL  (health: $URL/healthz)"
  ;;
scheduler)
  URL="$(gcloud run services describe "$SVC" --region="$REGION" --format='value(status.url)')"
  n="LINE_CRON_SECRET${SUF//-/_}"; n="${n^^}"
  CRON="$(gcloud secrets versions access latest --secret="$n")"
  gcloud scheduler jobs delete "$SVC-tick" --location="$REGION" --quiet >/dev/null 2>&1 || true
  gcloud scheduler jobs create http "$SVC-tick" --location="$REGION" --schedule="* * * * *" --time-zone="Asia/Hong_Kong" \
    --uri="$URL/cron/tick" --http-method=POST --headers="X-Cron-Secret=$CRON"
  ;;
doctor)
  URL="$(gcloud run services describe "$SVC" --region="$REGION" --format='value(status.url)')"
  n="LINE_ADMIN_TOKEN${SUF//-/_}"; n="${n^^}"; TOKEN="$(gcloud secrets versions access latest --secret="$n")"
  read -rp "Id (e.g. D1): " ID; read -rp "QR code (2 letters + 2 digits, e.g. TH01): " CODE
  read -rp "Name in English (e.g. Dr Thomas Hu): " NE; read -rp "Name in Chinese (e.g. 胡醫生): " NZ
  read -rp "Short name (e.g. Dr Hu): " SE; read -rp "Doctor's own WhatsApp number, digits only: " WA
  echo "Clinics, as name|中文名|address|地址|phone. Separate several clinics with ';'. Add |nocharge to a clinic where patients are never charged."
  read -rp "Clinics: " CL
  read -rp "Repeat fee HK$: " RF; read -rp "Result review fee HK$: " XF
  ID="$ID" CODE="$CODE" NE="$NE" NZ="$NZ" SE="$SE" WA="$WA" CL="$CL" RF="${RF:-0}" XF="${XF:-0}" python3 - "$URL" "$TOKEN" <<'PY'
import json, os, sys, urllib.request
e = os.environ
clinics = []
for i, c in enumerate([x for x in e["CL"].split(";") if x.strip()], 1):
    f = ([y.strip() for y in c.split("|")] + [""] * 6)[:6]
    clinics.append({"id": f"c{i}", "name_en": f[0], "name_zh": f[1], "address_en": f[2], "address_zh": f[3], "phone": f[4],
                    "charge": f[5].lower() != "nocharge"})
body = {"id": e["ID"], "code": e["CODE"].upper(), "name_en": e["NE"], "name_zh": e["NZ"], "short_en": e["SE"] or e["NE"], "short_zh": e["NZ"],
        "wa": e["WA"].lstrip("+").replace(" ", ""), "fees": {"repeat": int(e["RF"]), "result": int(e["XF"])}, "clinics": clinics}
req = urllib.request.Request(sys.argv[1] + "/api/admin/doctor", data=json.dumps(body).encode(), method="POST",
                             headers={"Authorization": "Bearer " + sys.argv[2], "Content-Type": "application/json"})
try:
    print(urllib.request.urlopen(req).read().decode())
except urllib.error.HTTPError as err:
    print("Not saved:", err.read().decode())
PY
  ;;
secret)
  secret_set "${3:?which secret, e.g. STRIPE_WEBHOOK_SECRET}"
  echo "Saved. Run deploy/line.sh deploy $ENVN so the service picks it up."
  ;;
urls)
  URL="$(gcloud run services describe "$SVC" --region="$REGION" --format='value(status.url)')"
  n="WA_VERIFY_TOKEN${SUF//-/_}"; n="${n^^}"
  echo "Meta webhook (WhatsApp → Configuration): $URL/webhook/whatsapp   verify token: $(gcloud secrets versions access latest --secret="$n")"
  echo "  subscribe to: messages"
  echo "Stripe webhook (Developers → Webhooks): $URL/webhook/stripe   events: checkout.session.completed, account.updated, customer.subscription.deleted"
  echo "  then: deploy/line.sh secret $ENVN STRIPE_WEBHOOK_SECRET, and deploy again"
  ;;
*) sed -n 2,15p "$0" ;;
esac
