"""Scrape planning over the harvested location tree (no network, no DB).

One home for the rules that the density prober, the start-URL generator and
the control panel must agree on:

  - node status from the prober's counts (classify_node), using the
    total_of_ads sentinels PADDED / UNSCOPED written by
    scripts/probe_density.py;
  - the start-URL plan for a set of subtree roots (build_plan): leaf, dense
    and remainder kinds, children before their remainder parent;
  - alias twins (find_aliases): the same place listed twice in OLX's
    sitemap, e.g. estado-sp/.../centro/aclimacao and .../zona-sul/aclimacao,
    which serve identical results — scraping both only re-reads duplicates;
  - the path <-> start URL <-> bronze region mappings.

Rows are any objects with .path, .parent_path, .total_of_ads and .probed_at
(LocationRow, or namedtuples in tests).
"""
import re
import unicodedata
from collections import defaultdict
from urllib.parse import urlencode

BASE = 'https://www.olx.com.br'
DEFAULT_SCOPE = 'imoveis/aluguel'
PAGE_SIZE = 50
# Measured 2026-07-12 (scripts/measure_page_cap.py): ?o=101 silently clamps
# to page 100. Re-measure if OLX changes pagination; catalog.py stops a start
# URL with stop_reason 'page_cap' instead of requesting pages past it.
PAGE_CAP = 100
# The most ads one scope can serve (PAGE_CAP x PAGE_SIZE); denser scopes are
# split into their children.
DEFAULT_THRESHOLD = PAGE_CAP * PAGE_SIZE

# Sentinels stored in locations.total_of_ads by scripts/probe_density.py.
# PADDED: OLX pads a scarce scope with a broader search, so the served count
# is a lie. UNSCOPED: the URL's location segment filters nothing.
PADDED = -1
UNSCOPED = -2


def norm(value):
    """Accent-folded kebab-case, the same shape as OLX slugs."""
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore') \
                       .decode()
    return re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')


def is_usable(node):
    """Probed with a real count (not a sentinel)."""
    return (node.probed_at is not None and node.total_of_ads is not None
            and node.total_of_ads >= 0)


def classify_node(node, kids, threshold=DEFAULT_THRESHOLD):
    """'unprobed' | 'padded' | 'unscoped' | 'leaf' | 'split' | 'dense'.

    'dense' is over the threshold with nothing usable to split into (no
    children, or only padded/unscoped/unprobed ones): it can only be scraped
    whole, knowing pagination truncates it."""
    count = node.total_of_ads
    if node.probed_at is None or count is None:
        return 'unprobed'
    if count == PADDED:
        return 'padded'
    if count == UNSCOPED:
        return 'unscoped'
    if count <= threshold:
        return 'leaf'
    return 'split' if any(is_usable(k) for k in kids) else 'dense'


def find_aliases(rows):
    """Map each non-canonical alias twin's path to its canonical twin.

    Twins share UF, depth-2 ancestor, depth and slug, and report the same
    positive count; the first path in sort order is canonical. Same-named
    places with different counts (several `centro`s) are distinct places."""
    groups = defaultdict(list)
    for r in rows:
        segments = r.path.split('/')
        if len(segments) < 3 or not is_usable(r) or r.total_of_ads == 0:
            continue
        key = (segments[0], segments[1], len(segments), segments[-1],
               r.total_of_ads)
        groups[key].append(r.path)
    aliases = {}
    for paths in groups.values():
        if len(paths) > 1:
            canonical, *twins = sorted(paths)
            aliases.update((twin, canonical) for twin in twins)
    return aliases


def build_plan(rows, roots, threshold=DEFAULT_THRESHOLD, alias_of=None):
    """Return (plan, warnings): plan is a list of (path, count, kind) in
    scrape order, kind in 'leaf' | 'dense' | 'remainder'.

      - leaf       count <= threshold: scraped directly.
      - dense      over the threshold with nothing usable to split into:
                   scraped knowing pagination truncates it.
      - remainder  a split node itself, emitted AFTER its children: ads
                   without a neighbourhood only appear in the parent scope,
                   and uid dedup is first-seen-wins, so children must claim
                   their ads first.

    PADDED/UNSCOPED nodes are skipped (the remainder covers them); unprobed
    ones are skipped with a warning. Roots nested under another root are
    dropped (the ancestor's plan already covers them). With `alias_of` (see
    find_aliases), only the first twin of each alias group in scrape order
    is kept — whichever twin the roots happen to reach."""
    by_path = {r.path: r for r in rows}
    kids_of = defaultdict(list)
    for r in rows:
        if r.parent_path:
            kids_of[r.parent_path].append(r)
    alias_of = alias_of or {}

    plan = []
    warnings = []

    def emit(node):
        kids = kids_of[node.path]
        kind = classify_node(node, kids, threshold)
        if kind == 'unprobed':
            warnings.append(f'{node.path}: unprobed, skipped')
        elif kind == 'leaf' or kind == 'dense':
            plan.append((node.path, node.total_of_ads, kind))
        elif kind == 'split':
            for kid in sorted(kids, key=lambda k: k.path):
                emit(kid)
            plan.append((node.path, node.total_of_ads, 'remainder'))

    for root in _outermost(roots):
        emit(by_path[root])

    if alias_of:
        groups_seen = set()
        deduped = []
        for entry in plan:
            group = alias_of.get(entry[0], entry[0])
            if group not in groups_seen:
                groups_seen.add(group)
                deduped.append(entry)
        plan = deduped
    return plan, warnings


def _outermost(roots):
    """Distinct roots in input order, minus any nested under another root."""
    distinct = list(dict.fromkeys(roots))
    return [r for r in distinct
            if not any(r != other and r.startswith(other + '/')
                       for other in distinct)]


def start_url(path, scope=DEFAULT_SCOPE, newest_first=True, page=None):
    """Catalog start URL for a location path. newest_first adds ?sf=1, which
    early stop relies on (new ads front-load); page > 1 adds ?o=N."""
    query = {}
    if newest_first:
        query['sf'] = 1
    if page and page > 1:
        query['o'] = page
    url = f"{BASE}/{scope.strip('/')}/{path.strip('/')}"
    return f'{url}?{urlencode(query)}' if query else url


def region_of(path):
    """Bronze region slug of a location path; equals
    export.infer_region(start_url(path))."""
    return path.strip('/').replace('/', '_')


def path_of(region):
    """Location path of a bronze region slug (slugs never contain '_')."""
    return region.replace('_', '/')
