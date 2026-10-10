"""Shared fixtures. Every test runs against a throwaway browser lock and
profile path, so nothing here can touch (or pkill) the real
.playwright_profile or block a real scrape's .browser.lock."""
import os

import pytest

from olx_patchright import browser, db


@pytest.fixture(autouse=True)
def isolated_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, 'LOCK_PATH', str(tmp_path / '.browser.lock'))
    monkeypatch.setattr(browser, 'PROFILE', str(tmp_path / '.playwright_profile'))
    monkeypatch.setattr(browser, 'CONTEXT_KWARGS',
                        dict(browser.CONTEXT_KWARGS,
                             user_data_dir=str(tmp_path / '.playwright_profile')))
    monkeypatch.setattr(browser, '_lock_fd', None)
    yield
    if browser._lock_fd is not None:
        os.close(browser._lock_fd)
        browser._lock_fd = None


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """cwd for the scraper's relative runs/ and logs/ paths, and a fresh
    olx.sqlite."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'scraped_data' / 'olx.sqlite'))
    return tmp_path


@pytest.fixture
def fast_catalog(monkeypatch):
    """catalog module with politeness delays removed."""
    from olx_patchright import catalog
    monkeypatch.setattr(catalog, 'PAGE_DELAY_S', 0)
    monkeypatch.setattr(catalog, 'PROPS_POLL_INTERVAL_S', 0)
    monkeypatch.setattr(catalog, 'NAV_RETRY_BACKOFF_S', 0)
    return catalog
