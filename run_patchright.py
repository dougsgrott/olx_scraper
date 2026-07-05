"""Entrypoint for the Scrapy-free OLX scraper (olx_patchright/ package).

Reads the same run_config.yaml as the legacy run_scrapy.py:

    mode: 'CATALOG'  scrape catalogue pages (catalog_spider.start_urls)
    mode: 'WARM'     open a headful browser to solve Cloudflare by hand
                     (warm.url, falling back to catalog_spider.start_urls[0])

    uv run python run_patchright.py
"""
import json

import yaml


def read_config(file_path='run_config.yaml'):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: Configuration file not found at '{file_path}'")
    except yaml.YAMLError as e:
        print(f"Error parsing YAML file: {e}")
    return None


def main():
    config = read_config()
    if not config:
        return

    mode = config.get('mode')
    catalog_urls = (config.get('catalog_spider') or {}).get('start_urls') or []

    if mode == 'WARM':
        url = (config.get('warm') or {}).get('url') or (catalog_urls[0] if catalog_urls else None)
        if not url:
            print("Error: WARM mode needs warm.url or catalog_spider.start_urls in run_config.yaml.")
            return
        from olx_patchright.warm import warm_profile
        warm_profile(url)

    elif mode == 'CATALOG':
        if not isinstance(catalog_urls, list) or not catalog_urls:
            print("Error: 'catalog_spider.start_urls' (as a list) not found in run_config.yaml.")
            return
        from olx_patchright.catalog import scrape_catalog
        print(f"--- Scraping catalogue: {len(catalog_urls)} start URL(s) ---")
        manifest = scrape_catalog(catalog_urls)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))

    elif mode == 'AD':
        print("AD mode is not ported to the Scrapy-free scraper yet (catalogue first).")

    else:
        print(f"Error: invalid mode '{mode}' in run_config.yaml. Must be 'CATALOG', 'WARM', or 'AD'.")


if __name__ == '__main__':
    main()
