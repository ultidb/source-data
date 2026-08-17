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
    slug: str  # URL path segment, e.g. "wucc-2026"
    season_id: str  # e.g. "WUCC2026", matches the API's season_id
    division_label: str  # raw division prefix, e.g. "World Ultimate Club Championships"
    series: List[WfdfSeries] = field(default_factory=list)
    city: str = ""
    state: str = ""
    country: str = ""
    start_date: Optional[date] = None
    end_date: Optional[date] = None


WFDF_EVENTS: List[WfdfEvent] = [
    WfdfEvent(
        year=2026,
        slug="wucc-2026",
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
