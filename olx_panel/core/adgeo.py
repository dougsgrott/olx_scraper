"""Where collected ads are, by their own location: an incremental aggregate
of olx.sqlite :: catalog_data by (uf, ddd, macro-region, municipality,
neighbourhood), kept in panel.sqlite.

catalog_data is append-only (one insert per first-seen uid), so an `id`
watermark makes each refresh read only new rows; reads are bounded by the
max id seen at the start, so rows a running scrape inserts meanwhile are
picked up next time rather than counted twice. If the row count ever stops
matching the watermark (rows deleted, DB restored) the aggregate is rebuilt.
The macro-region is the first path segment of the ad URL
(https://{uf}.olx.com.br/{macro}/imoveis/…), which names a depth-2 node.
"""
import os

from .sources import read_only

_GROUP_SQL = '''
WITH t AS (
    SELECT uf, ddd, municipality, neighbourhood, scraped_date,
           CASE WHEN instr(url, '.com.br/') > 0
                THEN substr(url, instr(url, '.com.br/') + 8) ELSE '' END AS rest
    FROM catalog_data WHERE id > ? AND id <= ?
)
SELECT coalesce(uf, ''), coalesce(ddd, ''),
       CASE WHEN instr(rest, '/') > 0 THEN substr(rest, 1, instr(rest, '/') - 1)
            ELSE '' END,
       coalesce(municipality, ''), coalesce(neighbourhood, ''),
       count(*), min(scraped_date), max(scraped_date)
FROM t GROUP BY 1, 2, 3, 4, 5
'''

_UPSERT_SQL = '''
INSERT INTO ad_geo_agg (uf, ddd, macro, municipality, neighbourhood, n,
                        first_seen, last_seen)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (uf, ddd, macro, municipality, neighbourhood) DO UPDATE SET
    n = n + excluded.n,
    first_seen = min(first_seen, excluded.first_seen),
    last_seen = max(last_seen, excluded.last_seen)
'''


def refresh_ad_agg(store, olx_db):
    """Fold new catalog_data rows into ad_geo_agg; True if anything changed."""
    if not os.path.exists(olx_db):
        return False
    with store.connect() as conn:
        watermark = int(store.get_meta(conn, 'ad_watermark', 0))
        counted = int(store.get_meta(conn, 'ad_count', 0))
    with read_only(olx_db) as ro:
        max_id, total = ro.execute(
            'SELECT max(id), count(*) FROM catalog_data').fetchone()
        max_id = max_id or 0
        if max_id == watermark and total == counted:
            return False
        fresh = ro.execute('SELECT count(*) FROM catalog_data WHERE id > ? AND id <= ?',
                           (watermark, max_id)).fetchone()[0]
        incremental = 0 < watermark <= max_id and counted + fresh == total
        since = watermark if incremental else 0
        groups = ro.execute(_GROUP_SQL, (since, max_id)).fetchall()
    with store.connect() as conn:
        if not incremental:
            conn.execute('DELETE FROM ad_geo_agg')
        conn.executemany(_UPSERT_SQL, [tuple(g) for g in groups])
        store.set_meta(conn, 'ad_watermark', max_id)
        store.set_meta(conn, 'ad_count', total)
        store.set_meta(conn, 'ad_version',
                       int(store.get_meta(conn, 'ad_version', 0)) + 1)
    return True


def load_ad_agg(store):
    with store.connect() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM ad_geo_agg')]
