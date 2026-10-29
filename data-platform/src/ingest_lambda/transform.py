"""Transformation Bronze (Excel brut UN Comtrade) -> Silver (données validées).

Module volontairement indépendant d'AWS (pandas uniquement) pour pouvoir être
testé en local sans compte AWS. Le handler Lambda s'occupe des I/O S3.

Principes de la couche Silver :
  * une ligne Silver = une déclaration Comtrade (on ne somme JAMAIS Export et
    Import, cf. description du dataset) ;
  * types propres, codes normalisés, orientation du flux (exportateur /
    importateur) et base de prix déclarée (FOB / CIF) ;
  * AUCUN calcul métier : prix unitaires, mirror matching et anomalies
    relèvent de la couche Results (Gold), hors du périmètre de ce module ;
  * aucune ligne n'est perdue silencieusement : ce qui est invalide part en
    quarantaine avec un motif, ce qui est suspect mais légitime est conservé
    avec des flags qualité (dq_flags).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from config import (
    AGGREGATE_PARTNERS,
    HS4_PRODUCTS,
    LOW_QTY_THRESHOLD_T,
    MIN_YEAR,
    PARTNER_CODE_MAPPING,
    REQUIRED_COLUMNS,
    SCOPE_EXPORTERS,
    VALID_FLOWS,
)

ISO3_RE = r"^[A-Z]{3}$"
PERIOD_RE = re.compile(r"^(\d{4})-(\d{2})$")

KEY_COLUMNS = ["year_month", "reporter_iso3", "flow", "partner_iso3", "hs4"]

# Ordre et noms des colonnes Silver (le contrat avec la couche suivante)
SILVER_COLUMNS = [
    "year_month",            # string  'YYYY-MM'
    "year",                  # int     (colonne de partition)
    "month",                 # int
    "period_date",           # date    1er jour du mois
    "reporter_iso3",         # string  pays déclarant
    "flow",                  # string  'Export' | 'Import'
    "partner_iso3",          # string  pays partenaire (normalisé)
    "partner_raw",           # string  libellé partenaire d'origine
    "exporter_iso3",         # string  pays exportateur du flux physique
    "importer_iso3",         # string  pays importateur du flux physique
    "price_basis",           # string  'FOB' (déclaration export) | 'CIF' (import)
    "hs4",                   # string  code HS4
    "product",               # string  libellé produit de référence
    "value_usd",             # double
    "qty_t",                 # double  (NULL si manquant dans la source)
    "partner_is_aggregate",  # boolean zone "nes" Comtrade, pas de miroir possible
    "dq_flags",              # string  flags qualité séparés par '|', NULL si aucun
    "source_row",            # int     n° de ligne dans le fichier source (Excel)
    "ingestion_id",          # string
    "source_file",           # string  s3://… du fichier Bronze
    "ingested_at",           # timestamp UTC
]


class SchemaError(ValueError):
    """Le fichier source n'a pas la structure attendue -> ingestion refusée."""


@dataclass
class TransformResult:
    silver: pd.DataFrame
    rejected: pd.DataFrame
    report: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Lecture
# --------------------------------------------------------------------------- #
def read_source(path: str) -> pd.DataFrame:
    """Lit le fichier Bronze en texte brut (aucune conversion implicite)."""
    lower = path.lower()
    if lower.endswith((".xlsx", ".xlsm")):
        return pd.read_excel(path, dtype=str, engine="openpyxl")
    if lower.endswith(".csv"):
        return pd.read_csv(path, dtype=str, keep_default_na=True)
    raise SchemaError(f"Format de fichier non supporté : {path}")


# --------------------------------------------------------------------------- #
# Transformation
# --------------------------------------------------------------------------- #
def _clean_str(s: pd.Series) -> pd.Series:
    s = s.astype("object").where(s.notna(), None)
    s = s.map(lambda v: v.strip() if isinstance(v, str) else v)
    return s.map(lambda v: None if v in ("", "nan", "NaN", "None") else v)


def _normalize_hs4(s: pd.Series) -> pd.Series:
    # Excel peut transformer 1001 en '1001.0' ou perdre un zéro de tête
    def norm(v):
        if v is None:
            return None
        v = re.sub(r"\.0+$", "", str(v))
        return v.zfill(4) if v.isdigit() else v
    return s.map(norm)


def transform(
    raw: pd.DataFrame,
    *,
    ingestion_id: str,
    source_file: str,
    ingested_at: datetime | None = None,
    today: date | None = None,
) -> TransformResult:
    ingested_at = ingested_at or datetime.now(timezone.utc)
    today = today or ingested_at.date()

    missing = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
    if missing:
        raise SchemaError(f"Colonnes manquantes : {missing}")
    if raw.empty:
        raise SchemaError("Le fichier source ne contient aucune ligne")

    src = raw[REQUIRED_COLUMNS].copy().reset_index(drop=True)
    for c in REQUIRED_COLUMNS:
        src[c] = _clean_str(src[c])

    n = len(src)
    out = pd.DataFrame(index=src.index)

    # --- Période ----------------------------------------------------------
    ym = src["YearMonth"].fillna("")
    parts = ym.str.extract(PERIOD_RE)
    year = pd.to_numeric(parts[0], errors="coerce")
    month = pd.to_numeric(parts[1], errors="coerce")
    current_ym = today.year * 100 + today.month
    period_ok = (
        year.notna() & month.between(1, 12)
        & (year >= MIN_YEAR) & ((year * 100 + month) <= current_ym)
    )

    # --- Flux, pays ---------------------------------------------------------
    flow = src["Flow"].fillna("").str.upper().map(VALID_FLOWS)
    reporter = src["Reporter"].fillna("").str.upper()
    partner_raw = src["Partner"].fillna("").str.upper()
    # NB : un code partenaire vide reste "" et sera rejeté (INVALID_PARTNER)
    partner = partner_raw.map(lambda p: PARTNER_CODE_MAPPING.get(p, p))
    partner_is_aggregate = partner.isin(AGGREGATE_PARTNERS)

    reporter_ok = reporter.str.match(ISO3_RE)
    partner_ok = partner.str.match(ISO3_RE) | partner_is_aggregate

    # --- Produit ------------------------------------------------------------
    hs4 = _normalize_hs4(src["HS4"])
    hs4_ok = hs4.isin(HS4_PRODUCTS.keys())
    product_ref = hs4.map(HS4_PRODUCTS)

    # --- Mesures -----------------------------------------------------------
    value = pd.to_numeric(src["Value_USD"], errors="coerce")
    qty = pd.to_numeric(src["Qty_t"], errors="coerce")
    value_bad_text = src["Value_USD"].notna() & value.isna()
    qty_bad_text = src["Qty_t"].notna() & qty.isna()

    # --- Périmètre : chaque ligne doit impliquer un des 10 exportateurs ----
    exporter = np.where(flow == "Export", reporter, partner)
    importer = np.where(flow == "Export", partner, reporter)
    in_scope = pd.Series(exporter, index=src.index).isin(SCOPE_EXPORTERS)

    # --- Motifs de rejet (le premier motif rencontré l'emporte) -----------
    conditions = [
        ~period_ok,
        flow.isna(),
        ~reporter_ok,
        ~partner_ok,
        reporter == partner,
        ~hs4_ok,
        ~in_scope,
        value.isna() | value_bad_text | (value < 0),
        qty_bad_text | (qty < 0),
    ]
    reasons = [
        "INVALID_PERIOD", "INVALID_FLOW", "INVALID_REPORTER", "INVALID_PARTNER",
        "SELF_TRADE", "UNKNOWN_HS4", "OUT_OF_SCOPE", "INVALID_VALUE", "INVALID_QTY",
    ]
    reject_reason = pd.Series(
        np.select(conditions, reasons, default=""), index=src.index
    ).replace("", None)

    # --- Doublons de clé (parmi les lignes encore valides) -----------------
    key_df = pd.DataFrame({
        "ym": ym, "r": reporter, "f": flow, "p": partner, "h": hs4,
    })
    valid_mask = reject_reason.isna()
    dup = key_df[valid_mask].duplicated(keep="first")
    reject_reason.loc[dup[dup].index] = "DUPLICATE_KEY"
    valid_mask = reject_reason.isna()

    # --- Flags qualité (lignes conservées) ---------------------------------
    flag_defs = {
        "MISSING_QTY": qty.isna(),
        "ZERO_QTY": qty == 0,
        "ZERO_VALUE": value == 0,
        "LOW_QTY": (qty > 0) & (qty < LOW_QTY_THRESHOLD_T),
        "PARTNER_CODE_MAPPED": partner != partner_raw,
        "PARTNER_AGGREGATE": partner_is_aggregate,
        "PRODUCT_LABEL_FIXED": hs4_ok & (src["Product"].fillna("") != product_ref.fillna("")),
    }
    flags = pd.Series([""] * n, index=src.index, dtype="object")
    for name, mask in flag_defs.items():
        flags = flags + np.where(mask.fillna(False), name + "|", "")
    flags = flags.str.rstrip("|").replace("", None)

    # --- Assemblage Silver ------------------------------------------------
    out["year_month"] = ym
    out["year"] = year
    out["month"] = month
    out["reporter_iso3"] = reporter
    out["flow"] = flow
    out["partner_iso3"] = partner
    out["partner_raw"] = src["Partner"]
    out["exporter_iso3"] = exporter
    out["importer_iso3"] = importer
    out["price_basis"] = np.where(flow == "Export", "FOB", "CIF")
    out["hs4"] = hs4
    out["product"] = product_ref
    out["value_usd"] = value
    out["qty_t"] = qty
    out["partner_is_aggregate"] = partner_is_aggregate.astype(bool)
    out["dq_flags"] = flags
    out["source_row"] = src.index + 2  # +1 en-tête, +1 base 1 (ligne Excel)

    silver = out[valid_mask].copy()
    silver["year"] = silver["year"].astype("int32")
    silver["month"] = silver["month"].astype("int32")
    silver["source_row"] = silver["source_row"].astype("int32")
    silver["value_usd"] = silver["value_usd"].astype("float64")
    silver["qty_t"] = silver["qty_t"].astype("float64")
    silver["period_date"] = [date(int(y), int(m), 1) for y, m in zip(silver["year"], silver["month"])]
    silver["ingestion_id"] = ingestion_id
    silver["source_file"] = source_file
    ts = pd.Timestamp(ingested_at)
    silver["ingested_at"] = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    for c in ["year_month", "reporter_iso3", "flow", "partner_iso3", "partner_raw",
              "exporter_iso3", "importer_iso3", "price_basis", "hs4", "product",
              "dq_flags", "ingestion_id", "source_file"]:
        silver[c] = silver[c].astype("object")
    silver = silver[SILVER_COLUMNS].sort_values(
        ["year_month", "hs4", "exporter_iso3", "importer_iso3", "flow"]
    ).reset_index(drop=True)

    # --- Quarantaine ---------------------------------------------------------
    rejected = src[~valid_mask].copy().astype("object")
    rejected.insert(0, "reject_reason", reject_reason[~valid_mask].astype("object"))
    rejected["source_row"] = (rejected.index + 2).astype("int32")
    rejected["ingestion_id"] = ingestion_id
    rejected["source_file"] = source_file
    rejected = rejected.reset_index(drop=True)

    report = build_report(src, silver, rejected, flag_defs, valid_mask,
                          ingestion_id=ingestion_id, source_file=source_file,
                          ingested_at=ingested_at)
    return TransformResult(silver=silver, rejected=rejected, report=report)


# --------------------------------------------------------------------------- #
# Rapport qualité
# --------------------------------------------------------------------------- #
def build_report(src, silver, rejected, flag_defs, valid_mask, *,
                 ingestion_id, source_file, ingested_at) -> dict:
    def counts(series):
        return {str(k): int(v) for k, v in series.value_counts().sort_index().items()}

    return {
        "status": "SUCCEEDED",
        "ingestion_id": ingestion_id,
        "source_file": source_file,
        "ingested_at": pd.Timestamp(ingested_at).isoformat(),
        "rows_in": int(len(src)),
        "rows_silver": int(len(silver)),
        "rows_rejected": int(len(rejected)),
        "rejected_by_reason": counts(rejected["reject_reason"]) if len(rejected) else {},
        "flags_on_silver_rows": {
            name: int((mask.fillna(False) & valid_mask).sum())
            for name, mask in flag_defs.items()
        },
        "period_min": silver["year_month"].min() if len(silver) else None,
        "period_max": silver["year_month"].max() if len(silver) else None,
        "rows_by_flow": counts(silver["flow"]),
        "rows_by_hs4": counts(silver["hs4"]),
        "rows_by_year": counts(silver["year"]),
        "exporters": sorted(silver["exporter_iso3"].unique().tolist()),
        "distinct_importers": int(silver["importer_iso3"].nunique()),
    }
