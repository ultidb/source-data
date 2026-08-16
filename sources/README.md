# Writing a source

A source is a plugin that turns "some event on the internet (or wherever)"
into a populated `models.Tournament`. Everything downstream of that --
serialization to the wire format, on-disk archiving, POSTing to the ingest
API, schema validation, and run tracking -- is handled by `core/` and the
CLI. A source never touches JSON, file paths, or the API.

See `MULTI-SOURCE-REDESIGN.md` ("The Source contract") and `CONTRACT.md`
section 7 for the frozen shapes referenced below. `sources/example/` is a
complete, working, fixture-backed implementation you can copy wholesale as
a starting point -- copy the directory, rename the class and `id`, and
replace the fixture-reading guts of `fetch_event`/`parse_event` with real
network calls and real parsing.

## The four methods

Implement `core.source.Source` (an ABC with these four abstract methods)
and set a class attribute `id: str` (e.g. `"usau"`, `"wfdf"`):

```python
class Source(ABC):
    id: str

    def discover(self, year: int) -> list[EventRef]: ...
    def event_key(self, ref: EventRef) -> str: ...
    def fetch_event(self, ref: EventRef, cache: Cache) -> FetchedPages: ...
    def parse_event(self, pages: FetchedPages, ref: EventRef, year: int) -> models.Tournament | None: ...
```

- **`discover(year)`** finds every scrapable event-division for a season
  and returns them as `EventRef`s. This is whatever "list tournaments for
  2026" means for your source -- crawling a calendar page, paging an API,
  reading a directory of fixtures.

- **`event_key(ref)`** returns a short, stable, filesystem- and
  URL-safe string that identifies this event-division *within your
  source* (not globally -- `core` combines it with your source id).
  It becomes part of the on-disk path (`data/<source>/<year>/<key>.json`,
  `html/<source>/<year>/<key>/...`) and the wire format's
  `sourceEventId`, so it must be stable across re-scrapes of the same
  event and must not collide across different events. This is the one
  thing that was most USAU-shaped before this refactor -- `scrape.py` used
  to hard-index USAU's URL structure to derive it; your source owns this
  logic completely instead.

- **`fetch_event(ref, cache)`** fetches the raw bytes for an event and
  returns them as a `FetchedPages` dict (`{"logical page name": bytes,
  ...}`) -- e.g. `{"tournament": <html>, "team:Truck Stop": <html>}` for an
  HTML-scraped source, or `{"event": <json bytes>}` for an API-backed one.
  Keeping it a dict of named pages (rather than one blob) lets `core`
  cache and invalidate each page independently. You decide what's
  cacheable and how: call `cache.fetch(key, url)` per page for a plain
  network fetch-with-caching, or bypass the cache entirely if there's
  nothing to cache (as `sources/example/` does, since its "pages" are
  already local files).

- **`parse_event(pages, ref, year)`** turns fetched bytes into a
  `models.Tournament` (teams, rosters, stages, games) -- the same domain
  model the rest of the scraper has always used. Return `None` if the
  event turned out to have nothing worth recording (e.g. no games
  scheduled yet). This is where all of your source's parsing logic lives;
  nothing here is shared with other sources except the four helpers in
  `core/parsing.py` (`extract_nickname`, `convertNonDigitScore`,
  `parseCoaches`, `parse_seeded_name`) if they happen to apply.

## What `core` handles for you

Once you have a `models.Tournament`, you never build JSON or touch a
filepath yourself:

| Concern | Module | You call |
|---|---|---|
| Domain model -> wire format | `core/serialize.py` | `tournament_to_document(tournament, source=..., source_event_id=..., source_url=...)` |
| Wire format validation | `core/schema.py` | happens automatically when a `Document` is constructed (pydantic) |
| On-disk archive | `core/emit.py` | `write_document(doc)` -> `data/<source>/<year>/<key>.json` |
| Page cache | `core/cache.py` | `FileCache(source, year, event_key, transport)` implements the `Cache` protocol your `fetch_event` receives |
| HTTP transport | `core/fetch.py` | `RequestsTransport()` for a plain-`requests` source; write your own callable (`fetch(url) -> bytes`) if you need something else |
| POST to the ingest API | `core/ingest_client.py` | `post_documents(documents, source=..., api_url=..., token=...)` |
| Run tracking (`EtlRun`/`EtlRunItem`) | server-side (Go) | nothing -- this happens entirely on the API side once your documents are POSTed |

The CLI (`cli.py`'s `scrape --source=<id> year <year>`) drives the whole
pipeline for any registered source: `discover` -> for each ref,
`fetch_event` -> `parse_event` -> `tournament_to_document` -> `write_document`
-> optionally `post_documents`.

## `EventRef.extra`: the escape hatch

```python
@dataclass(frozen=True)
class EventRef:
    url: str
    name: str | None = None
    division: str | None = None
    city: str = ""
    state: str = ""
    country: str = ""
    start_date: date | None = None
    end_date: date | None = None
    extra: dict = field(default_factory=dict)   # source-private payload
```

The named fields on `EventRef` are the ones `core` and the CLI need to know
about generically (mainly for logging and for populating `event.city` /
`event.state` / dates before `parse_event` runs). Anything else your
source needs to carry from `discover()` through to `fetch_event()` and
`parse_event()` -- a numeric event id, a season id, an auth token scoped to
this event, whatever -- goes in `extra`. It's a plain dict; only your
source reads or writes it, so there's no schema to keep in sync. See
`sources/example/source.py`'s `discover()` for a working example
(`extra={"fixture_path": ..., "year": ...}`), used later by both
`event_key()` and `fetch_event()`.

## Registering your source

Add one import + `register()` call to `sources/__init__.py`:

```python
from sources.wfdf.source import WfdfSource
register(WfdfSource())
```

That's the entire integration point -- `scraper sources`, `scraper
scrape --source=wfdf year 2026`, and `scraper post-documents` all work
against whatever `core.registry.list_sources()` returns, with no further
wiring.

## Note on USAU

`sources/usau/` doesn't exist yet. USAU still runs on the pre-refactor
`parse.py` + `scrape.py` path (see `cli.py`'s `scrape year --source=usau`,
which explicitly keeps using that code unchanged) until Phase 3 of
`MULTI-SOURCE-REDESIGN.md` ports it onto this contract.
