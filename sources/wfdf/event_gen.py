"""Derive a `WfdfEvent` for a new WFDF event from the event's own base URL,
for `cli.py`'s `wfdf-event` command.

The chicken-and-egg problem: page URLs are
`<base_url>/<data_path>/<SEASON_ID>_reference.json`, but the season id is
what we're trying to find. Two real WFDF sites were inspected while writing
this (results.wfdf.sport/wucc-2026 and wjuc.wfdf.sport, 2026-08-27) to see
how the season id actually surfaces in the page:

  - Neither site ever embeds a literal `<id>_reference.json` string in its
    HTML or its (1MB, minified) JS bundle -- the frontend builds that
    filename client-side by string concatenation, so grepping the markup
    for the literal filename pattern (the first thing tried below) finds
    nothing on either real example.
  - Both sites *do* inline a small JS config blob in a `<script>` tag on the
    base page itself, containing `"LIVE_SEASON_ID":"<season_id>"` and
    `"STATIC_CACHE_BASE_URL":"<data_path>/"` -- e.g.
    `"LIVE_SEASON_ID":"WUCC2026"` / `"STATIC_CACHE_BASE_URL":"/wucc-2026/live/data/"`
    for WUCC, `"LIVE_SEASON_ID":"wjuc2026"` / `"STATIC_CACHE_BASE_URL":"/live/data/"`
    for WJUC. That's the reliable signal `discover_season_id` uses.

`derive_event` then turns a fetched `<season_id>_reference.json` payload's
`season`/`series` blocks into a `WfdfEvent`, matching the mapping rules in
the WFDF CLI helper task:

  - `season_id`/`year`/`start_date`/`end_date` from `season.season_id` and
    `season.starttime`/`endtime`.
  - `series[].gender` from WFDF's series `name` via `GENDER_MAP` -- an
    unmapped name is never guessed; it comes back as the literal string
    `"TODO"` plus a warning naming the series, and `--write` must refuse.
  - `division` from `season.isnationalteams` (0 -> "club", 1 -> a
    national-teams division; verified against both real payloads above --
    WUCC's is 0, WJUC's is 1). Absent or not one of {0, 1} requires
    `--division`. When isnationalteams is 1, the specific national-teams
    division is *suggested* from `season.name`'s text via
    `infer_national_team_division` -- "WJUC"/"junior"/"u20" ->
    "international-u20", "WU24"/"u24"/"under-24" -> "international-u24",
    otherwise plain "international" (e.g. WUGC). This is a suggestion a
    human reviews before `--write`, not a guarantee -- `--division` always
    overrides it outright.
  - `name` by expanding `season.name`'s abbreviation via `NAME_EXPANSIONS`,
    always keeping the year; an unknown abbreviation is left raw, with a
    warning.
  - `city`/`state`/`country` are never in the payload (same as WUCC: no
    location fields at all) -- always emitted blank, for a human to fill in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Optional

from sources.wfdf.events import ACCEPTED_DIVISIONS, WfdfEvent, WfdfSeries
from sources.wfdf.parse import parse_wfdf_date

# WFDF's own series `name` -> wire gender name (ingest-contract.md section 4,
# `teams.Gender`: open | mixed | womens | boys | girls). "Women's" does NOT
# lowercase to "womens" (the apostrophe survives) -- stated as an explicit
# table entry, not derived by lowercasing, for the same reason WfdfSeries.gender
# is explicit rather than derived from `name`.
GENDER_MAP: Dict[str, str] = {
    "mixed": "mixed",
    "open": "open",
    "women's": "womens",
    "womens": "womens",
    "men": "open",
    "men's": "open",
    "mens": "open",
    "boys": "boys",
    "girls": "girls",
}

# WFDF's own season-name abbreviation (the leading token of `season.name`,
# e.g. "WUCC" in "WUCC 2026") -> its expansion. Verified against real WFDF
# payloads (WUCC2026_reference.json, wjuc2026_reference.json, 2026-08-27),
# not assumed.
NAME_EXPANSIONS: Dict[str, str] = {
    "WUCC": "World Ultimate Club Championships",
    "WMUCC": "World Masters Ultimate Club Championships",
    "WJUC": "World Junior Ultimate Championships",
    "WUGC": "World Ultimate Championships",
}

# The reliable signal on real WFDF pages -- see the module docstring for why
# this is tried before the literal-filename pattern below.
# Event-name text that suggests a national-teams event (season.isnationalteams
# == 1) is age-grouped rather than a senior/open event -- used only by
# infer_national_team_division below. Matched against season.name, WFDF's own
# (abbreviated) event name, e.g. "WJUC 2026" -- NOT the expanded display name
# -- since that's what's guaranteed present and is what the real WJUC/WUCC
# payloads (sources/wfdf/fixtures/, events.yaml) actually carry.
#
# \bwjuc\b / \bwu-?24\b need their own alternatives (not just \bu-?20\b /
# \bu-?24\b) because "u20"/"u24" only sit at a word boundary when they are
# their own token -- inside "WJUC" or "WU24" the digits are glued to a
# leading letter with no boundary in between, so a bare \bu-?20\b would never
# match "WJUC 2026" and a bare \bu-?24\b would never match "WU24 2027".
_U20_INDICATORS_RE = re.compile(r"\bwjuc\b|\bjunior\b|\bu-?20\b", re.IGNORECASE)
_U24_INDICATORS_RE = re.compile(r"\bwu-?24\b|\bu-?24\b|\bunder-?24\b", re.IGNORECASE)

_LIVE_SEASON_ID_RE = re.compile(r'"LIVE_SEASON_ID"\s*:\s*"([^"]+)"')
# Tried as a fallback in case some other WFDF deployment does embed the
# reference filename literally (e.g. a static prefetch <link>), even though
# neither site inspected while writing this does.
_REFERENCE_FILENAME_RE = re.compile(r'([A-Za-z0-9_-]+)_reference\.json')


class SeasonIdDiscoveryError(RuntimeError):
    """Raised by `discover_season_id` when neither pattern matches. Carries
    the base_url and both patterns tried so the caller can say exactly what
    was attempted, per the WFDF CLI helper task."""


def discover_season_id(base_url: str, transport: Callable[[str], bytes]) -> str:
    """Fetch `base_url` and find the season id WFDF's own frontend uses to
    build `<season_id>_reference.json` (see `WfdfSource._build_url`)."""
    html = transport(base_url).decode("utf-8", errors="replace")
    m = _LIVE_SEASON_ID_RE.search(html)
    if m:
        return m.group(1)
    m = _REFERENCE_FILENAME_RE.search(html)
    if m:
        return m.group(1)
    raise SeasonIdDiscoveryError(
        f"could not discover a season id from {base_url!r}. Tried:\n"
        f"  - a LIVE_SEASON_ID config value (regex {_LIVE_SEASON_ID_RE.pattern!r})\n"
        f"  - a literal '<id>_reference.json' reference (regex {_REFERENCE_FILENAME_RE.pattern!r})\n"
        f"Neither matched the fetched page. Pass --season-id to skip discovery."
    )


def infer_national_team_division(season_name: str) -> str:
    """Suggest which national-teams wire division (ingest-contract.md
    section 4) a national-teams event (season.isnationalteams == 1) should
    get, from `season_name` (WFDF's own `season.name`, e.g. "WJUC 2026" --
    the abbreviated form, not the expanded display name).

    This is only ever a *suggestion*: `derive_event` uses it exclusively for
    the isnationalteams-derived case, and `--division`/the `division`
    keyword argument always overrides it outright before this is ever
    called. A wrong guess is recoverable (the CLI's `--write` output is
    reviewed and pasted by a human), so the matching below is deliberately
    narrow and literal -- checked against the real WUCC/WJUC payloads
    (sources/wfdf/fixtures/, events.yaml) -- rather than an exhaustive or
    clever heuristic that would make a wrong guess harder to predict.

      - "WJUC", "junior", or "u20"/"u-20" appearing anywhere in the name ->
        "international-u20" (WJUC: World Junior Ultimate Championships).
      - "WU24", "u24"/"u-24", or "under-24"/"under24" -> "international-u24"
        (WU24: not yet a real events.yaml entry, but a real WFDF event).
      - anything else -> plain "international" (e.g. "WUGC 2028": World
        Ultimate Championships, WFDF's senior/open national-teams event).
    """
    if _U20_INDICATORS_RE.search(season_name):
        return "international-u20"
    if _U24_INDICATORS_RE.search(season_name):
        return "international-u24"
    return "international"


def map_gender(series_name: str) -> Optional[str]:
    """WFDF series `name` -> wire gender name, or None if unrecognized (see
    GENDER_MAP -- never guessed)."""
    return GENDER_MAP.get(series_name.strip().lower())


def expand_name(season_name: str) -> "tuple[str, bool]":
    """(name, expanded) from WFDF's `season.name` (e.g. "WUCC 2026", an
    abbreviation). Keeps every token after the abbreviation (so the year, and
    anything else WFDF appended, survives) -- expanded=False, name=season_name
    unchanged, if the leading token isn't a known abbreviation."""
    parts = season_name.strip().split()
    if not parts:
        return season_name, False
    abbrev = parts[0].upper()
    expansion = NAME_EXPANSIONS.get(abbrev)
    if expansion is None:
        return season_name, False
    rest = " ".join(parts[1:])
    return (f"{expansion} {rest}".strip(), True)


@dataclass
class DerivedEvent:
    """Result of `derive_event`: the candidate `WfdfEvent` plus everything
    the CLI needs to decide whether it's safe to `--write`."""
    event: WfdfEvent
    warnings: List[str] = field(default_factory=list)
    # Series names whose gender could not be mapped (WfdfSeries.gender is
    # the literal string "TODO" for these) -- non-empty means `--write` must
    # refuse.
    unmapped_series: List[str] = field(default_factory=list)
    # False if season.name's abbreviation was unknown and left unexpanded.
    name_expanded: bool = True
    # Where `division` came from: "isnationalteams" or "override".
    division_source: str = "isnationalteams"

    @property
    def is_safe_to_write(self) -> bool:
        return not self.unmapped_series


def derive_event(
    payload: dict,
    *,
    base_url: str,
    data_path: str = "live/data",
    division: Optional[str] = None,
    name: Optional[str] = None,
    city: str = "",
    state: str = "",
    country: str = "",
) -> DerivedEvent:
    """Build a `WfdfEvent` from a `<season_id>_reference.json` payload's
    `season`/`series` blocks. Raises `ValueError` (naming the missing/bad
    field) for anything that can't be resolved without a flag; unmapped
    series genders and unexpanded names are reported as warnings on the
    returned `DerivedEvent` instead of raising, since those have a safe
    fallback (TODO / raw name) a human can still act on.
    """
    season = payload.get("season")
    if not season:
        raise ValueError("payload has no 'season' block")
    series_payload = payload.get("series") or []
    if not series_payload:
        raise ValueError("payload has no 'series' entries")

    season_id = season.get("season_id")
    if not season_id:
        raise ValueError("season.season_id is missing")

    start_date = _parse_required_date(season.get("starttime"), field_name="season.starttime")
    end_date = _parse_required_date(season.get("endtime"), field_name="season.endtime")
    year = start_date.year

    warnings: List[str] = []

    if division is not None:
        resolved_division = division
        division_source = "override"
    else:
        flag = season.get("isnationalteams")
        if flag not in (0, 1):
            raise ValueError(
                f"season.isnationalteams={flag!r} (expected 0 or 1) -- pass --division "
                f"explicitly (0 -> club, 1 -> international/international-u20/international-u24)"
            )
        if flag == 0:
            resolved_division = "club"
        else:
            # National-teams event: suggest an age-grouped division from the
            # event name text rather than always defaulting to plain
            # "international" -- see infer_national_team_division. Still
            # just a suggestion; --division above already took precedence
            # over this whole branch.
            resolved_division = infer_national_team_division(season.get("name", ""))
        division_source = "isnationalteams"
    if resolved_division not in ACCEPTED_DIVISIONS:
        raise ValueError(
            f"division {resolved_division!r} is not one of the accepted wire division "
            f"names: {sorted(ACCEPTED_DIVISIONS)}"
        )

    unmapped: List[str] = []
    series_list: List[WfdfSeries] = []
    for s in series_payload:
        s_id = s.get("series_id")
        s_name = s.get("name")
        if s_id is None or not s_name:
            raise ValueError(f"malformed series entry: {s!r}")
        gender = map_gender(s_name)
        if gender is None:
            warnings.append(
                f"series {s_name!r} (series_id={s_id}) has no known gender mapping in "
                f"GENDER_MAP -- emitting gender: TODO. Add it there, or fix the pasted "
                f"entry by hand, before using --write."
            )
            unmapped.append(s_name)
            gender = "TODO"
        series_list.append(WfdfSeries(series_id=s_id, name=s_name, gender=gender))

    if name is not None:
        resolved_name = name
        name_expanded = True
    else:
        raw_name = season.get("name", "")
        resolved_name, name_expanded = expand_name(raw_name)
        if not name_expanded:
            warnings.append(
                f"season.name {raw_name!r} has no known abbreviation expansion in "
                f"NAME_EXPANSIONS -- emitting it unexpanded. Expand it by hand, or pass --name."
            )
        elif str(year) not in resolved_name:
            warnings.append(
                f"expanded name {resolved_name!r} does not contain the year {year} -- "
                f"double-check season.name {raw_name!r} by hand."
            )

    event = WfdfEvent(
        year=year,
        base_url=base_url,
        season_id=season_id,
        division=resolved_division,
        series=series_list,
        data_path=data_path,
        name=resolved_name,
        city=city,
        state=state,
        country=country,
        start_date=start_date,
        end_date=end_date,
    )
    return DerivedEvent(
        event=event,
        warnings=warnings,
        unmapped_series=unmapped,
        name_expanded=name_expanded,
        division_source=division_source,
    )


def _parse_required_date(value, *, field_name: str) -> date:
    if not value:
        raise ValueError(f"{field_name} is missing")
    try:
        return parse_wfdf_date(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{field_name}={value!r} is not parseable: {exc}") from exc


def event_to_yaml_block(event: WfdfEvent) -> str:
    """Render `event` as a YAML list-item block matching events.yaml's
    shape, ready to paste (or for `--write` to append verbatim). Hand-built
    (not `yaml.dump`) so field order and comment placement match
    events.yaml's existing entries and the blank city/state/country fields
    carry an explanatory comment rather than silently being empty strings.
    """
    lines: List[str] = []
    lines.append(f"- year: {event.year}")
    lines.append(f'  base_url: "{event.base_url}"')
    lines.append(f'  season_id: "{event.season_id}"')
    lines.append(f'  division: "{event.division}"')
    lines.append(f'  name: "{event.name}"')
    venue_comment = "  # not in the API payload -- fill in by hand"
    lines.append(f'  city: "{event.city}"{venue_comment if not event.city else ""}')
    lines.append(f'  state: "{event.state}"')
    lines.append(f'  country: "{event.country}"{venue_comment if not event.country else ""}')
    lines.append(f"  start_date: {event.start_date.isoformat()}")
    lines.append(f"  end_date: {event.end_date.isoformat()}")
    if event.data_path != "live/data":
        lines.append(f'  data_path: "{event.data_path}"')
    lines.append("  series:")
    for s in event.series:
        lines.append(f"    - series_id: {s.series_id}")
        lines.append(f'      name: "{s.name}"')
        gender_comment = "  # TODO: unmapped -- fill in by hand" if s.gender == "TODO" else ""
        lines.append(f'      gender: "{s.gender}"{gender_comment}')
    return "\n".join(lines) + "\n"
