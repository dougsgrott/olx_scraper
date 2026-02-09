# OLX Scraper (Real Estate)

A production-grade Scrapy-based web scraper designed to extract real estate listing data from OLX Brazil. The project implements a sophisticated two-stage scraping architecture with anti-bot protection, persistent storage, and cloud integration capabilities.

## Table of Contents

- [Project Overview](#project-overview)
- [Architecture Overview](#architecture-overview)
- [Key Components](#key-components)
- [Data Flow](#data-flow)
- [Database Schema](#database-schema)
- [Project Structure](#project-structure)
- [Features](#features)
- [Setup and Installation](#setup-and-installation)
- [Configuration](#configuration)
- [How to Run](#how-to-run)
- [Viewing the Data](#viewing-the-data)
- [Future Enhancements](#future-enhancements)

---

## Project Overview

The OLX Scraper is designed to build a comprehensive database of real estate listings from OLX Brazil through a two-stage process:

1. **Catalog Stage**: Discovers and collects listing URLs from search/catalog pages
2. **Ad Stage**: Extracts detailed information from individual ad pages

The project is currently optimized for real estate listings but designed with extensibility in mind for future generalization to other categories.

### Technology Stack

- **Framework**: Scrapy 2.12.0
- **Language**: Python 3.x
- **Database**: SQLite with SQLAlchemy ORM
- **Anti-Bot**: Cloudscraper (Cloudflare bypass)
- **HTML Parsing**: BeautifulSoup4
- **Async Runtime**: Twisted

---

## Architecture Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Run Configuration                        │
│                        (run_config.yaml)                         │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
                    ┌────────────────┐
                    │    run.py      │
                    │  Entry Point   │
                    └────────┬───────┘
                             │
                ┌────────────┴────────────┐
                │                         │
                ▼                         ▼
    ┌───────────────────┐     ┌──────────────────┐
    │  Catalog Spider   │     │    Ad Spider     │
    │  (Stage 1)        │     │   (Stage 2)      │
    └─────────┬─────────┘     └────────┬─────────┘
              │                        │
              │                        │
              ▼                        ▼
    ┌─────────────────────────────────────────────┐
    │     CloudScraper Middleware                 │
    │     (Cloudflare Bypass)                     │
    └─────────────────┬───────────────────────────┘
                      │
                      ▼
    ┌─────────────────────────────────────────────┐
    │           Item Processing                   │
    │  (Items → Item Loaders → Processors)        │
    └─────────────────┬───────────────────────────┘
                      │
                      ▼
    ┌─────────────────────────────────────────────┐
    │          Pipeline Processing                │
    │  • Duplicate Detection                      │
    │  • Default Values                           │
    │  • Data Normalization                       │
    │  • Database Storage                         │
    │  • Status Updates                           │
    └─────────────────┬───────────────────────────┘
                      │
                      ▼
    ┌─────────────────────────────────────────────┐
    │        SQLite Database                      │
    │   • Catalog Tables (3)                      │
    │   • Ad Tables (4)                           │
    │   • Cloud Upload Status                     │
    └─────────────────────────────────────────────┘
```

### Architecture Principles

1. **Separation of Concerns**: Catalog discovery and ad scraping are handled by separate spiders
2. **Modularity**: Items, pipelines, and models are cleanly separated
3. **Persistence**: All data stored in SQLite with cloud sync capabilities
4. **Resilience**: Duplicate detection, error handling, and incremental scraping
5. **Stealth**: CloudScraper middleware bypasses anti-bot measures

---

## Key Components

### 1. Spiders

#### CatalogSpider ([catalog_spider.py](olx_scraper/spiders/catalog_spider.py))

**Purpose**: Discovers and catalogs ad URLs from listing pages

**Key Features**:
- Pagination handling with automatic page calculation
- Unique ID generation using MD5 hashing
- Custom settings for rate limiting (3s delay, autothrottle)
- CloudScraper integration for bypassing Cloudflare

**Workflow**:
1. Starts with configured catalog URLs
2. Extracts ad cards from listing pages
3. Generates unique identifier (UID) for each listing
4. Extracts summary data (title, location, price, badges)
5. Automatically follows pagination links
6. Stores data via pipelines

**Custom Settings**:
- Download delay: 3 seconds
- Autothrottle: Enabled
- Logs: `logs/olx_catalog.log`
- Pipelines: Duplicate detection → Default values → Save to DB

#### AdSpider ([ad_spider.py](olx_scraper/spiders/ad_spider.py))

**Purpose**: Extracts detailed information from individual ad pages

**Key Features**:
- Database-driven URL fetching (scrapes unscraped URLs automatically)
- Manual URL mode for testing specific ads
- Advanced characteristic extraction with modal exclusion
- Breadcrumb navigation extraction

**Workflow**:
1. Fetches unscraped URLs from database (or uses provided URLs)
2. Visits each ad page
3. Extracts detailed data using XPath selectors
4. Parses characteristics sections (avoiding duplicate modals)
5. Stores comprehensive ad data
6. Updates catalog table to mark URL as scraped

**Custom Settings**:
- Download delay: 3 seconds
- Autothrottle: Enabled with debug
- Logs: `logs/olx_ad.log`
- Pipelines: Default values → Save ad info → Update catalog status

### 2. Items ([items.py](olx_scraper/items.py))

Defines the data structures and processing logic for scraped data.

#### CatalogItem

**Fields**:
- `uid`: Unique identifier (MD5 hash)
- `title`: Ad title
- `details`: Dictionary of ad details (bedrooms, bathrooms, etc.)
- `badges`: List of badges (e.g., "Destaque", "Urgente")
- `pricing`: Price information (price, IPTU, condomínio)
- `location`: Geographic location
- `date`: Listing date
- `code`: OLX ad code
- `url`: Ad URL
- `url_is_scraped`: Scraping status flag
- `scraped_date`: When catalog was scraped
- `url_scraped_date`: When ad details were scraped
- `uploaded_to_cloud`: Cloud sync status

**Processors**: BeautifulSoup-based parsers for each field

#### AdItem

**Fields**:
- `uid`: Links to catalog entry
- `title`: Full ad title
- `description`: Complete ad description
- `pricing`: Detailed pricing (original, current, listing type)
- `street_address`: Street-level address
- `full_location`: Complete location string
- `details`: Key-value details (e.g., area, floors)
- `characteristics`: Property characteristics organized by category
- `date`: Posting/update date
- `breadcrumb`: Navigation breadcrumb trail
- `code`: OLX ad code
- `url`: Ad URL
- `scraped_date`: Timestamp
- `uploaded_to_cloud`: Cloud sync status

**Processors**: Custom parsers for structured data extraction

### 3. Pipelines ([pipelines.py](olx_scraper/pipelines.py))

Handles data processing and storage with a modular pipeline architecture.

#### Catalog Pipelines

1. **DuplicatesCatalogPipeline** (Priority: 100)
   - Checks for duplicate UIDs in database
   - Drops duplicates to prevent reprocessing
   - Tracks redundancy metrics

2. **DefaultValuesCatalogPipeline** (Priority: 110)
   - Sets default values for missing fields
   - Ensures data integrity

3. **SaveCatalogInfoPipeline** (Priority: 200)
   - Saves main catalog record to `catalog_info` table

4. **SaveCatalogDetailsPipeline** (Priority: 210) *[Currently disabled]*
   - Normalizes details into key-value pairs
   - Stores in `catalog_details` table

5. **SaveCatalogPricingPipeline** (Priority: 220) *[Currently disabled]*
   - Normalizes pricing data into key-value pairs
   - Stores in `catalog_pricing` table

6. **SaveCatalogBadgesPipeline** (Priority: 230) *[Currently disabled]*
   - Stores badges as separate records
   - Enables badge-based queries

#### Ad Pipelines

1. **DefaultValuesAdPipeline** (Priority: 110)
   - Sets default values for ad fields

2. **SaveAdInfoPipeline** (Priority: 200)
   - Saves main ad data to `ad_info` table

3. **SaveAdCharacteristicsPipeline** (Priority: 210) *[Currently disabled]*
   - Normalizes characteristics into key-value pairs
   - Stores in `ad_characteristics` table

4. **SaveAdDetailsPipeline** (Priority: 220) *[Currently disabled]*
   - Normalizes details into key-value pairs
   - Stores in `ad_details` table

5. **SaveAdPricingPipeline** (Priority: 220) *[Currently disabled]*
   - Normalizes pricing into key-value pairs
   - Stores in `ad_pricing` table

6. **UpdateCatalogDatabasePipeline** (Priority: 300)
   - Marks catalog URLs as scraped
   - Updates `url_is_scraped` flag and timestamp

### 4. Models ([models.py](olx_scraper/models.py))

SQLAlchemy ORM models defining the database schema.

#### Catalog Tables

1. **catalog_info**: Main catalog data (denormalized)
2. **catalog_details**: Normalized detail key-value pairs
3. **catalog_pricing**: Normalized pricing key-value pairs
4. **catalog_badges**: Badge entries

#### Ad Tables

1. **ad_info**: Main ad data (denormalized)
2. **ad_characteristics**: Normalized characteristics
3. **ad_details**: Normalized details
4. **ad_pricing**: Normalized pricing

**Key Fields Across Tables**:
- Foreign keys: `uid` (catalog) or `code` (ad)
- `uploaded_to_cloud`: Cloud sync tracking
- Timestamps: `scraped_date`, `url_scraped_date`

### 5. Middleware ([middlewares.py](olx_scraper/middlewares.py))

#### CloudScraperMiddleware

**Purpose**: Bypasses Cloudflare anti-bot protection

**How It Works**:
1. Intercepts requests with `cloudflare_bypass` meta flag
2. Uses cloudscraper library to make requests with proper browser fingerprinting
3. Handles the request in a separate thread (via Twisted reactor)
4. Returns response to Scrapy engine
5. Supports all HTTP status codes (200, 403, 404, etc.)

**Configuration**:
- Browser fingerprint: Chrome on Windows
- Custom headers for realistic requests
- Timeout: 180 seconds
- Redirect following: Enabled

---

## Data Flow

### Catalog Scraping Flow

```
1. run.py reads run_config.yaml (mode: CATALOG)
                ↓
2. CatalogSpider starts with start_urls
                ↓
3. For each page:
   a. CloudScraper fetches HTML
   b. Parse ad cards (20-50 per page)
   c. Generate unique UID for each ad
   d. Extract summary data with ItemLoaders
                ↓
4. DuplicatesCatalogPipeline checks UID
   - If duplicate: Drop item
   - If new: Continue
                ↓
5. DefaultValuesCatalogPipeline sets defaults
                ↓
6. SaveCatalogInfoPipeline stores to database
   - catalog_info table (denormalized)
   - [Optional] Normalized tables
                ↓
7. Spider paginates to next page
                ↓
8. Repeat until no more pages
```

### Ad Scraping Flow

```
1. run.py reads run_config.yaml (mode: AD)
                ↓
2. AdSpider queries database for unscraped URLs
   (url_is_scraped = 0)
                ↓
3. For each URL:
   a. CloudScraper fetches HTML
   b. Extract detailed data with ItemLoaders
   c. Parse characteristics (exclude modals)
   d. Extract location, pricing, details
                ↓
4. DefaultValuesAdPipeline sets defaults
                ↓
5. SaveAdInfoPipeline stores to database
   - ad_info table (denormalized)
   - [Optional] Normalized tables
                ↓
6. UpdateCatalogDatabasePipeline updates catalog
   - Set url_is_scraped = 1
   - Set url_scraped_date = now
                ↓
7. Repeat until all URLs scraped
```

---

## Database Schema

### Catalog Schema

```sql
-- Main catalog table (denormalized)
CREATE TABLE catalog_info (
    id INTEGER PRIMARY KEY,
    uid VARCHAR(50),              -- MD5 hash of title|location|date
    title VARCHAR(200),
    location VARCHAR(50),
    date VARCHAR(50),
    code VARCHAR(50),             -- OLX ad code
    details TEXT,                 -- JSON string
    pricing TEXT,                 -- JSON string
    badges TEXT,                  -- JSON array
    scraped_date DATETIME,
    url VARCHAR(200),
    url_is_scraped INTEGER,       -- 0 = not scraped, 1 = scraped
    url_scraped_date DATETIME,
    uploaded_to_cloud INTEGER     -- 0 = not uploaded, 1 = uploaded
);

-- Normalized detail table
CREATE TABLE catalog_details (
    id INTEGER PRIMARY KEY,
    uid VARCHAR(50),              -- Foreign key to catalog_info
    key VARCHAR(20),              -- e.g., "Quartos", "Banheiros"
    value VARCHAR(30),            -- e.g., "2", "1"
    uploaded_to_cloud INTEGER
);

-- Normalized pricing table
CREATE TABLE catalog_pricing (
    id INTEGER PRIMARY KEY,
    uid VARCHAR(50),              -- Foreign key to catalog_info
    key VARCHAR(20),              -- e.g., "price", "iptu", "condomínio"
    value VARCHAR(30),            -- e.g., "R$ 1.500", "R$ 100"
    uploaded_to_cloud INTEGER
);

-- Badge table
CREATE TABLE catalog_badges (
    id INTEGER PRIMARY KEY,
    uid VARCHAR(50),              -- Foreign key to catalog_info
    key VARCHAR(20),              -- Badge text (e.g., "Destaque")
    uploaded_to_cloud INTEGER
);
```

### Ad Schema

```sql
-- Main ad table (denormalized)
CREATE TABLE ad_info (
    id INTEGER PRIMARY KEY,
    date VARCHAR(20),
    breadcrumb VARCHAR(100),
    code VARCHAR(20),             -- OLX ad code (foreign key)
    description TEXT,
    full_location VARCHAR(60),
    street_address VARCHAR(60),
    title VARCHAR(60),
    characteristics TEXT,         -- JSON string
    details TEXT,                 -- JSON string
    pricing TEXT,                 -- JSON string
    url VARCHAR(200),
    scraped_date DATETIME,
    uploaded_to_cloud INTEGER
);

-- Normalized characteristics table
CREATE TABLE ad_characteristics (
    id INTEGER PRIMARY KEY,
    code VARCHAR(50),             -- Foreign key to ad_info
    key VARCHAR(50),              -- Characteristic name
    value VARCHAR(50),            -- Category/type
    uploaded_to_cloud INTEGER
);

-- Normalized details table
CREATE TABLE ad_details (
    id INTEGER PRIMARY KEY,
    code VARCHAR(50),             -- Foreign key to ad_info
    key VARCHAR(50),              -- Detail key
    value VARCHAR(50),            -- Detail value
    uploaded_to_cloud INTEGER
);

-- Normalized pricing table
CREATE TABLE ad_pricing (
    id INTEGER PRIMARY KEY,
    code VARCHAR(50),             -- Foreign key to ad_info
    key VARCHAR(50),              -- Pricing component
    value VARCHAR(50),            -- Price value
    uploaded_to_cloud INTEGER
);
```

### Relationships

- **Catalog → Ad**: Linked via `code` field (OLX ad code)
- **Main → Normalized Tables**: One-to-many relationships
- **Cloud Sync**: `uploaded_to_cloud` flag on all tables

---

## Project Structure

```
olx_scraper/
│
├── logs/                           # Spider execution logs
│   ├── olx_ad.log                 # Ad spider logs
│   └── olx_catalog.log            # Catalog spider logs
│
├── scraped_data/                   # Data storage
│   └── olx.sqlite                 # SQLite database (auto-created)
│
├── olx_scraper/                    # Main package
│   ├── spiders/                   # Spider definitions
│   │   ├── __init__.py
│   │   ├── catalog_spider.py      # Stage 1: Catalog scraper
│   │   └── ad_spider.py           # Stage 2: Ad detail scraper
│   │
│   ├── __init__.py
│   ├── items.py                   # Data structures and processors
│   ├── middlewares.py             # CloudScraper middleware
│   ├── models.py                  # SQLAlchemy database models
│   ├── pipelines.py               # Data processing pipelines
│   └── settings.py                # Scrapy project settings
│
├── run.py                          # Main entry point (YAML-driven)
├── run_config.yaml                 # Runtime configuration
├── run_config_example.yaml         # Configuration template
├── requirements.txt                # Python dependencies
├── scrapy.cfg                      # Scrapy project config
├── secrets.yaml                    # Secret keys (not in git)
└── readme.md                       # This file
```

---

## Features

### Core Features

✅ **Two-Stage Scraping Architecture**
- Modular separation of catalog and ad scraping
- Independent execution of each stage
- Database-driven workflow coordination

✅ **Advanced Anti-Bot Evasion**
- CloudScraper middleware for Cloudflare bypass
- Browser fingerprinting (Chrome on Windows)
- Realistic request headers and timing
- Random delays and autothrottle

✅ **Persistent Storage with SQLAlchemy**
- SQLite database for local storage
- Dual schema: Denormalized + Normalized
- Automatic table creation
- Transaction safety with rollback

✅ **Intelligent Duplicate Prevention**
- MD5-based unique ID generation
- Database-level duplicate checking
- Redundancy tracking and metrics
- Skip already-scraped URLs

✅ **Robust Data Normalization**
- BeautifulSoup HTML parsing
- Custom ItemLoader processors
- JSON serialization for complex fields
- Default value handling

✅ **Flexible URL Management**
- Database-driven automatic URL fetching
- Manual URL mode for testing
- Pagination handling
- URL status tracking

✅ **Cloud Integration Ready**
- `uploaded_to_cloud` flags on all tables
- Designed for batch cloud uploads
- Incremental sync capability

✅ **Production-Ready Logging**
- Separate log files per spider
- INFO-level logging by default
- Structured stats on spider close
- Debug mode available

✅ **YAML-Based Configuration**
- Single config file for all settings
- Mode switching (CATALOG/AD)
- Multiple URL support
- Environment-specific configs

### Architecture Benefits

🏗️ **Scalability**: Modular design allows independent scaling of catalog and ad scrapers

🔒 **Reliability**: Duplicate detection and status tracking prevent data loss

⚡ **Efficiency**: Incremental scraping saves bandwidth and time

🎯 **Extensibility**: Clean separation of concerns enables easy feature additions

🛡️ **Resilience**: Error handling and retry logic at multiple levels

---

## Setup and Installation

### Prerequisites

- Python 3.8 or higher
- pip (Python package manager)
- Git (for cloning the repository)

### Installation Steps

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd olx_scraper
   ```

2. **Create a virtual environment** (recommended)
   ```bash
   python -m venv venv

   # On Windows
   venv\Scripts\activate

   # On Linux/Mac
   source venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Verify installation**
   ```bash
   scrapy version
   ```

### Dependencies

The project requires the following packages:

```
beautifulsoup4==4.13.4      # HTML parsing
cloudscraper==1.2.71        # Cloudflare bypass
itemadapter==0.3.0          # Item handling
itemloaders==1.3.2          # Data loading
Scrapy==2.12.0              # Web scraping framework
six==1.17.0                 # Python 2/3 compatibility
SQLAlchemy==2.0.41          # Database ORM
Twisted==25.5.0             # Async networking
w3lib==2.1.2                # Web scraping helpers
```

---

## Configuration

### Run Configuration ([run_config.yaml](run_config.yaml))

The main configuration file controls scraper behavior:

```yaml
# Set scraping mode
mode: 'CATALOG'  # Options: 'CATALOG' or 'AD'

# Catalog Spider Settings
catalog_spider:
  start_urls:
    - "https://www.olx.com.br/imoveis/aluguel/estado-es/norte-do-espirito-santo/vitoria"
    # Add more URLs as needed

# Ad Spider Settings
ad_spider:
  source: 'database'  # Options: 'database' or 'urls'
  start_urls:
    - "https://es.olx.com.br/norte-do-espirito-santo/imoveis/[example-url]"
    # Only used when source: 'urls'
```

**Mode Options**:
- `CATALOG`: Scrapes listing pages to discover ad URLs
- `AD`: Scrapes individual ad pages for detailed information

**Ad Spider Source Options**:
- `database`: Automatically fetches unscraped URLs from catalog_info table
- `urls`: Scrapes only the specific URLs listed in start_urls

### Scrapy Settings ([settings.py](olx_scraper/settings.py))

Key settings configured in the project:

```python
# Bot identification
BOT_NAME = 'olx_scraper'

# Respectful crawling
ROBOTSTXT_OBEY = False  # Required for OLX scraping
CONCURRENT_REQUESTS = 8
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...'

# Middleware configuration
DOWNLOADER_MIDDLEWARES = {
    'olx_scraper.middlewares.CloudScraperMiddleware': 543,
}

# Database connection
CONNECTION_STRING = 'sqlite:///scraped_data/olx.sqlite'
```

---

## How to Run

### Running via run.py (Recommended)

The recommended way to run the scrapers is using `run.py` with YAML configuration:

#### 1. Scrape Catalog Pages

**Edit [run_config.yaml](run_config.yaml)**:
```yaml
mode: 'CATALOG'
catalog_spider:
  start_urls:
    - "https://www.olx.com.br/imoveis/aluguel/estado-sc/florianopolis-e-regiao"
```

**Run the scraper**:
```bash
python run.py
```

**What happens**:
- Scrapes all listing pages starting from provided URLs
- Automatically handles pagination
- Extracts ad summaries and URLs
- Stores data in `scraped_data/olx.sqlite`
- Logs output to `logs/olx_catalog.log`

#### 2. Scrape Ad Details (Database Mode)

**Edit [run_config.yaml](run_config.yaml)**:
```yaml
mode: 'AD'
ad_spider:
  source: 'database'
```

**Run the scraper**:
```bash
python run.py
```

**What happens**:
- Queries database for URLs with `url_is_scraped = 0`
- Scrapes each ad page for detailed information
- Updates catalog table to mark URLs as scraped
- Logs output to `logs/olx_ad.log`

#### 3. Scrape Specific Ad URLs (Testing Mode)

**Edit [run_config.yaml](run_config.yaml)**:
```yaml
mode: 'AD'
ad_spider:
  source: 'urls'
  start_urls:
    - "https://es.olx.com.br/norte-do-espirito-santo/imoveis/[ad-url-1]"
    - "https://es.olx.com.br/norte-do-espirito-santo/imoveis/[ad-url-2]"
```

**Run the scraper**:
```bash
python run.py
```

### Running via Scrapy CLI (Alternative)

You can also run spiders directly using Scrapy commands:

#### Catalog Spider
```bash
scrapy crawl olx_catalog -a start_urls="https://www.olx.com.br/imoveis/aluguel/estado-sc"
```

#### Ad Spider (Database Mode)
```bash
scrapy crawl imoveis_sc_properties
```

#### Ad Spider (Specific URL)
```bash
scrapy crawl imoveis_sc_properties -a start_urls="https://es.olx.com.br/[ad-url]"
```

### Typical Workflow

```bash
# Step 1: Scrape catalogs (discover URLs)
# Edit run_config.yaml: mode = 'CATALOG'
python run.py

# Step 2: Review catalog data
# Open scraped_data/olx.sqlite in DB Browser

# Step 3: Scrape ad details
# Edit run_config.yaml: mode = 'AD', source = 'database'
python run.py

# Step 4: Analyze complete data
# Query the database for insights
```

---

## Viewing the Data

### Using DB Browser for SQLite

1. Download [DB Browser for SQLite](https://sqlitebrowser.org/)
2. Open `scraped_data/olx.sqlite`
3. Browse tables: `catalog_info`, `ad_info`, etc.
4. Run SQL queries for analysis

### Sample Queries

**Count total ads scraped**:
```sql
SELECT COUNT(*) FROM catalog_info;
```

**View ads not yet scraped**:
```sql
SELECT title, location, url
FROM catalog_info
WHERE url_is_scraped = 0;
```

**Ads by location**:
```sql
SELECT location, COUNT(*) as count
FROM catalog_info
GROUP BY location
ORDER BY count DESC;
```

**Ads with details**:
```sql
SELECT c.title, c.location, a.description, a.pricing
FROM catalog_info c
JOIN ad_info a ON c.code = a.code;
```

**Check scraping progress**:
```sql
SELECT
    COUNT(*) as total_ads,
    SUM(url_is_scraped) as scraped_ads,
    COUNT(*) - SUM(url_is_scraped) as remaining_ads,
    ROUND(100.0 * SUM(url_is_scraped) / COUNT(*), 2) as percent_complete
FROM catalog_info;
```

---

## Future Enhancements

### Planned Features

- [ ] **Implement normalized table pipelines**
  - Enable `SaveCatalogDetailsPipeline`
  - Enable `SaveCatalogPricingPipeline`
  - Enable `SaveCatalogBadgesPipeline`
  - Enable ad normalization pipelines

- [ ] **Cloud integration**
  - AWS S3 batch upload functionality
  - Implement cloud sync based on `uploaded_to_cloud` flags
  - Create data ingestion to AWS Glue/Athena

- [ ] **Data quality improvements**
  - Analyze scraped data for parsing errors
  - Fix edge cases in data processors
  - Add data validation rules

- [ ] **Sharding strategy**
  - Implement date-based or region-based data organization
  - Optimize for large-scale data storage

- [ ] **Scraping planner**
  - Automatic resume capability after interruptions
  - Prioritization of high-value ads
  - Scheduling for periodic re-scraping

- [ ] **Monitoring and alerting**
  - Scraping success/failure metrics
  - Data quality dashboards
  - Alert on anomalies

- [ ] **Category generalization**
  - Extend beyond real estate to other categories
  - Category-specific item definitions
  - Flexible schema support

### Known Limitations

- Currently optimized only for real estate listings
- Manual configuration required for different regions
- No automatic retry mechanism for failed requests
- Limited to Brazil OLX domain (olx.com.br)

---

## Contributing

This is a personal project, but suggestions and feedback are welcome. Please note that this scraper should be used responsibly and in accordance with OLX's terms of service.

## License

This project is for educational and personal use only. Please respect OLX's robots.txt and terms of service.

## Contact

For questions or issues, please open an issue in the repository.

---

**Last Updated**: February 2026
