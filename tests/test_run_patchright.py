import json
import os
import signal
import subprocess
import sys
import time

import pytest
import yaml

import run_patchright
from fakes import FakeSite, fake_sync_playwright
from olx_patchright import browser

HERE = os.path.dirname(os.path.abspath(__file__))
ES = 'https://www.olx.com.br/imoveis/aluguel/estado-es?sf=1'
VIX = 'https://www.olx.com.br/imoveis/aluguel/estado-es/norte-do-espirito-santo/vitoria?sf=1'


def write_config(path, config):
    path.write_text(yaml.safe_dump(config))
    return str(path)


@pytest.mark.parametrize('config, message', [
    (None, 'not found'),
    ('just a string', 'not a YAML mapping'),
    ({'mode': 'SCRAPE'}, 'invalid mode'),
    ({'mode': 'CATALOG', 'catalog_spider': {'start_urls': []}}, 'start_urls'),
    ({'mode': 'CATALOG', 'catalog_spider': {'start_urls': [ES], 'early_stop': 3}},
     'must be a mapping'),
    ({'mode': 'WARM'}, 'WARM mode needs'),
])
def test_config_errors_exit_2(tmp_path, capsys, config, message):
    path = tmp_path / 'run_config.yaml'
    if config is not None:
        write_config(path, config)
    assert run_patchright.main(['--config', str(path)]) == run_patchright.EXIT_CONFIG == 2
    assert message in capsys.readouterr().out


def test_catalog_run_exits_0(workdir, fast_catalog, monkeypatch):
    monkeypatch.setattr(fast_catalog, 'sync_playwright',
                        fake_sync_playwright(FakeSite(total=10)))
    cfg = write_config(workdir / 'job.yaml', {
        'mode': 'CATALOG', 'catalog_spider': {'start_urls': [ES]}})
    assert run_patchright.main(['--config', cfg]) == 0
    assert browser.lock_holder()['label'] == f'CATALOG, 1 URL(s) ({cfg})'


def test_busy_browser_exits_75(workdir, monkeypatch):
    holder = subprocess.Popen(
        [sys.executable, '-c',
         'import sys, time; sys.path.insert(0, sys.argv[1]); '
         'from olx_patchright import browser; browser.LOCK_PATH = sys.argv[2]; '
         'browser.acquire_browser_lock(); print("ready", flush=True); time.sleep(60)',
         os.path.dirname(HERE), browser.LOCK_PATH],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == 'ready'
        cfg = write_config(workdir / 'job.yaml', {
            'mode': 'CATALOG', 'catalog_spider': {'start_urls': [ES]}})
        with pytest.raises(SystemExit) as exc:
            run_patchright.main(['--config', cfg])
        assert exc.value.code == 75
    finally:
        holder.kill()
        holder.wait()


def test_sigint_cancels_cleanly_with_exit_130(tmp_path):
    """A real SIGINT mid-scrape: the current page completes, its manifest says
    'cancelled' with real page counts, the second URL never starts."""
    cfg = write_config(tmp_path / 'job.yaml', {
        'mode': 'CATALOG', 'catalog_spider': {'start_urls': [ES, VIX]}})
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, 'fake_run.py'), str(tmp_path), cfg,
         '1000', '0.3'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stderr.readline().startswith('page-served 1')
        assert proc.stderr.readline().startswith('page-served 2')
        proc.send_signal(signal.SIGINT)
        out, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 130, err
    manifests = [json.loads(p.read_text())
                 for p in (tmp_path / 'runs' / 'manifests').rglob('manifest_*.json')]
    assert [m['region'] for m in manifests] == ['estado-es']
    [m] = manifests
    assert m['stop_reason'] == ['cancelled']
    assert m['pages_scraped'] == 2 and m['new_items'] == 100
    assert 'stopping after the current page' in err


def test_second_sigint_stops_at_once(tmp_path):
    cfg = write_config(tmp_path / 'job.yaml', {
        'mode': 'CATALOG', 'catalog_spider': {'start_urls': [ES]}})
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, 'fake_run.py'), str(tmp_path), cfg,
         '1000', '2'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stderr.readline().startswith('page-served 1')
        proc.send_signal(signal.SIGINT)
        # Separate deliveries: two SIGINTs landing before Python runs its
        # handler would coalesce into one.
        time.sleep(0.3)
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 130, err
    [m] = [json.loads(p.read_text())
           for p in (tmp_path / 'runs' / 'manifests').rglob('manifest_*.json')]
    assert (m['stop_reason'], m['pages_scraped']) == (['cancelled'], 0)
