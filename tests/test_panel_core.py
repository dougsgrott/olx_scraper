import json
import os
import shutil
from datetime import datetime, timedelta

import pytest

from panel_env import build, write_ads
from olx_panel.core import manifests as mf
from olx_panel.core.adgeo import load_ad_agg, refresh_ad_agg
from olx_panel.core.manifests import index_manifests, load_runs
from olx_panel.core.service import PanelService
from olx_panel.core.tree import title_from_slug

NOW = datetime.now().astimezone()


@pytest.fixture
def env(tmp_path):
    settings, paths = build(str(tmp_path), NOW)
    service = PanelService(settings)
    service.refresh()
    return service, paths


def test_index_is_incremental_and_follows_moves(env):
    service, paths = env
    store = service.store
    assert len(load_runs(store)) == 9
    assert not index_manifests(store, service.settings.runs_dirs).changed

    # pipeline.py upload moves the file to the archive: same key, new path
    src = paths['zona_sul']
    dst = src.replace('/runs/', '/runs_uploaded/')
    os.makedirs(os.path.dirname(dst))
    shutil.move(src, dst)
    result = index_manifests(store, service.settings.runs_dirs)
    assert (result.added, result.moved, result.missing) == (0, 1, 0)
    [run] = [r for r in load_runs(store) if r.region == 'estado-xx_macro-a_zona-sul']
    assert (run.store, run.file_path, run.file_missing) == ('archive', dst, False)

    # a vanished file keeps its history, flagged missing
    os.remove(dst)
    assert index_manifests(store, service.settings.runs_dirs).missing == 1
    [run] = [r for r in load_runs(store) if r.region == 'estado-xx_macro-a_zona-sul']
    assert run.file_missing


def test_unreadable_manifest_is_reported_and_retried(env):
    service, _ = env
    bad_dir = os.path.join(service.settings.root, 'runs', 'manifests', 'spider=catalog',
                           'dt=2026-10-01', 'region=estado-yy')
    os.makedirs(bad_dir)
    bad = os.path.join(bad_dir, 'manifest_20261001T120000.json')
    with open(bad, 'w') as f:
        f.write('{"run_id": ')
    result = index_manifests(service.store, service.settings.runs_dirs)
    assert [p for p, _ in result.unreadable] == [bad]
    with open(bad, 'w') as f:
        json.dump({'run_id': '20261001T120000', 'region': 'estado-yy',
                   'stop_reason': ['exhausted'], 'new_items': 0}, f)
    result = index_manifests(service.store, service.settings.runs_dirs)
    assert result.added == 1 and not result.unreadable


def test_outcomes(env):
    service, _ = env
    model = service.model()
    by_key = {(r.region, r.start_page or 1, r.outcome) for r in model.runs}
    assert by_key == {
        ('estado-xx_macro-a_zona-sul', 1, 'caught_up'),
        ('estado-xx_macro-a_zona-sul_moema', 1, 'capped'),     # 104 pages
        ('estado-xx_macro-a_centro_aclimacao', 1, 'complete'),
        ('estado-xx_macro-b', 1, 'complete'),
        ('estado-xx_macro-b', 3, 'resume'),                     # v2 start_page
        ('estado-xx_macro-b_cidade', 1, 'scope_mismatch'),      # listing_url differs
        ('estado-xx_macro-a_serra', 1, 'scope_mismatch'),       # off-tree
        ('estado-yy', 1, 'unknown'),                            # stop_reason []
        ('estado-yy', 1, 'resume'),                             # overrides.yaml
    }
    flagged = [r for r in model.runs if r.note]
    assert [r.note for r in flagged] == ['started at o=7']


@pytest.mark.parametrize('reason, pages, start_page, outcome', [
    (['exhausted'], 3, 1, 'complete'),
    (['early_stop'], 100, 1, 'caught_up'),
    (['early_stop'], 101, 1, 'capped'),
    (['page_cap'], 100, 1, 'capped'),
    (['max_pages'], 10, 1, 'partial'),
    (['no_payload'], 0, 1, 'blocked'),
    (['nav_timeout'], 2, 1, 'failed'),
    (['crashed'], 0, 1, 'failed'),
    (['cancelled'], 2, 1, 'cancelled'),
    ([], 0, 1, 'unknown'),
    (None, None, 1, 'unknown'),
    (['exhausted'], 3, 92, 'resume'),
])
def test_classify(reason, pages, start_page, outcome):
    run = mf.Run(region='r', run_id='x', dt=None, started_at=NOW, finished_at=NOW,
                 duration_s=1, pages_scraped=pages, total_seen=0, new_items=0,
                 duplicate_items=0, stop_reason=reason, schema_version=2,
                 start_url=None, scope=None, start_page=start_page,
                 invocation_id=None, early_stop=None, total_of_ads=None,
                 listing_url=None, sort=None, breadcrumb=None, new_per_page=None,
                 store='outbox', file_path='', file_missing=False)
    assert mf.classify(run, page_cap=100) == outcome


def test_collected_located_and_legacy_children(env):
    model = env[0].model()
    s = model.stats
    assert model.total_collected() == model.ad_count == 12
    assert (s['estado-xx/macro-a/zona-sul'].collected,
            s['estado-xx/macro-a/zona-sul'].collected_subtree) == (2, 3)
    assert s['estado-xx/macro-a'].collected_subtree == 8  # incl. legacy serra
    assert s['estado-xx'].collected_subtree == 11
    serra = s['estado-xx/macro-a/serra']
    assert serra.legacy and serra.parent_path == 'estado-xx/macro-a'
    assert 'estado-xx/macro-a/serra' in s['estado-xx/macro-a'].children
    assert serra.eff_at is None
    assert (s['estado-xx'].located, s['estado-xx/macro-a'].located,
            s['estado-xx/macro-b'].located, s['estado-yy'].located) == (10, 7, 3, 2)
    assert s['estado-xx/macro-a/centro'].located is None


def test_freshness_propagates_down_and_across_alias_twins(env):
    s = env[0].model().stats
    zona_sul = s['estado-xx/macro-a/zona-sul'].eff_at
    assert (s['estado-xx/macro-a/zona-sul/moema'].eff_at,
            s['estado-xx/macro-a/zona-sul/moema'].eff_via) == \
        (zona_sul, 'estado-xx/macro-a/zona-sul')       # its own capped run earns nothing
    twin = s['estado-xx/macro-a/zona-sul/aclimacao']
    assert twin.alias_of == 'estado-xx/macro-a/centro/aclimacao'
    assert twin.complete_at == s['estado-xx/macro-a/centro/aclimacao'].complete_at
    assert twin.eff_at == zona_sul                      # newer than the twin's pass
    assert s['estado-xx/macro-b/cidade'].eff_via == 'estado-xx/macro-b'
    assert s['estado-yy'].eff_at is None                # unknown + resume only
    assert s['estado-xx'].eff_at is None


def test_shares_weigh_plan_leaves(env):
    model = env[0].model()
    week = model.shares(7, NOW)
    assert week['estado-xx/macro-a/zona-sul'].fresh == 1
    # macro-a: centro 1200 (stale) + zona-sul 6000 (fresh) + own remainder 800
    assert week['estado-xx/macro-a'].weight == 8000
    assert week['estado-xx/macro-a'].fresh == pytest.approx(6000 / 8000)
    assert week['estado-xx/macro-a'].baseline == pytest.approx(1400 / 8000)
    month = model.shares(30, NOW)
    # centro was never run whole; its aclimacao child (700) is fresh within 30 days
    assert month['estado-xx/macro-a/centro'].fresh == pytest.approx(700 / 1200)
    assert model.brazil_share(7, NOW).fresh == pytest.approx(6900 / 9100)
    assert week['estado-xx/macro-c'].fresh is None      # unprobed: no weight


def test_invocations_names_and_run_config(env):
    service, _ = env
    model = service.model()
    invocations = model.invocations()
    sources = sorted(i.source for i in invocations)
    assert sources == ['cli'] + ['legacy'] * 6
    [cli] = [i for i in invocations if i.source == 'cli']
    assert len(cli.runs) == 3
    assert model.stats['estado-xx/macro-b'].name == 'DDD 11 - Macro B'  # breadcrumb
    assert model.stats['estado-xx'].name == 'Estado Xx'
    rc = service.run_config()
    assert [p for _, p in rc.offsets] == [92]
    assert rc.off_tree == ['https://www.olx.com.br/imoveis/aluguel/estado-xx/macro-a/serra?sf=1']
    assert rc.alias_pairs == [('estado-xx/macro-a/zona-sul/aclimacao',
                               'estado-xx/macro-a/centro/aclimacao')]


def test_status_reconciles_and_flags_stranded_bronze(env):
    service, _ = env
    status = service.status()
    assert status['reconciliation'] == 0 and status['browser'] is None
    tmp_dir = os.path.join(service.settings.root, 'runs', 'spider=catalog',
                           'dt=2026-10-01', 'region=estado-yy')
    os.makedirs(tmp_dir)
    open(os.path.join(tmp_dir, '20261001T120000.jsonl.gz.tmp'), 'w').close()
    service.refresh()
    assert len(service.status()['stranded']) == 1


def test_ad_aggregate_is_incremental_and_rebuilds_on_drift(tmp_path):
    from olx_panel.core.store import Store
    db = str(tmp_path / 'olx.sqlite')
    write_ads(db, [('XX', '11', 'macro-a', 'A', 'Centro')] * 3)
    store = Store(str(tmp_path / 'panel.sqlite'))
    assert refresh_ad_agg(store, db)
    assert not refresh_ad_agg(store, db)
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("INSERT INTO catalog_data (uid, uf, ddd, url, municipality, neighbourhood) "
                "VALUES ('x', 'XX', '11', 'https://xx.olx.com.br/macro-b/imoveis/x', 'B', '')")
    con.commit()
    assert refresh_ad_agg(store, db)
    rows = {(r['macro'], r['n']) for r in load_ad_agg(store)}
    assert rows == {('macro-a', 3), ('macro-b', 1)}
    con.execute("DELETE FROM catalog_data WHERE uid = '0'")
    con.commit()
    con.close()
    assert refresh_ad_agg(store, db)
    assert {(r['macro'], r['n']) for r in load_ad_agg(store)} == {('macro-a', 2), ('macro-b', 1)}


def test_title_from_slug():
    assert title_from_slug('alto-de-pinheiros') == 'Alto de Pinheiros'
    assert title_from_slug('sao-paulo-e-regiao') == 'Sao Paulo e Regiao'
    assert title_from_slug('e-regiao') == 'E Regiao'
