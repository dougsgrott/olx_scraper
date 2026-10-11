"""From a selection of location nodes to a scrape plan the user can check
before queueing it: which URLs (olx_patchright.plan.build_plan — leaf /
dense / remainder, alias twins once, nested selections merged), roughly how
many pages and how long, and what to watch out for."""
import math
from dataclasses import dataclass, field
from datetime import timedelta

import yaml

from olx_patchright.export import infer_region, start_page_of
from olx_patchright.plan import (PAGE_CAP, PAGE_SIZE, build_plan, path_of,
                                 start_url)

DEFAULT_SECONDS_PER_PAGE = 8.0
# A selection entry '=<path>' means that scope's own URL only. A plain path
# means its whole subtree (a split node expands into its children, then its
# remainder), which is what picking a node means; "due" and "stale" pick
# remainders, which must not drag their whole subtree back in.
EXACT = '='

INCREMENTAL = 'incremental'  # early stop everywhere
SMART = 'smart'              # full pass only where never walked to the end
FULL = 'full'                # full pass everywhere
MODES = (INCREMENTAL, SMART, FULL)


@dataclass
class Options:
    patience: int = 4
    min_new_per_page: int = 5
    max_pages: int = 0
    mode: str = INCREMENTAL
    full_pass: bool = False  # shorthand for mode=FULL; kept in sync

    def __post_init__(self):
        if self.full_pass:
            self.mode = FULL
        if self.mode not in MODES:
            self.mode = INCREMENTAL
        self.full_pass = self.mode == FULL

    @property
    def early_stop(self):
        """The run's early_stop block; a full pass disables early stop (the
        page cap still ends dense scopes). SMART adds per-URL overrides."""
        if self.mode == FULL:
            return {}
        stop = {'patience': self.patience, 'min_new_per_page': self.min_new_per_page}
        if self.max_pages:
            stop['max_pages'] = self.max_pages
        return stop


@dataclass
class PlanItem:
    path: str
    name: str
    count: int
    kind: str
    url: str
    est_pages: int
    last_run: object
    eff_at: object
    complete_at: object = None  # last walk to the end (here, a twin or above)
    queued: bool = False
    full: bool = False          # walked to the end in this plan (no early stop)

    @property
    def full_pass_pages(self):
        return math.ceil(min(self.count, PAGE_CAP * PAGE_SIZE) / PAGE_SIZE)


@dataclass
class Preview:
    paths: list
    options: Options
    items: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    seconds_per_page: float = DEFAULT_SECONDS_PER_PAGE

    @property
    def pages(self):
        return sum(i.est_pages for i in self.items)

    @property
    def seconds(self):
        return self.pages * self.seconds_per_page

    @property
    def never_walked(self):
        """Leaves never walked to their last page. Early stop alone can't
        complete them: after global dedup it stops once the newest ads are
        known, while older never-collected ones may sit deeper. Dense scopes
        and remainders are left out — the page cap stops them first."""
        return [i for i in self.items if i.kind == 'leaf' and i.complete_at is None]

    @property
    def never_walked_pages(self):
        return sum(i.full_pass_pages for i in self.never_walked)

    def start_urls(self):
        """run_config start_urls: plain URLs, or {url, early_stop: {}} for a
        URL walked fully inside an otherwise early-stopped run."""
        mixed = bool(self.options.early_stop)
        return [{'url': i.url, 'early_stop': {}} if i.full and mixed else i.url
                for i in self.items]


def options_from_run_config(summary):
    """Defaults: the early-stop settings the user's run_config.yaml uses."""
    stop = summary.early_stop or {}
    return Options(patience=int(stop.get('patience') or 4),
                   min_new_per_page=int(stop.get('min_new_per_page') or 5),
                   max_pages=int(stop.get('max_pages') or 0))


def seconds_per_page(runs):
    pages = sum(r.pages_scraped or 0 for r in runs if r.duration_s)
    seconds = sum(r.duration_s or 0 for r in runs if r.pages_scraped)
    return seconds / pages if pages else DEFAULT_SECONDS_PER_PAGE


def _estimate(stats, count, kind, options, full):
    credited = [r for r in stats.runs if r.credited]
    if credited and not full:
        pages = credited[0].pages_scraped or 1
    elif kind == 'remainder' and not full:
        pages = options.patience + 1  # its children just took the newest ads
    else:
        pages = math.ceil(min(count, PAGE_CAP * PAGE_SIZE) / PAGE_SIZE)
    if options.max_pages:
        pages = min(pages, options.max_pages)
    return max(1, pages)


def preview(model, paths, options, scope, queued_paths=()):
    tree = model.tree
    result = Preview(paths=list(dict.fromkeys(paths)), options=options,
                     seconds_per_page=seconds_per_page(model.runs))
    roots, exact = [], []
    for entry in result.paths:
        path = entry[len(EXACT):] if entry.startswith(EXACT) else entry
        if path in tree:
            (exact if entry.startswith(EXACT) else roots).append(path)
        elif path in model.stats:
            result.warnings.append(f'{path}: off-tree legacy scope, not scrapable; skipped')
        else:
            result.warnings.append(f'{path}: unknown location path; skipped')
    plan, warnings = build_plan(list(tree.nodes.values()), roots, tree.threshold,
                                tree.alias_of)
    result.warnings += warnings
    planned = {p for p, _, _ in plan}
    for path in exact:  # own URL only, after any children (first-seen wins)
        status = tree.status[path]
        if path in planned or status in ('unprobed', 'padded', 'unscoped'):
            continue
        kind = 'remainder' if status == 'split' else status
        plan.append((path, tree.nodes[path].total_of_ads, kind))
        planned.add(path)
    queued = set(queued_paths)
    for path, count, kind in plan:
        stats = model.stats[path]
        full = options.mode == FULL or (
            options.mode == SMART and kind == 'leaf' and stats.complete_at is None)
        result.items.append(PlanItem(
            path=path, name=stats.name, count=count, kind=kind,
            url=start_url(path, scope),
            est_pages=_estimate(stats, count, kind, options, full),
            last_run=stats.last_run, eff_at=stats.eff_at,
            complete_at=stats.complete_at, queued=path in queued, full=full))
    dense = [i for i in result.items if i.kind == 'dense']
    if dense:
        result.warnings.append(f'{len(dense)} dense scope(s) hold more ads than OLX '
                               f'serves ({PAGE_CAP} pages); they end at the page cap.')
    if any(i.queued for i in result.items):
        result.warnings.append('Some URLs are already in a queued or running job.')
    if not result.items and roots:
        result.warnings.append('Nothing to scrape: the selection is unprobed, '
                               'padded or unscoped — probe it first.')
    return result


def plan_yaml(preview_):
    """A run_config fragment for the external (CLI / scheduled) runner."""
    catalog = {'start_urls': preview_.start_urls()}
    if preview_.options.early_stop:
        catalog['early_stop'] = preview_.options.early_stop
    header = (f'# generated by the OLX control panel: {len(preview_.items)} URLs, '
              f'~{preview_.pages} pages\n')
    return header + yaml.safe_dump({'mode': 'CATALOG', 'catalog_spider': catalog},
                                   sort_keys=False, allow_unicode=True)


def stale_paths(model, days, now):
    """Plan leaves scraped before (they have runs) but not fresh within
    `days`: what keeping the current coverage fresh would take."""
    cutoff = now - timedelta(days=days)
    tree = model.tree
    plan, _ = build_plan(list(tree.nodes.values()), [r.path for r in tree.roots],
                         tree.threshold, tree.alias_of)
    out = []
    for path, _, kind in plan:
        stats = model.stats[path]
        if stats.runs and (stats.eff_at is None or stats.eff_at < cutoff):
            out.append(_selection(path, kind))
    return out


def _selection(path, kind):
    return EXACT + path if kind == 'remainder' else path


def due_paths(model, now, min_new=50, fallback_days=7):
    """Plan leaves scraped before that are worth a run now: those expected to
    hold at least `min_new` new ads (yield x time since fresh), most first;
    then those without a yield yet (fewer than two credited runs) that are
    not fresh within `fallback_days`."""
    tree = model.tree
    plan, _ = build_plan(list(tree.nodes.values()), [r.path for r in tree.roots],
                         tree.threshold, tree.alias_of)
    cutoff = now - timedelta(days=fallback_days)
    by_yield, by_age = [], []
    for path, _, kind in plan:
        stats = model.stats[path]
        if not stats.runs:
            continue
        expected = model.expected_new(stats, now)
        if expected is not None:
            if expected >= min_new:
                by_yield.append((expected, _selection(path, kind)))
        elif stats.eff_at is None or stats.eff_at < cutoff:
            by_age.append(_selection(path, kind))
    return [p for _, p in sorted(by_yield, reverse=True)] + by_age


def run_config_paths(summary, tree):
    """The scopes run_config.yaml scrapes, as tree paths (resume offsets
    dropped; off-tree URLs reported)."""
    paths, skipped = [], []
    for url in summary.urls:
        path = path_of(infer_region(url))
        if path in tree:
            paths.append(path)
        else:
            skipped.append(url)
    offsets = sum(1 for url in summary.urls if start_page_of(url) > 1)
    return list(dict.fromkeys(paths)), skipped, offsets
