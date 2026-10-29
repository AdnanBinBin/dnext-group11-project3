#!/usr/bin/env bash
# Test de bout en bout sur l'API déployée : demande un rapport, suit son statut,
# télécharge le PDF dans out/.
#
# Usage : reports/scripts/test_report.sh [--id ANOMALY_ID | --file anomalie.json] [--force]
#   (défaut)  première anomalie renvoyée par GET /api/v1/anomalies (données Gold réelles)
#   --id      anomalie Gold précise
#   --file    mode autonome : chiffres fournis dans la requête (ex. reports/events/sample_anomaly.json)
#   --force   régénère même si un rapport existe déjà pour cette anomalie
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../scripts/lib.sh"

ANOMALY_ID=""; EVENT=""; FORCE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --id) ANOMALY_ID="$2"; shift 2 ;;
    --file) EVENT="$2"; shift 2 ;;
    --force) FORCE=true; shift ;;
    *) die "Argument inconnu : $1" ;;
  esac
done

json() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get(sys.argv[1]) or "")' "$1"; }

API="$(stack_output "${PROJECT}-backend" ApiUrl)"
[[ -n "$API" && "$API" != "None" ]] || die "Stack ${PROJECT}-backend introuvable"
log "API : $API"

if [[ -n "$EVENT" ]]; then
  BODY=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d["force"]=sys.argv[2]=="true"; print(json.dumps(d))' \
    "$EVENT" "$FORCE")
else
  if [[ -z "$ANOMALY_ID" ]]; then
    ANOMALY_ID=$(curl -sS "$API/api/v1/anomalies?exporter=ARG&product=1001&limit=1" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["items"][0]["anomaly_id"])')
  fi
  log "Anomalie $ANOMALY_ID"
  BODY="{\"anomaly_id\": \"$ANOMALY_ID\", \"force\": $FORCE}"
fi

RESP=$(curl -sS -X POST "$API/api/v1/reports" -H 'content-type: application/json' --data "$BODY")
ID=$(echo "$RESP" | json report_id)
[[ -n "$ID" ]] || die "Réponse inattendue : $RESP"
log "Rapport $ID ($(echo "$RESP" | json status))"

START=$SECONDS
while true; do
  STATUS_JSON=$(curl -sS "$API/api/v1/reports/$ID")
  STATUS=$(echo "$STATUS_JSON" | json status)
  printf '  %3ss  %s\n' "$((SECONDS - START))" "$STATUS"
  case "$STATUS" in
    COMPLETED) break ;;
    FAILED) die "Échec : $STATUS_JSON" ;;
  esac
  (( SECONDS - START < 240 )) || die "Délai dépassé"
  sleep 3
done

mkdir -p "$REPO_ROOT/out"
curl -sSL "$API/api/v1/reports/$ID/pdf" -o "$REPO_ROOT/out/$ID.pdf"
ok "Titre : $(echo "$STATUS_JSON" | json title)"
ok "PDF   : out/$ID.pdf"
