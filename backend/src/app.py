"""DNEXT read-only MVP API: API Gateway HTTP API -> Lambda -> Athena Gold.

No third-party Python dependencies: boto3 is included in the AWS Lambda Python runtime.
"""
from __future__ import annotations

import json
import os
import re
import time
import traceback
from typing import Any

import boto3

DB = os.getenv('ATHENA_DATABASE', 'dnext_trade')
TABLE = os.getenv('GOLD_TABLE', 'mirror_matches')
WORKGROUP = os.getenv('ATHENA_WORKGROUP', 'dnext-trade-wg')
_athena = None


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def athena_client():
    global _athena
    if _athena is None:
        _athena = boto3.client('athena')
    return _athena


def sql_rows(query: str, max_rows: int = 1200) -> list[dict[str, Any]]:
    client = athena_client()
    result = client.start_query_execution(
        QueryString=query,
        QueryExecutionContext={'Database': DB},
        WorkGroup=WORKGROUP,
    )
    execution_id = result['QueryExecutionId']
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        execution = client.get_query_execution(QueryExecutionId=execution_id)['QueryExecution']
        state = execution['Status']['State']
        if state == 'SUCCEEDED':
            break
        if state in ('FAILED', 'CANCELLED'):
            reason = execution['Status'].get('StateChangeReason', 'Unknown Athena error')
            print('Athena failed:', reason)
            raise ApiError(502, 'La requête analytique a échoué')
        time.sleep(0.5)
    else:
        client.stop_query_execution(QueryExecutionId=execution_id)
        raise ApiError(503, 'Athena répond trop lentement, réessaie')

    records = []
    next_token = None
    first_page = True
    while True:
        kwargs = {'QueryExecutionId': execution_id, 'MaxResults': 1000}
        if next_token:
            kwargs['NextToken'] = next_token
        response = client.get_query_results(**kwargs)
        columns = response['ResultSet']['ResultSetMetadata']['ColumnInfo']
        rows = response['ResultSet']['Rows']
        if first_page and rows:
            rows = rows[1:]  # Athena includes a header row on the first page.
        first_page = False
        for row in rows:
            fields = row.get('Data', [])
            obj = {}
            for idx, column in enumerate(columns):
                raw = fields[idx].get('VarCharValue') if idx < len(fields) else None
                typ = column['Type'].lower()
                if raw is None:
                    value = None
                elif typ == 'boolean':
                    value = raw.lower() == 'true'
                elif typ in ('tinyint', 'smallint', 'integer', 'bigint'):
                    value = int(raw)
                elif typ in ('float', 'double', 'real') or typ.startswith('decimal'):
                    value = float(raw)
                else:
                    value = raw
                obj[column['Name']] = value
            records.append(obj)
            if len(records) > max_rows:
                raise ApiError(500, 'Trop de résultats pour cette réponse')
        next_token = response.get('NextToken')
        if not next_token:
            return records


def validate_params(params: dict[str, str | None], *, require_scope: bool = False) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for name in ('exporter', 'importer'):
        value = (params.get(name) or '').strip().upper()
        if value and not re.fullmatch('[A-Z]{3}', value):
            raise ApiError(400, f'{name} doit être un code ISO3 de 3 lettres')
        if value:
            cleaned[name] = value
    product = (params.get('product') or '').strip()
    if product and not re.fullmatch(r'\d{4}', product):
        raise ApiError(400, 'product doit être un code HS4 de 4 chiffres')
    if product:
        cleaned['product'] = product
    for key in ('start', 'end'):
        value = (params.get(key) or '').strip()
        if value and (not re.fullmatch(r'20\d\d-(0[1-9]|1[0-2])', value)):
            raise ApiError(400, f'{key} doit être au format YYYY-MM')
        if value:
            cleaned[key] = value
    if 'start' in cleaned and 'end' in cleaned and cleaned['start'] > cleaned['end']:
        raise ApiError(400, 'start doit précéder end')
    if require_scope and not all(k in cleaned for k in ('exporter', 'product')):
        raise ApiError(400, 'exporter et product sont obligatoires')
    return cleaned


def where_sql(p: dict[str, Any]) -> str:
    # Values are substituted only after a strict whitelist/regex validation.
    conditions = ['1=1']
    for param, column in [('exporter', 'exporter_iso3'),
                          ('importer', 'importer_iso3'), ('product', 'hs4')]:
        if param in p:
            conditions.append(f"{column} = '{p[param]}'")
    if 'start' in p:
        conditions.append(f"year_month >= '{p['start']}'")
    if 'end' in p:
        conditions.append(f"year_month <= '{p['end']}'")
    return ' AND '.join(conditions)


def get_filters() -> dict[str, Any]:
    query = f"""
        SELECT 'exporter' AS kind, exporter_iso3 AS code,
               exporter_iso3 AS label FROM {DB}.{TABLE}
        WHERE exporter_iso3 IS NOT NULL GROUP BY exporter_iso3
        UNION ALL
        SELECT 'product' AS kind, hs4 AS code, max(product) AS label
        FROM {DB}.{TABLE} WHERE hs4 IS NOT NULL GROUP BY hs4
        ORDER BY kind, code
    """
    data = sql_rows(query, max_rows=100)
    return {
        'exporters': [r['code'] for r in data if r['kind'] == 'exporter'],
        'products': [{'hs4': r['code'], 'name': r['label']}
                     for r in data if r['kind'] == 'product'],
        'frequencies': ['monthly', 'quarterly', 'yearly'],
        'anomaly_threshold_pct': 35,
    }


def get_heatmap(params: dict[str, str | None]) -> dict[str, Any]:
    p = validate_params(params, require_scope=True)
    freq = (params.get('frequency') or 'yearly').strip().lower()
    period_expressions = {
        'monthly': 'b.year_month',
        'quarterly': "concat(substr(b.year_month,1,4), '-Q', "
                     "cast(cast(ceil(cast(substr(b.year_month,6,2) AS double)/3) AS integer) AS varchar))",
        'yearly': 'substr(b.year_month,1,4)',
    }
    if freq not in period_expressions:
        raise ApiError(400, 'frequency doit être monthly, quarterly ou yearly')
    period = period_expressions[freq]
    query = f"""
       WITH base AS (
           SELECT * FROM {DB}.{TABLE} WHERE {where_sql(p)}
       ), partners AS (
           SELECT importer_iso3
           FROM base
           WHERE importer_iso3 IS NOT NULL AND match_status <> 'AGGREGATE_PARTNER'
           GROUP BY importer_iso3
           ORDER BY SUM(COALESCE(fob_value_usd, cif_value_usd, 0)) DESC
           LIMIT 10
       )
       SELECT b.importer_iso3 AS partner, {period} AS period,
              COUNT(*) AS total_count,
              COUNT_IF(b.match_status='MATCHED') AS matched_count,
              COUNT_IF(b.is_anomaly) AS anomaly_count
       FROM base b JOIN partners p ON b.importer_iso3 = p.importer_iso3
       GROUP BY b.importer_iso3, {period}
       ORDER BY b.importer_iso3, period
    """
    records = sql_rows(query, max_rows=1200)
    for r in records:
        r['status'] = ('flagged' if r['anomaly_count'] > 0 else
                       'consistent' if r['matched_count'] > 0 else 'no_match')
    return {'exporter': p['exporter'], 'product': p['product'],
            'frequency': freq, 'cells': records}


def get_anomalies(params: dict[str, str | None]) -> dict[str, Any]:
    p = validate_params(params)
    try:
        limit = int(params.get('limit') or 50)
    except (ValueError, TypeError):
        raise ApiError(400, 'limit doit être un entier')
    if not 1 <= limit <= 100:
        raise ApiError(400, 'limit doit être compris entre 1 et 100')
    query = f"""
        SELECT anomaly_id, year_month, exporter_iso3, importer_iso3, hs4,
               product, ROUND(fob_price,2) AS fob_price,
               ROUND(cif_price,2) AS cif_price,
               ROUND(gap_pct,2) AS gap_pct, anomaly_reason
        FROM {DB}.{TABLE}
        WHERE {where_sql(p)} AND is_anomaly = true
        ORDER BY year_month DESC, exporter_iso3, importer_iso3, hs4
        LIMIT {limit}
    """
    return {'items': sql_rows(query, max_rows=100), 'limit': limit}


def get_anomaly(anomaly_id: str) -> dict[str, Any]:
    anomaly_id = (anomaly_id or '').upper()
    if not re.fullmatch(r'[A-F0-9]{32}', anomaly_id):
        raise ApiError(400, 'Identifiant d’anomalie invalide')
    rows = sql_rows(f"SELECT * FROM {DB}.{TABLE} WHERE anomaly_id = '{anomaly_id}' LIMIT 2", 2)
    if not rows:
        raise ApiError(404, 'Anomalie introuvable')
    return rows[0]


def response(status: int, body: Any) -> dict[str, Any]:
    return {'statusCode': status,
            'headers': {'content-type': 'application/json; charset=utf-8',
                        'cache-control': 'no-store'},
            'body': json.dumps(body, ensure_ascii=False)}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    method = event.get('requestContext', {}).get('http', {}).get('method', 'GET')
    path = (event.get('rawPath') or '/').rstrip('/') or '/'
    params = event.get('queryStringParameters') or {}
    try:
        if method != 'GET':
            raise ApiError(405, 'Méthode non autorisée')
        if path == '/api/v1/health':
            return response(200, {'status': 'ok', 'service': 'dnext-backend'})
        if path == '/api/v1/filters':
            return response(200, get_filters())
        if path == '/api/v1/heatmap':
            return response(200, get_heatmap(params))
        if path == '/api/v1/anomalies':
            return response(200, get_anomalies(params))
        if path.startswith('/api/v1/anomalies/'):
            return response(200, get_anomaly(path.split('/')[-1]))
        raise ApiError(404, 'Route introuvable')
    except ApiError as exc:
        return response(exc.status, {'error': str(exc)})
    except Exception:
        traceback.print_exc()
        return response(500, {'error': 'Erreur interne du serveur'})
