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
│   ├── browser.py        #   persistent-profile launch + single-browser lock (.browser.lock)
│   ├── parse.py          #   OLX App Router flight-payload parsing
│   ├── db.py             #   SQLite dedupe/insert (same catalog_data table as legacy; WAL mode)
│   ├── export.py         #   bronze .jsonl.gz + run manifest (same runs/ layout as legacy)
│   ├── catalog.py        #   the scrape loop (early stop, page cap, cooperative cancel)
│   ├── plan.py           #   location-tree planning: node status, start-URL plans, URL/region mapping
│   └── warm.py           #   Cloudflare warm-up (terminal or signal-driven)
│
├── olx_scrapy/           # LEGACY Scrapy project (catalog + ad spiders, SQLAlchemy models)
│
├── olx_panel/            # local web control panel (python -m olx_panel) — see docs/CONTROL_PANEL.md
│   ├── core/             #   manifests index, ad aggregate, location tree, coverage metrics (no web imports)
│   ├── web/              #   FastAPI routes per page (Coverage, Runs)
│   ├── templates/ static/#   Jinja2 + htmx (vendored, pinned; no npm)
│   └── overrides.yaml    #   hand-kept corrections (legacy resume runs, off-tree regions)
│
├── scripts/
│   ├── pipeline.py            # cloud pipeline: bronze upload, silver/gold Athena transforms, verify
│   ├── discover_locations.py  # harvest OLX's location tree from its sitemaps -> locations.sqlite
│   ├── probe_density.py       # per-node ad counts (totalOfAds) over the tree; --report prints it
│   ├── generate_start_urls.py # start-URL plan (leaf/dense/remainder) from the probed counts
│   ├── measure_page_cap.py    # one-off: how many catalogue pages OLX serves per scope
│   └── warm_profile.py        # warm-up helper used by the legacy Scrapy entrypoint
│
├── tests/                # pytest suite (uv run pytest); browser-free fakes in tests/fakes.py
│
├── infra/
│   ├── terraform/        # all AWS resources (bucket, Glue tables, Athena workgroup, IAM)
│   ├── silver/           # silver INSERT template (read by scripts/pipeline.py)
│   └── gold/             # gold rebuild INSERT (read by scripts/pipeline.py)
│
├── runs/                 # bronze outbox (gitignored) — spider=catalog/dt=.../region=.../*.jsonl.gz
├── runs_uploaded/        # uploaded-runs archive (gitignored) — pipeline.py upload moves files here
├── scraped_data/         # SQLite: olx.sqlite (dataset), locations.sqlite (location tree), panel.sqlite (panel cache)
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
  start_urls:            # multiple URLs are scraped sequentially, one browser
    - "https://www.olx.com.br/imoveis/aluguel/estado-es/norte-do-espirito-santo/vitoria?sf=1"
    - "https://www.olx.com.br/imoveis/aluguel/estado-es/norte-do-espirito-santo/vila-velha?sf=1"
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

Another config file can be passed with `--config path/to/config.yaml` (the
control panel will generate one per job). Exit codes: `0` ran (per-URL
outcomes are in the manifests), `2` configuration error, `75` browser busy,
`130` cancelled.

This is the **active, recommended** path (see [ADR 0001](docs/adr/0001-scraper-on-home-box-not-aws.md)
for why scraping happens on the home box rather than AWS). Start URLs are
scraped **sequentially through one browser**, each getting its own bronze
`region=` partition and its own manifest (region is inferred from each URL's
path — any level works: state, region, city, neighbourhood). Note that
`region` records **scrape provenance, not listing geography**: with
overlapping scopes (a city URL plus its whole-state URL), a listing belongs
to whichever scope saw it first — use the `municipality`/`uf` columns for
geographic analysis. It prints the manifests as a list:

```json
[
  {
    "run_id": "20260706T220156",
    "region": "estado-es_norte-do-espirito-santo_vitoria",
    "dt": "2026-07-06",
    "total_seen": 100,
    "new_items": 25,
    "duplicate_items": 75,
    "pages_scraped": 2,
    "stop_reason": ["early_stop"],
    "duration_s": 9.2
  }
]
```

Since manifest schema v2 each one also carries `schema_version`, the exact
`start_url` (with any `?o=` resume offset, also parsed into `start_page`),
`scope` (e.g. `imoveis/aluguel`), `invocation_id` (shared by all URLs of one
run; `OLX_INVOCATION_ID` if set), `started_at`/`finished_at` with UTC offset,
the applied `early_stop`, OLX's own `total_of_ads` / `listing_url` / `sort` /
location `breadcrumb` from the first page, and `new_per_page`.

`new_items` are appended to `scraped_data/olx.sqlite` (table `catalog_data`)
and written to `runs/spider=catalog/dt=<dt>/region=<slug>/<run_id>.jsonl.gz`
(bronze staging for the cloud pipeline below). Already-seen `uid`s are dropped
— re-running the same URL is always safe. Bronze files and manifests are
written under a `.tmp` name and renamed when complete, so `pipeline.py upload`
(which skips `.tmp`) never ships a partition that is still being written.

**One browser at a time.** Every browser user (scrape, WARM, prober, sitemap
harvest, page-cap probe, and the legacy entrypoint) takes `.browser.lock`
first and keeps it until it exits. A second one exits with code `75` and says
who holds the lock, instead of killing the first one's browser mid-scrape.
Leftover chromium from a crashed run is still cleaned up — only by the lock
holder.

**Stopping a run.** Ctrl-C (SIGINT) or SIGTERM finishes the current page,
records that start URL as `cancelled` and starts no further URL (no manifest
for those); the run exits `130`. A second Ctrl-C stops at once (the current
URL's manifest is still written).

### Early stopping

OLX is an open marketplace — stale ads (already-rented units) stay online
indefinitely, so on repeat runs most of a region's pages are ads you've already
seen. Since duplicates carry no information under the snapshot model, an
optional `early_stop` block cuts pagination short:

```yaml
catalog_spider:
  start_urls: [ ... ]
  early_stop:
    patience: 2           # stop after this many consecutive stale pages (0 = off)
    min_new_per_page: 1   # a page is stale when it yields fewer new items than this
    max_pages: 0          # hard cap on pages per start URL (0 = unlimited)
```

The patience rule and the hard cap are independent; omit the block (or set a
knob to 0) to disable. The stale-page counter resets whenever a page yields
enough new items, so featured/pinned ads reshuffled into every page don't
trigger false stops. Why the run ended is recorded per start URL in the
manifest's `stop_reason`:

| `stop_reason` | meaning | scope fully walked? |
|---|---|---|
| `exhausted` | reached the last page | yes |
| `early_stop` | patience rule fired (caught up with already-seen ads) | caught up |
| `max_pages` | hit the configured `max_pages` | no |
| `page_cap` | OLX serves no further pages (100; past it, it re-serves page 100) — the scope is denser than one URL can reach | no |
| `no_payload` | page had no ads payload (403 ⇒ Cloudflare, re-WARM) | no |
| `nav_timeout` | navigation kept timing out | no |
| `crashed` | unexpected error on this URL; the run moved on | no |
| `cancelled` | stopped by SIGINT/SIGTERM | no |

`page_cap` also guarantees termination: without early stop, a scope over the
cap used to request `?o=101` forever. Runs from before 2026-10 that hit the cap
show up as `early_stop` with 104 pages (100 real + 4 re-served).

Early stopping works best with newest-first results — append `?sf=t` to a
start URL (OLX's "Mais recentes" sort, verified) so new ads front-load and
the first stale page really means the rest is old. Under the default
relevance ordering, prefer a higher `patience`.

### Legacy Scrapy path

`run_scrapy.py` (package `olx_scrapy/`) is the original Scrapy +
scrapy-playwright implementation, kept only until fully retired. It reads the
same `run_config.yaml` and additionally supports `mode: 'AD'` (per-ad detail
scraping — not yet ported to `run_patchright.py`):

```bash
uv run python run_scrapy.py       # mode: 'CATALOG', 'AD', or 'WARM'
```

Both entrypoints take the same `.browser.lock`, so a second one exits `75`
instead of fighting over `.playwright_profile/`.

---

## Cloud pipeline

Once bronze files exist in `runs/`, push them to S3 and rebuild the
Athena-queryable layers with `scripts/pipeline.py` — see
[`docs/runbooks/catalog-transforms.md`](docs/runbooks/catalog-transforms.md)
for the full runbook (layer model, gotchas, verification queries).

```bash
uv run python scripts/pipeline.py upload   # bronze: runs/ outbox -> s3://.../raw/, then archive
uv run python scripts/pipeline.py silver   # rebuild newest local dt (or: --dt 2026-07-05)
uv run python scripts/pipeline.py gold     # full-rebuild gold (fact_listings)
uv run python scripts/pipeline.py verify   # smoke query + typing + grain checks
uv run python scripts/pipeline.py export   # silver+gold -> exports/ (parquet; --format csv)
```

For EDA, `export` pulls both layers into `exports/` (gitignored):
`pd.read_parquet("exports/fact_listings")` or, with `--format csv`,
`pd.read_csv("exports/fact_listings.csv")`. Note: the parquet `DECIMAL`
columns (`price_brl`, `size_m2`, `price_per_m2`, …) arrive as exact
`Decimal` objects — cast with `.astype(float)` before numeric summaries.

**Data characteristics** (2026-07-05 snapshot, 3,565 listings, 6 ES regions):
`price_brl` is heavily right-skewed — min R$30, median R$2,804, mean R$18,790,
max R$2,100,003 — and `fact_listings` is not filtered by listing type or price
plausibility. Extreme values include atypical listings and likely data-entry
errors. For aggregate statistics, filter first (e.g. by `real_estate_type` /
`category_name` and a price range) or use robust measures (median, IQR).
`price_brl` is NULL on a small fraction of rows (7 of 3,565 in this snapshot).

`runs/` is an **outbox**: `upload` moves each shipped file to `runs_uploaded/`
(same tree), so re-running uploads nothing twice and `runs/` never grows.
`silver` derives `--dt` from the newest local manifest when omitted — pass it
explicitly only for backfills. `--dry-run` on any command prints what would
happen without touching AWS.

---

## Infrastructure

All AWS resources (S3 bucket, Glue tables, Athena workgroup, IAM users/roles,
budget) are defined in `infra/terraform/` and applied with standard
`terraform plan` / `terraform apply`. See [`infra/README.md`](infra/README.md)
for the full workflow, IAM principal list, and credential rotation.

---

## Control panel

A localhost web app over the scraper's own files: per OLX location node
(state → DDD area → sub-region/city → neighbourhood) how much was collected,
OLX's live inventory, when it was last scraped and whether that run counts.

```bash
uv run python -m olx_panel              # http://localhost:8765 (also from the Windows browser)
uv run python -m olx_panel reindex      # rebuild scraped_data/panel.sqlite (panel stopped)
```

- **Coverage** — the location tree (lazy, expand per node) with *Collected*
  (Σ manifest `new_items`: ads first collected via that scope and below),
  *Located* (ads whose own `uf`/macro-region is there), *OLX ads* (prober
  count), *Last run*, *Fresh as of* (latest run that walked to the end or
  caught up — on the node, an alias twin, or any ancestor), *Fresh* (share of
  the node's plan-leaf inventory fresh within N days) and *Baseline* (ever
  walked completely). Filters switch to a flat list. Click a node for its run
  history.
- **Runs** — every invocation found in the manifests (CLI, scheduled, later
  the panel's own), with per-URL outcomes, plus a digest of `run_config.yaml`
  that warns about `?o=` resume offsets, off-tree URLs and alias pairs.

Run outcomes: `complete` (exhausted) and `caught up` (early stop before the
page cap) earn freshness; `capped`, `partial`, `blocked`, `failed`,
`cancelled`, `unknown`, `resume` (started past page 1) and `scope mismatch`
(OLX served another scope, or an off-tree legacy region) do not. Runs the
manifests can't classify correctly (legacy resumes) are flagged in
`olx_panel/overrides.yaml`.

The panel only reads `olx.sqlite`, `locations.sqlite`, the manifests and
`run_config.yaml`; its own state is `scraped_data/panel.sqlite`, disposable
(rebuilt from those files). It binds 127.0.0.1 only, rejects foreign
`Host`/`Origin` headers, and refuses to start twice (`panel.lock`). Run it
with one worker and no `--reload`.

## Viewing local data

Open `scraped_data/olx.sqlite` with any SQLite browser (e.g.
[DB Browser for SQLite](https://sqlitebrowser.org/)) to inspect
`catalog_data` directly, or query the cloud copy via Athena
(`olx_data.raw_catalog` / `silver_catalog_events` / `fact_listings`).

Both SQLite files run in **WAL mode** (set by `olx_patchright/db.py` on first
use), so readers never block a running scrape; expect `-wal`/`-shm` files next
to them. WAL needs shared memory, so open the files from Linux/WSLg tools, not
from Windows through `\\wsl$` (copy the file first, or use the SQLite backup
API — a plain copy of a live WAL database can miss recent commits).

## Tests

```bash
uv sync                  # includes the dev group (pytest)
uv run pytest
```

The suite runs the real scrape loop, exporter, browser lock and signal
handling against an in-process fake of patchright and OLX (`tests/fakes.py`),
so it needs no browser or network.

## Next steps

- Retire `run_scrapy.py` / `olx_scrapy/` once `run_patchright.py` covers the ad stage too
- Port ad-detail scraping to `olx_patchright/`
- Build `dim_location` gold dimension
