"""Persistent-profile browser lifecycle.

The anti-detection posture (ADR 0001) depends on exactly ONE chromium holding
`.playwright_profile` at a time: patchright + a persistent, headful profile
carrying the Cloudflare clearance. Chromium enforces single-instance access
via a SingletonLock, so before launching we kill any leftover browser from a
crashed run and drop stale lock files.
"""
import os
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, '.playwright_profile')

# Per patchright's anti-detection guidance: persistent profile, headful, real
# viewport, and NO custom user_agent (a faked UA creates inconsistencies that
# Cloudflare detects).
CONTEXT_KWARGS = {
    'user_data_dir': PROFILE,
    'headless': False,
    'no_viewport': True,
    'locale': 'pt-BR',
    'timezone_id': 'America/Sao_Paulo',
}


def clear_profile_lock():
    """Free the profile: SIGKILL any chromium still holding it (matched on the
    distinctive dir name, which only ever appears in a chromium
    --user-data-dir= for this project), wait until it is gone, then remove
    stale Singleton* files."""
    pattern = '.playwright_profile'

    def _still_running():
        try:
            return subprocess.run(
                ['pgrep', '-f', pattern], stdout=subprocess.DEVNULL
            ).returncode == 0
        except FileNotFoundError:
            return False

    if _still_running():
        print("A browser is still holding the Playwright profile; killing it.")
        try:
            subprocess.run(['pkill', '-9', '-f', pattern])
        except FileNotFoundError:
            pass
        for _ in range(15):
            time.sleep(0.3)
            if not _still_running():
                break

    for name in ('SingletonLock', 'SingletonCookie', 'SingletonSocket'):
        path = os.path.join(PROFILE, name)
        try:
            if os.path.islink(path) or os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


def launch_profile(playwright):
    """Launch the persistent profile context. Call clear_profile_lock() first."""
    return playwright.chromium.launch_persistent_context(**CONTEXT_KWARGS)


def first_page(ctx):
    return ctx.pages[0] if ctx.pages else ctx.new_page()
