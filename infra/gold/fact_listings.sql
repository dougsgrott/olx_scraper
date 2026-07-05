INSERT INTO olx_data.fact_listings
SELECT
    uid,
    price_brl,
    old_price_brl,
    condominio_brl,
    iptu_brl,
    size_m2,
    CASE WHEN size_m2 > 0 THEN CAST(price_brl / size_m2 AS DECIMAL(12,2)) END AS price_per_m2,
    rooms,
    bathrooms,
    garage_spaces,
    real_estate_type,
    category_name,
    title,
    url,
    region,
    neighbourhood,
    municipality,
    uf,
    first_seen_at
FROM (
    SELECT
        s.*,
        MIN(scraped_ts) OVER (PARTITION BY uid) AS first_seen_at,
        ROW_NUMBER()    OVER (PARTITION BY uid ORDER BY scraped_ts DESC) AS rn
    FROM olx_data.silver_catalog_events s
) t
WHERE rn = 1;
