# Define here the models for your spider middleware
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/spider-middleware.html

from scrapy import signals

# useful for handling different item types with a single interface
from itemadapter import is_item, ItemAdapter

import logging
from scrapy import logformatter

from scrapy.http import HtmlResponse
# from models import CatalogModel, HtmlCatalogModel, HtmlPropertyModel, db_connect, create_table
from sqlalchemy.orm import sessionmaker

import cloudscraper
from scrapy.http import TextResponse
from scrapy import signals
from twisted.internet import defer, reactor
from twisted.web.client import Agent, FileBodyProducer
from twisted.web.http_headers import Headers
from io import BytesIO
from datetime import datetime # For logging debug files
import os



# In olx_scraper/middlewares.py

import logging
import cloudscraper
from scrapy.http import TextResponse
from scrapy import signals
from twisted.internet import defer, reactor
# No longer need 'os' or 'datetime'
# No longer need from scrapy.exceptions import IgnoreRequest

class CloudScraperMiddleware:
    """
    A simplified Scrapy downloader middleware that uses the cloudscraper library
    to bypass Cloudflare protection. It's designed for production use,
    removing the debugging logic for saving failed responses.
    """
    def __init__(self):
        self.scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
        )
        # Use Scrapy's logging system
        self.logger = logging.getLogger(self.__class__.__name__)

    @classmethod
    def from_crawler(cls, crawler):
        # This method is used by Scrapy to create your middleware.
        middleware = cls()
        crawler.signals.connect(middleware.spider_opened, signal=signals.spider_opened)
        return middleware

    def spider_opened(self, spider):
        self.logger.info(f'CloudScraperMiddleware enabled for spider: {spider.name}')

    def process_request(self, request, spider):
        # We only process requests that have the 'cloudflare_bypass' meta key.
        if 'cloudflare_bypass' in request.meta and request.meta['cloudflare_bypass']:
            self.logger.debug(f"Bypassing Cloudflare for {request.url}")
            # Use Twisted's reactor to run the blocking cloudscraper call in a thread
            deferred = defer.Deferred()
            reactor.callInThread(self._make_request, request, deferred)
            return deferred
        # For all other requests, do nothing and let Scrapy handle them.
        return None

    def _make_request(self, request, deferred):
        try:
            # We can simplify the headers if cloudscraper's defaults are working well,
            # but keeping them is safer.
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
            }

            # Make the request using the cloudscraper instance
            resp = self.scraper.get(
                request.url,
                headers=headers,
                timeout=request.meta.get('download_timeout', 180),
                allow_redirects=True
            )

            # --- SIMPLIFIED LOGIC ---
            # No matter the status code (200, 403, 404, etc.), create a
            # Scrapy TextResponse and pass it back to the engine.
            # The spider's handle_httpstatus_list will ensure it gets processed.
            response = TextResponse(
                url=resp.url,
                status=resp.status_code,
                headers=resp.headers,
                body=resp.content,
                encoding=resp.encoding,
                request=request
            )
            # Send the result back to the main Scrapy thread
            reactor.callFromThread(deferred.callback, response)

        except Exception as e:
            self.logger.error(f"CloudScraperMiddleware exception for {request.url}: {e}")
            # In case of an exception, send the failure back to the main thread
            reactor.callFromThread(deferred.errback, e)


# class FakeCatalogResponseMiddleware:
#     def __init__(self):
#         # Set up the SQLAlchemy session
#         engine = db_connect()
#         create_table(engine)
#         self.Session = sessionmaker(bind=engine)
#         self.logger = logging.getLogger(__name__)

#     def process_request(self, request, spider):
#         # Only activate when fake scraping is enabled
#         # if getattr(spider, 'USE_FAKE_SCRAPING', False):
#         # if spider.settings.getbool('USE_FAKE_SCRAPING', False):
#         session = self.Session()
#         try:
#             # Query the database for a record with the matching URL
#             record = session.query(HtmlCatalogModel).filter_by(current_url=request.url).first()
#             if record and record.raw_html:
#                 self.logger.info(f"Returning fake response for {request.url}")
#                 # Create and return a fake HtmlResponse using the stored raw_html
#                 return HtmlResponse(
#                     url=request.url,
#                     body=record.raw_html,#.encode('utf-8'),
#                     encoding='utf-8',
#                     request=request
#                 )
#             else:
#                 self.logger.info(f"No fake data found for {request.url}. Returning fake 404 response.")
#                 # Return a fake response (e.g., 404) so that no network request is made
#                 return HtmlResponse(
#                     url=request.url,
#                     status=404,
#                     body=b'',
#                     encoding='utf-8',
#                     request=request
#                 )
#         finally:
#             session.close()
#         # Otherwise, proceed with normal downloading
#         # return None


# class FakePropertyResponseMiddleware:
#     def __init__(self):
#         # Set up the SQLAlchemy session
#         engine = db_connect()
#         create_table(engine)
#         self.Session = sessionmaker(bind=engine)
#         self.logger = logging.getLogger(__name__)

#     def process_request(self, request, spider):
#         # Only activate when fake scraping is enabled
#         # if getattr(spider, 'USE_FAKE_SCRAPING', False):
#         # if spider.settings.getbool('USE_FAKE_SCRAPING', False):
#         session = self.Session()
#         try:
#             # Query the database for a record with the matching URL
#             record = session.query(HtmlPropertyModel).filter_by(url=request.url).first()
#             if record and record.raw_html:
#                 self.logger.info(f"Returning fake response for {request.url}")
#                 # Create and return a fake HtmlResponse using the stored raw_html
#                 return HtmlResponse(
#                     url=request.url,
#                     body=record.raw_html,#.encode('utf-8'),
#                     encoding='utf-8',
#                     request=request
#                 )
#             else:
#                 self.logger.info(f"No fake data found for {request.url}. Returning fake 404 response.")
#                 # Return a fake response (e.g., 404) so that no network request is made
#                 return HtmlResponse(
#                     url=request.url,
#                     status=404,
#                     body=b'',
#                     encoding='utf-8',
#                     request=request
#                 )
#         finally:
#             session.close()
#         # Otherwise, proceed with normal downloading
#         # return None


class RealestateScraperSpiderMiddleware:
    # Not all methods need to be defined. If a method is not defined,
    # scrapy acts as if the spider middleware does not modify the
    # passed objects.

    @classmethod
    def from_crawler(cls, crawler):
        # This method is used by Scrapy to create your spiders.
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_spider_input(self, response, spider):
        # Called for each response that goes through the spider
        # middleware and into the spider.

        # Should return None or raise an exception.
        return None

    def process_spider_output(self, response, result, spider):
        # Called with the results returned from the Spider, after
        # it has processed the response.

        # Must return an iterable of Request, or item objects.
        for i in result:
            yield i

    def process_spider_exception(self, response, exception, spider):
        # Called when a spider or process_spider_input() method
        # (from other spider middleware) raises an exception.

        # Should return either None or an iterable of Request or item objects.
        pass

    def process_start_requests(self, start_requests, spider):
        # Called with the start requests of the spider, and works
        # similarly to the process_spider_output() method, except
        # that it doesn’t have a response associated.

        # Must return only requests (not items).
        for r in start_requests:
            yield r

    def spider_opened(self, spider):
        spider.logger.info('Spider opened: %s' % spider.name)


class RealestateScraperDownloaderMiddleware:
    # Not all methods need to be defined. If a method is not defined,
    # scrapy acts as if the downloader middleware does not modify the
    # passed objects.

    @classmethod
    def from_crawler(cls, crawler):
        # This method is used by Scrapy to create your spiders.
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_request(self, request, spider):
        # Called for each request that goes through the downloader
        # middleware.

        # Must either:
        # - return None: continue processing this request
        # - or return a Response object
        # - or return a Request object
        # - or raise IgnoreRequest: process_exception() methods of
        #   installed downloader middleware will be called
        return None

    def process_response(self, request, response, spider):
        # Called with the response returned from the downloader.

        # Must either;
        # - return a Response object
        # - return a Request object
        # - or raise IgnoreRequest
        return response

    def process_exception(self, request, exception, spider):
        # Called when a download handler or a process_request()
        # (from other downloader middleware) raises an exception.

        # Must either:
        # - return None: continue processing this exception
        # - return a Response object: stops process_exception() chain
        # - return a Request object: stops process_exception() chain
        pass

    def spider_opened(self, spider):
        spider.logger.info('Spider opened: %s' % spider.name)


class PoliteLogFormatter(logformatter.LogFormatter):
    def dropped(self, item, exception, response, spider):
        return {
            'level': logging.DEBUG,
            'msg': logformatter.DROPPEDMSG,
            'args': {
                'exception': exception,
                'item': item,
            }
        }