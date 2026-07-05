# OLX Scraper (for real estate)

Builds a **snapshot dataset of OLX Brazil real-estate listings** — one row per
ad (`uid`). A local box scrapes catalogue pages and stages results; AWS (S3 +
Athena, provisioned by Terraform) owns durable storage and analytical query.

Ads are not tracked over time — no price history, no "still online" checks.
`uid` is the sole key; one row per listing, first-seen wins
([ADR 0006](docs/adr/0006-snapshot-listings-drop-fingerprint.md)).

---

## Project structure

```
.
├── run_patchright.py     # ACTIVE entrypoint — plain patchright, no Scrapy
├── run_scrapy.py         # LEGACY entrypoint — Scrapy + scrapy-playwright (being phased out)
├── run_config.yaml       # shared config for both entrypoints (gitignored; copy from the example)
├── run_config_example.yaml
│
├── olx_patchright/       # ACTIVE scraper package
│   ├── browser.py        #   persistent-profile launch + SingletonLock preflight
│   ├── parse.py          #   OLX App Router flight-payload parsing
│   ├── db.py             #   SQLite dedupe/insert (same catalog_data table as legacy)
│   ├── export.py         #   bronze .jsonl.gz + run manifest (same runs/ layout as legacy)
│   ├── catalog.py        #   the scrape loop
│   └── warm.py           #   interactive Cloudflare warm-up
│
├── olx_scrapy/           # LEGACY Scrapy project (catalog + ad spiders, SQLAlchemy models)
│
├── scripts/
│   ├── pipeline.py       # cloud pipeline: bronze upload, silver/gold Athena transforms, verify
│   └── warm_profile.py   # warm-up helper used by the legacy Scrapy entrypoint
│
├── infra/
│   ├── terraform/        # all AWS resources (bucket, Glue tables, Athena workgroup, IAM)
│   ├── silver/           # silver INSERT template (read by scripts/pipeline.py)
│   └── gold/             # gold rebuild INSERT (read by scripts/pipeline.py)
│
├── runs/                 # local bronze staging (gitignored) — spider=catalog/dt=.../region=.../*.jsonl.gz
├── scraped_data/         # SQLite database (olx.sqlite)
└── docs/                 # ADRs, runbooks, glossary (docs/CONTEXT.md)
```

---

## Setup

```bash
uv sync
cp run_config_example.yaml run_config.yaml   # then edit start_urls
```

AWS commands (`scripts/pipeline.py`) need credentials for the `olx-scraper` and
`olx-pipeline` profiles in `~/.aws/credentials` — see
[`infra/README.md`](infra/README.md).

---

## Scraping the catalogue

`run_config.yaml` drives both entrypoints; only `mode` and
`catalog_spider.start_urls` matter for the catalogue stage.

```yaml
mode: 'CATALOG'          # or 'WARM'
catalog_spider:
  start_urls:
    - "https://www.olx.com.br/imoveis/aluguel/estado-es/norte-do-espirito-santo/vitoria/republica"
```

**First run (or whenever Cloudflare blocks you):** warm the persistent browser
profile by hand.

```bash
uv run python run_patchright.py   # with mode: 'WARM'
```

A headful browser opens on the catalogue URL. Solve any Cloudflare challenge,
wait for real listings to render, then press Enter in the terminal to save the
profile (`.playwright_profile/`, gitignored) and close the browser.

**Then scrape:**

```bash
uv run python run_patchright.py   # with mode: 'CATALOG'
```

This is the **active, recommended** path (see [ADR 0001](docs/adr/0001-scraper-on-home-box-not-aws.md)
for why scraping happens on the home box rather than AWS). It prints a run
manifest:

```json
{
  "run_id": "20260705T103412",
  "region": "estado-es_norte-do-espirito-santo_vitoria_republica",
  "dt": "2026-07-05",
  "total_seen": 5,
  "new_items": 5,
  "duplicate_items": 0,
  "duration_s": 7.3
}
```

`new_items` are appended to `scraped_data/olx.sqlite` (table `catalog_data`)
and written to `runs/spider=catalog/dt=<dt>/region=<slug>/<run_id>.jsonl.gz`
(bronze staging for the cloud pipeline below). Already-seen `uid`s are dropped
— re-running the same URL is always safe.

### Legacy Scrapy path

`run_scrapy.py` (package `olx_scrapy/`) is the original Scrapy +
scrapy-playwright implementation, kept only until fully retired. It reads the
same `run_config.yaml` and additionally supports `mode: 'AD'` (per-ad detail
scraping — not yet ported to `run_patchright.py`):

```bash
uv run python run_scrapy.py       # mode: 'CATALOG', 'AD', or 'WARM'
```

Do not run both entrypoints against the persistent profile at the same time —
each holds `.playwright_profile/` exclusively (Chromium `SingletonLock`).

---

## Cloud pipeline

Once bronze files exist in `runs/`, push them to S3 and rebuild the
Athena-queryable layers with `scripts/pipeline.py` — see
[`docs/runbooks/catalog-transforms.md`](docs/runbooks/catalog-transforms.md)
for the full runbook (layer model, gotchas, verification queries).

```bash
uv run python scripts/pipeline.py upload                 # bronze: runs/ -> s3://.../raw/
uv run python scripts/pipeline.py silver --dt 2026-07-05  # rebuild one day of silver
uv run python scripts/pipeline.py gold                    # full-rebuild gold (fact_listings)
uv run python scripts/pipeline.py verify                  # smoke query + typing + grain checks
```

`--dry-run` on any command prints what would happen without touching AWS.

---

## Infrastructure

All AWS resources (S3 bucket, Glue tables, Athena workgroup, IAM users/roles,
budget) are defined in `infra/terraform/` and applied with standard
`terraform plan` / `terraform apply`. See [`infra/README.md`](infra/README.md)
for the full workflow, IAM principal list, and credential rotation.

---

## Viewing local data

Open `scraped_data/olx.sqlite` with any SQLite browser (e.g.
[DB Browser for SQLite](https://sqlitebrowser.org/)) to inspect
`catalog_data` directly, or query the cloud copy via Athena
(`olx_data.raw_catalog` / `silver_catalog_events` / `fact_listings`).

## Next steps

- Retire `run_scrapy.py` / `olx_scrapy/` once `run_patchright.py` covers the ad stage too
- Port ad-detail scraping to `olx_patchright/`
- Build `dim_location` gold dimension
