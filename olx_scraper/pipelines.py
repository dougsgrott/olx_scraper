from itemadapter import ItemAdapter
from sqlalchemy.orm import sessionmaker, Session
# from models import CatalogModel, PropertyModel, HtmlCatalogModel, HtmlPropertyModel, BasicInfoModel, DetailsModel, create_table, db_connect
from models import create_table, db_connect
from models import CatalogInfoModel, CatalogDetailsModel, CatalogPricingModel, CatalogBagdesModel
from models import AdInfoModel, AdCharacteristicsModel, AdDetailsModel, AdPricingModel
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

    def process_item(self, item, spider):
        session = self.factory()
        exist_title = session.query(CatalogInfoModel).filter_by(uid=item["uid"]).first()
        if (exist_title is not None):
            settings.redundancy = settings.redundancy + 1
            settings.redundancy_streak = settings.redundancy_streak + 1
            raise DropItem("Duplicate item found: {}".format(item["title"]))
        else:
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

    def process_item(self, item, spider):
        """
        Save real estate index in the database
        This method is called for every item pipeline component
        """
        raise NotImplementedError

    def process_entry(self, entry, session):
        try:
            print('Entry added')
            session.add(entry)
            session.commit()
            settings.saved = settings.saved + 1
            settings.redundancy_streak = 0
        except:
            print('rollback')
            session.rollback()
            raise
        finally:
            session.close()
        return None


class SaveCatalogInfoPipeline(BaseSavePipeline):

    def process_item(self, item, spider):
        session = self.factory()
        entry = CatalogInfoModel()
        fields = ["uid", "title", "location", "date", "code",
                  "scraped_date", "url", "url_is_scraped", "url_scraped_date", "uploaded_to_cloud"]
        for k in fields:
            setattr(entry, k, item[k])
        self.process_entry(entry, session)
        return item


class SaveCatalogDetailsPipeline(BaseSavePipeline):

    def process_item(self, item, spider):
        session = self.factory()
        for k, v in item['details'].items():
            entry = CatalogDetailsModel()
            entry.uid = item['uid']
            entry.key = k
            entry.value = v
            self.process_entry(entry, session)
        return item


class SaveCatalogPricingPipeline(BaseSavePipeline):

    def process_item(self, item, spider):
        session = self.factory()
        for k, v in item['pricing'].items():
            entry = CatalogPricingModel()
            entry.uid = item['uid']
            entry.key = k
            entry.value = v
            self.process_entry(entry, session)
        return item


class SaveCatalogBadgesPipeline(BaseSavePipeline):

    def process_item(self, item, spider):
        session = self.factory()
        for k in item['badges']:
            entry = CatalogBagdesModel()
            entry.uid = item['uid']
            entry.key = k
            self.process_entry(entry, session)
        return item


class SaveAdInfoPipeline(BaseSavePipeline):

    def process_item(self, item, spider):
        session = self.factory()
        entry = AdInfoModel()
        fields = ['date', 'breadcrumb', 'code', 'description', 'full_location', 'street_address', 'title',
                  "scraped_date", "url", "uploaded_to_cloud"]
        for k in fields:
            setattr(entry, k, item[k])
        self.process_entry(entry, session)
        return item


class SaveAdCharacteristicsPipeline(BaseSavePipeline):

    def process_item(self, item, spider):
        session = self.factory()
        for k, v in item['characteristics'].items():
            entry = AdCharacteristicsModel()
            entry.code = item['code']
            entry.key = k
            entry.value = v
            self.process_entry(entry, session)
        return item


class SaveAdDetailsPipeline(BaseSavePipeline):

    def process_item(self, item, spider):
        session = self.factory()
        for k, v in item['details'].items():
            entry = AdDetailsModel()
            entry.code = item['code']
            entry.key = k
            entry.value = v
            self.process_entry(entry, session)
        return item


class SaveAdPricingPipeline(BaseSavePipeline):

    def process_item(self, item, spider):
        session = self.factory()
        for k, v in item['pricing'].items():
            entry = AdPricingModel()
            entry.code = item['code']
            entry.key = k
            entry.value = v
            self.process_entry(entry, session)
        return item


class UpdateCatalogDatabasePipeline(object):
    def __init__(self):
        engine = db_connect()
        create_table(engine)
        self.factory = sessionmaker(bind=engine)

    def process_item(self, item, spider):
        session = self.factory()
        # catalog = ImoveisSCCatalog()
        # catalog.title = item["title"]

        scraped_row = session.query(CatalogInfoModel).filter_by(code=item["code"]).first() # url=item["url"]
        scraped_row.url_is_scraped = 1
        scraped_row.url_scraped_date = datetime.now()

        session.commit()
        session.close()


class DefaultValuesCatalogPipeline(object):

    def process_item(self, item, spider):
        item.setdefault('badges', [])
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
        return item


class DefaultValuesAdPipeline(object):

    def process_item(self, item, spider):
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
        return item
