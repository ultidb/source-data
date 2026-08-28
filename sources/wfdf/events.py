"""WFDF event list, loaded from `events.yaml`.

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

The event data itself lives in `events.yaml` (YAML rather than JSON so the
hard-won warnings below -- the year being load-bearing in `name`, "Women's"
not lowercasing to "womens", WJUC needing `international-u20` (not `club`,
and not plain `international` either, since it's a U20 event) -- travel as
comments next to the data they apply to). `load_events()` reads
and validates it; `WFDF_EVENTS` below is the module-level result, so nothing
downstream (`discover`, `ongoing_events`, `upcoming_events`,
`recently_ended_events`, the tests) needs to change.

To add a new event, see `sources/wfdf/event_gen.py` (`cli.py`'s
`wfdf-event` command) -- it derives a candidate entry from a WFDF site's own
`<season_id>_reference.json` rather than requiring one to be hand-written
from scratch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Wire division/gender names ingest-contract.md section 4 ("Explicit path:
# accepted names") actually accepts. Validated against on load so a typo in
# events.yaml fails loudly here rather than surfacing as a confusing
# ingest-time 400. Kept here (not imported from Go) since this is the
# Python-side mirror the loader checks against; ingest-contract.md is the
# source of truth and changing it means updating both.
ACCEPTED_DIVISIONS = {
    "college",
    "club",
    "youth-club-u20",
    "youth-club-u17",
    "masters",
    "grand-masters",
    "great-grand-masters",
    "international",
    # Age-grouped national-team divisions (ingest-contract.md section 4):
    # split out of plain "international" so a country's U20/U24 squad
    # doesn't collide with its senior squad on the team match key
    # (name+division+gender+source). WJUC is "international-u20"; WU24 is
    # added alongside it because WFDF runs that event too, even though
    # nothing in events.yaml uses it yet.
    "international-u20",
    "international-u24",
    # "beach" is deliberately absent: Beach is a playing format, not an
    # age/level group -- a beach tournament has its own club/masters/
    # international divisions, so it was removed as a Division value
    # entirely (ingest-contract.md section 4, "Why Beach is not a
    # Division"). A "beach" division here would fail loudly at load time
    # anyway (matching the Go side's parseDivisionName rejecting it), but
    # it is called out explicitly so nobody re-adds it thinking it was an
    # oversight.
}
ACCEPTED_GENDERS = {"open", "mixed", "womens", "boys", "girls"}

EVENTS_YAML_PATH = Path(__file__).resolve().parent / "events.yaml"


class EventsValidationError(ValueError):
    """Raised by `load_events()` for a malformed or invalid entry in
    events.yaml, naming the offending event so the fix is obvious."""


@dataclass(frozen=True)
class WfdfSeries:
    """One gender division within a WFDF season, as named by the `series`
    array in `<season_id>_reference.json`."""
    series_id: int
    name: str  # WFDF's series name, e.g. "Mixed" | "Open" | "Women's"
    # The wire gender name for this series (ingest-contract.md section 4),
    # e.g. "mixed" | "open" | "womens". Stated explicitly rather than
    # derived from `name` by lowercasing -- "Women's" does NOT lowercase to
    # "womens" (the apostrophe survives), and that silent mismatch is
    # exactly the bug the explicit-gender wire field exists to prevent.
    gender: str


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
    ".../wucc-2026/live/data/WUCC2026_reference.json". Both WUCC 2026 and
    WJUC 2026 use "live/data" (verified against each site's own embedded
    `STATIC_CACHE_BASE_URL` config); it's per-event and overridable rather
    than assumed to be a global constant since that hasn't been verified for
    every possible WFDF deployment.

    `name` overrides the event name that lands on the wire document (and so
    the Tournament row's name and slug). WFDF's `season.name` is an
    abbreviation -- "WUCC 2026" -- which is not what we want displayed, so
    set this to the expanded form.

    **Always include the year.** The Go writer takes `event.name` verbatim
    (it does not append a year the way the legacy CSV reader does) and
    matches existing tournaments on name + division + gender, so a bare
    "World Ultimate Club Championships" would make the 2030 edition update
    the 2026 rows instead of creating its own.

    `city`/`state`/`country` are overridable here because the WFDF API
    carries no venue information at all (`reservations[].location` is always
    null, and `season` has no location fields). Leave them blank rather than
    guessing for an event whose venue is not known.

    `start_date`/`end_date` are hardcoded here (not fetched from the
    `_reference` endpoint) so that `ongoing_events`/`upcoming_events`/
    `recently_ended_events` below stay network-free, matching `discover()`.
    They're informational for scheduling only -- the authoritative dates
    that end up on the wire document still come from `season.starttime`/
    `endtime` in the live `_reference` payload (see `WfdfSource.parse_event`).

    `division` is the wire division name (ingest-contract.md section 4),
    e.g. "club" | "international" -- the age/level group, WFDF's gender-free
    half of what USAU bakes into one compound label. It travels on the wire
    alongside each series' `gender` (WfdfSeries.gender) rather than the two
    being concatenated into a single label for the Go writer to
    pattern-match back apart -- see the module docstring / ingest-contract.md.
    """
    year: int
    base_url: str  # e.g. "https://results.wfdf.sport/wucc-2026" or "https://wjuc.wfdf.sport"
    season_id: str  # e.g. "WUCC2026", matches the API's season_id
    division: str  # wire division name, e.g. "club" | "international" -- see docstring
    series: List[WfdfSeries] = field(default_factory=list)
    data_path: str = "live/data"  # relative path between base_url and "<season_id>_<resource>.json"
    name: str = ""  # overrides season.name; include the year (see docstring)
    city: str = ""
    state: str = ""
    country: str = ""
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    def __post_init__(self) -> None:
        # Frozen dataclass -- object.__setattr__ is the documented escape
        # hatch for normalizing a field in __post_init__.
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))


def _event_label(raw: Dict[str, Any], index: int) -> str:
    """Best-effort identifier for an events.yaml entry, for error messages --
    season_id if present, else its position in the file."""
    season_id = raw.get("season_id") if isinstance(raw, dict) else None
    return f"season_id={season_id!r}" if season_id else f"entry #{index}"


def _parse_date_field(value: Any, *, label: str, field_name: str) -> date:
    # PyYAML's safe_load already parses unquoted ISO dates (2026-08-15) into
    # `datetime.date`. Accept a plain string too (e.g. a quoted date in the
    # YAML, or a value built programmatically) for robustness.
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise EventsValidationError(
                f"{label}: {field_name}={value!r} is not a parseable ISO date"
            ) from exc
    raise EventsValidationError(
        f"{label}: {field_name}={value!r} is not a date (expected YYYY-MM-DD)"
    )


def _build_series(raw_series: Any, *, label: str) -> List[WfdfSeries]:
    if not isinstance(raw_series, list) or not raw_series:
        raise EventsValidationError(f"{label}: 'series' must be a non-empty list")
    series: List[WfdfSeries] = []
    for i, raw in enumerate(raw_series):
        if not isinstance(raw, dict):
            raise EventsValidationError(f"{label}: series[{i}] is not a mapping: {raw!r}")
        missing = [k for k in ("series_id", "name", "gender") if k not in raw]
        if missing:
            raise EventsValidationError(
                f"{label}: series[{i}] is missing required field(s) {missing}: {raw!r}"
            )
        gender = raw["gender"]
        if gender not in ACCEPTED_GENDERS:
            raise EventsValidationError(
                f"{label}: series[{i}] ({raw.get('name')!r}) has gender={gender!r}, "
                f"not one of the accepted wire gender names: {sorted(ACCEPTED_GENDERS)}"
            )
        series.append(WfdfSeries(series_id=raw["series_id"], name=raw["name"], gender=gender))
    return series


def _build_event(raw: Dict[str, Any], index: int) -> WfdfEvent:
    label = _event_label(raw, index)
    if not isinstance(raw, dict):
        raise EventsValidationError(f"entry #{index} is not a mapping: {raw!r}")

    required = ("year", "base_url", "season_id", "division", "series")
    missing = [k for k in required if k not in raw]
    if missing:
        raise EventsValidationError(f"{label}: missing required field(s) {missing}")

    division = raw["division"]
    if division not in ACCEPTED_DIVISIONS:
        raise EventsValidationError(
            f"{label}: division={division!r} is not one of the accepted wire division "
            f"names: {sorted(ACCEPTED_DIVISIONS)}"
        )

    series = _build_series(raw["series"], label=label)

    kwargs: Dict[str, Any] = dict(
        year=raw["year"],
        base_url=raw["base_url"],
        season_id=raw["season_id"],
        division=division,
        series=series,
    )
    if "data_path" in raw and raw["data_path"] is not None:
        kwargs["data_path"] = raw["data_path"]
    for str_field in ("name", "city", "state", "country"):
        if str_field in raw and raw[str_field] is not None:
            kwargs[str_field] = raw[str_field]
    for date_field in ("start_date", "end_date"):
        if date_field in raw and raw[date_field] is not None:
            kwargs[date_field] = _parse_date_field(raw[date_field], label=label, field_name=date_field)

    try:
        return WfdfEvent(**kwargs)
    except (TypeError, ValueError) as exc:
        raise EventsValidationError(f"{label}: {exc}") from exc


def load_events(path: Optional[Path] = None) -> List[WfdfEvent]:
    """Load and validate `events.yaml` (or `path`) into `WfdfEvent`s.

    Fails loudly, naming the offending event, on: a missing required field,
    an unrecognized `division`/series `gender` (checked against
    ingest-contract.md section 4's accepted wire names), an empty `series`
    list, or an unparseable date. A typo here should surface at load time,
    not as a confusing ingest-time 400.
    """
    p = path if path is not None else EVENTS_YAML_PATH
    with open(p, "r", encoding="utf-8") as f:
        raw_events = yaml.safe_load(f) or []

    if not isinstance(raw_events, list):
        raise EventsValidationError(f"{p}: expected a top-level list of events, got {type(raw_events)}")

    return [_build_event(raw, i) for i, raw in enumerate(raw_events)]


WFDF_EVENTS: List[WfdfEvent] = load_events()


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
