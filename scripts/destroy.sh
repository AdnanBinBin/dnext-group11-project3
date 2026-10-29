#!/usr/bin/env bash
# =============================================================================
#  Supprime une partie de la plateforme, dans l'ordre inverse des dépendances.
#
#    frontend app Amplify (URL publique du frontend)
#    reports  rapports IA (routes /api/v1/reports, Step Functions, Lambdas, bucket des PDF)
#    api      API commune et Lambda de consultation
#    gold     table mirror_matches et bucket Gold (données recalculables)
#    data     Bronze/Silver, Glue, Athena et bucket d'artefacts (TOUTES les données)
#
#  Usage :  ./scripts/destroy.sh frontend reports api   # plus rien d'exposé, les données restent
#           ./scripts/destroy.sh all                     # tout, avec confirmation
#  Sans argument, n'efface rien et affiche cette aide.
#  Rien n'est planifié : ce script ne s'exécute que lorsqu'on le lance.
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[[ $# -eq 0 ]] && { sed -n '2,15p' "$0"; exit 0; }
TARGETS=("$@")
[[ "${TARGETS[0]}" == "all" ]] && TARGETS=("${COMPONENTS[@]}")
for t in "${TARGETS[@]}"; do
  [[ " ${COMPONENTS[*]} " == *" $t "* ]] || die "Composant inconnu : $t (attendu : ${COMPONENTS[*]})"
done

require_aws

if [[ " ${TARGETS[*]} " == *" data "* || " ${TARGETS[*]} " == *" gold "* ]]; then
  read -r -p "Supprimer des données (${TARGETS[*]}) partagées avec toute l'équipe (y/N) ? " answer
  [[ "$answer" =~ ^[yYoO]$ ]] || { echo "Annulé."; exit 0; }
fi

destroy_gold() {
  local bucket="${PROJECT}-gold-${ACCOUNT_ID}"
  athena_query "DROP TABLE IF EXISTS ${GLUE_DB}.${GOLD_TABLE}" >/dev/null || true
  remove_stack "${PROJECT}-gold" --keep-buckets
  # Le bucket est en DeletionPolicy: Retain : on le supprime explicitement
  if aws s3api head-bucket --bucket "$bucket" >/dev/null 2>&1; then
    aws s3 rb "s3://$bucket" --force >/dev/null
  fi
  ok "Gold supprimée"
}

# Ordre inverse des dépendances, quel que soit l'ordre des arguments
for t in frontend reports api gold data; do
  [[ " ${TARGETS[*]} " == *" $t "* ]] || continue
  log "━━━━━━━━━━ ${t} ━━━━━━━━━━"
  case "$t" in
    frontend) "$REPO_ROOT/frontend/scripts/destroy.sh" ;;
    reports) "$REPO_ROOT/reports/scripts/destroy.sh" ;;
    api)
      if [[ "$(stack_status "${PROJECT}-reports")" != "NONE" ]]; then
        die "La stack ${PROJECT}-reports utilise l'API : la supprimer d'abord (destroy.sh reports api)"
      fi
      remove_stack "${PROJECT}-backend"
      ok "API supprimée" ;;
    gold) destroy_gold ;;
    data) "$REPO_ROOT/data-platform/scripts/destroy.sh" --yes ;;
  esac
done
