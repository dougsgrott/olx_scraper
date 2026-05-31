import gzip
import json
import os
from datetime import datetime
from urllib.parse import urlparse

from scrapy import signals

# Path segments that identify the OLX category/listing-type, not a geographic
# location. Stripped before building the region slug so that scraping at
# state, city, or neighbourhood level all produce unambiguous geographic slugs.
# Example: /imoveis/aluguel/estado-es/vitoria/republica
#          → estado-es_vitoria_republica
_NON_GEO_SEGMENTS = frozenset({
    'imoveis', 'aluguel', 'venda', 'temporada', 'comprar',
})


class CatalogEncounterExporter:
    """Writes delta catalog items to a partitioned .jsonl.gz and a run manifest.

    Only items that pass ChangeDetectionCatalogPipeline (new or changed
    fingerprint) are written to the .jsonl.gz. Unchanged items caught by
    DropItem are counted in the manifest only.

    Output layout:
        runs/spider=catalog/dt=YYYY-MM-DD/region=<slug>/<run-id>.jsonl.gz
        runs/manifests/spider=catalog/dt=YYYY-MM-DD/region=<slug>/manifest_<run-id>.json

    Manifests are written to a separate prefix so Athena does not pick them
    up as rows when scanning the raw_catalog partition.
    """

    def __init__(self):
        self._file = None
        self._new_items = 0
        self._changed_items = 0
        self._unchanged_items = 0
        self._start_time = None
        self._run_id = None
        self._region = None
        self._dt = None
        self._manifest_path = None

    @classmethod
    def from_crawler(cls, crawler):
        ext = cls()
        crawler.signals.connect(ext.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(ext.item_scraped, signal=signals.item_scraped)
        crawler.signals.connect(ext.item_dropped, signal=signals.item_dropped)
        crawler.signals.connect(ext.spider_closed, signal=signals.spider_closed)
        return ext

    def spider_opened(self, spider):
        if spider.name != 'olx_catalog':
            return

        self._start_time = datetime.now()
        self._run_id = self._start_time.strftime('%Y%m%dT%H%M%S')
        self._dt = self._start_time.strftime('%Y-%m-%d')
        self._region = self._infer_region(spider)

        data_dir = os.path.join(
            'runs', 'spider=catalog', f'dt={self._dt}', f'region={self._region}'
        )
        manifest_dir = os.path.join(
            'runs', 'manifests', 'spider=catalog', f'dt={self._dt}', f'region={self._region}'
        )
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(manifest_dir, exist_ok=True)

        jsonl_path = os.path.join(data_dir, f'{self._run_id}.jsonl.gz')
        self._manifest_path = os.path.join(manifest_dir, f'manifest_{self._run_id}.json')
        self._file = gzip.open(jsonl_path, 'wt', encoding='utf-8')

    @staticmethod
    def _infer_region(spider):
        urls = getattr(spider, 'start_urls', [])
        if not urls:
            return 'unknown'
        url = urls[0] if isinstance(urls, (list, tuple)) else str(urls)
        path = urlparse(url).path.strip('/')
        geo_parts = [p for p in path.split('/') if p and p not in _NON_GEO_SEGMENTS]
        return '_'.join(geo_parts) or 'unknown'

    def item_scraped(self, item, response, spider):
        if spider.name != 'olx_catalog' or self._file is None:
            return

        if item.get('_is_new_version'):
            self._new_items += 1
        else:
            self._changed_items += 1

        record = {k: v for k, v in item.items() if k != '_is_new_version'}
        self._file.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')

    def item_dropped(self, item, response, spider, exception):
        if spider.name != 'olx_catalog':
            return
        self._unchanged_items += 1

    def spider_closed(self, spider, reason):
        if spider.name != 'olx_catalog' or self._file is None:
            return

        self._file.close()
        self._file = None

        duration_s = round((datetime.now() - self._start_time).total_seconds(), 1)
        total_seen = self._new_items + self._changed_items + self._unchanged_items

        manifest = {
            'run_id': self._run_id,
            'region': self._region,
            'dt': self._dt,
            'total_seen': total_seen,
            'new_items': self._new_items,
            'changed_items': self._changed_items,
            'unchanged_items': self._unchanged_items,
            'duration_s': duration_s,
        }

        with open(self._manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
