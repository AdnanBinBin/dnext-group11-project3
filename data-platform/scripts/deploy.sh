#!/usr/bin/env bash
# =============================================================================
#  Déploie (ou redéploie) la plateforme de données DNEXT : Bronze -> Silver
#
#  Idempotent : à relancer après chaque purge du compte AWS. Le script
#    1. vérifie les identifiants et choisit le rôle IAM (LabRole sur AWS Academy)
#    2. construit et publie le zip de la Lambda
#    3. répare l'état si la stack a été cassée/purgée, puis déploie CloudFormation
#    4. dépose le fichier Comtrade dans la zone Bronze -> ingestion automatique
#    5. attend le rapport d'ingestion et affiche le résumé
#
#  Usage :  ./scripts/deploy.sh [--fresh] [--no-data]
#     --fresh    supprime tout et recrée depuis zéro
#     --no-data  déploie l'infra sans déposer le fichier de données
#  Variables : PROJECT, AWS_REGION, EXECUTION_ROLE_ARN, PANDAS_LAYER_ARN, DATA_FILE
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FRESH=false; UPLOAD_DATA=true
for arg in "$@"; do
  case "$arg" in
    --fresh) FRESH=true ;;
    --no-data) UPLOAD_DATA=false ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) die "Argument inconnu : $arg" ;;
  esac
done

PY="$(find_python)"
init_context

# ---------------------------------------------------------------- 1. rôle --
if [[ -z "${EXECUTION_ROLE_ARN:-}" ]]; then
  if [[ "$CALLER_ARN" == *"voclabs"* ]] || aws iam get-role --role-name LabRole >/dev/null 2>&1; then
    EXECUTION_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/LabRole"
    log "AWS Academy détecté : utilisation de LabRole"
  else
    EXECUTION_ROLE_ARN=""
    log "Rôle IAM dédié créé par le template (moindre privilège)"
  fi
fi

# -------------------------------------------------------- layer pandas ----
if [[ -z "${PANDAS_LAYER_ARN:-}" ]]; then
  PANDAS_LAYER_ARN="$(aws ssm get-parameter \
    --name "/aws/service/aws-sdk-pandas/${AWSSDKPANDAS_VERSION}/py3.12/x86_64/layer-arn" \
    --query Parameter.Value --output text 2>/dev/null || true)"
fi
if [[ -z "${PANDAS_LAYER_ARN:-}" || "$PANDAS_LAYER_ARN" == "None" ]]; then
  # Repli : dernière version publiée pour py3.12 / x86_64
  PANDAS_LAYER_ARN="$(aws ssm get-parameters-by-path --path /aws/service/aws-sdk-pandas --recursive \
    --query "Parameters[?contains(Name, '/py3.12/x86_64/layer-arn')].[Name,Value]" --output text 2>/dev/null \
    | sort -V | tail -n1 | cut -f2 || true)"
fi
[[ "$PANDAS_LAYER_ARN" == arn:aws:lambda:* ]] || die "Layer AWS SDK for pandas introuvable.
   Va sur https://aws-sdk-pandas.readthedocs.io/en/stable/layers.html et relance avec :
   PANDAS_LAYER_ARN=arn:aws:lambda:${AWS_REGION}:336392948345:layer:AWSSDKPandas-Python312:<version> $0"
ok "layer pandas : ${PANDAS_LAYER_ARN}"

# ---------------------------------------------------------- 2. build ------
log "Construction du zip de la Lambda"
read -r ZIP_PATH ZIP_HASH < <("$PY" "$ROOT/scripts/build_lambda.py")
[[ -n "${ZIP_HASH:-}" && -f "$ZIP_PATH" ]] || die "Échec de la construction du zip (pip install openpyxl ?)"
LAMBDA_KEY="lambda/ingest-bronze-to-silver-${ZIP_HASH}.zip"

if ! bucket_exists "$ARTIFACTS_BUCKET"; then
  log "Création du bucket d'artefacts ${ARTIFACTS_BUCKET}"
  if [[ "$AWS_REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$ARTIFACTS_BUCKET" >/dev/null
  else
    aws s3api create-bucket --bucket "$ARTIFACTS_BUCKET" \
      --create-bucket-configuration "LocationConstraint=${AWS_REGION}" >/dev/null
  fi
  aws s3api put-public-access-block --bucket "$ARTIFACTS_BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
fi
aws s3 cp "$ZIP_PATH" "s3://${ARTIFACTS_BUCKET}/${LAMBDA_KEY}" --only-show-errors
ok "code publié : s3://${ARTIFACTS_BUCKET}/${LAMBDA_KEY}"

# ------------------------------------------- 3. état de la stack ----------
STATUS="$(stack_status)"
if $FRESH; then
  log "--fresh : suppression complète avant redéploiement"
  delete_stack; cleanup_orphans
elif [[ "$STATUS" =~ ^(ROLLBACK_COMPLETE|ROLLBACK_FAILED|CREATE_FAILED|DELETE_FAILED|UPDATE_ROLLBACK_FAILED)$ ]]; then
  warn "Stack dans un état non récupérable (${STATUS}) → recréation"
  delete_stack; cleanup_orphans
elif [[ "$STATUS" == *_IN_PROGRESS ]]; then
  die "Une opération est en cours sur la stack (${STATUS}). Réessaie dans quelques minutes."
elif [[ "$STATUS" != "NONE" ]]; then
  # La stack existe : ses ressources ont-elles été purgées par derrière ?
  if ! bucket_exists "$RAW_BUCKET" || ! bucket_exists "$SILVER_BUCKET" \
     || ! aws lambda get-function --function-name "$FUNCTION_NAME" >/dev/null 2>&1 \
     || ! aws glue get-table --database-name "$GLUE_DB" --name trade_flows >/dev/null 2>&1; then
    warn "La stack existe mais des ressources ont disparu (purge du compte ?) → recréation"
    delete_stack; cleanup_orphans
  fi
else
  cleanup_orphans   # restes d'une purge partielle qui bloqueraient la création
fi

# ---------------------------------------------------- déploiement ---------
log "Déploiement CloudFormation de ${STACK_NAME} (2-3 min)"
aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file "$ROOT/infra/data-platform.yaml" \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset \
  --tags project="$PROJECT" course=T-CloudSys group="${GROUP:-gr11}" \
  --parameter-overrides \
      ProjectName="$PROJECT" \
      GlueDatabaseName="$GLUE_DB" \
      ArtifactsBucket="$ARTIFACTS_BUCKET" \
      LambdaCodeKey="$LAMBDA_KEY" \
      PandasLayerArn="$PANDAS_LAYER_ARN" \
      ExecutionRoleArn="$EXECUTION_ROLE_ARN" \
  || die "Échec du déploiement. Détail : aws cloudformation describe-stack-events --stack-name ${STACK_NAME}"
ok "infrastructure déployée"

# ------------------------------------------------------ 4. données --------
if ! $UPLOAD_DATA; then
  ok "Terminé (--no-data : aucune donnée déposée)"; exit 0
fi
[[ -f "$DATA_FILE" ]] || die "Fichier de données introuvable : $DATA_FILE"

RECEIVED_AT="$(date -u +%Y%m%dT%H%M%SZ)"
RAW_KEY="comtrade/bilateral/received_at=${RECEIVED_AT}/$(basename "$DATA_FILE")"
log "Dépôt du fichier dans la zone Bronze : s3://${RAW_BUCKET}/${RAW_KEY}"
aws s3 cp "$DATA_FILE" "s3://${RAW_BUCKET}/${RAW_KEY}" --only-show-errors

# ------------------------------------------- 5. attente du rapport --------
log "Attente de l'ingestion par la Lambda (≈ 30-60 s)"
REPORT_URI="s3://${SILVER_BUCKET}/reports/ingestion/latest.json"
for _ in $(seq 1 36); do
  sleep 10
  REPORT="$(aws s3 cp "$REPORT_URI" - 2>/dev/null || true)"
  if [[ "$REPORT" == *"$RAW_KEY"* ]]; then
    echo "$REPORT" | "$PY" "$ROOT/scripts/print_report.py" \
      || die "L'ingestion a échoué (logs : aws logs tail /aws/lambda/${FUNCTION_NAME} --since 15m)"
    ok "Couche Silver prête : base Glue « ${GLUE_DB} », tables trade_flows / trade_flows_rejected"
    ok "Requêtes : workgroup Athena « ${WORKGROUP} » — vérification : ./scripts/smoke_test.sh"
    exit 0
  fi
done
die "Pas de rapport après 6 min. Logs : aws logs tail /aws/lambda/${FUNCTION_NAME} --since 15m"
