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

BASE = 'https://www.olx.com.br'
PAGE_DELAY_S = 6  # same politeness delay as the catalog scraper
# Sentinel stored in total_of_ads for scopes OLX pads with broader results
# (zero-result fallback): genuinely scarce, but the served count is a lie.
PADDED = -1


def children_of(factory, path):
    from olx_patchright.db import LocationRow
    with factory() as session:
        return session.query(LocationRow).filter_by(parent_path=path) \
                      .order_by(LocationRow.path).all()


def probe_node(page, wait_for_props, scope, path):
    """Load page 1 of the node's scope and return totalOfAds, or None when the
    count can't be trusted (redirect away from the scope, or no payload)."""
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
    total = props.get('totalOfAds')
    return total if isinstance(total, int) else None


def walk(page, wait_for_props, factory, roots, args):
    """BFS over the tree; each queue entry carries the parent's count so a
    child reporting more ads than its parent — impossible for a subset scope,
    OLX's zero-result padding in disguise — is caught without extra fetches
    and stored as PADDED (-1): a scarce scope with no trustworthy count."""
    from olx_patchright.db import record_probe
    queue = deque((root, None) for root in roots)
    fetches = probed = 0
    while queue:
        node, parent_count = queue.popleft()
        count = node.total_of_ads if (node.probed_at is not None
                                      and not args.refresh) else None
        if count is not None and parent_count is not None \
                and count > parent_count:
            print(f'{node.path}: cached {count} exceeds parent count '
                  f'{parent_count}; re-probing', flush=True)
            count = None
        if count is not None:
            print(f'{node.path}: {count} ads (cached)', flush=True)
        else:
            if args.limit and fetches >= args.limit:
                print(f'Fetch limit ({args.limit}) reached; '
                      f'{len(queue) + 1} node(s) left for the next run.',
                      flush=True)
                break
            count = probe_node(page, wait_for_props, args.scope, node.path)
            fetches += 1
            if count is not None and parent_count is not None \
                    and count > parent_count:
                print(f'  {node.path}: {count} > parent count {parent_count}'
                      f' — zero-result padding; marked PADDED', flush=True)
                count = PADDED
            record_probe(factory, node.path, count)
            if count is not None:
                probed += 1
                if count != PADDED:
                    print(f'{node.path}: {count} ads', flush=True)
            time.sleep(PAGE_DELAY_S)
        if count is None or count <= args.threshold:
            continue
        kids = children_of(factory, node.path)
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
    leaves = need_split = padded = shown = 0
    for row in rows:
        if prefixes and not row.path.startswith(prefixes):
            continue
        shown += 1
        if row.total_of_ads == PADDED:
            status = 'padded (scarce, uncounted)'
            padded += 1
        elif row.total_of_ads > threshold:
            has_kids = bool(children_of(factory, row.path))
            status = 'split' if has_kids else 'NEEDS-SPLIT (no children)'
            need_split += 0 if has_kids else 1
        else:
            status = 'leaf'
            leaves += 1
        indent = '  ' * (row.depth - 1)
        print(f'{indent}{row.slug:40s} {row.total_of_ads:>8d}  {status}')
    print(f'\n{shown} probed of {total_nodes} nodes | {leaves} scrape leaves, '
          f'{padded} padded, {need_split} over-threshold dead ends '
          f'(threshold={threshold})')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('paths', nargs='*', metavar='path',
                        help='subtree root paths, e.g. estado-es '
                             '(default: all states)')
    parser.add_argument('--scope', default='imoveis/aluguel',
                        help='category scope prefixed to each path '
                             '(default: %(default)s)')
    parser.add_argument('--threshold', type=int, default=5000,
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
