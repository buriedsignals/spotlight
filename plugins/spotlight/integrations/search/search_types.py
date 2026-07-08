"""Shared types for the search seam."""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class SearchHit:
    """One search result. Kept deliberately small — skills read title/url/snippet."""

    url: str
    title: str = ""
    snippet: str = ""
    date: str | None = None
    engine: str | None = None


class SearchError(Exception):
    """Raised when a search provider cannot return results (unreachable, bad response, no backend)."""
