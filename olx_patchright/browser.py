"""Persistent-profile browser lifecycle.

The anti-detection posture (ADR 0001) depends on exactly ONE chromium holding
`.playwright_profile` at a time: patchright + a persistent, headful profile
carrying the Cloudflare clearance. Chromium enforces single-instance access
via a SingletonLock, so before launching we kill any leftover browser from a
crashed run and drop stale lock files.

Killing is only safe when the browser is an orphan, so every browser user —
catalog scrape, WARM, prober, sitemap harvest, page-cap probe, and the control
panel's jobs through them — first takes an exclusive advisory lock
(.browser.lock, flock) held until its process exits. A second launcher now
exits with EXIT_BUSY (75) instead of SIGKILLing the first one's browser
mid-scrape. The lock file's name must never contain `playwright_profile`:
command lines containing that string used to be pkill targets.
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, '.playwright_profile')
LOCK_PATH = os.path.join(ROOT, '.browser.lock')
# sysexits.h EX_TEMPFAIL: "busy, try again later" for wrappers and the panel.
EXIT_BUSY = 75

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

# The fd holding the browser lock; never closed, so the lock lives exactly as
# long as this process. Python fds are non-inheritable (PEP 446), so the
# patchright driver and chromium never keep the lock past us.
_lock_fd = None


class BrowserBusy(SystemExit):
    """Another process holds the browser lock. A SystemExit carrying
    EXIT_BUSY, so every entry point exits 75 without per-script handling."""

    def __init__(self, holder):
        super().__init__(EXIT_BUSY)
        self.holder = holder


def acquire_browser_lock(label=None, wait_s=2.0):
    """Take the browser lock for the rest of this process (no-op if already
    held). Polls for up to `wait_s` so a momentary lock_holder() probe can't
    make a starting job fail; raises BrowserBusy when someone really holds
    it. `label` describes the holder for whoever is kept waiting."""
    global _lock_fd
    if _lock_fd is not None:
        return
    fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                holder = lock_holder() or {}
                print(f"Browser busy: held by pid {holder.get('pid')} "
                      f"({holder.get('label')}) since {holder.get('since')}. "
                      f"Exiting with {EXIT_BUSY}.", file=sys.stderr, flush=True)
                raise BrowserBusy(holder)
            time.sleep(0.1)
    info = {
        'pid': os.getpid(),
        'label': label or os.path.basename(sys.argv[0] or 'python'),
        'invocation_id': os.environ.get('OLX_INVOCATION_ID'),
        'since': datetime.now().astimezone().isoformat(timespec='seconds'),
        'argv': sys.argv,
    }
    os.ftruncate(fd, 0)
    os.write(fd, json.dumps(info).encode())
    _lock_fd = fd


def lock_holder():
    """The current holder's info dict, or None when the browser is free.
    Probes with a shared lock that is released at once; the file's contents
    are only trusted while someone actually holds the lock."""
    try:
        fd = os.open(LOCK_PATH, os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raw = os.read(fd, 65536)
            try:
                return json.loads(raw)
            except ValueError:  # holder is still writing its info
                return {'pid': None, 'label': None, 'since': None}
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)


def _profile_pattern():
    """pgrep/pkill regex for a chromium using THIS profile: its own
    --user-data-dir argument (dots escaped, terminated), so neither
    .playwright_profile.blocked nor any editor/du/panel command line
    mentioning the directory can match."""
    escaped = re.sub(r'([.\[\]()*+?{}|^$\\])', r'\\\1', PROFILE)
    return f'user-data-dir={escaped}( |$)'


def clear_profile_lock():
    """Take the browser lock, then free the profile: SIGKILL any chromium
    still holding it — an orphan of a crashed run, since nobody else can hold
    the lock — wait until it is gone, then remove stale Singleton* files."""
    acquire_browser_lock()
    pattern = _profile_pattern()

    def _still_running():
        try:
            return subprocess.run(
                ['pgrep', '-f', pattern], stdout=subprocess.DEVNULL
            ).returncode == 0
        except FileNotFoundError:
            return False

    if _still_running():
        print("An orphaned browser is still holding the Playwright profile; "
              "killing it.")
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
    """Launch the persistent profile context. Call clear_profile_lock() first
    (the lock is re-asserted here as a backstop)."""
    acquire_browser_lock()
    return playwright.chromium.launch_persistent_context(**CONTEXT_KWARGS)


def first_page(ctx):
    return ctx.pages[0] if ctx.pages else ctx.new_page()
