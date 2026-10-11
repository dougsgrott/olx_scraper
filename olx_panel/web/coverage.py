"""Coverage page: a map of Brazil (states, DDD areas, municipalities) over
the OLX location tree with what was collected, OLX's live inventory and
freshness per node; filters switch the tree to a flat list."""
import os
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from olx_patchright.plan import norm, start_url
from olx_panel.core import planning
from olx_panel.core.mapdata import LEVELS, METRICS, locate, map_payload
from olx_panel.core.metrics import PROBLEM_STATUSES
from olx_panel.core.tree import UF_NAMES

from .common import render, service, supervisor
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
        map_ready=model.geo is not None, levels=LEVELS, metrics=METRICS,
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
    expected = model.expected_new(stats, now)
    return render(request, 'partials/node_detail.html', row=row, days=days,
                  url=url, subtree=_subtree_size(model, path), expected=expected,
                  due=expected is not None and expected >= svc.settings.due_new_ads,
                  ancestors=[model.stats[p] for p in reversed(model.tree.ancestors(path))
                             if p in model.stats])


def _subtree_size(model, path):
    size = unprobed = 0
    stack = [path]
    while stack:
        current = stack.pop()
        node = model.tree.nodes.get(current)
        if node is None:
            continue
        size += 1
        unprobed += node.probed_at is None
        stack.extend(k.path for k in model.tree.children[current])
    return {'size': size, 'unprobed': unprobed}


# -- map ----------------------------------------------------------------------

@router.get('/geo/br-municipios.topo.json')
def topology(request: Request):
    path = service(request).settings.geo_topology
    if not os.path.exists(path):
        raise HTTPException(404, 'map assets missing: run '
                                 '`uv run python scripts/build_geo.py`')
    return FileResponse(path, media_type='application/json',
                        headers={'Cache-Control': 'no-cache'})


@router.get('/api/map')
def map_values(request: Request, level: str = 'uf', metric: str = 'located',
               days: int = 0):
    model = service(request).model()
    if model.geo is None:
        raise HTTPException(404, 'map assets missing')
    if level not in LEVELS or metric not in METRICS:
        raise HTTPException(400, f'unknown level/metric: {level}/{metric}')
    return JSONResponse(map_payload(model, level, metric, _days(request, days),
                                    datetime.now().astimezone()))


@router.get('/api/locate')
def locate_node(request: Request, path: str):
    found = locate(service(request).model(), path)
    if found is None:
        raise HTTPException(404, f'unknown location path: {path}')
    return found


def _hood_matches(model, roots):
    """Neighbourhood nodes under a municipality's nodes, by normalised slug."""
    nodes = {}
    stack = list(roots)
    while stack:
        path = stack.pop()
        stats = model.stats.get(path)
        if stats is None:
            continue
        if stats.kind == 'neighbourhood':
            nodes[stats.slug] = path
        stack.extend(stats.children)
    return nodes


def _match_hood(name, hood_nodes):
    """The tree node an ad's neighbourhood name most likely falls under:
    exact slug, else containment (OLX groups bairros: ibirapuera-jd-luzitania)."""
    key = norm(name or '')
    if not key:
        return None
    if key in hood_nodes:
        return hood_nodes[key], True
    for slug, path in hood_nodes.items():
        if len(key) >= 4 and (key in slug or (len(slug) >= 4 and slug in key)):
            return path, False
    return None


@router.get('/coverage/muni')
def municipality_detail(request: Request, code: str, days: int = 0):
    model = service(request).model()
    geo = model.geo
    if geo is None or code not in geo.gaz.by_code:
        raise HTTPException(404, f'unknown municipality: {code}')
    place = geo.gaz.by_code[code]
    days = _days(request, days)
    now = datetime.now().astimezone()
    own = [model.stats[p] for p in geo.muni_nodes.get(code, []) if p in model.stats]
    area_macro = geo.area_macro.get(place.area)
    hood_nodes = _hood_matches(model, [s.path for s in own])
    hoods = defaultdict(lambda: {'n': 0, 'last_seen': None})
    for row in model.ad_rows_by_code.get(code, []):
        hood = hoods[row['neighbourhood']]
        hood['n'] += row['n']
        hood['last_seen'] = max(filter(None, (hood['last_seen'], row['last_seen'])),
                                default=None)
    hood_rows = sorted(({'name': name, 'n': h['n'], 'last_seen': h['last_seen'],
                         'match': _match_hood(name, hood_nodes)}
                        for name, h in hoods.items()), key=lambda r: -r['n'])
    return render(request, 'partials/muni_detail.html', place=place, days=days,
                  rows=_rows(model, own, days, now),
                  area_macro=model.stats.get(area_macro) if area_macro else None,
                  located=model.located_by_code.get(code, 0), hoods=hood_rows)


# -- selection -> plan (Phase 3) ----------------------------------------------

def _queued_paths(request):
    sup = supervisor(request)
    return {u.path for job in sup.jobs.queue() + sup.jobs.active() for u in job.urls}


def _plan_options(request, patience, min_new_per_page, max_pages, full_pass, mode=None):
    if patience is None:  # first preview: the user's run_config.yaml settings
        options = planning.options_from_run_config(service(request).run_config())
        return planning.Options(options.patience, options.min_new_per_page,
                                options.max_pages, mode=mode or planning.INCREMENTAL,
                                full_pass=bool(full_pass))
    return planning.Options(patience=max(0, patience),
                            min_new_per_page=max(1, min_new_per_page or 1),
                            max_pages=max(0, max_pages or 0), full_pass=bool(full_pass),
                            mode=mode or planning.INCREMENTAL)


@router.post('/coverage/plan')
def plan_preview(request: Request, path: list[str] = Form(default=[]),
                 patience: int = Form(None), min_new_per_page: int = Form(None),
                 max_pages: int = Form(None), full_pass: str = Form(''), mode: str = Form('')):
    svc = service(request)
    options = _plan_options(request, patience, min_new_per_page, max_pages, full_pass, mode)
    preview = planning.preview(svc.model(), path, options, svc.settings.scope,
                               _queued_paths(request))
    return render(request, 'partials/plan_preview.html', preview=preview,
                  browser=svc.status()['browser'])


@router.post('/coverage/plan/yaml')
def plan_yaml(request: Request, path: list[str] = Form(default=[]),
              patience: int = Form(None), min_new_per_page: int = Form(None),
              max_pages: int = Form(None), full_pass: str = Form(''), mode: str = Form('')):
    svc = service(request)
    options = _plan_options(request, patience, min_new_per_page, max_pages, full_pass, mode)
    preview = planning.preview(svc.model(), path, options, svc.settings.scope)
    return Response(planning.plan_yaml(preview), media_type='application/x-yaml',
                    headers={'Content-Disposition':
                             'attachment; filename="run_config.panel.yaml"'})


@router.get('/coverage/stale')
def stale_selection(request: Request, days: int = 0):
    svc = service(request)
    days = days if days > 0 else svc.settings.fresh_days
    return {'paths': planning.stale_paths(svc.model(), days, datetime.now().astimezone())}


@router.get('/coverage/due')
def due_selection(request: Request):
    svc = service(request)
    return {'paths': planning.due_paths(svc.model(), datetime.now().astimezone(),
                                        svc.settings.due_new_ads, svc.settings.fresh_days)}


@router.get('/coverage/import-run-config')
def import_run_config(request: Request):
    svc = service(request)
    paths, skipped, offsets = planning.run_config_paths(svc.run_config(), svc.model().tree)
    return {'paths': paths, 'skipped': skipped, 'offsets': offsets}


@router.get('/api/unit-paths')
def unit_paths(request: Request, level: str, key: str):
    """The OLX nodes behind one map unit (Shift-click adds them)."""
    model = service(request).model()
    geo = model.geo
    if geo is None:
        raise HTTPException(404, 'map assets missing')
    if level == 'uf':
        paths = [f'estado-{key.lower()}']
    elif level == 'area':
        paths = [geo.area_macro[key]] if key in geo.area_macro else []
    elif level == 'muni':
        paths = list(geo.muni_nodes.get(key, []))
    else:
        raise HTTPException(400, f'unknown level: {level}')
    return {'paths': [p for p in paths if p in model.tree]}
