"""Lambda d'ingestion Bronze -> Silver.

Déclenchement :
  * automatique : notification S3 "ObjectCreated" sur le bucket Bronze (raw)
  * manuel      : {"bucket": "<raw-bucket>", "key": "comtrade/bilateral/..."}

Sorties (bucket Silver) :
  silver/trade_flows/year=YYYY/part-<ingestion_id>.parquet   <- table Glue trade_flows
  quarantine/trade_flows/rejected-<ingestion_id>.parquet     <- table Glue trade_flows_rejected
  reports/ingestion/<ingestion_id>.json + latest.json        <- rapport qualité

Sémantique : chaque fichier Comtrade est un *snapshot complet*. Une nouvelle
ingestion écrit d'abord tous ses fichiers, puis supprime ceux de l'ingestion
précédente : la table Silver n'est jamais vide ni en double.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone
from io import BytesIO

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

from transform import SchemaError, read_source, transform

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SILVER_BUCKET = os.environ.get("SILVER_BUCKET", "")
SILVER_PREFIX = os.environ.get("SILVER_PREFIX", "silver/trade_flows/")
QUARANTINE_PREFIX = os.environ.get("QUARANTINE_PREFIX", "quarantine/trade_flows/")
REPORT_PREFIX = os.environ.get("REPORT_PREFIX", "reports/ingestion/")
SUPPORTED_EXT = (".xlsx", ".xlsm", ".csv")

s3 = boto3.client("s3")

SILVER_SCHEMA = pa.schema([
    ("year_month", pa.string()),
    ("month", pa.int32()),
    ("period_date", pa.date32()),
    ("reporter_iso3", pa.string()),
    ("flow", pa.string()),
    ("partner_iso3", pa.string()),
    ("partner_raw", pa.string()),
    ("exporter_iso3", pa.string()),
    ("importer_iso3", pa.string()),
    ("price_basis", pa.string()),
    ("hs4", pa.string()),
    ("product", pa.string()),
    ("value_usd", pa.float64()),
    ("qty_t", pa.float64()),
    ("partner_is_aggregate", pa.bool_()),
    ("dq_flags", pa.string()),
    ("source_row", pa.int32()),
    ("ingestion_id", pa.string()),
    ("source_file", pa.string()),
    ("ingested_at", pa.timestamp("ms", tz="UTC")),
])

REJECTED_SCHEMA = pa.schema(
    [("reject_reason", pa.string())]
    + [(c, pa.string()) for c in
       ["yearmonth", "reporter", "flow", "partner", "hs4", "product", "value_usd", "qty_t"]]
    + [("source_row", pa.int32()), ("ingestion_id", pa.string()), ("source_file", pa.string())]
)


# --------------------------------------------------------------------------- #
def lambda_handler(event, context):
    targets = _parse_event(event)
    results = [process_object(bucket, key) for bucket, key in targets]
    return {"results": results}


def _parse_event(event) -> list[tuple[str, str]]:
    if "Records" in event:
        return [
            (r["s3"]["bucket"]["name"], urllib.parse.unquote_plus(r["s3"]["object"]["key"]))
            for r in event["Records"] if r.get("eventSource") == "aws:s3"
        ]
    if "bucket" in event and "key" in event:
        return [(event["bucket"], event["key"])]
    raise ValueError(f"Événement non reconnu : {json.dumps(event)[:500]}")


def process_object(bucket: str, key: str) -> dict:
    source_file = f"s3://{bucket}/{key}"
    if not key.lower().endswith(SUPPORTED_EXT):
        logger.info("Ignoré (extension non supportée) : %s", source_file)
        return {"source_file": source_file, "status": "SKIPPED"}

    head = s3.head_object(Bucket=bucket, Key=key)
    etag = head["ETag"].strip('"')
    now = datetime.now(timezone.utc).replace(microsecond=0)
    ingestion_id = f"{now:%Y%m%dT%H%M%SZ}-{etag[:8]}"
    logger.info("Ingestion %s : %s (%s octets)", ingestion_id, source_file, head["ContentLength"])

    local_path = os.path.join("/tmp", os.path.basename(key))
    s3.download_file(bucket, key, local_path)

    try:
        raw = read_source(local_path)
        result = transform(raw, ingestion_id=ingestion_id, source_file=source_file, ingested_at=now)
    except SchemaError as exc:
        # Erreur de contenu : inutile de laisser Lambda réessayer, on documente l'échec.
        logger.error("Fichier refusé : %s", exc)
        report = _failure_report(ingestion_id, source_file, now, "REJECTED_SCHEMA", str(exc))
        _write_report(report)
        return report
    except Exception as exc:  # erreur technique -> rapport + retry Lambda
        logger.exception("Échec de l'ingestion")
        _write_report(_failure_report(ingestion_id, source_file, now, "FAILED", repr(exc)))
        raise
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

    written = _write_silver(result.silver, ingestion_id)
    removed = _remove_previous_snapshot(ingestion_id)
    rejected_key = _write_rejected(result.rejected, ingestion_id)

    report = result.report | {
        "silver_bucket": SILVER_BUCKET,
        "silver_files": written,
        "previous_files_removed": removed,
        "quarantine_file": rejected_key,
    }
    _write_report(report)
    logger.info("Ingestion terminée : %s lignes Silver, %s rejetées",
                report["rows_silver"], report["rows_rejected"])
    return {k: report[k] for k in ("ingestion_id", "status", "rows_in", "rows_silver", "rows_rejected")}


# --------------------------------------------------------------------------- #
# Écritures S3
# --------------------------------------------------------------------------- #
def _to_parquet_bytes(df, schema: pa.Schema) -> bytes:
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False, safe=False)
    buf = BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


def _write_silver(silver, ingestion_id: str) -> list[str]:
    keys = []
    for year, part in silver.groupby("year", sort=True):
        key = f"{SILVER_PREFIX}year={int(year)}/part-{ingestion_id}.parquet"
        body = _to_parquet_bytes(part.drop(columns=["year"]), SILVER_SCHEMA)
        s3.put_object(Bucket=SILVER_BUCKET, Key=key, Body=body,
                      ContentType="application/vnd.apache.parquet")
        keys.append(key)
    return keys


def _remove_previous_snapshot(ingestion_id: str) -> int:
    """Supprime les fichiers Silver qui n'appartiennent pas à l'ingestion courante."""
    to_delete = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=SILVER_BUCKET, Prefix=SILVER_PREFIX):
        for obj in page.get("Contents", []):
            if ingestion_id not in obj["Key"]:
                to_delete.append({"Key": obj["Key"]})
    for i in range(0, len(to_delete), 1000):
        s3.delete_objects(Bucket=SILVER_BUCKET, Delete={"Objects": to_delete[i:i + 1000], "Quiet": True})
    return len(to_delete)


def _write_rejected(rejected, ingestion_id: str) -> str | None:
    if rejected.empty:
        return None
    key = f"{QUARANTINE_PREFIX}rejected-{ingestion_id}.parquet"
    rejected = rejected.rename(columns=str.lower)  # noms de colonnes Athena-friendly
    s3.put_object(Bucket=SILVER_BUCKET, Key=key, Body=_to_parquet_bytes(rejected, REJECTED_SCHEMA))
    return key


def _write_report(report: dict) -> None:
    body = json.dumps(report, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    for key in (f"{REPORT_PREFIX}{report['ingestion_id']}.json", f"{REPORT_PREFIX}latest.json"):
        s3.put_object(Bucket=SILVER_BUCKET, Key=key, Body=body, ContentType="application/json")


def _failure_report(ingestion_id, source_file, now, status, error) -> dict:
    return {"status": status, "ingestion_id": ingestion_id, "source_file": source_file,
            "ingested_at": now.isoformat(), "error": error}
