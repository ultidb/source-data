"""Reference `Source` implementation, backed by checked-in fixture files
instead of the network.

Purpose (see sources/README.md): prove the whole plugin path -- discover ->
fetch_event -> parse_event -> core.serialize.tournament_to_document ->
pydantic validation -> core.emit.write_document -- works end to end,
entirely offline, and serve as the copy-paste starting point for a real
source (e.g. sources/wfdf/).

The fixture format under sources/example/fixtures/<year>/<key>.json is
*this source's own private shape* -- it is not the wire format
(core.schema.Document) and other sources are free to invent their own. A
network-backed source would put its raw HTML/API bytes here instead; this
one just stores a small hand-written JSON blob per event.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path
from typing import List, Optional

import models
from core.source import Cache, EventRef, FetchedPages, Source

_DEFAULT_FIXTURES_DIR = Path(__file__).parent / "fixtures"


class ExampleSource(Source):
    id = "example"

    def __init__(self, fixtures_dir: Optional[Path] = None):
        # Overridable so tests can point at a scratch fixtures directory
        # without touching the checked-in ones.
        self._fixtures_dir = Path(fixtures_dir) if fixtures_dir is not None else _DEFAULT_FIXTURES_DIR

    def discover(self, year: int) -> List[EventRef]:
        year_dir = self._fixtures_dir / str(year)
        if not year_dir.exists():
            return []

        refs = []
        for path in sorted(year_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            refs.append(
                EventRef(
                    url=f"fixture://{path.relative_to(self._fixtures_dir)}",
                    name=data.get("name"),
                    division=data.get("division"),
                    city=data.get("city", ""),
                    state=data.get("state", ""),
                    country=data.get("country", ""),
                    start_date=date.fromisoformat(data["startDate"]),
                    end_date=date.fromisoformat(data["endDate"]),
                    # source-private payload (see core/source.py's EventRef
                    # docstring) -- this is where a real source would stash
                    # e.g. USAU's season id or WFDF's numeric event id.
                    extra={"fixture_path": str(path), "year": year},
                )
            )
        return refs

    def event_key(self, ref: EventRef) -> str:
        year = ref.extra["year"]
        stem = Path(ref.extra["fixture_path"]).stem
        return f"{year}/{stem}"

    def fetch_event(self, ref: EventRef, cache: Cache) -> FetchedPages:
        # A network-backed source would call cache.fetch(key, url) here to
        # get caching + transport for free. The fixture is already on local
        # disk, so we read it directly -- but still warm the cache via
        # cache.put(), to exercise that half of the Cache protocol too.
        raw = Path(ref.extra["fixture_path"]).read_bytes()
        cache.put("event", raw)
        return {"event": raw}

    def parse_event(
        self, pages: FetchedPages, ref: EventRef, year: int
    ) -> Optional["models.Tournament"]:
        data = json.loads(pages["event"].decode("utf-8"))

        teams_by_name = {}
        teams = []
        for t in data["teams"]:
            team = models.Team(t["name"], t.get("seed", 0), t.get("url", ""), id=t.get("id"))
            team.roster = [models.Player(p["number"], p["name"]) for p in t.get("roster", [])]
            info = t.get("info")
            if info:
                team.info = models.TeamInfo(
                    info.get("nickname", ""),
                    info.get("location", ""),
                    info.get("coaches", []),
                    info.get("website", ""),
                    info.get("facebook", ""),
                    info.get("twitter", ""),
                )
            teams.append(team)
            teams_by_name[team.name] = team

        def build_game(g: dict) -> "models.Game":
            game_dt = datetime.fromisoformat(g["datetime"]) if g.get("datetime") else None
            return models.Game(
                teams_by_name[g["team1"]],
                teams_by_name[g["team2"]],
                g.get("score1", 0),
                g.get("score2", 0),
                game_dt,
                g.get("status", ""),
                round=g.get("round"),
            )

        stages = []
        if data.get("pools"):
            pools = []
            for p in data["pools"]:
                games = [build_game(g) for g in p["games"]]
                pool_teams_by_name = {}
                for gm in games:
                    pool_teams_by_name[gm.teamA.name] = gm.teamA
                    pool_teams_by_name[gm.teamB.name] = gm.teamB
                pool_teams = sorted(pool_teams_by_name.values(), key=lambda tm: tm.seed)
                pools.append(models.Pool(p["name"], pool_teams, games))
            stages.append(models.Pools(data.get("poolsStageName", "Pool Play"), pools))

        if data.get("brackets"):
            brackets = [
                models.Bracket(b["name"], [build_game(g) for g in b["games"]])
                for b in data["brackets"]
            ]
            stages.append(models.Brackets(data.get("bracketsStageName", "Bracket Play"), brackets))

        tournament_datetime = datetime.combine(date.fromisoformat(data["startDate"]), time.min)

        return models.Tournament(
            data["name"],
            ref.url,
            data.get("city", ""),
            data.get("state", ""),
            data["startDate"],
            data["endDate"],
            teams,
            tournament_datetime,
            data["division"],
            stages,
        )
