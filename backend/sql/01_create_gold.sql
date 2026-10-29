-- À exécuter dans Athena / dnext-trade-wg / dnext_trade
-- Pré-requis : bucket dnext-trade-gold-<ACCOUNT_ID> créé par CloudFormation.
-- Remplacer <ACCOUNT_ID> par l'ID de votre compte AWS.
-- IMPORTANT : créer une seule fois. Ne relancer pas l'INSERT sans vider le préfixe.
CREATE EXTERNAL TABLE IF NOT EXISTS dnext_trade.mirror_matches (
  anomaly_id string,
  year_month string,
  exporter_iso3 string,
  importer_iso3 string,
  hs4 string,
  product string,
  fob_value_usd double,
  fob_qty_t double,
  cif_value_usd double,
  cif_qty_t double,
  fob_price double,
  cif_price double,
  gap_pct double,
  match_status string,
  anomaly_reason string,
  is_anomaly boolean
)
STORED AS PARQUET
LOCATION 's3://dnext-trade-gold-<ACCOUNT_ID>/mirror_matches/'
TBLPROPERTIES ('parquet.compression'='SNAPPY');
