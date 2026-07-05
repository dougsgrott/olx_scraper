INSERT INTO olx_data.silver_catalog_events
SELECT
    uid,
    code,
    title,
    url,
    TRY(date_parse(date, '%Y-%m-%dT%H:%i:%s')) AS listing_ts,
    COALESCE(
        TRY(date_parse(scraped_date, '%Y-%m-%d %H:%i:%s.%f')),
        TRY(date_parse(scraped_date, '%Y-%m-%d %H:%i:%s'))
    ) AS scraped_ts,
    TRY_CAST(replace(regexp_replace(json_extract_scalar(pricing, '$.price'), '[^0-9,]', ''), ',', '.') AS DECIMAL(12,2)) AS price_brl,
    TRY_CAST(replace(regexp_replace(old_price,  '[^0-9,]', ''), ',', '.') AS DECIMAL(12,2)) AS old_price_brl,
    TRY_CAST(replace(regexp_replace(condominio, '[^0-9,]', ''), ',', '.') AS DECIMAL(12,2)) AS condominio_brl,
    TRY_CAST(replace(regexp_replace(iptu,       '[^0-9,]', ''), ',', '.') AS DECIMAL(12,2)) AS iptu_brl,
    TRY_CAST(replace(regexp_replace(size,       '[^0-9,]', ''), ',', '.') AS DECIMAL(10,2)) AS size_m2,
    TRY_CAST(regexp_extract(rooms,         '[0-9]+') AS INT) AS rooms,
    TRY_CAST(regexp_extract(bathrooms,     '[0-9]+') AS INT) AS bathrooms,
    TRY_CAST(regexp_extract(garage_spaces, '[0-9]+') AS INT) AS garage_spaces,
    real_estate_type,
    category_name,
    location,
    neighbourhood,
    municipality,
    uf,
    ddd,
    region,
    professional_ad,
    is_featured,
    price_reduction_badge,
    has_real_estate_highlight,
    dt
FROM olx_data.raw_catalog
WHERE dt = '__DT__' AND region = '__REGION__';
