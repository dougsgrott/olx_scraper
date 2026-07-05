"""Interactive profile warm-up: get past Cloudflare by hand.

A scrape parses as soon as the ads payload lands, leaving no time to solve a
Cloudflare challenge. This opens the SAME persistent profile the scraper uses,
headful, and pauses so you can solve any challenge manually. The clearance
cookie persists into .playwright_profile and is reused by later scrapes.
"""
from patchright.sync_api import sync_playwright

from .browser import clear_profile_lock, launch_profile, first_page


def warm_profile(url):
    clear_profile_lock()
    with sync_playwright() as p:
        ctx = launch_profile(p)
        page = first_page(ctx)
        page.goto(url, wait_until='domcontentloaded')
        print(f"\nOpened: {url}")
        input(
            "Solve any Cloudflare challenge in the window and wait until the real\n"
            "listings are visible, THEN press Enter here to save the profile and close... "
        )
        ctx.close()
        print("Profile saved. Set mode to CATALOG and run again.")
