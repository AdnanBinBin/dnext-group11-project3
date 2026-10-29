#!/usr/bin/env bash
# =============================================================================
#  Déploie la plateforme complète, ou une partie, dans l'ordre des dépendances :
#
#    data     Bronze -> Silver : buckets, Lambda d'ingestion, Glue, Athena (+ chargement de l'Excel)
#    gold     bucket Gold + table mirror_matches calculée par Athena (une seule fois)
#    api      Lambda de consultation + API HTTP commune (/api/v1/...)
#    reports  rapports IA : Step Functions, Lambdas, Bedrock, DynamoDB, S3 (routes /api/v1/reports)
#    frontend heatmap de démo hébergée sur Amplify, branchée sur l'URL de l'API
#
#  Usage :  ./scripts/deploy.sh [all | data gold api reports frontend ...]
#  Chaque étape est idempotente : relancer après une purge du compte recrée ce qui manque.
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TARGETS=("$@")
[[ ${#TARGETS[@]} -eq 0 || "${TARGETS[0]}" == "all" ]] && TARGETS=("${COMPONENTS[@]}")
for t in "${TARGETS[@]}"; do
  [[ " ${COMPONENTS[*]} " == *" $t "* ]] || die "Composant inconnu : $t (attendu : ${COMPONENTS[*]})"
done

require_aws

deploy_data() {
  # Le fichier Comtrade n'est rechargé (et réingéré) que si la Silver est vide
  local args=()
  if aws s3 ls "s3://${PROJECT}-silver-${ACCOUNT_ID}/silver/trade_flows/" 2>/dev/null | grep -q .; then
    args+=(--no-data)
    log "Silver déjà remplie : infrastructure seulement (data-platform/scripts/deploy.sh pour réingérer)"
  fi
  "$REPO_ROOT/data-platform/scripts/deploy.sh" "${args[@]}"
}

for t in "${TARGETS[@]}"; do
  log "━━━━━━━━━━ ${t} ━━━━━━━━━━"
  case "$t" in
    data)    deploy_data ;;
    gold)    "$REPO_ROOT/backend/scripts/build_gold.sh" ;;
    api)     "$REPO_ROOT/backend/scripts/deploy_api.sh" ;;
    reports) "$REPO_ROOT/reports/scripts/deploy.sh" ;;
    frontend) "$REPO_ROOT/frontend/scripts/deploy.sh" ;;
  esac
done

API="$(stack_output "${PROJECT}-backend" ApiUrl 2>/dev/null || true)"
[[ -n "$API" && "$API" != "None" ]] && ok "API commune : ${API}/api/v1/health"
WEB="$(stack_output "${PROJECT}-frontend" Url 2>/dev/null || true)"
[[ -n "$WEB" && "$WEB" != "None" ]] && ok "Frontend    : ${WEB}"
exit 0
