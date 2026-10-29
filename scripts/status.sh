#!/usr/bin/env bash
# État de la plateforme : stacks, buckets, tables, API.  Usage : ./scripts/status.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_aws

log "Stacks CloudFormation"
for s in data-platform gold backend reports frontend; do
  printf '    %-32s %s\n' "${PROJECT}-${s}" "$(stack_status "${PROJECT}-${s}")"
done

log "Buckets S3"
aws s3api list-buckets --query "Buckets[?starts_with(Name, '${PROJECT}-')].Name" --output text | tr '\t' '\n' | sed 's/^/    /'

log "Tables Glue (${GLUE_DB})"
aws glue get-tables --database-name "$GLUE_DB" --query "TableList[].Name" --output text 2>/dev/null \
  | tr '\t' '\n' | sed 's/^/    /' || echo "    (base absente)"

log "Ressources taguées project=${PROJECT}"
aws resourcegroupstaggingapi get-resources --tag-filters "Key=project,Values=${PROJECT}" \
  --query "ResourceTagMappingList[].ResourceARN" --output text | tr '\t' '\n' | sed 's/^/    /'

API="$(stack_output "${PROJECT}-backend" ApiUrl 2>/dev/null || true)"
if [[ -n "$API" && "$API" != "None" ]]; then
  log "API ${API}"
  printf '    /api/v1/health  -> %s\n' "$(curl -s -m 10 "$API/api/v1/health")"
fi

WEB="$(stack_output "${PROJECT}-frontend" Url 2>/dev/null || true)"
if [[ -n "$WEB" && "$WEB" != "None" ]]; then
  log "Frontend ${WEB}"
  printf '    HTTP %s\n' "$(curl -s -m 10 -o /dev/null -w '%{http_code}' "$WEB")"
fi
