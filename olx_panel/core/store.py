"""panel.sqlite: the panel's own, disposable state (manifest index, ad
aggregate, watermarks). Deleting the file loses nothing — it is rebuilt from
the scraper's files on the next start.

Migrations are numbered SQL files applied in order, tracked by
PRAGMA user_version; schema.sql is migration 1.
"""
import os
import sqlite3
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
MIGRATIONS = ['schema.sql']


class Store:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with self.connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            self._migrate(conn)

    @contextmanager
    def connect(self):
        """A connection whose block commits on success, rolls back on error."""
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _migrate(self, conn):
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        for number, name in enumerate(MIGRATIONS, 1):
            if number <= version:
                continue
            with open(os.path.join(HERE, name), encoding='utf-8') as f:
                conn.executescript(f.read())
            conn.execute(f'PRAGMA user_version = {number}')

    def get_meta(self, conn, key, default=None):
        row = conn.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, conn, key, value):
        conn.execute('INSERT INTO meta (key, value) VALUES (?, ?) '
                     'ON CONFLICT (key) DO UPDATE SET value = excluded.value',
                     (key, None if value is None else str(value)))
