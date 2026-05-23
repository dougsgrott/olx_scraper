from itemadapter import ItemAdapter
from sqlalchemy.orm import sessionmaker, Session
from models import create_table, db_connect, CatalogDataModel, AdDataModel
import json
from itemadapter import ItemAdapter
from datetime import datetime
import logging
import six
import scrapy
from scrapy.exceptions import DropItem
from scrapy.exporters import BaseItemExporter
import settings


def not_set(string):
    """Check if a string is None or ''.
    :returns: bool - True if the string is empty
    """
    if string is None:
        return True
    elif string == '':
        return True
    return False


class DuplicatesCatalogPipeline(object):
    def __init__(self):
        """
        Initializes database connection and sessionmaker
        Creates tables
        """
        engine = db_connect()
        create_table(engine)
        self.factory = sessionmaker(bind=engine)

    def process_item(self, item, spider=None):
        with self.factory() as session:
            exist_title = session.query(CatalogDataModel).filter_by(uid=item["uid"]).first()
        if exist_title is not None:
            settings.redundancy = settings.redundancy + 1
            settings.redundancy_streak = settings.redundancy_streak + 1
            raise DropItem("Duplicate item found: {}".format(item["title"]))
        return item


class BaseSavePipeline(object):
    def __init__(self):
        """
        Initializes database connection and sessionmaker
        Creates tables
        """
        engine = db_connect()
        create_table(engine)
        self.factory = sessionmaker(bind=engine)

    def process_item(self, item, spider=None):
        """
        Save real estate index in the database
        This method is called for every item pipeline component
        """
        raise NotImplementedError

    def process_entry(self, entry):
        # `with` guarantees the connection is returned to the pool, even on
        # error or when the caller's loop never runs.
        with self.factory() as session:
            try:
                session.add(entry)
                session.commit()
                settings.saved = settings.saved + 1
                settings.redundancy_streak = 0
            except:
                session.rollback()
                raise
        return None


class SaveCatalogDataPipeline(BaseSavePipeline):
    JSON_FIELDS = {'details', 'pricing', 'badges', 'characteristics'}

    def process_item(self, item, spider=None):
        entry = CatalogDataModel()
        fields = [
            'uid', 'title', 'location', 'date', 'code',
            'scraped_date', 'url', 'url_is_scraped', 'url_scraped_date', 'uploaded_to_cloud',
            'details', 'pricing', 'badges',
            # Promoted from ads[i].properties:
            'real_estate_type', 'condominio', 'iptu', 'size',
            'rooms', 'bathrooms', 'garage_spaces', 'characteristics',
            # Pricing extras:
            'old_price',
            # Structured location:
            'neighbourhood', 'municipality', 'uf', 'ddd',
            # Ad-level flags / metadata:
            'category_name', 'professional_ad', 'is_featured', 'fixed_on_top',
            'price_reduction_badge', 'has_real_estate_highlight',
        ]
        for k in fields:
            val = item.get(k)
            if k in self.JSON_FIELDS and val is not None:
                val = json.dumps(val, ensure_ascii=False)
            setattr(entry, k, val)
        self.process_entry(entry)
        return item


class SaveAdDataPipeline(BaseSavePipeline):
    JSON_FIELDS = {'characteristics', 'details', 'pricing'}

    def process_item(self, item, spider=None):
        entry = AdDataModel()
        fields = [
            'date', 'breadcrumb', 'code', 'description',
            'full_location', 'street_address', 'title',
            'scraped_date', 'url', 'uploaded_to_cloud',
            'characteristics', 'details', 'pricing',
            # Promoted from ad['properties']:
            'real_estate_type', 'condominio', 'size',
            'rooms', 'bathrooms', 'garage_spaces',
            # Structured location:
            'neighbourhood', 'neighbourhood_id',
            'municipality', 'municipality_id',
            'uf', 'zipcode', 'lat', 'lng',
            'ddd', 'zone', 'zone_id', 'region',
        ]
        for k in fields:
            val = item.get(k)
            if k in self.JSON_FIELDS and val is not None:
                val = json.dumps(val, ensure_ascii=False)
            setattr(entry, k, val)
        self.process_entry(entry)
        return item


class UpdateCatalogDatabasePipeline(object):
    def __init__(self):
        engine = db_connect()
        create_table(engine)
        self.factory = sessionmaker(bind=engine)

    def process_item(self, item, spider=None):
        with self.factory() as session:
            scraped_row = session.query(CatalogDataModel).filter_by(code=item["code"]).first()
            if scraped_row is not None:
                scraped_row.url_is_scraped = 1
                scraped_row.url_scraped_date = datetime.now()
                session.commit()
        return item


class DefaultValuesCatalogPipeline(object):

    _NEW_NULLABLE_FIELDS = (
        'real_estate_type', 'condominio', 'iptu', 'size',
        'rooms', 'bathrooms', 'garage_spaces',
        'old_price',
        'neighbourhood', 'municipality', 'uf', 'ddd',
        'category_name', 'professional_ad', 'is_featured', 'fixed_on_top',
        'price_reduction_badge', 'has_real_estate_highlight',
    )

    def process_item(self, item, spider=None):
        item.setdefault('badges', [])
        item.setdefault('characteristics', {})
        item.setdefault('code', '')
        item.setdefault('date', '')
        item.setdefault('details', {})
        item.setdefault('location', '')
        item.setdefault('pricing', {})
        item.setdefault('scraped_date', datetime.now())
        item.setdefault('title', '')
        item.setdefault('uploaded_to_cloud', 0)
        item.setdefault('url_is_scraped', 0)
        item.setdefault('url_scraped_date', None)
        # New JSON-sourced scalar fields default to None (SQLite NULL).
        for k in self._NEW_NULLABLE_FIELDS:
            item.setdefault(k, None)
        return item


class DefaultValuesAdPipeline(object):

    _NEW_NULLABLE_FIELDS = (
        'real_estate_type', 'condominio', 'size', 'rooms', 'bathrooms', 'garage_spaces',
        'neighbourhood', 'neighbourhood_id', 'municipality', 'municipality_id',
        'uf', 'zipcode', 'lat', 'lng', 'ddd', 'zone', 'zone_id', 'region',
    )

    def process_item(self, item, spider=None):
        item.setdefault('breadcrumb', '')
        item.setdefault('characteristics', {})
        item.setdefault('code', '')
        item.setdefault('date', '')
        item.setdefault('description', '')
        item.setdefault('details', {})
        item.setdefault('full_location', '')
        item.setdefault('pricing', {})
        item.setdefault('scraped_date', datetime.now())
        item.setdefault('street_address', '')
        item.setdefault('title', '')
        item.setdefault('uploaded_to_cloud', 0)
        # New JSON-sourced scalar fields. Default to None so missing values
        # land as SQLite NULL instead of empty string/zero.
        for k in self._NEW_NULLABLE_FIELDS:
            item.setdefault(k, None)
        return item
