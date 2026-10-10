"""Bronze export: partitioned .jsonl.gz of first-seen listings + run manifest.

Identical output layout to the Scrapy-era CatalogEncounterExporter, so
`pipeline.py upload` and the raw_catalog Athena table keep working:

    runs/spider=catalog/dt=YYYY-MM-DD/region=<slug>/<run-id>.jsonl.gz
    runs/manifests/spider=catalog/dt=YYYY-MM-DD/region=<slug>/manifest_<run-id>.json

Manifests live under a separate prefix so Athena does not pick them up as rows
when scanning the raw_catalog partition.

Both files only appear under their final names once complete: the bronze file
is written as `<run-id>.jsonl.gz.tmp` and the manifest as `….json.tmp`, then
renamed. `pipeline.py upload` skips `.tmp` files, so an upload running during
a scrape can no longer ship (and move away) a half-written partition, and
readers of the manifests never see half-written JSON.
"""
import gzip
import json
import os
from datetime import datetime
from urllib.parse import urlparse, parse_qs

# Path segments that identify the OLX category/listing-type, not a geographic
# location. Stripped before building the region slug so that scraping at
# state, city, or neighbourhood level all produce unambiguous geographic slugs.
# Example: /imoveis/aluguel/estado-es/vitoria/republica
#          -> estado-es_vitoria_republica
_NON_GEO_SEGMENTS = frozenset({
    'imoveis', 'aluguel', 'venda', 'temporada', 'comprar',
})

# v2 added the start URL, invocation, timestamps with offset, early-stop
# settings and OLX's page-1 metadata; v1 fields are unchanged.
MANIFEST_SCHEMA_VERSION = 2


def infer_region(url):
    path = urlparse(url).path.strip('/')
    geo_parts = [p for p in path.split('/') if p and p not in _NON_GEO_SEGMENTS]
    return '_'.join(geo_parts) or 'unknown'


def infer_scope(url):
    """The category part of a start URL's path (e.g. 'imoveis/aluguel') —
    what infer_region drops, so rent and sale scrapes stay distinguishable."""
    path = urlparse(url).path.strip('/')
    return '/'.join(p for p in path.split('/') if p in _NON_GEO_SEGMENTS)


def start_page_of(url):
    """The ?o= page a start URL begins at (1 when absent); > 1 means a resume
    that skipped the newest pages."""
    try:
        return max(1, int(parse_qs(urlparse(url).query).get('o', ['1'])[0]))
    except ValueError:
        return 1


def _now():
    return datetime.now().astimezone()


class EncounterExporter:
    """Only first-seen uids are written to the .jsonl.gz (delta-only bronze);
    already-seen uids are counted in the manifest only. One exporter per
    start URL/region: a multi-region run produces one manifest each."""

    def __init__(self, region, start_url=None, invocation_id=None,
                 early_stop=None):
        self._start_time = _now()
        self._run_id = self._start_time.strftime('%Y%m%dT%H%M%S')
        self._dt = self._start_time.strftime('%Y-%m-%d')
        self._region = region
        self._start_url = start_url
        self._invocation_id = invocation_id
        self._early_stop = early_stop
        self._new_items = 0
        self._duplicate_items = 0
        self._pages_scraped = 0
        self._new_per_page = []
        self._stop_reasons = []
        self._scope_meta = {}

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
        self._file = gzip.open(self._jsonl_path + '.tmp', 'wt', encoding='utf-8')

    def write_new(self, record):
        self._new_items += 1
        self._file.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')

    def count_duplicate(self):
        self._duplicate_items += 1

    def record_scope(self, props):
        """Keep OLX's own description of the scope from the first page: its
        live ad count, the listing it actually served (an unknown scope is
        served as a broader search at the same URL), the sort, and the
        location breadcrumb (display names; the `region` level's value is the
        DDD number)."""
        if self._scope_meta:
            return
        filters = props.get('filters') or {}
        self._scope_meta = {
            'total_of_ads': props.get('totalOfAds'),
            'listing_url': props.get('listingUrl'),
            'sort': filters.get('sort') if isinstance(filters, dict) else None,
            'breadcrumb': [
                {k: loc.get(k) for k in ('level', 'value', 'label')}
                for loc in props.get('locations') or [] if isinstance(loc, dict)
            ],
        }

    def count_page(self, new_items):
        """One catalogue page fully processed, yielding `new_items` new uids."""
        self._pages_scraped += 1
        self._new_per_page.append(new_items)

    def record_stop(self, stop_reason):
        """Why pagination ended for this start URL: 'exhausted', 'early_stop',
        'max_pages', 'no_payload', 'nav_timeout', 'page_cap', 'crashed' or
        'cancelled'. All but 'exhausted' and 'early_stop' mean the scope was
        not fully walked; 'page_cap' means OLX serves no further pages, so
        the scope is denser than one URL can reach."""
        self._stop_reasons.append(stop_reason)

    def close(self):
        """Close the .jsonl.gz, write the manifest, and return it as a dict.
        An all-duplicates run yields no bronze rows — drop the empty .jsonl.gz
        so it is never staged for upload (the manifest still records the run)."""
        self._file.close()
        if self._new_items == 0:
            os.remove(self._jsonl_path + '.tmp')
        else:
            os.replace(self._jsonl_path + '.tmp', self._jsonl_path)

        finished = _now()
        duration_s = round((finished - self._start_time).total_seconds(), 1)
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
            'schema_version': MANIFEST_SCHEMA_VERSION,
            'start_url': self._start_url,
            'scope': infer_scope(self._start_url) if self._start_url else None,
            'start_page': start_page_of(self._start_url) if self._start_url else None,
            'invocation_id': self._invocation_id,
            'started_at': self._start_time.isoformat(timespec='seconds'),
            'finished_at': finished.isoformat(timespec='seconds'),
            'early_stop': self._early_stop,
            'total_of_ads': self._scope_meta.get('total_of_ads'),
            'listing_url': self._scope_meta.get('listing_url'),
            'sort': self._scope_meta.get('sort'),
            'breadcrumb': self._scope_meta.get('breadcrumb'),
            'new_per_page': self._new_per_page,
        }
        tmp_path = self._manifest_path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._manifest_path)
        return manifest
