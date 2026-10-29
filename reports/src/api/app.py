"""API des rapports IA, routes ajoutées à l'API HTTP commune (payload v2).

POST /api/v1/reports                 -> demande un rapport pour une anomalie (asynchrone)
GET  /api/v1/reports?anomaly_id=...  -> rapports déjà générés pour une anomalie
GET  /api/v1/reports/{report_id}     -> statut + lien de téléchargement quand terminé
GET  /api/v1/reports/{report_id}/pdf -> redirection vers le PDF (URL présignée)

Une anomalie est désignée par son anomaly_id Gold (cas normal, renvoyé par
GET /api/v1/anomalies) ou, en mode autonome, par ses chiffres complets.
"""
import json
import os
import re
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

table = boto3.resource("dynamodb").Table(os.environ["REPORTS_TABLE"])
# Endpoint régional explicite : via l'endpoint global, S3 redirige les buckets récents
# hors us-east-1 et la signature de l'URL présignée ne correspond plus.
REGION = os.environ.get("AWS_REGION", "eu-north-1")
s3 = boto3.client("s3", region_name=REGION, endpoint_url=f"https://s3.{REGION}.amazonaws.com",
                  config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
sfn = boto3.client("stepfunctions")

BUCKET = os.environ["REPORTS_BUCKET"]
STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
DEFAULT_LANGUAGE = os.environ.get("REPORT_LANGUAGE", "fr")
URL_TTL = 3600
BASE = "/api/v1/reports"

REQUIRED_FIELDS = ("reporter", "partner", "hs_code", "period")
KEY_FIELDS = ("reporter", "partner", "hs_code", "frequency", "period")
CONTROL_FIELDS = ("force", "language", "anomaly")
ANOMALY_ID = re.compile(r"[A-F0-9]{32}")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def response(status, body=None, headers=None):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json; charset=utf-8", **(headers or {})},
        "body": json.dumps(body, ensure_ascii=False) if body is not None else "",
    }


def normalize_anomaly(fields):
    """Valide la désignation de l'anomalie et renvoie (anomalie, clé de déduplication)."""
    anomaly = {k: v for k, v in fields.items() if k not in CONTROL_FIELDS}
    if anomaly.get("anomaly_id"):
        anomaly_id = str(anomaly["anomaly_id"]).strip().upper()
        if not ANOMALY_ID.fullmatch(anomaly_id):
            raise ApiError(400, "anomaly_id doit être un identifiant hexadécimal de 32 caractères")
        anomaly["anomaly_id"] = anomaly_id
        return anomaly, f"ID|{anomaly_id}"
    missing = [f for f in REQUIRED_FIELDS if not anomaly.get(f)]
    if missing:
        raise ApiError(400, f"anomaly_id ou champs {', '.join(missing)} obligatoires")
    anomaly.setdefault("frequency", "A")
    return anomaly, "|".join(str(anomaly[k]).strip().upper() for k in KEY_FIELDS)


def public_view(item):
    view = {
        "report_id": item["report_id"],
        "status": item["status"],
        "anomaly": json.loads(item.get("anomaly_json", "{}")),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }
    if item.get("title"):
        view["title"] = item["title"]
    if item["status"] == "COMPLETED":
        view["pdf_url"] = presign(item["pdf_key"])
        view["json_url"] = presign(item["json_key"])
    if item["status"] == "FAILED":
        view["error"] = item.get("error_type")
    return view


def presign(key):
    return s3.generate_presigned_url(
        "get_object", Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=URL_TTL
    )


def reports_for(key, limit=10):
    result = table.query(
        IndexName="by-anomaly",
        KeyConditionExpression=Key("anomaly_key").eq(key),
        ScanIndexForward=False,
        Limit=limit,
    )
    return result["Items"]


def create_report(event):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        raise ApiError(400, "Corps JSON invalide")
    anomaly, key = normalize_anomaly(body.get("anomaly", body))

    # Un clic répété sur la même anomalie renvoie le rapport existant (pas de nouvel appel Bedrock)
    if not body.get("force"):
        for item in reports_for(key, limit=5):
            if item["status"] != "FAILED":
                return response(200, {**public_view(item), "reused": True})

    language = body.get("language") or DEFAULT_LANGUAGE
    report_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(Item={
        "report_id": report_id,
        "anomaly_key": key,
        "status": "PENDING",
        "language": language,
        "anomaly_json": json.dumps(anomaly, ensure_ascii=False),
        "created_at": now,
        "updated_at": now,
    })
    sfn.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        name=report_id,
        input=json.dumps({"report_id": report_id, "language": language, "anomaly": anomaly}),
    )
    return response(202, {
        "report_id": report_id,
        "status": "PENDING",
        "status_url": f"{BASE}/{report_id}",
    })


def list_reports(event):
    params = event.get("queryStringParameters") or {}
    _, key = normalize_anomaly(params)
    return response(200, {"anomaly_key": key, "reports": [public_view(i) for i in reports_for(key)]})


def get_report(report_id):
    item = table.get_item(Key={"report_id": report_id}).get("Item")
    if not item:
        raise ApiError(404, "Rapport introuvable")
    return response(200, public_view(item))


def download_report(report_id):
    item = table.get_item(Key={"report_id": report_id}).get("Item")
    if not item:
        raise ApiError(404, "Rapport introuvable")
    if item["status"] != "COMPLETED":
        return response(409, {"error": "Rapport pas encore disponible", "status": item["status"]})
    return response(302, headers={"location": presign(item["pdf_key"])})


def handler(event, _context):
    route = event.get("routeKey")
    report_id = (event.get("pathParameters") or {}).get("report_id")
    try:
        if route == f"POST {BASE}":
            return create_report(event)
        if route == f"GET {BASE}":
            return list_reports(event)
        if route == f"GET {BASE}/{{report_id}}":
            return get_report(report_id)
        if route == f"GET {BASE}/{{report_id}}/pdf":
            return download_report(report_id)
        raise ApiError(404, "Route introuvable")
    except ApiError as exc:
        return response(exc.status, {"error": str(exc)})
