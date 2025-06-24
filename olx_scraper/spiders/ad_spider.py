#import scrapy
from scrapy.crawler import CrawlerProcess

from itemloaders import ItemLoader
from itemloaders.processors import MapCompose, TakeFirst
from scrapy.spiders import Spider
from scrapy import Request
from scrapy.utils.project import get_project_settings
from w3lib.html import remove_tags
from datetime import datetime
import sys
import time
import random
import logging


sys.path.append(r"C:\Users\douglas.sgrott_indic\Documents\Pet Projects\olx_scraper\olx_scraper")
# import settings
from items import AdItem
from models import create_table, db_connect
from models import CatalogInfoModel
from sqlalchemy.orm import sessionmaker


class AdSpider(Spider):
    name = 'imoveis_sc_properties'
    handle_httpstatus_list = [403, 404]
    custom_settings = {
        'DOWNLOADER_MIDDLEWARES': {
            'olx_scraper.middlewares.CloudScraperMiddleware': 543,
            'scrapy.downloadermiddlewares.httpcompression.HttpCompressionMiddleware': None,
        },
        'AUTOTHROTTLE_ENABLED': True,
        'AUTOTHROTTLE_DEBUG': True,
        'DOWNLOAD_DELAY': 3,
        'ROBOTSTXT_OBEY': False,
        'ITEM_PIPELINES': {
            # 'olx_scraper.pipelines.UpdateCatalogDatabasePipeline': 200,
            'olx_scraper.pipelines.DefaultValuesAdPipeline': 110,
            'olx_scraper.pipelines.SaveAdInfoPipeline': 200,
            'olx_scraper.pipelines.SaveAdCharacteristicsPipeline': 210,
            'olx_scraper.pipelines.SaveAdDetailsPipeline': 220,
            'olx_scraper.pipelines.SaveAdPricingPipeline': 220,
        },
        'LOG_LEVEL': 'INFO',
        'LOG_FILE': 'logs/olx_ad.log',
        'LOG_FILE_APPEND': False,
    }

    def __init__(self, *args, **kwargs): # start_urls=None,    sys.path.append(r"C:\Users\douglas.sgrott_indic\Documents\Pet Projects\olx_scraper\olx_scraper")
        super().__init__(*args, **kwargs)

        start_urls_arg = kwargs.get('start_urls')
        if 'start_urls' in kwargs:
            self.start_urls = kwargs.get('start_urls').split(',')
        if start_urls_arg:
            if isinstance(start_urls_arg, str):
                self.start_urls = [url.strip() for url in start_urls_arg.split(',') if url.strip()]
            elif isinstance(start_urls_arg, list):
                self.start_urls = start_urls_arg
            else:
                self.start_urls = []
        else:
            self.start_urls = []

    def get_urls_from_db(self):
        engine = db_connect()
        create_table(engine)
        Session = sessionmaker(bind=engine)

        with Session() as session:
            rows_not_scraped = session.query(CatalogInfoModel).filter(CatalogInfoModel.url_is_scraped == 0).all()

            for row in rows_not_scraped:
                # time.sleep(random.randint(self.min_delay, self.max_delay))
                time.sleep(random.randint(3, 6))
                yield Request(url=row.url, callback=self.parse, meta={'cloudflare_bypass': True}) # meta={'catalogo_id': row.id}

    def start_requests(self):
        if self.start_urls:
            for url in self.start_urls:
                yield Request(url=url, callback=self.parse, meta={'cloudflare_bypass': True})
        else:
            yield from self.get_urls_from_db()

    def _parse_characteristics(self, response):
        all_features = {}

        # 1. THE PERFECTED SELECTOR: Find the blocks but exclude any inside a modal.
        section_blocks = response.xpath("//div[span[contains(text(), 'Características do')] and not(ancestor::*[@data-ds-component='DS-Modal'])]")
        
        # print(f"Found {len(section_blocks)} VISIBLE characteristic blocks.")

        if not section_blocks:
            # print("No characteristic blocks were found. The page structure may have changed.")
            return None

        for block in section_blocks:
            title = block.xpath("./span[contains(text(), 'Características do')]/text()").get()
            features = block.xpath(".//*[@data-ds-component='DS-Badge']//*[@data-ds-component='DS-Text']/text()").getall()

            if title:
                clean_title = title.strip()
                feature_list = [f.strip() for f in features if f.strip()]
                
                # 2. THE DEFENSIVE LOGIC: Only add to the dictionary if the feature list is NOT empty.
                if feature_list:
                    all_features[clean_title] = feature_list
                    # print(f"SUCCESS: Found title '{clean_title}' with features: {feature_list}")
                # else:
                    # print(f"Found title '{clean_title}' but it had no features listed. Skipping.")

        # print(f"Final extracted data: {all_features}")
        return all_features

    def parse(self, response):
        print(f"Processing URL: {response.url}")
        item_loader = ItemLoader(AdItem(), selector=response)
        characteristics = self._parse_characteristics(response)
        item_loader.add_xpath('breadcrumb', "//nav[@data-ds-component='DS-Breadcrumb']")
        item_loader.add_value('characteristics', characteristics)
        item_loader.add_xpath('code', ".//span[contains(text(), 'Código do anúncio:')]/text()")
        item_loader.add_xpath('date', "//span[contains(@class, 'olx-text olx-text--caption olx-text--block olx-text--semibold olx-color-neutral-100')]/text()")
        item_loader.add_xpath('description', './/div[contains(@id, "description-title")]')
        item_loader.add_xpath('details', './/div[contains(@id, "details")]')
        item_loader.add_xpath('full_location', './/div[contains(@id, "location")]')
        item_loader.add_xpath('pricing', './/div[contains(@id, "price-box-container")]')
        item_loader.add_xpath('street_address', './/div[contains(@id, "location")]')
        item_loader.add_xpath('title', './/div[contains(@id, "description-title")]')
        item_loader.add_value('url', response.url)
        loaded_item = item_loader.load_item()
        return loaded_item
