from typing import Optional
from sqlalchemy import create_engine, Column, String
# from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Integer, String, DateTime, Float
from sqlalchemy.orm import sessionmaker, declarative_base, Mapped, mapped_column, DeclarativeBase, Session
from scrapy.utils.project import get_project_settings
import os
from sqlalchemy import Text


class Base(DeclarativeBase):
    pass

def db_connect():
    """
    Performs database connection using database settings from settings.py.
    Returns sqlaclchemy engine instance.
    """
    url = get_project_settings().get("CONNECTION_STRING")
    os.makedirs('scraped_data', exist_ok=True)
    return create_engine(url)

def create_table(engine):
    Base.metadata.create_all(engine, checkfirst=True)


class CatalogDataModel(Base):
    __tablename__ = "catalog_data"
    id = Column(Integer, primary_key=True)
    uid = Column(String(50))
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


class AdDataModel(Base):
    __tablename__ = "ad_data"
    id = Column(Integer, primary_key=True)
    date = Column(String(20))
    breadcrumb = Column(String(100))
    code = Column(String(20))
    description = Column(Text)
    full_location = Column(String(60))
    street_address = Column(String(60))
    title = Column(String(60))
    characteristics = Column(Text)
    details = Column(Text)
    pricing = Column(Text)
    url = Column(String(200))
    scraped_date = Column(DateTime)
    uploaded_to_cloud = Column(Integer)

    # Property attributes (from ad['properties'][name=...].value)
    real_estate_type = Column(String(100))
    condominio = Column(String(50))
    size = Column(String(20))
    rooms = Column(String(10))
    bathrooms = Column(String(10))
    garage_spaces = Column(String(10))

    # Structured location (from ad['location'].*)
    neighbourhood = Column(String(100))
    neighbourhood_id = Column(Integer)
    municipality = Column(String(100))
    municipality_id = Column(Integer)
    uf = Column(String(5))
    zipcode = Column(String(15))
    lat = Column(Float)
    lng = Column(Float)
    ddd = Column(String(5))
    zone = Column(String(50))
    zone_id = Column(Integer)
    region = Column(String(200))
