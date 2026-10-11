import json
import sqlite3

import pytest

from olx_patchright import db
from olx_patchright.export import (
    EncounterExporter, infer_region, infer_scope, parse_breadcrumb, parse_sort,
    start_page_of,
)


def test_session_factory_runs_wal_and_readers_never_block_the_writer(workdir):
    factory = db.session_factory()
    db.insert_record(factory, {'uid': '1', 'title': 'a'})

    reader = sqlite3.connect(f'file:{db.DB_PATH}?mode=ro', uri=True)
    assert reader.execute('pragma journal_mode').fetchone() == ('wal',)
    reader.execute('begin')
    assert reader.execute('select count(*) from catalog_data').fetchone() == (1,)
    # A long read transaction is open; the scraper's commit still succeeds
    # at once (journal_mode=delete would block it until the busy timeout).
    db.insert_record(factory, {'uid': '2', 'title': 'b'})
    assert reader.execute('select count(*) from catalog_data').fetchone() == (1,)
    reader.execute('commit')
    assert reader.execute('select count(*) from catalog_data').fetchone() == (2,)

    with factory() as session:
        timeout = session.connection().exec_driver_sql('pragma busy_timeout').scalar()
    assert timeout == db.BUSY_TIMEOUT_S * 1000


def test_location_db_runs_wal_too(workdir, monkeypatch):
    monkeypatch.setattr(db, 'LOCATIONS_DB_PATH', str(workdir / 'locations.sqlite'))
    factory = db.location_session_factory()
    with factory() as session:
        mode = session.connection().exec_driver_sql('pragma journal_mode').scalar()
    assert mode == 'wal'


def test_url_helpers():
    url = 'https://www.olx.com.br/imoveis/aluguel/estado-es/vitoria/republica?sf=1&o=92'
    assert infer_region(url) == 'estado-es_vitoria_republica'
    assert infer_scope(url) == 'imoveis/aluguel'
    assert start_page_of(url) == 92
    assert start_page_of('https://www.olx.com.br/imoveis/aluguel/estado-es?sf=1') == 1
    assert start_page_of('https://www.olx.com.br/imoveis/aluguel/estado-es?o=x') == 1


def uploadable(workdir):
    """What `pipeline.py upload` would ship right now (it skips .tmp)."""
    return [p for p in (workdir / 'runs').rglob('*')
            if p.is_file() and p.suffix != '.tmp']


def test_partition_is_invisible_to_upload_until_closed(workdir):
    exporter = EncounterExporter('estado-es', start_url='https://x/imoveis/aluguel/estado-es')
    exporter.write_new({'uid': '1'})
    exporter.count_page(1)
    assert uploadable(workdir) == []
    exporter.record_stop('exhausted')
    manifest = exporter.close()
    names = sorted(p.name for p in uploadable(workdir))
    assert names == [f"{manifest['run_id']}.jsonl.gz", f"manifest_{manifest['run_id']}.json"]
    assert not [p for p in (workdir / 'runs').rglob('*.tmp')]


def test_all_duplicate_run_keeps_only_its_manifest(workdir):
    exporter = EncounterExporter('estado-es')
    exporter.count_duplicate()
    exporter.count_page(0)
    exporter.record_stop('early_stop')
    manifest = exporter.close()
    [only] = uploadable(workdir)
    assert json.loads(only.read_text()) == manifest
    assert (manifest['new_items'], manifest['duplicate_items']) == (0, 1)
    assert manifest['start_url'] is None and manifest['scope'] is None


def test_same_region_twice_in_one_second_gets_distinct_run_ids(workdir):
    first = EncounterExporter('estado-es')
    first.record_stop('no_payload')
    one = first.close()
    second = EncounterExporter('estado-es')
    second.record_stop('exhausted')
    two = second.close()
    assert one['run_id'] != two['run_id']
    manifests = sorted((workdir / 'runs' / 'manifests').rglob('manifest_*.json'))
    assert len(manifests) == 2


JULY = {'locations': [
    {'level': 'region', 'value': '27', 'label': 'DDD 27 - Norte do Espírito Santo',
     'url': '/imoveis/aluguel/estado-es/norte-do-espirito-santo', 'extra': 1}]}


def test_breadcrumb_july_layout():
    entries, raw, notes = parse_breadcrumb(JULY)
    assert entries == [{'level': 'region', 'value': '27',
                        'label': 'DDD 27 - Norte do Espírito Santo',
                        'url': '/imoveis/aluguel/estado-es/norte-do-espirito-santo'}]
    assert raw is None and notes == []


@pytest.mark.parametrize('field', [
    [{'name': 'Vitória', 'href': 'https://www.olx.com.br/imoveis/aluguel/estado-es/x/vitoria'}],
    {'items': [{'text': 'Vitória', 'link': '/imoveis/aluguel/estado-es/x/vitoria', 'id': 9}]},
])
def test_breadcrumb_renamed_keys_are_read(field):
    entries, raw, notes = parse_breadcrumb({'breadcrumbs': field})
    assert entries[0]['label'] == 'Vitória' and entries[0]['url'].endswith('/vitoria')
    assert raw is None and notes == []


def test_breadcrumb_unreadable_shape_keeps_a_raw_sample():
    entries, raw, notes = parse_breadcrumb({'breadcrumbs': ['Brasil', 'ES']})
    assert entries == [] and raw == '["Brasil", "ES"]'
    assert 'no recognisable label/url' in notes[0]
    assert parse_breadcrumb({})[2] == [
        'no location breadcrumb (neither `locations` nor `breadcrumbs`)']


def test_sort_is_read_by_any_known_key():
    assert parse_sort({'filters': {'sort': 'date'}}) == ('date', [])
    assert parse_sort({'filters': {'sf': '1'}}) == ('1', [])
    assert parse_sort({'filters': {'state': '14'}}) == (None, ['no sort in `filters` (keys: state)'])
