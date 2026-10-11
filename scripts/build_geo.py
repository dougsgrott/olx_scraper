"""Build the control panel's map assets from IBGE + a municipality/DDD table.

Two steps, both writing under scraped_data/geo/ (gitignored, rebuildable):

  fetch   download the three sources into scraped_data/geo/sources/ and
          record their URLs, sha256 and fetch time in SOURCES.json
          (network; IBGE and GitHub serve plain HTTP, no browser needed):
            - IBGE malhas v4: every municipality as ONE TopoJSON topology
              (qualidade=minima, ~1.5 MB; geometries carry only `codarea`)
            - IBGE localidades v1: names and UF per municipality
            - kelvins/municipios-brasileiros (MIT), pinned to a commit:
              DDD, seat lat/lon and the capital flag per municipality
  build   offline: rewrite the topology's properties to the compact
          {c: code, u: UF, d: DDD, a: "UF-DDD" area, n: name} and write
          br-municipios.topo.json + municipios.csv for the panel.

DDD areas and state outlines are not stored: the browser derives them from
the single topology (topojson merge/mesh on `a` and `u`), so the borders line
up exactly and no GIS library is needed.

    uv run python scripts/build_geo.py            # fetch + build
    uv run python scripts/build_geo.py build      # rebuild from sources/
"""
import argparse
import csv
import gzip
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEO_DIR = os.path.join(ROOT, 'scraped_data', 'geo')
SOURCES_DIR = os.path.join(GEO_DIR, 'sources')

TOPOLOGY_URL = ('https://servicodados.ibge.gov.br/api/v4/malhas/paises/BR'
                '?intrarregiao=municipio&formato=application/json&qualidade=minima')
NAMES_URL = ('https://servicodados.ibge.gov.br/api/v1/localidades/municipios'
             '?view=nivelado')
KELVINS_REPO = 'kelvins/municipios-brasileiros'
KELVINS_COMMIT_URL = f'https://api.github.com/repos/{KELVINS_REPO}/commits/main'
KELVINS_CSV_URL = ('https://raw.githubusercontent.com/' + KELVINS_REPO +
                   '/{sha}/csv/municipios.csv')

TOPOLOGY_FILE = 'ibge_municipios.topo.json'
NAMES_FILE = 'ibge_localidades.json'
KELVINS_FILE = 'kelvins_municipios.csv'
OUT_TOPOLOGY = 'br-municipios.topo.json'
OUT_CSV = 'municipios.csv'


def _get(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'olx-panel-geo/1'})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    # IBGE gzips its responses even when the request doesn't ask for it.
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    return data


def _save(name, data):
    os.makedirs(SOURCES_DIR, exist_ok=True)
    path = os.path.join(SOURCES_DIR, name)
    with open(path, 'wb') as f:
        f.write(data)
    return hashlib.sha256(data).hexdigest()


def fetch(kelvins_sha=None):
    if not kelvins_sha:
        kelvins_sha = json.loads(_get(KELVINS_COMMIT_URL))['sha']
    sources = {}
    for name, url in ((TOPOLOGY_FILE, TOPOLOGY_URL), (NAMES_FILE, NAMES_URL),
                      (KELVINS_FILE, KELVINS_CSV_URL.format(sha=kelvins_sha))):
        print(f'Fetching {url}', flush=True)
        sources[name] = {'url': url, 'sha256': _save(name, _get(url))}
    manifest = {
        'fetched_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'kelvins_commit': kelvins_sha,
        'sources': sources,
    }
    with open(os.path.join(SOURCES_DIR, 'SOURCES.json'), 'w') as f:
        json.dump(manifest, f, indent=2)
    print(f'Sources saved to {SOURCES_DIR}/ (kelvins @ {kelvins_sha[:8]}).')


def build(sources_dir=SOURCES_DIR, out_dir=GEO_DIR):
    """Join the three sources on the IBGE code; returns the problems found
    (codes missing from one source), which are also printed."""
    with open(os.path.join(sources_dir, TOPOLOGY_FILE), encoding='utf-8') as f:
        topology = json.load(f)
    with open(os.path.join(sources_dir, NAMES_FILE), encoding='utf-8') as f:
        names = {str(m['municipio-id']): (m['municipio-nome'], m['UF-sigla'])
                 for m in json.load(f)}
    with open(os.path.join(sources_dir, KELVINS_FILE), encoding='utf-8') as f:
        kelvins = {r['codigo_ibge']: r for r in csv.DictReader(f)}

    [(object_name, collection)] = topology['objects'].items()
    geometries = collection['geometries']
    problems = []
    rows = []
    for geometry in geometries:
        code = str(geometry['properties']['codarea'])
        name, uf = names.get(code, (None, None))
        extra = kelvins.get(code)
        if name is None:
            problems.append(f'{code}: in the mesh but not in IBGE localidades')
        if extra is None:
            problems.append(f'{code} {name}: no DDD in the kelvins table')
        ddd = extra['ddd'] if extra else ''
        geometry['properties'] = {'c': code, 'u': uf or '', 'd': ddd,
                                  'a': f'{uf}-{ddd}' if uf and ddd else '',
                                  'n': name or code}
        rows.append({'code': code, 'name': name or '', 'uf': uf or '', 'ddd': ddd,
                     'capital': extra['capital'] if extra else '0',
                     'lat': extra['latitude'] if extra else '',
                     'lon': extra['longitude'] if extra else ''})
    in_mesh = {r['code'] for r in rows}
    problems += [f'{code} {names[code][0]}: in IBGE localidades but not in the mesh'
                 for code in sorted(set(names) - in_mesh)]
    topology['objects'] = {'municipios': collection}

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, OUT_TOPOLOGY), 'w', encoding='utf-8') as f:
        json.dump(topology, f, ensure_ascii=False, separators=(',', ':'))
    with open(os.path.join(out_dir, OUT_CSV), 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: r['code']))
    print(f'Built {len(rows)} municipalities ({object_name} -> municipios) into {out_dir}/.')
    for problem in problems:
        print(f'  WARN {problem}')
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('step', nargs='?', choices=('fetch', 'build', 'all'),
                        default='all')
    parser.add_argument('--kelvins-sha', help='pin a kelvins commit '
                        '(default: the current main)')
    args = parser.parse_args()
    if args.step in ('fetch', 'all'):
        fetch(args.kelvins_sha)
    if args.step in ('build', 'all'):
        if not os.path.exists(os.path.join(SOURCES_DIR, TOPOLOGY_FILE)):
            sys.exit('No sources yet: run `scripts/build_geo.py fetch` first.')
        build()


if __name__ == '__main__':
    main()
