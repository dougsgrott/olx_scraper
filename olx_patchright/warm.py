"""Interactive profile warm-up: get past Cloudflare by hand.

A scrape parses as soon as the ads payload lands, leaving no time to solve a
Cloudflare challenge. This opens the SAME persistent profile the scraper uses,
headful, and pauses so you can solve any challenge manually. The clearance
cookie persists into .playwright_profile and is reused by later scrapes.

From a terminal, press Enter when done. Without one (e.g. launched by the
control panel, stdin not a TTY) it waits for SIGINT, SIGTERM or SIGUSR1 — the
panel's "Done" button — and gives up after WARM_TIMEOUT_S so a forgotten
warm-up cannot hold the browser lock forever. Either way the profile is saved
by closing the context cleanly.
"""
import signal
import sys
import threading
import time

from patchright.sync_api import sync_playwright

from .browser import clear_profile_lock, launch_profile, first_page

WARM_TIMEOUT_S = 30 * 60

_done = False


def _on_done_signal(signum, frame):
    global _done
    _done = True


def _wait_for_done_signal(timeout_s):
    """Block until a done signal arrives or timeout_s passes; True if signalled."""
    global _done
    _done = False
    previous = {}
    if threading.current_thread() is threading.main_thread():
        previous = {sig: signal.signal(sig, _on_done_signal)
                    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1)}
    try:
        deadline = time.monotonic() + timeout_s
        while not _done and time.monotonic() < deadline:
            time.sleep(0.25)
        return _done
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _interactive():
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def warm_profile(url):
    clear_profile_lock()
    with sync_playwright() as p:
        ctx = launch_profile(p)
        try:
            page = first_page(ctx)
            page.goto(url, wait_until='domcontentloaded')
            print(f"\nOpened: {url}", flush=True)
            if _interactive():
                input(
                    "Solve any Cloudflare challenge in the window and wait until the real\n"
                    "listings are visible, THEN press Enter here to save the profile and close... "
                )
            else:
                print("Solve any Cloudflare challenge in the window and wait until the "
                      "real listings are visible, then signal done (SIGUSR1/SIGINT — the "
                      f"control panel's Done button). Closing on its own after "
                      f"{WARM_TIMEOUT_S // 60} min.", flush=True)
                if not _wait_for_done_signal(WARM_TIMEOUT_S):
                    print("Warm-up timed out; saving the profile as it is.", flush=True)
        finally:
            ctx.close()
    print("Profile saved. Set mode to CATALOG and run again.")
