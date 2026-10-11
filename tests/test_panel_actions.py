"""Phase 3 web routes: plan preview, YAML export, selection helpers, job
creation and job actions (the runner itself is in test_jobs.py)."""
import os

import pytest
import yaml
from fastapi.testclient import TestClient

from panel_env import build
from olx_panel.app import create_app

HX = {'HX-Request': 'true'}


@pytest.fixture
def app_client(tmp_path):
    settings, _ = build(str(tmp_path), geo=True)
    settings.jobs_dir = str(tmp_path / 'jobs')
    app = create_app(settings, background=False)
    with TestClient(app, base_url='http://localhost') as client:
        yield app, client


def test_plan_preview_and_yaml(app_client):
    app, client = app_client
    page = client.post('/coverage/plan', data={'path': ['estado-xx/macro-a/zona-sul']},
                       headers=HX).text
    assert 'Scrape plan' in page and 'Enqueue scrape' in page
    assert 'Moema' in page and 'dense' in page
    assert 'value="4"' in page  # patience from run_config.yaml
    full = client.post('/coverage/plan', headers=HX, data={
        'path': ['estado-xx/macro-a/zona-sul'], 'patience': '4', 'min_new_per_page': '5',
        'max_pages': '0', 'full_pass': '1'}).text
    assert 'name="full_pass" value="1" checked' in full
    response = client.post('/coverage/plan/yaml', headers={'Origin': 'http://localhost'},
                           data={'path': ['estado-xx/macro-b']})
    assert response.headers['content-disposition'].startswith('attachment')
    config = yaml.safe_load(response.text)
    assert config['catalog_spider']['start_urls'] == [
        'https://www.olx.com.br/imoveis/aluguel/estado-xx/macro-b?sf=1']


def test_selection_helpers(app_client):
    app, client = app_client
    assert 'estado-xx/macro-a/zona-sul/moema' in client.get('/coverage/stale?days=1').json()['paths']
    imported = client.get('/coverage/import-run-config').json()
    assert imported['offsets'] == 1 and len(imported['skipped']) == 1
    assert client.get('/api/unit-paths?level=muni&key=1000001').json()['paths'] == [
        'estado-xx/macro-a/centro', 'estado-xx/macro-a/zona-sul']
    assert client.get('/api/unit-paths?level=uf&key=XX').json()['paths'] == ['estado-xx']
    assert client.get('/api/unit-paths?level=area&key=XX-12').json()['paths'] == ['estado-xx/macro-b']


def test_enqueue_scrape_and_act_on_it(app_client):
    app, client = app_client
    response = client.post('/jobs/scrape', headers=HX, data={
        'path': ['estado-xx/macro-b'], 'patience': '4', 'min_new_per_page': '5',
        'max_pages': '0'})
    target = response.headers['hx-redirect']
    job_id = target.rsplit('/', 1)[-1]
    job = app.state.supervisor.jobs.get(job_id)
    assert job.kind == 'scrape' and job.state == 'queued'
    assert [u.url for u in job.urls] == ['https://www.olx.com.br/imoveis/aluguel/estado-xx/macro-b?sf=1']
    assert job.params['early_stop'] == {'patience': 4, 'min_new_per_page': 5}
    assert 'macro-b' in client.get(target).text
    assert client.get('/jobs').status_code == 200
    assert job_id in client.get('/jobs/table').text
    assert '1 queued' in client.get('/partials/banner').text
    card = client.post(f'/jobs/{job_id}/cancel', headers=HX).text
    assert 'cancelled' in card
    assert client.post('/jobs/scrape', headers=HX, data={'path': ['nope']}).status_code == 400


def test_warm_probe_and_queue_controls(app_client):
    app, client = app_client
    sup = app.state.supervisor
    client.post('/jobs/scrape', headers=HX, data={'path': ['estado-yy']})
    warm_id = client.post('/jobs/warm', headers=HX).headers['hx-redirect'].rsplit('/', 1)[-1]
    warm = sup.jobs.get(warm_id)
    assert warm.params['url'].endswith('centro/aclimacao?sf=1')  # run_config's first URL
    assert sup.jobs.queue()[0].id == warm_id                     # jumps the queue
    probe_id = client.post('/jobs/probe', headers=HX,
                           data={'path': 'estado-xx'}).headers['hx-redirect'].rsplit('/', 1)[-1]
    assert sup.jobs.get(probe_id).params['paths'] == ['estado-xx']
    assert client.post('/jobs/probe', headers=HX, data={'path': '../etc'}).status_code == 400
    client.post('/queue/pause', headers=HX)
    assert sup.jobs.paused() == 'paused by you'
    assert 'queue paused' in client.get('/partials/banner').text
    client.post('/queue/resume', headers=HX)
    assert sup.jobs.paused() is None


def test_log_stream_and_retry_of_a_finished_job(app_client):
    app, client = app_client
    sup = app.state.supervisor
    redirect = client.post('/jobs/scrape', headers=HX, data={
        'path': ['estado-xx/macro-a/zona-sul/moema', 'estado-xx/macro-b']}).headers['hx-redirect']
    job_id = redirect.rsplit('/', 1)[-1]
    job_dir = os.path.join(app.state.settings.jobs_dir, job_id)
    os.makedirs(job_dir)
    with open(os.path.join(job_dir, 'output.log'), 'w') as f:
        f.write('--- Scraping catalogue: 2 start URL(s) ---\n<b>not html</b>\n')
    sup.jobs.update(job_id, state='partial', job_dir=job_dir)
    with client.stream('GET', f'/jobs/{job_id}/log/stream') as response:
        body = ''.join(response.iter_text())
    assert 'data: --- Scraping catalogue: 2 start URL(s) ---' in body
    assert 'data: <b>not html</b>' in body          # sent as text; jobs.js inserts text nodes
    assert body.rstrip().endswith('event: end\ndata: partial')
    with client.stream('GET', f'/jobs/{job_id}/log/stream',
                       headers={'Last-Event-ID': '43'}) as response:
        resumed = ''.join(response.iter_text())
    assert 'Scraping catalogue' not in resumed and 'not html' in resumed
    # neither URL wrote a manifest: both are retryable
    retry = client.post(f'/jobs/{job_id}/retry', headers=HX, data={'full_pass': '1'})
    new_job = sup.jobs.get(retry.headers['hx-redirect'].rsplit('/', 1)[-1])
    assert len(new_job.urls) == 2 and new_job.params['full_pass'] is True
    assert new_job.params['early_stop'] == {}


def test_actions_need_the_panels_origin(app_client):
    app, client = app_client
    assert client.post('/jobs/scrape', data={'path': ['estado-yy']}).status_code == 403
    assert client.post('/jobs/warm', headers={'Origin': 'http://evil.example'}).status_code == 403
