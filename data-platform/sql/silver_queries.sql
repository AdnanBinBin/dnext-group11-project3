-- =============================================================================
--  Couche Silver - requêtes d'exploration et de contrôle qualité
--  Base Glue : dnext_trade   ·   Workgroup Athena : dnext-trade-wg
--  (périmètre : données nettoyées uniquement, aucun calcul métier)
-- =============================================================================

-- Aperçu
SELECT * FROM trade_flows LIMIT 20;

-- Volumétrie par année et par flux (filtrer sur year réduit les données lues)
SELECT year, flow, count(*) AS rows
FROM trade_flows
GROUP BY 1, 2
ORDER BY 1, 2;

-- Déclarations d'un exportateur pour un produit et une année
SELECT year_month, reporter_iso3, flow, partner_iso3, value_usd, qty_t, price_basis
FROM trade_flows
WHERE exporter_iso3 = 'FRA' AND hs4 = '1001' AND year = 2023
ORDER BY year_month, partner_iso3, flow;

-- Qualité : flags portés par les lignes conservées
SELECT dq_flags, count(*) AS rows
FROM trade_flows
WHERE dq_flags IS NOT NULL
GROUP BY 1
ORDER BY 2 DESC;

-- Qualité : partenaires renommés et zones agrégées
SELECT partner_raw, partner_iso3, partner_is_aggregate, count(*) AS rows
FROM trade_flows
WHERE partner_raw <> partner_iso3 OR partner_is_aggregate
GROUP BY 1, 2, 3;

-- Quarantaine : lignes rejetées et motif
SELECT reject_reason, count(*) AS rows
FROM trade_flows_rejected
GROUP BY 1;

-- Traçabilité : retrouver une ligne dans le fichier source
SELECT source_file, source_row, ingestion_id, ingested_at
FROM trade_flows
WHERE reporter_iso3 = 'FRA' AND partner_iso3 = 'EGY' AND hs4 = '1001' AND year_month = '2023-05';
