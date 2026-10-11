"""The job runner end to end: real wrapper and job subprocesses (a fake
scraper on the real scrape loop), real signals, driven tick by tick."""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from panel_env import build
from olx_patchright import browser
from olx_panel.core import planning
from olx_panel.core.jobs import kinds
from olx_panel.core.jobs.model import JobUrl
from olx_panel.core.jobs.runner import Supervisor
from olx_panel.core.service import PanelService

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = (sys.executable, '-u', os.path.join(HERE, 'fake_scrape.py'))
YY = 'https://www.olx.com.br/imoveis/aluguel/estado-yy?sf=1'
MACRO_B = 'https://www.olx.com.br/imoveis/aluguel/estado-xx/macro-b?sf=1'


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings, _ = build(str(tmp_path))
    settings.scrape_command = FAKE
    settings.cleanup_command = (sys.executable, '-c', 'print("Browser profile freed.")')
    settings.jobs_dir = str(tmp_path / 'jobs')
    settings.cancel_grace_s = 15.0   # cooperative stops finish well within this
    settings.term_grace_s = 1.0
    settings.busy_backoff_s = 0.2
    service = PanelService(settings)
    service.refresh()
    supervisor = Supervisor(service)
    yield service, supervisor, monkeypatch
    for job in supervisor.jobs.active():  # never leave a test job running
        try:
            os.killpg(job.wrapper_pid, signal.SIGKILL)
        except (ProcessLookupError, TypeError):
            pass


def scrape(supervisor, *urls, early_stop=None):
    return supervisor.enqueue(
        kinds.SCRAPE, 'test scrape', {'early_stop': early_stop or {}},
        [JobUrl(i, url.split('aluguel/')[1].split('?')[0], url, 'leaf', 1)
         for i, url in enumerate(urls)])


def drive(supervisor, job_id, until, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        supervisor.tick()
        job = supervisor.jobs.get(job_id)
        if until(job):
            return job
        time.sleep(0.1)
    raise AssertionError(f'timed out; job is {supervisor.jobs.get(job_id)}')


def log_of(job):
    with open(os.path.join(job.job_dir, 'output.log')) as f:
        return f.read()


def test_scrape_job_succeeds_and_its_manifest_names_it(env):
    service, sup, _ = env
    job = scrape(sup, YY)
    job = drive(sup, job.id, lambda j: j.finished)
    assert (job.state, job.exit_code) == ('succeeded', 0)
    assert 'Page 1' in log_of(job)
    [outcome] = sup.url_outcomes(job)
    assert outcome['run'].outcome == 'complete'
    assert outcome['run'].invocation == job.id
    assert [i.source for i in service.model().invocations() if i.key == job.id] == ['panel']
    params = json.load(open(os.path.join(job.job_dir, 'params.json')))
    assert params['argv'][-2] == '--config'


def test_cancel_finishes_the_page_and_skips_later_urls(env):
    service, sup, monkeypatch = env
    monkeypatch.setenv('FAKE_TOTAL', '1000')
    monkeypatch.setenv('FAKE_DELAY', '0.3')
    job = scrape(sup, YY, MACRO_B)
    job = drive(sup, job.id, lambda j: j.state == 'running' and 'Page 2' in log_of(j))
    sup.cancel(job.id)
    assert sup.jobs.get(job.id).state == 'cancelling'
    job = drive(sup, job.id, lambda j: j.finished)
    assert job.state == 'cancelled' and job.exit_code == 130
    first, second = sup.url_outcomes(job)
    assert first['run'].outcome == 'cancelled' and first['run'].pages_scraped >= 2
    assert second['run'] is None


def test_overdue_cancel_escalates_and_force_kill_queues_cleanup(env):
    service, sup, monkeypatch = env
    monkeypatch.setenv('FAKE_TOTAL', '1000')
    monkeypatch.setenv('FAKE_DELAY', '30')   # a page that never finishes in time
    job = scrape(sup, YY)
    job = drive(sup, job.id, lambda j: j.state == 'running')
    time.sleep(0.5)
    sup.force(job.id)
    job = drive(sup, job.id, lambda j: j.finished)
    assert job.state == 'cancelled' and 'killed' in job.state_reason
    [cleanup] = [j for j in sup.jobs.list() if j.kind == kinds.CLEANUP]  # queued at the front
    cleanup = drive(sup, cleanup.id, lambda j: j.finished)
    assert cleanup.state == 'succeeded' and 'freed' in log_of(cleanup)


def test_sigterm_escalation_after_the_grace_period(env):
    service, sup, monkeypatch = env
    sup.settings.cancel_grace_s = 1.0
    monkeypatch.setenv('FAKE_TOTAL', '1000')
    monkeypatch.setenv('FAKE_DELAY', '30')
    job = scrape(sup, YY)
    job = drive(sup, job.id, lambda j: j.state == 'running')
    time.sleep(0.5)
    sup.cancel(job.id)
    job = drive(sup, job.id, lambda j: j.finished, timeout=30)
    assert job.state == 'cancelled' and job.term_sent_at is not None
    assert job.kill_sent_at is None  # the second signal stopped it at once


def test_waits_while_another_process_holds_the_browser(env):
    service, sup, _ = env
    holder = subprocess.Popen(
        [sys.executable, '-c',
         'import sys, time; sys.path.insert(0, sys.argv[1]); '
         'from olx_patchright import browser; browser.LOCK_PATH = sys.argv[2]; '
         'browser.acquire_browser_lock(label="external CLI run"); print("ready", flush=True); '
         'time.sleep(60)', os.path.dirname(HERE), browser.LOCK_PATH],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == 'ready'
        job = scrape(sup, YY)
        sup.tick()
        job = sup.jobs.get(job.id)
        assert job.state == 'waiting' and 'external CLI run' in job.state_reason
    finally:
        holder.kill()
        holder.wait()
    job = drive(sup, job.id, lambda j: j.finished)
    assert job.state == 'succeeded'


def test_cloudflare_block_pauses_the_queue(env):
    service, sup, monkeypatch = env
    monkeypatch.setenv('FAKE_PAYLOAD', '0')
    monkeypatch.setenv('FAKE_STATUS', '403')
    job = scrape(sup, YY, MACRO_B)
    later = scrape(sup, YY)
    job = drive(sup, job.id, lambda j: j.finished)
    assert 'Cloudflare' in sup.jobs.paused()
    assert any(e['kind'] == 'blocked' for e in sup.jobs.events(job.id))
    for _ in range(5):
        sup.tick()
    assert sup.jobs.get(later.id).state == 'queued'  # paused: nothing starts
    sup.resume()
    monkeypatch.setenv('FAKE_PAYLOAD', '1')
    monkeypatch.setenv('FAKE_STATUS', '200')
    assert drive(sup, later.id, lambda j: j.finished).state == 'succeeded'


def test_restarted_panel_reattaches_or_marks_lost(env):
    service, sup, monkeypatch = env
    monkeypatch.setenv('FAKE_TOTAL', '150')
    monkeypatch.setenv('FAKE_DELAY', '0.4')
    job = scrape(sup, YY)
    drive(sup, job.id, lambda j: j.state == 'running')
    fresh = Supervisor(service)  # the panel restarted: no Popen handle any more
    fresh.reattach()
    assert fresh.jobs.get(job.id).state == 'running'
    job = drive(fresh, job.id, lambda j: j.finished)
    assert job.state == 'succeeded' and job.exit_code == 0

    ghost = scrape(fresh, YY)
    fresh.jobs.update(ghost.id, state='running', wrapper_pid=2 ** 22 + 7,
                      job_dir=os.path.join(service.settings.jobs_dir, ghost.id))
    fresh.reattach()
    assert fresh.jobs.get(ghost.id).state == 'lost'


def test_cancel_before_start_and_run_next(env):
    service, sup, _ = env
    sup.pause('test')
    first, second = scrape(sup, YY), scrape(sup, YY)
    sup.run_next(second.id)
    assert [j.id for j in sup.jobs.queue()] == [second.id, first.id]
    sup.cancel(first.id)
    assert sup.jobs.get(first.id).state == 'cancelled'


def test_plan_preview_estimates_and_yaml(env):
    service, sup, _ = env
    model = service.model()
    options = planning.Options(patience=4, min_new_per_page=5)
    preview = planning.preview(model, ['estado-xx/macro-a', 'estado-xx/macro-a/zona-sul',
                                       'estado-xx/macro-a/serra', 'nope'],
                               options, 'imoveis/aluguel')
    assert [(i.path, i.kind) for i in preview.items] == [
        ('estado-xx/macro-a/centro', 'leaf'),
        ('estado-xx/macro-a/zona-sul/aclimacao', 'leaf'),  # twin reached first here
        ('estado-xx/macro-a/zona-sul/moema', 'dense'),
        ('estado-xx/macro-a/zona-sul', 'remainder'),
        ('estado-xx/macro-a', 'remainder'),
    ]
    by_path = {i.path: i for i in preview.items}
    assert by_path['estado-xx/macro-a/centro'].est_pages == 24          # first pass: 1200/50
    assert by_path['estado-xx/macro-a/zona-sul'].est_pages == 5         # credited before: its last run
    assert by_path['estado-xx/macro-a'].est_pages == 5                  # remainder: patience + 1
    assert any('off-tree' in w for w in preview.warnings)
    assert any('unknown location path' in w for w in preview.warnings)
    full = planning.preview(model, ['estado-xx/macro-a'], planning.Options(full_pass=True),
                            'imoveis/aluguel')
    assert {i.path: i.est_pages for i in full.items}['estado-xx/macro-a/zona-sul'] == 100
    text = planning.plan_yaml(preview)
    assert 'early_stop' in text and 'estado-xx/macro-a/centro?sf=1' in text
    assert 'early_stop' not in planning.plan_yaml(full)


def test_stale_selection_and_run_config_import(env):
    service, sup, _ = env
    model = service.model()
    from datetime import datetime
    now = datetime.now().astimezone()
    # moema is credited only via zona-sul (2 days ago); within 1 day it is stale
    assert 'estado-xx/macro-a/zona-sul/moema' in planning.stale_paths(model, 1, now)
    assert 'estado-xx/macro-a/zona-sul/moema' not in planning.stale_paths(model, 7, now)
    paths, skipped, offsets = planning.run_config_paths(service.run_config(), model.tree)
    assert 'estado-xx/macro-a/zona-sul/moema' in paths and offsets == 1
    assert skipped == ['https://www.olx.com.br/imoveis/aluguel/estado-xx/macro-a/serra?sf=1']


def test_smart_mode_walks_only_never_walked_leaves_to_the_end(env):
    service, sup, _ = env
    model = service.model()
    smart = planning.preview(model, ['estado-xx/macro-a'], planning.Options(mode='smart'),
                             'imoveis/aluguel')
    full = {i.path for i in smart.items if i.full}
    assert full == {'estado-xx/macro-a/centro'}            # the one never-walked leaf
    assert {i.path: i.est_pages for i in smart.items}['estado-xx/macro-a/centro'] == 24
    urls = smart.start_urls()
    assert {'url': 'https://www.olx.com.br/imoveis/aluguel/estado-xx/macro-a/centro?sf=1',
            'early_stop': {}} in urls
    assert 'https://www.olx.com.br/imoveis/aluguel/estado-xx/macro-a/zona-sul?sf=1' in urls
    assert yaml_start_urls(planning.plan_yaml(smart)) == urls
    # the job's generated run_config carries the same mixed entries
    job = sup.enqueue(kinds.SCRAPE, 't', {'early_stop': smart.options.early_stop,
                                          'mode': 'smart', 'full_paths': sorted(full)},
                      [JobUrl(n, i.path, i.url, i.kind, i.est_pages)
                       for n, i in enumerate(smart.items)])
    job_dir = os.path.join(service.settings.jobs_dir, job.id)
    os.makedirs(job_dir)
    kinds.command(sup.jobs.get(job.id), service.settings, job_dir)
    import yaml
    config = yaml.safe_load(open(os.path.join(job_dir, 'run_config.yaml')))
    assert config['catalog_spider']['start_urls'] == urls
    assert config['catalog_spider']['early_stop'] == {'patience': 4, 'min_new_per_page': 5}


def yaml_start_urls(text):
    import yaml
    return yaml.safe_load(text)['catalog_spider']['start_urls']


def test_newer_run_counts_update_inventory_and_status(env):
    service, sup, monkeypatch = env
    monkeypatch.setenv('FAKE_TOTAL', '6000')  # OLX now reports 6,000 for macro-b
    job = scrape(sup, MACRO_B, early_stop={'max_pages': 2})
    drive(sup, job.id, lambda j: j.finished)
    stats = service.model().stats['estado-xx/macro-b']
    assert (stats.inventory, stats.inventory_source) == (6000, 'run')
    assert stats.status == 'split'       # 900 at the probe; over the threshold now,
                                         # so the plan splits it into its probed child
    assert service.model().stats['estado-yy'].inventory_source == 'probe'


def test_yield_and_due(env):
    service, sup, monkeypatch = env
    from datetime import datetime, timedelta
    from panel_env import v2, write_manifest
    root = service.settings.root
    base = 'https://www.olx.com.br/imoveis/aluguel/estado-xx/macro-b/cidade?sf=1'
    now = datetime.now().astimezone()
    for days_ago, new in ((21, 500), (14, 70), (7, 70)):   # backlog, then 10/day
        write_manifest(root, 'estado-xx_macro-b_cidade', now - timedelta(days=days_ago),
                       stop_reason=['early_stop'], pages_scraped=3, new_items=new,
                       **v2(base, invocation=f'cli-{days_ago}'))
    service.refresh()
    model = service.model()
    stats = model.stats['estado-xx/macro-b/cidade']
    assert stats.yield_per_day == pytest.approx(10.0, rel=0.01)
    # fresh since macro-b's complete run 30 h ago (newer than its own last run)
    assert stats.eff_via == 'estado-xx/macro-b'
    days_since = (now - stats.eff_at).total_seconds() / 86400
    assert model.expected_new(stats, now) == pytest.approx(10 * days_since, rel=0.01)
    # cidade isn't a plan leaf (macro-b is, with no yield of its own: one
    # credited run), so "due" falls back to staleness for macro-b
    assert 'estado-xx/macro-b' not in planning.due_paths(model, now, 50, 7)
    later = planning.due_paths(model, now + timedelta(days=30), 50, 7)
    assert 'estado-xx/macro-b' in later and 'estado-xx/macro-b/cidade' not in later


def test_plan_command_writes_a_run_config_or_exits_3(env, tmp_path, capsys):
    import argparse
    import yaml
    from olx_panel.__main__ import EXIT_NOTHING, plan
    service, _, _ = env

    def args(**kw):
        base = dict(paths=[], due=False, stale=None, mode='incremental', patience=None,
                    min_new=None, max_pages=None, output=None)
        return argparse.Namespace(**{**base, **kw})

    out = str(tmp_path / 'due.yaml')
    assert plan(args(paths=['estado-xx/macro-a'], mode='smart', output=out),
                service.settings) == 0
    config = yaml.safe_load(open(out))
    urls = config['catalog_spider']['start_urls']
    assert {'url': 'https://www.olx.com.br/imoveis/aluguel/estado-xx/macro-a/centro?sf=1',
            'early_stop': {}} in urls
    assert config['catalog_spider']['early_stop'] == {'patience': 4, 'min_new_per_page': 5}
    assert '1 full pass' in capsys.readouterr().err
    # moema is credited only via zona-sul two days ago: stale within one day
    assert plan(args(stale=1), service.settings) == 0
    assert 'zona-sul/moema' in capsys.readouterr().out
    assert plan(args(paths=['estado-xx/macro-c']), service.settings) == EXIT_NOTHING


def test_due_and_stale_pick_remainders_as_their_own_url_only(env):
    service, sup, _ = env
    from datetime import datetime, timedelta
    model = service.model()
    later = datetime.now().astimezone() + timedelta(days=30)
    stale = planning.stale_paths(model, 7, later)
    assert '=estado-xx/macro-a/zona-sul' in stale      # a remainder: exact
    assert 'estado-xx/macro-a/zona-sul/moema' in stale  # a dense leaf: plain
    preview = planning.preview(model, ['=estado-xx/macro-a/zona-sul'], planning.Options(),
                               'imoveis/aluguel')
    assert [(i.path, i.kind) for i in preview.items] == [('estado-xx/macro-a/zona-sul', 'remainder')]
    both = planning.preview(model, ['estado-xx/macro-a/zona-sul/moema', '=estado-xx/macro-a/zona-sul'],
                            planning.Options(), 'imoveis/aluguel')
    assert [i.path for i in both.items] == ['estado-xx/macro-a/zona-sul/moema',
                                            'estado-xx/macro-a/zona-sul']   # child first
