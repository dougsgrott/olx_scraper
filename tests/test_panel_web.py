import pytest
from fastapi.testclient import TestClient

from panel_env import build
from olx_panel.app import PanelAlreadyRunning, create_app


@pytest.fixture
def client(tmp_path):
    settings, _ = build(str(tmp_path))
    with TestClient(create_app(settings, background=False),
                    base_url='http://localhost') as client:
        yield client


@pytest.mark.parametrize('url, expect', [
    ('/coverage', 'Estado Xx'),
    ('/coverage?days=30', 'Fresh ≤ 30 days'),
    ('/coverage/tree?parent=estado-xx/macro-a', 'Zona Sul'),
    ('/coverage/tree?parent=estado-xx/macro-a', 'serra'),
    ('/coverage/node?path=estado-xx/macro-a/zona-sul/moema', 'capped'),
    ('/coverage/node?path=estado-xx/macro-a/serra', 'Off-tree legacy scope'),
    ('/coverage?q=moema', 'estado-xx/macro-a/zona-sul/moema'),
    ('/coverage?status=dense', 'Moema'),
    ('/coverage?never=1', 'Estado Yy'),
    ('/coverage?incomplete=1', 'Moema'),
    ('/runs', 'start past page 1'),
    ('/partials/banner?v=0-0', 'new data'),
])
def test_pages_render(client, url, expect):
    response = client.get(url)
    assert response.status_code == 200
    assert expect in response.text


def test_root_redirects_and_unknown_paths_404(client):
    assert client.get('/', follow_redirects=False).headers['location'] == '/coverage'
    assert client.get('/coverage/node?path=nope').status_code == 404
    assert client.get('/coverage/tree?parent=nope').status_code == 404
    assert client.get('/runs/invocation?key=nope').status_code == 404


def test_invocation_rows(client):
    page = client.get('/runs').text
    key = page.split('/runs/invocation?key=')[1].split('"')[0]
    response = client.get(f'/runs/invocation?key={key}')
    assert response.status_code == 200 and '<table' in response.text


def test_post_needs_the_panels_own_origin(client):
    assert client.post('/admin/refresh').status_code == 403
    assert client.post('/admin/refresh', headers={'Origin': 'http://evil.example'}).status_code == 403
    assert client.post('/admin/refresh', headers={'Origin': 'http://localhost'}).status_code == 200
    assert client.post('/admin/refresh', headers={'HX-Request': 'true'}).status_code == 200


def test_foreign_host_is_rejected(client):
    assert client.get('/coverage', headers={'Host': 'evil.example'}).status_code == 400


def test_second_panel_refuses_to_start(tmp_path):
    settings, _ = build(str(tmp_path))
    with TestClient(create_app(settings, background=False), base_url='http://localhost'):
        with pytest.raises(PanelAlreadyRunning):
            with TestClient(create_app(settings, background=False),
                            base_url='http://localhost'):
                pass
