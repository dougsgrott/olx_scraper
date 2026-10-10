"""Read-only access to the scraper's SQLite files.

Connections are opened per refresh and closed at once: a held connection or
a long read on olx.sqlite must never stand in the way of a running scrape's
per-record commits (WAL makes reads non-blocking; short-lived connections
keep it that way even before a file has been switched to WAL).
"""
import os
import sqlite3
from contextlib import contextmanager


@contextmanager
def read_only(path):
    """A query-only connection to an existing SQLite file."""
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def file_version(path):
    """A token that changes whenever the file (or its WAL) changes."""
    parts = []
    for p in (path, path + '-wal'):
        try:
            st = os.stat(p)
            parts.append((st.st_mtime_ns, st.st_size))
        except FileNotFoundError:
            parts.append(None)
    return tuple(parts)
