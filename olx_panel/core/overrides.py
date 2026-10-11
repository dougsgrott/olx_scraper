"""Hand-maintained corrections (olx_panel/overrides.yaml) for facts the
manifests cannot tell: legacy runs that were resumes, and where off-tree
legacy regions should count."""
import os
from dataclasses import dataclass, field

import yaml

from .geo import KINDS

from .manifests import CREDITED, COMPLETE, CAUGHT_UP, CAPPED, PARTIAL, BLOCKED, \
    FAILED, CANCELLED, UNKNOWN, RESUME, SCOPE_MISMATCH

OUTCOMES = frozenset({COMPLETE, CAUGHT_UP, CAPPED, PARTIAL, BLOCKED, FAILED,
                      CANCELLED, UNKNOWN, RESUME, SCOPE_MISMATCH}) | CREDITED


@dataclass
class Overrides:
    legacy_runs: dict = field(default_factory=dict)     # (region, run_id) -> (outcome, note)
    legacy_regions: dict = field(default_factory=dict)  # region -> attach path
    crosswalk: dict = field(default_factory=dict)       # node path -> (kind, IBGE code)
    ad_municipalities: dict = field(default_factory=dict)  # (uf, name) -> IBGE code
    version: object = None


def load_overrides(path):
    try:
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        version = os.stat(path).st_mtime_ns
    except FileNotFoundError:
        return Overrides()
    runs = {}
    for entry in data.get('legacy_runs') or []:
        outcome = entry.get('outcome')
        if outcome not in OUTCOMES:
            raise ValueError(f'{path}: unknown outcome {outcome!r} for {entry}')
        runs[(entry['region'], str(entry['run_id']))] = (outcome, entry.get('note'))
    regions = {region: attach for region, attach in
               (data.get('legacy_regions') or {}).items()}
    crosswalk = {}
    for path, entry in (data.get('crosswalk') or {}).items():
        if entry.get('kind') not in KINDS:
            raise ValueError(f'{path}: unknown crosswalk kind {entry.get("kind")!r}')
        crosswalk[path] = (entry['kind'], str(entry['ibge']) if entry.get('ibge') else None)
    ad_names = {}
    for key, code in (data.get('ad_municipalities') or {}).items():
        uf, _, name = key.partition('/')
        ad_names[(uf, name)] = str(code)
    return Overrides(runs, regions, crosswalk, ad_names, version)
