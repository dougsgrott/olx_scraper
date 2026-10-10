"""Page registry. Each page module exposes PAGE; the nav is built from the
registry, so a new page (e.g. the future cloud-ingestion one) is a module
plus one entry in PAGE_MODULES."""
import importlib
from dataclasses import dataclass

from fastapi import APIRouter

PAGE_MODULES = ['coverage', 'runs']


@dataclass
class Page:
    slug: str
    title: str
    router: APIRouter
    nav_order: int


def load_pages():
    pages = [importlib.import_module(f'olx_panel.web.{name}').PAGE
             for name in PAGE_MODULES]
    return sorted(pages, key=lambda p: p.nav_order)
