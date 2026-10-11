"""Shared web plumbing: templates and their filters, the banner, refresh."""
import os
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from olx_panel.settings import PACKAGE_DIR

templates = Jinja2Templates(directory=os.path.join(PACKAGE_DIR, 'templates'))
router = APIRouter()


def _ts(value):
    return value.strftime('%Y-%m-%d %H:%M') if value else '—'


def _day(value):
    return value.strftime('%Y-%m-%d') if value else '—'


def _ago(value):
    if not value:
        return 'never'
    seconds = (datetime.now().astimezone() - value).total_seconds()
    if seconds < 3600:
        return f'{max(0, int(seconds // 60))} min ago'
    if seconds < 86400:
        return f'{int(seconds // 3600)} h ago'
    return f'{int(seconds // 86400)} d ago'


def _num(value):
    return '—' if value is None else f'{value:,}'.replace(',', ' ')


def _pct(value):
    return '—' if value is None else f'{value:.0%}'


def _duration(seconds):
    if seconds is None:
        return '—'
    seconds = int(seconds)
    if seconds < 60:
        return f'{seconds}s'
    if seconds < 3600:
        return f'{seconds // 60}m{seconds % 60:02d}s'
    return f'{seconds // 3600}h{seconds % 3600 // 60:02d}m'


templates.env.filters.update(ts=_ts, day=_day, ago=_ago, num=_num, pct=_pct,
                             duration=_duration)


def service(request):
    return request.app.state.service


def render(request, name, active=None, **context):
    context.update(request=request, pages=request.app.state.pages, active=active)
    return templates.TemplateResponse(request, name, context)


@router.get('/')
def home():
    return RedirectResponse('/coverage', status_code=307)


def supervisor(request):
    return request.app.state.supervisor


def _banner(request, page_version=''):
    status = service(request).status()
    return render(request, 'partials/banner.html', status=status,
                  queue=supervisor(request).status(),
                  stale_page=bool(page_version) and page_version != status['version'])


@router.get('/partials/banner')
def banner(request: Request, v: str = ''):
    return _banner(request, v)


@router.post('/admin/refresh')
def refresh(request: Request, v: str = ''):
    service(request).refresh()
    return _banner(request, v or 'refreshed')
