#!/usr/bin/env bash
# Supprime la stack des rapports IA (vide d'abord son bucket, sinon CloudFormation échoue).
# Les routes /api/v1/reports disparaissent de l'API commune ; le reste du backend est intact.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../scripts/lib.sh"

STACK_NAME="${PROJECT}-reports"
require_aws

if [[ "$(stack_status "$STACK_NAME")" == "NONE" ]]; then
  ok "Stack ${STACK_NAME} absente"
  exit 0
fi
remove_stack "$STACK_NAME"
ok "${STACK_NAME} supprimée"
