from collections import namedtuple
from datetime import datetime

from olx_patchright.export import infer_region
from olx_patchright.plan import (
    PADDED, UNSCOPED, build_plan, classify_node, find_aliases, norm, path_of,
    region_of, start_url,
)

Row = namedtuple('Row', 'path parent_path total_of_ads probed_at')
T = datetime(2026, 7, 12)


def row(path, count, probed=True):
    parent = '/'.join(path.split('/')[:-1]) or None
    return Row(path, parent, count, T if probed else None)


# A small tree: a split state whose dense macro holds a zone with an alias
# pair, a padded and an unscoped child, and an unprobed city.
TREE = [
    row('estado-xx', 9000),
    row('estado-xx/macro-a', 8000),
    row('estado-xx/macro-a/centro', 1200),
    row('estado-xx/macro-a/centro/aclimacao', 700),
    row('estado-xx/macro-a/centro/se', 300),
    row('estado-xx/macro-a/zona-sul', 6000),
    row('estado-xx/macro-a/zona-sul/aclimacao', 700),
    row('estado-xx/macro-a/zona-sul/moema', 5200),
    row('estado-xx/macro-a/zona-sul/vazia', PADDED),
    row('estado-xx/macro-b', 900),
    row('estado-xx/macro-b/cidade', None, probed=False),
    row('estado-xx/macro-c', 7000),
    row('estado-xx/macro-c/ignorada', UNSCOPED),
    row('estado-xx/macro-d', None, probed=False),
]


def kids(path):
    return [r for r in TREE if r.parent_path == path]


def by_path(path):
    return next(r for r in TREE if r.path == path)


def test_classify_node():
    assert classify_node(by_path('estado-xx'), kids('estado-xx')) == 'split'
    assert classify_node(by_path('estado-xx/macro-b'), []) == 'leaf'
    assert classify_node(by_path('estado-xx/macro-d'), []) == 'unprobed'
    assert classify_node(by_path('estado-xx/macro-a/zona-sul/vazia'), []) == 'padded'
    assert classify_node(by_path('estado-xx/macro-c/ignorada'), []) == 'unscoped'
    # Over the threshold with only an UNSCOPED child: cannot be split.
    assert classify_node(by_path('estado-xx/macro-c'), kids('estado-xx/macro-c')) == 'dense'
    assert classify_node(by_path('estado-xx/macro-a/zona-sul/moema'), []) == 'dense'


def test_build_plan_orders_children_before_remainder():
    plan, warnings = build_plan(TREE, ['estado-xx'], threshold=5000)
    assert plan == [
        ('estado-xx/macro-a/centro', 1200, 'leaf'),
        ('estado-xx/macro-a/zona-sul/aclimacao', 700, 'leaf'),
        ('estado-xx/macro-a/zona-sul/moema', 5200, 'dense'),
        ('estado-xx/macro-a/zona-sul', 6000, 'remainder'),
        ('estado-xx/macro-a', 8000, 'remainder'),
        ('estado-xx/macro-b', 900, 'leaf'),
        ('estado-xx/macro-c', 7000, 'dense'),
        ('estado-xx', 9000, 'remainder'),
    ]
    assert warnings == ['estado-xx/macro-d: unprobed, skipped']


def test_build_plan_drops_nested_and_repeated_roots():
    nested, _ = build_plan(TREE, ['estado-xx/macro-a/centro', 'estado-xx',
                                  'estado-xx'], threshold=5000)
    whole, _ = build_plan(TREE, ['estado-xx'], threshold=5000)
    assert nested == whole


def test_find_aliases_matches_same_place_same_count():
    assert find_aliases(TREE) == {
        'estado-xx/macro-a/zona-sul/aclimacao': 'estado-xx/macro-a/centro/aclimacao',
    }
    different_count = TREE + [row('estado-xx/macro-a/zona-sul/se', 301)]
    assert 'estado-xx/macro-a/zona-sul/se' not in find_aliases(different_count)
    other_macro = TREE + [row('estado-xx/macro-b/aclimacao', 700)]
    assert 'estado-xx/macro-b/aclimacao' not in find_aliases(other_macro)


def test_build_plan_keeps_the_first_alias_twin_it_reaches():
    tree = TREE[:2] + [row('estado-xx/macro-a/centro', 6000)] + TREE[3:]
    aliases = find_aliases(tree)
    plan, _ = build_plan(tree, ['estado-xx/macro-a'], 5000, aliases)
    paths = [p for p, _, _ in plan]
    assert 'estado-xx/macro-a/centro/aclimacao' in paths
    assert 'estado-xx/macro-a/zona-sul/aclimacao' not in paths
    # Only the non-canonical twin's parent selected: it is still scraped.
    plan, _ = build_plan(tree, ['estado-xx/macro-a/zona-sul'], 5000, aliases)
    assert ('estado-xx/macro-a/zona-sul/aclimacao', 700, 'leaf') in plan


def test_start_url_and_region_mappings():
    path = 'estado-es/norte-do-espirito-santo/vitoria'
    assert start_url(path) == \
        'https://www.olx.com.br/imoveis/aluguel/estado-es/norte-do-espirito-santo/vitoria?sf=1'
    assert start_url(path, newest_first=False) == \
        'https://www.olx.com.br/imoveis/aluguel/estado-es/norte-do-espirito-santo/vitoria'
    assert start_url(path, page=3).endswith('?sf=1&o=3')
    assert start_url(path, scope='/imoveis/venda/').startswith(
        'https://www.olx.com.br/imoveis/venda/estado-es/')
    assert region_of(path) == infer_region(start_url(path)) == \
        'estado-es_norte-do-espirito-santo_vitoria'
    assert path_of(region_of(path)) == path


def test_norm_matches_olx_slugs():
    assert norm('Vitória') == 'vitoria'
    assert norm("Santa Bárbara d'Oeste") == 'santa-barbara-d-oeste'
    assert norm('  Jardim  Camburi ') == 'jardim-camburi'
