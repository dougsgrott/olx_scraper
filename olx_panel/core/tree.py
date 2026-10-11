"""OLX's location tree (scraped_data/locations.sqlite), read-only, with the
planner's status per node and alias twins (olx_patchright/plan.py)."""
import os
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime

from olx_patchright.plan import classify_node, find_aliases

from .sources import read_only

UF_NAMES = {
    'AC': 'Acre', 'AL': 'Alagoas', 'AP': 'Amapá', 'AM': 'Amazonas',
    'BA': 'Bahia', 'CE': 'Ceará', 'DF': 'Distrito Federal',
    'ES': 'Espírito Santo', 'GO': 'Goiás', 'MA': 'Maranhão',
    'MT': 'Mato Grosso', 'MS': 'Mato Grosso do Sul', 'MG': 'Minas Gerais',
    'PA': 'Pará', 'PB': 'Paraíba', 'PR': 'Paraná', 'PE': 'Pernambuco',
    'PI': 'Piauí', 'RJ': 'Rio de Janeiro', 'RN': 'Rio Grande do Norte',
    'RS': 'Rio Grande do Sul', 'RO': 'Rondônia', 'RR': 'Roraima',
    'SC': 'Santa Catarina', 'SP': 'São Paulo', 'SE': 'Sergipe',
    'TO': 'Tocantins',
}


@dataclass
class Node:
    path: str
    uf: str
    parent_path: str
    slug: str
    depth: int
    total_of_ads: int
    probed_at: datetime

    @property
    def inventory(self):
        """OLX's live ad count, or None (unprobed or a sentinel)."""
        count = self.total_of_ads
        return count if self.probed_at and count is not None and count >= 0 else None


def _local(value):
    """locations.sqlite stores naive local datetimes as text."""
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone()


class LocationTree:
    def __init__(self, nodes, threshold, inventory_source=None):
        self.threshold = threshold
        # path -> 'run' where a scrape's page-1 count replaced the probe's
        self.inventory_source = inventory_source or {}
        self.nodes = {n.path: n for n in nodes}
        self.children = defaultdict(list)
        for n in sorted(nodes, key=lambda n: n.path):
            if n.parent_path:
                self.children[n.parent_path].append(n)
        self.roots = sorted((n for n in nodes if n.depth == 1),
                            key=lambda n: n.path)
        self.status = {n.path: classify_node(n, self.children[n.path], threshold)
                       for n in nodes}
        self.alias_of = find_aliases(nodes)
        self.twins = defaultdict(set)  # every member -> all members of its group
        for twin, canonical in self.alias_of.items():
            group = self.twins[canonical] | {canonical, twin}
            for member in group:
                self.twins[member] = group

    def __contains__(self, path):
        return path in self.nodes

    def __len__(self):
        return len(self.nodes)

    def ancestors(self, path):
        """Ancestor paths, nearest first."""
        segments = path.split('/')
        return ['/'.join(segments[:i]) for i in range(len(segments) - 1, 0, -1)]

    def walk(self):
        """Every node, parents before children."""
        stack = list(reversed(self.roots))
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(self.children[node.path]))


def load_tree(locations_db, threshold):
    if not os.path.exists(locations_db):
        return LocationTree([], threshold)
    with read_only(locations_db) as ro:
        rows = ro.execute('SELECT path, uf, parent_path, slug, depth, '
                          'total_of_ads, probed_at FROM locations').fetchall()
    return LocationTree([
        Node(r['path'], r['uf'], r['parent_path'], r['slug'], r['depth'],
             r['total_of_ads'], _local(r['probed_at']))
        for r in rows
    ], threshold)


# Portuguese connectors stay lowercase inside place names ("Alto de Pinheiros").
_CONNECTORS = frozenset({'de', 'da', 'do', 'das', 'dos', 'e'})


def with_run_counts(tree, runs):
    """The tree with each scope's OLX count brought up to date: a run
    records OLX's live count on its first page (manifest v2 total_of_ads),
    so a count newer than the probe's replaces it — status, plan kinds and
    shares then follow OLX's current numbers. Sentinel nodes keep their
    sentinel (a padded scope's served count is a broader search's), and
    runs whose served scope didn't match the request are ignored."""
    latest = {}
    for run in runs:
        count = run.total_of_ads
        if not isinstance(count, int) or count < 0 or run.off_tree or \
                run.outcome == 'scope_mismatch':
            continue
        if run.node_path not in latest or run.started_at > latest[run.node_path][1]:
            latest[run.node_path] = (count, run.started_at)
    nodes, sources = [], {}
    for node in tree.nodes.values():
        update = latest.get(node.path)
        if update and (node.total_of_ads is None or node.total_of_ads >= 0) and \
                (node.probed_at is None or update[1] > node.probed_at):
            nodes.append(replace(node, total_of_ads=update[0], probed_at=update[1]))
            sources[node.path] = 'run'
        else:
            nodes.append(node)
    if not sources:
        return tree
    return LocationTree(nodes, tree.threshold, sources)


def title_from_slug(slug):
    """Fallback display name (no accents: slugs drop them). Breadcrumb labels
    from v2 manifests replace these as scopes get scraped."""
    words = slug.split('-')
    return ' '.join(w if i and w in _CONNECTORS else w.capitalize()
                    for i, w in enumerate(words))
