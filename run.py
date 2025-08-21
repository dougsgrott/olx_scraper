import yaml
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

def read_config(file_path="run_config.yaml"):
    """
    Reads and parses the YAML configuration file.

    Args:
        file_path (str): The path to the configuration file.

    Returns:
        dict: The loaded configuration, or None if an error occurs.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return yaml.safe_load(file)
    except FileNotFoundError:
        print(f"Error: Configuration file not found at '{file_path}'")
    except yaml.YAMLError as e:
        print(f"Error parsing YAML file: {e}")
    return None

def run_scraper():
    """
    Initializes and runs the Scrapy crawler based on the settings
    in the run_config.yaml file.
    """
    config = read_config()
    if not config:
        print("Could not start scraper due to configuration error.")
        return

    mode = config.get('mode')
    if not mode:
        print("Error: 'mode' not specified in run_config.yaml. Should be 'CATALOG' or 'AD'.")
        return

    settings = get_project_settings()
    process = CrawlerProcess(settings)

    print(f"--- Starting scraper in '{mode}' mode ---")

    if mode == 'CATALOG':
        from olx_scraper.spiders.catalog_spider import CatalogSpider
        catalog_config = config.get('catalog_spider', {})
        start_urls = catalog_config.get('start_urls', [])

        if not start_urls or not isinstance(start_urls, list):
            print("Error: 'start_urls' (as a list) not found for CATALOG mode in run_config.yaml.")
            return

        print(f"Found {len(start_urls)} start URL(s) for CatalogSpider.")
        # The spider's __init__ expects a comma-separated string for the start_urls argument
        process.crawl(CatalogSpider, start_urls=','.join(start_urls))

    elif mode == 'AD':
        from olx_scraper.spiders.ad_spider import AdSpider
        ad_config = config.get('ad_spider', {})
        source = ad_config.get('source', 'database')

        if source == 'urls':
            start_urls = ad_config.get('start_urls', [])
            if not start_urls or not isinstance(start_urls, list):
                print("Error: 'source' is 'urls' but no 'start_urls' (as a list) provided for AD mode.")
                return
            print(f"Found {len(start_urls)} specific URL(s) for AdSpider.")
            process.crawl(AdSpider, start_urls=','.join(start_urls))
        elif source == 'database':
            print("AdSpider will fetch URLs from the database.")
            # We crawl without start_urls, so the spider uses its default get_urls_from_db method
            process.crawl(AdSpider)
        else:
            print(f"Error: Invalid 'source' for AD mode: '{source}'. Must be 'urls' or 'database'.")
            return

    else:
        print(f"Error: Invalid mode '{mode}' in config. Must be 'CATALOG' or 'AD'.")
        return

    # Start the Scrapy engine
    process.start()
    print("--- Crawl finished! ---")


if __name__ == "__main__":
    run_scraper()
