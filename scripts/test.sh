#!/usr/bin/env bash
# Tests unitaires locaux de tous les composants (aucun appel AWS).
# Usage : ./scripts/test.sh [data backend reports]     Prérequis : uv
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TARGETS=("$@")
[[ ${#TARGETS[@]} -eq 0 ]] && TARGETS=(data backend reports)
FAILED=()

run() {  # run <nom> <dossier> <commande...>
  local name="$1" dir="$2"; shift 2
  log "Tests ${name}"
  if (cd "$REPO_ROOT/$dir" && "$@"); then ok "$name"; else FAILED+=("$name"); fi
}

for t in "${TARGETS[@]}"; do
  case "$t" in
    data)    run data data-platform \
               uv run --quiet --python 3.12 --with-requirements requirements-dev.txt pytest -q tests ;;
    backend) run backend backend \
               uv run --quiet --python 3.12 --with boto3 python -m unittest discover -s tests ;;
    reports) run reports reports \
               uv run --quiet --python 3.13 --with-requirements tests/requirements.txt pytest -q tests ;;
    *) die "Composant inconnu : $t (attendu : data backend reports)" ;;
  esac
done

[[ ${#FAILED[@]} -eq 0 ]] && ok "Tous les tests passent" || die "Échecs : ${FAILED[*]}"
