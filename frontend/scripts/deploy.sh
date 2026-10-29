#!/usr/bin/env bash
# Publie le frontend de démo sur Amplify Hosting, branché sur l'API commune déployée.
# Amplify sans dépôt Git : on crée l'app (CloudFormation) puis on lui envoie un zip du site.
# À relancer après chaque redéploiement de l'API (son URL change si la stack est recréée).
# Usage : frontend/scripts/deploy.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../scripts/lib.sh"
require_aws

STACK="${PROJECT}-frontend"
FRONTEND="$REPO_ROOT/frontend"
BUILD="$FRONTEND/.build"

API="$(stack_output "${PROJECT}-backend" ApiUrl 2>/dev/null || true)"
[[ -n "$API" && "$API" != "None" ]] || die "Stack ${PROJECT}-backend introuvable : ./scripts/deploy.sh api"

prepare_stack "$STACK"
log "Stack ${STACK} (app Amplify)"
aws cloudformation deploy --stack-name "$STACK" \
  --template-file "$FRONTEND/infra/amplify.yaml" \
  --parameter-overrides ProjectName="$PROJECT" \
  --tags "${STACK_TAGS[@]}" \
  --no-fail-on-empty-changeset
APP_ID="$(stack_output "$STACK" AppId)"
BRANCH="$(stack_output "$STACK" BranchName)"

log "Build de l'application Vue"
(cd "$FRONTEND" && npm ci --no-audit --no-fund --loglevel=error && npm run build --silent)

log "Zip du site (API : ${API})"
rm -rf "$BUILD" && mkdir -p "$BUILD"
cp -r "$FRONTEND/dist" "$BUILD/site"
echo "window.APP_CONFIG = { apiUrl: \"$API\" };" > "$BUILD/site/config.js"
(cd "$BUILD/site" && python3 -m zipfile -c ../site.zip *)

log "Déploiement Amplify"
read -r JOB_ID UPLOAD_URL < <(aws amplify create-deployment --app-id "$APP_ID" --branch-name "$BRANCH" \
  --query '[jobId, zipUploadUrl]' --output text)
curl -sSf -X PUT -H "Content-Type: application/zip" --data-binary "@$BUILD/site.zip" "$UPLOAD_URL" >/dev/null
aws amplify start-deployment --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$JOB_ID" >/dev/null

while true; do
  state="$(aws amplify get-job --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$JOB_ID" \
    --query 'job.summary.status' --output text)"
  case "$state" in
    SUCCEED) break ;;
    FAILED|CANCELLED) die "Déploiement Amplify ${JOB_ID} : ${state}" ;;
    *) sleep 3 ;;
  esac
done
ok "Frontend : $(stack_output "$STACK" Url)"
