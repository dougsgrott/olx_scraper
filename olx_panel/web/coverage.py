"""Coverage page: the OLX location tree with what was collected, OLX's live
inventory and freshness per node; filters switch to a flat list."""
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request

from olx_patchright.plan import start_url
from olx_panel.core.metrics import PROBLEM_STATUSES
from olx_panel.core.tree import UF_NAMES

from .common import render, service
from .pages import Page

router = APIRouter()
PAGE = Page('coverage', 'Coverage', router, nav_order=10)

DAY_CHOICES = (3, 7, 14, 30)
STATUSES = ('leaf', 'split', 'dense', 'unprobed', 'padded', 'unscoped', 'legacy')


def _freshness(stats, cutoff):
    if stats.legacy:
        return 'legacy'
    if stats.eff_at is None:
        return 'na' if stats.status in PROBLEM_STATUSES else 'never'
    return 'fresh' if stats.eff_at >= cutoff else 'stale'


def _baseline(stats, share):
    if stats.legacy:
        return 'na', '—'
    if stats.complete_at:
        return 'complete', f'complete {stats.complete_at:%Y-%m-%d}'
    if stats.status == 'dense':
        return 'truncated', 'truncated'
    if share and share.baseline is not None and \
            (stats.status == 'split' or share.baseline > 0):
        # per plan leaf (split), or what a leaf's scraped children cover
        return ('complete' if share.baseline >= 0.995 else 'partial',
                f'{share.baseline:.0%} complete')
    if stats.status == 'leaf':
        return 'never', 'never'
    return 'na', '—'


def _rows(model, stats_list, days, now):
    shares = model.shares(days, now)
    cutoff = now - timedelta(days=days)
    rows = []
    for stats in stats_list:
        share = shares.get(stats.path)
        baseline_cls, baseline = _baseline(stats, share)
        rows.append({
            's': stats,
            'share': share,
            'fresh_cls': _freshness(stats, cutoff),
            'baseline_cls': baseline_cls,
            'baseline': baseline,
        })
    return rows


def _days(request, days):
    return days if days in DAY_CHOICES else service(request).settings.fresh_days


@router.get('/coverage')
def coverage(request: Request, days: int = 0, q: str = '', uf: str = '',
             status: str = '', stale: int = 0, never: int = 0, incomplete: int = 0):
    svc = service(request)
    model = svc.model()
    days = _days(request, days)
    now = datetime.now().astimezone()
    filters = {'q': q, 'uf': uf, 'status': status, 'stale': stale,
               'never': never, 'incomplete': incomplete}
    filtered = any(filters.values())
    if filtered:
        found = model.find(query=q, uf=uf or None, status=status or None,
                           stale_days=stale or None, never_fresh=bool(never),
                           incomplete=bool(incomplete), now=now)
        rows = _rows(model, found[:500], days, now)
        truncated = len(found) > 500
    else:
        rows = _rows(model, model.roots(), days, now)
        truncated = False
    invocations = model.invocations()
    return render(
        request, 'coverage.html', active='coverage',
        rows=rows, filtered=filtered, truncated=truncated, filters=filters,
        days=days, day_choices=DAY_CHOICES, statuses=STATUSES,
        ufs=sorted(UF_NAMES), brazil=model.brazil_share(days, now),
        model=model, last_invocation=invocations[0] if invocations else None,
        n_invocations=len(invocations),
        scopes_scraped=sum(1 for s in model.stats.values() if s.runs),
        version=svc.status()['version'],
    )


@router.get('/coverage/tree')
def tree_rows(request: Request, parent: str, days: int = 0):
    model = service(request).model()
    if parent not in model.stats:
        raise HTTPException(404, f'unknown location path: {parent}')
    days = _days(request, days)
    now = datetime.now().astimezone()
    return render(request, 'partials/tree_rows.html',
                  rows=_rows(model, model.children(parent), days, now),
                  days=days, flat=False)


@router.get('/coverage/node')
def node_detail(request: Request, path: str, days: int = 0):
    svc = service(request)
    model = svc.model()
    stats = model.stats.get(path)
    if stats is None:
        raise HTTPException(404, f'unknown location path: {path}')
    days = _days(request, days)
    now = datetime.now().astimezone()
    [row] = _rows(model, [stats], days, now)
    url = None if stats.legacy else start_url(path, svc.settings.scope)
    return render(request, 'partials/node_detail.html', row=row, days=days,
                  url=url, ancestors=[model.stats[p] for p in
                                      reversed(model.tree.ancestors(path))
                                      if p in model.stats])
