"""Where the panel finds the scraper's files, and its tunables. Every path is
a field so tests can point the whole panel at a temporary tree."""
import os
import sys
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
    # Map assets built by scripts/build_geo.py
    geo_topology: str = os.path.join(ROOT, 'scraped_data', 'geo', 'br-municipios.topo.json')
    geo_csv: str = os.path.join(ROOT, 'scraped_data', 'geo', 'municipios.csv')
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
    # a scope is due once about one catalogue page of new ads is expected
    due_new_ads: int = 50
    refresh_interval_s: float = 15.0
    # Legacy manifests carry no invocation id; a gap longer than this between
    # one URL's end and the next one's start separates two invocations.
    invocation_gap_s: float = 600.0
    host: str = '127.0.0.1'
    port: int = 8765
    # Jobs (Phase 3): one directory per job under logs/jobs/ (gitignored)
    jobs_dir: str = os.path.join(ROOT, 'logs', 'jobs')
    scrape_command: tuple = (sys.executable, '-u', os.path.join(ROOT, 'run_patchright.py'))
    probe_command: tuple = (sys.executable, '-u',
                            os.path.join(ROOT, 'scripts', 'probe_density.py'))
    cleanup_command: tuple = (sys.executable, '-u', '-c',
                              'from olx_patchright.browser import clear_profile_lock; '
                              'clear_profile_lock(); print("Browser profile freed.")')
    supervisor_tick_s: float = 1.0
    # cancel: SIGINT (finish the page), then SIGTERM to the group, then SIGKILL
    cancel_grace_s: float = 60.0
    term_grace_s: float = 15.0
    busy_backoff_s: float = 30.0
