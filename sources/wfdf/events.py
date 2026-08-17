"""Hardcoded WFDF event list.

MVP scope is WUCC 2026 only (see the WFDF source task / MULTI-SOURCE-REDESIGN.md
Phase 2): "WFDF runs few enough tournaments that a hardcoded list is correct."
No calendar scraping.

Each entry is one WFDF "season" (WFDF's own term for a tournament instance,
e.g. season_id "WUCC2026") plus the series (gender divisions) that season
carries. The series id/name pairs are hardcoded here too, rather than
discovered from the `_reference` endpoint, so that `WfdfSource.discover()`
stays pure and network-free -- WFDF's static-JSON API (results.wfdf.sport)
has no calendar/index endpoint to crawl, and a WUCC's set of series doesn't
change once the event is set up.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional


@dataclass(frozen=True)
class WfdfSeries:
    """One gender division within a WFDF season, as named by the `series`
    array in `<season_id>_reference.json`."""
    series_id: int
    name: str  # WFDF's series name, e.g. "Mixed" | "Open" | "Women's"


@dataclass(frozen=True)
class WfdfEvent:
    """One WFDF season/event.

    `base_url` is everything up to but not including the data path -- WFDF
    does not put every event under one host with the event as a path
    segment. Two shapes exist:

      - path-style (WUCC 2026): one host, event as a path segment, e.g.
        "https://results.wfdf.sport/wucc-2026"
      - subdomain-style (WJUC 2026): the event's own subdomain, event at the
        host root, e.g. "https://wjuc.wfdf.sport"

    A trailing slash is normalised away in `__post_init__` so both
    "https://host/event" and "https://host/event/" behave identically.
    `data_path` (below) is joined onto this to build page URLs; see
    `WfdfSource._build_url`.

    `data_path` is the path segment between `base_url` and the
    season_id-prefixed filename, e.g. "live/data" in
    ".../wucc-2026/live/data/WUCC2026_reference.json". WUCC 2026 uses
    "live/data"; that layout has NOT been verified for WJUC or any other
    subdomain-style event (this codebase is offline w.r.t. WFDF's API), so
    `data_path` is per-event and overridable rather than assumed to be a
    global constant.

    `city`/`state`/`country` are deliberately blank and overridable here --
    the WFDF API carries no venue information at all (`reservations[].location`
    is always null, and `season` has no location fields). Do not invent a
    venue; fill these in by hand later if/when the information is known.

    `start_date`/`end_date` are hardcoded here (not fetched from the
    `_reference` endpoint) so that `ongoing_events`/`upcoming_events`/
    `recently_ended_events` below stay network-free, matching `discover()`.
    They're informational for scheduling only -- the authoritative dates
    that end up on the wire document still come from `season.starttime`/
    `endtime` in the live `_reference` payload (see `WfdfSource.parse_event`).
    """
    year: int
    base_url: str  # e.g. "https://results.wfdf.sport/wucc-2026" or "https://wjuc.wfdf.sport"
    season_id: str  # e.g. "WUCC2026", matches the API's season_id
    division_label: str  # raw division prefix, e.g. "World Ultimate Club Championships"
    series: List[WfdfSeries] = field(default_factory=list)
    data_path: str = "live/data"  # relative path between base_url and "<season_id>_<resource>.json"
    city: str = ""
    state: str = ""
    country: str = ""
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    def __post_init__(self) -> None:
        # Frozen dataclass -- object.__setattr__ is the documented escape
        # hatch for normalizing a field in __post_init__.
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))


WFDF_EVENTS: List[WfdfEvent] = [
    WfdfEvent(
        year=2026,
        base_url="https://results.wfdf.sport/wucc-2026",
        season_id="WUCC2026",
        division_label="World Ultimate Club Championships",
        series=[
            WfdfSeries(series_id=1001, name="Mixed"),
            WfdfSeries(series_id=1002, name="Open"),
            WfdfSeries(series_id=1000, name="Women's"),
        ],
        start_date=date(2026, 8, 15),
        end_date=date(2026, 8, 22),
    ),
    # WJUC 2026 -- subdomain-style base_url, event at the host root (no path
    # segment). NOT added live: season_id and series ids are only known from
    # WJUC's own `<season_id>_reference.json`, and this codebase is offline
    # w.r.t. WFDF's API, so they can't be verified here -- do not guess them.
    # Uncomment and fill in the real values (marked below) once fetched.
    #
    # Also note for whoever adds this: WJUC is a national-teams event, not a
    # club one, so its division_label must NOT map to Division.Club the way
    # WUCC's does (see _go_division-style mapping in the Go writer / ingest
    # contract), and it must not pick up the USAU team-matching fallback --
    # the Go writer's `fallbackTeamSources` already restricts that to
    # WUCC/WMUCC, which is correct and should stay that way. Pick a
    # division_label that resolves to something other than "Other" (an
    # ingest error, not a silent skip -- see
    # ultidb/docs/ingest-contract.md §4), e.g. one containing "national team"
    # / "international" / "world" so it maps to International, not Club.
    #
    # WfdfEvent(
    #     year=2026,
    #     base_url="https://wjuc.wfdf.sport",
    #     season_id="TODO_FROM_REFERENCE_JSON",  # e.g. "WJUC2026" -- verify against the real payload
    #     division_label="World Junior Ultimate Championships",  # must not resolve to Division.Club
    #     series=[
    #         # TODO: real series_id/name pairs from WJUC's own
    #         # <season_id>_reference.json -- do not guess these.
    #         WfdfSeries(series_id=0, name="TODO"),
    #     ],
    #     start_date=date(2026, 1, 1),  # TODO: real dates
    #     end_date=date(2026, 1, 1),  # TODO: real dates
    # ),
]


def events_for_year(year: int) -> List[WfdfEvent]:
    """All hardcoded WFDF events for `year` (empty for any year other than
    one of the hardcoded entries above)."""
    return [e for e in WFDF_EVENTS if e.year == year]


def _resolve(events: Optional[List[WfdfEvent]]) -> List[WfdfEvent]:
    return events if events is not None else WFDF_EVENTS


def ongoing_events(
    events: Optional[List[WfdfEvent]] = None, today: Optional[date] = None
) -> List[WfdfEvent]:
    """Events currently running: `start_date <= today <= end_date`,
    inclusive on both ends. `today` is injectable so tests don't depend on
    the real date; `events` likewise (defaults to the hardcoded
    WFDF_EVENTS), mirroring `WfdfSource`'s own events override."""
    today = today if today is not None else date.today()
    return [
        e
        for e in _resolve(events)
        if e.start_date is not None and e.end_date is not None and e.start_date <= today <= e.end_date
    ]


def upcoming_events(
    events: Optional[List[WfdfEvent]] = None,
    today: Optional[date] = None,
    within_days: int = 10,
) -> List[WfdfEvent]:
    """Events starting in the next `within_days` days but not yet started:
    `today < start_date <= today + within_days`. An event starting today is
    "ongoing", not "upcoming" -- these are non-overlapping."""
    today = today if today is not None else date.today()
    horizon = today + timedelta(days=within_days)
    return [
        e for e in _resolve(events) if e.start_date is not None and today < e.start_date <= horizon
    ]


def recently_ended_events(
    events: Optional[List[WfdfEvent]] = None,
    today: Optional[date] = None,
    within_days: int = 3,
) -> List[WfdfEvent]:
    """Events that ended in the last `within_days` days but not today:
    `today - within_days <= end_date < today`. An event ending today is
    still "ongoing", not "recently ended" -- these are non-overlapping."""
    today = today if today is not None else date.today()
    floor = today - timedelta(days=within_days)
    return [e for e in _resolve(events) if e.end_date is not None and floor <= e.end_date < today]
