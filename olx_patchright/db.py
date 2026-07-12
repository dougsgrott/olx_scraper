"""SQLite persistence for catalogue records and the location tree.

Same database file and table (scraped_data/olx.sqlite :: catalog_data) as the
Scrapy version, so existing dedupe state, the ad-stage `url_is_scraped` queue,
and any downstream reads keep working unchanged.

The harvested location tree lives in its own file
(scraped_data/locations.sqlite :: locations): olx.sqlite is the dataset,
restorable from S3 bronze, while locations is crawler planning state with no
bronze representation — separate files keep the DR story clean and avoid
single-writer lock contention between a scrape and a location probe.
"""
import json
import os
from datetime import datetime

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'scraped_data', 'olx.sqlite')
LOCATIONS_DB_PATH = os.path.join(ROOT, 'scraped_data', 'locations.sqlite')

# Dict/list-valued record fields, stored as JSON text columns.
JSON_FIELDS = {'details', 'pricing', 'badges', 'characteristics'}


class Base(DeclarativeBase):
    pass


class CatalogRow(Base):
    __tablename__ = 'catalog_data'
    id = Column(Integer, primary_key=True)
    uid = Column(String(50), index=True)
    title = Column(String(200))
    location = Column(String(50))
    date = Column(String(50))
    code = Column(String(50))
    details = Column(Text)
    pricing = Column(Text)
    badges = Column(Text)

    scraped_date = Column(DateTime)
    url = Column(String(200))
    url_is_scraped = Column(Integer)
    url_scraped_date = Column(DateTime)
    uploaded_to_cloud = Column(Integer)

    # Property attributes (from ads[i].properties[name=...].value)
    real_estate_type = Column(String(100))
    condominio = Column(String(50))
    iptu = Column(String(50))
    size = Column(String(20))
    rooms = Column(String(10))
    bathrooms = Column(String(10))
    garage_spaces = Column(String(10))
    characteristics = Column(Text)

    # Pricing extras
    old_price = Column(String(50))

    # Structured location (from ads[i].locationDetails.*)
    neighbourhood = Column(String(100))
    municipality = Column(String(100))
    uf = Column(String(5))
    ddd = Column(String(5))

    # Ad-level flags / metadata
    category_name = Column(String(100))
    professional_ad = Column(Integer)
    is_featured = Column(Integer)
    fixed_on_top = Column(Integer)
    price_reduction_badge = Column(Integer)
    has_real_estate_highlight = Column(Integer)


class LocationBase(DeclarativeBase):
    """Separate metadata so catalogue tables and the location tree are created
    each in their own database file."""


class LocationRow(LocationBase):
    """One node of OLX's official location tree, harvested from its per-state
    sitemaps (scripts/discover_locations.py). `path` is the URL suffix that
    scopes a catalog page (prepend e.g. /imoveis/aluguel/ to get a start URL);
    depth 1 = state, then macro-region / sub-region / city-or-neighbourhood.
    total_of_ads/probed_at are reserved for the density prober."""
    __tablename__ = 'locations'
    path = Column(String(200), primary_key=True)
    uf = Column(String(5), index=True)
    parent_path = Column(String(200))
    slug = Column(String(100))
    depth = Column(Integer)
    discovered_at = Column(DateTime)
    total_of_ads = Column(Integer)
    probed_at = Column(DateTime)


def session_factory():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    engine = create_engine('sqlite:///' + DB_PATH)
    Base.metadata.create_all(engine, checkfirst=True)
    return sessionmaker(bind=engine)


def location_session_factory():
    os.makedirs(os.path.dirname(LOCATIONS_DB_PATH), exist_ok=True)
    engine = create_engine('sqlite:///' + LOCATIONS_DB_PATH)
    LocationBase.metadata.create_all(engine, checkfirst=True)
    return sessionmaker(bind=engine)


def uid_exists(factory, uid):
    """Snapshot model (ADR 0006): one row per uid, uid is the sole key."""
    with factory() as session:
        return session.query(CatalogRow).filter_by(uid=uid).first() is not None


def upsert_locations(factory, uf, paths):
    """Insert location paths not yet in the table (existing rows keep their
    discovered_at and prober columns). Returns the number of new rows."""
    now = datetime.now()
    with factory() as session:
        existing = {p for (p,) in session.query(LocationRow.path).filter_by(uf=uf)}
        new_paths = sorted(set(paths) - existing)
        for path in new_paths:
            segments = path.split('/')
            session.add(LocationRow(
                path=path,
                uf=uf,
                parent_path='/'.join(segments[:-1]) or None,
                slug=segments[-1],
                depth=len(segments),
                discovered_at=now,
            ))
        session.commit()
    return len(new_paths)


def record_probe(factory, path, total_of_ads):
    """Store a density-probe result. A None result clears any previous count
    (so a stale/bogus value can't linger) and leaves the node unprobed, to be
    retried on the next prober run."""
    values = (
        {'total_of_ads': total_of_ads, 'probed_at': datetime.now()}
        if total_of_ads is not None
        else {'total_of_ads': None, 'probed_at': None}
    )
    with factory() as session:
        session.query(LocationRow).filter_by(path=path).update(values)
        session.commit()


def insert_record(factory, record):
    row = CatalogRow()
    for col in CatalogRow.__table__.columns:
        if col.name == 'id':
            continue
        val = record.get(col.name)
        if col.name in JSON_FIELDS and val is not None:
            val = json.dumps(val, ensure_ascii=False)
        setattr(row, col.name, val)
    with factory() as session:
        try:
            session.add(row)
            session.commit()
        except Exception:
            session.rollback()
            raise
