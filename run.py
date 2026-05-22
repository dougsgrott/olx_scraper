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


def run_scraper():
    config = read_config()
    if not config:
        print("Could not start scraper due to configuration error.")
        return

    mode = config.get('mode')
    if not mode:
        print("Error: 'mode' not specified in run_config.yaml. Should be 'CATALOG' or 'AD'.")
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
                from olx_scraper.spiders.catalog_spider import CatalogSpider
                catalog_config = config.get('catalog_spider', {})
                start_urls = catalog_config.get('start_urls', [])

                if not start_urls or not isinstance(start_urls, list):
                    print("Error: 'start_urls' (as a list) not found for CATALOG mode in run_config.yaml.")
                    return

                print(f"Found {len(start_urls)} start URL(s) for CatalogSpider.")
                print("Detailed logs -> logs/olx_catalog.log (tail -f it to watch live)")
                yield runner.crawl(CatalogSpider, start_urls=','.join(start_urls))

            elif mode == 'AD':
                from olx_scraper.spiders.ad_spider import AdSpider
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
                print(f"Error: Invalid mode '{mode}' in config. Must be 'CATALOG' or 'AD'.")
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
