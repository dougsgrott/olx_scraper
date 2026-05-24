import json
import pprint
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import scrapy
from scrapy.spiders import Spider, signals

from ..items import CatalogItem


class CatalogSpider(Spider):
    name = 'olx_catalog'
    handle_httpstatus_list = [403, 404]

    custom_settings = {
        'AUTOTHROTTLE_ENABLED': True,
        'AUTOTHROTTLE_DEBUG': False,
        'DOWNLOAD_DELAY': 3,
        'ROBOTSTXT_OBEY': False,
        'ITEM_PIPELINES': {
           'olx_scraper.pipelines.ChangeDetectionCatalogPipeline': 100,
           'olx_scraper.pipelines.DefaultValuesCatalogPipeline': 110,
           'olx_scraper.pipelines.SaveCatalogDataPipeline': 200,
        },
        'LOG_LEVEL': 'DEBUG',
        'LOG_FILE': 'logs/olx_catalog.log',
        'LOG_FILE_APPEND': False,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'start_urls' in kwargs:
            self.start_urls = kwargs.get('start_urls').split(',')

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super(CatalogSpider, cls).from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider.handle_spider_closed, signals.spider_closed)
        crawler.signals.connect(spider.handle_spider_opened, signals.spider_opened)
        return spider

    def handle_spider_opened(self, spider):
        self.logger.info(f"Spider opened: {spider.name}")

    def handle_spider_closed(self, spider, reason):
        stats = self.crawler.stats.get_stats()
        self.logger.info("Scraping Stats:\n" + pprint.pformat(stats))
        self.logger.info(f"Spider Closed. Reason: {reason}")

    def _playwright_request(self, url):
        """Build a request routed through Playwright. Content is captured at
        `domcontentloaded` (OLX server-renders its listings). We deliberately
        avoid `playwright_page_methods`: scrapy-playwright runs a
        `wait_for_load_state()` after each one, and waiting for the full `load`
        event is unreliable on OLX's ad-heavy pages -- a single hanging tracker
        stalls it past the navigation timeout."""
        return scrapy.Request(
            url=url,
            callback=self.parse,
            meta={
                'playwright': True,
                'playwright_page_goto_kwargs': {'wait_until': 'domcontentloaded'},
            },
        )

    async def start(self):
        if not hasattr(self, 'start_urls'):
            raise AttributeError("Spider was not started with start_urls. Please provide them with the -a flag (e.g., -a start_urls=http://...).")

        for url in self.start_urls:
            self.logger.info(f"Scheduling request for: {url}")
            yield self._playwright_request(url)


    _PROMOTED_PROPERTY_NAMES = {
        'category', 'real_estate_type', 'condominio', 'iptu', 'size',
        'rooms', 'bathrooms', 'garage_spaces',
        're_features', 're_complex_features',
    }
    _BOOL_BADGE_FIELDS = (
        ('is_featured', 'isFeatured'),
        ('fixed_on_top', 'fixedOnTop'),
        ('price_reduction_badge', 'priceReductionBadge'),
        ('has_real_estate_highlight', 'hasRealEstateHighlight'),
    )

    # 1. LISTING PARSE — driven by the page's __NEXT_DATA__ JSON
    def parse(self, response):
        page_props = self._page_props(response)

        if page_props is None:
            dump_path = f"logs/blocked_{response.status}.html"
            with open(dump_path, 'w', encoding='utf-8') as fh:
                fh.write(response.text)
            self.logger.warning(
                f"No __NEXT_DATA__ at {response.url} (HTTP {response.status}, "
                f"{len(response.text)} chars). Saved served page to {dump_path}."
            )
            return

        for ad in page_props.get('ads') or []:
            yield self._build_item(ad)

        yield from self.paginate(response, page_props)

    def _page_props(self, response):
        """Parse <script id='__NEXT_DATA__'> and return props.pageProps (or None)."""
        raw = response.xpath('//script[@id="__NEXT_DATA__"]/text()').get()
        if not raw:
            return None
        try:
            return json.loads(raw).get('props', {}).get('pageProps')
        except (ValueError, TypeError):
            return None

    def _build_item(self, ad):
        loc = ad.get('locationDetails') or {}
        properties = ad.get('properties') or []
        props_by_name = {p.get('name'): p for p in properties if p.get('name')}

        item = CatalogItem()
        list_id = ad.get('listId')
        uid_code = '' if list_id is None else str(list_id)

        # listId is globally unique on OLX, so it doubles as both code and uid
        # (ChangeDetectionCatalogPipeline keys off uid to detect price changes
        # across snapshots).
        item['uid']   = uid_code
        item['code']  = uid_code
        item['title'] = ad.get('subject') or ad.get('title') or ''
        item['url']   = ad.get('friendlyUrl') or ad.get('url') or ''

        # date: origListTime is unix seconds; render as ISO for human readability.
        ts = ad.get('origListTime') or ad.get('date')
        item['date'] = (
            datetime.fromtimestamp(ts).isoformat(timespec='seconds')
            if isinstance(ts, (int, float)) else (ts or '')
        )

        # location string (rendered) + structured components from locationDetails
        item['location']      = ad.get('location') or ''
        item['neighbourhood'] = loc.get('neighbourhood')
        item['municipality']  = loc.get('municipality')
        item['uf']            = loc.get('uf')
        item['ddd']           = loc.get('ddd')

        # Dict-valued fields (serialized to JSON by SaveCatalogDataPipeline)
        item['pricing']         = self._pricing_from_ad(ad)
        item['characteristics'] = self._characteristics_from_properties(properties)
        item['details']         = self._details_from_properties(properties)
        item['badges']          = [name for name, key in self._BOOL_BADGE_FIELDS if ad.get(key)]

        # Promoted property attributes
        item['real_estate_type'] = self._prop(props_by_name, 'real_estate_type')
        item['condominio']       = self._prop(props_by_name, 'condominio')
        item['iptu']             = self._prop(props_by_name, 'iptu')
        item['size']             = self._prop(props_by_name, 'size')
        item['rooms']            = self._prop(props_by_name, 'rooms')
        item['bathrooms']        = self._prop(props_by_name, 'bathrooms')
        item['garage_spaces']    = self._prop(props_by_name, 'garage_spaces')

        # Pricing extras
        old_price = ad.get('oldPrice')
        item['old_price'] = old_price if old_price else None

        # Ad-level flags / metadata
        item['category_name']             = ad.get('categoryName') or ad.get('category')
        item['professional_ad']           = 1 if ad.get('professionalAd') else 0
        item['is_featured']               = 1 if ad.get('isFeatured') else 0
        item['fixed_on_top']              = 1 if ad.get('fixedOnTop') else 0
        item['price_reduction_badge']     = 1 if ad.get('priceReductionBadge') else 0
        item['has_real_estate_highlight'] = 1 if ad.get('hasRealEstateHighlight') else 0

        if item['uid']=='' and item['title']=='':
            return None
        return item

    def _pricing_from_ad(self, ad):
        out = {}
        price = ad.get('priceValue') or ad.get('price')
        if price:
            out['price'] = price
        if ad.get('oldPrice'):
            out['old_price'] = ad.get('oldPrice')
        return out

    @staticmethod
    def _prop(props_by_name, name):
        p = props_by_name.get(name)
        return p.get('value') if p else None

    def _characteristics_from_properties(self, properties):
        """re_features + re_complex_features. Catalog gives them as a single
        comma-separated string (no `values` list like ad-detail pages), so we
        always split on comma."""
        out = {}
        for p in properties:
            if p.get('name') in ('re_features', 're_complex_features'):
                label = p.get('label') or p.get('name')
                vals = p.get('values') or [
                    v.strip() for v in (p.get('value') or '').split(',') if v.strip()
                ]
                out[label] = vals
        return out

    def _details_from_properties(self, properties):
        return {p.get('label'): p.get('value')
                for p in properties
                if p.get('name') not in self._PROMOTED_PROPERTY_NAMES
                and p.get('label')}

    # 2. PAGINATION — driven by JSON pagination metadata, not a fragile regex
    def paginate(self, response, page_props):
        total_ads = page_props.get('totalOfAds')
        page_size = page_props.get('pageSize')
        current_page = page_props.get('pageIndex')

        if not (isinstance(total_ads, int) and isinstance(page_size, int) and page_size):
            self.logger.info(
                f"No pagination metadata on {response.url}; treating as a single page."
            )
            return

        total_pages = (total_ads + page_size - 1) // page_size
        if not isinstance(current_page, int) or current_page >= total_pages:
            return

        # Build next page URL with ?o=N+1
        parsed_url = urlparse(response.url)
        query_params = parse_qs(parsed_url.query)
        query_params['o'] = [str(current_page + 1)]
        new_query = urlencode(query_params, doseq=True)
        next_url = urlunparse(parsed_url._replace(query=new_query))

        self.logger.info(
            f"Paginating: page {current_page + 1}/{total_pages} -> {next_url}"
        )
        yield self._playwright_request(next_url)
