"""What a running job's output.log says. The scraper only prints; these are
its line formats (run_patchright.py, olx_patchright/catalog.py), kept in one
place so a wording change breaks one regex, not the runner."""
import re
from dataclasses import dataclass, field

from olx_patchright.export import infer_region

_TOTAL = re.compile(r'^--- Scraping catalogue: (\d+) start URL')
_PAGE = re.compile(r'^Page (\d+) \((\S+)\): (\d+) new, (\d+) already seen\.$')
_NO_PAYLOAD = re.compile(r'^No ad payload at (\S+) \(HTTP (\d+)\)')
_PROBE = re.compile(r'^(estado-[a-z0-9/-]+): (-?\d+) ads')


@dataclass
class Progress:
    total_urls: int = None
    regions: list = field(default_factory=list)  # start URLs seen, in order
    current_url: str = None
    current_page: int = None
    new: int = 0
    duplicates: int = 0
    pages: int = 0
    blocks: list = field(default_factory=list)   # (url, http status)
    probed: int = 0
    last_line: str = None

    @property
    def url_index(self):
        return len(self.regions)


def parse(text):
    progress = Progress()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        progress.last_line = line
        m = _PAGE.match(line)
        if m:
            page, url, new, dup = int(m[1]), m[2], int(m[3]), int(m[4])
            region = infer_region(url)
            if not progress.regions or progress.regions[-1] != region:
                progress.regions.append(region)
            progress.current_url, progress.current_page = url, page
            progress.new += new
            progress.duplicates += dup
            progress.pages += 1
            continue
        m = _TOTAL.match(line)
        if m:
            progress.total_urls = int(m[1])
            continue
        m = _NO_PAYLOAD.match(line)
        if m:
            progress.blocks.append((m[1], int(m[2])))
            continue
        if _PROBE.match(line):
            progress.probed += 1
    return progress


def cloudflare_blocks(text):
    """403 'no ad payload' lines: Cloudflare stopped serving the profile."""
    return [(url, status) for url, status in parse(text).blocks if status == 403]
