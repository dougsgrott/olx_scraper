"""Parse OLX catalogue pages into plain-dict listing records.

OLX serves its listing data as a Next.js App Router flight payload: escaped
JSON fragments streamed through `self.__next_f.push([n, "..."])` script calls.
We reconstruct that payload and pull out the object carrying the `ads` list;
its shape (`ads`, `totalOfAds`, `pageSize`, `pageIndex`, per-ad fields) matches
the old Pages Router `pageProps`, so the field mapping is unchanged from the
Scrapy-era spider.
"""
import json
import re
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

_PROMOTED_PROPERTY_NAMES = {
    'category', 'real_estate_type', 'condominio', 'iptu', 'size',
    'rooms', 'bathrooms', 'garage_spaces',
    're_features', 're_complex_features',
}
_BOOL_BADGE_FIELDS = (
    ('is_featured', 'isFeatured'),
    ('fixed_on_top', 'fixedOnTop'),
    ('price_reduction_badge', 'priceReductionBadge'),
    ('has_real_estate_highlight', 'hasRealEstateHighlight'),
)


def page_props(html):
    """Return the search props (ads + pagination metadata) or None."""
    payload = _flight_payload(html)
    if not payload:
        return None
    for m in re.finditer(r'\{"ads":', payload):
        raw = _balanced_object(payload, m.start())
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj.get('ads'), list):
            return obj
    return None


def _flight_payload(html):
    """Concatenate every `self.__next_f.push([n, "<chunk>"])` string literal
    (JSON-decoding each) into the full App Router flight payload."""
    chunks = re.findall(
        r'self\.__next_f\.push\(\[\d+,\s*("(?:[^"\\]|\\.)*")\s*\]\)',
        html, re.S,
    )
    out = []
    for c in chunks:
        try:
            out.append(json.loads(c))
        except ValueError:
            pass
    return ''.join(out)


def _balanced_object(s, start):
    """Return the JSON object substring starting at `s[start] == '{'`, matching
    braces while skipping string literals. None if unbalanced."""
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(s)):
        c = s[j]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return s[start:j + 1]
    return None


def build_record(ad):
    """Map one raw ad object to a catalog_data record (plain dict).
    Returns None for empty/promoted placeholder entries."""
    loc = ad.get('locationDetails') or {}
    properties = ad.get('properties') or []
    props_by_name = {p.get('name'): p for p in properties if p.get('name')}

    list_id = ad.get('listId')
    uid_code = '' if list_id is None else str(list_id)

    record = {}
    # listId is globally unique on OLX, so it doubles as both code and uid
    # (uid is the snapshot model's sole natural key, ADR 0006).
    record['uid'] = uid_code
    record['code'] = uid_code
    record['title'] = ad.get('subject') or ad.get('title') or ''
    record['url'] = ad.get('friendlyUrl') or ad.get('url') or ''

    # date: origListTime is unix seconds; render as ISO for human readability.
    ts = ad.get('origListTime') or ad.get('date')
    record['date'] = (
        datetime.fromtimestamp(ts).isoformat(timespec='seconds')
        if isinstance(ts, (int, float)) else (ts or '')
    )

    # location string (rendered) + structured components from locationDetails
    record['location'] = ad.get('location') or ''
    record['neighbourhood'] = loc.get('neighbourhood')
    record['municipality'] = loc.get('municipality')
    record['uf'] = loc.get('uf')
    record['ddd'] = loc.get('ddd')

    # Dict-valued fields (JSON-serialized on save)
    record['pricing'] = _pricing_from_ad(ad)
    record['characteristics'] = _characteristics_from_properties(properties)
    record['details'] = _details_from_properties(properties)
    record['badges'] = [name for name, key in _BOOL_BADGE_FIELDS if ad.get(key)]

    # Promoted property attributes
    for name in ('real_estate_type', 'condominio', 'iptu', 'size',
                 'rooms', 'bathrooms', 'garage_spaces'):
        record[name] = _prop(props_by_name, name)

    old_price = ad.get('oldPrice')
    record['old_price'] = old_price if old_price else None

    # Ad-level flags / metadata
    record['category_name'] = ad.get('categoryName') or ad.get('category')
    record['professional_ad'] = 1 if ad.get('professionalAd') else 0
    record['is_featured'] = 1 if ad.get('isFeatured') else 0
    record['fixed_on_top'] = 1 if ad.get('fixedOnTop') else 0
    record['price_reduction_badge'] = 1 if ad.get('priceReductionBadge') else 0
    record['has_real_estate_highlight'] = 1 if ad.get('hasRealEstateHighlight') else 0

    if record['uid'] == '' and record['title'] == '':
        return None
    return record


def _pricing_from_ad(ad):
    out = {}
    price = ad.get('priceValue') or ad.get('price')
    if price:
        out['price'] = price
    if ad.get('oldPrice'):
        out['old_price'] = ad.get('oldPrice')
    return out


def _prop(props_by_name, name):
    p = props_by_name.get(name)
    return p.get('value') if p else None


def _characteristics_from_properties(properties):
    """re_features + re_complex_features. Catalogue gives them as a single
    comma-separated string (no `values` list like ad-detail pages), so we
    always split on comma."""
    out = {}
    for p in properties:
        if p.get('name') in ('re_features', 're_complex_features'):
            label = p.get('label') or p.get('name')
            vals = p.get('values') or [
                v.strip() for v in (p.get('value') or '').split(',') if v.strip()
            ]
            out[label] = vals
    return out


def _details_from_properties(properties):
    return {p.get('label'): p.get('value')
            for p in properties
            if p.get('name') not in _PROMOTED_PROPERTY_NAMES
            and p.get('label')}


def next_page_url(url, props):
    """Next catalogue page (?o=N+1) from the page's pagination metadata, or
    None when this is the last (or only) page."""
    total_ads = props.get('totalOfAds')
    page_size = props.get('pageSize')
    current_page = props.get('pageIndex')

    if not (isinstance(total_ads, int) and isinstance(page_size, int) and page_size):
        return None

    total_pages = (total_ads + page_size - 1) // page_size
    if not isinstance(current_page, int) or current_page >= total_pages:
        return None

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query['o'] = [str(current_page + 1)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
