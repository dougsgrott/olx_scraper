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


@dataclass
class Options:
    patience: int = 4
    min_new_per_page: int = 5
    max_pages: int = 0
    full_pass: bool = False

    @property
    def early_stop(self):
        """run_config early_stop block; a full pass disables early stop (the
        page cap still ends dense scopes)."""
        if self.full_pass:
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


def _estimate(stats, count, kind, options):
    credited = [r for r in stats.runs if r.credited]
    if credited and not options.full_pass:
        pages = credited[0].pages_scraped or 1
    elif kind == 'remainder' and not options.full_pass:
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
    roots = []
    for path in result.paths:
        if path in tree:
            roots.append(path)
        elif path in model.stats:
            result.warnings.append(f'{path}: off-tree legacy scope, not scrapable; skipped')
        else:
            result.warnings.append(f'{path}: unknown location path; skipped')
    plan, warnings = build_plan(list(tree.nodes.values()), roots, tree.threshold,
                                tree.alias_of)
    result.warnings += warnings
    queued = set(queued_paths)
    for path, count, kind in plan:
        stats = model.stats[path]
        result.items.append(PlanItem(
            path=path, name=stats.name, count=count, kind=kind,
            url=start_url(path, scope), est_pages=_estimate(stats, count, kind, options),
            last_run=stats.last_run, eff_at=stats.eff_at,
            complete_at=stats.complete_at, queued=path in queued))
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
    catalog = {'start_urls': [i.url for i in preview_.items]}
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
    for path, _, _ in plan:
        stats = model.stats[path]
        if stats.runs and (stats.eff_at is None or stats.eff_at < cutoff):
            out.append(path)
    return out


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
