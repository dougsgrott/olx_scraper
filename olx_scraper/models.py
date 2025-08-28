from typing import Optional
from sqlalchemy import create_engine, Column, String
# from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Integer, String, DateTime
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


class CatalogInfoModel(Base):
    __tablename__ = "catalog_info"
    id = Column(Integer, primary_key=True)
    uid = Column(String(50))
    title = Column(String(200))
    location = Column(String(50))
    date = Column(String(50))
    code = Column(String(50))

    scraped_date = Column(DateTime)
    url = Column(String(200))
    url_is_scraped = Column(Integer)
    url_scraped_date = Column(DateTime)
    uploaded_to_cloud = Column(Integer)


class CatalogDetailsModel(Base):
    __tablename__ = "catalog_details"
    id = Column(Integer, primary_key=True)
    uid = Column(String(50))
    key = Column(String(20))
    value = Column(String(30))
    uploaded_to_cloud = Column(Integer)


class CatalogPricingModel(Base):
    __tablename__ = "catalog_pricing"
    id = Column(Integer, primary_key=True)
    uid = Column(String(50))
    key = Column(String(20))
    value = Column(String(30))
    uploaded_to_cloud = Column(Integer)


class CatalogBagdesModel(Base):
    __tablename__ = "catalog_badges"
    id = Column(Integer, primary_key=True)
    uid = Column(String(50))
    key = Column(String(20))
    uploaded_to_cloud = Column(Integer)


class AdInfoModel(Base):
    __tablename__ = "ad_info"
    id = Column(Integer, primary_key=True)
    date = Column(String(20))
    breadcrumb = Column(String(100))
    code = Column(String(20))
    description = Column(Text)
    full_location = Column(String(60))
    street_address = Column(String(60))
    title = Column(String(60))
    url = Column(String(200))
    scraped_date = Column(DateTime)
    uploaded_to_cloud = Column(Integer)


class AdCharacteristicsModel(Base):
    __tablename__ = "ad_characteristics"
    id = Column(Integer, primary_key=True)
    code = Column(String(50))
    key = Column(String(50))
    value = Column(String(50))
    uploaded_to_cloud = Column(Integer)


class AdDetailsModel(Base):
    __tablename__ = "ad_details"
    id = Column(Integer, primary_key=True)
    code = Column(String(50))
    key = Column(String(50))
    value = Column(String(50))
    uploaded_to_cloud = Column(Integer)


class AdPricingModel(Base):
    __tablename__ = "ad_pricing"
    id = Column(Integer, primary_key=True)
    code = Column(String(50))
    key = Column(String(50))
    value = Column(String(50))
    uploaded_to_cloud = Column(Integer)
