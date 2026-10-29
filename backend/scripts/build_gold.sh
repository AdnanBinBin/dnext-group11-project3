#!/usr/bin/env bash
# =============================================================================
#  Crée la couche Gold (bucket + table mirror_matches) et la remplit une fois.
#
#  Idempotent : sans option, ne recalcule rien si la table contient déjà des
#  lignes (02_populate_gold.sql fait un INSERT : le relancer créerait des doublons).
#
#  Usage :  backend/scripts/build_gold.sh [--rebuild]
#     --rebuild  supprime la table et ses fichiers Parquet, puis recalcule tout
#                (à utiliser après une nouvelle ingestion Silver)
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../scripts/lib.sh"
HERE="$REPO_ROOT/backend"

REBUILD=false
[[ "${1:-}" == "--rebuild" ]] && REBUILD=true

require_aws
GOLD_STACK="${PROJECT}-gold"
GOLD_BUCKET="${PROJECT}-gold-${ACCOUNT_ID}"

# Les fichiers SQL sont écrits pour le compte de démonstration : on adapte
# compte, préfixe et base Glue à la configuration courante.
render_sql() {
  grep -v '^[[:space:]]*--' "$HERE/sql/$1" \
    | sed -e "s/<ACCOUNT_ID>/${ACCOUNT_ID}/g" \
          -e "s/dnext-trade-gold-/${PROJECT}-gold-/g" \
          -e "s/dnext_trade\./${GLUE_DB}./g"
}

# ---------------------------------------------------------------- bucket --
# Bucket en Retain : une stack à recréer garde ses données, réutilisées ci-dessous
prepare_stack "$GOLD_STACK" --keep-buckets
if aws s3api head-bucket --bucket "$GOLD_BUCKET" >/dev/null 2>&1 \
   && [[ "$(stack_status "$GOLD_STACK")" == "NONE" ]]; then
  warn "Bucket ${GOLD_BUCKET} conservé d'une ancienne stack (Retain) : réutilisé tel quel"
else
  log "Stack ${GOLD_STACK} (bucket Gold)"
  aws cloudformation deploy --stack-name "$GOLD_STACK" \
    --template-file "$HERE/infra/gold-storage.yaml" \
    --parameter-overrides "ProjectName=${PROJECT}" \
    --tags "${STACK_TAGS[@]}" --no-fail-on-empty-changeset >/dev/null
  ok "bucket ${GOLD_BUCKET}"
fi

aws glue get-table --database-name "$GLUE_DB" --name trade_flows >/dev/null 2>&1 \
  || die "Table Silver ${GLUE_DB}.trade_flows absente : déployer d'abord data-platform"

# ----------------------------------------------------------------- table --
if $REBUILD; then
  log "--rebuild : suppression de ${GLUE_DB}.${GOLD_TABLE} et de ses fichiers"
  athena_query "DROP TABLE IF EXISTS ${GLUE_DB}.${GOLD_TABLE}" >/dev/null
  aws s3 rm "s3://${GOLD_BUCKET}/${GOLD_TABLE}/" --recursive --quiet
fi

log "Déclaration de la table ${GLUE_DB}.${GOLD_TABLE}"
athena_query "$(render_sql 01_create_gold.sql)" >/dev/null

ROWS="$(athena_query "SELECT count(*) FROM ${GLUE_DB}.${GOLD_TABLE}" | tr -d '[:space:]')"
if [[ "$ROWS" == "0" ]]; then
  log "Calcul du mirror matching (Athena, ~1 min)"
  athena_query "$(render_sql 02_populate_gold.sql)" >/dev/null
  ok "table Gold remplie"
else
  ok "table déjà remplie (${ROWS} lignes) : rien à recalculer (--rebuild pour forcer)"
fi

# ------------------------------------------------------------ vérification --
log "Contrôles (03_validate_gold.sql)"
athena_query "SELECT match_status, COUNT(*) FROM ${GLUE_DB}.${GOLD_TABLE} GROUP BY 1 ORDER BY 2 DESC" \
  | (column -t 2>/dev/null || cat)
read -r TOTAL ANOMALIES UNIQUE_IDS < <(athena_query \
  "SELECT COUNT(*), COUNT_IF(is_anomaly), COUNT(DISTINCT anomaly_id) FROM ${GLUE_DB}.${GOLD_TABLE}")
echo "    lignes ${TOTAL} · anomalies ${ANOMALIES} · identifiants distincts ${UNIQUE_IDS}"
[[ "$TOTAL" == "$UNIQUE_IDS" ]] || die "Identifiants en double : la table a probablement été remplie deux fois (--rebuild)"
ok "Gold prête"
