#!/usr/bin/env bash
# Fonctions communes à deploy.sh / destroy.sh / smoke_test.sh
# shellcheck disable=SC2034

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Configuration commune du dépôt (préfixe, région, base Glue…)
# shellcheck source=../../config.env
[[ -f "$ROOT/../config.env" ]] && source "$ROOT/../config.env"

# ---------------------------------------------------------------- config --
PROJECT="${PROJECT:-dnext-trade}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-eu-north-1}}"
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION" AWS_PAGER=""
STACK_NAME="${STACK_NAME:-${PROJECT}-data-platform}"
GLUE_DB="${GLUE_DB:-dnext_trade}"
DATA_FILE="${DATA_FILE:-$ROOT/data/Bilateral_data.xlsx}"
# Version de "AWS SDK for pandas" dont on prend le layer Lambda (pandas + pyarrow)
AWSSDKPANDAS_VERSION="${AWSSDKPANDAS_VERSION:-3.17.1}"

# ---------------------------------------------------------------- logs ----
c_blue=$'\033[1;34m'; c_green=$'\033[1;32m'; c_yellow=$'\033[1;33m'; c_red=$'\033[1;31m'; c_off=$'\033[0m'
log()  { echo "${c_blue}==>${c_off} $*" >&2; }
ok()   { echo "${c_green}✔${c_off}  $*" >&2; }
warn() { echo "${c_yellow}!${c_off}  $*" >&2; }
die()  { echo "${c_red}✘  $*${c_off}" >&2; exit 1; }

# ---------------------------------------------------------------- outils --
find_python() {
  for p in python3 python; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
      echo "$p"; return
    fi
  done
  die "Python >= 3.9 introuvable"
}

init_context() {
  command -v aws >/dev/null 2>&1 || die "AWS CLI v2 introuvable (https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)"
  CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text 2>/dev/null)" \
    || die "Identifiants AWS invalides ou expirés. Sur AWS Academy : recopie 'AWS Details > AWS CLI' dans ~/.aws/credentials."
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
  RAW_BUCKET="${PROJECT}-raw-${ACCOUNT_ID}"
  SILVER_BUCKET="${PROJECT}-silver-${ACCOUNT_ID}"
  ATHENA_BUCKET="${PROJECT}-athena-${ACCOUNT_ID}"
  ARTIFACTS_BUCKET="${PROJECT}-artifacts-${ACCOUNT_ID}"
  FUNCTION_NAME="${PROJECT}-ingest-bronze-to-silver"
  WORKGROUP="${PROJECT}-wg"
  DATA_BUCKETS=("$RAW_BUCKET" "$SILVER_BUCKET" "$ATHENA_BUCKET")
  log "Compte ${ACCOUNT_ID} · région ${AWS_REGION} · identité ${CALLER_ARN}"
}

# ------------------------------------------------------------- stack ------
stack_status() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "NONE"
}

bucket_exists() { aws s3api head-bucket --bucket "$1" >/dev/null 2>&1; }

# Vide un bucket, y compris toutes les versions et delete markers
empty_bucket() {
  local b="$1" kind objs
  bucket_exists "$b" || return 0
  aws s3 rm "s3://$b" --recursive --quiet >/dev/null 2>&1 || true
  for kind in Versions DeleteMarkers; do
    while true; do
      objs="$(aws s3api list-object-versions --bucket "$b" \
        --query "{Objects: ${kind}[0:1000].{Key:Key,VersionId:VersionId}, Quiet: \`true\`}" \
        --output json 2>/dev/null || echo '{"Objects": null}')"
      [[ "$objs" == *'"Objects": null'* || "$objs" == *'"Objects": []'* ]] && break
      aws s3api delete-objects --bucket "$b" --delete "$objs" >/dev/null
    done
  done
}

delete_bucket() {
  local b="$1"
  bucket_exists "$b" || return 0
  empty_bucket "$b"
  aws s3api delete-bucket --bucket "$b" && ok "bucket supprimé : $b"
}

# Supprime la stack (en vidant d'abord les buckets, sinon CloudFormation échoue)
delete_stack() {
  local status; status="$(stack_status)"
  [[ "$status" == "NONE" ]] && return 0
  log "Suppression de la stack ${STACK_NAME} (statut : ${status})"
  for b in "${DATA_BUCKETS[@]}"; do empty_bucket "$b"; done
  aws cloudformation delete-stack --stack-name "$STACK_NAME"
  if ! aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"; then
    warn "Suppression incomplète, nettoyage manuel des ressources restantes puis nouvel essai"
    cleanup_orphans
    aws cloudformation delete-stack --stack-name "$STACK_NAME"
    aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" \
      || die "Impossible de supprimer la stack ${STACK_NAME} (voir la console CloudFormation)"
  fi
  ok "stack supprimée"
}

# Ressources à nom fixe qui bloqueraient une recréation si elles ont survécu
# hors stack (ex. purge partielle du compte par le professeur).
cleanup_orphans() {
  for b in "${DATA_BUCKETS[@]}"; do delete_bucket "$b"; done
  if aws lambda get-function --function-name "$FUNCTION_NAME" >/dev/null 2>&1; then
    aws lambda delete-function --function-name "$FUNCTION_NAME" && ok "lambda orpheline supprimée"
  fi
  if aws glue get-database --name "$GLUE_DB" >/dev/null 2>&1; then
    aws glue delete-database --name "$GLUE_DB" && ok "base Glue orpheline supprimée"
  fi
  if aws athena get-work-group --work-group "$WORKGROUP" >/dev/null 2>&1; then
    aws athena delete-work-group --work-group "$WORKGROUP" --recursive-delete-option \
      && ok "workgroup Athena orphelin supprimé"
  fi
}

stack_output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
