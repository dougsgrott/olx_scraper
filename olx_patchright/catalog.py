"""Catalogue scrape: one persistent browser, one tab, sequential pages.

For each start URL: navigate, wait for the App Router flight payload to carry
the ads list, map ads to records, keep first-seen uids (SQLite insert + bronze
export), follow ?o=N pagination until the last page — or until an optional
early-stop rule fires (consecutive stale pages, or a max_pages limit).

A long run is kept alive by two guards: the browser context is recycled every
PAGES_PER_BROWSER navigations (one context measurably degrades — see
_BrowserSession), and a navigation that keeps timing out ends only the current
start URL, never the whole run.

A start URL also ends with 'page_cap' when OLX will serve no further pages
(plan.PAGE_CAP; past it OLX silently re-serves the last page), so a scope
denser than one URL can reach always terminates and is reported as truncated
rather than as caught up.

SIGINT/SIGTERM stop cooperatively: the current page finishes, its start URL is
recorded as 'cancelled', and no further start URL begins (no manifest for
them). A second signal raises KeyboardInterrupt at once — the manifest of the
URL in progress is still written.
"""
import os
import signal
import threading
import time
import uuid
from datetime import datetime

from patchright.sync_api import sync_playwright
from patchright.sync_api import TimeoutError as PlaywrightTimeoutError

from .browser import clear_profile_lock, launch_profile, first_page
from .db import session_factory, uid_exists, insert_record
from .export import EncounterExporter, infer_region, start_page_of
from .parse import page_props, build_record, next_page_url
from .plan import PAGE_CAP

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
# A page whose ads were (nearly) all on the previous page is OLX re-serving
# the last page it is willing to serve.
REPEAT_PAGE_OVERLAP = 0.9

# Set by the first SIGINT/SIGTERM during scrape_catalog. A plain flag, not a
# threading.Event: a signal handler that takes a lock the interrupted main
# thread may be holding can deadlock.
_stop_requested = False


def _request_stop(signum, frame):
    global _stop_requested
    if _stop_requested:
        raise KeyboardInterrupt
    _stop_requested = True
    # os.write, not print: print from a signal handler can hit a reentrant
    # call into the stdout buffer the main thread is in the middle of.
    os.write(2, f'\nSignal {signum}: stopping after the current page '
                f'(signal again to stop now).\n'.encode())


def _install_stop_handlers():
    """Route SIGINT/SIGTERM to _request_stop; returns the handlers to restore.
    Signal handlers can only be set from the main thread."""
    global _stop_requested
    _stop_requested = False
    if threading.current_thread() is not threading.main_thread():
        return {}
    return {sig: signal.signal(sig, _request_stop)
            for sig in (signal.SIGINT, signal.SIGTERM)}


def _pause(seconds):
    """time.sleep that returns early once a stop is requested."""
    deadline = time.monotonic() + seconds
    while not _stop_requested:
        left = deadline - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, 0.25))


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
        try:
            self._ctx.close()
        except Exception as exc:  # already gone (e.g. the driver got SIGINT)
            print(f"Ignoring error while closing the browser: {exc}")


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
            if attempt == NAV_ATTEMPTS or _stop_requested:
                return _NAV_FAILED
            _pause(NAV_RETRY_BACKOFF_S)
            if _stop_requested:
                return _NAV_FAILED
            session.force_recycle()
    return _NAV_FAILED


def scrape_catalog(start_urls, early_stop=None):
    """Scrape each start URL in turn through one shared browser tab.

    Each start URL gets its own exporter (region inferred from its path), so a
    multi-city run writes one bronze partition + manifest per region instead
    of attributing everything to the first URL's region. A start URL that
    fails is recorded and skipped, so one bad page cannot end the run; a stop
    request (SIGINT/SIGTERM) ends the run after the current page.
    Returns the list of manifests, in start_urls order."""
    factory = session_factory()
    stop_cfg = _normalize_early_stop(early_stop)
    invocation_id = os.environ.get('OLX_INVOCATION_ID') or \
        f"cli-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    manifests = []

    previous_handlers = _install_stop_handlers()
    try:
        with sync_playwright() as p:
            session = _BrowserSession(p)
            try:
                for start_url in start_urls:
                    if _stop_requested:
                        print(f"Stop requested: skipping the remaining "
                              f"{len(start_urls) - len(manifests)} start URL(s).")
                        break
                    exporter = EncounterExporter(
                        region=infer_region(start_url), start_url=start_url,
                        invocation_id=invocation_id, early_stop=stop_cfg,
                    )
                    try:
                        reason = _scrape_from(
                            session, start_url, factory, exporter, stop_cfg
                        )
                        exporter.record_stop(reason)
                    except Exception as exc:
                        reason = 'cancelled' if _stop_requested else 'crashed'
                        print(f"Aborting {start_url} ({reason}): {exc!r}.")
                        exporter.record_stop(reason)
                    except BaseException:
                        exporter.record_stop('cancelled')
                        raise
                    finally:
                        manifests.append(exporter.close())
            finally:
                session.close()
    except Exception as exc:
        # After a terminal Ctrl-C the driver itself got SIGINT and is gone,
        # so shutting playwright down can fail; the manifests are written.
        if not _stop_requested:
            raise
        print(f"Ignoring browser shutdown error after the stop request: {exc!r}")
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)

    return manifests


def _normalize_early_stop(early_stop):
    """Clamp config to non-negative ints; 0 disables the corresponding rule.
    patience=0 (or a missing block) disables the stale-page rule; max_pages=0
    disables the per-URL page limit — so the default paginates to the end
    (or to OLX's page cap, whichever comes first)."""
    cfg = early_stop or {}
    return {
        'patience': max(0, int(cfg.get('patience') or 0)),
        'min_new_per_page': max(1, int(cfg.get('min_new_per_page') or 1)),
        'max_pages': max(0, int(cfg.get('max_pages') or 0)),
    }


def _scrape_from(session, start_url, factory, exporter, stop_cfg):
    """Paginate from start_url, counting each processed page on the exporter;
    return the stop_reason: 'exhausted', 'early_stop', 'max_pages',
    'page_cap', 'no_payload', 'nav_timeout' or 'cancelled'."""
    url = start_url
    page_no = start_page_of(start_url)  # absolute ?o= page being requested
    pages_done = 0
    stale_pages = 0
    prev_uids = set()
    while url:
        if _stop_requested:
            return 'cancelled'
        response = _goto_with_retry(session, url)
        if response is _NAV_FAILED:
            if _stop_requested:
                return 'cancelled'
            print(f"Giving up on {url} after {NAV_ATTEMPTS} navigation "
                  f"timeouts; moving on to the next start URL.")
            return 'nav_timeout'
        status = response.status if response else 0
        props = _wait_for_props(session.page)

        if props is None:
            dump_path = _dump_page(session.page, status)
            print(
                f"No ad payload at {url} (HTTP {status}). Saved served page to "
                f"{dump_path}. (403 => Cloudflare block, try WARM mode; "
                f"200 => page structure changed.)"
            )
            return 'no_payload'

        ads = props.get('ads') or []
        uids = {str(ad['listId']) for ad in ads if ad.get('listId') is not None}
        served_index = props.get('pageIndex')
        if isinstance(served_index, int) and served_index != page_no:
            print(f"Page cap: asked for page {page_no}, OLX served page "
                  f"{served_index}; it serves no further pages of {start_url}.")
            return 'page_cap'
        if uids and prev_uids and \
                len(uids & prev_uids) >= REPEAT_PAGE_OVERLAP * len(uids):
            print(f"Page cap: page {page_no} repeats the previous page; OLX "
                  f"serves no further pages of {start_url}.")
            return 'page_cap'
        exporter.record_scope(props)

        new = dup = 0
        for ad in ads:
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
        exporter.count_page(new)
        pages_done += 1
        prev_uids = uids

        print(f"Page {page_no} ({url}): {new} new, {dup} already seen.")

        if stop_cfg['patience']:
            stale_pages = stale_pages + 1 if new < stop_cfg['min_new_per_page'] else 0
            if stale_pages >= stop_cfg['patience']:
                print(
                    f"Early stop: {stale_pages} consecutive page(s) with fewer "
                    f"than {stop_cfg['min_new_per_page']} new item(s)."
                )
                return 'early_stop'
        if stop_cfg['max_pages'] and pages_done >= stop_cfg['max_pages']:
            print(f"Stopping: reached max_pages = {stop_cfg['max_pages']}.")
            return 'max_pages'

        url = next_page_url(url, props)
        if url and page_no >= PAGE_CAP:
            print(f"Page cap: {start_url} has more than {PAGE_CAP} pages, "
                  f"which is all OLX serves.")
            return 'page_cap'
        if url:
            page_no += 1
            session.count_page()
            _pause(PAGE_DELAY_S)
    return 'exhausted'
