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
from typing import List


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
    """
    year: int
    slug: str  # URL path segment, e.g. "wucc-2026"
    season_id: str  # e.g. "WUCC2026", matches the API's season_id
    division_label: str  # raw division prefix, e.g. "World Ultimate Club Championships"
    series: List[WfdfSeries] = field(default_factory=list)
    city: str = ""
    state: str = ""
    country: str = ""


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
    ),
]


def events_for_year(year: int) -> List[WfdfEvent]:
    """All hardcoded WFDF events for `year` (empty for any year other than
    one of the hardcoded entries above)."""
    return [e for e in WFDF_EVENTS if e.year == year]
