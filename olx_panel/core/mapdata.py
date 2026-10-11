"""Map payloads: one value class per state, DDD area (UF-DDD) or
municipality, for one metric. The server decides classes and colours so the
browser only paints (`match` on a feature-state class).

Units and the OLX nodes behind them (via geo.Crosswalk):
  uf     the state node
  area   the macro most of the area's municipalities are listed under
         (GO-61 → the DF macro, whose `outras-cidades` holds those towns)
  muni   the municipality's own city/district/zone nodes (São Paulo → its
         five zones); without one, freshness metrics are inherited from the
         area's macro and drawn fainter, counts are left blank.
"""
from .geo import macro_of

LEVELS = ('uf', 'area', 'muni')
METRICS = {
    'located': 'Ads located there',
    'fresh': 'Fresh share',
    'baseline': 'Ever fully walked',
    'age': 'Freshness age',
    'inventory': 'OLX ads (live)',
    'collected': 'Ads collected via scope',
}
INHERITED_METRICS = ('fresh', 'baseline', 'age')
# viridis, light to dark: darker = more / fresher; colour-blind safe
VIRIDIS = ['#fde725', '#5ec962', '#21918c', '#3b528b', '#440154']
# neutral classes follow the page theme: the browser resolves these tokens
NONE_COLOR = 'var(--map-none)'
ZERO_COLOR = 'var(--map-zero)'

COUNT_BINS = [(1, '1–99'), (100, '100–999'), (1000, '1k–9.9k'),
              (10000, '10k–99k'), (100000, '≥ 100k')]
SHARE_BINS = [(0.0001, '1–25 %'), (0.25, '25–50 %'), (0.5, '50–75 %'),
              (0.75, '75–99 %'), (0.995, '100 %')]
AGE_BINS = [(90, '> 90 days'), (30, '31–90 days'), (7, '8–30 days'),
            (1, '2–7 days'), (0, '≤ 1 day')]


def legend(metric):
    if metric in ('fresh', 'baseline'):
        bins, zero = SHARE_BINS, '0 %'
    elif metric == 'age':
        bins, zero = AGE_BINS, 'never'
    else:
        bins, zero = COUNT_BINS, '0'
    return [{'label': zero, 'color': ZERO_COLOR}] + [
        {'label': label, 'color': color} for (_, label), color in zip(bins, VIRIDIS)]


def _count_class(n):
    if not n:
        return 0
    return max(i + 1 for i, (low, _) in enumerate(COUNT_BINS) if n >= low)


def _share_class(share):
    if not share:
        return 0
    return max(i + 1 for i, (low, _) in enumerate(SHARE_BINS) if share >= low)


def _age_class(days):
    if days is None:
        return 0
    for i, (threshold, _) in enumerate(AGE_BINS):
        if days > threshold:
            return i + 1
    return len(AGE_BINS)


def _fmt(n):
    return f'{n:,}'.replace(',', ' ')


def _aggregate(stats_list, shares, now):
    """Counts summed, shares weighted by plan-leaf inventory, freshness taken
    from the stalest node (several zones make one capital)."""
    inventories = [s.inventory for s in stats_list if s.inventory is not None]
    weighted = [shares[s.path] for s in stats_list
                if s.path in shares and shares[s.path].fresh is not None]
    weight = sum(sh.weight for sh in weighted)
    effs = [s.eff_at for s in stats_list]
    return {
        'inventory': sum(inventories) if inventories else None,
        'collected': sum(s.collected_subtree for s in stats_list),
        'fresh': sum(sh.fresh * sh.weight for sh in weighted) / weight if weight else None,
        'baseline': sum(sh.baseline * sh.weight for sh in weighted) / weight if weight else None,
        'age': (now - min(effs)).total_seconds() / 86400
        if effs and None not in effs else None,
    }


def _value(metric, agg, located, days):
    """(class, tooltip text) for one unit; class None = no data (grey)."""
    if metric == 'located':
        return _count_class(located), f'{_fmt(located)} ads located here'
    if metric in ('inventory', 'collected'):
        n = agg[metric]
        if n is None:
            return None, 'not probed' if metric == 'inventory' else 'no data'
        what = 'OLX ads live' if metric == 'inventory' else 'ads collected via scope'
        return _count_class(n), f'{_fmt(n)} {what}'
    if metric in ('fresh', 'baseline'):
        share = agg[metric]
        if share is None:
            return None, 'no probed inventory'
        what = f'fresh ≤ {days} d' if metric == 'fresh' else 'ever fully walked'
        return _share_class(share), f'{share:.0%} {what}'
    age = agg['age']
    if age is None:
        return 0, 'never fresh'
    return _age_class(age), f'fresh {age:.0f} d ago' if age >= 1 else 'fresh today'


def _units(model, level):
    """(key, display name, node paths, inherited) per map unit."""
    geo = model.geo
    gaz = geo.gaz
    if level == 'uf':
        for uf in sorted({m.uf for m in gaz.by_code.values()}):
            path = f'estado-{uf.lower()}'
            yield uf, uf, [path] if path in model.stats else [], False
    elif level == 'area':
        for area in sorted({m.area for m in gaz.by_code.values()}):
            macro = geo.area_macro.get(area)
            uf, ddd = area.split('-')
            yield area, f'{uf} · DDD {ddd}', [macro] if macro else [], False
    else:
        for code, place in gaz.by_code.items():
            own = geo.muni_nodes.get(code)
            if own:
                yield code, f'{place.name} ({place.uf})', own, False
            else:
                macro = geo.area_macro.get(place.area)
                yield code, f'{place.name} ({place.uf})', [macro] if macro else [], True


def map_payload(model, level, metric, days, now):
    if level not in LEVELS or metric not in METRICS:
        raise ValueError(f'unknown level/metric: {level}/{metric}')
    shares = model.shares(days, now)
    values = {}
    nodes = {}
    for key, name, paths, inherited in _units(model, level):
        stats = [model.stats[p] for p in paths if p in model.stats]
        if level == 'uf':
            located = stats[0].located if stats else 0
        elif level == 'area':
            located = model.located_by_area.get(key, 0)
        else:
            located = model.located_by_code.get(key, 0)
        if inherited and metric not in INHERITED_METRICS and metric != 'located':
            cls, text = None, 'no OLX scope of its own' \
                if paths else 'not listed by OLX'
        elif not stats and metric != 'located':
            cls, text = None, 'not listed by OLX'
        else:
            cls, text = _value(metric, _aggregate(stats, shares, now), located, days)
        # compact: 5.5k municipalities per metric switch; names come from
        # the topology, node paths from /coverage/muni on click
        values[key] = [cls, text, int(inherited and metric in INHERITED_METRICS)]
        if level != 'muni' and paths:
            nodes[key] = paths[0]
    return {'level': level, 'metric': metric, 'days': days,
            'title': METRICS[metric], 'legend': legend(metric),
            'none_color': NONE_COLOR, 'values': values, 'nodes': nodes}


def locate(model, path):
    """Map keys a tree node corresponds to at each level (for highlighting)."""
    geo = model.geo
    stats = model.stats.get(path)
    if geo is None or stats is None:
        return None
    macro = macro_of(path)
    code = stats.code
    if stats.legacy:
        code = None
    areas = geo.macro_areas.get(macro, [])
    if stats.depth >= 3 and code in geo.gaz.by_code:
        areas = [geo.gaz.by_code[code].area]
    return {'uf': path.split('/')[0][-2:].upper(), 'areas': areas,
            'muni': code if stats.depth >= 3 else None}
