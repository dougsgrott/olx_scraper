import json
import signal
import sqlite3

import pytest

from fakes import FakeSite, FakeSites, fake_sync_playwright

ES = '/imoveis/aluguel/estado-es'
VIX = '/imoveis/aluguel/estado-es/norte-do-espirito-santo/vitoria'


def url(path, query='sf=1'):
    return f'https://www.olx.com.br{path}' + (f'?{query}' if query else '')


def manifests_on_disk(workdir):
    return sorted((json.loads(p.read_text()) for p in
                   (workdir / 'runs' / 'manifests').rglob('manifest_*.json')),
                  key=lambda m: m['run_id'] + m['region'])


def all_files(workdir):
    return sorted(p.relative_to(workdir).as_posix()
                  for p in (workdir / 'runs').rglob('*') if p.is_file())


def scrape(catalog, monkeypatch, site, urls, early_stop=None):
    monkeypatch.setattr(catalog, 'sync_playwright', fake_sync_playwright(site))
    return catalog.scrape_catalog(urls, early_stop=early_stop)


def test_exhausted_scope_writes_a_v2_manifest(workdir, fast_catalog, monkeypatch):
    monkeypatch.setenv('OLX_INVOCATION_ID', 'job-1')
    [m] = scrape(fast_catalog, monkeypatch, FakeSite(total=120), [url(ES)])

    assert m['stop_reason'] == ['exhausted']
    assert (m['pages_scraped'], m['new_items'], m['duplicate_items']) == (3, 120, 0)
    assert m['new_per_page'] == [50, 50, 20]
    assert m['region'] == 'estado-es'
    assert m['schema_version'] == 2
    assert m['start_url'] == url(ES)
    assert (m['scope'], m['start_page']) == ('imoveis/aluguel', 1)
    assert m['invocation_id'] == 'job-1'
    assert m['early_stop'] == {'patience': 0, 'min_new_per_page': 1, 'max_pages': 0}
    assert (m['total_of_ads'], m['listing_url'], m['sort']) == (120, ES + '?sf=1', 'date')
    assert m['breadcrumb'][2] == {
        'level': 'region', 'value': '27', 'label': 'DDD 27 - Norte do Espírito Santo',
        'url': '/imoveis/aluguel/estado-es/norte-do-espirito-santo'}
    assert m['started_at'][-6] in '+-' and m['finished_at'] >= m['started_at']
    assert manifests_on_disk(workdir) == [m]

    files = all_files(workdir)
    assert not [f for f in files if f.endswith('.tmp')]
    assert f"runs/spider=catalog/dt={m['dt']}/region=estado-es/{m['run_id']}.jsonl.gz" in files

    con = sqlite3.connect(workdir / 'scraped_data' / 'olx.sqlite')
    assert con.execute('select count(*) from catalog_data').fetchone() == (120,)
    assert con.execute('pragma journal_mode').fetchone() == ('wal',)


def test_rerun_counts_duplicates_and_drops_empty_bronze(workdir, fast_catalog, monkeypatch):
    site = FakeSite(total=60)
    scrape(fast_catalog, monkeypatch, site, [url(ES)])
    [m] = scrape(fast_catalog, monkeypatch, site, [url(ES)],
                 early_stop={'patience': 1, 'min_new_per_page': 1})
    assert m['stop_reason'] == ['early_stop']
    assert (m['new_items'], m['duplicate_items'], m['pages_scraped']) == (0, 50, 1)
    gz = [f for f in all_files(workdir) if f.startswith('runs/spider=catalog')]
    assert len(gz) == 1  # only the first run's file


def test_dense_scope_stops_at_page_cap_even_without_early_stop(
        workdir, fast_catalog, monkeypatch):
    # Without the cap, patience 0 + max_pages 0 would request ?o=N+1 forever.
    monkeypatch.setattr(fast_catalog, 'PAGE_CAP', 3)
    site = FakeSite(total=6000, page_cap=3)
    [m] = scrape(fast_catalog, monkeypatch, site, [url(ES)])
    assert m['stop_reason'] == ['page_cap']
    assert m['pages_scraped'] == 3
    assert len(site.requests) == 3


def test_clamped_page_is_detected_by_page_index(workdir, fast_catalog, monkeypatch):
    site = FakeSite(total=6000, page_cap=2)
    [m] = scrape(fast_catalog, monkeypatch, site, [url(ES)])
    assert m['stop_reason'] == ['page_cap']
    assert (m['pages_scraped'], m['new_items'], m['duplicate_items']) == (2, 100, 0)


def test_clamped_page_is_detected_by_repeated_ads(workdir, fast_catalog, monkeypatch):
    site = FakeSite(total=6000, page_cap=2, index_lies=True)
    [m] = scrape(fast_catalog, monkeypatch, site, [url(ES)])
    assert m['stop_reason'] == ['page_cap']
    assert (m['pages_scraped'], m['duplicate_items']) == (2, 0)


def test_resumed_start_url_records_its_start_page(workdir, fast_catalog, monkeypatch):
    [m] = scrape(fast_catalog, monkeypatch, FakeSite(total=200), [url(ES, 'sf=1&o=3')])
    assert (m['start_page'], m['pages_scraped'], m['stop_reason']) == (3, 2, ['exhausted'])


def test_no_payload_and_crash_end_only_their_start_url(workdir, fast_catalog, monkeypatch):
    def crash_on_page_2(page_no, _url):
        if page_no == 2:
            raise RuntimeError('target closed')

    sites = FakeSites({
        ES: FakeSite(total=50, payload=False),
        VIX: FakeSite(total=120, uid_base=2000000, on_goto=crash_on_page_2),
        ES + '/sul-do-espirito-santo': FakeSite(total=10, uid_base=3000000),
    })
    blocked, crashed, fine = scrape(
        fast_catalog, monkeypatch, sites,
        [url(ES), url(VIX), url(ES + '/sul-do-espirito-santo')])
    assert (blocked['stop_reason'], blocked['pages_scraped']) == (['no_payload'], 0)
    assert (workdir / 'logs' / 'no_ads_200.html').exists()
    assert (crashed['stop_reason'], crashed['pages_scraped'], crashed['new_items']) == \
        (['crashed'], 1, 50)
    assert fine['stop_reason'] == ['exhausted']


def test_stop_request_finishes_the_page_and_skips_later_urls(
        workdir, fast_catalog, monkeypatch):
    def stop_during_page_2(page_no, _url):
        if page_no == 2:
            fast_catalog._request_stop(signal.SIGINT, None)

    sites = FakeSites({
        ES: FakeSite(total=200, on_goto=stop_during_page_2),
        VIX: FakeSite(total=10, uid_base=2000000),
    })
    handler_before = signal.getsignal(signal.SIGINT)
    manifests = scrape(fast_catalog, monkeypatch, sites, [url(ES), url(VIX)])
    assert [m['region'] for m in manifests] == ['estado-es']
    [m] = manifests
    assert (m['stop_reason'], m['pages_scraped'], m['new_items']) == (['cancelled'], 2, 100)
    assert sites.by_path[VIX].requests == []
    assert manifests_on_disk(workdir) == manifests
    assert signal.getsignal(signal.SIGINT) is handler_before


def test_second_stop_request_interrupts_at_once_but_keeps_the_manifest(
        workdir, fast_catalog, monkeypatch):
    def double_signal(page_no, _url):
        if page_no == 2:
            fast_catalog._request_stop(signal.SIGINT, None)
            fast_catalog._request_stop(signal.SIGINT, None)

    with pytest.raises(KeyboardInterrupt):
        scrape(fast_catalog, monkeypatch, FakeSite(total=200, on_goto=double_signal),
               [url(ES)])
    [m] = manifests_on_disk(workdir)
    assert (m['stop_reason'], m['pages_scraped'], m['new_items']) == (['cancelled'], 1, 50)
    assert not [f for f in all_files(workdir) if f.endswith('.tmp')]


def test_october_page_data_and_a_note_for_what_is_missing(workdir, fast_catalog,
                                                          monkeypatch, capsys):
    site = FakeSite(total=120, page_data='2026-10')
    [m] = scrape(fast_catalog, monkeypatch, site, [url(ES)])
    assert m['breadcrumb'][2]['label'] == 'DDD 27 - Norte do Espírito Santo'
    assert m['breadcrumb'][2]['url'].endswith('/estado-es/norte-do-espirito-santo')
    assert m['breadcrumb_raw'] is None
    assert m['location_text'] == 'Vitória, ES'
    assert m['sort'] is None
    out = capsys.readouterr().out
    assert out.count('Note: page data') == 1           # once per run, not per page
    assert 'no sort in `filters` (keys: pageIndex, state)' in out


def test_unreadable_breadcrumb_is_kept_raw_in_the_manifest(workdir, fast_catalog,
                                                           monkeypatch, capsys):
    [m] = scrape(fast_catalog, monkeypatch, FakeSite(total=10, page_data='opaque'), [url(ES)])
    assert m['breadcrumb'] == [] and m['breadcrumb_raw'] == '["Brasil", "ES", "DDD 27"]'
    assert 'sample: ["Brasil", "ES", "DDD 27"]' in capsys.readouterr().out


def test_a_start_url_can_carry_its_own_early_stop(workdir, fast_catalog, monkeypatch):
    sites = FakeSites({ES: FakeSite(total=200), VIX: FakeSite(total=200, uid_base=2000000)})
    monkeypatch.setattr(fast_catalog, 'sync_playwright', fake_sync_playwright(sites))
    # seed both scopes so a re-run sees only duplicates
    fast_catalog.scrape_catalog([url(ES), url(VIX)])
    stop = {'patience': 1, 'min_new_per_page': 1}
    es, vix = fast_catalog.scrape_catalog([url(ES), {'url': url(VIX), 'early_stop': {}}],
                                          early_stop=stop)
    assert (es['stop_reason'], es['pages_scraped']) == (['early_stop'], 1)
    assert es['early_stop'] == {'patience': 1, 'min_new_per_page': 1, 'max_pages': 0}
    assert (vix['stop_reason'], vix['pages_scraped']) == (['exhausted'], 4)  # walked to the end
    assert vix['early_stop'] == {'patience': 0, 'min_new_per_page': 1, 'max_pages': 0}
