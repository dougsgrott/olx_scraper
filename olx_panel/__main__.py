"""Run the control panel (localhost only).

    uv run python -m olx_panel                 # serve on http://127.0.0.1:8765
    uv run python -m olx_panel --port 8800
    uv run python -m olx_panel reindex         # rebuild panel.sqlite and exit
    uv run python -m olx_panel crosswalk-report  # OLX node <-> IBGE matching gaps
    uv run python -m olx_panel plan --due -o due.yaml   # run_config for a scheduler

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


EXIT_NOTHING = 3


def plan(args, settings=None):
    """Write a run_config for the CLI / scheduled runner: the scopes that are
    due (yield x time since fresh), stale, or named — planned like the
    panel's preview. Exits 3 when there is nothing to scrape, so a wrapper
    can skip the run."""
    from datetime import datetime
    from .core import planning
    from .core.service import PanelService
    settings = settings or Settings()
    service = PanelService(settings)
    service.refresh()
    model = service.model()
    now = datetime.now().astimezone()
    paths = list(args.paths)
    if args.due:
        paths += planning.due_paths(model, now, settings.due_new_ads, settings.fresh_days)
    if args.stale:
        paths += planning.stale_paths(model, args.stale, now)
    defaults = planning.options_from_run_config(service.run_config())
    options = planning.Options(
        patience=defaults.patience if args.patience is None else args.patience,
        min_new_per_page=args.min_new or defaults.min_new_per_page,
        max_pages=defaults.max_pages if args.max_pages is None else args.max_pages,
        mode=args.mode)
    preview = planning.preview(model, paths, options, settings.scope)
    for warning in preview.warnings:
        print(f'# WARN {warning}', file=sys.stderr)
    if not preview.items:
        print('Nothing to scrape.', file=sys.stderr)
        return EXIT_NOTHING
    text = planning.plan_yaml(preview)
    if args.output:
        tmp = args.output + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, args.output)
    else:
        sys.stdout.write(text)
    full = sum(1 for i in preview.items if i.full)
    print(f'{len(preview.items)} URL(s), ~{preview.pages} pages, '
          f'~{preview.seconds / 60:.0f} min{f", {full} full pass(es)" if full else ""}.',
          file=sys.stderr)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--port', type=int, default=Settings.port)
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('serve', help='run the web panel (default)').set_defaults(func=serve)
    sub.add_parser('reindex', help='rebuild panel.sqlite and exit').set_defaults(func=reindex)
    sub.add_parser('crosswalk-report', help='list OLX nodes / ad places the '
                   'IBGE crosswalk could not match').set_defaults(func=crosswalk_report)
    planner = sub.add_parser('plan', help='write a run_config for the CLI / scheduler '
                             '(exit 3: nothing to scrape)')
    planner.add_argument('paths', nargs='*', help='location paths to plan')
    planner.add_argument('--due', action='store_true',
                         help='add the plan leaves due by yield (or stale without one)')
    planner.add_argument('--stale', type=int, metavar='DAYS',
                         help='add plan leaves scraped before but not fresh within DAYS')
    planner.add_argument('--mode', choices=('incremental', 'smart', 'full'),
                         default='incremental',
                         help='smart: full pass only where never walked to the end')
    planner.add_argument('--patience', type=int, help='default: run_config.yaml')
    planner.add_argument('--min-new', type=int, help='default: run_config.yaml')
    planner.add_argument('--max-pages', type=int, help='default: run_config.yaml')
    planner.add_argument('-o', '--output', help='write here instead of stdout')
    planner.set_defaults(func=plan)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    return getattr(args, 'func', serve)(args)


if __name__ == '__main__':
    sys.exit(main())
