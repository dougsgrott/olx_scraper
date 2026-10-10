"""Warm the persistent Playwright profile so the scraper can get past Cloudflare.

Standalone form of run_patchright.py's WARM mode, kept for the legacy
run_scrapy.py entrypoint (which spawns it to keep the sync Playwright driver
away from its Twisted reactor). Delegates to olx_patchright.warm, so it takes
the same browser lock as every other browser user and exits 75 if one is
running.

    uv run python scripts/warm_profile.py [start_url]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_URL = (
    "https://www.olx.com.br/imoveis/aluguel/estado-es/"
    "norte-do-espirito-santo/vitoria/republica"
)


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    from olx_patchright.warm import warm_profile
    warm_profile(url)


if __name__ == "__main__":
    main()
