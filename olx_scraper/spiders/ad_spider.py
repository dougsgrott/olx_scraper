import json

from scrapy.spiders import Spider
from scrapy import Request
from sqlalchemy.orm import sessionmaker

from ..items import AdItem
from ..models import create_table, db_connect, CatalogDataModel


class AdSpider(Spider):
    name = 'olx_ad'
    handle_httpstatus_list = [403, 404]
    custom_settings = {
        'AUTOTHROTTLE_ENABLED': True,
        'AUTOTHROTTLE_DEBUG': True,
        'DOWNLOAD_DELAY': 3,
        'ROBOTSTXT_OBEY': False,
        'ITEM_PIPELINES': {
            'olx_scraper.pipelines.DefaultValuesAdPipeline': 110,
            'olx_scraper.pipelines.SaveAdDataPipeline': 200,
            'olx_scraper.pipelines.MarkScrapedPipeline': 300,
        },
        'LOG_LEVEL': 'INFO',
        'LOG_FILE': 'logs/olx_ad.log',
        'LOG_FILE_APPEND': False,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        start_urls_arg = kwargs.get('start_urls')
        if isinstance(start_urls_arg, str):
            self.start_urls = [url.strip() for url in start_urls_arg.split(',') if url.strip()]
        elif isinstance(start_urls_arg, list):
            self.start_urls = start_urls_arg
        else:
            self.start_urls = []

    def get_urls_from_db(self):
        engine = db_connect()
        create_table(engine)
        Session = sessionmaker(bind=engine)

        with Session() as session:
            rows_not_scraped = session.query(CatalogDataModel).filter(CatalogDataModel.url_is_scraped == 0).all()
            for row in rows_not_scraped:
                yield Request(
                    url=row.url,
                    callback=self.parse,
                    meta={
                        'playwright': True,
                        'playwright_page_goto_kwargs': {'wait_until': 'domcontentloaded'},
                        'watched_state_fingerprint': row.watched_state_fingerprint,
                    },
                )

    async def start(self):
        if self.start_urls:
            for url in self.start_urls:
                yield Request(url=url, callback=self.parse, meta={'playwright': True, 'playwright_page_goto_kwargs': {'wait_until': 'domcontentloaded'}})
        else:
            # async start() can't `yield from` a sync generator
            for request in self.get_urls_from_db():
                yield request

    _PROMOTED_PROPERTY_NAMES = {
        'category', 'real_estate_type', 'condominio', 'size', 'rooms',
        'bathrooms', 'garage_spaces', 're_features', 're_complex_features',
    }

    def parse(self, response):
        self.logger.info(f"Processing URL: {response.url}")

        ad = self._initial_data(response)
        if ad is None:
            self.logger.warning(
                f"No initial-data JSON at {response.url} (HTTP {response.status}); "
                f"item will be mostly empty."
            )
            ad = {}

        loc = ad.get('location') or {}
        properties = ad.get('properties') or []
        props_by_name = {p.get('name'): p for p in properties if p.get('name')}

        item = AdItem()

        # --- Top-level ad fields ---
        item['title']         = ad.get('subject') or ''
        item['description']   = ad.get('description') or ad.get('body') or ''
        item['code']          = '' if ad.get('listId') is None else str(ad['listId'])
        item['date']          = ad.get('listTime') or ''
        item['url']           = ad.get('canonicalUrl') or response.url
        item['breadcrumb']    = self._breadcrumb_from_ad(ad)

        # --- Dict-valued fields (serialized to JSON by SaveAdDataPipeline) ---
        item['pricing']         = self._pricing_from_ad(ad)
        item['characteristics'] = self._characteristics_from_properties(properties)
        item['details']         = self._details_from_properties(properties)

        # --- Property attributes (apartment/house ads populate these) ---
        item['real_estate_type'] = self._prop(props_by_name, 'real_estate_type')
        item['condominio']       = self._prop(props_by_name, 'condominio')
        item['size']             = self._prop(props_by_name, 'size')
        item['rooms']            = self._prop(props_by_name, 'rooms')
        item['bathrooms']        = self._prop(props_by_name, 'bathrooms')
        item['garage_spaces']    = self._prop(props_by_name, 'garage_spaces')

        # --- Structured location ---
        item['street_address']   = loc.get('address') or ''
        item['full_location']    = ", ".join(
            p for p in (loc.get('neighbourhood'), loc.get('municipality'), loc.get('uf')) if p
        )
        item['neighbourhood']    = loc.get('neighbourhood')
        item['neighbourhood_id'] = loc.get('neighbourhoodId')
        item['municipality']     = loc.get('municipality')
        item['municipality_id']  = loc.get('municipalityId')
        item['uf']               = loc.get('uf')
        item['zipcode']          = loc.get('zipcode')
        item['lat']              = loc.get('mapLati')
        item['lng']              = loc.get('mapLong')
        item['ddd']              = loc.get('ddd')
        item['zone']             = loc.get('zone')
        item['zone_id']          = loc.get('zoneId')
        item['region']           = loc.get('region')

        # Carries the catalog version's fingerprint forward (None when the ad
        # was scraped via the `source: 'urls'` flow, which bypasses the catalog).
        item['watched_state_fingerprint'] = response.meta.get('watched_state_fingerprint')

        return item

    def _initial_data(self, response):
        """Parse the <script id='initial-data'> JSON; return the `ad` dict or None."""
        raw = response.xpath('//script[@id="initial-data"]/@data-json').get()
        if not raw:
            return None
        try:
            return json.loads(raw).get('ad')
        except (ValueError, TypeError):
            return None

    def _breadcrumb_from_ad(self, ad):
        crumbs = ad.get('breadcrumbUrls') or []
        return " > ".join(c.get('label', '') for c in crumbs if c.get('label'))

    def _pricing_from_ad(self, ad):
        out = {}
        price = ad.get('priceValue') or ad.get('price')
        if price:
            out['price'] = price
        if ad.get('priceLabel'):
            out['price_label'] = ad.get('priceLabel')
        if ad.get('oldPrice'):
            out['old_price'] = ad.get('oldPrice')
        return out

    @staticmethod
    def _prop(props_by_name, name):
        p = props_by_name.get(name)
        return p.get('value') if p else None

    def _characteristics_from_properties(self, properties):
        """Build the characteristics dict from ad['properties'] re_features /
        re_complex_features entries."""
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
        """Catch-all dict of ad properties not promoted to scalar columns."""
        return {p.get('label'): p.get('value')
                for p in properties
                if p.get('name') not in self._PROMOTED_PROPERTY_NAMES
                and p.get('label')}
