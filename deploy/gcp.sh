#!/usr/bin/env bash
# Déploie Liga IA sur Google Cloud (offre gratuite), depuis Cloud Shell :
#
#   git clone https://github.com/ZgolliS/liga-ia && cd liga-ia && bash deploy/gcp.sh
#
# Le script peut être relancé sans risque : il ne recrée que ce qui manque, puis
# reconstruit l'image et met à jour le site et le job.
#
#   Cloud Scheduler (15 min) ──► Cloud Run Job « liga-collecte » ──► bucket Cloud Storage
#                                                                          │
#   Visiteurs ──────────────────► Cloud Run Service « liga-ia » ◄──────────┘
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
# us-central1 : région où le stockage Cloud Storage est inclus dans l'offre gratuite.
REGION="${REGION:-us-central1}"
BUCKET="${BUCKET:-${PROJECT_ID}-liga-data}"
SERVICE="liga-ia"
JOB="liga-collecte"
SCHEDULE="${SCHEDULE:-*/15 * * * *}"   # toutes les 15 minutes
REPO="liga"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/liga-ia:latest"
SA_NAME="liga-ia"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

step() { printf '\n\033[1;32m▶ %s\033[0m\n' "$1"; }

if [[ -z "$PROJECT_ID" ]]; then
  echo "Aucun projet sélectionné. Lance : gcloud config set project TON_ID_DE_PROJET" >&2
  exit 1
fi
echo "Projet : $PROJECT_ID · Région : $REGION · Bucket : gs://$BUCKET"

step "1/8 Activation des services Google Cloud (1 à 2 min la première fois)"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com \
  cloudscheduler.googleapis.com storage.googleapis.com --project "$PROJECT_ID"

step "2/8 Dépôt d'images Docker (Artifact Registry)"
if ! gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPO" --repository-format docker \
    --location "$REGION" --description "Images de Liga IA"
fi
# On ne garde que l'image la plus récente : l'espace gratuit est limité (0,5 Go).
cat > /tmp/liga-cleanup.json <<'EOF'
[
  {"name": "garder-la-plus-recente", "action": {"type": "Keep"},
   "mostRecentVersions": {"keepCount": 1}},
  {"name": "supprimer-le-reste", "action": {"type": "Delete"},
   "condition": {"tagState": "any"}}
]
EOF
gcloud artifacts repositories set-cleanup-policies "$REPO" --location "$REGION" \
  --policy /tmp/liga-cleanup.json --no-dry-run >/dev/null

step "3/8 Bucket Cloud Storage (la base de données vit ici)"
if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --location "$REGION" \
    --uniform-bucket-level-access
fi

step "4/8 Clés API dans Secret Manager (saisies masquées, jamais affichées)"
for name in FOOTBALL_DATA_TOKEN LLM_API_KEY; do
  if gcloud secrets describe "$name" >/dev/null 2>&1; then
    echo "  $name : déjà enregistrée"
  else
    read -rsp "  Colle ta clé $name puis Entrée : " value; echo
    printf '%s' "$value" | gcloud secrets create "$name" --data-file=- \
      --replication-policy automatic >/dev/null
    unset value
  fi
done

step "5/8 Compte de service avec le minimum de droits"
if ! gcloud iam service-accounts describe "$SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --display-name "Liga IA"
  sleep 10  # le temps que le compte soit visible partout
fi
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "serviceAccount:$SA" --role roles/storage.objectAdmin >/dev/null
for name in FOOTBALL_DATA_TOKEN LLM_API_KEY; do
  gcloud secrets add-iam-policy-binding "$name" \
    --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor >/dev/null
done

step "6/8 Construction de l'image Docker par Cloud Build (5 à 10 min)"
gcloud builds submit --tag "$IMAGE" --region "$REGION" .

step "7/8 Job de collecte + premier remplissage de la base"
gcloud run jobs deploy "$JOB" --image "$IMAGE" --region "$REGION" \
  --service-account "$SA" --command python --args=-m,foot.job \
  --set-env-vars "STORAGE_URL=gs://$BUCKET" \
  --set-secrets "FOOTBALL_DATA_TOKEN=FOOTBALL_DATA_TOKEN:latest,LLM_API_KEY=LLM_API_KEY:latest" \
  --memory 1Gi --cpu 1 --task-timeout 30m --max-retries 0
if ! gcloud storage objects describe "gs://$BUCKET/foot.db" >/dev/null 2>&1; then
  echo "  Premier remplissage : l'IA analyse tous les articles (5 à 10 min)…"
  gcloud run jobs execute "$JOB" --region "$REGION" --wait
fi
# Le planificateur lance le job toutes les 15 minutes.
gcloud run jobs add-iam-policy-binding "$JOB" --region "$REGION" \
  --member "serviceAccount:$SA" --role roles/run.invoker >/dev/null
RUN_URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
if gcloud scheduler jobs describe "$JOB" --location "$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$JOB" --location "$REGION" --schedule "$SCHEDULE" \
    --uri "$RUN_URI" --http-method POST --oauth-service-account-email "$SA" >/dev/null
else
  gcloud scheduler jobs create http "$JOB" --location "$REGION" --schedule "$SCHEDULE" \
    --uri "$RUN_URI" --http-method POST --oauth-service-account-email "$SA" \
    --time-zone "Europe/Paris"
fi

step "8/8 Site web (s'éteint quand personne ne le visite : gratuit)"
gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" \
  --service-account "$SA" --allow-unauthenticated \
  --set-env-vars "STORAGE_URL=gs://$BUCKET" \
  --set-secrets "LLM_API_KEY=LLM_API_KEY:latest" \
  --memory 1Gi --cpu 1 --min-instances 0 --max-instances 2 --concurrency 20

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')"
printf '\n\033[1;32m✅ Site en ligne : %s\033[0m\n' "$URL"
echo "Pense à créer une alerte budget (Facturation > Budgets et alertes), par exemple 1 €."
