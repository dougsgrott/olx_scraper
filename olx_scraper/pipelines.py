import json
from datetime import datetime

from sqlalchemy.orm import sessionmaker
from scrapy.exceptions import DropItem

from .models import create_table, db_connect, CatalogDataModel, AdDataModel


def _columns(model):
    return [c.name for c in model.__table__.columns if c.name != 'id']


class BaseSavePipeline:
    def __init__(self):
        engine = db_connect()
        create_table(engine)
        self.factory = sessionmaker(bind=engine)

    def process_item(self, item, spider=None):
        raise NotImplementedError

    def process_entry(self, entry):
        # `with` guarantees the connection is returned to the pool, even on
        # error or when the caller's loop never runs.
        with self.factory() as session:
            try:
                session.add(entry)
                session.commit()
            except Exception:
                session.rollback()
                raise


class DuplicatesCatalogPipeline(BaseSavePipeline):
    def process_item(self, item, spider=None):
        with self.factory() as session:
            exist_title = session.query(CatalogDataModel).filter_by(uid=item["uid"]).first()
        if exist_title is not None:
            raise DropItem("Duplicate item found: {}".format(item["title"]))
        return item


class SaveCatalogDataPipeline(BaseSavePipeline):
    JSON_FIELDS = {'details', 'pricing', 'badges', 'characteristics'}

    def process_item(self, item, spider=None):
        entry = CatalogDataModel()
        for k in _columns(CatalogDataModel):
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
        for k in _columns(AdDataModel):
            val = item.get(k)
            if k in self.JSON_FIELDS and val is not None:
                val = json.dumps(val, ensure_ascii=False)
            setattr(entry, k, val)
        self.process_entry(entry)
        return item


class DefaultValuesCatalogPipeline:
    def process_item(self, item, spider=None):
        item.setdefault('scraped_date', datetime.now())
        item.setdefault('uploaded_to_cloud', 0)
        item.setdefault('url_is_scraped', 0)
        return item


class DefaultValuesAdPipeline:
    def process_item(self, item, spider=None):
        item.setdefault('scraped_date', datetime.now())
        item.setdefault('uploaded_to_cloud', 0)
        return item
