"""Scrapy-free OLX catalogue scraper.

Replaces the olx_scrapy Scrapy project with plain patchright (sync API):
one persistent browser context, one page, sequential navigation. No Twisted
reactor, no module aliasing, no per-scheme download handlers -- and therefore
no SingletonLock races on .playwright_profile.

Writes to the same SQLite table (catalog_data) and the same runs/ bronze
layout as the Scrapy version, so `pipeline.py upload` and the silver/gold
transforms are unaffected.
"""
