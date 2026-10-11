import pytest
from fastapi.testclient import TestClient

from panel_env import build
from olx_panel.app import create_app


@pytest.fixture
def client(tmp_path):
    settings, _ = build(str(tmp_path), geo=True)
    with TestClient(create_app(settings, background=False),
                    base_url='http://localhost') as client:
        yield client


def values(client, level, metric, days=7):
    response = client.get(f'/api/map?level={level}&metric={metric}&days={days}')
    assert response.status_code == 200
    return response.json()


def test_located_counts_per_unit(client):
    muni = values(client, 'muni', 'located')
    assert muni['values']['1000001'][:2] == [1, '7 ads located here']
    assert muni['values']['1000002'][0] == 1
    assert muni['values']['1000003'][0] == 0               # Serra: none
    assert values(client, 'uf', 'located')['values']['XX'][1] == '10 ads located here'
    area = values(client, 'area', 'located')
    assert area['values']['XX-12'][1] == '3 ads located here'
    # YY has no depth-2 node, so its area maps to no macro
    assert area['nodes'] == {'XX-11': 'estado-xx/macro-a', 'XX-12': 'estado-xx/macro-b'}


def test_capital_aggregates_its_zones_and_towns_inherit(client):
    fresh = values(client, 'muni', 'fresh')['values']
    # centro (1200, stale) + zona-sul (6000, fresh) -> 83 %
    assert fresh['1000001'] == [4, '83% fresh ≤ 7 d', 0]
    # Serra has no node: the macro's freshness, drawn faint
    cls, text, inherited = fresh['1000003']
    assert inherited == 1 and text == '75% fresh ≤ 7 d'
    inventory = values(client, 'muni', 'inventory')['values']
    assert inventory['1000003'] == [None, 'no OLX scope of its own', 0]
    assert inventory['1000001'][1] == '7 200 OLX ads live'


def test_legend_and_bad_requests(client):
    data = values(client, 'uf', 'age')
    assert [e['label'] for e in data['legend']][0] == 'never'
    assert len(data['legend']) == 6
    assert client.get('/api/map?level=planet&metric=located').status_code == 400


def test_locate_and_municipality_detail(client):
    assert client.get('/api/locate?path=estado-xx/macro-a/zona-sul/moema').json() == \
        {'uf': 'XX', 'areas': ['XX-11'], 'muni': '1000001'}
    page = client.get('/coverage/muni?code=1000001&days=7').text
    assert 'Capital' in page and 'Zona Sul' in page and 'Centro' in page
    assert '7 ads' in page
    serra = client.get('/coverage/muni?code=1000003').text
    assert 'No OLX scope of its own' in serra
    assert client.get('/coverage/muni?code=0').status_code == 404


def test_city_nodes_get_names_and_located(client):
    page = client.get('/coverage/tree?parent=estado-xx/macro-b').text
    assert 'Cidade' in page
    detail = client.get('/coverage/node?path=estado-xx/macro-b/cidade').text
    assert 'city' in detail and 'Municipality' in detail
    assert client.get('/coverage').text.count('id="map"') == 1


def test_topology_served_only_when_built(client, tmp_path):
    assert client.get('/geo/br-municipios.topo.json').status_code == 200


def test_without_geo_assets_the_page_explains(tmp_path):
    settings, _ = build(str(tmp_path))
    with TestClient(create_app(settings, background=False), base_url='http://localhost') as c:
        assert 'scripts/build_geo.py' in c.get('/coverage').text
        assert c.get('/geo/br-municipios.topo.json').status_code == 404
        assert c.get('/api/map').status_code == 404
