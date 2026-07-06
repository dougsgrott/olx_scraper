"""Catalogue scrape: one persistent browser, one tab, sequential pages.

For each start URL: navigate, wait for the App Router flight payload to carry
the ads list, map ads to records, keep first-seen uids (SQLite insert + bronze
export), follow ?o=N pagination until the last page — or until an optional
early-stop rule fires (consecutive stale pages, or a hard page cap).
"""
import os
import time
from datetime import datetime

from patchright.sync_api import sync_playwright

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


def scrape_catalog(start_urls, early_stop=None):
    clear_profile_lock()
    factory = session_factory()
    exporter = EncounterExporter(region=infer_region(start_urls[0]))
    stop_cfg = _normalize_early_stop(early_stop)

    try:
        with sync_playwright() as p:
            ctx = launch_profile(p)
            ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)
            page = first_page(ctx)
            try:
                for start_url in start_urls:
                    pages, reason = _scrape_from(
                        page, start_url, factory, exporter, stop_cfg
                    )
                    exporter.record_stop(pages, reason)
            finally:
                ctx.close()
    finally:
        manifest = exporter.close()

    return manifest


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


def _scrape_from(page, start_url, factory, exporter, stop_cfg):
    """Paginate from start_url; return (pages_scraped, stop_reason) where
    stop_reason is 'exhausted', 'early_stop', 'max_pages', or 'no_payload'."""
    url = start_url
    page_no = 0
    stale_pages = 0
    while url:
        page_no += 1
        response = page.goto(url, wait_until='domcontentloaded')
        status = response.status if response else 0
        props = _wait_for_props(page)

        if props is None:
            dump_path = _dump_page(page, status)
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
            time.sleep(PAGE_DELAY_S)
    return page_no, 'exhausted'
