"""A read-only digest of run_config.yaml: what the next CLI/scheduled run
will scrape, and the things worth a warning (resume offsets, URLs off the
location tree, alias twins scraped twice). The panel never writes this file."""
from dataclasses import dataclass, field

import yaml

from olx_patchright.export import infer_region, infer_scope, start_page_of
from olx_patchright.plan import path_of


@dataclass
class RunConfigSummary:
    path: str
    error: str = None
    mode: str = None
    urls: list = field(default_factory=list)
    early_stop: dict = None
    scopes: list = field(default_factory=list)
    offsets: list = field(default_factory=list)      # (url, start page)
    off_tree: list = field(default_factory=list)     # urls
    alias_pairs: list = field(default_factory=list)  # (twin path, canonical path)


def summarize_run_config(path, tree):
    summary = RunConfigSummary(path)
    try:
        with open(path, encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        summary.error = 'not found'
        return summary
    except yaml.YAMLError as exc:
        summary.error = f'invalid YAML: {exc}'
        return summary
    if not isinstance(config, dict):
        summary.error = 'not a YAML mapping'
        return summary
    catalog = config.get('catalog_spider') or {}
    summary.mode = config.get('mode')
    summary.urls = [u.get('url') if isinstance(u, dict) else u
                    for u in catalog.get('start_urls') or []
                    if isinstance(u, str) or (isinstance(u, dict) and isinstance(u.get('url'), str))]
    summary.early_stop = catalog.get('early_stop')
    summary.scopes = sorted({infer_scope(u) for u in summary.urls})
    paths = {}
    for url in summary.urls:
        node_path = path_of(infer_region(url))
        paths[node_path] = url
        if start_page_of(url) > 1:
            summary.offsets.append((url, start_page_of(url)))
        if node_path not in tree:
            summary.off_tree.append(url)
    for twin, canonical in tree.alias_of.items():
        if twin in paths and canonical in paths:
            summary.alias_pairs.append((twin, canonical))
    return summary
