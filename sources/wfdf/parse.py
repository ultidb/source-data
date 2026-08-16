"""Pure mapping functions: WFDF's `_reference`/`_games`/`_teams_<id>` JSON
shapes -> `models.py` domain objects. No network, no I/O -- everything here
takes already-fetched dicts and returns `models.Team`/`models.Game`/stage
objects, so it's unit-testable straight from the checked-in fixtures.

See the WFDF source task for the full mapping spec this implements; the
short version:

- One document per `series` (WFDF's term for gender division: Mixed/Open/
  Women's).
- Pools (`pools[].type`): 1 = pool play (or, if `continuingpool`, placement
  pools), 2 = brackets, 4 = clusters (crossovers/placement).
- Bracket/cluster follower pools (`isfollower == 1`) are linked to their
  parent via `ordering` (parent "I" -> followers "I1".."I4"), not by name;
  name-prefix matching is only a logged fallback.
- A game is skipped if `hometeam`/`visitorteam` is falsy (unresolved bracket
  slot) -- this is the large majority of `_games` early in a tournament.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import models

log = logging.getLogger(__name__)

POOL_TYPE_POOL = 1
POOL_TYPE_BRACKET = 2
POOL_TYPE_CLUSTER = 4

_STATUS_MAP = {
    "completed": "Final",
    "ongoing": "In Progress",
    "scheduled": "Scheduled",
}


def parse_wfdf_datetime(value: Optional[str]) -> Optional[datetime]:
    """WFDF timestamps are naive `"YYYY-MM-DD HH:MM:SS"` strings. Returns
    None (TBA) for a missing or unparseable value -- this is used for
    `time_utc`, so the naive result is already UTC wall-clock and needs no
    further conversion (core.serialize._normalize_datetime treats a naive
    datetime as UTC, per the ingest contract)."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        log.warning("wfdf: could not parse datetime %r", value)
        return None


def parse_wfdf_date(value: str) -> date:
    """`season.starttime`/`season.endtime` -> date part only."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").date()


def map_status(raw_status: Optional[str]) -> str:
    mapped = _STATUS_MAP.get(raw_status)
    if mapped is None:
        log.warning("wfdf: unrecognized game status %r, defaulting to Scheduled", raw_status)
        return "Scheduled"
    return mapped


def build_teams(
    reference: dict, series_id: int
) -> Tuple[List["models.Team"], Dict[int, "models.Team"]]:
    """`_reference.teams` filtered to `series_id` -> (teams list, team_id ->
    Team map). `rank` -> seed, `countries[country].name` -> `info.location`.
    No coaches exist in this API."""
    countries_by_id = {c["country_id"]: c["name"] for c in reference.get("countries", [])}

    teams: List["models.Team"] = []
    teams_by_id: Dict[int, "models.Team"] = {}
    for t in reference.get("teams", []):
        if t.get("series") != series_id:
            continue
        team = models.Team(t["name"], t.get("rank") or 0, "", id=t["team_id"])
        team.roster = []
        location = countries_by_id.get(t.get("country"), "") or ""
        team.info = models.TeamInfo("", location, [], "", "", "")
        teams.append(team)
        teams_by_id[t["team_id"]] = team
    return teams, teams_by_id


def build_roster(team_json: dict) -> List["models.Player"]:
    """`_teams_<id>.players[]` -> `[Player(number=str, name="first last")]`."""
    roster = []
    for p in team_json.get("players", []):
        number = p.get("num")
        name = f"{p.get('firstname', '') or ''} {p.get('lastname', '') or ''}".strip()
        roster.append(models.Player(str(number) if number is not None else "", name))
    return roster


def _pools_for_series(reference: dict, series_id: int) -> List[dict]:
    return [p for p in reference.get("pools", []) if p.get("series_id") == series_id]


def _find_parent(follower: dict, pools_in_series: List[dict]) -> Optional[dict]:
    """A follower's `ordering` is its parent's `ordering` plus a trailing
    digit suffix (parent "I" -> followers "I1".."I4"). Falls back to
    name-prefix matching (logged) only if the ordering linkage fails."""
    ordering = follower.get("ordering", "")
    stripped = ordering.rstrip("0123456789")
    if stripped and stripped != ordering:
        for p in pools_in_series:
            if p.get("isfollower") == 0 and p.get("ordering") == stripped:
                return p

    for p in pools_in_series:
        if (
            p.get("isfollower") == 0
            and p.get("pool_id") != follower.get("pool_id")
            and follower.get("poolname", "").startswith(p.get("poolname", ""))
        ):
            log.warning(
                "wfdf: pool %r (ordering=%r) parent not found via ordering; "
                "falling back to name-prefix match with %r",
                follower.get("poolname"), ordering, p.get("poolname"),
            )
            return p

    log.warning(
        "wfdf: pool %r (ordering=%r) has no resolvable parent pool",
        follower.get("poolname"), ordering,
    )
    return None


def _round_for_follower(follower: dict, parent: Optional[dict]) -> str:
    """Strip the parent's poolname prefix off the follower's poolname and
    trim -- "Playoff (1-32) Quarterfinals" under parent "Playoff (1-32)" ->
    "Quarterfinals"."""
    name = follower.get("poolname", "")
    if parent is None:
        return name
    parent_name = parent.get("poolname", "")
    if name.startswith(parent_name):
        return name[len(parent_name):].strip()
    log.warning(
        "wfdf: follower pool %r does not start with parent poolname %r; "
        "using the full follower name as the round label",
        name, parent_name,
    )
    return name


def _games_for_pool(
    pool_id: int,
    games_by_pool: Dict[int, List[dict]],
    teams_by_id: Dict[int, "models.Team"],
    round_label: str,
) -> Tuple[List["models.Game"], int]:
    """Games for one pool -> ([Game], skipped_count). Skips (and counts) any
    game whose hometeam/visitorteam is falsy -- an unresolved bracket slot."""
    games_out: List["models.Game"] = []
    skipped = 0
    for g in games_by_pool.get(pool_id, []):
        home_id = g.get("hometeam")
        visitor_id = g.get("visitorteam")
        if not home_id or not visitor_id:
            skipped += 1
            continue

        team_a = teams_by_id.get(home_id)
        team_b = teams_by_id.get(visitor_id)
        if team_a is None or team_b is None:
            log.warning(
                "wfdf: game %s references team(s) not in this series (home=%r, visitor=%r); skipping",
                g.get("game_id"), home_id, visitor_id,
            )
            skipped += 1
            continue

        games_out.append(
            models.Game(
                team_a,
                team_b,
                g.get("homescore", 0) or 0,
                g.get("visitorscore", 0) or 0,
                parse_wfdf_datetime(g.get("time_utc")),
                map_status(g.get("status")),
                round=round_label,
            )
        )
    return games_out, skipped


def _teams_for_pool(
    pool_id: int,
    pool_placements_by_pool: Dict[int, List[dict]],
    teams_by_id: Dict[int, "models.Team"],
) -> List["models.Team"]:
    entries = sorted(pool_placements_by_pool.get(pool_id, []), key=lambda pp: pp.get("placement", 0))
    return [teams_by_id[e["team_id"]] for e in entries if e.get("team_id") in teams_by_id]


def _build_pools_stage(
    pools_in_series: List[dict],
    games_by_pool: Dict[int, List[dict]],
    teams_by_id: Dict[int, "models.Team"],
    pool_placements_by_pool: Dict[int, List[dict]],
    *,
    continuing: int,
    stage_name: str,
) -> Tuple[Optional["models.Pools"], int]:
    matching = sorted(
        (p for p in pools_in_series if p.get("type") == POOL_TYPE_POOL and p.get("continuingpool") == continuing),
        key=lambda p: p.get("ordering", ""),
    )
    if not matching:
        return None, 0

    total_skipped = 0
    pools_out = []
    for p in matching:
        games, skipped = _games_for_pool(p["pool_id"], games_by_pool, teams_by_id, round_label="")
        total_skipped += skipped
        pool_teams = _teams_for_pool(p["pool_id"], pool_placements_by_pool, teams_by_id)
        pools_out.append(models.Pool(p["poolname"], pool_teams, games))
    return models.Pools(stage_name, pools_out), total_skipped


def _build_brackets_stage(
    pools_in_series: List[dict],
    games_by_pool: Dict[int, List[dict]],
    teams_by_id: Dict[int, "models.Team"],
) -> Tuple[Optional["models.Brackets"], int]:
    parents = sorted(
        (p for p in pools_in_series if p.get("type") == POOL_TYPE_BRACKET and p.get("isfollower") == 0),
        key=lambda p: p.get("ordering", ""),
    )
    if not parents:
        return None, 0

    followers = [p for p in pools_in_series if p.get("type") == POOL_TYPE_BRACKET and p.get("isfollower") == 1]
    followers_by_parent_id: Dict[int, List[dict]] = defaultdict(list)
    for f in followers:
        parent = _find_parent(f, pools_in_series)
        key = parent["pool_id"] if parent is not None else f["pool_id"]
        followers_by_parent_id[key].append((f, parent))

    total_skipped = 0
    brackets = []
    for parent in parents:
        games, skipped = _games_for_pool(parent["pool_id"], games_by_pool, teams_by_id, round_label="")
        total_skipped += skipped

        for follower, follower_parent in sorted(
            followers_by_parent_id.get(parent["pool_id"], []), key=lambda fp: fp[0].get("ordering", "")
        ):
            round_label = _round_for_follower(follower, follower_parent)
            f_games, f_skipped = _games_for_pool(follower["pool_id"], games_by_pool, teams_by_id, round_label)
            games.extend(f_games)
            total_skipped += f_skipped

        brackets.append(models.Bracket(parent["poolname"], games))
    return models.Brackets("Bracket Play", brackets), total_skipped


def _build_clusters_stage(
    pools_in_series: List[dict],
    games_by_pool: Dict[int, List[dict]],
    teams_by_id: Dict[int, "models.Team"],
) -> Tuple[Optional["models.Clusters"], int]:
    matching = sorted(
        (p for p in pools_in_series if p.get("type") == POOL_TYPE_CLUSTER),
        key=lambda p: p.get("ordering", ""),
    )
    if not matching:
        return None, 0

    total_skipped = 0
    clusters = []
    for p in matching:
        games, skipped = _games_for_pool(p["pool_id"], games_by_pool, teams_by_id, round_label="")
        total_skipped += skipped
        clusters.append(models.Cluster(p["poolname"], games))
    return models.Clusters("Crossovers & Placement", clusters), total_skipped


def build_stages(
    reference: dict,
    games_data: List[dict],
    series_id: int,
    teams_by_id: Dict[int, "models.Team"],
) -> Tuple[List, int]:
    """All stages for one series, in order: Pool Play, Placement Pools,
    Bracket Play, Clusters (any stage with zero matching pools is omitted).
    Returns (stages, total_skipped_game_count)."""
    pools_in_series = _pools_for_series(reference, series_id)

    games_by_pool: Dict[int, List[dict]] = defaultdict(list)
    for g in games_data:
        games_by_pool[g.get("pool")].append(g)

    pool_placements_by_pool: Dict[int, List[dict]] = defaultdict(list)
    for pp in reference.get("pool_placements", []):
        pool_placements_by_pool[pp["pool_id"]].append(pp)

    stages = []
    total_skipped = 0

    pool_play, skipped = _build_pools_stage(
        pools_in_series, games_by_pool, teams_by_id, pool_placements_by_pool,
        continuing=0, stage_name="Pool Play",
    )
    total_skipped += skipped
    if pool_play is not None:
        stages.append(pool_play)

    placement_pools, skipped = _build_pools_stage(
        pools_in_series, games_by_pool, teams_by_id, pool_placements_by_pool,
        continuing=1, stage_name="Placement Pools",
    )
    total_skipped += skipped
    if placement_pools is not None:
        stages.append(placement_pools)

    brackets, skipped = _build_brackets_stage(pools_in_series, games_by_pool, teams_by_id)
    total_skipped += skipped
    if brackets is not None:
        stages.append(brackets)

    clusters, skipped = _build_clusters_stage(pools_in_series, games_by_pool, teams_by_id)
    total_skipped += skipped
    if clusters is not None:
        stages.append(clusters)

    return stages, total_skipped
