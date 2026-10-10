"""Hand-maintained corrections (olx_panel/overrides.yaml) for facts the
manifests cannot tell: legacy runs that were resumes, and where off-tree
legacy regions should count."""
import os
from dataclasses import dataclass, field

import yaml

from .manifests import CREDITED, COMPLETE, CAUGHT_UP, CAPPED, PARTIAL, BLOCKED, \
    FAILED, CANCELLED, UNKNOWN, RESUME, SCOPE_MISMATCH

OUTCOMES = frozenset({COMPLETE, CAUGHT_UP, CAPPED, PARTIAL, BLOCKED, FAILED,
                      CANCELLED, UNKNOWN, RESUME, SCOPE_MISMATCH}) | CREDITED


@dataclass
class Overrides:
    legacy_runs: dict = field(default_factory=dict)     # (region, run_id) -> (outcome, note)
    legacy_regions: dict = field(default_factory=dict)  # region -> attach path
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
    return Overrides(runs, regions, version)
