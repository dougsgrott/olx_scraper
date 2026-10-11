"""Job rows in panel.sqlite. A job is one wrapped subprocess; scrape jobs
also list their start URLs (job_url) so outcomes can be matched to the
manifests they write (invocation_id = job id)."""
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime

QUEUED = 'queued'
WAITING = 'waiting'        # browser busy, or backing off after exit 75
RUNNING = 'running'
CANCELLING = 'cancelling'
SUCCEEDED = 'succeeded'
PARTIAL = 'partial'        # ran, but some URLs earned no freshness credit
FAILED = 'failed'
CANCELLED = 'cancelled'
LOST = 'lost'              # process gone without an exit record
PENDING = (QUEUED, WAITING)
ACTIVE = (RUNNING, CANCELLING)
FINISHED = (SUCCEEDED, PARTIAL, FAILED, CANCELLED, LOST)


def now():
    return datetime.now().astimezone()


def iso(ts):
    return ts.isoformat(timespec='seconds') if ts else None


def parse_ts(value):
    return datetime.fromisoformat(value) if value else None


def new_job_id():
    """Also the scrape's OLX_INVOCATION_ID, so its manifests name it."""
    return f'j{now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}'


@dataclass
class JobUrl:
    ord: int
    path: str
    url: str
    plan_kind: str
    est_pages: int


@dataclass
class Job:
    id: str
    kind: str
    state: str
    state_reason: str
    title: str
    params: dict
    priority: int
    seq: int
    created_at: datetime
    started_at: datetime
    finished_at: datetime
    not_before: datetime
    wrapper_pid: int
    exit_code: int
    signal: int
    cancel_requested_at: datetime
    term_sent_at: datetime
    kill_sent_at: datetime
    job_dir: str
    urls: list = field(default_factory=list)

    @property
    def active(self):
        return self.state in ACTIVE

    @property
    def pending(self):
        return self.state in PENDING

    @property
    def finished(self):
        return self.state in FINISHED

    @property
    def duration_s(self):
        if not self.started_at:
            return None
        return ((self.finished_at or now()) - self.started_at).total_seconds()


_TS = ('created_at', 'started_at', 'finished_at', 'not_before',
       'cancel_requested_at', 'term_sent_at', 'kill_sent_at')


def _job(row):
    data = dict(row)
    data['params'] = json.loads(data['params'] or '{}')
    for key in _TS:
        data[key] = parse_ts(data[key])
    return Job(**data)


class JobStore:
    def __init__(self, store):
        self.store = store

    def create(self, kind, title, params, urls=(), priority=0):
        job_id = new_job_id()
        with self.store.connect() as conn:
            seq = conn.execute('SELECT coalesce(max(seq), 0) + 1 FROM job').fetchone()[0]
            conn.execute(
                'INSERT INTO job (id, kind, state, title, params, priority, seq, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (job_id, kind, QUEUED, title, json.dumps(params), priority, seq, iso(now())))
            conn.executemany(
                'INSERT INTO job_url (job_id, ord, path, url, plan_kind, est_pages) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                [(job_id, i, u.path, u.url, u.plan_kind, u.est_pages)
                 for i, u in enumerate(urls)])
        self.event(job_id, 'queued', title)
        return self.get(job_id)

    def get(self, job_id):
        with self.store.connect() as conn:
            row = conn.execute('SELECT * FROM job WHERE id = ?', (job_id,)).fetchone()
            if row is None:
                return None
            job = _job(row)
            job.urls = [JobUrl(r['ord'], r['path'], r['url'], r['plan_kind'], r['est_pages'])
                        for r in conn.execute('SELECT * FROM job_url WHERE job_id = ? '
                                              'ORDER BY ord', (job_id,))]
        return job

    def list(self, states=None, limit=200):
        sql = 'SELECT * FROM job'
        args = []
        if states:
            sql += f' WHERE state IN ({",".join("?" for _ in states)})'
            args = list(states)
        sql += ' ORDER BY seq DESC LIMIT ?'
        with self.store.connect() as conn:
            return [_job(r) for r in conn.execute(sql, args + [limit])]

    def queue(self):
        """Pending jobs in the order they will run."""
        with self.store.connect() as conn:
            rows = conn.execute(
                f'SELECT * FROM job WHERE state IN ({",".join("?" for _ in PENDING)}) '
                'ORDER BY priority DESC, seq', PENDING).fetchall()
        return [_job(r) for r in rows]

    def active(self):
        with self.store.connect() as conn:
            rows = conn.execute(
                f'SELECT * FROM job WHERE state IN ({",".join("?" for _ in ACTIVE)}) '
                'ORDER BY seq', ACTIVE).fetchall()
        return [_job(r) for r in rows]

    def update(self, job_id, **fields):
        for key in _TS:
            if key in fields and isinstance(fields[key], datetime):
                fields[key] = iso(fields[key])
        assignments = ', '.join(f'{k} = ?' for k in fields)
        with self.store.connect() as conn:
            conn.execute(f'UPDATE job SET {assignments} WHERE id = ?',
                         list(fields.values()) + [job_id])

    def event(self, job_id, kind, message=None):
        with self.store.connect() as conn:
            conn.execute('INSERT INTO job_event (job_id, ts, kind, message) VALUES (?, ?, ?, ?)',
                         (job_id, iso(now()), kind, message))

    def events(self, job_id):
        with self.store.connect() as conn:
            return [dict(r) for r in conn.execute(
                'SELECT * FROM job_event WHERE job_id = ? ORDER BY id', (job_id,))]

    def paused(self):
        with self.store.connect() as conn:
            return self.store.get_meta(conn, 'queue_paused') or None

    def set_paused(self, reason):
        with self.store.connect() as conn:
            self.store.set_meta(conn, 'queue_paused', reason or '')
