"""What each job kind runs. Every kind drives the one browser profile, so all
of them hold the 'browser' resource and run one at a time; the child takes
.browser.lock itself and exits 75 if something outside the panel holds it.

Commands are argument lists (never a shell) built from tree-validated paths
and plan.start_url; the per-job run_config.yaml is generated, never the
user's own."""
import os

import yaml

from olx_patchright.plan import DEFAULT_SCOPE

SCRAPE = 'scrape'
WARM = 'warm'
PROBE = 'probe'
CLEANUP = 'browser_cleanup'
KINDS = (SCRAPE, WARM, PROBE, CLEANUP)
RESOURCES = {kind: frozenset({'browser'}) for kind in KINDS}
LABELS = {SCRAPE: 'Scrape', WARM: 'Warm profile', PROBE: 'Probe OLX counts',
          CLEANUP: 'Browser cleanup'}


def _write_config(job_dir, config):
    path = os.path.join(job_dir, 'run_config.yaml')
    with open(path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)
    return path


def command(job, settings, job_dir):
    """The argv for a job; writes whatever input files it needs into job_dir."""
    params = job.params
    if job.kind == SCRAPE:
        full = set(params.get('full_paths') or [])
        mixed = bool(params.get('early_stop'))
        catalog = {'start_urls': [{'url': u.url, 'early_stop': {}}
                                  if mixed and u.path in full else u.url
                                  for u in job.urls]}
        if params.get('early_stop'):
            catalog['early_stop'] = params['early_stop']
        path = _write_config(job_dir, {'mode': 'CATALOG', 'catalog_spider': catalog})
        return list(settings.scrape_command) + ['--config', path]
    if job.kind == WARM:
        path = _write_config(job_dir, {'mode': 'WARM', 'warm': {'url': params['url']}})
        return list(settings.scrape_command) + ['--config', path]
    if job.kind == PROBE:
        argv = list(settings.probe_command) + list(params['paths'])
        argv += ['--scope', params.get('scope') or DEFAULT_SCOPE]
        if params.get('refresh'):
            argv.append('--refresh')
        return argv
    if job.kind == CLEANUP:
        return list(settings.cleanup_command)
    raise ValueError(f'unknown job kind: {job.kind}')
