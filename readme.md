# OLX Scraper (for real estate)

This Scrapy project is designed to perform a two-stage scrape of listings from OLX Brazil. It first scrapes catalog pages to gather a list of ad URLs and then scrapes the individual ad pages for detailed information. For know, it is being built primarily for real estate ads, possibly being generalized in the future.

## Project Goal

The primary goal is to build a comprehensive database of real estate ads by:
1.  **Cataloging:** Browsing listing pages to discover new ads.
2.  **Ad scraping:** Visiting each ad page to extract in-depth information.
3.  **Storing:** Saving all extracted data into a structured SQLite database for future analysis.

---

## Features

* **Two-Stage Scraping:** Utilizes separate spiders for catalog and ad pages for a modular and robust workflow.
* **Anti-Bot Evasion:** Integrates the `cloudscraper` library via a custom middleware to bypass Cloudflare's anti-bot measures.
* **Persistent Storage:** Uses SQLAlchemy and a SQLite database (`olx.sqlite`) to store scraped data, allowing for incremental scraping and data retention.
* **Duplicate Prevention:** A pipeline checks for duplicate ads based on a unique hash, preventing reprocessing of the same listing.
* **Data Normalization:** Employs `ItemLoaders` and custom processors in `items.py` to clean, parse, and structure the extracted data before storage.
* **Structured Database:** Defines a clear database schema in `models.py` to store catalog and ad information across multiple related tables.
* **Dynamic URL Handling:** The `AdSpider` can be run with a specific start URL or, if none is provided, it will automatically fetch unscraped URLs from the database.

---

## Project Structure

```
.
├── logs/
│   ├── olx_ad.log          # Log file for the Ad Spider
│   └── olx_catalog.log     # Log file for the Catalog Spider
├── scraped_data/
│   └── olx.sqlite          # SQLite database file where all data is stored
├── olx_scraper/
│   ├── spiders/
│   │   ├── __init__.py
│   │   ├── catalog_spider.py   # Spider to scrape listing/catalog pages
│   │   └── ad_spider.py        # Spider to scrape individual ad pages
│   ├── __init__.py
│   ├── items.py              # Defines the data structure (Items) and processors
│   ├── middlewares.py        # Contains the CloudScraper anti-bot middleware
│   ├── models.py             # Defines the SQLAlchemy database models/schema
│   ├── pipelines.py          # Defines data processing and storage pipelines
│   └── settings.py           # Scrapy project settings
└── scrapy.cfg                # Scrapy project configuration file
```

---

## How It Works

The scraping process is designed to be run in two distinct steps:

### Step 1: Run the Catalog Spider

The `CatalogSpider` (`olx_catalog`) is responsible for discovering ads.

1.  It starts with a catalog URL (e.g., a search results page).
2.  It extracts summary data for each ad on the page, including its title, location, price, and URL.
3.  It generates a unique `uid` for each ad to handle duplicates.
4.  The `DuplicatesCatalogPipeline` checks if the ad already exists in the database. If it does, the item is dropped.
5.  If the ad is new, its information is saved to the `catalog_*` tables in the `olx.sqlite` database by the `SaveCatalog*` pipelines.
6.  The spider automatically handles pagination, moving to the next page of results until no more ads are found.

### Step 2: Run the Ad Spider

The `AdSpider` (`imoveis_sc_properties`) is responsible for getting the details.

1.  When started without a `start_urls` argument, it queries the `olx.sqlite` database for any ad URLs that have not yet been scraped (`url_is_scraped == 0`).
2.  It visits each of these ad URLs.
3.  It uses detailed XPath selectors to extract comprehensive information, such as the ad description, property characteristics (`Características`), details (bedrooms, bathrooms), and location.
4.  The extracted data is processed and cleaned by the `AdItem` loader.
5.  The `SaveAd*` pipelines save the detailed information into the `ad_*` tables in the database.
6.  (Optional) A pipeline could be added to mark the URL as scraped in the `catalog_info` table to prevent re-scraping.

---

## Setup and Installation

Documentation work in progress

<!-- 1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd <your-repo-name>
    ```

2.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    ```

3.  **Install dependencies:**
    This project requires Scrapy and other libraries. Create a `requirements.txt` file with the following content:
    ```
    Scrapy
    cloudscraper
    SQLAlchemy
    bs4
    ```
    Then, install them:
    ```bash
    pip install -r requirements.txt
    ``` -->

---

## How to Run

Documentation work in progress

<!-- Make sure you are in the project's root directory (the one containing `scrapy.cfg`).

### To Scrape a New Catalog

Run the `olx_catalog` spider, providing a starting URL.

```bash
scrapy crawl olx_catalog -a start_urls="[https://www.olx.com.br/imoveis/aluguel/estado-es](https://www.olx.com.br/imoveis/aluguel/estado-es)"
``` -->

### To Scrape Ad Details

Documentation work in progress

<!-- Once you have populated the database with catalog URLs, run the `imoveis_sc_properties` spider without any arguments. It will automatically find and scrape the pending URLs.

```bash
scrapy crawl imoveis_sc_properties
```

You can also run it on a single ad URL for testing purposes:

```bash
scrapy crawl imoveis_sc_properties -a start_urls="<url-of-a-single-ad>"
``` -->

---

## Viewing the Data

You can inspect the scraped data using any SQLite database browser (like [DB Browser for SQLite](https://sqlitebrowser.org/)). Simply open the `scraped_data/olx.sqlite` file to view the tables and their contents.

## Next steps

- Analyze scraped data and fix any kinks in data processing
- Implement UpdateTable for catalog
- Create sharding strategy for data organization
- Create a planner to resume scraping
- Ingest batched data to a cloud server