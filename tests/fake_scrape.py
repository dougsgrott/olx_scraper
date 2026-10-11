"""Stands in for run_patchright.py in panel job tests: the same CLI
(--config), the real run_patchright.main / scrape_catalog / exporter /
browser lock, but a FakeSite instead of chromium. Everything the scraper
writes lands under the job's cwd (the panel's settings.root in tests).

Behaviour comes from the environment the panel passes down:
  FAKE_TOTAL   ads per start URL (default 40)
  FAKE_DELAY   seconds each page takes (default 0)
  FAKE_STATUS  HTTP status; with FAKE_PAYLOAD=0 pages carry no ads
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]

from fakes import FakeSite, fake_sync_playwright  # noqa: E402
from olx_patchright import browser, catalog, db  # noqa: E402
import run_patchright  # noqa: E402


def main():
    root = os.getcwd()
    db.DB_PATH = os.path.join(root, 'scraped_data', 'olx.sqlite')
    browser.LOCK_PATH = os.path.join(root, '.browser.lock')
    browser.PROFILE = os.path.join(root, '.playwright_profile')
    catalog.PAGE_DELAY_S = 0
    catalog.PROPS_POLL_INTERVAL_S = 0
    site = FakeSite(total=int(os.environ.get('FAKE_TOTAL', '40')),
                    delay_s=float(os.environ.get('FAKE_DELAY', '0')),
                    status=int(os.environ.get('FAKE_STATUS', '200')),
                    payload=os.environ.get('FAKE_PAYLOAD', '1') == '1')
    catalog.sync_playwright = fake_sync_playwright(site)
    try:
        code = run_patchright.main(sys.argv[1:])
    except KeyboardInterrupt:
        code = run_patchright.EXIT_CANCELLED
    sys.exit(code)


if __name__ == '__main__':
    main()
