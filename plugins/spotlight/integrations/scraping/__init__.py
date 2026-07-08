"""Spotlight scraping seam — Crawl4AI-default, provider-agnostic fetch (U14).

Public surface skills import:
    from integrations.scraping import scrape, ScrapeResult, ScrapeError
"""
from .scrape import scrape
from .scrape_types import ScrapeError, ScrapeResult

__all__ = ["scrape", "ScrapeResult", "ScrapeError"]
