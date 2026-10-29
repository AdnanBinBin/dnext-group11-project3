#!/usr/bin/env bash
# Lance le frontend Vue en local (serveur de développement Vite, rechargement à chaud),
# branché sur l'API commune déployée.
# Usage : frontend/scripts/serve.sh      (http://localhost:8080)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../scripts/lib.sh"
FRONTEND="$REPO_ROOT/frontend"

API="$(stack_output "${PROJECT}-backend" ApiUrl)"
[[ -n "$API" && "$API" != "None" ]] || die "Stack ${PROJECT}-backend introuvable : ./scripts/deploy.sh api"
mkdir -p "$FRONTEND/public"
echo "window.APP_CONFIG = { apiUrl: \"$API\" };" > "$FRONTEND/public/config.js"

cd "$FRONTEND"
[[ -d node_modules ]] || npm ci --no-audit --no-fund
ok "API      : $API"
exec npm run dev
