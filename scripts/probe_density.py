"""Probe per-scope ad density (totalOfAds) over the harvested location tree.

Walks `locations` (scraped_data/locations.sqlite) top-down, loading page 1 of
each node's catalog scope through the warmed patchright profile and recording
totalOfAds. A node's children are visited only when its own count exceeds
--threshold (≈ how many ads pagination can actually serve), so sparse regions
cost one fetch and only dense ones are drilled into. The probed counts are the
density map that picks scrape granularity:

  - count <= threshold      -> scrape leaf: use this node as a start URL
  - count >  threshold,
    has children            -> split: scrape its children instead
  - count >  threshold,
    no children             -> cannot subdivide by location; needs query
                               filters (price bands etc.) or acceptance of
                               truncation
  - count == -1 (PADDED)    -> OLX zero-result padding: the scope is genuinely
                               scarce but its served count is a broader
                               search's; detected as child count > parent
                               count (impossible for a subset scope)
  - count == -2 (UNSCOPED)  -> the URL segment filters nothing: OLX serves an
                               ancestor's result at this URL (e.g. every city
                               under estado-sc/norte-de-santa-catarina).
                               Detected two ways: the served ads' own
                               locationDetails don't match the slug, or two
                               siblings both report ~the parent's full count
                               (jointly impossible). Plan such branches at
                               their deepest functional ancestor.

Counts are scope-specific: probing under a different --scope overwrites
total_of_ads with that scope's numbers. Already-probed nodes are skipped
(cached) unless --refresh, so an interrupted run resumes where it stopped.
Do NOT run while a scrape is running (single-instance browser profile).

    uv run python scripts/probe_density.py                      # from all 27 states
    uv run python scripts/probe_density.py estado-es            # subtree root(s)
    uv run python scripts/probe_density.py --report             # print map, no browser
    uv run python scripts/probe_density.py --limit 10           # cap fetches (testing)
"""
import argparse
import os
import sys
import time
from collections import deque
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# PADDED / UNSCOPED are the total_of_ads sentinels this prober writes (see the
# module docstring); they and the status rules are shared through plan.py.
from olx_patchright.plan import (  # noqa: E402
    BASE, DEFAULT_SCOPE, DEFAULT_THRESHOLD, PADDED, UNSCOPED, classify_node,
    norm,
)

PAGE_DELAY_S = 6  # same politeness delay as the catalog scraper
# A probed count within this fraction of the parent's marks a near-parent
# sibling; two of them under one parent are jointly impossible => UNSCOPED.
NEAR_PARENT = 0.98
# Ignore the near-parent rule under tiny parents, where one busy child
# legitimately holding ~all ads is common and drift dominates.
NEAR_PARENT_MIN = 500
# Above parent by more than this factor => zero-result padding (padding jumps
# to an ancestor scope, far beyond the parent); within it, the overshoot is
# live-count drift against a stale cached parent and the near-parent sibling
# logic decides instead.
PADDING_JUMP = 1.05


def children_of(factory, path):
    from olx_patchright.db import LocationRow
    with factory() as session:
        return session.query(LocationRow).filter_by(parent_path=path) \
                      .order_by(LocationRow.path).all()


def _slug_names_place(slug):
    """Whether the slug names one municipality/neighbourhood that the served
    ads' own locationDetails can be checked against (aggregates cannot)."""
    if slug in ('outras-cidades', 'todas-as-cidades') or '-e-regiao' in slug:
        return False
    return not slug.startswith(('regiao-', 'zona-', 'grande-'))


def _content_scoped(props, slug):
    """False when the sampled ads' own locations contradict the slug — OLX is
    serving a broader search at this URL. Containment (not just equality)
    tolerates composite slugs like ibirapuera-jd-luzitania."""
    sampled = matched = 0
    for ad in (props.get('ads') or [])[:20]:
        loc = ad.get('locationDetails') or {}
        names = [norm(v) for v in (loc.get('municipality'),
                                    loc.get('neighbourhood')) if v]
        if not names:
            continue
        sampled += 1
        if any(n == slug or (len(n) >= 4 and n in slug)
               or (len(slug) >= 4 and slug in n) for n in names):
            matched += 1
    if sampled < 3:
        return True  # too little signal to condemn the scope
    return matched / sampled >= 0.2


def probe_node(page, wait_for_props, scope, path, validate_slug=None):
    """Load page 1 of the node's scope and return totalOfAds, UNSCOPED when
    the served ads contradict `validate_slug`, or None when the count can't
    be trusted (redirect away from the scope, or no payload)."""
    url = f'{BASE}/{scope}/{path}'
    page.goto(url, wait_until='domcontentloaded')
    final_path = urlparse(page.url).path.strip('/')
    if final_path != f'{scope}/{path}':
        # OLX redirects unknown/retired scopes to a parent or the homepage;
        # the served count would belong to that other scope.
        print(f'  {path}: redirected to {page.url}; left unprobed', flush=True)
        return None
    props = wait_for_props(page)
    if props is None:
        print(f'  {path}: no ad payload (Cloudflare block or page change); '
              f'left unprobed', flush=True)
        return None
    # OLX serves unknown/retired scopes as a broader search at the same URL
    # (no redirect), so the count would belong to e.g. the whole state;
    # listingUrl in the payload is the scope that was actually served.
    served = (props.get('listingUrl') or '').strip('/')
    if served != f'{scope}/{path}':
        print(f'  {path}: OLX served /{served} instead (stale/unknown '
              f'scope); left unprobed', flush=True)
        return None
    if validate_slug and not _content_scoped(props, validate_slug):
        print(f'  {path}: served ads are not from "{validate_slug}" — URL '
              f'filters nothing; marked UNSCOPED', flush=True)
        return UNSCOPED
    total = props.get('totalOfAds')
    return total if isinstance(total, int) else None


def walk(page, wait_for_props, factory, roots, args):
    """BFS over the tree; each queue entry carries the parent's count.

    Two count-arithmetic traps are caught without extra fetches: a child
    reporting MORE ads than its parent (impossible for a subset scope — OLX's
    zero-result padding) is stored as PADDED, and TWO siblings each reporting
    ~the parent's full count (jointly impossible — the location segment is
    being ignored) are both stored as UNSCOPED, the first retroactively.
    Childless nodes whose slug names one place additionally get their served
    ads content-checked against the slug (see probe_node)."""
    from olx_patchright.db import record_probe
    queue = deque((root, None) for root in roots)
    near_first = {}  # parent_path -> first sibling seen near the parent count
    unscoped = set()  # paths marked UNSCOPED this run (children are moot)
    fetches = probed = 0

    def near_parent_sibling(node, count, parent_count):
        """True when another sibling already sits near the parent's full
        count (marking that first sibling retroactively); otherwise register
        this node as the first and return False."""
        if count is None or count < 0 or parent_count is None:
            return False
        if parent_count < NEAR_PARENT_MIN or count < NEAR_PARENT * parent_count:
            return False
        prev = near_first.setdefault(node.parent_path, node.path)
        if prev == node.path:
            return False
        print(f'  {node.path}: {count} ≈ parent count {parent_count} and so '
              f'does sibling {prev} — location segment ignored; both marked '
              f'UNSCOPED', flush=True)
        record_probe(factory, prev, UNSCOPED)
        unscoped.update((prev, node.path))
        return True

    while queue:
        node, parent_count = queue.popleft()
        if node.parent_path in unscoped:
            # Parent's location segment is ignored, so this deeper URL is
            # moot; enqueued before the parent's (retroactive) marking.
            continue
        kids = children_of(factory, node.path)
        count = node.total_of_ads if (node.probed_at is not None
                                      and not args.refresh) else None
        if count is not None and parent_count is not None \
                and count > parent_count:
            print(f'{node.path}: cached {count} exceeds parent count '
                  f'{parent_count}; re-probing', flush=True)
            count = None
        if count is not None:
            if near_parent_sibling(node, count, parent_count):
                count = UNSCOPED
                record_probe(factory, node.path, count)
            else:
                print(f'{node.path}: {count} ads (cached)', flush=True)
        else:
            if args.limit and fetches >= args.limit:
                print(f'Fetch limit ({args.limit}) reached; '
                      f'{len(queue) + 1} node(s) left for the next run.',
                      flush=True)
                break
            validate_slug = node.slug if (not kids and
                                          _slug_names_place(node.slug)) \
                else None
            count = probe_node(page, wait_for_props, args.scope, node.path,
                               validate_slug)
            fetches += 1
            if count == UNSCOPED:
                unscoped.add(node.path)
            if count is not None and count >= 0 and parent_count is not None \
                    and count > parent_count * PADDING_JUMP:
                print(f'  {node.path}: {count} > parent count {parent_count}'
                      f' — zero-result padding; marked PADDED', flush=True)
                count = PADDED
            elif near_parent_sibling(node, count, parent_count):
                count = UNSCOPED
            elif count is not None and count >= 0 \
                    and parent_count is not None and count > parent_count:
                # Over the parent but no corroborating sibling (yet):
                # ambiguous between padding and drift — exclude it either way.
                print(f'  {node.path}: {count} > parent count {parent_count} '
                      f'(no corroborating sibling); marked PADDED',
                      flush=True)
                count = PADDED
            record_probe(factory, node.path, count)
            if count is not None:
                probed += 1
                if count >= 0:
                    print(f'{node.path}: {count} ads', flush=True)
            time.sleep(PAGE_DELAY_S)
        if count is None or count <= args.threshold:
            continue
        if kids:
            queue.extend((kid, count) for kid in kids)
        else:
            print(f'  !! {node.path}: {count} > {args.threshold} but has no '
                  f'children to split into', flush=True)
    print(f'Done: {fetches} fetch(es), {probed} count(s) recorded.',
          flush=True)


def report(factory, roots, threshold):
    """Print the probed part of the density map as an indented tree."""
    from olx_patchright.db import LocationRow
    with factory() as session:
        rows = session.query(LocationRow) \
                      .filter(LocationRow.probed_at.isnot(None)) \
                      .order_by(LocationRow.path).all()
        total_nodes = session.query(LocationRow).count()
    prefixes = tuple(r.path for r in roots) if roots else None
    leaves = need_split = padded = unscoped = shown = 0
    labels = {
        'padded': 'padded (scarce, uncounted)',
        'unscoped': 'UNSCOPED (URL filters nothing)',
        'split': 'split',
        'dense': 'NEEDS-SPLIT (no usable children)',
        'leaf': 'leaf',
    }
    for row in rows:
        if prefixes and not row.path.startswith(prefixes):
            continue
        shown += 1
        kind = classify_node(row, children_of(factory, row.path)
                             if row.total_of_ads > threshold else [],
                             threshold)
        status = labels[kind]
        leaves += kind == 'leaf'
        padded += kind == 'padded'
        unscoped += kind == 'unscoped'
        need_split += kind == 'dense'
        indent = '  ' * (row.depth - 1)
        print(f'{indent}{row.slug:40s} {row.total_of_ads:>8d}  {status}')
    print(f'\n{shown} probed of {total_nodes} nodes | {leaves} scrape leaves, '
          f'{padded} padded, {unscoped} unscoped, {need_split} '
          f'over-threshold dead ends (threshold={threshold})')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('paths', nargs='*', metavar='path',
                        help='subtree root paths, e.g. estado-es '
                             '(default: all states)')
    parser.add_argument('--scope', default=DEFAULT_SCOPE,
                        help='category scope prefixed to each path '
                             '(default: %(default)s)')
    parser.add_argument('--threshold', type=int, default=DEFAULT_THRESHOLD,
                        help='descend into children above this count '
                             '(default: %(default)s ≈ 100 pages x 50 ads)')
    parser.add_argument('--limit', type=int, default=0,
                        help='max page fetches this run (0 = unlimited)')
    parser.add_argument('--refresh', action='store_true',
                        help='re-probe nodes that already have a count')
    parser.add_argument('--report', action='store_true',
                        help='print the probed density map and exit')
    args = parser.parse_args()
    args.scope = args.scope.strip('/')

    from olx_patchright.db import location_session_factory, LocationRow
    factory = location_session_factory()

    with factory() as session:
        if args.paths:
            roots = []
            for path in args.paths:
                row = session.get(LocationRow, path.strip('/'))
                if row is None:
                    parser.error(f'unknown location path: {path} '
                                 f'(harvest it first?)')
                roots.append(row)
        else:
            roots = session.query(LocationRow).filter_by(depth=1) \
                           .order_by(LocationRow.path).all()

    if args.report:
        report(factory, roots if args.paths else [], args.threshold)
        return

    from patchright.sync_api import sync_playwright
    from olx_patchright.browser import clear_profile_lock, launch_profile, \
        first_page
    from olx_patchright.catalog import NAV_TIMEOUT_MS, _wait_for_props

    clear_profile_lock()
    with sync_playwright() as p:
        ctx = launch_profile(p)
        ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        page = first_page(ctx)
        try:
            walk(page, _wait_for_props, factory, roots, args)
        finally:
            ctx.close()


if __name__ == '__main__':
    main()
