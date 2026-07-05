# asyncioreactor must be installed before any other Twisted/Scrapy imports
from twisted.internet import asyncioreactor
asyncioreactor.install()

# Route scrapy-playwright through patchright (a drop-in, anti-detection
# Playwright fork) instead of vanilla playwright. This must run before
# scrapy_playwright is imported. patchright mirrors playwright's module
# layout, so we alias every playwright module scrapy-playwright imports.
import sys
import patchright
import patchright.async_api
import patchright._impl._errors

sys.modules["playwright"] = patchright
sys.modules["playwright.async_api"] = patchright.async_api
sys.modules["playwright._impl"] = patchright._impl
sys.modules["playwright._impl._errors"] = patchright._impl._errors

import os
import subprocess
import time
import traceback
import yaml
from scrapy.crawler import CrawlerRunner
from scrapy.utils.project import get_project_settings
from scrapy.utils.log import configure_logging
from twisted.internet import reactor, defer


def read_config(file_path="run_config.yaml"):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return yaml.safe_load(file)
    except FileNotFoundError:
        print(f"Error: Configuration file not found at '{file_path}'")
    except yaml.YAMLError as e:
        print(f"Error parsing YAML file: {e}")
    return None


def _clear_profile_lock():
    """Free the persistent Playwright profile before launching a browser.

    Chromium enforces single-instance access to a user-data-dir via a
    `SingletonLock`. scrapy-playwright routinely leaves an orphaned chromium
    alive after a run; if it is still holding the profile when the next run
    launches, launch_persistent_context fails with 'SingletonLock: File exists',
    silently falls back to an unauthenticated throwaway context, and gets a
    Cloudflare 403.

    Match on the distinctive profile dir name (`.playwright_profile` only ever
    appears in a chromium `--user-data-dir=` for this project, not in run.py's
    own argv), SIGKILL it, wait until it is actually gone, then drop stale locks.
    """
    profile = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.playwright_profile')
    pattern = '.playwright_profile'

    def _still_running():
        try:
            return subprocess.run(
                ['pgrep', '-f', pattern], stdout=subprocess.DEVNULL
            ).returncode == 0
        except FileNotFoundError:
            return False

    if _still_running():
        print("A browser is still holding the Playwright profile; killing it.")
        try:
            subprocess.run(['pkill', '-9', '-f', pattern])
        except FileNotFoundError:
            pass
        for _ in range(15):
            time.sleep(0.3)
            if not _still_running():
                break

    for name in ('SingletonLock', 'SingletonCookie', 'SingletonSocket'):
        path = os.path.join(profile, name)
        try:
            if os.path.islink(path) or os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


def _warm_profile(config):
    """Launch the interactive profile warm-up (scripts/warm_profile.py) in a
    clean subprocess, so its sync Playwright driver doesn't collide with the
    Twisted asyncio reactor this module installs. Warms the catalog spider's
    first start_url unless `warm.url` overrides it in run_config.yaml."""
    warm_cfg = config.get('warm') or {}
    url = warm_cfg.get('url')
    if not url:
        urls = (config.get('catalog_spider') or {}).get('start_urls') or []
        url = urls[0] if urls else None

    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, 'scripts', 'warm_profile.py')
    cmd = [sys.executable, script] + ([url] if url else [])
    print(f"--- Warming profile{f' at {url}' if url else ''} ---")
    subprocess.run(cmd, check=False)


def run_scraper():
    config = read_config()
    if not config:
        print("Could not start scraper due to configuration error.")
        return

    mode = config.get('mode')
    if not mode:
        print("Error: 'mode' not specified in run_config.yaml. Should be 'CATALOG', 'AD', or 'WARM'.")
        return

    _clear_profile_lock()

    if mode == 'WARM':
        _warm_profile(config)
        return

    os.makedirs('logs', exist_ok=True)

    settings = get_project_settings()
    # CrawlerRunner (unlike CrawlerProcess) does NOT configure logging on its own.
    # Without this call there is no console output and the spider's LOG_FILE is
    # never written. Each crawler then re-points the handler at its own LOG_FILE.
    configure_logging(settings)
    runner = CrawlerRunner(settings)

    print(f"--- Starting scraper in '{mode}' mode ---")

    @defer.inlineCallbacks
    def crawl():
        try:
            if mode == 'CATALOG':
                from olx_scrapy.spiders.catalog_spider import CatalogSpider
                catalog_config = config.get('catalog_spider', {})
                start_urls = catalog_config.get('start_urls', [])

                if not start_urls or not isinstance(start_urls, list):
                    print("Error: 'start_urls' (as a list) not found for CATALOG mode in run_config.yaml.")
                    return

                print(f"Found {len(start_urls)} start URL(s) for CatalogSpider.")
                print("Detailed logs -> logs/olx_catalog.log (tail -f it to watch live)")
                yield runner.crawl(CatalogSpider, start_urls=','.join(start_urls))

            elif mode == 'AD':
                from olx_scrapy.spiders.ad_spider import AdSpider
                ad_config = config.get('ad_spider', {})
                source = ad_config.get('source', 'database')

                if source == 'urls':
                    start_urls = ad_config.get('start_urls', [])
                    if not start_urls or not isinstance(start_urls, list):
                        print("Error: 'source' is 'urls' but no 'start_urls' provided for AD mode.")
                        return
                    print(f"Found {len(start_urls)} specific URL(s) for AdSpider.")
                    print("Detailed logs -> logs/olx_ad.log (tail -f it to watch live)")
                    yield runner.crawl(AdSpider, start_urls=','.join(start_urls))
                elif source == 'database':
                    print("AdSpider will fetch URLs from the database.")
                    print("Detailed logs -> logs/olx_ad.log (tail -f it to watch live)")
                    yield runner.crawl(AdSpider)
                else:
                    print(f"Error: Invalid 'source' for AD mode: '{source}'. Must be 'urls' or 'database'.")
                    return
            else:
                print(f"Error: Invalid mode '{mode}' in config. Must be 'CATALOG', 'AD', or 'WARM'.")
                return
        except Exception:
            print("--- Crawl failed with an exception ---")
            traceback.print_exc()
        finally:
            print("--- Crawl finished! ---")
            if reactor.running:
                reactor.stop()

    deferred = crawl()
    # If crawl() returned before reaching a 'yield' (e.g. bad config), its
    # deferred is already fired and there is nothing to wait on.
    if not deferred.called:
        reactor.run()


if __name__ == "__main__":
    run_scraper()
