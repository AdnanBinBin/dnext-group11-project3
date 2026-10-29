-- À exécuter UNE fois après 01_create_gold.sql.
-- Conserve les déclarations sans miroir et les prix non calculables.
-- Un rerun ajouterait des doublons : voir README (procédure de reconstruction).
INSERT INTO dnext_trade.mirror_matches
WITH exports AS (
    SELECT * FROM dnext_trade.trade_flows
    WHERE flow = 'Export' AND price_basis = 'FOB'
), imports AS (
    SELECT * FROM dnext_trade.trade_flows
    WHERE flow = 'Import' AND price_basis = 'CIF'
), paired AS (
    SELECT
        COALESCE(e.year_month, i.year_month) AS year_month,
        COALESCE(e.exporter_iso3, i.exporter_iso3) AS exporter_iso3,
        COALESCE(e.importer_iso3, i.importer_iso3) AS importer_iso3,
        COALESCE(e.hs4, i.hs4) AS hs4,
        COALESCE(e.product, i.product) AS product,
        e.value_usd AS fob_value_usd,
        e.qty_t AS fob_qty_t,
        i.value_usd AS cif_value_usd,
        i.qty_t AS cif_qty_t,
        CASE WHEN e.qty_t > 0 AND e.value_usd > 0
             THEN e.value_usd / e.qty_t END AS fob_price,
        CASE WHEN i.qty_t > 0 AND i.value_usd > 0
             THEN i.value_usd / i.qty_t END AS cif_price,
        CASE
          WHEN COALESCE(e.partner_is_aggregate, false)
            OR COALESCE(i.partner_is_aggregate, false) THEN 'AGGREGATE_PARTNER'
          WHEN e.reporter_iso3 IS NULL THEN 'MISSING_EXPORT'
          WHEN i.reporter_iso3 IS NULL THEN 'MISSING_IMPORT'
          WHEN e.qty_t IS NULL OR i.qty_t IS NULL
            OR e.qty_t <= 0 OR i.qty_t <= 0
            OR e.value_usd <= 0 OR i.value_usd <= 0 THEN 'INVALID_PRICE'
          ELSE 'MATCHED'
        END AS match_status
    FROM exports e
    FULL OUTER JOIN imports i
      ON e.year_month = i.year_month
     AND e.exporter_iso3 = i.exporter_iso3
     AND e.importer_iso3 = i.importer_iso3
     AND e.hs4 = i.hs4
), evaluated AS (
    SELECT *,
      CASE WHEN match_status = 'MATCHED'
           THEN 100.0 * (cif_price / fob_price - 1.0) END AS gap_pct,
      CASE
        WHEN match_status <> 'MATCHED' THEN NULL
        WHEN cif_price < fob_price THEN 'CIF_BELOW_FOB'
        WHEN cif_price > 1.35 * fob_price THEN 'GAP_ABOVE_35'
        ELSE NULL
      END AS anomaly_reason
    FROM paired
)
SELECT
  to_hex(md5(to_utf8(concat(year_month, '|', exporter_iso3, '|', importer_iso3, '|', hs4)))) AS anomaly_id,
  year_month, exporter_iso3, importer_iso3, hs4, product,
  fob_value_usd, fob_qty_t, cif_value_usd, cif_qty_t,
  fob_price, cif_price, gap_pct, match_status, anomaly_reason,
  anomaly_reason IS NOT NULL AS is_anomaly
FROM evaluated;
