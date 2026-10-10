"""Run the control panel (localhost only).

    uv run python -m olx_panel                 # serve on http://127.0.0.1:8765
    uv run python -m olx_panel --port 8800
    uv run python -m olx_panel reindex         # rebuild panel.sqlite and exit

One worker, no --reload: the panel holds scraped_data/panel.lock and (from
Phase 3) owns the job queue, so there must be exactly one process.
"""
import argparse
import logging
import os
import sys

from .settings import Settings


def serve(args):
    import uvicorn
    from .app import create_app
    settings = Settings(port=args.port)
    print(f'OLX control panel on http://localhost:{settings.port}', flush=True)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port,
                workers=1, log_level='warning')


def reindex(args):
    """Drop panel.sqlite and rebuild it from the scraper's files."""
    from .app import PanelAlreadyRunning, _take_panel_lock
    from .core.service import PanelService
    settings = Settings()
    try:
        _take_panel_lock(settings.panel_lock)
    except PanelAlreadyRunning:
        sys.exit('The panel is running; stop it before reindexing.')
    for suffix in ('', '-wal', '-shm'):
        try:
            os.remove(settings.panel_db + suffix)
        except FileNotFoundError:
            pass
    service = PanelService(settings)
    service.refresh()
    model = service.model()
    print(f'Indexed {len(model.runs)} manifests, {model.ad_count} ads, '
          f'{len(model.tree)} location nodes.')
    if service.refresh_error:
        sys.exit(f'Refresh failed: {service.refresh_error}')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--port', type=int, default=Settings.port)
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('serve', help='run the web panel (default)').set_defaults(func=serve)
    sub.add_parser('reindex', help='rebuild panel.sqlite and exit').set_defaults(func=reindex)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    getattr(args, 'func', serve)(args)


if __name__ == '__main__':
    main()
