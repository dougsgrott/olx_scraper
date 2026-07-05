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
    """Snapshot model (ADR 0006): one row per uid. Drop the item if its uid is
    already stored; otherwise let it through to be saved. No versioning, no
    fingerprint -- uid is the sole key."""

    def process_item(self, item, spider=None):
        with self.factory() as session:
            exists = (session.query(CatalogDataModel)
                      .filter_by(uid=item['uid'])
                      .first())

        if exists is not None:
            raise DropItem(f"Duplicate uid: {item['uid']}")
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


class MarkScrapedPipeline(BaseSavePipeline):
    """After a successful ad-detail save, flip every catalog_data row matching
    this ad's `code` (== OLX listId) to `url_is_scraped = 1` so AdSpider's
    `url_is_scraped == 0` filter excludes it on the next run.

    Updates all snapshots for the uid (the change-detection pipeline can leave
    multiple rows per ad); this is idempotent and self-healing.
    """

    def process_item(self, item, spider=None):
        code = item.get('code')
        if not code:
            return item
        with self.factory() as session:
            (session.query(CatalogDataModel)
                .filter(CatalogDataModel.code == code)
                .update({
                    CatalogDataModel.url_is_scraped: 1,
                    CatalogDataModel.url_scraped_date: datetime.now(),
                }, synchronize_session=False))
            session.commit()
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
