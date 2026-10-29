#!/usr/bin/env bash
# =============================================================================
#  Supprime toutes les ressources AWS de la plateforme (stack + buckets vidés).
#  À lancer quand le projet n'est pas utilisé pour ne pas consommer de crédit.
#  Usage :  ./scripts/destroy.sh [--yes]
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
init_context

if [[ "${1:-}" != "--yes" ]]; then
  read -r -p "Supprimer la stack ${STACK_NAME} et TOUTES ses données (y/N) ? " answer
  [[ "$answer" =~ ^[yYoO]$ ]] || { echo "Annulé."; exit 0; }
fi

delete_stack
cleanup_orphans
delete_bucket "$ARTIFACTS_BUCKET"
ok "Tout est supprimé. Pour tout recréer : ./scripts/deploy.sh"
