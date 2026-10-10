"""Generate catalog start URLs from the probed density map (no network).

Walks the locations tree (scraped_data/locations.sqlite) using the counts
recorded by scripts/probe_density.py and emits a start-URL plan:

  - leaf       count <= --threshold: scraped directly.
  - dense      count >  --threshold but nothing usable to split into (no
               children, or all children padded/unscoped): scraped knowing
               pagination may truncate it. Splitting these further needs
               query filters (price bands) — not implemented.
  - remainder  a split node itself, emitted AFTER its children: children
               don't sum to the parent (ads without a neighbourhood only
               appear in the parent scope), so the parent is re-walked with
               early-stop to pick up the difference cheaply.

PADDED children are skipped (scarce scopes already covered by the remainder);
UNSCOPED children are skipped (their URLs serve the parent's result anyway).
Unprobed children of a split node are skipped with a warning — the remainder
covers their content.

The --threshold does not need to match the probe run's: counts are stored, so
re-generating with a different threshold (e.g. after measure_page_cap.py
finds the real ceiling) requires no new probing.

    uv run python scripts/generate_start_urls.py                  # YAML block, all states
    uv run python scripts/generate_start_urls.py estado-es --plain
    uv run python scripts/generate_start_urls.py --threshold 4000 --no-sf
"""
import argparse
import math
import os
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = 'https://www.olx.com.br'
# Sentinels written by scripts/probe_density.py (see LocationRow docstring).
PADDED = -1
UNSCOPED = -2
PAGE_SIZE = 50


def build_plan(rows, roots, threshold):
    """Return (plan, warnings): plan is a list of (path, count, kind) in
    scrape order (children before their remainder parent)."""
    by_path = {r.path: r for r in rows}
    kids_of = defaultdict(list)
    for r in rows:
        if r.parent_path:
            kids_of[r.parent_path].append(r)

    plan = []
    warnings = []

    def emit(node):
        count = node.total_of_ads
        if node.probed_at is None or count is None:
            warnings.append(f'{node.path}: unprobed, skipped')
            return
        if count in (PADDED, UNSCOPED):
            return
        if count <= threshold:
            plan.append((node.path, count, 'leaf'))
            return
        usable = [k for k in kids_of[node.path]
                  if k.probed_at is not None and k.total_of_ads is not None
                  and k.total_of_ads >= 0]
        if not usable:
            plan.append((node.path, count, 'dense'))
            return
        for kid in sorted(kids_of[node.path], key=lambda k: k.path):
            emit(kid)
        plan.append((node.path, count, 'remainder'))

    for root in roots:
        emit(by_path[root])
    return plan, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('paths', nargs='*', metavar='path',
                        help='subtree roots (default: all states)')
    parser.add_argument('--scope', default='imoveis/aluguel',
                        help='category scope (default: %(default)s); must '
                             'match the scope the densities were probed under')
    parser.add_argument('--threshold', type=int, default=5000,
                        help='max ads a single scope may hold '
                             '(default: %(default)s)')
    parser.add_argument('--no-sf', action='store_true',
                        help="don't append ?sf=1 (newest-first, which "
                             'early-stop relies on)')
    parser.add_argument('--plain', action='store_true',
                        help='tab-separated url/count/kind instead of YAML')
    args = parser.parse_args()
    scope = args.scope.strip('/')

    from olx_patchright.db import location_session_factory, LocationRow
    factory = location_session_factory()
    with factory() as session:
        rows = session.query(LocationRow).order_by(LocationRow.path).all()

    all_paths = {r.path for r in rows}
    roots = [p.strip('/') for p in args.paths] or \
            sorted(r.path for r in rows if r.depth == 1)
    unknown = [p for p in roots if p not in all_paths]
    if unknown:
        parser.error(f'unknown location path(s): {", ".join(unknown)}')

    plan, warnings = build_plan(rows, roots, args.threshold)
    suffix = '' if args.no_sf else '?sf=1'

    def url(path):
        return f'{BASE}/{scope}/{path}{suffix}'

    n = defaultdict(int)
    for _, _, kind in plan:
        n[kind] += 1
    leaf_pages = sum(math.ceil(c / PAGE_SIZE) for _, c, k in plan
                     if k == 'leaf')

    if args.plain:
        for path, count, kind in plan:
            print(f'{url(path)}\t{count}\t{kind}')
    else:
        print(f'# generated by scripts/generate_start_urls.py on '
              f'{date.today()}')
        print(f'# scope={scope} threshold={args.threshold} | '
              f"{len(plan)} URLs: {n['leaf']} leaves (~{leaf_pages} pages "
              f"first run), {n['remainder']} remainders, {n['dense']} dense "
              f'(may truncate)')
        print('# remainders/dense rely on early_stop + ?sf=1 to stay cheap')
        print('catalog_spider:')
        print('  start_urls:')
        for path, count, kind in plan:
            print(f'    - "{url(path)}"  # {kind}, {count} ads')
    for w in warnings:
        print(f'# WARN {w}', file=sys.stderr)


if __name__ == '__main__':
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)  # output piped into head/less and closed early
