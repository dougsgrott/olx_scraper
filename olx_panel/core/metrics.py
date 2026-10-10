"""Per-node coverage metrics (docs/CONTROL_PANEL.md, "Metric definitions").

  collected        Σ new_items of the node's runs — ads first collected via
                   this scope (exact: delta-only bronze); the tree shows the
                   subtree sum.
  located          ads whose own location is in the place; exact for states
                   (uf) and DDD areas (ad URL's macro segment) — deeper levels
                   need the Phase 2 crosswalk.
  inventory        OLX's live count from the prober (never summed over
                   children: aliases double count, parents hold ads with no
                   neighbourhood).
  eff_at           effective freshness: the latest start of a credited run
                   (complete / caught_up) on the node, an alias twin, or any
                   ancestor — a newest-first walk that caught up certifies
                   everything below it.
  complete_at      the latest full walk (exhausted) on the node, a twin or an
                   ancestor: the baseline. Caught-up runs certify freshness,
                   not completeness (a remainder's first pass can stop early
                   while scattered older ads were never collected).
  fresh / baseline the inventory-weighted share of the node's plan leaves
  share            that are fresh / completely walked (plan.build_plan's
                   leaf/dense/remainder split; a remainder weighs only what
                   its children don't hold). A leaf never run as a whole
                   gets what its probed children cover (a state whose cities
                   were scraped one by one). Alias twins are not de-duplicated
                   inside an aggregate (4 SP pairs, ≈2.6k ads).

Off-tree legacy regions appear as synthetic `legacy` children of the node
they attach to: their ads count, their runs earn no freshness.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from olx_patchright.export import infer_region
from olx_patchright.plan import is_usable, path_of

from .manifests import COMPLETE, INCOMPLETE
from .tree import UF_NAMES, title_from_slug

PROBLEM_STATUSES = ('unprobed', 'padded', 'unscoped')


@dataclass(frozen=True)
class Share:
    fresh: float      # None when no probed inventory backs the node
    baseline: float
    weight: int       # plan-leaf inventory the shares are taken over


@dataclass
class NodeStats:
    path: str
    name: str
    depth: int
    uf: str
    parent_path: str
    status: str
    inventory: int = None
    probed_at: object = None
    alias_of: str = None
    twins: list = field(default_factory=list)
    legacy: bool = False
    runs: list = field(default_factory=list)
    children: list = field(default_factory=list)
    collected: int = 0
    collected_subtree: int = 0
    located: int = None
    last_good: object = None
    eff_at: object = None
    eff_via: str = None
    complete_at: object = None
    complete_via: str = None

    @property
    def last_run(self):
        return self.runs[0] if self.runs else None

    @property
    def slug(self):
        return self.path.rsplit('/', 1)[-1]


@dataclass
class Invocation:
    key: str
    inferred: bool
    runs: list

    @property
    def started_at(self):
        return min(r.started_at for r in self.runs)

    @property
    def finished_at(self):
        ends = [r.finished_at for r in self.runs if r.finished_at]
        return max(ends) if ends else None

    def total(self, attr):
        return sum(getattr(r, attr) or 0 for r in self.runs)

    @property
    def outcomes(self):
        return Counter(r.outcome for r in self.runs)

    @property
    def stores(self):
        return sorted({r.store for r in self.runs})

    @property
    def source(self):
        if self.inferred:
            return 'legacy'
        return 'cli' if self.key.startswith('cli-') else 'panel'


class CoverageModel:
    def __init__(self, tree, runs, ad_rows, ad_count, page_cap):
        self.tree = tree
        self.runs = runs
        self.ad_count = ad_count
        self.page_cap = page_cap
        self.stats = {}
        self.unattached = [r for r in runs if r.attach_path is None]
        labels = self._labels(runs)
        for node in tree.walk():
            self.stats[node.path] = NodeStats(
                path=node.path, depth=node.depth, uf=node.uf,
                parent_path=node.parent_path,
                name=self._name(node.path, node.uf, node.depth, labels),
                status=tree.status[node.path], inventory=node.inventory,
                probed_at=node.probed_at, alias_of=tree.alias_of.get(node.path),
                twins=sorted(tree.twins.get(node.path, set()) - {node.path}),
                children=[k.path for k in tree.children[node.path]],
            )
        self._attach_runs(labels)
        self._roll_up_collected()
        self._locate(ad_rows)
        self._freshness()
        self._share_cache = {}

    # -- construction --------------------------------------------------------

    @staticmethod
    def _labels(runs):
        """Display names from v2 manifests' breadcrumbs (newest wins)."""
        labels = {}
        for run in sorted(runs, key=lambda r: r.started_at):
            for crumb in run.breadcrumb or []:
                if crumb.get('url') and crumb.get('label') and \
                        crumb.get('level') not in ('national', 'state'):
                    region = infer_region(crumb['url'])
                    if region != 'unknown':
                        labels[path_of(region)] = crumb['label']
        return labels

    @staticmethod
    def _name(path, uf, depth, labels):
        if depth == 1 and uf in UF_NAMES:
            return UF_NAMES[uf]
        return labels.get(path) or title_from_slug(path.rsplit('/', 1)[-1])

    def _attach_runs(self, labels):
        for run in sorted(self.runs, key=lambda r: r.started_at, reverse=True):
            if run.attach_path is None:
                continue
            if run.off_tree:
                stats = self.stats.get(run.node_path)
                if stats is None:
                    parent = self.stats[run.attach_path]
                    stats = NodeStats(
                        path=run.node_path, depth=run.node_path.count('/') + 1,
                        uf=parent.uf, parent_path=run.attach_path,
                        name=labels.get(run.node_path) or
                        title_from_slug(run.node_path.rsplit('/', 1)[-1]),
                        status='legacy', legacy=True,
                    )
                    self.stats[run.node_path] = stats
                    parent.children.append(run.node_path)
            else:
                stats = self.stats[run.attach_path]
            stats.runs.append(run)
            stats.collected += run.new_items or 0

    def _roll_up_collected(self):
        def total(path):
            stats = self.stats[path]
            stats.collected_subtree = stats.collected + sum(
                total(kid) for kid in stats.children)
            return stats.collected_subtree
        for root in self.tree.roots:
            total(root.path)

    def _locate(self, ad_rows):
        by_uf = Counter()
        by_macro = Counter()
        for row in ad_rows:
            by_uf[row['uf'].upper()] += row['n']
            by_macro[(row['uf'].upper(), row['macro'])] += row['n']
        for stats in self.stats.values():
            if stats.legacy:
                continue
            if stats.depth == 1:
                stats.located = by_uf.get(stats.uf, 0)
            elif stats.depth == 2:
                stats.located = by_macro.get((stats.uf, stats.slug), 0)

    def _group_runs(self, stats):
        members = [stats] + [self.stats[t] for t in stats.twins if t in self.stats]
        return [run for member in members for run in member.runs]

    def _freshness(self):
        for node in self.tree.walk():
            stats = self.stats[node.path]
            runs = self._group_runs(stats)
            good = [r for r in runs if r.credited]
            complete = [r for r in runs if r.outcome == COMPLETE]
            stats.last_good = max(good, key=lambda r: r.started_at) if good else None
            own_eff = stats.last_good.started_at if stats.last_good else None
            own_complete = max((r.started_at for r in complete), default=None)
            parent = self.stats.get(node.parent_path)
            stats.eff_at, stats.eff_via = _latest(
                (own_eff, node.path),
                (parent.eff_at, parent.eff_via) if parent else (None, None))
            stats.complete_at, stats.complete_via = _latest(
                (own_complete, node.path),
                (parent.complete_at, parent.complete_via) if parent else (None, None))

    # -- shares (depend on the freshness window and the clock) ---------------

    def shares(self, days, now):
        """path -> Share over the node's plan leaves (tree nodes only)."""
        key = (days, now.replace(second=0, microsecond=0))
        if key not in self._share_cache:
            self._share_cache = {key: self._compute_shares(days, now)}
        return self._share_cache[key]

    def _compute_shares(self, days, now):
        cutoff = now - timedelta(days=days)
        sums = {}

        def flags(stats):
            fresh = stats.eff_at is not None and stats.eff_at >= cutoff
            return fresh, stats.complete_at is not None

        def agg(path):
            if path in sums:
                return sums[path]
            stats = self.stats[path]
            node = self.tree.nodes[path]
            kids = self.tree.children[path]
            fresh, complete = flags(stats)
            if stats.status in PROBLEM_STATUSES:
                result = (0, 0, 0)
            elif stats.status == 'split':  # children, then the remainder
                parts = [agg(k.path) for k in kids]
                w = max(0, node.total_of_ads -
                        sum(k.total_of_ads for k in kids if is_usable(k)))
                result = (sum(p[0] for p in parts) + w,
                          sum(p[1] for p in parts) + (w if fresh else 0),
                          sum(p[2] for p in parts) + (w if complete else 0))
            else:
                # leaf / dense: one URL walks the whole scope. Without a run
                # of its own, credit what probed children were walked (e.g. a
                # state whose cities were scraped one by one), capped at w.
                w = node.total_of_ads
                parts = [agg(k.path) for k in kids if is_usable(k)]
                result = (w, w if fresh else min(w, sum(p[1] for p in parts)),
                          w if complete else min(w, sum(p[2] for p in parts)))
            sums[path] = result
            return result

        shares = {}
        for path in self.tree.nodes:
            total, fresh, complete = agg(path)
            shares[path] = Share(fresh / total if total else None,
                                 complete / total if total else None, total)
        return shares

    def brazil_share(self, days, now):
        """Share over every state's plan leaves."""
        shares = self.shares(days, now)
        parts = [shares[r.path] for r in self.tree.roots if shares[r.path].weight]
        total = sum(p.weight for p in parts)
        if not total:
            return Share(None, None, 0)
        return Share(sum(p.fresh * p.weight for p in parts) / total,
                     sum(p.baseline * p.weight for p in parts) / total, total)

    # -- queries for the views -----------------------------------------------

    def roots(self):
        return [self.stats[n.path] for n in self.tree.roots]

    def children(self, path):
        return [self.stats[p] for p in self.stats[path].children]

    def total_collected(self):
        return sum(r.new_items or 0 for r in self.runs)

    def invocations(self):
        groups = defaultdict(list)
        for run in self.runs:
            groups[run.invocation].append(run)
        invocations = [Invocation(key, not runs[0].invocation_id,
                                  sorted(runs, key=lambda r: r.started_at))
                       for key, runs in groups.items()]
        return sorted(invocations, key=lambda i: i.started_at, reverse=True)

    def find(self, query=None, uf=None, status=None, stale_days=None,
             never_fresh=False, incomplete=False, now=None):
        """Nodes matching every given filter, in path order."""
        query = (query or '').strip().lower()
        cutoff = now - timedelta(days=stale_days) if stale_days and now else None
        out = []
        for path in sorted(self.stats):
            s = self.stats[path]
            if query and query not in path and query not in s.name.lower():
                continue
            if uf and s.uf != uf:
                continue
            if status and s.status != status:
                continue
            if never_fresh and (s.eff_at is not None or s.legacy):
                continue
            if cutoff and (s.legacy or (s.eff_at is not None and s.eff_at >= cutoff)):
                continue
            if incomplete and not (s.last_run and s.last_run.outcome in INCOMPLETE):
                continue
            out.append(s)
        return out


def _latest(*candidates):
    """(timestamp, via) with the latest timestamp; ties keep the first."""
    best = (None, None)
    for ts, via in candidates:
        if ts is not None and (best[0] is None or ts > best[0]):
            best = (ts, via)
    return best
