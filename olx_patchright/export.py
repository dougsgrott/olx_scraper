"""Bronze export: partitioned .jsonl.gz of first-seen listings + run manifest.

Identical output layout to the Scrapy-era CatalogEncounterExporter, so
`pipeline.py upload` and the raw_catalog Athena table keep working:

    runs/spider=catalog/dt=YYYY-MM-DD/region=<slug>/<run-id>.jsonl.gz
    runs/manifests/spider=catalog/dt=YYYY-MM-DD/region=<slug>/manifest_<run-id>.json

Manifests live under a separate prefix so Athena does not pick them up as rows
when scanning the raw_catalog partition.
"""
import gzip
import json
import os
from datetime import datetime
from urllib.parse import urlparse

# Path segments that identify the OLX category/listing-type, not a geographic
# location. Stripped before building the region slug so that scraping at
# state, city, or neighbourhood level all produce unambiguous geographic slugs.
# Example: /imoveis/aluguel/estado-es/vitoria/republica
#          -> estado-es_vitoria_republica
_NON_GEO_SEGMENTS = frozenset({
    'imoveis', 'aluguel', 'venda', 'temporada', 'comprar',
})


def infer_region(url):
    path = urlparse(url).path.strip('/')
    geo_parts = [p for p in path.split('/') if p and p not in _NON_GEO_SEGMENTS]
    return '_'.join(geo_parts) or 'unknown'


class EncounterExporter:
    """Only first-seen uids are written to the .jsonl.gz (delta-only bronze);
    already-seen uids are counted in the manifest only."""

    def __init__(self, region):
        self._start_time = datetime.now()
        self._run_id = self._start_time.strftime('%Y%m%dT%H%M%S')
        self._dt = self._start_time.strftime('%Y-%m-%d')
        self._region = region
        self._new_items = 0
        self._duplicate_items = 0
        self._pages_scraped = 0
        self._stop_reasons = []

        data_dir = os.path.join(
            'runs', 'spider=catalog', f'dt={self._dt}', f'region={self._region}'
        )
        manifest_dir = os.path.join(
            'runs', 'manifests', 'spider=catalog', f'dt={self._dt}', f'region={self._region}'
        )
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(manifest_dir, exist_ok=True)

        self._jsonl_path = os.path.join(data_dir, f'{self._run_id}.jsonl.gz')
        self._manifest_path = os.path.join(
            manifest_dir, f'manifest_{self._run_id}.json'
        )
        self._file = gzip.open(self._jsonl_path, 'wt', encoding='utf-8')

    def write_new(self, record):
        self._new_items += 1
        self._file.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')

    def count_duplicate(self):
        self._duplicate_items += 1

    def record_stop(self, pages_scraped, stop_reason):
        """Per start URL: how many pages were fetched and why pagination ended
        ('exhausted', 'early_stop', 'max_pages', or 'no_payload')."""
        self._pages_scraped += pages_scraped
        self._stop_reasons.append(stop_reason)

    def close(self):
        """Close the .jsonl.gz, write the manifest, and return it as a dict."""
        self._file.close()

        duration_s = round((datetime.now() - self._start_time).total_seconds(), 1)
        manifest = {
            'run_id': self._run_id,
            'region': self._region,
            'dt': self._dt,
            'total_seen': self._new_items + self._duplicate_items,
            'new_items': self._new_items,
            'duplicate_items': self._duplicate_items,
            'pages_scraped': self._pages_scraped,
            'stop_reason': self._stop_reasons,
            'duration_s': duration_s,
        }
        with open(self._manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        return manifest
