"""The Supervisor: one thread per panel (panel.lock guarantees one panel)
that runs queued jobs one at a time, watches them, cancels them and records
how they ended.

Each tick (1 s):
  - every running job: reap it (exit.json, written by wrap.py), escalate an
    overdue cancel, and scan new output for a Cloudflare block — a 403
    cancels the job and pauses the queue until a re-warm;
  - if nothing runs and the queue isn't paused: start the next job, unless
    another process (CLI, scheduled run) holds the browser lock — then the
    job waits and the banner names the holder.

Jobs are started in their own session, so restarting the panel never kills
them; on start the Supervisor reattaches to running jobs by pid (checking
/proc/<pid>/cmdline names the job, against pid reuse) or marks them lost.
"""
import json
import logging
import os
import signal
import subprocess
import sys
import threading
from datetime import timedelta

from olx_patchright.browser import lock_holder
from olx_patchright.plan import region_of

from . import kinds
from .model import (CANCELLED, CANCELLING, FAILED, LOST, PARTIAL, RUNNING,
                    SUCCEEDED, WAITING, JobStore, now)
from .progress import parse

log = logging.getLogger(__name__)
# Started by file path (stdlib only), so it runs whatever the job's cwd is.
WRAPPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wrap.py')
EXIT_BUSY = 75
EXIT_CANCELLED = 130


class Supervisor(threading.Thread):
    def __init__(self, service):
        super().__init__(name='panel-supervisor', daemon=True)
        self.service = service
        self.settings = service.settings
        self.jobs = JobStore(service.store)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._procs = {}     # job id -> Popen, for jobs this process started
        self._offsets = {}   # job id -> output.log bytes already scanned
        self.last_error = None

    # -- thread ----------------------------------------------------------------

    def run(self):
        self.reattach()
        while not self._stop_event.wait(self.settings.supervisor_tick_s):
            self.tick()

    def stop(self):
        self._stop_event.set()

    def tick(self):
        try:
            with self._lock:
                for job in self.jobs.active():
                    self._watch(job)
                if not self.jobs.active() and not self.jobs.paused():
                    self._start_next()
            self.last_error = None
        except Exception as exc:  # keep supervising; the banner shows it
            log.exception('supervisor tick failed')
            self.last_error = repr(exc)

    # -- queue operations (also called from web requests) ---------------------

    def enqueue(self, kind, title, params, urls=(), front=False):
        with self._lock:
            priority = 10 if front else 0
            return self.jobs.create(kind, title, params, urls, priority)

    def cancel(self, job_id):
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job.pending:
                self.jobs.update(job_id, state=CANCELLED, finished_at=now(),
                                 state_reason='cancelled before it started')
                self.jobs.event(job_id, 'cancelled')
            elif job.state == RUNNING:
                self.jobs.update(job_id, state=CANCELLING, cancel_requested_at=now())
                self.jobs.event(job_id, 'cancel', 'SIGINT: finish the current page')
                self._signal(job, signal.SIGINT)
            return self.jobs.get(job_id)

    def force(self, job_id):
        """Kill now (the whole process group). Bronze rows of the page in
        flight may be lost; a browser cleanup job is queued first."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job and job.active:
                self._kill(job)
            return self.jobs.get(job_id)

    def done(self, job_id):
        """WARM: the challenge is solved — save the profile and close."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job and job.kind == kinds.WARM and job.state == RUNNING:
                self.jobs.event(job_id, 'done', 'SIGINT: save the profile')
                self._signal(job, signal.SIGINT)
            return job

    def run_next(self, job_id):
        with self._lock:
            top = max((j.priority for j in self.jobs.queue()), default=0)
            self.jobs.update(job_id, priority=top + 1)

    def pause(self, reason):
        self.jobs.set_paused(reason)

    def resume(self):
        self.jobs.set_paused(None)

    # -- starting ------------------------------------------------------------

    def _start_next(self):
        current = now()
        for job in self.jobs.queue():
            if job.not_before and job.not_before > current:
                continue
            holder = lock_holder()
            if holder:
                reason = (f"browser busy: {holder.get('label') or 'another process'} "
                          f"(pid {holder.get('pid')})")
                if job.state != WAITING or job.state_reason != reason:
                    self.jobs.update(job.id, state=WAITING, state_reason=reason)
                return
            self._launch(job)
            return

    def _launch(self, job):
        job = self.jobs.get(job.id)  # with its URLs
        job_dir = os.path.join(self.settings.jobs_dir, job.id)
        os.makedirs(job_dir, exist_ok=True)
        for stale in ('exit.json', 'wrapper.json'):  # from a busy (75) attempt
            try:
                os.remove(os.path.join(job_dir, stale))
            except FileNotFoundError:
                pass
        argv = kinds.command(job, self.settings, job_dir)
        env = dict(os.environ, OLX_INVOCATION_ID=job.id, PYTHONUNBUFFERED='1')
        with open(os.path.join(job_dir, 'params.json'), 'w') as f:
            json.dump({'kind': job.kind, 'params': job.params, 'argv': argv}, f, indent=2)
        with open(os.path.join(job_dir, 'output.log'), 'ab') as out:
            proc = subprocess.Popen(
                [sys.executable, '-u', WRAPPER, '--job-dir', job_dir, '--', *argv],
                cwd=self.settings.root, env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        self._procs[job.id] = proc
        self.jobs.update(job.id, state=RUNNING, state_reason=None, started_at=now(),
                         finished_at=None, wrapper_pid=proc.pid, job_dir=job_dir,
                         not_before=None)
        self.jobs.event(job.id, 'started', ' '.join(argv))

    # -- watching ------------------------------------------------------------

    def reattach(self):
        """After a panel restart: keep watching live jobs, finalize ended
        ones, mark vanished ones lost."""
        with self._lock:
            for job in self.jobs.active():
                if self._exit_info(job) is None and not self._alive(job):
                    self._finalize(job, None)

    def _watch(self, job):
        ended, info = self._ended(job)
        if job.kind == kinds.SCRAPE:
            # before reaping too: a blocked run can end within one tick
            self._scan_for_blocks(job, still_running=not ended)
        if ended:
            self._finalize(job, info)
        elif job.state == CANCELLING:
            self._escalate(job)

    def _ended(self, job):
        proc = self._procs.get(job.id)
        if proc is not None:
            code = proc.poll()
            if code is None:
                return False, None
            info = self._exit_info(job)
            if info is None:  # wrapper itself died (SIGKILL to the group)
                info = {'exit_code': code if code >= 0 else None,
                        'signal': -code if code < 0 else None}
            return True, info
        info = self._exit_info(job)
        if info is not None:
            return True, info
        return (False, None) if self._alive(job) else (True, None)

    def _exit_info(self, job):
        path = os.path.join(job.job_dir or '', 'exit.json')
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, ValueError):
            return None

    def _alive(self, job):
        """The wrapper still runs (and the pid wasn't reused by another
        process: its command line names this job's directory)."""
        if not job.wrapper_pid:
            return False
        try:
            with open(f'/proc/{job.wrapper_pid}/cmdline', 'rb') as f:
                return job.id.encode() in f.read()
        except OSError:
            return False

    def _scan_for_blocks(self, job, still_running):
        path = os.path.join(job.job_dir, 'output.log')
        offset = self._offsets.get(job.id, 0)
        try:
            with open(path, 'rb') as f:
                f.seek(offset)
                chunk = f.read()
        except FileNotFoundError:
            return
        complete = chunk[:chunk.rfind(b'\n') + 1]  # whole lines only
        self._offsets[job.id] = offset + len(complete)
        blocks = [b for b in parse(complete.decode('utf-8', 'replace')).blocks
                  if b[1] == 403]
        if blocks:
            url = blocks[0][0]
            reason = f'Cloudflare block (HTTP 403) at {url}: re-warm the profile'
            self.jobs.event(job.id, 'blocked', reason)
            self.pause(reason)
            if still_running and self.jobs.get(job.id).state == RUNNING:
                self.cancel(job.id)

    def _escalate(self, job):
        current = now()
        grace = timedelta(seconds=self.settings.cancel_grace_s)
        if job.cancel_requested_at and not job.term_sent_at and \
                current - job.cancel_requested_at > grace:
            self.jobs.update(job.id, term_sent_at=current)
            self.jobs.event(job.id, 'cancel', 'SIGTERM to the job group')
            self._signal_group(job, signal.SIGTERM)
        elif job.term_sent_at and not job.kill_sent_at and \
                current - job.term_sent_at > timedelta(seconds=self.settings.term_grace_s):
            self._kill(job)

    def _kill(self, job):
        self.jobs.update(job.id, state=CANCELLING, kill_sent_at=now(),
                         cancel_requested_at=job.cancel_requested_at or now())
        self.jobs.event(job.id, 'killed', 'SIGKILL to the job group')
        self._signal_group(job, signal.SIGKILL)

    def _signal(self, job, sig):
        try:
            os.kill(job.wrapper_pid, sig)
        except (ProcessLookupError, TypeError):
            pass

    def _signal_group(self, job, sig):
        try:
            os.killpg(job.wrapper_pid, sig)  # the wrapper leads its session
        except (ProcessLookupError, TypeError, PermissionError):
            pass

    # -- finishing -----------------------------------------------------------

    def _finalize(self, job, info):
        self._procs.pop(job.id, None)
        self._offsets.pop(job.id, None)
        job = self.jobs.get(job.id)
        self.service.refresh()  # index this job's manifests right away
        code = info.get('exit_code') if info else None
        sig = info.get('signal') if info else None
        fields = {'finished_at': now(), 'exit_code': code, 'signal': sig}
        if info is None:
            state, reason = LOST, 'the job process disappeared without an exit record'
        elif job.kill_sent_at:
            state, reason = CANCELLED, 'killed; bronze may miss rows of the page in flight'
            self.enqueue(kinds.CLEANUP, 'Free the browser profile after a kill', {},
                         front=True)
        elif code == EXIT_BUSY:
            self.jobs.update(job.id, state=WAITING, wrapper_pid=None, started_at=None,
                             state_reason='browser busy (exit 75): retrying',
                             not_before=now() + timedelta(seconds=self.settings.busy_backoff_s))
            self.jobs.event(job.id, 'busy', 'another browser job held the lock')
            return
        elif job.cancel_requested_at or code == EXIT_CANCELLED or sig in (2, 15):
            state, reason = CANCELLED, None
        elif code == 0:
            state, reason = self._success_state(job)
        else:
            state, reason = FAILED, f'exit {code}' if code is not None else f'signal {sig}'
        fields.update(state=state, state_reason=reason)
        self.jobs.update(job.id, **fields)
        self.jobs.event(job.id, state, reason)

    def reconcile(self, job):
        """Re-derive succeeded/partial of a finished scrape from its
        manifests, which may since be classified differently (a rule fix,
        an overrides.yaml entry). Returns the up-to-date job."""
        if job.kind != kinds.SCRAPE or job.state not in (SUCCEEDED, PARTIAL) \
                or job.exit_code != 0:
            return job
        state, reason = self._success_state(job)
        if (state, reason) != (job.state, job.state_reason):
            with self._lock:
                self.jobs.update(job.id, state=state, state_reason=reason)
                self.jobs.event(job.id, 'reclassified', f'{job.state} -> {state}')
            job = self.jobs.get(job.id)
        return job

    def _success_state(self, job):
        if job.kind != kinds.SCRAPE:
            return SUCCEEDED, None
        outcomes = self.url_outcomes(job)
        missing = sum(1 for o in outcomes if o['run'] is None)
        uncredited = sum(1 for o in outcomes if o['run'] and not o['run'].credited)
        if missing or uncredited:
            return PARTIAL, f'{uncredited} URL(s) not credited, {missing} without a manifest'
        return SUCCEEDED, None

    def url_outcomes(self, job):
        """Each start URL of a scrape job with the manifest it wrote."""
        model = self.service.model()
        runs = {r.region: r for r in model.runs if r.invocation == job.id}
        return [{'url': u, 'run': runs.get(region_of(u.path))} for u in job.urls]

    # -- status for the banner ------------------------------------------------

    def status(self):
        running = self.jobs.active()
        return {
            'running': running[0] if running else None,
            'queued': len(self.jobs.queue()),
            'paused': self.jobs.paused(),
            'error': self.last_error,
        }

