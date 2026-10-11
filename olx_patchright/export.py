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
import time
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

# OLX renames page-data fields now and then: the location breadcrumb was
# `locations` (2026-07: level/value/label/url per entry) and is `breadcrumbs`
# by 2026-10 (entry shape unverified), so entries are read by any of these
# keys and the raw value is kept whenever that still yields nothing usable.
_CRUMB_KEYS = {
    'level': ('level', 'type', 'kind'),
    'value': ('value', 'id'),
    'label': ('label', 'name', 'text', 'title'),
    'url': ('url', 'href', 'link', 'path', 'pathname'),
}
_SORT_KEYS = ('sort', 'sf', 'order', 'orderBy')
RAW_SAMPLE_CHARS = 2000


def _first(entry, keys):
    for key in keys:
        value = entry.get(key)
        if value not in (None, ''):
            return value
    return None


def _crumb_list(props):
    for key in ('locations', 'breadcrumbs'):
        value = props.get(key)
        if isinstance(value, dict):  # e.g. {"items": [...]}
            value = next((v for v in value.values() if isinstance(v, list)), None)
        if isinstance(value, list) and value:
            return key, value
    return None, None


def parse_breadcrumb(props):
    """(entries, raw, notes): entries normalised to level/value/label/url;
    `raw` is a JSON sample of the source field when the entries lack labels
    or URLs (so the next manifest shows its real shape); notes say what is
    missing, for the run's log."""
    notes = []
    source, crumbs = _crumb_list(props)
    if crumbs is None:
        notes.append('no location breadcrumb (neither `locations` nor `breadcrumbs`)')
        return [], None, notes
    entries = [{field: _first(c, keys) for field, keys in _CRUMB_KEYS.items()}
               for c in crumbs if isinstance(c, dict)]
    raw = None
    if not any(e['label'] and e['url'] for e in entries):
        raw = json.dumps(crumbs, ensure_ascii=False, default=str)[:RAW_SAMPLE_CHARS]
        notes.append(f'`{source}` entries have no recognisable label/url; '
                     f'sample: {raw[:400]}')
    return entries, raw, notes


def parse_sort(props):
    filters = props.get('filters')
    if not isinstance(filters, dict):
        return None, ['no `filters` object']
    sort = _first(filters, _SORT_KEYS)
    if sort is None:
        return None, [f'no sort in `filters` (keys: {", ".join(sorted(filters))})']
    return sort, []


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
        self._region = region
        # run_id has one-second resolution: a second run of the same region
        # within that second (a blocked URL ends instantly, then a retry)
        # would overwrite the first's files — wait for a fresh second.
        while True:
            self._start_time = _now()
            self._run_id = self._start_time.strftime('%Y%m%dT%H%M%S')
            self._dt = self._start_time.strftime('%Y-%m-%d')
            if not self._taken():
                break
            time.sleep(0.2)
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

    def _taken(self):
        base = os.path.join('runs', 'manifests', 'spider=catalog', f'dt={self._dt}',
                            f'region={self._region}', f'manifest_{self._run_id}')
        data = os.path.join('runs', 'spider=catalog', f'dt={self._dt}',
                            f'region={self._region}', f'{self._run_id}.jsonl.gz')
        return any(os.path.exists(p) for p in (base + '.json', base + '.json.tmp',
                                               data, data + '.tmp'))

    def write_new(self, record):
        self._new_items += 1
        self._file.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')

    def count_duplicate(self):
        self._duplicate_items += 1

    def record_scope(self, props):
        """Keep OLX's own description of the scope from the first page: its
        live ad count, the listing it actually served (an unknown scope is
        served as a broader search at the same URL), the sort, the location
        breadcrumb (display names, each with the scope URL it names; the DDD
        is in the macro-region entry) and OLX's location text. Returns notes
        on whatever OLX's page data no longer carries."""
        if self._scope_meta:
            return []
        breadcrumb, raw, notes = parse_breadcrumb(props)
        sort, sort_notes = parse_sort(props)
        location_text = props.get('adLocation')
        self._scope_meta = {
            'total_of_ads': props.get('totalOfAds'),
            'listing_url': props.get('listingUrl'),
            'sort': sort,
            'breadcrumb': breadcrumb,
            'breadcrumb_raw': raw,
            'location_text': location_text.strip(' -') if isinstance(location_text, str)
            else None,
        }
        return notes + sort_notes

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
            'breadcrumb_raw': self._scope_meta.get('breadcrumb_raw'),
            'location_text': self._scope_meta.get('location_text'),
            'new_per_page': self._new_per_page,
        }
        tmp_path = self._manifest_path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._manifest_path)
        return manifest
