"""Run run_patchright.main() in a child process against FakeSite, so tests
can deliver REAL signals to the scrape loop (signal handlers only exist in a
process's main thread).

    python tests/fake_run.py <workdir> <config> <total_ads> <delay_s>

Everything the scraper touches (cwd, olx.sqlite, browser lock, profile) is
redirected under <workdir>. Prints 'page-served' to stderr as each page is
served so the test knows when to signal.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]

from fakes import FakeSite, fake_sync_playwright  # noqa: E402
from olx_patchright import browser, catalog, db  # noqa: E402
import run_patchright  # noqa: E402


def main():
    workdir, config, total, delay_s = sys.argv[1:5]
    os.chdir(workdir)
    db.DB_PATH = os.path.join(workdir, 'olx.sqlite')
    browser.LOCK_PATH = os.path.join(workdir, '.browser.lock')
    browser.PROFILE = os.path.join(workdir, '.playwright_profile')
    catalog.PAGE_DELAY_S = 0

    def announce(page_no, url):
        print(f'page-served {page_no}', file=sys.stderr, flush=True)

    site = FakeSite(total=int(total), delay_s=float(delay_s), on_goto=announce)
    catalog.sync_playwright = fake_sync_playwright(site)
    try:
        code = run_patchright.main(['--config', config])
    except KeyboardInterrupt:
        code = run_patchright.EXIT_CANCELLED
    sys.exit(code)


if __name__ == '__main__':
    main()
