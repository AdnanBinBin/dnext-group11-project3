"""Étape 1 du workflow : rassembler le contexte chiffré d'une anomalie.

- chiffres du flux signalé (valeurs, poids, prix unitaires FOB/CIF, écart) ;
- historique du même couple exportateur/importateur/produit autour de la période ;
- prix pratiqués par le même exportateur vers ses autres partenaires sur la période.

Cas normal : la requête donne un anomaly_id et tout est lu dans la table Gold
(mirror_matches) avec Athena. Mode autonome (pas d'anomaly_id, ou ATHENA_DATABASE
vide) : chiffres, historique et comparables viennent de la requête elle-même.

Vocabulaire et unités internes : prix unitaires en USD/kg, écart en fraction,
motifs d'anomalie identiques à la Gold (CIF_BELOW_FOB, GAP_ABOVE_35).
Les noms lisibles (pays, produit) sont ajoutés dans la langue du rapport pour
que le texte rédigé et le PDF ne montrent pas de codes.
"""
import os
import statistics
import time

import boto3

from countries import COUNTRIES

ATHENA_DATABASE = os.environ.get("ATHENA_DATABASE", "")
ATHENA_TABLE = os.environ.get("ATHENA_TABLE", "mirror_matches")
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "primary")
GAP_THRESHOLD = float(os.environ.get("GAP_THRESHOLD", "0.35"))
HISTORY_MONTHS = 12  # de part et d'autre du mois de l'anomalie

ANOMALY_REASONS = ("CIF_BELOW_FOB", "GAP_ABOVE_35")
NUMERIC_FIELDS = (
    "export_value_usd", "export_weight_kg", "import_value_usd", "import_weight_kg",
    "fob_unit_price", "cif_unit_price", "gap",
)

# Colonnes Gold converties dans le vocabulaire interne (tonnes -> kg, USD/t -> USD/kg, % -> fraction)
GOLD_COLUMNS = """
    anomaly_id, year_month AS period, exporter_iso3 AS reporter, importer_iso3 AS partner,
    hs4 AS hs_code, product,
    fob_value_usd AS export_value_usd, fob_qty_t * 1000 AS export_weight_kg,
    cif_value_usd AS import_value_usd, cif_qty_t * 1000 AS import_weight_kg,
    fob_price / 1000 AS fob_unit_price, cif_price / 1000 AS cif_unit_price,
    gap_pct / 100 AS gap, match_status, anomaly_reason
"""
ANOMALY_SQL = f'SELECT {GOLD_COLUMNS} FROM "{{table}}" WHERE anomaly_id = ? LIMIT 1'
HISTORY_SQL = f"""
SELECT {GOLD_COLUMNS} FROM "{{table}}"
WHERE exporter_iso3 = ? AND importer_iso3 = ? AND hs4 = ? AND year_month BETWEEN ? AND ?
ORDER BY year_month
"""
PEERS_SQL = f"""
SELECT {GOLD_COLUMNS} FROM "{{table}}"
WHERE exporter_iso3 = ? AND hs4 = ? AND year_month = ? AND match_status = 'MATCHED'
ORDER BY fob_value_usd DESC
LIMIT 15
"""

# Produits suivis par la Gold (code SH à 4 chiffres) : (français, anglais)
PRODUCTS = {
    "1001": ("Blé", "Wheat"), "1003": ("Orge", "Barley"), "1005": ("Maïs", "Corn"),
    "1201": ("Soja", "Soybeans"), "1205": ("Colza", "Rapeseed"),
    "1206": ("Graines de tournesol", "Sunflower seeds"), "2304": ("Tourteaux de soja", "Soybean meal"),
}

athena = boto3.client("athena")


def country_name(code, language):
    names = COUNTRIES.get(str(code))
    return names[0 if language == "fr" else 1] if names else str(code)


def product_name(hs_code, fallback, language):
    names = PRODUCTS.get(str(hs_code))
    return names[0 if language == "fr" else 1] if names else (fallback or str(hs_code))


class AnomalyNotFound(Exception):
    """anomaly_id absent de la table Gold : inutile de réessayer."""


def to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ratio(a, b):
    if a is None or not b:
        return None
    return a / b


def classify(fob, cif, gap):
    """Même règle que la Gold (02_populate_gold.sql)."""
    if fob is None or cif is None:
        return None
    if cif < fob:
        return "CIF_BELOW_FOB"
    if gap is not None and gap > GAP_THRESHOLD:
        return "GAP_ABOVE_35"
    return "OK"


def normalize(row):
    """Complète un flux : prix unitaires (USD/kg), écart, ratios miroir et motif d'anomalie."""
    flow = dict(row)
    for field in NUMERIC_FIELDS:
        flow[field] = to_float(flow.get(field))

    fob = flow["fob_unit_price"]
    if fob is None:
        fob = ratio(flow["export_value_usd"], flow["export_weight_kg"])
    cif = flow["cif_unit_price"]
    if cif is None:
        cif = ratio(flow["import_value_usd"], flow["import_weight_kg"])
    gap = flow["gap"]
    if gap is None and fob and cif is not None:
        gap = (cif - fob) / fob

    status = flow.pop("match_status", None)
    reason = flow.pop("anomaly_reason", None)
    if reason:
        flag = reason
    elif status and status != "MATCHED":
        flag = status  # MISSING_EXPORT, MISSING_IMPORT, INVALID_PRICE, AGGREGATE_PARTNER
    else:
        flag = flow.get("flag") or classify(fob, cif, gap)

    flow.update(
        fob_unit_price=fob,
        cif_unit_price=cif,
        gap=gap,
        weight_ratio_import_export=ratio(flow["import_weight_kg"], flow["export_weight_kg"]),
        value_ratio_import_export=ratio(flow["import_value_usd"], flow["export_value_usd"]),
        flag=flag,
    )
    return flow


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def athena_rows(sql, params):
    # Le workgroup impose son emplacement de résultats (EnforceWorkGroupConfiguration)
    execution = athena.start_query_execution(
        QueryString=sql.format(table=ATHENA_TABLE),
        QueryExecutionContext={"Database": ATHENA_DATABASE},
        WorkGroup=ATHENA_WORKGROUP,
        ExecutionParameters=[quote(p) for p in params],
    )
    query_id = execution["QueryExecutionId"]

    deadline = time.time() + 45
    while True:
        status = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]
        if status["State"] == "SUCCEEDED":
            break
        if status["State"] in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Athena {status['State']}: {status.get('StateChangeReason')}")
        if time.time() > deadline:
            athena.stop_query_execution(QueryExecutionId=query_id)
            raise TimeoutError("Requête Athena trop longue")
        time.sleep(0.5)

    rows = []
    for page in athena.get_paginator("get_query_results").paginate(QueryExecutionId=query_id):
        rows.extend([c.get("VarCharValue") for c in r["Data"]] for r in page["ResultSet"]["Rows"])
    header, data = rows[0], rows[1:]
    return [dict(zip(header, r)) for r in data]


def shift_month(year_month, months):
    year, month = map(int, year_month.split("-"))
    index = year * 12 + (month - 1) + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def from_gold(anomaly_id):
    found = athena_rows(ANOMALY_SQL, [anomaly_id])
    if not found:
        raise AnomalyNotFound(f"anomaly_id {anomaly_id} absent de {ATHENA_DATABASE}.{ATHENA_TABLE}")
    anomaly = {**found[0], "frequency": "M"}
    period = anomaly["period"]
    history = athena_rows(HISTORY_SQL, [
        anomaly["reporter"], anomaly["partner"], anomaly["hs_code"],
        shift_month(period, -HISTORY_MONTHS), shift_month(period, HISTORY_MONTHS),
    ])
    peers = athena_rows(PEERS_SQL, [anomaly["reporter"], anomaly["hs_code"], period])
    return anomaly, history, peers


def history_stats(history, period):
    others = [h for h in history if str(h.get("period")) != str(period)]
    usual_gaps = [h["gap"] for h in others if h.get("gap") is not None and h.get("flag") == "OK"]
    return {
        "periods_available": len(history),
        "flagged_periods": [h["period"] for h in history if h.get("flag") in ANOMALY_REASONS],
        "periods_without_mirror": [h["period"] for h in history
                                   if h.get("flag") in ("MISSING_EXPORT", "MISSING_IMPORT")],
        "median_gap_unflagged_periods": statistics.median(usual_gaps) if usual_gaps else None,
    }


def handler(event, _context):
    raw = event["anomaly"]
    language = event.get("language") or "fr"
    warnings = []

    if raw.get("anomaly_id") and ATHENA_DATABASE:
        anomaly, history, peers = from_gold(raw["anomaly_id"])
        source = "gold"
    else:
        anomaly = {k: v for k, v in raw.items() if k not in ("history", "peers")}
        history, peers = raw.get("history", []), raw.get("peers", [])
        source = "request"

    anomaly = normalize(anomaly)
    history = sorted((normalize(h) for h in history), key=lambda h: str(h.get("period")))
    peers = [normalize(p) for p in peers]
    anomaly.update(
        reporter_name=country_name(anomaly["reporter"], language),
        partner_name=country_name(anomaly["partner"], language),
        product_name=product_name(anomaly["hs_code"], anomaly.get("product"), language),
    )
    for peer in peers:
        peer["partner_name"] = country_name(peer["partner"], language)
    if len(history) <= 1:
        warnings.append("Aucun historique comparable pour ce couple de pays : "
                        "impossible de comparer avec les périodes voisines."
                        if language == "fr" else
                        "No comparable history for this pair of countries: "
                        "the neighbouring periods cannot be compared.")

    return {
        "anomaly": anomaly,
        "rule": {
            "gap_formula": "(CIF - FOB) / FOB",
            "gap_threshold": GAP_THRESHOLD,
            "triggered": anomaly["flag"],
        },
        "history": history,
        "history_stats": history_stats(history, anomaly["period"]),
        "peers_same_period": peers,
        "external_context": [],
        "data_source": source,
        "warnings": warnings,
    }
