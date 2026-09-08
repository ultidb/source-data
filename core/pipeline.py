"""Shared discover -> fetch_event -> parse_event -> tournament_to_document
-> write_document -> post_documents pipeline for any `core.source.Source`.

Extracted out of `cli.py`'s `_scrape_year_with_source` so that app.py's
WFDF scheduler jobs (which need to run the same pipeline over a specific
event subset -- "just the ongoing ones" -- rather than a whole year) don't
duplicate the loop. MULTI-SOURCE-REDESIGN.md's "Pre-existing bugs" section
already flags cli.py/app.py duplication (`_commit_to_git`/`_post_to_receiver`
vs. app.py's versions) as an existing wart; this must not add another
instance of it. `cli.py` and `app.py` both call `run_pipeline` now.
"""
from __future__ import annotations

import logging as log
from pathlib import Path
from typing import List, Optional

from core.cache import FileCache
from core.emit import write_document
from core.ingest_client import post_documents
from core.schema import Document
from core.serialize import tournament_to_document
from core.source import EventRef, Source


def run_pipeline(
    src: Source,
    year: int,
    refs: Optional[List[EventRef]] = None,
    *,
    out_dir: Optional[Path] = None,
    post: bool = False,
    api_url: Optional[str] = None,
    ingest_token: Optional[str] = None,
) -> List[Document]:
    """Run `src` through discover -> fetch_event -> parse_event ->
    tournament_to_document -> write_document, optionally POSTing the
    resulting documents.

    `refs` lets a caller drive a specific event subset instead of `src`'s
    whole `discover(year)` result -- e.g. app.py's scheduler jobs, which
    scope WfdfSource to just the currently-ongoing (or upcoming) events by
    constructing it with an already-filtered `events=` list and still need
    the year to pass to `discover`/`FileCache`. Passing None (the default)
    reproduces the old cli.py behaviour of scraping everything `src`
    discovers for `year`.

    One transport is built via `src.make_transport()` and reused across
    every event in this call: request pacing (see core/fetch.py) is
    stateful, and a fresh transport per event would reset the throttle
    between events.

    A single event failing (network error, bad parse, etc.) is caught and
    logged rather than aborting the whole run -- callers that loop over
    many events (the scheduler in particular) need one bad tournament to
    not block the rest, or take down the scheduler thread.
    """
    if refs is None:
        refs = src.discover(year)
    log.info(f"pipeline: {len(refs)} event(s) for source={src.id} year={year}")

    transport = src.make_transport()

    documents: List[Document] = []
    for i, ref in enumerate(refs, start=1):
        try:
            key = src.event_key(ref)
            log.info(f"pipeline: scraping {i}/{len(refs)} {key} ({ref.url})")
            cache = FileCache(src.id, year, key, transport)
            pages = src.fetch_event(ref, cache)
            tournament = src.parse_event(pages, ref, year)
            if tournament is None:
                log.warning(f"pipeline: parse_event returned None for {ref.url!r}, skipping")
                continue

            doc = tournament_to_document(
                tournament, source=src.id, source_event_id=key, source_url=ref.url
            )
            path = write_document(doc, base_dir=out_dir)
            log.info(f"pipeline: wrote {path}")
            documents.append(doc)
        except Exception:
            log.exception(f"pipeline: failed processing {ref.url!r}, skipping")

    log.info(f"pipeline: produced {len(documents)} document(s) for source={src.id} year={year}")

    if post:
        if not api_url:
            raise ValueError("post=True requires api_url")
        result = post_documents(documents, source=src.id, api_url=api_url, token=ingest_token)
        log.info(f"pipeline: posted {len(documents)} document(s): {result}")

    return documents
