CREATE TABLE job (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    state_reason TEXT,
    title TEXT,
    params TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    seq INTEGER NOT NULL,
    created_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    not_before TEXT,
    wrapper_pid INTEGER,
    exit_code INTEGER,
    signal INTEGER,
    cancel_requested_at TEXT,
    term_sent_at TEXT,
    kill_sent_at TEXT,
    job_dir TEXT
);

CREATE INDEX ix_job_state ON job (state, priority, seq);

CREATE TABLE job_url (
    job_id TEXT NOT NULL,
    ord INTEGER NOT NULL,
    path TEXT NOT NULL,
    url TEXT NOT NULL,
    plan_kind TEXT,
    est_pages INTEGER,
    PRIMARY KEY (job_id, ord)
);

CREATE TABLE job_event (
    id INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT
);

CREATE INDEX ix_job_event_job ON job_event (job_id, id);
