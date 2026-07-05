"""SQLite persistence for catalogue records.

Same database file and table (scraped_data/olx.sqlite :: catalog_data) as the
Scrapy version, so existing dedupe state, the ad-stage `url_is_scraped` queue,
and any downstream reads keep working unchanged.
"""
import json
import os

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'scraped_data', 'olx.sqlite')

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


def session_factory():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    engine = create_engine('sqlite:///' + DB_PATH)
    Base.metadata.create_all(engine, checkfirst=True)
    return sessionmaker(bind=engine)


def uid_exists(factory, uid):
    """Snapshot model (ADR 0006): one row per uid, uid is the sole key."""
    with factory() as session:
        return session.query(CatalogRow).filter_by(uid=uid).first() is not None


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
