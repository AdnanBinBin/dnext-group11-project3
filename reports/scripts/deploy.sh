#!/usr/bin/env bash
# Déploie (ou met à jour) la stack des rapports IA.
# Prérequis : stack backend déployée (on ajoute nos routes à son API HTTP),
#             aws cli configurée, uv (construction de la couche Python).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../scripts/lib.sh"

HERE="$REPO_ROOT/reports"
BUILD_DIR="$HERE/.build"
STACK_NAME="${PROJECT}-reports"

require_aws
ensure_artifacts_bucket

API_ID="$(stack_output "${PROJECT}-backend" ApiId 2>/dev/null || true)"
[[ -n "$API_ID" && "$API_ID" != "None" ]] \
  || die "Stack ${PROJECT}-backend absente ou sans sortie ApiId : ./scripts/deploy.sh api d'abord"
prepare_stack "$STACK_NAME"

# 1. Couche Python : wheels Linux x86_64 / Python 3.13, quel que soit l'OS local.
#    Reconstruite seulement si requirements.txt a changé.
HASH=$(sha256sum "$HERE/layer/requirements.txt" | cut -d' ' -f1)
if [[ "$(cat "$BUILD_DIR/layer.hash" 2>/dev/null)" != "$HASH" ]]; then
  log "Construction de la couche Python (matplotlib, fpdf2)"
  rm -rf "$BUILD_DIR/layer"
  mkdir -p "$BUILD_DIR/layer/python"
  uv pip install --quiet \
    --target "$BUILD_DIR/layer/python" \
    --python-platform x86_64-manylinux_2_28 \
    --python-version 3.13 \
    --only-binary :all: \
    -r "$HERE/layer/requirements.txt"
  find "$BUILD_DIR/layer" -type d \( -name __pycache__ -o -name tests \) -prune -exec rm -rf {} +
  echo "$HASH" > "$BUILD_DIR/layer.hash"
fi
log "Couche : $(du -sh "$BUILD_DIR/layer" | cut -f1)"

# 2. Envoi du code dans S3 et déploiement CloudFormation (transform SAM côté AWS)
log "Packaging"
aws cloudformation package \
  --template-file "$HERE/infra/template.yaml" \
  --s3-bucket "$ARTIFACTS_BUCKET" \
  --s3-prefix reports \
  --output-template-file "$BUILD_DIR/packaged.yaml" 2> >(grep -v Uploading >&2) >/dev/null

log "Déploiement de ${STACK_NAME}"
aws cloudformation deploy \
  --template-file "$BUILD_DIR/packaged.yaml" \
  --stack-name "$STACK_NAME" \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
  --parameter-overrides \
      "Prefix=$PROJECT" \
      "BackendApiId=$API_ID" \
      "BedrockModelId=$BEDROCK_MODEL_ID" \
      "ReportLanguage=$REPORT_LANGUAGE" \
      "AthenaDatabase=$GLUE_DB" \
      "GoldTable=$GOLD_TABLE" \
      "AthenaWorkGroup=$ATHENA_WORKGROUP" \
  --tags "${STACK_TAGS[@]}" \
  --no-fail-on-empty-changeset

aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" --output table
