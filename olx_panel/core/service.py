"""The panel's data service: keeps panel.sqlite in step with the scraper's
files and hands out a CoverageModel rebuilt only when the data changed.

refresh() is cheap enough to run every few seconds: a directory walk over the
manifest trees (only new manifests are parsed), an id-watermarked read of new
catalog_data rows, and a glob for stranded partial bronze files.
"""
import glob
import logging
import os
import re
import threading
from datetime import datetime

from olx_patchright.browser import lock_holder
from olx_patchright.export import infer_region
from olx_patchright.plan import path_of

from .adgeo import load_ad_agg, refresh_ad_agg
from .geo import Crosswalk, load_gazetteer
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
        self._gazetteer = None
        self._gazetteer_key = None
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
                file_version(self.settings.overrides),
                file_version(self.settings.geo_csv))

    def tree(self):
        key = file_version(self.settings.locations_db)
        with self._lock:
            if self._tree is None or key != self._tree_key:
                self._tree = load_tree(self.settings.locations_db,
                                       self.settings.threshold)
                self._tree_key = key
            return self._tree

    def gazetteer(self):
        """IBGE municipalities with DDDs, or None before build_geo.py ran."""
        key = file_version(self.settings.geo_csv)
        with self._lock:
            if key != self._gazetteer_key:
                self._gazetteer = load_gazetteer(self.settings.geo_csv)
                self._gazetteer_key = key
            return self._gazetteer

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
                gazetteer = self.gazetteer()
                geo = Crosswalk(tree, gazetteer, overrides.crosswalk,
                                _breadcrumb_ddds(runs)) if gazetteer else None
                self._model = CoverageModel(tree, runs, load_ad_agg(self.store),
                                            ad_count, self.settings.page_cap, geo,
                                            overrides.ad_municipalities)
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
            'geo_missing': model.geo is None,
            'unmatched_places': sorted(model.unmatched_ad_places.items()),
            'unknown_nodes': model.geo.report()['unknown'] if model.geo else [],
            'refresh_error': self.refresh_error,
            'last_refresh': self.last_refresh,
            'version': '-'.join(str(v) for v in self.data_version()[:2]),
        }


_DDD_LABEL = re.compile(r'\bDDD\s*(\d{2})\b')


def _breadcrumb_ddds(runs):
    """OLX's own word on each macro's DDD, from v2 manifest breadcrumbs: the
    macro-region entry (its URL names a depth-2 path) carries the DDD as its
    value (`level: region`, 2026-07 format) and in its label ("DDD 27 -
    Norte do Espírito Santo"), which survives field renames."""
    ddds = {}
    for run in sorted(runs, key=lambda r: r.started_at):
        for crumb in run.breadcrumb or []:
            region = infer_region(crumb['url']) if crumb.get('url') else 'unknown'
            if region == 'unknown' or region.count('_') != 1:
                continue
            value = str(crumb.get('value') or '')
            if crumb.get('level') == 'region' and value.isdigit() and len(value) == 2:
                ddds[path_of(region)] = value
                continue
            m = _DDD_LABEL.search(crumb.get('label') or '')
            if m:
                ddds[path_of(region)] = m.group(1)
    return ddds


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
