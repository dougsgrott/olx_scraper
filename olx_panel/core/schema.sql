CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE manifest (
    region TEXT NOT NULL,
    run_id TEXT NOT NULL,
    dt TEXT,
    started_at TEXT,
    finished_at TEXT,
    duration_s REAL,
    pages_scraped INTEGER,
    total_seen INTEGER,
    new_items INTEGER,
    duplicate_items INTEGER,
    stop_reason TEXT,
    schema_version INTEGER,
    start_url TEXT,
    scope TEXT,
    start_page INTEGER,
    invocation_id TEXT,
    early_stop TEXT,
    total_of_ads INTEGER,
    listing_url TEXT,
    sort TEXT,
    breadcrumb TEXT,
    new_per_page TEXT,
    store TEXT,
    file_path TEXT,
    file_missing INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT,
    PRIMARY KEY (region, run_id)
);

CREATE INDEX ix_manifest_started ON manifest (started_at);

CREATE TABLE ad_geo_agg (
    uf TEXT NOT NULL,
    ddd TEXT NOT NULL,
    macro TEXT NOT NULL,
    municipality TEXT NOT NULL,
    neighbourhood TEXT NOT NULL,
    n INTEGER NOT NULL,
    first_seen TEXT,
    last_seen TEXT,
    PRIMARY KEY (uf, ddd, macro, municipality, neighbourhood)
);
