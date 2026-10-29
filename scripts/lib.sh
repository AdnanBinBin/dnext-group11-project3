#!/usr/bin/env bash
# Fonctions communes aux scripts globaux (deploy, destroy, status, test).
# shellcheck disable=SC2034

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../config.env
source "$REPO_ROOT/config.env"
export PROJECT GROUP AWS_REGION GLUE_DB ATHENA_WORKGROUP GOLD_TABLE BEDROCK_MODEL_ID REPORT_LANGUAGE
export AWS_DEFAULT_REGION="$AWS_REGION" AWS_PAGER=""

COMPONENTS=(data gold api reports frontend)
# Tags posés sur toutes les stacks (cloudformation deploy --tags)
STACK_TAGS=(project="$PROJECT" course=T-CloudSys group="$GROUP")

c_blue=$'\033[1;34m'; c_green=$'\033[1;32m'; c_yellow=$'\033[1;33m'; c_red=$'\033[1;31m'; c_off=$'\033[0m'
log()  { echo "${c_blue}==>${c_off} $*" >&2; }
ok()   { echo "${c_green}✔${c_off}  $*" >&2; }
warn() { echo "${c_yellow}!${c_off}  $*" >&2; }
die()  { echo "${c_red}✘  $*${c_off}" >&2; exit 1; }

require_aws() {
  command -v aws >/dev/null 2>&1 || die "AWS CLI v2 introuvable"
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)" \
    || die "Identifiants AWS invalides ou expirés (aws sts get-caller-identity)"
  ARTIFACTS_BUCKET="${PROJECT}-artifacts-${ACCOUNT_ID}"
  log "Compte ${ACCOUNT_ID} · région ${AWS_REGION} · préfixe ${PROJECT}"
}

stack_status() {
  aws cloudformation describe-stacks --stack-name "$1" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "NONE"
}

stack_output() {
  aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text
}

# Supprime une stack et attend la fin. Vide d'abord ses buckets (sinon CloudFormation
# échoue), sauf avec --keep-buckets pour ceux en DeletionPolicy: Retain qui portent des données.
remove_stack() {
  local stack="$1" keep="${2:-}" bucket
  [[ "$(stack_status "$stack")" == "NONE" ]] && return 0
  if [[ "$keep" != "--keep-buckets" ]]; then
    for bucket in $(aws cloudformation list-stack-resources --stack-name "$stack" \
        --query "StackResourceSummaries[?ResourceType=='AWS::S3::Bucket'].PhysicalResourceId" --output text); do
      aws s3api head-bucket --bucket "$bucket" >/dev/null 2>&1 || continue
      log "Vidage de s3://$bucket"
      aws s3 rm "s3://$bucket" --recursive --quiet || true
    done
  fi
  log "Suppression de la stack ${stack}"
  aws cloudformation delete-stack --stack-name "$stack"
  aws cloudformation wait stack-delete-complete --stack-name "$stack" \
    || die "Suppression de ${stack} impossible (aws cloudformation describe-stack-events --stack-name ${stack})"
}

# Ressources d'une stack supprimées hors CloudFormation (détection de dérive)
stack_deleted_resources() {
  local stack="$1" id state
  id="$(aws cloudformation detect-stack-drift --stack-name "$stack" \
    --query StackDriftDetectionId --output text)" || return 0
  while state="$(aws cloudformation describe-stack-drift-detection-status --stack-drift-detection-id "$id" \
      --query DetectionStatus --output text)" && [[ "$state" == "DETECTION_IN_PROGRESS" ]]; do
    sleep 2
  done
  aws cloudformation describe-stack-resource-drifts --stack-name "$stack" \
    --stack-resource-drift-status-filters DELETED \
    --query 'StackResourceDrifts[].LogicalResourceId' --output text | sed 's/^None$//'
}

# À appeler avant `cloudformation deploy` : une stack en échec, ou dont des ressources
# ont été purgées par derrière, serait laissée telle quelle par un deploy sans changement.
# On la supprime pour qu'elle soit recréée entièrement.
# Usage : prepare_stack <stack> [--keep-buckets]
prepare_stack() {
  local stack="$1" status missing
  status="$(stack_status "$stack")"
  case "$status" in
    NONE) return 0 ;;
    *_IN_PROGRESS) die "Opération en cours sur ${stack} (${status}) : réessayer dans quelques minutes" ;;
    ROLLBACK_COMPLETE|ROLLBACK_FAILED|CREATE_FAILED|DELETE_FAILED|UPDATE_ROLLBACK_FAILED|IMPORT_ROLLBACK_FAILED)
      warn "Stack ${stack} dans un état non récupérable (${status}) → recréation" ;;
    *)
      log "Vérification des ressources de ${stack}"
      missing="$(stack_deleted_resources "$stack")"
      [[ -z "${missing//[[:space:]]/}" ]] && return 0
      warn "Ressources de ${stack} supprimées hors CloudFormation (${missing//$'\t'/, }) → recréation" ;;
  esac
  remove_stack "$stack" "${2:-}"
}

ensure_artifacts_bucket() {
  aws s3api head-bucket --bucket "$ARTIFACTS_BUCKET" >/dev/null 2>&1 && return 0
  log "Création du bucket d'artefacts ${ARTIFACTS_BUCKET}"
  aws s3api create-bucket --bucket "$ARTIFACTS_BUCKET" \
    --create-bucket-configuration "LocationConstraint=${AWS_REGION}" >/dev/null
  aws s3api put-public-access-block --bucket "$ARTIFACTS_BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
}

# Exécute une requête Athena dans le workgroup du projet et affiche les lignes (hors en-tête)
athena_query() {
  local sql="$1" qid state
  qid="$(aws athena start-query-execution --work-group "$ATHENA_WORKGROUP" \
    --query-execution-context "Database=${GLUE_DB}" \
    --query-string "$sql" --query QueryExecutionId --output text)"
  while true; do
    state="$(aws athena get-query-execution --query-execution-id "$qid" \
      --query QueryExecution.Status.State --output text)"
    case "$state" in
      SUCCEEDED) break ;;
      FAILED|CANCELLED)
        aws athena get-query-execution --query-execution-id "$qid" \
          --query QueryExecution.Status.StateChangeReason --output text >&2
        return 1 ;;
      *) sleep 1 ;;
    esac
  done
  aws athena get-query-results --query-execution-id "$qid" \
    --query 'ResultSet.Rows[1:].Data[*].VarCharValue' --output text
}
