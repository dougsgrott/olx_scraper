"""A miniature scraper checkout for control-panel tests: a location tree,
catalog_data rows, manifests in both stores, run_config.yaml and
overrides.yaml, all under one temporary directory.

Tree (threshold 5000):

    estado-xx 9000 split
      macro-a 8000 split
        centro 1200 leaf ── aclimacao 700 (canonical alias twin)
        zona-sul 6000 split
          aclimacao 700 leaf (twin of centro/aclimacao)
          moema 5200 dense
      macro-b 900 leaf ── cidade 300
      macro-c unprobed
    estado-yy 100 leaf

With geo=True a gazetteer (scraped_data/geo/municipios.csv) names XX's
capital "Capital" (DDD 11; centro and zona-sul are its zones), "Cidade"
(DDD 12, macro-b/cidade) and "Serra" (DDD 11, no node of its own), and YY's
"Ypsilon".
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta

from olx_panel.settings import Settings

PROBED = '2026-07-12 10:00:00.000000'

TREE = [
    ('estado-xx', 9000), ('estado-xx/macro-a', 8000),
    ('estado-xx/macro-a/centro', 1200), ('estado-xx/macro-a/centro/aclimacao', 700),
    ('estado-xx/macro-a/zona-sul', 6000), ('estado-xx/macro-a/zona-sul/aclimacao', 700),
    ('estado-xx/macro-a/zona-sul/moema', 5200),
    ('estado-xx/macro-b', 900), ('estado-xx/macro-b/cidade', 300),
    ('estado-xx/macro-c', None), ('estado-yy', 100),
]

LOCATIONS_DDL = '''CREATE TABLE locations (
    path VARCHAR(200) NOT NULL PRIMARY KEY, uf VARCHAR(5), parent_path VARCHAR(200),
    slug VARCHAR(100), depth INTEGER, discovered_at DATETIME, total_of_ads INTEGER,
    probed_at DATETIME)'''


def write_locations(path, tree=TREE):
    con = sqlite3.connect(path)
    con.execute(LOCATIONS_DDL)
    for node_path, count in tree:
        segments = node_path.split('/')
        con.execute('INSERT INTO locations VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (
            node_path, segments[0].split('-')[1].upper(),
            '/'.join(segments[:-1]) or None, segments[-1], len(segments),
            PROBED, count, PROBED if count is not None else None))
    con.commit()
    con.close()


def write_ads(path, ads):
    """ads: list of (uf, ddd, macro, municipality, neighbourhood)."""
    from sqlalchemy import create_engine
    from olx_patchright.db import Base
    Base.metadata.create_all(create_engine('sqlite:///' + path))
    con = sqlite3.connect(path)
    for i, (uf, ddd, macro, municipality, hood) in enumerate(ads):
        con.execute('INSERT INTO catalog_data (uid, uf, ddd, url, municipality, '
                    'neighbourhood, scraped_date) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (str(i), uf, ddd, f'https://{uf.lower()}.olx.com.br/{macro}/imoveis/ad-{i}',
                     municipality, hood, '2026-10-01 12:00:00.000000'))
    con.commit()
    con.close()


def run_id_at(when):
    return when.strftime('%Y%m%dT%H%M%S')


def write_manifest(root, region, when, store='runs', **fields):
    run_id = run_id_at(when)
    manifest = {'run_id': run_id, 'region': region, 'dt': when.strftime('%Y-%m-%d'),
                'total_seen': 0, 'new_items': 0, 'duplicate_items': 0,
                'pages_scraped': 1, 'stop_reason': ['exhausted'], 'duration_s': 60.0}
    manifest.update(fields)
    directory = os.path.join(root, store, 'manifests', 'spider=catalog',
                             f'dt={manifest["dt"]}', f'region={region}')
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f'manifest_{run_id}.json')
    with open(path, 'w') as f:
        json.dump(manifest, f)
    return path


def v2(start_url, invocation='cli-test', start_page=1, listing_url=None, **fields):
    from urllib.parse import urlparse
    return dict(schema_version=2, start_url=start_url, scope='imoveis/aluguel',
                start_page=start_page, invocation_id=invocation,
                listing_url=listing_url or urlparse(start_url).path,
                breadcrumb=fields.pop('breadcrumb', None), **fields)


GAZETTEER = [
    ('1000001', 'Capital', 'XX', '11', '1'),
    ('1000002', 'Cidade', 'XX', '12', '0'),
    ('1000003', 'Serra', 'XX', '11', '0'),
    ('2000001', 'Ypsilon', 'YY', '22', '1'),
]


def write_geo(root):
    geo_dir = os.path.join(root, 'scraped_data', 'geo')
    os.makedirs(geo_dir, exist_ok=True)
    with open(os.path.join(geo_dir, 'municipios.csv'), 'w', encoding='utf-8') as f:
        f.write('code,name,uf,ddd,capital,lat,lon\n')
        for code, name, uf, ddd, capital in GAZETTEER:
            f.write(f'{code},{name},{uf},{ddd},{capital},0,0\n')
    with open(os.path.join(geo_dir, 'br-municipios.topo.json'), 'w') as f:
        json.dump({'type': 'Topology', 'arcs': [], 'objects': {'municipios': {
            'type': 'GeometryCollection', 'geometries': []}}}, f)


def build(root, now=None, geo=False):
    """Create the whole environment; returns (Settings, paths of interest)."""
    now = now or datetime.now().astimezone()
    os.makedirs(os.path.join(root, 'scraped_data'), exist_ok=True)
    write_locations(os.path.join(root, 'scraped_data', 'locations.sqlite'))
    write_ads(os.path.join(root, 'scraped_data', 'olx.sqlite'),
              [('XX', '11', 'macro-a', 'Capital', 'Centro')] * 7 +
              [('XX', '12', 'macro-b', 'Cidade', '')] * 3 +
              [('YY', '22', 'yy-macro', 'Ypsilon', 'Bairro')] * 2)
    if geo:
        write_geo(root)
    base = 'https://www.olx.com.br/imoveis/aluguel/'
    paths = {}
    # caught up on zona-sul 2 days ago: certifies its whole subtree
    paths['zona_sul'] = write_manifest(
        root, 'estado-xx_macro-a_zona-sul', now - timedelta(days=2),
        stop_reason=['early_stop'], pages_scraped=5, new_items=2)
    # capped moema run yesterday: no credit
    write_manifest(root, 'estado-xx_macro-a_zona-sul_moema', now - timedelta(days=1),
                   stop_reason=['early_stop'], pages_scraped=104, new_items=1)
    # complete pass on the canonical aclimacao twin, archived
    paths['aclimacao'] = write_manifest(
        root, 'estado-xx_macro-a_centro_aclimacao', now - timedelta(days=10),
        store='runs_uploaded', stop_reason=['exhausted'], new_items=2)
    # v2: complete macro-b, then a resume and a scope mismatch
    write_manifest(root, 'estado-xx_macro-b', now - timedelta(hours=30),
                   **v2(base + 'estado-xx/macro-b?sf=1', new_items=2, breadcrumb=[
                       {'level': 'region', 'value': '11', 'label': 'DDD 11 - Macro B',
                        'url': '/imoveis/aluguel/estado-xx/macro-b'}]))
    write_manifest(root, 'estado-xx_macro-b', now - timedelta(hours=20),
                   **v2(base + 'estado-xx/macro-b?sf=1&o=3', start_page=3))
    write_manifest(root, 'estado-xx_macro-b_cidade', now - timedelta(hours=19),
                   **v2(base + 'estado-xx/macro-b/cidade?sf=1',
                        listing_url='/imoveis/aluguel/estado-xx/macro-b', new_items=1))
    # off-tree legacy region: counts under macro-a, never credited
    write_manifest(root, 'estado-xx_macro-a_serra', now - timedelta(days=3),
                   stop_reason=['exhausted'], new_items=3)
    # legacy crash (no stop reason) and an overridden resume
    write_manifest(root, 'estado-yy', now - timedelta(days=4), stop_reason=[], new_items=1)
    paths['flagged'] = write_manifest(root, 'estado-yy', now - timedelta(days=5),
                                      stop_reason=['exhausted'], new_items=0)
    overrides = os.path.join(root, 'overrides.yaml')
    with open(overrides, 'w') as f:
        f.write('legacy_runs:\n'
                f'  - region: estado-yy\n    run_id: "{run_id_at(now - timedelta(days=5))}"\n'
                '    outcome: resume\n    note: started at o=7\n'
                'legacy_regions: {}\n')
    run_config = os.path.join(root, 'run_config.yaml')
    with open(run_config, 'w') as f:
        f.write('mode: CATALOG\ncatalog_spider:\n  start_urls:\n'
                f'    - "{base}estado-xx/macro-a/centro/aclimacao?sf=1"\n'
                f'    - "{base}estado-xx/macro-a/zona-sul/aclimacao?sf=1"\n'
                f'    - "{base}estado-xx/macro-a/zona-sul/moema?sf=1&o=92"\n'
                f'    - "{base}estado-xx/macro-a/serra?sf=1"\n'
                '  early_stop: {patience: 4, min_new_per_page: 5}\n')
    settings = Settings(
        root=root,
        olx_db=os.path.join(root, 'scraped_data', 'olx.sqlite'),
        locations_db=os.path.join(root, 'scraped_data', 'locations.sqlite'),
        panel_db=os.path.join(root, 'scraped_data', 'panel.sqlite'),
        panel_lock=os.path.join(root, 'scraped_data', 'panel.lock'),
        run_config=run_config, overrides=overrides,
        geo_topology=os.path.join(root, 'scraped_data', 'geo', 'br-municipios.topo.json'),
        geo_csv=os.path.join(root, 'scraped_data', 'geo', 'municipios.csv'),
        runs_dirs=(('outbox', os.path.join(root, 'runs')),
                   ('archive', os.path.join(root, 'runs_uploaded'))),
    )
    return settings, paths
