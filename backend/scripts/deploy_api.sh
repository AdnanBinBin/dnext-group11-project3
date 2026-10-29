#!/usr/bin/env bash
set -euo pipefail
# Configuration commune du dépôt (préfixe, région, base Glue…) et fonctions partagées
source "$(dirname "${BASH_SOURCE[0]}")/../../scripts/lib.sh"
HERE="$REPO_ROOT/backend"
REGION="$AWS_REGION"
STACK="${PROJECT}-backend"
require_aws
ensure_artifacts_bucket
prepare_stack "$STACK"
ARTIFACTS="$ARTIFACTS_BUCKET"
KEY="dnext-backend/backend.zip"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$HERE/.build"
cp "$HERE/src/app.py" "$TMP/app.py"
(cd "$TMP" && python3 -m zipfile -c "$HERE/.build/backend.zip" app.py)
aws s3 cp "$HERE/.build/backend.zip" "s3://${ARTIFACTS}/${KEY}" --region "$REGION"
aws cloudformation deploy \
  --stack-name "$STACK" \
  --template-file "$HERE/infra/backend-api.yaml" \
  --parameter-overrides "ArtifactBucket=${ARTIFACTS}" "ArtifactKey=${KEY}" \
      "ProjectName=${PROJECT}" "GlueDatabase=${GLUE_DB}" \
      "GoldTable=${GOLD_TABLE}" "AthenaWorkGroup=${ATHENA_WORKGROUP}" \
  --capabilities CAPABILITY_IAM \
  --tags "${STACK_TAGS[@]}" \
  --region "$REGION" \
  --no-fail-on-empty-changeset
# Met à jour le code lors d'un rerun où l'URL S3 reste inchangée.
aws lambda update-function-code --function-name "${PROJECT}-backend-api" \
  --s3-bucket "$ARTIFACTS" --s3-key "$KEY" --region "$REGION" >/dev/null
aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs" --output table
