"""Jobs page: the panel's own queue (scrape, warm, probe, browser cleanup),
each job's live log and per-URL outcomes, and the actions on them."""
import asyncio
import os

from fastapi import APIRouter, Form, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from olx_patchright.plan import region_of, start_url
from olx_panel.core import planning
from olx_panel.core.jobs import kinds
from olx_panel.core.jobs.model import FINISHED, PENDING, JobUrl
from olx_panel.core.jobs.progress import parse
from olx_panel.core.manifests import CAPPED

from .common import render, service, supervisor
from .pages import Page

router = APIRouter()
PAGE = Page('jobs', 'Jobs', router, nav_order=15)

LOG_TAIL_BYTES = 256 * 1024


def _job_or_404(request, job_id):
    job = supervisor(request).jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f'unknown job: {job_id}')
    return job


def _redirect(url):
    return Response(status_code=200, headers={'HX-Redirect': url})


def _log_text(job, limit=LOG_TAIL_BYTES):
    if not job.job_dir:
        return ''
    try:
        with open(os.path.join(job.job_dir, 'output.log'), 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read().decode('utf-8', 'replace')
    except FileNotFoundError:
        return ''


def _view(request, job):
    """A job plus what its log and manifests say about it."""
    sup = supervisor(request)
    model = service(request).model()
    progress = parse(_log_text(job)) if job.kind in (kinds.SCRAPE, kinds.PROBE) else None
    outcomes = sup.url_outcomes(job) if job.kind == kinds.SCRAPE else []
    remaining_s = None
    if job.kind == kinds.SCRAPE and job.state in ('running', 'cancelling') and progress:
        started = set(progress.regions)
        pending = sum(u.est_pages or 1 for u in job.urls
                      if region_of(u.path) not in started)
        remaining_s = pending * planning.seconds_per_page(model.runs)
    return {'job': job, 'progress': progress, 'outcomes': outcomes,
            'remaining_s': remaining_s, 'label': kinds.LABELS.get(job.kind, job.kind),
            'retryable': [o for o in outcomes if _retryable(o)],
            'events': sup.jobs.events(job.id)}


def _retryable(outcome):
    run = outcome['run']
    return run is None or (not run.credited and run.outcome != CAPPED)


@router.get('/jobs')
def jobs_page(request: Request):
    return render(request, 'jobs.html', active='jobs', **_lists(request),
                  version=service(request).status()['version'])


def _lists(request):
    sup = supervisor(request)
    return {'queue': sup.jobs.queue(), 'running': sup.jobs.active(),
            'history': sup.jobs.list(states=FINISHED, limit=60),
            'paused': sup.jobs.paused(), 'labels': kinds.LABELS}


@router.get('/jobs/table')
def jobs_table(request: Request):
    return render(request, 'partials/jobs_table.html', **_lists(request))


@router.get('/jobs/{job_id}')
def job_page(request: Request, job_id: str):
    job = _job_or_404(request, job_id)
    return render(request, 'job_detail.html', active='jobs', **_view(request, job),
                  version=service(request).status()['version'])


@router.get('/jobs/{job_id}/card')
def job_card(request: Request, job_id: str):
    return render(request, 'partials/job_card.html',
                  **_view(request, _job_or_404(request, job_id)))


@router.get('/jobs/{job_id}/log/stream')
async def job_log(request: Request, job_id: str,
                  last_event_id: str = Header(default='')):
    """The job's output.log as server-sent events: one event per line, the
    byte offset as its id (an EventSource reconnects from there); `end` once
    the job has finished and the log is read to the end."""
    _job_or_404(request, job_id)
    sup = supervisor(request)

    async def events():
        offset = int(last_event_id) if last_event_id.isdigit() else 0
        skipped = offset > 0
        while not await request.is_disconnected():
            current = sup.jobs.get(job_id)
            # a queued job has no directory yet: look again every round
            path = os.path.join(current.job_dir, 'output.log') \
                if current and current.job_dir else None
            chunk = b''
            if path and os.path.exists(path):
                if not skipped and os.path.getsize(path) > LOG_TAIL_BYTES:
                    offset = os.path.getsize(path) - LOG_TAIL_BYTES
                    yield f'id: {offset}\ndata: … (earlier output skipped)\n\n'
                skipped = True
                with open(path, 'rb') as f:
                    f.seek(offset)
                    chunk = f.read()
            complete = chunk[:chunk.rfind(b'\n') + 1]
            for raw in complete.splitlines(keepends=True):
                offset += len(raw)
                line = raw.decode('utf-8', 'replace').rstrip('\r\n').replace('\r', ' ')
                yield f'id: {offset}\ndata: {line}\n\n'
            if not complete:
                if current is None or current.finished:
                    yield f'event: end\ndata: {current.state if current else "gone"}\n\n'
                    return
                await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache'})


# -- creating jobs ------------------------------------------------------------

def _options(patience, min_new_per_page, max_pages, full_pass):
    return planning.Options(patience=max(0, patience), min_new_per_page=max(1, min_new_per_page),
                            max_pages=max(0, max_pages), full_pass=bool(full_pass))


def create_scrape(request, paths, options, title=None):
    svc = service(request)
    preview = planning.preview(svc.model(), paths, options, svc.settings.scope)
    if not preview.items:
        raise HTTPException(400, 'nothing to scrape: ' + '; '.join(preview.warnings))
    names = ', '.join(i.name for i in preview.items[:3])
    more = f' +{len(preview.items) - 3}' if len(preview.items) > 3 else ''
    urls = [JobUrl(i, item.path, item.url, item.kind, item.est_pages)
            for i, item in enumerate(preview.items)]
    return supervisor(request).enqueue(
        kinds.SCRAPE, title or f'{len(urls)} URL(s): {names}{more}',
        {'paths': preview.paths, 'early_stop': options.early_stop,
         'full_pass': options.full_pass}, urls)


@router.post('/jobs/scrape')
def enqueue_scrape(request: Request, path: list[str] = Form(default=[]),
                   patience: int = Form(4), min_new_per_page: int = Form(5),
                   max_pages: int = Form(0), full_pass: str = Form('')):
    job = create_scrape(request, path, _options(patience, min_new_per_page, max_pages, full_pass))
    return _redirect(f'/jobs/{job.id}')


@router.post('/jobs/warm')
def enqueue_warm(request: Request, url: str = Form('')):
    svc = service(request)
    if not url:
        urls = svc.run_config().urls
        url = urls[0] if urls else start_url('estado-sp', svc.settings.scope)
    if not url.startswith('https://www.olx.com.br/'):
        raise HTTPException(400, 'warm URL must be an https://www.olx.com.br/ page')
    job = supervisor(request).enqueue(kinds.WARM, 'Warm the browser profile',
                                      {'url': url}, front=True)
    return _redirect(f'/jobs/{job.id}')


@router.post('/jobs/probe')
def enqueue_probe(request: Request, path: str = Form(...), refresh: str = Form('')):
    svc = service(request)
    if path not in svc.model().tree:
        raise HTTPException(400, f'unknown location path: {path}')
    job = supervisor(request).enqueue(
        kinds.PROBE, f'Probe {path}{" (refresh)" if refresh else ""}',
        {'paths': [path], 'refresh': bool(refresh), 'scope': svc.settings.scope})
    return _redirect(f'/jobs/{job.id}')


# -- acting on jobs -----------------------------------------------------------

def _card(request, job_id):
    return render(request, 'partials/job_card.html',
                  **_view(request, _job_or_404(request, job_id)))


@router.post('/jobs/{job_id}/cancel')
def cancel_job(request: Request, job_id: str):
    _job_or_404(request, job_id)
    supervisor(request).cancel(job_id)
    return _card(request, job_id)


@router.post('/jobs/{job_id}/force')
def force_job(request: Request, job_id: str):
    _job_or_404(request, job_id)
    supervisor(request).force(job_id)
    return _card(request, job_id)


@router.post('/jobs/{job_id}/done')
def warm_done(request: Request, job_id: str):
    _job_or_404(request, job_id)
    supervisor(request).done(job_id)
    return _card(request, job_id)


@router.post('/jobs/{job_id}/run-next')
def run_next(request: Request, job_id: str):
    job = _job_or_404(request, job_id)
    if job.state in PENDING:
        supervisor(request).run_next(job_id)
    return jobs_table(request)


@router.post('/jobs/{job_id}/retry')
def retry_job(request: Request, job_id: str, full_pass: str = Form('')):
    job = _job_or_404(request, job_id)
    view = _view(request, job)
    paths = [o['url'].path for o in view['retryable']]
    if not paths:
        raise HTTPException(400, 'nothing to retry')
    stop = job.params.get('early_stop') or {}
    options = planning.Options(patience=stop.get('patience', 4),
                               min_new_per_page=stop.get('min_new_per_page', 5),
                               max_pages=stop.get('max_pages', 0),
                               full_pass=bool(full_pass) or job.params.get('full_pass', False))
    new = create_scrape(request, paths, options,
                        title=f'Retry of {job.id}: {len(paths)} URL(s)')
    return _redirect(f'/jobs/{new.id}')


@router.post('/queue/pause')
def pause_queue(request: Request):
    supervisor(request).pause('paused by you')
    return jobs_table(request)


@router.post('/queue/resume')
def resume_queue(request: Request):
    supervisor(request).resume()
    return jobs_table(request)
