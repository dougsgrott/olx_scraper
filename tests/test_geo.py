import csv
import json
import os

import pytest

from olx_panel.core.geo import Crosswalk, Gazetteer, Municipality, name_keys
from olx_panel.core.tree import LocationTree, Node

T = '2026-07-12'

MUNICIPALITIES = [
    # code, name, uf, ddd, capital
    ('5300108', 'Brasília', 'DF', '61', True),
    ('5212501', 'Luziânia', 'GO', '61', False),
    ('5208707', 'Goiânia', 'GO', '62', True),
    ('3550308', 'São Paulo', 'SP', '11', True),
    ('3505708', 'Barueri', 'SP', '11', False),
    ('3540200', 'Pontal', 'SP', '16', False),
    ('3500204', "Aparecida d'Oeste", 'SP', '17', False),
    ('3167202', 'Sete Lagoas', 'MG', '31', False),
    ('3106200', 'Belo Horizonte', 'MG', '31', True),
    ('3131307', 'Ipatinga', 'MG', '31', False),
    ('1302603', 'Manaus', 'AM', '92', True),
    ('1300409', 'Anori', 'AM', '97', False),
]


def gazetteer():
    return Gazetteer([Municipality(c, n, uf, ddd, cap, 0.0, 0.0)
                      for c, n, uf, ddd, cap in MUNICIPALITIES])


def tree(paths):
    nodes = []
    for path in paths:
        segments = path.split('/')
        nodes.append(Node(path, segments[0][-2:].upper(), '/'.join(segments[:-1]) or None,
                          segments[-1], len(segments), 100, None))
    return LocationTree(nodes, 5000)


PATHS = [
    'estado-df', 'estado-df/distrito-federal-e-regiao',
    'estado-df/distrito-federal-e-regiao/taguatinga',
    'estado-df/distrito-federal-e-regiao/taguatinga/centro',
    'estado-df/distrito-federal-e-regiao/outras-cidades',
    'estado-df/distrito-federal-e-regiao/outras-cidades/luziania',
    'estado-go', 'estado-go/grande-goiania-e-anapolis',
    'estado-go/grande-goiania-e-anapolis/regiao-central',
    'estado-go/grande-goiania-e-anapolis/regiao-central/setor-oeste',
    'estado-sp', 'estado-sp/sao-paulo-e-regiao',
    'estado-sp/sao-paulo-e-regiao/zona-sul', 'estado-sp/sao-paulo-e-regiao/zona-sul/moema',
    'estado-sp/sao-paulo-e-regiao/barueri', 'estado-sp/sao-paulo-e-regiao/alphaville',
    'estado-sp/regiao-de-ribeirao-preto', 'estado-sp/regiao-de-ribeirao-preto/candia-pontal',
    'estado-sp/regiao-de-sao-jose-do-rio-preto',
    'estado-sp/regiao-de-sao-jose-do-rio-preto/aparecida-doeste',
    'estado-mg', 'estado-mg/belo-horizonte-e-regiao',
    'estado-mg/belo-horizonte-e-regiao/regiao-de-sete-lagoas',
    'estado-mg/belo-horizonte-e-regiao/regiao-de-sete-lagoas/7-lagoas',
    'estado-mg/belo-horizonte-e-regiao/zona-da-mata',
    'estado-mg/belo-horizonte-e-regiao/regiao-de-ipatinga',
    'estado-am', 'estado-am/manaus-e-regiao', 'estado-am/manaus-e-regiao/manaus',
    'estado-am/leste-do-amazonas', 'estado-am/leste-do-amazonas/regiao-do-sul-amazonense',
]


@pytest.fixture
def crosswalk():
    overrides = {'estado-sp/sao-paulo-e-regiao/alphaville': ('district', '3505708')}
    return Crosswalk(tree(PATHS), gazetteer(), overrides,
                     breadcrumb_ddd={'estado-sp/sao-paulo-e-regiao': '11'})


def kind(cw, path):
    geo = cw.nodes[path]
    return geo.kind, geo.code


def test_name_keys_cover_olx_slug_habits():
    assert 'aparecida-doeste' in name_keys("Aparecida d'Oeste")
    assert 'aparecida-d-oeste' in name_keys("Aparecida d'Oeste")
    assert '7-lagoas' in name_keys('Sete Lagoas')
    assert 'santa-rita-do-passa-4' in name_keys('Santa Rita do Passa Quatro')
    assert 'sao-paulo' in name_keys('São Paulo')


def test_gazetteer_lookups():
    gaz = gazetteer()
    assert gaz.match('luziania', 'DF') is None
    assert gaz.match_in_ddd('luziania', '61') == '5212501'
    assert gaz.match_suffix('candia-pontal', 'SP') == '3540200'
    assert gaz.match_spans('grande-goiania-e-anapolis', 'GO') == ['5208707']
    assert gaz.by_name('Vila Nova (Barueri)', 'SP') == '3505708'
    assert gaz.by_name('Matilde', 'SP', {('SP', 'Matilde'): '3505708'}) == '3505708'
    assert gaz.by_name('Nowhere', 'SP') is None


def test_kinds_are_decided_by_structure_then_names(crosswalk):
    cw = crosswalk
    assert kind(cw, 'estado-sp') == ('state', None)
    assert kind(cw, 'estado-sp/sao-paulo-e-regiao') == ('ddd_area', None)
    assert kind(cw, 'estado-sp/sao-paulo-e-regiao/barueri') == ('city', '3505708')
    # a zone of the capital the macro names; its children are its bairros
    assert kind(cw, 'estado-sp/sao-paulo-e-regiao/zona-sul') == ('zone', '3550308')
    assert kind(cw, 'estado-sp/sao-paulo-e-regiao/zona-sul/moema') == ('neighbourhood', '3550308')
    assert kind(cw, 'estado-go/grande-goiania-e-anapolis/regiao-central') == ('zone', '5208707')
    # DF: macro names no municipality -> zones of the UF capital
    assert kind(cw, 'estado-df/distrito-federal-e-regiao/taguatinga') == ('zone', '5300108')
    # GO towns listed under the DF macro (DDD 61) match across UFs
    assert kind(cw, 'estado-df/distrito-federal-e-regiao/outras-cidades') == ('aggregate', None)
    assert kind(cw, 'estado-df/distrito-federal-e-regiao/outras-cidades/luziania') == \
        ('city', '5212501')
    assert kind(cw, 'estado-sp/regiao-de-sao-jose-do-rio-preto/aparecida-doeste') == \
        ('city', '3500204')
    assert kind(cw, 'estado-mg/belo-horizonte-e-regiao/regiao-de-sete-lagoas') == ('aggregate', None)
    assert kind(cw, 'estado-mg/belo-horizonte-e-regiao/regiao-de-sete-lagoas/7-lagoas') == \
        ('city', '3167202')
    assert kind(cw, 'estado-sp/regiao-de-ribeirao-preto/candia-pontal') == ('district', '3540200')
    # childless regions stay aggregates, even when a town ends the slug
    assert kind(cw, 'estado-mg/belo-horizonte-e-regiao/zona-da-mata') == ('aggregate', None)
    assert kind(cw, 'estado-mg/belo-horizonte-e-regiao/regiao-de-ipatinga') == ('aggregate', None)
    assert kind(cw, 'estado-sp/sao-paulo-e-regiao/alphaville') == ('district', '3505708')
    assert cw.nodes['estado-sp/sao-paulo-e-regiao/alphaville'].method == 'override'
    assert not cw.report()['unknown']


def test_macro_ddds_and_areas(crosswalk):
    cw = crosswalk
    assert cw.macro_ddd['estado-sp/sao-paulo-e-regiao'] == '11'          # breadcrumb
    assert cw.macro_ddd['estado-mg/belo-horizonte-e-regiao'] == '31'     # city votes
    assert cw.macro_ddd['estado-go/grande-goiania-e-anapolis'] == '62'   # slug names Goiânia
    assert cw.macro_ddd['estado-df/distrito-federal-e-regiao'] == '61'   # UF's only DDD
    assert cw.macro_ddd['estado-am/leste-do-amazonas'] == '97'           # left unclaimed
    # Luziânia (GO, 61) is listed under the DF macro: area GO-61 maps there
    assert cw.area_macro['GO-61'] == 'estado-df/distrito-federal-e-regiao'
    assert set(cw.macro_areas['estado-df/distrito-federal-e-regiao']) == {'DF-61', 'GO-61'}
    assert sorted(cw.muni_nodes['3550308']) == ['estado-sp/sao-paulo-e-regiao/zona-sul']
    assert cw.nodes['estado-df/distrito-federal-e-regiao/outras-cidades/luziania'].ddd == '61'


def test_build_geo_joins_sources_offline(tmp_path):
    from scripts import build_geo
    sources = tmp_path / 'sources'
    sources.mkdir()
    topology = {'type': 'Topology', 'arcs': [[[0, 0], [1, 0], [0, 1], [-1, 0], [0, -1]]],
                'objects': {'BRMU': {'type': 'GeometryCollection', 'geometries': [
                    {'type': 'Polygon', 'arcs': [[0]], 'properties': {'codarea': '3550308'}},
                    {'type': 'Polygon', 'arcs': [[0]], 'properties': {'codarea': '9999999'}},
                ]}}}
    (sources / build_geo.TOPOLOGY_FILE).write_text(json.dumps(topology))
    (sources / build_geo.NAMES_FILE).write_text(json.dumps([
        {'municipio-id': 3550308, 'municipio-nome': 'São Paulo', 'UF-sigla': 'SP'},
        {'municipio-id': 9999999, 'municipio-nome': 'Nova', 'UF-sigla': 'SP'},
    ]))
    with open(sources / build_geo.KELVINS_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['codigo_ibge', 'nome', 'latitude', 'longitude', 'capital',
                         'codigo_uf', 'siafi_id', 'ddd', 'fuso_horario'])
        writer.writerow(['3550308', 'São Paulo', '-23.5', '-46.6', '1', '35', '7107', '11',
                         'America/Sao_Paulo'])
    problems = build_geo.build(str(sources), str(tmp_path))
    assert problems == ['9999999 Nova: no DDD in the kelvins table']
    out = json.loads((tmp_path / build_geo.OUT_TOPOLOGY).read_text())
    geometries = out['objects']['municipios']['geometries']
    assert geometries[0]['properties'] == {'c': '3550308', 'u': 'SP', 'd': '11',
                                           'a': 'SP-11', 'n': 'São Paulo'}
    rows = list(csv.DictReader(open(tmp_path / build_geo.OUT_CSV, encoding='utf-8')))
    assert [(r['code'], r['ddd'], r['capital']) for r in rows] == \
        [('3550308', '11', '1'), ('9999999', '', '0')]
    assert os.path.exists(tmp_path / build_geo.OUT_TOPOLOGY)
