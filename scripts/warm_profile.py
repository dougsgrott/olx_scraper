"""Warm the persistent Playwright profile so the scraper can get past Cloudflare.

A normal crawl waits only for `domcontentloaded` and parses at once, leaving no
time to solve a Cloudflare challenge. This launches the SAME persistent profile
the scraper uses (`.playwright_profile/`), headful, and pauses so you can solve
any challenge by hand. The clearance cookie is then persisted into the profile
and reused by `run.py`.

Run it while NOTHING else holds the profile (no scraper running, no leftover
Chrome). Uses patchright (the anti-detection fork), mirroring settings.py's
PLAYWRIGHT_CONTEXTS exactly.

    uv run python scripts/warm_profile.py [start_url]
"""
import os
import sys

from patchright.sync_api import sync_playwright

PROFILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".playwright_profile"
)
DEFAULT_URL = (
    "https://www.olx.com.br/imoveis/aluguel/estado-es/"
    "norte-do-espirito-santo/vitoria/republica"
)


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE,
            headless=False,
            no_viewport=True,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        print(f"\nOpened: {url}")
        input(
            "Solve any Cloudflare challenge in the window and wait until the real\n"
            "listings are visible, THEN press Enter here to save the profile and close... "
        )
        ctx.close()
        print("Profile saved. Try `uv run python run.py` now.")


if __name__ == "__main__":
    main()
