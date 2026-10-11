"""Run the control panel (localhost only).

    uv run python -m olx_panel                 # serve on http://127.0.0.1:8765
    uv run python -m olx_panel --port 8800
    uv run python -m olx_panel reindex         # rebuild panel.sqlite and exit
    uv run python -m olx_panel crosswalk-report  # OLX node <-> IBGE matching gaps

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


def crosswalk_report(args):
    """What the OLX node <-> IBGE municipality crosswalk could not resolve;
    fix leftovers in olx_panel/overrides.yaml."""
    from .core.service import PanelService
    service = PanelService(Settings())
    service.refresh()
    model = service.model()
    if model.geo is None:
        sys.exit('No map assets: run `uv run python scripts/build_geo.py` first.')
    report = model.geo.report()
    print('Node kinds:   ' + ', '.join(f'{k} {n}' for k, n in sorted(report['kinds'].items())))
    print('Matched by:   ' + ', '.join(f'{k} {n}' for k, n in sorted(report['methods'].items())))
    sections = [
        ('Unknown nodes (add to overrides.yaml crosswalk:)', report['unknown']),
        ('Macros without a DDD', report['macros_without_ddd']),
        ('UF-DDD areas no macro lists (border towns using a neighbour\'s DDD)',
         report['areas_without_macro']),
        ('Ad municipalities not in IBGE (add to overrides.yaml ad_municipalities:)',
         [f'{uf}/{name} ({n} ads)' for (uf, name), n in
          sorted(model.unmatched_ad_places.items())]),
    ]
    for title, items in sections:
        print(f'\n{title}: {len(items)}')
        for item in items:
            print(f'  {item}')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--port', type=int, default=Settings.port)
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('serve', help='run the web panel (default)').set_defaults(func=serve)
    sub.add_parser('reindex', help='rebuild panel.sqlite and exit').set_defaults(func=reindex)
    sub.add_parser('crosswalk-report', help='list OLX nodes / ad places the '
                   'IBGE crosswalk could not match').set_defaults(func=crosswalk_report)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    getattr(args, 'func', serve)(args)


if __name__ == '__main__':
    main()
