#!/usr/bin/env bash
# Supprime l'app Amplify du frontend (et son URL publique).  Usage : frontend/scripts/destroy.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../scripts/lib.sh"
require_aws

STACK="${PROJECT}-frontend"
if [[ "$(stack_status "$STACK")" == "NONE" ]]; then
  ok "Stack ${STACK} absente"
  exit 0
fi
remove_stack "$STACK"
ok "Frontend supprimé"
