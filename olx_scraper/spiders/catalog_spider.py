from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from scrapy.exceptions import CloseSpider
from scrapy.loader import ItemLoader
from scrapy.spiders import Spider, signals
import cloudscraper
from scrapy.http import TextResponse
import scrapy
from itemloaders.processors import TakeFirst
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import re
import time
import random
import logging
import pprint
import hashlib
from sqlalchemy.orm import sessionmaker

import sys
import os

sys.path.append(r"C:\Users\douglas.sgrott_indic\Documents\Pet Projects\olx_scraper\olx_scraper")
from items import CatalogItem, StatusItem


class CatalogSpider(Spider):
    name = 'olx_catalog'
    handle_httpstatus_list = [403, 404]

    custom_settings = {
        'DOWNLOADER_MIDDLEWARES': {
            'olx_scraper.middlewares.CloudScraperMiddleware': 543,
            'scrapy.downloadermiddlewares.httpcompression.HttpCompressionMiddleware': None,
        },
        'AUTOTHROTTLE_ENABLED': True,
        'AUTOTHROTTLE_DEBUG': False,
        'DOWNLOAD_DELAY': 3,
        'ROBOTSTXT_OBEY': False,
        'ITEM_PIPELINES': {
           'olx_scraper.pipelines.DuplicatesCatalogPipeline': 100,
           'olx_scraper.pipelines.DefaultValuesCatalogPipeline': 110,
           'olx_scraper.pipelines.SaveCatalogInfoPipeline': 200,
           'olx_scraper.pipelines.SaveCatalogDetailsPipeline': 210,
           'olx_scraper.pipelines.SaveCatalogPricingPipeline': 220,
           'olx_scraper.pipelines.SaveCatalogBadgesPipeline': 230,
        },
        'LOG_LEVEL': 'INFO',
        'LOG_FILE': 'logs/olx_catalog.log',
        'LOG_FILE_APPEND': False,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # We get the start_urls from the kwargs passed by the -a flag.
        # We split by comma to allow passing multiple URLs in the future.
        if 'start_urls' in kwargs:
            self.start_urls = kwargs.get('start_urls').split(',')

        # You can keep your other attributes here if needed
        self.page_items = []
        self.duplicated_page_count = 0
        self.skipping = False
        # self.planner = BasicSkipper(threshold=10, skip_n=10)
        # self.min_delay = min_delay
        # self.max_delay = max_delay

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super(CatalogSpider, cls).from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider.handle_spider_closed, signals.spider_closed)
        crawler.signals.connect(spider.handle_spider_opened, signals.spider_opened)
        return spider

    def handle_spider_opened(self, spider):
        self.logger.info(f"Spider opened: {spider.name}")

    def handle_spider_closed(self, reason=""):
        stats = self.crawler.stats.get_stats()
        self.logger.info("Scraping Stats:\n" + pprint.pformat(stats))
        self.logger.info(f"Spider Closed. Reason: {reason}")

    def start_requests(self):
        # Check if start_urls was actually provided
        if not hasattr(self, 'start_urls'):
            raise AttributeError("Spider was not started with start_urls. Please provide them with the -a flag (e.g., -a start_urls=http://...).")
            
        for url in self.start_urls:
            self.logger.info(f"Scheduling request for: {url}")
            yield scrapy.Request(url=url, callback=self.parse, meta={'cloudflare_bypass': True})


    # 1. FOLLOWING LEVEL 1
    def parse(self, response):
        self.page_items = []
        selector_str = '//div[@class="AdListing_adListContainer__ALQla"]/section'
        selectors = response.xpath(selector_str)
        for sel in selectors:
            yield self.populate_catalog(sel, response.url)
        time.sleep(random.randint(5, 8))
        yield from self.paginate(response)
        # Breadcrumb: response.xpath(".//ol[contains(@class, 'olx-breadcrumb__list')]")[0].get()

    # 2. SCRAPING LEVEL 1
    def populate_catalog(self, selector, url):
        item_loader = ItemLoader(item=CatalogItem(), selector=selector)
        item_loader.default_output_processor = TakeFirst()
        item_loader.add_xpath('badges', './/div[contains(@class, "olx-adcard__badges")]')
        item_loader.add_xpath('code', './/a[contains(@class, "olx-adcard__link")]/@href')
        item_loader.add_xpath('date', './/div[contains(@class, "olx-adcard__bottombody")]')
        item_loader.add_xpath('details', './/div[contains(@class, "olx-adcard__details")]')
        item_loader.add_xpath('location', './/div[contains(@class, "olx-adcard__bottombody")]')
        item_loader.add_xpath('pricing', './/div[contains(@class, "olx-adcard__mediumbody")]')
        item_loader.add_xpath('title', './/h2[contains(@class, "olx-adcard__title")]/text()')
        item_loader.add_xpath('url', './/a[contains(@class, "olx-adcard__link")]/@href')
        loaded_item = item_loader.load_item()

        # Concatenate unique fields (safely)
        uid_string = f"{loaded_item.get('title', '')}|{loaded_item.get('ad_location', '')}|{loaded_item.get('ad_date', '')}"
        uid_hash = hashlib.md5(uid_string.encode('utf-8')).hexdigest()  # or use sha256
        loaded_item['uid'] = uid_hash

        self.page_items.append(loaded_item)
        return loaded_item

    def create_hash(self, input_string: str) -> str: #, last_idx: int = 12
        """
        Generates a unique id
        refs:
        - md5: https://stackoverflow.com/questions/22974499/generate-id-from-string-in-python
        - sha3: https://stackoverflow.com/questions/47601592/safest-way-to-generate-a-unique-hash
        (- guid/uiid: https://stackoverflow.com/questions/534839/how-to-create-a-guid-uuid-in-python?noredirect=1&lq=1)
        """
        m = hashlib.md5()
        input_string = input_string.encode('utf-8')
        m.update(input_string)
        unqiue_name = str(int(m.hexdigest(), 16))#[0:last_idx]
        return unqiue_name


    # 3. PAGINATION LEVEL 1
    def paginate(self, response):
        # Extract pagination string
        pagination_str = response.xpath('//div[contains(@id, "total-of-ads")]/div/p/text()').get()

        # Extract total number of ads using regex
        match = re.search(r'de\s+([\d.]+)', pagination_str)
        if match:
            total_ads = int(match.group(1).replace('.', ''))
            ads_per_page = 50
            total_pages = (total_ads + ads_per_page - 1) // ads_per_page
        else:
            total_pages = 1

        # Get current page from URL
        parsed_url = urlparse(response.url)
        query_params = parse_qs(parsed_url.query)
        current_page = int(query_params.get('o', ['1'])[0])

        if current_page < total_pages:
            next_page = current_page + 1
            query_params['o'] = [str(next_page)]
            new_query = urlencode(query_params, doseq=True)
            next_url = urlunparse(parsed_url._replace(query=new_query))

            self.logger.info(f"Paginating: Scheduling next request for {next_url}")
            yield scrapy.Request(url=next_url, callback=self.parse, meta={'cloudflare_bypass': True})
