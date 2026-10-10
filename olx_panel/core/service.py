"""The panel's data service: keeps panel.sqlite in step with the scraper's
files and hands out a CoverageModel rebuilt only when the data changed.

refresh() is cheap enough to run every few seconds: a directory walk over the
manifest trees (only new manifests are parsed), an id-watermarked read of new
catalog_data rows, and a glob for stranded partial bronze files.
"""
import glob
import logging
import os
import threading
from datetime import datetime

from olx_patchright.browser import lock_holder

from .adgeo import load_ad_agg, refresh_ad_agg
from .manifests import index_manifests, interpret, load_runs
from .metrics import CoverageModel
from .overrides import load_overrides
from .runconfig import summarize_run_config
from .sources import file_version
from .store import Store
from .tree import load_tree

log = logging.getLogger(__name__)


class PanelService:
    def __init__(self, settings):
        self.settings = settings
        self.store = Store(settings.panel_db)
        self._lock = threading.RLock()
        self._model = None
        self._model_key = None
        self._tree = None
        self._tree_key = None
        self.last_refresh = None
        self.refresh_error = None
        self.unreadable = ()
        self.stranded = []

    # -- keeping panel.sqlite current ----------------------------------------

    def refresh(self):
        """Index new manifests and ads; never raises (the error is kept for
        the banner and the next refresh retries)."""
        try:
            result = index_manifests(self.store, self.settings.runs_dirs)
            self.unreadable = result.unreadable
            refresh_ad_agg(self.store, self.settings.olx_db)
            self.stranded = self._stranded_bronze()
            self.refresh_error = None
        except Exception as exc:  # keep serving the last good model
            log.exception('refresh failed')
            self.refresh_error = repr(exc)
        self.last_refresh = datetime.now().astimezone()

    def _stranded_bronze(self):
        """Partial bronze left by a killed run (written as .tmp, renamed on
        close). Only meaningful while no browser job could be writing one."""
        found = []
        for _, root in self.settings.runs_dirs:
            found += glob.glob(os.path.join(
                root, 'spider=catalog', 'dt=*', 'region=*', '*.jsonl.gz.tmp'))
        return sorted(found)

    # -- the model ------------------------------------------------------------

    def data_version(self):
        with self.store.connect() as conn:
            manifests = self.store.get_meta(conn, 'manifest_version', 0)
            ads = self.store.get_meta(conn, 'ad_version', 0)
        return (manifests, ads, file_version(self.settings.locations_db),
                file_version(self.settings.overrides))

    def tree(self):
        key = file_version(self.settings.locations_db)
        with self._lock:
            if self._tree is None or key != self._tree_key:
                self._tree = load_tree(self.settings.locations_db,
                                       self.settings.threshold)
                self._tree_key = key
            return self._tree

    def model(self):
        key = self.data_version()
        with self._lock:
            if self._model is None or key != self._model_key:
                tree = self.tree()
                overrides = load_overrides(self.settings.overrides)
                runs = interpret(load_runs(self.store), tree.nodes,
                                 self.settings.page_cap, overrides,
                                 self.settings.invocation_gap_s)
                with self.store.connect() as conn:
                    ad_count = int(self.store.get_meta(conn, 'ad_count', 0))
                self._model = CoverageModel(tree, runs, load_ad_agg(self.store),
                                            ad_count, self.settings.page_cap)
                self._model_key = key
            return self._model

    def run_config(self):
        return summarize_run_config(self.settings.run_config, self.tree())

    # -- banner ---------------------------------------------------------------

    def status(self):
        model = self.model()
        holder = lock_holder()
        collected = model.total_collected()
        return {
            'browser': holder,
            'display': bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')),
            'reconciliation': collected - model.ad_count if model.runs else 0,
            'collected': collected,
            'ad_count': model.ad_count,
            'unreadable': self.unreadable,
            'stranded': self.stranded if holder is None else [],
            'unattached': model.unattached,
            'refresh_error': self.refresh_error,
            'last_refresh': self.last_refresh,
            'version': '-'.join(str(v) for v in self.data_version()[:2]),
        }


class Refresher(threading.Thread):
    """Background refresh loop; stop() ends it at the next tick."""

    def __init__(self, service, interval_s):
        super().__init__(name='panel-refresher', daemon=True)
        self.service = service
        self.interval_s = interval_s
        self._stop_event = threading.Event()

    def run(self):
        while not self._stop_event.wait(self.interval_s):
            self.service.refresh()

    def stop(self):
        self._stop_event.set()
