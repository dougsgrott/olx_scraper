"""Browser-free stand-ins for patchright and OLX.

FakeSite plays OLX: it serves catalogue pages as App Router flight-payload
HTML (the shape olx_patchright.parse.page_props reads), paginated by ?o=N,
and — like the real site — re-serves its last page for any ?o= beyond its
page cap. fake_sync_playwright(site) replaces catalog.sync_playwright /
warm.sync_playwright so the real scrape loop, exporter, lock and signal
handling run without a browser.
"""
import json
import time
from urllib.parse import urlparse, parse_qs

BREADCRUMB = [
    {'value': '1', 'level': 'national', 'label': 'Brasil', 'url': '/imoveis/aluguel'},
    {'name': 'Espírito Santo', 'value': '14', 'level': 'state', 'label': 'ES',
     'url': '/imoveis/aluguel/estado-es'},
    {'value': '27', 'level': 'region', 'label': 'DDD 27 - Norte do Espírito Santo',
     'url': '/imoveis/aluguel/estado-es/norte-do-espirito-santo'},
]


def page_html(props):
    """Wrap props the way OLX streams them: one self.__next_f.push chunk."""
    if props is None:
        return '<html><body>no payload</body></html>'
    payload = json.dumps(props, ensure_ascii=False)
    return f'<script>self.__next_f.push([1,{json.dumps(payload)}])</script>'


def make_ad(uid):
    return {
        'listId': uid,
        'subject': f'Apartamento {uid}',
        'friendlyUrl': f'https://es.olx.com.br/norte-do-espirito-santo/imoveis/apto-{uid}',
        'location': 'Vitória, Centro',
        'locationDetails': {'municipality': 'Vitória', 'neighbourhood': 'Centro',
                            'uf': 'ES', 'ddd': '27'},
        'priceValue': 'R$ 1.500',
        'origListTime': 1780000000,
        'properties': [],
    }


class FakeSite:
    """`total` ads under one scope, newest first, PAGE_SIZE per page, at most
    `page_cap` pages served. `uid_base` keeps sites' uids disjoint.
    `on_goto(page_no, url)` lets a test act mid-scrape (raise, signal, sleep);
    `index_lies` makes clamped pages echo the requested pageIndex, leaving
    only their repeated content to give them away."""

    def __init__(self, total, page_cap=100, page_size=50, uid_base=1000000,
                 on_goto=None, index_lies=False, payload=True, delay_s=0, status=200):
        self.total = total
        self.page_cap = page_cap
        self.page_size = page_size
        self.uid_base = uid_base
        self.on_goto = on_goto
        self.index_lies = index_lies
        self.payload = payload
        self.delay_s = delay_s
        self.status = status
        self.requests = []

    def props_for(self, url):
        parsed = urlparse(url)
        requested = int(parse_qs(parsed.query).get('o', ['1'])[0])
        served = min(requested, self.page_cap)
        first = (served - 1) * self.page_size
        count = max(0, min(self.page_size, self.total - first))
        return {
            'ads': [make_ad(self.uid_base + first + i) for i in range(count)],
            'totalOfAds': self.total,
            'pageSize': self.page_size,
            'pageIndex': requested if self.index_lies else served,
            # like real OLX: the path plus the query it was asked for
            'listingUrl': parsed.path + (f'?{parsed.query}' if parsed.query else ''),
            'locations': BREADCRUMB,
            'filters': {'sort': 'date', 'pageIndex': served},
        }


class FakeSites:
    """Route each start URL's path to its own FakeSite."""

    def __init__(self, by_path):
        self.by_path = by_path

    def site_for(self, url):
        return self.by_path[urlparse(url).path]


class FakeResponse:
    def __init__(self, status=200):
        self.status = status


class FakePage:
    def __init__(self, sites):
        self._sites = sites
        self._props = None
        self.url = 'about:blank'

    def goto(self, url, wait_until=None):
        site = self._sites.site_for(url)
        site.requests.append(url)
        page_no = int(parse_qs(urlparse(url).query).get('o', ['1'])[0])
        if site.on_goto:
            site.on_goto(page_no, url)
        if site.delay_s:
            time.sleep(site.delay_s)
        self.url = url
        self._props = site.props_for(url) if site.payload else None
        return FakeResponse(site.status)

    def content(self):
        return page_html(self._props)


class FakeContext:
    def __init__(self, sites):
        self.pages = [FakePage(sites)]
        self.closed = False

    def set_default_navigation_timeout(self, ms):
        pass

    def new_page(self):
        page = FakePage(self.pages[0]._sites)
        self.pages.append(page)
        return page

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, sites):
        self._sites = sites
        self.contexts = []

    def launch_persistent_context(self, **kwargs):
        ctx = FakeContext(self._sites)
        self.contexts.append(ctx)
        return ctx


class FakePlaywright:
    def __init__(self, sites):
        self.chromium = FakeChromium(sites)


class _Manager:
    def __init__(self, playwright):
        self._playwright = playwright

    def __enter__(self):
        return self._playwright

    def __exit__(self, *exc):
        return False


def fake_sync_playwright(sites):
    """A drop-in for patchright.sync_api.sync_playwright; `sites` is a
    FakeSite (serves every URL) or FakeSites. The FakePlaywright is exposed
    as `.playwright` for assertions."""
    if isinstance(sites, FakeSite):
        single = sites
        sites = FakeSites({})
        sites.site_for = lambda url: single
    playwright = FakePlaywright(sites)

    def factory():
        return _Manager(playwright)

    factory.playwright = playwright
    return factory
