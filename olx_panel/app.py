"""FastAPI application: middleware, static files, page routers, and the
lifespan that owns the single-instance lock and the background refresher."""
import fcntl
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .core.jobs.runner import Supervisor
from .core.service import PanelService, Refresher
from .security import ALLOWED_HOSTS, OriginGuard
from .settings import PACKAGE_DIR, Settings
from .web import common
from .web.pages import load_pages

log = logging.getLogger(__name__)


class PanelAlreadyRunning(RuntimeError):
    pass


def _take_panel_lock(path):
    """One panel per checkout: two would both write panel.sqlite and (from
    Phase 3) both run job queues. Held until the process exits."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise PanelAlreadyRunning(f'another panel holds {path}')
    return fd


def create_app(settings=None, background=True):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        lock_fd = _take_panel_lock(settings.panel_lock)
        service = PanelService(settings)
        service.refresh()
        app.state.service = service
        refresher = Refresher(service, settings.refresh_interval_s)
        supervisor = Supervisor(service)
        app.state.supervisor = supervisor
        if background:
            refresher.start()
            supervisor.start()
        try:
            yield
        finally:
            # Jobs run in their own sessions: stopping the panel never stops
            # them; the next panel reattaches.
            supervisor.stop()
            refresher.stop()
            os.close(lock_fd)

    app = FastAPI(title='OLX control panel', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.pages = load_pages()
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.add_middleware(OriginGuard)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    app.mount('/static', StaticFiles(directory=os.path.join(PACKAGE_DIR, 'static')),
              name='static')
    app.include_router(common.router)
    for page in app.state.pages:
        app.include_router(page.router)
    return app
