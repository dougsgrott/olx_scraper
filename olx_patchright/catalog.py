"""Catalogue scrape: one persistent browser, one tab, sequential pages.

For each start URL: navigate, wait for the App Router flight payload to carry
the ads list, map ads to records, keep first-seen uids (SQLite insert + bronze
export), follow ?o=N pagination until the last page.
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
PAGE_DELAY_S = 3
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


def scrape_catalog(start_urls):
    clear_profile_lock()
    factory = session_factory()
    exporter = EncounterExporter(region=infer_region(start_urls[0]))

    try:
        with sync_playwright() as p:
            ctx = launch_profile(p)
            ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)
            page = first_page(ctx)
            try:
                for start_url in start_urls:
                    _scrape_from(page, start_url, factory, exporter)
            finally:
                ctx.close()
    finally:
        manifest = exporter.close()

    return manifest


def _scrape_from(page, start_url, factory, exporter):
    url = start_url
    page_no = 1
    while url:
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
            return

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

        url = next_page_url(url, props)
        page_no += 1
        if url:
            time.sleep(PAGE_DELAY_S)
