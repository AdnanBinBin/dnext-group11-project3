#!/usr/bin/env bash
# =============================================================================
#  Test d'intégration post-déploiement : interroge la couche Silver via Athena
#  et vérifie quelques invariants. Usage : ./scripts/smoke_test.sh
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
init_context

FAILED=0

# Exécute une requête Athena et affiche le résultat (lignes tabulées)
athena() {
  local sql="$1" qid state
  qid="$(aws athena start-query-execution --work-group "$WORKGROUP" \
    --query-execution-context "Database=${GLUE_DB}" \
    --query-string "$sql" --query QueryExecutionId --output text)"
  while true; do
    state="$(aws athena get-query-execution --query-execution-id "$qid" \
      --query QueryExecution.Status.State --output text)"
    case "$state" in
      SUCCEEDED) break ;;
      FAILED|CANCELLED)
        aws athena get-query-execution --query-execution-id "$qid" \
          --query QueryExecution.Status.StateChangeReason --output text >&2
        return 1 ;;
      *) sleep 1 ;;
    esac
  done
  aws athena get-query-results --query-execution-id "$qid" \
    --query 'ResultSet.Rows[1:].Data[*].VarCharValue' --output text
}

check() {  # check "<description>" "<SQL qui renvoie 1 valeur>" "<valeur attendue>"
  local got; got="$(athena "$2" | tr -d '[:space:]')"
  if [[ "$got" == "$3" ]]; then ok "$1 ($got)"; else warn "$1 : attendu $3, obtenu $got"; FAILED=1; fi
}

log "Volumétrie Silver par année et par flux"
athena "SELECT year, flow, count(*) AS rows, round(sum(value_usd)/1e9, 2) AS value_busd
        FROM trade_flows GROUP BY 1, 2 ORDER BY 1, 2" | (column -t 2>/dev/null || cat)

log "Invariants"
check "Silver + quarantaine = lignes source" \
  "SELECT (SELECT count(*) FROM trade_flows)
        + (SELECT count(*) FROM trade_flows_rejected
           WHERE ingestion_id = (SELECT max(ingestion_id) FROM trade_flows))" "200587"
check "Une seule ingestion dans la table Silver" \
  "SELECT count(DISTINCT ingestion_id) FROM trade_flows" "1"
check "Clé (période, déclarant, flux, partenaire, HS4) unique" \
  "SELECT count(*) FROM (SELECT year_month, reporter_iso3, flow, partner_iso3, hs4
                         FROM trade_flows GROUP BY 1,2,3,4,5 HAVING count(*) > 1)" "0"
check "Exportateurs du périmètre" \
  "SELECT count(DISTINCT exporter_iso3) FROM trade_flows" "10"
check "Prix FOB uniquement sur les exports" \
  "SELECT count(*) FROM trade_flows WHERE (flow = 'Export') <> (price_basis = 'FOB')" "0"

[[ $FAILED -eq 0 ]] && ok "Smoke test OK" || die "Smoke test en échec"
