import os
import re
import signal
import subprocess
import sys

import pytest

from olx_patchright import browser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HOLDER = '''
import sys, time
sys.path.insert(0, sys.argv[1])
from olx_patchright import browser
browser.LOCK_PATH = sys.argv[2]
browser.acquire_browser_lock(label='test holder')
print('ready', flush=True)
time.sleep(60)
'''


@pytest.fixture
def holder():
    """A separate process holding the browser lock until killed."""
    proc = subprocess.Popen(
        [sys.executable, '-c', HOLDER, ROOT, browser.LOCK_PATH],
        stdout=subprocess.PIPE, text=True,
    )
    assert proc.stdout.readline().strip() == 'ready'
    yield proc
    proc.kill()
    proc.wait()


def test_lock_is_idempotent_and_describes_its_holder(monkeypatch):
    monkeypatch.setenv('OLX_INVOCATION_ID', 'job-123')
    assert browser.lock_holder() is None
    browser.acquire_browser_lock(label='unit test')
    fd = browser._lock_fd
    browser.acquire_browser_lock(label='again')
    assert browser._lock_fd == fd
    info = browser.lock_holder()
    assert info['pid'] == os.getpid()
    assert info['label'] == 'unit test'
    assert info['invocation_id'] == 'job-123'


def test_second_process_gets_busy_75(holder):
    with pytest.raises(browser.BrowserBusy) as exc:
        browser.acquire_browser_lock(wait_s=0.2)
    assert exc.value.code == browser.EXIT_BUSY == 75
    assert exc.value.holder['pid'] == holder.pid
    assert exc.value.holder['label'] == 'test holder'


def test_lock_is_released_when_the_holder_dies(holder):
    holder.send_signal(signal.SIGKILL)
    holder.wait()
    assert browser.lock_holder() is None
    browser.acquire_browser_lock(wait_s=0.2)
    assert browser.lock_holder()['pid'] == os.getpid()


def test_clear_profile_lock_never_kills_while_another_job_holds_the_lock(
        holder, monkeypatch):
    calls = []
    monkeypatch.setattr(browser.subprocess, 'run',
                        lambda *a, **k: calls.append(a))
    with pytest.raises(browser.BrowserBusy):
        browser.clear_profile_lock()
    assert calls == []


def test_kill_pattern_only_matches_this_profiles_chromium(monkeypatch):
    monkeypatch.setattr(browser, 'PROFILE', '/srv/olx/.playwright_profile')
    pattern = re.compile(browser._profile_pattern())
    chromium = ('/opt/chrome --no-first-run '
                '--user-data-dir=/srv/olx/.playwright_profile --remote-debugging-pipe')
    assert pattern.search(chromium)
    assert pattern.search('chrome --user-data-dir=/srv/olx/.playwright_profile')
    assert not pattern.search('chrome --user-data-dir=/srv/olx/.playwright_profile.blocked')
    assert not pattern.search('du -sh /srv/olx/.playwright_profile')
    assert not pattern.search('chrome --user-data-dir=/srv/olx/Xplaywright_profile')
