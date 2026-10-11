"""Crosswalk between OLX location nodes and IBGE municipalities.

The map draws IBGE geometry (scraped_data/geo, built by scripts/build_geo.py);
the panel's metrics live on OLX nodes. Every node gets a kind and, where it
denotes a place on the ground, a municipality code:

  state          depth 1
  ddd_area       depth 2 (OLX macro-region = a telephone DDD area)
  city           a municipality (depth 3, or depth 4 under an aggregate)
  district       part of a municipality named with it (`candia-pontal`)
  zone           a capital's zone (`zona-sul`, `centro`): part of the
                 macro's core city
  aggregate      a group of cities (`outras-cidades`, `grande-*`) or a
                 region listed without its cities (`zona-da-mata`)
  neighbourhood  below a city or zone; mapped to that municipality
  unknown        nothing matched (see the report; fix in overrides.yaml)

Structure first, names second: a depth-3 node is a city only if its slug is
a municipality name; one whose children are mostly municipalities is an
aggregate; one with other children is a zone. Names match in the node's UF
first, then among municipalities sharing the macro's DDD in any UF (the DF
macro lists Goiás towns of DDD 61). OLX slugs drop apostrophes (`doeste`) and
write number words as digits (`2-irmaos`), so both variants are keys.
"""
import csv
import os
from collections import Counter, defaultdict
from dataclasses import dataclass

from olx_patchright.plan import norm

KINDS = ('state', 'ddd_area', 'city', 'district', 'zone', 'aggregate',
         'neighbourhood', 'unknown')
PLACE_KINDS = ('city', 'district', 'zone')  # map to one municipality each
NUMBER_WORDS = {'um': '1', 'uma': '1', 'dois': '2', 'duas': '2', 'tres': '3',
                'quatro': '4', 'cinco': '5', 'seis': '6', 'sete': '7',
                'oito': '8', 'nove': '9', 'dez': '10'}
_AGGREGATE_SLUGS = ('outras-cidades', 'todas-as-cidades')
# Slugs starting like this name a region, even when a town ends the slug
# (`regiao-de-ipatinga`, `vale-do-jequitinhonha`): never a district.
_REGION_PREFIXES = ('regiao-', 'regioes-', 'vale-', 'grande-', 'zona-',
                    'norte-', 'sul-', 'leste-', 'oeste-', 'centro-')


@dataclass(frozen=True)
class Municipality:
    code: str
    name: str
    uf: str
    ddd: str
    capital: bool
    lat: float
    lon: float

    @property
    def area(self):
        return f'{self.uf}-{self.ddd}'


def name_keys(name):
    """Every slug OLX may use for a place name."""
    keys = {norm(name), norm(name.replace("'", '').replace('’', ''))}
    for key in list(keys):
        keys.add('-'.join(NUMBER_WORDS.get(t, t) for t in key.split('-')))
    keys.discard('')
    return keys


class Gazetteer:
    def __init__(self, municipalities):
        self.by_code = {m.code: m for m in municipalities}
        self._by_uf = defaultdict(dict)
        self._by_ddd = defaultdict(lambda: defaultdict(set))
        self.capital = {}
        self.ddds_of_uf = defaultdict(set)
        for m in municipalities:
            for key in name_keys(m.name):
                self._by_uf[m.uf][key] = m.code
                self._by_ddd[m.ddd][key].add(m.code)
            if m.capital:
                self.capital[m.uf] = m.code
            self.ddds_of_uf[m.uf].add(m.ddd)

    def match(self, slug, uf):
        return self._by_uf[uf].get(slug)

    def match_in_ddd(self, slug, ddd):
        codes = self._by_ddd[ddd].get(slug) if ddd else None
        return next(iter(codes)) if codes and len(codes) == 1 else None

    def match_suffix(self, slug, uf):
        """A trailing span naming a municipality (`candia-pontal`)."""
        tokens = slug.split('-')
        for i in range(1, len(tokens)):
            code = self.match('-'.join(tokens[i:]), uf)
            if code:
                return code
        return None

    def match_spans(self, slug, uf):
        """Municipalities named by any contiguous span, longest first."""
        tokens = slug.split('-')
        found = []
        for size in range(len(tokens), 0, -1):
            for start in range(len(tokens) - size + 1):
                code = self.match('-'.join(tokens[start:start + size]), uf)
                if code and code not in found:
                    found.append(code)
        return found

    def by_name(self, name, uf, aliases=None):
        """Code of an ad's locationDetails municipality. OLX sometimes puts a
        district there: "Distrito (Município)" resolves to the municipality;
        bare district names need an entry in `aliases` ({(uf, name): code})."""
        name = name or ''
        if aliases and (uf, name) in aliases:
            return aliases[(uf, name)]
        candidates = [name]
        if '(' in name and name.endswith(')'):
            candidates.append(name[name.rindex('(') + 1:-1])
        for candidate in candidates:
            for key in name_keys(candidate):
                code = self.match(key, uf)
                if code:
                    return code
        return None


def load_gazetteer(csv_path):
    if not os.path.exists(csv_path):
        return None
    with open(csv_path, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    return Gazetteer([
        Municipality(r['code'], r['name'], r['uf'], r['ddd'], r['capital'] == '1',
                     float(r['lat'] or 0), float(r['lon'] or 0))
        for r in rows
    ])


@dataclass
class NodeGeo:
    path: str
    kind: str
    uf: str
    ddd: str = None
    code: str = None
    method: str = None


class Crosswalk:
    def __init__(self, tree, gazetteer, overrides=None, breadcrumb_ddd=None):
        self.tree = tree
        self.gaz = gazetteer
        self.overrides = overrides or {}
        self.breadcrumb_ddd = breadcrumb_ddd or {}
        self.nodes = {}
        self.macro_ddd = {}
        self._first_pass()
        self._macro_ddds()
        self._second_pass()
        self._index()

    # -- classification ------------------------------------------------------

    def _set(self, node, kind, code=None, method=None):
        self.nodes[node.path] = NodeGeo(node.path, kind, node.uf, code=code,
                                        method=method)

    def _kids_matching(self, node, ddd=None):
        kids = self.tree.children[node.path]
        if not kids:
            return 0.0
        hits = sum(1 for k in kids if self.gaz.match(k.slug, node.uf) or
                   (ddd and self.gaz.match_in_ddd(k.slug, ddd)))
        return hits / len(kids)

    def _first_pass(self):
        """Everything decidable from names within the node's own UF."""
        for node in self.tree.walk():
            override = self.overrides.get(node.path)
            if override:
                self._set(node, override[0], override[1], 'override')
                continue
            parent = self.nodes.get(node.parent_path)
            if node.depth == 1:
                self._set(node, 'state', method='depth')
            elif node.depth == 2:
                self._set(node, 'ddd_area', method='depth')
            elif parent and parent.kind in ('city', 'district', 'zone', 'neighbourhood'):
                self._set(node, 'neighbourhood', parent.code, 'parent')
            elif node.depth == 3 or (parent and parent.kind == 'aggregate'):
                code = self.gaz.match(node.slug, node.uf)
                if code:
                    self._set(node, 'city', code, 'name')
                elif node.depth == 3 and self._kids_matching(node) >= 0.5:
                    self._set(node, 'aggregate', method='children')
            # anything else waits for the macro's DDD (second pass)

    def _macro_ddds(self):
        """Each macro's DDD: OLX's own breadcrumb, else the DDD most of its
        cities share, else that of a town its slug names, else the UF's only
        DDD — or the one DDD of the UF its sibling macros leave unclaimed."""
        votes = defaultdict(Counter)
        for geo in self.nodes.values():
            if geo.kind == 'city' and geo.code:
                votes[macro_of(geo.path)][self.gaz.by_code[geo.code].ddd] += 1
        macros = [n for n in self.tree.walk() if n.depth == 2]
        for node in macros:
            ddd = self.breadcrumb_ddd.get(node.path)
            if not ddd and votes[node.path]:
                ddd = votes[node.path].most_common(1)[0][0]
            if not ddd:
                named = self.gaz.match_spans(node.slug, node.uf)
                ddd = self.gaz.by_code[named[0]].ddd if named else None
            if not ddd and len(self.gaz.ddds_of_uf.get(node.uf, ())) == 1:
                ddd = next(iter(self.gaz.ddds_of_uf[node.uf]))
            self.macro_ddd[node.path] = ddd
        for node in macros:
            if self.macro_ddd[node.path]:
                continue
            claimed = {self.macro_ddd[m.path] for m in macros if m.uf == node.uf}
            left = self.gaz.ddds_of_uf.get(node.uf, set()) - claimed
            if len(left) == 1:
                self.macro_ddd[node.path] = next(iter(left))

    def _second_pass(self):
        for node in self.tree.walk():
            ddd = self.macro_ddd.get(macro_of(node.path))
            if node.path in self.nodes:
                self._set_ddd(self.nodes[node.path], ddd)
                continue
            parent = self.nodes.get(node.parent_path)
            if parent and parent.kind in ('city', 'district', 'zone', 'neighbourhood'):
                self._set(node, 'neighbourhood', parent.code, 'parent')
            elif node.depth == 3 or (parent and parent.kind == 'aggregate'):
                code = self.gaz.match_in_ddd(node.slug, ddd)
                kids = self.tree.children[node.path]
                if code:
                    self._set(node, 'city', code, 'name-in-ddd')
                elif node.depth == 3 and kids and self._kids_matching(node, ddd) >= 0.5:
                    self._set(node, 'aggregate', method='children')
                elif node.depth == 3 and kids:
                    self._set(node, 'zone', self._core_city(node), 'zone')
                elif not node.slug.startswith(_REGION_PREFIXES) and \
                        self.gaz.match_suffix(node.slug, node.uf):
                    self._set(node, 'district', self.gaz.match_suffix(node.slug, node.uf),
                              'suffix')
                elif node.depth == 3 or node.slug in _AGGREGATE_SLUGS:
                    self._set(node, 'aggregate', method='childless')
                else:
                    self._set(node, 'unknown', method='unmatched')
            else:
                self._set(node, 'unknown', method='unmatched')
            self._set_ddd(self.nodes[node.path], ddd)

    def _set_ddd(self, geo, macro_ddd):
        """A municipality's own DDD where known, else its macro's."""
        place = self.gaz.by_code.get(geo.code)
        geo.ddd = place.ddd if place else macro_ddd

    def _core_city(self, zone):
        """The municipality a capital zone belongs to: the UF capital if the
        macro's slug names it (or names nothing), else the named one."""
        macro_slug = macro_of(zone.path).rsplit('/', 1)[-1]
        capital = self.gaz.capital.get(zone.uf)
        named = self.gaz.match_spans(macro_slug, zone.uf)
        if capital in named or not named:
            return capital
        return named[0]

    # -- indexes for the map -------------------------------------------------

    def _index(self):
        self.muni_nodes = defaultdict(list)   # code -> place node paths
        votes = defaultdict(Counter)          # area -> macro votes
        for geo in self.nodes.values():
            if geo.kind in PLACE_KINDS and geo.code in self.gaz.by_code:
                self.muni_nodes[geo.code].append(geo.path)
                votes[self.gaz.by_code[geo.code].area][macro_of(geo.path)] += 1
        for macro, ddd in self.macro_ddd.items():
            if ddd:
                votes[f'{macro.split("/")[0][-2:].upper()}-{ddd}'][macro] += 1
        self.area_macro = {area: c.most_common(1)[0][0] for area, c in votes.items()}
        self.macro_areas = defaultdict(list)
        for area, macro in sorted(self.area_macro.items()):
            self.macro_areas[macro].append(area)

    def code_of(self, path):
        geo = self.nodes.get(path)
        return geo.code if geo else None

    def report(self):
        kinds = Counter(g.kind for g in self.nodes.values())
        methods = Counter(g.method for g in self.nodes.values())
        return {
            'kinds': dict(kinds),
            'methods': dict(methods),
            'unknown': sorted(p for p, g in self.nodes.items() if g.kind == 'unknown'),
            'macros_without_ddd': sorted(m for m, d in self.macro_ddd.items() if not d),
            'areas_without_macro': sorted(
                {m.area for m in self.gaz.by_code.values()} - set(self.area_macro)),
        }


def macro_of(path):
    """The depth-2 (DDD area) ancestor path, or the path itself."""
    return '/'.join(path.split('/')[:2])
