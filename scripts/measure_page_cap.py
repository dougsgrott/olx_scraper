"""Measure OLX's catalog pagination ceiling (one-off).

Loads ?o=N for an ascending list of page numbers on ONE dense scope and
reports the last page OLX genuinely serves. A page counts as served when the
payload comes back with ads, its pageIndex matches the requested number, and
its first ad differs from the previous good page's (OLX may silently clamp
?o= beyond the cap and re-serve the last page). The verdict calibrates
probe_density's --threshold: ceiling ≈ last_good_page x pageSize ads per
scope; anything denser truncates and must be split.

Needs the warmed patchright profile; do not run during a scrape.

    uv run python scripts/measure_page_cap.py
    uv run python scripts/measure_page_cap.py estado-sp/sao-paulo-e-regiao/zona-sul/jardins
    uv run python scripts/measure_page_cap.py --pages 95,100,101,102
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from olx_patchright.plan import BASE, DEFAULT_SCOPE, PAGE_SIZE  # noqa: E402

PAGE_DELAY_S = 6
# ~10.5k ads / 50 per page = 211 pages if uncapped; the sequence brackets the
# historically suspected 100-page cap, then pushes upward.
DEFAULT_PATH = 'estado-sc/norte-de-santa-catarina'
DEFAULT_PAGES = '50,90,100,101,105,125,150,200'


def probe_page(page, wait_for_props, url):
    """Return (pageIndex, n_ads, first_uid, totalOfAds) or None."""
    page.goto(url, wait_until='domcontentloaded')
    props = wait_for_props(page)
    if props is None:
        return None
    ads = props.get('ads') or []
    first_uid = ads[0].get('listId') if ads else None
    return props.get('pageIndex'), len(ads), first_uid, props.get('totalOfAds')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('path', nargs='?', default=DEFAULT_PATH,
                        help=f'location path to walk (default: {DEFAULT_PATH})')
    parser.add_argument('--scope', default=DEFAULT_SCOPE)
    parser.add_argument('--pages', default=DEFAULT_PAGES,
                        help=f'ascending page numbers to try '
                             f'(default: {DEFAULT_PAGES})')
    args = parser.parse_args()
    pages = sorted({int(n) for n in args.pages.split(',')})

    from patchright.sync_api import sync_playwright
    from olx_patchright.browser import clear_profile_lock, launch_profile, \
        first_page
    from olx_patchright.catalog import NAV_TIMEOUT_MS, _wait_for_props

    base_url = f"{BASE}/{args.scope.strip('/')}/{args.path.strip('/')}"
    last_good = None
    prev_first_uid = None

    clear_profile_lock()
    with sync_playwright() as p:
        ctx = launch_profile(p)
        ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        page = first_page(ctx)
        try:
            for n in pages:
                result = probe_page(page, _wait_for_props, f'{base_url}?o={n}')
                if result is None:
                    print(f'o={n}: no payload — beyond the ceiling',
                          flush=True)
                    break
                page_index, n_ads, first_uid, total = result
                print(f'o={n}: served pageIndex={page_index}, {n_ads} ads, '
                      f'totalOfAds={total}', flush=True)
                if n_ads == 0 or page_index != n:
                    print(f'  -> clamped/empty; ceiling is below {n}',
                          flush=True)
                    break
                if first_uid is not None and first_uid == prev_first_uid:
                    print(f'  -> same first ad as previous good page; '
                          f'silently clamped below {n}', flush=True)
                    break
                last_good = n
                prev_first_uid = first_uid
                time.sleep(PAGE_DELAY_S)
        finally:
            ctx.close()

    if last_good is None:
        print('\nVerdict: not even the first tried page was served; '
              'check the scope.')
    else:
        print(f'\nVerdict: last genuinely served page = {last_good} '
              f'(~{last_good * PAGE_SIZE} reachable ads per scope). '
              f'Update plan.PAGE_CAP and the --threshold defaults.')


if __name__ == '__main__':
    main()
