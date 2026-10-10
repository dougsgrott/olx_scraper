"""Entrypoint for the Scrapy-free OLX scraper (olx_patchright/ package).

Reads the same run_config.yaml as the legacy run_scrapy.py (or another file
via --config, e.g. one generated per job by the control panel):

    mode: 'CATALOG'  scrape catalogue pages (catalog_spider.start_urls)
    mode: 'WARM'     open a headful browser to solve Cloudflare by hand
                     (warm.url, falling back to catalog_spider.start_urls[0])

Exit codes, for wrappers and the control panel:

    0    ran (per-URL outcomes are in the manifests)
    2    configuration error
    75   browser busy: another process holds the browser lock; try later
    130  cancelled (SIGINT/SIGTERM) before every start URL was scraped

    uv run python run_patchright.py
    uv run python run_patchright.py --config logs/jobs/<id>/run_config.yaml
"""
import argparse
import json
import sys

import yaml

from olx_patchright.browser import acquire_browser_lock

EXIT_CONFIG = 2
EXIT_CANCELLED = 130


def read_config(file_path='run_config.yaml'):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: Configuration file not found at '{file_path}'")
    except yaml.YAMLError as e:
        print(f"Error parsing YAML file: {e}")
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--config', default='run_config.yaml',
                        help='run configuration file (default: %(default)s)')
    args = parser.parse_args(argv)

    config = read_config(args.config)
    if not isinstance(config, dict):
        if config is not None:
            print(f"Error: '{args.config}' is not a YAML mapping.")
        return EXIT_CONFIG

    mode = config.get('mode')
    catalog_urls = (config.get('catalog_spider') or {}).get('start_urls') or []

    if mode == 'WARM':
        url = (config.get('warm') or {}).get('url') or (catalog_urls[0] if catalog_urls else None)
        if not url:
            print("Error: WARM mode needs warm.url or catalog_spider.start_urls in the config.")
            return EXIT_CONFIG
        acquire_browser_lock(label=f'WARM ({args.config})')
        from olx_patchright.warm import warm_profile
        warm_profile(url)

    elif mode == 'CATALOG':
        if not isinstance(catalog_urls, list) or not catalog_urls:
            print("Error: 'catalog_spider.start_urls' (as a list) not found in the config.")
            return EXIT_CONFIG
        early_stop = (config.get('catalog_spider') or {}).get('early_stop')
        if early_stop is not None and not isinstance(early_stop, dict):
            print("Error: 'catalog_spider.early_stop' must be a mapping (patience / min_new_per_page / max_pages).")
            return EXIT_CONFIG
        # Fail fast (exit 75) before importing patchright if a browser job is
        # already running.
        acquire_browser_lock(
            label=f'CATALOG, {len(catalog_urls)} URL(s) ({args.config})'
        )
        from olx_patchright.catalog import scrape_catalog
        print(f"--- Scraping catalogue: {len(catalog_urls)} start URL(s) ---")
        manifests = scrape_catalog(catalog_urls, early_stop=early_stop)
        print(json.dumps(manifests, ensure_ascii=False, indent=2))
        if len(manifests) < len(catalog_urls) or \
                any('cancelled' in m['stop_reason'] for m in manifests):
            return EXIT_CANCELLED

    elif mode == 'AD':
        print("AD mode is not ported to the Scrapy-free scraper yet (catalogue first).")
        return EXIT_CONFIG

    else:
        print(f"Error: invalid mode '{mode}' in the config. Must be 'CATALOG', 'WARM', or 'AD'.")
        return EXIT_CONFIG
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        sys.exit(EXIT_CANCELLED)
