"""Catalogue scrape: one persistent browser, one tab, sequential pages.

For each start URL: navigate, wait for the App Router flight payload to carry
the ads list, map ads to records, keep first-seen uids (SQLite insert + bronze
export), follow ?o=N pagination until the last page — or until an optional
early-stop rule fires (consecutive stale pages, or a hard page cap).

A long run is kept alive by two guards: the browser context is recycled every
PAGES_PER_BROWSER navigations (one context measurably degrades — see
_BrowserSession), and a navigation that keeps timing out ends only the current
start URL, never the whole run.
"""
import os
import time
from datetime import datetime

from patchright.sync_api import sync_playwright
from patchright.sync_api import TimeoutError as PlaywrightTimeoutError

from .browser import clear_profile_lock, launch_profile, first_page
from .db import session_factory, uid_exists, insert_record
from .export import EncounterExporter, infer_region
from .parse import page_props, build_record, next_page_url

NAV_TIMEOUT_MS = 30000
# Politeness delay between page navigations (was DOWNLOAD_DELAY = 3).
PAGE_DELAY_S = 6
# The flight payload streams in after domcontentloaded; poll the DOM for the
# ads object instead of waiting for the unreliable `load` event (OLX's
# ad-heavy pages routinely stall it past the navigation timeout).
PROPS_POLL_ATTEMPTS = 10
PROPS_POLL_INTERVAL_S = 1
# One chromium context degrades steadily over a long run: the 2026-08-28 run
# went from ~2s to ~38s per navigation across 1424 pages, until goto finally
# blew NAV_TIMEOUT_MS mid-scrape. Relaunching resets it, and the persistent
# profile keeps the Cloudflare clearance, so recycling costs only the launch.
PAGES_PER_BROWSER = 250
# A navigation is retried this many times before the start URL is abandoned;
# the backoff also gives a rate-limited server a moment to relent.
NAV_ATTEMPTS = 3
NAV_RETRY_BACKOFF_S = 15
# Distinct from None, which goto returns for a same-document navigation.
_NAV_FAILED = object()


class _BrowserSession:
    """The persistent-profile context plus its page, recycled every
    PAGES_PER_BROWSER navigations. Pagination drives off a self-describing
    ?o=N URL, so swapping the context mid-scrape loses no state."""

    def __init__(self, playwright):
        self._playwright = playwright
        self._pages_since_launch = 0
        self._launch()

    def _launch(self):
        clear_profile_lock()
        self._ctx = launch_profile(self._playwright)
        self._ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        self.page = first_page(self._ctx)

    def count_page(self):
        """Record one navigation, relaunching once the budget is spent."""
        self._pages_since_launch += 1
        if self._pages_since_launch >= PAGES_PER_BROWSER:
            print(f"Recycling browser after {self._pages_since_launch} pages.")
            self.force_recycle()

    def force_recycle(self):
        try:
            self._ctx.close()
        except Exception as exc:  # already dead; clear_profile_lock cleans up
            print(f"Ignoring error while closing the browser: {exc}")
        self._pages_since_launch = 0
        self._launch()

    def close(self):
        self._ctx.close()


def _wait_for_props(page):
    for remaining in range(PROPS_POLL_ATTEMPTS - 1, -1, -1):
        props = page_props(page.content())
        if props is not None:
            return props
        if remaining:
            time.sleep(PROPS_POLL_INTERVAL_S)
    return None


def _dump_page(page, status):
    os.makedirs('logs', exist_ok=True)
    dump_path = f'logs/no_ads_{status}.html'
    with open(dump_path, 'w', encoding='utf-8') as fh:
        fh.write(page.content())
    return dump_path


def _goto_with_retry(session, url):
    """Navigate, retrying a timeout on a fresh context; _NAV_FAILED when every
    attempt timed out. A repeated timeout is the symptom of a degraded
    context, so the last attempt gets a new one rather than a fourth try on
    the old."""
    for attempt in range(1, NAV_ATTEMPTS + 1):
        try:
            return session.page.goto(url, wait_until='domcontentloaded')
        except PlaywrightTimeoutError:
            print(f"Navigation timeout ({attempt}/{NAV_ATTEMPTS}) at {url}.")
            if attempt == NAV_ATTEMPTS:
                return _NAV_FAILED
            time.sleep(NAV_RETRY_BACKOFF_S)
            session.force_recycle()
    return _NAV_FAILED


def scrape_catalog(start_urls, early_stop=None):
    """Scrape each start URL in turn through one shared browser tab.

    Each start URL gets its own exporter (region inferred from its path), so a
    multi-city run writes one bronze partition + manifest per region instead
    of attributing everything to the first URL's region. A start URL that
    fails is recorded and skipped, so one bad page cannot end the run.
    Returns the list of manifests, in start_urls order."""
    factory = session_factory()
    stop_cfg = _normalize_early_stop(early_stop)
    manifests = []

    with sync_playwright() as p:
        session = _BrowserSession(p)
        try:
            for start_url in start_urls:
                exporter = EncounterExporter(region=infer_region(start_url))
                try:
                    pages, reason = _scrape_from(
                        session, start_url, factory, exporter, stop_cfg
                    )
                    exporter.record_stop(pages, reason)
                except Exception as exc:
                    print(f"Aborting {start_url}: {exc!r}. Continuing with "
                          f"the next start URL.")
                    exporter.record_stop(0, 'crashed')
                finally:
                    manifests.append(exporter.close())
        finally:
            session.close()

    return manifests


def _normalize_early_stop(early_stop):
    """Clamp config to non-negative ints; 0 disables the corresponding rule.
    patience=0 (or a missing block) disables the stale-page rule; max_pages=0
    disables the hard cap — so the default is the old paginate-to-the-end
    behavior."""
    cfg = early_stop or {}
    return {
        'patience': max(0, int(cfg.get('patience') or 0)),
        'min_new_per_page': max(1, int(cfg.get('min_new_per_page') or 1)),
        'max_pages': max(0, int(cfg.get('max_pages') or 0)),
    }


def _scrape_from(session, start_url, factory, exporter, stop_cfg):
    """Paginate from start_url; return (pages_scraped, stop_reason) where
    stop_reason is 'exhausted', 'early_stop', 'max_pages', 'no_payload', or
    'nav_timeout'."""
    url = start_url
    page_no = 0
    stale_pages = 0
    while url:
        page_no += 1
        response = _goto_with_retry(session, url)
        if response is _NAV_FAILED:
            print(f"Giving up on {url} after {NAV_ATTEMPTS} navigation "
                  f"timeouts; moving on to the next start URL.")
            return page_no, 'nav_timeout'
        status = response.status if response else 0
        props = _wait_for_props(session.page)

        if props is None:
            dump_path = _dump_page(session.page, status)
            print(
                f"No ad payload at {url} (HTTP {status}). Saved served page to "
                f"{dump_path}. (403 => Cloudflare block, try WARM mode; "
                f"200 => page structure changed.)"
            )
            return page_no, 'no_payload'

        new = dup = 0
        for ad in props.get('ads') or []:
            record = build_record(ad)
            if record is None:
                continue
            if uid_exists(factory, record['uid']):
                exporter.count_duplicate()
                dup += 1
            else:
                record['scraped_date'] = datetime.now()
                record['uploaded_to_cloud'] = 0
                record['url_is_scraped'] = 0
                insert_record(factory, record)
                exporter.write_new(record)
                new += 1

        print(f"Page {page_no} ({url}): {new} new, {dup} already seen.")

        if stop_cfg['patience']:
            stale_pages = stale_pages + 1 if new < stop_cfg['min_new_per_page'] else 0
            if stale_pages >= stop_cfg['patience']:
                print(
                    f"Early stop: {stale_pages} consecutive page(s) with fewer "
                    f"than {stop_cfg['min_new_per_page']} new item(s)."
                )
                return page_no, 'early_stop'
        if stop_cfg['max_pages'] and page_no >= stop_cfg['max_pages']:
            print(f"Stopping: reached max_pages = {stop_cfg['max_pages']}.")
            return page_no, 'max_pages'

        url = next_page_url(url, props)
        if url:
            session.count_page()
            time.sleep(PAGE_DELAY_S)
    return page_no, 'exhausted'
