"""WFDF legacy-results event list, loaded from `events.yaml`.

Mirrors `sources/wfdf/events.py`'s validated-dataclass pattern closely, with
two deliberate differences (see the WFDF-legacy source task):

- No `series_id` is hardcoded on `WfdfLegacySeries`. The legacy site's own
  left-nav sidebar (`table.leftmenulinks`) exposes `series_id -> series_name`
  on every page, so `WfdfLegacySource.discover()` matches a configured
  series by *name* against that sidebar at fetch time, rather than trusting
  a hand-typed id that could silently drift out of sync with a given event's
  actual numbering (WUC2024's own series ids -- 1=Mixed, 2=Open, 3=Women's --
  aren't even in name order, which is exactly the kind of thing a hardcoded
  id would get wrong for the next event).
- No `ongoing_events`/`upcoming_events`/`recently_ended_events`. Every
  `wfdf-legacy` event is, by definition, a finished historical tournament --
  there is no "live" concept for this source to schedule around.

`ACCEPTED_DIVISIONS`/`ACCEPTED_GENDERS` are imported from `sources.wfdf.events`
rather than duplicated: both modules are permanent siblings validating
against the same wire vocabulary (ingest-contract.md section 4), unlike the
deliberate duplication in `core/parsing.py`, which exists only because that
source module (USAU's old parse.py) is scheduled for deletion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from sources.wfdf.events import ACCEPTED_DIVISIONS, ACCEPTED_GENDERS

EVENTS_YAML_PATH = Path(__file__).resolve().parent / "events.yaml"


class EventsValidationError(ValueError):
    """Raised by `load_events()` for a malformed or invalid entry in
    events.yaml, naming the offending event so the fix is obvious."""


@dataclass(frozen=True)
class WfdfLegacySeries:
    """One gender division within a WFDF-legacy event, matched by name (not
    a hardcoded id -- see the module docstring) against the site's own
    left-nav sidebar."""
    name: str  # the legacy site's own series name, e.g. "Mixed" | "Open" | "Women's"
    # The wire gender name for this series (ingest-contract.md section 4),
    # e.g. "mixed" | "open" | "womens". Stated explicitly rather than
    # derived from `name` by lowercasing -- "Women's" does NOT lowercase to
    # "womens" (the apostrophe survives), same caveat as sources/wfdf/events.py.
    gender: str


@dataclass(frozen=True)
class WfdfLegacyEvent:
    """One WFDF-legacy event (one dedicated site under `base_url`).

    `base_url` is the site's root, e.g. "https://results.wfdf.sport/wuc"
    (trailing slash normalised away below) -- every page is
    `f"{base_url}/?view=...&..."`. The site 301-redirects a request missing
    that slash to the correct URL, so leaving it out would still work, just
    with an extra round trip on every single request.

    `name` overrides the event name that lands on the wire document. Always
    include the year (see `sources/wfdf/events.py`'s `WfdfEvent` docstring
    for why -- the Go writer takes `event.name` verbatim and matches
    tournaments on name + division + gender).

    `start_date`/`end_date` are informational only, for humans reading
    events.yaml -- the authoritative dates on the wire document come from
    the min/max of every scraped game's own date (read off the page's <h3>
    day headers), computed in `WfdfLegacySource.parse_event`.
    """
    year: int
    base_url: str
    season_id: str  # e.g. "WUC2024" -- used in team/roster page query strings
    division: str  # wire division name, e.g. "international"
    series: List[WfdfLegacySeries] = field(default_factory=list)
    name: str = ""
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
    season_id = raw.get("season_id") if isinstance(raw, dict) else None
    return f"season_id={season_id!r}" if season_id else f"entry #{index}"


def _parse_date_field(value: Any, *, label: str, field_name: str) -> date:
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


def _build_series(raw_series: Any, *, label: str) -> List[WfdfLegacySeries]:
    if not isinstance(raw_series, list) or not raw_series:
        raise EventsValidationError(f"{label}: 'series' must be a non-empty list")
    series: List[WfdfLegacySeries] = []
    for i, raw in enumerate(raw_series):
        if not isinstance(raw, dict):
            raise EventsValidationError(f"{label}: series[{i}] is not a mapping: {raw!r}")
        missing = [k for k in ("name", "gender") if k not in raw]
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
        series.append(WfdfLegacySeries(name=raw["name"], gender=gender))
    return series


def _build_event(raw: Dict[str, Any], index: int) -> WfdfLegacyEvent:
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
    for str_field in ("name", "city", "state", "country"):
        if str_field in raw and raw[str_field] is not None:
            kwargs[str_field] = raw[str_field]
    for date_field in ("start_date", "end_date"):
        if date_field in raw and raw[date_field] is not None:
            kwargs[date_field] = _parse_date_field(raw[date_field], label=label, field_name=date_field)

    try:
        return WfdfLegacyEvent(**kwargs)
    except (TypeError, ValueError) as exc:
        raise EventsValidationError(f"{label}: {exc}") from exc


def load_events(path: Optional[Path] = None) -> List[WfdfLegacyEvent]:
    """Load and validate `events.yaml` (or `path`) into `WfdfLegacyEvent`s.

    Fails loudly, naming the offending event, on: a missing required field,
    an unrecognized `division`/series `gender`, an empty `series` list, or
    an unparseable date.
    """
    p = path if path is not None else EVENTS_YAML_PATH
    with open(p, "r", encoding="utf-8") as f:
        raw_events = yaml.safe_load(f) or []

    if not isinstance(raw_events, list):
        raise EventsValidationError(f"{p}: expected a top-level list of events, got {type(raw_events)}")

    return [_build_event(raw, i) for i, raw in enumerate(raw_events)]


WFDF_LEGACY_EVENTS: List[WfdfLegacyEvent] = load_events()


def events_for_year(year: int) -> List[WfdfLegacyEvent]:
    """All hardcoded WFDF-legacy events for `year`."""
    return [e for e in WFDF_LEGACY_EVENTS if e.year == year]
