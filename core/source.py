"""The Source contract.

Copied VERBATIM from MULTI-SOURCE-REDESIGN.md, "The Source contract" section
(lines 89-136) / CONTRACT.md section 7 ("Use those definitions exactly.").
Do not edit the shapes below without updating both docs first.

`models.Tournament` refers to the existing top-level `models.py` (unchanged in
this phase; only in Phase 3 does USAU get split into sources/usau/).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import models


@dataclass(frozen=True)
class EventRef:
    """One scrapable event-division, as returned by discovery."""
    url: str
    name: str | None = None
    division: str | None = None            # raw source label, e.g. "Club - Men"
    city: str = ""
    state: str = ""
    country: str = ""
    start_date: date | None = None
    end_date: date | None = None
    extra: dict = field(default_factory=dict)   # source-private payload


# Logical page name -> raw bytes. USAU returns {"tournament": ..., "team:Truck Stop": ...};
# a source backed by an API can return {"event": <json bytes>}. Keeping it a dict lets
# core cache and invalidate each page independently.
FetchedPages = dict[str, bytes]


class Cache(Protocol):
    """core/cache.py — page cache under html/<source>/<year>/<event_key>/<page>.html"""

    def get(self, key: str) -> bytes | None: ...
    def put(self, key: str, content: bytes) -> None: ...
    def fetch(self, key: str, url: str, *, refresh: bool = False) -> bytes: ...


class Source(ABC):
    id: str                                        # "usau" | "wfdf"

    @abstractmethod
    def discover(self, year: int) -> list[EventRef]: ...

    @abstractmethod
    def event_key(self, ref: EventRef) -> str: ...      # stable per-source doc id

    @abstractmethod
    def fetch_event(self, ref: EventRef, cache: Cache) -> FetchedPages: ...

    @abstractmethod
    def parse_event(
        self, pages: FetchedPages, ref: EventRef, year: int
    ) -> models.Tournament | None: ...

    def make_transport(self):
        """Build the transport this source's pages are fetched through.

        Optional. The default is a plain, unthrottled `RequestsTransport`.
        Override to set request pacing, a different user agent, or an
        entirely different transport -- a source knows what its upstream can
        comfortably take, and core does not.
        """
        from core.fetch import RequestsTransport

        return RequestsTransport()
