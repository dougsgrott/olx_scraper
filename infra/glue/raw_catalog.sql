-- CREATE EXTERNAL TABLE for the raw catalog bronze layer.
-- Run once manually in the Athena console using the olx_data workgroup.
-- Replace BUCKET_NAME with the actual bucket name before running
-- (e.g. olx-data-123456789012).
--
-- Complex fields (pricing, details, characteristics, badges) are stored as
-- STRING. Silver transforms are responsible for casting and parsing them.

CREATE EXTERNAL TABLE IF NOT EXISTS olx_data.raw_catalog (
    uid                       STRING,
    code                      STRING,
    title                     STRING,
    url                       STRING,
    date                      STRING,
    location                  STRING,
    neighbourhood             STRING,
    municipality              STRING,
    uf                        STRING,
    ddd                       STRING,
    pricing                   STRING,
    old_price                 STRING,
    details                   STRING,
    badges                    STRING,
    characteristics           STRING,
    real_estate_type          STRING,
    condominio                STRING,
    iptu                      STRING,
    size                      STRING,
    rooms                     STRING,
    bathrooms                 STRING,
    garage_spaces             STRING,
    category_name             STRING,
    professional_ad           INT,
    is_featured               INT,
    fixed_on_top              INT,
    price_reduction_badge     INT,
    has_real_estate_highlight INT,
    url_is_scraped            INT,
    url_scraped_date          STRING,
    scraped_date              STRING,
    uploaded_to_cloud         INT,
    watched_state_fingerprint STRING
)
PARTITIONED BY (
    dt     STRING,
    region STRING
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
WITH SERDEPROPERTIES (
    'ignore.malformed.json' = 'true'
)
STORED AS INPUTFORMAT
    'org.apache.hadoop.mapred.TextInputFormat'
OUTPUTFORMAT
    'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://BUCKET_NAME/raw/spider=catalog/'
TBLPROPERTIES (
    'has_encrypted_data'                  = 'false',
    'projection.enabled'                  = 'true',
    'projection.dt.type'                  = 'date',
    'projection.dt.format'                = 'yyyy-MM-dd',
    'projection.dt.range'                 = '2026-01-01,NOW',
    'projection.dt.interval'              = '1',
    'projection.dt.interval.unit'         = 'DAYS',
    'projection.region.type'              = 'injected',
    'storage.location.template'           = 's3://BUCKET_NAME/raw/spider=catalog/dt=${dt}/region=${region}/'
);
