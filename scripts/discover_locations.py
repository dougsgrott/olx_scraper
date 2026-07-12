"""Harvest OLX's location tree from its per-state sitemaps into `locations`.

OLX declares one sitemap index per state in robots.txt
(www.olx.com.br/{uf}/sitemap_index.xml). Every URL in the child sitemaps ends
with a location path (…/estado-{uf}/{macro-region}/{sub-region}/{city-or-
neighbourhood}); extracting that suffix from every URL and deduplicating —
ancestors included — yields OLX's complete official location vocabulary, with
no slug construction or guessing. Rows land in scraped_data/locations.sqlite
:: locations (kept out of olx.sqlite, which is the restorable-from-bronze
dataset); a `path` becomes a catalog start URL by prepending a category scope
(e.g. https://www.olx.com.br/imoveis/aluguel/ + path).

Cloudflare blocks plain HTTP from this box (even robots.txt), so fetches go
through the same warmed patchright profile as the scraper — do NOT run this
while a scrape is running (the profile is single-instance; whoever starts
second kills the first). Re-running is safe: inserts are dedupe-only, so a
partial harvest resumes where it left off.

    uv run python scripts/discover_locations.py            # all 27 states
    uv run python scripts/discover_locations.py es sc      # selected UFs
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchright.sync_api import sync_playwright

from olx_patchright.browser import clear_profile_lock, launch_profile, first_page
from olx_patchright.db import location_session_factory, upsert_locations

UFS = [
    'ac', 'al', 'ap', 'am', 'ba', 'ce', 'df', 'es', 'go', 'ma', 'mt', 'ms',
    'mg', 'pa', 'pb', 'pr', 'pe', 'pi', 'rj', 'rn', 'rs', 'ro', 'rr', 'sc',
    'sp', 'se', 'to',
]
INDEX_URL = 'https://www.olx.com.br/{uf}/sitemap_index.xml'

NAV_TIMEOUT_MS = 30000
# Politeness delay between sitemap fetches (lighter than catalog pages, which
# use 6s).
FETCH_DELAY_S = 4
# state / macro-region / sub-region / city / neighbourhood, +1 spare for
# levels OLX may add; anything deeper is treated as extraction noise.
MAX_DEPTH = 6

# Sitemaps are fetched with in-page fetch() from a page already sitting on
# www.olx.com.br (same-origin, so cookies + the browser's TLS fingerprint
# apply). Navigating the tab to each 5 MB XML instead would render 50k nodes
# in Chromium's XML viewer and read the body via Response.text(), both of
# which can wedge with no timeout.
ORIGIN_URL = 'https://www.olx.com.br/robots.txt'
FETCH_JS = """async (url) => {
    const resp = await fetch(url, {signal: AbortSignal.timeout(25000)});
    return {status: resp.status, body: await resp.text()};
}"""

_LOC_RE = re.compile(r'<loc>\s*([^<]+?)\s*</loc>')


class BlockedError(Exception):
    """Cloudflare stopped serving us; abort the run (it resumes on re-run)."""


def fetch(page, url):
    try:
        result = page.evaluate(FETCH_JS, url)
    except Exception as e:
        print(f'fetch failed for {url}: {e}', flush=True)
        return 0, ''
    if result['status'] == 403:
        raise BlockedError(
            f'HTTP 403 at {url} — Cloudflare block; warm the profile '
            f'(WARM mode) and re-run to resume.'
        )
    return result['status'], result['body']


def sitemap_urls(xml):
    return _LOC_RE.findall(xml)


def location_paths(urls, uf):
    """Distinct location paths (ancestors included) found as URL suffixes."""
    tail_re = re.compile(r'/(estado-%s(?:/[a-z0-9\-]+)*)/?$' % re.escape(uf))
    paths = set()
    for url in urls:
        m = tail_re.search(url.split('?', 1)[0])
        if not m:
            continue
        segments = m.group(1).split('/')
        if len(segments) > MAX_DEPTH:
            continue
        for i in range(1, len(segments) + 1):
            paths.add('/'.join(segments[:i]))
    return paths


def harvest_uf(page, factory, uf):
    status, xml = fetch(page, INDEX_URL.format(uf=uf))
    if status != 200 or '<sitemapindex' not in xml:
        print(f'[{uf}] sitemap index unavailable (HTTP {status}); skipping.',
              flush=True)
        return
    children = sitemap_urls(xml)

    paths = set()
    scanned = 0
    for i, child in enumerate(children, 1):
        time.sleep(FETCH_DELAY_S)
        status, xml = fetch(page, child)
        if status != 200:
            print(f'[{uf}] HTTP {status} on {child}; skipping that file.',
                  flush=True)
            continue
        urls = sitemap_urls(xml)
        scanned += len(urls)
        paths |= location_paths(urls, uf)
        print(f'[{uf}] {i}/{len(children)}: {len(urls)} urls, '
              f'{len(paths)} paths so far', flush=True)

    new = upsert_locations(factory, uf.upper(), paths)
    print(f'[{uf}] {len(children)} sitemap(s), {scanned} urls scanned -> '
          f'{len(paths)} location paths ({new} new)', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('ufs', nargs='*', default=UFS, metavar='uf',
                        help='states to harvest (default: all 27)')
    args = parser.parse_args()
    ufs = [uf.lower() for uf in args.ufs]
    unknown = sorted(set(ufs) - set(UFS))
    if unknown:
        parser.error(f'unknown UF(s): {", ".join(unknown)}')

    factory = location_session_factory()
    clear_profile_lock()
    with sync_playwright() as p:
        ctx = launch_profile(p)
        ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        page = first_page(ctx)
        try:
            page.goto(ORIGIN_URL, wait_until='domcontentloaded')
            for uf in ufs:
                harvest_uf(page, factory, uf)
                time.sleep(FETCH_DELAY_S)
        except BlockedError as e:
            print(f'ABORTED: {e}', flush=True)
            sys.exit(1)
        finally:
            ctx.close()


if __name__ == '__main__':
    main()
