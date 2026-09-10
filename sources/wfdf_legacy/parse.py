"""Pure bs4 functions: WFDF-legacy's static HTML pages
(`results.wfdf.sport/<slug>/`) -> `models.py` domain objects. No network, no
I/O -- everything here takes an already-fetched `BeautifulSoup` (or, for
`build_teams`/`build_stages`, already-parsed intermediate structures) and
returns plain data, so it's unit-testable straight from the checked-in
fixtures under `sources/wfdf_legacy/fixtures/`.

Site shape (WUC 2024, `https://results.wfdf.sport/wuc`), verified against
the real captured HTML:

- `?view=games&filter=tournaments&group=all` carries both the left-nav
  sidebar (`table.leftmenulinks`, series -> pools) and the full day-by-day
  schedule, as one big page. `parse_nav` reads the former; `parse_schedule_page`
  reads the latter.
- `?view=teams&season=<id>&list=allteams` / `...&list=byseeding` list every
  team, grouped by series. `parse_teams_page` / `parse_seeding_page`.
- `?view=teamcard&team=<id>` is one team's roster. `parse_teamcard`.

Team identity fix (see the WFDF-legacy source task, "Team identity fix"):
the schedule/teams pages' own team string is division-suffixed
("Australia Open"), which must NOT become `models.Team.name` -- that would
bake division/gender into the name, breaking cross-source team matching.
`build_teams` uses the bare country name for `Team.name` instead, and keeps
the suffixed-name -> Team mapping as an internal-only return value
(`teams_by_raw_name`) used solely to resolve game rows' team1/team2 text.

Deviation from the original design, discovered against the real fixture
(flagged for the Phase 2 check-in): the left-nav sidebar does NOT list every
pool. WUC2024's bracket "Semifinals"/"Finals" follower pools (11 of them,
~38 of 244 games on the page) have no sidebar entry at all -- their only
name anywhere on the site is the schedule table's own per-pool `<th>` header
(e.g. "Mixed Playoff (1-8) Semifinals", series-name-prefixed). Silently
trusting the sidebar as the sole source of pool names -- the original plan's
assumption -- would drop these games. `parse_schedule_page` therefore also
returns a `pool_id -> raw <th> text` map, and `build_stages` falls back to
it (stripping the known `series_name` prefix) for any pool id absent from
the nav. This is the "requires fragile stripping" case the original design
explicitly wanted to avoid -- it turned out to be unavoidable for a real
subset of pools, not merely a hypothetical.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Comment, Tag

import models
from core.parsing import convertNonDigitScore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NavPool:
    pool_id: int
    name: str  # bare pool name, e.g. "Pool A" | "Crossover" | "Playoff (1-8)"


@dataclass(frozen=True)
class NavSeries:
    series_id: int
    name: str  # e.g. "Mixed" | "Open" | "Women's"
    pools: List[NavPool] = field(default_factory=list)


class UnrecognizedStageShapeError(ValueError):
    """Raised by `classify_stage` when a pool name doesn't match any of the
    site's known stage shapes (Pool/Crossover/Playoff). This means the
    site's HTML shape changed in a way this source doesn't understand -- not
    a routine data gap -- so `build_stages` logs it as an error and skips
    just that one pool's games rather than silently guessing or aborting the
    whole event."""

    def __init__(self, pool_name: str):
        super().__init__(f"unrecognized stage shape for pool name {pool_name!r}")
        self.pool_name = pool_name


@dataclass(frozen=True)
class StageClassification:
    kind: str  # "pool" | "cluster" | "bracket"
    group_name: str
    round_label: str


@dataclass(frozen=True)
class TeamRow:
    team_id: int
    raw_name: str  # division-suffixed display string, e.g. "Australia Open"
    country: str  # bare country name, e.g. "Australia"


@dataclass(frozen=True)
class RawGame:
    pool_id: int
    date: date
    time_text: str
    team1_raw_name: str
    team2_raw_name: str
    score1_text: str
    score2_text: str
    game_id: Optional[int]


# "Playoff (1-8)" (WUC2024, WU24-2025, WJUC2024) and "Bracket(s) (1-8)"
# (WU24-2023 -- singular "Bracket" for Open, plural "Brackets" for Mixed/
# Women's, on the very same event) are the same shape under different
# literal words the site happens to print for a given event/division; treat
# them as one pattern rather than one-off named exceptions.
_NUMBERED_BRACKET_RE = re.compile(r"^((?:Playoff|Brackets?)\s+\([^)]*\))(?:\s+(.*))?$")
_COMMENT_RE = re.compile(r"res:[^ ]*\s+pool:(\d+)\s+date:")
_H3_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_ID_RE = re.compile(r"=(\d+)")


def _int_param(href: str) -> Optional[int]:
    """Last `=<digits>` in a query string, e.g. "?view=teamcard&team=60" -> 60."""
    matches = _ID_RE.findall(href or "")
    return int(matches[-1]) if matches else None


# ---------------------------------------------------------------------------
# parse_nav
# ---------------------------------------------------------------------------


def parse_nav(soup: BeautifulSoup) -> Dict[int, NavSeries]:
    """`table.leftmenulinks` (present on every page of the site) ->
    `series_id -> NavSeries(name, pools=[NavPool(pool_id, name), ...])`.

    This is the *only* place a series' set of pools is discovered from --
    except for the fallback in `build_stages` for pools the sidebar omits
    entirely (see the module docstring)."""
    table = soup.find("table", class_="leftmenulinks")
    if table is None:
        raise ValueError("wfdf-legacy: no left-nav sidebar (table.leftmenulinks) found on the page")

    nav: Dict[int, NavSeries] = {}
    current_series_id: Optional[int] = None

    for a in table.find_all("a"):
        classes = a.get("class") or []
        href = a.get("href", "")

        if "subnav" in classes:
            series_id = _int_param(href)
            if series_id is None:
                continue
            current_series_id = series_id
            nav[series_id] = NavSeries(series_id, a.get_text(strip=True))
            continue

        if "navpoollink" in classes and "poolstatus" in href:
            if current_series_id is None:
                log.warning("wfdf-legacy: pool link %r found before any series header; skipping", href)
                continue
            pool_id = _int_param(href)
            if pool_id is None:
                continue
            # Strip the leading "» " (decoded &raquo;) marker.
            name = re.sub(r"^\W+\s*", "", a.get_text(strip=True))
            nav[current_series_id].pools.append(NavPool(pool_id, name))

    return nav


# ---------------------------------------------------------------------------
# classify_stage
# ---------------------------------------------------------------------------


# Bare pool names, seen on real events, that are single-elimination bracket
# rounds but don't fit the numbered "Playoff/Bracket(s) (a-b)" shape -- e.g.
# WU24 2025's and WU24-2023's Open series both have a "Pre-Quarters" (or,
# on WJUC2024, "Pre-quarters" -- casing varies by event) pool feeding into
# the numbered bracket, sitting in the nav sidebar as its own pool with no
# "(a-b)" range in the name. Matched case-insensitively since the site's own
# casing isn't consistent event to event; `group_name` still uses the
# pool's actual on-page text, not this canonical spelling. Extend this set
# (with a comment naming the event that surfaced it) rather than loosening
# the fail-loud fallback below -- a new unnamed shape should still raise so
# a human looks at it once, the way this one was.
_NAMED_BRACKET_STAGES_CASEFOLDED = {
    "pre-quarters",  # WU24 2025 & WU24-2023 (Open); "Pre-quarters" on WJUC2024
}


def classify_stage(pool_name: str) -> StageClassification:
    """Bare pool name (from the nav sidebar, or the schedule's own <th>
    fallback -- see the module docstring) -> its stage shape. Explicit
    ordered rules, raise on no match (fail loud):

    1. "Pool <letter>" -> a pool-play pool.
    2. "Crossover" (or starting with it) -> a cluster.
    3. "Playoff (...)" or "Bracket(s) (...)" [+ trailing round text] -> a
       bracket; the "Playoff/Bracket(s) (...)" prefix is the bracket's group
       name, everything after it (verbatim, unvalidated -- e.g.
       "Semifinals"/"Finals") is the round label for games under this
       specific pool. Different WFDF-legacy events print different words for
       the same shape ("Playoff" vs. singular "Bracket" vs. plural
       "Brackets", sometimes even split by division within one event -- see
       `_NUMBERED_BRACKET_RE`), so all three are accepted uniformly.
    4. A case-insensitive match in `_NAMED_BRACKET_STAGES_CASEFOLDED` -> its
       own bracket group, round_label="".
    5. Anything else -> UnrecognizedStageShapeError.
    """
    if pool_name.startswith("Pool "):
        return StageClassification(kind="pool", group_name=pool_name, round_label="")

    if pool_name.startswith("Crossover"):
        return StageClassification(kind="cluster", group_name=pool_name, round_label="")

    m = _NUMBERED_BRACKET_RE.match(pool_name)
    if m is not None:
        group_name = m.group(1)
        round_label = (m.group(2) or "").strip()
        return StageClassification(kind="bracket", group_name=group_name, round_label=round_label)

    if pool_name.casefold() in _NAMED_BRACKET_STAGES_CASEFOLDED:
        return StageClassification(kind="bracket", group_name=pool_name, round_label="")

    raise UnrecognizedStageShapeError(pool_name)


# ---------------------------------------------------------------------------
# parse_teams_page / parse_seeding_page / parse_teamcard
# ---------------------------------------------------------------------------


def parse_teams_page(soup: BeautifulSoup) -> Dict[str, List[TeamRow]]:
    """`?view=teams&list=allteams` -> series_name -> [TeamRow, ...].

    Rows are grouped under `<tr><th colspan='3'>{SeriesName}</th></tr>`
    header rows; a team row's first `<a>` (`?view=teamcard&team=N`) carries
    the raw, division-suffixed display name ("Australia Open"), its second
    `<a>` (`?view=countrycard&...`) the bare country name ("Australia")."""
    teams_by_series: Dict[str, List[TeamRow]] = defaultdict(list)
    current_series: Optional[str] = None

    for tr in soup.find_all("tr"):
        th = tr.find("th")
        if th is not None:
            current_series = th.get_text(strip=True)
            continue

        tds = tr.find_all("td")
        if len(tds) < 2 or current_series is None:
            continue

        team_link = tds[0].find("a", href=lambda h: h and "view=teamcard" in h)
        if team_link is None:
            continue
        team_id = _int_param(team_link["href"])
        if team_id is None:
            continue

        country_link = tds[1].find("a", href=lambda h: h and "view=countrycard" in h)
        country = country_link.get_text(strip=True) if country_link else ""

        teams_by_series[current_series].append(
            TeamRow(team_id=team_id, raw_name=team_link.get_text(strip=True), country=country)
        )

    return dict(teams_by_series)


def parse_seeding_page(soup: BeautifulSoup) -> Dict[int, int]:
    """`?view=teams&list=byseeding` -> team_id -> seeding pot number.

    Same row shape as `allteams`, plus a leading `<td>N.</td>` pot-number
    cell. NOTE: the pot number is a seeding *pot*, not a unique rank --
    multiple teams in the same series can (and do, in WUC2024) share the
    same pot number."""
    seeds: Dict[int, int] = {}

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue

        team_link = tds[1].find("a", href=lambda h: h and "view=teamcard" in h)
        if team_link is None:
            continue
        team_id = _int_param(team_link["href"])
        if team_id is None:
            continue

        pot_text = tds[0].get_text(strip=True).rstrip(".")
        if pot_text == "-":
            # Observed for WUC2024's Guts teams (a different series this
            # source never configures) -- "-" means "no seeding pot
            # assigned", not a parse failure, so this isn't worth a warning.
            continue
        try:
            seeds[team_id] = int(pot_text)
        except ValueError:
            log.warning(
                "wfdf-legacy: could not parse seeding pot number %r for team_id=%s", pot_text, team_id
            )

    return seeds


def parse_teamcard(soup: BeautifulSoup) -> List["models.Player"]:
    """`?view=teamcard&team=<id>` -> roster (jersey number + full name).

    Finds the roster table by header text ("#"/"Name"), mirroring
    `sources/usau/parse.py`'s `addRosterToTeam` -- rather than assuming a
    fixed table position on the page.

    The real page's HTML is not well-formed (several `<table>`s are never
    properly closed), which makes `html.parser` merge the roster table's own
    header row together with a handful of *later*, unrelated stats tables
    (event/spirit summaries) into one bloated `<tr>` -- that merged table
    still starts with "#", "Name" but also carries dozens of extra headers
    and, if picked, would pull those later tables' rows in as bogus
    "players". Picking the *smallest* header-matching table (there is also a
    second, genuinely self-contained copy of the roster table further down
    the page) reliably lands on the real roster rather than that merge."""
    candidates = []
    for table in soup.find_all("table"):
        header_row = table.find("tr")
        if header_row is None:
            continue
        headers = [th.get_text(strip=True) for th in header_row.find_all("th")]
        if headers[:2] == ["#", "Name"]:
            candidates.append((len(headers), table))

    if not candidates:
        return []
    _, roster_table = min(candidates, key=lambda pair: pair[0])

    players: List["models.Player"] = []
    for tr in roster_table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        number = tds[0].get_text(strip=True)
        name_link = tds[1].find("a")
        name = name_link.get_text(strip=True) if name_link is not None else tds[1].get_text(strip=True)
        players.append(models.Player(number, name))

    return players


# ---------------------------------------------------------------------------
# parse_schedule_page
# ---------------------------------------------------------------------------


def parse_schedule_page(soup: BeautifulSoup) -> Tuple[Dict[int, List[RawGame]], Dict[int, str]]:
    """Walks the whole games page (every series, including Guts -- callers
    filter to the pool ids they care about) and returns:

    - `pool_id -> [RawGame, ...]`, every game row found, tagged with the
      pool id that most recently preceded it via an HTML comment
      (`<!-- res:{DayGroup} pool:{N} date:{D}.{M}.-->`) and the date most
      recently established by an `<h3>` day header (which carries the year
      the comment's own `date:D.M.` does not).
    - `pool_id -> raw <th> header text` (e.g. "Open Pool C", "Mixed Playoff
      (1-8) Semifinals"), the first occurrence seen for each pool id -- used
      by `build_stages` as a fallback pool name for any pool id the nav
      sidebar omits (see the module docstring).
    """
    schedule_by_pool_id: Dict[int, List[RawGame]] = defaultdict(list)
    pool_headers_by_id: Dict[int, str] = {}

    current_pool_id: Optional[int] = None
    current_date: Optional[date] = None

    for el in soup.descendants:
        if isinstance(el, Comment):
            m = _COMMENT_RE.search(str(el))
            if m:
                current_pool_id = int(m.group(1))
            continue

        if not isinstance(el, Tag):
            continue

        if el.name == "h3":
            m = _H3_DATE_RE.search(el.get_text())
            if m:
                day, month, year = (int(x) for x in m.groups())
                current_date = date(year, month, day)
            continue

        if el.name == "th":
            text = el.get_text(strip=True)
            if text and current_pool_id is not None:
                pool_headers_by_id.setdefault(current_pool_id, text)
            continue

        if el.name != "tr":
            continue

        tds = el.find_all("td", recursive=False)
        if len(tds) < 7 or current_pool_id is None or current_date is None:
            continue

        time_text = tds[0].get_text(strip=True)
        team1 = tds[2].get_text(strip=True)
        team2 = tds[6].get_text(strip=True)
        score1_span = tds[3].find("span")
        score2_span = tds[5].find("span")

        game_id = None
        link = el.find("a", href=lambda h: h and "view=gameplay" in h)
        if link is not None:
            game_id = _int_param(link["href"])

        schedule_by_pool_id[current_pool_id].append(
            RawGame(
                pool_id=current_pool_id,
                date=current_date,
                time_text=time_text,
                team1_raw_name=team1,
                team2_raw_name=team2,
                score1_text=score1_span.get_text(strip=True) if score1_span else "",
                score2_text=score2_span.get_text(strip=True) if score2_span else "",
                game_id=game_id,
            )
        )

    return dict(schedule_by_pool_id), pool_headers_by_id


# ---------------------------------------------------------------------------
# derive_status / score coercion / datetime combination
# ---------------------------------------------------------------------------


def derive_status(score1_text: str, score2_text: str) -> str:
    """Both scores parseable as plain digits -> "Final"; either missing (a
    not-yet-played game) -> "Scheduled". This source never scrapes a
    tournament mid-event, so there is no "In Progress" case."""
    def _parseable(t: str) -> bool:
        return bool(t) and t.strip().isdigit()

    if _parseable(score1_text) and _parseable(score2_text):
        return "Final"
    return "Scheduled"


def _coerce_score(text: str) -> int:
    """All scores observed in this source's fixtures are plain digits, but
    route through `core.parsing.convertNonDigitScore` defensively for
    forfeit markers ("W"/"L"/...), matching `sources/wfdf/parse.py`'s
    scoring convention for the same defensive reason."""
    stripped = (text or "").strip()
    if stripped.isdigit():
        return int(stripped)
    return convertNonDigitScore(stripped)


def _combine_datetime(game_date: date, time_text: str) -> Optional[datetime]:
    stripped = (time_text or "").strip()
    if not stripped or ":" not in stripped:
        return None
    hour_text, _, minute_text = stripped.partition(":")
    try:
        return datetime(game_date.year, game_date.month, game_date.day, int(hour_text), int(minute_text))
    except ValueError:
        log.warning("wfdf-legacy: could not parse game time %r on %s", time_text, game_date)
        return None


# ---------------------------------------------------------------------------
# build_teams
# ---------------------------------------------------------------------------


def build_teams(
    teams_by_series: Dict[str, List[TeamRow]],
    series_name: str,
    seeds_by_team_id: Dict[int, int],
) -> Tuple[List["models.Team"], Dict[str, "models.Team"]]:
    """`teams_by_series[series_name]` -> (teams, raw-schedule-name -> Team).

    `Team.name` is the bare country name (never the division-suffixed
    display string) -- see the module docstring's "Team identity fix".
    `teams_by_raw_name` (keyed by the suffixed string, e.g. "Australia
    Open") exists solely so callers can resolve a schedule game row's
    team1/team2 text; it is not meant to leave `parse.py`/`source.py`.

    A team id genuinely absent from `seeds_by_team_id` (rather than present
    with any value) defaults its seed to 0."""
    teams: List["models.Team"] = []
    teams_by_raw_name: Dict[str, "models.Team"] = {}

    for row in teams_by_series.get(series_name, []):
        team = models.Team(row.country, seeds_by_team_id.get(row.team_id, 0), "", id=row.team_id)
        team.roster = []
        team.info = models.TeamInfo("", row.country, [], "", "", "")
        teams.append(team)
        teams_by_raw_name[row.raw_name] = team

    return teams, teams_by_raw_name


# ---------------------------------------------------------------------------
# build_stages
# ---------------------------------------------------------------------------


def _resolve_pool_entries(
    nav_pools: List[NavPool], pool_headers_by_id: Dict[int, str], series_name: str
) -> List[Tuple[int, str]]:
    """Every (pool_id, bare pool name) this series should classify: the
    series' own nav pools, plus any pool id the schedule's own <th> header
    attributes to this series (by an exact "{series_name} " prefix) but
    that the nav sidebar has no entry for at all -- concretely, a bracket's
    Semifinals/Finals follower pool (see the module docstring). The exact
    prefix match keeps e.g. "Guts Open" pools from bleeding into "Open"."""
    entries: List[Tuple[int, str]] = [(p.pool_id, p.name) for p in nav_pools]
    seen_ids = {pool_id for pool_id, _ in entries}

    prefix = f"{series_name} "
    extra = sorted(
        (pool_id, text[len(prefix):])
        for pool_id, text in pool_headers_by_id.items()
        if pool_id not in seen_ids and text.startswith(prefix)
    )
    entries.extend(extra)
    return entries


def _games_for_pool(
    pool_id: int,
    schedule_by_pool_id: Dict[int, List[RawGame]],
    teams_by_raw_name: Dict[str, "models.Team"],
    round_label: str,
) -> Tuple[List["models.Game"], int]:
    games: List["models.Game"] = []
    skipped = 0
    for raw in schedule_by_pool_id.get(pool_id, []):
        team_a = teams_by_raw_name.get(raw.team1_raw_name)
        team_b = teams_by_raw_name.get(raw.team2_raw_name)
        if team_a is None or team_b is None:
            log.warning(
                "wfdf-legacy: game %s (pool_id=%s) references unresolved team name(s) "
                "(%r, %r); skipping",
                raw.game_id, pool_id, raw.team1_raw_name, raw.team2_raw_name,
            )
            skipped += 1
            continue

        games.append(
            models.Game(
                team_a,
                team_b,
                _coerce_score(raw.score1_text),
                _coerce_score(raw.score2_text),
                _combine_datetime(raw.date, raw.time_text),
                derive_status(raw.score1_text, raw.score2_text),
                round=round_label,
            )
        )
    return games, skipped


def build_stages(
    nav_pools_for_series: List[NavPool],
    schedule_by_pool_id: Dict[int, List[RawGame]],
    teams_by_raw_name: Dict[str, "models.Team"],
    series_name: str,
    pool_headers_by_id: Optional[Dict[int, str]] = None,
) -> Tuple[List, int]:
    """All stages for one series: classifies every pool via `classify_stage`,
    groups "pool" pools into one `Pools` stage, "bracket" pools into one
    `Brackets` stage (one `Bracket` per distinct group name, games from
    every pool sharing that group name appended in nav order with their own
    round label -- same shape as `sources/wfdf/parse.py`'s
    `_build_brackets_stage`), and "cluster" pools into one `Clusters` stage.
    Any stage with zero matching pools is omitted.

    `pool_headers_by_id` (from `parse_schedule_page`) lets this also pick up
    pools the nav sidebar omits entirely -- see `_resolve_pool_entries` and
    the module docstring.

    Returns `(stages, skipped_game_count)`: skips and counts a game with an
    unresolved team name (`log.warning`), and skips and counts an entire
    pool's games on `UnrecognizedStageShapeError` (`log.error` -- this means
    the site's HTML shape changed in a way this source doesn't understand,
    not a routine data gap) without aborting the rest of the event."""
    pool_entries = _resolve_pool_entries(nav_pools_for_series, pool_headers_by_id or {}, series_name)

    pools_out: List["models.Pool"] = []
    clusters_out: List["models.Cluster"] = []
    brackets_by_group: "Dict[str, List[models.Game]]" = {}
    total_skipped = 0

    for pool_id, pool_name in pool_entries:
        try:
            classification = classify_stage(pool_name)
        except UnrecognizedStageShapeError:
            log.error(
                "wfdf-legacy: %s: pool_id=%s pool_name=%r has an unrecognized stage shape; "
                "skipping its games",
                series_name, pool_id, pool_name,
            )
            total_skipped += len(schedule_by_pool_id.get(pool_id, []))
            continue

        games, skipped = _games_for_pool(
            pool_id, schedule_by_pool_id, teams_by_raw_name, classification.round_label
        )
        total_skipped += skipped

        if classification.kind == "pool":
            pools_out.append(models.Pool(classification.group_name, [], games))
        elif classification.kind == "cluster":
            clusters_out.append(models.Cluster(classification.group_name, games))
        else:  # "bracket"
            brackets_by_group.setdefault(classification.group_name, []).extend(games)

    stages: List = []
    if pools_out:
        stages.append(models.Pools("Pools", pools_out))
    if brackets_by_group:
        brackets = [models.Bracket(name, games) for name, games in brackets_by_group.items()]
        stages.append(models.Brackets("Brackets", brackets))
    if clusters_out:
        stages.append(models.Clusters("Clusters", clusters_out))

    return stages, total_skipped
