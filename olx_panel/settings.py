"""Where the panel finds the scraper's files, and its tunables. Every path is
a field so tests can point the whole panel at a temporary tree."""
import os
from dataclasses import dataclass, field

from olx_patchright.plan import DEFAULT_SCOPE, DEFAULT_THRESHOLD, PAGE_CAP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


@dataclass
class Settings:
    root: str = ROOT
    olx_db: str = os.path.join(ROOT, 'scraped_data', 'olx.sqlite')
    locations_db: str = os.path.join(ROOT, 'scraped_data', 'locations.sqlite')
    panel_db: str = os.path.join(ROOT, 'scraped_data', 'panel.sqlite')
    panel_lock: str = os.path.join(ROOT, 'scraped_data', 'panel.lock')
    run_config: str = os.path.join(ROOT, 'run_config.yaml')
    overrides: str = os.path.join(PACKAGE_DIR, 'overrides.yaml')
    # (store, directory): manifests and bronze live in the outbox until
    # `pipeline.py upload` moves them to the archive.
    runs_dirs: tuple = field(default_factory=lambda: (
        ('outbox', os.path.join(ROOT, 'runs')),
        ('archive', os.path.join(ROOT, 'runs_uploaded')),
    ))
    scope: str = DEFAULT_SCOPE
    threshold: int = DEFAULT_THRESHOLD
    page_cap: int = PAGE_CAP
    fresh_days: int = 7
    refresh_interval_s: float = 15.0
    # Legacy manifests carry no invocation id; a gap longer than this between
    # one URL's end and the next one's start separates two invocations.
    invocation_gap_s: float = 600.0
    host: str = '127.0.0.1'
    port: int = 8765
