-- Vérification Gold après ingestion. Exécuter séparément chaque instruction.
SELECT match_status, COUNT(*) AS n
FROM dnext_trade.mirror_matches
GROUP BY match_status ORDER BY n DESC;

SELECT COUNT(*) AS gold_rows,
       COUNT_IF(is_anomaly) AS anomalies,
       COUNT(DISTINCT anomaly_id) AS unique_ids
FROM dnext_trade.mirror_matches;

SELECT year_month, exporter_iso3, importer_iso3, hs4,
       ROUND(fob_price,2) AS fob_price, ROUND(cif_price,2) AS cif_price,
       ROUND(gap_pct,2) AS gap_pct, is_anomaly
FROM dnext_trade.mirror_matches
WHERE year_month='2023-01' AND exporter_iso3='ARG'
  AND importer_iso3='BRA' AND hs4='1001';
