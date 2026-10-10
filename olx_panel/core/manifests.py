"""Run manifests: indexing them into panel.sqlite, and reading them back as
runs with an outcome.

Manifests are the scrape history: one per start URL per run, written by
olx_patchright/export.py under runs/ (outbox) and moved verbatim to
runs_uploaded/ (archive) by `pipeline.py upload`. Their `new_items` sum to
exactly the catalog_data row count, so they are also the exact per-scope
provenance.

The index stores raw manifest facts only, keyed by (region, run_id) — taken
from the directory and file names, so a moved file is recognised without
re-reading it. Everything interpretive (outcome, freshness credit, legacy
attachment, invocation grouping) is derived on read, so rule or
overrides.yaml changes apply without reindexing.
"""
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse

from olx_patchright.plan import path_of

_MANIFEST_RE = re.compile(r'^manifest_(\d{8}T\d{6})\.json$')

# Outcomes. Only complete and caught_up runs certify freshness: everything
# newer than where they stopped is in the DB.
COMPLETE = 'complete'      # exhausted: walked to the last page
CAUGHT_UP = 'caught_up'    # early_stop before the page cap
CAPPED = 'capped'          # page_cap, or a legacy run that ran past it
PARTIAL = 'partial'        # max_pages set by the user
BLOCKED = 'blocked'        # no_payload (403 => Cloudflare; re-WARM)
FAILED = 'failed'          # nav_timeout, crashed
CANCELLED = 'cancelled'
UNKNOWN = 'unknown'        # [] or no stop_reason (legacy crash/interrupt)
RESUME = 'resume'          # started past page 1: the newest pages were skipped
SCOPE_MISMATCH = 'scope_mismatch'  # OLX served another scope / off-tree region
CREDITED = frozenset({COMPLETE, CAUGHT_UP})
INCOMPLETE = frozenset({CAPPED, PARTIAL, BLOCKED, FAILED, CANCELLED, UNKNOWN})

_COLUMNS = (
    'region', 'run_id', 'dt', 'started_at', 'finished_at', 'duration_s',
    'pages_scraped', 'total_seen', 'new_items', 'duplicate_items',
    'stop_reason', 'schema_version', 'start_url', 'scope', 'start_page',
    'invocation_id', 'early_stop', 'total_of_ads', 'listing_url', 'sort',
    'breadcrumb', 'new_per_page', 'store', 'file_path', 'indexed_at',
)
_JSON_COLUMNS = ('stop_reason', 'early_stop', 'breadcrumb', 'new_per_page')


def walk_manifests(runs_dirs):
    """Yield (store, region, run_id, path) for every manifest file."""
    for store, root in runs_dirs:
        base = os.path.join(root, 'manifests', 'spider=catalog')
        try:
            dt_dirs = list(os.scandir(base))
        except FileNotFoundError:
            continue
        for dt_dir in dt_dirs:
            if not (dt_dir.name.startswith('dt=') and dt_dir.is_dir()):
                continue
            for region_dir in os.scandir(dt_dir.path):
                if not (region_dir.name.startswith('region=') and region_dir.is_dir()):
                    continue
                region = region_dir.name.split('=', 1)[1]
                for entry in os.scandir(region_dir.path):
                    m = _MANIFEST_RE.match(entry.name)
                    if m and entry.is_file():
                        yield store, region, m.group(1), entry.path


def _local_from_run_id(run_id):
    """Legacy manifests only carry run_id: the start time, naive local."""
    return datetime.strptime(run_id, '%Y%m%dT%H%M%S').astimezone()


def _row(data, region, run_id, store, path):
    started = data.get('started_at') or \
        _local_from_run_id(run_id).isoformat(timespec='seconds')
    finished = data.get('finished_at')
    if not finished and data.get('duration_s') is not None:
        finished = (datetime.fromisoformat(started) +
                    timedelta(seconds=data['duration_s'])).isoformat(timespec='seconds')
    row = {
        'region': region, 'run_id': run_id, 'dt': data.get('dt'),
        'started_at': started, 'finished_at': finished,
        'duration_s': data.get('duration_s'),
        'pages_scraped': data.get('pages_scraped'),
        'total_seen': data.get('total_seen'),
        'new_items': data.get('new_items'),
        'duplicate_items': data.get('duplicate_items'),
        'stop_reason': data.get('stop_reason'),
        'schema_version': data.get('schema_version', 1),
        'start_url': data.get('start_url'), 'scope': data.get('scope'),
        'start_page': data.get('start_page'),
        'invocation_id': data.get('invocation_id'),
        'early_stop': data.get('early_stop'),
        'total_of_ads': data.get('total_of_ads'),
        'listing_url': data.get('listing_url'), 'sort': data.get('sort'),
        'breadcrumb': data.get('breadcrumb'),
        'new_per_page': data.get('new_per_page'),
        'store': store, 'file_path': path,
        'indexed_at': datetime.now().astimezone().isoformat(timespec='seconds'),
    }
    for col in _JSON_COLUMNS:
        if row[col] is not None:
            row[col] = json.dumps(row[col], ensure_ascii=False)
    return row


@dataclass
class IndexResult:
    added: int = 0
    moved: int = 0
    missing: int = 0
    unreadable: tuple = ()

    @property
    def changed(self):
        return bool(self.added or self.moved or self.missing)


def index_manifests(store, runs_dirs):
    """Bring the manifest index in line with the files on disk."""
    result = IndexResult()
    unreadable = []
    placeholders = ', '.join('?' for _ in _COLUMNS)
    insert = f'INSERT INTO manifest ({", ".join(_COLUMNS)}) VALUES ({placeholders})'
    with store.connect() as conn:
        known = {(r['region'], r['run_id']): (r['file_path'], r['file_missing'])
                 for r in conn.execute(
                     'SELECT region, run_id, file_path, file_missing FROM manifest')}
        seen = set()
        for store_name, region, run_id, path in walk_manifests(runs_dirs):
            key = (region, run_id)
            if key in seen:  # caught mid-move, present in both trees
                continue
            seen.add(key)
            if key in known:
                old_path, missing = known[key]
                if old_path != path or missing:
                    conn.execute(
                        'UPDATE manifest SET file_path = ?, store = ?, file_missing = 0 '
                        'WHERE region = ? AND run_id = ?',
                        (path, store_name, region, run_id))
                    result.moved += 1
                continue
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
            except (OSError, ValueError) as exc:
                unreadable.append((path, str(exc)))
                continue
            row = _row(data, region, run_id, store_name, path)
            conn.execute(insert, [row[c] for c in _COLUMNS])
            result.added += 1
        for key, (_, missing) in known.items():
            if key not in seen and not missing:
                conn.execute('UPDATE manifest SET file_missing = 1 '
                             'WHERE region = ? AND run_id = ?', key)
                result.missing += 1
        if result.changed:
            version = int(store.get_meta(conn, 'manifest_version', 0)) + 1
            store.set_meta(conn, 'manifest_version', version)
    result.unreadable = tuple(unreadable)
    return result


@dataclass
class Run:
    """One indexed manifest, read back with derived fields."""
    region: str
    run_id: str
    dt: str
    started_at: datetime
    finished_at: datetime
    duration_s: float
    pages_scraped: int
    total_seen: int
    new_items: int
    duplicate_items: int
    stop_reason: list
    schema_version: int
    start_url: str
    scope: str
    start_page: int
    invocation_id: str
    early_stop: dict
    total_of_ads: int
    listing_url: str
    sort: str
    breadcrumb: list
    new_per_page: list
    store: str
    file_path: str
    file_missing: bool
    node_path: str = None      # the location path the region names
    attach_path: str = None    # tree node it counts under (== node_path unless off-tree)
    off_tree: bool = False
    outcome: str = None
    note: str = None
    invocation: str = None

    @property
    def credited(self):
        return self.outcome in CREDITED

    @property
    def reason(self):
        return self.stop_reason[-1] if self.stop_reason else None


def _parse_ts(value):
    return datetime.fromisoformat(value) if value else None


def load_runs(store):
    with store.connect() as conn:
        rows = conn.execute('SELECT * FROM manifest').fetchall()
    runs = []
    for r in rows:
        d = dict(r)
        for col in _JSON_COLUMNS:
            if d[col] is not None:
                d[col] = json.loads(d[col])
        d['started_at'] = _parse_ts(d['started_at'])
        d['finished_at'] = _parse_ts(d['finished_at'])
        d['file_missing'] = bool(d['file_missing'])
        d.pop('indexed_at', None)
        runs.append(Run(**d))
    return runs


def classify(run, page_cap):
    """The run's outcome from its own facts (see the module constants)."""
    reasons = run.stop_reason
    if not reasons:
        return UNKNOWN
    if (run.start_page or 1) > 1:
        return RESUME
    if run.listing_url and run.start_url and \
            urlparse(run.start_url).path.strip('/') != run.listing_url.strip('/'):
        return SCOPE_MISMATCH
    reason = reasons[-1]
    if reason == 'exhausted':
        return COMPLETE
    if reason == 'early_stop':
        # Before the page_cap stop reason existed, a capped scope paged on
        # through re-served copies of page 100 until patience fired.
        return CAPPED if (run.pages_scraped or 0) > page_cap else CAUGHT_UP
    return {
        'page_cap': CAPPED, 'max_pages': PARTIAL, 'no_payload': BLOCKED,
        'nav_timeout': FAILED, 'crashed': FAILED, 'cancelled': CANCELLED,
    }.get(reason, UNKNOWN)


def interpret(runs, tree_paths, page_cap, overrides, gap_s):
    """Fill node/attach paths, outcome and invocation on every run.

    An off-tree region (a hand-written start URL OLX accepted but its sitemap
    doesn't list) counts under its nearest existing ancestor (or the one
    overrides.yaml names) and never earns freshness credit: what it actually
    filtered is unknown (estado-es/norte-do-espirito-santo/serra served ads
    from across DDD 27)."""
    for run in runs:
        run.node_path = path_of(run.region)
        if run.node_path in tree_paths:
            run.attach_path = run.node_path
        else:
            run.off_tree = True
            run.attach_path = overrides.legacy_regions.get(run.region) or \
                _nearest_ancestor(run.node_path, tree_paths)
        flagged = overrides.legacy_runs.get((run.region, run.run_id))
        if flagged:
            run.outcome, run.note = flagged
        elif run.off_tree:
            run.outcome = SCOPE_MISMATCH
        else:
            run.outcome = classify(run, page_cap)
    _group_invocations(runs, gap_s)
    return runs


def _nearest_ancestor(path, tree_paths):
    segments = path.split('/')
    for i in range(len(segments) - 1, 0, -1):
        candidate = '/'.join(segments[:i])
        if candidate in tree_paths:
            return candidate
    return None


def _group_invocations(runs, gap_s):
    """v2 manifests name their invocation; legacy ones are clustered: one
    invocation per stretch of consecutive URLs without a long gap."""
    legacy = sorted((r for r in runs if not r.invocation_id),
                    key=lambda r: r.started_at)
    key = last_end = None
    for run in legacy:
        end = run.finished_at or run.started_at
        if key is None or (run.started_at - last_end).total_seconds() > gap_s:
            key = f'legacy-{run.run_id}'
            last_end = end
        else:
            last_end = max(last_end, end)
        run.invocation = key
    for run in runs:
        if run.invocation_id:
            run.invocation = run.invocation_id
